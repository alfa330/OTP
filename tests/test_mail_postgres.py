# -*- coding: utf-8 -*-
"""«Почта» (#417) на настоящем Postgres: MAIL_TEST_PORT — порт локального кластера.

Чистые правила (статусы, сроки, проверка полей, права) проверяет tests/test_mail_rules.py.
Здесь — весь серверный путь через настоящий Blueprint: «Участники», создание и правка,
шаги статусов с файлами, сверка, просрочка, выгрузка, справочники — и то, что видимость
в SQL совпадает с access.can_view.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным
именем, в конце она удаляется. Люди и офисы выдуманы.

    MAIL_TEST_PORT=55433 PGPASSWORD=verify python -m pytest tests/test_mail_postgres.py
"""
import contextlib
import json
import os
import uuid
from datetime import date, datetime, timedelta
from io import BytesIO

import pytest

from mail import access, queries, rules, schema

PORT = os.environ.get('MAIL_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (MAIL_TEST_PORT)')


@pytest.fixture(autouse=True)
def section_open_to_participants(monkeypatch):
    """Здесь проверяются правила раздела для участников. Пилот «пока только суперадминам»
    (access.PILOT_SUPER_ADMIN_ONLY) — отдельным тестом ниже."""
    monkeypatch.setattr(access, 'PILOT_SUPER_ADMIN_ONLY', False)

SUPER, ADMIN, CLERK_A, CLERK_P, CLERK_P2, COORD, REGIONAL, ACCOUNTING, CENTRAL, OUTSIDER, FIRED = (
    1, 2, 10, 11, 12, 20, 30, 40, 50, 60, 70)
OFFICE_A, OFFICE_A2, OFFICE_P, OFFICE_S, OFFICE_OLD = 101, 102, 103, 104, 105
PAYER = 7

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text UNIQUE, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, role text NOT NULL,
                        status text NOT NULL DEFAULT 'working', department_id integer, phone varchar(50),
                        city varchar(255));
    CREATE TABLE wiki_offices (id integer PRIMARY KEY, name text NOT NULL, city text, address text,
                               phone text, status text NOT NULL DEFAULT 'active', kind text NOT NULL DEFAULT 'park',
                               no_office boolean NOT NULL DEFAULT FALSE, is_online boolean NOT NULL DEFAULT FALSE,
                               space_id integer, position integer NOT NULL DEFAULT 0);
    CREATE TABLE payment_legal_entities (id integer PRIMARY KEY, name text NOT NULL,
                                         is_active boolean NOT NULL DEFAULT TRUE);
    INSERT INTO departments (id, code, name) VALUES (1, 'front_office', 'Фронт офисы'), (2, 'accounting', 'Бухгалтерия');
    INSERT INTO users (id, name, login, role, department_id, phone, city, status) VALUES
        (1, 'Суперадминов Сергей', 'super', 'super_admin', NULL, NULL, NULL, 'working'),
        (2, 'Админова Алия', 'admin', 'admin', 1, '+77010000002', 'Алматы', 'working'),
        (10, 'Алматинцева Айгуль', 'clerk_a', 'operator', 1, '+77010000010', 'Алматы', 'working'),
        (11, 'Павлодарова Динара', 'clerk_p', 'operator', 1, '+77010000011', 'Павлодар', 'working'),
        (12, 'Павлодаров Ерлан', 'clerk_p2', 'operator', 1, '+77010000012', 'Павлодар', 'working'),
        (20, 'Координаторов Канат', 'coord', 'admin', 1, '+77010000020', 'Алматы', 'working'),
        (30, 'Регионова Роза', 'regional', 'sv', 1, NULL, 'Павлодар', 'working'),
        (40, 'Бухгалтерова Бота', 'buh', 'accounting_manager', 2, NULL, 'Алматы', 'working'),
        (50, 'Центров Цезарь', 'central', 'admin', 1, NULL, 'Алматы', 'working'),
        (60, 'Посторонний Петр', 'outsider', 'operator', 1, NULL, NULL, 'working'),
        (70, 'Уволенный Ульян', 'fired', 'operator', 1, NULL, NULL, 'fired');
    INSERT INTO wiki_offices (id, name, city, address, phone, status, space_id, position) VALUES
        (101, 'Офис Алматы №1', 'Алматы', 'ул. Абая, 1', '+77270000001', 'active', 1, 1),
        (102, 'Офис Алматы №2', 'Алматы', 'ул. Сатпаева, 2', NULL, 'active', 1, 2),
        (103, 'Офис Павлодар', 'Павлодар', 'ул. Ленина, 3', NULL, 'active', 1, 1),
        (104, 'Офис Шымкент', 'Шымкент', 'пр. Тауке хана, 4', NULL, 'active', 1, 1),
        (105, 'Старый офис', 'Костанай', 'ул. Старая, 5', NULL, 'archived', 1, 1);
    INSERT INTO wiki_offices (id, name, city, status, is_online, space_id) VALUES
        (106, 'ОНЛАЙН · все города', 'Все города', 'active', TRUE, 1);
    INSERT INTO payment_legal_entities (id, name, is_active) VALUES (7, 'ТОО «Stock Company»', TRUE),
                                                                    (8, 'ТОО «Закрытое»', FALSE);
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
            cur.execute("SET statement_timeout TO '20s'")
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


class _Blob:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def upload_from_string(self, data, content_type=None):
        self.store[self.path] = (data, content_type)

    def exists(self):
        return self.path in self.store

    def download_as_bytes(self):
        return self.store[self.path][0]

    def delete(self):
        self.store.pop(self.path, None)


class _Client:
    def __init__(self, store):
        self.store = store

    def bucket(self, _name):
        store = self.store

        class _Bucket:
            def blob(self, path):
                return _Blob(store, path)
        return _Bucket()


@pytest.fixture
def db(monkeypatch):
    name = 't_mail_' + uuid.uuid4().hex
    base = _Db(name)
    admin = base.connect()
    try:
        with admin.cursor() as cur:
            cur.execute('CREATE SCHEMA ' + name)
            cur.execute('SET search_path TO ' + name)
            cur.execute(BASE_DDL)
            schema.ensure_in_cursor(cur)
        admin.commit()
        monkeypatch.setattr(queries, 'section_space_ids', lambda cursor: [1])
        yield base
    finally:
        admin.rollback()
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + name + ' CASCADE')
        admin.commit()
        admin.close()


class Api:
    def __init__(self, db):
        from flask import Flask, jsonify

        from mail.routes import build_mail_blueprint

        self.user = SUPER
        self.blobs = {}
        app = Flask(__name__)
        app.register_blueprint(build_mail_blueprint(
            db=db,
            require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: (jsonify({}), 200),
            resolve_requester=lambda: (self.user, None, None),
            excel_text_warning=None,
            gcs={'bucket_name': lambda: 'mail-test', 'client': lambda: _Client(self.blobs)},
        ))
        self.client = app.test_client()

    def as_(self, user):
        self.user = user
        return self

    def get(self, path, **params):
        return self.client.get('/api/mail' + path, query_string=params)

    def post(self, path, body=None, files=None):
        if files:
            data = {'payload': json.dumps(body or {}, ensure_ascii=False, default=str),
                    'files': [(BytesIO(content), filename) for filename, content in files]}
            return self.client.post('/api/mail' + path, data=data, content_type='multipart/form-data')
        return self.client.post('/api/mail' + path, json=body or {})

    def put(self, path, body):
        return self.client.put('/api/mail' + path, json=body)

    def patch(self, path, body):
        return self.client.patch('/api/mail' + path, json=body)

    def delete(self, path):
        return self.client.delete('/api/mail' + path)


def seed_role(db, user_id, role, offices):
    """Роль, которую вкладка не выдаёт (администратор — заводится схемой, координатор, региональный,
    центр, бухгалтерия — заложены на будущее): строкой в базе напрямую, как её завели бы вручную."""
    with db._get_cursor() as cur:
        cur.execute("INSERT INTO mail_participants (user_id, role) VALUES (%s, %s)", (user_id, role))
        for office_id in offices:
            cur.execute("INSERT INTO mail_participant_offices (user_id, office_id) VALUES (%s, %s)", (user_id, office_id))


@pytest.fixture
def api(db):
    client = Api(db)
    for user_id, role, offices in ((ADMIN, 'admin', []), (COORD, 'coordinator', [OFFICE_A, OFFICE_P]),
                                   (REGIONAL, 'regional', [OFFICE_P]), (ACCOUNTING, 'accounting', []),
                                   (CENTRAL, 'central', [])):
        seed_role(db, user_id, role, offices)
    # Сотрудников офисов заводит администратор раздела во вкладке «Участники».
    client.as_(ADMIN)
    for user_id, office in ((CLERK_A, OFFICE_A), (CLERK_P, OFFICE_P), (CLERK_P2, OFFICE_P)):
        response = client.put('/participants/%d' % user_id, {'office_ids': [office]})
        assert response.status_code == 200, response.get_json()
    return client


def shipment_body(**overrides):
    body = {
        'sender_office_id': OFFICE_A, 'recipient_office_id': OFFICE_P,
        'sender_name': 'Алматинцева Айгуль', 'sender_phone': '+77010000010',
        'recipient_name': 'Павлодарова Динара', 'recipient_phone': '+7 701 000-00-11',
        'recipient_address': 'ул. Ленина, 3', 'recipient_postcode': '140000',
        'payer_entity_id': PAYER, 'weight_kg': '0,4', 'places': 1,
        'items': [
            {'doc_type': 'avr', 'qty': 5, 'period_from': '2026-09-01', 'period_to': '2026-09-30'},
            {'doc_type': 'avr_return', 'qty': 3, 'copy_kind': 'copy'},
        ],
    }
    body.update(overrides)
    return body


def create(api, user=CLERK_A, submit=True, **overrides):
    response = api.as_(user).post('/shipments', dict(shipment_body(**overrides), submit=submit))
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def hand_over(api, shipment_id, user=CLERK_A, **body):
    payload = {'handed_at': queries.now_almaty().replace(microsecond=0).isoformat(), 'track_number': 'MBE-1'}
    payload.update(body)
    return api.as_(user).post('/shipments/%d/actions/hand_over' % shipment_id, payload,
                              files=[('накладная.pdf', b'%PDF-1.4 test')])


def registry_numbers(api, user, **params):
    response = api.as_(user).get('/shipments', scope=params.pop('scope', 'registry'), **params)
    assert response.status_code == 200, response.get_json()
    return {item['number'] for item in response.get_json()['items']}


# ─── Схема и «Участники» ─────────────────────────────────────────────────────

def test_schema_is_idempotent_and_seeds_zones_once(db):
    with db._get_cursor() as cur:
        schema.ensure_in_cursor(cur)
        cur.execute("UPDATE mail_zones SET max_days = 4 WHERE code = '1'")
        schema.ensure_in_cursor(cur)
        cur.execute("SELECT code, min_days, max_days FROM mail_zones ORDER BY position")
        assert cur.fetchall() == [('2', 1, 1), ('1', 2, 4), ('3', 7, 15)]
        # Постановщика (532) в этой базе нет — строку не заводим, но и повторно не пытаемся.
        cur.execute("SELECT COUNT(*) FROM mail_participants")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT 1 FROM mail_settings WHERE key = 'seeded_admins_v1'")
        assert cur.fetchone()


def test_only_participants_and_super_admin_open_the_section(api, db):
    assert api.as_(OUTSIDER).get('/context').status_code == 403
    assert api.as_(FIRED).get('/context').status_code == 403
    context = api.as_(SUPER).get('/context').get_json()
    assert context['capabilities']['is_admin'] and context['capabilities']['tabs'] == [
        'overview', 'registry', 'problems', 'participants', 'settings']
    clerk = api.as_(CLERK_A).get('/context').get_json()
    assert clerk['capabilities']['tabs'] == ['overview', 'outgoing', 'incoming', 'registry', 'problems']
    assert clerk['sender_office_ids'] == [OFFICE_A] and clerk['default_sender_office_id'] == OFFICE_A
    assert [o['id'] for o in clerk['offices']] == [OFFICE_A, OFFICE_A2, OFFICE_P, OFFICE_S]
    assert clerk['legal_entities'] == [{'id': PAYER, 'name': 'ТОО «Stock Company»'}]
    with db._get_cursor() as cur:
        assert queries.section_open_for(cur, CLERK_A, 'operator')
        assert queries.section_open_for(cur, SUPER, 'super_admin')
        assert not queries.section_open_for(cur, OUTSIDER, 'operator')


def test_participants_are_office_employees_managed_by_section_admin(api):
    assert api.as_(CLERK_A).get('/participants').status_code == 403
    assert api.as_(COORD).put('/participants/%d' % OUTSIDER, {'office_ids': [OFFICE_S]}).status_code == 403
    # Сотруднику офиса — ровно один офис, архивный не добавить, уволенного — нельзя.
    bad = api.as_(ADMIN).put('/participants/%d' % OUTSIDER, {'office_ids': [OFFICE_A, OFFICE_P]})
    assert bad.status_code == 400 and 'office_ids' in bad.get_json()['fields']
    assert api.as_(ADMIN).put('/participants/%d' % OUTSIDER, {'office_ids': []}).status_code == 400
    assert api.as_(ADMIN).put('/participants/%d' % OUTSIDER, {'office_ids': [OFFICE_OLD]}).status_code == 400
    assert api.as_(ADMIN).put('/participants/%d' % FIRED, {'office_ids': [OFFICE_S]}).status_code == 404
    # Роль из запроса не читается: администратором вкладкой не сделать.
    ok = api.as_(ADMIN).put('/participants/%d' % OUTSIDER, {'role': 'admin', 'office_ids': [OFFICE_S]})
    assert ok.status_code == 200
    row = next(p for p in ok.get_json()['participants'] if p['user_id'] == OUTSIDER)
    assert row['role'] == 'office' and row['office_ids'] == [OFFICE_S] and row['changed_by'] == 'Админова Алия'
    assert api.as_(OUTSIDER).get('/participants').status_code == 403
    people = api.as_(ADMIN).get('/people', q='посто').get_json()['items']
    assert [p['id'] for p in people] == [OUTSIDER] and people[0]['role'] == 'office'
    assert api.as_(ADMIN).get('/people', q='увол').get_json()['items'] == []
    assert api.as_(ADMIN).delete('/participants/%d' % OUTSIDER).status_code == 200
    assert api.as_(OUTSIDER).get('/context').status_code == 403
    # Администратор раздела остаётся администратором; свой офис ему — по желанию.
    own = api.as_(ADMIN).put('/participants/%d' % ADMIN, {'office_ids': [OFFICE_A]})
    admin_row = next(p for p in own.get_json()['participants'] if p['user_id'] == ADMIN)
    assert admin_row['role'] == 'admin' and admin_row['office_ids'] == [OFFICE_A]
    assert api.as_(ADMIN).get('/context').get_json()['capabilities']['tabs'][:3] == ['overview', 'outgoing', 'incoming']
    none = api.as_(ADMIN).put('/participants/%d' % ADMIN, {'office_ids': []})
    assert next(p for p in none.get_json()['participants'] if p['user_id'] == ADMIN)['office_ids'] == []


# ─── Создание и полнота ──────────────────────────────────────────────────────

def test_other_needs_description_even_in_a_draft(api):
    response = api.as_(CLERK_A).post('/shipments', shipment_body(items=[{'doc_type': 'other', 'qty': 10}]))
    assert response.status_code == 400
    assert response.get_json()['fields'] == {'items.0.other_description': 'Укажите, что именно отправляется'}
    card = create(api, submit=False, items=[{'doc_type': 'other', 'qty': 10,
                                             'other_description': 'Фирменные кепки — 10 шт.'}])
    assert card['items'][0]['other_description'] == 'Фирменные кепки — 10 шт.'
    # Сменили тип — старое описание не хранится.
    edited = api.as_(CLERK_A).patch('/shipments/%d' % card['shipment']['id'],
                                    shipment_body(items=[{'doc_type': 'contract', 'qty': 1,
                                                          'other_description': 'хвост'}])).get_json()
    assert edited['items'][0]['other_description'] is None
    found = registry_numbers(api, CLERK_A, scope='outgoing', q='кепки')
    assert found == set()  # описание ушло вместе с «Иное»


def test_ready_needs_complete_card_and_number_grows_per_year(api):
    draft = create(api, submit=False, recipient_name='', weight_kg=None, payer_entity_id=None)
    shipment = draft['shipment']
    assert shipment['status'] == 'draft' and shipment['number'].endswith('-00001')
    assert set(draft['missing_for_ready']) == {'recipient_name', 'weight_kg', 'payer_entity_id'}
    refused = api.as_(CLERK_A).post('/shipments/%d/actions/ready' % shipment['id'])
    assert refused.status_code == 400 and set(refused.get_json()['fields']) == set(draft['missing_for_ready'])
    second = create(api)
    assert second['shipment']['number'].endswith('-00002') and second['shipment']['status'] == 'ready'
    assert second['shipment']['number'].startswith('MAIL-%d-' % queries.today_almaty().year)
    # Закрытое юрлицо и чужой офис отправки не пройдут.
    assert api.as_(CLERK_A).post('/shipments', shipment_body(payer_entity_id=8)).status_code == 400
    assert api.as_(CLERK_A).post('/shipments', shipment_body(sender_office_id=OFFICE_P)).status_code == 400
    assert api.as_(REGIONAL).post('/shipments', shipment_body()).status_code == 403


# ─── Весь путь отправления ───────────────────────────────────────────────────

def test_full_path_from_draft_to_accepted(api):
    card = create(api, submit=False)
    sid = card['shipment']['id']
    number = card['shipment']['number']
    # «Свои черновики»: коллега по офису и получатель черновик не видят, координатор видит.
    assert number not in registry_numbers(api, CLERK_P, scope='incoming')
    assert api.as_(CLERK_P).get('/shipments/%d' % sid).status_code == 404
    assert number in registry_numbers(api, COORD)
    assert api.as_(CLERK_A).post('/shipments/%d/actions/ready' % sid).status_code == 200
    requested = api.as_(CLERK_A).post('/shipments/%d/actions/courier_request' % sid,
                                      {'planned_pickup_on': queries.today_almaty().isoformat()},
                                      files=[('накладная.pdf', b'%PDF')])
    assert requested.status_code == 200, requested.get_json()
    assert requested.get_json()['files'][0]['kind'] == 'waybill'
    failed = api.as_(CLERK_A).post('/shipments/%d/actions/pickup_failed' % sid,
                                   {'planned_pickup_on': (queries.today_almaty() + timedelta(days=1)).isoformat()})
    assert failed.status_code == 400 and failed.get_json()['fields'] == {'note': 'Укажите причину'}
    failed = api.as_(CLERK_A).post('/shipments/%d/actions/pickup_failed' % sid, {
        'planned_pickup_on': (queries.today_almaty() + timedelta(days=1)).isoformat(), 'note': 'курьер не приехал'})
    assert failed.get_json()['shipment']['status'] == 'courier_requested'
    # Маршрута в матрице нет — зону выбирают вручную; накладная уже есть, второй файл не нужен.
    assert card['route_zone'] is None
    no_zone = api.as_(CLERK_A).post('/shipments/%d/actions/hand_over' % sid, {
        'handed_at': queries.now_almaty().replace(microsecond=0).isoformat(), 'track_number': 'MBE-58214'})
    assert no_zone.status_code == 400 and 'zone_code' in no_zone.get_json()['fields']
    handed = api.as_(CLERK_A).post('/shipments/%d/actions/hand_over' % sid, {
        'handed_at': queries.now_almaty().replace(microsecond=0).isoformat(), 'track_number': 'MBE-58214',
        'zone_code': '1'})
    assert handed.status_code == 200, handed.get_json()
    shipment = handed.get_json()['shipment']
    holidays = rules.kz_holidays()
    handed_on = queries.today_almaty()
    assert shipment['status'] == 'handed_over' and shipment['zone_manual'] is True
    assert (shipment['sla_min_days'], shipment['sla_max_days']) == (2, 3)
    assert shipment['due_on'] == rules.add_working_days(handed_on, 3, holidays).isoformat()
    # Входящая видна сразу после передачи курьеру — и получателю, и его коллеге.
    assert number in registry_numbers(api, CLERK_P, scope='incoming')
    assert number in registry_numbers(api, CLERK_P2, scope='incoming')
    # Своё отправление не принимает никто, даже админ-координатор, который его создал бы.
    assert 'reconcile' not in api.as_(CLERK_A).get('/shipments/%d' % sid).get_json()['actions']
    assert 'reconcile' in api.as_(CLERK_P).get('/shipments/%d' % sid).get_json()['actions']
    assert api.as_(REGIONAL).get('/shipments/%d' % sid).get_json()['actions'] == []
    items = api.as_(CLERK_P).get('/shipments/%d' % sid).get_json()['items']
    # Получатель сверяет сразу, без отметки курьера: дата вручения обязательна.
    lines = [{'id': item['id'], 'line_status': 'ok'} for item in items]
    no_date = api.as_(CLERK_P).post('/shipments/%d/actions/reconcile' % sid, {'lines': lines})
    assert no_date.status_code == 400 and 'delivered_on' in no_date.get_json()['fields']
    accepted = api.as_(CLERK_P).post('/shipments/%d/actions/reconcile' % sid,
                                     {'lines': lines, 'delivered_on': handed_on.isoformat()})
    assert accepted.status_code == 200, accepted.get_json()
    final = accepted.get_json()
    assert final['shipment']['status'] == 'accepted' and final['shipment']['reconciled_by_name'] == \
        'Павлодарова Динара'
    assert final['shipment']['closed_at'] and final['shipment']['received_total'] == 8
    assert [item['received_qty'] for item in final['items']] == [5, 3]
    steps = [(e['kind'], e['from_status'], e['to_status']) for e in final['events']]
    assert steps == [('created', None, 'draft'), ('status', 'draft', 'ready'),
                     ('status', 'ready', 'courier_requested'), ('pickup_failed', None, None),
                     ('status', 'courier_requested', 'handed_over'), ('status', 'handed_over', 'delivered'),
                     ('status', 'delivered', 'accepted')]
    assert final['actions'] == [] and final['edit']['allowed'] is False


def test_hand_over_needs_time_track_and_proof_and_warns_about_duplicate_track(api):
    api.as_(SUPER).put('/settings', {'zones': [
        {'code': '2', 'label': 'Внутригородская', 'min_days': 1, 'max_days': 1},
        {'code': '1', 'label': 'Зона 1', 'min_days': 2, 'max_days': 3},
        {'code': '3', 'label': 'Зона 3', 'min_days': 7, 'max_days': 15}],
        'routes': [{'from_city': 'Алматы', 'to_city': 'Павлодар', 'zone_code': '1', 'both_ways': True}]})
    first = create(api)['shipment']['id']
    second = create(api)['shipment']['id']
    assert api.as_(CLERK_A).get('/shipments/%d' % first).get_json()['route_zone'] == {
        'code': '1', 'label': 'Зона 1', 'window': [2, 3]}
    now = queries.now_almaty().replace(microsecond=0)
    no_proof = api.as_(CLERK_A).post('/shipments/%d/actions/hand_over' % first,
                                     {'handed_at': now.isoformat(), 'track_number': 'MBE-1'})
    assert no_proof.status_code == 400 and 'files' in no_proof.get_json()['fields']
    future = hand_over(api, first, handed_at=(now + timedelta(hours=2)).isoformat())
    assert future.status_code == 400 and 'handed_at' in future.get_json()['fields']
    no_track = hand_over(api, first, track_number='')
    assert no_track.status_code == 400 and 'track_number' in no_track.get_json()['fields']
    assert hand_over(api, first).status_code == 200
    duplicate = hand_over(api, second)
    assert duplicate.status_code == 409 and duplicate.get_json()['code'] == 'MAIL_TRACK_DUPLICATE'
    assert duplicate.get_json()['duplicates'] == [api.as_(CLERK_A).get('/shipments/%d' % first)
                                                  .get_json()['shipment']['number']]
    confirmed = hand_over(api, second, confirm_duplicate=True)
    assert confirmed.status_code == 200 and confirmed.get_json()['shipment']['zone_manual'] is False
    # Отменить после передачи нельзя.
    assert api.as_(CLERK_A).post('/shipments/%d/actions/cancel' % first, {'note': 'ошиблись'}).status_code == 409


def test_discrepancy_becomes_problem_and_coordinator_resolves_it(api, db):
    sid = create(api)['shipment']['id']
    assert hand_over(api, sid, zone_code='1').status_code == 200
    delivered = api.as_(CLERK_P).post('/shipments/%d/actions/deliver' % sid,
                                      {'delivered_on': queries.today_almaty().isoformat()})
    assert delivered.get_json()['shipment']['status'] == 'delivered'
    items = delivered.get_json()['items']
    lines = [{'id': items[0]['id'], 'line_status': 'shortage', 'received_qty': 4},
             {'id': items[1]['id'], 'line_status': 'ok'}]
    no_note = api.as_(CLERK_P).post('/shipments/%d/actions/reconcile' % sid, {'lines': lines})
    assert no_note.status_code == 400 and no_note.get_json()['fields'] == {'note': 'Опишите расхождение'}
    wrong_qty = api.as_(CLERK_P).post('/shipments/%d/actions/reconcile' % sid, {
        'lines': [dict(lines[0], received_qty=5), lines[1]], 'note': 'не хватает'})
    assert wrong_qty.status_code == 400 and 'lines.0.received_qty' in wrong_qty.get_json()['fields']
    problem = api.as_(CLERK_P).post('/shipments/%d/actions/reconcile' % sid, {'lines': lines, 'note': 'одного АВР нет'},
                                    files=[('фото.jpg', b'\xff\xd8\xff')])
    card = problem.get_json()
    assert card['shipment']['status'] == 'problem' and card['files'][-1]['kind'] == 'problem'
    assert card['items'][0]['line_status'] == 'shortage' and card['items'][0]['received_qty'] == 4
    number = card['shipment']['number']
    assert number in registry_numbers(api, COORD, scope='problems')
    assert number in registry_numbers(api, CLERK_A, scope='problems')
    # Строки после сверки не правит никто.
    locked = api.as_(SUPER).patch('/shipments/%d' % sid, dict(shipment_body(items=[{'doc_type': 'avr', 'qty': 4}]),
                                                              reason='поправить'))
    assert locked.status_code == 409
    assert 'resolve' not in api.as_(CLERK_P).get('/shipments/%d' % sid).get_json()['actions']
    resolved = api.as_(COORD).post('/shipments/%d/actions/resolve' % sid, {'note': 'досылаем отдельно'})
    assert resolved.get_json()['shipment']['status'] == 'accepted'


def test_overdue_tiles_and_problem_tab(api, db):
    late = create(api)['shipment']
    assert hand_over(api, late['id'], zone_code='1', track_number='MBE-LATE').status_code == 200
    on_time = create(api)['shipment']
    assert hand_over(api, on_time['id'], zone_code='3', track_number='MBE-OK').status_code == 200
    failed = create(api)['shipment']
    assert hand_over(api, failed['id'], zone_code='1', track_number='MBE-FAIL').status_code == 200
    assert api.as_(CLERK_P).post('/shipments/%d/actions/not_delivered' % failed['id'],
                                 {'note': 'адресата нет на месте'}).status_code == 200
    ready = create(api)['shipment']
    past = queries.today_almaty() - timedelta(days=10)
    with db._get_cursor() as cur:
        cur.execute("UPDATE mail_shipments SET due_on = %s WHERE id IN (%s, %s)", (past, late['id'], failed['id']))
    counters = api.as_(COORD).get('/overview').get_json()['counters']
    assert (counters['to_send'], counters['in_transit'], counters['overdue'], counters['problems']) == (1, 1, 1, 1)
    problems = registry_numbers(api, COORD, scope='problems')
    assert problems == {late['number'], failed['number']}
    card = api.as_(CLERK_A).get('/shipments/%d' % late['id']).get_json()['shipment']
    assert card['overdue'] is True and card['deadline']['kind'] == 'overdue' and card['deadline']['days'] < 0
    attention = api.as_(CLERK_A).get('/overview').get_json()['attention']
    reasons = {group['reason']: {item['number'] for item in group['items']} for group in attention}
    assert reasons['to_send'] == {ready['number']} and reasons['overdue'] == {late['number']}
    assert reasons['problems'] == {failed['number']}


def test_sql_visibility_matches_access_rules(api, db):
    numbers = []
    draft = create(api, submit=False)['shipment']
    numbers.append(draft['number'])
    ready = create(api, recipient_office_id=OFFICE_S)['shipment']
    handed = create(api)['shipment']
    assert hand_over(api, handed['id'], zone_code='1').status_code == 200
    from_p = create(api, user=CLERK_P2, submit=False, sender_office_id=OFFICE_P, recipient_office_id=OFFICE_A2)
    by_coord = create(api, user=COORD, sender_office_id=OFFICE_P, recipient_office_id=OFFICE_A)['shipment']
    by_admin = create(api, user=SUPER, sender_office_id=OFFICE_S, recipient_office_id=OFFICE_A2)['shipment']
    del from_p, by_coord, by_admin, ready
    with db._get_cursor() as cur:
        cur.execute("SELECT id FROM mail_shipments ORDER BY id")
        ids = [row[0] for row in cur.fetchall()]
        shipments = [queries.read_shipment(cur, sid) for sid in ids]
        for user in (SUPER, ADMIN, CLERK_A, CLERK_P, CLERK_P2, COORD, REGIONAL, ACCOUNTING, CENTRAL):
            ctx = queries.load_context(cur, user)
            expected = {s['number'] for s in shipments if access.can_view(ctx, s)}
            rows, _total = queries.list_shipments(cur, ctx, today=queries.today_almaty(), limit=200)
            assert {row['number'] for row in rows} == expected, user
    assert draft['number'] not in registry_numbers(api, ACCOUNTING)


def test_edit_after_handover_needs_reason_and_keeps_offices(api):
    card = create(api)
    sid = card['shipment']['id']
    # До передачи — свободно; без изменений — в историю ничего не идёт.
    same = api.as_(CLERK_A).patch('/shipments/%d' % sid, shipment_body()).get_json()
    assert [e['kind'] for e in same['events']] == ['created']
    assert hand_over(api, sid, zone_code='1').status_code == 200
    no_reason = api.as_(CLERK_A).patch('/shipments/%d' % sid, shipment_body(recipient_phone='+77019999999'))
    assert no_reason.status_code == 400 and no_reason.get_json()['code'] == 'MAIL_REASON_REQUIRED'
    moved = api.as_(CLERK_A).patch('/shipments/%d' % sid, dict(shipment_body(recipient_office_id=OFFICE_S),
                                                              reason='перепутали'))
    assert moved.status_code == 409
    edited = api.as_(CLERK_A).patch('/shipments/%d' % sid, dict(shipment_body(recipient_phone='+77019999999'),
                                                               reason='сменился номер'))
    assert edited.status_code == 200
    event = edited.get_json()['events'][-1]
    assert event['kind'] == 'edited' and event['note'] == 'сменился номер'
    assert event['payload']['changes'] == {'recipient_phone': ['+7 701 000-00-11', '+77019999999']}
    # Получатель чужую карточку не правит.
    assert api.as_(CLERK_P).patch('/shipments/%d' % sid, dict(shipment_body(), reason='x')).status_code == 403


def test_accounting_reads_but_does_not_open_confidential_files(api):
    sid = create(api, items=[{'doc_type': 'contract', 'qty': 1, 'is_confidential': True}])['shipment']['id']
    assert hand_over(api, sid, zone_code='1').status_code == 200
    card = api.as_(ACCOUNTING).get('/shipments/%d' % sid).get_json()
    assert card['files'] == [] and card['files_hidden'] is True and card['actions'] == []
    file_id = api.as_(CLERK_A).get('/shipments/%d' % sid).get_json()['files'][0]['id']
    assert api.as_(ACCOUNTING).get('/files/%d/download' % file_id).status_code == 403
    download = api.as_(CLERK_P).get('/files/%d/download' % file_id)
    assert download.status_code == 200 and download.data == b'%PDF-1.4 test'


def test_export_has_context_registry_items_and_history(api):
    from openpyxl import load_workbook

    sid = create(api, comment='=СУММ(1;2)')['shipment']['id']
    assert hand_over(api, sid, zone_code='1').status_code == 200
    response = api.as_(COORD).get('/export', scope='registry', q='павлодар')
    assert response.status_code == 200
    book = load_workbook(BytesIO(response.data))
    assert book.sheetnames == ['Контекст', 'Отправления', 'Содержимое', 'История']
    sheet = book['Отправления']
    header = [cell.value for cell in sheet[1]]
    row = dict(zip(header, [cell.value for cell in sheet[2]]))
    assert row['Статус'] == 'Передано курьеру' and row['Трек-номер'] == 'MBE-1'
    assert row['Направление'] == 'Алматы → Павлодар' and row['Заявлено'] == 8
    assert row['Комментарий'] == '=СУММ(1;2)' and sheet.cell(2, header.index('Комментарий') + 1).data_type == 's'
    assert row['Зона'] == 'Зона 1 (вручную)' and isinstance(row['Крайний срок'], datetime)
    assert book['Содержимое'].max_row == 3
    history = [[cell.value for cell in line] for line in book['История'].iter_rows(min_row=2)]
    # Создано сразу «Готово к отправке» — одно событие, затем передача курьеру.
    assert [(line[3], line[5]) for line in history] == [('Создано', 'Готово к отправке'),
                                                       ('Смена статуса', 'Передано курьеру')]
    assert 'трек MBE-1' in history[1][6] and 'срок до' in history[1][6]


def test_settings_zones_and_routes(api):
    assert api.as_(CLERK_A).get('/settings').status_code == 403
    saved = api.as_(ADMIN).put('/settings', {
        'zones': [{'code': '2', 'label': 'Внутригородская', 'min_days': 1, 'max_days': 1},
                  {'code': '1', 'label': 'Зона 1', 'min_days': 2, 'max_days': 3, 'eco_min_days': 6,
                   'eco_max_days': 12}],
        'routes': [{'from_city': 'Алматы', 'to_city': 'Павлодар', 'zone_code': '1'}]})
    assert saved.status_code == 200, saved.get_json()
    data = saved.get_json()
    assert [z['code'] for z in data['zones']] == ['2', '1']
    assert {(r['from_city'], r['to_city']) for r in data['routes']} == {('Алматы', 'Павлодар'), ('Павлодар', 'Алматы')}
    assert data['cities'] == ['Алматы', 'Павлодар', 'Шымкент']
    # Зону с маршрутами не убрать, пока на неё ссылаются.
    refused = api.as_(ADMIN).put('/settings', {
        'zones': [{'code': '2', 'label': 'Внутригородская', 'min_days': 1, 'max_days': 1}],
        'routes': [{'from_city': 'Алматы', 'to_city': 'Павлодар', 'zone_code': '1'}]})
    assert refused.status_code == 400
    # Эконом: срок берётся из своих границ зоны.
    sid = create(api, delivery_type='economy')['shipment']['id']
    card = hand_over(api, sid).get_json()['shipment']
    assert (card['sla_min_days'], card['sla_max_days']) == (6, 12)


# ─── Находки разбора 13.11.2026 ──────────────────────────────────────────────

ZONES_WITH_ECONOMY = [
    {'code': '2', 'label': 'Внутригородская', 'min_days': 1, 'max_days': 1},
    {'code': '1', 'label': 'Зона 1', 'min_days': 2, 'max_days': 3, 'eco_min_days': 6, 'eco_max_days': 12},
    {'code': '3', 'label': 'Зона 3', 'min_days': 7, 'max_days': 15},
]


def problem_shipment(api, author):
    sid = create(api, user=author)['shipment']['id']
    assert hand_over(api, sid, user=author, zone_code='1').status_code == 200
    items = api.as_(CLERK_P).get('/shipments/%d' % sid).get_json()['items']
    response = api.as_(CLERK_P).post('/shipments/%d/actions/reconcile' % sid, {
        'delivered_on': queries.today_almaty().isoformat(), 'note': 'не хватает',
        'lines': [{'id': items[0]['id'], 'line_status': 'shortage', 'received_qty': 4},
                  {'id': items[1]['id'], 'line_status': 'ok'}]})
    assert response.get_json()['shipment']['status'] == 'problem'
    return sid


def test_author_never_closes_own_shipment_even_as_coordinator(api):
    sid = problem_shipment(api, COORD)
    assert 'resolve' not in api.as_(COORD).get('/shipments/%d' % sid).get_json()['actions']
    refused = api.as_(COORD).post('/shipments/%d/actions/resolve' % sid, {'note': 'досошлём'})
    assert refused.status_code == 403
    assert api.as_(CENTRAL).post('/shipments/%d/actions/resolve' % sid, {'note': 'досослали'}).status_code == 200


def test_recipient_side_cannot_rewrite_items_and_history_keeps_both_versions(api, db):
    # Координатор только офиса получения (роль заложена, вкладкой не выдаётся).
    seed_role(db, OUTSIDER, 'coordinator', [OFFICE_P])
    sid = create(api)['shipment']['id']
    assert hand_over(api, sid, zone_code='1').status_code == 200
    card = api.as_(OUTSIDER).get('/shipments/%d' % sid).get_json()
    assert card['edit']['allowed'] is True and card['edit']['items'] is False and card['can_fix_handover'] is False
    rewritten = api.as_(OUTSIDER).patch('/shipments/%d' % sid, dict(shipment_body(items=[
        {'doc_type': 'avr', 'qty': 4, 'period_from': '2026-09-01', 'period_to': '2026-09-30'},
        {'doc_type': 'avr_return', 'qty': 3, 'copy_kind': 'copy'}]), reason='пришло меньше'))
    assert rewritten.status_code == 409 and rewritten.get_json()['code'] == 'MAIL_ITEMS_LOCKED'
    # Сторона отправки — может, и в истории остаются обе версии.
    edited = api.as_(CLERK_A).patch('/shipments/%d' % sid, dict(shipment_body(items=[
        {'doc_type': 'avr', 'qty': 6, 'period_from': '2026-09-01', 'period_to': '2026-09-30'},
        {'doc_type': 'avr_return', 'qty': 3, 'copy_kind': 'copy'}]), reason='доложили один акт'))
    assert edited.status_code == 200, edited.get_json()
    payload = edited.get_json()['events'][-1]['payload']
    assert payload['items_before'][0].startswith('АВР ×5') and payload['items_after'][0].startswith('АВР ×6')
    # С «Доставлено курьером» строки закрыты и для отправителя.
    assert api.as_(CLERK_P).post('/shipments/%d/actions/deliver' % sid,
                                 {'delivered_on': queries.today_almaty().isoformat()}).status_code == 200
    assert api.as_(CLERK_A).get('/shipments/%d' % sid).get_json()['edit']['items'] is False


def test_handover_can_be_corrected_and_the_deadline_follows(api):
    sid = create(api)['shipment']['id']
    assert hand_over(api, sid, zone_code='1', track_number='MBE-OLD').status_code == 200
    other = create(api)['shipment']['id']
    assert hand_over(api, other, zone_code='1', track_number='MBE-TAKEN').status_code == 200
    card = api.as_(CLERK_A).get('/shipments/%d' % sid).get_json()
    assert card['can_fix_handover'] is True
    assert api.as_(CLERK_P).get('/shipments/%d' % sid).get_json()['can_fix_handover'] is False
    earlier = queries.today_almaty() - timedelta(days=7)
    body = dict(shipment_body(), reason='ошиблись датой', handed_at=earlier.isoformat() + 'T10:00',
                track_number='MBE-TAKEN')
    duplicate = api.as_(CLERK_A).patch('/shipments/%d' % sid, body)
    assert duplicate.status_code == 409 and duplicate.get_json()['code'] == 'MAIL_TRACK_DUPLICATE'
    fixed = api.as_(CLERK_A).patch('/shipments/%d' % sid, dict(body, confirm_duplicate=True))
    assert fixed.status_code == 200, fixed.get_json()
    shipment = fixed.get_json()['shipment']
    assert shipment['track_number'] == 'MBE-TAKEN' and shipment['handed_at'].startswith(earlier.isoformat())
    assert shipment['due_on'] == rules.add_working_days(earlier, 3, rules.kz_holidays()).isoformat()
    changes = fixed.get_json()['events'][-1]['payload']['changes']
    assert set(changes) == {'track_number', 'handed_at', 'due_on'}
    # Получатель трек не правит, даже прямым запросом.
    assert api.as_(CLERK_P).patch('/shipments/%d' % sid, dict(shipment_body(), reason='x',
                                                             track_number='MBE-X')).status_code == 403


def test_delivery_type_change_after_handover_recalculates_window(api):
    assert api.as_(ADMIN).put('/settings', {'zones': ZONES_WITH_ECONOMY, 'routes': []}).status_code == 200
    sid = create(api)['shipment']['id']
    assert hand_over(api, sid, zone_code='1').status_code == 200
    changed = api.as_(CLERK_A).patch('/shipments/%d' % sid, dict(shipment_body(delivery_type='economy'),
                                                                reason='ушло экономом'))
    assert changed.status_code == 200, changed.get_json()
    shipment = changed.get_json()['shipment']
    assert (shipment['sla_min_days'], shipment['sla_max_days']) == (6, 12)
    # Зона 3 без эконома: тип не меняется.
    third = create(api)['shipment']['id']
    assert hand_over(api, third, zone_code='3', track_number='MBE-3').status_code == 200
    refused = api.as_(CLERK_A).patch('/shipments/%d' % third, dict(shipment_body(delivery_type='economy'), reason='x'))
    assert refused.status_code == 409


def test_economy_is_refused_at_ready_until_its_terms_are_set(api):
    refused = api.as_(CLERK_A).post('/shipments', dict(shipment_body(delivery_type='economy'), submit=True))
    assert refused.status_code == 400 and 'delivery_type' in refused.get_json()['fields']
    draft = create(api, submit=False, delivery_type='economy')['shipment']['id']
    assert api.as_(CLERK_A).post('/shipments/%d/actions/ready' % draft).status_code == 400
    assert api.as_(ADMIN).put('/settings', {'zones': ZONES_WITH_ECONOMY, 'routes': [
        {'from_city': 'Алматы', 'to_city': 'Павлодар', 'zone_code': '3'}]}).status_code == 200
    # Маршрут в зоне 3, у которой эконома нет, — тоже отказ, с названием зоны.
    third = api.as_(CLERK_A).post('/shipments/%d/actions/ready' % draft)
    assert third.status_code == 400 and 'Зона 3' in third.get_json()['fields']['delivery_type']


def test_removed_intracity_zone_falls_back_to_manual_choice(api):
    assert api.as_(ADMIN).put('/settings', {'zones': [z for z in ZONES_WITH_ECONOMY if z['code'] != '2'],
                                            'routes': []}).status_code == 200
    sid = create(api, recipient_office_id=OFFICE_A2)['shipment']['id']
    assert api.as_(CLERK_A).get('/shipments/%d' % sid).get_json()['route_zone'] is None
    no_zone = hand_over(api, sid)
    assert no_zone.status_code == 400 and 'zone_code' in no_zone.get_json()['fields']
    manual = hand_over(api, sid, zone_code='1')
    assert manual.status_code == 200 and manual.get_json()['shipment']['zone_manual'] is True


def test_accounting_sees_no_names_of_closed_files(api):
    from openpyxl import load_workbook

    sid = create(api, items=[{'doc_type': 'contract', 'qty': 1, 'is_confidential': True}])['shipment']['id']
    assert hand_over(api, sid, zone_code='1').status_code == 200
    events = api.as_(ACCOUNTING).get('/shipments/%d' % sid).get_json()['events']
    files = events[-1]['payload']['files']
    assert files == [{'kind': 'handover', 'filename': None}]
    own = api.as_(CLERK_A).get('/shipments/%d' % sid).get_json()['events'][-1]['payload']['files']
    assert own == [{'kind': 'handover', 'filename': 'накладная.pdf'}]
    book = load_workbook(BytesIO(api.as_(ACCOUNTING).get('/export').data))
    history = ' '.join(str(cell.value) for row in book['История'].iter_rows() for cell in row)
    assert 'накладная.pdf' not in history and 'подтверждение передачи' in history


def test_status_and_event_date_filters(api):
    ready = create(api)['shipment']
    handed = create(api)['shipment']
    assert hand_over(api, handed['id'], zone_code='1').status_code == 200
    assert registry_numbers(api, COORD, status='ready') == {ready['number']}
    today = queries.today_almaty().isoformat()
    assert registry_numbers(api, COORD, event='handed', event_from=today, event_to=today) == {handed['number']}
    assert registry_numbers(api, COORD, event='delivered', event_from=today, event_to=today) == set()
    assert api.as_(COORD).get('/shipments', event_to='9999-12-31').status_code == 400
    assert api.as_(COORD).get('/shipments', event='teleport').status_code == 400


def test_odd_inputs_answer_400_not_500(api):
    tiny = api.as_(CLERK_A).post('/shipments', shipment_body(weight_kg='0,0001'))
    assert tiny.status_code == 400 and 'weight_kg' in tiny.get_json()['fields']
    sid = create(api)['shipment']['id']
    # Время с поясом переводится в Алматы: 06:30 UTC = 11:30 по Алматы.
    today = queries.today_almaty().isoformat()
    utc = hand_over(api, sid, zone_code='1', handed_at=today + 'T00:05:00Z')
    assert utc.status_code == 200, utc.get_json()
    assert utc.get_json()['shipment']['handed_at'] == today + 'T05:05:00'


def test_pilot_opens_the_section_to_super_admins_only(api, db, monkeypatch):
    """Пилот 13.11.2026: «чтобы раздел был доступен пока для суперадминов»."""
    monkeypatch.setattr(access, 'PILOT_SUPER_ADMIN_ONLY', True)
    assert api.as_(SUPER).get('/context').status_code == 200
    for user in (ADMIN, CLERK_A, COORD, ACCOUNTING, CENTRAL):
        assert api.as_(user).get('/context').status_code == 403, user
        assert api.as_(user).get('/shipments').status_code == 403, user
    with db._get_cursor() as cur:
        assert queries.section_open_for(cur, SUPER, 'super_admin')
        assert not queries.section_open_for(cur, CLERK_A, 'operator')
        assert not queries.section_open_for(cur, ADMIN, 'admin')
