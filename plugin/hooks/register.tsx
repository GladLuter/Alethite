import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Savings, Snap } from '../types'
import { columns, columnsSvg, delayAt, fitTokens, lineTokens, SHIELD, tokensWidth, when } from './band'
import { isStale, statusText } from './guard'
import { autoMemDir, autoMemTokens, compressionLevel, isMemoryFact } from './memory'
import type { Compression } from './memory'
import { flush, notePath, summaryNote, summaryPath } from './notes'
import type { Note } from './notes'
import { encryptArgs, encryptHealth, encryptReply, isTimeout } from './encrypt'
import type { EncryptResult, EncryptStatus } from './encrypt'
import { indexDirArgs, indexDirRefusal, indexDirReply } from './indexdir'
import type { IndexDirResult } from './indexdir'
import { healthLines, homeNotesOf, indexProblemOf, message, parseCg } from './py'
import type { LastError } from './py'
import { jobArgs, jobHealth } from './job'
import type { JobRun } from './job'
import { END, forgetArg, formatLessons, gateFor, lessonContext, lessonSaveArgs, lessonsMatchArgs, oneLine, START } from './lessons'
import type { Found, Lesson } from './lessons'
import { formatHits, formatRows, recallArgs } from './recall'
import type { Hit, IndexError, Row } from './recall'
import { skipArgs, syncArgs, syncReport } from './sync'
import type { SyncResult } from './sync'
import { oldSettingsPath, settingsPath, withFile } from './settings'
import { language, setLanguage, T } from './text'
import { launch } from './worker'
import type { LaunchIO } from './worker'

const snap = atom({ plugin: 'alethite', key: 'snap' } as const, null)
const hiddenId = atom({ plugin: 'alethite', key: 'hiddenId' } as const, '')
const isArmed = atom({ plugin: 'alethite', key: 'isArmed' } as const, false)
const isBusy = atom({ plugin: 'alethite', key: 'isBusy' } as const, false)
const info = atom({ plugin: 'alethite', key: 'info' } as const, null)
const isDetails = atom({ plugin: 'alethite', key: 'isDetails' } as const, false)
const calmId = atom({ plugin: 'alethite', key: 'calmId' } as const, '')
const delayedAt = atom({ plugin: 'alethite', key: 'delayedAt' } as const, null)

// The helpers below take `$` and so stay in this file: the loader follows `$` only into functions declared here, never across an import.

// The notice of cg.py about where the index is (not in its folder, a merge done) that was shown last: a toast only when it changes or comes back
// after an answer with none, /al-health keeps saying it.
let lastNotice = ''

// The notes of cg.py about the one-time move of the mod folder (home_notes) that were toasted: each one once per load of the mod.
// /al-health tells the notes of its own fresh status answer instead, so a note that is no longer true does not stay on the report.
const toastedNotes: string[] = []

// Why cg.py cannot use the index, kept until an answer comes without it, and what the status line shows besides the warning about it.
let indexProblem = ''
let statusLine = ''

// The one door for the status line: while the index cannot be used, the warning goes with whatever else is shown.
const showStatus = ($: EngineInterface, text: string) => {
  statusLine = text
  $.ui.status(indexProblem ? [text, T.indexProblemStatus].filter(Boolean).join(' · ') : text)
}

const setProblem = ($: EngineInterface, text: string) => {
  if (text === indexProblem) return
  indexProblem = text
  showStatus($, statusLine)
}

const runCg = async ($: EngineInterface, python: string, args: string[], timeoutMs = 120_000) => {
  // run() rejects only when the child cannot start or times out; cg.py speaks the language of the mod
  const r = await $.process.run([python, `${$.plugin.root}/scripts/cg.py`, '--lang', language(), ...args], { timeoutMs }).catch((err: unknown) => {
    throw new Error(`${T.pythonMissing(python)} (${message(err)})`)
  })
  const problem = indexProblemOf(r.stdout) // before parseCg throws: an error answer carries it too
  if (problem !== undefined) setProblem($, problem)
  for (const note of homeNotesOf(r.stdout)) {
    if (toastedNotes.includes(note)) continue
    toastedNotes.push(note)
    toast($, note)
  }
  const out = parseCg(r)
  const notice = typeof out.index_notice === 'string' ? out.index_notice : ''
  if (!notice) lastNotice = ''
  else if (notice !== lastNotice) {
    toast($, notice)
    lastNotice = notice // told once, until an answer comes without it
  }
  return out
}

// The one door for toasts: everything this mod puts in one starts with its name (the texts in text.ts do not). The status line needs no mark: the engine labels it with the plugin name.
const toast = ($: EngineInterface, text: string) => $.ui.toast(T.toast(text))

// An owner's prompt while the index cannot be used (none otherwise: a healthy chat pays nothing): ask again with a command that needs no index (its
// answer sets or clears the problem), and say it again if it is still there. A failed check leaves the problem as it was and never holds the prompt.
const recheck = async ($: EngineInterface, python: string) => {
  if (!indexProblem) return
  try {
    await runCg($, python, ['last-turn', '--session', await $.session.id()], 8_000)
  } catch {}
  if (indexProblem) toast($, indexProblem)
}

// What /al-health shows as the last error; every path that reports a failure leaves it here.
const noteError = async ($: EngineInterface, where: string, err: unknown) => $.store.set('lastError', { at: await $.clock.now(), where, msg: message(err) })

// An owner-started action failed: tell them now and keep it for /al-health.
const fail = async ($: EngineInterface, where: string, err: unknown) => {
  toast($, T.failed(where, message(err)))
  await noteError($, where, err)
}

// cg.py skips transcripts it cannot read and retries them next time, but the owner must hear of it: on the status line when nobody asked, in a toast when they did.
const indexErrors = async ($: EngineInterface, where: string, errors: IndexError[], isAsked: boolean) => {
  if (errors.length === 0) return
  const text = T.indexErrors(errors.length)
  if (isAsked) toast($, text)
  else showStatus($, text)
  await noteError($, where, T.indexErrorFirst(errors.length, errors[0]!.path, errors[0]!.error))
}

// A memory tool call: its text for the model, or a refusal the model reads as an error, and the owner sees the failure too.
const served = async ($: EngineInterface, where: string, work: () => Promise<string>, failed: (msg: string) => string = T.recallFailed) => {
  try {
    return { result: await work() }
  } catch (err) {
    await fail($, where, err)
    return { deny: failed(message(err)) }
  }
}

// The settings file that is there now: the new one, else the old one, even if only its folder is there (cg.py moves that folder on its first run, and
// a folder made here first would stand beside it and strand the index there), else the new one, to be written. Asked at every use: the move happens
// under a running chat.
const settingsFileOf = async ($: EngineInterface, home: string) => {
  const old = oldSettingsPath(home)
  const isOld = !(await $.fs.exists(settingsPath(home))) && ((await $.fs.exists(old)) || (await $.fs.exists(old.replace(/[\\/][^\\/]+$/, ''))))
  return isOld ? old : settingsPath(home)
}

// The memory files loaded in this chat, as the engine counts them.
const memoryFiles = async ($: EngineInterface) => (await $.session.usage({ breakdown: 'summary' })).context.breakdown?.memoryFiles ?? []

const memoryDir = async ($: EngineInterface) => autoMemDir(await memoryFiles($))

// The periodic and background paths: the error goes to the status line (a toast every 30 s would be spam) and is kept for /al-health.
const guarded = async ($: EngineInterface, where: string, work: () => Promise<unknown>) => {
  try {
    await work()
    return true
  } catch (err) {
    showStatus($, T.statusError(message(err)))
    await noteError($, where, err)
    return false
  }
}

// One sync with the other computers through the shared folder, for the current project's short memory too (none known, none synced).
// cg.py stops itself after 60 s and leaves the rest for the next run.
const runSync = async ($: EngineInterface, python: string, syncDir: string, model: string, shareCipher: string, skip: string[] = []) => {
  const [dir, root] = await Promise.all([memoryDir($), $.session.root()])
  return (await runCg($, python, syncArgs(syncDir, model, dir, root, skip, shareCipher), 120_000)) as unknown as SyncResult
}

// Three seconds into a session: bring the index up to date, give the records without vectors up to 2 minutes of the embedding model
// (the rest waits for the next start), then push our new records and take in the other computers'.
// Returns the steps that failed, if any did. The index run carries --follow-job (as /al-health's status does): cg.py points the background job at this copy of itself.
const catchUp = async ($: EngineInterface, python: string, model: string, syncDir: string, shareCipher: string) => {
  const isIndexed = await guarded($, 'index', async () => indexErrors($, 'index', (await runCg($, python, ['--follow-job', 'index'], 600_000)).errors as IndexError[], false))
  if (!isIndexed) return 'index' // the same database would fail the same way, and the first cause must stay in /al-health
  const isEmbedded = await guarded($, 'embed', async () => {
    const [first] = (await runCg($, python, ['embed', '--model', model, '--max-seconds', '120'], 180_000)).errors as { error: string }[]
    if (first) throw new Error(first.error)
  })
  // records without vectors go out all the same, and their vectors with a later batch
  const isSynced =
    syncDir === '' ||
    (await guarded($, 'sync', async () => {
      const [first] = (await runSync($, python, syncDir, model, shareCipher)).errors
      if (first) throw new Error(first.error)
    }))
  const failed = [...(isEmbedded ? [] : ['embed']), ...(isSynced ? [] : ['sync'])]
  return failed.length ? failed.join(', ') : null
}

// The lessons of this chat's project (stage 4).
const lessonsOf = async ($: EngineInterface, python: string) =>
  ((await runCg($, python, ['lessons', '--cwd', await $.session.root()])).lessons as Lesson[] | undefined) ?? []

// The same, quietly, for the paths nobody asked on: a failure keeps the list there was, and must not push the first cause of a failed
// start out of /al-health (the database that failed the index fails this too); /al-lessons tells it when asked.
const lessonsOr = async ($: EngineInterface, python: string, kept: Lesson[]) => {
  try {
    return await lessonsOf($, python)
  } catch {
    return kept
  }
}

// How often the lessons came back, on this computer, for /al-health: what they cost and whether they stop anything.
const countLessons = async ($: EngineInterface, key: 'shown' | 'chars' | 'stopped', n: number) => {
  const all = ((await $.store.get('lessonCounts')) as Record<string, number> | undefined) ?? {}
  await $.store.set('lessonCounts', { ...all, [key]: (all[key] ?? 0) + n })
}

// The turn size is kept for the last 200 chats; the oldest by `at` go first.
const KEEP_TURNS = 200
const pruneTurns = async ($: EngineInterface) => {
  const keys = (await $.store.keys()).filter(k => k.startsWith('turn:'))
  if (keys.length <= KEEP_TURNS) return
  const ats = await Promise.all(keys.map(async k => [k, ((await $.store.get(k)) as { at: number | null } | undefined)?.at ?? 0] as const))
  for (const [k] of ats.sort((a, b) => a[1] - b[1]).slice(0, keys.length - KEEP_TURNS)) await $.store.delete(k)
}

// chats whose size was already asked of cg.py in this load, so a failure is not retried on every tick
const asked = new Set<string>()

// Measures the session once; the status line and the band both draw from the result.
const refresh = async ($: EngineInterface, python: string) => {
  const [id, model, usage, now] = await Promise.all([$.session.id(), $.session.model(), $.session.usage(), $.clock.now()])
  let rec = (await $.store.get(`turn:${id}`)) as { at: number | null; tokens: number | null } | undefined
  if (rec === undefined && !asked.has(id)) {
    asked.add(id)
    try {
      const last = await runCg($, python, ['last-turn', '--session', id], 30_000)
      rec = { at: Date.parse(last.at as string) || null, tokens: last.tokens as number | null }
      await $.store.set(`turn:${id}`, rec)
    } catch (err) {
      await fail($, 'last-turn', err)
    }
  }
  const fresh: Snap = {
    id,
    model,
    now,
    // a resumed chat has no live token count until its first reply, so fall back to the last one stored
    tokens: usage.context.tokens ?? rec?.tokens ?? null,
    at: rec?.at ?? null,
    cost: usage.cost?.usd ?? null,
    limits: usage.rateLimits.map(({ kind, percentUsed, resetsAt }) => ({ kind, percentUsed, resetsAt })),
  }
  showStatus($, statusText(fresh.tokens, fresh.at, now))
  await update($, snap, () => fresh)
}

// The owner's calendar day (local time) as YYYY-MM-DD.
const calendarDay = (now: number) => new Date(now - new Date(now).getTimezoneOffset() * 60_000).toISOString().slice(0, 10)

// A call to the Obsidian server; a throw and an answer with isError are both a failure, with the server's words when it gave any.
const obsidian = async ($: EngineInterface, tool: string, args: Record<string, unknown>) => {
  const r = await $.mcp.call('obsidian', tool, args)
  const text = r.content.map(c => c.text ?? '').join('')
  if (r.isError) {
    let why = text || tool // the server's JSON { error, errorCode } when it is one, else the raw text
    try {
      const { error, errorCode } = JSON.parse(text)
      if (error) why = `${error}${errorCode ? ` (${errorCode})` : ''}`
    } catch {}
    throw new Error(why)
  }
  return text
}

// The summary note: the frontmatter of every .md note directly in the folder, as one table (not the summary itself).
const rebuildSummary = async ($: EngineInterface) => {
  const [folder, summary] = [T.statsFolder, summaryPath()]
  // 1000 is the server's maximum: past it the summary would silently miss notes
  const { files, truncated } = JSON.parse(await obsidian($, 'list_vault_files', { directory: folder, limit: 1000 })) as { files: string[]; truncated: boolean }
  if (truncated === true) throw new Error(T.summaryTruncated)
  const notes = files.filter(p => p.endsWith('.md') && p !== summary && p.startsWith(`${folder}/`) && !p.includes('/', folder.length + 1))
  const fronts = await Promise.all(notes.map(async path => JSON.parse(await obsidian($, 'get_note_properties', { path })).frontmatter ?? {}))
  await obsidian($, 'create_vault_file', { path: summary, content: summaryNote(fronts) })
}

// One /al-stats run: measure, queue the note, send the queue, rebuild the summary. Returns what the owner reads.
const runStats = async ($: EngineInterface, python: string, days: number, compression: string) => {
  const [now, host, usage, files] = await Promise.all([$.clock.now(), $.env.get('COMPUTERNAME'), $.session.usage(), memoryFiles($)])
  if (!host) throw new Error(T.noHost)
  const today = calendarDay(now)
  const since = new Date(Date.parse(today) - days * 86_400_000).toISOString().slice(0, 10)
  const limits = usage.rateLimits.map(({ kind, percentUsed }) => ({ kind, percentUsed }))
  const args = ['stats', '--since', since, '--host', host, '--today', today, '--limits', JSON.stringify(limits), '--rule-chars', String(T.memoryRule.length)]
  args.push('--memory-tokens', String(autoMemTokens(files)), '--compression', compression) // what the memory takes of every request (stage 3)
  const { note, errors } = (await runCg($, python, args, 600_000)) as { note: string; errors: IndexError[] }
  await indexErrors($, 'al-stats', errors, true) // an unreadable transcript lowers the figures; the note is written all the same
  const path = notePath(host, today)
  // one note per computer and day: a note still waiting for this day is replaced, not doubled
  const queue = [...(((await $.store.get('outbox')) as Note[] | undefined) ?? []).filter(n => n.path !== path), { path, content: note }]
  await $.store.set('outbox', queue) // kept before sending, so nothing is lost if the send is cut short
  const sent = await flush(queue, async n => void (await obsidian($, 'create_vault_file', n)))
  await $.store.set('outbox', sent.left)
  const figures = note.replace(/^---\n[\s\S]*?\n---\n/, '').trim() + (errors.length ? `\n\n${T.cgStatsErrors(errors.length)}` : '') // the frontmatter is for the vault
  if (sent.error !== undefined) {
    await fail($, 'al-stats', sent.error)
    return `${figures}\n\n${T.cgStatsQueued(sent.left.length)}`
  }
  try {
    await rebuildSummary($)
  } catch (err) {
    await fail($, 'al-stats-summary', err)
    return `${figures}\n\n${T.cgStatsSummaryFailed}`
  }
  return `${figures}\n\n${T.cgStatsSent(sent.sent)}`
}

// What the band's «База» and «сэкономлено» draw from: asked of cg.py when something changes, never while drawing. A field that did not come is null;
// the first failure of a load goes to /al-health, no toast (a broken index would repeat it every 10 minutes).
let isInfoNoted = false
let loadedAt = 0 // when this load of the mod started: an error older than that is not this load's first cause
let isSyncOn = false // the sync folder is set: a stored sync error means something only then
let infoSeq = 0 // an older, slower answer must not replace a newer one
// The flag goes up before anything is awaited (two failures at once note one); an error of this load stays, it is the first cause (a broken index fails these too).
const noteInfoOnce = async ($: EngineInterface, err: unknown) => {
  if (isInfoNoted) return
  isInfoNoted = true
  const last = (await $.store.get('lastError')) as LastError | undefined
  if (!last || last.at < loadedAt) await noteError($, 'band', err)
}
const refreshInfo = async ($: EngineInterface, python: string) => {
  const seq = ++infoSeq
  try {
    const [id, root] = await Promise.all([$.session.id(), $.session.root()])
    const ask = (args: string[], bad: (err: unknown) => unknown = () => null) => runCg($, python, args).catch(async (err: unknown) => (await noteInfoOnce($, err), bad(err)))
    // a status that fails (cg.py exits 1 for an index it cannot use) keeps its reason: the band must light the base alarm for it
    const [status, savings] = await Promise.all([ask(['status', `--cwd=${root}`], err => ({ index_problem: indexProblem || message(err) })), ask(['savings', '--session', id, '--cwd', root])])
    if (seq === infoSeq) await update($, info, () => ({ status: status as Record<string, unknown>, savings: savings as Savings | null, isSyncOn }))
  } catch (err) {
    await noteInfoOnce($, err).catch(() => {}) // never throws: callers run it bare
  }
}

// Whether the model is mid-turn, as the last band render saw it (only the render is told): /clear then is dangerous.
let isWorking = false

// When this chat's wait comes due; a stored value that is not a time the clock can show counts as no wait.
const waitOf = async ($: EngineInterface, id: string) => {
  const wait = (await $.store.get(`delayed:${id}`)) as { at?: unknown } | undefined
  return typeof wait?.at === 'number' && !Number.isNaN(new Date(wait.at).getTime()) ? wait.at : null
}

// The delayed New worker: the wait is a store key of its chat; the 30 s tick starts it once due. The key goes first, so it never fires twice.
// A turn or a launch in progress leaves the wait where it is for the next tick. The band shows the wait of the chat that is open (a /clear or /resume
// changes the id without a session.start), so a wait goes only after the band has shown it for a tick.
const fireDue = async ($: EngineInterface, python: string, compression: string) => {
  const [id, now] = await Promise.all([$.session.id(), $.clock.now()])
  const at = await waitOf($, id)
  let shown: number | null = null
  await update($, delayedAt, v => ((shown = v), at))
  if (at === null || at > now || isWorking || shown !== at) return
  await $.store.delete(`delayed:${id}`)
  await update($, delayedAt, () => null)
  // false = nothing was launched (busy, or the busy flag could not be set): the wait goes back. A throw from startWorker's tail comes after the launch: no put-back.
  if (await startWorker($, python, 'send', compression)) return
  await $.store.set(`delayed:${id}`, { at })
  await update($, delayedAt, () => at)
}

// "Новый работник": binds launch() to the engine through closures made here. One launch at a time.
const startWorker = async ($: EngineInterface, python: string, mode: 'send' | 'fill', compression: string) => {
  let wasBusy = false
  try {
    await update($, isBusy, busy => ((wasBusy = busy), true)) // atomic: a second press before the redraw is refused here
  } catch (err) {
    await fail($, 'launch', err)
    return false
  }
  if (wasBusy) return false
  let outcome: Awaited<ReturnType<typeof launch>> | undefined
  let isCleared = false
  try {
    await update($, isArmed, () => false)
    let told = '' // the last thing launch told the owner; it is the reason when the launch did not go through
    const io: LaunchIO = {
      draft: async () => (await $.prompt.read()).text,
      handoff: async () => {
        const [id, now, dir] = await Promise.all([$.session.id(), $.clock.now(), memoryDir($)])
        const args = ['handoff', '--session', id, '--today', calendarDay(now), ...(dir ? ['--memory-dir', dir] : []), '--compress', compression]
        return (await runCg($, python, args)) as { text: string; path: string }
      },
      clear: () => ((isCleared = true), $.command.run({ command: 'clear' })),
      submit: text => $.prompt.submit({ text, asUser: true }),
      fill: async text => {
        const r = await $.prompt.fill({ text })
        if (!r.isFilled) throw new Error(T.fillRefused[r.refusal ?? 'other'])
      },
      // the draft stays in the box after submit; clear it unless the owner has typed something new meanwhile
      emptyBox: async draft => {
        if ((await $.prompt.read()).text === draft) await $.prompt.fill({ text: '' })
      },
      toast: text => {
        toast($, text)
        told = text
      },
    }
    outcome = await launch(io, mode)
    if (outcome === 'ready') toast($, T.pickThenEnter)
    if (outcome === 'failed' || outcome === 'dropped') await noteError($, 'launch', told)
  } catch (err) {
    await fail($, 'launch', err)
  } finally {
    // /clear changed the session id: the picker must not stay armed for the new chat
    await update($, isArmed, () => false)
    await update($, isBusy, () => false)
    await guarded($, 'launch', () => refresh($, python))
  }
  return outcome !== undefined || isCleared // a throw before /clear launched nothing: the delayed wait goes back
}

export const register: Register = (on, options) => {
  // the settings: /config's values now, the mod's own file over them once session.start has read it
  let syncDir: string, shareCipher: string, indexCipher: string, minTokens: number, idleMinutes: number, showLimits: boolean, python: string, embedModel: string, compression: Compression
  let isLessonsOn: boolean, lessonsMinScore: number
  const apply = (o: Record<string, unknown>) => {
    setLanguage(o.language as string)
    syncDir = (o.syncDir as string).trim().replace(/^"(.*)"$/, '$1') // Explorer's "Copy as path" puts quotes around it
    isSyncOn = syncDir !== ''
    shareCipher = (o.shareCipher as string | undefined) ?? 'sqlcipher' // sqlcipher or cryptography; any other word is refused by cg.py, not turned into the other
    indexCipher = (o.indexCipher as string | undefined) ?? 'sqlcipher' // sqlcipher or efs; likewise refused by cg.py when unknown
    minTokens = o.bandMinTokens as number
    idleMinutes = o.bandIdleMinutes as number
    showLimits = o.showLimits as boolean
    python = o.python as string
    embedModel = o.embedModel as string
    compression = compressionLevel(o.compression) // what the model reads of the memory: off, lite, full, ultra (stage 3)
    isLessonsOn = o.lessons !== false
    lessonsMinScore = typeof o.lessonsMinScore === 'number' ? o.lessonsMinScore : 0.45
  }
  apply(options)
  let homeDir: string | undefined
  let settingsFile: string | undefined
  let isVectorToasted = false // the owner hears of a missing vector search once per load of the mod, not on every recall
  let isIndexToasted = false // likewise of unreadable transcripts a search came across
  let isCatchUpToasted = false // likewise a failed background catch-up: the status line shows it for 30 s only
  // the lessons of this chat's project (stage 4), read at the start, after a sync and after a save; what each chat was shown or stopped
  let lessonList: Lesson[] = []
  const shownIn = new Map<string, Set<number>>()
  const stoppedIn = new Map<string, Set<number>>()
  const setOf = (m: Map<string, Set<number>>, id: string) => m.get(id) ?? m.set(id, new Set()).get(id)!
  let isLessonsNoted = false // a lesson search that fails on every message is kept for /al-health once, not on each

  on('session.start', async ($, e, next) => {
    loadedAt = await $.clock.now()
    isInfoNoted = false // once per load of the mod
    // first, so the language and every other setting of the file hold before anything is drawn or said; a reload reads it again
    homeDir = (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME'))
    if (homeDir !== undefined) {
      settingsFile = settingsPath(homeDir) // the name for a failure below
      try {
        settingsFile = await settingsFileOf($, homeDir)
        // missing, it is born with every value of the day; shortcut: a default changed later reaches it only once its key is deleted
        if (await $.fs.exists(settingsFile)) {
          const file = withFile(options, await $.fs.read(settingsFile))
          apply(file.options)
          if (file.bad.length) await fail($, 'settings', T.settingsBad(settingsFile, file.bad))
        } else await $.fs.write(settingsFile, `${JSON.stringify(options, null, 2)}\n`)
      } catch (err) {
        await fail($, 'settings', `${settingsFile}: ${message(err)}`)
      }
    }


    // timers outlive /clear (no session.start after it), so one tick also keeps a new session's figures fresh
    $.clock.every(30_000, () => {
      void guarded($, 'timer', () => refresh($, python))
      void guarded($, 'delayed', () => fireDue($, python, compression))
    })
    // a chat open for hours learns of a lesson saved in another chat or brought by the job's sync
    // and the band's data (never awaited together: one slow answer must not hold the other)
    $.clock.every(600_000, () => {
      void lessonsOr($, python, lessonList).then(l => void (lessonList = l))
      void refreshInfo($, python)
    })
    // session.end has 1.5 s, too little to index: a closed chat is picked up here, or by the next recall. Never awaited, so session.start is not held.
    $.clock.after(
      3_000,
      () =>
        void catchUp($, python, embedModel, syncDir, shareCipher).then(async failed => {
          if (failed && !isCatchUpToasted) toast($, T.catchUpFailed(failed))
          if (failed) isCatchUpToasted = true
          lessonList = await lessonsOr($, python, lessonList) // the sync may have brought the other computer's
          await refreshInfo($, python)
        }),
    )
    await guarded($, 'session.start', () => refresh($, python))
    await guarded($, 'session.start', async () => {
      const isOpen = (await $.store.get('details')) === true
      await update($, isDetails, () => isOpen)
      const at = await waitOf($, await $.session.id()) // a wait outlives a restart
      await update($, delayedAt, () => at)
    })

    // after the status line and the timers: a refusal of one schema must not leave the owner without the rest
    // two short schemas in every request cost less than the extra ToolSearch turn a deferred tool needs in each new chat
    const registrations = [
      () => $.command.register({ name: 'al-band', description: T.cgBandDescription, argumentHint: '[off]' }),
      () => $.command.register({ name: 'al-health', description: T.cgHealthDescription }),
      () => $.command.register({ name: 'al-index', description: T.cgIndexDescription, argumentHint: T.cgIndexHint }),
      () => $.command.register({ name: 'al-stats', description: T.cgStatsDescription, argumentHint: T.cgStatsHint }),
      () => $.command.register({ name: 'al-sync', description: T.cgSyncDescription, argumentHint: T.cgSyncHint }),
      () => $.command.register({ name: 'al-lessons', description: T.cgLessonsDescription, argumentHint: T.cgLessonsHint }),
      () => $.command.register({ name: 'al-job', description: T.cgJobDescription, argumentHint: T.cgJobHint }),
      () => $.command.register({ name: 'al-encrypt', description: T.cgEncryptDescription, argumentHint: T.cgEncryptHint }),
      () => $.command.register({ name: 'al-index-dir', description: T.cgIndexDirDescription, argumentHint: T.cgIndexDirHint }),
      () =>
        $.tool.register({
          name: 'recall',
          description: T.recallDescription,
          inputSchema: {
            type: 'object',
            properties: {
              query: { type: 'string', description: T.recallQuery },
              also: { type: 'array', items: { type: 'string' }, maxItems: 3, description: T.recallAlso },
              scope: { type: 'string', enum: ['project', 'all'] },
              limit: { type: 'integer', minimum: 1, maximum: 20 },
            },
            required: ['query'],
          },
          isDeferred: false,
        }),
      () =>
        $.tool.register({
          name: 'recall_more',
          description: T.recallMoreDescription,
          inputSchema: { type: 'object', properties: { conv: { type: 'string' }, pos: { type: 'integer' } }, required: ['conv', 'pos'] },
          isDeferred: false,
        }),
      () =>
        $.tool.register({
          name: 'lesson_save',
          description: T.lessonSaveDescription,
          inputSchema: {
            type: 'object',
            properties: {
              action: { type: 'string', description: T.lessonAction },
              symptom: { type: 'string', description: T.lessonSymptom },
              cause: { type: 'string', description: T.lessonCause },
              fix: { type: 'string', description: T.lessonFix },
              trigger: { type: 'string', description: T.lessonTrigger },
            },
            required: ['action', 'symptom', 'cause'],
          },
          isDeferred: false,
        }),
    ]
    for (const work of registrations) await guarded($, 'register', work)
    void lessonsOr($, python, lessonList).then(l => void (lessonList = l)) // never awaited: a slow Python must not hold session.start

    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    // a subagent's turn or one that cost nothing (interrupted, API error) does not move the cache clock
    if (e.agentId === undefined && e.usage !== undefined) {
      await guarded($, 'turn.complete', async () => {
        // an armed picker never outlives a turn
        await update($, isArmed, () => false)
        const [id, now, usage] = await Promise.all([$.session.id(), $.clock.now(), $.session.usage()])
        const prev = (await $.store.get(`turn:${id}`)) as { at: number | null } | undefined
        await $.store.set(`turn:${id}`, { at: now, tokens: usage.context.tokens ?? null })
        await pruneTurns($)
        await refresh($, python)
        if (!prev?.at) void refreshInfo($, python) // the first reply of a chat (a new worker's): only now has its transcript a call for the savings to read

      })
    }

    return next(e)
  })

  on('command.run', { command: 'al-band' }, async ($, e) => {
    const isOff = e.args.trim() === 'off'
    const isOk = await guarded($, 'al-band', async () => {
      await refresh($, python)
      const id = await $.session.id()
      await update($, hiddenId, () => (isOff ? id : ''))
    })

    return { text: !isOk ? T.cgBandFailed : isOff ? T.cgBandOff : T.cgBandOn }
  })

  on('command.run', { command: 'al-health' }, async $ => {
    try {
      const mem = await memoryFiles($)
      const dir = autoMemDir(mem)
      // the root, as recall scopes by it: this is what «в этом проекте» means
      const root = await $.session.root()
      const [{ version }, index, files, lastError] = await Promise.all([
        $.session.version(),
        runCg($, python, ['--follow-job', 'status', `--cwd=${root}`]).catch((err: Error) => err.message),
        dir === null ? [] : $.fs.list(dir),
        $.store.get('lastError'),
      ])
      const facts = files.filter(f => f.name.endsWith('.md') && f.name !== 'MEMORY.md').length
      const memory = dir === null ? null : { dir, facts, tokens: autoMemTokens(mem) }
      const embedCmd = `${python} "${$.plugin.root}/scripts/cg.py" --lang ${language()} embed --model ${embedModel} --max-seconds 100000`
      const counts = ((await $.store.get('lessonCounts')) as Record<string, number> | undefined) ?? {}
      const lessons = { count: lessonList.length, shown: counts.shown ?? 0, chars: counts.chars ?? 0, stopped: counts.stopped ?? 0, on: isLessonsOn }
      if (homeDir !== undefined) settingsFile = await settingsFileOf($, homeDir) // after the status run, which may have moved the folder
      return {
        text: healthLines({ version, index, memory, root, embedCmd, lastError: lastError as LastError | undefined, syncDir, compression, settingsFile, shareCipher, lessons, homeNotes: typeof index === 'string' ? [] : (index.home_notes as string[] | undefined) }).join('\n'),
      }
    } catch (err) {
      await fail($, 'al-health', err)
      return { text: T.cgHealthFailed }
    }
  })

  on('command.run', { command: 'al-index' }, async ($, e) => {
    try {
      const i = await runCg($, python, ['index', ...(e.args.trim() === 'rebuild' ? ['--rebuild'] : [])], 600_000)
      await indexErrors($, 'al-index', i.errors as IndexError[], true)
      void refreshInfo($, python)
      return { text: T.cgIndexDone(i) }
    } catch (err) {
      await fail($, 'al-index', err)
      return { text: T.cgIndexFailed }
    }
  })

  on('command.run', { command: 'al-sync' }, async ($, e) => {
    if (syncDir === '') return { text: T.cgSyncOff }
    const skip = skipArgs(e.args)
    if (skip === null) return { text: T.cgSyncSkipUsage }
    try {
      const r = await runSync($, python, syncDir, embedModel, shareCipher, skip)
      lessonList = await lessonsOr($, python, lessonList) // the other computer's lessons, if it brought any
      void refreshInfo($, python)
      if (r.errors.length) {
        // the reply lists them all; the toast and /al-health keep the first one for when the reply has scrolled away
        toast($, T.syncErrors(r.errors.length))
        await noteError($, 'al-sync', r.errors[0]!.error)
      }
      return { text: syncReport(r).join('\n') }
    } catch (err) {
      await fail($, 'al-sync', err)
      return { text: T.cgSyncFailed }
    }
  })

  // on, join and off open windows in the Python process (the recovery code, the questions), and the owner's time in them counts. The host
  // cuts a process.run at 10 minutes (1_800_000 is refused outright), so cg.py keeps its own waits near 190 s of locks and 120 s of push
  // (ENCRYPT_WAITS, ENCRYPT_PUSH_SECONDS); a run cut all the same is safe to repeat. The state asks nothing.
  on('command.run', { command: 'al-encrypt' }, async ($, e) => {
    try {
      // a chat that was not reloaded after the settings were edited holds the old ciphers, and the flags below would turn the index back: the file as it is now decides
      if (homeDir !== undefined) settingsFile = await settingsFileOf($, homeDir) // cg.py may have moved the folder since the start
      if (settingsFile && (await $.fs.exists(settingsFile))) apply(withFile(options, await $.fs.read(settingsFile)).options)
    } catch (err) {
      await fail($, 'settings', `${settingsFile}: ${message(err)}`)
    }
    const args = encryptArgs(e.args, syncDir, shareCipher, indexCipher, embedModel)
    if (args === null) return { text: T.cgEncryptUsage }
    const isState = args[1] === 'status'
    try {
      if (!isState) toast($, T.encryptWindow)
      const r = await runCg($, python, args, isState ? 120_000 : 600_000)
      if (!isState) void refreshInfo($, python)
      return { text: isState ? encryptHealth(r as unknown as EncryptStatus, shareCipher).join('\n') : encryptReply(r as unknown as EncryptResult) }
    } catch (err) {
      // cut at the time limit: not "Python did not start" (what runCg says of any failure of the process), but what to do
      const cut = !isState && isTimeout(message(err)) ? T.encryptTimeout(args[1]!) : undefined
      await fail($, 'al-encrypt', cut ?? err)
      return { text: cut === undefined ? T.cgEncryptFailed : `al-encrypt ${args[1]}: ${cut}` }
    }
  })

  // Without a path a window asks for the folder (and a Yes/No box about the old copy), so the owner's time counts: the same 10 minutes as encrypt.
  // A refusal (not a local disk, no space, a merge not done yet, busy) is what the owner must act on, so it is in the reply, not in a toast alone.
  on('command.run', { command: 'al-index-dir' }, async ($, e) => {
    try {
      if (indexDirArgs(e.args).length === 1) toast($, T.indexDirWindow) // only the run without a path opens the window
      const r = (await runCg($, python, indexDirArgs(e.args), 600_000)) as unknown as IndexDirResult
      void refreshInfo($, python)
      return { text: indexDirReply(r) }
    } catch (err) {
      const cut = isTimeout(message(err)) ? T.indexDirTimeout : undefined
      await fail($, 'al-index-dir', cut ?? err)
      return { text: indexDirRefusal(cut ?? message(err)) }
    }
  })

  on('command.run', { command: 'al-stats' }, async ($, e) => {
    const days = Number(e.args.trim() || 7)
    if (!Number.isInteger(days) || days < 1) return { text: T.cgStatsDays }
    try {
      return { text: await runStats($, python, days, compression) }
    } catch (err) {
      await fail($, 'al-stats', err)
      return { text: T.cgStatsFailed }
    }
  })

  on('tool.call', { tool: 'mcp__alethite__recall' }, ($, e) =>
    served($, 'recall', async () => {
      const input = e as unknown as { query: string; also?: unknown; scope?: string; limit?: number }
      if (typeof input.query !== 'string' || !input.query.trim()) throw new Error(T.noQuery) // the schema asks for it, but nothing makes the model obey
      // the root, not cwd(): a shell `cd` into a subfolder moves cwd but not the project the conversations are filed under
      const r = await runCg($, python, recallArgs(input, await $.session.root(), embedModel, compression))
      // a transcript that stays unreadable would toast on every search: once per load, as the vector search below
      if (!isIndexToasted && (r.errors as IndexError[]).length) {
        isIndexToasted = true
        await indexErrors($, 'recall', r.errors as IndexError[], true)
      }
      const hits = formatHits(r.hits as Hit[], input.query, input.scope ?? 'project')
      if (r.vector === 'ok') return hits
      // only the words were searched: the model reads why every time; the owner gets a toast once per load, and /al-health keeps
      // that cause without it pushing out every later error
      const off = T.vectorOff(String(r.vector))
      if (!isVectorToasted) {
        isVectorToasted = true // before the await: two searches at once must not both toast
        toast($, off)
        await noteError($, 'recall', off)
      }
      return `${hits}\n${off}`
    }),
  )

  on('tool.call', { tool: 'mcp__alethite__recall_more' }, ($, e) =>
    served($, 'recall_more', async () => formatRows((await runCg($, python, ['expand', `--conv=${e.conv}`, `--pos=${e.pos}`, '--compress', compression])).rows as Row[])),
  )

  on('tool.call', { tool: 'mcp__alethite__lesson_save' }, ($, e) =>
    served($, 'lesson_save', async () => {
      const input = e as unknown as Record<string, unknown>
      const r = await runCg($, python, lessonSaveArgs(input, await $.session.root(), embedModel))
      lessonList = await lessonsOr($, python, lessonList)
      if (!r.new) return T.lessonKnown
      toast($, T.lessonToast(oneLine(String(r.text))))
      return T.lessonSaved(String(r.text))
    }, T.lessonSaveFailed),
  )

  on('command.run', { command: 'al-job' }, async ($, e) => {
    const args = jobArgs(e.args)
    if (args === null) return { text: T.cgJobUsage }
    try {
      const r = await runCg($, python, args, 60_000)
      if (args[1] === 'install') return { text: T.cgJobOn(r.minutes as number) }
      if (args[1] === 'remove') return { text: T.cgJobOff }
      return { text: [T.cgJobState(r.minutes as number | null), ...jobHealth(r.last as JobRun | null)].join('\n') }
    } catch (err) {
      await fail($, 'al-job', err)
      return { text: T.cgJobFailed }
    }
  })

  on('command.run', { command: 'al-lessons' }, async ($, e) => {
    const pos = forgetArg(e.args)
    if (pos === undefined) return { text: T.cgLessonsUsage }
    try {
      lessonList = await lessonsOf($, python)
      if (pos === null) return { text: formatLessons(lessonList) }
      const lesson = lessonList.find(l => l.pos === pos)
      const n = lesson ? ((await runCg($, python, ['forget', `--conv=${lesson.conv}`, `--pos=${pos}`])).forgotten as number) : 0
      lessonList = await lessonsOf($, python)
      return { text: T.cgLessonsForgotten(n) }
    } catch (err) {
      await fail($, 'al-lessons', err)
      return { text: T.cgLessonsFailed }
    }
  })

  // The lessons that fit the owner's message go to the model beside it, in the hidden context, not the system prompt: the prompt cache
  // stays whole. Only the owner's own words; nothing when the project has no lessons (no Python then), when none is close enough, or
  // when the chat was shown it already. A failure costs the owner nothing but a line in /al-health.
  on('prompt.submit', async ($, e, next) => {
    // an engine older than the origin (2.1.29x) stamps none: there a prompt is the owner's; in the desktop Code tab (an SDK host) it is `sdk`, or `unclassified` from a channel the engine cannot attest
    const kind = (e.origin as { kind?: string } | undefined)?.kind
    const isOwners = kind === undefined || kind === 'composer' || kind === 'bridge' || kind === 'sdk' || kind === 'unclassified'
    if (isOwners && !e.isMeta) await recheck($, python)
    if (!isLessonsOn || lessonList.length === 0 || e.command !== undefined || e.isMeta || !isOwners || e.text.trim().length < 12) return next(e)
    let block: string | undefined
    try {
      const [root, id] = await Promise.all([$.session.root(), $.session.id()])
      const shown = setOf(shownIn, id)
      // every lesson shown already: no Python; else ask for the shown ones too, so they do not take the places of the fresh ones
      const found = lessonList.every(l => shown.has(l.id))
        ? []
        : ((await runCg($, python, lessonsMatchArgs(e.text, root, embedModel, lessonsMinScore, 2 + shown.size), 8_000)).lessons as Found[])
      const fresh = found.filter(l => !shown.has(l.id)).slice(0, 2)
      if (fresh.length) {
        block = lessonContext(fresh)
        for (const l of fresh) shown.add(l.id)
        await countLessons($, 'shown', fresh.length)
        await countLessons($, 'chars', block.length)
      }
    } catch (err) {
      if (!isLessonsNoted) await noteError($, 'lessons', err)
      isLessonsNoted = true
    }
    return next(block === undefined ? e : { ...e, context: [...(e.context ?? []), block] })
  })

  // A Bash or PowerShell command that holds a lesson's trigger is stopped once in a chat, with the lesson; the same command again passes.
  // Whatever goes wrong here lets the command run: the lessons must never be what stops the owner's work.
  on('tool.call', { tool: ['Bash', 'PowerShell'] }, async ($, e, next) => {
    if (!isLessonsOn || lessonList.length === 0) return next(e)
    let lesson: Lesson | undefined
    try {
      const stopped = setOf(stoppedIn, await $.session.id())
      lesson = gateFor(e.command, lessonList, stopped)
      if (lesson !== undefined) {
        stopped.add(lesson.id)
        await countLessons($, 'stopped', 1)
      }
    } catch {
      lesson = undefined
    }
    // between the marks: a denial the engine records as an error result must not enter the index as the lesson's text a second time (transcripts.py)
    return lesson === undefined ? next(e) : { deny: [START, T.lessonGate(oneLine(lesson.text)), END].join('\n') }
  })

  // After a compaction the model no longer has what it was shown: this chat's lessons may be shown and may stop a command once more.
  // Only the main conversation's own, finished compaction (a subagent's, a precompute and a skipped one leave the chat as it was); the
  // result goes back whatever happens here.
  on('session.compact', async ($, e, next) => {
    const r = await next(e)
    try {
      if (e.trigger !== 'precompute' && e.agentId === undefined && r.skip === undefined) {
        const id = await $.session.id()
        shownIn.delete(id)
        stoppedIn.delete(id)
      }
    } catch {}
    return r
  })

  // last and on the session side: every shared section must precede it, and the text never varies, so the prompt cache is not spent
  on('prompt.compose', async (_, e, next) => ({ sections: [...(await next(e)).sections, { id: 'alethite:memory-rule', text: T.memoryRule, scope: 'session' }] }))

  // The worker's or a subagent's write into the auto-memory folder; the owner's Write/Edit result always comes back as it was.
  on('tool.call', { tool: ['Write', 'Edit'] }, async ($, e, next) => {
    const ran = await next(e)
    await guarded($, 'remembered', async () => {
      if (ran.deny === undefined && !ran.isError && ran.result?.staged !== true && isMemoryFact(e.file_path)) toast($, T.remembered(e.file_path.split(/[\\/]/).pop()!.slice(0, -3)))
    })
    return ran
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    // every read first, so the band is subscribed to all of them even when it draws nothing
    const s = await read($, snap)
    const hidden = await read($, hiddenId)
    const calm = await read($, calmId)
    const armed = await read($, isArmed)
    const busy = await read($, isBusy)
    const wait = await read($, delayedAt)
    const data = await read($, info)
    const isOpen = await read($, isDetails)

    isWorking = e.props.isWorking // /clear in the middle of a turn is dangerous: no New worker then, and no waiting one either
    if (s === null || e.props.hasSurvey || s.id === hidden) return next(e)

    const { Box, Button, Text } = $.ui.resolve(e)
    // the band above the prompt is one area for every mod: ours is a row or a frame of its own, and what the mods beneath draw stays under it
    const below = await next(e)
    const isLit = isStale(s, minTokens, idleMinutes) && s.id !== calm
    const title = <Text bold>{T.bandTitle}</Text>
    const after = delayAt(s.limits, s.now)

    if (armed && !isWorking) {
      return (
        <Box flexDirection="column">
          <Box borderStyle="round" borderDimColor flexDirection="column" paddingX={1}>
            {title}
            <Text>{T.willClear}</Text>
            <Box gap={1} flexWrap="wrap">
              <Button key="send" label={T.sendNow} onPress={() => startWorker($, python, 'send', compression)} />
              <Button key="pick" label={T.pickInMenu} onPress={() => startWorker($, python, 'fill', compression)} />
              {after !== null && (
                <Button
                  key="after"
                  label={T.afterReset(when(new Date(after).toISOString(), s.now))}
                  onPress={() =>
                    guarded($, 'band', async () => {
                      await update($, isArmed, () => false)
                      if (!(await $.prompt.read()).text.trim()) return toast($, T.draftEmpty)
                      await $.store.set(`delayed:${await $.session.id()}`, { at: after }) // the time printed on the button; the chat that is open now (a /clear may have come)
                      await update($, delayedAt, () => after)
                    })
                  }
                />
              )}
              <Button key="cancel" label={T.cancel} onPress={() => update($, isArmed, () => false)} />
            </Box>
            <Text dimColor>{T.atFilesHint}</Text>
          </Box>
          {below}
        </Box>
      )
    }

    const launchLabel = busy ? T.busy : T.newWorker
    const detailsLabel = isOpen ? T.compact : T.details
    const toggle = () => guarded($, 'band', async () => (await update($, isDetails, () => !isOpen), await $.store.set('details', !isOpen)))
    const waitText = wait === null ? '' : T.waitingAt(when(new Date(wait).toISOString(), s.now))
    const cancelWait = () => guarded($, 'band', async () => (await $.store.delete(`delayed:${await $.session.id()}`), await update($, delayedAt, () => null)))
    const actions = (wrap: boolean, hasDetails = true) => (
      <Box gap={1} flexWrap={wrap ? 'wrap' : undefined}>
        {wait !== null && <Text color="warning">{waitText}</Text>}
        {wait !== null && <Button key="cancel-delay" label={T.cancel} onPress={cancelWait} />}
        {wait === null && !isWorking && <Button key="launch" label={launchLabel} onPress={() => (busy ? undefined : update($, isArmed, () => true))} />}
        {wait === null && !isWorking && isLit && <Button key="stay" label={T.stayHere} onPress={() => update($, calmId, () => s.id)} />}
        {hasDetails && <Button key="details" label={detailsLabel} onPress={toggle} />}
      </Box>
    )

    if (!isOpen) {
      // the terminal counts cells; the desktop draws wider and does not wrap, so only the terminal trims the tokens to the room the buttons leave
      const buttons = wait !== null ? [waitText, T.cancel, detailsLabel] : [launchLabel, ...(isLit ? [T.stayHere] : []), detailsLabel].slice(isWorking ? -1 : 0)
      // the row is the title, the tokens ("· " + text), a spacer and the buttons, one gap between neighbours; a Button draws `[ label ]`, its label plus 4.
      // The tokens' ' · ' joins in tokensWidth are 3 cells short of what they take in the row, and the spacer's gap is one more.
      const room = (rowTitle: string) => (e.surface === 'terminal' ? e.props.bodyColumns - rowTitle.length - buttons.join('').length - 5 * buttons.length - 4 : Infinity)
      const all = lineTokens(s, data, isLit)
      let [rowTitle, left] = [T.bandTitle, room(T.bandTitle)]
      let tokens = fitTokens(all, left)
      if (tokensWidth(tokens) > left) [rowTitle, left, tokens] = [SHIELD, room(SHIELD), fitTokens(all, room(SHIELD))] // not even the base alarm fits beside the name: the shield alone
      // Details goes too when a waiting row still cannot hold it (the row stays one line)
      return (
        <Box flexDirection="column">
          <Box gap={1}>
            <Text bold>{rowTitle}</Text>
            {tokens.map(t => (
              <Text key={t.key} color={t.tone}>{`· ${t.text}`}</Text>
            ))}
            <Box flexGrow={1} />
            {actions(false, wait === null || left >= 0)}
          </Box>
          {below}
        </Box>
      )
    }

    const cols = columns(s, data, showLimits)
    let body = (
      <Box gap={2} flexWrap="wrap">
        {cols.map(c => (
          <Box key={c.key} flexDirection="column">
            <Text dimColor>{c.label}</Text>
            <Text bold color={c.tone}>{c.value}</Text>
            {c.sub.map(line => (
              <Text key={line} dimColor wrap="truncate-end">{line}</Text>
            ))}
          </Box>
        ))}
      </Box>
    )
    // box characters break in the desktop's proportional font: one image there
    if (e.surface === 'desktop') {
      const { Svg } = $.ui.resolve(e)
      body = <Svg source={columnsSvg(cols)} alt={cols.map(c => `${c.label} ${c.value}`).join(', ')} />
    }
    return (
      <Box flexDirection="column">
        <Box borderStyle="round" borderDimColor flexDirection="column" paddingX={1}>
          {title}
          {body}
          {actions(true)}
        </Box>
        {below}
      </Box>
    )
  })
}
