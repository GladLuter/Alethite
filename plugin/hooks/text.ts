// Every string the mod shows or tells the model: one dictionary per language in ./lang, English the reference.
// A new language is one more file there and its line below; a key it lacks is taken from English.
import { en } from './lang/en'
import type { Texts } from './lang/en'
import { ru } from './lang/ru'

export type { Texts }

export const LANGUAGES: Record<string, Partial<Texts>> = { en, ru }

// The texts of the language chosen in /config, filled in from English. One object that register() fills before anything is drawn or
// said, so every file that imports it reads the chosen language.
export const T: Texts = { ...en }
let current = 'en'

export const setLanguage = (code: string, dictionary: Partial<Texts> | undefined = LANGUAGES[code]) => {
  current = dictionary === undefined ? 'en' : code
  Object.assign(T, en, dictionary ?? {})
}

// The language cg.py is told with --lang: its own tables go by the same codes.
export const language = () => current
