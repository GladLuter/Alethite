import { expect, test } from 'claude-code/testing'
import { END, forgetArg, formatLessons, gateFor, lessonContext, START } from '../hooks/lessons'
import { T } from '../hooks/text'

const lesson = (id: number, trigger: string | null) => ({ id, conv: 'c', pos: id, ts: '2026-10-20T00:00:00Z', text: `Action: a${id}`, trigger, from: id === 2 ? 'MTLAPTOP' : null })

test('gate: the first lesson whose trigger the command holds, in any case, not one that stopped already, never one without a trigger', () => {
  const all = [lesson(1, null), lesson(2, 'npm publish'), lesson(3, 'NPM PUBLISH --tag')]
  expect(gateFor('npm Publish --tag next', all, new Set())?.id).toBe(2)
  expect(gateFor('npm Publish --tag next', all, new Set([2]))?.id).toBe(3)
  expect(gateFor('git status', all, new Set())).toBeUndefined()
  expect(gateFor(undefined, all, new Set())).toBeUndefined()
})

test('gate: a trigger shorter than 8 characters stops nothing (lessons.py refuses it, another computer or an older build may not)', () => {
  expect(gateFor('git status', [lesson(1, ''), lesson(2, 'git')], new Set())).toBeUndefined()
})

test('context: between the marks, each lesson on one line and at most 600 characters', () => {
  const block = lessonContext([{ id: 1, conv: 'c', pos: 1, text: 'Action: x\nSymptom: ' + 'я'.repeat(900), score: 0.7 }])
  const lines = block.split('\n')
  expect([lines[0], lines[1], lines.at(-1)]).toEqual([START, T.lessonsIntro, END])
  expect(lines[2]!.length).toBe(602) // "- " and 600
  expect(lines[2]!.endsWith('…')).toBe(true)
})

test('list and forget: one line a lesson with its pos and the computer it came from; forget takes a pos only', () => {
  expect(formatLessons([lesson(2, null)])).toBe('pos=2 [2026-10-20 MTLAPTOP] Action: a2')
  expect(formatLessons([])).toBe(T.noLessons)
  expect([forgetArg(''), forgetArg(' forget 12 '), forgetArg('forget x'), forgetArg('all')]).toEqual([null, 12, undefined, undefined])
})
