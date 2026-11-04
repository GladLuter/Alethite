import argparse
import contextlib
import gc
import io
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock
import cg
import crypto
import dialogs
import encrypt
import indexdir
import job
import lessons
import memdb
import texts
import vectors
from test_encrypt import FakeUI
from test_job import Scheduler
from test_texts import use_lang
from texts import t
from transcripts import project_key

CG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cg.py')
TS = '2026-10-13T10:00:00.000Z'
USAGE = {'input_tokens': 10, 'cache_read_input_tokens': 5, 'cache_creation_input_tokens': 0, 'output_tokens': 3}


def owner_line(text, cwd='C:\\a'):
    return dict(type='user', timestamp=TS, cwd=cwd, message={'role': 'user', 'content': text})


def claude_line(text, cwd='C:\\a'):
    m = {'id': 'm1', 'model': 'claude-opus-5-5', 'role': 'assistant', 'usage': USAGE, 'content': [{'type': 'text', 'text': text}]}
    return dict(type='assistant', timestamp=TS, cwd=cwd, message=m)


def setUpModule():
    # cg.main without --db moves the old mod folder: no test of this module may reach the real ~/.claude/cacheguard or ~/.claude/alethite
    d = tempfile.TemporaryDirectory()
    unittest.addModuleCleanup(d.cleanup)
    for name in ('HOME_DIR', 'OLD_HOME_DIR'):
        patch = mock.patch.object(crypto, name, os.path.join(d.name, name))
        patch.start()
        unittest.addModuleCleanup(patch.stop)
    patch = Scheduler().patched()  # and the real Task Scheduler is not asked: this one has no tasks
    patch.start()
    unittest.addModuleCleanup(patch.stop)


class CgTest(unittest.TestCase):
    def setUp(self):
        use_lang(self, 'ru')
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

    def test_every_command_runs_alone(self):
        """Each command starts in a fresh process with only the modules it needs: --help of every one, and `status` on a temporary index."""
        cmds = next(a for a in cg._parser()._actions if isinstance(a, argparse._SubParsersAction)).choices
        for cmd in cmds:
            code, out = self.run_cg(cmd, '--help')
            self.assertEqual((cmd, code), (cmd, 0))
            self.assertIn('help', out)
        self.assertEqual(self.run_cg('status')[0], 0)

    def test_cli_error_is_json(self):
        code, out = self.run_cg('status', db=self.tmp)  # a folder instead of the database file
        self.assertEqual(code, 1)
        self.assertIn('error', out)

    def test_bad_arguments_are_json(self):
        code, out = self.run_cg('recall')
        self.assertEqual(code, 1)
        self.assertIn('error', out)

    def test_help_is_json(self):  # stage 1 nit: it printed usage text the mod could not read
        for args in (['--help'], ['sync', '-h']):
            code, out = self.run_cg(*args)
            self.assertEqual(code, 0)
            self.assertIn('usage: cg.py', out['help'])

    def test_a_session_id_is_no_path(self):  # stage 1 nit: it went into the handoff file name unchecked
        for cmd in ('handoff', 'last-turn'):
            code, out = self.run_cg(cmd, '--session', '..\\..\\x', *(['--today', '2026-10-19'] if cmd == 'handoff' else []))
            self.assertEqual(code, 1)
            self.assertIn('..', out['error'])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'x.md')))

    def test_last_turn_unknown_session(self):
        self.assertEqual(self.run_cg('last-turn', '--session', 'nope'), (0, {'tokens': None, 'at': None, 'model': None}))

    def test_last_turn(self):
        self.write('P/S1.jsonl', [owner_line('вопрос'), claude_line('ответ')])
        self.assertEqual(self.run_cg('last-turn', '--session', 'S1'), (0, {'tokens': 18, 'at': TS, 'model': 'claude-opus-5-5'}))

    def test_index_status_expand(self):
        self.write('P/S1.jsonl', [owner_line('вопрос про ёжика'), claude_line('ответ')])
        code, out = self.run_cg('index')
        self.assertEqual((code, out['files'], out['rows'], out['errors']), (0, 1, 2, []))
        self.assertEqual(self.run_cg('index', '--rebuild')[1]['rows'], 0)  # nothing is missing, nothing is added
        code, out = self.run_cg('status')
        self.assertEqual((code, out['convs'], out['rows'], out['db']), (0, 1, 2, self.db))
        hit = self.run_cg('recall', '--query', 'ёжика', '--all')[1]['hits'][0]
        code, out = self.run_cg('expand', '--conv', hit['conv'], '--pos', str(hit['pos']))
        self.assertEqual((code, [r['kind'] for r in out['rows']]), (0, ['owner', 'claude']))

    def test_status_project(self):  # /al-health shows which project «в этом проекте» means and how many conversations it finds
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

    def test_recall_answers_from_the_index_while_another_run_holds_the_write_lock(self):  # a sync or an embed is writing
        self.write('P/S1.jsonl', [owner_line('ёжик в тумане')])
        self.run_cg('index')
        short = memdb.connect(self.db)
        self.addCleanup(short.close)
        short.execute('PRAGMA busy_timeout = 200')  # connect waits 30 s
        other = memdb.connect(self.db)
        self.addCleanup(other.close)
        other.execute('BEGIN IMMEDIATE')
        a = cg._parser().parse_args(['--db', self.db, '--projects', self.projects, 'recall', '--query', 'ёжик', '--all'])
        with mock.patch.object(memdb, 'connect', lambda path: short):
            out = a.fn(a)
        self.assertIn('ёжик', out['hits'][0]['snippet'])
        self.assertEqual([e['error'] for e in out['errors']], ['OperationalError: database is locked'])

    def test_compression_of_what_the_model_reads(self):  # stage 3
        self.write('P/S1.jsonl', [owner_line('Это очень важный ёжик, и он живёт в `C:\\x\\y.py`.'), claude_line('Ответ: это ёжик.')])
        hit = lambda *c: self.run_cg('recall', '--query', 'важный', '--all', *c)[1]['hits'][0]
        self.assertIn('Это очень', hit()['snippet'])  # off unless asked: a terminal user sees the text as it is
        snippet = hit('--compress', 'ultra')['snippet']
        self.assertNotIn('очень', snippet)
        self.assertIn('`C:\\x\\y.py`', snippet)
        self.assertIn('ёжик', snippet)
        h = hit()
        rows = self.run_cg('expand', '--conv', h['conv'], '--pos', str(h['pos']), '--compress', 'full')[1]['rows']
        self.assertEqual(rows[1]['text'], 'Ответ: ёжик.')
        self.assertEqual(self.run_cg('recall', '--query', 'ёжик', '--compress', 'max')[0], 1)

    def test_a_tool_call_is_a_command_and_never_compressed(self):  # the review of PR 1
        command = 'git add . && git commit -m "fix the bug in Parser"'
        m = {'id': 'm2', 'model': 'claude-opus-5-5', 'role': 'assistant', 'usage': USAGE,
             'content': [{'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {'command': command}}]}
        self.write('P/S1.jsonl', [owner_line('закоммить'), dict(type='assistant', timestamp=TS, cwd='C:\\a', message=m)])
        hit = self.run_cg('recall', '--query', 'Parser', '--all', '--compress', 'ultra')[1]['hits'][0]
        self.assertEqual(hit['kind'], 'tool')
        self.assertIn('git add . && git commit -m "fix the bug in «Parser»"', hit['snippet'])
        rows = self.run_cg('expand', '--conv', hit['conv'], '--pos', str(hit['pos']), '--compress', 'ultra')[1]['rows']
        self.assertIn('Bash: ' + command, [r['text'] for r in rows])

    def aliases(self, value):  # the mod's settings lie beside the index
        os.makedirs(os.path.dirname(self.db), exist_ok=True)
        with open(os.path.join(os.path.dirname(self.db), 'settings.json'), 'w', encoding='utf-8') as f:
            json.dump({'projectAliases': value}, f)

    def test_recall_from_new_folder_sees_old_project_with_alias(self):  # the folder was renamed: its conversations are keyed to the old path
        self.write('A/S1.jsonl', [owner_line('ёжик в старой папке', cwd='C:\\old\\proj')])
        self.write('B/S2.jsonl', [owner_line('ёжик в новой папке', cwd='C:\\new\\proj')])
        self.write('C/S3.jsonl', [owner_line('ёжик в чужой папке', cwd='C:\\other')])
        self.aliases({'C:\\new\\proj': ['C:\\old\\proj']})
        hits = lambda cwd: sorted(h['conv'] for h in self.run_cg('recall', '--query', 'ёжик', '--cwd', cwd)[1]['hits'])
        self.assertEqual(hits('C:\\new\\proj'), ['S1', 'S2'])
        self.assertEqual(hits('C:\\New\\Proj\\.claude\\worktrees\\w1'), ['S1', 'S2'])  # a worktree is the same project
        self.assertEqual(hits('C:\\old\\proj'), ['S1'])  # the alias goes one way: from the new folder to the old

    def test_recall_without_alias_does_not_see_old_project(self):
        self.write('A/S1.jsonl', [owner_line('ёжик в старой папке', cwd='C:\\old\\proj')])
        self.assertEqual(self.run_cg('recall', '--query', 'ёжик', '--cwd', 'C:\\new\\proj')[1]['hits'], [])
        self.aliases({'C:\\elsewhere': ['C:\\old\\proj']})  # an alias of another folder is no alias of this one
        self.assertEqual(self.run_cg('recall', '--query', 'ёжик', '--cwd', 'C:\\new\\proj')[1]['hits'], [])

    def test_lessons_listing_and_match_see_alias(self):
        conn = memdb.connect(self.db)
        lessons.save(conn, project_key('C:\\old\\proj'), {'action': 'запустил сборку', 'symptom': 'HTTP 429 от GitHub', 'cause': 'частые вызовы API'})
        conn.close()
        listed = lambda: [l['text'] for l in self.run_cg('lessons', '--cwd', 'C:\\new\\proj')[1]['lessons']]
        # lessons-match asks no model when the project has no lessons ('ok'); with one it goes on and says there are no vectors of this model
        matched = lambda: self.run_cg('lessons-match', '--cwd', 'C:\\new\\proj', '--text', '429', '--model', 'nosuch')[1]['vector']
        self.assertEqual((listed(), matched()), ([], 'ok'))
        self.aliases({'C:\\new\\proj': ['C:\\old\\proj']})
        self.assertIn('Action: запустил сборку', listed()[0])
        self.assertIn('nosuch', matched())

    def test_lesson_saved_under_new_key(self):  # a write keeps one key: the folder's own
        self.aliases({'C:\\new\\proj': ['C:\\old\\proj']})
        out = self.run_cg('lesson-save', '--cwd', 'C:\\new\\proj', '--action', 'a', '--symptom', 's', '--cause', 'c', '--model', 'nosuch')[1]
        conn = memdb.connect(self.db)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute('SELECT project FROM conv_projects WHERE conv = ?', (out['conv'],)).fetchall(), [(project_key('C:\\new\\proj'),)])

    def test_malformed_aliases_are_ignored(self):
        self.write('A/S1.jsonl', [owner_line('ёжик в старой папке', cwd='C:\\old\\proj')])
        for bad in (['x'], {'C:\\new': 'C:\\old'}, {'C:\\new\\proj': ['C:\\old\\proj', 5]}, {'C:\\new\\proj': ['C:\\old\\proj'], 'C:\\x': 'C:\\y'}, 'C:\\old\\proj', None):
            self.aliases(bad)
            code, out = self.run_cg('recall', '--query', 'ёжик', '--cwd', 'C:\\new\\proj')
            self.assertEqual((code, out['hits']), (0, []), bad)  # the good pair beside the bad item goes too: the value is taken whole or not at all
        self.write('B/S2.jsonl', [owner_line('ёжик в новой папке', cwd='C:\\new\\proj')])
        with open(os.path.join(os.path.dirname(self.db), 'settings.json'), 'w', encoding='utf-8') as f:
            f.write('[1]')  # not an object at all
        code, out = self.run_cg('recall', '--query', 'ёжик', '--cwd', 'C:\\new\\proj')
        self.assertEqual((code, [h['conv'] for h in out['hits']]), (0, ['S2']))  # the search still answers, from the project's own memory

    def test_blank_aliases_are_skipped(self):  # project_key('') is the folder the process runs in: a blank path would widen the scope to it
        here = os.getcwd()
        self.write('A/S1.jsonl', [owner_line('ёжик там, где запущен процесс', cwd=here)])
        self.write('B/S2.jsonl', [owner_line('ёжик в старой папке', cwd='C:\\old\\proj')])
        self.aliases({'C:\\new\\proj': ['', '  ', 'C:\\old\\proj'], '': ['C:\\old\\proj'], ' ': ['C:\\x']})
        hits = lambda cwd: sorted(h['conv'] for h in self.run_cg('recall', '--query', 'ёжик', '--cwd', cwd)[1]['hits'])
        self.assertEqual(hits('C:\\new\\proj'), ['S2'])  # the good entry stays, the blank ones add nothing
        self.assertEqual(hits(here), ['S1'])  # the blank key does not make the process folder a new name for the old project

    def test_status_project_counts_alias(self):
        self.write('A/S1.jsonl', [owner_line('там', cwd='C:\\old\\proj')])
        self.write('A/S1/subagents/agent-a1.jsonl', [owner_line('суб', cwd='')])
        self.write('B/S2.jsonl', [owner_line('здесь', cwd='C:\\new\\proj')])
        self.run_cg('index')
        status = lambda: self.run_cg('status', '--cwd', 'C:\\new\\proj')[1]['project']
        self.assertEqual(status(), {'key': project_key('C:\\new\\proj'), 'convs': 1})
        self.aliases({'C:\\new\\proj': ['C:\\old\\proj']})
        self.assertEqual(status(), {'key': project_key('C:\\new\\proj'), 'convs': 3})

    def test_recall_takes_other_wordings_and_reads_an_error_as_it_is(self):  # stage 4
        err = {'type': 'tool_result', 'tool_use_id': 't1', 'content': 'HTTP 429: the limit of 120 requests per hour', 'is_error': True}
        self.write('P/S1.jsonl', [owner_line('запусти ещё раз'), dict(type='user', timestamp=TS, cwd='C:\\a', message={'role': 'user', 'content': [err]})])
        out = self.run_cg('recall', '--query=повторный запуск', '--also=429 limit', '--all', '--compress', 'ultra')[1]
        self.assertEqual(out['hits'][0]['kind'], 'error')
        self.assertIn('HTTP «429»: the «limit» of 120 requests per hour', out['hits'][0]['snippet'])  # an error is not compressed

    def test_a_lesson_is_read_as_it_is(self):  # stage 4: its wording is the lesson
        conn = memdb.connect(self.db)
        lessons.save(conn, project_key('C:\\a'), {'action': 'запустил сборку', 'symptom': 'HTTP 429 от GitHub', 'cause': 'это очень частые вызовы API'})
        conn.close()
        hit = self.run_cg('recall', '--query', '429', '--all', '--compress', 'ultra')[1]['hits'][0]
        self.assertEqual(hit['kind'], 'lesson')
        self.assertIn('это очень частые вызовы API', hit['snippet'])

    def test_compress_previews_a_level(self):
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONIOENCODING', 'PYTHONUTF8')}
        env.update(USERPROFILE=self.tmp, HOME=self.tmp)  # no --db: cg.py looks for the index in the home, and the test must not touch the real one
        p = subprocess.run([sys.executable, CG, 'compress', '--level', 'ultra'], input='Это очень просто, правда.'.encode('utf-8'), capture_output=True, env=env)
        self.assertEqual(json.loads(p.stdout.decode('utf-8')), {'text': 'просто правда', 'chars_in': 25, 'chars_out': 13})
        path = os.path.join(self.tmp, 'note.txt')
        with open(path, 'w', encoding='utf-8-sig') as f:  # as Notepad saves it, with a BOM
            f.write('Это очень просто, правда.')
        self.assertEqual(self.run_cg('compress', '--level', 'full', '--file', path)[1]['text'], 'просто, правда.')  # «просто» is a word here (simple), not a filler

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

    def test_a_second_embed_is_busy_at_once_and_never_asks_ollama(self):  # two chats would embed the same records through the one Ollama
        self.write('P/S1.jsonl', [owner_line('вопрос'), claude_line('ответ')])
        self.run_cg('index')
        self.addCleanup(gc.collect)  # as above: the temp folder cleanup needs the files released
        other = sqlite3.connect(self.db + '.embed.lock', isolation_level=None)  # what the first embed holds while it runs
        self.addCleanup(other.close)
        other.execute('BEGIN EXCLUSIVE')
        a = cg._parser().parse_args(['--db', self.db, '--projects', self.projects, 'embed'])
        with mock.patch.object(vectors, 'ollama_embed') as ollama:
            out = a.fn(a)
        self.assertEqual((out['done'], out.get('busy'), out['errors']), (0, True, []))
        ollama.assert_not_called()
        other.close()  # the first embed is over: the next one runs
        with mock.patch.object(vectors, 'ollama_embed', lambda texts, model: [[1.0] * 768 for _ in texts]), contextlib.redirect_stderr(io.StringIO()):
            out = a.fn(a)
        self.assertEqual((out['done'], 'busy' in out), (2, False))

    def test_a_second_sync_waits_for_the_first_and_then_runs(self):
        share = os.path.join(self.tmp, 'share')
        os.makedirs(share)
        os.makedirs(os.path.dirname(self.db))
        self.addCleanup(gc.collect)  # as above: the temp folder cleanup needs the files released
        first = sqlite3.connect(self.db + '.sync.lock', isolation_level=None, check_same_thread=False)  # what the first sync holds while it runs
        first.execute('BEGIN EXCLUSIVE')
        ends = threading.Timer(0.6, first.close)
        self.addCleanup(ends.cancel)
        self.addCleanup(first.close)
        a = cg._parser().parse_args(['--db', self.db, '--projects', self.projects, 'sync', '--sync-dir', share, '--machine', 'M', '--max-seconds', '10'])
        t0 = time.monotonic()  # in this process: a child's start-up could take longer than the lock is held
        ends.start()
        out = a.fn(a)
        self.assertGreaterEqual(time.monotonic() - t0, 0.5)
        self.assertEqual(out['errors'], [])

    # stage 5: encryption

    def main_json(self, *args):
        return self.capture(['--db', self.db, '--projects', self.projects, *args])

    def capture(self, argv):
        """cg.main in this process, where a window can be faked: (exit code, what it printed as JSON, everything it wrote: stdout and stderr)."""
        out, err = (io.TextIOWrapper(io.BytesIO(), encoding='utf-8') for _ in range(2))
        with mock.patch.object(sys, 'stdout', out), mock.patch.object(sys, 'stderr', err):
            code = cg.main(argv)
        out.flush()
        err.flush()
        text = out.buffer.getvalue().decode('utf-8')
        return code, json.loads(text), text + err.buffer.getvalue().decode('utf-8')

    def share_dir(self, name='share'):
        path = os.path.join(self.tmp, name)
        os.makedirs(path, exist_ok=True)
        return path

    def settings(self, **fields):
        """The mod's settings file beside the index."""
        os.makedirs(os.path.dirname(self.db), exist_ok=True)
        with open(crypto.settings_path(self.db), 'w', encoding='utf-8') as f:
            json.dump(fields, f)

    def encrypted_index(self, **fields):
        """An encrypted index of one conversation, its key and these settings in the file beside it; returns the key."""
        self.write('P/S1.jsonl', [owner_line('вопрос про ёжика'), claude_line('ответ')])
        self.settings(**fields)
        key = secrets.token_bytes(32)
        crypto.save_key(crypto.settings_path(self.db), key)
        conn = memdb.connect(self.db)  # no file and a key: the index is born encrypted
        memdb.update(conn, self.projects)
        conn.close()
        self.assertTrue(crypto.is_encrypted(self.db))
        return key

    def maintain(self, battery=True):
        self.addCleanup(gc.collect)  # maintain never closes its connection; the temp folder cleanup needs the file released
        a = cg._parser().parse_args(['--db', self.db, '--projects', self.projects, 'maintain'])
        with mock.patch.dict(os.environ, {'COMPUTERNAME': 'M'}):
            return cg.maintain(a, battery=lambda: battery, digest=lambda m: None)

    def test_encrypt_status_plain(self):
        self.write('P/S1.jsonl', [owner_line('вопрос'), claude_line('ответ')])
        self.run_cg('index')
        self.assertEqual(self.run_cg('encrypt', 'status'), (0, {'on': False, 'db': 'plain', 'key': 'missing', 'notes': []}))
        self.assertFalse(os.path.exists(self.db + '.maintain.lock'))  # a question takes no lock

    def test_status_has_encryption(self):
        self.assertFalse(self.run_cg('status')[1]['encryption']['on'])

    @unittest.skipUnless(os.name == 'nt', 'DPAPI is Windows only')
    def test_encrypt_on_prints_no_code(self):
        self.write('P/S1.jsonl', [owner_line('вопрос про ёжика'), claude_line('ответ')])
        self.run_cg('index')
        share, ui = self.share_dir(), FakeUI()
        no_memory = os.path.join(self.tmp, 'no-memory')  # a folder that is not there is no memory to sync, not a failed sync
        with mock.patch('dialogs.WindowsUI', lambda: ui), mock.patch('urllib.request.build_opener', side_effect=OSError):  # no Ollama to ask
            code, out, text = self.main_json('encrypt', 'on', '--sync-dir', share, '--machine', 'M', '--memory-dir', no_memory)
        self.assertEqual((code, out['done'], out['notes']), (0, True, []))
        self.assertTrue(crypto.is_encrypted(self.db))
        self.assertEqual(len(ui.shown), 1)
        for secret in (ui.shown[0], 'CG1-', crypto.load_key(crypto.settings_path(self.db)).hex()):
            self.assertNotIn(secret, text)
        self.assertEqual(sorted(os.listdir(os.path.join(share, 'M', 'gen-0001'))), ['batch-00000001.sqlcipher', 'generation.json'])

    @unittest.skipUnless(os.name == 'nt', 'DPAPI is Windows only')
    def test_status_shows_an_unfinished_share(self):  # the one reminder after a run that left the old data in the folder, or did not finish
        self.write('P/S1.jsonl', [owner_line('вопрос про ёжика'), claude_line('ответ')])
        self.run_cg('index')
        share = self.share_dir()
        on = ('--lang', 'ru', 'encrypt', 'on', '--sync-dir', share, '--machine', 'M', '--memory-dir', os.path.join(self.tmp, 'no-memory'))
        notes = lambda: self.main_json('--lang', 'ru', 'status')[1]['encryption']['notes']
        self.addCleanup(gc.collect)
        with mock.patch('urllib.request.build_opener', side_effect=OSError):  # no Ollama to ask
            self.main_json('--lang', 'ru', 'sync', '--sync-dir', share, '--machine', 'M')  # an open history in the folder, for `on` to leave
            with mock.patch('dialogs.WindowsUI', lambda: FakeUI([True, True, False])):  # code saved, the open copy goes, the old data stays
                self.assertEqual(self.main_json(*on)[1]['notes'], [t('note_old_share_kept')])
            self.assertEqual(notes(), [t('note_old_share_kept')])
            conn = memdb.connect(self.db)
            conn.execute("INSERT OR REPLACE INTO meta VALUES('share_unfinished', 'pending')")  # what a run cut short leaves
            conn.close()
            self.assertEqual(notes(), [t('note_share_pending')])
            with mock.patch('dialogs.WindowsUI', lambda: FakeUI()):  # the repeat deletes the old data
                self.assertEqual(self.main_json(*on)[1]['done'], True)
            self.assertEqual(notes(), [])

    @unittest.skipUnless(os.name == 'nt', 'DPAPI is Windows only')
    def test_what_the_job_seals_with_after_encrypt_is_the_cipher_encrypt_was_given(self):
        self.write('P/S1.jsonl', [owner_line('вопрос про ёжика'), claude_line('ответ')])
        self.run_cg('index')
        share = self.share_dir()
        with mock.patch('dialogs.WindowsUI', lambda: FakeUI()), mock.patch('urllib.request.build_opener', side_effect=OSError):
            code, out, _ = self.main_json('encrypt', 'on', '--sync-dir', share, '--machine', 'M', '--memory-dir', os.path.join(self.tmp, 'no-memory'),
                                          '--share-cipher', 'cryptography')
        self.assertEqual((code, out['done']), (0, True))
        gen = os.path.join(share, 'M', 'gen-0001')
        self.assertEqual(sorted(os.listdir(gen)), ['batch-00000001.aesgcm', 'generation.json'])
        path = crypto.settings_path(self.db)  # the job knows the folder from the file, and the cipher: `encrypt` wrote it beside the key
        with open(path, encoding='utf-8') as f:
            s = json.load(f)
        self.assertEqual(s['shareCipher'], 'cryptography')
        s['syncDir'] = share
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(s, f)
        self.write('P/S2.jsonl', [owner_line('второй вопрос'), claude_line('второй ответ')])
        out = self.maintain()  # no flag
        self.assertEqual((out['errors'], out['sync']['errors']), ([], 0))
        self.assertEqual(sorted(os.listdir(os.path.join(share, 'M'))), ['gen-0001'])  # the same generation: no new one, no re-send
        self.assertEqual(sorted(os.listdir(gen)), ['batch-00000001.aesgcm', 'batch-00000002.aesgcm', 'generation.json'])

    def test_encrypt_busy(self):
        os.makedirs(os.path.dirname(self.db))
        self.addCleanup(gc.collect)  # the lock that did not open is released by collecting it; the temp folder cleanup needs the file
        other = sqlite3.connect(self.db + '.maintain.lock', isolation_level=None)  # what the background job holds while it runs
        self.addCleanup(other.close)
        other.execute('BEGIN EXCLUSIVE')
        with mock.patch.object(cg, 'ENCRYPT_WAITS', {'maintain': 0, 'embed': 0, 'sync': 0}):
            code, out, _ = self.main_json('--lang', 'ru', 'encrypt', 'on')
        self.assertEqual(code, 1)
        self.assertIn(t('encrypt_busy'), out['error'])

    def test_encrypt_runs_alone_and_holds_no_connection_to_the_index(self):
        self.write('P/S1.jsonl', [owner_line('вопрос'), claude_line('ответ')])
        self.run_cg('index')
        self.addCleanup(gc.collect)  # the locks that did not open stay in the tracebacks of the errors until collected
        ran = []

        def fake_on(db, *args, **kw):
            os.replace(db, db + '.moved')  # Windows will not move a file that a connection of this process holds open
            os.replace(db + '.moved', db)
            for name in ('maintain', 'embed', 'sync'):  # the three locks of the background job, held while the command runs
                with self.assertRaises(sqlite3.OperationalError):
                    memdb.lock(db, name, 0)
            ran.append(db)
            return {'action': 'on', 'done': True, 'notes': []}

        with mock.patch.object(encrypt, 'on', fake_on):
            self.assertEqual(self.main_json('encrypt', 'on')[1]['done'], True)
        self.assertEqual(ran, [self.db])
        for name in ('maintain', 'embed', 'sync'):  # and let go afterwards
            memdb.lock(self.db, name, 0).close()

    def test_a_busy_index_is_encrypt_busy_and_any_other_database_error_is_not(self):
        import sqlcipher3
        for exc in (sqlite3.OperationalError, sqlcipher3.OperationalError):
            with mock.patch.object(encrypt, 'on', side_effect=exc('database is locked')):
                code, out, _ = self.main_json('--lang', 'ru', 'encrypt', 'on')
            self.assertEqual((code, out['error']), (1, 'ValueError: ' + t('encrypt_busy')), exc)
        with mock.patch.object(encrypt, 'on', side_effect=sqlite3.OperationalError('unable to open database file')):
            code, out, _ = self.main_json('encrypt', 'on')
        self.assertEqual((code, out['error']), (1, 'OperationalError: unable to open database file'))

    def test_encrypt_takes_the_folder_and_the_cipher_from_the_settings(self):
        share = self.share_dir()
        self.settings(syncDir='"%s"' % share, shareCipher='cryptography')
        calls = []
        with mock.patch.object(encrypt, 'on', lambda *a, **kw: calls.append((a, kw)) or {'done': True}):
            self.main_json('encrypt', 'on')
            self.main_json('encrypt', 'on', '--sync-dir', '', '--share-cipher', 'sqlcipher')  # given on the line: the line wins
            code, out, _ = self.main_json('encrypt', 'on', '--share-cipher', 'rot13')
        self.assertEqual([(a[1], a[2]) for a, _ in calls], [(share, 'cryptography'), ('', 'sqlcipher')])
        self.assertEqual([kw['seconds'] for _, kw in calls], [120, 120])  # the push is short: the mod gives the whole command 600 s at most
        self.assertLess(sum(cg.ENCRYPT_WAITS.values()) + cg.ENCRYPT_PUSH_SECONDS, 400)  # and the owner is left the rest, 200 s and more, in the windows
        self.assertEqual(code, 1)
        self.settings(shareCipher='rot13')
        with mock.patch.object(encrypt, 'on', side_effect=AssertionError):
            code, out, _ = self.main_json('encrypt', 'on')
        self.assertEqual(code, 1)
        self.assertIn('shareCipher', out['error'])  # the setting is named, no KeyError of the cipher table

    def test_encrypt_takes_the_index_cipher_from_the_line_or_the_settings(self):
        calls = []
        with mock.patch.object(encrypt, 'on', lambda *a, **kw: calls.append(kw['index_cipher']) or {'done': True}):
            self.main_json('encrypt', 'on')  # nothing set: sqlcipher
            self.main_json('encrypt', 'on', '--index-cipher', 'efs')
            self.settings(indexCipher='efs')
            self.main_json('encrypt', 'on')  # the setting
            self.main_json('encrypt', 'on', '--index-cipher', 'sqlcipher')  # given on the line: the line wins
            code, _, _ = self.main_json('encrypt', 'on', '--index-cipher', 'rot13')
        self.assertEqual((calls, code), (['sqlcipher', 'efs', 'efs', 'sqlcipher'], 1))  # the flag knows two ciphers only
        self.settings(indexCipher='rot13')
        with mock.patch.object(encrypt, 'on', side_effect=AssertionError):
            code, out, _ = self.main_json('encrypt', 'on')
        self.assertEqual((code, out['error']), (1, 'ValueError: ' + t('index_cipher_bad', value='rot13')))  # the setting is named, no KeyError

    @unittest.skipUnless(os.name == 'nt', 'DPAPI is Windows only')
    def test_maintain_encrypted(self):
        share = self.share_dir()
        self.encrypted_index(syncDir=share)
        out = self.maintain()
        self.assertEqual((out['errors'], out['sync']['errors']), ([], 0))
        self.assertEqual(sorted(os.listdir(os.path.join(share, 'M', 'gen-0001'))), ['batch-00000001.sqlcipher', 'generation.json'])

    @unittest.skipUnless(os.name == 'nt', 'DPAPI is Windows only')
    def test_maintain_without_the_key_says_so_instead_of_dying(self):
        self.encrypted_index(syncDir=self.share_dir())
        crypto.save_key(crypto.settings_path(self.db), None)
        out = self.maintain()
        self.assertEqual(list(out), ['errors'])  # the index cannot be opened to keep the run in: /al-health shows the same words from `status`
        self.assertIn(t('key_missing'), out['errors'][0]['error'])
        code, status = self.run_cg('--lang', 'ru', 'status')
        self.assertEqual(code, 1)
        self.assertIn(t('key_missing'), status['error'])

    def test_maintain_survives_a_key_that_cannot_be_read_and_a_cipher_that_is_unknown(self):
        self.write('P/S1.jsonl', [owner_line('вопрос'), claude_line('ответ')])
        memdb.connect(self.db).close()  # a plain index, which opens whatever the settings file says of a key
        share = self.share_dir()
        self.settings(syncDir=share, encryptionKey='junk')
        out = self.maintain()
        self.assertEqual([e['where'] for e in out['errors']], ['sync'])
        self.assertIn(t('key_unreadable'), out['errors'][0]['error'])
        self.assertIsNotNone(out['index'])  # the step before it ran
        self.settings(syncDir=share, shareCipher='rot13')
        out = self.maintain()
        self.assertEqual([e['where'] for e in out['errors']], ['sync'])
        self.assertIn('shareCipher', out['errors'][0]['error'])

    def test_a_settings_file_that_cannot_be_opened_stops_neither_status_nor_sync_of_a_plain_index(self):  # encryption off: all as before stage 5
        self.write('P/S1.jsonl', [owner_line('вопрос'), claude_line('ответ')])
        self.run_cg('index')
        os.mkdir(crypto.settings_path(self.db))  # a folder cannot be opened: the OSError of every system, as an editor's lock or a refused right
        share = self.share_dir()
        code, out = self.run_cg('status')
        self.assertEqual((code, out['encryption']['key'], out['encryption']['db']), (0, 'unreadable', 'plain'))
        code, out = self.run_cg('sync', '--sync-dir', share, '--machine', 'M')
        self.assertEqual((code, out['errors']), (0, []))

    @unittest.skipUnless(os.name == 'nt', 'DPAPI is Windows only')
    def test_a_settings_file_that_cannot_be_opened_does_not_open_an_encrypted_index(self):
        self.encrypted_index()
        os.remove(crypto.settings_path(self.db))
        os.mkdir(crypto.settings_path(self.db))
        self.assertEqual(self.run_cg('status')[0], 1)

    @unittest.skipUnless(os.name == 'nt', 'DPAPI is Windows only')
    def test_a_settings_file_lost_after_the_index_opened_makes_no_open_sync_of_an_encrypted_index(self):
        share = self.share_dir()
        self.encrypted_index(syncDir=share)
        self.addCleanup(gc.collect)
        for fail in (lambda: PermissionError(13, 'Permission denied'), lambda: crypto.SettingsBroken('broken')):  # made anew: an error keeps the frames, and so the locks, of its run
            real, calls = crypto.load_key, []

            def load_key(path):  # the first read is the open of the index; the file is gone for the read of the sync
                calls.append(path)
                if len(calls) > 1:
                    raise fail()
                return real(path)

            with mock.patch.object(crypto, 'load_key', load_key):
                self.assertEqual(self.main_json('sync', '--sync-dir', share, '--machine', 'M')[0], 1)  # said, not swallowed
            self.assertEqual(len(calls), 2)
            del calls[:]
            with mock.patch.object(crypto, 'load_key', load_key):
                out = self.maintain()
            self.assertEqual([e['where'] for e in out['errors']], ['sync'])
            self.assertEqual([f for _, _, files in os.walk(share) for f in files], [])  # no open batch, no label: nothing went out

    def test_a_broken_settings_file_stops_no_sync_of_a_plain_index(self):
        memdb.connect(self.db).close()
        with open(crypto.settings_path(self.db), 'w') as f:
            f.write('{ "syncDir": ')
        share = self.share_dir()
        self.addCleanup(gc.collect)
        a = cg._parser().parse_args(['--db', self.db, '--projects', self.projects, 'sync', '--sync-dir', share, '--machine', 'M'])
        self.assertEqual(a.fn(a)['errors'], [])

    @unittest.skipUnless(os.name == 'nt', 'DPAPI is Windows only')
    def test_the_cipher_is_the_flags_or_the_settings(self):
        share, other = self.share_dir(), self.share_dir('other')
        self.encrypted_index(syncDir=share, shareCipher='cryptography')
        self.maintain()
        self.assertEqual(sorted(os.listdir(os.path.join(share, 'M', 'gen-0001'))), ['batch-00000001.aesgcm', 'generation.json'])
        self.addCleanup(gc.collect)
        a = cg._parser().parse_args(['--db', self.db, '--projects', self.projects, 'sync', '--sync-dir', other, '--machine', 'M', '--share-cipher', 'sqlcipher'])
        self.assertEqual(a.fn(a)['errors'], [])  # the flag wins over the file
        self.assertEqual(sorted(os.listdir(os.path.join(other, 'M', 'gen-0001'))), ['batch-00000001.sqlcipher', 'generation.json'])
        self.assertEqual(self.run_cg('sync', '--sync-dir', other, '--share-cipher', 'rot13')[0], 1)  # the flag knows two ciphers only

    # the index folder: where cg.py finds the index when no --db is given

    def home_dir(self, **settings):
        """A stand-in for ~/.claude/alethite with these settings; the real one is never read."""
        home = os.path.join(self.tmp, 'home')
        os.makedirs(home, exist_ok=True)
        with open(os.path.join(home, 'settings.json'), 'w', encoding='utf-8') as f:
            json.dump(settings, f)
        self.patch_home(home, os.path.join(self.tmp, 'old-home'))
        return home

    def patch_home(self, home, old):
        for patch in (mock.patch.object(crypto, 'HOME_DIR', home), mock.patch.object(crypto, 'OLD_HOME_DIR', old), mock.patch.object(crypto, 'INDEX_DIRS', set())):  # main registers the folder in it
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(gc.collect)  # cg never closes its connection; the temp folder cleanup needs the file released

    def main_home(self, *args):
        return self.capture(['--lang', 'ru', '--projects', self.projects, *args])

    def old_home(self):
        """A stand-in for ~/.claude/cacheguard with a settings file, and for ~/.claude/alethite that is not there yet: (old, new)."""
        old, new = (os.path.join(self.tmp, n) for n in ('old', 'new'))
        os.makedirs(old)
        with open(os.path.join(old, 'settings.json'), 'w', encoding='utf-8') as f:
            f.write('{}')
        self.patch_home(new, old)
        return old, new

    def test_main_with_db_never_moves(self):
        old, new = self.old_home()
        s = self.scheduler({'CacheGuard': 30})
        for cmd in (('status',), ('last-turn', '--session', 'S1'), ('lessons', '--all')):
            code, out, _ = self.main_json(*cmd)
            self.assertEqual(code, 0, out)
            self.assertNotIn('home_notes', out)
        self.assertEqual((os.path.isfile(os.path.join(old, 'settings.json')), os.path.exists(new)), (True, False))
        self.assertEqual((s.calls, list(s.tasks)), ([], ['CacheGuard']))  # and the old task is not asked about, let alone taken off

    def test_main_without_db_moves_the_old_folder_first(self):
        old, new = self.old_home()
        code, out, _ = self.main_home('status')
        self.assertEqual((code, out['db'], os.path.exists(old), os.path.isfile(os.path.join(new, 'settings.json'))), (0, os.path.join(new, 'index.db'), False, True))
        self.assertNotIn('home_notes', out)

    def test_main_reports_home_notes(self):
        old, new = self.old_home()
        busy = PermissionError(13, 'in use', None, 32)
        # an index answer, the short ones (the mod kills them after 8 s: one try, no wait) and an error answer
        for cmd, tries in ((('status',), 100), (('last-turn', '--session', 'S1'), 1), (('lessons-match', '--text', '429', '--model', 'nosuch'), 1),
                           (('last-turn', '--session', '..\\x'), 1)):
            with mock.patch.object(crypto, 'HOME_DIR', new), mock.patch('os.rename', side_effect=busy) as rename, mock.patch('time.sleep') as sleep:  # one process per run, as in life
                code, out, _ = self.main_home(*cmd)
            self.assertEqual(out['home_notes'], [t('home_move_failed', old=old, new=new, e=busy)], cmd)
            self.assertEqual(code, int('error' in out), cmd)
            self.assertEqual((rename.call_count, sleep.call_count), (tries, tries - 1), cmd)
        self.assertEqual(out['error'], 'ValueError: ' + t('bad_session', id='..\\x'))

    # the background job follows the rename: the old task goes with the old folder, the new one comes back with its interval

    def scheduler(self, tasks=None, deny=(), running=()):
        """The Task Scheduler of this test, with these tasks: cg.main asks it about the old task and about the new one."""
        s = Scheduler(tasks, deny, running)
        patch = s.patched()
        patch.start()
        self.addCleanup(patch.stop)
        return s

    def test_move_removes_old_task_and_installs_new_with_its_interval(self):
        old, new = self.old_home()
        s = self.scheduler({'CacheGuard': 45})
        code, out, _ = self.main_home('status')
        self.assertEqual(code, 0, out)
        self.assertEqual([(c[0], c[2]) for c in s.calls], [('/Query', 'CacheGuard'), ('/End', 'CacheGuard'), ('/Delete', 'CacheGuard'), ('/Query', 'Alethite'), ('/Create', 'Alethite')])
        self.assertEqual(list(s.tasks), ['Alethite'])
        self.assertIn('<Interval>PT45M</Interval>', s.tasks['Alethite'])
        self.assertTrue(os.path.isfile(os.path.join(new, 'job.xml')))  # beside the index
        self.assertNotIn('home_notes', out)  # /End found nothing running and said so: no note for that

    def test_old_task_goes_before_the_folder_moves_and_the_new_one_comes_after_the_index_is_found(self):
        old, new = self.old_home()
        far = os.path.join(self.tmp, 'far')  # the index lives elsewhere: the task file lies beside it, not in the mod folder
        with open(os.path.join(old, 'settings.json'), 'w', encoding='utf-8') as f:
            json.dump({'indexDir': far}, f)
        memdb.connect(os.path.join(far, 'index.db')).close()
        s = self.scheduler({'CacheGuard': 45})
        seen = []
        s.probe = lambda args: seen.append((args[0], args[2], os.path.isdir(old)))  # was the old folder still there when the scheduler was asked?
        code, out, _ = self.main_home('status')
        self.assertEqual((code, out['db']), (0, os.path.join(far, 'index.db')), out)
        self.assertEqual(seen, [('/Query', 'CacheGuard', True), ('/End', 'CacheGuard', True), ('/Delete', 'CacheGuard', True), ('/Query', 'Alethite', False),
                                ('/Create', 'Alethite', False)])
        self.assertEqual((os.path.isfile(os.path.join(far, 'job.xml')), os.path.exists(os.path.join(new, 'job.xml'))), (True, False))

    def test_a_new_task_that_is_there_keeps_its_interval(self):
        old, new = self.old_home()
        s = self.scheduler({'CacheGuard': 45, 'Alethite': 20})
        code, out, _ = self.main_home('status')
        self.assertEqual((code, 'home_notes' in out, list(s.tasks)), (0, False, ['Alethite']), out)
        self.assertIn('<Interval>PT20M</Interval>', s.tasks['Alethite'])
        self.assertNotIn('/Create', [c[0] for c in s.calls])

    def test_running_old_task_is_ended_before_it_is_deleted(self):  # its instance would hold the old index open and the folder could not move
        old, new = self.old_home()
        s = self.scheduler({'CacheGuard': 45}, running=('CacheGuard',))
        code, out, _ = self.main_home('status')
        self.assertEqual((code, 'home_notes' in out, os.path.exists(old), os.path.isdir(new), s.running), (0, False, False, True, set()), out)
        self.assertEqual([c[0] for c in s.calls if c[2] == 'CacheGuard'], ['/Query', '/End', '/Delete'])

    def test_old_task_removed_when_both_folders_exist(self):  # the old code would make the old folder again
        old, new = self.old_home()
        os.makedirs(new)
        s = self.scheduler({'CacheGuard': 30})
        code, out, _ = self.main_home('status')
        self.assertEqual((code, list(s.tasks), os.path.isdir(old)), (0, ['Alethite'], True))
        self.assertEqual(out['home_notes'], [t('home_move_both', old=old, new=new)])

    def test_no_old_folder_no_schtasks_call(self):
        self.home_dir()  # no old folder
        s = self.scheduler({'CacheGuard': 30})
        for cmd in (('status',), ('last-turn', '--session', 'S1')):
            self.assertEqual(self.main_home(*cmd)[0], 0, cmd)
        self.assertEqual((s.calls, list(s.tasks)), ([], ['CacheGuard']))

    def test_follow_job_flag_only(self):
        home = self.home_dir()
        s = self.scheduler({'Alethite': (45, 'C:\\clone\\plugin\\scripts\\cg.py')})
        self.assertEqual(self.main_home('status')[0], 0)
        self.assertEqual(s.calls, [])  # a run without the flag leaves the task alone
        code, out, _ = self.main_home('--follow-job', 'status')  # before the command, like --lang
        self.assertEqual((code, 'home_notes' in out), (0, False), out)
        self.assertEqual([c[0] for c in s.calls], ['/Query', '/Query', '/Query', '/Create'])  # the old task, where it points, its interval, the new task
        self.assertEqual(job.target(s, lambda: True), job.CG)
        self.assertIn('<Interval>PT45M</Interval>', s.tasks['Alethite'])
        self.assertTrue(os.path.isfile(os.path.join(home, 'job.xml')))

    def test_follow_job_takes_off_an_old_task_that_outlived_the_move(self):  # removal was refused, or an old chat made it again: no old folder to show it
        home = self.home_dir()
        s = self.scheduler({'CacheGuard': 45})
        code, out, _ = self.main_home('--follow-job', 'status')
        self.assertEqual((code, 'home_notes' in out, list(s.tasks)), (0, False, ['Alethite']), out)
        self.assertEqual([(c[0], c[2]) for c in s.calls], [('/Query', 'CacheGuard'), ('/End', 'CacheGuard'), ('/Delete', 'CacheGuard'), ('/Query', 'Alethite'),
                                                           ('/Create', 'Alethite'), ('/Query', 'Alethite')])  # the last: follow finds the task already pointing here
        self.assertIn('<Interval>PT45M</Interval>', s.tasks['Alethite'])
        self.assertTrue(os.path.isfile(os.path.join(home, 'job.xml')))

    def job_error(self, deny, moved, key, waits=False):
        old, new = self.old_home()
        s = self.scheduler({'CacheGuard': 45}, deny)
        code, out, _ = self.main_home('status')
        self.assertEqual((code, 'error' in out, os.path.exists(old), os.path.isdir(new)), (0, False, not moved, moved), out)  # the index answered, from the folder that holds it
        self.assertEqual(out['db'], os.path.join(new if moved else old, 'index.db'))
        self.assertEqual(out['home_notes'], [t(key, e=t('job_schtasks', e='ERROR: Access is denied.')), *([t('home_move_waits', old=old, new=new)] if waits else [])])
        return s

    def test_job_error_during_move_does_not_stop_command(self):
        # the old task could not be removed: it stays, the note names it, and the folder stays too, so that it and the mod share one index
        self.assertEqual(list(self.job_error('/Delete', False, 'home_old_task_failed', waits=True).tasks), ['CacheGuard'])  # waits: only here does a folder wait for the task

    def test_the_next_run_retries_the_old_task_and_then_moves(self):
        old, new = self.old_home()
        s = self.scheduler({'CacheGuard': 45}, '/Delete')
        self.assertEqual(self.main_home('status')[1]['home_notes'], [t('home_old_task_failed', e=t('job_schtasks', e='ERROR: Access is denied.')), t('home_move_waits', old=old, new=new)])
        s.deny = ()
        with mock.patch.object(crypto, 'HOME_DIR', new):  # one process per run, as in life
            code, out, _ = self.main_home('status')
        self.assertEqual((code, 'home_notes' in out, os.path.exists(old), os.path.isdir(new), list(s.tasks)), (0, False, False, True, ['Alethite']), out)
        self.assertIn('<Interval>PT45M</Interval>', s.tasks['Alethite'])

    def test_a_refused_removal_leaves_a_new_folder_that_is_there_alone(self):  # an old chat made the old folder again: the new one is the real one
        old, new = self.old_home()
        os.makedirs(new)
        self.scheduler({'CacheGuard': 45}, '/Delete')
        code, out, _ = self.main_home('status')
        self.assertEqual((code, out['db']), (0, os.path.join(new, 'index.db')), out)
        self.assertEqual(out['home_notes'], [t('home_old_task_failed', e=t('job_schtasks', e='ERROR: Access is denied.')), t('home_move_both', old=old, new=new)])  # nothing moves later: no waits note

    def test_a_refused_removal_with_no_old_folder_says_nothing_of_a_move(self):  # --follow-job found an old task that outlived the move
        self.home_dir()
        self.scheduler({'CacheGuard': 45}, '/Delete')
        code, out, _ = self.main_home('--follow-job', 'status')
        self.assertEqual((code, out['home_notes']), (0, [t('home_old_task_failed', e=t('job_schtasks', e='ERROR: Access is denied.'))]), out)

    def test_new_task_error_during_move_is_said_too(self):
        self.assertEqual(list(self.job_error('/Create', True, 'home_job_failed').tasks), [])

    def test_the_job_comes_back_even_when_the_index_cannot_be_found(self):  # the old task is gone and the old folder is not there to bring it up next time
        old, new = self.old_home()
        with open(os.path.join(old, 'settings.json'), 'w', encoding='utf-8') as f:
            f.write('{ "indexDir": ')
        s = self.scheduler({'CacheGuard': 45})
        code, out, _ = self.main_home('status')
        self.assertEqual((code, 'error' in out, list(s.tasks)), (1, True, ['Alethite']))
        self.assertTrue(os.path.isfile(os.path.join(new, 'job.xml')))

    def test_short_command_moves_before_opening_index(self):
        old, new = self.old_home()
        conn = memdb.connect(os.path.join(old, 'index.db'))
        conn.execute("INSERT OR REPLACE INTO meta VALUES('marker', 'old')")
        conn.commit()
        conn.close()
        self.scheduler()
        code, out, _ = self.main_home('lessons-match', '--text', '429', '--model', 'nosuch')
        self.assertEqual((code, 'error' in out, 'home_notes' in out, os.path.exists(old)), (0, False, False, False), out)
        conn = memdb.connect(os.path.join(new, 'index.db'))
        self.addCleanup(conn.close)
        self.assertEqual(memdb.meta(conn, 'marker'), 'old')  # the index that moved, not a second one made in the new folder

    def test_with_db_the_home_settings_are_not_read_at_all(self):
        home = self.home_dir()
        with open(os.path.join(home, 'settings.json'), 'w') as f:
            f.write('{ "indexDir": ')
        with mock.patch.object(indexdir, 'resolve', side_effect=AssertionError):
            for cmd in (('status',), ('lessons', '--all'), ('forget', '--conv', 'x', '--pos', '1')):
                code, out, _ = self.main_json(*cmd)
                self.assertEqual(code, 0, out)
                self.assertNotIn('index_notice', out)
            self.assertEqual(self.main_json('status')[1]['index_dir'], {'configured': None, 'fallback': False, 'notes': []})

    def test_without_db_the_standard_folder_is_the_index(self):
        home = self.home_dir()
        code, out, _ = self.main_home('status')
        self.assertEqual((code, out['db'], out['index_dir']), (0, os.path.join(home, 'index.db'), {'configured': None, 'fallback': False, 'notes': []}))
        self.assertNotIn('index_notice', out)

    def test_an_index_in_its_folder_is_used_and_its_settings_are_the_home_ones(self):
        far = os.path.join(self.tmp, 'far')
        memdb.connect(os.path.join(far, 'index.db')).close()
        home = self.home_dir(indexDir=far)
        code, out, _ = self.main_home('status')
        self.assertEqual((code, out['db'], out['index_dir']), (0, os.path.join(far, 'index.db'), {'configured': far, 'fallback': False, 'notes': []}))
        self.assertNotIn('index_notice', out)
        self.assertEqual(crypto.settings_path(out['db']), os.path.join(home, 'settings.json'))

    def test_while_its_folder_is_gone_every_answer_says_so_and_nothing_is_made_there(self):
        far = os.path.join(self.tmp, 'far')  # not made: the disk is away
        home = self.home_dir(indexDir=far)
        self.write('P/S1.jsonl', [owner_line('вопрос'), claude_line('ответ')])
        open(os.path.join(home, 'index.db'), 'wb').close()  # the standard folder has an index to work with
        want = t('index_fallback', dir=far, home=home)
        for cmd in (('status',), ('index',), ('lessons', '--all'), ('forget', '--conv', 'x', '--pos', '1')):
            code, out, _ = self.main_home(*cmd)
            self.assertEqual((code, out['index_notice']), (0, want), cmd)
        self.assertEqual(self.main_home('status')[1]['index_dir'], {'configured': far, 'fallback': True, 'notes': [want]})
        self.assertFalse(os.path.exists(far))

    def test_a_failed_command_while_its_folder_is_gone_still_says_where_the_index_is(self):
        far = os.path.join(self.tmp, 'far')  # not made: the disk is away
        home = self.home_dir(indexDir=far)
        open(os.path.join(home, 'index.db'), 'wb').close()
        with mock.patch.object(encrypt, 'SUPPORTED', False):
            code, out, _ = self.main_home('index-dir')
        self.assertEqual((code, out), (1, {'error': 'ValueError: ' + t('index_dir_windows_only'), 'index_notice': t('index_fallback', dir=far, home=home)}))

    def test_a_run_that_chose_the_home_index_just_before_a_merge_deleted_it_leaves_the_new_one_marked(self):
        far = os.path.join(self.tmp, 'far')
        home = self.home_dir(indexDir=far)
        mark = os.path.join(home, indexdir.MARK)
        open(os.path.join(home, 'index.db'), 'wb').close()
        real = indexdir.resolve

        def resolve():  # the folder is back and another run merges the home index and deletes it, after this run chose it and before it opens it
            where = real()
            os.remove(where.db)
            os.remove(mark)
            return where

        with mock.patch.object(indexdir, 'resolve', resolve):
            code, out, _ = self.main_home('status')
        self.assertEqual((code, os.path.isfile(os.path.join(home, 'index.db'))), (0, True), out)
        with open(mark, encoding='utf-8') as f:  # so the next run merges it and the records it got are not lost
            self.assertEqual(f.read(), far)

    def test_no_room_for_a_new_index_is_one_error_that_makes_nothing(self):
        far = os.path.join(self.tmp, 'far')
        home = self.home_dir(indexDir=far)
        with mock.patch('shutil.disk_usage', return_value=SimpleNamespace(free=2 ** 30)):
            code, out, _ = self.main_home('status')
        text = t('index_fallback_no_space', dir=far, home=home, free=1.0, min=2)
        self.assertEqual((code, out), (1, {'error': 'ValueError: ' + text, 'index_problem': text}))
        self.assertEqual((os.listdir(home), os.path.exists(far)), (['settings.json'], False))

    def test_commands_that_need_no_index_work_when_it_cannot_be_used_and_every_answer_says_why(self):
        far = os.path.join(self.tmp, 'far')  # not made: the disk is away
        home = self.home_dir(indexDir=far)
        self.write('P/S1.jsonl', [owner_line('вопрос'), claude_line('ответ')])
        sample = os.path.join(self.tmp, 'sample.txt')
        with open(sample, 'w', encoding='utf-8') as f:
            f.write('some text, the sample of the compression')
        text = t('index_fallback_no_space', dir=far, home=home, free=1.0, min=2)
        with mock.patch('shutil.disk_usage', return_value=SimpleNamespace(free=2 ** 30)):  # no index on C: and 1 GB free: resolve raises
            for cmd in (('last-turn', '--session', 'S1'), ('compress', '--level', 'lite', '--file', sample), ('handoff', '--session', 'S1', '--today', '2026-10-27')):
                code, out, _ = self.main_home(*cmd)
                self.assertEqual((code, out['index_problem'], 'error' in out, 'index_notice' in out), (0, text, False, False), cmd)
            self.assertEqual(out['path'], os.path.join(home, 'handoffs', 'S1.md'))
            self.assertTrue(os.path.isfile(out['path']))
            self.assertEqual(sorted(os.listdir(home)), ['handoffs', 'settings.json'])  # no index made, no mark
            for cmd in (('status',), ('recall', '--query', 'x'), ('lessons-match', '--text', 'some message'), ('encrypt', 'status')):
                self.assertEqual(self.main_home(*cmd)[:2], (1, {'error': 'ValueError: ' + text, 'index_problem': text}), cmd)  # the others fail, as they did, and say it
        with open(os.path.join(home, 'settings.json'), 'w', encoding='utf-8') as f:  # the other way: nothing says where the index is
            f.write('{ "indexDir": ')
        code, out, _ = self.main_home('last-turn', '--session', 'S1')
        self.assertEqual((code, 'tokens' in out), (0, True))
        self.assertIn(os.path.join(home, 'settings.json'), out['index_problem'])
        code, err, _ = self.main_home('status')
        self.assertEqual((code, err), (1, {'error': 'ValueError: ' + out['index_problem'], 'index_problem': out['index_problem']}))

    def test_a_run_whose_index_can_be_used_has_no_problem(self):
        far = os.path.join(self.tmp, 'far')
        memdb.connect(os.path.join(far, 'index.db')).close()
        self.home_dir(indexDir=far)
        self.write('P/S1.jsonl', [owner_line('вопрос'), claude_line('ответ')])
        for cmd in (('status',), ('last-turn', '--session', 'S1'), ('forget', '--conv', 'x', '--pos', '1'), ('status', '--bad')):  # an error of the command is no problem of the index
            code, out, _ = self.main_home(*cmd)
            self.assertNotIn('index_problem', out, cmd)

    def test_the_job_on_an_index_in_its_folder_reads_the_settings_in_the_home(self):
        far, share = os.path.join(self.tmp, 'far'), self.share_dir()
        self.home_dir(indexDir=far, syncDir=share, language='uk')
        crypto.INDEX_DIRS.add(crypto.norm(far))
        self.write('P/S1.jsonl', [owner_line('вопрос'), claude_line('ответ')])
        a = cg._parser().parse_args(['--db', os.path.join(far, 'index.db'), '--projects', self.projects, 'maintain'])
        with mock.patch.dict(os.environ, {'COMPUTERNAME': 'M'}):
            out = cg.maintain(a, battery=lambda: True, digest=lambda m: None)
        self.assertEqual((out['errors'], out['sync']['pushed'], texts.lang), ([], 2, 'uk'))

    def temporary_index(self):
        """The folder is back with an empty index, and the standard folder has the temporary one, marked, with two records: (far, home)."""
        far = os.path.join(self.tmp, 'far')
        home = self.home_dir(indexDir=far)
        self.write('P/S1.jsonl', [owner_line('вопрос'), claude_line('ответ')])
        memdb.connect(os.path.join(far, 'index.db')).close()
        conn = memdb.connect(os.path.join(home, 'index.db'))
        memdb.update(conn, self.projects)
        conn.close()
        with open(os.path.join(home, indexdir.MARK), 'w', encoding='utf-8') as f:
            f.write(far)
        return far, home

    def test_a_temporary_index_is_merged_before_the_command_and_the_answer_says_so_once(self):
        far, home = self.temporary_index()
        share = self.share_dir()
        code, out, _ = self.main_home('sync', '--sync-dir', share, '--machine', 'M', '--max-seconds', '5')  # takes the lock a merge holds meanwhile
        self.assertEqual((code, out['errors'], out['pushed'], out['index_notice']), (0, [], 2, t('index_merged', dir=far, home=home, n=2)))
        self.assertEqual([os.path.exists(os.path.join(home, n)) for n in ('index.db', indexdir.MARK)], [False, False])
        code, out, _ = self.main_home('status')
        self.assertEqual((code, out['db'], out['rows']), (0, os.path.join(far, 'index.db'), 2))
        self.assertNotIn('index_notice', out)

    def test_the_forgets_a_merge_brought_in_are_kept_even_when_the_command_after_it_fails(self):
        far, home = self.temporary_index()
        with contextlib.closing(memdb.connect(os.path.join(home, 'index.db'))) as conn:  # forgotten on C: during the outage
            pos = conn.execute('SELECT pos FROM chunks ORDER BY pos').fetchone()[0]
            self.assertEqual(memdb.forget(conn, 'S1', pos), 1)
        with mock.patch.object(cg, '_status', side_effect=RuntimeError('boom')):
            code, out, _ = self.main_home('status')
        self.assertEqual((code, out), (1, {'error': 'RuntimeError: boom', 'index_notice': t('index_merged', dir=far, home=home, n=1)}))  # the merge is told in the error answer too
        self.assertFalse(os.path.exists(os.path.join(home, 'index.db')))  # the merge was done, the index on C: is gone
        with open(os.path.join(home, indexdir.FORGETS), encoding='utf-8') as f:  # so a new index on C: in a second outage forgets it too
            self.assertEqual(len(json.load(f)), 1)

    def test_a_failing_remark_does_not_replace_the_error_of_the_command_nor_stop_keep_forgets(self):
        far = os.path.join(self.tmp, 'far')
        self.home_dir(indexDir=far)
        memdb.connect(os.path.join(far, 'index.db')).close()
        with mock.patch.object(indexdir, 'remark', side_effect=OSError('disk')), mock.patch.object(indexdir, 'keep_forgets') as keep,                 mock.patch.object(cg, '_status', side_effect=RuntimeError('boom')):
            code, out, _ = self.main_home('status')
        self.assertEqual((code, out, keep.call_count), (1, {'error': 'RuntimeError: boom'}, 1))

    def test_the_prompt_hooks_lessons_match_does_not_open_the_index_again_to_keep_forgets(self):
        far = os.path.join(self.tmp, 'far')
        self.home_dir(indexDir=far)
        memdb.connect(os.path.join(far, 'index.db')).close()
        for cmd, calls in ((('lessons-match', '--text', 'some message to match'), 0), (('status',), 1)):
            with mock.patch.object(indexdir, 'keep_forgets') as keep:
                self.assertEqual(self.main_home(*cmd)[0], 0)
            self.assertEqual(keep.call_count, calls, cmd)

    def test_a_merge_that_waits_for_a_lock_says_nothing_and_the_command_works_on_the_index_in_its_folder(self):
        far, home = self.temporary_index()
        held = memdb.lock(os.path.join(home, 'index.db'), 'maintain', 0)  # the background job is running on the temporary one
        self.addCleanup(held.close)
        code, out, _ = self.main_home('status')
        self.assertEqual((code, out['db'], out['rows'], out['index_dir']), (0, os.path.join(far, 'index.db'), 0, {'configured': far, 'fallback': False, 'notes': []}))
        self.assertNotIn('index_notice', out)
        self.assertTrue(os.path.exists(os.path.join(home, 'index.db')))

    def test_a_merge_that_fails_says_why_the_command_still_runs_and_health_shows_it_too(self):
        far, home = self.temporary_index()
        with mock.patch.object(indexdir, 'absorb', side_effect=OSError('диск занят')):
            code, out, _ = self.main_home('status')
        want = t('index_merge_failed', dir=far, home=home, e='диск занят')
        self.assertEqual((code, out['db'], out['index_notice'], out['index_dir']['notes']), (0, os.path.join(far, 'index.db'), want, [want]))
        code, out, _ = self.main_home('status')  # the next run tries again, and what it merges takes the failure away and leaves the merge for health
        merged = t('index_merged', dir=far, home=home, n=2)
        self.assertEqual((code, out['rows'], out['index_notice'], out['index_dir']['notes']), (0, 2, merged, [merged]))
        self.assertEqual(self.main_home('status')[1]['index_dir']['notes'], [merged])  # and a run that merges nothing still shows it

    def test_a_run_with_little_time_leaves_a_pending_merge_to_the_next_one(self):  # the prompt hook's lessons-match has 8 s, a big merge takes more
        far, home = self.temporary_index()
        there = lambda: [os.path.exists(os.path.join(home, n)) for n in ('index.db', indexdir.MARK)]
        for cmd in (('lessons-match', '--text', 'some message to match'), ('last-turn', '--session', 'S1'), ('handoff', '--session', 'S1', '--today', '2026-10-27')):
            code, out, _ = self.main_home(*cmd)
            self.assertEqual((code, 'index_notice' in out, there()), (0, False, [True, True]), cmd)
        code, out, _ = self.main_home('status')  # a run with time merges, and the command works on the index in the folder
        self.assertEqual((code, out['index_notice'], out['rows'], there()), (0, t('index_merged', dir=far, home=home, n=2), 2, [False, False]))

    def test_encrypt_waits_while_the_index_is_away_or_a_merge_is_pending_and_its_status_still_answers(self):
        far, home = self.temporary_index()
        held = memdb.lock(os.path.join(home, 'index.db'), 'maintain', 0)  # the merge cannot run: the background job holds the temporary index
        self.addCleanup(held.close)
        want = {'error': 'ValueError: ' + t('encrypt_index_away', dir=far, home=home)}
        no_window = mock.patch('dialogs.WindowsUI', side_effect=AssertionError('the command went on to ask the owner'))
        no_window.start()
        self.addCleanup(no_window.stop)
        for state in ('pending', 'away'):
            for action in ('on', 'join', 'off'):
                self.assertEqual(self.main_home('encrypt', action)[:2], (1, {**want, **({'index_notice': t('index_fallback', dir=far, home=home)} if state == 'away' else {})}), (state, action))
            code, out, _ = self.main_home('encrypt', 'status')
            self.assertEqual((code, out['on'], out['db']), (0, False, 'plain'), state)
            if state == 'pending':
                held.close()  # on to the other state: the folder is gone and C: stands in for it
                gc.collect()
                os.rename(far, far + '-away')
        with open(os.path.join(home, 'settings.json'), encoding='utf-8') as f:
            self.assertNotIn(crypto.KEY_FIELD, json.load(f))  # no key was made, and none taken away

    def secret_in(self, db):
        """(records with the secret, forgotten keys, lines in the outbox) of the index at `db`."""
        with contextlib.closing(sqlite3.connect(db)) as conn:
            return (conn.execute("SELECT count(*) FROM chunks WHERE chunks MATCH 'hunter2xyz'").fetchone()[0],
                    conn.execute('SELECT count(*) FROM forgotten').fetchone()[0], conn.execute('SELECT count(*) FROM outbox').fetchone()[0])

    def forgotten_in_the_folder(self, old_copy):
        """The folder has its index with a secret in it, and `forget` removes it there. With `old_copy` C: has the index as it was before.
        Then the disk goes away. (far, home)"""
        far = os.path.join(self.tmp, 'far')
        home = self.home_dir(indexDir=far)
        self.write('P/S1.jsonl', [owner_line('my password is hunter2xyz'), claude_line('noted')])
        for folder in ((home, far) if old_copy else (far,)):
            conn = memdb.connect(os.path.join(folder, 'index.db'))
            memdb.update(conn, self.projects)
            conn.close()
        with contextlib.closing(sqlite3.connect(os.path.join(far, 'index.db'))) as conn:
            pos = conn.execute("SELECT pos FROM chunks WHERE text LIKE '%hunter2xyz%'").fetchone()[0]
        self.assertEqual(self.main_home('forget', '--conv', 'S1', '--pos', str(pos))[1], {'forgotten': 1})
        with open(os.path.join(home, indexdir.FORGETS), 'rb') as f:  # kept at home by that run: keys, no text
            kept = f.read()
        self.assertEqual((len(json.loads(kept)), b'hunter2xyz' in kept), (1, False))
        gc.collect()
        os.rename(far, far + '-away')
        return far, home

    def test_a_forgotten_record_does_not_come_back_in_the_new_index_made_while_the_folder_is_away(self):
        far, home = self.forgotten_in_the_folder(old_copy=False)
        with mock.patch('shutil.disk_usage', return_value=SimpleNamespace(free=10 * 2 ** 30)):
            code, out, _ = self.main_home('index')  # the first update of the new index reads every transcript again
        self.assertEqual((code, out['rows']), (0, 1))
        self.assertEqual(self.secret_in(os.path.join(home, 'index.db')), (0, 1, 0))  # the key is forgotten here, and no line for the others

    def test_a_forgotten_record_is_forgotten_in_an_old_copy_that_stands_in_for_the_folder(self):
        far, home = self.forgotten_in_the_folder(old_copy=True)
        db = os.path.join(home, 'index.db')
        self.assertEqual(self.secret_in(db), (1, 0, 0))  # the copy lacks the forget made in the folder after the move
        self.assertEqual(self.main_home('lessons', '--all')[0], 0)  # whatever the first run is, it applies the file first
        self.assertEqual(self.secret_in(db), (0, 1, 0))
        self.assertEqual(self.main_home('index', '--rebuild')[0], 0)
        self.assertEqual(self.secret_in(db), (0, 1, 0))  # and a rebuild does not bring it back

    def break_settings(self, home):
        path = os.path.join(home, 'settings.json')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('{ "indexDir": ')
        return path

    def test_unreadable_settings_applies_forgets(self):
        far, home = self.forgotten_in_the_folder(old_copy=True)
        db = os.path.join(home, 'index.db')
        self.break_settings(home)
        self.assertEqual(self.secret_in(db), (1, 0, 0))
        self.assertEqual(self.main_home('lessons', '--all')[0], 0)
        self.assertEqual(self.secret_in(db), (0, 1, 0))  # the forget made in the folder is applied here too

    def test_a_forget_made_while_the_settings_are_unreadable_reaches_the_index_folder(self):
        far = os.path.join(self.tmp, 'far')
        home = self.home_dir(indexDir=far)
        self.write('P/S1.jsonl', [owner_line('my password is hunter2xyz'), claude_line('noted')])
        for folder in (home, far):
            conn = memdb.connect(os.path.join(folder, 'index.db'))
            memdb.update(conn, self.projects)
            conn.close()
        with contextlib.closing(sqlite3.connect(os.path.join(home, 'index.db'))) as conn:
            pos = conn.execute("SELECT pos FROM chunks WHERE text LIKE '%hunter2xyz%'").fetchone()[0]
        self.break_settings(home)
        self.assertEqual(self.main_home('forget', '--conv', 'S1', '--pos', str(pos))[1]['forgotten'], 1)
        self.home_dir(indexDir=far)  # the file is mended
        self.assertEqual(self.main_home('lessons', '--all')[0], 0)
        self.assertEqual(self.secret_in(os.path.join(far, 'index.db')), (0, 1, 0))

    def test_unreadable_settings_tells_owner(self):
        far, home = self.forgotten_in_the_folder(old_copy=True)
        path = self.break_settings(home)
        code, out, _ = self.main_home('status')
        self.assertEqual(code, 0, out)
        self.assertEqual(out['index_notice'], t('index_settings_fallback', path=path, e=t('settings_broken', path=path), home=home))
        self.assertEqual(out['index_dir']['notes'], [out['index_notice']])

    def test_a_pending_merge_of_plain_indexes_never_reads_the_key(self):  # a key that cannot be read stops no plain index, merges included
        far, home = self.temporary_index()
        with open(os.path.join(home, 'settings.json'), 'w', encoding='utf-8') as f:
            json.dump({'indexDir': far, 'encryptionKey': 'junk'}, f)  # a key that is set but cannot be read
        code, out, _ = self.main_home('status')
        self.assertEqual((code, out.get('error'), out.get('index_notice')), (0, None, t('index_merged', dir=far, home=home, n=2)))
        self.assertEqual(out['rows'], 2)
        self.assertEqual([os.path.exists(os.path.join(home, n)) for n in ('index.db', indexdir.MARK)], [False, False])

    def encrypted_temporary_index(self):
        """temporary_index() with the key set before both indexes are made: the pair is encrypted. (far, home, settings path)"""
        far = os.path.join(self.tmp, 'far')
        home = self.home_dir(indexDir=far)
        settings = os.path.join(home, 'settings.json')
        crypto.save_key(settings, secrets.token_bytes(32))
        crypto.INDEX_DIRS.add(crypto.norm(far))
        self.write('P/S1.jsonl', [owner_line('вопрос'), claude_line('ответ')])
        memdb.connect(os.path.join(far, 'index.db')).close()
        conn = memdb.connect(os.path.join(home, 'index.db'))
        memdb.update(conn, self.projects)
        conn.close()
        with open(os.path.join(home, indexdir.MARK), 'w', encoding='utf-8') as f:
            f.write(far)
        self.assertTrue(crypto.is_encrypted(os.path.join(far, 'index.db')) and crypto.is_encrypted(os.path.join(home, 'index.db')))
        return far, home, settings

    def test_a_pending_merge_of_encrypted_indexes_reads_the_key_and_merges(self):
        far, home, _ = self.encrypted_temporary_index()
        code, out, _ = self.main_home('status')
        self.assertEqual((code, out.get('error'), out.get('index_notice'), out.get('rows')), (0, None, t('index_merged', dir=far, home=home, n=2), 2))
        self.assertEqual([os.path.exists(os.path.join(home, n)) for n in ('index.db', indexdir.MARK)], [False, False])

    def test_a_pending_merge_of_encrypted_indexes_with_a_key_that_cannot_be_read_is_told_and_changes_nothing(self):
        far, home, settings = self.encrypted_temporary_index()
        broken = crypto.SettingsBroken(t('settings_broken', path=settings))
        for fail, want in ((None, 'ValueError: ' + t('key_unreadable')), (broken, 'SettingsBroken: ' + str(broken))):
            if fail is None:
                with open(settings, 'w', encoding='utf-8') as f:
                    json.dump({'indexDir': far, 'encryptionKey': 'junk'}, f)
                ctx = contextlib.nullcontext()
            else:
                ctx = mock.patch.object(crypto, 'load_key', side_effect=fail)
            with ctx:
                code, out, _ = self.main_home('status')
            self.assertEqual((code, out), (1, {'error': want}))
            self.assertEqual([os.path.exists(os.path.join(home, n)) for n in ('index.db', indexdir.MARK)], [True, True])

    # index-dir: the move, as the mod runs it

    def move_ui(self, *answers, folder=None):
        """The windows of the move are a fake, and every disk is a fixed one: the temp folder may lie on any."""
        ui = FakeUI(answers, folder=folder)
        for patch in (mock.patch('dialogs.WindowsUI', lambda: ui), mock.patch.object(indexdir, '_drive_type', lambda root: 3)):
            patch.start()
            self.addCleanup(patch.stop)
        return ui

    def home_index(self, home):
        self.write('P/S1.jsonl', [owner_line('вопрос'), claude_line('ответ')])
        conn = memdb.connect(os.path.join(home, 'index.db'))
        memdb.update(conn, self.projects)
        conn.close()

    @unittest.skipUnless(os.name == 'nt', 'the move is Windows only')
    def test_index_dir_moves_the_index_and_every_next_run_follows_it(self):
        home = self.home_dir()
        self.home_index(home)
        far = os.path.join(self.tmp, 'far')
        ui = self.move_ui(False)
        code, out, _ = self.main_home('index-dir', '--path', far)
        self.assertEqual((code, out), (0, {'action': 'index-dir', 'done': True, 'dir': far, 'notes': [t('index_old_kept', dir=home)]}))
        self.assertEqual(ui.asked, [t('dlg_delete_old_index', dir=home)])
        code, out, _ = self.main_home('status')
        self.assertEqual((code, out['db'], out['rows']), (0, os.path.join(far, 'index.db'), 2))
        self.assertEqual(out['index_dir'], {'configured': far, 'fallback': False, 'notes': [t('index_old_copy', dir=home)]})
        self.assertNotIn('index_notice', out)

    @unittest.skipUnless(os.name == 'nt', 'the move is Windows only')
    def test_a_merge_done_in_the_same_run_is_no_pending_merge_for_the_move(self):  # the folder is back: the common case of the first move after it
        far, home = self.temporary_index()
        other = os.path.join(self.tmp, 'other')
        self.move_ui()
        code, out, _ = self.main_home('index-dir', '--path=' + other)
        self.assertEqual((code, out.get('error')), (0, None))
        self.assertEqual((out['done'], out['dir'], out['notes']), (True, other, []))
        self.assertEqual(out['index_notice'], t('index_merged', dir=far, home=home, n=2))  # the merge is told, not lost
        self.assertEqual([os.path.exists(os.path.join(home, n)) for n in ('index.db', indexdir.MARK)], [False, False])
        code, out, _ = self.main_home('status')
        self.assertEqual((code, out['db'], out['rows']), (0, os.path.join(other, 'index.db'), 2))

    @unittest.skipUnless(os.name == 'nt', 'the move is Windows only')
    def test_a_merge_that_did_not_happen_still_stops_the_move(self):
        far, home = self.temporary_index()
        held = memdb.lock(os.path.join(home, 'index.db'), 'maintain', 0)  # the background job runs on the temporary index: no merge this run
        self.addCleanup(held.close)
        self.move_ui()
        other = os.path.join(self.tmp, 'other')
        code, out, _ = self.main_home('index-dir', '--path=' + other)
        self.assertEqual((code, out), (1, {'error': 'ValueError: ' + t('index_dir_merge_pending', dir=far, home=home)}))
        self.assertEqual((os.path.exists(os.path.join(home, 'index.db')), os.path.exists(other)), (True, False))

    @unittest.skipUnless(os.name == 'nt', 'the move is Windows only')
    def test_the_locks_the_move_holds_are_given_back_before_the_old_copy_goes(self):
        home = self.home_dir()
        self.home_index(home)
        self.move_ui(True)
        code, out, _ = self.main_home('index-dir', '--path', os.path.join(self.tmp, 'far'))
        self.assertEqual((code, out['done'], out['notes']), (0, True, []))  # a lock file still open would have been index_old_busy
        self.assertEqual(os.listdir(home), ['settings.json'])

    @unittest.skipUnless(os.name == 'nt', 'the move is Windows only')
    def test_a_busy_index_stops_the_move_before_anything_and_none_of_the_locks_is_kept(self):
        home = self.home_dir()
        self.home_index(home)
        self.move_ui()
        far = os.path.join(self.tmp, 'far')
        db = os.path.join(home, 'index.db')
        held = memdb.lock(db, 'embed', 0)  # a chat embeds right now
        self.addCleanup(held.close)
        with mock.patch.object(cg, 'ENCRYPT_WAITS', {'maintain': 0, 'embed': 0, 'sync': 0}):
            code, out, _ = self.main_home('index-dir', '--path', far)
        self.assertEqual((code, out), (1, {'error': 'ValueError: ' + t('index_dir_busy')}))
        self.assertFalse(os.path.exists(far))
        held.close()
        for name in ('maintain', 'embed', 'sync'):
            memdb.lock(db, name, 0).close()

    @unittest.skipUnless(os.name == 'nt', 'the move is Windows only')
    def test_a_refusal_that_changes_nothing_comes_before_the_locks(self):
        home = self.home_dir()
        self.home_index(home)
        self.move_ui()
        held = memdb.lock(os.path.join(home, 'index.db'), 'embed', 0)  # busy: waiting for it would end in index_dir_busy
        self.addCleanup(held.close)
        far = os.path.join(self.tmp, 'far')
        with mock.patch.object(cg, 'ENCRYPT_WAITS', {'maintain': 0, 'embed': 0, 'sync': 0}), mock.patch.object(indexdir, '_drive_type', lambda root: 4):
            code, out, _ = self.main_home('index-dir', '--path', far)
        self.assertEqual((code, out), (1, {'error': 'ValueError: ' + t('index_dir_not_local', dir=far)}))

    @unittest.skipUnless(os.name == 'nt', 'the move is Windows only')
    def test_the_folder_window_does_not_open_for_a_move_that_is_refused_anyway(self):
        far, home = self.temporary_index()
        held = memdb.lock(os.path.join(home, 'index.db'), 'maintain', 0)  # no merge this run
        self.addCleanup(held.close)
        ui = self.move_ui()
        ui.pick_folder = mock.Mock(return_value=os.path.join(self.tmp, 'other'))
        code, out, _ = self.main_home('index-dir')
        self.assertEqual((code, out), (1, {'error': 'ValueError: ' + t('index_dir_merge_pending', dir=far, home=home)}))
        ui.pick_folder.assert_not_called()
        with mock.patch.object(encrypt, 'SUPPORTED', False):
            code, out, _ = self.main_home('index-dir')
        self.assertEqual((code, out), (1, {'error': 'ValueError: ' + t('index_dir_windows_only')}))
        ui.pick_folder.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'the move is Windows only')
    def test_without_a_path_the_window_chooses_the_folder_and_cancelling_it_changes_nothing(self):
        home = self.home_dir()
        self.home_index(home)
        far = os.path.join(self.tmp, 'far')
        before = sorted(os.listdir(home))
        self.move_ui(folder=None)
        self.assertEqual(self.main_home('index-dir')[:2], (0, {'done': False, 'notes': [t('index_dir_cancelled')]}))
        self.assertEqual((sorted(os.listdir(home)), os.path.exists(far)), (before, False))
        self.move_ui(False, folder=far)
        code, out, _ = self.main_home('index-dir')
        self.assertEqual((code, out['done'], out['dir']), (0, True, far))

    @unittest.skipUnless(os.name == 'nt', 'the move is Windows only')
    def test_the_index_on_c_made_permanent_does_not_report_the_folder_that_is_gone(self):
        far = os.path.join(self.tmp, 'far')  # not made: the disk is away
        home = self.home_dir(indexDir=far)
        open(os.path.join(home, 'index.db'), 'wb').close()
        self.assertEqual(self.main_home('status')[1]['index_notice'], t('index_fallback', dir=far, home=home))
        self.move_ui()
        code, out, _ = self.main_home('index-dir', '--path', home)
        self.assertEqual((code, out['done'], out['notes']), (0, True, [t('index_dir_permanent', dir=home)]))
        self.assertNotIn('index_notice', out)  # the notice of the state before the command is stale by now
        code, out, _ = self.main_home('status')
        self.assertEqual((out['index_dir'], 'index_notice' in out), ({'configured': None, 'fallback': False, 'notes': []}, False))
        with open(os.path.join(home, 'settings.json'), encoding='utf-8') as f:
            self.assertNotIn('indexDir', json.load(f))

    @unittest.skipUnless(os.name == 'nt', 'the move is Windows only')
    def test_with_db_the_index_named_is_the_one_moved(self):
        home = self.home_dir()
        memdb.connect(self.db).close()
        far = os.path.join(self.tmp, 'far')
        self.move_ui()
        code, out, _ = self.main_json('index-dir', '--path', far)
        self.assertEqual((code, out['done'], out['notes']), (0, True, []))
        self.assertEqual(os.listdir(far), ['index.db'])
        self.assertEqual(os.path.exists(self.db), False)
        with open(os.path.join(home, 'settings.json'), encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'indexDir': far})


@unittest.skipUnless(os.name == 'nt', 'user32 is Windows only')
class DialogsTest(unittest.TestCase):
    """No window opens here: the system is a fake. How the windows look is shown to the owner by hand."""

    def test_the_boxes_are_topmost_and_a_yes_is_the_only_yes(self):
        user32 = mock.Mock()
        with mock.patch('ctypes.WinDLL', return_value=user32):
            user32.MessageBoxW.return_value = 1
            dialogs.WindowsUI().show_code('CG1-AAAA')
            user32.MessageBoxW.return_value = 6
            self.assertTrue(dialogs.WindowsUI().confirm('?'))
            user32.MessageBoxW.return_value = 7
            self.assertFalse(dialogs.WindowsUI().confirm('?'))
        calls = [c[0] for c in user32.MessageBoxW.call_args_list]
        self.assertIn('CG1-AAAA', calls[0][1])
        # topmost, foreground; Yes/No with No the default (a held Enter must not say Yes), warning sign
        self.assertEqual([c[3] for c in calls[:2]], [0x40000 | 0x10000, 0x40000 | 0x10000 | 0x4 | 0x30 | 0x100])

    def test_a_window_that_cannot_open_says_what_failed_not_what_it_held(self):
        user32 = mock.Mock()
        user32.MessageBoxW.return_value = 0
        with mock.patch('ctypes.WinDLL', return_value=user32):
            for call in (lambda ui: ui.show_code('CG1-SECRET'), lambda ui: ui.confirm('SECRET?')):
                with self.assertRaises(OSError) as e:
                    call(dialogs.WindowsUI())
                self.assertNotIn('SECRET', str(e.exception))

    def test_the_folder_window_is_hidden_on_top_and_gives_back_the_folder_or_none(self):
        tk = mock.MagicMock()
        for chosen, want in (('D:/Cache', 'D:/Cache'), ('', None), ((), None)):  # a cancelled window gives back an empty string or tuple
            tk.filedialog.askdirectory.return_value = chosen
            with mock.patch.dict(sys.modules, {'tkinter': tk, 'tkinter.filedialog': tk.filedialog}):
                self.assertEqual(dialogs.pick_folder('title'), want)
        root = tk.Tk.return_value
        root.withdraw.assert_called()
        root.attributes.assert_called_with('-topmost', True)
        self.assertEqual(root.destroy.call_count, 3)  # whatever the answer
        tk.filedialog.askdirectory.assert_called_with(parent=root, title='title')

    def test_without_tkinter_the_folder_window_asks_for_the_path_in_the_command(self):
        for modules in ({'tkinter': None}, {'tkinter': mock.Mock(Tk=mock.Mock(side_effect=Exception('no Tcl')))}):  # no package, or no Tcl/Tk behind it
            with mock.patch.dict(sys.modules, modules), self.assertRaises(ValueError) as e:
                dialogs.pick_folder('title')
            self.assertEqual(str(e.exception), t('index_dir_no_picker'))

    def test_the_ui_asks_for_the_folder_with_its_title(self):
        with mock.patch.object(dialogs, 'pick_folder', return_value='D:/Cache') as pick:
            self.assertEqual(dialogs.WindowsUI().pick_folder(), 'D:/Cache')
        pick.assert_called_once_with(t('dlg_pick_index_dir'))

    def test_without_tkinter_the_code_is_typed_in_a_console_and_comes_back_through_the_pipe_only(self):
        for returncode, stdout, want in ((0, b'CG1-AAAA', 'CG1-AAAA'), (1, b'', None)):  # closing the console window is a cancel
            ran = SimpleNamespace(returncode=returncode, stdout=stdout)
            with mock.patch.dict(sys.modules, {'tkinter': None}), mock.patch('subprocess.run', return_value=ran) as run:
                self.assertEqual(dialogs.WindowsUI().ask_code(), want)
            args, kw = run.call_args
            self.assertEqual((args[0][:3], kw['stdout'], kw['creationflags']), ([sys.executable, '-I', '-c'], subprocess.PIPE, 0x10))  # -I: no modules of the folder it runs from
        ran = SimpleNamespace(returncode=0, stdout=b'')
        # a window-less Python gets no console from CREATE_NEW_CONSOLE: the python.exe beside it runs the entry
        with mock.patch.dict(sys.modules, {'tkinter': None}), mock.patch('subprocess.run', return_value=ran) as run, \
                mock.patch.object(sys, 'executable', 'C:\\Py\\Pythonw.exe'):
            dialogs.WindowsUI().ask_code()
        self.assertEqual(run.call_args[0][0][0], 'C:\\Py\\python.exe')


if __name__ == '__main__':
    unittest.main()
