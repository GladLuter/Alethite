"""Where the index is on every run of cg.py: in the folder `indexDir` of the home settings names, else in the home folder itself. While that
folder has no index the mod works with the home folder and says so. It makes no folder and no index there (only the move command does:
`move`, which copies the index to a folder of the owner's choice, checks the copy, switches the setting and asks before the old copy goes).
When the folder has its index back, what the home folder gathered meanwhile is merged into it and deleted (`merge_back`)."""
import collections
import contextlib
import ctypes
import glob
import json
import os
import shutil
import sqlite3
import crypto
import memdb
from texts import t
from vectors import DIMS

MIN_FREE = 2 * 2 ** 30  # a new index in the home folder (C:) only while that much is free there
MARK = 'index.db.fallback'  # in the home folder: the index beside it stands in for indexDir, and the file holds that folder
FORGETS = 'forgotten.json'  # in the home folder: the keys (no text) of every forget an index in another folder holds, for a stand-in index to apply
# db: the index to open; configured: indexDir, None when it is not set or is the home folder; fallback: the home index stands in for it;
# merge_from: the home index to merge into db, None when there is none; settings_error: why the settings could not be read, the home index
# is worked with (and the forgets applied) meanwhile, None when they were read
Where = collections.namedtuple('Where', 'db configured fallback merge_from settings_error', defaults=(None,))
LOCKS = ('maintain', 'embed', 'sync')  # the long runs on an index (memdb.lock): a merge waits for none, it takes all of both indexes or does nothing
DRIVE_FIXED = 3  # GetDriveTypeW: a disk inside this computer, not a removable or a network one
SPARE = 200 * 2 ** 20  # what a move leaves free on the target disk besides the files it copies
MB = 2 ** 20
LOCK_BYTE = 0x40000000  # where SQLite's lock-byte page starts, in a file over 1 GB: it never stores data there
CHUNK = 2 ** 20
CLOUD = ('OneDrive', 'OneDriveCommercial', 'OneDriveConsumer')  # the folders a cloud service syncs: no index there
# the keys of the temporary index (tmp) that the index (main) has neither as a record nor forgotten, and the forgets it lacks
_NEW = 'SELECT conv, cls, hash, n FROM tmp.keys EXCEPT SELECT conv, cls, hash, n FROM main.keys EXCEPT SELECT conv, cls, hash, n FROM main.forgotten'
_UNFORGOTTEN = 'SELECT conv, cls, hash, n FROM tmp.forgotten EXCEPT SELECT conv, cls, hash, n FROM main.forgotten'


def _free(path):
    return shutil.disk_usage(path).free


def _drive_type(root):
    return ctypes.windll.kernel32.GetDriveTypeW(root)


def resolve(home=None, free=None):
    """The `Where` of this run, from the settings file in `home` (crypto.HOME_DIR). `free(path)` is the free bytes of a disk."""
    home = home or crypto.HOME_DIR
    free = free or _free
    db, mark, settings = (os.path.join(home, name) for name in ('index.db', MARK, 'settings.json'))
    try:
        d = crypto._read(settings).get('indexDir')
    except (crypto.SettingsBroken, OSError) as e:  # nothing says where the index is: the home one is all there is to go by, and no guess is made without it
        if os.path.isfile(db):
            return Where(db, None, False, None, str(e))
        raise ValueError(t('index_settings_unreadable', path=settings, e=e)) from None
    if isinstance(d, str) and not d.strip():  # a value emptied by hand means no folder, as none does
        d = None
    usable = isinstance(d, str) and os.path.isabs(d)
    d = crypto.moved(d) if usable else d  # written under the old name of the mod folder
    if d is None or (usable and crypto.norm(d) == crypto.norm(home)):
        return Where(db, None, False, None)
    if usable and os.path.isfile(os.path.join(d, 'index.db')):
        return Where(os.path.join(d, 'index.db'), d, False, db if os.path.isfile(mark) and os.path.isfile(db) else None)
    d = str(d)  # a value that is no path is shown as it is
    if not os.path.isfile(db):  # an index that is there is worked with whatever the space: it grows no faster than before
        room = free(home)
        if room < MIN_FREE:
            raise ValueError(t('index_fallback_no_space', dir=d, home=home, free=round(room / 2 ** 30, 1), min=MIN_FREE // 2 ** 30))
    if not os.path.isfile(mark):
        with open(mark, 'w', encoding='utf-8') as f:
            f.write(d)
    return Where(db, d, True, None)


def remark(where):
    """A run that stands in for the folder gives the home index its mark again if a merge by another run deleted both between its resolve and its
    open: that index is new then, and without the mark nobody would merge it."""
    mark = os.path.join(os.path.dirname(where.db), MARK)
    if where.fallback and os.path.isfile(where.db) and not os.path.isfile(mark):
        with open(mark, 'w', encoding='utf-8') as f:
            f.write(where.configured)


def notice(where):
    """What the owner is told when the index is not where indexDir says; None when it is."""
    if where and where.fallback:
        return t('index_fallback', dir=where.configured, home=os.path.dirname(where.db))
    if where and where.settings_error:
        home = os.path.dirname(where.db)
        return t('index_settings_fallback', path=os.path.join(home, 'settings.json'), e=where.settings_error, home=home)


def status(conn, where):
    """The `index_dir` of `status`: where the index is meant to be and the notes the index keeps. `where` is None for a run with --db."""
    old, failed, done = (memdb.meta(conn, k) for k in ('old_copy', 'merge_error', 'merge_done'))
    # the failed merge names both folders: the one the index is in and the one of the temporary index, which a run with --db does not know.
    # It is told while that index is there to merge: a note that nothing would take away is none
    merge = failed and where and where.merge_from and t('index_merge_failed', dir=where.configured, home=os.path.dirname(where.merge_from), e=failed)
    done = done and json.loads(done)
    merged = done and t('index_merged', **dict(done, home=crypto.moved(done['home'])))  # the last merge, in the language of this run: merge_back stored its figures
    notes = [n for n in (notice(where), *(t('index_old_copy', dir=d) for d in _old_copies(old)), merge, merged) if n]
    return {'configured': where and where.configured, 'fallback': bool(where and where.fallback), 'notes': notes}


def _connect(path, key):
    """The index at `path`: an open one as it is, an encrypted one with `key`."""
    import encrypt
    if not crypto.is_encrypted(path):
        return crypto.plain_db(path)
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
    # a record both have, which the temporary index put into its batches: it is not sent again from here
    conn.execute('UPDATE main.keys SET pushed = 1 WHERE pushed = 0 AND EXISTS (SELECT 1 FROM tmp.keys k WHERE k.pushed = 1 AND k.conv = keys.conv '
                 'AND k.cls = keys.cls AND k.hash = keys.hash AND k.n = keys.n)')
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
    import encrypt
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
        with contextlib.closing(_connect(db, key)) as conn:  # a move may have left src as the old copy of db: its note goes with it
            old = _old_copies(memdb.meta(conn, 'old_copy'))
            _set_old(conn, [d for d in old if crypto.norm(d) != crypto.norm(fields['home'])])
            # for /cg-health: a merge done by the background job is told nowhere else
            conn.execute("INSERT OR REPLACE INTO meta VALUES('merge_done', ?)", (json.dumps(dict(fields, n=done['records'])),))
        encrypt._remove(src)
        for lock in held:  # Windows will not delete a lock file that is open
            lock.close()
        for name in LOCKS:
            with contextlib.suppress(OSError):
                os.remove('%s.%s.lock' % (src, name))
        with contextlib.suppress(OSError):  # a mark left beside no index does nothing, and no failure may stay on record once the index is gone
            os.remove(os.path.join(fields['home'], MARK))
        return t('index_merged', n=done['records'], **fields)
    except Exception as e:
        why = str(e) or type(e).__name__
        with contextlib.suppress(Exception):  # the index folder may be what failed
            with contextlib.closing(_connect(db, key)) as conn:
                conn.execute("INSERT OR REPLACE INTO meta VALUES('merge_error', ?)", (why,))
                conn.execute("DELETE FROM meta WHERE key = 'merge_done'")  # the last result is the one told
        return t('index_merge_failed', e=why, **fields)
    finally:
        for lock in held:
            lock.close()


# ---- forgets that a stand-in index must not lose: kept in the home folder by the index in another folder, applied by the one that stands in for it

def _kept():
    """The keys in the home folder's file. A file that is missing or cannot be read holds none: it is written anew at the end of a run."""
    try:
        with open(os.path.join(crypto.HOME_DIR, FORGETS), encoding='utf-8') as f:
            return {tuple(k) for k in json.load(f) if isinstance(k, list) and len(k) == 4}
    except (OSError, ValueError, TypeError):
        return set()


def keep_forgets(db):
    """Adds the forget keys of the index `db` (in a folder other than the home one) to the home folder's file, after the run that made them.
    Keys only, never a text. Never an error: the next run writes what this one could not."""
    with contextlib.suppress(Exception):
        with contextlib.closing(memdb.connect(db)) as conn:
            have = set(conn.execute('SELECT conv, cls, hash, n FROM forgotten'))
        kept = _kept()
        if have - kept:  # shortcut: the whole file is rewritten, fine for the few forgets an owner makes, append lines past thousands
            crypto.atomic_write(os.path.join(crypto.HOME_DIR, FORGETS), json.dumps(list(kept | have)).encode('utf-8'))


def apply_forgets(db):
    """The index `db` that stands in for its folder forgets every key of the file, as an imported forget is forgotten: the record and its
    vectors go, the key stays in `forgotten`, no line goes to the outbox. Before it reads a transcript: an old copy lacks the forgets made in the
    folder after the move, a new index all of them."""
    keys = _kept()
    if keys:
        with contextlib.closing(memdb.connect(db)) as conn:
            todo = keys - set(conn.execute('SELECT conv, cls, hash, n FROM forgotten'))
            if todo:
                conn.execute('BEGIN IMMEDIATE')
                with conn:
                    for k in todo:
                        memdb.forget_key(conn, *k, push=False)


# ---- the move (/cg-index-dir)

def _same(a, b):
    """The file `b` is a whole copy of `a`: the same size and the same first page (every commit rewrites it)."""
    import encrypt
    return os.path.isfile(a) and os.path.isfile(b) and os.path.getsize(a) == os.path.getsize(b) and encrypt._stamp(a) == encrypt._stamp(b)


def _existing(path):
    """The nearest folder of `path` that is there: the disk a folder that is not made yet will lie on."""
    while not os.path.exists(path) and os.path.dirname(path) != path:
        path = os.path.dirname(path)
    return path


def _copyfile(path, copy, page, lock=LOCK_BYTE):
    """`path` copied to `copy` in chunks, the lock-byte page (`page` bytes from `lock`) written as zeros and not read. Our write lock is a
    byte-range lock there, which Windows enforces against a read through another handle: a plain copy of an index over 1 GB would fail."""
    with open(path, 'rb') as src, open(copy, 'wb') as dst:
        size, pos = os.fstat(src.fileno()).st_size, 0
        while pos < size:
            if lock <= pos < lock + page:
                end = min(lock + page, size)
                dst.write(bytes(end - pos))
                src.seek(end)
            else:
                end = min(pos + CHUNK, size, lock if pos < lock else size)  # a chunk stops where the page begins
                dst.write(src.read(end - pos))
            pos = end


def _copy(path, copy, key):
    """The index file `path` copied to `copy` under its own write lock (a chat cannot write meanwhile) and counted from a fresh open;
    the first page of `path` as it was then."""
    import encrypt
    with contextlib.closing(_connect(path, key)) as conn:
        conn.execute('BEGIN IMMEDIATE')
        want, stamp = encrypt._counts(conn), encrypt._stamp(path)
        _copyfile(path, copy, int(conn.execute('PRAGMA page_size').fetchone()[0]))  # SQLCipher answers with text
        conn.execute('COMMIT')
    with contextlib.closing(_connect(copy, key)) as dst:
        got = encrypt._counts(dst)
    if got != want:
        raise ValueError(t('encrypt_copy_differs', path=path))
    return stamp


def _handoffs_in(folder):
    """The handoff files of an index folder, `handoffs\\*.md` and nothing deeper: a move copies and deletes these and walks no tree, so a
    folder that lies inside `handoffs` (or the new index there) is never reached."""
    return [p for p in glob.glob(os.path.join(glob.escape(folder), 'handoffs', '*.md')) if os.path.isfile(p)]


def _efs_first(path):
    """A path of the target that is there already and plain goes under EFS before anything is written into it: a file would take the new
    bytes in clear, a folder would hold its new files so. What is made in an encrypted folder is born encrypted and needs this no more."""
    if os.path.exists(path) and not crypto.efs_on(path):
        crypto.efs_do(crypto.efs_encrypt, path)


def _copy_extras(cur, folder, efs):
    """The handoff files and job.xml beside the index go over as they are; with `efs` each folder and file they are written to is under EFS first."""
    def put(path, dest):
        if efs:
            _efs_first(dest)
        shutil.copyfile(path, dest)

    mine = _handoffs_in(cur)
    if mine:
        to = os.path.join(folder, 'handoffs')
        os.makedirs(to, exist_ok=True)
        if efs:
            _efs_first(to)
        for path in mine:
            put(path, os.path.join(to, os.path.basename(path)))
    job = os.path.join(cur, 'job.xml')
    if os.path.isfile(job):
        put(job, os.path.join(folder, 'job.xml'))


def _unmark():
    """The mark of a temporary index in the home folder goes: what lies there is a plain old copy now, or the permanent index."""
    with contextlib.suppress(FileNotFoundError):
        os.remove(os.path.join(crypto.HOME_DIR, MARK))


def _delete_old(old):
    """The files an index leaves in the folder `old` go: the index and the backup of stage 2 with their journals, the lock files, job.xml,
    the handoff files and the mark. Never the settings file, never a folder tree. Whether a file stayed because something holds it."""
    import encrypt
    db = os.path.join(old, 'index.db')
    names = [*encrypt._files(db), *('%s.%s.lock' % (db, name) for name in LOCKS), os.path.join(old, 'job.xml'), os.path.join(old, MARK), *_handoffs_in(old)]
    stayed = False
    for path in names:
        try:
            encrypt._remove(path)
        except OSError:
            stayed = True
    with contextlib.suppress(OSError):  # a folder that still holds something (the new index may lie in it) or is in use stays
        os.rmdir(os.path.join(old, 'handoffs'))
    return stayed


def _old_copies(value):
    """The folders meta.old_copy holds, one per line (a single folder is a value of one line, as it always was)."""
    return [crypto.moved(d) for d in (value or '').split('\n') if d]


def _set_old(conn, folders):
    if folders:
        conn.execute("INSERT OR REPLACE INTO meta VALUES('old_copy', ?)", ('\n'.join(dict.fromkeys(folders)),))
    else:
        conn.execute("DELETE FROM meta WHERE key = 'old_copy'")


def _drop_old(olds, db, ui, notes):
    """The question about each old copy in the folders `olds`, the index in use being `db`: Yes deletes it, No leaves a note (and its line in
    meta.old_copy of `db`, which /cg-health shows). That line goes when the last file does. A name that is no full path, or is the folder of
    `db` itself, is no old copy to delete: only its line goes."""
    done = set()
    for old in olds:
        if os.path.isabs(old) and crypto.norm(old) != crypto.norm(os.path.dirname(db)):
            if not ui.confirm(t('dlg_delete_old_index', dir=old), t('dlg_title_index_dir')):
                notes.append(t('index_old_kept', dir=old))
                continue
            if _delete_old(old):
                notes.append(t('index_old_busy', dir=old))
                continue
        done.add(old)
    with contextlib.closing(memdb.connect(db)) as conn:
        _set_old(conn, [d for d in _old_copies(memdb.meta(conn, 'old_copy')) if d not in done])


def refuse(where, target=None, drive_type=None):
    """The refusals of `move` that change nothing and need no lock: not Windows, a merge not done, and (with a `target`) a folder that is no
    local disk or lies in a cloud folder. A ValueError. cg.py asks before it waits for locks and before the folder window opens."""
    import encrypt
    if not encrypt.SUPPORTED:
        raise ValueError(t('index_dir_windows_only'))
    if where.merge_from:  # the records saved on C: while the folder was away are not in its index yet, and the old copy is that index
        raise ValueError(t('index_dir_merge_pending', dir=os.path.dirname(where.db), home=os.path.dirname(where.merge_from)))
    if target is None:
        return
    folder = os.path.abspath(target)
    n, drive = crypto.norm(folder), os.path.splitdrive(folder)[0]
    if not (os.path.isabs(target) and os.path.splitdrive(target)[0]) or drive.startswith('\\\\') or (drive_type or _drive_type)(drive + '\\') != DRIVE_FIXED:
        raise ValueError(t('index_dir_not_local', dir=folder))
    for var in CLOUD:
        cloud = crypto.norm(os.environ[var]) if os.environ.get(var) else None
        if cloud and (n == cloud or n.startswith(cloud.rstrip(os.sep) + os.sep)):
            raise ValueError(t('index_dir_cloud', dir=folder))


def _switch(out, where, target, key, ui, drive_type, free):
    """The move up to the switch, with `out` (see `move`) filled in. Every refusal is a ValueError raised before anything is changed; a
    failure later leaves at most a whole copy in the target, which the repeat takes for its own. Returns (the folders of old copies, the
    index that notes it) when the owner is to be asked about deleting it, else None."""
    import encrypt
    src, folder, notes = where.db, out['dir'], out['notes']
    cur, n, home = os.path.dirname(src), crypto.norm(out['dir']), crypto.norm(crypto.HOME_DIR)
    settings = os.path.join(crypto.HOME_DIR, 'settings.json')
    refuse(where, target, drive_type)
    if where.fallback and n == home:  # before "here": the folder C: works in now is the home one, and this makes it the permanent one
        crypto.set_fields(settings, {'indexDir': None})
        _unmark()
        out['done'] = True
        notes.append(t('index_dir_permanent', dir=folder))
        return None
    if n == crypto.norm(cur):
        old = []
        if os.path.isfile(src):
            with contextlib.closing(memdb.connect(src)) as conn:
                old = _old_copies(memdb.meta(conn, 'old_copy'))
        out['done'] = True  # after the read: a locked index there is a "busy" that changed nothing, and it is true
        out['here'] = True  # the reply does not announce a move
        notes.append(t('index_dir_here', dir=folder))
        return (old, src) if old else None
    if glob.glob(os.path.join(glob.escape(cur), '*.plain')):
        raise ValueError(t('index_dir_plain_left', dir=cur))
    new_db = os.path.join(folder, 'index.db')
    pairs = list(zip(encrypt._files(src), encrypt._files(new_db)))
    # one the target has must be a copy of ours, a half-done move of this one, and no journal lies there (a hot one belongs to some index)
    if any(os.path.exists(b) and not _same(a, b) or os.path.exists(b + '-journal') for a, b in pairs):
        if os.path.isfile(src) and any(os.path.exists(b) for _, b in pairs):  # a journal alone is no old copy
            with contextlib.closing(memdb.connect(src)) as conn:
                if n in map(crypto.norm, _old_copies(memdb.meta(conn, 'old_copy'))):  # the old copy of this index, left by an earlier move
                    raise ValueError(t('index_dir_old_copy_there', dir=folder, cur=cur))
        raise ValueError(t('index_dir_foreign', dir=folder))
    pairs = [(a, b) for a, b in pairs if os.path.isfile(a)]
    if key is None and any(crypto.is_encrypted(a) for a, _ in pairs):
        raise ValueError(t('key_missing'))
    crypto._read(settings)  # a settings file that cannot be read stops the move here, not after the copy
    efs = crypto.efs_on(src) or crypto.efs_on(os.path.dirname(settings))  # an index under EFS goes into a folder under EFS only, or nowhere
    if efs:
        try:
            supported = crypto.efs_supported(folder)
        except OSError as e:  # a drive that is not ready
            raise ValueError(t('efs_failed', dir=folder, e=e)) from None
        if not supported:
            raise ValueError(t('index_dir_no_efs', dir=folder))
        if n != home:  # the home folder is the mod's own; any other is walked whole by `encrypt on` and `off`
            encrypt._own_only(folder)
    if not os.path.exists(new_db):  # a copy of an earlier try has taken its room already
        need, room = sum(os.path.getsize(a) for a, _ in pairs) + SPARE, free(_existing(folder))
        if room < need:
            raise ValueError(t('index_dir_no_space', dir=folder, need=-(-need // MB), free=room // MB))
    field = None if n == home else folder  # the home folder is "no indexDir"
    made, d = [], folder  # the folders this move makes, the deepest first; only under EFS, where a refused move removes them again
    while efs and not os.path.exists(d) and os.path.dirname(d) != d:
        made.append(d)
        d = os.path.dirname(d)
    os.makedirs(folder, exist_ok=True)
    if efs:  # before any file is made in it: what is born in an encrypted folder is encrypted, a file renamed in from outside stays plain
        try:
            _efs_first(folder)
        except ValueError:  # a refused move leaves nothing behind
            for d in made:
                with contextlib.suppress(OSError):
                    os.rmdir(d)
            raise
    if not pairs:  # an index nowhere: the first one is made in the target
        crypto.INDEX_DIRS.add(n)
        memdb.connect(new_db).close()
        crypto.set_fields(settings, {'indexDir': field})
        out['done'] = True
        return None
    copies = [b + '.cg-move' for _, b in pairs]
    try:
        if efs:  # a copy of an earlier try, plain, is written over
            for c in copies:
                _efs_first(c)
        stamps = [_copy(a, c, key) for (a, _), c in zip(pairs, copies)]
        if [encrypt._stamp(a) for a, _ in pairs] != stamps:  # a chat wrote to an index meanwhile, and the copy lacks it
            raise ValueError(t('index_dir_busy'))  # before the handoffs and job.xml go over: "nothing was changed" stays true
        _copy_extras(cur, folder, efs)
        for c, (_, b) in zip(copies, pairs):
            os.replace(c, b)
    finally:
        for c in copies:  # what the replace did not take
            encrypt._remove(c)
    crypto.set_fields(settings, {'indexDir': field})  # from here every new run of cg.py works in the target
    crypto.INDEX_DIRS.add(n)
    out['done'] = True  # the switch is made: a locked index from here on is told as moved, not as "busy, nothing was changed"
    with contextlib.closing(memdb.connect(new_db)) as conn:  # the copy came with the old copies of the source: the source joins them
        _set_old(conn, [*_old_copies(memdb.meta(conn, 'old_copy')), cur])
    _unmark()  # left behind, the source is a plain old copy, and a mark beside it (a stale one too) would have the next run merge it and delete it
    if field:  # its forgets are kept at home from now on: the folder may be away before the next run
        keep_forgets(new_db)
    return [cur], new_db


@contextlib.contextmanager
def _late(out):
    """A database that is locked once the switch is made is not the "busy" of a move that changed nothing (cg.py turns every 'locked' into it)."""
    try:
        yield
    except memdb.db_errors() as e:
        if out['done'] and 'locked' in str(e):
            raise ValueError(t('index_dir_locked_after', dir=out['dir'])) from None
        raise


def move(where, target, key, ui, drive_type=None, free=None, locks=()):
    """/cg-index-dir: the index in use (`where.db`) moves to the folder `target` (a full path). The checks come first and change nothing. Then
    the index files are copied under their write lock, counted and put in place, the settings point to the target (`indexDir`, the other
    fields as they were), and only then the owner is asked about the old copy (`ui.confirm`, default No). Result: {'action': 'index-dir',
    'done': True, 'dir': the target, 'notes': what the owner is told besides, and 'here': True when the index was there already}. A refusal or a failure is a ValueError. `key` opens an encrypted
    index (the home settings hold it). `drive_type(root)` and `free(path)` are the system's by default. `locks`: the connections the caller
    holds on the source index (memdb.lock). They are closed here as soon as the switch is made, or on any failure: with them open the lock
    files and the index could not be deleted."""
    folder = os.path.abspath(target)
    out = {'action': 'index-dir', 'done': False, 'dir': folder, 'notes': []}
    try:
        with _late(out):
            old = _switch(out, where, target, key, ui, drive_type or _drive_type, free or _free)
    finally:
        for lock in locks:
            lock.close()
    if where.fallback and out['done'] and crypto.norm(folder) not in (crypto.norm(crypto.HOME_DIR), crypto.norm(where.configured)):
        out['notes'].append(t('index_dir_left_behind', dir=where.configured))  # a third folder: what the configured one holds is left
    if old:
        with _late(out):
            _drop_old(*old, ui, out['notes'])
    return out
