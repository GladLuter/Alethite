import unittest
from compress import LEVELS, compress

EN = 'The cache is expired because the model was changed, and this is why we need to run the index again. It is not a bug.'
RU = 'Это очень просто: кэш истёк, потому что модель была изменена, и это не ошибка. Нет, индекс тут ни при чём.'
UK = 'Це дуже просто: кеш сплив, бо модель було змінено, і це не помилка. Ні, індекс тут ні до чого.'


class CompressTest(unittest.TestCase):
    def test_off_and_an_unknown_level(self):
        self.assertEqual(compress(EN, 'off'), EN)
        self.assertEqual(compress('', 'ultra'), '')
        with self.assertRaisesRegex(ValueError, 'ultra'):
            compress(EN, 'max')

    def test_each_level_takes_more_and_ultra_saves_a_fifth(self):
        for text in (EN, RU, UK):
            sizes = [len(compress(text, level)) for level in LEVELS]
            self.assertEqual(sizes, sorted(sizes, reverse=True), text)
            self.assertLess(sizes[-1], 0.8 * len(text), text)

    def test_the_same_text_gives_the_same_result_and_a_second_pass_changes_nothing(self):
        for level in LEVELS:
            for text in (EN, RU, UK):
                self.assertEqual(compress(text, level), compress(text, level))
                self.assertEqual(compress(compress(text, level), level), compress(text, level), (level, text))

    def test_function_words_go_negations_and_joining_words_stay(self):
        out = compress(EN, 'full').split()
        for gone in ('The', 'is', 'the', 'this'):
            self.assertNotIn(gone, out)
        for kept in ('not', 'because', 'why', 'need', 'to'):
            self.assertIn(kept, out)
        ru, uk = compress(RU, 'full'), compress(UK, 'full')
        self.assertNotIn('очень', ru)
        self.assertNotIn('дуже', uk)
        for kept in ('не', 'Нет', 'ни', 'потому'):
            self.assertIn(kept, ru.split() + [w.strip(',.') for w in ru.split()])
        for kept in ('не', 'Ні', 'ні', 'бо'):
            self.assertIn(kept, [w.strip(',.') for w in uk.split()])

    def test_ultra_drops_punctuation_and_capitals_of_plain_words_only(self):
        out = compress('Кэш, API и GitHub: работает? «Модель» — да. M a.txt', 'ultra')
        self.assertEqual(out, 'кэш API GitHub работает? «модель» да M a.txt')

    def test_code_paths_links_numbers_names_and_flags_go_through_as_they_are(self):
        keep = ['`cg.py index --rebuild`', 'C:\\Users\\AIJ\\x.py', '~/.claude/projects', 'https://example.com/a_b?c=1', '42', 'snake_case',
                '--no-verify', 'v2.1.303', '[cg-handoff]', '#123', 'x=1']
        text = 'The ' + ' and the '.join(keep) + ' is here.'
        for level in LEVELS:
            out = compress(text, level)
            for k in keep:
                self.assertIn(k, out, (level, k))
        block = 'Сделал это:\n```python\nx  =  1\n\n\n\nprint( x )  # это код\n```\nГотово.'
        for level in LEVELS:
            self.assertIn('```python\nx  =  1\n\n\n\nprint( x )  # это код\n```', compress(block, level))

    def test_a_fence_cut_off_by_a_snippet_is_code_to_the_end(self):
        self.assertEqual(compress('Это пример:\n```\nэто код и он не сжимается', 'ultra'), 'пример\n```\nэто код и он не сжимается')

    def test_the_word_the_search_matched_stays(self):
        self.assertEqual(compress('…кэш «истёк» и «это» тоже…', 'full'), '…кэш «истёк» «это» …')  # the cut stays too

    def test_lite_squeezes_spaces_blank_lines_and_emphasis_and_keeps_words_and_indents(self):
        text = '**Важно:**  кэш   истёк.\n\n\n\n- пункт\n    - вложенный   пункт  \n'
        self.assertEqual(compress(text, 'lite'), 'Важно: кэш истёк.\n\n- пункт\n    - вложенный пункт')


if __name__ == '__main__':
    unittest.main()
