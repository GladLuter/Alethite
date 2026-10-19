import datetime
import os
import re
import sqlite3
import time
from redact import redact
from transcripts import lp, project_key, read_records

DB_PATH = os.path.join(os.path.expanduser('~'), '.claude', 'cacheguard', 'index.db')
_SCHEMA = """
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INTEGER, offset INTEGER);
CREATE TABLE IF NOT EXISTS convs(conv TEXT PRIMARY KEY, parent TEXT, first_ts TEXT, last_ts TEXT);
CREATE TABLE IF NOT EXISTS conv_projects(conv TEXT, project TEXT, PRIMARY KEY(conv, project));
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
  text, conv UNINDEXED, pos UNINDEXED, ts UNINDEXED, kind UNINDEXED,
  tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS vecs(chunk INTEGER, part INTEGER, conv TEXT, vec BLOB, PRIMARY KEY(chunk, part));
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


def connect(path=DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # no implicit transactions: _file opens its own with BEGIN IMMEDIATE
    conn.executescript(_SCHEMA)
    return conn


def _file(conn, path, conv, parent, rebuild):
    """Index the new lines of one transcript in one transaction. Rows added, or None if there is nothing to read."""
    conn.execute('BEGIN IMMEDIATE')  # the write lock comes before the offset is read, so overlapping updates cannot both insert the same lines
    try:
        size = os.path.getsize(path)
        old = conn.execute('SELECT size, offset FROM files WHERE path = ?', (path,)).fetchone()
        if old and size == old[0] and not rebuild:
            conn.rollback()
            return None
        # rebuild re-reads the files found on disk; rows of transcripts that are gone stay
        if rebuild or (old and size < old[0]):  # shortcut: only a shorter file counts as rewritten; same-size or longer rewrites are not noticed
            for t in ('chunks', 'vecs', 'conv_projects', 'convs'):
                conn.execute('DELETE FROM %s WHERE conv = ?' % t, (conv,))
            old = None
        recs, off = read_records(path, old[1] if old else 0)
        # the last sub text of a subagent is its report; a newer one demotes the previous report
        last = max((i for i, r in enumerate(recs) if r['kind'] == 'sub'), default=None)
        if last is not None and old:
            conn.execute("UPDATE chunks SET kind = 'sub' WHERE conv = ? AND kind = 'report'", (conv,))
        conn.executemany('INSERT INTO chunks(text, conv, pos, ts, kind) VALUES(?, ?, ?, ?, ?)',
                         [(redact(r['text']), conv, r['pos'], r['ts'], 'report' if i == last else r['kind']) for i, r in enumerate(recs)])
        conn.executemany('INSERT OR IGNORE INTO conv_projects VALUES(?, ?)', {(conv, project_key(r['cwd'])) for r in recs if r['cwd']})
        ts = [r['ts'] for r in recs if r['ts']]
        if recs:
            conn.execute('INSERT INTO convs VALUES(?, ?, ?, ?) ON CONFLICT(conv) DO UPDATE SET '
                         'first_ts = min(first_ts, excluded.first_ts), last_ts = max(last_ts, excluded.last_ts)',
                         (conv, parent, min(ts, default=''), max(ts, default='')))
        conn.execute('INSERT OR REPLACE INTO files VALUES(?, ?, ?)', (path, size, off))
        conn.commit()
        return len(recs)
    except BaseException:  # no half-written file is ever left in an open transaction
        conn.rollback()
        raise


def _error(path, e):
    return {'path': path, 'error': '%s: %s' % (type(e).__name__, e)}


def update(conn, projects, rebuild=False):
    """Index what is new in the transcripts under `projects`; with `rebuild`, re-read every file found there from the start.
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
                    added = _file(conn, path, n[:-len('.jsonl')], parent, rebuild)
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
