import { expect, mock, test } from 'claude-code/testing'
import type { CommandRunInput, SessionCompactResult, SessionMessage } from 'claude-code'
import { ru } from '../hooks/lang/ru'
import { uk } from '../hooks/lang/uk'
import { notePath, summaryPath } from '../hooks/notes'
import { when } from '../hooks/band'
import { END, START } from '../hooks/lessons'
import { bgRow } from '../hooks/bg'
import type { BgRun } from '../types'
import { price } from '../hooks/guard'
import { T } from '../hooks/text'

const usage = { context: { tokens: 512_000, window: 1_000_000 }, rateLimits: [{ kind: 'five_hour', percentUsed: 7 }] }
const turn = { answer: '', durationMs: 1, isAborted: false, turnId: 't', reason: 'answer', usage: { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, model: 'claude-opus-5-5' } }
// a person's /command as the engine stamps it: typed at the prompt (composer), on the main screen of an 80-column terminal
const typedCmd = (command: string, args = ''): CommandRunInput => ({ command, args, origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 80 } })
const props = { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 80 }

const ran = (stdout: string) => ({ value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })
const noTurn = () => ran('{"tokens":null,"at":null,"model":null}')

// stands for the engine beneath the plugin; `statuses` collects what the status line was set to, `answerRun` answers cg.py
const ids = { now: 'id' }
const chat = { model: 'claude-opus-5-5', switches: true } // what /model does in the mock
const wire = (on: any, statuses: (string | undefined)[], answerUsage: () => unknown, answerRun: (_: unknown, e: { argv: string[] }) => unknown = noTurn, store = new Map<string, unknown>()) => {
  ids.now = 'id'
  chat.model = 'claude-opus-5-5'
  chat.switches = true
  const clock = mock.clock(on, { now: 10_000_000_000 })
  on('store.get', (_: unknown, e: { key: string }) => ({ value: store.get(e.key) }))
  on('store.set', (_: unknown, e: { key: string; value: unknown }) => (store.set(e.key, e.value), { value: undefined }))
  on('store.keys', () => ({ value: [...store.keys()] }))
  on('store.delete', (_: unknown, e: { key: string }) => (store.delete(e.key), { value: undefined }))
  on('session.id', () => ({ value: ids.now }))
  on('session.model', () => ({ value: chat.model }))
  on('session.usage', answerUsage)
  on('session.version', () => ({ value: { version: '2.1.295' } }))
  on('process.run', answerRun)
  on('ui.status', (_: unknown, e: { text?: string }) => (statuses.push(e.text), { value: undefined }))
  on('ui.render', { component: 'AbovePrompt' }, () => ({ type: 'Text', children: ['no band'] }))
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
  expect(text).toMatch(/& "python" ".*cg\.py" --lang en embed --model embeddinggemma --max-seconds 100000/) // 400 records have no vectors: how to finish them, in the mod's language
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
  const state = { text: draft, refuse: undefined as string | undefined, broken: false, drop: undefined as string | undefined, nextId: undefined as string | undefined, clearFail: false, contexts: [] as string[][], asUser: [] as unknown[] }
  on('prompt.read', () => (state.broken ? { deny: 'boom' } : { value: { text: state.text, cursor: 0 } }))
  on('prompt.fill', (_: unknown, e: { text: string }) => (state.refuse ? { isFilled: false, refusal: state.refuse } : ((state.text = e.text), { isFilled: true })))
  on('prompt.submit', async (_: unknown, e: { text: string; origin?: { asUser?: boolean }; context?: readonly string[] }) => (log.push(`submit ${e.text}`), state.contexts.push([...(e.context ?? [])]), state.asUser.push(e.origin?.asUser), await gate, state.drop === undefined ? { text: e.text } : { drop: state.drop }))
  on('command.run', (_: unknown, e: { command: string; args: string }) => (log.push(`/${e.command} ${e.args}`.trim()), e.command === 'clear' && state.clearFail ? (() => { throw new Error('no clear') })() : (e.command === 'clear' && state.nextId && (ids.now = state.nextId), e.command === 'model' && chat.switches && (chat.model = `claude-${e.args}-5-5`), { text: '' })))
  return state
}
const withMemory = { value: { ...usage, context: { ...usage.context, breakdown: { memoryFiles: [{ path: 'C:/mem/memory/MEMORY.md', type: 'AutoMem', tokens: 1 }] } } } }
const handoffOk = (_: unknown, e: { argv: string[] }) => (e.argv.includes('handoff') ? ran(JSON.stringify({ text: '[al-handoff]x[/al-handoff]', path: 'C:/h/id.md' })) : noTurn())
const mountBand = async ($: any) => {
  await $.command.run(typedCmd('al-band'))
  return $.ui.mount({ plugin: 'alethite', surface: 'terminal', component: 'AbovePrompt', props } as never)
}

type Drawn = { type: string; props?: Record<string, unknown>; children: Drawn[] }

const texts = async (ui: any) => (await ui.findAll({ type: 'Text' })).map((t: { text: string }) => t.text).filter((t: string) => t !== 'no band') // the stand-in for another mod's band is not ours
const UI = { plugin: 'alethite', surface: 'terminal', component: 'AbovePrompt', props } as never

test('the band is always one row: the title first, no frame', async ($, on) => {
  wire(on, [], () => ({ value: usage }))
  const ui = await mountBand($)
  const band = (await ui.drawn()) as unknown as Drawn
  expect(band.children[1]!.props?.borderStyle).toBeUndefined()
  expect(await buttons(ui)).toEqual(['launch', 'details'])
  expect((await texts(ui))[0]).toBe(T.bandTitle)
})


test('a stale chat paints the size and the price as a warning, the price at the cold rate', async ($, on) => {
  const at = new Date(10_000_000_000 - 2 * 3_600_000).toISOString()
  wire(on, [], () => ({ value: usage }), () => ran(JSON.stringify({ tokens: 300_000, at, model: 'claude-opus-5-5' })))
  await $.command.run(typedCmd('al-band'))
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
  const frame = ((await ui.drawn()) as unknown as Drawn).children[1]!.children[0]! // the engine wraps the part of the mod: [part: [frame, the engine's slot for what is beneath]]
  expect(frame).toMatchObject({ type: 'Box', props: { borderStyle: 'round' } })
  expect(frame.children[0]).toMatchObject({ type: 'Text', props: { bold: true }, children: [T.bandTitle] })
  expect(await texts(ui)).toContain(T.col.chat)
  expect(store.get('details')).toBe(true)
  await ui.press({ key: 'details' })
  expect(store.get('details')).toBe(false)
  expect(((await ui.drawn()) as unknown as Drawn).children[1]!.children[0]!.props?.borderStyle).toBeUndefined()
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

test('desktop Details draws the columns as Text, with the rule Svg first; the terminal has no rule', async ($, on) => {
  wire(on, [], () => ({ value: usage }))
  const term = await mountBand($)
  expect(await term.findAll({ type: 'Svg' })).toHaveLength(0)
  const ui = await $.ui.mount({ plugin: 'alethite', surface: 'desktop', component: 'AbovePrompt', props } as never)
  expect(((await ui.drawn()) as unknown as Drawn).children[1]!.children[0]).toMatchObject({ type: 'Svg' })
  await ui.press({ key: 'details' })
  expect(await ui.findAll({ type: 'Svg' })).toHaveLength(1)
  expect(await texts(ui)).toContain(T.col.chat)
})

test('/al-band off hides the band in this chat, /al-band brings it back', async ($, on) => {
  wire(on, [], () => ({ value: usage }))
  const ui = await mountBand($)
  await ($ as any).command.run({ command: 'al-band', args: 'off' })
  expect(await buttons(ui)).toEqual([])
  await ($ as any).command.run({ command: 'al-band', args: '' })
  expect(await buttons(ui)).toEqual(['launch', 'details'])
})

test("another mod's band is not covered: it stays, and ours goes last, right above the prompt", async ($, on) => {
  wire(on, [], () => ({ value: usage })) // what is drawn beneath the plugin under test: the text 'no band', standing for another mod's band
  const band = (await (await mountBand($)).drawn()) as unknown as Drawn
  expect(band.children).toHaveLength(2)
  expect(band.children[0]).toMatchObject({ type: 'Text', children: ['no band'] })
  expect(JSON.stringify(band.children[1])).toContain(T.bandTitle) // the second child is the part of Alethite
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
    ['background_run', false, T.bgDescription],
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

test('exactly ten commands are registered, all al-*, none cg-*', async ($, on) => {
  const names: string[] = []
  on('command.register', (_: unknown, e: { name: string }) => (names.push(e.name), { value: { command: e.name } }))
  on('tool.register', () => ({ value: { tool: 'x' } }))
  mock.env(on, {})
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  wire(on, [], () => ({ value: usage }))
  await $.session.start({ cwd: 'C:/w', surface: null, isInteractive: false })
  expect(names).toEqual(['al-band', 'al-health', 'al-index', 'al-stats', 'al-sync', 'al-lessons', 'al-job', 'al-encrypt', 'al-index-dir', 'al-cleanup'])
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
  expect(names).toEqual({ commands: ['al-band', 'al-health', 'al-index', 'al-stats', 'al-sync', 'al-lessons', 'al-job', 'al-encrypt', 'al-index-dir', 'al-cleanup'], tools: ['recall', 'recall_more', 'lesson_save', 'background_run'] }) // every tool was tried, the commands went through
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
  expect((await ($ as any).command.run({ command: 'al-index', args: '' })).text).toBe(T.cmdFailed('al-index'))
  expect(m.toasts[1]).toBe(T.toast(T.failed('al-index', 'no such table')))
})

// ---- short memory ----
const facts = { model: 'claude-opus-5-5', promptModel: 'claude-opus-5-5', surfaces: [], tools: [], outputStyle: null, traits: [] }

test('prompt: the memory rule is the last section, on the session side, the same text every time', async ($, on) => {
  const engine = [{ id: 'intro', text: 'a', scope: 'shared' as const }, { id: 'env', text: 'b', scope: 'session' as const }]
  on('prompt.compose', () => ({ sections: engine }))
  const first = (await ($ as any).prompt.compose(facts)).sections
  expect(first).toEqual([...engine, { id: 'alethite:lean-agents', text: T.leanRule, scope: 'session' }, { id: 'alethite:memory-rule', text: T.memoryRule, scope: 'session' }])
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
  const v = { files: new Map<string, string>(), calls: [] as string[], listLimit: undefined as number | undefined, fail: (_tool: string) => false, failure: 'throws' as 'throws' | 'isError', onWrite: (_path: string) => {}, onRead: undefined as undefined | (() => Promise<void>) }
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
    const answer = ok(JSON.stringify({ path, frontmatter: Object.keys(frontmatter).length ? frontmatter : null, keys: Object.keys(frontmatter) }))
    return v.onRead ? v.onRead().then(() => answer) : answer // a read that takes time: the calls in flight can be counted
  })
  return v
}

const outboxKeys = (m: { store: Map<string, unknown> }) => [...m.store.keys()].filter(k => k.startsWith('outbox:'))
const statsRig = async ($: any, on: any, answer: (a: string[]) => unknown = () => ran(JSON.stringify({ metrics: {}, note: NOTE, errors: [] })), env: Record<string, string> = { COMPUTERNAME: 'PC-1' }, store = new Map<string, unknown>()) => {
  const vault = fakeVault(on)
  const m = await started($, on, a => (a[0] === 'stats' ? answer(a) : ran('{}')), undefined, undefined, env, undefined, store)
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
  expect([flag(argv, '--host'), flag(argv, '--limits'), flag(argv, '--rule-chars'), timeoutMs]).toEqual(['PC-1', JSON.stringify(usage.rateLimits), String(T.memoryRule.length + T.leanRule.length), 600_000])
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
  expect(outboxKeys(m)).toEqual([])
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
  expect(outboxKeys(m)).toEqual([`outbox:${notePath('PC-1', today())}`])
  expect(text).toContain(T.cgStatsQueued(1))
  expect(text).toContain('| метрика | база | сейчас |') // the figures are not lost with the vault
  expect(vault.files.size).toBe(0)

  vault.fail = () => false
  expect((await stats()).text).toContain(T.cgStatsSent(1)) // the same day: the new note replaces the queued one, not doubles it
  expect(vault.files.get(notePath('PC-1', today()))).toBe(NOTE)
  expect(outboxKeys(m)).toEqual([])
})

test("/al-stats: an answer with isError is a failure too, with the server's words", async ($, on) => {
  const { vault, m, stats } = await statsRig($, on)
  vault.fail = () => true
  vault.failure = 'isError'
  await stats()
  expect(m.toasts[0]).toContain('vault locked')
  expect(outboxKeys(m)).toHaveLength(1)
})

test('/al-stats: what waited from an earlier day goes first', async ($, on) => {
  const { vault, m, stats, today } = await statsRig($, on)
  const old = { path: notePath('PC-1', '2026-01-01'), content: 'old' }
  await m.store.set('outbox', [old])
  const duringSend: unknown[] = []
  vault.onWrite = () => duringSend.push(outboxKeys(m))
  const { text } = await stats()
  expect(duringSend[0]).toEqual([`outbox:${old.path}`, `outbox:${notePath('PC-1', today())}`]) // saved before the first send, in case the send is cut short
  expect(vault.calls.slice(0, 2)).toEqual([`obsidian create_vault_file ${old.path}`, `obsidian create_vault_file ${notePath('PC-1', today())}`])
  expect(vault.files.get(old.path)).toBe('old')
  expect(outboxKeys(m)).toEqual([])
  expect(text).toContain(T.cgStatsSent(2))
})

test('/al-stats: the vault dies after the first of two: only the rest stays queued', async ($, on) => {
  const { vault, m, stats, today } = await statsRig($, on)
  const old = { path: notePath('PC-1', '2026-01-01'), content: 'old' }
  await m.store.set('outbox', [old])
  vault.fail = () => vault.files.size === 1
  const { text } = await stats()
  expect(vault.files.has(old.path)).toBe(true)
  expect(outboxKeys(m)).toEqual([`outbox:${notePath('PC-1', today())}`])
  expect(text).toContain(T.cgStatsQueued(1))
})

test('/al-stats: the summary cannot be built: the note is safe, the owner is told', async ($, on) => {
  const { vault, m, stats, today } = await statsRig($, on)
  vault.fail = tool => tool === 'list_vault_files'
  const { text } = await stats()
  expect(vault.files.get(notePath('PC-1', today()))).toBe(NOTE)
  expect(outboxKeys(m)).toEqual([])
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
  expect((await stats()).text).toBe(T.cmdFailed('al-stats'))
  expect(m.toasts).toEqual([T.toast(T.failed('al-stats', 'PermissionError: x'))])
  expect(m.store.get('outbox')).toBeUndefined()
  expect(vault.calls).toEqual([])
})

test('/al-stats: no computer name, no note', async ($, on) => {
  const { vault, m, stats } = await statsRig($, on, undefined, {})
  expect((await stats()).text).toBe(T.cmdFailed('al-stats'))
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
  expect(m.specs.map(s => s.description)).toEqual([ru.recallDescription, ru.recallMoreDescription, ru.lessonSaveDescription, ru.bgDescription])
  expect((await ($ as any).prompt.compose(facts)).sections).toEqual([{ id: 'alethite:lean-agents', text: ru.leanRule, scope: 'session' }, { id: 'alethite:memory-rule', text: ru.memoryRule, scope: 'session' }])
})

test('leanRule: terse prompts phrase, and the rule stays short in every language', async () => {
  expect(T.leanRule).toContain('terse')
  expect(ru.leanRule).toContain('сжатым')
  expect(uk.leanRule).toContain('стислою')
  for (const rule of [T.leanRule, ru.leanRule, uk.leanRule]) expect(rule!.length).toBeLessThanOrEqual(1200)
})

test('language uk: Ukrainian to the owner and to the model, and to cg.py', { options: { language: 'uk' } }, async ($, on) => {
  const m = await started($, on, a => (a[0] === 'embed' ? embedded() : indexed()))
  await m.clock.advance(3_000)
  for (const lang of m.langs) expect(lang).toEqual(['--lang', 'uk'])
  expect((await ($ as any).command.run({ command: 'al-band', args: '' })).text).toBe(uk.cgBandOn)
  expect(m.specs.map(s => s.description)).toEqual([uk.recallDescription, uk.recallMoreDescription, uk.lessonSaveDescription, uk.bgDescription])
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

test('session.start fills the missing keys through cg.py', { options: { embedModel: 'bge-m3', syncDir: 'D:\\old' } }, async ($, on) => {
  settingsRig(on, '{ "language": "ru", "syncDir": "Z:\\\\cg" }')
  const m = await started($, on, () => ran('{"added":["embedModel"]}'), [], undefined, home)
  const fill = ofCg(m, 'settings-fill')
  expect(fill).toHaveLength(1)
  const values = JSON.parse(fill[0]!.argv[1]!.replace('--values=', ''))
  expect(values.embedModel).toBe('bge-m3')
  expect('syncDir' in values).toBe(false) // the file has it: its value stays
  expect(m.toasts).toEqual([])
})

test('a failing settings-fill is kept for /al-health and toasts nothing', { options: { embedModel: 'bge-m3' } }, async ($, on) => {
  settingsRig(on, '{ "language": "ru" }')
  const m = await started($, on, a => (a[0] === 'settings-fill' ? failed('OperationalError: database is locked') : ran('{}')), [], undefined, home)
  const { text } = await ($ as any).command.run({ command: 'al-health', args: '' })
  expect(m.store.get('lastError')).toMatchObject({ where: 'settings' })
  expect(text).toContain('OperationalError: database is locked')
  expect(m.toasts).toEqual([])
})

test('the old settings file with a missing key gets it through cg.py once, with the /config value', { options: { embedModel: 'bge-m3' } }, async ($, on) => {
  settingsRig(on, undefined, { [OLD_SETTINGS]: '{ "language": "ru" }' })
  const m = await started($, on, () => ran('{"added":["embedModel"]}'), [], undefined, home)
  const fill = ofCg(m, 'settings-fill')
  expect(fill).toHaveLength(1)
  expect(JSON.parse(fill[0]!.argv[1]!.replace('--values=', '')).embedModel).toBe('bge-m3')
})

test('session.start does not call settings-fill when the file has every key', async ($, on) => {
  settingsRig(on, undefined)
  const m = await started($, on, () => ran('{}'), [], undefined, home) // born with every value: nothing missing
  expect(ofCg(m, 'settings-fill')).toEqual([])
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
  expect((await ($ as any).command.run({ command: 'al-sync', args: '' })).text).toBe(T.cmdFailed('al-sync'))
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
const lessonRig = async ($: any, on: any, found: object[] = [{ ...lesson, score: 0.81 }], list: object[] = [lesson], options: { context?: string[][]; store?: Map<string, unknown> } = {}) => {
  const contexts = options.context ?? []
  on('prompt.submit', (_: unknown, e: { text: string; context?: readonly string[] }) => (contexts.push([...(e.context ?? [])]), { text: e.text, context: e.context }))
  on('tool.call', { tool: 'Bash' }, () => ({ result: 'ran' }))
  const m = await started($, on, a =>
    a[0] === 'lessons' ? listed(list) : a[0] === 'lessons-match' ? ran(JSON.stringify({ lessons: found, vector: 'ok' })) : a[0] === 'lesson-save' ? ran(JSON.stringify({ id: 9, conv: 'lessons-abc', pos: 1, text: 'Action: x\nSymptom: y\nCause: z', new: true, vector: 'ok' })) : a[0] === 'forget' ? ran('{"forgotten":1}') : ran('{}'),
    undefined, undefined, undefined, undefined, options.store,
  )
  await ($ as any).command.run({ command: 'al-lessons', args: '' }) // the list, awaited: the start reads it in the background
  return { m, contexts }
}
const typed = ($: any, text: string, extra: object = {}) => $.prompt.submit({ text, wait: false, origin: { kind: 'composer' }, ...extra })
// the gate's denial of the lesson above, between the marks the index cuts out (transcripts.py)
const gateText = () => ['[al-lessons]', T.lessonGate('Action: gh api in a loop | Symptom: HTTP 429 | Cause: 120 requests an hour | Trigger: gh api /repos', false), '[/al-lessons]'].join('\n')

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
  expect(m.store.get('lessonCounts:id')).toEqual({ stopped: 1 })
})

test('lessons: a PowerShell command is stopped once as well', async ($, on) => {
  on('tool.call', { tool: 'PowerShell' }, () => ({ result: 'ran' }))
  await lessonRig($, on)
  expect(await ($ as any).tool.call({ tool: 'PowerShell', command: 'gh api /repos/owner/x' })).toEqual({ deny: gateText() })
  expect(await ($ as any).tool.call({ tool: 'PowerShell', command: 'gh api /repos/owner/x' })).toEqual({ result: 'ran' })
})

test('lessons: after a compaction of the chat a lesson is shown and stops a command once more; a precompute, a skipped one and a subagent\'s change nothing', async ($, on) => {
  const kept: SessionMessage[] = [{ role: 'user', text: 'summary', toolUses: [] }] // a compaction leaves at least one message
  let result: SessionCompactResult = { messages: kept }
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
  const counts = m.store.get('lessonCounts:id') as Record<string, number>
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
  const kept: SessionMessage[] = [{ role: 'user', text: 'summary', toolUses: [] }]
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

test('global lessons: after a compaction the rules come once, also when the first turn after it is interrupted', async ($, on) => {
  const kept: SessionMessage[] = [{ role: 'user', text: 'summary', toolUses: [] }]
  on('session.compact', () => ({ messages: kept }))
  const contexts: string[][] = []
  const { m } = await lessonRig($, on, [], [rule], { context: contexts })
  const got = async () => (await typed($, 'да'), contexts.at(-1)!.length)
  withSize(m, 180_000)
  expect(await got()).toBe(1)
  await ($ as any).session.compact({ messages: kept })
  expect(m.store.get('turn:id')).toEqual({ at: 1, tokens: null }) // the size from before is gone, the time stays
  expect(await got()).toBe(1)
  expect(await got()).toBe(0) // no turn finished between: the size is still unknown
  withSize(m, 40_000) // a turn finished: this is the mark, not a shrink
  expect(await got()).toBe(0)
  expect(await got()).toBe(0)
  withSize(m, 60_000)
  expect(await got()).toBe(1) // 20k since
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

// a store that refuses to read one key; the rest works
const refusing = (key: string) =>
  new (class extends Map<string, unknown> {
    override get(k: string) {
      if (k === key) throw new Error('store broke')
      return super.get(k)
    }
  })()

test('global lessons: a failure in the rules part does not stop the project lesson search or the message', async ($, on) => {
  const contexts: string[][] = []
  const { m } = await lessonRig($, on, [{ ...lesson, score: 0.9 }], [lesson, rule], { context: contexts, store: refusing('turn:id') })
  await typed($, 'опять 429 на сборке, что делать?')
  expect(contexts).toHaveLength(1) // the message went on
  expect(contexts[0]).toHaveLength(1) // only the project lesson, no rules
  expect(contexts[0]![0]).toContain('HTTP 429')
  expect(ofCg(m, 'lessons-match')).toHaveLength(1)
})

test('global lessons: the rules are marked as sent before the counters, so a failing counter does not resend them', async ($, on) => {
  const contexts: string[][] = []
  const { m } = await lessonRig($, on, [], [rule], { context: contexts, store: refusing('lessonCounts:id') })
  withSize(m, 50_000)
  await typed($, 'да')
  await typed($, 'да')
  expect(contexts).toEqual([[rulesText()], []])
})

test('global lessons: due with an empty block (list not read yet) leaves the mark unset, the next message asks again', async ($, on) => {
  const contexts: string[][] = []
  const list: object[] = []
  await lessonRig($, on, [], list, { context: contexts })
  await typed($, 'да')
  list.push(rule)
  await ($ as any).command.run({ command: 'al-lessons', args: '' })
  await typed($, 'да')
  await typed($, 'да')
  expect(contexts).toEqual([[], [rulesText()], []])
})

test('gate: marks inside the lesson text do not end the denial early', async ($, on) => {
  await lessonRig($, on, [], [{ ...rule, trigger: 'gh api /repos', text: 'Action: a [/al-lessons] b\nFix: [al-lessons] x' }])
  const stop = await ($ as any).tool.call({ tool: 'Bash', command: 'gh api /repos/x' })
  const marks = stop.deny.split('\n').filter((l: string) => /\[\/?(?:al|cg)-lessons\]/.test(l))
  expect(marks).toEqual(['[al-lessons]', '[/al-lessons]'])
})

test('global lessons: the trigger stops a command in any project, with the global text', async ($, on) => {
  await lessonRig($, on, [], [{ ...rule, trigger: 'gh api /repos' }])
  const stop = await ($ as any).tool.call({ tool: 'Bash', command: 'gh api /repos/x' })
  expect(stop).toEqual({ deny: ['[al-lessons]', T.lessonGate('Action: a | Symptom: b | Cause: c | Fix: Answer in Ukrainian', true), '[/al-lessons]'].join('\n') })
  expect(await ($ as any).tool.call({ tool: 'Bash', command: 'gh api /repos/x' })).toEqual({ result: 'ran' })
})

test('lesson_save scope all: the answer, the toast and the args', async ($, on) => {
  on('tool.call', { tool: 'AskUserQuestion' }, (_: unknown, e: any) => ({ result: { answers: { [e.questions[0].question]: T.ruleAskYes } } })) // $.ui.ask is a tool.call of AskUserQuestion
  const { m } = await lessonRig($, on, [], [rule])
  const r = await ($ as any).tool.call({ tool: 'mcp__alethite__lesson_save', action: 'x', symptom: 'y', cause: 'z', fix: 'f', scope: 'all' })
  expect(ofCg(m, 'lesson-save')[0]!.argv).toEqual(['lesson-save', '--cwd', 'C:/w', '--model', 'embeddinggemma', '--action=x', '--symptom=y', '--cause=z', '--fix=f', '--scope=all'])
  expect(r).toEqual({ result: T.lessonSaved('Action: x\nSymptom: y\nCause: z', { isOn: true, stale: false }) })
  expect(m.toasts).toEqual([T.toast(T.lessonToast('Action: x | Symptom: y | Cause: z', { left: 0, isOn: true, stale: false }))])
})

test('global lessons: rules that no longer fit show in the save toast and in /al-health', async ($, on) => {
  on('tool.call', { tool: 'AskUserQuestion' }, (_: unknown, e: any) => ({ result: { answers: { [e.questions[0].question]: T.ruleAskYes } } })) // $.ui.ask is a tool.call of AskUserQuestion
  const long = (id: number, c: string) => ({ ...rule, id, pos: id, fix: c.repeat(300) })
  const { m } = await lessonRig($, on, [], [long(8, 'a'), long(9, 'b'), long(10, 'c'), long(11, 'd')]) // 4 x 302 > RULES_CHARS: three fit
  await typed($, 'да')
  await ($ as any).tool.call({ tool: 'mcp__alethite__lesson_save', action: 'x', symptom: 'y', cause: 'z', fix: 'f', scope: 'all' })
  expect(m.toasts).toEqual([T.toast(T.lessonToast('Action: x | Symptom: y | Cause: z', { left: 1, isOn: true, stale: false }))])
  const counts = m.store.get('lessonCounts:id') as Record<string, number>
  const { text } = await ($ as any).command.run({ command: 'al-health', args: '' })
  expect(text).toContain(T.healthRules(4, 1, counts.rulesChars!, 1, true))
})

test('lesson_save scope all: a failed re-read of the list keeps the old list and the answer and the toast say so', async ($, on) => {
  on('tool.call', { tool: 'AskUserQuestion' }, (_: unknown, e: any) => ({ result: { answers: { [e.questions[0].question]: T.ruleAskYes } } })) // $.ui.ask is a tool.call of AskUserQuestion
  let broken = false
  const list: object[] = [rule]
  const m = await started($, on, a => (a[0] === 'lessons' ? (broken ? failed('db locked') : listed(list)) : a[0] === 'lesson-save' ? ran(JSON.stringify({ text: 'Action: x', new: true })) : ran('{}')))
  await ($ as any).command.run({ command: 'al-lessons', args: '' })
  broken = true
  const r = await ($ as any).tool.call({ tool: 'mcp__alethite__lesson_save', action: 'x', symptom: 'y', cause: 'z', fix: 'f', scope: 'all' })
  const g = { left: 0, isOn: true, stale: true }
  expect(r).toEqual({ result: T.lessonSaved('Action: x', g) })
  expect(m.toasts).toEqual([T.toast(T.lessonToast('Action: x', g))])
  expect(T.lessonToast('Action: x', g)).not.toBe(T.lessonToast('Action: x', { ...g, stale: false }))
})

test('lesson_save scope all with lessons off does not promise the rule will come in every chat', { options: { lessons: false } }, async ($, on) => {
  on('tool.call', { tool: 'AskUserQuestion' }, (_: unknown, e: any) => ({ result: { answers: { [e.questions[0].question]: T.ruleAskYes } } })) // $.ui.ask is a tool.call of AskUserQuestion
  const { m } = await lessonRig($, on, [], [rule])
  const r = await ($ as any).tool.call({ tool: 'mcp__alethite__lesson_save', action: 'x', symptom: 'y', cause: 'z', fix: 'f', scope: 'all' })
  const g = { left: 0, isOn: false, stale: false }
  expect(r).toEqual({ result: T.lessonSaved('Action: x\nSymptom: y\nCause: z', g) })
  expect(m.toasts).toEqual([T.toast(T.lessonToast('Action: x | Symptom: y | Cause: z', g))])
  expect(T.lessonSaved('t', g)).not.toBe(T.lessonSaved('t', { ...g, isOn: true }))
})

test('global lessons: a rule that the sync brought from the other computer is toasted once, not silently added', async ($, on) => {
  const list: object[] = []
  const { m } = await lessonRig($, on, [], list)
  const arrived = T.toast(T.lessonToast('Action: a | Symptom: b | Cause: c | Fix: Answer in Ukrainian', { left: 0, isOn: true, stale: false }))
  const count = () => m.toasts.filter(t => t === arrived).length
  expect(count()).toBe(0)
  list.push(rule)
  await m.clock.advance(600_000) // the timer re-reads the list
  expect(count()).toBe(1)
  await m.clock.advance(600_000)
  expect(count()).toBe(1)
})

const arrivedText = () => T.toast(T.lessonToast('Action: a | Symptom: b | Cause: c | Fix: Answer in Ukrainian', { left: 0, isOn: true, stale: false }))

test('global lessons: the first read after the update stores the rules and says nothing', async ($, on) => {
  const { m } = await lessonRig($, on, [], [rule])
  expect(m.toasts).not.toContain(arrivedText())
  expect(m.store.get(`rulesSeen:${rule.pos}`)).toBe(true)
  expect(m.store.has('rulesSeen')).toBe(false)
})

test('global lessons: a rule the store has seen (another chat) says nothing', async ($, on) => {
  const { m } = await lessonRig($, on, [], [rule], { store: new Map<string, unknown>([['rulesSeen', [rule.pos]]]) })
  expect(m.toasts).not.toContain(arrivedText())
})

test('global lessons: a rule brought while no chat was open is toasted once, at the start, not again by the next read', async ($, on) => {
  const { m } = await lessonRig($, on, [], [rule], { store: new Map<string, unknown>([['rulesSeen', []]]) })
  await ($ as any).command.run({ command: 'al-lessons', args: '' })
  expect(m.toasts.filter(t => t === arrivedText())).toHaveLength(1)
})

test('/al-health shows the rules line', async ($, on) => {
  const { m } = await lessonRig($, on, [], [rule])
  await typed($, 'да')
  const counts = m.store.get('lessonCounts:id') as Record<string, number>
  expect(counts.rules).toBe(1)
  expect(counts.rulesChars).toBeGreaterThan(0)
  const { text } = await ($ as any).command.run({ command: 'al-health', args: '' })
  expect(text).toContain(T.healthRules(1, 1, counts.rulesChars!, 0, true))
  expect(text).toContain(T.healthLessons(0, 0, 0, 0, true))
})

test('/al-health: with lessons off the rules line says the reminders are off', { options: { lessons: false } }, async ($, on) => {
  await lessonRig($, on, [], [rule])
  const { text } = await ($ as any).command.run({ command: 'al-health', args: '' })
  expect(text).toContain(T.healthRules(1, 0, 0, 0, false))
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
  expect((await ($ as any).command.run({ command: 'al-job', args: 'on' })).text).toBe(T.cmdFailed('al-job'))
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
  expect(lines[lines.findIndex((l: string) => l.startsWith('Index:')) + 1]).toBe('n1')
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
  const at = lines.findIndex((l: string) => l.startsWith('Index:'))
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

const savingsJson = ran(JSON.stringify({ last: null, worker: { usd: 0, links: 0, calls: 0, lean: { usd: 0, agents: 0 } }, project: null }))

test('band data: status and savings are asked at the start and after /al-sync', { options: { syncDir: SHARE } }, async ($, on) => {
  const m = await started($, on, a => (a[0] === 'savings' ? savingsJson : a[0] === 'sync' ? synced() : a[0] === 'embed' ? embedded() : a[0] === 'index' ? indexed() : ran('{}')))
  await m.clock.advance(3_000)
  const asked = () => [ofCg(m, 'status').length, ofCg(m, 'savings').length]
  expect(ofCg(m, 'savings')[0]!.argv).toEqual(['savings', '--session', 'id', '--cwd', 'C:/w'])
  expect(ofCg(m, 'status')[0]!.argv).toEqual(['status', '--cwd=C:/w'])
  const [s0, v0] = asked()
  await ($ as any).command.run({ command: 'al-sync', args: '' })
  await m.clock.advance(0) // the refresh is not awaited by the reply
  expect(asked()).toEqual([s0! + 1, v0! + 1])
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

const worker = { last: null, worker: { usd: 18.7, links: 7, calls: 400, lean: { usd: 0, agents: 0 } }, project: null }
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
test('a stale row fits 60 columns', async ($, on) => {
  wire(on, [], () => ({ value: usage }), () => ran(JSON.stringify({ tokens: 300_000, at: STALE, model: 'm' })))
  await $.command.run(typedCmd('al-band'))
  const ui = await atColumns($, 60)
  expect(await buttons(ui)).toEqual(['launch', 'details'])
  expect(await rowWidth(ui)).toBeLessThanOrEqual(60)
})

test('the same in Russian: the name shortens to the shield when the buttons leave no room', { options: { language: 'ru' } }, async ($, on) => {
  wire(on, [], () => ({ value: usage }), () => ran(JSON.stringify({ tokens: 300_000, at: STALE, model: 'm' })))
  await $.command.run(typedCmd('al-band'))
  const ui = await atColumns($, 36)
  expect(await buttons(ui)).toEqual(['launch', 'details'])
  expect(await rowWidth(ui)).toBeLessThanOrEqual(36)
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
  await bandData($, on, ran(JSON.stringify({ ...worker, worker: { usd: -2, links: 3, calls: 9, lean: { usd: 0, agents: 0 } } })))
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

test('after-reset is offered while a window resets within 5 hours or is near its limit',async ($, on) => {
  wire(on, [], nearLimit())
  const ui = await mountBand($)
  await ui.press({ key: 'launch' })
  expect(await buttons(ui)).toEqual(['send', 'pick', 'after', 'cancel'])
  await ui.press({ key: 'after' })
  expect(await buttons(ui)).toEqual(['model', 'model-opus', 'model-sonnet', 'cancel']) // one time: straight to the model
  expect(await texts(ui)).toContain(T.afterReset(when(new Date(AT).toISOString(), NOW)))
})

test('after-reset is offered at low usage too', async ($, on) => {
  wire(on, [], nearLimit(undefined, 50))
  const ui = await mountBand($)
  await ui.press({ key: 'launch' })
  expect(await buttons(ui)).toEqual(['send', 'pick', 'after', 'cancel'])
})

test('after-reset is not offered without a reset time', async ($, on) => {
  wire(on, [], () => ({ value: { ...usage, rateLimits: [{ kind: 'five_hour', percentUsed: 92 }] } }))
  const ui = await mountBand($)
  await ui.press({ key: 'launch' })
  expect(await buttons(ui)).toEqual(['send', 'pick', 'cancel'])
})

test('a chat with no readings yet still gets After reset from the last reading, kept for all chats', async ($, on) => {
  let answer = nearLimit()
  const store = new Map<string, unknown>()
  wire(on, [], () => answer(), undefined, store)
  await ($ as any).command.run({ command: 'al-band', args: '' })
  expect(store.get('limits')).toEqual([{ kind: 'five_hour', percentUsed: 92, resetsAt: new Date(NOW + 3_600_000).toISOString() }])
  answer = () => ({ value: { ...usage, rateLimits: [] } }) // a fresh chat after /clear
  ids.now = 'other'
  await ($ as any).command.run({ command: 'al-band', args: '' })
  const ui = await mountBand($)
  await ui.press({ key: 'launch' })
  expect(await buttons(ui)).toEqual(['send', 'pick', 'after', 'cancel'])
  expect(store.get('limits')).toHaveLength(1)
})

test('an idle chat with an old reading does not overwrite a newer stored one, and writes nothing when unchanged', async ($, on) => {
  const newer = { kind: 'five_hour', percentUsed: 40, resetsAt: new Date(NOW + 3_600_000).toISOString() }
  let sets = 0
  class Counting extends Map<string, unknown> {
    set(k: string, v: unknown) {
      if (k === 'limits') sets++
      return super.set(k, v)
    }
  }
  const store = new Counting([['limits', [newer]]])
  sets = 0
  wire(on, [], nearLimit(new Date(NOW + 1000).toISOString(), 95), undefined, store) // an older window than the stored one
  await ($ as any).command.run({ command: 'al-band', args: '' })
  expect(store.get('limits')).toEqual([newer])
  expect(sets).toBe(0)
  const ui = await mountBand($)
  await ui.press({ key: 'launch' })
  expect(await buttons(ui)).toEqual(['send', 'pick', 'after', 'cancel'])
})

test('a stored reading whose reset has passed is not used',async ($, on) => {
  const store = new Map<string, unknown>([['limits', [{ kind: 'five_hour', percentUsed: 92, resetsAt: new Date(NOW - 1000).toISOString() }]]])
  wire(on, [], () => ({ value: { ...usage, rateLimits: [] } }), undefined, store)
  const ui = await mountBand($)
  await ui.press({ key: 'launch' })
  expect(await buttons(ui)).toEqual(['send', 'pick', 'cancel'])
})

test('after-reset needs a draft and waits', async ($, on) => {
  const { m, state, ui } = await delayed($, on, '')
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'after' })
  await ui.press({ key: 'model' })
  expect(m.toasts).toEqual([T.toast(T.draftEmpty)])
  expect(m.store.has('delayed:id')).toBe(false)

  state.text = 'продолжаем'
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'after' })
  await ui.press({ key: 'model' })
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

// After reset with a model: /model goes first (the model holds through /clear, probe 2026-11-15), and a model that did not switch stops the send
const withModel = (model: string, store = new Map<string, unknown>([['delayed:id', { at: SOON, model }]])) => store
test('After reset with a model: the wait keeps it, the plain button keeps none', async ($, on) => {
  const { m, ui } = await delayed($, on)
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'after' })
  await ui.press({ key: 'model-sonnet' })
  expect(m.store.get('delayed:id')).toEqual({ at: AT, model: 'sonnet' })
  await ui.press({ key: 'cancel-delay' })
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'after' })
  await ui.press({ key: 'model-opus' })
  expect(m.store.get('delayed:id')).toEqual({ at: AT, model: 'opus' })
  await ui.press({ key: 'cancel-delay' })
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'after' })
  await ui.press({ key: 'model' })
  expect(m.store.get('delayed:id')).toEqual({ at: AT })
})

const two = (five: number, week: number) => () => ({ value: { ...usage, rateLimits: [{ kind: 'five_hour', percentUsed: five, resetsAt: new Date(NOW + 3_600_000).toISOString() }, { kind: 'seven_day', percentUsed: week, resetsAt: new Date(NOW + 3 * 86_400_000).toISOString() }] } })
const MODELS = ['model', 'model-opus', 'model-sonnet', 'cancel']

test('after-reset asks the time when the week has room', async ($, on) => {
  const { m, ui } = await delayed($, on, 'продолжаем', new Map(), two(85, 91))
  await ui.press({ key: 'launch' })
  expect(await buttons(ui)).toEqual(['send', 'pick', 'after', 'cancel'])
  await ui.press({ key: 'after' })
  expect(await buttons(ui)).toEqual(['at-1', 'at-2', 'cancel'])
  const labels = (await ui.findAll({ type: 'Button' })).map((b: { text: string }) => b.text)
  expect(labels.slice(0, 2)).toEqual([NOW + 3_600_000 + 300_000, NOW + 3 * 86_400_000 + 300_000].map(t => T.afterReset(when(new Date(t).toISOString(), NOW))))
  await ui.press({ key: 'at-2' })
  expect(await buttons(ui)).toEqual(MODELS)
  await ui.press({ key: 'model-sonnet' })
  expect(m.store.get('delayed:id')).toEqual({ at: NOW + 3 * 86_400_000 + 300_000, model: 'sonnet' })
})

test('after-reset skips the time when the week is used up', async ($, on) => {
  const { m, ui } = await delayed($, on, 'продолжаем', new Map(), two(100, 100))
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'after' })
  expect(await buttons(ui)).toEqual(MODELS)
  await ui.press({ key: 'model' })
  expect(m.store.get('delayed:id')).toEqual({ at: NOW + 3 * 86_400_000 + 300_000 })
})

test('cancel on the model level returns to a plain band', async ($, on) => {
  const { ui } = await delayed($, on)
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'after' })
  await ui.press({ key: 'cancel' })
  await ui.press({ key: 'launch' })
  expect(await buttons(ui)).toEqual(['send', 'pick', 'after', 'cancel'])
})

test('cancel on the time level returns to a plain band', async ($, on) => {
  const { ui } = await delayed($, on, 'продолжаем', new Map(), two(85, 91))
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'after' })
  await ui.press({ key: 'cancel' })
  await ui.press({ key: 'launch' })
  expect(await buttons(ui)).toEqual(['send', 'pick', 'after', 'cancel'])
})

test('the time level goes back to the first one when a new snapshot leaves one time', async ($, on) => {
  let limits = two(91, 91)
  const { m, ui } = await delayed($, on, 'продолжаем', new Map(), () => limits())
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'after' })
  expect(await buttons(ui)).toEqual(['at-1', 'at-2', 'cancel'])
  // a new week starts (later reset, low share): only the 5-hour reset is left
  limits = () => ({ value: { ...usage, rateLimits: [{ kind: 'five_hour', percentUsed: 91, resetsAt: new Date(NOW + 3_600_000).toISOString() }, { kind: 'seven_day', percentUsed: 5, resetsAt: new Date(NOW + 10 * 86_400_000).toISOString() }] } })
  await m.clock.advance(30_000)
  expect(await buttons(ui)).toEqual(['send', 'pick', 'after', 'cancel'])
})

test('a wait with a model: /model after the handoff, before /clear and the send, which then goes out', async ($, on) => {
  const { m, log } = await delayed($, on, 'продолжаем', withModel('sonnet'))
  await m.clock.advance(SOON - NOW)
  expect(log).toEqual(['/model sonnet', '/clear', 'submit h' + String.fromCharCode(10, 10) + 'продолжаем'])
})

test('a wait with a model that does not switch: nothing is sent, the draft stays in the box, the toast says why', async ($, on) => {
  const { m, log, state } = await delayed($, on, 'продолжаем', withModel('sonnet'))
  chat.switches = false
  await m.clock.advance(SOON - NOW)
  expect(log).toEqual(['/model sonnet'])
  expect(state.text).toBe('продолжаем')
  expect(m.toasts).toContain(T.toast(T.modelSwitchFailed('sonnet')))
  expect(m.store.get('delayed:id')).toEqual({ at: SOON, model: 'sonnet' }) // nothing was launched: the wait is back once
  await m.clock.advance(3 * 30_000)
  expect(m.store.has('delayed:id')).toBe(false)
})

test('a 0.8.0 wait without a model never touches /model', async ($, on) => {
  const { m, log } = await delayed($, on, 'продолжаем', dueSoon())
  await m.clock.advance(SOON - NOW)
  expect(log).toEqual(['/clear', 'submit h' + String.fromCharCode(10, 10) + 'продолжаем'])
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
  const { m, log } = await delayed($, on, 'x', new Map([['delayed:other', { at: NOW }]]))
  await m.clock.advance(60_000)
  expect(log).toEqual([])
  expect(m.store.get('delayed:other')).toEqual({ at: NOW })
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
  await ui.press({ key: 'model' })
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
    : a[0] === 'savings' ? ran(JSON.stringify(a[2] === 'new' ? worker : { last: null, worker: { usd: 0, links: 0, calls: 0, lean: { usd: 0, agents: 0 } }, project: null }))
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

// ---- deleting the old chats (/al-cleanup) ----
const cleanupAnswer = (on: boolean) => ran(JSON.stringify({ on, preview: { chats: 2, mb: 4.56 } }))

test('/al-cleanup: on, off and no word go to cg.py as cleanup on|off|status and the reply has the chats and megabytes', async ($, on) => {
  const m = await started($, on, a => (a[0] === 'cleanup' ? cleanupAnswer(a[1] === 'on') : ran('{}')))
  const run = async (args: string) => (await ($ as any).command.run({ command: 'al-cleanup', args })).text
  expect(await run('on')).toBe(T.cleanupState(true, 2, 4.6))
  expect(await run('')).toBe(T.cleanupState(false, 2, 4.6))
  expect(await run('off')).toBe(T.cleanupState(false, 2, 4.6))
  expect(ofCg(m, 'cleanup').map(c => c.argv)).toEqual([['cleanup', 'on'], ['cleanup', 'status'], ['cleanup', 'off']])
})

test('/al-cleanup: a wrong word gets the hint and cg.py is not run; a failure goes to a toast', async ($, on) => {
  let fail = false
  const m = await started($, on, a => (a[0] === 'cleanup' ? (fail ? failed('ValueError: boom') : cleanupAnswer(true)) : ran('{}')))
  const run = async (args: string) => (await ($ as any).command.run({ command: 'al-cleanup', args })).text
  expect(await run('maybe')).toBe(T.cgCleanupUsage)
  expect(ofCg(m, 'cleanup')).toEqual([])
  fail = true
  expect(await run('on')).toBe(T.cmdFailed('al-cleanup'))
  expect(m.toasts).toEqual([T.toast(T.failed('al-cleanup', 'ValueError: boom'))])
})

// ---- the guard of cold agents ----
const AG = 'a0123456789abcdef'
const guardRig = async ($: any, on: any, answer: () => unknown = () => ran('{"found":true,"age":900,"limit":300,"context":100000,"usd":0.4}')) => {
  on('turn.start', () => ({ turnId: 't' }))
  on('tool.call', { tool: 'SendMessage' }, () => ({ result: 'sent' }))
  return started($, on, a => (a[0] === 'agent-age' ? answer() : ran('{}')))
}
const send = ($: any, to: string) => $.tool.call({ tool: 'SendMessage', to, message: 'go on' })
const coldText = () => T.coldAgent(15, '100k', '$0.40')

test('guard: a cold agent is stopped once with the price, the same message again passes', async ($, on) => {
  const m = await guardRig($, on)
  expect(await send($, AG)).toEqual({ deny: coldText() })
  expect(await send($, AG)).toEqual({ result: 'sent' })
  const { argv, timeoutMs } = ofCg(m, 'agent-age')[0]!
  expect(argv.slice(0, 5)).toEqual(['agent-age', '--session', 'id', '--agent', AG])
  expect(timeoutMs).toBe(8_000)
})

test('guard: a warm agent, an unfound one and a name nothing is known of pass', async ($, on) => {
  let reply = '{"found":true,"age":100,"limit":300,"context":1,"usd":0}'
  const m = await guardRig($, on, () => ran(reply))
  expect(await send($, AG)).toEqual({ result: 'sent' })
  reply = '{"found":false,"age":null,"limit":300,"context":0,"usd":0}'
  expect(await send($, 'a1111111111111111')).toEqual({ result: 'sent' })
  expect(await send($, 'researcher')).toEqual({ result: 'sent' })
  expect(ofCg(m, 'agent-age')).toHaveLength(2) // the name never reached cg.py
})

test('guard: an agent between turn.start and turn.complete passes without cg.py, after the end it is asked with the end time', async ($, on) => {
  const m = await guardRig($, on)
  await ($ as any).turn.start({ text: '', turnId: 't', agentId: AG })
  expect(await send($, AG)).toEqual({ result: 'sent' })
  expect(ofCg(m, 'agent-age')).toHaveLength(0)
  await ($ as any).turn.complete({ ...turn, agentId: AG })
  expect(await send($, AG)).toEqual({ result: 'sent' }) // ended under 5 min ago: warm, nothing to ask
  expect(ofCg(m, 'agent-age')).toHaveLength(0)
  await m.clock.advance(300_000)
  expect(await send($, AG)).toEqual({ deny: coldText() })
  const { argv } = ofCg(m, 'agent-age')[0]!
  expect(flag(argv, '--ended')).toBe(new Date(10_000_000_000).toISOString())
})

const failing = (name: string, fail: () => unknown) =>
  test(`guard: cg.py ${name}: it was asked, and the message goes through`, async ($, on) => {
    const m = await guardRig($, on, fail)
    expect(await send($, AG)).toEqual({ result: 'sent' })
    expect(await send($, AG)).toEqual({ result: 'sent' }) // nothing was remembered as stopped
    expect(ofCg(m, 'agent-age')).toHaveLength(2) // the guard path was reached both times
  })
failing('process.run rejects', () => ({ deny: 'boom' }))
failing('hook throws', () => {
  throw new Error('spawn broke')
})

test('guard: a name given at the spawn is the agent id', async ($, on) => {
  on('agent.spawn', () => ({ model: 'm', agentId: AG }))
  const m = await guardRig($, on)
  await ($ as any).agent.spawn({ prompt: 'p', name: 'scout-1' })
  expect(await send($, 'scout-1')).toEqual({ deny: coldText() })
  expect(flag(ofCg(m, 'agent-age')[0]!.argv, '--agent')).toBe(AG)
})

// ---- the guard of background commands ----
const bgRig = async ($: any, on: any, type: string) => {
  on('agent.spawn', () => ({ model: 'm', agentId: AG }))
  on('tool.call', { tool: ['Bash', 'PowerShell'] }, () => ({ result: 'ran' }))
  const m = await started($, on, () => ran('{}'))
  await $.agent.spawn({ prompt: 'p', subagentType: type })
  return m
}
const bash = ($: any, over: object) => $.tool.call({ tool: 'Bash', command: 'sleep 1', ...over })

test('background guard: a lean agent that asks for run_in_background is refused, in the foreground it passes, PowerShell the same', async ($, on) => {
  await bgRig($, on, 'alethite:coder')
  expect(await bash($, { agentId: AG, run_in_background: true })).toEqual({ deny: T.bgDenied })
  expect(await bash($, { agentId: AG, run_in_background: 'true' as never })).toEqual({ deny: T.bgDenied })
  expect(await bash($, { agentId: AG, run_in_background: false })).toEqual({ result: 'ran' })
  expect(await bash($, { agentId: AG })).toEqual({ result: 'ran' })
  expect(await $.tool.call({ tool: 'PowerShell', command: 'x', agentId: AG, run_in_background: true })).toEqual({ deny: T.bgDenied })
})

test('background guard: the main chat, another type and an unknown agent pass', async ($, on) => {
  await bgRig($, on, 'general-purpose')
  expect(await bash($, { agentId: AG, run_in_background: true })).toEqual({ result: 'ran' })
  expect(await bash($, { run_in_background: true })).toEqual({ result: 'ran' })
  expect(await bash($, { agentId: 'a1111111111111111', run_in_background: true })).toEqual({ result: 'ran' })
})

let hintReply = ''
const hintRig = async ($: any, on: any, reply: string) => {
  hintReply = reply
  return started($, on, a => (a[0] === 'review-hint' ? ran(hintReply) : noTurn()))
}
const submits = (on: any) => {
  const contexts: string[][] = []
  on('prompt.submit', (_: unknown, e: { text: string; context?: readonly string[] }) => (contexts.push([...(e.context ?? [])]), { text: e.text }))
  return contexts
}
const hintText = (n: number) => ['[al-lessons]', T.reviewHint(n), '[/al-lessons]'].join('\n')
const turnTo = async ($: any, m: any, id: string) => ((ids.now = id), await m.clock.advance(30_000)) // the tick refreshes the snap, the band asks for that chat

test('review hint: asked when New worker becomes ready, not on a redraw; the next prompt carries it once, the band shows nothing of it', async ($, on) => {
  const contexts = submits(on)
  const m = await hintRig($, on, '{"ponytail":true,"edits":6}')
  await $.command.run(typedCmd('al-band'))
  const ui = await $.ui.mount({ plugin: 'alethite', surface: 'terminal', component: 'AbovePrompt', props: { ...props, bodyColumns: 140 } } as never)
  expect(ofCg(m, 'review-hint')).toHaveLength(1)
  expect(flag(ofCg(m, 'review-hint')[0]!.argv, '--session')).toBe('id')
  await ui.press({ key: 'details' })
  await ui.press({ key: 'details' })
  expect(ofCg(m, 'review-hint')).toHaveLength(1)
  expect((await texts(ui)).join(' ')).not.toContain('ponytail')
  await typed($, 'first')
  await typed($, 'second')
  expect(contexts).toEqual([[hintText(6)], []])
})

test('review hint: edits fall under 5, then rise again: said once more', async ($, on) => {
  const contexts = submits(on)
  const m = await hintRig($, on, '{"ponytail":true,"edits":5}')
  await mountBand($)
  await typed($, 'a')
  hintReply = '{"ponytail":true,"edits":2}'
  await turnTo($, m, 'id2')
  await turnTo($, m, 'id')
  hintReply = '{"ponytail":true,"edits":6}'
  await turnTo($, m, 'id2')
  await turnTo($, m, 'id')
  await typed($, 'b')
  await typed($, 'c')
  expect(contexts).toEqual([[hintText(5)], [hintText(6)], []])
})

test('review hint: still at 5 or more after it was said: not said again', async ($, on) => {
  const contexts = submits(on)
  const m = await hintRig($, on, '{"ponytail":true,"edits":5}')
  await mountBand($)
  await typed($, 'a')
  await turnTo($, m, 'id2')
  await turnTo($, m, 'id')
  await typed($, 'b')
  expect(contexts).toEqual([[hintText(5)], []])
})

test('review hint: asked again for the next chat, with its session', async ($, on) => {
  const m = await hintRig($, on, '{"ponytail":true,"edits":6}')
  await mountBand($)
  await turnTo($, m, 'id2')
  expect(ofCg(m, 'review-hint')).toHaveLength(2)
  expect(flag(ofCg(m, 'review-hint')[1]!.argv, '--session')).toBe('id2')
})

test('review hint: no ponytail is not asked again for the next chat', async ($, on) => {
  const m = await hintRig($, on, '{"ponytail":false,"edits":0}')
  await mountBand($)
  await turnTo($, m, 'id2')
  expect(ofCg(m, 'review-hint')).toHaveLength(1)
})

test('review hint: not asked while the model works', async ($, on) => {
  const contexts = submits(on)
  const m = await hintRig($, on, '{"ponytail":true,"edits":6}')
  await $.command.run(typedCmd('al-band'))
  await $.ui.mount({ plugin: 'alethite', surface: 'terminal', component: 'AbovePrompt', props: { ...props, isWorking: true } } as never)
  expect(ofCg(m, 'review-hint')).toHaveLength(0)
  await typed($, 'a')
  expect(contexts).toEqual([[]])
})

for (const reply of ['{"ponytail":true,"edits":4}', '{"ponytail":false,"edits":9}'])
  test(`review hint: ${reply} gives the model nothing`, async ($, on) => {
    const contexts = submits(on)
    const m = await hintRig($, on, reply)
    await mountBand($)
    expect(ofCg(m, 'review-hint')).toHaveLength(1)
    await typed($, 'a')
    expect(contexts).toEqual([[]])
  })

test('review hint: lessons off, a command and a meta prompt leave it for the next real prompt', { options: { lessons: false } }, async ($, on) => {
  const contexts = submits(on)
  const m = await hintRig($, on, '{"ponytail":true,"edits":6}')
  await mountBand($)
  await typed($, '/cmd', { command: 'cmd' })
  await typed($, 'meta', { isMeta: true })
  await typed($, 'real')
  await typed($, 'next')
  expect(contexts).toEqual([[], [], [hintText(6)], []])
  expect(ofCg(m, 'review-hint')).toHaveLength(1)
})

test('review hint: a dropped prompt gives the hint back to the next one', async ($, on) => {
  const contexts: string[][] = []
  on('prompt.submit', (_: unknown, e: { text: string; context?: readonly string[] }) => (contexts.push([...(e.context ?? [])]), e.text === 'drop' ? { drop: 'no' } : { text: e.text }))
  await hintRig($, on, '{"ponytail":true,"edits":6}')
  await mountBand($)
  await typed($, 'drop')
  await typed($, 'again')
  await typed($, 'last')
  expect(contexts).toEqual([[hintText(6)], [hintText(6)], []])
})

test('review hint: cg.py fails: the prompt goes through without it', async ($, on) => {
  const contexts = submits(on)
  const m = await started($, on, a => (a[0] === 'review-hint' ? { deny: 'boom' } : noTurn()))
  await mountBand($)
  expect(ofCg(m, 'review-hint')).toHaveLength(1)
  await typed($, 'a')
  expect(contexts).toEqual([[]])
})

// ---- 0.9.2 audit: rules for a New worker, the rule-for-all question, lesson stops per agent ----
const workerOrigin = (name: string, asUser?: boolean) => ({ origin: { kind: 'plugin', name, ...(asUser === undefined ? {} : { asUser }) } })

test("a New worker's own asUser prompt gets the rules and lessons", async ($, on) => {
  const contexts: string[][] = []
  const { m } = await lessonRig($, on, [], [rule], { context: contexts })
  withSize(m, 50_000)
  await typed($, 'handoff and draft', workerOrigin('alethite', true))
  expect(contexts).toEqual([[rulesText()]])
})

test("a New worker's first message is searched for lessons by the owner's draft, not by the handoff", async ($, on) => {
  const { m } = await lessonRig($, on)
  await typed($, '[al-handoff]\nsession abc, memory folder C:/m, recall hint\n[/al-handoff]\n\nопять 429 на сборке, что делать?', workerOrigin('alethite', true))
  expect(ofCg(m, 'lessons-match').map(r => r.argv.find(a => a.startsWith('--text=')))).toEqual(['--text=\n\nопять 429 на сборке, что делать?'])
})

test("another plugin's asUser prompt and our prompt without asUser get none", async ($, on) => {
  const contexts: string[][] = []
  const { m } = await lessonRig($, on, [], [rule], { context: contexts })
  withSize(m, 50_000)
  await typed($, 'x', workerOrigin('other', true))
  await typed($, 'x', workerOrigin('alethite'))
  expect(contexts).toEqual([[], []])
})

test('a rule for all projects is saved only on Yes', async ($, on) => {
  let answer: string | undefined = T.ruleAskNo
  const asked: string[] = []
  on('tool.call', { tool: 'AskUserQuestion' }, (_: unknown, e: any) => (asked.push(JSON.stringify(e)), answer === undefined ? { deny: 'closed' } : { result: { answers: { [e.questions[0].question]: answer } } }))
  const { m } = await lessonRig($, on)
  const save = (extra: object) => ($ as any).tool.call({ tool: 'mcp__alethite__lesson_save', action: 'a', symptom: 'b', cause: 'c', fix: 'Answer in Ukrainian', ...extra })
  expect(await save({ scope: 'all' })).toEqual({ result: T.ruleDeclined })
  answer = undefined
  expect(await save({ scope: 'all' })).toEqual({ result: T.ruleDeclined })
  expect(ofCg(m, 'lesson-save')).toHaveLength(0)
  expect(asked).toHaveLength(2)
  expect(asked[0]).toContain('Answer in Ukrainian')
  answer = T.ruleAskYes
  await save({ scope: 'all' })
  expect(ofCg(m, 'lesson-save')).toHaveLength(1)
  await save({ scope: 'project' })
  expect(ofCg(m, 'lesson-save')).toHaveLength(2)
  expect(asked).toHaveLength(3) // a project lesson asks nothing
})

test('the rule question holds the rule as a chat reads it and ends with the question', async ($, on) => {
  const asked: string[] = []
  on('tool.call', { tool: 'AskUserQuestion' }, (_: unknown, e: any) => (asked.push(e.questions[0].question), { result: { answers: { [e.questions[0].question]: T.ruleAskNo } } }))
  await lessonRig($, on)
  await ($ as any).tool.call({ tool: 'mcp__alethite__lesson_save', action: 'a', symptom: 'b', cause: 'c', fix: 'x'.repeat(400), scope: 'all' })
  expect(asked).toEqual([T.ruleAsk(`${'x'.repeat(299)}…`)])
  expect(asked[0]!.endsWith('?')).toBe(true)
})

test('a rule for all projects without a fix asks nothing: cg.py refuses it', async ($, on) => {
  const asked: unknown[] = []
  on('tool.call', { tool: 'AskUserQuestion' }, (_: unknown, e: unknown) => (asked.push(e), { result: { answers: {} } }))
  const m = await started($, on, a => (a[0] === 'lesson-save' ? failed('ValueError: a lesson for all projects needs fix') : ran('{}')))
  for (const fix of [{ fix: '' }, {}]) {
    const r = await ($ as any).tool.call({ tool: 'mcp__alethite__lesson_save', action: 'a', symptom: 'b', cause: 'c', scope: 'all', ...fix })
    expect(r).toEqual({ deny: T.lessonSaveFailed('ValueError: a lesson for all projects needs fix') })
  }
  expect(ofCg(m, 'lesson-save')).toHaveLength(2)
  expect(asked).toEqual([])
})

test('the review hint comes back when next() throws', async ($, on) => {
  let isBroken = true
  const contexts: string[][] = []
  on('prompt.submit', (_: unknown, e: { text: string; context?: readonly string[] }) => {
    contexts.push([...(e.context ?? [])])
    if (isBroken) throw new Error('boom')
    return { text: e.text }
  })
  await hintRig($, on, '{"ponytail":true,"edits":6}')
  await mountBand($)
  await expect(typed($, 'a')).rejects.toThrow()
  isBroken = false
  await typed($, 'b')
  expect(contexts).toEqual([[hintText(6)], [hintText(6)]])
})

test("a subagent's stop does not use up the main chat's", async ($, on) => {
  await lessonRig($, on)
  const run = (extra: object = {}) => ($ as any).tool.call({ tool: 'Bash', command: 'gh api /repos/x', ...extra })
  expect(await run({ agentId: 'a1' })).toEqual({ deny: gateText() })
  expect(await run({ agentId: 'a1' })).toEqual({ result: 'ran' })
  expect(await run()).toEqual({ deny: gateText() })
  expect(await run()).toEqual({ result: 'ran' })
})

test('a failing review-hint is recorded once per chat, and again after it worked', async ($, on) => {
  let isOk = false
  const m = await started($, on, a => (a[0] === 'review-hint' ? (isOk ? ran('{"ponytail":true,"edits":0}') : { deny: 'boom' }) : noTurn()))
  await mountBand($)
  expect(m.store.get('lastError')).toMatchObject({ where: 'review' })
  await turnTo($, m, 'id2')
  m.store.set('lastError', { at: 1, where: 'sync', msg: 'other step' })
  await turnTo($, m, 'id') // the same chat fails again
  expect(ofCg(m, 'review-hint')).toHaveLength(3)
  expect(m.store.get('lastError')).toMatchObject({ where: 'sync' })
  isOk = true
  await turnTo($, m, 'id2')
  await turnTo($, m, 'id') // it works now
  isOk = false
  await turnTo($, m, 'id2')
  m.store.set('lastError', { at: 1, where: 'sync', msg: 'other step' })
  await turnTo($, m, 'id') // and fails once more: a new failure, recorded
  expect(ofCg(m, 'review-hint')).toHaveLength(7)
  expect(m.store.get('lastError')).toMatchObject({ where: 'review' })
})

test('the summary reads only new notes', async ($, on) => {
  const { vault, stats, today } = await statsRig($, on)
  await stats() // only to learn the day of the mock clock
  const day = (back: number) => new Date(Date.parse(today()) - back * 86_400_000).toISOString().slice(0, 10)
  const old = `${T.statsFolder}/${day(5)} PC-2.md`
  const recent = `${T.statsFolder}/${day(1)} PC-3.md`
  vault.files.set(old, OTHER)
  vault.files.set(recent, OTHER)
  await stats()
  const reads = () => vault.calls.filter(c => c.startsWith('obsidian get_note_properties')).map(c => c.slice('obsidian get_note_properties '.length))
  expect(reads()).toContain(old)
  vault.calls.length = 0
  await stats()
  expect(reads()).not.toContain(old)
  expect(reads()).toContain(recent) // the last two days are read again
  expect(vault.files.get(summaryPath())).toContain('PC-2') // the kept row is still in the table
})

test('a final note read with no frontmatter is read again next time', async ($, on) => {
  const { vault, m, stats, today } = await statsRig($, on)
  await stats()
  const half = `${T.statsFolder}/${new Date(Date.parse(today()) - 5 * 86_400_000).toISOString().slice(0, 10)} PC-2.md`
  vault.files.set(half, 'half synced') // no frontmatter yet
  await stats()
  expect(m.store.has(`summaryRow:${half}`)).toBe(false)
  vault.files.set(half, OTHER)
  vault.calls.length = 0
  await stats()
  expect(vault.calls).toContain(`obsidian get_note_properties ${half}`)
  expect(m.store.get(`summaryRow:${half}`)).toMatchObject({ host: 'PC-2' })
})

test('the kept row of a note no longer in the folder is deleted, and a store that refuses a row still gets the summary', async ($, on) => {
  let isFull = false
  class FullStore extends Map<string, unknown> {
    override set(k: string, v: unknown) {
      if (isFull && k.startsWith('summaryRow:')) throw new Error('the store is full')
      return super.set(k, v)
    }
  }
  const gone = `summaryRow:${T.statsFolder}/2025-01-01 PC-9.md` // moved into a year subfolder
  const { vault, m, stats } = await statsRig($, on, undefined, undefined, new FullStore([[gone, { host: 'PC-9' }]]))
  isFull = true
  vault.files.set(`${T.statsFolder}/2026-01-01 PC-2.md`, OTHER)
  await stats()
  expect(m.store.has(gone)).toBe(false)
  expect(vault.files.get(summaryPath())).toContain('PC-2')
  expect(m.toasts).toEqual([])
})

test('the summary reads at most 8 notes at once and lists them all', async ($, on) => {
  const { vault, stats } = await statsRig($, on)
  for (let i = 1; i <= 20; i++) vault.files.set(`${T.statsFolder}/2026-01-${String(i).padStart(2, '0')} PC-2.md`, OTHER)
  let [now, peak] = [0, 0]
  vault.onRead = async () => {
    peak = Math.max(peak, ++now)
    await new Promise(r => (globalThis as any).setTimeout(r, 5))
    now--
  }
  await stats()
  expect(peak).toBe(8)
  expect(vault.files.get(summaryPath())!.split('\n').filter(l => /^\| 2026/.test(l))).toHaveLength(21) // and the note of today
})

test('old rulesSeen and outbox are carried over', async ($, on) => {
  const store = new Map<string, unknown>([['rulesSeen', [5, 7]]])
  const { m } = await lessonRig($, on, [], [], { store })
  expect([...m.store.keys()].filter(k => k.startsWith('rulesSeen'))).toEqual(expect.arrayContaining(['rulesSeen:5', 'rulesSeen:7']))
  expect(m.store.has('rulesSeen')).toBe(false)
})

test('old outbox is carried over into one key per note', async ($, on) => {
  const { vault, m, stats, today } = await statsRig($, on)
  vault.fail = () => true
  const n1 = { path: notePath('PC-1', '2026-01-01'), content: 'old' }
  await m.store.set('outbox', [n1])
  await stats()
  expect(outboxKeys(m)).toEqual([`outbox:${n1.path}`, `outbox:${notePath('PC-1', today())}`])
  expect(m.store.has('outbox')).toBe(false)
  expect(m.store.get(`outbox:${n1.path}`)).toEqual(n1)
})

test('two chats counting at once lose nothing', async ($, on) => {
  const { m } = await lessonRig($, on)
  await ($ as any).tool.call({ tool: 'Bash', command: 'gh api /repos/x' })
  ids.now = 'other'
  await ($ as any).tool.call({ tool: 'Bash', command: 'gh api /repos/x' })
  expect(m.store.get('lessonCounts:id')).toEqual({ stopped: 1 })
  expect(m.store.get('lessonCounts:other')).toEqual({ stopped: 1 })
  const { text } = await ($ as any).command.run({ command: 'al-health', args: '' })
  expect(text).toContain(T.healthLessons(1, 0, 0, 2, true))
})

test('a chat that is pruned folds its counters into the total', async ($, on) => {
  const store = new Map<string, unknown>([['lessonCounts', { stopped: 1 }], ['lessonCounts:c1', { stopped: 2, shown: 1 }]])
  for (let i = 1; i <= 200; i++) store.set(`turn:c${i}`, { at: i, tokens: i })
  wire(on, [], () => ({ value: usage }), undefined, store)
  await ($ as any).turn.complete(turn)
  expect(store.get('lessonCounts')).toEqual({ stopped: 3, shown: 1 })
  expect(store.has('lessonCounts:c1')).toBe(false)
})

test('stale After-reset waits are removed at the start', async ($, on) => {
  const store = new Map<string, unknown>([['delayed:x', { at: NOW - 25 * 3_600_000 }], ['delayed:y', { at: NOW + 3_600_000 }]])
  const { m } = await delayed($, on, 'x', store)
  expect(m.store.has('delayed:x')).toBe(false)
  expect(m.store.get('delayed:y')).toEqual({ at: NOW + 3_600_000 })
})

test('a failed handoff puts the After-reset wait back once', async ($, on) => {
  const log: string[] = []
  const store = dueSoon()
  box(on, 'продолжаем', log)
  const m = await started($, on, a => (a[0] === 'handoff' ? failed('no transcript') : ran('{}')), [], nearLimit(), {}, noTurn, store)
  await $.ui.mount(UI)
  await m.clock.advance(SOON - NOW)
  expect(m.store.get('delayed:id')).toEqual({ at: SOON }) // back with the same time
  await m.clock.advance(3 * 30_000)
  expect(m.store.has('delayed:id')).toBe(false) // the second failure with the same time is not put back
  expect(log).toEqual([])
})

// ---- 0.9.2: the edge of cg.py, one failure text, the embed command, the cache clock ----
test('a cut answer of cg.py is an error, not exit 0', async ($, on) => {
  const m = await started($, on, () => ({ value: { exitCode: 0, stdout: '{"ok":1', stderr: '', isStdoutTruncated: true, isStderrTruncated: false } }))
  expect((await ($ as any).command.run({ command: 'al-index', args: '' })).text).toBe(T.cmdFailed('al-index'))
  expect(m.toasts).toEqual([T.toast(T.failed('al-index', T.cgTooLong))])
})

test('every command failure reads the same', async ($, on) => {
  const m = await started($, on, () => failed('boom'))
  expect((await ($ as any).command.run({ command: 'al-index', args: '' })).text).toBe(T.cmdFailed('al-index'))
  expect((await ($ as any).command.run({ command: 'al-job', args: 'on' })).text).toBe(T.cmdFailed('al-job'))
  expect(m.toasts).toEqual([T.toast(T.failed('al-index', 'boom')), T.toast(T.failed('al-job', 'boom'))])
})

test('the embed command quotes the python path', { options: { python: 'C:\\Program Files\\Python\\python.exe' } }, async ($, on) => {
  const vectors = { model: 'embeddinggemma', with_vectors: 1, total: 2, updated: null }
  on('fs.list', () => ({ value: [] }))
  await started($, on, a => (a[0] === 'status' ? ran(JSON.stringify({ vectors })) : ran('{}')))
  const { text } = await ($ as any).command.run({ command: 'al-health', args: '' })
  expect(text).toContain('& "C:\\Program Files\\Python\\python.exe" "')
})

test('the cache clock is the start of the last request of the turn, not its end', async ($, on) => {
  on('turn.step', async function* () {
    return { turnId: 't', index: 0, answer: '', toolUses: [], stopReason: null, usage: null }
  })
  const m = await started($, on, () => noTurn())
  for await (const _ of ($ as any).turn.step({ turnId: 't', index: 0, model: 'm', messageCount: 1 })) void _ // a stream: the hook runs as it is read
  await m.clock.advance(5_000)
  await ($ as any).turn.complete(turn)
  expect(m.store.get('turn:id')).toMatchObject({ at: 10_000_000_000 })
})

// the band's rows come in the order the plugins loaded: our row goes last, right above the prompt
const bandRow = (plugin: string) => ({ plugin, tree: { type: 'Text', children: [plugin] } })
const bandRows = async ($: any, on: any, rows: unknown[]) => {
  let got: any
  on('ui.render', { component: 'AbovePromptList' }, (_: unknown, e: any) => ((got = e), { type: 'Text', children: ['x'] }))
  await $.ui.mount({ plugin: 'alethite', surface: 'terminal', component: 'AbovePromptList', props: { ...props, rows } } as never)
  return got
}

test('the Alethite row moves to the end of the band', async ($, on) => {
  const [a, p, o] = [bandRow('alethite'), bandRow('ponytail'), bandRow('other')]
  const got = await bandRows($, on, [a, p, o])
  expect(got.props.rows).toEqual([p, o, a])
  expect(got.props.maxRows).toBe(10)
})

test('the band keeps its order when our row is already last', async ($, on) => {
  const [a, p] = [bandRow('alethite'), bandRow('ponytail')]
  expect((await bandRows($, on, [p, a])).props.rows).toEqual([p, a])
})

test('the band keeps its order when we have no row', async ($, on) => {
  const [p, o] = [bandRow('ponytail'), bandRow('other')]
  expect((await bandRows($, on, [p, o])).props.rows).toEqual([p, o])
})

// background runs: the tool, the token-free watch, the band row
const BG_DIR = 'C:/bg/r1'
const BG_STATUS = 'C:/w/status.txt'
const bgRun = (over: object = {}) => ({ dir: BG_DIR, name: 'train', pid: 7, started: NOW - 5 * 60_000, log: `${BG_DIR}/log.txt`, statusFile: BG_STATUS, ifCold: 'show', ...over })
const bgCall = ($: any, over: object = {}) => $.tool.call({ tool: 'mcp__alethite__background_run', name: 'train', command: 'python train.py', if_cold: 'show', ...over })
const bgVerbs = (m: { runs: { argv: string[] }[] }) => m.runs.filter(r => verb(r.argv)[0]!.startsWith('bg-'))
const norm = (p: string) => p.replace(/\\/g, '/') // the engine may hand paths back with backslashes
const runRig = async ($: any, on: any, store = new Map<string, unknown>(), files: Record<string, string> = {}, answerUsage: () => unknown = () => ({ value: usage }), init: object = {}) => {
  const fs = { files, readError: undefined as string | undefined, alive: true, startError: undefined as string | undefined, handoffError: undefined as string | undefined, maintainError: undefined as string | undefined, onAlive: undefined as (() => void) | undefined, onHandoff: undefined as (() => unknown) | undefined, gate: undefined as Promise<void> | undefined, ...init }
  on('fs.exists', (_: unknown, e: { path: string }) => ({ value: norm(e.path) in fs.files }))
  on('fs.read', async (_: unknown, e: { path: string }) => (await fs.gate, fs.readError ? { deny: fs.readError } : norm(e.path) in fs.files ? { value: fs.files[norm(e.path)] } : { deny: 'ENOENT' }))
  const answer = (a: string[]) => (a[0] === 'maintain-detached' && fs.maintainError ? failed(fs.maintainError) : a[0] === 'bg-alive' ? (fs.onAlive?.(), ran(JSON.stringify({ alive: fs.alive }))) : a[0] === 'bg-start' ? (fs.startError ? failed(fs.startError) : ran(JSON.stringify({ dir: BG_DIR, pid: 7, log: `${BG_DIR}/log.txt` }))) : a[0] === 'handoff' ? Promise.resolve(fs.onHandoff?.()).then(() => (fs.handoffError ? failed(fs.handoffError) : ran(JSON.stringify({ text: 'h', path: 'C:/h/id.md' })))) : a[0] === 'index' ? indexed() : a[0] === 'embed' ? embedded() : ran('{}'))
  const m = await started($, on, answer, [], answerUsage, {}, noTurn, store)
  return { m, fs, ui: await $.ui.mount(UI) }
}
const bgTexts = async (ui: any) => (await texts(ui)).filter((t: string) => t.includes('train') || t.includes('far'))

test('background_run: a subagent is refused, nothing is started', async ($, on) => {
  const { m } = await runRig($, on)
  expect(await bgCall($, { agentId: AG })).toEqual({ deny: T.bgSubagent })
  expect(bgVerbs(m)).toEqual([])
})

test('background_run: a run that has not ended refuses a second one, with its name', async ($, on) => {
  const { m } = await runRig($, on, new Map([['bg:id', bgRun()]]))
  expect(await bgCall($, { name: 'other' })).toEqual({ deny: T.bgBusy('train', when(new Date(NOW - 5 * 60_000).toISOString(), NOW)) })
  expect(bgVerbs(m).filter(r => r.argv[0] === 'bg-start')).toEqual([])
})

test('background_run: starts through cg.py, keeps the record, tells the model the log', async ($, on) => {
  const { m, ui } = await runRig($, on)
  const r = await bgCall($, { status_file: BG_STATUS, cwd: 'C:/x' })
  expect(r).toEqual({ result: T.bgStarted('train', `${BG_DIR}/log.txt`) })
  const a = bgVerbs(m)[0]!.argv
  expect(a[0]).toBe('bg-start')
  expect(a).toEqual(expect.arrayContaining(['--session=id', '--name=train', '--cwd=C:/x', '--if-cold=show', '--command=python train.py', `--status-file=${BG_STATUS}`]))
  expect(m.store.get('bg:id')).toEqual({ dir: BG_DIR, name: 'train', pid: 7, started: NOW, log: `${BG_DIR}/log.txt`, statusFile: BG_STATUS, ifCold: 'show' })
  expect(await bgTexts(ui)).toHaveLength(1) // the row is there at once
})

test('background_run: a bad input starts nothing', async ($, on) => {
  const { m } = await runRig($, on)
  expect(await bgCall($, { if_cold: 'later' })).toEqual({ deny: T.bgBadInput })
  expect(bgVerbs(m)).toEqual([])
})

test('background_run: a refusal of cg.py goes to the model, a toast and /al-health', async ($, on) => {
  const { m, fs } = await runRig($, on)
  fs.startError = 'no lock'
  expect(await bgCall($)).toEqual({ deny: 'no lock' })
  expect(m.toasts).toEqual([T.toast(T.failed('background_run', 'no lock'))])
  expect(m.store.has('bg:id')).toBe(false)
})

test('the 30 s tick starts no process; the row shows the last full line of the status file', async ($, on) => {
  const { m, ui } = await runRig($, on, new Map([['bg:id', bgRun()]]), { [BG_STATUS]: 'a\nepoch 3 loss 0.4\nepo' })
  const before = bgVerbs(m).length
  await m.clock.advance(30_000)
  expect(bgVerbs(m).length).toBe(before)
  expect(await bgTexts(ui)).toEqual([bgRow(bgRun() as BgRun, 'epoch 3 loss 0.4', NOW + 30_000).text])
})

test('the row says a dash when there is no status file', async ($, on) => {
  const { m, ui } = await runRig($, on, new Map([['bg:id', bgRun()]]))
  await m.clock.advance(30_000)
  expect((await bgTexts(ui))[0]).toContain(' · — · ')
  expect(m.store.has('lastError')).toBe(false)
})

test('a status file over 4 MiB: the row says so, no toast in 3 ticks', async ($, on) => {
  const { m, fs, ui } = await runRig($, on, new Map([['bg:id', bgRun()]]), { [BG_STATUS]: 'x' })
  fs.readError = 'file is over 4 MiB'
  await m.clock.advance(3 * 30_000)
  expect((await bgTexts(ui))[0]).toContain(T.bgTooBig)
  expect(m.toasts).toEqual([])
})

test('another read error: a dash in the row, a note for /al-health, no toast', async ($, on) => {
  const { m, fs, ui } = await runRig($, on, new Map([['bg:id', bgRun()]]), { [BG_STATUS]: 'x' })
  fs.readError = 'EACCES'
  await m.clock.advance(3 * 30_000)
  expect((await bgTexts(ui))[0]).toContain(' · — · ')
  expect(m.store.get('lastError')).toMatchObject({ where: 'bg' })
  expect(m.toasts).toEqual([])
})

test('exit.json appears: the record gets the end with the code from the file', async ($, on) => {
  const { m, fs, ui } = await runRig($, on, new Map([['bg:id', bgRun()]]))
  fs.files[`${BG_DIR}/exit.json`] = JSON.stringify({ code: 3, ended: '2026-11-23T10:00:00Z' })
  await m.clock.advance(30_000)
  expect((m.store.get('bg:id') as BgRun).ended).toEqual({ code: 3, at: Date.parse('2026-11-23T10:00:00Z') })
  expect((await bgTexts(ui))[0]).toContain(T.bgExit(3))
})

test('the 600 s tick asks bg-alive; dead and no exit.json means lost', async ($, on) => {
  const { m, fs } = await runRig($, on, new Map([['bg:id', bgRun()]]))
  fs.alive = false
  await m.clock.advance(600_000)
  expect(bgVerbs(m).some(r => r.argv[0] === 'bg-alive' && r.argv.includes(`--dir=${BG_DIR}`))).toBe(true)
  expect((m.store.get('bg:id') as BgRun).ended).toMatchObject({ code: null, lost: true })
})

test('dead but exit.json is there: the code from the file wins over lost', async ($, on) => {
  const { m, fs } = await runRig($, on, new Map([['bg:id', bgRun()]]))
  fs.alive = false
  fs.files[`${BG_DIR}/exit.json`] = JSON.stringify({ code: 0, ended: '2026-11-23T10:00:00Z' })
  await m.clock.advance(600_000)
  expect((m.store.get('bg:id') as BgRun).ended).toEqual({ code: 0, at: Date.parse('2026-11-23T10:00:00Z') })
})

test('bg-alive: exit.json appears while it runs, the real code stays', async ($, on) => {
  const { m, fs } = await runRig($, on, new Map([['bg:id', bgRun()]]))
  fs.alive = false
  fs.onAlive = () => { fs.files[`${BG_DIR}/exit.json`] = JSON.stringify({ code: 5, ended: '2026-11-23T10:00:00Z' }) }
  await m.clock.advance(600_000)
  expect((m.store.get('bg:id') as BgRun).ended).toEqual({ code: 5, at: Date.parse('2026-11-23T10:00:00Z') })
})

test('bg-alive: the record is removed while it runs, it is not recreated', async ($, on) => {
  const { m, fs } = await runRig($, on, new Map([['bg:id', bgRun()]]))
  fs.alive = false
  fs.onAlive = () => { m.store.delete('bg:id') }
  await m.clock.advance(600_000)
  expect(m.store.has('bg:id')).toBe(false)
})

test('exit.json that is not valid JSON: the run is lost, not running forever', async ($, on) => {
  const { m, fs } = await runRig($, on, new Map([['bg:id', bgRun()]]))
  fs.files[`${BG_DIR}/exit.json`] = '{broken'
  await m.clock.advance(30_000)
  expect((m.store.get('bg:id') as BgRun).ended).toMatchObject({ code: null, lost: true })
})

test('overlapping watches: one toast for another chat run', async ($, on) => {
  const { m, fs } = await runRig($, on, new Map([['bg:other', bgRun({ name: 'far' })]]))
  fs.files[`${BG_DIR}/exit.json`] = JSON.stringify({ code: 0, ended: '2026-11-23T10:00:00Z' })
  await Promise.all([m.clock.advance(30_000), m.clock.advance(30_000)])
  expect(m.toasts.filter(t => t.includes('far'))).toHaveLength(1)
})

test('background_run: quotes, &, Cyrillic and a leading - stay one argv item', async ($, on) => {
  const { m } = await runRig($, on)
  const command = `-x "a b" & echo 'привіт' && dir`
  await bgCall($, { command })
  expect(bgVerbs(m)[0]!.argv).toContain(`--command=${command}`)
})

test('[x] removes the record and the row', async ($, on) => {
  const { m, ui } = await runRig($, on, new Map([['bg:id', bgRun()]]))
  await m.clock.advance(30_000)
  expect(await buttons(ui)).toContain('bg-remove')
  await ui.press({ key: 'bg-remove' })
  expect(m.store.has('bg:id')).toBe(false)
  expect(await bgTexts(ui)).toEqual([])
})

test("another chat's run ends: one toast ever, no prompt", async ($, on) => {
  const log: string[] = []
  box(on, '', log)
  const { m, fs } = await runRig($, on, new Map([['bg:other', bgRun({ name: 'far' })]]))
  fs.files[`${BG_DIR}/exit.json`] = JSON.stringify({ code: 0, ended: '2026-11-23T10:00:00Z' })
  await m.clock.advance(3 * 30_000)
  expect(m.toasts).toHaveLength(1)
  expect(m.toasts[0]).toContain('far')
  expect((m.store.get('bg:other') as { toasted?: true }).toasted).toBe(true)
  expect(log).toEqual([])
})

test('a watch asked meanwhile: one toast, one more pass shows the run started meanwhile', async ($, on) => {
  const { m, fs, ui } = await runRig($, on, new Map([['bg:other', bgRun({ name: 'far' })]]))
  fs.files[`${BG_DIR}/exit.json`] = JSON.stringify({ code: 0, ended: '2026-11-23T10:00:00Z' })
  let release!: () => void
  fs.gate = new Promise<void>(r => (release = r))
  const first = m.clock.advance(30_000) // its watch blocks on the read of exit.json
  await new Promise(r => (globalThis as any).setTimeout(r, 50))
  await bgCall($, { name: 'late' }) // asks for a watch while the first is blocked
  release()
  await first
  await new Promise(r => (globalThis as any).setTimeout(r, 50))
  expect(m.toasts.filter(t => t.includes('far'))).toHaveLength(1)
  expect((await texts(ui)).filter((t: string) => t.includes('late'))).toHaveLength(1)
})

test('[x] while a watch is in flight: the removed row does not come back', async ($, on) => {
  const { m, fs, ui } = await runRig($, on, new Map([['bg:id', bgRun()]]), { [BG_STATUS]: 'x' })
  await m.clock.advance(30_000)
  let release!: () => void
  fs.gate = new Promise<void>(r => (release = r))
  const tick = m.clock.advance(30_000) // the watch blocks on the status file, holding the row
  await new Promise(r => (globalThis as any).setTimeout(r, 50))
  await ui.press({ key: 'bg-remove' })
  release()
  await tick
  await new Promise(r => (globalThis as any).setTimeout(r, 50))
  expect(await bgTexts(ui)).toEqual([])
})

test('session.start drops an empty or garbled bg record too', async ($, on) => {
  const { m } = await runRig($, on, new Map<string, unknown>([['bg:empty', {}], ['bg:bad', { started: 'x' }], ['bg:new', bgRun()]]))
  expect([...m.store.keys()].filter(k => k.startsWith('bg:'))).toEqual(['bg:new'])
})

test('session.start drops runs started over 7 days ago', async ($, on) => {
  const { m } = await runRig($, on, new Map([['bg:old', bgRun({ started: NOW - 8 * 86_400_000 })], ['bg:new', bgRun({ started: NOW - 6 * 86_400_000 })]]))
  expect([...m.store.keys()].filter(k => k.startsWith('bg:'))).toEqual(['bg:new'])
})

// background runs: what happens when one ends (task 5)
const WARM = NOW - 60_000
const COLD = NOW - 2 * 3_600_000
const ENDED = { code: 0, at: NOW - 60_000 }
const finRig = async ($: any, on: any, o: { at?: number | null; run?: object; draft?: string; limit?: () => unknown; gate?: Promise<unknown>; drop?: string; handoffError?: string } = {}) => {
  const log: string[] = []
  const state = box(on, o.draft ?? 'мой текст', log, o.gate)
  state.drop = o.drop
  const store = new Map<string, unknown>([['bg:id', bgRun({ ended: ENDED, ...o.run })], ['turn:id', { at: o.at === undefined ? WARM : o.at, tokens: 1000 }]])
  const r = await runRig($, on, store, { [BG_STATUS]: 'a\nepoch 3 done\n' }, o.limit, { handoffError: o.handoffError })
  return { ...r, log, state, store }
}
const bgSent = (log: string[]) => log.filter(l => l.startsWith('submit'))

test('a run that ended on a warm cache wakes the agent once and the record goes', async ($, on) => {
  const { m, log, store, state } = await finRig($, on)
  expect(bgSent(log)).toEqual([]) // session.start does not act: no band render has told isWorking yet
  await m.clock.advance(30_000)
  expect(bgSent(log)).toHaveLength(1)
  expect(state.asUser).toEqual([true])
  expect(bgSent(log)[0]).toContain('epoch 3 done')
  expect(bgSent(log)[0]).toContain(`${BG_DIR}/log.txt`)
  expect(store.has('bg:id')).toBe(false)
})

test('a wake that is dropped keeps the record and toasts once for two ticks', async ($, on) => {
  const { m, log, store } = await finRig($, on, { drop: 'no' })
  await m.clock.advance(60_000)
  expect(store.has('bg:id')).toBe(true)
  expect(bgSent(log).length).toBeGreaterThan(1)
  expect(m.toasts.filter(t => t.includes(T.bgFinishFailed('train', 'no')))).toHaveLength(1)
  expect(store.get('lastError')).toMatchObject({ where: 'bg', msg: 'no' })
})

test('a second tick while the wake-up submit hangs sends no second one', async ($, on) => {
  let release!: () => void
  const { m, log, store } = await finRig($, on, { gate: new Promise<void>(r => (release = r)) })
  await m.clock.advance(60_000)
  expect(bgSent(log)).toHaveLength(1)
  release() // the finish ends inside the test: a submit left hanging is refused later and its error note would outlive the test
  for (let i = 0; i < 100 && store.has('bg:id'); i++) await new Promise(r => (globalThis as any).setTimeout(r, 10))
  expect(store.has('bg:id')).toBe(false)
  expect(bgSent(log)).toHaveLength(1)
})

test('a cold cache with "show": nothing is sent, the row says it ended, the next message carries the note once', async ($, on) => {
  const { m, log, store, ui, state } = await finRig($, on, { at: COLD })
  const contexts = state.contexts
  await m.clock.advance(30_000)
  expect(bgSent(log)).toEqual([])
  expect(await bgTexts(ui)).toEqual([bgRow(bgRun({ ended: ENDED }) as BgRun, 'epoch 3 done', NOW + 30_000).text])
  await typed($, 'привет')
  const note = [START, T.bgNote('train', when(new Date(ENDED.at).toISOString(), NOW + 30_000), '0', `${BG_DIR}/log.txt`), END].join('\n')
  expect(contexts).toEqual([[note]])
  expect(store.has('bg:id')).toBe(false)
  await typed($, 'ещё')
  expect(contexts[1]).toEqual([])
})

test('a dropped owner message leaves the note for the next one', async ($, on) => {
  const { m, store, state } = await finRig($, on, { at: COLD })
  const contexts = state.contexts
  await m.clock.advance(30_000)
  state.drop = 'no'
  await typed($, 'drop')
  state.drop = undefined
  expect(store.has('bg:id')).toBe(true)
  await typed($, 'again')
  expect(contexts[1]).toEqual(contexts[0])
  expect(contexts[0]).toHaveLength(1)
  expect(store.has('bg:id')).toBe(false)
})

test('cold with new_worker: /clear and the finish text, the draft is not touched, the record goes after the launch', async ($, on) => {
  const { m, log, state, store } = await finRig($, on, { at: COLD, run: { ifCold: 'new_worker' } })
  state.nextId = 'new'
  await m.clock.advance(30_000)
  expect(log[0]).toBe('/clear')
  expect(bgSent(log)).toHaveLength(1)
  expect(bgSent(log)[0]).toContain('submit h\n\n')
  expect(bgSent(log)[0]).toContain('epoch 3 done')
  expect(state.text).toBe('мой текст')
  expect(store.has('bg:id')).toBe(false)
  expect(store.has('bg:new')).toBe(false)
})

test('cold new_worker that is not launched: the record is patched to show, one toast and one /al-health line per run', async ($, on) => {
  const { m, log, store } = await finRig($, on, { at: COLD, run: { ifCold: 'new_worker', dir: 'C:/bg/r2' }, handoffError: 'boom' })
  await m.clock.advance(60_000)
  expect(log).toEqual([])
  expect(store.get('bg:id')).toMatchObject({ name: 'train', ifCold: 'show', ended: ENDED })
  expect(m.toasts.filter(t => t.includes(T.bgFinishFailed('train', T.bgNotLaunched)))).toHaveLength(1)
  expect(store.get('lastError')).toMatchObject({ where: 'bg' })
})

test('cold new_worker whose submit drops after /clear: the run moves to the new chat as show', async ($, on) => {
  const { m, log, state, store } = await finRig($, on, { at: COLD, run: { ifCold: 'new_worker' }, drop: 'no' })
  state.nextId = 'new'
  await m.clock.advance(30_000)
  expect(store.has('bg:id')).toBe(false)
  expect(store.get('bg:new')).toMatchObject({ name: 'train', ifCold: 'show', ended: ENDED })
  await m.clock.advance(30_000) // the next tick in the new chat: show, no second launch
  expect(bgSent(log)).toHaveLength(1)
})

test('the note rides no command, no meta message and no message of ours', async ($, on) => {
  const { m, store, state } = await finRig($, on, { at: COLD })
  await m.clock.advance(30_000)
  await typed($, 'x', { command: 'foo' })
  await typed($, 'x', { isMeta: true })
  await typed($, 'x', { origin: { kind: 'plugin', name: 'alethite', asUser: true } })
  expect(state.contexts.flat()).toEqual([])
  expect(store.has('bg:id')).toBe(true)
})

test('exit.json without an end time: the run ends at the tick time, the note shows it', async ($, on) => {
  const { m, fs, store, state } = await finRig($, on, { at: COLD, run: { ended: undefined } })
  fs.files[`${BG_DIR}/exit.json`] = JSON.stringify({ code: 0 })
  await m.clock.advance(30_000)
  expect((store.get('bg:id') as { ended: object }).ended).toEqual({ code: 0, at: NOW + 30_000 })
  await typed($, 'привет')
  expect(state.contexts[0]).toEqual([[START, T.bgNote('train', when(new Date(NOW + 30_000).toISOString(), NOW + 30_000), '0', `${BG_DIR}/log.txt`), END].join('\n')])
  expect(store.has('bg:id')).toBe(false)
})

test('a spent limit with new_worker: the finish waits for the reset, then the worker starts with it', async ($, on) => {
  const { m, log, store } = await finRig($, on, { at: COLD, run: { ifCold: 'new_worker' }, limit: nearLimit(undefined, 100) })
  await m.clock.advance(30_000)
  expect(bgSent(log)).toEqual([])
  expect(store.get('delayed:id')).toMatchObject({ at: AT })
  const { text } = store.get('delayed:id') as { text: string }
  expect(text).toContain('epoch 3 done')
  expect(store.get('bg:id')).toMatchObject({ ifCold: 'show', ended: ENDED }) // the result stays until it has reached someone
  await m.clock.advance(AT - NOW)
  expect(log[0]).toBe('/clear')
  expect(bgSent(log)[0]).toBe(`submit h\n\n${text}`)
  expect(store.has('delayed:id')).toBe(false)
  expect(store.has('bg:id')).toBe(false) // the launch carried it
})

test('After reset of a finish never overwrites the wait the owner set', async ($, on) => {
  const { m, log, store } = await finRig($, on, { at: COLD, run: { ifCold: 'new_worker' }, limit: nearLimit(undefined, 100) })
  store.set('delayed:id', { at: AT + 86_400_000, model: 'opus' })
  await m.clock.advance(30_000)
  await m.clock.advance(30_000) // the next tick: show now, nothing launched, the owner's wait untouched
  expect(store.get('delayed:id')).toEqual({ at: AT + 86_400_000, model: 'opus' })
  expect(store.get('bg:id')).toMatchObject({ ifCold: 'show', ended: ENDED })
  expect(log).toEqual([])
})

test('Cancel of the finish wait leaves the show record, so the result still reaches the owner', async ($, on) => {
  const { m, store, ui } = await finRig($, on, { at: COLD, run: { ifCold: 'new_worker' }, limit: nearLimit(undefined, 100) })
  await m.clock.advance(30_000)
  expect(store.get('delayed:id')).toMatchObject({ at: AT })
  await ui.press({ key: 'cancel-delay' })
  await m.clock.advance(30_000) // the next tick does not bring the wait back: the record is show now
  expect(store.has('delayed:id')).toBe(false)
  expect(store.get('bg:id')).toMatchObject({ ifCold: 'show', ended: ENDED })
})

test('a manual New worker while the run goes: the handoff gets the note, the record moves to the new chat', async ($, on) => {
  const { m, ui, store, state } = await finRig($, on, { run: { ended: undefined } })
  state.nextId = 'new'
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'send' })
  const note = T.bgHandoffNote('train', when(new Date(NOW - 5 * 60_000).toISOString(), NOW), BG_STATUS, `${BG_DIR}/log.txt`)
  expect(ofCg(m, 'handoff')[0]!.argv).toContain(`--note=${note}`)
  expect(store.has('bg:id')).toBe(false)
  expect(store.get('bg:new')).toMatchObject({ name: 'train', pid: 7 })
})

test('a manual New worker with an ended run: the handoff gets the run\'s result note, the old record goes', async ($, on) => {
  const { m, ui, store, state } = await finRig($, on, { at: COLD })
  state.nextId = 'new'
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'send' })
  const note = T.bgNote('train', when(new Date(ENDED.at).toISOString(), NOW), '0', `${BG_DIR}/log.txt`)
  expect(ofCg(m, 'handoff')[0]!.argv).toContain(`--note=${note}`)
  expect(store.has('bg:id')).toBe(false)
  expect(store.has('bg:new')).toBe(false)
})

// cleanup after a New worker (task 6): only a launch that went through asks cg.py to start it
for (const key of ['send', 'pick']) {
  test(`New worker, ${key}: maintain-detached is asked once`, async ($, on) => {
    const { m, ui, state } = await finRig($, on, { at: COLD })
    state.nextId = 'new'
    await ui.press({ key: 'launch' })
    await ui.press({ key })
    expect(ofCg(m, 'maintain-detached')).toHaveLength(1)
  })
}

test('New worker with an empty draft: no cleanup', async ($, on) => {
  const { m, ui } = await finRig($, on, { draft: '' })
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'send' })
  expect(ofCg(m, 'maintain-detached')).toEqual([])
})

test('New worker whose handoff fails: no cleanup', async ($, on) => {
  const { m, ui } = await finRig($, on, { handoffError: 'boom' })
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'send' })
  expect(ofCg(m, 'maintain-detached')).toEqual([])
})

test('a failing maintain-detached: a note for /al-health, no toast, the launch is as before', async ($, on) => {
  const { m, ui, log, state, fs, store } = await finRig($, on, { at: COLD })
  fs.maintainError = 'no spawn'
  state.nextId = 'new'
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'send' })
  expect(bgSent(log)).toHaveLength(1)
  expect(store.get('lastError')).toMatchObject({ where: 'cleanup' })
  expect(m.toasts).toEqual([])
})

const RESET_SOON = new Date(NOW + 600_000).toISOString()

test('a spent-limit finish on a warm cache: the wake after the reset delivers once, the finish wait goes with it', async ($, on) => {
  const { m, log, store } = await finRig($, on, { at: WARM, run: { ifCold: 'new_worker' }, limit: nearLimit(RESET_SOON, 100) })
  await m.clock.advance(30_000)
  expect(store.get('delayed:id')).toMatchObject({ at: NOW + 900_000 })
  await m.clock.advance(11 * 60_000) // past the reset, the cache is still warm
  expect(bgSent(log)).toHaveLength(1)
  expect(store.has('bg:id')).toBe(false)
  expect(store.has('delayed:id')).toBe(false)
  await m.clock.advance(10 * 60_000)
  expect(log.filter(l => l === '/clear')).toEqual([])
  expect(bgSent(log)).toHaveLength(1)
})

test('the owner message that carries the note takes the finish wait with it', async ($, on) => {
  const { m, store } = await finRig($, on, { at: COLD, run: { ifCold: 'new_worker' }, limit: nearLimit(undefined, 100) })
  await m.clock.advance(30_000)
  expect(store.get('delayed:id')).toHaveProperty('text')
  await typed($, 'привет')
  expect(store.has('bg:id')).toBe(false)
  expect(store.has('delayed:id')).toBe(false)
})

test('the owner message that carries the note leaves an owner wait alone', async ($, on) => {
  const { m, store } = await finRig($, on, { at: COLD, run: { ifCold: 'new_worker' }, limit: nearLimit(undefined, 100) })
  store.set('delayed:id', { at: AT + 86_400_000, model: 'opus' })
  await m.clock.advance(30_000)
  await typed($, 'привет')
  expect(store.has('bg:id')).toBe(false)
  expect(store.get('delayed:id')).toEqual({ at: AT + 86_400_000, model: 'opus' })
})

test('a /clear that throws without a new id: one attempt, the record turns to show, one toast', async ($, on) => {
  const { m, log, state, store } = await finRig($, on, { at: COLD, run: { ifCold: 'new_worker' } })
  state.clearFail = true
  await m.clock.advance(90_000)
  expect(log.filter(l => l === '/clear')).toHaveLength(1)
  expect(store.get('bg:id')).toMatchObject({ ifCold: 'show', ended: ENDED })
  expect(m.toasts.filter(t => t.includes(T.bgFinishFailed('train', T.bgNotLaunched)))).toHaveLength(1)
  expect(store.get('lastError')).toMatchObject({ where: 'bg' })
})

test('a run that ends during the launch moves to the new chat as show, its result is not lost', async ($, on) => {
  const { m, ui, fs, state, store } = await finRig($, on, { run: { ended: undefined } })
  fs.onHandoff = () => void store.set('bg:id', { ...(store.get('bg:id') as object), ended: ENDED })
  state.nextId = 'new'
  await ui.press({ key: 'launch' })
  await ui.press({ key: 'send' })
  expect(store.has('bg:id')).toBe(false)
  expect(store.get('bg:new')).toMatchObject({ name: 'train', ifCold: 'show', ended: ENDED })
  expect(ofCg(m, 'handoff')).toHaveLength(1)
})

test('a finish wait that fires while the chat holds a different running run: the handoff still says it runs', async ($, on) => {
  const { m, state, store } = await finRig($, on, { at: COLD, run: { ended: undefined } })
  store.set('delayed:id', { at: NOW + 30_000, text: 'финиш' })
  state.nextId = 'new'
  await m.clock.advance(3 * 30_000)
  const note = ofCg(m, 'handoff')[0]!.argv.find((a: string) => a.startsWith('--note='))
  expect(note).toContain(BG_STATUS)
})

test('a launch in flight at the tick: the ended record is left alone, no failure toast', async ($, on) => {
  let release!: () => void
  const { m, ui, store } = await finRig($, on, { at: COLD, run: { ifCold: 'new_worker' }, gate: new Promise<void>(r => (release = r)) })
  await ui.press({ key: 'launch' })
  const sending = ui.press({ key: 'send' }) // its submit hangs on the gate: the launch is in flight
  await new Promise(r => (globalThis as any).setTimeout(r, 50))
  await m.clock.advance(30_000)
  expect(store.get('bg:id')).toMatchObject({ ifCold: 'new_worker', ended: ENDED })
  expect(m.toasts.filter(t => t.includes('train'))).toEqual([])
  release()
  await sending
})

// final review fixes
const working = async ($: any, ui: any) => (await ui.unmount(), await $.ui.mount({ ...(UI as object), props: { ...props, isWorking: true } } as never), await new Promise(r => (globalThis as any).setTimeout(r, 50)))

test('bgCheck: a record removed during bg-alive of an earlier one does not throw', async ($, on) => {
  const { m, fs } = await runRig($, on, new Map([['bg:id', bgRun()], ['bg:other', bgRun({ dir: 'C:/bg/r2' })]]))
  fs.alive = false
  fs.onAlive = () => { m.store.delete('bg:other') }
  await m.clock.advance(600_000)
  expect(m.store.has('lastError')).toBe(false)
})

test('[x] after a spent-limit finish takes the finish wait with it: no New worker after the reset', async ($, on) => {
  const { m, ui, log, store } = await finRig($, on, { at: COLD, run: { ifCold: 'new_worker' }, limit: nearLimit(undefined, 100) })
  await m.clock.advance(30_000)
  expect(store.get('delayed:id')).toHaveProperty('text')
  await ui.press({ key: 'bg-remove' })
  expect(store.has('bg:id')).toBe(false)
  expect(store.has('delayed:id')).toBe(false)
  await m.clock.advance(AT - NOW + 60_000)
  expect(log.filter(l => l === '/clear')).toEqual([])
})

test('a launch in flight at the tick: a warm finish does not wake the old chat, the next tick does once', async ($, on) => {
  let release!: () => void
  const { m, ui, log, state } = await finRig($, on, { gate: new Promise<void>(r => (release = r)) })
  state.drop = 'no' // the launch ends dropped in the same chat: the record stays
  await ui.press({ key: 'launch' })
  const sending = ui.press({ key: 'send' })
  await new Promise(r => (globalThis as any).setTimeout(r, 50))
  await m.clock.advance(30_000)
  expect(bgSent(log).filter(l => l.includes('epoch 3 done'))).toEqual([])
  release()
  await sending
  state.drop = undefined
  await m.clock.advance(30_000)
  expect(bgSent(log).filter(l => l.includes('epoch 3 done'))).toHaveLength(1)
})

test('a turn that starts during the handoff of a finish launch: no /clear, the record turns to show', async ($, on) => {
  const { m, log, fs, store, ui } = await finRig($, on, { at: COLD, run: { ifCold: 'new_worker' } })
  fs.onHandoff = () => working($, ui)
  await m.clock.advance(30_000)
  await new Promise(r => (globalThis as any).setTimeout(r, 300)) // the launch ends after the tick
  expect(log).toEqual([])
  expect(store.get('bg:id')).toMatchObject({ ifCold: 'show', ended: ENDED })
})

test('a turn that starts during the handoff of a due wait: no /clear, the wait is back', async ($, on) => {
  const log: string[] = []
  const store = dueSoon()
  box(on, 'продолжаем', log)
  let ui: any
  const m = await started($, on, a => (a[0] === 'handoff' ? working($, ui).then(() => ran(JSON.stringify({ text: 'h', path: 'C:/h/id.md' }))) : ran('{}')), [], nearLimit(), {}, noTurn, store)
  ui = await $.ui.mount(UI)
  await m.clock.advance(SOON - NOW)
  await new Promise(r => (globalThis as any).setTimeout(r, 300)) // the launch ends after the tick
  expect(log).toEqual([])
  expect(store.get('delayed:id')).toEqual({ at: SOON })
})

test('background_run: an ended run that nobody was told of is reported with the next start', async ($, on) => {
  const { m } = await runRig($, on, new Map<string, unknown>([['bg:id', bgRun({ ended: ENDED })], ['delayed:id', { at: AT, text: 'финиш' }]]))
  const r = await bgCall($)
  expect(r).toEqual({ result: `${T.bgStarted('train', `${BG_DIR}/log.txt`)}\n${T.bgNote('train', when(new Date(ENDED.at).toISOString(), NOW), '0', `${BG_DIR}/log.txt`)}` })
  expect(m.store.has('delayed:id')).toBe(false) // the finish wait would tell the same result again
  expect(m.store.get('bg:id')).toMatchObject({ pid: 7 })
})
