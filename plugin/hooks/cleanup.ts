// Pure side of /al-cleanup: the cg.py command line and what the owner reads of its answer and of /al-health.
// Everything that touches `$` (runCg, the toasts) lives in register.tsx: the loader follows `$` only inside its own file.
import { T } from './text'

export type CleanupResult = { on: boolean; preview?: { chats: number; mb: number } }
export type CleanupStatus = { on: boolean; removed_total: number }

// No word is the state; null is a word that is neither on, off nor status.
export const cleanupArgs = (args: string): string[] | null => {
  const word = args.trim() || 'status'
  return ['on', 'off', 'status'].includes(word) ? ['cleanup', word] : null
}

export const cleanupReply = (r: CleanupResult) => (r.preview ? T.cleanupState(r.on, r.preview.chats, Math.round(r.preview.mb * 10) / 10) : T.cleanupNoPreview(r.on))
export const cleanupHealth = (c: CleanupStatus) => T.healthCleanup(c.on, c.removed_total)
