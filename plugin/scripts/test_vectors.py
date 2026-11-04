import http.server
import itertools
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import zlib
from unittest import mock
import numpy
import memdb
from memdb import connect, status, update
from test_memdb import TS, claude_line, owner_line, search
from test_texts import use_lang
from transcripts import project_key
from vectors import (CHUNK, DIMS, DOC_PREFIX, MODEL, OVERLAP, OllamaDown, chunks_of, embed_pending, hybrid_search, ollama_embed, pack,
                     rrf_scores, search_many, search_vectors, vector_status)

TOOL = dict(type='assistant', timestamp=TS, cwd='C:\\a', message={
    'id': 'm2', 'model': 'claude-opus-5-5', 'role': 'assistant', 'content': [{'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'ls'}}]})


def fake(texts, model=MODEL, timeout=None):
    """A bag of words: each word adds 1 to one of 256 dimensions, so texts with shared words are close."""
    out = []
    for t in texts:
        v = [0.0] * DIMS
        for w in re.findall(r'\w+', t.lower()):
            v[zlib.crc32(w.encode()) % DIMS] += 1.0
        out.append(v)
    return out


def synonym(texts, model=MODEL, timeout=None):  # for this embedder «истёк» and «протух» are one word
    return fake([t.replace('истёк', 'протух') for t in texts])


def never(texts, model, timeout=None):
    raise AssertionError('the model must not be asked')


class RrfTest(unittest.TestCase):
    def test_rrf(self):
        def rrf(lists, weights):  # the ids best first; a tie keeps the order of first appearance
            score = rrf_scores(lists, weights)
            return sorted(score, key=score.__getitem__, reverse=True)

        self.assertEqual(rrf([[1, 2, 3], [3, 4]], [1.0, 1.0])[:2], [3, 1])
        self.assertEqual(rrf([[1, 2], [3]], [1.0, 1.0]), [1, 3, 2])  # a tie keeps the order of the first list
        self.assertEqual(rrf([[1], [2]], [0.5, 1.0]), [2, 1])
        self.assertEqual(rrf([[1], [2, 3]], [1.0, lambda i: 0.1 if i == 2 else 1.0]), [1, 3, 2])  # a weight may depend on the id


class ChunkingTest(unittest.TestCase):
    def setUp(self):
        use_lang(self, 'ru')  # the messages these tests read are the Russian ones of stage 1

    def test_chunks_of(self):
        parts = chunks_of('я' * 3700)
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(len(p) <= CHUNK for p in parts))
        self.assertEqual(parts[0][-OVERLAP:], parts[1][:OVERLAP])
        self.assertEqual(chunks_of('коротко'), ['коротко'])
        self.assertEqual(chunks_of('я' * CHUNK), ['я' * CHUNK])
        text = ''.join(map(str, range(2000)))  # no gaps: the windows' non-overlapping parts plus the last window are the whole text
        parts = chunks_of(text)
        self.assertEqual(''.join(p[:CHUNK - OVERLAP] for p in parts[:-1]) + parts[-1], text)

    def test_pack_is_unit_256(self):
        v = numpy.frombuffer(pack([3.0] * 768), dtype='float32')
        self.assertEqual(len(v), DIMS)
        self.assertAlmostEqual(float(numpy.linalg.norm(v)), 1.0, places=5)
        self.assertEqual(len(pack([0.0] * DIMS)), DIMS * 4)  # a zero vector stays zero, no division by zero
        with self.assertRaises(ValueError):  # a model with fewer dimensions must not fill the table with short vectors
            pack([1.0] * 10)

    def test_without_numpy(self):
        with mock.patch.dict(sys.modules, {'numpy': None}):
            with self.assertRaisesRegex(RuntimeError, 'numpy не установлен'):
                pack([1.0] * DIMS)


class VectorsTest(unittest.TestCase):
    def setUp(self):
        use_lang(self, 'ru')
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.projects = os.path.join(d.name, 'projects')
        self.conn = connect(os.path.join(d.name, 'db', 'index.db'))
        self.addCleanup(self.conn.close)
        self.s1 = self.write('P1/S1.jsonl', [
            owner_line('ёжик в тумане'), claude_line('ничего ' * 600 + 'ёжик в тумане'), TOOL, owner_line('совсем другое про кэш')])
        self.write('P1/S1/subagents/agent-a1.jsonl', [owner_line('найди ёжик', cwd=''), claude_line('итог про ёжика', cwd='')])
        self.write('P2/S2.jsonl', [owner_line('ёжик в другом проекте', cwd='C:\\b')])
        update(self.conn, self.projects)
        self.records = self.q("SELECT count(*) FROM chunks WHERE kind != 'tool'")  # owner, claude, task, report and no tool row

    def q(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()[0]

    def write(self, name, objs):
        path = os.path.join(self.projects, *name.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(''.join(json.dumps(o, ensure_ascii=False) + '\n' for o in objs).encode('utf-8'))
        return path

    def test_embed_pending_incremental_and_kinds(self):
        calls = []

        def spy(texts, model):
            self.assertFalse(self.conn.in_transaction)  # the write lock is not held while the model works
            calls.append((model, texts))
            return fake(texts)

        self.assertEqual(self.q('SELECT count(*) FROM chunks'), self.records + 1)
        self.assertEqual(vector_status(self.conn), {'model': None, 'with_vectors': 0, 'total': self.records, 'failed': 0, 'updated': None})
        r = embed_pending(self.conn, embed_fn=spy, batch=2)
        self.assertEqual((r['done'], r['pending'], r['model'], r['errors']), (self.records, 0, MODEL, []))
        self.assertTrue(all(m == MODEL and len(t) <= 2 for m, t in calls))
        sent = [t for _, ts in calls for t in ts]
        self.assertTrue(all(t.startswith(DOC_PREFIX) for t in sent))
        self.assertEqual(len(sent), self.q('SELECT count(*) FROM vecs'))
        self.assertEqual(self.q("SELECT count(*) FROM vecs WHERE chunk IN (SELECT rowid FROM chunks WHERE kind = 'tool')"), 0)
        self.assertEqual(self.q('SELECT count(*) FROM vecs WHERE part > 0'), 3)  # the 4 kB reply is 4 pieces
        st = vector_status(self.conn)
        self.assertEqual((st['model'], st['with_vectors'], st['total']), (MODEL, self.records, self.records))
        self.assertRegex(st['updated'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertEqual(status(self.conn)['vectors'], st)
        self.assertEqual(embed_pending(self.conn, embed_fn=fake)['done'], 0)

    def test_time_cap_leaves_pending(self):
        ticks = itertools.count()  # the clock moves by 1 on every look
        r = embed_pending(self.conn, embed_fn=fake, max_seconds=0, batch=1, now=lambda: next(ticks))
        self.assertEqual(r['done'], 1)
        self.assertEqual(r['pending'], self.records - 1)
        self.assertEqual(embed_pending(self.conn, embed_fn=fake)['done'], self.records - 1)  # the next run continues

    def test_model_change_reembeds(self):
        embed_pending(self.conn, embed_fn=fake)
        n = self.q('SELECT count(*) FROM vecs')
        r = embed_pending(self.conn, model='other', embed_fn=fake)
        self.assertEqual((r['done'], r['model']), (self.records, 'other'))
        self.assertEqual((self.q('SELECT count(*) FROM vecs'), vector_status(self.conn)['model']), (n, 'other'))

    def test_embedder_error_is_reported_and_keeps_progress(self):
        n = []

        def fails_after_first_batch(texts, model):
            n.append(1)
            if len(n) > 1:
                raise RuntimeError('Ollama не отвечает: тест')
            return fake(texts)

        r = embed_pending(self.conn, embed_fn=fails_after_first_batch, batch=1)
        self.assertEqual((r['done'], r['pending']), (1, self.records - 1))
        self.assertEqual(len(r['errors']), 1)
        self.assertIn('Ollama', r['errors'][0]['error'])
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(vector_status(self.conn)['with_vectors'], 1)
        self.assertEqual(vector_status(self.conn)['failed'], 0)  # the second batch failed because Ollama is down, not because of its record

    def poison(self, text='совсем другое про кэш'):
        """An embedder that cannot embed one record (the way Ollama answers 500 for it) and works for the rest; the rowid of that record."""
        def bad(texts, model, timeout=None):
            if any(text in t for t in texts):
                raise RuntimeError('Ollama не отвечает: HTTP Error 500: Internal Server Error')
            return fake(texts)

        return bad, self.q("SELECT rowid FROM chunks WHERE text = ?", text)

    def test_one_failing_record_gets_a_sentinel_and_the_run_goes_on(self):
        bad, rowid = self.poison()
        r = embed_pending(self.conn, embed_fn=bad)  # the whole batch fails, the records are then tried one by one
        self.assertEqual((r['done'], r['pending']), (self.records - 1, 0))
        self.assertEqual(r['errors'], [{'conv': 'S1', 'pos': self.q('SELECT pos FROM chunks WHERE rowid = ?', rowid), 'error': r['errors'][0]['error']}])
        self.assertIn('Ollama', r['errors'][0]['error'])
        self.assertNotIn('кэш', json.dumps(r['errors'], ensure_ascii=False))  # the position says which record; its text is not repeated
        self.assertEqual(self.q('SELECT count(*) FROM vecs WHERE length(vec) = 0'), 1)
        self.assertEqual(self.q('SELECT count(*) FROM vecs WHERE chunk = ? AND length(vec) = 0', rowid), 1)
        st = vector_status(self.conn)
        self.assertEqual((st['failed'], st['with_vectors'], st['total']), (1, self.records, self.records))  # a sentinel counts as done
        again = embed_pending(self.conn, embed_fn=never)  # the second run does nothing and does not ask the model
        self.assertEqual((again['done'], again['pending'], again['errors']), (0, 0, []))
        # search ignores the sentinel, and the record is still found by its words
        qvec = pack(fake(['кэш'])[0])
        self.assertEqual(len(search_vectors(self.conn, qvec, None)), self.records - 1)
        self.assertNotIn(rowid, [c for c, _ in search_vectors(self.conn, qvec, None)])
        hits, state = hybrid_search(self.conn, 'кэш', embed_fn=fake)
        self.assertEqual(state, 'ok')
        self.assertIn(rowid, [h['id'] for h in hits])

    def test_a_dead_ollama_writes_no_sentinels_and_stops(self):
        calls = []

        def down(texts, model):
            calls.append(len(texts))
            raise RuntimeError('Ollama не отвечает: тест')

        r = embed_pending(self.conn, embed_fn=down)
        self.assertEqual((r['done'], r['pending']), (0, self.records))
        self.assertEqual(len(r['errors']), 1)
        self.assertIn('Ollama не отвечает', r['errors'][0]['error'])
        self.assertEqual(self.q('SELECT count(*) FROM vecs'), 0)
        self.assertEqual(vector_status(self.conn)['failed'], 0)
        self.assertLessEqual(len(calls), 3)  # the batch, its first record, one probe: it does not go through all the records

    def test_an_unreachable_ollama_stops_the_run_at_once(self):  # stage 1 nit: a hung one cost three timeouts of 120 s
        calls = []

        def hung(texts, model):
            calls.append(1)
            raise OllamaDown('Ollama не отвечает: timed out')

        r = embed_pending(self.conn, embed_fn=hung)
        self.assertEqual((len(calls), r['done'], r['pending'], len(r['errors'])), (1, 0, self.records, 1))
        self.assertIn('timed out', r['errors'][0]['error'])
        self.assertEqual(self.q('SELECT count(*) FROM vecs'), 0)

    def test_ollama_dying_during_the_record_by_record_pass_keeps_what_was_done(self):
        calls = []

        def dies(texts, model):
            calls.append(1)
            if len(calls) == 1 or len(calls) > 3:  # the batch fails, two records pass alone, then Ollama is gone
                raise RuntimeError('Ollama не отвечает: тест')
            return fake(texts)

        r = embed_pending(self.conn, embed_fn=dies)
        self.assertEqual((r['done'], r['pending'], len(r['errors'])), (2, self.records - 2, 1))
        self.assertEqual(vector_status(self.conn)['failed'], 0)

    def test_a_locked_database_is_reported_and_nothing_is_counted(self):
        self.conn.execute('PRAGMA busy_timeout = 50')
        other = connect(self.conn.execute('PRAGMA database_list').fetchone()[2])
        self.addCleanup(other.close)
        other.execute('BEGIN IMMEDIATE')  # the index is being updated at this moment
        r = embed_pending(self.conn, embed_fn=fake)
        self.assertEqual((r['done'], r['pending'], len(r['errors'])), (0, self.records, 1))
        self.assertIn('locked', r['errors'][0]['error'])

    def test_a_missing_model_does_not_turn_the_records_into_sentinels(self):
        def not_found(texts, model):
            raise RuntimeError('модель %s не найдена: выполните ollama pull %s' % (model, model))

        r = embed_pending(self.conn, embed_fn=not_found)
        self.assertEqual((r['done'], r['pending'], self.q('SELECT count(*) FROM vecs')), (0, self.records, 0))

    def test_model_change_clears_sentinels(self):
        bad, _ = self.poison()
        embed_pending(self.conn, embed_fn=bad)
        self.assertEqual(vector_status(self.conn)['failed'], 1)
        r = embed_pending(self.conn, model='other', embed_fn=fake)  # the new model may well cope with that record
        self.assertEqual((r['done'], r['errors'], vector_status(self.conn)['failed']), (self.records, [], 0))

    def traced(self):
        sql = []
        self.conn.set_trace_callback(sql.append)
        self.addCleanup(self.conn.set_trace_callback, None)
        return sql

    def test_embed_pending_reads_each_row_once(self):
        self.write('P1/Many.jsonl', [owner_line('запись %d' % i) for i in range(40)])
        update(self.conn, self.projects)
        n = self.q("SELECT count(*) FROM chunks WHERE kind != 'tool'")
        sql = self.traced()
        embed_pending(self.conn, embed_fn=fake, batch=16)
        reads = [x for x in sql if x.startswith('SELECT rowid, text')]
        self.assertEqual(len(reads), -(-n // 16) + 1)  # the batches and the empty one that ends the run
        marks = [int(re.search(r'rowid > (\d+)', x).group(1)) for x in reads]  # each read starts after the last row of the batch before
        ids = [r[0] for r in self.conn.execute('SELECT rowid FROM chunks WHERE kind != "tool" ORDER BY rowid')]
        self.assertEqual(marks, [0] + ids[15::16] + ([ids[-1]] if len(ids) % 16 else []))  # the last row of each batch of 16

    def test_embed_pending_row_changed_midbatch(self):
        def rebuilds_s2(texts, model):
            with mock.patch.object(memdb, 'redact', lambda t: t.replace('другом', 'ином')):
                update(self.conn, self.projects, rebuild=True)
            return fake(texts)

        r = embed_pending(self.conn, embed_fn=rebuilds_s2, batch=1000)
        self.assertEqual((r['pending'], r['errors'], self.q("SELECT count(*) FROM vecs WHERE conv = 'S2'")), (1, [], 0))
        self.assertEqual(embed_pending(self.conn, embed_fn=fake)['done'], 1)

    def test_vector_status_scans_once(self):
        embed_pending(self.conn, embed_fn=fake, batch=2)
        self.write('P1/Late.jsonl', [owner_line('ещё без вектора')])
        update(self.conn, self.projects)
        sql = self.traced()
        st = vector_status(self.conn)
        self.assertEqual((st['with_vectors'], st['total']), (self.records, self.records + 1))
        self.assertEqual(len([x for x in sql if 'FROM chunks' in x]), 1)

    def test_wrong_number_of_vectors_writes_nothing(self):
        r = embed_pending(self.conn, embed_fn=lambda texts, model: fake(texts)[:-1], batch=1000)
        self.assertEqual((r['done'], r['pending']), (0, self.records))
        self.assertIn('вектор', r['errors'][0]['error'])
        self.assertEqual(self.q('SELECT count(*) FROM vecs'), 0)

    def test_rewrite_while_embedding_stores_nothing_for_the_old_text(self):
        def rebuilds_s2(texts, model):  # a rebuild with a fixed secret filter replaces S2's text in place: the very rowid that was read
            with mock.patch.object(memdb, 'redact', lambda t: t.replace('другом', 'ином')):
                update(self.conn, self.projects, rebuild=True)
            return fake(texts)

        ticks = itertools.count()  # one batch only
        r = embed_pending(self.conn, embed_fn=rebuilds_s2, max_seconds=0, now=lambda: next(ticks))
        self.assertEqual((r['pending'], self.q("SELECT count(*) FROM vecs WHERE conv = 'S2'")), (1, 0))
        self.assertEqual(embed_pending(self.conn, embed_fn=fake)['done'], 1)
        self.assertEqual(self.q("SELECT vec FROM vecs WHERE conv = 'S2'"), pack(fake([DOC_PREFIX + 'ёжик в ином проекте'])[0]))

    def test_vectors_a_sync_restores_meanwhile_are_not_wiped_by_the_model_switch(self):  # a fresh index embeds, a restore takes its model on
        other = connect(self.conn.execute('PRAGMA database_list').fetchone()[2])
        self.addCleanup(other.close)
        last = self.q("SELECT max(rowid) FROM chunks WHERE kind != 'tool'")  # not in the first batch
        restored, asked = pack(fake(['из восстановления'])[0]), []

        def restores_meanwhile(texts, model):
            if not asked:  # the write lock is free while the model works
                other.execute('INSERT INTO vecs SELECT rowid, 0, conv, ? FROM chunks WHERE rowid = ?', (restored, last))
                other.execute("INSERT OR REPLACE INTO meta VALUES('embed_model', ?)", (model,))
            asked.append(1)
            return fake(texts)

        r = embed_pending(self.conn, embed_fn=restores_meanwhile, batch=2)
        self.assertEqual((self.q('SELECT vec = ? FROM vecs WHERE chunk = ?', restored, last), r['done']), (1, self.records - 1))  # not embedded again

    def test_search_vectors_scope_and_order(self):
        embed_pending(self.conn, embed_fn=fake)
        conv_of = lambda rowid: self.q('SELECT conv FROM chunks WHERE rowid = ?', rowid)
        qvec = pack(fake(['ёжик в тумане'])[0])
        hits = search_vectors(self.conn, qvec, [project_key('C:\\a')])
        self.assertEqual(conv_of(hits[0][0]), 'S1')
        self.assertTrue(all(conv_of(h[0]) in ('S1', 'agent-a1') for h in hits))
        self.assertEqual(len(hits), 5)  # five records, the 4-piece reply once, not four times
        reply = self.q("SELECT rowid FROM chunks WHERE kind = 'claude'")
        scores = [float(numpy.frombuffer(v, 'float32') @ numpy.frombuffer(qvec, 'float32')) for (v,) in self.conn.execute('SELECT vec FROM vecs WHERE chunk = ?', (reply,))]
        self.assertAlmostEqual(dict(hits)[reply], max(scores), places=5)
        self.assertGreater(max(scores), min(scores))
        self.assertEqual([s for _, s in hits], sorted((s for _, s in hits), reverse=True))
        self.assertAlmostEqual(hits[0][1], 0.707, places=2)
        self.assertEqual({conv_of(h[0]) for h in search_vectors(self.conn, qvec, None)}, {'S1', 'S2', 'agent-a1'})
        self.assertEqual(len(search_vectors(self.conn, qvec, None, limit=2)), 2)
        self.assertEqual(search_vectors(self.conn, qvec, [project_key('C:\\нет')]), [])
        self.assertEqual(len(search_vectors(self.conn, qvec, [project_key('C:\\нет'), project_key('C:\\a')])), 5)  # one key that matches is enough

    def test_model_change_waits_for_proof(self):
        embed_pending(self.conn, embed_fn=fake)
        n = self.q('SELECT count(*) FROM vecs')

        def not_found(texts, model):
            raise RuntimeError('модель %s не найдена: выполните ollama pull %s' % (model, model))

        r = embed_pending(self.conn, model='typo', embed_fn=not_found)  # a typo in the model name must not cost the owner the index
        self.assertIn('typo', r['errors'][0]['error'])
        self.assertEqual((self.q('SELECT count(*) FROM vecs'), vector_status(self.conn)['model']), (n, MODEL))
        self.assertEqual(r['pending'], self.records)  # all of it is still to be recomputed for the new model

    def test_search_vectors_ignores_wrong_length_blobs(self):
        embed_pending(self.conn, embed_fn=fake)
        tool = self.q("SELECT rowid FROM chunks WHERE kind = 'tool'")
        self.conn.execute("INSERT INTO vecs VALUES(?, 0, 'S1', X'0102')", (tool,))
        hits = search_vectors(self.conn, pack(fake(['ёжик'])[0]), None)
        self.assertEqual(len(hits), self.records)
        self.assertNotIn(tool, [c for c, _ in hits])

    def add(self, name, objs):
        self.write(name, objs)
        update(self.conn, self.projects)

    def test_hybrid_finds_a_paraphrase_that_words_miss(self):
        self.add('P1/S3.jsonl', [owner_line('кэш протух')])
        embed_pending(self.conn, embed_fn=synonym)
        self.assertEqual(search(self.conn, 'истёк'), [])
        hits, state = hybrid_search(self.conn, 'истёк', embed_fn=synonym)
        self.assertEqual(state, 'ok')
        self.assertEqual((hits[0]['conv'], hits[0]['kind'], hits[0]['snippet']), ('S3', 'owner', 'кэш протух'))
        self.assertEqual(set(hits[0]), {'conv', 'parent', 'ts', 'kind', 'pos', 'snippet', 'id'})

    def test_hybrid_merges_both_legs(self):
        embed_pending(self.conn, embed_fn=fake)
        hits, state = hybrid_search(self.conn, 'ёжик в тумане', embed_fn=fake)
        words = search(self.conn, 'ёжик в тумане', limit=50, budget=10 ** 9)
        self.assertEqual(state, 'ok')
        self.assertEqual(hits[0]['id'], words[0]['id'])
        self.assertIn('«', hits[0]['snippet'])  # found by both: the snippet of the word search
        self.assertEqual(len({h['id'] for h in hits}), len(hits))
        self.assertGreater(len(hits), len(words))  # records that only the vectors found are added

    def test_hybrid_skips_the_mods_own_calls(self):
        own = self.conn.execute("INSERT INTO chunks(text, conv, pos, ts, kind) VALUES('mcp__cacheguard__recall: кэш', 'S1', 1, ?, 'tool')", (TS,)).lastrowid
        embed_pending(self.conn, embed_fn=fake)
        hits, state = hybrid_search(self.conn, 'кэш', embed_fn=fake)
        self.assertEqual(state, 'ok')
        self.assertTrue(hits)
        self.assertNotIn(own, [h['id'] for h in hits])

    def test_hybrid_shows_a_resumed_copy_once(self):
        self.add('P1/S3.jsonl', [owner_line('ёжик в тумане')])  # S1's first message, copied
        self.add('P1/S4.jsonl', [owner_line('кэш протух')])
        self.add('P1/S5.jsonl', [owner_line('кэш протух')])
        embed_pending(self.conn, embed_fn=synonym)
        for query, said in (('ёжик в тумане', 'ёжик в тумане'), ('истёк', 'кэш протух')):  # found by words and vectors, and by vectors alone
            hits, state = hybrid_search(self.conn, query, embed_fn=synonym)
            self.assertEqual(state, 'ok')
            self.assertEqual(len([h for h in hits if h['kind'] == 'owner' and h['snippet'].replace('«', '').replace('»', '') == said]), 1)

    def test_vector_only_hit_shows_the_best_piece(self):
        text = 'альфа ' * 250 + 'бета ' * 300 + 'протух ' * 220
        self.add('P1/S3.jsonl', [owner_line(text)])
        embed_pending(self.conn, embed_fn=synonym)
        hits, _ = hybrid_search(self.conn, 'истёк', embed_fn=synonym)
        self.assertEqual(hits[0]['snippet'], chunks_of(text)[3][:300])  # not the first piece

    def test_other_wordings_find_an_error_by_its_trace(self):  # stage 4: the action missed, the error text found it
        self.add('P1/S3.jsonl', [owner_line('запускаю сборку'),
                                 {'type': 'user', 'timestamp': TS, 'cwd': 'C:\\a', 'message': {'role': 'user', 'content': [
                                     {'type': 'tool_result', 'tool_use_id': 't1', 'content': 'HTTP 429: лимит 120 запросов в час', 'is_error': True}]}}])
        calls = []

        def counted(texts, model, timeout=None):
            calls.append(len(texts))
            return fake(texts)

        embed_pending(self.conn, embed_fn=fake)
        self.assertEqual(search(self.conn, 'повторный запуск лишние вызовы'), [])
        hits, state = hybrid_search(self.conn, 'повторный запуск лишние вызовы', also=['429 лимит запросов'], embed_fn=counted)
        self.assertEqual(state, 'ok')
        self.assertEqual((hits[0]['kind'], hits[0]['conv']), ('error', 'S3'))
        self.assertEqual(calls, [2])  # one call to Ollama for every wording

        def down(texts, model, timeout):
            calls.append(len(texts))
            raise RuntimeError('Ollama не отвечает: тест')

        calls.clear()
        hits, state = hybrid_search(self.conn, 'повторный запуск', also=['429', '', 'повторный запуск'], embed_fn=down)
        self.assertEqual((hits[0]['kind'], state, calls), ('error', 'Ollama не отвечает: тест', [2]))  # words for both, one wait, no blanks or repeats
        many = hybrid_search(self.conn, 'кэш', also=['a', 'b', 'c', 'd', 'e'], embed_fn=counted)
        self.assertEqual(calls[-1], 4)  # the query and three other wordings at most
        self.assertLessEqual(len(many[0]), 8)

    def test_vector_leg_down_falls_back_to_words_with_reason(self):
        embed_pending(self.conn, embed_fn=fake)
        words = search(self.conn, 'кэш')

        def down(texts, model, timeout):
            raise RuntimeError('Ollama не отвечает: тест')

        def odd(texts, model, timeout):
            raise ValueError('странно')

        self.assertEqual(hybrid_search(self.conn, 'кэш', embed_fn=down), (words, 'Ollama не отвечает: тест'))
        self.assertEqual(hybrid_search(self.conn, 'кэш', embed_fn=odd), (words, 'странно'))  # any exception, not only RuntimeError
        hits, state = hybrid_search(self.conn, 'кэш', model='other', embed_fn=never)  # the vectors in the base are of another model
        self.assertEqual(hits, words)
        self.assertIn('other', state)
        with mock.patch.dict(sys.modules, {'numpy': None}):
            hits, state = hybrid_search(self.conn, 'кэш', embed_fn=fake)
        self.assertEqual(hits, words)
        self.assertIn('numpy', state)

    def test_query_is_embedded_with_a_short_timeout(self):  # a hung Ollama must leave room in the mod's 120 s for the word hits
        embed_pending(self.conn, embed_fn=fake)
        seen = []
        hybrid_search(self.conn, 'кэш', embed_fn=lambda texts, model, timeout: (seen.append(timeout), fake(texts))[1])
        self.assertEqual(seen, [45])

    def test_hung_ollama_falls_back_to_words_with_reason(self):
        embed_pending(self.conn, model='slow', embed_fn=fake)
        _, url, _ = serve(self)
        t = time.monotonic()
        hits, state = hybrid_search(self.conn, 'кэш', model='slow', embed_fn=lambda texts, model, timeout: ollama_embed(texts, model, url, 0.2))
        self.assertLess(time.monotonic() - t, 0.9)
        self.assertEqual(hits, search(self.conn, 'кэш'))
        self.assertIn('Ollama не отвечает', state)

    def test_vector_only_hit_failing_to_build_falls_back_to_words(self):  # the transcript may be rewritten between the search and the read
        embed_pending(self.conn, embed_fn=fake)
        words = search(self.conn, 'ёжик в тумане')
        with mock.patch('vectors._best_piece', side_effect=IndexError('гонка')) as piece:
            hits, state = hybrid_search(self.conn, 'ёжик в тумане', embed_fn=fake)
        piece.assert_called()
        self.assertEqual((hits, state), (words, 'гонка'))

    def test_other_wordings_read_the_vectors_once(self):
        embed_pending(self.conn, embed_fn=fake)
        reads = []
        self.conn.set_trace_callback(lambda sql: reads.append(sql) if 'SELECT chunk, vec FROM vecs' in sql else None)
        self.addCleanup(self.conn.set_trace_callback, None)
        hits, state = hybrid_search(self.conn, 'ёжик в тумане', also=['кэш', 'другой проект', 'итог'], embed_fn=fake)
        self.assertEqual(state, 'ok')
        self.assertEqual(len(reads), 1)  # was one read per wording

    def test_search_many_answers_each_query_on_its_own(self):
        embed_pending(self.conn, embed_fn=fake)
        qs = [pack(v) for v in fake(['ёжик в тумане', 'совсем другое про кэш'])]
        many = search_many(self.conn, qs, None)
        cache = self.q("SELECT rowid FROM chunks WHERE text = 'совсем другое про кэш'")
        self.assertEqual(many[1][0][0], cache)
        self.assertNotEqual(many[0][0][0], cache)
        self.assertEqual(many, [search_vectors(self.conn, q, None) for q in qs])
        self.assertEqual([len(h) for h in search_many(self.conn, qs, None, limit=2)], [2, 2])
        empty = search_many(self.conn, qs, project_key('C:\нет'))
        self.assertEqual(empty, [[], []])
        self.assertIsNot(empty[0], empty[1])

    def test_one_or_two_words_favour_the_word_search(self):
        embed_pending(self.conn, embed_fn=fake)
        w1, w2, v1 = [r[0] for r in self.conn.execute("SELECT rowid FROM chunks WHERE kind = 'owner' ORDER BY rowid LIMIT 3")]
        word_hits = [dict(id=i, conv='', parent='', ts='', kind='owner', pos=0, snippet='x%d' % i) for i in (w1, w2)]
        with mock.patch.object(memdb, 'word_hits', return_value=word_hits), mock.patch('vectors.search_many', return_value=[[(v1, 0.9)]]):
            ids = lambda query: [h['id'] for h in hybrid_search(self.conn, query, embed_fn=fake)[0]]
            self.assertEqual(ids('один два'), [w1, w2, v1])  # the vector hit counts half
            self.assertEqual(ids('один два три'), [w1, v1, w2])

    def test_no_vectors_falls_back_with_reason(self):
        hits, state = hybrid_search(self.conn, 'кэш', embed_fn=never)
        self.assertEqual(hits, search(self.conn, 'кэш'))
        self.assertNotEqual(state, 'ok')

    def test_scope_and_budget_hold(self):
        self.add('P1/S4.jsonl', [owner_line('бюджет ' + 'слово ' * 200 + str(i), ts='2026-10-13T10:00:%02d.000Z' % i) for i in range(30)])
        self.add('P2/S5.jsonl', [owner_line('бюджет другого проекта', cwd='C:\\b')])
        embed_pending(self.conn, embed_fn=fake)
        a = project_key('C:\\a')
        hits, state = hybrid_search(self.conn, 'бюджет', project=a, embed_fn=fake)
        self.assertEqual(state, 'ok')
        self.assertTrue(0 < len(hits) <= 8)
        self.assertLessEqual(sum(len(h['snippet']) for h in hits), 7000)
        self.assertTrue(all(h['conv'] in ('S1', 'S4', 'agent-a1') for h in hits))
        small, _ = hybrid_search(self.conn, 'бюджет', project=a, budget=300, embed_fn=fake)
        self.assertTrue(0 < len(small) < len(hits) and sum(len(h['snippet']) for h in small) <= 300)
        self.assertEqual(len(hybrid_search(self.conn, 'бюджет', project=a, limit=3, embed_fn=fake)[0]), 3)

    def test_subagent_counts_half_in_the_vector_leg(self):
        # the subagent row is indexed first: on equal similarity only the weight can put the owner's row ahead
        self.add('P1/S1/subagents/agent-a2.jsonl', [owner_line('начало'), claude_line('кэш протух'), claude_line('итог')])
        self.add('P1/S3.jsonl', [owner_line('кэш протух')])
        embed_pending(self.conn, embed_fn=synonym)
        hits, state = hybrid_search(self.conn, 'истёк', limit=50, embed_fn=synonym)  # half weight sends the subagent row far down
        self.assertEqual(state, 'ok')
        order =[(h['conv'], h['kind']) for h in hits]
        self.assertLess(order.index(('S3', 'owner')), order.index(('agent-a2', 'sub')))

    def test_rewritten_file_keeps_its_vectors(self):  # stage 2 §6
        embed_pending(self.conn, embed_fn=fake)
        before = self.conn.execute("SELECT * FROM vecs WHERE conv = 'S1' ORDER BY chunk, part").fetchall()
        self.write('P1/S1.jsonl', [owner_line('ёжик')])  # shorter: the index re-reads it from the start and adds what is new
        update(self.conn, self.projects)
        self.assertEqual(self.conn.execute("SELECT * FROM vecs WHERE conv = 'S1' ORDER BY chunk, part").fetchall(), before)
        self.assertEqual(embed_pending(self.conn, embed_fn=fake)['done'], 1)  # only the new record
        hits, state = hybrid_search(self.conn, 'совсем другое', embed_fn=fake)  # the old text is still found, by words and by vectors
        self.assertEqual((state, hits[0]['conv']), ('ok', 'S1'))

    def test_rebuild_keeps_the_vectors_and_the_count(self):  # stage 2 §6
        embed_pending(self.conn, embed_fn=fake)
        vecs, rows = self.conn.execute('SELECT * FROM vecs ORDER BY chunk, part').fetchall(), self.q('SELECT count(*) FROM chunks')
        self.assertEqual(update(self.conn, self.projects, rebuild=True)['rows'], 0)
        self.assertEqual((self.conn.execute('SELECT * FROM vecs ORDER BY chunk, part').fetchall(), self.q('SELECT count(*) FROM chunks')), (vecs, rows))
        self.assertEqual(embed_pending(self.conn, embed_fn=never)['done'], 0)

    def test_new_vectors_of_a_record_go_out_again(self):
        embed_pending(self.conn, embed_fn=fake)
        self.conn.execute('UPDATE keys SET vec_pushed = 1')
        self.assertEqual(self.q('SELECT count(*) FROM keys WHERE vec_pushed = 0'), 0)
        embed_pending(self.conn, model='other', embed_fn=fake)  # another model: every vector is new
        self.assertEqual(self.q('SELECT count(*) FROM keys WHERE vec_pushed = 1'), 0)


def serve(test):
    """A fake Ollama on a local port: (server, url, requests seen). Model names pick the answer: missing, broken, garbage, empty, short, slow."""
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append((self.path, body))
            if body['model'] == 'slow':  # never answers; the client has given up long before
                time.sleep(3)  # long enough that a loaded CPU cannot blur the 0.2 s give-up
                return
            code, out = {'missing': (404, {'error': 'model not found'}), 'broken': (500, {'error': 'boom'}),
                         'garbage': (200, {'oops': 1}), 'empty': (200, {'embeddings': []}),
                         'short': (200, {'embeddings': [[0.5] * 768 for _ in body['input'][1:]]})}.get(
                body['model'], (200, {'embeddings': [[0.5] * 768 for _ in body['input']]}))
            data = json.dumps(out).encode()
            self.send_response(code)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    srv = http.server.HTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    test.addCleanup(srv.server_close)
    test.addCleanup(srv.shutdown)
    return srv, 'http://127.0.0.1:%d' % srv.server_address[1], seen


class OllamaEmbedTest(unittest.TestCase):
    def setUp(self):
        use_lang(self, 'ru')  # the messages these tests read are the Russian ones of stage 1

    def test_ollama_embed_against_a_local_fake_server(self):
        srv, url, seen = serve(self)
        self.assertEqual([len(v) for v in ollama_embed(['a', 'b'], url=url)], [768, 768])
        self.assertEqual(seen, [('/api/embed', {'model': MODEL, 'input': ['a', 'b'], 'keep_alive': '30m'})])
        with self.assertRaisesRegex(RuntimeError, 'модель missing не найдена: выполните ollama pull missing'):
            ollama_embed(['a'], 'missing', url)
        for m in ('broken', 'garbage'):
            with self.assertRaisesRegex(RuntimeError, 'Ollama не отвечает'):
                ollama_embed(['a'], m, url)
        srv.shutdown()
        srv.server_close()  # the port is closed now
        with self.assertRaisesRegex(RuntimeError, 'Ollama не отвечает'):
            ollama_embed(['a'], url=url)

    def test_ollama_embed_rejects_too_few_vectors(self):  # hybrid_search takes [0] of the answer: an empty one must not become an IndexError
        _, url, _ = serve(self)
        for m in ('empty', 'short'):
            with self.assertRaisesRegex(RuntimeError, 'Ollama не отвечает: вернул'):
                ollama_embed(['a', 'b'], m, url)

    def test_ollama_embed_gives_up_after_its_timeout(self):
        _, url, _ = serve(self)
        start = time.monotonic()
        with self.assertRaisesRegex(OllamaDown, 'Ollama не отвечает'):
            ollama_embed(['a'], 'slow', url, timeout=0.2)
        self.assertLess(time.monotonic() - start, 2.0)  # the server sleeps 3 s

    def test_system_proxy_is_not_used(self):  # the owner's conversation text must not travel through a proxy
        _, url, _ = serve(self)
        with socket.socket() as s:  # a port that nobody listens on
            s.bind(('127.0.0.1', 0))
            dead = s.getsockname()[1]
        urllib.request.install_opener(None)  # urlopen caches the proxies it saw first
        self.addCleanup(urllib.request.install_opener, None)
        proxy = 'http://127.0.0.1:%d' % dead
        with mock.patch.dict(os.environ, {'HTTP_PROXY': proxy, 'http_proxy': proxy, 'NO_PROXY': '', 'no_proxy': ''}):
            self.assertEqual(len(ollama_embed(['a'], url=url)), 1)


if __name__ == '__main__':
    unittest.main()
