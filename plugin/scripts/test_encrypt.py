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
import sys
import tempfile
import unittest
from unittest import mock
import crypto
import encrypt
import memdb
import sync as cgsync
from test_memdb import search
from test_sync import Computer
from texts import t


class FakeUI:
    """`answers`: what `confirm` says one after the other, "yes" when they run out; `code`: what the entry window gives back (None: cancelled);
    `folder`: what the folder window gives back (None: cancelled)."""

    def __init__(self, answers=(), code=None, folder=None):
        self.answers, self.code, self.folder, self.shown, self.asked, self.entries, self.titles = list(answers), code, folder, [], [], 0, []

    def show_code(self, code):
        self.shown.append(code)

    def confirm(self, text, title=None):
        self.asked.append(text)
        self.titles.append(title)
        return self.answers.pop(0) if self.answers else True

    def ask_code(self):
        self.entries += 1
        return self.code

    def pick_folder(self):
        return self.folder


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

    def reinstall(self, c):
        """Windows installed anew: the index and the key of `c` are gone, its own history in the sync folder is not."""
        c.close()
        os.remove(c.db)
        crypto.save_key(crypto.settings_path(c.db), None)

    def assert_own_history_left(self, c, res, code):
        """`res` of an `off` that could not read the own encrypted generation: nothing was deleted, and the recovery code brings it all back."""
        path = os.path.join(self.own(c), 'gen-0001')
        self.assertEqual((res['done'], res['notes']), (False, [t('note_share_pending'), t('own_share_unread', machine=c.name, path=path)]))
        self.assertEqual(sorted(os.listdir(self.own(c))), ['gen-0001'])
        self.assertIn('batch-00000001.sqlcipher', os.listdir(path))
        self.assertTrue(self.join(c, code)['done'])
        self.assertEqual(c.rows(), 2)

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
        self.assertEqual([h['conv'] for h in search(a.conn, 'ёжик')], ['conv-a'])  # the full-text index came over too
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

    def test_on_after_a_reinstall_is_no_second_key(self):  # the own history in the share is under the old key: its code joins, a new key would orphan it
        a = self.a
        ui = FakeUI()
        self.assertTrue(self.on(a, ui)['done'])
        code = ui.shown[0]
        self.reinstall(a)
        again = FakeUI()
        with self.assertRaises(ValueError) as cm:
            self.on(a, again)
        self.assertEqual(str(cm.exception), t('encrypt_use_join', machine='AIJ-HP'))
        self.assertEqual((again.shown, again.asked, self.key(a), sorted(os.listdir(self.own(a)))), ([], [], None, ['gen-0001']))
        self.assertTrue(self.join(a, code)['done'])
        self.assertEqual(a.rows(), 2)

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
        self.assertTrue(self.on(a)['done'])  # the key is here: a folder that is gone now only leaves the switch pending
        res = self.on(a, share=nowhere)
        self.assertFalse(res['done'])
        self.assertEqual(res['notes'][0], t('note_share_pending'))
        self.assertTrue(res['notes'][1].startswith('the sync folder is unavailable: ' + nowhere), res['notes'])
        self.assertTrue(crypto.is_encrypted(a.db))
        self.assertEqual(self.flag(a), 'pending')

    def test_on_makes_no_key_while_the_sync_folder_cannot_be_seen(self):  # it may hold another computer's key: a second one would split the share
        a = self.a
        nowhere = os.path.join(self.root, 'nowhere')
        ui = FakeUI()
        with self.assertRaises(ValueError) as cm:
            self.on(a, ui, share=nowhere)
        self.assertEqual(str(cm.exception), t('encrypt_share_unreachable', dir=nowhere))
        self.assertEqual((ui.shown, ui.asked, self.key(a), crypto.is_encrypted(a.db)), ([], [], None, False))
        self.assertTrue(self.on(a)['done'])  # once the folder is back

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

    def test_a_write_that_lands_after_the_export_stops_the_swap(self):  # a chat takes none of encrypt's locks: its forget must not be lost
        a = self.a
        real_link, writer = os.link, {}

        def link(src, dst):  # right after the export a chat opens the plain index
            real_link(src, dst)
            w = sqlite3.connect(src, isolation_level=None)
            w.execute('SELECT count(*) FROM forgotten').fetchone()
            writer['conn'] = w

        def sleep(seconds):  # the swap waits for the file; the chat commits its forget and lets go
            w = writer.pop('conn', None)
            if w:
                w.execute("INSERT INTO forgotten VALUES('conv-x', 'owner', 'h', 1)")
                w.close()

        forgotten = lambda: a.conn.execute("SELECT count(*) FROM forgotten WHERE conv = 'conv-x'").fetchone()[0]
        with mock.patch('os.link', link), mock.patch('time.sleep', sleep), self.assertRaises(ValueError) as cm:
            self.on(a)
        self.assertEqual(str(cm.exception), t('encrypt_busy'))
        self.assertEqual((crypto.is_encrypted(a.db), os.path.exists(a.db + '.cg-new'), forgotten()), (False, False, 1))
        self.assertTrue(self.on(a)['done'])  # the repeat exports again, with the forget
        self.assertEqual((crypto.is_encrypted(a.db), forgotten()), (True, 1))

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

    def test_off_after_a_reinstall_leaves_the_own_history_in_the_share(self):
        a = self.a
        ui = FakeUI()
        self.assertTrue(self.on(a, ui)['done'])
        self.reinstall(a)
        again = FakeUI()
        res = self.cmd(encrypt.off, a, again)
        self.assertEqual(again.asked, [])  # no question about deleting the old data: it is not old data yet
        self.assert_own_history_left(a, res, ui.shown[0])

    def test_off_before_the_join_has_read_the_own_history_back_leaves_it(self):
        a = self.a
        ui = FakeUI()
        self.assertTrue(self.on(a, ui)['done'])
        self.reinstall(a)
        self.assertTrue(self.join(a, ui.shown[0], seconds=0)['done'])  # no time to read: the new index holds nothing of its history yet
        self.assertEqual(a.rows(), 0)
        again = FakeUI()
        res = self.cmd(encrypt.off, a, again)
        self.assertEqual(again.asked, [])
        self.assert_own_history_left(a, res, ui.shown[0])

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
        self.assertEqual((fields(), self.key(a)), ({'language': 'ru', 'shareCipher': 'sqlcipher', 'indexCipher': 'sqlcipher'}, None))  # off takes the key out and nothing else

    # the index under EFS
    def efs(self, c, ui=None, cipher='cryptography', **kw):
        return self.cmd(encrypt.on, c, ui or FakeUI(), cipher, index_cipher='efs', **kw)

    def alone(self, c):
        """A sync folder for `c` alone: two computers that each start with a key of their own would refuse each other's."""
        path = os.path.join(self.root, 'share-' + c.name)
        os.makedirs(path)
        return path

    def efs_files(self, c):
        """What EFS is to hold beside the index of `c`: the backup of before stage 2, a handoff, and an empty lock file, which it leaves out."""
        folder = os.path.dirname(c.db)
        backup = self.old_backup(c)
        handoff = os.path.join(folder, 'handoffs', 'S1.md')
        os.makedirs(os.path.dirname(handoff))
        with open(handoff, 'w', encoding='utf-8') as f:
            f.write('передача дел')
        lock = c.db + '.sync.lock'
        open(lock, 'wb').close()
        return folder, backup, handoff, lock

    def assert_plain_16k(self, path, counts=None):
        """An open SQLite file with 16 KB pages that the standard sqlite3 opens, holding `counts` of the index tables when given."""
        self.assertFalse(crypto.is_encrypted(path))
        conn = crypto.plain_db(path)
        try:
            self.assertEqual(conn.execute('PRAGMA page_size').fetchone(), (crypto.INDEX_PAGE,))
            if counts:
                self.assertEqual(self.counts(conn), counts)
        finally:
            conn.close()

    def test_efs_on_from_plain(self):
        a = self.a
        folder, backup, handoff, lock = self.efs_files(a)
        before = self.counts(a.conn)
        self.assertEqual(a.conn.execute('PRAGMA page_size').fetchone(), (4096,))
        ui = FakeUI()
        res = self.efs(a, ui)
        self.assertEqual((res['action'], res['done'], res['notes']), ('on', True, []))
        settings = crypto.settings_path(a.db)
        for p in (folder, a.db, backup, os.path.dirname(handoff), handoff, settings):
            self.assertTrue(crypto.efs_on(p), p)
        self.assertFalse(crypto.efs_on(lock))
        self.assertEqual(ui.asked, [t('dlg_code_saved')])  # no open copy to delete: outside EFS there is none
        self.assertEqual(len(ui.shown), 1)
        self.assert_plain_16k(a.db, before)
        self.assert_plain_16k(backup)
        self.assertEqual([h['conv'] for h in search(a.conn, 'ёжик')], ['conv-a'])
        with open(settings, encoding='utf-8') as f:
            fields = json.load(f)
        self.assertEqual((fields['indexCipher'], fields['shareCipher']), ('efs', 'cryptography'))
        self.assertEqual([os.path.exists(a.db + s) for s in ('.plain', '.cg-new')], [False, False])
        self.assertEqual(encrypt.status(a.db), {'on': True, 'db': 'efs', 'key': 'ok', 'notes': []})
        self.assertEqual(cgsync.generation(self.own(a)).cipher, 'cryptography')

    def test_efs_on_from_sqlcipher(self):
        a = self.a
        self.assertTrue(self.on(a)['done'])
        before = self.counts(a.conn)
        self.assertTrue(all(before.values()))
        ui = FakeUI()
        self.assertTrue(self.efs(a, ui, 'sqlcipher')['done'])
        self.assertEqual((ui.shown, ui.asked), ([], []))  # the key is there; no open copy to ask about
        self.assertTrue(crypto.efs_on(a.db) and crypto.efs_on(os.path.dirname(a.db)))
        self.assert_plain_16k(a.db, before)
        self.assertEqual([os.path.exists(a.db + s) for s in ('.plain', '.cg-new')], [False, False])
        self.assertEqual(encrypt.status(a.db)['db'], 'efs')

    def test_efs_on_asks_about_the_open_copy_an_earlier_on_left(self):  # "No" to the question, then EFS: /cg-health says to repeat on, so on must be able to delete it
        a = self.a
        plain = a.db + '.plain'
        self.assertEqual(self.on(a, FakeUI([True, False]))['notes'], [t('note_plain_kept', path=plain)])
        ui = FakeUI([False])
        res = self.efs(a, ui, 'sqlcipher')
        self.assertEqual((res['done'], res['notes'], ui.asked), (False, [t('note_plain_kept', path=plain)], [t('dlg_delete_plain', path=plain)]))
        self.assertEqual((os.path.exists(plain), encrypt.status(a.db)['notes']), (True, [t('note_plain_kept', path=plain)]))
        again = FakeUI()
        self.assertTrue(self.efs(a, again, 'sqlcipher')['done'])
        self.assertEqual((os.path.exists(plain), again.asked), (False, [t('dlg_delete_plain', path=plain)]))
        self.assertEqual(encrypt.status(a.db), {'on': True, 'db': 'efs', 'key': 'ok', 'notes': []})

    def test_efs_on_unsupported_changes_nothing(self):
        a = self.a
        folder = os.path.dirname(a.db)
        err = OSError(0, 'The request is not supported', None, 50)  # what a Windows without EFS answers
        no_drive = OSError(0, 'The device is not ready', None, 21)  # a drive that is not there or not ready: told, never a traceback
        refusals = [(mock.patch.object(crypto, 'efs_supported', return_value=False), t('efs_unsupported', dir=folder)),
                    (mock.patch.object(crypto, 'efs_supported', side_effect=no_drive), t('efs_failed', dir=folder, e=no_drive)),
                    (mock.patch.object(crypto, 'efs_encrypt', side_effect=err), t('efs_failed', dir=folder, e=err))]
        for patch, text in refusals:
            for fn in (encrypt.on, encrypt.join):
                ui = FakeUI(code=crypto.make_code(secrets.token_bytes(32)))
                with patch, self.assertRaises(ValueError) as cm:
                    self.cmd(fn, a, ui, 'cryptography', index_cipher='efs')
                self.assertEqual(str(cm.exception), text)
                self.assertEqual((ui.shown, ui.asked, ui.entries), ([], [], 0))
                self.assertEqual((os.path.exists(crypto.settings_path(a.db)), os.listdir(self.share)), (False, []))
                self.assertEqual([crypto.efs_on(p) for p in (folder, a.db)], [False, False])

    def apart(self, c):
        """The mod's home folder is one of its own and the index of `c` lies in another folder, as after /cg-index-dir: (home, that folder)."""
        home, folder = os.path.join(self.root, 'home-' + c.name), os.path.dirname(c.db)
        os.makedirs(home)
        for p in (mock.patch.object(crypto, 'HOME_DIR', home), mock.patch.object(crypto, 'INDEX_DIRS', {crypto.norm(folder)})):
            p.start()
            self.addCleanup(p.stop)
        return home, folder

    def test_efs_refuses_an_index_folder_that_holds_files_of_the_owner(self):  # EFS walks the whole folder: on would encrypt them, off take EFS off
        a = self.a
        home, folder = self.apart(a)
        report = os.path.join(folder, 'project', 'report.txt')
        os.makedirs(os.path.dirname(report))
        with open(report, 'w', encoding='utf-8') as f:
            f.write('the owner\'s file')
        for fn in (encrypt.on, encrypt.join):
            ui = FakeUI(code=crypto.make_code(secrets.token_bytes(32)))
            with self.assertRaises(ValueError) as cm:
                self.cmd(fn, a, ui, 'cryptography', index_cipher='efs')
            self.assertEqual(str(cm.exception), t('efs_foreign_files', dir=folder))
            self.assertEqual((ui.shown, ui.asked, ui.entries), ([], [], 0))  # before the code window
            self.assertEqual((os.path.exists(crypto.settings_path(a.db)), os.listdir(self.share)), (False, []))  # no key, no ciphers
            self.assertEqual([crypto.efs_on(p) for p in (home, folder, os.path.dirname(report), report, a.db)], [False] * 5)
            with open(report, encoding='utf-8') as f:
                self.assertEqual(f.read(), 'the owner\'s file')

    def test_efs_in_an_index_folder_of_its_own_beside_a_home_folder(self):  # the names the mod makes there are no foreign files
        a = self.a
        home, folder = self.apart(a)
        folder, backup, handoff, lock = self.efs_files(a)
        job = os.path.join(folder, 'job.xml')
        with open(job, 'w', encoding='utf-8') as f:
            f.write('<Task/>')
        self.assertTrue(self.efs(a)['done'])
        paths = (home, crypto.settings_path(a.db), folder, a.db, backup, os.path.dirname(handoff), handoff, job)
        self.assertEqual([crypto.efs_on(p) for p in paths], [True] * len(paths))
        self.assertTrue(self.cmd(encrypt.off, a, FakeUI())['done'])
        self.assertEqual([crypto.efs_on(p) for p in paths], [False] * len(paths))

    def busy(self, times):
        """An efs_encrypt that finds the index in use `times` times (a sharing violation, 32); `.calls` counts them."""
        real = crypto.efs_encrypt

        def fn(path):
            if os.path.basename(path) == 'index.db' and len(fn.calls) < times:
                fn.calls.append(path)
                raise OSError(0, 'in use', None, 32)
            return real(path)

        fn.calls = []
        return fn

    def test_efs_on_file_busy(self):
        a = self.a
        before, ui = self.counts(a.conn), FakeUI()
        forever = self.busy(10 ** 6)
        with mock.patch.object(crypto, 'efs_encrypt', forever), mock.patch('time.sleep'), self.assertRaises(ValueError) as cm:
            self.efs(a, ui)
        self.assertEqual(str(cm.exception), t('encrypt_busy'))
        self.assertEqual(len(forever.calls), 100)  # 10 s of waiting at 0.1 s
        self.assertEqual((crypto.is_encrypted(a.db), crypto.efs_on(a.db), self.counts(a.conn), len(ui.shown)), (False, False, before, 1))
        self.assertIsNotNone(self.key(a))  # the key is kept: the repeat goes on from here, with no second code
        waits, again = self.busy(3), FakeUI()
        with mock.patch.object(crypto, 'efs_encrypt', waits), mock.patch('time.sleep'):
            self.assertTrue(self.efs(a, again)['done'])  # and a file that is let go of in time is no failure
        self.assertEqual((len(waits.calls), again.shown, crypto.efs_on(a.db)), (3, [], True))
        self.assert_plain_16k(a.db, before)

    def test_efs_on_repeat_after_cut(self):  # the folder is under EFS, the index is still SQLCipher, a half-made copy lies beside it
        a = self.a
        self.assertTrue(self.on(a)['done'])
        before = self.counts(a.conn)
        a.close()
        crypto.efs_encrypt(os.path.dirname(a.db))
        left = [a.db + '.cg-new', a.db + '.cg-new-journal']
        for p in left:
            with open(p, 'wb') as f:
                f.write(b'plain text of the index')
        self.assertTrue(self.efs(a, cipher='sqlcipher')['done'])
        self.assertEqual([os.path.exists(p) for p in left], [False, False])
        self.assert_plain_16k(a.db, before)
        self.assertTrue(crypto.efs_on(a.db))

    def test_efs_join_never_imports_sqlcipher3(self):  # Smart App Control: the package is there and cannot be loaded
        a, b = self.a, self.b
        with mock.patch.dict(sys.modules, {'sqlcipher3': None}):  # an import of it is an ImportError
            ui = FakeUI()
            self.assertTrue(self.efs(a, ui)['done'])
            key = self.key(a)
            self.assertTrue(self.cmd(encrypt.join, b, FakeUI(code=ui.shown[0]), 'cryptography', index_cipher='efs')['done'])
            self.assertEqual((self.key(b), crypto.efs_on(b.db)), (key, True))
            self.assert_plain_16k(b.db)
            self.assertEqual(b.rows(), 4)  # B took A's records in
            self.assertEqual(b.sync(key=key, cipher='cryptography')['errors'], [])
            memdb.connect(b.db).close()
        self.assertEqual(cgsync.generation(self.own(b)).cipher, 'cryptography')

    def test_efs_off(self):
        a = self.a
        folder, backup, handoff, lock = self.efs_files(a)
        self.assertTrue(self.efs(a)['done'])
        before = self.counts(a.conn)
        res = self.cmd(encrypt.off, a, FakeUI())
        self.assertEqual((res['action'], res['done'], res['notes']), ('off', True, []))
        for p in (folder, a.db, backup, os.path.dirname(handoff), handoff, crypto.settings_path(a.db)):
            self.assertFalse(crypto.efs_on(p), p)
        self.assertIsNone(self.key(a))
        conn = sqlite3.connect(a.db)
        self.assertEqual(self.counts(conn), before)
        conn.close()
        self.assertEqual(self.counts(a.conn), before)
        self.assertEqual(encrypt.status(a.db), {'on': False, 'db': 'plain', 'key': 'missing', 'notes': []})

    def efs_paths(self, c, folder, backup, handoff):
        return (folder, c.db, backup, os.path.dirname(handoff), handoff, crypto.settings_path(c.db))

    def page_size(self, path):
        conn = sqlite3.connect(path)
        try:
            return conn.execute('PRAGMA page_size').fetchone()[0]
        finally:
            conn.close()

    def test_efs_off_cut_in_the_walk_is_finished_by_a_repeat(self):  # the top folder is the last to go: while anything is left, a repeat still sees EFS
        a = self.a
        folder, backup, handoff, lock = self.efs_files(a)
        paths = self.efs_paths(a, folder, backup, handoff)
        self.assertTrue(self.efs(a)['done'])
        real = crypto.efs_decrypt

        def busy(path):
            if os.path.basename(path) == 'index.db':
                raise OSError(0, 'in use', None, 32)
            return real(path)

        with mock.patch.object(crypto, 'efs_decrypt', busy), mock.patch('time.sleep'), self.assertRaises(ValueError) as cm:
            self.cmd(encrypt.off, a, FakeUI())
        self.assertEqual(str(cm.exception), t('encrypt_busy'))
        self.assertTrue(crypto.efs_on(folder) and crypto.efs_on(a.db))
        self.assertIsNotNone(self.key(a))  # the key goes last: the repeat is an `off` like the first
        self.assertTrue(self.cmd(encrypt.off, a, FakeUI())['done'])
        self.assertEqual([crypto.efs_on(p) for p in paths], [False] * len(paths))
        self.assertIsNone(self.key(a))

    def test_efs_off_finds_an_encrypted_file_in_a_plain_folder(self):  # the folder was decrypted by hand
        a = self.a
        self.assertTrue(self.efs(a)['done'])
        crypto.efs_decrypt(os.path.dirname(a.db))
        self.assertTrue(crypto.efs_on(a.db))
        self.assertTrue(self.cmd(encrypt.off, a, FakeUI())['done'])
        self.assertFalse(crypto.efs_on(a.db))

    def test_efs_off_with_no_index_makes_no_new_one_under_efs(self):  # the connect of off sees the key and indexCipher efs of the settings
        a = self.a
        folder, backup, handoff, lock = self.efs_files(a)
        self.assertTrue(self.efs(a)['done'])
        a.close()
        os.remove(a.db)
        res = encrypt.off(a.db, None, a.name, FakeUI(), memory_dir=a.memory, project_cwd=a.cwd)
        self.assertEqual((res['done'], self.key(a)), (True, None))
        for p in self.efs_paths(a, folder, backup, handoff):
            self.assertFalse(crypto.efs_on(p), p)
        self.assertEqual(encrypt.status(a.db), {'on': False, 'db': 'plain', 'key': 'missing', 'notes': []})

    def test_efs_a_folder_that_cannot_be_listed_is_told_and_nothing_is_converted(self):
        a = self.a
        folder, backup, handoff, lock = self.efs_files(a)
        sub, real = os.path.dirname(handoff), os.scandir
        err = PermissionError(13, 'Access is denied', sub, 5)

        def scandir(path):
            if os.path.basename(os.fspath(path)) == 'handoffs':
                raise err
            return real(path)

        with mock.patch('os.scandir', scandir), self.assertRaises(ValueError) as cm:
            self.efs(a)
        self.assertEqual(str(cm.exception), t('efs_failed', dir=sub, e=err))
        self.assertEqual(self.page_size(a.db), 4096)

    def test_efs_a_folder_that_is_not_there_is_nothing_to_walk(self):
        db = os.path.join(self.root, 'nowhere', 'index.db')
        encrypt._efs_all(db, crypto.efs_encrypt, True)
        self.assertFalse(os.path.exists(os.path.dirname(db)))

    def test_efs_a_file_that_is_gone_between_the_walk_and_the_call_is_skipped(self):  # a chat's journal, say
        a = self.a
        folder, backup, handoff, lock = self.efs_files(a)
        real = crypto.efs_encrypt

        def encrypt_file(path):
            if os.path.basename(path) == 'S1.md':
                os.remove(path)
            return real(path)

        with mock.patch.object(crypto, 'efs_encrypt', encrypt_file):
            self.assertTrue(self.efs(a)['done'])
        self.assertFalse(os.path.exists(handoff))
        self.assertTrue(crypto.efs_on(backup))
        self.assert_plain_16k(a.db)

    def test_nothing_plain_is_written_while_the_folder_is_not_under_efs(self):
        a = self.a
        with self.assertRaises(ValueError) as cm:
            encrypt._to_efs(a.db, None)
        self.assertEqual(str(cm.exception), t('efs_unsupported', dir=os.path.dirname(a.db)))
        self.assertEqual(self.page_size(a.db), 4096)

    def test_a_vacuum_against_a_busy_index_is_told_as_busy(self):
        a = self.a
        crypto.efs_encrypt(os.path.dirname(a.db))  # _to_efs works in a folder that EFS holds
        real = crypto.plain_db
        holder = sqlite3.connect(a.db, isolation_level=None)
        holder.execute('BEGIN IMMEDIATE')  # a chat takes none of our locks: it can hold the write lock
        try:
            with mock.patch.object(crypto, 'plain_db', lambda p: real(p, timeout=0.05)), self.assertRaises(ValueError) as cm:
                encrypt._to_efs(a.db, None)
        finally:
            holder.close()
        self.assertEqual(str(cm.exception), t('encrypt_busy'))
        self.assertEqual(self.page_size(a.db), 4096)

    def test_a_sqlcipher_index_held_by_a_writer_is_told_as_busy_when_it_goes_to_efs(self):
        a = self.a
        self.assertTrue(self.on(a)['done'])
        before, key = self.counts(a.conn), self.key(a)
        a.close()
        encrypt._efs_all(a.db, crypto.efs_encrypt, True)  # the cut of an earlier run: all of it under EFS, the index still SQLCipher
        real = crypto.open_db
        holder = crypto.open_db(a.db, key, page=crypto.INDEX_PAGE)
        holder.execute('BEGIN IMMEDIATE')  # a chat takes none of our locks: it can hold the write lock
        try:
            with mock.patch.object(crypto, 'open_db', lambda p, k, **kw: real(p, k, **dict(kw, timeout=0.05))), self.assertRaises(ValueError) as cm:
                self.efs(a, cipher='sqlcipher')
        finally:
            holder.close()
        self.assertEqual(str(cm.exception), t('encrypt_busy'))
        self.assertEqual((crypto.is_encrypted(a.db), os.path.exists(a.db + '.cg-new'), self.counts(a.conn)), (True, False, before))
        self.assertTrue(self.efs(a, cipher='sqlcipher')['done'])  # the repeat goes on from here
        self.assert_plain_16k(a.db, before)

    def test_off_without_efs_leaves_the_folders_alone(self):
        a = self.a
        self.assertTrue(self.on(a)['done'])
        with mock.patch.object(encrypt, '_efs_all', side_effect=AssertionError):
            self.assertTrue(self.cmd(encrypt.off, a, FakeUI())['done'])

    def test_efs_to_sqlcipher(self):
        a = self.a
        self.assertTrue(self.efs(a, cipher='sqlcipher')['done'])
        before, key = self.counts(a.conn), self.key(a)
        ui = FakeUI()
        self.assertTrue(self.on(a, ui)['done'])  # sqlcipher for the index too
        self.assertTrue(crypto.is_encrypted(a.db))
        self.assert_index_pages(a.db, key)
        self.assertEqual(self.counts(a.conn), before)
        self.assertTrue(crypto.efs_on(os.path.dirname(a.db)))  # the folder stays under EFS: it does no harm
        self.assertEqual(ui.asked, [t('dlg_delete_plain', path=a.db + '.plain')])
        with open(crypto.settings_path(a.db), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['indexCipher'], 'sqlcipher')
        self.assertEqual(encrypt.status(a.db)['db'], 'sqlcipher')

    def test_a_plain_file_opened_through_sqlcipher_keeps_temp_in_memory(self):
        conn = encrypt._open(self.a.db, None)
        try:
            self.assertEqual(conn.execute('PRAGMA temp_store').fetchone()[0], 2)
        finally:
            conn.close()

    def test_only_sqlcipher_asks_for_its_package(self):
        a, b = self.a, self.b
        alone = self.alone(b)
        self.assertTrue(self.on(b, share=alone)['done'])
        for c, share, want in ((a, None, False), (b, alone, True)):  # the index of b is SQLCipher: it is read to be converted
            needed = []
            with mock.patch.object(crypto, 'need', side_effect=needed.append):
                self.assertTrue(self.efs(c, share=share)['done'])
            self.assertEqual('sqlcipher' in needed, want, c.name)

    def test_status_methods(self):
        a, b = self.a, self.b
        settings = crypto.settings_path(a.db)
        self.assertEqual(encrypt.status(os.path.join(self.root, 'none', 'index.db'))['db'], 'none')
        self.assertEqual(encrypt.status(a.db), {'on': False, 'db': 'plain', 'key': 'missing', 'notes': []})
        crypto.save_key(settings, secrets.token_bytes(32))  # a key and an index that EFS does not hold
        self.assertEqual(encrypt.status(a.db), {'on': True, 'db': 'plain', 'key': 'ok', 'notes': [t('note_index_plain')]})
        crypto.save_key(settings, None)
        self.assertTrue(self.efs(a)['done'])
        self.assertEqual(encrypt.status(a.db), {'on': True, 'db': 'efs', 'key': 'ok', 'notes': []})
        crypto.save_key(settings, None)
        self.assertEqual(encrypt.status(a.db), {'on': True, 'db': 'efs', 'key': 'missing', 'notes': []})
        self.assertTrue(self.on(b, share=self.alone(b))['done'])
        self.assertEqual(encrypt.status(b.db), {'on': True, 'db': 'sqlcipher', 'key': 'ok', 'notes': []})

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
        self.assertEqual(encrypt.status(a.db), {'on': True, 'db': 'sqlcipher', 'key': 'ok', 'notes': []})
        crypto.save_key(settings, None)
        self.assertEqual(encrypt.status(a.db), {'on': True, 'db': 'sqlcipher', 'key': 'missing', 'notes': [t('key_missing')]})

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
