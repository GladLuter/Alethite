import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import cc_token_audit
import cg
import savings
from memdb import connect
from stats import metrics, note_markdown
from test_texts import use_lang
from texts import t

CG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cg.py')
IN_1M = {'input_tokens': 1_000_000, 'output_tokens': 0, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}  # sonnet-5: $2.00
WRITE_1M = dict(IN_1M, input_tokens=0, cache_creation_input_tokens=1_000_000)  # a 5 min cache write of 1M tokens: $2.50


def owner_line(text, ts):
    return dict(type='user', timestamp=ts, cwd='C:\\a', message={'role': 'user', 'content': text})


def call(mid, ts, usage):
    m = {'id': mid, 'model': 'claude-sonnet-5', 'role': 'assistant', 'usage': usage, 'content': [{'type': 'text', 'text': 'ответ'}]}
    return dict(type='assistant', timestamp=ts, cwd='C:\\a', message=m)


def acall(mid, ts, **u):
    c5, c1 = u.get('c5', 0), u.get('c1', 0)
    usage = dict(IN_1M, input_tokens=0, cache_creation_input_tokens=c5 + c1, cache_read_input_tokens=u.get('cr', 0),
                 cache_creation={'ephemeral_5m_input_tokens': c5, 'ephemeral_1h_input_tokens': c1})
    return call(mid, ts, usage)


CODER = {'type': 'alethite:coder', 'ts': '2026-10-05T10:00:02.000Z', 'model': 'claude-sonnet-5-5', 'usage': [0, 0, 8000, 2000], 'calls': 2, 'workflow': False,
         'resumes': [], 'cw1h_total': 8000}


class StatsTest(unittest.TestCase):
    def setUp(self):
        use_lang(self, 'ru')  # the note of stage 1
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.projects = os.path.join(d.name, 'projects')
        self.db = os.path.join(d.name, 'db', 'index.db')
        self.conn = connect(self.db)
        self.addCleanup(self.conn.close)
        # the main chat: one call before the window, two owner messages and two calls inside it (the second one rewrites the cache after 8 days idle);
        # a subagent call ($2.00) costs but is not main
        self.write('P/S.jsonl', [owner_line('давно', '2026-09-01T10:00:00.000Z'), call('m0', '2026-09-01T10:00:01.000Z', IN_1M),
                                 owner_line('первое', '2026-10-05T10:00:00.000Z'), call('m1', '2026-10-05T10:00:01.000Z', IN_1M),
                                 owner_line('второе', '2026-10-13T10:00:00.000Z'), call('m2', '2026-10-13T10:00:01.000Z', WRITE_1M)])
        self.write('P/S/subagents/agent-1.jsonl', [call('a1', '2026-10-05T10:00:02.000Z', IN_1M)])

    def write(self, name, objs):
        path = os.path.join(self.projects, *name.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(''.join(json.dumps(o, ensure_ascii=False) + '\n' for o in objs).encode('utf-8'))

    def test_metrics_from_synthetic_history(self):
        m = metrics(self.projects, self.conn, '2026-10-01')
        self.assertEqual(m['owner_msgs'], 2)
        self.assertAlmostEqual(m['usd_per_owner_msg'], m['cost_main'] / 2)
        self.assertAlmostEqual(m['cost_main'], 4.5)
        self.assertEqual((m['main_calls'], m['median_main_context'], m['since'], m['until']), (2, 1_000_000, '2026-10-01', None))
        self.assertAlmostEqual(m['rewrite_share'], 2.5 / 6.5)  # of all cost, the subagent's $2.00 included
        self.assertAlmostEqual(m['idle60_share'], 2.5 / 6.5)

    def test_audit_cost_and_cache_rewrites(self):  # the cost and cache-rewrite checks of the measure /al-stats stands on
        """Tiny synthetic transcript: duplicate message ids, a tool_result, an attachment, a compaction, a subagent chain."""
        att1, att2 = {'type': 'skill_listing', 'content': 'x' * 350}, {'type': 'task_reminder', 'content': 'z' * 100}
        # bookkeeping attachments (must be ignored in baseline and growth split), 4 kinds
        noise = [{'type': 'attachment', 'attachment': a} for a in (
            {'type': 'hook_success', 'hookEvent': 'PostToolUse', 'command': 'c' * 500, 'stdout': '{}', 'content': ''},
            {'type': 'credential_org', 'organizationUuid': 'u'},
            {'type': 'command_permissions', 'allowedTools': []},
            {'type': 'async_hook_response', 'exitCode': 1, 'stdout': '', 'response': '{}', 'stderr': 'e' * 100})]
        u1 = {'input_tokens': 10, 'cache_creation_input_tokens': 1000, 'cache_read_input_tokens': 0, 'output_tokens': 20,
              'output_tokens_details': {'thinking_tokens': 10}, 'cache_creation': {'ephemeral_1h_input_tokens': 0, 'ephemeral_5m_input_tokens': 1000}}
        u2 = {'input_tokens': 5, 'cache_creation_input_tokens': 100, 'cache_read_input_tokens': 1000, 'output_tokens': 30,
              'cache_creation': {'ephemeral_1h_input_tokens': 100, 'ephemeral_5m_input_tokens': 0}}
        u3 = {'input_tokens': 3, 'cache_creation_input_tokens': 200, 'cache_read_input_tokens': 0, 'output_tokens': 10}
        u4 = {'input_tokens': 7, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0, 'output_tokens': 3}

        def asst(mid, ts, usage, content, model='claude-sonnet-5'):
            return {'type': 'assistant', 'timestamp': ts, 'message': {'id': mid, 'model': model, 'usage': usage, 'content': content}}
        text = [{'type': 'text', 'text': 'hi'}]
        main = noise + [
            {'type': 'attachment', 'attachment': att1},
            {'type': 'user', 'message': {'content': 'hello'}},
            asst('m1', '2026-01-05T10:00:00Z', u1, text),
            asst('m1', '2026-01-05T10:00:01Z', dict(u1, output_tokens=50), [{'type': 'tool_use', 'id': 't1', 'name': 'Read', 'input': {}}]),
            asst('m1', '2026-01-05T10:00:02Z', u1, text),  # duplicate with lower output_tokens must not win
            asst('s', '2026-01-05T10:00:02Z', u1, text, '<synthetic>'),
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'y' * 350}]}},
            {'type': 'attachment', 'attachment': att2},
        ] + noise + [
            asst('m2', '2026-01-05T10:01:00Z', u2, text),
            {'type': 'system', 'subtype': 'compact_boundary'},
            {'type': 'user', 'isCompactSummary': True, 'message': {'content': 'summary'}},
            asst('m3', '2026-02-02T10:00:00Z', u3, []),
        ]
        u5 = {'input_tokens': 6, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0, 'output_tokens': 2}  # 6/7 of m4: small drop
        sub = [{'type': 'user', 'message': {'content': 'do it!'}}, asst('m4', '2026-02-03T10:00:00Z', u4, text),
               asst('m5', '2026-02-03T10:00:05Z', u5, text)]
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, 'proj-a', 's1', 'subagents'))
            with open(os.path.join(tmp, 'proj-a', 's1.jsonl'), 'w', encoding='utf-8') as f:
                f.write('\n'.join(json.dumps(x) for x in main) + '\nnot json\n')
            with open(os.path.join(tmp, 'proj-a', 's1', 'subagents', 'agent-1.jsonl'), 'w', encoding='utf-8') as f:
                f.write('\n'.join(json.dumps(x) for x in sub) + '\n')
            R = cc_token_audit.analyze(tmp)
        cats = {c['category']: c for c in R['categories']}
        close = lambda a, b: abs(a - b) < 1e-9
        h = len(json.dumps(att2))
        exp = ((10 * 2 + 1000 * 2.5) + 50 * 10 + (5 * 2 + 100 * 4 + 1000 * .2) + 30 * 10 + (3 * 2 + 200 * 2.5) + 10 * 10 + (7 * 2) + 3 * 10 + (6 * 2) + 2 * 10) / 1e6
        assert R['totals']['calls'] == 5 and R['totals']['chains'] == 2, R['totals']  # dedupe + synthetic skipped
        assert R['meta']['odd']['small_context_drops_scaled_not_reset'] == 1 and 'context_resets_without_compact_boundary' not in R['meta']['odd']
        assert R['meta']['lines_skipped'] == 1 and R['meta']['files_parsed'] == 2
        assert close(R['totals']['cost_usd'], exp) and close(sum(c['cost_usd'] for c in R['categories']), exp)  # cost split sums to total
        assert close(sum(R['cost_by_token_type'].values()), exp)
        assert set(R['by_kind']) == {'main', 'subagent'} and R['by_kind']['subagent']['calls'] == 2
        assert cats['output:visible']['tokens_added'] == 40 + 30 + 10 + 3 + 2 and cats['output:thinking']['tokens_added'] == 10
        assert cats['assistant_output_in_context']['tokens_added'] == 40  # call 1 visible output, delta 95
        assert close(cats['tool:Read']['tokens_added'], 55 * 350 / (350 + h)) and close(cats['att:task_reminder']['tokens_added'], 55 * h / (350 + h))
        sa = len(json.dumps(att1))
        # bookkeeping attachments take no share of the baseline or the growth (the numbers above only add up without them)
        assert not [k for k in cats if k.split(':')[-1] in ('hook_success', 'credential_org', 'command_permissions', 'async_hook_response')], cats.keys()
        assert {k: v['entries'] for k, v in R['meta']['attachments_excluded_as_bookkeeping'].items()} == {k: 2 for k in cc_token_audit.EXCLUDED}
        assert close(cats['baseline:tools+other']['tokens_added'], 1010 - (sa + 5) / 3.5 + 7 - 6 / 3.5)
        assert close(cats['baseline:att:skill_listing']['tokens_added'], sa / 3.5)
        # context tokens added = first prompt (1010) + growth (95) + reset (0) + subagent first prompt (7)
        ctx_added = sum(c['tokens_added'] for k, c in cats.items() if not k.startswith('output:'))
        assert close(ctx_added, 1010 + 95 + 0 + 7), ctx_added
        assert R['stats']['all']['compactions'] == 1 and R['meta']['compact_boundary_entries'] == 1
        assert R['stats']['main']['baseline_tokens_median'] == 1010 and R['stats']['all']['prompt_total_max'] == 1105
        assert not R['meta']['unpriced_model_calls']
        assert R['cache_rewrites']['total']['calls'] == 0  # nothing above 100k prompt_total in the chains above

        # cache rewrites: one call per cause, plus two near-misses (small cache write; prompt_total < 100k)
        def rw(mid, minute, model, inp, cci, cr, h1=0):
            u = {'input_tokens': inp, 'cache_creation_input_tokens': cci, 'cache_read_input_tokens': cr, 'output_tokens': 1,
                 'cache_creation': {'ephemeral_1h_input_tokens': h1}}
            return asst(mid, '2026-03-01T%02d:%02d:00Z' % (10 + minute // 60, minute % 60), u, text, model)
        S, O = 'claude-sonnet-5', 'claude-opus-5'
        kept = {'type': 'attachment', 'attachment': {'type': 'async_hook_response', 'exitCode': 2, 'stdout': '', 'response': '{}', 'stderr': 'e' * 136}}
        chain = [kept, noise[0],  # blocking async hook error is kept, hook_success is not
                 {'type': 'user', 'message': {'content': 'x'}},
                 rw('r1', 0, S, 1000, 150000, 0),                  # first call in chain: 150000 * 2.5
                 rw('r2', 90, S, 10, 120000, 50000, h1=120000),    # 90 min idle, 1h write: 120000 * 4
                 rw('r3', 100, S, 5, 90000, 50000),                # 10 min idle: 90000 * 2.5
                 rw('r4', 101, O, 5, 90000, 60000),                # model switch: 90000 * 6.25
                 rw('r5', 102, O, 5, 100000, 50000),               # nothing else explains it: 100000 * 6.25
                 {'type': 'system', 'subtype': 'compact_boundary'},
                 rw('r6', 103, O, 5, 100000, 30000),               # after compaction: 100000 * 6.25
                 rw('r7', 104, O, 5, 1000, 130000),                # not a rewrite: little cache written
                 rw('r8', 105, O, 5, 80000, 10000)]                # not a rewrite: prompt_total < 100k
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, 'proj-b'))
            with open(os.path.join(tmp, 'proj-b', 's2.jsonl'), 'w', encoding='utf-8') as f:
                f.write('\n'.join(json.dumps(x) for x in chain) + '\n')
            R2 = cc_token_audit.analyze(tmp)
        # same order as cc_token_audit.REWRITE_CAUSES: first call, after compaction, model switch, idle >= 60, idle 5-60, prefix changed
        W, want = R2['cache_rewrites'], [150000 * 2.5, 100000 * 6.25, 90000 * 6.25, 120000 * 4, 90000 * 2.5, 100000 * 6.25]
        assert list(W['by_cause']) == cc_token_audit.REWRITE_CAUSES
        for (cause, r), usd_ in zip(W['by_cause'].items(), want):
            assert r['calls'] == 1 and close(r['cost_usd'], usd_ / 1e6) and r['main']['calls'] == 1 and r['subagent']['calls'] == 0, cause
            assert close(r['share_of_all_cost'], r['cost_usd'] / R2['totals']['cost_usd'])
        assert W['total']['calls'] == 6 and close(W['total']['cost_usd'], sum(want) / 1e6)
        assert W['by_cause']['idle >= 60 min']['median_gap_min'] == 90 and W['by_cause']['idle 5-60 min']['median_gap_min'] == 10
        assert R2['stats']['all']['compactions'] == 1
        cats2 = {c['category'] for c in R2['categories']}
        assert 'baseline:att:async_hook_response' in cats2 and 'baseline:att:hook_success' not in cats2

    def test_no_messages_no_division(self):
        m = metrics(self.projects, self.conn, '2030-01-01')
        self.assertIsNone(m['usd_per_owner_msg'])
        self.assertEqual((m['owner_msgs'], m['cost_main'], m['main_calls']), (0, 0, 0))

    def test_until_excludes_later_calls(self):
        m = metrics(self.projects, self.conn, '2026-10-01', '2026-10-13')
        self.assertLess(m['cost_main'], metrics(self.projects, self.conn, '2026-10-01')['cost_main'])
        self.assertEqual((m['owner_msgs'], m['main_calls'], m['rewrite_share'], m['until']), (1, 1, 0, '2026-10-13'))

    def test_note_has_host_baseline_and_rule_cost(self):
        m = metrics(self.projects, self.conn, '2026-10-01')
        n = note_markdown(m, 'PC-1', '2026-11-13', [{'kind': 'seven_day', 'percentUsed': 40}], 350)
        self.assertIn('host: PC-1', n)
        self.assertIn('date: 2026-11-13', n)
        self.assertIn('usd_per_owner_msg: 2.25', n)
        self.assertIn('until: null', n)
        self.assertIn('5.2', n)
        self.assertIn('70% за 4,5 дня', n)
        self.assertIn('40%', n)
        self.assertIn('100 токенов × 2 вызовов', n)

    def test_the_memory_in_the_context_is_measured(self):  # stage 3: measure before compressing more
        recall = {'type': 'tool_use', 'id': 'r1', 'name': 'mcp__cacheguard__recall', 'input': {'query': 'кэш'}}
        other = {'type': 'tool_use', 'id': 'b1', 'name': 'Bash', 'input': {'command': 'ls'}}
        result = lambda i, text: dict(type='user', timestamp='2026-10-06T10:00:02.000Z', message={'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': i, 'content': [{'type': 'text', 'text': text}]}]})
        self.write('P/T.jsonl', [
            owner_line('[cg-handoff]\n' + 'п' * 673 + '\n[/cg-handoff]\n\nчерновик', '2026-10-06T10:00:00.000Z'),  # 13 + 673 + 14 = 700 characters
            dict(call('t1', '2026-10-06T10:00:01.000Z', IN_1M), message={'id': 't1', 'model': 'claude-sonnet-5', 'role': 'assistant', 'usage': IN_1M,
                                                                        'content': [recall, other]}),
            result('r1', 'x' * 700), result('b1', 'y' * 7000),
            owner_line('[cg-handoff]\nдо периода[/cg-handoff]', '2026-09-01T10:00:00.000Z'),
            owner_line('а что делает [cg-handoff] в начале передачи?', '2026-10-06T10:00:03.000Z')])  # a mention is no handoff
        m = metrics(self.projects, self.conn, '2026-10-01')
        self.assertEqual((m['recall_calls'], m['recall_tokens'], m['handoffs'], m['handoff_tokens']), (1, 200, 1, 200))
        self.assertGreaterEqual(m['recall_cost'], 0)
        n = note_markdown(m, 'PC-1', '2026-11-13', [], 350, 950, 'ultra')
        self.assertIn('## Память в контексте', n)
        self.assertIn('| MEMORY.md | в каждом из %d запросов | 950 | — |' % m['main_calls'], n)
        self.assertIn('| результаты recall и recall_more | 1 вызовов | 200 |', n)
        self.assertIn('| передачи дел новому работнику | 1 раз | 200 | — |', n)
        self.assertIn('ultra', n)
        self.assertIn('memory_index_tokens: 950', n)
        self.assertIn('recall_share: ', n)  # the summary note reads it

    def test_mod_tools_of_both_names_are_counted(self):
        tool = lambda i, name: {'type': 'tool_use', 'id': i, 'name': name, 'input': {'query': 'кэш'}}
        result = lambda i: dict(type='user', timestamp='2026-10-06T10:00:02.000Z', message={'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': i, 'content': 'x' * 700}]})
        self.write('P/T.jsonl', [
            dict(call('t1', '2026-10-06T10:00:01.000Z', IN_1M), message={'id': 't1', 'model': 'claude-sonnet-5', 'role': 'assistant', 'usage': IN_1M,
                                                                        'content': [tool('r1', 'mcp__cacheguard__recall'), tool('r2', 'mcp__alethite__recall_more')]}),
            result('r1'), result('r2')])
        real = cc_token_audit.analyze

        def two_servers(*a, **k):  # the audit names a category after the server: both of the mod's add up, others do not
            R = real(*a, **k)
            R['categories'] = [{'category': 'tool:mcp:cacheguard', 'cost_usd': 0.5, 'share': 0.1}, {'category': 'tool:mcp:alethite', 'cost_usd': 0.25, 'share': 0.05},
                               {'category': 'tool:mcp:other', 'cost_usd': 9, 'share': 0.9}]
            return R

        with mock.patch('stats.cc_token_audit.analyze', two_servers):
            m = metrics(self.projects, self.conn, '2026-10-01')
        self.assertEqual((m['recall_calls'], m['recall_tokens']), (2, 400))
        self.assertAlmostEqual(m['recall_cost'], 0.75)
        self.assertAlmostEqual(m['recall_share'], 0.15)

    def test_handoffs_of_both_names_are_counted(self):
        old, new = '[cg-handoff]\nстарая\n[/cg-handoff]', '[al-handoff]\nновая\n[/al-handoff]'
        self.write('P/T.jsonl', [owner_line(old, '2026-10-06T10:00:00.000Z'), owner_line('  ' + new + '\n\nчерновик', '2026-10-06T10:00:01.000Z')])
        m = metrics(self.projects, self.conn, '2026-10-01')
        self.assertEqual((m['handoffs'], m['handoff_tokens']), (2, round((len(old) + len(new)) / 3.5)))

    def agent(self, name, typ, objs):
        self.write('P/S/subagents/%s.jsonl' % name, objs)
        with open(os.path.join(self.projects, 'P', 'S', 'subagents', name + '.meta.json'), 'w') as f:
            json.dump({'agentType': typ}, f)

    def test_metrics_count_lean_agents(self):
        T = '2026-10-05T10:00:02.000Z'
        m = metrics(self.projects, self.conn, '2026-10-01')
        self.assertEqual((m['subagents'], m['lean_agents'], m['lean_usd']), (0, 0, 0))
        self.assertIn(t('stats_lean_none'), note_markdown(m, 'PC-1', '2026-11-13', [], 0))
        self.agent('agent-g', 'general-purpose', [acall('g1', T, c5=50000, cr=10000)])
        self.agent('agent-c', 'alethite:coder', [acall('c1', T, c1=8000, cr=2000), acall('c2', T, cr=3000)])
        self.agent('agent-s', 'alethite:scout', [acall('s1', '2026-09-05T10:00:00.000Z', c1=8000, cr=2000)])  # before the period
        m = metrics(self.projects, self.conn, '2026-10-01')
        self.assertEqual((m['subagents'], m['lean_agents']), (2, 1))
        self.assertGreater(m['lean_usd'], 0)
        self.assertEqual((m['lean_ref_tokens'], m['lean_ref_since']), (60000, '2026-10-05'))
        self.assertIn(t('stats_lean', total=2, lean=1, share='50%', usd='$%.2f' % m['lean_usd'], ref='60 000', since='2026-10-05'),
                      note_markdown(m, 'PC-1', '2026-11-13', [], 0))
        self.assertEqual(metrics(self.projects, self.conn, '2026-10-01', '2026-10-05')['subagents'], 0)  # until excludes the day
        self.assertEqual(metrics(self.projects, self.conn, '2026-10-01', gone={'X': {'agents': [CODER]}})['lean_agents'], 2)  # a removed chat still counts
        self.assertEqual(metrics(self.projects, self.conn, '2026-10-01', gone={'S': {'agents': [CODER]}})['lean_agents'], 1)  # on the disk too: the files count, once
        m = metrics(self.projects, self.conn, '2026-10-01', now=1_790_000_000)
        self.assertIn('subagents: 2', note_markdown(m, 'PC-1', '2026-11-13', [], 0))  # the frontmatter holds every metric

    def test_negative_lean_sum_reads_dearer_not_dollar_minus(self):
        m = metrics(self.projects, self.conn, '2026-10-01')
        m.update(subagents=2, lean_agents=1, lean_usd=-0.3, lean_ref_tokens=60000, lean_ref_since='2026-10-05')
        out = note_markdown(m, 'PC-1', '2026-11-13', [], 0)
        self.assertIn(t('stats_lean_dearer', total=2, lean=1, share='50%', usd='$0.30', ref='60 000', since='2026-10-05'), out)
        self.assertNotIn('$-', out)

    def test_analyze_missing_folder_is_json_error(self):
        self.projects = os.path.join(self.projects, 'nope')
        code, out = self.run_cg('--since', '2026-10-01', '--host', 'h', '--today', '2026-10-13', '--limits', '[]', '--rule-chars', '0')
        self.assertEqual((code, list(out)), (1, ['error']))
        self.assertIn('FileNotFoundError', out['error'])

    def test_lean_stats_reads_agent_files_through_the_cache(self):
        self.agent('agent-g', 'general-purpose', [acall('g1', '2026-10-05T10:00:02.000Z', c5=50000, cr=10000)])
        self.agent('agent-c', 'alethite:coder', [acall('c1', '2026-10-05T10:00:02.000Z', c1=8000, cr=2000)])
        cache = os.path.join(os.path.dirname(self.db), 'savings-cache.json')
        first = savings.lean_stats(self.projects, '2026-10-01', None, None, 1_790_000_000, cache)  # the audit reads these files on its own: lean_stats alone
        real_open, opened = open, []

        def count(path, *a, **k):
            if 'subagents' in str(path) and str(path).endswith('.jsonl'):
                opened.append(str(path))
            return real_open(path, *a, **k)

        with mock.patch('builtins.open', count):
            second = savings.lean_stats(self.projects, '2026-10-01', None, None, 1_790_000_000, cache)
        self.assertEqual(opened, [])
        self.assertEqual((first['agents'], first['lean']), (2, 1))
        self.assertEqual(second, first)

    def test_metrics_reads_each_transcript_once(self):
        real_open, opened = open, []

        def count(path, *a, **k):
            if str(path).endswith('.jsonl'):
                opened.append(str(path))
            return real_open(path, *a, **k)

        with mock.patch('builtins.open', count), mock.patch('stats.memdb.update', return_value={'errors': []}):  # the index reads on its own
            metrics(self.projects, self.conn, '2026-10-01')
        self.assertEqual(sorted(opened), sorted(set(opened)))
        self.assertEqual(len(opened), 2)

    def test_a_transcript_older_than_since_is_skipped(self):  # its lines carry dates inside the period, only the file mtime says it is stale
        self.write('P/OLD.jsonl', [owner_line('[cg-handoff]\nстарое[/cg-handoff]', '2026-10-06T10:00:00.000Z')])
        old = os.path.join(self.projects, 'P', 'OLD.jsonl')
        self.assertEqual(metrics(self.projects, self.conn, '2026-10-01')['handoffs'], 1)  # fresh file: counted
        os.utime(old, (1788000000, 1788000000))  # 2026-08-29
        self.assertEqual(metrics(self.projects, self.conn, '2026-10-01')['handoffs'], 0)

    def test_note_without_messages_or_limits(self):
        m = metrics(self.projects, self.conn, '2030-01-01')
        n = note_markdown(m, 'PC-1', '2030-01-02', [], 0)
        self.assertIn('usd_per_owner_msg: null', n)
        self.assertIn('0 токенов', n)

    def run_cg(self, *args):
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONIOENCODING', 'PYTHONUTF8')}
        p = subprocess.run([sys.executable, CG, '--db', self.db, '--projects', self.projects, 'stats', *args], capture_output=True, env=env)
        return p.returncode, json.loads(p.stdout.decode('utf-8'))

    def test_cli_returns_metrics_and_note(self):
        limits = json.dumps([{'kind': 'seven_day', 'percentUsed': 40}])
        code, out = self.run_cg('--since', '2026-10-01', '--until', '2026-10-14', '--host', 'ПК-1', '--today', '2026-10-13', '--limits', limits, '--rule-chars', '350')
        self.assertEqual(code, 0)
        self.assertEqual((out['metrics']['owner_msgs'], out['metrics']['until']), (2, '2026-10-14'))
        self.assertIn('host: ПК-1', out['note'])
        self.assertEqual(out['errors'], [])

    def stats_out(self):  # in this process, so that open() can be patched
        a = cg._parser().parse_args(['--db', self.db, '--projects', self.projects, 'stats', '--since', '2026-10-01', '--host', 'h', '--today', '2026-10-13',
                                     '--limits', '[]', '--rule-chars', '0'])
        with mock.patch('cg.memdb.connect', return_value=self.conn):  # the test closes it; cg.py leaves that to the process exit
            return a.fn(a)

    def test_cli_passes_the_removed_chats_and_survives_their_failure(self):
        a = cg._parser().parse_args(['--db', self.db, '--projects', self.projects, 'stats', '--since', '2026-10-01', '--host', 'h', '--today', '2026-10-13',
                                     '--limits', '[]', '--rule-chars', '0'])
        for gone, effect in (({'X': {'agents': [CODER]}}, None), (None, ValueError('damaged'))):
            with mock.patch('cg.memdb.connect', return_value=self.conn), mock.patch('cleanup.gone', return_value=gone, side_effect=effect), \
                    mock.patch('stats.metrics', wraps=metrics) as spy:
                a.fn(a)
            self.assertEqual(spy.call_args.args[7], gone)

    def test_unreadable_transcript_is_reported_with_the_figures(self):
        real_open = open

        def deny(path, *a, **k):  # the main chat cannot be opened: neither by the index nor by the audit
            if str(path).endswith('S.jsonl'):
                raise PermissionError(13, 'denied', path)
            return real_open(path, *a, **k)

        with mock.patch('builtins.open', deny), contextlib.redirect_stderr(io.StringIO()):  # the audit's "unreadable file" line stays out of the suite output
            out = self.stats_out()
        self.assertEqual(out['metrics']['owner_msgs'], 0)  # the undercount the errors warn about
        self.assertIn('usd_per_owner_msg', out['metrics'])
        self.assertIn('host: h', out['note'])
        index, audit = out['errors']
        self.assertTrue(index['path'].endswith('S.jsonl') and index['error'].startswith('PermissionError'))
        self.assertEqual(audit, {'path': 'audit', 'error': 'files_unreadable: 1'})

    def test_unpriced_model_is_reported(self):
        self.write('P/T.jsonl', [owner_line('x', '2026-10-06T10:00:00.000Z'), dict(call('u1', '2026-10-06T10:00:01.000Z', IN_1M), message={
            'id': 'u1', 'model': 'mystery-9', 'role': 'assistant', 'usage': IN_1M, 'content': []})])
        self.assertEqual(self.stats_out()['errors'], [{'path': 'audit', 'error': 'unpriced_model_calls: mystery-9=1'}])

    def test_cli_bad_date_is_json_error(self):
        code, out = self.run_cg('--since', 'вчера', '--host', 'h', '--today', '2026-10-13', '--limits', '[]', '--rule-chars', '0')
        self.assertEqual(code, 1)
        self.assertIn('error', out)


if __name__ == '__main__':
    unittest.main()
