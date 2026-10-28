import datetime
import json
import re
import time
import urllib.error
import urllib.request
import memdb
from texts import t

DIMS, CHUNK, OVERLAP = 256, 1500, 200
KINDS = ('owner', 'claude', 'task', 'report', 'sub', 'error', 'lesson')  # tool summaries are not embedded; errors and lessons are (stage 4)
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
        raise RuntimeError(t('numpy_missing')) from None
    return numpy


def chunks_of(text):
    """Windows of at most CHUNK characters, each overlapping the previous by OVERLAP; a short text is one piece."""
    return [text[i:i + CHUNK] for i in range(0, max(len(text) - OVERLAP, 1), CHUNK - OVERLAP)]


class OllamaDown(RuntimeError):
    """Ollama cannot be reached or does not answer in time: no record is to blame, so nothing is retried record by record."""


def ollama_embed(texts, model=MODEL, url=OLLAMA, timeout=120):
    req = urllib.request.Request(url + '/api/embed', json.dumps({'model': model, 'input': texts, 'keep_alive': '30m'}).encode(),
                                 {'Content-Type': 'application/json'})
    try:
        # no proxy: the text of the owner's conversations must stay on this computer
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as r:
            out = json.load(r)['embeddings']
        if len(out) < len(texts):  # an empty answer would otherwise surface as an IndexError far from here
            raise ValueError(t('too_few_vectors', got=len(out), want=len(texts)))
        return out
    except urllib.error.HTTPError as e:  # before OSError: HTTPError is one
        if e.code == 404:
            raise RuntimeError(t('model_missing', model=model)) from None
        raise RuntimeError(t('ollama_down', e=e)) from None
    except OSError as e:  # refused, timed out
        raise OllamaDown(t('ollama_down', e=e)) from None
    except (ValueError, KeyError) as e:
        raise RuntimeError(t('ollama_down', e=e)) from None


def pack(vec):
    """The first DIMS numbers (the model is trained so that a prefix keeps the meaning), unit length, float32 bytes."""
    np = _np()
    if len(vec) < DIMS:
        raise ValueError(t('short_vector', n=len(vec), dims=DIMS))
    v = np.array(vec[:DIMS], dtype='float32')
    return (v / (np.linalg.norm(v) or 1)).tobytes()


def _embed(rows, model, embed_fn, batch):
    """_STORE parameters for every piece of `rows` ((rowid, text, ...) tuples); raises if the model cannot embed them."""
    parts = [(rid, text, i, p) for rid, text, *_ in rows for i, p in enumerate(chunks_of(text))]
    texts = [DOC_PREFIX + p[3] for p in parts]
    # slices of `batch` texts: one huge record must not turn into one request that times out again and again
    out = [v for i in range(0, len(texts), batch) for v in embed_fn(texts[i:i + batch], model)]
    if len(out) != len(texts):
        raise RuntimeError(t('wrong_count', got=len(out), want=len(texts)))
    return [(i, pack(v), rid, text) for (rid, text, i, _), v in zip(parts, out)]


def embed_pending(conn, model=MODEL, embed_fn=ollama_embed, max_seconds=120, batch=16, now=time.monotonic):
    """Embed records that have no vectors yet, `batch` records per transaction, until `max_seconds` pass. Progress is kept on an error.
    A failed batch is retried record by record. A record that fails alone while a trivial text still embeds is the record's fault: it gets
    an empty vector (a sentinel, counted as done until the model changes) and an entry in 'errors'. If the trivial text fails too,
    Ollama or the model is out (down, hung, no such model): the run stops and marks nothing."""
    _np()
    start, done, errors = now(), 0, []
    # vectors of different models cannot be compared, but the old ones go only once the new model has answered (a typo in its name costs nothing)
    changed = memdb.meta(conn, 'embed_model') != model
    todo = lambda: _RECORDS if changed else _PENDING  # until then every record is to be done again
    fmt = lambda e: '%s: %s' % (type(e).__name__, e)
    is_out, last = False, 0
    while not is_out:
        # after the last row of the previous batch: a row that changed meanwhile (and so got no vector) waits for the next run
        rows = conn.execute('SELECT rowid, text, conv, pos ' + todo() + ' AND rowid > ? ORDER BY rowid LIMIT ?', (last, batch)).fetchall()
        if not rows:
            break
        last = rows[-1][0]
        stored, bad, ok = [], [], len(rows)
        try:
            stored = _embed(rows, model, embed_fn, batch)
        except OllamaDown as e:  # a hung Ollama would otherwise cost two more timeouts before the run stops
            errors.append({'error': fmt(e)})
            break
        except Exception:
            stored, ok = [], 0
            for row in rows:
                try:
                    stored += _embed([row], model, embed_fn, batch)
                    ok += 1
                except Exception as e:
                    try:
                        _embed([(0, 'ping')], model, embed_fn, batch)
                    except Exception as gone:
                        errors.append({'error': fmt(gone)})
                        is_out = True
                        break
                    bad.append(row)
                    errors.append({'conv': row[2], 'pos': row[3], 'error': fmt(e)})
        if stored or bad:  # the model has answered without the write lock; the transaction is only the inserts
            try:
                conn.execute('BEGIN IMMEDIATE')
                with conn:
                    # asked again under the lock: a sync's restore or another embed may have taken this model on since the start
                    if changed and memdb.meta(conn, 'embed_model') != model:
                        conn.execute('DELETE FROM vecs')
                        conn.execute('UPDATE keys SET vec_pushed = 0')
                        conn.execute("INSERT OR REPLACE INTO meta VALUES('embed_model', ?)", (model,))
                    conn.executemany(_STORE, stored + [(0, b'', rid, text) for rid, text, *_ in bad])
                    conn.executemany('UPDATE keys SET vec_pushed = 0 WHERE id = ?', {(s[2],) for s in stored})  # new vectors of ours go out again
            except Exception as e:  # e.g. the index is being updated and the database stays locked
                errors.append({'error': fmt(e)})
                break
            changed = False
        done += ok
        if now() - start > max_seconds:
            break
    if done:
        conn.execute("INSERT OR REPLACE INTO meta VALUES('vec_updated', ?)", (datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),))
    return {'done': done, 'pending': conn.execute('SELECT count(*) ' + todo()).fetchone()[0], 'seconds': now() - start,
            'model': model, 'errors': errors}


def search_vectors(conn, qvec, project, limit=50):
    """(chunks.rowid, cosine) pairs, best first; of several pieces of one record the best counts. Same project scope as memdb.word_hits."""
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
    total, pending = conn.execute('SELECT count(*), coalesce(sum(NOT EXISTS (SELECT 1 FROM vecs WHERE chunk = chunks.rowid)), 0) ' + _RECORDS).fetchone()
    return {'model': memdb.meta(conn, 'embed_model'), 'with_vectors': total - pending,
            'total': total, 'failed': conn.execute('SELECT count(*) FROM vecs WHERE length(vec) = 0').fetchone()[0],  # sentinels, see embed_pending
            'updated': memdb.meta(conn, 'vec_updated')}


def rrf_scores(lists, weights, k=60):
    """Reciprocal Rank Fusion: score(id) = sum of w / (k + rank) over the lists, ranks from 1, in the order of first appearance.
    A weight may be a function of the id."""
    score = {}
    for ids, w in zip(lists, weights):
        for rank, i in enumerate(ids, 1):
            score[i] = score.get(i, 0) + (w(i) if callable(w) else w) / (k + rank)
    return score


def _best_piece(conn, rowid, qvec):
    """The first 300 characters of the piece of the record that is closest to the query."""
    np = _np()
    q = np.frombuffer(qvec, dtype='float32')
    rows = conn.execute('SELECT part, vec FROM vecs WHERE chunk = ? AND length(vec) = ?', (rowid, 4 * DIMS)).fetchall()
    part = max(rows, key=lambda r: np.frombuffer(r[1], dtype='float32') @ q)[0]
    return chunks_of(conn.execute('SELECT text FROM chunks WHERE rowid = ?', (rowid,)).fetchone()[0])[part][:300]


def _ranked(conn, query, project, limit, qvec):
    """The hits of one query, best first and not cut yet, and their RRF scores: the word hits, merged with the vector hits when `qvec`
    is given."""
    # repeats are cut only at the end: both copies of a message are word hits with one snippet there, but the vector leg shows one of them with another
    words = memdb.word_hits(conn, query, project, 50)
    if qvec is None:
        return words, rrf_scores([[h['id'] for h in words]], [1.0])
    vec = search_vectors(conn, qvec, project, 50)
    info = {r[0]: r[1:] for r in conn.execute(
        'SELECT rowid, conv, pos, ts, kind, (SELECT parent FROM convs WHERE convs.conv = chunks.conv) FROM chunks WHERE rowid IN (%s)'
        % ','.join('?' * len(vec)), [c for c, _ in vec])}
    # a few words give the exact match the lead; subagent chatter counts half, as in the word search
    wv = 0.5 if len(re.findall(r'\w+', query)) <= 2 else 1.0
    score = rrf_scores([[h['id'] for h in words], [c for c, _ in vec]],
                       [1.0, lambda i: wv * (0.5 if info[i][3] in ('sub', 'tool') else 1.0)])
    hits = {h['id']: h for h in words}
    ranked = sorted(score, key=score.__getitem__, reverse=True)[:3 * limit]  # the spare ones stand in for the repeats cut drops
    for i in ranked:
        if i not in hits:  # only the vectors found it
            conv, pos, ts, kind, parent = info[i]
            hits[i] = {'conv': conv, 'parent': parent or '', 'ts': ts, 'kind': kind, 'pos': pos, 'snippet': _best_piece(conn, i, qvec), 'id': i}
    return [hits[i] for i in ranked], {i: score[i] for i in ranked}


ALSO = 3  # other wordings a search takes at most


def hybrid_search(conn, query, project=None, limit=8, budget=7000, model=MODEL, embed_fn=ollama_embed, also=()):
    """memdb.word_hits merged with the vector search by RRF: (hits as in memdb.cut, 'ok' or why only the words were used).
    `also`: other wordings of the same search (stage 4: a past error is found by its trace, the error text, as often as by the action
    that caused it), each searched the same way; the lists are merged by RRF, the main query first, within the one budget and limit."""
    queries = [query] + [q for q in also if q.strip() and q != query][:ALSO]
    state, qvecs = 'ok', [None] * len(queries)
    try:  # everything that needs the vectors, so that whatever goes wrong (even a transcript rewritten meanwhile) leaves the word hits
        _np()
        have = memdb.meta(conn, 'embed_model')
        if have != model:
            raise LookupError(t('no_model_vectors', model=model, have=have or t('nothing')))
        # one call for every wording: a hung Ollama must leave room for the words inside the mod's 120 s, once and not per wording
        qvecs = [pack(v) for v in embed_fn([QUERY_PREFIX + q for q in queries], model, timeout=45)]
        lists = [_ranked(conn, q, project, limit, v) for q, v in zip(queries, qvecs)]
    except Exception as e:
        state = str(e) or type(e).__name__
        lists = [_ranked(conn, q, project, limit, None) for q in queries]
    if len(lists) == 1:
        return memdb.cut(lists[0][0], budget, limit), state
    # the scores add up across the wordings: a record found by the words and the vectors of one wording leads one that only the vectors
    # of another found; a tie keeps the order of the main query
    hits, total = {}, {}
    for found, score in lists:
        for h in found:
            hits.setdefault(h['id'], h)
            total[h['id']] = total.get(h['id'], 0) + score[h['id']]
    return memdb.cut([hits[i] for i in sorted(total, key=total.__getitem__, reverse=True)], budget, limit), state
