"""Global forwarding regression tests on disposable localhost Postgres only.

WAZZUP_PILOT_TEST_PORT selects an isolated cluster, never application credentials.
The imported fixture creates and drops one random schema per test.
"""
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from tests.test_wazzup_pilot_persistence import PORT, pg  # noqa: F401
from tests.test_wazzup_syntony import GLOBAL, OTHER, message, relay_env  # noqa: F401
from wazzup import syntony, syntony_schema


pytestmark = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')


class Database:
    def __init__(self, connection):
        self.connection = connection
        self.in_transaction = False

    @contextmanager
    def _get_cursor(self):
        assert not self.in_transaction, 'nested transactions are not expected'
        self.in_transaction = True
        try:
            with self.connection.cursor() as cursor:
                yield cursor
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        finally:
            self.in_transaction = False


@pytest.fixture
def relay(pg, relay_env, monkeypatch):
    connection, cursor, _, connect = pg
    syntony_schema.init_schema(cursor)
    connection.commit()
    monkeypatch.setattr(syntony, 'start_worker', lambda *_args, **_kwargs: None)
    return Database(connection), cursor, connect


def response(status=200, headers=None):
    return SimpleNamespace(status_code=status, headers=headers or {}, close=Mock())


def send_transport(db, sent, status=200):
    def send(url, **kwargs):
        assert not db.in_transaction, 'HTTP must not hold a database connection/transaction'
        sent.append((url, kwargs))
        return response(status)
    return send


def rows(db):
    with db._get_cursor() as cursor:
        cursor.execute('''SELECT phase,payload,attempts,last_error FROM wazzup_syntony_outbox
                          ORDER BY id''')
        return [(phase, bytes(payload) if payload is not None else None, attempts, error)
                for phase, payload, attempts, error in cursor.fetchall()]


def stored_message(db, message_id, channel, account='op'):
    with db._get_cursor() as cursor:
        cursor.execute('''INSERT INTO wazzup_messages
            (message_id,channel_id,chat_id,dt,is_echo,account)
            VALUES (%s,%s,'70000000000',now(),TRUE,%s)''', (message_id, channel, account))


def due(db):
    with db._get_cursor() as cursor:
        cursor.execute('''UPDATE wazzup_syntony_outbox SET next_attempt_at=now()-interval '1 second'
                          WHERE phase IN ('ready','resolve')''')


def test_schema_is_idempotent_and_send_preserves_original_global_json_bytes(relay):
    db, _, _ = relay
    with db._get_cursor() as cursor:
        syntony_schema.init_schema(cursor)
    payload = {'messages': [message(text='Тестовый текст')]}
    raw = ('\n  ' + json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    assert syntony.enqueue(db, payload, raw_body=raw) == 1
    sent = []
    assert syntony.process_once(db, transport=send_transport(db, sent))
    assert len(sent) == 1
    url, kwargs = sent[0]
    assert url == 'https://relay.example.test/wazzup'
    assert kwargs['data'] == raw
    assert kwargs['headers']['Content-Type'] == 'application/json'
    assert kwargs['headers']['Authorization'] == 'Bearer synthetic-syntony-secret'
    assert kwargs['allow_redirects'] is False
    assert isinstance(kwargs['timeout'], tuple) and max(kwargs['timeout']) <= 15
    assert not syntony.process_once(db, transport=send_transport(db, sent))
    assert any(phase == 'done' for phase, *_ in rows(db))


def test_duplicate_webhook_has_one_delivery_and_no_replay_after_success(relay):
    db, _, _ = relay
    payload = {'messages': [message()]}
    assert syntony.enqueue(db, payload) == 1
    assert syntony.enqueue(db, payload) == 0
    sent = []
    assert syntony.process_once(db, transport=send_transport(db, sent))
    assert syntony.enqueue(db, payload) == 0
    assert not syntony.process_once(db, transport=send_transport(db, sent))
    assert len(sent) == 1


@pytest.mark.parametrize('account,payload', [
    ('potok', {'messages': [message()]}),
    ('op', {'messages': [message(channel=OTHER)]}),
    ('unknown', {'messages': [message()]}),
])
def test_other_accounts_and_channels_never_queue_delivery(relay, account, payload):
    db, _, _ = relay
    assert syntony.enqueue(db, payload, account=account) == 0
    send = Mock(side_effect=AssertionError('non-Global data must not leave the service'))
    assert not syntony.process_once(db, transport=send)
    send.assert_not_called()
    assert not any(phase in ('ready', 'resolve') for phase, *_ in rows(db))


def test_status_only_payload_uses_account_scoped_database_ownership(relay):
    db, _, _ = relay
    stored_message(db, 'op-global', GLOBAL)
    stored_message(db, 'op-private', OTHER)
    stored_message(db, 'potok-global-collision', GLOBAL, account='potok')
    payload = {'statuses': [{'messageId': 'op-global', 'status': 'read'},
                            {'messageId': 'op-private', 'status': 'delivered'},
                            {'messageId': 'potok-global-collision', 'status': 'error'}]}
    syntony.enqueue(db, payload)
    sent = []
    for _ in range(4):
        if not syntony.process_once(db, transport=send_transport(db, sent)):
            break
    assert len(sent) == 1
    forwarded = json.loads(sent[0][1]['data'])
    assert forwarded == {'statuses': payload['statuses'][:1]}


def test_early_status_waits_then_resolves_global_and_drops_other_without_duplicate(relay):
    db, _, _ = relay
    payload = {'statuses': [{'messageId': 'arrives-global-later', 'status': 'read'},
                            {'messageId': 'arrives-other-later', 'status': 'error'}]}
    assert syntony.enqueue(db, payload) == 1
    sent = []
    assert syntony.process_once(db, transport=send_transport(db, sent))
    assert sent == []
    assert any(phase == 'resolve' for phase, *_ in rows(db))
    stored_message(db, 'arrives-global-later', GLOBAL)
    stored_message(db, 'arrives-other-later', OTHER)
    due(db)
    for _ in range(4):
        if not syntony.process_once(db, transport=send_transport(db, sent)):
            break
    assert len(sent) == 1
    assert json.loads(sent[0][1]['data']) == {'statuses': payload['statuses'][:1]}
    assert syntony.enqueue(db, payload) == 0
    assert not syntony.process_once(db, transport=send_transport(db, sent))


def test_timeout_is_retried_later_with_same_idempotency_key_and_no_secret_in_error(relay):
    db, _, _ = relay
    syntony.enqueue(db, {'messages': [message()]})
    calls = []

    def timeout(_url, **kwargs):
        assert not db.in_transaction
        calls.append(kwargs)
        raise requests.Timeout('synthetic-syntony-secret must not enter the DB error')

    assert syntony.process_once(db, transport=timeout)
    pending = [row for row in rows(db) if row[0] == 'ready']
    assert len(pending) == 1 and pending[0][2] >= 1
    assert 'synthetic-syntony-secret' not in str(pending[0][3])
    assert not syntony.process_once(db, transport=timeout)
    due(db)
    sent = []
    assert syntony.process_once(db, transport=send_transport(db, sent))
    assert len(sent) == 1
    assert sent[0][1]['headers']['Idempotency-Key'] == calls[0]['headers']['Idempotency-Key']


@pytest.mark.parametrize('status', [429, 500, 503])
def test_temporary_http_failure_remains_durable_for_retry(relay, status):
    db, _, _ = relay
    syntony.enqueue(db, {'messages': [message()]})
    sent = []
    assert syntony.process_once(db, transport=send_transport(db, sent, status))
    assert any(phase == 'ready' and attempts >= 1 for phase, _, attempts, _ in rows(db))
    assert not syntony.process_once(db, transport=send_transport(db, sent))


def test_redirect_does_not_forward_credentials_to_another_host(relay):
    db, _, _ = relay
    syntony.enqueue(db, {'messages': [message()]})
    calls = []

    def redirect(url, **kwargs):
        calls.append((url, kwargs))
        assert kwargs['allow_redirects'] is False
        return response(302, {'Location': 'https://different.example.test/capture'})

    assert syntony.process_once(db, transport=redirect)
    assert len(calls) == 1
    assert calls[0][0] == 'https://relay.example.test/wazzup'
    assert not syntony.process_once(db, transport=redirect)


def test_claim_is_committed_before_http_and_another_worker_cannot_duplicate_it(relay):
    db, _, connect = relay
    other = Database(connect())
    syntony.enqueue(db, {'messages': [message()]})
    sent = []

    def send(url, **kwargs):
        assert not db.in_transaction
        with other._get_cursor() as cursor:
            cursor.execute('''SELECT COUNT(*) FROM wazzup_syntony_outbox
                              WHERE phase='ready' AND lease_token IS NOT NULL AND lease_until>now()''')
            assert cursor.fetchone()[0] == 1, 'claim must be visible to other workers before HTTP'
        assert not syntony.process_once(other, transport=Mock(side_effect=AssertionError('duplicate send')))
        sent.append((url, kwargs))
        return response()

    assert syntony.process_once(db, transport=send)
    assert len(sent) == 1


def test_expired_lease_recovers_a_claim_after_worker_crash(relay):
    db, _, _ = relay
    syntony.enqueue(db, {'messages': [message()]})
    with db._get_cursor() as cursor:
        cursor.execute('''UPDATE wazzup_syntony_outbox SET
            lease_token='00000000-0000-0000-0000-000000000001',
            lease_until=now()-interval '1 second' WHERE phase='ready' ''')
    sent = []
    assert syntony.process_once(db, transport=send_transport(db, sent))
    assert len(sent) == 1


def test_custom_api_key_header_contains_only_syntony_key(relay, monkeypatch):
    db, _, _ = relay
    monkeypatch.setenv('SYNTONY_WEBHOOK_AUTH_HEADER', 'X-API-Key')
    monkeypatch.setenv('SYNTONY_WEBHOOK_AUTH_PREFIX', '')
    monkeypatch.setenv('WAZZUP_API_KEY', 'do-not-share-wazzup-key')
    syntony.enqueue(db, {'messages': [message()]})
    sent = []
    assert syntony.process_once(db, transport=send_transport(db, sent))
    headers = sent[0][1]['headers']
    assert headers['X-API-Key'] == 'synthetic-syntony-secret'
    assert 'Authorization' not in headers
    assert 'do-not-share-wazzup-key' not in str(headers)


def test_receipt_and_delivery_rollback_together_so_a_database_error_cannot_lose_event(relay):
    db, _, _ = relay
    with db._get_cursor() as cursor:
        cursor.execute('''
            CREATE FUNCTION reject_test_delivery() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.phase='ready' THEN RAISE EXCEPTION 'synthetic insert failure'; END IF;
                RETURN NEW;
            END; $$;
            CREATE TRIGGER reject_test_delivery BEFORE INSERT ON wazzup_syntony_outbox
                FOR EACH ROW EXECUTE FUNCTION reject_test_delivery();
        ''')
    payload = {'messages': [message()]}
    with pytest.raises(Exception, match='synthetic insert failure'):
        syntony.enqueue(db, payload)
    assert rows(db) == [], 'failed delivery must not leave a dedup marker that loses the retry'
    with db._get_cursor() as cursor:
        cursor.execute('DROP TRIGGER reject_test_delivery ON wazzup_syntony_outbox')
    assert syntony.enqueue(db, payload) == 1
    sent = []
    assert syntony.process_once(db, transport=send_transport(db, sent))
    assert len(sent) == 1


def test_disabling_relay_preserves_already_queued_delivery(relay, monkeypatch):
    db, _, _ = relay
    syntony.enqueue(db, {'messages': [message()]})
    before = rows(db)
    monkeypatch.setenv('SYNTONY_WEBHOOK_ENABLED', 'false')
    send = Mock(side_effect=AssertionError('disabled relay must not deliver'))
    assert not syntony.process_once(db, transport=send)
    assert rows(db) == before
    send.assert_not_called()
    monkeypatch.setenv('SYNTONY_WEBHOOK_ENABLED', 'true')
    sent = []
    assert syntony.process_once(db, transport=send_transport(db, sent))
    assert len(sent) == 1


def test_status_with_unresolved_channel_expires_without_leaking_payload(relay):
    db, _, _ = relay
    syntony.enqueue(db, {'statuses': [{'messageId': 'never-known', 'status': 'read'}]})
    with db._get_cursor() as cursor:
        cursor.execute("UPDATE wazzup_syntony_outbox SET created_at=now()-interval '73 hours'")
    send = Mock(side_effect=AssertionError('unknown channel must never be delivered'))
    assert syntony.process_once(db, transport=send)
    assert not syntony.process_once(db, transport=send)
    send.assert_not_called()
    assert not any(phase in ('ready', 'resolve') for phase, *_ in rows(db))
    assert all(payload is None for _, payload, *_ in rows(db))


def test_syntony_retry_after_does_not_cause_immediate_retries(relay):
    db, _, _ = relay
    syntony.enqueue(db, {'messages': [message()]})
    reply = response(429, {'Retry-After': '120'})
    assert syntony.process_once(db, transport=lambda *_args, **_kwargs: reply)
    reply.close.assert_called_once()
    with db._get_cursor() as cursor:
        cursor.execute("""SELECT EXTRACT(EPOCH FROM next_attempt_at-now())
                          FROM wazzup_syntony_outbox WHERE phase='ready' """)
        delay = float(cursor.fetchone()[0])
    assert 110 <= delay <= 125
