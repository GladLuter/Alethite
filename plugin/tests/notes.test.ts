import { expect, test } from 'claude-code/testing'
import { flush, notePath, summaryNote, summaryPath } from '../hooks/notes'
import { setLanguage } from '../hooks/text'

test('flush stops at the first failure and keeps the rest', async () => {
  const q = [{ path: 'a', content: '1' }, { path: 'b', content: '2' }]
  let n = 0
  const r = await flush(q, async () => {
    if (n++ === 1) throw new Error('offline')
  })
  expect(r).toEqual({ sent: 1, left: [q[1]], error: 'offline' })
})
test('flush sends in order and leaves nothing', async () => {
  const seen: string[] = []
  const r = await flush([{ path: 'a', content: '1' }, { path: 'b', content: '2' }], async n => void seen.push(n.path))
  expect(seen).toEqual(['a', 'b'])
  expect(r).toEqual({ sent: 2, left: [] })
})
test('flush of an empty queue sends nothing', async () => expect(await flush([], async () => {})).toEqual({ sent: 0, left: [] }))

test('note path', () => expect(notePath('PC-1', '2026-11-13')).toBe('Alethite/Measurements/2026-11-13 PC-1.md'))
test('the summary sits in the same folder as the notes', () => expect(summaryPath()).toBe('Alethite/Measurements/Summary.md'))
test('the folder follows the language; Russian keeps the folder of before', () => {
  setLanguage('ru')
  try {
    expect([notePath('PC-1', '2026-11-13'), summaryPath()]).toEqual(['Alethite/Замеры/2026-11-13 PC-1.md', 'Alethite/Замеры/Сводка.md'])
  } finally {
    setLanguage('en')
  }
})

const front = (host: string, date: string, usd: number | null) => ({
  host,
  date,
  usd_per_owner_msg: usd,
  rewrite_share: 0.3123,
  idle60_share: 0.2,
  median_main_context: 400000,
  owner_msgs: 120,
  recall_share: 0.031,
})

test('summary: one row per note, newest first, then by computer', () => {
  const s = summaryNote([front('B', '2026-11-13', 4.5), front('A', '2026-10-20', 5), front('A', '2026-11-13', 4)])
  const rows = s.split('\n').filter(l => /^\| 2026/.test(l))
  expect(rows.map(r => r.split('|')[1]!.trim() + ' ' + r.split('|')[2]!.trim())).toEqual(['2026-11-13 A', '2026-11-13 B', '2026-10-20 A'])
  expect(rows[0]).toContain('$4.00')
  expect(rows[0]).toContain('31.2%')
  expect(rows[0]).toContain('400 000')
  expect(rows[0]!.split('|')[8]!.trim()).toBe('3.1%') // stage 3: what recall's results cost
})
test('summary: a missing figure is a dash, a file that is no note is left out', () => {
  const s = summaryNote([front('A', '2026-11-13', null), { title: 'not a measurement' }, {}])
  const rows = s.split('\n').filter(l => /^\| 2026/.test(l))
  expect(rows).toHaveLength(1)
  expect(rows[0]!.split('|')[3]!.trim()).toBe('—')
})
