# -*- coding: utf-8 -*-
"""Чаты Syntony на настоящем Postgres: SYNTONY_CHATS_TEST_PORT — порт локального кластера.

Подпись, разбор и ответы ручки без базы проверяет tests/test_syntony_chats.py. Здесь —
настоящие запросы и схема: повтор события ничего не портит, старый снимок не переписывает
новый, статус идёт только «вперёд», событие статуса раньше сообщения не теряется — и ручка
поверх всего этого с подписанными запросами.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным
именем, в конце она удаляется. Люди и номера выдуманы.

    SYNTONY_CHATS_TEST_PORT=55473 python -m pytest tests/test_syntony_chats_postgres.py
"""

import contextlib
import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from syntony_chats import rules, schema, store  # noqa: E402
from test_syntony_chats import (  # noqa: E402
    CONVERSATION, MESSAGE, NOW, SECRET, URL, body_of, created_event, signed, status_event)

PORT = os.environ.get('SYNTONY_CHATS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (SYNTONY_CHATS_TEST_PORT)')


def at(text):
    return rules.parse_time(text)


class _Db:
    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT),
                                user=os.environ.get('SYNTONY_CHATS_TEST_USER', 'postgres'),
                                dbname='postgres', connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.name)
            cur.execute("SET statement_timeout TO '20s'")
        conn.commit()
        return conn

    @contextlib.contextmanager
    def _get_cursor(self):
        conn = self.connect()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    def one(self, sql, params=()):
        with self._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def message(self, message_id=MESSAGE['id']):
        with self._get_cursor() as cur:
            cur.execute('SELECT * FROM syntony_chat_messages WHERE id = %s', (message_id,))
            row = cur.fetchone()
            return dict(zip([c[0] for c in cur.description], row)) if row else None

    def conversation(self, conversation_id=CONVERSATION['id']):
        with self._get_cursor() as cur:
            cur.execute('SELECT * FROM syntony_chat_conversations WHERE id = %s', (conversation_id,))
            row = cur.fetchone()
            return dict(zip([c[0] for c in cur.description], row)) if row else None


@pytest.fixture()
def db():
    import psycopg2
    name = 'syntony_chats_' + uuid.uuid4().hex[:12]
    admin = psycopg2.connect(host='127.0.0.1', port=int(PORT),
                             user=os.environ.get('SYNTONY_CHATS_TEST_USER', 'postgres'),
                             dbname='postgres', connect_timeout=3)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + name)
    database = _Db(name)
    try:
        with database._get_cursor() as cur:
            schema.init_syntony_chats_schema(cur)
            schema.init_syntony_chats_schema(cur)   # повторный старт сервера — без ошибок
        yield database
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + name + ' CASCADE')
        admin.close()


def created(db, message=None, conversation=None, occurred='2025-11-17T09:40:02Z'):
    payload = created_event(message, conversation, occurred)
    with db._get_cursor() as cur:
        return store.apply_message_created(cur, payload['data'], at(payload['occurredAt']))


def status(db, value, status_at, message_id=MESSAGE['id'], error=None):
    payload = status_event(value, message_id=message_id, status_at=status_at, error=error)
    with db._get_cursor() as cur:
        return store.apply_message_status(cur, payload['data'], at(payload['occurredAt']))


def test_message_created_writes_conversation_and_message(db):
    assert created(db) == 'inserted'
    msg, conv = db.message(), db.conversation()
    assert (msg['conversation_id'], msg['direction'], msg['sender_type'], msg['origin']) == (
        CONVERSATION['id'], 'outbound', 'human', 'crm_operator')
    assert (msg['author_name'], msg['content_type'], msg['text']) == (
        'Тестовый Оператор', 'image', 'Вот образец техпаспорта')
    assert msg['media'] == MESSAGE['media']
    assert (msg['status'], msg['status_rank']) == ('read', 3)
    assert msg['read_at'] == at('2025-11-17T09:41:10Z')
    assert msg['created_at'] == at('2025-11-17T09:40:02Z')
    assert msg['first_seen_via'] == 'webhook' and msg['extra'] is None
    assert (conv['park_name'], conv['channel_phone'], conv['contact_phone']) == (
        'Global', '77000000000', '77000000001')
    assert (conv['assigned_kind'], conv['message_count'], conv['lead_id']) == (
        'user', 14, CONVERSATION['leadId'])
    assert conv['handoff']['summary'] == 'Вопрос про штраф'
    assert conv['snapshot_at'] == at('2025-11-17T09:40:02Z')


def test_replay_changes_nothing(db):
    created(db)
    before = (db.message()['updated_at'], db.conversation()['updated_at'])
    assert created(db) == 'unchanged'
    assert (db.message()['updated_at'], db.conversation()['updated_at']) == before
    assert db.one('SELECT count(*) FROM syntony_chat_messages')[0] == 1


def test_old_conversation_snapshot_does_not_overwrite_newer(db):
    newer = dict(CONVERSATION, messageCount=20, status='closed', closedAt='2025-11-17T12:00:00Z')
    second = dict(MESSAGE, id='0f4b7c31-0000-4000-8000-000000000002')
    created(db, second, newer, occurred='2025-11-17T12:00:00Z')
    created(db, occurred='2025-11-17T09:40:02Z')      # повтор старого события пришёл позже
    conv = db.conversation()
    assert (conv['message_count'], conv['status']) == (20, 'closed')
    assert conv['snapshot_at'] == at('2025-11-17T12:00:00Z')
    assert db.one('SELECT count(*) FROM syntony_chat_messages')[0] == 2
    created(db, conversation=dict(CONVERSATION, messageCount=21), occurred='2025-11-17T12:05:00Z')
    assert db.conversation()['message_count'] == 21


def test_status_goes_only_forward_but_remembers_every_moment(db):
    created(db, dict(MESSAGE, status='sent', statusAt='2025-11-17T09:40:03Z'))
    assert status(db, 'read', '2025-11-17T09:45:00Z') == 'updated'
    # «Доставлено» опоздало и пришло после «прочитано»: статус не откатывается,
    # а момент доставки всё равно записан.
    assert status(db, 'delivered', '2025-11-17T09:41:00Z') == 'updated'
    msg = db.message()
    assert (msg['status'], msg['status_rank'], msg['status_at']) == ('read', 3, at('2025-11-17T09:45:00Z'))
    assert msg['sent_at'] == at('2025-11-17T09:40:03Z')
    assert msg['delivered_at'] == at('2025-11-17T09:41:00Z')
    assert msg['read_at'] == at('2025-11-17T09:45:00Z')
    assert status(db, 'delivered', '2025-11-17T09:41:00Z') == 'unchanged'   # повтор


def test_failed_is_final_and_keeps_provider_error(db):
    created(db, dict(MESSAGE, status='sent', statusAt='2025-11-17T09:40:03Z'))
    status(db, 'failed', '2025-11-17T09:41:10Z', error={'code': '131047', 'message': 'Re-engagement message'})
    status(db, 'delivered', '2025-11-17T09:42:00Z')
    status(db, 'read', '2025-11-17T09:43:00Z')
    msg = db.message()
    assert (msg['status'], msg['error_code'], msg['error_message']) == (
        'failed', '131047', 'Re-engagement message')
    assert msg['failed_at'] == at('2025-11-17T09:41:10Z')
    assert msg['delivered_at'] == at('2025-11-17T09:42:00Z')
    assert msg['read_at'] == at('2025-11-17T09:43:00Z')
    assert msg['status_at'] == at('2025-11-17T09:41:10Z')


def test_status_before_message_is_not_lost(db):
    # Порядок message.created и message.status не гарантирован (§9.5).
    assert status(db, 'delivered', '2025-11-17T09:41:00Z') == 'inserted'
    stub = db.message()
    assert (stub['status'], stub['text'], stub['content_at'], stub['first_seen_via']) == (
        'delivered', None, None, 'status')
    assert stub['conversation_id'] == CONVERSATION['id']
    assert created(db, dict(MESSAGE, status='sent', statusAt='2025-11-17T09:40:03Z')) == 'updated'
    msg = db.message()
    assert (msg['status'], msg['text'], msg['author_name']) == ('delivered', 'Вот образец техпаспорта',
                                                                 'Тестовый Оператор')
    assert msg['sent_at'] == at('2025-11-17T09:40:03Z')
    assert msg['delivered_at'] == at('2025-11-17T09:41:00Z')
    assert msg['content_at'] == at('2025-11-17T09:40:02Z')
    assert msg['first_seen_via'] == 'status'
    assert db.conversation()['park_name'] == 'Global'


def test_newer_content_replaces_and_older_does_not(db):
    created(db)
    edited = dict(MESSAGE, text='Вот образец техпаспорта (исправлено)', editedAt='2025-11-17T09:50:00Z')
    created(db, edited, occurred='2025-11-17T09:55:00Z')
    assert db.message()['text'] == 'Вот образец техпаспорта (исправлено)'
    created(db, occurred='2025-11-17T09:40:02Z')        # опоздавший повтор исходного события
    msg = db.message()
    assert msg['text'] == 'Вот образец техпаспорта (исправлено)'
    assert msg['edited_at'] == at('2025-11-17T09:50:00Z')
    assert msg['status'] == 'read'


def test_nul_and_unknown_fields_are_stored(db):
    message = dict(MESSAGE, text='при\x00вет', reactions=[{'emoji': '👍'}])
    conversation = dict(CONVERSATION, contact=dict(CONVERSATION['contact'], name='Имя\x00'))
    assert created(db, message, conversation) == 'inserted'
    msg = db.message()
    assert msg['text'] == 'привет' and msg['extra'] == {'reactions': [{'emoji': '👍'}]}
    assert db.conversation()['contact_name'] == 'Имя'


def test_message_without_conversation_id_takes_it_from_the_snapshot(db):
    message = dict(MESSAGE)
    del message['conversationId']
    created(db, message)
    assert db.message()['conversation_id'] == CONVERSATION['id']


def test_webhook_end_to_end_with_signed_requests(db):
    from flask import Flask
    from syntony_chats import routes

    app = Flask(__name__)
    app.register_blueprint(routes.build_syntony_chats_blueprint(
        db=db, environ={rules.ENV_SECRET: SECRET}, clock=lambda: NOW))
    client = app.test_client()

    def send(payload):
        body = body_of(payload)
        return client.post(URL, data=body, headers=signed(body))

    first = created_event(dict(MESSAGE, status='sent', statusAt='2025-11-17T09:40:03Z'))
    assert send(status_event('read', status_at='2025-11-17T09:45:00Z')).status_code == 200
    assert send(first).status_code == 200
    assert send(first).status_code == 200             # повтор с тем же eventId
    assert send({'event': 'webhook.test', 'eventId': 'evt_t', 'data': {}}).status_code == 200
    msg = db.message()
    assert (msg['status'], msg['text']) == ('read', 'Вот образец техпаспорта')
    assert msg['sent_at'] == at('2025-11-17T09:40:03Z') and msg['read_at'] == at('2025-11-17T09:45:00Z')
    assert db.one('SELECT count(*) FROM syntony_chat_messages')[0] == 1
    assert db.one('SELECT count(*) FROM syntony_chat_conversations')[0] == 1

    # Чужая подпись ничего не пишет.
    body = body_of(created_event(dict(MESSAGE, id='0f4b7c31-0000-4000-8000-0000000000ff')))
    assert client.post(URL, data=body, headers=signed(body, secret='other-synthetic')).status_code == 403
    assert db.one('SELECT count(*) FROM syntony_chat_messages')[0] == 1


def test_schema_missing_answers_503_so_syntony_retries(db):
    from flask import Flask
    from syntony_chats import routes

    with db._get_cursor() as cur:
        cur.execute('DROP TABLE syntony_chat_messages')
    app = Flask(__name__)
    app.register_blueprint(routes.build_syntony_chats_blueprint(
        db=db, environ={rules.ENV_SECRET: SECRET}, clock=lambda: NOW))
    body = body_of(created_event())
    response = app.test_client().post(URL, data=body, headers=signed(body))
    assert response.status_code == 503
    # Транзакция откатилась целиком: диалог без сообщения не остался.
    assert db.conversation() is None


def test_broken_emoji_half_and_huge_counter_are_stored(db):
    # JSON.stringify отдаёт одинокую половинку суррогатной пары как \ud83d — Python
    # получает символ, который драйвер не запишет в UTF-8. Счётчик сверх INTEGER — тоже.
    raw = body_of(created_event(dict(MESSAGE, text='обрыв XX'),
                                dict(CONVERSATION, messageCount=10 ** 12)))
    raw = raw.replace('XX'.encode(), b'\\ud83d')
    payload = rules.parse_event(raw)
    with db._get_cursor() as cur:
        assert store.apply_message_created(cur, payload['data'], payload['occurred_at']) == 'inserted'
    assert db.message()['text'] == 'обрыв �'
    assert db.conversation()['message_count'] is None


def test_data_error_in_the_database_is_a_422_not_an_endless_retry(db, monkeypatch):
    from flask import Flask
    from syntony_chats import routes

    app = Flask(__name__)
    app.register_blueprint(routes.build_syntony_chats_blueprint(
        db=db, environ={rules.ENV_SECRET: SECRET}, clock=lambda: NOW))
    # Ранг вне SMALLINT — настоящая ошибка класса 22 от Postgres (numeric_value_out_of_range):
    # повтор упал бы так же и сутки держал очередь вебхука.
    monkeypatch.setitem(rules.STATUS_RANKS, 'read', 40000)
    body = body_of(created_event())
    response = app.test_client().post(URL, data=body, headers=signed(body))
    assert response.status_code == 422
    assert response.get_json() == {'ok': False, 'error': 'unprocessable'}
    assert db.conversation() is None and db.message() is None
