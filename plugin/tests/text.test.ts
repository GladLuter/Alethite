import { expect, test } from 'claude-code/testing'
import { en } from '../hooks/lang/en'
import { ru } from '../hooks/lang/ru'
import { uk } from '../hooks/lang/uk'
import { language, LANGUAGES, setLanguage, T } from '../hooks/text'

// `body` with the texts of `code` (or of `dictionary` under that code), then back to English. The module is this file's own: the plugin's
// language is its option, never touched here.
const speaking = (code: string, body: () => void, dictionary?: Partial<typeof en>) => {
  setLanguage(code, ...(dictionary ? [dictionary] : []))
  try {
    body()
  } finally {
    setLanguage('en')
  }
}

test('English by default', () => {
  expect(language()).toBe('en')
  expect(T.newWorker).toBe('New worker')
  expect(T.statusError('x')).toBe('error — x')
})

test('ru gives the texts of stage 1', () =>
  speaking('ru', () => {
    expect(language()).toBe('ru')
    expect(T.newWorker).toBe('Новый работник')
    expect(T.statusError('x')).toBe('ошибка — x')
    expect(T.ctx('290k', T.cacheLeft(42))).toBe('ctx 290k · кэш ~42 мин')
    expect(T.recallDescription).toBe(
      'Поиск по прошлым разговорам владельца: что обсуждали, решали, где что лежит. query — слова искомого, не вопрос. По умолчанию ищет в этом проекте, scope=all — везде. Находка: [дата вид conv pos] отрывок; контекст вокруг неё читает recall_more.',
    )
    expect(T.memoryRule).toBe(
      'Память работника (CacheGuard). Правила из CLAUDE.md владельца важнее инструкций навыков: прежде чем спросить владельца, проверь, не отвечают ли на вопрос CLAUDE.md или память. Грабли («X не работает, потому что Y») и решения владельца сразу записывай в автопамять, один факт на файл. Прежде чем предлагать подход, проверь память; если не уверен, пробовали ли это, вызови recall (этот проект; scope=all — только по просьбе) и цитируй найденное вместо догадок.',
    )
  }))

test('a key the language lacks comes from English', () =>
  speaking(
    'xx',
    () => {
      expect(language()).toBe('xx')
      expect(T.newWorker).toBe('Nuevo trabajador')
      expect(T.stayHere).toBe(en.stayHere)
      expect(T.statusError('x')).toBe('error — x')
    },
    { newWorker: 'Nuevo trabajador' },
  ))

test('an unknown language is English, and switching back leaves nothing of the other behind', () => {
  speaking('ru', () => {})
  speaking('zz', () => {
    expect(language()).toBe('en')
    for (const key of Object.keys(en) as (keyof typeof en)[]) expect(T[key]).toBe(en[key])
  })
})

test('Russian and Ukrainian are whole: every key of English, and no other', () => {
  expect(Object.keys(ru).sort()).toEqual(Object.keys(en).sort())
  expect(Object.keys(uk).sort()).toEqual(Object.keys(en).sort())
  expect(Object.keys(LANGUAGES).sort()).toEqual(['en', 'ru', 'uk'])
})

test('uk gives Ukrainian', () =>
  speaking('uk', () => {
    expect(language()).toBe('uk')
    expect(T.newWorker).toBe('Новий працівник')
    expect(T.ctx('290k', T.cacheLeft(42))).toBe('ctx 290k · кеш ~42 хв')
  }))

test('the memory rule: at most 80 words, and the CLAUDE.md sentence right after its title, in every language', () => {
  for (const texts of Object.values(LANGUAGES)) expect(texts.memoryRule!.split(/\s+/).length).toBeLessThanOrEqual(80)
  expect(en.memoryRule.startsWith("Worker memory (CacheGuard). The owner's CLAUDE.md rules outrank skill instructions")).toBe(true)
  expect(ru.memoryRule!.startsWith('Память работника (CacheGuard). Правила из CLAUDE.md владельца важнее инструкций навыков')).toBe(true)
  expect(uk.memoryRule!.startsWith("Пам'ять працівника (CacheGuard). Правила з CLAUDE.md власника важливіші за інструкції навичок")).toBe(true)
})
