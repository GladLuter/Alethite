import base64
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import crypto
from texts import t

KEY = bytes(range(32))
dpapi = unittest.skipUnless(os.name == 'nt', 'DPAPI is Windows only')


def bad(test, fn, text, *args):
    """fn(*args) raises a ValueError whose message is exactly `text`: the key or the code never rides along in it."""
    with test.assertRaises(ValueError) as cm:
        fn(*args)
    test.assertEqual(str(cm.exception), text)


class CodeTest(unittest.TestCase):
    def test_code_roundtrip(self):
        code = crypto.make_code(KEY)
        self.assertTrue(re.fullmatch(r'CG1(-[A-Z2-7]{4}){14}', code))
        self.assertEqual(crypto.parse_code(code), KEY)
        self.assertEqual(crypto.parse_code(code.lower().replace('-', ' ')), KEY)

    def test_code_typo(self):
        code = crypto.make_code(KEY)
        typo = code[:4] + ('B' if code[4] == 'A' else 'A') + code[5:]
        bad(self, crypto.parse_code, t('code_bad'), typo)
        bad(self, crypto.parse_code, t('code_bad'), code + '-AAAA')
        bad(self, crypto.parse_code, t('code_bad'), code[:-5])
        bad(self, crypto.parse_code, t('code_bad'), 'XX' + code[2:])
        bad(self, crypto.parse_code, t('code_bad'), code.replace('-', '-0', 1))

    def test_fingerprint_and_opaque(self):
        other = bytes(32)
        self.assertEqual(crypto.fingerprint(KEY), crypto.fingerprint(KEY))
        self.assertEqual(len(crypto.fingerprint(KEY)), 16)
        self.assertNotEqual(crypto.fingerprint(KEY), crypto.fingerprint(other))
        self.assertEqual(crypto.opaque(KEY, 'a'), crypto.opaque(KEY, 'a'))
        self.assertEqual(len(crypto.opaque(KEY, 'a')), 32)
        self.assertNotEqual(crypto.opaque(KEY, 'a'), crypto.opaque(KEY, 'b'))
        self.assertNotEqual(crypto.opaque(KEY, 'a'), crypto.opaque(other, 'a'))


class SettingsTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = os.path.join(d.name, 'settings.json')

    def write(self, text):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(text)

    def raw(self):
        with open(self.path, 'rb') as f:
            return f.read()

    def test_settings_path_is_beside_the_db(self):
        self.assertEqual(crypto.settings_path(os.path.join('x', 'index.db')), os.path.join('x', 'settings.json'))

    @dpapi
    def test_dpapi_roundtrip(self):
        blob = crypto.protect(KEY)
        self.assertEqual(crypto.unprotect(blob), KEY)
        self.assertNotIn(KEY, blob)
        with self.assertRaises(OSError):
            crypto.unprotect(b'junk')

    @dpapi
    def test_settings_key_keeps_the_rest(self):
        self.write('\ufeff{"language": "ru", "syncDir": "Z:\\\\cg"}')
        crypto.save_key(self.path, KEY)
        self.assertEqual(crypto.load_key(self.path), KEY)
        with open(self.path, encoding='utf-8-sig') as f:
            s = json.load(f)
        self.assertEqual(list(s), ['language', 'syncDir', crypto.KEY_FIELD])
        self.assertEqual((s['language'], s['syncDir']), ('ru', 'Z:\\cg'))
        text = self.raw().decode('utf-8')
        self.assertFalse(self.raw().startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(KEY.hex(), text)
        self.assertNotIn(base64.b64encode(KEY).decode(), text)
        self.assertFalse(os.path.exists(self.path + '.cg-tmp'))
        crypto.save_key(self.path, None)
        self.assertIsNone(crypto.load_key(self.path))
        with open(self.path, encoding='utf-8-sig') as f:
            self.assertEqual(json.load(f), {'language': 'ru', 'syncDir': 'Z:\\cg'})

    @dpapi
    def test_settings_created_with_the_key_only(self):
        crypto.save_key(self.path, KEY)
        with open(self.path, encoding='utf-8-sig') as f:
            self.assertEqual(list(json.load(f)), [crypto.KEY_FIELD])

    def test_settings_broken_untouched(self):
        for text in ['{"language": "ru",', '[1]']:
            self.write(text)
            bad(self, crypto.save_key, t('settings_broken', path=self.path), self.path, KEY)
            bad(self, crypto.load_key, t('settings_broken', path=self.path), self.path)
            self.assertEqual(self.raw(), text.encode('utf-8'))
            self.assertFalse(os.path.exists(self.path + '.cg-tmp'))

    @dpapi
    def test_key_unreadable(self):
        junk = 'dpapi:' + base64.b64encode(b'junk').decode()
        short = 'dpapi:' + base64.b64encode(crypto.protect(b'x' * 16)).decode()
        for field in [junk, short, 'plain', 5]:
            self.write(json.dumps({crypto.KEY_FIELD: field}))
            bad(self, crypto.load_key, t('key_unreadable'), self.path)

    def test_missing_settings(self):
        self.assertIsNone(crypto.load_key(self.path))
        self.write('{}')
        self.assertIsNone(crypto.load_key(self.path))


LINES = ['{"a":"секрет-маркер"}', 'x' * 10000]
AAD = 'A/1/batch-00000001'
MODULES = {'sqlcipher': ['sqlcipher3'], 'cryptography': ['cryptography', 'cryptography.hazmat', 'cryptography.hazmat.primitives',
                                                         'cryptography.hazmat.primitives.ciphers', 'cryptography.hazmat.primitives.ciphers.aead']}


class SealTest(unittest.TestCase):
    def test_need(self):
        for cipher, mods in MODULES.items():
            with self.subTest(cipher):
                crypto.need(cipher)
                with mock.patch.dict(sys.modules, dict.fromkeys(mods)):  # None: the import of the package fails
                    bad(self, crypto.need, t('cipher_missing', pkg=mods[0]), cipher)
                    bad(self, crypto.seal, t('cipher_missing', pkg=mods[0]), LINES, cipher, KEY, AAD)
                    bad(self, crypto.unseal, t('cipher_missing', pkg=mods[0]), b'', cipher, KEY, AAD)
                self.assertIn('python -m pip install ' + mods[0], t('cipher_missing', pkg=mods[0]))

    def test_seal_roundtrip(self):
        self.assertEqual(crypto.CIPHERS, ('sqlcipher', 'cryptography'))
        self.assertEqual(crypto.EXT, {None: '.jsonl.gz', 'sqlcipher': '.sqlcipher', 'cryptography': '.aesgcm'})
        for cipher in crypto.CIPHERS:
            with self.subTest(cipher):
                data = crypto.seal(LINES, cipher, KEY, AAD)
                self.assertEqual(crypto.unseal(data, cipher, KEY, AAD), LINES)
                self.assertNotIn('секрет'.encode('utf-8'), data)

    def test_seal_refuses(self):
        for cipher in crypto.CIPHERS:
            with self.subTest(cipher):
                data = crypto.seal(LINES, cipher, KEY, AAD)
                mid = len(data) // 2
                torn = data[:mid] + bytes([data[mid] ^ 1]) + data[mid + 1:]
                for args in [(data, cipher, bytes(32), AAD), (data, cipher, KEY, 'A/1/batch-00000002'), (torn, cipher, KEY, AAD),
                             (data[:mid], cipher, KEY, AAD), (b'', cipher, KEY, AAD), (b'junk', cipher, KEY, AAD)]:
                    bad(self, crypto.unseal, t('seal_broken'), *args)

    def test_seal_leaves_no_file_behind(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(tempfile, 'tempdir', d):
            data = crypto.seal(LINES, 'sqlcipher', KEY, AAD)
            crypto.unseal(data, 'sqlcipher', KEY, AAD)
            bad(self, crypto.unseal, t('seal_broken'), data, 'sqlcipher', bytes(32), AAD)
            bad(self, crypto.unseal, t('seal_broken'), data, 'sqlcipher', KEY, 'other')
            self.assertEqual(os.listdir(d), [])


    def test_failed_cleanup_does_not_break_a_seal(self):
        # a scanner may hold the temp file for a moment: what is left is only ciphertext, and must not turn a result into an error
        with tempfile.TemporaryDirectory() as d, mock.patch.object(tempfile, 'tempdir', d):
            with mock.patch.object(os, 'remove', side_effect=PermissionError):
                data = crypto.seal(LINES, 'sqlcipher', KEY, AAD)
                self.assertEqual(crypto.unseal(data, 'sqlcipher', KEY, AAD), LINES)
                bad(self, crypto.unseal, t('seal_broken'), b'junk', 'sqlcipher', KEY, AAD)


class DbTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = os.path.join(d.name, 'index.db')

    def test_open_db(self):
        c = crypto.open_db(self.path, KEY)
        try:
            c.execute('CREATE VIRTUAL TABLE t USING fts5(x)')
            c.execute('INSERT INTO t VALUES(?)', ('привет мир',))
            self.assertEqual(c.execute("SELECT x FROM t WHERE t MATCH 'мир'").fetchall(), [('привет мир',)])
        finally:
            c.close()
        self.assertTrue(crypto.is_encrypted(self.path))
        with open(self.path, 'rb') as f:
            raw = f.read()
        self.assertNotIn(b'SQLite format 3', raw)
        self.assertNotIn('привет'.encode('utf-8'), raw)
        bad(self, crypto.open_db, t('key_wrong'), self.path, bytes(32))
        c = crypto.open_db(self.path, KEY)  # the failed attempt left it as it was
        self.assertEqual(c.execute('SELECT count(*) FROM t').fetchone(), (1,))
        c.close()

    def test_wrong_key_is_quiet(self):
        crypto.open_db(self.path, KEY).close()
        c = crypto.open_db(self.path, KEY)
        c.execute('CREATE TABLE t(x)')
        c.close()
        # SQLCipher reports a failed key on stderr from C, below Python: only another process shows it
        code = 'import sys, crypto\ntry:\n    crypto.open_db(sys.argv[1], bytes(32))\nexcept ValueError:\n    pass'
        r = subprocess.run([sys.executable, '-c', code, self.path], cwd=os.path.dirname(os.path.abspath(__file__)), capture_output=True)
        self.assertEqual((r.returncode, r.stderr), (0, b''))

    def test_busy_is_not_a_wrong_key(self):
        import sqlcipher3
        holder = crypto.open_db(self.path, KEY)
        try:
            holder.execute('CREATE TABLE t(x)')
            holder.execute('BEGIN EXCLUSIVE')
            with self.assertRaises(sqlcipher3.OperationalError):
                crypto.open_db(self.path, KEY, timeout=0.1)
        finally:
            holder.close()

    def test_is_encrypted(self):
        self.assertFalse(crypto.is_encrypted(self.path))  # no file
        open(self.path, 'wb').close()
        self.assertFalse(crypto.is_encrypted(self.path))  # empty
        c = sqlite3.connect(self.path)
        c.execute('CREATE TABLE t(x)')
        c.commit()
        c.close()
        self.assertFalse(crypto.is_encrypted(self.path))  # open
        c = crypto.open_db(self.path + '.enc', KEY)
        c.execute('CREATE TABLE t(x)')
        c.close()
        self.assertTrue(crypto.is_encrypted(self.path + '.enc'))


if __name__ == '__main__':
    unittest.main()
