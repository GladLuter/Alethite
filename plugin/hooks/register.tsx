import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Snap } from '../types'
import { bandLines, isStale, modelChoices, statusText } from './guard'
import { healthLines, message, parseCg } from './py'
import type { LastError } from './py'
import { T } from './text'

const snap = atom({ plugin: 'cacheguard', key: 'snap' } as const, null)
const hiddenId = atom({ plugin: 'cacheguard', key: 'hiddenId' } as const, '')
const forcedId = atom({ plugin: 'cacheguard', key: 'forcedId' } as const, '')
const isArmed = atom({ plugin: 'cacheguard', key: 'isArmed' } as const, false)

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

export const register: Register = (on, options) => {
  const minTokens = options.bandMinTokens as number
  const idleMinutes = options.bandIdleMinutes as number
  const showLimits = options.showLimits as boolean
  const python = options.python as string

  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'cg-band', description: T.cgBandDescription, argumentHint: '[off]' })
    await $.command.register({ name: 'cg-health', description: T.cgHealthDescription })
    // timers outlive /clear (no session.start after it), so one tick also keeps a new session's figures fresh
    $.clock.every(30_000, () => void guarded($, 'timer', () => refresh($, python)))
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

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    // every read first, so the band is subscribed to all of them even when it draws nothing
    const s = await read($, snap)
    const hidden = await read($, hiddenId)
    const forced = await read($, forcedId)
    const armed = await read($, isArmed)

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
                <Button key={`model:${choice.model ?? 'same'}`} label={choice.label} onPress={() => $.ui.toast(T.notYet)} />
              ))}
              <Button key="cancel" label={T.cancel} onPress={() => update($, isArmed, () => false)} />
            </Box>
            <Text dimColor>{T.atFilesHint}</Text>
          </Box>
        ) : (
          <Box gap={1} flexWrap="wrap">
            <Button key="launch" label={T.newWorker} onPress={() => update($, isArmed, () => true)} />
            <Button key="stay" label={T.stayHere} onPress={() => update($, hiddenId, () => s.id)} />
          </Box>
        )}
      </Box>
    )
  })
}
