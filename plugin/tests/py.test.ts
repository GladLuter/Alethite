import { expect, test } from 'claude-code/testing'
import { healthLines, parseCg } from '../hooks/py'
import { T } from '../hooks/text'

test('parseCg: ok JSON', () => expect(parseCg({ exitCode: 0, stdout: '{"hits":[]}', stderr: '' })).toEqual({ hits: [] }))
test('parseCg: error JSON', () => expect(() => parseCg({ exitCode: 1, stdout: '{"error":"OperationalError: x"}', stderr: '' })).toThrow('OperationalError: x'))
test('parseCg: traceback without JSON', () => expect(() => parseCg({ exitCode: 1, stdout: '', stderr: 'Traceback…\nNameError: y' })).toThrow('NameError: y'))
test('parseCg: silent failure', () => expect(() => parseCg({ exitCode: 3, stdout: '', stderr: '' })).toThrow('exit 3'))
test('parseCg: only the last 300 characters of stderr', () => {
  let msg = ''
  try {
    parseCg({ exitCode: 1, stdout: '', stderr: 'HEAD' + 'x'.repeat(300) })
  } catch (err) {
    msg = (err as Error).message
  }
  expect(msg).toBe('x'.repeat(300))
})
test('health: empty memory and index error are spelled out', () => {
  const lines = healthLines({ version: '2.1.295', index: 'OperationalError: x', memory: null, root: 'C:/w', embedCmd: '' })
  expect(lines.join('\n')).toContain('OperationalError: x')
  expect(lines.join('\n')).toContain(T.memoryNotLoaded)
})
test('health: last error is shown with where and message', () => {
  const lines = healthLines({ version: '2.1.295', index: {}, memory: null, root: 'C:/w', embedCmd: '', lastError: { at: 0, where: 'recall', msg: 'boom' } })
  expect(lines.join('\n')).toContain('recall')
  expect(lines.join('\n')).toContain('boom')
})
const cmd = 'python "C:/p/scripts/cg.py" embed --model embeddinggemma --max-seconds 100000'
const vectors = { model: 'embeddinggemma', with_vectors: 40, total: 100, updated: '2026-10-18T09:00:00Z' }
test('health: vectors, and the command to finish them while some records have none', () => {
  const lines = healthLines({ version: '2.1.295', index: { vectors }, memory: null, root: 'C:/w', embedCmd: cmd })
  expect(lines).toContain(T.vectorsLine('embeddinggemma', 40, 100, '2026-10-18T09:00:00Z'))
  expect(lines).toContain(T.vectorsHint(cmd))
})
test('health: every record has vectors: no command; nothing about vectors when the index is unreadable', () => {
  const done = healthLines({ version: '2.1.295', index: { vectors: { ...vectors, with_vectors: 100 } }, memory: null, root: 'C:/w', embedCmd: cmd })
  expect(done.join(' ')).toContain('100 из 100')
  expect(done.join(' ')).not.toContain(cmd)
  expect(healthLines({ version: '2.1.295', index: 'OperationalError: x', memory: null, root: 'C:/w', embedCmd: cmd }).join(' ')).not.toContain('Векторы')
})
test('health: the project scope is shown, and a hint when it matches no conversation', () => {
  const health = (convs: number) => healthLines({ version: '2.1.295', index: { project: { key: 'c:/w', convs } }, memory: null, root: 'C:/w', embedCmd: cmd })
  expect(health(12)).toContain(T.projectLine('C:/w', 12))
  expect(health(12)).not.toContain(T.projectEmpty)
  expect(health(0)).toContain(T.projectLine('C:/w', 0))
  expect(health(0)).toContain(T.projectEmpty)
  // nothing to say when the index could not be read, or cg.py did not report a project
  expect(healthLines({ version: '2.1.295', index: 'OperationalError: x', memory: null, root: 'C:/w', embedCmd: cmd }).join(' ')).not.toContain('Проект')
  expect(healthLines({ version: '2.1.295', index: {}, memory: null, root: 'C:/w', embedCmd: cmd }).join(' ')).not.toContain('Проект')
})
