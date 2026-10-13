import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Snap } from '../types'
import { bandLines, isStale, modelChoices, statusText } from './guard'
import { healthLines, message, parseCg } from './py'
import type { LastError } from './py'
import { formatHits, formatRows, recallArgs } from './recall'
import type { Hit, IndexError, Row } from './recall'
import { T } from './text'
import { launch } from './worker'
import type { LaunchIO } from './worker'

const snap = atom({ plugin: 'cacheguard', key: 'snap' } as const, null)
const hiddenId = atom({ plugin: 'cacheguard', key: 'hiddenId' } as const, '')
const forcedId = atom({ plugin: 'cacheguard', key: 'forcedId' } as const, '')
const isArmed = atom({ plugin: 'cacheguard', key: 'isArmed' } as const, false)
const isBusy = atom({ plugin: 'cacheguard', key: 'isBusy' } as const, false)

// The helpers below take `$` and so stay in this file: the loader follows `$` only into functions declared here, never across an import.

const runCg = async ($: EngineInterface, python: string, args: string[], timeoutMs = 120_000) => {
  // run() rejects only when the child cannot start or times out
  const r = await $.process.run([python, `${$.plugin.root}/scripts/cg.py`, ...args], { timeoutMs }).catch(() => {
    throw new Error(T.pythonMissing(python))
  })
  return parseCg(r)
}

// What /cg-health shows as the last error; every path that reports a failure leaves it here.
const noteError = async ($: EngineInterface, where: string, err: unknown) => $.store.set('lastError', { at: await $.clock.now(), where, msg: message(err) })

// An owner-started action failed: tell them now and keep it for /cg-health.
const fail = async ($: EngineInterface, where: string, err: unknown) => {
  $.ui.toast(T.failed(where, message(err)))
  await noteError($, where, err)
}

// cg.py skips transcripts it cannot read and retries them next time, but the owner must hear of it: on the status line when nobody asked, in a toast when they did.
const indexErrors = async ($: EngineInterface, where: string, errors: IndexError[], isAsked: boolean) => {
  if (errors.length === 0) return
  const text = T.indexErrors(errors.length)
  if (isAsked) $.ui.toast(text)
  else $.ui.status(text)
  await noteError($, where, T.indexErrorFirst(errors.length, errors[0]!.path, errors[0]!.error))
}

// A memory tool call: its text for the model, or a refusal the model reads as an error, and the owner sees the failure too.
const served = async ($: EngineInterface, where: string, work: () => Promise<string>) => {
  try {
    return { result: await work() }
  } catch (err) {
    await fail($, where, err)
    return { deny: T.recallFailed(message(err)) }
  }
}

// The auto-memory folder is the one the first loaded AutoMem file sits in; none loaded, none known.
const memoryDir = async ($: EngineInterface) => {
  const files = (await $.session.usage({ breakdown: 'summary' })).context.breakdown?.memoryFiles
  return files?.find(f => f.type === 'AutoMem')?.path.replace(/[\\/][^\\/]*$/, '') ?? null
}

// The periodic and background paths: the error goes to the status line (a toast every 30 s would be spam) and is kept for /cg-health.
const guarded = async ($: EngineInterface, where: string, work: () => Promise<unknown>) => {
  try {
    await work()
    return true
  } catch (err) {
    $.ui.status(T.statusError(message(err)))
    await noteError($, where, err)
    return false
  }
}

// chats whose size was already asked of cg.py in this load, so a failure is not retried on every tick
const asked = new Set<string>()

// Measures the session once; the status line and the band both draw from the result.
const refresh = async ($: EngineInterface, python: string) => {
  const [id, model, usage, now] = await Promise.all([$.session.id(), $.session.model(), $.session.usage(), $.clock.now()])
  let rec = (await $.store.get(`turn:${id}`)) as { at: number | null; tokens: number | null } | undefined
  if (rec === undefined && !asked.has(id)) {
    asked.add(id)
    try {
      const last = await runCg($, python, ['last-turn', '--session', id], 30_000)
      rec = { at: Date.parse(last.at as string) || null, tokens: last.tokens as number | null }
      await $.store.set(`turn:${id}`, rec)
    } catch (err) {
      await fail($, 'last-turn', err)
    }
  }
  const fresh: Snap = {
    id,
    model,
    now,
    // a resumed chat has no live token count until its first reply, so fall back to the last one stored
    tokens: usage.context.tokens ?? rec?.tokens ?? null,
    at: rec?.at ?? null,
    limits: usage.rateLimits.map(({ kind, percentUsed }) => ({ kind, percentUsed })),
  }
  $.ui.status(statusText(fresh.tokens, fresh.at, now))
  await update($, snap, () => fresh)
}

// "Новый работник": binds launch() to the engine through closures made here. One launch at a time.
const startWorker = async ($: EngineInterface, python: string, model: string | null) => {
  let wasBusy = false
  await update($, isBusy, busy => ((wasBusy = busy), true)) // atomic: a second press before the redraw is refused here
  if (wasBusy) return
  try {
    await update($, isArmed, () => false)
    let told = '' // the last thing launch told the owner; it is the reason when the launch did not go through
    const io: LaunchIO = {
      draft: async () => (await $.prompt.read()).text,
      handoff: async () => {
        const [id, now, dir] = await Promise.all([$.session.id(), $.clock.now(), memoryDir($)])
        const today = new Date(now - new Date(now).getTimezoneOffset() * 60_000).toISOString().slice(0, 10) // the owner's calendar day
        return (await runCg($, python, ['handoff', '--session', id, '--today', today, ...(dir ? ['--memory-dir', dir] : [])])) as { text: string; path: string }
      },
      clear: () => $.command.run({ command: 'clear' }),
      setModel: async name => {
        await $.command.run({ command: 'model', args: name })
        // the command may answer with an error line instead of rejecting, so look at the model itself; a wrong alarm only costs an Enter
        const now = await $.session.model()
        if (!now.toLowerCase().includes(name)) throw new Error(T.modelStill(now))
      },
      submit: text => $.prompt.submit({ text, asUser: true }),
      fill: async text => {
        const r = await $.prompt.fill({ text })
        if (!r.isFilled) throw new Error(T.fillRefused[r.refusal ?? 'other'])
      },
      // the draft stays in the box after submit; clear it unless the owner has typed something new meanwhile
      emptyBox: async draft => {
        if ((await $.prompt.read()).text === draft) await $.prompt.fill({ text: '' })
      },
      toast: text => {
        $.ui.toast(text)
        told = text
      },
    }
    const outcome = await launch(io, model)
    if (outcome === 'failed' || outcome === 'dropped') await noteError($, 'launch', told)
  } catch (err) {
    await fail($, 'launch', err)
  } finally {
    // /clear changed the session id: the picker must not stay armed for the new chat
    await update($, isArmed, () => false)
    await update($, isBusy, () => false)
    await guarded($, 'launch', () => refresh($, python))
  }
}

export const register: Register = (on, options) => {
  const minTokens = options.bandMinTokens as number
  const idleMinutes = options.bandIdleMinutes as number
  const showLimits = options.showLimits as boolean
  const python = options.python as string

  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'cg-band', description: T.cgBandDescription, argumentHint: '[off]' })
    await $.command.register({ name: 'cg-health', description: T.cgHealthDescription })
    await $.command.register({ name: 'cg-index', description: T.cgIndexDescription, argumentHint: '[rebuild]' })
    // two short schemas in every request cost less than the extra ToolSearch turn a deferred tool needs in each new chat
    await $.tool.register({
      name: 'recall',
      description: T.recallDescription,
      inputSchema: {
        type: 'object',
        properties: { query: { type: 'string' }, scope: { type: 'string', enum: ['project', 'all'] }, limit: { type: 'integer', minimum: 1, maximum: 20 } },
        required: ['query'],
      },
      isDeferred: false,
    })
    await $.tool.register({
      name: 'recall_more',
      description: T.recallMoreDescription,
      inputSchema: { type: 'object', properties: { conv: { type: 'string' }, pos: { type: 'integer' } }, required: ['conv', 'pos'] },
      isDeferred: false,
    })
    // timers outlive /clear (no session.start after it), so one tick also keeps a new session's figures fresh
    $.clock.every(30_000, () => void guarded($, 'timer', () => refresh($, python)))
    // session.end has 1.5 s, too little to index: a closed chat is picked up here, or by the next recall. Never awaited, so session.start is not held.
    $.clock.after(3_000, () => void guarded($, 'index', async () => indexErrors($, 'index', (await runCg($, python, ['index'], 600_000)).errors as IndexError[], false)))
    await guarded($, 'session.start', () => refresh($, python))

    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    // a subagent's turn or one that cost nothing (interrupted, API error) does not move the cache clock
    if (e.agentId === undefined && e.usage !== undefined) {
      await guarded($, 'turn.complete', async () => {
        // "Остаться здесь" lasts one turn, and an armed picker never outlives one
        await update($, hiddenId, () => '')
        await update($, isArmed, () => false)
        const [id, now, usage] = await Promise.all([$.session.id(), $.clock.now(), $.session.usage()])
        await $.store.set(`turn:${id}`, { at: now, tokens: usage.context.tokens ?? null })
        await refresh($, python)
      })
    }

    return next(e)
  })

  on('command.run', { command: 'cg-band' }, async ($, e) => {
    const isOff = e.args.trim() === 'off'
    const isOk = await guarded($, 'cg-band', async () => {
      await refresh($, python)
      const id = await $.session.id()
      await update($, forcedId, () => (isOff ? '' : id))
      if (!isOff) await update($, hiddenId, () => '')
    })

    return { text: !isOk ? T.cgBandFailed : isOff ? T.cgBandOff : T.cgBandOn }
  })

  on('command.run', { command: 'cg-health' }, async $ => {
    try {
      const dir = await memoryDir($)
      const [{ version }, index, files, lastError] = await Promise.all([
        $.session.version(),
        runCg($, python, ['status']).catch((err: Error) => err.message),
        dir === null ? [] : $.fs.list(dir),
        $.store.get('lastError'),
      ])
      const facts = files.filter(f => f.name.endsWith('.md') && f.name !== 'MEMORY.md').length
      const memory = dir === null ? null : { dir, facts }
      return { text: healthLines({ version, index, memory, lastError: lastError as LastError | undefined }).join('\n') }
    } catch (err) {
      await fail($, 'cg-health', err)
      return { text: T.cgHealthFailed }
    }
  })

  on('command.run', { command: 'cg-index' }, async ($, e) => {
    try {
      const i = await runCg($, python, ['index', ...(e.args.trim() === 'rebuild' ? ['--rebuild'] : [])], 600_000)
      await indexErrors($, 'cg-index', i.errors as IndexError[], true)
      return { text: T.cgIndexDone(i) }
    } catch (err) {
      await fail($, 'cg-index', err)
      return { text: T.cgIndexFailed }
    }
  })

  on('tool.call', { tool: 'mcp__cacheguard__recall' }, ($, e) =>
    served($, 'recall', async () => {
      const input = e as unknown as { query: string; scope?: string; limit?: number }
      if (typeof input.query !== 'string' || !input.query.trim()) throw new Error(T.noQuery) // the schema asks for it, but nothing makes the model obey
      const r = await runCg($, python, recallArgs(input, await $.session.cwd()))
      await indexErrors($, 'recall', r.errors as IndexError[], true)
      return formatHits(r.hits as Hit[], input.query, input.scope ?? 'project')
    }),
  )

  on('tool.call', { tool: 'mcp__cacheguard__recall_more' }, ($, e) =>
    served($, 'recall_more', async () => formatRows((await runCg($, python, ['expand', `--conv=${e.conv}`, `--pos=${e.pos}`])).rows as Row[])),
  )

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    // every read first, so the band is subscribed to all of them even when it draws nothing
    const s = await read($, snap)
    const hidden = await read($, hiddenId)
    const forced = await read($, forcedId)
    const armed = await read($, isArmed)
    const busy = await read($, isBusy)

    if (s === null || e.props.hasSurvey || s.id === hidden) return next(e)
    if (!isStale(s, minTokens, idleMinutes) && s.id !== forced) return next(e)

    const { Box, Button, Text } = $.ui.resolve(e)

    return (
      <Box flexDirection="column">
        {bandLines(s, showLimits).map(line => (
          <Text>{line}</Text>
        ))}
        {armed ? (
          <Box flexDirection="column">
            <Text>{T.pickModel}</Text>
            <Box gap={1} flexWrap="wrap">
              {modelChoices(s.model).map(choice => (
                <Button key={`model:${choice.model ?? 'same'}`} label={choice.label} onPress={() => startWorker($, python, choice.model)} />
              ))}
              <Button key="cancel" label={T.cancel} onPress={() => update($, isArmed, () => false)} />
            </Box>
            <Text dimColor>{T.atFilesHint}</Text>
          </Box>
        ) : (
          <Box gap={1} flexWrap="wrap">
            <Button key="launch" label={busy ? T.busy : T.newWorker} onPress={() => (busy ? undefined : update($, isArmed, () => true))} />
            <Button key="stay" label={T.stayHere} onPress={() => update($, hiddenId, () => s.id)} />
          </Box>
        )}
      </Box>
    )
  })
}
