// Pure side of the band: the one line, the Details columns, the time for the delayed worker. No `$`, so the tests call it directly.
import type { Info, Snap } from '../types'
import { cacheState, CACHE_MS, price } from './guard'
import { T } from './text'

export type Tone = 'error' | 'warning' | 'success'
export type Token = { key: 'size' | 'cache' | 'send' | 'saved' | 'base'; text: string; tone?: Tone }
export type Col = { key: string; label: string; value: string; sub: string[]; tone?: Tone }

const usd = (n: number) => `$${n.toFixed(2)}`
const asRec = (v: unknown) => (v && typeof v === 'object' ? (v as Record<string, any>) : undefined)
// the clock time of an ISO stamp, local; with the weekday when it is not the day of `now`
const when = (iso: string, now: number) => {
  const d = new Date(iso)
  if (Number.isNaN(+d)) return '?'
  const time = d.toLocaleTimeString(T.locale, { hour: '2-digit', minute: '2-digit' })
  return d.toDateString() === new Date(now).toDateString() ? time : `${d.toLocaleDateString(T.locale, { weekday: 'short' })} ${time}`
}

const cacheText = (s: Snap) => {
  const state = cacheState(s.at, s.now)
  return state === 'warm' ? T.bandWarm(Math.ceil((CACHE_MS - (s.now - (s.at ?? s.now))) / 60_000)) : T.bandCache[state]
}

export const money = (n: number): { text: string; tone?: Tone } => (n >= 0 ? { text: usd(n) } : { text: T.dearer(usd(-n)), tone: 'error' })

// Something to look at in the base: an unusable index, a key that is not ok, vectors missing or failed, errors of the last sync or of the background job.
export const baseTrouble = (status: Record<string, unknown> | null) => {
  const s = asRec(status)
  if (!s) return false
  const v = asRec(s.vectors)
  return (
    (typeof s.index_problem === 'string' && s.index_problem !== '') ||
    (s.encryption?.on === true && s.encryption.key !== 'ok') ||
    (v !== undefined && (v.failed > 0 || v.with_vectors < v.total)) ||
    s.sync?.last?.errors > 0 ||
    (Array.isArray(s.job?.errors) && s.job.errors.length > 0)
  )
}

export const lineTokens = (s: Snap, info: Info | null, isStaleLit: boolean): Token[] => {
  const state = cacheState(s.at, s.now)
  const warn = isStaleLit ? ('warning' as const) : undefined
  const worker = info?.savings?.worker
  const saved = worker && worker.links > 0 ? money(worker.usd) : undefined
  return [
    { key: 'size', text: s.tokens === null ? '?' : `${Math.round(s.tokens / 1000)}k`, tone: warn },
    { key: 'cache', text: cacheText(s) },
    ...(s.tokens === null ? [] : [{ key: 'send' as const, text: T.sendHere(usd(price(s.model, s.tokens, state === 'expired' || isStaleLit))), tone: warn }]),
    ...(saved ? [{ key: 'saved' as const, text: saved.tone ? saved.text : T.saved(saved.text), tone: saved.tone }] : []),
    ...(baseTrouble(info?.status ?? null) ? [{ key: 'base' as const, text: T.baseTrouble, tone: 'warning' as const }] : []),
  ]
}

// Drops the savings, then the price, until the line fits; the base alarm stays.
export const fitTokens = (tokens: Token[], budget: number) => {
  const long = (t: Token[]) => t.map(x => x.text).join(' · ').length > budget
  let out = tokens
  for (const key of ['saved', 'send']) if (long(out)) out = out.filter(t => t.key !== key)
  return out
}

export const columns = (s: Snap, info: Info | null, showLimits: boolean): Col[] => {
  const state = cacheState(s.at, s.now)
  const k = s.tokens === null ? '?' : `${Math.round(s.tokens / 1000)}k`
  const chat: Col = {
    key: 'chat', label: T.col.chat, value: `${k} · ${cacheText(s)}`,
    sub: s.tokens === null ? [] : [`${T.sendHere(usd(price(s.model, s.tokens, false)))} / ${T.coldPrice(usd(price(s.model, s.tokens, true)))}`],
  }

  const sv = info?.savings
  const last = sv?.last ? money(sv.last.usd) : undefined
  const lastCol: Col = {
    key: 'last', label: T.col.last, value: sv ? (last?.text ?? '—') : '?', tone: last?.tone,
    sub: sv?.last ? [`${Math.round(sv.last.old_tokens / 1000)}k → ${Math.round(sv.last.new_tokens / 1000)}k`] : [],
  }

  const w = sv?.worker
  const wm = w && w.links > 0 ? money(w.usd) : undefined
  const workerCol: Col = {
    key: 'worker', label: T.col.worker, value: sv ? (wm ? (wm.tone ? wm.text : T.upTo(wm.text)) : '—') : '?', tone: wm?.tone,
    sub: sv ? [...(w && w.links > 0 ? [T.workerSub(w.links, w.calls)] : []), ...(sv.project ? [T.projectSub(usd(sv.project.usd), sv.project.links)] : [])] : [],
  }

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
  const baseCol: Col = {
    key: 'base', label: T.col.base, value: st ? T.baseConvs(st.convs) : '?', tone: baseTrouble(info?.status ?? null) ? 'warning' : undefined,
    sub: st
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
  const at = Math.max(...resets) + 300_000
  return resets.length && at > now ? at : null
}

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const fit = (s: string, max: number) => (s.length > max ? `${s.slice(0, Math.max(1, max - 1))}…` : s)
const FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif"
// The image cannot read the app's theme, so it follows the system's.
// shortcut: an app theme that differs from the system theme gets the wrong text color; pass the theme in if that bites.
const STYLE = `.l,.s{fill:#87867f;font:12px ${FONT}}.v{fill:#1f1e1d;font:600 16px ${FONT}}.d{stroke:#1f1e1d;stroke-opacity:.12}.warning{fill:#a86400}.success{fill:#2e7d32}.error{fill:#c62828}@media (prefers-color-scheme:dark){.v{fill:#ecebe6}.d{stroke:#fff;stroke-opacity:.14}.warning{fill:#e3a03d}.success{fill:#6abf69}.error{fill:#ef6a5a}}`

// The Details columns as one SVG for the desktop, where box characters break in a proportional font.
export const columnsSvg = (cols: Col[], width = 720) => {
  const colW = width / Math.max(1, cols.length)
  const height = 52 + 15 * Math.max(1, ...cols.map(c => c.sub.length))
  let body = `<line class="d" x1="0" y1="0.5" x2="${width}" y2="0.5"/>`
  cols.forEach((c, i) => {
    const x = i * colW + (i === 0 ? 0 : 12)
    if (i > 0) body += `<line class="d" x1="${i * colW}" y1="14" x2="${i * colW}" y2="${height - 2}"/>`
    body += `<text class="l" x="${x}" y="24">${esc(c.label)}</text>`
    body += `<text class="v${c.tone ? ` ${c.tone}` : ''}" x="${x}" y="44">${esc(fit(c.value, Math.floor((colW - 16) / 9)))}</text>`
    c.sub.forEach((line, j) => (body += `<text class="s" x="${x}" y="${61 + 15 * j}">${esc(fit(line, Math.floor((colW - 16) / 6.2)))}</text>`))
  })
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><style>${STYLE}</style>${body}</svg>`
}
