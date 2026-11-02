import gc
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
import crypto
import memdb
from memdb import connect, cut, expand, owner_count, status, update, word_hits
from test_crypto import deny_read, hold
from texts import t
from transcripts import project_key

TS = '2026-10-13T10:00:00.000Z'
KEY = bytes(range(32))
dpapi = unittest.skipUnless(os.name == 'nt', 'DPAPI is Windows only')


def search(conn, query, project=None, limit=8, budget=7000):  # what the recall does of the word hits: the rows beyond `limit` stand in for the repeats cut drops
    return cut(word_hits(conn, query, project, max(limit, 50)), budget, limit)


def owner_line(text, ts=TS, cwd='C:\\a'):
    return dict(type='user', timestamp=ts, cwd=cwd, message={'role': 'user', 'content': text})


def claude_line(text, ts=TS, cwd='C:\\a'):
    m = {'id': 'm1', 'model': 'claude-opus-5-5', 'role': 'assistant', 'content': [{'type': 'text', 'text': text}]}
    return dict(type='assistant', timestamp=ts, cwd=cwd, message=m)


def tool_line(name, ts=TS, **inp):
    m = {'id': 'm1', 'model': 'claude-opus-5-5', 'role': 'assistant', 'content': [{'type': 'tool_use', 'name': name, 'input': inp}]}
    return dict(type='assistant', timestamp=ts, cwd='C:\\a', message=m)


class LockTest(unittest.TestCase):
    def test_a_writer_waits_for_a_sync_up_to_30_seconds(self):  # a sync holds the write lock while it writes to the share
        with tempfile.TemporaryDirectory() as d:
            conn = connect(os.path.join(d, 'index.db'))
            self.assertEqual(conn.execute('PRAGMA busy_timeout').fetchone()[0], 30000)
            conn.close()

    def test_lock_is_one_per_name_and_index_and_ends_with_its_connection(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, 'index.db')
            held = memdb.lock(db, 'sync', 0)
            with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                memdb.lock(db, 'sync', 0)
            memdb.lock(db, 'embed', 0).close()  # another name is another lock
            memdb.lock(os.path.join(d, 'other.db'), 'sync', 0).close()  # and another index
            held.close()
            gc.collect()  # the lock that did not open stays in the traceback of its error until collected, and Windows will not remove its file
            memdb.lock(db, 'sync', 0).close()

    def test_a_lock_that_was_busy_does_not_keep_its_file_open(self):  # a later task deletes the lock files, and Windows refuses an open one
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, 'index.db')
            held = memdb.lock(db, 'sync', 0)
            try:
                memdb.lock(db, 'sync', 0)
            except sqlite3.OperationalError as e:
                refused = e  # its traceback keeps the frame of lock(), and the connection in it, alive
            held.close()
            os.remove(db + '.sync.lock')
            self.assertTrue(refused)


class EncryptedIndexTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.projects = os.path.join(d.name, 'projects')
        self.db = os.path.join(d.name, 'db', 'index.db')
        self.settings = crypto.settings_path(self.db)
        os.makedirs(os.path.dirname(self.db))

    def raw(self):
        with open(self.db, 'rb') as f:
            return f.read()

    @dpapi
    def test_connect_encrypted_new(self):
        crypto.save_key(self.settings, KEY)
        os.makedirs(os.path.join(self.projects, 'P'))
        with open(os.path.join(self.projects, 'P', 'S.jsonl'), 'wb') as f:
            f.write((json.dumps(owner_line('зашифрованноеслово в тексте'), ensure_ascii=False) + '\n').encode('utf-8'))
        conn = connect(self.db)
        try:
            self.assertEqual(update(conn, self.projects)['rows'], 1)
            self.assertEqual(len(search(conn, 'зашифрованноеслово')), 1)
        finally:
            conn.close()
        self.assertTrue(crypto.is_encrypted(self.db))
        crypto.open_db(self.db, KEY, page=crypto.INDEX_PAGE).close()  # a new encrypted index is made with 16 KB pages
        with self.assertRaises(ValueError):
            crypto.open_db(self.db, KEY)
        self.assertNotIn('зашифрованноеслово'.encode('utf-8'), self.raw())
        conn = connect(self.db)  # an encrypted file opens again with the key
        try:
            self.assertEqual(conn.execute('SELECT count(*) FROM chunks').fetchone(), (1,))
        finally:
            conn.close()

    @dpapi
    def test_connect_missing_key(self):
        crypto.save_key(self.settings, KEY)
        connect(self.db).close()
        crypto.save_key(self.settings, None)
        before = hashlib.sha1(self.raw()).hexdigest()
        with self.assertRaises(ValueError) as cm:
            connect(self.db)
        self.assertEqual(str(cm.exception), t('key_missing'))
        self.assertEqual(hashlib.sha1(self.raw()).hexdigest(), before)
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.db))), ['index.db', 'settings.json'])

    def test_connect_no_key_no_db_is_plain(self):
        connect(self.db).close()
        self.assertEqual(self.raw()[:16], b'SQLite format 3\x00')

    def test_a_plain_index_never_reads_the_key(self):  # a broken settings file must not stop whoever has not turned encryption on
        connect(self.db).close()
        with open(self.settings, 'w') as f:
            f.write('{broken')
        with mock.patch.object(crypto, 'load_key', side_effect=AssertionError('the key was read')):
            connect(self.db).close()
        self.assertFalse(crypto.is_encrypted(self.db))

    def test_settings_that_cannot_give_a_key(self):
        with open(self.settings, 'w') as f:
            f.write('{broken')
        connect(self.db).close()  # no index yet and no key to be seen: a plain one, as before stage 5 (the job says the file is broken)
        self.assertFalse(crypto.is_encrypted(self.db))
        os.remove(self.db)
        with open(self.settings, 'w') as f:
            f.write('{"%s": "not a key"}' % crypto.KEY_FIELD)
        with self.assertRaises(ValueError) as cm:  # a key is set and unreadable: no plain index beside it
            connect(self.db)
        self.assertEqual(str(cm.exception), t('key_unreadable'))
        self.assertFalse(os.path.exists(self.db))
        c = crypto.open_db(self.db, KEY, page=crypto.INDEX_PAGE)
        c.execute('CREATE TABLE x(y)')  # the file is there only after a write
        c.close()
        with open(self.settings, 'w') as f:
            f.write('{broken')
        with self.assertRaises(ValueError) as cm:  # an encrypted index needs the key: the file that may hold it is no reason to guess
            connect(self.db)
        self.assertEqual(str(cm.exception), t('settings_broken', path=self.settings))

    def test_a_settings_file_that_cannot_be_opened_is_one_that_shows_no_key(self):
        os.mkdir(self.settings)  # a folder cannot be opened: the OSError of every system, as an editor's lock or a refused right
        connect(self.db).close()  # no index yet: a plain one, as before stage 5
        self.assertFalse(crypto.is_encrypted(self.db))
        os.remove(self.db)
        open(self.db, 'wb').close()  # an empty file is no index yet either
        connect(self.db).close()
        self.assertFalse(crypto.is_encrypted(self.db))
        os.remove(self.db)
        c = crypto.open_db(self.db, KEY, page=crypto.INDEX_PAGE)
        c.execute('CREATE TABLE x(y)')
        c.close()
        before =hashlib.sha1(self.raw()).hexdigest()
        with self.assertRaises(OSError):  # an encrypted index needs the key: it is refused, never guessed at
            connect(self.db)
        self.assertEqual(hashlib.sha1(self.raw()).hexdigest(), before)

    @dpapi
    def test_an_empty_file_is_a_new_index(self):  # sqlite makes the file only on the first write: a crash before it must not leave a plain index
        crypto.save_key(self.settings, KEY)
        open(self.db, 'wb').close()
        connect(self.db).close()
        self.assertTrue(crypto.is_encrypted(self.db))

    def pragma(self, name):
        conn = connect(self.db)
        try:
            return conn.execute('PRAGMA ' + name).fetchone()[0]
        finally:
            conn.close()

    @dpapi
    def test_connect_keeps_temp_in_memory(self):  # %TEMP% is not under EFS
        self.assertEqual(self.pragma('temp_store'), 2)  # a new index
        self.assertEqual(self.pragma('temp_store'), 2)  # an open one
        os.remove(self.db)
        crypto.save_key(self.settings, KEY)
        self.assertEqual(self.pragma('temp_store'), 2)  # a new encrypted one
        self.assertEqual(self.pragma('temp_store'), 2)  # an encrypted one again

    @dpapi
    def test_new_index_in_efs_folder_has_16k_pages(self):
        self.assertEqual(self.pragma('page_size'), 4096)  # an ordinary folder
        crypto.efs_encrypt(os.path.dirname(self.db))
        self.assertEqual(self.pragma('page_size'), 4096)  # an index that is there keeps its pages
        os.remove(self.db)
        self.assertEqual(self.pragma('page_size'), 16384)  # a new one
        self.assertTrue(crypto.efs_on(self.db))
        self.assertEqual(self.pragma('page_size'), 16384)

    @dpapi
    def test_an_empty_file_in_efs_folder_is_a_new_index(self):
        crypto.efs_encrypt(os.path.dirname(self.db))
        open(self.db, 'wb').close()
        self.assertEqual(self.pragma('page_size'), 16384)

    def efs_index(self):
        crypto.efs_encrypt(os.path.dirname(self.db))
        connect(self.db).close()
        self.assertTrue(crypto.efs_on(self.db))
        return os.path.getsize(self.db), os.stat(self.db).st_mtime_ns

    def assert_untouched(self, before):
        self.assertEqual((os.path.getsize(self.db), os.stat(self.db).st_mtime_ns), before)
        self.assertEqual(os.listdir(os.path.dirname(self.db)), ['index.db'])  # no new index beside it

    @dpapi
    def test_efs_index_denied_is_an_error(self):  # "access denied" (5) of a file that EFS holds: never a new empty index in its place
        before = self.efs_index()
        deny_read(self, self.db)
        with self.assertRaises(ValueError) as cm:
            connect(self.db)
        self.assertEqual(str(cm.exception), t('index_efs_denied', path=self.db))
        self.assert_untouched(before)

    @dpapi
    def test_a_busy_efs_index_is_not_denied(self):  # in use (32): the owner would be told to remove a good index; the error stays what it was
        before = self.efs_index()
        hold(self, self.db)
        with self.assertRaises(PermissionError):
            connect(self.db)
        self.assert_untouched(before)

    def test_peers_gen_added(self):
        old = sqlite3.connect(self.db)
        old.execute("CREATE TABLE peers(machine TEXT PRIMARY KEY, last_batch INTEGER NOT NULL DEFAULT 0, skipped TEXT NOT NULL DEFAULT '[]', "
                    "model TEXT, digest TEXT, error TEXT)")
        old.execute("INSERT INTO peers(machine, last_batch) VALUES('M', 3)")
        old.commit()
        old.close()
        for _ in range(2):  # the second connect finds the column there
            conn = connect(self.db)
            try:
                self.assertEqual(conn.execute('SELECT machine, last_batch, gen FROM peers').fetchall(), [('M', 3, 0)])
            finally:
                conn.close()

    def test_a_new_index_has_peers_gen(self):
        conn = connect(self.db)
        try:
            self.assertEqual(conn.execute('SELECT gen FROM peers').fetchall(), [])
        finally:
            conn.close()

    def test_db_errors(self):
        with mock.patch.dict(sys.modules):
            sys.modules.pop('sqlcipher3', None)  # not imported unless an encrypted index was opened
            self.assertEqual(memdb.db_errors(), (sqlite3.OperationalError,))
        import sqlcipher3
        self.assertEqual(memdb.db_errors(), (sqlite3.OperationalError, sqlcipher3.OperationalError))


class MemdbTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.projects = os.path.join(d.name, 'projects')
        self.db = os.path.join(d.name, 'db', 'index.db')
        self.conn = connect(self.db)  # connect creates the folder
        self.addCleanup(self.conn.close)

    def write(self, name, objs, mode='wb'):
        path = os.path.join(self.projects, *name.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode) as f:
            f.write(''.join(json.dumps(o, ensure_ascii=False) + '\n' for o in objs).encode('utf-8'))
        return path

    def update(self, **kw):
        return update(self.conn, self.projects, **kw)

    def rows_of(self, conv):
        return self.conn.execute('SELECT count(*) FROM chunks WHERE conv = ?', (conv,)).fetchone()[0]

    def kinds_of(self, conv):
        return [r[0] for r in self.conn.execute('SELECT kind FROM chunks WHERE conv = ? ORDER BY pos, rowid', (conv,))]

    def test_incremental(self):
        self.assertIsNone(status(self.conn)['last_update'])
        self.write('P/S.jsonl', [owner_line('первая'), claude_line('ответ')])
        s = self.update()
        self.assertGreater(s['rows'], 0)
        self.assertEqual((s['files'], s['rows'], s['convs'], s['errors']), (1, 2, 1, []))
        self.write('P/S.jsonl', [owner_line('добавка')], 'ab')
        s2 = self.update()
        self.assertEqual((s2['files'], s2['rows']), (1, 1))
        self.assertEqual(self.update()['files'], 0)
        st = status(self.conn)
        self.assertEqual((st['convs'], st['rows']), (1, 3))
        self.assertTrue(st['last_update'] and os.path.isfile(st['db']) and st['size_mb'] > 0)
        s3 = self.update(rebuild=True)  # re-reads the file from the start; only what the index lacks is added, and it lacks nothing
        self.assertEqual((s3['files'], s3['rows'], s3['errors']), (1, 0, []))
        self.assertEqual(status(self.conn)['rows'], 3)

    def test_rebuild_keeps_deleted_transcripts(self):
        self.write('P/A.jsonl', [owner_line('альфа здесь'), owner_line('и ещё альфа')])
        b = self.write('P/B.jsonl', [owner_line('бета здесь')])
        self.update()
        os.remove(b)  # Claude Code cleaned it up; the index must remember it
        s = self.update(rebuild=True)
        self.assertEqual((s['files'], s['rows'], s['convs']), (1, 0, 2))
        self.assertEqual((self.rows_of('A'), self.rows_of('B')), (2, 1))
        self.assertEqual(search(self.conn, 'бета')[0]['conv'], 'B')
        with self.assertRaises(OSError):  # a wrong folder must not wipe anything
            update(self.conn, os.path.join(self.projects, 'нет'), rebuild=True)
        self.assertEqual(status(self.conn)['rows'], 3)

    def test_missing_root_raises_and_keeps_last_update(self):
        with self.assertRaises(OSError):
            update(self.conn, os.path.join(self.projects, 'нет'))
        self.assertIsNone(status(self.conn)['last_update'])

    def test_unreadable_file_is_reported_and_retried(self):
        for n in ('good', 'bad', 'gone'):
            self.write('P/%s.jsonl' % n, [owner_line('текст ' + n)])
        real = memdb.read_records

        def fake(path, start=0):
            if os.path.basename(path) == 'bad.jsonl':
                raise PermissionError(13, 'нет доступа')
            if os.path.basename(path) == 'gone.jsonl':
                raise FileNotFoundError(2, 'исчез')  # vanished after the walk: not an error
            return real(path, start)

        with mock.patch.object(memdb, 'read_records', fake):
            s = self.update()
        self.assertEqual((s['files'], s['rows']), (1, 1))
        self.assertEqual([(os.path.basename(e['path']), e['error'].split(':')[0]) for e in s['errors']], [('bad.jsonl', 'PermissionError')])
        self.assertEqual((self.rows_of('good'), self.rows_of('bad'), self.rows_of('gone')), (1, 0, 0))
        s2 = self.update()
        self.assertEqual((s2['files'], s2['rows'], s2['errors']), (2, 2, []))

        def walk(top, onerror=None):  # a subfolder that cannot be listed
            onerror(PermissionError(13, 'нет доступа', os.path.join(top, 'sub')))
            yield top, [], []

        with mock.patch.object(os, 'walk', walk):
            s3 = self.update()
        self.assertEqual([(os.path.basename(e['path']), e['error'].split(':')[0]) for e in s3['errors']], [('sub', 'PermissionError')])

    def test_failure_mid_file_rolls_back(self):
        self.write('P/bad.jsonl', [owner_line('раз', cwd='C:\bad'), owner_line('два', cwd='C:\bad')])
        self.write('P/good.jsonl', [owner_line('три')])
        real = memdb.project_key

        def key(cwd, *a):  # runs after the file's chunks are inserted
            if cwd == 'C:\bad':
                raise ValueError('сбой')
            return real(cwd, *a)

        with mock.patch.object(memdb, 'project_key', key):
            s = self.update()
        self.assertEqual([(os.path.basename(e['path']), e['error']) for e in s['errors']], [('bad.jsonl', 'ValueError: сбой')])
        self.assertEqual((self.rows_of('bad'), self.rows_of('good')), (0, 1))
        self.assertFalse(self.conn.in_transaction)

    def test_overlapping_updates_do_not_duplicate(self):
        self.write('P/S.jsonl', [owner_line('раз'), owner_line('два')])
        other = connect(self.db)  # the owner's second chat
        self.addCleanup(other.close)
        other.execute('PRAGMA busy_timeout = 0')  # the lock is held by this very thread: fail at once instead of waiting
        real, nested = memdb.read_records, []

        def racing(path, start=0):
            if not nested:  # the other update runs between this one reading the offset and inserting
                try:
                    nested.append(update(other, self.projects))
                except sqlite3.OperationalError as e:  # locked out, which is the point
                    nested.append(str(e))
            return real(path, start)

        with mock.patch.object(memdb, 'read_records', racing):
            self.update()
        self.assertEqual(len(nested), 1)
        self.assertEqual(self.rows_of('S'), 2)
        self.assertEqual(update(other, self.projects)['files'], 0)

    def test_an_unchanged_file_is_told_without_the_write_lock(self):  # a search must not wait on every transcript while a sync writes
        self.write('P/S.jsonl', [owner_line('раз')])
        self.update()
        path = self.conn.execute('SELECT path FROM files').fetchone()[0]  # the key as update stored it
        other = connect(self.db)
        self.addCleanup(other.close)
        other.execute('BEGIN IMMEDIATE')  # the sync's write lock
        self.conn.execute('PRAGMA busy_timeout = 200')  # connect waits 30 s
        self.assertIsNone(memdb._file(self.conn, path, 'S', '', False, None))  # update itself still ends with a write of last_update

    def test_shorter_file_keeps_what_was_indexed(self):  # stage 2 §6: text that got into the index never leaves it
        self.write('P/S.jsonl', [owner_line('одна'), owner_line('две'), owner_line('три')])
        self.update()
        self.write('P/S.jsonl', [owner_line('одна'), owner_line('четыре')])  # the file got shorter: read again from the start
        s = self.update()
        self.assertEqual((s['files'], s['rows']), (1, 1))  # only what is new
        self.assertEqual(sorted(r[0] for r in self.conn.execute("SELECT text FROM chunks WHERE conv = 'S'")), ['две', 'одна', 'три', 'четыре'])
        self.assertEqual(search(self.conn, 'три')[0]['conv'], 'S')
        self.write('P/S.jsonl', [owner_line('пять')], 'ab')  # and it goes on growing as before
        self.assertEqual(self.update()['rows'], 1)
        self.assertEqual(self.rows_of('S'), 5)

    def keys_of(self, conv):
        return [tuple(r) for r in self.conn.execute(
            'SELECT c.text, k.cls, k.hash, k.n, k.origin, k.pushed FROM keys k JOIN chunks c ON c.rowid = k.id WHERE k.conv = ? ORDER BY c.pos, k.id', (conv,))]

    def test_keys_count_the_occurrences_of_a_text(self):
        self.write('P/S.jsonl', [owner_line('да'), claude_line('да'), owner_line('да')])
        self.update()
        self.write('P/S.jsonl', [owner_line('да')], 'ab')
        self.update()
        h = memdb.text_hash('да')
        self.assertEqual(self.keys_of('S'), [('да', 'owner', h, 0, None, 0), ('да', 'claude', h, 0, None, 0), ('да', 'owner', h, 1, None, 0),
                                             ('да', 'owner', h, 2, None, 0)])
        self.update(rebuild=True)  # the same keys from a read that starts at the beginning
        self.assertEqual(self.rows_of('S'), 4)

    def test_a_report_keeps_its_key_when_a_newer_one_demotes_it(self):
        self.write('P/S/subagents/agent-a1.jsonl', [owner_line('сделай'), claude_line('итог1')])
        self.update()
        before = self.keys_of('agent-a1')
        self.write('P/S/subagents/agent-a1.jsonl', [claude_line('итог2')], 'ab')
        self.update()
        self.assertEqual(self.kinds_of('agent-a1'), ['task', 'sub', 'report'])
        self.assertEqual(self.keys_of('agent-a1')[:2], before)  # report and sub are one class: the key does not move
        self.update(rebuild=True)
        self.assertEqual(self.kinds_of('agent-a1'), ['task', 'sub', 'report'])

    def test_rebuild_keeps_the_vectors(self):  # stage 2 §6: a rebuild used to drop them, 5.5 hours of embedding on the i3
        self.write('P/S.jsonl', [owner_line('ёжик'), claude_line('ответ')])
        self.update()
        self.conn.executemany("INSERT INTO vecs SELECT rowid, 0, conv, X'01020304' FROM chunks WHERE rowid = ?", [(r,) for (r,) in self.conn.execute('SELECT rowid FROM chunks')])
        vecs = self.conn.execute('SELECT * FROM vecs ORDER BY chunk').fetchall()
        s = self.update(rebuild=True)
        self.assertEqual((s['files'], s['rows'], self.rows_of('S')), (1, 0, 2))
        self.assertEqual(self.conn.execute('SELECT * FROM vecs ORDER BY chunk').fetchall(), vecs)

    def test_rebuild_replaces_a_text_the_filter_now_hides(self):  # stage 2 §5.7.2: a fix of redact.py reaches the index and the batches
        self.write('P/S.jsonl', [owner_line('ключ zq-0123456789 и всё'), claude_line('ответ')])
        self.update()
        (rid, old), = self.conn.execute("SELECT rowid, text FROM chunks WHERE kind = 'owner'").fetchall()
        self.conn.execute("INSERT INTO vecs VALUES(?, 0, 'S', X'01')", (rid,))
        self.conn.execute('UPDATE keys SET pushed = 1, vec_pushed = 1')
        with mock.patch.object(memdb, 'redact', lambda t: t.replace('zq-0123456789', '[скрыто]')):
            s = self.update(rebuild=True)
        self.assertEqual((s['rows'], self.rows_of('S')), (1, 2))  # replaced in place, not added
        self.assertEqual(self.conn.execute('SELECT text FROM chunks WHERE rowid = ?', (rid,)).fetchone(), ('ключ [скрыто] и всё',))
        self.assertEqual(search(self.conn, 'zq'), [])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM vecs WHERE chunk = ?', (rid,)).fetchone(), (0,))  # to be embedded again
        self.assertEqual(self.conn.execute('SELECT hash, pushed, vec_pushed FROM keys WHERE id = ?', (rid,)).fetchone(), (memdb.text_hash('ключ [скрыто] и всё'), 0, 0))
        gone = ['S', 'owner', memdb.text_hash(old), 0]
        self.assertEqual(self.conn.execute('SELECT * FROM forgotten').fetchall(), [tuple(gone)])
        self.assertEqual([json.loads(l) for (l,) in self.conn.execute('SELECT line FROM outbox')], [{'type': 'forget', 'key': gone}])
        self.assertEqual(self.update(rebuild=True)['rows'], 0)  # once done, done
        self.assertEqual(self.rows_of('S'), 2)

    def test_forget_deletes_here_for_good_and_tells_the_others(self):
        self.write('P/S.jsonl', [owner_line('секрет 42'), claude_line('ответ')])
        self.update()
        pos = self.conn.execute("SELECT pos FROM chunks WHERE kind = 'owner'").fetchone()[0]
        self.conn.execute("INSERT INTO vecs SELECT rowid, 0, conv, X'01' FROM chunks")
        self.assertEqual(memdb.forget(self.conn, 'S', pos), 1)
        self.assertEqual((self.kinds_of('S'), search(self.conn, 'секрет')), (['claude'], []))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM vecs').fetchone(), (1,))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM outbox').fetchone(), (1,))
        self.update(rebuild=True)  # the transcript still holds it: it does not come back
        self.write('P/S.jsonl', [owner_line('секрет 42')])  # nor when the file is rewritten
        self.update()
        self.assertEqual(self.kinds_of('S'), ['claude'])
        self.assertEqual(memdb.forget(self.conn, 'S', pos), 0)
        self.assertFalse(self.conn.in_transaction)

    def test_stage_1_index_gets_keys_and_home_projects_once(self):
        path = os.path.join(os.path.dirname(self.db), 'old.db')
        old = sqlite3.connect(path)
        old.executescript(memdb._SCHEMA.split('-- what a record is')[0])  # the tables of stage 1
        old.executemany('INSERT INTO chunks(text, conv, pos, ts, kind) VALUES(?, ?, ?, ?, ?)',
                        [('да', 'S', 1, TS, 'owner'), ('да', 'S', 2, TS, 'owner'), ('итог', 'A', 1, TS, 'report')])
        old.execute("INSERT INTO vecs VALUES(1, 0, 'S', X'01')")
        old.executemany('INSERT INTO conv_projects VALUES(?, ?)', [('S', 'c:\\users\\aij\\proj'), ('A', 'c:\\_git\\x')])
        old.commit()
        old.close()
        conn = connect(path, home='C:\\Users\\AIJ')
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute('SELECT id, conv, cls, n, origin, pushed FROM keys ORDER BY id').fetchall(),
                         [(1, 'S', 'owner', 0, None, 0), (2, 'S', 'owner', 1, None, 0), (3, 'A', 'sub', 0, None, 0)])
        self.assertEqual(sorted(conn.execute('SELECT * FROM conv_projects')), [('A', 'c:\\_git\\x'), ('S', '~\\proj')])
        self.assertEqual(conn.execute('SELECT count(*) FROM vecs').fetchone(), (1,))
        conn.close()
        again = connect(path, home='C:\\Users\\other')  # once: a second home does not touch the keys again
        self.addCleanup(again.close)
        self.assertEqual(sorted(again.execute('SELECT * FROM conv_projects')), [('A', 'c:\\_git\\x'), ('S', '~\\proj')])

    def test_secret_not_searchable(self):
        self.write('P/S.jsonl', [owner_line('вот password=hunter22 для теста')])
        self.update()
        self.assertEqual(search(self.conn, 'hunter22'), [])
        self.assertIn('[скрыто]', search(self.conn, 'теста')[0]['snippet'])

    def test_project_scope_and_subagent_inherits(self):
        wt = 'C:\\a\\.claude\\worktrees\\w1\\src'  # a worktree of C:\a is the same project
        self.write('P1/S1.jsonl', [owner_line('ёжик в тумане', cwd=wt)])
        self.write('P1/S1/subagents/workflows/wf_1/agent-a1.jsonl', [owner_line('найди ёжик', cwd='')])  # no cwd: the project comes from the parent
        self.write('P2/S2.jsonl', [owner_line('ёжик в другом проекте', cwd='C:\\b')])
        self.update()
        hits = search(self.conn, 'ёжик', project=project_key('C:\\a'))
        self.assertEqual({h['conv'] for h in hits}, {'S1', 'agent-a1'})
        self.assertEqual({h['parent'] for h in hits}, {'', 'S1'})
        self.assertEqual(len(search(self.conn, 'ёжик')), 3)

    def test_hit_carries_the_chunk_rowid(self):
        self.write('P/S.jsonl', [owner_line('первое'), owner_line('второе ёжик')])
        self.update()
        hit = search(self.conn, 'ёжик')[0]
        self.assertEqual(self.conn.execute('SELECT pos, text FROM chunks WHERE rowid = ?', (hit['id'],)).fetchone(), (hit['pos'], 'второе ёжик'))

    def test_fts_syntax_never_raises(self):
        self.write('P/S.jsonl', [owner_line('C++ и foo bar')])
        self.update()
        for q in ['C++', '"', 'a AND', '-x', 'foo:bar', '*', 'NEAR(', '', '___']:
            search(self.conn, q)  # без исключения
        self.assertEqual(search(self.conn, ''), [])
        self.assertEqual(len(search(self.conn, 'foo zzz')), 1)  # all words missing -> any word

    def test_owner_ranks_above_subagent_chatter(self):
        filler = [owner_line('разговор %d про другое' % i) for i in range(6)]
        self.write('P/S.jsonl', filler + [owner_line('редкослово и ещё пара слов')])
        self.write('P/S/subagents/agent-a1.jsonl', [claude_line('редкослово'), claude_line('итог')])  # shorter, so it would win on bm25 alone; the last sub text is a report, not chatter
        self.update()
        self.assertEqual([h['kind'] for h in search(self.conn, 'редкослово')], ['owner', 'sub'])

    def test_budget_and_limit(self):
        self.write('P/S.jsonl', [owner_line('бюджет ' + 'слово ' * 200 + str(i), ts='2026-10-13T10:00:%02d.000Z' % i) for i in range(50)])  # one timestamp each: a same snippet at one time is a repeat
        self.update()
        hits = search(self.conn, 'бюджет')
        self.assertLessEqual(len(hits), 8)
        self.assertLessEqual(sum(len(h['snippet']) for h in hits), 7000)
        self.assertEqual(len(search(self.conn, 'бюджет', limit=3)), 3)
        small = search(self.conn, 'бюджет', budget=300)
        self.assertTrue(0 < len(small) < len(hits) and sum(len(h['snippet']) for h in small) <= 300)

    def test_the_mods_own_calls_are_not_hits(self):
        self.write('P/S.jsonl', [owner_line('проба кнопки'), tool_line('Bash', command='проба кнопки'), tool_line('mcp__cacheguard__recall', query='проба кнопки')])
        self.update()
        self.assertEqual(self.kinds_of('S'), ['owner', 'tool'])  # index time: the mod's own call gets no row
        # an index built before that holds such rows, and a person may quote one
        ins = "INSERT INTO chunks(text, conv, pos, ts, kind) VALUES('mcp__cacheguard__recall: проба кнопки', 'S', ?, ?, ?)"
        self.conn.execute(ins, (1, TS, 'tool'))
        self.conn.execute(ins, (2, TS, 'owner'))
        found = [self.conn.execute('SELECT kind, text FROM chunks WHERE rowid = ?', (h['id'],)).fetchone() for h in search(self.conn, 'проба кнопки')]
        self.assertEqual(sorted(found), [('owner', 'mcp__cacheguard__recall: проба кнопки'), ('owner', 'проба кнопки'), ('tool', 'Bash: проба кнопки')])

    def test_resumed_session_copy_is_one_hit(self):
        # Claude Code copies the history of a resumed session into the new transcript: the same message under two convs
        later = '2026-10-14T10:00:00.000Z'
        self.write('P/S1.jsonl', [owner_line('ёжик в тумане'), owner_line('второй ёжик', ts=later)])
        self.write('P/S2.jsonl', [owner_line('ёжик в тумане'), owner_line('второй ёжик', ts=later), owner_line('новый ёжик', ts=later)])
        self.update()
        hits = search(self.conn, 'ёжик')
        self.assertEqual(len(hits), 3)
        self.assertEqual(len({(h['kind'], h['ts'], h['snippet']) for h in hits}), 3)
        self.assertEqual(search(self.conn, 'ёжик', limit=3), hits)  # the limit counts kept hits: 5 rows match, 3 messages are shown
        self.assertEqual(search(self.conn, 'ёжик', budget=sum(len(h['snippet']) for h in hits)), hits)  # and so does the budget
        self.assertEqual(len(search(self.conn, 'ёжик', limit=2)), 2)

    def test_same_text_at_another_time_is_not_a_repeat(self):
        self.write('P/S1.jsonl', [owner_line('ёжик в тумане')])
        self.write('P/S2.jsonl', [owner_line('ёжик в тумане', ts='2026-10-14T10:00:00.000Z')])
        self.update()
        self.assertEqual(len(search(self.conn, 'ёжик')), 2)

    def test_last_subagent_text_is_report(self):
        self.write('P/S/subagents/agent-a1.jsonl', [owner_line('сделай'), claude_line('шаг1'), claude_line('итог1')])
        self.update()
        self.assertEqual(self.kinds_of('agent-a1'), ['task', 'sub', 'report'])
        self.write('P/S/subagents/agent-a1.jsonl', [claude_line('итог2')], 'ab')
        self.update()
        self.assertEqual(self.kinds_of('agent-a1'), ['task', 'sub', 'sub', 'report'])
        self.assertEqual(self.conn.execute("SELECT text FROM chunks WHERE kind = 'report'").fetchall(), [('итог2',)])

    def test_expand_and_owner_count(self):
        self.write('P/S.jsonl', [
            owner_line('раз', ts='2026-10-12T23:00:00.000Z'), claude_line('два'), owner_line('три'),
            claude_line('четыре'), owner_line('пять'), claude_line('x' * 3000)])
        self.update()
        pos = [r[0] for r in self.conn.execute('SELECT pos FROM chunks ORDER BY pos')]
        self.assertEqual([r['pos'] for r in expand(self.conn, 'S', pos[2], radius=1)], pos[1:4])
        self.assertEqual([r['pos'] for r in expand(self.conn, 'S', pos[0], radius=1)], pos[:2])
        self.assertEqual(expand(self.conn, 'S', 1), [])
        rows = expand(self.conn, 'S', pos[5], radius=5)
        self.assertEqual(len(rows[-1]['text']), 1500)
        rows = expand(self.conn, 'S', pos[5], radius=5, budget=1504)  # 1500 for the hit + 4 for 'пять': only the nearest neighbour fits
        self.assertEqual([r['pos'] for r in rows], [pos[4], pos[5]])
        self.assertEqual(owner_count(self.conn, '2026-10-13'), 2)
        self.assertEqual(owner_count(self.conn, '2026-10-12', '2026-10-13'), 1)

    def test_expand_same_rows_via_keys(self):
        self.write('P/S.jsonl', [owner_line('раз'), claude_line('два'), owner_line('три'), claude_line('четыре')])
        self.update()
        for i in (5, 1, 3):  # imported rows of the same conversation, out of order, one sharing a pos
            rid = self.conn.execute("INSERT INTO chunks(text, conv, pos, ts, kind) VALUES(?, 'S', ?, '2026-10-14T10:00:00.000Z', 'owner')",
                                    ('чужое%d' % i, 100 + i % 2)).lastrowid
            self.conn.execute("INSERT INTO keys(id, conv, cls, hash, n, origin) VALUES(?, 'S', 'owner', ?, 0, 'A')", (rid, 'h%d' % i))
        old = self.conn.execute('SELECT pos, ts, kind, substr(text, 1, 1500) FROM chunks WHERE conv = ? ORDER BY pos, rowid', ('S',)).fetchall()
        pos = old[len(old) // 2][0]
        want = [dict(zip(('pos', 'ts', 'kind', 'text'), r)) for r in old]
        self.assertEqual(expand(self.conn, 'S', pos, radius=50), want)

    def test_owner_count_skips_other_computers(self):
        self.write('P/S.jsonl', [owner_line('раз'), owner_line('два'), owner_line('три')])
        self.update()
        for i in range(3):  # imported from computer A
            self.add_imported('A', i)
        self.assertEqual(owner_count(self.conn, '2026-10-01', machine='B'), 3)
        self.add_imported('B', 9)  # taken back from our own folder: ours too
        self.assertEqual(owner_count(self.conn, '2026-10-01', machine='B'), 4)

    def add_imported(self, origin, i):
        rid = self.conn.execute("INSERT INTO chunks(text, conv, pos, ts, kind) VALUES(?, 'X', ?, '2026-10-14T10:00:00.000Z', 'owner')",
                                ('чужое%d' % i, 100 + i)).lastrowid
        self.conn.execute('INSERT INTO keys(id, conv, cls, hash, n, origin) VALUES(?, ?, ?, ?, 0, ?)', (rid, 'X', 'owner', 'h%d' % i, origin))


if __name__ == '__main__':
    unittest.main()
