import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import cg
import review


def use(name, inp=None):
    return {'type': 'tool_use', 'name': name, 'input': inp or {}}


def line(ts, *blocks, kind='assistant'):
    return dict(type=kind, timestamp='2026-11-15T10:%02d:00.000Z' % ts, message={'role': kind, 'content': list(blocks)})


class ReviewTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name

    def put(self, rel, data):
        path = os.path.join(self.dir, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(data if isinstance(data, str) else ''.join(json.dumps(o) + '\n' for o in data))
        return path

    def plugins(self, installed, enabled=None, nested=True):
        self.put('.claude/plugins/installed_plugins.json', json.dumps({'version': 2, 'plugins': installed} if nested else installed))
        if enabled is not None:
            self.put('.claude/settings.json', json.dumps({'enabledPlugins': enabled}))

    def test_ponytail_installed_either_layout(self):
        for nested in (True, False):
            self.plugins({'ponytail@ponytail': [{'scope': 'user'}]}, {'ponytail@ponytail': True}, nested)
            self.assertTrue(review.ponytail(self.dir))

    def test_ponytail_without_settings_entry_counts(self):
        self.plugins({'ponytail@ponytail': [{}]})
        self.assertTrue(review.ponytail(self.dir))

    def test_ponytail_switched_off_or_other_plugin_is_false(self):
        self.plugins({'ponytail@ponytail': [{}]}, {'ponytail@ponytail': False})
        self.assertFalse(review.ponytail(self.dir))
        self.plugins({'alethite@alethite': [{}]}, {'alethite@alethite': True})
        self.assertFalse(review.ponytail(self.dir))

    def test_ponytail_missing_or_broken_files_are_false(self):
        self.assertFalse(review.ponytail(self.dir))
        self.put('.claude/plugins/installed_plugins.json', '{ not json')
        self.assertFalse(review.ponytail(self.dir))
        self.put('.claude/plugins/installed_plugins.json', '[1, 2]')
        self.assertFalse(review.ponytail(self.dir))

    def test_edits_count_after_the_last_review(self):
        edit = use('Edit')
        main = self.put('S.jsonl', [line(i, edit) for i in range(6)] + [line(7, use('Skill', {'skill': 'ponytail:ponytail-review'}))] + [line(8, use('Write')), line(9, use('NotebookEdit'))])
        self.assertEqual(review.edits([main]), 2)

    def test_edits_in_an_agent_file_count_and_order_is_by_time(self):
        main = self.put('S.jsonl', [line(1, use('Edit')), line(5, use('Skill', {'skill': 'ponytail-review'})), line(6, use('Read'))])
        sub = self.put('S/subagents/agent-a1.jsonl', [line(2, use('Edit')), line(7, use('Edit')), line(8, use('Write'))])
        self.assertEqual(review.edits([main, sub]), 2)

    def test_no_review_counts_all_and_other_reviews_count_as_review(self):
        self.assertEqual(review.edits([self.put('A.jsonl', [line(i, use('Edit')) for i in range(3)])]), 3)
        for ev in (line(5, use('Agent', {'prompt': 'run ponytail-review on the diff'})), line(5, {'type': 'text', 'text': '/ponytail-review'}, kind='user')):
            self.assertEqual(review.edits([self.put('B.jsonl', [line(1, use('Edit')), ev, line(6, use('Edit'))])]), 1)

    def test_typed_command_in_the_real_tag_format_is_a_review(self):
        tags = '<command-message>ponytail:ponytail-review</command-message>\n<command-name>/ponytail:ponytail-review</command-name>'
        for text in (tags, '<command-name>/ponytail-review</command-name>'):
            ev = line(5, {'type': 'text', 'text': text}, kind='user')
            self.assertEqual(review.edits([self.put('D.jsonl', [line(1, use('Edit')), ev, line(6, use('Edit'))])]), 1)
        other = line(5, {'type': 'text', 'text': '<command-name>/other:review</command-name>'}, kind='user')
        self.assertEqual(review.edits([self.put('E.jsonl', [line(1, use('Edit')), other])]), 1)

    def test_edits_missing_file_and_broken_lines_are_zero(self):
        self.assertEqual(review.edits([os.path.join(self.dir, 'none.jsonl'), self.put('C.jsonl', 'not json\n[1]\n')]), 0)

    def test_cg_review_hint_answers_both(self):
        self.plugins({'ponytail@ponytail': [{}]})
        self.put('projects/p/S1.jsonl', [line(i, use('Edit')) for i in range(5)])
        a = SimpleNamespace(projects=os.path.join(self.dir, 'projects'), session='S1')
        with mock.patch.dict(os.environ, {'USERPROFILE': self.dir, 'HOME': self.dir}):
            self.assertEqual(cg._review_hint(a), {'ponytail': True, 'edits': 5})
        with mock.patch.dict(os.environ, {'USERPROFILE': self.dir, 'HOME': self.dir}), mock.patch.object(review, 'edits', side_effect=AssertionError('scanned')):
            os.remove(os.path.join(self.dir, '.claude', 'plugins', 'installed_plugins.json'))
            self.assertEqual(cg._review_hint(a), {'ponytail': False, 'edits': 0})  # no ponytail: nothing is scanned
        self.plugins({'ponytail@ponytail': [{}]})
        a.session = 'nope'
        with mock.patch.dict(os.environ, {'USERPROFILE': self.dir, 'HOME': self.dir}):
            self.assertEqual(cg._review_hint(a), {'ponytail': True, 'edits': 0})


if __name__ == '__main__':
    unittest.main()
