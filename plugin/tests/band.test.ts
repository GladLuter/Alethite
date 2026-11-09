import { expect, test } from 'claude-code/testing'
import { baseTrouble, columns, columnsSvg, delayAt, fitTokens, lineTokens, money } from '../hooks/band'
import { setLanguage } from '../hooks/text'

setLanguage('ru')

const now = 10_000_000_000
const snap = (over = {}) => ({ id: 'id', model: 'claude-opus-5-5', now, tokens: 120_000, at: now - 37 * 60_000,
  limits: [{ kind: 'five_hour', percentUsed: 37, resetsAt: new Date(now + 3_600_000).toISOString() }, { kind: 'seven_day', percentUsed: 12 }], ...over })
const status = (over = {}) => ({ convs: 1539, size_mb: 99.05, vectors: { with_vectors: 19557, total: 19557, failed: 0 },
  encryption: { on: true, db: 'efs', key: 'ok' }, sync: { last: { at: '2026-11-09T08:17:06Z', errors: 0, error: null } }, job: { at: '2026-11-09T08:15:26Z', errors: [] }, ...over })
const savings = (over = {}) => ({ last: { usd: 1.24, old_tokens: 303_000, new_tokens: 61_000 }, worker: { usd: 18.7, links: 7, calls: 412 }, project: { usd: 40.1, links: 17 }, ...over })
const info = (over = {}) => ({ status: status(), savings: savings(), ...over })

test('line: size, cache, price here, savings', () => {
  const t = lineTokens(snap(), info(), false)
  expect(t.map(x => x.key)).toEqual(['size', 'cache', 'send', 'saved'])
  expect(t.map(x => x.text)).toEqual(['120k', 'кэш ещё 23 мин', 'сюда ≈ $0.02', 'сэкономлено до $18.70'])
})
test('line: stale lit warns the size and prices the cold write; no links, no savings; trouble adds base', () => {
  const t = lineTokens(snap(), info({ savings: savings({ worker: { usd: 0, links: 0, calls: 0 } }), status: status({ encryption: { on: true, db: 'efs', key: 'missing' } }) }), true)
  expect(t.map(x => x.key)).toEqual(['size', 'cache', 'send', 'base'])
  expect([t[0]?.tone, t[2]?.tone, t[2]?.text]).toEqual(['warning', 'warning', 'сюда ≈ $0.96'])
})
test('fitTokens: saved goes first, then send, base stays', () => {
  const t = [...lineTokens(snap(), info({ status: status({ index_problem: 'x' }) }), false)]
  expect(fitTokens(t, 200).length).toBe(5)
  expect(fitTokens(t, 55).map(x => x.key)).toEqual(['size', 'cache', 'send', 'base'])
  expect(fitTokens(t, 40).map(x => x.key)).toEqual(['size', 'cache', 'base'])
  expect(fitTokens(t, 10).map(x => x.key)).toContain('base')
})
test('money: saved is plain, dearer is an error', () => {
  expect(money(1.24)).toEqual({ text: '$1.24' })
  expect(money(-0.4).tone).toBe('error')
  expect(money(-0.4).text).toContain('0.40')
})
test('baseTrouble', () => {
  expect(baseTrouble(null)).toBe(false)
  expect(baseTrouble(status())).toBe(false)
  expect(baseTrouble(status({ encryption: { on: true, db: 'efs', key: 'missing' } }))).toBe(true)
  expect(baseTrouble(status({ index_problem: 'locked' }))).toBe(true)
  expect(baseTrouble(status({ vectors: { with_vectors: 5, total: 9, failed: 0 } }))).toBe(true)
  expect(baseTrouble(status({ vectors: { with_vectors: 9, total: 9, failed: 2 } }))).toBe(true)
  expect(baseTrouble(status({ sync: { last: { errors: 1 } } }))).toBe(true)
  expect(baseTrouble(status({ job: { errors: [{ where: 'sync', error: 'x' }] } }))).toBe(true)
})
test('columns: limits only when shown and present; unknown is ?, no handoffs is a dash', () => {
  const keys = (c: ReturnType<typeof columns>) => c.map(x => x.key)
  expect(keys(columns(snap(), info(), true))).toEqual(['chat', 'last', 'worker', 'limits', 'base'])
  expect(keys(columns(snap(), info(), false))).toEqual(['chat', 'last', 'worker', 'base'])
  expect(keys(columns(snap({ limits: [] }), info(), true))).toEqual(['chat', 'last', 'worker', 'base'])
  const none = columns(snap(), null, true)
  expect(none.find(c => c.key === 'base')?.value).toBe('?')
  const bare = columns(snap(), info({ savings: savings({ last: null }) }), true)
  expect(bare.find(c => c.key === 'last')?.value).toBe('—')
  const dear = columns(snap(), info({ savings: savings({ last: { usd: -0.4, old_tokens: 61_000, new_tokens: 62_000 } }) }), true)
  expect(dear.find(c => c.key === 'last')?.tone).toBe('error')
  expect(columns(snap(), info(), true).find(c => c.key === 'worker')?.value).toBe('до $18.70')
})
test('delayAt: the latest reset at 90% or more, plus 5 minutes', () => {
  const at = (ms: number) => new Date(ms).toISOString()
  const day = 86_400_000
  expect(delayAt([{ kind: 'five_hour', percentUsed: 100, resetsAt: at(now + 3_600_000) }, { kind: 'seven_day', percentUsed: 95, resetsAt: at(now + 3 * day) }], now)).toBe(now + 3 * day + 300_000)
  expect(delayAt([{ kind: 'five_hour', percentUsed: 89, resetsAt: at(now + 3_600_000) }], now)).toBeNull()
  expect(delayAt([{ kind: 'five_hour', percentUsed: 100 }], now)).toBeNull()
  expect(delayAt([{ kind: 'five_hour', percentUsed: 100, resetsAt: 'soon' }], now)).toBeNull()
  expect(delayAt([{ kind: 'five_hour', percentUsed: 100, resetsAt: at(now - 3_600_000) }], now)).toBeNull()
  expect(delayAt([], now)).toBeNull()
})
test('columnsSvg: values, and the error color class', () => {
  const svg = columnsSvg(columns(snap(), info({ savings: savings({ last: { usd: -0.4, old_tokens: 1, new_tokens: 2 } }) }), true))
  expect(svg).toContain('до $18.70')
  expect(svg).toContain('class="v error"')
  expect(svg).toContain('#c62828')
})
