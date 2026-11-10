import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest

import lessons
import memdb
import sync as cgsync
from memdb import connect
from test_memdb import search
from test_texts import use_lang
from test_vectors import fake, never
from transcripts import project_key
from vectors import MODEL, embed_pending, hybrid_search

CG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cg.py')
A = project_key('C:\\a', 'C:\\Users\\x')
B = project_key('C:\\b', 'C:\\Users\\x')
RATE = {'action': 'запустил сборку ещё раз сразу после ошибки', 'symptom': 'HTTP 429: лимит 120 запросов в час',
        'cause': 'каждый запуск дёргает API GitHub', 'fix': 'ждать час или кэшировать ответ', 'trigger': 'gh api /repos'}


class LessonsTest(unittest.TestCase):
    def setUp(self):
        use_lang(self, 'ru')
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name
        self.conn = connect(os.path.join(d.name, 'index.db'))
        self.addCleanup(self.conn.close)

    def test_a_lesson_is_a_record_of_its_project_with_a_key(self):
        out = lessons.save(self.conn, A, RATE)
        self.assertTrue(out['new'])
        self.assertEqual(out['text'], 'Action: запустил сборку ещё раз сразу после ошибки\nSymptom: HTTP 429: лимит 120 запросов в час\n'
                                      'Cause: каждый запуск дёргает API GitHub\nFix: ждать час или кэшировать ответ\nTrigger: gh api /repos')
        row = self.conn.execute("SELECT kind, conv, pos FROM chunks WHERE rowid = ?", (out['id'],)).fetchone()
        self.assertEqual(row, ('lesson', lessons.conv_of(A), out['pos']))
        self.assertEqual(self.conn.execute('SELECT cls, origin, pushed FROM keys WHERE id = ?', (out['id'],)).fetchone(), ('lesson', None, 0))
        self.assertEqual(lessons.save(self.conn, A, dict(RATE))['new'], False)  # the same lesson again changes nothing
        self.assertEqual(search(self.conn, '429', [A])[0]['kind'], 'lesson')  # recall finds it as any record

    def test_what_a_lesson_needs(self):
        with self.assertRaisesRegex(ValueError, 'cause'):
            lessons.save(self.conn, A, {'action': 'a', 'symptom': 's', 'cause': '  '})
        with self.assertRaisesRegex(ValueError, '8'):
            lessons.save(self.conn, A, dict(RATE, trigger='git'))  # it would stop every other command
        with self.assertRaisesRegex(ValueError, 'symptom'):
            lessons.save(self.conn, A, dict(RATE, symptom=['не', 'текст']))
        long = lessons.save(self.conn, A, dict(RATE, cause='я' * 1000, trigger=None))
        self.assertEqual(lessons.fields_of(long['text'])['cause'], 'я' * 299 + '…')
        self.assertNotIn('Trigger', long['text'])

    def test_a_secret_in_a_lesson_is_hidden(self):
        out = lessons.save(self.conn, A, dict(RATE, fix='задать token=abc123def456 в окружении'))
        self.assertIn('token=[скрыто]', out['text'])

    def test_the_list_is_of_the_project_newest_first_with_triggers(self):
        now = datetime.datetime(2026, 10, 20, 9, 0, tzinfo=datetime.timezone.utc)  # one millisecond for both: the order is by pos, not by ts
        lessons.save(self.conn, A, RATE, now)
        lessons.save(self.conn, A, dict(RATE, action='второй урок', trigger=None), now)
        lessons.save(self.conn, B, dict(RATE, action='урок другого проекта'))
        mine = lessons.listing(self.conn, [A])
        self.assertEqual([(l['text'].split('\n')[0], l['trigger']) for l in mine],
                         [('Action: второй урок', None), ('Action: запустил сборку ещё раз сразу после ошибки', 'gh api /repos')])
        self.assertEqual(len(lessons.listing(self.conn, None)), 3)

    def test_listing_same_lessons_as_the_full_scan(self):
        now = datetime.datetime(2026, 10, 20, 9, 0, tzinfo=datetime.timezone.utc)
        lessons.save(self.conn, A, RATE, now)
        lessons.save(self.conn, B, dict(RATE, action='урок другого проекта'), now)
        lessons.save(self.conn, A, dict(RATE, action='второй урок'), now)
        old = "SELECT rowid FROM chunks WHERE kind = 'lesson' AND " + memdb.IN_PROJECT + ' ORDER BY pos DESC'
        for p in ([A], [B], [A, B], None):
            self.assertEqual([l['id'] for l in lessons.listing(self.conn, p)], [r[0] for r in self.conn.execute(old, {'p': json.dumps(p)})])

    def test_listing_after_the_project_key_was_migrated(self):
        raw, home = r'C:\old\proj', r'C:\Users\x'
        lessons.save(self.conn, raw, RATE)
        new = project_key(raw, home)
        self.conn.execute('UPDATE conv_projects SET project = ?', (new,))  # what _migrate does; the lesson conv keeps the hash of the raw key
        old = "SELECT rowid FROM chunks WHERE kind = 'lesson' AND " + memdb.IN_PROJECT
        want = [r[0] for r in self.conn.execute(old, {'p': json.dumps([new])})]
        self.assertEqual(len(want), 1)
        self.assertEqual([l['id'] for l in lessons.listing(self.conn, [new])], want)

    def test_save_finds_a_repeat_and_continues_pos(self):
        now = datetime.datetime(2026, 10, 20, 9, 0, tzinfo=datetime.timezone.utc)
        first = lessons.save(self.conn, A, RATE, now)
        again = lessons.save(self.conn, A, RATE, now)
        self.assertEqual((again['new'], again['id'], again['pos']), (False, first['id'], first['pos']))
        last = self.conn.execute('SELECT max(pos) FROM chunks WHERE conv = ?', (first['conv'],)).fetchone()[0]
        self.assertEqual(lessons.save(self.conn, A, dict(RATE, action='второй'), now)['pos'], last + 1)

    def test_a_message_finds_the_lesson_that_fits_and_only_it(self):
        fit = lessons.save(self.conn, A, RATE)
        lessons.save(self.conn, A, {'action': 'поменял модель векторов', 'symptom': 'всё пересчитывается заново', 'cause': 'векторы разных моделей несравнимы'})
        lessons.save(self.conn, B, RATE)  # another project's lesson never comes
        embed_pending(self.conn, embed_fn=fake)
        # the bag of words of the fake embedder: 0.32 for the lesson that fits, 0.17 for the one that does not, 0.07 for another theme
        found, state = lessons.match(self.conn, 'опять HTTP 429 лимит запросов в час на сборке', [A], MODEL, 0.25, embed_fn=fake)
        self.assertEqual((state, [l['id'] for l in found]), ('ok', [fit['id']]))
        self.assertGreaterEqual(found[0]['score'], 0.25)
        self.assertEqual(lessons.match(self.conn, 'покрась кнопку в синий', [A], MODEL, 0.25, embed_fn=fake)[0], [])

    def test_a_project_with_aliases_sees_the_lessons_of_all_its_keys(self):  # the folder moved: the lessons stay under the old key
        old = project_key('C:\\old\\proj', 'C:\\Users\\x')
        gone = lessons.save(self.conn, old, RATE)
        mine = lessons.save(self.conn, A, dict(RATE, action='урок новой папки'))
        embed_pending(self.conn, embed_fn=fake)
        self.assertEqual([l['id'] for l in lessons.listing(self.conn, [A])], [mine['id']])
        self.assertEqual({l['id'] for l in lessons.listing(self.conn, [A, old])}, {gone['id'], mine['id']})
        text = 'опять HTTP 429 лимит запросов в час на сборке'
        self.assertIn(gone['id'], [l['id'] for l in lessons.match(self.conn, text, [A, old], MODEL, 0.25, embed_fn=fake)[0]])
        self.assertNotIn(gone['id'], [l['id'] for l in lessons.match(self.conn, text, [A], MODEL, 0.25, embed_fn=fake)[0]])

    def test_global_lesson_needs_fix(self):
        bare = {k: v for k, v in RATE.items() if k != 'fix'}
        with self.assertRaisesRegex(ValueError, 'fix'):
            lessons.save(self.conn, lessons.GLOBAL, bare)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM chunks').fetchone(), (0,))
        out = lessons.save(self.conn, lessons.GLOBAL, RATE)
        self.assertEqual(out['conv'], lessons.conv_of('*'))

    def test_listing_marks_global_and_fix(self):
        lessons.save(self.conn, A, dict(RATE, fix=None))
        lessons.save(self.conn, lessons.GLOBAL, dict(RATE, action='общий урок'))
        both = {l['text'].split(chr(10))[0]: l for l in lessons.listing(self.conn, [A, lessons.GLOBAL])}
        self.assertEqual(len(both), 2)
        self.assertEqual((both['Action: общий урок']['global'], both['Action: общий урок']['fix']), (True, RATE['fix']))
        mine = both['Action: запустил сборку ещё раз сразу после ошибки']
        self.assertEqual((mine['global'], mine['fix']), (False, None))
        self.assertEqual([l['global'] for l in lessons.listing(self.conn, [A])], [False])

    def test_match_never_returns_global(self):
        lessons.save(self.conn, lessons.GLOBAL, RATE)
        lessons.save(self.conn, A, dict(RATE, action='урок проекта'))
        embed_pending(self.conn, embed_fn=fake)
        found = lessons.match(self.conn, '429', [A], MODEL, 0.0, limit=5, embed_fn=fake)[0]
        self.assertEqual([l['text'].split(chr(10))[0] for l in found], ['Action: урок проекта'])

    def test_matching_costs_nothing_without_lessons_and_says_why_it_could_not(self):
        self.assertEqual(lessons.match(self.conn, 'что угодно', [A], MODEL, 0.4, embed_fn=never), ([], 'ok'))  # no lessons: Ollama not asked
        lessons.save(self.conn, A, RATE)
        self.assertEqual(lessons.match(self.conn, 'что угодно', [A], MODEL, 0.4, embed_fn=never)[0], [])  # no vectors yet: nor now
        embed_pending(self.conn, embed_fn=fake)

        def down(texts, model, timeout):
            raise RuntimeError('Ollama не отвечает: тест')

        self.assertEqual(lessons.match(self.conn, '429', [A], MODEL, 0.4, embed_fn=down), ([], 'Ollama не отвечает: тест'))
        self.assertIn('other', lessons.match(self.conn, '429', [A], 'other', 0.4, embed_fn=never)[1])

    def test_a_new_lesson_gets_its_vectors_at_once_when_the_model_is_the_indexs(self):
        embed_pending(self.conn, embed_fn=fake)  # the index holds this model's vectors (here: of nothing yet)
        memdb_model = self.conn.execute("SELECT value FROM meta WHERE key = 'embed_model'").fetchone()
        if not memdb_model:
            self.conn.execute("INSERT INTO meta VALUES('embed_model', ?)", (MODEL,))
        out = lessons.save(self.conn, A, RATE)
        self.assertEqual(lessons.embed_one(self.conn, out['id'], MODEL, fake), 'ok')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM vecs WHERE chunk = ?', (out['id'],)).fetchone()[0], 1)
        self.assertIn('other', lessons.embed_one(self.conn, out['id'], 'other', never))  # another model's index: the next embed run does it

    def test_a_lesson_goes_to_the_other_computer_and_forget_deletes_it_there(self):
        share = os.path.join(self.dir, 'share')
        os.makedirs(share)
        other = connect(os.path.join(self.dir, 'other.db'))
        self.addCleanup(other.close)
        out = lessons.save(self.conn, A, RATE)
        embed_pending(self.conn, embed_fn=fake)
        for conn, name in ((self.conn, 'HOMEPC'), (other, 'NOTEBOOK')):
            self.assertEqual(cgsync.sync(conn, share, name, digest=lambda m: None)['errors'], [])
        there = lessons.listing(other, [A])
        self.assertEqual([l['trigger'] for l in there], ['gh api /repos'])
        self.assertEqual(there[0]['from'], 'HOMEPC')
        self.assertEqual(memdb.forget(self.conn, out['conv'], out['pos']), 1)
        cgsync.sync(self.conn, share, 'HOMEPC', digest=lambda m: None)
        cgsync.sync(other, share, 'NOTEBOOK', digest=lambda m: None)
        self.assertEqual(lessons.listing(other, [A]), [])

    def test_recall_finds_a_lesson_by_the_trace(self):
        lessons.save(self.conn, A, RATE)
        embed_pending(self.conn, embed_fn=fake)
        hits, state = hybrid_search(self.conn, 'лишние вызовы', [A], also=['429 лимит'], embed_fn=fake)
        self.assertEqual((state, hits[0]['kind']), ('ok', 'lesson'))

    def test_cli(self):
        db = os.path.join(self.dir, 'cli.db')
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONIOENCODING', 'PYTHONUTF8')}
        run = lambda *args: json.loads(subprocess.run([sys.executable, CG, '--db', db, '--lang', 'ru', *args], capture_output=True, env=env).stdout.decode('utf-8'))
        out = run('lesson-save', '--cwd', 'C:\\a', '--action', RATE['action'], '--symptom', RATE['symptom'], '--cause', RATE['cause'],
                  '--trigger', RATE['trigger'], '--model', 'nosuch')
        self.assertTrue(out['new'])
        self.assertIn('nosuch', out['vector'])  # no vectors of that model in the index: the next embed run does it
        listed = run('lessons', '--cwd', 'C:\\a')['lessons']
        self.assertEqual([l['trigger'] for l in listed], ['gh api /repos'])
        self.assertEqual(run('lessons', '--cwd', 'C:\\b')['lessons'], [])
        self.assertEqual(run('lessons-match', '--cwd', 'C:\\a', '--text', '429', '--model', 'nosuch')['lessons'], [])
        self.assertIn('уроку нужны', run('lesson-save', '--cwd', 'C:\\a', '--action', 'a', '--symptom', 's', '--cause', ' ')['error'])


if __name__ == '__main__':
    unittest.main()
