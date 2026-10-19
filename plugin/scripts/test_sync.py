"""Stage 2 acceptance (§9): two computers, each with its own home, transcripts and index, and a temporary folder for the network share.
Every transcript here is synthetic."""
import gzip
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import memdb
import sync as cgsync
from memdb import connect, search, update
from test_memdb import TS, claude_line, owner_line
from test_vectors import fake
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
        # each record is ours or theirs: /cg-health tells them apart
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

        def cut(folder, n, lines):
            real(folder, n, lines)
            os.rename(os.path.join(folder, 'batch-%08d.jsonl.gz' % n), os.path.join(folder, 'batch-%08d.jsonl.gz.tmp' % n))
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

        def written_then_lost(folder, n, lines):  # the batch is out, then the run dies before the index marks it
            real(folder, n, lines)
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

    def break_line(self, c, n, line):
        path = os.path.join(self.share, c.name, 'batch-%08d.jsonl.gz' % n)
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

    def test_a_missing_batch_with_later_ones_is_an_error(self):
        self.ready(self.b)
        self.b.sync()
        self.b.say('SB2', 'вторая')
        self.ready(self.b)
        self.b.sync()
        os.remove(os.path.join(self.share, 'MTLAPTOP', 'batch-00000001.jsonl.gz'))
        r = self.a.sync()
        self.assertEqual((r['errors'][0]['batch'], r['pulled']), (1, 0))
        self.assertIn('/cg-sync skip MTLAPTOP 1', r['errors'][0]['error'])

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
        self.assertEqual(json.loads(memdb.meta(self.a.conn, 'sync_last'))['errors'], 1)  # kept for /cg-health
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
            self.assertEqual({h['conv'] for h in search(c.conn, 'ответ', project=key)}, {'SA', 'SB'})
            self.assertEqual(search(c.conn, 'ответ', project=project_key('C:\\_git\\other', c.home)), [])

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
        self.assertEqual(out['errors'], [])
        st = run(self.b, 'status', '--machine', 'MTLAPTOP')['sync']
        self.assertEqual(([p['skipped'] for p in st['peers'] if p['machine'] == 'AIJ-HP'], st['last']['errors']), ([[7]], 0))
        self.assertEqual(run(self.b, 'sync', '--sync-dir', self.share, '--skip-batch', 'AIJ-HP', 'x')['error'].split(':')[0], 'ValueError')
        pos = self.a.q("SELECT pos FROM chunks WHERE text = 'ёжик в тумане на i3'")
        self.assertEqual(run(self.a, 'forget', '--conv', 'SA', '--pos', str(pos)), {'forgotten': 1})


if __name__ == '__main__':
    unittest.main()
