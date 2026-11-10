import { expect, test } from 'claude-code/testing'
import { healthLines, homeNotesOf, parseCg } from '../hooks/py'
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
  expect(done).toContain(T.vectorsLine('embeddinggemma', 100, 100, '2026-10-18T09:00:00Z'))
  expect(done.join(' ')).not.toContain(cmd)
  expect(healthLines({ version: '2.1.295', index: 'OperationalError: x', memory: null, root: 'C:/w', embedCmd: cmd }).join(' ')).not.toContain('Vectors')
})
test('health: the project scope is shown, and a hint when it matches no conversation', () => {
  const health = (convs: number) => healthLines({ version: '2.1.295', index: { project: { key: 'c:/w', convs } }, memory: null, root: 'C:/w', embedCmd: cmd })
  expect(health(12)).toContain(T.projectLine('C:/w', 12))
  expect(health(12)).not.toContain(T.projectEmpty)
  expect(health(0)).toContain(T.projectLine('C:/w', 0))
  expect(health(0)).toContain(T.projectEmpty)
  // nothing to say when the index could not be read, or cg.py did not report a project
  expect(healthLines({ version: '2.1.295', index: 'OperationalError: x', memory: null, root: 'C:/w', embedCmd: cmd }).join(' ')).not.toContain('Project')
  expect(healthLines({ version: '2.1.295', index: {}, memory: null, root: 'C:/w', embedCmd: cmd }).join(' ')).not.toContain('Project')
})
test('health: records the model could not embed are counted; none, no line', () => {
  const lines = (failed: number) => healthLines({ version: '2.1.295', index: { vectors: { ...vectors, failed } }, memory: null, root: 'C:/w', embedCmd: cmd })
  expect(lines(3)).toContain(T.vectorsFailed(3))
  expect(lines(0).join(' ')).not.toContain(T.vectorsFailed(0))
})
test('health: the encryption line comes after the settings file, when cg.py reports it; none otherwise', () => {
  const encryption = { on: true, db: 'efs', key: 'ok', notes: ['n1'] }
  const lines = healthLines({ version: '2.1.295', index: { encryption }, memory: null, root: 'C:/w', embedCmd: cmd, settingsFile: 'C:/h/settings.json', shareCipher: 'cryptography' })
  const at = lines.indexOf(T.healthEncryptionOn('EFS', 'AES-GCM'))
  expect(at).toBeGreaterThan(lines.indexOf(T.healthSettings('C:/h/settings.json')))
  expect(lines[at + 1]).toBe('n1')
  expect(healthLines({ version: '2.1.295', index: { encryption: { ...encryption, on: false, db: 'plain', key: 'missing', notes: [] } }, memory: null, root: 'C:/w', embedCmd: cmd }).join(' ')).toContain(T.healthEncryptionOff)
  expect(healthLines({ version: '2.1.295', index: {}, memory: null, root: 'C:/w', embedCmd: cmd }).join(' ')).not.toContain(T.healthEncryptionOff)
  expect(healthLines({ version: '2.1.295', index: 'OperationalError: x', memory: null, root: 'C:/w', embedCmd: cmd }).join(' ')).not.toContain(T.healthEncryptionOff)
})
test('health: the notes of the index folder come right after the index line, when cg.py reports them; none otherwise', () => {
  const index = { db: 'C:/h/index.db', convs: 1, rows: 2, size_mb: 3, index_dir: { configured: 'D:/cg', fallback: true, notes: ['n1', 'n2'] } }
  const lines = healthLines({ version: '2.1.295', index, memory: null, root: 'C:/w', embedCmd: cmd })
  const at = lines.indexOf(T.healthIndex(index))
  expect(at).toBeGreaterThan(-1)
  expect(lines.slice(at + 1, at + 3)).toEqual(['n1', 'n2'])
  const quiet = healthLines({ version: '2.1.295', index: { ...index, index_dir: { configured: null, fallback: false, notes: [] } }, memory: null, root: 'C:/w', embedCmd: cmd })
  expect(quiet).toEqual(healthLines({ version: '2.1.295', index: { ...index, index_dir: undefined }, memory: null, root: 'C:/w', embedCmd: cmd }))
  expect(healthLines({ version: '2.1.295', index: 'OperationalError: x', memory: null, root: 'C:/w', embedCmd: cmd }).join(' ')).not.toContain('n1')
})
test('health: the notes of the mod folder move come after the notes of the index folder; none, no line', () => {
  const index = { db: 'C:/h/index.db', convs: 1, rows: 2, size_mb: 3, index_dir: { configured: 'D:/cg', fallback: true, notes: ['n1'] } }
  const lines = healthLines({ version: '2.1.295', index, memory: null, root: 'C:/w', embedCmd: cmd, homeNotes: ['h1', 'h2'] })
  expect(lines.slice(lines.indexOf(T.healthIndex(index)) + 1, lines.indexOf(T.healthIndex(index)) + 4)).toEqual(['n1', 'h1', 'h2'])
  expect(healthLines({ version: '2.1.295', index, memory: null, root: 'C:/w', embedCmd: cmd, homeNotes: [] })).toEqual(healthLines({ version: '2.1.295', index, memory: null, root: 'C:/w', embedCmd: cmd }))
})
test('home notes of an answer: the strings of home_notes, nothing for another shape or no JSON', () => {
  expect(homeNotesOf('{"home_notes":["a","b"],"error":"x"}')).toEqual(['a', 'b'])
  expect(homeNotesOf('{"home_notes":["a",1,null,""]}')).toEqual(['a'])
  expect(homeNotesOf('{"home_notes":"a"}')).toEqual([])
  expect(homeNotesOf('{}')).toEqual([])
  expect(homeNotesOf('Traceback…')).toEqual([])
})
test('health: the rules line follows the lessons line, only when some lessons are global', () => {
  const lessons = { count: 2, shown: 0, chars: 0, stopped: 0, on: true }
  const lines = (global: number) => healthLines({ version: '2.1.295', index: {}, memory: null, root: 'C:/w', embedCmd: cmd, lessons, rules: { global, rules: 2, chars: 140, left: 1 } })
  const on = lines(3)
  expect(on[on.indexOf(T.healthLessons(2, 0, 0, 0, true)) + 1]).toBe(T.healthRules(3, 2, 140, 1))
  expect(lines(0).join(' ')).not.toContain(T.healthRules(0, 2, 140, 1))
})
