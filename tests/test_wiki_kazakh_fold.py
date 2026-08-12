# -*- coding: utf-8 -*-
"""Казахские буквы, набранные на русской раскладке, обязаны находиться.

Люди пишут «Казына» вместо «Қазына», «Азаттык» вместо «Азаттық», «Тиркеуге»
вместо «Тіркеуге». Постгресу это разные слова: to_tsvector('russian', 'Қазына')
даёт лексему «қазын», а «Казына» — «казын», совпадения нет вообще. Замер на
проде: акция «7 Қазына» из статьи «Все акции» не находилась по запросу «7 Казына»
ни поиском, ни помощником.

Свёртка — тот же приём, которым в разделе уже свёрнуто ё→е, и главное требование
к нему: ОБЕ стороны, индекс и запрос, сворачиваются одинаково. Рассогласование
хуже отсутствия свёртки, поэтому здесь проверяется и правило, и то, что оно
одинаково стоит во всех местах сравнения текста.
"""

import re
import unittest

from tests import prod_db
from wiki import schema as wiki_schema
from wiki import search as wiki_search
from wiki import text as wiki_text
from wiki.ai import retrieve as ai_retrieve
from wiki.ai import similar as ai_similar


class FoldRuleTest(unittest.TestCase):
    def test_pairs_cover_all_kazakh_specific_letters(self):
        """Девять казахских букв плюс ё — весь набор, отличающий раскладки."""
        folded = {pair[0] for pair in wiki_text.KAZAKH_FOLD}
        self.assertEqual(set('әғқңөұүһіё'), folded)

    def test_sql_arguments_are_the_same_length(self):
        """translate() с разной длиной аргументов молча теряет буквы."""
        self.assertEqual(len(wiki_text.SQL_FOLD_FROM), len(wiki_text.SQL_FOLD_TO))

    def test_both_cases_are_folded(self):
        self.assertEqual('Казына', wiki_text.fold_kazakh('Қазына'))
        self.assertEqual('казына', wiki_text.fold_kazakh('қазына'))
        self.assertEqual('АЗАТТЫК', wiki_text.fold_kazakh('АЗАТТЫҚ'))

    def test_real_corpus_words(self):
        """Три слова, реально встречающиеся в статьях вики."""
        self.assertEqual('Азаттык', wiki_text.fold_kazakh('Азаттық'))
        self.assertEqual('Тиркеуге', wiki_text.fold_kazakh('Тіркеуге'))
        self.assertEqual('Улы', wiki_text.fold_kazakh('Ұлы'))

    def test_russian_text_is_untouched(self):
        self.assertEqual('Аренда 14 дней', wiki_text.fold_kazakh('Аренда 14 дней'))

    def test_yo_is_still_folded(self):
        """Свёртка ё→е была раньше и обязана сохраниться."""
        self.assertEqual('отчет', wiki_text.fold_kazakh('отчёт'))


class OneRuleEverywhereTest(unittest.TestCase):
    """Правило обязано стоять во ВСЕХ местах сравнения текста, и одно и то же.

    Рассогласование сторон — самый неприятный исход: индекс свёрнут, запрос нет,
    и поиск перестаёт находить даже то, что находил раньше.
    """

    PLACES = (
        ('схема: поисковый вектор статей', lambda: ' '.join(wiki_schema._SEARCH_STATEMENTS)),
        ('схема: вектор кусков помощника', lambda: ' '.join(wiki_schema._AI_STATEMENTS)),
        ('поиск по статьям', lambda: wiki_search._TITLE + wiki_search._ALIASES
                                     + wiki_search._SEARCH_SQL),
        ('поиск помощника', lambda: ai_retrieve._SEARCH_CHUNKS_SQL),
        ('поиск дублей', lambda: ai_similar._TITLE_SQL + ai_similar._TEXT_SQL),
    )

    def test_every_place_folds(self):
        for label, source in self.PLACES:
            with self.subTest(label):
                self.assertIn(wiki_text.SQL_FOLD_FROM, source(),
                              '%s: правило свёртки не применяется' % label)

    def test_no_stale_yo_only_rule_left(self):
        """Старое правило «только ё» не должно остаться нигде: это и есть рассогласование."""
        for label, source in self.PLACES:
            with self.subTest(label):
                self.assertNotIn("'ёЁ', 'еЕ'", source(), label)
                self.assertNotIn("'ё', 'е'", source(), label)


@unittest.skipIf(prod_db.skip_reason() is not None, 'база недоступна')
class PostgresBehaviourTest(unittest.TestCase):
    """Как это работает в самом постгресе — замер, а не рассуждение."""

    @classmethod
    def setUpClass(cls):
        cls.cursor = prod_db.connection().cursor()

    def tearDown(self):
        prod_db.rollback()

    def fold(self, expression):
        return "translate(%s, '%s', '%s')" % (
            expression, wiki_text.SQL_FOLD_FROM, wiki_text.SQL_FOLD_TO)

    def test_without_folding_there_is_no_match(self):
        """Фиксируем причину: без свёртки лексемы разные."""
        self.cursor.execute(
            "SELECT to_tsvector('russian', 'Акция 7 Қазына') "
            "@@ websearch_to_tsquery('russian', 'Казына')")
        self.assertFalse(self.cursor.fetchone()[0])

    def test_with_folding_query_matches(self):
        self.cursor.execute(
            "SELECT to_tsvector('russian', %s) @@ websearch_to_tsquery('russian', %s)"
            % (self.fold("'Акция 7 Қазына для курьеров'"), self.fold("'Казына'")))
        self.assertTrue(self.cursor.fetchone()[0])

    def test_reverse_direction_also_matches(self):
        """Набравший по-казахски обязан найти русское написание — свёртка двусторонняя."""
        self.cursor.execute(
            "SELECT to_tsvector('russian', %s) @@ websearch_to_tsquery('russian', %s)"
            % (self.fold("'Акция 7 Казына для курьеров'"), self.fold("'Қазына'")))
        self.assertTrue(self.cursor.fetchone()[0])

    def test_trigram_similarity_becomes_exact(self):
        """Поиск по названию: 0,4 без свёртки против 1,0 с ней."""
        self.cursor.execute("SELECT similarity('Қазына', 'Казына')")
        self.assertLess(float(self.cursor.fetchone()[0]), 0.5)
        self.cursor.execute("SELECT similarity(%s, 'Казына')" % self.fold("'Қазына'"))
        self.assertEqual(1.0, float(self.cursor.fetchone()[0]))

    def test_schema_expression_is_valid_sql(self):
        """Выражение генерируемой колонки обязано исполняться постгресом."""
        fragment = re.search(r"setweight\(to_tsvector\('russian',\s*translate\(coalesce\(text.*?'D'\)",
                             ' '.join(wiki_schema._AI_STATEMENTS), re.S)
        self.assertIsNotNone(fragment, 'выражение не найдено в схеме')
        self.cursor.execute(
            'SELECT %s' % fragment.group(0).replace('text', "'Қазына'::text", 1))
        self.assertIn('казын', self.cursor.fetchone()[0])


if __name__ == '__main__':
    unittest.main()
