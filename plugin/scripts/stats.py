"""The success metrics of the design (§9): cost and cache figures from the token audit, owner messages from the index; and what the
memory takes of the context (stage 3: measure before compressing more)."""
import math
import cc_token_audit
import memdb
from texts import t
from transcripts import HANDOFF_END, HANDOFF_START

MOD_TOOLS = ('mcp__cacheguard__recall', 'mcp__cacheguard__recall_more')

BASELINE = {'usd_per_owner_msg': 5.20, 'rewrite_share': 0.417, 'idle60_share': 0.319, 'median_main_context': 428000}  # weekly limit: t('stats_weekly_base')


def metrics(projects, conn, since, until=None, errors=None, machine=None):
    """Figures for the API calls on/after `since` (and before `until`) in the transcripts under `projects`.
    What would make them wrong (unreadable transcripts, models without a price) is appended to the `errors` list, as the index reports its own."""
    errors = [] if errors is None else errors
    errors += memdb.update(conn, projects)['errors']  # the owner count comes from the index
    load, visit = memory_load(since, until)
    R = cc_token_audit.analyze(projects, since, until=until, visit=visit)
    meta = R['meta']
    if meta['files_unreadable']:
        errors.append({'path': 'audit', 'error': 'files_unreadable: %d' % meta['files_unreadable']})
    if meta['unpriced_model_calls']:
        errors.append({'path': 'audit', 'error': 'unpriced_model_calls: ' + ', '.join('%s=%d' % kv for kv in meta['unpriced_model_calls'].items())})
    cost = R['by_kind'].get('main', {}).get('cost_usd', 0)
    n = memdb.owner_count(conn, since, until, machine)
    main = R['stats']['main']  # {'chains': 0} when the period holds no main chat
    rw = R['cache_rewrites']
    # what the audit charges to the results of recall and recall_more: their tokens in the context of every later request
    tools = next((c for c in R['categories'] if c['category'] == 'tool:mcp:cacheguard'), {'cost_usd': 0, 'share': 0})
    return {'since': since, 'until': until, 'cost_main': cost, 'owner_msgs': n, 'usd_per_owner_msg': cost / n if n else None,
            'rewrite_share': rw['total']['share_of_all_cost'], 'idle60_share': rw['by_cause']['idle >= 60 min']['share_of_all_cost'],
            'median_main_context': main.get('prompt_total_median', 0), 'main_calls': main.get('calls', 0),
            'recall_calls': load['recall_calls'], 'recall_tokens': round(load['recall_chars'] / cc_token_audit.CHARS_PER_TOKEN),
            'recall_cost': tools['cost_usd'], 'recall_share': tools['share'],
            'handoffs': load['handoffs'], 'handoff_tokens': round(load['handoff_chars'] / cc_token_audit.CHARS_PER_TOKEN)}


def memory_load(since, until=None):
    """What the mod put into the context over the period: the calls of recall and recall_more and the characters they returned, the handoffs
    sent to new workers and their characters. Returns (counts, visit): the audit calls visit(path, line) for every line of every transcript
    it reads, so the transcripts are read once."""
    out = {'recall_calls': 0, 'recall_chars': 0, 'handoffs': 0, 'handoff_chars': 0}
    ours = set()  # (transcript, tool_use id) of the mod's tools

    def visit(path, o):
        m = o.get('message')
        blocks = m.get('content') if isinstance(m, dict) else None
        blocks = [{'type': 'text', 'text': blocks}] if isinstance(blocks, str) else blocks if isinstance(blocks, list) else []
        day = str(o.get('timestamp') or '')[:10]
        inside = day >= since and (until is None or day < until)
        for b in (b for b in blocks if isinstance(b, dict)):
            if o.get('type') == 'assistant' and b.get('type') == 'tool_use' and b.get('name') in MOD_TOOLS:
                ours.add((path, b.get('id')))
            elif o.get('type') == 'user' and inside and b.get('type') == 'tool_result' and (path, b.get('tool_use_id')) in ours:
                out['recall_calls'] += 1
                out['recall_chars'] += cc_token_audit.result_chars(b.get('content'))
            elif (o.get('type') == 'user' and inside and b.get('type') == 'text' and isinstance(b.get('text'), str)
                  and b['text'].lstrip().startswith(HANDOFF_START)):  # a message that only mentions the marker is no handoff
                text = b['text']
                start = text.index(HANDOFF_START)
                end = text.find(HANDOFF_END, start)
                out['handoffs'] += 1
                out['handoff_chars'] += (end + len(HANDOFF_END) if end >= 0 else len(text)) - start
    return out, visit


def _usd(x):
    return '—' if x is None else '$%.2f' % x


def _pct(x):
    return '%.1f%%' % (100 * x)


def _int(x):
    return '{:,.0f}'.format(x).replace(',', ' ')


def _memory_rows(m, rule, memory_tokens, compression):
    """The section of the note on the memory in the context (stage 3)."""
    each = t('stats_mem_each', calls=m['main_calls'])
    rows = [(t('stats_mem_rule'), each, _int(rule), '—'), (t('stats_mem_index'), each, _int(memory_tokens) if memory_tokens else '—', '—'),
            (t('stats_mem_recall'), t('stats_mem_calls', n=m['recall_calls']), _int(m['recall_tokens']),
             t('stats_mem_cost', usd=_usd(m['recall_cost']), share=_pct(m['recall_share']))),
            (t('stats_mem_handoff'), t('stats_mem_times', n=m['handoffs']), _int(m['handoff_tokens']), '—')]
    return (['', t('stats_mem_title'), '', t('stats_mem_columns'), '|---|---|---|---|'] + ['| %s | %s | %s | %s |' % r for r in rows] +
            ['', t('stats_mem_compression', level=compression)])


def note_markdown(m, host, today, limits, rule_chars, memory_tokens=0, compression='off'):
    """One computer's note for one date: the figures in the frontmatter (the summary reads them), the comparison with the baseline below."""
    front = {'host': host, 'date': today, **m, 'memory_index_tokens': memory_tokens, 'compression': compression}
    week = next((l['percentUsed'] for l in limits if l.get('kind') == 'seven_day'), None)
    b = BASELINE
    rows = [(t('stats_usd'), _usd(b['usd_per_owner_msg']), _usd(m['usd_per_owner_msg'])),
            (t('stats_rewrite'), _pct(b['rewrite_share']), _pct(m['rewrite_share'])),
            (t('stats_idle'), _pct(b['idle60_share']), _pct(m['idle60_share'])),
            (t('stats_context'), _int(b['median_main_context']), _int(m['median_main_context'])),
            (t('stats_weekly'), t('stats_weekly_base'), '—' if week is None else '%s%%' % week)]
    rule = math.ceil(rule_chars / cc_token_audit.CHARS_PER_TOKEN)
    return '\n'.join(
        ['---'] + ['%s: %s' % (k, 'null' if v is None else round(v, 4) if isinstance(v, float) else v) for k, v in front.items()] + ['---', '',
         t('stats_title', today=today, host=host), '',
         t('stats_period', since=m['since'], until=m['until'] or today, owner=m['owner_msgs'], calls=m['main_calls'], cost=_usd(m['cost_main'])), '',
         t('stats_columns'), '|---|---|---|'] + ['| %s | %s | %s |' % r for r in rows] +
        ([''] + [t('stats_limits') + ', '.join('%s %s%%' % (l['kind'], l['percentUsed']) for l in limits)] if limits else []) +
        ['', t('stats_rule', rule=rule, calls=m['main_calls'], total=_int(rule * m['main_calls']))] +
        _memory_rows(m, rule, memory_tokens, compression)) + '\n'
