# -*- coding: utf-8 -*-
"""«Воронка ОП» Тез во вкладке «Аналитика» (задача #407 Алчинбаевой Анель, 03.11.2026) на настоящем
PostgreSQL: база → диалог → вышли к заказам по городам, фильтры базы (файл), периода и города,
причины отказа. Звонки и поездки кладутся прямо в таблицы — проверяется счёт, а не путь звонка
(путь звонка — test_dial_list_tez_postgres.py).

Запуск: DIAL_LIST_TEST_PORT=<порт локального PostgreSQL> pytest tests/test_dial_list_tez_funnel_postgres.py
(пользователь postgres без пароля; каждый тест — в своей схеме, схема удаляется)."""
import contextlib
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import analytics as dial_analytics  # noqa: E402
from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402

PORT = os.environ.get('DIAL_LIST_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DIAL_LIST_TEST_PORT)')

TEZ, RCC = 560, 1954
OPERATOR = 530
ALMATY = timezone(timedelta(hours=5))
PERIOD = date(2026, 10, 1)

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, name text NOT NULL, code text,
                              is_active boolean DEFAULT TRUE);
    CREATE TABLE test_phone_numbers (phone_key varchar(10) UNIQUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, role text,
                        status text NOT NULL DEFAULT 'working', department_id integer,
                        sip_number text, rate numeric);
    CREATE TABLE sip_department_config (department_id integer PRIMARY KEY, provider text,
                                        sip_server text, ring_timeout_rings smallint);
    INSERT INTO departments (id, name, code) VALUES (560, 'Тез КЦ', 'tez'), (1954, 'Удаленный КЦ', 'remote_cc');
    INSERT INTO sip_department_config VALUES (560, 'binotel', 'sip53.binotel.com', NULL),
                                             (1954, 'binotel', 'sip53.binotel.com', NULL);
    INSERT INTO users (id, name, login, role, status, department_id, sip_number) VALUES
        (1, 'Админ', 'admin', 'admin', 'working', NULL, NULL),
        (530, 'Ашимова Айгерим', 'aigerim', 'operator', 'working', 560, '601');
'''


class _Db:
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


def _at(day, hour=12):
    """Момент дня октября 2026 по Алматы."""
    return datetime(2026, 10, day, hour, 0, tzinfo=ALMATY)


class _Env:
    def __init__(self, db, svc):
        self.db, self.svc = db, svc
        self.portion = None

    def q(self, sql, params=None):
        with self.db._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def one(self, sql, params=None):
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def base(self, rows, file_name='база.xlsx'):
        """rows: [(номер, город)] → лиды базы октября; возвращает {номер: lead_id}."""
        parsed = [(i, f'Водитель {i}', '8' + phone[1:], phone, '', city) for i, (phone, city) in enumerate(rows, 2)]
        counts = self.svc.import_leads(TEZ, 1, file_name, parsed, period=PERIOD, require_iin=False)
        ids = {phone: str(self.one("SELECT id FROM dial_list_leads WHERE department_id = %s AND period = %s "
                                   "AND phone_norm = %s", (TEZ, PERIOD, phone))[0]) for phone, _city in rows}
        return ids, counts["batch_id"]

    def call(self, lead_id, when, disposition='ANSWER', billsec=30, auto_hangup='', outcome=None, reason=None):
        if self.portion is None:
            self.portion = str(self.one("INSERT INTO dial_list_portions (operator_id, department_id, size) "
                                        "VALUES (%s, %s, 50) RETURNING id", (OPERATOR, TEZ))[0])
        # Выдача сразу закрыта — как после звонка: открытая у водителя может быть только одна.
        assignment = self.one("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position, state) "
                              "VALUES (%s, %s, %s, 1, 'done') RETURNING id", (self.portion, lead_id, OPERATOR))[0]
        self.q("INSERT INTO dial_list_attempts (assignment_id, operator_id, requested_at, state, disposition, "
               "billsec, auto_hangup, outcome_id, outcome_subtype_id) "
               "VALUES (%s, %s, %s, 'finished', %s, %s, %s, %s, %s)",
               (assignment, OPERATOR, when, disposition, billsec, auto_hangup, outcome, reason))

    def trip(self, lead_id, when):
        self.q("UPDATE dial_list_leads SET signed_at = %s, sign_status = 'online' WHERE id = %s", (when, lead_id))

    def outcome(self, name):
        return str(self.one("SELECT id FROM dial_list_outcomes WHERE department_id = %s AND name = %s",
                            (TEZ, name))[0])

    def reason(self, outcome_id, name, position=1, active=True):
        return str(self.one("INSERT INTO dial_list_outcome_subtypes (outcome_id, department_id, name, position, "
                            "is_active) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                            (outcome_id, TEZ, name, position, active))[0])

    def funnel(self, **filters):
        return self.svc.funnel(TEZ, period=PERIOD, **filters)


@pytest.fixture
def env():
    schema = 't_dial_funnel_' + uuid.uuid4().hex
    db = _Db(schema)
    admin = db.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
        cur.execute("INSERT INTO dial_list_department_settings (department_id, enabled, binotel_company) "
                    "VALUES (560, FALSE, 'remote_cc'), (1954, TRUE, 'remote_cc')")
    svc = dial_service.DialListService(db, client_factory=lambda dep: None, calls_submit=lambda fn: fn(),
                                       trips_fetch=lambda phones, month, timeout=None: {},
                                       trips_configured=lambda: True)
    # Тез КЦ переводит админ; итоги — набор Тез (в нём «Отказ»).
    svc.enroll_department(TEZ, changed_by=1, profile='tez')
    try:
        yield _Env(db, svc)
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


def _row(data, city):
    return next(r for r in data["by_city"] if r["city"] == city)


def test_steps_count_each_driver_once_and_only_trips_after_the_dialog(env):
    leads, _batch = env.base([('77010000001', 'Алматы'), ('77010000002', 'Алматы'), ('77010000003', ' Алматы '),
                              ('77010000004', 'Тараз'), ('77010000005', ''), ('77010000006', 'Тараз')])
    a1, a2, a3, t4, n5, t6 = (leads[p] for p in sorted(leads))
    # a1: два диалога — один водитель; поездка после первого диалога — вышел к заказам.
    env.call(a1, _at(5), billsec=40)
    env.call(a1, _at(6), billsec=25)
    env.trip(a1, _at(7))
    # a2: диалог, но поездка была ДО него — в «после контакта» не идёт.
    env.call(a2, _at(10), billsec=15)
    env.trip(a2, _at(3))
    # a3: только короткий разговор (9 с), сообщение сети и недозвон — диалога нет; поездка без контакта.
    env.call(a3, _at(4), billsec=9)
    env.call(a3, _at(4, 13), billsec=60, auto_hangup='announcement')
    env.call(a3, _at(4, 14), disposition='NOANSWER', billsec=0)
    env.trip(a3, _at(8))
    # t4: диалог ровно 10 с — считается; поездки нет.
    env.call(t4, _at(9), billsec=10)
    # n5 (без города): диалог и поездка после него.
    env.call(n5, _at(11), billsec=120)
    env.trip(n5, _at(12))
    data = env.funnel()
    assert data["totals"] == {"base": 6, "already_registered": None, "dialog": 4, "registered_after": None,
                              "orders_after": 2}
    assert data["rates"] == {"dialog_to_registration": None, "dialog_to_orders": 50.0}
    assert data["registration_available"] is False
    # Города: пробелы по краям не делят город; «не указан» — в конце; крупные — первыми.
    assert [(r["city"], r["base"], r["dialog"], r["orders_after"], r["dialog_to_orders"]) for r in data["by_city"]] == [
        ('Алматы', 3, 2, 1, 50.0), ('Тараз', 2, 1, 0, 0.0), ('', 1, 1, 1, 100.0)]
    assert [c["name"] for c in data["options"]["cities"]] == ['Алматы', 'Тараз', '']
    assert data["period"] == '2026-10-01'


def test_period_bounds_dialogs_and_trips_count_after_the_first_dialog_inside_it(env):
    leads, _batch = env.base([('77010000001', 'Алматы'), ('77010000002', 'Алматы'), ('77010000003', 'Алматы')])
    a1, a2, a3 = (leads[p] for p in sorted(leads))
    env.call(a1, _at(2), billsec=30)          # до периода
    env.call(a1, _at(10), billsec=30)         # в периоде
    env.trip(a1, _at(5))                      # после первого диалога вообще, но ДО диалога в периоде
    env.call(a2, _at(10, 23), billsec=30)     # последний час последнего дня периода — внутри
    env.trip(a2, _at(14))
    env.call(a3, _at(11, 0), billsec=30)      # полночь следующего дня — уже вне периода
    env.trip(a3, _at(12))
    data = env.funnel(date_from=date(2026, 10, 9), date_to=date(2026, 10, 10))
    assert (data["totals"]["base"], data["totals"]["dialog"], data["totals"]["orders_after"]) == (3, 2, 1)
    assert data["filters"]["date_from"] == '2026-10-09'
    # Без периода у a1 первый диалог 2.10 — поездка 5.10 уже «после контакта».
    data = env.funnel()
    assert (data["totals"]["dialog"], data["totals"]["orders_after"]) == (3, 3)


def test_no_dialogs_give_zero_percent_not_an_error(env):
    env.base([('77010000001', 'Алматы')])
    data = env.funnel()
    assert data["totals"]["dialog"] == 0
    assert data["rates"]["dialog_to_orders"] == 0.0
    assert data["by_city"][0]["dialog_to_orders"] == 0.0
    # Пустая база — пустые разрезы и нули.
    empty = env.svc.funnel(TEZ, period=date(2026, 9, 1))
    assert (empty["totals"]["base"], empty["by_city"], empty["refusals"]["total"]) == (0, [], 0)


def test_file_and_city_filters(env):
    first, batch1 = env.base([('77010000001', 'Алматы'), ('77010000002', 'Тараз')], file_name='первая.xlsx')
    second, batch2 = env.base([('77010000003', 'Тараз'), ('77010000004', '')], file_name='добор.xlsx')
    env.call(second['77010000003'], _at(5), billsec=30)
    data = env.funnel(batch_id=batch2)
    assert (data["totals"]["base"], data["totals"]["dialog"]) == (2, 1)
    assert [c["name"] for c in data["options"]["cities"]] == ['Тараз', '']
    assert [b["file_name"] for b in data["options"]["batches"]] == ['добор.xlsx', 'первая.xlsx']
    # Ключ города — тот, что вернула воронка; «не указан» — свой ключ.
    taraz = next(c["key"] for c in env.funnel()["options"]["cities"] if c["name"] == 'Тараз')
    data = env.funnel(city=taraz)
    assert [(r["city"], r["base"], r["dialog"]) for r in data["by_city"]] == [('Тараз', 2, 1)]
    # Выбор города остаётся полным, пока выбран город: варианты от него не сужаются (крупные — первыми).
    assert [(c["name"], c["base"]) for c in data["options"]["cities"]] == [('Тараз', 2), ('Алматы', 1), ('', 1)]
    data = env.funnel(city=dial_analytics.FUNNEL_CITY_NONE)
    assert (data["totals"]["base"], data["by_city"][0]["city"], data["by_city"][0]["key"]) == (
        1, '', dial_analytics.FUNNEL_CITY_NONE)
    # Повторная загрузка того же номера с городом ставит его и старой строке.
    env.base([('77010000004', 'Шымкент')], file_name='города.xlsx')
    assert env.one("SELECT city FROM dial_list_leads WHERE phone_norm = '77010000004'")[0] == 'Шымкент'


def test_refusal_reasons_are_the_last_refusal_of_each_driver_in_the_period(env):
    leads, _batch = env.base([('7701000000%d' % i, 'Алматы') for i in range(1, 7)])
    l1, l2, l3, l4, l5, l6 = (leads[p] for p in sorted(leads))
    refusal = env.outcome('Отказ')
    expensive = env.reason(refusal, 'Дорого', 1)
    other_park = env.reason(refusal, 'Работает в другом парке', 2)
    callback = env.outcome('Перезвонить')
    env.call(l1, _at(5), outcome=refusal, reason=expensive)
    env.call(l2, _at(5), outcome=refusal, reason=expensive)
    # l3 сначала «дорого», потом «в другом парке» — считается один раз, с последней причиной.
    env.call(l3, _at(5), outcome=refusal, reason=expensive)
    env.call(l3, _at(7), outcome=refusal, reason=other_park)
    # l4 — отказ без причины (телефон до причин): строка «без причины».
    env.call(l4, _at(6), outcome=refusal)
    # l5 — не отказ.
    env.call(l5, _at(6), outcome=callback)
    # l6 — отказ вне периода.
    env.call(l6, _at(20), outcome=refusal, reason=expensive)
    data = env.funnel(date_to=date(2026, 10, 15))
    refusals = data["refusals"]
    assert refusals["total"] == 4
    assert [(r["name"], r["count"], r["share"]) for r in refusals["reasons"]] == [
        ('Дорого', 2, 50.0), ('Работает в другом парке', 1, 25.0), ('', 1, 25.0)]
    assert refusals["reasons"][2]["subtype_id"] is None
    assert refusals["outcomes"] == [{"name": 'Отказ', "reasons": 2}]
    # Без периода l6 тоже в отчёте.
    assert env.funnel()["refusals"]["total"] == 5
    # Город фильтрует и отчёт об отказах.
    assert env.funnel(city=dial_analytics.FUNNEL_CITY_NONE)["refusals"]["total"] == 0


def test_refusal_outcome_is_found_by_name_whatever_the_case(env):
    leads, _batch = env.base([('77010000001', 'Алматы')])
    env.q("UPDATE dial_list_outcomes SET name = '  отказ водителя' WHERE department_id = %s AND name = 'Отказ'",
          (TEZ,))
    refusal = env.outcome('  отказ водителя')
    env.call(leads['77010000001'], _at(5), outcome=refusal)
    data = env.funnel()
    assert (data["refusals"]["total"], data["refusals"]["outcomes"]) == (1, [{"name": '  отказ водителя', "reasons": 0}])
    # Выключенный итог-отказ: история считается, в списке «настроенных» его нет.
    env.q("UPDATE dial_list_outcomes SET is_active = FALSE WHERE id = %s", (refusal,))
    data = env.funnel()
    assert (data["refusals"]["total"], data["refusals"]["outcomes"]) == (1, [])
    assert dial_analytics.is_refusal_outcome('ОТКАЗ') and not dial_analytics.is_refusal_outcome('Перезвонить')


def test_test_numbers_are_not_drivers(env):
    leads, _batch = env.base([('77010000001', 'Алматы'), ('77019999999', 'Алматы')])
    env.q("INSERT INTO test_phone_numbers (phone_key) VALUES ('7019999999')")
    env.call(leads['77019999999'], _at(5), billsec=30, outcome=env.outcome('Отказ'))
    data = env.funnel()
    assert (data["totals"]["base"], data["totals"]["dialog"], data["refusals"]["total"]) == (1, 0, 0)


def test_funnel_is_tez_only(env):
    with pytest.raises(dial_service.DialListError) as refused:
        env.svc.funnel(RCC, period=PERIOD)
    assert refused.value.status == 400
    with pytest.raises(dial_service.DialListError):
        env.funnel(date_from=date(2026, 10, 10), date_to=date(2026, 10, 9))
