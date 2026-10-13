import { expect, test } from 'claude-code/testing'
import { isMemoryFact } from '../hooks/memory'
import { T } from '../hooks/text'

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

test('rule stays within 80 words', () => expect(T.memoryRule.split(/\s+/).length).toBeLessThanOrEqual(80))
test('toast names the fact', () => expect(T.remembered('foo')).toBe('запомнил: foo'))
