// Pure side of the bridge to scripts/cg.py: one JSON object on stdout, {"error": "..."} and exit 1 on failure.
// Everything that touches `$` (runCg, fail, memoryDir) lives in register.tsx: the loader follows `$` only inside its own file.
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

type Vectors = { model: string | null; with_vectors: number; total: number; updated: string | null }

// embedCmd: the terminal command that finishes the vectors, shown while some records have none.
export const healthLines = (h: {
  version: string
  index: Record<string, unknown> | string
  memory: { dir: string; facts: number } | null
  embedCmd: string
  lastError?: LastError
}) => {
  const v = typeof h.index === 'string' ? undefined : (h.index.vectors as Vectors | undefined)
  return [
    T.healthVersion(h.version),
    typeof h.index === 'string' ? T.healthIndexError(h.index) : T.healthIndex(h.index),
    ...(v ? [T.vectorsLine(v.model, v.with_vectors, v.total, v.updated), ...(v.with_vectors < v.total ? [T.vectorsHint(h.embedCmd)] : [])] : []),
    h.memory ? T.healthMemory(h.memory.dir, h.memory.facts) : T.memoryNotLoaded,
    h.lastError ? T.healthLastError(new Date(h.lastError.at).toLocaleString('ru-RU'), h.lastError.where, h.lastError.msg) : T.healthNoError,
  ]
}
