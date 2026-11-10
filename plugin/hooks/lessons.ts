// Pure side of the lessons (stage 4): a past error and what it taught, kept in the index and not in the auto-memory, which goes into
// every request. The model saves one with lesson_save; the mod brings it back mechanically and only where it fits: in the hidden context
// of an owner's message close to it, and as a stop before a command that holds its trigger. No model call either way.
// Everything that touches `$` lives in register.tsx: the loader follows `$` only inside its own file.
import { T } from './text'

export type Lesson = { id: number; conv: string; pos: number; ts: string; text: string; trigger: string | null; from: string | null; fix: string | null; global: boolean }
export type Found = { id: number; conv: string; pos: number; text: string; score: number }

export const FIELDS = ['action', 'symptom', 'cause', 'fix', 'trigger'] as const
// the marks the index cuts out of the owner's words (transcripts.py), whatever the engine keeps of the context
export const START = '[al-lessons]'
export const END = '[/al-lessons]'
export const RULES_CHARS = 1000 // the rules of the owner for all projects, the lines together (with the "- ", without the intro)
export const RULES_EVERY = 20_000 // context tokens between two reminders of them
const LESSON_CHARS = 600 // one lesson in the context; two at most a message: about 300 tokens when they fit, none when they do not

// Each value one `--field=…` item: one starting with `-` must not be read as a flag. An empty or a non-text value is left out, and
// cg.py says what is missing.
export const lessonSaveArgs = (input: Record<string, unknown>, cwd: string, model: string) => [
  'lesson-save',
  '--cwd',
  cwd,
  '--model',
  model,
  ...FIELDS.flatMap(k => {
    const v = input[k]
    return typeof v === 'string' && v.trim() ? [`--${k}=${v}`] : []
  }),
  ...(input.scope === 'all' ? ['--scope=all'] : []),
]

// The owner waits for this one: 3 s for Ollama inside cg.py, a cold model is skipped rather than waited for.
export const lessonsMatchArgs = (text: string, cwd: string, model: string, minScore: number, limit: number) => [
  'lessons-match',
  '--cwd',
  cwd,
  `--text=${text.slice(0, 2000)}`,
  '--model',
  model,
  '--min-score',
  String(minScore),
  '--limit',
  String(limit),
  '--timeout',
  '3',
]

export const oneLine = (text: string) => text.split('\n').join(' | ')
const clip = (s: string) => (s.length <= LESSON_CHARS ? s : `${s.slice(0, LESSON_CHARS - 1)}…`)

// What the model reads beside the owner's message: the lessons that fit, between the marks.
export const lessonContext = (found: Found[]) => [START, T.lessonsIntro, ...found.map(l => `- ${clip(oneLine(l.text))}`), END].join('\n')

// The lessons for all projects, as the one-line rules they carry (`fix`), newest first, while they fit RULES_CHARS; `left` did not fit.
export const rulesContext = (lessons: readonly Lesson[]) => {
  const all = lessons.filter(l => l.global && l.fix !== null).map(l => `- ${l.fix}`)
  let used = 0
  const lines = all.filter(line => (used += line.length) <= RULES_CHARS)
  return { block: lines.length ? [START, T.rulesIntro, ...lines, END].join('\n') : undefined, left: all.length - lines.length }
}

// Whether the rules go with this message. `last`: the context size at the last reminder (undefined: none yet in this chat, null: its size was
// unknown); `tokens`: the size now (null: unknown). Due on the first message, after the context shrank (/compact) and every `every` tokens.
export const rulesDue = (last: number | null | undefined, tokens: number | null, every: number) => {
  if (last === undefined) return { due: true, mark: tokens }
  if (tokens === null) return { due: false, mark: last }
  if (last === null) return { due: false, mark: tokens }
  return tokens < last || tokens - last >= every ? { due: true, mark: tokens } : { due: false, mark: last }
}

const TRIGGER_MIN = 8 // as in lessons.py: a lesson from another computer or an older build may hold a shorter one, which would stop every command

// The lesson whose trigger the command holds (any case), if one does and it has not stopped a command in this chat yet.
export const gateFor = (command: unknown, lessons: readonly Lesson[], stopped: ReadonlySet<number>) => {
  if (typeof command !== 'string') return undefined
  const c = command.toLowerCase()
  return lessons.find(l => l.trigger !== null && l.trigger.length >= TRIGGER_MIN && !stopped.has(l.id) && c.includes(l.trigger.toLowerCase()))
}

// /al-lessons: one line each, newest first, with the pos `forget` takes and the computer it came from.
export const formatLessons = (lessons: readonly Lesson[]) =>
  lessons.length === 0 ? T.noLessons : lessons.map(l => `pos=${l.pos} [${l.ts.slice(0, 10)}${l.from ? ` ${l.from}` : ''}] ${l.global ? T.lessonGlobalMark : ''}${oneLine(l.text)}`).join('\n')

// `/al-lessons forget 1792…` → the pos; null for a plain list; undefined for anything else.
export const forgetArg = (args: string) => {
  const a = args.trim()
  if (a === '') return null
  const m = /^forget\s+(\d+)$/.exec(a)
  return m ? Number(m[1]) : undefined
}
