import builtins
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import cc_token_audit
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
        self.assertEqual(r['worker'], {'usd': 0, 'lean': {'usd': 0, 'agents': 0}, 'links': 0, 'calls': 0})

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

    def test_cache_of_another_shape_is_empty(self):
        self.old(A)
        self.new(B, A, HOUR2)
        for bad in ('{"stray": 1}', '{"v": 1, "files": {}}', '[]', '{"v": 2, "files": {"nobar": {"size": 1}, "read_new|x": {"size": 1, "mtime": 1}}}'):
            with open(self.cache, 'w') as f:
                f.write(bad)
            self.assertEqual(self.run_savings(B)['worker']['links'], 1, bad)
            with open(self.cache, encoding='utf-8') as f:
                self.assertEqual(json.load(f)['v'], 2)  # and the next write replaces it

    def test_first_call_5m_write_is_priced_at_the_5m_rate(self):
        self.old(A)
        with open(os.path.join(self.dir, 'p', B + '.jsonl'), 'w', encoding='utf-8') as f:
            for o in (user(block(A), HOUR2), call('fb', HOUR2, cc=60586, inp=2)):
                if o['type'] == 'assistant':
                    o['message']['usage']['cache_creation'] = {'ephemeral_5m_input_tokens': 60586, 'ephemeral_1h_input_tokens': 0}
                f.write(json.dumps(o) + chr(10))
        self.assertAlmostEqual(self.run_savings(B)['last']['usd'], OLD * 8e-6 - (60586 * 5e-6 + 2 * 4e-6), 6)

    def cg(self, *args, home=None):
        code = 'import sys, crypto, cg; crypto.HOME_DIR = sys.argv[1]; crypto.OLD_HOME_DIR = sys.argv[1] + "-old"; cg.main(sys.argv[2:])'
        here = os.path.dirname(os.path.abspath(__file__))
        p = subprocess.run([sys.executable, '-c', code, home, *args], capture_output=True, cwd=here)
        return json.loads(p.stdout.decode('utf-8'))

    def test_command_without_a_findable_index_still_answers(self):
        self.old(A)
        self.new(B, A, HOUR2)
        home = os.path.join(self.dir, 'home')
        os.makedirs(home)
        with open(os.path.join(home, 'settings.json'), 'w') as f:
            f.write('{broken')
        out = self.cg('--projects', self.dir, 'savings', '--session', B, '--cwd', self.dir, home=home)
        self.assertEqual((out['worker']['links'], out['project'], 'error' in out), (1, None, False))
        self.assertFalse(os.path.exists(os.path.join(home, 'index.db')))
        self.assertTrue(os.path.exists(os.path.join(home, 'savings-cache.json')))

    def test_cache_lies_with_the_settings_not_beside_an_index_folder(self):
        self.old(A)
        self.new(B, A, HOUR2)
        home, idx = os.path.join(self.dir, 'home'), os.path.join(self.dir, 'idx')
        os.makedirs(home)
        os.makedirs(idx)
        memdb.connect(os.path.join(idx, 'index.db')).close()
        with open(os.path.join(home, 'settings.json'), 'w') as f:
            json.dump({'indexDir': idx}, f)
        out = self.cg('--projects', self.dir, 'savings', '--session', B, '--cwd', self.dir, home=home)
        self.assertEqual(out['worker']['links'], 1)
        self.assertEqual(sorted(n for n in os.listdir(idx) if not n.startswith('index.')), [])
        self.assertTrue(os.path.exists(os.path.join(home, 'savings-cache.json')))


def acall(mid, ts, model='claude-sonnet-5-5', inp=0, c5=0, c1=0, cr=0):
    o = call(mid, ts, model=model, cc=c5 + c1, cr=cr, inp=inp, isSidechain=True)
    o['message']['usage']['cache_creation'] = {'ephemeral_5m_input_tokens': c5, 'ephemeral_1h_input_tokens': c1}
    return o


def rec(typ='coder', ts=T, model='claude-sonnet-5-5', usage=(0, 0, 8000, 2000), calls=3, workflow=False, resumes=(), cw1h_total=0):
    return {'type': typ, 'ts': ts, 'model': model, 'usage': list(usage), 'calls': calls, 'workflow': workflow, 'resumes': list(resumes), 'cw1h_total': cw1h_total}


REF = {'w': 50000, 'r': 10000, 'start': 60000, 'n': 3, 'since': T}


class LeanTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.cache = os.path.join(self.dir, 'savings-cache.json')

    def agent(self, sid, name, typ, objs, wf=False, meta=True):
        d = os.path.join(self.dir, 'p', sid, 'subagents', *(['workflows', 'wf_1'] if wf else []))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name + '.jsonl'), 'w', encoding='utf-8') as f:
            f.write(''.join(json.dumps(o) + '\n' for o in objs))
        if meta:
            with open(os.path.join(d, name + '.meta.json'), 'w', encoding='utf-8') as f:
                json.dump({'agentType': typ}, f)
        return os.path.join(d, name + '.jsonl')

    def main(self, sid, objs):
        os.makedirs(os.path.join(self.dir, 'p'), exist_ok=True)
        with open(os.path.join(self.dir, 'p', sid + '.jsonl'), 'w', encoding='utf-8') as f:
            f.write(''.join(json.dumps(o) + '\n' for o in objs))

    def test_agent_start_reads_type_and_first_call(self):
        p = self.agent(A, 'agent-1', 'alethite:coder', [acall('m1', T, inp=3, c5=10, c1=20, cr=30), acall('m1', T, cr=99), acall('m2', T, cr=40),
                                                       acall('m3', T, model='<synthetic>', cr=40)])
        r = savings.agent_start(p)
        self.assertEqual((r['type'], r['ts'], r['model'], r['usage'], r['calls'], r['workflow']), ('alethite:coder', T, 'claude-sonnet-5-5', [3, 10, 20, 30], 2, False))
        w = self.agent(A, 'agent-2', 'alethite:scout', [acall('m1', T, cr=5)], wf=True)
        self.assertTrue(savings.agent_start(w)['workflow'])

    def test_agent_start_without_meta_is_none(self):
        self.assertIsNone(savings.agent_start(self.agent(A, 'agent-1', 'x', [acall('m1', T, cr=5)], meta=False)))
        self.assertIsNone(savings.agent_start(self.agent(A, 'agent-2', 'x', [])))  # no call at all

    def test_reference_takes_the_14_day_median_per_source(self):
        now = 1_800_000_000
        iso = lambda days: time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(now - days * 86400))
        gp = lambda ts, w, r: rec('general-purpose', ts, usage=(0, w, 0, r))
        ref = savings.reference([gp(iso(1), 10, 100), gp(iso(2), 30, 300), gp(iso(3), 20, 200), gp(iso(20), 9999, 9999),
                                 rec('workflow-subagent', iso(1), usage=(0, 5, 0, 7), workflow=True)], now)
        self.assertEqual((ref['agent']['w'], ref['agent']['r'], ref['agent']['start'], ref['agent']['n']), (20, 200, 220, 3))
        self.assertEqual((ref['workflow']['w'], ref['workflow']['n']), (5, 1))

    def test_reference_falls_back_to_the_last_20_and_then_to_none(self):
        now = 1_800_000_000
        self.assertEqual(savings.reference([], now), {'agent': None, 'workflow': None})
        old = [rec('general-purpose', '2026-0%d-01T00:00:00.000Z' % (n % 9 + 1), usage=(0, 100, 0, 0)) for n in range(5)]
        self.assertEqual(savings.reference(old, now)['agent']['n'], 5)  # all older than 14 days
        many = [rec('general-purpose', '2026-01-%02dT00:00:00.000Z' % (n + 1), usage=(0, n, 0, 0)) for n in range(25)] + [rec('general-purpose', None, usage=(0, 1000, 0, 0))]
        self.assertEqual(savings.reference(many, now)['agent']['n'], 20)
        self.assertEqual(savings.reference([rec('general-purpose', None, usage=(0, 50, 0, 0))], now)['agent']['w'], 50)  # no ts: only as a fallback

    def p(self):
        return cc_token_audit.price('claude-sonnet-5-5', 10000)[0]

    def test_lean_usd_prices_the_first_write_and_later_reads(self):
        p = self.p()
        self.assertAlmostEqual(savings.lean_usd(rec(), REF), (42000 * p[2] + 8000 * p[3] + 2 * 50000 * p[3]) / 1e6)
        self.assertEqual(savings.lean_usd(rec(), None), 0)

    def test_lean_usd_is_never_negative(self):
        p = self.p()
        self.assertEqual(savings.lean_usd(rec(usage=(0, 0, 90000, 90000), calls=3), REF), 0)
        self.assertAlmostEqual(savings.lean_usd(rec(usage=(0, 0, 0, 2000), calls=1), REF), 8000 * p[3] / 1e6)  # a warm prefix: no write was saved

    def test_resident_net_can_be_negative(self):
        p = self.p()
        r = rec('alethite:resident', usage=(0, 0, 50000, 10000), calls=1, cw1h_total=200000)
        base = savings.lean_usd(r, REF)
        self.assertAlmostEqual(base, -200000 * (p[2] - p[1]) / 1e6)
        self.assertLess(base, 0)
        self.assertAlmostEqual(savings.lean_usd(dict(r, resumes=[100000]), REF) - base, 100000 * (p[1] - p[3]) / 1e6)

    def test_resident_resumes_need_a_pause_of_5_to_60_minutes(self):
        t = lambda m: '2026-11-09T%02d:%02d:00.000Z' % divmod(m, 60)
        for gap, n in ((3, 0), (20, 1), (70, 0)):
            p = self.agent(A, 'agent-%d' % gap, 'alethite:resident', [acall('m1', t(600), cr=1), acall('m2', t(600 + gap), cr=777)])
            self.assertEqual(savings.agent_start(p)['resumes'], [777] * n, gap)

    def test_unpriced_model_counts_at_the_default_price(self):
        self.assertEqual(savings.lean_usd(rec(model='foo'), REF), savings.lean_usd(rec(model='claude-sonnet-5'), REF))

    def test_agent_without_time_is_a_fallback_and_counts(self):
        p = self.agent(A, 'agent-1', 'alethite:coder', [dict(acall('m1', T, c1=8000, cr=2000), timestamp=None)])
        r = savings.agent_start(p)
        self.assertIsNone(r['ts'])
        self.assertGreater(savings.lean_usd(r, REF), 0)

    def seed(self):
        """A holds the general-purpose reference, B (a handoff of A) a coder; returns the coder's expected saving."""
        self.main(A, [call('oa', T, cr=OLD)])
        self.agent(A, 'agent-g', 'general-purpose', [acall('g1', T, c5=50000, cr=10000)])
        self.main(B, [user(block(A), HOUR2), call('fb', HOUR2, cc=60586, inp=2)])
        self.agent(B, 'agent-c', 'alethite:coder', [acall('c1', T, c1=8000, cr=2000), acall('c2', T, cr=3000), acall('c3', T, cr=3000)])
        return savings.lean_usd(rec(), REF)

    def test_worker_and_project_include_lean_agents_once(self):
        x = self.seed()
        base = savings.savings(self.dir, B, None, os.path.join(self.dir, 'nocache.json'))['last']['usd']
        r = savings.savings(self.dir, B, [A, B], self.cache)
        for k in ('worker', 'project'):
            self.assertEqual(r[k]['lean']['agents'], 1, k)
            self.assertAlmostEqual(r[k]['lean']['usd'], x, 9)
            self.assertAlmostEqual(r[k]['usd'], base + x, 9)

    def test_agent_read_before_its_meta_is_read_again(self):
        self.seed()
        p = self.agent(B, 'agent-late', 'alethite:coder', [acall('l1', T, c1=8000, cr=2000)], meta=False)
        self.assertEqual(savings.savings(self.dir, B, [A, B], self.cache)['project']['lean']['agents'], 1)
        with open(p[:-6] + '.meta.json', 'w', encoding='utf-8') as f:
            json.dump({'agentType': 'alethite:coder'}, f)
        self.assertEqual(savings.savings(self.dir, B, [A, B], self.cache)['project']['lean']['agents'], 2)

    def test_agents_of_a_chat_in_two_folders_come_from_the_first_only(self):
        self.seed()
        self.assertEqual(len(savings._agents(self.dir, savings._paths(self.dir))[B]), 1)
        q = os.path.join(self.dir, 'q')  # the same session id under a later folder, with an agent of its own
        os.makedirs(os.path.join(q, B, 'subagents'))
        shutil.copy(os.path.join(self.dir, 'p', B, 'subagents', 'agent-c.jsonl'), os.path.join(q, B, 'subagents', 'agent-x.jsonl'))
        shutil.copy(os.path.join(self.dir, 'p', B + '.jsonl'), os.path.join(q, B + '.jsonl'))
        self.assertEqual(len(savings._agents(self.dir, savings._paths(self.dir))[B]), 1)

    def test_unchanged_agent_files_are_not_read_again(self):
        self.seed()
        first = savings.savings(self.dir, B, [A, B], self.cache)
        real, opened = builtins.open, []

        def spy(path, *a, **kw):
            if 'agent-' in str(path):
                opened.append(path)
            return real(path, *a, **kw)

        with mock.patch('builtins.open', spy):
            self.assertEqual(savings.savings(self.dir, B, [A, B], self.cache), first)
        self.assertEqual(opened, [])


if __name__ == '__main__':
    unittest.main()
