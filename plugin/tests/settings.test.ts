import { expect, test } from 'claude-code/testing'
import { oldSettingsPath, settingsPath, withFile } from '../hooks/settings'
import { T } from '../hooks/text'

const options = { language: 'en', syncDir: '', bandMinTokens: 100000, showLimits: true }

test('settings path: in the folder of the mod, with the separator of the home folder; the old folder has its own path', () => {
  expect(settingsPath('C:\\Users\\A')).toBe('C:\\Users\\A\\.claude\\alethite\\settings.json')
  expect(settingsPath('/home/a')).toBe('/home/a/.claude/alethite/settings.json')
  expect(oldSettingsPath('C:\\Users\\A')).toBe('C:\\Users\\A\\.claude\\cacheguard\\settings.json')
  expect(oldSettingsPath('/home/a')).toBe('/home/a/.claude/cacheguard/settings.json')
})

test('settings file: its values over the options; a key that is no field or of another type is left out and named', () => {
  const r = withFile(options, '\uFEFF{ "language": "ru", "syncDir": "Z:\\\\cg", "bandMinTokens": "5", "colour": 1, "showLimits": false }')
  expect(r.options).toEqual({ language: 'ru', syncDir: 'Z:\\cg', bandMinTokens: 100000, showLimits: false })
  expect(r.bad).toEqual(['bandMinTokens', 'colour'])
})

test('settings file: the keys cg.py keeps there are no options and are not reported as bad keys', () => {
  const r = withFile(options, '{"language": "ru", "encryptionKey": "dpapi:AAAA", "indexDir": "D:\\\\Alethite"}')
  expect(r.options).toEqual({ ...options, language: 'ru' })
  expect(r.bad).toEqual([])
})

test('settings file: broken JSON or not one object is an error', () => {
  expect(() => withFile(options, '{ "language": "ru", }')).toThrow()
  expect(() => withFile(options, '["ru"]')).toThrow(T.settingsNotObject)
  expect(() => withFile(options, 'null')).toThrow(T.settingsNotObject)
})

test('projectAliases in the file is not reported', () => {
  const r = withFile(options, '{"language": "ru", "projectAliases": {"C:\\\\_git\\\\Alethite": ["C:\\\\_git\\\\Claude-CacheGuard"]}}')
  expect(r.options).toEqual({ ...options, language: 'ru' })
  expect(r.bad).toEqual([])
})
