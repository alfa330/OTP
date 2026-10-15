# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: PAYMENTS_TEST_PORT — порт локального Postgres.

Наблюдатели заявки «Оплаты счетов» (просьба владельца 15.10.2026). В
tests/test_payments_observers.py запросы подменены и видно только, что они
НАПИСАНЫ. Что они ЗНАЧАТ — кто видит заявку в реестре, что попадает в «Наблюдаю»,
сколько в счётчике, что остаётся после удаления заявки или учётки — исполняется
здесь, на настоящей схеме раздела (payments.schema) и маленьком наборе людей.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со
случайным именем, в конце она удаляется. Запуск на локальном кластере (пароль,
если он есть, — через PGPASSWORD):

    PAYMENTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_payments_observers_postgres.py

Люди выдуманы.
"""
import contextlib
import os
import uuid

import pytest

from payments import access, flow, queries, schema, workflow

PORT = os.environ.get('PAYMENTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (PAYMENTS_TEST_PORT)')

INITIATOR, WATCHER, SECOND, OUTSIDER, FIRED, ACCOUNTANT, MANAGER, APPROVER = 10, 20, 21, 30, 40, 50, 60, 70

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL DEFAULT 'operator',
                        status text NOT NULL DEFAULT 'working', department_id integer, supervisor_id integer,
                        telegram_id bigint, job_title text, avatar_bucket text, avatar_blob_path text);
    INSERT INTO departments (id, code, name) VALUES (1, 'it', 'IT-отдел'), (2, 'acc', 'Бухгалтерия');
    INSERT INTO users (id, name, status, department_id) VALUES
        (10, 'Инициаторова Ирина', 'working', 1),
        (20, 'Наблюдаев Нурлан', 'working', 1),
        (21, 'Вторая Вера', 'working', NULL),
        (30, 'Посторонний Павел', 'working', 1),
        (40, 'Бывшев Борис', 'fired', 1),
        (50, 'Счётова Сауле', 'working', 2),
        (60, 'Руководов Рустам', 'working', 1),
        (70, 'Утвердова Ульяна', 'working', 1);
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
    name = 't_payments_observers_' + uuid.uuid4().hex
    base = _Db(name)
    admin = base.connect()
    # Только таблицы раздела (schema.statements): без pg_trgm и без переноса первой
    # версии — они не про наблюдателей и трогали бы кластер за пределами схемы теста.
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


def new_request(cur, *, initiator=INITIATOR, status='active', name='Бумага А4'):
    cur.execute("INSERT INTO payment_requests (initiator_id, initiator_name, expense_name, status, stage) "
                "VALUES (%s, 'Инициаторова Ирина', %s, %s, 'approval') RETURNING id", (initiator, name, status))
    return cur.fetchone()[0]


def registry(cur, viewer, **filters):
    """Реестр так, как его спрашивает ручка для человека без полного реестра."""
    filters.update({'viewer_id': viewer, 'viewer_roles': set(), 'visible_to': viewer, 'visible_roles': set()})
    total, items = queries.list_requests(cur, limit=100, **filters)
    return total, [item['id'] for item in items]


def test_add_list_remove(db):
    with db.cursor() as cur:
        rid = new_request(cur)
        assert queries.add_observers(cur, rid, [WATCHER, SECOND, WATCHER], INITIATOR) == [WATCHER, SECOND]
        assert queries.add_observers(cur, rid, [WATCHER], INITIATOR) == [], 'повтор ничего не добавляет'
        people = queries.list_observers(cur, rid)
        assert [(p['user_id'], p['name'], p['department_name'], p['fired']) for p in people] == [
            (SECOND, 'Вторая Вера', None, False), (WATCHER, 'Наблюдаев Нурлан', 'IT-отдел', False)], \
            'добавленные одним действием — одного времени, по алфавиту'
        cur.execute("SELECT added_by FROM payment_request_observers WHERE request_id = %s", (rid,))
        assert {row[0] for row in cur.fetchall()} == {INITIATOR}
        assert queries.observes(cur, rid, WATCHER) and not queries.observes(cur, rid, OUTSIDER)
        assert queries.remove_observers(cur, rid, [SECOND, OUTSIDER]) == [SECOND], 'снимаются только стоящие в списке'
        assert [p['user_id'] for p in queries.list_observers(cur, rid)] == [WATCHER]
        assert queries.add_observers(cur, rid, [], INITIATOR) == [] and queries.remove_observers(cur, rid, []) == []


def test_fired_stays_in_the_list_but_hears_nothing(db):
    with db.cursor() as cur:
        first, second = new_request(cur), new_request(cur, name='Картридж')
        queries.add_observers(cur, first, [WATCHER, FIRED], INITIATOR)
        queries.add_observers(cur, second, [FIRED], INITIATOR)
        assert [p['fired'] for p in queries.list_observers(cur, first)] == [True, False]
        assert queries.observers_by_request(cur, [first, second, 999]) == {first: {WATCHER}}
        assert queries.observer_ids(cur, first) == {WATCHER}
        assert queries.observer_ids(cur, second) == set()
        assert queries.observers_by_request(cur, []) == {}


def test_observer_sees_the_request_in_the_registry_and_in_watching(db):
    with db.cursor() as cur:
        watched, closed, foreign = new_request(cur), new_request(cur, status='done'), new_request(cur)
        queries.add_observers(cur, watched, [WATCHER], INITIATOR)
        queries.add_observers(cur, closed, [WATCHER], INITIATOR)
        total, ids = registry(cur, WATCHER)
        assert sorted(ids) == sorted([watched, closed]), 'чужая заявка без наблюдения не видна'
        assert foreign not in ids
        assert registry(cur, OUTSIDER) == (0, [])
        total, ids = registry(cur, WATCHER, state='watching')
        assert ids == [watched], '«Наблюдаю» — заявки в работе; закрытая видна во «Все»'
        # Инициатор свою заявку видит, но «Наблюдаю» у него пусто.
        assert registry(cur, INITIATOR, state='watching') == (0, [])
        counters = queries.state_counters(cur, viewer_id=WATCHER, visible_to=WATCHER, visible_roles=set())
        assert counters['watching'] == 1 and counters['all'] == 2 and counters['done'] == 1
        counters = queries.state_counters(cur, viewer_id=INITIATOR, visible_to=INITIATOR, visible_roles=set())
        assert counters['watching'] == 0 and counters['all'] == 3
        # Полный реестр (бухгалтерия) — «Наблюдаю» и там только свои наблюдения.
        assert queries.state_counters(cur, viewer_id=ACCOUNTANT)['watching'] == 0
        assert queries.list_requests(cur, state='watching', viewer_id=ACCOUNTANT)[0] == 0


def test_history_and_duplicates_follow_the_registry_border(db):
    """Справки «История оплат» и «Возможный дубль» видят то же, что реестр смотрящего."""
    with db.cursor() as cur:
        cur.execute("INSERT INTO payment_counterparties (name) VALUES ('ТОО «Бумага»') RETURNING id")
        supplier = cur.fetchone()[0]
        watched = new_request(cur)
        cur.execute("UPDATE payment_requests SET counterparty_id = %s, paid_on = CURRENT_DATE, amount = 100, "
                    "invoice_number = '7', invoice_date = CURRENT_DATE WHERE id = %s", (supplier, watched))
        queries.add_observers(cur, watched, [WATCHER], INITIATOR)
        seen = queries.payment_history(cur, counterparty_id=supplier, visible_to=WATCHER, visible_roles=set())
        assert [row['request_id'] for row in seen] == [watched]
        assert queries.payment_history(cur, counterparty_id=supplier, visible_to=OUTSIDER, visible_roles=set()) == []
        twin = {'id': -1, 'counterparty_id': supplier, 'invoice_number': '7', 'amount': 100}
        cur.execute("SELECT invoice_date FROM payment_requests WHERE id = %s", (watched,))
        twin['invoice_date'] = cur.fetchone()[0]
        assert [row['id'] for row in queries.find_duplicates(cur, twin, visible_to=WATCHER)] == [watched]
        assert queries.find_duplicates(cur, twin, visible_to=OUTSIDER) == []


def test_deleting_request_or_account_takes_the_rows(db):
    with db.cursor() as cur:
        rid = new_request(cur)
        queries.add_observers(cur, rid, [WATCHER, SECOND], INITIATOR)
        cur.execute("DELETE FROM users WHERE id = %s", (SECOND,))
        assert [p['user_id'] for p in queries.list_observers(cur, rid)] == [WATCHER]
        cur.execute("DELETE FROM users WHERE id = %s", (INITIATOR,))
        cur.execute("SELECT added_by FROM payment_request_observers WHERE request_id = %s", (rid,))
        assert cur.fetchall() == [(None,)], 'удалили того, кто добавил, — строка остаётся без автора'
        queries.delete_request(cur, rid)
        cur.execute("SELECT count(*) FROM payment_request_observers")
        assert cur.fetchone()[0] == 0


def test_schema_is_idempotent(db):
    """Повторный старт приложения — те же операторы: ни ошибок, ни дублей индексов."""
    with db.cursor() as cur:
        for sql in schema.statements():
            cur.execute(sql)
        cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname = %s AND tablename = 'payment_request_observers' "
                    "ORDER BY indexname", (db.name,))
        assert [row[0] for row in cur.fetchall()] == ['idx_payment_request_observers_user',
                                                       'payment_request_observers_pkey']


# ─── Маршрут заявки глазами наблюдателя ──────────────────────────────────────

def person(cur, user_id):
    cur.execute('SELECT name FROM users WHERE id = %s', (user_id,))
    return {'id': user_id, 'name': cur.fetchone()[0]}


def submitted_invoice_request(cur, observers, *, with_entity=True):
    """Заявка по счёту с наблюдателями, отправленная на согласование, — как её ведёт раздел."""
    for role, user_id in (('approver', APPROVER), ('accounting', ACCOUNTANT)):
        cur.execute("INSERT INTO payment_role_members (role_code, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (role, user_id))
    # Компания указана: без неё маршрут начинался бы с её уточнения бухгалтерией (KIND_ENTITY).
    entity_id = None
    if with_entity:
        cur.execute("INSERT INTO payment_legal_entities (name) VALUES ('ТОО «Наблюдение»') RETURNING id")
        entity_id = cur.fetchone()[0]
    fields = {'request_kind': 'purchase', 'expense_name': 'Бумага А4', 'payment_method': 'invoice',
              'object_type': 'service', 'invoice_number': 'A-1', 'invoice_date': '2026-10-10',
              'legal_entity_id': entity_id}
    rid = queries.create_request(cur, fields=fields, items=[{'name': 'Бумага', 'quantity': 1, 'unit': 'шт',
                                                              'unit_price': 10000}],
                                 actor=person(cur, INITIATOR), initiator=person(cur, INITIATOR))
    flow.change_observers(cur, queries.read_request(cur, rid), person(cur, INITIATOR), add=observers)
    queries.add_attachment(cur, rid, kind='invoice', file_name='счёт.pdf', content_type='application/pdf',
                           file_size=1, bucket='b', blob_path='p', actor=person(cur, INITIATOR),
                           subtask_kind=workflow.KIND_INITIATION)
    request = queries.read_request(cur, rid)
    route = workflow.build_route(request, initiator=person(cur, INITIATOR), manager=person(cur, MANAGER),
                                 manager_step=True, approver=person(cur, APPROVER))
    initiation = queries.read_subtask(cur, rid, workflow.KIND_INITIATION)
    queries.complete_subtask(cur, initiation['id'], person(cur, INITIATOR), outcome='submitted')
    queries.write_route(cur, rid, route)
    queries.mark_submitted(cur, rid, snapshot=workflow.essentials(request), basis={'trace': []},
                           manager=person(cur, MANAGER), set_manager=True, reset_approval=True)
    flow.advance(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)
    return rid


def unread(cur, user_id, rid):
    cur.execute("SELECT kind, title FROM payment_notifications WHERE user_id = %s AND request_id = %s "
                "AND read_at IS NULL ORDER BY id", (user_id, rid))
    return cur.fetchall()


def act(cur, rid, kind, action, who, **fields):
    files = fields.pop('files', ())
    flow.act(cur, rid, kind, action, person(cur, who), fields=fields, new_file_kinds=files, outbox=[], base_url=None)


def test_observer_follows_the_route_and_the_bell_cleans_up(db, monkeypatch):
    """Инициатор «в отпуске»: наблюдатель узнаёт о ходе и о том, что заявка ждёт инициатора;
    когда ждать перестали — строка гаснет; Telegram наблюдателю не нужен (колокол только)."""
    monkeypatch.setattr(access, 'SECTION_ALLOWED_USER_IDS', (INITIATOR, WATCHER, ACCOUNTANT, MANAGER, APPROVER))
    with db.cursor() as cur:
        rid = submitted_invoice_request(cur, [WATCHER])
        act(cur, rid, workflow.KIND_MANAGER, 'approve', MANAGER)
        act(cur, rid, workflow.KIND_APPROVAL, 'approve', APPROVER)
        act(cur, rid, workflow.KIND_INVOICE, 'pay', ACCOUNTANT, paid_on='2026-10-15', paid_amount=10000,
            files=['payment_order'])
        assert unread(cur, WATCHER, rid) == [
            ('watching', 'Вас добавили наблюдателем заявки'), ('approved', 'Заявка согласована'),
            ('paid', 'Счёт оплачен'), ('waits_initiator', 'Заявка ждёт подтверждения получения от инициатора')]
        act(cur, rid, workflow.KIND_RECEIVING, 'confirm', INITIATOR, received_on='2026-10-16', received_quantity='1')
        kinds = [kind for kind, _title in unread(cur, WATCHER, rid)]
        assert 'waits_initiator' not in kinds, 'получение подтвердили — ждать нечего'
        assert ('docs_needed', 'Бухгалтерия ждёт закрывающие документы') in unread(cur, WATCHER, rid)
        cur.execute("SELECT count(*) FROM payment_notifications WHERE user_id = %s AND actionable", (WATCHER,))
        assert cur.fetchone()[0] == 0, 'у наблюдателя нет задач — только сведения'


def test_returned_then_cancelled_leaves_one_line(db, monkeypatch):
    monkeypatch.setattr(access, 'SECTION_ALLOWED_USER_IDS', (INITIATOR, WATCHER, ACCOUNTANT, MANAGER, APPROVER))
    with db.cursor() as cur:
        rid = submitted_invoice_request(cur, [WATCHER])
        act(cur, rid, workflow.KIND_MANAGER, 'return', MANAGER, comment='уточните количество')
        assert ('returned', 'Заявку вернули на доработку') in unread(cur, WATCHER, rid)
        flow.cancel(cur, rid, person(cur, INITIATOR), 'не нужно')
        assert [kind for kind, _title in unread(cur, WATCHER, rid)] == ['watching', 'cancelled'], \
            'о мёртвой заявке — одна строка «отменена», а не ещё и «вернули на доработку»'


def test_company_clarified_by_accounting_reaches_observers(db, monkeypatch):
    """Заявка без компании (её уточняет бухгалтерия, ba662b89): задача бухгалтерии наблюдателю не
    копируется — это не ожидание инициатора, а «Компания указана» приходит, как инициатору."""
    monkeypatch.setattr(access, 'SECTION_ALLOWED_USER_IDS', (INITIATOR, WATCHER, ACCOUNTANT, MANAGER, APPROVER))
    with db.cursor() as cur:
        rid = submitted_invoice_request(cur, [WATCHER], with_entity=False)
        assert queries.read_subtask(cur, rid, workflow.KIND_ENTITY)['state'] == 'open'
        assert [kind for kind, _title in unread(cur, WATCHER, rid)] == ['watching']
        cur.execute("INSERT INTO payment_legal_entities (name) VALUES ('ТОО «Уточнённая»') RETURNING id")
        act(cur, rid, workflow.KIND_ENTITY, 'set_entity', ACCOUNTANT, legal_entity_id=cur.fetchone()[0])
        assert ('entity_set', 'Компания указана') in unread(cur, WATCHER, rid)
