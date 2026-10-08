"""Reply-window metadata uses all customer messages, not just the visible page."""
import ast
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import Flask, jsonify, request

from tests import source_cache
from tests.test_wazzup_pilot import CHANNEL, RefreshDatabase, fixture
from tests.test_wazzup_pilot_persistence import pg
from wazzup.window import last_inbound_at


def history_app(db):
    """Run the real route without importing the server or its production pool."""
    path = Path(__file__).resolve().parents[1] / 'bot_schedule2.py'
    source = source_cache.parse(path.read_text(encoding='utf-8-sig'))
    route = next(node for node in source.body
                 if isinstance(node, ast.FunctionDef) and node.name == 'api_wazzup_chat_messages')
    route.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[route], type_ignores=[]))
    namespace = dict(db=db, jsonify=jsonify, request=request, logging=logging,
                     _verifier_chats_guard=lambda: (1, None),
                     _wazzup_account_arg=lambda: request.args.get('account', 'op'))
    exec(compile(module, str(path), 'exec'), namespace)
    app = Flask(__name__)
    app.add_url_rule('/api/wazzup/chat-messages', view_func=namespace['api_wazzup_chat_messages'])
    return app


@pytest.fixture
def archive():
    db = RefreshDatabase()
    try:
        yield db
    finally:
        db.connection.close()


def seed_customer_history(db):
    db.add(1, is_echo=False)
    # Deletion does not cancel the window opened by the customer message.
    db.add(2, is_echo=False, is_deleted=True)
    for number in range(10, 81):
        db.add(number)
    # An old message delivered later must not reopen the window.
    db.add(500, is_echo=False, wazzup_dt='2026-10-05T00:00:00+00:00')
    db.add(1001, is_echo=False, account='potok')
    db.add(1002, is_echo=False, channel='foreign-channel')
    db.add(1003, is_echo=False, chat='foreign-chat')


def test_history_metadata_is_scoped_and_independent_of_pagination(archive):
    seed_customer_history(archive)
    client = history_app(archive).test_client()
    query = dict(account='op', channel_id=CHANNEL, chat_id='test-chat', limit=5)
    newest = client.get('/api/wazzup/chat-messages', query_string=query)
    older = client.get('/api/wazzup/chat-messages', query_string={
        **query, 'before': '2026-10-06T00:00:30+00:00'})
    assert newest.status_code == older.status_code == 200
    first, second = newest.get_json(), older.get_json()
    assert first['lastInboundAt'] == second['lastInboundAt'] == '2026-10-06T00:00:02+00:00'
    assert len(first['items']) == len(second['items']) == 5
    assert first['items'] != second['items']
    assert all(item['messageId'] != 'm-2' for item in first['items'] + second['items'])


def test_refresh_metadata_includes_customer_before_displayed_range(archive):
    seed_customer_history(archive)
    app, _, _, _ = fixture(db=archive)
    response = app.test_client().post('/api/wazzup/pilot/refresh', json=dict(
        account='op', channelId=CHANNEL, chatId='test-chat', messageIds=['m-10']))
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['lastInboundAt'] == '2026-10-06T00:00:02+00:00'
    assert all(item['messageId'] != 'm-2' for item in payload['items'])
    assert payload['serverTime']


def test_both_routes_return_null_without_customer_messages(archive):
    archive.add(10)
    history = history_app(archive).test_client().get('/api/wazzup/chat-messages', query_string=dict(
        account='op', channel_id=CHANNEL, chat_id='test-chat')).get_json()
    app, _, _, _ = fixture(db=archive)
    refresh = app.test_client().post('/api/wazzup/pilot/refresh', json=dict(
        account='op', channelId=CHANNEL, chatId='test-chat', messageIds=[])).get_json()
    assert history['lastInboundAt'] is None
    assert refresh['lastInboundAt'] is None


@pytest.mark.skipif(not os.environ.get('WAZZUP_PILOT_TEST_PORT'), reason='needs isolated local PG')
def test_postgres_timestamp_offsets_original_time_and_scope(pg):
    _, cursor, _, _ = pg
    rows = [
        ('first', 'op', 'channel', 'chat', '2026-10-06T08:00:00Z', False, None, False),
        ('deleted', 'op', 'channel', 'chat', '2026-10-06T14:00:00+05:00', False, None, True),
        ('delayed', 'op', 'channel', 'chat', '2026-10-07T10:00:00Z', False, '2026-10-05T10:00:00Z', False),
        ('outgoing', 'op', 'channel', 'chat', '2026-10-08T10:00:00Z', True, None, False),
        ('account', 'potok', 'channel', 'chat', '2026-10-08T10:00:00Z', False, None, False),
        ('channel', 'op', 'other', 'chat', '2026-10-08T10:00:00Z', False, None, False),
        ('chat', 'op', 'channel', 'other', '2026-10-08T10:00:00Z', False, None, False),
    ]
    cursor.executemany('''INSERT INTO wazzup_messages
        (message_id, account, channel_id, chat_id, dt, is_echo, wazzup_dt, is_deleted)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''', rows)
    value = last_inbound_at(cursor, 'op', 'channel', 'chat')
    assert datetime.fromisoformat(value) == datetime(2026, 10, 6, 9, tzinfo=timezone.utc)
    assert last_inbound_at(cursor, 'op', 'channel', 'unknown') is None
