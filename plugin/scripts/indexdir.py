"""Where the index is on every run of cg.py: in the folder `indexDir` of the home settings names, else in the home folder itself. While that
folder has no index the mod works with the home folder and says so. It makes no folder and no index there (only the move command does)."""
import collections
import os
import shutil
import crypto
import memdb
from texts import t

MIN_FREE = 2 * 2 ** 30  # a new index in the home folder (C:) only while that much is free there
MARK = 'index.db.fallback'  # in the home folder: the index beside it stands in for indexDir, and the file holds that folder
# db: the index to open; configured: indexDir, None when it is not set or is the home folder; fallback: the home index stands in for it;
# merge_from: the home index to merge into db, None when there is none
Where = collections.namedtuple('Where', 'db configured fallback merge_from')


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
    notes = [n for n in (notice(where), old and t('index_old_copy', dir=old), failed and t('index_merge_failed', e=failed)) if n]
    return {'configured': where and where.configured, 'fallback': bool(where and where.fallback), 'notes': notes}
