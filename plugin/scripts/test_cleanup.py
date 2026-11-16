import gc
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

import cc_token_audit
import cg
import cleanup
import crypto
import memdb
import savings
import stats
from memdb import connect
import test_savings as savings_test
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

    def test_removed_chat_keeps_its_lean_agents(self):
        def agent(sid, name, typ, objs):
            self.write('P/%s/subagents/%s.jsonl' % (sid, name), objs)
            with open(os.path.join(self.projects, 'P', sid, 'subagents', name + '.meta.json'), 'w') as f:
                json.dump({'agentType': typ}, f)

        def acall(mid, **kw):
            return savings_test.acall(mid, T, **kw)

        self.write('P/%s.jsonl' % A, [call('oa', T, cr=OLD)])
        self.write('P/%s.jsonl' % C, [call('oc', T, cr=OLD)])
        agent(C, 'agent-g', 'general-purpose', [acall('g1', c5=50000, cr=10000)])
        agent(A, 'agent-c', 'alethite:coder', [acall('c1', c1=8000, cr=2000), acall('c2', cr=3000)])
        before = savings.savings(self.projects, C, [A, C], self.cache)['project']['lean']
        self.assertEqual(before['agents'], 1)
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        self.remove(A)
        self.assertFalse(any('agent' in c for c in cleanup.saved_chains(self.conn)))  # the starts live in the agents column
        gone = cleanup.gone(self.conn)
        self.assertEqual([a['type'] for a in gone[A]['agents']], ['alethite:coder'])
        self.assertEqual(savings.savings(self.projects, C, [A, C], self.cache, gone)['project']['lean'], before)

    def test_gone_skips_a_damaged_row_and_counts_an_agent_once(self):
        ag = {'type': 'alethite:coder', 'ts': T, 'model': 'm', 'usage': [0, 0, 1, 1], 'calls': 1, 'workflow': False, 'resumes': [], 'cw1h_total': 0}
        for conv, a in (('bad', '{bad'), ('x', json.dumps({'P/x/subagents/agent-1.jsonl': ag})), ('old', None), ('list', '[1]')):
            self.conn.execute('INSERT INTO removed(conv, path, at, last_usage, read_new, chains, agents) VALUES(?,?,?,?,?,?,?)',
                              (conv, 'P/%s.jsonl' % conv, 'now', 'null', 'null', '{bad', a))
        gone = cleanup.gone(self.conn)
        self.assertEqual([gone[c]['agents'] for c in ('bad', 'old', 'list')], [[], [], []])
        self.assertEqual(gone['x']['agents'], [ag])

    def test_snapshot_survives_an_agents_value_that_is_not_an_object(self):
        self.chat_a()
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        self.conn.execute("UPDATE removed SET agents = '[1]'")
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM removed').fetchone()[0], 1)

    def test_snapshot_survives_a_damaged_saved_row(self):
        self.chat_a()
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        n = len(json.loads(self.conn.execute('SELECT chains FROM removed').fetchone()[0]))
        self.conn.execute("UPDATE removed SET chains = '{bad', read_new = '{bad'")
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        self.assertEqual(len(json.loads(self.conn.execute('SELECT chains FROM removed').fetchone()[0])), n)

    def test_an_index_made_without_the_agents_column_opens_and_gone_works(self):
        path = os.path.join(os.path.dirname(self.db), 'old.db')
        old = sqlite3.connect(path)
        old.execute('CREATE TABLE removed(conv TEXT PRIMARY KEY, path TEXT NOT NULL, at TEXT NOT NULL, last_usage TEXT, read_new TEXT, chains TEXT NOT NULL)')
        old.execute("INSERT INTO removed VALUES('o', 'P/o.jsonl', 'now', 'null', 'null', '[]')")
        old.commit()
        old.close()
        conn = memdb.connect(path)
        self.assertEqual(cleanup.gone(conn)['o']['agents'], [])
        conn.close()

    def test_snapshot_rewrites_on_second_call(self):
        self.chat_a()
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        self.write('P/%s.jsonl' % A, [call('a3', '2026-11-09T11:00:00.000Z', cr=OLD)], 'a')
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM removed').fetchone()[0], 1)
        main = next(c for c in cleanup.saved_chains(self.conn) if c['rel'] == 'P/%s.jsonl' % A)
        self.assertEqual(len([e for e in main['ev'] if e[0] == 'c']), 3)
        self.assertEqual(len(cleanup.saved_chains(self.conn)), 2)  # the main chat and its subagent
        self.assertEqual(self.conn.execute('SELECT path FROM removed').fetchone()[0], 'P/%s.jsonl' % A)

    def totals(self):
        """The token audit of the disk and the snapshots together: what /al-stats is made of."""
        return cc_token_audit.analyze(self.projects, SINCE, saved=cc_token_audit.missing(self.projects, cleanup.saved_chains(self.conn)))

    def test_nested_workflow_agent_kept_in_snapshot(self):
        self.chat_a()
        self.write('P/%s/subagents/workflows/wf_1/agent-2.jsonl' % A, [call('w1', T, cr=OLD, out=9), call('w2', MIN10, cr=OLD, out=9)])
        self.write('P/%s.jsonl' % B, [user(block(A), HOUR2), call('b1', HOUR2, cc=60586, inp=2)])
        before = self.totals()
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        self.remove(A)
        after = self.totals()
        self.assertEqual(len(cleanup.saved_chains(self.conn)), 3)
        self.assertEqual((after['totals'], after['cache_rewrites']['total']), (before['totals'], before['cache_rewrites']['total']))

    def test_transcript_that_came_back_keeps_first_snapshot(self):
        self.chat_a()
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        before = len(self.conn.execute('SELECT read_new FROM removed').fetchone()[0]), self.totals()['totals']
        self.remove(A)
        self.write('P/%s.jsonl' % A, [call('a9', '2026-11-09T12:00:00.000Z', cr=OLD)])  # the resumed chat appends: only the new line is there
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM removed').fetchone()[0], 1)
        self.assertEqual(len(self.conn.execute('SELECT read_new FROM removed').fetchone()[0]), before[0])
        self.remove(A)
        self.assertEqual(self.totals()['totals']['calls'], before[1]['calls'] + 1)

    def test_saved_chain_before_since_is_skipped(self):
        self.chat_a()
        for p in (self.path(A), os.path.join(self.projects, 'P', A, 'subagents', 'agent-1.jsonl')):
            os.utime(p, (time.time() - 90 * 86400,) * 2)
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        self.remove(A)
        self.assertEqual(self.totals()['meta']['files_skipped_before_since'], 2)  # by the stored mtime: the file is not there to ask

    def test_damaged_saved_row_is_skipped(self):
        self.chat_a()
        cleanup.snapshot(self.conn, self.projects, self.path(A))
        self.conn.execute("INSERT INTO removed(conv, path, at, last_usage, read_new, chains, agents) VALUES('X', 'P/X.jsonl', '2026-11-11T10:00:00+00:00', '{', '{', '{', '{')")
        self.assertEqual(len(cleanup.saved_chains(self.conn)), 2)
        self.assertEqual(set(cleanup.gone(self.conn)), {A, 'X'})

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

    def test_odd_chat_does_not_stop_the_pass(self):
        self.ready()
        self.write('P/%s.jsonl' % C, [call('c1', T, cc=50000, inp=2)])  # chat A was handed off from it: a second candidate, after A
        memdb.update(self.conn, self.projects)
        real = cleanup.snapshot

        def odd(conn, projects, path):
            if path == self.path(A):
                raise ValueError('odd line')
            return real(conn, projects, path)

        with mock.patch('cleanup.snapshot', odd):
            out = self.run_cleanup()
        self.assertEqual((out['removed'], len(out['errors'])), (1, 1))
        self.assertTrue(os.path.exists(self.path(A)))
        self.assertFalse(os.path.exists(self.path(C)))

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

    def test_handoff_file_failure_counted_and_retried(self):
        self.ready()
        real = os.remove

        def locked_md(p, *a):
            if p.endswith('.md'):
                raise PermissionError('locked')
            return real(p, *a)

        with mock.patch('os.remove', locked_md):
            out = self.run_cleanup()
        md = os.path.join(self.handoffs, A + '.md')
        self.assertEqual((out['removed'], [e['path'] for e in out['errors']]), (1, [md]))
        self.assertFalse(os.path.exists(self.path(A)))
        self.assertTrue(os.path.exists(md))
        self.assertEqual((self.run_cleanup()['errors'], os.path.exists(md)), ([], False))

    def test_run_waits_on_partial_last_line(self):
        self.ready()
        with open(self.path(A), 'a') as f:
            f.write('{"type": "user"')  # no newline yet: the index stops before it (offset < size)
        memdb.update(self.conn, self.projects)
        self.assertEqual(self.conn.execute('SELECT offset < size FROM files WHERE path LIKE ?', ('%' + A + '.jsonl',)).fetchone()[0], 1)
        self.assertEqual(self.run_cleanup()['waiting'], 1)
        self.assertTrue(os.path.exists(self.path(A)))

    def test_unlistable_folder_reported_not_removed(self):
        self.ready()

        def denied(top, onerror=None, **kw):
            onerror(PermissionError('denied'))
            return iter(())

        with mock.patch('os.walk', denied):
            out = self.run_cleanup()
        self.assertEqual((out['removed'], len(out['errors'])), (0, 1))
        self.assertTrue(os.path.exists(self.path(A)))

    def test_maintain_waits_while_sync_pending(self):
        self.ready()
        for p in (self.path(A), os.path.join(self.projects, 'P', A, 'subagents', 'agent-1.jsonl')):
            os.utime(p, (time.time() - 7200,) * 2)  # cold
        crypto.set_fields(crypto.settings_path(self.db), {'deleteOldChats': True, 'syncDir': os.path.join(os.path.dirname(self.db), 'share')})
        ok = {'pushed': 0, 'pulled': 0, 'errors': []}
        with mock.patch('sync.sync', return_value=ok), mock.patch('sync.pending', return_value=True):
            out = self.maintain()
        self.assertEqual((out['cleanup']['removed'], out['cleanup']['waiting']), (0, 1))
        self.assertTrue(os.path.exists(self.path(A)))
        with mock.patch('sync.sync', return_value=ok), mock.patch('sync.pending', return_value=False):
            self.assertEqual(self.maintain()['cleanup']['removed'], 1)

    def test_status_counts_only_chats_gone_from_disk(self):
        self.ready()
        cleanup.snapshot(self.conn, self.projects, self.path(A))  # as a failed removal leaves it
        a = cg._parser().parse_args(['--db', self.db, '--projects', self.projects, 'status'])
        self.assertEqual(a.fn(a)['cleanup']['removed_total'], 0)
        self.run_cleanup()
        self.assertEqual(a.fn(a)['cleanup']['removed_total'], 1)

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

    def test_cli_on_answers_without_preview_when_it_fails(self):
        self.ready()
        a = cg._parser().parse_args(['--db', self.db, '--projects', self.projects, 'cleanup', 'on'])
        with mock.patch('cleanup.preview', side_effect=OSError('denied')):
            self.assertEqual(a.fn(a), {'on': True, 'preview': None})

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
