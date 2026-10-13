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

export const healthLines = (h: {
  version: string
  index: Record<string, unknown> | string
  memory: { dir: string; facts: number } | null
  lastError?: LastError
}) => [
  T.healthVersion(h.version),
  typeof h.index === 'string' ? T.healthIndexError(h.index) : T.healthIndex(h.index),
  h.memory ? T.healthMemory(h.memory.dir, h.memory.facts) : T.memoryNotLoaded,
  h.lastError ? T.healthLastError(new Date(h.lastError.at).toLocaleString('ru-RU'), h.lastError.where, h.lastError.msg) : T.healthNoError,
]
