import { expect, test } from 'claude-code/testing'
import { formatHits, formatRows, recallArgs } from '../hooks/recall'
import { LANGUAGES, T } from '../hooks/text'

test('args: project scope by default, with the embedding model and the compression level', () =>
  expect(recallArgs({ query: 'кэш' }, 'C:\\w', 'embeddinggemma', 'ultra')).toEqual(['recall', '--query=кэш', '--cwd', 'C:\\w', '--limit', '8', '--model', 'embeddinggemma', '--compress', 'ultra']))
test('args: all', () => expect(recallArgs({ query: 'q', scope: 'all', limit: 3 }, 'C:\\w', 'm', 'off')).toEqual(['recall', '--query=q', '--all', '--limit', '3', '--model', 'm', '--compress', 'off']))
test('args: a limit outside the schema is brought into 1..20', () =>
  expect([0, -1, 99, 2.7, Number.NaN].map(limit => recallArgs({ query: 'q', limit }, 'C:\\w', 'm', 'off')[5])).toEqual(['8', '1', '20', '2', '8']))
test('args: query that looks like a flag', () => expect(recallArgs({ query: '--no-verify' }, 'C:\\w', 'm', 'off')[1]).toBe('--query=--no-verify'))
test('args: other wordings, each one item; no strings, blanks, repeats and the query itself left out, three at most', () => {
  const args = recallArgs({ query: 'повторный запуск', also: ['429 limit', '', 7, '429 limit', 'повторный запуск', '-x', 'b', 'c'] }, 'C:\\w', 'm', 'off')
  expect(args.slice(0, 5)).toEqual(['recall', '--query=повторный запуск', '--also=429 limit', '--also=-x', '--also=b'])
  expect(recallArgs({ query: 'q', also: 'not a list' }, 'C:\\w', 'm', 'off')[2]).toBe('--cwd')
})
test('hits format', () =>
  expect(formatHits([{ conv: 'S', parent: '', ts: '2026-10-10T08:00:00Z', kind: 'owner', pos: 42, snippet: '«кэш» истёк' }], 'кэш', 'project')).toBe(
    '[2026-10-10 owner conv=S pos=42] «кэш» истёк',
  ))
test('hits format: a snippet stays on its line', () =>
  expect(formatHits([{ conv: 'S', parent: '', ts: '2026-10-10T08:00:00Z', kind: 'tool', pos: 1, snippet: 'a\n  b' }], 'q', 'all')).toBe('[2026-10-10 tool conv=S pos=1] a b'))
test('no hits says what to try', () => expect(formatHits([], 'кэш', 'project')).toContain('scope=all'))
test('rows format: in order, none says what to check', () => {
  expect(formatRows([{ ts: '2026-10-10T08:00:00Z', kind: 'owner', pos: 3, text: 'a\nb' }, { ts: '2026-10-11T08:00:00Z', kind: 'assistant', pos: 4, text: 'c' }])).toBe(
    '[2026-10-10 owner pos=3] a\nb\n\n[2026-10-11 assistant pos=4] c',
  )
  expect(formatRows([])).toBe(T.noRows)
})
test('tool descriptions fit the 300 characters the model reads, in every language', () => {
  for (const texts of Object.values(LANGUAGES)) {
    expect(texts.recallDescription!.length).toBeLessThanOrEqual(300)
    expect(texts.recallMoreDescription!.length).toBeLessThanOrEqual(300)
  }
  expect(T.recallDescription.length).toBeLessThanOrEqual(300)
})
