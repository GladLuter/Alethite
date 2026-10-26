"""The encryption key of the index and of the shared folder: kept in the mod's settings file under Windows DPAPI (bound to the Windows
user), restorable from a recovery code. The key, the code and the unprotected bytes never go into a message, an exception or a log."""
import base64
import ctypes
import hashlib
import hmac
import json
import os
import re

from texts import t

KEY_FIELD = 'encryptionKey'
ENTROPY = b'Claude-CacheGuard'  # another program of the same user does not open the blob by accident
PREFIX = 'dpapi:'


def settings_path(db):
    """The mod's settings file lies beside the database, as cg.py reads it."""
    return os.path.join(os.path.dirname(db), 'settings.json')


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
        raise ValueError(t('settings_broken', path=settings))
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


def save_key(settings, key):
    """Puts the key into the settings file (None takes it out), the other fields as they were."""
    s = _read(settings)
    if key is None:
        s.pop(KEY_FIELD, None)
    else:
        s[KEY_FIELD] = PREFIX + base64.b64encode(protect(key)).decode()
    tmp = settings + '.cg-tmp'
    with open(tmp, 'wb') as f:
        f.write(json.dumps(s, ensure_ascii=False, indent=2).encode('utf-8'))
    os.replace(tmp, settings)
