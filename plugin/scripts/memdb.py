import datetime
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import crypto
from redact import redact
from texts import t
from transcripts import lp, project_key, read_records

DB_PATH = os.path.join(crypto.HOME_DIR, 'index.db')
_SCHEMA = """
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INTEGER, offset INTEGER);
CREATE TABLE IF NOT EXISTS convs(conv TEXT PRIMARY KEY, parent TEXT, first_ts TEXT, last_ts TEXT);
CREATE TABLE IF NOT EXISTS conv_projects(conv TEXT, project TEXT, PRIMARY KEY(conv, project));
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
  text, conv UNINDEXED, pos UNINDEXED, ts UNINDEXED, kind UNINDEXED,
  tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS vecs(chunk INTEGER, part INTEGER, conv TEXT, vec BLOB, PRIMARY KEY(chunk, part));
-- what a record is on every computer (FTS5 takes no new column): conv, class of kind, sha1 of the text, which occurrence of that text.
-- origin: NULL for a record of this computer's transcripts, else the computer it came from; pushed, vec_pushed: in our batches already
CREATE TABLE IF NOT EXISTS keys(id INTEGER PRIMARY KEY, conv TEXT NOT NULL, cls TEXT NOT NULL, hash TEXT NOT NULL, n INTEGER NOT NULL,
  origin TEXT, pushed INTEGER NOT NULL DEFAULT 0, vec_pushed INTEGER NOT NULL DEFAULT 0, UNIQUE(conv, cls, hash, n));
-- keys deleted on purpose (cg.py forget, a text the secret filter now hides): never added again, here or from a batch
CREATE TABLE IF NOT EXISTS forgotten(conv TEXT, cls TEXT, hash TEXT, n INTEGER, PRIMARY KEY(conv, cls, hash, n));
-- forget lines for our next batch
CREATE TABLE IF NOT EXISTS outbox(seq INTEGER PRIMARY KEY, line TEXT NOT NULL);
-- every computer whose folder we read, ourselves included: the last batch taken in, the ones skipped by hand, what its batches said;
-- gen: the generation of its folder those batches belong to (an index from before stage 5 gets the column in connect)
CREATE TABLE IF NOT EXISTS peers(machine TEXT PRIMARY KEY, last_batch INTEGER NOT NULL DEFAULT 0, skipped TEXT NOT NULL DEFAULT '[]',
  model TEXT, digest TEXT, error TEXT, gen INTEGER NOT NULL DEFAULT 0);
-- memory facts copied in from another computer: under which name here, from whom, and the hash they had when copied
CREATE TABLE IF NOT EXISTS mem_files(project TEXT, name TEXT, source TEXT NOT NULL, src_name TEXT NOT NULL, hash TEXT NOT NULL,
  PRIMARY KEY(project, name));
"""
# a table with a `conv` column and the parameter :p (NULL = every project); a subagent belongs to its parent's project
IN_PROJECT = """(:p IS NULL
  OR conv IN (SELECT conv FROM conv_projects WHERE project = :p)
  OR conv IN (SELECT c.conv FROM convs c JOIN conv_projects p ON p.conv = c.parent WHERE p.project = :p))"""
# the mod's own tool calls are no memory; rows of them from before transcripts.py stopped emitting them are left out here
_SEARCH = """
SELECT conv, pos, ts, kind, snippet(chunks, 0, '«', '»', '…', 24),
       (SELECT parent FROM convs WHERE convs.conv = chunks.conv), rowid
FROM chunks WHERE chunks MATCH :q AND NOT (kind = 'tool' AND text GLOB 'mcp__cacheguard__*') AND """ + IN_PROJECT + """
ORDER BY bm25(chunks) * CASE WHEN kind IN ('sub', 'tool') THEN 0.5 ELSE 1.0 END LIMIT :n
"""


def meta(conn, key):
    return (conn.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone() or (None,))[0]


def cls_of(kind):
    """The class of a kind in a record key: a report is a sub that turned out to be the last, and the last can change later."""
    return 'sub' if kind == 'report' else kind


def text_hash(text):
    return hashlib.sha1(text.encode('utf-8')).hexdigest()


def _migrate(conn, home):
    """Stage 2, once: a key for every record indexed before keys existed (no rebuild, the vectors stay), and project keys from the home
    folder on as ~, the form they are compared in now."""
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        if meta(conn, 'schema') == '2':  # another process got here first
            return
        taken = {(c, k, h): n for c, k, h, n in conn.execute('SELECT conv, cls, hash, max(n) FROM keys GROUP BY 1, 2, 3')}
        rows = conn.execute('SELECT rowid, conv, kind, text FROM chunks WHERE rowid NOT IN (SELECT id FROM keys) ORDER BY conv, pos, rowid').fetchall()
        new = []
        for rid, conv, kind, text in rows:
            k = (conv, cls_of(kind), text_hash(text))
            taken[k] = taken.get(k, -1) + 1
            new.append((rid, *k, taken[k]))
        conn.executemany('INSERT INTO keys(id, conv, cls, hash, n) VALUES(?, ?, ?, ?, ?)', new)
        pairs = conn.execute('SELECT conv, project FROM conv_projects').fetchall()
        conn.execute('DELETE FROM conv_projects')
        conn.executemany('INSERT OR IGNORE INTO conv_projects VALUES(?, ?)', {(c, p if p.startswith('~') else project_key(p, home)) for c, p in pairs})
        conn.execute("INSERT OR REPLACE INTO meta VALUES('schema', '2')")


def _gen_column(conn):
    """Stage 5, once: peers.gen on an index made before it."""
    def missing():
        return 'gen' not in [r[1] for r in conn.execute('PRAGMA table_info(peers)')]
    if missing():
        conn.execute('BEGIN IMMEDIATE')
        with conn:
            if missing():  # another process got here first
                conn.execute('ALTER TABLE peers ADD COLUMN gen INTEGER NOT NULL DEFAULT 0')


def db_errors():
    """What a locked or busy index raises: sqlcipher3 has classes of its own, and is loaded only once an encrypted index was opened."""
    lib = sys.modules.get('sqlcipher3')
    return (sqlite3.OperationalError, lib.OperationalError) if lib else (sqlite3.OperationalError,)


def _open(path):
    """No implicit transactions: _file opens its own with BEGIN IMMEDIATE. A sync holds the write lock while it writes a batch to the
    share, so the others wait up to 30 s for it, not the 5 s by default."""
    # a plain index never costs a look at the key: a broken settings file must not stop whoever has not turned encryption on. An empty
    # file is no index yet (sqlite makes the file on the first write), so it is new like no file at all
    if os.path.exists(path) and os.path.getsize(path) and not crypto.is_encrypted(path):
        return sqlite3.connect(path, isolation_level=None, timeout=30)
    try:
        key = crypto.load_key(crypto.settings_path(path))
    except (crypto.SettingsBroken, OSError):
        # a broken settings file, or one that cannot be opened, shows no key: a new index is then what it was before stage 5 (the job reports
        # the file); an encrypted one needs the key. A key that is set but unreadable (key_unreadable) is no reason for a plain index either:
        # it is not caught
        if crypto.is_encrypted(path):
            raise
        key = None
    if key:
        return crypto.open_db(path, key, page=crypto.INDEX_PAGE)
    if crypto.is_encrypted(path):  # never a new index over the one that is there
        raise ValueError(t('key_missing'))
    return sqlite3.connect(path, isolation_level=None, timeout=30)


def lock(db, name, wait):
    """One `name` run per index at a time, whichever chat or terminal started it: an exclusive transaction on a lock file beside the
    index, which the OS drops with the process. Hold the result while the run lasts; sqlite3.OperationalError after `wait` seconds."""
    conn = sqlite3.connect('%s.%s.lock' % (db, name), timeout=wait, isolation_level=None)
    try:
        conn.execute('BEGIN EXCLUSIVE')
    except BaseException:  # a try that failed keeps no file open: Windows will not delete the lock file of a connection that lives on in a traceback
        conn.close()
        raise
    return conn


def connect(path=DB_PATH, home=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = _open(path)
    conn.executescript(_SCHEMA)
    _gen_column(conn)
    if meta(conn, 'schema') != '2':
        _migrate(conn, home)
    return conn


def forget_key(conn, conv, cls, h, n, push=True):
    """Delete the record of this key with its vectors, if it is here, and keep the key from ever coming back. Inside the caller's transaction.
    With `push` the forget goes into our next batch, for the other computers."""
    for (rid,) in conn.execute('SELECT id FROM keys WHERE conv = ? AND cls = ? AND hash = ? AND n = ?', (conv, cls, h, n)).fetchall():
        conn.execute('DELETE FROM chunks WHERE rowid = ?', (rid,))
        conn.execute('DELETE FROM vecs WHERE chunk = ?', (rid,))
        conn.execute('DELETE FROM keys WHERE id = ?', (rid,))
    conn.execute('INSERT OR IGNORE INTO forgotten VALUES(?, ?, ?, ?)', (conv, cls, h, n))
    if push:
        conn.execute('INSERT INTO outbox(line) VALUES(?)', (json.dumps({'type': 'forget', 'key': [conv, cls, h, n]}, ensure_ascii=False),))


def forget(conn, conv, pos):
    """The one way to delete from the index (a secret the filter missed): every record at `pos` of `conv`, here and, through our next
    batch, on the other computers. The number of records forgotten."""
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        keys = conn.execute('SELECT k.cls, k.hash, k.n FROM keys k JOIN chunks c ON c.rowid = k.id WHERE k.conv = ? AND c.pos = ?', (conv, pos)).fetchall()
        for k in keys:
            forget_key(conn, conv, *k)
    return len(keys)


def _records(conn, conv, recs, whole, replace):
    """Insert the records of one read of a transcript that the index lacks, by key; rows added or replaced. Inside the caller's transaction.
    `whole`: the read starts at the beginning of the file, so the occurrences count from 0 and every record already here is matched, not
    added again. `replace`: the same file read again (rebuild); a record that has no key here but stands at the (pos, class) of a row this read
    did not match is that row processed anew (a fix of the secret filter or the parser): its text is replaced in place, its vectors go,
    and the old key is forgotten here and on the other computers. Nothing is ever deleted otherwise."""
    have, top = {}, {}
    for rid, c, h, n, pos in conn.execute('SELECT k.id, k.cls, k.hash, k.n, c.pos FROM keys k JOIN chunks c ON c.rowid = k.id WHERE k.conv = ?', (conv,)):
        have[(c, h, n)] = (rid, pos)
        top[(c, h)] = max(top.get((c, h), 0), n + 1)
    gone = {tuple(k) for k in conn.execute('SELECT cls, hash, n FROM forgotten WHERE conv = ?', (conv,))}
    for c, h, n in gone:
        top[(c, h)] = max(top.get((c, h), 0), n + 1)
    # the last sub text of a subagent is its report; a newer one demotes the previous report
    last = max((i for i, r in enumerate(recs) if r['kind'] == 'sub'), default=None)
    seen, plan = {}, []
    for i, r in enumerate(recs):
        kind = 'report' if i == last else r['kind']
        text = redact(r['text'])
        c, h = cls_of(kind), text_hash(text)
        k = seen.get((c, h), 0)
        seen[(c, h)] = k + 1
        plan.append((r, kind, text, (c, h, k if whole else top.get((c, h), 0) + k)))
    orphans = {}  # (pos, class) -> rows of this conv no record of this read is, oldest first
    if replace:
        matched = {key for *_, key in plan}
        for key, (rid, pos) in sorted(have.items(), key=lambda kv: kv[1][0]):
            if key not in matched:
                orphans.setdefault((pos, key[0]), []).append((rid, key))
    added, report = 0, None
    for i, (r, kind, text, key) in enumerate(plan):
        if key in gone:
            continue
        if key in have:
            rid = have[key][0]
        elif orphans.get((r['pos'], key[0])):
            rid, old = orphans[(r['pos'], key[0])].pop(0)
            forget_key(conn, conv, *old)  # the old key is in the batches already, maybe with the very secret the filter now hides
            conn.execute('INSERT INTO chunks(rowid, text, conv, pos, ts, kind) VALUES(?, ?, ?, ?, ?, ?)', (rid, text, conv, r['pos'], r['ts'], kind))
            conn.execute('INSERT INTO keys(id, conv, cls, hash, n) VALUES(?, ?, ?, ?, ?)', (rid, conv, *key))
            added += 1
        else:
            rid = conn.execute('INSERT INTO chunks(text, conv, pos, ts, kind) VALUES(?, ?, ?, ?, ?)', (text, conv, r['pos'], r['ts'], kind)).lastrowid
            conn.execute('INSERT INTO keys(id, conv, cls, hash, n) VALUES(?, ?, ?, ?, ?)', (rid, conv, *key))
            added += 1
        if i == last:
            report = rid
    if report is not None:
        set_report(conn, conv, report)
    return added


def set_report(conn, conv, rid):
    """`rid` is the report of subagent `conv`; any other report of it becomes a sub."""
    for (other,) in conn.execute("SELECT k.id FROM keys k JOIN chunks c ON c.rowid = k.id WHERE k.conv = ? AND k.cls = 'sub' AND c.kind = 'report'", (conv,)).fetchall():
        if other != rid:
            conn.execute("UPDATE chunks SET kind = 'sub' WHERE rowid = ?", (other,))
    conn.execute("UPDATE chunks SET kind = 'report' WHERE rowid = ? AND kind != 'report'", (rid,))


def _file(conn, path, conv, parent, rebuild, home):
    """Index the new lines of one transcript in one transaction. Rows added, or None if there is nothing to read.
    Text that once got into the index never leaves it this way: a shorter (rewritten) file and a rebuild are read again from the start
    and only the records the index lacks are added."""
    # an unchanged file is told without the write lock: a search must not wait on every transcript while a sync or an embed writes
    old = conn.execute('SELECT size FROM files WHERE path = ?', (path,)).fetchone()
    if old and not rebuild and os.path.getsize(path) == old[0]:
        return None
    conn.execute('BEGIN IMMEDIATE')  # the write lock comes before the offset is read, so overlapping updates cannot both insert the same lines
    try:
        size = os.path.getsize(path)
        old = conn.execute('SELECT size, offset FROM files WHERE path = ?', (path,)).fetchone()
        if old and size == old[0] and not rebuild:
            conn.rollback()
            return None
        # shortcut: only a shorter file counts as rewritten; same-size or longer rewrites are not noticed
        whole = rebuild or not old or size < old[0]
        recs, off = read_records(path, 0 if whole else old[1])
        added = _records(conn, conv, recs, whole, rebuild and bool(old) and size >= old[0])
        conn.executemany('INSERT OR IGNORE INTO conv_projects VALUES(?, ?)', {(conv, project_key(r['cwd'], home)) for r in recs if r['cwd']})
        ts = [r['ts'] for r in recs if r['ts']]
        if recs:
            conn.execute('INSERT INTO convs VALUES(?, ?, ?, ?) ON CONFLICT(conv) DO UPDATE SET '
                         'first_ts = min(first_ts, excluded.first_ts), last_ts = max(last_ts, excluded.last_ts)',
                         (conv, parent, min(ts, default=''), max(ts, default='')))
        conn.execute('INSERT OR REPLACE INTO files VALUES(?, ?, ?)', (path, size, off))
        conn.commit()
        return added
    except BaseException:  # no half-written file is ever left in an open transaction
        conn.rollback()
        raise


def _error(path, e):
    return {'path': path, 'error': '%s: %s' % (type(e).__name__, e)}


def update(conn, projects, rebuild=False, home=None):
    """Index what is new in the transcripts under `projects`; with `rebuild`, re-read every file found there from the start.
    Project keys are made against `home` (this computer's home folder by default).
    A missing or unreadable `projects` raises. Files that could not be read are listed in the result's 'errors' and retried next time."""
    t0, files, rows, errors = time.perf_counter(), 0, 0, []
    top = lp(projects)

    def walk_error(e):
        if e.filename == top:
            raise e
        errors.append(_error(e.filename, e))

    for dp, _, names in os.walk(top, onerror=walk_error):
        rel = dp[len(top):].split(os.sep)
        # a subagent file lies under <session id>\subagents\ (nested workflow agents deeper), the session id is its parent
        parent = rel[rel.index('subagents') - 1] if 'subagents' in rel else ''
        for n in names:
            if n.endswith('.jsonl'):
                path = os.path.join(dp, n)
                try:
                    added = _file(conn, path, n[:-len('.jsonl')], parent, rebuild, home)
                except FileNotFoundError:  # vanished between the walk and the open
                    continue
                except Exception as e:
                    errors.append(_error(path, e))
                    continue
                if added is not None:
                    files, rows = files + 1, rows + added
    conn.execute("INSERT OR REPLACE INTO meta VALUES('last_update', ?)", (datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),))
    return {'files': files, 'rows': rows, 'convs': conn.execute('SELECT count(*) FROM convs').fetchone()[0],
            'seconds': time.perf_counter() - t0, 'errors': errors}


def status(conn):
    from vectors import vector_status  # vectors imports memdb
    db = conn.execute('PRAGMA database_list').fetchone()[2]
    last = conn.execute("SELECT value FROM meta WHERE key = 'last_update'").fetchone()
    return {'db': db, 'convs': conn.execute('SELECT count(*) FROM convs').fetchone()[0],
            'rows': conn.execute('SELECT count(*) FROM chunks').fetchone()[0],
            'last_update': last[0] if last else None, 'size_mb': round(os.path.getsize(db) / 2 ** 20, 2),
            'vectors': vector_status(conn)}


def word_hits(conn, query, project=None, n=50):
    """At most `n` hits containing all the words of the query, else any of them; best first, repeats still in."""
    terms = ['"%s"' % w for w in re.findall(r'\w+', query)]
    rows = []
    for q in ((' '.join(terms), ' OR '.join(terms)) if terms else ()):
        rows = conn.execute(_SEARCH, {'q': q, 'p': project, 'n': n}).fetchall()
        if rows:
            break
    return [{'conv': conv, 'parent': parent or '', 'ts': ts, 'kind': kind, 'pos': pos, 'snippet': snippet, 'id': rowid}
            for conv, pos, ts, kind, snippet, parent, rowid in rows]


def search(conn, query, project=None, limit=8, budget=7000):
    """Hits containing all the words of the query, else any of them; best first, cut at `limit` hits or `budget` snippet characters."""
    return cut(word_hits(conn, query, project, max(limit, 50)), budget, limit)  # the rows beyond `limit` stand in for the repeats cut drops


def cut(hits, budget, limit=None):
    """The leading hits that fit in `budget` snippet characters and `limit` hits. A hit that repeats an earlier one is dropped and counts
    for nothing: Claude Code copies the history of a resumed session into the new transcript, so one message sits under two convs."""
    out, seen, used = [], set(), 0
    for h in hits:
        key = (h['kind'], h['ts'], h['snippet'])
        if key in seen:
            continue
        used += len(h['snippet'])
        if used > budget or len(out) == limit:
            break
        seen.add(key)
        out.append(h)
    return out


def expand(conn, conv, pos, radius=6, budget=7000):
    """The records around `pos` (±radius), in order; when over budget the ones farthest from `pos` are dropped first."""
    # shortcut: expand scans the FTS table by conv; add a plain msgs table with an index if the index grows past ~1M rows
    rows = conn.execute('SELECT pos, ts, kind, substr(text, 1, 1500) FROM chunks WHERE conv = ? ORDER BY pos, rowid', (conv,)).fetchall()
    hit = [i for i, r in enumerate(rows) if r[0] == pos]
    if not hit:
        return []
    lo, hi = hit[0], hit[-1]
    keep, used = [], 0
    for i in sorted(range(max(0, lo - radius), min(len(rows), hi + radius + 1)), key=lambda i: max(lo - i, i - hi, 0)):
        used += len(rows[i][3])
        if used > budget:
            break
        keep.append(i)
    return [dict(zip(('pos', 'ts', 'kind', 'text'), rows[i])) for i in sorted(keep)]


def owner_count(conn, since, until=None):
    return conn.execute("SELECT count(*) FROM chunks WHERE kind = 'owner' AND ts >= ? AND (? IS NULL OR ts < ?)",
                        (since, until, until)).fetchone()[0]
