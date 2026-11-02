// Pure side of the bridge to scripts/cg.py: one JSON object on stdout, {"error": "..."} and exit 1 on failure.
// Everything that touches `$` (runCg, fail, memoryDir) lives in register.tsx: the loader follows `$` only inside its own file.
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

export const parseCg = ({ exitCode, stdout, stderr }: { exitCode: number; stdout: string; stderr: string }): Record<string, unknown> => {
  let json: any
  try {
    json = JSON.parse(stdout)
  } catch {}
  if (exitCode === 0 && json && !json.error) return json
  throw new Error(json?.error || stderr.trim().slice(-300) || `exit ${exitCode}`)
}

// Why cg.py cannot use the index (index_problem, in an error answer too): '' when the answer has none, undefined when it is no JSON (nothing is known then).
export const indexProblemOf = (stdout: string): string | undefined => {
  try {
    const problem = JSON.parse(stdout).index_problem
    return typeof problem === 'string' ? problem : ''
  } catch {
    return undefined
  }
}

type Vectors = { model: string | null; with_vectors: number; total: number; failed?: number; updated: string | null }
type Project = { key: string; convs: number }

// embedCmd: the terminal command that finishes the vectors, shown while some records have none.
// syncDir: the setting ('' is off); left out, nothing is said about the sync. settingsFile: the mod's own, once session.start has found it.
// shareCipher: the setting of the same name, named beside "on" (and the index's cipher, from cg.py) when cg.py reports the encryption (the manifest's default when left out).
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
  lessons?: { count: number; shown: number; chars: number; stopped: number; on: boolean }
}) => {
  const job = typeof h.index === 'string' ? undefined : (h.index.job as JobRun | null | undefined)
  const v = typeof h.index === 'string' ? undefined : (h.index.vectors as Vectors | undefined)
  const p = typeof h.index === 'string' ? undefined : (h.index.project as Project | undefined)
  const enc = typeof h.index === 'string' ? undefined : (h.index.encryption as EncryptStatus | undefined)
  const dir = typeof h.index === 'string' ? undefined : (h.index.index_dir as IndexDirStatus | undefined)
  return [
    T.healthVersion(h.version),
    typeof h.index === 'string' ? T.healthIndexError(h.index) : T.healthIndex(h.index),
    ...(dir?.notes ?? []), // where the index is, when it is not where indexDir says: cg.py words it
    ...(p ? [T.projectLine(h.root, p.convs), ...(p.convs === 0 ? [T.projectEmpty] : [])] : []),
    ...(v
      ? [T.vectorsLine(v.model, v.with_vectors, v.total, v.updated), ...(v.failed ? [T.vectorsFailed(v.failed)] : []), ...(v.with_vectors < v.total ? [T.vectorsHint(h.embedCmd)] : [])]
      : []),
    ...(h.memory ? [T.healthMemory(h.memory.dir, h.memory.facts), ...(h.memory.tokens ? [T.memoryIndexTokens(h.memory.tokens)] : [])] : [T.memoryNotLoaded]),
    ...(h.settingsFile === undefined ? [] : [T.healthSettings(h.settingsFile)]),
    ...(enc ? encryptHealth(enc, h.shareCipher ?? 'sqlcipher') : []),
    ...(h.compression === undefined ? [] : [T.compressionLine(h.compression)]),
    ...(h.lessons === undefined ? [] : [T.healthLessons(h.lessons.count, h.lessons.shown, h.lessons.chars, h.lessons.stopped, h.lessons.on)]),
    ...(h.syncDir === undefined ? [] : syncHealth(h.syncDir, typeof h.index === 'string' ? undefined : (h.index.sync as SyncStatus | undefined))),
    ...(typeof h.index === 'string' || !('job' in h.index) ? [] : jobHealth(job)),
    h.lastError ? T.healthLastError(new Date(h.lastError.at).toLocaleString(T.locale), h.lastError.where, h.lastError.msg) : T.healthNoError,
  ]
}
