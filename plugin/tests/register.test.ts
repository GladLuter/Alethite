import { expect, mock, test } from 'claude-code/testing'
import { ru } from '../hooks/lang/ru'
import { uk } from '../hooks/lang/uk'
import { notePath, summaryPath } from '../hooks/notes'
import { when } from '../hooks/band'
import { price } from '../hooks/guard'
import { T } from '../hooks/text'

const usage = { context: { tokens: 512_000, window: 1_000_000 }, rateLimits: [{ kind: 'five_hour', percentUsed: 7 }] }
const turn = { answer: '', durationMs: 1, isAborted: false, turnId: 't', reason: 'answer', usage: { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, model: 'claude-opus-5-5' } }
const props = { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 80 }

const ran = (stdout: string) => ({ value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })
const noTurn = () => ran('{"tokens":null,"at":null,"model":null}')

// stands for the engine beneath the plugin; `statuses` collects what the status line was set to, `answerRun` answers cg.py
const ids = { now: 'id' }
const wire = (on: any, statuses: (string | undefined)[], answerUsage: () => unknown, answerRun: (_: unknown, e: { argv: string[] }) => unknown = noTurn, store = new Map<string, unknown>()) => {
  ids.now = 'id'
  const clock = mock.clock(on, { now: 10_000_000_000 })
  on('store.get', (_: unknown, e: { key: string }) => ({ value: store.get(e.key) }))
  on('store.set', (_: unknown, e: { key: string; value: unknown }) => (store.set(e.key, e.value), { value: undefined }))
  on('store.keys', () => ({ value: [...store.keys()] }))
  on('store.delete', (_: unknown, e: { key: string }) => (store.delete(e.key), { value: undefined }))
  on('session.id', () => ({ value: ids.now }))
  on('session.model', () => ({ value: 'claude-opus-5-5' }))
  on('session.usage', answerUsage)
  on('session.version', () => ({ value: { version: '2.1.295' } }))
  on('process.run', answerRun)
  on('ui.status', (_: unknown, e: { text?: string }) => (statuses.push(e.text), { value: undefined }))
  on('ui.render', () => ({ type: 'Text', children: ['no band'] }))
  on('turn.complete', () => ({ text: '' }))
  return clock
}
const buttons = async (ui: any) => (await ui.findAll({ type: 'Button' })).map((b: { key: string }) => b.key)

test('a failed refresh shows its error in the status line', async ($, on) => {
  const statuses: (string | undefined)[] = []
  const store = new Map<string, unknown>()
  wire(on, statuses, () => ({ deny: 'boom' }), undefined, store)
  await ($ as any).command.run({ command: 'al-band', args: '' })
  expect(statuses.some(t => t?.startsWith(T.statusError('')))).toBe(true)
  expect(store.get('lastError')).toMatchObject({ at: 10_000_000_000, where: 'al-band' }) // kept for /al-health too
})

test('an armed picker lasts one turn', async ($, on) => {
  wire(on, [], () => ({ value: usage }))
  await ($ as any).command.run({ command: 'al-band', args: '' })
  const ui = await $.ui.mount({ plugin: 'alethite', surface: 'terminal', component: 'AbovePrompt', props } as never)

  await ui.press({ key: 'launch' })
  expect(await buttons(ui)).toEqual(['send', 'pick', 'cancel'])
  await ($ as any).turn.complete(turn)
  expect(await buttons(ui)).toEqual(['launch', 'details'])
})

test('while the model works the row stays without New worker', async ($, on) => {
  wire(on, [], () => ({ value: usage }))
  await ($ as any).command.run({ command: 'al-band', args: '' })
  expect(await buttons(await $.ui.mount({ plugin: 'alethite', surface: 'terminal', component: 'AbovePrompt', props: { ...props, isWorking: true } } as never))).toEqual(['details'])
})

const noSize = () => ({ value: { context: { window: 1_000_000 }, rateLimits: [] } })

test('after a restart the size comes from cg.py last-turn, asked once per chat', async ($, on) => {
  const statuses: (string | undefined)[] = []
  const argvs: string[][] = []
  const store = new Map<string, unknown>()
  const at = new Date(10_000_000_000 - 2 * 3_600_000).toISOString()
  wire(on, statuses, noSize, (_, e) => (argvs.push(e.argv), ran(JSON.stringify({ tokens: 300_000, at, model: 'm' }))), store)
  await ($ as any).command.run({ command: 'al-band', args: '' })
  await ($ as any).command.run({ command: 'al-band', args: '' })
  expect(statuses).toContain(T.ctx('300k', T.cacheExpired))
  expect(argvs.filter(a => a.includes('last-turn')).length).toBe(1)
  expect(store.get('turn:id')).toEqual({ at: Date.parse(at), tokens: 300_000 })
})

test('the turn size is kept for the last 200 chats: the 201st pushes out the oldest by at', async ($, on) => {
  const store = new Map<string, unknown>()
  for (let i = 1; i <= 200; i++) store.set(`turn:c${i}`, { at: i, tokens: i })
  wire(on, [], () => ({ value: usage }), undefined, store)
  await ($ as any).turn.complete(turn) // the current chat, id "id", is the 201st
  const turns = [...store.keys()].filter(k => k.startsWith('turn:'))
  expect(turns.length).toBe(200)
  expect(store.has('turn:c1')).toBe(false)
  expect(store.get('turn:c2')).toEqual({ at: 2, tokens: 2 })
  expect(store.get('turn:id')).toEqual({ at: 10_000_000_000, tokens: 512_000 })
})

test('python missing: one toast, the error is kept for /al-health', async ($, on) => {
  const toasts: string[] = []
  on('ui.toast', (_: unknown, e: { text: string }) => (toasts.push(e.text), { value: undefined }))
  wire(on, [], noSize, () => ({ deny: 'ENOENT' }))
  on('session.root', () => ({ value: 'C:/w' }))
  await ($ as any).command.run({ command: 'al-band', args: '' })
  await ($ as any).command.run({ command: 'al-band', args: '' })
  // the engine's own reason follows (a timeout must not read like a missing command), worded by the engine
  const lead = `${T.pythonMissing('python')} (`
  expect(toasts).toHaveLength(1)
  expect(toasts[0]!.startsWith(T.toast(T.failed('last-turn', lead)))).toBe(true)
  expect(toasts[0]!.endsWith('ENOENT)')).toBe(true)
  const { text } = await ($ as any).command.run({ command: 'al-health', args: '' })
  expect(text).toContain('last-turn')
  expect(text).toContain(lead)
})

test('/al-health: version, index figures, auto-memory folder with its fact count', async ($, on) => {
  const dir = 'C:/mem/memory'
  const files = [{ path: 'C:/p/CLAUDE.md', type: 'Project', tokens: 1 }, { path: `${dir}/MEMORY.md`, type: 'AutoMem', tokens: 1 }]
  const vectors = { model: 'embeddinggemma', with_vectors: 3000, total: 3400, updated: '2026-10-18T09:00:00Z' }
  const status = { db: 'C:/x/mem.db', convs: 12, rows: 3400, last_update: '2026-10-13T09:00:00', size_mb: 1.5, vectors, project: { key: 'c:/w', convs: 5 } }
  const argvs: string[][] = []
  wire(on, [], () => ({ value: { context: { window: 1_000_000, breakdown: { memoryFiles: files } }, rateLimits: [] } }), (_, e) => (argvs.push(e.argv), e.argv.includes('status') ? ran(JSON.stringify(status)) : noTurn()))
  on('session.root', () => ({ value: 'C:/w' }))
  on('fs.list', () => ({ value: ['MEMORY.md', 'a.md', 'b.md', 'notes.txt'].map(name => ({ name, kind: 'file', size: 1, mtimeMs: 0, isLink: false })) }))
  const { text } = await ($ as any).command.run({ command: 'al-health', args: '' })
  expect(argvs.find(a => a.includes('status'))!.slice(4)).toEqual(['--follow-job', 'status', '--cwd=C:/w']) // the root, the same folder that recall scopes by
  expect(text).toContain(T.projectLine('C:/w', 5))
  expect(text).not.toContain(T.projectEmpty)
  expect(text).toContain('2.1.295')
  expect(text).toContain('3400')
  expect(text).toContain(T.healthMemory(dir, 2))
  expect(text).toContain(T.vectorsLine('embeddinggemma', 3000, 3400, '2026-10-18T09:00:00Z'))
  expect(text).toMatch(/python ".*cg\.py" --lang en embed --model embeddinggemma --max-seconds 100000/) // 400 records have no vectors: how to finish them, in the mod's language
})

test('/al-health: a project that matches no conversation is said so', async ($, on) => {
  const status = { db: 'C:/x/mem.db', convs: 12, rows: 3400, last_update: null, size_mb: 1.5, project: { key: 'c:/w', convs: 0 } }
  wire(on, [], () => ({ value: { context: { window: 1_000_000 }, rateLimits: [] } }), (_, e) => (e.argv.includes('status') ? ran(JSON.stringify(status)) : noTurn()))
  on('session.root', () => ({ value: 'C:/w' }))
  const { text } = await ($ as any).command.run({ command: 'al-health', args: '' })
  expect(text).toContain(T.projectLine('C:/w', 0))
  expect(text).toContain(T.projectEmpty)
})

// The engine beneath a launch: the prompt box (`refuse` makes a fill fail), /clear, and the first message. `gate` holds the submit until released.
const box = (on: any, draft: string, log: string[], gate: Promise<unknown> = Promise.resolve()) => {
  const state = { text: draft, refuse: undefined as string | undefined, broken: false }
  on('prompt.read', () => (state.broken ? { deny: 'boom' } : { value: { text: state.text, cursor: 0 } }))
  on('prompt.fill', (_: unknown, e: { text: string }) => (state.refuse ? { isFilled: false, refusal: state.refuse } : ((state.text = e.text), { isFilled: true })))
  on('prompt.submit', async (_: unknown, e: { text: string }) => (log.push(`submit ${e.text}`), await gate, { text: e.text }))
  on('command.run', (_: unknown, e: { command: string; args: string }) => (log.push(`/${e.command} ${e.args}`.trim()), { text: '' }))
  return state
}
const withMemory = { value: { ...usage, context: { ...usage.context, breakdown: { memoryFiles: [{ path: 'C:/mem/memory/MEMORY.md', type: 'AutoMem', tokens: 1 }] } } } }
const handoffOk = (_: unknown, e: { argv: string[] }) => (e.argv.includes('handoff') ? ran(JSON.stringify({ text: '[al-handoff]x[/al-handoff]', path: 'C:/h/id.md' })) : noTurn())
const mountBand = async ($: any) => {
  await $.command.run({ command: 'al-band', args: '' })
  return $.ui.mount({ plugin: 'alethite', surface: 'terminal', component: 'AbovePrompt', props } as never)
}

type Drawn = { type: string; props?: Record<string, unknown>; children: Drawn[] }

const texts = async (ui: any) => (await ui.findAll({ type: 'Text' })).map((t: { text: string }) => t.text)
const UI = { plugin: 'alethite', surface: 'terminal', component: 'AbovePrompt', props } as never

test('the band is always one row: the title first, no frame', async ($, on) => {
  wire(on, [], () => ({ value: usage }))
  const ui = await mountBand($)
  const band = (await ui.drawn()) as unknown as Drawn
  expect(band.children[0]!.props?.borderStyle).toBeUndefined()
  expect(await buttons(ui)).toEqual(['launch', 'details'])
  expect((await texts(ui))[0]).toBe(T.bandTitle)
})

test('a stale chat lights the row and offers to stay; staying lasts to the end of the chat', async ($, on) => {
  const at = new Date(10_000_000_000 - 2 * 3_600_000).toISOString()
  wire(on, [], () => ({ value: usage }), () => ran(JSON.stringify({ tokens: 300_000, at, model: 'm' })))
  const ui = await mountBand($)
  expect(await buttons(ui)).toEqual(['launch', 'stay', 'details'])
  await ui.press({ key: 'stay' })
  expect(await buttons(ui)).toEqual(['launch', 'details'])
  await ($ as any).turn.complete(turn) // a turn must not bring the light back
  expect(await buttons(ui)).toEqual(['launch', 'details'])
})

test('a stale chat paints the size and the price as a warning, the price at the cold rate', async ($, on) => {
  const at = new Date(10_000_000_000 - 2 * 3_600_000).toISOString()
  wire(on, [], () => ({ value: usage }), () => ran(JSON.stringify({ tokens: 300_000, at, model: 'claude-opus-5-5' })))
  await $.command.run({ command: 'al-band', args: '' })
  const ui = await $.ui.mount({ ...(UI as object), props: { ...props, bodyColumns: 140 } } as never)
  const all = await ui.findAll({ type: 'Text' })
  const warned = all.filter((t: { props: { color?: string } }) => t.props.color === 'warning').map((t: { text: string }) => t.text)
  expect(warned).toHaveLength(2)
  expect(warned.some((t: string) => t.includes(T.sendHere(`$${price('claude-opus-5-5', 512_000, true).toFixed(2)}`)))).toBe(true)
})

test('Details opens the columns in a frame, remembers it, and the next press brings the row back', async ($, on) => {
  const store = new Map<string, unknown>()
  wire(on, [], () => ({ value: usage }), undefined, store)
  const ui = await mountBand($)
  await ui.press({ key: 'details' })
  const frame = ((await ui.drawn()) as unknown as Drawn).children[0]!
  expect(frame).toMatchObject({ type: 'Box', props: { borderStyle: 'round' } })
  expect(frame.children[0]).toMatchObject({ type: 'Text', props: { bold: true }, children: [T.bandTitle] })
  expect(await texts(ui)).toContain(T.col.chat)
  expect(store.get('details')).toBe(true)
  await ui.press({ key: 'details' })
  expect(store.get('details')).toBe(false)
  expect(((await ui.drawn()) as unknown as Drawn).children[0]!.props?.borderStyle).toBeUndefined()
})

test('Details stays open after a restart of the mod', async ($, on) => {
  mock.env(on, {})
  on('tool.register', () => ({ value: { tool: 'x' } }))
  on('command.register', () => ({ value: { command: 'x' } }))
  on('session.root', () => ({ value: 'C:/w' }))
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  wire(on, [], () => ({ value: usage }), undefined, new Map<string, unknown>([['details', true]]))
  await ($ as any).session.start({ cwd: 'C:/w', surface: null, isInteractive: false })
  const ui = await $.ui.mount(UI)
  expect(await texts(ui)).toContain(T.col.chat)
})

test('desktop draws the columns as one Svg', async ($, on) => {
  wire(on, [], () => ({ value: usage }))
  await mountBand($)
  const ui = await $.ui.mount({ plugin: 'alethite', surface: 'desktop', component: 'AbovePrompt', props } as never)
  await ui.press({ key: 'details' })
  const svg = await ui.findAll({ type: 'Svg' })
  expect(svg).toHaveLength(1)
  expect(typeof svg[0]!.props.alt).toBe('string')
})

test('/al-band off hides the band in this chat, /al-band brings it back', async ($, on) => {
  wire(on, [], () => ({ value: usage }))
  const ui = await mountBand($)
  await ($ as any).command.run({ command: 'al-band', args: 'off' })
  expect(await buttons(ui)).toEqual([])
  await ($ as any).command.run({ command: 'al-band', args: '' })
  expect(await buttons(ui)).toEqual(['launch', 'details'])
})

test("another mod's band beneath ours is not covered: it stays, under our frame", async ($, on) => {
  wire(on, [], () => ({ value: usage })) // what is drawn beneath the plugin under test: the text 'no band', standing for another mod's band
  const band = (await (await mountBand($)).drawn()) as unknown as Drawn
  expect(band.children).toHaveLength(2)
  expect(band.children[1]).toMatchObject({ type: 'Text', children: ['no band'] })
})

test('new worker: the first press arms the band: two ways to go, cancel and the @files hint; cancel disarms', async ($, on) => {
  wire(on, [], () => ({ value: usage }))
  const ui = await mountBand($)

  await ui.press({ key: 'launch' })
  expect(await buttons(ui)).toEqual(['send', 'pick', 'cancel'])
  expect(await ui.findAll({ type: 'Button', text: T.sendNow })).toHaveLength(1)
  expect(await ui.findAll({ type: 'Button', text: T.pickInMenu })).toHaveLength(1)
  expect(await ui.findAll({ type: 'Text', text: T.atFilesHint })).toHaveLength(1)
  expect(await ui.findAll({ type: 'Text', text: T.willClear })).toHaveLength(1)

  await ui.press({ key: 'cancel' })
  expect(await buttons(ui)).toEqual(['launch', 'details'])
})

test('new worker, send at once: /clear, then handoff + draft as the first message on the same model, draft taken out of the box, band idle', async ($, on) => {
  const log: string[] = []
  const argvs: string[][] = []
  wire(on, [], () => withMemory, (_, e) => (e.argv.includes('handoff') && argvs.push(e.argv), handoffOk(_, e)))
  const state = box(on, 'продолжаем', log)
  const ui = await mountBand($)

  await ui.press({ key: 'launch' })
  await ui.press({ key: 'send' })

  expect(log).toEqual(['/clear', 'submit [al-handoff]x[/al-handoff]\n\nпродолжаем']) // no /model
  expect(state.text).toBe('')
  const a = argvs[0]!
  expect(a.slice(a.indexOf('handoff'))).toEqual(['handoff', '--session', 'id', '--today', expect.stringMatching(/^\d{4}-\d\d-\d\d$/), '--memory-dir', 'C:/mem/memory', '--compress', 'ultra'])
  expect(await buttons(ui)).toEqual(['launch', 'details']) // not armed, not busy
  expect(await ui.findAll({ type: 'Button', text: T.busy })).toEqual([])
})

test('new worker, pick the model myself: /clear, handoff + draft left in the box, no submit, never a /model, one toast says what to do', async ($, on) => {
  const toasts: string[] = []
  const log: string[] = []
  on('ui.toast', (_: unknown, e: { text: string }) => (toasts.push(e.text), { value: undefined }))
  wire(on, [], () => ({ value: usage }), handoffOk)
  const state = box(on, 'продолжаем', log)
  const ui = await mountBand($)

  await ui.press({ key: 'launch' })
  await ui.press({ key: 'pick' })

  expect(log).toEqual(['/clear']) // no submit, no /model
  expect(state.text).toBe('[al-handoff]x[/al-handoff]\n\nпродолжаем')
  expect(toasts).toEqual([T.toast(T.pickThenEnter)])
  expect(await buttons(ui)).toEqual(['launch', 'details']) // not armed, not busy
  expect(await ui.findAll({ type: 'Button', text: T.busy })).toEqual([])
})

test('new worker: busy for the whole launch, the arm button is ignored meanwhile', async ($, on) => {
  const log: string[] = []
  let release = () => {}
  wire(on, [], () => ({ value: usage }), handoffOk)
  box(on, 'x', log, new Promise<void>(r => (release = r)))
  const ui = await mountBand($)

  await ui.press({ key: 'launch' })
  const pressed = ui.press({ key: 'send' })
  const again = ui.press({ key: 'send' }).catch(() => undefined) // a double click: either the button is gone or the launch refuses it
  for (let i = 0; i < 100 && !log.some(l => l.startsWith('submit')); i++) await new Promise(r => (globalThis as any).setTimeout(r, 10))
  expect(await ui.findAll({ type: 'Button', text: T.busy })).toHaveLength(1)
  await ui.press({ key: 'launch' })
  expect(await buttons(ui)).toEqual(['launch', 'details']) // the picker did not open

  release()
  await Promise.all([pressed, again])
  expect(log.filter(l => l === '/clear')).toHaveLength(1)
  expect(await ui.findAll({ type: 'Button', text: T.busy })).toEqual([])
})

test('new worker: a failed handoff leaves the chat alone, tells why and is kept for /al-health', async ($, on) => {
  const toasts: string[] = []
  const log: string[] = []
  const store = new Map<string, unknown>()
  on('ui.toast', (_: unknown, e: { text: string }) => (toasts.push(e.text), { value: undefined }))
  on('session.root', () => ({ value: 'C:/w' }))
  wire(on, [], () => ({ value: usage }), (_, e) => (e.argv.includes('handoff') ? { value: { exitCode: 1, stdout: '{"error":"no transcript"}', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } } : noTurn()), store)
  box(on, 'x', log)
  const ui = await mountBand($)

  await ui.press({ key: 'launch' })
  await ui.press({ key: 'pick' })

  expect(log).toEqual([])
  expect(toasts).toEqual([T.toast(T.handoffFailed('no transcript'))])
  expect(store.get('lastError')).toMatchObject({ where: 'launch', msg: T.handoffFailed('no transcript') })
  expect(await buttons(ui)).toEqual(['launch', 'details'])
  expect(await ui.findAll({ type: 'Button', text: T.newWorker })).toHaveLength(1) // busy ended
})

test('new worker, pick the model myself: the box refuses the text: readable reason and the saved path, not a raw code', async ($, on) => {
  const toasts: string[] = []
  on('ui.toast', (_: unknown, e: { text: string }) => (toasts.push(e.text), { value: undefined }))
  wire(on, [], () => ({ value: usage }), handoffOk)
  box(on, 'x', []).refuse = 'dialog' // the engine strips a code a hook writes itself, so the closure sees the code-less refusal here
  const ui = await mountBand($)

  await ui.press({ key: 'launch' })
  await ui.press({ key: 'pick' })

  expect(toasts).toEqual([T.toast(T.fillFailed(T.notSent, T.fillRefused.other, 'C:/h/id.md'))])
  expect(await buttons(ui)).toEqual(['launch', 'details'])
})

// ---- long memory ----
const hit = { conv: 'S', parent: '', ts: '2026-10-10T08:00:00Z', kind: 'owner', pos: 42, snippet: '«кэш» истёк' }
const hitLine = '[2026-10-10 owner conv=S pos=42] «кэш» истёк'
const bad = { path: 'C:/p/a.jsonl', error: 'PermissionError: x' }
const found = (extra: object = {}) => ran(JSON.stringify({ hits: [hit], errors: [], vector: 'ok', ...extra }))
const failed = (error: string) => ({ value: { exitCode: 1, stdout: JSON.stringify({ error }), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })

// A session that has started: cg.py answers `answer(argv)` and every run is recorded in `runs`; the tools and the index timer exist.
const started = async ($: any, on: any, answer: (a: string[]) => unknown, statuses: (string | undefined)[] = [], answerUsage: () => unknown = () => ({ value: usage }), env: Record<string, string> = {}, lastTurn: () => unknown = noTurn, store = new Map<string, unknown>()) => {
  mock.env(on, env) // no USERPROFILE: no settings file
  const m = { runs: [] as { argv: string[]; timeoutMs?: number }[], langs: [] as string[][], toasts: [] as string[], specs: [] as any[], store, clock: undefined as any }
  on('ui.toast', (_: unknown, e: { text: string }) => (m.toasts.push(e.text), { value: undefined }))
  on('tool.register', (_: unknown, e: unknown) => (m.specs.push(e), { value: { tool: 'x' } }))
  on('command.register', () => ({ value: { command: 'x' } }))
  on('session.root', () => ({ value: 'C:/w' }))
  on('session.cwd', () => ({ value: 'C:/w/plugin/scripts' })) // after a shell `cd`: must not reach the search
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  // argv: python, cg.py, --lang, <language>, then the command
  m.clock = wire(on, statuses, answerUsage, (_, e: any) => (m.runs.push({ argv: e.argv.slice(4), timeoutMs: e.init?.timeoutMs }), m.langs.push(e.argv.slice(2, 4)), e.argv.includes('last-turn') ? lastTurn() : answer(verb(e.argv.slice(4)))), m.store)
  await $.session.start({ cwd: 'C:/w', surface: null, isInteractive: false })
  return m
}
const recall = ($: any, input: object = { query: 'кэш' }) => $.tool.call({ tool: 'mcp__alethite__recall', ...input })
// the call without the mod's own flag, which goes before the command name (see --follow-job below)
const verb = (a: string[]) => (a[0] === '--follow-job' ? a.slice(1) : a)
const ofCg = <R extends { argv: string[] }>(m: { runs: R[] }, cmd: string) => m.runs.filter(r => verb(r.argv)[0] === cmd)

test('memory tools: all in the prompt, not behind ToolSearch', async ($, on) => {
  const m = await started($, on, () => ran('{}'))
  expect(m.specs.map(s => [s.name, s.isDeferred, s.description])).toEqual([
    ['recall', false, T.recallDescription],
    ['recall_more', false, T.recallMoreDescription],
    ['lesson_save', false, T.lessonSaveDescription],
  ])
  // stage 4: how to search for an error is told by the parameters, the description stays short
  expect(m.specs[0].inputSchema.properties.query.description).toBe(T.recallQuery)
  expect(m.specs[0].inputSchema.properties.also).toEqual({ type: 'array', items: { type: 'string' }, maxItems: 3, description: T.recallAlso })
})

test('recall: project by default, scope=all and the limit when asked; the model reads the hits', async ($, on) => {
  const m = await started($, on, () => found())
  expect(await recall($)).toEqual({ result: hitLine })
  await recall($, { query: 'q', scope: 'all', limit: 3 })
  const model = ['--model', 'embeddinggemma', '--compress', 'ultra']
  expect(ofCg(m, 'recall').map(r => r.argv)).toEqual([['recall', '--query=кэш', '--cwd', 'C:/w', '--limit', '8', ...model], ['recall', '--query=q', '--all', '--limit', '3', ...model]])
  expect(m.toasts).toEqual([])
})

test('recall: nothing found says what to try', async ($, on) => {
  await started($, on, () => ran('{"hits":[],"errors":[],"vector":"ok"}'))
  expect(await recall($)).toEqual({ result: T.noHits('кэш', 'project') })
})

test('recall: cg.py fails: the model reads an error, the owner a toast, /al-health the cause', async ($, on) => {
  const m = await started($, on, () => failed('OperationalError: x'))
  expect(await recall($)).toEqual({ deny: T.recallFailed('OperationalError: x') })
  expect(m.toasts).toEqual([T.toast(T.failed('recall', 'OperationalError: x'))])
  expect(m.store.get('lastError')).toMatchObject({ where: 'recall', msg: 'OperationalError: x' })
})

test('recall: no query means no search', async ($, on) => {
  const m = await started($, on, () => ran('{}'))
  expect(await recall($, {})).toEqual({ deny: T.recallFailed(T.noQuery) })
  expect(ofCg(m, 'recall')).toEqual([])
})

test('recall: unreadable transcripts reach the owner, the hits still reach the model', async ($, on) => {
  const m = await started($, on, () => found({ errors: [bad, bad] }))
  expect(await recall($)).toEqual({ result: hitLine })
  expect(await recall($)).toEqual({ result: hitLine })
  expect(m.toasts).toEqual([T.toast(T.indexErrors(2))]) // once per load, not on every search
  expect(m.store.get('lastError')).toMatchObject({ where: 'recall', msg: T.indexErrorFirst(2, bad.path, bad.error) })
})

test('recall: no vector search: the model reads why, the owner gets one toast per load, /al-health keeps the cause', async ($, on) => {
  const why = 'Ollama не отвечает: тест'
  const m = await started($, on, () => found({ vector: why }))
  const told = { result: [hitLine, T.vectorOff(why)].join('\n') }
  expect(await recall($)).toEqual(told)
  expect(await recall($)).toEqual(told)
  expect(m.toasts).toEqual([T.toast(T.vectorOff(why))])
  expect(m.store.get('lastError')).toMatchObject({ where: 'recall', msg: T.vectorOff(why) })
})

test('recall: two searches at once without vectors still give one toast', async ($, on) => {
  const m = await started($, on, () => found({ vector: 'Ollama is not answering: x' }))
  await Promise.all([recall($), recall($)])
  expect(m.toasts).toEqual([T.toast(T.vectorOff('Ollama is not answering: x'))])
})

test('recall: a later error is not pushed out of /al-health by every search without vectors', async ($, on) => {
  const m = await started($, on, () => found({ vector: 'Ollama is not answering: x' }))
  await recall($)
  m.store.set('lastError', { at: 1, where: 'sync', msg: 'later' })
  await recall($)
  expect(m.store.get('lastError')).toEqual({ at: 1, where: 'sync', msg: 'later' })
})

test('recall_more: reads around a hit', async ($, on) => {
  const m = await started($, on, () => ran(JSON.stringify({ rows: [{ ts: '2026-10-10T08:00:00Z', kind: 'owner', pos: 42, text: 'привет' }] })))
  expect(await $.tool.call({ tool: 'mcp__alethite__recall_more', conv: 'S', pos: 42 })).toEqual({ result: '[2026-10-10 owner pos=42] привет' })
  expect(ofCg(m, 'expand')[0]!.argv).toEqual(['expand', '--conv=S', '--pos=42', '--compress', 'ultra'])
})

test('recall_more: a failure is an error for the model and a toast for the owner', async ($, on) => {
  const m = await started($, on, () => failed('boom'))
  expect(await $.tool.call({ tool: 'mcp__alethite__recall_more', conv: 'S', pos: 42 })).toEqual({ deny: T.recallFailed('boom') })
  expect(m.toasts).toEqual([T.toast(T.failed('recall_more', 'boom'))])
})

const indexed = (errors: unknown[] = []) => ran(JSON.stringify({ files: 1, rows: 5, convs: 1, seconds: 0.5, errors }))
const embedded = (errors: unknown[] = []) => ran(JSON.stringify({ done: 5, pending: 0, seconds: 1.5, model: 'embeddinggemma', errors }))

test('index: 3 s after session.start, in the background, with errors on the status line', async ($, on) => {
  const statuses: (string | undefined)[] = []
  const m = await started($, on, a => (a[0] === 'embed' ? embedded() : indexed([bad])), statuses)
  expect(ofCg(m, 'index')).toEqual([]) // session.start did not wait for it, nor run it
  await m.clock.advance(3_000)
  expect(ofCg(m, 'index')).toEqual([{ argv: ['--follow-job', 'index'], timeoutMs: 600_000 }])
  expect(statuses).toContain(T.indexErrors(1))
  expect(m.toasts).toEqual([])
  expect(m.store.get('lastError')).toMatchObject({ where: 'index' })
})

test('index: a failed background run goes to the status line, /al-health and one toast', async ($, on) => {
  const statuses: (string | undefined)[] = []
  const m = await started($, on, () => failed('database is locked'), statuses)
  await m.clock.advance(3_000)
  expect(statuses).toContain(T.statusError('database is locked'))
  expect(m.toasts).toEqual([T.toast(T.catchUpFailed('index'))])
  expect(m.store.get('lastError')).toMatchObject({ where: 'index', msg: 'database is locked' })
  expect(ofCg(m, 'embed')).toEqual([]) // the same database would fail the same way, and the first cause must stay in /al-health
})

test('embed: right after the index in the same timer, 120 s of work at most, and the setting names the model', { options: { embedModel: 'bge-m3' } }, async ($, on) => {
  const m = await started($, on, a => (a[0] === 'embed' ? embedded() : a[0] === 'recall' ? found() : indexed()))
  await m.clock.advance(3_000)
  expect(m.runs.filter(r => verb(r.argv)[0] === 'index' || r.argv[0] === 'embed')).toEqual([
    { argv: ['--follow-job', 'index'], timeoutMs: 600_000 },
    { argv: ['embed', '--model', 'bge-m3', '--max-seconds', '120'], timeoutMs: 180_000 },
  ])
  await recall($)
  expect(ofCg(m, 'recall')[0]!.argv.slice(-4, -2)).toEqual(['--model', 'bge-m3'])
  expect(m.toasts).toEqual([])
})

// the embed run answers `answer`; the owner must see `msg` on the status line and in /al-health, and hear of it in one toast
const embedGoesWrong = async ($: any, on: any, answer: unknown, msg: string) => {
  const statuses: (string | undefined)[] = []
  const m = await started($, on, a => (a[0] === 'embed' ? answer : indexed()), statuses)
  await m.clock.advance(3_000)
  expect(statuses).toContain(T.statusError(msg))
  expect(m.store.get('lastError')).toMatchObject({ where: 'embed', msg })
  expect(m.toasts).toEqual([T.toast(T.catchUpFailed('embed'))])
}

test('embed: a failed run reaches the status line, /al-health and a toast', ($, on) => embedGoesWrong($, on, failed('Ollama не отвечает: x'), 'Ollama не отвечает: x'))
test('embed: errors in a finished run do too', ($, on) => embedGoesWrong($, on, embedded([{ error: 'RuntimeError: модель m не найдена' }]), 'RuntimeError: модель m не найдена'))

test('catch-up: the toast comes once per load of the mod, every failure is kept for /al-health', async ($, on) => {
  const m = await started($, on, a => (a[0] === 'embed' ? failed('Ollama не отвечает: x') : indexed()))
  await m.clock.advance(3_000)
  expect(m.toasts).toEqual([T.toast(T.catchUpFailed('embed'))])
  expect(m.store.get('lastError')).toMatchObject({ where: 'embed', msg: 'Ollama не отвечает: x' })
  m.store.delete('lastError')
  await $.session.start({ cwd: 'C:/w', surface: null, isInteractive: false }) // a second catch-up in the same load
  await m.clock.advance(3_000)
  expect(m.toasts).toEqual([T.toast(T.catchUpFailed('embed'))])
  expect(m.store.get('lastError')).toMatchObject({ where: 'embed', msg: 'Ollama не отвечает: x' })
})

test('toasts start with the mod name, once; status lines do not (the engine adds the name)', async ($, on) => {
  const statuses: (string | undefined)[] = []
  const m = await started($, on, a => (a[0] === 'embed' ? failed('Ollama не отвечает: x') : a[0] === 'recall' ? found({ errors: [bad], vector: 'нет' }) : a[0] === 'expand' ? failed('boom') : indexed([bad])), statuses)
  await m.clock.advance(3_000) // refresh, unreadable transcripts and a failed embed on the status line; the failed catch-up in a toast
  await recall($) // unreadable transcripts and no vector search: two toasts
  await $.tool.call({ tool: 'mcp__alethite__recall_more', conv: 'S', pos: 1 }) // a failure: a toast
  expect(statuses).toHaveLength(3)
  expect(m.toasts).toHaveLength(4)
  for (const t of statuses) expect(t).not.toContain('Alethite')
  for (const t of m.toasts) expect(t).toMatch(/^Alethite: (?!.*Alethite)/s)
})

test('exactly nine commands are registered, all al-*, none cg-*', async ($, on) => {
  const names: string[] = []
  on('command.register', (_: unknown, e: { name: string }) => (names.push(e.name), { value: { command: e.name } }))
  on('tool.register', () => ({ value: { tool: 'x' } }))
  mock.env(on, {})
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  wire(on, [], () => ({ value: usage }))
  await $.session.start({ cwd: 'C:/w', surface: null, isInteractive: false })
  expect(names).toEqual(['al-band', 'al-health', 'al-index', 'al-stats', 'al-sync', 'al-lessons', 'al-job', 'al-encrypt', 'al-index-dir'])
  expect(names.filter(n => n.startsWith('cg-'))).toEqual([])
})

test('--follow-job goes before the command name in the index run of a session start and in the status of /al-health, and in no other call', async ($, on) => {
  const m = await started($, on, a => (a[0] === 'embed' ? embedded() : a[0] === 'status' ? ran('{"db":"d","convs":0,"rows":0,"size_mb":0}') : a[0] === 'recall' ? found() : indexed()))
  await m.clock.advance(3_000)
  await ($ as any).command.run({ command: 'al-health', args: '' })
  await ($ as any).command.run({ command: 'al-index', args: '' })
  await recall($)
  expect(m.runs.filter(r => r.argv.includes('--follow-job')).map(r => r.argv)).toEqual([['--follow-job', 'index'], ['--follow-job', 'status', '--cwd=C:/w']])
  expect(ofCg(m, 'index').map(r => r.argv)).toEqual([['--follow-job', 'index'], ['index']]) // the owner's /al-index is a plain run
})

// item 2: the engine refuses a tool schema
test('session.start: a refused registration leaves the status line, the timers and the other registrations alone', async ($, on) => {
  const statuses: (string | undefined)[] = []
  const store = new Map<string, unknown>()
  const names = { commands: [] as string[], tools: [] as string[] }
  on('command.register', (_: unknown, e: { name: string }) => (names.commands.push(e.name), { value: { command: e.name } }))
  on('tool.register', (_: unknown, e: { name: string }) => (names.tools.push(e.name), { deny: 'schema refused' }))
  mock.env(on, {})
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  const clock = wire(on, statuses, () => ({ value: usage }), (_, e) => (e.argv.includes('index') ? indexed() : e.argv.includes('embed') ? embedded() : noTurn()), store)
  await $.session.start({ cwd: 'C:/w', surface: null, isInteractive: false })
  expect(statuses[0]).toMatch(/^ctx /) // the first refresh ran
  expect(statuses.at(-1)!.startsWith(T.statusError(''))).toBe(true) // ...and the refusal is the last word
  expect(statuses.at(-1)).toContain('schema refused')
  expect(store.get('lastError')).toMatchObject({ where: 'register', msg: expect.stringContaining('schema refused') })
  expect(names).toEqual({ commands: ['al-band', 'al-health', 'al-index', 'al-stats', 'al-sync', 'al-lessons', 'al-job', 'al-encrypt', 'al-index-dir'], tools: ['recall', 'recall_more', 'lesson_save'] }) // every tool was tried, the commands went through
  await clock.advance(30_000) // the timers run
  expect(statuses.at(-1)).toMatch(/^ctx /)
})

test('/al-index: updates, "rebuild" re-reads everything, the answer has the figures', async ($, on) => {
  const stats = { files: 3, rows: 120, convs: 7, seconds: 1.234, errors: [] }
  const m = await started($, on, () => ran(JSON.stringify(stats)))
  expect((await ($ as any).command.run({ command: 'al-index', args: '' })).text).toBe(T.cgIndexDone(stats))
  await ($ as any).command.run({ command: 'al-index', args: 'rebuild' })
  expect(ofCg(m, 'index').map(r => r.argv)).toEqual([['index'], ['index', '--rebuild']])
})

test('/al-index: unreadable transcripts and failures are shown in a toast', async ($, on) => {
  let answer: unknown = ran(JSON.stringify({ files: 0, rows: 0, convs: 0, seconds: 0, errors: [bad] }))
  const m = await started($, on, () => answer)
  await ($ as any).command.run({ command: 'al-index', args: '' })
  expect(m.toasts).toEqual([T.toast(T.indexErrors(1))])
  answer = failed('no such table')
  expect((await ($ as any).command.run({ command: 'al-index', args: '' })).text).toBe(T.cgIndexFailed)
  expect(m.toasts[1]).toBe(T.toast(T.failed('al-index', 'no such table')))
})

// ---- short memory ----
const facts = { model: 'claude-opus-5-5', promptModel: 'claude-opus-5-5', surfaces: [], tools: [], outputStyle: null, traits: [] }

test('prompt: the memory rule is the last section, on the session side, the same text every time', async ($, on) => {
  const engine = [{ id: 'intro', text: 'a', scope: 'shared' as const }, { id: 'env', text: 'b', scope: 'session' as const }]
  on('prompt.compose', () => ({ sections: engine }))
  const first = (await ($ as any).prompt.compose(facts)).sections
  expect(first).toEqual([...engine, { id: 'alethite:memory-rule', text: T.memoryRule, scope: 'session' }])
  expect((await ($ as any).prompt.compose(facts)).sections).toEqual(first)
})

const fact = 'C:\\Users\\A\\.claude\\projects\\X\\memory\\feedback_cache.md'
const done = { result: { type: 'create' } }

// The engine beneath a write: `rig.answer` is what the tool answers, `rig.toasts` what the owner saw; `write` is one Write (or Edit) call.
const rig = ($: any, on: any) => {
  const r = { answer: done as unknown, toasts: [] as string[], toastDeny: undefined as string | undefined, write: (file_path: string, tool = 'Write') => $.tool.call({ tool, file_path, content: 'x' }) }
  on('ui.toast', (_: unknown, e: { text: string }) => (r.toasts.push(e.text), r.toastDeny ? { deny: r.toastDeny } : { value: undefined }))
  on('tool.call', () => r.answer)
  return r
}

test('remembered: a fact written into the auto-memory folder is toasted by name, by Write and by Edit, the result untouched', async ($, on) => {
  const r = rig($, on)
  expect(await r.write(fact)).toEqual(done)
  await r.write(fact, 'Edit')
  expect(r.toasts).toEqual([T.toast(T.remembered('feedback_cache')), T.toast(T.remembered('feedback_cache'))])
})

test('remembered: no toast for MEMORY.md, a file elsewhere, a refused, an errored or a staged write; each result comes back as it was', async ($, on) => {
  const r = rig($, on)
  await r.write('C:/Users/A/.claude/projects/X/memory/MEMORY.md')
  await r.write('C:/_git/p/memory/a.md')
  r.answer = { deny: 'no' }
  expect(await r.write(fact)).toEqual({ deny: 'no' })
  r.answer = { isError: true, result: 'boom', text: 'boom' }
  expect(await r.write(fact)).toMatchObject({ isError: true, text: 'boom' })
  r.answer = { result: { staged: true } }
  await r.write(fact)
  expect(r.toasts).toEqual([])
})

test('remembered: a throw while judging the write does not break it; the cause goes to the status line and /al-health', async ($, on) => {
  const statuses: (string | undefined)[] = []
  const store = new Map<string, unknown>()
  const r = rig($, on)
  wire(on, statuses, () => ({ value: usage }), undefined, store)
  // a path that is no string still looks like a fact to the regex (it is coerced) and then has no .split
  expect(await r.write([fact] as unknown as string)).toEqual(done)
  expect(statuses.some(t => t?.startsWith(T.statusError('')))).toBe(true)
  expect(store.get('lastError')).toMatchObject({ where: 'remembered' })
  expect(r.toasts).toEqual([])
})

// ---- measurements ----
const NOTE = '---\nhost: PC-1\ndate: 2026-10-13\nusd_per_owner_msg: 4.5\n---\n# Замер\n\n| метрика | база | сейчас |\n|---|---|---|\n| $ на сообщение | $5.20 | $4.50 |'
const OTHER = '---\nhost: PC-2\ndate: 2026-10-01\nusd_per_owner_msg: 5.1\n---\n'
const flag = (a: string[], name: string) => a[a.indexOf(name) + 1]!

// The Obsidian server beneath the plugin: a vault in memory. `fail(tool)` says which calls break, `failure` how (a throw, or an answer with isError and the server's JSON).
const fakeVault = (on: any) => {
  const v = { files: new Map<string, string>(), calls: [] as string[], listLimit: undefined as number | undefined, fail: (_tool: string) => false, failure: 'throws' as 'throws' | 'isError', onWrite: (_path: string) => {} }
  const ok = (text: string) => ({ value: { content: [{ type: 'text', text }], isError: false } })
  on('mcp.call', (_: unknown, e: { server: string; tool: string; args: { path: string; directory: string; content?: string; limit?: number } }) => {
    const { path, directory, content } = e.args
    v.calls.push(`${e.server} ${e.tool} ${path ?? directory}`)
    if (v.fail(e.tool)) return v.failure === 'throws' ? { deny: 'connection refused' } : { value: { content: [{ type: 'text', text: JSON.stringify({ error: 'vault locked', errorCode: 'vault_locked' }) }], isError: true } }
    if (e.tool === 'create_vault_file') return v.onWrite(path), v.files.set(path, content!), ok('OK')
    if (e.tool === 'list_vault_files') {
      // as the server answers: at most `limit` files (200 when not asked), and `truncated` says whether more were there
      const all = [...v.files.keys()].filter(p => p.startsWith(directory + '/'))
      const max = e.args.limit ?? 200
      v.listLimit = e.args.limit
      return ok(JSON.stringify({ files: all.slice(0, max), truncated: all.length > max, total: all.length }))
    }
    const front = v.files.get(path)!.split('---')[1] ?? ''
    const frontmatter = Object.fromEntries([...front.matchAll(/^(\w+): (.*)$/gm)].map(([, k, x]) => [k, x === 'null' ? null : isNaN(Number(x)) ? x : Number(x)]))
    return ok(JSON.stringify({ path, frontmatter: Object.keys(frontmatter).length ? frontmatter : null, keys: Object.keys(frontmatter) }))
  })
  return v
}

const statsRig = async ($: any, on: any, answer: (a: string[]) => unknown = () => ran(JSON.stringify({ metrics: {}, note: NOTE, errors: [] })), env: Record<string, string> = { COMPUTERNAME: 'PC-1' }) => {
  const vault = fakeVault(on)
  const m = await started($, on, a => (a[0] === 'stats' ? answer(a) : ran('{}')), undefined, undefined, env)
  const stats = (args = '') => $.command.run({ command: 'al-stats', args }) as Promise<{ text: string }>
  const today = () => flag(ofCg(m, 'stats').at(-1)!.argv, '--today')
  return { vault, m, stats, today }
}

test('/al-stats: cg.py gets the period, the computer, the limits and the size of the memory rule; the figures are in the reply', async ($, on) => {
  const { m, stats, today } = await statsRig($, on)
  const { text } = await stats()
  const { argv, timeoutMs } = ofCg(m, 'stats')[0]!
  expect(argv.slice(0, 1)).toEqual(['stats'])
  expect(today()).toMatch(/^\d{4}-\d\d-\d\d$/)
  expect(Date.parse(today()) - Date.parse(flag(argv, '--since'))).toBe(7 * 86_400_000) // the default is a week
  expect(argv).not.toContain('--until')
  expect([flag(argv, '--host'), flag(argv, '--limits'), flag(argv, '--rule-chars'), timeoutMs]).toEqual(['PC-1', JSON.stringify(usage.rateLimits), String(T.memoryRule.length), 600_000])
  expect(text).toContain('| метрика | база | сейчас |')
  expect(text).not.toContain('host: PC-1') // the frontmatter is for the vault, not for the reply
})

test('/al-stats 30: the period is as long as asked', async ($, on) => {
  const { m, stats, today } = await statsRig($, on)
  await stats(' 30 ')
  expect(Date.parse(today()) - Date.parse(flag(ofCg(m, 'stats')[0]!.argv, '--since'))).toBe(30 * 86_400_000)
})

test('/al-stats: no sense in the days: say so, run nothing', async ($, on) => {
  const { m, stats } = await statsRig($, on)
  for (const bad of ['abc', '0', '-3', '2.5']) expect((await stats(bad)).text).toBe(T.cgStatsDays)
  expect(ofCg(m, 'stats')).toEqual([])
})

test('/al-stats: Obsidian up: the note and the summary are written, the outbox is empty', async ($, on) => {
  const { vault, m, stats, today } = await statsRig($, on)
  vault.files.set(`${T.statsFolder}/2026-10-01 PC-2.md`, OTHER)
  const { text } = await stats()
  expect(vault.files.get(notePath('PC-1', today()))).toBe(NOTE)
  const summary = vault.files.get(summaryPath())!
  expect(summary).toContain('PC-1')
  expect(summary).toContain('PC-2')
  expect(summary.split('\n').filter(l => /^\| 2026/.test(l))).toHaveLength(2) // the summary does not list itself
  expect(vault.calls[0]).toBe(`obsidian create_vault_file ${notePath('PC-1', today())}`)
  expect(m.store.get('outbox')).toEqual([])
  expect(m.toasts).toEqual([])
  expect(text).toContain(T.cgStatsSent(1))
})

test('/al-stats: Obsidian down: the error is shown, the note waits in the outbox, the next run sends it', async ($, on) => {
  const { vault, m, stats, today } = await statsRig($, on)
  vault.fail = () => true
  const { text } = await stats()
  expect(m.toasts).toHaveLength(1)
  expect(m.toasts[0]).toContain(T.failed('al-stats', '').trim())
  expect(m.store.get('lastError')).toMatchObject({ where: 'al-stats' })
  expect(m.store.get('outbox')).toEqual([{ path: notePath('PC-1', today()), content: NOTE }])
  expect(text).toContain(T.cgStatsQueued(1))
  expect(text).toContain('| метрика | база | сейчас |') // the figures are not lost with the vault
  expect(vault.files.size).toBe(0)

  vault.fail = () => false
  expect((await stats()).text).toContain(T.cgStatsSent(1)) // the same day: the new note replaces the queued one, not doubles it
  expect(vault.files.get(notePath('PC-1', today()))).toBe(NOTE)
  expect(m.store.get('outbox')).toEqual([])
})

test("/al-stats: an answer with isError is a failure too, with the server's words", async ($, on) => {
  const { vault, m, stats } = await statsRig($, on)
  vault.fail = () => true
  vault.failure = 'isError'
  await stats()
  expect(m.toasts[0]).toContain('vault locked')
  expect((m.store.get('outbox') as unknown[]).length).toBe(1)
})

test('/al-stats: what waited from an earlier day goes first', async ($, on) => {
  const { vault, m, stats, today } = await statsRig($, on)
  const old = { path: notePath('PC-1', '2026-01-01'), content: 'old' }
  await m.store.set('outbox', [old])
  const duringSend: unknown[] = []
  vault.onWrite = () => duringSend.push(m.store.get('outbox'))
  const { text } = await stats()
  expect(duringSend[0]).toEqual([old, { path: notePath('PC-1', today()), content: NOTE }]) // saved before the first send, in case the send is cut short
  expect(vault.calls.slice(0, 2)).toEqual([`obsidian create_vault_file ${old.path}`, `obsidian create_vault_file ${notePath('PC-1', today())}`])
  expect(vault.files.get(old.path)).toBe('old')
  expect(m.store.get('outbox')).toEqual([])
  expect(text).toContain(T.cgStatsSent(2))
})

test('/al-stats: the vault dies after the first of two: only the rest stays queued', async ($, on) => {
  const { vault, m, stats, today } = await statsRig($, on)
  const old = { path: notePath('PC-1', '2026-01-01'), content: 'old' }
  await m.store.set('outbox', [old])
  vault.fail = () => vault.files.size === 1
  const { text } = await stats()
  expect(vault.files.has(old.path)).toBe(true)
  expect(m.store.get('outbox')).toEqual([{ path: notePath('PC-1', today()), content: NOTE }])
  expect(text).toContain(T.cgStatsQueued(1))
})

test('/al-stats: the summary cannot be built: the note is safe, the owner is told', async ($, on) => {
  const { vault, m, stats, today } = await statsRig($, on)
  vault.fail = tool => tool === 'list_vault_files'
  const { text } = await stats()
  expect(vault.files.get(notePath('PC-1', today()))).toBe(NOTE)
  expect(m.store.get('outbox')).toEqual([])
  expect(m.toasts[0]).toContain('al-stats-summary')
  expect(m.store.get('lastError')).toMatchObject({ where: 'al-stats-summary' })
  expect(text).toContain(T.cgStatsSummaryFailed)
})

test('/al-stats: the folder listing is truncated: the note is written, the summary is not rebuilt, the owner is told', async ($, on) => {
  const { vault, m, stats, today } = await statsRig($, on)
  for (let i = 0; i < 1001; i++) vault.files.set(`${T.statsFolder}/old-${i}.md`, OTHER)
  const { text } = await stats()
  expect(vault.listLimit).toBe(1000)
  expect(vault.files.get(notePath('PC-1', today()))).toBe(NOTE)
  expect(vault.files.has(summaryPath())).toBe(false)
  expect(m.toasts[0]).toContain(T.summaryTruncated)
  expect(m.store.get('lastError')).toMatchObject({ where: 'al-stats-summary' })
  expect(text).toContain(T.cgStatsSummaryFailed)
})

test('/al-stats: the summary rebuild skips the summary itself, a non-.md file and a note in a subfolder', async ($, on) => {
  const { vault, stats, today } = await statsRig($, on)
  vault.files.set(summaryPath(), 'old summary')
  vault.files.set(`${T.statsFolder}/2026-10-01 PC-2.md`, OTHER)
  vault.files.set(`${T.statsFolder}/chart.png`, 'x')
  vault.files.set(`${T.statsFolder}/old/2026-01-01 PC-3.md`, OTHER)
  await stats()
  const read = vault.calls.filter(c => c.startsWith('obsidian get_note_properties')).sort()
  expect(read).toEqual([`obsidian get_note_properties ${T.statsFolder}/2026-10-01 PC-2.md`, `obsidian get_note_properties ${notePath('PC-1', today())}`].sort())
  expect(vault.files.get(summaryPath())).toContain('PC-2')
})

test('/al-stats: cg.py reports what made the figures wrong: a toast, /al-health, a word in the reply; the note is written all the same', async ($, on) => {
  const errors = [{ path: 'audit', error: 'files_unreadable: 2' }]
  const { vault, m, stats, today } = await statsRig($, on, () => ran(JSON.stringify({ metrics: {}, note: NOTE, errors })))
  const { text } = await stats()
  expect(m.toasts).toEqual([T.toast(T.indexErrors(1))])
  expect(m.store.get('lastError')).toMatchObject({ where: 'al-stats', msg: T.indexErrorFirst(1, 'audit', 'files_unreadable: 2') })
  expect(vault.files.get(notePath('PC-1', today()))).toBe(NOTE)
  expect(text).toContain(T.cgStatsErrors(1))
  expect(text).toContain(T.cgStatsSent(1))
})

test('/al-stats: cg.py fails: a toast, nothing is queued or sent', async ($, on) => {
  const { vault, m, stats } = await statsRig($, on, () => failed('PermissionError: x'))
  expect((await stats()).text).toBe(T.cgStatsFailed)
  expect(m.toasts).toEqual([T.toast(T.failed('al-stats', 'PermissionError: x'))])
  expect(m.store.get('outbox')).toBeUndefined()
  expect(vault.calls).toEqual([])
})

test('/al-stats: no computer name, no note', async ($, on) => {
  const { vault, m, stats } = await statsRig($, on, undefined, {})
  expect((await stats()).text).toBe(T.cgStatsFailed)
  expect(m.toasts).toEqual([T.toast(T.failed('al-stats', T.noHost))])
  expect(vault.calls).toEqual([])
})

// ---- languages ----
test('cg.py is told the language of the mod: English by default', async ($, on) => {
  const m = await started($, on, a => (a[0] === 'embed' ? embedded() : indexed()))
  await m.clock.advance(3_000)
  expect(m.langs.length).toBeGreaterThan(1)
  for (const lang of m.langs) expect(lang).toEqual(['--lang', 'en'])
  expect((await ($ as any).command.run({ command: 'al-band', args: '' })).text).toBe(T.cgBandOn)
})

test('language ru: the mod speaks Russian to the owner and to the model, and so does cg.py', { options: { language: 'ru' } }, async ($, on) => {
  on('prompt.compose', () => ({ sections: [] }))
  const m = await started($, on, a => (a[0] === 'embed' ? embedded() : indexed()))
  await m.clock.advance(3_000)
  for (const lang of m.langs) expect(lang).toEqual(['--lang', 'ru'])
  expect((await ($ as any).command.run({ command: 'al-band', args: '' })).text).toBe(ru.cgBandOn)
  expect(m.specs.map(s => s.description)).toEqual([ru.recallDescription, ru.recallMoreDescription, ru.lessonSaveDescription])
  expect((await ($ as any).prompt.compose(facts)).sections).toEqual([{ id: 'alethite:memory-rule', text: ru.memoryRule, scope: 'session' }])
})

test('language uk: Ukrainian to the owner and to the model, and to cg.py', { options: { language: 'uk' } }, async ($, on) => {
  const m = await started($, on, a => (a[0] === 'embed' ? embedded() : indexed()))
  await m.clock.advance(3_000)
  for (const lang of m.langs) expect(lang).toEqual(['--lang', 'uk'])
  expect((await ($ as any).command.run({ command: 'al-band', args: '' })).text).toBe(uk.cgBandOn)
  expect(m.specs.map(s => s.description)).toEqual([uk.recallDescription, uk.recallMoreDescription, uk.lessonSaveDescription])
})

test('a language the mod does not know is English', { options: { language: 'xx' } }, async ($, on) => {
  const m = await started($, on, () => indexed())
  await m.clock.advance(3_000)
  expect(m.langs[0]).toEqual(['--lang', 'en'])
  expect((await ($ as any).command.run({ command: 'al-band', args: '' })).text).toBe(T.cgBandOn)
})

// ---- the mod's own settings file ----
const SETTINGS = 'C:\\h\\.claude\\alethite\\settings.json'
const OLD_SETTINGS = 'C:\\h\\.claude\\cacheguard\\settings.json' // where the file lies until cg.py has moved the folder (its first run)
const home = { USERPROFILE: 'C:\\h' }
// the file system holds `file` at SETTINGS (undefined: no file there), or the files of `at` (path → text); what the mod writes is collected. A
// Windows path reaches the hooks resolved against the folder of the run on Linux, so the path is known by its end
const isSettings = (path: string) => path.endsWith(SETTINGS)
const settingsRig = (on: any, file: string | undefined, at: Record<string, string> = file === undefined ? {} : { [SETTINGS]: file }) => {
  const written: [string, string][] = []
  const key = (path: string) => Object.keys(at).find(k => path.endsWith(k))
  const isFolder = (path: string) => Object.keys(at).some(k => path.endsWith(k.slice(0, k.lastIndexOf('\\')))) // the folder of a file is there with it
  on('fs.exists', (_: unknown, e: { path: string }) => ({ value: key(e.path) !== undefined || isFolder(e.path) }))
  on('fs.read', (_: unknown, e: { path: string }) => (key(e.path) !== undefined ? { value: at[key(e.path)!] } : { deny: 'ENOENT' }))
  on('fs.write', (_: unknown, e: { path: string; text: string }) => (written.push([e.path, e.text]), { value: undefined }))
  return written
}

test('settings file: its values win over /config, a key that is no setting is named in a toast, /al-health names the file', { options: { syncDir: 'D:\\old' } }, async ($, on) => {
  settingsRig(on, '{ "language": "ru", "syncDir": "Z:\\\\cg", "colour": 1 }')
  const m = await started($, on, a => (a[0] === 'sync' ? synced() : ran('{}')), [], undefined, home)
  await ($ as any).command.run({ command: 'al-sync', args: '' })
  expect(ofCg(m, 'sync')[0]!.argv[1]).toBe('--sync-dir=Z:\\cg')
  expect(m.langs.at(-1)).toEqual(['--lang', 'ru'])
  expect(m.toasts).toEqual([T.toast(T.failed('settings', ru.settingsBad!(SETTINGS, ['colour'])))])
  expect((await ($ as any).command.run({ command: 'al-health', args: '' })).text).toContain(ru.healthSettings!(SETTINGS))
})

test('settings file: cg.py writes indexCipher there on "encrypt on": it is a setting of the mod, no bad key', async ($, on) => {
  settingsRig(on, '{ "shareCipher": "cryptography", "indexCipher": "efs" }')
  const m = await started($, on, a => (a[0] === 'encrypt' ? ran('{"action":"on","done":true,"notes":[]}') : ran('{}')), [], undefined, home)
  await ($ as any).command.run({ command: 'al-encrypt', args: 'on' })
  expect(m.toasts).toEqual([T.toast(T.encryptWindow)]) // no settingsBad toast
  expect(ofCg(m, 'encrypt')[0]!.argv).toContain('--index-cipher=efs')
})

test('settings file: /al-encrypt reads it again: a chat not reloaded after the edit must not turn the index back with its old ciphers', async ($, on) => {
  let file = '{ "shareCipher": "sqlcipher", "indexCipher": "sqlcipher" }'
  on('fs.exists', (_: unknown, e: { path: string }) => ({ value: isSettings(e.path) }))
  on('fs.read', (_: unknown, e: { path: string }) => (isSettings(e.path) ? { value: file } : { deny: 'ENOENT' }))
  const m = await started($, on, a => (a[0] === 'encrypt' ? ran('{"action":"on","done":true,"notes":[]}') : ran('{}')), [], undefined, home)
  file = '{ "shareCipher": "cryptography", "indexCipher": "efs" }' // edited, the chat not reloaded
  await ($ as any).command.run({ command: 'al-encrypt', args: 'on' })
  expect(ofCg(m, 'encrypt')[0]!.argv).toEqual(['encrypt', 'on', '--sync-dir=', '--share-cipher=cryptography', '--index-cipher=efs', '--model', 'embeddinggemma'])
  file = '{ "indexCipher": "sqlcipher", }' // broken now: the values it had hold, and the owner is told
  await ($ as any).command.run({ command: 'al-encrypt', args: 'on' })
  expect(ofCg(m, 'encrypt')[1]!.argv).toContain('--index-cipher=efs')
  expect(m.toasts.filter(x => x.includes(SETTINGS))).toHaveLength(1)
})

test('settings file: missing, it is written with the values of /config and nothing is said', { options: { language: 'uk' } }, async ($, on) => {
  const written = settingsRig(on, undefined)
  const m = await started($, on, () => ran('{}'), [], undefined, home)
  expect(written.map(([path]) => isSettings(path))).toEqual([true]) // neither folder has one: it is born in the new one
  expect(JSON.parse(written[0]![1])).toMatchObject({ language: 'uk', syncDir: '', compression: 'ultra', python: 'python', indexCipher: 'sqlcipher', shareCipher: 'sqlcipher' })
  expect(m.toasts).toEqual([])
})

test('reads the old settings file when the new one is missing: nothing is written, so the new folder is not made before cg.py moves the old one', { options: { language: 'uk', syncDir: 'D:\\old' } }, async ($, on) => {
  const written = settingsRig(on, undefined, { [OLD_SETTINGS]: '{ "language": "ru", "syncDir": "Z:\\\\cg" }' })
  const m = await started($, on, a => (a[0] === 'sync' ? synced() : ran('{}')), [], undefined, home)
  await ($ as any).command.run({ command: 'al-sync', args: '' })
  expect(ofCg(m, 'sync')[0]!.argv[1]).toBe('--sync-dir=Z:\\cg') // the values of the old file hold
  expect(m.langs.at(-1)).toEqual(['--lang', 'ru'])
  expect((await ($ as any).command.run({ command: 'al-health', args: '' })).text).toContain(ru.healthSettings!(OLD_SETTINGS)) // the file in use is named
  expect(written).toEqual([])
  expect(m.toasts).toEqual([])
})

test('prefers the new settings file when both exist', { options: { language: 'uk' } }, async ($, on) => {
  const written = settingsRig(on, undefined, { [SETTINGS]: '{ "language": "ru" }', [OLD_SETTINGS]: '{ "language": "en" }' })
  const m = await started($, on, () => indexed(), [], undefined, home)
  await ($ as any).command.run({ command: 'al-index', args: '' })
  expect(m.langs.at(-1)).toEqual(['--lang', 'ru'])
  expect((await ($ as any).command.run({ command: 'al-health', args: '' })).text).toContain(ru.healthSettings!(SETTINGS))
  expect(written).toEqual([])
})

test('settings file: an old folder with no settings file in it gets the default one, the new folder is not made before cg.py moves the old one', { options: { language: 'uk' } }, async ($, on) => {
  const written = settingsRig(on, undefined, { 'C:\\h\\.claude\\cacheguard\\index.db': '' })
  await started($, on, () => ran('{}'), [], undefined, home)
  expect(written.map(([path]) => path.endsWith(OLD_SETTINGS))).toEqual([true])
  expect(written.some(([path]) => isSettings(path))).toBe(false)
})

test('settings file: /al-encrypt finds it where it is now, after cg.py moved the folder under a chat that read the old one', async ($, on) => {
  const at: Record<string, string> = { [OLD_SETTINGS]: '{ "shareCipher": "sqlcipher", "indexCipher": "sqlcipher" }' }
  const written = settingsRig(on, undefined, at)
  const m = await started($, on, a => (a[0] === 'encrypt' ? ran('{"action":"on","done":true,"notes":[]}') : ran('{}')), [], undefined, home)
  at[SETTINGS] = '{ "shareCipher": "cryptography", "indexCipher": "efs" }' // the move: the old file is gone, the settings are in the new folder
  delete at[OLD_SETTINGS]
  await ($ as any).command.run({ command: 'al-encrypt', args: 'on' })
  expect(ofCg(m, 'encrypt')[0]!.argv).toContain('--index-cipher=efs')
  expect(written).toEqual([])
})

test('settings file: /al-health names it where it is now, after cg.py moved the folder under a chat that read the old one', async ($, on) => {
  const at: Record<string, string> = { [OLD_SETTINGS]: '{ }' }
  settingsRig(on, undefined, at)
  await started($, on, () => ran('{}'), [], undefined, home)
  const health = async () => (await ($ as any).command.run({ command: 'al-health', args: '' })).text
  expect(await health()).toContain(T.healthSettings(OLD_SETTINGS))
  at[SETTINGS] = '{ }' // the move: the old file is gone, the settings are in the new folder
  delete at[OLD_SETTINGS]
  expect(await health()).toContain(T.healthSettings(SETTINGS))
})

test('settings file: broken JSON: a toast with the file, and the values of /config hold', { options: { language: 'uk' } }, async ($, on) => {
  const written = settingsRig(on, '{ "language": "ru", }')
  const m = await started($, on, () => indexed(), [], undefined, home)
  await ($ as any).command.run({ command: 'al-index', args: '' })
  expect(m.langs.at(-1)).toEqual(['--lang', 'uk'])
  expect(m.toasts).toHaveLength(1)
  expect(m.toasts[0]).toContain(SETTINGS)
  expect(written).toEqual([]) // the owner's file is never overwritten
})

// ---- sync of two computers ----
const SHARE = '\\\\NAS\\share\\alethite'
const synced = (over: object = {}) =>
  ran(JSON.stringify({ machine: 'HOMEPC', pushed: 3, pulled: 5, vectors: { pushed: 3, pulled: 5 }, forgotten: 0, skipped_vectors: 0, peers: { NOTEBOOK: 2 }, memory: { pushed: 0, pulled: 1, conflicts: 0 }, warnings: [], errors: [], ...over }))
const unreachable = { where: 'sync', error: `the sync folder is unavailable: ${SHARE} (FileNotFoundError: x)` }

test('/al-sync: off without a folder: says where to set it and runs nothing', async ($, on) => {
  const m = await started($, on, () => synced())
  expect((await ($ as any).command.run({ command: 'al-sync', args: '' })).text).toBe(T.cgSyncOff)
  expect(ofCg(m, 'sync')).toEqual([])
})

test('/al-sync: the folder, the model, the auto-memory folder and the project root go to cg.py; the reply sums it up', { options: { syncDir: ` ${SHARE} ` } }, async ($, on) => {
  const m = await started($, on, a => (a[0] === 'sync' ? synced() : ran('{}')), [], () => withMemory)
  const { text } = await ($ as any).command.run({ command: 'al-sync', args: '' })
  const [run] = ofCg(m, 'sync')
  expect(run).toEqual({ argv: ['sync', `--sync-dir=${SHARE}`, '--model', 'embeddinggemma', '--max-seconds', '60', '--memory-dir=C:/mem/memory', '--project-cwd=C:/w', '--share-cipher=sqlcipher'], timeoutMs: 120_000 })
  expect(text.split('\n')).toEqual([
    T.cgSyncDone({ pushed: 3, pulled: 5, vectors: { pushed: 3, pulled: 5 } }),
    T.cgSyncPeers('NOTEBOOK 2'),
    T.syncMemory({ pushed: 0, pulled: 1, conflicts: 0 }),
  ])
  expect(m.toasts).toEqual([])
})

test('/al-sync: a folder pasted with Explorer\'s quotes is the folder', { options: { syncDir: ` "${SHARE}" ` } }, async ($, on) => {
  const m = await started($, on, a => (a[0] === 'sync' ? synced() : ran('{}')))
  await ($ as any).command.run({ command: 'al-sync', args: '' })
  expect(ofCg(m, 'sync')[0]!.argv[1]).toBe(`--sync-dir=${SHARE}`)
})

test('/al-sync: the cipher of the sync folder is the setting', { options: { syncDir: SHARE, shareCipher: 'cryptography' } }, async ($, on) => {
  const m = await started($, on, a => (a[0] === 'sync' ? synced() : ran('{}')))
  await ($ as any).command.run({ command: 'al-sync', args: 'skip NOTEBOOK 12' })
  expect(ofCg(m, 'sync')[0]!.argv.slice(-4)).toEqual(['--share-cipher=cryptography', '--skip-batch', 'NOTEBOOK', '12'])
})

test('/al-sync: no auto-memory folder in this chat: no memory step, and the reply says so', { options: { syncDir: SHARE } }, async ($, on) => {
  const m = await started($, on, a => (a[0] === 'sync' ? synced({ memory: null }) : ran('{}')))
  const { text } = await ($ as any).command.run({ command: 'al-sync', args: '' })
  expect(ofCg(m, 'sync')[0]!.argv.some(a => a.startsWith('--memory-dir'))).toBe(false)
  expect(text).toContain(T.syncMemoryOff)
})

test('/al-sync skip: the owner goes on past a broken batch; without a computer and a number, how to ask', { options: { syncDir: SHARE } }, async ($, on) => {
  const m = await started($, on, a => (a[0] === 'sync' ? synced() : ran('{}')))
  await ($ as any).command.run({ command: 'al-sync', args: 'skip NOTEBOOK 12' })
  expect(ofCg(m, 'sync')[0]!.argv.slice(-3)).toEqual(['--skip-batch', 'NOTEBOOK', '12'])
  expect((await ($ as any).command.run({ command: 'al-sync', args: 'skip 12' })).text).toBe(T.cgSyncSkipUsage)
  expect(ofCg(m, 'sync')).toHaveLength(1)
})

test('/al-sync: errors of the run are in the reply, in one toast, and /al-health keeps the first', { options: { syncDir: SHARE } }, async ($, on) => {
  const broken = { where: 'pull', machine: 'NOTEBOOK', batch: 3, error: 'batch 3 is broken' }
  const m = await started($, on, a => (a[0] === 'sync' ? synced({ errors: [broken, unreachable] }) : ran('{}')))
  const { text } = await ($ as any).command.run({ command: 'al-sync', args: '' })
  expect(text).toContain(T.cgSyncError('batch 3 is broken'))
  expect(text).toContain(T.cgSyncError(unreachable.error))
  expect(m.toasts).toEqual([T.toast(T.syncErrors(2))])
  expect(m.store.get('lastError')).toMatchObject({ where: 'al-sync', msg: 'batch 3 is broken' })
})

test('/al-sync: cg.py fails: a toast and a short reply', { options: { syncDir: SHARE } }, async ($, on) => {
  const m = await started($, on, a => (a[0] === 'sync' ? failed('OperationalError: database is locked') : ran('{}')))
  expect((await ($ as any).command.run({ command: 'al-sync', args: '' })).text).toBe(T.cgSyncFailed)
  expect(m.toasts).toEqual([T.toast(T.failed('al-sync', 'OperationalError: database is locked'))])
})

test('background: the sync follows the index and the vectors, 60 s in cg.py, 120 s for the process', { options: { syncDir: SHARE } }, async ($, on) => {
  const m = await started($, on, a => (a[0] === 'sync' ? synced() : a[0] === 'embed' ? embedded() : indexed()))
  await m.clock.advance(3_000)
  const steps = m.runs.filter(r => r.argv[0] !== 'last-turn').map(r => [verb(r.argv)[0], r.timeoutMs])
  // the lessons are read at the start and again after the sync, which may have brought the other computer's (stage 4)
  expect(steps).toEqual([['lessons', 120_000], ['index', 600_000], ['embed', 180_000], ['sync', 120_000], ['lessons', 120_000], ['status', 120_000], ['savings', 120_000]])
  expect(ofCg(m, 'sync')[0]!.argv).toContain('--max-seconds')
  expect(m.toasts).toEqual([])
})

test('background: no folder, no sync', async ($, on) => {
  const m = await started($, on, a => (a[0] === 'embed' ? embedded() : indexed()))
  await m.clock.advance(3_000)
  expect(ofCg(m, 'sync')).toEqual([])
})

test('background: the sync runs even when the vectors failed; both are told', { options: { syncDir: SHARE } }, async ($, on) => {
  const m = await started($, on, a => (a[0] === 'sync' ? synced() : a[0] === 'embed' ? failed('Ollama is not answering: x') : indexed()))
  await m.clock.advance(3_000)
  expect(ofCg(m, 'sync')).toHaveLength(1)
  expect(m.toasts).toEqual([T.toast(T.catchUpFailed('embed'))])
})

test('background: an unavailable folder: the status line, /al-health and one toast per load; the local memory goes on', { options: { syncDir: SHARE } }, async ($, on) => {
  const statuses: (string | undefined)[] = []
  const m = await started($, on, a => (a[0] === 'sync' ? synced({ pushed: 0, pulled: 0, errors: [unreachable] }) : a[0] === 'embed' ? embedded() : a[0] === 'recall' ? found() : indexed()), statuses)
  await m.clock.advance(3_000)
  expect(statuses).toContain(T.statusError(unreachable.error))
  expect(m.store.get('lastError')).toMatchObject({ where: 'sync', msg: unreachable.error })
  expect(m.toasts).toEqual([T.toast(T.catchUpFailed('sync'))])
  await $.session.start({ cwd: 'C:/w', surface: null, isInteractive: false }) // a second catch-up in the same load: no second toast
  await m.clock.advance(3_000)
  expect(m.toasts).toEqual([T.toast(T.catchUpFailed('sync'))])
  expect(await recall($)).toEqual({ result: hitLine })
})

test('/al-health: the folder, the computers, their last batches, the errors', { options: { syncDir: SHARE } }, async ($, on) => {
  const sync = {
    machine: 'HOMEPC',
    last: { at: '2026-10-19T10:00:00Z', dir: SHARE, pushed: 3, pulled: 5, memory: null, errors: 1, error: 'batch 8 is broken' },
    peers: [
      { machine: 'HOMEPC', last_batch: 9, skipped: [], model: 'embeddinggemma', error: null },
      { machine: 'NOTEBOOK', last_batch: 7, skipped: [4], model: 'embeddinggemma', error: 'batch 8 is broken' },
    ],
    origins: { 'HOMEPC': 100, NOTEBOOK: 40 },
  }
  const status = { db: 'C:/x/mem.db', convs: 12, rows: 140, last_update: null, size_mb: 1.5, sync }
  wire(on, [], () => ({ value: { context: { window: 1_000_000 }, rateLimits: [] } }), (_, e) => (e.argv.includes('status') ? ran(JSON.stringify(status)) : noTurn()))
  on('session.root', () => ({ value: 'C:/w' }))
  const { text } = await ($ as any).command.run({ command: 'al-health', args: '' })
  for (const line of [
    T.healthSync(SHARE, 'HOMEPC'),
    T.healthOrigins('HOMEPC 100, NOTEBOOK 40'),
    T.healthOwnBatches('HOMEPC', 9),
    T.healthPeer('NOTEBOOK', 7, 'embeddinggemma'),
    T.healthPeerSkipped('NOTEBOOK', '4'),
    T.healthPeerError('NOTEBOOK', 'batch 8 is broken'),
    T.healthSyncError('batch 8 is broken'),
  ])
    expect(text).toContain(line)
})

test('/al-health: sync off is said', async ($, on) => {
  wire(on, [], () => ({ value: { context: { window: 1_000_000 }, rateLimits: [] } }), (_, e) => (e.argv.includes('status') ? ran('{"db":"d","convs":0,"rows":0,"size_mb":0}') : noTurn()))
  on('session.root', () => ({ value: 'C:/w' }))
  expect((await ($ as any).command.run({ command: 'al-health', args: '' })).text).toContain(T.healthSyncOff)
})

// ---- stage 3: compression and the memory in the context ----
test('compression: the setting reaches recall, recall_more and the handoff', { options: { compression: 'lite' } }, async ($, on) => {
  const m = await started($, on, a => (a[0] === 'expand' ? ran('{"rows":[]}') : found()))
  await recall($)
  await $.tool.call({ tool: 'mcp__alethite__recall_more', conv: 'S', pos: 1 })
  expect(ofCg(m, 'recall')[0]!.argv.slice(-2)).toEqual(['--compress', 'lite'])
  expect(ofCg(m, 'expand')[0]!.argv.slice(-2)).toEqual(['--compress', 'lite'])
})

test('/al-health: the compression level and what MEMORY.md takes of every request', { options: { compression: 'full' } }, async ($, on) => {
  const files = [{ path: 'C:/mem/memory/MEMORY.md', type: 'AutoMem', tokens: 950 }]
  wire(on, [], () => ({ value: { context: { window: 1_000_000, breakdown: { memoryFiles: files } }, rateLimits: [] } }), (_, e) =>
    e.argv.includes('status') ? ran('{"db":"d","convs":0,"rows":0,"size_mb":0}') : noTurn(),
  )
  on('session.root', () => ({ value: 'C:/w' }))
  on('fs.list', () => ({ value: [] }))
  const { text } = await ($ as any).command.run({ command: 'al-health', args: '' })
  expect(text).toContain(T.compressionLine('full'))
  expect(text).toContain(T.memoryIndexTokens(950))
})

test('/al-stats: cg.py gets what MEMORY.md takes and the compression level', async ($, on) => {
  const { m, stats } = await statsRig($, on)
  await stats()
  const { argv } = ofCg(m, 'stats')[0]!
  expect([flag(argv, '--memory-tokens'), flag(argv, '--compression')]).toEqual(['0', 'ultra'])
})

// ---- lessons (stage 4) ----
const lesson = {
  id: 7,
  conv: 'lessons-abc',
  pos: 1792000000000,
  ts: '2026-10-20T10:00:00.000Z',
  text: 'Action: gh api in a loop\nSymptom: HTTP 429\nCause: 120 requests an hour\nTrigger: gh api /repos',
  trigger: 'gh api /repos',
  from: null,
}
const listed = (list: object[] = [lesson]) => ran(JSON.stringify({ lessons: list }))
// cg.py answers the lesson commands; `found` is what lessons-match finds for any message
const lessonRig = async ($: any, on: any, found: object[] = [{ ...lesson, score: 0.81 }], list: object[] = [lesson], options: { context?: string[][] } = {}) => {
  const contexts = options.context ?? []
  on('prompt.submit', (_: unknown, e: { text: string; context?: readonly string[] }) => (contexts.push([...(e.context ?? [])]), { text: e.text, context: e.context }))
  on('tool.call', { tool: 'Bash' }, () => ({ result: 'ran' }))
  const m = await started($, on, a =>
    a[0] === 'lessons' ? listed(list) : a[0] === 'lessons-match' ? ran(JSON.stringify({ lessons: found, vector: 'ok' })) : a[0] === 'lesson-save' ? ran(JSON.stringify({ id: 9, conv: 'lessons-abc', pos: 1, text: 'Action: x\nSymptom: y\nCause: z', new: true, vector: 'ok' })) : a[0] === 'forget' ? ran('{"forgotten":1}') : ran('{}'),
  )
  await ($ as any).command.run({ command: 'al-lessons', args: '' }) // the list, awaited: the start reads it in the background
  return { m, contexts }
}
const typed = ($: any, text: string, extra: object = {}) => $.prompt.submit({ text, wait: false, origin: { kind: 'composer' }, ...extra })
// the gate's denial of the lesson above, between the marks the index cuts out (transcripts.py)
const gateText = () => ['[al-lessons]', T.lessonGate('Action: gh api in a loop | Symptom: HTTP 429 | Cause: 120 requests an hour | Trigger: gh api /repos'), '[/al-lessons]'].join('\n')

test('lesson_save: the fields go to cg.py one item each, the owner gets a toast, the model the lesson', async ($, on) => {
  const { m } = await lessonRig($, on)
  const r = await ($ as any).tool.call({ tool: 'mcp__alethite__lesson_save', action: '-x', symptom: 'HTTP 429', cause: 'limit', fix: '', trigger: 7 })
  expect(ofCg(m, 'lesson-save')[0]!.argv).toEqual(['lesson-save', '--cwd', 'C:/w', '--model', 'embeddinggemma', '--action=-x', '--symptom=HTTP 429', '--cause=limit'])
  expect(r).toEqual({ result: T.lessonSaved('Action: x\nSymptom: y\nCause: z') })
  expect(m.toasts).toEqual([T.toast(T.lessonToast('Action: x | Symptom: y | Cause: z'))]) // the whole lesson on one line, Fix included
})

test('lessons: one that fits goes beside the owner\'s message once a chat, in the hidden context; nothing else is asked or added', async ($, on) => {
  const contexts: string[][] = []
  const { m } = await lessonRig($, on, undefined, undefined, { context: contexts })
  await typed($, 'короткий') // too short to be about anything
  await typed($, 'сообщение от другого плагина', { origin: { kind: 'plugin', name: 'x' } })
  await typed($, '/al-health', { command: { name: 'al-health', args: '' } })
  expect(ofCg(m, 'lessons-match')).toEqual([])
  await typed($, 'опять 429 на сборке, что делать?')
  await typed($, 'опять 429 на сборке, что делать?')
  expect(contexts[3]).toEqual([['[al-lessons]', T.lessonsIntro, '- Action: gh api in a loop | Symptom: HTTP 429 | Cause: 120 requests an hour | Trigger: gh api /repos', '[/al-lessons]'].join('\n')])
  expect(contexts[4]).toEqual([]) // shown once in this chat
  const [match, ...more] = ofCg(m, 'lessons-match')
  expect(match!.argv).toEqual(['lessons-match', '--cwd', 'C:/w', '--text=опять 429 на сборке, что делать?', '--model', 'embeddinggemma', '--min-score', '0.45', '--limit', '2', '--timeout', '3'])
  expect(match!.timeoutMs).toBe(8_000)
  expect(more).toEqual([]) // every lesson was shown already: no Python for the second
})

test('lessons: none in the project, none asked for; off in the settings, none either', { options: { lessons: false } }, async ($, on) => {
  const { m } = await lessonRig($, on)
  await typed($, 'опять 429 на сборке, что делать?')
  expect(ofCg(m, 'lessons-match')).toEqual([])
  expect(await ($ as any).tool.call({ tool: 'Bash', command: 'gh api /repos/x' })).toEqual({ result: 'ran' })
})

test('lessons: a project without lessons runs no Python on a message', async ($, on) => {
  const { m } = await lessonRig($, on, [], [])
  await typed($, 'опять 429 на сборке, что делать?')
  expect(ofCg(m, 'lessons-match')).toEqual([])
})

test('lessons: a failed search costs the owner a line in /al-health once, and the message goes on as typed', async ($, on) => {
  const contexts: string[][] = []
  on('prompt.submit', (_: unknown, e: { text: string; context?: readonly string[] }) => (contexts.push([...(e.context ?? [])]), { text: e.text }))
  const m = await started($, on, a => (a[0] === 'lessons' ? listed() : a[0] === 'lessons-match' ? failed('OperationalError: database is locked') : ran('{}')))
  await ($ as any).command.run({ command: 'al-lessons', args: '' })
  await typed($, 'опять 429 на сборке, что делать?')
  m.store.set('lastError', { at: 1, where: 'sync', msg: 'later' })
  await typed($, 'опять 429 на сборке, что делать?')
  expect(contexts).toEqual([[], []])
  expect(m.toasts).toEqual([])
  expect(m.store.get('lastError')).toMatchObject({ where: 'sync' })
})

test('lessons: a command with a trigger is stopped once with the lesson, then passes', async ($, on) => {
  const { m } = await lessonRig($, on)
  const stop = await ($ as any).tool.call({ tool: 'Bash', command: 'GH API /repos/owner/x --paginate' })
  expect(stop).toEqual({ deny: gateText() })
  expect(await ($ as any).tool.call({ tool: 'Bash', command: 'gh api /repos/owner/x --paginate' })).toEqual({ result: 'ran' })
  expect(await ($ as any).tool.call({ tool: 'Bash', command: 'git status' })).toEqual({ result: 'ran' })
  expect(m.store.get('lessonCounts')).toEqual({ stopped: 1 })
})

test('lessons: a PowerShell command is stopped once as well', async ($, on) => {
  on('tool.call', { tool: 'PowerShell' }, () => ({ result: 'ran' }))
  await lessonRig($, on)
  expect(await ($ as any).tool.call({ tool: 'PowerShell', command: 'gh api /repos/owner/x' })).toEqual({ deny: gateText() })
  expect(await ($ as any).tool.call({ tool: 'PowerShell', command: 'gh api /repos/owner/x' })).toEqual({ result: 'ran' })
})

test('lessons: after a compaction of the chat a lesson is shown and stops a command once more; a precompute, a skipped one and a subagent\'s change nothing', async ($, on) => {
  const kept = [{ role: 'user', text: 'summary', toolUses: [] }] // a compaction leaves at least one message
  let result: object = { messages: kept }
  on('session.compact', () => result)
  const contexts: string[][] = []
  await lessonRig($, on, undefined, undefined, { context: contexts })
  // [lessons beside the message, whether the command was stopped]
  const round = async () => {
    await typed($, 'опять 429 на сборке, что делать?')
    return [contexts.at(-1)!.length, 'deny' in (await ($ as any).tool.call({ tool: 'Bash', command: 'gh api /repos/x' }))]
  }
  const compact = (input: object = {}) => ($ as any).session.compact({ messages: kept, ...input })
  expect(await round()).toEqual([1, true])
  expect(await round()).toEqual([0, false])
  await compact({ trigger: 'precompute' })
  await compact({ agentId: 'sub' })
  result = { skip: 'no' }
  await compact()
  expect(await round()).toEqual([0, false])
  result = { messages: kept }
  await compact()
  expect(await round()).toEqual([1, true])
})

test('/al-lessons: the list; forget deletes by pos through cg.py; anything else, how to ask', async ($, on) => {
  const { m } = await lessonRig($, on)
  expect((await ($ as any).command.run({ command: 'al-lessons', args: '' })).text).toBe(
    'pos=1792000000000 [2026-10-20] Action: gh api in a loop | Symptom: HTTP 429 | Cause: 120 requests an hour | Trigger: gh api /repos',
  )
  expect((await ($ as any).command.run({ command: 'al-lessons', args: 'forget 1792000000000' })).text).toBe(T.cgLessonsForgotten(1))
  expect(ofCg(m, 'forget')[0]!.argv).toEqual(['forget', '--conv=lessons-abc', '--pos=1792000000000'])
  expect((await ($ as any).command.run({ command: 'al-lessons', args: 'forget 5' })).text).toBe(T.cgLessonsForgotten(0))
  expect((await ($ as any).command.run({ command: 'al-lessons', args: 'drop all' })).text).toBe(T.cgLessonsUsage)
})

test('/al-health: how many lessons, how often they came back and what that cost', async ($, on) => {
  const { m } = await lessonRig($, on)
  await typed($, 'опять 429 на сборке, что делать?')
  await ($ as any).tool.call({ tool: 'Bash', command: 'gh api /repos/x' })
  const counts = m.store.get('lessonCounts') as Record<string, number>
  const { text } = await ($ as any).command.run({ command: 'al-health', args: '' })
  expect(text).toContain(T.healthLessons(1, 1, counts.chars!, 1, true))
})

// ---- lessons for all projects ----
const rule = { id: 8, conv: 'g', pos: 1792000000001, ts: '2026-10-21T10:00:00.000Z', text: 'Action: a\nSymptom: b\nCause: c\nFix: Answer in Ukrainian', trigger: null, from: null, fix: 'Answer in Ukrainian', global: true }
const rulesText = () => ['[al-lessons]', T.rulesIntro, '- Answer in Ukrainian', '[/al-lessons]'].join('\n')
const withSize = (m: { store: Map<string, unknown> }, tokens: number) => m.store.set('turn:id', { at: 1, tokens })

test('global lessons: the first owner message carries the rules, the next one at the same size does not', async ($, on) => {
  const contexts: string[][] = []
  const { m } = await lessonRig($, on, [], [rule], { context: contexts })
  withSize(m, 50_000)
  await typed($, 'да')
  await typed($, 'да')
  expect(contexts).toEqual([[rulesText()], []])
})

test('global lessons: due again after 20k tokens, after a compaction and after a smaller size', async ($, on) => {
  const kept = [{ role: 'user', text: 'summary', toolUses: [] }]
  on('session.compact', () => ({ messages: kept }))
  const contexts: string[][] = []
  const { m } = await lessonRig($, on, [], [rule], { context: contexts })
  const got = async (tokens: number) => (withSize(m, tokens), await typed($, 'да'), contexts.at(-1)!.length)
  expect(await got(50_000)).toBe(1)
  expect(await got(69_999)).toBe(0)
  expect(await got(70_000)).toBe(1) // 20k since the last reminder
  expect(await got(80_000)).toBe(0)
  expect(await got(30_000)).toBe(1) // smaller: the chat was cut
  expect(await got(30_000)).toBe(0)
  await ($ as any).session.compact({ messages: kept })
  expect(await got(31_000)).toBe(1) // a compaction
})

test('global lessons: a new session id gets the rules', async ($, on) => {
  const contexts: string[][] = []
  const { m } = await lessonRig($, on, [], [rule], { context: contexts })
  withSize(m, 50_000)
  await typed($, 'да')
  await typed($, 'да')
  ids.now = 'other' // /clear changes the id without session.start
  m.store.set('turn:other', { at: 1, tokens: 50_000 })
  await typed($, 'да')
  expect(contexts.map(c => c.length)).toEqual([1, 0, 1])
})

test('global lessons: only globals means no Python on a message', async ($, on) => {
  const { m } = await lessonRig($, on, [], [rule])
  await typed($, 'опять 429 на сборке, что делать?')
  expect(ofCg(m, 'lessons-match')).toEqual([])
})

test('global lessons: a short message gets the rules but no lesson search', async ($, on) => {
  const contexts: string[][] = []
  const { m } = await lessonRig($, on, [{ ...lesson, score: 0.9 }], [lesson, rule], { context: contexts })
  await typed($, 'да')
  expect(contexts).toEqual([[rulesText()]])
  expect(ofCg(m, 'lessons-match')).toEqual([])
})

test('global lessons: off switch stops the rules', { options: { lessons: false } }, async ($, on) => {
  const contexts: string[][] = []
  await lessonRig($, on, [], [rule], { context: contexts })
  await typed($, 'да')
  expect(contexts).toEqual([[]])
})

test('global lessons: the trigger stops a command in any project, with the global text', async ($, on) => {
  await lessonRig($, on, [], [{ ...rule, trigger: 'gh api /repos' }])
  const stop = await ($ as any).tool.call({ tool: 'Bash', command: 'gh api /repos/x' })
  expect(stop).toEqual({ deny: ['[al-lessons]', T.lessonGateGlobal('Action: a | Symptom: b | Cause: c | Fix: Answer in Ukrainian'), '[/al-lessons]'].join('\n') })
  expect(await ($ as any).tool.call({ tool: 'Bash', command: 'gh api /repos/x' })).toEqual({ result: 'ran' })
})

test('lesson_save scope all: the answer, the toast and the args', async ($, on) => {
  const { m } = await lessonRig($, on, [], [rule])
  const r = await ($ as any).tool.call({ tool: 'mcp__alethite__lesson_save', action: 'x', symptom: 'y', cause: 'z', fix: 'f', scope: 'all' })
  expect(ofCg(m, 'lesson-save')[0]!.argv).toEqual(['lesson-save', '--cwd', 'C:/w', '--model', 'embeddinggemma', '--action=x', '--symptom=y', '--cause=z', '--fix=f', '--scope=all'])
  expect(r).toEqual({ result: T.lessonSavedGlobal('Action: x\nSymptom: y\nCause: z') })
  expect(m.toasts).toEqual([T.toast(T.lessonToastGlobal('Action: x | Symptom: y | Cause: z', 0))])
})

test('/al-health shows the rules line', async ($, on) => {
  const { m } = await lessonRig($, on, [], [rule])
  await typed($, 'да')
  const counts = m.store.get('lessonCounts') as Record<string, number>
  expect(counts.rules).toBe(1)
  expect(counts.rulesChars).toBeGreaterThan(0)
  const { text } = await ($ as any).command.run({ command: 'al-health', args: '' })
  expect(text).toContain(T.healthRules(1, 1, counts.rulesChars!, 0))
  expect(text).toContain(T.healthLessons(0, 0, 0, 0, true))
})

// ---- the background job (stage 4) ----
const lastRun = { at: '2026-10-20T10:00:00Z', index: { rows: 3, errors: 0 }, embed: { skipped: 'battery' }, sync: { pushed: 2, pulled: 5, errors: 1 }, errors: [{ where: 'sync', error: 'batch 4 is broken' }] }

test('/al-job: on and off go to cg.py with the minutes; the state shows the last run; anything else, how to ask', async ($, on) => {
  const m = await started($, on, a =>
    a[0] !== 'job' ? ran('{}') : a[1] === 'install' ? ran(JSON.stringify({ installed: true, minutes: Number(a[3]) })) : a[1] === 'remove' ? ran('{"installed":false}') : ran(JSON.stringify({ supported: true, minutes: 30, last: lastRun })),
  )
  const run = async (args: string) => (await ($ as any).command.run({ command: 'al-job', args })).text
  expect(await run('on')).toBe(T.cgJobOn(30))
  expect(await run('on 15')).toBe(T.cgJobOn(15))
  expect(await run('off')).toBe(T.cgJobOff)
  expect((await run('')).split('\n')).toEqual([
    T.cgJobState(30),
    T.jobLast(new Date(lastRun.at).toLocaleString(T.locale), [T.jobIndex(3), T.jobOnBattery, T.jobSync(2, 5)].join(', '), 1),
    T.jobError('sync', 'batch 4 is broken'),
  ])
  expect(await run('on soon')).toBe(T.cgJobUsage)
  expect(ofCg(m, 'job').map(r => r.argv)).toEqual([['job', 'install', '--minutes', '30'], ['job', 'install', '--minutes', '15'], ['job', 'remove'], ['job', 'status']])
})

test('/al-job: not on this system, said plainly; a failure is a toast', async ($, on) => {
  const m = await started($, on, a => (a[0] !== 'job' ? ran('{}') : a[1] === 'status' ? ran(JSON.stringify({ supported: false, minutes: null, last: null })) : failed('ValueError: the background job is for Windows for now')))
  expect((await ($ as any).command.run({ command: 'al-job', args: '' })).text).toBe([T.cgJobState(null), T.jobNever].join('\n'))
  expect((await ($ as any).command.run({ command: 'al-job', args: 'on' })).text).toBe(T.cgJobFailed)
  expect(m.toasts).toEqual([T.toast(T.failed('al-job', 'ValueError: the background job is for Windows for now'))])
})

test('/al-health: the background job\'s last run', async ($, on) => {
  wire(on, [], () => ({ value: { context: { window: 1_000_000 }, rateLimits: [] } }), (_, e) =>
    e.argv.includes('status') ? ran(JSON.stringify({ db: 'd', convs: 0, rows: 0, size_mb: 0, job: lastRun })) : noTurn(),
  )
  on('session.root', () => ({ value: 'C:/w' }))
  const { text } = await ($ as any).command.run({ command: 'al-health', args: '' })
  expect(text).toContain(T.jobError('sync', 'batch 4 is broken'))
})

test('lessons: a message with no origin (an engine older than the origin) is the owner\'s', async ($, on) => {
  const contexts: string[][] = []
  await lessonRig($, on, undefined, undefined, { context: contexts })
  await ($ as any).prompt.submit({ text: 'опять 429 на сборке, что делать?', wait: false })
  expect(contexts[0]!.length).toBe(1)
})

// the desktop Code tab is an SDK host: the owner's typing comes as sdk, or as unclassified from a channel the engine cannot attest
for (const kind of ['sdk', 'unclassified']) {
  test(`lessons: a message of origin ${kind} is the owner's, a task notification is not`, async ($, on) => {
    const contexts: string[][] = []
    const { m } = await lessonRig($, on, undefined, undefined, { context: contexts })
    await typed($, 'опять 429 на сборке, что делать?', { origin: { kind: 'task-notification' } })
    expect(ofCg(m, 'lessons-match')).toEqual([])
    await typed($, 'опять 429 на сборке, что делать?', { origin: { kind } })
    expect(contexts.map(c => c.length)).toEqual([0, 1])
  })
}

test('lessons: with three that fit the third comes with the next message, and once all were shown no Python runs', async ($, on) => {
  const three = [7, 8, 9].map(id => ({ ...lesson, id, pos: id, text: `Action: a${id}` }))
  const contexts: string[][] = []
  on('prompt.submit', (_: unknown, e: { text: string; context?: readonly string[] }) => (contexts.push([...(e.context ?? [])]), { text: e.text, context: e.context }))
  // cg.py gives the best `--limit` of the three
  const m = await started($, on, a =>
    a[0] === 'lessons' ? listed(three) : a[0] === 'lessons-match' ? ran(JSON.stringify({ lessons: three.slice(0, Number(a[a.indexOf('--limit') + 1])).map(l => ({ ...l, score: 0.8 })), vector: 'ok' })) : ran('{}'),
  )
  await ($ as any).command.run({ command: 'al-lessons', args: '' })
  for (let i = 0; i < 3; i++) await typed($, 'опять 429 на сборке, что делать?')
  expect(contexts.map(c => c.length)).toEqual([1, 1, 0])
  expect([/a7.*a8/s.test(contexts[0]![0]!), contexts[1]![0]!.includes('a9'), contexts[1]![0]!.includes('a7')]).toEqual([true, true, false])
  expect(ofCg(m, 'lessons-match').map(r => r.argv[r.argv.indexOf('--limit') + 1])).toEqual(['2', '4']) // the shown ones are asked for too; the third message asks nothing
})

test('lessons: a chat open for long learns of a lesson saved elsewhere, every 10 minutes', async ($, on) => {
  const contexts: string[][] = []
  const list: object[] = []
  const { m } = await lessonRig($, on, undefined, list, { context: contexts })
  await m.clock.advance(3_000) // the catch-up reads the lessons once more
  await typed($, 'опять 429 на сборке, что делать?')
  list.push(lesson) // another chat saved it
  await m.clock.advance(600_000)
  await typed($, 'опять 429 на сборке, что делать?')
  expect(contexts.map(c => c.length)).toEqual([0, 1])
})

test('lesson_save: a refusal is told as a refused save, not as a failed search', async ($, on) => {
  const m = await started($, on, a => (a[0] === 'lesson-save' ? failed('ValueError: the lesson needs: cause') : ran('{}')))
  const r = await ($ as any).tool.call({ tool: 'mcp__alethite__lesson_save', action: 'a', symptom: 's' })
  expect(r).toEqual({ deny: T.lessonSaveFailed('ValueError: the lesson needs: cause') })
  expect(m.toasts).toEqual([T.toast(T.failed('lesson_save', 'ValueError: the lesson needs: cause'))])
})

// ---- encryption (stage 5) ----
const encrypted = { on: true, db: 'sqlcipher', key: 'ok', notes: [] }

test('/al-encrypt: on, join and off point to the window first, wait as long as the host allows and tell how it ended; the folder and both ciphers are the settings', { options: { syncDir: SHARE, shareCipher: 'cryptography', indexCipher: 'efs' } }, async ($, on) => {
  const seen: number[] = [] // the toasts there were when cg.py was started
  const m = await started($, on, a => (a[0] === 'encrypt' ? (seen.push(m.toasts.length), ran(JSON.stringify({ action: a[1], done: a[1] !== 'join', notes: a[1] === 'join' ? ['n1'] : [] }))) : ran('{}')))
  const run = async (args: string) => (await ($ as any).command.run({ command: 'al-encrypt', args })).text
  expect(await run('on')).toBe(T.encryptDone('on'))
  expect(await run('join')).toBe([T.encryptUnfinished, 'n1'].join('\n'))
  expect(await run('off')).toBe(T.encryptDone('off'))
  expect(ofCg(m, 'encrypt')[0]).toEqual({ argv: ['encrypt', 'on', `--sync-dir=${SHARE}`, '--share-cipher=cryptography', '--index-cipher=efs', '--model', 'embeddinggemma'], timeoutMs: 600_000 })
  expect(ofCg(m, 'encrypt').map(r => [r.argv[1], r.timeoutMs])).toEqual([['on', 600_000], ['join', 600_000], ['off', 600_000]])
  expect(seen).toEqual([1, 2, 3])
  expect(m.toasts).toEqual(Array(3).fill(T.toast(T.encryptWindow)))
})

test('/al-encrypt: the state asks no window and shows the health lines; a word it does not know says how to ask and runs nothing', async ($, on) => {
  const m = await started($, on, a => (a[0] === 'encrypt' ? ran(JSON.stringify({ ...encrypted, on: false, db: 'plain', key: 'missing', notes: ['n1'] })) : ran('{}')))
  const run = async (args: string) => (await ($ as any).command.run({ command: 'al-encrypt', args })).text
  expect(await run('')).toBe([T.healthEncryptionOff, 'n1'].join('\n'))
  expect(await run('status')).toBe([T.healthEncryptionOff, 'n1'].join('\n'))
  expect(await run('join now')).toBe(T.cgEncryptUsage)
  expect(ofCg(m, 'encrypt')).toEqual([{ argv: ['encrypt', 'status'], timeoutMs: 120_000 }, { argv: ['encrypt', 'status'], timeoutMs: 120_000 }])
  expect(m.toasts).toEqual([])
})

test('/al-encrypt: cg.py fails: a toast with its words, a short reply, the error kept for /al-health', async ($, on) => {
  const m = await started($, on, a => (a[0] === 'encrypt' ? failed('ValueError: the recovery code is not right') : ran('{}')))
  expect((await ($ as any).command.run({ command: 'al-encrypt', args: 'join' })).text).toBe(T.cgEncryptFailed)
  expect(m.toasts).toEqual([T.toast(T.encryptWindow), T.toast(T.failed('al-encrypt', 'ValueError: the recovery code is not right'))])
  expect(m.store.get('lastError')).toMatchObject({ where: 'al-encrypt', msg: 'ValueError: the recovery code is not right' })
})

test('/al-encrypt: a run the host stopped at its time limit says so and what to do, not that Python did not start; a start failure still says the latter', async ($, on) => {
  let why = 'alethite: $.process.run(python) aborted: still running after 600000ms' // as the engine words it
  const m = await started($, on, a => (a[0] === 'encrypt' ? { deny: why } : ran('{}')))
  const run = async (args: string) => (await ($ as any).command.run({ command: 'al-encrypt', args })).text
  expect(await run('join')).toBe(`al-encrypt join: ${T.encryptTimeout('join')}`)
  expect(m.toasts).toEqual([T.toast(T.encryptWindow), T.toast(T.failed('al-encrypt', T.encryptTimeout('join')))])
  expect(m.toasts[1]).not.toContain('al-encrypt — al-encrypt') // the name once at the head of the toast, not twice
  expect(m.store.get('lastError')).toMatchObject({ where: 'al-encrypt', msg: T.encryptTimeout('join') })
  why = 'failed to start: ENOENT'
  expect(await run('on')).toBe(T.cgEncryptFailed)
  expect(m.toasts.at(-1)).toContain(T.pythonMissing('python'))
})

test('/al-health: the encryption line, from what cg.py status reports', async ($, on) => {
  wire(on, [], () => ({ value: { context: { window: 1_000_000 }, rateLimits: [] } }), (_, e) =>
    e.argv.includes('status') ? ran(JSON.stringify({ db: 'd', convs: 0, rows: 0, size_mb: 0, encryption: { ...encrypted, notes: ['n1'] } })) : noTurn(),
  )
  on('session.root', () => ({ value: 'C:/w' }))
  const lines = (await ($ as any).command.run({ command: 'al-health', args: '' })).text.split('\n')
  expect(lines[lines.indexOf(T.healthEncryptionOn('SQLCipher', 'SQLCipher')) + 1]).toBe('n1')
})

// ---- the index folder (/al-index-dir) ----
const moved = { action: 'index-dir', done: true, dir: 'D:/cg', notes: ['the note'] }

test('/al-index-dir: with no path the window opens, with a path it is given; both wait as long as the host allows and tell how it ended', async ($, on) => {
  const seen: number[] = [] // the toasts there were when cg.py was started
  const m = await started($, on, a => (a[0] === 'index-dir' ? (seen.push(m.toasts.length), ran(JSON.stringify(a.length === 1 ? { ...moved, done: false, dir: '' } : moved))) : ran('{}')))
  const run = async (args: string) => (await ($ as any).command.run({ command: 'al-index-dir', args })).text
  expect(await run('')).toBe([T.indexDirUnfinished, 'The note'].join('\n'))
  expect(await run(' "D:/cg" ')).toBe([T.indexDirDone('D:/cg'), 'The note'].join('\n'))
  expect(ofCg(m, 'index-dir')).toEqual([{ argv: ['index-dir'], timeoutMs: 600_000 }, { argv: ['index-dir', '--path=D:/cg'], timeoutMs: 600_000 }])
  expect(seen).toEqual([1, 1]) // the window is announced for the run without a path only
  expect(m.toasts).toEqual([T.toast(T.indexDirWindow)])
})

test('/al-index-dir: a refusal of cg.py is in the reply with its reason, in a toast and kept for /al-health', async ($, on) => {
  const m = await started($, on, a => (a[0] === 'index-dir' ? failed('ValueError: D:/cg is not on a local disk. Nothing was changed') : ran('{}')))
  expect((await ($ as any).command.run({ command: 'al-index-dir', args: 'D:/cg' })).text).toBe('al-index-dir: D:/cg is not on a local disk. Nothing was changed')
  expect(m.toasts).toEqual([T.toast(T.failed('al-index-dir', 'ValueError: D:/cg is not on a local disk. Nothing was changed'))])
  expect(m.store.get('lastError')).toMatchObject({ where: 'al-index-dir', msg: 'ValueError: D:/cg is not on a local disk. Nothing was changed' })
})

test('/al-index-dir: a run the host stopped at its time limit says so and what to do; a start failure says Python did not start', async ($, on) => {
  let why = 'alethite: $.process.run(python) aborted: still running after 600000ms' // as the engine words it
  const m = await started($, on, a => (a[0] === 'index-dir' ? { deny: why } : ran('{}')))
  const run = async () => (await ($ as any).command.run({ command: 'al-index-dir', args: '' })).text
  expect(await run()).toBe(`al-index-dir: ${T.indexDirTimeout}`)
  expect(m.toasts).toEqual([T.toast(T.indexDirWindow), T.toast(T.failed('al-index-dir', T.indexDirTimeout))])
  expect(m.store.get('lastError')).toMatchObject({ where: 'al-index-dir', msg: T.indexDirTimeout })
  why = 'failed to start: ENOENT'
  expect(await run()).toContain(T.pythonMissing('python'))
})

test('index notice: any answer of cg.py that carries one gives a toast when it differs from the last one shown, and an answer with none forgets it', async ($, on) => {
  let notice: string | undefined = 'the index of the test is not in D:/one'
  // every answer carries the notice, status and savings too: the band's background calls (refreshInfo, after /al-index) raise index-notice toasts like any other call
  const m = await started($, on, a => (ran(JSON.stringify({ ...(a[0] === 'index' && { files: 0, rows: 0, convs: 0, seconds: 0, errors: [] }), index_notice: notice }))))
  const index = () => ($ as any).command.run({ command: 'al-index', args: '' })
  await index()
  await index()
  expect(m.toasts).toEqual([T.toast(notice!)])
  const first = notice!
  notice = 'a merge of the test happened in D:/two'
  await index()
  await index()
  expect(m.toasts).toEqual([T.toast(first), T.toast(notice)])
  notice = undefined // the folder is back and nothing is said: a second outage with the very first text is news again
  await index()
  notice = first
  await index()
  expect(m.toasts).toEqual([T.toast(first), T.toast('a merge of the test happened in D:/two'), T.toast(first)])
})

test('index problem: an answer with one puts a warning on the status line, every owner prompt toasts it and asks again, an answer without it clears both', async ($, on) => {
  let problem = 'the index folder D:/one is not available and C: has only 1 GB free'
  on('prompt.submit', (_: unknown, e: { text: string }) => ({ text: e.text }))
  const statuses: (string | undefined)[] = []
  const says = (body: object) => ran(JSON.stringify({ ...body, ...(problem ? { index_problem: problem } : {}) })) // every answer of cg.py carries it while it lasts
  const m = await started($, on, () => says({}), statuses, undefined, {}, () => says({ tokens: 1, at: null, model: 'm' }))
  const lastTurns = () => ofCg(m, 'last-turn').length
  expect(statuses.at(-1)).toBe(`${T.ctx('512k', T.cacheUnknown)} · ${T.indexProblemStatus}`) // the start asked last-turn: the warning stands beside the token line
  const asked = lastTurns()
  await typed($, 'сообщение от другого плагина', { origin: { kind: 'plugin', name: 'x' } }) // not the owner's
  await typed($, 'a meta prompt', { isMeta: true })
  expect([lastTurns() - asked, m.toasts]).toEqual([0, []])
  await typed($, 'a message of the owner')
  await typed($, 'a message of the owner')
  expect(lastTurns() - asked).toBe(2) // asked on each prompt, with the 8 s a prompt can spare
  expect(ofCg(m, 'last-turn').at(-1)!.timeoutMs).toBe(8_000)
  expect(m.toasts).toEqual(Array(2).fill(T.toast(problem))) // and said again each time
  problem = ''
  await typed($, 'a message of the owner')
  expect(m.toasts).toHaveLength(2)
  expect(statuses.at(-1)).toBe(T.ctx('512k', T.cacheUnknown)) // the warning goes with the problem
  await typed($, 'a message of the owner')
  expect(lastTurns() - asked).toBe(3) // a healthy chat pays nothing
  expect(m.toasts).toHaveLength(2)
})

test('index problem: an error answer carries it too, and a check that fails keeps it without holding the prompt', async ($, on) => {
  const problem = 'the settings cannot be read'
  on('prompt.submit', (_: unknown, e: { text: string }) => ({ text: e.text }))
  let lastTurn: () => unknown = noTurn
  let isBroken = false
  const statuses: (string | undefined)[] = []
  const refused = { value: { exitCode: 1, stdout: JSON.stringify({ error: `ValueError: ${problem}`, index_problem: problem }), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  const m = await started($, on, a => (a[0] === 'index' ? refused : ran('{}')), statuses, () => (isBroken ? { deny: 'boom' } : { value: usage }), {}, () => lastTurn())
  expect(statuses.some(t => t?.includes(T.indexProblemStatus))).toBe(false)
  await ($ as any).command.run({ command: 'al-index', args: '' })
  expect(statuses.at(-1)).toBe(`${T.ctx('512k', T.cacheUnknown)} · ${T.indexProblemStatus}`) // the error answer set it
  isBroken = true // a failed refresh puts its error on the status line, and the warning stays beside it
  await ($ as any).command.run({ command: 'al-band', args: '' })
  expect(statuses.at(-1)!.startsWith(T.statusError(''))).toBe(true)
  expect(statuses.at(-1)!.endsWith(` · ${T.indexProblemStatus}`)).toBe(true)
  lastTurn = () => ({ deny: 'boom' }) // the check itself fails
  m.toasts.length = 0
  await typed($, 'a message of the owner')
  expect(m.toasts).toEqual([T.toast(problem)])
})

test('/al-health: the notes of the index folder follow the index line', async ($, on) => {
  wire(on, [], () => ({ value: { context: { window: 1_000_000 }, rateLimits: [] } }), (_, e) =>
    e.argv.includes('status') ? ran(JSON.stringify({ db: 'd', convs: 0, rows: 0, size_mb: 0, index_dir: { configured: 'D:/cg', fallback: true, notes: ['n1'] } })) : noTurn(),
  )
  on('session.root', () => ({ value: 'C:/w' }))
  const lines = (await ($ as any).command.run({ command: 'al-health', args: '' })).text.split('\n')
  expect(lines[lines.findIndex(l => l.startsWith('Index:')) + 1]).toBe('n1')
})

test('home_notes: each new note of cg.py gives one toast, and /al-health tells all of them right after the index', async ($, on) => {
  let notes: string[] = ['the folder of the test could not be moved: busy']
  const m = await started($, on, a => (a[0] === 'status' ? ran(JSON.stringify({ db: 'd', convs: 0, rows: 0, size_mb: 0, home_notes: notes })) : a[0] === 'index' ? ran(JSON.stringify({ files: 0, rows: 0, convs: 0, seconds: 0, errors: [], home_notes: notes })) : a[0] === 'embed' ? embedded() : ran('{}')))
  const first = notes[0]!
  const run = (command: string) => ($ as any).command.run({ command, args: '' })
  await run('al-index')
  await run('al-index')
  await m.clock.advance(30_000) // the timer asks cg.py again: no new toast
  expect(m.toasts).toEqual([T.toast(first)])
  notes = [first, 'the old folder of the test can be deleted']
  await run('al-index')
  expect(m.toasts).toEqual([T.toast(first), T.toast(notes[1]!)])
  const lines = (await run('al-health')).text.split('\n')
  const at = lines.findIndex(l => l.startsWith('Index:'))
  expect(lines.slice(at + 1, at + 3)).toEqual(notes)
  expect(m.toasts).toHaveLength(2)
})

test('home_notes: /al-health tells the notes of its own status answer, a note that is gone is not told again', async ($, on) => {
  let notes: string[] = ['the folder of the test could not be moved: busy']
  const m = await started($, on, a => (a[0] === 'status' ? ran(JSON.stringify({ db: 'd', convs: 0, rows: 0, size_mb: 0, home_notes: notes })) : ran('{}')))
  const health = async () => (await ($ as any).command.run({ command: 'al-health', args: '' })).text.split('\n')
  expect(await health()).toContain(notes[0])
  notes = []
  expect(await health()).not.toContain('the folder of the test could not be moved: busy')
  expect(m.toasts).toEqual([T.toast('the folder of the test could not be moved: busy')]) // told once, not again
})

test('home_notes: a note beside an error answer is told as well', async ($, on) => {
  const refused = { value: { exitCode: 1, stdout: JSON.stringify({ error: 'boom', home_notes: ['a note beside an error'] }), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  const m = await started($, on, a => (a[0] === 'index' ? refused : ran('{}')))
  await ($ as any).command.run({ command: 'al-index', args: '' })
  expect(m.toasts).toEqual([T.toast('a note beside an error'), T.toast(T.failed('al-index', 'boom'))])
})

const savingsJson = ran(JSON.stringify({ last: null, worker: { usd: 0, links: 0, calls: 0 }, project: null }))

test('band data: status and savings are asked at the start and after /al-sync', { options: { syncDir: SHARE } }, async ($, on) => {
  const m = await started($, on, a => (a[0] === 'savings' ? savingsJson : a[0] === 'sync' ? synced() : a[0] === 'embed' ? embedded() : a[0] === 'index' ? indexed() : ran('{}')))
  await m.clock.advance(3_000)
  const asked = () => [ofCg(m, 'status').length, ofCg(m, 'savings').length]
  expect(ofCg(m, 'savings')[0]!.argv).toEqual(['savings', '--session', 'id', '--cwd', 'C:/w'])
  expect(ofCg(m, 'status')[0]!.argv).toEqual(['status', '--cwd=C:/w'])
  const [s0, v0] = asked()
  await ($ as any).command.run({ command: 'al-sync', args: '' })
  await m.clock.advance(0) // the refresh is not awaited by the reply
  expect(asked()).toEqual([s0 + 1, v0 + 1])
})

test('band data: a failed savings is noted once, status still arrives', async ($, on) => {
  const m = await started($, on, a => (a[0] === 'savings' ? failed('boom') : a[0] === 'embed' ? embedded() : a[0] === 'index' ? indexed() : ran('{}')))
  await m.clock.advance(3_000)
  expect(m.store.get('lastError')).toMatchObject({ where: 'band', msg: 'boom' })
  m.store.delete('lastError')
  await m.clock.advance(600_000)
  expect(ofCg(m, 'savings').length).toBeGreaterThanOrEqual(2)
  expect(ofCg(m, 'status').length).toBeGreaterThanOrEqual(2)
  expect(m.store.get('lastError')).toBeUndefined() // noted once per load
  expect(m.toasts).toEqual([])
})

test("band data: an older lastError in the store does not hide this load's band failure", async ($, on) => {
  const store = new Map<string, unknown>([['lastError', { at: 5, where: 'sync', msg: 'last week' }]])
  const m = await started($, on, a => (a[0] === 'savings' ? failed('boom') : a[0] === 'embed' ? embedded() : a[0] === 'index' ? indexed() : ran('{}')), [], undefined, {}, noTurn, store)
  await m.clock.advance(3_000)
  expect(store.get('lastError')).toMatchObject({ where: 'band', msg: 'boom' })
})

test('band data: both calls failing at once note only the first error', async ($, on) => {
  const m = await started($, on, a => (a[0] === 'savings' ? failed('savings down') : a[0] === 'status' ? failed('status down') : a[0] === 'embed' ? embedded() : a[0] === 'index' ? indexed() : ran('{}')))
  await m.clock.advance(3_000)
  expect(m.store.get('lastError')).toMatchObject({ where: 'band' })
  const first = m.store.get('lastError')
  await m.clock.advance(600_000)
  expect(m.store.get('lastError')).toEqual(first)
})

const worker = { last: null, worker: { usd: 18.7, links: 7, calls: 400 }, project: null }
const bandData = async ($: any, on: any, savings: unknown, status: object = { convs: 12, size_mb: 1.5 }, statusAnswer: unknown = ran(JSON.stringify(status))) => {
  const m = await started($, on, a => (a[0] === 'savings' ? savings : a[0] === 'status' ? statusAnswer : a[0] === 'embed' ? embedded() : a[0] === 'index' ? indexed() : ran('{}')))
  await m.clock.advance(3_000)
  return m
}

// cells the collapsed row takes in the terminal: every Text, every Button as `[ label ]`, one gap between neighbours and one for the spacer
const rowWidth = async (ui: any) => {
  const t: string[] = (await texts(ui)).filter((x: string) => x !== 'no band')
  const b: { text: string }[] = await ui.findAll({ type: 'Button' })
  return t.reduce((n, x) => n + x.length + (x.includes('⏰') ? 1 : 0), 0) + b.reduce((n, x) => n + x.text.length + 4, 0) + t.length + b.length
}
const atColumns = ($: any, bodyColumns: number) => $.ui.mount({ ...(UI as object), props: { ...props, bodyColumns } } as never)

test('narrow terminal keeps one row: the savings drop out first, the row fits', async ($, on) => {
  await bandData($, on, ran(JSON.stringify(worker)))
  const wide = await texts(await atColumns($, 120))
  expect(wide.some((t: string) => t.includes(T.saved('$18.70')))).toBe(true)
  for (const cols of [120, 80, 60]) expect(await rowWidth(await atColumns($, cols))).toBeLessThanOrEqual(cols)
  const narrow = await texts(await atColumns($, 60))
  expect(narrow.some((t: string) => t.includes(T.saved('')))).toBe(false)
  expect(narrow[0]).toBe(T.bandTitle)
})

const STALE = new Date(10_000_000_000 - 2 * 3_600_000).toISOString()
test('a stale row with Stay here fits 60 columns', async ($, on) => {
  wire(on, [], () => ({ value: usage }), () => ran(JSON.stringify({ tokens: 300_000, at: STALE, model: 'm' })))
  await $.command.run({ command: 'al-band', args: '' })
  const ui = await atColumns($, 60)
  expect(await buttons(ui)).toEqual(['launch', 'stay', 'details'])
  expect(await rowWidth(ui)).toBeLessThanOrEqual(60)
})

test('the same in Russian: the name shortens to the shield when the buttons leave no room', { options: { language: 'ru' } }, async ($, on) => {
  wire(on, [], () => ({ value: usage }), () => ran(JSON.stringify({ tokens: 300_000, at: STALE, model: 'm' })))
  await $.command.run({ command: 'al-band', args: '' })
  const ui = await atColumns($, 60)
  expect(await buttons(ui)).toEqual(['launch', 'stay', 'details'])
  expect(await rowWidth(ui)).toBeLessThanOrEqual(60)
  expect((await texts(ui))[0]).toBe('🛡')
})

// cg.py exits 1 with {error, index_problem} for an index it cannot use: status then never answers ok
const refusedStatus = ran('{}')
refusedStatus.value.exitCode = 1
refusedStatus.value.stdout = JSON.stringify({ error: 'ValueError: broken', index_problem: 'broken' })

test('a base alarm stays on the row however narrow; the price goes before it', async ($, on) => {
  await bandData($, on, ran(JSON.stringify(worker)), undefined, refusedStatus)
  const row = async (bodyColumns: number) => (await texts(await atColumns($, bodyColumns))).join(' ')
  expect(await row(120)).toContain(T.baseTrouble)
  expect(await row(60)).toContain(T.baseTrouble)
  expect(await row(60)).not.toContain(T.saved(''))
  const tight = await row(45)
  expect(tight).toContain(T.baseTrouble)
  expect(tight).not.toContain(T.sendHere(''))
  expect(await rowWidth(await atColumns($, 45))).toBeLessThanOrEqual(45)
})

test('an index that cannot be used: the Base column says ? and the alarm is on', async ($, on) => {
  await bandData($, on, ran(JSON.stringify(worker)), undefined, refusedStatus)
  const ui = await $.ui.mount(UI)
  await ui.press({ key: 'details' })
  const all = await texts(ui)
  expect(all).toContain('?')
  expect(all).not.toContain(T.baseConvs(12))
})

test('savings that cost more are drawn as an error', async ($, on) => {
  await bandData($, on, ran(JSON.stringify({ ...worker, worker: { usd: -2, links: 3, calls: 9 } })))
  const dear = (await (await $.ui.mount({ ...(UI as object), props: { ...props, bodyColumns: 140 } } as never)).findAll({ type: 'Text' })).filter((t: { props: { color?: string } }) => t.props.color === 'error')
  expect(dear.map((t: { text: string }) => t.text).join()).toContain(T.dearer('$2.00'))
})

test('Details shows what the chat has spent when the engine says', async ($, on) => {
  wire(on, [], () => ({ value: { ...usage, cost: { usd: 3.2 } } }))
  const ui = await mountBand($)
  await ui.press({ key: 'details' })
  expect(await texts(ui)).toContain(T.spent('$3.20'))
})

test('the Base column is filled while savings failed', async ($, on) => {
  await bandData($, on, failed('boom'))
  const ui = await $.ui.mount(UI)
  await ui.press({ key: 'details' })
  const all = await texts(ui)
  expect(all).toContain(T.baseConvs(12))
  expect(all).toContain('?') // the handoff columns say they do not know
})

// the delayed New worker: a limit at 92% that resets in an hour, so the wait ends 5 minutes after the reset
const NOW = 10_000_000_000
const AT = NOW + 3_600_000 + 300_000
const nearLimit = (resetsAt: string | undefined = new Date(NOW + 3_600_000).toISOString(), percentUsed = 92) => () => ({ value: { ...usage, rateLimits: [{ kind: 'five_hour', percentUsed, resetsAt }] } })
const waitLabel = T.waitingAt(when(new Date(AT).toISOString(), NOW))

// a chat that started, with a draft in the box; its handoff notes whether the wait was already gone
const delayed = async ($: any, on: any, draft = 'продолжаем', store = new Map<string, unknown>(), limit = nearLimit()) => {
  const log: string[] = []
  const gone: boolean[] = []
  const state = box(on, draft, log)
  const m = await started($, on, a => (a[0] === 'handoff' ? (gone.push(!store.has('delayed:id')), ran(JSON.stringify({ text: 'h', path: 'C:/h/id.md' }))) : ran('{}')), [], limit, {}, noTurn, store)
  return { m, log, gone, state, ui: await $.ui.mount(UI) }
}

test('after-reset is offered only near a limit', async ($, on) => {
  wire(on, [], nearLimit())
  const ui = await mountBand($)
  await ui.press({ key: 'launch' })
  expect(await buttons(ui)).toEqual(['send', 'pick', 'after', 'cancel'])
  expect(await ui.findAll({ type: 'Button', text: T.afterReset(when(new Date(AT).toISOString(), NOW)) })).toHaveLength(1)
})

for (const [name, limit] of [
  ['under 90%', nearLimit(undefined, 50)],
  ['without a reset time', () => ({ value: { ...usage, rateLimits: [{ kind: 'five_hour', percentUsed: 92 }] } })],
] as const)
  test(`after-reset is not offered ${name}`, async ($, on) => {
    wire(on, [], limit)
    const ui = await mountBand($)
    await ui.press({ key: 'launch' })
    expect(await buttons(ui)).toEqual(['send', 'pick', 'cancel'])
  })

test('after-reset needs a draft and waits', async ($, on) => {
  const { m, state, ui } = await delayed($, on, '')
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'after' })
  expect(m.toasts).toEqual([T.toast(T.draftEmpty)])
  expect(m.store.has('delayed:id')).toBe(false)

  state.text = 'продолжаем'
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'after' })
  expect(m.store.get('delayed:id')).toEqual({ at: AT })
  expect(await texts(ui)).toContain(waitLabel)
  expect(await buttons(ui)).toEqual(['cancel-delay', 'details'])
})

// a wait that comes due within a minute: the same paths as the hour-long one in 2-3 ticks of 30 s
const SOON = NOW + 60_000
const soonLabel = T.waitingAt(when(new Date(SOON).toISOString(), NOW))
const dueSoon = () => new Map<string, unknown>([['delayed:id', { at: SOON }]])

test('a wait fires once when due: the key goes before the launch', async ($, on) => {
  const { m, log, gone } = await delayed($, on, 'продолжаем', dueSoon())
  await m.clock.advance(SOON - NOW - 30_000)
  expect(log).toEqual([])
  await m.clock.advance(30_000)
  expect(log).toEqual(['/clear', 'submit h' + String.fromCharCode(10, 10) + 'продолжаем'])
  expect(gone).toEqual([true])
  await m.clock.advance(120_000)
  expect(log).toHaveLength(2)
})

test('cancel-delay removes the wait', async ($, on) => {
  const { m, log, ui } = await delayed($, on, 'продолжаем', dueSoon())
  await ui.press({ key: 'cancel-delay' })
  expect(m.store.has('delayed:id')).toBe(false)
  expect(await buttons(ui)).toEqual(['launch', 'details'])
  await m.clock.advance(120_000)
  expect(log).toEqual([])
})

test("another chat's wait is left alone", async ($, on) => {
  const { m, log } = await delayed($, on, 'x', new Map([['delayed:other', { at: 1 }]]))
  await m.clock.advance(60_000)
  expect(log).toEqual([])
  expect(m.store.get('delayed:other')).toEqual({ at: 1 })
})

test('a wait survives a restart', async ($, on) => {
  const { m, log, ui } = await delayed($, on, 'продолжаем', dueSoon())
  expect(await texts(ui)).toContain(soonLabel)
  expect(await buttons(ui)).toEqual(['cancel-delay', 'details'])
  await m.clock.advance(SOON - NOW)
  expect(log[0]).toBe('/clear')
})

test('an emptied box at fire time cancels with a toast', async ($, on) => {
  const { m, log } = await delayed($, on, '', dueSoon())
  await m.clock.advance(SOON - NOW)
  expect(log).toEqual([])
  expect(m.toasts.filter(t => t === T.toast(T.draftEmpty))).toHaveLength(1)
  expect(m.store.has('delayed:id')).toBe(false)
  await m.clock.advance(120_000)
  expect(m.toasts.filter(t => t === T.toast(T.draftEmpty))).toHaveLength(1) // the tick does not say it again
})

test('a wait that falls due during a launch stays and fires after it', async ($, on) => {
  const log: string[] = []
  let release!: () => void
  const state = box(on, 'продолжаем', log, new Promise<void>(r => (release = r)))
  const m = await started($, on, a => (a[0] === 'handoff' ? ran(JSON.stringify({ text: 'h', path: 'C:/h/id.md' })) : ran('{}')), [], nearLimit(), {}, noTurn)
  const ui = await $.ui.mount(UI)
  await ui.press({ key: 'launch' })
  void ui.press({ key: 'send' }) // held at the submit
  await m.clock.advance(1)
  m.store.set('delayed:id', { at: SOON })
  await m.clock.advance(60_000)
  expect(log.filter(l => l === '/clear')).toHaveLength(1)
  expect(m.store.get('delayed:id')).toEqual({ at: SOON }) // not dropped
  release()
  state.text = 'ещё' // the first launch emptied the box; the owner typed again
  await m.clock.advance(60_000)
  expect(log.filter(l => l === '/clear')).toHaveLength(2)
  expect(m.store.has('delayed:id')).toBe(false)
})

test('a wait that falls due mid-turn waits for the turn to end', async ($, on) => {
  const { m, log } = await delayed($, on, 'продолжаем', dueSoon())
  await $.ui.mount({ ...(UI as object), props: { ...props, isWorking: true } } as never)
  await m.clock.advance(120_000)
  expect(log).toEqual([])
  expect(m.store.get('delayed:id')).toEqual({ at: SOON })
  await $.ui.mount(UI)
  await m.clock.advance(30_000)
  expect(log[0]).toBe('/clear')
  expect(m.store.has('delayed:id')).toBe(false)
})

for (const bad of [{}, { at: 'x' }, { at: 1e16 }, 'x'])
  test(`a malformed wait (${JSON.stringify(bad)}) is no wait`, async ($, on) => {
    const { m, log, ui } = await delayed($, on, 'продолжаем', new Map([['delayed:id', bad]]))
    expect(await buttons(ui)).toEqual(['launch', 'details'])
    await m.clock.advance(120_000)
    expect(log).toEqual([])
  })

test('a throw before anything is launched puts the wait back', async ($, on) => {
  const { m, log, state } = await delayed($, on, 'продолжаем', dueSoon())
  state.broken = true // the draft cannot be read: startWorker throws before /clear
  await m.clock.advance(SOON - NOW)
  expect(log).toEqual([])
  expect(m.store.get('delayed:id')).toEqual({ at: SOON })
  state.broken = false
  await m.clock.advance(30_000)
  expect(log[0]).toBe('/clear')
  expect(m.store.has('delayed:id')).toBe(false)
})

test('a launch that keeps throwing before /clear is retried once, then the wait is dropped', async ($, on) => {
  const { m, log, state } = await delayed($, on, 'продолжаем', dueSoon())
  state.broken = true
  await m.clock.advance(SOON - NOW + 3 * 30_000)
  expect(log).toEqual([])
  expect(m.toasts.filter(t => t.includes('launch'))).toHaveLength(2)
  expect(m.store.has('delayed:id')).toBe(false)
})

test('Cancel in the gap after /clear also removes the wait of the chat the row was drawn for', async ($, on) => {
  const { m, ui } = await delayed($, on, 'продолжаем', dueSoon())
  ids.now = 'other' // /clear has run no hook: the row still shows the old wait
  await ui.press({ key: 'cancel-delay' })
  expect(m.store.has('delayed:id')).toBe(false)
})

test('a /clear while a wait is pending: the new chat shows no wait, and the old chat keeps it', async ($, on) => {
  const { m, log, ui } = await delayed($, on, 'продолжаем', dueSoon())
  expect(await buttons(ui)).toEqual(['cancel-delay', 'details'])
  ids.now = 'other'
  await m.clock.advance(30_000)
  expect(await buttons(ui)).toEqual(['launch', 'details'])
  await m.clock.advance(120_000)
  expect(log).toEqual([])
  expect(m.store.get('delayed:id')).toEqual({ at: SOON })
})

test('a /resume into a chat with a due wait shows it for a tick before it goes', async ($, on) => {
  const { m, log, ui } = await delayed($, on, 'продолжаем', new Map([['delayed:other', { at: NOW }]]))
  expect(await buttons(ui)).toEqual(['launch', 'details'])
  ids.now = 'other'
  await m.clock.advance(30_000)
  expect(log).toEqual([])
  expect(await buttons(ui)).toEqual(['cancel-delay', 'details'])
  await m.clock.advance(30_000)
  expect(log[0]).toBe('/clear')
})

test('After reset and its Cancel act on the chat that is open now, with the time on the button', async ($, on) => {
  const { m, ui } = await delayed($, on)
  await ui.press({ key: 'launch' })
  ids.now = 'other'
  await ui.press({ key: 'after' })
  expect(m.store.get('delayed:other')).toEqual({ at: AT })
  expect(m.store.has('delayed:id')).toBe(false)
  await ui.press({ key: 'cancel-delay' })
  expect(m.store.has('delayed:other')).toBe(false)
})

test('the first reply of a new chat refreshes the savings, not the launch before it', async ($, on) => {
  const log: string[] = []
  let release!: () => void
  box(on, 'продолжаем', log, new Promise<void>(r => (release = r)))
  const answer = (a: string[]) =>
    a[0] === 'handoff' ? ran(JSON.stringify({ text: 'h', path: 'C:/h/id.md' }))
    : a[0] === 'savings' ? ran(JSON.stringify(a[2] === 'new' ? worker : { last: null, worker: { usd: 0, links: 0, calls: 0 }, project: null }))
    : ran('{}')
  const m = await started($, on, answer)
  await m.clock.advance(3_000)
  const ui = await $.ui.mount({ ...(UI as object), props: { ...props, bodyColumns: 140 } } as never)
  await ui.press({ key: 'launch' })
  const pressed = ui.press({ key: 'send' })
  while (!log.some(l => l.startsWith('submit'))) await m.clock.advance(1)
  ids.now = 'new' // /clear has changed the id
  release()
  await pressed
  await m.clock.advance(0)
  expect(ofCg(m, 'savings').filter(r => r.argv[2] === 'new')).toHaveLength(0)
  expect((await texts(ui)).some((t: string) => t.includes(T.saved('$18.70')))).toBe(false)
  await ($ as any).turn.complete(turn)
  await m.clock.advance(0)
  expect(ofCg(m, 'savings').filter(r => r.argv[2] === 'new')).toHaveLength(1)
  expect((await texts(ui)).some((t: string) => t.includes(T.saved('$18.70')))).toBe(true)
  await ($ as any).turn.complete(turn)
  await m.clock.advance(0)
  expect(ofCg(m, 'savings').filter(r => r.argv[2] === 'new')).toHaveLength(1) // only the first reply
})

test('the waiting row stays on one line at 60 columns and keeps its tokens', async ($, on) => {
  await delayed($, on, 'продолжаем', dueSoon())
  const ui = await atColumns($, 60)
  expect(await texts(ui)).toContain(soonLabel)
  expect(await buttons(ui)).toContain('cancel-delay')
  expect(await rowWidth(ui)).toBeLessThanOrEqual(60)
  const wide = await texts(await atColumns($, 140))
  expect(wide).toContain(soonLabel)
  expect(wide.some((t: string) => t.includes('512k'))).toBe(true) // the numbers stay while a worker waits
})
