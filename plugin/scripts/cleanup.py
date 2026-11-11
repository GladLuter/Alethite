"""A chat removed from the disk keeps its numbers in the index: the token audit's chains (no conversation text) and the two reads the handoff
savings take from a transcript. `removed` is written right before the removal; stats and savings read it back."""
import datetime
import json
import os
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
    conn.execute('INSERT OR REPLACE INTO removed VALUES(?,?,?,?,?,?)',
                 (conv, chains[0]['rel'], datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
                  json.dumps(last_usage(path)), json.dumps(savings.read_new(path)), json.dumps(chains)))
    conn.commit()


def saved_chains(conn):
    return [c for (s,) in conn.execute('SELECT chains FROM removed ORDER BY conv') for c in json.loads(s)]


def gone(conn):
    return {conv: {'last_usage': json.loads(u), 'read_new': json.loads(n)} for conv, u, n in conn.execute('SELECT conv, last_usage, read_new FROM removed')}
