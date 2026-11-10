# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: DIAL_LIST_TEST_PORT — порт локального Postgres.

Смена месяца в удалённом КЦ (владелец, 10.11.2026: «у операторов в телефоне контакты за прошлый
месяц — такого вообще не должно быть»): строки водителей прошлой базы месяца телефону не
показываются, «Ещё» не держат и в новую пачку не переезжают; «Позвонить» и «Позвонить ещё раз»
по ним — отказ; вкладки итогов — только обзваниваемая база. В базе при этом ничего не меняется:
снова сделали обзваниваемой прошлую базу — её строки на экране опять.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным именем,
в конце она удаляется. Binotel и Sapar подменены. Запуск на локальном кластере:

    DIAL_LIST_TEST_PORT=55441 python -m pytest tests/test_dial_list_month_base_postgres.py

Люди и номера выдуманы.
"""
import contextlib
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402

PORT = os.environ.get('DIAL_LIST_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DIAL_LIST_TEST_PORT)')

RCC, OPERATOR = 1954, 520
GUARDED = '3.22.34'

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, name text NOT NULL, code text,
                              is_active boolean DEFAULT TRUE);
    CREATE TABLE test_phone_numbers (phone_key varchar(10) UNIQUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, role text,
                        status text NOT NULL DEFAULT 'working', department_id integer,
                        sip_number text);
    CREATE TABLE sip_department_config (department_id integer PRIMARY KEY, provider text,
                                        sip_server text, ring_timeout_rings smallint);
    INSERT INTO departments (id, name, code) VALUES (1954, 'Удаленный КЦ', 'remote_cc');
    INSERT INTO sip_department_config VALUES (1954, 'binotel', 'sip53.binotel.com', NULL);
    INSERT INTO users (id, name, login, role, status, department_id, sip_number) VALUES
        (520, 'Своев Сава', 'sava', 'operator', 'working', 1954, '902');
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


class _Binotel:
    def __init__(self):
        self.originated = []

    def originate_internal_to_external(self, internal_number, external_number, **extra):
        self.originated.append((internal_number, external_number))
        return 'gc-%d-%s' % (len(self.originated), uuid.uuid4().hex[:6])

    def call_details(self, general_call_ids, max_wait=None, timeout=None):
        return {}


class _NoSapar:
    def configured(self):
        return False


def _sync(fn):
    fn()


class _Env:
    def __init__(self, db, svc, binotel):
        self.db, self.svc, self.binotel = db, svc, binotel
        self.now = dial_service.current_period()
        self.prev = (self.now.replace(day=1) - dial_service.timedelta(days=1)).replace(day=1)

    def q(self, sql, params=None):
        with self.db._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def one(self, sql, params=None):
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def portion(self, closed=False):
        return str(self.one("INSERT INTO dial_list_portions (operator_id, department_id, size, closed_at) "
                            "VALUES (%s, %s, 1, CASE WHEN %s THEN CURRENT_TIMESTAMP END) RETURNING id",
                            (OPERATOR, RCC, closed))[0])

    def lead(self, name, phone, period, portion=None, state='issued', result=''):
        """Водитель базы period; с portion — его строка у оператора (state, result)."""
        lead_id = str(self.one("INSERT INTO dial_list_leads (department_id, phone_norm, full_name, period) "
                               "VALUES (%s, %s, %s, %s) RETURNING id", (RCC, phone, name, period))[0])
        if portion is None:
            return lead_id, None
        position = self.one("SELECT COALESCE(MAX(position), 0) + 1 FROM dial_list_assignments "
                            "WHERE portion_id = %s", (portion,))[0]
        asg = str(self.one("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position, state, "
                           "result, done_at) VALUES (%s, %s, %s, %s, %s, %s, "
                           "CASE WHEN %s = 'done' THEN CURRENT_TIMESTAMP END) RETURNING id",
                           (portion, lead_id, OPERATOR, position, state, result, state))[0])
        return lead_id, asg

    def refused(self, fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except dial_service.DialListError as exc:
            return str(exc), exc.status
        return None


@pytest.fixture
def env():
    schema = 't_dial_month_' + uuid.uuid4().hex
    db = _Db(schema)
    admin = db.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
        cur.execute("INSERT INTO dial_list_department_settings (department_id, enabled) VALUES (1954, TRUE)")
        cur.execute("INSERT INTO dial_list_outcomes (department_id, name, position, requeue) VALUES "
                    "(1954, 'Отказ', 1, FALSE), (1954, 'Перезвонить позже', 2, TRUE)")
    binotel = _Binotel()
    svc = dial_service.DialListService(db, client_factory=lambda dep: binotel, sign_checker=_NoSapar(),
                                       calls_submit=_sync)
    svc.operator_context = lambda uid: {
        "user_id": int(uid), "name": "Оператор", "department_id": RCC, "internal_number": '902',
        "settings": svc.department_settings(RCC)}
    try:
        yield _Env(db, svc, binotel)
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


def test_open_portion_of_previous_month_is_hidden_and_does_not_hold_next(env):
    # 10.11.2026 на проде: открытая октябрьская пачка, в ней необработанные строки; ноябрьская база
    # загружена. Раньше телефон показывал октябрьских водителей, а «Ещё» отвечало «осталось N».
    old = env.portion()
    _, old_a = env.lead('Октябрев Олег', '77010000001', env.prev, portion=old)
    old_lead_b, old_b = env.lead('Октябрева Ольга', '77010000002', env.prev, portion=old)
    env.lead('Октябрев Отработанный', '77010000003', env.prev, portion=old, state='done', result='no_answer')
    new_leads = {env.lead('Ноябрев Никита', '77010000011', env.now)[0],
                 env.lead('Ноябрева Нина', '77010000012', env.now)[0]}

    state = env.svc.get_state(OPERATOR, phone_version=GUARDED)
    assert state["portion"] is None                                    # октябрьских строк на экране нет
    assert (state["can_request_next"], state["pool_available"]) == (True, 2)

    # «Позвонить» по строке прошлой базы со старого экрана — отказ; строка в базе не тронута.
    assert env.refused(env.svc.start_call, OPERATOR, old_a) == (dial_service.OLD_BASE_REFUSAL, 409)
    assert env.binotel.originated == []
    # «Позвонить ещё раз» водителю прошлой базы — тоже.
    assert env.refused(env.svc.recall_lead, OPERATOR, old_lead_b) == (dial_service.OLD_BASE_REFUSAL, 409)

    state = env.svc.issue_next_portion(OPERATOR, phone_version=GUARDED)
    assert {i["lead_id"] for i in state["portion"]["items"]} == new_leads   # октябрьские не переехали
    assert state["portion"]["pending"] == 2
    assert env.one("SELECT closed_at IS NOT NULL FROM dial_list_portions WHERE id = %s", (old,))[0] is True
    # Строки прошлой базы — как были: история и сводки за прошлые дни не меняются.
    assert env.q("SELECT state FROM dial_list_assignments WHERE id IN (%s, %s)", (old_a, old_b)) == [
        ('issued',), ('issued',)]


def test_mixed_portion_shows_and_counts_only_the_current_base(env):
    portion = env.portion()
    env.lead('Октябрев Олег', '77010000001', env.prev, portion=portion)
    new_lead, new_asg = env.lead('Ноябрев Никита', '77010000011', env.now, portion=portion)
    env.lead('Ноябрева Нина', '77010000012', env.now)               # в пуле
    state = env.svc.get_state(OPERATOR, phone_version=GUARDED)
    assert [i["lead_id"] for i in state["portion"]["items"]] == [new_lead]
    assert (state["portion"]["pending"], state["can_request_next"]) == (1, False)
    assert env.refused(env.svc.issue_next_portion, OPERATOR, phone_version=GUARDED) == (
        "Сначала обработайте текущий список: осталось 1", 409)


def test_closed_portion_of_previous_month_is_not_shown(env):
    old = env.portion(closed=True)
    env.lead('Октябрев Олег', '77010000001', env.prev, portion=old, state='done', result='no_answer')
    state = env.svc.get_state(OPERATOR, phone_version=GUARDED)
    assert state["portion"] is None


def test_worked_tabs_show_only_the_current_base(env):
    # Обработана в этом месяце, но водитель из прошлой базы (хвост октябрьской пачки, прозвоненный
    # в ноябре) — на вкладках его нет; ноябрьский — есть.
    old = env.portion(closed=True)
    env.lead('Октябрев Олег', '77010000001', env.prev, portion=old, state='done', result='no_answer')
    new = env.portion(closed=True)
    new_lead, _ = env.lead('Ноябрев Никита', '77010000011', env.now, portion=new, state='done', result='no_answer')
    worked = env.svc.operator_worked(OPERATOR)
    assert [i["lead_id"] for i in worked["items"]] == [new_lead]
    assert env.svc.operator_worked(OPERATOR, q='Октябрев')["items"] == []


def test_previous_base_made_active_again_comes_back(env):
    # Руководитель сам сделал обзваниваемой прошлую базу — её строки снова на экране (в базе их не
    # меняли), а строки нынешнего месяца — нет.
    portion = env.portion()
    old_lead, _ = env.lead('Октябрев Олег', '77010000001', env.prev, portion=portion)
    env.lead('Ноябрев Никита', '77010000011', env.now, portion=portion)
    env.q("UPDATE dial_list_department_settings SET active_period = %s WHERE department_id = %s",
          (env.prev, RCC))
    state = env.svc.get_state(OPERATOR, phone_version=GUARDED)
    assert [i["lead_id"] for i in state["portion"]["items"]] == [old_lead]
