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


def user(role='super_admin', active=True, status='working'):
    # Without an injected access policy only super admins may process chats.
    result = [None] * 20
    result[0], result[2], result[7], result[10] = 42, 'Pilot Tester', 'pilot-tester', active
    result[3], result[11] = role, status
    return result


class MemoryDatabase:
    """Transactional outbox fixture; unexpected SQL fails instead of hitting a DB."""

    def __init__(self, actor=None):
        self.actor = user() if actor is None else actor
        self.outbox, self.messages, self.statements = {}, {}, []
        self.outbox_authors, self.author_map = {}, {}
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
            request_id, channel, chat, text, user_id, _name, reply_to, attachment_id, author_id = values
            if request_id not in self.db.outbox:
                self.db.outbox[request_id] = ['op', channel, chat, text, user_id,
                                              'sending', None, None, None, reply_to, attachment_id]
                self.db.outbox_authors[request_id] = author_id
                self.row = (request_id,)
        elif sql.startswith('INSERT INTO wazzup_operator_map'):
            author_id, name, user_id, _updated_by = values
            self.db.author_map.setdefault(author_id, (name, user_id))
        elif sql.startswith('UPDATE wazzup_messages SET author_id='):
            author_id, name, message_id = values
            self.db.messages[message_id].update(authorId=author_id, authorName=name)
        elif sql.startswith('UPDATE wazzup_pilot_outbox SET state='):
            state, message_id, code, explanation, request_id = values
            self.db.outbox[request_id][5:9] = [state, message_id, code, explanation]
        elif sql.startswith("SELECT 1 FROM wazzup_messages WHERE account='op'"):
            item = self.db.messages.get(values[2])
            self.row = (1,) if item and item.get('account') == 'op' and item.get('channelId') == values[0] and item.get('chatId') == values[1] and not item.get('isDeleted') else None
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
            CREATE TABLE wazzup_pilot_outbox (account TEXT, message_id TEXT, author_name TEXT, reply_to_message_id TEXT, request_id TEXT);
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


def fixture(*, db=None, guard_denied=False, broker=None, transport=None, channels=None, access=None):
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
        transport=transport, event_broker=broker, access=access,
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

    def test_only_active_super_admin_can_send_or_stream_without_a_policy(self):
        for actor in (user('admin'), user('sv'), user('operator'),
                      user(status='fired'), user(status='dismissal')):
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

    def test_admin_off_operator_shift_can_use_pilot(self):
        self.db.actor = user(active=False)
        self.assertTrue(self.client.get('/api/wazzup/pilot').get_json()['enabled'])
        self.assertEqual(201, self.client.post('/api/wazzup/pilot/send', json=self.body).status_code)
        self.transport.post.assert_called_once()

    def test_existing_section_guard_is_required_even_for_a_super_admin(self):
        app, db, _, transport = fixture(guard_denied=True)
        client = app.test_client()
        self.assertEqual(403, client.post('/api/wazzup/pilot/send', json=self.body).status_code)
        self.assertEqual(403, client.get('/api/wazzup/pilot/stream').status_code)
        self.assertFalse(db.statements)
        transport.post.assert_not_called()

    def test_injected_access_policy_decides_instead_of_the_role(self):
        # The monolith's rule is the single source: a verifier with a confirmed
        # session passes, a super admin the policy refuses does not.
        for role, can_process, expected in (('operator', True, 201), ('super_admin', False, 403)):
            app, db, _, transport = fixture(
                db=MemoryDatabase(user(role)),
                access=lambda can_process=can_process: {'mode': 'full', 'can_process': can_process})
            with app.test_client() as client:
                self.assertEqual(expected, client.post('/api/wazzup/pilot/send', json=self.body).status_code)
                self.assertEqual(can_process, client.get('/api/wazzup/pilot').get_json()['enabled'])
            self.assertEqual(1 if can_process else 0, transport.post.call_count)

    def test_verifier_send_is_credited_to_the_sender(self):
        # Wazzup echoes API sends as "Admin" with no author id; the reports would
        # credit nobody. The claim carries our author key and binds it once.
        app, db, _, _ = fixture(db=MemoryDatabase(user('operator')),
                                access=lambda: {'mode': 'operator', 'can_process': True})
        with app.test_client() as client:
            self.assertEqual(201, client.post('/api/wazzup/pilot/send', json=self.body).status_code)
        self.assertEqual('icore:42', db.outbox_authors[self.body['clientMessageId']])
        self.assertEqual({'icore:42': ('Pilot Tester', 42)}, db.author_map)
        stored = db.messages['vendor-message-1']
        self.assertEqual(('icore:42', 'Pilot Tester'), (stored['authorId'], stored['authorName']))

    def test_verifier_send_stamps_an_echo_that_arrived_first(self):
        app, db, _, _ = fixture(db=MemoryDatabase(user('operator')),
                                access=lambda: {'mode': 'operator', 'can_process': True})
        db.messages['vendor-message-1'] = {'status': 'read', 'authorName': 'Admin', 'authorId': None}
        with app.test_client() as client:
            self.assertEqual(201, client.post('/api/wazzup/pilot/send', json=self.body).status_code)
        stored = db.messages['vendor-message-1']
        self.assertEqual(('icore:42', 'Pilot Tester', 'read'),
                         (stored['authorId'], stored['authorName'], stored['status']))

    def test_super_admin_send_stays_out_of_the_reports(self):
        # As before the change: no author key, no mapping row, no stamp.
        app, db, _, _ = fixture(access=lambda: {'mode': 'full', 'can_process': True})
        db.messages['vendor-message-1'] = {'status': 'sent', 'authorName': 'Admin', 'authorId': None}
        with app.test_client() as client:
            self.assertEqual(201, client.post('/api/wazzup/pilot/send', json=self.body).status_code)
        self.assertIsNone(db.outbox_authors[self.body['clientMessageId']])
        self.assertFalse(db.author_map)
        self.assertIsNone(db.messages['vendor-message-1']['authorId'])

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

    def test_reply_is_scoped_to_chat_and_immutable_on_retry(self):
        self.db.messages['original'] = dict(account='op', channelId=CHANNEL, chatId=self.body['chatId'])
        body = dict(self.body, replyToMessageId='original')
        self.assertEqual(201, self.client.post('/api/wazzup/pilot/send', json=body).status_code)
        self.assertEqual('original', self.transport.post.call_args.kwargs['json']['refMessageId'])
        self.assertEqual(200, self.client.post('/api/wazzup/pilot/send', json=body).status_code)
        self.assertEqual(409, self.client.post('/api/wazzup/pilot/send', json=self.body).status_code)
        self.assertEqual(409, self.client.post('/api/wazzup/pilot/send', json=dict(body,replyToMessageId='other')).status_code)
        self.transport.post.assert_called_once()

    def test_reply_rejects_cross_chat_deleted_and_invalid_targets(self):
        for item in (dict(account='potok', channelId=CHANNEL, chatId=self.body['chatId']),
                     dict(account='op', channelId=CHANNEL, chatId='another'),
                     dict(account='op', channelId=CHANNEL, chatId=self.body['chatId'],isDeleted=True)):
            self.db.messages['original'] = item
            self.assertEqual(400, self.client.post('/api/wazzup/pilot/send',json=dict(self.body,replyToMessageId='original')).status_code)
        for invalid in ([], {}, 4, 'x'*201):
            self.assertEqual(400, self.client.post('/api/wazzup/pilot/send',json=dict(self.body,replyToMessageId=invalid)).status_code)
        self.transport.post.assert_not_called()

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

    @staticmethod
    def frame_parts(frame):
        lines = frame.decode().strip().split('\n')
        fields = dict(line.split(': ', 1) for line in lines if not line.startswith(':'))
        return fields.get('id'), fields.get('event'), json.loads(fields.get('data', 'null'))

    def test_reconnect_resumes_after_the_last_seen_frame_and_loses_nothing(self):
        response = self.client.get('/api/wazzup/pilot/stream', buffered=False)
        frames = iter(response.response)
        _, event, connected = self.frame_parts(next(frames))
        self.assertEqual(('connected', False), (event, connected['resumed']))
        seen = dict(account='op', channelId=CHANNEL, chatId='77000000000', messageId='seen', status='sent')
        self.broker.publish(seen)
        last_id, event, payload = self.frame_parts(next(frames))
        self.assertEqual(('change', [seen]), (event, payload['changes']))
        response.close()
        # Published while the browser was reconnecting (the 120 s re-auth cycle).
        missed = [dict(seen, messageId='missed', status='pending'), dict(seen, status='delivered')]
        for change in missed:
            self.broker.publish(change)
        response = self.client.get(f'/api/wazzup/pilot/stream?epoch={connected["epoch"]}&after={last_id}',
                                   buffered=False)
        frames = iter(response.response)
        _, event, resumed = self.frame_parts(next(frames))
        self.assertEqual(('connected', True, int(last_id)), (event, resumed['resumed'], resumed['seq']))
        frame_id, event, payload = self.frame_parts(next(frames))
        self.assertEqual('change', event)
        self.assertEqual({'missed': 'pending', 'seen': 'delivered'},
                         {change['messageId']: change['status'] for change in payload['changes']})
        self.assertEqual(self.broker.current_seq(), int(frame_id))
        response.close()
        self.assertEqual(0, self.broker.streams)

    def test_cursor_of_another_process_or_beyond_the_buffer_reconciles(self):
        broker = EventBroker(capacity=2)
        app, _, _, _ = fixture(broker=broker)
        client = app.test_client()
        for number in range(4):
            broker.publish({'messageId': str(number)})
        for query, label in ((f'epoch=old-process&after=3', 'restarted process'),
                             (f'epoch={broker.epoch}&after=1', 'cursor older than the buffer'),
                             (f'epoch={broker.epoch}&after=9', 'cursor from the future'),
                             (f'epoch={broker.epoch}&after=x', 'malformed cursor'),
                             ('', 'first connection')):
            with self.subTest(label):
                response = client.get('/api/wazzup/pilot/stream?' + query, buffered=False)
                _, event, connected = self.frame_parts(next(iter(response.response)))
                self.assertEqual(('connected', False, 4), (event, connected['resumed'], connected['seq']))
                response.close()
        response = client.get(f'/api/wazzup/pilot/stream?epoch={broker.epoch}&after=2', buffered=False)
        _, _, connected = self.frame_parts(next(iter(response.response)))
        self.assertTrue(connected['resumed'], 'the oldest buffered point is still resumable')
        response.close()

    def test_reload_and_unavailable_frames_carry_the_cursor_too(self):
        response = self.client.get('/api/wazzup/pilot/stream', buffered=False)
        frames = iter(response.response)
        next(frames)
        self.broker.set_ready(True)
        frame_id, event, _ = self.frame_parts(next(frames))
        self.assertEqual(('reload', self.broker.current_seq()), (event, int(frame_id)))
        self.broker.set_ready(False)
        frame_id, event, _ = self.frame_parts(next(frames))
        self.assertEqual(('unavailable', self.broker.current_seq()), (event, int(frame_id)))
        response.close()

    def test_change_frames_keep_cyrillic_readable_and_compact(self):
        response = self.client.get('/api/wazzup/pilot/stream', buffered=False)
        frames = iter(response.response)
        next(frames)
        self.broker.publish(dict(account='op', channelId=CHANNEL, chatId='77000000000', messageId='m1',
                                 message={'messageId': 'm1', 'text': 'Здравствуйте'}))
        raw = next(frames)
        self.assertIn('Здравствуйте'.encode('utf-8'), raw)
        self.assertNotIn(b'\\u0417', raw)
        response.close()

    def test_a_too_long_gap_is_reconciled_instead_of_replayed_as_one_huge_frame(self):
        from wazzup import realtime
        broker = EventBroker(capacity=realtime.RESUME_MAX_EVENTS + 50)
        for number in range(realtime.RESUME_MAX_EVENTS + 10):
            broker.publish({'messageId': str(number)})
        self.assertEqual((broker.seq, False), broker.resume_from(broker.epoch, broker.seq - realtime.RESUME_MAX_EVENTS - 1))
        self.assertEqual((broker.seq - realtime.RESUME_MAX_EVENTS, True),
                         broker.resume_from(broker.epoch, broker.seq - realtime.RESUME_MAX_EVENTS))

    def test_every_process_has_its_own_epoch(self):
        self.assertNotEqual(EventBroker().epoch, EventBroker().epoch)

    def test_missing_send_key_is_a_definite_failure_not_a_lost_answer(self):
        with patch.object(pilot.accounts, 'api_key', return_value=''):
            response = self.client.post('/api/wazzup/pilot/send', json=self.body)
        self.assertEqual(503, response.status_code)
        self.assertEqual(('failed', 'SEND_KEY_MISSING'), (response.get_json()['state'], response.get_json()['code']))
        self.transport.post.assert_not_called()
        self.assertEqual({}, self.db.outbox)

    def test_send_reports_its_stages_in_server_timing(self):
        timings = []

        @self.app.after_request
        def capture(response):
            from flask import g
            timings.extend(name for name, _ in getattr(g, 'server_timings', []))
            return response

        self.assertEqual(201, self.client.post('/api/wazzup/pilot/send', json=self.body).status_code)
        self.assertEqual(['wz-access', 'wz-checks', 'wz-post', 'wz-store'], timings)

    def test_default_transport_is_the_shared_keep_alive_pool(self):
        response = Mock(status_code=201)
        response.json.return_value = {'messageId': 'pooled-message'}
        with patch.object(pilot.wazzup_transport, 'post', return_value=response) as post:
            app = Flask(__name__)
            app.register_blueprint(pilot.build_pilot_blueprint(
                db=MemoryDatabase(), require_api_key=lambda fn: fn, guard=lambda: (42, None),
                channels=lambda account: [{'channelId': CHANNEL, 'state': 'active', 'transport': 'whatsapp'}],
                preflight=lambda: ('', 204), event_broker=EventBroker()))
            sent = app.test_client().post('/api/wazzup/pilot/send', json=self.body)
        self.assertEqual(201, sent.status_code)
        post.assert_called_once()
        self.assertEqual('https://api.wazzup24.com/v3/message', post.call_args.args[0])


class KeepAliveTransportTests(unittest.TestCase):
    def test_reuses_one_pool_while_warm_and_replaces_it_after_idle(self):
        sessions = []

        def factory():
            sessions.append(Mock())
            return sessions[-1]

        transport = pilot.KeepAliveTransport(idle_seconds=45, factory=factory)
        with patch.object(pilot.time, 'monotonic', side_effect=[100.0, 130.0, 176.0, 177.0]):
            for _ in range(4):
                transport.post('https://api.wazzup24.com/v3/message', json={}, timeout=(5, 20))
        # 100 → 130 reuses (30 s idle); 130 → 176 exceeds 45 s: a new pool, reused at 177.
        self.assertEqual(2, len(sessions))
        self.assertEqual(2, sessions[0].post.call_count)
        self.assertEqual(2, sessions[1].post.call_count)
        sessions[0].close.assert_not_called()

    def test_every_use_keeps_the_pool_warm(self):
        sessions = []

        def factory():
            sessions.append(Mock())
            return sessions[-1]

        transport = pilot.KeepAliveTransport(idle_seconds=45, factory=factory)
        with patch.object(pilot.time, 'monotonic', side_effect=[100.0, 140.0, 180.0, 220.0]):
            for _ in range(4):
                transport.post('https://api.wazzup24.com/v3/message', json={}, timeout=(5, 20))
        # 40 s between sends, 120 s in all: still one pool, because each send refreshes it.
        self.assertEqual(1, len(sessions))
        self.assertEqual(4, sessions[0].post.call_count)

    def test_vendor_idle_limit_is_above_the_reuse_window(self):
        # Measured 09.10.2026: the vendor still answered after 65 s idle and closed at 90 s.
        self.assertLess(pilot.WAZZUP_IDLE_REUSE_SECONDS, 65)

    def test_the_pool_keeps_one_idle_connection_so_none_ages_unseen(self):
        # urllib3 hands pooled connections out LIFO: with more than one slot a
        # buried connection could be reused long after the pool's last use.
        adapter = pilot.wazzup_session().get_adapter(pilot.WAZZUP_API_ORIGIN + '/v3/message')
        self.assertEqual(1, adapter._pool_maxsize)
        self.assertIs(pilot.wazzup_session, pilot.KeepAliveTransport().__dict__['_factory'])

    def test_only_the_expected_one_slot_warning_is_silenced(self):
        import logging
        quiet = pilot._OneSlotPoolFilter()

        def record(host):
            return logging.LogRecord('urllib3.connectionpool', logging.WARNING, __file__, 1,
                                     'Connection pool is full, discarding connection: %s. Connection pool size: %s',
                                     (host, 1), None)

        self.assertFalse(quiet.filter(record('api.wazzup24.com')))
        self.assertTrue(quiet.filter(record('example.org')))
        self.assertIn(quiet.__class__, {type(f) for f in logging.getLogger('urllib3.connectionpool').filters})


class MessageItemTests(unittest.TestCase):
    def test_client_message_id_is_serialised_for_json(self):
        request_id = uuid.uuid4()
        item = pilot.message_item(['m'] + [None] * 14 + [request_id])
        self.assertEqual(str(request_id), item['clientMessageId'])
        self.assertEqual(len(pilot.MESSAGE_FIELDS), len(pilot.message_item([None] * 16)))


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

    def test_iCORE_send_carries_its_client_id_only_into_its_own_chat(self):
        self.db.add(10)
        self.db.add(11)
        self.db.add(12, account='potok')
        self.db.connection.executemany('INSERT INTO wazzup_pilot_outbox VALUES (?,?,?,?,?)', [
            ('op', 'm-10', 'Оператор', None, 'client-id-10'),
            ('op', 'm-12', 'Чужой аккаунт', None, 'client-id-12')])
        items = self.client.post('/api/wazzup/pilot/refresh', json=self.body).get_json()['items']
        self.assertEqual({'m-10': 'client-id-10', 'm-11': None},
                         {item['messageId']: item['clientMessageId'] for item in items})


if __name__ == '__main__':
    unittest.main()
