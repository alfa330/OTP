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
from datetime import datetime
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
    groups = client.post('/api/hiring/groups', json={'name': 'Группа 2', 'start_date': '2026-11-12', 'trainer_id': TRAINER}, headers=lead)
    assert groups.status_code == 201 and groups.get_json()['items'][0]['start_date'] == '2026-11-12'
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


def test_channels_through_routes(client):
    """Каналы — метки откликов с цветом (владелец 09.11.2026): справочник руководителя и метка карточки ручками."""
    lead, rec = as_user(HR_HEAD), as_user(REC1)
    boot = client.get('/api/hiring', headers=rec).get_json()
    assert [(c['name'], c['color']) for c in boot['dictionaries']['channels']] == [('Таргет', 'blue'), ('hh', 'red'), ('Каспи', 'pink')]
    added = client.post('/api/hiring/channels', json={'name': 'OLX', 'color': 'green'}, headers=lead)
    assert added.status_code == 201 and added.get_json()['items'][-1]['name'] == 'OLX'
    olx = added.get_json()['items'][-1]
    assert client.post('/api/hiring/channels', json={'name': 'olx', 'color': 'green'}, headers=lead).status_code == 409
    assert client.post('/api/hiring/channels', json={'name': 'X', 'color': 'black'}, headers=lead).get_json()['code'] == 'HIRING_BAD_CHANNEL'
    assert client.post('/api/hiring/channels', json={'name': 'X', 'color': 'red'}, headers=rec).status_code == 403
    assert client.patch('/api/hiring/channels/%s' % olx['id'], json={'color': 'teal'}, headers=rec).status_code == 403
    off = client.patch('/api/hiring/channels/%s' % olx['id'], json={'is_active': False}, headers=lead).get_json()['items']
    assert [c['is_active'] for c in off if c['id'] == olx['id']] == [False]
    # Рекрутер видит в выборе только включённые; руководитель — все (выключенные нужны настройкам).
    assert 'OLX' not in [c['name'] for c in client.get('/api/hiring', headers=rec).get_json()['dictionaries']['channels']]
    assert 'OLX' in [c['name'] for c in client.get('/api/hiring', headers=lead).get_json()['dictionaries']['channels']]

    manual = next(s for s in boot['dictionaries']['sources'] if s['code'] == 'manual')
    card = client.post('/api/hiring/cards', json={'phone': '8 705 321 00 01', 'source_id': manual['id']}, headers=rec).get_json()['card']
    assert card['channel_id'] is None and client.post('/api/hiring/cards/%s/take' % card['id'], headers=rec).status_code == 200
    target = boot['dictionaries']['channels'][0]
    labeled = client.patch('/api/hiring/cards/%s' % card['id'], json={'channel_id': target['id']}, headers=rec)
    assert labeled.status_code == 200
    assert (labeled.get_json()['card']['channel_name'], labeled.get_json()['card']['channel_color']) == ('Таргет', 'blue')
    refused = client.patch('/api/hiring/cards/%s' % card['id'], json={'channel_id': olx['id']}, headers=rec)
    assert refused.status_code == 400 and refused.get_json()['code'] == 'HIRING_BAD_CHANNEL'
    in_work = next(column for column in client.get('/api/hiring/board', headers=rec).get_json()['columns'] if column['stage'] == 'in_work')
    assert [(c['channel_name'], c['channel_color']) for c in in_work['cards']] == [('Таргет', 'blue')]
    assert client.get('/api/hiring/cards/%s' % card['id'], headers=rec).get_json()['card']['channel_name'] == 'Таргет'
    assert client.patch('/api/hiring/cards/%s' % card['id'], json={'channel_id': None}, headers=rec).get_json()['card']['channel_id'] is None


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
