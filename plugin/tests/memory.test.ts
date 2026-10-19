import { expect, test } from 'claude-code/testing'
import { autoMemDir, autoMemTokens, isMemoryFact } from '../hooks/memory'
import { setLanguage, T } from '../hooks/text'

test('memory fact paths', () => {
  expect(isMemoryFact('C:\\Users\\A\\.claude\\projects\\X\\memory\\foo.md')).toBe(true)
  expect(isMemoryFact('C:/Users/A/.claude/projects/X/memory/foo.md')).toBe(true)
  expect(isMemoryFact('C:\\Users\\A\\.claude\\projects\\X\\memory\\MEMORY.md')).toBe(false)
  expect(isMemoryFact('C:\\_git\\proj\\memory\\foo.md')).toBe(false)
})

test('memory fact paths: case-blind, the index in any case is not a fact, only .md files directly in memory/', () => {
  expect(isMemoryFact('c:\\users\\a\\.CLAUDE\\Projects\\X\\Memory\\Foo.MD')).toBe(true)
  expect(isMemoryFact('C:/Users/A/.claude/projects/X/memory/memory.md')).toBe(false)
  expect(isMemoryFact('C:/Users/A/.claude/projects/X/memory/notes.txt')).toBe(false)
  expect(isMemoryFact('C:/Users/A/.claude/projects/X/memory/sub/foo.md')).toBe(false)
  expect(isMemoryFact('C:/Users/A/.claude/projects/memory/foo.md')).toBe(false)
})

test('the auto-memory folder and what it puts in every request', () => {
  const files = [
    { path: 'C:/p/CLAUDE.md', type: 'Project', tokens: 500 },
    { path: 'C:\\mem\\memory\\MEMORY.md', type: 'AutoMem', tokens: 950 },
  ]
  expect(autoMemDir(files)).toBe('C:\\mem\\memory')
  expect(autoMemTokens(files)).toBe(950)
  expect(autoMemDir([])).toBeNull()
  expect(autoMemTokens([])).toBe(0)
})

test('toast names the fact', () => {
  expect(T.remembered('foo')).toBe('remembered: foo')
  setLanguage('ru')
  try {
    expect(T.remembered('foo')).toBe('запомнил: foo')
  } finally {
    setLanguage('en')
  }
})
