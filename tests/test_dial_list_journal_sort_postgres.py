# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: DIAL_LIST_TEST_PORT — порт локального Postgres.

Сортировка «Журнала» удалённого КЦ (владелец, 10.11.2026): «Сначала свежие» стали «Сначала новые»,
добавилась обратная «Сначала старые». Что защищаем:
  • «Сначала старые» — ровно обратный порядок «Сначала новых», по последнему действию с водителем:
    звонку или загрузке в базу (позвонили позже всех — последний, хоть и загружен раньше всех);
  • база одного файла без звонков — одно время загрузки на всех — встаёт в закреплённом порядке
    (последний ключ каждой сортировки — id) и листается страницами «Показать ещё» без повторов
    и пропусков.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным именем,
в конце она удаляется. Запуск на локальном кластере:

    DIAL_LIST_TEST_PORT=55441 python -m pytest tests/test_dial_list_journal_sort_postgres.py

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

# Направление каждой сортировки журнала: по нему же идут равные строки (по id).
DIRECTION = {"activity": "desc", "activity_asc": "asc", "name": "asc", "created": "desc", "attempts": "desc"}


class _Db:
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


class _NoSapar:
    def configured(self):
        return False


class _Env:
    def __init__(self, db, svc):
        self.db, self.svc = db, svc
        self.period = dial_service.current_period()

    def q(self, sql, params=None):
        with self.db._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def upload(self, rows, at):
        """Водители одним файлом: [(id, ФИО, номер)] одной вставкой — одно время загрузки `at` на всех."""
        values = ", ".join(["(%s::uuid, %s, %s, %s, %s, %s::timestamptz)"] * len(rows))
        params = []
        for lead_id, name, phone in rows:
            params += [lead_id, RCC, phone, name, self.period, at]
        self.q("INSERT INTO dial_list_leads (id, department_id, phone_norm, full_name, period, created_at) "
               "VALUES " + values, params)

    def call(self, lead_id, at):
        """Звонок оператора водителю в момент `at` — последнее действие с ним."""
        portion = self.q("INSERT INTO dial_list_portions (operator_id, department_id, size, closed_at) "
                         "VALUES (%s, %s, 1, %s::timestamptz) RETURNING id", (OPERATOR, RCC, at))[0][0]
        asg = self.q("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position, state, result, "
                     "done_at) VALUES (%s, %s::uuid, %s, 1, 'done', 'no_answer', %s::timestamptz) RETURNING id",
                     (portion, lead_id, OPERATOR, at))[0][0]
        self.q("INSERT INTO dial_list_attempts (assignment_id, operator_id, requested_at, state, disposition) "
               "VALUES (%s, %s, %s::timestamptz, 'finished', 'NOANSWER')", (asg, OPERATOR, at))

    def order(self, sort, limit=200, offset=0):
        page = self.svc.leads_journal(RCC, sort=sort, limit=limit, offset=offset)
        assert page["sort"] == sort
        return [item["id"] for item in page["items"]]


@pytest.fixture
def env():
    schema = 't_dial_sort_' + uuid.uuid4().hex
    db = _Db(schema)
    admin = db.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
        cur.execute("INSERT INTO dial_list_department_settings (department_id, enabled) VALUES (1954, TRUE)")
    svc = dial_service.DialListService(db, sign_checker=_NoSapar())
    try:
        yield _Env(db, svc)
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


def _ids(count):
    """count id по возрастанию и порядок вставки, не совпадающий ни с прямым, ни с обратным:
    без id последним ключом равные строки встали бы как легли в таблицу, а не по id."""
    ids = sorted(str(uuid.uuid4()) for _ in range(count))
    shuffled = ids[1::2] + ids[0::2]
    assert shuffled not in (ids, ids[::-1])
    return ids, shuffled


def test_oldest_first_is_the_mirror_of_newest_first_by_last_action(env):
    file_ids, shuffled = _ids(5)
    early, called = str(uuid.uuid4()), str(uuid.uuid4())
    # Позвонили позже всех, хотя загружен раньше всех: последнее действие — звонок.
    env.upload([(called, 'Звонков Захар', '77010000001')], '2026-11-01 08:00+05')
    env.upload([(early, 'Ранов Роман', '77010000002')], '2026-11-01 09:00+05')
    env.upload([(lead_id, 'Файлов Фёдор', '7701000001%d' % n) for n, lead_id in enumerate(shuffled)],
               '2026-11-02 10:00+05')
    env.call(called, '2026-11-03 12:00+05')

    newest = env.order('activity')
    assert newest == [called] + file_ids[::-1] + [early]
    oldest = env.order('activity_asc')
    assert oldest == [early] + file_ids + [called]
    assert oldest == newest[::-1]


@pytest.mark.parametrize('sort', sorted(DIRECTION))
def test_one_upload_file_pages_without_repeats_or_gaps(env, sort):
    assert set(DIRECTION) == set(dial_service.JOURNAL_SORTS)    # новая сортировка — сюда же
    ids, shuffled = _ids(7)
    env.upload([(lead_id, 'Файлов Фёдор', '7701000002%d' % n) for n, lead_id in enumerate(shuffled)],
               '2026-11-02 10:00+05')
    expected = ids if DIRECTION[sort] == 'asc' else ids[::-1]
    assert env.order(sort) == expected
    pages = env.order(sort, limit=3) + env.order(sort, limit=3, offset=3) + env.order(sort, limit=3, offset=6)
    assert pages == expected


def test_unknown_sort_falls_back_to_newest_first(env):
    ids, shuffled = _ids(3)
    env.upload([(lead_id, 'Файлов Фёдор', '7701000003%d' % n) for n, lead_id in enumerate(shuffled)],
               '2026-11-02 10:00+05')
    page = env.svc.leads_journal(RCC, sort='fresh')
    assert page["sort"] == 'activity'
    assert [item["id"] for item in page["items"]] == ids[::-1]
