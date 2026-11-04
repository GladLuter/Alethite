import json
import os
import tempfile
import unittest
from transcripts import find_session, last_usage, project_key, project_keys, read_records, strip_handoff

TS = '2026-10-13T10:00:00.000Z'


def u(content, **kw):
    return dict(type='user', timestamp=TS, cwd='C:\\r', message={'role': 'user', 'content': content}, **kw)


def a(blocks, mid='m1', model='claude-opus-5-5', usage=None, ts=TS):
    m = {'id': mid, 'model': model, 'role': 'assistant', 'content': blocks}
    if usage:
        m['usage'] = usage
    return dict(type='assistant', timestamp=ts, cwd='C:\\r', message=m)


def text(t):
    return {'type': 'text', 'text': t}


def tool(name, **inp):
    return {'type': 'tool_use', 'id': 't1', 'name': name, 'input': inp}


def jsonl(objs):
    return ''.join((o if isinstance(o, str) else json.dumps(o, ensure_ascii=False)) + '\n' for o in objs).encode('utf-8')


class TranscriptsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, name, objs):
        path = os.path.join(self.dir.name, *name.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(jsonl(objs))
        return path

    def owner_texts(self, path):
        return [r['text'] for r in read_records(path)[0] if r['kind'] == 'owner']

    def test_owner_filters(self):
        objs = [
            u('meta', isMeta=True),
            u('<command-name>/clear</command-name>\n<command-message>clear</command-message>'),
            u('<local-command-stdout>out</local-command-stdout>'),
            u('<system-reminder>r</system-reminder>'),
            u('<task-notification>done</task-notification>'),
            u([{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'файл'}]),
            u('сводка', isCompactSummary=True),
            u('Привет, сделай X <system-reminder>r</system-reminder>'),
            u([text('вторая часть')]),
            {'type': 'attachment', 'timestamp': TS, 'cwd': 'C:\\r', 'attachment': {'type': 'queued_command', 'prompt': 'в очереди'}},
            {'type': 'attachment', 'timestamp': TS, 'cwd': 'C:\\r', 'attachment': {'type': 'queued_command', 'prompt': 'м', 'isMeta': True}},
            {'type': 'attachment', 'timestamp': TS, 'cwd': 'C:\\r',
             'attachment': {'type': 'queued_command', 'prompt': 'системное', 'commandMode': 'task-notification'}},
        ]
        recs, _ = read_records(self.write('s.jsonl', objs))
        self.assertEqual([(r['kind'], r['text']) for r in recs if r['kind'] == 'owner'],
                         [('owner', 'Привет, сделай X'), ('owner', 'вторая часть'), ('owner', 'в очереди')])
        self.assertEqual((recs[0]['ts'], recs[0]['cwd'], recs[0]['pos']), (TS, 'C:\\r', len(jsonl(objs[:7]))))

    def test_a_queued_message_with_a_picture_keeps_its_text(self):  # stage 1 nit: a list prompt was lost
        q = {'type': 'attachment', 'timestamp': TS, 'cwd': 'C:\\r', 'attachment': {'type': 'queued_command', 'prompt': [
            {'type': 'text', 'text': 'посмотри скрин'}, {'type': 'image', 'source': {}}, {'type': 'text', 'text': 'и поправь'}]}}
        self.assertEqual(self.owner_texts(self.write('s.jsonl', [q])), ['посмотри скрин\nи поправь'])

    def test_a_tool_name_that_is_no_string_does_not_stop_the_file(self):  # stage 1 nit: it raised and the file was never indexed
        path = self.write('s.jsonl', [a([{'type': 'tool_use', 'id': 't', 'name': 7, 'input': {'command': 'ls'}}]), u('дальше')])
        self.assertEqual([(r['kind'], r['text']) for r in read_records(path)[0]], [('tool', 'unknown: ls'), ('owner', 'дальше')])

    def test_old_handoff_is_not_owner_text(self):
        path = self.write('s.jsonl', [u('[cg-handoff]\nстарое\n[/cg-handoff]\n\nновый черновик'), u('[cg-handoff]\nтолько он')])
        self.assertEqual(self.owner_texts(path), ['новый черновик'])
        self.assertEqual(strip_handoff('  [cg-handoff]x'), '')
        self.assertEqual(strip_handoff('a[cg-handoff]x[/cg-handoff]b'), 'ab')
        # the owner writing about the marker keeps every word
        self.assertEqual(strip_handoff('Что делает [cg-handoff] в начале? Поправь README.'), 'Что делает [cg-handoff] в начале? Поправь README.')

    def test_old_and_new_handoff_blocks_are_cut(self):  # the rename: transcripts of both times
        path = self.write('s.jsonl', [u('a [cg-handoff]x[/cg-handoff] b [al-handoff]y[/al-handoff] c')])
        self.assertEqual([t.split() for t in self.owner_texts(path)], [['a', 'b', 'c']])

    def test_old_and_new_lessons_blocks_are_cut(self):
        path = self.write('s.jsonl', [u('a [cg-lessons]x[/cg-lessons] b [al-lessons]y[/al-lessons] c')])
        self.assertEqual([t.split() for t in self.owner_texts(path)], [['a', 'b', 'c']])

    def test_tool_calls_of_both_names_are_skipped(self):
        path = self.write('s.jsonl', [a([tool('mcp__cacheguard__recall', query='q'), tool('mcp__alethite__recall', query='q'), tool('Bash', command='ls')])])
        self.assertEqual([r['text'] for r in read_records(path)[0]], ['Bash: ls'])

    def test_a_lone_surrogate_does_not_stop_the_file(self):
        path = self.write('s.jsonl', [json.dumps(u('ok \ud83d cut')), u('дальше')])  # the line holds the escape, as Claude Code writes it
        self.assertEqual(self.owner_texts(path), ['ok ? cut', 'дальше'])
        '\n'.join(self.owner_texts(path)).encode('utf-8')  # what the index and the handoff write

    def test_leading_reminder_keeps_owner_text(self):
        rem = '<system-reminder>r</system-reminder>\n'
        path = self.write('s.jsonl', [
            u(rem + 'печатный текст один'),
            u([text(rem + 'печатный текст два')]),
            {'type': 'attachment', 'timestamp': TS, 'cwd': 'C:\\r', 'attachment': {'type': 'queued_command', 'prompt': rem + 'печатный текст три'}},
            u('[cg-handoff]\nстарое\n[/cg-handoff]\n\n' + rem + 'печатный текст четыре'),
            u(rem + '<command-name>/clear</command-name>'),
            u('<system-reminder>без конца'),
        ])
        self.assertEqual(self.owner_texts(path), ['печатный текст один', 'печатный текст два', 'печатный текст три', 'печатный текст четыре'])

    def test_interrupt_markers_are_not_owner(self):
        path = self.write('s.jsonl', [u('[Request interrupted by user]'), u('[Request interrupted by user for tool use]'), u('продолжай')])
        self.assertEqual(self.owner_texts(path), ['продолжай'])

    def test_partial_last_line(self):
        data = jsonl([u('раз'), u('два'), u('три')])
        first_two = len(jsonl([u('раз'), u('два')]))
        path = os.path.join(self.dir.name, 's.jsonl')
        with open(path, 'wb') as f:
            f.write(data[:first_two + 10])
        recs, off = read_records(path)
        self.assertEqual((len(recs), off), (2, first_two))
        with open(path, 'ab') as f:
            f.write(data[first_two + 10:])
        recs2, off2 = read_records(path, off)
        self.assertEqual([(r['text'], r['pos']) for r in recs2], [('три', off)])
        self.assertEqual(off2, os.path.getsize(path))

    def test_tool_summary_and_edit(self):
        path = self.write('s.jsonl', [
            a([text('готово'), tool('Bash', command='git status'), tool('Edit', file_path='C:\\r\\a.py', old_string='x'),
               tool('Read', file_path='x'), tool('NotebookEdit', notebook_path='n.ipynb'), tool('Bash', command='a\nb'),
               tool('Bash', command='z' * 400), tool('TodoWrite', todos=[])]),
            '{оборванный json',
            a([text('синтетика')], model='<synthetic>'),
        ])
        recs, _ = read_records(path)
        tools = [r['text'] for r in recs if r['kind'] == 'tool']
        self.assertEqual(tools, ['Bash: git status', 'Edit: C:\\r\\a.py', 'Read: x', 'NotebookEdit: n.ipynb', 'Bash: a b',
                                 'Bash: ' + 'z' * 300, 'TodoWrite'])
        self.assertEqual([r['edit'] for r in recs if 'edit' in r], ['C:\\r\\a.py', 'n.ipynb'])
        self.assertEqual([r['text'] for r in recs if r['kind'] == 'claude'], ['готово'])

    def test_the_mods_own_tool_calls_get_no_record(self):
        # recall's own summary would be the top hit of the very query that made it
        path = self.write('s.jsonl', [a([text('ответ'), tool('mcp__cacheguard__recall', query='проба кнопки'), tool('mcp__cacheguard__recall_more', conv='S', pos=1),
                                         tool('mcp__other__recall', query='проба')])])
        self.assertEqual([(r['kind'], r['text']) for r in read_records(path)[0]], [('claude', 'ответ'), ('tool', 'mcp__other__recall: проба')])

    def test_the_lessons_the_mod_added_are_not_the_owners_words(self):  # stage 4
        path = self.write('s.jsonl', [u([{'type': 'text', 'text': 'опять 429'}, {'type': 'text', 'text': '[cg-lessons]\nурок\n[/cg-lessons]'}]),
                                      u('вопрос\n[cg-lessons]\nурок\n[/cg-lessons]\nи ещё')])
        self.assertEqual([r['text'] for r in read_records(path)[0]], ['опять 429', 'вопрос\n\nи ещё'])

    def test_a_mention_of_the_lessons_mark_keeps_every_word(self):  # the mod writes both marks: an unclosed one is the owner's text
        path = self.write('s.jsonl', [u('Что делает [cg-lessons] в сообщении? Поправь README.')])
        self.assertEqual(self.owner_texts(path), ['Что делает [cg-lessons] в сообщении? Поправь README.'])

    def test_a_failed_tool_call_is_an_error_record_a_good_one_none(self):  # stage 4: a past error is found by its trace
        err = lambda content: u([{'type': 'tool_result', 'tool_use_id': 't1', 'content': content, 'is_error': True}])
        path = self.write('s.jsonl', [
            a([tool('Bash', command='gh api /repos')]),
            err('HTTP 429:   API rate limit exceeded\n(limit 120 requests per hour)'),
            err([{'type': 'text', 'text': 'Exit code 1'}, {'type': 'image'}, {'type': 'text', 'text': 'ENOENT: ' + 'x' * 900}]),
            err('<system-reminder>шум</system-reminder>'),
            err('[cg-lessons]\nAction: a\n[/cg-lessons]'),  # the gate's denial: the lesson, not an error
            u([{'type': 'tool_result', 'tool_use_id': 't2', 'content': 'всё хорошо'}]),
            u([{'type': 'tool_result', 'tool_use_id': 't3', 'content': 'не ошибка', 'is_error': False}])])
        errors = [r['text'] for r in read_records(path)[0] if r['kind'] == 'error']
        self.assertEqual(errors[0], 'HTTP 429: API rate limit exceeded (limit 120 requests per hour)')
        self.assertTrue(errors[1].startswith('Exit code 1 ENOENT: x'))
        self.assertEqual(len(errors[1]), 503)  # 200 + ' … ' + 300
        self.assertEqual(len(errors), 2)  # a reminder alone, a lessons block alone, a good result and is_error false give none

    def test_a_bare_exit_code_and_a_bare_refusal_are_no_error_records(self):  # no trace to find again; a refusal with the owner's words stays
        bare = "The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it was a file edit, the new_string was NOT written to the file)"
        err = lambda content: u([{'type': 'tool_result', 'tool_use_id': 't1', 'content': content, 'is_error': True}])
        path = self.write('s.jsonl', [err('Exit code 1'), err(bare), err(bare + '.'), err(bare + '. To tell you how to proceed, the user said:\nне надо'),
                                      err('Exit code 2\nENOENT')])
        self.assertEqual([r['text'] for r in read_records(path)[0] if r['kind'] == 'error'],
                         [bare + '. To tell you how to proceed, the user said: не надо', 'Exit code 2 ENOENT'])

    def test_a_long_error_keeps_its_head_and_its_last_line(self):  # a traceback ends with the error itself
        trace = 'Traceback (most recent call last):\n' + '  File "a.py", line 1, in f\n    g()\n' * 40 + 'TypeError: unsupported operand'
        path = self.write('s.jsonl', [u([{'type': 'tool_result', 'tool_use_id': 't1', 'content': trace, 'is_error': True}])])
        text = next(r['text'] for r in read_records(path)[0] if r['kind'] == 'error')
        self.assertTrue(text.startswith('Traceback (most recent call last): File "a.py"'))
        self.assertTrue(text.endswith('TypeError: unsupported operand'))
        self.assertIn(' … ', text)

    def test_subagent_kinds(self):
        path = self.write('S/subagents/agent-a1.jsonl', [
            u('сделай Y'), a([text('шаг')]), u([{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'ок'}]), a([text('отчёт')], mid='m2')])
        recs, _ = read_records(path)
        self.assertEqual([r['kind'] for r in recs], ['task', 'sub', 'sub'])

    def test_project_key(self):
        self.assertEqual(project_key(r'C:\_git\X\.claude\worktrees\wf-1\src'), project_key(r'c:\_git\x'))
        self.assertEqual(project_key(r'C:\_git\X\.claude\worktrees\wf-1'), project_key('C:/_git/X'))
        self.assertNotEqual(project_key(r'C:\_git\X'), project_key(r'C:\_git\Y'))

    def test_project_key_under_two_profiles_is_one(self):  # stage 2 §5.4: the i3 (C:\Users\Ann) and the laptop (C:\Users\alice)
        i3, laptop = r'C:\Users\Ann', r'C:\Users\alice'
        self.assertEqual(project_key(r'C:\Users\Ann\work\proj', i3), r'~\work\proj')
        self.assertEqual(project_key(r'c:\users\alice\Work\Proj\.claude\worktrees\w1', laptop), r'~\work\proj')
        self.assertEqual(project_key(i3, i3 + '\\'), '~')
        self.assertEqual(project_key(r'C:\Users\Ann2\proj', i3), r'c:\users\ann2\proj')  # a prefix of the name is not the folder
        self.assertEqual(project_key(r'C:\_git\X', i3), project_key(r'C:\_git\X', laptop))  # outside the profile: the path itself
        self.assertEqual(project_key('/home/a/proj', '/home/a'), os.path.join('~', 'proj'))  # ~\\proj on Windows
        self.assertEqual(project_key('rel', '/nowhere'), project_key(os.path.abspath('rel'), '/nowhere'))  # a relative folder is made absolute

    def test_project_keys_add_the_old_folders_of_the_aliases_that_name_this_one(self):
        names = {r'C:\new\proj': [r'C:\old\proj', r'C:\older\proj']}
        self.assertEqual(project_keys(r'C:\new\proj', names), [project_key(r'C:\new\proj'), project_key(r'C:\old\proj'), project_key(r'C:\older\proj')])
        self.assertEqual(project_keys(r'c:\NEW\proj\.claude\worktrees\w1\src', names), project_keys(r'C:\new\proj', names))  # the key of the folder, not its spelling
        self.assertEqual(project_keys(r'C:\old\proj', names), [project_key(r'C:\old\proj')])  # one way only
        self.assertEqual(project_keys(r'C:\new\proj', {}), [project_key(r'C:\new\proj')])
        # two names for one folder (another case, a worktree) are merged, and no key comes twice
        two = {r'C:\new\proj': [r'C:\old\proj', r'C:\new\proj'], r'c:\NEW\PROJ\.claude\worktrees\w': [r'C:\old\proj', r'C:\x']}
        self.assertEqual(project_keys(r'C:\new\proj', two), [project_key(r'C:\new\proj'), project_key(r'C:\old\proj'), project_key(r'C:\x')])
        i3 = r'C:\Users\Ann'  # the keys of the old folders are made the way the owner's own are
        self.assertEqual(project_keys(r'C:\Users\Ann\new', {r'C:\Users\Ann\new': [r'C:\Users\Ann\old']}, i3), [r'~\new', r'~\old'])

    def test_find_session_and_last_usage(self):
        u1 = {'input_tokens': 9, 'cache_read_input_tokens': 9, 'cache_creation_input_tokens': 9, 'output_tokens': 9}
        u2 = {'input_tokens': 1, 'cache_read_input_tokens': 2, 'cache_creation_input_tokens': 3, 'output_tokens': 4}
        at = '2026-10-13T10:00:00.000Z'
        main = self.write('P/S.jsonl', [
            a([text('один')], mid='m1', usage=u1, ts='2026-10-13T09:00:00.000Z', model='claude-sonnet-5'),
            a([text('два')], mid='m2', usage=dict(u2, output_tokens=1), ts='2026-10-13T09:59:00.000Z', model='claude-opus-5-5'),
            a([tool('Read', file_path='x')], mid='m2', usage=u2, ts=at, model='claude-opus-5-5'),
            a([text('синтетика')], mid='m3', usage=u1, model='<synthetic>'),
            u('вопрос')])
        sub = self.write('P/S/subagents/agent-a1.jsonl', [u('сделай Y')])
        self.write('P/S/subagents/notes.jsonl', [u('не агент')])
        self.assertEqual(find_session(self.dir.name, 'S'), (main, [sub]))
        self.assertEqual(find_session(self.dir.name, 'nope'), (None, []))
        self.assertEqual(find_session(os.path.join(self.dir.name, 'нет'), 'S'), (None, []))
        w1, w2 = (self.write('P/W/subagents/' + n, [u('сделай Z')]) for n in ('agent-w1.jsonl', 'workflows/wf_1/agent-w2.jsonl'))
        self.write('P/W/subagents/workflows/wf_1/notes.jsonl', [u('не агент')])
        self.assertEqual(find_session(self.dir.name, 'W'), (None, [w1, w2]))
        self.assertEqual([r['kind'] for r in read_records(w2)[0]], ['task'])
        self.assertEqual(last_usage(main), {'tokens': 1 + 2 + 3 + 4, 'at': at, 'model': 'claude-opus-5-5'})

    def test_last_usage_beyond_tail(self):
        at = '2026-10-13T10:00:00.000Z'
        usage = {'input_tokens': 1, 'output_tokens': 2}
        far = self.write('P/F.jsonl', [a([text('один')], usage=usage, ts=at, model='m'), u('x' * 300000), u('y' * 300000)])
        self.assertEqual(last_usage(far), {'tokens': 3, 'at': at, 'model': 'm'})


if __name__ == '__main__':
    unittest.main()
