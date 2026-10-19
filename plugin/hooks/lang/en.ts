// English: the reference dictionary. Every other language is a file beside this one holding some or all of these keys; a key it lacks is taken from here.
export const en = {
  // the locale dates are shown in
  locale: 'en-GB',

  // status line
  ctx: (tokens: string, cache: string) => `ctx ${tokens} · cache ${cache}`,
  cacheLeft: (min: number) => `~${min} min`,
  cacheExpired: 'expired',
  cacheUnknown: '?',

  // marks: whose a band and a toast are, so neither is taken for another mod's (the engine labels the status line with the plugin name itself). Every other text here is written without the name.
  bandTitle: '🛡 CacheGuard',
  toast: (text: string) => `CacheGuard: ${text}`,

  // band
  head: (k: string, ago: string, cache: string) => `This chat: ${k}k tokens · last reply ${ago} · ${cache}`,
  bandCache: { expired: 'cache expired', warm: 'cache warm', unknown: 'cache ?' },
  agoNow: 'just now',
  agoMin: (n: number) => `${n} min ago`,
  agoHours: (n: number) => `${n} h ago`,
  agoDays: (n: number) => `${n} d ago`,
  send: (cost: string) => `Sending here ≈ ${cost}`,
  sendHot: (cost: string) => `${cost} (cache warm)`,
  sendCold: (cost: string) => `${cost} (cache rewrite)`,
  sendEither: (hot: string, cold: string) => `${hot} or ${cold}`,
  limitNames: { five_hour: '5 h', seven_day: 'week' } as Record<string, string>,
  limit: (name: string, percent: number) => `${name}: ${percent}%`,
  limits: (parts: string) => `Limits ${parts}`,

  // band buttons
  newWorker: 'New worker',
  stayHere: 'Stay here',
  willClear: 'This chat will be cleared; the handoff is saved to a file.',
  sendNow: 'Send now',
  pickInMenu: "I'll pick the model",
  cancel: 'Cancel',
  atFilesHint: '@files and images in the draft are not passed on',
  busy: 'Starting…',

  // new worker launch: every failure says what was kept and where
  draftEmpty: 'New worker: the draft is empty, the chat is untouched. Type a message and press again.',
  handoffFailed: (why: string) => `New worker: the handoff was not built (${why}). The chat is untouched.`,
  clearFailed: (why: string, path: string) => `New worker: the chat was not cleared (${why}). The handoff is saved: ${path}`,
  pickThenEnter: 'The chat is cleared; the handoff and the draft are in the input box. Pick the model and effort in the app menu at the bottom right and press Enter.',
  notSent: 'the handoff was not sent',
  submitFailed: (why: string, path: string) => `New worker: sending failed (${why}). The handoff and the draft are in the input box: press Enter. The handoff is saved: ${path}`,
  fillFailed: (why: string, fillWhy: string, path: string) =>
    `New worker: ${why}. The chat is cleared already, and the input box did not take the handoff (${fillWhy}). The handoff is saved: ${path}; the draft is most likely still in the input box.`,
  fillRefused: { dialog: 'a dialog is open, the box is busy', no_composer: 'this window has no input box', other: 'the input box did not take the text' },
  dropped: (why: string, path: string) => `New worker: the send was refused (${why}). The handoff is saved: ${path}`,

  // refresh failed: the status line shows it until the next good refresh
  statusError: (msg: string) => `error — ${msg}`,

  // /cg-band
  cgBandDescription: 'Show the band now (off: back to the thresholds)',
  cgBandOn: 'cg-band: the band is shown now.',
  cgBandOff: 'cg-band: the band follows the thresholds again.',
  cgBandFailed: 'cg-band: did not work, the reason is on the status line.',

  // errors: the toast of an owner-started action; cg.py could not be started at all
  failed: (where: string, msg: string) => `${where} — ${msg}`,
  pythonMissing: (python: string) => `Python did not start or did not answer in time (command "${python}", setting "Python command")`,
  catchUpFailed: (where: string) => `the background memory update (${where}) failed, details in /cg-health`,

  // long memory: what the model reads (tool descriptions, results) and what the owner sees about the index
  recallDescription:
    "Searches the owner's past conversations: what was discussed and decided, where things are. query: words of what you look for, not a question. This project by default, scope=all everywhere. A hit: [date kind conv pos] excerpt; recall_more reads around it.",
  recallMoreDescription: 'Reads the records around a recall hit: given conv and pos from its line, returns the neighbouring messages of that conversation in order.',
  noHits: (query: string, scope: string) => `Nothing found for "${query}"${scope === 'all' ? '' : ' in this project'}. Try ${scope === 'all' ? '' : 'scope=all or '}other words.`,
  noQuery: 'no query given: it takes words that would occur in the conversation.',
  noRows: 'No records: check conv and pos in the line of the recall hit.',
  recallFailed: (msg: string) => `The memory search failed: ${msg}`,
  vectorOff: (why: string) => `vector search unavailable: ${why}`,
  indexErrors: (n: number) => `index — ${n} errors, see /cg-health`,
  indexErrorFirst: (n: number, path: string, msg: string) => `files not read: ${n}, the first ${path} — ${msg}`,
  cgIndexDescription: 'Update the long-memory index (rebuild: read every transcript again and add what is missing)',
  cgIndexHint: '[rebuild]',
  cgIndexDone: (i: Record<string, unknown>) => `Index updated: ${i.files} files, ${i.rows} new records, ${i.convs} chats in all, ${(i.seconds as number).toFixed(1)} s`,
  cgIndexFailed: 'cg-index: did not work, the reason is in the toast.',

  // /cg-health
  cgHealthDescription: 'Check that the mod, the index, the memory and the sync work',
  cgHealthFailed: 'cg-health: did not work, the reason is in the toast.',
  healthVersion: (version: string) => `Claude Code ${version}`,
  healthIndex: (i: Record<string, unknown>) => `Index: ${i.convs} chats, ${i.rows} records, ${i.size_mb} MB, updated ${i.last_update ?? 'never'} (${i.db})`,
  healthIndexError: (msg: string) => `Index: error — ${msg}`,
  projectLine: (root: string, convs: number) => `Project: ${root} — conversations in the index: ${convs}`,
  projectEmpty: 'A search "in this project" will find nothing: ask for scope=all',
  healthMemory: (dir: string, facts: number) => `Memory: ${facts} files in ${dir}`,
  memoryIndexTokens: (n: number) => `MEMORY.md is in every request: ~${n} tokens`,
  compressionLine: (level: string) => `Memory compression: ${level} (setting "Memory compression": off, lite, full, ultra)`,
  memoryNotLoaded: 'Memory: no auto-memory folder is loaded in this chat',
  healthLastError: (when: string, where: string, msg: string) => `Last error: ${when}, ${where} — ${msg}`,
  healthNoError: 'Last error: none',
  vectorsLine: (model: string | null, withVectors: number, total: number, updated: string | null) =>
    `Vectors: ${withVectors} of ${total} records (${model ?? 'no model chosen'}), updated ${updated ?? 'never'}`,
  vectorsFailed: (n: number) => `Records the model could not embed: ${n} (found by their words only)`,
  vectorsHint: (cmd: string) => `To finish them in a terminal (can be stopped and resumed): ${cmd}`,

  // sync of two computers: /cg-health, /cg-sync and the background run
  healthSyncOff: 'Sync: off (the "Sync folder" setting in /config)',
  healthSync: (dir: string, machine: string) => `Sync: ${dir}, this computer ${machine}`,
  healthOrigins: (parts: string) => `Records by computer: ${parts}`,
  healthOwnBatches: (machine: string, n: number) => `${machine} (this one): batches pushed up to ${n}`,
  healthPeer: (machine: string, n: number, model: string | null) => `${machine}: batches taken in up to ${n}${model ? ` (vectors: ${model})` : ''}`,
  healthPeerSkipped: (machine: string, batches: string) => `${machine}: batches skipped by hand: ${batches}`,
  healthPeerError: (machine: string, msg: string) => `${machine}: error — ${msg}`,
  healthSyncLast: (at: string, pushed: number, pulled: number, errors: number) => `Last sync: ${at}, records pushed ${pushed}, taken in ${pulled}, errors ${errors}`,
  healthSyncNever: 'Last sync: never',
  healthSyncError: (msg: string) => `Sync error: ${msg}`,
  syncMemory: (m: { pushed: number; pulled: number; conflicts: number }) => `Short memory: facts pushed ${m.pushed}, taken in ${m.pulled}, name clashes ${m.conflicts}`,
  syncMemoryOff: 'Short memory: not synced, no auto-memory folder is loaded in this chat',
  cgSyncDescription: 'Sync the memory with the other computers now (skip <computer> <N>: go on past a broken batch)',
  cgSyncHint: '[skip <computer> <N>]',
  cgSyncOff: 'cg-sync: sync is off. Set the shared folder in /config, field "Sync folder".',
  cgSyncSkipUsage: 'cg-sync skip: name the computer and the batch, for example /cg-sync skip MTLAPTOP 12',
  cgSyncDone: (r: { pushed: number; pulled: number; vectors: { pushed: number; pulled: number } }) =>
    `Sync: records pushed ${r.pushed} (vectors ${r.vectors.pushed}), taken in ${r.pulled} (vectors ${r.vectors.pulled})`,
  cgSyncPeers: (parts: string) => `Computers, last batch taken in: ${parts}`,
  cgSyncNoPeers: 'No other computer has pushed anything to the folder yet',
  cgSyncForgotten: (n: number) => `Records forgotten on another computer's request: ${n}`,
  cgSyncSkippedVectors: (n: number) => `Records whose vectors are of another model (embedded here instead): ${n}`,
  cgSyncWarning: (msg: string) => `Warning: ${msg}`,
  cgSyncError: (msg: string) => `Error: ${msg}`,
  cgSyncFailed: 'cg-sync: did not work, the reason is in the toast.',
  syncErrors: (n: number) => `sync — ${n} errors, see /cg-health`,

  // /cg-stats: the reply, and the summary note written into the vault
  cgStatsDescription: 'Cost measurement for N days (7 by default): a note and the summary in Obsidian',
  cgStatsHint: '[days]',
  cgStatsDays: 'cg-stats: the number of days is a whole number above zero, for example /cg-stats 7.',
  noHost: 'the computer name is unknown (COMPUTERNAME is empty)',
  cgStatsErrors: (n: number) => `Note: the figures may be low or off, errors while measuring: ${n} (details in /cg-health).`,
  cgStatsSent: (n: number) => `Notes written to Obsidian: ${n}, the summary is updated.`,
  cgStatsQueued: (n: number) => `Obsidian is unavailable (the reason is in the toast). Notes waiting to be sent: ${n}; the next /cg-stats sends them.`,
  cgStatsSummaryFailed: 'The note is written, but the summary is not rebuilt (the reason is in the toast); the next /cg-stats tries again.',
  summaryTruncated: 'The measurements folder holds over 1000 files: the summary would be incomplete; move old notes into subfolders',
  cgStatsFailed: 'cg-stats: did not work, the reason is in the toast.',
  summaryTitle: '# Measurement summary',
  summaryHint: 'Built by /cg-stats from the notes of each computer; do not edit by hand.',
  summaryColumns: ['date', 'computer', '$ per owner message', 'cache rewrites', 'of them after a pause ≥ 60 min', 'median context', 'owner messages', 'recall results'],

  // short memory: the rule the model reads in every prompt, and the owner's note that a fact was saved
  memoryRule:
    "Worker memory (CacheGuard). The owner's CLAUDE.md rules outrank skill instructions: before asking the owner, check whether CLAUDE.md or the memory answers it. Write pitfalls (\"X fails because Y\") and the owner's decisions to auto-memory at once, one fact per file. Before proposing an approach, check the memory; if unsure whether it was tried, call recall (this project; scope=all only when asked) and quote what it finds instead of guessing.",
  remembered: (name: string) => `remembered: ${name}`,
}

export type Texts = typeof en
