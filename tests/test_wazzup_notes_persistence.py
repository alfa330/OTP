"""Private-note transactions and NOTIFY on explicitly selected local PostgreSQL."""
import json
import select
import threading
from contextlib import contextmanager

import pytest

from tests.test_wazzup_notes import CHAT, PATH, body
from tests.test_wazzup_pilot import CHANNEL, fixture, user
from tests.test_wazzup_pilot_persistence import PORT, pg  # noqa: F401
from wazzup.notes_schema import init_schema
from wazzup.realtime import EventBroker, broadcast_changes


pytestmark = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')


def client_for(database):
    database.get_user = lambda **_: user()
    app, _, _, _ = fixture(db=database)
    return app.test_client()


def add_chat(cursor):
    cursor.execute("INSERT INTO wazzup_chats(channel_id,chat_id,account,chat_type) VALUES(%s,%s,'op','whatsapp')",
                   (CHANNEL, CHAT))


def test_schema_retry_and_commit_notify_do_not_touch_wazzup_or_syntony(pg):
    conn, cursor, database, connect = pg
    init_schema(cursor)
    add_chat(cursor)
    conn.commit()
    listener = connect()
    listener.set_session(autocommit=True)
    with listener.cursor() as subscription:
        subscription.execute('LISTEN wazzup_pilot_events')
    client = client_for(database())
    request = body(text='Synthetic confidential internal note')
    first = client.post(PATH, json=request)
    assert first.status_code == 201
    assert not select.select([listener], [], [], 0.05)[0], 'A transaction cannot leak uncommitted notes'
    conn.commit()
    assert select.select([listener], [], [], 1)[0]
    listener.poll()
    assert len(listener.notifies) == 1
    event = json.loads(listener.notifies[0].payload)
    assert event['kind'] == 'note' and event['noteId'] == first.json['item']['id']
    assert 'text' not in event and request['text'] not in listener.notifies[0].payload
    broker = EventBroker()
    broker.acquire()
    broadcast_changes(cursor, [event], broker)
    assert broker.wait(0)[0][0]['note'] == first.json['item']
    second = client.post(PATH, json=request)
    assert second.status_code == 200 and second.json == first.json
    conn.commit()
    listener.notifies.clear()
    assert not select.select([listener], [], [], 0.05)[0]
    for table in ('wazzup_messages', 'wazzup_pilot_outbox', 'wazzup_chat_read_state',
                  'wazzup_unanswered_messages', 'wazzup_syntony_outbox'):
        cursor.execute('SELECT COUNT(*) FROM ' + table)
        assert cursor.fetchone() == (0,), table
    cursor.execute('SELECT messages_count,inbound_count,outbound_count,last_message_at FROM wazzup_chats')
    assert cursor.fetchone() == (0, 0, 0, None)


def test_simultaneous_retries_create_one_note_and_conflicting_payload_stays_original(pg):
    conn, cursor, database, connect = pg
    add_chat(cursor)
    conn.commit()
    request, results, errors = body(), [], []
    barrier = threading.Barrier(3)
    clients = []
    for _ in range(2):
        connection = connect()
        db = database(connection)
        @contextmanager
        def transaction(conn=connection):
            with conn:
                with conn.cursor() as cur:
                    yield cur
        db._get_cursor = transaction
        clients.append(client_for(db))

    def submit(client):
        try:
            barrier.wait(timeout=5)
            response = client.post(PATH, json=request)
            results.append((response.status_code, response.json))
        except Exception as error:
            errors.append(error)

    workers = [threading.Thread(target=submit, args=(client,)) for client in clients]
    for worker in workers:
        worker.start()
    barrier.wait(timeout=5)
    for worker in workers:
        worker.join(timeout=10)
        assert not worker.is_alive()
    assert not errors
    assert sorted(code for code, _ in results) == [200, 201]
    assert results[0][1] == results[1][1]
    assert clients[0].post(PATH, json=request | dict(text='Different')).status_code == 409
    cursor.execute('SELECT COUNT(*),MIN(text) FROM wazzup_chat_notes')
    assert cursor.fetchone() == (1, request['text'])
