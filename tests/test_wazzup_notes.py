"""Internal comments never enter WhatsApp sends, unread state or vendor events."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from flask import Flask, jsonify

from tests.test_wazzup_pilot import CHANNEL, fixture, user
from wazzup import pilot
from wazzup.notes import PAGE_SIZE
from wazzup.realtime import EventBroker, broadcast_changes


PATH = '/api/wazzup/pilot/notes'
CHAT = '77000000000'


def body(**updates):
    return dict(account='op', channelId=CHANNEL, chatId=CHAT, text='Internal only',
                clientNoteId=str(uuid.uuid4())) | updates


class NotesDatabase:
    """Execute production SQL against a local SQLite table with equivalent keys."""
    def __init__(self, actor=None):
        self.actor = actor if actor is not None else user()
        self.statements = []
        self.connection = sqlite3.connect(':memory:')
        self.connection.executescript('''
            CREATE TABLE wazzup_chats (account TEXT, channel_id TEXT, chat_id TEXT);
            CREATE TABLE wazzup_chat_notes (
                id TEXT PRIMARY KEY, account TEXT, channel_id TEXT, chat_id TEXT,
                client_note_id TEXT, author_id INTEGER, author_name TEXT, text TEXT,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
                UNIQUE (author_id,client_note_id)
            );
        ''')
        self.connection.execute('INSERT INTO wazzup_chats VALUES (?,?,?)', ('op', CHANNEL, CHAT))

    def get_user(self, **_):
        return self.actor

    @contextmanager
    def _get_cursor(self):
        with self.connection:
            yield self

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if 'id=ANY(%s::uuid[])' in sql:
            sql = sql.replace('id=ANY(%s::uuid[])', 'id IN (' + ','.join('?' * len(params[0])) + ')')
            params = params[0]
        self.cursor = self.connection.execute(sql.replace('%s', '?'), params)

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


@pytest.fixture
def notes():
    db = NotesDatabase()
    app, _, _, transport = fixture(db=db)
    yield app.test_client(), db, transport
    db.connection.close()


@pytest.mark.parametrize('actor', [user('other'), user(status='fired'), user(status='dismissal')])
def test_private_notes_reject_other_or_revoked_actors_before_database(actor):
    db = NotesDatabase(actor)
    app, _, _, transport = fixture(db=db)
    with app.test_client() as client:
        assert client.get(PATH, query_string=body()).status_code == 403
        assert client.post(PATH, json=body()).status_code == 403
    assert db.statements == []
    transport.post.assert_not_called()
    db.connection.close()


def test_missing_session_cannot_read_or_write_notes():
    db, app = NotesDatabase(), Flask(__name__)
    app.register_blueprint(pilot.build_pilot_blueprint(
        db=db, require_api_key=lambda fn: fn, guard=lambda: (None, (jsonify(error='Login required'), 401)),
        channels=Mock(), preflight=lambda: ('', 204)))
    client = app.test_client()
    assert client.get(PATH, query_string=body()).status_code == 401
    assert client.post(PATH, json=body()).status_code == 401
    assert db.statements == []
    db.connection.close()


def test_scope_and_validation_prevent_database_writes(notes):
    client, db, transport = notes
    for changes, status in [({'account': 'potok'}, 403), ({'channelId': 'broken'}, 400),
                            ({'chatId': ''}, 400), ({'chatId': 'x' * 101}, 400)]:
        assert client.get(PATH, query_string=body(**changes)).status_code == status
        assert client.post(PATH, json=body(**changes)).status_code == status
    for channel in pilot.EXCLUDED_CHANNELS:
        for value in (channel, channel.upper()):
            assert client.get(PATH, query_string=body(channelId=value)).status_code == 403
            assert client.post(PATH, json=body(channelId=value)).status_code == 403
    for changes in ({'text': ''}, {'text': ' '}, {'text': 12}, {'text': 'x' * 4001},
                    {'text': 'x\x00y'}, {'clientNoteId': 'bad'}, {'clientNoteId': None}):
        assert client.post(PATH, json=body(**changes)).status_code == 400
    assert client.post(PATH, json=[]).status_code == 400
    assert client.post(PATH, data='broken', content_type='application/json').status_code == 400
    assert client.post(PATH, json=body(text='x' * 65536)).status_code == 413
    assert db.statements == []
    transport.post.assert_not_called()


def test_unknown_or_other_account_chat_is_not_created(notes):
    client, db, _ = notes
    for changes in ({'chatId': 'missing'}, {'channelId': str(uuid.uuid4())}):
        assert client.get(PATH, query_string=body(**changes)).status_code == 404
        assert client.post(PATH, json=body(**changes)).status_code == 404
    db.connection.execute("UPDATE wazzup_chats SET account='potok'")
    assert client.get(PATH, query_string=body()).status_code == 404
    assert client.post(PATH, json=body()).status_code == 404
    assert all('SELECT 1 FROM wazzup_chats' in sql for sql, _ in db.statements)


def test_post_keeps_author_and_time_server_side_and_never_calls_external_send(notes, monkeypatch):
    client, db, transport = notes
    external = Mock(side_effect=AssertionError('A private note must never send HTTP'))
    monkeypatch.setattr('requests.sessions.Session.request', external)
    request = body(text='Private\nКомментарий', authorName='Spoofed', authorId=999,
                   createdAt='2000-01-01T00:00:00Z', id=str(uuid.uuid4()), isEcho=True)
    response = client.post(PATH, json=request)
    assert response.status_code == 201
    item = response.json['item']
    assert item['authorName'] == 'Pilot Tester' and item['authorId'] == 42
    assert item['text'] == 'Private\nКомментарий' and item['id'] != request['id']
    assert datetime.fromisoformat(item['createdAt']).year == datetime.now(timezone.utc).year
    assert response.headers['Cache-Control'] == 'private, no-store'
    snapshot = client.get(PATH, query_string=body())
    assert snapshot.json == dict(items=[item], hasMore=False, nextBeforeId=None)
    assert snapshot.headers['Cache-Control'] == 'private, no-store'
    assert all('wazzup_chats' in sql or 'wazzup_chat_notes' in sql for sql, _ in db.statements)
    transport.post.assert_not_called()
    external.assert_not_called()


def test_retry_is_idempotent_but_other_payload_or_scope_conflicts(notes):
    client, db, _ = notes
    request = body()
    first = client.post(PATH, json=request)
    second = client.post(PATH, json=request)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json == second.json
    assert client.post(PATH, json=request | {'text': 'Changed'}).status_code == 409
    other_channel = str(uuid.uuid4())
    db.connection.executemany('INSERT INTO wazzup_chats VALUES (?,?,?)',
                             [('op', other_channel, CHAT), ('op', CHANNEL, 'other')])
    for changes in ({'channelId': other_channel}, {'chatId': 'other'}):
        conflict = client.post(PATH, json=request | changes)
        assert conflict.status_code == 409 and conflict.json['code'] == 'REQUEST_CONFLICT'
    assert db.connection.execute('SELECT COUNT(*) FROM wazzup_chat_notes').fetchone() == (1,)


def test_read_pagination_is_bounded_ordered_and_cannot_use_foreign_cursor(notes):
    client, db, _ = notes
    ids = [str(uuid.uuid4()) for _ in range(PAGE_SIZE + 3)]
    for number, note_id in enumerate(ids):
        db.connection.execute('''INSERT INTO wazzup_chat_notes
            (id,account,channel_id,chat_id,client_note_id,author_id,author_name,text,created_at)
            VALUES (?,'op',?,?,?,?,?,?,?)''',
            (note_id, CHANNEL, CHAT, str(uuid.uuid4()), 42, 'Tester', str(number),
             f'2026-10-08T00:{number // 60:02}:{number % 60:02}+00:00'))
    page = client.get(PATH, query_string=body()).json
    assert len(page['items']) == PAGE_SIZE and page['hasMore']
    assert [item['text'] for item in page['items']] == [str(i) for i in range(3, PAGE_SIZE + 3)]
    previous = client.get(PATH, query_string=body(beforeId=page['nextBeforeId'])).json
    assert [item['text'] for item in previous['items']] == ['0', '1', '2']
    assert not previous['hasMore'] and previous['nextBeforeId'] is None
    assert client.get(PATH, query_string=body(beforeId='invalid')).status_code == 400
    db.connection.execute("UPDATE wazzup_chat_notes SET chat_id='other' WHERE id=?", (ids[0],))
    assert client.get(PATH, query_string=body(beforeId=ids[0])).status_code == 404
    assert client.get(PATH, query_string=body(beforeId=str(uuid.uuid4()))).status_code == 404


def change(note_id, **updates):
    return dict(account='op', channelId=CHANNEL, chatId=CHAT, kind='note',
                noteId=note_id, messageId='note:' + note_id, affectsList=False, statusOnly=False) | updates


def test_realtime_notes_share_one_query_across_six_readers_without_message_hydration(notes):
    client, db, _ = notes
    items = [client.post(PATH, json=body(text=f'Note {number}')).json['item'] for number in range(3)]
    db.statements.clear()
    broker = EventBroker()
    for _ in range(6):
        broker.acquire()
    broadcast_changes(db, [change(item['id']) for item in items], broker)
    assert len(db.statements) == 1 and 'FROM wazzup_chat_notes' in db.statements[0][0]
    for _ in range(6):
        events, _ = broker.wait(0)
        assert [event['note'] for event in events] == items
        assert all(not event['affectsList'] and 'message' not in event and 'chat' not in event for event in events)


def test_notes_realtime_preserves_scope_and_falls_back_when_unhydrated(notes):
    client, db, _ = notes
    item = client.post(PATH, json=body()).json['item']
    broker = EventBroker()
    broadcast_changes(db, [change(item['id'])], broker)
    assert 'note' not in broker.wait(0)[0][0]
    broker.acquire()
    before = broker.current_seq()
    broadcast_changes(db, [change(item['id'], chatId='foreign')], broker)
    assert 'note' not in broker.wait(before)[0][0]
    before = broker.current_seq()
    broadcast_changes(db, [change(item['id'])], broker)
    broker.publish(change(item['id']))
    assert 'note' not in broker.wait(before)[0][0], 'A later refresh fallback must not retain stale note text'


def test_oversized_notes_use_reload_fallback_instead_of_truncation():
    broker = EventBroker(max_bytes=1500)
    event = change(str(uuid.uuid4()), note=dict(text='Я' * 3000))
    broker.publish(event)
    result = broker.wait(0)[0][0]
    assert 'note' not in result and result['kind'] == 'note'
    assert len(event['note']['text']) == 3000 and broker.buffer_bytes <= 1500
