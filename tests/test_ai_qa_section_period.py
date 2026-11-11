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


def _bind(sql, params):
    """Подставить параметры по порядку %s — как это сделает драйвер (разбор ревьюера)."""
    parts = str(sql).split('%s')
    if len(parts) - 1 != len(params):
        raise AssertionError(f'{len(parts) - 1} %s, а параметров {len(params)}: {sql[:80]}')
    out = parts[0]
    for value, tail in zip(params, parts[1:]):
        out += repr(value) + tail
    return ' '.join(out.split())


class CatalogPeriodParamsTests(unittest.TestCase):
    """Параметры каталога стоят на своих местах: перепутанный порядок в фасетах
    («скоуп» и «период» местами) на живой базе — ошибка, а проверка «D1 in params»
    этого не видела."""

    def test_every_catalog_query_binds_scope_and_period_in_place(self):
        _, queries = _catalog_queries(date_from=D1, date_to=D2)
        for sql, params in queries:
            if 'FROM qa_policy_rule_catalog c' not in sql:
                continue
            with self.subTest(sql=sql[:60]):
                bound = _bind(sql, params)
                self.assertIn('c.direction_id = ANY([83])', bound)
                self.assertIn(f'{RULE_DAY} >= {D1!r}', bound)
                self.assertIn(f'{RULE_DAY} <= {D2!r}', bound)

    def test_old_schema_never_serves_a_one_sided_period(self):
        for kwargs in ({'date_from': D1}, {'date_to': D2}):
            with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
                conn = mock.MagicMock()
                conn.cursor.return_value.execute.side_effect = psycopg2.errors.UndefinedTable('pending')
                with mock.patch.object(api.config, 'connect_ro', return_value=conn), \
                     mock.patch.object(api.runtime_store, 'is_schema_compat_error', return_value=True), \
                     mock.patch.object(api, '_legacy_adjudications_page') as legacy:
                    result = api.adjudications_list(department='op', **kwargs)
                legacy.assert_not_called()
                self.assertEqual(result['health']['status'], 'error')


def _period_filters_with(args):
    """_ai_qa_period_filters из склейки монолита — с подменённым request."""
    import copy
    import types
    path = source_cache.monolith_flat_path()
    source = path.read_text(encoding='utf-8-sig')
    node = copy.deepcopy(next(n for n in source_cache.parse(source).body
                              if isinstance(n, ast.FunctionDef) and n.name == '_ai_qa_period_filters'))
    node.decorator_list = []
    namespace = {'request': types.SimpleNamespace(args=args), 'jsonify': lambda payload: payload}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['_ai_qa_period_filters']()


class PeriodFiltersTests(unittest.TestCase):
    def test_dates_only_and_bad_date_is_400(self):
        period, error = _period_filters_with({'date_from': '2026-11-04', 'date_to': '2026-11-10', 'q': 'x'})
        self.assertIsNone(error)
        self.assertEqual(period, {'date_from': D1, 'date_to': D2}, 'из отбора — только период')
        period, error = _period_filters_with({'date_from': '10.11.2026'})
        self.assertIsNone(period)
        self.assertEqual(error[1], 400)
        period, error = _period_filters_with({'date_from': '2026-11-10', 'date_to': '2026-11-04'})
        self.assertEqual(error[1], 400, 'перевёрнутый период — отказ, а не пустой список')
        self.assertEqual(_period_filters_with({}), ({}, None))


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
        """Оба пути «Обзора» по отбору — пустой отбор и обычный — отдают период блоку RAG."""
        filters = {'date_from': D1, 'date_to': D2}
        for scope, label in (((None, ()), 'отбор заведомо пуст'), (('', ()), 'обычный отбор')):
            with self.subTest(label), \
                 mock.patch.object(api, '_direction_predicate', return_value=scope), \
                 mock.patch.object(api, '_list_filters_predicate', return_value=('', ())), \
                 mock.patch.object(api, '_marketing_join', return_value=''), \
                 mock.patch.object(api, '_reviewed_metrics', return_value={}), \
                 mock.patch.object(api, '_verdict_metrics', return_value={}), \
                 mock.patch.object(api, '_fill_verdict_stats'), \
                 mock.patch.object(api, '_fill_rag_stats') as rag:
                cur = mock.MagicMock()
                cur.fetchall.return_value = []
                cur.fetchone.return_value = (0,)
                out = {}
                api._filtered_stats(cur, out, None, 'op', filters)
                rag.assert_called_once_with(cur, out, filters)


def _route_with(names, args, **stubs):
    """Ручки монолита из склейки — вместе с настоящим _ai_qa_period_filters, с
    подменённым request и заглушками всего, что ходит в базу и права."""
    import copy
    import logging
    import types
    path = source_cache.monolith_flat_path()
    source = path.read_text(encoding='utf-8-sig')
    wanted = set(names) | {'_ai_qa_period_filters'}
    nodes = []
    for node in source_cache.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            node = copy.deepcopy(node)
            node.decorator_list = []
            nodes.append(node)
    namespace = {
        'request': types.SimpleNamespace(method='GET', args=args),
        'jsonify': lambda payload: payload,
        'logging': logging,
        '_ai_qa_guard': lambda: (7, None),
        '_ai_qa_requested_department': lambda requester_id: ('op', None),
        '_ai_qa_direction_scope': lambda requester_id: [83],
        '_ai_qa_list_filters': lambda: ({}, None),
        '_ai_qa_digest_queued': lambda department, day: False,
        '_ai_qa_month_queued': lambda department, month: False,
        '_ai_qa_iso': lambda value: value,
        **stubs,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


class RouteWiringTests(unittest.TestCase):
    ARGS = {'date_from': '2026-11-04', 'date_to': '2026-11-10', 'q': 'Иванов', 'operator_id': '42'}

    def test_digests_list_only_days_of_the_period(self):
        namespace = _route_with(['api_ai_qa_digests'], self.ARGS)
        with mock.patch('call_qa.api.evaluations_days', return_value={'days': []}) as days, \
             mock.patch('call_qa.digest.service.headlines', return_value={}), \
             mock.patch('call_qa.digest.monthly.known', return_value={}):
            payload, status = namespace['api_ai_qa_digests']()
        self.assertEqual(status, 200, payload)
        self.assertEqual(days.call_args.kwargs['filters'], {'date_from': D1, 'date_to': D2},
                         'из отбора — только период: сотрудник и поиск «Звонков» к «Сводке» не относятся')
        with mock.patch('call_qa.api.evaluations_days', return_value={'days': []}) as days, \
             mock.patch('call_qa.digest.service.headlines', return_value={}), \
             mock.patch('call_qa.digest.monthly.known', return_value={}):
            _route_with(['api_ai_qa_digests'], {})['api_ai_qa_digests']()
        self.assertIsNone(days.call_args.kwargs['filters'], 'без периода — все дни, как было')

    def test_digests_refuse_a_reversed_period(self):
        namespace = _route_with(['api_ai_qa_digests'], {'date_from': '2026-11-10', 'date_to': '2026-11-04'})
        with mock.patch('call_qa.api.evaluations_days') as days:
            _, status = namespace['api_ai_qa_digests']()
        self.assertEqual(status, 400)
        days.assert_not_called()

    def test_catalog_gets_the_period(self):
        namespace = _route_with(['api_ai_qa_adjudications'], self.ARGS)
        with mock.patch('call_qa.api.adjudications_list', return_value={'items': []}) as catalog:
            payload, status = namespace['api_ai_qa_adjudications']()
        self.assertEqual(status, 200, payload)
        self.assertEqual((catalog.call_args.kwargs['date_from'], catalog.call_args.kwargs['date_to']), (D1, D2))
        self.assertIsNone(catalog.call_args.kwargs['filters'], 'немаркетинговый отбор в каталог не уходит')
        with mock.patch('call_qa.api.adjudications_list', return_value={'items': []}) as catalog:
            _route_with(['api_ai_qa_adjudications'], {})['api_ai_qa_adjudications']()
        self.assertEqual((catalog.call_args.kwargs['date_from'], catalog.call_args.kwargs['date_to']), (None, None))

    def test_period_filter_rejects_reversed_or_broken_dates(self):
        with self.assertRaises(ValueError):
            api.normalise_list_filters({'date_from': '2026-11-10', 'date_to': '2026-11-04'})
        with self.assertRaises(ValueError):
            api.normalise_list_filters({'date_from': '10.11.2026'})
        self.assertEqual(api.normalise_list_filters({'date_from': '2026-11-04', 'date_to': '2026-11-10'}),
                         {'date_from': D1, 'date_to': D2})


if __name__ == '__main__':
    unittest.main()
