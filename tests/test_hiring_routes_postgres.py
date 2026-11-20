# -*- coding: utf-8 -*-
"""«Воронка найма» (#404): ручки Blueprint на настоящем Postgres (HIRING_TEST_PORT) через Flask test client.

Процесс проверяет tests/test_hiring_postgres.py; здесь — что каждая ручка отдаёт JSON без падений
(даты, Decimal, вложенные словари), держит границу раздела и права, принимает файл и отдаёт xlsx,
а внешний вход откликов работает по ключу партнёра.

    HIRING_TEST_PORT=55432 python -m pytest tests/test_hiring_routes_postgres.py
"""

import contextlib
import io
import os
import uuid
from datetime import datetime, timedelta
from functools import wraps
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, request

from hiring import queries, routes, rules, schema
from hiring.service import HiringService
from partner_keys.limits import RateLimiter

PORT = os.environ.get('HIRING_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (HIRING_TEST_PORT)')

SUPER, HR_HEAD, REC1, REC2, TRAINER, OP_HEAD, STRANGER = 1, 2, 10, 11, 20, 30, 40

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text UNIQUE, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL,
                        status text NOT NULL DEFAULT 'working', department_id integer, phone text, telegram_id bigint);
    CREATE TABLE daily_hours (id serial PRIMARY KEY, operator_id integer NOT NULL, day date NOT NULL,
                              work_time double precision NOT NULL DEFAULT 0, UNIQUE (operator_id, day));
    INSERT INTO departments (id, code, name, head_user_id) VALUES
        (1499, 'hr', 'HR', 2), (367, 'op', 'Отдел продаж', 30), (1, 'szov', 'СЗоВ', NULL);
    INSERT INTO users (id, name, role, status, department_id) VALUES
        (1, 'Супер Админов', 'super_admin', 'working', 1),
        (2, 'Глава HR', 'admin', 'working', 1499),
        (10, 'Первый Рекрутер', 'operator', 'working', 1),
        (11, 'Удалённый Рекрутер', 'operator', 'working', 1),
        (20, 'Тренер Групп', 'trainer', 'working', 367),
        (30, 'Глава Продаж', 'admin', 'working', 367),
        (40, 'Чужой Оператор', 'operator', 'working', 1);
'''


class _Db:
    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user=os.environ.get('HIRING_TEST_USER', 'postgres'),
                                dbname='postgres', connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.name)
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


class _FakeAuth:
    OK, NOT_READY, UNKNOWN, REVOKED, NO_SCOPE, ERROR = 'ok', 'not_ready', 'unknown', 'revoked', 'no_scope', 'error'

    def presented_from(self, headers, bearer=False):
        return headers.get('X-Api-Key')

    def lookup(self, db, presented, *, scope):
        if presented == 'good':
            return self.OK, {'id': 1, 'name': 'Kaspi', 'scopes': [scope]}
        if presented == 'noscope':
            return self.NO_SCOPE, {'id': 2, 'name': 'Другой', 'scopes': []}
        return self.UNKNOWN, None


class _FakeJournal:
    def __init__(self):
        self.calls = []

    def record(self, db, key, **fields):
        self.calls.append((key['id'], fields))
        return len(self.calls)


class _DialListError(Exception):
    """Как dial_list.service.DialListError: текст и HTTP-статус."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


_DialListError.__name__ = 'DialListError'


class _FakeDialList:
    """Сервис «Удаленного КЦ» без Binotel: один отдел, две линии, посадка «чужого» сотрудника."""

    def __init__(self):
        self.members = {}        # user_id -> (department_id, internal_number)
        self.calls = []
        self.settings = {}       # user_id -> персональное включение обзвона (None = как у отдела)

    def user_setting(self, user_id):
        return self.settings.get(int(user_id))

    def save_user_setting(self, user_id, enabled, changed_by=None):
        self.settings[int(user_id)] = enabled
        return {'user_id': int(user_id), 'enabled': enabled}

    def list_departments(self, department_ids=None, profile=None):
        return [{'department_id': 2200, 'department_name': 'Удаленный КЦ', 'code': 'remote_cc', 'profile': 'remote_cc'}]

    def department_sip_server(self, department_id):
        return 'sip52.binotel.com'

    def list_lines(self, department_id):
        holders = {number: uid for uid, (dep, number) in self.members.items() if dep == department_id}
        return [{'internal_number': number, 'online': number == '8501', 'status': 'online' if number == '8501' else 'offline',
                 'icore_user': ({'id': holders[number], 'name': 'Сотрудник %s' % holders[number], 'login': 'x', 'guest': True,
                                 'department_name': ''} if number in holders else None)}
                for number in ('8501', '8502')]

    def line_member(self, user_id):
        member = self.members.get(int(user_id))
        return {'user_id': int(user_id), 'department_id': member[0], 'internal_number': member[1]} if member else None

    def assign_line(self, department_id, user_id, internal_number, changed_by=None, seat_anyone=False):
        self.calls.append(('assign', department_id, user_id, internal_number, changed_by, seat_anyone))
        if not seat_anyone:
            raise _DialListError('нужно seat_anyone', 403)
        if internal_number not in ('8501', '8502'):
            raise _DialListError('Линии %s у компании Binotel нет' % internal_number, 404)
        for uid, (dep, number) in self.members.items():
            if dep == department_id and number == internal_number and uid != int(user_id):
                raise _DialListError('Линия %s уже у сотрудника %s' % (internal_number, uid), 409)
        self.members[int(user_id)] = (int(department_id), internal_number)
        return {'user_id': int(user_id), 'internal_number': internal_number, 'guest': True}

    def release_line(self, department_id, user_id, changed_by=None, seat_anyone=False):
        self.calls.append(('release', department_id, user_id, changed_by, seat_anyone))
        self.members.pop(int(user_id), None)
        return {'user_id': int(user_id), 'internal_number': ''}

    def client_for_department(self, department_id):
        return SimpleNamespace(get_call_record_url=lambda general_call_id, max_wait=None:
                               'https://records.test/%s/%s.mp3' % (department_id, general_call_id))


@pytest.fixture
def client():
    name = 't_hiringroutes_' + uuid.uuid4().hex
    db = _Db(name)
    admin = db.connect()
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + name)
        cur.execute('SET search_path TO ' + name)
        cur.execute(BASE_DDL)
        schema.init_hiring_schema(cur)
        queries.upsert_person(cur, REC1, 'staff', SUPER, rules.now_almaty())
        queries.upsert_person(cur, REC2, 'remote', SUPER, rules.now_almaty())
        vacancy_id = queries.create_vacancy(cur, 'Оператор', 367, True, SUPER, rules.now_almaty())
        group_id = queries.create_group(cur, 'Группа', datetime(2026, 11, 5).date(), TRAINER, 367, vacancy_id, SUPER, rules.now_almaty())
    admin.commit()

    dial_list = _FakeDialList()
    service = HiringService(cursor_factory=db._get_cursor, dial_list=lambda: dial_list)
    app = Flask('hiring-test')

    def require_api_key(handler):
        @wraps(handler)
        def wrapper(*args, **kwargs):
            return handler(*args, **kwargs)
        return wrapper

    def resolve_requester():
        raw = request.headers.get('X-User-Id')
        if not raw:
            return None, None, ('Не авторизован', 401)
        return int(raw), SimpleNamespace(id=int(raw)), None

    journal = _FakeJournal()
    app.register_blueprint(routes.build_hiring_blueprint(
        db=db, require_api_key=require_api_key, build_cors_preflight_response=lambda: ('', 204),
        resolve_requester=resolve_requester, service=service, dial_list=lambda: dial_list))
    app.register_blueprint(routes.build_hiring_external_blueprint(
        db=db, service=service, partner_auth=_FakeAuth(), partner_scopes=SimpleNamespace(HIRING_LEADS='hiring_leads'),
        partner_journal=journal, rate_limiter=RateLimiter(100)))
    app.register_blueprint(routes.build_hiring_wa_agent_blueprint(service=service, build_cors_preflight_response=lambda: ('', 204)))
    test_client = app.test_client()
    test_client.world = {'vacancy_id': vacancy_id, 'group_id': group_id, 'journal': journal, 'db': db, 'dial_list': dial_list,
                         'service': service}
    try:
        yield test_client
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA %s CASCADE' % name)
        admin.commit()
        admin.close()


def as_user(user_id):
    return {'X-User-Id': str(user_id)}


def test_boot_rights_and_dictionaries(client):
    assert client.get('/api/hiring').status_code == 401
    assert client.get('/api/hiring', headers=as_user(STRANGER)).status_code == 403
    assert client.get('/api/hiring', headers=as_user(999)).status_code == 404
    lead = client.get('/api/hiring', headers=as_user(HR_HEAD)).get_json()
    assert lead['capabilities']['lead'] and lead['capabilities']['manage']
    assert lead['me']['scope_label'] == 'Руководитель рекрутинга'
    assert len(lead['meta']['stages']) == 10 and lead['meta']['shifts_to_hire'] == 7
    assert {row['code'] for row in lead['integrations']} == {'hh', 'olx', 'webhook', 'whatsapp'}
    assert lead['dictionaries']['departments']
    recruiter = client.get('/api/hiring', headers=as_user(REC1)).get_json()
    assert recruiter['capabilities']['recruiter'] and not recruiter['capabilities']['manage']
    assert recruiter['integrations'] == [] and recruiter['dictionaries']['departments'] == []
    assert client.get('/api/hiring', headers=as_user(TRAINER)).status_code == 200     # у тренера есть группа
    assert client.get('/api/hiring', headers=as_user(OP_HEAD)).status_code == 200     # есть вакансия его отдела


def test_card_flow_through_routes(client):
    vacancy_id = client.world['vacancy_id']
    boot = client.get('/api/hiring', headers=as_user(REC1)).get_json()
    manual = next(s for s in boot['dictionaries']['sources'] if s['code'] == 'manual')
    created = client.post('/api/hiring/cards', json={'phone': '8 705 123 45 67', 'full_name': 'Асель', 'city': 'Алматы',
                                                     'source_id': manual['id'], 'vacancy_id': vacancy_id}, headers=as_user(REC1))
    assert created.status_code == 201
    card = created.get_json()['card']
    assert card['stage'] == 'responses' and card['phone_pretty'] == '+7 705 123 45 67' and card['received_at']
    # Удалённому рекрутеру в пуле телефон не виден.
    pool = client.get('/api/hiring/board', headers=as_user(REC2)).get_json()
    responses = next(column for column in pool['columns'] if column['stage'] == 'responses')
    assert responses['total'] == 1 and responses['cards'][0]['phone'] is None and responses['cards'][0]['phone_hidden']
    assert client.post('/api/hiring/cards', json={'phone': '8 705 123 45 67', 'source_id': manual['id']}, headers=as_user(REC1)).status_code == 200
    assert client.post('/api/hiring/cards', json={'phone': '12', 'source_id': manual['id']}, headers=as_user(REC1)).get_json()['code'] == 'HIRING_BAD_PHONE'

    taken = client.post('/api/hiring/cards/%s/take' % card['id'], headers=as_user(REC1))
    assert taken.status_code == 200 and taken.get_json()['card']['recruiter_id'] == REC1
    again = client.post('/api/hiring/cards/%s/take' % card['id'], headers=as_user(REC2))
    assert again.status_code == 409 and again.get_json()['code'] == 'HIRING_ALREADY_TAKEN'
    assert client.get('/api/hiring/cards/%s' % card['id'], headers=as_user(REC2)).status_code == 404
    # «WhatsApp» карточки — адрес её чата в окне «Чаты» (переписки нет — пустой чат); чужому — карточки нет.
    opened = client.post('/api/hiring/cards/%s/chat' % card['id'], headers=as_user(REC1))
    assert opened.status_code == 200 and opened.get_json() == {'key': '+77051234567', 'created': True}
    assert client.post('/api/hiring/cards/%s/chat' % card['id'], headers=as_user(REC2)).status_code == 404
    # Тренер читает всю воронку (владелец 19.11.2026): чужую карточку видит с телефоном, но только для чтения.
    seen = client.get('/api/hiring/cards/%s' % card['id'], headers=as_user(TRAINER))
    assert seen.status_code == 200 and seen.get_json()['read_only'] is True and seen.get_json()['card']['phone_pretty'] == '+7 705 123 45 67'
    refused = client.post('/api/hiring/cards/%s/touches' % card['id'], json={'kind': 'comment', 'body': 'x'}, headers=as_user(TRAINER))
    assert refused.status_code == 403 and refused.get_json()['error'] == 'Карточка открыта вам только для чтения'
    assert client.post('/api/hiring/cards/%s/take' % card['id'], headers=as_user(TRAINER)).status_code == 403
    assert client.get('/api/hiring/cards/%s' % card['id'], headers=as_user(REC1)).get_json()['read_only'] is False
    trainer_board = client.get('/api/hiring/board', headers=as_user(TRAINER)).get_json()
    assert next(c for c in trainer_board['columns'] if c['stage'] == 'in_work')['total'] == 1

    touched = client.post('/api/hiring/cards/%s/touches' % card['id'], json={'kind': 'call', 'result': 'no_answer',
                          'next_contact_at': '2026-10-30T10:00'}, headers=as_user(REC1))
    assert touched.status_code == 201
    # Первое касание в ленте — повторный отклик от второго POST, звонок — последнее.
    assert touched.get_json()['card']['substage'] == 'no_answer' and touched.get_json()['touches'][-1]['kind'] == 'call'
    # «Позвонить» без исхода — касание ждёт телефонию; недостающее дописывается PATCH.
    pending = client.post('/api/hiring/cards/%s/touches' % card['id'], json={'kind': 'call'}, headers=as_user(REC1))
    assert pending.status_code == 201 and pending.get_json()['touches'][-1]['result'] is None
    touch_id = pending.get_json()['touches'][-1]['id']
    edited = client.patch('/api/hiring/cards/%s/touches/%s' % (card['id'], touch_id), json={'result': 'dial', 'body': 'договорились'}, headers=as_user(REC1))
    assert edited.status_code == 200 and edited.get_json()['card']['substage'] == 'dial' and edited.get_json()['touches'][-1]['body'] == 'договорились'
    assert client.patch('/api/hiring/cards/%s/touches/%s' % (card['id'], touch_id), json={'result': 'maybe'}, headers=as_user(REC1)).status_code == 400
    assert client.patch('/api/hiring/cards/%s/touches/999999' % card['id'], json={'body': 'x'}, headers=as_user(REC1)).status_code == 404
    assert client.put('/api/hiring/cards/%s/substage' % card['id'], json={'substage': 'whatsapp'}, headers=as_user(REC1)).get_json()['card']['substage'] == 'whatsapp'
    bad = client.post('/api/hiring/cards/%s/move' % card['id'], json={'target': 'invited_office'}, headers=as_user(REC1))
    assert bad.status_code == 409 and bad.get_json()['code'] == 'HIRING_NEED_OFFICE_DATE'
    moved = client.post('/api/hiring/cards/%s/move' % card['id'], json={'target': 'invited_office', 'office_visit_at': '2026-10-31T11:00'},
                        headers=as_user(REC1)).get_json()
    assert moved['card']['stage'] == 'invited_office' and moved['card']['office_visit_at'] == '2026-10-31T11:00:00'
    assert [row['stage'] for row in moved['stages']] == ['responses', 'in_work', 'invited_office']
    edited = client.patch('/api/hiring/cards/%s' % card['id'], json={'city': 'Астана', 'next_contact_at': None}, headers=as_user(REC1)).get_json()
    assert edited['card']['city'] == 'Астана' and edited['card']['next_contact_at'] is None
    # Смены руками не ведутся: старой ручки нет, пересчёт из статусов — только у карточки на линии.
    assert client.post('/api/hiring/cards/%s/shifts' % card['id'], json={'date': '2026-10-30'}, headers=as_user(REC1)).status_code == 404
    assert client.post('/api/hiring/cards/%s/shifts/sync' % card['id'], headers=as_user(REC1)).status_code == 409

    full = client.get('/api/hiring/cards/%s' % card['id'], headers=as_user(REC1)).get_json()
    assert full['card']['targets'] == ['invited_training', 'refused'] and 'owner' in full['actor']
    assert full['touches'] and full['assignments'][0]['reason'] == 'take' and full['motivation'] is None
    desk = client.get('/api/hiring/desk', query_string={'month': '2026-10'}, headers=as_user(REC1)).get_json()
    assert desk['recruiter_id'] == REC1 and desk['funnel'][0]['stage'] == 'in_work' and desk['motivation']['counted'] == 0
    # Поиск по имени в том же регистре: на тестовом кластере с --locale=C ILIKE не сводит
    # кириллицу к одному регистру (ловушка стенда, на проде UTF-8-локаль), поиск по цифрам телефона — отдельно.
    listed = client.get('/api/hiring/cards', query_string={'q': 'Асель'}, headers=as_user(REC1)).get_json()
    assert listed['total'] == 1
    assert client.get('/api/hiring/cards', query_string={'q': '123 45 67'}, headers=as_user(REC1)).get_json()['total'] == 1
    # Руководитель переназначает и возвращает в пул; чужой в карточку не попадает.
    reassigned = client.post('/api/hiring/cards/%s/reassign' % card['id'], json={'recruiter_id': REC2, 'comment': 'передача'}, headers=as_user(HR_HEAD))
    assert reassigned.status_code == 200 and reassigned.get_json()['card']['recruiter_id'] == REC2
    assert client.post('/api/hiring/cards/%s/reassign' % card['id'], json={'recruiter_id': REC1, 'comment': 'x'}, headers=as_user(REC1)).status_code == 403
    assert client.post('/api/hiring/notifications/seen', headers=as_user(REC1)).status_code == 200


def test_upload_reports_and_settings(client):
    lead = as_user(HR_HEAD)
    boot = client.get('/api/hiring', headers=lead).get_json()
    base = next(s for s in boot['dictionaries']['sources'] if s['code'] == 'base_bought')
    data = {'file': (io.BytesIO('ФИО;Телефон;Город\nА;8 705 000 11 22;Алматы\nБ;нет;\n'.encode('utf-8')), 'база.csv'), 'source_id': str(base['id'])}
    uploaded = client.post('/api/hiring/upload', data=data, content_type='multipart/form-data', headers=lead)
    assert uploaded.status_code == 201
    batch = uploaded.get_json()['batch']
    assert (batch['loaded'], batch['errors']) == (1, 1) and batch['loaded_at']
    assert client.get('/api/hiring/batches', headers=lead).get_json()['items'][0]['id'] == batch['id']
    assert client.get('/api/hiring/batches/%s' % batch['id'], headers=lead).get_json()['batch']['error_rows'][0]['row'] == 3
    assert client.post('/api/hiring/upload', data={'source_id': str(base['id'])}, content_type='multipart/form-data', headers=lead).status_code == 400
    assert client.get('/api/hiring/batches', headers=as_user(REC1)).status_code == 403

    # Загрузка выше пишет карточку текущим временем — месяц отчёта тот же, иначе с ноября тест красный.
    month = rules.month_of(rules.now_almaty())
    dashboard = client.get('/api/hiring/reports/dashboard', query_string={'month': month}, headers=lead).get_json()
    assert dashboard['filters']['month'] == month and len(dashboard['funnel']) == 7 and dashboard['losses']['refused']['count'] == 0
    export = client.get('/api/hiring/reports/dashboard/export', query_string={'month': month}, headers=lead)
    assert export.status_code == 200 and export.data[:2] == b'PK' and 'filename*=UTF-8' in export.headers['Content-Disposition']
    assert client.get('/api/hiring/reports/cards', query_string={'month': month, 'key': 'received'}, headers=lead).get_json()['total'] == 1
    assert client.get('/api/hiring/reports/cards', headers=lead).status_code == 400
    motivation = client.get('/api/hiring/reports/motivation', query_string={'month': '2026-10'}, headers=lead).get_json()
    assert motivation['items'] == []
    assert client.get('/api/hiring/reports/motivation/export', headers=lead).data[:2] == b'PK'

    assert client.post('/api/hiring/people', json={'user_id': TRAINER, 'kind': 'staff'}, headers=lead).status_code == 201
    assert any(row['user_id'] == TRAINER for row in client.get('/api/hiring/people', headers=lead).get_json()['items'])
    assert client.get('/api/hiring/people/search', query_string={'q': 'Тренер'}, headers=lead).get_json()['items'][0]['id'] == TRAINER
    assert client.delete('/api/hiring/people/%s' % TRAINER, headers=lead).get_json()['returned_to_pool'] == 0
    assert client.post('/api/hiring/people', json={'user_id': TRAINER, 'kind': 'staff'}, headers=as_user(REC1)).status_code == 403
    sources = client.post('/api/hiring/sources', json={'name': 'База 2026', 'kind': 'contacts'}, headers=lead)
    assert sources.status_code == 201 and any(s['name'] == 'База 2026' for s in sources.get_json()['items'])
    new_source = next(s for s in sources.get_json()['items'] if s['name'] == 'База 2026')
    assert next(s for s in client.patch('/api/hiring/sources/%s' % new_source['id'], json={'is_active': False}, headers=lead).get_json()['items']
                if s['id'] == new_source['id'])['is_active'] is False
    assert client.post('/api/hiring/vacancies', json={'name': 'Курьер', 'department_id': 367, 'office_interview': False}, headers=lead).status_code == 201
    assert client.post('/api/hiring/reasons', json={'kind': 'drop_we', 'name': 'Опоздания'}, headers=lead).status_code == 201
    assert client.post('/api/hiring/reasons', json={'kind': 'nope', 'name': 'x'}, headers=lead).status_code == 400
    groups = client.post('/api/hiring/groups', json={'name': 'Группа 2', 'start_date': '2026-11-12', 'trainer_id': TRAINER, 'capacity': 15},
                         headers=lead)
    assert groups.status_code == 201 and groups.get_json()['items'][0]['start_date'] == '2026-11-12'
    assert groups.get_json()['items'][0]['capacity'] == 15 and groups.get_json()['items'][0]['trainer_id'] == TRAINER
    plans = client.post('/api/hiring/plans', json={'month': '2026-11', 'plan_fte': '5', 'planned_date': '2026-11-30', 'department_id': 367}, headers=lead)
    assert plans.status_code == 201 and plans.get_json()['items'][0]['plan_fte'] == 5.0
    plan_id = plans.get_json()['items'][0]['id']
    assert client.patch('/api/hiring/plans/%s' % plan_id, json={'plan_fte': 6}, headers=lead).get_json()['items'][0]['plan_fte'] == 6.0
    assert client.get('/api/hiring/plans', query_string={'month': '2026-11'}, headers=lead).get_json()['items'][0]['id'] == plan_id
    assert client.delete('/api/hiring/plans/%s' % plan_id, headers=lead).status_code == 200
    assert client.post('/api/hiring/plans', json={'month': '2026-11', 'plan_fte': 0}, headers=lead).status_code == 400
    integrations = client.get('/api/hiring/integrations', headers=lead).get_json()['items']
    olx = next(row for row in integrations if row['code'] == 'olx')
    assert olx['settings'] == {} and any(item['code'] == 'itaxi' for item in olx['cabinets_available'])
    # Кабинеты «для найма»: только известные роботу, пустой список — чаты в воронку не идут.
    assert client.put('/api/hiring/integrations/olx/settings', json={'cabinets': ['nope']}, headers=lead).status_code == 400
    assert client.put('/api/hiring/integrations/olx/settings', json={'cabinets': 'itaxi'}, headers=lead).status_code == 400
    saved = client.put('/api/hiring/integrations/olx/settings', json={'cabinets': ['itaxi', 'itaxi']}, headers=lead).get_json()['items']
    assert next(row for row in saved if row['code'] == 'olx')['settings'] == {'cabinets': ['itaxi']}
    assert client.put('/api/hiring/integrations/olx/settings', json={'cabinets': ['itaxi']}, headers=as_user(REC1)).status_code == 403
    run = client.post('/api/hiring/integrations/olx/run', headers=lead).get_json()
    assert run['received'] == 0 and next(row for row in run['items'] if row['code'] == 'olx')['state'] == 'not_configured'
    assert client.post('/api/hiring/integrations/nope/run', headers=lead).status_code == 404


def test_trainer_groups_through_routes(client):
    """Тренер (владелец 18.11.2026): раздел открыт ролью и без группы, группы с вместимостью и вакансией заводит сам,
    чужие не правит; рекрутер выбирает из действующих групп и видит свободные места, предстоящие — выше начавшихся."""
    boot = client.get('/api/hiring', headers=as_user(TRAINER)).get_json()
    caps = boot['capabilities']
    assert caps['trainer'] and caps['groups'] and caps['create_groups'] and not (caps['manage'] or caps['recruiter'] or caps['pool'])
    assert boot['me']['scope_label'] == 'Тренер'
    with client.world['db']._get_cursor() as cur:
        cur.execute("INSERT INTO users (id, name, role, status, department_id) VALUES (21, 'Новый Тренер', 'trainer', 'working', 1)")
    newbie = as_user(21)
    vacancy = client.world['vacancy_id']
    today = rules.now_almaty().date()
    soon, later = (today + timedelta(days=3)).isoformat(), (today + timedelta(days=10)).isoformat()
    assert client.get('/api/hiring', headers=newbie).get_json()['capabilities']['groups']
    assert client.get('/api/hiring/groups', headers=newbie).get_json()['items'] == []
    board = client.get('/api/hiring/board', headers=newbie).get_json()
    assert sum(column['total'] for column in board['columns']) == 0

    created = client.post('/api/hiring/groups', json={'name': 'Группа 21', 'start_date': later, 'capacity': '12', 'vacancy_id': vacancy},
                          headers=newbie)
    assert created.status_code == 201
    items = created.get_json()['items']
    assert [(g['name'], g['trainer_id'], g['capacity'], g['seats_taken'], g['seats_free'], g['start_date'], g['status'], g['vacancy_id'])
            for g in items] == [('Группа 21', 21, 12, 0, 12, later, 'active', vacancy)]
    no_capacity = client.post('/api/hiring/groups', json={'name': 'Без мест', 'start_date': later, 'vacancy_id': vacancy}, headers=newbie)
    assert no_capacity.status_code == 400 and no_capacity.get_json()['code'] == 'HIRING_BAD_CAPACITY'
    # Без вакансии у группы тренера не было бы отдела — «Отработал 7 дней» подтвердить было бы некому.
    no_vacancy = client.post('/api/hiring/groups', json={'name': 'Без вакансии', 'start_date': later, 'capacity': 5}, headers=newbie)
    assert no_vacancy.status_code == 400 and no_vacancy.get_json()['code'] == 'HIRING_NEED_VACANCY'
    other = client.post('/api/hiring/groups', json={'name': 'Чужая', 'start_date': later, 'capacity': 3, 'trainer_id': TRAINER,
                                                    'vacancy_id': vacancy}, headers=newbie)
    assert other.status_code == 403
    assert client.patch('/api/hiring/groups/%s' % client.world['group_id'], json={'capacity': 3}, headers=newbie).status_code == 403
    assert client.patch('/api/hiring/groups/999999', json={'capacity': 3}, headers=newbie).status_code == 404
    edited = client.patch('/api/hiring/groups/%s' % items[0]['id'], json={'capacity': 10, 'status': 'done'}, headers=newbie)
    assert edited.status_code == 200 and (edited.get_json()['items'][0]['capacity'], edited.get_json()['items'][0]['status']) == (10, 'done')

    # Рекрутер групп не заводит, а выбирает из действующих: завершённая группа нового тренера в выбор не попала,
    # у старой группы вместимость не задана — мест не считаем.
    recruiter = as_user(REC1)
    assert client.post('/api/hiring/groups', json={'name': 'x', 'start_date': later, 'capacity': 5, 'vacancy_id': vacancy},
                       headers=recruiter).status_code == 403
    assert client.patch('/api/hiring/groups/%s' % client.world['group_id'], json={'capacity': 5}, headers=recruiter).status_code == 403
    picked = client.get('/api/hiring/groups', query_string={'pick': '1'}, headers=recruiter).get_json()['items']
    assert [g['id'] for g in picked] == [client.world['group_id']]
    assert picked[0]['capacity'] is None and picked[0]['seats_free'] is None and picked[0]['seats_taken'] == 0
    # Выбор: предстоящие — ближний старт сверху, начавшаяся группа мира (старт 05.11.2026) — ниже; список тренера и
    # руководителя — свежие сверху.
    ids = {}
    for name, start in (('Поздняя', later), ('Скорая', soon)):
        reply = client.post('/api/hiring/groups', json={'name': name, 'start_date': start, 'capacity': 4, 'vacancy_id': vacancy}, headers=newbie)
        ids[name] = next(g['id'] for g in reply.get_json()['items'] if g['name'] == name)
    picked = client.get('/api/hiring/groups', query_string={'pick': '1'}, headers=recruiter).get_json()['items']
    assert [g['id'] for g in picked] == [ids['Скорая'], ids['Поздняя'], client.world['group_id']]
    assert [g['id'] for g in client.get('/api/hiring/groups', headers=recruiter).get_json()['items']] == [
        ids['Поздняя'], ids['Скорая'], client.world['group_id']]
    # Руководитель видит все группы и задаёт вместимость любой.
    lead = as_user(HR_HEAD)
    assert {g['id'] for g in client.get('/api/hiring/groups', headers=lead).get_json()['items']} == {
        client.world['group_id'], items[0]['id'], ids['Поздняя'], ids['Скорая']}
    assert client.patch('/api/hiring/groups/%s' % client.world['group_id'], json={'capacity': 8}, headers=lead).status_code == 200
    picked = client.get('/api/hiring/groups', query_string={'pick': '1'}, headers=recruiter).get_json()['items']
    legacy = next(g for g in picked if g['id'] == client.world['group_id'])
    assert (legacy['capacity'], legacy['seats_free']) == (8, 8)


def test_sources_with_color_through_routes(client):
    """Владелец 20.11.2026: каналы слиты с источниками, цвет — у источника. Справочник руководителя с цветом, источник
    карточки — PATCH карточки; ручек каналов больше нет."""
    lead, rec = as_user(HR_HEAD), as_user(REC1)
    boot = client.get('/api/hiring', headers=rec).get_json()
    assert 'channels' not in boot['dictionaries']
    colors = {s['code']: s['color'] for s in boot['dictionaries']['sources']}
    assert (colors['target'], colors['hh'], colors['kaspi'], colors['whatsapp']) == ('blue', 'red', 'pink', None)
    assert client.post('/api/hiring/channels', json={'name': 'OLX', 'color': 'green'}, headers=lead).status_code == 404

    added = client.post('/api/hiring/sources', json={'name': 'Instagram', 'kind': 'responses', 'color': 'violet'}, headers=lead)
    assert added.status_code == 201
    insta = next(s for s in added.get_json()['items'] if s['name'] == 'Instagram')
    assert (insta['kind'], insta['color'], insta['is_active']) == ('responses', 'violet', True)
    assert client.post('/api/hiring/sources', json={'name': 'instagram', 'kind': 'responses'}, headers=lead).status_code == 409
    bad = client.post('/api/hiring/sources', json={'name': 'X', 'kind': 'responses', 'color': 'black'}, headers=lead)
    assert bad.status_code == 400 and bad.get_json()['code'] == 'HIRING_BAD_SOURCE'
    assert client.post('/api/hiring/sources', json={'name': 'X', 'kind': 'responses'}, headers=rec).status_code == 403
    assert client.patch('/api/hiring/sources/%s' % insta['id'], json={'color': 'teal'}, headers=rec).status_code == 403
    recolored = client.patch('/api/hiring/sources/%s' % insta['id'], json={'color': 'teal'}, headers=lead).get_json()['items']
    assert [s['color'] for s in recolored if s['id'] == insta['id']] == ['teal']
    # Окно источника шлёт имя вместе с цветом — своё имя не дубль.
    same_name = client.patch('/api/hiring/sources/%s' % insta['id'], json={'name': 'Instagram', 'color': 'green'}, headers=lead)
    assert same_name.status_code == 200 and [s['color'] for s in same_name.get_json()['items'] if s['id'] == insta['id']] == ['green']
    assert client.patch('/api/hiring/sources/999999', json={'color': 'teal'}, headers=lead).status_code == 404

    manual = next(s for s in boot['dictionaries']['sources'] if s['code'] == 'manual')
    target = next(s for s in boot['dictionaries']['sources'] if s['code'] == 'target')
    base = next(s for s in boot['dictionaries']['sources'] if s['code'] == 'base_bought')
    card = client.post('/api/hiring/cards', json={'phone': '8 705 321 00 01', 'source_id': manual['id']}, headers=rec).get_json()['card']
    assert (card['source_name'], card['source_color']) == ('Вручную', None) and 'channel_id' not in card
    assert client.post('/api/hiring/cards/%s/take' % card['id'], headers=rec).status_code == 200
    moved = client.patch('/api/hiring/cards/%s' % card['id'], json={'source_id': target['id']}, headers=rec)
    assert moved.status_code == 200 and (moved.get_json()['card']['source_name'], moved.get_json()['card']['source_color']) == ('Таргет', 'blue')
    refused = client.patch('/api/hiring/cards/%s' % card['id'], json={'source_id': base['id']}, headers=rec)
    assert refused.status_code == 400 and refused.get_json()['code'] == 'HIRING_BAD_SOURCE'
    in_work = next(column for column in client.get('/api/hiring/board', headers=rec).get_json()['columns'] if column['stage'] == 'in_work')
    assert [(c['source_name'], c['source_color']) for c in in_work['cards']] == [('Таргет', 'blue')] and 'groups' not in in_work


def test_board_responses_groups_and_period_through_routes(client):
    """Владелец 20.11.2026: «Отклики» поделены по источникам с названием и числом; группы свёрнуты, пока их не раскрыли
    (open — карточки только раскрытым); период — только у «Откликов» (по дате поступления, «по» — включительно),
    остальные этапы им не режутся; «Показать ещё» — expand. Карточки группы — номерами: сами они в колонке, один раз."""
    rec = as_user(REC1)
    boot = client.get('/api/hiring', headers=rec).get_json()
    by_code = {s['code']: s for s in boot['dictionaries']['sources']}

    def response(phone, code, received):
        card = client.post('/api/hiring/cards', json={'phone': phone, 'source_id': by_code[code]['id']}, headers=rec).get_json()['card']
        with client.world['db']._get_cursor() as cur:
            cur.execute("UPDATE hiring_cards SET received_at = %s WHERE id = %s", (received, card['id']))
        return card['id']

    ids = [response('8 705 400 00 0%d' % n, code, datetime(2026, 11, day, 12, 0))
           for n, (code, day) in enumerate((('whatsapp', 10), ('target', 12), ('target', 18), ('hh', 19)), start=1)]
    old = response('8 705 400 00 05', 'hh', datetime(2026, 11, 1, 12, 0))
    assert client.post('/api/hiring/cards/%s/take' % old, headers=rec).status_code == 200

    def board(**query):
        columns = client.get('/api/hiring/board', query_string=query, headers=rec).get_json()['columns']
        return {column['stage']: column for column in columns}

    full = board()
    responses = full['responses']
    assert responses['total'] == 4 and [c['id'] for c in responses['cards']] == ids[::-1]
    # Группы — в порядке справочника источников (HeadHunter, Таргет, WhatsApp), с цветом, числом и номерами карточек.
    assert [(g['name'], g['color'], g['total'], g['card_ids']) for g in responses['groups']] == [
        ('HeadHunter', 'red', 1, [ids[3]]), ('Таргет', 'blue', 2, [ids[2], ids[1]]), ('WhatsApp', None, 1, [ids[0]])]
    assert all('cards' not in g for g in responses['groups'])                  # карточка в ответе — один раз
    assert full['in_work']['total'] == 1 and 'groups' not in full['in_work']

    # Свёрнутые группы: open — раскрытые; карточки только им, числа — всем; '-' — ни одной; мусор не ломает ответ.
    target_id, hh_id = by_code['target']['id'], by_code['hh']['id']
    opened = board(open=str(target_id))['responses']
    assert [c['id'] for c in opened['cards']] == [ids[2], ids[1]] and opened['total'] == 4
    assert [(g['name'], g['total'], g['card_ids']) for g in opened['groups']] == [
        ('HeadHunter', 1, []), ('Таргет', 2, [ids[2], ids[1]]), ('WhatsApp', 1, [])]
    closed = board(open='-')
    assert closed['responses']['cards'] == [] and closed['responses']['total'] == 4 and closed['in_work']['total'] == 1
    assert [c['id'] for c in board(open='%s,abc,99999999999,none' % hh_id)['responses']['cards']] == [ids[3]]
    # Выключенный источник группу держит — со своим именем и цветом.
    client.patch('/api/hiring/sources/%s' % hh_id, json={'is_active': False}, headers=as_user(HR_HEAD))
    assert [(g['name'], g['color']) for g in board()['responses']['groups']][0] == ('HeadHunter', 'red')
    client.patch('/api/hiring/sources/%s' % hh_id, json={'is_active': True}, headers=as_user(HR_HEAD))
    # Удалённый рекрутер: в группах только номера, телефоны пула скрыты в карточках колонки.
    remote = client.get('/api/hiring/board', headers=as_user(REC2)).get_json()['columns']
    remote_responses = next(column for column in remote if column['stage'] == 'responses')
    assert remote_responses['cards'] and all(c['phone'] is None and c['phone_hidden'] for c in remote_responses['cards'])
    assert all(set(g) == {'source_id', 'name', 'color', 'total', 'card_ids'} for g in remote_responses['groups'])

    # Период 12.11–18.11 включительно: «Отклики» — только поступившие в эти дни, «В работе» — как было.
    cut = board(responses_from='2026-11-12', responses_to='2026-11-18')
    assert cut['responses']['total'] == 2 and [(g['name'], g['total']) for g in cut['responses']['groups']] == [('Таргет', 2)]
    assert cut['in_work']['total'] == 1 and [c['id'] for c in cut['in_work']['cards']] == [old]
    assert board(responses_from='2026-11-19')['responses']['total'] == 1
    assert board(responses_to='2026-11-10')['responses']['total'] == 1
    assert board(responses_from='мусор', responses_to='2026-13-45')['responses']['total'] == 4     # неразборчивое — без периода
    assert board(responses_from='2026-12-01')['responses']['groups'] == []
    # Общий отбор доски (from / to) по-прежнему режет все колонки — период «Откликов» отдельный параметр.
    assert board(**{'from': '2026-11-12'})['in_work']['total'] == 0
    # expand принимает только числа (в пределах int4) и не ломает ответ.
    assert board(expand='%s,abc,99999999999' % by_code['target']['id'])['responses']['total'] == 4

    # «Показать ещё»: порция — 60 карточек на группу, expand отдаёт группу целиком.
    with client.world['db']._get_cursor() as cur:
        now = rules.now_almaty()
        for n in range(60):
            queries.insert_card(cur, {'kind': 'response', 'full_name': 'Кандидат %d' % n, 'phone': '+7705410%04d' % n,
                                      'source_id': target_id, 'received_at': now, 'stage': 'responses',
                                      'created_at': now, 'updated_at': now})
    portion = board(open=str(target_id))['responses']
    target_group = next(g for g in portion['groups'] if g['source_id'] == target_id)
    assert (target_group['total'], len(target_group['card_ids'])) == (62, 60)
    whole = board(open=str(target_id), expand=str(target_id))['responses']
    assert len(next(g for g in whole['groups'] if g['source_id'] == target_id)['card_ids']) == 62
    assert len(whole['cards']) == 62


def test_external_responses(client):
    body = {'source': 'kaspi', 'items': [{'phone': '8 705 777 00 01', 'name': 'Kaspi Один', 'city': 'Алматы', 'vacancy': 'Водитель'},
                                        {'phone': 'мусор'}]}
    assert client.post('/api/hiring/external/responses', json=body).status_code == 401
    assert client.post('/api/hiring/external/responses', json=body, headers={'X-Api-Key': 'noscope'}).status_code == 403
    accepted = client.post('/api/hiring/external/responses', json=body, headers={'X-Api-Key': 'good'})
    assert accepted.status_code == 200
    payload = accepted.get_json()
    # Партнёру — только «принят / не телефон», без номеров карточек и без «это наш сотрудник».
    assert payload['accepted'] == 1 and [row['status'] for row in payload['results']] == ['accepted', 'invalid']
    assert all('card_id' not in row for row in payload['results'])
    repeat = client.post('/api/hiring/external/responses', json={'source': 'kaspi', 'phone': '8 705 777 00 01'}, headers={'X-Api-Key': 'good'}).get_json()
    assert repeat['results'] == [{'status': 'accepted', 'phone': '+77057770001'}]
    # Площадки со своим опросом (hh, olx) и базы контактов партнёру закрыты.
    assert client.post('/api/hiring/external/responses', json={'source': 'hh', 'phone': '8 705 777 00 05'}, headers={'X-Api-Key': 'good'}).status_code == 400
    assert client.post('/api/hiring/external/responses', json={'items': [{'phone': '8 705 777 00 02'}]}, headers={'X-Api-Key': 'good'}).status_code == 400
    assert client.post('/api/hiring/external/responses', json={'source': 'nope', 'phone': '8 705 777 00 03'}, headers={'X-Api-Key': 'good'}).status_code == 400
    assert client.post('/api/hiring/external/responses', data='[]', content_type='application/json', headers={'X-Api-Key': 'good'}).status_code == 400
    assert [call[1]['outcome'] for call in client.world['journal'].calls] == ['forbidden', 'ok', 'ok', 'rejected', 'rejected']
    card = client.get('/api/hiring/cards', query_string={'q': 'Kaspi'}, headers=as_user(HR_HEAD)).get_json()['items'][0]
    assert card['source_code'] == 'kaspi' and card['vacancy_text'] == 'Водитель' and card['stage'] == 'responses'


def test_recruiter_lines_from_remote_call_center(client):
    """Линия Binotel рекрутеру — из настроек воронки (владелец 30.10.2026): тем же сервисом, что
    в «Удаленном КЦ», посадка «чужого» сотрудника (seat_anyone); отключение снимает линию."""
    lead = as_user(HR_HEAD)
    dial_list = client.world['dial_list']
    people = client.get('/api/hiring/people', headers=lead).get_json()
    assert people['lines_available'] is True and all(row['line'] is None for row in people['items'])
    departments = client.get('/api/hiring/lines', headers=lead).get_json()
    assert departments['departments'] == [{'department_id': 2200, 'department_name': 'Удаленный КЦ'}] and departments['lines'] == []
    lines = client.get('/api/hiring/lines', query_string={'department_id': 2200}, headers=lead).get_json()
    assert lines['sip_server'] == 'sip52.binotel.com'
    assert [(line['internal_number'], line['online'], line['holder']) for line in lines['lines']] == [('8501', True, None), ('8502', False, None)]
    assert client.get('/api/hiring/lines', query_string={'department_id': 1}, headers=lead).status_code == 404
    assert client.get('/api/hiring/lines', headers=as_user(REC1)).status_code == 403
    # Посадить можно только действующего рекрутера, только на известную линию, и не дважды на одну.
    assert client.post('/api/hiring/people/%s/line' % TRAINER, json={'department_id': 2200, 'internal_number': '8501'}, headers=lead).get_json()['code'] == 'HIRING_NOT_RECRUITER'
    assert client.post('/api/hiring/people/%s/line' % REC1, json={'department_id': 2200}, headers=lead).status_code == 400
    missing = client.post('/api/hiring/people/%s/line' % REC1, json={'department_id': 2200, 'internal_number': '9999'}, headers=lead)
    assert missing.status_code == 404 and missing.get_json()['code'] == 'HIRING_LINE_ERROR'
    seated = client.post('/api/hiring/people/%s/line' % REC1, json={'department_id': 2200, 'internal_number': '8501'}, headers=lead)
    assert seated.status_code == 200
    row = next(item for item in seated.get_json()['items'] if item['user_id'] == REC1)
    assert row['line'] == {'department_id': 2200, 'department_name': 'Удаленный КЦ', 'internal_number': '8501'}
    assert dial_list.calls[-1] == ('assign', 2200, REC1, '8501', HR_HEAD, True)
    # Телефон рекрутера — обычная учётка, не обзвон (владелец 30.10.2026): персонально «выключено».
    assert dial_list.settings[REC1] is False
    busy = client.post('/api/hiring/people/%s/line' % REC2, json={'department_id': 2200, 'internal_number': '8501'}, headers=lead)
    assert busy.status_code == 409 and 'уже у сотрудника' in busy.get_json()['error']
    taken = client.get('/api/hiring/lines', query_string={'department_id': 2200}, headers=lead).get_json()['lines'][0]
    assert taken['holder']['id'] == REC1
    assert client.post('/api/hiring/people/%s/line' % REC1, json={'department_id': 2200, 'internal_number': '8501'}, headers=as_user(REC1)).status_code == 403
    # Снять с линии — и явно, и при отключении рекрутера.
    released = client.delete('/api/hiring/people/%s/line' % REC1, headers=lead)
    assert released.status_code == 200 and next(item for item in released.get_json()['items'] if item['user_id'] == REC1)['line'] is None
    assert dial_list.calls[-1] == ('release', 2200, REC1, HR_HEAD, True)
    assert dial_list.settings[REC1] is None      # снят с линии — снова «как у отдела»
    assert client.delete('/api/hiring/people/%s/line' % REC1, headers=lead).status_code == 404
    client.post('/api/hiring/people/%s/line' % REC2, json={'department_id': 2200, 'internal_number': '8502'}, headers=lead)
    assert dial_list.line_member(REC2)['internal_number'] == '8502'
    assert client.delete('/api/hiring/people/%s' % REC2, headers=lead).status_code == 200
    assert dial_list.line_member(REC2) is None


def test_call_recording_link(client):
    """Звонки из телефонии в карточке и запись разговора ссылкой от Binotel — тому, кто видит карточку;
    у недозвона записи нет (владелец 30.10.2026)."""
    vacancy_id = client.world['vacancy_id']
    boot = client.get('/api/hiring', headers=as_user(REC1)).get_json()
    manual = next(s for s in boot['dictionaries']['sources'] if s['code'] == 'manual')
    card = client.post('/api/hiring/cards', json={'phone': '8 705 777 00 01', 'full_name': 'Данияр', 'source_id': manual['id'],
                                                  'vacancy_id': vacancy_id}, headers=as_user(REC1)).get_json()['card']
    assert client.post('/api/hiring/cards/%s/take' % card['id'], headers=as_user(REC1)).status_code == 200
    now = datetime(2026, 10, 30, 11, 0)
    with client.world['db']._get_cursor() as cur:
        answered = queries.insert_call(cur, {'general_call_id': 'g-1', 'internal_number': '8501', 'external_phone': '+77057770001',
                                             'direction': 'out', 'disposition': 'ANSWER', 'billsec': 95, 'waitsec': 4,
                                             'started_at': now}, card['id'], REC1, 2200, 'answered', 'available', 'webhook', now)
        missed = queries.insert_call(cur, {'general_call_id': 'g-2', 'internal_number': '8501', 'external_phone': '+77057770001',
                                           'direction': 'in', 'disposition': 'NOANSWER', 'billsec': 0, 'waitsec': 20,
                                           'started_at': now}, card['id'], REC1, 2200, 'no_answer', 'none', 'history', now)
    shown = client.get('/api/hiring/cards/%s' % card['id'], headers=as_user(REC1)).get_json()
    assert [(row['general_call_id'], row['result'], row['recording']) for row in shown['calls']] == \
        [('g-1', 'answered', 'available'), ('g-2', 'no_answer', 'none')]
    assert shown['calls'][0]['recruiter_name'] == 'Первый Рекрутер' and shown['calls'][0]['started_at'] == '2026-10-30T11:00:00'
    link = client.get('/api/hiring/calls/%s/recording' % answered, headers=as_user(REC1))
    assert link.status_code == 200
    assert link.get_json() == {'call_id': answered, 'url': 'https://records.test/2200/g-1.mp3', 'billsec': 95}
    none = client.get('/api/hiring/calls/%s/recording' % missed, headers=as_user(REC1))
    assert none.status_code == 404 and none.get_json()['code'] == 'HIRING_NO_RECORDING'
    assert client.get('/api/hiring/calls/%s/recording' % answered, headers=as_user(REC2)).status_code == 404
    assert client.get('/api/hiring/calls/%s/recording' % answered, headers=as_user(HR_HEAD)).status_code == 200
    assert client.get('/api/hiring/calls/999999/recording', headers=as_user(REC1)).status_code == 404


def test_whatsapp_bridge_is_signed_and_buttons_are_lead_only(client, monkeypatch):
    """Сервис WhatsApp на шлюзе входит подписью моста (CDR_AGENT_KEYS), как автоинформатор: без подписи,
    повтором и с подменённым телом — отказ. Кнопки привязки — только руководителю рекрутинга."""
    import json as _json_mod

    from cdr_bridge import signing

    private_b64, public_b64, _kid = signing.generate()
    monkeypatch.setenv('CDR_AGENT_KEYS', public_b64)
    signer = signing.Signer(signing.load_private_key(private_b64))

    def signed(path, payload, **header_overrides):
        body = _json_mod.dumps(payload).encode('utf-8')
        headers = signer.headers('POST', path, body)
        headers.update(header_overrides)
        return client.post(path, data=body, content_type='application/json', headers=headers), headers, body

    poll = '/api/hiring/wa/agent/poll'
    assert client.post(poll, json={'state': 'unpaired'}).status_code == 401
    response, headers, body = signed(poll, {'state': 'unpaired', 'detail': {'version': 'wa-bridge 1.0.0'}})
    assert response.status_code == 200 and response.get_json() == {'command': None, 'next_poll_sec': 10}
    # Тот же одноразовый номер второй раз — повтор; чужое тело под той же подписью — подмена.
    assert client.post(poll, data=body, content_type='application/json', headers=headers).status_code == 401
    tampered = signer.headers('POST', poll, body)
    assert client.post(poll, data=body.replace(b'unpaired', b'connected'), content_type='application/json',
                       headers=tampered).status_code == 401
    assert signed(poll, {'state': 'nonsense'})[0].status_code == 400

    lead, recruiter = as_user(HR_HEAD), as_user(REC1)
    assert client.get('/api/hiring/whatsapp', headers=recruiter).status_code == 403
    assert client.post('/api/hiring/whatsapp/command', json={'action': 'pair'}, headers=recruiter).status_code == 403
    status = client.get('/api/hiring/whatsapp', headers=lead).get_json()
    assert status['state'] == 'unpaired' and status['actions']['pair'] is True and status['unmatched'] == {'foreign': 0, 'hidden': 0}
    queued = client.post('/api/hiring/whatsapp/command', json={'action': 'pair'}, headers=lead)
    assert queued.status_code == 200 and queued.get_json()['pending'] == 'pair'
    assert client.post('/api/hiring/whatsapp/command', json={'action': 'logout'}, headers=lead).status_code == 409
    command = signed(poll, {'state': 'unpaired'})[0].get_json()['command']
    assert command['action'] == 'pair'

    events = '/api/hiring/wa/agent/events'
    message = {'type': 'message', 'id': 'w1', 'chat': '77055551122@s.whatsapp.net', 'phone': '77055551122',
               'from_me': False, 'ts': 1792047600, 'push_name': 'Кандидат', 'kind': 'text', 'text': 'Здравствуйте'}
    accepted = signed(events, {'events': [message]})[0]
    assert accepted.status_code == 200 and accepted.get_json()['outcomes'] == {'created': 1}
    assert signed(events, {'events': []})[0].status_code == 400
    card = client.get('/api/hiring/cards', query_string={'q': '7055551122'}, headers=lead).get_json()['items'][0]
    shown = client.get('/api/hiring/cards/%s' % card['id'], headers=lead).get_json()
    assert shown['whatsapp']['messages'] == 1
    thread = client.get('/api/hiring/cards/%s/whatsapp' % card['id'], headers=lead).get_json()
    assert thread['messages'][0]['body'] == 'Здравствуйте'
    # Удалённому рекрутеру карточка пула видна, а телефон и переписка — нет.
    remote = client.get('/api/hiring/cards/%s' % card['id'], headers=as_user(REC2)).get_json()
    assert remote['card']['phone'] is None and remote['whatsapp'] is None
    assert client.get('/api/hiring/cards/%s/whatsapp' % card['id'], headers=as_user(REC2)).status_code == 403
    # «Отвечает с телефона» — только руководитель; выбор берёт в работу уже отвеченную карточку.
    reply = dict(message, id='w2', from_me=True, ts=1792047600 + 600, text='Добрый день!')
    assert signed(events, {'events': [reply]})[0].get_json()['outcomes'] == {'stored': 1}
    assert client.put('/api/hiring/whatsapp/settings', json={'phone_recruiter_id': REC1}, headers=recruiter).status_code == 403
    saved = client.put('/api/hiring/whatsapp/settings', json={'phone_recruiter_id': REC1}, headers=lead)
    assert saved.status_code == 200 and saved.get_json()['applied'] == 1 and saved.get_json()['phone_recruiter_id'] == REC1
    taken = client.get('/api/hiring/cards/%s' % card['id'], headers=lead).get_json()['card']
    assert taken['stage'] == 'in_work' and taken['recruiter_id'] == REC1

    # Файл сообщения: id в пути, файл телом — всё под подписью; без подписи и с подменой — отказ.
    stored = {}
    client.world['service']._storage = SimpleNamespace(
        available=lambda: True, upload=lambda path, data, ctype: stored.update({path: (data, ctype)}),
        sign=lambda path, ctype, disposition: 'https://storage.test/' + path, delete=lambda path: None)
    photo = dict(message, id='w3', kind='image', text='', ts=1792047600 + 700,
                 media={'mime': 'image/jpeg', 'size': 5, 'fetch': True})
    assert signed(events, {'events': [photo]})[0].status_code == 200
    media_path = '/api/hiring/wa/agent/media/w3'
    raw = b'\xff\xd8\xff\xe0JPEG'
    assert client.post(media_path, data=raw, content_type='application/octet-stream').status_code == 401
    upload_headers = signer.headers('POST', media_path, raw)
    assert client.post(media_path, data=raw + b'!', content_type='application/octet-stream',
                       headers=upload_headers).status_code == 401
    uploaded = client.post(media_path, data=raw, content_type='application/octet-stream', headers=signer.headers('POST', media_path, raw))
    assert uploaded.status_code == 200 and uploaded.get_json() == {'stored': True}
    assert list(stored.values()) == [(raw, 'image/jpeg')]
    unknown = '/api/hiring/wa/agent/media/missing'
    assert client.post(unknown, data=raw, content_type='application/octet-stream',
                       headers=signer.headers('POST', unknown, raw)).status_code == 404
    shown = client.get('/api/hiring/cards/%s/whatsapp' % card['id'], headers=lead).get_json()['messages']
    assert [m['media']['state'] for m in shown if m['wa_id'] == 'w3'] == ['stored']

    # Фото до 09.11.2026 (шлюз его не качал): подключённый сервис новой версии получает его в ответе на
    # опрос — попросить телефон прислать заново; старый сервис (без features) — нет. Файл — обычным путём.
    old = dict(message, id='w4', kind='image', text='Я увидел вакансию', ts=1792047600 + 800,
               media={'mime': 'image/jpeg', 'size': 5})
    assert signed(events, {'events': [old]})[0].status_code == 200
    connected = {'state': 'connected', 'detail': {'paired': True}}
    assert 'resend' not in signed(poll, connected)[0].get_json()
    asked = signed(poll, dict(connected, features=['resend']))[0]
    assert asked.status_code == 200
    assert asked.get_json()['resend'] == [{'id': 'w4', 'chat': '77055551122@s.whatsapp.net', 'from_me': False}]
    shown = client.get('/api/hiring/cards/%s/whatsapp' % card['id'], headers=lead).get_json()['messages']
    assert [m['media']['state'] for m in shown if m['wa_id'] == 'w4'] == ['loading']
    old_path = '/api/hiring/wa/agent/media/w4'
    resent = client.post(old_path, data=raw, content_type='application/octet-stream', headers=signer.headers('POST', old_path, raw))
    assert resent.status_code == 200 and resent.get_json() == {'stored': True}
    shown = client.get('/api/hiring/cards/%s/whatsapp' % card['id'], headers=lead).get_json()['messages']
    assert [m['media']['state'] for m in shown if m['wa_id'] == 'w4'] == ['stored']
