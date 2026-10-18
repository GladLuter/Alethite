import contextlib
import gc
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import cg
import memdb
import vectors
from transcripts import project_key

CG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cg.py')
TS = '2026-10-13T10:00:00.000Z'
USAGE = {'input_tokens': 10, 'cache_read_input_tokens': 5, 'cache_creation_input_tokens': 0, 'output_tokens': 3}


def owner_line(text, cwd='C:\\a'):
    return dict(type='user', timestamp=TS, cwd=cwd, message={'role': 'user', 'content': text})


def claude_line(text, cwd='C:\\a'):
    m = {'id': 'm1', 'model': 'claude-opus-5-5', 'role': 'assistant', 'usage': USAGE, 'content': [{'type': 'text', 'text': text}]}
    return dict(type='assistant', timestamp=TS, cwd=cwd, message=m)


class CgTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = d.name
        self.projects = os.path.join(d.name, 'projects')
        self.db = os.path.join(d.name, 'db', 'index.db')

    def write(self, name, objs):
        path = os.path.join(self.projects, *name.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(''.join(json.dumps(o, ensure_ascii=False) + '\n' for o in objs).encode('utf-8'))

    def run_cg(self, *args, db=None):
        # without these two Python writes stdout in cp1252 on Windows and dies on Cyrillic
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONIOENCODING', 'PYTHONUTF8')}
        p = subprocess.run([sys.executable, CG, '--db', db or self.db, '--projects', self.projects, *args], capture_output=True, env=env)
        return p.returncode, json.loads(p.stdout.decode('utf-8'))

    def test_cli_utf8(self):
        self.write('P/S.jsonl', [owner_line('ёжик в тумане')])
        code, out = self.run_cg('recall', '--query', 'ёжик', '--all')
        self.assertEqual(code, 0)
        self.assertIn('ёжик', out['hits'][0]['snippet'])
        self.assertEqual(out['errors'], [])

    def test_cli_error_is_json(self):
        code, out = self.run_cg('status', db=self.tmp)  # a folder instead of the database file
        self.assertEqual(code, 1)
        self.assertIn('error', out)

    def test_bad_arguments_are_json(self):
        code, out = self.run_cg('recall')
        self.assertEqual(code, 1)
        self.assertIn('error', out)

    def test_last_turn_unknown_session(self):
        self.assertEqual(self.run_cg('last-turn', '--session', 'nope'), (0, {'tokens': None, 'at': None, 'model': None}))

    def test_last_turn(self):
        self.write('P/S1.jsonl', [owner_line('вопрос'), claude_line('ответ')])
        self.assertEqual(self.run_cg('last-turn', '--session', 'S1'), (0, {'tokens': 18, 'at': TS, 'model': 'claude-opus-5-5'}))

    def test_index_status_expand(self):
        self.write('P/S1.jsonl', [owner_line('вопрос про ёжика'), claude_line('ответ')])
        code, out = self.run_cg('index')
        self.assertEqual((code, out['files'], out['rows'], out['errors']), (0, 1, 2, []))
        self.assertEqual(self.run_cg('index', '--rebuild')[1]['rows'], 2)
        code, out = self.run_cg('status')
        self.assertEqual((code, out['convs'], out['rows'], out['db']), (0, 1, 2, self.db))
        hit = self.run_cg('recall', '--query', 'ёжика', '--all')[1]['hits'][0]
        code, out = self.run_cg('expand', '--conv', hit['conv'], '--pos', str(hit['pos']))
        self.assertEqual((code, [r['kind'] for r in out['rows']]), (0, ['owner', 'claude']))

    def test_status_project(self):  # /cg-health shows which project «в этом проекте» means and how many conversations it finds
        self.write('A/S1.jsonl', [owner_line('здесь', cwd='C:\a')])
        self.write('A/S1/subagents/agent-a1.jsonl', [owner_line('суб', cwd='')])  # no cwd of its own: the parent's project
        self.write('B/S2.jsonl', [owner_line('там', cwd='C:\b')])
        self.run_cg('index')
        for cwd, n in (('C:\a', 2), ('C:\b', 1), ('C:\нет', 0)):
            code, out = self.run_cg('status', '--cwd', cwd)
            self.assertEqual((code, out['project'], out['convs']), (0, {'key': project_key(cwd), 'convs': n}, 3))
        self.assertNotIn('project', self.run_cg('status')[1])  # without --cwd the answer is as before

    def test_recall_scope(self):
        self.write('A/S1.jsonl', [owner_line('ёжик здесь', cwd='C:\\a')])
        self.write('B/S2.jsonl', [owner_line('ёжик там', cwd='C:\\b')])
        hits = lambda *scope: sorted(h['conv'] for h in self.run_cg('recall', '--query', 'ёжик', *scope)[1]['hits'])
        self.assertEqual(hits('--cwd', 'C:\\a'), ['S1'])
        self.assertEqual(hits('--all'), ['S1', 'S2'])
        self.assertEqual(len(self.run_cg('recall', '--query', 'ёжик', '--all', '--limit', '1')[1]['hits']), 1)

    def test_recall_says_what_the_vector_leg_did(self):
        self.write('P/S1.jsonl', [owner_line('ёжик в тумане')])
        code, out = self.run_cg('recall', '--query', 'ёжик', '--all')  # no vectors: words only, and the reason is told
        self.assertEqual((code, len(out['hits'])), (0, 1))
        self.assertNotEqual(out['vector'], 'ok')
        conn = memdb.connect(self.db)
        conn.execute("INSERT OR REPLACE INTO meta VALUES('embed_model', 'A')")  # vectors of another model: the model is not asked
        conn.close()
        self.assertIn(vectors.MODEL, self.run_cg('recall', '--query', 'ёжик', '--all')[1]['vector'])
        out = self.run_cg('recall', '--query', 'ёжик', '--all', '--model', 'B')[1]
        self.assertTrue(out['hits'] and 'B' in out['vector'])

    def test_embed_options(self):
        out = self.run_cg('embed', '--model', 'other', '--max-seconds', '5')  # an empty index: nothing to embed, so no Ollama is called
        self.assertEqual((out[0], out[1]['model'], out[1]['done'], out[1]['pending'], out[1]['errors']), (0, 'other', 0, 0, []))

    def test_embed_progress_on_stderr(self):
        self.write('P/S1.jsonl', [owner_line('вопрос'), claude_line('ответ')])
        self.run_cg('index')
        self.addCleanup(gc.collect)  # cg never closes its connection; a collected one releases the file that the temp folder cleanup deletes
        a = cg._parser().parse_args(['--db', self.db, '--projects', self.projects, 'embed'])
        err = io.StringIO()
        with mock.patch.object(vectors, 'ollama_embed', lambda texts, model: [[1.0] * 768 for _ in texts]), contextlib.redirect_stderr(err):
            out = a.fn(a)
        self.assertEqual((out['done'], out['pending'], out['errors']), (2, 0, []))
        self.assertIn('посчитано кусков: 2', err.getvalue())
        self.assertEqual(self.run_cg('status')[1]['vectors']['with_vectors'], 2)


if __name__ == '__main__':
    unittest.main()
