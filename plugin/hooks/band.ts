// Pure side of the band: the one line, the Details columns, the time for the delayed worker. No `$`, so the tests call it directly.
import type { Info, Snap } from '../types'
import { cacheState, minutesLeft, price, size } from './guard'
import { T } from './text'

export type Tone = 'error' | 'warning'
export type Token = { key: 'size' | 'cache' | 'send' | 'saved' | 'base'; text: string; tone?: Tone }
export type Col = { key: string; label: string; value: string; sub: string[]; subTone?: Record<number, Tone>; tone?: Tone }

export const SHIELD = '🛡' // the title shortened to this when the row is tight
const usd = (n: number) => `$${n.toFixed(2)}`
const asRec = (v: unknown) => (v && typeof v === 'object' ? (v as Record<string, any>) : undefined)
// the clock time of an ISO stamp, local; with the weekday when it is not the day of `now`
export const when = (iso: string, now: number) => {
  const d = new Date(iso)
  if (Number.isNaN(+d)) return '?'
  const time = d.toLocaleTimeString(T.locale, { hour: '2-digit', minute: '2-digit' })
  return d.toDateString() === new Date(now).toDateString() ? time : `${d.toLocaleDateString(T.locale, { weekday: 'short' })} ${time}`
}

const cacheText = (s: Snap) => {
  const state = cacheState(s.at, s.now)
  return state === 'warm' ? T.bandWarm(minutesLeft(s.at, s.now)) : T.bandCache[state]
}

export const money = (n: number): { text: string; tone?: Tone } => (n >= 0 ? { text: usd(n) } : { text: T.dearer(usd(-n)), tone: 'error' })

// Something the owner must act on in the base: an unusable or broken index, a key that is not ok, a real error of the last sync or of the background job.
// Pending vectors, failed-record sentinels and the job's in-progress marker are normal and stay out (the Base column shows N/M); a stored sync error counts only while the sync is on.
export const baseTrouble = (status: Record<string, unknown> | null, isSyncOn: boolean) => {
  const s = asRec(status)
  if (!s) return false
  const jobErrors = Array.isArray(s.job?.errors) ? s.job.errors.filter((e: any) => e?.where !== 'job' && (isSyncOn || e?.where !== 'sync')) : []
  return (
    (typeof s.index_problem === 'string' && s.index_problem !== '') ||
    (s.encryption?.on === true && s.encryption.key !== 'ok') ||
    (isSyncOn && s.sync?.last?.errors > 0) ||
    jobErrors.length > 0
  )
}

export const lineTokens = (s: Snap, info: Info | null, isStaleLit: boolean): Token[] => {
  const state = cacheState(s.at, s.now)
  const warn = isStaleLit ? ('warning' as const) : undefined
  const worker = info?.savings?.worker
  const saved = worker && worker.links > 0 ? money(worker.usd) : undefined
  return [
    { key: 'size', text: size(s.tokens), tone: warn },
    { key: 'cache', text: cacheText(s) },
    ...(s.tokens === null ? [] : [{ key: 'send' as const, text: T.sendHere(usd(price(s.model, s.tokens, state === 'expired' || isStaleLit))), tone: warn }]),
    ...(saved ? [{ key: 'saved' as const, text: saved.tone ? saved.text : T.saved(saved.text), tone: saved.tone }] : []),
    ...(baseTrouble(info?.status ?? null, info?.isSyncOn ?? false) ? [{ key: 'base' as const, text: T.baseTrouble, tone: 'warning' as const }] : []),
  ]
}

// The cells the tokens take once joined with " · ".
export const tokensWidth = (tokens: Token[]) => tokens.map(x => x.text).join(' · ').length

// Drops the savings, the price, the cache and the size, in that order, until the line fits; the base alarm stays.
export const fitTokens = (tokens: Token[], budget: number) => {
  let out = tokens
  for (const key of ['saved', 'send', 'cache', 'size']) if (tokensWidth(out) > budget) out = out.filter(t => t.key !== key)
  return out
}

// "up to $X" for a saving, the dearer form (an error) as it is
const upTo = (m: { text: string; tone?: Tone }) => (m.tone ? m.text : T.upTo(m.text))

export const columns = (s: Snap, info: Info | null, showLimits: boolean): Col[] => {
  const state = cacheState(s.at, s.now)
  const chat: Col = {
    key: 'chat', label: T.col.chat, value: `${size(s.tokens)} · ${cacheText(s)}`,
    sub: [
      ...(s.tokens === null ? [] : [`${T.sendHere(usd(price(s.model, s.tokens, false)))} / ${T.coldPrice(usd(price(s.model, s.tokens, true)))}`]),
      ...(s.cost === null ? [] : [T.spent(usd(s.cost))]),
    ],
  }

  const sv = info?.savings
  const last = sv?.last ? money(sv.last.usd) : undefined
  const lastCol: Col = {
    key: 'last', label: T.col.last, value: sv ? (last?.text ?? '—') : '?', tone: last?.tone,
    sub: sv?.last ? [`${size(sv.last.old_tokens)} → ${size(sv.last.new_tokens)}`] : [],
  }

  const w = sv?.worker
  const wm = w && w.links > 0 ? money(w.usd) : undefined
  const workerCol: Col = {
    key: 'worker', label: T.col.worker, value: sv ? (wm ? upTo(wm) : '—') : '?', tone: wm?.tone,
    sub: sv ? [...(w && w.links > 0 ? [T.workerSub(w.links, w.calls)] : []), ...(sv.project ? [T.projectSub(upTo(money(sv.project.usd)), sv.project.links)] : [])] : [],
  }
  if (sv?.project && money(sv.project.usd).tone) workerCol.subTone = { [workerCol.sub.length - 1]: 'error' } // the project line is the last one

  const [first, ...rest] = s.limits
  const limitsCol: Col[] = showLimits && first
    ? [{
        key: 'limits', label: T.col.limits, value: T.limit(T.limitNames[first.kind] ?? first.kind, first.percentUsed),
        sub: [
          ...(first.resetsAt ? [T.resets(when(first.resetsAt, s.now))] : []),
          ...rest.map(l => `${T.limit(T.limitNames[l.kind] ?? l.kind, l.percentUsed)}${l.resetsAt ? `, ${when(l.resetsAt, s.now)}` : ''}`),
        ],
      }]
    : []

  const st = asRec(info?.status)
  const v = asRec(st?.vectors)
  const enc = asRec(st?.encryption)
  const at = (iso: unknown) => (typeof iso === 'string' ? when(iso, s.now) : '—')
  const isRead = st !== undefined && typeof st.convs === 'number' // a failed status carries only the reason
  const baseCol: Col = {
    key: 'base', label: T.col.base, value: isRead ? T.baseConvs(st.convs) : '?', tone: baseTrouble(info?.status ?? null, info?.isSyncOn ?? false) ? 'warning' : undefined,
    sub: isRead
      ? [
          ...(v ? [T.baseVectors(v.with_vectors, v.total)] : []),
          T.baseSize(Math.round(st.size_mb), enc?.on ? (enc.db === 'sqlcipher' ? 'SQLCipher' : enc.db === 'efs' ? 'EFS' : '—') : '—'),
          T.baseTimes(at(st.sync?.last?.at), at(st.job?.at)),
        ]
      : [],
  }
  return [chat, lastCol, workerCol, ...limitsCol, baseCol]
}

// The latest reset among the limits at 90% or more, plus 5 minutes (so the worker does not hit the next limit); null when there is none or it has passed.
export const delayAt = (limits: Snap['limits'], now: number) => {
  const resets = limits.flatMap(l => (l.percentUsed >= 90 && l.resetsAt ? [Date.parse(l.resetsAt)] : [])).filter(Number.isFinite)
  if (!resets.length) return null
  const at = Math.max(...resets) + 300_000
  return at > now ? at : null
}

// A thin line the desktop draws above our band so it does not merge with the band drawn above it. It cannot read the app's theme, so it follows the system's.
export const RULE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="12" viewBox="0 0 1200 12"><style>.d{stroke:#1f1e1d;stroke-opacity:.12}@media (prefers-color-scheme:dark){.d{stroke:#fff;stroke-opacity:.14}}</style><line class="d" x1="0" y1="6" x2="1200" y2="6" stroke-width="2"/></svg>'
