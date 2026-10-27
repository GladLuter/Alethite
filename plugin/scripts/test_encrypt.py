"""Stage 5: encrypt on, join, off and status, each one safe to repeat. Two computers as in test_sync, a temporary folder for the share.
Every transcript here is synthetic; a window is a FakeUI that answers as told."""
import contextlib
import io
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock
import crypto
import encrypt
import memdb
import sync as cgsync
from test_sync import Computer
from texts import t


class FakeUI:
    """`answers`: what `confirm` says one after the other, "yes" when they run out; `code`: what the entry window gives back (None: cancelled)."""

    def __init__(self, answers=(), code=None):
        self.answers, self.code, self.shown, self.asked, self.entries = list(answers), code, [], [], 0

    def show_code(self, code):
        self.shown.append(code)

    def confirm(self, text):
        self.asked.append(text)
        return self.answers.pop(0) if self.answers else True

    def ask_code(self):
        self.entries += 1
        return self.code


@unittest.skipUnless(os.name == 'nt', 'DPAPI is Windows only')
class EncryptTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = d.name
        self.share = os.path.join(d.name, 'share')
        os.makedirs(self.share)
        p = mock.patch('urllib.request.build_opener', side_effect=OSError)  # no Ollama to ask for a digest
        p.start()
        self.addCleanup(p.stop)
        self.a, self.b = self.computer('AIJ-HP'), self.computer('MTLAPTOP')
        self.a.say('conv-a', 'ёжик в тумане на i3', 'ответ про ёжика')
        self.b.say('conv-b', 'кэш протух на ноутбуке', 'ответ про кэш')
        for c in (self.a, self.b):
            c.index()
            c.embed()

    def computer(self, name):
        c = Computer(self.root, self.share, name)
        self.addCleanup(c.close)
        return c

    def cmd(self, fn, c, ui, *cipher, share=None, **kw):
        """`fn` as the command line runs it for computer `c`: its connection is shut meanwhile (Windows will not replace an open file)."""
        c.close()
        try:
            return fn(c.db, share or c.share, *cipher, c.name, ui, memory_dir=c.memory, project_cwd=c.cwd, **kw)
        finally:
            with contextlib.suppress(ValueError):  # an encrypted index with no key does not open, and that is the test
                c.open()

    def on(self, c, ui=None, **kw):
        return self.cmd(encrypt.on, c, ui or FakeUI(), 'sqlcipher', **kw)

    def join(self, c, code, **kw):
        return self.cmd(encrypt.join, c, FakeUI(code=code), 'sqlcipher', **kw)

    def key(self, c):
        return crypto.load_key(crypto.settings_path(c.db))

    def counts(self, conn):
        return {n: conn.execute('SELECT count(*) FROM ' + n).fetchone()[0] for n in ('chunks', 'keys', 'vecs')}

    def assert_index_pages(self, path, key):
        """The encrypted index file has 16 KB pages: it opens with them and does not open with the default 4096 (so it really has them)."""
        conn = crypto.open_db(path, key, page=crypto.INDEX_PAGE)
        self.assertEqual(conn.execute('PRAGMA cipher_page_size').fetchone(), ('16384',))
        conn.close()
        with self.assertRaises(ValueError) as cm:
            crypto.open_db(path, key)
        self.assertEqual(str(cm.exception), t('key_wrong'))

    def own(self, c):
        return os.path.join(self.share, c.name)

    def flag(self, c):
        return (c.conn.execute("SELECT value FROM meta WHERE key = 'share_unfinished'").fetchone() or (None,))[0]

    # on
    def test_on(self):
        a = self.a
        self.assertEqual(a.sync()['errors'], [])  # an open history in the share first
        before = self.counts(a.conn)
        self.assertTrue(all(before.values()))
        ui, out, err = FakeUI(), io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            res = self.on(a, ui)
        self.assertEqual((res['action'], res['done'], res['notes']), ('on', True, []))
        self.assertTrue(crypto.is_encrypted(a.db))
        self.assertEqual(self.counts(a.conn), before)
        self.assert_index_pages(a.db, self.key(a))
        self.assertEqual([h['conv'] for h in memdb.search(a.conn, 'ёжик')], ['conv-a'])  # the full-text index came over too
        self.assertFalse(os.path.exists(a.db + '.plain'))
        self.assertFalse(os.path.exists(a.db + '.cg-new'))
        self.assertEqual(len(ui.shown), 1)
        key = self.key(a)
        self.assertEqual(crypto.parse_code(ui.shown[0]), key)
        for secret in (ui.shown[0], key.hex()):
            for text in (json.dumps(res), out.getvalue(), err.getvalue(), *ui.asked):
                self.assertNotIn(secret, text)
        self.assertEqual(ui.asked, [t('dlg_code_saved'), t('dlg_delete_plain', path=a.db + '.plain'), t('dlg_delete_old_share', n=1)])
        gen = cgsync.generation(self.own(a))
        self.assertEqual((gen.n, gen.cipher, gen.fingerprint), (1, 'sqlcipher', crypto.fingerprint(key)))
        self.assertEqual([n for n in os.listdir(self.own(a)) if n.startswith('batch-')], [])
        self.assertEqual(sorted(os.listdir(gen.path)), ['batch-00000001.sqlcipher', 'generation.json'])

    def test_on_keep_plain_then_repeat(self):
        a = self.a
        ui = FakeUI([True, False])  # the code is saved; the open copy is not to go yet
        res = self.on(a, ui)
        plain = a.db + '.plain'
        self.assertEqual((res['done'], res['notes']), (False, [t('note_plain_kept', path=plain)]))
        self.assertTrue(os.path.exists(plain))
        self.assertTrue(crypto.is_encrypted(a.db))
        self.assertIn(t('note_plain_kept', path=plain), encrypt.status(a.db)['notes'])
        again = FakeUI()
        self.assertTrue(self.on(a, again)['done'])
        self.assertFalse(os.path.exists(plain))
        self.assertEqual((len(ui.shown), again.shown), (1, []))
        self.assertEqual(again.asked, [t('dlg_delete_plain', path=plain)])
        self.assertEqual(encrypt.status(a.db)['notes'], [])

    def test_on_code_not_saved(self):
        a = self.a
        before = self.counts(a.conn)
        res = self.on(a, FakeUI([False]))
        self.assertEqual((res['done'], res['notes']), (False, [t('code_not_saved')]))
        self.assertIsNone(self.key(a))
        self.assertFalse(crypto.is_encrypted(a.db))
        self.assertEqual((self.counts(a.conn), os.listdir(self.share)), (before, []))

    def old_backup(self, c):
        """An index of before stage 2 beside the index of `c`: no keys and no vecs in it."""
        backup = os.path.join(os.path.dirname(c.db), 'index.before-stage2.db')
        old = sqlite3.connect(backup)
        old.execute('CREATE VIRTUAL TABLE chunks USING fts5(text, conv UNINDEXED, pos UNINDEXED, ts UNINDEXED, kind UNINDEXED)')
        old.execute("INSERT INTO chunks VALUES('старый ёж', 'c', 1, 't', 'owner')")
        old.commit()
        old.close()
        return backup

    def test_on_backup(self):
        a = self.a
        backup = self.old_backup(a)
        ui = FakeUI()
        self.assertTrue(self.on(a, ui)['done'])
        self.assertTrue(crypto.is_encrypted(a.db) and crypto.is_encrypted(backup))
        self.assertFalse(os.path.exists(backup + '.plain'))
        self.assert_index_pages(backup, self.key(a))
        conn = crypto.open_db(backup, self.key(a), page=crypto.INDEX_PAGE)
        self.assertEqual(conn.execute('SELECT text FROM chunks').fetchall(), [('старый ёж',)])
        conn.close()
        self.assertIn(t('dlg_delete_plain', path=backup + '.plain'), ui.asked)

    def test_on_refuses_other_encrypted(self):
        self.assertTrue(self.on(self.b)['done'])
        a, ui = self.a, FakeUI()
        for key in (None, secrets.token_bytes(32)):  # no key here, or another one
            if key:
                crypto.save_key(crypto.settings_path(a.db), key)
            with self.assertRaises(ValueError) as cm:
                self.on(a, ui)
            self.assertEqual(str(cm.exception), t('encrypt_use_join', machine='MTLAPTOP'))
        self.assertEqual((ui.shown, ui.asked, crypto.is_encrypted(a.db), os.path.exists(self.own(a))), ([], [], False, False))

    def test_on_refuses_key_missing(self):
        a = self.a
        self.assertTrue(self.on(a)['done'])
        crypto.save_key(crypto.settings_path(a.db), None)
        ui = FakeUI()
        with self.assertRaises(ValueError) as cm:
            self.on(a, ui)
        self.assertEqual(str(cm.exception), t('key_missing'))
        self.assertEqual((ui.shown, ui.asked), ([], []))
        self.assertTrue(crypto.is_encrypted(a.db))

    def test_on_share_unfinished(self):
        a = self.a
        self.assertEqual(a.sync()['errors'], [])
        ui = FakeUI()
        res = self.on(a, ui, seconds=0)  # the new generation starts, nothing of it goes out
        self.assertEqual((res['done'], res['notes']), (False, [t('note_share_pending')]))
        self.assertEqual(self.flag(a), 'pending')
        self.assertEqual(ui.asked, [t('dlg_code_saved'), t('dlg_delete_plain', path=a.db + '.plain')])  # the old generation stays: nothing replaces it yet
        self.assertEqual(cgsync.generation(self.own(a)).n, 1)
        self.assertTrue(os.path.exists(os.path.join(self.own(a), 'batch-00000001.jsonl.gz')))
        again = FakeUI()
        res = self.on(a, again)
        self.assertEqual((res['done'], res['notes'], self.flag(a)), (True, [], None))
        self.assertEqual(again.asked, [t('dlg_delete_old_share', n=1)])
        self.assertEqual(sorted(os.listdir(cgsync.generation(self.own(a)).path)), ['batch-00000001.sqlcipher', 'generation.json'])

    def test_on_share_missing_is_pending_too(self):
        a = self.a
        nowhere = os.path.join(self.root, 'nowhere')
        res = self.on(a, share=nowhere)
        self.assertFalse(res['done'])
        self.assertEqual(res['notes'][0], t('note_share_pending'))
        self.assertTrue(res['notes'][1].startswith('the sync folder is unavailable: ' + nowhere), res['notes'])
        self.assertTrue(crypto.is_encrypted(a.db))
        self.assertEqual(self.flag(a), 'pending')

    def test_on_share_blocked_by_an_own_batch_that_is_broken(self):
        a = self.a
        self.assertEqual(a.sync()['errors'], [])
        with open(os.path.join(self.own(a), 'batch-00000002.jsonl.gz'), 'wb') as f:
            f.write(b'garbage')
        ui = FakeUI()
        res = self.on(a, ui)  # our own history is read back before a new generation: it cannot be, so none starts
        self.assertFalse(res['done'])
        self.assertEqual(res['notes'][0], t('note_share_pending'))
        self.assertTrue(res['notes'][1].startswith('batch 2 is broken'), res['notes'])
        self.assertEqual((self.flag(a), cgsync.generation(self.own(a)).n), ('pending', 0))
        self.assertEqual(len(ui.asked), 2)
        self.assertTrue(os.path.exists(os.path.join(self.own(a), 'batch-00000001.jsonl.gz')))

    def test_on_interrupted_swap(self):
        a = self.a
        before, real, calls = self.counts(a.conn), os.replace, []

        def boom(src, dst):
            if src.endswith('.cg-new') and not calls:
                calls.append(src)
                raise OSError('boom')
            return real(src, dst)

        with mock.patch('os.replace', boom), self.assertRaises(OSError):
            self.on(a)
        self.assertFalse(crypto.is_encrypted(a.db))
        self.assertEqual(self.counts(a.conn), before)
        self.assertFalse(os.path.exists(a.db + '.cg-new'))
        ui = FakeUI()
        self.assertTrue(self.on(a, ui)['done'])
        self.assertEqual((ui.shown, crypto.is_encrypted(a.db), os.path.exists(a.db + '.plain')), ([], True, False))
        self.assertEqual(ui.asked, [t('dlg_delete_plain', path=a.db + '.plain')])  # the link of the failed run was no question
        self.assertEqual(self.counts(a.conn), before)

    def test_on_swap_busy(self):
        a = self.a
        before, real = self.counts(a.conn), os.replace

        def deny(src, dst):
            if src.endswith('.cg-new'):
                raise PermissionError(13, 'in use')
            return real(src, dst)

        with mock.patch('os.replace', deny), mock.patch('time.sleep'), self.assertRaises(ValueError) as cm:
            self.on(a)
        self.assertEqual(str(cm.exception), t('encrypt_busy'))
        self.assertFalse(crypto.is_encrypted(a.db))
        self.assertEqual(self.counts(a.conn), before)
        self.assertFalse(os.path.exists(a.db + '.cg-new'))
        self.assertIsNotNone(self.key(a))  # the key is kept: the next run goes on from here
        self.assertTrue(self.on(a)['done'])

    def test_on_waits_for_a_file_that_is_in_use(self):
        a = self.a
        real, calls = os.replace, []

        def busy_twice(src, dst):
            if src.endswith('.cg-new') and len(calls) < 2:
                calls.append(src)
                raise PermissionError(13, 'in use')
            return real(src, dst)

        with mock.patch('os.replace', busy_twice), mock.patch('time.sleep'):
            self.assertTrue(self.on(a)['done'])
        self.assertEqual((len(calls), crypto.is_encrypted(a.db)), (2, True))

    def test_on_refuses_a_copy_that_differs(self):
        a = self.a
        before = self.counts(a.conn)
        with mock.patch.object(encrypt, '_counts', side_effect=[{'chunks': 2}, {'chunks': 1}]), self.assertRaises(ValueError) as cm:
            self.on(a)
        self.assertEqual(str(cm.exception), t('encrypt_copy_differs', path=a.db))
        self.assertEqual((crypto.is_encrypted(a.db), self.counts(a.conn)), (False, before))
        self.assertEqual([os.path.exists(a.db + s) for s in ('.cg-new', '.plain')], [False, False])  # no copy, and nothing linked yet

    def test_on_asks_about_a_plain_copy_that_is_another_file(self):
        a = self.a
        plain = a.db + '.plain'
        a.close()
        shutil.copyfile(a.db, plain)  # an older copy, not the link of a replace that failed
        with open(plain, 'rb') as f:
            stale = f.read()
        ui = FakeUI([True, False])  # the code is saved; the older copy stays
        res = self.on(a, ui)
        self.assertEqual((res['done'], res['notes']), (False, [t('note_plain_kept', path=plain)]))
        self.assertEqual((ui.asked[1:], crypto.is_encrypted(a.db)), ([t('dlg_delete_plain', path=plain)], False))  # asked before, not converted
        with open(plain, 'rb') as f:
            self.assertEqual(f.read(), stale)
        again = FakeUI()
        self.assertTrue(self.on(a, again)['done'])
        self.assertEqual(again.asked, [t('dlg_delete_plain', path=plain)] * 2)  # the older copy, then the copy of this run
        self.assertEqual((os.path.exists(plain), crypto.is_encrypted(a.db)), (False, True))

    def test_leftover_copies_of_a_killed_run_go_in_either_direction(self):
        a = self.a
        left = [a.db + '.cg-new', a.db + '.cg-new-journal']

        def leave():
            for p in left:
                with open(p, 'wb') as f:
                    f.write(b'plain text of the index')

        leave()
        self.assertTrue(self.on(a)['done'])
        self.assertEqual([os.path.exists(p) for p in left], [False, False])
        leave()  # the index is encrypted already: nothing to convert, the leftover still goes
        self.assertTrue(self.on(a)['done'])
        self.assertEqual([os.path.exists(p) for p in left], [False, False])
        self.assertTrue(self.cmd(encrypt.off, a, FakeUI())['done'])
        leave()  # and the same when it is open already
        self.assertTrue(self.cmd(encrypt.off, a, FakeUI())['done'])
        self.assertEqual([os.path.exists(p) for p in left], [False, False])

    def test_old_share_dialog_counts_generations(self):
        a = self.a
        self.assertEqual(a.sync()['errors'], [])
        a.say('conv-a2', 'третье сообщение', 'ответ на третье')
        a.index()
        self.assertEqual(a.sync()['errors'], [])  # two batches of generation 0 lie in the folder itself
        res = self.on(a, FakeUI([True, True, False]))  # the code, the open copy, and the old data of the folder stays
        self.assertEqual(res['notes'], [t('note_old_share_kept')])
        ui = FakeUI()
        self.assertTrue(self.cmd(encrypt.off, a, ui)['done'])
        self.assertEqual(ui.asked, [t('dlg_delete_old_share', n=2)])  # generation 0 with its two batches, and gen-0001: three paths
        self.assertEqual(sorted(os.listdir(self.own(a))), ['gen-0002'])

    def test_flag_stays_when_the_cleanup_fails_midway(self):
        a = self.a
        self.assertEqual(a.sync()['errors'], [])
        with mock.patch.object(encrypt, '_delete', side_effect=OSError('boom')), self.assertRaises(OSError):
            self.on(a)
        self.assertEqual(self.flag(a), 'old')
        self.assertTrue(os.path.exists(os.path.join(self.own(a), 'batch-00000001.jsonl.gz')))
        self.assertTrue(self.on(a)['done'])
        self.assertEqual((self.flag(a), sorted(os.listdir(self.own(a)))), (None, ['gen-0001']))

    def test_flag_is_set_before_the_sync(self):
        a = self.a
        self.assertTrue(self.on(a)['done'])
        with mock.patch.object(cgsync, 'generation', side_effect=OSError('hiccup')), self.assertRaises(OSError):
            self.cmd(encrypt.off, a, FakeUI())  # the folder cannot be looked at after the sync
        self.assertEqual(self.flag(a), 'pending')
        self.assertTrue(self.cmd(encrypt.off, a, FakeUI())['done'])
        self.assertEqual((self.flag(a), sorted(os.listdir(self.own(a)))), (None, ['gen-0002']))

    def test_delete_needs_nothing_newer_than_python_3_8(self):  # the project documents 3.8
        d = os.path.join(self.root, 'folder')
        os.makedirs(os.path.join(d, 'sub'))
        with mock.patch.object(os.path, 'isjunction', create=True, side_effect=AttributeError):
            encrypt._delete(d)
        self.assertFalse(os.path.exists(d))

    def test_on_old_share_is_cleaned_without_following_links(self):
        a = self.a
        self.assertEqual(a.sync()['errors'], [])
        target = os.path.join(self.root, 'precious')
        os.makedirs(target)
        with open(os.path.join(target, 'keep.txt'), 'w') as f:
            f.write('x')
        link = os.path.join(self.own(a), 'memory')
        if subprocess.run(['cmd', '/c', 'mklink', '/J', link, target], capture_output=True).returncode:
            self.skipTest('no junction here')
        self.assertTrue(self.on(a)['done'])
        self.assertFalse(os.path.lexists(link))
        self.assertEqual(os.listdir(target), ['keep.txt'])
        self.assertEqual(sorted(os.listdir(self.own(a))), ['gen-0001'])

    # join
    def test_join(self):
        a, b = self.a, self.b
        self.assertTrue(self.on(a)['done'])
        key = self.key(a)
        code = crypto.make_code(key)
        self.assertEqual(self.join(b, code.lower().replace('-', ' '))['done'], True)
        self.assertEqual((self.key(b), crypto.is_encrypted(b.db)), (key, True))
        self.assert_index_pages(b.db, key)
        gen = cgsync.generation(self.own(b))
        self.assertEqual((gen.cipher, gen.fingerprint), ('sqlcipher', crypto.fingerprint(key)))
        self.assertEqual(b.rows(), 4)  # B took A's records in
        self.assertEqual(a.sync(key=key)['errors'], [])
        self.assertEqual((a.rows(), a.texts()), (4, b.texts()))  # and A reads B's
        with self.assertRaises(ValueError) as cm:  # a stale code on a computer that has the right key: the code is wrong, not the key here
            self.join(b, crypto.make_code(secrets.token_bytes(32)))
        self.assertEqual(str(cm.exception), t('code_other_key'))
        self.assertEqual(self.key(b), key)

    def test_join_refuses_what_it_cannot_check(self):
        a, b = self.a, self.b
        self.assertTrue(self.on(a)['done'])
        good = crypto.make_code(self.key(a))
        for code, text in ((crypto.make_code(secrets.token_bytes(32)), t('code_other_key')),
                           (good[:4] + ('B' if good[4] == 'A' else 'A') + good[5:], t('code_bad'))):
            ui = FakeUI(code=code)
            with self.assertRaises(ValueError) as cm:
                self.cmd(encrypt.join, b, ui, 'sqlcipher')
            self.assertEqual(str(cm.exception), text)
        with self.assertRaises(ValueError) as cm:  # no encrypted index here and nothing encrypted in the folder to hold the code against
            self.join(b, good, share=os.path.join(self.root, 'nowhere'))
        self.assertEqual(str(cm.exception), t('code_unverifiable'))
        self.assertEqual((self.key(b), crypto.is_encrypted(b.db), os.path.exists(self.own(b))), (None, False, False))

    def test_join_cancelled(self):
        ui = FakeUI()
        res = self.cmd(encrypt.join, self.b, ui, 'sqlcipher')
        self.assertEqual((res, ui.entries, ui.asked, self.key(self.b)), ({'action': 'join', 'done': False, 'notes': []}, 1, [], None))

    def test_join_refuses_to_replace_another_key(self):  # a code that fits the folder, on a computer that has a key of its own
        a, b = self.a, self.b
        self.assertTrue(self.on(a)['done'])
        other = secrets.token_bytes(32)
        cgsync.start_generation(b.conn, self.own(b), 'MTLAPTOP', 'sqlcipher', other)
        with self.assertRaises(ValueError) as cm:
            self.join(a, crypto.make_code(other))
        self.assertEqual(str(cm.exception), t('join_other_key_here'))
        self.assertEqual(crypto.fingerprint(self.key(a)), cgsync.generation(self.own(a)).fingerprint)

    def test_join_checks_the_index_too(self):  # a code that fits the folder but not the index here is not saved
        a, b = self.a, self.b
        self.assertTrue(self.on(a)['done'])
        other = secrets.token_bytes(32)
        cgsync.start_generation(b.conn, self.own(b), 'MTLAPTOP', 'sqlcipher', other)
        crypto.save_key(crypto.settings_path(a.db), None)
        with self.assertRaises(ValueError) as cm:
            self.join(a, crypto.make_code(other))
        self.assertEqual(str(cm.exception), t('key_wrong'))
        self.assertIsNone(self.key(a))

    def test_join_restores_lost_key(self):
        a = self.a
        self.assertTrue(self.on(a)['done'])
        key, settings = self.key(a), crypto.settings_path(a.db)
        code = crypto.make_code(key)
        for damage, text in ((lambda: crypto.save_key(settings, None), t('key_missing')),
                             (lambda: self.write_settings(settings, {'encryptionKey': 'dpapi:AAAA'}), t('key_unreadable'))):
            damage()
            a.close()
            with self.assertRaises(ValueError) as cm:
                memdb.connect(a.db)
            self.assertEqual(str(cm.exception), text)
            self.assertTrue(self.join(a, code)['done'])
            self.assertEqual(self.key(a), key)
            a.open()
            self.assertEqual(a.rows(), 2)

    def write_settings(self, path, obj):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(obj, f)

    # off
    def test_off(self):
        a, b = self.a, self.b
        self.assertTrue(self.on(a)['done'])
        key = self.key(a)
        self.assertTrue(self.join(b, crypto.make_code(key))['done'])
        self.assertEqual(a.sync(key=key)['errors'], [])
        before = self.counts(a.conn)
        ui = FakeUI()
        res = self.cmd(encrypt.off, a, ui)
        self.assertEqual((res['action'], res['done'], res['notes']), ('off', True, []))
        self.assertFalse(crypto.is_encrypted(a.db))
        self.assertIsNone(self.key(a))
        self.assertEqual(self.counts(a.conn), before)
        plain = sqlite3.connect(a.db)  # the 16 KB pages of the encrypted file are gone: a plain file that sqlite3 itself opens
        self.assertEqual(self.counts(plain), before)
        plain.close()
        self.assertFalse(os.path.exists(a.db + '.cg-new'))
        gen = cgsync.generation(self.own(a))
        self.assertEqual((gen.n, gen.cipher, gen.fingerprint), (2, None, None))  # open again, and the encrypted one before it is gone
        self.assertEqual(sorted(os.listdir(self.own(a))), ['gen-0002'])
        self.assertEqual(sorted(os.listdir(gen.path)), ['batch-00000001.jsonl.gz', 'generation.json'])
        self.assertEqual(ui.asked, [t('dlg_delete_old_share', n=1)])
        self.assertEqual(b.sync(key=key)['errors'], [{'where': 'pull', 'machine': 'AIJ-HP', 'error': t('peer_plain', machine='AIJ-HP')}])
        self.assertEqual(encrypt.status(a.db), {'on': False, 'db': 'plain', 'key': 'missing', 'notes': []})

    def test_off_keeps_the_old_share_when_told_to(self):
        a = self.a
        self.assertTrue(self.on(a)['done'])
        res = self.cmd(encrypt.off, a, FakeUI([False]))
        self.assertEqual((res['done'], res['notes'], self.flag(a)), (False, [t('note_old_share_kept')], 'old'))
        self.assertTrue(os.path.isdir(os.path.join(self.own(a), 'gen-0001')))
        self.assertTrue(self.cmd(encrypt.off, a, FakeUI())['done'])  # the repeat finds nothing to switch and asks again
        self.assertEqual((sorted(os.listdir(self.own(a))), self.flag(a)), (['gen-0002'], None))

    def test_off_refuses_when_the_key_is_gone(self):
        a = self.a
        self.assertTrue(self.on(a)['done'])
        crypto.save_key(crypto.settings_path(a.db), None)
        ui = FakeUI()
        with self.assertRaises(ValueError) as cm:
            self.cmd(encrypt.off, a, ui)
        self.assertEqual(str(cm.exception), t('key_missing'))
        self.assertEqual((ui.asked, crypto.is_encrypted(a.db), cgsync.generation(self.own(a)).cipher), ([], True, 'sqlcipher'))

    def test_off_converts_the_backup_too(self):
        a = self.a
        backup = self.old_backup(a)
        self.assertTrue(self.on(a)['done'])
        self.assertTrue(crypto.is_encrypted(backup))
        self.assertTrue(self.cmd(encrypt.off, a, FakeUI())['done'])
        self.assertFalse(crypto.is_encrypted(backup))
        conn = sqlite3.connect(backup)
        self.assertEqual(conn.execute('SELECT text FROM chunks').fetchall(), [('старый ёж',)])
        conn.close()

    def test_the_cipher_is_written_beside_the_key_on_every_run_and_off_leaves_it(self):  # the job reads the file only: it must seal as on and join did
        a, b = self.a, self.b

        def fields(c=a):
            with open(crypto.settings_path(c.db), encoding='utf-8-sig') as f:
                return json.load(f)

        self.write_settings(crypto.settings_path(a.db), {'language': 'ru', 'shareCipher': 'sqlcipher'})
        self.assertTrue(self.cmd(encrypt.on, a, FakeUI(), 'cryptography')['done'])
        key = self.key(a)
        self.assertEqual((fields()['shareCipher'], fields()['language']), ('cryptography', 'ru'))  # a key made in this run: one write
        self.assertEqual(cgsync.generation(self.own(a)).cipher, 'cryptography')
        self.assertTrue(self.cmd(encrypt.on, a, FakeUI(), 'sqlcipher')['done'])  # the key is there: the field is written all the same
        self.assertEqual((fields()['shareCipher'], fields()['language'], self.key(a)), ('sqlcipher', 'ru', key))
        self.assertTrue(self.cmd(encrypt.join, b, FakeUI(code=crypto.make_code(key)), 'cryptography')['done'])
        self.assertEqual(fields(b)['shareCipher'], 'cryptography')
        self.assertTrue(self.cmd(encrypt.off, a, FakeUI())['done'])
        self.assertEqual((fields(), self.key(a)), ({'language': 'ru', 'shareCipher': 'sqlcipher'}, None))  # off takes the key out and nothing else

    # status and refusals
    def test_status(self):
        a = self.a
        self.assertEqual(encrypt.status(os.path.join(self.root, 'none', 'index.db')), {'on': False, 'db': 'none', 'key': 'missing', 'notes': []})
        settings = crypto.settings_path(a.db)
        with mock.patch('sqlite3.connect', side_effect=AssertionError), mock.patch('crypto.open_db', side_effect=AssertionError):
            self.assertEqual(encrypt.status(a.db), {'on': False, 'db': 'plain', 'key': 'missing', 'notes': []})
            crypto.save_key(settings, secrets.token_bytes(32))
            self.assertEqual(encrypt.status(a.db), {'on': True, 'db': 'plain', 'key': 'ok', 'notes': [t('note_index_plain')]})
            self.write_settings(settings, {'encryptionKey': 'dpapi:AAAA'})
            self.assertEqual(encrypt.status(a.db), {'on': True, 'db': 'plain', 'key': 'unreadable', 'notes': [t('key_unreadable')]})
            self.write_settings(settings, ['not an object'])
            self.assertEqual(encrypt.status(a.db)['notes'], [t('settings_broken', path=settings)])
        self.write_settings(settings, {})
        self.assertTrue(self.on(a)['done'])
        self.assertEqual(encrypt.status(a.db), {'on': True, 'db': 'encrypted', 'key': 'ok', 'notes': []})
        crypto.save_key(settings, None)
        self.assertEqual(encrypt.status(a.db), {'on': True, 'db': 'encrypted', 'key': 'missing', 'notes': [t('key_missing')]})

    def test_unsupported_and_missing_package(self):
        a = self.a
        ui = FakeUI(code=crypto.make_code(secrets.token_bytes(32)))
        refusals = [(mock.patch.object(encrypt, 'SUPPORTED', False), t('encrypt_unsupported')),
                    (mock.patch('crypto.need', side_effect=ValueError('no package')), 'no package')]
        for patch, text in refusals:
            for fn in (encrypt.on, encrypt.join):
                with patch, self.assertRaises(ValueError) as cm:
                    self.cmd(fn, a, ui, 'sqlcipher')
                self.assertEqual(str(cm.exception), text)
        self.assertEqual((ui.shown, ui.asked, ui.entries), ([], [], 0))
        self.assertEqual((self.key(a), crypto.is_encrypted(a.db), os.listdir(self.share)), (None, False, []))


if __name__ == '__main__':
    unittest.main()
