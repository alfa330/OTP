"""Unread authorization plus real trigger/ACK races on disposable local PostgreSQL."""
import json
import select
import threading

import pytest

from tests.test_wazzup_pilot import MemoryDatabase, fixture, user
from tests.test_wazzup_pilot_persistence import PORT, pg  # noqa: F401: shared local-only PG fixture
from wazzup.pilot import EXCLUDED_CHANNELS
from wazzup.realtime import EventBroker, broadcast_changes


postgres = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')


def inbound(message_id='inbound-1', **updates):
    result = dict(messageId=message_id, channelId='channel', chatId='70000000000',
                  chatType='whatsapp', dateTime='2026-10-06T08:00:00Z', isEcho=False,
                  text='Synthetic unread test')
    result.update(updates)
    return result


def reply(message_id='reply-1', **updates):
    result = inbound(message_id, isEcho=True, status='sent', authorName='Other operator',
                     dateTime='2026-10-06T08:01:00Z')
    result.update(updates)
    return result


def read_state(cursor, channel='channel', chat='70000000000'):
    cursor.execute('''SELECT unread_count,last_inbound_id,revision FROM wazzup_chat_read_state
                      WHERE account='op' AND channel_id=%s AND chat_id=%s''', (channel, chat))
    return cursor.fetchone()


def client_for(database):
    database.get_user = lambda **_: user()
    app, _, _, _ = fixture(db=database)
    return app.test_client()


@postgres
def test_icore_template_crud_uses_real_schema(pg, monkeypatch):
    from wazzup import templates
    monkeypatch.setattr(templates, 'list_templates', lambda *a, **k: dict(items=[], stale=False, sourceWarnings=[]))
    _, _, database, _ = pg
    client = client_for(database())
    created = client.post('/api/wazzup/pilot/templates', json=dict(account='op', title='Synthetic', text='Hello'))
    assert created.status_code == 201
    template_id = created.json['item']['id']
    updated = client.patch('/api/wazzup/pilot/templates/' + template_id,
                          json=dict(account='op', title='Updated', text='New body'))
    assert updated.status_code == 200 and updated.json['item']['text'] == 'New body'
    assert client.get('/api/wazzup/pilot/templates').json['items'][0]['title'] == 'Updated'
    assert client.delete('/api/wazzup/pilot/templates/' + template_id).status_code == 200
    assert client.get('/api/wazzup/pilot/templates').json['items'] == []


def body(seen='inbound-1', **updates):
    result = dict(account='op', channelId='channel', chatId='70000000000', seenMessageId=seen)
    result.update(updates)
    return result


@pytest.mark.parametrize('actor', [user('other'), user(status='fired'), user(status='dismissal')])
def test_unread_routes_remain_restricted_to_processing_users(actor):
    app, db, _, _ = fixture(db=MemoryDatabase(actor))
    with app.test_client() as client:
        assert client.get('/api/wazzup/pilot/unread').status_code == 403
        assert client.post('/api/wazzup/pilot/read', json=body()).status_code == 403
    assert not db.statements


def test_unread_routes_reject_other_account_and_global_before_queries():
    app, db, _, _ = fixture()
    with app.test_client() as client:
        assert client.get('/api/wazzup/pilot/unread?account=potok').status_code == 403
        assert client.post('/api/wazzup/pilot/read', json=body(account='potok')).status_code == 403
        for excluded in EXCLUDED_CHANNELS:
            assert client.post('/api/wazzup/pilot/read', json=body(channelId=excluded)).status_code == 403
        for invalid in (None, '', 'a' * 201, 3):
            assert client.post('/api/wazzup/pilot/read', json=body(seenMessageId=invalid)).status_code == 400
    assert not db.statements


def test_unread_events_coalesce_without_hydration_queries_or_losing_zero_count():
    broker = EventBroker()
    broker.acquire()
    changes = [dict(account='op', channelId='channel', chatId='chat', messageId='unread:channel:chat',
                    kind='unread', statusOnly=True, affectsList=False, unreadCount=count,
                    unreadVersion=revision, lastInboundId='message-2')
               for revision, count in ((1, 1), (2, 2), (3, 0))]

    class NoDatabase:
        def execute(self, *_):
            raise AssertionError('Unread notifications require no hydration query')

    broadcast_changes(NoDatabase(), changes, broker)
    events, _ = broker.wait(0)
    assert len(events) == 1
    assert events[0]['kind'] == 'unread'
    assert events[0]['unreadCount'] == 0
    assert events[0]['unreadVersion'] == 3
    assert not events[0]['affectsList']


@postgres
def test_new_inbound_counts_once_despite_duplicate_webhooks_and_status_updates(pg):
    _, cursor, database, _ = pg
    db = database()
    db.store_wazzup_messages([inbound()])
    assert read_state(cursor) == (1, 'inbound-1', 1)
    db.store_wazzup_messages([inbound(), inbound(status='read')])
    db.update_wazzup_statuses([dict(messageId='inbound-1', status='read')])
    assert read_state(cursor) == (1, 'inbound-1', 1)
    db.store_wazzup_messages([inbound('inbound-2')])
    assert read_state(cursor) == (2, 'inbound-2', 2)


@postgres
def test_echo_deleted_group_other_account_and_global_do_not_increment(pg):
    _, cursor, database, _ = pg
    db = database()
    variants = [dict(isEcho=True), dict(isDeleted=True), dict(chatType='whatsgroup')]
    variants.extend(dict(channelId=excluded) for excluded in EXCLUDED_CHANNELS)
    for index, variant in enumerate(variants):
        db.store_wazzup_messages([inbound(f'ignored-{index}', **variant)])
    db.store_wazzup_messages([inbound('other-account')], account='potok')
    cursor.execute('SELECT COUNT(*) FROM wazzup_chat_read_state')
    assert cursor.fetchone()[0] == 0


@postgres
def test_snapshot_and_seen_ack_leave_newer_unread_messages_intact(pg):
    _, cursor, database, _ = pg
    db = database()
    db.store_wazzup_messages([inbound(), inbound('inbound-2')])
    client = client_for(db)
    response = client.get('/api/wazzup/pilot/unread')
    assert response.status_code == 200
    snapshot = response.get_json()
    assert snapshot['next'] is None
    assert len(snapshot['items']) == 1
    assert snapshot['items'][0]['unreadCount'] == 2
    assert snapshot['items'][0]['chat']['chatType'] == 'whatsapp'
    stale = client.post('/api/wazzup/pilot/read', json=body()).get_json()['item']
    assert stale['unreadCount'] == 2
    assert stale['lastInboundId'] == 'inbound-2'
    current = client.post('/api/wazzup/pilot/read', json=body('inbound-2')).get_json()['item']
    assert current['unreadCount'] == 0
    assert current['unreadVersion'] == 3
    client.post('/api/wazzup/pilot/read', json=body('inbound-2'))
    assert read_state(cursor) == (0, 'inbound-2', 3)
    assert client.get('/api/wazzup/pilot/unread').get_json()['items'] == []


@postgres
def test_read_ack_racing_with_inbound_does_not_clear_unseen_message(pg):
    connection, cursor, database, connect = pg
    db = database()
    db.store_wazzup_messages([inbound()])
    connection.commit()
    other = connect()
    other_db = database(other)
    other_db.get_user = lambda **_: user()
    app, _, _, _ = fixture(db=other_db)
    cursor.execute("SELECT 1 FROM wazzup_chat_read_state WHERE account='op' FOR UPDATE")
    started, done, failures, result = threading.Event(), threading.Event(), [], []

    def acknowledge():
        try:
            with app.test_client() as client:
                started.set()
                response = client.post('/api/wazzup/pilot/read', json=body())
                result.append(response.get_json())
                other.commit()
        except Exception as error:
            failures.append(error)
        finally:
            done.set()

    thread = threading.Thread(target=acknowledge, daemon=True)
    thread.start()
    assert started.wait(1)
    assert not done.wait(.15)
    db.store_wazzup_messages([inbound('inbound-2')])
    connection.commit()
    thread.join(5)
    assert not thread.is_alive()
    assert not failures
    assert result[0]['item']['unreadCount'] == 2
    assert read_state(cursor) == (2, 'inbound-2', 2)


@postgres
def test_unread_snapshot_pagination_does_not_drop_chats(pg):
    _, cursor, database, _ = pg
    cursor.execute('''INSERT INTO wazzup_chat_read_state
        (account,channel_id,chat_id,unread_count,last_inbound_id)
        SELECT 'op','channel',LPAD(i::text,5,'0'),1,'m-'||i::text FROM generate_series(1,1002) i''')
    client = client_for(database())
    first = client.get('/api/wazzup/pilot/unread').get_json()
    assert len(first['items']) == 1000
    assert first['next']
    second = client.get('/api/wazzup/pilot/unread', query_string={'after': first['next']}).get_json()
    assert len(second['items']) == 2
    assert second['next'] is None
    combined = first['items'] + second['items']
    assert len({(item['channelId'], item['chatId']) for item in combined}) == 1002


@postgres
def test_unread_notifications_are_transactional_and_include_read_clearing(pg):
    connection, _, database, connect = pg
    db = database()
    listener = connect()
    listener.autocommit = True
    with listener.cursor() as cursor:
        cursor.execute('LISTEN wazzup_pilot_events')

    def drain():
        select.select([listener], [], [], .1)
        listener.poll()
        notifications = [json.loads(event.payload) for event in listener.notifies]
        listener.notifies.clear()
        return [event for event in notifications if event.get('kind') == 'unread']

    db.store_wazzup_messages([inbound()])
    assert drain() == []
    connection.commit()
    events = drain()
    assert len(events) == 1
    assert events[0]['statusOnly'] is True
    assert events[0]['unreadCount'] == 1
    assert events[0]['lastInboundId'] == 'inbound-1'
    client_for(db).post('/api/wazzup/pilot/read', json=body())
    connection.commit()
    events = drain()
    assert len(events) == 1
    assert events[0]['unreadCount'] == 0
    assert events[0]['unreadVersion'] == 2


@postgres
@pytest.mark.parametrize('status', [None, 'sent', 'delivered', 'read'])
def test_reply_from_another_wazzup_operator_clears_waiting_messages(pg, status):
    _, cursor, database, _ = pg
    db = database()
    db.store_wazzup_messages([inbound(), inbound('inbound-2')])
    db.store_wazzup_messages([reply(status=status)])
    assert read_state(cursor) == (0, 'inbound-2', 3)
    cursor.execute('SELECT COUNT(*) FROM wazzup_unanswered_messages')
    assert cursor.fetchone() == (0,)
    db.store_wazzup_messages([reply(status=status)])
    assert read_state(cursor) == (0, 'inbound-2', 3)


@postgres
@pytest.mark.parametrize('status', ['pending', 'error', 'failed', 'accepted'])
def test_unconfirmed_or_failed_outgoing_does_not_hide_waiting_messages(pg, status):
    _, cursor, database, _ = pg
    db = database()
    db.store_wazzup_messages([inbound(), reply(status=status)])
    assert read_state(cursor) == (1, 'inbound-1', 1)
    db.update_wazzup_statuses([dict(messageId='reply-1', status='delivered')])
    assert read_state(cursor) == (0, 'inbound-1', 2)


@postgres
def test_late_reply_and_status_clear_only_the_inbounds_preceding_that_reply(pg):
    _, cursor, database, _ = pg
    db = database()
    db.store_wazzup_messages([inbound(), reply(status='pending'),
                              inbound('inbound-2', dateTime='2026-10-06T08:02:00Z')])
    db.update_wazzup_statuses([dict(messageId='reply-1', status='sent')])
    assert read_state(cursor) == (1, 'inbound-2', 3)
    db.store_wazzup_messages([reply('older-echo', dateTime='2026-10-06T08:00:30Z')])
    assert read_state(cursor) == (1, 'inbound-2', 3)
    db.store_wazzup_messages([inbound('late-inbound', dateTime='2026-10-06T08:00:10Z')])
    assert read_state(cursor) == (1, 'inbound-2', 3)
    cursor.execute('SELECT message_id FROM wazzup_unanswered_messages')
    assert cursor.fetchall() == [('inbound-2',)]


@postgres
def test_reply_uses_original_messenger_time_not_adjusted_archive_time(pg):
    _, cursor, database, _ = pg
    db = database()
    # Simulate the archive's late-delivery normalization at insert time.
    cursor.execute('''INSERT INTO wazzup_messages
        (message_id,channel_id,chat_id,chat_type,dt,wazzup_dt,is_echo)
        VALUES('late-inbound','channel','70000000000','whatsapp',
               '2026-10-06T08:02:00Z','2026-10-06T08:00:00Z',FALSE)''')
    db.store_wazzup_messages([reply()])
    assert read_state(cursor) == (0, 'late-inbound', 2)


@postgres
def test_auto_greeting_does_not_dismiss_but_native_wazzup_reply_does(pg):
    _, cursor, database, _ = pg
    db = database()
    db.store_wazzup_messages([inbound(), reply('bot', authorName=None, authorId=None,
                                              sentFromApp=False)])
    assert read_state(cursor) == (1, 'inbound-1', 1)
    db.store_wazzup_messages([reply('native', authorName=None, authorId=None,
                                  sentFromApp=True)])
    assert read_state(cursor) == (0, 'inbound-1', 2)


@postgres
def test_own_outbox_reply_can_clear_without_vendor_author_fields(pg):
    import uuid
    _, cursor, database, _ = pg
    db = database()
    db.store_wazzup_messages([inbound()])
    cursor.execute('''INSERT INTO wazzup_pilot_outbox
        (request_id,account,channel_id,chat_id,chat_type,text,user_id,message_id)
        VALUES(%s,'op','channel','70000000000','whatsapp','Reply',1,'our-reply')''',
        (str(uuid.uuid4()),))
    db.store_wazzup_messages([reply('our-reply', authorName=None, authorId=None, status='pending')])
    assert read_state(cursor) == (1, 'inbound-1', 1)
    db.update_wazzup_statuses([dict(messageId='our-reply', status='sent')])
    assert read_state(cursor) == (0, 'inbound-1', 2)


@postgres
def test_deleting_a_pending_inbound_updates_shared_counter_once(pg):
    _, cursor, database, _ = pg
    db = database()
    db.store_wazzup_messages([inbound(), inbound('inbound-2')])
    db.store_wazzup_messages([inbound(isDeleted=True)])
    assert read_state(cursor) == (1, 'inbound-2', 3)
    db.store_wazzup_messages([inbound(isDeleted=True)])
    assert read_state(cursor) == (1, 'inbound-2', 3)


@postgres
def test_echo_before_first_inbound_prevents_reopening_old_messages(pg):
    _, cursor, database, _ = pg
    db = database()
    db.store_wazzup_messages([reply(), inbound()])
    assert read_state(cursor)[0] == 0
    db.store_wazzup_messages([inbound('new-inbound', dateTime='2026-10-06T08:02:00Z')])
    assert read_state(cursor) == (1, 'new-inbound', 1)


@postgres
def test_manual_dismissal_does_not_reopen_after_duplicate_or_old_echo(pg):
    _, cursor, database, _ = pg
    db = database()
    db.store_wazzup_messages([inbound()])
    client_for(db).post('/api/wazzup/pilot/read', json=body())
    db.store_wazzup_messages([inbound(), reply(dateTime='2026-10-06T07:59:00Z')])
    assert read_state(cursor) == (0, 'inbound-1', 2)
    cursor.execute('SELECT COUNT(*) FROM wazzup_unanswered_messages')
    assert cursor.fetchone() == (0,)
    db.store_wazzup_messages([inbound('new-inbound', dateTime='2026-10-06T08:02:00Z')])
    assert read_state(cursor) == (1, 'new-inbound', 3)


@postgres
def test_schema_upgrade_reconciles_only_existing_counter_tail_once(pg):
    from wazzup.unread import init_unread_schema
    _, cursor, database, _ = pg
    db = database()
    cursor.execute('ALTER TABLE wazzup_messages DISABLE TRIGGER wazzup_unread_insert')
    db.store_wazzup_messages([inbound('dismissed-old'),
        inbound('answered-inbound', dateTime='2026-10-06T08:00:30Z'), reply(),
        inbound('still-pending', dateTime='2026-10-06T08:02:00Z'),
        inbound('untracked-history', chatId='other-chat')])
    cursor.execute('''INSERT INTO wazzup_chat_read_state
        (account,channel_id,chat_id,unread_count,last_inbound_id,revision,unanswered_ready)
        VALUES('op','channel','70000000000',2,'still-pending',9,FALSE)''')
    cursor.execute('ALTER TABLE wazzup_messages ENABLE TRIGGER wazzup_unread_insert')
    init_unread_schema(cursor)
    assert read_state(cursor) == (1, 'still-pending', 10)
    cursor.execute('SELECT message_id FROM wazzup_unanswered_messages')
    assert cursor.fetchall() == [('still-pending',)]
    assert read_state(cursor, chat='other-chat') is None
    init_unread_schema(cursor)
    assert read_state(cursor) == (1, 'still-pending', 10)


@postgres
def test_schema_upgrade_preserves_manually_dismissed_zero_counter(pg):
    from wazzup.unread import init_unread_schema
    _, cursor, database, _ = pg
    db = database()
    db.store_wazzup_messages([inbound()])
    client_for(db).post('/api/wazzup/pilot/read', json=body())
    cursor.execute('UPDATE wazzup_chat_read_state SET unanswered_ready=FALSE')
    init_unread_schema(cursor)
    assert read_state(cursor) == (0, 'inbound-1', 2)


@postgres
@pytest.mark.parametrize('first', ['reply', 'inbound'])
def test_first_reply_racing_with_first_inbound_never_loses_answer(pg, first):
    connection, cursor, database, connect = pg
    db = database()
    other = connect()
    other_db = database(other)
    messages = {'reply': reply(), 'inbound': inbound()}
    db.store_wazzup_messages([messages[first]])
    started, done, failures = threading.Event(), threading.Event(), []

    def second_message():
        try:
            started.set()
            other_db.store_wazzup_messages([messages['inbound' if first == 'reply' else 'reply']])
            other.commit()
        except Exception as error:
            failures.append(error)
        finally:
            done.set()

    thread = threading.Thread(target=second_message, daemon=True)
    thread.start()
    assert started.wait(1)
    assert not done.wait(.15)
    connection.commit()
    thread.join(5)
    assert not thread.is_alive()
    assert not failures
    assert read_state(cursor)[0] == 0
