# -*- coding: utf-8 -*-
"""Триграммная ветка ретривера: имя собственное, названное с ошибкой в букву.

Зачем ветка вообще. Голосовой наставник получает вопрос из распознавания речи,
а оно путает гласные. Замер на проде 23.08.2026, акция записана как
«Лимонопад»: «расскажи про акцию Лимонопад» находит нужный кусок ПЕРВЫМ
(счёт 5,7), «расскажи про акцию Лимонапад» не находит его вовсе — лексема
другая, а вектор не дотягивает до порога, потому что имя занимает одну строку
в табличном куске на 1430 знаков. Наставник отвечал «в доступных мне статьях
нет информации об акции Лимонапад», хотя обычный поиск вики отдаёт её сразу.

Здесь закрепляются оба предиката отбора — без них ветка либо не работает, либо
насыпает мусора, и оба провала уже случались на живых данных:
  * добираем ТОЛЬКО слова, которых вика не знает лексикой;
  * вся выдача ветки обязана лежать в ОДНОЙ статье.

SQL проверяется БОЕВОЙ, на синтетических кусках: CTE перекрывает одноимённые
таблицы, как в SearchSqlTest (tests/test_wiki_search.py). Иначе тест проверял
бы свою копию запроса, а не то, что уходит в базу.
"""

import unittest

from tests import prod_db
from wiki.ai import answer as ai_answer
from wiki.ai.retrieve import (
    FUZZY_MIN_WORD,
    FUZZY_THRESHOLD,
    fuse,
    search_fuzzy,
)
from wiki.text import SQL_FOLD_FROM, SQL_FOLD_TO


def row(chunk_id, article_id, **extra):
    base = {'chunk_id': chunk_id, 'article_id': article_id, 'chunk_idx': 0,
            'slug': f'a{article_id}', 'title': f'Статья {article_id}',
            'heading_path': '', 'text': f'текст {chunk_id}',
            'requires_ack': False}
    base.update(extra)
    return base


class FuzzySqlTest(unittest.TestCase):
    """Боевой запрос ветки на синтетических кусках."""

    # Выражение chunk_tsv обязано совпадать с генерируемой колонкой из
    # wiki/schema.py, а свёртка берётся из wiki/text.py: разойдись они — тест
    # проверял бы не тот индекс, что на проде.
    STUB = """
WITH wiki_ai_chunks AS (
    SELECT id::int, article_id::int, chunk_idx::int, heading_path::text,
           text::text, requires_ack::bool,
           setweight(to_tsvector('russian', translate(coalesce(heading_path, ''), '{fold_from}', '{fold_to}')), 'B') ||
           setweight(to_tsvector('russian', translate(coalesce(text, ''),         '{fold_from}', '{fold_to}')), 'D') AS chunk_tsv
      FROM (VALUES {chunks}) AS t(id, article_id, chunk_idx, heading_path,
                                  text, requires_ack)
),
wiki_articles AS (
    SELECT id::int, title::text, slug::text
      FROM (VALUES {articles}) AS t(id, title, slug)
),
words AS ("""

    CHUNKS = [
        # Табличная строка со списком акций — ровно та форма, на которой ветка
        # и понадобилась: название лежит внутри строки, слова «акция» в ней нет.
        "(1, 33, 3, 'Розыгрыши', 'Парк: все; Даты: 03.08.2026 - 31.08.2026; "
        "Название: Лимонопад; Механика: 50 заказов = 1 купон', false)",
        "(2, 33, 9, 'Акции СМЗ', 'Название акции: 500п-20к для новых водителей "
        "СМЗ из других парков', false)",
        "(3, 14, 0, 'Грузовая', 'Грузовой тариф: расскажите водителю про "
        "габариты и вес груза', false)",
        "(4, 18, 2, 'Страхование', 'Страхование поездок покрывает ущерб "
        "пассажиру и водителю', false)",
    ]
    ARTICLES = ["(33, 'Все акции', 'vse-akcii')",
                "(14, 'Грузовая', 'gruzovaya')",
                "(18, 'Страхование поездок', 'strahovanie')"]

    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.conn = prod_db.connection()
        cursor = cls.conn.cursor()
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
        has_trigram = cursor.fetchone() is not None
        cursor.close()
        prod_db.rollback()
        if not has_trigram:
            raise unittest.SkipTest('pg_trgm недоступен — ветка молчит по проекту')

    def run_fuzzy(self, query, *, chunks=None, articles=None, ids=(33, 14, 18)):
        """search_fuzzy целиком, только таблицы подменены синтетикой."""
        from wiki.ai import retrieve as ai_retrieve

        stub = self.STUB.format(
            fold_from=SQL_FOLD_FROM, fold_to=SQL_FOLD_TO,
            chunks=', '.join(chunks if chunks is not None else self.CHUNKS),
            articles=', '.join(articles if articles is not None else self.ARTICLES),
        )
        sql = ai_retrieve._FUZZY_CHUNKS_SQL.replace('WITH words AS (', stub, 1)
        cursor = self.conn.cursor()
        try:
            patched = _CursorWithSql(cursor, sql)
            return search_fuzzy(patched, article_ids=list(ids), query=query)
        finally:
            prod_db.rollback()
            cursor.close()

    def test_swapped_letter_finds_the_name(self):
        """Одна перепутанная гласная — и кусок всё равно находится."""
        rows = self.run_fuzzy('Расскажи мне про акцию «Лимонапад».')
        self.assertEqual([1], [item['chunk_id'] for item in rows])
        self.assertGreaterEqual(rows[0]['fuzzy'], FUZZY_THRESHOLD)
        self.assertTrue(rows[0]['fuzzy_hit'])

    def test_exact_name_is_left_to_the_lexical_branch(self):
        """Слово, которое вика знает, ветка не трогает: добор бы дал соседей."""
        self.assertEqual([], self.run_fuzzy('Что за акция Лимонопад?'))

    def test_known_word_is_never_expanded(self):
        """«Страхование» есть в вике лексикой — похожих кусков не добираем."""
        self.assertEqual([], self.run_fuzzy('Интересует страхование поездок.'))

    def test_matches_across_articles_are_dropped(self):
        """Похоже в двух статьях — это не имя, а общее слово.

        Провал, поймавший это правило: казахский вопрос «жолаушы затын салонда
        қалдырды» (лексика не находит ничего, находит вектор) собрал по одному
        постороннему куску на слово из двух разных статей.
        """
        chunks = self.CHUNKS + [
            "(5, 14, 7, 'Грузовая', 'Лимонопад для грузовых тарифов', false)",
        ]
        self.assertEqual([], self.run_fuzzy('Расскажи про акцию Лимонапад.',
                                            chunks=chunks))

    def test_same_article_twice_is_kept(self):
        """А два куска ОДНОЙ статьи — находка, а не мусор."""
        chunks = self.CHUNKS + [
            "(6, 33, 4, 'Розыгрыши', 'Лимонопад: призы вручаются по средам', false)",
        ]
        rows = self.run_fuzzy('Расскажи про акцию Лимонапад.', chunks=chunks)
        self.assertEqual({1, 6}, {item['chunk_id'] for item in rows})

    def test_short_word_is_not_a_name(self):
        """У слова из пяти букв триграмм мало: близость 0,45 набирает полкорпуса."""
        self.assertEqual([], self.run_fuzzy('Что такое байга?'))


class _CursorWithSql:
    """Курсор, подменяющий боевой запрос его версией с синтетикой.

    Проверку pg_trgm пропускаем к настоящему курсору: она часть ветки.
    """

    def __init__(self, cursor, sql):
        self._cursor = cursor
        self._sql = sql

    def execute(self, sql, params=None):
        if 'word_similarity' in sql:
            sql = self._sql
        return self._cursor.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _NoTrigramCursor:
    """pg_trgm не установлен: ветка обязана молчать, а не падать."""

    def __init__(self):
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append(sql)

    def fetchone(self):
        return None

    def fetchall(self):
        raise AssertionError('до основного запроса дело доходить не должно')


class _ExplodingCursor:
    def execute(self, *args, **kwargs):
        raise AssertionError('лишний запрос к базе')


class FuzzyGuardsTest(unittest.TestCase):
    """Когда ветка не должна дойти до базы вовсе."""

    def test_no_long_words_no_query(self):
        rows = search_fuzzy(_ExplodingCursor(), article_ids=[1],
                            query='а что там с ним')
        self.assertEqual([], rows)

    def test_empty_perimeter_no_query(self):
        rows = search_fuzzy(_ExplodingCursor(), article_ids=[],
                            query='Расскажи про акцию Лимонапад')
        self.assertEqual([], rows)

    def test_without_trigram_extension_branch_is_silent(self):
        cursor = _NoTrigramCursor()
        rows = search_fuzzy(cursor, article_ids=[1],
                           query='Расскажи про акцию Лимонапад')
        self.assertEqual([], rows)
        self.assertEqual(1, len(cursor.queries))

    def test_min_word_is_documented_value(self):
        self.assertEqual(6, FUZZY_MIN_WORD)


class FuzzyInFusionTest(unittest.TestCase):
    """Место триграммной находки в слиянии — впереди вектора."""

    def test_fuzzy_goes_first(self):
        out = fuse([row(9, 13)], [row(2, 11), row(3, 12)], [row(1, 33)],
                   limit=10, per_article=3)
        self.assertEqual(1, out[0]['chunk_id'])

    def test_fuzzy_survives_a_full_dense_page(self):
        """Главное свойство: место находке гарантировано.

        На проде 23.08.2026 второй вопрос про ту же акцию пришёл при полной
        плотной выдаче (21 кусок). Без гарантированного места добор вытеснялся
        бы ею целиком, и правка была бы половинчатой.
        """
        dense = [row(i, 10 + i) for i in range(1, 9)]
        out = fuse([], dense, [row(99, 33)], limit=8, per_article=3)
        self.assertEqual(99, out[0]['chunk_id'])
        self.assertEqual(8, len(out))

    def test_fuzzy_branch_is_marked(self):
        out = fuse([], [], [row(1, 33)], limit=8)
        self.assertEqual([2], out[0]['found_by'])

    def test_chunk_found_by_all_branches(self):
        out = fuse([row(1, 33)], [row(1, 33)], [row(1, 33)], limit=8)
        self.assertEqual([0, 1, 2], out[0]['found_by'])

    def test_dense_row_is_not_duplicated_by_fuzzy(self):
        out = fuse([], [row(1, 33), row(2, 34)], [row(1, 33)], limit=8)
        self.assertEqual([1, 2], [item['chunk_id'] for item in out])


class FuzzyAnswerGateTest(unittest.TestCase):
    """Слой ответа обязан признавать находку ветки годной."""

    def test_fuzzy_chunk_is_usable_without_similarity(self):
        """Близости у куска нет по построению — порог его отбрасывать не должен."""
        chunk = row(1, 33, found_by=[2], fuzzy=0.54, fuzzy_hit=True)
        self.assertEqual([chunk], ai_answer.usable_chunks([chunk]))

    def test_fuzzy_hit_stops_the_clarifying_question(self):
        """Человек назвал вещь по имени — переспрашивать нечего.

        Без этого гейт переспрашивал бы ровно на том вопросе, который ветка
        только что и вытащила: он короткий, близости нет, статей несколько.
        """
        chunks = [row(1, 33, found_by=[2], fuzzy_hit=True),
                  row(2, 14, found_by=[0])]
        clarify, _why = ai_answer.should_clarify('акция Лимонапад', chunks)
        self.assertFalse(clarify)

    def test_without_fuzzy_hit_the_gate_still_works(self):
        chunks = [row(1, 33, found_by=[1], similarity=0.70),
                  row(2, 14, found_by=[1], similarity=0.69)]
        clarify, why = ai_answer.should_clarify('что с машиной', chunks)
        self.assertTrue(clarify)
        self.assertIn('близость', why)


if __name__ == '__main__':
    unittest.main()
