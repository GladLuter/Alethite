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


EXACT = ('tool', 'error')  # a command and an error message are read as they are: one squeezed word changes what they say


def _squeeze(items, field, level):
    """`items` with `field` compressed at `level` (stage 3): what the model reads of the memory. A tool call is a command, an error is the
    trace it is found by: as they are."""
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
    return sync.sync(conn, a.sync_dir, a.machine, a.model, max(1, a.max_seconds - (time.monotonic() - t0)), a.memory_dir, a.project_cwd)


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
    s.add_argument('--min-score', type=float, default=0.6)
    s.add_argument('--limit', type=int, default=2)
    s.add_argument('--timeout', type=float, default=5)  # the owner waits for this one: a cold Ollama is skipped, not waited for
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
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')  # the progress line of embed
    sys.stdin.reconfigure(encoding='utf-8')  # the text cg.py compress reads
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
