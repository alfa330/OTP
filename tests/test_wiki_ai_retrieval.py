# -*- coding: utf-8 -*-
"""Ретривер помощника: измерение recall на боевом содержимом вики.

Таблицы wiki_ai_chunks на проде ещё нет, а мерить надо на настоящем тексте —
синтетические статьи ничего не скажут о том, найдёт ли поиск «Забытые Вещи» по
запросу «пассажир забыл телефон». Поэтому куски считаются в Python из боевого
content (соединение read-only), подставляются в запрос CTE-заглушкой, и дальше
исполняется РОВНО тот же текст SQL, что пойдёт в прод.

Заглушка передаётся массивами через unnest, а не VALUES-простынёй: 200 кусков по
550 символов в тексте запроса — это 110 КБ SQL и проблемы с экранированием.

Числа из этого теста — вход для решения по этапу 4 (эмбеддинги). Тест не падает
из-за низкого recall: он его ПЕЧАТАЕТ. Падать он обязан только на поломке
механики — синтаксисе SQL, утечке за периметр, нарушении лимита на статью.
"""

import json
import unittest
from pathlib import Path

from tests import prod_db
from wiki import schema as wiki_schema
from wiki.text import SQL_FOLD_FROM, SQL_FOLD_TO
from wiki.ai.chunker import chunk_article
from wiki.ai.retrieve import _SEARCH_CHUNKS_SQL, search_chunks

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'tests' / 'data' / 'wiki_ai_questions.json'

# Выражение генерируемой колонки собирается ИЗ ТОГО ЖЕ правила свёртки, что и
# схема (wiki/text.py), а не переписывается второй раз: дословная копия уже
# разошлась со схемой, когда к ё→е добавились казахские буквы, и замер стал бы
# ложью. Расхождение всё равно ловит тест ниже — но теперь ловить почти нечего.
_FOLD = "'%s', '%s'" % (SQL_FOLD_FROM, SQL_FOLD_TO)
_TSV = ("setweight(to_tsvector('russian', translate(coalesce(heading_path, ''), %s)), 'B') || "
        "setweight(to_tsvector('russian', translate(coalesce(text, ''), %s)), 'D')"
        % (_FOLD, _FOLD))

# wiki_articles тоже подменяется, хотя строки берутся боевые. Причина —
# развязка со СХЕМОЙ прода: запрос выбирает a.historical (флаг «сведения не
# действуют»), и пока колонка не доехала до боевой базы, тест про РАНЖИРОВАНИЕ
# падал бы с UndefinedColumn — то есть сообщал бы не о том, что проверяет.
# Имя CTE внутри собственного тела не видно, поэтому FROM wiki_articles здесь
# читает настоящую таблицу.
_STUB = """
WITH wiki_ai_chunks AS (
    SELECT id, article_id, chunk_idx, heading_path, text, requires_ack,
           %s AS chunk_tsv
      FROM unnest(%%(c_id)s::bigint[], %%(c_article)s::int[], %%(c_idx)s::int[],
                  %%(c_path)s::text[], %%(c_text)s::text[], %%(c_ack)s::boolean[])
           AS t(id, article_id, chunk_idx, heading_path, text, requires_ack)
),
wiki_articles AS (
    SELECT id, title, slug, false AS historical FROM wiki_articles
),
""" % _TSV


class _StubCursor:
    """Курсор, подставляющий заглушку кусков перед боевым запросом."""

    def __init__(self, cursor, chunk_params):
        self._cursor = cursor
        self._chunk_params = chunk_params
        self.description = None

    def execute(self, sql, params=None):
        if 'wiki_ai_chunks' in sql:
            sql = sql.replace('WITH variants AS (', _STUB + 'variants AS (', 1)
            params = dict(params or {})
            params.update(self._chunk_params)
        self._cursor.execute(sql, params)
        self.description = self._cursor.description

    def fetchall(self):
        return self._cursor.fetchall()


class RetrievalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.conn = prod_db.connection()
        cls.dataset = json.loads(DATA.read_text(encoding='utf-8'))

        with cls.conn.cursor() as cursor:
            cursor.execute("""SELECT id, slug, coalesce(content, ''),
                                     coalesce(content_plain, '')
                                FROM wiki_articles WHERE status = 'published'
                               ORDER BY id""")
            articles = cursor.fetchall()
        prod_db.rollback()

        cls.slug_by_id = {row[0]: row[1] for row in articles}
        cls.all_ids = sorted(cls.slug_by_id)

        ids, art, idx, path, text, ack = [], [], [], [], [], []
        counter = 0
        for article_id, _slug, html, plain in articles:
            for position, chunk in enumerate(chunk_article(html, plain)):
                counter += 1
                ids.append(counter)
                art.append(article_id)
                idx.append(position)
                path.append(chunk['heading_path'])
                text.append(chunk['text'])
                ack.append(bool(chunk['requires_ack']))
        cls.chunk_params = {'c_id': ids, 'c_article': art, 'c_idx': idx,
                            'c_path': path, 'c_text': text, 'c_ack': ack}
        cls.chunk_count = counter

    def search(self, query, *, article_ids=None, limit=8, per_article=3):
        with self.conn.cursor() as raw:
            cursor = _StubCursor(raw, self.chunk_params)
            try:
                return search_chunks(
                    cursor,
                    article_ids=self.all_ids if article_ids is None else article_ids,
                    query=query, limit=limit, per_article=per_article)
            finally:
                prod_db.rollback()

    # ── механика: здесь тест обязан падать ───────────────────────────────────
    def test_stub_matches_schema_expression(self):
        """Заглушка обязана считать tsvector так же, как схема."""
        schema_sql = ' '.join(' '.join(wiki_schema._AI_STATEMENTS).split())
        for source in ('heading_path', 'text'):
            fragment = "translate(coalesce(%s, ''), %s)" % (source, _FOLD)
            self.assertIn(fragment.replace(' ', ''), schema_sql.replace(' ', ''))

    def test_corpus_is_not_empty(self):
        self.assertGreater(self.chunk_count, 100, 'корпус кусков подозрительно мал')

    def test_sql_runs_and_returns_shape(self):
        rows = self.search('минимальный срок аренды')
        self.assertTrue(rows)
        for key in ('chunk_id', 'article_id', 'title', 'slug', 'heading_path',
                    'text', 'requires_ack', 'score', 'strict_hit'):
            self.assertIn(key, rows[0])

    def test_empty_perimeter_returns_nothing(self):
        self.assertEqual([], self.search('аренда', article_ids=[]))

    def test_never_leaves_the_perimeter(self):
        """Главное свойство: ни один кусок вне переданного периметра."""
        allowed = self.all_ids[:3]
        for question in (item['q'] for item in self.dataset['questions']):
            for row in self.search(question, article_ids=allowed, limit=20):
                self.assertIn(row['article_id'], allowed,
                              f'утечка за периметр на запросе «{question}»')

    def test_per_article_cap_is_enforced(self):
        rows = self.search('водитель', limit=50, per_article=2)
        counts = {}
        for row in rows:
            counts[row['article_id']] = counts.get(row['article_id'], 0) + 1
        self.assertTrue(counts)
        self.assertLessEqual(max(counts.values()), 2, counts)

    def test_limit_is_enforced(self):
        self.assertLessEqual(len(self.search('водитель', limit=5)), 5)

    def test_scores_are_sorted_desc(self):
        scores = [row['score'] for row in self.search('аренда автомобиля', limit=20)]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_garbage_query_is_survivable(self):
        """Запрос из служебных символов не должен ломать to_tsquery."""
        for query in ('!!!', '&&&', '   ', ')(', 'a | b', "it's"):
            self.search(query)

    # ── замер: печатает, а не падает ─────────────────────────────────────────
    def test_report_recall(self):
        questions = self.dataset['questions']
        report = {'ru': {'hit1': 0, 'hit6': 0, 'miss': 0, 'total': 0},
                  'kk': {'hit1': 0, 'hit6': 0, 'miss': 0, 'total': 0}}
        misses = []
        for item in questions:
            lang = item.get('lang', 'ru')
            expected = set(item['expect'])
            rows = self.search(item['q'], limit=6, per_article=3)
            slugs = [row['slug'] for row in rows]
            bucket = report[lang]
            bucket['total'] += 1
            if slugs and slugs[0] in expected:
                bucket['hit1'] += 1
            if expected & set(slugs):
                bucket['hit6'] += 1
            else:
                bucket['miss'] += 1
                misses.append((lang, item['q'], sorted(expected), slugs[:3]))

        lines = [f'\nЗАМЕР РЕТРИВЕРА (кусков в индексе: {self.chunk_count})']
        for lang in ('ru', 'kk'):
            data = report[lang]
            if not data['total']:
                continue
            lines.append(
                f"  {lang}: вопросов {data['total']}, "
                f"первым результатом {data['hit1']}/{data['total']}, "
                f"в топ-6 {data['hit6']}/{data['total']}, "
                f"не найдено вовсе {data['miss']}")
        if misses:
            lines.append('  промахи:')
            for lang, question, expected, got in misses:
                lines.append(f'    [{lang}] «{question}»')
                lines.append(f'          ждали {expected}, получили {got}')
        print('\n'.join(lines))

        # Падаем только если сломана механика: по-русски не найдено НИЧЕГО.
        self.assertGreater(report['ru']['hit6'], 0,
                           'лексический поиск не нашёл ни одного русского вопроса — '
                           'это поломка, а не низкий recall')


if __name__ == '__main__':
    unittest.main()
