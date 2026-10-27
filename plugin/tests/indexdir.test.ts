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

test('index dir reply: done names the folder, unfinished says so; the notes follow', () => {
  expect(indexDirReply({ action: 'index-dir', done: true, dir: 'D:\\X', notes: [] })).toBe(T.indexDirDone('D:\\X'))
  expect(indexDirReply({ action: 'index-dir', done: true, dir: 'D:\\X', notes: ['n1', 'n2'] })).toBe([T.indexDirDone('D:\\X'), 'n1', 'n2'].join('\n'))
  expect(indexDirReply({ action: 'index-dir', done: false, dir: '', notes: ['n1'] })).toBe([T.indexDirUnfinished, 'n1'].join('\n'))
  expect(T.indexDirDone('D:\\X')).toContain('D:\\X')
})

test('index dir refusal: the reason of cg.py without its Python exception name; any other text as it is', () => {
  expect(indexDirRefusal('ValueError: D:\\X is not a local disk. Nothing was changed')).toBe('cg-index-dir: D:\\X is not a local disk. Nothing was changed')
  expect(indexDirRefusal('PermissionError: [WinError 5] denied')).toBe('cg-index-dir: [WinError 5] denied')
  expect(indexDirRefusal(T.indexDirTimeout)).toBe(`cg-index-dir: ${T.indexDirTimeout}`)
  expect(indexDirRefusal('Note: the text is not a name of an error')).toBe('cg-index-dir: Note: the text is not a name of an error')
})
