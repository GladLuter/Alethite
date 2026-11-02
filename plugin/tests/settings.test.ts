import { expect, test } from 'claude-code/testing'
import { settingsPath, withFile } from '../hooks/settings'
import { T } from '../hooks/text'

const options = { language: 'en', syncDir: '', bandMinTokens: 100000, showLimits: true }

test('settings path: beside the index, with the separator of the home folder', () => {
  expect(settingsPath('C:\\Users\\A')).toBe('C:\\Users\\A\\.claude\\cacheguard\\settings.json')
  expect(settingsPath('/home/a')).toBe('/home/a/.claude/cacheguard/settings.json')
})

test('settings file: its values over the options; a key that is no field or of another type is left out and named', () => {
  const r = withFile(options, '\uFEFF{ "language": "ru", "syncDir": "Z:\\\\cg", "bandMinTokens": "5", "colour": 1, "showLimits": false }')
  expect(r.options).toEqual({ language: 'ru', syncDir: 'Z:\\cg', bandMinTokens: 100000, showLimits: false })
  expect(r.bad).toEqual(['bandMinTokens', 'colour'])
})

test('settings file: the keys cg.py keeps there are no options and are not reported as bad keys', () => {
  const r = withFile(options, '{"language": "ru", "encryptionKey": "dpapi:AAAA", "indexDir": "D:\\\\CacheGuard"}')
  expect(r.options).toEqual({ ...options, language: 'ru' })
  expect(r.bad).toEqual([])
})

test('settings file: indexCipher is a field like shareCipher: its value wins and it is not reported', () => {
  const r = withFile({ ...options, indexCipher: 'sqlcipher' }, '{"indexCipher": "efs"}')
  expect(r.options).toEqual({ ...options, indexCipher: 'efs' })
  expect(r.bad).toEqual([])
})

test('settings file: broken JSON or not one object is an error', () => {
  expect(() => withFile(options, '{ "language": "ru", }')).toThrow()
  expect(() => withFile(options, '["ru"]')).toThrow(T.settingsNotObject)
  expect(() => withFile(options, 'null')).toThrow(T.settingsNotObject)
})
