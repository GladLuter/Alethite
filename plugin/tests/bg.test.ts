import { expect, test } from 'claude-code/testing'
import { delayTimes } from '../hooks/band'
import { bgRow, finishAction, finishText, lastLine } from '../hooks/bg'
import type { BgRun } from '../hooks/bg'
import { setLanguage } from '../hooks/text'

setLanguage('ru')

const now = 10_000_000_000
const reset = new Date(now + 3_600_000).toISOString()
const fine = [{ kind: 'five_hour', percentUsed: 37, resetsAt: reset }]
const full = [{ kind: 'five_hour', percentUsed: 100, resetsAt: reset }]
const ask = (over = {}) => finishAction({ ifCold: 'show', at: now - 30 * 60_000, now, limits: fine, isWorking: false, ...over })
const run = (over = {}): BgRun => ({ dir: 'd', name: 'job', pid: 1, started: now - 102 * 60_000, log: 'C:/l.log', ifCold: 'show', ...over })

test('finishAction: busy waits, warm wakes, cold follows ifCold', () => {
  expect(ask({ isWorking: true })).toBe('wait')
  expect(ask()).toBe('wake')
  expect(ask({ at: now - 59 * 60_000 })).toBe('show')
  expect(ask({ at: now - 59 * 60_000, ifCold: 'new_worker' })).toBe('newWorker')
  expect(ask({ at: null, ifCold: 'new_worker' })).toBe('newWorker')
  expect(ask({ at: now - 58 * 60_000 })).toBe('wake')
  expect(ask({ at: now - 58 * 60_000 - 1 })).toBe('show')
  expect(ask({ ifCold: 'new_worker' })).toBe('wake')
})
test('finishAction: an exhausted limit blocks wake', () => {
  expect(ask({ limits: full })).toBe('show')
  expect(ask({ limits: full, ifCold: 'new_worker' })).toEqual({ afterReset: delayTimes(full, now)[0] })
  expect(ask({ limits: [{ kind: 'five_hour', percentUsed: 100 }], ifCold: 'new_worker' })).toBe('show')
})
test('lastLine', () => {
  expect(lastLine('a\nb\n')).toBe('b')
  expect(lastLine('a\nb')).toBe('a')
  expect(lastLine('b')).toBe('b')
  expect(lastLine('')).toBe('—')
  expect(lastLine('\n\n')).toBe('—')
  expect(lastLine('x'.repeat(200) + '\n').length).toBe(80)
})
test('bgRow: running, failed, lost', () => {
  const r = bgRow(run(), 'rerun exit 0', now)
  expect(r.text).toContain('1 ч 42 мин')
  expect(r.text).toContain('rerun exit 0')
  expect(r.tone).toBeUndefined()
  expect(bgRow(run({ ended: { code: 0, at: now } }), 'x', now).text).toContain('код 0')
  const bad = bgRow(run({ ended: { code: 1, at: now } }), 'x', now)
  expect([bad.tone, bad.text.startsWith('✗')]).toEqual(['error', true])
  expect(bgRow(run({ ended: { code: null, at: now, lost: true } }), 'x', now).tone).toBe('warning')
})
test('finishText: name, code, log, at most 5 tail lines', () => {
  const t = finishText(run({ ended: { code: 3, at: now } }), ['1', '2', '3', '4', '5', '6', '7'], now)
  expect(t).toContain('job')
  expect(t).toContain('код 3')
  expect(t).toContain('C:/l.log')
  expect(t).not.toContain('\n2\n')
  expect(t).toContain('\n7\n')
  const unknown = finishText(run({ ended: { code: null, at: now } }), [], now)
  expect(unknown).toContain('код неизвестно')
  expect(unknown).not.toContain('Последние строки')
  expect(unknown).not.toContain('\n\n')
})
