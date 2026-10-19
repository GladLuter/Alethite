// Pure side of the bridge to scripts/cg.py: one JSON object on stdout, {"error": "..."} and exit 1 on failure.
// Everything that touches `$` (runCg, fail, memoryDir) lives in register.tsx: the loader follows `$` only inside its own file.
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

type Vectors = { model: string | null; with_vectors: number; total: number; failed?: number; updated: string | null }
type Project = { key: string; convs: number }

// embedCmd: the terminal command that finishes the vectors, shown while some records have none.
// syncDir: the setting ('' is off); left out, nothing is said about the sync.
export const healthLines = (h: {
  version: string
  index: Record<string, unknown> | string
  memory: { dir: string; facts: number; tokens?: number } | null
  root: string
  embedCmd: string
  lastError?: LastError
  syncDir?: string
  compression?: string
}) => {
  const v = typeof h.index === 'string' ? undefined : (h.index.vectors as Vectors | undefined)
  const p = typeof h.index === 'string' ? undefined : (h.index.project as Project | undefined)
  return [
    T.healthVersion(h.version),
    typeof h.index === 'string' ? T.healthIndexError(h.index) : T.healthIndex(h.index),
    ...(p ? [T.projectLine(h.root, p.convs), ...(p.convs === 0 ? [T.projectEmpty] : [])] : []),
    ...(v
      ? [T.vectorsLine(v.model, v.with_vectors, v.total, v.updated), ...(v.failed ? [T.vectorsFailed(v.failed)] : []), ...(v.with_vectors < v.total ? [T.vectorsHint(h.embedCmd)] : [])]
      : []),
    ...(h.memory ? [T.healthMemory(h.memory.dir, h.memory.facts), ...(h.memory.tokens ? [T.memoryIndexTokens(h.memory.tokens)] : [])] : [T.memoryNotLoaded]),
    ...(h.compression === undefined ? [] : [T.compressionLine(h.compression)]),
    ...(h.syncDir === undefined ? [] : syncHealth(h.syncDir, typeof h.index === 'string' ? undefined : (h.index.sync as SyncStatus | undefined))),
    h.lastError ? T.healthLastError(new Date(h.lastError.at).toLocaleString(T.locale), h.lastError.where, h.lastError.msg) : T.healthNoError,
  ]
}
