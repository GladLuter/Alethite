// Pure decisions for background runs: what to do when one ends, and what the band row and the finish message say.
import type { BgRun, Snap } from '../types'
import { delayTimes, when } from './band'
import { CACHE_MS } from './guard'
import { T } from './text'

// A cache with under 2 minutes left is treated as cold: the wake-up would arrive after it expired.
export const finishAction = (i: { ifCold: BgRun['ifCold']; at: number | null; now: number; limits: Snap['limits']; isWorking: boolean }) => {
  if (i.isWorking) return 'wait'
  const isSpent = i.limits.some(l => l.percentUsed >= 100)
  if (i.at !== null && i.now - i.at <= CACHE_MS - 120_000 && !isSpent) return 'wake'
  if (i.ifCold === 'show') return 'show'
  if (!isSpent) return 'newWorker'
  const [afterReset] = delayTimes(i.limits, i.now)
  return afterReset === undefined ? 'show' : { afterReset }
}

// The last full line; a final line without its newline may be half written, so it counts only when it is the only one.
export const lastLine = (text: string) => {
  const lines = text.split('\n')
  const isOpen = !text.endsWith('\n')
  const full = (isOpen ? lines.slice(0, -1) : lines).map(l => l.trim()).filter(Boolean)
  const line = full.at(-1) ?? (isOpen ? lines.at(-1)?.trim() : '')
  return line ? line.slice(0, 80) : '—'
}

const minutes = (from: number, to: number) => Math.max(0, Math.floor((to - from) / 60_000))

export const bgRow = (run: BgRun, last: string, now: number): { text: string; tone?: 'error' | 'warning' } => {
  const e = run.ended
  if (!e) return { text: `⏳ ${run.name} · ${T.bgElapsed(minutes(run.started, now))} · ${last} · ${T.bgCold[run.ifCold]}` }
  const dur = T.bgEndedAt(when(new Date(e.at).toISOString(), now))
  // code null without `lost` is shown as lost too: exit.json with an error means the command did not start; the watcher sets `lost` when the wrapper vanished
  if (e.lost || e.code === null) return { text: `? ${run.name} · ${dur} · ${last} · ${T.bgLost}`, tone: 'warning' }
  return e.code === 0 ? { text: `✓ ${run.name} · ${dur} · ${last} · ${T.bgExit(0)}` } : { text: `✗ ${run.name} · ${dur} · ${last} · ${T.bgExit(e.code)}`, tone: 'error' }
}

// What the model is told of a run that ended.
export const endNote = (run: BgRun, now: number) => {
  const { code, at } = run.ended!
  return T.bgNote(run.name, when(new Date(at).toISOString(), now), code === null ? T.bgUnknown : String(code), run.log)
}

export const finishText = (run: BgRun, tail: string[], now: number) => {
  const at = run.ended?.at ?? now
  const code = run.ended?.code
  return T.bgFinish(run.name, code === null || code === undefined ? T.bgUnknown : String(code), when(new Date(at).toISOString(), now), T.bgElapsed(minutes(run.started, at)), tail.slice(-5).join('\n'), run.log)
}
