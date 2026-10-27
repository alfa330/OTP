# -*- coding: utf-8 -*-
"""«Аренда авто» — ночной отчёт (#398) на настоящем Postgres: RENTAL_TEST_PORT — порт кластера.

Чистые правила и клиенты источников проверяет tests/test_rental.py. Здесь — весь процесс на
схеме раздела: сбор слота → строки с «Состоянием» по адресу → ручная правка с журналом →
«Обновить» (ручное остаётся, подставленное считается заново, пропавшая машина уходит, а с
ручным состоянием — остаётся) → пересбор при молчащей GPS-системе прежние данные не трогает →
ручки раздела: права, экран, Excel, история не пересобирается.

Источники — подделка (без сети), нить сбора — синхронная. Только 127.0.0.1, своя схема со
случайным именем, в конце она удаляется. Люди выдуманы.

    RENTAL_TEST_PORT=55432 python -m pytest tests/test_rental_postgres.py
"""

import contextlib
import os
import uuid
from datetime import datetime, timedelta
from io import BytesIO

import pytest

from rental import queries, rules, schema
from rental.service import Busy, RentalService

PORT = os.environ.get('RENTAL_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (RENTAL_TEST_PORT)')

SUPER, HEAD_OOZ, NIGHT_OP, STRANGER, FIRED = 1, 20, 500, 600, 700
NOW_TS = 1793124000.0   # 28.10.2026 04:00 по Алматы
SLOT = datetime(2026, 10, 28, 1)

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text UNIQUE, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL,
                        status text NOT NULL DEFAULT 'working', department_id integer);
    INSERT INTO departments (id, code, name, head_user_id) VALUES
        (2008, 'request_processing_department', 'ООЗ', 20), (1, 'szov', 'СЗоВ', NULL);
    INSERT INTO users (id, name, role, status, department_id) VALUES
        (1, 'Супер Админов', 'super_admin', 'working', 1),
        (20, 'Глава Оозова', 'admin', 'working', 2008),
        (500, 'Ночной Оператор', 'operator', 'working', 2008),
        (600, 'Чужой Оператор', 'operator', 'working', 1),
        (700, 'Уволенный Ночной', 'operator', 'fired', 2008);
'''


class _Db:
    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user=os.environ.get('RENTAL_TEST_USER', 'postgres'),
                                dbname='postgres', connect_timeout=3)
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


@pytest.fixture
def db():
    name = 't_rental_' + uuid.uuid4().hex
    base = _Db(name)
    admin = base.connect()
    try:
        with admin.cursor() as cur:
            cur.execute('CREATE SCHEMA ' + name)
            cur.execute('SET search_path TO ' + name)
            cur.execute(BASE_DDL)
            schema.init_rental_schema(cur)
            # Повторный запуск — как каждый рестарт прода: идемпотентно.
            schema.init_rental_schema(cur)
        admin.commit()
    except Exception:
        admin.rollback()
        admin.close()
        raise
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA %s CASCADE' % name)
        admin.commit()
        admin.close()


def _row(plate, *, site='Wialon', gps='online', address=None, rent='no', in_fleet=False, signal_ts=NOW_TS - 60):
    state = rules.state_by_address(address)
    return {'plate': plate, 'gps_site': site, 'gps_sites': [site] if site else [], 'gps_status': gps,
            'signal_ts': signal_ts, 'address': address, 'lat': 43.24, 'lon': 76.85, 'rent_status': rent,
            'in_fleet': in_fleet, 'model': 'Hongqi E-QM5',
            'state': state, 'state_source': rules.STATE_BY_ADDRESS if state else None}


ALL_OK = {name: {'ok': True, 'count': 1, 'error': None} for name in ('yume', 'wialon', 'glonass', 'efir')}


class Collector:
    """Подделка collect.collect: отдаёт заранее заданный срез."""

    def __init__(self):
        self.result = {'rows': [], 'sources': dict(ALL_OK), 'collected_ts': NOW_TS}
        self.calls = 0

    def __call__(self, _config):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def env(db):
    collector = Collector()
    clock = {'now': datetime(2026, 10, 28, 4)}
    service = RentalService(cursor_factory=db._get_cursor, config_loader=lambda: {}, collector=collector,
                            now=lambda: clock['now'], start_thread=lambda target: target())
    return {'db': db, 'collector': collector, 'service': service, 'clock': clock}


def _rows(db, report_id):
    with db._get_cursor() as cur:
        return {row['plate']: row for row in queries.rows_for_report(cur, report_id)}


def _report(db, report_id):
    with db._get_cursor() as cur:
        return queries.get_report(cur, report_id)


def test_first_collection_writes_rows_with_state_by_address(env):
    env['collector'].result['rows'] = [
        _row('111AAA02', address='Утеген Батыра ул., 15А, Алматы', rent='no', in_fleet=True),
        _row('222BBB02', address='15/1 улица Утеген Батыра, Алматы', rent='no', in_fleet=True),
        _row('333CCC02', site=None, gps='none', rent='in_rent', in_fleet=True, signal_ts=None),
    ]
    report = env['service'].start(rules.KIND_NIGHT, SLOT, by=HEAD_OOZ)
    stored = _report(env['db'], report['id'])
    assert stored['status'] == 'ready'
    assert stored['collected_at'] == rules.almaty_from_ts(NOW_TS)
    assert stored['sources']['wialon']['ok'] is True
    rows = _rows(env['db'], report['id'])
    assert (rows['111AAA02']['state'], rows['111AAA02']['state_source']) == ('base', 'address')
    assert rows['222BBB02']['state'] == 'repair'
    assert rows['333CCC02']['gps_status'] == 'none' and rows['333CCC02']['signal_at'] is None
    # Отчёт слота один: второй сбор того же слота — тот же отчёт, а не новый.
    again = env['service'].start(rules.KIND_NIGHT, SLOT, by=HEAD_OOZ)
    assert again['id'] == report['id']


def test_manual_state_survives_refresh_and_is_logged(env):
    service, db = env['service'], env['db']
    env['collector'].result['rows'] = [
        _row('111AAA02', address='Абая 10', in_fleet=True),
        _row('222BBB02', address='Утеген Батыра ул., 15А'),
        _row('444DDD02', address='Абая 20'),
        _row('555EEE02', address='Абая 30'),
    ]
    report = service.start(rules.KIND_NIGHT, SLOT)
    rows = _rows(db, report['id'])
    ctx = service.context(NIGHT_OP)
    assert service.set_state(ctx, rows['111AAA02']['id'], 'sale')[0] == 'ok'
    assert service.set_state(ctx, rows['111AAA02']['id'], 'sale')[0] == 'ok'   # повтор — без второй записи
    assert service.set_state(ctx, rows['555EEE02']['id'], 'repair')[0] == 'ok'
    assert service.set_state(ctx, rows['222BBB02']['id'], 'bogus')[0] == 'bad_state'
    with db._get_cursor() as cur:
        cur.execute('SELECT plate, old_state, new_state, changed_by_name FROM rental_night_state_log ORDER BY id')
        log = cur.fetchall()
    assert log == [('111AAA02', None, 'sale', 'Ночной Оператор'), ('555EEE02', None, 'repair', 'Ночной Оператор')]

    # «Обновить»: машина 111 переехала на базу — ручное «На продаже» остаётся; 222 уехала с базы —
    # подставленное «На базе» снимается; 444 пропала — уходит; 555 пропала, но с ручным — остаётся.
    env['collector'].result['rows'] = [
        _row('111AAA02', address='Утеген Батыра ул., 15А', in_fleet=True),
        _row('222BBB02', address='Абая 99'),
    ]
    service.start(rules.KIND_NIGHT, SLOT)
    rows = _rows(db, report['id'])
    assert set(rows) == {'111AAA02', '222BBB02', '555EEE02'}
    assert (rows['111AAA02']['state'], rows['111AAA02']['state_source']) == ('sale', 'manual')
    assert rows['111AAA02']['address'] == 'Утеген Батыра ул., 15А'
    assert rows['222BBB02']['state'] is None
    assert rows['555EEE02']['state'] == 'repair'


def test_refresh_with_silent_gps_keeps_previous_rows(env):
    service, db = env['service'], env['db']
    env['collector'].result['rows'] = [_row('111AAA02'), _row('222BBB02')]
    report = service.start(rules.KIND_NIGHT, SLOT)
    env['collector'].result = {'rows': [_row('111AAA02')], 'collected_ts': NOW_TS + 600,
                               'sources': dict(ALL_OK, wialon={'ok': False, 'count': 0, 'error': 'вход не удался'})}
    service.start(rules.KIND_NIGHT, SLOT)
    stored = _report(db, report['id'])
    assert stored['status'] == 'ready'
    assert 'Wialon' in stored['error']
    assert stored['sources']['wialon']['ok'] is True          # список источников — прежнего сбора
    assert stored['collected_at'] == rules.almaty_from_ts(NOW_TS)
    assert set(_rows(db, report['id'])) == {'111AAA02', '222BBB02'}


def test_first_collection_with_nothing_answered_fails(env):
    env['collector'].result = {'rows': [], 'collected_ts': NOW_TS,
                               'sources': {n: {'ok': False, 'count': 0, 'error': 'x'} for n in ALL_OK}}
    report = env['service'].start(rules.KIND_MORNING, datetime(2026, 10, 28, 7))
    stored = _report(env['db'], report['id'])
    assert stored['status'] == 'failed' and 'не ответили' in stored['error']


def test_collector_crash_marks_report_failed(env):
    env['collector'].result = RuntimeError('boom')
    report = env['service'].start(rules.KIND_NIGHT, SLOT)
    assert _report(env['db'], report['id'])['status'] == 'failed'
    assert env['service'].running() is None


def test_busy_and_stale_collecting(env):
    service, db = env['service'], env['db']
    service._running = 999
    with pytest.raises(Busy):
        service.start(rules.KIND_NIGHT, SLOT)
    service._running = None
    with db._get_cursor() as cur:
        report, busy = queries.claim_report(cur, rules.KIND_NIGHT, SLOT, None, datetime(2026, 10, 28, 1))
    assert not busy
    with db._get_cursor() as cur:
        _same, busy = queries.claim_report(cur, rules.KIND_NIGHT, SLOT, None, datetime(2026, 10, 28, 1, 1))
    assert busy                                    # уже собирается — второй сбор не начнётся
    with db._get_cursor() as cur:
        queries.expire_stale(cur, datetime(2026, 10, 28, 1, 20))
    stored = _report(db, report['id'])
    assert stored['status'] == 'failed' and 'прервался' in stored['error']


def test_access_context_from_department_and_headship(env):
    service = env['service']
    assert service.context(HEAD_OOZ)['headed_codes'] == ['request_processing_department']
    assert service.context(NIGHT_OP)['department_code'] == 'request_processing_department'
    assert service.context(12345) is None


# ─── Ручки ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(env, monkeypatch):
    from flask import Flask, jsonify, request

    from rental.routes import build_rental_blueprint

    monkeypatch.setattr(queries, 'now_almaty', lambda: env['clock']['now'])
    app = Flask(__name__)
    app.register_blueprint(build_rental_blueprint(
        db=env['db'],
        require_api_key=lambda handler: handler,
        build_cors_preflight_response=lambda: jsonify({}),
        resolve_requester=lambda: (int(request.headers['X-User-Id']), {}, None),
        service=env['service'],
    ))
    return app.test_client()


def _as(user_id):
    return {'X-User-Id': str(user_id)}


def test_section_is_closed_to_others_and_fired(client):
    assert client.get('/api/rental', headers=_as(STRANGER)).status_code == 403
    assert client.get('/api/rental', headers=_as(FIRED)).status_code == 403
    assert client.get('/api/rental', headers=_as(HEAD_OOZ)).status_code == 200


def test_screen_state_export_and_locked_history(client, env):
    env['collector'].result['rows'] = [_row('111AAA02', address='Абая 10', in_fleet=True),
                                       _row('222BBB02', address='Утеген Батыра ул., 15А')]
    # 04:00 — окно ночного слота: пропущенный отчёт можно собрать кнопкой.
    screen = client.get('/api/rental', headers=_as(NIGHT_OP)).get_json()
    assert screen['report'] is None and screen['slot']['can_collect'] is True
    assert screen['slot']['kind_label'] == '01:00'
    created = client.post('/api/rental/reports', headers=_as(NIGHT_OP))
    assert created.status_code == 202
    assert client.post('/api/rental/reports', headers=_as(NIGHT_OP)).status_code == 409   # уже есть

    screen = client.get('/api/rental', headers=_as(NIGHT_OP)).get_json()
    assert screen['report']['shows_rent'] is True
    rows = {row['plate']: row for row in screen['rows']}
    assert rows['111AAA02']['needs_check'] is True
    changed = client.put('/api/rental/rows/%d/state' % rows['111AAA02']['id'], json={'state': 'base'},
                         headers=_as(NIGHT_OP)).get_json()['row']
    assert (changed['state'], changed['state_source'], changed['state_by_name']) == ('base', 'manual', 'Ночной Оператор')
    assert changed['needs_check'] is False

    exported = client.get('/api/rental/reports/%d/export' % screen['report']['id'], headers=_as(HEAD_OOZ))
    assert exported.status_code == 200
    assert "filename*=UTF-8''" in exported.headers['Content-Disposition']
    from openpyxl import load_workbook
    sheet = load_workbook(BytesIO(exported.data))['Отчёт']
    assert [c.value for c in sheet[1]][2] == 'Статус аренды'

    # 09:00: отчёт за 01:00 стал историей — пересобрать его нельзя; а пропущенный за 07:00 ещё можно.
    env['clock']['now'] = datetime(2026, 10, 28, 9)
    refused = client.post('/api/rental/reports/%d/refresh' % screen['report']['id'], headers=_as(NIGHT_OP))
    assert refused.status_code == 409 and refused.get_json()['code'] == 'RENTAL_HISTORY_LOCKED'
    morning = client.get('/api/rental', headers=_as(NIGHT_OP)).get_json()
    assert morning['slot']['kind_label'] == '07:00' and morning['slot']['can_collect'] is True
    # 14:00 — окно утреннего слота закрыто: кнопки «Собрать» у ночной смены нет.
    env['clock']['now'] = datetime(2026, 10, 28, 14)
    late = client.get('/api/rental', headers=_as(NIGHT_OP)).get_json()
    assert late['slot']['kind_label'] == '07:00' and late['slot']['can_collect'] is False
    # …а супер-админ может собрать отчёт текущего слота вне окна (проверка после выкладки).
    assert client.get('/api/rental', headers=_as(SUPER)).get_json()['slot']['can_collect'] is True
