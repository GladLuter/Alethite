import { expect, test } from 'claude-code/testing'
import { isStale, price, shouldDenyBackground, shouldStop, statusText } from '../hooks/guard'
import { setLanguage } from '../hooks/text'
import type { Snap } from '../types'

// the Russian texts of stage 1 for one test; the module is this file's own, the plugin's language is not touched
const inRussian = (body: () => void) => {
  setLanguage('ru')
  try {
    body()
  } finally {
    setLanguage('en')
  }
}

const now = 10_000_000_000
const snap = (over: Partial<Snap> = {}): Snap => ({ id: 'id', model: 'claude-opus-5-5', now, tokens: 512_000, at: now - 6.2 * 3_600_000, cost: null,
  limits: [{ kind: 'five_hour', percentUsed: 7 }, { kind: 'seven_day', percentUsed: 70 }], ...over })

test('price: haiku-5-5 over 100k uses the long-context price', () => {
  expect(price('claude-haiku-5-5', 300_000, true)).toBe(0.3)
})
test('stale: both thresholds, defaults 100k and 55 min', () => {
  expect(isStale(snap({ tokens: 120_000, at: now - 56 * 60_000 }), 100_000, 55)).toBe(true)
  expect(isStale(snap({ tokens: 90_000, at: now - 56 * 60_000 }), 100_000, 55)).toBe(false)
  expect(isStale(snap({ tokens: 120_000, at: now - 54 * 60_000 }), 100_000, 55)).toBe(false)
  expect(isStale(snap({ at: null }), 100_000, 55)).toBe(false)
})
test('stale: thresholds are strict, "больше"', () => {
  expect(isStale(snap({ tokens: 100_000, at: now - 56 * 60_000 }), 100_000, 55)).toBe(false)
  expect(isStale(snap({ tokens: 120_000, at: now - 55 * 60_000 }), 100_000, 55)).toBe(false)
  expect(isStale(snap({ tokens: 100_001, at: now - 55 * 60_000 - 1 }), 100_000, 55)).toBe(true)
})
test('status line', () => {
  expect(statusText(290_000, now - 18 * 60_000, now)).toBe('ctx 290k · cache ~42 min')
  expect(statusText(290_000, now - 61 * 60_000, now)).toBe('ctx 290k · cache expired')
  inRussian(() => {
    expect(statusText(290_000, now - 18 * 60_000, now)).toBe('ctx 290k · кэш ~42 мин')
    expect(statusText(290_000, now - 61 * 60_000, now)).toBe('ctx 290k · кэш истёк')
  })
})

test('shouldStop: only a found, idle, not yet stopped agent older than its limit', () => {
  const a = { found: true, age: 900, limit: 300 }
  expect(shouldStop(a, false, false)).toBe(true)
  expect(shouldStop({ ...a, found: false }, false, false)).toBe(false)
  expect(shouldStop(a, true, false)).toBe(false)
  expect(shouldStop(a, false, true)).toBe(false)
  expect(shouldStop({ ...a, age: 300 }, false, false)).toBe(false)
  expect(shouldStop({ ...a, age: null }, false, false)).toBe(false)
})

test('shouldDenyBackground: only an alethite:* type with run_in_background true', () => {
  expect(shouldDenyBackground('alethite:coder', true)).toBe(true)
  expect(shouldDenyBackground('alethite:coder', 'true')).toBe(true)
  expect(shouldDenyBackground('alethite:coder', 'false')).toBe(false)
  expect(shouldDenyBackground('alethite:coder', false)).toBe(false)
  expect(shouldDenyBackground('alethite:coder', undefined)).toBe(false)
  expect(shouldDenyBackground('general-purpose', true)).toBe(false)
  expect(shouldDenyBackground(undefined, true)).toBe(false)
})

test('background given as TRUE or 1 is denied', () => {
  for (const v of ['TRUE', 'True', 1, '1', true, 'true']) expect(shouldDenyBackground('alethite:coder', v)).toBe(true)
  for (const v of [false, 'false', 0, '0', undefined, null, 2]) expect(shouldDenyBackground('alethite:coder', v)).toBe(false)
})
