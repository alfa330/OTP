# -*- coding: utf-8 -*-
"""«Реестр тестовых номеров»: ключ номера, ввод, права, ручки и проводка раздела.

Что сторожится:
  * ключ — последние десять ASCII-цифр, одинаковый в Python, SQL и T-SQL;
    короче десяти цифр — ключа нет (внутренний номер не станет тестовым);
  * исключение в SQL — NOT EXISTS: строка без номера из расчёта не выпадает;
  * ключи едут в запрос к Oktell литералами только в виде ровно десяти цифр;
  * кеш ключей для живых источников сбрасывается правкой реестра, а лежащая
    база не роняет табло;
  * реестр ведут админы и главы отделов (решение владельца 09.10.2026);
  * ручки сначала проверяют всё и только потом пишут; дубль номера — 409 с
    тем, кому номер уже принадлежит;
  * раздел подключён: схема без SAVEPOINT, Blueprint, пункт меню в двух ветках.

Номера в тестах выдуманные (+7 700 000 01 0X): реальных в репозитории быть не должно.
"""

import sys
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from test_numbers import access, activity, keys, queries, routes, rules, schema  # noqa: E402

APP_JSX = ROOT / 'src' / 'App.jsx'
DATABASE_PY = ROOT / 'database.py'
BOT_PY = ROOT / 'bot_schedule2.py'


def _read(path):
    return path.read_text(encoding='utf-8-sig')


def person(role='operator', department_code='szov', headed_codes=(), user_id=10):
    return {
        'user_id': user_id, 'name': 'Сотрудник %d' % user_id, 'role': role,
        'department_id': 909, 'department_code': department_code, 'city': 'Алматы',
        'headed_department_ids': [909] if headed_codes else [],
        'headed_department_codes': list(headed_codes),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Ключ номера
# ─────────────────────────────────────────────────────────────────────────────

class PhoneKeyTests(unittest.TestCase):

    def test_every_kazakh_spelling_is_one_key(self):
        for raw in ('+7 700 000 01 01', '87000000101', '77000000101', '7000000101',
                    '+7 (700) 000-01-01', '8 700 000 01 01'):
            with self.subTest(raw=raw):
                self.assertEqual(keys.phone_key(raw), '7000000101')

    def test_sources_with_prefixes_give_the_same_key(self):
        """FreePBX пишет «+7…», Oktell и Binotel — «7…», Chat2Desk — иногда «8…»."""
        self.assertEqual(keys.phone_key('+77000000101'), keys.phone_key('7000000101'))

    def test_short_numbers_have_no_key(self):
        for raw in (None, '', '6024', '3010', '123456789', 'abc'):
            with self.subTest(raw=raw):
                self.assertIsNone(keys.phone_key(raw))

    def test_only_ascii_digits_count(self):
        """`\\d` в Python понимает и арабские цифры — SQL их не повторит."""
        self.assertIsNone(keys.phone_key('٧٧٠٠٠٠٠٠١٠١'))
        self.assertEqual(keys.phone_key('7٧7000000101'), '7000000101')

    def test_is_test_phone(self):
        registry = frozenset({'7000000101'})
        self.assertTrue(keys.is_test_phone('+7 700 000 01 01', registry))
        self.assertFalse(keys.is_test_phone('+7 700 000 01 02', registry))
        self.assertFalse(keys.is_test_phone(None, registry))
        self.assertFalse(keys.is_test_phone('+7 700 000 01 01', frozenset()))


class SqlFragmentTests(unittest.TestCase):

    def test_exclusion_is_not_exists(self):
        """NOT IN по колонке с NULL выбросил бы строки без номера (Telegram, 2ГИС)."""
        fragment = keys.sql_not_test('r.client_phone')
        self.assertTrue(fragment.startswith('NOT EXISTS (SELECT 1 FROM test_phone_numbers _tpn'))
        self.assertNotIn('NOT IN', fragment)
        self.assertIn('COALESCE((r.client_phone)::text', fragment)

    def test_digits_variant_skips_regex(self):
        fragment = keys.sql_not_test('t.phone', digits=True)
        self.assertNotIn('REGEXP_REPLACE', fragment)
        self.assertIn('RIGHT(COALESCE((t.phone)::text', fragment)

    def test_regex_has_no_percent(self):
        """psycopg2 читает «%» как место параметра — фрагмент встаёт в запросы с параметрами."""
        for digits in (False, True):
            self.assertNotIn('%', keys.sql_not_test('x', digits=digits))

    def test_tsql_takes_only_ten_digit_keys(self):
        fragment = keys.tsql_not_test('x.[number]', {'7000000101', "1' OR 1=1 --", '12345', None})
        self.assertIn("NOT IN ('7000000101')", fragment)
        self.assertNotIn('OR 1=1', fragment)
        self.assertEqual(keys.tsql_not_test('x.[number]', set()), '')
        self.assertEqual(keys.tsql_not_test('x.[number]', {'abc'}), '')


class CachedKeysTests(unittest.TestCase):

    def setUp(self):
        keys.invalidate_cache()
        self.addCleanup(keys.invalidate_cache)
        self.calls = 0
        self.registry = {'7000000101'}
        self.fail = False

    @contextmanager
    def get_cursor(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError('база недоступна')
        test = self

        class Cursor:
            def execute(self, sql, params=None):
                test.last_sql = sql

            def fetchall(self):
                return [(key,) for key in sorted(test.registry)]

        yield Cursor()

    def test_reads_once_within_the_window(self):
        clock = iter([100.0, 110.0, 129.0]).__next__
        self.assertEqual(keys.cached_keys(self.get_cursor, clock=clock), {'7000000101'})
        self.assertEqual(keys.cached_keys(self.get_cursor, clock=clock), {'7000000101'})
        self.assertEqual(keys.cached_keys(self.get_cursor, clock=clock), {'7000000101'})
        self.assertEqual(self.calls, 1)

    def test_rereads_after_the_window(self):
        clock = iter([100.0, 131.0]).__next__
        keys.cached_keys(self.get_cursor, clock=clock)
        self.registry.add('7000000102')
        self.assertEqual(keys.cached_keys(self.get_cursor, clock=clock), {'7000000101', '7000000102'})
        self.assertEqual(self.calls, 2)

    def test_change_in_the_section_is_seen_at_once(self):
        clock = iter([100.0, 101.0]).__next__
        keys.cached_keys(self.get_cursor, clock=clock)
        self.registry.add('7000000102')
        keys.invalidate_cache()
        self.assertIn('7000000102', keys.cached_keys(self.get_cursor, clock=clock))

    def test_broken_database_keeps_the_last_set(self):
        clock = iter([100.0, 131.0, 140.0]).__next__
        keys.cached_keys(self.get_cursor, clock=clock)
        self.fail = True
        with self.assertLogs(level='ERROR'):
            self.assertEqual(keys.cached_keys(self.get_cursor, clock=clock), {'7000000101'})
        # и не бьётся в лежащую базу на каждом опросе табло
        self.assertEqual(keys.cached_keys(self.get_cursor, clock=clock), {'7000000101'})
        self.assertEqual(self.calls, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Ввод номера
# ─────────────────────────────────────────────────────────────────────────────

class ParsePhoneTests(unittest.TestCase):

    def test_kazakh_number_is_shown_one_way(self):
        for raw in ('8 700 000 01 01', '+7 (700) 000-01-01', '7000000101', '77000000101'):
            with self.subTest(raw=raw):
                self.assertEqual(rules.parse_phone(raw), ('7000000101', '+7 700 000 01 01'))

    def test_foreign_number_keeps_its_country_code(self):
        self.assertEqual(rules.parse_phone('+996 555 000 101'), ('6555000101', '+996555000101'))
        self.assertEqual(rules.parse_phone('8 900 000 01 01'), ('9000000101', '+79000000101'))

    def test_rejects(self):
        for raw, fragment in (('', 'Введите'), ('   ', 'Введите'), ('6024', 'меньше 10'),
                              ('+7 700 000 01 01 доб 5', 'только цифры'),
                              ('7700000010177000000101', 'больше 15')):
            with self.subTest(raw=raw):
                with self.assertRaises(rules.PhoneError) as caught:
                    rules.parse_phone(raw)
                self.assertIn(fragment, str(caught.exception))


# ─────────────────────────────────────────────────────────────────────────────
# Права
# ─────────────────────────────────────────────────────────────────────────────

class AccessTests(unittest.TestCase):

    def test_admins_and_heads_keep_the_registry(self):
        for ctx in (person(role='super_admin', department_code=None),
                    person(role='admin', department_code=None),
                    person(role='admin', headed_codes=('op',)),
                    person(role='sv', department_code='op', headed_codes=('op',)),
                    person(role='operator', department_code='front_office', headed_codes=('front_office',))):
            with self.subTest(ctx=ctx):
                self.assertTrue(access.can_open_section(ctx))
                self.assertTrue(access.can_edit(ctx))

    def test_everyone_else_is_not_let_in(self):
        """СВ не ведёт реестр: номером в реестре можно спрятать звонок своего оператора."""
        for role in ('sv', 'supervisor', 'operator', 'trainee', 'trainer'):
            with self.subTest(role=role):
                self.assertFalse(access.can_open_section(person(role=role)))
        self.assertFalse(access.can_open_section(None))

    def test_frontend_mirror_has_the_same_circle(self):
        app = _read(APP_JSX)
        predicate = app.split('const canAccessTestNumbersSectionForUser = (userLike) => {')[1].split('\n};')[0]
        self.assertIn("role === 'super_admin' || role === 'admin'", predicate)
        self.assertIn('isDepartmentHead(userLike)', predicate)


# ─────────────────────────────────────────────────────────────────────────────
# Ручки
# ─────────────────────────────────────────────────────────────────────────────

class Store:
    """Реестр в памяти вместо SQL — ручки проверяются по порядку проверок и записей."""

    def __init__(self):
        self.rows = {}
        self.people_rows = {5: 'Иванова Айгерим', 6: 'Карабек Ержан'}
        self.writes = []
        self.next_id = 1

    def _row(self, number_id):
        row = self.rows.get(number_id)
        if not row:
            return None
        return {**row, 'owner_name': self.people_rows.get(row['owner_user_id']),
                'owner_department': 'СЗоВ', 'owner_fired': False, 'created_by_name': 'Админ',
                'created_at': datetime(2026, 10, 9, 5, 30, tzinfo=timezone.utc)}

    def list_numbers(self, cursor):
        return [self._row(number_id) for number_id in sorted(self.rows)]

    def get_number(self, cursor, number_id):
        return self._row(int(number_id))

    def find_by_key(self, cursor, phone_key):
        return next((self._row(i) for i, r in self.rows.items() if r['phone_key'] == phone_key), None)

    def people(self, cursor):
        return [{'id': i, 'name': n, 'department_name': 'СЗоВ'} for i, n in self.people_rows.items()]

    def active_person(self, cursor, user_id):
        name = self.people_rows.get(int(user_id))
        return {'id': int(user_id), 'name': name} if name else None

    def add_number(self, cursor, *, phone_key, phone_display, owner, actor):
        self.writes.append(('add', phone_key))
        number_id = self.next_id
        self.next_id += 1
        self.rows[number_id] = {'id': number_id, 'phone_key': phone_key, 'phone_display': phone_display,
                                'owner_user_id': owner['id'], 'created_by': actor['user_id']}
        return self._row(number_id)

    def change_owner(self, cursor, number_id, *, owner, actor):
        self.writes.append(('owner', number_id))
        if number_id not in self.rows:
            return None
        self.rows[number_id]['owner_user_id'] = owner['id']
        return self._row(number_id)

    def remove_number(self, cursor, number_id, *, actor):
        self.writes.append(('remove', number_id))
        row = self._row(number_id)
        self.rows.pop(number_id, None)
        return row


class _RowsCursor:
    """Курсор с колонками: ровно то, что читает activity._rows."""

    def __init__(self, columns, rows):
        self.description = [(name,) for name in columns]
        self.rows = rows
        self.sql = None

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return list(self.rows)


class ActivityTests(unittest.TestCase):
    """«Тесты по дням»: период, Oktell, склейка переписок, упавший источник."""

    TEST = '7000000101'

    def test_period_limits(self):
        today = date(2026, 10, 9)
        self.assertEqual(activity.parse_period(None, None, today), (date(2026, 9, 26), today))
        self.assertEqual(activity.parse_period('2026-09-09', '2026-10-09', today), (date(2026, 9, 9), today))
        for args in (('2026-09-08', '2026-10-09'), ('2026-10-09', '2026-10-01'), ('вчера', None)):
            with self.subTest(args=args), self.assertRaises(activity.PeriodError):
                activity.parse_period(*args, today)

    def test_oktell_selects_exactly_the_registry_numbers_newest_first(self):
        sql = activity.oktell_sql({self.TEST}, date(2026, 10, 1), date(2026, 10, 9))
        # Условие «не тестовый» перевёрнуто: здесь нужны как раз тестовые.
        self.assertIn(keys.tsql_key('t.[number]') + f" IN ('{self.TEST}')", sql)
        self.assertNotIn('NOT IN', sql)
        self.assertIn("t.dt_insert >= '20261001' AND t.dt_insert < '20261010'", sql)
        # Упёрлись в потолок — теряются старые, а не сегодняшние.
        self.assertTrue(sql.endswith('ORDER BY t.dt_insert DESC'))
        self.assertTrue(sql.startswith(f'SELECT TOP {activity.OKTELL_ROW_CAP} '))
        self.assertIsNone(activity.oktell_sql(set(), date(2026, 10, 1), date(2026, 10, 9)))

    def test_oktell_is_read_once_per_period(self):
        calls = []

        def query(sql):
            calls.append(sql)
            return [{'call_id': 1, 'occurred_at': '2026-10-08 10:00:00', 'route': 'incoming',
                     'phone': '77000000101', 'total_length': 30}]

        activity._oktell_cache.clear()
        self.addCleanup(activity._oktell_cache.clear)
        moments = iter([100.0, 150.0, 300.0])
        args = (query, [self.TEST], date(2026, 10, 1), date(2026, 10, 9))
        first, truncated = activity._oktell(*args, clock=lambda: next(moments))
        self.assertEqual((len(first), truncated), (1, False))
        activity._oktell(*args, clock=lambda: next(moments))
        self.assertEqual(len(calls), 1, 'повтор в пределах 120 с — из кеша')
        activity._oktell(*args, clock=lambda: next(moments))
        self.assertEqual(len(calls), 2)

    def test_failed_source_goes_to_missing_and_others_stay(self):
        def freepbx(cursor, phone_keys, start, end):
            return [activity._item('freepbx', self.TEST, datetime(2026, 10, 8, 10), id='freepbx:1:x')]

        def broken(cursor, phone_keys, start, end):
            raise RuntimeError('таблицы нет')

        @contextmanager
        def get_cursor():
            yield object()

        with mock.patch.object(activity, '_DB_SOURCES', (('freepbx', freepbx), ('wazzup', broken))), \
                mock.patch.object(activity.logging, 'exception'):
            result = activity.collect(get_cursor, [self.TEST], date(2026, 10, 7), date(2026, 10, 9))
        self.assertEqual([item['id'] for item in result['items']], ['freepbx:1:x'])
        self.assertEqual([(m['source'], m['reason']) for m in result['missing']],
                         [('wazzup', 'не удалось прочитать'), ('oktell', 'Oktell не подключён')])
        self.assertEqual([(d['day'], d['calls'], d['chats']) for d in result['days']],
                         [('2026-10-09', 0, 0), ('2026-10-08', 1, 0), ('2026-10-07', 0, 0)])

    def test_chat2desk_one_chat_a_day_is_one_test(self):
        """Обращение, закрытое утром и открытое вечером, — та же переписка того же дня."""
        columns = ('request_id', 'day', 'request_start', 'transport', 'channel_id', 'channel_name',
                   'client_phone', 'assigned_phone', 'operator_name', 'incoming_messages',
                   'outgoing_messages', 'rating_score', 'client_key', 'assigned_key')
        oct8, oct9 = date(2026, 10, 8), date(2026, 10, 9)
        rows = [
            (1, oct8, datetime(2026, 10, 8, 19), 'whatsapp', 11, 'Линия', '77000000101', None, 'Оператор Б',
             1, 1, None, self.TEST, ''),
            # Клиент WhatsApp пришёл идентификатором: номер — в assigned_phone.
            (2, oct8, datetime(2026, 10, 8, 9), 'whatsapp', 11, 'Линия', '[wa_gupshup] KZ.1234567890123456',
             '77000000101', 'Оператор А', 2, 1, None, '7890123456', self.TEST),
            (3, oct8, datetime(2026, 10, 8, 12), 'telegram', 12, 'Телеграм', '77000000101', None, None,
             1, 0, None, self.TEST, ''),
            (4, oct9, datetime(2026, 10, 9, 9), 'whatsapp', 11, 'Линия', '77000000101', None, None,
             1, 0, None, self.TEST, ''),
        ]
        items = activity._chat2desk(_RowsCursor(columns, rows), [self.TEST], oct8, oct9)
        self.assertEqual(len(items), 3, 'Линия 8-го, Телеграм 8-го, Линия 9-го')
        self.assertEqual(len({item['id'] for item in items}), 3)
        line = next(item for item in items if item['day'] == '2026-10-08' and item['note'] == 'Линия')
        self.assertEqual((line['at'], line['messages'], line['operator']),
                         ('2026-10-08T09:00:00', 5, 'Оператор Б, Оператор А'))
        self.assertEqual({item['phone_key'] for item in items}, {self.TEST})

    def test_chatapp_ids_differ_by_messenger(self):
        columns = ('license_id', 'messenger_type', 'chat_id', 'phone_key', 'day', 'first_at', 'messages',
                   'inbound', 'operators')
        at = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)
        rows = [(7, 'grWhatsApp', '77000000101', self.TEST, date(2026, 10, 8), at, 3, 2, None),
                (7, 'caWhatsApp', '77000000101', self.TEST, date(2026, 10, 8), at, 1, 1, None)]
        items = activity._chatapp(_RowsCursor(columns, rows), [self.TEST], date(2026, 10, 8), date(2026, 10, 8))
        self.assertEqual(len({item['id'] for item in items}), 2)


class InsertSqlTests(unittest.TestCase):

    def test_insert_is_conflict_safe(self):
        """Без ON CONFLICT гонка двух вставок дала бы UniqueViolation — 500 вместо 409."""
        class Cursor:
            sql = []

            def execute(self, sql, params=None):
                self.sql.append(sql)

            def fetchone(self):
                return None

        cursor = Cursor()
        created = queries.add_number(cursor, phone_key='7000000101', phone_display='+7 700 000 01 01',
                                     owner={'id': 5}, actor={'user_id': 1})
        self.assertIsNone(created)
        self.assertIn('ON CONFLICT (phone_key) DO NOTHING', cursor.sql[0])
        self.assertEqual(len(cursor.sql), 1, 'без вставки — ни журнала, ни перечитывания')


class FakeDb:
    def __init__(self):
        self.commits = 0

    @contextmanager
    def _get_cursor(self):
        yield object()
        self.commits += 1


@unittest.skipIf(Flask is None, 'Flask не установлен')
class RouteTests(unittest.TestCase):

    def setUp(self):
        self.store = Store()
        self.viewer = person(role='admin', department_code=None)
        patches = {'load_access_context': lambda cursor, user_id: dict(self.viewer)}
        for name in ('list_numbers', 'get_number', 'find_by_key', 'people', 'active_person',
                     'add_number', 'change_owner', 'remove_number'):
            patches[name] = getattr(self.store, name)
        patcher = mock.patch.multiple(queries, **patches)
        patcher.start()
        self.addCleanup(patcher.stop)
        ready = mock.patch.object(schema, 'schema_is_ready', lambda cursor: True)
        ready.start()
        self.addCleanup(ready.stop)
        self.invalidations = 0

        def invalidate():
            self.invalidations += 1

        inv = mock.patch.object(keys, 'invalidate_cache', invalidate)
        inv.start()
        self.addCleanup(inv.stop)

        app = Flask(__name__)
        app.register_blueprint(routes.build_test_numbers_blueprint(
            db=FakeDb(),
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (self.viewer['user_id'], None, None),
        ))
        app.config['TESTING'] = True
        self.client = app.test_client()

    def add(self, phone='+7 700 000 01 01', owner=5):
        return self.client.post('/api/test_numbers/numbers', json={'phone': phone, 'owner_user_id': owner})

    def test_screen(self):
        self.add()
        response = self.client.get('/api/test_numbers')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['capabilities'], {'edit': True})
        self.assertEqual(len(body['numbers']), 1)
        number = body['numbers'][0]
        self.assertEqual(number['phone_display'], '+7 700 000 01 01')
        self.assertEqual(number['owner']['name'], 'Иванова Айгерим')
        # Время — по Алматы, а не GMT от jsonify.
        self.assertEqual(number['created_at'], '2026-10-09T10:30:00+05:00')

    def test_supervisor_gets_403_everywhere(self):
        """Реестр ведут админы и главы отделов; СВ, оператор, тренер и стажёр — закрыто."""
        for role in ('sv', 'operator', 'trainer', 'trainee'):
            self.viewer = person(role=role)
            for method, url in (('get', '/api/test_numbers'), ('get', '/api/test_numbers/people'),
                                ('get', '/api/test_numbers/activity'),
                                ('post', '/api/test_numbers/numbers'),
                                ('patch', '/api/test_numbers/numbers/1'),
                                ('delete', '/api/test_numbers/numbers/1')):
                with self.subTest(role=role, url=url, method=method):
                    response = getattr(self.client, method)(url, json={})
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.get_json()['code'], 'TEST_NUMBERS_CLOSED')
        self.assertEqual(self.store.writes, [])

    def test_head_of_any_department_is_let_in(self):
        self.viewer = person(role='sv', headed_codes=('hr',))
        self.assertEqual(self.client.get('/api/test_numbers').status_code, 200)
        self.assertEqual(self.add().status_code, 201)

    def test_insert_race_answers_409_not_500(self):
        """Два админа (или двойной клик) между проверкой и вставкой: ON CONFLICT DO NOTHING
        отдаёт None, и ручка отвечает тем же 409 с владельцем, что и обычный дубль."""
        self.add()
        existing = self.store.find_by_key(None, '7000000101')
        calls = iter([None, existing])
        with mock.patch.object(queries, 'find_by_key', lambda cursor, key: next(calls)), \
                mock.patch.object(queries, 'add_number', lambda cursor, **kw: None):
            response = self.add(phone='8 700 000 01 01', owner=6)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'TEST_NUMBERS_DUPLICATE')
        self.assertEqual(response.get_json()['number']['owner']['id'], 5)

    def test_add_validates_before_writing(self):
        cases = (
            ({'phone': '6024', 'owner_user_id': 5}, 400, 'TEST_NUMBERS_PHONE_INVALID'),
            ({'phone': '+7 700 000 01 01'}, 400, 'TEST_NUMBERS_OWNER_REQUIRED'),
            ({'phone': '+7 700 000 01 01', 'owner_user_id': True}, 400, 'TEST_NUMBERS_OWNER_REQUIRED'),
            ({'phone': '+7 700 000 01 01', 'owner_user_id': 999}, 404, 'TEST_NUMBERS_OWNER_UNKNOWN'),
        )
        for payload, status, code in cases:
            with self.subTest(payload=payload):
                response = self.client.post('/api/test_numbers/numbers', json=payload)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.get_json()['code'], code)
        self.assertEqual(self.store.writes, [])
        self.assertEqual(self.invalidations, 0)

    def test_add_then_duplicate_in_another_spelling(self):
        first = self.add()
        self.assertEqual(first.status_code, 201)
        self.assertEqual(self.invalidations, 1)
        again = self.add(phone='8 700 000 01 01', owner=6)
        self.assertEqual(again.status_code, 409)
        body = again.get_json()
        self.assertEqual(body['code'], 'TEST_NUMBERS_DUPLICATE')
        self.assertIn('Иванова Айгерим', body['error'])
        self.assertEqual(body['number']['owner']['id'], 5)
        self.assertEqual(self.store.writes, [('add', '7000000101')])

    def test_change_owner(self):
        self.add()
        response = self.client.patch('/api/test_numbers/numbers/1', json={'owner_user_id': 6})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['number']['owner']['name'], 'Карабек Ержан')
        missing = self.client.patch('/api/test_numbers/numbers/77', json={'owner_user_id': 6})
        self.assertEqual(missing.status_code, 404)
        self.assertNotIn(('owner', 77), self.store.writes)

    def test_remove(self):
        self.add()
        response = self.client.delete('/api/test_numbers/numbers/1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['removed']['phone_key'], '7000000101')
        self.assertEqual(self.invalidations, 2)
        self.assertEqual(self.client.delete('/api/test_numbers/numbers/1').status_code, 404)


# ─────────────────────────────────────────────────────────────────────────────
# Проводка
# ─────────────────────────────────────────────────────────────────────────────

class WiringTests(unittest.TestCase):

    def test_schema_runs_without_savepoint(self):
        """Не развернись таблица тихо — упали бы все расчёты с условием исключения."""
        source = _read(DATABASE_PY)
        self.assertIn('self._init_test_numbers_schema_tx(cursor)', source)
        method = source.split('def _init_test_numbers_schema_tx(self, cursor):')[1].split('\n    def ')[0]
        self.assertIn('init_test_numbers_schema(cursor)', method)
        self.assertNotIn('SAVEPOINT', method.split('"""')[2])

    def test_schema_creates_the_table_the_fragments_read(self):
        statements = ' '.join(schema._STATEMENTS)
        self.assertIn('CREATE TABLE IF NOT EXISTS %s' % keys.TABLE, statements)
        self.assertIn("CHECK (phone_key ~ '^[0-9]{10}$')", statements)

    def test_blueprint_is_registered(self):
        source = _read(BOT_PY)
        self.assertIn('from test_numbers.routes import build_test_numbers_blueprint', source)

    def test_menu_item_in_admin_and_head_branches(self):
        """Пункт, выданный главе, обязан быть и в его ветке меню, а не только в админской."""
        app = _read(APP_JSX)
        self.assertEqual(app.count("handleSidebarViewNavigation(e, 'test_numbers')"), 2)
        # Отрисовка и гард навигации — разные строки: без строки гарда главу отдела со своим
        # набором разделов выкидывало бы из реестра сразу после входа.
        self.assertIn("{view === 'test_numbers' && canAccessTestNumbersSection && (", app)
        self.assertIn("if (view === 'test_numbers' && canAccessTestNumbersSection) return;", app)


if __name__ == '__main__':
    unittest.main()
