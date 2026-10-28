"""Поиск «Чатов ОП» по тексту сообщений (wazzup/chat_search.py).

Разбор запроса, указателя страницы и куска текста проверяется всегда. SQL —
на изолированном локальном Postgres (WAZZUP_PILOT_TEST_PORT): порядок, страницы
без дыр и повторов, удалённые и чужой аккаунт, метасимволы, строка чата у
найденного, окно ленты от найденного сообщения и построение индексов.
"""
import ast
import datetime as dt
import logging
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from flask import Flask, jsonify, request

from tests import source_cache
from wazzup import chat_search
from wazzup.chat_list import chat_list_item, chat_list_queries
from wazzup.chat_search import (decode_cursor, encode_cursor, focus_window, message_hits_query,
                                recent_hits_query, search_messages, searchable_query, snippet)
from wazzup.pilot import MESSAGE_SELECT
from wazzup.pilot_schema import init_schema

PORT = os.environ.get('WAZZUP_PILOT_TEST_PORT')
needs_pg = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')
T0 = dt.datetime(2026, 10, 9, 8, 0, tzinfo=dt.timezone.utc)


def test_text_is_searched_from_three_letters_or_digits_in_a_row():
    assert searchable_query('  водительское   удостоверение ') == 'водительское удостоверение'
    assert searchable_query('сәл') == 'сәл'
    assert searchable_query('777') == '777'
    assert searchable_query('ID 77') == ''           # ни одной триграммы — индекс не сузит
    assert searchable_query('а б в') == ''
    assert searchable_query('7_7') == ''              # '_' не буква
    assert searchable_query('%%%') == ''
    assert searchable_query(None) == ''
    assert len(searchable_query('я' * 500)) == chat_search.MAX_QUERY_LENGTH


def test_cursor_round_trip_and_rejects_damaged_values():
    moment = dt.datetime(2026, 10, 9, 8, 0, 1, 250000, tzinfo=dt.timezone.utc)
    assert decode_cursor(encode_cursor(moment, 'wamid.A|B')) == (moment, 'wamid.A|B')
    assert decode_cursor('') is None and decode_cursor(None) is None
    for broken in ('2026-10-09T08:00:00+00:00', '2026-10-09T08:00:00+00:00|', 'nonsense|m1',
                   '2026-10-09T08:00:00|m1', '2026-10-09T08:00:00+00:00|' + 'x' * 400):
        with pytest.raises(ValueError):
            decode_cursor(broken)


def test_snippet_keeps_the_match_visible_in_a_narrow_row():
    assert snippet('Привет,\nкак   дела?', 'как') == 'Привет, как дела?'
    text = ' '.join(['слово'] * 40) + ' УДОСТОВЕРЕНИЕ водителя ' + ' '.join(['хвост'] * 80)
    piece = snippet(text, 'удостоверение')
    assert piece.startswith('…слово') and piece.endswith('…')
    assert 'УДОСТОВЕРЕНИЕ' in piece and piece.index('УДОСТОВЕРЕНИЕ') <= chat_search.SNIPPET_BEFORE + 1
    # Не нашлось (иное правило регистра) — начало сообщения, а не пустота.
    assert snippet('Начало текста', 'чего-нет') == 'Начало текста'
    assert snippet(None, 'abc') == ''


def test_hits_query_escapes_metacharacters_and_continues_after_the_cursor():
    sql, params = message_hits_query('op', '3%_\\', limit=5)
    assert params == ['op', '%3\\%\\_\\\\%', 6]
    assert 'NOT m.is_deleted' in sql and 'ORDER BY m.dt DESC, m.message_id DESC' in sql
    moment = T0
    sql, params = message_hits_query('potok', 'abc', limit=2, before=(moment, 'm9'))
    assert params == ['potok', '%abc%', moment, moment, 'm9', 3]
    assert '(m.dt, m.message_id) < (%s, %s)' in sql
    sql, params = recent_hits_query('op', 'a_c', limit=2, before=(moment, 'm9'), window=7)
    assert params == ['op', moment, moment, 'm9', 7, '%a\\_c%', 3, 7]
    assert sql.count('%s') == len(params)


@pytest.fixture
def pg():
    import psycopg2
    schema = 't_wazzup_search_' + uuid.uuid4().hex
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
            quoted_message_id TEXT, quoted_is_echo BOOLEAN, quoted_type TEXT, quoted_text TEXT,
            quoted_author_name TEXT,
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
        yield schema, connection, cursor
    finally:
        connection.rollback()
        cursor.execute('DROP SCHEMA ' + schema + ' CASCADE')
        connection.commit()
        connection.close()


def chat(cursor, chat_id, account='op', channel='ch-a', name='Клиент'):
    cursor.execute('''INSERT INTO wazzup_chats(account, channel_id, chat_id, chat_type, contact_name,
            contact_phone, last_message_at, messages_count) VALUES (%s,%s,%s,'whatsapp',%s,%s,%s,1)''',
                   (account, channel, chat_id, name, chat_id, T0))


def message(cursor, chat_id, seconds, text, *, account='op', channel='ch-a', message_id=None,
            echo=False, deleted=False, kind='text'):
    message_id = message_id or uuid.uuid4().hex
    cursor.execute('''INSERT INTO wazzup_messages(message_id, account, channel_id, chat_id, chat_type, dt,
            is_echo, type, text, author_name, status, is_deleted)
            VALUES (%s,%s,%s,%s,'whatsapp',%s,%s,%s,%s,%s,%s,%s)''',
                   (message_id, account, channel, chat_id, T0 + dt.timedelta(seconds=seconds), echo, kind,
                    text, 'Оператор' if echo else None, 'sent' if echo else None, deleted))
    return message_id


def all_pages(cursor, account, query, limit):
    seen, before, pages = [], None, 0
    while True:
        items, after = search_messages(cursor, account, query, limit, before)
        seen.extend(item['messageId'] for item in items)
        pages += 1
        if not after:
            return seen, pages
        before = decode_cursor(after)


@needs_pg
def test_hits_are_newest_first_case_insensitive_and_scoped(pg):
    _, _, cursor = pg
    chat(cursor, '77000000001', name='Айдар')
    chat(cursor, '77000000002')
    chat(cursor, '77000000003', account='potok', channel='ch-p')
    old = message(cursor, '77000000001', 10, 'Отправьте ВОДИТЕЛЬСКОЕ удостоверение')
    message(cursor, '77000000001', 20, 'Спасибо')
    new = message(cursor, '77000000002', 30, 'водительское отправил', echo=True)
    message(cursor, '77000000002', 40, 'водительское — удалено', deleted=True)
    message(cursor, '77000000003', 50, 'водительское в Потоке', account='potok', channel='ch-p')
    items, after = search_messages(cursor, 'op', 'водительское')
    assert [item['messageId'] for item in items] == [new, old] and after is None
    first = items[0]
    assert (first['isEcho'], first['type'], first['status']) == (True, 'text', 'sent')
    assert dt.datetime.fromisoformat(first['dt']) == T0 + dt.timedelta(seconds=30)
    # Строка чата у найденного — та же, что в списке: открывается тем же путём.
    _, page = chat_list_queries('op', chat_id='77000000002')
    cursor.execute(*page)
    assert first['chat'] == chat_list_item(cursor.fetchone())
    assert items[1]['chat']['contactName'] == 'Айдар'
    assert [item['chatId'] for item in search_messages(cursor, 'potok', 'ВОДИТЕЛЬСКОЕ')[0]] == ['77000000003']
    # Метасимволы — буквально.
    message(cursor, '77000000002', 60, 'комиссия 3% в день')
    message(cursor, '77000000002', 70, 'комиссия 35 в день')
    assert [item['text'] for item in search_messages(cursor, 'op', 'комиссия 3%')[0]] == ['комиссия 3% в день']


@needs_pg
@pytest.mark.parametrize('window', [1, 2, 3, 7, 3000])
def test_pages_follow_each_other_without_gaps_or_repeats_even_at_equal_times(pg, monkeypatch, window):
    """Любая граница свежего окна (шаг 1 → шаг 2) — без дыр и повторов."""
    monkeypatch.setattr(chat_search, 'RECENT_WINDOW', window)
    _, _, cursor = pg
    chat(cursor, '77000000001')
    expected = []
    for index in range(23):
        # Пары с одинаковым временем: порядок держит message_id.
        expected.append(message(cursor, '77000000001', index // 2, f'регистрация номер {index}',
                                message_id=f'm{index:02d}'))
        if index % 5 == 0:
            message(cursor, '77000000001', index // 2, 'без совпадения', message_id=f'n{index:02d}')
            message(cursor, '77000000001', index // 2, 'регистрация удалена', message_id=f'd{index:02d}',
                    deleted=True)
    expected.sort(key=lambda mid: (int(mid[1:]) // 2, mid), reverse=True)
    for limit in (1, 2, 5, 23, 50):
        assert all_pages(cursor, 'op', 'регистрация', limit) == (expected, -(-23 // limit) if limit < 23 else 1)


class Recording:
    """Курсор, запоминающий текст запросов: какой шаг поиска выполнялся."""

    def __init__(self, cursor):
        self.cursor, self.statements = cursor, []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        return self.cursor.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self.cursor, name)


@needs_pg
def test_a_frequent_word_is_served_by_the_fresh_window_alone(pg, monkeypatch):
    monkeypatch.setattr(chat_search, 'RECENT_WINDOW', 10)
    _, _, cursor = pg
    chat(cursor, '77000000001')
    for index in range(30):
        message(cursor, '77000000001', index, 'здравствуйте' if index % 2 else 'спасибо', message_id=f'm{index:02d}')
    frequent = Recording(cursor)
    items, after = search_messages(frequent, 'op', 'Здравствуйте', limit=4)
    assert [item['messageId'] for item in items] == ['m29', 'm27', 'm25', 'm23'] and after
    assert not any('enable_indexscan' in sql for sql in frequent.statements)
    # Редкое — свежее окно не набрало страницу, дальше индекс.
    message(cursor, '77000000001', -5, 'редкое слово', message_id='old')
    rare = Recording(cursor)
    items, after = search_messages(rare, 'op', 'редкое', limit=4)
    assert [item['messageId'] for item in items] == ['old'] and after is None
    switched = [index for index, sql in enumerate(rare.statements) if 'enable_indexscan' in sql]
    assert [rare.statements[index].split('= ')[1] for index in switched] == ['off', 'on']
    # Между ними — ровно шаг 2; строки чатов читаются уже с обычными планами.
    assert switched[1] - switched[0] == 2 and 'unnest' in rare.statements[switched[1] + 1]
    # Окно не заполнено целиком (архив короче) — второй шаг не нужен.
    monkeypatch.setattr(chat_search, 'RECENT_WINDOW', 100)
    whole = Recording(cursor)
    assert [item['messageId'] for item in search_messages(whole, 'op', 'редкое')[0]] == ['old']
    assert not any('enable_indexscan' in sql for sql in whole.statements)


@needs_pg
def test_a_hit_without_its_chat_row_is_skipped_but_paging_goes_on(pg):
    _, _, cursor = pg
    chat(cursor, '77000000001')
    message(cursor, '77000000001', 1, 'паспорт раз', message_id='a')
    message(cursor, '77000000009', 2, 'паспорт без чата', message_id='b')     # сводки нет
    message(cursor, '77000000001', 3, 'паспорт два', message_id='c')
    items, after = search_messages(cursor, 'op', 'паспорт', limit=2)
    assert [item['messageId'] for item in items] == ['c'] and after
    items, after = search_messages(cursor, 'op', 'паспорт', limit=2, before=decode_cursor(after))
    assert [item['messageId'] for item in items] == ['a'] and after is None


@needs_pg
def test_focus_window_runs_from_the_hit_to_the_end_with_some_context(pg, monkeypatch):
    _, _, cursor = pg
    ids = [message(cursor, '77000000001', index, f'сообщение {index}', message_id=f'm{index:02d}')
           for index in range(40)]
    message(cursor, '77000000002', 15, 'чужой чат', message_id='other')
    window, has_more = focus_window(cursor, MESSAGE_SELECT, 'op', 'ch-a', '77000000001', 'm25')
    got = [row[0] for row in window]
    # Новые вперёд, как обычная страница: от последнего до найденного и 20 ранних.
    assert got == list(reversed(ids[5:])) and has_more is True
    window, has_more = focus_window(cursor, MESSAGE_SELECT, 'op', 'ch-a', '77000000001', 'm10')
    assert [row[0] for row in window] == list(reversed(ids)) and has_more is False
    # Чужое сообщение, чужой аккаунт и неизвестный id — обычная страница.
    assert focus_window(cursor, MESSAGE_SELECT, 'op', 'ch-a', '77000000001', 'other') is None
    assert focus_window(cursor, MESSAGE_SELECT, 'potok', 'ch-a', '77000000001', 'm25') is None
    assert focus_window(cursor, MESSAGE_SELECT, 'op', 'ch-a', '77000000001', 'nope') is None
    monkeypatch.setattr(chat_search, 'FOCUS_MAX_NEWER', 10)
    assert focus_window(cursor, MESSAGE_SELECT, 'op', 'ch-a', '77000000001', 'm25') is None
    assert len(focus_window(cursor, MESSAGE_SELECT, 'op', 'ch-a', '77000000001', 'm31')[0]) == 9 + 20


def route_app(name, path, cursor, **extra):
    """Настоящая ручка из bot_schedule2.py без импорта сервера и его пула."""
    source = source_cache.parse((Path(__file__).resolve().parents[1] / 'bot_schedule2.py')
                                .read_text(encoding='utf-8-sig'))
    route = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == name)
    route.decorator_list = []

    class Db:
        @contextmanager
        def _get_cursor(self):
            cursor.execute('SAVEPOINT route')
            try:
                yield cursor
                cursor.execute('RELEASE SAVEPOINT route')
            except Exception:
                cursor.execute('ROLLBACK TO SAVEPOINT route')
                raise

    import psycopg2
    namespace = dict(db=Db(), jsonify=jsonify, request=request, logging=logging, time=time,
                     psycopg2=psycopg2, wazzup_chat_search=chat_search,
                     _record_elapsed_server_timing=lambda *_: None,
                     _build_cors_preflight_response=lambda: ('', 204),
                     _wazzup_chat_reader_guard=lambda: (1, None),
                     _wazzup_account_arg=lambda: request.args.get('account', 'op'),
                     _wazzup_channel_scope=lambda: None, **extra)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[route], type_ignores=[])), 'bot_schedule2.py', 'exec'),
         namespace)
    app = Flask(__name__)
    app.add_url_rule(path, view_func=namespace[name])
    return app.test_client()


@needs_pg
def test_search_route_pages_and_refuses_damaged_cursors(pg):
    _, _, cursor = pg
    chat(cursor, '77000000001')
    for index in range(3):
        message(cursor, '77000000001', index, f'техпаспорт {index}', message_id=f't{index}')
    client = route_app('api_wazzup_chat_search', '/api/wazzup/chat-search', cursor)
    first = client.get('/api/wazzup/chat-search', query_string={'q': 'ТЕХПАСПОРТ', 'limit': 2}).get_json()
    assert [item['messageId'] for item in first['items']] == ['t2', 't1'] and first['next']
    second = client.get('/api/wazzup/chat-search', query_string={
        'q': 'техпаспорт', 'limit': 2, 'before': first['next']}).get_json()
    assert [item['messageId'] for item in second['items']] == ['t0'] and second['next'] is None
    assert client.get('/api/wazzup/chat-search', query_string={'q': 'тх'}).get_json() == {
        'status': 'success', 'items': [], 'next': None}
    assert client.get('/api/wazzup/chat-search', query_string={'q': 'техпаспорт', 'before': 'x|y'}).status_code == 400


@needs_pg
def test_search_route_answers_503_when_the_query_runs_out_of_time(pg, monkeypatch):
    _, _, cursor = pg
    chat(cursor, '77000000001')
    message(cursor, '77000000001', 1, 'техпаспорт')
    monkeypatch.setattr(chat_search, 'STATEMENT_TIMEOUT', '20ms')
    real = chat_search.recent_hits_query
    monkeypatch.setattr(chat_search, 'recent_hits_query', lambda *args, **kwargs: (
        real(*args, **kwargs)[0].replace('FROM wazzup_messages m', 'FROM wazzup_messages m, pg_sleep(0.3)'),
        real(*args, **kwargs)[1]))
    response = route_app('api_wazzup_chat_search', '/api/wazzup/chat-search', cursor).get(
        '/api/wazzup/chat-search', query_string={'q': 'техпаспорт'})
    assert response.status_code == 503
    assert 'уточните запрос' in response.get_json()['error']


@needs_pg
def test_history_route_opens_on_the_focused_message(pg):
    _, _, cursor = pg
    for index in range(80):
        message(cursor, '77000000001', index, f'сообщение {index}', message_id=f'm{index:02d}')
    client = route_app('api_wazzup_chat_messages', '/api/wazzup/chat-messages', cursor)
    query = {'channel_id': 'ch-a', 'chat_id': '77000000001', 'limit': 10}
    focused = client.get('/api/wazzup/chat-messages', query_string={**query, 'focus': 'm30'}).get_json()
    assert [item['messageId'] for item in focused['items']] == [f'm{index:02d}' for index in range(10, 80)]
    assert focused['hasMore'] is True and focused['lastInboundAt']
    # Неизвестное сообщение — обычная последняя страница; «Более ранние» фокус не трогает.
    plain = client.get('/api/wazzup/chat-messages', query_string={**query, 'focus': 'nope'}).get_json()
    assert [item['messageId'] for item in plain['items']] == [f'm{index:02d}' for index in range(70, 80)]
    older = client.get('/api/wazzup/chat-messages', query_string={
        **query, 'focus': 'm30', 'before': (T0 + dt.timedelta(seconds=10)).isoformat()}).get_json()
    assert [item['messageId'] for item in older['items']] == [f'm{index:02d}' for index in range(0, 10)]


def index_connect(schema):
    import psycopg2

    def connect():
        return psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                                connect_timeout=3, options=f'-c search_path={schema}')
    return connect


def index_states(cursor):
    cursor.execute("""SELECT c.relname, i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
        WHERE c.relname IN ('idx_wazzup_messages_text_trgm', 'idx_wazzup_chats_search_trgm')
          AND c.relnamespace = current_schema()::regnamespace ORDER BY 1""")
    return cursor.fetchall()


@needs_pg
def test_indexes_are_built_once_and_a_broken_build_is_redone(pg):
    schema, connection, cursor = pg
    chat(cursor, '77000000001')
    message(cursor, '77000000001', 1, 'водительское удостоверение')
    connection.commit()
    assert chat_search.build_search_indexes(index_connect(schema)) is True
    assert index_states(cursor) == [('idx_wazzup_chats_search_trgm', True), ('idx_wazzup_messages_text_trgm', True)]
    cursor.execute("SELECT indexrelid FROM pg_index WHERE indexrelid = to_regclass('idx_wazzup_messages_text_trgm')")
    built = cursor.fetchone()[0]
    assert chat_search.build_search_indexes(index_connect(schema)) is True
    cursor.execute("SELECT indexrelid FROM pg_index WHERE indexrelid = to_regclass('idx_wazzup_messages_text_trgm')")
    assert cursor.fetchone()[0] == built                     # готовый не перестраивается
    # Прерванный CONCURRENTLY оставляет невалидный индекс — его сносят и строят заново.
    cursor.execute("UPDATE pg_index SET indisvalid = false WHERE indexrelid = %s", (built,))
    connection.commit()
    assert chat_search.build_search_indexes(index_connect(schema)) is True
    assert index_states(cursor) == [('idx_wazzup_chats_search_trgm', True), ('idx_wazzup_messages_text_trgm', True)]
    cursor.execute("SELECT indexrelid FROM pg_index WHERE indexrelid = to_regclass('idx_wazzup_messages_text_trgm')")
    assert cursor.fetchone()[0] != built


@needs_pg
def test_a_second_process_leaves_the_build_to_the_lock_holder(pg):
    schema, connection, cursor = pg
    holder = index_connect(schema)()
    holder.autocommit = True
    with holder.cursor() as other:
        other.execute('SELECT pg_advisory_lock(%s)', (chat_search.INDEX_LOCK_KEY,))
    try:
        assert chat_search.build_search_indexes(index_connect(schema)) is True
        assert index_states(cursor) == []
    finally:
        holder.close()


def test_failed_builds_are_retried_after_a_pause_and_then_given_up():
    calls, pauses = [], []

    def broken():
        calls.append(1)
        raise RuntimeError('connection refused')

    assert chat_search.ensure_search_indexes(broken, sleep=pauses.append) is False
    assert len(calls) == 1 + len(chat_search.INDEX_RETRY_DELAYS)
    assert pauses == list(chat_search.INDEX_RETRY_DELAYS)
    outcomes = iter([RuntimeError('deadlock'), True])

    def flaky():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome

        class Connection:
            autocommit = False

            def cursor(self):
                return Cursor()

            def close(self):
                pass

        class Cursor:
            def execute(self, *_):
                pass

            def fetchone(self):
                return (False,)                               # замок у другого — готово

        return Connection()

    assert chat_search.ensure_search_indexes(flaky, sleep=lambda _: None) is True
