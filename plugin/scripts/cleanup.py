"""A chat removed from the disk keeps its numbers in the index: the token audit's chains (no conversation text) and the two reads the handoff
savings take from a transcript. `removed` is written right before the removal; stats and savings read it back."""
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


def _ids(c):
    return {e[1]['id'] for e in c['ev'] if e[0] == 'c'}


def snapshot(conn, projects, path):
    """Write (or rewrite) the row of the main transcript `path` (absolute) with the chains of every .jsonl of its folder (nested workflow agents too)."""
    root = lp(projects)
    conv = os.path.basename(path)[:-6]
    chains, agents = [], {}
    for p in (q for q in _files(path) if q.endswith('.jsonl')):  # the same list the gate checks, the main transcript first
        mem, ours = [], set()
        ev, ch = cc_token_audit.read_chain(p, Counter(), lambda q, o: mem.extend(stats._mem_event(q, o, ours)))
        chains.append({'rel': os.path.relpath(p, root).replace(os.sep, '/'), 'mtime': os.path.getmtime(p), 'ev': ev, 'ch': dict(ch), 'mem': mem})
        if p != lp(path) and (a := savings.agent_start(p)):  # a subagent's start: the lean-agent savings outlive the file
            agents[chains[-1]['rel']] = a
    row = conn.execute('SELECT chains, read_new, agents FROM removed WHERE conv = ?', (conv,)).fetchone()
    read_new = savings.read_new(path)
    if row:
        fresh = set().union(*map(_ids, chains))
        for c in json.loads(row[0]):
            if not os.path.exists(lp(os.path.join(projects, c['rel']))):  # a pass that failed half-way left files gone: their chains are only in the old row
                chains.append(c)
            elif not _ids(c) <= fresh:  # the transcript came back holding only new lines: the old chain stays under a name no file has
                chains.append(dict(c, rel='%s#%d' % (c['rel'], c['mtime'])))
        read_new = read_new or json.loads(row[1] or 'null')
        old = _load(row[2] or 'null')
        for rel, a in (old.items() if isinstance(old, dict) else ()):
            if not os.path.exists(lp(os.path.join(projects, rel))):  # the same half-failed pass: a file gone keeps its start
                agents.setdefault(rel, a)
    conn.execute('INSERT OR REPLACE INTO removed(conv, path, at, last_usage, read_new, chains, agents) VALUES(?,?,?,?,?,?,?)',
                 (conv, chains[0]['rel'], datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
                  json.dumps(last_usage(path)), json.dumps(read_new), json.dumps(chains), json.dumps(agents)))
    conn.commit()


def _load(s):
    try:
        return json.loads(s)
    except ValueError:  # a damaged row is skipped, the other chats still count
        return None


def saved_chains(conn):
    return [c for (s,) in conn.execute('SELECT chains FROM removed ORDER BY conv') for c in _load(s) or ()]


def gone(conn):
    """{conv: {last_usage, read_new, agents}} of the removed chats; a NULL or damaged agents column reads as none."""
    out = {}
    for conv, u, n, a in conn.execute('SELECT conv, last_usage, read_new, agents FROM removed'):
        a = _load(a or 'null')
        out[conv] = {'last_usage': _load(u), 'read_new': _load(n), 'agents': list(a.values()) if isinstance(a, dict) else []}
    return out


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
    olds |= {r['old'] for (s,) in conn.execute('SELECT read_new FROM removed') if (r := _load(s or 'null'))}
    return [paths[c] for c in sorted(olds) if c in paths]


def _files(path):
    """Every file of the chat: its transcript and what lies in its folder (subagents)."""
    folder = lp(path[:-6])
    # an unlistable subfolder raises: the chat is reported and waits, it is not taken for emptier than it is
    return [lp(path)] + ([os.path.join(d, n) for d, _, names in os.walk(folder, onerror=_raise) for n in names] if os.path.isdir(folder) else [])


def _raise(e):
    raise e


def _gone_rows(conn, projects):
    """(conv, path) of the removed rows whose transcript is really gone: snapshot writes the row before the removal."""
    return [(c, p) for c, p in conn.execute('SELECT conv, path FROM removed ORDER BY conv') if not os.path.exists(lp(os.path.join(projects, p)))]


def removed_total(conn, projects):
    return len(_gone_rows(conn, projects))


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
            out['removed'], out['mb'] = out['removed'] + 1, out['mb'] + mb
        except Exception as e:  # one chat must not stop the pass: an odd transcript or a busy index keeps it for the next one
            out['errors'].append({'path': path, 'error': '%s: %s' % (type(e).__name__, e)})
    for conv, _ in _gone_rows(conn, projects) if handoffs_dir else []:  # also the retry of a handoff text that could not be removed with its chat
        md = os.path.join(handoffs_dir, conv + '.md')
        try:
            os.remove(md)
        except FileNotFoundError:
            pass
        except OSError as e:
            out['errors'].append({'path': md, 'error': '%s: %s' % (type(e).__name__, e)})
    return out


def preview(conn, projects):
    """What `run` would remove without the conditions of the sync and the cold hour: {chats, mb}."""
    found = candidates(conn, projects)
    return {'chats': len(found), 'mb': sum(_mb(p) for p in found)}
