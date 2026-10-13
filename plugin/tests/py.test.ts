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
  const lines = healthLines({ version: '2.1.295', index: 'OperationalError: x', memory: null })
  expect(lines.join('\n')).toContain('OperationalError: x')
  expect(lines.join('\n')).toContain(T.memoryNotLoaded)
})
test('health: last error is shown with where and message', () => {
  const lines = healthLines({ version: '2.1.295', index: {}, memory: null, lastError: { at: 0, where: 'recall', msg: 'boom' } })
  expect(lines.join('\n')).toContain('recall')
  expect(lines.join('\n')).toContain('boom')
})
