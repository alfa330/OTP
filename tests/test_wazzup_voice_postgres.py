"""Очередь текста голосовых (wazzup/voice.py) на настоящем Postgres.

Нужен одноразовый локальный кластер: WAZZUP_PILOT_TEST_PORT (как у
test_wazzup_pilot_persistence). Каждый тест — в своей схеме, удаляет только её.
Схема очереди — боевая (wazzup.voice_schema.init_schema); от архива сообщений
взяты только колонки, которые читает очередь. Распознавание подменено: здесь
проверяется, что очередь делает с его ответами.
"""
import os
import uuid
from contextlib import contextmanager

import pytest
from flask import Flask, jsonify, request

from wazzup import voice
from wazzup.voice_schema import init_schema

PORT = os.environ.get('WAZZUP_PILOT_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')
CHANNEL, OTHER_CHANNEL, CHAT = 'channel-a', 'channel-b', '77000000000'
URI = 'https://store.wazzup24.com/voice'


class Database:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def _get_cursor(self):
        cursor = self.connection.cursor()
        try:
            yield cursor
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            cursor.close()

    def one(self, sql, params=()):
        with self._get_cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()


@pytest.fixture
def db():
    import psycopg2
    schema = 't_wazzup_voice_' + uuid.uuid4().hex
    connection = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres',
                                  dbname='postgres', connect_timeout=3)
    with connection.cursor() as cursor:
        cursor.execute(f'CREATE SCHEMA {schema}')
        cursor.execute(f'SET search_path TO {schema}')
        cursor.execute("SET TIME ZONE 'UTC'")
        cursor.execute("""
            CREATE TABLE wazzup_messages (
                message_id TEXT PRIMARY KEY, account TEXT NOT NULL DEFAULT 'op',
                channel_id TEXT NOT NULL, chat_id TEXT NOT NULL, dt TIMESTAMPTZ NOT NULL,
                type TEXT, content_uri TEXT, is_deleted BOOLEAN NOT NULL DEFAULT FALSE)
        """)
        init_schema(cursor)
    connection.commit()
    yield Database(connection)
    connection.rollback()
    with connection.cursor() as cursor:
        cursor.execute(f'DROP SCHEMA {schema} CASCADE')
    connection.commit()
    connection.close()


def message(db, message_id, *, type='audio', age='1 minute', channel=CHANNEL, chat=CHAT,
            uri=URI, deleted=False, account='op'):
    with db._get_cursor() as cursor:
        cursor.execute("""INSERT INTO wazzup_messages
                              (message_id, account, channel_id, chat_id, dt, type, content_uri, is_deleted)
                          VALUES (%s, %s, %s, %s, now() - %s::interval, %s, %s, %s)""",
                       (message_id, account, channel, chat, age, type, uri, deleted))


def row(db, message_id):
    with db._get_cursor() as cursor:
        cursor.execute("""SELECT status, text, attempts, error, estimated_cost, model, prompt_version,
                                 audio_duration_ms, audio_tokens, output_tokens, usage IS NOT NULL,
                                 next_attempt_at > now(), lease_token IS NULL, source, requested_by
                            FROM wazzup_voice_transcripts WHERE message_id = %s""", (message_id,))
        found = cursor.fetchone()
    if found is None:
        return None
    keys = ('status', 'text', 'attempts', 'error', 'cost', 'model', 'prompt_version', 'duration_ms',
            'audio_tokens', 'output_tokens', 'has_usage', 'later', 'released', 'source', 'requested_by')
    return dict(zip(keys, found))


PAID = {'status': 'ready', 'text': 'Қашан келесіз?', 'language': 'kk', 'duration': 3.2,
        'model': 'gemini-3.5-flash-lite', 'model_version': 'v1', 'latency_ms': 1800, 'cost': 0.0004,
        'audio_bytes': 25600,
        'usage': {'candidatesTokenCount': 12,
                  'promptTokensDetails': [{'modality': 'AUDIO', 'tokenCount': 80},
                                          {'modality': 'TEXT', 'tokenCount': 490}]}}


def test_discover_queues_recent_voice_only_once(db):
    message(db, 'voice-new')
    message(db, 'voice-old', age='3 days')
    message(db, 'text', type='text', uri=None)
    message(db, 'voice-deleted', deleted=True)
    message(db, 'voice-no-file', uri=None)
    assert voice.discover(db) == 1
    assert voice.discover(db) == 0
    assert row(db, 'voice-new')['status'] == 'pending' and row(db, 'voice-new')['source'] == 'arrival'
    assert row(db, 'voice-old') is None


def test_a_claim_leases_and_a_ready_answer_keeps_what_was_paid(db):
    message(db, 'v1')
    voice.discover(db)
    [item] = voice._claim(db, 3)
    assert item['content_uri'] == URI and item['attempts'] == 1
    assert voice._claim(db, 3) == []          # leased: another process does not take it
    voice._finish(db, item, PAID)
    saved = row(db, 'v1')
    assert saved['status'] == 'ready' and saved['text'] == 'Қашан келесіз?' and saved['released']
    assert float(saved['cost']) == pytest.approx(0.0004)
    assert (saved['model'], saved['prompt_version'], saved['duration_ms']) == ('gemini-3.5-flash-lite', 1, 3200)
    assert (saved['audio_tokens'], saved['output_tokens'], saved['has_usage']) == (80, 12, True)
    assert voice._paid_today(db) == 1


def test_failures_back_off_add_up_their_cost_and_give_up(db):
    message(db, 'v2')
    voice.discover(db)
    for attempt in range(1, voice.MAX_ATTEMPTS + 1):
        db.one("UPDATE wazzup_voice_transcripts SET next_attempt_at = now() WHERE message_id = 'v2' "
               "RETURNING 1")
        [item] = voice._claim(db, 1)
        assert item['attempts'] == attempt
        voice._retry(db, item, voice.voice_asr.VoiceError('расшифровка не похожа на запись'),
                     {'cost': 0.001, 'model': 'gemini-3.5-flash-lite', 'usage': PAID['usage']})
        saved = row(db, 'v2')
        if attempt < voice.MAX_ATTEMPTS:
            assert saved['status'] == 'pending' and saved['later'] and saved['released']
            assert voice._claim(db, 1) == []   # not before its back-off
    saved = row(db, 'v2')
    assert saved['status'] == 'failed' and 'не похожа' in saved['error']
    assert float(saved['cost']) == pytest.approx(0.001 * voice.MAX_ATTEMPTS)


def test_a_stale_lease_is_taken_over_and_the_old_owner_cannot_write(db):
    message(db, 'v3')
    voice.discover(db)
    [first] = voice._claim(db, 1)
    db.one("UPDATE wazzup_voice_transcripts SET lease_until = now() - interval '1 second' RETURNING 1")
    [second] = voice._claim(db, 1)
    voice._finish(db, first, dict(PAID, text='от первого'))   # its lease is gone
    assert row(db, 'v3')['status'] == 'pending'
    voice._finish(db, second, PAID)
    assert row(db, 'v3')['text'] == 'Қашан келесіз?'


def test_rows_of_messages_gone_from_the_archive_are_swept(db):
    message(db, 'v4')
    message(db, 'v5')
    voice.discover(db)
    db.one("UPDATE wazzup_voice_transcripts SET created_at = now() - interval '2 hours' RETURNING 1")
    db.one("DELETE FROM wazzup_messages WHERE message_id = 'v4' RETURNING 1")
    voice._maintenance_at = 0.0
    voice._maintenance(db)
    assert row(db, 'v4') is None and row(db, 'v5') is not None


def app_for(db, *, user_id=7, scope=None, account='op'):
    app = Flask(__name__)
    woken = []
    app.register_blueprint(voice.build_voice_blueprint(
        db=db, require_api_key=lambda f: f, guard=lambda: (user_id, None),
        preflight=lambda: ('', 204), account_arg=lambda: request.args.get('account', account),
        channel_scope=lambda: scope, on_queued=lambda: woken.append(1)))
    return app.test_client(), woken


def post(client, ids, *, channel=CHANNEL, retry=None, account='op'):
    return client.post(f'/api/wazzup/voice-transcripts?account={account}',
                       json={'channelId': channel, 'chatId': CHAT, 'messageIds': ids, 'retry': retry or []})


def test_route_queues_shown_voice_and_answers_only_for_this_chat(db):
    voice._queued_by_user.clear()
    message(db, 'old-voice', age='20 days')
    message(db, 'ready-voice')
    message(db, 'text-1', type='text', uri=None)
    message(db, 'elsewhere', channel=OTHER_CHANNEL)
    message(db, 'no-file', uri=None)
    voice.discover(db)   # the 20-day-old one is outside the arrival window
    [item] = [i for i in voice._claim(db, 5) if i['message_id'] == 'ready-voice']
    voice._finish(db, item, PAID)
    client, woken = app_for(db)
    response = post(client, ['old-voice', 'ready-voice', 'text-1', 'elsewhere', 'no-file', 'missing'])
    assert response.status_code == 200
    items = {i['messageId']: i for i in response.get_json()['items']}
    assert set(items) == {'old-voice', 'ready-voice'}
    assert items['ready-voice'] == {'messageId': 'ready-voice', 'status': 'ready', 'text': 'Қашан келесіз?'}
    assert items['old-voice']['status'] == 'pending' and woken == [1]
    queued = row(db, 'old-voice')
    assert queued['source'] == 'view' and queued['requested_by'] == 7
    # Повторный опрос ничего не ставит заново и не будит обработчик.
    assert post(client, ['old-voice']).get_json()['items'][0]['status'] == 'pending' and woken == [1]


def test_route_retry_requeues_only_a_failed_one(db):
    message(db, 'failed-voice')
    message(db, 'ready-voice')
    voice.discover(db)
    db.one("UPDATE wazzup_voice_transcripts SET status = 'failed', attempts = 4, error = 'x' "
           "WHERE message_id = 'failed-voice' RETURNING 1")
    db.one("UPDATE wazzup_voice_transcripts SET status = 'ready', text = 'Алло' "
           "WHERE message_id = 'ready-voice' RETURNING 1")
    client, woken = app_for(db)
    items = {i['messageId']: i for i in post(client, [], retry=['failed-voice', 'ready-voice']).get_json()['items']}
    assert items['failed-voice']['status'] == 'pending' and items['ready-voice']['text'] == 'Алло'
    again = row(db, 'failed-voice')
    assert (again['attempts'], again['error'], again['source']) == (0, None, 'retry') and woken == [1]


def test_route_keeps_channel_scope_and_rejects_bad_input(db):
    message(db, 'v6')
    client, _ = app_for(db, scope=frozenset({OTHER_CHANNEL}))
    assert post(client, ['v6']).status_code == 403
    client, _ = app_for(db)
    assert client.post('/api/wazzup/voice-transcripts', json={'channelId': CHANNEL}).status_code == 400
    assert post(client, ['x'] * (voice.MAX_IDS + 1)).status_code == 400
    assert post(client, [123]).status_code == 400
    assert row(db, 'v6') is None


def test_switched_off_feature_queues_nothing_but_still_shows_ready_text(db, monkeypatch):
    message(db, 'v7')
    message(db, 'v8')
    voice.discover(db)
    db.one("UPDATE wazzup_voice_transcripts SET status = 'ready', text = 'Готово' "
           "WHERE message_id = 'v7' RETURNING 1")
    db.one("DELETE FROM wazzup_voice_transcripts WHERE message_id = 'v8' RETURNING 1")
    monkeypatch.setenv('WAZZUP_VOICE_TRANSCRIPTS', 'off')
    client, woken = app_for(db)
    items = post(client, ['v7', 'v8']).get_json()['items']
    assert items == [{'messageId': 'v7', 'status': 'ready', 'text': 'Готово'}] and woken == []
    assert row(db, 'v8') is None


def test_an_answer_that_cannot_be_stored_still_runs_out_of_attempts(db, monkeypatch):
    message(db, 'nul-voice')
    voice.discover(db)
    stored_text = dict(PAID, text='Алло\x00')       # Postgres refuses NUL in text
    monkeypatch.setattr(voice, 'process', lambda item: ('final', stored_text))
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as pool:
        for _ in range(voice.MAX_ATTEMPTS + 3):
            db.one("UPDATE wazzup_voice_transcripts SET next_attempt_at = now() RETURNING 1")
            voice.drain(db, pool)
    saved = row(db, 'nul-voice')
    assert saved['status'] == 'failed' and saved['attempts'] == voice.MAX_ATTEMPTS
    assert 'не удалось сохранить' in saved['error']
    assert float(saved['cost']) == pytest.approx(0.0004 * voice.MAX_ATTEMPTS)


def test_spent_attempts_are_never_claimed_and_the_sweep_closes_them(db):
    message(db, 'stuck')
    voice.discover(db)
    db.one(f"UPDATE wazzup_voice_transcripts SET attempts = {voice.MAX_ATTEMPTS} RETURNING 1")
    assert voice._claim(db, 3) == []
    voice._maintenance_at = 0.0
    voice._maintenance(db)
    assert row(db, 'stuck')['status'] == 'failed'


def test_the_daily_fuse_counts_attempts_when_they_start(db):
    message(db, 'd1')
    message(db, 'd2')
    voice.discover(db)
    db.one("UPDATE wazzup_voice_transcripts SET created_at = now() - interval '3 days' RETURNING 1")
    items = voice._claim(db, 5)
    assert len(items) == 2 and voice._paid_today(db) == 2
    for item in items:
        voice._retry(db, item, 'x')
    db.one("UPDATE wazzup_voice_transcripts SET status = 'failed' RETURNING 1")
    client, _ = app_for(db)
    post(client, [], retry=['d1', 'd2'])      # «Повторить» starts attempts over…
    assert voice._paid_today(db) == 2         # …but not the day's count


def test_a_row_created_by_someone_else_meanwhile_is_reported_not_dropped(db, monkeypatch):
    message(db, 'raced')
    real_allow = voice._allow_queue

    def allow_and_race(user_id, count, now=None):
        # Discovery commits the row between the route's read and its insert.
        voice.discover(Database(__import__('psycopg2').connect(
            host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
            options=f"-c search_path={db.one('SHOW search_path')[0]}")))
        return real_allow(user_id, count, now)
    monkeypatch.setattr(voice, '_allow_queue', allow_and_race)
    client, _ = app_for(db)
    assert post(client, ['raced']).get_json()['items'] == [{'messageId': 'raced', 'status': 'pending', 'text': None}]


def test_a_hanging_message_does_not_hold_the_others(db, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    for name in ('slow', 'b', 'c', 'd', 'e'):
        message(db, name)
    voice.discover(db)
    db.one("UPDATE wazzup_voice_transcripts SET next_attempt_at = now() - interval '1 minute' "
           "WHERE message_id = 'slow' RETURNING 1")    # claimed first
    order, others_done = [], threading.Event()

    def process(item):
        if item['message_id'] == 'slow':
            # Vertex hangs on this one until the others are through (or 5 s).
            others_done.wait(5)
        order.append(item['message_id'])
        if len(order) == 4 and 'slow' not in order:
            others_done.set()
        return 'final', dict(PAID)
    monkeypatch.setattr(voice, 'process', process)
    with ThreadPoolExecutor(voice.WORKERS) as pool:
        assert voice.drain(db, pool) is False
    assert order[-1] == 'slow' and sorted(order) == ['b', 'c', 'd', 'e', 'slow']
    assert all(row(db, name)['status'] == 'ready' for name in ('slow', 'b', 'c', 'd', 'e'))
