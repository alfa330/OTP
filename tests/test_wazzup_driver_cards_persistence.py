"""Карточка водителя на настоящем PostgreSQL: ручки, реестр, NOTIFY и очередь amoCRM.

Как у остальных проверок пилота: только явно выбранный локальный кластер
(WAZZUP_PILOT_TEST_PORT), своя случайная схема на тест, её же и удаляем. amoCRM —
заглушка (tests/test_wazzup_driver_cards.py: FakeClient), в сеть ничего не уходит.
"""
import json
import os
import select
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from flask import Flask, jsonify

from tests.test_wazzup_driver_cards import ANCHORS, FakeClient, FakeResponse, contact, event
from tests.test_wazzup_pilot import CHANNEL, user
from wazzup import driver_cards_sync as sync
from wazzup import pilot
from wazzup.pilot_schema import init_schema
from wazzup.realtime import EventBroker, broadcast_changes

PORT = os.environ.get('WAZZUP_PILOT_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')

CHAT = '77000000101'
OTHER_CHAT = '77000000102'
GLOBAL = '99df6893-fb6b-4e1d-a78e-9e6e6b37abb2'
DRIVER = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
OTHER_DRIVER = '0f1e2d3c4b5a69788796a5b4c3d2e1f0'
OPTIONS = {'parks': [{'id': 1667515, 'name': 'Jana Taxi'}, {'id': 2386199, 'name': 'Tenge Taxi'}],
           'cities': [{'id': 1610837, 'name': 'Алматы'}, {'id': 1610839, 'name': 'Астана'}]}
CARD_PATH = '/api/wazzup/pilot/driver-card'


class Database:
    """Транзакция на вызов, как Database._get_cursor: коммит или откат."""

    def __init__(self, connection, actor=None):
        self.connection = connection
        self.actor = actor if actor is not None else user()

    def get_user(self, **_):
        return self.actor

    @contextmanager
    def _get_cursor(self):
        cursor = self.connection.cursor()
        try:
            yield cursor
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            cursor.close()


@pytest.fixture
def pg():
    import psycopg2
    schema = 't_wazzup_cards_' + uuid.uuid4().hex
    connections = []

    def connect():
        connection = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres',
                                      dbname='postgres', connect_timeout=3)
        connections.append(connection)
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO ' + schema)
            cursor.execute("SET statement_timeout TO '5s'")
        connection.commit()
        return connection

    connection = connect()
    cursor = connection.cursor()
    cursor.execute('CREATE SCHEMA ' + schema)
    cursor.execute('SET search_path TO ' + schema)
    cursor.execute('''
        CREATE TABLE wazzup_messages (
            message_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, chat_type TEXT,
            chat_id TEXT NOT NULL, dt TIMESTAMPTZ NOT NULL, is_echo BOOLEAN NOT NULL,
            type TEXT, text TEXT, content_uri TEXT, author_name TEXT, author_id TEXT,
            contact_name TEXT, contact_phone TEXT, status TEXT, sent_from_app TEXT,
            is_edited BOOLEAN NOT NULL DEFAULT FALSE, is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            account TEXT NOT NULL DEFAULT 'op', wazzup_dt TIMESTAMPTZ,
            quoted_message_id TEXT, quoted_is_echo BOOLEAN, quoted_type TEXT, quoted_text TEXT,
            quoted_author_name TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE wazzup_chats (
            channel_id TEXT NOT NULL, chat_id TEXT NOT NULL, chat_type TEXT,
            contact_name TEXT, contact_phone TEXT, last_message_at TIMESTAMPTZ,
            last_message_text TEXT, last_message_is_echo BOOLEAN,
            messages_count INTEGER DEFAULT 0, inbound_count INTEGER DEFAULT 0,
            outbound_count INTEGER DEFAULT 0, updated_at TIMESTAMPTZ DEFAULT now(),
            account TEXT NOT NULL DEFAULT 'op', PRIMARY KEY(channel_id, chat_id)
        );
    ''')
    init_schema(cursor)
    for channel, chat, name in ((CHANNEL, CHAT, 'Асқар Водитель'), (CHANNEL, OTHER_CHAT, 'Другой'),
                                (GLOBAL, CHAT, 'Global')):
        cursor.execute("INSERT INTO wazzup_chats(channel_id,chat_id,chat_type,contact_name,contact_phone,account) "
                       "VALUES (%s,%s,'whatsapp',%s,%s,'op')", (channel, chat, name, '+' + chat))
    for index, at in enumerate(ANCHORS):
        cursor.execute("INSERT INTO wazzup_messages(message_id,channel_id,chat_id,dt,is_echo,account) "
                       "VALUES (%s,%s,%s,to_timestamp(%s),%s,'op')",
                       ('m%d' % index, CHANNEL, CHAT, at, index % 2 == 0))
    connection.commit()
    try:
        yield connection, connect
    finally:
        for other in connections[1:]:
            other.close()
        connection.rollback()
        with connection.cursor() as cleanup:
            cleanup.execute('DROP SCHEMA ' + schema + ' CASCADE')
        connection.commit()
        connection.close()


def app_for(database, *, can_process=True, options=OPTIONS, wake=None):
    access = (lambda: {'mode': 'full', 'can_process': can_process})
    app = Flask(__name__)
    app.testing = True
    app.register_blueprint(pilot.build_pilot_blueprint(
        db=database, require_api_key=lambda fn: fn, guard=lambda: (42, None),
        channels=lambda account: [], preflight=lambda: ('', 204), listen_connect=lambda: None,
        transport=Mock(), event_broker=EventBroker(), access=access,
        driver_card_sync={'wake': wake or Mock(), 'options': lambda: options}))
    return app.test_client()


def card_body(**updates):
    return dict(account='op', channelId=CHANNEL, chatId=CHAT, clientCardId=str(uuid.uuid4()),
                driverId=DRIVER, parkId=1667515, cityId=1610837) | updates


def rows(connection, sql='SELECT amo_state, driver_id, park_name, city_name FROM wazzup_driver_cards '
                         'ORDER BY created_at'):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        result = cursor.fetchall()
    connection.commit()
    return result


# ── Ручки ───────────────────────────────────────────────────────────────────

def test_save_keeps_versions_and_the_chat_shows_the_newest_first(pg):
    connection, _ = pg
    wake = Mock()
    client = app_for(Database(connection), wake=wake)
    first = client.post(CARD_PATH, json=card_body(driverId='https://fleet.yandex.kz/contractors/%s/details?park_id=%s'
                                                  % (DRIVER.upper(), OTHER_DRIVER)))
    assert first.status_code == 201, first.json
    item = first.json['item']
    assert item['driverId'] == DRIVER
    assert item['park'] == {'id': 1667515, 'name': 'Jana Taxi'}
    assert item['city'] == {'id': 1610837, 'name': 'Алматы'}
    assert item['amo']['state'] == 'pending' and item['authorName'] == 'Pilot Tester'
    assert item['contactName'] == 'Асқар Водитель' and item['contactPhone'] == '+' + CHAT
    assert wake.call_count == 1
    second = client.post(CARD_PATH, json=card_body(parkId=2386199, cityId=1610839))
    assert second.status_code == 201
    listing = client.get(CARD_PATH, query_string={'account': 'op', 'channelId': CHANNEL, 'chatId': CHAT})
    assert listing.status_code == 200 and listing.json['canEdit'] is True
    assert [card['park']['name'] for card in listing.json['items']] == ['Tenge Taxi', 'Jana Taxi']
    assert listing.headers['Cache-Control'] == 'private, no-store'
    assert rows(connection) == [('pending', DRIVER, 'Jana Taxi', 'Алматы'), ('pending', DRIVER, 'Tenge Taxi', 'Астана')]


def test_the_same_save_retried_is_one_version_and_a_reused_id_elsewhere_is_refused(pg):
    connection, _ = pg
    client = app_for(Database(connection))
    body = card_body()
    first = client.post(CARD_PATH, json=body)
    again = client.post(CARD_PATH, json=body)
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json['item'] == first.json['item']
    elsewhere = client.post(CARD_PATH, json=dict(body, chatId=OTHER_CHAT))
    assert elsewhere.status_code == 409 and elsewhere.json['code'] == 'REQUEST_CONFLICT'
    assert len(rows(connection)) == 1


@pytest.mark.parametrize('updates, status, needle', [
    ({'driverId': '77000000099'}, 400, 'ID водителя — 32 символа'),
    ({'parkId': 999}, 400, 'таксопарка больше нет в списке amoCRM — выберите другой'),
    ({'cityId': 'x'}, 400, 'Некорректное значение'),
    ({'parkId': True}, 400, 'Некорректное значение'),
    ({'driverId': '', 'parkId': None, 'cityId': None}, 400, 'хотя бы одно поле'),
    ({'channelId': GLOBAL}, 403, 'Global'),
    ({'account': 'potok'}, 403, 'только в чатах Верификаторов'),
    ({'chatId': '70000000000'}, 404, 'Чат не найден'),
    ({'channelId': 'not-a-uuid'}, 400, 'Некорректный канал'),
    ({'clientCardId': 'x'}, 400, 'идентификатор'),
])
def test_invalid_cards_are_refused_before_any_write(pg, updates, status, needle):
    connection, _ = pg
    response = app_for(Database(connection)).post(CARD_PATH, json=card_body(**updates))
    assert response.status_code == status and needle in response.json['error']
    assert rows(connection) == []


def test_a_filled_field_can_be_changed_but_not_cleared(pg):
    connection, _ = pg
    client = app_for(Database(connection))
    assert client.post(CARD_PATH, json=card_body(cityId=None)).status_code == 201
    cleared = client.post(CARD_PATH, json=card_body(driverId='', cityId=1610837))
    assert cleared.status_code == 400 and 'ID водителя' in cleared.json['error']
    cleared = client.post(CARD_PATH, json=card_body(parkId=None))
    assert cleared.status_code == 400 and 'Таксопарк привлечения' in cleared.json['error']
    assert client.post(CARD_PATH, json=card_body(driverId=OTHER_DRIVER)).status_code == 201
    assert [row[1] for row in rows(connection)] == [DRIVER, OTHER_DRIVER]


def test_an_unchanged_save_is_not_a_new_version_unless_the_last_one_failed(pg):
    connection, _ = pg
    client = app_for(Database(connection))
    first = client.post(CARD_PATH, json=card_body())
    same = client.post(CARD_PATH, json=card_body())
    assert same.status_code == 200 and same.json['unchanged'] is True
    assert same.json['item']['id'] == first.json['item']['id']
    with connection.cursor() as cursor:
        cursor.execute("UPDATE wazzup_driver_cards SET amo_state='failed', amo_error='amoCRM ответила 502'")
    connection.commit()
    again = client.post(CARD_PATH, json=card_body())
    assert again.status_code == 201 and again.json['item']['amo']['state'] == 'pending'
    assert [row[0] for row in rows(connection)] == ['failed', 'pending']


def test_without_amo_lists_nothing_can_be_validated(pg):
    connection, _ = pg
    client = app_for(Database(connection), options=None)
    response = client.post(CARD_PATH, json=card_body())
    assert response.status_code == 503 and response.json['code'] == 'OPTIONS_UNAVAILABLE'
    assert client.get(CARD_PATH + '/options').status_code == 503
    assert rows(connection) == []


def test_viewers_see_the_card_but_only_processors_write_it(pg):
    connection, _ = pg
    writer = app_for(Database(connection))
    saved = writer.post(CARD_PATH, json=card_body()).json['item']
    reader = app_for(Database(connection), can_process=False)
    listing = reader.get(CARD_PATH, query_string={'account': 'op', 'channelId': CHANNEL, 'chatId': CHAT})
    assert listing.status_code == 200 and listing.json['canEdit'] is False
    assert listing.json['items'][0]['id'] == saved['id']
    assert reader.post(CARD_PATH, json=card_body(parkId=2386199)).status_code == 403
    assert reader.post(CARD_PATH + '/retry', json=dict(account='op', channelId=CHANNEL, chatId=CHAT,
                                                       cardId=saved['id'])).status_code == 403
    assert reader.get(CARD_PATH + '/options').json['parks'] == OPTIONS['parks']
    assert reader.get('/api/wazzup/pilot/driver-cards').status_code == 200
    assert len(rows(connection)) == 1


def test_registry_shows_only_the_current_card_of_each_chat(pg):
    connection, _ = pg
    client = app_for(Database(connection))
    client.post(CARD_PATH, json=card_body())
    client.post(CARD_PATH, json=card_body(parkId=2386199))
    client.post(CARD_PATH, json=card_body(chatId=OTHER_CHAT, driverId=OTHER_DRIVER, parkId=None, cityId=1610839))
    listing = client.get('/api/wazzup/pilot/driver-cards', query_string={'account': 'op'})
    assert listing.status_code == 200 and listing.json['total'] == 2
    assert [(card['chatId'], (card['park'] or {}).get('name')) for card in listing.json['items']] == [
        (OTHER_CHAT, None), (CHAT, 'Tenge Taxi')]
    assert listing.json['items'][0]['chatAvailable'] is True
    assert listing.json['items'][0]['chatType'] == 'whatsapp'

    def found(q):
        return [card['chatId'] for card in client.get('/api/wazzup/pilot/driver-cards',
                                                      query_string={'q': q}).json['items']]
    assert found(DRIVER[:8]) == [CHAT]
    assert found('0000102') == [OTHER_CHAT]
    assert found('tenge') == [CHAT]
    assert found('асқар') == [CHAT]
    assert found('pilot') == [OTHER_CHAT, CHAT]
    assert found('100%') == [] and found('a_b') == []
    page = client.get('/api/wazzup/pilot/driver-cards', query_string={'limit': 1, 'offset': 5}).json
    assert page == {'items': [], 'total': 2}, 'итог не обнуляется на странице за хвостом'
    old = client.get('/api/wazzup/pilot/driver-cards', query_string={'from': '2026-01-01', 'to': '2026-01-31'}).json
    assert old['total'] == 0
    bad = client.get('/api/wazzup/pilot/driver-cards', query_string={'from': 'вчера'})
    assert bad.status_code == 400
    year = client.get('/api/wazzup/pilot/driver-cards', query_string={'from': '2024-01-01', 'to': '2026-01-01'})
    assert year.status_code == 400 and 'год' in year.json['error']
    assert client.get('/api/wazzup/pilot/driver-cards', query_string={'account': 'potok'}).status_code == 403


def test_registry_keeps_the_card_after_the_chat_left_by_retention(pg):
    connection, _ = pg
    client = app_for(Database(connection))
    client.post(CARD_PATH, json=card_body())
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM wazzup_chats WHERE chat_id=%s", (CHAT,))
    connection.commit()
    item = client.get('/api/wazzup/pilot/driver-cards').json['items'][0]
    assert item['contactName'] == 'Асқар Водитель' and item['chatAvailable'] is False


def test_retry_requeues_only_the_latest_failed_card(pg):
    connection, _ = pg
    client = app_for(Database(connection))
    first = client.post(CARD_PATH, json=card_body()).json['item']
    with connection.cursor() as cursor:
        cursor.execute("UPDATE wazzup_driver_cards SET amo_state='no_deal', amo_error='нет сделки', amo_attempts=3")
    connection.commit()
    retry = dict(account='op', channelId=CHANNEL, chatId=CHAT, cardId=first['id'])
    done = client.post(CARD_PATH + '/retry', json=retry)
    retried = done.json['item']['amo']
    assert done.status_code == 200 and dict(retried, queuedAt=None) == dict(
        first['amo'], state='pending', error=None, queuedAt=None)
    assert retried['queuedAt'] > first['amo']['queuedAt'], '«Повторить» ставит версию в очередь заново'
    assert rows(connection, 'SELECT amo_attempts, amo_error FROM wazzup_driver_cards') == [(0, None)]
    assert client.post(CARD_PATH + '/retry', json=retry).json['item']['amo']['state'] == 'pending'
    client.post(CARD_PATH, json=card_body(parkId=2386199))
    stale = client.post(CARD_PATH + '/retry', json=retry)
    assert stale.status_code == 409 and stale.json['code'] == 'STALE_CARD'


# ── Живой поток ─────────────────────────────────────────────────────────────

def test_notify_carries_no_card_values_and_skips_lease_bookkeeping(pg):
    connection, connect = pg
    listener = connect()
    listener.set_session(autocommit=True)
    with listener.cursor() as cursor:
        cursor.execute('LISTEN wazzup_pilot_events')
    database = Database(connection)
    saved = app_for(database).post(CARD_PATH, json=card_body()).json['item']
    assert select.select([listener], [], [], 1)[0]
    listener.poll()
    payloads = [notice.payload for notice in listener.notifies]
    listener.notifies.clear()
    assert len(payloads) == 1
    notice = json.loads(payloads[0])
    assert notice['kind'] == 'card' and notice['cardId'] == saved['id']
    assert notice['messageId'] == 'card:' + saved['id'] and notice['affectsList'] is False
    assert DRIVER not in payloads[0] and 'Jana' not in payloads[0]

    item = sync.claim(database)
    assert item is not None
    assert not select.select([listener], [], [], 0.2)[0], 'аренда не событие для экрана'
    sync._finish(database, item, 'synced', lead_id=10000001)
    assert select.select([listener], [], [], 1)[0]
    listener.poll()
    finished = json.loads(listener.notifies[-1].payload)
    broker = EventBroker()
    broker.acquire()
    with connection.cursor() as cursor:
        broadcast_changes(cursor, [finished], broker)
    connection.commit()
    card = broker.wait(0)[0][0]['card']
    assert card['amo']['state'] == 'synced' and card['amo']['leadId'] == 10000001
    assert card['amo']['leadUrl'].endswith('/leads/detail/10000001')
    assert card['driverId'] == DRIVER


# ── Очередь ─────────────────────────────────────────────────────────────────

def amo_client(lead=10000001, patch=None):
    return FakeClient({
        ('GET', '/api/v4/contacts'): FakeResponse(200, {'_embedded': {'contacts': [contact(1, '+' + CHAT)]}}),
        ('GET', '/api/v4/events'): FakeResponse(200, {'_embedded': {'events': [
            event(at, 9001, lead) for at in ANCHORS]}}),
        ('PATCH', '/api/v4/leads/%d' % lead): patch or FakeResponse(200, {'id': lead}),
    })


class Holder:
    def __init__(self, client):
        self.client, self.dropped = client, 0

    def get(self):
        return self.client

    def drop(self):
        self.dropped += 1


def state(connection):
    return rows(connection, "SELECT amo_state, amo_lead_id, amo_error, amo_attempts, "
                            "amo_next_attempt_at > now(), amo_synced_at IS NOT NULL, amo_lease_token IS NULL "
                            "FROM wazzup_driver_cards ORDER BY created_at")


def test_worker_writes_the_card_into_the_deal_of_this_chat(pg):
    connection, _ = pg
    database = Database(connection)
    app_for(database).post(CARD_PATH, json=card_body())
    client = amo_client()
    assert sync.process_once(database, Holder(client)) is True
    assert state(connection) == [('synced', 10000001, None, 1, False, True, True)]
    method, path, kwargs = client.calls[-1]
    assert (method, path) == ('PATCH', '/api/v4/leads/10000001')
    assert kwargs['json'] == {'custom_fields_values': [
        {'field_id': 1176191, 'values': [{'value': DRIVER}]},
        {'field_id': 915311, 'values': [{'enum_id': 1667515}]},
        {'field_id': 1073351, 'values': [{'enum_id': 1610837}]}]}
    assert sync.process_once(database, Holder(client)) is False


def test_only_the_newest_version_reaches_amo_and_a_leased_one_blocks_the_chat(pg):
    connection, _ = pg
    database = Database(connection)
    client_app = app_for(database)
    client_app.post(CARD_PATH, json=card_body())
    leased = sync.claim(database)
    client_app.post(CARD_PATH, json=card_body(parkId=2386199))
    assert sync.claim(database) is None, 'пока старая версия в аренде, новая ждёт'
    sync._finish(database, leased, 'synced', lead_id=10000001)
    client_app.post(CARD_PATH, json=card_body(parkId=1667515, cityId=1610839))
    client = amo_client()
    assert sync.process_once(database, Holder(client)) is True
    assert [row[0] for row in state(connection)] == ['synced', 'superseded', 'synced']
    patched = client.calls[-1][2]['json']['custom_fields_values']
    assert {'field_id': 1073351, 'values': [{'enum_id': 1610839}]} in patched


def test_a_new_chat_waits_for_its_deal_then_reports_it_missing(pg):
    connection, _ = pg
    database = Database(connection)
    app_for(database).post(CARD_PATH, json=card_body())
    nobody = FakeClient({('GET', '/api/v4/contacts'): FakeResponse(204)})
    with connection.cursor() as cursor:
        due = "UPDATE wazzup_driver_cards SET amo_next_attempt_at = now() - interval '1 second'"
        for attempt in (1, 2):
            assert sync.process_once(database, Holder(nobody)) is True
            assert state(connection)[0][:5] == ('pending', None, None, attempt, True)
            cursor.execute(due)
            connection.commit()
        assert sync.process_once(database, Holder(nobody)) is True
    assert state(connection)[0][:4] == ('no_deal', None, 'В amoCRM нет контакта с этим номером', 3)


def test_amo_outage_is_retried_and_given_up_after_a_day(pg):
    connection, _ = pg
    database = Database(connection)
    app_for(database).post(CARD_PATH, json=card_body())
    holder = Holder(amo_client(patch=FakeResponse(503, {})))
    assert sync.process_once(database, holder) is True
    assert state(connection)[0][:5] == ('pending', 10000001, 'amoCRM ответила 503', 1, True)
    with connection.cursor() as cursor:
        cursor.execute("UPDATE wazzup_driver_cards SET amo_next_attempt_at = now() - interval '1 second', "
                       "amo_queued_at = now() - interval '25 hours'")
    connection.commit()
    assert sync.process_once(database, holder) is True
    assert state(connection)[0][:4] == ('failed', 10000001, 'amoCRM ответила 503', 2)


def test_refused_login_gets_a_fresh_client_and_a_refusal_on_the_merits_is_final(pg):
    connection, _ = pg
    database = Database(connection)
    app_for(database).post(CARD_PATH, json=card_body())
    holder = Holder(amo_client(patch=FakeResponse(401, {})))
    assert sync.process_once(database, holder) is True
    assert holder.dropped == 1 and state(connection)[0][0] == 'pending'
    with connection.cursor() as cursor:
        cursor.execute("UPDATE wazzup_driver_cards SET amo_next_attempt_at = now() - interval '1 second'")
    connection.commit()
    final = Holder(amo_client(patch=FakeResponse(400, {'title': 'Bad Request'})))
    assert sync.process_once(database, final) is True
    result = state(connection)[0]
    assert result[:2] == ('failed', 10000001) and 'Bad Request' in result[2]


def test_worker_sleeps_until_the_nearest_retry_not_a_fixed_idle(pg):
    connection, _ = pg
    database = Database(connection)
    assert sync.next_due_seconds(database) is None
    app_for(database).post(CARD_PATH, json=card_body())
    assert sync.next_due_seconds(database) == 0.0
    nobody = FakeClient({('GET', '/api/v4/contacts'): FakeResponse(204)})
    sync.process_once(database, Holder(nobody))
    due = sync.next_due_seconds(database)
    assert sync.NO_DEAL_DELAYS[0] - 2 < due <= sync.NO_DEAL_DELAYS[0]
    with connection.cursor() as cursor:
        cursor.execute("UPDATE wazzup_driver_cards SET amo_next_attempt_at = now() - interval '5 seconds', "
                       "amo_lease_until = now() + interval '40 seconds'")
    connection.commit()
    assert 38 < sync.next_due_seconds(database) <= 40, 'чужая аренда — ждать её конца, а не опрашивать каждую секунду'


def test_options_snapshot_survives_an_amo_outage(pg):
    connection, _ = pg
    database = Database(connection)
    good = FakeClient({('GET', '/api/v4/leads/custom_fields'): FakeResponse(200, {'_embedded': {'custom_fields': [
        {'id': 1176191, 'type': 'text'},
        {'id': 915311, 'enums': [{'id': 1667515, 'value': 'Jana Taxi', 'sort': 1}]},
        {'id': 1073351, 'enums': [{'id': 1610837, 'value': 'Алматы', 'sort': 1}]}]}})})
    assert sync.refresh_options(database, Holder(good), force=True)['parks'] == [{'id': 1667515, 'name': 'Jana Taxi'}]
    down = FakeClient({('GET', '/api/v4/leads/custom_fields'): FakeResponse(502, {})})
    assert sync.refresh_options(database, Holder(down), force=True) is None
    snapshot = sync.current_options(database)
    assert snapshot['cities'] == [{'id': 1610837, 'name': 'Алматы'}] and snapshot['fetchedAt']


def test_a_refused_login_while_reading_lists_gets_a_fresh_client(pg):
    connection, _ = pg
    holder = Holder(FakeClient({('GET', '/api/v4/leads/custom_fields'): FakeResponse(401, {})}))
    assert sync.refresh_options(Database(connection), holder, force=True) is None
    assert holder.dropped == 1, 'повторный вход старой сессией amoCRM не работает'


def test_options_are_not_refetched_from_routes_right_after_a_failure(pg, monkeypatch):
    connection, _ = pg
    database = Database(connection)
    calls = []
    monkeypatch.setattr(sync, 'amo_configured', lambda: True)
    sync._options_next_try[0] = 0.0

    class Failing:
        def get(self):
            calls.append(1)
            raise sync.amo.AmoTransientError('down')

    monkeypatch.setattr(sync, 'ClientHolder', Failing)
    assert sync.current_options(database) is None
    assert sync.current_options(database) is None
    assert len(calls) == 1, 'после неудачи — пауза, а не запрос в amoCRM на каждый клик'
    sync._options_next_try[0] = 0.0


def due_now(connection):
    with connection.cursor() as cursor:
        cursor.execute("UPDATE wazzup_driver_cards SET amo_next_attempt_at = now() - interval '1 second'")
    connection.commit()


def test_retry_next_day_gets_its_own_day_of_retries(pg):
    # Карточка сдалась после суток повторов; утром «Повторить», а amoCRM снова ответила 429.
    connection, _ = pg
    database = Database(connection)
    client_app = app_for(database)
    saved = client_app.post(CARD_PATH, json=card_body()).json['item']
    with connection.cursor() as cursor:
        cursor.execute("UPDATE wazzup_driver_cards SET amo_state='failed', amo_attempts=9, "
                       "amo_error='amoCRM ответила 503', created_at = now() - interval '30 hours', "
                       "amo_queued_at = now() - interval '30 hours'")
    connection.commit()
    retry = client_app.post(CARD_PATH + '/retry', json=dict(account='op', channelId=CHANNEL, chatId=CHAT,
                                                            cardId=saved['id'])).json['item']
    assert retry['amo']['state'] == 'pending'
    assert sync.process_once(database, Holder(amo_client(patch=FakeResponse(429, {})))) is True
    assert state(connection)[0][:5] == ('pending', 10000001, 'amoCRM ответила 429', 1, True), \
        'сутки повторов — от «Повторить», а не от создания карточки'


def test_deal_waits_of_a_new_chat_are_counted_apart_from_amo_failures(pg):
    connection, _ = pg
    database = Database(connection)
    app_for(database).post(CARD_PATH, json=card_body())
    flaky = Holder(amo_client(patch=FakeResponse(503, {})))
    for _ in range(2):
        sync.process_once(database, flaky)
        due_now(connection)
    nobody = Holder(FakeClient({('GET', '/api/v4/contacts'): FakeResponse(204)}))
    sync.process_once(database, nobody)
    assert state(connection)[0][:2] == ('pending', 10000001), \
        'два сбоя amoCRM не съедают ожидания сделки нового чата'
    assert rows(connection, 'SELECT amo_no_deal_attempts, amo_attempts FROM wazzup_driver_cards') == [(1, 3)]
    due_now(connection)
    sync.process_once(database, flaky)
    with connection.cursor() as cursor:
        cursor.execute("SELECT EXTRACT(EPOCH FROM amo_next_attempt_at - now()) FROM wazzup_driver_cards")
        delay = cursor.fetchone()[0]
    connection.commit()
    assert sync.RETRY_DELAYS[2] - 5 < delay <= sync.RETRY_DELAYS[2], 'третий сбой — третья пауза, ожидание не в счёт'


def test_a_version_taken_over_while_searching_the_deal_is_not_written(pg):
    # Пока искали сделку, аренда истекла, и другой процесс (второй экземпляр на
    # время выкладки) пометил версию заменённой: запоздалый PATCH положил бы её поверх новой.
    connection, connect = pg
    database = Database(connection)
    app_for(database).post(CARD_PATH, json=card_body())
    other = connect()

    def events(_kwargs):
        with other.cursor() as cursor:
            cursor.execute("UPDATE wazzup_driver_cards SET amo_state='superseded', amo_lease_token=NULL")
        other.commit()
        return FakeResponse(200, {'_embedded': {'events': [event(at, 9001, 10000001) for at in ANCHORS]}})

    client = amo_client()
    client.routes[('GET', '/api/v4/events')] = events
    assert sync.process_once(database, Holder(client)) is True
    assert [call[0] for call in client.calls] == ['GET', 'GET'], 'PATCH не ушёл'
    assert state(connection)[0][0] == 'superseded'


def test_a_retry_with_an_error_reaches_the_screen_even_if_the_state_stays_pending(pg):
    connection, connect = pg
    listener = connect()
    listener.set_session(autocommit=True)
    with listener.cursor() as cursor:
        cursor.execute('LISTEN wazzup_pilot_events')
    database = Database(connection)
    app_for(database).post(CARD_PATH, json=card_body())
    select.select([listener], [], [], 1)
    listener.poll()
    listener.notifies.clear()
    sync.process_once(database, Holder(amo_client(patch=FakeResponse(503, {}))))
    assert select.select([listener], [], [], 1)[0], '«amoCRM: повторим» с причиной — тоже событие'
    listener.poll()
    assert json.loads(listener.notifies[-1].payload)['kind'] == 'card'


def test_registry_searches_phones_only_by_phone_like_queries_and_days_by_almaty(pg):
    connection, _ = pg
    client = app_for(Database(connection))
    client.post(CARD_PATH, json=card_body())
    # Цифры этого ID («0000001») есть в номерах обоих чатов.
    digits_id = 'aa0000001' + 'b' * 23
    client.post(CARD_PATH, json=card_body(chatId=OTHER_CHAT, driverId=digits_id))

    def found(**params):
        return sorted(card['chatId'] for card in client.get('/api/wazzup/pilot/driver-cards',
                                                            query_string=params).json['items'])
    assert found(q='+7 700 000-01-02') == [OTHER_CHAT]
    assert found(q='0000001') == [CHAT, OTHER_CHAT], 'одни цифры — это поиск по номеру'
    assert found(q=digits_id[:9]) == [OTHER_CHAT], 'кусок ID ищет по ID, а не по цифрам номеров'
    with connection.cursor() as cursor:
        # 10.10 в 19:30 UTC — это уже 11.10, 00:30 по Алматы.
        cursor.execute("UPDATE wazzup_driver_cards SET created_at = '2026-10-10 19:30:00+00' WHERE chat_id = %s",
                       (CHAT,))
    connection.commit()
    assert found(**{'from': '2026-10-11', 'to': '2026-10-11'}) == [CHAT]
    assert found(**{'from': '2026-10-10', 'to': '2026-10-10'}) == []
    item = client.get('/api/wazzup/pilot/driver-cards').json['items'][0]
    assert item['amo']['queuedAt']
