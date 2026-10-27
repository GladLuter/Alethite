import contextlib
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
import crypto
import encrypt
import indexdir
import lessons
import memdb
import sync as cgsync
import texts
from indexdir import MARK, MIN_FREE, Where, absorb, merge_back, resolve
from test_encrypt import FakeUI
from test_memdb import claude_line, owner_line
from test_sync import Computer
from test_texts import use_lang
from test_vectors import fake
from texts import t
from vectors import DIMS, MODEL, embed_pending, pack

GB = 2 ** 30


class ResolveTest(unittest.TestCase):
    def setUp(self):
        use_lang(self, 'ru')
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.home = os.path.join(d.name, 'home')  # the mod's folder: the settings, and the C: index
        self.far = os.path.join(d.name, 'far')  # what indexDir names
        os.makedirs(self.home)
        self.db = os.path.join(self.home, 'index.db')
        self.far_db = os.path.join(self.far, 'index.db')
        self.mark = os.path.join(self.home, MARK)
        self.asked = []

    def free(self, gb=10):
        def free(path):
            self.asked.append(path)
            return int(gb * GB)
        return free

    def settings(self, raw=None, **fields):
        with open(os.path.join(self.home, 'settings.json'), 'w', encoding='utf-8-sig') as f:  # a BOM, as PowerShell writes it
            f.write(raw if raw is not None else json.dumps(fields))

    def touch(self, path, text=''):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)

    def where(self, gb=10):
        return resolve(self.home, self.free(gb))

    def test_no_settings_or_no_indexdir_is_the_standard_folder(self):
        for fields in (None, {}, {'language': 'ru'}, {'indexDir': None}):
            if fields is not None:
                self.settings(**fields)
            self.assertEqual(self.where(), Where(self.db, None, False, None))
        self.assertEqual(os.listdir(self.home), ['settings.json'])  # not even the index is made: that is memdb's
        self.assertEqual(self.asked, [])

    def test_indexdir_that_is_the_standard_folder_in_any_spelling(self):
        for spelling in (self.home.upper(), self.home.replace('\\', '/') + '/', os.path.join(self.home, '..', 'home'), self.home + os.sep):
            self.settings(indexDir=spelling)
            self.assertEqual(self.where(), Where(self.db, None, False, None), spelling)
        self.assertFalse(os.path.exists(self.mark))

    def test_an_empty_indexdir_is_not_set(self):  # a hand-emptied value means no folder: no fallback, no mark, no notice
        for value in ('', '   ', ' \t'):
            self.settings(indexDir=value)
            self.assertEqual(self.where(), Where(self.db, None, False, None), repr(value))
        self.assertEqual(os.listdir(self.home), ['settings.json'])
        self.assertEqual(self.asked, [])

    def test_a_folder_with_the_index_is_where_it_is_and_nothing_is_written(self):
        self.touch(self.far_db)
        self.settings(indexDir=self.far)
        self.assertEqual(self.where(), Where(self.far_db, self.far, False, None))
        self.assertEqual(sorted(os.listdir(self.home)), ['settings.json'])
        self.assertEqual(self.asked, [])

    def test_the_standard_index_is_to_merge_only_with_the_mark(self):
        self.touch(self.far_db)
        self.settings(indexDir=self.far)
        self.touch(self.db)  # an old copy of the move: no mark, nobody touches it
        self.assertEqual(self.where().merge_from, None)
        self.touch(self.mark, self.far)
        self.assertEqual(self.where(), Where(self.far_db, self.far, False, self.db))
        os.remove(self.db)  # a mark with no index beside it: nothing to merge
        self.assertEqual(self.where().merge_from, None)

    def test_no_folder_no_file_or_a_bad_value_is_work_on_the_standard_folder(self):
        os.makedirs(self.far)  # the folder is there and empty: the index is not
        for value, shown in ((self.far, self.far), (self.far + 'x', self.far + 'x'), ('rel\\x', 'rel\\x'), (5, '5')):
            if os.path.exists(self.mark):
                os.remove(self.mark)
            self.settings(indexDir=value)
            self.assertEqual(self.where(), Where(self.db, shown, True, None), value)
            with open(self.mark, encoding='utf-8') as f:
                self.assertEqual(f.read(), shown)
        self.assertFalse(os.path.exists(self.far + 'x'))  # nothing is made for the folder

    def test_the_mark_is_never_written_over(self):
        self.settings(indexDir=self.far)
        self.touch(self.mark, 'D:\\older')
        self.assertTrue(self.where().fallback)
        with open(self.mark, encoding='utf-8') as f:
            self.assertEqual(f.read(), 'D:\\older')

    def test_an_old_copy_on_the_standard_folder_is_used_whatever_the_free_space(self):
        self.settings(indexDir=self.far)
        self.touch(self.db)
        self.assertEqual(self.where(0), Where(self.db, self.far, True, None))
        self.assertTrue(os.path.exists(self.mark))
        self.assertEqual(self.asked, [])  # nothing new is made, so nothing is measured

    def test_a_new_index_on_the_standard_folder_needs_2_gb(self):
        self.settings(indexDir=self.far)
        self.assertEqual(MIN_FREE, 2 * GB)
        self.assertEqual(self.where(2), Where(self.db, self.far, True, None))  # exactly 2 GB is enough
        os.remove(self.mark)
        before = sorted(os.listdir(self.home))
        with self.assertRaises(ValueError) as e:
            self.where(1.54)
        self.assertEqual(str(e.exception), t('index_fallback_no_space', dir=self.far, home=self.home, free=1.5))
        self.assertIn('1.5', str(e.exception))
        self.assertEqual(sorted(os.listdir(self.home)), before)  # no mark, no new file
        self.assertFalse(os.path.exists(self.far))
        self.assertEqual(self.asked, [self.home, self.home])

    def test_settings_that_cannot_be_read_leave_the_standard_folder_when_it_has_an_index(self):
        path = os.path.join(self.home, 'settings.json')
        self.touch(self.db)
        for raw in ('{ "indexDir": ', '[1]', 'not json'):
            self.settings(raw=raw)
            self.assertEqual(self.where(), Where(self.db, None, False, None), raw)
        os.remove(path)
        os.mkdir(path)  # a folder, not a file: OSError
        self.assertEqual(self.where(), Where(self.db, None, False, None))
        self.assertFalse(os.path.exists(self.mark))
        self.assertEqual(self.asked, [])

    def test_settings_that_cannot_be_read_and_no_index_is_an_error_that_says_which_file(self):
        self.settings(raw='{ "indexDir": ')
        with self.assertRaises(ValueError) as e:
            self.where()
        self.assertIn(os.path.join(self.home, 'settings.json'), str(e.exception))
        self.assertIn(t('settings_broken', path=os.path.join(self.home, 'settings.json')), str(e.exception))
        self.assertEqual(sorted(os.listdir(self.home)), ['settings.json'])

    def test_defaults_are_the_home_of_the_mod_and_the_free_space_of_the_disk(self):
        self.settings(indexDir=self.far)
        with mock.patch.object(crypto, 'HOME_DIR', self.home), mock.patch('shutil.disk_usage', return_value=SimpleNamespace(free=MIN_FREE - 1)) as usage:
            with self.assertRaises(ValueError):
                resolve()
            usage.return_value = SimpleNamespace(free=MIN_FREE)
            self.assertEqual(resolve(), Where(self.db, self.far, True, None))
        usage.assert_called_with(self.home)


class StatusTest(unittest.TestCase):
    def setUp(self):
        use_lang(self, 'ru')
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        self.conn.execute('CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)')

    def meta(self, **kv):
        self.conn.executemany('INSERT INTO meta VALUES(?, ?)', kv.items())

    def test_nothing_to_say(self):
        quiet = {'configured': None, 'fallback': False, 'notes': []}
        self.assertEqual(indexdir.status(self.conn, None), quiet)
        self.assertEqual(indexdir.status(self.conn, Where('H\\index.db', None, False, None)), quiet)
        self.assertEqual(indexdir.status(self.conn, Where('D:\\x\\index.db', 'D:\\x', False, None)), dict(quiet, configured='D:\\x'))

    def test_work_on_the_standard_folder_an_old_copy_and_a_failed_merge_are_notes_in_this_order(self):
        self.meta(old_copy='C:\\old', merge_error='диск занят')
        out = indexdir.status(self.conn, Where('H\\index.db', 'D:\\x', True, None))
        self.assertEqual(out, {'configured': 'D:\\x', 'fallback': True, 'notes': [
            t('index_fallback', dir='D:\\x', home='H'), t('index_old_copy', dir='C:\\old'),
            t('index_merge_failed', dir='D:\\x', home='H', e='диск занят')]})
        self.assertIn('C:\\old', out['notes'][1])
        self.assertIn('диск занят', out['notes'][2])
        self.assertIn('H', out['notes'][2])

    def test_a_failed_merge_names_the_folder_of_the_temporary_index(self):  # db is in the index folder then, the temporary one is merge_from
        self.meta(merge_error='диск занят')
        out = indexdir.status(self.conn, Where('D:\\x\\index.db', 'D:\\x', False, 'H\\index.db'))
        self.assertEqual(out['notes'], [t('index_merge_failed', dir='D:\\x', home='H', e='диск занят')])

    def test_the_notes_of_the_index_itself_show_without_a_where(self):  # a run with --db has none
        self.meta(old_copy='C:\\old', merge_error='диск занят')  # a failed merge needs both folders to be told, and a run with --db knows neither
        self.assertEqual(indexdir.status(self.conn, None)['notes'], [t('index_old_copy', dir='C:\\old')])


HOME = 'C:\\Users\\x'  # the home the project keys are made against
REAL, OTHER = (pack(fake([w])[0]) for w in ('alpha', 'omega'))  # two vectors of the length an index keeps


def texts_of(conn):
    return sorted(r[0] for r in conn.execute('SELECT text FROM chunks'))


def key_of(conn, text):
    return conn.execute('SELECT k.conv, k.cls, k.hash, k.n FROM keys k JOIN chunks c ON c.rowid = k.id WHERE c.text = ?', (text,)).fetchone()


def put(conn, conv, text, kind='owner', pos=1, n=0, vec=None, **marks):
    """A record with its key, and a vector (bytes, b'' is the empty one of a record that failed to embed) when given; `marks`: origin, pushed, vec_pushed."""
    rid = conn.execute('INSERT INTO chunks(text, conv, pos, ts, kind) VALUES(?, ?, ?, ?, ?)', (text, conv, pos, '2026-10-13T10:00:00.000Z', kind)).lastrowid
    conn.execute('INSERT INTO keys(id, conv, cls, hash, n, origin, pushed, vec_pushed) VALUES(?, ?, ?, ?, ?, ?, ?, ?)',
                 (rid, conv, memdb.cls_of(kind), memdb.text_hash(text), n, marks.get('origin'), marks.get('pushed', 0), marks.get('vec_pushed', 0)))
    if vec is not None:
        conn.execute('INSERT INTO vecs VALUES(?, 0, ?, ?)', (rid, conv, vec))
    return rid


class MergeTest(unittest.TestCase):
    """The temporary index in the standard folder (home) and the index in its folder (far), made here from synthetic transcripts."""

    def setUp(self):
        use_lang(self, 'en')
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.addCleanup(self.close)  # first of the two to run: Windows will not remove a folder with an open file in it
        self.home, self.far, self.projects = (os.path.join(d.name, n) for n in ('home', 'far', 'projects'))
        self.src, self.db = os.path.join(self.home, 'index.db'), os.path.join(self.far, 'index.db')
        self.mark = os.path.join(self.home, MARK)
        self.where = Where(self.db, self.far, False, self.src)
        self.open = []
        os.makedirs(self.home)
        with open(self.mark, 'w', encoding='utf-8') as f:
            f.write(self.far)

    def close(self):
        for conn in self.open:
            conn.close()
        self.open.clear()

    def index(self, path, key=None):
        """The index at `path` (made when it is not there, encrypted with `key` as memdb makes it), open until `close`."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if key:
            conn = crypto.open_db(path, key, page=crypto.INDEX_PAGE)
            conn.executescript(memdb._SCHEMA)
        else:
            conn = memdb.connect(path)
        self.open.append(conn)
        return conn

    def say(self, conv, *texts, mode='wb'):
        """The transcript of `conv`: the texts are lines, the owner's and the answer's by turns; `mode` 'ab' adds lines to it."""
        path = os.path.join(self.projects, 'P', conv + '.jsonl')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = [(owner_line if i % 2 == 0 else claude_line)(text) for i, text in enumerate(texts)]
        with open(path, mode) as f:
            f.write(''.join(json.dumps(o, ensure_ascii=False) + '\n' for o in lines).encode('utf-8'))

    def read(self, conn):
        return memdb.update(conn, self.projects, home=HOME)

    def embed(self, conn):
        return embed_pending(conn, MODEL, fake)

    def merge(self, key=None):
        self.close()
        return merge_back(self.where, key)

    def free(self):
        """Every lock either index has can be taken: nothing is held."""
        for path in (self.db, self.src):
            for name in indexdir.LOCKS:
                memdb.lock(path, name, 0).close()

    def two(self, key=None, other=None):
        """Both indexes read the same two lines, the temporary one also reads two more (and has vectors for all); the pair is closed."""
        self.say('S1', 'alpha one', 'beta two')
        d = self.index(self.db, key)
        self.read(d)
        self.embed(d)
        c = self.index(self.src, other)
        self.read(c)
        self.say('S1', 'gamma three', 'delta four', mode='ab')
        self.read(c)
        self.embed(c)
        self.close()

    def test_what_the_temporary_index_has_comes_over_what_it_forgot_goes_and_it_is_deleted(self):
        self.say('S1', 'alpha one', 'beta two')
        d = self.index(self.db)
        self.read(d)
        self.embed(d)
        d.execute("INSERT INTO meta VALUES('merge_error', 'the disk was busy')")  # a try before this one failed
        c = self.index(self.src)
        self.read(c)  # the standard folder began empty and read every transcript again
        self.say('S1', 'gamma three', 'delta four', mode='ab')
        self.read(c)
        lesson = lessons.save(c, 'proj', {'action': 'did a', 'symptom': 'saw b', 'cause': 'because c'})['text']
        self.embed(c)
        key = key_of(c, 'beta two')
        self.assertEqual(memdb.forget(c, key[0], c.execute('SELECT pos FROM chunks WHERE text = ?', ('beta two',)).fetchone()[0]), 1)
        line = json.dumps({'type': 'forget', 'key': list(key)}, ensure_ascii=False)
        self.assertEqual(c.execute('SELECT line FROM outbox').fetchall(), [(line,)])
        self.assertEqual(self.merge(), t('index_merged', dir=self.far, home=self.home, n=3))
        d = self.index(self.db)
        self.assertEqual(texts_of(d), sorted(['alpha one', 'gamma three', 'delta four', lesson]))
        self.assertEqual(d.execute('SELECT count(*) FROM chunks c WHERE NOT EXISTS (SELECT 1 FROM vecs v WHERE v.chunk = c.rowid AND length(v.vec) = ?)',
                                   (4 * DIMS,)).fetchone(), (0,))  # every record has its vector, the new ones from the temporary index
        self.assertEqual(d.execute('SELECT count(*) FROM vecs').fetchone(), (4,))  # and none is left of the forgotten one
        self.assertEqual(d.execute('SELECT conv, cls, hash, n FROM forgotten').fetchall(), [key])
        self.assertEqual(d.execute('SELECT line FROM outbox').fetchall(), [(line,)])  # for the other computers
        self.assertIsNone(memdb.meta(d, 'merge_error'))
        self.assertEqual([os.path.exists(p) for p in (self.src, self.src + '-journal', self.mark)], [False] * 3)
        self.free()

    def test_a_record_forgotten_here_after_the_copy_was_taken_does_not_come_back(self):
        self.say('S1', 'alpha one', 'beta two')
        d, c = self.index(self.db), self.index(self.src)
        for conn in (d, c):
            self.read(conn)
            self.embed(conn)
        key = key_of(d, 'beta two')
        self.assertEqual(memdb.forget(d, key[0], d.execute('SELECT pos FROM chunks WHERE text = ?', ('beta two',)).fetchone()[0]), 1)
        line = d.execute('SELECT line FROM outbox').fetchone()
        self.assertEqual(self.merge(), t('index_merged', dir=self.far, home=self.home, n=0))
        d = self.index(self.db)
        self.assertEqual(texts_of(d), ['alpha one'])
        self.assertEqual(d.execute('SELECT chunk FROM vecs').fetchall(), [(d.execute('SELECT rowid FROM chunks').fetchone()[0],)])
        self.assertEqual(d.execute('SELECT conv, cls, hash, n FROM forgotten').fetchall(), [key])
        self.assertEqual(d.execute('SELECT line FROM outbox').fetchall(), [line])  # its own line, once
        self.assertFalse(os.path.exists(self.src))

    def test_a_lock_that_is_taken_leaves_everything_as_it_was_and_none_is_kept(self):
        self.two()
        before = {}
        for p in (self.db, self.src):
            with open(p, 'rb') as f:
                before[p] = f.read()
        for path, name in ((self.src, 'embed'), (self.db, 'sync'), (self.db, 'maintain'), (self.src, 'sync')):
            held = memdb.lock(path, name, 0)
            self.assertIsNone(merge_back(self.where, None), (path, name))
            held.close()
            self.free()  # the ones the refused try took before it met the busy one are given back
        for p, data in before.items():
            with open(p, 'rb') as f:
                self.assertEqual(f.read(), data)
        self.assertTrue(os.path.exists(self.mark))
        self.assertEqual(self.merge(), t('index_merged', dir=self.far, home=self.home, n=2))  # and once nothing is taken it goes through

    def test_a_changed_stamp_keeps_the_temporary_index_and_the_next_run_merges_nothing_and_deletes_it(self):
        self.two()
        with mock.patch.object(encrypt, '_stamp', side_effect=[b'before', b'after']):  # a run that began earlier wrote to it meanwhile
            self.assertIsNone(merge_back(self.where, None))
        self.assertTrue(os.path.exists(self.src) and os.path.exists(self.mark))
        self.assertEqual(texts_of(self.index(self.db)), ['alpha one', 'beta two', 'delta four', 'gamma three'])  # what was there is merged already
        self.assertEqual(self.merge(), t('index_merged', dir=self.far, home=self.home, n=0))
        self.assertEqual([os.path.exists(p) for p in (self.src, self.mark)], [False, False])

    def test_the_read_marks_of_the_transcripts_come_over_so_the_next_update_adds_no_twin(self):
        self.two()
        self.assertEqual(self.merge(), t('index_merged', dir=self.far, home=self.home, n=2))
        d = self.index(self.db)
        self.say('S1', 'epsilon five', mode='ab')
        self.assertEqual(self.read(d)['rows'], 1)  # gamma and delta are not read again as new
        self.assertEqual(texts_of(d), ['alpha one', 'beta two', 'delta four', 'epsilon five', 'gamma three'])

    def test_the_handoffs_written_beside_the_temporary_index_go_to_the_folder_that_stays(self):
        self.two()
        old, new = (os.path.join(p, 'handoffs') for p in (self.home, self.far))
        os.makedirs(old)
        os.makedirs(new)
        for folder, name, text in ((old, 'S1.md', 'new'), (old, 'S2.md', 'taken'), (new, 'S2.md', 'kept'), (new, 'S3.md', 'there')):
            with open(os.path.join(folder, name), 'w') as f:
                f.write(text)
        self.assertEqual(self.merge(), t('index_merged', dir=self.far, home=self.home, n=2))
        self.assertEqual(sorted(os.listdir(new)), ['S1.md', 'S2.md', 'S3.md'])
        for name, text in (('S1.md', 'new'), ('S2.md', 'kept')):  # what D: has is not written over
            with open(os.path.join(new, name)) as f:
                self.assertEqual(f.read(), text)
        self.assertEqual(os.listdir(old), ['S2.md'])

    def check_pair(self, db_key, src_key):
        self.two(db_key, src_key)
        self.assertEqual(self.merge(db_key or src_key), t('index_merged', dir=self.far, home=self.home, n=2))
        d = self.index(self.db, db_key)
        self.assertEqual(texts_of(d), ['alpha one', 'beta two', 'delta four', 'gamma three'])
        self.assertEqual(d.execute('SELECT count(*) FROM vecs WHERE length(vec) = ?', (4 * DIMS,)).fetchone(), (4,))
        self.assertEqual(crypto.is_encrypted(self.db), bool(db_key))
        self.assertFalse(os.path.exists(self.src))

    def test_an_encrypted_pair_merges_with_its_key_and_its_16_kb_pages(self):
        key = secrets.token_bytes(32)
        self.check_pair(key, key)
        d = self.index(self.db, key)
        self.assertEqual(d.execute('PRAGMA cipher_page_size').fetchone(), ('16384',))
        with self.assertRaises(ValueError):
            crypto.open_db(self.db, key)  # the default page does not open it: the merge kept the pages it has

    def test_an_encrypted_index_takes_in_an_open_temporary_one(self):
        self.check_pair(secrets.token_bytes(32), None)

    def test_two_open_indexes_need_no_key(self):
        self.check_pair(None, None)

    def test_an_open_index_and_an_encrypted_temporary_one_is_a_failed_merge_that_changes_nothing(self):
        key = secrets.token_bytes(32)
        self.two(None, key)
        why = t('index_merge_mixed')
        self.assertEqual(self.merge(key), t('index_merge_failed', dir=self.far, home=self.home, e=why))
        d = self.index(self.db)
        self.assertEqual((memdb.meta(d, 'merge_error'), texts_of(d)), (why, ['alpha one', 'beta two']))
        self.assertTrue(all(os.path.exists(p) for p in (self.src, self.mark)))
        self.free()

    def test_the_failed_merge_text_names_both_folders_and_the_reason_in_every_language(self):
        for lang in texts.TABLES:
            with mock.patch.object(texts, 'lang', lang):
                text = t('index_merge_failed', dir='THE-DIR', home='THE-HOME', e='THE-WHY')
            self.assertTrue(all(word in text for word in ('THE-DIR', 'THE-HOME', 'THE-WHY')), lang)

    def test_an_error_is_told_and_kept_in_the_index_and_goes_when_a_try_succeeds(self):
        self.two()
        with mock.patch.object(indexdir, 'absorb', side_effect=OSError('the disk is gone')):
            self.assertEqual(merge_back(self.where, None), t('index_merge_failed', dir=self.far, home=self.home, e='the disk is gone'))
        self.assertEqual(memdb.meta(self.index(self.db), 'merge_error'), 'the disk is gone')
        self.assertTrue(os.path.exists(self.src))
        self.assertEqual(self.merge(), t('index_merged', dir=self.far, home=self.home, n=2))
        self.assertIsNone(memdb.meta(self.index(self.db), 'merge_error'))

    def test_a_temporary_index_that_cannot_be_deleted_is_a_failed_merge_and_the_next_run_deletes_it(self):
        self.two()
        with mock.patch.object(encrypt, '_remove', side_effect=PermissionError('in use')):
            self.assertEqual(merge_back(self.where, None), t('index_merge_failed', dir=self.far, home=self.home, e='in use'))
        self.assertEqual(memdb.meta(self.index(self.db), 'merge_error'), 'in use')
        self.assertEqual(self.merge(), t('index_merged', dir=self.far, home=self.home, n=0))
        self.assertIsNone(memdb.meta(self.index(self.db), 'merge_error'))

    def test_a_mark_that_cannot_be_deleted_is_no_failed_merge_once_the_temporary_index_is_gone(self):  # a note that nothing would ever take away
        self.two()
        remove = os.remove

        def refuse_the_mark(path):
            if path == self.mark:
                raise PermissionError('in use')
            remove(path)

        with mock.patch('os.remove', refuse_the_mark):
            self.assertEqual(merge_back(self.where, None), t('index_merged', dir=self.far, home=self.home, n=2))
        self.assertEqual([os.path.exists(self.src), os.path.exists(self.mark)], [False, True])
        self.assertIsNone(memdb.meta(self.index(self.db), 'merge_error'))

    def test_a_missing_or_wrong_key_is_a_failed_merge_that_changes_nothing(self):
        key = secrets.token_bytes(32)
        self.two(key, key)
        for wrong, why in ((None, 'key_missing'), (secrets.token_bytes(32), 'key_wrong')):
            self.assertEqual(merge_back(self.where, wrong), t('index_merge_failed', dir=self.far, home=self.home, e=t(why)))
            self.assertTrue(all(os.path.exists(p) for p in (self.src, self.mark)))
        self.assertEqual(self.merge(key), t('index_merged', dir=self.far, home=self.home, n=2))

    def test_the_note_of_an_old_copy_goes_with_the_temporary_index_that_was_that_copy(self):  # the move left it on C: and the owner said No
        self.two()
        d = self.index(self.db)
        d.execute("INSERT INTO meta VALUES('old_copy', ?)", (self.home.upper() + '\\',))  # the same folder, written another way
        self.assertEqual(self.merge(), t('index_merged', dir=self.far, home=self.home, n=2))
        self.assertIsNone(memdb.meta(self.index(self.db), 'old_copy'))

    def test_the_note_of_another_old_copy_stays(self):
        self.two()
        elsewhere = os.path.join(os.path.dirname(self.home), 'elsewhere')
        self.index(self.db).execute("INSERT INTO meta VALUES('old_copy', ?)", (elsewhere,))
        self.assertEqual(self.merge(), t('index_merged', dir=self.far, home=self.home, n=2))
        self.assertEqual(memdb.meta(self.index(self.db), 'old_copy'), elsewhere)


class AbsorbTest(unittest.TestCase):
    """absorb on indexes with hand-made rows: what each step does. `a` is the index in its folder, `b` the temporary one."""

    def setUp(self):
        use_lang(self, 'en')
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.db, self.src = (os.path.join(d.name, n, 'index.db') for n in ('far', 'home'))
        self.a, self.b = memdb.connect(self.db), memdb.connect(self.src)
        for conn in (self.a, self.b):
            self.addCleanup(conn.close)

    def absorb(self):
        return absorb(self.db, self.src, None)

    def model(self, a, b):
        for conn, name in ((self.a, a), (self.b, b)):
            if name:
                conn.execute("INSERT INTO meta VALUES('embed_model', ?)", (name,))

    def test_new_records_come_with_their_marks_and_vectors_and_a_second_run_adds_nothing(self):
        self.model('m1', 'm1')
        put(self.a, 'S', 'shared', vec=REAL)
        put(self.b, 'S', 'shared', vec=REAL)
        put(self.b, 'S', 'fresh', pos=5, vec=OTHER, origin='OTHER-PC', pushed=1, vec_pushed=1)
        put(self.b, 'S', 'unsent', pos=6)
        self.assertEqual(self.absorb(), {'records': 2, 'vectors': 1, 'forgets': 0})
        rows = self.a.execute('SELECT c.text, c.pos, c.kind, k.origin, k.pushed, k.vec_pushed FROM chunks c JOIN keys k ON k.id = c.rowid ORDER BY c.pos').fetchall()
        self.assertEqual(rows, [('shared', 1, 'owner', None, 0, 0), ('fresh', 5, 'owner', 'OTHER-PC', 1, 1), ('unsent', 6, 'owner', None, 0, 0)])
        self.assertEqual(self.a.execute('SELECT c.text, v.vec FROM vecs v JOIN chunks c ON c.rowid = v.chunk ORDER BY c.pos').fetchall(), [('shared', REAL), ('fresh', OTHER)])
        self.assertEqual(self.absorb(), {'records': 0, 'vectors': 0, 'forgets': 0})
        self.assertEqual(self.a.execute('SELECT count(*) FROM chunks').fetchone(), (3,))

    def test_vectors_of_another_model_stay_behind(self):
        self.model('m1', 'm2')
        put(self.b, 'S', 'fresh', vec=REAL)
        self.assertEqual(self.absorb(), {'records': 1, 'vectors': 0, 'forgets': 0})
        self.assertEqual((self.a.execute('SELECT count(*) FROM vecs').fetchone(), memdb.meta(self.a, 'embed_model')), ((0,), 'm1'))

    def test_an_index_with_no_model_takes_the_one_of_the_vectors_it_gets(self):
        self.model(None, 'm2')
        put(self.b, 'S', 'fresh', vec=REAL)
        self.assertEqual(self.absorb(), {'records': 1, 'vectors': 1, 'forgets': 0})
        self.assertEqual((self.a.execute('SELECT count(*) FROM vecs').fetchone(), memdb.meta(self.a, 'embed_model')), ((1,), 'm2'))

    def test_a_vector_the_index_lacks_comes_in_place_of_the_empty_one_and_a_real_one_stays(self):
        self.model('m1', 'm1')
        lacking = put(self.a, 'S', 'lacking', pos=1, vec=b'')  # it failed to embed here
        put(self.b, 'S', 'lacking', pos=1, vec=REAL, vec_pushed=1)
        kept = put(self.a, 'S', 'kept', pos=2, vec=OTHER)
        put(self.b, 'S', 'kept', pos=2, vec=REAL)
        self.assertEqual(self.absorb(), {'records': 0, 'vectors': 1, 'forgets': 0})
        self.assertEqual(self.a.execute('SELECT chunk, part, vec FROM vecs ORDER BY chunk').fetchall(), [(lacking, 0, REAL), (kept, 0, OTHER)])
        self.assertEqual(self.a.execute('SELECT vec_pushed FROM keys WHERE id = ?', (lacking,)).fetchone(), (1,))

    def test_a_forget_of_the_temporary_index_is_done_here_a_forget_here_stays_and_the_outbox_is_not_doubled(self):
        put(self.a, 'S', 'gone here', pos=1, vec=REAL)
        put(self.b, 'S', 'gone here', pos=1)
        put(self.a, 'S', 'gone there', pos=2)
        put(self.b, 'S', 'gone there', pos=2)  # forgotten here, and the temporary index still has it
        self.assertEqual((memdb.forget(self.b, 'S', 1), memdb.forget(self.a, 'S', 2)), (1, 1))
        self.assertEqual(self.absorb(), {'records': 0, 'vectors': 0, 'forgets': 1})
        count = lambda table: self.a.execute('SELECT count(*) FROM ' + table).fetchone()[0]
        self.assertEqual([count(n) for n in ('chunks', 'keys', 'vecs', 'forgotten', 'outbox')], [0, 0, 0, 2, 2])  # an own line and the temporary one's
        lines = sorted(self.a.execute('SELECT line FROM outbox').fetchall())
        self.assertEqual(self.absorb()['forgets'], 0)
        self.assertEqual(sorted(self.a.execute('SELECT line FROM outbox').fetchall()), lines)

    def test_a_report_of_the_temporary_index_replaces_the_one_here(self):
        put(self.a, 'AG', 'first', kind='sub', pos=1)
        put(self.a, 'AG', 'last', kind='report', pos=2)
        put(self.b, 'AG', 'first', kind='sub', pos=1)
        put(self.b, 'AG', 'last', kind='sub', pos=2)
        put(self.b, 'AG', 'newest', kind='report', pos=3)
        self.assertEqual(self.absorb()['records'], 1)
        self.assertEqual(self.a.execute('SELECT text, kind FROM chunks ORDER BY pos').fetchall(), [('first', 'sub'), ('last', 'sub'), ('newest', 'report')])

    def test_conversations_projects_memory_files_peers_and_read_marks_merge_the_way_sync_takes_them_in(self):
        a, b = self.a, self.b
        a.executemany('INSERT INTO convs VALUES(?, ?, ?, ?)', [('S', '', '2026-10-02', '2026-10-05')])
        b.executemany('INSERT INTO convs VALUES(?, ?, ?, ?)', [('S', '', '2026-10-01', '2026-10-04'), ('T', 'S', '2026-10-06', '2026-10-07')])
        a.execute("INSERT INTO conv_projects VALUES('S', '~p1')")
        b.executemany('INSERT INTO conv_projects VALUES(?, ?)', [('S', '~p1'), ('S', '~p2')])
        a.execute("INSERT INTO mem_files VALUES('proj', 'f.md', 'A', 'f.md', 'h1')")
        b.executemany('INSERT INTO mem_files VALUES(?, ?, ?, ?, ?)', [('proj', 'f.md', 'B', 'f.md', 'h2'), ('proj', 'g.md', 'B', 'g.md', 'h3')])
        a.executemany('INSERT INTO peers(machine, last_batch, gen) VALUES(?, ?, ?)', [('M1', 3, 0), ('M2', 1, 1), ('M3', 4, 0)])
        b.executemany('INSERT INTO peers(machine, last_batch, skipped, gen) VALUES(?, ?, ?, ?)', [('M1', 5, '[4]', 0), ('M2', 9, '[]', 0), ('M4', 2, '[]', 0)])
        a.executemany('INSERT INTO files VALUES(?, ?, ?)', [('p1', 100, 100), ('p2', 50, 50)])
        b.executemany('INSERT INTO files VALUES(?, ?, ?)', [('p1', 200, 190), ('p2', 40, 40), ('p3', 10, 10)])
        self.absorb()
        self.assertEqual(a.execute('SELECT * FROM convs ORDER BY conv').fetchall(), [('S', '', '2026-10-01', '2026-10-05'), ('T', 'S', '2026-10-06', '2026-10-07')])
        self.assertEqual(a.execute('SELECT * FROM conv_projects ORDER BY project').fetchall(), [('S', '~p1'), ('S', '~p2')])
        self.assertEqual(a.execute('SELECT project, name, source, hash FROM mem_files ORDER BY name').fetchall(), [('proj', 'f.md', 'A', 'h1'), ('proj', 'g.md', 'B', 'h3')])
        self.assertEqual(a.execute('SELECT machine, last_batch, skipped, gen FROM peers ORDER BY machine').fetchall(),
                         [('M1', 5, '[4]', 0), ('M2', 1, '[]', 1), ('M3', 4, '[]', 0), ('M4', 2, '[]', 0)])  # the one that has gone further, a new generation beats a long run
        self.assertEqual(a.execute('SELECT * FROM files ORDER BY path').fetchall(), [('p1', 200, 190), ('p2', 50, 50), ('p3', 10, 10)])

    def test_a_key_that_did_not_come_over_is_no_merge_at_all(self):
        put(self.b, 'S', 'good', pos=1)
        put(self.b, 'S', 'broken', pos=2)
        self.b.execute("DELETE FROM chunks WHERE text = 'broken'")  # a key with no record: it cannot come over
        with self.assertRaises(ValueError) as e:
            self.absorb()
        self.assertEqual(str(e.exception), t('index_merge_differs'))
        self.assertEqual([self.a.execute('SELECT count(*) FROM ' + n).fetchone()[0] for n in ('chunks', 'keys')], [0, 0])  # the good one is undone too


def counts(path, key=None):
    """The rows of the tables that matter, from a fresh open."""
    with contextlib.closing(indexdir._connect(path, key)) as conn:
        return encrypt._counts(conn)


@unittest.skipUnless(os.name == 'nt', 'the move is Windows only')
class MoveTest(unittest.TestCase):
    """indexdir.move on temporary folders. `home` stands for ~/.claude/cacheguard: crypto.HOME_DIR is patched, the real one is never read.
    The fakes answer the drive type and the free space, and record what they were asked."""

    def setUp(self):
        use_lang(self, 'en')
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = d.name
        self.target = os.path.join(d.name, 'new')
        self.kind, self.room, self.roots, self.asked = 3, 10 * GB, [], []  # DRIVE_FIXED, plenty of room
        env = mock.patch.dict(os.environ)  # the OneDrive of this computer is no business of the temp folder
        env.start()
        self.addCleanup(env.stop)
        for var in indexdir.CLOUD:
            os.environ.pop(var, None)
        self.make_home(os.path.join(d.name, 'home'))

    def make_home(self, home):
        self.home = home
        os.makedirs(home)
        self.settings, self.src, self.mark = (os.path.join(home, n) for n in ('settings.json', 'index.db', MARK))
        self.where = Where(self.src, None, False, None)
        for patch in (mock.patch.object(crypto, 'HOME_DIR', home), mock.patch.object(crypto, 'INDEX_DIRS', set())):
            patch.start()
            self.addCleanup(patch.stop)

    def drive(self, root):
        self.roots.append(root)
        return self.kind

    def free(self, path):
        self.asked.append(path)
        return self.room

    def move(self, target=None, ui=None, key=None, where=None, **kw):
        self.ui = ui or FakeUI()
        return indexdir.move(where or self.where, target or self.target, key, self.ui, drive_type=self.drive, free=self.free, **kw)

    def index(self, path, *texts, key=None):
        """An index at `path` with a record and a vector for each text, closed."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if key:
            conn = crypto.open_db(path, key, page=crypto.INDEX_PAGE)
            conn.executescript(memdb._SCHEMA)
            conn.execute("INSERT INTO meta VALUES('schema', '2')")  # as memdb.connect leaves it: opening it later migrates nothing
        else:
            conn = memdb.connect(path)
        with contextlib.closing(conn):
            for i, text in enumerate(texts):
                put(conn, 'S', text, pos=i + 1, vec=REAL)

    def write(self, path, text='x'):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)

    def tree(self, *folders):
        """{path: bytes} of every file under the folders: what 'nothing was changed' compares."""
        out = {}
        for folder in folders:
            for dp, _, names in os.walk(folder):
                for n in names:
                    with open(os.path.join(dp, n), 'rb') as f:
                        out[os.path.join(dp, n)] = f.read()
        return out

    def fields(self):
        with open(self.settings, encoding='utf-8') as f:
            return json.load(f)

    def full_home(self, key=None):
        """The index with two records, the backup of stage 2, a handoff, job.xml and a settings file with a language."""
        self.index(self.src, 'alpha', 'beta', key=key)
        self.index(os.path.join(self.home, 'index.before-stage2.db'), 'alpha', key=key)
        self.write(os.path.join(self.home, 'handoffs', 'S1.md'), 'handoff')
        self.write(os.path.join(self.home, 'job.xml'), '<Task/>')
        crypto.set_fields(self.settings, {'language': 'ru'})

    def new_db(self, folder=None):
        return os.path.join(folder or self.target, 'index.db')

    def old_copy(self, db):
        with contextlib.closing(memdb.connect(db)) as conn:
            return memdb.meta(conn, 'old_copy')

    def test_a_move_copies_checks_switches_the_setting_and_leaves_the_old_copy_when_the_answer_is_no(self):
        self.full_home()
        crypto.save_key(self.settings, secrets.token_bytes(32))  # a key is set, the index is open: the field must come through the move
        was = self.fields()
        want = {n: counts(os.path.join(self.home, n)) for n in ('index.db', 'index.before-stage2.db')}
        self.assertEqual(want['index.db']['chunks'], 2)
        out = self.move(ui=FakeUI([False]))
        self.assertEqual(out, {'action': 'index-dir', 'done': True, 'dir': self.target, 'notes': [t('index_old_kept', dir=self.home)]})
        for name, rows in want.items():
            self.assertEqual(counts(os.path.join(self.target, name)), rows)
        for name, text in (('handoffs\\S1.md', 'handoff'), ('job.xml', '<Task/>')):
            with open(os.path.join(self.target, name), encoding='utf-8') as f:
                self.assertEqual(f.read(), text)
        self.assertEqual(sorted(os.listdir(self.target)), ['handoffs', 'index.before-stage2.db', 'index.db', 'job.xml'])  # no .cg-move
        self.assertEqual(self.fields(), dict(was, indexDir=self.target))  # the key and the rest as they were
        self.assertEqual(self.ui.asked, [t('dlg_delete_old_index', dir=self.home)])
        self.assertEqual(self.roots, [os.path.splitdrive(self.target)[0] + '\\'])  # the root of the drive
        self.assertEqual(self.asked, [self.root])  # the target is not there yet: the nearest folder that is
        self.assertEqual(crypto.INDEX_DIRS, {crypto.norm(self.target)})
        self.assertTrue(os.path.exists(self.src))  # No: nothing of the old copy is touched
        self.assertEqual(self.old_copy(self.new_db()), self.home)
        with contextlib.closing(memdb.connect(self.new_db())) as conn:  # and /cg-health says where it lies
            self.assertEqual(indexdir.status(conn, Where(self.new_db(), self.target, False, None))['notes'], [t('index_old_copy', dir=self.home)])

    def test_yes_deletes_the_listed_files_of_the_old_copy_and_its_note_and_nothing_else(self):
        self.full_home()
        locks = [memdb.lock(self.src, name, 0) for name in indexdir.LOCKS]  # what cg.py holds meanwhile: given back before the old copy goes
        self.write(self.mark)
        self.write(os.path.join(self.home, 'notes.txt'))
        self.write(os.path.join(self.home, 'other', 'keep.txt'))
        out = self.move(locks=locks)
        self.assertEqual(out['notes'], [])  # no file was busy
        self.assertEqual(sorted(os.listdir(self.home)), ['notes.txt', 'other', 'settings.json'])
        self.assertEqual(self.fields(), {'language': 'ru', 'indexDir': self.target})
        self.assertIsNone(self.old_copy(self.new_db()))

    def test_the_same_folder_in_any_spelling_is_here_and_asks_about_the_old_copy_again(self):
        self.full_home()
        self.move(ui=FakeUI([False]))
        where = resolve(self.home, self.free)
        self.assertEqual(where, Where(self.new_db(), self.target, False, None))
        for spelling in (self.target.upper(), self.target.replace('\\', '/') + '/', self.target + '\\', os.path.join(self.target, '..', 'new')):
            out = self.move(spelling, where=where, ui=FakeUI([False]))
            self.assertEqual(out['notes'], [t('index_dir_here', dir=out['dir']), t('index_old_kept', dir=self.home)], spelling)
            self.assertEqual((out['done'], self.ui.asked), (True, [t('dlg_delete_old_index', dir=self.home)]), spelling)
        self.assertTrue(os.path.exists(self.src))
        out = self.move(where=where)  # and Yes deletes it
        self.assertEqual(out['notes'], [t('index_dir_here', dir=self.target)])
        self.assertEqual(os.listdir(self.home), ['settings.json'])
        self.assertIsNone(self.old_copy(self.new_db()))
        self.move(where=where)  # nothing is left to ask about
        self.assertEqual(self.ui.asked, [])

    def test_a_note_that_names_the_folder_in_use_or_no_full_path_deletes_nothing_and_only_goes(self):  # the index must never be its own old copy
        self.full_home()
        self.move()
        where = resolve(self.home, self.free)
        for value in (self.target.upper() + '\\', 'relative\\x'):
            with contextlib.closing(memdb.connect(self.new_db())) as conn:
                conn.execute("INSERT OR REPLACE INTO meta VALUES('old_copy', ?)", (value,))
            self.move(where=where)
            self.assertEqual(self.ui.asked, [], value)
            self.assertEqual(counts(self.new_db())['chunks'], 2)
            self.assertIsNone(self.old_copy(self.new_db()))

    def test_a_move_cut_after_the_rename_and_before_the_setting_is_finished_by_the_repeat(self):
        self.full_home()
        was = self.fields()
        with mock.patch.object(crypto, 'set_fields', side_effect=OSError('cut')), self.assertRaises(OSError):
            self.move()
        self.assertEqual(self.fields(), was)
        self.assertTrue(os.path.exists(self.new_db()))  # renamed already
        out = self.move(ui=FakeUI([False]))  # the same index in the target is the half-done copy of this move
        self.assertEqual(out['notes'], [t('index_old_kept', dir=self.home)])
        self.assertEqual(self.fields(), dict(was, indexDir=self.target))
        self.assertEqual(counts(self.new_db()), counts(self.src))
        self.assertEqual(sorted(os.listdir(self.target)), ['handoffs', 'index.before-stage2.db', 'index.db', 'job.xml'])

    def test_another_index_in_the_target_is_refused_and_nothing_changes(self):
        self.full_home()
        with open(self.src, 'rb') as f:
            mine = f.read()
        flipped = bytearray(mine)
        flipped[24] ^= 1  # the change counter of page 1: the same size, another first page
        target = self.new_db()
        for label, make in (
                ('another index', lambda: self.index(target, 'other', 'index', 'with', 'more')),
                ('the same size, another first page', lambda: self.write_bytes(target, bytes(flipped))),
                ('longer', lambda: self.write_bytes(target, mine + b'x')),
                ('only a backup', lambda: self.write_bytes(os.path.join(self.target, 'index.before-stage2.db'), b'other')),
                ('a folder', lambda: os.makedirs(target))):
            with self.subTest(label):
                shutil.rmtree(self.target, ignore_errors=True)
                make()
                before = self.tree(self.home, self.target)
                with self.assertRaises(ValueError) as e:
                    self.move()
                self.assertEqual(str(e.exception), t('index_dir_foreign', dir=self.target))
                self.assertEqual(self.tree(self.home, self.target), before)
                self.assertEqual((self.ui.asked, crypto.INDEX_DIRS, self.fields()), ([], set(), {'language': 'ru'}))
        shutil.rmtree(self.target, ignore_errors=True)
        os.remove(self.src)  # no index here and one there is not a half-done copy of anything
        os.remove(os.path.join(self.home, 'index.before-stage2.db'))
        self.index(target, 'other')
        with self.assertRaises(ValueError) as e:
            self.move()
        self.assertEqual(str(e.exception), t('index_dir_foreign', dir=self.target))

    def write_bytes(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)

    def test_a_refusal_comes_before_any_change(self):
        self.full_home()
        size = sum(os.path.getsize(os.path.join(self.home, n)) for n in ('index.db', 'index.before-stage2.db'))
        need = -(-(size + 200 * 2 ** 20) // 2 ** 20)  # the files and 200 MB to spare, in whole MB
        onedrive = lambda var: mock.patch.dict(os.environ, {var: os.path.join(self.root, 'OD')})
        od = lambda *parts: os.path.join(self.root, 'OD', *parts)
        cases = (
            ('not Windows', t('index_dir_windows_only'), self.target, lambda: mock.patch.object(encrypt, 'SUPPORTED', False)),
            ('a network drive', t('index_dir_not_local', dir=self.target), self.target, lambda: mock.patch.object(self, 'kind', 4)),
            ('a removable drive', t('index_dir_not_local', dir=self.target), self.target, lambda: mock.patch.object(self, 'kind', 2)),
            ('UNC', t('index_dir_not_local', dir='\\\\server\\share\\x'), '\\\\server\\share\\x', contextlib.nullcontext),
            ('UNC with slashes', t('index_dir_not_local', dir='\\\\server\\share\\y'), '//server/share/y', contextlib.nullcontext),
            ('not a full path', t('index_dir_not_local', dir=os.path.abspath('new')), 'new', contextlib.nullcontext),
            ('OneDrive', t('index_dir_cloud', dir=od('idx')), od('idx'), lambda: onedrive('OneDrive')),
            ('OneDrive itself', t('index_dir_cloud', dir=od()), od(), lambda: onedrive('OneDriveCommercial')),
            ('OneDrive of a person', t('index_dir_cloud', dir=od('a')), od('a'), lambda: onedrive('OneDriveConsumer')),
            ('no room', t('index_dir_no_space', dir=self.target, need=need, free=100), self.target, lambda: mock.patch.object(self, 'room', 100 * 2 ** 20)),
            ('an unencrypted copy beside', t('index_dir_plain_left', dir=self.home), self.target, self.plain_beside))
        for label, text, target, setup in cases:
            with self.subTest(label), setup():
                before = self.tree(self.home)
                with self.assertRaises(ValueError) as e:
                    self.move(target)
                self.assertEqual(str(e.exception), text)
                self.assertEqual(self.tree(self.home), before)
                self.assertFalse(os.path.exists(self.target))
                self.assertEqual((self.ui.asked, crypto.INDEX_DIRS), ([], set()))
        self.assertEqual(self.fields(), {'language': 'ru'})

    @contextlib.contextmanager
    def plain_beside(self):
        self.write(self.src + '.plain')
        try:
            yield
        finally:
            os.remove(self.src + '.plain')

    def test_a_folder_that_only_starts_like_the_onedrive_one_is_fine(self):
        self.full_home()
        with mock.patch.dict(os.environ, {'OneDrive': os.path.join(self.root, 'OD')}):
            out = self.move(os.path.join(self.root, 'OD2'))
        self.assertTrue(out['done'])

    def test_a_computer_without_an_index_gets_an_empty_one_in_the_target(self):
        crypto.set_fields(self.settings, {'language': 'ru'})
        out = self.move()
        self.assertEqual((out['done'], out['notes'], self.ui.asked, self.asked), (True, [], [], [self.root]))  # nothing to ask about, and the room is checked as for any
        self.assertEqual(os.listdir(self.target), ['index.db'])
        with contextlib.closing(memdb.connect(self.new_db())) as conn:
            self.assertEqual((texts_of(conn), memdb.meta(conn, 'old_copy')), ([], None))
        self.assertEqual(self.fields(), {'language': 'ru', 'indexDir': self.target})

    def test_that_empty_index_is_encrypted_when_a_key_is_set(self):  # the key is the home one, as the folder is registered first
        key = secrets.token_bytes(32)
        crypto.save_key(self.settings, key)
        self.move()
        self.assertTrue(crypto.is_encrypted(self.new_db()))
        crypto.open_db(self.new_db(), key, page=crypto.INDEX_PAGE).close()

    def test_back_to_the_standard_folder_takes_indexDir_out_and_keeps_the_rest(self):
        self.full_home()
        want = counts(self.src)
        self.move()  # Yes: the copy in the home folder goes
        out = self.move(self.home.replace('\\', '/') + '/', where=resolve(self.home, self.free), ui=FakeUI([False]))
        self.assertEqual((out['dir'], out['notes']), (self.home, [t('index_old_kept', dir=self.target)]))
        self.assertEqual(self.fields(), {'language': 'ru'})
        self.assertEqual(counts(self.src), want)
        self.assertEqual(self.old_copy(self.src), self.target)
        self.assertEqual(resolve(self.home, self.free), Where(self.src, None, False, None))

    def test_the_index_on_c_with_its_folder_gone_for_good_becomes_the_standard_one_and_nothing_is_copied(self):
        far = os.path.join(self.root, 'far')
        self.full_home()
        crypto.set_fields(self.settings, {'indexDir': far})
        self.write(self.mark, far)
        kept = {k: v for k, v in self.tree(self.home).items() if k not in (self.mark, self.settings)}
        out = self.move(self.home.upper(), where=Where(self.src, far, True, None))
        self.assertEqual((out['done'], out['notes'], self.ui.asked, self.asked), (True, [t('index_dir_permanent', dir=self.home.upper())], [], []))
        self.assertEqual(self.fields(), {'language': 'ru'})
        self.assertFalse(os.path.exists(self.mark))
        self.assertEqual({k: v for k, v in self.tree(self.home).items() if k != self.settings}, kept)  # not a byte else
        self.assertEqual(self.roots, [os.path.splitdrive(self.home.upper())[0] + '\\'])  # checked as any target is
        self.assertFalse(os.path.exists(far))

    def test_the_index_on_c_goes_to_its_old_folder_when_that_is_back_and_empty_as_to_any_other(self):
        far = os.path.join(self.root, 'far')
        os.makedirs(far)
        self.full_home()
        crypto.set_fields(self.settings, {'indexDir': far})
        self.write(self.mark, far)
        want = counts(self.src)
        out = self.move(far, where=Where(self.src, far, True, None))
        self.assertEqual((out['done'], out['notes']), (True, []))  # Yes: the copy on C: goes
        self.assertEqual(counts(self.new_db(far)), want)
        self.assertEqual(self.fields(), {'language': 'ru', 'indexDir': far})
        self.assertEqual(sorted(os.listdir(self.home)), ['settings.json'])  # the index, the mark and the rest of the temporary copy
        self.assertIsNone(self.old_copy(self.new_db(far)))

    def test_a_temporary_copy_that_is_kept_is_only_an_old_copy_with_no_mark(self):
        far = os.path.join(self.root, 'far')
        self.full_home()
        crypto.set_fields(self.settings, {'indexDir': far})
        self.write(self.mark, far)
        self.move(far, where=Where(self.src, far, True, None), ui=FakeUI([False]))
        self.assertTrue(os.path.exists(self.src))
        self.assertFalse(os.path.exists(self.mark))
        self.assertEqual(self.old_copy(self.new_db(far)), self.home)

    def test_a_source_that_changed_while_it_was_copied_is_busy_and_nothing_stays_behind(self):
        self.full_home()
        real, seen = encrypt._stamp, []

        def stamp(path):
            seen.append(path)
            return real(path) + (b'x' if seen.count(path) > 1 else b'')  # the second look at a source finds it changed

        before = self.tree(self.home)
        with mock.patch.object(encrypt, '_stamp', stamp), self.assertRaises(ValueError) as e:
            self.move()
        self.assertEqual(str(e.exception), t('index_dir_busy'))
        self.assertEqual(self.tree(self.home), before)
        self.assertEqual(sorted(os.listdir(self.target)), ['handoffs', 'job.xml'])  # no .cg-move, no index
        self.assertEqual((self.fields(), self.ui.asked, crypto.INDEX_DIRS), ({'language': 'ru'}, [], set()))
        self.assertTrue(self.move()['done'])  # and a try that finds it quiet goes through

    def test_an_encrypted_index_moves_and_opens_in_the_target_with_the_key_of_the_home_settings(self):
        key = secrets.token_bytes(32)
        self.full_home(key)
        crypto.save_key(self.settings, key)
        want = {n: counts(os.path.join(self.home, n), key) for n in ('index.db', 'index.before-stage2.db')}
        out = self.move(key=key, ui=FakeUI([False]))
        self.assertTrue(out['done'])
        for name, rows in want.items():
            path = os.path.join(self.target, name)
            self.assertTrue(crypto.is_encrypted(path))
            self.assertEqual(counts(path, key), rows)
            with contextlib.closing(crypto.open_db(path, key, page=crypto.INDEX_PAGE)) as conn:
                self.assertEqual(conn.execute('PRAGMA cipher_page_size').fetchone(), ('16384',))
        self.assertEqual(crypto.settings_path(self.new_db()), self.settings)  # the folder is registered: the key is looked for at home
        self.assertEqual(self.old_copy(self.new_db()), self.home)  # and the mod opens it its own way
        self.assertEqual(crypto.load_key(self.settings), key)

    def test_an_encrypted_index_with_no_key_is_refused_and_nothing_changes(self):
        key = secrets.token_bytes(32)
        self.full_home(key)
        before = self.tree(self.home)
        with self.assertRaises(ValueError) as e:
            self.move()
        self.assertEqual(str(e.exception), t('key_missing'))
        self.assertEqual(self.tree(self.home), before)
        self.assertFalse(os.path.exists(self.target))

    def test_a_target_inside_the_old_folder_is_not_hit_by_the_deletion(self):
        self.full_home()
        for name in ('notes.txt', 'other\\keep.txt'):
            self.write(os.path.join(self.home, name))
        inside = os.path.join(self.home, 'D-copy')
        self.move(inside)  # Yes
        self.assertEqual(sorted(os.listdir(self.home)), ['D-copy', 'notes.txt', 'other', 'settings.json'])
        self.assertEqual(counts(self.new_db(inside))['chunks'], 2)
        self.assertIsNone(self.old_copy(self.new_db(inside)))

    def test_a_target_inside_the_handoffs_of_the_old_folder_keeps_its_index(self):  # the handoff files go, the folder they are in is not walked
        self.full_home()
        inside = os.path.join(self.home, 'handoffs')
        self.move(inside)
        self.assertEqual(counts(self.new_db(inside))['chunks'], 2)
        self.assertFalse(os.path.exists(os.path.join(inside, 'S1.md')))
        with open(os.path.join(inside, 'handoffs', 'S1.md'), encoding='utf-8') as f:  # its copy beside the new index
            self.assertEqual(f.read(), 'handoff')

    def test_the_old_folder_inside_the_target_loses_its_files_and_the_target_keeps_all_else(self):
        outer = os.path.join(self.root, 'outer')
        self.make_home(os.path.join(outer, 'home'))
        self.full_home()
        self.write(os.path.join(outer, 'mine.txt'))
        self.move(outer)
        self.assertEqual(sorted(os.listdir(outer)), ['handoffs', 'home', 'index.before-stage2.db', 'index.db', 'job.xml', 'mine.txt'])
        self.assertEqual(os.listdir(self.home), ['settings.json'])
        self.assertEqual(counts(self.new_db(outer))['chunks'], 2)

    def test_a_file_of_the_old_copy_in_use_stays_with_a_note_and_the_repeat_deletes_the_rest(self):
        self.full_home()
        job = os.path.join(self.home, 'job.xml')
        with open(job, encoding='utf-8'):  # a program that holds it: Windows will not delete it
            out = self.move()
            self.assertEqual(out['notes'], [t('index_old_busy', dir=self.home)])
            self.assertEqual(sorted(os.listdir(self.home)), ['job.xml', 'settings.json'])
            self.assertEqual(self.old_copy(self.new_db()), self.home)  # the note stays while a file does
        out = self.move(where=resolve(self.home, self.free))
        self.assertEqual(out['notes'], [t('index_dir_here', dir=self.target)])
        self.assertEqual(os.listdir(self.home), ['settings.json'])
        self.assertIsNone(self.old_copy(self.new_db()))

    def test_the_locks_are_given_back_after_the_switch_and_on_a_refusal(self):
        self.full_home()
        take = lambda: [memdb.lock(self.src, name, 0) for name in indexdir.LOCKS]
        self.kind = 4
        with self.assertRaises(ValueError):
            self.move(locks=take())
        for lock in take():  # none is kept: all three can be taken again
            lock.close()
        self.kind = 3
        locks = take()
        self.move(locks=locks)
        for lock in locks:  # closed, so the lock files could be deleted
            with self.assertRaises(sqlite3.ProgrammingError):
                lock.execute('SELECT 1')
        self.assertFalse(any(os.path.exists('%s.%s.lock' % (self.src, name)) for name in indexdir.LOCKS))

    def test_the_locks_are_given_back_when_the_copy_fails_too(self):
        self.full_home()
        locks = [memdb.lock(self.src, name, 0) for name in indexdir.LOCKS]
        with mock.patch.object(crypto, 'set_fields', side_effect=OSError('cut')), self.assertRaises(OSError):
            self.move(locks=locks)
        for lock in locks:
            with self.assertRaises(sqlite3.ProgrammingError):
                lock.execute('SELECT 1')

    def test_the_settings_are_read_before_anything_is_copied(self):
        self.full_home()
        with open(self.settings, 'w') as f:
            f.write('{ "indexDir": ')
        with self.assertRaises(crypto.SettingsBroken):
            self.move()
        self.assertFalse(os.path.exists(self.target))


class OwnBatchTest(unittest.TestCase):
    """The batches the temporary index put out (as the same computer) are the next ones of the index in its folder: no error, no twin."""

    def test_the_index_in_its_folder_goes_on_with_the_batch_after_the_temporary_ones(self):
        use_lang(self, 'en')
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root, share = d.name, os.path.join(d.name, 'share')
        os.makedirs(share)
        digest = lambda m: None
        c = Computer(root, share, 'AIJ-HP')  # the standard folder: its index is the temporary one
        self.addCleanup(c.close)
        far = os.path.join(root, 'far', 'index.db')
        c.say('conv-a', 'first question', 'first answer')
        d1 = memdb.connect(far, home=c.home)  # the index in its folder, before the disk went away: it put out batch 1
        memdb.update(d1, c.projects, home=c.home)
        self.assertEqual(cgsync.sync(d1, share, c.name, MODEL, 60, None, c.cwd, c.home, digest=digest)['own_batch'], 1)
        d1.close()
        c.say('conv-a', 'second question', 'second answer', mode='ab')  # the disk is away: the standard folder reads, takes batch 1 as its own, puts out batch 2
        c.index()
        out = c.sync(memory=False)
        self.assertEqual((out['errors'], out['pushed'], out['own_batch']), ([], 2, 2))
        c.close()
        with open(os.path.join(os.path.dirname(c.db), MARK), 'w') as f:
            f.write(os.path.dirname(far))
        self.assertEqual(merge_back(Where(far, os.path.dirname(far), False, c.db), None),
                         t('index_merged', dir=os.path.dirname(far), home=os.path.dirname(c.db), n=2))
        d2 = memdb.connect(far, home=c.home)
        self.addCleanup(d2.close)
        self.assertEqual(d2.execute('SELECT last_batch FROM peers WHERE machine = ?', (c.name,)).fetchone(), (2,))
        c.say('conv-a', 'third question', 'third answer', mode='ab')
        self.assertEqual(memdb.update(d2, c.projects, home=c.home)['rows'], 2)  # the new lines only
        out = cgsync.sync(d2, share, c.name, MODEL, 60, None, c.cwd, c.home, digest=digest)
        self.assertEqual((out['errors'], out['pushed'], out['pulled'], out['own_batch']), ([], 2, 0, 3))
        rows = d2.execute('SELECT text FROM chunks').fetchall()
        self.assertEqual((len(rows), len(set(rows))), (6, 6))
        b = Computer(root, share, 'MTLAPTOP')  # another computer reads all three batches: no record twice
        self.addCleanup(b.close)
        self.assertEqual(b.sync(memory=False)['errors'], [])
        self.assertEqual(b.texts(), sorted(r[0] for r in rows))


if __name__ == '__main__':
    unittest.main()
