# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: PAYMENTS_TEST_PORT — порт локального Postgres.

IT-закуп через Helpdesk IT (просьба владельца 04.11.2026). В tests/test_payments_helpdesk.py
запросы подменены и видно только, что они НАПИСАНЫ. Что они ЗНАЧАТ — какой маршрут строит
отправка IT-заявки, у кого она на столе и на доске, что пишут «Передать на утверждение» и
«Выдать из наличия», что остаётся пройденным после пересборки маршрута, — исполняется здесь,
на настоящей схеме раздела (payments.schema) и через тот же ход заявки (payments.flow), что
у ручек.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным
именем, в конце она удаляется. Запуск на локальном кластере (пароль, если он есть, —
через PGPASSWORD):

    PAYMENTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_payments_helpdesk_postgres.py

Люди выдуманы.
"""
import contextlib
import os
import uuid
from decimal import Decimal

import pytest

from payments import access, assets as assets_sql, directory, flow, queries, schema, workflow

PORT = os.environ.get('PAYMENTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (PAYMENTS_TEST_PORT)')

INITIATOR, MANAGER, HELPDESK, APPROVER, BIG_APPROVER, ACCOUNTANT, KEEPER = 10, 20, 30, 40, 41, 50, 60
PEOPLE = (INITIATOR, MANAGER, HELPDESK, APPROVER, BIG_APPROVER, ACCOUNTANT, KEEPER)

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL DEFAULT 'operator',
                        status text NOT NULL DEFAULT 'working', department_id integer, supervisor_id integer,
                        telegram_id bigint, job_title text, avatar_bucket text, avatar_blob_path text);
    INSERT INTO departments (id, code, name) VALUES (1, 'szov', 'СЗоВ'), (2, 'it', 'IT');
    INSERT INTO users (id, name, department_id, supervisor_id) VALUES
        (10, 'Инициаторова Ирина', 1, 20),
        (20, 'Руководов Рустам', 1, NULL),
        (30, 'Хелпов Хасан', 2, NULL),
        (40, 'Утвердова Ульяна', 1, NULL),
        (41, 'Большова Белла', 1, NULL),
        (50, 'Счётова Светлана', 1, NULL),
        (60, 'Складов Самат', 2, NULL);
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
    name = 't_payments_helpdesk_' + uuid.uuid4().hex
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
    monkeypatch.setattr(access, 'SECTION_ALLOWED_USER_IDS', PEOPLE)
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
    """Участники ролей, IT-категория, компания, поставщики и лимит «до 500 000 ₸ — Утвердова»."""
    # Helpdesk IT и учёт имущества — одна роль (06.11.2026): в ней и «Хелпов», и «Складов».
    for role, user_id in (('approver', APPROVER), ('approver', BIG_APPROVER), ('accounting', ACCOUNTANT),
                          ('asset_keeper', HELPDESK), ('asset_keeper', KEEPER)):
        queries.add_role_member(cur, role, user_id, None)
    it_category = directory.upsert_category(cur, name='Оргтехника', via_helpdesk=True)
    plain_category = directory.upsert_category(cur, name='Канцелярия')
    cur.execute("INSERT INTO payment_legal_entities (name) VALUES ('ТОО «Тест»') RETURNING id")
    entity = cur.fetchone()[0]
    suppliers = []
    for name in ('Sulpak', 'Technodom', 'Alser'):
        cur.execute("INSERT INTO payment_counterparties (name) VALUES (%s) RETURNING id", (name,))
        suppliers.append(cur.fetchone()[0])
    directory.upsert_limit(cur, fields={
        'number': '7', 'issued_on': '2026-09-01', 'starts_on': '2026-09-01', 'status': 'active',
        'replaces_role': 'founder', 'delegate_user_id': APPROVER, 'amount_limit': Decimal('500000'),
        'all_projects': True, 'all_counterparties': True}, project_ids=[], counterparty_ids=[])
    return {'it': it_category, 'plain': plain_category, 'entity': entity, 'suppliers': suppliers}


def it_request(cur, ids, *, category=None, prices=('0',)):
    """IT-закуп так, как его заводит форма: что и сколько, цены — если знает, поставщиков нет."""
    fields = {'request_kind': 'purchase', 'expense_name': 'Ноутбук для работы', 'payment_method': 'invoice',
              'object_type': 'goods', 'accounting_category': 'consumable', 'legal_entity_id': ids['entity'],
              'department_id': 1, 'category_id': category or ids['it'], 'justification': 'Старый не тянет 1С',
              'due_on': '2026-11-20', 'no_alternatives': False}
    items = [{'name': 'Ноутбук %s' % (index + 1), 'quantity': Decimal('1'), 'unit': 'шт', 'unit_price': Decimal(price)}
             for index, price in enumerate(prices)]
    return queries.create_request(cur, fields=fields, items=items, actor=person(cur, INITIATOR),
                                  initiator=person(cur, INITIATOR))


def state(cur, rid):
    return {item['kind']: item['state'] for item in queries.list_subtasks(cur, rid)}


def act(cur, rid, kind, action, who, **fields):
    return flow.act(cur, rid, kind, action, person(cur, who), fields=fields, outbox=[], base_url=None)


def offers_fields(ids, *amounts, recommended=0):
    offers = [{'id': None, 'counterparty_id': supplier, 'supplier_name': name, 'amount': Decimal(amount),
               'terms': None, 'link': None, 'comment': None, 'is_recommended': index == recommended}
              for index, (supplier, name, amount) in enumerate(zip(ids['suppliers'], ('Sulpak', 'Technodom', 'Alser'),
                                                                   amounts))]
    return {'offers': offers, 'counterparty_id': offers[recommended]['counterparty_id'],
            'supplier_choice_reason': 'Дешевле, доставка завтра', 'no_alternatives': False}


def board(cur, code, user_id, roles):
    scope = access.board_scope({'user_id': user_id}, code, roles)
    pairs = queries.board_requests(cur, code, own_user_id=user_id if scope == 'own' else None, own_roles=roles)
    return {row['id']: workflow.board_column(code, row, subtasks) for row, subtasks in pairs}


def test_it_purchase_goes_manager_helpdesk_approver(db):
    with db.cursor() as cur:
        ids = seed(cur)
        rid = it_request(cur, ids)
        # Цены нет, поставщиков нет — IT-закуп всё равно уходит: их принесёт Helpdesk IT.
        assert flow.submit_problems(cur, queries.read_request(cur, rid)) == []
        flow.submit(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)
        request = queries.read_request(cur, rid)
        assert request['it_purchase'] is True and request['category_helpdesk'] is True
        assert state(cur, rid) == {'initiation': 'done', 'manager_approval': 'open', 'it_review': 'pending',
                                   'approval': 'pending', 'invoice_payment': 'pending', 'receiving': 'pending',
                                   'closing_docs': 'pending'}
        act(cur, rid, workflow.KIND_MANAGER, 'approve', MANAGER)
        assert state(cur, rid)['it_review'] == 'open'
        # Задача — каждому из Helpdesk IT: на столе и на их доске, в «Новых».
        assert (rid, 'it_review') in queries.desk_pairs(cur, HELPDESK, {'asset_keeper'})
        assert board(cur, 'helpdesk', HELPDESK, {'asset_keeper'}) == {rid: 'new'}
        assert queries.read_request(cur, rid)['current_role_code'] == 'asset_keeper'
        flow.move(cur, rid, workflow.KIND_IT, 'in_progress', person(cur, HELPDESK))
        assert board(cur, 'helpdesk', HELPDESK, {'asset_keeper'}) == {rid: 'in_progress'}

        fields = dict(offers_fields(ids, '450000', '470000', '455000'), comment='Модель — ThinkPad E14',
                      items=[{'name': 'Ноутбук Lenovo ThinkPad E14', 'quantity': Decimal('1'), 'unit': 'шт',
                              'unit_price': Decimal('450000')}])
        result = act(cur, rid, workflow.KIND_IT, 'it_offers', HELPDESK, **fields)
        assert len(result['offer_ids']) == 3
        request = queries.read_request(cur, rid)
        assert request['amount'] == Decimal('450000.00'), 'сумма — из цен Helpdesk IT'
        assert request['issued_from_stock'] is False
        assert request['counterparty_name'] == 'Sulpak'
        assert request['supplier_choice_reason'] == 'Дешевле, доставка завтра'
        assert [item['name'] for item in queries.list_items(cur, rid)] == ['Ноутбук Lenovo ThinkPad E14']
        assert [(offer['supplier_name'], offer['is_recommended']) for offer in queries.list_offers(cur, rid)] == [
            ('Sulpak', True), ('Technodom', False), ('Alser', False)]
        # Пересборка: руководитель и Helpdesk пройдены, утверждение — по лимиту под настоящую сумму.
        steps = {item['kind']: item for item in queries.list_subtasks(cur, rid)}
        assert (steps['manager_approval']['state'], steps['manager_approval']['done_by']) == ('done', MANAGER)
        assert (steps['it_review']['state'], steps['it_review']['outcome'], steps['it_review']['done_by']) == (
            'done', 'offers', HELPDESK)
        assert steps['it_review']['comment'] == 'Модель — ThinkPad E14'
        assert (steps['approval']['state'], steps['approval']['assignee_id']) == ('open', APPROVER)
        assert request['route_basis']['source'] == 'limit'
        assert request['submitted_snapshot']['amount'] == '450000.00'
        assert board(cur, 'helpdesk', HELPDESK, {'asset_keeper'}) == {rid: 'offers'}
        assert board(cur, 'approval', APPROVER, {'approver'}) == {rid: 'new'}
        events = [event['kind'] for event in queries.list_events(cur, rid)]
        assert events[-1] == 'it_offers'
        cur.execute("SELECT kind FROM payment_notifications WHERE user_id = %s AND request_id = %s ORDER BY id",
                    (INITIATOR, rid))
        assert 'it_offers' in [row[0] for row in cur.fetchall()], 'инициатор узнаёт, у кого предлагают купить'


def bare_it_request(cur, ids):
    """IT-закуп так, как его заводит форма с 06.11.2026: «мне нужна мышка» — что, сколько, срок и
    пожелания; ни обоснования, ни товара или услуги, ни категории учёта, ни способа оплаты."""
    fields = {'request_kind': 'purchase', 'expense_name': 'Мышка', 'department_id': 1, 'category_id': ids['it'],
              'justification': None, 'due_on': '2026-11-20', 'no_alternatives': False}
    items = [{'name': 'Мышка', 'quantity': Decimal('1'), 'unit': 'шт', 'unit_price': Decimal('0')}]
    return queries.create_request(cur, fields=fields, items=items, actor=person(cur, INITIATOR),
                                  initiator=person(cur, INITIATOR))


def test_bare_it_request_gets_its_payment_from_helpdesk(db, monkeypatch):
    """Инициатор: «мне нужна мышка». Helpdesk IT: поставщик, цена, товар, имущество, карта сотрудника —
    маршрут встаёт по его выбору: пополнение карты с чеком и постановка на учёт (владелец 06.11.2026)."""
    monkeypatch.setenv('JWT_SECRET', 'test-secret')
    monkeypatch.delenv('PAYMENTS_CARD_KEY', raising=False)
    with db.cursor() as cur:
        ids = seed(cur)
        queries.add_role_member(cur, 'finance', ACCOUNTANT, None)
        rid = bare_it_request(cur, ids)
        assert flow.submit_problems(cur, queries.read_request(cur, rid)) == []
        flow.submit(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)
        request = queries.read_request(cur, rid)
        assert (request['payment_method'], request['object_type'], request['legal_entity_id']) == (None, None, None)
        # Пока способа оплаты нет — после утверждения стоит оплата счёта; Helpdesk её поменяет.
        assert list(state(cur, rid)) == ['initiation', 'manager_approval', 'it_review', 'entity_clarify', 'approval',
                                         'invoice_payment', 'receiving', 'closing_docs']
        act(cur, rid, workflow.KIND_MANAGER, 'approve', MANAGER)
        # Без товара, учёта и способа оплаты Helpdesk этап не закроет.
        bare_offer = dict(offers_fields(ids, '9000', '9500', '9900'),
                          items=[{'name': 'Мышка Logitech', 'quantity': Decimal('1'), 'unit': 'шт', 'unit_price': Decimal('9000')}])
        with pytest.raises(flow.FlowError) as caught:
            act(cur, rid, workflow.KIND_IT, 'it_offers', HELPDESK, **bare_offer)
        assert caught.value.missing == ['Выберите тип объекта: товар или услуга', workflow.PAYMENT_METHOD_MISSING]
        payment = {'object_type': 'goods', 'accounting_category': 'asset', 'payment_method': 'card',
                   'card_recipient': 'employee', 'card_holder_user_id': INITIATOR, 'card_holder_name': 'Инициаторова Ирина',
                   'payment_purpose': 'Мышка для работы'}
        act(cur, rid, workflow.KIND_IT, 'it_offers', HELPDESK, payment=payment, card={'number': '4111111111111111'},
            card_number_ok=True, **bare_offer)
        request = queries.read_request(cur, rid)
        assert (request['object_type'], request['accounting_category'], request['payment_method'],
                request['card_recipient'], request['card_holder_user_id']) == ('goods', 'asset', 'card', 'employee', INITIATOR)
        assert (request['has_card'], request['card_last4']) == (True, '1111')
        assert queries.card_number(cur, rid) == '4111111111111111', 'номер — зашифрованным, как из формы'
        route = list(state(cur, rid))
        assert 'invoice_payment' not in route
        assert route[route.index('approval'):] == ['approval', 'card_topup', 'receipt_confirm', 'receiving',
                                                   'asset_registration', 'closing_docs']
        steps = {item['kind']: item for item in queries.list_subtasks(cur, rid)}
        assert steps['asset_registration']['role_code'] == 'asset_keeper', 'на учёт ставит та же роль'
        assert (steps['it_review']['state'], steps['entity_clarify']['state']) == ('done', 'open')
        # Снимок условий — уже с выбором Helpdesk: ответ инициатора на будущий возврат его не перестроит.
        snapshot = request['submitted_snapshot']
        assert (snapshot['payment_method'], snapshot['object_type'], snapshot['card_last4']) == ('card', 'goods', '1111')
        assert not workflow.essentials_changed(snapshot, request)


def test_helpdesk_role_rows_move_to_the_asset_keeper(db):
    """Миграция 06.11.2026: участники прежней роли «Helpdesk IT» — в «Helpdesk IT и учёт имущества»,
    без дублей; этапы и заявки — тоже. Второй запуск ничего не меняет."""
    with db.cursor() as cur:
        ids = seed(cur)
        cur.execute("DELETE FROM payment_role_members WHERE role_code = 'asset_keeper' AND user_id = %s", (HELPDESK,))
        cur.execute("INSERT INTO payment_role_members (role_code, user_id) VALUES ('helpdesk', %s), ('helpdesk', %s)",
                    (HELPDESK, KEEPER))
        rid = it_request(cur, ids)
        flow.submit(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)
        act(cur, rid, workflow.KIND_MANAGER, 'approve', MANAGER)
        cur.execute("UPDATE payment_subtasks SET role_code = 'helpdesk' WHERE request_id = %s AND kind = 'it_review'", (rid,))
        cur.execute("UPDATE payment_requests SET current_role_code = 'helpdesk' WHERE id = %s", (rid,))
        for _run in range(2):
            for sql in schema.statements():
                cur.execute(sql)
            cur.execute("SELECT role_code, user_id FROM payment_role_members WHERE role_code IN ('helpdesk', 'asset_keeper') "
                        "ORDER BY role_code, user_id")
            assert cur.fetchall() == [('asset_keeper', HELPDESK), ('asset_keeper', KEEPER)]
            assert queries.read_subtask(cur, rid, workflow.KIND_IT)['role_code'] == 'asset_keeper'
            assert queries.read_request(cur, rid)['current_role_code'] == 'asset_keeper'
        # После переноса этап — у роли: «Хелпов» видит его на столе и на доске.
        assert (rid, 'it_review') in queries.desk_pairs(cur, HELPDESK, {'asset_keeper'})


def test_company_clarified_after_helpdesk_keeps_its_check(db):
    """Без компании: руководитель → Helpdesk IT → бухгалтерия указывает компанию. Пересборка маршрута
    при выборе компании проверку Helpdesk IT не сбрасывает — она остаётся пройденной, с вариантами."""
    with db.cursor() as cur:
        ids = seed(cur)
        rid = it_request(cur, ids)
        queries.update_request_fields(cur, rid, {'legal_entity_id': None})
        flow.submit(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)
        assert list(state(cur, rid))[:5] == ['initiation', 'manager_approval', 'it_review', 'entity_clarify', 'approval']
        act(cur, rid, workflow.KIND_MANAGER, 'approve', MANAGER)
        act(cur, rid, workflow.KIND_IT, 'it_offers', HELPDESK,
            items=[{'name': 'Ноутбук', 'quantity': Decimal('1'), 'unit': 'шт', 'unit_price': Decimal('450000')}],
            **offers_fields(ids, '450000', '470000', '455000'))
        assert state(cur, rid)['entity_clarify'] == 'open'
        act(cur, rid, workflow.KIND_ENTITY, 'set_entity', ACCOUNTANT, legal_entity_id=ids['entity'])
        steps = {item['kind']: item for item in queries.list_subtasks(cur, rid)}
        assert (steps['it_review']['state'], steps['it_review']['outcome'], steps['it_review']['done_by']) == (
            'done', 'offers', HELPDESK)
        assert (steps['manager_approval']['state'], steps['entity_clarify']['state']) == ('done', 'done')
        assert (steps['approval']['state'], steps['approval']['assignee_id']) == ('open', APPROVER)
        assert len(queries.list_offers(cur, rid)) == 3


def test_helpdesk_role_emptied_after_the_check_blocks_nothing(db):
    """Роль «Helpdesk IT» опустела, когда проверка уже пройдена (защита «последнего участника»
    пройденный этап не считает): бухгалтерия всё равно указывает компанию — пересборка маршрута
    пройденный этап не держит (разбор 04.11.2026)."""
    with db.cursor() as cur:
        ids = seed(cur)
        rid = it_request(cur, ids)
        queries.update_request_fields(cur, rid, {'legal_entity_id': None})
        flow.submit(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)
        act(cur, rid, workflow.KIND_MANAGER, 'approve', MANAGER)
        act(cur, rid, workflow.KIND_IT, 'it_offers', HELPDESK,
            items=[{'name': 'Ноутбук', 'quantity': Decimal('1'), 'unit': 'шт', 'unit_price': Decimal('450000')}],
            **offers_fields(ids, '450000', '470000', '455000'))
        assert queries.role_backlog(cur, 'asset_keeper') == 0, 'по пройденному этапу роль убрать можно'
        queries.remove_role_member(cur, 'asset_keeper', HELPDESK)
        queries.remove_role_member(cur, 'asset_keeper', KEEPER)
        act(cur, rid, workflow.KIND_ENTITY, 'set_entity', ACCOUNTANT, legal_entity_id=ids['entity'])
        assert state(cur, rid)['approval'] == 'open'


def test_step_given_to_someone_outside_the_emptied_role(db):
    """Роль опустела, администратор лично передал открытую проверку сотруднику не из роли —
    тот передаёт заявку на утверждение."""
    with db.cursor() as cur:
        ids = seed(cur)
        rid = it_request(cur, ids)
        flow.submit(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)
        act(cur, rid, workflow.KIND_MANAGER, 'approve', MANAGER)
        queries.set_subtask_assignee(cur, rid, workflow.KIND_IT, person(cur, ACCOUNTANT), person(cur, ACCOUNTANT))
        queries.remove_role_member(cur, 'asset_keeper', HELPDESK)
        queries.remove_role_member(cur, 'asset_keeper', KEEPER)
        act(cur, rid, workflow.KIND_IT, 'it_offers', ACCOUNTANT,
            items=[{'name': 'Ноутбук', 'quantity': Decimal('1'), 'unit': 'шт', 'unit_price': Decimal('450000')}],
            **offers_fields(ids, '450000', '470000', '455000'))
        steps = {item['kind']: item for item in queries.list_subtasks(cur, rid)}
        assert (steps['it_review']['state'], steps['it_review']['done_by']) == ('done', ACCOUNTANT)
        assert steps['approval']['state'] == 'open'


def test_route_without_manager_still_starts_with_the_manager(db):
    """Маршрут «до 100 000 ₸ — без руководителя»: при отправке у IT-закупа цены нет (0 ₸), но
    руководитель всё равно первый — иначе он появился бы после Helpdesk IT."""
    with db.cursor() as cur:
        ids = seed(cur)
        cur.execute("INSERT INTO payment_approval_routes (name, position, manager_step, amount_to, is_active) "
                    "VALUES ('Мелкое — без руководителя', 1, 'skip', 100000, TRUE)")
        rid = it_request(cur, ids)
        flow.submit(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)
        assert list(state(cur, rid))[:3] == ['initiation', 'manager_approval', 'it_review']
        assert state(cur, rid)['manager_approval'] == 'open'


def test_price_over_the_limit_goes_to_the_whole_role(db):
    """Сумма Helpdesk IT выше лимита — утверждает любой из утверждающих, а не тот, кого назвал
    бы лимит по нулевой сумме при отправке."""
    with db.cursor() as cur:
        ids = seed(cur)
        rid = it_request(cur, ids)
        flow.submit(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)
        assert queries.read_subtask(cur, rid, workflow.KIND_APPROVAL)['assignee_id'] == APPROVER, \
            'при отправке сумма 0 — лимит «до 500 000 ₸» подошёл'
        act(cur, rid, workflow.KIND_MANAGER, 'approve', MANAGER)
        act(cur, rid, workflow.KIND_IT, 'it_offers', HELPDESK,
            items=[{'name': 'Сервер', 'quantity': Decimal('1'), 'unit': 'шт', 'unit_price': Decimal('900000')}],
            **offers_fields(ids, '900000', '950000', '990000'))
        assert queries.read_subtask(cur, rid, workflow.KIND_APPROVAL)['assignee_id'] is None
        assert queries.read_request(cur, rid)['route_basis']['source'] == 'role'


def test_issued_from_stock_closes_without_purchase(db):
    with db.cursor() as cur:
        ids = seed(cur)
        rid = it_request(cur, ids)
        flow.submit(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)
        act(cur, rid, workflow.KIND_MANAGER, 'approve', MANAGER)
        issued = [{'id': 1, 'name': 'Ноутбук HP ProBook', 'inventory_number': 'IT-0042'}]
        assert act(cur, rid, workflow.KIND_IT, 'it_stock', HELPDESK, stock_assets=issued,
                   comment='Забрать в 305 кабинете') == {'next': None}
        request = queries.read_request(cur, rid)
        assert (request['status'], request['stage'], request['paid_on']) == ('done', 'closed', None)
        # Строка реестра и выгрузки знает, что это выдача, а не закуп.
        assert request['issued_from_stock'] is True
        _total, rows = queries.list_requests(cur, limit=10, state='done')
        assert [(row['id'], row['issued_from_stock']) for row in rows] == [(rid, True)]
        steps = {item['kind']: item for item in queries.list_subtasks(cur, rid)}
        assert (steps['it_review']['state'], steps['it_review']['outcome']) == ('done', 'from_stock')
        for kind in ('approval', 'invoice_payment', 'receiving', 'closing_docs'):
            assert (steps[kind]['state'], steps[kind]['comment']) == ('skipped', 'Не потребовалось: выдано из наличия')
        assert workflow.closing_conditions(request, list(steps.values())) == [{'label': 'Выдано из наличия', 'ok': True}]
        event = next(item for item in queries.list_events(cur, rid) if item['kind'] == 'it_stock')
        assert event['payload']['assets'] == issued
        assert board(cur, 'helpdesk', HELPDESK, {'asset_keeper'}) == {rid: 'stock'}
        assert queries.desk_pairs(cur, HELPDESK, {'asset_keeper'}) == []
        cur.execute("SELECT count(*) FROM payment_notifications WHERE request_id = %s AND actionable AND read_at IS NULL",
                    (rid,))
        assert cur.fetchone()[0] == 0, 'по закрытой заявке ни от кого ничего не ждут'


def test_return_from_helpdesk_comes_back_to_helpdesk(db):
    with db.cursor() as cur:
        ids = seed(cur)
        rid = it_request(cur, ids)
        flow.submit(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)
        act(cur, rid, workflow.KIND_MANAGER, 'approve', MANAGER)
        act(cur, rid, workflow.KIND_IT, 'return', HELPDESK, comment='Какой нужен объём SSD?')
        assert (state(cur, rid)['it_review'], state(cur, rid)['initiation']) == ('waiting', 'open')
        assert board(cur, 'helpdesk', HELPDESK, {'asset_keeper'}) == {rid: 'clarification'}
        # Ответ без смены условий — снова к Helpdesk IT, руководитель второй раз не согласует.
        result = flow.submit(cur, rid, person(cur, INITIATOR), comment='512 ГБ', outbox=[], base_url=None)
        assert result == {'rerouted': False, 'next': 'it_review', 'changed': []}
        assert (state(cur, rid)['it_review'], state(cur, rid)['manager_approval']) == ('open', 'done')


def test_category_changed_on_rework_rebuilds_the_route_without_helpdesk(db):
    with db.cursor() as cur:
        ids = seed(cur)
        rid = it_request(cur, ids)
        flow.submit(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)
        act(cur, rid, workflow.KIND_MANAGER, 'return', MANAGER, comment='Это не техника, а канцелярия')
        queries.update_request_fields(cur, rid, {'category_id': ids['plain']})
        # Обычная категория — поставщиков и цену теперь указывает инициатор, как у любого закупа.
        problems = flow.submit_problems(cur, queries.read_request(cur, rid))
        assert 'Сумма заявки должна быть больше нуля' in problems
        queries.replace_items(cur, rid, [{'name': 'Бумага', 'quantity': Decimal('1'), 'unit': 'шт',
                                          'unit_price': Decimal('9000')}])
        offers = offers_fields(ids, '9000', '9500', '9900')
        queries.save_offers(cur, rid, offers['offers'])
        queries.update_request_fields(cur, rid, {'counterparty_id': offers['counterparty_id'],
                                                 'supplier_choice_reason': 'Дешевле'})
        result = flow.submit(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)
        assert result['rerouted'] is True
        assert queries.read_request(cur, rid)['it_purchase'] is False
        assert state(cur, rid)['it_review'] == 'skipped'
        assert state(cur, rid)['manager_approval'] == 'open'


def test_category_flag_lives_in_the_directory(db):
    with db.cursor() as cur:
        ids = seed(cur)
        flags = {item['id']: item['via_helpdesk'] for item in directory.list_categories(cur)}
        assert (flags[ids['it']], flags[ids['plain']]) == (True, False)
        # Правка без отметки (форма прежней сборки во время выкладки) отметку не снимает.
        directory.upsert_category(cur, category_id=ids['it'], name='Оргтехника и ПО')
        assert directory.category_helpdesk(cur, ids['it']) is True
        directory.upsert_category(cur, category_id=ids['it'], name='Оргтехника и ПО', via_helpdesk=False)
        assert directory.category_helpdesk(cur, ids['it']) is False
        # У подкатегории отметки не бывает: IT-закуп узнаётся по категории верхнего уровня.
        child = directory.upsert_category(cur, name='Ноутбуки', parent_id=ids['plain'], via_helpdesk=True)
        assert directory.category_helpdesk(cur, child) is False
        # Отмеченную категорию перенесли под родителя — отметка снимается, даже если о ней не сказали.
        directory.upsert_category(cur, category_id=ids['it'], name='Оргтехника и ПО', via_helpdesk=True)
        directory.upsert_category(cur, category_id=ids['it'], name='Оргтехника и ПО', parent_id=ids['plain'])
        assert directory.category_helpdesk(cur, ids['it']) is False
        assert directory.category_helpdesk(cur, None) is False


def test_stock_and_employees_assets(db):
    with db.cursor() as cur:
        ids = seed(cur)
        for name, inventory, status, holder in (('Ноутбук HP ProBook', 'IT-0042', 'in_stock', None),
                                                 ('Монитор Dell', 'IT-0007', 'in_use', INITIATOR),
                                                 ('Старый ноутбук', 'IT-0001', 'written_off', INITIATOR)):
            assets_sql.create_asset(cur, request_id=None, actor=person(cur, KEEPER), fields={
                'name': name, 'inventory_number': inventory, 'status': status, 'city': 'Алматы', 'location': 'Склад',
                'responsible_user_id': holder, 'legal_entity_id': ids['entity']})
        total, rows = assets_sql.list_assets(cur, limit=30, status='in_stock', query='ноут')
        assert (total, [row['name'] for row in rows]) == (1, ['Ноутбук HP ProBook'])
        assert [row['name'] for row in assets_sql.of_user(cur, INITIATOR)] == ['Монитор Dell'], \
            'списанное за сотрудником уже не числится'
        laptop = rows[0]['id']
        rid = it_request(cur, ids)
        flow.submit(cur, rid, person(cur, INITIATOR), outbox=[], base_url=None)
        act(cur, rid, workflow.KIND_MANAGER, 'approve', MANAGER)
        assert assets_sql.issued_from_stock(cur, [laptop]) == {}
    # Каждый шаг — своей транзакцией, как у людей: время записи у них разное.
    with db.cursor() as cur:
        act(cur, rid, workflow.KIND_IT, 'it_stock', HELPDESK,
            stock_asset_ids=None, stock_assets=[{'id': laptop, 'name': 'Ноутбук HP ProBook', 'inventory_number': 'IT-0042'}])
        assert assets_sql.read_asset(cur, laptop)['status'] == 'in_stock', 'выдача карточку не трогает (R2)'
        assert assets_sql.issued_from_stock(cur, [laptop])[laptop]['request_id'] == rid
    # Правка описания и перенос места внутри склада вещь не выдают — отметка остаётся.
    with db.cursor() as cur:
        assets_sql.update_details(cur, laptop, {'serial_number': 'SN-NEW'})
        assets_sql.move(cur, laptop, actor=person(cur, KEEPER), fields={'location': 'Склад IT, полка 2'})
    with db.cursor() as cur:
        assert assets_sql.issued_from_stock(cur, [laptop])[laptop]['request_id'] == rid, 'вещь всё ещё не передана'
    # Передали по акту (новый ответственный, «В эксплуатации») — отметка гаснет.
    with db.cursor() as cur:
        assets_sql.move(cur, laptop, actor=person(cur, KEEPER), fields={
            'responsible_user_id': INITIATOR, 'responsible_name': 'Инициаторова Ирина', 'status': 'in_use',
            'location': 'Офис СЗоВ'})
    with db.cursor() as cur:
        assert assets_sql.issued_from_stock(cur, [laptop]) == {}
        assert assets_sql.issued_from_stock(cur, []) == {}


def test_schema_is_idempotent(db):
    with db.cursor() as cur:
        for sql in schema.statements():
            cur.execute(sql)
        cur.execute("SELECT table_name, column_name, is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_schema = %s AND column_name IN ('via_helpdesk', 'it_purchase') ORDER BY table_name",
                    (db.name,))
        assert cur.fetchall() == [('payment_categories', 'via_helpdesk', 'NO', 'false'),
                                  ('payment_requests', 'it_purchase', 'NO', 'false')]
