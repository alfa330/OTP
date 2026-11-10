# -*- coding: utf-8 -*-
"""«Аналитика» журнала оценок за ПЕРИОД (задача #389).

Раньше «Аналитика» жила по месяцам. Теперь период любой — день, неделя,
диапазон, месяц, год, «Весь период», — но правило подсчёта не меняется:

* один целый месяц — прежний месячный путь (те же функции, тот же план), чтобы
  цифры за октябрь не сдвинулись ни на одну оценку;
* несколько целых месяцев — по месяцу журнала (calls.month), как месяц;
* день, неделя, диапазон — по дню обращения (calls.appeal_date). Дата самой
  оценки не годится: на проде у 418 из 1278 оценок октября она в ноябре;
* плана у периода не из одного месяца нет — он месячный (часы месяца → норма).

Боевые модули не импортируются (на импорте поднимается пул к БД) — функции
достаются через ast, как в соседних наборах.
"""
import ast
import calendar
import textwrap
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from tests import source_cache

DATABASE_SOURCE = source_cache.database_flat_path().read_text(encoding='utf-8-sig')
DATABASE_CLASS = next(node for node in source_cache.parse(DATABASE_SOURCE).body
                      if isinstance(node, ast.ClassDef) and node.name == 'Database')
ROUTES_PATH = Path(__file__).resolve().parents[1] / 'supervisors' / 'routes.py'


def _routes_source():
    return ROUTES_PATH.read_text(encoding='utf-8-sig')


def _method(name):
    node = next(n for n in DATABASE_CLASS.body if isinstance(n, ast.FunctionDef) and n.name == name)
    source = textwrap.dedent(ast.get_source_segment(DATABASE_SOURCE, node))
    namespace = {'Optional': Optional, 'List': List, 'Dict': Dict, 'Any': Any,
                 'date': date, 'datetime': datetime, 'timedelta': timedelta}
    exec(source, namespace)
    function = namespace[name]
    is_static = any(isinstance(d, ast.Name) and d.id == 'staticmethod' for d in node.decorator_list)
    return staticmethod(function) if is_static else function


def _route_function(name):
    source = _routes_source()
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = {'dt_date': date, 'datetime': datetime, 'calendar': calendar}
    exec(textwrap.dedent(ast.get_source_segment(source, node)), namespace)
    return namespace


class _Cursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.executions = []

    def execute(self, sql, params=None):
        self.executions.append((sql, params))

    def fetchall(self):
        return self.rows


class _Db:
    _journal_period_sql = _method('_journal_period_sql')
    get_operator_score_aggregates_for_period = _method('get_operator_score_aggregates_for_period')
    get_feedback_sla_rows_for_period = _method('get_feedback_sla_rows_for_period')

    def __init__(self, rows=()):
        self.cursor = _Cursor(rows)

    @contextmanager
    def _get_cursor(self):
        yield self.cursor

    def _feedback_sla_row_dict(self, row, reference_date=None):
        return {'operator_id': row[1]}


def _flat(sql):
    return ' '.join(str(sql).split())


class PeriodArgsTests(unittest.TestCase):
    """Разбор периода ручкой /api/sv/data."""

    @classmethod
    def setUpClass(cls):
        namespace = _route_function('_journal_whole_months')
        namespace.update(_route_function('_journal_period_args'))
        namespace['_journal_period_args'].__globals__['_journal_whole_months'] = namespace['_journal_whole_months']
        cls.parse = staticmethod(namespace['_journal_period_args'])

    def test_one_whole_month_goes_the_old_month_way(self):
        period, error = self.parse({'date_from': '2026-10-01', 'date_to': '2026-10-31'})
        self.assertIsNone(error)
        self.assertEqual(period, {'kind': 'month', 'month': '2026-10'})
        self.assertEqual(self.parse({'date_from': '2024-02-01', 'date_to': '2024-02-29'})[0],
                         {'kind': 'month', 'month': '2024-02'})

    def test_several_whole_months_and_a_year_count_by_journal_month(self):
        period, _ = self.parse({'date_from': '2026-09-01', 'date_to': '2026-10-31'})
        self.assertEqual(period['kind'], 'months')
        self.assertEqual(period['months'], ['2026-09', '2026-10'])
        year, _ = self.parse({'date_from': '2025-01-01', 'date_to': '2025-12-31'})
        self.assertEqual(year['kind'], 'months')
        self.assertEqual(len(year['months']), 12)
        across, _ = self.parse({'date_from': '2025-12-01', 'date_to': '2026-01-31'})
        self.assertEqual(across['months'], ['2025-12', '2026-01'])

    def test_days_week_and_partial_ranges_count_by_appeal_day(self):
        for raw in ({'date_from': '2026-11-10', 'date_to': '2026-11-10'},
                    {'date_from': '2026-11-04', 'date_to': '2026-11-10'},
                    {'date_from': '2026-09-01', 'date_to': '2026-10-15'},
                    {'date_from': '2026-10-01', 'date_to': '2026-10-30'}):
            with self.subTest(raw=raw):
                period, error = self.parse(raw)
                self.assertIsNone(error)
                self.assertEqual(period['kind'], 'range')
                self.assertEqual(period['start'], date.fromisoformat(raw['date_from']))
                self.assertEqual(period['end'], date.fromisoformat(raw['date_to']))

    def test_all_time_and_old_month_parameter(self):
        self.assertEqual(self.parse({'period': 'all'})[0], {'kind': 'all'})
        self.assertEqual(self.parse({'month': '2026-08'})[0], {'kind': 'month', 'month': '2026-08'})
        current = self.parse({})[0]
        self.assertEqual(current['kind'], 'month')
        self.assertEqual(current['month'], datetime.now().strftime('%Y-%m'))
        # Даты важнее period=all: явный период не теряется.
        self.assertEqual(self.parse({'period': 'all', 'date_from': '2026-11-10', 'date_to': '2026-11-10'})[0]['kind'],
                         'range')

    def test_broken_or_reversed_period_is_an_error_not_a_silent_month(self):
        for raw in ({'date_from': '2026-11-10'}, {'date_from': '10.11.2026', 'date_to': '2026-11-10'},
                    {'date_from': '2026-11-10', 'date_to': '2026-11-04'}, {'month': '2026-13'}):
            with self.subTest(raw=raw):
                period, error = self.parse(raw)
                self.assertIsNone(period)
                self.assertTrue(error)


class JournalPeriodSqlTests(unittest.TestCase):
    def test_whole_months_by_journal_month(self):
        sql, params = _Db._journal_period_sql(months=['2026-09', '2026-10'])
        self.assertEqual(sql, 'month = ANY(%s)')
        self.assertEqual(params, [['2026-09', '2026-10']])
        self.assertEqual(_Db._journal_period_sql(months=['2026-09'], alias='c')[0], 'c.month = ANY(%s)')

    def test_range_by_appeal_day_end_inclusive(self):
        sql, params = _Db._journal_period_sql(start=date(2026, 11, 4), end=date(2026, 11, 10))
        self.assertEqual(sql, 'appeal_date >= %s AND appeal_date < %s')
        self.assertEqual(params, [date(2026, 11, 4), date(2026, 11, 11)])
        self.assertEqual(_Db._journal_period_sql(start=date(2026, 12, 31), end=date(2026, 12, 31))[1],
                         [date(2026, 12, 31), date(2027, 1, 1)])

    def test_all_time_has_no_date_condition(self):
        self.assertEqual(_Db._journal_period_sql(), ('TRUE', []))


class PeriodAggregatesTests(unittest.TestCase):
    def test_latest_version_per_call_in_the_period(self):
        db = _Db(rows=[(17, 3, 91.234, 4)])
        result = db.get_operator_score_aggregates_for_period(
            ['17', 17, 'x'], start=date(2026, 11, 4), end=date(2026, 11, 10))
        [(sql, params)] = db.cursor.executions
        flat = _flat(sql)
        self.assertIn('SELECT DISTINCT ON (operator_id, phone_number, month, appeal_date)', flat)
        self.assertIn('ORDER BY operator_id, phone_number, month, appeal_date, created_at DESC, id DESC', flat)
        self.assertIn('appeal_date >= %s AND appeal_date < %s AND is_draft = FALSE', flat)
        self.assertIn('COUNT(*) FILTER (WHERE score IS NOT NULL AND survey_response_id IS NULL)', flat)
        self.assertIn('operator_id = ANY(%s)', flat)
        self.assertEqual(params, (date(2026, 11, 4), date(2026, 11, 11), [17]))
        self.assertEqual(result[17], {'call_count': 3, 'avg_score': 91.23, 'avg_score_raw': 91.234,
                                      'evaluation_row_count': 4, 'has_evaluation_data': True})

    def test_months_and_all_time(self):
        db = _Db()
        db.get_operator_score_aggregates_for_period([5], months=['2026-09', '2026-10'])
        sql, params = db.cursor.executions[-1]
        self.assertIn('month = ANY(%s) AND is_draft = FALSE', _flat(sql))
        self.assertEqual(params, (['2026-09', '2026-10'], [5]))
        db.get_operator_score_aggregates_for_period([5])
        sql, params = db.cursor.executions[-1]
        self.assertIn('WHERE TRUE AND is_draft = FALSE', _flat(sql))
        self.assertEqual(params, ([5],))

    def test_empty_scope_does_not_query(self):
        db = _Db()
        self.assertEqual(db.get_operator_score_aggregates_for_period([None, 'x'], months=['2026-10']), {})
        self.assertEqual(db.cursor.executions, [])


class _SlaCursor(_Cursor):
    """Первый запрос — строки оценок, второй — критерии направлений."""

    def __init__(self, rows, criteria):
        super().__init__(rows)
        self.criteria = criteria

    def fetchall(self):
        return self.rows if len(self.executions) == 1 else self.criteria


def _sla_row(call_id, operator_id, direction_id):
    row = [None] * 14
    row[0], row[1], row[7] = call_id, operator_id, direction_id
    return tuple(row)


class PeriodFeedbackRowsTests(unittest.TestCase):
    def _db(self, rows, criteria=()):
        db = _Db()
        db.cursor = _SlaCursor(rows, list(criteria))
        db.seen = []
        db._feedback_sla_row_dict = lambda row, reference_date=None: db.seen.append(row) or {'call_id': row[0]}
        return db

    def test_period_cuts_both_latest_versions_and_rows(self):
        db = self._db([_sla_row(1, 17, 72), _sla_row(2, 17, 72), _sla_row(3, 17, None)], [(72, [{'isCritical': True}])])
        rows = db.get_feedback_sla_rows_for_period([17], start=date(2026, 11, 4), end=date(2026, 11, 10))
        sql, params = db.cursor.executions[0]
        flat = _flat(sql)
        self.assertIn('WHERE appeal_date >= %s AND appeal_date < %s AND is_draft = FALSE', flat)
        self.assertIn('WHERE c.appeal_date >= %s AND c.appeal_date < %s AND c.is_draft = FALSE', flat)
        self.assertIn("LOWER(COALESCE(op.role, '')) IN ('operator', 'trainee')", flat)
        # Люди — уже в первой выборке (последняя версия оценки зависит только от
        # строк самого оператора): иначе ради группы перебирались бы все оценки периода.
        self.assertIn('AND operator_id = ANY(%s) GROUP BY operator_id, phone_number, month, appeal_date', flat)
        self.assertEqual(params, (date(2026, 11, 4), date(2026, 11, 11), [17], date(2026, 11, 4), date(2026, 11, 11)))
        self.assertEqual(rows, [{'call_id': 1}, {'call_id': 2}, {'call_id': 3}])

    def test_direction_criteria_are_read_once_per_direction(self):
        """Критерии — JSON по 40–47 КБ: в каждой строке за год по группе это сотни мегабайт."""
        criteria = [{'isCritical': True}]
        db = self._db([_sla_row(1, 17, 72), _sla_row(2, 18, 72), _sla_row(3, 17, 5), _sla_row(4, 17, None)],
                      [(72, criteria), (5, [])])
        db.get_feedback_sla_rows_for_period([17, 18], months=['2026-10'])
        main_sql = _flat(db.cursor.executions[0][0])
        self.assertNotIn('LEFT JOIN directions', main_sql)
        self.assertNotIn('criteria', main_sql)
        directions_sql, directions_params = db.cursor.executions[1]
        self.assertIn('FROM directions WHERE id = ANY(%s)', _flat(directions_sql))
        self.assertEqual(directions_params, ([5, 72],))
        self.assertEqual(len(db.cursor.executions), 2)
        # В строку для расчёта срока ОС критерии встают на место прежнего d.criteria.
        self.assertIs(db.seen[0][7], criteria)
        self.assertIs(db.seen[1][7], criteria)
        self.assertEqual(db.seen[2][7], [])
        self.assertIsNone(db.seen[3][7])

    def test_months(self):
        db = self._db([])
        db.get_feedback_sla_rows_for_period([17], months=['2026-10'])
        sql, params = db.cursor.executions[-1]
        self.assertIn('c.month = ANY(%s)', _flat(sql))
        self.assertEqual(params, (['2026-10'], [17], ['2026-10']))
        self.assertEqual(len(db.cursor.executions), 1, 'нет строк — нет и запроса критериев')


class SvDataRouteWiringTests(unittest.TestCase):
    """Ручка /api/sv/data: целый месяц — прежним путём, период — новыми функциями без плана."""

    @classmethod
    def setUpClass(cls):
        source = _routes_source()
        start = source.index("@bp.route('/sv/data'")
        cls.body = source[start:source.index("@bp.route('/sv/supervisor_hours/sync'", start)]

    def test_month_keeps_the_old_functions_and_plan(self):
        self.assertIn("db.get_operator_score_aggregates_for_month(month=month, operator_ids=metric_ids)", self.body)
        self.assertIn("db.get_operator_call_evaluation_targets_for_month(metric_ids, month=month)", self.body)
        self.assertIn("db.get_group_operators_for_month(group_id, month)", self.body)
        # План — только у месячного ответа, в обоих режимах (группа и СВ).
        self.assertEqual(self.body.count('"plan_available": period[\'kind\'] == \'month\','), 2)

    def test_period_uses_period_functions_and_has_no_plan(self):
        self.assertIn("db.get_group_operators_for_period(group_id, period_start, period_end)", self.body)
        self.assertIn("db.get_operator_score_aggregates_for_period(", self.body)
        self.assertIn("db.get_feedback_sla_operator_stats_for_period(", self.body)
        period_branch = self.body.split("# Период не из одного месяца (#389)", 1)[1].split("seen_response_ids", 1)[0]
        self.assertIn("operator_targets = {}", period_branch)
        self.assertNotIn("targets_for_month", period_branch)

    def test_response_names_its_period(self):
        self.assertIn('"requested_period": requested_period', self.body)
        self.assertIn("period, period_error = _journal_period_args(request.args)", self.body)


if __name__ == '__main__':
    unittest.main()
