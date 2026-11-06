# -*- coding: utf-8 -*-
"""«Воронка ОП» Тез во вкладке «Аналитика» (задача #407 Алчинбаевой Анель, 03.11.2026) на настоящем
PostgreSQL. С 06.11.2026 воронка считается по базе успешек (tez_leads — вкладка «Успешки») и её звонкам
Binotel (tez_lead_calls): база → уже зарегистрированы → диалог → зарегистрировались после контакта →
вышли к заказам по городам, фильтры месяца, файла, дней и города, причины отказа (итоги автообзвона
отдела по номеру). Здесь же — запись ответа TEZ APP о водителе в базу успешек (сверка, перенос, разовая
досверка), город из файла и его путь через удаление и возврат загрузки.

Таблицы базы успешек — тем же SQL, что на проде (кусок схемы из склейки исходника), методы Database —
из той же склейки (tests/source_cache): копий в тесте нет. Схема автообзвона — dial_list.schema.

Запуск: DIAL_LIST_TEST_PORT=<порт локального PostgreSQL> pytest tests/test_dial_list_tez_funnel_postgres.py
(пользователь postgres без пароля; каждый тест — в своей схеме, схема удаляется)."""
import ast
import contextlib
import json
import os
import sys
import textwrap
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import analytics as dial_analytics  # noqa: E402
from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402
from tests import source_cache  # noqa: E402

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
'''
BASE_SEED = '''
    INSERT INTO departments (id, name, code) VALUES (560, 'Тез КЦ', 'tez'), (1954, 'Удаленный КЦ', 'remote_cc');
    INSERT INTO sip_department_config VALUES (560, 'binotel', 'sip53.binotel.com', NULL),
                                             (1954, 'binotel', 'sip53.binotel.com', NULL);
    INSERT INTO users (id, name, login, role, status, department_id, sip_number) VALUES
        (1, 'Админ', 'admin', 'admin', 'working', NULL, NULL),
        (530, 'Ашимова Айгерим', 'aigerim', 'operator', 'working', 560, '601');
'''

SOURCE = source_cache.database_flat_path().read_text(encoding='utf-8-sig')
_LINES = SOURCE.splitlines(keepends=True)


def _tez_schema_sql():
    """Кусок боевой схемы «Успешки TEZ ОП»: все таблицы базы успешек, их колонки, индексы и блоки."""
    start = SOURCE.index('-- ======================= Успешки TEZ ОП')
    end = SOURCE.index('-- Бэкофилл привязки направлений к отделу', start)
    return SOURCE[start:end]


_TEZ_METHODS = ('create_tez_lead_batch', 'import_tez_lead_rows', 'get_tez_phones_pending_first_order',
                'save_tez_first_orders', 'save_tez_carry_first_orders', 'delete_tez_lead_batch',
                'restore_tez_lead_batch', '_lock_tez_lead_batch_for_change_tx')
_TEZ_STATIC = ('_tez_actor_name_tx', '_tez_resync_leads_from_rows_tx')


def _tez_database_class():
    """Методы Database базы успешек из склейки исходника — без импорта приложения (db.core на Windows
    не импортируется: time.tzset)."""
    from psycopg2.extras import Json, execute_values

    module = source_cache.parse(SOURCE)
    database = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'Database')
    nodes = {n.name: n for n in database.body if isinstance(n, ast.FunctionDef)}
    lock_ns = next(n for n in database.body if isinstance(n, ast.Assign)
                   and any(getattr(t, 'id', '') == 'TEZ_LEAD_PERIOD_LOCK_NAMESPACE' for t in n.targets))
    namespace = {'execute_values': execute_values, 'Json': Json, 'datetime': datetime, 'timedelta': timedelta}
    attrs = {'TEZ_LEAD_PERIOD_LOCK_NAMESPACE': ast.literal_eval(lock_ns.value)}
    for name in _TEZ_METHODS + _TEZ_STATIC + ('_lock_tez_lead_period_tx',):
        node = nodes[name]
        lines = list(_LINES[node.lineno - 1:node.end_lineno])
        exec(textwrap.dedent(''.join(lines)), namespace)
        attrs[name] = namespace[name]
    for name in _TEZ_STATIC:
        attrs[name] = staticmethod(attrs[name])
    attrs['_lock_tez_lead_period_tx'] = classmethod(attrs['_lock_tez_lead_period_tx'])
    return type('TezDatabase', (), attrs)


_TezDatabase = _tez_database_class()


class _Db(_TezDatabase):
    """База теста: соединения переиспользуются (подключение на Windows — ~0,1 с), вложенный вызов
    получает своё, как из пула приложения."""
    _SIP_INACTIVE_STATUSES = ('fired', 'dismissal')

    def __init__(self, schema):
        self.schema = schema
        self._free = []

    def close(self):
        while self._free:
            self._free.pop().close()

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                                connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.schema)
            # С запасом: на медленном диске контрольная точка локального сервера стопорит запросы на секунды.
            cur.execute("SET statement_timeout TO '30s'")
            # Сотни DDL схемы — каждая своей фиксацией: не ждать сброса журнала на диск (только скорость).
            cur.execute("SET synchronous_commit TO off")
        conn.commit()
        return conn

    @contextlib.contextmanager
    def _get_cursor(self):
        conn = self._free.pop() if self._free else self.connect()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            self._free.append(conn)


def _at(day, hour=12, month=10):
    """Момент дня 2026 года по Алматы (по умолчанию — октября)."""
    return datetime(2026, month, day, hour, 0, tzinfo=ALMATY)


class _Env:
    def __init__(self, db, svc):
        self.db, self.svc = db, svc
        self.portion = None
        self.call_seq = 0

    def q(self, sql, params=None):
        with self.db._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def one(self, sql, params=None):
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def base(self, rows, file_name='база.xlsx', period=PERIOD):
        """rows: [(номер, город)] → база успешек месяца настоящей загрузкой; ({номер: lead_id}, batch_id)."""
        batch_id = self.db.create_tez_lead_batch(TEZ, period.year, period.month, 1, file_name)
        parsed = [(i, f'Водитель {i}', '8' + phone[1:], phone, city) for i, (phone, city) in enumerate(rows, 2)]
        self.db.import_tez_lead_rows(batch_id, period.year, period.month, parsed)
        ids = {phone: str(self.one("SELECT id FROM tez_leads WHERE year = %s AND month = %s AND phone_norm = %s",
                                   (period.year, period.month, phone))[0]) for phone, _city in rows}
        return ids, batch_id

    def call(self, phone, when, billsec=30, operator=OPERATOR, call_type=1):
        """Звонок зеркала Binotel: исходящий (call_type 1) оператора ОП; от 10 с — квалифицирующий."""
        self.call_seq += 1
        qualifying = call_type == 1 and billsec >= 10 and operator is not None
        self.q("INSERT INTO tez_lead_calls (general_call_id, phone_norm, started_at, call_type, billsec, "
               "disposition, operator_id, is_qualifying) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
               (f'g{self.call_seq}', phone, when, call_type, billsec, 'ANSWER' if billsec else 'NOANSWER',
                operator, qualifying))

    def tez(self, lead_id, registered=None, last_order=None, month_first=None, carry_first=None):
        """Сохранённый ответ TEZ APP о водителе базы успешек."""
        self.q("UPDATE tez_leads SET registered_at = %s, last_order_at = %s, month_first_order_at = %s, "
               "carry_first_order_at = %s, account_checked_at = now() WHERE id = %s",
               (registered, last_order, month_first, carry_first, lead_id))

    def outcome(self, name):
        return str(self.one("SELECT id FROM dial_list_outcomes WHERE department_id = %s AND name = %s",
                            (TEZ, name))[0])

    def reason(self, outcome_id, name, position=1, active=True):
        return str(self.one("INSERT INTO dial_list_outcome_subtypes (outcome_id, department_id, name, position, "
                            "is_active) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                            (outcome_id, TEZ, name, position, active))[0])

    def autodial_outcome(self, phone, when, outcome, reason=None):
        """Итог попытки автообзвона отдела по водителю с этим номером (карточка iCORE Phone)."""
        lead = self.one("SELECT id FROM dial_list_leads WHERE department_id = %s AND phone_norm = %s", (TEZ, phone))
        if lead is None:
            self.svc.import_leads(TEZ, 1, 'обзвон.xlsx', [(2, 'Водитель', '8' + phone[1:], phone, '', '')],
                                  period=PERIOD, require_iin=False)
            lead = self.one("SELECT id FROM dial_list_leads WHERE department_id = %s AND phone_norm = %s",
                            (TEZ, phone))
        if self.portion is None:
            self.portion = str(self.one("INSERT INTO dial_list_portions (operator_id, department_id, size) "
                                        "VALUES (%s, %s, 50) RETURNING id", (OPERATOR, TEZ))[0])
        assignment = self.one("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position, state) "
                              "VALUES (%s, %s, %s, 1, 'done') RETURNING id", (self.portion, lead[0], OPERATOR))[0]
        self.q("INSERT INTO dial_list_attempts (assignment_id, operator_id, requested_at, state, disposition, "
               "billsec, outcome_id, outcome_subtype_id) VALUES (%s, %s, %s, 'finished', 'ANSWER', 30, %s, %s)",
               (assignment, OPERATOR, when, outcome, reason))

    def funnel(self, **filters):
        filters.setdefault('period', PERIOD)
        return self.svc.funnel(TEZ, **filters)


@pytest.fixture(scope='module')
def schema_db():
    """Схема одна на файл: сотни DDL обеих баз — секунды, а между тестами хватает TRUNCATE."""
    schema = 't_dial_funnel_' + uuid.uuid4().hex
    db = _Db(schema)
    admin = db.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        cur.execute(_tez_schema_sql())
        dial_schema.init_dial_list_schema(cur)
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename", (schema,))
        tables = [f'{schema}."{name}"' for (name,) in cur.fetchall()]
    try:
        yield db, admin, tables
    finally:
        db.close()
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


@pytest.fixture
def env(schema_db):
    db, admin, tables = schema_db
    with admin.cursor() as cur:
        # Строк в тестах единицы: DELETE без проверки ключей быстрее TRUNCATE, который на Windows
        # пересоздаёт файл каждой таблицы.
        cur.execute("SET session_replication_role = replica")
        cur.execute('; '.join('DELETE FROM ' + table for table in tables))
        cur.execute("SET session_replication_role = DEFAULT")
        cur.execute(BASE_SEED)
        cur.execute("INSERT INTO dial_list_department_settings (department_id, enabled, binotel_company) "
                    "VALUES (560, FALSE, 'remote_cc'), (1954, TRUE, 'remote_cc')")
    svc = dial_service.DialListService(db, client_factory=lambda dep: None, calls_submit=lambda fn: fn(),
                                       trips_fetch=lambda phones, month, timeout=None: {},
                                       trips_configured=lambda: True)
    # Тез КЦ переводит админ; итоги — набор Тез (в нём «Отказ»).
    svc.enroll_department(TEZ, changed_by=1, profile='tez')
    yield _Env(db, svc)


# ── воронка ──────────────────────────────────────────────────────────────────

def test_steps_count_each_driver_once_and_only_results_after_the_dialog(env):
    leads, _batch = env.base([('77010000001', 'Алматы'), ('77010000002', 'Алматы'), ('77010000003', ' Алматы '),
                              ('77010000004', 'Тараз'), ('77010000005', ''), ('77010000006', 'Тараз')])
    a1, a2, a3, t4, n5, t6 = (leads[p] for p in sorted(leads))
    # a1: зарегистрирован до звонков; два диалога — один водитель; первый заказ месяца после диалога.
    env.call('77010000001', _at(5), billsec=40)
    env.call('77010000001', _at(6), billsec=25)
    env.tez(a1, registered=_at(1) - timedelta(days=30), month_first=_at(7), last_order=_at(7))
    # a2: диалог, но поездка была ДО него — в «после контакта» не идёт; аккаунт — до первого звонка.
    env.call('77010000002', _at(10), billsec=15)
    env.tez(a2, registered=_at(2), month_first=_at(3), last_order=_at(3))
    # a3: короткий разговор (9 с) и недозвон — диалога нет; входящий от водителя и исходящий не оператора
    # ОП (линия) — не звонки обзвона. Аккаунт создан после первого звонка ОП — не «уже», а без диалога и
    # не «после контакта»; поездка без контакта.
    env.call('77010000003', _at(2), billsec=60, call_type=0)
    env.call('77010000003', _at(3), billsec=60, operator=None)
    env.call('77010000003', _at(4), billsec=9)
    env.call('77010000003', _at(4, 14), billsec=0)
    env.tez(a3, registered=_at(4, 15), month_first=_at(8), last_order=_at(8))
    # t4: диалог ровно 10 с — считается; зарегистрировался после него; поездки нет.
    env.call('77010000004', _at(9), billsec=10)
    env.tez(t4, registered=_at(9, 15))
    # n5 (без города): диалог, регистрация и поездка после него.
    env.call('77010000005', _at(11), billsec=120)
    env.tez(n5, registered=_at(11, 15), month_first=_at(12), last_order=_at(12))
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
    assert (data["period"], data["call_window"]) == ('2026-10-01', {"from": '2026-09-24', "to": '2026-10-31'})
    # Номера сводят базу со звонками, но в ответ не уходят — ни целиком, ни хвостом.
    assert '7010000' not in json.dumps(data, ensure_ascii=False, default=str)


def test_dialogs_without_days_are_the_window_of_the_base_month_like_reached(env):
    leads, _batch = env.base([('7701000000%d' % i, 'Алматы') for i in range(1, 5)])
    l1, l2, l3, l4 = (leads[p] for p in sorted(leads))
    # l1: разговор 20.09 — раньше окна (оно с 24.09): ни диалог, ни начало обзвона; аккаунт после него — «уже».
    env.call('77010000001', _at(20, month=9))
    env.tez(l1, registered=_at(22, month=9))
    # l2: разговор 24.09 в полночь — первый день окна.
    env.call('77010000002', datetime(2026, 9, 24, 0, 0, tzinfo=ALMATY))
    # l3: разговор 5.11 — месяц доработки, за окном; виден, если выбрать дни.
    env.call('77010000003', _at(5, month=11))
    env.tez(l3, month_first=None, carry_first=_at(6, month=11))
    # l4: последний час окна — 31.10 23:00.
    env.call('77010000004', _at(31, 23))
    data = env.funnel()
    assert (data["totals"]["dialog"], data["totals"]["already_registered"]) == (2, 1)
    # «Состоялся диалог» без дней — то же, что «Дозвонились» вкладки «Успешки» (db.get_tez_lead_funnel).
    reached = env.one("""
        SELECT COUNT(DISTINCT l.phone_norm) FILTER (WHERE c.is_qualifying)
        FROM tez_leads l LEFT JOIN tez_lead_calls c ON c.phone_norm = l.phone_norm AND c.call_type = 1
             AND c.operator_id IS NOT NULL AND c.started_at >= %s AND c.started_at < %s
        WHERE l.year = 2026 AND l.month = 10
    """, (datetime(2026, 9, 24, tzinfo=ALMATY), datetime(2026, 11, 1, tzinfo=ALMATY)))[0]
    assert reached == data["totals"]["dialog"]
    data = env.funnel(date_from=date(2026, 11, 1), date_to=date(2026, 11, 10))
    assert (data["totals"]["dialog"], data["totals"]["orders_after"]) == (1, 1)


def test_period_bounds_dialogs_and_trips_count_after_the_first_dialog_inside_it(env):
    leads, _batch = env.base([('77010000001', 'Алматы'), ('77010000002', 'Алматы'), ('77010000003', 'Алматы')])
    a1, a2, a3 = (leads[p] for p in sorted(leads))
    env.call('77010000001', _at(2))           # до периода
    env.call('77010000001', _at(10))          # в периоде
    env.tez(a1, month_first=_at(5))           # после первого диалога вообще, но ДО диалога в периоде
    env.call('77010000002', _at(10, 23))      # последний час последнего дня периода — внутри
    env.tez(a2, month_first=_at(14))
    env.call('77010000003', _at(11, 0))       # полночь следующего дня — уже вне периода
    env.tez(a3, month_first=_at(12))
    data = env.funnel(date_from=date(2026, 10, 9), date_to=date(2026, 10, 10))
    assert (data["totals"]["base"], data["totals"]["dialog"], data["totals"]["orders_after"]) == (3, 2, 1)
    assert data["filters"]["date_from"] == '2026-10-09'
    # Без периода у a1 первый диалог 2.10 — поездка 5.10 уже «после контакта».
    data = env.funnel()
    assert (data["totals"]["dialog"], data["totals"]["orders_after"]) == (3, 3)


def test_trip_after_the_dialog_counts_in_any_month(env):
    leads, _batch = env.base([('7701000000%d' % i, 'Алматы') for i in range(1, 6)])
    l1, l2, l3, l4, l5 = (leads[p] for p in sorted(leads))
    # l1: звонок в конце месяца, поездка в начале следующего (месяц доработки) — результат есть.
    env.call('77010000001', _at(30))
    env.tez(l1, carry_first=_at(2, month=11), last_order=_at(2, month=11))
    # l2: последняя поездка до диалога — не результат.
    env.call('77010000002', _at(15))
    env.tez(l2, last_order=_at(10))
    # l3: первый заказ месяца после диалога.
    env.call('77010000003', _at(5))
    env.tez(l3, month_first=_at(7), last_order=_at(7))
    # l4: первый заказ месяца до диалога, но последняя поездка после — результат.
    env.call('77010000004', _at(20))
    env.tez(l4, month_first=_at(7), last_order=_at(25))
    # l5: только последняя поездка (через месяц) — результат.
    env.call('77010000005', _at(20))
    env.tez(l5, last_order=_at(20, month=11))
    data = env.funnel()
    assert (data["totals"]["dialog"], data["totals"]["orders_after"]) == (5, 4)


def test_start_of_calling_is_the_first_op_call_and_does_not_follow_the_period(env):
    leads, _batch = env.base([('7701000000%d' % i, 'Алматы') for i in range(1, 5)])
    r1, r2, r4, r5 = (leads[p] for p in sorted(leads))
    # r1: недозвон 2.10, аккаунт 3.10, диалог 10.10 — регистрация между звонком и диалогом: ни «уже»,
    # ни «после контакта».
    env.call('77010000001', _at(2), billsec=0)
    env.call('77010000001', _at(10))
    env.tez(r1, registered=_at(3))
    # r2: входящий от водителя и звонок линии 1.10 — не начало обзвона; аккаунт 2.10, диалог 5.10 — «уже».
    env.call('77010000002', _at(1), call_type=0)
    env.call('77010000002', _at(1, 13), operator=None)
    env.call('77010000002', _at(5))
    env.tez(r2, registered=_at(2))
    # r4: диалог до периода и в периоде, аккаунт между ними: «после контакта» — только без периода.
    env.call('77010000003', _at(2))
    env.call('77010000003', _at(10))
    env.tez(r4, registered=_at(5))
    # r5: аккаунта нет — ни в одном шаге регистрации.
    env.call('77010000004', _at(10))
    env.tez(r5)
    data = env.funnel()
    assert (data["totals"]["already_registered"], data["totals"]["dialog"], data["totals"]["registered_after"]) == (
        1, 4, 1)
    # Дни звонков двигают диалоги и «после контакта», а «уже зарегистрированы» — свойство базы.
    data = env.funnel(date_from=date(2026, 10, 9), date_to=date(2026, 10, 10))
    assert (data["totals"]["already_registered"], data["totals"]["dialog"], data["totals"]["registered_after"]) == (
        1, 3, 0)
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


def test_no_dialogs_give_zero_percent_not_an_error(env):
    env.base([('77010000001', 'Алматы')])
    data = env.funnel()
    assert data["totals"]["dialog"] == 0
    assert data["rates"] == {"dialog_to_registration": 0.0, "dialog_to_orders": 0.0}
    assert data["by_city"][0]["dialog_to_orders"] == 0.0
    # Пустой месяц — пустые разрезы и нули, но сам месяц в выборе есть.
    empty = env.funnel(period=date(2026, 11, 1))
    assert (empty["totals"]["base"], empty["by_city"], empty["refusals"]["total"]) == (0, [], 0)
    assert [m["period"] for m in empty["options"]["months"]] == ['2026-11-01', '2026-10-01']


def test_months_of_the_successes_base_and_the_default_month(env):
    env.base([('77010000001', 'Алматы')], period=date(2026, 9, 1))
    env.base([('77010000001', 'Алматы'), ('77010000002', 'Алматы'), ('77019999999', 'Алматы')])
    env.q("INSERT INTO test_phone_numbers (phone_key) VALUES ('7019999999')")
    data = env.funnel()
    assert data["options"]["months"] == [{"period": '2026-10-01', "base": 2}, {"period": '2026-09-01', "base": 1}]
    # Без месяца — текущий, как во вкладке «Успешки».
    data = env.svc.funnel(TEZ)
    assert data["period"] == dial_service.current_period().isoformat()


def test_file_and_city_filters(env):
    first, batch1 = env.base([('77010000001', 'Алматы'), ('77010000002', 'Тараз')], file_name='первая.xlsx')
    second, batch2 = env.base([('77010000003', 'Тараз'), ('77010000004', '')], file_name='добор.xlsx')
    env.call('77010000003', _at(5))
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
    # Повторная загрузка того же номера с городом ставит его и старой строке, а пустой город не стирает.
    env.base([('77010000004', 'Шымкент')], file_name='города.xlsx')
    env.base([('77010000004', '')], file_name='без города.xlsx')
    assert env.one("SELECT city FROM tez_leads WHERE phone_norm = '77010000004'")[0] == 'Шымкент'
    # Удалённая загрузка из выбора файлов уходит.
    env.q("UPDATE tez_lead_batches SET deleted_at = now() WHERE id = %s", (batch1,))
    assert 'первая.xlsx' not in [b["file_name"] for b in env.funnel()["options"]["batches"]]


def test_refusal_reasons_are_the_last_autodial_refusal_of_each_driver_in_the_period(env):
    phones = ['7701000000%d' % i for i in range(1, 7)]
    env.base([(p, 'Алматы') for p in phones])
    refusal = env.outcome('Отказ')
    expensive = env.reason(refusal, 'Дорого', 1)
    other_park = env.reason(refusal, 'Работает в другом парке', 2)
    callback = env.outcome('Перезвонить')
    env.autodial_outcome(phones[0], _at(5), refusal, expensive)
    env.autodial_outcome(phones[1], _at(5), refusal, expensive)
    # Третий сначала «дорого», потом «в другом парке» — считается один раз, с последней причиной.
    env.autodial_outcome(phones[2], _at(5), refusal, expensive)
    env.autodial_outcome(phones[2], _at(7), refusal, other_park)
    # Четвёртый — отказ без причины (телефон до причин): строка «без причины».
    env.autodial_outcome(phones[3], _at(6), refusal)
    # Пятый — не отказ.
    env.autodial_outcome(phones[4], _at(6), callback)
    # Шестой — отказ вне периода.
    env.autodial_outcome(phones[5], _at(20), refusal, expensive)
    # Водитель не из базы успешек этого месяца — не в отчёте.
    env.autodial_outcome('77010000099', _at(5), refusal, expensive)
    data = env.funnel(date_to=date(2026, 10, 15))
    refusals = data["refusals"]
    assert refusals["total"] == 4
    assert [(r["name"], r["count"], r["share"]) for r in refusals["reasons"]] == [
        ('Дорого', 2, 50.0), ('Работает в другом парке', 1, 25.0), ('', 1, 25.0)]
    assert refusals["reasons"][2]["subtype_id"] is None
    assert refusals["outcomes"] == [{"name": 'Отказ', "reasons": 2}]
    # Без периода (окно месяца базы) шестой тоже в отчёте.
    assert env.funnel()["refusals"]["total"] == 5
    # Город фильтрует и отчёт об отказах.
    assert env.funnel(city=dial_analytics.FUNNEL_CITY_NONE)["refusals"]["total"] == 0


def test_refusal_outcome_is_found_by_name_whatever_the_case(env):
    env.base([('77010000001', 'Алматы')])
    env.q("UPDATE dial_list_outcomes SET name = '  отказ водителя' WHERE department_id = %s AND name = 'Отказ'",
          (TEZ,))
    refusal = env.outcome('  отказ водителя')
    env.autodial_outcome('77010000001', _at(5), refusal)
    data = env.funnel()
    assert (data["refusals"]["total"], data["refusals"]["outcomes"]) == (1, [{"name": '  отказ водителя', "reasons": 0}])
    # Выключенный итог-отказ: история считается, в списке «настроенных» его нет.
    env.q("UPDATE dial_list_outcomes SET is_active = FALSE WHERE id = %s", (refusal,))
    data = env.funnel()
    assert (data["refusals"]["total"], data["refusals"]["outcomes"]) == (1, [])
    assert dial_analytics.is_refusal_outcome('ОТКАЗ') and not dial_analytics.is_refusal_outcome('Перезвонить')


def test_test_numbers_are_not_drivers(env):
    env.base([('77010000001', 'Алматы'), ('77019999999', 'Алматы')])
    env.q("INSERT INTO test_phone_numbers (phone_key) VALUES ('7019999999')")
    env.call('77019999999', _at(5))
    env.autodial_outcome('77019999999', _at(5), env.outcome('Отказ'))
    data = env.funnel()
    assert (data["totals"]["base"], data["totals"]["dialog"], data["refusals"]["total"]) == (1, 0, 0)


def test_funnel_is_tez_only(env):
    with pytest.raises(dial_service.DialListError) as refused:
        env.svc.funnel(RCC, period=PERIOD)
    assert refused.value.status == 400
    with pytest.raises(dial_service.DialListError):
        env.funnel(date_from=date(2026, 10, 10), date_to=date(2026, 10, 9))


# ── ответ TEZ APP в базе успешек ─────────────────────────────────────────────

def test_tez_app_answer_is_asked_once_for_everyone_then_only_for_pending(env):
    leads, _batch = env.base([('77010000001', ''), ('77010000002', ''), ('77010000003', '')])
    # Выехал ещё до полей TEZ APP (поездка в месяце известна, об аккаунте не спрашивали).
    env.q("UPDATE tez_leads SET month_first_order_at = %s WHERE id = %s", (_at(3), leads['77010000001']))
    assert env.db.get_tez_phones_pending_first_order(2026, 10) == ['77010000001', '77010000002', '77010000003']
    env.db.save_tez_first_orders(2026, 10, {
        '77010000001': {'month': _at(3), 'registered': _at(1, month=9), 'last_order': _at(20)},
        '77010000002': {'month': None, 'registered': _at(2), 'last_order': _at(2, month=9)},
        '77010000003': {'month': None, 'registered': None, 'last_order': None},
    })
    rows = env.q("SELECT phone_norm, registered_at, last_order_at, account_checked_at IS NOT NULL, "
                 "month_first_order_at FROM tez_leads ORDER BY phone_norm")
    assert rows == [('77010000001', _at(1, month=9), _at(20), True, _at(3)),
                    ('77010000002', _at(2), _at(2, month=9), True, None),
                    ('77010000003', None, None, True, None)]
    # Дальше — только те, у кого поездки в месяце нет.
    assert env.db.get_tez_phones_pending_first_order(2026, 10) == ['77010000002', '77010000003']
    # Пропажа полей из ответа не стирает сохранённое; последняя поездка только растёт.
    env.db.save_tez_first_orders(2026, 10, {
        '77010000002': {'month': None, 'registered': None, 'last_order': _at(1, month=8)},
        '77010000003': {'month': None, 'registered': _at(25), 'last_order': _at(26)},
    })
    assert env.q("SELECT phone_norm, registered_at, last_order_at FROM tez_leads WHERE phone_norm <> '77010000001' "
                 "ORDER BY phone_norm") == [('77010000002', _at(2), _at(2, month=9)),
                                            ('77010000003', _at(25), _at(26))]


def test_month_of_rework_freshens_registration_of_the_last_month_base(env):
    leads, _batch = env.base([('77010000001', ''), ('77010000002', '')])
    env.q("UPDATE tez_leads SET status = 'success' WHERE id = %s", (leads['77010000002'],))
    env.db.save_tez_carry_first_orders(2026, 11, {
        '77010000001': {'month': _at(4, month=11), 'registered': _at(30), 'last_order': _at(5, month=11)},
        '77010000002': {'month': _at(4, month=11), 'registered': _at(30), 'last_order': _at(5, month=11)},
    })
    assert env.q("SELECT phone_norm, carry_first_order_at, registered_at, last_order_at, "
                 "account_checked_at IS NOT NULL FROM tez_leads ORDER BY phone_norm") == [
        ('77010000001', _at(4, month=11), _at(30), _at(5, month=11), True),
        # Успешка октября в доработку не идёт — её строку перенос не трогает.
        ('77010000002', None, None, None, False)]


def test_city_survives_deleting_and_restoring_the_file(env):
    leads, batch = env.base([('77010000001', 'Тараз'), ('77010000002', '')], file_name='база.xlsx')
    env.tez(leads['77010000001'], registered=_at(1))
    deleted = env.db.delete_tez_lead_batch(batch, 1, reason='проверка')
    assert deleted and deleted.get('leads_removed') == 2
    assert env.one("SELECT COUNT(*) FROM tez_leads")[0] == 0
    restored = env.db.restore_tez_lead_batch(batch, 1, reason='проверка')
    assert restored and not restored.get('not_deleted')
    # Город вернулся из архива; ответ TEZ APP не архивируется — сверка спросит вернувшегося заново.
    assert env.q("SELECT phone_norm, city, account_checked_at IS NULL FROM tez_leads ORDER BY phone_norm") == [
        ('77010000001', 'Тараз', True), ('77010000002', '', True)]
    assert env.db.get_tez_phones_pending_first_order(2026, 10) == ['77010000001', '77010000002']
