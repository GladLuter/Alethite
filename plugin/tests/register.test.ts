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

// The engine beneath a launch: the prompt box, /clear and /model, and the first message. `gate` holds the submit until released.
const box = (on: any, draft: string, log: string[], gate: Promise<unknown> = Promise.resolve()) => {
  const state = { text: draft }
  on('prompt.read', () => ({ value: { text: state.text, cursor: 0 } }))
  on('prompt.fill', (_: unknown, e: { text: string }) => ((state.text = e.text), { isFilled: true }))
  on('prompt.submit', async (_: unknown, e: { text: string }) => (log.push(`submit ${e.text}`), await gate, { text: e.text }))
  on('command.run', (_: unknown, e: { command: string; args: string }) => (log.push(`/${e.command} ${e.args}`.trim()), { text: '' }))
  return state
}
const withMemory = { value: { ...usage, context: { ...usage.context, breakdown: { memoryFiles: [{ path: 'C:/mem/memory/MEMORY.md', type: 'AutoMem', tokens: 1 }] } } } }
const handoffOk = (_: unknown, e: { argv: string[] }) => (e.argv.includes('handoff') ? ran(JSON.stringify({ text: '[cg-handoff]x[/cg-handoff]', path: 'C:/h/id.md' })) : noTurn())
const mountBand = async ($: any) => {
  await $.command.run({ command: 'cg-band', args: '' })
  return $.ui.mount({ plugin: 'cacheguard', surface: 'terminal', component: 'AbovePrompt', props } as never)
}

test('new worker: /clear, /model, then handoff + draft as the first message, draft taken out of the box, band idle', async ($, on) => {
  const log: string[] = []
  const argvs: string[][] = []
  wire(on, [], () => withMemory, (_, e) => (e.argv.includes('handoff') && argvs.push(e.argv), handoffOk(_, e)))
  const state = box(on, 'продолжаем', log)
  const ui = await mountBand($)

  await ui.press({ key: 'launch' })
  await ui.press({ key: 'model:sonnet' })

  expect(log).toEqual(['/clear', '/model sonnet', 'submit [cg-handoff]x[/cg-handoff]\n\nпродолжаем'])
  expect(state.text).toBe('')
  const a = argvs[0]!
  expect(a.slice(a.indexOf('handoff'))).toEqual(['handoff', '--session', 'id', '--today', expect.stringMatching(/^\d{4}-\d\d-\d\d$/), '--memory-dir', 'C:/mem/memory'])
  expect(await buttons(ui)).toEqual(['launch', 'stay']) // not armed, not busy
  expect(await ui.findAll({ type: 'Button', text: T.busy })).toEqual([])
})

test('new worker: busy for the whole launch, the arm button is ignored meanwhile', async ($, on) => {
  const log: string[] = []
  let release = () => {}
  wire(on, [], () => ({ value: usage }), handoffOk)
  box(on, 'x', log, new Promise<void>(r => (release = r)))
  const ui = await mountBand($)

  await ui.press({ key: 'launch' })
  const pressed = ui.press({ key: 'model:haiku' })
  const again = ui.press({ key: 'model:haiku' }).catch(() => undefined) // a double click: either the button is gone or the launch refuses it
  for (let i = 0; i < 100 && !log.some(l => l.startsWith('submit')); i++) await new Promise(r => setTimeout(r, 10))
  expect(await ui.findAll({ type: 'Button', text: T.busy })).toHaveLength(1)
  await ui.press({ key: 'launch' })
  expect(await buttons(ui)).toEqual(['launch', 'stay']) // the picker did not open

  release()
  await Promise.all([pressed, again])
  expect(log.filter(l => l === '/clear')).toHaveLength(1)
  expect(await ui.findAll({ type: 'Button', text: T.busy })).toEqual([])
})

test('new worker: a failed handoff leaves the chat alone, tells why and is kept for /cg-health', async ($, on) => {
  const toasts: string[] = []
  const log: string[] = []
  const store = new Map<string, unknown>()
  on('ui.toast', (_: unknown, e: { text: string }) => (toasts.push(e.text), { value: undefined }))
  wire(on, [], () => ({ value: usage }), (_, e) => (e.argv.includes('handoff') ? { value: { exitCode: 1, stdout: '{"error":"no transcript"}', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } } : noTurn()), store)
  box(on, 'x', log)
  const ui = await mountBand($)

  await ui.press({ key: 'launch' })
  await ui.press({ key: 'model:sonnet' })

  expect(log).toEqual([])
  expect(toasts).toEqual([T.handoffFailed('no transcript')])
  expect(store.get('lastError')).toMatchObject({ where: 'launch', msg: T.handoffFailed('no transcript') })
  expect(await buttons(ui)).toEqual(['launch', 'stay'])
  expect(await ui.findAll({ type: 'Button', text: T.newWorker })).toHaveLength(1) // busy ended
})
