"""What the New-worker handoffs saved, from the transcripts alone: no index, no model. Numbers only; the sign is the saving (negative: dearer)."""
import calendar
import json
import os
import re
import statistics
import tempfile
import time

from cc_token_audit import minutes, price
from transcripts import HANDOFF_BLOCK, INJECTED, _USAGE, _blocks, _load, last_usage, lp

CACHE_VERSION = 3  # raise it when read_new, last_usage or agent_start return something else: old entries are then dropped
COLD_AFTER = 3600  # seconds: the cache of a chat that was silent this long is gone
WINDOW, SPARE, RESUME = 14 * 86400, 20, (300, 3600)  # reference window (s), spare records, a pause (s, exclusive) after which a call resumes a cold cache
_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
# shortcut: every cache write is priced as the 1h write (what Claude Code sends); a 5m write in the first call is split off when the usage names it


def _call(o):
    """(message id, model, usage) of a main-chat API call, else None."""
    m = o.get('message') if o.get('type') == 'assistant' and not o.get('isSidechain') else None
    u = m.get('usage') if isinstance(m, dict) and m.get('model') != '<synthetic>' else None
    return (m.get('id'), m.get('model') or '', u) if isinstance(u, dict) else None


def _num(u, k):
    return u.get(k) if isinstance(u.get(k), int) else 0


def _start(o):
    """What a parsed line says of the chat's start: None while it is not the owner's first text, () when that text holds no handoff,
    else (the old chat's id, the time)."""
    if o.get('type') != 'user' or o.get('isMeta'):
        return None
    texts = [b['text'] for b in _blocks(o.get('message', {}).get('content') if isinstance(o.get('message'), dict) else None)
             if b.get('type') == 'text' and isinstance(b.get('text'), str) and b['text'].strip() and not INJECTED.match(b['text'])]
    if not texts:
        return None
    block = HANDOFF_BLOCK.match(texts[0].lstrip())
    uid = block and _UUID.search(block.group())
    return (uid.group(), o.get('timestamp') or '') if uid else ()


def old_of(path):
    """The id of the chat a transcript is the handoff of, else None; reads the head up to the owner's first text only."""
    with open(lp(path), 'rb') as f:
        for raw in f:
            o = _load(raw)
            s = _start(o) if o else None
            if s is not None:
                return s[0] if s else None
    return None


def read_new(path):
    """The handoff a transcript starts with: {old, ts, model, tokens, usage: [input, cw5m, cw1h, cache_read], tail: [[model, cold, n], ...]}, else None."""
    old = ts = first = None
    seen, tail = set(), {}
    with open(lp(path), 'rb') as f:
        for n, raw in enumerate(f):
            if old is not None and b'assistant' not in raw:  # the tail is only assistant calls: no parse of the big tool results
                continue
            o = _load(raw)
            if not o:
                continue
            if old is None:
                s = _start(o)
                if s is None:
                    continue
                if not s:
                    return None
                old, ts = s
                continue
            c = _call(o)
            if not c or c[0] in seen:
                continue
            seen.add(c[0] or n)
            _, model, u = c
            cc = _num(u, 'cache_creation_input_tokens')
            if first is None:
                cc5 = min(cc, _num(u.get('cache_creation') if isinstance(u.get('cache_creation'), dict) else {}, 'ephemeral_5m_input_tokens'))
                first = {'model': model, 'tokens': sum(_num(u, k) for k in _USAGE), 'usage': [_num(u, 'input_tokens'), cc5, cc - cc5, _num(u, 'cache_read_input_tokens')]}
            else:
                key = (model, cc > _num(u, 'cache_read_input_tokens'))
                tail[key] = tail.get(key, 0) + 1
    return first and {'old': old, 'ts': ts, **first, 'tail': [[m, c, n] for (m, c), n in tail.items()]}


def _cached(cache, path, fn):
    """fn(path), kept in `cache` while the file keeps its size and mtime."""
    st = os.stat(lp(path))
    key = fn.__name__ + '|' + path  # a chat is read both as a new one and as an old one
    e = cache.get(key)
    if not (isinstance(e, dict) and e.get('size') == st.st_size and e.get('mtime') == st.st_mtime_ns):
        v = fn(path)
        if v is None and fn is agent_start and _kind(path) is None:  # a .meta.json may come later and is no part of the signature: read again
            cache.pop(key, None)
            return None
        e = cache[key] = {'size': st.st_size, 'mtime': st.st_mtime_ns, 'value': v}
    return e['value']


def _read(paths, cache, gone, conv, fn):
    """fn of the transcript of `conv`; of a removed one, what cleanup.snapshot kept (`gone`)."""
    if conv in paths:
        return _cached(cache, paths[conv], fn)
    return (gone or {}).get(conv, {}).get(fn.__name__)


def link(paths, new, cache, gone=None):
    """{new, usd, first, old_tokens, new_tokens, calls} for the chat `new` if it starts with a handoff whose old transcript is here or kept in `gone`, else None."""
    try:
        r = _read(paths, cache, gone, new, read_new)
        o = r and _read(paths, cache, gone, r['old'], last_usage)
    except OSError:  # a transcript removed or locked meanwhile counts as missing
        return None
    if not o:
        return None
    t_old, t_new = o['tokens'], r['tokens']
    _, _, p_cw1, p_cr, _ = price(o['model'], t_old)[0]
    # shortcut: tokens are the window size (prompt + output of the last call) on both sides; an unparsable time counts as warm, the cheaper read, so the saving is never overstated
    cold = (minutes(o['at'], r['ts']) or 0) * 60 >= COLD_AFTER
    p = price(r['model'], t_new)[0]
    (i, c5, c1, cr) = r['usage']
    first = t_old * (p_cw1 if cold else p_cr) - (i * p[0] + c5 * p[1] + c1 * p[2] + cr * p[3])
    d = max(0, t_old - t_new)
    tail = sum(n * d * price(m, t_old)[0][2 if c else 3] for m, c, n in r['tail'])
    return {'new': new, 'usd': (first + tail) / 1e6, 'first': first / 1e6, 'old_tokens': t_old, 'new_tokens': t_new,
            'calls': sum(n for _, _, n in r['tail'])}


def _paths(projects):
    """{session id: transcript} of every main transcript."""
    out = {}
    try:
        dirs = sorted(os.listdir(lp(projects)))
    except OSError:
        return out
    for d in dirs:
        try:
            for n in os.listdir(lp(os.path.join(projects, d))):
                if n.endswith('.jsonl'):
                    out.setdefault(n[:-6], os.path.join(projects, d, n))
        except OSError:
            pass
    return out


def _kind(path):
    """The agentType of the subagent transcript from its .meta.json, None if that is missing or unreadable."""
    try:
        with open(lp(path[:-6] + '.meta.json'), encoding='utf-8') as f:
            kind = json.load(f)['agentType']
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return kind if isinstance(kind, str) else None


def agent_start(path):
    """The start of a subagent transcript: {type, ts, model, usage: [input, cw5m, cw1h, cache_read], calls, workflow, resumes, cw1h_total, end, last}; None without
    a readable .meta.json or without a call. `resumes` are the cache reads of calls after a pause inside RESUME, `cw1h_total` the 1h writes of all calls,
    `end` the time and `last` the context size (input + writes + reads) of the last call."""
    kind = _kind(path)
    if kind is None:
        return None
    first, seen, prev, resumes, cw1h, last = None, set(), None, [], 0, 0
    with open(lp(path), 'rb') as f:
        for n, raw in enumerate(f):
            if b'assistant' not in raw:  # the calls are the only lines that matter: no parse of the big tool results
                continue
            o = _load(raw)
            m = o.get('message') if o and o.get('type') == 'assistant' else None
            u = m.get('usage') if isinstance(m, dict) and m.get('model') != '<synthetic>' else None
            if not isinstance(u, dict) or (m.get('id') or n) in seen:
                continue
            seen.add(m.get('id') or n)
            cc, cr, ts = _num(u, 'cache_creation_input_tokens'), _num(u, 'cache_read_input_tokens'), o.get('timestamp')
            split = u.get('cache_creation') if isinstance(u.get('cache_creation'), dict) else {}
            cw1h += _num(split, 'ephemeral_1h_input_tokens')
            if first is None:
                cc5 = min(cc, _num(split, 'ephemeral_5m_input_tokens'))
                first = {'type': kind, 'ts': ts if isinstance(ts, str) else None, 'model': m.get('model') or '',
                         'usage': [_num(u, 'input_tokens'), cc5, cc - cc5, cr], 'workflow': 'subagents/workflows/' in path.replace(chr(92), '/')}
            elif RESUME[0] < (minutes(prev, ts) or 0) * 60 < RESUME[1]:
                resumes.append(cr)
            prev, last = ts, _num(u, 'input_tokens') + cc + cr
    return first and {**first, 'calls': len(seen), 'resumes': resumes, 'cw1h_total': cw1h, 'end': prev if isinstance(prev, str) else None, 'last': last}


def agent_age(projects, sid, agent, ended, now):
    """{found, age, limit, context, usd} of the subagent `agent` of chat `sid`: seconds since it ended (`ended`, an ISO time, else its last call), the
    cache lifetime it wrote for (3600 with 1h writes, else 300), the context a resume rewrites and what that costs at its writing price."""
    gone = {'found': False, 'age': None, 'limit': 300, 'context': 0, 'usd': 0.0}
    path = None
    try:
        for d in os.listdir(lp(projects)):  # the one chat's folders only: not every chat's agents
            top = os.path.join(projects, d, sid, 'subagents')
            for root, _, names in os.walk(lp(top)):
                if 'agent-%s.jsonl' % agent in names:
                    path = os.path.join(top, os.path.relpath(root, lp(top)), 'agent-%s.jsonl' % agent)
    except OSError:
        pass
    r = path and agent_start(path)
    if not r:
        return gone
    age = _age(ended or r['end'], now)
    hour = r['cw1h_total'] > 0
    return {'found': True, 'age': age, 'limit': 3600 if hour else 300, 'context': r['last'], 'usd': r['last'] * price(r['model'], r['last'])[0][2 if hour else 1] / 1e6}


def _agents(projects, paths):
    """{session id: [agent-*.jsonl under <projects>/<folder>/<id>/subagents/, nested ones too]}; of the folder `paths` has the chat in, if it has one."""
    out = {}
    try:
        dirs = sorted(os.listdir(lp(projects)))
    except OSError:
        return out
    for d in dirs:
        try:
            for sid in os.listdir(lp(os.path.join(projects, d))):
                if sid in paths and os.path.dirname(paths[sid]) != os.path.join(projects, d):
                    continue
                top = os.path.join(projects, d, sid, 'subagents')
                for root, _, names in os.walk(lp(top)):
                    out.setdefault(sid, []).extend(os.path.join(top, os.path.relpath(root, lp(top)), n) for n in sorted(names)
                                                   if n.startswith('agent-') and n.endswith('.jsonl'))
        except OSError:
            pass
    return {s: [os.path.normpath(p) for p in ps] for s, ps in out.items()}


def _age(ts, now):
    try:
        return now - calendar.timegm(time.strptime(ts[:19], '%Y-%m-%dT%H:%M:%S'))
    except (TypeError, ValueError):
        return None


def reference(records, now):
    """{'agent', 'workflow'}: the median start of the general-purpose agents of the last 14 days (else of the last 20 records), or None."""
    out = {}
    for name, kind, wf in (('agent', 'general-purpose', False), ('workflow', 'workflow-subagent', True)):
        rs = [r for r in records if r['type'] == kind and r['workflow'] == wf]
        timed = sorted((r for r in rs if _age(r['ts'], now) is not None), key=lambda r: r['ts'], reverse=True)
        pick = [r for r in timed if _age(r['ts'], now) <= WINDOW] or (timed + [r for r in rs if _age(r['ts'], now) is None])[:SPARE]
        med = lambda f: statistics.median(f(r['usage']) for r in pick)
        out[name] = pick and {'w': med(lambda u: u[1] + u[2]), 'r': med(lambda u: u[3]), 'start': med(sum), 'n': len(pick),
                              'since': min((r['ts'] for r in pick if r['ts']), default=None)} or None
    return out


def lean_usd(rec, ref):
    """USD a lean agent saved against the reference: its smaller first write and read, and the smaller start of every later call.
    A resident (1h writes) is net: its resumes add, its dearer 1h writes subtract."""
    if ref is None:
        return 0
    i, c5, c1, cr = rec['usage']
    start = i + c5 + c1 + cr
    p = price(rec['model'], start)[0]
    resident = rec['type'] == 'alethite:resident'
    write = p[1] if resident or not c1 else p[2]
    usd = max(0, ref['r'] - cr) * p[3] + (max(0, ref['w'] - c5 - c1) * write if c5 + c1 else 0)  # a warm prefix (no write) saved no write
    usd += (rec['calls'] - 1) * max(0, ref['start'] - start) * p[3]
    if resident:
        usd += sum(rec['resumes']) * (p[1] - p[3]) - rec['cw1h_total'] * (p[2] - p[1])
    return usd / 1e6


def _disk(projects, paths, cache, now):
    """(on_disk, recs, ref): the agent transcripts of the chats on the disk, their starts {path: record or None} and the reference of those."""
    on_disk = _agents(projects, paths)
    recs = {}
    for ps in on_disk.values():
        for p in ps:
            try:
                recs[p] = _cached(cache, p, agent_start)
            except OSError:  # removed or locked meanwhile
                pass
    return on_disk, recs, reference([r for r in recs.values() if r], now)


def _records(convs, on_disk, recs, paths, gone):
    """The agent records of those chats: the files while the chat's transcript is on the disk, else what cleanup.snapshot kept in `gone`; a chat once."""
    out = []
    for c in set(convs):
        out += [recs.get(p) for p in on_disk.get(c, ())] if c in paths else (gone or {}).get(c, {}).get('agents') or []
    return [r for r in out if r]


def _lean(projects, paths, cache, gone):
    """(fn, files): fn(convs) is {usd, agents} of the lean agents of those chats (the files while the chat is on the disk, else what cleanup.snapshot
    kept in `gone`; a chat once); files are the agent transcripts read. The reference comes from the disk only."""
    on_disk, recs, ref = _disk(projects, paths, cache, time.time())

    def of(convs):
        mine = [r for r in _records(convs, on_disk, recs, paths, gone) if r['type'].startswith('alethite:')]
        return {'usd': sum(lean_usd(r, ref['workflow' if r['workflow'] else 'agent']) for r in mine), 'agents': len(mine)}
    return of, set(recs)


def _load_cache(cache_path):
    try:
        with open(cache_path, encoding='utf-8') as f:
            data = json.load(f)
        files = data['files'] if data['v'] == CACHE_VERSION else {}  # another version: the numbers may mean something else
        return {k: e for k, e in files.items() if isinstance(k, str) and '|' in k and isinstance(e, dict) and {'size', 'mtime', 'value'} <= e.keys()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):  # an unreadable or foreign file reads as empty
        return {}


def _save_cache(cache_path, cache, keep):
    tmp = None
    try:  # a failed cache write costs only speed
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(cache_path) or '.', suffix='.tmp')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'v': CACHE_VERSION, 'files': {k: e for k, e in cache.items() if k.split('|', 1)[1] in keep}}, f)
        os.replace(tmp, cache_path)
    except OSError:
        tmp and os.path.exists(tmp) and os.remove(tmp)


def lean_stats(projects, since, until, gone, now, cache_path=None):
    """The subagents that started on [since, until) over all chats (disk while the chat is there, else `gone`): {agents, lean, usd, ref_tokens, ref_since}.
    The reference is the 'agent' one of the disk records."""
    cache = _load_cache(cache_path)
    paths = _paths(projects)
    on_disk, disk, ref = _disk(projects, paths, cache, now)
    cache_path and _save_cache(cache_path, cache, set(paths.values()) | set(disk))
    recs = _records(set(on_disk) | set(gone or ()), on_disk, disk, paths, gone)
    mine = [r for r in recs if isinstance(r['ts'], str) and r['ts'][:10] >= since and (until is None or r['ts'][:10] < until)]
    lean = [r for r in mine if r['type'].startswith('alethite:')]
    a = ref['agent']
    return {'agents': len(mine), 'lean': len(lean), 'usd': sum(lean_usd(r, ref['workflow' if r['workflow'] else 'agent']) for r in lean),
            'ref_tokens': a and round(a['start']), 'ref_since': a and a['since'] and a['since'][:10]}


def savings(projects, session, convs, cache_path, gone=None):
    """{last, worker, project}; `convs` are the project's conversations from the index, None if it could not be read."""
    cache = _load_cache(cache_path)
    paths = _paths(projects)
    chain, seen, cur = [], set(), session
    while cur not in seen and (ln := link(paths, cur, cache, gone)):
        seen.add(cur)
        chain.append(ln)
        cur = _read(paths, cache, gone, cur, read_new)['old']
    lean, agent_files = _lean(projects, paths, cache, gone)
    mine = lean(seen | {cur})  # the current chat and every old one of the chain
    project = None
    if convs is not None:
        links = {ln['new']: ln for ln in chain}
        for c in convs:
            if c not in links and (ln := link(paths, c, cache, gone)):
                links[c] = ln
        every = lean(set(convs) | seen | {cur})
        project = {'usd': sum(ln['usd'] for ln in links.values()) + every['usd'], 'links': len(links), 'lean': every}
    _save_cache(cache_path, cache, set(paths.values()) | agent_files)
    return {'last': chain and {'usd': chain[0]['first'], 'old_tokens': chain[0]['old_tokens'], 'new_tokens': chain[0]['new_tokens']} or None,
            'worker': {'usd': sum(ln['usd'] for ln in chain) + mine['usd'], 'lean': mine, 'links': len(chain), 'calls': sum(ln['calls'] for ln in chain)},
            'project': project}
