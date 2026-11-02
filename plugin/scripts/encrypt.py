"""The commands behind /cg-encrypt: encryption on, a key joined from another computer, encryption off, and its state. Each one can be run
again after any interruption and goes on from where it stopped: first the key, then the index files, then the sync folder. The key and the
recovery code are in no result, exception or output; the code goes only to the window (`ui.show_code`).
`ui` is the windows: show_code(code), confirm(text) -> bool, ask_code() -> str | None (None: cancelled)."""
import contextlib
import os
import secrets
import shutil
import stat
import time

import crypto
import memdb
import sync
from texts import t
from vectors import MODEL

SUPPORTED = os.name == 'nt'  # the key sits under Windows DPAPI
_TABLES = ('chunks', 'keys', 'vecs')


def _files(db):
    """The index and its backup from before stage 2, which holds the same text."""
    return [db, os.path.join(os.path.dirname(db), 'index.before-stage2.db')]


def _size(path):
    try:
        return os.path.getsize(path)
    except FileNotFoundError:
        return 0


def _can(db, index_cipher, cipher):
    """Before anything is touched: the system, and the packages this needs. SQLCipher is loaded for a cipher that is one, or an index that is."""
    if not SUPPORTED:
        raise ValueError(t('encrypt_unsupported'))
    if index_cipher == 'sqlcipher' or any(crypto.is_encrypted(p) for p in _files(db)):
        crypto.need('sqlcipher')
    crypto.need(cipher)


def _key(db):
    """(settings file, key or None) of this index. An encrypted index with no key is an error, never a new index over it."""
    settings = crypto.settings_path(db)
    key = crypto.load_key(settings)
    if key is None and any(crypto.is_encrypted(p) for p in _files(db)):
        raise ValueError(t('key_missing'))
    return settings, key


def _result(action, notes):
    return {'action': action, 'done': not notes, 'notes': notes}


def _shared(sync_dir):
    """{computer: its current generation} for the folders of the sync folder, ours too; none when there is no such folder."""
    if not sync_dir or not os.path.isdir(sync_dir):
        return {}
    return {d: sync.generation(os.path.join(sync_dir, d)) for d in sorted(os.listdir(sync_dir)) if os.path.isdir(os.path.join(sync_dir, d))}


# ---- the index files

def _open(path, key):
    """The database at `path` through SQLCipher, under `key`, or as it is when there is none (an open one)."""
    if key:
        return crypto.open_db(path, key, page=crypto.INDEX_PAGE)
    import sqlcipher3
    conn = sqlcipher3.connect(path, isolation_level=None, timeout=30)
    conn.execute('PRAGMA cipher_log_level = NONE')
    conn.execute('PRAGMA temp_store = MEMORY')  # as in crypto.plain_db
    return conn


def _counts(conn):
    """The rows of the tables that matter; an index from before stage 2 lacks some of them."""
    have = {n for (n,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return {n: conn.execute('SELECT count(*) FROM ' + n).fetchone()[0] for n in _TABLES if n in have}


def _remove(path):
    """A file and the journal beside it, if they are there."""
    for p in (path, path + '-journal'):
        with contextlib.suppress(FileNotFoundError):
            os.remove(p)


def _stamp(path):
    """The start of a database file as it is: every commit rewrites page 1 (the change counter of an open file, fresh ciphertext of an encrypted one)."""
    with open(path, 'rb') as f:
        return f.read(4096)


def _replace(new, path, stamp):
    """Windows will not replace a file another process has open: wait up to 10 s for it, then say it is busy. A chat takes none of our locks,
    so a commit may land in `path` after the copy was made (`stamp` is its start then): the copy lacks it, and the repeat copies again."""
    for _ in range(100):
        try:
            if _stamp(path) != stamp:
                raise ValueError(t('encrypt_busy'))
            # shortcut: a commit between this compare and the replace (microseconds) still goes; only a lock held across the replace would stop it
            return os.replace(new, path)
        except PermissionError:
            time.sleep(0.1)
    raise ValueError(t('encrypt_busy'))


@contextlib.contextmanager
def _busy():
    """A database that another connection holds locked (a chat takes none of our locks) is no failure of ours: nothing was changed, try again.
    Used as a decorator."""
    try:
        yield
    except memdb.db_errors() as e:
        if 'locked' in str(e):
            raise ValueError(t('encrypt_busy')) from None
        raise


@_busy()
def _convert(path, was, now, keep=None, page=None):
    """The database at `path` under another key (`was` to `now`, None is open): a copy is made beside it under the write lock of the
    original, counted against it from a fresh open, and only then put in its place. `keep` is a hard link to the original made just
    before, so the old file outlives the replacing. `page`: the page size of an open copy (an encrypted one always has the index's).
    A `.cg-new` of a run that died is cleared by the caller."""
    new = path + '.cg-new'
    try:
        src = _open(path, was)
        try:
            src.execute('ATTACH DATABASE ? AS target KEY ?', (new, "x'%s'" % now.hex() if now else ''))
            if now:
                src.execute('PRAGMA target.cipher_page_size = %d' % crypto.INDEX_PAGE)
            elif page:
                src.execute('PRAGMA target.page_size = %d' % page)
            src.execute('BEGIN IMMEDIATE')
            src.execute("SELECT sqlcipher_export('target')")
            want = _counts(src)
            stamp = _stamp(path)  # under the lock: what the copy holds is what this file was
            src.execute('COMMIT')
        finally:
            src.close()
        dst = _open(new, now)
        try:
            got = _counts(dst)
        finally:
            dst.close()
        if got != want:
            raise ValueError(t('encrypt_copy_differs', path=path))
        if keep:
            os.link(path, keep)
        _replace(new, path, stamp)
    except BaseException:
        _remove(new)
        raise


def _drop_plain(plain, ui, notes):
    """The unencrypted copy deleted if the owner says so, else a note; whether it is gone."""
    if ui.confirm(t('dlg_delete_plain', path=plain)):
        os.remove(plain)
        return True
    notes.append(t('note_plain_kept', path=plain))
    return False


def _encrypt_file(path, key, ui, notes):
    """One index file encrypted, then its open copy deleted if the owner says so. A file that is encrypted already is not touched, only the
    question about a copy left from an earlier run is asked again. A `.plain` beside an open file that is not its link is somebody's data:
    it is asked about first, and with "No" the file is left as it is this time."""
    _remove(path + '.cg-new')  # a whole copy of a run that died, in whichever form: never the only one, os.replace is atomic
    if not _size(path):
        return
    plain = path + '.plain'
    if not crypto.is_encrypted(path):
        if os.path.exists(plain):
            if os.path.samefile(plain, path):
                os.remove(plain)  # the link of a replace that failed: the same file, no data goes with it
            elif not _drop_plain(plain, ui, notes):
                return
        _convert(path, None, key, keep=plain)
    if os.path.exists(plain):
        _drop_plain(plain, ui, notes)


# ---- the index under EFS: the files stay plain SQLite, the folders they lie in are encrypted by Windows

def _efs_dirs(db):
    """The mod's folder (the settings file lies there) and the index's, once each."""
    return list(dict.fromkeys((os.path.dirname(crypto.settings_path(db)), os.path.dirname(db))))


def _efs_do(fn, path):
    """`fn` (efs_encrypt or efs_decrypt) on `path`. A file in use is waited for up to 10 s, like _replace; any other refusal is told with the path."""
    for _ in range(100):
        try:
            return fn(path)
        except OSError as e:
            if e.winerror != 32:
                raise ValueError(t('efs_failed', dir=path, e=e)) from None
            time.sleep(0.1)
    raise ValueError(t('encrypt_busy'))


def _own_only(folder):
    """EFS takes everything that lies in a folder, so an index folder that is not the mod's own one may hold only what the mod makes in it: the
    index files and their copies, journals and locks (index.*), `handoffs` and job.xml. Else it is told, and nothing is changed."""
    try:
        names = [n.lower() for n in os.listdir(folder)]
    except OSError as e:
        _unlistable(e)  # a folder that is not there yet holds nothing
        return
    if any(not (n.startswith('index.') or n in ('handoffs', 'job.xml')) for n in names):
        raise ValueError(t('efs_foreign_files', dir=folder))


def _efs_probe(db):
    """Before the key, the code or any file: the volumes of both folders have EFS, the index's folder holds nothing but the mod's files, and the
    first EncryptFileW (on the mod's folder) goes through. A Windows Home refuses it here."""
    dirs = _efs_dirs(db)
    for d in dirs:
        try:
            supported = crypto.efs_supported(d)
        except OSError as e:  # a drive that is not there or not ready
            raise ValueError(t('efs_failed', dir=d, e=e)) from None
        if not supported:
            raise ValueError(t('efs_unsupported', dir=d))
    if len(dirs) > 1 and crypto.norm(dirs[1]) != crypto.norm(dirs[0]):
        _own_only(dirs[1])
    _efs_do(crypto.efs_encrypt, dirs[0])


def _unlistable(e):
    """The onerror of os.walk: a folder that cannot be listed is told (a walk that skipped it would leave its files in clear), one that is gone is not."""
    if not isinstance(e, FileNotFoundError):
        raise ValueError(t('efs_failed', dir=e.filename, e=e)) from None


def _efs_all(db, fn, want):
    """`fn` on every folder and file of both folders whose EFS bit is not `want` yet. Encrypting goes top-down, a folder before what lies in
    it (from then on a file made there is born encrypted); decrypting bottom-up, a folder after its files, so a cut leaves the top folder
    under EFS and a repeat of `off` still sees it. Not the lock files: they are empty, and the caller holds them open."""
    def one(p):
        try:
            fn(p)
        except OSError:
            if os.path.lexists(p):  # one that is gone between the listing and the call needs nothing (EncryptFileW tells it as "access denied")
                raise

    for top in _efs_dirs(db):
        for d, _, names in os.walk(top, topdown=want, onerror=_unlistable):
            items = [d] + [os.path.join(d, n) for n in names if not n.endswith('.lock')]
            for p in items if want else reversed(items):
                if crypto.efs_on(p) != want:
                    _efs_do(one, p)


@_busy()
def _to_efs(path, key):
    """One index file, in a folder under EFS already, as a plain database with 16 KB pages: SQLCipher is converted (the copy is born
    encrypted in the same folder, so nothing plain lies outside EFS), a plain one with smaller pages is vacuumed into them."""
    _remove(path + '.cg-new')  # a whole copy of a run that died, in whichever form
    if not _size(path):
        return
    folder = os.path.dirname(path)
    if not crypto.efs_on(folder):  # never a plain copy outside EFS
        raise ValueError(t('efs_unsupported', dir=folder))
    if crypto.is_encrypted(path):
        _convert(path, key, None, page=crypto.INDEX_PAGE)
        return
    conn = crypto.plain_db(path)
    try:
        if conn.execute('PRAGMA page_size').fetchone()[0] < crypto.INDEX_PAGE:
            conn.execute('PRAGMA page_size = %d' % crypto.INDEX_PAGE)
            conn.execute('VACUUM')
    finally:
        conn.close()


# ---- the sync folder

def _delete(path):
    """A file, a folder or a link out of the share: a link goes by itself, never what it points to (the share is not to be trusted). A
    junction is told by its reparse point bit, a symlink by lstat: nothing here needs Python above 3.8 (Windows only, like the command)."""
    st = os.lstat(path)
    if stat.S_ISDIR(st.st_mode) and not st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        shutil.rmtree(path)
    else:
        os.remove(path)


def _flag(conn, value):
    """meta.share_unfinished for /cg-health: 'pending' (the new generation is not all out), 'old' (the old data is still in the folder)."""
    if value:
        conn.execute("INSERT OR REPLACE INTO meta VALUES('share_unfinished', ?)", (value,))
    else:
        conn.execute("DELETE FROM meta WHERE key = 'share_unfinished'")


def _generations(old):
    """How many generations the paths of `old_generations` make: the entries of generation 0 lie in the folder itself and count as one."""
    names = [os.path.basename(p) for p in old]
    return sum(n.startswith('gen-') for n in names) + any(not n.startswith('gen-') for n in names)


def _share(conn, sync_dir, machine, key, cipher, ui, notes, **kw):
    """Moves the sync folder to the wanted form (`key` None: open): our new generation out, then the old ones gone once the owner agrees.
    The flag is set before each step and cleared only when the last one is done, so a failure or a kill in between leaves it for /cg-health.
    Whether the new generation is out is read from the folder after the sync, not taken from the sync having returned: it may stop at the
    time cap, or an own batch that cannot be read may keep the new generation from starting."""
    _flag(conn, 'pending')
    report = sync.sync(conn, sync_dir, machine, key=key, cipher=cipher, **kw)
    own = os.path.join(sync_dir, machine)
    gen = sync.generation(own)
    wanted = (cipher, crypto.fingerprint(key)) if key else (None, None)
    if (gen.cipher, gen.fingerprint) != wanted or sync.pending(conn) or any(e['where'] == 'sync' for e in report['errors']):
        notes.append(t('note_share_pending'))
        notes += [e['error'] for e in report['errors'] if e['where'] == 'sync' or e.get('machine') == machine]
        return
    old = sync.old_generations(own)
    if old:
        _flag(conn, 'old')
        if not ui.confirm(t('dlg_delete_old_share', n=_generations(old))):
            notes.append(t('note_old_share_kept'))
            return
        for path in old:
            _delete(path)
    _flag(conn, None)


def _encrypt(db, sync_dir, cipher, machine, ui, key, notes, index_cipher, **kw):
    """What `on` and `join` do once the key is in the settings: the index files, then the sync folder."""
    if index_cipher == 'efs':
        _efs_all(db, crypto.efs_encrypt, True)  # all of it first: what _to_efs writes beside a file is born encrypted
        for path in _files(db):
            _to_efs(path, key)
            if os.path.exists(path + '.plain'):  # of an earlier `on` whose question was answered No
                _drop_plain(path + '.plain', ui, notes)
    else:
        for path in _files(db):
            _encrypt_file(path, key, ui, notes)
    if sync_dir:
        with contextlib.closing(memdb.connect(db)) as conn:
            _share(conn, sync_dir, machine, key, cipher, ui, notes, **kw)


# ---- the commands

def on(db, sync_dir, cipher, machine, ui, index_cipher='sqlcipher', model=MODEL, memory_dir=None, project_cwd=None, seconds=600):
    """Encryption on: a key (shown once as a recovery code), the index files encrypted by `index_cipher` ('efs': their folders are, and the
    files become plain SQLite with 16 KB pages), the sync folder switched to `cipher`; both go into the settings file beside the key (the
    background job reads them there) on every run. With the key in the settings already the code is not shown again. `seconds`: how long to
    push to the sync folder."""
    _can(db, index_cipher, cipher)
    if index_cipher == 'efs':
        _efs_probe(db)
    settings, key = _key(db)
    machine = machine or sync.machine_name()
    for peer, gen in _shared(sync_dir).items():  # a computer is encrypted already, this one too after a reinstall: its key is ours to join, not a second one to make
        if gen.cipher and (key is None or gen.fingerprint != crypto.fingerprint(key)):
            raise ValueError(t('encrypt_use_join', machine=peer))
    if key is None:
        if sync_dir and not os.path.isdir(sync_dir):  # the check above saw nothing: another key may be in there
            raise ValueError(t('encrypt_share_unreachable', dir=sync_dir))
        key = secrets.token_bytes(32)
        ui.show_code(crypto.make_code(key))
        if not ui.confirm(t('dlg_code_saved')):
            return _result('on', [t('code_not_saved')])
    crypto.save_key(settings, key, cipher, index_cipher)  # a new key and the ciphers in one write; a key that is there is written again (same key, new blob)
    notes = []
    _encrypt(db, sync_dir, cipher, machine, ui, key, notes, index_cipher, model=model, memory_dir=memory_dir, project_cwd=project_cwd,
             max_seconds=seconds)
    return _result('on', notes)


def join(db, sync_dir, cipher, machine, ui, index_cipher='sqlcipher', model=MODEL, memory_dir=None, project_cwd=None, seconds=600):
    """The key of another computer entered from its recovery code, after it was checked against what it must open (the encrypted data in the
    sync folder, the encrypted index here), then the same as `on`. Also the way back when the key here was lost."""
    _can(db, index_cipher, cipher)
    if index_cipher == 'efs':
        _efs_probe(db)
    code = ui.ask_code()
    if code is None:
        return {'action': 'join', 'done': False, 'notes': []}
    key = crypto.parse_code(code)
    settings = crypto.settings_path(db)
    fingerprints = {g.fingerprint for g in _shared(sync_dir).values() if g.cipher}
    if fingerprints and crypto.fingerprint(key) not in fingerprints:  # first: a stale code is wrong, whatever key this computer has
        raise ValueError(t('code_other_key'))
    try:
        have = crypto.load_key(settings)
    except crypto.SettingsBroken:
        raise
    except ValueError:  # a key that cannot be opened: the code replaces it
        have = None
    if have and crypto.fingerprint(have) != crypto.fingerprint(key):
        raise ValueError(t('join_other_key_here'))
    if crypto.is_encrypted(db):
        crypto.open_db(db, key, page=crypto.INDEX_PAGE).close()
    elif not fingerprints:
        raise ValueError(t('code_unverifiable'))
    crypto.save_key(settings, key, cipher, index_cipher)
    notes = []
    _encrypt(db, sync_dir, cipher, machine or sync.machine_name(), ui, key, notes, index_cipher, model=model, memory_dir=memory_dir,
             project_cwd=project_cwd, max_seconds=seconds)
    return _result('join', notes)


def off(db, sync_dir, machine, ui, model=MODEL, memory_dir=None, project_cwd=None, seconds=600):
    """Encryption off: the index files open again (the encrypted ones are replaced, no copy is kept), EFS taken off the folders that hold it
    and what lies in them, the key gone from the settings, our folder in the sync folder open again. The flag in the index that lets the sync
    leave an encrypted folder is set before the key goes."""
    settings, key = _key(db)
    for path in _files(db):
        _remove(path + '.cg-new')  # of a run that died: a plain copy of the encrypted index
        if crypto.is_encrypted(path):
            _convert(path, key, None)
    if any(map(crypto.efs_on, _efs_dirs(db) + _files(db))):  # no walk through folders and files that EFS does not hold
        _efs_all(db, crypto.efs_decrypt, False)
    if key and not _size(db):  # no index to flag: the connect below would make one under the key, and under EFS again
        crypto.save_key(settings, None)
        key = None
    machine = machine or sync.machine_name()
    notes = []
    with contextlib.closing(memdb.connect(db)) as conn:
        conn.execute("INSERT OR REPLACE INTO meta VALUES('share_plain', '1')")
        if key:
            crypto.save_key(settings, None)
        if sync_dir:
            _share(conn, sync_dir, machine, None, None, ui, notes, model=model, memory_dir=memory_dir, project_cwd=project_cwd, max_seconds=seconds)
    return _result('off', notes)


def status(db):
    """Where encryption stands, from the files alone (the index is not opened): on or off, the index ('none', 'sqlcipher', 'efs' or 'plain'),
    the key, and what is left to finish."""
    notes = []
    try:
        key = 'ok' if crypto.load_key(crypto.settings_path(db)) else 'missing'
    except (ValueError, OSError) as e:  # a key that cannot be opened, a settings file that is broken or cannot be opened; the text says which
        key = 'unreadable'
        notes.append(str(e))
    state = 'none' if not _size(db) else 'sqlcipher' if crypto.is_encrypted(db) else 'efs' if crypto.efs_on(db) else 'plain'
    if state == 'sqlcipher' and key == 'missing':
        notes.append(t('key_missing'))
    if state == 'plain' and key == 'ok':
        notes.append(t('note_index_plain'))
    notes += [t('note_plain_kept', path=p + '.plain') for p in _files(db) if os.path.exists(p + '.plain')]
    return {'on': key != 'missing' or state in ('sqlcipher', 'efs'), 'db': state, 'key': key, 'notes': notes}
