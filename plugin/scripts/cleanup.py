"""A chat removed from the disk keeps its numbers in the index: the token audit's chains (no conversation text) and the two reads the handoff
savings take from a transcript. `removed` is written right before the removal; stats and savings read it back."""
import contextlib
import datetime
import json
import os
import shutil
import time
from collections import Counter

import cc_token_audit
import savings
import stats
from transcripts import last_usage, lp


def snapshot(conn, projects, path):
    """Write (or rewrite) the row of the main transcript `path` (absolute) with its subagents' chains."""
    root = lp(projects)
    conv = os.path.basename(path)[:-6]
    chains = []
    subs = os.path.join(path[:-6], 'subagents')
    for p in [path] + [os.path.join(subs, n) for n in sorted(os.listdir(lp(subs)) if os.path.isdir(lp(subs)) else []) if n.endswith('.jsonl')]:
        mem, ours = [], set()
        ev, ch = cc_token_audit.read_chain(p, Counter(), lambda q, o: mem.extend(stats._mem_event(q, o, ours)))
        chains.append({'rel': os.path.relpath(lp(p), root).replace(os.sep, '/'), 'kind': 'main' if p == path else 'subagent',
                       'mtime': os.path.getmtime(lp(p)), 'ev': ev, 'ch': dict(ch), 'mem': mem})
    row = conn.execute('SELECT chains FROM removed WHERE conv = ?', (conv,)).fetchone()
    # a pass that failed half-way left files gone: their chains are only in the old row
    chains += [c for c in json.loads(row[0]) if not os.path.exists(lp(os.path.join(projects, c['rel'])))] if row else []
    conn.execute('INSERT OR REPLACE INTO removed VALUES(?,?,?,?,?,?)',
                 (conv, chains[0]['rel'], datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
                  json.dumps(last_usage(path)), json.dumps(savings.read_new(path)), json.dumps(chains)))
    conn.commit()


def saved_chains(conn):
    return [c for (s,) in conn.execute('SELECT chains FROM removed ORDER BY conv') for c in json.loads(s)]


def gone(conn):
    return {conv: {'last_usage': json.loads(u), 'read_new': json.loads(n)} for conv, u, n in conn.execute('SELECT conv, last_usage, read_new FROM removed')}


def _old(path):
    try:
        return savings.old_of(path)
    except OSError:  # removed or locked meanwhile: not a handoff for now
        return None


def candidates(conn, projects):
    """Main transcripts on disk that a handoff names as its old chat: the transcripts that are on the disk now and the removed rows
    (a removed chat names its old one too). The current chat is none of them: nothing hands off from it yet."""
    paths = savings._paths(projects)
    olds = {o for p in paths.values() if (o := _old(p))}
    olds |= {r['old'] for (s,) in conn.execute('SELECT read_new FROM removed') if (r := json.loads(s or 'null'))}
    return [paths[c] for c in sorted(olds) if c in paths]


def _files(path):
    """Every file of the chat: its transcript and what lies in its folder (subagents)."""
    return [lp(path)] + [os.path.join(d, n) for d, _, names in os.walk(lp(path[:-6])) for n in names]


def _mb(path):
    return sum(os.path.getsize(p) for p in _files(path)) / 2 ** 20


def _ready(conn, path, now):
    """The chat is read into the index to its last byte (the transcript and every .jsonl of its folder) and untouched for COLD_AFTER."""
    files = _files(path)
    if any(now - os.path.getmtime(p) < savings.COLD_AFTER for p in files):
        return False
    for p in (p for p in files if p.endswith('.jsonl')):
        row = conn.execute('SELECT size, offset FROM files WHERE path = ?', (p,)).fetchone()
        if not row or not row[0] == row[1] == os.path.getsize(p):
            return False
    return True


def run(conn, projects, synced, handoffs_dir=None, now=time.time):
    """Remove the handed-off chats that are indexed, `synced` (the caller says whether the share holds all of ours) and cold: the numbers
    first (snapshot), then the transcript, its folder and, in `handoffs_dir`, its handoff text. What cannot be removed is in 'errors' and tried again."""
    out = {'removed': 0, 'mb': 0.0, 'waiting': 0, 'errors': []}
    t = now()
    for path in candidates(conn, projects):
        try:
            if not (synced and _ready(conn, path, t)):
                out['waiting'] += 1
                continue
            mb = _mb(path)
            snapshot(conn, projects, path)
            if os.path.isdir(lp(path[:-6])):
                shutil.rmtree(lp(path[:-6]))  # first: a folder that fails to go keeps the transcript, so the next pass is a candidate again
            os.remove(lp(path))
            if handoffs_dir:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(os.path.join(handoffs_dir, os.path.basename(path)[:-6] + '.md'))
            out['removed'], out['mb'] = out['removed'] + 1, out['mb'] + mb
        except OSError as e:
            out['errors'].append({'path': path, 'error': '%s: %s' % (type(e).__name__, e)})
    return out


def preview(conn, projects):
    """What `run` would remove without the conditions of the sync and the cold hour: {chats, mb}."""
    found = candidates(conn, projects)
    return {'chats': len(found), 'mb': sum(_mb(p) for p in found)}
