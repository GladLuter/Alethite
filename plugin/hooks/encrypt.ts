// Pure side of /cg-encrypt (stage 5): the cg.py command line, what the owner reads of a run, and the encryption line of /cg-health.
// The key and the recovery code never pass here: cg.py shows the code in its own window and keeps the key in the settings file.
// Everything that touches `$` (runCg, the toasts) lives in register.tsx: the loader follows `$` only inside its own file.
import { T } from './text'

export type EncryptStatus = { on: boolean; db: 'encrypted' | 'plain' | 'none'; key: 'ok' | 'missing' | 'unreadable'; notes: string[] }
export type EncryptResult = { action: string; done: boolean; notes: string[] }

// `/cg-encrypt` or `status` → the state; `on | join | off` → the run, which opens windows and takes the sync folder and the cipher of the
// settings (values as `--flag=value`: a folder that starts with `-` must not be read as a flag); null for anything else.
export const encryptArgs = (args: string, syncDir: string, shareCipher: string, model: string): string[] | null => {
  const what = args.trim()
  if (what === '' || what === 'status') return ['encrypt', 'status']
  return ['on', 'join', 'off'].includes(what) ? ['encrypt', what, `--sync-dir=${syncDir}`, `--share-cipher=${shareCipher}`, '--model', model] : null
}

// The end of a run: done, or not finished (a window answered No or was closed, a step waits) with the notes cg.py gives.
export const encryptReply = (r: EncryptResult) => [r.done ? T.encryptDone(r.action) : T.encryptUnfinished, ...r.notes].join('\n')

// The /cg-health lines: on or off, and the notes of cg.py as they are (a key that cannot be read, a plain copy left, what to repeat).
// A key beside an index that is still open is not "on": the index is not protected yet, and its note says what to run.
export const encryptHealth = (e: EncryptStatus, shareCipher: string) => [e.on && e.db !== 'plain' ? T.healthEncryptionOn(shareCipher) : T.healthEncryptionOff, ...e.notes]
