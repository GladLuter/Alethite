import { expect, mock, test } from 'claude-code/testing'
import { FOLDER, notePath, SUMMARY_PATH } from '../hooks/notes'
import { T } from '../hooks/text'

const usage = { context: { tokens: 512_000, window: 1_000_000 }, rateLimits: [{ kind: 'five_hour', percentUsed: 7 }] }
const turn = { answer: '', durationMs: 1, isAborted: false, turnId: 't', reason: 'answer', usage: { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, model: 'claude-opus-5-5' } }
const props = { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 80 }

const ran = (stdout: string) => ({ value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })
const noTurn = () => ran('{"tokens":null,"at":null,"model":null}')

// stands for the engine beneath the plugin; `statuses` collects what the status line was set to, `answerRun` answers cg.py
const wire = (on: any, statuses: (string | undefined)[], answerUsage: () => unknown, answerRun: (_: unknown, e: { argv: string[] }) => unknown = noTurn, store = new Map<string, unknown>(), chat = { model: 'claude-opus-5-5' }) => {
  const clock = mock.clock(on, { now: 10_000_000_000 })
  on('store.get', (_: unknown, e: { key: string }) => ({ value: store.get(e.key) }))
  on('store.set', (_: unknown, e: { key: string; value: unknown }) => (store.set(e.key, e.value), { value: undefined }))
  on('session.id', () => ({ value: 'id' }))
  on('session.model', () => ({ value: chat.model }))
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

test('no band while the model is working', async ($, on) => {
  wire(on, [], () => ({ value: usage }))
  await ($ as any).command.run({ command: 'cg-band', args: '' }) // forces the band
  expect(await buttons(await $.ui.mount({ plugin: 'cacheguard', surface: 'terminal', component: 'AbovePrompt', props } as never))).toEqual(['launch', 'stay'])
  expect(await buttons(await $.ui.mount({ plugin: 'cacheguard', surface: 'terminal', component: 'AbovePrompt', props: { ...props, isWorking: true } } as never))).toEqual([])
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
  on('session.root', () => ({ value: 'C:/w' }))
  await ($ as any).command.run({ command: 'cg-band', args: '' })
  await ($ as any).command.run({ command: 'cg-band', args: '' })
  // the engine's own reason follows (a timeout must not read like a missing command), worded by the engine
  const lead = `${T.pythonMissing('python')} (`
  expect(toasts).toHaveLength(1)
  expect(toasts[0]).toContain(T.failed('last-turn', lead))
  expect(toasts[0]!.endsWith('ENOENT)')).toBe(true)
  const { text } = await ($ as any).command.run({ command: 'cg-health', args: '' })
  expect(text).toContain('last-turn')
  expect(text).toContain(lead)
})

test('/cg-health: version, index figures, auto-memory folder with its fact count', async ($, on) => {
  const dir = 'C:/mem/memory'
  const files = [{ path: 'C:/p/CLAUDE.md', type: 'Project', tokens: 1 }, { path: `${dir}/MEMORY.md`, type: 'AutoMem', tokens: 1 }]
  const vectors = { model: 'embeddinggemma', with_vectors: 3000, total: 3400, updated: '2026-10-18T09:00:00Z' }
  const status = { db: 'C:/x/mem.db', convs: 12, rows: 3400, last_update: '2026-10-13T09:00:00', size_mb: 1.5, vectors, project: { key: 'c:/w', convs: 5 } }
  const argvs: string[][] = []
  wire(on, [], () => ({ value: { context: { window: 1_000_000, breakdown: { memoryFiles: files } }, rateLimits: [] } }), (_, e) => (argvs.push(e.argv), e.argv.includes('status') ? ran(JSON.stringify(status)) : noTurn()))
  on('session.root', () => ({ value: 'C:/w' }))
  on('fs.list', () => ({ value: ['MEMORY.md', 'a.md', 'b.md', 'notes.txt'].map(name => ({ name, kind: 'file', size: 1, mtimeMs: 0, isLink: false })) }))
  const { text } = await ($ as any).command.run({ command: 'cg-health', args: '' })
  expect(argvs.find(a => a.includes('status'))!.slice(2)).toEqual(['status', '--cwd=C:/w']) // the root, the same folder that recall scopes by
  expect(text).toContain(T.projectLine('C:/w', 5))
  expect(text).not.toContain(T.projectEmpty)
  expect(text).toContain('2.1.295')
  expect(text).toContain('3400')
  expect(text).toContain(T.healthMemory(dir, 2))
  expect(text).toContain(T.vectorsLine('embeddinggemma', 3000, 3400, '2026-10-18T09:00:00Z'))
  expect(text).toMatch(/python ".*cg\.py" embed --model embeddinggemma --max-seconds 100000/) // 400 records have no vectors: how to finish them
})

test('/cg-health: a project that matches no conversation is said so', async ($, on) => {
  const status = { db: 'C:/x/mem.db', convs: 12, rows: 3400, last_update: null, size_mb: 1.5, project: { key: 'c:/w', convs: 0 } }
  wire(on, [], () => ({ value: { context: { window: 1_000_000 }, rateLimits: [] } }), (_, e) => (e.argv.includes('status') ? ran(JSON.stringify(status)) : noTurn()))
  on('session.root', () => ({ value: 'C:/w' }))
  const { text } = await ($ as any).command.run({ command: 'cg-health', args: '' })
  expect(text).toContain(T.projectLine('C:/w', 0))
  expect(text).toContain(T.projectEmpty)
})

// What session.model() answers; /model changes it unless `isSticky` (the command printed an error and left the model alone).
const newChat = () => ({ model: 'claude-opus-5-5', isSticky: false })

// The engine beneath a launch: the prompt box (`refuse` makes a fill fail), /clear and /model, and the first message. `gate` holds the submit until released.
const box = (on: any, draft: string, log: string[], chat: ReturnType<typeof newChat>, gate: Promise<unknown> = Promise.resolve()) => {
  const state = { text: draft, refuse: undefined as string | undefined }
  on('prompt.read', () => ({ value: { text: state.text, cursor: 0 } }))
  on('prompt.fill', (_: unknown, e: { text: string }) => (state.refuse ? { isFilled: false, refusal: state.refuse } : ((state.text = e.text), { isFilled: true })))
  on('prompt.submit', async (_: unknown, e: { text: string }) => (log.push(`submit ${e.text}`), await gate, { text: e.text }))
  on('command.run', (_: unknown, e: { command: string; args: string }) => {
    log.push(`/${e.command} ${e.args}`.trim())
    if (e.command === 'model' && !chat.isSticky) chat.model = `claude-${e.args}-5-5`
    return { text: '' }
  })
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
  const chat = newChat()
  wire(on, [], () => withMemory, (_, e) => (e.argv.includes('handoff') && argvs.push(e.argv), handoffOk(_, e)), undefined, chat)
  const state = box(on, 'продолжаем', log, chat)
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
  const chat = newChat()
  wire(on, [], () => ({ value: usage }), handoffOk, undefined, chat)
  box(on, 'x', log, chat, new Promise<void>(r => (release = r)))
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
  box(on, 'x', log, newChat())
  const ui = await mountBand($)

  await ui.press({ key: 'launch' })
  await ui.press({ key: 'model:sonnet' })

  expect(log).toEqual([])
  expect(toasts).toEqual([T.handoffFailed('no transcript')])
  expect(store.get('lastError')).toMatchObject({ where: 'launch', msg: T.handoffFailed('no transcript') })
  expect(await buttons(ui)).toEqual(['launch', 'stay'])
  expect(await ui.findAll({ type: 'Button', text: T.newWorker })).toHaveLength(1) // busy ended
})

test('new worker: /model that leaves the model unchanged stops the launch, handoff and draft are back in the box', async ($, on) => {
  const toasts: string[] = []
  const log: string[] = []
  const chat = { ...newChat(), isSticky: true }
  on('ui.toast', (_: unknown, e: { text: string }) => (toasts.push(e.text), { value: undefined }))
  wire(on, [], () => ({ value: usage }), handoffOk, undefined, chat)
  const state = box(on, 'x', log, chat)
  const ui = await mountBand($)

  await ui.press({ key: 'launch' })
  await ui.press({ key: 'model:sonnet' })

  expect(log).toEqual(['/clear', '/model sonnet']) // no submit
  expect(state.text).toBe('[cg-handoff]x[/cg-handoff]\n\nx')
  expect(toasts).toEqual([T.modelFailed('sonnet', T.modelStill('claude-opus-5-5'), 'C:/h/id.md')])
  expect(await buttons(ui)).toEqual(['launch', 'stay'])
})

test('new worker: the box refuses the text: readable reason and the saved path, not a raw code', async ($, on) => {
  const toasts: string[] = []
  const chat = { ...newChat(), isSticky: true }
  on('ui.toast', (_: unknown, e: { text: string }) => (toasts.push(e.text), { value: undefined }))
  wire(on, [], () => ({ value: usage }), handoffOk, undefined, chat)
  box(on, 'x', [], chat).refuse = 'dialog' // the engine strips a code a hook writes itself, so the closure sees the code-less refusal here
  const ui = await mountBand($)

  await ui.press({ key: 'launch' })
  await ui.press({ key: 'model:sonnet' })

  expect(toasts).toEqual([T.fillFailed(T.modelStill('claude-opus-5-5'), T.fillRefused.other, 'C:/h/id.md')])
})

// ---- long memory ----
const hit = { conv: 'S', parent: '', ts: '2026-10-10T08:00:00Z', kind: 'owner', pos: 42, snippet: '«кэш» истёк' }
const hitLine = '[2026-10-10 owner conv=S pos=42] «кэш» истёк'
const bad = { path: 'C:/p/a.jsonl', error: 'PermissionError: x' }
const found = (extra: object = {}) => ran(JSON.stringify({ hits: [hit], errors: [], vector: 'ok', ...extra }))
const failed = (error: string) => ({ value: { exitCode: 1, stdout: JSON.stringify({ error }), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })

// A session that has started: cg.py answers `answer(argv)` and every run is recorded in `runs`; the tools and the index timer exist.
const started = async ($: any, on: any, answer: (a: string[]) => unknown, statuses: (string | undefined)[] = []) => {
  const m = { runs: [] as { argv: string[]; timeoutMs?: number }[], toasts: [] as string[], specs: [] as any[], store: new Map<string, unknown>(), clock: undefined as any }
  on('ui.toast', (_: unknown, e: { text: string }) => (m.toasts.push(e.text), { value: undefined }))
  on('tool.register', (_: unknown, e: unknown) => (m.specs.push(e), { value: { tool: 'x' } }))
  on('command.register', () => ({ value: { command: 'x' } }))
  on('session.root', () => ({ value: 'C:/w' }))
  on('session.cwd', () => ({ value: 'C:/w/plugin/scripts' })) // after a shell `cd`: must not reach the search
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  m.clock = wire(on, statuses, () => ({ value: usage }), (_, e: any) => (m.runs.push({ argv: e.argv.slice(2), timeoutMs: e.init?.timeoutMs }), e.argv.includes('last-turn') ? noTurn() : answer(e.argv.slice(2))), m.store)
  await $.session.start({ cwd: 'C:/w', surface: null, isInteractive: false })
  return m
}
const recall = ($: any, input: object = { query: 'кэш' }) => $.tool.call({ tool: 'mcp__cacheguard__recall', ...input })
const ofCg = (m: { runs: { argv: string[] }[] }, cmd: string) => m.runs.filter(r => r.argv[0] === cmd)

test('memory tools: both in the prompt, not behind ToolSearch', async ($, on) => {
  const m = await started($, on, () => ran('{}'))
  expect(m.specs.map(s => [s.name, s.isDeferred, s.description])).toEqual([['recall', false, T.recallDescription], ['recall_more', false, T.recallMoreDescription]])
})

test('recall: project by default, scope=all and the limit when asked; the model reads the hits', async ($, on) => {
  const m = await started($, on, () => found())
  expect(await recall($)).toEqual({ result: hitLine })
  await recall($, { query: 'q', scope: 'all', limit: 3 })
  const model = ['--model', 'embeddinggemma']
  expect(ofCg(m, 'recall').map(r => r.argv)).toEqual([['recall', '--query=кэш', '--cwd', 'C:/w', '--limit', '8', ...model], ['recall', '--query=q', '--all', '--limit', '3', ...model]])
  expect(m.toasts).toEqual([])
})

test('recall: nothing found says what to try', async ($, on) => {
  await started($, on, () => ran('{"hits":[],"errors":[],"vector":"ok"}'))
  expect(await recall($)).toEqual({ result: T.noHits('кэш', 'project') })
})

test('recall: cg.py fails: the model reads an error, the owner a toast, /cg-health the cause', async ($, on) => {
  const m = await started($, on, () => failed('OperationalError: x'))
  expect(await recall($)).toEqual({ deny: T.recallFailed('OperationalError: x') })
  expect(m.toasts).toEqual([T.failed('recall', 'OperationalError: x')])
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
  expect(m.toasts).toEqual([T.indexErrors(2)])
  expect(m.store.get('lastError')).toMatchObject({ where: 'recall', msg: T.indexErrorFirst(2, bad.path, bad.error) })
})

test('recall: no vector search: the model reads why, the owner gets one toast per load, /cg-health keeps the cause', async ($, on) => {
  const why = 'Ollama не отвечает: тест'
  const m = await started($, on, () => found({ vector: why }))
  const told = { result: [hitLine, T.vectorOff(why)].join('\n') }
  expect(await recall($)).toEqual(told)
  expect(await recall($)).toEqual(told)
  expect(m.toasts).toEqual([T.vectorOff(why)])
  expect(m.store.get('lastError')).toMatchObject({ where: 'recall', msg: T.vectorOff(why) })
})

test('recall_more: reads around a hit', async ($, on) => {
  const m = await started($, on, () => ran(JSON.stringify({ rows: [{ ts: '2026-10-10T08:00:00Z', kind: 'owner', pos: 42, text: 'привет' }] })))
  expect(await $.tool.call({ tool: 'mcp__cacheguard__recall_more', conv: 'S', pos: 42 })).toEqual({ result: '[2026-10-10 owner pos=42] привет' })
  expect(ofCg(m, 'expand')[0]!.argv).toEqual(['expand', '--conv=S', '--pos=42'])
})

test('recall_more: a failure is an error for the model and a toast for the owner', async ($, on) => {
  const m = await started($, on, () => failed('boom'))
  expect(await $.tool.call({ tool: 'mcp__cacheguard__recall_more', conv: 'S', pos: 42 })).toEqual({ deny: T.recallFailed('boom') })
  expect(m.toasts).toEqual([T.failed('recall_more', 'boom')])
})

const indexed = (errors: unknown[] = []) => ran(JSON.stringify({ files: 1, rows: 5, convs: 1, seconds: 0.5, errors }))
const embedded = (errors: unknown[] = []) => ran(JSON.stringify({ done: 5, pending: 0, seconds: 1.5, model: 'embeddinggemma', errors }))

test('index: 3 s after session.start, in the background, with errors on the status line', async ($, on) => {
  const statuses: (string | undefined)[] = []
  const m = await started($, on, a => (a[0] === 'embed' ? embedded() : indexed([bad])), statuses)
  expect(ofCg(m, 'index')).toEqual([]) // session.start did not wait for it, nor run it
  await m.clock.advance(3_000)
  expect(ofCg(m, 'index')).toEqual([{ argv: ['index'], timeoutMs: 600_000 }])
  expect(statuses).toContain(T.indexErrors(1))
  expect(m.toasts).toEqual([])
  expect(m.store.get('lastError')).toMatchObject({ where: 'index' })
})

test('index: a failed background run goes to the status line, /cg-health and one toast', async ($, on) => {
  const statuses: (string | undefined)[] = []
  const m = await started($, on, () => failed('database is locked'), statuses)
  await m.clock.advance(3_000)
  expect(statuses).toContain(T.statusError('database is locked'))
  expect(m.toasts).toEqual([T.catchUpFailed('index')])
  expect(m.store.get('lastError')).toMatchObject({ where: 'index', msg: 'database is locked' })
  expect(ofCg(m, 'embed')).toEqual([]) // the same database would fail the same way, and the first cause must stay in /cg-health
})

test('embed: right after the index in the same timer, 120 s of work at most, and the setting names the model', { options: { embedModel: 'bge-m3' } }, async ($, on) => {
  const m = await started($, on, a => (a[0] === 'embed' ? embedded() : a[0] === 'recall' ? found() : indexed()))
  await m.clock.advance(3_000)
  expect(m.runs.filter(r => r.argv[0] === 'index' || r.argv[0] === 'embed')).toEqual([
    { argv: ['index'], timeoutMs: 600_000 },
    { argv: ['embed', '--model', 'bge-m3', '--max-seconds', '120'], timeoutMs: 180_000 },
  ])
  await recall($)
  expect(ofCg(m, 'recall')[0]!.argv.slice(-2)).toEqual(['--model', 'bge-m3'])
  expect(m.toasts).toEqual([])
})

// the embed run answers `answer`; the owner must see `msg` on the status line and in /cg-health, and hear of it in one toast
const embedGoesWrong = async ($: any, on: any, answer: unknown, msg: string) => {
  const statuses: (string | undefined)[] = []
  const m = await started($, on, a => (a[0] === 'embed' ? answer : indexed()), statuses)
  await m.clock.advance(3_000)
  expect(statuses).toContain(T.statusError(msg))
  expect(m.store.get('lastError')).toMatchObject({ where: 'embed', msg })
  expect(m.toasts).toEqual([T.catchUpFailed('embed')])
}

test('embed: a failed run reaches the status line, /cg-health and a toast', ($, on) => embedGoesWrong($, on, failed('Ollama не отвечает: x'), 'Ollama не отвечает: x'))
test('embed: errors in a finished run do too', ($, on) => embedGoesWrong($, on, embedded([{ error: 'RuntimeError: модель m не найдена' }]), 'RuntimeError: модель m не найдена'))

test('catch-up: the toast comes once per load of the mod, every failure is kept for /cg-health', async ($, on) => {
  const m = await started($, on, a => (a[0] === 'embed' ? failed('Ollama не отвечает: x') : indexed()))
  await m.clock.advance(3_000)
  expect(m.toasts).toEqual([T.catchUpFailed('embed')])
  expect(m.store.get('lastError')).toMatchObject({ where: 'embed', msg: 'Ollama не отвечает: x' })
  m.store.delete('lastError')
  await $.session.start({ cwd: 'C:/w', surface: null, isInteractive: false }) // a second catch-up in the same load
  await m.clock.advance(3_000)
  expect(m.toasts).toEqual([T.catchUpFailed('embed')])
  expect(m.store.get('lastError')).toMatchObject({ where: 'embed', msg: 'Ollama не отвечает: x' })
})

// item 2: the engine refuses a tool schema
test('session.start: a refused registration leaves the status line, the timers and the other registrations alone', async ($, on) => {
  const statuses: (string | undefined)[] = []
  const store = new Map<string, unknown>()
  const names = { commands: [] as string[], tools: [] as string[] }
  on('command.register', (_: unknown, e: { name: string }) => (names.commands.push(e.name), { value: { command: e.name } }))
  on('tool.register', (_: unknown, e: { name: string }) => (names.tools.push(e.name), { deny: 'schema refused' }))
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  const clock = wire(on, statuses, () => ({ value: usage }), (_, e) => (e.argv.includes('index') ? indexed() : e.argv.includes('embed') ? embedded() : noTurn()), store)
  await $.session.start({ cwd: 'C:/w', surface: null, isInteractive: false })
  expect(statuses[0]).toMatch(/^ctx /) // the first refresh ran
  expect(statuses.at(-1)).toMatch(/^CacheGuard: ошибка — .*schema refused/) // ...and the refusal is the last word
  expect(store.get('lastError')).toMatchObject({ where: 'register', msg: expect.stringContaining('schema refused') })
  expect(names).toEqual({ commands: ['cg-band', 'cg-health', 'cg-index', 'cg-stats'], tools: ['recall', 'recall_more'] }) // both tools were tried, the commands went through
  await clock.advance(30_000) // the timers run
  expect(statuses.at(-1)).toMatch(/^ctx /)
})

test('/cg-index: updates, "rebuild" re-reads everything, the answer has the figures', async ($, on) => {
  const stats = { files: 3, rows: 120, convs: 7, seconds: 1.234, errors: [] }
  const m = await started($, on, () => ran(JSON.stringify(stats)))
  expect((await $.command.run({ command: 'cg-index', args: '' })).text).toBe(T.cgIndexDone(stats))
  await $.command.run({ command: 'cg-index', args: 'rebuild' })
  expect(ofCg(m, 'index').map(r => r.argv)).toEqual([['index'], ['index', '--rebuild']])
})

test('/cg-index: unreadable transcripts and failures are shown in a toast', async ($, on) => {
  let answer: unknown = ran(JSON.stringify({ files: 0, rows: 0, convs: 0, seconds: 0, errors: [bad] }))
  const m = await started($, on, () => answer)
  await $.command.run({ command: 'cg-index', args: '' })
  expect(m.toasts).toEqual([T.indexErrors(1)])
  answer = failed('no such table')
  expect((await $.command.run({ command: 'cg-index', args: '' })).text).toBe(T.cgIndexFailed)
  expect(m.toasts[1]).toBe(T.failed('cg-index', 'no such table'))
})

// ---- short memory ----
const facts = { model: 'claude-opus-5-5', promptModel: 'claude-opus-5-5', surfaces: [], tools: [], outputStyle: null, traits: [] }

test('prompt: the memory rule is the last section, on the session side, the same text every time', async ($, on) => {
  const engine = [{ id: 'intro', text: 'a', scope: 'shared' }, { id: 'env', text: 'b', scope: 'session' }]
  on('prompt.compose', () => ({ sections: engine }))
  const first = (await ($ as any).prompt.compose(facts)).sections
  expect(first).toEqual([...engine, { id: 'cacheguard:memory-rule', text: T.memoryRule, scope: 'session' }])
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
  expect(r.toasts).toEqual([T.remembered('feedback_cache'), T.remembered('feedback_cache')])
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

test('remembered: a throw while judging the write does not break it; the cause goes to the status line and /cg-health', async ($, on) => {
  const statuses: (string | undefined)[] = []
  const store = new Map<string, unknown>()
  const r = rig($, on)
  wire(on, statuses, () => ({ value: usage }), undefined, store)
  // a path that is no string still looks like a fact to the regex (it is coerced) and then has no .split
  expect(await r.write([fact] as unknown as string)).toEqual(done)
  expect(statuses.some(t => t?.startsWith('CacheGuard: ошибка — '))).toBe(true)
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
  mock.env(on, env)
  const vault = fakeVault(on)
  const m = await started($, on, a => (a[0] === 'stats' ? answer(a) : ran('{}')))
  const stats = (args = '') => $.command.run({ command: 'cg-stats', args }) as Promise<{ text: string }>
  const today = () => flag(ofCg(m, 'stats').at(-1)!.argv, '--today')
  return { vault, m, stats, today }
}

test('/cg-stats: cg.py gets the period, the computer, the limits and the size of the memory rule; the figures are in the reply', async ($, on) => {
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

test('/cg-stats 30: the period is as long as asked', async ($, on) => {
  const { m, stats, today } = await statsRig($, on)
  await stats(' 30 ')
  expect(Date.parse(today()) - Date.parse(flag(ofCg(m, 'stats')[0]!.argv, '--since'))).toBe(30 * 86_400_000)
})

test('/cg-stats: no sense in the days: say so, run nothing', async ($, on) => {
  const { m, stats } = await statsRig($, on)
  for (const bad of ['abc', '0', '-3', '2.5']) expect((await stats(bad)).text).toBe(T.cgStatsDays)
  expect(ofCg(m, 'stats')).toEqual([])
})

test('/cg-stats: Obsidian up: the note and the summary are written, the outbox is empty', async ($, on) => {
  const { vault, m, stats, today } = await statsRig($, on)
  vault.files.set(`${FOLDER}/2026-10-01 PC-2.md`, OTHER)
  const { text } = await stats()
  expect(vault.files.get(notePath('PC-1', today()))).toBe(NOTE)
  const summary = vault.files.get(SUMMARY_PATH)!
  expect(summary).toContain('PC-1')
  expect(summary).toContain('PC-2')
  expect(summary.split('\n').filter(l => /^\| 2026/.test(l))).toHaveLength(2) // the summary does not list itself
  expect(vault.calls[0]).toBe(`obsidian create_vault_file ${notePath('PC-1', today())}`)
  expect(m.store.get('outbox')).toEqual([])
  expect(m.toasts).toEqual([])
  expect(text).toContain(T.cgStatsSent(1))
})

test('/cg-stats: Obsidian down: the error is shown, the note waits in the outbox, the next run sends it', async ($, on) => {
  const { vault, m, stats, today } = await statsRig($, on)
  vault.fail = () => true
  const { text } = await stats()
  expect(m.toasts).toHaveLength(1)
  expect(m.toasts[0]).toContain(T.failed('cg-stats', '').trim())
  expect(m.store.get('lastError')).toMatchObject({ where: 'cg-stats' })
  expect(m.store.get('outbox')).toEqual([{ path: notePath('PC-1', today()), content: NOTE }])
  expect(text).toContain(T.cgStatsQueued(1))
  expect(text).toContain('| метрика | база | сейчас |') // the figures are not lost with the vault
  expect(vault.files.size).toBe(0)

  vault.fail = () => false
  expect((await stats()).text).toContain(T.cgStatsSent(1)) // the same day: the new note replaces the queued one, not doubles it
  expect(vault.files.get(notePath('PC-1', today()))).toBe(NOTE)
  expect(m.store.get('outbox')).toEqual([])
})

test("/cg-stats: an answer with isError is a failure too, with the server's words", async ($, on) => {
  const { vault, m, stats } = await statsRig($, on)
  vault.fail = () => true
  vault.failure = 'isError'
  await stats()
  expect(m.toasts[0]).toContain('vault locked')
  expect((m.store.get('outbox') as unknown[]).length).toBe(1)
})

test('/cg-stats: what waited from an earlier day goes first', async ($, on) => {
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

test('/cg-stats: the vault dies after the first of two: only the rest stays queued', async ($, on) => {
  const { vault, m, stats, today } = await statsRig($, on)
  const old = { path: notePath('PC-1', '2026-01-01'), content: 'old' }
  await m.store.set('outbox', [old])
  vault.fail = () => vault.files.size === 1
  const { text } = await stats()
  expect(vault.files.has(old.path)).toBe(true)
  expect(m.store.get('outbox')).toEqual([{ path: notePath('PC-1', today()), content: NOTE }])
  expect(text).toContain(T.cgStatsQueued(1))
})

test('/cg-stats: the summary cannot be built: the note is safe, the owner is told', async ($, on) => {
  const { vault, m, stats, today } = await statsRig($, on)
  vault.fail = tool => tool === 'list_vault_files'
  const { text } = await stats()
  expect(vault.files.get(notePath('PC-1', today()))).toBe(NOTE)
  expect(m.store.get('outbox')).toEqual([])
  expect(m.toasts[0]).toContain('cg-stats-summary')
  expect(m.store.get('lastError')).toMatchObject({ where: 'cg-stats-summary' })
  expect(text).toContain(T.cgStatsSummaryFailed)
})

test('/cg-stats: the folder listing is truncated: the note is written, the summary is not rebuilt, the owner is told', async ($, on) => {
  const { vault, m, stats, today } = await statsRig($, on)
  for (let i = 0; i < 1001; i++) vault.files.set(`${FOLDER}/old-${i}.md`, OTHER)
  const { text } = await stats()
  expect(vault.listLimit).toBe(1000)
  expect(vault.files.get(notePath('PC-1', today()))).toBe(NOTE)
  expect(vault.files.has(SUMMARY_PATH)).toBe(false)
  expect(m.toasts[0]).toContain(T.summaryTruncated)
  expect(m.store.get('lastError')).toMatchObject({ where: 'cg-stats-summary' })
  expect(text).toContain(T.cgStatsSummaryFailed)
})

test('/cg-stats: the summary rebuild skips the summary itself, a non-.md file and a note in a subfolder', async ($, on) => {
  const { vault, stats, today } = await statsRig($, on)
  vault.files.set(SUMMARY_PATH, 'old summary')
  vault.files.set(`${FOLDER}/2026-10-01 PC-2.md`, OTHER)
  vault.files.set(`${FOLDER}/chart.png`, 'x')
  vault.files.set(`${FOLDER}/old/2026-01-01 PC-3.md`, OTHER)
  await stats()
  const read = vault.calls.filter(c => c.startsWith('obsidian get_note_properties')).sort()
  expect(read).toEqual([`obsidian get_note_properties ${FOLDER}/2026-10-01 PC-2.md`, `obsidian get_note_properties ${notePath('PC-1', today())}`].sort())
  expect(vault.files.get(SUMMARY_PATH)).toContain('PC-2')
})

test('/cg-stats: cg.py reports what made the figures wrong: a toast, /cg-health, a word in the reply; the note is written all the same', async ($, on) => {
  const errors = [{ path: 'audit', error: 'files_unreadable: 2' }]
  const { vault, m, stats, today } = await statsRig($, on, () => ran(JSON.stringify({ metrics: {}, note: NOTE, errors })))
  const { text } = await stats()
  expect(m.toasts).toEqual([T.indexErrors(1)])
  expect(m.store.get('lastError')).toMatchObject({ where: 'cg-stats', msg: T.indexErrorFirst(1, 'audit', 'files_unreadable: 2') })
  expect(vault.files.get(notePath('PC-1', today()))).toBe(NOTE)
  expect(text).toContain(T.cgStatsErrors(1))
  expect(text).toContain(T.cgStatsSent(1))
})

test('/cg-stats: cg.py fails: a toast, nothing is queued or sent', async ($, on) => {
  const { vault, m, stats } = await statsRig($, on, () => failed('PermissionError: x'))
  expect((await stats()).text).toBe(T.cgStatsFailed)
  expect(m.toasts).toEqual([T.failed('cg-stats', 'PermissionError: x')])
  expect(m.store.get('outbox')).toBeUndefined()
  expect(vault.calls).toEqual([])
})

test('/cg-stats: no computer name, no note', async ($, on) => {
  const { vault, m, stats } = await statsRig($, on, undefined, {})
  expect((await stats()).text).toBe(T.cgStatsFailed)
  expect(m.toasts).toEqual([T.failed('cg-stats', T.noHost)])
  expect(vault.calls).toEqual([])
})
