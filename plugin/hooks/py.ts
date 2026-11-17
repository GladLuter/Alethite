// Pure side of the bridge to scripts/cg.py: one JSON object on stdout, {"error": "..."} and exit 1 on failure.
// Everything that touches `$` (runCg, fail, memoryDir) lives in register.tsx: the loader follows `$` only inside its own file.
import { cleanupHealth } from './cleanup'
import type { CleanupStatus } from './cleanup'
import { encryptHealth } from './encrypt'
import type { EncryptStatus } from './encrypt'
import type { IndexDirStatus } from './indexdir'
import { jobHealth } from './job'
import type { JobRun } from './job'
import { syncHealth } from './sync'
import type { SyncStatus } from './sync'
import { T } from './text'

export type LastError = { at: number; where: string; msg: string }

export const message = (err: unknown) => (err instanceof Error ? err.message : String(err))

type Answer = Record<string, unknown> | undefined

// The one parse of cg.py's stdout: undefined when it is no JSON object (nothing is known then).
export const parseJson = (stdout: string): Answer => {
  try {
    const json = JSON.parse(stdout)
    return json && typeof json === 'object' ? json : undefined
  } catch {}
}

export const parseCg = ({ exitCode, stderr }: { exitCode: number; stderr: string }, json: Answer): Record<string, unknown> => {
  if (exitCode === 0 && json && !json.error) return json
  throw new Error((json?.error as string | undefined) || stderr.trim().slice(-300) || `exit ${exitCode}`)
}

// Why cg.py cannot use the index (index_problem, in an error answer too): '' when the answer has none, undefined when it is no JSON.
export const indexProblemOf = (json: Answer): string | undefined => (json === undefined ? undefined : typeof json.index_problem === 'string' ? json.index_problem : '')

// The notes of cg.py about the move of the mod folder (home_notes, in an error answer too), already worded: none when the answer has none or is no JSON.
export const homeNotesOf = (json: Answer): string[] => (Array.isArray(json?.home_notes) ? json.home_notes.filter((n): n is string => typeof n === 'string' && n !== '') : [])

type Vectors = { model: string | null; with_vectors: number; total: number; failed?: number; updated: string | null }
type Project = { key: string; convs: number }

// embedCmd: the terminal command that finishes the vectors, shown while some records have none.
// syncDir: the setting ('' is off); left out, nothing is said about the sync. settingsFile: the mod's own, once session.start has found it.
// shareCipher: the setting, named in the encryption line (the manifest's default when left out).
export const healthLines = (h: {
  version: string
  index: Record<string, unknown> | string
  memory: { dir: string; facts: number; tokens?: number } | null
  root: string
  embedCmd: string
  lastError?: LastError
  syncDir?: string
  compression?: string
  settingsFile?: string
  shareCipher?: string
  lessons?: { count: number; shown: number; chars: number; stopped: number; isOn: boolean }
  rules?: { global: number; rules: number; chars: number; left: number } // the lessons for all projects and the reminder of them
  homeNotes?: string[] // the notes of the move of the mod folder in the status answer shown
}) => {
  const err = typeof h.index === 'string' ? h.index : undefined
  const i = (typeof h.index === 'string' ? {} : h.index) as { job?: JobRun | null; vectors?: Vectors; project?: Project; encryption?: EncryptStatus; cleanup?: CleanupStatus; index_dir?: IndexDirStatus; sync?: SyncStatus }
  const { job, vectors: v, project: p, encryption: enc, cleanup, index_dir: dir } = i
  return [
    T.healthVersion(h.version),
    err !== undefined ? T.healthIndexError(err) : T.healthIndex(h.index as Record<string, unknown>),
    ...(dir?.notes ?? []), // where the index is, when it is not where indexDir says: cg.py words it
    ...(h.homeNotes ?? []), // the move of the mod folder: likewise
    ...(p ? [T.projectLine(h.root, p.convs), ...(p.convs === 0 ? [T.projectEmpty] : [])] : []),
    ...(v
      ? [T.vectorsLine(v.model, v.with_vectors, v.total, v.updated), ...(v.failed ? [T.vectorsFailed(v.failed)] : []), ...(v.with_vectors < v.total ? [T.vectorsHint(h.embedCmd)] : [])]
      : []),
    ...(h.memory ? [T.healthMemory(h.memory.dir, h.memory.facts), ...(h.memory.tokens ? [T.memoryIndexTokens(h.memory.tokens)] : [])] : [T.memoryNotLoaded]),
    ...(h.settingsFile === undefined ? [] : [T.healthSettings(h.settingsFile)]),
    ...(enc ? encryptHealth(enc, h.shareCipher ?? 'sqlcipher') : []),
    ...(h.compression === undefined ? [] : [T.compressionLine(h.compression)]),
    ...(h.lessons === undefined ? [] : [T.healthLessons(h.lessons.count, h.lessons.shown, h.lessons.chars, h.lessons.stopped, h.lessons.isOn)]),
    ...(h.rules && h.rules.global > 0 ? [T.healthRules(h.rules.global, h.rules.rules, h.rules.chars, h.rules.left, h.lessons?.isOn ?? true)] : []),
    ...(cleanup ? [cleanupHealth(cleanup)] : []),
    ...(h.syncDir === undefined ? [] : syncHealth(h.syncDir, i.sync)),
    ...(err !== undefined || !('job' in i) ? [] : jobHealth(job)),
    h.lastError ? T.healthLastError(new Date(h.lastError.at).toLocaleString(T.locale), h.lastError.where, h.lastError.msg) : T.healthNoError,
  ]
}
