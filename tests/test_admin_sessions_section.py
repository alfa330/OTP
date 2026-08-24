# -*- coding: utf-8 -*-
"""Раздел «Сессии»: фильтры, сортировка и карточка сессии.

Задача #238. До неё раздел тянул страницу за страницей и фильтровал уже
загруженное на клиенте: нажав «Админы», человек видел горсть строк из первой
сотни, а список продолжал догружаться. Здесь зафиксировано, что фильтры,
сортировка и пагинация считаются в базе, а плашки продолжают показывать полную
картину по поисковому запросу — иначе, выбрав роль, из неё было бы не выйти.

Отдельно сторожатся две вещи, которые ломаются молча:

  * белые списки. `sort`, `role` и `device` попадают в SQL как имена колонок и
    значения, поэтому берутся ТОЛЬКО из словарей класса — чужая строка обязана
    отваливаться, а не доезжать до запроса;
  * разбор user-agent живёт в SQL. Вторая копия правил на клиенте показывает,
    а не фильтрует, и плашка «Планшет 12» с пустым списком означает, что копии
    разъехались.
"""

import ast
import copy
import json
import logging
import textwrap
import typing
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests import prod_db, source_cache

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / 'database.py'
BOT_PATH = ROOT / 'bot_schedule2.py'


def _sessions_api():
    """Методы раздела из `database.py` без подъёма пула к боевой базе."""
    module = source_cache.parse(DB_PATH.read_text(encoding='utf-8-sig'))
    cls = next(n for n in module.body
               if isinstance(n, ast.ClassDef) and n.name == 'Database')
    wanted = {
        '_active_session_device_sql', '_build_active_sessions_where_clause',
        '_active_sessions_order_by', '_active_session_row',
        '_active_sessions_summary_sql', '_active_sessions_summary_row',
        'get_active_sessions_page', 'get_active_session_detail',
        'list_session_access_events', 'set_session_sensitive_access',
    }
    body = [
        node for node in cls.body
        if (isinstance(node, ast.Assign)
            and any(getattr(t, 'id', '').startswith(('_UA_', 'ACTIVE_SESSION')) for t in node.targets))
        or (isinstance(node, ast.FunctionDef) and node.name in wanted)
    ]
    stub = ast.ClassDef(name='SessionsApi', bases=[], keywords=[],
                        body=copy.deepcopy(body), decorator_list=[])
    namespace = {'Optional': typing.Optional, 'List': typing.List, 'Any': typing.Any}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[stub], type_ignores=[])),
                 str(DB_PATH), 'exec'), namespace)
    return namespace['SessionsApi']


SessionsApi = _sessions_api()


class WhereClauseTests(unittest.TestCase):
    """Что вообще доезжает до WHERE."""

    def setUp(self):
        self.api = SessionsApi()

    def test_active_only_by_default(self):
        where, params = self.api._build_active_sessions_where_clause()
        self.assertIn('us.revoked_at IS NULL', where)
        self.assertIn('us.expires_at >', where)
        self.assertEqual(params, [])

    def test_search_covers_six_columns(self):
        where, params = self.api._build_active_sessions_where_clause(search='  ай  ')
        self.assertEqual(params, ['%ай%'] * 6)
        self.assertIn('u.login', where)
        self.assertIn('sv.name', where)

    def test_admin_filter_includes_super_admin(self):
        """super_admin показывается плашкой «Администраторы» — и фильтром тоже."""
        _, params = self.api._build_active_sessions_where_clause(role='admin')
        self.assertEqual(params, [['admin', 'super_admin']])

    def test_supervisor_filter_covers_both_spellings(self):
        _, params = self.api._build_active_sessions_where_clause(role='sv')
        self.assertEqual(params, [['sv', 'supervisor']])

    def test_unknown_role_is_ignored_not_injected(self):
        where, params = self.api._build_active_sessions_where_clause(role="admin'; DROP TABLE users--")
        self.assertEqual(params, [])
        self.assertNotIn('DROP TABLE', where)

    def test_device_filter_goes_as_value_not_as_sql(self):
        where, params = self.api._build_active_sessions_where_clause(device='mobile')
        self.assertEqual(params, ['mobile'])
        self.assertIn('CASE WHEN', where)

    def test_unknown_device_is_ignored(self):
        _, params = self.api._build_active_sessions_where_clause(device='watch')
        self.assertEqual(params, [])

    def test_filters_stack(self):
        where, params = self.api._build_active_sessions_where_clause(
            search='ай', role='operator', device='desktop')
        self.assertEqual(params, ['%ай%'] * 6 + [['operator'], 'desktop'])
        self.assertEqual(where.count('AND'), where.count('AND'))


class OrderByTests(unittest.TestCase):
    """Сортировка — имя колонки в тексте запроса, поэтому только из словаря."""

    def setUp(self):
        self.api = SessionsApi()

    def test_default_is_freshest_activity_first(self):
        self.assertIn('us.last_seen_at DESC', self.api._active_sessions_order_by())

    def test_unknown_key_falls_back_to_default(self):
        sql = self.api._active_sessions_order_by('u.name; DROP TABLE users--', 'asc')
        self.assertIn('us.last_seen_at DESC', sql)
        self.assertNotIn('DROP TABLE', sql)

    def test_unknown_direction_falls_back_to_desc(self):
        self.assertIn('DESC', self.api._active_sessions_order_by('user_name', 'вверх'))

    def test_every_whitelisted_key_maps_to_a_column(self):
        for key, column in SessionsApi.ACTIVE_SESSION_SORT_KEYS.items():
            self.assertIn(column, self.api._active_sessions_order_by(key, 'asc'), key)

    def test_order_is_stable_between_pages(self):
        """Без второго ключа строки с одинаковым временем прыгают при догрузке."""
        self.assertTrue(self.api._active_sessions_order_by().rstrip().endswith('us.session_id ASC'))


SESSION_FIXTURES = [
    # session_id, user_id, ua, ip, минут с активности, срок жизни (дней), отозвана
    ('11111111-1111-1111-1111-111111111111', 1,
     'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/126 Safari/537.36', '10.0.0.1', 1, 10, False),
    ('22222222-2222-2222-2222-222222222222', 2,
     'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125', '10.0.0.2', 5, 10, False),
    ('33333333-3333-3333-3333-333333333333', 3,
     'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148 Safari', '10.0.0.3', 2, 10, False),
    ('44444444-4444-4444-4444-444444444444', 4,
     'Mozilla/5.0 (Linux; Android 13; SM-A536E) Mobile Chrome/124', '10.0.0.4', 9, 10, False),
    ('55555555-5555-5555-5555-555555555555', 5,
     'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) Safari', '10.0.0.5', 3, 10, False),
    ('66666666-6666-6666-6666-666666666666', 5, '', '10.0.0.6', 4, 10, False),
    ('77777777-7777-7777-7777-777777777777', 4,
     'Googlebot/2.1 (+http://www.google.com/bot.html)', '10.0.0.7', 6, 10, False),
    ('88888888-8888-8888-8888-888888888888', 4,
     'Mozilla/5.0 (Windows NT 10.0) Chrome/120', '10.0.0.8', 7, -1, False),   # истекла
    ('99999999-9999-9999-9999-999999999999', 4,
     'Mozilla/5.0 (Windows NT 10.0) Chrome/120', '10.0.0.9', 8, 10, True),    # прервана
]

USER_FIXTURES = [
    (1, 'Ядигаров Руслан', 'ruslan', 'super_admin', None),
    (2, 'Хайрихан Шерзад', 'sherzad', 'admin', None),
    (3, 'Супервайзер Айгуль', 'aigul', 'sv', None),
    (4, 'Оператор Асель', 'asel', 'operator', 3),
    (5, 'Оператор Бота', 'bota', 'operator', 3),
]


def _sql_literal(value):
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _stub_cte():
    """Синтетические таблицы поверх боевых: реальные данные не читаются."""
    users = ', '.join(
        '({}, {}, {}, {}, {})'.format(
            uid, _sql_literal(name), _sql_literal(login), _sql_literal(role), _sql_literal(sv))
        for uid, name, login, role, sv in USER_FIXTURES
    )
    sessions = ', '.join(
        "({}::uuid, {}, {}, {}, (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - make_interval(mins => {}),"
        " (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + make_interval(days => {}), {})".format(
            _sql_literal(sid), uid, _sql_literal(ua), _sql_literal(ip), minutes, days,
            "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')" if revoked else 'NULL::timestamp')
        for sid, uid, ua, ip, minutes, days, revoked in SESSION_FIXTURES
    )
    return textwrap.dedent(f"""
        WITH users AS (
            SELECT id, name, login, role, supervisor_id::int AS supervisor_id,
                   NULL::text AS avatar_bucket, NULL::text AS avatar_blob_path,
                   NULL::int AS department_id
              FROM (VALUES {users}) AS t(id, name, login, role, supervisor_id)
        ),
        user_sessions AS (
            SELECT session_id, user_id, user_agent, ip_address, last_seen_at, expires_at, revoked_at,
                   FALSE AS sensitive_data_unlocked,
                   NULL::timestamp AS sensitive_data_unlocked_at,
                   NULL::int AS sensitive_data_unlocked_by
              FROM (VALUES {sessions})
                   AS t(session_id, user_id, user_agent, ip_address, last_seen_at, expires_at, revoked_at)
        ),
    """).strip()


class _StubCursor:
    """Курсор, подменяющий `users` и `user_sessions` синтетическими данными."""

    def __init__(self, cursor):
        self._cursor = cursor
        self._stub = _stub_cte()

    def execute(self, sql, params=None):
        text = sql.lstrip()
        # created_at в фикстурах нет — списку он нужен только как колонка.
        text = text.replace('us.created_at', 'us.last_seen_at')
        if text.upper().startswith('WITH'):
            text = self._stub + ' ' + text[len('WITH'):].lstrip()
        else:
            text = self._stub.rstrip(',') + '\n' + text
        return self._cursor.execute(text, params)

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchone(self):
        return self._cursor.fetchone()


class SessionsPageSqlTests(unittest.TestCase):
    """Боевой SQL на синтетических данных."""

    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.connection = prod_db.connection()
        cls.raw = cls.connection.cursor()

        stub = _StubCursor(cls.raw)

        class _Api(SessionsApi):
            def _get_cursor(self):
                import contextlib

                @contextlib.contextmanager
                def ctx():
                    yield stub
                return ctx()

        cls.api = _Api()

    def tearDown(self):
        prod_db.rollback()

    def test_expired_and_revoked_sessions_are_out(self):
        page = self.api.get_active_sessions_page(limit=50)
        self.assertEqual(page['matched_sessions'], 7)
        ids = {row['session_id'] for row in page['sessions']}
        self.assertNotIn('88888888-8888-8888-8888-888888888888', ids)
        self.assertNotIn('99999999-9999-9999-9999-999999999999', ids)

    def test_role_filter_narrows_the_list_but_not_the_tiles(self):
        """Плашки продолжают считать всех — иначе из фильтра не выйти."""
        page = self.api.get_active_sessions_page(limit=50, role='operator')
        self.assertEqual({row['user_role'] for row in page['sessions']}, {'operator'})
        self.assertEqual(page['matched_sessions'], 4)
        self.assertEqual(page['summary']['total_sessions'], 7)
        self.assertEqual(page['summary']['role_counts'],
                         {'admin': 2, 'sv': 1, 'operator': 4})

    def test_device_filter_agrees_with_the_tile_it_sits_under(self):
        page = self.api.get_active_sessions_page(limit=50)
        for device, expected in page['summary']['device_counts'].items():
            filtered = self.api.get_active_sessions_page(limit=50, device=device)
            self.assertEqual(filtered['matched_sessions'], expected, device)

    def test_search_matches_supervisor_name_too(self):
        page = self.api.get_active_sessions_page(limit=50, search='Айгуль')
        self.assertEqual(page['matched_sessions'], 5)
        self.assertEqual(page['summary']['total_sessions'], 5,
                         'плашки считаются по поиску, иначе цифры врут')

    def test_filters_stack_with_search(self):
        page = self.api.get_active_sessions_page(limit=50, search='Айгуль', role='operator')
        self.assertEqual(page['matched_sessions'], 4)
        self.assertEqual(page['summary']['total_sessions'], 5)

    def test_pagination_counts_the_filtered_set(self):
        first = self.api.get_active_sessions_page(limit=3, role='operator')
        self.assertTrue(first['has_more'])
        self.assertEqual(len(first['sessions']), 3)
        second = self.api.get_active_sessions_page(limit=3, offset=3, role='operator')
        self.assertFalse(second['has_more'])
        self.assertEqual(len(second['sessions']), 1)
        self.assertFalse(
            {r['session_id'] for r in first['sessions']}
            & {r['session_id'] for r in second['sessions']},
            'страницы не должны пересекаться')

    def test_sorting_is_done_by_the_server(self):
        asc = self.api.get_active_sessions_page(limit=50, sort_key='user_name', sort_dir='asc')
        names = [row['user_name'] for row in asc['sessions']]
        self.assertEqual(names, sorted(names))
        desc = self.api.get_active_sessions_page(limit=50, sort_key='user_name', sort_dir='desc')
        self.assertEqual([r['user_name'] for r in desc['sessions']], sorted(names, reverse=True))

    def test_default_sort_is_freshest_first(self):
        page = self.api.get_active_sessions_page(limit=50)
        self.assertEqual(page['sessions'][0]['ip_address'], '10.0.0.1')


class DeviceClassificationParityTests(unittest.TestCase):
    """SQL и клиент обязаны звать одно и то же устройство одинаково.

    Правила разбора user-agent живут в двух местах: в SQL (по нему фильтруют и
    считают плашки) и в `src/components/sessions/userAgent.js` (по нему рисуют
    строку). Корпус общий с `tests/session_user_agent.test.mjs` — расхождение
    видно с обеих сторон, а не всплывает плашкой «Планшет 12» с пустым списком.
    """

    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.cursor = prod_db.connection().cursor()
        corpus = json.loads((ROOT / 'tests' / 'data' / 'session_user_agents.json')
                            .read_text(encoding='utf-8'))
        cls.cases = corpus['cases']

    def tearDown(self):
        prod_db.rollback()

    def test_sql_agrees_with_the_shared_corpus(self):
        expression = SessionsApi._active_session_device_sql("LOWER(COALESCE(t.ua, ''))")
        self.cursor.execute(
            f"SELECT t.ua, ({expression}) FROM unnest(%s::text[]) AS t(ua)",
            ([case['ua'] for case in self.cases],))
        actual = dict(self.cursor.fetchall())
        for case in self.cases:
            self.assertEqual(actual[case['ua']], case['type'], case['ua'] or '(пустой user-agent)')


def _route(name, namespace):
    module = source_cache.parse(BOT_PATH.read_text(encoding='utf-8-sig'))
    node = copy.deepcopy(next(n for n in module.body
                              if isinstance(n, ast.FunctionDef) and n.name == name))
    node.decorator_list = []
    scope = dict(namespace)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 str(BOT_PATH), 'exec'), scope)
    return scope[name]


class _RouteDB:
    ACTIVE_SESSION_ROLE_FILTERS = SessionsApi.ACTIVE_SESSION_ROLE_FILTERS
    ACTIVE_SESSION_DEVICE_FILTERS = SessionsApi.ACTIVE_SESSION_DEVICE_FILTERS
    ACTIVE_SESSION_SORT_KEYS = SessionsApi.ACTIVE_SESSION_SORT_KEYS

    def __init__(self):
        self.calls = []

    def get_user(self, id):  # noqa: A002 — сигнатура монолита
        return (id, None, 'Админ', 'admin', None, None, None)

    def get_active_sessions_page(self, **kwargs):
        self.calls.append(kwargs)
        return {
            'sessions': [],
            'summary': {'total_sessions': 0, 'total_users': 0,
                        'role_counts': {}, 'device_counts': {}},
            'matched_sessions': 0,
            'has_more': False,
        }


def _status(result):
    return result[1] if isinstance(result, tuple) else 200


class ListRouteFilterValidationTests(unittest.TestCase):
    """Ручка не пускает в SQL то, чего нет в белом списке."""

    def _call(self, args):
        fake_db = _RouteDB()
        route = _route('list_admin_sessions', {
            'db': fake_db,
            'g': SimpleNamespace(user_id=1),
            'request': SimpleNamespace(args=args),
            'jsonify': lambda payload: payload,
            'logging': logging,
            '_is_admin_role': lambda role: role in ('admin', 'super_admin'),
            '_current_session_id_from_access_token': lambda: None,
            '_serialize_admin_session': lambda item, current=None: item,
        })
        return route(), fake_db

    def test_known_filters_reach_the_query(self):
        result, fake_db = self._call({'role': 'sv', 'device': 'mobile',
                                      'sort': 'user_name', 'dir': 'asc'})
        self.assertEqual(_status(result), 200)
        self.assertEqual(fake_db.calls[0]['role'], 'sv')
        self.assertEqual(fake_db.calls[0]['device'], 'mobile')
        self.assertEqual(fake_db.calls[0]['sort_key'], 'user_name')
        self.assertEqual(fake_db.calls[0]['sort_dir'], 'asc')

    def test_all_means_no_filter(self):
        _, fake_db = self._call({'role': 'all', 'device': 'all'})
        self.assertIsNone(fake_db.calls[0]['role'])
        self.assertIsNone(fake_db.calls[0]['device'])

    def test_unknown_role_is_rejected(self):
        result, fake_db = self._call({'role': 'trainer'})
        self.assertEqual(_status(result), 400)
        self.assertEqual(fake_db.calls, [])

    def test_unknown_sort_key_is_rejected(self):
        result, fake_db = self._call({'sort': 'refresh_token_hash'})
        self.assertEqual(_status(result), 400)
        self.assertEqual(fake_db.calls, [])

    def test_unknown_direction_is_rejected(self):
        result, _ = self._call({'dir': 'наверх'})
        self.assertEqual(_status(result), 400)

    def test_pagination_limits_are_kept(self):
        self.assertEqual(_status(self._call({'limit': '9000'})[0]), 400)
        self.assertEqual(_status(self._call({'offset': '-1'})[0]), 400)


class SensitiveAccessAuditTests(unittest.TestCase):
    """Кто выдал доступ — теперь записывается, а не только уходит в Telegram."""

    def test_grant_writes_the_actor_and_the_journal(self):
        recorded = []

        class _Cursor:
            def execute(self, sql, params=None):
                recorded.append((' '.join(sql.split()), params))

            def fetchone(self):
                return ('sid',) if 'UPDATE user_sessions' in recorded[-1][0] else None

        import contextlib

        class _Api(SessionsApi):
            def _get_cursor(self):
                @contextlib.contextmanager
                def ctx():
                    yield _Cursor()
                return ctx()

        ok = _Api().set_session_sensitive_access(
            'sid', 7, True, actor_id=3, actor_role='sv',
            ip_address='10.0.0.3', user_agent='Chrome')

        self.assertTrue(ok)
        update_sql, update_params = recorded[0]
        self.assertIn('sensitive_data_unlocked_by', update_sql)
        self.assertIn(3, update_params)
        insert_sql, insert_params = recorded[1]
        self.assertIn('INSERT INTO session_access_events', insert_sql)
        self.assertEqual(insert_params, ('sid', 7, 'granted', 3, 'sv', '10.0.0.3', 'Chrome'))

    def test_nothing_is_journalled_when_the_session_did_not_change(self):
        """Запись в журнале обязана означать, что доступ реально переключился."""
        recorded = []

        class _Cursor:
            def execute(self, sql, params=None):
                recorded.append(' '.join(sql.split()))

            def fetchone(self):
                return None

        import contextlib

        class _Api(SessionsApi):
            def _get_cursor(self):
                @contextlib.contextmanager
                def ctx():
                    yield _Cursor()
                return ctx()

        self.assertFalse(_Api().set_session_sensitive_access('sid', 7, True, actor_id=3))
        self.assertEqual(len(recorded), 1)
        self.assertNotIn('session_access_events', recorded[0])

    def test_revoke_clears_the_granter(self):
        recorded = []

        class _Cursor:
            def execute(self, sql, params=None):
                recorded.append((' '.join(sql.split()), params))

            def fetchone(self):
                return ('sid',)

        import contextlib

        class _Api(SessionsApi):
            def _get_cursor(self):
                @contextlib.contextmanager
                def ctx():
                    yield _Cursor()
                return ctx()

        _Api().set_session_sensitive_access('sid', 7, False, actor_id=7, actor_role='operator')
        self.assertEqual(recorded[1][1][2], 'revoked')


if __name__ == '__main__':
    unittest.main()
