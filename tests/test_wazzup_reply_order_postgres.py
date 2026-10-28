"""Ответ из окна iCORE и входящее канала с минутным временем — на настоящем Postgres.

Нужен одноразовый локальный кластер: WAZZUP_PILOT_TEST_PORT (как у
test_wazzup_pilot_persistence). Каждый тест работает в своей схеме и удаляет
только её. Триггеры «ждут ответа» (wazzup/unread.py) и уведомлений — боевые, из
wazzup.pilot_schema.init_schema.

Что здесь видно только на базе: до правки счётчик «ждут ответа» считал ответ
клиента, пришедший после вопроса оператора в ту же минуту, отвеченным — время
ленты у ответа клиента было раньше вопроса. Приёмник и починка истории это
исправляют, а холостой прогон починки проходит в транзакции только для чтения.
"""
import ast
import os
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest

from tests import source_cache
from tests.test_wazzup_reply_order import CHAT_1, CHAT_1_WAZZUP
from wazzup.pilot_schema import init_schema

PORT = os.environ.get('WAZZUP_PILOT_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')
ROOT = Path(__file__).resolve().parents[1]
CHANNEL, CHAT = '0052314a-0000-4000-8000-000000000000', '77000000000'


def _database_class():
    source = source_cache.parse((ROOT / 'database.py').read_text(encoding='utf-8-sig'))
    cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'Database')
    wanted = {'store_wazzup_messages', '_wazzup_chat_history_tx', '_wazzup_recent_arrivals_tx',
              '_wazzup_chat_later_tx', '_refresh_wazzup_chat_tx', 'update_wazzup_statuses',
              '_wazzup_episode_kept_sql', 'retime_wazzup_inbound_after_replies',
              '_retime_wazzup_chat_after_replies_tx'}
    attrs = {'WAZZUP_EPISODE_REFERENCES', 'WAZZUP_EPISODE_LOCK_KEY', 'WAZZUP_RETIME_CHATS_PER_READ'}
    body = [n for n in cls.body
            if (isinstance(n, ast.FunctionDef) and n.name in wanted)
            or (isinstance(n, ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Name) and n.targets[0].id in attrs)]
    assert {getattr(n, 'name', None) or n.targets[0].id for n in body} == wanted | attrs
    module = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name='Database', bases=[], keywords=[], body=body, decorator_list=[])], type_ignores=[]))
    namespace = {'logging': __import__('logging')}
    exec(compile(module, str(ROOT / 'database.py'), 'exec'), namespace)
    return namespace['Database']


@pytest.fixture
def pg():
    import psycopg2
    schema = 't_wazzup_reply_order_' + uuid.uuid4().hex
    connections = []

    def connect():
        connection = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres',
                                      dbname='postgres', connect_timeout=3)
        connections.append(connection)
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO ' + schema)
            cursor.execute("SET statement_timeout TO '10s'")
            cursor.execute("SET TIME ZONE 'UTC'")
        connection.commit()
        return connection

    connection = connect()
    cursor = connection.cursor()
    cursor.execute('CREATE SCHEMA ' + schema)
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
        CREATE INDEX ON wazzup_messages(channel_id, chat_id, dt);
        CREATE TABLE wazzup_chats (
            channel_id TEXT NOT NULL, chat_id TEXT NOT NULL, chat_type TEXT,
            contact_name TEXT, contact_phone TEXT, last_message_at TIMESTAMPTZ,
            last_message_text TEXT, last_message_is_echo BOOLEAN,
            messages_count INTEGER DEFAULT 0, inbound_count INTEGER DEFAULT 0,
            outbound_count INTEGER DEFAULT 0, updated_at TIMESTAMPTZ DEFAULT now(),
            account TEXT NOT NULL DEFAULT 'op', PRIMARY KEY(channel_id, chat_id)
        );
        CREATE TABLE wazzup_episodes (
            id SERIAL PRIMARY KEY, channel_id TEXT NOT NULL, chat_id TEXT NOT NULL,
            kind TEXT, started_at TIMESTAMPTZ NOT NULL, ended_at TIMESTAMPTZ NOT NULL,
            journal_evaluated_at TIMESTAMPTZ, UNIQUE(channel_id, chat_id, started_at)
        );
        CREATE TABLE c2d_chat_snapshots (source TEXT, wz_channel_id TEXT, wz_chat_id TEXT,
                                         episode_start TIMESTAMPTZ);
        CREATE TABLE ai_evaluation_runs (subject_kind TEXT, call_id BIGINT);
    ''')
    init_schema(cursor)
    connection.commit()

    def database(conn=connection):
        db = _database_class()()

        @contextmanager
        def get_cursor():
            with conn.cursor() as cur:
                try:
                    yield cur
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        db._get_cursor = get_cursor
        return db

    try:
        yield connection, cursor, database, connect
    finally:
        for other in connections[1:]:
            other.close()
        connection.rollback()
        cursor.execute('DROP SCHEMA ' + schema + ' CASCADE')
        connection.commit()
        connection.close()


def _almaty(parts):
    from datetime import datetime, timezone
    return datetime(*parts, tzinfo=timezone(timedelta(hours=5))).astimezone(timezone.utc)


def _iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%S.') + f'{dt.microsecond // 1000:03d}Z'


def _outbox(cursor, message_id):
    cursor.execute("""INSERT INTO wazzup_pilot_outbox
        (request_id,account,channel_id,chat_id,chat_type,text,user_id,state,message_id,author_id)
        VALUES (%s,'op',%s,%s,'whatsapp','ответ',42,'sent',%s,'icore:42')""",
                   (str(uuid.uuid4()), CHANNEL, CHAT, message_id))


def _order(cursor):
    cursor.execute('SELECT message_id FROM wazzup_messages WHERE chat_id=%s ORDER BY dt, message_id', (CHAT,))
    return [r[0] for r in cursor.fetchall()]


def _waiting(cursor):
    cursor.execute("""SELECT s.unread_count, s.last_inbound_id,
                             ARRAY(SELECT message_id FROM wazzup_unanswered_messages u
                                    WHERE u.account=s.account AND u.channel_id=s.channel_id
                                      AND u.chat_id=s.chat_id ORDER BY sent_at)
                        FROM wazzup_chat_read_state s WHERE s.chat_id=%s""", (CHAT,))
    return cursor.fetchone()


def test_receiver_orders_like_wazzup_and_the_answer_waits_for_a_reply(pg):
    connection, cursor, database, _ = pg
    db = database()
    waiting_after = {}
    for mid, echo, sent, received in sorted(CHAT_1, key=lambda r: r[3]):
        if echo:
            # Как send(): строка исхода, строка архива со временем отправки, потом эхо «sent».
            _outbox(cursor, mid)
            connection.commit()
            db.store_wazzup_messages([dict(messageId=mid, channelId=CHANNEL, chatId=CHAT,
                                           chatType='whatsapp', dateTime=_almaty(sent).isoformat(),
                                           isEcho=True, type='text', text=mid, status='pending',
                                           authorId='icore:42', authorName='Оператор')])
            db.update_wazzup_statuses([dict(messageId=mid, status='sent')])
        else:
            db.store_wazzup_messages([dict(messageId=mid, channelId=CHANNEL, chatId=CHAT,
                                           chatType='whatsapp', dateTime=_iso(_almaty(sent)),
                                           isEcho=False, type='text', text=mid, status='inbound')],
                                     received_at=_almaty(received))
        waiting_after[mid] = _waiting(cursor)
    assert _order(cursor) == CHAT_1_WAZZUP
    # Ответ клиента после вопроса — ждёт ответа (до правки счётчик считал его отвеченным).
    assert waiting_after['only'] == (3, 'only', ['no', 'wolt', 'only'])
    assert waiting_after['wolt?'] == (1, 'wolt?', ['wolt?'])
    assert waiting_after['register?'][0] == 0 and waiting_after['register?'][2] == []
    assert waiting_after['sorry'][0] == 0 and waiting_after['sorry'][2] == []
    cursor.execute('SELECT last_message_text, last_message_is_echo FROM wazzup_chats WHERE chat_id=%s', (CHAT,))
    assert cursor.fetchone() == ('sorry', True)


def _old_rows(cursor, rows, statuses=None):
    """Строки так, как их сохранил приёмник ДО правки: время Wazzup, приход — created_at.
    Вставка в порядке прихода — триггер «ждут ответа» считает так же, как на проде."""
    statuses = statuses or {}
    for mid, echo, sent, received in sorted(rows, key=lambda r: r[3]):
        if echo:
            _outbox(cursor, mid)
        cursor.execute("""INSERT INTO wazzup_messages
            (message_id,channel_id,chat_type,chat_id,dt,is_echo,type,text,status,author_id,author_name,
             account,created_at)
            VALUES (%s,%s,'whatsapp',%s,%s,%s,'text',%s,%s,%s,%s,'op',%s)""",
                       (mid, CHANNEL, CHAT, _almaty(sent), echo, mid,
                        statuses.get(mid, 'read' if echo else 'inbound'),
                        'icore:42' if echo else None, 'Оператор' if echo else None, _almaty(received)))


def test_repair_dry_run_is_read_only_then_apply_fixes_order_episodes_and_waiting(pg):
    connection, cursor, database, connect = pg
    rows = [r for r in CHAT_1 if r[0] != 'sorry']      # последнему ответу клиента так и не ответили
    _old_rows(cursor, rows)
    start, register = _almaty((2026, 10, 19, 11, 21, 0, 1000)), _almaty((2026, 10, 19, 11, 23, 29, 537000))
    cursor.execute("""INSERT INTO wazzup_episodes (channel_id, chat_id, kind, started_at, ended_at)
                      VALUES (%s,%s,'dialog',%s,%s), (%s,%s,'unanswered',%s,%s) RETURNING id""",
                   (CHANNEL, CHAT, start, _almaty((2026, 10, 19, 11, 23, 0, 4000)) + timedelta(seconds=30),
                    CHANNEL, CHAT, _almaty((2026, 10, 19, 20, 0)), _almaty((2026, 10, 19, 20, 5))))
    free_ids = [r[0] for r in cursor.fetchall()]
    connection.commit()
    before = _order(cursor)
    assert before.index('no') < before.index('register?'), 'до починки ответ стоит над вопросом'
    assert _waiting(cursor) == (0, 'courier', []), 'и счётчик «ждут ответа» молчит о трёх ответах'

    readonly = connect()
    with readonly.cursor() as ro:
        ro.execute('SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY')
    readonly.commit()
    dry = database(readonly).retime_wazzup_inbound_after_replies(
        account='op', apply=False, waiting_since=_almaty((2026, 10, 19)))
    assert (dry['messages'], dry['chats'], dry['waiting_messages']) == (4, 1, 3)
    assert dry['episodes_deleted'] == {'dialog': 1, 'unanswered': 1}
    assert _order(cursor) == before, 'холостой прогон ничего не поменял'

    result = database().retime_wazzup_inbound_after_replies(
        account='op', apply=True, waiting_since=_almaty((2026, 10, 19)))
    assert {k: result[k] for k in ('messages', 'chats', 'episodes_deleted', 'waiting_chats', 'waiting_messages')} == \
        {k: dry[k] for k in ('messages', 'chats', 'episodes_deleted', 'waiting_chats', 'waiting_messages')}
    assert _order(cursor) == [m for m in CHAT_1_WAZZUP if m != 'sorry']
    cursor.execute("SELECT message_id, dt FROM wazzup_messages WHERE message_id IN ('no','wolt','only') ORDER BY dt")
    assert [dt for _, dt in cursor.fetchall()] == [register + timedelta(microseconds=n) for n in (1, 2, 3)]
    cursor.execute('SELECT count(*) FROM wazzup_episodes WHERE id = ANY(%s)', (free_ids,))
    assert cursor.fetchone()[0] == 0, 'неоценённые эпизоды удалены под пересборку'
    cursor.execute('SELECT last_message_text, last_message_is_echo FROM wazzup_chats WHERE chat_id=%s', (CHAT,))
    assert cursor.fetchone() == ('only', False), 'сводка чата: последнее теперь — ответ клиента'
    assert _waiting(cursor) == (3, 'only', ['no', 'wolt', 'only'])

    again = database().retime_wazzup_inbound_after_replies(
        account='op', apply=True, waiting_since=_almaty((2026, 10, 19)))
    assert (again['messages'], again['waiting_messages']) == (0, 0), 'повторный запуск пуст'
    assert _waiting(cursor) == (3, 'only', ['no', 'wolt', 'only'])

    # Ответ оператора снимает вернувшееся ожидание обычным триггером.
    _old_rows(cursor, [('sorry', True, (2026, 10, 19, 11, 24, 23, 15000), (2026, 10, 19, 11, 24, 23, 16000))])
    connection.commit()
    assert _waiting(cursor)[0] == 0 and _waiting(cursor)[2] == []


def test_repair_never_deletes_or_splits_an_evaluated_episode(pg):
    connection, cursor, database, _ = pg
    rows = [r for r in CHAT_1 if r[0] != 'sorry']
    _old_rows(cursor, rows)
    start, register = _almaty((2026, 10, 19, 11, 21, 0, 1000)), _almaty((2026, 10, 19, 11, 23, 29, 537000))
    # Эпизод собран по старому порядку и кончается вопросом оператора; его оценили.
    cursor.execute("""INSERT INTO wazzup_episodes (channel_id, chat_id, kind, started_at, ended_at)
                      VALUES (%s,%s,'dialog',%s,%s) RETURNING id""", (CHANNEL, CHAT, start, register))
    evaluated = cursor.fetchone()[0]
    cursor.execute("INSERT INTO ai_evaluation_runs VALUES ('wz_episode', %s)", (evaluated,))
    connection.commit()
    result = database().retime_wazzup_inbound_after_replies(account='op', apply=True)
    assert result['kept_in_place'] == 3 and result['messages'] == 1
    assert result['episodes_kept'] == [evaluated]
    cursor.execute('SELECT count(*) FROM wazzup_episodes WHERE id=%s', (evaluated,))
    assert cursor.fetchone()[0] == 1
    cursor.execute("SELECT count(*) FROM wazzup_messages WHERE chat_id=%s AND dt > %s", (CHAT, register))
    assert cursor.fetchone()[0] == 0, 'ничего не ушло за конец оценённого эпизода'


def test_phone_reply_after_the_moved_answer_clears_waiting(pg):
    connection, cursor, database, _ = pg
    db = database()
    for mid, echo, sent, received in sorted([r for r in CHAT_1 if r[0] != 'sorry'], key=lambda r: r[3]):
        if echo:
            _outbox(cursor, mid)
            connection.commit()
            db.store_wazzup_messages([dict(messageId=mid, channelId=CHANNEL, chatId=CHAT, chatType='whatsapp',
                                           dateTime=_almaty(sent).isoformat(), isEcho=True, type='text',
                                           text=mid, status='pending', authorId='icore:42',
                                           authorName='Оператор')])
            db.update_wazzup_statuses([dict(messageId=mid, status='sent')])
        else:
            db.store_wazzup_messages([dict(messageId=mid, channelId=CHANNEL, chatId=CHAT, chatType='whatsapp',
                                           dateTime=_iso(_almaty(sent)), isEcho=False, type='text', text=mid,
                                           status='inbound')], received_at=_almaty(received))
    assert _waiting(cursor)[2] == ['no', 'wolt', 'only']
    # Оператор ответил с телефона: эхо с минутным временем (номер после «only»).
    db.store_wazzup_messages([dict(messageId='phone-ok', channelId=CHANNEL, chatId=CHAT, chatType='whatsapp',
                                   dateTime='2026-10-19T06:23:00.005Z', isEcho=True, type='text',
                                   text='ok', status='sent', authorId='7', authorName='Оператор')],
                             received_at=_almaty((2026, 10, 19, 11, 23, 58)))
    assert _order(cursor)[-4:] == ['no', 'wolt', 'only', 'phone-ok']
    assert _waiting(cursor)[0] == 0 and _waiting(cursor)[2] == [], 'ответ с телефона снял ожидание'


def test_repair_does_not_reopen_a_chat_closed_with_no_reply_needed(pg):
    connection, cursor, database, _ = pg
    # Вопрос из окна iCORE не дошёл (error) — ответом он не считается, и ответы
    # клиента после приветствия счётчик учёл; оператор нажал «Ответ не нужен».
    _old_rows(cursor, [r for r in CHAT_1 if r[0] != 'sorry'], statuses={'register?': 'error'})
    connection.commit()
    assert _waiting(cursor)[0] == 4, "courier, no, wolt, only"
    cursor.execute('SELECT wazzup_dismiss_unanswered(%s, %s, %s)', (CHANNEL, CHAT, 'only'))
    connection.commit()
    assert _waiting(cursor)[0] == 0
    result = database().retime_wazzup_inbound_after_replies(
        account='op', apply=True, waiting_since=_almaty((2026, 10, 19)))
    assert result['messages'] == 4, 'порядок чинится'
    assert result['waiting_chats'] == [] and _waiting(cursor)[0] == 0, '«Ответ не нужен» остаётся в силе'
