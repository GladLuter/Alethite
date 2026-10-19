// Pure side of the sync of two computers: the cg.py command line and what the owner reads of a run and of its state.
// Everything that touches `$` (runCg, memoryDir, the toasts) lives in register.tsx: the loader follows `$` only inside its own file.
import { T } from './text'

export type SyncError = { where: string; machine?: string; batch?: number; error: string }
export type SyncMemory = { pushed: number; pulled: number; conflicts: number }
export type SyncResult = {
  machine: string
  pushed: number
  pulled: number
  vectors: { pushed: number; pulled: number }
  forgotten: number
  skipped_vectors: number
  peers: Record<string, number>
  memory: SyncMemory | null
  warnings: string[]
  errors: SyncError[]
}
export type SyncStatus = {
  machine: string
  last: { at: string; dir: string; pushed: number; pulled: number; memory: SyncMemory | null; errors: number; error: string | null } | null
  peers: { machine: string; last_batch: number; skipped: number[]; model: string | null; error: string | null }[]
  origins: Record<string, number>
}

// Values go as `--flag=value`: a folder that starts with `-` must not be read as a flag. No memory folder known, no memory step.
export const syncArgs = (syncDir: string, model: string, memoryDir: string | null, root: string, skip: string[] = []) => [
  'sync',
  `--sync-dir=${syncDir}`,
  '--model',
  model,
  '--max-seconds',
  '60',
  ...(memoryDir ? [`--memory-dir=${memoryDir}`] : []),
  `--project-cwd=${root}`,
  ...(skip.length ? ['--skip-batch', ...skip] : []),
]

// `/cg-sync skip MTLAPTOP 12` → ['MTLAPTOP', '12']; [] for a plain run; null when skip is asked without a computer and a number.
export const skipArgs = (args: string): string[] | null => {
  const words = args.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return []
  return words[0] === 'skip' && words.length === 3 && /^[1-9]\d*$/.test(words[2]!) ? [words[1]!, words[2]!] : null
}

const list = (pairs: [string, number][]) => pairs.map(([name, n]) => `${name} ${n}`).join(', ')

// The reply of /cg-sync: what moved, how far each computer is, the short memory, and every warning and error.
export const syncReport = (r: SyncResult) => [
  T.cgSyncDone(r),
  Object.keys(r.peers).length ? T.cgSyncPeers(list(Object.entries(r.peers))) : T.cgSyncNoPeers,
  r.memory ? T.syncMemory(r.memory) : T.syncMemoryOff,
  ...(r.forgotten ? [T.cgSyncForgotten(r.forgotten)] : []),
  ...(r.skipped_vectors ? [T.cgSyncSkippedVectors(r.skipped_vectors)] : []),
  ...r.warnings.map(T.cgSyncWarning),
  ...r.errors.map(e => T.cgSyncError(e.error)),
]

// The /cg-health lines of the sync: the folder, the records by computer, how far each computer is, the last run and its errors.
export const syncHealth = (syncDir: string, s: SyncStatus | undefined) => {
  if (!syncDir) return [T.healthSyncOff]
  if (!s) return [T.healthSync(syncDir, '?')]
  const own = s.peers.find(p => p.machine === s.machine)
  return [
    T.healthSync(syncDir, s.machine),
    ...(Object.keys(s.origins).length ? [T.healthOrigins(list(Object.entries(s.origins)))] : []),
    ...(own ? [T.healthOwnBatches(s.machine, own.last_batch)] : []),
    ...s.peers
      .filter(p => p.machine !== s.machine)
      .flatMap(p => [
        T.healthPeer(p.machine, p.last_batch, p.model),
        ...(p.skipped.length ? [T.healthPeerSkipped(p.machine, p.skipped.join(', '))] : []),
        ...(p.error ? [T.healthPeerError(p.machine, p.error)] : []),
      ]),
    ...(s.last
      ? [
          T.healthSyncLast(new Date(s.last.at).toLocaleString(T.locale), s.last.pushed, s.last.pulled, s.last.errors),
          ...(s.last.error ? [T.healthSyncError(s.last.error)] : []),
          s.last.memory ? T.syncMemory(s.last.memory) : T.syncMemoryOff,
        ]
      : [T.healthSyncNever]),
  ]
}
