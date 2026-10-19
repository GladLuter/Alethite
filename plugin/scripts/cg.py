"""Command line of Claude-CacheGuard: every command prints one JSON object in UTF-8; an error is {"error": "<Type>: <text>"} with exit code 1."""
import argparse
import datetime
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memdb
import stats
import sync
import texts
import vectors
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
    upd = memdb.update(conn, a.projects)  # seconds when nothing is new
    hits, vector = vectors.hybrid_search(conn, a.query, None if a.all else project_key(a.cwd), a.limit, model=a.model)
    return {'hits': hits, 'errors': upd['errors'], 'vector': vector}


def _expand(a):
    return {'rows': memdb.expand(memdb.connect(a.db), a.conv, a.pos)}


def _embed(a):
    pieces, t0 = [0], time.monotonic()

    def embed(batch, model):  # one progress line per call, for a run from a terminal
        out = vectors.ollama_embed(batch, model)
        pieces[0] += len(batch)
        print(texts.t('embed_progress', n=pieces[0], s=int(time.monotonic() - t0)), file=sys.stderr, flush=True)
        return out

    return vectors.embed_pending(memdb.connect(a.db), a.model, embed, a.max_seconds)


def _last_turn(a):
    main, _ = find_session(a.projects, _session(a.session))
    return (main and last_usage(main)) or {'tokens': None, 'at': None, 'model': None}


def _handoff(a):
    text = build_handoff(a.projects, _session(a.session), a.today, a.memory_dir)
    folder = os.path.join(os.path.dirname(a.db), 'handoffs')  # next to the index: ~/.claude/cacheguard/handoffs by default
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, a.session + '.md')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:  # written before the answer: the owner's insurance if clear/submit fails
        f.write(text)
    return {'text': text, 'path': path}


def _sync(a):
    conn = memdb.connect(a.db)
    if a.skip_batch:
        machine, n = a.skip_batch
        sync.skip_batch(conn, machine, int(n) if n.isdigit() else -1)
    return sync.sync(conn, a.sync_dir, a.machine, a.model, a.max_seconds, a.memory_dir, a.project_cwd)


def _forget(a):
    return {'forgotten': memdb.forget(memdb.connect(a.db), a.conv, a.pos)}


def _day(s):  # argparse reports a ValueError from a type function as a bad argument
    return datetime.date.fromisoformat(s).isoformat()


def _stats(a):
    errors = []
    m = stats.metrics(a.projects, memdb.connect(a.db), a.since, a.until, errors)
    return {'metrics': m, 'note': stats.note_markdown(m, a.host, a.today, json.loads(a.limits), a.rule_chars), 'errors': errors}


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
    s.add_argument('--limit', type=int, default=8)
    s.add_argument('--model', default=vectors.MODEL)
    scope = s.add_mutually_exclusive_group()
    scope.add_argument('--cwd', default=os.getcwd())
    scope.add_argument('--all', action='store_true')
    s = add('expand', _expand)
    s.add_argument('--conv', required=True)
    s.add_argument('--pos', type=int, required=True)
    s = add('embed', _embed)
    s.add_argument('--model', default=vectors.MODEL)
    s.add_argument('--max-seconds', type=float, default=120)
    add('last-turn', _last_turn).add_argument('--session', required=True)
    s = add('handoff', _handoff)
    s.add_argument('--session', required=True)
    s.add_argument('--today', required=True)
    s.add_argument('--memory-dir')
    s = add('sync', _sync)
    s.add_argument('--sync-dir', required=True)
    s.add_argument('--machine')
    s.add_argument('--model', default=vectors.MODEL)  # the model this computer embeds with: vectors of another are not taken in
    s.add_argument('--max-seconds', type=float, default=60)
    s.add_argument('--memory-dir')  # the auto-memory folder of the current project, and its root: the short memory is synced for it
    s.add_argument('--project-cwd')
    s.add_argument('--skip-batch', nargs=2, metavar=('MACHINE', 'N'))
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
    return p


def main(argv):
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')  # the progress line of embed
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
