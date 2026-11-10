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
  bandTitle: 'Alethite - 🛡 CacheGuard',
  toast: (text: string) => `Alethite: ${text}`,

  // band
  bandCache: { expired: 'cache expired', unknown: 'cache ?' },
  limitNames: { five_hour: '5 h', seven_day: 'week' } as Record<string, string>,
  limit: (name: string, percent: number) => `${name}: ${percent}%`,
  // band, new
  details: 'Details',
  compact: 'Collapse',
  bandWarm: (min: number) => `cache ${min} min left`,
  sendHere: (cost: string) => `here ≈ ${cost}`,
  saved: (usd: string) => `saved up to ${usd}`,
  dearer: (usd: string) => `${usd} dearer`,
  upTo: (usd: string) => `up to ${usd}`,
  baseTrouble: 'base !',
  col: { chat: 'This chat', last: 'Last handoff', worker: 'Whole worker', limits: 'Limits', base: 'Base' },
  spent: (usd: string) => `spent ${usd}`,
  coldPrice: (cost: string) => `cold ${cost}`,
  workerSub: (links: number, calls: number) => `handoffs: ${links} · calls: ${calls}`,
  projectSub: (usd: string, links: number) => `project: ${usd} (${links})`,
  resets: (when: string) => `resets ${when}`,
  baseConvs: (n: number) => `conversations: ${n}`,
  baseVectors: (done: number, total: number) => `vectors ${done}/${total}`,
  baseSize: (mb: number, cipher: string) => `${mb} MB · ${cipher}`,
  baseTimes: (sync: string, job: string) => `sync ${sync}; background ${job}`,

  // band buttons
  newWorker: 'New worker',
  stayHere: 'Stay here',
  willClear: 'This chat will be cleared; the handoff is saved to a file.',
  sendNow: 'Send now',
  pickInMenu: "I'll pick the model",
  cancel: 'Cancel',
  atFilesHint: '@files and images in the draft are not passed on',
  busy: 'Starting…',
  afterReset: (time: string) => `After reset (${time})`,
  waitingAt: (time: string) => `⏰ worker at ${time}`,

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
  // cg.py cannot use the index (its folder is away and C: has no room, or the settings cannot be read): the status line says so beside whatever else it shows
  indexProblemStatus: 'Index unavailable, details in /al-health',

  // /al-band
  cgBandDescription: 'Hide the band in this chat (off) or bring it back',
  cgBandOn: 'al-band: the band is back.',
  cgBandOff: 'al-band: the band is hidden in this chat. /al-band brings it back.',
  cgBandFailed: 'al-band: did not work, the reason is on the status line.',

  // errors: the toast of an owner-started action; cg.py could not be started at all
  failed: (where: string, msg: string) => `${where} — ${msg}`,
  pythonMissing: (python: string) => `Python did not start or did not answer in time (command "${python}", setting "Python command")`,
  catchUpFailed: (where: string) => `the background memory update (${where}) failed, details in /al-health`,

  // long memory: what the model reads (tool descriptions, results) and what the owner sees about the index
  recallDescription:
    "Searches the owner's past conversations: what was discussed and decided, what failed and why, where things are. This project by default, scope=all everywhere. A hit: [date kind conv pos] excerpt; recall_more reads around it.",
  recallQuery: "Words as they were written, not a question. For a past error: its exact text, code or number (429, ENOENT, 'database is locked').",
  recallAlso: 'Up to 3 other wordings searched in the same call: for an error, the action and the cause beside its text.',
  recallMoreDescription: 'Reads the records around a recall hit: given conv and pos from its line, returns the neighbouring messages of that conversation in order.',
  noHits: (query: string, scope: string) =>
    `Nothing found for "${query}"${scope === 'all' ? '' : ' in this project'}. Try ${scope === 'all' ? '' : 'scope=all or '}other words; for an error, its text or code in also.`,
  noQuery: 'no query given: it takes words that would occur in the conversation.',
  noRows: 'No records: check conv and pos in the line of the recall hit.',
  recallFailed: (msg: string) => `The memory search failed: ${msg}`,
  vectorOff: (why: string) => `vector search unavailable: ${why}`,
  indexErrors: (n: number) => `index — ${n} errors, see /al-health`,
  indexErrorFirst: (n: number, path: string, msg: string) => `files not read: ${n}, the first ${path} — ${msg}`,
  cgIndexDescription: 'Update the long-memory index (rebuild: read every transcript again and add what is missing)',
  cgIndexHint: '[rebuild]',
  cgIndexDone: (i: Record<string, unknown>) => `Index updated: ${i.files} files, ${i.rows} new records, ${i.convs} chats in all, ${(i.seconds as number).toFixed(1)} s`,
  cgIndexFailed: 'al-index: did not work, the reason is in the toast.',

  // /al-health
  cgHealthDescription: 'Check that the mod, the index, the memory and the sync work',
  cgHealthFailed: 'al-health: did not work, the reason is in the toast.',
  healthVersion: (version: string) => `Claude Code ${version}`,
  healthIndex: (i: Record<string, unknown>) => `Index: ${i.convs} chats, ${i.rows} records, ${i.size_mb} MB, updated ${i.last_update ?? 'never'} (${i.db})`,
  healthIndexError: (msg: string) => `Index: error — ${msg}`,
  projectLine: (root: string, convs: number) => `Project: ${root} — conversations in the index: ${convs}`,
  projectEmpty: 'A search "in this project" will find nothing: ask for scope=all',
  healthMemory: (dir: string, facts: number) => `Memory: ${facts} files in ${dir}`,
  memoryIndexTokens: (n: number) => `MEMORY.md is in every request: ~${n} tokens`,
  compressionLine: (level: string) => `Memory compression: ${level} (setting "compression": off, lite, full, ultra)`,
  memoryNotLoaded: 'Memory: no auto-memory folder is loaded in this chat',
  healthLastError: (when: string, where: string, msg: string) => `Last error: ${when}, ${where} — ${msg}`,
  healthNoError: 'Last error: none',
  healthSettings: (path: string) => `Settings: ${path} (they win over /config; after a change: /reload-plugins)`,
  settingsBad: (path: string, keys: string[]) => `${path}: not a setting or not of its type, ignored: ${keys.join(', ')}`,
  settingsNotObject: 'the settings file must hold one JSON object: { "language": "ru", ... }',
  vectorsLine: (model: string | null, withVectors: number, total: number, updated: string | null) =>
    `Vectors: ${withVectors} of ${total} records (${model ?? 'no model chosen'}), updated ${updated ?? 'never'}`,
  vectorsFailed: (n: number) => `Records the model could not embed: ${n} (found by their words only)`,
  vectorsHint: (cmd: string) => `To finish them in a terminal (can be stopped and resumed): ${cmd}`,

  // sync of two computers: /al-health, /al-sync and the background run
  healthSyncOff: 'Sync: off (the "syncDir" setting)',
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
  cgSyncOff: 'al-sync: sync is off. Put the shared folder in "syncDir" of the settings file (/al-health names it), then /reload-plugins.',
  cgSyncSkipUsage: 'al-sync skip: name the computer and the batch, for example /al-sync skip NOTEBOOK 12',
  cgSyncDone: (r: { pushed: number; pulled: number; vectors: { pushed: number; pulled: number } }) =>
    `Sync: records pushed ${r.pushed} (vectors ${r.vectors.pushed}), taken in ${r.pulled} (vectors ${r.vectors.pulled})`,
  cgSyncPeers: (parts: string) => `Computers, last batch taken in: ${parts}`,
  cgSyncNoPeers: 'No other computer has pushed anything to the folder yet',
  cgSyncForgotten: (n: number) => `Records forgotten on another computer's request: ${n}`,
  cgSyncSkippedVectors: (n: number) => `Records whose vectors are of another model (embedded here instead): ${n}`,
  cgSyncWarning: (msg: string) => `Warning: ${msg}`,
  cgSyncError: (msg: string) => `Error: ${msg}`,
  cgSyncFailed: 'al-sync: did not work, the reason is in the toast.',
  syncErrors: (n: number) => `sync — ${n} errors, see /al-health`,

  // /al-stats: the reply, and the summary note written into the vault
  cgStatsDescription: 'Cost measurement for N days (7 by default): a note and the summary in Obsidian',
  cgStatsHint: '[days]',
  cgStatsDays: 'al-stats: the number of days is a whole number above zero, for example /al-stats 7.',
  noHost: 'the computer name is unknown (COMPUTERNAME is empty)',
  cgStatsErrors: (n: number) => `Note: the figures may be low or off, errors while measuring: ${n} (details in /al-health).`,
  cgStatsSent: (n: number) => `Notes written to Obsidian: ${n}, the summary is updated.`,
  cgStatsQueued: (n: number) => `Obsidian is unavailable (the reason is in the toast). Notes waiting to be sent: ${n}; the next /al-stats sends them.`,
  cgStatsSummaryFailed: 'The note is written, but the summary is not rebuilt (the reason is in the toast); the next /al-stats tries again.',
  summaryTruncated: 'The measurements folder holds over 1000 files: the summary would be incomplete; move old notes into subfolders',
  cgStatsFailed: 'al-stats: did not work, the reason is in the toast.',
  statsFolder: 'Alethite/Measurements',
  statsSummary: 'Summary',
  summaryTitle: '# Measurement summary',
  summaryHint: 'Built by /al-stats from the notes of each computer; do not edit by hand.',
  summaryColumns: ['date', 'computer', '$ per owner message', 'cache rewrites', 'of them after a pause ≥ 60 min', 'median context', 'owner messages', 'recall results'],

  // short memory: the rule the model reads in every prompt, and the owner's note that a fact was saved
  memoryRule:
    "Worker memory (Alethite). The owner's CLAUDE.md rules outrank skill instructions: before asking the owner, check whether CLAUDE.md or the memory answers it. Once an error is understood, save it with lesson_save at once, not to auto-memory; his rule for every project: scope all. The owner's decisions go to auto-memory, one fact per file. Before proposing an approach, check the memory; if unsure it was tried, call recall (this project; scope=all only when asked) by an error's text, and quote it.",
  remembered: (name: string) => `remembered: ${name}`,

  // lessons (stage 4): a past error and what it taught, in the index, brought back only where it fits
  lessonSaveDescription:
    'Saves a lesson: an error this project ran into and what it taught, so it is not repeated. Call it once the cause is clear. The mod shows the lesson again when a message or a command comes close to it; it stays out of auto-memory and costs nothing otherwise.',
  lessonAction: 'What was done: the command, the change.',
  lessonSymptom: 'What happened: the error text, code or number as it was.',
  lessonCause: 'Why it happened.',
  lessonFix: 'What to do instead, if known.',
  lessonTrigger: "Optional: a literal piece of a command, 8 characters or more (e.g. 'gh api /repos'), that stops and reminds before it runs again.",
  lessonScope: "Optional: 'all' for a standing rule of the owner about how to work, true in any project and never a fact about this project's code; needs fix.",
  lessonSaved: (text: string) => `Lesson saved:\n${text}`,
  lessonKnown: 'This lesson is saved already.',
  lessonSaveFailed: (msg: string) => `The lesson was not saved: ${msg}`,
  lessonToast: (action: string) => `lesson saved: ${action}`,
  lessonSavedGlobal: (text: string) => `Lesson for all projects saved:
${text}`,
  lessonToastGlobal: (action: string, left: number) =>
    `lesson saved for all projects, it will come in every chat: ${action}${left > 0 ? ` (${left} older no longer fit)` : ''}`,
  lessonGlobalMark: '[all projects] ',
  rulesIntro: "Alethite: the owner's standing rules for all projects. Follow them.",
  lessonsIntro: 'Alethite: lessons of this project that may concern this message. Check them before acting; ignore what is unrelated.',
  lessonGate: (lesson: string) =>
    `Alethite stopped this command once: it holds the trigger of a lesson of this project. ${lesson}. If the lesson does not apply now, run the command again and it passes.`,
  lessonGateGlobal: (lesson: string) =>
    `Alethite stopped this command once: it holds the trigger of a lesson for all projects. ${lesson}. If the lesson does not apply now, run the command again and it passes.`,
  noLessons: 'No lessons in this project yet.',
  cgLessonsDescription: 'The lessons of this project and the ones for all projects (forget <pos>: delete one here and on the other computers)',
  cgLessonsHint: '[forget <pos>]',
  cgLessonsUsage: 'Use: /al-lessons, or /al-lessons forget <pos> with the pos from the list.',
  cgLessonsForgotten: (n: number): string => (n ? 'Lesson deleted, here and, with the next sync, on the other computers.' : 'No lesson at that pos.'),
  cgLessonsFailed: 'al-lessons: did not work, the reason is in the toast.',
  healthLessons: (count: number, shown: number, chars: number, stopped: number, on: boolean) =>
    on
      ? `Lessons: ${count} in this project; shown with a message ${shown} times (~${Math.round(chars / 3.5)} tokens in all), commands stopped ${stopped} times`
      : `Lessons: ${count} in this project; brought back: off (setting "Lessons")`,

  healthRules: (global: number, rules: number, chars: number, left: number) =>
    `Lessons for all projects: ${global}; ${rules} come in every chat (~${Math.round(chars / 3.5)} tokens)${left > 0 ? `, ${left} no longer fit` : ''}`,

  // the background job (stage 4)
  cgJobDescription: 'The background job of this computer (Windows): on [minutes], off, or its state',
  cgJobHint: '[on [minutes] | off]',
  cgJobUsage: 'Use: /al-job, /al-job on [minutes, 30 by default], /al-job off.',
  cgJobOn: (minutes: number) =>
    `Background job on: every ${minutes} minutes the index, the vectors (on mains power) and the sync run with no chat open. It is the task "Alethite" of the Task Scheduler, with the settings of the mod's file.`,
  cgJobOff: 'Background job off: the task "Alethite" is removed. The mod catches up when a chat starts, as before.',
  cgJobState: (minutes: number | null): string =>
    minutes === null ? 'Background job: not on this system yet (Windows only); the mod catches up when a chat starts.' : minutes ? `Background job: on, every ${minutes} minutes.` : 'Background job: off (/al-job on).',
  cgJobFailed: 'al-job: did not work, the reason is in the toast.',
  jobIndex: (rows: number) => `index +${rows}`,
  jobEmbed: (done: number, left: number) => `vectors +${done} (${left} left)`,
  jobOnBattery: 'vectors wait for the mains',
  jobEmbedBusy: 'vectors: a chat was at it',
  jobSync: (up: number, down: number) => `sync ↑${up} ↓${down}`,
  jobLast: (at: string, did: string, errors: number) => `Background job, last run ${at}: ${did}${errors ? `; errors: ${errors}` : ''}`,
  jobError: (where: string, error: string) => `Background job error (${where}): ${error}`,
  jobNever: 'Background job: no run yet (/al-job on).',

  // encryption of the index and the sync folder (stage 5)
  cgEncryptDescription: 'Encryption of the index and the sync folder: on, join (with the recovery code of another computer), off, or its state',
  cgEncryptHint: '[on | join | off | status]',
  cgEncryptUsage: 'Use: /al-encrypt (the state), /al-encrypt on, /al-encrypt join (with the recovery code of another computer), /al-encrypt off.',
  encryptWindow: 'look for the Alethite window: it may be behind other windows',
  encryptDone: (action: string): string =>
    action === 'off'
      ? 'Encryption is off: the index and what this computer puts in the sync folder are open again.'
      : action === 'join'
        ? 'This computer joined the encryption: it has the key now, and the index and what it puts in the sync folder are encrypted.'
        : 'Encryption is on: the index and what this computer puts in the sync folder are encrypted.',
  encryptUnfinished: 'al-encrypt: not finished. Repeating the command is safe: it goes on from where it stopped.',
  encryptTimeout: (action: string) => `did not finish in 10 minutes and was stopped. Every step is safe to repeat: run /al-encrypt ${action} again, or check /al-encrypt status.`,
  cgEncryptFailed: 'al-encrypt: did not work, the reason is in the toast.',
  healthEncryptionOn: (index: string, share: string) => `Encryption: on (index: ${index}, sync folder: ${share})`,
  healthEncryptionOff: 'Encryption: off',

  // the folder of the index (/al-index-dir)
  cgIndexDirDescription: 'Move the index to a folder of your choice on a local disk. With no path a window asks for the folder',
  cgIndexDirHint: '[folder]',
  indexDirWindow: 'look for the Alethite window with the question about the folder. It may be behind other windows',
  indexDirDone: (dir: string) => `The index is in ${dir} now.`,
  indexDirUnfinished: 'al-index-dir: not finished.',
  indexDirTimeout: 'did not finish in 10 minutes and was stopped. /al-health shows where the index is now. If it is not where you want it, run /al-index-dir again.',
}

export type Texts = typeof en
