import base64
import json
import os
import re
import tempfile
import unittest

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


if __name__ == '__main__':
    unittest.main()
