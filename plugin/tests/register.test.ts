import { expect, mock, test } from 'claude-code/testing'
import { T } from '../hooks/text'

const usage = { context: { tokens: 512_000, window: 1_000_000 }, rateLimits: [{ kind: 'five_hour', percentUsed: 7 }] }
const turn = { answer: '', durationMs: 1, isAborted: false, turnId: 't', reason: 'answer', usage: { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, model: 'claude-opus-5-5' } }
const props = { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 80 }

const ran = (stdout: string) => ({ value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })
const noTurn = () => ran('{"tokens":null,"at":null,"model":null}')

// stands for the engine beneath the plugin; `statuses` collects what the status line was set to, `answerRun` answers cg.py
const wire = (on: any, statuses: (string | undefined)[], answerUsage: () => unknown, answerRun: (_: unknown, e: { argv: string[] }) => unknown = noTurn, store = new Map<string, unknown>()) => {
  mock.clock(on, { now: 10_000_000_000 })
  on('store.get', (_: unknown, e: { key: string }) => ({ value: store.get(e.key) }))
  on('store.set', (_: unknown, e: { key: string; value: unknown }) => (store.set(e.key, e.value), { value: undefined }))
  on('session.id', () => ({ value: 'id' }))
  on('session.model', () => ({ value: 'claude-opus-5-5' }))
  on('session.usage', answerUsage)
  on('session.version', () => ({ value: { version: '2.1.295' } }))
  on('process.run', answerRun)
  on('ui.status', (_: unknown, e: { text?: string }) => (statuses.push(e.text), { value: undefined }))
  on('ui.render', () => ({ type: 'Text', children: ['no band'] }))
  on('turn.complete', () => ({ text: '' }))
}
const buttons = async (ui: any) => (await ui.findAll({ type: 'Button' })).map((b: { key: string }) => b.key)

test('a failed refresh shows its error in the status line', async ($, on) => {
  const statuses: (string | undefined)[] = []
  const store = new Map<string, unknown>()
  wire(on, statuses, () => ({ deny: 'boom' }), undefined, store)
  await ($ as any).command.run({ command: 'cg-band', args: '' })
  expect(statuses.some(t => t?.startsWith('CacheGuard: ошибка — '))).toBe(true)
  expect(store.get('lastError')).toMatchObject({ at: 10_000_000_000, where: 'cg-band' }) // kept for /cg-health too
})

test('"Остаться здесь" and an armed picker last one turn', async ($, on) => {
  wire(on, [], () => ({ value: usage }))
  await ($ as any).command.run({ command: 'cg-band', args: '' })
  const ui = await $.ui.mount({ plugin: 'cacheguard', surface: 'terminal', component: 'AbovePrompt', props } as never)

  await ui.press({ key: 'launch' })
  expect(await buttons(ui)).toContain('cancel')
  await ($ as any).turn.complete(turn)
  expect(await buttons(ui)).toEqual(['launch', 'stay'])

  await ui.press({ key: 'stay' })
  expect(await buttons(ui)).toEqual([])
  await ($ as any).turn.complete(turn)
  expect(await buttons(ui)).toEqual(['launch', 'stay'])
})

const noSize = () => ({ value: { context: { window: 1_000_000 }, rateLimits: [] } })

test('after a restart the size comes from cg.py last-turn, asked once per chat', async ($, on) => {
  const statuses: (string | undefined)[] = []
  const argvs: string[][] = []
  const store = new Map<string, unknown>()
  const at = new Date(10_000_000_000 - 2 * 3_600_000).toISOString()
  wire(on, statuses, noSize, (_, e) => (argvs.push(e.argv), ran(JSON.stringify({ tokens: 300_000, at, model: 'm' }))), store)
  await ($ as any).command.run({ command: 'cg-band', args: '' })
  await ($ as any).command.run({ command: 'cg-band', args: '' })
  expect(statuses).toContain('ctx 300k · кэш истёк')
  expect(argvs.filter(a => a.includes('last-turn')).length).toBe(1)
  expect(store.get('turn:id')).toEqual({ at: Date.parse(at), tokens: 300_000 })
})

test('python missing: one toast, the error is kept for /cg-health', async ($, on) => {
  const toasts: string[] = []
  on('ui.toast', (_: unknown, e: { text: string }) => (toasts.push(e.text), { value: undefined }))
  wire(on, [], noSize, () => ({ deny: 'ENOENT' }))
  await ($ as any).command.run({ command: 'cg-band', args: '' })
  await ($ as any).command.run({ command: 'cg-band', args: '' })
  expect(toasts).toEqual([T.failed('last-turn', T.pythonMissing('python'))])
  const { text } = await ($ as any).command.run({ command: 'cg-health', args: '' })
  expect(text).toContain(T.pythonMissing('python'))
  expect(text).toContain('last-turn')
})

test('/cg-health: version, index figures, auto-memory folder with its fact count', async ($, on) => {
  const dir = 'C:/mem/memory'
  const files = [{ path: 'C:/p/CLAUDE.md', type: 'Project', tokens: 1 }, { path: `${dir}/MEMORY.md`, type: 'AutoMem', tokens: 1 }]
  const status = { db: 'C:/x/mem.db', convs: 12, rows: 3400, last_update: '2026-10-13T09:00:00', size_mb: 1.5 }
  wire(on, [], () => ({ value: { context: { window: 1_000_000, breakdown: { memoryFiles: files } }, rateLimits: [] } }), (_, e) => (e.argv.includes('status') ? ran(JSON.stringify(status)) : noTurn()))
  on('fs.list', () => ({ value: ['MEMORY.md', 'a.md', 'b.md', 'notes.txt'].map(name => ({ name, kind: 'file', size: 1, mtimeMs: 0, isLink: false })) }))
  const { text } = await ($ as any).command.run({ command: 'cg-health', args: '' })
  expect(text).toContain('2.1.295')
  expect(text).toContain('3400')
  expect(text).toContain(T.healthMemory(dir, 2))
})
