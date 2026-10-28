// Pure helpers: no engine calls, so the test runs them alone.
import type { Snap } from '../types'
import { T } from './text'

export const CACHE_MS = 3_600_000

type Row = [model: string, write1h: number, read: number]

const SONNET5: Row = ['sonnet-5', 4, 0.2] // also the price of an unknown model

// USD per MTok, most specific first; same numbers as cc_token_audit.py, change both
const PRICES: Row[] = [
  ['opus-5-5', 8, 0.2], ['opus-5', 10, 0.5], ['opus-4', 10, 0.5],
  ['sonnet-5-5', 4, 0.1], SONNET5, ['sonnet-4', 6, 0.3],
  ['haiku-5-5', 0.2, 0.01], ['haiku-4-5', 2, 0.1],
  ['fable-5-1', 20, 0.25], ['fable-5', 20, 1],
]
const HAIKU55_LONG: Row = ['haiku-5-5', 1, 0.05] // haiku-5-5 with a context over 100k

export const price = (model: string, tokens: number, isExpired: boolean) => {
  const id = model.toLowerCase().replace(/[^a-z0-9]+/g, '-')
  const [, write, read] = id.includes('haiku-5-5') && tokens > 100_000 ? HAIKU55_LONG : PRICES.find(([key]) => id.includes(key)) ?? SONNET5
  return (tokens / 1e6) * (isExpired ? write : read)
}

export const cacheState = (at: number | null, now: number) =>
  at === null ? 'unknown' : now - at >= CACHE_MS ? 'expired' : 'warm'

const k = (tokens: number | null) => (tokens === null ? '?' : String(Math.round(tokens / 1000)))

export const statusText = (tokens: number | null, at: number | null, now: number) => {
  const state = cacheState(at, now)
  const left = Math.ceil((CACHE_MS - (now - (at ?? now))) / 60_000)
  const cache = state === 'warm' ? T.cacheLeft(left) : state === 'expired' ? T.cacheExpired : T.cacheUnknown
  return T.ctx(`${k(tokens)}${tokens === null ? '' : 'k'}`, cache)
}

export const isStale = (s: Snap, minTokens: number, idleMinutes: number) =>
  s.at !== null && (s.tokens ?? 0) > minTokens && s.now - s.at > idleMinutes * 60_000

const ago = (ms: number) => {
  const min = Math.floor(ms / 60_000)
  return min < 1 ? T.agoNow : min < 60 ? T.agoMin(min) : min < 2880 ? T.agoHours(Math.floor(min / 60)) : T.agoDays(Math.floor(min / 1440))
}

export const bandLines = (s: Snap, showLimits: boolean) => {
  const state = cacheState(s.at, s.now)
  const cost = (isExpired: boolean) => `$${price(s.model, s.tokens ?? 0, isExpired).toFixed(2)}`
  const hot = T.sendHot(cost(false))
  const cold = T.sendCold(cost(true))
  const send = state === 'expired' ? cold : state === 'warm' ? hot : T.sendEither(hot, cold)
  return [
    T.head(k(s.tokens), s.at === null ? '?' : ago(s.now - s.at), T.bandCache[state]),
    ...(s.tokens === null ? [] : [T.send(send)]),
    ...(showLimits && s.limits.length ? [T.limits(s.limits.map(l => T.limit(T.limitNames[l.kind] ?? l.kind, l.percentUsed)).join(' · '))] : []),
  ]
}
