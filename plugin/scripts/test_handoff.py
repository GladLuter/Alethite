import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import handoff
from handoff import build_handoff
from test_transcripts import a, jsonl, text, tool, u
from transcripts import strip_handoff

CG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cg.py')
LATER = '2026-10-13T11:00:00.000Z'


class HandoffTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = d.name
        self.projects = os.path.join(d.name, 'projects')

    def write(self, name, objs):
        path = os.path.join(self.projects, *name.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(jsonl(objs))
        return path

    def build(self, *args):
        return build_handoff(self.projects, 'S', '2026-10-13', *args)

    def git(self, repo, *args):
        subprocess.run(['git', '-C', repo, *args], check=True, capture_output=True)

    def make_repo(self, untracked=0):
        """A repository with one commit, a.txt modified and not committed, and `untracked` files with long names."""
        repo = os.path.join(self.tmp, 'repo')
        os.makedirs(repo)
        for args in (['init'], ['config', 'user.name', 't'], ['config', 'user.email', 't@example.com']):
            self.git(repo, *args)
        for name, body in [('a.txt', 'one\n')]:
            with open(os.path.join(repo, name), 'w') as f:
                f.write(body)
        self.git(repo, 'add', 'a.txt')
        self.git(repo, 'commit', '-m', 'first commit')
        with open(os.path.join(repo, 'a.txt'), 'w') as f:
            f.write('two\n')
        for i in range(untracked):
            open(os.path.join(repo, 'f%02d' % i + 'n' * 100), 'w').close()
        return os.path.realpath(repo)

    def test_owner_messages_survive_long_replies(self):
        objs = []
        for i in range(10):
            objs += [u('владелец-%d ' % i + 'в' * 300), a([text('ответ-%d ' % i + 'я' * 5000)], mid='m%d' % i)]
        self.write('P/S.jsonl', objs)
        t = self.build()
        for i in range(6, 10):
            self.assertIn('владелец-%d' % i, t)
        self.assertNotIn('владелец-5', t)
        self.assertLessEqual(len(t), 10500)

    def test_last_reply_keeps_head_and_tail(self):
        self.write('P/S.jsonl', [u('вопрос'), a([text('НАЧАЛО' + 'x' * 6000 + 'КОНЕЦ')])])
        t = self.build()
        self.assertIn('НАЧАЛО', t)
        self.assertIn('КОНЕЦ', t)
        self.assertNotIn('x' * 3000, t)

    def test_last_reply_joins_every_part_after_the_owner(self):
        self.write('P/S.jsonl', [u('один'), a([text('старый ответ')]), u('два'),
                                 a([text('часть-1'), tool('Bash', command='ls'), text('часть-2')], mid='m2')])
        t = self.build()
        self.assertEqual([x in t for x in ('старый ответ', 'часть-1', 'часть-2')], [True] * 3)
        self.assertLess(t.index('старый ответ'), t.index('**Ты:** два'))  # in the order of the conversation
        self.assertLess(t.index('**Ты:** два'), t.index('часть-1'))

    def test_commands_and_old_handoff_filtered(self):
        self.write('P/S.jsonl', [
            u('<command-name>/clear</command-name>\n<command-message>clear</command-message>'),
            u('[cg-handoff]\nСТАРАЯ\n[/cg-handoff]\n\nчерновик'),
            a([text('пример: [/cg-handoff] и дальше текст')])])
        t = self.build()
        self.assertNotIn('/clear', t)
        self.assertNotIn('СТАРАЯ', t)
        self.assertIn('черновик', t)
        self.assertEqual(strip_handoff(t).strip(), '')  # a reply that quotes the end marker must not cut the handoff short

    def test_edits_of_chat_and_subagents_listed_newest_first(self):
        self.write('P/S.jsonl', [u('сделай'), a([tool('Edit', file_path='C:\\r\\main.py'), tool('Edit', file_path='C:\\r\\main.py')], ts=LATER)])
        self.write('P/S/subagents/agent-a1.jsonl', [u('сделай Y'), a([tool('Write', file_path='C:\\r\\only_sub.py')])])
        t = self.build()
        self.assertEqual(t.count('- C:\\r\\main.py'), 1)
        self.assertLess(t.index('- C:\\r\\main.py'), t.index('- C:\\r\\only_sub.py'))  # by time, not by file

    def test_git_of_touched_repo_not_cwd(self):
        repo = self.make_repo()
        cwd = os.path.join(self.tmp, 'plain')
        os.makedirs(cwd)
        self.write('P/S.jsonl', [dict(u('сделай'), cwd=cwd), dict(a([tool('Edit', file_path=os.path.join(repo, 'a.txt'))]), cwd=cwd)])
        t = self.build()
        self.assertIn('### ' + repo, t)
        self.assertIn(' M a.txt', t)
        self.assertIn('first commit', t)
        self.assertNotIn('(git:', t)  # "not a repository" is not an error

    def test_git_failure_is_reported_once(self):
        self.write('P/S.jsonl', [u('сделай'), a([tool('Edit', file_path=os.path.join(self.tmp, 'a.txt')),
                                                tool('Edit', file_path=os.path.join(self.projects, 'b.txt'))])])
        with mock.patch.object(handoff.subprocess, 'run', side_effect=OSError('нет git')):
            t = self.build()
        self.assertEqual(t.count('(git: нет git)'), 1)

    def check_cap(self, t):
        self.assertLessEqual(len(t), 10500)
        self.assertTrue(t.startswith('[cg-handoff]'))
        self.assertTrue(t.rstrip().endswith('[/cg-handoff]'))
        self.assertIn('КОНЕЦ', t)

    def test_cap_drops_oldest_early_replies_first(self):
        objs = []
        for i in range(10):
            objs += [u('владелец-%d ' % i + 'в' * 300), a([text('РАННИЙ-%d ' % i + 'я' * 900)], mid='m%d' % i)]
        objs[-1]['message']['content'][0]['text'] = 'НАЧАЛО' + 'x' * 6000 + 'КОНЕЦ'
        paths = ['C:\\r\\f%02d' % i + 'n' * 120 for i in range(30)]
        objs.append(a([tool('Edit', file_path=p) for p in paths], mid='m99'))
        self.write('P/S.jsonl', objs)
        t = self.build()
        self.check_cap(t)
        self.assertEqual([p in t for p in paths], [True] * 30)  # the early replies go first, the file list stays whole
        self.assertEqual([i for i in range(9) if 'РАННИЙ-%d ' % i in t], [6, 7, 8])  # four fit, the oldest went, the newest stay

    def test_cap_cuts_the_end_when_nothing_else_is_left(self):
        repo = self.make_repo(untracked=35)  # `git status` alone is ~3 000 characters
        objs = []
        for i in range(6):
            objs += [u('владелец-%d ' % i + 'в' * 2000), a([text('ответ-%d' % i)], mid='m%d' % i)]
        objs[-1]['message']['content'][0]['text'] = 'НАЧАЛО' + 'x' * 6000 + 'КОНЕЦ'
        objs.append(a([tool('Edit', file_path=os.path.join(repo, 'a.txt'))], mid='m99'))
        self.write('P/S.jsonl', objs)
        t = self.build()
        self.check_cap(t)
        self.assertIn('владелец-5', t)
        self.assertIn('### ' + repo, t)

    def test_memory_line(self):
        self.write('P/S.jsonl', [u('вопрос')])
        two, empty = (os.path.join(self.tmp, n) for n in ('two', 'empty'))
        for d in (two, empty):
            os.makedirs(d)
        for name in ('MEMORY.md', 'one.md', 'two.md', 'notes.txt'):
            open(os.path.join(two, name), 'w').close()
        self.assertIn('Короткая память: %s (фактов: 2)' % two, self.build(two))
        self.assertIn('Короткая память ПУСТА: %s' % empty, self.build(empty))
        self.assertNotIn('Короткая память', self.build())

    def test_unknown_session(self):
        with self.assertRaises(ValueError):
            build_handoff(self.projects, 'нет', '2026-10-13')

    def test_cli_saves_text_before_answering(self):
        self.write('P/S.jsonl', [u('вопрос про ёжика'), a([text('ответ')])])
        db = os.path.join(self.tmp, 'db', 'index.db')
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONIOENCODING', 'PYTHONUTF8')}
        run = lambda session: subprocess.run([sys.executable, CG, '--db', db, '--projects', self.projects, 'handoff',
                                              '--session', session, '--today', '2026-10-13'], capture_output=True, env=env)
        p = run('S')
        out = json.loads(p.stdout.decode('utf-8'))
        self.assertEqual((p.returncode, out['path']), (0, os.path.join(self.tmp, 'db', 'handoffs', 'S.md')))
        self.assertIn('ёжика', out['text'])
        with open(out['path'], 'rb') as f:
            self.assertEqual(f.read().decode('utf-8'), out['text'])
        p = run('нет')
        self.assertEqual(p.returncode, 1)
        self.assertIn('error', json.loads(p.stdout.decode('utf-8')))


if __name__ == '__main__':
    unittest.main()
