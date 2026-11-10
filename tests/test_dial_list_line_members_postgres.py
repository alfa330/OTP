# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: DIAL_LIST_TEST_PORT — порт локального Postgres.

Раздел «Удаленный КЦ»: сотрудник другого отдела на линии (запрос владельца 07.10.2026).
Строковые проверки в tests/test_dial_list_line_members.py ловят, что запрос НАПИСАН, но
не что он ЗНАЧИТ: разбор 07.10.2026 показал мутации JOIN/WHERE, которые проходили их
зелёными (право — любому главе, телефону — телефония чужого отдела, сводка — не по той
базе). Здесь те же запросы исполняются на настоящей схеме раздела (dial_list.schema) и
маленьком наборе людей.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным
именем, в конце она удаляется. Запуск на локальном кластере:

    DIAL_LIST_TEST_PORT=55441 python -m pytest tests/test_dial_list_line_members_postgres.py

Люди выдуманы.
"""
import ast
import contextlib
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests import source_cache
from dial_list import schema as dial_schema
from dial_list import service as dial_service

PORT = os.environ.get('DIAL_LIST_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DIAL_LIST_TEST_PORT)')
ROOT = Path(__file__).resolve().parents[1]

SZOV, OP, TEZ, RCC = 1, 367, 560, 1954
GUEST, GUEST2, OWN, OWN2, FIRED, TEZ_OP, NOBODY = 701, 702, 520, 521, 704, 703, 705

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, name text NOT NULL, code text,
                              is_active boolean DEFAULT TRUE);
    CREATE TABLE test_phone_numbers (phone_key varchar(10) UNIQUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, role text,
                        status text NOT NULL DEFAULT 'working', department_id integer,
                        sip_number text);
    CREATE TABLE sip_department_config (department_id integer PRIMARY KEY, provider text,
                                        sip_server text, auto_answer boolean,
                                        auto_answer_delay smallint);
    INSERT INTO departments (id, name, code) VALUES
        (1, 'СЗоВ', 'SZOV'), (367, 'Отдел продаж', 'op'), (560, 'Тез КЦ', 'tez'),
        (1954, 'Удаленный КЦ', 'remote_cc');
    INSERT INTO sip_department_config VALUES
        (367, 'asterisk', '192.168.88.251', NULL, NULL),
        (560, 'binotel', 'sip52.binotel.com', TRUE, 3),
        (1954, 'binotel', 'sip53.binotel.com', NULL, 4);
    INSERT INTO users (id, name, login, role, status, department_id, sip_number) VALUES
        (1, 'Главнова Гульнар', 'head', 'admin', 'working', 1, NULL),
        (2, 'Суперов Самат', 'super', 'super_admin', 'working', 1, NULL),
        (701, 'Гостев Глеб', 'gleb', 'operator', 'working', 1, '1024'),
        (702, 'Вторая Вера', 'vera', 'operator', 'working', 1, '1025'),
        (520, 'Своев Сава', 'sava', 'operator', 'working', 1954, '902'),
        (521, 'Новичков Назар', 'nazar', 'operator', 'working', 1954, NULL),
        (704, 'Бывшев Борис', 'boris', 'operator', 'fired', 1, '1099'),
        (703, 'Тезов Тимур', 'timur', 'operator', 'working', 560, '913'),
        (705, 'Ничейный Наиль', 'nail', 'operator', 'working', NULL, NULL);
'''


class _Db:
    """_get_cursor на своей схеме: отдельное соединение, фиксация в конце блока."""

    _SIP_INACTIVE_STATUSES = ('fired', 'dismissal')

    def __init__(self, schema):
        self.schema = schema

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                                connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.schema)
            cur.execute("SET statement_timeout TO '10s'")
        conn.commit()
        return conn

    @contextlib.contextmanager
    def _get_cursor(self):
        conn = self.connect()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()


@pytest.fixture
def db():
    schema = 't_dial_line_' + uuid.uuid4().hex
    base = _Db(schema)
    admin = base.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


def _q(db, sql, params=None):
    with db._get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _seat(db, svc, user_id, number, department_id=RCC, by=1):
    with db._get_cursor() as cur:
        svc._seat_member(cur, department_id, user_id, number, f'lg{number}', f'pw{number}', changed_by=by)


def test_szov_head_is_found_by_code_in_any_case(db):
    svc = dial_service.DialListService(db)
    assert svc._heads_department_with_code([SZOV], dial_service.DIAL_LIST_SEAT_ANYONE_HEAD_CODES) is True
    assert svc._heads_department_with_code([OP, TEZ, RCC], dial_service.DIAL_LIST_SEAT_ANYONE_HEAD_CODES) is False
    assert svc.can_seat_anyone(False, [OP, SZOV]) is True
    assert svc.can_seat_anyone(False, [OP]) is False


def test_binding_is_read_with_the_line_department_config(db):
    svc = dial_service.DialListService(db)
    assert svc.line_member(GUEST) is None
    _seat(db, svc, GUEST, '903')
    member = svc.line_member(GUEST)
    assert member == {
        'user_id': GUEST, 'department_id': RCC, 'internal_number': '903',
        'sip_login': 'lg903', 'sip_password': 'pw903', 'provider': 'binotel',
        # Сервер и ярус автоприёма — отдела ЛИНИИ, а не отдела человека.
        'sip_server': 'sip53.binotel.com', 'auto_answer': None, 'auto_answer_delay': 4,
    }
    svc._unseat_member(GUEST, changed_by=2)
    assert svc.line_member(GUEST) is None
    assert _q(db, 'SELECT internal_number, sip_password, released_by FROM dial_list_line_members '
                  'WHERE user_id = %s', (GUEST,)) == [('903', '', 2)]


def test_one_person_one_line_and_one_line_one_person(db):
    import psycopg2.errors
    svc = dial_service.DialListService(db)
    _seat(db, svc, GUEST, '903')
    _seat(db, svc, GUEST, '904')            # пересадка: прежняя привязка закрыта
    assert _q(db, 'SELECT internal_number, released_at IS NULL FROM dial_list_line_members '
                  'WHERE user_id = %s ORDER BY id', (GUEST,)) == [('903', False), ('904', True)]
    with pytest.raises(psycopg2.errors.UniqueViolation) as raised:
        _seat(db, svc, GUEST2, '904')       # линию держит работающий — индекс не пускает
    assert raised.value.diag.constraint_name == 'uq_dial_list_line_members_line'
    # Уволенного с линии снимает сама посадка другого.
    _q(db, "UPDATE users SET status = 'fired' WHERE id = %s", (GUEST,))
    _seat(db, svc, GUEST2, '904')
    assert _q(db, 'SELECT user_id FROM dial_list_line_members WHERE released_at IS NULL') == [(GUEST2,)]


def test_department_users_show_the_seated_and_hide_the_gone(db):
    svc = dial_service.DialListService(db)
    _seat(db, svc, GUEST, '903')
    _seat(db, svc, TEZ_OP, '905')
    _seat(db, svc, FIRED, '906')            # уволенный в списке не показывается
    users = {u['id']: u for u in svc.department_users(RCC)}
    assert set(users) == {GUEST, TEZ_OP, OWN, OWN2}
    # Линия гостя — из привязки, а не users.sip_number (там его логин Oktell).
    assert (users[GUEST]['sip_number'], users[GUEST]['guest'], users[GUEST]['department_name']) == ('903', True, 'СЗоВ')
    assert (users[OWN]['sip_number'], users[OWN]['guest'], users[OWN]['department_name']) == ('902', False, '')
    # В своём отделе посаженный на линию удалённого КЦ сотрудник Теза не числится.
    assert TEZ_OP not in {u['id'] for u in svc.department_users(TEZ)}
    # Перевели гостя в отдел линии — он свой, линия при нём.
    _q(db, 'UPDATE users SET department_id = %s WHERE id = %s', (RCC, GUEST))
    users = {u['id']: u for u in svc.department_users(RCC)}
    assert (users[GUEST]['guest'], users[GUEST]['sip_number']) == (False, '903')


def test_candidates_come_back_after_release(db):
    svc = dial_service.DialListService(db)
    ids = lambda: {c['id'] for c in svc.line_candidates(RCC)}  # noqa: E731
    # Все действующие сотрудники других отделов (и руководители тоже); свои и уволенные — нет.
    assert ids() == {1, 2, GUEST, GUEST2, TEZ_OP, NOBODY}
    _seat(db, svc, GUEST, '903')
    assert GUEST not in ids()
    svc._unseat_member(GUEST, changed_by=1)
    # Сняли — снова можно выбрать (разбор: без released_at IS NULL человек пропадал навсегда).
    assert GUEST in ids()
    by_id = {c['id']: c for c in svc.line_candidates(RCC)}
    assert by_id[GUEST] == {'id': GUEST, 'name': 'Гостев Глеб', 'login': 'gleb', 'department_name': 'СЗоВ'}
    assert by_id[NOBODY]['department_name'] == ''


def test_line_holder_sees_bindings_and_own_sip_settings(db):
    svc = dial_service.DialListService(db)
    with db._get_cursor() as cur:
        holder = lambda number, exclude=None: svc._line_holder(cur, RCC, number, exclude_user_id=exclude)  # noqa: E731
        assert holder('902') == {'id': OWN, 'name': 'Своев Сава'}          # своя линия в SIP-настройках
        assert holder('902', exclude=OWN) is None                            # сам себе не помеха
        assert holder('903') is None
    _seat(db, svc, GUEST, '903')
    _seat(db, svc, OWN, '901', department_id=TEZ)                             # свой — гостем на линии Теза
    _seat(db, svc, FIRED, '906')
    with db._get_cursor() as cur:
        assert svc._line_holder(cur, RCC, '903') == {'id': GUEST, 'name': 'Гостев Глеб'}
        # Своя 902 по-прежнему его: учётка в его настройках, и после снятия он вернётся на неё.
        assert svc._line_holder(cur, RCC, '902') == {'id': OWN, 'name': 'Своев Сава'}
        assert svc._line_holder(cur, RCC, '906') is None                     # уволенный не держит
        assert svc._line_holder(cur, TEZ, '901') == {'id': OWN, 'name': 'Своев Сава'}


def test_line_lock_makes_the_second_assignment_wait(db):
    svc = dial_service.DialListService(db)
    order = []
    entered = threading.Event()

    def first():
        with svc._line_lock(RCC, '905'):
            order.append('first-in')
            entered.set()
            time.sleep(0.6)
            order.append('first-out')

    def second():
        entered.wait(5)
        with svc._line_lock(RCC, '905'):
            order.append('second-in')

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert order == ['first-in', 'first-out', 'second-in']
    # Другая линия замком не задерживается.
    with svc._line_lock(RCC, '905'), svc._line_lock(RCC, '906'):
        pass


def test_overview_is_scoped_by_the_base_and_former_operators_are_listed(db):
    svc = dial_service.DialListService(db)
    now = datetime.now(timezone.utc)
    day = now.astimezone(timezone(timedelta(hours=5))).date()
    with db._get_cursor() as cur:
        rows = {}
        for dept, phone in ((RCC, '77010000001'), (RCC, '77010000002'), (TEZ, '77010000003')):
            cur.execute('INSERT INTO dial_list_leads (department_id, phone_norm, full_name) VALUES (%s, %s, %s) '
                        'RETURNING id', (dept, phone, 'Водитель ' + phone[-1]))
            rows[phone] = cur.fetchone()[0]
        calls = ((GUEST, RCC, '77010000001', 'ANSWER', 40), (OWN, RCC, '77010000002', 'NOANSWER', 0),
                 (GUEST, TEZ, '77010000003', 'ANSWER', 15))
        for operator, dept, phone, disposition, billsec in calls:
            cur.execute('INSERT INTO dial_list_portions (operator_id, department_id, size) VALUES (%s, %s, 1) '
                        'RETURNING id', (operator, dept))
            portion = cur.fetchone()[0]
            cur.execute("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position, state) "
                        "VALUES (%s, %s, %s, 1, 'done') RETURNING id", (portion, rows[phone], operator))
            assignment = cur.fetchone()[0]
            cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, internal_number, state, "
                        "disposition, billsec) VALUES (%s, %s, '903', 'finished', %s, %s)",
                        (assignment, operator, disposition, billsec))
    rcc = {r['operator_id']: r for r in svc.overview([RCC], day)}
    assert set(rcc) == {GUEST, OWN}
    # У гостя в сводке удалённого КЦ — только звонки по его базе: Тез сюда не попадает.
    assert (rcc[GUEST]['attempts'], rcc[GUEST]['answered'], rcc[GUEST]['talk_sec'], rcc[GUEST]['issued']) == (1, 1, 40, 1)
    tez = {r['operator_id']: r for r in svc.overview([TEZ], day)}
    assert set(tez) == {GUEST} and tez[GUEST]['talk_sec'] == 15
    every = {r['operator_id']: r for r in svc.overview(None, day)}
    assert every[GUEST]['attempts'] == 2
    # Фильтр журнала: обзванивал базу, но в нынешнем составе отдела его нет.
    assert svc.former_operators(RCC, exclude_ids=[OWN]) == [{'id': GUEST, 'name': 'Гостев Глеб', 'login': 'gleb'}]
    assert svc.former_operators(RCC, exclude_ids=[OWN, GUEST]) == []


def test_hours_calls_count_every_placed_call_of_szov_staff_by_almaty_day(db, monkeypatch):
    """Задача SZOV-4 (владелец 10.11.2026: «все звонки нужно учитывать»): звонки из телефона идут
    в «Звонки» «Учёта часов». Считается каждый набранный звонок сотрудника СЗоВ — по его
    отделу, а не по отделу базы или линии; день — алматинский."""
    monkeypatch.delenv(dial_service.HOURS_CALLS_ENV, raising=False)
    svc = dial_service.DialListService(db)
    almaty = timezone(timedelta(hours=5))
    day = datetime(2026, 11, 10, tzinfo=almaty)
    with db._get_cursor() as cur:
        cur.execute("INSERT INTO test_phone_numbers (phone_key) VALUES ('7010000009')")
        leads = {}
        for dept, phone in ((RCC, '77010000001'), (TEZ, '77010000003'), (RCC, '77010000009')):
            cur.execute('INSERT INTO dial_list_leads (department_id, phone_norm, full_name) VALUES (%s, %s, %s) '
                        'RETURNING id', (dept, phone, 'Водитель ' + phone[-1]))
            leads[phone] = cur.fetchone()[0]
        calls = (
            # кто, база, номер, когда (Алматы), состояние, итог АТС, секунд, сброшен оператором
            (GUEST, RCC, '77010000001', day.replace(hour=10), 'finished', 'ANSWER', 40, False),
            (GUEST, RCC, '77010000001', day.replace(hour=11), 'finished', 'NOANSWER', 0, False),
            (GUEST, RCC, '77010000001', day.replace(hour=12), 'finished', 'CANCEL', 0, True),
            (GUEST, RCC, '77010000001', day.replace(hour=13), 'finished', 'ANSWER', 3, False),
            (GUEST, RCC, '77010000001', day.replace(hour=14), 'leg_answered', '', 0, False),   # идёт сейчас
            (GUEST, TEZ, '77010000003', day.replace(hour=15), 'finished', 'ANSWER', 15, False),  # чужая база
            (GUEST, RCC, '77010000001', day.replace(hour=16), 'failed', '', 0, False),          # отказ АТС
            (GUEST, RCC, '77010000009', day.replace(hour=17), 'finished', 'ANSWER', 60, False),  # тестовый номер
            # Сутки — алматинские: 23:30 ещё 10-е, 00:10 уже 11-е (по UTC оба — 10-е).
            (GUEST, RCC, '77010000001', day.replace(hour=23, minute=30), 'finished', 'BUSY', 0, False),
            (GUEST, RCC, '77010000001', day + timedelta(days=1, minutes=10), 'finished', 'ANSWER', 20, False),
            (GUEST2, RCC, '77010000001', day + timedelta(days=1, hours=9), 'finished', 'ANSWER', 20, False),
            (FIRED, RCC, '77010000001', day.replace(hour=9), 'finished', 'ANSWER', 20, False),
            # Не СЗоВ: свой сотрудник удалённого КЦ, оператор Тез, человек без отдела.
            (OWN, RCC, '77010000001', day.replace(hour=10), 'finished', 'ANSWER', 40, False),
            (TEZ_OP, TEZ, '77010000003', day.replace(hour=10), 'finished', 'ANSWER', 40, False),
            (NOBODY, RCC, '77010000001', day.replace(hour=10), 'finished', 'ANSWER', 40, False),
            # Вне периода.
            (GUEST, RCC, '77010000001', day - timedelta(minutes=1), 'finished', 'ANSWER', 40, False),
            (GUEST, RCC, '77010000001', day + timedelta(days=2), 'finished', 'ANSWER', 40, False),
        )
        for operator, dept, phone, at, state, disposition, billsec, cancelled in calls:
            cur.execute('INSERT INTO dial_list_portions (operator_id, department_id, size) VALUES (%s, %s, 1) '
                        'RETURNING id', (operator, dept))
            portion = cur.fetchone()[0]
            cur.execute("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position, state) "
                        "VALUES (%s, %s, %s, 1, 'done') RETURNING id", (portion, leads[phone], operator))
            assignment = cur.fetchone()[0]
            cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, internal_number, requested_at, "
                        "state, disposition, billsec, cancelled) VALUES (%s, %s, '903', %s, %s, %s, %s, %s)",
                        (assignment, operator, at, state, disposition, billsec, cancelled))
    first, second = '2026-11-10', '2026-11-11'
    assert svc.hours_calls(day.date(), day.date() + timedelta(days=1)) == {
        (GUEST, first): 7, (FIRED, first): 1, (GUEST, second): 1, (GUEST2, second): 1}
    # Один день — только его звонки; строкой день принимается так же, как датой.
    assert svc.hours_calls(first, first) == {(GUEST, first): 7, (FIRED, first): 1}
    assert svc.hours_calls(second, second) == {(GUEST, second): 1, (GUEST2, second): 1}
    # Рубильник на сервере.
    monkeypatch.setenv(dial_service.HOURS_CALLS_ENV, '0')
    assert svc.hours_calls(first, second) == {}


def _find_holder_method():
    node = source_cache.function_copy(source_cache.database_flat_path(), 'find_dial_list_line_holder', class_name='Database')
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    namespace = {'logging': logging, 'Optional': __import__('typing').Optional}
    exec(compile(module, str(source_cache.database_flat_path()), 'exec'), namespace)
    return namespace['find_dial_list_line_holder']


def test_sip_settings_see_a_line_held_by_a_seated_employee(db):
    """«Настройки SIP» не должны отдать своему сотруднику учётку занятой гостем линии."""
    svc = dial_service.DialListService(db)
    find = _find_holder_method()
    assert find(db, sip_login='lg903') is None
    _seat(db, svc, GUEST, '903')
    held = {'user_id': GUEST, 'name': 'Гостев Глеб', 'internal_number': '903'}
    assert find(db, sip_login=' LG903 ') == held
    assert find(db, number='903', domain='SIP53.binotel.com') == held
    assert find(db, number='903', domain='sip52.binotel.com') is None       # тот же номер у другой компании
    assert find(db, sip_login='lg903', exclude_user_ids=[GUEST]) is None
    _q(db, "UPDATE users SET status = 'fired' WHERE id = %s", (GUEST,))
    assert find(db, sip_login='lg903') is None                               # уволенный линию не держит
