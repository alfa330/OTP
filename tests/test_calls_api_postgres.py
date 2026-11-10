# -*- coding: utf-8 -*-
"""«Звонки Binotel» для CRM и переключатель режима телефона — настоящим SQL.

CALLS_API_TEST_PORT — порт локального Postgres (своя схема со случайным именем, в конце
удаляется; ни боевых учёток, ни импорта приложения). Запуск:

    CALLS_API_TEST_PORT=55497 python -m pytest tests/test_calls_api_postgres.py

Что сторожится:
* чей звонок: кабинет по companyID (у Тез те же номера линий), посадка на линию в момент звонка
  (пересаженную линию вчерашний звонок помнит за прежним), только «Фронт офисы», без попыток
  обзвона и идущих звонков; пока ID кабинета не узнан — вебхук ждёт сверки;
* событие уходит один раз на звонок и ещё раз только при уточнении; ключу без области и
  отозванному — нет; повтор вебхука и сверка того же звонка — тишина;
* сверка с историей узнаёт кабинет, догоняет пропущенное, смотрит вчера только после полуночи;
* ручки на живой схеме: подписка, проверка приёмника, история с фильтрами, запись;
* режим телефона: переключает только менеджер фронт-офиса на линии удалённого КЦ с включённым
  обзвоном; «phone» — персональное «выключено», «remote_cc» — снова как у отдела.

Люди выдуманы.
"""
import contextlib
import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from binotel.client import RateLimited
from calls_api import queries as calls_queries
from calls_api import routes as calls_routes
from calls_api import rules as calls_rules
from calls_api import schema as calls_schema
from calls_api import service as calls_service
from dial_list import schema as dial_schema
from dial_list import service as dial_service
from partner_keys import keys as partner_keys
from partner_keys import schema as partner_schema

PORT = os.environ.get('CALLS_API_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (CALLS_API_TEST_PORT)')

SZOV, TEZ, FRONT, RCC = 1, 560, 909, 1954
FO1, FO2, FO_OLD, FO_IDLE, SZ, SZ2 = 801, 802, 803, 804, 701, 702
COMPANY = '98190'
UTC = timezone.utc

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, name text NOT NULL, code text,
                              is_active boolean DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, email text, role text,
                        status text NOT NULL DEFAULT 'working', department_id integer,
                        sip_number text);
    CREATE TABLE sip_department_config (department_id integer PRIMARY KEY, provider text,
                                        sip_server text, auto_answer boolean,
                                        auto_answer_delay smallint);
    INSERT INTO departments (id, name, code) VALUES
        (1, 'СЗоВ', 'SZOV'), (560, 'Тез КЦ', 'tez'), (909, 'Фронт офисы', 'front_office'),
        (1954, 'Удаленный КЦ', 'remote_cc');
    INSERT INTO sip_department_config VALUES
        (560, 'binotel', 'sip52.binotel.com', NULL, NULL),
        (1954, 'binotel', 'sip53.binotel.com', NULL, 4);
    INSERT INTO users (id, name, login, email, role, department_id) VALUES
        (801, 'Регионова Алия', 'alia', 'alia@example.kz', 'operator', 909),
        (802, 'Степнов Ерлан', 'erlan', 'Erlan@Example.kz', 'operator', 909),
        (803, 'Прежний Олжас', 'olzhas', 'olzhas@example.kz', 'operator', 909),
        (804, 'Безлинейная Дана', 'dana', 'dana@example.kz', 'operator', 909),
        (701, 'Заботина Сауле', 'saule', 'saule@example.kz', 'operator', 1),
        (702, 'Новый Арман', 'arman', 'arman@example.kz', 'operator', 1);
'''


class _Db:
    def __init__(self, schema):
        self.schema = schema

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                                connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.schema)
            cur.execute("SET statement_timeout TO '10s'")
            cur.execute("SET TIME ZONE 'UTC'")
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

    def get_sip_operator(self, user_id):
        with self._get_cursor() as cur:
            cur.execute("""
                SELECT u.id, u.name, u.department_id, COALESCE(c.provider, 'asterisk')
                FROM users u LEFT JOIN sip_department_config c ON c.department_id = u.department_id
                WHERE u.id = %s""", (int(user_id),))
            row = cur.fetchone()
        if not row:
            return None
        return {'id': row[0], 'name': row[1], 'department_id': row[2], 'department_provider': row[3]}


@pytest.fixture
def db(monkeypatch):
    # Проверка ключа смотрит таблицы в public (partner_keys.schema), а тест — в своей схеме.
    monkeypatch.setattr(partner_schema, 'schema_is_ready', lambda cursor: True)
    schema = 't_calls_api_' + uuid.uuid4().hex
    base = _Db(schema)
    admin = base.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
        partner_schema.init_partner_keys_schema(cur)
        calls_schema.init_calls_api_schema(cur)
        cur.execute("""
            INSERT INTO dial_list_department_settings (department_id, enabled, binotel_company, profile)
            VALUES (1954, TRUE, 'remote_cc', 'remote_cc'), (560, FALSE, 'tez', 'tez')""")
        seats = [
            (FO1, '915', datetime(2026, 11, 1, 4, 0, tzinfo=UTC), None),
            (FO2, '924', datetime(2026, 11, 1, 4, 0, tzinfo=UTC), None),
            (SZ, '928', datetime(2026, 11, 1, 4, 0, tzinfo=UTC), None),
            # Линию 930 пересадили: до 03.11 на ней фронт-офис, потом СЗоВ.
            (FO_OLD, '930', datetime(2026, 10, 1, 4, 0, tzinfo=UTC), datetime(2026, 11, 3, 4, 0, tzinfo=UTC)),
            (SZ2, '930', datetime(2026, 11, 3, 4, 0, tzinfo=UTC), None),
        ]
        for user_id, line, assigned, released in seats:
            cur.execute("""
                INSERT INTO dial_list_line_members (user_id, department_id, internal_number, sip_login,
                                                    sip_password, assigned_at, released_at)
                VALUES (%s, 1954, %s, 'lg' || %s, 'pw', %s, %s)""", (user_id, line, line, assigned, released))
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


def _q(db, sql, params=None):
    with db._get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _key(db, scopes=('binotel_calls',), revoked=False, subscribe=True):
    secret = partner_keys.generate()
    with db._get_cursor() as cur:
        cur.execute("""
            INSERT INTO partner_keys (name, key_hash, key_mark, scopes, otp_role, department_id, revoked_at)
            VALUES ('CRM', %s, %s, %s, 'operator', 1, %s) RETURNING id""",
                    (partner_keys.digest(secret), partner_keys.mark(secret), list(scopes),
                     datetime.now(UTC) if revoked else None))
        key_id = cur.fetchone()[0]
        if subscribe:
            cur.execute("""
                INSERT INTO partner_webhooks (key_id, scope, url, secret, events)
                VALUES (%s, 'binotel_calls', 'https://crm.example/hook', 'secret-0123456789abcdef', '{}')""",
                        (key_id,))
    return key_id, secret


def _outbox(db):
    return _q(db, 'SELECT o.event, o.payload, w.key_id FROM partner_webhook_outbox o '
                  'JOIN partner_webhooks w ON w.id = o.webhook_id ORDER BY o.id')


def _webhook(gid, line, *, company=COMPANY, at=datetime(2026, 11, 5, 5, 0, tzinfo=UTC), disposition='ANSWER',
             billsec=47, waitsec=12, phone='87011234567', call_type='1'):
    return {'requestType': 'apiCallCompleted', 'callDetails[companyID]': company,
            'callDetails[generalCallID]': str(gid), 'callDetails[internalNumber]': line,
            'callDetails[externalNumber]': phone, 'callDetails[callType]': call_type,
            'callDetails[startTime]': str(int(at.timestamp())), 'callDetails[disposition]': disposition,
            'callDetails[billsec]': str(billsec), 'callDetails[waitsec]': str(waitsec),
            'callDetails[recordingStatus]': 'uploaded'}


def _history(gid, line, *, company=COMPANY, at=datetime(2026, 11, 5, 5, 0, tzinfo=UTC), disposition='ANSWER',
             billsec=47, waitsec=12, phone='87011234567'):
    return {'general_call_id': str(gid), 'internal_number': line, 'external_number': phone, 'call_type': 1,
            'disposition': disposition, 'billsec': billsec, 'waitsec': waitsec,
            'start_time': int(at.timestamp()), 'recording_status': 'uploaded', 'company_id': company}


class FakeClient:
    def __init__(self, days=None, record=None):
        self.days = days or {}
        self.record = record
        self.requests = []

    def list_calls_for_day(self, day, max_wait=None):
        self.requests.append(('day', day.isoformat()))
        return list(self.days.get(day.isoformat(), []))

    def get_call_record_url(self, general_call_id, max_wait=None):
        self.requests.append(('record', general_call_id))
        if isinstance(self.record, Exception):
            raise self.record
        return self.record


class FakeDialList:
    def __init__(self, client):
        self.client = client
        self.departments = []

    def client_for_department(self, department_id):
        self.departments.append(department_id)
        return self.client


def _service(db, client=None, now=datetime(2026, 11, 5, 12, 0, tzinfo=calls_rules.ALMATY)):
    fake = FakeDialList(client or FakeClient())
    return calls_service.CallsApiService(db, dial_list=lambda: fake, now=lambda: now), fake


def _learn(db, company=COMPANY):
    with db._get_cursor() as cur:
        calls_queries.set_state(cur, calls_service.COMPANY_KEY, company)


# ── чей звонок ──────────────────────────────────────────────────────────────

def test_webhook_waits_until_the_cabinet_is_known(db):
    _key(db)
    svc, _ = _service(db)
    assert svc.on_binotel_call(_webhook(1001, '915')) is None
    assert _q(db, 'SELECT count(*) FROM calls_api_calls') == [(0,)]


def test_front_office_call_is_saved_once_and_published(db):
    key_id, _ = _key(db)
    _learn(db)
    svc, _ = _service(db)
    assert svc.on_binotel_call(_webhook(1001, '915')) == 'new'
    assert svc.on_binotel_call(_webhook(1001, '915')) == 'same'          # повтор вебхука Binotel
    events = _outbox(db)
    assert [(event, key) for event, _payload, key in events] == [('call.completed', key_id)]
    call = events[0][1]['call']
    assert (call['id'], call['line'], call['phone'], call['result'], call['talk_sec'], call['source']) == \
        ('1001', '915', '+77011234567', 'answered', 47, 'webhook')
    assert call['operator'] == {'id': FO1, 'name': 'Регионова Алия', 'email': 'alia@example.kz',
                                'department': 'Фронт офисы'}
    assert call['started_at'] == '2026-11-05T10:00:00+05:00'
    # Уточнение (разговор длиннее) — второе событие, тот же id.
    assert svc.on_binotel_call(_webhook(1001, '915', billsec=50)) == 'changed'
    assert [payload['call']['talk_sec'] for _e, payload, _k in _outbox(db)] == [47, 50]


def test_other_calls_are_not_ours(db):
    _key(db)
    _learn(db)
    svc, _ = _service(db)
    assert svc.on_binotel_call(_webhook(2001, '924', company='77777')) is None   # Тез: тот же номер линии
    assert svc.on_binotel_call(_webhook(2002, '928')) is None                    # линия СЗоВ
    assert svc.on_binotel_call(_webhook(2003, '999')) is None                    # на линии никого
    assert svc.on_binotel_call(_webhook(2004, '915'), matched=True) is None      # попытка обзвона
    assert svc.on_binotel_call(_webhook(2005, '915', disposition='ONLINE')) is None
    # Линия до посадки менеджера — не его звонок.
    assert svc.on_binotel_call(_webhook(2006, '915', at=datetime(2026, 10, 31, 5, 0, tzinfo=UTC))) is None
    # Попытка обзвона, о которой вебхук не сказал (matched False), — по таблице попыток.
    with db._get_cursor() as cur:
        cur.execute("INSERT INTO dial_list_leads (department_id, phone_norm) VALUES (1954, '7011234567') "
                    "RETURNING id")
        lead_id = cur.fetchone()[0]
        cur.execute("INSERT INTO dial_list_portions (department_id, operator_id, size) VALUES (1954, %s, 1) "
                    "RETURNING id", (FO1,))
        portion_id = cur.fetchone()[0]
        cur.execute("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position) "
                    "VALUES (%s, %s, %s, 1) RETURNING id", (portion_id, lead_id, FO1))
        assignment_id = cur.fetchone()[0]
        cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, general_call_id) "
                    "VALUES (%s, %s, '2007')", (assignment_id, FO1))
    assert svc.on_binotel_call(_webhook(2007, '915')) is None
    assert _q(db, 'SELECT count(*) FROM calls_api_calls') == [(0,)]
    assert _outbox(db) == []


def test_reseated_line_remembers_who_called(db):
    _key(db)
    _learn(db)
    svc, _ = _service(db)
    assert svc.on_binotel_call(_webhook(3001, '930', at=datetime(2026, 11, 2, 5, 0, tzinfo=UTC))) == 'new'
    assert svc.on_binotel_call(_webhook(3002, '930', at=datetime(2026, 11, 4, 5, 0, tzinfo=UTC))) is None
    assert _q(db, 'SELECT general_call_id, user_id FROM calls_api_calls') == [('3001', FO_OLD)]


def test_released_line_is_nobodys(db):
    """Менеджера сняли с линии и никого не посадили: звонки после снятия — не его, до — его."""
    _key(db)
    _learn(db)
    _q(db, "UPDATE dial_list_line_members SET released_at = '2026-11-04T04:00:00+00' WHERE user_id = %s", (FO2,))
    client = FakeClient(days={'2026-11-05': [_history(9101, '924', at=datetime(2026, 11, 5, 4, 0, tzinfo=UTC))]})
    svc, _ = _service(db, client)
    assert svc.on_binotel_call(_webhook(9102, '924', at=datetime(2026, 11, 5, 5, 0, tzinfo=UTC))) is None
    assert svc.sync_history() == {'calls': 1, 'saved': 0}
    assert svc.on_binotel_call(_webhook(9103, '924', at=datetime(2026, 11, 3, 5, 0, tzinfo=UTC))) == 'new'
    assert _q(db, 'SELECT general_call_id, user_id FROM calls_api_calls') == [('9103', FO2)]


def test_record_saves_only_the_scope(db):
    _learn(db)
    svc, _ = _service(db)
    call = calls_rules.from_webhook(_webhook(9201, '928'))
    with db._get_cursor() as cur:
        seat = calls_queries.seat_at(cur, calls_rules.COMPANY, '928', call['started_at'])
        assert seat['user_id'] == SZ
        assert svc.record(cur, call, seat, 'webhook') is None
        assert svc.record(cur, call, None, 'webhook') is None
    assert _q(db, 'SELECT count(*) FROM calls_api_calls') == [(0,)]


def test_keys_without_the_door_get_nothing(db):
    _key(db, scopes=('olx_chats',))
    _key(db, revoked=True)
    _learn(db)
    svc, _ = _service(db)
    assert svc.on_binotel_call(_webhook(4001, '915')) == 'new'
    assert _outbox(db) == []                                    # звонок сохранён, событий некому


def test_the_real_webhook_path_reaches_the_crm(db):
    """Наблюдатель висит на настоящем handle_webhook: форма Binotel и JSON с callDetails."""
    _key(db)
    _learn(db)
    svc, _ = _service(db)
    dial = dial_service.DialListService(db)
    dial.call_observers.append(svc.on_binotel_call)
    dial.handle_webhook(_webhook(8001, '915'), '194.88.218.116')
    nested = {key.replace('callDetails[', '').rstrip(']'): value
              for key, value in _webhook(8002, '924').items() if key.startswith('callDetails[')}
    dial.handle_webhook({'requestType': 'apiCallCompleted', 'callDetails': nested}, '194.88.218.116')
    assert _q(db, 'SELECT general_call_id, user_id FROM calls_api_calls ORDER BY 1') == [('8001', FO1), ('8002', FO2)]
    assert [payload['call']['id'] for _e, payload, _k in _outbox(db)] == ['8001', '8002']


# ── сверка с историей ───────────────────────────────────────────────────────

def test_sync_learns_the_cabinet_and_catches_up(db):
    key_id, _ = _key(db)
    client = FakeClient(days={'2026-11-05': [
        _history(5001, '915'), _history(5002, '924', disposition='NOANSWER', billsec=0, waitsec=30),
        _history(5003, '928'), _history(5004, '915', disposition='ONLINE', billsec=0),
    ]})
    svc, fake = _service(db, client)
    assert svc.sync_history() == {'calls': 4, 'saved': 2}
    assert client.requests == [('day', '2026-11-05')]           # полдень — вчера не спрашиваем
    assert fake.departments == [RCC]
    assert svc.company_id() == COMPANY
    assert _q(db, 'SELECT general_call_id, user_id, result, source FROM calls_api_calls ORDER BY 1') == [
        ('5001', FO1, 'answered', 'history'), ('5002', FO2, 'no_answer', 'history')]
    assert len(_outbox(db)) == 2
    # Тот же звонок вебхуком (Binotel повторил) и повторная сверка — без новых событий.
    assert svc.on_binotel_call(_webhook(5001, '915')) == 'same'
    assert svc.sync_history() == {'calls': 4, 'saved': 0}
    assert [k for _e, _p, k in _outbox(db)] == [key_id, key_id]


def test_sync_follows_seat_changes_within_the_day(db):
    """Посреди дня одного менеджера сняли с линии 924, другого посадили на 940: сверка отдаёт
    звонок тому, кто сидел на линии в его момент, и не отдаёт звонки «до» и «после»."""
    _q(db, "UPDATE dial_list_line_members SET released_at = '2026-11-05T06:00:00+00' WHERE user_id = %s", (FO2,))
    _q(db, "INSERT INTO dial_list_line_members (user_id, department_id, internal_number, assigned_at) "
           "VALUES (%s, 1954, '940', '2026-11-05T06:00:00+00')", (FO_IDLE,))
    at = lambda hour: datetime(2026, 11, 5, hour, 0, tzinfo=UTC)  # noqa: E731
    client = FakeClient(days={'2026-11-05': [
        _history(9301, '924', at=at(5)), _history(9302, '924', at=at(7)),
        _history(9303, '940', at=at(5)), _history(9304, '940', at=at(7)),
    ]})
    svc, _ = _service(db, client)
    assert svc.sync_history() == {'calls': 4, 'saved': 2}
    assert _q(db, 'SELECT general_call_id, user_id FROM calls_api_calls ORDER BY 1') == [
        ('9301', FO2), ('9304', FO_IDLE)]


def test_sync_finds_the_cabinet_in_quiet_days(db):
    """Сегодня в кабинете тихо (так было 05.11.2026): ID ищется в прошлых сутках, один раз, и
    старые звонки при этом в CRM не уходят; узнанный ID следующая сверка уже не ищет."""
    _key(db)
    client = FakeClient(days={'2026-11-03': [_history(9401, '915', at=datetime(2026, 11, 3, 5, 0, tzinfo=UTC))]})
    svc, _ = _service(db, client)
    assert svc.sync_history() == {'calls': 0, 'saved': 0}
    assert client.requests == [('day', '2026-11-05'), ('day', '2026-11-04'), ('day', '2026-11-03')]
    assert svc.company_id() == COMPANY
    assert _q(db, 'SELECT count(*) FROM calls_api_calls') == [(0,)] and _outbox(db) == []
    client.requests.clear()
    svc.sync_history()
    assert client.requests == [('day', '2026-11-05')]
    # Теперь первый же вебхук — сразу событие.
    assert svc.on_binotel_call(_webhook(9402, '915')) == 'new'


def test_sync_gives_up_after_a_week_of_silence(db):
    client = FakeClient()
    svc, _ = _service(db, client)
    assert svc.sync_history() == {'calls': 0, 'saved': 0}
    assert len(client.requests) == 1 + calls_service.LEARN_LOOKBACK_DAYS
    assert svc.company_id() is None


def test_sync_looks_at_yesterday_only_after_midnight(db):
    client = FakeClient(days={'2026-11-04': [_history(6001, '915', at=datetime(2026, 11, 4, 18, 58, tzinfo=UTC))]})
    svc, _ = _service(db, client, now=datetime(2026, 11, 5, 0, 10, tzinfo=calls_rules.ALMATY))
    assert svc.sync_history() == {'calls': 1, 'saved': 1}
    assert client.requests == [('day', '2026-11-04'), ('day', '2026-11-05')]


# ── ручки ───────────────────────────────────────────────────────────────────

def _client(db, svc):
    from flask import Flask
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(calls_routes.build_calls_api_blueprint(db=db, service=svc, environ={}))
    return app.test_client()


def test_routes_on_a_live_schema(db):
    _learn(db)
    key_id, secret = _key(db, subscribe=False)
    client = FakeClient(record='https://records.example/5001.mp3')
    svc, fake = _service(db, client)
    http = _client(db, svc)
    headers = {partner_keys.HEADER: secret}
    # Подписка: секрет выпускаем мы и показываем один раз.
    response = http.put('/api/calls/webhook', json={'url': 'https://crm.example/calls'}, headers=headers)
    assert response.status_code == 200 and len(response.get_json()['secret']) >= 32
    assert http.put('/api/calls/webhook', json={'url': 'http://crm.example/calls'}, headers=headers).status_code == 400
    assert http.put('/api/calls/webhook', json={'url': 'https://crm.example/calls', 'events': ['call.started']},
                    headers=headers).status_code == 400
    assert http.get('/api/calls/webhook', headers=headers).get_json()['subscription']['events'] == ['call.completed']
    assert http.post('/api/calls/webhook/test', headers=headers).get_json() == {'status': 'queued', 'event': 'ping'}
    assert [event for event, _p, _k in _outbox(db)] == ['ping']
    assert http.get('/api/calls/webhook/deliveries', headers=headers).get_json()['deliveries'][0]['event'] == 'ping'
    # Звонки и история.
    assert svc.on_binotel_call(_webhook(7001, '915')) == 'new'
    assert svc.on_binotel_call(_webhook(7002, '924', disposition='BUSY', billsec=0, waitsec=3,
                                        at=datetime(2026, 11, 5, 6, 0, tzinfo=UTC))) == 'new'
    listing = http.get('/api/calls', headers=headers).get_json()
    assert [c['id'] for c in listing['calls']] == ['7002', '7001'] and listing['pagination']['total'] == 2
    assert [c['id'] for c in http.get('/api/calls?result=busy', headers=headers).get_json()['calls']] == ['7002']
    assert [c['id'] for c in http.get('/api/calls?operator=erlan@example.kz',
                                      headers=headers).get_json()['calls']] == ['7002']
    assert [c['id'] for c in http.get('/api/calls?operator=%d' % FO1, headers=headers).get_json()['calls']] == ['7001']
    assert [c['id'] for c in http.get('/api/calls?phone=+77011234567&from=2026-11-05T10:30:00',
                                      headers=headers).get_json()['calls']] == ['7002']
    assert http.get('/api/calls/7001', headers=headers).get_json()['call']['operator']['id'] == FO1
    assert http.get('/api/calls/7999', headers=headers).status_code == 404
    # Запись: только у разговора, ключом кабинета линии.
    assert http.get('/api/calls/7001/recording', headers=headers).get_json() == \
        {'id': '7001', 'url': 'https://records.example/5001.mp3', 'talk_sec': 47}
    assert fake.departments == [RCC]
    assert http.get('/api/calls/7002/recording', headers=headers).get_json()['code'] == 'CALLS_API_NO_RECORDING'
    client.record = None
    assert http.get('/api/calls/7001/recording', headers=headers).status_code == 409
    client.record = RateLimited('too frequent')
    assert http.get('/api/calls/7001/recording', headers=headers).status_code == 503
    # Журнал вызовов ключа ведётся.
    assert _q(db, "SELECT count(*) FROM partner_key_calls WHERE key_id = %s AND scope = 'binotel_calls'",
              (key_id,))[0][0] >= 8
    assert http.delete('/api/calls/webhook', headers=headers).get_json() == {'status': 'deleted'}


def test_receiver_check_with_an_explicit_event_list(db):
    """Как подписалась CRM 05.11.2026: events=["call.completed"] — проверка приёмника всё равно
    уходит (раньше 409), а события подписка получает только свои; выключенная — 409, как было."""
    _learn(db)
    _key_id, secret = _key(db, subscribe=False)
    svc, _ = _service(db)
    http = _client(db, svc)
    headers = {partner_keys.HEADER: secret}
    response = http.put('/api/calls/webhook', json={'url': 'https://crm.example/calls', 'events': ['call.completed']},
                        headers=headers)
    assert response.status_code == 200 and response.get_json()['subscription']['events'] == ['call.completed']
    assert http.post('/api/calls/webhook/test', headers=headers).get_json() == {'status': 'queued', 'event': 'ping'}
    assert svc.on_binotel_call(_webhook(9501, '915')) == 'new'
    assert [event for event, _p, _k in _outbox(db)] == ['ping', 'call.completed']
    _q(db, 'UPDATE partner_webhooks SET enabled = FALSE')
    response = http.post('/api/calls/webhook/test', headers=headers)
    assert (response.status_code, response.get_json()['code']) == (409, 'CALLS_API_SUBSCRIPTION_DISABLED')


def test_operators_show_lines_and_phone_mode(db):
    svc, _ = _service(db)
    _q(db, 'INSERT INTO dial_list_user_settings (user_id, enabled) VALUES (%s, FALSE)', (FO2,))
    assert [(o['id'], o['line'], o['phone_mode'], o['email']) for o in svc.operators()] == [
        (FO1, '915', 'remote_cc', 'alia@example.kz'), (FO2, '924', 'phone', 'Erlan@Example.kz')]


# ── режим телефона ──────────────────────────────────────────────────────────

def test_front_office_switches_the_phone_mode(db):
    svc = dial_service.DialListService(db)
    block = svc.phone_settings(FO1)
    assert (block['enabled'], block['switchable']) == (True, True)
    block = svc.set_phone_mode(FO1, 'phone')
    assert (block['enabled'], block['switchable']) == (False, True)
    assert svc.user_setting(FO1) is False
    block = svc.set_phone_mode(FO1, 'remote_cc')
    assert (block['enabled'], block['switchable']) == (True, True)
    assert svc.user_setting(FO1) is None                        # снова «как у отдела»
    with pytest.raises(dial_service.DialListError):
        svc.set_phone_mode(FO1, 'auto')


def test_only_front_office_on_an_enabled_line_switches(db):
    svc = dial_service.DialListService(db)
    assert svc.phone_settings(SZ)['switchable'] is False        # СЗоВ на линии — обзвон как был
    with pytest.raises(dial_service.DialListError) as raised:
        svc.set_phone_mode(SZ, 'phone')
    assert raised.value.status == 403
    with pytest.raises(dial_service.DialListError) as raised:
        svc.set_phone_mode(FO_IDLE, 'phone')                    # без линии удалённого КЦ
    assert raised.value.status == 403
    assert svc.user_setting(SZ) is None and svc.user_setting(FO_IDLE) is None
    # Отдел линии выключил обзвон — переключать нечего.
    _q(db, 'UPDATE dial_list_department_settings SET enabled = FALSE WHERE department_id = 1954')
    assert svc.phone_settings(FO1) == dict(svc.phone_settings(FO1), enabled=False, switchable=False)


def test_oktell_session_closes_only_for_szov_on_dial_out(db, monkeypatch):
    """Владелец 10.11.2026: оператор СЗоВ подключается к обзвону через телефон — сессию Oktell
    на его компьютере закрыть. Флаг уходит телефону в блоке dial_list."""
    monkeypatch.delenv(dial_service.OKTELL_LOGOUT_ENV, raising=False)
    svc = dial_service.DialListService(db)
    assert svc.phone_settings(SZ)['oktell_logout'] is True
    assert svc.phone_settings(FO1)['oktell_logout'] is False       # фронт-офис в Oktell не работает
    assert 'oktell_logout' not in svc.phone_settings(FO_IDLE)      # без линии обзвона нет вовсе
    # Обзвон лично выключен — человек к обзвону не подключается, Oktell не трогаем.
    svc.save_user_setting(SZ, False)
    assert svc.phone_settings(SZ)['oktell_logout'] is False
    svc.save_user_setting(SZ, None)
    assert svc.phone_settings(SZ)['oktell_logout'] is True
    # Рубильник на сервере.
    monkeypatch.setenv(dial_service.OKTELL_LOGOUT_ENV, '0')
    assert svc.phone_settings(SZ)['oktell_logout'] is False
    monkeypatch.delenv(dial_service.OKTELL_LOGOUT_ENV)
    # Отдел линии выключил обзвон — закрывать Oktell незачем.
    _q(db, 'UPDATE dial_list_department_settings SET enabled = FALSE WHERE department_id = 1954')
    assert svc.phone_settings(SZ)['oktell_logout'] is False


def test_mode_switch_route(db):
    from flask import Flask, jsonify
    from dial_list.routes import build_dial_list_blueprint

    requester = {'id': FO1}

    def resolve_requester():
        return requester['id'], (requester['id'], 'x', 'x', 'operator', None, None, None, 'login'), None

    app = Flask(__name__)
    app.config['TESTING'] = True
    bp = build_dial_list_blueprint(
        db=db, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: (jsonify({}), 200),
        resolve_requester=resolve_requester, is_admin_role=lambda role: False,
        headed_department_ids=lambda user_id: [], service=dial_service.DialListService(db))
    app.register_blueprint(bp)
    http = app.test_client()
    response = http.post('/api/operator/dial_list/mode', json={'mode': 'phone'})
    assert response.status_code == 200
    assert (response.get_json()['dial_list']['enabled'], response.get_json()['dial_list']['switchable']) == \
        (False, True)
    requester['id'] = SZ
    response = http.post('/api/operator/dial_list/mode', json={'mode': 'phone'})
    assert response.status_code == 403 and 'фронт-офиса' in response.get_json()['error']
