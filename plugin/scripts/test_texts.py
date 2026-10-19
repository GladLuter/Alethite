import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import texts
from texts import EN, RU, TABLES, t

CG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cg.py')


def use_lang(test, lang):
    """The texts of `lang` for the rest of `test`, then back to what they were."""
    test.addCleanup(setattr, texts, 'lang', texts.lang)
    texts.lang = lang


class TextsTest(unittest.TestCase):
    def test_english_by_default(self):
        self.assertEqual(texts.lang, 'en')
        self.assertEqual(t('ollama_down', e='x'), 'Ollama is not answering: x')

    def test_russian_gives_the_stage_1_texts(self):
        use_lang(self, 'ru')
        self.assertEqual(t('ollama_down', e='x'), 'Ollama не отвечает: x')
        self.assertEqual(t('handoff_owner'), '**Ты:** ')

    def test_a_missing_key_comes_from_english(self):
        with mock.patch.dict(TABLES, {'xx': {'nothing': 'nada'}}):
            use_lang(self, 'xx')
            self.assertEqual(t('nothing'), 'nada')
            self.assertEqual(t('numpy_missing'), EN['numpy_missing'])

    def test_an_unknown_language_is_english(self):
        use_lang(self, 'zz')
        self.assertEqual(t('nothing'), EN['nothing'])

    def test_every_table_has_the_keys_of_english_and_no_others(self):
        for name, table in TABLES.items():
            self.assertEqual(set(table), set(EN), name)

    def test_the_fields_of_a_text_are_the_same_in_every_language(self):  # a field only one language knows would raise KeyError there
        import string
        fields = lambda s: sorted(f for _, f, _, _ in string.Formatter().parse(s) if f)
        for key, text in RU.items():
            self.assertEqual(fields(text), fields(EN[key]), key)

    def test_cli_lang(self):
        with tempfile.TemporaryDirectory() as d:
            env = {k: v for k, v in os.environ.items() if k not in ('PYTHONIOENCODING', 'PYTHONUTF8')}
            run = lambda *lang: subprocess.run([sys.executable, CG, '--db', os.path.join(d, 'index.db'), '--projects', d, *lang, 'handoff',
                                                '--session', 'S', '--today', '2026-10-19'], capture_output=True, env=env).stdout.decode('utf-8')
            os.makedirs(os.path.join(d, 'P'))
            with open(os.path.join(d, 'P', 'S.jsonl'), 'w', encoding='utf-8') as f:
                f.write('{"type":"user","timestamp":"2026-10-19T10:00:00Z","cwd":"C:\\\\a","message":{"role":"user","content":"hi"}}\n')
            self.assertIn('Handoff from the previous chat', run())
            self.assertIn('Передача дел от предыдущего чата', run('--lang', 'ru'))


if __name__ == '__main__':
    unittest.main()
