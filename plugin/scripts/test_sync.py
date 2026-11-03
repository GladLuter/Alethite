"""Stage 2 acceptance (§9): two computers, each with its own home, transcripts and index, and a temporary folder for the network share.
Every transcript here is synthetic."""
import base64
import gzip
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import crypto
import memdb
import sync as cgsync
from memdb import connect, update
from test_memdb import TS, claude_line, owner_line, search
from test_vectors import fake
from texts import t
from transcripts import project_key
from vectors import DOC_PREFIX, MODEL, embed_pending, hybrid_search, pack, search_vectors

CG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cg.py')
HOMES = {'AIJ-HP': 'C:\\Users\\AIJ', 'MTLAPTOP': 'C:\\Users\\gladl', 'THIRD': 'C:\\Users\\third'}


class Computer:
    """One of the owner's computers: its transcripts, its index, its auto-memory folder of the project, and what it asked the embedder."""

    def __init__(self, root, share, name):
        self.name, self.share, self.home = name, share, HOMES[name]
        self.cwd = self.home + '\\work\\proj'  # one project, inside the profile: its path differs between the computers
        self.dir = os.path.join(root, name)
        self.projects = os.path.join(self.dir, 'projects')
        self.memory = os.path.join(self.projects, 'C--work-proj', 'memory')
        os.makedirs(self.memory)
        self.db = os.path.join(self.dir, 'cacheguard', 'index.db')
        self.conn = None
        self.embedded = []
        self.open()

    def open(self):
        if self.conn:
            self.conn.close()
        self.conn = connect(self.db, home=self.home)

    def close(self):
        self.conn.close()

    def write(self, conv, objs, mode='wb'):
        path = os.path.join(self.projects, 'C--work-proj', conv + '.jsonl')
        with open(path, mode) as f:
            f.write(''.join(json.dumps(o, ensure_ascii=False) + '\n' for o in objs).encode('utf-8'))
        return path

    def say(self, conv, *texts, mode='wb'):
        self.write(conv, [owner_line(t, cwd=self.cwd) if i % 2 == 0 else claude_line(t, cwd=self.cwd) for i, t in enumerate(texts)], mode)

    def index(self, **kw):
        return update(self.conn, self.projects, home=self.home, **kw)

    def spy(self, texts, model, timeout=None):
        self.embedded += texts
        return fake(texts)

    def embed(self, model=MODEL):
        return embed_pending(self.conn, model, self.spy)

    def sync(self, model=MODEL, memory=True, **kw):
        return cgsync.sync(self.conn, self.share, self.name, model, kw.pop('max_seconds', 60), self.memory if memory else None, self.cwd,
                           self.home, digest=kw.pop('digest', lambda m: None), **kw)

    def q(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()[0]

    def rows(self):
        return self.q('SELECT count(*) FROM chunks')

    def vecs(self):
        return self.q('SELECT count(*) FROM vecs')

    def texts(self):
        return sorted(r[0] for r in self.conn.execute('SELECT text FROM chunks'))

    def batches(self):
        return sorted(cgsync._batches(os.path.join(self.share, self.name)))

    def batch_lines(self, n):
        with gzip.open(os.path.join(self.share, self.name, 'batch-%08d.jsonl.gz' % n), 'rt', encoding='utf-8') as f:
            return [json.loads(l) for l in f if l.strip()]

    def fact(self, name, body, description='d'):
        with open(os.path.join(self.memory, name), 'w', encoding='utf-8', newline='\n') as f:
            f.write('---\nname: %s\ndescription: %s\ntype: feedback\n---\n%s\n' % (name[:-3], description, body))

    def read(self, name):
        with open(os.path.join(self.memory, name), encoding='utf-8') as f:
            return f.read()


class SyncTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = d.name
        self.share = os.path.join(d.name, 'share')
        os.makedirs(self.share)
        self.a = self.computer('AIJ-HP')
        self.b = self.computer('MTLAPTOP')
        self.a.say('SA', 'ёжик в тумане на i3', 'ответ про ёжика')
        self.b.say('SB', 'кэш протух на ноутбуке', 'ответ про кэш')

    def computer(self, name):
        c = Computer(self.root, self.share, name)
        self.addCleanup(c.close)
        return c

    def ready(self, *cs):
        for c in cs:
            c.index()
            c.embed()

    def exchange(self):
        self.ready(self.a, self.b)
        ra, rb, ra2 = self.a.sync(), self.b.sync(), self.a.sync()
        for r in (ra, rb, ra2):
            self.assertEqual(r['errors'], [])
        return ra, rb, ra2

    # 1. exchange
    def test_both_indexes_hold_both_after_a_sync_with_the_vectors(self):
        ra, rb, ra2 = self.exchange()
        self.assertEqual((ra['pushed'], ra['pulled'], rb['pushed'], rb['pulled'], ra2['pulled']), (2, 0, 2, 2, 2))
        self.assertEqual((ra['vectors']['pushed'], rb['vectors']['pulled'], ra2['vectors']['pulled']), (2, 2, 2))
        self.assertEqual((rb['peers'], ra2['peers']), ({'AIJ-HP': 1}, {'MTLAPTOP': 1}))
        self.assertEqual(self.a.texts(), self.b.texts())
        self.assertEqual((self.a.rows(), self.a.vecs(), self.b.rows(), self.b.vecs()), (4, 4, 4, 4))
        # the laptop's conversation is found on the i3 by its words and by its vectors
        self.assertEqual(search(self.a.conn, 'ноутбуке')[0]['conv'], 'SB')
        sb = self.a.q("SELECT rowid FROM chunks WHERE text = 'кэш протух на ноутбуке'")
        self.assertEqual(search_vectors(self.a.conn, pack(fake(['протух ноутбуке'])[0]), None)[0][0], sb)
        hits, state = hybrid_search(self.a.conn, 'кэш протух', embed_fn=fake)
        self.assertEqual((state, hits[0]['conv']), ('ok', 'SB'))
        # neither computer embedded the other's records, now or later
        self.assertFalse(any('ёжик' in t for t in self.b.embedded))
        self.assertFalse(any('ноутбук' in t for t in self.a.embedded))
        self.assertEqual((self.a.embed()['done'], self.b.embed()['done']), (0, 0))
        # each record is ours or theirs: /al-health tells them apart
        self.assertEqual(cgsync.status(self.a.conn, 'AIJ-HP')['origins'], {'AIJ-HP': 2, 'MTLAPTOP': 2})

    # 2. repeat
    def test_a_second_sync_changes_nothing(self):
        self.exchange()
        before = [(c.rows(), c.vecs(), c.batches()) for c in (self.a, self.b)]
        for c in (self.a, self.b, self.a, self.b):
            r = c.sync()
            self.assertEqual((r['pushed'], r['pulled'], r['vectors'], r['batches'], r['errors']),
                             (0, 0, {'pushed': 0, 'pulled': 0}, {'pushed': 0, 'pulled': 0}, []))
        self.assertEqual([(c.rows(), c.vecs(), c.batches()) for c in (self.a, self.b)], before)

    def test_new_lines_go_out_in_the_next_batch(self):
        self.exchange()
        self.a.say('SA', 'третье сообщение', mode='ab')
        self.ready(self.a)
        self.assertEqual(self.a.sync()['pushed'], 1)
        self.assertEqual(self.a.batches(), [1, 2])
        self.assertEqual([l['type'] for l in self.a.batch_lines(2)], ['header', 'conv', 'project', 'chunk', 'vec'])
        self.assertEqual(self.b.sync()['pulled'], 1)
        self.assertEqual(search(self.b.conn, 'третье')[0]['conv'], 'SA')

    # 3. a cut
    def test_a_batch_cut_before_its_rename_is_not_seen_and_is_written_again(self):
        self.ready(self.a)
        real = cgsync._write

        def cut(gen, n, lines, machine, key):
            real(gen, n, lines, machine, key)
            os.rename(os.path.join(gen.path, 'batch-%08d.jsonl.gz' % n), os.path.join(gen.path, 'batch-%08d.jsonl.gz.tmp' % n))
            raise OSError('the cable is out')

        with mock.patch.object(cgsync, '_write', cut):
            r = self.a.sync()
        self.assertIn('the cable is out', r['errors'][0]['error'])
        self.assertEqual((self.a.batches(), os.listdir(os.path.join(self.share, 'AIJ-HP'))), ([], ['batch-00000001.jsonl.gz.tmp']))
        self.assertEqual(self.b.sync()['pulled'], 0)  # a .tmp is no batch
        self.assertEqual(self.a.sync()['pushed'], 2)  # nothing was marked: all of it again, under the same number
        self.assertEqual(sorted(os.listdir(os.path.join(self.share, 'AIJ-HP'))), ['batch-00000001.jsonl.gz'])
        self.assertEqual(self.b.sync()['pulled'], 2)

    def test_a_push_whose_mark_is_lost_is_pushed_again_and_doubles_nothing(self):
        self.ready(self.a)
        real = cgsync._write

        def written_then_lost(gen, n, lines, machine, key):  # the batch is out, then the run dies before the index marks it
            real(gen, n, lines, machine, key)
            raise OSError('the laptop fell asleep')

        with mock.patch.object(cgsync, '_write', written_then_lost):
            self.a.sync()
        self.assertEqual((self.a.batches(), self.a.q('SELECT count(*) FROM keys WHERE pushed = 1')), ([1], 0))
        r = self.a.sync()  # our own folder is read first: batch 1 is ours, its records are marked now and not pushed twice
        self.assertEqual((r['pushed'], r['vectors']['pushed'], r['own_batch'], r['errors']), (0, 0, 1, []))
        self.assertEqual(self.a.batches(), [1])
        self.assertEqual(self.a.q('SELECT count(*) FROM keys WHERE pushed = 1 AND vec_pushed = 1'), 2)
        r = self.b.sync()
        self.assertEqual((r['pulled'], r['vectors']['pulled'], r['batches']['pulled'], r['errors']), (2, 2, 1, []))
        self.b.index()
        self.assertEqual((self.b.rows(), self.b.vecs()), (4, 2))

    def second_writer(self, c):
        """A function that makes a second connection of `c` write at once: True when it got through, False when the index was locked."""
        other = connect(c.db, home=c.home)
        self.addCleanup(other.close)
        other.execute('PRAGMA busy_timeout = 200')

        def write():
            try:
                other.execute("INSERT OR REPLACE INTO meta VALUES('probe', '1')")
                return True
            except sqlite3.OperationalError:
                return False
        return write

    def test_push_writes_share_outside_write_lock(self):
        self.ready(self.a)
        write, real, got = self.second_writer(self.a), cgsync._write, []

        def slow(*args):
            got.append(write())
            real(*args)

        with mock.patch.object(cgsync, '_write', slow):
            r = self.a.sync(memory=False)
        self.assertEqual((got, r['pushed'], r['errors']), ([True], 2, []))

    def test_push_failure_marks_nothing(self):
        self.ready(self.a)
        self.a.conn.execute("INSERT INTO outbox(line) VALUES('{\"type\": \"forget\", \"key\": [\"SA\", 0, \"h\", 0]}')")
        with mock.patch.object(cgsync, '_write', side_effect=OSError('the share is gone')):
            r = self.a.sync(memory=False)
        self.assertIn('the share is gone', r['errors'][0]['error'])
        self.assertEqual((self.a.q('SELECT count(*) FROM keys WHERE pushed = 1'), self.a.q('SELECT count(*) FROM outbox')), (0, 1))
        r = self.a.sync(memory=False)
        self.assertEqual((r['pushed'], r['errors'], self.a.q('SELECT count(*) FROM outbox')), (2, [], 0))

    def test_push_skips_record_replaced_meanwhile(self):
        self.ready(self.a)
        real, calls = cgsync._write, []

        def replaced(gen, n, lines, machine, key):
            calls.append(n)
            if len(calls) == 1:  # the record is rebuilt after the collect, before the mark
                self.a.conn.execute("UPDATE keys SET hash = 'replaced' WHERE id = (SELECT min(id) FROM keys)")
                real(gen, n, lines, machine, key)
            else:
                raise OSError('stop here')

        with mock.patch.object(cgsync, '_write', replaced):
            self.a.sync(memory=False)
        self.assertEqual(self.a.q("SELECT pushed FROM keys WHERE hash = 'replaced'"), 0)
        self.assertEqual(self.a.q("SELECT count(*) FROM keys WHERE hash != 'replaced' AND pushed = 1"), 1)

    def test_push_vector_of_record_replaced_after_its_chunk_waits(self):
        self.ready(self.a)
        real, real_dumps, batches, hit = cgsync._write, cgsync._dumps, [], []

        def dumps(o):
            if o.get('type') == 'conv' and not hit:  # the chunks are read, the vectors not yet
                hit.append(1)
                self.a.conn.execute("UPDATE keys SET hash = 'replaced' WHERE id = (SELECT min(id) FROM keys)")
            return real_dumps(o)

        def write(gen, n, lines, machine, key):
            batches.append([json.loads(l) for l in lines])
            if len(batches) == 1:
                real(gen, n, lines, machine, key)
            else:
                raise OSError('stop here')

        with mock.patch.object(cgsync, '_dumps', dumps), mock.patch.object(cgsync, '_write', write):
            self.a.sync(memory=False)
        chunks = {tuple(l['key']) for l in batches[0] if l['type'] == 'chunk'}
        self.assertTrue(all(tuple(l['key']) in chunks for l in batches[0] if l['type'] == 'vec'))
        self.assertEqual(self.a.q("SELECT pushed + vec_pushed FROM keys WHERE hash = 'replaced'"), 0)

    def test_sync_memory_reads_share_outside_write_lock(self):
        self.a.fact('from_a.md', 'a')
        self.b.fact('from_b.md', 'b')
        self.ready(self.a, self.b)
        self.b.sync()
        write, real, got = self.second_writer(self.a), cgsync._read_facts, []

        def slow(*args):
            got.append(write())
            return real(*args)

        with mock.patch.object(cgsync, '_read_facts', slow):
            r = self.a.sync()
        self.assertEqual((r['errors'], r['memory']['pulled'], got), ([], 1, [True, True]))

    def break_line(self, c, n, line):
        path = os.path.join(c.share, c.name, 'batch-%08d.jsonl.gz' % n)
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            lines = f.read().split('\n')
        lines[line] = lines[line][:-7]
        with gzip.open(path, 'wt', encoding='utf-8') as f:
            f.write('\n'.join(lines))

    def test_a_broken_line_stops_that_computer_only_and_skip_batch_goes_past_it(self):
        c = self.computer('THIRD')
        c.say('SC', 'третий компьютер')
        self.ready(self.b, c)
        self.b.sync()
        self.b.say('SB2', 'вторая пачка ноутбука')
        self.ready(self.b)
        self.b.sync()
        c.sync()
        self.break_line(self.b, 1, 3)  # a chunk line of the laptop's first batch
        r = self.a.sync()
        self.assertEqual(len(r['errors']), 1)
        e = r['errors'][0]
        self.assertEqual((e['machine'], e['batch']), ('MTLAPTOP', 1))
        self.assertIn('skip MTLAPTOP 1', e['error'])
        self.assertEqual(r['peers'], {'MTLAPTOP': 0, 'THIRD': 1})  # the third computer came in; the laptop's mark stays where it was
        self.assertEqual(search(self.a.conn, 'третий')[0]['conv'], 'SC')
        self.assertEqual((search(self.a.conn, 'ноутбуке'), search(self.a.conn, 'вторая')), ([], []))  # nothing of the broken batch, and the next one waits
        self.assertEqual(self.a.sync()['errors'][0]['batch'], 1)  # told on every sync
        peer = lambda: next(p for p in cgsync.status(self.a.conn, 'AIJ-HP')['peers'] if p['machine'] == 'MTLAPTOP')
        self.assertEqual(peer()['error'], e['error'])
        cgsync.skip_batch(self.a.conn, 'MTLAPTOP', 1)
        r = self.a.sync()
        self.assertEqual((r['errors'], r['peers']['MTLAPTOP'], r['pulled']), ([], 2, 1))
        self.assertEqual(search(self.a.conn, 'вторая')[0]['conv'], 'SB2')
        self.assertEqual((peer()['skipped'], peer()['error']), ([1], None))
        with self.assertRaises(ValueError):
            cgsync.skip_batch(self.a.conn, 'MTLAPTOP', 0)

    def test_skip_batch_takes_only_a_known_computer_and_the_batch_it_waits_at(self):  # the review of PR 1
        self.ready(self.b)
        self.b.sync()
        self.b.say('SB2', 'вторая пачка ноутбука')
        self.ready(self.b)
        self.b.sync()
        self.break_line(self.b, 1, 3)
        self.assertEqual(self.a.sync()['errors'][0]['batch'], 1)
        with self.assertRaisesRegex(ValueError, 'MTLAPTOP'):
            cgsync.skip_batch(self.a.conn, 'NOSUCH', 1)  # no phantom computer
        with self.assertRaisesRegex(ValueError, ' 1'):
            cgsync.skip_batch(self.a.conn, 'MTLAPTOP', 2)  # a sound batch is not skipped ahead
        self.assertEqual(self.a.q("SELECT count(*) FROM peers WHERE machine = 'NOSUCH'"), 0)
        cgsync.skip_batch(self.a.conn, 'mtlaptop', 1)  # the name in any case: the one in the folder is meant
        r = self.a.sync()
        self.assertEqual((r['errors'], r['peers']['MTLAPTOP']), ([], 2))

    def test_a_missing_batch_with_later_ones_is_an_error(self):
        self.ready(self.b)
        self.b.sync()
        self.b.say('SB2', 'вторая')
        self.ready(self.b)
        self.b.sync()
        os.remove(os.path.join(self.share, 'MTLAPTOP', 'batch-00000001.jsonl.gz'))
        r = self.a.sync()
        self.assertEqual((r['errors'][0]['batch'], r['pulled']), (1, 0))
        self.assertIn('/al-sync skip MTLAPTOP 1', r['errors'][0]['error'])

    def test_a_hiccup_of_the_share_or_a_locked_index_is_no_broken_batch_and_offers_no_skip(self):  # the next sync simply tries again
        import sqlcipher3  # an encrypted index raises its own class, once it is loaded
        self.ready(self.b)
        self.b.sync()
        for e in (OSError(64, 'network name no longer available'), sqlite3.OperationalError('database is locked'),
                  sqlcipher3.OperationalError('database is locked')):
            with self.subTest(type(e).__name__), mock.patch.object(cgsync, '_import', side_effect=e):
                r = self.a.sync()
                err = r['errors'][0]
                self.assertEqual((len(r['errors']), err['machine'], err['batch']), (1, 'MTLAPTOP', 1))
                self.assertNotIn('skip', err['error'])
                self.assertIn('could not be read now', err['error'])

    def test_a_batch_that_is_no_gzip_or_no_json_is_still_broken_and_offers_its_skip(self):  # BadGzipFile is an OSError too
        self.ready(self.b)
        self.b.sync()
        for name, data in (('no gzip', b'not a gzip'), ('no json', gzip.compress(b'not json\n'))):
            with self.subTest(name):
                with open(os.path.join(self.share, 'MTLAPTOP', 'batch-00000001.jsonl.gz'), 'wb') as f:
                    f.write(data)
                self.assertIn('/al-sync skip MTLAPTOP 1', self.a.sync()['errors'][0]['error'])

    # 4. an unavailable folder
    def test_an_unavailable_folder_is_an_error_and_the_local_memory_works(self):
        self.ready(self.a)
        for gone in (os.path.join(self.root, 'nas-off'), os.path.join(self.share, 'file')):
            if gone.endswith('file'):
                open(gone, 'w').close()
            r = cgsync.sync(self.a.conn, gone, 'AIJ-HP', digest=lambda m: None)
            self.assertEqual((r['pushed'], len(r['errors'])), (0, 1))
            self.assertIn(gone, r['errors'][0]['error'])
        self.assertEqual(search(self.a.conn, 'ёжик')[0]['conv'], 'SA')
        self.assertEqual(hybrid_search(self.a.conn, 'ёжик', embed_fn=fake)[1], 'ok')
        last = json.loads(memdb.meta(self.a.conn, 'sync_last'))  # kept for /al-health
        self.assertEqual((last['errors'], last['stopped']), (1, True))
        self.assertFalse(os.path.exists(os.path.join(self.root, 'nas-off')))  # an absent share is not made on the local disk

    def test_a_folder_without_batches_is_no_computer(self):
        os.makedirs(os.path.join(self.share, 'notes'))
        self.ready(self.a)
        r = self.a.sync()
        self.assertEqual((r['peers'], [p['machine'] for p in cgsync.status(self.a.conn, 'AIJ-HP')['peers']]), ({}, ['AIJ-HP']))

    # 5. no echo
    def test_what_came_from_the_laptop_never_goes_out_from_the_i3(self):
        self.exchange()
        self.a.say('SA', 'ещё на i3', mode='ab')
        self.ready(self.a)
        self.a.sync()
        lines = [l for n in self.a.batches() for l in self.a.batch_lines(n)]
        self.assertTrue(lines)
        self.assertFalse([l for l in lines if 'SB' in json.dumps(l)])

    # 6. another model
    def test_vectors_of_another_model_are_skipped_and_counted_the_texts_come(self):
        self.ready(self.a)
        self.b.index()
        self.b.embed('other')
        self.a.sync()
        r = self.b.sync(model='other')
        self.assertEqual((r['pulled'], r['vectors']['pulled'], r['skipped_vectors'], r['errors']), (2, 0, 2, []))
        self.assertEqual(search(self.b.conn, 'ёжик')[0]['conv'], 'SA')
        self.assertEqual(memdb.meta(self.b.conn, 'embed_model'), 'other')
        self.assertEqual(self.b.embed('other')['done'], 2)  # the laptop embeds them with its own model

    def test_another_build_of_the_same_model_is_only_a_warning(self):
        self.ready(self.a, self.b)
        self.a.sync(digest=lambda m: 'sha256:aaaa')
        r = self.b.sync(digest=lambda m: 'sha256:bbbb')
        self.assertEqual((r['vectors']['pulled'], r['errors'], len(r['warnings'])), (2, [], 1))
        self.assertIn('AIJ-HP', r['warnings'][0])
        self.assertEqual(self.a.batch_lines(1)[0]['digest'], 'sha256:aaaa')

    # 7. one project under two profiles
    def test_the_project_is_one_on_both_computers(self):
        self.exchange()
        for c in (self.a, self.b):
            key = project_key(c.cwd, c.home)
            self.assertEqual(key, '~\\work\\proj')
            self.assertEqual({h['conv'] for h in search(c.conn, 'ответ', project=[key])}, {'SA', 'SB'})
            self.assertEqual(search(c.conn, 'ответ', project=[project_key('C:\\_git\\other', c.home)]), [])

    # 8. short memory
    def test_a_fact_from_the_i3_comes_into_the_laptops_memory(self):
        self.a.fact('feedback_cache.md', 'кэш живёт час')
        self.b.fact('own_fact.md', 'своё')
        with open(os.path.join(self.b.memory, 'MEMORY.md'), 'w', encoding='utf-8') as f:
            f.write('- [own_fact](own_fact.md) — своё')
        ra = self.a.sync()
        self.assertEqual(ra['memory'], {'pushed': 1, 'pulled': 0, 'conflicts': 0})
        rb = self.b.sync()
        self.assertEqual(rb['memory'], {'pushed': 1, 'pulled': 1, 'conflicts': 0})
        self.assertIn('кэш живёт час', self.b.read('feedback_cache.md'))
        self.assertEqual(self.b.read('MEMORY.md'), '- [own_fact](own_fact.md) — своё\n- [feedback_cache](feedback_cache.md) — d\n')
        self.assertEqual(self.a.sync()['memory'], {'pushed': 0, 'pulled': 1, 'conflicts': 0})
        self.assertEqual(self.b.sync()['memory'], {'pushed': 0, 'pulled': 0, 'conflicts': 0})  # what came is not pushed back

    def test_a_fact_of_the_same_name_is_not_overwritten_the_other_goes_beside(self):
        self.a.fact('note.md', 'версия i3')
        self.b.fact('note.md', 'версия ноутбука')
        self.a.sync()
        r = self.b.sync()
        self.assertEqual(r['memory'], {'pushed': 1, 'pulled': 1, 'conflicts': 1})
        self.assertIn('версия ноутбука', self.b.read('note.md'))
        self.assertIn('версия i3', self.b.read('note--AIJ-HP.md'))
        self.assertIn('(note--AIJ-HP.md)', self.b.read('MEMORY.md'))
        self.a.sync()
        self.assertIn('версия i3', self.a.read('note.md'))
        self.assertIn('версия ноутбука', self.a.read('note--MTLAPTOP.md'))
        # the copy with the suffix does not go back
        self.assertEqual(sorted(os.listdir(os.path.join(self.share, 'MTLAPTOP', 'memory', cgsync.project_folder('~\\work\\proj')))), ['note.md'])
        self.assertEqual(self.b.sync()['memory'], {'pushed': 0, 'pulled': 0, 'conflicts': 0})

    def test_a_newer_version_replaces_an_untouched_copy_and_never_an_edited_one(self):
        self.a.fact('f.md', 'v1')
        self.a.sync()
        self.b.sync()
        self.a.fact('f.md', 'v2')
        self.a.sync()
        self.assertEqual(self.b.sync()['memory']['pulled'], 1)
        self.assertIn('v2', self.b.read('f.md'))
        self.assertEqual(self.b.read('MEMORY.md').count('(f.md)'), 1)
        with open(os.path.join(self.b.memory, 'f.md'), 'a', encoding='utf-8') as f:
            f.write('правка владельца\n')
        self.a.fact('f.md', 'v3')
        self.a.sync()
        r = self.b.sync()
        self.assertEqual(r['memory']['conflicts'], 1)
        self.assertIn('правка владельца', self.b.read('f.md'))
        self.assertIn('v3', self.b.read('f--AIJ-HP.md'))

    def test_a_fact_the_owner_deleted_does_not_come_back(self):
        self.a.fact('f.md', 'v1')
        self.a.sync()
        self.b.sync()
        os.remove(os.path.join(self.b.memory, 'f.md'))
        self.assertEqual(self.b.sync()['memory'], {'pushed': 0, 'pulled': 0, 'conflicts': 0})
        self.a.fact('f.md', 'v2')  # not even a newer version
        self.a.sync()
        self.b.sync()
        self.assertFalse(os.path.exists(os.path.join(self.b.memory, 'f.md')))

    def test_a_fact_that_is_already_here_without_a_record_is_still_ours_to_push(self):  # copied by hand, not taken from the laptop
        self.b.fact('f.md', 'v1')
        self.a.fact('f.md', 'v1')
        self.b.sync()
        self.a.sync()
        self.a.fact('f.md', 'v2')
        self.a.sync()
        with open(os.path.join(self.share, 'AIJ-HP', 'memory', cgsync.project_folder('~\\work\\proj'), 'f.md'), encoding='utf-8') as f:
            self.assertIn('v2', f.read())

    def test_no_memory_folder_known_no_memory_step(self):
        self.a.fact('f.md', 'v1')
        self.assertIsNone(self.a.sync(memory=False)['memory'])
        self.assertFalse(os.path.exists(os.path.join(self.share, 'AIJ-HP', 'memory')))
        r = cgsync.sync(self.a.conn, self.share, 'AIJ-HP', memory_dir=os.path.join(self.root, 'nope'), project_cwd=self.a.cwd, digest=lambda m: None)
        self.assertIn('nope', r['errors'][0]['error'])

    # 13. the refinements of §5.7
    def test_a_fresh_index_takes_back_its_records_and_vectors_from_its_own_folder(self):
        self.ready(self.a)
        self.a.sync()
        self.a.close()
        os.remove(self.a.db)  # Windows reinstalled; the transcripts are still there
        self.a.embedded.clear()
        self.a.open()
        r = self.a.sync()
        self.assertEqual((r['pulled'], r['vectors']['pulled'], r['pushed'], r['errors']), (2, 2, 0, []))
        self.assertEqual((self.a.rows(), self.a.vecs()), (2, 2))
        self.assertEqual(self.a.index()['rows'], 0)  # the transcripts add nothing twice
        self.assertEqual(self.a.embed()['done'], 0)  # and nothing is embedded again
        self.assertEqual(self.a.embedded, [])
        self.assertEqual((self.a.sync()['pushed'], self.a.batches()), (0, [1]))  # nor pushed again
        self.assertEqual(cgsync.status(self.a.conn, 'AIJ-HP')['origins'], {'AIJ-HP': 2})

    def test_a_fresh_index_past_a_broken_batch_of_its_own_loses_none_of_the_later_ones(self):
        self.a.say('SBIG', *['сообщение номер %d ' % i + 'я' * 300 for i in range(40)])
        self.ready(self.a)
        with mock.patch.object(cgsync, 'BATCH_BYTES', 3000):
            self.a.sync()
        batches = self.a.batches()
        self.assertGreater(len(batches), 3)
        lost = len(self.batch_texts(self.a, 2))
        self.a.close()
        os.remove(self.a.db)  # Windows reinstalled, and the old transcripts are gone too: the batches are all that is left
        for name in ('SA', 'SBIG'):
            os.remove(os.path.join(self.a.projects, 'C--work-proj', name + '.jsonl'))
        self.a.open()
        self.a.say('SNEW', 'новый разговор после переустановки')
        self.ready(self.a)
        self.break_line(self.a, 2, 3)
        r = self.a.sync()  # stops at its own batch 2, and still pushes the new conversation
        self.assertEqual((r['errors'][0]['batch'], r['pushed']), (2, 1))
        self.assertEqual(self.a.sync()['errors'][0]['batch'], 2)  # the error stays until it is skipped
        cgsync.skip_batch(self.a.conn, 'AIJ-HP', 2)
        r = self.a.sync()
        self.assertEqual(r['errors'], [])
        self.assertEqual(self.a.rows(), 2 + 40 - lost + 1)  # every record but those of the broken batch
        self.assertEqual(self.a.q("SELECT last_batch FROM peers WHERE machine = 'AIJ-HP'"), batches[-1] + 1)  # the new conversation's batch

    def batch_texts(self, c, n):
        return [l['text'] for l in c.batch_lines(n) if l.get('type') == 'chunk']

    def test_a_fresh_index_indexed_first_still_pushes_nothing_twice(self):
        self.ready(self.a)
        self.a.sync()
        self.a.close()
        os.remove(self.a.db)
        self.a.open()
        self.a.index()  # the session start indexes before it syncs
        r = self.a.sync()
        self.assertEqual((r['pulled'], r['vectors']['pulled'], r['pushed'], self.a.batches()), (0, 2, 0, [1]))
        self.assertEqual(self.a.embed()['done'], 0)

    def test_forget_on_the_i3_deletes_on_the_laptop(self):
        self.exchange()
        pos = self.a.q("SELECT pos FROM chunks WHERE text = 'ёжик в тумане на i3'")
        self.assertEqual(memdb.forget(self.a.conn, 'SA', pos), 1)
        self.assertEqual(self.a.sync()['pushed'], 0)
        r = self.b.sync()
        self.assertEqual(r['forgotten'], 1)
        self.assertEqual(search(self.b.conn, 'тумане'), [])
        self.assertEqual((self.b.rows(), self.b.vecs()), (3, 3))
        self.a.close()  # even a fresh index of the i3, filled from its own folder, does not bring it back
        os.remove(self.a.db)
        self.a.open()
        self.a.sync()
        self.a.index(rebuild=True)
        self.assertEqual(search(self.a.conn, 'тумане'), [])

    def test_forget_of_the_laptops_record_on_the_i3_reaches_the_laptop(self):
        self.exchange()
        pos = self.a.q("SELECT pos FROM chunks WHERE text = 'кэш протух на ноутбуке'")
        memdb.forget(self.a.conn, 'SB', pos)
        self.a.sync()
        self.b.sync()
        self.b.index(rebuild=True)  # its own transcript still holds it
        self.assertEqual(search(self.b.conn, 'протух'), [])

    def test_a_fixed_secret_filter_replaces_the_text_on_the_laptop_too(self):
        self.a.say('SA', 'пароль zq-0123456789 тут', 'ок')
        self.exchange()
        self.assertTrue(search(self.b.conn, 'zq'))
        with mock.patch.object(memdb, 'redact', lambda t: t.replace('zq-0123456789', '[скрыто]')):
            self.assertEqual(self.a.index(rebuild=True)['rows'], 1)
        self.a.embed()
        r = self.a.sync()
        self.assertEqual((r['pushed'], r['vectors']['pushed']), (1, 1))
        r = self.b.sync()
        self.assertEqual((r['pulled'], r['forgotten'], r['vectors']['pulled']), (1, 1, 1))
        self.assertEqual(search(self.b.conn, 'zq'), [])
        self.assertEqual(search(self.b.conn, 'пароль')[0]['snippet'].replace('«', '').replace('»', ''), 'пароль [скрыто] тут')
        self.assertEqual((self.b.rows(), self.b.vecs()), (4, 4))

    def test_a_laptop_without_vectors_takes_the_model_of_the_i3_and_wipes_nothing(self):
        self.ready(self.a)
        self.b.index()  # the laptop has never embedded
        self.a.sync()
        r = self.b.sync()
        self.assertEqual((r['vectors']['pulled'], r['skipped_vectors']), (2, 0))
        self.assertEqual(memdb.meta(self.b.conn, 'embed_model'), MODEL)
        self.assertEqual(self.b.embed()['done'], 2)  # its own two; the i3's stay
        self.assertEqual(self.b.vecs(), 4)
        self.assertFalse(any('ёжик' in t for t in self.b.embedded))

    def test_a_laptop_set_to_another_model_does_not_take_the_i3s(self):
        self.ready(self.a)
        self.b.index()
        self.a.sync()
        r = self.b.sync(model='other')
        self.assertEqual((r['vectors']['pulled'], r['skipped_vectors']), (0, 2))
        self.assertIsNone(memdb.meta(self.b.conn, 'embed_model'))

    def test_a_report_demoted_later_is_demoted_on_the_other_computer(self):
        sub = os.path.join(self.a.projects, 'C--work-proj', 'SA', 'subagents')
        os.makedirs(sub)
        with open(os.path.join(sub, 'agent-x.jsonl'), 'w', encoding='utf-8') as f:
            f.write(json.dumps(owner_line('задание', cwd='')) + '\n' + json.dumps(claude_line('итог1', cwd='')) + '\n')
        self.ready(self.a)
        self.a.sync()
        self.b.sync()
        with open(os.path.join(sub, 'agent-x.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(claude_line('итог2', cwd='')) + '\n')
        self.ready(self.a)
        self.a.sync()
        self.b.sync()
        kinds = lambda c: [tuple(r) for r in c.conn.execute("SELECT text, kind FROM chunks WHERE conv = 'agent-x' ORDER BY text")]
        self.assertEqual(kinds(self.b), [('задание', 'task'), ('итог1', 'sub'), ('итог2', 'report')])
        self.assertEqual(kinds(self.b), kinds(self.a))
        self.assertEqual({h['conv'] for h in search(self.b.conn, 'итог2', project='~\\work\\proj')}, {'agent-x'})  # the parent's project

    def test_the_time_cap_leaves_the_rest_for_the_next_sync(self):
        self.ready(self.a)
        ticks = iter(range(100))
        r = self.a.sync(max_seconds=1, now=lambda: next(ticks))  # one look at the clock passes before the push, then it is over
        self.assertEqual((r['pushed'], r['errors']), (0, []))
        self.assertEqual(self.a.sync()['pushed'], 2)

    def test_big_history_goes_in_several_batches(self):
        self.a.say('SBIG', *['сообщение номер %d ' % i + 'я' * 300 for i in range(40)])
        self.ready(self.a)
        with mock.patch.object(cgsync, 'BATCH_BYTES', 3000):
            r = self.a.sync()
        self.assertGreater(r['batches']['pushed'], 3)
        self.assertEqual(r['pushed'], 42)
        r = self.b.sync()
        self.assertEqual((r['pulled'], r['vectors']['pulled'], r['errors']), (42, 42, []))

    # 14. generations of a computer's folder (stage 5): the others read a new one again from its first batch
    def start(self, c):
        return cgsync.start_generation(c.conn, os.path.join(self.share, c.name), c.name, None, None)

    def gen_dir(self, c, n):
        return os.path.join(c.share, c.name, 'gen-%04d' % n)

    def gen_lines(self, c, n):
        out = []
        for b in sorted(cgsync._batches(self.gen_dir(c, n))):
            with gzip.open(os.path.join(self.gen_dir(c, n), 'batch-%08d.jsonl.gz' % b), 'rt', encoding='utf-8') as f:
                out += [json.loads(l) for l in f if l.strip()]
        return out

    def peer_row(self, c, peer):
        return tuple(c.conn.execute('SELECT gen, last_batch, skipped, error FROM peers WHERE machine = ?', (peer,)).fetchone())

    def test_new_generation_is_read_again(self):
        self.exchange()
        self.assertEqual(self.start(self.a), cgsync.Gen(1, self.gen_dir(self.a, 1), None, None))
        r = self.a.sync()
        self.assertEqual(r['errors'], [])
        lines = self.gen_lines(self.a, 1)
        self.assertEqual(sum(l['type'] == 'chunk' for l in lines), 2)  # its own two, not the laptop's
        self.assertEqual({l['gen'] for l in lines if l['type'] == 'header'}, {1})
        before = (self.b.rows(), self.b.vecs())
        r = self.b.sync()
        self.assertEqual((r['errors'], r['pulled'], r['batches']['pulled']), ([], 0, 1))  # read from batch 1; nothing is new in it
        self.assertEqual((self.b.rows(), self.b.vecs()), before)
        self.assertEqual(self.peer_row(self.b, 'AIJ-HP'), (1, 1, '[]', None))
        self.assertEqual(self.b.sync()['batches']['pulled'], 0)  # and once only

    def test_generation_clears_skip(self):
        self.ready(self.a)
        self.a.sync()
        self.break_line(self.a, 1, 3)
        self.assertEqual(self.b.sync()['errors'][0]['batch'], 1)
        cgsync.skip_batch(self.b.conn, 'AIJ-HP', 1)
        self.assertEqual(self.b.sync()['errors'], [])
        self.assertEqual(self.peer_row(self.b, 'AIJ-HP')[:3], (0, 1, '[1]'))
        self.start(self.a)
        self.a.sync()
        r = self.b.sync()  # the new generation holds what the skipped batch did
        self.assertEqual((r['errors'], r['pulled']), ([], 2))
        self.assertEqual(self.peer_row(self.b, 'AIJ-HP'), (1, 1, '[]', None))

    def test_restored_index_reads_own_generation(self):
        self.ready(self.a)
        self.start(self.a)
        self.a.sync()
        self.a.close()
        os.remove(self.a.db)
        self.a.open()
        r = self.a.sync()
        self.assertEqual((r['pulled'], r['vectors']['pulled'], r['pushed'], r['errors']), (2, 2, 0, []))
        self.assertEqual((sorted(cgsync._batches(self.gen_dir(self.a, 1))), os.listdir(os.path.join(self.share, 'AIJ-HP'))), ([1], ['gen-0001']))
        self.assertEqual(self.peer_row(self.a, 'AIJ-HP'), (1, 1, '[]', None))

    def test_new_generation_carries_forgets(self):
        self.exchange()
        pos = self.a.q("SELECT pos FROM chunks WHERE text = 'кэш протух на ноутбуке'")
        memdb.forget(self.a.conn, 'SB', pos)
        self.a.sync()  # the forget is in the batch of generation 0, which a new reader never looks at
        self.start(self.a)
        self.a.sync()
        c = self.computer('THIRD')  # the laptop's batch still holds the record
        r = c.sync()
        self.assertEqual((r['errors'], r['forgotten']), ([], 1))
        self.assertEqual(c.texts(), sorted(['ёжик в тумане на i3', 'ответ про ёжика', 'ответ про кэш']))

    def test_old_generations(self):
        self.a.fact('f.md', 'v1')
        self.exchange()
        own = os.path.join(self.share, 'AIJ-HP')
        self.assertEqual(cgsync.old_generations(own), [])  # generation 0 is the current one
        self.start(self.a)
        self.a.sync()
        names = lambda: sorted(os.path.basename(p) for p in cgsync.old_generations(own))
        self.assertEqual(names(), ['batch-00000001.jsonl.gz', 'memory'])
        open(os.path.join(own, 'batch-00000002.jsonl.gz.tmp'), 'w').close()
        self.start(self.a)
        self.assertEqual(names(), ['batch-00000001.jsonl.gz', 'batch-00000002.jsonl.gz.tmp', 'gen-0001', 'memory'])

    def test_a_start_that_fails_leaves_no_generation_and_changes_nothing(self):
        self.exchange()
        own = os.path.join(self.share, 'AIJ-HP')
        marks = lambda: (self.peer_row(self.a, 'AIJ-HP'), self.a.q('SELECT count(*) FROM keys WHERE pushed = 1'))
        other = connect(self.a.db, home=self.a.home)
        self.addCleanup(other.close)
        other.execute('BEGIN IMMEDIATE')  # another run holds the index
        self.a.conn.execute('PRAGMA busy_timeout = 0')
        with self.assertRaises(memdb.db_errors()):
            self.start(self.a)
        self.assertEqual([n for n in os.listdir(own) if n.startswith('gen-')], [])  # the label waits for the lock
        other.rollback()
        pos = self.a.q("SELECT pos FROM chunks WHERE text = 'кэш протух на ноутбуке'")
        memdb.forget(self.a.conn, 'SB', pos)
        before = marks()
        with mock.patch.object(memdb, 'forget_key', side_effect=RuntimeError('boom')):  # dies after the label is out
            with self.assertRaises(RuntimeError):
                self.start(self.a)
        self.assertEqual((marks(), cgsync.generation(own).n, [n for n in os.listdir(own) if n.startswith('gen-')]), (before, 0, ['gen-0001']))
        self.assertEqual(os.listdir(os.path.join(own, 'gen-0001')), [])  # no label, no half-written one
        self.assertEqual(self.start(self.a).n, 2)  # and the next start goes beyond the empty folder

    def test_a_failed_start_leaves_the_label_that_another_start_wrote(self):
        winner = self.start(self.a)
        with mock.patch.object(cgsync, '_gens', return_value={}):  # a start that did not see the folder of the other one
            with self.assertRaises(FileExistsError):
                self.start(self.a)
        self.assertEqual(cgsync.generation(os.path.join(self.share, 'AIJ-HP')), winner)

    def test_restored_own_records_go_out_again_in_the_next_generation(self):
        self.ready(self.a)
        self.start(self.a)
        self.a.sync()
        self.a.close()
        os.remove(self.a.db)
        self.a.open()
        self.a.sync()  # the records are back from gen-0001, taken in from our own folder: origin is ours, and they are marked pushed
        self.assertEqual(self.a.q("SELECT count(*) FROM keys WHERE origin = 'AIJ-HP' AND pushed = 1"), 2)
        self.start(self.a)
        self.assertEqual(self.a.sync()['errors'], [])
        lines = self.gen_lines(self.a, 2)
        self.assertEqual((sum(l['type'] == 'chunk' for l in lines), sum(l['type'] == 'vec' for l in lines)), (2, 2))

    def test_a_label_that_cannot_be_read_now_is_no_missing_label(self):  # a hiccup of the share must not demote the generation
        self.ready(self.a)
        self.start(self.a)
        self.a.sync()
        self.start(self.a)
        self.a.sync()
        self.b.sync()
        self.assertEqual(self.peer_row(self.b, 'AIJ-HP'), (2, 1, '[]', None))
        self.a.say('SA', 'третье сообщение', mode='ab')
        self.ready(self.a)
        real = open

        def denied(path, *args, **kw):
            if os.path.basename(str(path)) == 'generation.json':
                raise PermissionError(13, 'sharing violation')
            return real(path, *args, **kw)

        with mock.patch('builtins.open', denied):
            ra, rb = self.a.sync(), self.b.sync()
        # we write nothing into the generation before the current one, and the laptop keeps its place in the current one
        self.assertEqual(([e['where'] for e in ra['errors']], ra['pushed'], sorted(cgsync._batches(self.gen_dir(self.a, 2)))), (['sync'], 0, [1]))
        self.assertEqual([(e['where'], e['machine'], 'batch' in e) for e in rb['errors']], [('pull', 'AIJ-HP', False)])
        self.assertEqual(self.peer_row(self.b, 'AIJ-HP')[:3], (2, 1, '[]'))
        self.assertIn('PermissionError', self.peer_row(self.b, 'AIJ-HP')[3])
        self.assertEqual((self.a.sync()['pushed'], sorted(cgsync._batches(self.gen_dir(self.a, 2)))), (1, [1, 2]))
        self.assertEqual((self.b.sync()['errors'], self.peer_row(self.b, 'AIJ-HP')), ([], (2, 2, '[]', None)))

    def test_only_the_one_spelling_of_a_number_names_a_generation_or_a_batch(self):  # else two names are one number
        own = os.path.join(self.root, 'M')
        arabic = lambda s: s.translate({ord(c): 0x660 + int(c) for c in '0123456789'})  # digits that \d matches and int() reads
        for name in ('gen-0000', 'gen-00001', 'gen-0003', 'gen-10000', arabic('gen-0002'), 'gen-1'):
            os.makedirs(os.path.join(own, name))
        for name in ('batch-00000002.jsonl.gz', arabic('batch-00000001.jsonl.gz'), 'batch-00000003.jsonl.gz.tmp', 'batch-1.jsonl.gz'):
            open(os.path.join(own, name), 'w').close()
        self.assertEqual(sorted(cgsync._gens(own)), [3, 10000])
        self.assertEqual(cgsync._batches(own), {2: 'batch-00000002.jsonl.gz'})
        self.assertIsNone(cgsync._GEN.fullmatch('gen-0001\n'))

    def test_a_generation_with_no_readable_label_is_ignored(self):  # the share is untrusted input
        own, fp = os.path.join(self.root, 'M'), 'ab' * 8
        self.assertEqual(cgsync.generation(own), cgsync.Gen(0, own, None, None))  # no folder at all
        good = {'format': 1, 'gen': 2, 'cipher': 'sqlcipher', 'fingerprint': fp}

        def put(n, text):
            os.makedirs(os.path.join(own, 'gen-%04d' % n), exist_ok=True)
            with open(os.path.join(own, 'gen-%04d' % n, 'generation.json'), 'w') as f:
                f.write(text)

        os.makedirs(os.path.join(own, 'gen-0001'))  # no label yet: a start cut short
        self.assertEqual(cgsync.generation(own).n, 0)
        put(1, json.dumps({**good, 'gen': 1, 'cipher': None, 'fingerprint': None}))
        bad = ['', '{', '[]', '"x"', '[' * 5000, '1', json.dumps({**good, 'format': 2}), json.dumps({**good, 'gen': 3}),
               json.dumps({**good, 'cipher': 'rot13'}), json.dumps({**good, 'cipher': ['sqlcipher']}), json.dumps({**good, 'fingerprint': None}),
               json.dumps({**good, 'fingerprint': fp.upper()}), json.dumps({**good, 'fingerprint': fp[:-1]}),
               json.dumps({k: v for k, v in good.items() if k != 'cipher'})]
        for text in bad:
            with self.subTest(text=text[:40]):
                put(2, text)
                self.assertEqual(cgsync.generation(own), cgsync.Gen(1, os.path.join(own, 'gen-0001'), None, None))
        put(2, json.dumps(good))
        self.assertEqual(cgsync.generation(own), cgsync.Gen(2, os.path.join(own, 'gen-0002'), 'sqlcipher', fp))
        os.makedirs(os.path.join(own, 'gen-0007'))  # unreadable, yet the next number goes beyond it
        self.assertEqual(cgsync.start_generation(self.a.conn, own, 'AIJ-HP', 'sqlcipher', b'k' * 32).n, 8)

    def test_a_peer_first_seen_in_a_generation_is_read_once_and_can_skip_a_broken_batch(self):
        self.ready(self.a)
        self.start(self.a)
        self.a.sync()
        self.assertEqual(self.b.sync()['batches']['pulled'], 1)
        self.assertEqual((self.b.sync()['batches']['pulled'], self.peer_row(self.b, 'AIJ-HP')), (0, (1, 1, '[]', None)))
        c = self.computer('THIRD')
        path = os.path.join(self.gen_dir(self.a, 1), 'batch-00000001.jsonl.gz')
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            lines = f.read().split('\n')
        lines[3] = lines[3][:-7]
        with gzip.open(path, 'wt', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        self.assertEqual(c.sync()['errors'][0]['batch'], 1)
        cgsync.skip_batch(c.conn, 'AIJ-HP', 1)  # the mark of the generation must stay, or the next sync would clear the skip
        r = c.sync()
        self.assertEqual((r['errors'], self.peer_row(c, 'AIJ-HP')), ([], (1, 1, '[1]', None)))

    def set_gen(self, path, gen):
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            lines = f.read().split('\n')
        head = json.loads(lines[0])
        head.pop('gen', None)
        if gen is not None:
            head['gen'] = gen
        with gzip.open(path, 'wt', encoding='utf-8') as f:
            f.write('\n'.join([json.dumps(head)] + lines[1:]))

    def test_a_batch_names_its_generation(self):
        self.ready(self.a)
        self.a.sync()
        path = os.path.join(self.share, 'AIJ-HP', 'batch-00000001.jsonl.gz')
        self.set_gen(path, None)  # a batch of before stage 5 has no such field: generation 0
        self.assertEqual((self.b.sync()['errors'], self.b.rows()), ([], 2))
        self.start(self.a)
        self.a.sync()
        self.set_gen(os.path.join(self.gen_dir(self.a, 1), 'batch-00000001.jsonl.gz'), 0)  # a copy from another generation
        r = self.b.sync()
        self.assertEqual((r['errors'][0]['batch'], self.peer_row(self.b, 'AIJ-HP')[1]), (1, 0))
        self.assertIn('AIJ-HP', r['errors'][0]['error'])

    # 15. encrypted batches (stage 5): one key, one of two ciphers; a computer with the key takes nothing plain, one without it nothing sealed
    def pair(self, tag):
        """Two more computers with a share of their own, who said what the ones of setUp did: a test that goes over the ciphers needs one each."""
        root = os.path.join(self.root, tag)
        share = os.path.join(root, 'share')
        os.makedirs(share)
        a, b = Computer(root, share, 'AIJ-HP'), Computer(root, share, 'MTLAPTOP')
        for c in (a, b):
            self.addCleanup(c.close)
        a.say('conv-of-aij-hp', 'ёжик в тумане на i3', 'ответ про ёжика')
        b.say('conv-of-mtlaptop', 'кэш протух на ноутбуке', 'ответ про кэш')
        self.ready(a, b)
        return a, b

    def swap(self, a, b, key, cipher='sqlcipher'):
        out = [c.sync(memory=False, key=key, cipher=cipher) for c in (a, b, a)]
        self.assertEqual([r['errors'] for r in out], [[], [], []])

    def test_encrypted_roundtrip(self):
        key = secrets.token_bytes(32)
        for cipher in crypto.CIPHERS:
            with self.subTest(cipher=cipher):
                a, b = self.pair(cipher)
                self.swap(a, b, key, cipher)
                self.assertEqual(a.texts(), b.texts())
                self.assertEqual((a.rows(), a.vecs(), b.rows(), b.vecs()), (4, 4, 4, 4))
                secret = [x.encode('utf-8') for x in a.texts() + ['conv-of-aij-hp', 'conv-of-mtlaptop']]
                name = re.compile(r'AIJ-HP|MTLAPTOP|gen-0001|generation\.json|batch-[0-9]{8}' + re.escape(crypto.EXT[cipher]))
                for d, dirs, files in os.walk(a.share):
                    for n in dirs + files:
                        self.assertTrue(name.fullmatch(n), n)
                    for n in files:
                        with open(os.path.join(d, n), 'rb') as f:
                            data = f.read()
                        for s in secret:
                            self.assertNotIn(s, data, os.path.join(d, n))

    def test_mixed_ciphers(self):
        a, b = self.pair('mixed')
        key = secrets.token_bytes(32)
        for c, cipher in ((a, 'sqlcipher'), (b, 'cryptography'), (a, 'sqlcipher')):
            self.assertEqual(c.sync(memory=False, key=key, cipher=cipher)['errors'], [])
        self.assertEqual((a.texts(), len(b.texts())), (b.texts(), 4))
        self.assertEqual([sorted(os.listdir(self.gen_dir(c, 1))) for c in (a, b)],
                         [['batch-00000001.sqlcipher', 'generation.json'], ['batch-00000001.aesgcm', 'generation.json']])

    def plain_meets_key(self):
        """A and B swap their records in the plain way, then A takes a key: A takes nothing of B, and B nothing of what A writes now."""
        a, b = self.pair('plain-meets-key')
        key = secrets.token_bytes(32)
        a.sync(), b.sync()
        return a, b, key, a.sync(key=key), b.sync()

    def test_plain_peer_refused(self):
        a, b, key, ra, rb = self.plain_meets_key()
        self.assertEqual(ra['errors'], [{'where': 'pull', 'machine': 'MTLAPTOP', 'error': t('peer_plain', machine='MTLAPTOP')}])
        self.assertEqual(rb['errors'], [{'where': 'pull', 'machine': 'AIJ-HP', 'error': t('peer_needs_key', machine='AIJ-HP')}])
        self.assertEqual((a.rows(), b.rows()), (2, 4))  # A never took B's records; B keeps what came before
        self.assertEqual(self.peer_row(b, 'AIJ-HP'), (0, 1, '[]', t('peer_needs_key', machine='AIJ-HP')))  # and nothing is reset

    def test_join_converges(self):
        a, b, key, _, _ = self.plain_meets_key()
        self.assertEqual(b.sync(key=key)['errors'], [])  # B joins: a sealed generation of its own
        self.assertEqual(a.sync(key=key)['errors'], [])
        self.assertEqual((a.texts(), len(b.texts())), (b.texts(), 4))
        self.assertEqual(cgsync.generation(os.path.join(b.share, 'MTLAPTOP')).cipher, 'sqlcipher')

    def test_other_key(self):
        a, b = self.pair('other-key')
        k1, k2 = secrets.token_bytes(32), secrets.token_bytes(32)
        a.sync(key=k1)
        self.assertEqual(b.sync(key=k2)['errors'], [{'where': 'pull', 'machine': 'AIJ-HP', 'error': t('peer_other_key', machine='AIJ-HP')}])
        self.assertEqual(a.sync(key=k1)['errors'], [{'where': 'pull', 'machine': 'MTLAPTOP', 'error': t('peer_other_key', machine='MTLAPTOP')}])
        self.assertEqual((a.rows(), b.rows()), (2, 2))

    def broken(self, n):
        return t('batch_broken', n=n, machine='AIJ-HP', e='ValueError: ' + t('seal_broken'))

    def test_tampered_batch(self):
        key = secrets.token_bytes(32)
        for cipher in crypto.CIPHERS:
            with self.subTest(cipher=cipher):
                a, b = self.pair('tampered-' + cipher)
                a.sync(key=key, cipher=cipher)
                path = os.path.join(self.gen_dir(a, 1), 'batch-00000001' + crypto.EXT[cipher])
                with open(path, 'r+b') as f:
                    data = bytearray(f.read())
                    data[len(data) // 2] ^= 1
                    f.seek(0)
                    f.write(data)
                r = b.sync(key=key, cipher=cipher)
                self.assertEqual([(e['batch'], e['error']) for e in r['errors']], [(1, self.broken(1))])
                self.assertEqual(b.rows(), 2)
                cgsync.skip_batch(b.conn, 'AIJ-HP', 1)
                a.say('conv-of-aij-hp-2', 'третье сообщение', 'ответ на третье')
                self.ready(a)
                a.sync(key=key, cipher=cipher)
                self.assertEqual((b.sync(key=key, cipher=cipher)['errors'], b.rows(), self.peer_row(b, 'AIJ-HP')[1:3]), ([], 4, (2, '[1]')))

    def test_renamed_batch(self):  # a sound batch under another number is no batch there
        key = secrets.token_bytes(32)
        for cipher in crypto.CIPHERS:
            with self.subTest(cipher=cipher):
                a, b = self.pair('renamed-' + cipher)
                a.sync(key=key, cipher=cipher)
                one = os.path.join(self.gen_dir(a, 1), 'batch-00000001' + crypto.EXT[cipher])
                shutil.copy(one, os.path.join(self.gen_dir(a, 1), 'batch-00000002' + crypto.EXT[cipher]))
                r = b.sync(key=key, cipher=cipher)
                self.assertEqual([(e['batch'], e['error']) for e in r['errors']], [(2, self.broken(2))])
                self.assertEqual((b.rows(), self.peer_row(b, 'AIJ-HP')[1]), (4, 1))

    def test_needs_join_after_reinstall(self):
        a, b = self.pair('reinstall')
        a.sync(key=secrets.token_bytes(32))
        listing = lambda: sorted((d, sorted(fs)) for d, _, fs in os.walk(a.share))
        before = listing()
        a.close()
        os.remove(a.db)
        a.open()
        r = a.sync()
        self.assertEqual(r['errors'], [{'where': 'sync', 'error': t('share_needs_join', machine='AIJ-HP')}])
        self.assertEqual((listing(), a.rows()), (before, 0))  # nothing written, nothing read

    def test_cipher_change(self):
        a, b = self.pair('cipher-change')
        key = secrets.token_bytes(32)
        self.swap(a, b, key)
        rows = b.rows()
        self.assertEqual(a.sync(key=key, cipher='cryptography')['errors'], [])
        self.assertEqual(sorted(os.listdir(self.gen_dir(a, 2))), ['batch-00000001.aesgcm', 'generation.json'])
        self.assertEqual((b.sync(key=key)['errors'], b.rows(), self.peer_row(b, 'AIJ-HP')), ([], rows, (2, 1, '[]', None)))

    def test_plain_after_off(self):
        a, b = self.pair('off')
        a.sync(key=secrets.token_bytes(32))
        a.conn.execute("INSERT OR REPLACE INTO meta VALUES('share_plain', '1')")
        self.assertEqual(a.sync()['errors'], [])
        self.assertEqual(cgsync.generation(os.path.join(a.share, 'AIJ-HP')), cgsync.Gen(2, self.gen_dir(a, 2), None, None))
        self.assertEqual(sorted(os.listdir(self.gen_dir(a, 2))), ['batch-00000001.jsonl.gz', 'generation.json'])
        self.assertEqual(sum(l['type'] == 'chunk' for l in self.gen_lines(a, 2)), 2)
        self.assertIsNone(memdb.meta(a.conn, 'share_plain'))
        self.assertEqual((b.sync()['errors'], b.rows()), ([], 4))  # and a computer with no key reads it again

    def test_a_sqlcipher_generation_that_cannot_be_opened_is_an_error_of_that_computer_only(self):
        a, b = self.pair('package')
        key = secrets.token_bytes(32)
        self.swap(a, b, key, 'cryptography')
        a.sync(key=key, cipher='sqlcipher')  # a new generation of A
        with mock.patch.dict(sys.modules, {'sqlcipher3': None}):
            r, r2 = b.sync(key=key, cipher='cryptography'), b.sync(key=key, cipher='sqlcipher')
        self.assertEqual(r['errors'], [{'where': 'pull', 'machine': 'AIJ-HP', 'error': t('peer_sqlcipher_unreadable', machine='AIJ-HP')}])
        error = t('cipher_missing', pkg='sqlcipher3')
        self.assertEqual((r2['errors'], os.listdir(os.path.join(b.share, 'MTLAPTOP'))), ([{'where': 'sync', 'error': error}], ['gen-0001']))  # no label
        self.assertEqual(self.peer_row(b, 'AIJ-HP'), (1, 1, '[]', t('peer_sqlcipher_unreadable', machine='AIJ-HP')))  # still at the generation it was
        self.assertEqual((b.sync(key=key, cipher='cryptography')['errors'], self.peer_row(b, 'AIJ-HP')), ([], (2, 1, '[]', None)))

    def test_a_start_killed_before_its_reset_is_repaired_by_the_next_sync(self):  # the label is out, the marks still say 'out already'
        self.ready(self.a)
        self.a.sync()
        with mock.patch.object(cgsync, '_restart'):
            self.start(self.a)
        self.assertEqual((self.peer_row(self.a, 'AIJ-HP')[:2], self.a.q('SELECT count(*) FROM keys WHERE pushed = 1')), ((0, 1), 2))
        self.assertEqual(self.a.sync()['pushed'], 2)
        lines = self.gen_lines(self.a, 1)
        self.assertEqual((sum(l['type'] == 'chunk' for l in lines), sum(l['type'] == 'vec' for l in lines)), (2, 2))
        self.assertEqual((self.peer_row(self.a, 'AIJ-HP'), self.a.sync()['pushed']), ((1, 1, '[]', None), 0))

    def test_a_start_takes_a_cipher_only_with_a_key(self):
        for cipher, key in (('sqlcipher', None), (None, b'k' * 32), ('rot13', b'k' * 32)):
            with self.assertRaises(ValueError):
                cgsync.start_generation(self.a.conn, os.path.join(self.share, 'AIJ-HP'), 'AIJ-HP', cipher, key)
        self.assertEqual((os.path.exists(os.path.join(self.share, 'AIJ-HP')), self.a.conn.in_transaction), (False, False))

    def test_pending(self):
        pending = lambda: cgsync.pending(self.a.conn)
        self.assertFalse(pending())
        self.a.index()  # records not out
        self.assertTrue(pending())
        self.a.sync()
        self.assertFalse(pending())
        self.a.embed()  # vectors not out
        self.assertTrue(pending())
        self.a.sync()
        self.assertFalse(pending())
        memdb.forget(self.a.conn, 'SA', self.a.q("SELECT pos FROM chunks WHERE text = 'ёжик в тумане на i3'"))  # a forget not out
        self.assertTrue(pending())
        self.a.sync()
        self.assertFalse(pending())
        self.b.index()
        self.b.sync()
        self.a.sync()  # records that came from B are not ours to send
        self.assertFalse(pending())

    def sealed_lines(self, c, n, key, cipher='sqlcipher'):
        out = []
        for b in sorted(cgsync._batches(self.gen_dir(c, n), crypto.EXT[cipher])):
            with open(os.path.join(self.gen_dir(c, n), 'batch-%08d%s' % (b, crypto.EXT[cipher])), 'rb') as f:
                out += [json.loads(l) for l in crypto.unseal(f.read(), cipher, key, '%s/%d/batch-%08d' % (c.name, n, b)) if l]
        return out

    def reinstalled(self, tag):
        """B's history is in the share, plain (generation 0); B's index is gone, as after a reinstall."""
        a, b = self.pair(tag)
        b.sync()
        b.close()
        os.remove(b.db)
        b.open()
        return b, os.path.join(b.share, 'MTLAPTOP')

    def test_a_fresh_index_with_a_key_brings_its_own_history_into_the_new_generation(self):
        b, own = self.reinstalled('fresh-key')
        key = secrets.token_bytes(32)
        self.assertEqual(b.rows(), 0)
        self.assertEqual(b.sync(key=key)['errors'], [])
        self.assertEqual(b.texts(), sorted(['кэш протух на ноутбуке', 'ответ про кэш']))
        lines = self.sealed_lines(b, 1, key)
        self.assertEqual((sum(l['type'] == 'chunk' for l in lines), sum(l['type'] == 'vec' for l in lines)), (2, 2))
        self.assertEqual(cgsync.generation(own).cipher, 'sqlcipher')

    def test_a_new_generation_waits_for_an_own_batch_that_is_broken(self):
        b, own = self.reinstalled('fresh-broken')
        key = secrets.token_bytes(32)
        self.break_line(b, 1, 3)
        r = b.sync(key=key)
        self.assertEqual([(e['where'], e['machine'], e['batch']) for e in r['errors']], [('pull', 'MTLAPTOP', 1)])
        self.assertEqual(sorted(os.listdir(own)), ['batch-00000001.jsonl.gz'])  # no generation, no batch of the new kind
        cgsync.skip_batch(b.conn, 'MTLAPTOP', 1)
        self.assertEqual(b.sync(key=key)['errors'], [])
        self.assertEqual(cgsync.generation(own).n, 1)

    def test_a_new_generation_waits_for_the_own_history_the_time_cap_left_unread(self):
        b, own = self.reinstalled('fresh-cap')
        key = secrets.token_bytes(32)
        ticks = iter(range(100))
        r = b.sync(key=key, max_seconds=1, now=lambda: next(ticks))  # the time is over before the first batch is read
        self.assertEqual((r['errors'], r['pushed'], b.rows(), [n for n in os.listdir(own) if n.startswith('gen-')]), ([], 0, 0, []))
        self.assertEqual(b.sync(key=key)['errors'], [])
        self.assertEqual(sum(l['type'] == 'chunk' for l in self.sealed_lines(b, 1, key)), 2)

    def test_a_batch_copied_into_another_computers_folder_is_refused(self):  # same generation, same number: the computer is in what it is tied to
        key = secrets.token_bytes(32)
        for cipher in crypto.CIPHERS:
            with self.subTest(cipher=cipher):
                a, b = self.pair('copied-' + cipher)
                a.sync(key=key, cipher=cipher)
                shutil.copytree(self.gen_dir(a, 1), os.path.join(a.share, 'THIRD', 'gen-0001'))  # the label and batch 1 of A, under THIRD
                r = b.sync(key=key, cipher=cipher)
                self.assertEqual([(e['machine'], e['batch'], e['error']) for e in r['errors']],
                                 [('THIRD', 1, t('batch_broken', n=1, machine='THIRD', e='ValueError: ' + t('seal_broken')))])
                self.assertEqual(b.rows(), 4)  # A's own are taken, THIRD's copy is not

    def test_a_sealed_batch_is_written_by_its_generation_not_by_what_the_seal_returned(self):  # fail closed
        gen = cgsync.Gen(1, self.root, 'sqlcipher', 'ab' * 8)
        with mock.patch.object(crypto, 'seal', return_value=b''):
            cgsync._write(gen, 1, ['{"x":1}'], 'AIJ-HP', b'k' * 32)
        with open(os.path.join(self.root, 'batch-00000001.sqlcipher'), 'rb') as f:
            self.assertEqual(f.read(), b'')

    # 16. the short memory with a key: one sealed file per project, opaque in name
    def memory_dir(self, c, n=1):
        return os.path.join(self.gen_dir(c, n), 'memory')

    def sealed_memory(self, c, key, header, entries, cipher='sqlcipher'):
        """Replace the sealed memory file of C's project by what the test wants in it, sealed to its right place."""
        name = crypto.opaque(key, project_key(c.cwd, c.home))
        lines = [json.dumps(header)] + [json.dumps({'name': n, 'data': base64.b64encode(d).decode('ascii')}) for n, d in entries]
        with open(os.path.join(self.memory_dir(c), name + crypto.EXT[cipher]), 'wb') as f:
            f.write(crypto.seal(lines, cipher, key, '%s/1/memory/%s' % (c.name, name)))

    def test_memory_encrypted(self):
        key = secrets.token_bytes(32)
        for cipher in crypto.CIPHERS:
            with self.subTest(cipher=cipher):
                a, b = self.pair('memory-' + cipher)
                a.fact('fact-one.md', 'памятьмаркер')
                a.sync(key=key, cipher=cipher)
                self.assertTrue(re.fullmatch(r'[0-9a-f]{32}' + re.escape(crypto.EXT[cipher]), ''.join(os.listdir(self.memory_dir(a)))))
                for d, dirs, files in os.walk(a.share):
                    for n in dirs + files:
                        self.assertNotIn('fact-one', n)
                    for n in files:
                        with open(os.path.join(d, n), 'rb') as f:
                            data = f.read()
                        self.assertNotIn('памятьмаркер'.encode('utf-8'), data, os.path.join(d, n))
                self.assertEqual(b.sync(key=key, cipher=cipher)['memory'], {'pushed': 0, 'pulled': 1, 'conflicts': 0})
                with open(os.path.join(a.memory, 'fact-one.md'), 'rb') as f, open(os.path.join(b.memory, 'fact-one.md'), 'rb') as g:
                    self.assertEqual(f.read(), g.read())
                self.assertEqual(b.read('MEMORY.md'), '- [fact-one](fact-one.md) — d\n')

    def test_memory_plain_refused(self):
        key = secrets.token_bytes(32)
        a, b = self.pair('memory-plain')
        b.fact('open.md', 'открыто')
        b.sync()
        r = a.sync(key=key)
        self.assertEqual([(e['where'], e['error']) for e in r['errors']], [('pull', t('peer_plain', machine='MTLAPTOP'))])
        self.assertEqual((r['memory'], os.listdir(a.memory)), ({'pushed': 0, 'pulled': 0, 'conflicts': 0}, []))
        b.fact('open.md', 'открыто снова')
        self.assertEqual(b.sync()['memory']['pulled'], 0)  # and the other way round: B, with no key, takes nothing sealed
        self.assertEqual(os.listdir(b.memory), ['open.md'])

    def test_memory_of_another_key_is_not_read(self):
        a, b = self.pair('memory-other-key')
        a.fact('f.md', 'v1')
        a.sync(key=secrets.token_bytes(32))
        r = b.sync(key=secrets.token_bytes(32))
        self.assertEqual([(e['where'], e['error']) for e in r['errors']], [('pull', t('peer_other_key', machine='AIJ-HP'))])
        self.assertEqual((r['memory']['pulled'], os.listdir(b.memory)), (0, []))

    def test_memory_swapped_projects(self):
        key = secrets.token_bytes(32)
        a, b = self.pair('memory-swapped')
        a.fact('one.md', 'первый')
        a.sync(key=key)
        other = os.path.join(a.dir, 'other-memory')
        os.makedirs(other)
        with open(os.path.join(other, 'two.md'), 'w', encoding='utf-8') as f:
            f.write('второй')
        cgsync.sync(a.conn, a.share, a.name, memory_dir=other, project_cwd=a.home + '\\work\\other', home=a.home, digest=lambda m: None, key=key)
        folder = self.memory_dir(a)
        x, y = sorted(os.listdir(folder))
        os.rename(os.path.join(folder, x), os.path.join(folder, 'swap'))
        os.rename(os.path.join(folder, y), os.path.join(folder, x))
        os.rename(os.path.join(folder, 'swap'), os.path.join(folder, y))
        r = b.sync(key=key)
        self.assertEqual([(e['where'], e['machine'], e['error']) for e in r['errors']],
                         [('memory', 'AIJ-HP', t('memory_broken', machine='AIJ-HP', e='ValueError: ' + t('seal_broken')))])
        self.assertEqual((r['memory']['pulled'], os.listdir(b.memory)), (0, []))

    def test_memory_file_that_is_not_what_it_says_writes_no_fact(self):
        key = secrets.token_bytes(32)
        good = {'type': 'memory', 'machine': 'AIJ-HP', 'gen': 1}
        for tag, header, entries in (('machine', {**good, 'machine': 'THIRD'}, [('f.md', b'x')]),
                                     ('gen', {**good, 'gen': 2}, [('f.md', b'x')]),
                                     ('type', {**good, 'type': 'batch'}, [('f.md', b'x')]),
                                     ('up', good, [('ok.md', b'x'), ('..\\evil.md', b'x')]),
                                     ('slash', good, [('sub/evil.md', b'x')]),
                                     ('index', good, [('MEMORY.md', b'x')]),
                                     ('kind', good, [('f.txt', b'x')])):
            with self.subTest(tag):
                a, b = self.pair('memory-bad-' + tag)
                a.fact('f.md', 'v1')
                a.sync(key=key)
                self.sealed_memory(a, key, header, entries)
                r = b.sync(key=key)
                self.assertEqual([(e['where'], e['machine']) for e in r['errors']], [('memory', 'AIJ-HP')])
                self.assertEqual((r['memory'], os.listdir(b.memory)), ({'pushed': 0, 'pulled': 0, 'conflicts': 0}, []))
                self.assertFalse(os.path.exists(os.path.join(os.path.dirname(b.memory), 'evil.md')))  # out of the folder, where '..' leads

    def test_an_own_memory_file_that_cannot_be_read_is_told_and_not_written_over(self):
        key = secrets.token_bytes(32)
        a, b = self.pair('memory-own-broken')
        a.fact('f1.md', 'v1')
        a.sync(key=key)
        path = os.path.join(self.memory_dir(a), os.listdir(self.memory_dir(a))[0])
        with open(path, 'wb') as f:
            f.write(b'not a sealed file')
        a.fact('f2.md', 'v2')
        b.fact('f3.md', 'v3')
        b.sync(key=key)
        r = a.sync(key=key)
        self.assertEqual([(e['where'], e['machine']) for e in r['errors']], [('memory', 'AIJ-HP')])
        self.assertEqual((r['memory']['pushed'], r['memory']['pulled']), (0, 1))  # the others' facts come all the same
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), b'not a sealed file')

    def test_encrypted_memory_is_rewritten_only_on_a_change_and_keeps_its_facts(self):
        key = secrets.token_bytes(32)
        for cipher in crypto.CIPHERS:
            with self.subTest(cipher=cipher):
                a, b = self.pair('memory-rewrite-' + cipher)
                a.fact('f1.md', 'v1')
                a.sync(key=key, cipher=cipher)
                path = os.path.join(self.memory_dir(a), os.listdir(self.memory_dir(a))[0])
                with open(path, 'rb') as f:
                    first = f.read()
                self.assertEqual(a.sync(key=key, cipher=cipher)['memory'], {'pushed': 0, 'pulled': 0, 'conflicts': 0})
                with open(path, 'rb') as f:
                    self.assertEqual(f.read(), first)  # no seal comes out the same twice: the same bytes are no rewrite
                os.remove(os.path.join(a.memory, 'f1.md'))
                a.fact('f2.md', 'v2')
                self.assertEqual(a.sync(key=key, cipher=cipher)['memory']['pushed'], 1)
                b.sync(key=key, cipher=cipher)
                self.assertEqual(sorted(os.listdir(b.memory)), ['MEMORY.md', 'f1.md', 'f2.md'])  # a fact deleted here stays in the share
                a.fact('f2.md', 'v3')
                a.sync(key=key, cipher=cipher)
                self.assertEqual(b.sync(key=key, cipher=cipher)['memory']['pulled'], 1)
                self.assertIn('v3', b.read('f2.md'))
                self.assertEqual(b.read('MEMORY.md').count('(f2.md)'), 1)

    def test_write_facts_adds_to_a_sealed_file_and_touches_it_only_on_a_change(self):
        key, gen = secrets.token_bytes(32), cgsync.Gen(1, os.path.join(self.root, 'gen-0001'), 'cryptography', 'ab' * 8)
        write = lambda facts: cgsync._write_facts(gen, 'AIJ-HP', 'proj', key, facts)
        read = lambda: cgsync._read_facts(gen, 'AIJ-HP', 'proj', key)
        write({})
        self.assertEqual((read(), os.path.exists(gen.path)), ({}, False))  # nothing to say, no file
        write({'a.md': b'one', 'b.md': 'два'.encode('utf-8')})
        path = os.path.join(gen.path, 'memory', os.listdir(os.path.join(gen.path, 'memory'))[0])
        with open(path, 'rb') as f:
            first = f.read()
        write({'a.md': b'one'})
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), first)
        write({'a.md': b'uno', 'c.md': b'three'})
        self.assertEqual(read(), {'a.md': b'uno', 'b.md': 'два'.encode('utf-8'), 'c.md': b'three'})
        self.assertEqual(os.listdir(os.path.dirname(path)), [os.path.basename(path)])  # no tmp left

    def test_memory_of_an_open_generation_after_the_first_lies_under_it(self):
        self.start(self.a)
        self.a.fact('f.md', 'v1')
        self.a.sync()
        folder = os.path.join(self.memory_dir(self.a), cgsync.project_folder('~\\work\\proj'))
        self.assertEqual(os.listdir(folder), ['f.md'])
        self.assertFalse(os.path.exists(os.path.join(self.share, 'AIJ-HP', 'memory')))
        self.assertEqual(self.b.sync()['memory'], {'pushed': 0, 'pulled': 1, 'conflicts': 0})

    def test_no_memory_step_while_the_new_generation_waits(self):
        b, own = self.reinstalled('memory-waits')
        self.break_line(b, 1, 3)
        b.fact('f.md', 'v1')
        r = b.sync(key=secrets.token_bytes(32))
        self.assertEqual((r['memory'], os.listdir(own)), (None, ['batch-00000001.jsonl.gz']))

    def test_cli_sync_and_forget(self):
        self.a.index()
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONIOENCODING', 'PYTHONUTF8')}
        run = lambda c, *args: json.loads(subprocess.run([sys.executable, CG, '--db', c.db, '--projects', c.projects, '--lang', 'ru', *args],
                                                         capture_output=True, env=env).stdout.decode('utf-8'))
        out = run(self.a, 'sync', '--sync-dir', self.share, '--machine', 'AIJ-HP', '--max-seconds', '30')
        self.assertEqual(set(out) >= {'pushed', 'pulled', 'peers', 'skipped_vectors', 'errors', 'memory'}, True)
        self.assertEqual((out['pushed'], out['errors']), (2, []))
        out = run(self.b, 'sync', '--sync-dir', self.share, '--machine', 'MTLAPTOP', '--memory-dir', self.b.memory, '--project-cwd', self.b.cwd)
        self.assertEqual((out['pulled'], out['peers'], out['memory']), (2, {'AIJ-HP': 1}, {'pushed': 0, 'pulled': 0, 'conflicts': 0}))
        out = run(self.b, 'sync', '--sync-dir', os.path.join(self.root, 'off'), '--machine', 'MTLAPTOP')
        self.assertIn('папка синхронизации недоступна', out['errors'][0]['error'])
        out = run(self.b, 'sync', '--sync-dir', self.share, '--machine', 'MTLAPTOP', '--skip-batch', 'AIJ-HP', '7')
        self.assertIn('AIJ-HP ждёт на пачке 2, а не 7', out['error'])  # a typo skips nothing
        out = run(self.b, 'sync', '--sync-dir', self.share, '--machine', 'MTLAPTOP', '--skip-batch', 'AIJ-HP', '2')
        self.assertEqual(out['errors'], [])
        st = run(self.b, 'status', '--machine', 'MTLAPTOP')['sync']
        self.assertEqual(([p['skipped'] for p in st['peers'] if p['machine'] == 'AIJ-HP'], st['last']['errors']), ([[2]], 0))
        self.assertEqual(run(self.b, 'sync', '--sync-dir', self.share, '--skip-batch', 'AIJ-HP', 'x')['error'].split(':')[0], 'ValueError')
        pos = self.a.q("SELECT pos FROM chunks WHERE text = 'ёжик в тумане на i3'")
        self.assertEqual(run(self.a, 'forget', '--conv', 'SA', '--pos', str(pos)), {'forgotten': 1})


if __name__ == '__main__':
    unittest.main()
