# -*- coding: utf-8 -*-
"""Каталог запросов портала к базе Oktell (cdr/oktell_datasets.py).

По каталогу один и тот же текст запроса строят две стороны: портал (уходит мосту Oktell) и
коннектор Oktell на VM шлюза (исполняет сам; от портала ему приходят имя набора, параметры и
отпечаток текста — не SQL). Что здесь закреплено:

  * отпечаток каталога: ИЗМЕНИЛ ЗАПРОС — ОБНОВИ КОННЕКТОР НА VM ШЛЮЗА и поправь число в тесте;
  * (имя набора, параметры) переживают JSON и дают тот же текст и тот же отпечаток;
  * параметры чистятся: ни дата, ни число, ни идентификатор не протащат в текст чужой SQL;
  * таблицы, которые читает набор, объявлены — по ним коннектор проверяет права учётки базы;
  * построители портала отдают текст с именем набора, а сырых запросов к базе Oktell в коде
    портала вне каталога нет (иначе шлюз их исполнить не сможет).
"""
import ast
import json
import os
import re
import sys
import unittest
import uuid
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr import oktell_datasets  # noqa: E402
from tests import source_cache  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY = date(2026, 10, 8)
CONN_A = '0f8fad5b-d9cb-469f-a165-70867728950e'
CONN_B = '7C9E6679-7425-40DE-944B-E07FC1F90AE7'
KEY = '7000000101'

# Отпечаток каталога. Он меняется, когда меняется текст ЛЮБОГО набора (или их список).
# Коннектор Oktell на VM шлюза строит запросы из СВОЕЙ копии каталога и исполняет только те,
# чей текст совпал с текстом портала. Поэтому, меняя запрос:
#   1) поправь отпечаток здесь;
#   2) после выкладки портала обнови коннектор на VM шлюза (cdr_bridge/README.md, «Коннектор
#      Oktell» → «Обновление»): пока он старый, изменённый набор идёт мосту Oktell, а если
#      моста нет — не исполняется вовсе.
CATALOG_FINGERPRINT = '1ab7e84adc3a74fe'

INJECTIONS = ("20261008' OR 1=1 --", "20261008; DROP TABLE x", "2026-10-08", "'; EXEC xp_cmdshell 'dir' --",
              "0x41", "1 UNION SELECT Password FROM oktell_settings.dbo.A_Users", None, [], {}, 5.5, True)


class CatalogTests(unittest.TestCase):

    def test_fingerprint_is_pinned(self):
        self.assertEqual(
            oktell_datasets.fingerprint(), CATALOG_FINGERPRINT,
            'Текст запроса к базе Oktell изменился. Поправь CATALOG_FINGERPRINT и ОБНОВИ КОННЕКТОР '
            'НА VM ШЛЮЗА (cdr_bridge/README.md → «Коннектор Oktell» → «Обновление»): до этого '
            'изменённый набор шлюз исполнять не будет.')

    def test_every_dataset_builds_from_its_sample_and_survives_json(self):
        samples = oktell_datasets.samples(DAY)
        self.assertEqual(set(samples), set(oktell_datasets.NAMES))
        self.assertEqual(len(oktell_datasets.NAMES), 21)
        for name, params in samples.items():
            with self.subTest(name=name):
                query = oktell_datasets.build(name, params)
                self.assertIsInstance(query, str)
                self.assertEqual(query.dataset, name)
                self.assertTrue(query.lstrip().upper().startswith('SELECT'))
                # Так поступит коннектор: параметры пришли из JSON, текст он строит сам.
                again = oktell_datasets.build(name, json.loads(json.dumps(query.params)))
                self.assertEqual(again, query)
                self.assertEqual(again.digest, query.digest)
                self.assertEqual(again.params, query.params)
                self.assertEqual(query.digest, oktell_datasets.digest(str(query)))

    def test_query_is_a_plain_string_for_the_bridge(self):
        query = oktell_datasets.build('call_count', {'date_from': '20261008', 'date_to': '20261009'})
        self.assertEqual(json.dumps({'sql': query}), json.dumps({'sql': str(query)}))
        self.assertIn('A_Cube_CC_OperatorStates', query)
        self.assertEqual(type(query + ''), str, 'склейка даёт обычную строку — без имени набора')

    def test_only_selects(self):
        for name, params in oktell_datasets.samples(DAY).items():
            with self.subTest(name=name):
                text = oktell_datasets.build(name, params)
                # Целым словом: колонка dt_insert — не INSERT.
                self.assertIsNone(re.search(
                    r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|EXEC|EXECUTE|MERGE|TRUNCATE|OPENROWSET|'
                    r'OPENQUERY|INTO|GRANT|DENY|WAITFOR|SHUTDOWN)\b|\bxp_|\bsp_', text, re.I))
                for forbidden in (';', '--', '/*'):
                    self.assertNotIn(forbidden, text)

    def test_declared_tables_are_exactly_the_tables_in_the_text(self):
        names = {name: key for key, (name, _columns) in oktell_datasets.TABLES.items()}
        used = set()
        for name, params in oktell_datasets.samples(DAY).items():
            with self.subTest(name=name):
                text = oktell_datasets.build(name, dict(params, group_by='line') if name == 'billing' else params)
                found = set(re.findall(r'oktell(?:_cc_temp|_settings)?\.dbo\.[A-Za-z_0-9]+', text))
                self.assertTrue(found <= set(names), 'таблица не объявлена в TABLES: %s' % (found - set(names)))
                self.assertEqual({names[item] for item in found}, set(oktell_datasets.tables_of(name)))
                used |= {names[item] for item in found}
        self.assertEqual(used, set(oktell_datasets.TABLES), 'в TABLES нет лишних таблиц')

    def test_billing_tables_depend_on_the_grouping(self):
        """«Группировка» (день × час) не читает оценки водителей — учётке без права на
        quality_employes она всё равно доступна; «таксопарки» и «линии» без него — нет."""
        names = {name: key for key, (name, _columns) in oktell_datasets.TABLES.items()}
        base = {'date_from': '20261008', 'date_to': '20261009', 'minute_from': 0, 'minute_to': 1439,
                'sl_seconds': 20}
        expected = {'hour': {'calls'}, 'park': {'calls', 'ratings'}, 'line': {'calls', 'connections', 'ratings'}}
        for group_by, tables in expected.items():
            with self.subTest(group_by=group_by):
                query = oktell_datasets.build('billing', dict(base, group_by=group_by))
                found = set(re.findall(r'oktell(?:_cc_temp|_settings)?\.dbo\.[A-Za-z_0-9]+', query))
                self.assertEqual({names[item] for item in found}, tables)
                self.assertEqual(set(oktell_datasets.tables_of('billing', query.params)), tables)
        self.assertEqual(set(oktell_datasets.tables_of('billing')), {'calls', 'connections', 'ratings'})

    def test_oktell_users_are_read_by_id_and_login_only(self):
        """В таблице пользователей Oktell лежат пароли: каталогу нужны только ID и Login, и право
        учётке шлюза выдаётся на эти две колонки."""
        self.assertEqual(oktell_datasets.TABLES['users'], ('oktell_settings.dbo.A_Users', 'ID, Login'))
        self.assertEqual(oktell_datasets.table_probe_sql('users'),
                         'SELECT TOP 0 ID, Login FROM oktell_settings.dbo.A_Users')
        for name, params in oktell_datasets.samples(DAY).items():
            text = oktell_datasets.build(name, params)
            if 'A_Users' not in text:
                continue
            with self.subTest(name=name):
                self.assertEqual(set(re.findall(r'\bu\.([A-Za-z_]+)', text)), {'ID', 'Login'})
                self.assertNotIn('password', text.lower())

    def test_row_cap_matches_the_bridge(self):
        self.assertEqual(oktell_datasets.MAX_ROWS, 1000)


class ParameterCleaningTests(unittest.TestCase):

    def assert_refused(self, name, params):
        with self.assertRaises(ValueError):
            oktell_datasets.build(name, params)

    def test_unknown_dataset_and_bad_params_object(self):
        self.assert_refused('drop_everything', {})
        self.assert_refused(None, {})
        self.assert_refused(['call_count'], {})
        self.assert_refused('call_count', None)
        self.assert_refused('call_count', 'date_from=20261008')

    def test_dates_are_eight_digits_of_a_real_day(self):
        for value in INJECTIONS + ('20261332', '2026108', '202610081'):
            with self.subTest(value=value):
                self.assert_refused('call_count', {'date_from': value, 'date_to': '20261009'})
                self.assert_refused('call_count', {'date_from': '20261008', 'date_to': value})
        self.assert_refused('call_count', {'date_from': '20261008'})

    def test_period_is_bounded(self):
        oktell_datasets.build('forecast_daily', {'date_from': '20260101', 'date_to': '20260531'})
        self.assert_refused('forecast_daily', {'date_from': '20240101', 'date_to': '20261009'})
        self.assert_refused('guard_history', {'since': '20240101 00:00:00', 'until': '20261009 00:00:00'})

    def test_numbers_are_integers_in_bounds(self):
        base = {'date_from': '20261008', 'date_to': '20261009', 'minute_from': 0, 'minute_to': 1439,
                'sl_seconds': 20, 'group_by': 'park'}
        for field in ('minute_from', 'minute_to', 'sl_seconds'):
            for value in ("20 OR 1=1", "1; DROP TABLE x", -1, 10 ** 9, 5.5, None, True, [20], float('inf')):
                with self.subTest(field=field, value=value):
                    self.assert_refused('billing', dict(base, **{field: value}))
        self.assertIn('t.LenQueue <= 30 ', oktell_datasets.build('billing', dict(base, sl_seconds='30')))
        self.assert_refused('status_page', {'date_from': '20261008', 'date_to': '20261009', 'cursor': '0 OR 1=1'})
        self.assert_refused('wallboard_hourly', {'hour_to': 99, 'sl_seconds': 20})
        self.assert_refused('eval_sample', {'date_from': '20261008', 'date_to': '20261009',
                                            'operator_ids': [CONN_A], 'cap': 100000})

    def test_unknown_billing_group_means_parks_like_before(self):
        base = {'date_from': '20261008', 'date_to': '20261009', 'minute_from': 0, 'minute_to': 1439,
                'sl_seconds': 20}
        park = oktell_datasets.build('billing', dict(base, group_by='park'))
        other = oktell_datasets.build('billing', dict(base, group_by="line' OR 1=1"))
        self.assertEqual(other, park)
        self.assertEqual(other.params['group_by'], 'park')

    def test_identifiers_are_uuids(self):
        ok = oktell_datasets.build('record_paths', {'conn_ids': [CONN_A, CONN_B]})
        self.assertIn("WHERE Id IN ('%s', '%s')" % (CONN_A, CONN_B), ok)
        for bad in ([], [CONN_A, "x') OR 1=1 --"], [CONN_A + "'"], CONN_A, [None], [uuid.UUID(CONN_A)],
                    ['%s OR 1=1' % CONN_A], [CONN_A] * 1001):
            for name in ('record_paths', 'call_end_party', 'call_parks'):
                with self.subTest(name=name, bad=str(bad)[:40]):
                    self.assert_refused(name, {'conn_ids': bad})
        self.assert_refused('eval_sample', {'date_from': '20261008', 'date_to': '20261009',
                                            'operator_ids': ["' OR 1=1 --"], 'cap': 5})

    def test_connection_types_are_one_of_three(self):
        base = {'date_from': '20261008', 'date_to': '20261009'}
        for value in oktell_datasets.EVAL_CONNECTION_TYPES:
            self.assertIn('s.ConnectionType IN %s AND' % value,
                          oktell_datasets.build('eval_operators', dict(base, conn_types=value)))
        for value in ('(1, 5) OR 1=1', '(4)', '1', '(1,5)', ['(1)']):
            with self.subTest(value=value):
                self.assert_refused('eval_operators', dict(base, conn_types=value))

    def test_test_number_keys_are_ten_digits_or_dropped(self):
        base = {'date_from': '20261008', 'date_to': '20261009'}
        query = oktell_datasets.build('resource_hourly', dict(
            base, test_keys=[KEY, "1') OR 1=1 --", '12345', 7000000101, None, KEY, '٧٠٠٠٠٠٠١٠١']))
        self.assertEqual(query.params['test_keys'], [KEY])
        self.assertEqual(query.count("NOT IN ('%s')" % KEY), 1)
        self.assertNotIn('OR 1=1', query)
        self.assertEqual(oktell_datasets.build('resource_hourly', dict(base, test_keys="';--")).params['test_keys'], [])
        self.assert_refused('test_number_calls', dict(base, phone_keys=["' OR 1=1"], row_cap=1000))

    def test_phone_tail_is_ascii_digits(self):
        base = {'date_from': '20261008', 'date_to': '20261009', 'operator_ids': [CONN_A], 'cap': 60}
        query = oktell_datasets.build('eval_sample', dict(base, phone_digits="+7 (701) 222-33-44' OR 1=1 --"))
        # Остаются одни цифры (и «1=1» превращается в две цифры хвоста) — последние десять.
        self.assertEqual(query.params['phone_digits'], '1222334411')
        self.assertNotIn('OR 1=1', query)
        self.assertIn("LIKE '%" + query.params['phone_digits'] + "' ", query)
        self.assertNotIn('LIKE', oktell_datasets.build('eval_sample', dict(base, phone_digits='нет цифр')))

    def test_moments_have_a_strict_format(self):
        ok = oktell_datasets.build('break_episodes', {'since': '2026-10-08T10:00:00',
                                                      'until': '2026-10-08T13:00:00', 'icode': 4})
        self.assertIn("CONVERT(datetime, '2026-10-08T10:00:00', 126)", ok)
        for bad in ("2026-10-08T10:00:00' OR 1=1", '2026-10-08 10:00:00', '20261008 10:00:00', None, 5):
            with self.subTest(bad=bad):
                self.assert_refused('break_episodes', {'since': bad, 'until': '2026-10-08T13:00:00', 'icode': 4})
        for bad in ("20261008 10:00:00' OR 1=1", '2026-10-08T10:00:00', '20261008', '20261008 25:00:00', None, 5):
            with self.subTest(bad=bad):
                self.assert_refused('guard_history', {'since': '20261008 10:00:00', 'until': bad})

    def test_login_is_the_only_free_string_and_it_is_quoted(self):
        base = {'since': '20261008 10:00:00', 'until': '20261008 10:05:00'}
        query = oktell_datasets.build('guard_operator_history', dict(base, login="6612' OR 1=1 --"))
        self.assertIn("WHERE u.Login = '6612'' OR 1=1 --' ", query)
        self.assert_refused('guard_operator_history', dict(base, login='6612\n; DROP TABLE x'))
        self.assert_refused('guard_operator_history', dict(base, login='x' * 500))

    def test_extra_parameters_are_ignored_not_executed(self):
        query = oktell_datasets.build('call_count', {'date_from': '20261008', 'date_to': '20261009',
                                                     'sql': 'DROP TABLE x', 'where': '1=1'})
        self.assertEqual(query.params, {'date_from': '20261008', 'date_to': '20261009'})
        self.assertNotIn('DROP', query)


class PortalBuildersTests(unittest.TestCase):
    """Построители портала отдают текст каталога вместе с именем набора и параметрами."""

    FUNCTIONS = {
        '_oktell_status_page_sql', '_oktell_call_count_sql', '_oktell_resource_hourly_sql',
        '_oktell_billing_sql', '_oktell_billing_detail_page_sql', '_oktell_billing_detail_export_page_sql',
        '_oktell_billing_operator_calls_sql', '_oktell_billing_operator_states_sql',
        '_oktell_wallboard_hourly_sql', '_oktell_break_episodes_sql', '_oktell_normalize_conn_id',
        '_oktell_call_end_party_rows_sql', '_oktell_eval_operators_sql', '_oktell_eval_sample_sql',
    }

    @classmethod
    def setUpClass(cls):
        cls.source = source_cache.monolith_flat_path().read_text(encoding='utf-8-sig')
        tree = source_cache.parse(cls.source)
        body = [node for node in tree.body
                if (isinstance(node, ast.FunctionDef) and node.name in cls.FUNCTIONS)
                or (isinstance(node, ast.Assign)
                    and {getattr(t, 'id', '') for t in node.targets} & {'_OKTELL_EVAL_CONNECTION_TYPES'})]
        found = {node.name for node in body if isinstance(node, ast.FunctionDef)}
        assert found == cls.FUNCTIONS, cls.FUNCTIONS - found
        cls.ns = {'uuid': uuid, 'datetime': datetime, 'timedelta': timedelta,
                  'OKTELL_BILLING_SL_DEFAULT_SECONDS': 20, 'OKTELL_API_PAGE_SIZE': 1000, 'SZOV_BREAK_ICODE': 4}
        exec(compile(ast.Module(body=body, type_ignores=[]), 'bot_schedule2.py', 'exec'), cls.ns)

    def test_builders_return_catalog_queries(self):
        ns = self.ns
        cases = {
            'status_page': ns['_oktell_status_page_sql']('20261008', '20261009', -1),
            'call_count': ns['_oktell_call_count_sql']('20261008', '20261015'),
            'resource_hourly': ns['_oktell_resource_hourly_sql']('20261008', '20261015'),
            'billing': ns['_oktell_billing_sql']('20261008', '20261015', 0, 1439, 20, 'line'),
            'billing_detail_page': ns['_oktell_billing_detail_page_sql']('20261008', '20261009', 0, 1439, 2, 50),
            'billing_detail_export': ns['_oktell_billing_detail_export_page_sql']('20261008', '20261009', 0, 1439),
            'billing_operator_calls': ns['_oktell_billing_operator_calls_sql']('20261008', '20261009', 0, 1439),
            'billing_operator_states': ns['_oktell_billing_operator_states_sql']('20261008', '20261009', 540, 1080),
            'wallboard_hourly': ns['_oktell_wallboard_hourly_sql'](14, date(2026, 10, 8)),
            'break_episodes': ns['_oktell_break_episodes_sql'](datetime(2026, 10, 8, 10), datetime(2026, 10, 8, 13)),
            'call_end_party': ns['_oktell_call_end_party_rows_sql']([CONN_A, CONN_B]),
            'eval_operators': ns['_oktell_eval_operators_sql']('20261001', '20261101', 20, 600),
            'eval_sample': ns['_oktell_eval_sample_sql']('20261001', '20261101', [CONN_A], 40, 20, 600),
        }
        for name, query in cases.items():
            with self.subTest(name=name):
                self.assertIsInstance(query, oktell_datasets.Query)
                self.assertEqual(query.dataset, name)
                self.assertEqual(oktell_datasets.build(name, json.loads(json.dumps(query.params))), query)
        self.assertEqual(cases['billing_detail_export'].params['size'], 1000)
        self.assertEqual(cases['call_end_party'].params['conn_ids'], [CONN_A, CONN_B.lower()])
        self.assertEqual(cases['wallboard_hourly'].params, {'hour_to': 14, 'day': '20261008',
                                                           'sl_seconds': 20, 'test_keys': []})

    def test_package_builders_return_catalog_queries(self):
        from call_qa import taxi_parks
        from oktell_guard import sweep, verify
        from resource_fte import forecast_engine
        from test_numbers import activity

        cases = {
            'call_parks': taxi_parks.oktell_parks_sql([CONN_B, CONN_A, "' OR 1=1 --"]),
            'guard_history': sweep.build_history_sql(datetime(2026, 9, 4), datetime(2026, 9, 5), cursor_enum=500),
            'guard_operator_history': verify.build_history_sql('6612', datetime(2026, 8, 18, 14, 30), 180),
            'forecast_daily': forecast_engine.oktell_daily_sql('20260101', '20260531', [KEY]),
            'forecast_params': forecast_engine.oktell_params_sql('20261001', '20261101', '20260930'),
            'forecast_after_call': forecast_engine.oktell_after_call_sql('20261001', '20261101'),
            'test_number_calls': activity.oktell_sql({KEY}, date(2026, 10, 1), date(2026, 10, 9)),
        }
        for name, query in cases.items():
            with self.subTest(name=name):
                self.assertIsInstance(query, oktell_datasets.Query)
                self.assertEqual(query.dataset, name)
                self.assertEqual(oktell_datasets.build(name, json.loads(json.dumps(query.params))), query)

    def test_record_paths_are_asked_through_the_catalog(self):
        block = source_cache.function_node(source_cache.monolith_flat_path(), '_oktell_record_paths_by_conn')
        text = ast.get_source_segment(self.source, block)
        self.assertIn("oktell_datasets.build('record_paths', {'conn_ids': chunk})", text)

    def test_no_raw_oktell_sql_outside_the_catalog(self):
        """Запрос к базе Oktell, собранный строкой мимо каталога, шлюз исполнить не сможет: у
        него не будет имени набора. Новый запрос добавляется набором в cdr/oktell_datasets.py."""
        pattern = re.compile(r'\b(?:FROM|JOIN)\s+oktell(?:_cc_temp|_settings)?\.dbo\.', re.I)
        allowed = {os.path.join('cdr', 'oktell_datasets.py'), os.path.join('cdr', 'oktell_wallboard.py')}
        offenders = []
        for folder in ('', 'cdr', 'call_qa', 'oktell_guard', 'oktell_live', 'resource_fte', 'test_numbers',
                       'szov_wallboard', 'db', 'pbx', 'dial_list'):
            base = os.path.join(ROOT, folder)
            if not os.path.isdir(base):
                continue
            for name in sorted(os.listdir(base)):
                relative = os.path.join(folder, name) if folder else name
                if not name.endswith('.py') or relative in allowed:
                    continue
                with open(os.path.join(base, name), encoding='utf-8-sig') as handle:
                    try:
                        tree = ast.parse(handle.read())
                    except SyntaxError:
                        continue
                docstrings = set()
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        first = node.body[0] if node.body else None
                        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                                and isinstance(first.value.value, str)):
                            docstrings.add(id(first.value))
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                            and id(node) not in docstrings and pattern.search(node.value)):
                        offenders.append('%s:%d' % (relative, node.lineno))
        self.assertEqual(offenders, [], 'запрос к базе Oktell строкой мимо каталога cdr/oktell_datasets.py')


if __name__ == '__main__':
    unittest.main()
