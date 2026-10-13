import { expect, test } from 'claude-code/testing'
import { bandLines, isStale, modelChoices, price, statusText } from '../hooks/guard'

const now = 10_000_000_000
const snap = (over = {}) => ({ id: 'id', model: 'claude-opus-5-5', now, tokens: 512_000, at: now - 6.2 * 3_600_000,
  limits: [{ kind: 'five_hour', percentUsed: 7 }, { kind: 'seven_day', percentUsed: 70 }], ...over })

test('band: owner example, 512k opus-5-5, expired', () => {
  expect(bandLines(snap(), true)).toEqual([
    'Этот чат: 512 тыс. токенов · последний ответ 6 ч назад · кэш истёк',
    'Отправка сюда ≈ $4.10 (перезапись кэша)',
    'Лимит 5 ч: 7% · неделя: 70%',
  ])
})
test('band: limits line is a setting', () => {
  expect(bandLines(snap(), false).some(l => l.startsWith('Лимит'))).toBe(false)
})
test('price: haiku-5-5 over 100k uses the long-context price', () => {
  expect(price('claude-haiku-5-5', 300_000, true)).toBe(0.3)
})
test('stale: both thresholds, defaults 100k and 55 min', () => {
  expect(isStale(snap({ tokens: 120_000, at: now - 56 * 60_000 }), 100_000, 55)).toBe(true)
  expect(isStale(snap({ tokens: 90_000, at: now - 56 * 60_000 }), 100_000, 55)).toBe(false)
  expect(isStale(snap({ tokens: 120_000, at: now - 54 * 60_000 }), 100_000, 55)).toBe(false)
  expect(isStale(snap({ at: null }), 100_000, 55)).toBe(false)
})
test('status line', () => {
  expect(statusText(290_000, now - 18 * 60_000, now)).toBe('ctx 290k · кэш ~42 мин')
  expect(statusText(290_000, now - 61 * 60_000, now)).toBe('ctx 290k · кэш истёк')
})
test('model choices: same model first, its alias not repeated', () => {
  expect(modelChoices('claude-opus-5-5').map(c => c.model)).toEqual([null, 'sonnet', 'haiku'])
  expect(modelChoices('claude-sonnet-5-5').map(c => c.model)).toEqual([null, 'opus', 'haiku'])
  expect(modelChoices('claude-opus-5-5')[0].label).toContain('claude-opus-5-5')
})
