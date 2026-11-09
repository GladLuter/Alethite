import { expect, test } from 'claude-code/testing'
import { isStale, price, statusText } from '../hooks/guard'
import { setLanguage } from '../hooks/text'

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
const snap = (over = {}) => ({ id: 'id', model: 'claude-opus-5-5', now, tokens: 512_000, at: now - 6.2 * 3_600_000,
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
