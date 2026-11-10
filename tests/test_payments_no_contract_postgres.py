# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: PAYMENTS_TEST_PORT — порт локального Postgres.

«Без договора» (решение владельца 10.11.2026) на схеме раздела (payments.schema) и его
настоящем ходе (flow.submit / flow.act / flow.advance):

* счёт свыше 300 000 ₸ без договора и без отметки после согласования возвращается
  инициатору («Нужен действующий договор»); ответ с отметкой «Без договора» уводит
  его обратно бухгалтерии без нового согласования;
* отметка, поставленная сразу, пускает счёт к бухгалтерии без возврата, а бухгалтер
  может сам запросить договор — причиной из списка;
* регулярный платёж хранит отметку, заявка по нему берёт её из справочника; рядом с
  договором отметки нет ни в справочнике, ни в заявке;
* колонки — NOT NULL DEFAULT FALSE, повторный старт схемы ничего не ломает.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным
именем, в конце она удаляется. Запуск:

    PAYMENTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_payments_no_contract_postgres.py

Люди и компании выдуманы.
"""
import contextlib
import os
import uuid
from datetime import date
from decimal import Decimal

import pytest

from payments import access, directory, fixed, flow, queries, schema, workflow

PORT = os.environ.get('PAYMENTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (PAYMENTS_TEST_PORT)')

INITIATOR, ACCOUNTANT, APPROVER = 10, 50, 70
ADMIN_ID = 70  # кто правит справочник — для истории шаблона не важно

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
    name = 't_payments_no_contract_' + uuid.uuid4().hex
    base = _Db(name)
    admin = base.connect()
    # Только таблицы раздела (schema.statements): без pg_trgm и переноса первой версии.
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
    monkeypatch.setattr(access, 'SECTION_ALLOWED_USER_IDS', (INITIATOR, ACCOUNTANT, APPROVER))
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


def setup_directories(cur):
    for role, user_id in (('approver', APPROVER), ('accounting', ACCOUNTANT)):
        cur.execute("INSERT INTO payment_role_members (role_code, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (role, user_id))
    cur.execute("INSERT INTO payment_legal_entities (name) VALUES ('ТОО «Плательщик»') RETURNING id")
    entity = cur.fetchone()[0]
    cur.execute("INSERT INTO payment_categories (name) VALUES ('Мебель') RETURNING id")
    category = cur.fetchone()[0]
    cur.execute("INSERT INTO payment_counterparties (name, vat_payer) VALUES ('ТОО «Столы»', FALSE) RETURNING id")
    supplier = cur.fetchone()[0]
    return entity, category, supplier


def new_purchase(cur, *, amount, no_contract=False):
    """Новый закуп по счёту, готовый к отправке: всё, что требует п. 4, и счёт с номером и датой."""
    entity, category, supplier = setup_directories(cur)
    fields = {'request_kind': 'purchase', 'department_id': 1, 'expense_name': 'Столы в переговорную',
              'justification': 'Новый офис', 'category_id': category, 'due_on': date(2026, 11, 30),
              'object_type': 'service', 'payment_method': 'invoice', 'legal_entity_id': entity,
              'counterparty_id': supplier, 'no_alternatives': True, 'no_alternatives_reason': 'single_supplier',
              'invoice_number': 'СЧ-77', 'invoice_date': date(2026, 11, 10), 'no_contract': no_contract}
    rid = queries.create_request(cur, fields=fields,
                                 items=[{'name': 'Стол', 'quantity': 1, 'unit': 'шт', 'unit_price': amount}],
                                 actor=person(cur, INITIATOR), initiator=person(cur, INITIATOR))
    queries.save_offers(cur, rid, [{'counterparty_id': supplier, 'supplier_name': 'ТОО «Столы»',
                                    'amount': amount, 'is_recommended': True}])
    queries.add_attachment(cur, rid, kind='invoice', file_name='счёт.pdf', content_type='application/pdf',
                           file_size=1, bucket='b', blob_path='p/invoice.pdf', actor=person(cur, INITIATOR),
                           subtask_kind=workflow.KIND_INITIATION)
    return rid


def state(cur, rid, kind):
    item = queries.read_subtask(cur, rid, kind)
    return item['state'] if item else None


def submit(cur, rid):
    return flow.submit(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)


def act(cur, rid, kind, action, who, **fields):
    files = fields.pop('files', ())
    return flow.act(cur, rid, kind, action, person(cur, who), fields=fields, new_file_kinds=files,
                    outbox=[], base_url=None)


def test_empty_field_returns_the_invoice_and_no_contract_answers_it(db):
    with db.cursor() as cur:
        rid = new_purchase(cur, amount=900000)
        assert queries.read_request(cur, rid)['no_contract'] is False, 'не выбрано — не «Без договора»'
        submit(cur, rid)
        assert state(cur, rid, workflow.KIND_APPROVAL) == 'open'
        act(cur, rid, workflow.KIND_APPROVAL, 'approve', APPROVER)

        # Свыше порога без договора и без отметки — назад к инициатору, как и раньше.
        initiation = queries.read_subtask(cur, rid, workflow.KIND_INITIATION)
        assert initiation['state'] == 'open' and initiation['clarify_reason'] == 'contract_required'
        assert '«Без договора»' in initiation['clarify_comment']
        assert state(cur, rid, workflow.KIND_INVOICE) == 'waiting'

        # Пустой ответ не проходит — правило то же, что у бухгалтерии.
        with pytest.raises(flow.FlowError) as empty:
            submit(cur, rid)
        assert empty.value.code == 'PAYMENT_INCOMPLETE'

        # Инициатор выбрал «Без договора» — счёт уходит бухгалтерии без нового согласования.
        queries.update_request_fields(cur, rid, {'no_contract': True})
        result = submit(cur, rid)
        assert result == {'rerouted': False, 'next': workflow.KIND_INVOICE, 'changed': []}
        assert state(cur, rid, workflow.KIND_INVOICE) == 'open'
        assert state(cur, rid, workflow.KIND_APPROVAL) == 'done'


def test_no_contract_chosen_upfront_goes_straight_to_accounting(db):
    with db.cursor() as cur:
        rid = new_purchase(cur, amount=900000, no_contract=True)
        submit(cur, rid)
        act(cur, rid, workflow.KIND_APPROVAL, 'approve', APPROVER)
        assert state(cur, rid, workflow.KIND_INVOICE) == 'open'
        assert state(cur, rid, workflow.KIND_INITIATION) == 'done', 'возврата инициатору не было'

        # Договор всё же нужен — бухгалтер возвращает заявку причиной из списка.
        act(cur, rid, workflow.KIND_INVOICE, 'request_info', ACCOUNTANT, reason='contract_required',
            comment='Сумма большая — нужен договор поставки')
        initiation = queries.read_subtask(cur, rid, workflow.KIND_INITIATION)
        assert initiation['state'] == 'open' and initiation['clarify_reason'] == 'contract_required'
        assert state(cur, rid, workflow.KIND_INVOICE) == 'waiting'


def test_contract_and_flag_never_stand_together(db):
    with db.cursor() as cur:
        _entity, _category, supplier = setup_directories(cur)
        cur.execute("INSERT INTO payment_contracts (counterparty_id, number, status) VALUES (%s, '12', 'active') "
                    "RETURNING id", (supplier,))
        contract = cur.fetchone()[0]
        rid = queries.create_request(cur, fields={'request_kind': 'purchase', 'expense_name': 'Столы',
                                                  'counterparty_id': supplier, 'contract_id': contract,
                                                  'no_contract': True},
                                     items=[], actor=person(cur, INITIATOR), initiator=person(cur, INITIATOR))
        row = queries.read_request(cur, rid)
        assert (row['contract_id'], row['contract_number'], row['no_contract']) == (contract, '12', False)


def template_fields(supplier, **patch):
    fields = {'name': 'Аренда офиса', 'amount': Decimal('500000'), 'periodicity': 'monthly',
              'interval_days': None, 'next_due_on': date(2026, 12, 5), 'lead_days': 7,
              'responsible_user_id': INITIATOR, 'counterparty_id': supplier, 'object_type': 'service',
              'auto_create': True, 'is_active': True, 'no_contract': True}
    fields.update(patch)
    return fields


def test_regular_template_keeps_the_choice_and_hands_it_to_the_request(db):
    with db.cursor() as cur:
        _entity, _category, supplier = setup_directories(cur)
        template_id = directory.upsert_template(cur, fields=template_fields(supplier), actor_id=INITIATOR)
        template = directory.read_template(cur, template_id)
        assert template['no_contract'] is True
        fields = fixed.request_fields(template)
        assert fields['no_contract'] is True
        rid = queries.create_request(cur, fields=dict(fields, request_kind='regular'), items=[],
                                     actor=person(cur, INITIATOR), initiator=person(cur, INITIATOR))
        assert queries.read_request(cur, rid)['no_contract'] is True

        # Пустой выбор — не «Без договора»: колонка NOT NULL, а значит False, а не NULL.
        plain_id = directory.upsert_template(cur, fields=template_fields(supplier, name='Связь', no_contract=None),
                                             actor_id=INITIATOR)
        assert directory.read_template(cur, plain_id)['no_contract'] is False


def test_template_with_contract_drops_the_flag(db):
    with db.cursor() as cur:
        _entity, _category, supplier = setup_directories(cur)
        fields = template_fields(supplier)
        cur.execute("INSERT INTO payment_contracts (counterparty_id, number, status) VALUES (%s, '7', 'active') "
                    "RETURNING id", (fields['counterparty_id'],))
        fields['contract_id'] = cur.fetchone()[0]
        template_id = directory.upsert_template(cur, fields=fields, actor_id=INITIATOR)
        template = directory.read_template(cur, template_id)
        assert (template['contract_number'], template['no_contract']) == ('7', False)
        assert fixed.request_fields(template)['no_contract'] is False


def test_columns_and_repeated_start(db):
    with db.cursor() as cur:
        for sql in schema.statements():
            cur.execute(sql)
        cur.execute("SELECT table_name, is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_schema = %s AND column_name = 'no_contract' ORDER BY table_name", (db.name,))
        assert cur.fetchall() == [('payment_fixed_templates', 'NO', 'false'), ('payment_requests', 'NO', 'false')]


def test_accountant_asking_for_a_contract_breaks_the_loop(db):
    """Разбор 10.11.2026: бухгалтер запросил договор у счёта с «Без договора», а ответ без единой
    правки возвращал заявку ему же. Теперь отметка снимается, и без нового выбора ответ не проходит."""
    with db.cursor() as cur:
        rid = new_purchase(cur, amount=900000, no_contract=True)
        submit(cur, rid)
        act(cur, rid, workflow.KIND_APPROVAL, 'approve', APPROVER)
        act(cur, rid, workflow.KIND_INVOICE, 'request_info', ACCOUNTANT, reason='contract_required')
        row = queries.read_request(cur, rid)
        assert row['no_contract'] is False
        initiation = queries.read_subtask(cur, rid, workflow.KIND_INITIATION)
        assert initiation['clarify_comment'] == workflow.CLARIFY_BY_CODE['contract_required']['ask'], \
            'окно бухгалтера обещает инициатору этот текст'
        assert flow.answer_problems(cur, row) == [workflow.contract_missing_text(row)]
        with pytest.raises(flow.FlowError) as unchanged:
            submit(cur, rid)
        assert unchanged.value.code == 'PAYMENT_INCOMPLETE'
        # Договора правда нет — инициатор снова выбирает «Без договора», решает бухгалтер.
        queries.update_request_fields(cur, rid, {'no_contract': True})
        assert flow.answer_problems(cur, queries.read_request(cur, rid)) == []
        assert submit(cur, rid)['next'] == workflow.KIND_INVOICE
        assert state(cur, rid, workflow.KIND_INVOICE) == 'open'


def test_regular_reply_takes_the_choice_from_the_directory(db):
    """Разбор 10.11.2026: «Отправить ответ» регулярной заявки не видел «Без договора», которое
    администратор поставил в справочнике после её возврата."""
    with db.cursor() as cur:
        entity, category, supplier = setup_directories(cur)
        template_id = directory.upsert_template(cur, fields=template_fields(
            supplier, legal_entity_id=entity, category_id=category, no_contract=False), actor_id=INITIATOR)
        template = directory.read_template(cur, template_id)
        fields = dict(fixed.request_fields(template), request_kind='regular', department_id=1,
                      payment_period='декабрь 2026', invoice_number='А-5', invoice_date=date(2026, 11, 10))
        rid = queries.create_request(cur, fields=fields,
                                     items=[{'name': 'Аренда офиса', 'quantity': 1, 'unit_price': 500000}],
                                     actor=person(cur, INITIATOR), initiator=person(cur, INITIATOR))
        queries.add_attachment(cur, rid, kind='invoice', file_name='счёт.pdf', content_type='application/pdf',
                               file_size=1, bucket='b', blob_path='p/rent.pdf', actor=person(cur, INITIATOR),
                               subtask_kind=workflow.KIND_INITIATION)
        submit(cur, rid)
        act(cur, rid, workflow.KIND_APPROVAL, 'approve', APPROVER)
        initiation = queries.read_subtask(cur, rid, workflow.KIND_INITIATION)
        assert initiation['clarify_reason'] == 'contract_required'
        assert '«Регулярные платежи»' in initiation['clarify_comment'], 'инициатору — кого просить, а не «выберите»'
        assert flow.answer_problems(cur, queries.read_request(cur, rid)) == [
            workflow.contract_missing_text({'request_kind': 'regular'})]

        # Администратор поставил в справочнике «Без договора» — ответ из окна заявки проходит.
        directory.upsert_template(cur, template_id=template_id, fields=dict(template_fields(
            supplier, legal_entity_id=entity, category_id=category), no_contract=True), actor_id=ADMIN_ID)
        assert flow.answer_problems(cur, queries.read_request(cur, rid)) == []
        assert submit(cur, rid)['next'] == workflow.KIND_INVOICE
        assert queries.read_request(cur, rid)['no_contract'] is True
        edited = [event for event in queries.list_events(cur, rid) if event['kind'] == 'edited']
        assert edited and edited[-1]['payload']['changes'] == {'no_contract': [False, True]}
