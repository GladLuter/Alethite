"""The success metrics of the design (§9): cost and cache figures from the token audit, owner messages from the index."""
import math
import cc_token_audit
import memdb
from texts import t

BASELINE = {'usd_per_owner_msg': 5.20, 'rewrite_share': 0.417, 'idle60_share': 0.319, 'median_main_context': 428000}  # weekly limit: t('stats_weekly_base')


def metrics(projects, conn, since, until=None, errors=None):
    """Figures for the API calls on/after `since` (and before `until`) in the transcripts under `projects`.
    What would make them wrong (unreadable transcripts, models without a price) is appended to the `errors` list, as the index reports its own."""
    errors = [] if errors is None else errors
    errors += memdb.update(conn, projects)['errors']  # the owner count comes from the index
    R = cc_token_audit.analyze(projects, since, until=until)
    meta = R['meta']
    if meta['files_unreadable']:
        errors.append({'path': 'audit', 'error': 'files_unreadable: %d' % meta['files_unreadable']})
    if meta['unpriced_model_calls']:
        errors.append({'path': 'audit', 'error': 'unpriced_model_calls: ' + ', '.join('%s=%d' % kv for kv in meta['unpriced_model_calls'].items())})
    cost = R['by_kind'].get('main', {}).get('cost_usd', 0)
    n = memdb.owner_count(conn, since, until)
    main = R['stats']['main']  # {'chains': 0} when the period holds no main chat
    rw = R['cache_rewrites']
    return {'since': since, 'until': until, 'cost_main': cost, 'owner_msgs': n, 'usd_per_owner_msg': cost / n if n else None,
            'rewrite_share': rw['total']['share_of_all_cost'], 'idle60_share': rw['by_cause']['idle >= 60 min']['share_of_all_cost'],
            'median_main_context': main.get('prompt_total_median', 0), 'main_calls': main.get('calls', 0)}


def _usd(x):
    return '—' if x is None else '$%.2f' % x


def _pct(x):
    return '%.1f%%' % (100 * x)


def _int(x):
    return '{:,.0f}'.format(x).replace(',', ' ')


def note_markdown(m, host, today, limits, rule_chars):
    """One computer's note for one date: the figures in the frontmatter (the summary reads them), the comparison with the baseline below."""
    front = {'host': host, 'date': today, **m}
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
        ['', t('stats_rule', rule=rule, calls=m['main_calls'], total=_int(rule * m['main_calls']))]) + '\n'
