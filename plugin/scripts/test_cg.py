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
import lessons
import memdb
import vectors
from test_encrypt import FakeUI
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
        """cg.main in this process, where a window can be faked: (exit code, what it printed as JSON, everything it wrote: stdout and stderr)."""
        out, err = (io.TextIOWrapper(io.BytesIO(), encoding='utf-8') for _ in range(2))
        with mock.patch.object(sys, 'stdout', out), mock.patch.object(sys, 'stderr', err):
            code = cg.main(['--db', self.db, '--projects', self.projects, *args])
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
                    cg._alone(SimpleNamespace(db=db), name, 0)
            ran.append(db)
            return {'action': 'on', 'done': True, 'notes': []}

        with mock.patch.object(encrypt, 'on', fake_on):
            self.assertEqual(self.main_json('encrypt', 'on')[1]['done'], True)
        self.assertEqual(ran, [self.db])
        for name in ('maintain', 'embed', 'sync'):  # and let go afterwards
            cg._alone(SimpleNamespace(db=self.db), name, 0).close()

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
        self.assertEqual(list(out), ['errors'])  # the index cannot be opened to keep the run in: /cg-health shows the same words from `status`
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
