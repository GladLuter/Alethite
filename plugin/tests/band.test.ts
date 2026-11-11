import { expect, test } from 'claude-code/testing'
import { baseTrouble, columns, delayAt, fitTokens, lineTokens, money } from '../hooks/band'
import { setLanguage } from '../hooks/text'

setLanguage('ru')

const now = 10_000_000_000
const snap = (over = {}) => ({ id: 'id', model: 'claude-opus-5-5', now, tokens: 120_000, at: now - 37 * 60_000, cost: null as number | null,
  limits: [{ kind: 'five_hour', percentUsed: 37, resetsAt: new Date(now + 3_600_000).toISOString() }, { kind: 'seven_day', percentUsed: 12 }], ...over })
const status = (over = {}) => ({ convs: 1539, size_mb: 99.05, vectors: { with_vectors: 19557, total: 19557, failed: 0 },
  encryption: { on: true, db: 'efs', key: 'ok' }, sync: { last: { at: '2026-11-09T08:17:06Z', errors: 0, error: null } }, job: { at: '2026-11-09T08:15:26Z', errors: [] }, ...over })
const savings = (over = {}) => ({ last: { usd: 1.24, old_tokens: 303_000, new_tokens: 61_000 }, worker: { usd: 18.7, links: 7, calls: 412 }, project: { usd: 40.1, links: 17 }, ...over })
const info = (over = {}) => ({ status: status(), savings: savings(), isSyncOn: true, ...over })

test('line: size, cache, price here, savings', () => {
  const t = lineTokens(snap(), info(), false)
  expect(t.map(x => x.key)).toEqual(['size', 'cache', 'send', 'saved'])
  expect(t.map(x => x.text)).toEqual(['120k', 'кэш 23 мин', 'сюда ≈ $0.02', 'экономия ≤ $18.70'])
})
test('line: stale lit warns the size and prices the cold write; no links, no savings; trouble adds base', () => {
  const t = lineTokens(snap(), info({ savings: savings({ worker: { usd: 0, links: 0, calls: 0 } }), status: status({ encryption: { on: true, db: 'efs', key: 'missing' } }) }), true)
  expect(t.map(x => x.key)).toEqual(['size', 'cache', 'send', 'base'])
  expect([t[0]?.tone, t[2]?.tone, t[2]?.text]).toEqual(['warning', 'warning', 'сюда ≈ $0.96'])
})
test('fitTokens: saved goes first, then send, cache, size; base stays', () => {
  const t = [...lineTokens(snap(), info({ status: status({ index_problem: 'x' }) }), false)]
  expect(fitTokens(t, 200).length).toBe(5)
  expect(fitTokens(t, 55).map(x => x.key)).toEqual(['size', 'cache', 'send', 'base'])
  expect(fitTokens(t, 40).map(x => x.key)).toEqual(['size', 'cache', 'base'])
  expect(fitTokens(t, 20).map(x => x.key)).toEqual(['size', 'base'])
  expect(fitTokens(t, 10).map(x => x.key)).toEqual(['base'])
  expect(fitTokens(t, 0).map(x => x.key)).toEqual(['base'])
})
test('money: saved is plain, dearer is an error', () => {
  expect(money(1.24)).toEqual({ text: '$1.24' })
  expect(money(-0.4).tone).toBe('error')
  expect(money(-0.4).text).toContain('0.40')
})
test('baseTrouble: only what the owner must act on', () => {
  const is = (over: object, on = true) => baseTrouble(status(over), on)
  expect(baseTrouble(null, true)).toBe(false)
  expect(is({})).toBe(false)
  expect(is({ encryption: { on: true, db: 'efs', key: 'missing' } })).toBe(true)
  expect(is({ index_problem: 'locked' })).toBe(true)
  expect(baseTrouble({ index_problem: 'status down' }, false)).toBe(true) // a status that failed carries only the reason
  expect(is({ sync: { last: { errors: 1 } } })).toBe(true)
  expect(is({ job: { errors: [{ where: 'sync', error: 'x' }] } })).toBe(true)
  // normal operation stays quiet: vectors pending after a recall, a failed-record sentinel, the job's in-progress marker
  expect(is({ vectors: { with_vectors: 5, total: 9, failed: 0 } })).toBe(false)
  expect(is({ vectors: { with_vectors: 9, total: 9, failed: 2 } })).toBe(false)
  expect(is({ job: { errors: [{ where: 'job', error: 'unfinished' }] } })).toBe(false)
  // an old sync error with the sync off
  expect(is({ sync: { last: { errors: 1 } } }, false)).toBe(false)
  expect(is({ job: { errors: [{ where: 'sync', error: 'x' }] } }, false)).toBe(false)
  expect(is({ job: { errors: [{ where: 'embed', error: 'x' }] } }, false)).toBe(true)
})
test('columns: a status that failed is a ? with the alarm lit', () => {
  const c = columns(snap(), info({ status: { index_problem: 'broken' } }), false)
  const base = c.find(x => x.key === 'base')!
  expect([base.value, base.sub, base.tone]).toEqual(['?', [], 'warning'])
  expect(lineTokens(snap(), info({ status: { index_problem: 'broken' } }), false).map(x => x.key)).toContain('base')
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
test('columns: sub-lines, project null, reset times, base facts', () => {
  const col = (c: ReturnType<typeof columns>, key: string) => c.find(x => x.key === key)!
  const c = columns(snap(), info(), true)
  expect(col(c, 'chat').value).toBe('120k · кэш 23 мин')
  expect(col(c, 'chat').sub).toEqual(['сюда ≈ $0.02 / холодно $0.96'])
  expect(col(c, 'last').value).toBe('$1.24')
  expect(col(c, 'last').sub).toEqual(['303k → 61k'])
  expect(col(c, 'worker').sub).toEqual(['передач: 7 · вызовов: 412', 'проект: до $40.10 (17)'])
  expect(col(c, 'limits').sub).toHaveLength(2)
  expect(col(c, 'limits').sub[0]).toMatch(/^сброс \d\d:\d\d$/)
  expect(col(c, 'base').value).toBe('разговоров: 1539')
  expect(col(c, 'base').sub[0]).toBe('векторы 19557/19557')
  expect(col(c, 'base').sub[1]).toContain('99')
  expect(col(c, 'base').sub[1]).toContain('EFS')
  expect(col(c, 'base').sub[2]).toMatch(/\d\d:\d\d.*\d\d:\d\d/)
  expect(col(columns(snap(), info({ savings: savings({ project: null }) }), true), 'worker').sub).toEqual(['передач: 7 · вызовов: 412'])
  expect(col(columns(snap({ tokens: null }), info(), true), 'chat').sub).toEqual([])
  expect(col(columns(snap({ cost: 3.2 }), info(), true), 'chat').sub).toEqual(['сюда ≈ $0.02 / холодно $0.96', 'потрачено $3.20'])
  expect(col(columns(snap({ tokens: null, cost: 0 }), info(), true), 'chat').sub).toEqual(['потрачено $0.00'])
})
test('columns: a negative project sum is the dearer form, not "up to $-0.40"', () => {
  const w = columns(snap(), info({ savings: savings({ project: { usd: -0.4, links: 4 } }) }), true).find(x => x.key === 'worker')!
  expect(w.sub[1]).toBe('проект: дороже на $0.40 (4)')
  expect(w.subTone).toEqual({ 1: 'error' })
})
test('columns: a reset on another day carries the weekday; a bad stamp is ?', () => {
  const far = columns(snap({ limits: [{ kind: 'seven_day', percentUsed: 50, resetsAt: new Date(now + 3 * 86_400_000).toISOString() }] }), info(), true)
  expect(far.find(x => x.key === 'limits')!.sub[0]).toMatch(/^сброс \S+ \d\d:\d\d$/)
  const bad = columns(snap({ limits: [{ kind: 'seven_day', percentUsed: 50, resetsAt: 'soon' }] }), info(), true)
  expect(bad.find(x => x.key === 'limits')!.sub[0]).toBe('сброс ?')
})
test('line: dearer worker is an error and has no "saved" prefix', () => {
  const t = lineTokens(snap(), info({ savings: savings({ worker: { usd: -0.4, links: 2, calls: 3 } }) }), false)
  const saved = t.find(x => x.key === 'saved')!
  expect(saved.tone).toBe('error')
  expect(saved.text).toContain('0.40')
  expect(saved.text).not.toContain('сэкономлено')
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
