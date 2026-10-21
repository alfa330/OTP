"""Attachment idempotency on explicitly selected disposable local PostgreSQL."""
import hashlib
import threading
import uuid
from contextlib import contextmanager
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
import requests

from tests.test_wazzup_pilot import CHANNEL, MemoryDatabase
from tests.test_wazzup_pilot_persistence import PORT, pg  # noqa: F401
from tests.test_wazzup_uploads import (CHAT, PATH, PDF, FakeStorage, client_for, fetch_during_post,
                                       send_body, upload)
from wazzup import pilot, uploads
from wazzup.uploads_schema import init_schema

pytestmark = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')


def database(connection, local=None):
    result = MemoryDatabase()
    if local is not None:
        result.local = local

    @contextmanager
    def cursor():
        result.local.transaction = True
        try:
            with connection:
                with connection.cursor() as cur:
                    yield cur
        finally:
            result.local.transaction = False
    result._get_cursor = cursor
    return result


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.delenv('RENDER_EXTERNAL_URL', raising=False)
    monkeypatch.setattr(pilot.accounts, 'api_key', lambda account: 'synthetic-key')
    monkeypatch.setattr(uploads, 'schedule_cleanup', lambda *args: None)
    uploads._rate.__globals__['_usage'].clear()


def add_chat(connection, cursor):
    cursor.execute("INSERT INTO wazzup_chats(channel_id,chat_id,account,chat_type) VALUES(%s,%s,'op','whatsapp')",
                   (CHANNEL, CHAT))
    connection.commit()


def test_upload_schema_and_real_send_replay_keep_expired_unknown_attempt(pg):
    connection, cursor, _, _ = pg
    init_schema(cursor)  # repeat migrations
    add_chat(connection, cursor)
    db = database(connection)
    storage = FakeStorage(db)
    client, transport = client_for(db, storage)
    identifier = str(uuid.uuid4())
    first = upload(client, client_id=identifier)
    assert first.status_code == 201
    assert upload(client, client_id=identifier).json == first.json
    attachment = first.json['attachment']
    body = send_body(attachment)
    transport.post.side_effect = requests.Timeout('synthetic timeout')
    result = client.post(PATH + '/send', json=body)
    assert result.status_code == 409 and result.json['state'] == 'unknown'
    cursor.execute("UPDATE wazzup_pilot_uploads SET expires_at=now()-interval '1 hour'")
    connection.commit()
    uploads.cleanup_expired(db, storage.config)
    replay = client.post(PATH + '/send', json=body)
    assert replay.status_code == 409 and replay.json['state'] == 'unknown'
    transport.post.assert_called_once()
    cursor.execute('SELECT state,attachment_id,text FROM wazzup_pilot_outbox')
    state, attachment_id, text = cursor.fetchone()
    assert (state, str(attachment_id), text) == ('unknown', attachment['id'], '')
    assert upload(client, client_id=identifier).status_code == 410
    assert not storage.objects
    for table in ('wazzup_messages', 'wazzup_chat_notes', 'wazzup_syntony_outbox'):
        cursor.execute('SELECT COUNT(*) FROM ' + table)
        assert cursor.fetchone() == (0,)


def test_wazzup_downloads_the_file_by_its_link_during_the_send(pg):
    connection, cursor, _, connect = pg
    init_schema(cursor)  # repeat migrations
    connection.commit()
    add_chat(connection, cursor)
    db = database(connection)
    storage = FakeStorage(db)
    client, transport = client_for(db, storage)
    attachment = upload(client, filename='Договор.pdf').json['attachment']
    fetched = []
    # Wazzup's download is another request on another connection: it finds the
    # link only if the claim was committed before the vendor POST.
    fetch_during_post(client_for(database(connect()), storage)[0], transport, fetched)
    assert client.post(PATH + '/send', json=send_body(attachment)).status_code == 201
    [(uri, download)] = fetched
    assert download.status_code == 200 and download.data == PDF
    path = urlsplit(uri).path
    cursor.execute('SELECT link_hash, link_expires_at - now() FROM wazzup_pilot_uploads')
    digest, left = cursor.fetchone()
    connection.commit()
    assert digest == hashlib.sha256(path.split('/')[-2].encode()).hexdigest()
    assert timedelta(minutes=29) < left <= timedelta(minutes=30)
    cursor.execute("UPDATE wazzup_pilot_uploads SET link_expires_at=now()-interval '1 second'")
    connection.commit()
    assert client.get(path).status_code == 404
    cursor.execute("UPDATE wazzup_pilot_uploads SET link_expires_at=now()+interval '1 minute',deleted_at=now()")
    connection.commit()
    assert client.get(path).status_code == 404
    cursor.execute('UPDATE wazzup_pilot_uploads SET deleted_at=NULL')
    connection.commit()
    assert client.get(path).status_code == 200


def test_parallel_uploads_commit_one_file_and_one_outbox_send(pg):
    connection, cursor, _, connect = pg
    add_chat(connection, cursor)
    shared_local = threading.local()
    dbs = [database(connect(), shared_local) for _ in range(2)]
    storage = FakeStorage(dbs[0])
    storage.barrier = threading.Barrier(2)
    clients_transports = [client_for(db, storage) for db in dbs]
    identifier = str(uuid.uuid4())
    responses, failures = [], []

    def perform(client):
        try:
            responses.append(upload(client, client_id=identifier))
        except Exception as error:
            failures.append(error)

    threads = [threading.Thread(target=perform, args=(client,)) for client, _ in clients_transports]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert not failures
    assert sorted(response.status_code for response in responses) == [200, 201]
    assert responses[0].json == responses[1].json
    assert len(storage.objects) == 1 and len(storage.deletes) == 1
    cursor.execute('SELECT COUNT(*) FROM wazzup_pilot_uploads')
    assert cursor.fetchone() == (1,)
    body = send_body(responses[0].json['attachment'])
    # Concurrent sends share the immutable file but only one outbox claim wins.
    responses.clear()
    gate = threading.Barrier(2)
    def send(client):
        gate.wait(timeout=5)
        responses.append(client.post(PATH + '/send', json=body))
    threads = [threading.Thread(target=send, args=(client,)) for client, _ in clients_transports]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert any(response.status_code == 201 for response in responses)
    assert all(response.status_code in (200, 201, 409) for response in responses)
    assert sum(transport.post.call_count for _, transport in clients_transports) == 1
    cursor.execute('SELECT COUNT(*) FROM wazzup_pilot_outbox')
    assert cursor.fetchone() == (1,)
