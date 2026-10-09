"""Presence is authenticated, bounded and transient; no external connections."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest
from flask import Flask, jsonify

from tests.test_wazzup_pilot import CHANNEL, fixture, user
from wazzup import pilot
from wazzup.realtime import CHANNEL as NOTIFY_CHANNEL, EventBroker, broadcast_changes
from wazzup.typing import TTL_MS, TypingLimiter, normalize_event

PATH = '/api/wazzup/pilot/typing'
CHAT = '77000000000'
CLIENT = '11111111-2222-3333-4444-555555555555'


def body(**updates):
    return dict(account='op', channelId=CHANNEL, chatId=CHAT, clientId=CLIENT, typing=True) | updates


def event(**updates):
    now = int(time.time() * 1000)
    return dict(body(), kind='typing', userId='42', authorName='Pilot Tester',
                emittedAt=now, expiresAt=now + TTL_MS) | updates


class TypingDatabase:
    """Run the actual scoped existence/NOTIFY SELECT against isolated SQLite."""
    def __init__(self, actor=None):
        self.actor = user() if actor is None else actor
        self.statements, self.notifications = [], []
        self.connection = sqlite3.connect(':memory:')
        self.connection.execute('CREATE TABLE wazzup_chats '
                                '(account TEXT, channel_id TEXT, chat_id TEXT, chat_type TEXT)')
        self.connection.execute('INSERT INTO wazzup_chats VALUES (?,?,?,?)',
                                ('op', CHANNEL, CHAT, 'whatsapp'))
        self.connection.create_function('pg_notify', 2,
            lambda channel, payload: self.notifications.append((channel, json.loads(payload))))

    def get_user(self, **_):
        return self.actor

    @contextmanager
    def _get_cursor(self):
        with self.connection:
            yield self

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        self.cursor = self.connection.execute(sql.replace('%s', '?'), params)

    def fetchone(self):
        return self.cursor.fetchone()


@pytest.fixture
def typing_client():
    db = TypingDatabase()
    app, _, broker, transport = fixture(db=db)
    yield app.test_client(), db, broker, transport
    db.connection.close()


@pytest.mark.parametrize('actor', [user('operator'), user('other'), user(status='fired')])
def test_ineligible_actors_cannot_publish(actor):
    db = TypingDatabase(actor)
    try:
        app, _, _, transport = fixture(db=db)
        assert app.test_client().post(PATH, json=body()).status_code == 403
        assert db.statements == []
        transport.post.assert_not_called()
    finally:
        db.connection.close()


def test_section_guard_and_injected_processing_policy_control_typing():
    db = TypingDatabase(user('operator'))
    try:
        for guard_denied, can_process, expected in ((True, True, 403), (False, False, 403),
                                                    (False, True, 204)):
            app, _, _, _ = fixture(db=db, guard_denied=guard_denied,
                access=lambda: {'mode': 'operator', 'can_process': can_process})
            assert app.test_client().post(PATH, json=body()).status_code == expected
        assert len(db.notifications) == 1
    finally:
        db.connection.close()


def test_missing_session_cannot_publish():
    db, app = TypingDatabase(), Flask(__name__)
    try:
        app.register_blueprint(pilot.build_pilot_blueprint(
            db=db, require_api_key=lambda fn: fn,
            guard=lambda: (None, (jsonify(error='Login required'), 401)),
            channels=Mock(), preflight=lambda: ('', 204)))
        assert app.test_client().post(PATH, json=body()).status_code == 401
        assert db.statements == []
    finally:
        db.connection.close()


@pytest.mark.parametrize('updates,status', [
    ({'account': 'potok'}, 403), ({'account': None}, 403),
    ({'channelId': next(iter(pilot.EXCLUDED_CHANNELS))}, 403),
    ({'channelId': 'not-a-uuid'}, 400),
    ({'chatId': ''}, 400), ({'chatId': ' '}, 400), ({'chatId': 'x' * 101}, 400),
    ({'chatId': '\x00'}, 400), ({'clientId': 'short'}, 400),
    ({'typing': 'true'}, 400), ({'typing': 1}, 400), ({'typing': None}, 400),
    ({'sequence': -1}, 400), ({'sequence': True}, 400), ({'sequence': 2 ** 53}, 400),
])
def test_invalid_scope_and_shape_never_query_or_notify(typing_client, updates, status):
    client, db, _, transport = typing_client
    assert client.post(PATH, json=body(**updates)).status_code == status
    assert db.statements == []
    assert db.notifications == []
    transport.post.assert_not_called()


def test_internal_presence_does_not_fetch_vendor_channels_and_stop_survives_disconnection():
    db = TypingDatabase()
    try:
        channels = Mock(side_effect=AssertionError('Presence must not request vendor channels'))
        app, _, _, _ = fixture(db=db, channels=channels)
        client = app.test_client()
        assert client.post(PATH, json=body()).status_code == 204
        assert client.post(PATH, json=body(typing=False)).status_code == 204
        assert [notice['typing'] for _, notice in db.notifications] == [True, False]
        channels.assert_not_called()
    finally:
        db.connection.close()


@pytest.mark.parametrize('scope', [('potok', CHANNEL, CHAT, 'whatsapp'),
    ('op', str(uuid.uuid4()), CHAT, 'whatsapp'), ('op', CHANNEL, 'other-chat', 'whatsapp'),
    ('op', CHANNEL, CHAT, 'whatsgroup')])
def test_scoped_sql_never_notifies_for_a_foreign_missing_or_group_chat(typing_client, scope):
    client, db, _, _ = typing_client
    db.connection.execute('DELETE FROM wazzup_chats')
    db.connection.execute('INSERT INTO wazzup_chats VALUES (?,?,?,?)', scope)
    for _ in range(2):
        assert client.post(PATH, json=body()).status_code == 404
    assert db.notifications == []


def test_author_is_authenticated_and_only_one_small_notify_query_is_needed(typing_client):
    client, db, _, transport = typing_client
    response = client.post(PATH, json=body(authorName='Forged', userId='999',
                                          text='Private draft text', sequence=1))
    assert response.status_code == 204
    assert response.headers['Cache-Control'] == 'no-store'
    assert len(db.statements) == 1
    channel, sent = db.notifications[0]
    assert channel == NOTIFY_CHANNEL
    assert sent['userId'] == '42' and sent['authorName'] == 'Pilot Tester'
    assert sent['expiresAt'] - sent['emittedAt'] == TTL_MS
    assert sent['sequence'] == 1
    assert 'text' not in sent and 'Private draft text' not in json.dumps(sent)
    transport.post.assert_not_called()


def test_coalesced_requests_do_no_db_work_but_stop_is_immediate(typing_client):
    client, db, _, _ = typing_client
    for sequence in range(1, 20):
        assert client.post(PATH, json=body(sequence=sequence)).status_code == 204
    assert len(db.notifications) == 1
    assert client.post(PATH, json=body(typing=False, sequence=20)).status_code == 204
    assert [notice['typing'] for _, notice in db.notifications] == [True, False]
    assert client.post(PATH, json=body(typing=True, sequence=19)).status_code == 204
    assert client.post(PATH, json=body(typing=False, sequence=21)).status_code == 204
    assert len(db.statements) == 2


def test_no_notification_or_state_is_created_for_oversized_request(typing_client):
    client, db, _, _ = typing_client
    assert client.post(PATH, json=body(text='x' * 4096)).status_code == 413
    assert not db.statements


def test_heartbeat_throttle_tombstones_and_memory_are_bounded():
    now = [100.0]
    limiter = TypingLimiter(max_sessions=3, max_users=2, clock=lambda: now[0])
    assert limiter.reserve('u', 'tab', ('c', 'chat'), True, 1)
    now[0] += 1
    assert limiter.reserve('u', 'tab', ('c', 'chat'), True, 2) is None
    now[0] += 2
    assert limiter.reserve('u', 'tab', ('c', 'chat'), True, 3)
    assert limiter.reserve('u', 'tab', ('c', 'chat'), False, 4)
    assert limiter.reserve('u', 'tab', ('c', 'chat'), True, 3) is None
    for index in range(20):
        assert limiter.reserve(str(index), 'tab', ('c', 'chat'), True, 1)
    assert len(limiter.sessions) == 3 and len(limiter.users) == 2
    now[0] += 61
    assert limiter.reserve('fresh', 'tab', ('c', 'chat'), True, 1)
    assert len(limiter.sessions) == len(limiter.users) == 1


def test_user_bucket_limits_rotating_tabs_and_allows_final_stop():
    limiter = TypingLimiter(clock=lambda: 100)
    for index in range(8):
        assert limiter.reserve('u', str(index), ('c', 'chat'), True, 1)
    assert limiter.reserve('u', 'new', ('c', 'chat'), True, 1) is None
    assert limiter.reserve('u', '0', ('c', 'chat'), False, 2)
    assert limiter.reserve('u', '0', ('c', 'chat'), False, 3) is None
    assert limiter.reserve('u', '0', ('c', 'chat'), True, 4) is None
    assert len(limiter.sessions) == 8


def test_typing_batches_never_hydrate_and_preserve_other_users_tabs_and_message_events():
    cursor, broker = Mock(), EventBroker()
    broker.acquire()
    notices = [event(sequence=2, typing=False), event(sequence=1),
               event(userId='43'), event(clientId=str(uuid.uuid4())),
               event(chatId='another-chat')]
    message = dict(account='op', channelId=CHANNEL, chatId=CHAT, messageId='m1',
                   status='read', statusOnly=True, affectsList=False)
    broadcast_changes(cursor, [*notices, message], broker)
    received, sequence = broker.wait(0)
    assert len(received) == 5
    assert received[0]['typing'] is False and received[0]['sequence'] == 2
    assert received[-1] == message
    assert not any('affectsList' in notice or 'message' in notice for notice in received[:-1])
    cursor.execute.assert_not_called()
    assert broker.resume_from(broker.epoch, sequence) == (sequence, True)


def test_slow_old_request_never_overwrites_stop_during_broker_replay():
    broker = EventBroker()
    now = int(time.time() * 1000)
    stop = event(sequence=2, typing=False, emittedAt=now, expiresAt=now + TTL_MS)
    delayed_start = event(sequence=1, emittedAt=now + 1, expiresAt=now + 1 + TTL_MS)
    broker.publish(stop)
    broker.publish(delayed_start)
    assert broker.wait(0)[0] == [stop]


def test_stop_wins_millisecond_ties_without_optional_sequence():
    broker = EventBroker()
    start = event()
    stop = dict(start, typing=False)
    for notice in (start, stop, start):
        broker.publish(notice)
    assert broker.wait(0)[0] == [stop]


def test_expired_presence_is_not_replayed_but_resume_cursor_and_messages_still_advance():
    broker = EventBroker()
    notice = event()
    broker.publish(notice)
    broker.publish(dict(messageId='m1', statusOnly=True))
    with patch('wazzup.realtime.time.time', return_value=notice['expiresAt'] / 1000):
        changes, sequence = broker.wait(0)
    assert changes == [dict(messageId='m1', statusOnly=True)]
    assert sequence == 2
    assert broker.resume_from(broker.epoch, 1) == (1, True)


@pytest.mark.parametrize('updates', [dict(account='potok'), dict(typing='true'),
    dict(userId=42), dict(authorName='x' * 201), dict(clientId='bad'), dict(channelId='bad'),
    dict(expiresAt=0), dict(expiresAt=2 ** 63), dict(emittedAt=True), dict(sequence=True)])
def test_invalid_notifications_are_ignored_without_database_hydration(updates):
    cursor, broker = Mock(), EventBroker()
    broadcast_changes(cursor, [event(**updates)], broker)
    assert broker.current_seq() == 0
    cursor.execute.assert_not_called()


def test_notification_whitelist_never_transmits_draft_content():
    notice = event(text='private draft', arbitrary='ignored')
    normalized = normalize_event(notice)
    assert normalized is not None
    assert 'text' not in normalized and 'arbitrary' not in normalized


def _frame(raw):
    lines = raw.decode().strip().split('\n')
    fields = dict(line.split(': ', 1) for line in lines)
    return fields.get('id'), fields['event'], json.loads(fields['data'])


def test_existing_sse_delivers_presence_and_server_clock_without_another_stream(typing_client):
    client, db, broker, _ = typing_client
    response = client.get('/api/wazzup/pilot/stream', buffered=False)
    try:
        frames = iter(response.response)
        assert _frame(next(frames))[1] == 'connected'
        assert client.post(PATH, json=body(sequence=1)).status_code == 204
        cursor = Mock()
        broadcast_changes(cursor, [db.notifications[0][1]], broker)
        frame_id, name, payload = _frame(next(frames))
        assert (frame_id, name) == (str(broker.current_seq()), 'typing')
        assert payload['typing'][0]['kind'] == 'typing'
        assert payload['serverTime'] >= payload['typing'][0]['emittedAt']
        assert broker.streams == 1
        cursor.execute.assert_not_called()
    finally:
        response.close()
    assert broker.streams == 0


def test_tabs_from_before_presence_never_receive_it_among_message_changes(typing_client):
    # Their `change` handler re-reads the list and the thread for any unknown
    # event; an unknown frame name is skipped, its `id:` still advances resume.
    client, _, broker, _ = typing_client
    response = client.get('/api/wazzup/pilot/stream', buffered=False)
    try:
        frames = iter(response.response)
        next(frames)
        message = dict(account='op', channelId=CHANNEL, chatId=CHAT, messageId='m1',
                       status='read', statusOnly=True, affectsList=False)
        broadcast_changes(Mock(), [event(sequence=1), message], broker)
        change, typing = _frame(next(frames)), _frame(next(frames))
        assert change[1] == 'change' and change[2] == {'changes': [message]}
        assert typing[1] == 'typing' and [e['kind'] for e in typing[2]['typing']] == ['typing']
        assert change[0] == typing[0] == str(broker.current_seq())
    finally:
        response.close()
