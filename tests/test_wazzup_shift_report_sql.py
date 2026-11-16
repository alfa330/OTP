# -*- coding: utf-8 -*-
"""SQL «Смен» «Чатов ОП» (wazzup/shift_report_routes.py) на отдельном локальном Postgres.

Порт одноразового кластера — в WAZZUP_PILOT_TEST_PORT (тот же, что у тестов обработки чатов).
Приложение не импортируется, боевых доступов нет; каждый тест живёт в своей случайной схеме и
убирает только её. Проверяется то, что легко сломать в самом SQL: границы суток Алматы при UTC в
базе, отсев рассылок и тестовых номеров ещё в базе, события смены только раздела (не телефона),
членство на дату и ручка целиком — период, отказ, сжатие."""
import gzip
import json
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta

import pytest
from flask import Flask, jsonify, request

from tests.test_wazzup_pilot_persistence import PORT
from wazzup import shift_report_routes as routes

pytestmark = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')

DAY = date(2026, 11, 12)
NOW = datetime(2026, 11, 13, 10, 0)
ANNA, OLGA, VERA = 10, 40, 30


class _Db:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def _get_cursor(self):
        with self.connection.cursor() as cursor:
            try:
                yield cursor
            except Exception:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()


@pytest.fixture
def stand():
    import psycopg2
    schema = 't_wazzup_shift_report_' + uuid.uuid4().hex
    connection = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres',
                                  dbname='postgres', connect_timeout=3)
    cursor = connection.cursor()
    cursor.execute('CREATE SCHEMA ' + schema)
    cursor.execute('SET search_path TO ' + schema)
    # База на проде живёт в UTC; сутки Алматы обязаны резаться так же и здесь.
    cursor.execute("SET TIME ZONE 'UTC'")
    cursor.execute("SET statement_timeout TO '8s'")
    cursor.execute('''
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE groups (id INTEGER PRIMARY KEY, calculation_model_code TEXT);
        CREATE TABLE group_operator_memberships (
            id SERIAL PRIMARY KEY, operator_id INTEGER, group_id INTEGER,
            start_date DATE NOT NULL, end_date DATE);
        CREATE TABLE operator_status_events (
            id BIGSERIAL PRIMARY KEY, operator_id INTEGER NOT NULL, event_at TIMESTAMP NOT NULL,
            event_date DATE, status_key TEXT NOT NULL, state_note TEXT,
            event_kind TEXT NOT NULL DEFAULT 'status', client_event_id TEXT);
        CREATE TABLE work_shifts (
            id SERIAL PRIMARY KEY, operator_id INTEGER, shift_date DATE, start_time TIME,
            end_time TIME, shift_type TEXT NOT NULL DEFAULT 'regular');
        CREATE TABLE daily_hours (
            operator_id INTEGER, day DATE, late_seconds INTEGER, early_leave_seconds INTEGER);
        CREATE TABLE wazzup_operator_map (
            author_id TEXT PRIMARY KEY, author_name TEXT, user_id INTEGER, is_bot BOOLEAN DEFAULT FALSE,
            account TEXT DEFAULT 'op');
        CREATE TABLE test_phone_numbers (phone_key TEXT PRIMARY KEY);
        CREATE TABLE wazzup_messages (
            message_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, chat_type TEXT, chat_id TEXT NOT NULL,
            dt TIMESTAMPTZ NOT NULL, is_echo BOOLEAN NOT NULL, type TEXT, author_id TEXT,
            contact_phone TEXT, is_deleted BOOLEAN NOT NULL DEFAULT FALSE, account TEXT NOT NULL DEFAULT 'op');
    ''')
    cursor.execute("INSERT INTO users VALUES (%s, 'Анна'), (%s, 'Ольга'), (%s, 'Вера')", (ANNA, OLGA, VERA))
    cursor.execute("INSERT INTO groups VALUES (13, 'op_verificator'), (36, 'OP_OSNOVA')")
    cursor.execute("""INSERT INTO group_operator_memberships (operator_id, group_id, start_date, end_date) VALUES
        (%s, 36, '2026-01-01', '2026-11-11'), (%s, 13, '2026-11-12', NULL),
        (%s, 36, '2026-01-01', NULL), (%s, 13, '2026-01-01', NULL)""", (ANNA, ANNA, OLGA, VERA))
    connection.commit()
    try:
        yield connection
    finally:
        connection.rollback()
        cursor = connection.cursor()
        cursor.execute('DROP SCHEMA ' + schema + ' CASCADE')
        connection.commit()
        connection.close()


def local(day, hhmm):
    return datetime.combine(day, time(*map(int, hhmm.split(':'))))


def utc(day, hhmm):
    """Время Алматы (UTC+5) — в UTC, как его хранит timestamptz."""
    return (local(day, hhmm) - timedelta(hours=5)).strftime('%Y-%m-%d %H:%M:%S+00')


def message(cursor, mid, chat, day, hhmm, echo, author=None, *, kind='text', phone=None,
            deleted=False, account='op'):
    cursor.execute("""INSERT INTO wazzup_messages
        (message_id, channel_id, chat_type, chat_id, dt, is_echo, type, author_id, contact_phone,
         is_deleted, account)
        VALUES (%s, 'ch', 'whatsapp', %s, %s, %s, %s, %s, %s, %s, %s)""",
                   (mid, chat, utc(day, hhmm), echo, kind, author, phone, deleted, account))


def seed(connection):
    cursor = connection.cursor()
    cursor.executemany("""INSERT INTO wazzup_operator_map (author_id, user_id, is_bot) VALUES (%s, %s, %s)""",
                       [('icore:10', ANNA, False), ('wz-olga', OLGA, False), ('wz-bot', None, True),
                        ('wz-yar', None, False)])
    cursor.execute("INSERT INTO test_phone_numbers VALUES ('7011112233')")
    # Смена Анны в разделе + событие её телефона, которое в смену чатов не входит.
    cursor.executemany("""INSERT INTO operator_status_events
        (operator_id, event_at, event_date, status_key, state_note, client_event_id) VALUES (%s, %s, %s, %s, %s, %s)""", [
        (ANNA, local(DAY, '09:00'), DAY, 'готов', None, 'wzws-a1'),
        (ANNA, local(DAY, '11:00'), DAY, 'перерыв', None, 'wzws-a2'),
        (ANNA, local(DAY, '11:30'), DAY, 'готов', None, 'wzws-a3'),
        (ANNA, local(DAY, '10:00'), DAY, 'выключен', None, 'phone-1'),
        (ANNA, local(DAY, '18:00'), DAY, 'выключен', 'Портал закрыт', 'wzws-auto-10-1'),
    ])
    cursor.executemany("""INSERT INTO work_shifts (operator_id, shift_date, start_time, end_time, shift_type)
        VALUES (%s, %s, %s, %s, %s)""", [
        (ANNA, DAY, time(9, 0), time(18, 0), 'regular'),
        (VERA, DAY, time(9, 0), time(18, 0), 'regular'),
        (OLGA, DAY, time(10, 0), time(19, 0), 'regular'),
    ])
    cursor.execute("INSERT INTO daily_hours VALUES (%s, %s, 0, 600), (%s, %s, 0, 0)", (ANNA, DAY, OLGA, DAY))
    # Переписка: граница суток — по Алматы, хотя база в UTC.
    message(cursor, 'm1', 'c1', DAY, '09:10', False)
    message(cursor, 'm2', 'c1', DAY, '09:11', True, 'icore:10')
    message(cursor, 'm3', 'c2', DAY, '14:00', False)
    message(cursor, 'm4', 'c2', DAY, '14:02', True, 'wz-olga')
    message(cursor, 'm5', 'c2', DAY, '14:03', True, 'wz-bot')                      # бот
    message(cursor, 'm6', 'c2', DAY, '14:04', True, None)                          # «Admin»
    message(cursor, 'm7', 'c3', DAY, '15:00', True, 'wz-yar', kind='wapi_template')  # рассылка
    message(cursor, 'm8', 'c4', DAY, '15:00', True, 'wz-new')                      # не привязан
    message(cursor, 'm9', '77011112233', DAY, '15:05', True, 'icore:10')           # тестовый номер
    message(cursor, 'm10', 'c1', DAY, '16:00', True, 'icore:10', deleted=True)     # удалено
    message(cursor, 'm11', 'c1', DAY, '16:00', True, 'icore:10', account='potok')  # другой аккаунт
    message(cursor, 'm12', 'c5', DAY - timedelta(days=1), '11:00', False)          # до запаса в 12 ч
    message(cursor, 'm13', 'c5', DAY - timedelta(days=1), '20:00', False)          # в запасе: начало диалога
    connection.commit()


def test_loaders(stand):
    seed(stand)
    cursor = stand.cursor()
    since, until = local(DAY, '00:00') - timedelta(hours=12), local(DAY + timedelta(days=1), '00:00')
    rows = routes.load_messages(cursor, since, until, 'op')
    assert sorted(row['message_id'] for row in rows) == ['m1', 'm13', 'm2', 'm3', 'm4', 'm8']
    by_id = {row['message_id']: row for row in rows}
    assert by_id['m2']['at'] == local(DAY, '09:11') and by_id['m2']['user_id'] == ANNA
    assert by_id['m8']['user_id'] is None and by_id['m8']['author_id'] == 'wz-new'

    assert routes.load_roster(cursor, DAY - timedelta(days=1), DAY) == {ANNA, VERA}
    events = routes.load_events(cursor, {ANNA, VERA}, local(DAY, '00:00'), until)
    assert [(key, note) for _at, key, note in events[ANNA]] == [
        ('готов', None), ('перерыв', None), ('готов', None), ('выключен', 'Портал закрыт')]
    assert VERA not in events

    models = routes.load_models(cursor, {ANNA, OLGA, VERA}, DAY - timedelta(days=1), DAY)
    assert models[(ANNA, DAY - timedelta(days=1))] == 'op_osnova'
    assert models[(ANNA, DAY)] == 'op_verificator'
    assert models[(OLGA, DAY)] == 'op_osnova'

    plans = routes.load_plans(cursor, {ANNA, VERA}, DAY, DAY)
    assert plans[ANNA] == [(local(DAY, '09:00'), local(DAY, '18:00'), 'regular')]
    assert routes.load_lateness(cursor, {ANNA, OLGA}, DAY, DAY) == {(ANNA, DAY): (0, 600)}
    assert routes.load_names(cursor, {ANNA}) == {ANNA: 'Анна'}
    assert routes.load_events(cursor, set(), since, until) == {}


def _app(stand, refuse=False):
    app = Flask(__name__)
    blueprint = routes.build_shift_report_blueprint(
        db=_Db(stand), require_api_key=lambda handler: handler, preflight=lambda: ('', 204),
        guard=lambda: (None, (jsonify({'error': 'forbidden'}), 403)) if refuse else (1, None),
        account_arg=lambda: {'op': 'op', 'potok': 'potok'}.get(request.args.get('account', 'op')),
        now=lambda: NOW)
    app.register_blueprint(blueprint)
    return app, blueprint


def test_route_end_to_end(stand):
    seed(stand)
    app, _ = _app(stand)
    client = app.test_client()
    response = client.get('/api/wazzup/shift_report?from=2026-11-12&to=2026-11-12&account=op',
                          headers={'Accept-Encoding': 'gzip'})
    assert response.status_code == 200
    body = response.data
    if response.headers.get('Content-Encoding') == 'gzip':
        body = gzip.decompress(body)
    payload = json.loads(body)
    assert (payload['from'], payload['to'], payload['withShifts']) == ('2026-11-12', '2026-11-12', True)
    kinds = sorted((row['kind'], row['userId']) for row in payload['rows'])
    assert kinds == [('absent', VERA), ('noShift', OLGA), ('shift', ANNA)]
    anna = next(row for row in payload['rows'] if row['userId'] == ANNA)
    assert (anna['start'], anna['end'], anna['endNote']) == ('2026-11-12T09:00:00', '2026-11-12T18:00:00', 'portal')
    assert (anna['active'], anna['breakTime'], anna['chats'], anna['messages']) == (8.5 * 3600, 1800, 1, 1)
    assert (anna['firstSum'], anna['firstCount'], anna['earlySeconds']) == (60.0, 1, 600)
    assert anna['groupKey'] == 'verifier'
    assert anna['plan'] == [{'start': '09:00', 'end': '18:00', 'nextDay': False, 'type': None}]
    olga = next(row for row in payload['rows'] if row['userId'] == OLGA)
    assert (olga['groupKey'], olga['chats'], olga['firstSum']) == ('osnova', 1, 120.0)
    assert payload['unlinked'] == {'authors': 1, 'messages': 1}
    assert payload['norms'] == {'firstReplySeconds': 60, 'innerReplySeconds': 240}


def test_route_refusals_and_period(stand):
    app, _ = _app(stand, refuse=True)
    assert app.test_client().get('/api/wazzup/shift_report').status_code == 403
    app, _ = _app(stand)
    client = app.test_client()
    assert client.get('/api/wazzup/shift_report?account=nope').status_code == 400
    too_long = client.get('/api/wazzup/shift_report?from=2026-10-01&to=2026-11-12')
    assert too_long.status_code == 400 and 'суток' in too_long.get_json()['error']
    future = client.get('/api/wazzup/shift_report?from=2026-11-20&to=2026-11-21')
    assert future.status_code == 400


def test_potok_account_has_no_shifts(stand):
    seed(stand)
    app, _ = _app(stand)
    payload = app.test_client().get('/api/wazzup/shift_report?from=2026-11-12&to=2026-11-12&account=potok').get_json()
    assert payload['withShifts'] is False
    assert [(row['kind'], row['userId']) for row in payload['rows']] == [('noShift', ANNA)]


def test_report_is_cached_per_period(stand):
    seed(stand)
    app, blueprint = _app(stand)
    first = blueprint.report('op', DAY, DAY)
    cursor = stand.cursor()
    cursor.execute("DELETE FROM wazzup_messages")
    stand.commit()
    assert blueprint.report('op', DAY, DAY) is first
    assert blueprint.report('op', DAY - timedelta(days=1), DAY) is not first
