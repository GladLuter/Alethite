import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Snap } from '../types'
import { bandLines, isStale, modelChoices, statusText } from './guard'
import { T } from './text'

const snap = atom({ plugin: 'cacheguard', key: 'snap' } as const, null)
const hiddenId = atom({ plugin: 'cacheguard', key: 'hiddenId' } as const, '')
const forcedId = atom({ plugin: 'cacheguard', key: 'forcedId' } as const, '')
const isArmed = atom({ plugin: 'cacheguard', key: 'isArmed' } as const, false)

// Measures the session once; the status line and the band both draw from the result.
const refresh = async ($: EngineInterface) => {
  const [id, model, usage, now] = await Promise.all([$.session.id(), $.session.model(), $.session.usage(), $.clock.now()])
  const rec = (await $.store.get(`turn:${id}`)) as { at: number; tokens: number | null } | undefined
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

// A failed step puts its error in the status line; the next good refresh overwrites it. Task 7 swaps this for fail().
const guarded = async ($: EngineInterface, work: () => Promise<unknown>) => {
  try {
    await work()
    return true
  } catch (err) {
    $.ui.status(T.statusError(err instanceof Error ? err.message : String(err)))
    return false
  }
}

export const register: Register = (on, options) => {
  const minTokens = options.bandMinTokens as number
  const idleMinutes = options.bandIdleMinutes as number
  const showLimits = options.showLimits as boolean

  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'cg-band', description: T.cgBandDescription, argumentHint: '[off]' })
    // timers outlive /clear (no session.start after it), so one tick also keeps a new session's figures fresh
    $.clock.every(30_000, () => void guarded($, () => refresh($)))
    await guarded($, () => refresh($))

    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    // a subagent's turn or one that cost nothing (interrupted, API error) does not move the cache clock
    if (e.agentId === undefined && e.usage !== undefined) {
      await guarded($, async () => {
        // "Остаться здесь" lasts one turn, and an armed picker never outlives one
        await update($, hiddenId, () => '')
        await update($, isArmed, () => false)
        const [id, now, usage] = await Promise.all([$.session.id(), $.clock.now(), $.session.usage()])
        await $.store.set(`turn:${id}`, { at: now, tokens: usage.context.tokens ?? null })
        await refresh($)
      })
    }

    return next(e)
  })

  on('command.run', { command: 'cg-band' }, async ($, e) => {
    const isOff = e.args.trim() === 'off'
    const isOk = await guarded($, async () => {
      await refresh($)
      const id = await $.session.id()
      await update($, forcedId, () => (isOff ? '' : id))
      if (!isOff) await update($, hiddenId, () => '')
    })

    return { text: !isOk ? T.cgBandFailed : isOff ? T.cgBandOff : T.cgBandOn }
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
