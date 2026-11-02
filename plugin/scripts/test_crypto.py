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

    def test_settings_path_is_home_for_a_registered_dir(self):
        with mock.patch.object(crypto, 'HOME_DIR', 'H'), mock.patch.object(crypto, 'INDEX_DIRS', {crypto.norm('d:/x/')}):
            self.assertEqual(crypto.settings_path('D:\\X\\index.db'), os.path.join('H', 'settings.json'))  # written one way, asked another
            self.assertEqual(crypto.settings_path('d:/x/index.db'), os.path.join('H', 'settings.json'))
            self.assertEqual(crypto.settings_path('D:\\Y\\index.db'), os.path.join('D:\\Y', 'settings.json'))  # not registered: beside the db

    def test_set_fields_keeps_the_others(self):
        self.write('\ufeff{"encryptionKey": "dpapi:abc", "language": "ru"}')
        crypto.set_fields(self.path, {'indexDir': 'D:\\X'})
        with open(self.path, encoding='utf-8-sig') as f:
            self.assertEqual(json.load(f), {'encryptionKey': 'dpapi:abc', 'language': 'ru', 'indexDir': 'D:\\X'})
        crypto.set_fields(self.path, {'indexDir': None, 'nothing': None})  # None takes a key out, one that is not there is no error
        with open(self.path, encoding='utf-8-sig') as f:
            self.assertEqual(json.load(f), {'encryptionKey': 'dpapi:abc', 'language': 'ru'})
        self.assertFalse(os.path.exists(self.path + '.cg-tmp'))
        self.write('[1]')
        with self.assertRaises(crypto.SettingsBroken):  # a file that cannot be read is not written over
            crypto.set_fields(self.path, {'indexDir': 'D:\\X'})
        self.assertEqual(self.raw(), b'[1]')
        self.assertFalse(os.path.exists(self.path + '.cg-tmp'))

    def test_set_fields_puts_the_bytes_on_the_disk_before_the_replace(self):
        calls = []
        with mock.patch('os.fsync', lambda fd: calls.append('fsync')), mock.patch('os.replace', lambda a, b: calls.append('replace')):
            crypto.set_fields(self.path, {'indexDir': 'D:\\X'})
        self.assertEqual(calls, ['fsync', 'replace'])

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

    @dpapi
    def test_settings_cipher_goes_in_with_the_key_and_stays_when_none_is_given(self):
        self.write('{"language": "ru"}')
        crypto.save_key(self.path, KEY, 'cryptography')  # the key and the cipher in one write
        with open(self.path, encoding='utf-8-sig') as f:
            s = json.load(f)
        self.assertEqual((list(s), s['language'], s['shareCipher']), (['language', crypto.KEY_FIELD, 'shareCipher'], 'ru', 'cryptography'))
        self.assertEqual(crypto.load_key(self.path), KEY)
        for key, cipher, left in ((KEY, 'sqlcipher', 'sqlcipher'), (KEY, None, 'sqlcipher'), (None, None, 'sqlcipher')):  # a new one wins; none given, none taken
            crypto.save_key(self.path, key, cipher)
            with open(self.path, encoding='utf-8-sig') as f:
                self.assertEqual(json.load(f).get('shareCipher'), left)
        self.assertIsNone(crypto.load_key(self.path))

    def test_settings_broken_untouched(self):
        for text in ['{"language": "ru",', '[1]']:
            self.write(text)
            bad(self, crypto.save_key, t('settings_broken', path=self.path), self.path, KEY)
            bad(self, crypto.load_key, t('settings_broken', path=self.path), self.path)
            for fn, args in ((crypto.save_key, (self.path, KEY)), (crypto.load_key, (self.path,))):  # told apart by class, not by text
                with self.assertRaises(crypto.SettingsBroken):
                    fn(*args)
            self.assertEqual(self.raw(), text.encode('utf-8'))
            self.assertFalse(os.path.exists(self.path + '.cg-tmp'))

    def test_a_broken_settings_file_is_told_before_the_key_is_protected(self):
        self.write('[1]')
        with mock.patch.object(crypto, 'protect', side_effect=AssertionError('the key was protected first')):
            with self.assertRaises(crypto.SettingsBroken):
                crypto.save_key(self.path, KEY)

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
# AES-GCM recorded once from the cryptography package (AESGCM(KEY).encrypt with NONCE): (data, aad, ciphertext as hex, tag as hex);
# files written by the old code must open, and the test imports no package
NONCE = bytes(range(100, 112))
RECORDED = [
    (b'', '',
     '',
     '609bd50f6ad76df665f048ba8c5fd706'),
    (b'', 'aad',
     '',
     '9edc85d37715943677af73627fac7583'),
    (bytes(i * 7 % 251 for i in range(1000)), 'A/1/batch-00000001',
     ('481cd07365ca7caf065d19a58e3e089432b5788f07ff69d30f7e1af53f687791740eae354d1a9412e0d7530dc4028d814e07'
      'fb8d51323eae7765377446311234cd4008d437a3d4f38966fd540faf1dd55e39577bf198e9adad88b643f586d2f224c02ac1'
      'ad806ca551132e32a107a9f03d0dad2bfa3bf064e527f6dda9ce894fbd09050cc280fbeb0e8783d96a6aad7a92ccef5f706c'
      'a8a70dd2341cb84ec397e44aa5d5004b7f45c7eb1b3624bb7513721a3f5032bf41d559293e7effc4549c934f6184dd69f78b'
      'd3e7f4c5e88e49ca899841dc62b4ff47f41516cfcecc21dc7062eea8f8e443516cd1d85a3acc2076fe63bb6839153514211d'
      'ee6b2530ef875459bbb8426fd982e66abff48fd89b9c3b11ec820a89cda112919fb28894b172dbfb03765c4e8fd120df18c9'
      'af94bcda7f6edd64dd9eec71aa6ed0df1a03b6c848be1bf3121806f2a7f4300fdb312873ea8c604dfc826a56214a082c9e0e'
      '71d017500d7c81e7cffbbb80acfa7c28dd434480f400c1a383c47c967737ae574640cfa68f8d7a461cd17933889150cea3b5'
      'f11233319230c9a7f63e5519929cb7a1e19cc4d0419f3dee4e9c4d0c74f2077b7a9b07b68f494fda46b6c2d5130a86264cde'
      'd68e2995e6d5896432c88cf0c2f344f137e85166a47e77791e7988f0adc8593281627dab63393b93143b3f680cb9b66306e8'
      '5e5e044c6d78d6f881ac3db6e552bd9d09c7e8189d21a8942c111b4b534eb262829869105ea7e1037d8d139fd991d58bdc4c'
      'a5ea9e7aad4ed79f1a421ffe15ae9d3fdd7ec23b99e388c55fdcfc5017a46519cbcdb5b056de060268b3b2d7f393dfadedeb'
      'd7d9a8ac38000553b11cbdf75004aa9e0f093b8db16b181232fc288b2ce5bde0579fe2a6c2bdad193775e54ddda6dfcbeca8'
      '81d0c1d57a9b42e5b477bc74eed65f99ea058f0dc1fa104414989112762c302dafbf664eaec1ca8ae3045db978e0fb219bd0'
      '19290acc31551d671718a20991e0a61f7bc7561123f8499b1afdf7eb1f804636c89e8b3d6862eec8a98e204c32a11fa5f768'
      'a815b23bbc766f16a0606696ecf95627ccfa6b2bc96f1d4b9c11c2507582164903de87f4944eb292d477a77fe03ecedeb726'
      '7d4de7618745d5c25c9bb821b877b6863c4c602daee571030975fec7c2707721416976cb31e60ba590cec3dccb539a094608'
      'ea18099dda47ad8fc8a61f7d44e9c6078ed4d4e3e2717bcf4c4e280461676ebd1c4ceb46b4aeb5dc2f98c547f6ddc9137c70'
      '24505049ffbb79bf5f9ce5dd9499ec2885621afa503cd84056e392e5eeb1f35ae1d30dba438057e5def80a93141aec747c19'
      'afe4c394507446f5b1a98715b1b211a2fe9b398f538140ff198e1a38c2eb13373990cc263bb427bc53a35bc3588943d1020d'),
     '79505ee198f376b993e3833f6a2d942d'),
]


class SealTest(unittest.TestCase):
    def test_need(self):
        crypto.need('sqlcipher')
        with mock.patch.dict(sys.modules, {'sqlcipher3': None}):  # None: the import of the package fails
            bad(self, crypto.need, t('cipher_missing', pkg='sqlcipher3'), 'sqlcipher')
            bad(self, crypto.seal, t('cipher_missing', pkg='sqlcipher3'), LINES, 'sqlcipher', KEY, AAD)
            bad(self, crypto.unseal, t('cipher_missing', pkg='sqlcipher3'), b'', 'sqlcipher', KEY, AAD)
        self.assertIn('python -m pip install sqlcipher3', t('cipher_missing', pkg='sqlcipher3'))

    def test_cryptography_cipher_needs_no_package(self):  # its AES-GCM is Windows' own bcrypt.dll
        with mock.patch.dict(sys.modules, {'cryptography': None, 'cryptography.hazmat.primitives.ciphers.aead': None}):
            crypto.need('cryptography')
            data = crypto.seal(LINES, 'cryptography', KEY, AAD)
            self.assertEqual(crypto.unseal(data, 'cryptography', KEY, AAD), LINES)

    def test_gcm_spec_vector(self):  # test case 16 of the GCM specification; seal's nonce is random, so the vector goes through _gcm
        key = bytes.fromhex('feffe9928665731c6d6a8f9467308308' * 2)
        iv = bytes.fromhex('cafebabefacedbaddecaf888')
        text = bytes.fromhex('d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72'
                             '1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b39')
        aad = bytes.fromhex('feedfacedeadbeeffeedfacedeadbeefabaddad2')
        sealed = bytes.fromhex('522dc1f099567d07f47f37a32a84427d643a8cdcbfe5c0c97598a2bd2555d1aa'
                               '8cb08e48590dbb3da7b08b1056828838c5f61e6393ba7a0abcc9f662')
        tag = bytes.fromhex('76fc6ece0f4e1768cddf8853bb2d551b')
        self.assertEqual(crypto._gcm(False, key, iv, text, aad, bytes(16)), (sealed, tag))
        self.assertEqual(crypto._gcm(True, key, iv, sealed, aad, tag), (text, tag))

    def test_gcm_recorded_vectors(self):
        for data, aad, ct, tag in RECORDED:
            with self.subTest(size=len(data), aad=aad):
                ct, tag = bytes.fromhex(ct), bytes.fromhex(tag)
                self.assertEqual(crypto._gcm(False, KEY, NONCE, data, aad.encode(), bytes(16)), (ct, tag))
                self.assertEqual(crypto._gcm(True, KEY, NONCE, ct, aad.encode(), tag), (data, tag))
                self.assertIsNone(crypto._gcm(True, KEY, NONCE, ct, aad.encode(), bytes(16)))
                self.assertEqual(crypto._gcm_body(NONCE + ct + tag, KEY, aad), data)  # the file layout of the old code: nonce, ciphertext, tag

    def test_gcm_refuses_what_is_no_tag_mismatch(self):
        with self.assertRaises(OSError):
            crypto._chk(-1073741811)  # STATUS_INVALID_PARAMETER
        for args in [(b'k' * 16, bytes(12), b'', b'', bytes(16)), (KEY, bytes(8), b'', b'', bytes(16)), (KEY, bytes(12), b'', b'', bytes(8))]:
            with self.assertRaises(ValueError):
                crypto._gcm(False, *args)

    def test_gcm_tamper(self):
        data = crypto.seal(LINES, 'cryptography', KEY, AAD)
        flip = lambda i: data[:i] + bytes([data[i] ^ 1]) + data[i + 1:]
        for args in [(data, bytes(32), AAD), (data, KEY, 'A/1/batch-00000002'), (flip(0), KEY, AAD), (flip(12), KEY, AAD),
                     (flip(len(data) - 17), KEY, AAD), (flip(len(data) - 16), KEY, AAD), (flip(len(data) - 1), KEY, AAD),
                     (data[:-1], KEY, AAD), (data[:15], KEY, AAD)]:
            bad(self, crypto.unseal, t('seal_broken'), args[0], 'cryptography', *args[1:])

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

    def test_a_locked_scratch_file_is_no_verdict_on_the_batch(self):  # a skip offered for it would lose records
        import sqlcipher3
        data = crypto.seal(LINES, 'sqlcipher', KEY, AAD)
        real, holders = crypto.open_db, []

        def locked(path, key, timeout=30):  # another process holds the scratch file for a moment (a scanner)
            holders.append(real(path, key))
            holders[-1].execute('BEGIN EXCLUSIVE')
            return real(path, key, timeout=0.1)

        with tempfile.TemporaryDirectory() as d, mock.patch.object(tempfile, 'tempdir', d):
            try:
                with mock.patch.object(crypto, 'open_db', locked), self.assertRaises(sqlcipher3.OperationalError):
                    crypto.unseal(data, 'sqlcipher', KEY, AAD)
            finally:
                for h in holders:
                    h.close()
            other = os.path.join(d, 'other')  # whereas a container with no table c is a damaged one
            c = crypto.open_db(other, KEY)
            c.execute('CREATE TABLE d(x)')
            c.close()
            with open(other, 'rb') as f:
                bad(self, crypto.unseal, t('seal_broken'), f.read(), 'sqlcipher', KEY, AAD)

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

    def test_page_size(self):
        self.assertEqual(crypto.INDEX_PAGE, 16384)
        for page, other in ((crypto.INDEX_PAGE, None), (None, crypto.INDEX_PAGE)):  # a file opens with the page it was made with only
            path = '%s.%s' % (self.path, page)
            c = crypto.open_db(path, KEY, page=page)
            c.execute('CREATE TABLE t(x)')
            self.assertEqual(c.execute('PRAGMA cipher_page_size').fetchone(), (str(page or 4096),))  # a string from the pragma
            c.close()
            crypto.open_db(path, KEY, page=page).close()
            bad(self, crypto.open_db, t('key_wrong'), path, KEY, 30, other)
        # a sealed container keeps the default page, as the other computers read it (unseal, which opens with the default, covers it too)
        data = crypto.seal(LINES, 'sqlcipher', KEY, AAD)
        with open(self.path, 'wb') as f:
            f.write(data)
        crypto.open_db(self.path, KEY).close()
        bad(self, crypto.open_db, t('key_wrong'), self.path, KEY, 30, crypto.INDEX_PAGE)

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

    def test_temp_store_is_memory(self):  # %TEMP% is not under EFS: a sorting or a big query must not spill into it in clear
        plain, enc = crypto.plain_db(self.path), crypto.open_db(self.path + '.enc', KEY)
        try:
            self.assertEqual(plain.execute('PRAGMA temp_store').fetchone()[0], 2)
            self.assertEqual(enc.execute('PRAGMA temp_store').fetchone()[0], 2)
            self.assertIsNone(plain.isolation_level)
        finally:
            plain.close()
            enc.close()

    def test_plain_db_denied_by_efs(self):
        with open(self.path, 'wb') as f:
            f.write(b'x')
        refuse = mock.patch.object(sqlite3, 'connect', side_effect=sqlite3.OperationalError('unable to open database file'))
        with refuse, mock.patch.object(crypto, 'efs_on', return_value=True):
            bad(self, crypto.plain_db, t('index_efs_denied', path=self.path), self.path)
        with refuse, mock.patch.object(crypto, 'efs_on', return_value=False), self.assertRaises(sqlite3.OperationalError):  # a refusal that is not EFS's stays as it is
            crypto.plain_db(self.path)
        with mock.patch.object(sqlite3, 'connect', side_effect=sqlite3.OperationalError('disk I/O error')), \
                mock.patch.object(crypto, 'efs_on', return_value=True), self.assertRaises(sqlite3.OperationalError):
            crypto.plain_db(self.path)

    @dpapi
    def test_header_of_an_efs_file_that_is_refused_is_efs_denied(self):  # the read of the header fails first, as Windows says "access denied"
        crypto.efs_encrypt(os.path.dirname(self.path))
        with open(self.path, 'wb') as f:
            f.write(b'x')
        with mock.patch('builtins.open', side_effect=PermissionError(13, 'Permission denied')):
            bad(self, crypto.is_encrypted, t('index_efs_denied', path=self.path), self.path)
            with mock.patch.object(crypto, 'efs_on', return_value=False), self.assertRaises(PermissionError):  # outside EFS it is as it was
                crypto.is_encrypted(self.path)

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


@dpapi
class EfsTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name

    def test_efs_roundtrip(self):
        self.assertTrue(crypto.efs_supported(self.dir))
        self.assertTrue(crypto.efs_supported(os.path.join(self.dir, 'not', 'there', 'yet')))  # the nearest folder that exists answers
        crypto.efs_encrypt(self.dir)
        self.assertTrue(crypto.efs_on(self.dir))
        p = os.path.join(self.dir, 'f.bin')
        with open(p, 'wb') as f:
            f.write(b'content')
        self.assertTrue(crypto.efs_on(p))  # a file made in an encrypted folder is encrypted
        crypto.efs_decrypt(p)
        self.assertFalse(crypto.efs_on(p))
        with open(p, 'rb') as f:
            self.assertEqual(f.read(), b'content')
        crypto.efs_decrypt(self.dir)
        self.assertFalse(crypto.efs_on(self.dir))

    def test_efs_on_missing_is_false(self):
        self.assertFalse(crypto.efs_on(os.path.join(self.dir, 'nothing')))

    def test_a_failure_is_an_oserror_with_the_windows_code(self):
        missing = os.path.join(self.dir, 'nothing')
        with self.assertRaises(OSError) as cm:
            crypto.efs_decrypt(missing)
        self.assertEqual(cm.exception.winerror, 2)  # ERROR_FILE_NOT_FOUND
        with self.assertRaises(OSError) as cm:
            crypto.efs_encrypt(missing)
        self.assertIsNotNone(cm.exception.winerror)  # which one differs: EncryptFileW says "access denied" here


if __name__ == '__main__':
    unittest.main()
