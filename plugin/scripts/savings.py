"""What the New-worker handoffs saved, from the transcripts alone: no index, no model. Numbers only; the sign is the saving (negative: dearer)."""
import json
import os
import re
import tempfile

from cc_token_audit import minutes, price
from transcripts import HANDOFF_BLOCK, INJECTED, _USAGE, _blocks, _load, last_usage, lp

COLD_AFTER = 3600  # seconds: the cache of a chat that was silent this long is gone
_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
# shortcut: every cache write is priced as the 1h write (what Claude Code sends); a 5m write in the first call is split off when the usage names it


def _call(o):
    """(message id, model, usage) of a main-chat API call, else None."""
    m = o.get('message') if o.get('type') == 'assistant' and not o.get('isSidechain') else None
    u = m.get('usage') if isinstance(m, dict) and m.get('model') != '<synthetic>' else None
    return (m.get('id'), m.get('model') or '', u) if isinstance(u, dict) else None


def _num(u, k):
    return u.get(k) if isinstance(u.get(k), int) else 0


def read_new(path):
    """The handoff a transcript starts with: {old, ts, model, tokens, usage: [input, cw5m, cw1h, cache_read], tail: [[model, cold, n], ...]}, else None."""
    old = ts = first = None
    seen, tail = set(), {}
    with open(lp(path), 'rb') as f:
        for n, raw in enumerate(f):
            o = _load(raw)
            if not o:
                continue
            if old is None:
                if o.get('type') != 'user' or o.get('isMeta'):
                    continue
                texts = [b['text'] for b in _blocks(o.get('message', {}).get('content') if isinstance(o.get('message'), dict) else None)
                         if b.get('type') == 'text' and isinstance(b.get('text'), str) and b['text'].strip() and not INJECTED.match(b['text'])]
                if not texts:
                    continue
                block = HANDOFF_BLOCK.match(texts[0].lstrip())
                uid = block and _UUID.search(block.group())
                if not uid:
                    return None
                old, ts = uid.group(), o.get('timestamp') or ''
                continue
            c = _call(o)
            if not c or c[0] in seen:
                continue
            seen.add(c[0] or n)
            mid, model, u = c
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
        e = cache[key] = {'size': st.st_size, 'mtime': st.st_mtime_ns, 'value': fn(path)}
    return e['value']


def link(paths, new, cache):
    """{new, usd, first, old_tokens, new_tokens, calls} for the chat `new` if it starts with a handoff whose old transcript is still here, else None."""
    try:
        r = new in paths and _cached(cache, paths[new], read_new)
        old = r and paths.get(r['old'])
        o = old and _cached(cache, old, last_usage)
    except OSError:  # a transcript removed or locked meanwhile counts as missing
        return None
    if not o:
        return None
    t_old, t_new = o['tokens'], r['tokens']
    pi, p_cw5, p_cw1, p_cr, _ = price(o['model'], t_old)[0]
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


def savings(projects, session, convs, cache_path):
    """{last, worker, project}; `convs` are the project's conversations from the index, None if it could not be read."""
    try:
        with open(cache_path, encoding='utf-8') as f:
            cache = json.load(f)
        cache = cache if isinstance(cache, dict) else {}
    except (OSError, ValueError):
        cache = {}
    paths = _paths(projects)
    chain, seen, cur = [], set(), session
    while cur not in seen and (ln := link(paths, cur, cache)):
        seen.add(cur)
        chain.append(ln)
        cur = _cached(cache, paths[cur], read_new)['old']
    project = None
    if convs is not None:
        links = {ln['new']: ln for ln in chain}
        for c in convs:
            if c not in links and (ln := link(paths, c, cache)):
                links[c] = ln
        project = {'usd': sum(ln['usd'] for ln in links.values()), 'links': len(links)}
    tmp = None
    try:  # a failed cache write costs only speed
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(cache_path) or '.', suffix='.tmp')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({k: e for k, e in cache.items() if k.split('|', 1)[1] in paths.values()}, f)
        os.replace(tmp, cache_path)
    except OSError:
        tmp and os.path.exists(tmp) and os.remove(tmp)
    return {'last': chain and {'usd': chain[0]['first'], 'old_tokens': chain[0]['old_tokens'], 'new_tokens': chain[0]['new_tokens']} or None,
            'worker': {'usd': sum(ln['usd'] for ln in chain), 'links': len(chain), 'calls': sum(ln['calls'] for ln in chain)},
            'project': project}
