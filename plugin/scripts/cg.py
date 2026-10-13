"""Command line of Claude-CacheGuard: every command prints one JSON object in UTF-8; an error is {"error": "<Type>: <text>"} with exit code 1."""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memdb
from handoff import build_handoff
from transcripts import find_session, last_usage, project_key


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # a bad command line is a JSON error too, not usage text on stderr
        raise ValueError(message)


def _index(a):
    return memdb.update(memdb.connect(a.db), a.projects, a.rebuild)


def _status(a):
    return memdb.status(memdb.connect(a.db))


def _recall(a):
    conn = memdb.connect(a.db)
    upd = memdb.update(conn, a.projects)  # seconds when nothing is new
    return {'hits': memdb.search(conn, a.query, None if a.all else project_key(a.cwd), a.limit), 'errors': upd['errors']}


def _expand(a):
    return {'rows': memdb.expand(memdb.connect(a.db), a.conv, a.pos)}


def _last_turn(a):
    main, _ = find_session(a.projects, a.session)
    return (main and last_usage(main)) or {'tokens': None, 'at': None, 'model': None}


def _handoff(a):
    text = build_handoff(a.projects, a.session, a.today, a.memory_dir)
    folder = os.path.join(os.path.dirname(a.db), 'handoffs')  # next to the index: ~/.claude/cacheguard/handoffs by default
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, a.session + '.md')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:  # written before the answer: the owner's insurance if clear/submit fails
        f.write(text)
    return {'text': text, 'path': path}


def _parser():
    p = _Parser(prog='cg.py')
    p.add_argument('--db', default=memdb.DB_PATH)
    p.add_argument('--projects', default=os.path.join(os.path.expanduser('~'), '.claude', 'projects'))
    sub = p.add_subparsers(dest='cmd', required=True)  # stats (Task 11) is added here

    def add(name, fn):
        s = sub.add_parser(name)
        s.set_defaults(fn=fn)
        return s

    add('index', _index).add_argument('--rebuild', action='store_true')
    add('status', _status)
    s = add('recall', _recall)
    s.add_argument('--query', required=True)
    s.add_argument('--limit', type=int, default=8)
    scope = s.add_mutually_exclusive_group()
    scope.add_argument('--cwd', default=os.getcwd())
    scope.add_argument('--all', action='store_true')
    s = add('expand', _expand)
    s.add_argument('--conv', required=True)
    s.add_argument('--pos', type=int, required=True)
    add('last-turn', _last_turn).add_argument('--session', required=True)
    s = add('handoff', _handoff)
    s.add_argument('--session', required=True)
    s.add_argument('--today', required=True)
    s.add_argument('--memory-dir')
    return p


def main(argv):
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        a = _parser().parse_args(argv)
        out, code = a.fn(a), 0
    except Exception as e:
        out, code = {'error': '%s: %s' % (type(e).__name__, e)}, 1
    print(json.dumps(out, ensure_ascii=False))
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
