# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: PAYMENTS_TEST_PORT — порт локального Postgres.

Сверка ТЗ «Закуп и оплата» 09.11.2026: пункты, сделанные «не дословно», доделаны по
слову владельца («кроме первого, до 10 все нужно реализовать»). Что новые запросы
ЗНАЧАТ, исполняется здесь, на настоящей схеме раздела (payments.schema):

* п. 18 — удаление уводит заявку в архив «Удалённые»: из реестра, досок, стола,
  напоминаний и справок она уходит, а история, файлы и наблюдатели остаются;
* п. 17 — свои сроки у чека и закрывающих документов и напоминания по ним;
* п. 8 — НДС поставщика, вписанного названием, «не указан», пока его не укажет
  бухгалтер; «Прошлые оплаты» поставщику не вытесняются чужими;
* п. 12 — смена компании-владельца имущества ложится строкой истории;
* п. 11 — имущество заявки из архива передают с актом как обычно;
* п. 9 — полные номера карт для доски финансового отдела и журнал их показа.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным
именем, в конце она удаляется. Запуск:

    PAYMENTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_payments_tz_gaps_postgres.py

Люди и компании выдуманы.
"""
import contextlib
import os
import time
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from payments import access, assets as assets_sql, directory, flow, legacy, queries, schema, workflow

PORT = os.environ.get('PAYMENTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (PAYMENTS_TEST_PORT)')

ADMIN = access.SECTION_ADMIN_USER_IDS[0]
INITIATOR, WATCHER, OUTSIDER, ACCOUNTANT, FINANCE, KEEPER, APPROVER = 10, 20, 30, 50, 55, 60, 70
PEOPLE = (ADMIN, INITIATOR, WATCHER, ACCOUNTANT, FINANCE, KEEPER, APPROVER)
TODAY = date(2026, 11, 9)

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL DEFAULT 'operator',
                        status text NOT NULL DEFAULT 'working', department_id integer, supervisor_id integer,
                        telegram_id bigint, job_title text, avatar_bucket text, avatar_blob_path text);
    INSERT INTO departments (id, code, name) VALUES (1, 'szov', 'СЗоВ'), (2, 'acc', 'Бухгалтерия');
    INSERT INTO users (id, name, department_id) VALUES
        (%(admin)s, 'Администраторов Аман', 1),
        (10, 'Инициаторова Ирина', 1),
        (20, 'Наблюдаев Нурлан', 1),
        (30, 'Посторонний Павел', 1),
        (50, 'Счётова Сауле', 2),
        (55, 'Финансов Фарид', 2),
        (60, 'Складов Самат', 1),
        (70, 'Утвердова Ульяна', 1);
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
def db(monkeypatch):
    name = 't_payments_tz_gaps_' + uuid.uuid4().hex
    base = _Db(name)
    admin = base.connect()
    try:
        with admin.cursor() as cur:
            cur.execute('CREATE SCHEMA ' + name)
            cur.execute('SET search_path TO ' + name)
            cur.execute(BASE_DDL % {'admin': ADMIN})
            for sql in schema.statements():
                cur.execute(sql)
        admin.commit()
    except Exception:
        admin.rollback()
        admin.close()
        raise
    monkeypatch.setattr(access, 'SECTION_ALLOWED_USER_IDS', PEOPLE)
    monkeypatch.setenv('PAYMENTS_CARD_KEY', 'test-phrase-for-cards')
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + name + ' CASCADE')
        admin.commit()
        admin.close()


def person(user_id):
    return {'id': user_id, 'name': 'Тест %s' % user_id}


def new_request(cur, *, initiator=INITIATOR, status='active', stage='approval', name='Бумага А4', **cols):
    cur.execute("INSERT INTO payment_requests (initiator_id, initiator_name, expense_name, status, stage, "
                "request_kind, payment_method, submitted_at) "
                "VALUES (%s, 'Инициаторова Ирина', %s, %s, %s, 'purchase', 'invoice', "
                "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')) RETURNING id",
                (initiator, name, status, stage))
    rid = cur.fetchone()[0]
    if cols:
        cur.execute('UPDATE payment_requests SET %s WHERE id = %%s' % ', '.join('%s = %%s' % key for key in cols),
                    list(cols.values()) + [rid])
    return rid


def subtask(cur, rid, kind, state, *, role, assignee=None, opened_at=None, done_at=None, status='new'):
    item = workflow.subtask(kind)
    cur.execute("INSERT INTO payment_subtasks (request_id, kind, position, stage, role_code, assignee_id, state, "
                "status, opened_at, done_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (rid, kind, item['position'], item['stage'], role, assignee, state, status, opened_at, done_at))
    sid = cur.fetchone()[0]
    if state == 'open':
        cur.execute("UPDATE payment_requests SET current_subtask_id = %s, current_role_code = %s, "
                    "current_assignee_id = %s WHERE id = %s", (sid, role, assignee, rid))
    return sid


def at(day, hour=10):
    return datetime(day.year, day.month, day.day, hour, 0)


def ids(rows):
    return [row['id'] for row in rows]


# ─── П. 18: архив «Удалённые» ────────────────────────────────────────────────

def test_deleted_request_leaves_every_working_list_and_keeps_its_history(db):
    with db.cursor() as cur:
        cur.execute("INSERT INTO payment_counterparties (name, vat_payer) VALUES ('ТОО «Бумага»', FALSE) RETURNING id")
        supplier = cur.fetchone()[0]
        shared = {'counterparty_id': supplier, 'invoice_number': 'СФ-7', 'invoice_date': date(2026, 11, 1),
                  'amount': Decimal('25000'), 'due_on': date(2026, 11, 5)}
        live = new_request(cur, **shared)
        gone = new_request(cur, **shared)
        for rid in (live, gone):
            subtask(cur, rid, 'approval', 'open', role='approver', opened_at=at(TODAY))
            queries.add_observers(cur, rid, [WATCHER], INITIATOR)
            queries.log_event(cur, rid, 'submitted', person(INITIATOR), payload={'first': True})
        queries.add_attachment(cur, gone, kind='invoice', file_name='счёт.pdf', content_type='application/pdf',
                               file_size=10, bucket='b', blob_path='p/gone.pdf', actor=person(INITIATOR))
        cur.execute("INSERT INTO payment_notifications (user_id, request_id, kind, title) "
                    "VALUES (%s, %s, 'task', 'Утвердите закуп')", (APPROVER, gone))
        events_before = len(queries.list_events(cur, gone))

        flow.delete(cur, gone, person(ADMIN), 'Дубль заявки №%s' % live)

        row = queries.read_request(cur, gone)
        assert row['deleted'] and row['state'] == 'deleted' and row['deleted_by'] == ADMIN
        assert row['deleted_comment'] == 'Дубль заявки №%s' % live and row['status'] == 'active', \
            'статус не трогаем: в архиве видно, на каком этапе убрали'
        # Реестр: удалённой нет нигде, кроме среза «Удалённые» — и тот только администратору.
        full = {'viewer_id': ACCOUNTANT, 'viewer_roles': {'accounting'}}
        assert ids(queries.list_requests(cur, limit=50, **full)[1]) == [live]
        assert ids(queries.list_requests(cur, limit=50, state='deleted', deleted_visible=True, **full)[1]) == [gone]
        assert queries.list_requests(cur, limit=50, state='deleted', **full) == (0, [])
        counters = queries.state_counters(cur, deleted_visible=True, **full)
        assert (counters['all'], counters['open'], counters['overdue'], counters['deleted']) == (1, 1, 1, 1)
        assert 'deleted' not in queries.state_counters(cur, **full)
        assert queries.state_counters(cur, **full)['all'] == 1
        # Доски, стол, напоминания, справки.
        assert [item['id'] for item, _subtasks in queries.board_requests(cur, 'approval')] == [live]
        assert [pair[0] for pair in queries.desk_pairs(cur, APPROVER, {'approver'})] == [live]
        assert queries.desk_count(cur, APPROVER, {'approver'}) == 1
        assert ids(queries.own_requests(cur, INITIATOR, set(), limit=5)[1]) == [live]
        assert queries.role_backlog(cur, 'approver') == 1
        assert [item['id'] for item, _mark in queries.due_requests(cur, TODAY, 2)] == [live]
        assert queries.find_duplicates(cur, queries.read_request(cur, live)) == [], 'двойник в архиве — не дубль'
        assert queries.duplicate_request_ids(cur, [live]) == set()
        # История, файлы, наблюдатели — целы; удаление — само строка истории.
        events = queries.list_events(cur, gone)
        assert len(events) == events_before + 1 and events[-1]['kind'] == 'deleted'
        assert events[-1]['comment'] == 'Дубль заявки №%s' % live and events[-1]['actor_id'] == ADMIN
        assert [item['file_name'] for item in queries.list_attachments(cur, gone)] == ['счёт.pdf']
        assert queries.observer_ids(cur, gone) == {WATCHER}
        cur.execute("SELECT read_at IS NOT NULL FROM payment_notifications WHERE request_id = %s", (gone,))
        assert cur.fetchall() == [(True,)], 'колокол по удалённой заявке гаснет'
        # Действия по ней закрыты, второй раз не удалить; видит её только администратор раздела.
        with pytest.raises(flow.FlowError) as acted:
            flow.load_for_action(cur, gone, 'approval')
        assert acted.value.code == 'PAYMENT_REQUEST_DELETED'
        with pytest.raises(flow.FlowError) as twice:
            flow.delete(cur, gone, person(ADMIN))
        assert twice.value.code == 'PAYMENT_REQUEST_DELETED' and twice.value.status == 409
        assert access.can_see_request({'user_id': ADMIN}, row)
        assert not access.can_see_request({'user_id': INITIATOR}, row)
        assert not access.can_see_request({'user_id': ACCOUNTANT}, row, {'accounting'}, participant=True)


def test_archive_of_closed_and_waiting_requests_leaves_counters_and_the_initiator_desk(db):
    """Удалённые закрытая и ждущая документов заявки не попадают ни в счётчики «Закрыты» /
    «Ждут меня» / «Наблюдаю», ни в «приложите документы» на столе инициатора."""
    with db.cursor() as cur:
        closed = new_request(cur, status='done', stage='closed')
        docs = new_request(cur, stage='closing', closing_docs_status='none')
        subtask(cur, docs, 'closing_docs', 'open', role='accounting', opened_at=at(TODAY), status='awaiting')
        queries.add_observers(cur, docs, [WATCHER], INITIATOR)
        before = {
            'counters': queries.state_counters(cur, viewer_id=WATCHER, visible_to=WATCHER, visible_roles=set()),
            'desk': queries.desk_count(cur, INITIATOR, set()), 'docs': queries.docs_wanted_ids(cur, INITIATOR),
            'done': queries.state_counters(cur, viewer_id=ACCOUNTANT, viewer_roles={'accounting'})['done'],
        }
        assert before['counters']['watching'] == 1 and before['desk'] == 1 and before['docs'] == [docs]
        assert before['done'] == 1
        queries.mark_deleted(cur, closed, person(ADMIN))
        queries.mark_deleted(cur, docs, person(ADMIN))
        counters = queries.state_counters(cur, viewer_id=WATCHER, visible_to=WATCHER, visible_roles=set())
        assert (counters['watching'], counters['all']) == (0, 0)
        assert queries.state_counters(cur, viewer_id=ACCOUNTANT, viewer_roles={'accounting'})['done'] == 0
        admin = queries.state_counters(cur, viewer_id=ADMIN, deleted_visible=True)
        assert (admin['done'], admin['open'], admin['deleted']) == (0, 0, 2)
        assert queries.desk_count(cur, INITIATOR, set()) == 0
        assert queries.docs_wanted_ids(cur, INITIATOR) == []
        assert queries.own_open_count(cur, INITIATOR, set()) == 0


def test_stock_item_of_an_archived_it_request_is_free_again(db):
    """Выдача из наличия по заявке, ушедшей в «Удалённые», вещь больше не держит (как при
    прежнем удалении, стиравшем историю): Helpdesk IT может выдать её по другой заявке."""
    with db.cursor() as cur:
        first, _second = _entities(cur)
        source = new_request(cur, stage='closed', status='done')
        asset_id = assets_sql.create_asset(cur, request_id=source,
                                           fields=_asset_fields(first, status='in_stock'), actor=person(KEEPER))
        issued_by = new_request(cur, stage='closed', status='done')
        queries.log_event(cur, issued_by, 'it_stock', person(KEEPER),
                          payload={'kind': 'it_review', 'assets': [{'id': asset_id, 'name': 'Ноутбук'}]})
        assert set(assets_sql.issued_from_stock(cur, [asset_id])) == {asset_id}
        queries.mark_deleted(cur, issued_by, person(ADMIN), 'ошибочная')
        assert assets_sql.issued_from_stock(cur, [asset_id]) == {}


def test_history_of_payments_skips_the_archive(db):
    with db.cursor() as cur:
        cur.execute("INSERT INTO payment_counterparties (name) VALUES ('ТОО «Бумага»') RETURNING id")
        supplier = cur.fetchone()[0]
        paid = {'counterparty_id': supplier, 'paid_on': date(2026, 11, 2), 'amount': Decimal('100')}
        kept, archived = new_request(cur, **paid), new_request(cur, **paid)
        queries.mark_deleted(cur, archived, person(ADMIN))
        assert [row['request_id'] for row in queries.payment_history(cur, counterparty_id=supplier)] == [kept]


def test_legacy_pending_ignores_the_archive(db):
    with db.cursor() as cur:
        pending = new_request(cur)
        archived = new_request(cur)
        queries.mark_deleted(cur, archived, person(ADMIN))
        assert legacy.pending(cur) == [pending], 'заявку из архива в работу не переносят'


# ─── П. 17: сроки чека и закрывающих документов ──────────────────────────────

def _notified(cur):
    cur.execute("SELECT request_id, kind, user_id FROM payment_notifications ORDER BY request_id, kind, user_id")
    result = {}
    for rid, kind, user_id in cur.fetchall():
        result.setdefault((rid, kind), set()).add(user_id)
    return result


def test_receipt_and_closing_documents_remind_once_per_deadline(db):
    with db.cursor() as cur:
        queries.add_role_member(cur, 'accounting', ACCOUNTANT, None)
        # Чек: срок — 3 дня после пополнения (открытия этапа).
        soon = new_request(cur, stage='payment')
        subtask(cur, soon, 'receipt_confirm', 'open', role='initiator', assignee=INITIATOR,
                opened_at=at(TODAY - timedelta(days=2)))
        queries.add_observers(cur, soon, [WATCHER], INITIATOR)
        late = new_request(cur, stage='payment')
        subtask(cur, late, 'receipt_confirm', 'open', role='initiator', assignee=INITIATOR,
                opened_at=at(TODAY - timedelta(days=5)))
        fresh = new_request(cur, stage='payment')
        subtask(cur, fresh, 'receipt_confirm', 'open', role='initiator', assignee=INITIATOR, opened_at=at(TODAY))
        # Документы: срок — 10 дней после подтверждения получения.
        docs = new_request(cur, stage='closing', closing_docs_status='none')
        subtask(cur, docs, 'receiving', 'done', role='initiator', assignee=INITIATOR,
                done_at=at(TODAY - timedelta(days=11)))
        subtask(cur, docs, 'closing_docs', 'open', role='accounting', opened_at=at(TODAY - timedelta(days=3)),
                status='awaiting')
        handed = new_request(cur, stage='closing', closing_docs_status='original')
        subtask(cur, handed, 'receiving', 'done', role='initiator', assignee=INITIATOR,
                done_at=at(TODAY - timedelta(days=12)))
        subtask(cur, handed, 'closing_docs', 'open', role='accounting', opened_at=at(TODAY - timedelta(days=12)),
                status='awaiting')
        archived = new_request(cur, stage='closing', closing_docs_status='none')
        subtask(cur, archived, 'receiving', 'done', role='initiator', assignee=INITIATOR,
                done_at=at(TODAY - timedelta(days=20)))
        subtask(cur, archived, 'closing_docs', 'open', role='accounting', opened_at=at(TODAY - timedelta(days=20)),
                status='awaiting')
        queries.mark_deleted(cur, archived, person(ADMIN))

        rows = {(row[0]['id'], row[1]['kind']) for row in queries.stage_deadline_rows(cur)}
        assert rows == {(soon, 'receipt_confirm'), (late, 'receipt_confirm'), (fresh, 'receipt_confirm'),
                        (docs, 'closing_docs'), (handed, 'closing_docs')}

        created = flow.remind_deadlines(cur, today=TODAY)
        got = _notified(cur)
        assert got == {
            (soon, 'receipt_due_soon'): {INITIATOR, WATCHER},
            (late, 'receipt_overdue'): {INITIATOR},
            (docs, 'docs_overdue'): {ACCOUNTANT, INITIATOR},
            (handed, 'docs_overdue'): {ACCOUNTANT, INITIATOR},
        }, 'решение владельца: тот, у кого этап, инициатор и наблюдатели; свежий этап молчит'
        assert created == sum(len(users) for users in got.values())
        cur.execute("SELECT title, tone FROM payment_notifications WHERE request_id = %s", (late,))
        assert cur.fetchall() == [('Чек просрочен на 2 дн. — срок был 07.11.2026', 'warning')]
        cur.execute("SELECT DISTINCT title FROM payment_notifications WHERE request_id = %s", (soon,))
        assert cur.fetchall() == [('Чек — срок завтра, 10.11.2026',)]
        # Тот же день и следующий прогон — без повторов: напоминание одно на срок.
        assert flow.remind_deadlines(cur, today=TODAY) == 0

        # Срок — настройка: чек за 1 день — «скоро» становится «просрочено».
        directory.save_setting(cur, workflow.SETTING_RECEIPT_DAYS, 1, ADMIN)
        flow.remind_deadlines(cur, today=TODAY)
        assert _notified(cur)[(soon, 'receipt_overdue')] == {INITIATOR, WATCHER}


def test_receipt_passed_to_someone_else_reminds_them(db):
    """Этап чека администратор передал другому — напоминание идёт ему (и инициатору)."""
    with db.cursor() as cur:
        rid = new_request(cur, stage='payment')
        subtask(cur, rid, 'receipt_confirm', 'open', role='initiator', assignee=APPROVER,
                opened_at=at(TODAY - timedelta(days=6)))
        flow.remind_deadlines(cur, today=TODAY)
        assert _notified(cur) == {(rid, 'receipt_overdue'): {APPROVER, INITIATOR}}


def test_reminders_go_out_when_the_stage_is_done(db):
    """«Отправить чек» гасит напоминания о сроке чека у всех, кому они пришли."""
    with db.cursor() as cur:
        rid = new_request(cur, stage='payment')
        subtask(cur, rid, 'receipt_confirm', 'open', role='initiator', assignee=INITIATOR,
                opened_at=at(TODAY - timedelta(days=6)))
        subtask(cur, rid, 'receiving', 'pending', role='initiator', assignee=INITIATOR)
        queries.add_observers(cur, rid, [WATCHER], INITIATOR)
        flow.remind_deadlines(cur, today=TODAY)
        queries.add_attachment(cur, rid, kind='receipt', file_name='чек.jpg', content_type='image/jpeg', file_size=3,
                               bucket='b', blob_path='p/r.jpg', actor=person(INITIATOR), subtask_kind='receipt_confirm')
        flow.act(cur, rid, 'receipt_confirm', 'provide', person(INITIATOR), fields={}, outbox=[], base_url=None)
        cur.execute("SELECT user_id, read_at IS NOT NULL FROM payment_notifications "
                    "WHERE request_id = %s AND kind = 'receipt_overdue' ORDER BY user_id", (rid,))
        assert cur.fetchall() == [(INITIATOR, True), (WATCHER, True)]


def test_stage_deadline_for_the_card_of_the_request(db):
    with db.cursor() as cur:
        rid = new_request(cur, stage='closing')
        subtask(cur, rid, 'receiving', 'done', role='initiator', assignee=INITIATOR, done_at=at(TODAY - timedelta(days=4)))
        subtask(cur, rid, 'closing_docs', 'open', role='accounting', opened_at=at(TODAY - timedelta(days=1)))
        deadline = workflow.stage_deadline(queries.list_subtasks(cur, rid), directory.get_settings(cur), TODAY)
        assert deadline == {'kind': 'closing_docs', 'due_on': date(2026, 11, 15), 'days_left': 6}


# ─── П. 8: НДС поставщика и прошлые оплаты ───────────────────────────────────

def test_supplier_vat_stays_unknown_until_accounting_sets_it(db):
    with db.cursor() as cur:
        cur.execute("SELECT is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = 'payment_counterparties' "
                    "AND column_name = 'vat_payer'")
        assert cur.fetchone() == ('YES', None)
        # Схема прежней версии (NOT NULL DEFAULT FALSE) — тот же блок старта переводит её.
        cur.execute("ALTER TABLE payment_counterparties ALTER COLUMN vat_payer SET DEFAULT FALSE")
        cur.execute("UPDATE payment_counterparties SET vat_payer = FALSE WHERE vat_payer IS NULL")
        cur.execute("ALTER TABLE payment_counterparties ALTER COLUMN vat_payer SET NOT NULL")
        block = next(sql for sql in schema.statements() if 'vat_payer DROP NOT NULL' in sql)
        cur.execute(block)
        cur.execute(block)
        cur.execute("SELECT is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = 'payment_counterparties' "
                    "AND column_name = 'vat_payer'")
        assert cur.fetchone() == ('YES', None), 'блок повторяется без вреда'

        supplier, created = directory.find_or_create_counterparty(cur, 'Sulpak', INITIATOR)
        assert created
        assert directory.read_counterparty(cur, supplier)['vat_payer'] is None
        rid = new_request(cur, counterparty_id=supplier)
        row = queries.read_request(cur, rid)
        assert row['counterparty_vat'] is None and workflow.supplier_vat_unknown(row)
        assert directory.find_or_create_counterparty(cur, 'sulpak', INITIATOR) == (supplier, False)
        assert directory.set_counterparty_vat(cur, supplier, True)
        assert not directory.set_counterparty_vat(cur, supplier, False), 'указанный НДС правят в справочнике'
        assert queries.read_request(cur, rid)['counterparty_vat'] is True


def test_payment_carries_the_vat_choice_into_the_supplier_card(db):
    """«Оплачено» с выбором НДС: выбор ложится в карточку поставщика и в историю заявки; без
    выбора оплата не проходит; у следующей заявки того же поставщика НДС уже известен."""
    with db.cursor() as cur:
        queries.add_role_member(cur, 'accounting', ACCOUNTANT, None)
        supplier, _created = directory.find_or_create_counterparty(cur, 'Sulpak', INITIATOR)

        def invoice_request():
            rid = new_request(cur, stage='payment', counterparty_id=supplier, amount=Decimal('25000'))
            subtask(cur, rid, 'invoice_payment', 'open', role='accounting', opened_at=at(TODAY), status='paying')
            subtask(cur, rid, 'receiving', 'pending', role='initiator', assignee=INITIATOR)
            subtask(cur, rid, 'closing_docs', 'pending', role='accounting')
            return rid

        first, second = invoice_request(), invoice_request()
        paid = {'paid_on': TODAY, 'paid_amount': Decimal('25000')}
        with pytest.raises(flow.FlowError) as refused:
            flow.act(cur, first, 'invoice_payment', 'pay', person(ACCOUNTANT), fields=dict(paid),
                     new_file_kinds={'payment_order'}, outbox=[], base_url=None)
        assert any(item.startswith('Укажите НДС поставщика') for item in refused.value.missing)
        flow.act(cur, first, 'invoice_payment', 'pay', person(ACCOUNTANT), fields=dict(paid, supplier_vat=False),
                 new_file_kinds={'payment_order'}, outbox=[], base_url=None)
        assert directory.read_counterparty(cur, supplier)['vat_payer'] is False
        kinds = [event['kind'] for event in queries.list_events(cur, first)]
        assert kinds.index('supplier_vat') < kinds.index('paid'), 'НДС указан той же оплатой, перед ней'
        shown = next(event for event in queries.list_events(cur, first) if event['kind'] == 'supplier_vat')
        assert shown['payload']['vat_payer'] is False and shown['actor_id'] == ACCOUNTANT
        assert queries.read_request(cur, second)['counterparty_vat'] is False
        assert not workflow.supplier_vat_unknown(queries.read_request(cur, second)), 'второй раз не спросят'
        flow.act(cur, second, 'invoice_payment', 'pay', person(ACCOUNTANT), fields=dict(paid),
                 new_file_kinds={'payment_order'}, outbox=[], base_url=None)


def test_past_payments_to_the_supplier_are_not_crowded_out(db):
    with db.cursor() as cur:
        category = directory.upsert_category(cur, name='Канцелярия')
        cur.execute("INSERT INTO payment_counterparties (name) VALUES ('ТОО «Наш»'), ('ТОО «Чужой»') RETURNING id")
        ours, other = [row[0] for row in cur.fetchall()]
        old = new_request(cur, counterparty_id=ours, category_id=category, paid_on=date(2026, 9, 1),
                          amount=Decimal('900'))
        for day in range(1, 6):
            new_request(cur, counterparty_id=other, category_id=category, paid_on=date(2026, 10, day),
                        amount=Decimal('100'))
        current = new_request(cur, counterparty_id=ours, category_id=category)
        common = queries.payment_history(cur, counterparty_id=ours, category_id=category, exclude_request_id=current)
        assert len(common) == 5 and old not in [row['request_id'] for row in common], \
            'общая справка: пять свежих оплат категории вытеснили оплату нашему поставщику'
        own = queries.payment_history(cur, counterparty_id=ours, exclude_request_id=current, limit=3)
        assert [row['request_id'] for row in own] == [old]


# ─── Пп. 11–12: имущество ────────────────────────────────────────────────────

def _entities(cur):
    cur.execute("INSERT INTO payment_legal_entities (name) VALUES ('ТОО «Первая»'), ('ТОО «Вторая»') RETURNING id")
    return [row[0] for row in cur.fetchall()]


def _asset_fields(entity, **extra):
    fields = {'name': 'Ноутбук', 'category_id': None, 'serial_number': 'SN-1', 'inventory_number': 'ИНВ-%s' % uuid.uuid4().hex[:6],
              'received_on': TODAY, 'cost': Decimal('350000'), 'legal_entity_id': entity, 'city': 'Алматы',
              'department_id': 1, 'department_name': 'СЗоВ', 'responsible_user_id': INITIATOR,
              'responsible_name': 'Инициаторова Ирина', 'location': 'Офис', 'status': 'in_use', 'note': None}
    fields.update(extra)
    return fields


def test_owner_company_change_is_a_history_row(db):
    with db.cursor() as cur:
        first, second = _entities(cur)
        rid = new_request(cur, stage='assets')
        asset_id = assets_sql.create_asset(cur, request_id=rid, fields=_asset_fields(first), actor=person(KEEPER))
        registered = assets_sql.list_moves(cur, asset_id)[0]
        assert (registered['kind'], registered['to_legal_entity_name']) == ('registered', 'ТОО «Первая»')

        assert assets_sql.move(cur, asset_id, fields={'responsible_user_id': WATCHER, 'responsible_name': 'Наблюдаев Нурлан'},
                               actor=person(KEEPER))
        moved = assets_sql.list_moves(cur, asset_id)[0]
        assert moved['kind'] == 'moved'
        assert (moved['from_legal_entity_name'], moved['to_legal_entity_name']) == ('ТОО «Первая»', 'ТОО «Первая»'), \
            'при перемещении компания стоит и «было», и «стало»'

        current = assets_sql.read_asset(cur, asset_id)
        assets_sql.update_details(cur, asset_id, {'legal_entity_id': second}, actor=person(KEEPER), current=current)
        owner = assets_sql.list_moves(cur, asset_id)[0]
        assert owner['kind'] == 'owner' and owner['moved_by'] == KEEPER
        assert (owner['from_legal_entity_id'], owner['to_legal_entity_id']) == (first, second)
        assert (owner['from_legal_entity_name'], owner['to_legal_entity_name']) == ('ТОО «Первая»', 'ТОО «Вторая»')
        assert owner['from_responsible_user_id'] == owner['to_responsible_user_id'] == WATCHER
        assert assets_sql.read_asset(cur, asset_id)['legal_entity_id'] == second

        before = len(assets_sql.list_moves(cur, asset_id))
        current = assets_sql.read_asset(cur, asset_id)
        assets_sql.update_details(cur, asset_id, {'legal_entity_id': second, 'note': 'стол 4'},
                                  actor=person(KEEPER), current=current)
        assets_sql.update_details(cur, asset_id, {'cost': Decimal('340000')}, actor=person(KEEPER),
                                  current=assets_sql.read_asset(cur, asset_id))
        assert len(assets_sql.list_moves(cur, asset_id)) == before, 'описание без смены компании истории не пишет'
        # Выдача со склада по-прежнему гаснет только с передачей — смена компании её не снимает.
        cur.execute("SELECT COUNT(*) FROM payment_asset_moves WHERE asset_id = %s AND kind = 'owner' "
                    "AND (to_responsible_user_id IS DISTINCT FROM from_responsible_user_id "
                    "     OR to_status IS DISTINCT FROM from_status)", (asset_id,))
        assert cur.fetchone()[0] == 0


def test_asset_of_an_archived_request_moves_with_an_act(db):
    with db.cursor() as cur:
        first, _second = _entities(cur)
        rid = new_request(cur, stage='closed', status='done')
        asset_id = assets_sql.create_asset(cur, request_id=rid, fields=_asset_fields(first), actor=person(KEEPER))
        queries.mark_deleted(cur, rid, person(ADMIN), 'тестовая')
        asset = assets_sql.read_asset(cur, asset_id)
        assert asset['request_id'] == rid and asset['request_deleted'], 'имущество живёт дальше, ссылка на заявку цела'
        assert assets_sql.list_assets(cur, limit=10)[1][0]['request_deleted'] is True
        # Так, как это делает ручка перемещения: акт ложится в заявку, строка истории — на акт.
        act = queries.add_attachment(cur, rid, kind='handover_act', file_name='акт.pdf', content_type='application/pdf',
                                     file_size=5, bucket='b', blob_path='p/act.pdf', actor=person(KEEPER),
                                     subtask_kind=workflow.KIND_ASSETS)
        assert assets_sql.move(cur, asset_id, fields={'responsible_user_id': WATCHER, 'responsible_name': 'Наблюдаев Нурлан'},
                               actor=person(KEEPER), attachment_id=act)
        assert assets_sql.list_moves(cur, asset_id)[0]['attachment_name'] == 'акт.pdf'
        assert assets_sql.attachment_in_history(cur, act)
        other = queries.add_attachment(cur, rid, kind='invoice', file_name='счёт.pdf', content_type='application/pdf',
                                       file_size=5, bucket='b', blob_path='p/inv.pdf', actor=person(INITIATOR))
        assert not assets_sql.attachment_in_history(cur, other), 'счёт заявки — не акт имущества'


# ─── П. 9: полные номера карт на доске финансового отдела ────────────────────

def test_card_numbers_and_the_access_journal(db):
    with db.cursor() as cur:
        first, second, plain = new_request(cur), new_request(cur), new_request(cur)
        queries.set_card_number(cur, first, '4111 1111 1111 1111')
        queries.set_card_number(cur, second, '5500000000000004')
        assert queries.card_numbers(cur, [first, second, plain, None]) == {
            first: '4111 1111 1111 1111', second: '5500 0000 0000 0004'}
        assert queries.card_numbers(cur, []) == {}
        assert queries.log_card_shown(cur, [first, second], person(FINANCE)) == 2
        assert queries.log_card_shown(cur, [first, second], person(FINANCE)) == 0, 'один раз на человека и заявку'
        assert queries.log_card_shown(cur, [first], person(ADMIN)) == 1
        assert queries.log_card_shown(cur, [first], {'id': None, 'name': 'iCore'}) == 0
        shown = [event for event in queries.list_events(cur, first, with_hidden=True) if event['kind'] == 'card_revealed']
        assert [(event['actor_id'], event['payload']) for event in shown] == [
            (FINANCE, {'shown': True, 'last4': '1111'}), (ADMIN, {'shown': True, 'last4': '1111'})]
        assert not [event for event in queries.list_events(cur, first) if event['kind'] == 'card_revealed'], \
            'в общей ленте журнала доступа нет'
        # Номер заменили на уточнении — показ нового номера тоже в журнале (разбор 09.11.2026).
        queries.set_card_number(cur, first, '5500 0000 0000 0004')
        assert queries.log_card_shown(cur, [first], person(FINANCE)) == 1
        assert queries.log_card_shown(cur, [first], person(FINANCE)) == 0


def test_card_shown_from_two_tabs_at_once_is_one_row(db):
    """Две вкладки доски финотдела в одну секунду: обе транзакции не видят строк друг друга,
    но вторую строку не пускает уникальный индекс (ON CONFLICT DO NOTHING)."""
    import threading
    with db.cursor() as cur:
        rid = new_request(cur)
        queries.set_card_number(cur, rid, '4111 1111 1111 1111')
    barrier = threading.Barrier(2)
    results, errors = [], []

    def tab():
        conn = db.connect()
        try:
            with conn.cursor() as cur:
                barrier.wait()
                results.append(queries.log_card_shown(cur, [rid], person(FINANCE)))
            # Первая вкладка держит транзакцию открытой: запрос второй идёт, пока её строка не видна.
            time.sleep(0.5)
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
            conn.rollback()
        finally:
            conn.close()

    threads = [threading.Thread(target=tab) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert not errors, errors
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM payment_events WHERE request_id = %s AND kind = 'card_revealed'", (rid,))
        assert cur.fetchone()[0] == 1
    assert sorted(results) == [0, 1], results
