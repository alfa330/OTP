# -*- coding: utf-8 -*-
"""Поиск дубля статьи: боевые SQL по синтетическим статьям.

Запросы исполняются НАСТОЯЩИМ постгресом (CTE перекрывает одноимённую таблицу,
приём как в test_wiki_ai_retrieval), потому что проверять здесь надо именно то,
чего не проверить в Python: работу similarity() из pg_trgm, поведение IDF при
нулевой документной частоте и деление на суммарный вес слов.

Пороги фичи измерены на боевом корпусе и зафиксированы в шапке wiki/ai/similar.py.
Здесь проверяется МЕХАНИКА, а не пороги: что дубль по названию находится даже
когда триграммы бессильны, что покрытие по тексту — доля, а не «лучший из
выдачи», и что вердикт не выставляется на пустой находке.
"""

import unittest

from tests import prod_db
from wiki.ai import similar as ai_similar

# Синтетические статьи. Заголовки нарочно повторяют боевые случаи: настоящий
# дубль «Рабочие сайты» существует на проде дважды (id 11 и 24, обе опубликованы),
# а «Отпуск» внутри «Отпуск, больничный и отгулы» даёт всего 0,280 по триграммам.
ARTICLES = [
    (1, 'Рабочие сайты', 'rabochie-sajty', 'published', 'Ссылки на рабочие сервисы',
     'таксометр диспетчерская платформа яндекс профиль водителя ссылки сервисы'),
    (2, 'Отпуск, больничный и отгулы', 'otpusk', 'published', 'Как оформить отпуск',
     'отпуск оформляется заявлением больничный лист отгул руководитель кадры'),
    (3, 'Термопакет', 'termopaket', 'published', 'Выдача термопакета',
     'термопакет выдаётся депозит пять тысяч тенге туркестан возврат'),
    (4, 'Баллы приоритета', 'bally', 'archived', 'Как считаются баллы',
     'баллы приоритета начисляются рейтинг активность выполненные заказы'),
]

# Привязка статей к разделам: раздел показывается в панели и различает статьи с
# одинаковыми названиями.
SECTIONS = [(1, 10), (2, 20), (3, 10), (4, 20)]
SECTION_NAMES = [(10, 'Работа с водителями'), (20, 'Кадры')]

# Разделы тоже подменяем: без этого запрос названий разделов ушёл бы в боевые
# таблицы, и тест зависел бы от того, как сейчас устроено дерево на проде.
_STUB = """
WITH wiki_articles AS (
    SELECT id, title, slug, status, summary, content_plain
      FROM unnest(%(a_id)s::int[], %(a_title)s::text[], %(a_slug)s::text[],
                  %(a_status)s::text[], %(a_summary)s::text[], %(a_plain)s::text[])
           AS t(id, title, slug, status, summary, content_plain)
),
wiki_article_sections AS (
    SELECT article_id, section_id
      FROM unnest(%(s_article)s::int[], %(s_section)s::int[])
           AS t(article_id, section_id)
),
wiki_sections AS (
    SELECT id, name
      FROM unnest(%(sec_id)s::int[], %(sec_name)s::text[]) AS t(id, name)
),
"""


class _StubCursor:
    """Курсор, подставляющий синтетические статьи вместо боевой таблицы."""

    def __init__(self, cursor):
        self._cursor = cursor
        self._params = {
            'a_id': [row[0] for row in ARTICLES],
            'a_title': [row[1] for row in ARTICLES],
            'a_slug': [row[2] for row in ARTICLES],
            'a_status': [row[3] for row in ARTICLES],
            'a_summary': [row[4] for row in ARTICLES],
            'a_plain': [row[5] for row in ARTICLES],
            's_article': [row[0] for row in SECTIONS],
            's_section': [row[1] for row in SECTIONS],
            'sec_id': [row[0] for row in SECTION_NAMES],
            'sec_name': [row[1] for row in SECTION_NAMES],
        }

    def execute(self, sql, params=None):
        merged = dict(self._params)
        merged.update(params or {})
        if sql.lstrip().upper().startswith('WITH'):
            # У запроса уже есть WITH — свою заглушку вклеиваем в его начало.
            sql = _STUB + sql.lstrip()[len('WITH'):].lstrip()
        else:
            sql = _STUB.rstrip(',\n') + '\n' + sql
        return self._cursor.execute(sql, merged)

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchone(self):
        return self._cursor.fetchone()


class SimilarSqlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.connection = prod_db.connection()
        cls.raw = cls.connection.cursor()
        cls.cursor = _StubCursor(cls.raw)

    @classmethod
    def tearDownClass(cls):
        prod_db.rollback()

    def tearDown(self):
        prod_db.rollback()

    def all_ids(self):
        return [row[0] for row in ARTICLES]

    # ── название ─────────────────────────────────────────────────────────
    def test_identical_title_is_found(self):
        rows = ai_similar.by_title(self.cursor, article_ids=self.all_ids(),
                                   title='Рабочие сайты')
        self.assertTrue(rows)
        self.assertEqual(1, rows[0]['article_id'])
        self.assertGreaterEqual(rows[0]['score'], 0.99)

    def test_short_title_inside_long_one_is_found(self):
        """Триграммы дают тут 0,280 — ловит правило вхождения, а не similarity."""
        rows = ai_similar.by_title(self.cursor, article_ids=self.all_ids(),
                                   title='Отпуск')
        found = {row['article_id'] for row in rows}
        self.assertIn(2, found)

    def test_unrelated_title_is_not_found(self):
        rows = ai_similar.by_title(self.cursor, article_ids=self.all_ids(),
                                   title='Регламент выдачи корпоративных карт')
        self.assertEqual([], rows)

    def test_excluded_article_is_skipped(self):
        rows = ai_similar.by_title(self.cursor, article_ids=self.all_ids(),
                                   title='Рабочие сайты', exclude_id=1)
        self.assertNotIn(1, {row['article_id'] for row in rows})

    def test_archived_article_is_still_a_duplicate(self):
        """Дубль в архиве — тоже дубль. Скрыть его значит позвать третью копию."""
        rows = ai_similar.by_title(self.cursor, article_ids=self.all_ids(),
                                   title='Баллы приоритета')
        self.assertEqual('archived', rows[0]['status'])

    def test_excerpt_comes_with_the_row(self):
        """Отрывок нужен в ответе: открытие статьи писало бы просмотр."""
        rows = ai_similar.by_title(self.cursor, article_ids=self.all_ids(),
                                   title='Термопакет')
        self.assertIn('термопакет', rows[0]['excerpt'].lower())

    # ── текст ────────────────────────────────────────────────────────────
    def test_text_coverage_is_a_share_not_a_ranking(self):
        """Балл — доля веса слов документа, поэтому он сравним между запросами.

        Нормировка «на лучшего из выдачи» давала бы почти единицу всегда, даже
        когда похожего нет вовсе.
        """
        words = ['термопакет', 'депозит', 'тенге', 'туркестан', 'возврат']
        rows = ai_similar.by_text(self.cursor, article_ids=self.all_ids(), words=words)
        best = next(row for row in rows if row['article_id'] == 3)
        self.assertGreaterEqual(best['score'], 0.9)

    def test_partial_overlap_scores_low(self):
        words = ['термопакет', 'депозит', 'кассир', 'инкассация', 'сейф', 'ключи']
        rows = ai_similar.by_text(self.cursor, article_ids=self.all_ids(), words=words)
        for row in rows:
            self.assertLess(row['score'], 0.8)

    def test_unknown_words_do_not_break_the_query(self):
        """Слова, которых нет ни в одной статье, дают df=0 — деления на ноль быть не должно."""
        rows = ai_similar.by_text(self.cursor, article_ids=self.all_ids(),
                                 words=['абракадабра', 'квазимодо'])
        self.assertEqual([], rows)

    def test_short_words_are_ignored(self):
        rows = ai_similar.by_text(self.cursor, article_ids=self.all_ids(),
                                 words=['и', 'на', 'по'])
        self.assertEqual([], rows)

    # ── сведение ─────────────────────────────────────────────────────────
    def test_verdict_says_duplicate_on_full_match(self):
        found = ai_similar.find_duplicates(
            self.cursor, visible_ids=self.all_ids(), indexed_ids=[],
            title='Рабочие сайты',
            text_words=['таксометр', 'диспетчерская', 'платформа', 'профиль'])
        self.assertEqual('дубль', found['verdict'])
        self.assertEqual(1, found['items'][0]['article_id'])
        self.assertFalse(found['vector_covered'])

    def test_no_verdict_when_nothing_matches(self):
        found = ai_similar.find_duplicates(
            self.cursor, visible_ids=self.all_ids(), indexed_ids=[],
            title='Регламент инкассации выручки',
            text_words=['инкассация', 'сейф', 'кассир'])
        self.assertIsNone(found['verdict'])
        self.assertEqual([], found['items'])

    def test_reasons_are_merged_for_one_article(self):
        found = ai_similar.find_duplicates(
            self.cursor, visible_ids=self.all_ids(), indexed_ids=[],
            title='Термопакет',
            text_words=['термопакет', 'депозит', 'тенге', 'туркестан', 'возврат'])
        item = next(row for row in found['items'] if row['article_id'] == 3)
        self.assertIn('название', item['found_by'])
        self.assertIn('текст', item['found_by'])

    def test_section_name_distinguishes_same_titles(self):
        """На проде три пары статей с одинаковыми названиями — раздел различает их."""
        found = ai_similar.find_duplicates(
            self.cursor, visible_ids=self.all_ids(), indexed_ids=[],
            title='Термопакет', text_words=['термопакет', 'депозит'])
        item = next(row for row in found['items'] if row['article_id'] == 3)
        self.assertEqual('Работа с водителями', item['section'])

    def test_vector_coverage_is_reported_honestly(self):
        """«Похожего нет» при неполном покрытии значит меньше, чем кажется."""
        found = ai_similar.find_duplicates(
            self.cursor, visible_ids=self.all_ids(), indexed_ids=[], title='Отпуск',
            text_words=['отпуск'], vector=None)
        self.assertFalse(found['vector_covered'])


if __name__ == '__main__':
    unittest.main()
