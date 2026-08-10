# -*- coding: utf-8 -*-
"""Периметр ИИ-помощника: проверка САМОГО SQL на синтетических данных.

Проверяется wiki.perimeter._AI_ELIGIBLE_SQL — сужение личного периметра чтения
до статей, которые вообще допустимо отправить во внешний ИИ. Логика доступа тут
не дублируется: читаемость считает visible_article_ids (её проверяет
tests/test_wiki_article_visibility.py), а здесь — только три отсечения сверх неё
(статус, строгий режим, рубильник ai_opt_out) и то, что периметр не расширяется.

Приём тот же, что в тесте видимости: в PostgreSQL CTE перекрывает одноимённую
таблицу, поэтому заглушки wiki_articles / wiki_article_sections / wiki_sections
подставляются ПЕРЕД боевым текстом запроса, и исполняется он сам, без правок.
Соединение read-only, боевые таблицы не читаются.

Отдельно закреплён случай, на котором план ошибался: статья БЕЗ разделов обязана
остаться в периметре. При семантике «выпадает, если ВСЕ разделы помечены»
all() по пустому множеству истинно, и такая статья исчезала бы молча, без ошибки
и без записи в лог. На проде она ровно одна и содержательная — «Классификатор
авто» (0 строк в wiki_article_sections), причём единственная, под которую заведены
правила доступа.
"""

import unittest

from tests import prod_db
from wiki.perimeter import _AI_ELIGIBLE_SQL

# Колонки заглушек — ровно те, что читает боевой запрос. Типы приводим явно:
# NULL в VALUES без приведения Postgres считает text, и сравнение падает.
_STUBS = """
WITH wiki_articles AS (
    SELECT id::int, status::text, strict_mode::boolean, ai_opt_out::boolean
      FROM (VALUES {articles}) AS t(id, status, strict_mode, ai_opt_out)
),
wiki_article_sections AS (
    SELECT article_id::int, section_id::int
      FROM (VALUES {sections}) AS t(article_id, section_id)
),
wiki_sections AS (
    SELECT id::int, ai_opt_out::boolean
      FROM (VALUES {section_flags}) AS t(id, ai_opt_out)
)
"""

_EMPTY = {
    'articles': "(NULL::int, NULL::text, NULL::bool, NULL::bool)",
    'sections': "(NULL::int, NULL::int)",
    'section_flags': "(NULL::int, NULL::bool)",
}


def _values(rows, fallback):
    return ', '.join(rows) if rows else fallback


class AiPerimeterSqlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.conn = prod_db.connection()

    def eligible(self, *, articles, sections=(), section_flags=(), candidates):
        stub = _STUBS.format(
            articles=_values(articles, _EMPTY['articles']),
            sections=_values(sections, _EMPTY['sections']),
            section_flags=_values(section_flags, _EMPTY['section_flags']),
        )
        # Боевой запрос начинается с "SELECT a.id" — заглушки приклеиваются
        # перед ним, сам текст не меняется.
        sql = stub + _AI_ELIGIBLE_SQL
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(sql, {'candidates': list(candidates)})
                return {row[0] for row in cursor.fetchall()}
        finally:
            prod_db.rollback()

    # ── Статус ───────────────────────────────────────────────────────────────
    def test_published_passes(self):
        got = self.eligible(articles=["(1, 'published', false, false)"],
                            candidates=[1])
        self.assertEqual({1}, got)

    def test_draft_excluded(self):
        """Черновик не источник для ответа, даже если автор его видит."""
        got = self.eligible(articles=["(1, 'draft', false, false)"],
                            candidates=[1])
        self.assertEqual(set(), got)

    def test_archived_excluded(self):
        got = self.eligible(articles=["(1, 'archived', false, false)"],
                            candidates=[1])
        self.assertEqual(set(), got)

    # ── Строгий режим ────────────────────────────────────────────────────────
    def test_strict_mode_excluded(self):
        """Чтение строгой статьи пишется в журнал поимённо; ответ ИИ — не журнал."""
        got = self.eligible(articles=["(1, 'published', true, false)"],
                            candidates=[1])
        self.assertEqual(set(), got)

    # ── Рубильник на статье ──────────────────────────────────────────────────
    def test_article_opt_out_excluded(self):
        got = self.eligible(articles=["(1, 'published', false, true)"],
                            candidates=[1])
        self.assertEqual(set(), got)

    # ── Рубильник на разделе ─────────────────────────────────────────────────
    def test_section_opt_out_excludes_article(self):
        got = self.eligible(
            articles=["(1, 'published', false, false)"],
            sections=["(1, 50)"],
            section_flags=["(50, true)"],
            candidates=[1],
        )
        self.assertEqual(set(), got)

    def test_one_flagged_section_is_enough(self):
        """Строгая семантика: помечен хотя бы один раздел — статья не уходит."""
        got = self.eligible(
            articles=["(1, 'published', false, false)"],
            sections=["(1, 50)", "(1, 51)"],
            section_flags=["(50, true)", "(51, false)"],
            candidates=[1],
        )
        self.assertEqual(set(), got)

    def test_clean_section_passes(self):
        got = self.eligible(
            articles=["(1, 'published', false, false)"],
            sections=["(1, 51)"],
            section_flags=["(51, false)"],
            candidates=[1],
        )
        self.assertEqual({1}, got)

    def test_article_without_sections_stays(self):
        """Статья без разделов обязана остаться — см. шапку файла."""
        got = self.eligible(
            articles=["(36, 'published', false, false)"],
            sections=["(1, 50)"],           # разделы есть, но у ДРУГОЙ статьи
            section_flags=["(50, true)"],
            candidates=[36],
        )
        self.assertEqual({36}, got)

    # ── Периметр не расширяется ──────────────────────────────────────────────
    def test_candidate_list_is_the_ceiling(self):
        """Пригодная статья вне переданного периметра не возвращается."""
        got = self.eligible(
            articles=["(1, 'published', false, false)",
                      "(2, 'published', false, false)"],
            candidates=[1],
        )
        self.assertEqual({1}, got)

    def test_mixed_set(self):
        got = self.eligible(
            articles=["(1, 'published', false, false)",
                      "(2, 'draft', false, false)",
                      "(3, 'published', true, false)",
                      "(4, 'published', false, true)",
                      "(5, 'published', false, false)"],
            sections=["(5, 50)"],
            section_flags=["(50, true)"],
            candidates=[1, 2, 3, 4, 5],
        )
        self.assertEqual({1}, got)


class EligibleHelperTest(unittest.TestCase):
    """Пустой периметр не должен доходить до базы вовсе."""

    def test_empty_candidates_short_circuit(self):
        from wiki import perimeter

        class ExplodingCursor:
            def execute(self, *args, **kwargs):
                raise AssertionError('запрос при пустом периметре не нужен')

        self.assertEqual(frozenset(),
                         perimeter.eligible_article_ids(ExplodingCursor(), []))

    def test_hash_is_order_independent(self):
        from wiki import perimeter

        self.assertEqual(perimeter.perimeter_hash([3, 1, 2]),
                         perimeter.perimeter_hash([1, 2, 3]))
        self.assertNotEqual(perimeter.perimeter_hash([1, 2]),
                            perimeter.perimeter_hash([1, 2, 3]))


if __name__ == '__main__':
    unittest.main()
