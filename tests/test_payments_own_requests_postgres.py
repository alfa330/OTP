# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: PAYMENTS_TEST_PORT — порт локального Postgres.

«Мои заявки» на рабочем столе «Оплаты счетов» (просьба владельца 22.10.2026). В
tests/test_payments_own_requests.py запросы подменены и видно только, что они НАПИСАНЫ.
Что они ЗНАЧАТ — какие заявки попадают в блок, в каком порядке, сколько их на счётчике и
что ни одна заявка не стоит одновременно в задачах стола и в блоке, — исполняется здесь,
на настоящей схеме раздела (payments.schema).

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным
именем, в конце она удаляется. Запуск на локальном кластере (пароль, если он есть, —
через PGPASSWORD):

    PAYMENTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_payments_own_requests_postgres.py

Люди выдуманы.
"""
import contextlib
import os
import uuid

import pytest

from payments import queries, schema

PORT = os.environ.get('PAYMENTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (PAYMENTS_TEST_PORT)')

INITIATOR, OTHER, APPROVER = 10, 30, 70

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL DEFAULT 'operator',
                        status text NOT NULL DEFAULT 'working', department_id integer, supervisor_id integer,
                        telegram_id bigint, job_title text, avatar_bucket text, avatar_blob_path text);
    INSERT INTO departments (id, code, name) VALUES (1, 'it', 'IT-отдел');
    INSERT INTO users (id, name, department_id) VALUES
        (10, 'Инициаторова Ирина', 1), (30, 'Другов Даниил', 1), (70, 'Утвердова Ульяна', 1);
'''


class _Db:
    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                                connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.name)
            cur.execute("SET statement_timeout TO '10s'")
        conn.commit()
        return conn

    @contextlib.contextmanager
    def cursor(self):
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
    name = 't_payments_own_' + uuid.uuid4().hex
    base = _Db(name)
    admin = base.connect()
    try:
        with admin.cursor() as cur:
            cur.execute('CREATE SCHEMA ' + name)
            cur.execute('SET search_path TO ' + name)
            cur.execute(BASE_DDL)
            for sql in schema.statements():
                cur.execute(sql)
        admin.commit()
    except Exception:
        admin.rollback()
        admin.close()
        raise
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + name + ' CASCADE')
        admin.commit()
        admin.close()


def new_request(cur, *, initiator=INITIATOR, status='active', stage='approval', sent=True, name='Бумага А4',
                docs='none'):
    cur.execute(
        "INSERT INTO payment_requests (initiator_id, initiator_name, expense_name, status, stage, submitted_at, "
        "closing_docs_status) VALUES (%s, 'Инициаторова Ирина', %s, %s, %s, CASE WHEN %s THEN now() END, %s) "
        "RETURNING id", (initiator, name, status, stage, sent, docs))
    return cur.fetchone()[0]


def open_task(cur, request_id, kind, stage, role, *, assignee=None, state='open'):
    cur.execute("INSERT INTO payment_subtasks (request_id, kind, stage, role_code, assignee_id, state) "
                "VALUES (%s, %s, %s, %s, %s, %s)", (request_id, kind, stage, role, assignee, state))


def own(cur, user_id=INITIATOR, roles=(), approves_own=False):
    total, rows = queries.own_requests(cur, user_id, set(roles), approves_own, limit=10)
    assert total == queries.own_open_count(cur, user_id, set(roles), approves_own), 'счётчик `/ping` — тот же отбор'
    return total, [row['id'] for row in rows]


def desk_ids(cur, user_id=INITIATOR, roles=(), approves_own=False):
    """Заявки, которые стоят в задачах стола человека (как их собирает ручка /desk)."""
    pairs = queries.desk_pairs(cur, user_id, set(roles), approves_own)
    return {request_id for request_id, _kind in pairs} | set(queries.docs_wanted_ids(cur, user_id))


def test_sent_requests_with_others_newest_first(db):
    with db.cursor() as cur:
        at_approver = new_request(cur)
        open_task(cur, at_approver, 'approval', 'approval', 'approver', assignee=APPROVER)
        at_accounting = new_request(cur, stage='payment', name='Картриджи')
        open_task(cur, at_accounting, 'invoice_payment', 'payment', 'accounting')
        new_request(cur, stage='initiation', sent=False, name='Черновик')
        for status in ('done', 'rejected', 'cancelled'):
            new_request(cur, status=status, name='Закрыта: ' + status)
        foreign = new_request(cur, initiator=OTHER, name='Чужая')

        total, ids = own(cur)
        assert ids == [at_accounting, at_approver], 'отправленные свои у других, новые сверху'
        assert total == 2
        rows = queries.own_requests(cur, INITIATOR, set(), limit=10)[1]
        # Строка — та же, что у остальных выборок раздела: подпись этапа и состояние готовы.
        assert rows[0]['stage_label'] == 'Оплата' and rows[0]['state'] == 'active'
        assert own(cur, OTHER) == (1, [foreign])
        assert own(cur, 999) == (0, [])


def test_request_waiting_for_me_stays_in_tasks_only(db):
    """Вернули на доработку, ждут получения, ждут закрывающие документы — это задачи стола; в
    «Моих заявках» такой заявки нет. Документы приложены (скан) — заявка снова у бухгалтерии."""
    with db.cursor() as cur:
        returned = new_request(cur, stage='initiation', name='Вернули')
        open_task(cur, returned, 'initiation', 'initiation', 'initiator', assignee=INITIATOR)
        open_task(cur, returned, 'approval', 'approval', 'approver', assignee=APPROVER, state='waiting')
        receiving = new_request(cur, stage='receiving', name='Получение')
        open_task(cur, receiving, 'receiving', 'receiving', 'initiator', assignee=INITIATOR)
        docs_wanted = new_request(cur, stage='closing', name='Нужны документы')
        open_task(cur, docs_wanted, 'closing_docs', 'closing', 'accounting')
        docs_given = new_request(cur, stage='closing', name='Скан приложен', docs='scan')
        open_task(cur, docs_given, 'closing_docs', 'closing', 'accounting')

        total, ids = own(cur)
        assert ids == [docs_given] and total == 1
        tasks = desk_ids(cur)
        assert tasks == {returned, receiving, docs_wanted}
        assert not tasks & set(ids), 'ни одна заявка не стоит и в задачах, и в блоке'


def test_roles_decide_like_the_desk(db):
    """Бухгалтер-инициатор: его счёт у бухгалтерии ждёт его самого — это задача. Утверждающий
    свою заявку не утверждает (она у остальных) — она в блоке; администратор раздела может —
    тогда это его задача."""
    with db.cursor() as cur:
        at_accounting = new_request(cur, stage='payment', name='Счёт бухгалтера')
        open_task(cur, at_accounting, 'invoice_payment', 'payment', 'accounting')
        own_approval = new_request(cur, name='Своя на утверждении')
        open_task(cur, own_approval, 'approval', 'approval', 'approver')

        assert own(cur) == (2, [own_approval, at_accounting]), 'обычному инициатору обе у других'
        assert own(cur, roles={'accounting'}) == (1, [own_approval])
        assert own(cur, roles={'approver'}) == (2, [own_approval, at_accounting]), 'свою утверждают другие'
        assert own(cur, roles={'approver'}, approves_own=True) == (1, [at_accounting])
        for roles, approves_own in (((), False), (('accounting',), False), (('approver',), False), (('approver',), True)):
            tasks = desk_ids(cur, roles=roles, approves_own=approves_own)
            assert not tasks & set(own(cur, roles=roles, approves_own=approves_own)[1]), (roles, approves_own)


def test_limit_keeps_the_full_total(db):
    with db.cursor() as cur:
        ids = [new_request(cur, name='Заявка %s' % n) for n in range(12)]
        total, rows = queries.own_requests(cur, INITIATOR, set(), limit=5)
        assert total == 12, 'на счётчике — все такие заявки, а не показанные'
        assert [row['id'] for row in rows] == sorted(ids, reverse=True)[:5]
        assert queries.own_open_count(cur, INITIATOR, set()) == 12
