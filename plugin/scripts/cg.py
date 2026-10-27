"""Command line of Claude-CacheGuard: every command prints one JSON object in UTF-8; an error is {"error": "<Type>: <text>"} with exit code 1."""
import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crypto
import dialogs
import encrypt
import job
import lessons
import memdb
import stats
import sync
import texts
import vectors
from compress import LEVELS, compress
from handoff import build_handoff
from transcripts import find_session, last_usage, project_key


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
    conn = memdb.connect(a.db)
    out = memdb.status(conn)
    out['sync'] = sync.status(conn, a.machine)
    out['encryption'] = encrypt.status(a.db)
    out['job'] = json.loads(memdb.meta(conn, 'job_last') or 'null')  # the background job's last run (stage 4), for /cg-health
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


def _alone(a, name, wait):
    """One `name` run per index at a time, whichever chat or terminal started it: an exclusive transaction on a lock file beside the
    index, which the OS drops with the process. Hold the result while the run lasts; sqlite3.OperationalError after `wait` seconds."""
    lock = sqlite3.connect('%s.%s.lock' % (a.db, name), timeout=wait, isolation_level=None)
    lock.execute('BEGIN EXCLUSIVE')
    return lock


def _embed(a):
    conn = memdb.connect(a.db)  # first: it also makes the folder the lock file lies in
    try:
        lock = _alone(a, 'embed', 0)  # noqa: F841 (held until the return)
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
        with open(path, encoding='utf-8-sig') as f:  # Windows editors save it with a BOM
            s = json.load(f)
    except FileNotFoundError:
        return {}, None
    except (ValueError, OSError) as e:  # a broken file, or one that an editor holds or the owner may not read
        return {}, '%s: %s' % (path, e)
    return (s, None) if isinstance(s, dict) else ({}, path)


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
    """The key beside the index, None when there is none. A settings file that cannot be read shows none, as it does to memdb.connect: the
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


def maintain(a, embed_fn=vectors.ollama_embed, battery=job.on_battery, digest=sync.ollama_digest):
    """One run of the background job: the index, then the vectors (on mains power, while no chat embeds), then the sync (when a folder
    is set). Each step under the lock a chat takes for it, so a chat and the job never do one thing twice at a time; a step that fails
    does not stop the next. The result is kept in meta.job_last for /cg-health. The short memory waits for a chat: only a chat knows its
    project's auto-memory folder."""
    try:
        conn = memdb.connect(a.db)  # first: it also makes the folder the lock files lie in
    except ValueError as e:  # an encrypted index and no key that opens it: nowhere to keep the run, /cg-health tells the same from `status`
        return {'errors': [{'where': 'index', 'error': '%s: %s' % (type(e).__name__, e)}]}
    try:
        lock = _alone(a, 'maintain', 0)  # noqa: F841 (held until the return)
    except sqlite3.OperationalError as e:
        if 'locked' not in str(e):
            raise
        return {'busy': True}
    s, broken = _settings(a.settings or os.path.join(os.path.dirname(a.db), 'settings.json'))
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
            held = _alone(a, 'embed', 0)
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
            held = _alone(a, 'sync', a.sync_seconds)
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


def _maintain(a):
    return maintain(a)


def _job(a):
    folder = os.path.dirname(a.db)
    if a.action == 'install':
        return job.install(folder, a.minutes)
    if a.action == 'remove':
        return job.remove()
    return {'supported': job.is_supported(), 'minutes': job.installed(), 'last': json.loads(memdb.meta(memdb.connect(a.db), 'job_last') or 'null')}


def _last_turn(a):
    main, _ = find_session(a.projects, _session(a.session))
    return (main and last_usage(main)) or {'tokens': None, 'at': None, 'model': None}


def _handoff(a):
    text = build_handoff(a.projects, _session(a.session), a.today, a.memory_dir, a.compress)
    folder = os.path.join(os.path.dirname(a.db), 'handoffs')  # next to the index: ~/.claude/cacheguard/handoffs by default
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, a.session + '.md')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:  # written before the answer: the owner's insurance if clear/submit fails
        f.write(text)
    return {'text': text, 'path': path}


def _sync(a):
    # a second sync waits its turn within its own time and then takes in only what is still new
    t0 = time.monotonic()
    conn = memdb.connect(a.db)  # first: it also makes the folder the lock file lies in
    lock = _alone(a, 'sync', a.max_seconds)  # noqa: F841 (held until the return)
    if a.skip_batch:
        machine, n = a.skip_batch
        sync.skip_batch(conn, machine, int(n) if n.isdigit() else -1)
    cipher = _cipher(_settings(crypto.settings_path(a.db))[0], a.share_cipher)
    return sync.sync(conn, a.sync_dir, a.machine, a.model, max(1, a.max_seconds - (time.monotonic() - t0)), _memory_dir(a.memory_dir), a.project_cwd,
                     key=_key(a), cipher=cipher)


ENCRYPT_WAITS = {'maintain': 300, 'embed': 10, 'sync': 60}  # how long encrypt waits for each lock of the background job, in the job's order


def _encrypt(a):
    """/cg-encrypt: on, join, off (windows ask the owner; the code is shown there and nowhere else) and status. No connection to the index
    is held meanwhile: Windows cannot replace a file that is open. The sync folder and the cipher come from the settings file unless given."""
    if a.action == 'status':
        return encrypt.status(a.db)  # from the files alone: no lock, no connection
    s, _ = _settings(crypto.settings_path(a.db))
    sync_dir = _sync_dir(s) if a.sync_dir is None else a.sync_dir
    kw = dict(model=a.model, memory_dir=_memory_dir(a.memory_dir), project_cwd=a.project_cwd)
    os.makedirs(os.path.dirname(a.db), exist_ok=True)  # the lock files lie there; memdb.connect would open the index (and refuse an encrypted one)
    locks = []
    try:
        for name, wait in ENCRYPT_WAITS.items():
            locks.append(_alone(a, name, wait))
        ui = dialogs.WindowsUI()
        if a.action == 'off':
            return encrypt.off(a.db, sync_dir, a.machine, ui, **kw)
        return getattr(encrypt, a.action)(a.db, sync_dir, _cipher(s, a.share_cipher), a.machine, ui, **kw)
    except memdb.db_errors() as e:  # a locked lock file, or an index another process holds: nothing was lost
        if 'locked' not in str(e):
            raise
        raise ValueError(texts.t('encrypt_busy')) from None
    finally:
        for lock in locks:
            lock.close()


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
    errors = []
    m = stats.metrics(a.projects, memdb.connect(a.db), a.since, a.until, errors)
    return {'metrics': m, 'note': stats.note_markdown(m, a.host, a.today, json.loads(a.limits), a.rule_chars, a.memory_tokens, a.compression),
            'errors': errors}


def _parser():
    p = _Parser(prog='cg.py')
    p.add_argument('--db', default=memdb.DB_PATH)
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
    s = add('maintain', _maintain)
    s.add_argument('--settings')  # the mod's settings file; beside the index by default
    s.add_argument('--embed-seconds', type=float, default=300)
    s.add_argument('--sync-seconds', type=float, default=60)
    s = add('job', _job)
    s.add_argument('action', choices=('install', 'remove', 'status'))
    s.add_argument('--minutes', type=int, default=job.MINUTES)
    s = add('forget', _forget)
    s.add_argument('--conv', required=True)
    s.add_argument('--pos', type=int, required=True)
    s = add('stats', _stats)
    s.add_argument('--since', type=_day, required=True)
    s.add_argument('--until', type=_day)
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
    try:
        a = _parser().parse_args(argv)
        texts.lang = a.lang
        out, code = a.fn(a), 0
    except _Help as h:
        out, code = {'help': str(h)}, 0
    except Exception as e:
        out, code = {'error': '%s: %s' % (type(e).__name__, e)}, 1
    print(json.dumps(out, ensure_ascii=False))
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
