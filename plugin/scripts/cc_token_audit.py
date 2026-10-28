#!/usr/bin/env python3
"""Where tokens and API-equivalent dollars go in Claude Code sessions, from local transcripts (the part of the token audit the mod runs).

Python 3.8+, stdlib only. Counts and token/char numbers only, never message text."""
import json
import os
import re
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime

from transcripts import lp

CHARS_PER_TOKEN = 3.5  # estimate: used only to split the first prompt (baseline) and the growth between calls
IMAGE_CHARS = 6000     # image blocks count as this many chars (base64 length is not used)
# A compact_boundary, or a prompt_total falling below this fraction of the previous call's, resets the context
# composition. Smaller drops (context trimming, model switch; 73-100% of previous on this data) only scale it down.
# Set to 1.0 for the literal "any negative delta is a reset" rule: that turns ~0.5M-token contexts into compaction_summary.
RESET_BELOW = 0.5
# API-equivalent $ per MTok: (input, cache_write_5m, cache_write_1h, cache_read, output).
# Keep this table identical to PRICES in plugin/hooks/guard.ts.
# Matched as a substring of message.model, most specific first; anything else is priced as sonnet-5 and reported.
OPUS, SON5, SON4 = (5, 6.25, 10, .5, 25), (2, 2.5, 4, .2, 10), (3, 3.75, 6, .3, 15)
PRICES = [
    ('opus-5-5', (4, 5, 8, .2, 20)), ('opus-5', OPUS), ('opus-4-8', OPUS), ('opus-4-7', OPUS),
    ('opus-4-6', OPUS), ('opus-4-5', OPUS),
    ('sonnet-5-5', (2, 2.5, 4, .1, 10)), ('sonnet-5', SON5), ('sonnet-4-6', SON4), ('sonnet-4-5', SON4),
    ('haiku-5-5', (.1, .125, .2, .01, .5)), ('haiku-4-5', (1, 1.25, 2, .1, 5)),
    ('fable-5-1', (10, 12.5, 20, .25, 50)), ('fable-5', (10, 12.5, 20, 1, 50)),
]
HAIKU55_LONG = (.5, .625, 1, .05, 2.5)  # haiku-5-5 when prompt_total > 100k
# Cache rewrite = prompt_total > 100k with more than half of it written to cache; causes are tested in this order.
REWRITE_CAUSES = ['first call in chain (resume/fork)', 'after compaction', 'model switch', 'idle >= 60 min',
                  'idle 5-60 min', 'no idle, no switch (prefix changed)']
# Attachments that are hook/session bookkeeping, not model context: skipped in the baseline and growth split.
# Reasons were checked on Claude Code 2.1.283 transcripts (is the content empty / metadata only / copied elsewhere).
EXCLUDED = {
    'hook_success': 'hook bookkeeping: stdout is empty or "{}" in 79% and 97% of the chars of those entries are the hook command '
                    'line; non-empty stdout is mostly recorded again as hook_additional_context (exact copy for all SubagentStart, '
                    'all non-empty PreToolUse/PostToolUse/Stop with text, 521 of 823 SessionStart). Caveat: 242 SessionStart outputs '
                    '(median 5.2k chars) have text found nowhere else in the chain; if model-visible they are uncounted now and '
                    'sit in baseline:tools+other (at most ~0.4M baseline tokens)',
    'credential_org': 'only an organization id, no text',
    'command_permissions': 'only an allowedTools list ([] in 98%), no text',
    'async_hook_response (exitCode 1)': 'non-blocking hook error: stdout and response empty, constant 100-char stderr, user-facing only',
}
KEPT_UNSURE = {
    'async_hook_response (exitCode 0 or 2)': '14 with real stdout, 3 blocking errors whose stderr goes to the model',
    'deferred_tools_record': 'non-empty entries hold full tool schemas found nowhere else, probably the only size trace of the prompt '
                             'growth when deferred tools are loaded; 92% of entries are empty stubs',
    'model': 'short model-identity text, not duplicated in the system prompt snapshot',
    'session_context': 'text context (~780 chars) found nowhere else in the chain',
    'prompt_snapshot': 'used for baseline:system_prompt (systemPrompt only; its tools field, avg 160k chars in about half the '
                       'snapshots, is not counted)',
}
# user text that Claude Code injects without isMeta (task notifications, local command output, reminders)
INJECTED = re.compile(r'\s*<(task-notification|local-command-|system-reminder)')


def price(model, pt):
    for sub, pr in PRICES:
        if sub in model:
            return (HAIKU55_LONG if sub == 'haiku-5-5' and pt > 100000 else pr), True
    return SON5, False


def num(d, k):
    v = d.get(k) if isinstance(d, dict) else None
    return v if isinstance(v, (int, float)) else 0


def minutes(a, b):
    """Minutes from ISO timestamp a to b (second precision), None if either is missing or unparsable."""
    try:
        t = lambda s: datetime.strptime(s[:19], '%Y-%m-%dT%H:%M:%S')
        return (t(b) - t(a)).total_seconds() / 60
    except (TypeError, ValueError):
        return None


def bookkeeping(a):
    """EXCLUDED label if this attachment is bookkeeping rather than model context, else None."""
    at = a.get('type')
    if at in ('hook_success', 'credential_org', 'command_permissions'):
        return at
    if at == 'async_hook_response' and a.get('exitCode') == 1 and (a.get('stdout') or '').strip() in ('', '{}'):
        return 'async_hook_response (exitCode 1)'


def tool_cat(name):
    name = name or 'unknown'
    return 'tool:mcp:' + name.split('__')[1] if name.startswith('mcp__') and name.count('__') >= 2 else 'tool:' + name


def result_chars(c):
    if isinstance(c, str):
        return len(c)
    n = 0
    for x in c if isinstance(c, list) else []:
        t = x.get('type') if isinstance(x, dict) else None
        n += IMAGE_CHARS if t == 'image' else len(x.get('text') or '') if t == 'text' else len(json.dumps(x, ensure_ascii=False))
    return n


def read_chain(path, ctx, visit=None):
    """One transcript file -> ordered events: ('i', category, chars) | ('c', call) | ('k',) compaction; plus chain counters."""
    ev, calls, tools, ch = [], {}, {}, Counter()
    with open(lp(path), encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except ValueError:
                o = None
            if not isinstance(o, dict):
                ctx['lines_skipped'] += 1
                continue
            if visit:
                visit(path, o)
            t = o.get('type')
            if t == 'assistant':
                m = o.get('message') or {}
                if m.get('model') == '<synthetic>':
                    continue
                mid = m.get('id') or o.get('uuid')
                c = calls.get(mid)
                if c is None:  # one API call = several entries with the same message.id; keep the first position
                    c = calls[mid] = {'id': mid, 'model': m.get('model') or 'unknown', 'ts': o.get('timestamp') or '',
                                      'usage': None, 'seen': set()}
                    ev.append(('c', c))
                u = m.get('usage')
                if isinstance(u, dict) and (c['usage'] is None or num(u, 'output_tokens') >= num(c['usage'], 'output_tokens')):
                    c['usage'] = u
                for b in m.get('content') if isinstance(m.get('content'), list) else []:
                    if not isinstance(b, dict):
                        continue
                    if b.get('type') == 'tool_use':
                        tools[b.get('id')] = tool_cat(b.get('name'))
                    elif b.get('type') == 'text' and isinstance(b.get('text'), str) and hash(b['text']) not in c['seen']:
                        c['seen'].add(hash(b['text']))
            elif t == 'user':
                if o.get('isCompactSummary'):
                    continue
                cont = (o.get('message') or {}).get('content')
                if isinstance(cont, str):
                    cont = [{'type': 'text', 'text': cont}]
                for b in cont if isinstance(cont, list) else []:
                    if not isinstance(b, dict):
                        continue
                    bt = b.get('type')
                    if bt == 'text' and isinstance(b.get('text'), str):
                        cat = 'injected_meta' if o.get('isMeta') or INJECTED.match(b['text']) else 'user_prompt'
                        ev.append(('i', cat, len(b['text'])))
                    elif bt == 'image':
                        ev.append(('i', 'image', IMAGE_CHARS))
                    elif bt == 'tool_result':
                        ev.append(('i', tools.get(b.get('tool_use_id'), 'tool:unknown'), result_chars(b.get('content'))))
                    else:
                        ev.append(('i', 'user_other', len(json.dumps(b, ensure_ascii=False))))
            elif t == 'attachment' and isinstance(o.get('attachment'), dict):
                a = o['attachment']
                at = a.get('type') or 'unknown'
                if (skip := bookkeeping(a)):
                    ctx['excluded:' + skip] += 1
                    continue
                if at == 'prompt_snapshot':
                    cat, chars = 'att:prompt_snapshot', sum(len(s) for s in a.get('systemPrompt') or [] if isinstance(s, str))
                elif at == 'queued_command':  # a typed message queued mid-turn
                    p = a.get('prompt')
                    cat = 'injected_meta' if a.get('isMeta') else 'user_prompt'
                    chars = len(p) if isinstance(p, str) else len(json.dumps(a, ensure_ascii=False))
                else:
                    cat, chars = 'att:' + at, len(json.dumps(a, ensure_ascii=False))
                ev.append(('i', cat, chars))
                ch['att:' + at] += chars
            elif t == 'system' and o.get('subtype') == 'compact_boundary':
                ev.append(('k',))
                ctx['compact_boundary_entries'] += 1
    return ev, ch


def fit(parts, total):
    """Scale parts down if they exceed total; return (parts, remainder >= 0)."""
    s = sum(parts.values())
    f = min(1, total / s) if s > 0 else 1
    return {k: v * f for k, v in parts.items()}, max(0, total - s * f)


def run_chain(ev, ch, kind, A, since, until=None):
    comp, base, pend, touched = {}, {}, Counter(), set()
    prev_pt = prev_vis = prev_model = prev_ts = None
    reset = False
    row = {'kind': kind, 'pts': [], 'baseline': None, 'fast': 0, 'compactions': 0,
           'skill_chars': ch['att:skill_listing'], 'instr_chars': ch['att:instructions']}
    for e in ev:
        if e[0] == 'i':
            pend[e[1]] += e[2]
            continue
        if e[0] == 'k':
            reset = True
            continue
        c = e[1]
        u = c['usage']
        if u is None:
            A['odd']['calls_without_usage'] += 1
            continue
        inp, cr, cci, out = num(u, 'input_tokens'), num(u, 'cache_read_input_tokens'), num(u, 'cache_creation_input_tokens'), num(u, 'output_tokens')
        cw1 = min(num(u.get('cache_creation'), 'ephemeral_1h_input_tokens'), cci)
        cw5 = cci - cw1  # missing split -> all 5m
        think = min(num(u.get('output_tokens_details'), 'thinking_tokens'), out)
        vis = out - think
        pt = inp + cci + cr
        day = c['ts'][:10]
        counted = (not since or day >= since) and (not until or day < until)
        if isinstance(u.get('iterations'), list) and len(u['iterations']) > 1:
            A['odd']['calls_with_multiple_iterations (usage = last iteration only)'] += 1
        added = Counter()
        if prev_pt is None:  # baseline = everything before the first call; tools+other is the remainder
            parts = {'baseline:' + ('system_prompt' if k == 'att:prompt_snapshot' else k): v / CHARS_PER_TOKEN for k, v in pend.items()}
            if sum(parts.values()) > pt:
                A['odd']['chains_where_baseline_estimate_exceeds_prompt_total'] += 1
            comp, rem = fit(parts, pt)
            comp['baseline:tools+other'] = rem
            base = dict(comp)
            added.update(comp)
        elif reset or pt < prev_pt * RESET_BELOW:  # context was reset: baseline unchanged + compaction summary
            if reset:
                row['compactions'] += counted
            else:
                A['odd']['context_resets_without_compact_boundary'] += 1
            comp, rem = fit(base, pt)
            comp['compaction_summary'] = rem
            added['compaction_summary'] = rem
        elif pt < prev_pt:  # small drop: keep the mix, scale it down (what was trimmed is unknown)
            A['odd']['small_context_drops_scaled_not_reset'] += 1
            comp = {k: v * pt / prev_pt for k, v in comp.items()}
        else:
            delta = pt - prev_pt
            a = min(prev_vis, delta)  # previous visible output now sits in context (capped by the actual growth)
            new = delta - a
            comp['assistant_output_in_context'] = comp.get('assistant_output_in_context', 0) + a
            added['assistant_output_in_context'] = a
            tot = sum(pend.values())
            if new > 0:
                for k, v in (pend.items() if tot > 0 else [('unattributed', 1)]):
                    share = new * v / (tot if tot > 0 else 1)
                    comp[k] = comp.get(k, 0) + share
                    added[k] += share
        pr, known = price(c['model'], pt)
        if not known:
            A['unpriced'][c['model']] += 1
        tt = {'input': inp * pr[0], 'cache_write_5m': cw5 * pr[1], 'cache_write_1h': cw1 * pr[2], 'cache_read': cr * pr[3],
              'output_thinking': think * pr[4], 'output_visible': vis * pr[4]}
        tt = {k: v / 1e6 for k, v in tt.items()}
        prompt_cost = tt['input'] + tt['cache_write_5m'] + tt['cache_write_1h'] + tt['cache_read']
        s = sum(comp.values())
        costs = {k: prompt_cost * v / s for k, v in comp.items()} if s > 0 else {'unattributed': prompt_cost}
        costs['output:thinking'], costs['output:visible'] = tt['output_thinking'], tt['output_visible']
        added['output:thinking'], added['output:visible'] = think, vis
        first_call = prev_pt is None
        if pt > 100000 and cci > 0.5 * pt:  # cache rewrite: classify the cause (reset = compact_boundary since previous call)
            gap = minutes(prev_ts, c['ts'])
            cause = (REWRITE_CAUSES[0] if first_call else REWRITE_CAUSES[1] if reset else REWRITE_CAUSES[2] if c['model'] != prev_model
                     else REWRITE_CAUSES[3] if gap is not None and gap >= 60 else REWRITE_CAUSES[4] if gap is not None and gap >= 5
                     else REWRITE_CAUSES[5])
            if counted:
                w = A['rw'][cause][kind]
                w[0] += 1
                w[1] += tt['cache_write_5m'] + tt['cache_write_1h']
                if gap is not None:
                    A['rw'][cause]['gaps'].append(gap)
        prev_pt, prev_vis, prev_model, prev_ts, pend, reset = pt, vis, c['model'], c['ts'], Counter(), False
        if not counted:
            continue
        total = sum(costs.values())
        A['calls'] += 1
        A['cost'] += total
        A['first'] = min(A['first'] or day, day) if day else A['first']
        A['last'] = max(A['last'], day)
        if c.get('dup'):
            A['dup_cost'] += total
        for k, v in costs.items():
            A['cat'][k][1] += v
        for k, v in added.items():
            A['cat'][k][0] += v
        A['ttype'].update(tt)
        row['pts'].append(pt)
        row['fast'] += u.get('speed') not in (None, 'standard')
        if first_call:
            row['baseline'] = pt
        for key in (('kind', kind), ('model', c['model'])):
            g = A['g'][key[0]][key[1]]
            if key not in touched:
                touched.add(key)
                g['chains'] += 1
            g['calls'] += 1
            g['cost_usd'] += total
            g['prompt_tokens'] += pt
            g['cache_read_tokens'] += cr
            g['output_tokens'] += out
            g['cost_by_category'].update(costs)
    if row['pts']:
        A['rows'].append(row)


def med(xs):
    return statistics.median(xs) if xs else 0


def pct(xs, p):
    return sorted(xs)[min(len(xs) - 1, int(p * len(xs)))] if xs else 0


def stats(rows):
    n = len(rows)
    if not n:
        return {'chains': 0}
    base = [r['baseline'] for r in rows if r['baseline'] is not None]
    cpc = [len(r['pts']) for r in rows]
    pts = [p for r in rows for p in r['pts']]
    ins = [r['instr_chars'] for r in rows if r['instr_chars']]
    return {'chains': n, 'calls': sum(cpc),
            'baseline_tokens_median': med(base), 'baseline_tokens_p90': pct(base, .9),
            'calls_per_chain_median': med(cpc), 'calls_per_chain_p90': pct(cpc, .9),
            'prompt_total_median': med(pts), 'prompt_total_max': max(pts),
            'compactions': sum(r['compactions'] for r in rows), 'fast_mode_calls': sum(r['fast'] for r in rows),
            'avg_skill_listing_chars_per_chain': sum(r['skill_chars'] for r in rows) / n,
            'avg_instructions_chars_per_chain': sum(r['instr_chars'] for r in rows) / n,
            'chains_with_instructions': len(ins), 'instructions_chars_max': max(ins, default=0)}


def grp_out(gd, total):
    out = {}
    for k, g in sorted(gd.items(), key=lambda kv: -kv[1]['cost_usd']):
        out[k] = dict(g, share=g['cost_usd'] / total if total else 0,
                      cost_by_category=dict(g['cost_by_category'].most_common()))
    return out


def analyze(projects, since=None, until=None, visit=None):
    """visit(path, obj) is called for every parsed line of every transcript read (not for files older than `since`)."""
    t0 = time.time()
    root = lp(projects)
    if not os.path.isdir(root):
        raise SystemExit('projects dir not found: ' + str(projects))
    files = sorted(os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs if f.endswith('.jsonl'))
    A = {'calls': 0, 'cost': 0.0, 'dup_cost': 0.0, 'first': '', 'last': '', 'rows': [], 'cat': defaultdict(lambda: [0.0, 0.0]),
         'ttype': Counter(), 'odd': Counter(), 'unpriced': Counter(),
         'rw': {c: {'main': [0, 0.0], 'subagent': [0, 0.0], 'gaps': []} for c in REWRITE_CAUSES},
         'g': {k: defaultdict(lambda: {'chains': 0, 'calls': 0, 'cost_usd': 0.0, 'prompt_tokens': 0, 'cache_read_tokens': 0,
                                      'output_tokens': 0, 'cost_by_category': Counter()})
               for k in ('kind', 'model')}}
    ctx, seen_ids = Counter(), set()
    for p in files:
        # entries are never newer than the file mtime, so an older file cannot hold calls on/after --since
        if since and time.strftime('%Y-%m-%d', time.gmtime(os.path.getmtime(p))) < since:
            ctx['files_skipped_before_since'] += 1
            continue
        parts = re.split(r'[\\/]', p[len(root):].strip('\\/'))
        kind = 'subagent' if 'subagents' in parts[:-1] else 'main'
        try:
            ev, ch = read_chain(p, ctx, visit)
        except Exception as e:  # a line of an unexpected shape too: one file must not stop the whole measure
            ctx['files_unreadable'] += 1
            print('  unreadable file (%s)' % type(e).__name__, file=sys.stderr)
            continue
        ctx['files_parsed'] += 1
        for e in ev:
            if e[0] == 'c':
                e[1]['dup'] = e[1]['id'] in seen_ids  # resumed/forked sessions replay history; counted in both chains
                A['odd']['message_ids_also_seen_in_another_chain (counted in both)'] += e[1]['dup']
                seen_ids.add(e[1]['id'])
        run_chain(ev, ch, kind, A, since, until)
    rows, total = A['rows'], A['cost']
    cats = sorted(({'category': k, 'tokens_added': v[0], 'cost_usd': v[1], 'share': v[1] / total if total else 0}
                   for k, v in A['cat'].items()), key=lambda d: -d['cost_usd'])
    def rw_rec(m, s, gaps=None):
        cost = m[1] + s[1]
        r = {'calls': m[0] + s[0], 'cost_usd': cost, 'share_of_all_cost': cost / total if total else 0,
             'main': {'calls': m[0], 'cost_usd': m[1]}, 'subagent': {'calls': s[0], 'cost_usd': s[1]}}
        if gaps is not None:
            r['median_gap_min'] = med(gaps) if gaps else None
        return r
    rw = A['rw']

    return {
        'meta': {'date_from': A['first'], 'date_to': A['last'], 'since': since, 'cost_unit': 'API-equivalent USD',
                 'chars_per_token_estimate': CHARS_PER_TOKEN, 'image_chars': IMAGE_CHARS,
                 'prices_usd_per_mtok_(input,cw5m,cw1h,cache_read,output)': {s: list(p) for s, p in PRICES},
                 'haiku_5_5_over_100k_prompt': list(HAIKU55_LONG),
                 'files_parsed': ctx['files_parsed'], 'files_skipped_before_since': ctx['files_skipped_before_since'],
                 'files_unreadable': ctx['files_unreadable'], 'lines_skipped': ctx['lines_skipped'],
                 'compact_boundary_entries': ctx['compact_boundary_entries'], 'odd': dict(A['odd']),
                 'cost_usd_of_calls_whose_message_id_is_in_an_earlier_chain': A['dup_cost'], 'reset_below': RESET_BELOW,
                 'attachments_excluded_as_bookkeeping': {k: {'entries': ctx['excluded:' + k], 'reason': v} for k, v in EXCLUDED.items()},
                 'attachments_kept_unsure': KEPT_UNSURE,
                 'unpriced_model_calls': dict(A['unpriced']), 'runtime_sec': round(time.time() - t0, 1)},
        'totals': {'chains': len(rows), 'calls': A['calls'], 'cost_usd': total},
        'categories': cats,
        'cache_rewrites': {
            'definition': 'prompt_total > 100000 and cache_creation_input_tokens > 0.5 * prompt_total; cost = cache-write $ of the call',
            'total': rw_rec([sum(w['main'][i] for w in rw.values()) for i in (0, 1)],
                            [sum(w['subagent'][i] for w in rw.values()) for i in (0, 1)]),
            'by_cause': {c: rw_rec(rw[c]['main'], rw[c]['subagent'], rw[c]['gaps']) for c in REWRITE_CAUSES}},
        'cost_by_token_type': dict(A['ttype']),
        'by_kind': grp_out(A['g']['kind'], total), 'by_model': grp_out(A['g']['model'], total),
        'stats': {'all': stats(rows), 'main': stats([r for r in rows if r['kind'] == 'main']),
                  'subagent': stats([r for r in rows if r['kind'] == 'subagent'])},
    }


def selftest():
    """Tiny synthetic transcript: duplicate message ids, a tool_result, an attachment, a compaction, a subagent chain."""
    att1, att2 = {'type': 'skill_listing', 'content': 'x' * 350}, {'type': 'task_reminder', 'content': 'z' * 100}
    # bookkeeping attachments (must be ignored in baseline and growth split), 4 kinds
    noise = [{'type': 'attachment', 'attachment': a} for a in (
        {'type': 'hook_success', 'hookEvent': 'PostToolUse', 'command': 'c' * 500, 'stdout': '{}', 'content': ''},
        {'type': 'credential_org', 'organizationUuid': 'u'},
        {'type': 'command_permissions', 'allowedTools': []},
        {'type': 'async_hook_response', 'exitCode': 1, 'stdout': '', 'response': '{}', 'stderr': 'e' * 100})]
    u1 = {'input_tokens': 10, 'cache_creation_input_tokens': 1000, 'cache_read_input_tokens': 0, 'output_tokens': 20,
          'output_tokens_details': {'thinking_tokens': 10}, 'cache_creation': {'ephemeral_1h_input_tokens': 0, 'ephemeral_5m_input_tokens': 1000}}
    u2 = {'input_tokens': 5, 'cache_creation_input_tokens': 100, 'cache_read_input_tokens': 1000, 'output_tokens': 30,
          'cache_creation': {'ephemeral_1h_input_tokens': 100, 'ephemeral_5m_input_tokens': 0}}
    u3 = {'input_tokens': 3, 'cache_creation_input_tokens': 200, 'cache_read_input_tokens': 0, 'output_tokens': 10}
    u4 = {'input_tokens': 7, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0, 'output_tokens': 3}

    def asst(mid, ts, usage, content, model='claude-sonnet-5'):
        return {'type': 'assistant', 'timestamp': ts, 'message': {'id': mid, 'model': model, 'usage': usage, 'content': content}}
    text = [{'type': 'text', 'text': 'hi'}]
    main = noise + [
        {'type': 'attachment', 'attachment': att1},
        {'type': 'user', 'message': {'content': 'hello'}},
        asst('m1', '2026-01-05T10:00:00Z', u1, text),
        asst('m1', '2026-01-05T10:00:01Z', dict(u1, output_tokens=50), [{'type': 'tool_use', 'id': 't1', 'name': 'Read', 'input': {}}]),
        asst('m1', '2026-01-05T10:00:02Z', u1, text),  # duplicate with lower output_tokens must not win
        asst('s', '2026-01-05T10:00:02Z', u1, text, '<synthetic>'),
        {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'y' * 350}]}},
        {'type': 'attachment', 'attachment': att2},
    ] + noise + [
        asst('m2', '2026-01-05T10:01:00Z', u2, text),
        {'type': 'system', 'subtype': 'compact_boundary'},
        {'type': 'user', 'isCompactSummary': True, 'message': {'content': 'summary'}},
        asst('m3', '2026-02-02T10:00:00Z', u3, []),
    ]
    u5 = {'input_tokens': 6, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0, 'output_tokens': 2}  # 6/7 of m4: small drop
    sub = [{'type': 'user', 'message': {'content': 'do it!'}}, asst('m4', '2026-02-03T10:00:00Z', u4, text),
           asst('m5', '2026-02-03T10:00:05Z', u5, text)]
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, 'proj-a', 's1', 'subagents'))
        with open(os.path.join(tmp, 'proj-a', 's1.jsonl'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(json.dumps(x) for x in main) + '\nnot json\n')
        with open(os.path.join(tmp, 'proj-a', 's1', 'subagents', 'agent-1.jsonl'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(json.dumps(x) for x in sub) + '\n')
        R = analyze(tmp)
    cats = {c['category']: c for c in R['categories']}
    close = lambda a, b: abs(a - b) < 1e-9
    h = len(json.dumps(att2))
    exp = ((10 * 2 + 1000 * 2.5) + 50 * 10 + (5 * 2 + 100 * 4 + 1000 * .2) + 30 * 10 + (3 * 2 + 200 * 2.5) + 10 * 10 + (7 * 2) + 3 * 10 + (6 * 2) + 2 * 10) / 1e6
    assert R['totals']['calls'] == 5 and R['totals']['chains'] == 2, R['totals']  # dedupe + synthetic skipped
    assert R['meta']['odd']['small_context_drops_scaled_not_reset'] == 1 and 'context_resets_without_compact_boundary' not in R['meta']['odd']
    assert R['meta']['lines_skipped'] == 1 and R['meta']['files_parsed'] == 2
    assert close(R['totals']['cost_usd'], exp) and close(sum(c['cost_usd'] for c in R['categories']), exp)  # cost split sums to total
    assert close(sum(R['cost_by_token_type'].values()), exp)
    assert set(R['by_kind']) == {'main', 'subagent'} and R['by_kind']['subagent']['calls'] == 2
    assert cats['output:visible']['tokens_added'] == 40 + 30 + 10 + 3 + 2 and cats['output:thinking']['tokens_added'] == 10
    assert cats['assistant_output_in_context']['tokens_added'] == 40  # call 1 visible output, delta 95
    assert close(cats['tool:Read']['tokens_added'], 55 * 350 / (350 + h)) and close(cats['att:task_reminder']['tokens_added'], 55 * h / (350 + h))
    sa = len(json.dumps(att1))
    # bookkeeping attachments take no share of the baseline or the growth (the numbers above only add up without them)
    assert not [k for k in cats if k.split(':')[-1] in ('hook_success', 'credential_org', 'command_permissions', 'async_hook_response')], cats.keys()
    assert {k: v['entries'] for k, v in R['meta']['attachments_excluded_as_bookkeeping'].items()} == {k: 2 for k in EXCLUDED}
    assert close(cats['baseline:tools+other']['tokens_added'], 1010 - (sa + 5) / 3.5 + 7 - 6 / 3.5)
    assert close(cats['baseline:att:skill_listing']['tokens_added'], sa / 3.5)
    # context tokens added = first prompt (1010) + growth (95) + reset (0) + subagent first prompt (7)
    ctx_added = sum(c['tokens_added'] for k, c in cats.items() if not k.startswith('output:'))
    assert close(ctx_added, 1010 + 95 + 0 + 7), ctx_added
    assert R['stats']['all']['compactions'] == 1 and R['meta']['compact_boundary_entries'] == 1
    assert R['stats']['main']['baseline_tokens_median'] == 1010 and R['stats']['all']['prompt_total_max'] == 1105
    assert not R['meta']['unpriced_model_calls']
    assert R['cache_rewrites']['total']['calls'] == 0  # nothing above 100k prompt_total in the chains above

    # cache rewrites: one call per cause, plus two near-misses (small cache write; prompt_total < 100k)
    def rw(mid, minute, model, inp, cci, cr, h1=0):
        u = {'input_tokens': inp, 'cache_creation_input_tokens': cci, 'cache_read_input_tokens': cr, 'output_tokens': 1,
             'cache_creation': {'ephemeral_1h_input_tokens': h1}}
        return asst(mid, '2026-03-01T%02d:%02d:00Z' % (10 + minute // 60, minute % 60), u, text, model)
    S, O = 'claude-sonnet-5', 'claude-opus-5'
    kept = {'type': 'attachment', 'attachment': {'type': 'async_hook_response', 'exitCode': 2, 'stdout': '', 'response': '{}', 'stderr': 'e' * 136}}
    chain = [kept, noise[0],  # blocking async hook error is kept, hook_success is not
             {'type': 'user', 'message': {'content': 'x'}},
             rw('r1', 0, S, 1000, 150000, 0),                  # first call in chain: 150000 * 2.5
             rw('r2', 90, S, 10, 120000, 50000, h1=120000),    # 90 min idle, 1h write: 120000 * 4
             rw('r3', 100, S, 5, 90000, 50000),                # 10 min idle: 90000 * 2.5
             rw('r4', 101, O, 5, 90000, 60000),                # model switch: 90000 * 6.25
             rw('r5', 102, O, 5, 100000, 50000),               # nothing else explains it: 100000 * 6.25
             {'type': 'system', 'subtype': 'compact_boundary'},
             rw('r6', 103, O, 5, 100000, 30000),               # after compaction: 100000 * 6.25
             rw('r7', 104, O, 5, 1000, 130000),                # not a rewrite: little cache written
             rw('r8', 105, O, 5, 80000, 10000)]                # not a rewrite: prompt_total < 100k
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, 'proj-b'))
        with open(os.path.join(tmp, 'proj-b', 's2.jsonl'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(json.dumps(x) for x in chain) + '\n')
        R2 = analyze(tmp)
    # same order as REWRITE_CAUSES: first call, after compaction, model switch, idle >= 60, idle 5-60, prefix changed
    W, want = R2['cache_rewrites'], [150000 * 2.5, 100000 * 6.25, 90000 * 6.25, 120000 * 4, 90000 * 2.5, 100000 * 6.25]
    assert list(W['by_cause']) == REWRITE_CAUSES
    for (cause, r), usd_ in zip(W['by_cause'].items(), want):
        assert r['calls'] == 1 and close(r['cost_usd'], usd_ / 1e6) and r['main']['calls'] == 1 and r['subagent']['calls'] == 0, cause
        assert close(r['share_of_all_cost'], r['cost_usd'] / R2['totals']['cost_usd'])
    assert W['total']['calls'] == 6 and close(W['total']['cost_usd'], sum(want) / 1e6)
    assert W['by_cause']['idle >= 60 min']['median_gap_min'] == 90 and W['by_cause']['idle 5-60 min']['median_gap_min'] == 10
    assert R2['stats']['all']['compactions'] == 1
    cats2 = {c['category'] for c in R2['categories']}
    assert 'baseline:att:async_hook_response' in cats2 and 'baseline:att:hook_success' not in cats2
    print('selftest OK', file=sys.stderr)
