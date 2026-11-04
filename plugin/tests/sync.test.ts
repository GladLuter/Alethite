import { expect, test } from 'claude-code/testing'
import { skipArgs, syncArgs, syncHealth, syncReport } from '../hooks/sync'
import type { SyncResult, SyncStatus } from '../hooks/sync'
import { T } from '../hooks/text'

test('sync args: each value one item, so a folder starting with - is no flag; no memory folder, no memory step; the cipher; a skip at the end', () => {
  expect(syncArgs('\\\\NAS\\cg', 'embeddinggemma', 'C:/mem', 'C:/w', [], 'sqlcipher')).toEqual([
    'sync',
    '--sync-dir=\\\\NAS\\cg',
    '--model',
    'embeddinggemma',
    '--max-seconds',
    '60',
    '--memory-dir=C:/mem',
    '--project-cwd=C:/w',
    '--share-cipher=sqlcipher',
  ])
  expect(syncArgs('-x', 'm', null, 'C:/w', ['NOTEBOOK', '3'], 'cryptography')).toEqual([
    'sync',
    '--sync-dir=-x',
    '--model',
    'm',
    '--max-seconds',
    '60',
    '--project-cwd=C:/w',
    '--share-cipher=cryptography',
    '--skip-batch',
    'NOTEBOOK',
    '3',
  ])
})

test('skip args: none for a plain run, the computer and the number for a skip, null for anything else', () => {
  expect(skipArgs('')).toEqual([])
  expect(skipArgs('  skip  NOTEBOOK 12 ')).toEqual(['NOTEBOOK', '12'])
  for (const bad of ['skip', 'skip NOTEBOOK', 'skip NOTEBOOK 0', 'skip NOTEBOOK x', 'skip NOTEBOOK 1 2', 'now']) expect(skipArgs(bad)).toBeNull()
})

const result = (over: Partial<SyncResult> = {}): SyncResult => ({
  machine: 'HOMEPC',
  pushed: 3,
  pulled: 5,
  vectors: { pushed: 2, pulled: 4 },
  forgotten: 0,
  skipped_vectors: 0,
  peers: { NOTEBOOK: 7 },
  memory: { pushed: 1, pulled: 2, conflicts: 0 },
  warnings: [],
  errors: [],
  ...over,
})

test('report: what moved, how far each computer is, the short memory', () => {
  expect(syncReport(result())).toEqual([T.cgSyncDone(result()), T.cgSyncPeers('NOTEBOOK 7'), T.syncMemory({ pushed: 1, pulled: 2, conflicts: 0 })])
})

test('report: no other computer yet, no memory folder, forgets, skipped vectors, every warning and error', () => {
  const r = result({ peers: {}, memory: null, forgotten: 1, skipped_vectors: 4, warnings: ['w'], errors: [{ where: 'pull', machine: 'NOTEBOOK', batch: 3, error: 'e1' }] })
  expect(syncReport(r)).toEqual([T.cgSyncDone(r), T.cgSyncNoPeers, T.syncMemoryOff, T.cgSyncForgotten(1), T.cgSyncSkippedVectors(4), T.cgSyncWarning('w'), T.cgSyncError('e1')])
})

test('report: a run that failed as a whole says why, not that the auto-memory folder is missing', () => {
  const r = result({ peers: {}, memory: null, errors: [{ where: 'sync', error: 'the sync folder is unavailable' }] })
  expect(syncReport(r)).toEqual([T.cgSyncDone(r), T.cgSyncNoPeers, T.cgSyncError('the sync folder is unavailable')])
})

const status: SyncStatus = {
  machine: 'HOMEPC',
  last: { at: '2026-10-19T10:00:00Z', dir: '\\\\NAS\\cg', pushed: 3, pulled: 5, memory: { pushed: 1, pulled: 0, conflicts: 0 }, errors: 1, error: 'boom' },
  peers: [
    { machine: 'HOMEPC', last_batch: 9, skipped: [], model: 'embeddinggemma', error: null },
    { machine: 'NOTEBOOK', last_batch: 7, skipped: [4], model: 'embeddinggemma', error: 'batch 8 is broken' },
  ],
  origins: { 'HOMEPC': 100, NOTEBOOK: 40 },
}

test('health: off says where to switch it on', () => expect(syncHealth('', status)).toEqual([T.healthSyncOff]))

test('health: the folder, the records by computer, the last batches, the skips, the errors, the last run', () => {
  expect(syncHealth('\\\\NAS\\cg', status)).toEqual([
    T.healthSync('\\\\NAS\\cg', 'HOMEPC'),
    T.healthOrigins('HOMEPC 100, NOTEBOOK 40'),
    T.healthOwnBatches('HOMEPC', 9),
    T.healthPeer('NOTEBOOK', 7, 'embeddinggemma'),
    T.healthPeerSkipped('NOTEBOOK', '4'),
    T.healthPeerError('NOTEBOOK', 'batch 8 is broken'),
    T.healthSyncLast(new Date('2026-10-19T10:00:00Z').toLocaleString(T.locale), 3, 5, 1),
    T.healthSyncError('boom'),
    T.syncMemory({ pushed: 1, pulled: 0, conflicts: 0 }),
  ])
})

test('health: a last run without the memory step tells its error, not a missing auto-memory folder', () => {
  const failed: SyncStatus = { ...status, last: { ...status.last!, memory: null, errors: 1, error: 'the sync folder is unavailable' } }
  const lines = syncHealth('\\\\NAS\\cg', failed)
  expect(lines).toContain(T.healthSyncError('the sync folder is unavailable'))
  expect(lines).not.toContain(T.syncMemoryOff)
})

test('health: never synced; nothing known of the index', () => {
  expect(syncHealth('Z:/cg', { machine: 'HOMEPC', last: null, peers: [], origins: {} })).toEqual([T.healthSync('Z:/cg', 'HOMEPC'), T.healthSyncNever])
  expect(syncHealth('Z:/cg', undefined)).toEqual([T.healthSync('Z:/cg', '?')])
})
