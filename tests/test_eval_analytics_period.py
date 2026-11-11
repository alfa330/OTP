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
import re
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
    _journal_operator_month_pairs = _method('_journal_operator_month_pairs')
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
        glob = namespace['_journal_period_args'].__globals__
        glob['_journal_whole_months'] = namespace['_journal_whole_months']
        glob['_JOURNAL_DAY_RE'] = re.compile(r'\d{4}-\d{2}-\d{2}')
        glob['_JOURNAL_FIRST_DAY'] = date(2000, 1, 1)
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

    def test_out_of_range_or_odd_format_dates_are_rejected(self):
        # Разбор ревьюера: «9999-12-31» давал 95 тысяч месяцев и молча пустую группу,
        # а fromisoformat в 3.11 принимает «2026-W45-1» и «20261110».
        for raw in ({'date_from': '2026-01-01', 'date_to': '9999-12-31'},
                    {'date_from': '1999-12-01', 'date_to': '1999-12-31'},
                    {'date_from': '2026-W45-1', 'date_to': '2026-11-10'},
                    {'date_from': '20261104', 'date_to': '20261110'}):
            with self.subTest(raw=raw):
                period, error = self.parse(raw)
                self.assertIsNone(period)
                self.assertTrue(error)
        period, error = self.parse({'date_from': f'{datetime.now().year}-01-01',
                                    'date_to': f'{datetime.now().year}-12-31'})
        self.assertIsNone(error, 'текущий год целиком — законный период')

    def test_broken_or_reversed_period_is_an_error_not_a_silent_month(self):
        for raw in ({'date_from': '2026-11-10'}, {'date_from': '10.11.2026', 'date_to': '2026-11-10'},
                    {'date_from': '2026-11-10', 'date_to': '2026-11-04'}, {'month': '2026-13'}):
            with self.subTest(raw=raw):
                period, error = self.parse(raw)
                self.assertIsNone(period)
                self.assertTrue(error)


class PeriodBoundsTests(unittest.TestCase):
    """_journal_period_bounds — границы состава и мерка оценок (раньше жили в теле ручки)."""

    @classmethod
    def setUpClass(cls):
        cls.bounds = staticmethod(_route_function('_journal_period_bounds')['_journal_period_bounds'])

    def test_each_kind(self):
        today = date(2026, 11, 10)
        start, end, requested, metric = self.bounds({'kind': 'month', 'month': '2026-02'}, today)
        self.assertEqual((start, end), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(requested, {'from': '2026-02-01', 'to': '2026-02-28'})
        self.assertEqual(metric, {})
        months = {'kind': 'months', 'months': ['2026-09', '2026-10'],
                  'start': date(2026, 9, 1), 'end': date(2026, 10, 31)}
        self.assertEqual(self.bounds(months, today)[3], {'months': ['2026-09', '2026-10']})
        week = {'kind': 'range', 'start': date(2026, 11, 4), 'end': date(2026, 11, 10)}
        self.assertEqual(self.bounds(week, today)[3], {'start': date(2026, 11, 4), 'end': date(2026, 11, 10)})
        start, end, requested, metric = self.bounds({'kind': 'all'}, today)
        self.assertEqual((start, end), (date(2000, 1, 1), today))
        self.assertEqual(requested, {'from': '', 'to': ''}, '«Весь период» о себе — пусто')
        self.assertEqual(metric, {}, '«Весь период» — без отбора по датам')


def _member_months():
    return _route_function('_journal_member_months')['_journal_member_months']


def _segment(group_id, start, end, current=False):
    return {'group_id': group_id, 'start_date': start, 'end_date': end, 'is_current': current}


class MemberMonthsTests(unittest.TestCase):
    """Разбор ревьюера F1: за год и «Весь период» человек приносит группе оценки
    только тех месяцев, когда был в ней (каждый месяц — целиком, как в месячном виде)."""

    def test_only_months_spent_in_this_group(self):
        ops = [{'id': 7, 'group_segments': [_segment(5, '2026-01-01', '2026-01-20'),
                                            _segment(9, '2026-01-21', '2026-12-31', True)]},
               {'id': 8, 'group_segments': [_segment(5, '2026-03-15', '2026-05-02')]}]
        self.assertEqual(_member_months()(ops, 5),
                         [(7, '2026-01'), (8, '2026-03'), (8, '2026-04'), (8, '2026-05')])

    def test_whole_months_period_keeps_only_its_months(self):
        ops = [{'id': 8, 'group_segments': [_segment(5, '2026-08-20', '2026-11-02')]}]
        self.assertEqual(_member_months()(ops, 5, ['2026-09', '2026-10']),
                         [(8, '2026-09'), (8, '2026-10')])

    def test_year_boundary_and_broken_segments(self):
        ops = [{'id': 3, 'group_segments': [_segment(5, '2025-12-10', '2026-01-05'),
                                            _segment(5, '2026-07-17', '2026-07-16'),
                                            {'group_id': 5, 'start_day': 1, 'end_day': 31}]},
               {'id': None, 'group_segments': [_segment(5, '2026-01-01', '2026-01-31')]}]
        self.assertEqual(_member_months()(ops, 5), [(3, '2025-12'), (3, '2026-01')])


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

    def test_member_months_replace_operator_list(self):
        db = _Db()
        db.get_operator_score_aggregates_for_period(
            [99], start=date(2026, 11, 4), end=date(2026, 11, 10),
            operator_months=[(7, '2026-11'), ('8', '2026-11'), ('x', '2026-11'), (9, 'bad')])
        sql, params = db.cursor.executions[-1]
        flat = _flat(sql)
        self.assertIn('AND (operator_id, month) IN (SELECT * FROM unnest(%s::int[], %s::text[]))', flat)
        self.assertNotIn('operator_id = ANY(%s)', flat, 'список людей не нужен — пары точнее')
        self.assertEqual(params, (date(2026, 11, 4), date(2026, 11, 11), [7, 8], ['2026-11', '2026-11']))
        empty = _Db()
        self.assertEqual(empty.get_operator_score_aggregates_for_period(operator_months=[]), {})
        self.assertEqual(empty.cursor.executions, [], 'пустые пары — не «все люди»')

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

    def test_member_months_in_feedback_rows(self):
        db = self._db([])
        db.get_feedback_sla_rows_for_period(months=['2026-09', '2026-10'],
                                            operator_months=[(17, '2026-09')])
        sql, params = db.cursor.executions[-1]
        self.assertIn('AND (operator_id, month) IN (SELECT * FROM unnest(%s::int[], %s::text[])) GROUP BY',
                      _flat(sql))
        self.assertEqual(params, (['2026-09', '2026-10'], [17], ['2026-09'], ['2026-09', '2026-10']))
        empty = self._db([])
        self.assertEqual(empty.get_feedback_sla_rows_for_period(operator_months=[]), [])
        self.assertEqual(empty.cursor.executions, [])

    def test_months(self):
        db = self._db([])
        db.get_feedback_sla_rows_for_period([17], months=['2026-10'])
        sql, params = db.cursor.executions[-1]
        self.assertIn('c.month = ANY(%s)', _flat(sql))
        self.assertEqual(params, (['2026-10'], [17], ['2026-10']))
        self.assertEqual(len(db.cursor.executions), 1, 'нет строк — нет и запроса критериев')


# ── /api/sv/data — настоящая ручка (Flask test client, база подменена) ───────
# Тесты разбора задачи (вместо проверок строк кода): какую функцию состава и какую
# мерку оценок получает каждый вид периода и что ответ говорит о своём периоде.

def _load_routes_module():
    """supervisors.routes с подменённым `database` (на импорте настоящий поднимает пул)."""
    import sys
    import types
    from unittest import mock
    fake_database = types.ModuleType('database')
    fake_database.db = mock.MagicMock()
    with mock.patch.dict(sys.modules, {'database': fake_database}):
        sys.modules.pop('supervisors.routes', None)
        import supervisors.routes as routes
    sys.modules.pop('supervisors.routes', None)
    return routes


def _sv_client(db, supervisors=()):
    from unittest import mock
    from flask import Flask
    routes = _load_routes_module()
    routes.db = db
    identity = lambda fn: fn  # noqa: E731
    deps = {name: mock.MagicMock() for name in (
        'ALLOWED_ORIGINS', 'API_TOKEN', 'AVATAR_SIGNED_URL_CACHE', 'AVATAR_SIGNED_URL_CACHE_LOCK',
        'AVATAR_SIGNED_URL_CACHE_MAX_ITEMS', 'AVATAR_SIGNED_URL_CACHE_MIN_REMAINING_SECONDS',
        'AVATAR_SIGNED_URL_TTL_SECONDS', 'ROLE_HIERARCHY', '_build_avatar_signed_url',
        '_build_cors_preflight_response', '_department_scope_id_for_requester', '_get_role_level',
        '_has_min_role', '_headed_department_ids', '_is_allowed_origin', '_is_global_admin_requester',
        '_load_target_user_with_scope', '_normalize_origin', '_operator_item_id',
        '_supervisor_hours_settings_scope', '_sync_supervisor_clockster_marks', 'get_gcs_client',
        '_is_supervisor_role')}
    deps.update(
        require_api_key=identity,
        _get_authenticated_requester=lambda: (1, (1, 'u', 'Админ', 'admin'), None),
        _normalize_user_role=lambda role: str(role or '').lower(),
        _headed_department_id=lambda _id: None,
        _is_marketing_observer=lambda *_: False,
        _is_admin_role=lambda role: role == 'admin',
        _scoped_groups_for_requester=lambda *a, **k: (
            [{'id': 5, 'name': 'Основа', 'supervisors': [{'id': sv} for sv in supervisors]}], None),
        _build_avatar_signed_url=lambda *a: None,
    )
    app = Flask(__name__)
    app.register_blueprint(routes.build_supervisors_blueprint(**deps))
    return app.test_client()


def _roster_db(segments=(), supervisor_rows=()):
    from unittest import mock
    db = mock.MagicMock()
    row = {'id': 17, 'name': 'Оператор', 'role': 'operator', 'status': 'working',
           'group_segments': list(segments)}
    db.get_group_operators_for_month.return_value = [row]
    db.get_group_operators_for_period.return_value = [row]
    for name in ('get_operator_score_aggregates_for_month', 'get_operator_call_evaluation_targets_for_month',
                 'get_feedback_sla_operator_stats_for_month', 'get_operator_score_aggregates_for_period',
                 'get_feedback_sla_operator_stats_for_period'):
        getattr(db, name).return_value = {}
    db._get_cursor.return_value.__enter__.return_value.fetchall.return_value = list(supervisor_rows)
    return db


class SvDataPeriodBehaviourTests(unittest.TestCase):
    def _get(self, query, segments=(), supervisors=(), supervisor_rows=()):
        db = _roster_db(segments, supervisor_rows)
        response = _sv_client(db, supervisors).get(f'/api/sv/data?group_id=5&{query}')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return db, response.get_json()

    def test_whole_month_goes_the_month_way_with_plan(self):
        db, body = self._get('date_from=2026-10-01&date_to=2026-10-31')
        db.get_group_operators_for_month.assert_called_once_with(5, '2026-10')
        db.get_group_operators_for_period.assert_not_called()
        db.get_operator_score_aggregates_for_period.assert_not_called()
        db.get_operator_call_evaluation_targets_for_month.assert_called_once()
        self.assertEqual(body['requested_month'], '2026-10')
        self.assertTrue(body['plan_available'])
        self.assertEqual(body['requested_period'], {'from': '2026-10-01', 'to': '2026-10-31'})

    def test_several_months_count_only_months_in_the_group(self):
        segments = [_segment(5, '2026-09-01', '2026-09-20'), _segment(9, '2026-09-21', '2026-10-31', True)]
        db, body = self._get('date_from=2026-09-01&date_to=2026-10-31', segments)
        db.get_group_operators_for_period.assert_called_once_with(5, date(2026, 9, 1), date(2026, 10, 31))
        db.get_group_operators_for_month.assert_not_called()
        db.get_operator_score_aggregates_for_period.assert_called_once_with(
            operator_months=[(17, '2026-09')], months=['2026-09', '2026-10'])
        db.get_feedback_sla_operator_stats_for_period.assert_called_once_with(
            operator_months=[(17, '2026-09')], months=['2026-09', '2026-10'])
        db.get_operator_call_evaluation_targets_for_month.assert_not_called()
        self.assertIsNone(body['requested_month'])
        self.assertFalse(body['plan_available'])
        self.assertEqual(body['requested_period'], {'from': '2026-09-01', 'to': '2026-10-31'})

    def test_range_counts_by_appeal_day_in_member_months(self):
        db, body = self._get('date_from=2026-11-04&date_to=2026-11-10',
                             [_segment(5, '2026-11-04', '2026-11-10', True)])
        db.get_group_operators_for_period.assert_called_once_with(5, date(2026, 11, 4), date(2026, 11, 10))
        db.get_operator_score_aggregates_for_period.assert_called_once_with(
            operator_months=[(17, '2026-11')], start=date(2026, 11, 4), end=date(2026, 11, 10))
        self.assertEqual(body['period_kind'], 'range')
        self.assertFalse(body['plan_available'])

    def test_all_time_counts_member_months_only_and_roster_ends_today(self):
        segments = [_segment(5, '2025-12-10', '2026-01-05'), _segment(9, '2026-01-06', '2026-11-10', True)]
        db, body = self._get('period=all', segments)
        db.get_group_operators_for_period.assert_called_once_with(5, date(2000, 1, 1), datetime.now().date())
        db.get_operator_score_aggregates_for_period.assert_called_once_with(
            operator_months=[(17, '2025-12'), (17, '2026-01')])
        db.get_feedback_sla_operator_stats_for_period.assert_called_once_with(
            operator_months=[(17, '2025-12'), (17, '2026-01')])
        self.assertEqual(body['requested_period'], {'from': '', 'to': ''})

    def test_group_supervisor_is_counted_for_the_whole_period(self):
        sv_row = (50, 'СВ группы', None, None, None, 'working', 1, None, None, None, None)
        db, body = self._get('date_from=2026-11-04&date_to=2026-11-10',
                             [_segment(5, '2026-11-04', '2026-11-10', True)], supervisors=[50],
                             supervisor_rows=[sv_row])
        calls = db.get_operator_score_aggregates_for_period.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].kwargs, {'operator_ids': [50], 'start': date(2026, 11, 4), 'end': date(2026, 11, 10)})

    def test_broken_or_odd_period_is_400(self):
        for query in ('date_from=2026-11-10&date_to=2026-11-04', 'date_from=2026-01-01&date_to=9999-12-31',
                      'date_from=2026-W45-1&date_to=2026-11-10'):
            with self.subTest(query=query):
                db = _roster_db()
                response = _sv_client(db).get(f'/api/sv/data?group_id=5&{query}')
                self.assertEqual(response.status_code, 400)
                db.get_group_operators_for_period.assert_not_called()


if __name__ == '__main__':
    unittest.main()
