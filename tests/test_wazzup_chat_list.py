"""Список чатов: ждущие ответа — первыми (wazzup/chat_list.py).

SQL проверяется на изолированном локальном Postgres (WAZZUP_PILOT_TEST_PORT):
настоящие триггеры общего счётчика ставят чат в «ждёт ответа» и снимают его
ответом оператора. Без порта эти проверки пропускаются; разбор строки и
экранирование поиска проверяются всегда.
"""
import datetime as dt
import os
import uuid

import pytest

from wazzup.chat_list import attach_channel_counts, chat_list_item, chat_list_queries, escape_like
from wazzup.pilot_schema import init_schema

PORT = os.environ.get('WAZZUP_PILOT_TEST_PORT')
needs_pg = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')
T0 = dt.datetime(2026, 10, 9, 8, 0, tzinfo=dt.timezone.utc)


def test_row_maps_to_the_list_item_with_the_waiting_count():
    row = ('ch', '77000000001', 'whatsapp', 'Клиент', '77000000001', T0, 'Здравствуйте',
           False, 3, 2, 1, 2, 'm3', 'inbound')
    item = chat_list_item(row)
    assert item['lastMessageAt'] == '2026-10-09T08:00:00+00:00'
    assert (item['chatId'], item['unreadCount'], item['outboundCount']) == ('77000000001', 2, 1)
    assert (item['lastMessageId'], item['lastMessageStatus']) == ('m3', 'inbound')
    assert chat_list_item(row[:5] + (None,) + row[6:])['lastMessageAt'] is None


def test_search_metacharacters_are_literal():
    assert escape_like('7_7%\\') == '7\\_7\\%\\\\'


def test_only_the_op_account_has_the_waiting_counter():
    _, page = chat_list_queries('potok')
    assert 'wazzup_chat_read_state' not in page[0]
    _, page = chat_list_queries('op')
    assert 'wazzup_chat_read_state' in page[0]


@pytest.fixture
def pg():
    import psycopg2
    schema = 't_wazzup_list_' + uuid.uuid4().hex
    connection = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres',
                                  dbname='postgres', connect_timeout=3)
    cursor = connection.cursor()
    cursor.execute('CREATE SCHEMA ' + schema)
    cursor.execute('SET search_path TO ' + schema)
    cursor.execute('''
        CREATE TABLE wazzup_messages (
            message_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, chat_type TEXT,
            chat_id TEXT NOT NULL, dt TIMESTAMPTZ NOT NULL, is_echo BOOLEAN NOT NULL,
            type TEXT, text TEXT, content_uri TEXT, author_name TEXT, author_id TEXT,
            contact_name TEXT, contact_phone TEXT, status TEXT, sent_from_app TEXT,
            is_edited BOOLEAN NOT NULL DEFAULT FALSE, is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            account TEXT NOT NULL DEFAULT 'op', wazzup_dt TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE wazzup_chats (
            channel_id TEXT NOT NULL, chat_id TEXT NOT NULL, chat_type TEXT,
            contact_name TEXT, contact_phone TEXT, last_message_at TIMESTAMPTZ,
            last_message_text TEXT, last_message_is_echo BOOLEAN,
            messages_count INTEGER DEFAULT 0, inbound_count INTEGER DEFAULT 0,
            outbound_count INTEGER DEFAULT 0, updated_at TIMESTAMPTZ DEFAULT now(),
            account TEXT NOT NULL DEFAULT 'op', PRIMARY KEY(channel_id, chat_id)
        );
    ''')
    init_schema(cursor)
    connection.commit()
    try:
        yield cursor
    finally:
        connection.rollback()
        cursor.execute('DROP SCHEMA ' + schema + ' CASCADE')
        connection.commit()
        connection.close()


def chat(cursor, chat_id, minutes, account='op', channel='ch-a', name='Клиент'):
    cursor.execute('''INSERT INTO wazzup_chats(account, channel_id, chat_id, chat_type, contact_name,
            contact_phone, last_message_at) VALUES (%s,%s,%s,'whatsapp',%s,%s,%s)''',
                   (account, channel, chat_id, name, chat_id, T0 + dt.timedelta(minutes=minutes)))


def message(cursor, chat_id, minutes, echo=False, author=None, channel='ch-a', message_id=None,
            status=None, deleted=False):
    cursor.execute('''INSERT INTO wazzup_messages(message_id, account, channel_id, chat_id, chat_type, dt,
            is_echo, author_name, status, is_deleted) VALUES (%s,'op',%s,%s,'whatsapp',%s,%s,%s,%s,%s)''',
                   (message_id or uuid.uuid4().hex, channel, chat_id, T0 + dt.timedelta(minutes=minutes), echo,
                    author, status or ('sent' if echo else None), deleted))


def page(cursor, account='op', **filters):
    count, rows = chat_list_queries(account, **filters)
    cursor.execute(*count)
    total = cursor.fetchone()[0]
    cursor.execute(*rows)
    return total, [(item['chatId'], item['unreadCount']) for item in map(chat_list_item, cursor.fetchall())]


@needs_pg
def test_waiting_chats_lead_the_list_and_an_answer_returns_a_chat_to_its_time(pg):
    cursor = pg
    chat(cursor, '77000000001', 30)                # свежий, ответили
    message(cursor, '77000000001', 20)
    message(cursor, '77000000001', 30, echo=True, author='Оператор')
    chat(cursor, '77000000002', 10)                # клиент ждёт
    message(cursor, '77000000002', 10)
    chat(cursor, '77000000003', 5)                 # старый, без счётчика
    chat(cursor, '77000000004', 1)                 # тоже ждёт, самый старый
    message(cursor, '77000000004', 1)
    chat(cursor, '77000000009', 40, account='potok')
    assert page(cursor) == (4, [('77000000002', 1), ('77000000004', 1),
                                ('77000000001', 0), ('77000000003', 0)])
    # Страницы идут тем же порядком: вторая страница по одному — второй ждущий.
    assert page(cursor, limit=1, offset=1) == (4, [('77000000004', 1)])
    # Ответ оператора снимает ожидание — чат встаёт на своё место по времени
    # (время последнего сообщения в строке чата обновляет запись сообщения).
    message(cursor, '77000000002', 41, echo=True, author='Оператор')
    cursor.execute("UPDATE wazzup_chats SET last_message_at=%s WHERE chat_id='77000000002'",
                   (T0 + dt.timedelta(minutes=41),))
    assert page(cursor)[1] == [('77000000004', 1), ('77000000002', 0),
                               ('77000000001', 0), ('77000000003', 0)]


@needs_pg
def test_filters_apply_to_both_queries_and_other_accounts_keep_time_order(pg):
    cursor = pg
    chat(cursor, '77000000001', 30, name='Алия_1')
    chat(cursor, '77000000002', 10, channel='ch-b')
    message(cursor, '77000000002', 10, channel='ch-b')
    chat(cursor, '77000000003', 5, name='Алия')
    assert page(cursor, channel_id='ch-b') == (1, [('77000000002', 1)])
    # '_' в поиске — буква, не «любой символ».
    assert page(cursor, q='Алия_') == (1, [('77000000001', 0)])
    assert page(cursor, q='алия')[0] == 2
    chat(cursor, '77000000007', 50, account='potok')
    chat(cursor, '77000000008', 60, account='potok')
    assert page(cursor, account='potok') == (2, [('77000000008', 0), ('77000000007', 0)])


@needs_pg
def test_rows_carry_the_last_message_status_by_the_preview_rule(pg):
    """Галочки строки — у того же сообщения, что и превью: последнее неудалённое."""
    cursor = pg
    chat(cursor, '77000000001', 30)
    message(cursor, '77000000001', 10, message_id='in-1')
    message(cursor, '77000000001', 20, echo=True, author='Оператор', message_id='out-1', status='read')
    message(cursor, '77000000001', 25, echo=True, author='Оператор', message_id='out-2', status='delivered',
            deleted=True)
    chat(cursor, '77000000002', 5)
    message(cursor, '77000000002', 5, message_id='in-2', status='inbound')
    chat(cursor, '77000000003', 1)
    count, rows = chat_list_queries('op')
    cursor.execute(*rows)
    items = {item['chatId']: item for item in map(chat_list_item, cursor.fetchall())}
    assert (items['77000000001']['lastMessageId'], items['77000000001']['lastMessageStatus']) == ('out-1', 'read')
    assert (items['77000000002']['lastMessageId'], items['77000000002']['lastMessageStatus']) == ('in-2', 'inbound')
    assert (items['77000000003']['lastMessageId'], items['77000000003']['lastMessageStatus']) == (None, None)


@needs_pg
def test_chats_of_one_number_across_channels_and_the_arrow_count(pg):
    """«Чаты по каналам»: точный номер во всех каналах аккаунта; стрелка — где их больше одного."""
    cursor = pg
    chat(cursor, '77000000001', 30, channel='ch-a')
    chat(cursor, '77000000001', 10, channel='ch-b')
    chat(cursor, '77000000002', 20, channel='ch-a')
    chat(cursor, '770000000011', 25, channel='ch-a')      # похожий номер — не тот же
    chat(cursor, '77000000001', 40, account='potok', channel='ch-p')
    count, rows = chat_list_queries('op', chat_id='77000000001')
    cursor.execute(*count)
    assert cursor.fetchone()[0] == 2
    cursor.execute(*rows)
    assert [(item['channelId'], item['chatId']) for item in map(chat_list_item, cursor.fetchall())] == [
        ('ch-a', '77000000001'), ('ch-b', '77000000001')]
    _, rows = chat_list_queries('op')
    cursor.execute(*rows)
    items = attach_channel_counts(cursor, 'op', [chat_list_item(row) for row in cursor.fetchall()])
    assert {(item['channelId'], item['chatId']): item['channelsCount'] for item in items} == {
        ('ch-a', '77000000001'): 2, ('ch-b', '77000000001'): 2,
        ('ch-a', '77000000002'): 1, ('ch-a', '770000000011'): 1}
    assert attach_channel_counts(cursor, 'op', []) == []
