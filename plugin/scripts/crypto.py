"""The encryption key of the index and of the shared folder: kept in the mod's settings file under Windows DPAPI (bound to the Windows
user), restorable from a recovery code. The key, the code and the unprotected bytes never go into a message, an exception or a log.
Also the sealed containers of both ciphers (a batch, a memory file) and the opening of the SQLCipher index."""
import base64
import contextlib
import ctypes
import gzip
import hashlib
import hmac
import importlib
import json
import os
import re
import sqlite3
import stat
import tempfile

from texts import t

KEY_FIELD = 'encryptionKey'
ENTROPY = b'Claude-CacheGuard'  # another program of the same user does not open the blob by accident
PREFIX = 'dpapi:'
CIPHERS = ('sqlcipher', 'cryptography')
INDEX_CIPHERS = ('sqlcipher', 'efs')
EXT = {None: '.jsonl.gz', 'sqlcipher': '.sqlcipher', 'cryptography': '.aesgcm'}
INDEX_PAGE = 16384  # the SQLCipher index and the one under EFS: the vector read of recall is 0.2 s slower with the default 4096; containers keep 4096 (their format)
# the package a cipher imports, lazily: without it the mod works as before with the encryption off. 'cryptography' is AES-GCM of Windows' own
# bcrypt.dll and needs none (a package's unsigned .pyd may be blocked by Smart App Control)
_MODULE = {'sqlcipher': 'sqlcipher3'}


HOME_DIR = os.path.join(os.path.expanduser('~'), '.claude', 'cacheguard')  # the mod's own folder: the settings, and the index unless moved
INDEX_DIRS = set()  # norm() of every other folder an index lives in: the settings of such an index are still the home ones, the key is there


def norm(path):
    return os.path.normcase(os.path.abspath(path))


def settings_path(db):
    """The mod's settings file lies beside the database, as cg.py reads it; but an index in a registered folder uses the home one."""
    d = os.path.dirname(db)
    return os.path.join(HOME_DIR if norm(d) in INDEX_DIRS else d, 'settings.json')


def _check(key):
    return hashlib.sha256(b'cacheguard-code' + key).digest()[:3]


def make_code(key):
    """CG1 and 14 groups of 4 base32 characters: the key and 3 bytes of checksum, which catches a typo."""
    b = base64.b32encode(key + _check(key)).decode()
    return 'CG1-' + '-'.join(b[i:i + 4] for i in range(0, len(b), 4))


def parse_code(code):
    """The key from a code written down or typed any way: case, spaces and hyphens do not matter."""
    s = re.sub(r'[\s-]', '', code.upper())
    try:
        raw = base64.b32decode(s[3:])
    except ValueError:
        raw = b''
    if not s.startswith('CG1') or len(raw) != 35 or raw[32:] != _check(raw[:32]):
        raise ValueError(t('code_bad'))
    return raw[:32]


def fingerprint(key):
    """Tells keys apart without revealing one: a share written under another key is refused by it."""
    return hashlib.sha256(b'cacheguard-fingerprint' + key).hexdigest()[:16]


def opaque(key, name):
    """The name of a project in the shared folder: the same on every computer with the key, meaningless without it."""
    return hmac.new(key, name.encode('utf-8'), 'sha256').hexdigest()[:32]


class _Blob(ctypes.Structure):  # DATA_BLOB
    _fields_ = [('n', ctypes.c_uint32), ('p', ctypes.c_void_p)]


def _dpapi(protect, data):
    crypt32, kernel32 = ctypes.WinDLL('crypt32', use_last_error=True), ctypes.WinDLL('kernel32')
    blob, ref, ptr = ctypes.POINTER(_Blob), ctypes.byref, ctypes.c_void_p
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [blob, ptr, blob, ptr, ptr, ctypes.c_uint32, blob]
    kernel32.LocalFree.argtypes = [ptr]
    bufs = [ctypes.create_string_buffer(b, len(b)) for b in (data, ENTROPY)]
    ins = [_Blob(len(b), ctypes.addressof(b)) for b in bufs]
    out = _Blob()
    if not fn(ref(ins[0]), None, ref(ins[1]), None, None, 0x1, ref(out)):  # CRYPTPROTECT_UI_FORBIDDEN: no prompt, only an error
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.p, out.n)
    finally:
        kernel32.LocalFree(out.p)


def protect(data):
    return _dpapi(True, data)


def unprotect(blob):
    return _dpapi(False, blob)


class SettingsBroken(ValueError):
    """The settings file is no readable object: what it holds, a key too, is unknown."""


def _read(settings):
    """The settings object, {} when there is no file. A file that cannot be read is never taken for one without a key, nor written over."""
    try:
        with open(settings, encoding='utf-8-sig') as f:  # Windows editors save it with a BOM
            s = json.load(f)
    except FileNotFoundError:
        return {}
    except ValueError:
        s = None
    if not isinstance(s, dict):
        raise SettingsBroken(t('settings_broken', path=settings))
    return s


def load_key(settings):
    """The key, None when there is none; a field that this user cannot open or that is not a key is an error, not 'no key'."""
    v = _read(settings).get(KEY_FIELD)
    if v is None:
        return None
    try:
        key = unprotect(base64.b64decode(v[len(PREFIX):], validate=True)) if isinstance(v, str) and v.startswith(PREFIX) else b''
    except (ValueError, OSError):
        key = b''
    if len(key) != 32:
        raise ValueError(t('key_unreadable'))
    return key


def atomic_write(path, data):
    """`data` (bytes) replaces the file at `path` in one step, and is on the disk before it does: after a power cut the file is the old one or
    the new one, never a new one made of zeros."""
    tmp = path + '.cg-tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def set_fields(settings, updates):
    """Writes `updates` into the settings file in one atomic write (a None value takes its key out); the other fields as they were."""
    s = _read(settings)
    for k, v in updates.items():
        if v is None:
            s.pop(k, None)
        else:
            s[k] = v
    atomic_write(settings, json.dumps(s, ensure_ascii=False, indent=2).encode('utf-8'))


def save_key(settings, key, cipher=None, index_cipher=None):
    """Puts the key into the settings file (None takes it out) and, if given, shareCipher and indexCipher beside it, in one write; the other
    fields as they were. The background job reads the cipher from this file only, so it must hold the one the sync folder was switched to."""
    _read(settings)  # a settings file that cannot be read is told first: a DPAPI error must not hide it
    up = {KEY_FIELD: None if key is None else PREFIX + base64.b64encode(protect(key)).decode()}
    if cipher is not None:  # none given, none taken: set_fields would read None as "remove"
        up['shareCipher'] = cipher
    if index_cipher is not None:
        up['indexCipher'] = index_cipher
    set_fields(settings, up)


def need(cipher):
    """The package of `cipher` is there, or an error that says how to install it; AES-GCM has none, so for it this always passes."""
    if cipher in _MODULE:
        try:
            importlib.import_module(_MODULE[cipher])
        except ImportError:
            raise ValueError(t('cipher_missing', pkg=_MODULE[cipher])) from None


def efs_supported(path):
    """The volume of the nearest folder of `path` that exists can encrypt with EFS (NTFS does; FAT, exFAT and some network drives do not)."""
    d = os.path.abspath(path)
    while not os.path.isdir(d) and os.path.dirname(d) != d:
        d = os.path.dirname(d)
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    root, flags = ctypes.create_unicode_buffer(32768), ctypes.c_uint32()
    if not (k32.GetVolumePathNameW(d, root, len(root)) and k32.GetVolumeInformationW(root, None, 0, None, None, ctypes.byref(flags), None, 0)):
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(flags.value & 0x20000)  # FILE_SUPPORTS_ENCRYPTION


def efs_on(path):
    """The file or folder is encrypted by EFS; one that is not there is not."""
    try:
        return bool(getattr(os.stat(path), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_ENCRYPTED)
    except FileNotFoundError:
        return False


def _efs_denied(path):
    """EFS holds `path` and Windows answers "access denied" (5) to a read of it. open() and SQLite tell no Windows code, so the file is tried
    once more with the plain API: a file in use (32), any other code, or no failure at all is no refusal and the caller's error stands."""
    if not efs_on(path):
        return False
    import _winapi
    try:
        _winapi.CloseHandle(_winapi.CreateFile(path, _winapi.GENERIC_READ, 7, 0, _winapi.OPEN_EXISTING, 0, 0))  # 7: share read, write and delete
    except OSError as e:
        return e.winerror == 5
    return False


def _efs(call, path, *more):
    if not getattr(ctypes.WinDLL('advapi32', use_last_error=True), call)(path, *more):
        raise ctypes.WinError(ctypes.get_last_error())  # an OSError with winerror; a file in use is for the caller to retry


def efs_encrypt(path):
    _efs('EncryptFileW', path)


def efs_decrypt(path):
    _efs('DecryptFileW', path, 0)


def is_encrypted(path):
    """By the header: an open SQLite file begins with its magic, an encrypted one with random bytes. No file or an empty one is not."""
    try:
        with open(path, 'rb') as f:
            head = f.read(16)
    except FileNotFoundError:
        return False
    except PermissionError:  # the same error for a refusal and for a file in use: _efs_denied tells which
        if _efs_denied(path):
            raise ValueError(t('index_efs_denied', path=path)) from None
        raise
    return bool(head) and head != b'SQLite format 3\x00'


def plain_db(path, timeout=30):
    """The open SQLite database at `path`, no implicit transactions. An index that EFS holds for another user is an error, never a new one."""
    try:
        conn = sqlite3.connect(path, isolation_level=None, timeout=timeout)
    except sqlite3.OperationalError as e:
        if 'unable to open' in str(e) and _efs_denied(path):
            raise ValueError(t('index_efs_denied', path=path)) from None
        raise
    conn.execute('PRAGMA temp_store = MEMORY')  # %TEMP% is not under EFS: nothing of the index may spill there in clear
    return conn


def open_db(path, key, timeout=30, page=None):
    """The SQLCipher database at `path` (a new one when there is no file), keyed and checked by a read: a wrong key shows only then.
    `page` is its cipher_page_size, which the file must have been made with (the default is 4096)."""
    need('sqlcipher')
    import sqlcipher3
    conn = sqlcipher3.connect(path, isolation_level=None, timeout=timeout)
    try:
        conn.execute('PRAGMA cipher_log_level = NONE')  # else a wrong key is also written to stderr from C
        conn.execute('PRAGMA key = "x\'%s\'"' % key.hex())
        if page:
            conn.execute('PRAGMA cipher_page_size = %d' % page)
        conn.execute('PRAGMA temp_store = MEMORY')  # as in plain_db
        conn.execute('SELECT count(*) FROM sqlite_master')
    except sqlcipher3.DatabaseError as e:
        conn.close()
        if isinstance(e, sqlcipher3.OperationalError):  # busy or locked says nothing about the key
            raise
        raise ValueError(t('key_wrong')) from None
    return conn


@contextlib.contextmanager
def _scratch():
    """A local file for one SQLCipher container: SQLite does not lock on a network drive, so only finished bytes go there. The caller
    closes its connection before leaving (Windows will not remove an open file)."""
    fd, path = tempfile.mkstemp(suffix='.cg')
    os.close(fd)
    try:
        yield path
    finally:
        for p in (path, path + '-journal'):
            with contextlib.suppress(OSError):  # a scanner may hold the file for a moment: a leftover is only ciphertext, a result is not lost for it
                os.remove(p)


_P, _L, _W = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p


class _AuthInfo(ctypes.Structure):  # BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO; ctypes pads it like MSVC (88 bytes on x64)
    _fields_ = [('cbSize', _L), ('dwInfoVersion', _L), ('pbNonce', _P), ('cbNonce', _L), ('pbAuthData', _P), ('cbAuthData', _L),
                ('pbTag', _P), ('cbTag', _L), ('pbMacContext', _P), ('cbMacContext', _L), ('cbAAD', _L), ('cbData', ctypes.c_ulonglong),
                ('dwFlags', _L)]


_TAG_MISMATCH = 0xC000A002  # STATUS_AUTH_TAG_MISMATCH
_CNG = {  # the arguments of the calls _gcm makes; a handle is 64-bit, so a void pointer and not the int default
    'BCryptOpenAlgorithmProvider': [_P, _W, _W, _L],
    'BCryptSetProperty': [_P, _W, _P, _L, _L],
    'BCryptGenerateSymmetricKey': [_P, _P, _P, _L, _P, _L, _L],
    'BCryptEncrypt': [_P, _P, _L, _P, _P, _L, _P, _L, _P, _L],
    'BCryptDecrypt': [_P, _P, _L, _P, _P, _L, _P, _L, _P, _L],
    'BCryptDestroyKey': [_P],
    'BCryptCloseAlgorithmProvider': [_P, _L],
}


def _chk(status):
    if status < 0:  # an NTSTATUS: warnings and information (>= 0) are no failure
        raise OSError('CNG failed, NTSTATUS 0x%08X' % (status & 0xFFFFFFFF))


def _gcm(decrypt, key, nonce, data, aad, tag):
    """AES-256-GCM by Windows CNG (bcrypt.dll), nothing to install. Encrypts `data` into (ciphertext, tag), or decrypts it into (text, tag)
    after checking `tag`: None when the tag does not match (a wrong key, aad or nonce, or a changed byte). Any other failure is an OSError."""
    if len(key) != 32 or len(nonce) != 12 or len(tag) != 16:
        raise ValueError('_gcm: a 32-byte key, a 12-byte nonce and a 16-byte tag')
    if len(data) > 0x7FFFFFFF or len(aad) > 0x7FFFFFFF:  # the ULONG lengths would wrap silently
        raise ValueError('_gcm: data or aad over 2 GB')
    bc, buf, ref = ctypes.WinDLL('bcrypt'), ctypes.create_string_buffer, ctypes.byref
    for name, args in _CNG.items():
        fn = getattr(bc, name)
        fn.argtypes, fn.restype = args, ctypes.c_long
    alg, hkey = _P(), _P()
    _chk(bc.BCryptOpenAlgorithmProvider(ref(alg), 'AES', None, 0))
    try:
        mode = ctypes.create_unicode_buffer('ChainingModeGCM')
        _chk(bc.BCryptSetProperty(alg, 'ChainingMode', mode, ctypes.sizeof(mode), 0))
        _chk(bc.BCryptGenerateSymmetricKey(alg, ref(hkey), None, 0, buf(key, 32), 32, 0))  # no key object buffer: Windows 8 and later
        try:
            n, a, g = buf(nonce, 12), buf(aad), buf(tag, 16)
            info = _AuthInfo(ctypes.sizeof(_AuthInfo), 1, ctypes.addressof(n), 12, ctypes.addressof(a), len(aad), ctypes.addressof(g), 16)
            d, out, done = buf(data), buf(len(data) + 1), _L()
            st = (bc.BCryptDecrypt if decrypt else bc.BCryptEncrypt)(hkey, d, len(data), ref(info), None, 0, out, len(data), ref(done), 0)
            if decrypt and (st & 0xFFFFFFFF) == _TAG_MISMATCH:
                return None
            _chk(st)
            return out.raw[:len(data)], g.raw
        finally:
            _chk(bc.BCryptDestroyKey(hkey))
    finally:
        _chk(bc.BCryptCloseAlgorithmProvider(alg, 0))


def seal(lines, cipher, key, aad):
    """The lines, compressed and sealed; `aad` ties the container to its place, so a copy moved elsewhere does not open."""
    need(cipher)
    body = gzip.compress('\n'.join(lines).encode('utf-8'), mtime=0)
    if cipher == 'cryptography':
        nonce = os.urandom(12)
        ct, tag = _gcm(False, key, nonce, body, aad.encode('utf-8'), bytes(16))
        return nonce + ct + tag
    with _scratch() as path:
        conn = open_db(path, key)
        try:
            conn.execute('CREATE TABLE c(aad TEXT, body BLOB)')
            conn.execute('INSERT INTO c VALUES(?, ?)', (aad, body))
        finally:
            conn.close()
        with open(path, 'rb') as f:
            return f.read()


def _gcm_body(data, key, aad):
    if len(data) < 28:  # shorter than a nonce and a tag
        return None
    opened = _gcm(True, key, data[:12], data[12:-16], aad.encode('utf-8'), data[-16:])
    return opened and opened[0]


def _db_body(data, key, aad):
    import sqlcipher3
    with _scratch() as path:
        with open(path, 'wb') as f:
            f.write(data)
        try:
            conn = open_db(path, key)
            try:
                row = conn.execute('SELECT body FROM c WHERE aad = ?', (aad,)).fetchone()
            finally:
                conn.close()
        except (ValueError, sqlcipher3.DatabaseError) as e:  # a wrong key, a torn page or not a container at all
            # but busy, locked or an I/O error of the scratch file is no verdict on the batch (a "skip" offered for it would lose records);
            # SQLITE_ERROR (1) is the one Operational error that is one: a container with no table c. An error code unknown here is passed on
            if isinstance(e, sqlcipher3.OperationalError) and getattr(e, 'sqlite_errorcode', None) != 1:
                raise
            return None
    return row and row[0]


def unseal(data, cipher, key, aad):
    """The lines sealed by `seal`; another key, another place or a damaged file is one error, with nothing of the key in it."""
    need(cipher)
    body = (_gcm_body if cipher == 'cryptography' else _db_body)(data, key, aad)
    if body is None:
        raise ValueError(t('seal_broken'))
    return gzip.decompress(body).decode('utf-8').split('\n')
