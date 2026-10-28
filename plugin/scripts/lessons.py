"""The lessons of stage 4: a past error and what it taught ("X fails because Y"), one record of kind 'lesson' in the index.
In the index, not in the auto-memory: the auto-memory goes into every request and grows with every pitfall, a lesson costs tokens only
when it fits, and the mod finds that out mechanically (the vectors of the owner's message, the trigger in a command), with no model call.
A lesson is a record like any other: it goes to the other computer in the batches, cg.py forget deletes it, it gets vectors, recall finds it."""
import datetime
import hashlib
import re
import time

import memdb
import vectors
from redact import redact
from texts import t

KIND = 'lesson'
FIELDS = ('action', 'symptom', 'cause', 'fix', 'trigger')
_LABELS = {'action': 'Action', 'symptom': 'Symptom', 'cause': 'Cause', 'fix': 'Fix', 'trigger': 'Trigger'}  # one language on every computer
_LINE = re.compile(r'^(%s): (.*)$' % '|'.join(_LABELS.values()), re.M)
REQUIRED = ('action', 'symptom', 'cause')
FIELD_CHARS = 300
TRIGGER_MIN = 8  # a shorter one ("git", "rm") would stop every other command
_SELECT = ("SELECT rowid, conv, pos, ts, text, (SELECT origin FROM keys WHERE id = chunks.rowid) FROM chunks "
           "WHERE rowid IN (SELECT id FROM keys WHERE cls = 'lesson' AND (:c IS NULL OR conv = :c))")  # the lessons of a project all sit in its conv_of


def conv_of(project):
    """One conversation of lessons per project, the same on every computer: the project key is the same there (stage 2)."""
    return 'lessons-' + hashlib.sha1(project.encode('utf-8')).hexdigest()[:12]


def text_of(fields):
    return '\n'.join('%s: %s' % (_LABELS[k], ' '.join(str(fields[k]).split())) for k in FIELDS if fields.get(k))


def fields_of(text):
    names = {v: k for k, v in _LABELS.items()}
    return {names[m.group(1)]: m.group(2) for m in _LINE.finditer(text)}


def _clean(fields):
    """The fields as they are kept, or ValueError with what is wrong: the three that make a lesson, each at most FIELD_CHARS, and a trigger
    long enough to name one command and not every one."""
    out = {}
    for k in FIELDS:
        v = fields.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        if not isinstance(v, str):
            raise ValueError(t('lesson_field_type', field=k))
        v = ' '.join(v.split())
        out[k] = v if len(v) <= FIELD_CHARS else v[:FIELD_CHARS - 1] + '…'
    missing = [k for k in REQUIRED if k not in out]
    if missing:
        raise ValueError(t('lesson_missing', fields=', '.join(missing)))
    if 'trigger' in out and len(out['trigger']) < TRIGGER_MIN:
        raise ValueError(t('lesson_trigger_short', n=TRIGGER_MIN))
    return out


def save(conn, project, fields, now=None):
    """Keep a lesson of `project`: {'id', 'conv', 'pos', 'text', 'new'}. The same text again changes nothing (new: False)."""
    text = redact(text_of(_clean(fields)))
    conv = conv_of(project)
    stamp = now or datetime.datetime.now(datetime.timezone.utc)
    ts = stamp.strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % (stamp.microsecond // 1000)
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        h = memdb.text_hash(text)
        row = conn.execute('SELECT c.rowid, c.pos FROM keys k JOIN chunks c ON c.rowid = k.id WHERE k.conv = ? AND k.cls = ? AND k.hash = ? AND c.text = ?',
                           (conv, KIND, h, text)).fetchone()
        if row:
            return {'id': row[0], 'conv': conv, 'pos': row[1], 'text': text, 'new': False}
        # milliseconds, not a counter: two computers that each save a lesson must not give two different lessons one pos (forget takes a pos)
        last = conn.execute('SELECT max(c.pos) FROM keys k JOIN chunks c ON c.rowid = k.id WHERE k.conv = ?', (conv,)).fetchone()[0] or 0
        pos = max(int(stamp.timestamp() * 1000), last + 1)
        conn.execute('INSERT INTO convs VALUES(?, ?, ?, ?) ON CONFLICT(conv) DO UPDATE SET last_ts = max(last_ts, excluded.last_ts)', (conv, '', ts, ts))
        conn.execute('INSERT OR IGNORE INTO conv_projects VALUES(?, ?)', (conv, project))
        n = conn.execute('SELECT (SELECT count(*) FROM keys WHERE conv = ? AND cls = ? AND hash = ?) + '
                         '(SELECT count(*) FROM forgotten WHERE conv = ? AND cls = ? AND hash = ?)', (conv, KIND, h) * 2).fetchone()[0]
        rid = conn.execute('INSERT INTO chunks(text, conv, pos, ts, kind) VALUES(?, ?, ?, ?, ?)', (text, conv, pos, ts, KIND)).lastrowid
        conn.execute('INSERT INTO keys(id, conv, cls, hash, n) VALUES(?, ?, ?, ?, ?)', (rid, conv, KIND, h, n))
    return {'id': rid, 'conv': conv, 'pos': pos, 'text': text, 'new': True}


def listing(conn, project):
    """The lessons of `project` (None: all), newest first: {'id', 'conv', 'pos', 'ts', 'text', 'trigger', 'from'}."""
    rows = conn.execute(_SELECT + ' ORDER BY pos DESC', {'c': project and conv_of(project)}).fetchall()  # not ts: two lessons of one millisecond tie
    return [{'id': rid, 'conv': conv, 'pos': pos, 'ts': ts, 'text': text, 'trigger': fields_of(text).get('trigger'), 'from': origin}
            for rid, conv, pos, ts, text, origin in rows]


def embed_one(conn, rowid, model, embed_fn=vectors.ollama_embed):
    """Vectors for one new lesson at once, so the next message can find it; nothing when the index holds another model's vectors
    (the next embed run takes it) or Ollama does not answer (the same). 'ok' or why not."""
    try:
        vectors._np()
        if memdb.meta(conn, 'embed_model') != model:
            return t('no_model_vectors', model=model, have=memdb.meta(conn, 'embed_model') or t('nothing'))
        row = conn.execute('SELECT rowid, text FROM chunks WHERE rowid = ?', (rowid,)).fetchone()
        stored = vectors._embed([row], model, lambda texts, m: embed_fn(texts, m, timeout=10), 16)
        conn.execute('BEGIN IMMEDIATE')
        with conn:
            conn.executemany(vectors._STORE, stored)
        return 'ok'
    except Exception as e:
        return str(e) or type(e).__name__


def match(conn, text, project, model, min_score, limit=2, embed_fn=vectors.ollama_embed, timeout=5):
    """The lessons of `project` whose vectors are closest to `text` (the owner's message), at least `min_score` (cosine), best first:
    ([{'id', 'conv', 'pos', 'text', 'score'}], 'ok' or why there was no looking). No lessons with vectors, no call to Ollama at all."""
    known = {r['id']: r for r in listing(conn, project)}
    if not known:
        return [], 'ok'
    try:
        np = vectors._np()
        if memdb.meta(conn, 'embed_model') != model:
            return [], t('no_model_vectors', model=model, have=memdb.meta(conn, 'embed_model') or t('nothing'))
        rows = conn.execute('SELECT chunk, vec FROM vecs WHERE length(vec) = ? AND chunk IN (%s)' % ','.join('?' * len(known)),
                            [4 * vectors.DIMS, *known]).fetchall()
        if not rows:
            return [], 'ok'
        q = np.frombuffer(vectors.pack(embed_fn([vectors.QUERY_PREFIX + text[:2000]], model, timeout=timeout)[0]), dtype='float32')
    except Exception as e:
        return [], str(e) or type(e).__name__
    best = {}
    for rid, vec in rows:
        best[rid] = max(best.get(rid, -1.0), float(np.frombuffer(vec, dtype='float32') @ q))
    found = sorted((rid for rid in best if best[rid] >= min_score), key=best.__getitem__, reverse=True)[:limit]
    return [{**{k: known[rid][k] for k in ('id', 'conv', 'pos', 'text')}, 'score': round(best[rid], 3)} for rid in found], 'ok'
