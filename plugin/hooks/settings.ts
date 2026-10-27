// The mod's own settings file, one per computer, beside the index: %USERPROFILE%\.claude\cacheguard\settings.json.
// Its keys are the manifest's userConfig fields; a value there wins over /config (pluginConfigs in Claude Code's settings.json).
import { T } from './text'

export const settingsPath = (home: string) => [home, '.claude', 'cacheguard', 'settings.json'].join(home.includes('/') ? '/' : '\\')

// Keys cg.py writes into the file itself (the encryption key under DPAPI, the index folder): no option, never reported, never shown.
const SILENT = ['encryptionKey', 'indexDir']

// The options with the file's values over them, and the file's keys that are not a field or not of the field's type, left out.
export const withFile = (options: Record<string, unknown>, text: string) => {
  const file: unknown = JSON.parse(text.replace(/^﻿/, '')) // Windows editors and PowerShell save UTF-8 with a BOM
  if (typeof file !== 'object' || file === null || Array.isArray(file)) throw new Error(T.settingsNotObject)
  const isField = ([k, v]: [string, unknown]) => k in options && typeof v === typeof options[k]
  const entries = Object.entries(file)
  return { options: { ...options, ...Object.fromEntries(entries.filter(isField)) }, bad: entries.filter(e => !isField(e) && !SILENT.includes(e[0])).map(([k]) => k) }
}
