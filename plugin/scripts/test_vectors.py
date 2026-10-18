import http.server
import itertools
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import zlib
from unittest import mock
import numpy
import memdb
from memdb import connect, status, update
from test_memdb import TS, claude_line, owner_line
from transcripts import project_key
from vectors import (CHUNK, DIMS, DOC_PREFIX, MODEL, OVERLAP, chunks_of, embed_pending, ollama_embed, pack,
                     search_vectors, vector_status)

TOOL = dict(type='assistant', timestamp=TS, cwd='C:\\a', message={
    'id': 'm2', 'model': 'claude-opus-5-5', 'role': 'assistant', 'content': [{'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'ls'}}]})


def fake(texts, model=MODEL):
    """A bag of words: each word adds 1 to one of 256 dimensions, so texts with shared words are close."""
    out = []
    for t in texts:
        v = [0.0] * DIMS
        for w in re.findall(r'\w+', t.lower()):
            v[zlib.crc32(w.encode()) % DIMS] += 1.0
        out.append(v)
    return out


class ChunkingTest(unittest.TestCase):
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
        self.assertEqual(vector_status(self.conn), {'model': None, 'with_vectors': 0, 'total': self.records, 'updated': None})
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

    def test_wrong_number_of_vectors_writes_nothing(self):
        r = embed_pending(self.conn, embed_fn=lambda texts, model: fake(texts)[:-1], batch=1000)
        self.assertEqual((r['done'], r['pending']), (0, self.records))
        self.assertIn('вектор', r['errors'][0]['error'])
        self.assertEqual(self.q('SELECT count(*) FROM vecs'), 0)

    def test_rewrite_while_embedding_stores_nothing_for_the_old_text(self):
        def rewrites_s2(texts, model):  # S2 holds the last rowid, so its new record takes the very rowid that was read
            self.write('P2/S2.jsonl', [owner_line('ёжик', cwd='C:\\b')])
            update(self.conn, self.projects)
            return fake(texts)

        ticks = itertools.count()  # one batch only
        r = embed_pending(self.conn, embed_fn=rewrites_s2, max_seconds=0, now=lambda: next(ticks))
        self.assertEqual((r['pending'], self.q("SELECT count(*) FROM vecs WHERE conv = 'S2'")), (1, 0))
        self.assertEqual(embed_pending(self.conn, embed_fn=fake)['done'], 1)
        self.assertEqual(self.q("SELECT vec FROM vecs WHERE conv = 'S2'"), pack(fake([DOC_PREFIX + 'ёжик'])[0]))

    def test_search_vectors_scope_and_order(self):
        embed_pending(self.conn, embed_fn=fake)
        conv_of = lambda rowid: self.q('SELECT conv FROM chunks WHERE rowid = ?', rowid)
        qvec = pack(fake(['ёжик в тумане'])[0])
        hits = search_vectors(self.conn, qvec, project_key('C:\\a'))
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
        self.assertEqual(search_vectors(self.conn, qvec, project_key('C:\\нет')), [])

    def test_rewritten_file_drops_its_vectors(self):
        embed_pending(self.conn, embed_fn=fake)
        self.assertGreater(self.q("SELECT count(*) FROM vecs WHERE conv = 'S1'"), 0)
        self.write('P1/S1.jsonl', [owner_line('ёжик')])  # shorter: the index re-reads it from the start
        update(self.conn, self.projects)
        self.assertEqual(self.q("SELECT count(*) FROM vecs WHERE conv = 'S1'"), 0)
        self.assertGreater(self.q("SELECT count(*) FROM vecs WHERE conv = 'S2'"), 0)
        self.assertEqual(embed_pending(self.conn, embed_fn=fake)['done'], 1)


class OllamaEmbedTest(unittest.TestCase):
    def test_ollama_embed_against_a_local_fake_server(self):
        seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                seen.append((self.path, body))
                code, out = {'missing': (404, {'error': 'model not found'}), 'broken': (500, {'error': 'boom'}),
                             'garbage': (200, {'oops': 1})}.get(body['model'], (200, {'embeddings': [[0.5] * 768 for _ in body['input']]}))
                data = json.dumps(out).encode()
                self.send_response(code)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        srv = http.server.HTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = 'http://127.0.0.1:%d' % srv.server_address[1]
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


if __name__ == '__main__':
    unittest.main()
