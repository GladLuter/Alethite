import json
import os
import shutil
import tempfile
import unittest

import cleanup
import savings
import stats
from memdb import connect
from test_savings import A, B, C, HOUR2, MIN10, OLD, T, block, call, user

SINCE = '2026-11-01'
RECALL = {'type': 'tool_use', 'id': 'r1', 'name': 'mcp__alethite__recall', 'input': {'query': 'x'}}


def result(text, ts):
    return dict(type='user', timestamp=ts, message={'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'r1', 'content': text}]})


class CleanupTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.projects, self.cache = os.path.join(d, 'projects'), os.path.join(d, 'savings-cache.json')
        self.conn = connect(os.path.join(d, 'db', 'index.db'))
        self.addCleanup(self.conn.close)

    def path(self, sid):
        return os.path.join(self.projects, 'P', sid + '.jsonl')

    def write(self, name, objs, mode='w'):
        path = os.path.join(self.projects, *name.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode, encoding='utf-8') as f:
            f.write(''.join(json.dumps(o) + '\n' for o in objs))

    def chat_a(self):
        """A: a handoff, a call that uses recall, its result, a call; one subagent call."""
        self.write('P/%s.jsonl' % A, [user(block(C), T), dict(call('a1', T, cr=OLD, out=5), message=dict(call('a1', T, cr=OLD, out=5)['message'], content=[RECALL])),
                                       result('r' * 700, T), call('a2', MIN10, cr=OLD, out=7)])
        self.write('P/%s/subagents/agent-1.jsonl' % A, [call('s1', T, cr=1000, out=3)])

    def remove(self, sid):
        os.remove(self.path(sid))
        shutil.rmtree(os.path.join(self.projects, 'P', sid), ignore_errors=True)

    def metrics(self, saved=None):
        return stats.metrics(self.projects, self.conn, SINCE, saved=saved)

    def test_metrics_same_after_file_removed(self):
        self.chat_a()
        self.write('P/%s.jsonl' % B, [user(block(A), HOUR2), call('b1', HOUR2, cc=60586, inp=2)])
        m1 = self.metrics()
        self.assertEqual((m1['recall_calls'], m1['handoffs']), (1, 2))
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        self.remove(A)
        self.assertNotEqual(self.metrics(), m1)  # the snapshot is what makes up for the file
        self.assertEqual(self.metrics(cleanup.saved_chains(self.conn)), m1)

    def test_saved_ignored_while_file_exists(self):
        self.chat_a()
        m1 = self.metrics()
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        self.assertEqual(self.metrics(cleanup.saved_chains(self.conn)), m1)

    def test_savings_chain_survives_middle_removed(self):
        self.write('P/%s.jsonl' % A, [call('oa', T, cr=OLD)])
        self.write('P/%s.jsonl' % B, [user(block(A), HOUR2), call('fb', HOUR2, cc=60586, inp=2), call('gb', HOUR2, cr=70000)])
        self.write('P/%s.jsonl' % C, [user(block(B), '2026-11-09T15:00:00.000Z'), call('fc', '2026-11-09T15:00:00.000Z', cc=50000, inp=2)])
        s1 = savings.savings(self.projects, C, [A, B, C], self.cache)
        self.assertEqual(s1['worker']['links'], 2)
        for sid in (A, B):
            cleanup.snapshot(self.conn, self.projects, self.path(sid))
            self.remove(sid)
        self.assertEqual(savings.savings(self.projects, C, [A, B, C], self.cache, cleanup.gone(self.conn)), s1)

    def test_snapshot_rewrites_on_second_call(self):
        self.chat_a()
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        self.write('P/%s.jsonl' % A, [call('a3', '2026-11-09T11:00:00.000Z', cr=OLD)], 'a')
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM removed').fetchone()[0], 1)
        main = next(c for c in cleanup.saved_chains(self.conn) if c['kind'] == 'main')
        self.assertEqual(len([e for e in main['ev'] if e[0] == 'c']), 3)
        self.assertEqual(len(cleanup.saved_chains(self.conn)), 2)  # the main chat and its subagent
        self.assertEqual(self.conn.execute('SELECT path FROM removed').fetchone()[0], 'P/%s.jsonl' % A)


if __name__ == '__main__':
    unittest.main()
