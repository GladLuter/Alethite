import datetime
import json
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


def chunks_of(text):
    """Windows of at most CHUNK characters, each overlapping the previous by OVERLAP; a short text is one piece."""
    return [text[i:i + CHUNK] for i in range(0, max(len(text) - OVERLAP, 1), CHUNK - OVERLAP)]


def ollama_embed(texts, model=MODEL, url=OLLAMA):
    req = urllib.request.Request(url + '/api/embed', json.dumps({'model': model, 'input': texts, 'keep_alive': '30m'}).encode(),
                                 {'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
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
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        if (conn.execute("SELECT value FROM meta WHERE key = 'embed_model'").fetchone() or (None,))[0] != model:
            conn.execute('DELETE FROM vecs')  # vectors of different models cannot be compared
            conn.execute("INSERT OR REPLACE INTO meta VALUES('embed_model', ?)", (model,))
    while True:
        rows = conn.execute('SELECT rowid, text ' + _PENDING + ' ORDER BY rowid LIMIT ?', (batch,)).fetchall()
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
                conn.executemany(_STORE, [(i, b, rid, text) for (rid, text, i, _), b in zip(parts, blobs)])
        except Exception as e:
            errors.append({'error': '%s: %s' % (type(e).__name__, e)})
            break
        done += len(rows)
        if now() - start > max_seconds:
            break
    if done:
        conn.execute("INSERT OR REPLACE INTO meta VALUES('vec_updated', ?)", (datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),))
    return {'done': done, 'pending': conn.execute('SELECT count(*) ' + _PENDING).fetchone()[0], 'seconds': now() - start,
            'model': model, 'errors': errors}


def search_vectors(conn, qvec, project, limit=50):
    """(chunks.rowid, cosine) pairs, best first; of several pieces of one record the best counts. Same project scope as memdb.search."""
    # shortcut: brute force over all vectors in numpy; switch to sqlite-vec past ~500k vectors (the BLOBs move as they are)
    np = _np()
    rows = conn.execute('SELECT chunk, vec FROM vecs WHERE ' + memdb.IN_PROJECT, {'p': project}).fetchall()
    if not rows:
        return []
    sims = np.frombuffer(b''.join(r[1] for r in rows), dtype='float32').reshape(len(rows), DIMS) @ np.frombuffer(qvec, dtype='float32')
    best = {}
    for (chunk, _), s in zip(rows, sims.tolist()):
        best[chunk] = max(s, best.get(chunk, s))
    return sorted(best.items(), key=lambda kv: -kv[1])[:limit]


def vector_status(conn):
    meta = lambda k: (conn.execute('SELECT value FROM meta WHERE key = ?', (k,)).fetchone() or (None,))[0]
    total = conn.execute('SELECT count(*) ' + _RECORDS).fetchone()[0]
    return {'model': meta('embed_model'), 'with_vectors': total - conn.execute('SELECT count(*) ' + _PENDING).fetchone()[0],
            'total': total, 'updated': meta('vec_updated')}
