import builtins
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import memdb
import savings
from transcripts import project_keys

A, B, C = ('%08x-0000-0000-0000-000000000000' % n for n in (0xa, 0xb, 0xc))
T = '2026-11-09T10:00:00.000Z'
HOUR2, MIN10 = '2026-11-09T12:00:00.000Z', '2026-11-09T10:10:00.000Z'
OPUS = 'claude-opus-5-5'
OLD = 303258


def call(mid, ts, model=OPUS, cc=0, cr=0, inp=0, out=0, **kw):
    u = {'input_tokens': inp, 'cache_creation_input_tokens': cc, 'cache_read_input_tokens': cr, 'output_tokens': out}
    return dict(type='assistant', timestamp=ts, message={'id': mid, 'model': model, 'role': 'assistant', 'content': [], 'usage': u}, **kw)


def user(text, ts):
    return dict(type='user', timestamp=ts, message={'role': 'user', 'content': [{'type': 'text', 'text': text}]})


def block(old, mark='al'):
    return '[%s-handoff]\nсессия %s\n[/%s-handoff]\n\nдальше' % (mark, old, mark)


class SavingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.cache = os.path.join(self.dir, 'savings-cache.json')

    def write(self, sid, objs):
        os.makedirs(os.path.join(self.dir, 'p'), exist_ok=True)
        with open(os.path.join(self.dir, 'p', sid + '.jsonl'), 'w', encoding='utf-8') as f:
            f.write(''.join(json.dumps(o) + '\n' for o in objs))

    def old(self, sid, at=T, tokens=OLD):
        self.write(sid, [call('o' + sid[:2], at, cr=tokens)])

    def new(self, sid, old, ts, first_cc=60586, tail=(), mark='al'):
        self.write(sid, [user(block(old, mark), ts), call('f' + sid[:2], ts, cc=first_cc, inp=2), *tail])

    def run_savings(self, session, convs=None):
        return savings.savings(self.dir, session, convs, self.cache)

    def test_cold_old_chat(self):
        self.old(A)
        self.new(B, A, HOUR2)
        r = self.run_savings(B)['last']
        self.assertAlmostEqual(r['usd'], OLD * 8e-6 - (60586 * 8e-6 + 2 * 4e-6), 6)
        self.assertEqual((r['old_tokens'], r['new_tokens']), (OLD, 60588))

    def test_warm_old_chat_is_negative(self):
        self.old(A)
        self.new(B, A, MIN10)
        r = self.run_savings(B)['last']
        self.assertAlmostEqual(r['usd'], OLD * 0.2e-6 - (60586 * 8e-6 + 2 * 4e-6), 6)
        self.assertLess(r['usd'], 0)

    def test_tail_counts_each_call_once(self):
        self.old(A)
        t = call('x', HOUR2, cr=70000)
        self.new(B, A, HOUR2, tail=[t, t, t, call('y', HOUR2, cr=70000, isSidechain=True), call('z', HOUR2, model='<synthetic>', cr=70000)])
        w = self.run_savings(B)['worker']
        self.assertEqual(w['calls'], 1)
        first = OLD * 8e-6 - (60586 * 8e-6 + 2 * 4e-6)
        self.assertAlmostEqual(w['usd'], first + (OLD - 60588) * 0.2e-6, 6)

    def test_cold_call_in_tail_is_priced_as_write(self):
        self.old(A)
        self.new(B, A, HOUR2, tail=[call('x', HOUR2, cc=50000, cr=10000)])
        w = self.run_savings(B)['worker']
        first = OLD * 8e-6 - (60586 * 8e-6 + 2 * 4e-6)
        self.assertAlmostEqual(w['usd'], first + (OLD - 60588) * 8e-6, 6)

    def test_chain_walks_back(self):
        self.old(A)
        self.new(B, A, HOUR2)
        self.new(C, B, '2026-11-09T15:00:00.000Z')
        r = self.run_savings(C)
        self.assertEqual(r['worker']['links'], 2)
        self.assertEqual(r['last']['old_tokens'], 60588)  # the link B -> C

    def test_chain_stops_at_missing_or_loop(self):
        self.new(B, A, HOUR2)  # A has no transcript
        self.assertEqual(self.run_savings(B)['worker']['links'], 0)
        self.new(C, B, HOUR2)
        self.assertEqual(self.run_savings(C)['worker']['links'], 1)
        self.new(A, C, HOUR2)  # A names C: a loop
        self.assertEqual(self.run_savings(C)['worker']['links'], 3)

    def test_old_marker_and_mention(self):
        self.old(A)
        self.new(B, A, HOUR2, mark='cg')
        self.assertEqual(self.run_savings(B)['worker']['links'], 1)
        self.write(C, [user('Что делает ' + block(A) + ' в начале?', HOUR2), call('f', HOUR2, cc=5)])
        self.assertIsNone(self.run_savings(C)['last'])

    def test_project_sums_index_convs(self):
        self.old(A)
        self.new(B, A, HOUR2)
        self.new(C, B, '2026-11-09T15:00:00.000Z')
        r = self.run_savings(C, [B, 'nosuch'])
        self.assertEqual(r['project']['links'], 2)  # B is in both lists, counted once
        self.assertAlmostEqual(r['project']['usd'], self.run_savings(C)['worker']['usd'], 6)
        r = self.run_savings(C)
        self.assertIsNone(r['project'])
        self.assertEqual(r['worker']['links'], 2)

    def test_no_handoff(self):
        self.old(A)
        r = self.run_savings(A)
        self.assertIsNone(r['last'])
        self.assertEqual(r['worker'], {'usd': 0, 'links': 0, 'calls': 0})

    def test_cache_reused_and_refreshed(self):
        self.old(A)
        self.new(B, A, HOUR2)
        first = self.run_savings(B)
        real, opened = builtins.open, []

        def spy(path, *a, **kw):
            if str(path).endswith('.jsonl'):
                opened.append(path)
            return real(path, *a, **kw)

        with mock.patch('builtins.open', spy):
            self.assertEqual(self.run_savings(B), first)
            self.assertEqual(opened, [])
            with real(os.path.join(self.dir, 'p', B + '.jsonl'), 'a', encoding='utf-8') as f:
                f.write(json.dumps(call('x', HOUR2, cr=70000)) + '\n')
            self.assertEqual(self.run_savings(B)['worker']['calls'], 1)
            self.assertTrue(opened)

    def test_broken_cache_is_empty(self):
        self.old(A)
        self.new(B, A, HOUR2)
        with open(self.cache, 'w') as f:
            f.write('{broken')
        self.assertEqual(self.run_savings(B)['worker']['links'], 1)

    def test_command_with_unreadable_index(self):
        self.old(A)
        self.new(B, A, HOUR2)
        db = os.path.join(self.dir, 'index.db')
        with open(db, 'wb') as f:
            f.write(b'not a database' * 100)
        p = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cg.py'), '--db', db, '--projects', self.dir,
                            'savings', '--session', B, '--cwd', self.dir], capture_output=True)
        out = json.loads(p.stdout.decode('utf-8'))
        self.assertEqual((out['worker']['links'], out['project']), (1, None))

    def test_output_is_in_the_window_not_in_the_first_price(self):
        self.old(A)
        self.write(B, [user(block(A), HOUR2), call('fb', HOUR2, cc=60586, inp=2, out=500)])
        r = self.run_savings(B)['last']
        self.assertEqual(r['new_tokens'], 61088)  # the window the next calls carry
        self.assertAlmostEqual(r['usd'], OLD * 8e-6 - (60586 * 8e-6 + 2 * 4e-6), 6)  # the first call's output is not in the price

    def test_unparsable_time_counts_as_warm(self):
        self.old(A, at='no time')
        self.new(B, A, HOUR2)
        self.assertAlmostEqual(self.run_savings(B)['last']['usd'], OLD * 0.2e-6 - (60586 * 8e-6 + 2 * 4e-6), 6)

    def test_vanished_transcript_is_skipped(self):
        self.old(A)
        self.new(B, A, HOUR2)
        gone = os.path.join(self.dir, 'p', 'gone.jsonl')
        real = savings._paths
        with mock.patch.object(savings, '_paths', lambda p: {**real(p), 'gone': gone}):
            r = self.run_savings(B, ['gone'])
        self.assertEqual((r['worker']['links'], r['project']['links']), (1, 1))

    def test_unwritable_cache_costs_nothing(self):
        self.old(A)
        self.new(B, A, HOUR2)
        r = savings.savings(self.dir, B, None, os.path.join(self.dir, 'no', 'such', 'savings-cache.json'))
        self.assertEqual(r['worker']['links'], 1)

    def test_command_sums_handoffs_of_the_project_from_a_real_index(self):
        self.old(A)
        self.new(B, A, HOUR2)
        self.new(C, B, '2026-11-09T15:00:00.000Z')
        D, S = ('%08x-0000-0000-0000-000000000000' % n for n in (0xd, 0x5))
        self.new(D, A, '2026-11-09T16:00:00.000Z')  # a handoff chat outside the current chain
        self.new(S, A, '2026-11-09T17:00:00.000Z')  # looks like one, but is a subagent of C
        db = os.path.join(self.dir, 'index.db')
        conn = memdb.connect(db)
        key = project_keys(self.dir, {})[0]
        for conv, parent in ((A, ''), (B, ''), (C, ''), (D, ''), (S, C)):
            conn.execute('INSERT INTO convs VALUES(?, ?, ?, ?)', (conv, parent, T, T))
            conn.execute('INSERT INTO conv_projects VALUES(?, ?)', (conv, key))
        conn.commit()
        conn.close()
        p = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cg.py'), '--db', db, '--projects', self.dir,
                            'savings', '--session', C, '--cwd', self.dir], capture_output=True)
        out = json.loads(p.stdout.decode('utf-8'))
        self.assertEqual((out['worker']['links'], out['project']['links']), (2, 3))


if __name__ == '__main__':
    unittest.main()
