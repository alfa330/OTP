"""Real SQL regression checks on an explicitly selected, isolated local Postgres.

Set WAZZUP_PILOT_TEST_PORT to a disposable local cluster's port. No app import,
environment-file loading, production credentials or nonlocal host is supported.
Each test owns a random schema and removes only that schema afterwards.
"""
import ast
import json
import os
import select
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests import source_cache
from wazzup.pilot_schema import init_schema

PORT = os.environ.get('WAZZUP_PILOT_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')
ROOT = Path(__file__).resolve().parents[1]


def _database_class():
    source = source_cache.parse((ROOT / 'database.py').read_text(encoding='utf-8-sig'))
    cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'Database')
    wanted = {'store_wazzup_messages', '_refresh_wazzup_chat_tx', 'update_wazzup_statuses'}
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {n.name for n in methods} == wanted
    module = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name='Database', bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[]))
    namespace = {}
    exec(compile(module, str(ROOT / 'database.py'), 'exec'), namespace)
    return namespace['Database']


@pytest.fixture
def pg():
    import psycopg2
    schema = 't_wazzup_pilot_' + uuid.uuid4().hex
    connections = []

    def connect():
        connection = psycopg2.connect(host='127.0.0.1', port=int(PORT),
                                      user='postgres', dbname='postgres', connect_timeout=3)
        connections.append(connection)
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO ' + schema)
            cursor.execute("SET statement_timeout TO '5s'")
        connection.commit()
        return connection

    connection = connect()
    cursor = connection.cursor()
    cursor.execute('CREATE SCHEMA ' + schema)
    cursor.execute('''
        CREATE TABLE wazzup_messages (
            message_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, chat_type TEXT,
            chat_id TEXT NOT NULL, dt TIMESTAMPTZ NOT NULL, is_echo BOOLEAN NOT NULL,
            type TEXT, text TEXT, content_uri TEXT, author_name TEXT, author_id TEXT,
            contact_name TEXT, contact_phone TEXT, status TEXT, sent_from_app TEXT,
            is_edited BOOLEAN NOT NULL DEFAULT FALSE, is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            account TEXT NOT NULL DEFAULT 'op', wazzup_dt TIMESTAMPTZ,
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
    connection.commit()

    def database(conn=connection):
        db = _database_class()()

        @contextmanager
        def get_cursor():
            with conn.cursor() as cur:
                yield cur
        db._get_cursor = get_cursor
        return db

    try:
        yield connection, cursor, database, connect
    finally:
        for other in connections[1:]:
            other.close()
        connection.rollback()
        cursor.execute('DROP SCHEMA ' + schema + ' CASCADE')
        connection.commit()
        connection.close()


def message(message_id='m1', status='sent', **changes):
    return dict(messageId=message_id, channelId='channel-op', chatId='70000000000',
                chatType='whatsapp', dateTime='2026-10-06T08:00:00Z', isEcho=True,
                text='Private message content', status=status, **changes)


def status(cursor, message_id='m1'):
    cursor.execute('SELECT status FROM wazzup_messages WHERE message_id=%s', (message_id,))
    row = cursor.fetchone()
    return row[0] if row else None


def test_schema_is_idempotent_and_outbox_message_is_unique_per_account(pg):
    connection, cursor, _, _ = pg
    init_schema(cursor)
    cursor.execute('''INSERT INTO wazzup_pilot_outbox
        (request_id,account,channel_id,chat_id,chat_type,text,user_id,message_id)
        VALUES (%s,'op','channel','chat','whatsapp','hello',1,'m1'),
               (%s,'potok','other','chat','whatsapp','hello',1,'m1')''',
        (str(uuid.uuid4()), str(uuid.uuid4())))
    cursor.execute("SELECT COUNT(*) FROM wazzup_pilot_outbox WHERE message_id='m1'")
    assert cursor.fetchone()[0] == 2
    cursor.execute('''SELECT indexdef FROM pg_indexes
        WHERE schemaname=current_schema() AND indexname='idx_wazzup_pilot_account_message' ''')
    assert 'UNIQUE' in cursor.fetchone()[0]


def test_status_before_echo_is_preserved_and_late_echo_cannot_regress_it(pg):
    _, cursor, database, _ = pg
    db = database()
    assert db.update_wazzup_statuses([{'messageId': 'm1', 'status': 'read'}]) == 0
    assert db.store_wazzup_messages([message()]) == 1
    assert status(cursor) == 'read'
    db.store_wazzup_messages([message(status='delivered')])
    db.update_wazzup_statuses([{'messageId': 'm1', 'status': 'sent'}])
    assert status(cursor) == 'read'


@pytest.mark.parametrize('events,expected', [
    (['sent', 'delivered', 'read', 'sent', 'error'], 'read'),
    (['error', 'sent'], 'error'),
    (['error', 'delivered'], 'delivered'),
    (['delivered', 'error', 'sent'], 'delivered'),
    (['edited'], 'pending'),
])
def test_duplicate_and_out_of_order_statuses_are_monotonic(pg, events, expected):
    _, cursor, database, _ = pg
    db = database()
    db.store_wazzup_messages([message(status='pending')])
    for event in events:
        db.update_wazzup_statuses([{'messageId': 'm1', 'status': event}])
    assert status(cursor) == expected


def test_edited_is_not_a_delivery_status(pg):
    _, cursor, database, _ = pg
    db = database()
    db.store_wazzup_messages([message(status='edited', isEdited=True)])
    assert status(cursor) is None
    db.update_wazzup_statuses([{'messageId': 'm1', 'status': 'delivered'}])
    db.store_wazzup_messages([message(status='edited', isEdited=True)])
    assert status(cursor) == 'delivered'


def test_status_receipts_and_message_collisions_are_account_isolated(pg):
    _, cursor, database, _ = pg
    db = database()
    db.update_wazzup_statuses([{'messageId': 'm1', 'status': 'read'}], account='potok')
    db.store_wazzup_messages([message(status='sent')], account='op')
    assert status(cursor) == 'sent'
    assert db.update_wazzup_statuses([{'messageId': 'm1', 'status': 'delivered'}], account='potok') == 0
    assert db.store_wazzup_messages([message(status='read')], account='potok') == 0
    cursor.execute('SELECT account,status,text FROM wazzup_messages')
    assert cursor.fetchone() == ('op', 'sent', 'Private message content')
    cursor.execute('SELECT account FROM wazzup_chats')
    assert cursor.fetchall() == [('op',)]


@pytest.mark.parametrize('first', ['status', 'message'])
def test_concurrent_status_and_echo_do_not_lose_receipt(pg, first):
    conn, cursor, database, connect = pg
    db = database()
    other = connect()
    other_db = database(other)
    receipt = [{'messageId': 'm1', 'status': 'read'}]
    if first == 'status':
        db.update_wazzup_statuses(receipt)
        work = lambda: other_db.store_wazzup_messages([message()])
    else:
        db.store_wazzup_messages([message()])
        work = lambda: other_db.update_wazzup_statuses(receipt)
    started, done = threading.Event(), threading.Event()
    failures = []

    def run():
        try:
            started.set()
            work()
            other.commit()
        except Exception as exc:
            failures.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert started.wait(1)
    assert not done.wait(.15), 'second transaction must wait for the first receipt/message'
    conn.commit()
    thread.join(5)
    assert not thread.is_alive()
    assert not failures
    assert status(cursor) == 'read'


def test_notifications_are_transactional_private_and_only_for_changes_in_op(pg):
    conn, cursor, database, connect = pg
    listener = connect()
    listener.autocommit = True
    with listener.cursor() as cur:
        cur.execute('LISTEN wazzup_pilot_events')
    db = database()

    def drain(timeout=.15):
        select.select([listener], [], [], timeout)
        listener.poll()
        events = [json.loads(n.payload) for n in listener.notifies]
        listener.notifies.clear()
        return events

    db.store_wazzup_messages([message()])
    assert drain() == []
    conn.commit()
    events = drain()
    assert len(events) == 1
    assert events[0]['account'] == 'op'
    assert events[0]['messageId'] == 'm1'
    assert set(events[0]) == {'account', 'channelId', 'chatId', 'messageId', 'status', 'emittedAt',
                              'affectsList', 'statusOnly', 'isEcho', 'createdAt'}
    assert events[0]['affectsList'] is True
    assert events[0]['statusOnly'] is False
    assert events[0]['isEcho'] is True
    assert events[0]['createdAt']
    db.store_wazzup_messages([message()])
    db.update_wazzup_statuses([{'messageId': 'm1', 'status': 'sent'}])
    db.store_wazzup_messages([message('potok1')], account='potok')
    conn.commit()
    assert drain() == []
    db.update_wazzup_statuses([{'messageId': 'm1', 'status': 'read'}])
    conn.commit()
    events = drain()
    assert len(events) == 1 and events[0]['status'] == 'read'
    assert events[0]['affectsList'] is False
    assert events[0]['statusOnly'] is True
    db.store_wazzup_messages([message('rolled-back')])
    conn.rollback()
    assert drain() == []
