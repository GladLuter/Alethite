// Pure side of the lessons (stage 4): a past error and what it taught, kept in the index and not in the auto-memory, which goes into
// every request. The model saves one with lesson_save; the mod brings it back mechanically and only where it fits: in the hidden context
// of an owner's message close to it, and as a stop before a command that holds its trigger. No model call either way.
// Everything that touches `$` lives in register.tsx: the loader follows `$` only inside its own file.
import { T } from './text'

export type Lesson = { id: number; conv: string; pos: number; ts: string; text: string; trigger: string | null; from: string | null }
export type Found = { id: number; conv: string; pos: number; text: string; score: number }

export const FIELDS = ['action', 'symptom', 'cause', 'fix', 'trigger'] as const
// the marks the index cuts out of the owner's words (transcripts.py), whatever the engine keeps of the context
export const START = '[cg-lessons]'
export const END = '[/cg-lessons]'
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

const TRIGGER_MIN = 8 // as in lessons.py: a lesson from another computer or an older build may hold a shorter one, which would stop every command

// The lesson whose trigger the command holds (any case), if one does and it has not stopped a command in this chat yet.
export const gateFor = (command: unknown, lessons: readonly Lesson[], stopped: ReadonlySet<number>) => {
  if (typeof command !== 'string') return undefined
  const c = command.toLowerCase()
  return lessons.find(l => l.trigger !== null && l.trigger.length >= TRIGGER_MIN && !stopped.has(l.id) && c.includes(l.trigger.toLowerCase()))
}

// /cg-lessons: one line each, newest first, with the pos `forget` takes and the computer it came from.
export const formatLessons = (lessons: readonly Lesson[]) =>
  lessons.length === 0 ? T.noLessons : lessons.map(l => `pos=${l.pos} [${l.ts.slice(0, 10)}${l.from ? ` ${l.from}` : ''}] ${oneLine(l.text)}`).join('\n')

// `/cg-lessons forget 1792…` → the pos; null for a plain list; undefined for anything else.
export const forgetArg = (args: string) => {
  const a = args.trim()
  if (a === '') return null
  const m = /^forget\s+(\d+)$/.exec(a)
  return m ? Number(m[1]) : undefined
}
