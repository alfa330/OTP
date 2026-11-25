# -*- coding: utf-8 -*-
"""«Чаты водителей» на Syntony — SQL раздела на настоящем Postgres (driver_chats/syntony.py):
водитель по хвосту номера на номерах СЗоВ, переписка окна, отметки «Передан» из журнала,
вложения и свежесть данных. Таблицы — схемы syntony_chats и driver_chats, как на проде.

SYNTONY_CHATS_TEST_PORT — порт локального кластера (пароль — PGPASSWORD). Номера выдуманы.

    SYNTONY_CHATS_TEST_PORT=55433 PGPASSWORD=… python -m pytest tests/test_driver_chats_syntony_postgres.py
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from driver_chats import queries, schema as dch_schema, syntony  # noqa: E402
from syntony_chats import rules, store, sync  # noqa: E402
from test_szov_chat_syntony_postgres import (  # noqa: E402,F401
    PORT, SZOV_CHANNEL, api_conversation, api_message, db, utc)

pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (SYNTONY_CHATS_TEST_PORT)')

SZOV_2 = '39114c57-557e-4549-9af2-7d169524272e'
FOREIGN = 'cd824063-38ca-481f-8c54-99b4455d8840'
CHANNELS = syntony.channel_ids({})
DRIVER = '77011234567'
TURK = '905317298361'
NOW = utc('2026-11-25T06:00:00Z')            # 11:00 по Алматы
WINDOW = (date(2026, 11, 24), date(2026, 11, 25))


def _section_tables(db):
    with db._get_cursor() as cur:
        cur.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        cur.execute("CREATE TABLE c2d_requests (request_id BIGINT PRIMARY KEY, client_phone TEXT, "
                    "assigned_phone TEXT)")
        cur.execute("INSERT INTO users VALUES (7, 'Оператор СЗоВ')")
        dch_schema.init_driver_chats_schema(cur)


def _conversation(db, cid, channel, phone, name='Водитель', messages=()):
    with db._get_cursor() as cur:
        conversation = api_conversation(cid, '2026-11-24T05:00:00Z', channel, phone=phone)
        conversation['contact']['name'] = name
        store.upsert_conversation(cur, rules.conversation_row(conversation, NOW))
        for mid, moment, kind, extra in messages:
            direction = 'inbound' if kind == 'driver' else 'outbound'
            sender = {'driver': 'contact', 'human': 'human', 'ai': 'ai'}[kind]
            message = api_message(mid, cid, moment, direction=direction, sender=sender,
                                  author='Бекова Динара' if kind == 'human' else None)
            message.update(extra or {})
            store.upsert_message(cur, rules.message_row(message, NOW, rules.VIA_API))


PHOTO = {'contentType': 'image', 'text': '',
         'media': {'mimeType': 'image/jpeg', 'fileName': 'a.jpg', 'url': '/api/chats-api/v1/files/p'}}


def _seed(db):
    _section_tables(db)
    _conversation(db, 'c1', SZOV_CHANNEL, DRIVER, 'Асхат', [
        ('old', '2026-11-22T05:00:00Z', 'driver', None),          # до окна (вчера и сегодня)
        ('m1', '2026-11-24T05:00:00Z', 'driver', None),
        ('m2', '2026-11-24T05:03:00Z', 'human', None),
        ('ph', '2026-11-25T04:00:00Z', 'driver', PHOTO),
        ('gone', '2026-11-25T04:01:00Z', 'driver', dict(PHOTO, deleteScope='contact')),
    ])
    # Тот же водитель, другой парк СЗоВ.
    _conversation(db, 'c2', SZOV_2, DRIVER, None, [
        ('n1', '2026-11-25T05:00:00Z', 'driver', None),
    ])
    # Тот же водитель на чужом номере сети — в разделе его нет.
    _conversation(db, 'c3', FOREIGN, DRIVER, 'Чужой', [
        ('f1', '2026-11-25T05:30:00Z', 'driver', PHOTO),
    ])
    _conversation(db, 'c4', SZOV_CHANNEL, TURK, None, [
        ('t1', '2026-11-25T05:10:00Z', 'driver', None),
    ])


def test_driver_is_found_by_the_tail_on_szov_numbers_only(db):
    _seed(db)
    with db._get_cursor() as cur:
        assert syntony.find_contact(cur, '87011234567', '011234567', CHANNELS) == (DRIVER, 'Асхат')
        # Номер без кода страны — по девяти последним цифрам.
        assert syntony.find_contact(cur, None, '317298361', CHANNELS) == (TURK, None)
        assert syntony.find_contact(cur, None, '999999999', CHANNELS) is None
        # Только на чужом номере — для раздела водителя нет.
        assert syntony.find_contact(cur, '77011234567', '011234567', [FOREIGN]) == (DRIVER, 'Чужой')
        assert syntony.find_contact(cur, '77011234567', '011234567', [SZOV_2]) == (DRIVER, None)


def test_two_drivers_behind_one_tail_are_ambiguous(db):
    _seed(db)
    _conversation(db, 'c5', SZOV_CHANNEL, '995317298361', None, [
        ('t2', '2026-11-25T05:20:00Z', 'driver', None)])
    with db._get_cursor() as cur:
        assert syntony.find_contact(cur, None, '317298361', CHANNELS) == ('ambiguous', None)
        # Номер целиком выбирает своего.
        assert syntony.find_contact(cur, TURK, '317298361', CHANNELS) == (TURK, None)


def test_window_is_yesterday_and_today_on_szov_numbers(db):
    _seed(db)
    start, end = syntony.window_range(*WINDOW)
    with db._get_cursor() as cur:
        rows, truncated = syntony.window_rows(cur, DRIVER, CHANNELS, start, end)
        assert [r[0] for r in rows] == ['m1', 'm2', 'ph', 'gone', 'n1']
        assert truncated is False
        newest, truncated = syntony.window_rows(cur, DRIVER, CHANNELS, start, end, cap=2)
        assert ([r[0] for r in newest], truncated) == (['gone', 'n1'], True)
    names = syntony.channel_names(rows)
    assert names == {SZOV_CHANNEL: 'Ноль такси', SZOV_2: 'Ноль такси 2'}


def test_handoff_marks_come_back_from_the_journal_into_their_own_chat(db):
    _seed(db)
    ctx = {'user_id': 7, 'name': 'Оператор СЗоВ', 'role': 'operator', 'department_id': 1}
    with db._get_cursor() as cur:
        event = queries.log_event(cur, ctx, 'handoff', phone=DRIVER, comment_text='Передан: фото',
                                  source='syntony', syntony_channel_id=SZOV_CHANNEL,
                                  syntony_conversation_id='c1')
        # Строка эпохи Chat2Desk того же номера — не отметка Syntony.
        queries.log_event(cur, ctx, 'handoff', phone=DRIVER, comment_text='старая', client_id=5,
                          channel_id=10908)
        assert syntony.contact_has_channel(cur, DRIVER, SZOV_CHANNEL) is True
        assert syntony.contact_has_channel(cur, DRIVER, SZOV_2) is True
        assert syntony.contact_has_channel(cur, TURK, SZOV_2) is False
    today = datetime.now().date()
    with db._get_cursor() as cur:
        notes = syntony.handoff_messages(cur, DRIVER, today - timedelta(days=1), today)
    assert [(n['id'], n['type'], n['channelId'], n['dialogId'], n['text']) for n in notes] == [
        ('dch-%s' % event['id'], 'comment', SZOV_CHANNEL, 'c1', 'Передан: фото')]


def test_media_is_served_only_for_szov_messages_that_still_have_it(db):
    _seed(db)
    with db._get_cursor() as cur:
        assert syntony.media_allowed(cur, 'ph', CHANNELS) is True
        assert syntony.media_allowed(cur, 'gone', CHANNELS) is False, 'водитель удалил — файла нет'
        assert syntony.media_allowed(cur, 'f1', CHANNELS) is False, 'чужой номер сети'
        assert syntony.media_allowed(cur, 'm1', CHANNELS) is False, 'вложения нет'
        assert syntony.media_allowed(cur, 'нет-такого', CHANNELS) is False


def test_stale_data_is_reported_only_when_both_the_puller_and_the_webhook_are_silent(db):
    _seed(db)
    with mock.patch.object(syntony.szov, '_puller_on', return_value=True):
        with db._get_cursor() as cur:
            # Отметок ещё нет — судить не по чему.
            assert syntony.stale_minutes(cur) is None
            store.set_mark(cur, sync.MARK_FEED, NOW)
            store.set_mark(cur, sync.MARK_CONVERSATIONS, NOW)
        with db._get_cursor() as cur:
            assert syntony.stale_minutes(cur) is None, 'сторож только что прошёл'
            cur.execute("UPDATE syntony_chat_sync_marks SET updated_at = now() - interval '25 minutes'")
        with db._get_cursor() as cur:
            assert syntony.stale_minutes(cur) == 25
        _conversation(db, 'c9', SZOV_CHANNEL, '77010000009', None, [
            ('live', datetime.now().astimezone().isoformat(), 'driver', None)])
        with db._get_cursor() as cur:
            assert syntony.stale_minutes(cur) is None, 'вебхук жив — сообщения идут'
    with mock.patch.object(syntony.szov, '_puller_on', return_value=False):
        with db._get_cursor() as cur:
            assert syntony.stale_minutes(cur) is None, 'без сторожа API — как у табло, не судим'


def test_section_schema_lands_on_a_base_with_syntony_tables(db):
    _section_tables(db)
    with db._get_cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'dch_events' AND column_name IN "
                    "('source', 'syntony_channel_id', 'syntony_conversation_id')")
        assert {row[0] for row in cur.fetchall()} == {
            'source', 'syntony_channel_id', 'syntony_conversation_id'}
        cur.execute("SELECT indexname FROM pg_indexes WHERE indexname IN "
                    "('idx_syntony_chat_conversations_phone_tail', "
                    "'idx_syntony_chat_messages_conversation')")
        assert len(cur.fetchall()) == 2
