"""Период раздела «ИИ-оценка» (задача #389): «Обзор», «Сводка», «База разборов».

Период — один на раздел (date_from/date_to отбора), а у каждой вкладки своя
мерка дня: у «Сводки» — день разговора (тот же, что у списков), у «Базы
разборов» — день, когда сделан разбор, у блока RAG в «Обзоре» — день запуска
поиска. Все три — по Алматы: подпись даты на экране и фильтр обязаны считать
сутки одинаково, иначе разбор, сделанный в полночь, был бы «вчера» в карточке
и «сегодня» в фильтре.
"""
import ast
import unittest
from datetime import date
from unittest import mock

import psycopg2

from call_qa import api
from tests import source_cache

D1 = date(2026, 11, 4)
D2 = date(2026, 11, 10)
RULE_DAY = "(c.created_at AT TIME ZONE 'Asia/Almaty')::date"


def _catalog_queries(**kwargs):
    conn = mock.MagicMock()
    cur = conn.cursor.return_value
    cur.fetchall.return_value = []
    cur.fetchone.side_effect = [(0,), (0, 0, 0, None, 'model')]
    with mock.patch.object(api.config, 'connect_ro', return_value=conn), \
         mock.patch.object(api, '_scoped_qa_family', return_value=[83]):
        result = api.adjudications_list(department='op', allowed_direction_ids=None, **kwargs)
    queries = [(str(c.args[0]), tuple(c.args[1]) if len(c.args) > 1 else ())
               for c in cur.execute.call_args_list]
    return result, queries


class CatalogPeriodTests(unittest.TestCase):
    def test_period_reaches_cards_and_status_counters_but_not_index_state(self):
        result, queries = _catalog_queries(date_from=D1, date_to=D2)
        self.assertEqual(result['health']['status'], 'healthy')
        catalog = [(sql, params) for sql, params in queries if 'FROM qa_policy_rule_catalog c' in sql]
        # Карточки, «COUNT(*) когда страница пуста», два фасета и статусы индекса.
        self.assertGreaterEqual(len(catalog), 4)
        for sql, params in catalog:
            with self.subTest(sql=sql[:70]):
                self.assertIn(f"{RULE_DAY} >= %s", sql)
                self.assertIn(f"{RULE_DAY} <= %s", sql)
                self.assertIn(D1, params)
                self.assertIn(D2, params)
        knowledge = [sql for sql, _ in queries if 'WITH scoped_rules' in sql]
        self.assertEqual(len(knowledge), 1)
        self.assertNotIn('AT TIME ZONE', knowledge[0],
                         'готовность индекса — по всей базе на сегодня, а не за период')

    def test_without_period_catalog_is_not_cut_by_day(self):
        _, queries = _catalog_queries()
        for sql, _ in queries:
            if 'FROM qa_policy_rule_catalog c' in sql and 'WHERE' in sql:
                where = sql.split('WHERE', 1)[1]
                self.assertNotIn(f"{RULE_DAY} >=", where)

    def test_one_sided_period_works(self):
        _, queries = _catalog_queries(date_from=D1)
        cards = next(sql for sql, _ in queries if 'COUNT(*) OVER()' in sql)
        self.assertIn(f"{RULE_DAY} >= %s", cards)
        self.assertNotIn(f"{RULE_DAY} <= %s", cards)

    def test_card_date_is_almaty_day_like_the_filter(self):
        _, queries = _catalog_queries()
        cards = next(sql for sql, _ in queries if 'COUNT(*) OVER()' in sql)
        self.assertIn(f"TO_CHAR({RULE_DAY}, 'DD.MM.YYYY')", cards)

    def test_card_date_is_taken_from_the_local_day_column(self):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value
        row = ['rule-1', 72, 'Основа', 'Приветствие', 'Incorrect', 'Correct', 'цитата', 'причина', None,
               'ситуация', 'active', 'ready', 'm', 'p', 768, 1, 'h', 0, 'Автор',
               mock.sentinel.created, mock.sentinel.updated, None, True, 'ok', 'v1', 'cfg', 1, 0,
               '10.11.2026']
        cur.fetchall.side_effect = [[tuple(row)], [], [], [], []]
        cur.fetchone.side_effect = [(0, 0, 0, None, 'model')]
        with mock.patch.object(api.config, 'connect_ro', return_value=conn), \
             mock.patch.object(api, '_scoped_qa_family', return_value=[72]):
            result = api.adjudications_list(department='op')
        self.assertEqual(result['items'][0]['date'], '10.11.2026')

    def test_legacy_catalog_never_ignores_a_period(self):
        conn = mock.MagicMock()
        conn.cursor.return_value.execute.side_effect = psycopg2.errors.UndefinedTable('migration pending')
        with mock.patch.object(api.config, 'connect_ro', return_value=conn), \
             mock.patch.object(api.runtime_store, 'is_schema_compat_error', return_value=True), \
             mock.patch.object(api, '_legacy_adjudications_page') as legacy:
            result = api.adjudications_list(department='op', date_from=D1, date_to=D2)
        legacy.assert_not_called()
        self.assertEqual(result['health']['status'], 'error')


class RagObservabilityPeriodTests(unittest.TestCase):
    def _runs_query(self, *args):
        cur = mock.MagicMock()
        cur.fetchone.side_effect = [(0, 0, 0, 0, 0, None, None), (0,)]
        cur.fetchall.return_value = []
        api._rag_observability(cur, *args)
        return cur.execute.call_args_list[0]

    def test_period_counts_runs_of_the_chosen_days(self):
        call = self._runs_query(D1, D2)
        sql, params = str(call.args[0]), tuple(call.args[1])
        self.assertIn("(created_at AT TIME ZONE 'Asia/Almaty')::date >= %s", sql)
        self.assertIn("(created_at AT TIME ZONE 'Asia/Almaty')::date <= %s", sql)
        self.assertNotIn("30 days", sql)
        self.assertEqual(params, (D1, D2))

    def test_without_period_last_30_days_as_before(self):
        call = self._runs_query()
        self.assertIn("created_at>now()-interval '30 days'", str(call.args[0]))
        self.assertEqual(tuple(call.args[1]), ())

    def test_fill_rag_stats_passes_the_period(self):
        with mock.patch.object(api, '_rag_observability', return_value={'status': 'ready'}) as rag:
            out = {}
            api._fill_rag_stats(mock.sentinel.cur, out, {'date_from': D1, 'date_to': D2, 'q': 'x'})
        rag.assert_called_once_with(mock.sentinel.cur, D1, D2)
        with mock.patch.object(api, '_rag_observability', return_value={'status': 'ready'}) as rag:
            api._fill_rag_stats(mock.sentinel.cur, {})
        rag.assert_called_once_with(mock.sentinel.cur, None, None)

    def test_filtered_stats_hand_the_filters_to_rag(self):
        source = ast.get_source_segment(
            api_source := open(api.__file__, encoding='utf-8').read(),
            next(node for node in ast.parse(api_source).body
                 if isinstance(node, ast.FunctionDef) and node.name == '_filtered_stats'))
        self.assertEqual(source.count('_fill_rag_stats(cur, out, filters)'), 2)
        self.assertNotIn('_fill_rag_stats(cur, out)\n', source)


class RouteWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.routes = source_cache.monolith_flat_path().read_text(encoding='utf-8-sig')
        cls.tree = source_cache.parse(cls.routes)

    def _function(self, name):
        node = next(item for item in self.tree.body
                    if isinstance(item, ast.FunctionDef) and item.name == name)
        return ast.get_source_segment(self.routes, node)

    def test_period_helper_reads_only_the_dates(self):
        body = self._function('_ai_qa_period_filters')
        self.assertIn("for key in ('date_from', 'date_to')", body)
        self.assertIn('normalise_list_filters(', body)
        self.assertIn('400', body, 'неверная дата — отказ, а не молча снятый фильтр')

    def test_digests_list_only_days_of_the_period(self):
        body = self._function('api_ai_qa_digests')
        self.assertIn('period, period_err = _ai_qa_period_filters()', body)
        self.assertIn('filters=period or None', body)

    def test_catalog_gets_the_period(self):
        body = self._function('api_ai_qa_adjudications')
        self.assertIn('period, period_err = _ai_qa_period_filters()', body)
        self.assertIn("date_from=period.get('date_from'), date_to=period.get('date_to')", body)

    def test_period_filter_rejects_reversed_or_broken_dates(self):
        with self.assertRaises(ValueError):
            api.normalise_list_filters({'date_from': '2026-11-10', 'date_to': '2026-11-04'})
        with self.assertRaises(ValueError):
            api.normalise_list_filters({'date_from': '10.11.2026'})
        self.assertEqual(api.normalise_list_filters({'date_from': '2026-11-04', 'date_to': '2026-11-10'}),
                         {'date_from': D1, 'date_to': D2})


if __name__ == '__main__':
    unittest.main()
