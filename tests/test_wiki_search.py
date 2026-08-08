# -*- coding: utf-8 -*-
"""Поиск: порт словаря алиасов и поведение SQL-запроса.

Первая часть — чистый Python: транслитерация, раскладка, алиасы. Это ровно то,
что составляло всю «умность» поиска исходной вики: синонимов Meilisearch,
стоп-слов и кастомных правил ранжирования там НЕ настроено, проверено по
дереву конфигов. Значит при смене движка терять нечего, но перенести словарь
надо точно.

Вторая часть — сам поисковый SQL на настоящем PostgreSQL. Приём тот же, что в
тесте видимости: CTE перекрывает одноимённую таблицу, поэтому боевой текст
запроса исполняется над синтетическими строками через read-only соединение.
"""

import os
import re
import unittest
from pathlib import Path

try:
    import psycopg2
except ImportError:  # pragma: no cover
    psycopg2 = None

from wiki import search as wiki_search
from wiki.text import (
    ALIAS_GROUPS,
    aliases_for_text,
    fix_keyboard_layout,
    normalize_text,
    query_variants,
    search_aliases_for_article,
    transliterate_cyrillic_to_latin,
    transliterate_latin_to_cyrillic,
)

ROOT = Path(__file__).resolve().parents[1]


def _dsn():
    env = os.environ.get('DATABASE_URL_READONLY')
    if env:
        return env
    local = ROOT / '.env.codex.local'
    if not local.exists():
        return None
    text = local.read_text(encoding='utf-8', errors='replace')
    match = re.search(r'^DATABASE_URL_READONLY\s*=\s*(.+)$', text, re.M)
    return match.group(1).strip().strip('"\'') if match else None


DSN = _dsn()


class NormalizeTest(unittest.TestCase):
    def test_case_and_punctuation(self):
        self.assertEqual(normalize_text('  Аренда, ТРАНСПОРТА!  '), 'аренда транспорта')

    def test_yo_becomes_latin_e(self):
        """В оригинале ё заменяется на ЛАТИНСКУЮ e. Из-за этого в самой вике
        словарь алиасов не срабатывал на написание с ё — там ключи брались
        без нормализации. У нас нормализуются и ключи, см. AliasTest."""
        self.assertEqual(normalize_text('хёндай'), 'хeндай')

    def test_kazakh_letters_survive(self):
        self.assertEqual(normalize_text('Қарағанды'), 'қарағанды')

    def test_empty(self):
        self.assertEqual(normalize_text(''), '')
        self.assertEqual(normalize_text(None), '')


class TransliterationTest(unittest.TestCase):
    def test_cyrillic_to_latin(self):
        self.assertEqual(transliterate_cyrillic_to_latin('щука'), 'shchuka')
        # ж->zh, ё->e, л->l, т->t, ы->y, й->y
        self.assertEqual(transliterate_cyrillic_to_latin('жёлтый'), 'zheltyy')

    def test_latin_to_cyrillic_longest_first(self):
        """Порядок замен важен: 'sh' не должен съесть часть 'shch'."""
        self.assertEqual(transliterate_latin_to_cyrillic('shchuka'), 'щука')

    def test_keyboard_layout(self):
        self.assertEqual(fix_keyboard_layout('ntrcn'), 'текст')
        self.assertEqual(fix_keyboard_layout('fhtylf'), 'аренда')


class AliasTest(unittest.TestCase):
    def test_dictionary_size(self):
        """Фиксируем реальный объём словаря: 80 групп, а не 110,
        как утверждалось в первичном исследовании."""
        self.assertEqual(len(ALIAS_GROUPS), 80)

    def test_misspelled_car_brand(self):
        """Главный сценарий: «хундай» обязан находить Hyundai."""
        self.assertIn('hyundai', aliases_for_text('хундай'))
        self.assertIn('hyundai', aliases_for_text('хёндай'))
        self.assertIn('хендай', aliases_for_text('hyundai'))

    def test_model_names(self):
        self.assertIn('camry', aliases_for_text('камри'))
        self.assertIn('солярис', aliases_for_text('solaris'))

    def test_short_words_ignored(self):
        self.assertEqual(aliases_for_text('а'), [])

    def test_no_alias_for_unknown(self):
        self.assertEqual(aliases_for_text('невероятнаястатья'), [])

    def test_every_group_is_symmetric(self):
        """Любое написание группы обязано находить все остальные."""
        for group in ALIAS_GROUPS:
            for word in group:
                found = set(aliases_for_text(word))
                expected = {w for w in group if w != word}
                # Многословные и составные написания («вай фай», «render.com»)
                # разбиваются нормализацией на несколько слов — для них
                # симметрия по определению неполная.
                from wiki.text import _normalize_for_index, _WORD_SPLIT
                if len(_WORD_SPLIT.split(_normalize_for_index(word))) > 1:
                    continue
                expected = {w for w in expected
                            if len(_WORD_SPLIT.split(_normalize_for_index(w))) == 1}
                self.assertTrue(expected <= found,
                                'группа %r: из %r не нашлись %r' % (group, word, expected - found))


class QueryVariantsTest(unittest.TestCase):
    def test_original_first(self):
        self.assertEqual(query_variants('Аренда')[0], 'Аренда')

    def test_layout_variant_present(self):
        self.assertIn('текст', query_variants('ntrcn'))

    def test_alias_variant_present(self):
        self.assertIn('hyundai', query_variants('хундай'))

    def test_no_duplicates(self):
        variants = query_variants('камри')
        self.assertEqual(len(variants), len(set(variants)))

    def test_empty_query(self):
        self.assertEqual(query_variants(''), [])


class ArticleAliasesTest(unittest.TestCase):
    def test_includes_transliteration_and_synonyms(self):
        value = search_aliases_for_article('Аренда Hyundai', 'Условия', ['транспорт'])
        self.assertIn('hyundai', value)
        self.assertIn('хендай', value, 'синонимы должны попасть в поле поиска')

    def test_no_body_text(self):
        """Тело статьи целиком в алиасы не идёт: в проде три статьи весят
        по 200-900 КБ из-за картинок, раздувать индекс незачем."""
        value = search_aliases_for_article('Заголовок', '', [], 'я' * 50000)
        self.assertLess(len(value), 3000)


@unittest.skipIf(psycopg2 is None or not DSN, 'нет psycopg2 или DATABASE_URL_READONLY')
class SearchSqlTest(unittest.TestCase):
    """Боевой SQL поиска на синтетических статьях."""

    STUB = """
WITH wiki_articles AS (
    SELECT id::int, slug::text, title::text, summary::text, status::text,
           views::int, NULL::timestamp AS updated_at,
           content_plain::text, search_aliases::text,
           setweight(to_tsvector('russian', coalesce(title, '')),          'A') ||
           setweight(to_tsvector('russian', coalesce(search_aliases, '')), 'B') ||
           setweight(to_tsvector('russian', coalesce(summary, '')),        'C') ||
           setweight(to_tsvector('russian', coalesce(content_plain, '')),  'D') AS search_vector
      FROM (VALUES {rows}) AS t(
        id, slug, title, summary, status, views, content_plain, search_aliases)
),
wiki_article_sections AS (
    SELECT article_id::int, section_id::int
      FROM (VALUES {sections}) AS t(article_id, section_id)
),
"""

    @classmethod
    def setUpClass(cls):
        cls.conn = psycopg2.connect(DSN, connect_timeout=30)
        cls.conn.set_session(readonly=True)
        cur = cls.conn.cursor()
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
        cls.has_trigram = cur.fetchone() is not None
        cls.conn.rollback()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def run_search(self, rows, query, *, sections=None, with_trigram=False, section_id=None):
        stub = self.STUB.format(
            rows=', '.join(rows),
            sections=', '.join(sections) if sections
            else '(NULL::int, NULL::int)',
        )
        sql = wiki_search.build_sql(with_trigram).replace('WITH q AS (', stub + 'q AS (', 1)
        cur = self.conn.cursor()
        try:
            cur.execute(sql, {'ids': [1, 2, 3], 'query': query,
                              'section': section_id, 'limit': 10})
            return [dict(zip(wiki_search._KEYS, row)) for row in cur.fetchall()]
        finally:
            self.conn.rollback()
            cur.close()

    ARTICLES = [
        "(1, 'arenda', 'Аренда транспорта', 'Условия аренды машины', 'published', 10,"
        " 'Здесь описаны условия аренды автомобиля в парке', 'arenda transporta')",
        "(2, 'bally', 'Баллы приоритета', 'Как начисляются баллы', 'published', 5,"
        " 'Баллы приоритета влияют на распределение заказов', 'bally prioriteta')",
        "(3, 'hyundai', 'Обслуживание Hyundai', 'Сервис', 'published', 1,"
        " 'Регламент обслуживания', 'hyundai хендай хёндай хундай')",
    ]

    def test_finds_by_title(self):
        found = self.run_search(self.ARTICLES, 'аренда')
        self.assertEqual([a['id'] for a in found], [1])

    def test_finds_by_body(self):
        found = self.run_search(self.ARTICLES, 'заказов')
        self.assertEqual([a['id'] for a in found], [2])

    def test_title_ranks_above_body(self):
        """Вес A у заголовка обязан перебивать вес D у текста."""
        found = self.run_search(self.ARTICLES, 'баллы')
        self.assertEqual(found[0]['id'], 2)

    def test_snippet_is_highlighted(self):
        found = self.run_search(self.ARTICLES, 'аренды')
        self.assertTrue(found)
        self.assertIn('<mark>', found[0]['snippet'])

    def test_alias_field_is_searchable(self):
        """«хундай» лежит в search_aliases статьи — и находит её."""
        found = self.run_search(self.ARTICLES, 'хундай')
        self.assertEqual([a['id'] for a in found], [3])

    def test_section_filter(self):
        found = self.run_search(self.ARTICLES, 'аренда',
                                sections=['(1, 7)'], section_id=7)
        self.assertEqual([a['id'] for a in found], [1])

        nothing = self.run_search(self.ARTICLES, 'аренда',
                                  sections=['(1, 7)'], section_id=9)
        self.assertEqual(nothing, [])

    def test_perimeter_is_respected(self):
        """Выдача обязана пересекаться с множеством видимых статей."""
        stub = self.STUB.format(rows=', '.join(self.ARTICLES),
                                sections='(NULL::int, NULL::int)')
        sql = wiki_search.build_sql(False).replace('WITH q AS (', stub + 'q AS (', 1)
        cur = self.conn.cursor()
        try:
            cur.execute(sql, {'ids': [2], 'query': 'аренда', 'section': None, 'limit': 10})
            self.assertEqual(cur.fetchall(), [],
                             'статья вне периметра не должна попасть в выдачу')
        finally:
            self.conn.rollback()
            cur.close()

    def test_nothing_found(self):
        self.assertEqual(self.run_search(self.ARTICLES, 'бетономешалка'), [])

    def test_trigram_variant_parses(self):
        """Форма запроса с триграммами обязана быть валидной, даже если
        расширение ещё не установлено — тогда её просто не используют."""
        if not self.has_trigram:
            self.skipTest('pg_trgm ещё не установлен в этой базе')
        found = self.run_search(self.ARTICLES, 'аренда', with_trigram=True)
        self.assertTrue(found)


if __name__ == '__main__':
    unittest.main()
