import { expect, test } from 'claude-code/testing'
import { encryptArgs, encryptHealth, encryptReply, isTimeout } from '../hooks/encrypt'
import { T } from '../hooks/text'

test('encrypt args: nothing or status is the state; on, join and off carry the folder, both ciphers and the model', () => {
  expect(encryptArgs('', 'Z:\\cg', 'sqlcipher', 'sqlcipher', 'm')).toEqual(['encrypt', 'status'])
  expect(encryptArgs(' status ', 'Z:\\cg', 'sqlcipher', 'sqlcipher', 'm')).toEqual(['encrypt', 'status'])
  for (const what of ['on', 'join', 'off']) expect(encryptArgs(` ${what} `, 'Z:\\cg', 'sqlcipher', 'sqlcipher', 'm')).toEqual(['encrypt', what, '--sync-dir=Z:\\cg', '--share-cipher=sqlcipher', '--index-cipher=sqlcipher', '--model', 'm'])
  // no sync folder: the empty value tells cg.py to leave the folder alone; a folder starting with - is no flag
  expect(encryptArgs('on', '', 'cryptography', 'efs', 'm')).toEqual(['encrypt', 'on', '--sync-dir=', '--share-cipher=cryptography', '--index-cipher=efs', '--model', 'm'])
  expect(encryptArgs('on', '-x', 'sqlcipher', 'sqlcipher', 'm')![2]).toBe('--sync-dir=-x')
})

test('encrypt args: anything else is null', () => {
  for (const bad of ['join x', 'foo', 'on off', 'ON', 'status now']) expect(encryptArgs(bad, 'Z:\\cg', 'sqlcipher', 'sqlcipher', 'm')).toBeNull()
})

test('encrypt reply: done says so by the action; unfinished says so and lists the notes; a cancelled window has no notes', () => {
  expect(encryptReply({ action: 'off', done: true, notes: [] })).toBe(T.encryptDone('off'))
  expect(encryptReply({ action: 'on', done: false, notes: ['n1', 'n2'] })).toBe([T.encryptUnfinished, 'n1', 'n2'].join('\n'))
  expect(encryptReply({ action: 'join', done: false, notes: [] })).toBe(T.encryptUnfinished)
})

test('encrypt health: on names both ciphers by their names (the index from cg.py, the folder from the setting), off says so, the notes follow', () => {
  expect(encryptHealth({ on: true, db: 'efs', key: 'ok', notes: [] }, 'cryptography')).toEqual([T.healthEncryptionOn('EFS', 'AES-GCM')])
  expect(encryptHealth({ on: true, db: 'sqlcipher', key: 'ok', notes: [] }, 'sqlcipher')).toEqual([T.healthEncryptionOn('SQLCipher', 'SQLCipher')])
  expect(encryptHealth({ on: false, db: 'plain', key: 'missing', notes: ['n1'] }, 'sqlcipher')).toEqual([T.healthEncryptionOff, 'n1'])
})

test('encrypt health: no index yet with a key is named by a dash, and a setting that is a word of Object.prototype is shown as it is', () => {
  expect(encryptHealth({ on: true, db: 'none', key: 'ok', notes: [] }, 'constructor')).toEqual([T.healthEncryptionOn('—', 'constructor')])
})

test('encrypt health: a key with an index that is still open is not "on"; its notes say what to do', () => {
  const lines = encryptHealth({ on: true, db: 'plain', key: 'ok', notes: ['repeat encrypt on'] }, 'sqlcipher')
  expect(lines).toEqual([T.healthEncryptionOff, 'repeat encrypt on'])
})

test('encrypt health: a key that cannot be read is told by cg.py in the notes, nothing else is added', () => {
  expect(encryptHealth({ on: true, db: 'sqlcipher', key: 'unreadable', notes: ['the key cannot be opened'] }, 'cryptography')).toEqual([T.healthEncryptionOn('SQLCipher', 'AES-GCM'), 'the key cannot be opened'])
  expect(encryptHealth({ on: true, db: 'plain', key: 'unreadable', notes: ['the key cannot be opened'] }, 'sqlcipher')).toEqual([T.healthEncryptionOff, 'the key cannot be opened'])
})

// the engine words it as `<plugin>: $.process.run(<command>) aborted: still running after <N>ms`; runCg puts it in brackets at the end of its own text
test('encrypt timeout: the words of the engine at the end of the message, in brackets of its own, and no other text', () => {
  const lead = `${T.pythonMissing('python')} (alethite: $.process.run(python)`
  expect(isTimeout(`${lead} aborted: still running after 600000ms)`)).toBe(true)
  expect(isTimeout(`${T.pythonMissing('C:/Program Files (x86)/Python/python.exe')} (alethite: $.process.run(C:/Program Files (x86)/Python/python.exe) aborted: still running after 600000ms)`)).toBe(true)
  expect(isTimeout(`${lead} failed to start: ENOENT: no such file or directory)`)).toBe(false)
  expect(isTimeout(`${lead} aborted)`)).toBe(false) // stopped by the session, not by the clock
  expect(isTimeout('ValueError: the push aborted: still running after 5ms of nothing')).toBe(false)
  expect(isTimeout('ValueError: the push aborted: still running after 5ms')).toBe(false) // a bare tail: a text of cg.py, not the engine's
  expect(isTimeout('ValueError: the push (aborted: still running after 5ms)')).toBe(false) // brackets alone are not enough either
  expect(isTimeout('')).toBe(false)
})
