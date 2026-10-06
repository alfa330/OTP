"""Isolated pilot safety tests: fake database/HTTP, no app or production imports."""
import json
import sqlite3
import threading
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import requests
from flask import Flask, jsonify

from wazzup import pilot
from wazzup.realtime import EventBroker


CHANNEL = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'


def user(login='alfa330', active=True):
    result = [None] * 11
    result[0], result[2], result[7], result[10] = 42, 'Pilot Tester', login, active
    return result


class MemoryDatabase:
    """Transactional outbox fixture; unexpected SQL fails instead of hitting a DB."""

    def __init__(self, actor=None):
        self.actor = user() if actor is None else actor
        self.outbox, self.messages, self.statements = {}, {}, []
        self.lock, self.local = threading.RLock(), threading.local()

    def get_user(self, **_):
        return self.actor

    @contextmanager
    def _get_cursor(self):
        with self.lock:
            self.local.transaction = True
            try:
                yield MemoryCursor(self)
            finally:
                self.local.transaction = False

    def store_wazzup_messages(self, messages, account):
        for message in messages:
            self.messages[message['messageId']] = dict(message, account=account)


class MemoryCursor:
    def __init__(self, db):
        self.db, self.row = db, None

    def execute(self, query, values=()):
        sql = ' '.join(query.split())
        self.db.statements.append((sql, values))
        self.row = None
        if sql.startswith('SELECT account,channel_id'):
            item = self.db.outbox.get(values[0])
            self.row = tuple(item) if item else None
        elif sql.startswith('SELECT chat_type FROM wazzup_chats'):
            self.row = ('whatsapp',)
        elif sql.startswith('INSERT INTO wazzup_pilot_outbox'):
            request_id, channel, chat, text, user_id, _name = values
            if request_id not in self.db.outbox:
                self.db.outbox[request_id] = ['op', channel, chat, text, user_id,
                                              'sending', None, None, None]
                self.row = (request_id,)
        elif sql.startswith('UPDATE wazzup_pilot_outbox SET state='):
            state, message_id, code, explanation, request_id = values
            self.db.outbox[request_id][5:] = [state, message_id, code, explanation]
        elif sql.startswith('SELECT 1 FROM wazzup_messages'):
            self.row = (1,) if values[0] in self.db.messages else None
        else:
            raise AssertionError('Unexpected SQL in isolated fixture: ' + sql)

    def fetchone(self):
        return self.row


class RefreshDatabase:
    """Run the real refresh SELECTs on local SQLite after placeholder adaptation."""

    def __init__(self):
        self.actor = user()
        self.statements = []
        self.connection = sqlite3.connect(':memory:')
        self.connection.executescript('''
            CREATE TABLE wazzup_messages (
                account TEXT, channel_id TEXT, chat_id TEXT, message_id TEXT, dt TEXT,
                is_echo BOOLEAN, type TEXT, text TEXT, content_uri TEXT, author_name TEXT,
                author_id TEXT, status TEXT, is_edited BOOLEAN, is_deleted BOOLEAN, wazzup_dt TEXT
            );
            CREATE TABLE wazzup_pilot_outbox (account TEXT, message_id TEXT, author_name TEXT);
        ''')

    def get_user(self, **_):
        return self.actor

    def add(self, number, *, account='op', channel=CHANNEL, chat='test-chat', **updates):
        dt = datetime(2026, 10, 6, tzinfo=timezone.utc) + timedelta(seconds=number)
        row = dict(account=account, channel_id=channel, chat_id=chat,
                   message_id=f'm-{number}', dt=dt.isoformat(), is_echo=True,
                   type='text', text=f'Message {number}', content_uri=None,
                   author_name=None, author_id=None, status='sent', is_edited=False,
                   is_deleted=False, wazzup_dt=None)
        row.update(updates)
        self.connection.execute('INSERT INTO wazzup_messages VALUES (' + ','.join('?' * 15) + ')',
                                tuple(row.values()))

    @contextmanager
    def _get_cursor(self):
        database = self

        class Cursor:
            def execute(self, query, params):
                database.statements.append((query, params))
                if 'message_id=ANY(%s)' in query:
                    identifiers = params[-1]
                    query = query.replace('message_id=ANY(%s)',
                                          'message_id IN (' + (','.join('?' * len(identifiers)) or 'NULL') + ')')
                    params = list(params[:-1]) + list(identifiers)
                self.cursor = database.connection.execute(query.replace('%s', '?'), params)

            def fetchone(self):
                return self.cursor.fetchone()

            def fetchall(self):
                return self.cursor.fetchall()

        yield Cursor()


def fixture(*, db=None, guard_denied=False, broker=None, transport=None, channels=None):
    db = db or MemoryDatabase()
    broker = broker or EventBroker()
    transport = transport or Mock()
    if transport.post.return_value is not None:
        transport.post.return_value.status_code = 200
        transport.post.return_value.json.return_value = {'messageId': 'vendor-message-1'}
    app = Flask(__name__)
    app.testing = True
    app.register_blueprint(pilot.build_pilot_blueprint(
        db=db, require_api_key=lambda fn: fn,
        guard=lambda: (None, (jsonify(error='forbidden'), 403)) if guard_denied else (42, None),
        channels=channels or (lambda account: [{'channelId': CHANNEL, 'state': 'active',
                                                'transport': 'whatsapp'}]),
        preflight=lambda: ('', 204), listen_connect=lambda: None,
        transport=transport, event_broker=broker,
    ))
    return app, db, broker, transport


class PilotRoutesTests(unittest.TestCase):
    def setUp(self):
        self.api_key = patch.object(pilot.accounts, 'api_key', return_value='isolated-test-key')
        self.api_key.start()
        self.addCleanup(self.api_key.stop)
        self.app, self.db, self.broker, self.transport = fixture()
        self.client = self.app.test_client()
        self.body = dict(account='op', channelId=CHANNEL, chatId='77000000000',
                         text='Local fixture only', clientMessageId=str(uuid.uuid4()))

    def test_only_active_alfa330_can_send_or_stream(self):
        for actor in (user('someone-else'), user(active=False)):
            app, db, broker, transport = fixture(db=MemoryDatabase(actor))
            with app.test_client() as client:
                for method, path in (('post', '/send'), ('get', '/stream'), ('post', '/refresh')):
                    response = getattr(client, method)('/api/wazzup/pilot' + path, json=self.body)
                    self.assertEqual(403, response.status_code)
                capabilities = client.get('/api/wazzup/pilot').get_json()
                self.assertFalse(capabilities['enabled'])
                self.assertFalse(capabilities['canSend'])
            self.assertFalse(db.statements)
            transport.post.assert_not_called()
            self.assertEqual(0, broker.streams)

    def test_existing_section_guard_is_required_even_for_alfa330(self):
        app, db, _, transport = fixture(guard_denied=True)
        client = app.test_client()
        self.assertEqual(403, client.post('/api/wazzup/pilot/send', json=self.body).status_code)
        self.assertEqual(403, client.get('/api/wazzup/pilot/stream').status_code)
        self.assertFalse(db.statements)
        transport.post.assert_not_called()

    def test_global_and_other_account_are_rejected_before_database_or_http(self):
        for channel in pilot.EXCLUDED_CHANNELS:
            response = self.client.post('/api/wazzup/pilot/send', json=dict(self.body, channelId=channel))
            self.assertEqual(403, response.status_code)
        response = self.client.post('/api/wazzup/pilot/send', json=dict(self.body, account='potok'))
        self.assertEqual(403, response.status_code)
        self.assertEqual(403, self.client.get('/api/wazzup/pilot/stream?account=potok').status_code)
        self.assertFalse(self.db.statements)
        self.transport.post.assert_not_called()

    def test_successful_retry_returns_same_result_without_resending(self):
        def send(*args, **kwargs):
            self.assertFalse(getattr(self.db.local, 'transaction', False),
                             'HTTP must not keep the DB transaction open')
            self.assertIn(self.body['clientMessageId'], self.db.outbox,
                          'durable claim must precede vendor HTTP')
            return self.transport.post.return_value

        self.transport.post.side_effect = send
        first = self.client.post('/api/wazzup/pilot/send', json=self.body)
        second = self.client.post('/api/wazzup/pilot/send', json=self.body)
        self.assertEqual(201, first.status_code)
        self.assertEqual(200, second.status_code)
        self.assertEqual(first.get_json()['messageId'], second.get_json()['messageId'])
        self.transport.post.assert_called_once()
        self.assertEqual('pending', self.db.messages['vendor-message-1']['status'])
        payload = self.transport.post.call_args.kwargs['json']
        self.assertEqual(self.body['clientMessageId'], payload['crmMessageId'])
        self.assertEqual(CHANNEL, payload['channelId'])

    def test_reusing_request_id_with_different_message_is_conflict(self):
        self.assertEqual(201, self.client.post('/api/wazzup/pilot/send', json=self.body).status_code)
        response = self.client.post('/api/wazzup/pilot/send', json=dict(self.body, text='Other text'))
        self.assertEqual(409, response.status_code)
        self.assertEqual('REQUEST_CONFLICT', response.get_json()['code'])
        self.transport.post.assert_called_once()

    def test_timeout_is_ambiguous_and_same_request_never_replays(self):
        self.transport.post.side_effect = requests.Timeout('Synthetic timeout')
        for _ in range(2):
            response = self.client.post('/api/wazzup/pilot/send', json=self.body)
            self.assertEqual(409, response.status_code)
            self.assertEqual('unknown', response.get_json()['state'])
            self.assertFalse(response.get_json()['retryable'])
        self.transport.post.assert_called_once()
        self.assertFalse(self.db.messages)

    def test_parallel_retry_while_vendor_is_busy_does_not_send_again(self):
        started, finish = threading.Event(), threading.Event()
        results = []

        def send(*args, **kwargs):
            started.set()
            if not finish.wait(3):
                raise AssertionError('Test transport was not released')
            return self.transport.post.return_value

        def original_request():
            with self.app.test_client() as client:
                results.append(client.post('/api/wazzup/pilot/send', json=self.body).status_code)

        self.transport.post.side_effect = send
        thread = threading.Thread(target=original_request, daemon=True)
        thread.start()
        try:
            self.assertTrue(started.wait(3))
            duplicate = self.client.post('/api/wazzup/pilot/send', json=self.body)
            self.assertEqual(409, duplicate.status_code)
            self.assertEqual('sending', duplicate.get_json()['state'])
            self.transport.post.assert_called_once()
        finally:
            finish.set()
            thread.join(3)
        self.assertEqual([201], results)

    def test_early_echo_read_status_survives_success(self):
        self.db.messages['vendor-message-1'] = {'status': 'read'}
        self.assertEqual(201, self.client.post('/api/wazzup/pilot/send', json=self.body).status_code)
        self.assertEqual('read', self.db.messages['vendor-message-1']['status'])

    def test_stream_sends_event_and_returns_slot_on_close(self):
        response = self.client.get('/api/wazzup/pilot/stream', buffered=False)
        self.assertEqual(200, response.status_code)
        self.assertEqual('no', response.headers['X-Accel-Buffering'])
        iterator = iter(response.response)
        self.assertIn(b'event: connected', next(iterator))
        self.assertEqual(1, self.broker.streams)
        change = dict(account='op', channelId=CHANNEL, chatId='77000000000', messageId='m1', status='read')
        self.broker.publish(change)
        frame = next(iterator).decode()
        self.assertIn('event: change', frame)
        self.assertEqual([change], json.loads(frame.split('data: ', 1)[1])['changes'])
        response.close()
        self.assertEqual(0, self.broker.streams)

    def test_stream_reports_pg_loss_and_requests_reload_when_restored(self):
        response = self.client.get('/api/wazzup/pilot/stream', buffered=False)
        iterator = iter(response.response)
        self.assertIn(b'"ready": false', next(iterator))
        self.broker.set_ready(True)
        self.assertIn(b'event: reload', next(iterator))
        self.broker.set_ready(False)
        self.assertIn(b'event: unavailable', next(iterator))
        self.broker.set_ready(True)
        self.assertIn(b'"ready": true', next(iterator))
        response.close()

    def test_stream_limit_rejects_without_leaking_slots(self):
        for _ in range(pilot.STREAM_LIMIT):
            self.assertTrue(self.broker.acquire(pilot.STREAM_LIMIT))
        response = self.client.get('/api/wazzup/pilot/stream')
        self.assertEqual(503, response.status_code)
        self.assertEqual(pilot.STREAM_LIMIT, self.broker.streams)


class EventBrokerTests(unittest.TestCase):
    def test_idle_stream_does_not_request_reload(self):
        broker = EventBroker()
        self.assertEqual(([], 0), broker.wait(0, timeout=0.001))

    def test_burst_coalesces_latest_status_for_each_message(self):
        broker = EventBroker()
        for status in ('sent', 'delivered', 'read'):
            broker.publish(dict(messageId='one', status=status))
        broker.publish(dict(messageId='two', status='read'))
        events, cursor = broker.wait(0)
        self.assertEqual(4, cursor)
        self.assertEqual({'one': 'read', 'two': 'read'}, {e['messageId']: e['status'] for e in events})

    def test_insert_then_status_in_one_batch_keeps_chat_list_invalidation(self):
        broker = EventBroker()
        broker.publish(dict(messageId='new-message', status='sent', affectsList=True))
        broker.publish(dict(messageId='new-message', status='delivered', affectsList=False))
        events, _ = broker.wait(0)
        self.assertEqual('delivered', events[0]['status'])
        self.assertTrue(events[0]['affectsList'], 'status coalescing must not hide a newly inserted chat')
        # Per-subscriber aggregation must not mutate shared ring entries: a
        # subscriber that already saw the INSERT now needs only the status.
        later_events, _ = broker.wait(1)
        self.assertFalse(later_events[0]['affectsList'])

    def test_missed_ring_or_listener_reconnect_requests_full_refresh(self):
        broker = EventBroker(capacity=2)
        for number in range(3):
            broker.publish({'messageId': str(number)})
        self.assertEqual(([{'reload': True}], 3), broker.wait(0))
        broker.set_ready(True)
        self.assertEqual(([{'reload': True}], 4), broker.wait(3))


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.db = RefreshDatabase()
        self.addCleanup(self.db.connection.close)
        self.app, _, _, _ = fixture(db=self.db)
        self.client = self.app.test_client()
        self.body = dict(account='op', channelId=CHANNEL, chatId='test-chat', messageIds=['m-10'])

    def test_refresh_retains_loaded_range_and_recovers_edits_deletions_and_gaps(self):
        for number in range(1, 81):
            self.db.add(number, status='read' if number == 10 else 'sent',
                        is_edited=number == 10, is_deleted=number == 15)
        response = self.client.post('/api/wazzup/pilot/refresh', json=self.body)
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual([f'm-{n}' for n in range(10, 81)], [m['messageId'] for m in payload['items']])
        self.assertEqual('read', payload['items'][0]['status'])
        self.assertTrue(payload['items'][0]['isEdited'])
        self.assertTrue(payload['items'][5]['isDeleted'])
        self.assertFalse(payload['reset'])

    def test_scope_excludes_other_channel_chat_and_account_in_both_queries(self):
        self.db.add(10)
        self.db.add(11)
        self.db.add(1, account='potok')
        self.db.add(2, channel='other-channel')
        self.db.add(3, chat='other-chat')
        response = self.client.post('/api/wazzup/pilot/refresh',
                                    json=dict(self.body, messageIds=['m-1', 'm-2', 'm-3', 'm-10']))
        self.assertEqual(['m-10', 'm-11'], [m['messageId'] for m in response.get_json()['items']])

    def test_retained_history_cap_resets_to_newest_2000(self):
        for number in range(2005):
            self.db.add(number)
        response = self.client.post('/api/wazzup/pilot/refresh', json=dict(self.body, messageIds=['m-0']))
        payload = response.get_json()
        self.assertTrue(payload['reset'])
        self.assertTrue(payload['hasMore'])
        self.assertEqual(2000, len(payload['items']))
        self.assertEqual('m-5', payload['items'][0]['messageId'])
        self.assertEqual('m-2004', payload['items'][-1]['messageId'])

    def test_without_surviving_cursor_returns_bounded_latest_page(self):
        for number in range(60):
            self.db.add(number)
        response = self.client.post('/api/wazzup/pilot/refresh', json=dict(self.body, messageIds=['expired']))
        payload = response.get_json()
        self.assertEqual(50, len(payload['items']))
        self.assertEqual('m-10', payload['items'][0]['messageId'])
        self.assertTrue(payload['reset'])

    def test_oversized_input_does_not_query_database(self):
        response = self.client.post('/api/wazzup/pilot/refresh',
                                    json=dict(self.body, messageIds=['m-10'] * 2001))
        self.assertEqual(400, response.status_code)
        self.assertFalse(self.db.statements)


if __name__ == '__main__':
    unittest.main()
