import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import cg
from memdb import connect
from stats import metrics, note_markdown
from test_texts import use_lang

CG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cg.py')
IN_1M = {'input_tokens': 1_000_000, 'output_tokens': 0, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}  # sonnet-5: $2.00
WRITE_1M = dict(IN_1M, input_tokens=0, cache_creation_input_tokens=1_000_000)  # a 5 min cache write of 1M tokens: $2.50


def owner_line(text, ts):
    return dict(type='user', timestamp=ts, cwd='C:\\a', message={'role': 'user', 'content': text})


def call(mid, ts, usage):
    m = {'id': mid, 'model': 'claude-sonnet-5', 'role': 'assistant', 'usage': usage, 'content': [{'type': 'text', 'text': 'ответ'}]}
    return dict(type='assistant', timestamp=ts, cwd='C:\\a', message=m)


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

    def test_unreadable_transcript_is_reported_with_the_figures(self):
        real_open = open

        def deny(path, *a, **k):  # the main chat cannot be opened: neither by the index nor by the audit
            if str(path).endswith('S.jsonl'):
                raise PermissionError(13, 'denied', path)
            return real_open(path, *a, **k)

        with mock.patch('builtins.open', deny):
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
