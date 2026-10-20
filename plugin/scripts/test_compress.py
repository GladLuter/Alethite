import time
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
        self.assertEqual(out, 'кэш API GitHub работает? «Модель» да M a.txt')  # the word the search matched keeps its case too

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

    def test_a_fence_cut_in_two_by_a_snippet_leaves_the_whole_text_as_it_is(self):  # which side is code cannot be told
        for text in ('Это пример:\n```\nэто код и он не сжимается', '…if row.name in «seen»:\n    pass\n```\nafter the code'):
            for level in LEVELS:
                self.assertEqual(compress(text, level), text, (level, text))

    def test_commands_keep_their_lone_dashes_and_dots_a_list_loses_its_dash(self):  # the review of PR 1
        for text in ('cd ..', 'git checkout -', 'npm run build -- --prod', 'git add . && git push'):
            self.assertEqual(compress(text, 'ultra'), text)
        self.assertEqual(compress('- пункт списка\n- второй', 'ultra'), 'пункт списка\nвторой')

    def test_abbreviations_of_and_who_keep_the_sense(self):  # the review of PR 1
        for text, want in [('at 9 AM', '9 AM'), ('Mode: ON', 'mode ON'), ('12 of 12 tests passed; 3 of 40 failed', '12 of 12 tests passed 3 of 40 failed'),
                           ('who deleted the file?', 'who deleted file?')]:
            self.assertEqual(compress(text, 'ultra'), want, text)
        self.assertEqual(compress('И это всё', 'ultra'), 'всё')  # a one-letter word in capitals is no abbreviation

    def test_emphasis_goes_around_words_only(self):
        for text in ('x**2 + y**2', 'src/**/*.ts and src/**/*.js'):
            self.assertEqual(compress(text, 'lite'), text)
        self.assertEqual(compress('**жирный** текст', 'lite'), 'жирный текст')

    def test_long_runs_stay_linear(self):  # each of these took 7-17 s
        for text, level in [('x' + ' ' * 40000 + 'x', 'lite'), ('**a ' * 20000, 'lite'), ('**a ' * 20000, 'ultra')]:
            start = time.monotonic()
            compress(text, level)
            self.assertLess(time.monotonic() - start, 2, level)

    def test_the_word_the_search_matched_stays(self):
        self.assertEqual(compress('…кэш «истёк» и «это» тоже…', 'full'), '…кэш «истёк» «это» …')  # the cut stays too

    def test_the_sense_stays(self):  # each of these turned into its opposite or into something else
        for text, kept in [('The compression is on and the cache is off', ' on '), ("It's not just a cache bug", 'just'),
                           ('Plan A or plan B? Option A is faster', 'option A'), ('Если бы кэш не истёк, мы бы не потеряли данные', 'бы'),
                           ('истёк ли кэш', 'ли'), ('Це не просто баг', 'просто')]:
            self.assertIn(kept, compress(text, 'ultra'), text)

    def test_lite_squeezes_spaces_blank_lines_and_emphasis_and_keeps_words_and_indents(self):
        text = '**Важно:**  кэш   истёк.\n\n\n\n- пункт\n    - вложенный   пункт  \n'
        self.assertEqual(compress(text, 'lite'), 'Важно: кэш истёк.\n\n- пункт\n    - вложенный пункт')


if __name__ == '__main__':
    unittest.main()
