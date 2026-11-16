# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: PAYMENTS_TEST_PORT — порт локального Postgres.

Правки владельца 16.11.2026 в «Оплате счетов» — на схеме раздела (payments.schema) и его
настоящем ходе (flow.submit / flow.act / flow.advance):

* пп. 3, 4, 10 — платёжное поручение, доверенность и подтверждение перевода «только если
  требуется»: без отметки оплату записывают датой и суммой, с отметкой — только с файлом;
* пп. 5, 6 — срок закрывающих документов ставит бухгалтер; заявку можно закрыть раньше
  документов: она закрыта, а документы ждут до срока — у инициатора задача на столе, у
  бухгалтерии карточка в «Ожидаются закрывающие документы», напоминания идут, документы
  досылают и закрывают уже в закрытой заявке;
* п. 7 — согласование с бухгалтерией (выбор юр. лица) у каждого нового закупа;
* пп. 8, 11, 12 — отделы внутри проекта: уникальны внутри проекта, отбор реестра по ним,
  используемый отдел и проект с отделами не удалить;
* п. 1 — прошлые закупы того же товара или услуги;
* п. 13 — срез «Оплачено».

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным
именем, в конце она удаляется. Люди и компании выдуманы.
"""
import contextlib
import os
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from payments import access, directory, flow, queries, schema, workflow

PORT = os.environ.get('PAYMENTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (PAYMENTS_TEST_PORT)')

INITIATOR, ACCOUNTANT, APPROVER, FINANCE, OUTSIDER = 10, 50, 70, 80, 90

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL DEFAULT 'operator',
                        status text NOT NULL DEFAULT 'working', department_id integer, supervisor_id integer,
                        telegram_id bigint, job_title text, avatar_bucket text, avatar_blob_path text);
    INSERT INTO departments (id, code, name) VALUES (1, 'adm', 'Администрация'), (2, 'acc', 'Бухгалтерия');
    INSERT INTO users (id, name, department_id) VALUES
        (10, 'Инициаторова Ирина', 1),
        (50, 'Счётова Сауле', 2),
        (70, 'Утвердова Ульяна', 1),
        (80, 'Финансова Фатима', 2),
        (90, 'Посторонний Пётр', 1);
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
    name = 't_payments_1116_' + uuid.uuid4().hex
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
    monkeypatch.setattr(access, 'SECTION_ALLOWED_USER_IDS', (INITIATOR, ACCOUNTANT, APPROVER, FINANCE))
    monkeypatch.setenv('JWT_SECRET', 'test-secret')
    monkeypatch.delenv('PAYMENTS_CARD_KEY', raising=False)
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + name + ' CASCADE')
        admin.commit()
        admin.close()


def person(cur, user_id):
    cur.execute('SELECT name FROM users WHERE id = %s', (user_id,))
    return {'id': user_id, 'name': cur.fetchone()[0]}


def seed(cur):
    for role, user_id in (('approver', APPROVER), ('accounting', ACCOUNTANT), ('finance', FINANCE)):
        queries.add_role_member(cur, role, user_id, None)
    cur.execute("INSERT INTO payment_legal_entities (name) VALUES ('ТОО «Альфа»') RETURNING id")
    entity = cur.fetchone()[0]
    cur.execute("INSERT INTO payment_categories (name) VALUES ('Оргтехника') RETURNING id")
    category = cur.fetchone()[0]
    cur.execute("INSERT INTO payment_counterparties (name, vat_payer) VALUES ('ТОО «Картриджи»', TRUE) RETURNING id")
    supplier = cur.fetchone()[0]
    return {'entity': entity, 'category': category, 'supplier': supplier}


def new_purchase(cur, ids, *, name='Картридж HP 85A', amount=Decimal('45000'), method='invoice', **extra):
    """Новый закуп, готовый к отправке: всё, что требует п. 4, и счёт с номером и датой."""
    fields = {'request_kind': 'purchase', 'department_id': 1, 'expense_name': name,
              'justification': 'Закончились', 'category_id': ids['category'], 'due_on': date(2026, 11, 30),
              'object_type': 'goods', 'accounting_category': 'consumable', 'payment_method': method,
              'legal_entity_id': ids['entity'], 'counterparty_id': ids['supplier'], 'no_alternatives': True,
              'no_alternatives_reason': 'single_supplier', 'invoice_number': 'СЧ-1', 'invoice_date': date(2026, 11, 10)}
    if method == 'card':
        fields.update(card_recipient='employee', card_holder_user_id=INITIATOR, card_holder_name='Инициаторова Ирина',
                      payment_purpose='Картриджи')
    fields.update(extra)
    rid = queries.create_request(cur, fields=fields,
                                 items=[{'name': name, 'quantity': 3, 'unit': 'шт', 'unit_price': amount / 3}],
                                 actor=person(cur, INITIATOR), initiator=person(cur, INITIATOR))
    queries.save_offers(cur, rid, [{'counterparty_id': ids['supplier'], 'supplier_name': 'ТОО «Картриджи»',
                                    'amount': amount, 'is_recommended': True}])
    queries.add_attachment(cur, rid, kind='invoice', file_name='счёт.pdf', content_type='application/pdf',
                           file_size=1, bucket='b', blob_path='p/invoice-%s.pdf' % rid, actor=person(cur, INITIATOR),
                           subtask_kind=workflow.KIND_INITIATION)
    if method == 'card':
        queries.set_card_number(cur, rid, '4111111111111111')
    return rid


def state(cur, rid, kind):
    item = queries.read_subtask(cur, rid, kind)
    return item['state'] if item else None


def act(cur, rid, kind, action, who, **fields):
    files = fields.pop('files', ())
    return flow.act(cur, rid, kind, action, person(cur, who), fields=fields, new_file_kinds=files,
                    outbox=[], base_url=None)


def to_payment(cur, rid):
    """Отправка → согласование с бухгалтерией (юр. лицо инициатора она подтверждает) → утверждение."""
    flow.submit(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)
    # Руководителя у инициатора нет — его этап пропущен; дальше бухгалтерия (16.11.2026, п. 7).
    assert state(cur, rid, workflow.KIND_ENTITY) == 'open'
    act(cur, rid, workflow.KIND_ENTITY, 'set_entity', ACCOUNTANT,
        legal_entity_id=queries.read_request(cur, rid)['legal_entity_id'])
    act(cur, rid, workflow.KIND_APPROVAL, 'approve', APPROVER)


def to_closing(cur, rid):
    to_payment(cur, rid)
    act(cur, rid, workflow.KIND_INVOICE, 'pay', ACCOUNTANT, paid_on='2026-11-12', paid_amount=Decimal('45000'))
    act(cur, rid, workflow.KIND_RECEIVING, 'confirm', INITIATOR, received_on='2026-11-14', received_quantity='3')
    assert state(cur, rid, workflow.KIND_CLOSING) == 'open'


def notices_of(cur, user_id, rid, kinds=('docs_needed', 'docs_due', 'closed')):
    """Непрочитанные строки колокола о документах и закрытии (сведения о ходе — «согласовано»,
    «оплачено» — гаснут, когда человек откроет заявку, и здесь не нужны)."""
    cur.execute("SELECT kind, title, actionable FROM payment_notifications WHERE user_id = %s AND request_id = %s "
                "AND read_at IS NULL AND kind = ANY(%s) ORDER BY id", (user_id, rid, list(kinds)))
    return cur.fetchall()


# ── пп. 3, 4, 10: документы оплаты «только если требуется» ──────────────────

def test_payment_without_marks_needs_no_files(db):
    with db.cursor() as cur:
        ids = seed(cur)
        rid = new_purchase(cur, ids)
        to_payment(cur, rid)
        act(cur, rid, workflow.KIND_INVOICE, 'pay', ACCOUNTANT, paid_on='2026-11-12', paid_amount=Decimal('45000'))
        assert state(cur, rid, workflow.KIND_INVOICE) == 'done'
        assert queries.read_request(cur, rid)['paid_on'] == date(2026, 11, 12)


def test_marked_payment_order_and_power_of_attorney_are_required(db):
    with db.cursor() as cur:
        ids = seed(cur)
        rid = new_purchase(cur, ids, needs_payment_order=True, needs_power_of_attorney=True)
        row = queries.read_request(cur, rid)
        assert (row['needs_payment_order'], row['needs_power_of_attorney'], row['needs_transfer_proof']) == (
            True, True, False)
        to_payment(cur, rid)
        with pytest.raises(flow.FlowError) as caught:
            act(cur, rid, workflow.KIND_INVOICE, 'pay', ACCOUNTANT, paid_on='2026-11-12', paid_amount=Decimal('45000'),
                files=['payment_order'])
        assert caught.value.missing == ['Приложите доверенность — её просят в заявке']
        act(cur, rid, workflow.KIND_INVOICE, 'pay', ACCOUNTANT, paid_on='2026-11-12', paid_amount=Decimal('45000'),
            files=['payment_order', 'power_of_attorney'])
        assert state(cur, rid, workflow.KIND_INVOICE) == 'done'


def test_marked_transfer_proof_is_required_for_the_card(db):
    with db.cursor() as cur:
        ids = seed(cur)
        rid = new_purchase(cur, ids, method='card', needs_transfer_proof=True)
        to_payment(cur, rid)
        assert state(cur, rid, workflow.KIND_CARD) == 'open'
        with pytest.raises(flow.FlowError) as caught:
            act(cur, rid, workflow.KIND_CARD, 'top_up', FINANCE, paid_on='2026-11-12', paid_amount=Decimal('45000'))
        assert caught.value.missing == ['Приложите подтверждение перевода — его просят в заявке']
        act(cur, rid, workflow.KIND_CARD, 'top_up', FINANCE, paid_on='2026-11-12', paid_amount=Decimal('45000'),
            files=['transfer_proof'])
        assert state(cur, rid, workflow.KIND_CARD) == 'done'


def test_flags_columns_survive_a_repeated_start(db):
    with db.cursor() as cur:
        for sql in schema.statements():
            cur.execute(sql)
        cur.execute("SELECT column_name, is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = 'payment_requests' AND column_name IN "
                    "('needs_payment_order', 'needs_power_of_attorney', 'needs_transfer_proof') ORDER BY column_name",
                    (db.name,))
        assert cur.fetchall() == [('needs_payment_order', 'NO', 'false'), ('needs_power_of_attorney', 'NO', 'false'),
                                  ('needs_transfer_proof', 'NO', 'false')]


# ── пп. 5, 6: срок закрывающих документов и закрытие заявки раньше них ─────────

def test_accountant_sets_the_docs_deadline(db):
    with db.cursor() as cur:
        ids = seed(cur)
        rid = new_purchase(cur, ids)
        to_closing(cur, rid)
        today = queries.today_almaty()
        settings = directory.get_settings(cur)
        default = workflow.docs_deadline(queries.read_request(cur, rid), queries.list_subtasks(cur, rid), settings)
        # По настройке — от подтверждения получения (момент нажатия, п. 17 с 09.11.2026) + 10 дней.
        received = queries.read_subtask(cur, rid, workflow.KIND_RECEIVING)['done_at'].date()
        assert default == received + timedelta(days=10)
        due = today + timedelta(days=5)
        with pytest.raises(flow.FlowError) as past:
            act(cur, rid, workflow.KIND_CLOSING, 'docs_due', ACCOUNTANT, docs_due_on=today - timedelta(days=1))
        assert past.value.missing == ['Срок закрывающих документов не может быть раньше сегодняшнего дня']
        act(cur, rid, workflow.KIND_CLOSING, 'docs_due', ACCOUNTANT, docs_due_on=due)
        row = queries.read_request(cur, rid)
        assert row['docs_due_on'] == due
        assert workflow.stage_deadline(queries.list_subtasks(cur, rid), settings, today, request=row)['due_on'] == due
        event = queries.list_events(cur, rid)[-1]
        assert (event['kind'], event['payload']['to']) == ('docs_due', due.isoformat())
        assert ('docs_due', 'Срок закрывающих документов — до %s' % workflow.fmt_date(due), False) in \
            notices_of(cur, INITIATOR, rid)
        assert state(cur, rid, workflow.KIND_CLOSING) == 'open', 'срок не закрывает этап'


def test_request_closed_before_the_docs_keeps_waiting_for_them(db):
    with db.cursor() as cur:
        ids = seed(cur)
        rid = new_purchase(cur, ids)
        to_closing(cur, rid)
        due = queries.today_almaty() + timedelta(days=7)
        act(cur, rid, workflow.KIND_CLOSING, 'docs_defer', ACCOUNTANT, docs_due_on=due, comment='Акт привезут позже')
        row = queries.read_request(cur, rid)
        assert (row['status'], row['closing_docs_status'], row['docs_due_on']) == ('done', 'none', due)
        assert row['docs_deferred_by'] == ACCOUNTANT and row['docs_deferred_at'] is not None
        assert row['docs_tail'] is True
        assert state(cur, rid, workflow.KIND_CLOSING) == 'open', 'этап документов ждёт и у закрытой заявки'
        kinds = [event['kind'] for event in queries.list_events(cur, rid)]
        assert kinds[-2:] == ['docs_deferred', 'closed']
        # Условия закрытия (п. 15): документы запрошены — это решение бухгалтера.
        closing = workflow.closing_conditions(row, queries.list_subtasks(cur, rid))
        assert closing[-1] == {'label': 'Закрывающие документы запрошены до %s' % workflow.fmt_date(due), 'ok': True}
        # Инициатору — задача: приложить документы до срока (строка гаснет, когда скан приложат).
        assert notices_of(cur, INITIATOR, rid) == [
            ('docs_needed', 'Заявка закрыта — приложите закрывающие документы до %s' % workflow.fmt_date(due), True)]
        assert queries.docs_wanted_ids(cur, INITIATOR) == [rid]
        assert queries.desk_count(cur, INITIATOR, set()) == 1
        assert access.can_upload_docs({'user_id': INITIATOR}, row, queries.list_subtasks(cur, rid))
        assert access.can_attach({'user_id': INITIATOR}, row, set())
        assert not access.can_attach({'user_id': OUTSIDER}, row, set())
        # Бухгалтерия видит её на доске в «Ожидаются закрывающие документы», напоминания идут.
        board = {item['id']: workflow.board_column('accounting', item, steps)
                 for item, steps in queries.board_requests(cur, 'accounting')}
        assert board == {rid: 'awaiting_docs'}
        assert [request['id'] for request, _stage, _received in queries.stage_deadline_rows(cur)] == [rid]
        # По закрытой заявке — только документы: закрыть её второй раз и платить нельзя.
        for kind, action in ((workflow.KIND_CLOSING, 'docs_defer'), (workflow.KIND_INVOICE, 'pay')):
            with pytest.raises(flow.FlowError) as closed:
                flow.load_for_action(cur, rid, kind, action)
            assert closed.value.code == 'PAYMENT_REQUEST_CLOSED'
        # Пока документов нет, их ждут от инициатора: в задачах бухгалтерии закрытой заявки нет.
        assert queries.desk_pairs(cur, ACCOUNTANT, {'accounting'}) == []
        assert queries.desk_count(cur, ACCOUNTANT, {'accounting'}) == 0
        # Инициатор досылает скан в закрытую заявку — «скан получен», его задача гаснет.
        queries.add_attachment(cur, rid, kind='act', file_name='акт.pdf', content_type='application/pdf',
                               file_size=1, bucket='b', blob_path='p/act.pdf', actor=person(cur, INITIATOR))
        assert flow.files_added(cur, rid, ['act'], person(cur, INITIATOR)) is True
        assert queries.read_request(cur, rid)['closing_docs_status'] == 'scan'
        assert queries.docs_wanted_ids(cur, INITIATOR) == []
        assert [kind for kind, _title, _actionable in notices_of(cur, INITIATOR, rid)] == []
        # …а у бухгалтерии она снова в задачах и в колоколе: документы пришли — проверить и закрыть.
        assert queries.desk_pairs(cur, ACCOUNTANT, {'accounting'}) == [(rid, workflow.KIND_CLOSING)]
        assert queries.desk_count(cur, ACCOUNTANT, {'accounting'}) == 1
        assert notices_of(cur, ACCOUNTANT, rid, kinds=('task',)) == [('task', 'Проверьте документы закрытой заявки', True)]
        # Второй файл — второго сообщения нет: статус уже «скан получен».
        assert flow.files_added(cur, rid, ['waybill'], person(cur, INITIATOR)) is False
        assert len(notices_of(cur, ACCOUNTANT, rid, kinds=('task',))) == 1
        # Бухгалтерия: оригинал получен, документы закрыты — заявка остаётся закрытой, второго «закрыта» нет.
        closed_at = queries.read_request(cur, rid)['closed_at']
        act(cur, rid, workflow.KIND_CLOSING, 'docs_original', ACCOUNTANT)
        assert act(cur, rid, workflow.KIND_CLOSING, 'docs_close', ACCOUNTANT) == {'next': None}
        row = queries.read_request(cur, rid)
        assert (row['status'], row['closing_docs_status'], row['closed_at'], row['docs_tail']) == (
            'done', 'closed', closed_at, False)
        assert state(cur, rid, workflow.KIND_CLOSING) == 'done'
        assert [event['kind'] for event in queries.list_events(cur, rid)].count('closed') == 1
        assert queries.stage_deadline_rows(cur) == []
        # Документы закрыты — задача и строка колокола бухгалтерии погасли.
        assert queries.desk_pairs(cur, ACCOUNTANT, {'accounting'}) == []
        assert notices_of(cur, ACCOUNTANT, rid, kinds=('task',)) == []


def test_closing_with_a_scan_already_in_tells_about_the_originals(db):
    with db.cursor() as cur:
        ids = seed(cur)
        rid = new_purchase(cur, ids)
        to_closing(cur, rid)
        queries.add_attachment(cur, rid, kind='waybill', file_name='накладная.pdf', content_type='application/pdf',
                               file_size=1, bucket='b', blob_path='p/w.pdf', actor=person(cur, INITIATOR))
        flow.files_added(cur, rid, ['waybill'], person(cur, INITIATOR))
        due = queries.today_almaty() + timedelta(days=3)
        act(cur, rid, workflow.KIND_CLOSING, 'docs_defer', ACCOUNTANT, docs_due_on=due)
        # Скан уже есть — задачи «приложите» нет, только сведения об оригиналах.
        assert notices_of(cur, INITIATOR, rid) == [
            ('closed', 'Заявка закрыта — оригиналы документов до %s' % workflow.fmt_date(due), False)]
        assert queries.docs_wanted_ids(cur, INITIATOR) == []
        assert not access.can_upload_docs({'user_id': INITIATOR}, queries.read_request(cur, rid),
                                          queries.list_subtasks(cur, rid))


# ── п. 7: согласование с бухгалтерией ──────────────────────────────────────

def test_initiators_company_is_a_hint_accounting_may_change_it(db):
    with db.cursor() as cur:
        ids = seed(cur)
        cur.execute("INSERT INTO payment_legal_entities (name) VALUES ('ИП «Бета»') RETURNING id")
        other = cur.fetchone()[0]
        rid = new_purchase(cur, ids)
        flow.submit(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)
        assert state(cur, rid, workflow.KIND_ENTITY) == 'open'
        assert queries.read_request(cur, rid)['current_role_code'] == 'accounting'
        act(cur, rid, workflow.KIND_ENTITY, 'set_entity', ACCOUNTANT, legal_entity_id=other)
        row = queries.read_request(cur, rid)
        assert (row['legal_entity_id'], row['legal_entity_name']) == (other, 'ИП «Бета»')
        assert state(cur, rid, workflow.KIND_APPROVAL) == 'open'


# ── пп. 8, 11, 12: отделы внутри проекта ────────────────────────────────────

def test_project_departments(db):
    with db.cursor() as cur:
        ids = seed(cur)
        tez = directory.upsert_project(cur, name='Tez', actor_id=APPROVER)
        yandex = directory.upsert_project(cur, name='Яндекс', actor_id=APPROVER)
        call_centre = directory.upsert_project_department(cur, project_id=tez, name='Колл-центр', actor_id=APPROVER)
        directory.upsert_project_department(cur, project_id=tez, name='Отдел продаж', actor_id=APPROVER)
        directory.upsert_project_department(cur, project_id=yandex, name='Колл-центр', actor_id=APPROVER)
        hidden = directory.upsert_project_department(cur, project_id=yandex, name='Архив', is_active=False)
        # Одно название в двух проектах — можно, дважды в одном — нет.
        assert directory.find_duplicate_name(cur, 'колл-центр', dictionary='project_departments', parent_id=tez) == (
            call_centre, 'Колл-центр')
        assert directory.find_duplicate_name(cur, 'Колл-центр', dictionary='project_departments', row_id=call_centre,
                                             parent_id=tez) is None
        tree = {row['name']: [item['name'] for item in row['departments']]
                for row in directory.list_projects(cur, include_inactive=True, with_departments=True)}
        assert tree == {'Tez': ['Колл-центр', 'Отдел продаж'], 'Яндекс': ['Архив', 'Колл-центр']}
        # В форму — только действующие отделы действующих проектов.
        assert [(item['project_name'], item['name']) for item in directory.list_project_departments(cur)] == [
            ('Tez', 'Колл-центр'), ('Tez', 'Отдел продаж'), ('Яндекс', 'Колл-центр')]
        assert hidden not in [item['id'] for item in directory.list_project_departments(cur)]
        # Заявка относится к проекту и его отделу; реестр отбирает по ним.
        rid = new_purchase(cur, ids, project_id=tez, project_department_id=call_centre)
        new_purchase(cur, ids, name='Бумага', project_id=yandex)
        row = queries.read_request(cur, rid)
        assert (row['project_name'], row['project_department_name']) == ('Tez', 'Колл-центр')
        assert [item['id'] for item in queries.list_requests(cur, project_department_id=call_centre)[1]] == [rid]
        assert queries.list_requests(cur, project_id=tez)[0] == 1
        # Используемый отдел и проект с отделами не удаляются: их скрывают.
        assert directory.usage(cur, 'project_departments', call_centre) == [('в заявках', 1)]
        assert ('в отделах проекта', 2) in directory.usage(cur, 'projects', tez)


# ── п. 1: прошлые закупы того же товара или услуги ──────────────────────────

def test_similar_purchases(db):
    with db.cursor() as cur:
        ids = seed(cur)
        cur.execute("INSERT INTO payment_categories (name) VALUES ('Хозтовары') RETURNING id")
        other_category = cur.fetchone()[0]

        def paid(rid, on):
            queries.update_request_fields(cur, rid, {'paid_on': on, 'paid_amount': Decimal('45000')})

        same = new_purchase(cur, ids, name='Картридж HP 85A')
        longer = new_purchase(cur, ids, name='Картридж HP 85A чёрный', category_id=other_category)
        unpaid = new_purchase(cur, ids, name='Картридж HP 85A')
        paper = new_purchase(cur, ids, name='Бумага А4')
        paid(same, date(2026, 10, 1))
        paid(longer, date(2026, 11, 1))
        paid(paper, date(2026, 11, 2))
        current = new_purchase(cur, ids, name='Картридж HP 85A')
        rows = queries.similar_purchases(cur, queries.read_request(cur, current), queries.list_items(cur, current))
        # Сначала та же категория, потом остальные по названию; неоплаченной и бумаги нет.
        assert [(row['request_id'], row['match'], row['same_category']) for row in rows] == [
            (same, 'item', True), (longer, 'item', False)]
        assert rows[0]['item_name'] == 'Картридж HP 85A' and rows[0]['unit_price'] == Decimal('15000.00')
        assert unpaid not in [row['request_id'] for row in rows]
        # Видимость — как у реестра: постороннему чужие закупы не показываются.
        assert queries.similar_purchases(cur, queries.read_request(cur, current), queries.list_items(cur, current),
                                         visible_to=OUTSIDER, visible_roles=set()) == []
        # Удалённая (архив) — не прошлый закуп.
        queries.mark_deleted(cur, same, person(cur, APPROVER))
        assert [row['request_id'] for row in queries.similar_purchases(
            cur, queries.read_request(cur, current), queries.list_items(cur, current))] == [longer]


# ── п. 13: срез «Оплачено» ───────────────────────────────────────────────────

def test_paid_slice_counts_paid_requests_still_going(db):
    with db.cursor() as cur:
        ids = seed(cur)
        going = new_purchase(cur, ids)
        new_purchase(cur, ids, name='Бумага')
        to_closing(cur, going)
        closed = new_purchase(cur, ids, name='Мышь')
        to_closing(cur, closed)
        act(cur, closed, workflow.KIND_CLOSING, 'docs_defer', ACCOUNTANT,
            docs_due_on=queries.today_almaty() + timedelta(days=1))
        counters = queries.state_counters(cur)
        assert counters['paid'] == 1, 'закрытая — в «Закрыты», неоплаченная — не в срезе'
        assert [item['id'] for item in queries.list_requests(cur, state='paid')[1]] == [going]
