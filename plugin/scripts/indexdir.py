"""Where the index is on every run of cg.py: in the folder `indexDir` of the home settings names, else in the home folder itself. While that
folder has no index the mod works with the home folder and says so. It makes no folder and no index there (only the move command does).
When the folder has its index back, what the home folder gathered meanwhile is merged into it and deleted (`merge_back`)."""
import collections
import contextlib
import os
import shutil
import sqlite3
import crypto
import encrypt
import memdb
from texts import t
from vectors import DIMS

MIN_FREE = 2 * 2 ** 30  # a new index in the home folder (C:) only while that much is free there
MARK = 'index.db.fallback'  # in the home folder: the index beside it stands in for indexDir, and the file holds that folder
# db: the index to open; configured: indexDir, None when it is not set or is the home folder; fallback: the home index stands in for it;
# merge_from: the home index to merge into db, None when there is none
Where = collections.namedtuple('Where', 'db configured fallback merge_from')
LOCKS = ('maintain', 'embed', 'sync')  # the long runs on an index (memdb.lock): a merge waits for none, it takes all of both indexes or does nothing
# the keys of the temporary index (tmp) that the index (main) has neither as a record nor forgotten, and the forgets it lacks
_NEW = 'SELECT conv, cls, hash, n FROM tmp.keys EXCEPT SELECT conv, cls, hash, n FROM main.keys EXCEPT SELECT conv, cls, hash, n FROM main.forgotten'
_UNFORGOTTEN = 'SELECT conv, cls, hash, n FROM tmp.forgotten EXCEPT SELECT conv, cls, hash, n FROM main.forgotten'


def resolve(home=None, free=None):
    """The `Where` of this run, from the settings file in `home` (crypto.HOME_DIR). `free(path)` is the free bytes of a disk."""
    home = home or crypto.HOME_DIR
    free = free or (lambda path: shutil.disk_usage(path).free)
    db, mark, settings = (os.path.join(home, name) for name in ('index.db', MARK, 'settings.json'))
    try:
        d = crypto._read(settings).get('indexDir')
    except (crypto.SettingsBroken, OSError) as e:  # nothing says where the index is: the home one is all there is to go by, and no guess is made without it
        if os.path.isfile(db):
            return Where(db, None, False, None)
        raise ValueError(t('index_settings_unreadable', path=settings, e=e)) from None
    if isinstance(d, str) and not d.strip():  # a value emptied by hand means no folder, as none does
        d = None
    usable = isinstance(d, str) and os.path.isabs(d)
    if d is None or (usable and crypto.norm(d) == crypto.norm(home)):
        return Where(db, None, False, None)
    if usable and os.path.isfile(os.path.join(d, 'index.db')):
        return Where(os.path.join(d, 'index.db'), d, False, db if os.path.isfile(mark) and os.path.isfile(db) else None)
    d = str(d)  # a value that is no path is shown as it is
    if not os.path.isfile(db):  # an index that is there is worked with whatever the space: it grows no faster than before
        room = free(home)
        if room < MIN_FREE:
            raise ValueError(t('index_fallback_no_space', dir=d, home=home, free=round(room / 2 ** 30, 1)))
    if not os.path.isfile(mark):
        with open(mark, 'w', encoding='utf-8') as f:
            f.write(d)
    return Where(db, d, True, None)


def notice(where):
    """What the owner is told when the index is not where indexDir says; None when it is."""
    if where and where.fallback:
        return t('index_fallback', dir=where.configured, home=os.path.dirname(where.db))


def status(conn, where):
    """The `index_dir` of `status`: where the index is meant to be and the notes the index keeps. `where` is None for a run with --db."""
    old, failed = memdb.meta(conn, 'old_copy'), memdb.meta(conn, 'merge_error')
    # the failed merge names both folders: the one the index is in and the one of the temporary index, which a run with --db does not know
    merge = failed and where and where.configured and t('index_merge_failed', dir=where.configured, home=os.path.dirname(where.merge_from or where.db), e=failed)
    notes = [n for n in (notice(where), old and t('index_old_copy', dir=old), merge) if n]
    return {'configured': where and where.configured, 'fallback': bool(where and where.fallback), 'notes': notes}


def _connect(path, key):
    """The index at `path`: an open one as it is, an encrypted one with `key`."""
    if not crypto.is_encrypted(path):
        return sqlite3.connect(path, isolation_level=None, timeout=30)
    if not key:
        raise ValueError(t('key_missing'))
    return encrypt._open(path, key)


def absorb(db, src, key):
    """Merge the temporary index `src` into the index `db` in one transaction: its records, their vectors (of the same model), lessons, forgets
    and read marks that `db` lacks come over. What either side forgot is never brought back. {'records', 'vectors', 'forgets'}: what came over.
    A merge after which `db` does not hold everything of `src` (but what was forgotten) is undone (ValueError), as is any failure. Clears
    meta.merge_error of `db`: this try is the one that counts."""
    enc = crypto.is_encrypted(src)
    if enc and not crypto.is_encrypted(db):
        raise ValueError(t('index_merge_mixed'))
    conn = _connect(db, key)
    try:
        if enc:  # SQLCipher reads the schema of a file at its ATTACH, so its page size is the default one by then: a pragma after the ATTACH is too late
            was = conn.execute('PRAGMA cipher_default_page_size').fetchone()[0]
            conn.execute('PRAGMA cipher_default_page_size = %d' % crypto.INDEX_PAGE)
        try:
            conn.execute('ATTACH DATABASE ? AS tmp KEY ?', (src, "x'%s'" % key.hex() if enc else ''))
        finally:
            if enc:
                conn.execute('PRAGMA cipher_default_page_size = ' + was)
        conn.execute('BEGIN IMMEDIATE')
        with conn:
            return _absorb(conn)
    finally:
        conn.close()


def _absorb(conn):
    """The steps of `absorb` on a connection to `db` with `src` attached as tmp, inside the caller's transaction."""
    def take(tid, rid, conv):
        """The vectors of record `tid` of tmp as the ones of `rid`; False when it has none of the right length."""
        parts = conn.execute('SELECT part, vec FROM tmp.vecs WHERE chunk = ? AND length(vec) = ? ORDER BY part', (tid, 4 * DIMS)).fetchall()
        if parts:
            conn.execute('DELETE FROM main.vecs WHERE chunk = ?', (rid,))  # the empty one of a record that failed to embed here
            conn.executemany('INSERT INTO main.vecs VALUES(?, ?, ?, ?)', [(rid, part, conv, vec) for part, vec in parts])
        return bool(parts)

    forgets = records = vectors = 0
    for k in conn.execute(_UNFORGOTTEN).fetchall():
        memdb.forget_key(conn, *k, push=False)  # the line for the other computers comes with the outbox, below
        forgets += 1
    conn.execute('INSERT INTO main.outbox(line) SELECT line FROM tmp.outbox WHERE line NOT IN (SELECT line FROM main.outbox) ORDER BY seq')
    have, theirs = (conn.execute("SELECT value FROM %s.meta WHERE key = 'embed_model'" % name).fetchone() for name in ('main', 'tmp'))
    have, theirs = have and have[0], theirs and theirs[0]
    same = bool(theirs) and have in (None, theirs)  # vectors of another model cannot be compared; an index with none takes the model it gets, as sync does
    for tid, conv, cls, h, n, origin, pushed, vec_pushed, pos, ts, kind, text in conn.execute(
            'SELECT k.id, k.conv, k.cls, k.hash, k.n, k.origin, k.pushed, k.vec_pushed, c.pos, c.ts, c.kind, c.text FROM tmp.keys k '
            'JOIN tmp.chunks c ON c.rowid = k.id WHERE (k.conv, k.cls, k.hash, k.n) IN (%s) ORDER BY k.id' % _NEW).fetchall():
        rid = conn.execute('INSERT INTO main.chunks(text, conv, pos, ts, kind) VALUES(?, ?, ?, ?, ?)', (text, conv, pos, ts, kind)).lastrowid
        conn.execute('INSERT INTO main.keys(id, conv, cls, hash, n, origin, pushed, vec_pushed) VALUES(?, ?, ?, ?, ?, ?, ?, ?)',
                     (rid, conv, cls, h, n, origin, pushed, vec_pushed))
        if kind == 'report':
            memdb.set_report(conn, conv, rid)
        records += 1
        vectors += same and take(tid, rid, conv)
    if same:  # a record both have, and only the temporary index has its vector
        for mid, tid, conv, vec_pushed in conn.execute(
                'SELECT m.id, k.id, m.conv, k.vec_pushed FROM tmp.keys k JOIN main.keys m ON m.conv = k.conv AND m.cls = k.cls AND m.hash = k.hash AND m.n = k.n '
                'WHERE NOT EXISTS (SELECT 1 FROM main.vecs v WHERE v.chunk = m.id AND length(v.vec) = :w) '
                'AND EXISTS (SELECT 1 FROM tmp.vecs v WHERE v.chunk = k.id AND length(v.vec) = :w)', {'w': 4 * DIMS}).fetchall():
            take(tid, mid, conv)
            conn.execute('UPDATE main.keys SET vec_pushed = ? WHERE id = ?', (vec_pushed, mid))
            vectors += 1
    if vectors and not have:
        conn.execute("INSERT OR REPLACE INTO main.meta VALUES('embed_model', ?)", (theirs,))
    conn.execute('INSERT INTO main.convs(conv, parent, first_ts, last_ts) SELECT conv, parent, first_ts, last_ts FROM tmp.convs WHERE true '
                 'ON CONFLICT(conv) DO UPDATE SET first_ts = min(first_ts, excluded.first_ts), last_ts = max(last_ts, excluded.last_ts)')
    conn.execute('INSERT OR IGNORE INTO main.conv_projects(conv, project) SELECT conv, project FROM tmp.conv_projects')
    conn.execute('INSERT OR IGNORE INTO main.mem_files(project, name, source, src_name, hash) SELECT project, name, source, src_name, hash FROM tmp.mem_files')
    # how far each transcript was read: without it the next update reads again what came over, and adds it as new records
    conn.execute('INSERT INTO main.files(path, size, offset) SELECT path, size, offset FROM tmp.files WHERE true '
                 'ON CONFLICT(path) DO UPDATE SET size = excluded.size, offset = excluded.offset WHERE excluded.offset > files.offset')
    # the batches the temporary index put out as this computer are the next ones of this one: the farther one (a new generation counts first) is right
    conn.execute('INSERT INTO main.peers(machine, last_batch, skipped, model, digest, error, gen) SELECT machine, last_batch, skipped, model, digest, error, gen '
                 'FROM tmp.peers WHERE true ON CONFLICT(machine) DO UPDATE SET last_batch = excluded.last_batch, skipped = excluded.skipped, '
                 'model = excluded.model, digest = excluded.digest, error = excluded.error, gen = excluded.gen '
                 'WHERE (excluded.gen, excluded.last_batch) > (peers.gen, peers.last_batch)')
    if any(conn.execute('SELECT count(*) FROM (%s)' % q).fetchone()[0] for q in (_NEW, _UNFORGOTTEN)):
        raise ValueError(t('index_merge_differs'))
    conn.execute("DELETE FROM main.meta WHERE key = 'merge_error'")
    return {'records': records, 'vectors': vectors, 'forgets': forgets}


def _handoffs(src, db):
    """The handoff files written beside the temporary index move to the folder of `db`, those it does not have yet."""
    old, new = (os.path.join(os.path.dirname(p), 'handoffs') for p in (src, db))
    if os.path.isdir(old):
        os.makedirs(new, exist_ok=True)
        for name in os.listdir(old):
            if not os.path.exists(os.path.join(new, name)):
                shutil.move(os.path.join(old, name), new)


def merge_back(where, key):
    """Merge the temporary index of the standard folder (`where.merge_from`) into the index in its folder (`where.db`), then delete it, its
    mark and its journal, after moving its handoff files over. The text for `index_notice`; None when there is nothing to tell: a background
    job, a sync or an embed holds either index (nothing was touched), or the temporary index was written to meanwhile (by a run that began
    before the folder was back: what came over is in, the next run takes the rest and deletes it). A failure is told and kept in
    meta.merge_error of `db`; both indexes stay as they were and the next run tries again."""
    db, src = where.db, where.merge_from
    fields = {'dir': where.configured, 'home': os.path.dirname(src)}
    held = []
    try:
        try:
            for path in (db, src):
                for name in LOCKS:
                    held.append(memdb.lock(path, name, 0))
        except sqlite3.OperationalError as e:
            if 'locked' in str(e):
                return None
            raise
        before = encrypt._stamp(src)
        done = absorb(db, src, key)
        if encrypt._stamp(src) != before:
            return None
        _handoffs(src, db)
        encrypt._remove(src)
        with contextlib.suppress(OSError):  # a mark left beside no index does nothing, and no failure may stay on record once the index is gone
            os.remove(os.path.join(fields['home'], MARK))
        return t('index_merged', n=done['records'], **fields)
    except Exception as e:
        why = str(e) or type(e).__name__
        with contextlib.suppress(Exception):  # the index folder may be what failed
            with contextlib.closing(_connect(db, key)) as conn:
                conn.execute("INSERT OR REPLACE INTO meta VALUES('merge_error', ?)", (why,))
        return t('index_merge_failed', e=why, **fields)
    finally:
        for lock in held:
            lock.close()
