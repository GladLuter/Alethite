import json
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
import crypto
import indexdir
from indexdir import MARK, MIN_FREE, Where, resolve
from test_texts import use_lang
from texts import t

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
            t('index_fallback', dir='D:\\x', home='H'), t('index_old_copy', dir='C:\\old'), t('index_merge_failed', e='диск занят')]})
        self.assertIn('C:\\old', out['notes'][1])
        self.assertIn('диск занят', out['notes'][2])

    def test_the_notes_of_the_index_itself_show_without_a_where(self):  # a run with --db has none
        self.meta(old_copy='C:\\old')
        self.assertEqual(indexdir.status(self.conn, None)['notes'], [t('index_old_copy', dir='C:\\old')])


if __name__ == '__main__':
    unittest.main()
