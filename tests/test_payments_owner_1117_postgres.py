# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: PAYMENTS_TEST_PORT — порт локального Postgres.

Правки владельца 17.11.2026 в «Оплате счетов» — на схеме раздела и его настоящем ходе:

* пп. 1, 6, 7 — регулярный платёж «без счёта» и проверка бухгалтерией 1-го числа: календарь
  заводит заявку, она сначала у проверяющего из справочника, «Проверено» ведёт дальше, а оплата
  без счёта не возвращается инициатору за счётом; проверяющий ушёл из бухгалтерии — проверит роль;
* пп. 2–4 — сколько выбрано по договору: оплачено, в работе, своя заявка не считается дважды;
* п. 8 — проверка по БИН у договора ложится и в карточку поставщика;
* п. 10 — журнал раздела: строки пишутся, журнал неизменяем, лента «Журнал» показывает и ход
  заявок, и строки журнала; повторный просмотр той же заявки — одна строка.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным
именем, в конце она удаляется. Люди и компании выдуманы.
"""
import contextlib
import os
import uuid
from datetime import date
from decimal import Decimal

import pytest

from payments import access, audit, directory, fixed, flow, queries, schema, workflow

PORT = os.environ.get('PAYMENTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (PAYMENTS_TEST_PORT)')

RESPONSIBLE, ACCOUNTANT, CHECKER, APPROVER, OUTSIDER = 10, 50, 60, 70, 90

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL DEFAULT 'operator',
                        status text NOT NULL DEFAULT 'working', department_id integer, supervisor_id integer,
                        telegram_id bigint, job_title text, avatar_bucket text, avatar_blob_path text);
    INSERT INTO departments (id, code, name) VALUES (1, 'adm', 'Администрация'), (2, 'acc', 'Бухгалтерия');
    INSERT INTO users (id, name, department_id) VALUES
        (10, 'Ответственнова Ольга', 1),
        (50, 'Счётова Сауле', 2),
        (60, 'Проверкина Полина', 2),
        (70, 'Утвердова Ульяна', 1),
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
    name = 't_payments_1117_' + uuid.uuid4().hex
    base = _Db(name)
    admin = base.connect()
    try:
        with admin.cursor() as cur:
            cur.execute('CREATE SCHEMA ' + name)
            cur.execute('SET search_path TO ' + name)
            cur.execute(BASE_DDL)
            for sql in schema.statements():
                cur.execute(sql)
            # Второй прогон схемы — как второй старт сервера: проверки наличия не ломаются.
            for sql in schema.statements():
                cur.execute(sql)
        admin.commit()
    except Exception:
        admin.rollback()
        admin.close()
        raise
    monkeypatch.setattr(access, 'SECTION_ALLOWED_USER_IDS', (RESPONSIBLE, ACCOUNTANT, CHECKER, APPROVER))
    monkeypatch.setenv('JWT_SECRET', 'test-secret')
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


def seed(cur, *, checker_in_accounting=True):
    members = [('approver', APPROVER), ('accounting', ACCOUNTANT)]
    if checker_in_accounting:
        members.append(('accounting', CHECKER))
    for role, user_id in members:
        queries.add_role_member(cur, role, user_id, None)
    cur.execute("INSERT INTO payment_legal_entities (name, bin) VALUES ('ТОО «Альфа»', '990000000011') RETURNING id")
    entity = cur.fetchone()[0]
    cur.execute("INSERT INTO payment_counterparties (name, bin, vat_payer) VALUES ('ТОО «Арендодатель»', '990000000106', TRUE) "
                "RETURNING id")
    supplier = cur.fetchone()[0]
    contract = directory.upsert_contract(cur, fields={
        'counterparty_id': supplier, 'legal_entity_id': entity, 'number': 'А-01', 'signed_on': date(2026, 1, 1),
        'starts_on': date(2026, 1, 1), 'ends_on': date(2026, 12, 31), 'status': 'active', 'subject': 'Аренда',
        'amount': Decimal('450000'), 'amount_limit': Decimal('5400000'), 'periodicity': 'monthly',
        'responsible_user_id': None, 'note': None})
    return {'entity': entity, 'supplier': supplier, 'contract': contract}


def template(cur, ids, **overrides):
    fields = {
        'name': 'Аренда офиса', 'amount': Decimal('450000'), 'periodicity': 'monthly', 'interval_days': None,
        'next_due_on': date(2026, 12, 5), 'lead_days': 7, 'project_id': None, 'branch': None,
        'responsible_user_id': RESPONSIBLE, 'category_id': None, 'subcategory_id': None,
        'counterparty_id': ids['supplier'], 'legal_entity_id': ids['entity'], 'contract_id': ids['contract'],
        'no_contract': False, 'counterparty_account_id': None, 'payment_purpose': 'Аренда офиса',
        'amount_limit': None, 'approver_user_id': None, 'object_type': 'service', 'accounting_category': None,
        'due_day': 5, 'auto_create': True, 'note': None, 'is_active': True,
        'invoice_required': False, 'checker_user_id': CHECKER,
    }
    fields.update(overrides)
    return directory.upsert_template(cur, fields=fields, actor_id=None)


def state(cur, rid, kind):
    item = queries.read_subtask(cur, rid, kind)
    return item['state'] if item else None


def act(cur, rid, kind, action, who, **fields):
    return flow.act(cur, rid, kind, action, person(cur, who), fields=fields, outbox=[], base_url=None)


# ── пп. 1, 6, 7 ──────────────────────────────────────────────────────────────

def test_calendar_request_goes_to_the_checker_first_and_needs_no_invoice(db):
    with db.cursor() as cur:
        ids = seed(cur)
        tid = template(cur, ids)
        created, _outbox = fixed.generate(cur, today=date(2026, 12, 1))
        assert len(created) == 1
        rid = created[0]
        request = queries.read_request(cur, rid)
        assert request['invoice_required'] is False
        assert request['due_on'] == date(2026, 12, 5)
        check = queries.read_subtask(cur, rid, workflow.KIND_REGULAR_CHECK)
        assert (check['state'], check['assignee_id']) == ('open', CHECKER)
        # Проверено — дальше утверждение (руководителя у ответственного нет), затем оплата без счёта.
        act(cur, rid, workflow.KIND_REGULAR_CHECK, 'approve', CHECKER)
        assert state(cur, rid, workflow.KIND_REGULAR_CHECK) == 'done'
        assert queries.read_subtask(cur, rid, workflow.KIND_REGULAR_CHECK)['outcome'] == 'checked'
        act(cur, rid, workflow.KIND_APPROVAL, 'approve', APPROVER)
        assert state(cur, rid, workflow.KIND_INVOICE) == 'open', 'без счёта — бухгалтерии, а не назад за счётом'
        kinds = [event['kind'] for event in queries.list_events(cur, rid)]
        assert 'checked' in kinds
        # Срок сдвинулся на период вперёд, второй заявки за этот месяц не будет.
        assert directory.read_template(cur, tid)['next_due_on'] == date(2027, 1, 5)
        assert fixed.generate(cur, today=date(2026, 12, 2))[0] == []


def test_with_invoice_payment_waits_for_the_invoice(db):
    with db.cursor() as cur:
        ids = seed(cur)
        template(cur, ids, invoice_required=True)
        rid = fixed.generate(cur, today=date(2026, 12, 1))[0][0]
        act(cur, rid, workflow.KIND_REGULAR_CHECK, 'approve', CHECKER)
        act(cur, rid, workflow.KIND_APPROVAL, 'approve', APPROVER)
        assert state(cur, rid, workflow.KIND_INVOICE) == 'waiting'
        assert queries.read_request(cur, rid)['clarify_reason'] == 'invoice_missing'


def test_checker_out_of_accounting_means_the_whole_role(db):
    with db.cursor() as cur:
        ids = seed(cur, checker_in_accounting=False)
        template(cur, ids)
        rid = fixed.generate(cur, today=date(2026, 12, 1))[0][0]
        check = queries.read_subtask(cur, rid, workflow.KIND_REGULAR_CHECK)
        assert (check['state'], check['assignee_id'], check['role_code']) == ('open', None, workflow.ROLE_ACCOUNTING)


def test_check_returned_goes_back_to_the_responsible_and_returns(db):
    with db.cursor() as cur:
        ids = seed(cur)
        template(cur, ids)
        rid = fixed.generate(cur, today=date(2026, 12, 1))[0][0]
        act(cur, rid, workflow.KIND_REGULAR_CHECK, 'return', CHECKER, comment='Сумма за декабрь другая')
        assert state(cur, rid, workflow.KIND_REGULAR_CHECK) == 'waiting'
        assert workflow.board_column(workflow.BOARD_ACCOUNTING, queries.read_request(cur, rid),
                                     queries.list_subtasks(cur, rid)) == 'clarification'
        flow.submit(cur, rid, person(cur, RESPONSIBLE), outbox=[], base_url=None)
        assert state(cur, rid, workflow.KIND_REGULAR_CHECK) == 'open'


# ── пп. 2–4 ──────────────────────────────────────────────────────────────────

def test_contract_usage_counts_paid_and_in_work_once(db):
    with db.cursor() as cur:
        ids = seed(cur)
        template(cur, ids)
        first = fixed.generate(cur, today=date(2026, 12, 1))[0][0]
        queries.update_request_fields(cur, first, {'paid_on': date(2026, 12, 4), 'paid_amount': Decimal('450000')})
        second = fixed.generate(cur, today=date(2027, 1, 1))[0][0]
        usage = queries.contract_usage(cur, [ids['contract']])[ids['contract']]
        assert (usage['paid'], usage['in_work']) == (Decimal('450000'), Decimal('450000'))
        mine = queries.contract_usage(cur, [ids['contract']], exclude_request_id=second)[ids['contract']]
        assert (mine['paid'], mine['in_work']) == (Decimal('450000'), Decimal('0'))
        view = workflow.contract_view(directory.read_contract(cur, ids['contract']), mine, amount=Decimal('450000'),
                                      today=date(2027, 1, 1))
        assert view['limit']['after'] == Decimal('4500000')
        assert queries.contract_usage(cur, []) == {}


# ── п. 8 ─────────────────────────────────────────────────────────────────────

def test_bin_check_lands_on_the_supplier(db):
    with db.cursor() as cur:
        ids = seed(cur)
        checked_at = directory.set_contract_tax(cur, ids['contract'], vat=False, regime='snr',
                                                note='на основе упрощённой декларации', source='kgd',
                                                actor=person(cur, ACCOUNTANT))
        contract = directory.read_contract(cur, ids['contract'])
        assert (contract['tax_vat'], contract['tax_regime'], contract['tax_source'], contract['tax_checked_by_name']) == (
            False, 'snr', 'kgd', 'Счётова Сауле')
        supplier = directory.read_counterparty(cur, ids['supplier'])
        assert (supplier['vat_payer'], supplier['tax_regime'], supplier['tax_regime_note']) == (
            False, 'snr', 'на основе упрощённой декларации')
        assert supplier['tax_checked_at'] == checked_at
        directory.set_contract_tax_file(cur, ids['contract'], file_name='Выписка.pdf', file_type='application/pdf',
                                        file_size=10, bucket='b', blob_path='p/kgd.pdf')
        assert directory.read_contract(cur, ids['contract'])['has_tax_file'] is True
        assert directory.contract_tax_file(cur, ids['contract'])['blob_path'] == 'p/kgd.pdf'


# ── п. 10 ────────────────────────────────────────────────────────────────────

def test_journal_writes_reads_and_cannot_be_edited(db):
    with db.cursor() as cur:
        ids = seed(cur)
        before = audit.snapshot(cur, 'contract', ids['contract'])
        directory.upsert_contract(cur, contract_id=ids['contract'], fields=dict(
            {key: before[key] for key in ('counterparty_id', 'legal_entity_id', 'number', 'signed_on', 'starts_on',
                                          'ends_on', 'status', 'subject', 'amount', 'amount_limit', 'periodicity',
                                          'responsible_user_id', 'note')}, ends_on=date(2027, 6, 30)))
        row_id = audit.record_change(cur, person(cur, ACCOUNTANT), 'contract', ids['contract'], before,
                                     audit.snapshot(cur, 'contract', ids['contract']))
        assert row_id
        history = audit.history(cur, 'contract', ids['contract'])
        assert history[0]['changes'] == {'ends_on': ['2026-12-31', '2027-06-30']}
        assert history[0]['action_label'] == 'Изменение'

    with db.cursor() as cur:
        with pytest.raises(Exception, match='журнал раздела неизменяем'):
            cur.execute('UPDATE payment_audit_log SET title = %s', ('правка',))


def test_journal_feed_mixes_events_and_dedupes_views(db):
    with db.cursor() as cur:
        ids = seed(cur)
        template(cur, ids)
        rid = fixed.generate(cur, today=date(2026, 12, 1))[0][0]
        actor = person(cur, CHECKER)
        assert audit.record_view(cur, actor, rid)
        assert audit.record_view(cur, actor, rid) is None, 'повтор за 10 минут — та же строка'
        assert audit.record_view(cur, person(cur, ACCOUNTANT), rid)
        rows, total = audit.journal(cur, request_id=rid)
        sources = {row['source'] for row in rows}
        assert sources == {'audit', 'event'} and total == len(rows)
        views = [row for row in rows if row['action'] == 'view']
        assert len(views) == 2
        only_views, _total = audit.journal(cur, action='view')
        assert {row['source'] for row in only_views} == {'audit'}
        contracts, _total = audit.journal(cur, entity='contract')
        assert all(row['entity'] == 'contract' for row in contracts)
        first, total_all = audit.journal(cur, page=1, page_size=10)
        assert total_all >= len(first) and first == sorted(first, key=lambda row: (row['created_at'], row['id']), reverse=True)


def test_template_snapshot_has_the_new_fields(db):
    with db.cursor() as cur:
        ids = seed(cur)
        tid = template(cur, ids)
        snap = audit.snapshot(cur, 'template', tid)
        assert (snap['invoice_required'], snap['checker_user_id']) == (False, CHECKER)
        row = directory.read_template(cur, tid)
        assert row['checker_name'] == 'Проверкина Полина'
        # Импорт и старые формы поля не шлют — счёт нужен.
        legacy = directory.upsert_template(cur, fields={key: value for key, value in directory.read_template(cur, tid).items()
                                                        if key not in ('invoice_required', 'checker_user_id', 'id')},
                                           actor_id=None)
        assert directory.read_template(cur, legacy)['invoice_required'] is True
