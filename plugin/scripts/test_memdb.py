import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock
import memdb
from memdb import connect, expand, owner_count, search, status, update
from transcripts import project_key

TS = '2026-10-13T10:00:00.000Z'


def owner_line(text, ts=TS, cwd='C:\\a'):
    return dict(type='user', timestamp=ts, cwd=cwd, message={'role': 'user', 'content': text})


def claude_line(text, ts=TS, cwd='C:\\a'):
    m = {'id': 'm1', 'model': 'claude-opus-5-5', 'role': 'assistant', 'content': [{'type': 'text', 'text': text}]}
    return dict(type='assistant', timestamp=ts, cwd=cwd, message=m)


class MemdbTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.projects = os.path.join(d.name, 'projects')
        self.db = os.path.join(d.name, 'db', 'index.db')
        self.conn = connect(self.db)  # connect creates the folder
        self.addCleanup(self.conn.close)

    def write(self, name, objs, mode='wb'):
        path = os.path.join(self.projects, *name.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode) as f:
            f.write(''.join(json.dumps(o, ensure_ascii=False) + '\n' for o in objs).encode('utf-8'))
        return path

    def update(self, **kw):
        return update(self.conn, self.projects, **kw)

    def rows_of(self, conv):
        return self.conn.execute('SELECT count(*) FROM chunks WHERE conv = ?', (conv,)).fetchone()[0]

    def kinds_of(self, conv):
        return [r[0] for r in self.conn.execute('SELECT kind FROM chunks WHERE conv = ? ORDER BY pos, rowid', (conv,))]

    def test_incremental(self):
        self.assertIsNone(status(self.conn)['last_update'])
        self.write('P/S.jsonl', [owner_line('первая'), claude_line('ответ')])
        s = self.update()
        self.assertGreater(s['rows'], 0)
        self.assertEqual((s['files'], s['rows'], s['convs'], s['errors']), (1, 2, 1, []))
        self.write('P/S.jsonl', [owner_line('добавка')], 'ab')
        s2 = self.update()
        self.assertEqual((s2['files'], s2['rows']), (1, 1))
        self.assertEqual(self.update()['files'], 0)
        st = status(self.conn)
        self.assertEqual((st['convs'], st['rows']), (1, 3))
        self.assertTrue(st['last_update'] and os.path.isfile(st['db']) and st['size_mb'] > 0)
        s3 = self.update(rebuild=True)  # re-reads the file from the start; its old rows are replaced, not doubled
        self.assertEqual((s3['files'], s3['rows'], s3['errors']), (1, 3, []))
        self.assertEqual(status(self.conn)['rows'], 3)

    def test_rebuild_keeps_deleted_transcripts(self):
        self.write('P/A.jsonl', [owner_line('альфа здесь'), owner_line('и ещё альфа')])
        b = self.write('P/B.jsonl', [owner_line('бета здесь')])
        self.update()
        os.remove(b)  # Claude Code cleaned it up; the index must remember it
        s = self.update(rebuild=True)
        self.assertEqual((s['files'], s['rows'], s['convs']), (1, 2, 2))
        self.assertEqual((self.rows_of('A'), self.rows_of('B')), (2, 1))
        self.assertEqual(search(self.conn, 'бета')[0]['conv'], 'B')
        with self.assertRaises(OSError):  # a wrong folder must not wipe anything
            update(self.conn, os.path.join(self.projects, 'нет'), rebuild=True)
        self.assertEqual(status(self.conn)['rows'], 3)

    def test_missing_root_raises_and_keeps_last_update(self):
        with self.assertRaises(OSError):
            update(self.conn, os.path.join(self.projects, 'нет'))
        self.assertIsNone(status(self.conn)['last_update'])

    def test_unreadable_file_is_reported_and_retried(self):
        for n in ('good', 'bad', 'gone'):
            self.write('P/%s.jsonl' % n, [owner_line('текст ' + n)])
        real = memdb.read_records

        def fake(path, start=0):
            if os.path.basename(path) == 'bad.jsonl':
                raise PermissionError(13, 'нет доступа')
            if os.path.basename(path) == 'gone.jsonl':
                raise FileNotFoundError(2, 'исчез')  # vanished after the walk: not an error
            return real(path, start)

        with mock.patch.object(memdb, 'read_records', fake):
            s = self.update()
        self.assertEqual((s['files'], s['rows']), (1, 1))
        self.assertEqual([(os.path.basename(e['path']), e['error'].split(':')[0]) for e in s['errors']], [('bad.jsonl', 'PermissionError')])
        self.assertEqual((self.rows_of('good'), self.rows_of('bad'), self.rows_of('gone')), (1, 0, 0))
        s2 = self.update()
        self.assertEqual((s2['files'], s2['rows'], s2['errors']), (2, 2, []))

        def walk(top, onerror=None):  # a subfolder that cannot be listed
            onerror(PermissionError(13, 'нет доступа', os.path.join(top, 'sub')))
            yield top, [], []

        with mock.patch.object(os, 'walk', walk):
            s3 = self.update()
        self.assertEqual([(os.path.basename(e['path']), e['error'].split(':')[0]) for e in s3['errors']], [('sub', 'PermissionError')])

    def test_failure_mid_file_rolls_back(self):
        self.write('P/bad.jsonl', [owner_line('раз', cwd='C:\bad'), owner_line('два', cwd='C:\bad')])
        self.write('P/good.jsonl', [owner_line('три')])
        real = memdb.project_key

        def key(cwd):  # runs after the file's chunks are inserted
            if cwd == 'C:\bad':
                raise ValueError('сбой')
            return real(cwd)

        with mock.patch.object(memdb, 'project_key', key):
            s = self.update()
        self.assertEqual([(os.path.basename(e['path']), e['error']) for e in s['errors']], [('bad.jsonl', 'ValueError: сбой')])
        self.assertEqual((self.rows_of('bad'), self.rows_of('good')), (0, 1))
        self.assertFalse(self.conn.in_transaction)

    def test_overlapping_updates_do_not_duplicate(self):
        self.write('P/S.jsonl', [owner_line('раз'), owner_line('два')])
        other = connect(self.db)  # the owner's second chat
        self.addCleanup(other.close)
        other.execute('PRAGMA busy_timeout = 0')  # the lock is held by this very thread: fail at once instead of waiting
        real, nested = memdb.read_records, []

        def racing(path, start=0):
            if not nested:  # the other update runs between this one reading the offset and inserting
                try:
                    nested.append(update(other, self.projects))
                except sqlite3.OperationalError as e:  # locked out, which is the point
                    nested.append(str(e))
            return real(path, start)

        with mock.patch.object(memdb, 'read_records', racing):
            self.update()
        self.assertEqual(len(nested), 1)
        self.assertEqual(self.rows_of('S'), 2)
        self.assertEqual(update(other, self.projects)['files'], 0)

    def test_rewritten_file_not_duplicated(self):
        self.write('P/S.jsonl', [owner_line('одна'), owner_line('две'), owner_line('три')])
        self.update()
        self.write('P/S.jsonl', [owner_line('одна')])  # файл стал короче
        self.update()
        self.assertEqual(self.rows_of('S'), 1)

    def test_secret_not_searchable(self):
        self.write('P/S.jsonl', [owner_line('вот password=hunter22 для теста')])
        self.update()
        self.assertEqual(search(self.conn, 'hunter22'), [])
        self.assertIn('[скрыто]', search(self.conn, 'теста')[0]['snippet'])

    def test_project_scope_and_subagent_inherits(self):
        wt = 'C:\\a\\.claude\\worktrees\\w1\\src'  # a worktree of C:\a is the same project
        self.write('P1/S1.jsonl', [owner_line('ёжик в тумане', cwd=wt)])
        self.write('P1/S1/subagents/workflows/wf_1/agent-a1.jsonl', [owner_line('найди ёжик', cwd='')])  # no cwd: the project comes from the parent
        self.write('P2/S2.jsonl', [owner_line('ёжик в другом проекте', cwd='C:\\b')])
        self.update()
        hits = search(self.conn, 'ёжик', project=project_key('C:\\a'))
        self.assertEqual({h['conv'] for h in hits}, {'S1', 'agent-a1'})
        self.assertEqual({h['parent'] for h in hits}, {'', 'S1'})
        self.assertEqual(len(search(self.conn, 'ёжик')), 3)

    def test_hit_carries_the_chunk_rowid(self):
        self.write('P/S.jsonl', [owner_line('первое'), owner_line('второе ёжик')])
        self.update()
        hit = search(self.conn, 'ёжик')[0]
        self.assertEqual(self.conn.execute('SELECT pos, text FROM chunks WHERE rowid = ?', (hit['id'],)).fetchone(), (hit['pos'], 'второе ёжик'))

    def test_fts_syntax_never_raises(self):
        self.write('P/S.jsonl', [owner_line('C++ и foo bar')])
        self.update()
        for q in ['C++', '"', 'a AND', '-x', 'foo:bar', '*', 'NEAR(', '', '___']:
            search(self.conn, q)  # без исключения
        self.assertEqual(search(self.conn, ''), [])
        self.assertEqual(len(search(self.conn, 'foo zzz')), 1)  # all words missing -> any word

    def test_owner_ranks_above_subagent_chatter(self):
        filler = [owner_line('разговор %d про другое' % i) for i in range(6)]
        self.write('P/S.jsonl', filler + [owner_line('редкослово и ещё пара слов')])
        self.write('P/S/subagents/agent-a1.jsonl', [claude_line('редкослово'), claude_line('итог')])  # shorter, so it would win on bm25 alone; the last sub text is a report, not chatter
        self.update()
        self.assertEqual([h['kind'] for h in search(self.conn, 'редкослово')], ['owner', 'sub'])

    def test_budget_and_limit(self):
        self.write('P/S.jsonl', [owner_line('бюджет ' + 'слово ' * 200 + str(i)) for i in range(50)])
        self.update()
        hits = search(self.conn, 'бюджет')
        self.assertLessEqual(len(hits), 8)
        self.assertLessEqual(sum(len(h['snippet']) for h in hits), 7000)
        self.assertEqual(len(search(self.conn, 'бюджет', limit=3)), 3)
        small = search(self.conn, 'бюджет', budget=300)
        self.assertTrue(0 < len(small) < len(hits) and sum(len(h['snippet']) for h in small) <= 300)

    def test_last_subagent_text_is_report(self):
        self.write('P/S/subagents/agent-a1.jsonl', [owner_line('сделай'), claude_line('шаг1'), claude_line('итог1')])
        self.update()
        self.assertEqual(self.kinds_of('agent-a1'), ['task', 'sub', 'report'])
        self.write('P/S/subagents/agent-a1.jsonl', [claude_line('итог2')], 'ab')
        self.update()
        self.assertEqual(self.kinds_of('agent-a1'), ['task', 'sub', 'sub', 'report'])
        self.assertEqual(self.conn.execute("SELECT text FROM chunks WHERE kind = 'report'").fetchall(), [('итог2',)])

    def test_expand_and_owner_count(self):
        self.write('P/S.jsonl', [
            owner_line('раз', ts='2026-10-12T23:00:00.000Z'), claude_line('два'), owner_line('три'),
            claude_line('четыре'), owner_line('пять'), claude_line('x' * 3000)])
        self.update()
        pos = [r[0] for r in self.conn.execute('SELECT pos FROM chunks ORDER BY pos')]
        self.assertEqual([r['pos'] for r in expand(self.conn, 'S', pos[2], radius=1)], pos[1:4])
        self.assertEqual([r['pos'] for r in expand(self.conn, 'S', pos[0], radius=1)], pos[:2])
        self.assertEqual(expand(self.conn, 'S', 1), [])
        rows = expand(self.conn, 'S', pos[5], radius=5)
        self.assertEqual(len(rows[-1]['text']), 1500)
        rows = expand(self.conn, 'S', pos[5], radius=5, budget=1504)  # 1500 for the hit + 4 for 'пять': only the nearest neighbour fits
        self.assertEqual([r['pos'] for r in rows], [pos[4], pos[5]])
        self.assertEqual(owner_count(self.conn, '2026-10-13'), 2)
        self.assertEqual(owner_count(self.conn, '2026-10-12', '2026-10-13'), 1)


if __name__ == '__main__':
    unittest.main()
