import gc
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import cg
import cleanup
import crypto
import memdb
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
        self.db, self.handoffs = os.path.join(d, 'db', 'index.db'), os.path.join(d, 'db', 'handoffs')
        self.conn = connect(self.db)
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

    # the job's removal

    def ready(self):
        """A (with a subagent) handed off to B, everything indexed, A's handoff text beside the index."""
        self.chat_a()
        self.write('P/%s.jsonl' % B, [user(block(A), HOUR2), call('b1', HOUR2, cc=60586, inp=2)])
        os.makedirs(self.handoffs)
        with open(os.path.join(self.handoffs, A + '.md'), 'w') as f:
            f.write('quotes')
        memdb.update(self.conn, self.projects)

    def run_cleanup(self, synced=True):
        """cleanup.run two hours from now: the chat is cold."""
        return cleanup.run(self.conn, self.projects, synced, self.handoffs, now=lambda: time.time() + 7200)

    def test_run_removes_old_chat_and_folder(self):
        self.ready()
        out = self.run_cleanup()
        self.assertEqual((out['removed'], out['waiting'], out['errors']), (1, 0, []))
        self.assertFalse(os.path.exists(self.path(A)))
        self.assertFalse(os.path.exists(os.path.join(self.projects, 'P', A)))
        self.assertFalse(os.path.exists(os.path.join(self.handoffs, A + '.md')))
        self.assertTrue(os.path.exists(self.path(B)))
        self.assertEqual(self.conn.execute('SELECT conv FROM removed').fetchall(), [(A,)])

    def test_run_waits_when_not_indexed(self):
        self.ready()
        self.write('P/%s.jsonl' % A, [call('a3', T, cr=OLD)], 'a')
        self.assertEqual(self.run_cleanup()['waiting'], 1)
        self.assertTrue(os.path.exists(self.path(A)))

    def test_run_waits_when_subagent_not_indexed(self):
        self.ready()
        self.write('P/%s/subagents/agent-1.jsonl' % A, [call('s2', T, cr=1000)], 'a')
        self.assertEqual(self.run_cleanup()['waiting'], 1)
        self.assertTrue(os.path.exists(self.path(A)))

    def test_run_waits_when_sync_pending(self):
        self.ready()
        self.assertEqual(self.run_cleanup(synced=False)['waiting'], 1)
        self.assertTrue(os.path.exists(self.path(A)))

    def test_run_waits_when_touched_recently(self):
        self.ready()
        newest = max(os.path.getmtime(p) for p in (self.path(A), os.path.join(self.projects, 'P', A, 'subagents', 'agent-1.jsonl')))
        for ago, removed in ((59 * 60, 0), (60 * 60, 1)):
            out = cleanup.run(self.conn, self.projects, True, self.handoffs, now=lambda: newest + ago)
            self.assertEqual(out['removed'], removed)
        self.assertFalse(os.path.exists(self.path(A)))

    def test_current_chat_never_candidate(self):
        self.ready()
        self.assertEqual(cleanup.candidates(self.conn, self.projects), [self.path(A)])

    def test_candidates_follow_removed_rows(self):
        """A removed, B (handed off from A) removed later: A's row still names its own old chat Z, whose file is still there."""
        Z = '%08x-0000-0000-0000-000000000000' % 0xd
        self.write('P/%s.jsonl' % Z, [call('z1', T, cr=OLD)])
        self.write('P/%s.jsonl' % A, [user(block(Z), T), call('a1', T, cr=OLD)])
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        self.remove(A)
        self.assertEqual(cleanup.candidates(self.conn, self.projects), [self.path(Z)])

    def test_locked_file_reported_and_retried(self):
        self.ready()
        with mock.patch('os.remove', side_effect=PermissionError('locked')):
            out = self.run_cleanup()
        self.assertEqual((out['removed'], len(out['errors']), out['errors'][0]['path']), (0, 1, self.path(A)))
        self.assertTrue(os.path.exists(self.path(A)))
        self.assertEqual(self.run_cleanup()['removed'], 1)

    def test_failed_folder_keeps_chat_for_next_pass(self):
        self.ready()
        sub = os.path.join(self.projects, 'P', A, 'subagents')
        self.write('P/%s/subagents/agent-2.jsonl' % A, [call('s2', T, cr=500, out=1)])
        memdb.update(self.conn, self.projects)
        before = self.metrics()

        def half(path, *a, **kw):  # one subagent file goes, the other is locked
            os.remove(os.path.join(sub, 'agent-1.jsonl'))
            raise PermissionError('locked')

        with mock.patch('shutil.rmtree', half):
            out = self.run_cleanup()
        self.assertEqual((out['removed'], len(out['errors']), out['errors'][0]['path']), (0, 1, self.path(A)))
        self.assertTrue(os.path.exists(self.path(A)))
        self.assertTrue(os.path.exists(os.path.join(self.handoffs, A + '.md')))
        out = self.run_cleanup()
        self.assertEqual((out['removed'], out['errors']), (1, []))
        self.assertFalse(os.path.exists(os.path.join(self.projects, 'P', A)))
        self.assertFalse(os.path.exists(self.path(A)))
        self.assertEqual(self.metrics(cleanup.saved_chains(self.conn)), before)

    def test_rebuild_keeps_removed_chat(self):
        self.chat_a()
        self.write('P/%s.jsonl' % A, [user('хомяк ёжик', T)], 'a')
        self.write('P/%s.jsonl' % B, [user(block(A), HOUR2), call('b1', HOUR2, cc=60586, inp=2)])
        memdb.update(self.conn, self.projects)
        self.assertEqual(self.run_cleanup()['removed'], 1)
        memdb.update(self.conn, self.projects, rebuild=True)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM chunks WHERE chunks MATCH ? AND conv = ?', ('ёжик', A)).fetchone()[0], 1)

    def test_preview_counts_without_conditions(self):
        self.ready()
        self.write('P/%s.jsonl' % A, [call('a3', T, cr=OLD)], 'a')  # not indexed, not cold: still counted
        p = cleanup.preview(self.conn, self.projects)
        self.assertEqual(p['chats'], 1)
        self.assertGreater(p['mb'], 0)

    def maintain(self):
        self.addCleanup(gc.collect)  # maintain never closes its connection
        a = cg._parser().parse_args(['--db', self.db, '--projects', self.projects, 'maintain'])
        return cg.maintain(a, battery=lambda: True, digest=lambda m: None)

    def test_maintain_skips_cleanup_when_off(self):
        self.ready()
        out = self.maintain()
        self.assertIsNone(out['cleanup'])
        self.assertTrue(os.path.exists(self.path(A)))

    def test_maintain_runs_cleanup_when_on(self):
        self.ready()
        crypto.set_fields(crypto.settings_path(self.db), {'deleteOldChats': True})
        out = self.maintain()
        self.assertEqual((out['cleanup']['waiting'], out['errors']), (1, []))  # the files are fresh: not cold yet

    def test_cli_on_off_writes_setting(self):
        self.ready()
        settings = crypto.settings_path(self.db)
        for word, want in (('on', True), ('off', None)):
            a = cg._parser().parse_args(['--db', self.db, '--projects', self.projects, 'cleanup', word])
            out = a.fn(a)
            self.assertEqual(crypto._read(settings).get('deleteOldChats'), want)
            self.assertEqual((out['on'], out['preview']['chats']), (want is True, 1))
        a = cg._parser().parse_args(['--db', self.db, '--projects', self.projects, 'status'])
        self.assertEqual(a.fn(a)['cleanup'], {'on': False, 'removed_total': 0})


if __name__ == '__main__':
    unittest.main()
