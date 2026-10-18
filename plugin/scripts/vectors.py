import datetime
import json
import re
import time
import urllib.error
import urllib.request
import memdb

DIMS, CHUNK, OVERLAP = 256, 1500, 200
KINDS = ('owner', 'claude', 'task', 'report', 'sub')  # tool summaries are not embedded
DOC_PREFIX, QUERY_PREFIX = 'title: none | text: ', 'task: search result | query: '
MODEL, OLLAMA = 'embeddinggemma', 'http://localhost:11434'
_RECORDS = "FROM chunks WHERE kind IN %s" % (KINDS,)
_PENDING = _RECORDS + " AND NOT EXISTS (SELECT 1 FROM vecs WHERE chunk = chunks.rowid)"
# the row must still be the one that was read: a concurrent rewrite of the transcript may have replaced it
_STORE = "INSERT OR REPLACE INTO vecs SELECT rowid, ?, conv, ? FROM chunks WHERE rowid = ? AND text = ?"


def _np():
    try:
        import numpy
    except ImportError:
        raise RuntimeError('numpy не установлен: pip install numpy') from None
    return numpy


def _meta(conn, key):
    return (conn.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone() or (None,))[0]


def chunks_of(text):
    """Windows of at most CHUNK characters, each overlapping the previous by OVERLAP; a short text is one piece."""
    return [text[i:i + CHUNK] for i in range(0, max(len(text) - OVERLAP, 1), CHUNK - OVERLAP)]


def ollama_embed(texts, model=MODEL, url=OLLAMA):
    req = urllib.request.Request(url + '/api/embed', json.dumps({'model': model, 'input': texts, 'keep_alive': '30m'}).encode(),
                                 {'Content-Type': 'application/json'})
    try:
        # no proxy: the text of the owner's conversations must stay on this computer
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=120) as r:
            return json.load(r)['embeddings']
    except urllib.error.HTTPError as e:  # before OSError: HTTPError is one
        if e.code == 404:
            raise RuntimeError('модель %s не найдена: выполните ollama pull %s' % (model, model)) from None
        raise RuntimeError('Ollama не отвечает: %s' % e) from None
    except (OSError, ValueError, KeyError) as e:
        raise RuntimeError('Ollama не отвечает: %s' % e) from None


def pack(vec):
    """The first DIMS numbers (the model is trained so that a prefix keeps the meaning), unit length, float32 bytes."""
    np = _np()
    if len(vec) < DIMS:
        raise ValueError('вектор из %d чисел, нужно не меньше %d' % (len(vec), DIMS))
    v = np.array(vec[:DIMS], dtype='float32')
    return (v / (np.linalg.norm(v) or 1)).tobytes()


def embed_pending(conn, model=MODEL, embed_fn=ollama_embed, max_seconds=120, batch=16, now=time.monotonic):
    """Embed records that have no vectors yet, `batch` records per transaction, until `max_seconds` pass. Progress is kept on an error."""
    _np()
    start, done, errors = now(), 0, []
    # vectors of different models cannot be compared, but the old ones go only once the new model has answered (a typo in its name costs nothing)
    changed = _meta(conn, 'embed_model') != model
    todo = lambda: _RECORDS if changed else _PENDING  # until then every record is to be done again
    while True:
        rows = conn.execute('SELECT rowid, text ' + todo() + ' ORDER BY rowid LIMIT ?', (batch,)).fetchall()
        if not rows:
            break
        try:
            parts = [(rid, text, i, p) for rid, text in rows for i, p in enumerate(chunks_of(text))]
            texts = [DOC_PREFIX + p[3] for p in parts]
            # slices of `batch` texts: one huge record must not turn into one request that times out again and again
            out = [v for i in range(0, len(texts), batch) for v in embed_fn(texts[i:i + batch], model)]
            if len(out) != len(texts):
                raise RuntimeError('Ollama вернул %d векторов вместо %d' % (len(out), len(texts)))
            blobs = [pack(v) for v in out]  # the model works without the write lock; the transaction is only the inserts
            conn.execute('BEGIN IMMEDIATE')
            with conn:
                if changed:
                    conn.execute('DELETE FROM vecs')
                    conn.execute("INSERT OR REPLACE INTO meta VALUES('embed_model', ?)", (model,))
                conn.executemany(_STORE, [(i, b, rid, text) for (rid, text, i, _), b in zip(parts, blobs)])
            changed = False
        except Exception as e:
            errors.append({'error': '%s: %s' % (type(e).__name__, e)})
            break
        done += len(rows)
        if now() - start > max_seconds:
            break
    if done:
        conn.execute("INSERT OR REPLACE INTO meta VALUES('vec_updated', ?)", (datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),))
    return {'done': done, 'pending': conn.execute('SELECT count(*) ' + todo()).fetchone()[0], 'seconds': now() - start,
            'model': model, 'errors': errors}


def search_vectors(conn, qvec, project, limit=50):
    """(chunks.rowid, cosine) pairs, best first; of several pieces of one record the best counts. Same project scope as memdb.search."""
    # shortcut: brute force over all vectors in numpy; switch to sqlite-vec past ~500k vectors (the BLOBs move as they are)
    np = _np()
    # a BLOB of another length (a broken write, another model) would break the matrix: such rows are left out
    rows = conn.execute('SELECT chunk, vec FROM vecs WHERE length(vec) = :n AND ' + memdb.IN_PROJECT, {'p': project, 'n': 4 * DIMS}).fetchall()
    if not rows:
        return []
    sims = np.frombuffer(b''.join(r[1] for r in rows), dtype='float32').reshape(len(rows), DIMS) @ np.frombuffer(qvec, dtype='float32')
    best = {}
    for (chunk, _), s in zip(rows, sims.tolist()):
        best[chunk] = max(s, best.get(chunk, s))
    return sorted(best.items(), key=lambda kv: -kv[1])[:limit]


def vector_status(conn):
    total = conn.execute('SELECT count(*) ' + _RECORDS).fetchone()[0]
    return {'model': _meta(conn, 'embed_model'), 'with_vectors': total - conn.execute('SELECT count(*) ' + _PENDING).fetchone()[0],
            'total': total, 'updated': _meta(conn, 'vec_updated')}


def rrf(lists, weights, k=60):
    """Reciprocal Rank Fusion: score(id) = sum of w / (k + rank) over the lists, ranks from 1. Best first, a tie keeps the order of
    first appearance. A weight may be a function of the id."""
    score = {}
    for ids, w in zip(lists, weights):
        for rank, i in enumerate(ids, 1):
            score[i] = score.get(i, 0) + (w(i) if callable(w) else w) / (k + rank)
    return sorted(score, key=score.__getitem__, reverse=True)


def _best_piece(conn, rowid, qvec):
    """The first 300 characters of the piece of the record that is closest to the query."""
    np = _np()
    q = np.frombuffer(qvec, dtype='float32')
    rows = conn.execute('SELECT part, vec FROM vecs WHERE chunk = ? AND length(vec) = ?', (rowid, 4 * DIMS)).fetchall()
    part = max(rows, key=lambda r: np.frombuffer(r[1], dtype='float32') @ q)[0]
    return chunks_of(conn.execute('SELECT text FROM chunks WHERE rowid = ?', (rowid,)).fetchone()[0])[part][:300]


def hybrid_search(conn, query, project=None, limit=8, budget=7000, model=MODEL, embed_fn=ollama_embed):
    """memdb.search merged with the vector search by RRF: (hits as in memdb.search, 'ok' or why only the words were used)."""
    words = memdb.search(conn, query, project, 50, 10 ** 9)
    state, vec, info = 'ok', [], {}
    try:
        _np()
        have = _meta(conn, 'embed_model')
        if have != model:
            raise LookupError('векторов модели %s нет (в базе: %s)' % (model, have or 'ничего'))
        qvec = pack(embed_fn([QUERY_PREFIX + query], model)[0])
        found = search_vectors(conn, qvec, project, 50)
        vec, info = found, {r[0]: r[1:] for r in conn.execute(  # both or neither: a half-built leg would break the ranking
            'SELECT rowid, conv, pos, ts, kind, (SELECT parent FROM convs WHERE convs.conv = chunks.conv) FROM chunks WHERE rowid IN (%s)'
            % ','.join('?' * len(found)), [c for c, _ in found])}
    except Exception as e:  # whatever went wrong, the words still answer
        state = str(e) or type(e).__name__
    # a few words give the exact match the lead; subagent chatter counts half, as in the word search
    wv = 0.5 if len(re.findall(r'\w+', query)) <= 2 else 1.0
    ranked = rrf([[h['id'] for h in words], [c for c, _ in vec]],
                 [1.0, lambda i: wv * (0.5 if info[i][3] in ('sub', 'tool') else 1.0)])
    hits = {h['id']: h for h in words}
    for i in ranked[:limit]:
        if i not in hits:  # only the vectors found it
            conv, pos, ts, kind, parent = info[i]
            hits[i] = {'conv': conv, 'parent': parent or '', 'ts': ts, 'kind': kind, 'pos': pos, 'snippet': _best_piece(conn, i, qvec), 'id': i}
    return memdb.cut([hits[i] for i in ranked[:limit]], budget), state
