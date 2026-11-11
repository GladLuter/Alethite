import { expect, test } from 'claude-code/testing'
import { cleanupArgs, cleanupHealth, cleanupReply } from '../hooks/cleanup'
import { healthLines } from '../hooks/py'
import { T } from '../hooks/text'

test('cleanup args: no word is the state, on/off/status go through, anything else is null', () => {
  expect(cleanupArgs('')).toEqual(['cleanup', 'status'])
  expect(cleanupArgs(' on ')).toEqual(['cleanup', 'on'])
  expect(cleanupArgs('off')).toEqual(['cleanup', 'off'])
  expect(cleanupArgs('status')).toEqual(['cleanup', 'status'])
  expect(cleanupArgs('yes')).toBeNull()
  expect(cleanupArgs('on now')).toBeNull()
})

test('cleanup reply: the state with the number of chats and megabytes rounded to one decimal', () => {
  const r = cleanupReply({ on: true, preview: { chats: 3, mb: 12.345 } })
  expect(r).toBe(T.cleanupState(true, 3, 12.3))
  expect(r).toContain('3')
  expect(r).toContain('12.3')
  expect(cleanupReply({ on: false, preview: { chats: 0, mb: 0 } })).toBe(T.cleanupState(false, 0, 0))
})

test('cleanup health: on says how many were deleted, off says off', () => {
  expect(cleanupHealth({ on: true, removed_total: 5 })).toContain('5')
  expect(cleanupHealth({ on: true, removed_total: 5 })).not.toBe(cleanupHealth({ on: false, removed_total: 5 }))
  const h = (cleanup?: unknown) => healthLines({ version: '2.1.295', index: cleanup ? { cleanup } : {}, memory: null, root: 'C:/w', embedCmd: '' })
  expect(h({ on: true, removed_total: 5 })).toContain(T.healthCleanup(true, 5))
  expect(h({ on: false, removed_total: 0 })).toContain(T.healthCleanup(false, 0))
  expect(h().join(' ')).not.toContain(T.healthCleanup(false, 0))
})
