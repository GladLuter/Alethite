import { expect, test } from 'claude-code/testing'
import { encryptArgs, encryptHealth, encryptReply } from '../hooks/encrypt'
import { T } from '../hooks/text'

test('encrypt args: nothing or status is the state; on, join and off carry the folder, the cipher and the model', () => {
  expect(encryptArgs('', 'Z:\\cg', 'sqlcipher', 'm')).toEqual(['encrypt', 'status'])
  expect(encryptArgs(' status ', 'Z:\\cg', 'sqlcipher', 'm')).toEqual(['encrypt', 'status'])
  for (const what of ['on', 'join', 'off']) expect(encryptArgs(` ${what} `, 'Z:\\cg', 'sqlcipher', 'm')).toEqual(['encrypt', what, '--sync-dir=Z:\\cg', '--share-cipher=sqlcipher', '--model', 'm'])
  // no sync folder: the empty value tells cg.py to leave the folder alone; a folder starting with - is no flag
  expect(encryptArgs('on', '', 'cryptography', 'm')).toEqual(['encrypt', 'on', '--sync-dir=', '--share-cipher=cryptography', '--model', 'm'])
  expect(encryptArgs('on', '-x', 'sqlcipher', 'm')![2]).toBe('--sync-dir=-x')
})

test('encrypt args: anything else is null', () => {
  for (const bad of ['join x', 'foo', 'on off', 'ON', 'status now']) expect(encryptArgs(bad, 'Z:\\cg', 'sqlcipher', 'm')).toBeNull()
})

test('encrypt reply: done says so by the action; unfinished says so and lists the notes; a cancelled window has no notes', () => {
  expect(encryptReply({ action: 'off', done: true, notes: [] })).toBe(T.encryptDone('off'))
  expect(encryptReply({ action: 'on', done: false, notes: ['n1', 'n2'] })).toBe([T.encryptUnfinished, 'n1', 'n2'].join('\n'))
  expect(encryptReply({ action: 'join', done: false, notes: [] })).toBe(T.encryptUnfinished)
})

test('encrypt health: on names the cipher, off says so, the notes follow', () => {
  expect(encryptHealth({ on: true, db: 'encrypted', key: 'ok', notes: [] }, 'sqlcipher')).toEqual([T.healthEncryptionOn('sqlcipher')])
  expect(T.healthEncryptionOn('sqlcipher')).toContain('sqlcipher')
  expect(encryptHealth({ on: false, db: 'plain', key: 'missing', notes: ['n1'] }, 'sqlcipher')).toEqual([T.healthEncryptionOff, 'n1'])
})

test('encrypt health: a key with an index that is still open is not "on"; its notes say what to do', () => {
  const lines = encryptHealth({ on: true, db: 'plain', key: 'ok', notes: ['repeat encrypt on'] }, 'sqlcipher')
  expect(lines).toEqual([T.healthEncryptionOff, 'repeat encrypt on'])
})

test('encrypt health: a key that cannot be read is told by cg.py in the notes, nothing else is added', () => {
  expect(encryptHealth({ on: true, db: 'encrypted', key: 'unreadable', notes: ['the key cannot be opened'] }, 'cryptography')).toEqual([T.healthEncryptionOn('cryptography'), 'the key cannot be opened'])
  expect(encryptHealth({ on: true, db: 'plain', key: 'unreadable', notes: ['the key cannot be opened'] }, 'sqlcipher')).toEqual([T.healthEncryptionOff, 'the key cannot be opened'])
})
