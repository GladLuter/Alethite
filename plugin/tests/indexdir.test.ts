import { expect, test } from 'claude-code/testing'
import { indexDirArgs, indexDirRefusal, indexDirReply } from '../hooks/indexdir'
import { T } from '../hooks/text'

test('index dir args: no path is the window; a path goes as --path= without the quotes around it, whatever it starts with', () => {
  expect(indexDirArgs('')).toEqual(['index-dir'])
  expect(indexDirArgs('   ')).toEqual(['index-dir'])
  expect(indexDirArgs('D:\\X')).toEqual(['index-dir', '--path=D:\\X'])
  expect(indexDirArgs(' "D:\\Мои файлы\\X" ')).toEqual(['index-dir', '--path=D:\\Мои файлы\\X'])
  expect(indexDirArgs("'D:\\X'")).toEqual(['index-dir', '--path=D:\\X'])
  expect(indexDirArgs('-x')).toEqual(['index-dir', '--path=-x']) // not read as a flag
  expect(indexDirArgs('D:\\Мои файлы\\X')).toEqual(['index-dir', '--path=D:\\Мои файлы\\X'])
})

test('index dir args: empty quotes are no path (the window), spaces inside the quotes are part of the path', () => {
  expect(indexDirArgs('""')).toEqual(['index-dir'])
  expect(indexDirArgs("''")).toEqual(['index-dir'])
  expect(indexDirArgs(' "" ')).toEqual(['index-dir'])
  expect(indexDirArgs('" D:\\X "')).toEqual(['index-dir', '--path= D:\\X '])
  expect(indexDirArgs('"  "')).toEqual(['index-dir', '--path=  '])
})

test('index dir reply: done names the folder, unfinished says so; each note follows with a capital', () => {
  expect(indexDirReply({ action: 'index-dir', done: true, dir: 'D:\\X', notes: [] })).toBe(T.indexDirDone('D:\\X'))
  expect(indexDirReply({ action: 'index-dir', done: true, dir: 'D:\\X', notes: ['one', 'two'] })).toBe([T.indexDirDone('D:\\X'), 'One', 'Two'].join('\n'))
  expect(indexDirReply({ action: 'index-dir', done: false, dir: '', notes: ['n1'] })).toBe([T.indexDirUnfinished, 'N1'].join('\n'))
  expect(indexDirReply({ action: 'index-dir', done: false, dir: '', notes: ['индекс остаётся'] })).toBe([T.indexDirUnfinished, 'Индекс остаётся'].join('\n'))
  expect(T.indexDirDone('D:\\X')).toContain('D:\\X')
})

test('index dir reply: an index that was there already is not announced as moved, only its note is', () => {
  const r = { action: 'index-dir', done: true, dir: 'D:\\X', here: true, notes: ['the index is in D:\\X already, nothing was moved', 'the old copy stays'] }
  expect(indexDirReply(r)).toBe('The index is in D:\\X already, nothing was moved\nThe old copy stays')
})

test('index dir refusal: the reason of cg.py without its Python exception name; any other text as it is', () => {
  expect(indexDirRefusal('ValueError: D:\\X is not a local disk. Nothing was changed')).toBe('al-index-dir: D:\\X is not a local disk. Nothing was changed')
  expect(indexDirRefusal('PermissionError: [WinError 5] denied')).toBe('al-index-dir: [WinError 5] denied')
  expect(indexDirRefusal('Exception: boom')).toBe('al-index-dir: boom')
  expect(indexDirRefusal('Error: boom')).toBe('al-index-dir: boom')
  expect(indexDirRefusal('SettingsBroken: the file is broken')).toBe('al-index-dir: the file is broken')
  expect(indexDirRefusal(T.indexDirTimeout)).toBe(`al-index-dir: ${T.indexDirTimeout}`)
  expect(indexDirRefusal('Note: the text is not a name of an error')).toBe('al-index-dir: Note: the text is not a name of an error')
})
