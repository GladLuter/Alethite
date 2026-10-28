"""Command line of Claude-CacheGuard: every command prints one JSON object in UTF-8; an error is {"error": "<Type>: <text>"} with exit code 1."""
import argparse
import contextlib
import datetime
import json
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crypto
import indexdir
import lessons
import memdb
import texts
import vectors
from compress import LEVELS, compress
from transcripts import find_session, last_usage, project_key


INDEX_FREE = ('last-turn', 'compress', 'handoff')  # the commands that read no index: no merge waits for them, and they still work when the index cannot be used


class _Help(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # a bad command line is a JSON error too, not usage text on stderr
        raise ValueError(message)

    def print_help(self, file=None):  # and --help is a JSON answer: {"help": "<usage>"}
        raise _Help(self.format_help())


def _session(s):
    """A session id goes into file paths: only letters, digits, - and _."""
    if not re.fullmatch(r'[\w-]+', s):
        raise ValueError(texts.t('bad_session', id=s))
    return s


def _index(a):
    return memdb.update(memdb.connect(a.db), a.projects, a.rebuild)


def _status(a):
    import encrypt, sync
    conn = memdb.connect(a.db)
    out = memdb.status(conn)
    out['sync'] = sync.status(conn, a.machine)
    out['encryption'] = encrypt.status(a.db)
    note = {'pending': 'note_share_pending', 'old': 'note_old_share_kept'}.get(memdb.meta(conn, 'share_unfinished'))  # set by `encrypt`, in the index
    if note:
        out['encryption']['notes'].append(texts.t(note))
    out['job'] = json.loads(memdb.meta(conn, 'job_last') or 'null')  # the background job's last run (stage 4), for /cg-health
    out['index_dir'] = indexdir.status(conn, a.where)
    if a.cwd:  # the scope of «в этом проекте»: a key that matches no conversation makes the project search find nothing
        key = project_key(a.cwd)
        out['project'] = {'key': key, 'convs': conn.execute('SELECT count(*) FROM convs WHERE ' + memdb.IN_PROJECT, {'p': key}).fetchone()[0]}
    return out


def _recall(a):
    conn = memdb.connect(a.db)
    try:
        upd = memdb.update(conn, a.projects)  # seconds when nothing is new
    except Exception as e:  # e.g. the index stays locked by another run: what it already holds is still searched
        upd = {'errors': [memdb._error(a.projects, e)]}
    hits, vector = vectors.hybrid_search(conn, a.query, None if a.all else project_key(a.cwd), a.limit, model=a.model, also=a.also)
    return {'hits': _squeeze(hits, 'snippet', a.compress), 'errors': upd['errors'], 'vector': vector}


EXACT = ('tool', 'error', 'lesson')  # a command, an error message and a lesson are read as they are: one squeezed word changes what they say


def _squeeze(items, field, level):
    """`items` with `field` compressed at `level` (stage 3): what the model reads of the memory. A tool call is a command, an error is the
    trace it is found by, a lesson its own words: as they are."""
    return [i if i.get('kind') in EXACT else dict(i, **{field: compress(i[field], level)}) for i in items]


def _expand(a):
    return {'rows': _squeeze(memdb.expand(memdb.connect(a.db), a.conv, a.pos), 'text', a.compress)}


def _compress(a):  # the owner's preview of a level: cg.py compress --level ultra --file text.txt (or the text on stdin)
    if a.file:
        with open(a.file, encoding='utf-8-sig') as f:  # Windows PowerShell 5.1 spoils non-ASCII it pipes into a program: a file is safer
            text = f.read()
    else:
        text = sys.stdin.read()
    out = compress(text, a.level)
    return {'text': out, 'chars_in': len(text), 'chars_out': len(out)}


def _embed(a):
    conn = memdb.connect(a.db)  # first: it also makes the folder the lock file lies in
    try:
        lock = memdb.lock(a.db, 'embed', 0)  # noqa: F841 (held until the return)
    except sqlite3.OperationalError as e:  # another chat embeds the same records with the same one Ollama: nothing to add to it
        if 'locked' not in str(e):
            raise
        return {'done': 0, 'pending': None, 'model': a.model, 'seconds': 0, 'errors': [], 'busy': True}
    pieces, t0 = [0], time.monotonic()

    def embed(batch, model):  # one progress line per call, for a run from a terminal
        out = vectors.ollama_embed(batch, model)
        pieces[0] += len(batch)
        print(texts.t('embed_progress', n=pieces[0], s=int(time.monotonic() - t0)), file=sys.stderr, flush=True)
        return out

    return vectors.embed_pending(conn, a.model, embed, a.max_seconds)


# ---- the background job (stage 4): what the scheduled task runs, and the task itself

def _settings(path):
    """The mod's own settings file (%USERPROFILE%\\.claude\\cacheguard\\settings.json): the job runs with what the mod runs with.
    (settings, None), or ({}, why) when the file is broken: the job goes on with the defaults and says so."""
    try:
        return crypto._read(path), None
    except crypto.SettingsBroken as e:  # its message names the path already
        return {}, str(e)
    except (ValueError, OSError) as e:  # a file that cannot be decoded, or one that an editor holds or the owner may not read
        return {}, '%s: %s' % (path, e)


def _sync_dir(s):
    """The sync folder of the settings file; "Copy as path" puts quotes around it."""
    return s['syncDir'].strip().strip('"') if isinstance(s.get('syncDir'), str) else ''


def _cipher(s, flag=None):
    """How the sync folder is sealed: --share-cipher, else shareCipher of the settings file, else sqlcipher. A name no cipher has is an error
    that names the setting, not a KeyError of the cipher table."""
    c = flag or s.get('shareCipher')
    if c is None:
        return 'sqlcipher'
    if c not in crypto.CIPHERS:
        raise ValueError(texts.t('share_cipher_bad', value=c))
    return c


def _key(a):
    """The key in the settings file of the index (the home one for an index in its folder), None when there is none. A settings file that cannot be read shows none, as it does to memdb.connect: the
    index it opened is plain then, and a sync refuses an encrypted share (share_needs_join) rather than write open data into it. The same
    for a file that cannot be opened (an editor holds it, no right to read it): `_settings` lives with that too. Not for an encrypted index:
    it was opened with the key a moment ago, and a file that fails now must not turn its records into an open sync."""
    try:
        return crypto.load_key(crypto.settings_path(a.db))
    except (crypto.SettingsBroken, OSError):
        if crypto.is_encrypted(a.db):
            raise
        return None


def _memory_dir(path):
    """The auto-memory folder when it is there: a project without one has no short memory to sync, and that is no failed sync."""
    return path if path and os.path.isdir(path) else None


def maintain(a, embed_fn=vectors.ollama_embed, battery=None, digest=None):
    """One run of the background job: the index, then the vectors (on mains power, while no chat embeds), then the sync (when a folder
    is set). Each step under the lock a chat takes for it, so a chat and the job never do one thing twice at a time; a step that fails
    does not stop the next. The result is kept in meta.job_last for /cg-health. The short memory waits for a chat: only a chat knows its
    project's auto-memory folder."""
    import job, sync
    battery, digest = battery or job.on_battery, digest or sync.ollama_digest
    try:
        conn = memdb.connect(a.db)  # first: it also makes the folder the lock files lie in
    except ValueError as e:  # an encrypted index and no key that opens it: nowhere to keep the run, /cg-health tells the same from `status`
        return {'errors': [{'where': 'index', 'error': '%s: %s' % (type(e).__name__, e)}]}
    try:
        lock = memdb.lock(a.db, 'maintain', 0)  # noqa: F841 (held until the return)
    except sqlite3.OperationalError as e:
        if 'locked' not in str(e):
            raise
        return {'busy': True}
    s, broken = _settings(a.settings or crypto.settings_path(a.db))
    if isinstance(s.get('language'), str) and s['language'] in texts.TABLES:
        texts.lang = s['language']
    model = s.get('embedModel') if isinstance(s.get('embedModel'), str) and s['embedModel'].strip() else vectors.MODEL
    sync_dir = _sync_dir(s)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    out = {'at': stamp, 'index': None, 'embed': None, 'sync': None, 'errors': []}
    fault = lambda where, e: out['errors'].append({'where': where, 'error': '%s: %s' % (type(e).__name__, e)})
    # a run cut off (the task's 30-minute limit) never gets to the end: /cg-health must not show the run before it as all well
    conn.execute("INSERT OR REPLACE INTO meta VALUES('job_last', ?)", (json.dumps(dict(out, errors=[{'where': 'job', 'error': texts.t('job_unfinished')}]), ensure_ascii=False),))
    if broken:
        out['errors'].append({'where': 'settings', 'error': broken})
    try:
        u = memdb.update(conn, a.projects)
        out['index'] = {'rows': u['rows'], 'errors': len(u['errors'])}
    except Exception as e:
        fault('index', e)
    if battery():
        out['embed'] = {'skipped': 'battery'}
    else:
        try:
            held = memdb.lock(a.db, 'embed', 0)
        except sqlite3.OperationalError as e:
            if 'locked' in str(e):
                out['embed'] = {'skipped': 'busy'}  # a chat embeds right now
            else:
                fault('embed', e)
        else:
            try:
                r = vectors.embed_pending(conn, model, embed_fn, a.embed_seconds)
                out['embed'] = {'done': r['done'], 'pending': r['pending'], 'errors': len(r['errors'])}
                if r['errors']:
                    out['errors'].append({'where': 'embed', 'error': r['errors'][0]['error']})
            except Exception as e:
                fault('embed', e)
            finally:
                held.close()
    if sync_dir:
        try:
            key, cipher = _key(a), _cipher(s)  # a key that cannot be read or a cipher that is none fails the sync step only
            held = memdb.lock(a.db, 'sync', a.sync_seconds)
            try:
                r = sync.sync(conn, sync_dir, None, model, a.sync_seconds, digest=digest, key=key, cipher=cipher)
            finally:
                held.close()
            out['sync'] = {'pushed': r['pushed'], 'pulled': r['pulled'], 'errors': len(r['errors'])}
            if r['errors']:
                out['errors'].append({'where': 'sync', 'error': r['errors'][0]['error']})
        except Exception as e:
            fault('sync', e)
    conn.execute("INSERT OR REPLACE INTO meta VALUES('job_last', ?)", (json.dumps(out, ensure_ascii=False),))
    return out


def _job(a):
    import job
    folder = os.path.dirname(a.db)
    if a.action == 'install':
        return job.install(folder, job.MINUTES if a.minutes is None else a.minutes)
    if a.action == 'remove':
        return job.remove()
    return {'supported': job.is_supported(), 'minutes': job.installed(), 'last': json.loads(memdb.meta(memdb.connect(a.db), 'job_last') or 'null')}


def _last_turn(a):
    main, _ = find_session(a.projects, _session(a.session))
    return (main and last_usage(main)) or {'tokens': None, 'at': None, 'model': None}


def _handoff(a):
    from handoff import build_handoff
    text = build_handoff(a.projects, _session(a.session), a.today, a.memory_dir, a.compress)
    folder = os.path.join(os.path.dirname(a.db), 'handoffs')  # next to the index: ~/.claude/cacheguard/handoffs by default
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, a.session + '.md')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:  # written before the answer: the owner's insurance if clear/submit fails
        f.write(text)
    return {'text': text, 'path': path}


def _sync(a):
    import sync
    # a second sync waits its turn within its own time and then takes in only what is still new
    t0 = time.monotonic()
    conn = memdb.connect(a.db)  # first: it also makes the folder the lock file lies in
    lock = memdb.lock(a.db, 'sync', a.max_seconds)  # noqa: F841 (held until the return)
    if a.skip_batch:
        machine, n = a.skip_batch
        sync.skip_batch(conn, machine, int(n) if n.isdigit() else -1)
    cipher = _cipher(_settings(crypto.settings_path(a.db))[0], a.share_cipher)
    return sync.sync(conn, a.sync_dir, a.machine, a.model, max(1, a.max_seconds - (time.monotonic() - t0)), _memory_dir(a.memory_dir), a.project_cwd,
                     key=_key(a), cipher=cipher)


_WAITS = {'maintain': 120, 'embed': 10, 'sync': 60}  # how long encrypt and index-dir wait for each lock of the background job
ENCRYPT_WAITS = {name: _WAITS[name] for name in indexdir.LOCKS}  # the names and their order are indexdir's; a lock without a wait is a KeyError at import
# How long encrypt pushes to the sync folder. The mod gives the whole command 600 s at most (the host's cap on a process); the locks (190 s at
# worst) and this leave about 290 s for the owner in the windows. What a big push leaves undone ends as note_share_pending: a repeat or the job
# finishes it.
ENCRYPT_PUSH_SECONDS = 120


@contextlib.contextmanager
def _all_locks(a, busy):
    """The three locks of the background job on the index in `a.db`, held for the block (a list of them, for a block that gives them back
    early). A lock held by another process is the ValueError `busy`; any other database error is not."""
    os.makedirs(os.path.dirname(a.db), exist_ok=True)  # the lock files lie there; memdb.connect would open the index (and refuse an encrypted one)
    locks = []
    try:
        for name, wait in ENCRYPT_WAITS.items():
            locks.append(memdb.lock(a.db, name, wait))
        yield locks
    except memdb.db_errors() as e:  # a locked lock file, or an index another process holds: nothing was lost
        if 'locked' not in str(e):
            raise
        raise ValueError(texts.t(busy)) from None
    finally:
        for lock in locks:
            lock.close()


def _encrypt(a):
    """/cg-encrypt: on, join, off (windows ask the owner; the code is shown there and nowhere else) and status. No connection to the index
    is held meanwhile: Windows cannot replace a file that is open. The sync folder and the cipher come from the settings file unless given."""
    import dialogs, encrypt
    if a.action == 'status':
        return encrypt.status(a.db)  # from the files alone: no lock, no connection
    if a.where and (a.where.fallback or a.where.merge_from):  # it would turn one of two indexes (and `off` would take the key from the other)
        raise ValueError(texts.t('encrypt_index_away', dir=a.where.configured, home=os.path.dirname(a.where.merge_from or a.where.db)))
    s, _ = _settings(crypto.settings_path(a.db))
    sync_dir = _sync_dir(s) if a.sync_dir is None else a.sync_dir
    kw = dict(model=a.model, memory_dir=_memory_dir(a.memory_dir), project_cwd=a.project_cwd, seconds=ENCRYPT_PUSH_SECONDS)
    with _all_locks(a, 'encrypt_busy'):
        ui = dialogs.WindowsUI()
        if a.action == 'off':
            return encrypt.off(a.db, sync_dir, a.machine, ui, **kw)
        return getattr(encrypt, a.action)(a.db, sync_dir, _cipher(s, a.share_cipher), a.machine, ui, **kw)


def _index_dir(a):
    """/cg-index-dir: the index moves to the folder of --path, or of the window that asks when there is none. Alone like encrypt: the locks of
    the background job are held until `move` has switched over, and given back there (the old copy could not be deleted with them open)."""
    import dialogs
    ui = dialogs.WindowsUI()
    path = a.path or ui.pick_folder()  # before the locks: nobody waits while the owner looks for a folder
    if path is None:
        return {'done': False, 'notes': [texts.t('index_dir_cancelled')]}
    where = a.where or indexdir.Where(a.db, None, False, None)  # a run with --db: that index is the one that moves
    with _all_locks(a, 'index_dir_busy') as locks:
        out = indexdir.move(where, path, _key(a), ui, locks=locks)
        a.where = None  # `main` adds the notice of where the index was before the command: stale now
        return out


def _forget(a):
    return {'forgotten': memdb.forget(memdb.connect(a.db), a.conv, a.pos)}


# ---- lessons (stage 4): the model saves one with lesson_save; the mod lists them and asks which fit the owner's message

def _lesson_save(a):
    conn = memdb.connect(a.db)
    fields = {k: getattr(a, k) for k in lessons.FIELDS}
    out = lessons.save(conn, project_key(a.cwd), fields)
    out['vector'] = lessons.embed_one(conn, out['id'], a.model) if out['new'] else 'ok'
    return out


def _lessons(a):
    return {'lessons': lessons.listing(memdb.connect(a.db), None if a.all else project_key(a.cwd))}


def _lessons_match(a):
    found, state = lessons.match(memdb.connect(a.db), a.text, project_key(a.cwd), a.model, a.min_score, a.limit, timeout=a.timeout)
    return {'lessons': found, 'vector': state}


def _day(s):  # argparse reports a ValueError from a type function as a bad argument
    return datetime.date.fromisoformat(s).isoformat()


def _stats(a):
    import stats, sync
    errors = []
    m = stats.metrics(a.projects, memdb.connect(a.db), a.since, a.until, errors, a.machine or sync.machine_name())
    return {'metrics': m, 'note': stats.note_markdown(m, a.host, a.today, json.loads(a.limits), a.rule_chars, a.memory_tokens, a.compression),
            'errors': errors}


def _parser():
    p = _Parser(prog='cg.py')
    p.add_argument('--db')  # the index file; without it `main` asks indexdir.resolve (the home settings), with it they are not read at all
    p.set_defaults(where=None)  # the answer of resolve, once `main` has one
    p.add_argument('--projects', default=os.path.join(os.path.expanduser('~'), '.claude', 'projects'))
    p.add_argument('--lang', default='en')  # of the texts the owner and the model read: errors, the handoff, the note
    sub = p.add_subparsers(dest='cmd', required=True)

    def add(name, fn):
        s = sub.add_parser(name)
        s.set_defaults(fn=fn)
        return s

    add('index', _index).add_argument('--rebuild', action='store_true')
    s = add('status', _status)
    s.add_argument('--cwd')
    s.add_argument('--machine')  # the name of this computer's folder in the sync folder; COMPUTERNAME by default
    s = add('recall', _recall)
    s.add_argument('--query', required=True)
    s.add_argument('--also', action='append', default=[])  # other wordings of the same search (stage 4), one item each
    s.add_argument('--limit', type=int, default=8)
    s.add_argument('--model', default=vectors.MODEL)
    s.add_argument('--compress', choices=LEVELS, default='off')  # what the model reads of the memory (stage 3); the mod passes its setting
    scope = s.add_mutually_exclusive_group()
    scope.add_argument('--cwd', default=os.getcwd())
    scope.add_argument('--all', action='store_true')
    s = add('expand', _expand)
    s.add_argument('--conv', required=True)
    s.add_argument('--pos', type=int, required=True)
    s.add_argument('--compress', choices=LEVELS, default='off')
    s = add('compress', _compress)
    s.add_argument('--level', choices=LEVELS, default='ultra')
    s.add_argument('--file')
    s = add('embed', _embed)
    s.add_argument('--model', default=vectors.MODEL)
    s.add_argument('--max-seconds', type=float, default=120)
    add('last-turn', _last_turn).add_argument('--session', required=True)
    s = add('handoff', _handoff)
    s.add_argument('--session', required=True)
    s.add_argument('--today', required=True)
    s.add_argument('--memory-dir')
    s.add_argument('--compress', choices=LEVELS, default='off')
    s = add('sync', _sync)
    s.add_argument('--sync-dir', required=True)
    s.add_argument('--machine')
    s.add_argument('--model', default=vectors.MODEL)  # the model this computer embeds with: vectors of another are not taken in
    s.add_argument('--max-seconds', type=float, default=60)
    s.add_argument('--memory-dir')  # the auto-memory folder of the current project, and its root: the short memory is synced for it
    s.add_argument('--project-cwd')
    s.add_argument('--skip-batch', nargs=2, metavar=('MACHINE', 'N'))
    s.add_argument('--share-cipher', choices=crypto.CIPHERS)  # of the sync folder when the index has a key; the settings file's by default
    s = add('encrypt', _encrypt)
    s.add_argument('action', choices=('on', 'join', 'off', 'status'))
    s.add_argument('--sync-dir')  # the settings file's by default; '' leaves the sync folder alone
    s.add_argument('--share-cipher', choices=crypto.CIPHERS)
    s.add_argument('--machine')
    s.add_argument('--model', default=vectors.MODEL)
    s.add_argument('--memory-dir')
    s.add_argument('--project-cwd')
    add('index-dir', _index_dir).add_argument('--path')  # a window chooses the folder without it
    s = add('lesson-save', _lesson_save)
    s.add_argument('--cwd', default=os.getcwd())
    s.add_argument('--model', default=vectors.MODEL)
    for k in lessons.FIELDS:
        s.add_argument('--' + k, required=k in lessons.REQUIRED)
    s = add('lessons', _lessons)
    scope = s.add_mutually_exclusive_group()
    scope.add_argument('--cwd', default=os.getcwd())
    scope.add_argument('--all', action='store_true')
    s = add('lessons-match', _lessons_match)
    s.add_argument('--cwd', default=os.getcwd())
    s.add_argument('--text', required=True)
    s.add_argument('--model', default=vectors.MODEL)
    s.add_argument('--min-score', type=float, default=0.45)
    s.add_argument('--limit', type=int, default=2)
    s.add_argument('--timeout', type=float, default=5)  # the owner waits for this one: a cold Ollama is skipped, not waited for
    s = add('maintain', maintain)
    s.add_argument('--settings')  # the mod's settings file; the index's own by default (the home one for an index in its folder)
    s.add_argument('--embed-seconds', type=float, default=300)
    s.add_argument('--sync-seconds', type=float, default=60)
    s = add('job', _job)
    s.add_argument('action', choices=('install', 'remove', 'status'))
    s.add_argument('--minutes', type=int, default=None)  # job.MINUTES
    s = add('forget', _forget)
    s.add_argument('--conv', required=True)
    s.add_argument('--pos', type=int, required=True)
    s = add('stats', _stats)
    s.add_argument('--since', type=_day, required=True)
    s.add_argument('--until', type=_day)
    s.add_argument('--machine')  # as for sync, whose owner messages are counted. COMPUTERNAME by default
    s.add_argument('--host', required=True)
    s.add_argument('--today', type=_day, required=True)
    s.add_argument('--limits', required=True)  # JSON: [{"kind", "percentUsed"}, ...]
    s.add_argument('--rule-chars', type=int, required=True)
    s.add_argument('--memory-tokens', type=int, default=0)  # MEMORY.md in the context now, as the engine counts it
    s.add_argument('--compression', choices=LEVELS, default='off')
    return p


def main(argv):
    for stream in (sys.stdout, sys.stderr, sys.stdin):  # stderr: the progress line of embed; stdin: the text cg.py compress reads
        if stream is not None:  # pythonw, the background job's, has none of them
            stream.reconfigure(encoding='utf-8')
    problem = None  # why the index cannot be used: every answer says so, an error answer too
    try:
        a = _parser().parse_args(argv)
        texts.lang = a.lang
        if a.db is None:
            try:
                a.where = indexdir.resolve()
            except (ValueError, OSError) as e:
                problem = str(e)
                if a.cmd not in INDEX_FREE:
                    raise
                a.db = os.path.join(crypto.HOME_DIR, 'index.db')  # a place for files beside it (the handoff), the index itself is never opened
            else:
                a.db = a.where.db
                if a.where.configured:  # the settings of an index in that folder are still the home ones
                    crypto.INDEX_DIRS.add(crypto.norm(a.where.configured))
        # the folder is back and the home folder has a temporary index: it is merged first, and its locks are given back before the command
        # a plain pair never reads the key (memdb._open): a key that cannot be read must not stop it
        # a merge of a big index takes seconds: not in a run that needs no index, nor in the prompt hook's lessons-match, which has eight
        pending = a.cmd not in (*INDEX_FREE, 'lessons-match') and a.where and a.where.merge_from  # the path of the temporary index, or nothing
        enc = pending and (crypto.is_encrypted(a.where.db) or crypto.is_encrypted(a.where.merge_from))
        merged = pending and indexdir.merge_back(a.where, crypto.load_key(crypto.settings_path(a.db)) if enc else None)
        if pending and not os.path.exists(pending):  # merged and deleted just now: the command is not to see it as still to merge
            a.where = a.where._replace(merge_from=None)
        if a.where and (a.where.fallback or a.where.settings_error) and a.cmd not in INDEX_FREE:  # before anything is read from a transcript
            indexdir.apply_forgets(a.db)
        out, code = a.fn(a), 0
        if a.where and a.where.configured and not a.where.fallback and a.cmd not in INDEX_FREE:  # this run may have made forgets, or taken some in
            indexdir.keep_forgets(a.db)
        note = merged or indexdir.notice(a.where)
        if note and isinstance(out, dict):
            out['index_notice'] = note
    except _Help as h:
        out, code = {'help': str(h)}, 0
    except Exception as e:
        out, code = {'error': '%s: %s' % (type(e).__name__, e)}, 1
    if problem:
        out['index_problem'] = problem
    print(json.dumps(out, ensure_ascii=False))
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
