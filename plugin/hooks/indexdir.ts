// Pure side of /cg-index-dir: the cg.py command line, what the owner reads of a run, and the notes of /cg-health.
// Everything that touches `$` (runCg, the toasts) lives in register.tsx: the loader follows `$` only inside its own file.
import { T } from './text'

export type IndexDirResult = { action: string; done: boolean; dir: string; notes: string[] }
export type IndexDirStatus = { configured: string | null; fallback: boolean; notes: string[] }

// No path: cg.py opens the window that asks for the folder. A path comes as `--path=value` (one that starts with `-` must not be read as a
// flag), without the quotes "Copy as path" puts around it.
export const indexDirArgs = (args: string): string[] => {
  const path = args.trim().replace(/^(["'])(.*)\1$/, '$2')
  return path === '' ? ['index-dir'] : ['index-dir', `--path=${path}`]
}

// The end of a run: moved, or not finished (the window was closed) with the notes cg.py gives.
export const indexDirReply = (r: IndexDirResult) => [r.done ? T.indexDirDone(r.dir) : T.indexDirUnfinished, ...r.notes].join('\n')

// A refusal or a failure: cg.py words its reason as `ValueError: <text>`, and the owner must act on the text, not on the class.
export const indexDirRefusal = (msg: string) => `cg-index-dir: ${msg.replace(/^\w+(Error|Exception): /, '')}`
