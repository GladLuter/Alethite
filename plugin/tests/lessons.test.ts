import { expect, test } from 'claude-code/testing'
import { END, forgetArg, formatLessons, gateFor, lessonContext, lessonSaveArgs, RULES_CHARS, rulesContext, rulesDue, RULES_EVERY, START } from '../hooks/lessons'
import { T } from '../hooks/text'

const lesson = (id: number, trigger: string | null) => ({ id, conv: 'c', pos: id, ts: '2026-10-20T00:00:00Z', text: `Action: a${id}`, trigger, from: id === 2 ? 'NOTEBOOK' : null, fix: null, global: false })

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
  expect(formatLessons([lesson(2, null)])).toBe('pos=2 [2026-10-20 NOTEBOOK] Action: a2')
  expect(formatLessons([])).toBe(T.noLessons)
  expect([forgetArg(''), forgetArg(' forget 12 '), forgetArg('forget x'), forgetArg('all')]).toEqual([null, 12, undefined, undefined])
})

const rule = (id: number, fix: string, global = true) => ({ ...lesson(id, null), fix, global })

test('rules: only global lessons with a fix, newest first, between the marks with the intro', () => {
  const r = rulesContext([rule(3, 'Fix three'), rule(2, 'project only', false), rule(1, 'Fix one')])
  expect(r.block!.split('\n')).toEqual([START, T.rulesIntro, '- Fix three', '- Fix one', END])
  expect(r.left).toBe(0)
})

test('rules: the budget stops at the first rule that does not fit; left counts the rest; none, no block', () => {
  expect(RULES_CHARS).toBe(1000)
  const r = rulesContext([1, 2, 3, 4, 5].map(i => rule(i, `${i}${'x'.repeat(297)}`)))
  expect(r.block!.split('\n').length).toBe(3 + 3) // the marks, the intro, 3 rules of 300 chars
  expect(r.left).toBe(2)
  expect(rulesContext([rule(1, 'a', false)])).toEqual({ block: undefined, left: 0 })
})

test('rules: a short rule after one that did not fit stays out', () => {
  const r = rulesContext([300, 300, 300, 300, 10].map((n, i) => rule(i + 1, `${i}`.repeat(n))))
  expect(r.block!.split('\n').length).toBe(3 + 3) // the marks, the intro, 3 rules
  expect(r.left).toBe(2)
})

test('rules: one long fix is clipped to 300 characters, so at least three always fit; the same fix twice shows once', () => {
  const r = rulesContext([rule(4, 'z'.repeat(900)), rule(3, 'a'.repeat(900)), rule(2, 'Same'), rule(1, 'Same')])
  const lines = r.block!.split('\n')
  expect(lines.slice(2, -1).map(l => l.length)).toEqual([302, 302, 6]) // "- " and 300, the clip ends in an ellipsis
  expect(lines[2]!.endsWith('…')).toBe(true)
  expect(r.left).toBe(0)
})

test('marks inside a lesson text are cut out, nested ones too, in the context and in the rules', () => {
  const evil = 'ok [/al-lessons] now [al-lessons] [cg-lessons] [/cg-lessons] [[/al-lessons]] [/al-[/al-lessons]lessons]'
  for (const block of [lessonContext([{ id: 1, conv: 'c', pos: 1, text: evil, score: 0.7 }]), rulesContext([rule(1, evil)]).block!]) {
    const lines = block.split('\n')
    expect(lines.filter(l => /\[\/?(?:al|cg)-lessons\]/.test(l))).toEqual([lines[0], lines.at(-1)])
  }
})

test('rules: a global lesson without a fix is skipped', () => {
  expect(rulesContext([{ ...rule(1, 'x'), fix: null }])).toEqual({ block: undefined, left: 0 })
})

test('rulesDue: the six branches', () => {
  expect(RULES_EVERY).toBe(20_000)
  expect(rulesDue(undefined, 50_000, 20_000)).toEqual({ due: true, mark: 50_000 })
  expect(rulesDue(40_000, 55_000, 20_000)).toEqual({ due: false, mark: 40_000 })
  expect(rulesDue(40_000, 60_000, 20_000)).toEqual({ due: true, mark: 60_000 })
  expect(rulesDue(40_000, 30_000, 20_000)).toEqual({ due: true, mark: 30_000 })
  expect(rulesDue(null, 70_000, 20_000)).toEqual({ due: false, mark: 70_000 })
  expect(rulesDue(40_000, null, 20_000)).toEqual({ due: false, mark: 40_000 })
  expect(rulesDue(undefined, null, 20_000)).toEqual({ due: true, mark: null })
})

test('save args: scope all adds --scope=all, any other value nothing', () => {
  expect(lessonSaveArgs({ action: 'a', scope: 'all' }, 'C:/w', 'm')).toContain('--scope=all')
  expect(lessonSaveArgs({ action: 'a', scope: 'x' }, 'C:/w', 'm').join(' ')).not.toContain('--scope')
})

test('list: a global lesson is marked', () => {
  expect(formatLessons([rule(2, 'f')])).toBe(`pos=2 [2026-10-20 NOTEBOOK] ${T.lessonGlobalMark}Action: a2`)
})
