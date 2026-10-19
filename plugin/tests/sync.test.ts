import { expect, test } from 'claude-code/testing'
import { skipArgs, syncArgs, syncHealth, syncReport } from '../hooks/sync'
import type { SyncResult, SyncStatus } from '../hooks/sync'
import { T } from '../hooks/text'

test('sync args: each value one item, so a folder starting with - is no flag; no memory folder, no memory step; a skip at the end', () => {
  expect(syncArgs('\\\\NAS\\cg', 'embeddinggemma', 'C:/mem', 'C:/w')).toEqual([
    'sync',
    '--sync-dir=\\\\NAS\\cg',
    '--model',
    'embeddinggemma',
    '--max-seconds',
    '60',
    '--memory-dir=C:/mem',
    '--project-cwd=C:/w',
  ])
  expect(syncArgs('-x', 'm', null, 'C:/w', ['MTLAPTOP', '3'])).toEqual(['sync', '--sync-dir=-x', '--model', 'm', '--max-seconds', '60', '--project-cwd=C:/w', '--skip-batch', 'MTLAPTOP', '3'])
})

test('skip args: none for a plain run, the computer and the number for a skip, null for anything else', () => {
  expect(skipArgs('')).toEqual([])
  expect(skipArgs('  skip  MTLAPTOP 12 ')).toEqual(['MTLAPTOP', '12'])
  for (const bad of ['skip', 'skip MTLAPTOP', 'skip MTLAPTOP 0', 'skip MTLAPTOP x', 'skip MTLAPTOP 1 2', 'now']) expect(skipArgs(bad)).toBeNull()
})

const result = (over: Partial<SyncResult> = {}): SyncResult => ({
  machine: 'AIJ-HP',
  pushed: 3,
  pulled: 5,
  vectors: { pushed: 2, pulled: 4 },
  forgotten: 0,
  skipped_vectors: 0,
  peers: { MTLAPTOP: 7 },
  memory: { pushed: 1, pulled: 2, conflicts: 0 },
  warnings: [],
  errors: [],
  ...over,
})

test('report: what moved, how far each computer is, the short memory', () => {
  expect(syncReport(result())).toEqual([T.cgSyncDone(result()), T.cgSyncPeers('MTLAPTOP 7'), T.syncMemory({ pushed: 1, pulled: 2, conflicts: 0 })])
})

test('report: no other computer yet, no memory folder, forgets, skipped vectors, every warning and error', () => {
  const r = result({ peers: {}, memory: null, forgotten: 1, skipped_vectors: 4, warnings: ['w'], errors: [{ where: 'pull', machine: 'MTLAPTOP', batch: 3, error: 'e1' }, { where: 'sync', error: 'e2' }] })
  expect(syncReport(r)).toEqual([T.cgSyncDone(r), T.cgSyncNoPeers, T.syncMemoryOff, T.cgSyncForgotten(1), T.cgSyncSkippedVectors(4), T.cgSyncWarning('w'), T.cgSyncError('e1'), T.cgSyncError('e2')])
})

const status: SyncStatus = {
  machine: 'AIJ-HP',
  last: { at: '2026-10-19T10:00:00Z', dir: '\\\\NAS\\cg', pushed: 3, pulled: 5, memory: { pushed: 1, pulled: 0, conflicts: 0 }, errors: 1, error: 'boom' },
  peers: [
    { machine: 'AIJ-HP', last_batch: 9, skipped: [], model: 'embeddinggemma', error: null },
    { machine: 'MTLAPTOP', last_batch: 7, skipped: [4], model: 'embeddinggemma', error: 'batch 8 is broken' },
  ],
  origins: { 'AIJ-HP': 100, MTLAPTOP: 40 },
}

test('health: off says where to switch it on', () => expect(syncHealth('', status)).toEqual([T.healthSyncOff]))

test('health: the folder, the records by computer, the last batches, the skips, the errors, the last run', () => {
  expect(syncHealth('\\\\NAS\\cg', status)).toEqual([
    T.healthSync('\\\\NAS\\cg', 'AIJ-HP'),
    T.healthOrigins('AIJ-HP 100, MTLAPTOP 40'),
    T.healthOwnBatches('AIJ-HP', 9),
    T.healthPeer('MTLAPTOP', 7, 'embeddinggemma'),
    T.healthPeerSkipped('MTLAPTOP', '4'),
    T.healthPeerError('MTLAPTOP', 'batch 8 is broken'),
    T.healthSyncLast(new Date('2026-10-19T10:00:00Z').toLocaleString(T.locale), 3, 5, 1),
    T.healthSyncError('boom'),
    T.syncMemory({ pushed: 1, pulled: 0, conflicts: 0 }),
  ])
})

test('health: never synced; nothing known of the index', () => {
  expect(syncHealth('Z:/cg', { machine: 'AIJ-HP', last: null, peers: [], origins: {} })).toEqual([T.healthSync('Z:/cg', 'AIJ-HP'), T.healthSyncNever])
  expect(syncHealth('Z:/cg', undefined)).toEqual([T.healthSync('Z:/cg', '?')])
})
