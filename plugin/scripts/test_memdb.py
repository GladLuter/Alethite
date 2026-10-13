import json
import os
import tempfile
import unittest
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
        self.conn = connect(os.path.join(d.name, 'db', 'index.db'))  # connect creates the folder
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
        self.assertEqual((s['files'], s['rows'], s['convs']), (1, 2, 1))
        self.write('P/S.jsonl', [owner_line('добавка')], 'ab')
        s2 = self.update()
        self.assertEqual((s2['files'], s2['rows']), (1, 1))
        self.assertEqual(self.update()['files'], 0)
        st = status(self.conn)
        self.assertEqual((st['convs'], st['rows']), (1, 3))
        self.assertTrue(st['last_update'] and os.path.isfile(st['db']) and st['size_mb'] > 0)
        self.assertEqual(self.update(rebuild=True)['rows'], 3)
        self.assertEqual(status(self.conn)['rows'], 3)

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
