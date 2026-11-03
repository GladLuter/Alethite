// Pure side of the background job (stage 4): the scheduled task of this computer that runs cg.py maintain every N minutes with no chat
// open (the index, the vectors on mains power, the sync). Windows first. Everything that touches `$` lives in register.tsx.
import { T } from './text'

export type JobRun = {
  at: string
  index: { rows: number; errors: number } | null
  embed: { done: number; pending: number | null; errors: number } | { skipped: 'battery' | 'busy' } | null
  sync: { pushed: number; pulled: number; errors: number } | null
  errors: { where: string; error: string }[]
}

// `/al-job` → status; `on [minutes]` → install; `off` → remove; null for anything else.
export const jobArgs = (args: string): string[] | null => {
  const [what, minutes, ...rest] = args.trim().split(/\s+/).filter(Boolean)
  if (what === undefined) return ['job', 'status']
  if (what === 'off' && minutes === undefined) return ['job', 'remove']
  if (what === 'on' && rest.length === 0 && (minutes === undefined || /^\d+$/.test(minutes))) return ['job', 'install', '--minutes', minutes ?? '30']
  return null
}

// What a run did, in one line: the steps it took and what each brought.
export const jobDid = (r: JobRun) =>
  [
    ...(r.index ? [T.jobIndex(r.index.rows)] : []),
    ...(r.embed === null ? [] : 'skipped' in r.embed ? [r.embed.skipped === 'battery' ? T.jobOnBattery : T.jobEmbedBusy] : [T.jobEmbed(r.embed.done, r.embed.pending ?? 0)]),
    ...(r.sync ? [T.jobSync(r.sync.pushed, r.sync.pulled)] : []),
  ].join(', ')

// The /al-health lines of the job: its last run, and the first error of it.
export const jobHealth = (r: JobRun | null | undefined) =>
  r ? [T.jobLast(new Date(r.at).toLocaleString(T.locale), jobDid(r), r.errors.length), ...(r.errors[0] ? [T.jobError(r.errors[0].where, r.errors[0].error)] : [])] : [T.jobNever]
