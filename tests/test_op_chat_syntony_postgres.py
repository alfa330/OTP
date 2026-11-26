# -*- coding: utf-8 -*-
"""Табло верификаторов ОП по Syntony (verifiers.py) на настоящем Postgres: запрос окна (ключ чата,
передача человеку, закрытие, тестовые номера, границы суток Алматы), ответы сети для онлайна и
снимок «Чата» целиком через ручку — с таблицами, которые пишут вебхук и сторож API Syntony.

SYNTONY_CHATS_TEST_PORT — порт локального кластера (пароль — PGPASSWORD). Своя схема со
случайным именем, в конце удаляется. Люди и номера выдуманы.

    SYNTONY_CHATS_TEST_PORT=55433 PGPASSWORD=… python -m pytest tests/test_op_chat_syntony_postgres.py
"""

import contextlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flask import Flask  # noqa: E402

from op_wallboard import routes as op_routes  # noqa: E402
from syntony_chats import schema, szov, verifiers  # noqa: E402

PORT = os.environ.get('SYNTONY_CHATS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (SYNTONY_CHATS_TEST_PORT)')

ALMATY = ZoneInfo('Asia/Almaty')
VER = 'cd824063-38ca-481f-8c54-99b4455d8840'      # из verifiers.VERIFIER_CHANNEL_IDS
SZOV = 'b40df588-75cc-4562-be29-b1e73580fd49'     # номер СЗоВ — не верификаторов
TEST_PHONE = '77019999999'
NAMES = {501: 'Тлеуқабыл Әсем Маратқызы', 502: 'Бекет Нұрлан Серікұлы'}
AUTHORS = {'Тлеукабыл Асем': 501, 'Нұрлан Бекет': 502}


class _Db:
    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT),
                                user=os.environ.get('SYNTONY_CHATS_TEST_USER', 'postgres'),
                                dbname=os.environ.get('SYNTONY_CHATS_TEST_DB', 'postgres'),
                                connect_timeout=3)
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


@pytest.fixture()
def db():
    name = 'op_chat_syntony_' + uuid.uuid4().hex[:10]
    admin = _Db('public')
    with admin._get_cursor() as cur:
        cur.execute('CREATE SCHEMA ' + name)
    handle = _Db(name)
    with handle._get_cursor() as cur:
        schema.init_syntony_chats_schema(cur)
    try:
        yield handle
    finally:
        with admin._get_cursor() as cur:
            cur.execute('DROP SCHEMA %s CASCADE' % name)


def _conversation(cur, cid, channel, contact, created, *, phone='77070000001', handoff=None, closed=None):
    cur.execute("""
        INSERT INTO syntony_chat_conversations (id, channel_id, contact_id, contact_phone, created_at,
                                                handoff, closed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (cid, channel, contact, phone, created,
          json.dumps({'requestedAt': handoff.astimezone(timezone.utc).isoformat()}) if handoff else None,
          closed))


def _message(cur, mid, cid, direction, sender, created, author=None, deleted=None):
    cur.execute("""
        INSERT INTO syntony_chat_messages (id, conversation_id, direction, sender_type, author_name,
                                           delete_scope, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (mid, cid, direction, sender, author, deleted, created))


def _local(hh, mm=0, day=None):
    day = day or datetime(2026, 11, 25).date()
    return datetime.combine(day, datetime.min.time(), tzinfo=ALMATY) + timedelta(hours=hh, minutes=mm)


def _is_test(expr):
    return "(%s = '%s')" % (expr, TEST_PHONE)


def test_window_keys_handoff_closing_and_bounds(db):
    with db._get_cursor() as cur:
        # Один водитель — два диалога на номере (первый закрыли): один чат табло.
        _conversation(cur, 'cv1', VER, 'driver-1', _local(9), closed=_local(9, 30))
        _conversation(cur, 'cv2', VER, 'driver-1', _local(9, 40), handoff=_local(9, 41))
        _conversation(cur, 'cv3', VER, '', _local(10))                       # без собеседника
        _conversation(cur, 'cv4', SZOV, 'driver-2', _local(10))              # номер СЗоВ
        _conversation(cur, 'cv5', VER, 'driver-3', _local(10), phone=TEST_PHONE)
        _message(cur, 'm1', 'cv1', 'inbound', 'contact', _local(9))
        _message(cur, 'm2', 'cv1', 'outbound', 'human', _local(9, 2), 'Тлеукабыл Асем')
        _message(cur, 'm3', 'cv2', 'inbound', 'contact', _local(9, 40))
        _message(cur, 'm4', 'cv2', 'outbound', 'ai', _local(9, 41))
        _message(cur, 'm5', 'cv3', 'inbound', 'contact', _local(10))
        _message(cur, 'm6', 'cv4', 'inbound', 'contact', _local(10))
        _message(cur, 'm7', 'cv5', 'inbound', 'contact', _local(10))
        _message(cur, 'm8', 'cv1', 'inbound', 'contact', _local(-0.5))       # 23:30 накануне
    since = _local(0).replace(tzinfo=None)
    with db._get_cursor() as cur:
        window = verifiers.load_window(cur, since=since, until=_local(12).replace(tzinfo=None),
                                       channels=verifiers.VERIFIER_CHANNEL_IDS, is_test_sql=_is_test)
    by_id = {row[0]: row for row in window.messages}
    assert sorted(by_id) == ['m1', 'm2', 'm3', 'm4', 'm5']               # СЗоВ, тест и вчера — нет
    assert by_id['m1'][2] == by_id['m3'][2] == 'driver-1'                 # один чат на два диалога
    assert by_id['m5'][2] == 'cv3'                                        # без собеседника — диалог
    assert by_id['m1'][7] == datetime(2026, 11, 25, 9, 0)                 # наивное Алматы
    assert window.conversations['cv2'][2] == datetime(2026, 11, 25, 9, 41)
    assert window.conversations['cv1'][3] == datetime(2026, 11, 25, 9, 30)
    assert window.newest == datetime(2026, 11, 25, 10, 0)
    rows = verifiers.build_rows(window, AUTHORS.get, since=since, until=_local(12).replace(tzinfo=None))
    assert [row['message_id'] for row in rows if row.get('is_handoff')] == ['handoff-cv2']


def test_replies_are_the_whole_network(db):
    with db._get_cursor() as cur:
        _conversation(cur, 'cv1', SZOV, 'driver-2', _local(10))
        _message(cur, 'r1', 'cv1', 'outbound', 'human', _local(10, 5), 'Тлеукабыл Асем')
        _message(cur, 'r2', 'cv1', 'outbound', 'human', _local(10, 6), 'Тлеукабыл Асем', deleted='unsent')
        _message(cur, 'r3', 'cv1', 'outbound', 'ai', _local(10, 7))
        _message(cur, 'r4', 'cv1', 'outbound', 'human', _local(10, 8))
        _message(cur, 'r5', 'cv1', 'inbound', 'contact', _local(10, 9))
    with db._get_cursor() as cur:
        replies = verifiers.load_replies(cur, since=_local(0).replace(tzinfo=None),
                                         until=_local(12).replace(tzinfo=None))
    assert replies == [('Тлеукабыл Асем', datetime(2026, 11, 25, 10, 5))]


def _clock_at(moment):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.replace(tzinfo=tz)
    return Clock


def test_snapshot_through_the_route(db):
    """Ручка «Чата» на этих таблицах: правила, ИИ, передача и онлайн по ответам."""
    now = datetime(2026, 11, 25, 12, 0)                       # «сейчас» ручки — наивное Алматы
    ago = lambda minutes: now.replace(tzinfo=ALMATY) - timedelta(minutes=minutes)
    with db._get_cursor() as cur:
        _conversation(cur, 'a1', VER, 'd1', ago(20))
        _message(cur, 'a1-1', 'a1', 'inbound', 'contact', ago(20))
        _message(cur, 'a1-2', 'a1', 'outbound', 'ai', ago(20))
        _message(cur, 'a1-3', 'a1', 'inbound', 'contact', ago(12))
        _message(cur, 'a1-4', 'a1', 'outbound', 'human', ago(10), 'Тлеукабыл Асем')   # 120 с
        _conversation(cur, 'a2', VER, 'd2', ago(30))                                  # только ИИ
        _message(cur, 'a2-1', 'a2', 'inbound', 'contact', ago(30))
        _message(cur, 'a2-2', 'a2', 'outbound', 'ai', ago(30))
        _conversation(cur, 'a3', VER, 'd3', ago(8), handoff=ago(6))                  # ждёт человека
        _message(cur, 'a3-1', 'a3', 'inbound', 'contact', ago(8))
        _message(cur, 'a3-2', 'a3', 'outbound', 'ai', ago(6))
        _conversation(cur, 'b1', SZOV, 'd9', ago(50))                                 # ответ на чужом номере
        _message(cur, 'b1-1', 'b1', 'outbound', 'human', ago(50), 'Нұрлан Бекет')
    with mock.patch.object(op_routes, 'load_chat_verifiers', lambda cursor, day_from, day_to=None: dict(NAMES)), \
            mock.patch.object(op_routes, 'datetime', _clock_at(now)), \
            mock.patch.object(szov, '_puller_on', return_value=False), \
            mock.patch.object(szov, '_coverage_gap', return_value=None):
        bp = op_routes.build_op_wallboard_blueprint(
            db=db, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            guard=lambda: (1, None), department_id=lambda: 7,
            snapshot_with_cache=lambda *, fetch, **_k: dict(fetch(), stale=False, age_seconds=0),
            restore_cache=lambda *a, **k: None, persist_cache=lambda *a, **k: None,
            status_entry=lambda key: ('Нет событий', 'unknown', 90), load_people=lambda d, day: [],
            live_statuses=lambda ids: {},
            chat_syntony={'matcher': lambda roster: AUTHORS.get, 'covers': lambda day: True,
                          'is_test_sql': _is_test})
        app = Flask(__name__)
        app.register_blueprint(bp)
        body = app.test_client().get('/api/op_wallboard/chat_snapshot').get_json()
    assert body['source_label'] == 'Syntony', body
    assert body['today']['chats'] == 2                                  # d1 и d3; d2 — только ИИ
    assert body['today']['first_reply_seconds'] == 120
    assert (body['now']['chats_waiting'], body['now']['longest_wait_seconds']) == (1, 360)
    people = {row['user_id']: row for row in body['now']['operators']}
    assert (people[501]['status_label'], people[501]['status_seconds']) == ('Онлайн', 600)
    assert (people[502]['status_label'], people[502]['status_seconds']) == ('Не в системе', 35 * 60)
