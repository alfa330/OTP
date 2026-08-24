# -*- coding: utf-8 -*-
"""Раздел «Сессии»: список по людям, фильтры и журнал выдачи доступа.

Задача #238. Раздел показывает по строке на СОТРУДНИКА, а не на сессию: у
одного человека живых сессий бывает четыре десятка, и список из них ничего не
сообщал — одно имя занимало весь экран. Сессии переехали внутрь его карточки,
и там же видно, кому открыт доступ к чувствительным данным, кем и когда.

Отдельно сторожатся вещи, которые ломаются молча:

  * плашки считаются по поиску, но БЕЗ выбранного фильтра — иначе, нажав
    «Админы», пользователь увидит нули у остальных и не сможет выйти обратно;
  * фильтр устройства применяется к ЧЕЛОВЕКУ («есть живая сессия с телефона»),
    а не к отдельной сессии: строка списка не может исчезнуть наполовину;
  * поиск по IP или ID сессии обязан возвращать человека ЦЕЛИКОМ, со всеми его
    сессиями, иначе счётчик в строке врёт;
  * белые списки. `sort`, `role` и `device` попадают в SQL как имя колонки и
    значения, поэтому берутся ТОЛЬКО из словарей класса;
  * разбор user-agent живёт в SQL, а вторая копия на клиенте только рисует.
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

_DB_METHODS = {
    '_active_session_device_sql', '_live_sessions_cte', '_live_search_predicate',
    '_people_row', '_people_filter_sql', '_people_order_by', '_summary_from_row',
    'get_active_session_people_page', 'get_active_session_user_detail',
    'list_active_session_ids_for_user', 'list_active_session_ids_for_users', 'set_session_sensitive_access',
    'list_session_access_events',
}
_DB_CONSTANT_PREFIXES = ('_UA_', 'ACTIVE_SESSION', '_PEOPLE', '_ROLE_FILTER', '_SESSION_COLUMNS', '_SUMMARY')


def _sessions_api():
    """Методы раздела из `database.py` без подъёма пула к боевой базе."""
    module = source_cache.parse(DB_PATH.read_text(encoding='utf-8-sig'))
    cls = next(n for n in module.body
               if isinstance(n, ast.ClassDef) and n.name == 'Database')
    body = [
        node for node in cls.body
        if (isinstance(node, ast.Assign)
            and any(getattr(t, 'id', '').startswith(_DB_CONSTANT_PREFIXES) for t in node.targets))
        or (isinstance(node, ast.FunctionDef) and node.name in _DB_METHODS)
    ]
    stub = ast.ClassDef(name='SessionsApi', bases=[], keywords=[],
                        body=copy.deepcopy(body), decorator_list=[])
    namespace = {'Optional': typing.Optional, 'List': typing.List, 'Any': typing.Any}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[stub], type_ignores=[])),
                 str(DB_PATH), 'exec'), namespace)
    return namespace['SessionsApi']


SessionsApi = _sessions_api()


class SearchPredicateTests(unittest.TestCase):
    """Поиск идёт и по человеку, и по любой его сессии."""

    def test_empty_search_adds_nothing(self):
        sql, params = SessionsApi._live_search_predicate('   ')
        self.assertEqual((sql, params), ('', []))

    def test_search_covers_person_and_session_columns(self):
        sql, params = SessionsApi._live_search_predicate('  ай  ')
        self.assertEqual(params, ['%ай%'] * 6)
        for column in ('session_id', 'ip_address', 'user_agent',
                       'user_name', 'user_login', 'supervisor_name'):
            self.assertIn(column, sql)

    def test_pattern_is_a_value_not_sql(self):
        _, params = SessionsApi._live_search_predicate("'; DROP TABLE users--")
        self.assertEqual(params[0], "%'; DROP TABLE users--%")


class LiveCteMaterializationTests(unittest.TestCase):
    """MATERIALIZED нужен списку и вреден карточке одного человека.

    Списку без него Postgres встраивает CTE, и разбор user-agent пересчитывается
    под каждый счётчик устройства. Карточке, наоборот, он запрещает пробросить
    `WHERE user_id = %s` внутрь CTE: вместо попадания по `idx_user_sessions_user_id`
    строится весь набор живых сессий ради трёх строк (замер на боевой базе:
    32 мс против 2 мс).
    """

    def setUp(self):
        self.api = SessionsApi()

    def test_list_cte_is_materialized(self):
        self.assertIn('live AS MATERIALIZED (', self.api._live_sessions_cte())

    def test_card_cte_is_not_materialized(self):
        cte = self.api._live_sessions_cte(materialized=False)
        self.assertIn('live AS (', cte)
        self.assertNotIn('MATERIALIZED', cte)

    def test_both_variants_select_the_same_columns(self):
        strip = lambda text: text.replace('MATERIALIZED ', '')
        self.assertEqual(strip(self.api._live_sessions_cte()),
                         self.api._live_sessions_cte(materialized=False))


class PeopleFilterTests(unittest.TestCase):
    """Фильтры применяются к человеку, а не к отдельной сессии."""

    def setUp(self):
        self.api = SessionsApi()

    def test_no_filters(self):
        sql, params = self.api._people_filter_sql()
        self.assertEqual((sql, params), ('TRUE', []))

    def test_admin_filter_includes_super_admin(self):
        _, params = self.api._people_filter_sql(role='admin')
        self.assertEqual(params, [['admin', 'super_admin']])

    def test_supervisor_filter_covers_both_spellings(self):
        _, params = self.api._people_filter_sql(role='sv')
        self.assertEqual(params, [['sv', 'supervisor']])

    def test_unknown_role_is_ignored_not_injected(self):
        sql, params = self.api._people_filter_sql(role="admin'; DROP TABLE users--")
        self.assertEqual((sql, params), ('TRUE', []))
        self.assertNotIn('DROP TABLE', sql)

    def test_device_filter_asks_for_at_least_one_such_session(self):
        sql, params = self.api._people_filter_sql(device='mobile')
        self.assertEqual(sql, 'p.mobile_count > 0')
        self.assertEqual(params, [], 'имя колонки — не параметр, оно из белого списка')

    def test_unknown_device_is_ignored(self):
        sql, _ = self.api._people_filter_sql(device='watch')
        self.assertEqual(sql, 'TRUE')

    def test_every_whitelisted_device_maps_to_a_counter(self):
        for device in SessionsApi.ACTIVE_SESSION_DEVICE_FILTERS:
            sql, _ = self.api._people_filter_sql(device=device)
            self.assertEqual(sql, f'p.{device}_count > 0', device)

    def test_filters_stack(self):
        sql, params = self.api._people_filter_sql(role='operator', device='desktop')
        self.assertIn('p.user_role = ANY(%s)', sql)
        self.assertIn('p.desktop_count > 0', sql)
        self.assertEqual(params, [['operator']])


class OrderByTests(unittest.TestCase):
    """Сортировка — имя колонки в тексте запроса, поэтому только из словаря."""

    def setUp(self):
        self.api = SessionsApi()

    def test_default_is_freshest_activity_first(self):
        self.assertIn('p.last_seen_at DESC', self.api._people_order_by())

    def test_unknown_key_falls_back_to_default(self):
        sql = self.api._people_order_by('u.name; DROP TABLE users--', 'asc')
        self.assertIn('p.last_seen_at DESC', sql)
        self.assertNotIn('DROP TABLE', sql)

    def test_unknown_direction_falls_back_to_desc(self):
        self.assertIn('DESC', self.api._people_order_by('user_name', 'вверх'))

    def test_every_whitelisted_key_maps_to_a_column(self):
        for key, column in SessionsApi.ACTIVE_SESSION_SORT_KEYS.items():
            self.assertIn(column, self.api._people_order_by(key, 'asc'), key)

    def test_order_is_stable_between_pages(self):
        """Без второго ключа люди с одинаковым значением прыгают при догрузке."""
        self.assertTrue(self.api._people_order_by().rstrip().endswith('p.user_id ASC'))


# ── Синтетические данные для боевого SQL ────────────────────────────────────
# id, имя, логин, роль, supervisor_id, department_id
USER_FIXTURES = [
    (1, 'Ядигаров Руслан', 'ruslan', 'super_admin', None, 1),
    (2, 'Хайрихан Шерзад', 'sherzad', 'admin', None, 1),
    (3, 'Супервайзер Айгуль', 'aigul', 'sv', None, 1),
    (4, 'Оператор Асель', 'asel', 'operator', 3, 1),
    (5, 'Оператор Бота', 'bota', 'operator', 3, 2),
    (6, 'Без сессий Ерлан', 'erlan', 'operator', 3, 2),
]

# session_id, user_id, ua, ip, минут с активности, срок жизни в днях, отозвана, доступ открыт, кем
SESSION_FIXTURES = [
    ('11111111-1111-1111-1111-111111111111', 1,
     'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/126 Safari/537.36', '10.0.0.1', 1, 10, False, False, None),
    ('22222222-2222-2222-2222-222222222222', 2,
     'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125', '10.0.0.2', 5, 10, False, False, None),
    ('33333333-3333-3333-3333-333333333333', 3,
     'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148 Safari', '10.0.0.3', 2, 10, False, False, None),
    ('44444444-4444-4444-4444-444444444444', 4,
     'Mozilla/5.0 (Linux; Android 13; SM-A536E) Mobile Chrome/124', '10.0.0.4', 9, 10, False, True, 3),
    ('55555555-5555-5555-5555-555555555555', 5,
     'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) Safari', '10.0.0.5', 3, 10, False, False, None),
    ('66666666-6666-6666-6666-666666666666', 5, '', '10.0.0.6', 4, 10, False, False, None),
    ('77777777-7777-7777-7777-777777777777', 4,
     'Googlebot/2.1 (+http://www.google.com/bot.html)', '10.0.0.7', 6, 10, False, False, None),
    ('88888888-8888-8888-8888-888888888888', 4,
     'Mozilla/5.0 (Windows NT 10.0) Chrome/120', '10.0.0.8', 7, -1, False, False, None),   # истекла
    ('99999999-9999-9999-9999-999999999999', 4,
     'Mozilla/5.0 (Windows NT 10.0) Chrome/120', '10.0.0.9', 8, 10, True, False, None),    # прервана
    ('aaaaaaaa-0000-0000-0000-00000000000a', 4,
     'Mozilla/5.0 (Windows NT 10.0) Chrome/126', '10.0.0.4', 10, 10, False, False, None),  # тот же IP, что у 4444
]


def _lit(value):
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
        '({}, {}, {}, {}, {}, {})'.format(uid, _lit(name), _lit(login), _lit(role), _lit(sv), _lit(dep))
        for uid, name, login, role, sv, dep in USER_FIXTURES
    )
    sessions = ', '.join(
        "({}::uuid, {}, {}, {}, (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - make_interval(mins => {}),"
        " (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - make_interval(days => 30),"
        " (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + make_interval(days => {}), {}, {}, {}, {})".format(
            _lit(sid), uid, _lit(ua), _lit(ip), minutes, days,
            "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')" if revoked else 'NULL::timestamp',
            _lit(unlocked),
            "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')" if unlocked else 'NULL::timestamp',
            _lit(by))
        for sid, uid, ua, ip, minutes, days, revoked, unlocked, by in SESSION_FIXTURES
    )
    return textwrap.dedent(f"""
        WITH users AS (
            SELECT id, name, login, role, supervisor_id::int AS supervisor_id,
                   department_id::int AS department_id,
                   NULL::text AS avatar_bucket, NULL::text AS avatar_blob_path,
                   'working'::text AS status, NULL::bigint AS telegram_id
              FROM (VALUES {users}) AS t(id, name, login, role, supervisor_id, department_id)
        ),
        departments AS (
            SELECT * FROM (VALUES (1, 'СЗоВ'), (2, 'ОП')) AS t(id, name)
        ),
        user_sessions AS (
            SELECT session_id, user_id, user_agent, ip_address, last_seen_at, created_at,
                   expires_at, revoked_at, sensitive_data_unlocked,
                   sensitive_data_unlocked_at, sensitive_data_unlocked_by
              FROM (VALUES {sessions})
                   AS t(session_id, user_id, user_agent, ip_address, last_seen_at, created_at,
                        expires_at, revoked_at, sensitive_data_unlocked,
                        sensitive_data_unlocked_at, sensitive_data_unlocked_by)
        ),
        session_access_events AS (
            SELECT * FROM (VALUES
                (1, '44444444-4444-4444-4444-444444444444'::uuid, 4, 'granted', 3, 'sv', '10.0.0.99',
                 (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'))
            ) AS t(id, session_id, user_id, action, actor_id, actor_role, ip_address, created_at)
        ),
    """).strip()


class _StubCursor:
    """Курсор, подменяющий боевые таблицы синтетическими данными."""

    def __init__(self, cursor):
        self._cursor = cursor
        self._stub = _stub_cte()

    def execute(self, sql, params=None):
        text = sql.lstrip()
        if text.upper().startswith('WITH'):
            text = self._stub + ' ' + text[len('WITH'):].lstrip()
        else:
            text = self._stub.rstrip(',') + '\n' + text
        return self._cursor.execute(text, params)

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchone(self):
        return self._cursor.fetchone()


def _api_on(cursor):
    import contextlib

    class _Api(SessionsApi):
        def _get_cursor(self):
            @contextlib.contextmanager
            def ctx():
                yield cursor
            return ctx()

    return _Api()


class PeoplePageSqlTests(unittest.TestCase):
    """Боевой SQL на синтетических данных."""

    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.raw = prod_db.connection().cursor()
        cls.api = _api_on(_StubCursor(cls.raw))

    def tearDown(self):
        prod_db.rollback()

    def test_one_row_per_person_not_per_session(self):
        page = self.api.get_active_session_people_page(limit=50)
        self.assertEqual(page['matched_people'], 5, 'Ерлан без сессий в списке не появляется')
        self.assertEqual(page['matched_sessions'], 8)
        self.assertEqual(len({p['user_id'] for p in page['people']}), 5)

    def test_expired_and_revoked_sessions_are_not_counted(self):
        page = self.api.get_active_session_people_page(limit=50)
        asel = next(p for p in page['people'] if p['user_id'] == 4)
        self.assertEqual(asel['sessions_count'], 3, 'истёкшая и прерванная не в счёт')

    def test_devices_and_addresses_are_summed_per_person(self):
        page = self.api.get_active_session_people_page(limit=50)
        asel = next(p for p in page['people'] if p['user_id'] == 4)
        self.assertEqual(asel['device_counts'],
                         {'desktop': 1, 'mobile': 1, 'tablet': 0, 'bot': 1, 'unknown': 0})
        self.assertEqual(asel['ip_count'], 2, 'две сессии с одного адреса — один адрес')

    def test_role_filter_narrows_the_list_but_not_the_tiles(self):
        """Плашки продолжают считать всех — иначе из фильтра не выйти."""
        page = self.api.get_active_session_people_page(limit=50, role='operator')
        self.assertEqual({p['user_role'] for p in page['people']}, {'operator'})
        self.assertEqual(page['matched_people'], 2)
        self.assertEqual(page['matched_sessions'], 5)
        self.assertEqual(page['summary']['total_people'], 5)
        self.assertEqual(page['summary']['role_counts'], {'admin': 2, 'sv': 1, 'operator': 2})

    def test_device_filter_means_person_has_such_a_session(self):
        page = self.api.get_active_session_people_page(limit=50)
        for device, expected in page['summary']['device_counts'].items():
            filtered = self.api.get_active_session_people_page(limit=50, device=device)
            self.assertEqual(filtered['matched_people'], expected, device)
            for person in filtered['people']:
                self.assertGreater(person['device_counts'][device], 0, device)

    def test_person_with_a_bot_session_keeps_all_his_sessions(self):
        """Фильтр отбирает людей, а не режет их сессии."""
        page = self.api.get_active_session_people_page(limit=50, device='bot')
        self.assertEqual([p['user_id'] for p in page['people']], [4])
        self.assertEqual(page['people'][0]['sessions_count'], 3)

    def test_search_by_ip_returns_the_whole_person(self):
        page = self.api.get_active_session_people_page(limit=50, search='10.0.0.3')
        self.assertEqual([p['user_name'] for p in page['people']], ['Супервайзер Айгуль'])
        self.assertEqual(page['people'][0]['sessions_count'], 1)

    def test_search_by_session_id_finds_its_owner(self):
        page = self.api.get_active_session_people_page(limit=50, search='44444444')
        self.assertEqual([p['user_id'] for p in page['people']], [4])
        self.assertEqual(page['people'][0]['sessions_count'], 3,
                         'нашли по одной сессии — показываем человека целиком')

    def test_search_by_supervisor_name(self):
        page = self.api.get_active_session_people_page(limit=50, search='Айгуль')
        self.assertEqual({p['user_id'] for p in page['people']}, {3, 4, 5})
        self.assertEqual(page['matched_sessions'], 6, 'у найденных людей считаются ВСЕ сессии')

    def test_search_narrows_the_tiles_too(self):
        page = self.api.get_active_session_people_page(limit=50, search='Айгуль')
        self.assertEqual(page['summary']['total_people'], 3)
        self.assertEqual(page['summary']['role_counts'], {'admin': 0, 'sv': 1, 'operator': 2})

    def test_filters_stack_with_search(self):
        page = self.api.get_active_session_people_page(limit=50, search='Айгуль', role='operator')
        self.assertEqual(page['matched_people'], 2)
        self.assertEqual(page['summary']['total_people'], 3)

    def test_pagination_counts_people_and_pages_do_not_overlap(self):
        first = self.api.get_active_session_people_page(limit=2)
        second = self.api.get_active_session_people_page(limit=2, offset=2)
        third = self.api.get_active_session_people_page(limit=2, offset=4)
        self.assertTrue(first['has_more'])
        self.assertTrue(second['has_more'])
        self.assertFalse(third['has_more'])
        seen = [p['user_id'] for p in first['people'] + second['people'] + third['people']]
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), 5)

    def test_page_beyond_the_end_still_reports_the_real_count(self):
        """Догрузка за хвост не имеет права обнулять «сколько всего нашли».

        Список догружается по offset, а выборка тем временем усыхает: сессии
        истекают, соседний админ прерывает пачку. Если счётчик считать оконной
        функцией по строкам страницы, пустой хвост молча даёт ноль, и подвал
        пишет «0 сотрудников» под полусотней видимых строк.
        """
        page = self.api.get_active_session_people_page(limit=2, offset=100)
        self.assertEqual(page['people'], [])
        self.assertEqual(page['matched_people'], 5)
        self.assertEqual(page['matched_sessions'], 8)
        self.assertEqual(page['summary']['total_people'], 5)
        self.assertFalse(page['has_more'])

    def test_page_beyond_the_end_keeps_the_filter_in_the_count(self):
        page = self.api.get_active_session_people_page(limit=2, offset=100, role='operator')
        self.assertEqual(page['people'], [])
        self.assertEqual(page['matched_people'], 2)
        self.assertEqual(page['summary']['total_people'], 5, 'плашки по-прежнему по всем')

    def test_filter_without_matches_keeps_the_tiles_alive(self):
        """Из фильтра, не давшего ни одной строки, должно быть чем выйти."""
        page = self.api.get_active_session_people_page(limit=50, search='Ядигаров', device='bot')
        self.assertEqual(page['people'], [])
        self.assertEqual(page['matched_people'], 0)
        self.assertEqual(page['summary']['total_people'], 1)
        self.assertEqual(page['summary']['role_counts']['admin'], 1)

    def test_empty_result_is_zero_not_stale(self):
        page = self.api.get_active_session_people_page(limit=50, search='нет-такого-человека')
        self.assertEqual((page['matched_people'], page['matched_sessions']), (0, 0))
        self.assertFalse(page['has_more'])
        self.assertEqual(page['people'], [])

    def test_sorting_is_done_by_the_server(self):
        asc = self.api.get_active_session_people_page(limit=50, sort_key='user_name', sort_dir='asc')
        names = [p['user_name'] for p in asc['people']]
        self.assertEqual(names, sorted(names))
        desc = self.api.get_active_session_people_page(limit=50, sort_key='user_name', sort_dir='desc')
        self.assertEqual([p['user_name'] for p in desc['people']], sorted(names, reverse=True))

    def test_sorting_by_session_count(self):
        page = self.api.get_active_session_people_page(limit=50, sort_key='sessions_count', sort_dir='desc')
        counts = [p['sessions_count'] for p in page['people']]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertEqual(page['people'][0]['user_id'], 4)

    def test_open_access_and_its_granter_are_in_the_list_row(self):
        """Главный вопрос раздела виден сразу, без открытия карточки."""
        page = self.api.get_active_session_people_page(limit=50)
        asel = next(p for p in page['people'] if p['user_id'] == 4)
        self.assertEqual(asel['sensitive_open_count'], 1)
        self.assertEqual(asel['sensitive_last_granted_by_name'], 'Супервайзер Айгуль')
        self.assertEqual(asel['sensitive_last_granted_by_role'], 'sv')
        self.assertIsNotNone(asel['sensitive_last_granted_at'])

        others = [p for p in page['people'] if p['user_id'] != 4]
        self.assertTrue(all(p['sensitive_open_count'] == 0 for p in others))
        self.assertEqual(page['summary']['sensitive_people'], 1)
        self.assertEqual(page['summary']['sensitive_sessions'], 1)


class UserDetailSqlTests(unittest.TestCase):
    """Карточка человека: все его живые сессии и общий журнал."""

    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.raw = prod_db.connection().cursor()
        cls.api = _api_on(_StubCursor(cls.raw))

    def tearDown(self):
        prod_db.rollback()

    def test_card_query_lets_the_index_do_the_work(self):
        """Карточка обязана искать по user_id, а не строить весь набор живых сессий."""
        self.raw.execute(
            f"""EXPLAIN WITH {self.api._live_sessions_cte(materialized=False)}
                SELECT session_id FROM live WHERE user_id = %s""", (4,))
        plan = ' '.join(row[0] for row in self.raw.fetchall())
        self.assertNotIn('CTE Scan on live', plan,
                         'MATERIALIZED здесь запрещает пробросить предикат внутрь CTE')

    def test_card_gathers_all_live_sessions_of_the_person(self):
        detail = self.api.get_active_session_user_detail(4)
        self.assertEqual(detail['user']['user_name'], 'Оператор Асель')
        self.assertEqual(detail['user']['department_name'], 'СЗоВ')
        self.assertEqual(detail['user']['supervisor_name'], 'Супервайзер Айгуль')
        self.assertEqual(len(detail['sessions']), 3)
        self.assertEqual(detail['user']['active_sessions'], 3)
        self.assertEqual(detail['user']['total_sessions'], 5, 'счётчик «за всё время» видит и мёртвые')

    def test_card_says_who_opened_the_data_and_when(self):
        detail = self.api.get_active_session_user_detail(4)
        opened = [s for s in detail['sessions'] if s['sensitive_data_unlocked']]
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0]['sensitive_data_unlocked_by_name'], 'Супервайзер Айгуль')
        self.assertEqual(opened[0]['sensitive_data_unlocked_by_role'], 'sv')
        self.assertIsNotNone(opened[0]['sensitive_data_unlocked_at'])

    def test_journal_is_attached_to_its_own_session(self):
        detail = self.api.get_active_session_user_detail(4)
        self.assertEqual(len(detail['access_events']), 1)
        with_events = [s for s in detail['sessions'] if s['access_events']]
        self.assertEqual(len(with_events), 1)
        self.assertEqual(with_events[0]['session_id'], '44444444-4444-4444-4444-444444444444')
        self.assertEqual(with_events[0]['access_events'][0]['actor_name'], 'Супервайзер Айгуль')

    def test_person_without_sessions_still_opens(self):
        detail = self.api.get_active_session_user_detail(6)
        self.assertEqual(detail['user']['user_name'], 'Без сессий Ерлан')
        self.assertEqual(detail['sessions'], [])
        self.assertEqual(detail['user']['active_sessions'], 0)

    def test_unknown_user_and_garbage_id(self):
        self.assertIsNone(self.api.get_active_session_user_detail(999))
        self.assertIsNone(self.api.get_active_session_user_detail('мусор'))
        self.assertIsNone(self.api.get_active_session_user_detail(None))

    def test_revoke_all_takes_only_live_sessions(self):
        self.assertEqual(len(self.api.list_active_session_ids_for_user(4)), 3)
        self.assertEqual(self.api.list_active_session_ids_for_user('мусор'), [])

    def test_bulk_revoke_collects_sessions_of_everyone_at_once(self):
        """Пачка собирается ОДНИМ запросом, а не циклом по человеку.

        Цикл «по человеку за запрос» пропускал выбранных, которых не было в
        загруженной странице, и обрывался на собственном разлогине админа.
        """
        ids = self.api.list_active_session_ids_for_users([4, 5, 6])
        self.assertEqual(len(ids), 5, 'три сессии Асель, две Боты, у Ерлана ни одной')
        self.assertEqual(len(set(ids)), 5)

    def test_bulk_revoke_ignores_garbage_ids(self):
        self.assertEqual(self.api.list_active_session_ids_for_users([]), [])
        self.assertEqual(self.api.list_active_session_ids_for_users(None), [])
        self.assertEqual(self.api.list_active_session_ids_for_users(['мусор', None]), [])
        self.assertEqual(len(self.api.list_active_session_ids_for_users(['4', 'мусор'])), 3,
                         'число строкой — нормальный ввод, мусор рядом не должен ронять запрос')


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
    nodes = []
    for target in (name, '_admin_sessions_guard'):
        node = copy.deepcopy(next(n for n in module.body
                                  if isinstance(n, ast.FunctionDef) and n.name == target))
        node.decorator_list = []
        nodes.append(node)
    scope = dict(namespace)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
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

    def get_active_session_people_page(self, **kwargs):
        self.calls.append(kwargs)
        return {
            'people': [],
            'summary': {'total_people': 0, 'total_sessions': 0,
                        'role_counts': {}, 'device_counts': {}},
            'matched_people': 0,
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
            '_serialize_session_person': lambda item: item,
        })
        return route(), fake_db

    def test_known_filters_reach_the_query(self):
        result, fake_db = self._call({'role': 'sv', 'device': 'mobile',
                                      'sort': 'sessions_count', 'dir': 'asc'})
        self.assertEqual(_status(result), 200)
        self.assertEqual(fake_db.calls[0]['role'], 'sv')
        self.assertEqual(fake_db.calls[0]['device'], 'mobile')
        self.assertEqual(fake_db.calls[0]['sort_key'], 'sessions_count')
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
        self.assertEqual(_status(self._call({'dir': 'наверх'})[0]), 400)

    def test_pagination_limits_are_kept(self):
        self.assertEqual(_status(self._call({'limit': '9000'})[0]), 400)
        self.assertEqual(_status(self._call({'offset': '-1'})[0]), 400)

    def test_section_is_closed_for_non_admins(self):
        fake_db = _RouteDB()
        fake_db.get_user = lambda id: (id, None, 'Оператор', 'operator', None, None, None)
        route = _route('list_admin_sessions', {
            'db': fake_db,
            'g': SimpleNamespace(user_id=7),
            'request': SimpleNamespace(args={}),
            'jsonify': lambda payload: payload,
            'logging': logging,
            '_is_admin_role': lambda role: role in ('admin', 'super_admin'),
            '_serialize_session_person': lambda item: item,
        })
        self.assertEqual(_status(route()), 403)
        self.assertEqual(fake_db.calls, [])


class _RevokeDB(_RouteDB):
    def __init__(self, live_ids=None, current=None):
        super().__init__()
        self.live_ids = list(live_ids or [])
        self.current = current
        self.asked_for = None
        self.revoked = None

    def list_active_session_ids_for_users(self, user_ids):
        self.asked_for = list(user_ids)
        return list(self.live_ids)

    def revoke_user_sessions_bulk(self, session_ids):
        self.revoked = list(session_ids)
        return len(session_ids)


def _revoke_route(name, fake_db, body, current_session=None):
    namespace = {
        'db': fake_db,
        'g': SimpleNamespace(user_id=1),
        'request': SimpleNamespace(method='POST', get_json=lambda silent=False: body, args={}),
        'jsonify': lambda payload: payload,
        'logging': logging,
        '_is_admin_role': lambda role: role in ('admin', 'super_admin'),
        '_current_session_id_from_access_token': lambda: current_session,
        '_clear_auth_cookies': lambda response: None,
        '_build_cors_preflight_response': lambda: ({}, 200),
        'ADMIN_SESSION_BULK_USERS_LIMIT': 200,
    }
    module = source_cache.parse(BOT_PATH.read_text(encoding='utf-8-sig'))
    nodes = []
    for target in (name, '_admin_sessions_guard', '_revoke_sessions_of_users'):
        node = copy.deepcopy(next(n for n in module.body
                                  if isinstance(n, ast.FunctionDef) and n.name == target))
        node.decorator_list = []
        nodes.append(node)
    scope = dict(namespace)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 str(BOT_PATH), 'exec'), scope)
    return scope[name]


class BulkRevokeRouteTests(unittest.TestCase):
    """Прервать сессии пачки людей — один запрос, а не N."""

    def test_all_sessions_of_all_users_go_in_one_update(self):
        fake_db = _RevokeDB(live_ids=['s1', 's2', 's3'])
        route = _revoke_route('revoke_admin_session_users_bulk', fake_db, {'user_ids': [4, 5]})
        result = route()
        self.assertEqual(_status(result), 200)
        self.assertEqual(fake_db.asked_for, [4, 5])
        self.assertEqual(fake_db.revoked, ['s1', 's2', 's3'])
        self.assertEqual(result[0]['revoked_count'], 3)
        self.assertEqual(result[0]['users_count'], 2)

    def test_own_session_in_the_batch_does_not_cut_the_batch_short(self):
        """Админ, отметивший себя, теряет свою сессию ПОСЛЕ остальных.

        Раньше клиент шёл циклом и на своём разлогине выходил из приложения,
        оставив остальных выбранных с живыми сессиями и без единого сообщения.
        """
        fake_db = _RevokeDB(live_ids=['mine', 's2', 's3'], current='mine')
        route = _revoke_route('revoke_admin_session_users_bulk', fake_db,
                              {'user_ids': [1, 4, 5]}, current_session='mine')
        result = route()
        self.assertEqual(fake_db.revoked, ['mine', 's2', 's3'], 'прерваны все, одним заходом')
        self.assertTrue(result[0]['current_session_revoked'])

    def test_nothing_to_revoke_is_success_not_error(self):
        fake_db = _RevokeDB(live_ids=[])
        route = _revoke_route('revoke_admin_session_users_bulk', fake_db, {'user_ids': [4]})
        result = route()
        self.assertEqual(_status(result), 200)
        self.assertEqual(result[0]['revoked_count'], 0)
        self.assertIsNone(fake_db.revoked, 'пустой UPDATE не отправляем')

    def test_empty_and_oversized_payload_are_rejected(self):
        for body in ({}, {'user_ids': []}, {'user_ids': 'все'}):
            fake_db = _RevokeDB(live_ids=['s1'])
            route = _revoke_route('revoke_admin_session_users_bulk', fake_db, body)
            self.assertEqual(_status(route()), 400, body)
            self.assertIsNone(fake_db.revoked)
        fake_db = _RevokeDB(live_ids=['s1'])
        route = _revoke_route('revoke_admin_session_users_bulk', fake_db,
                              {'user_ids': list(range(201))})
        self.assertEqual(_status(route()), 400)
        self.assertIsNone(fake_db.revoked)

    def test_single_user_route_shares_the_same_path(self):
        fake_db = _RevokeDB(live_ids=['s1', 's2'])
        route = _revoke_route('revoke_admin_session_user', fake_db, {})
        result = route(user_id=4)
        self.assertEqual(_status(result), 200)
        self.assertEqual(fake_db.asked_for, [4])
        self.assertEqual(result[0]['revoked_count'], 2)

    def test_bulk_revoke_is_closed_for_non_admins(self):
        fake_db = _RevokeDB(live_ids=['s1'])
        fake_db.get_user = lambda id: (id, None, 'Оператор', 'operator', None, None, None)
        route = _revoke_route('revoke_admin_session_users_bulk', fake_db, {'user_ids': [4]})
        self.assertEqual(_status(route()), 403)
        self.assertIsNone(fake_db.revoked)


class SensitiveAccessAuditTests(unittest.TestCase):
    """Кто выдал доступ — теперь записывается, а не только уходит в Telegram."""

    @staticmethod
    def _api(recorded, updated=True):
        import contextlib

        class _Cursor:
            def execute(self, sql, params=None):
                recorded.append((' '.join(sql.split()), params))

            def fetchone(self):
                return ('sid',) if updated else None

        class _Api(SessionsApi):
            def _get_cursor(self):
                @contextlib.contextmanager
                def ctx():
                    yield _Cursor()
                return ctx()

        return _Api()

    def test_grant_writes_the_actor_and_the_journal(self):
        recorded = []
        ok = self._api(recorded).set_session_sensitive_access(
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
        self.assertFalse(
            self._api(recorded, updated=False).set_session_sensitive_access('sid', 7, True, actor_id=3))
        self.assertEqual(len(recorded), 1)
        self.assertNotIn('session_access_events', recorded[0][0])

    def test_revoke_clears_the_granter(self):
        recorded = []
        self._api(recorded).set_session_sensitive_access(
            'sid', 7, False, actor_id=7, actor_role='operator')
        self.assertEqual(recorded[1][1][2], 'revoked')
        self.assertIn('ELSE NULL', recorded[0][0])


if __name__ == '__main__':
    unittest.main()
