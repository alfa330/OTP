# -*- coding: utf-8 -*-
"""«Воронка ОП» Тез во вкладке «Аналитика» (задача #407 Алчинбаевой Анель, 03.11.2026) на настоящем
PostgreSQL: база → уже зарегистрированы → диалог → зарегистрировались после контакта → вышли к заказам
по городам, фильтры базы (файл), периода и города, причины отказа. Звонки и ответы TEZ APP (регистрация
и поездки, поля с 04.11.2026) кладутся прямо в таблицы — проверяется счёт, а не путь звонка (путь
звонка и запись ответа TEZ APP — test_dial_list_tez_postgres.py).

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

    def call(self, lead_id, when, disposition='ANSWER', billsec=30, auto_hangup='', outcome=None, reason=None,
             state='finished', cancelled=False):
        if self.portion is None:
            self.portion = str(self.one("INSERT INTO dial_list_portions (operator_id, department_id, size) "
                                        "VALUES (%s, %s, 50) RETURNING id", (OPERATOR, TEZ))[0])
        # Выдача сразу закрыта — как после звонка: открытая у водителя может быть только одна.
        assignment = self.one("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position, state) "
                              "VALUES (%s, %s, %s, 1, 'done') RETURNING id", (self.portion, lead_id, OPERATOR))[0]
        self.q("INSERT INTO dial_list_attempts (assignment_id, operator_id, requested_at, state, disposition, "
               "billsec, auto_hangup, outcome_id, outcome_subtype_id, cancelled) "
               "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
               (assignment, OPERATOR, when, state, disposition, billsec, auto_hangup, outcome, reason, cancelled))

    def trip(self, lead_id, when):
        """Первый заказ в месяце базы — вышел на линию (так пишет проверка поездок)."""
        self.q("UPDATE dial_list_leads SET signed_at = %s, sign_status = 'online' WHERE id = %s", (when, lead_id))

    def tez(self, lead_id, registered=None, last_order=None):
        """Ответ TEZ APP о водителе: создание аккаунта и начало последней завершённой поездки."""
        self.q("UPDATE dial_list_leads SET registered_at = %s, last_order_at = %s, trip_checked_at = now() "
               "WHERE id = %s", (registered, last_order, lead_id))

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


def test_steps_count_each_driver_once_and_only_results_after_the_dialog(env):
    leads, _batch = env.base([('77010000001', 'Алматы'), ('77010000002', 'Алматы'), ('77010000003', ' Алматы '),
                              ('77010000004', 'Тараз'), ('77010000005', ''), ('77010000006', 'Тараз')])
    a1, a2, a3, t4, n5, t6 = (leads[p] for p in sorted(leads))
    # a1: зарегистрирован до звонков; два диалога — один водитель; поездка после первого диалога.
    env.call(a1, _at(5), billsec=40)
    env.call(a1, _at(6), billsec=25)
    env.trip(a1, _at(7))
    env.tez(a1, registered=_at(1) - timedelta(days=30), last_order=_at(7))
    # a2: диалог, но поездка была ДО него — в «после контакта» не идёт; аккаунт — до первого звонка.
    env.call(a2, _at(10), billsec=15)
    env.trip(a2, _at(3))
    env.tez(a2, registered=_at(2), last_order=_at(3))
    # a3: только короткий разговор (9 с), сообщение сети и недозвон — диалога нет. Аккаунт создан после
    # первого звонка — не «уже», а без диалога и не «после контакта»; поездка без контакта.
    env.call(a3, _at(4), billsec=9)
    env.call(a3, _at(4, 13), billsec=60, auto_hangup='announcement')
    env.call(a3, _at(4, 14), disposition='NOANSWER', billsec=0)
    env.trip(a3, _at(8))
    env.tez(a3, registered=_at(4, 15), last_order=_at(8))
    # t4: диалог ровно 10 с — считается; зарегистрировался после него; поездки нет.
    env.call(t4, _at(9), billsec=10)
    env.tez(t4, registered=_at(9, 15))
    # n5 (без города): диалог, регистрация и поездка после него.
    env.call(n5, _at(11), billsec=120)
    env.trip(n5, _at(12))
    env.tez(n5, registered=_at(11, 15), last_order=_at(12))
    # t6: не звонили — аккаунт есть, значит «уже зарегистрирован», когда бы ни появился.
    env.tez(t6, registered=_at(20))
    data = env.funnel()
    assert data["totals"] == {"base": 6, "already_registered": 3, "dialog": 4, "registered_after": 2,
                              "orders_after": 2}
    assert data["rates"] == {"dialog_to_registration": 50.0, "dialog_to_orders": 50.0}
    assert data["tez_unchecked"] == 0
    # Города: пробелы по краям не делят город; «не указан» — в конце; крупные — первыми.
    steps = ("city", "base", "already_registered", "dialog", "registered_after", "orders_after",
             "dialog_to_registration", "dialog_to_orders")
    assert [tuple(r[k] for k in steps) for r in data["by_city"]] == [
        ('Алматы', 3, 2, 2, 0, 1, 0.0, 50.0), ('Тараз', 2, 1, 1, 1, 0, 100.0, 0.0), ('', 1, 0, 1, 1, 1, 100.0, 100.0)]
    assert [c["name"] for c in data["options"]["cities"]] == ['Алматы', 'Тараз', '']
    assert data["period"] == '2026-10-01'


def test_trip_after_the_dialog_counts_in_any_month_and_frozen_dates_do_not_hide_it(env):
    leads, _batch = env.base([('7701000000%d' % i, 'Алматы') for i in range(1, 6)])
    l1, l2, l3, l4, l5 = (leads[p] for p in sorted(leads))
    # l1: звонок в конце месяца, поездка в начале следующего — заказа в месяце базы нет, а результат есть.
    env.call(l1, _at(30), billsec=30)
    env.tez(l1, registered=_at(1) - timedelta(days=90), last_order=_at(30) + timedelta(days=3))
    # l2: последняя поездка до диалога — не результат.
    env.call(l2, _at(15), billsec=30)
    env.tez(l2, registered=_at(1) - timedelta(days=90), last_order=_at(10))
    # l3: зарегистрировался и вышел на линию после диалога — дата последней поездки застыла на выходе.
    env.call(l3, _at(5), billsec=30)
    env.trip(l3, _at(7))
    env.tez(l3, registered=_at(6), last_order=_at(7))
    # l4: первый заказ месяца до диалога, но последняя поездка после — результат.
    env.call(l4, _at(20), billsec=30)
    env.trip(l4, _at(7))
    env.tez(l4, registered=_at(1) - timedelta(days=90), last_order=_at(25))
    # l5: вышел на линию после диалога, а последнюю поездку TEZ APP не отдал — решает первый заказ.
    env.call(l5, _at(20), billsec=30)
    env.trip(l5, _at(25))
    env.tez(l5, registered=_at(1) - timedelta(days=90))
    data = env.funnel()
    assert (data["totals"]["dialog"], data["totals"]["orders_after"]) == (5, 4)
    assert (data["totals"]["already_registered"], data["totals"]["registered_after"]) == (4, 1)


def test_start_of_calling_is_the_first_real_call_and_does_not_follow_the_period(env):
    leads, _batch = env.base([('7701000000%d' % i, 'Алматы') for i in range(1, 6)])
    r1, r2, r3, r4, r5 = (leads[p] for p in sorted(leads))
    # r1: недозвон 2.10, аккаунт 3.10, диалог 10.10 — регистрация между звонком и диалогом: ни «уже»,
    # ни «после контакта».
    env.call(r1, _at(2), disposition='NOANSWER', billsec=0)
    env.call(r1, _at(10), billsec=30)
    env.tez(r1, registered=_at(3))
    # r2: звонок, отменённый оператором до ответа, и r3: звонок, не ушедший в Binotel, — не начало обзвона.
    env.call(r2, _at(1), disposition='NOANSWER', billsec=0, cancelled=True)
    env.call(r2, _at(5), billsec=30)
    env.tez(r2, registered=_at(2))
    env.call(r3, _at(1), disposition='', billsec=0, state='failed')
    env.call(r3, _at(5), billsec=30)
    env.tez(r3, registered=_at(2))
    # r4: диалог до периода и в периоде, аккаунт между ними: «после контакта» — только без периода.
    env.call(r4, _at(2), billsec=30)
    env.call(r4, _at(10), billsec=30)
    env.tez(r4, registered=_at(5))
    # r5: аккаунта нет — ни в одном шаге регистрации.
    env.call(r5, _at(10), billsec=30)
    env.tez(r5)
    data = env.funnel()
    assert (data["totals"]["already_registered"], data["totals"]["dialog"], data["totals"]["registered_after"]) == (
        2, 5, 1)
    # Период звонков двигает диалоги и «после контакта», а «уже зарегистрированы» — свойство базы.
    data = env.funnel(date_from=date(2026, 10, 9), date_to=date(2026, 10, 10))
    assert (data["totals"]["already_registered"], data["totals"]["dialog"], data["totals"]["registered_after"]) == (
        2, 3, 0)
    assert data["rates"]["dialog_to_registration"] == 0.0


def test_drivers_tez_app_has_not_answered_about_are_counted_apart(env):
    leads, _batch = env.base([('77010000001', 'Алматы'), ('77010000002', 'Алматы'), ('77010000003', 'Тараз'),
                              ('77010000004', 'Алматы')])
    env.tez(leads['77010000001'], registered=_at(1))
    # Ответил «аккаунта нет» — это проверенный водитель, а не пробел в данных.
    env.tez(leads['77010000004'])
    data = env.funnel()
    assert (data["tez_unchecked"], data["totals"]["already_registered"]) == (2, 1)
    taraz = next(c["key"] for c in data["options"]["cities"] if c["name"] == 'Тараз')
    assert env.funnel(city=taraz)["tez_unchecked"] == 1


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
