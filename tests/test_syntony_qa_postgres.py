# -*- coding: utf-8 -*-
"""Проверка качества чатов СЗоВ по Syntony (SZOV-9) на настоящем Postgres: заморозка закрытых
эпизодов в снимки (syntony_chats/qa.freeze) и «Случайный чат» журнала (qa.pick).

Таблицы Syntony — их схема, как на проде; снимки, сотрудники и оценки журнала — минимальная
копия колонок, которые читают заморозка и выборка (полная схема журнала — в db/schema.py, её
уникальный индекс эпизода Syntony сторожит tests/test_syntony_qa.py). Люди и номера выдуманы.

    SYNTONY_CHATS_TEST_PORT=55433 PGPASSWORD=… python -m pytest tests/test_syntony_qa_postgres.py
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from syntony_chats import qa, rules, store, szov  # noqa: E402
from test_szov_chat_syntony_postgres import (  # noqa: E402,F401
    PORT, SZOV_CHANNEL, api_conversation, api_message, db, utc)

pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (SYNTONY_CHATS_TEST_PORT)')

FOREIGN = 'cd824063-38ca-481f-8c54-99b4455d8840'
PEOPLE = {'Бекова Динара': {'id': 2, 'name': 'Бекова Динара'},
          'Ахметова Айгерим': {'id': 1, 'name': 'Ахметова Айгерим'}}


def _ctx():
    return szov.Context(resolve=PEOPLE.get, roster=list(PEOPLE.values()),
                        channels={SZOV_CHANNEL})


def _journal_tables(db):
    with db._get_cursor() as cur:
        cur.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, department_id INTEGER, "
                    "status TEXT)")
        cur.execute("INSERT INTO users VALUES (1, 'Ахметова Айгерим', 1, 'working'), "
                    "(2, 'Бекова Динара', 1, 'working'), (9, 'Проверяющий', 1, 'working')")
        cur.execute("""
            CREATE TABLE c2d_chat_snapshots (
                id SERIAL PRIMARY KEY, request_id BIGINT UNIQUE, dialog_id BIGINT, day DATE,
                operator_id INTEGER, c2d_operator_name TEXT, channel_name TEXT, transport TEXT,
                client_name TEXT, client_phone TEXT,
                messages JSONB NOT NULL DEFAULT '[]'::jsonb,
                messages_count INTEGER NOT NULL DEFAULT 0, request_payload JSONB,
                created_by INTEGER, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                source TEXT NOT NULL DEFAULT 'chat2desk', wz_channel_id TEXT, wz_chat_id TEXT,
                episode_start TIMESTAMPTZ, episode_end TIMESTAMPTZ)
        """)
        cur.execute("CREATE UNIQUE INDEX idx_syn_snapshots_episode ON c2d_chat_snapshots "
                    "(wz_channel_id, wz_chat_id, episode_start) WHERE source = 'syntony'")
        cur.execute("CREATE TABLE calls (id SERIAL PRIMARY KEY, c2d_snapshot_id INTEGER, "
                    "evaluator_id INTEGER)")


def _conversation(db, cid, channel, messages, now):
    """messages: (id, момент UTC, 'driver'|'human'|'ai', автор, дополнение)."""
    with db._get_cursor() as cur:
        conversation = api_conversation(cid, messages[0][1], channel, phone='7701000000' + cid[-1])
        store.upsert_conversation(cur, rules.conversation_row(conversation, now))
        for mid, moment, kind, author, extra in messages:
            direction = 'inbound' if kind == 'driver' else 'outbound'
            sender = {'driver': 'contact', 'human': 'human', 'ai': 'ai'}[kind]
            message = api_message(mid, cid, moment, direction=direction, sender=sender,
                                  author=author)
            message.update(extra or {})
            store.upsert_message(cur, rules.message_row(message, now, rules.VIA_API))


PHOTO = {'contentType': 'image', 'text': '',
         'media': {'mimeType': 'image/jpeg', 'fileName': 'a.jpg', 'url': '/api/chats-api/v1/files/p'}}
NOW = utc('2026-11-23T12:00:00Z')            # 17:00 по Алматы


def _seed(db):
    _journal_tables(db)
    _conversation(db, 'c1', SZOV_CHANNEL, [
        # 19.11 (до перехода) — не берётся.
        ('a1', '2026-11-19T10:00:00Z', 'driver', None, None),
        ('a2', '2026-11-19T10:05:00Z', 'human', 'Бекова Динара', None),
        # 21.11 — диалог: ИИ, потом сотрудник, фото водителя.
        ('b1', '2026-11-21T05:00:00Z', 'driver', None, None),
        ('b2', '2026-11-21T05:01:00Z', 'ai', None, None),
        ('b3', '2026-11-21T05:03:00Z', 'human', 'Бекова Динара', None),
        ('b4', '2026-11-21T05:04:00Z', 'driver', None, PHOTO),
        ('b5', '2026-11-21T05:06:00Z', 'human', 'Бекова Динара', None),
        ('b6', '2026-11-21T05:07:00Z', 'human', 'Ахметова Айгерим', None),
        # 23.11 — ещё открыт (последнее сообщение 2 часа назад).
        ('c1', '2026-11-23T10:00:00Z', 'driver', None, None),
        ('c2', '2026-11-23T10:02:00Z', 'human', 'Ахметова Айгерим', None),
    ], NOW)
    # Целиком вёл ИИ — не берётся.
    _conversation(db, 'c2', SZOV_CHANNEL, [
        ('d1', '2026-11-22T05:00:00Z', 'driver', None, None),
        ('d2', '2026-11-22T05:01:00Z', 'ai', None, None),
    ], NOW)
    # Чужой номер сети — не СЗоВ.
    _conversation(db, 'c3', FOREIGN, [
        ('e1', '2026-11-22T05:00:00Z', 'driver', None, None),
        ('e2', '2026-11-22T05:01:00Z', 'human', 'Бекова Динара', None),
    ], NOW)


def _snapshots(db):
    with db._get_cursor() as cur:
        cur.execute("""SELECT wz_chat_id, day, operator_id, messages_count, messages, request_payload,
                              channel_name, episode_start
                         FROM c2d_chat_snapshots WHERE source = 'syntony' ORDER BY episode_start""")
        return cur.fetchall()


def test_freeze_takes_operator_dialogs_from_the_twentieth(db):
    _seed(db)
    totals = qa.freeze(db, _ctx(), now=NOW, environ={})
    assert totals['written'] == 1
    assert totals['dialogs'] == 1 and totals['ai_only'] == 1 and totals['before_start'] == 1
    rows = _snapshots(db)
    assert len(rows) == 1
    chat_id, day, operator_id, count, messages, payload, channel_name, start = rows[0]
    assert (chat_id, day, operator_id, count) == ('c1', date(2026, 11, 21), 2, 6)
    assert channel_name == 'Ноль такси'
    assert start == utc('2026-11-21T05:00:00Z')
    assert [m['type'] for m in messages] == ['from_client', 'autoreply', 'to_client', 'from_client',
                                            'to_client', 'to_client']
    assert messages[1]['label'] == 'ИИ-агент'
    assert messages[3]['photo'] == 'syntony-file:b4?filename=a.jpg'
    assert messages[5]['author'] == 'Ахметова Айгерим'
    assert payload['operator_share'] == 0.667 and payload['human_outbound_count'] == 3
    assert payload['ai_messages'] == 1
    with db._get_cursor() as cur:
        cur.execute("SELECT conversation_id, closed_until FROM syntony_qa_marks ORDER BY 1")
        marks = dict(cur.fetchall())
        cur.execute("SELECT mark FROM syntony_chat_sync_marks WHERE name = %s", (qa.MARK_RUN,))
        run_mark = cur.fetchone()[0]
    assert marks == {'c1': utc('2026-11-21T05:07:00Z'), 'c2': utc('2026-11-22T05:01:00Z')}
    assert run_mark == NOW


def test_second_run_is_idempotent_and_picks_up_closed_tail(db):
    _seed(db)
    qa.freeze(db, _ctx(), now=NOW, environ={})
    again = qa.freeze(db, _ctx(), now=NOW + timedelta(minutes=30), environ={})
    assert again['written'] == 0
    # Через 6 часов тишины открытый эпизод 23.11 закрывается и пишется.
    later = qa.freeze(db, _ctx(), now=utc('2026-11-23T16:30:00Z'), environ={})
    assert later['written'] == 1
    rows = _snapshots(db)
    assert [(r[0], r[1], r[2]) for r in rows] == [('c1', date(2026, 11, 21), 2),
                                                  ('c1', date(2026, 11, 23), 1)]


def test_late_message_inside_frozen_episode_is_ignored(db):
    _seed(db)
    qa.freeze(db, _ctx(), now=NOW, environ={})
    with db._get_cursor() as cur:
        late = api_message('b9', 'c1', '2026-11-21T05:05:00Z', direction='inbound', sender='contact')
        store.upsert_message(cur, rules.message_row(late, NOW, rules.VIA_API))
    again = qa.freeze(db, _ctx(), now=NOW + timedelta(hours=1), environ={})
    assert again['written'] == 0
    assert _snapshots(db)[0][3] == 6


def test_pick_filters_like_chat2desk(db):
    _seed(db)
    # Диалог одного сотрудника 22.11 — к двум снимкам c1 (21.11 — двое, 23.11 — один).
    _conversation(db, 'c4', SZOV_CHANNEL, [
        ('g1', '2026-11-22T07:00:00Z', 'driver', None, None),
        ('g2', '2026-11-22T07:02:00Z', 'human', 'Бекова Динара', None),
        ('g3', '2026-11-22T07:03:00Z', 'driver', None, None),
        ('g4', '2026-11-22T07:04:00Z', 'human', 'Бекова Динара', None),
    ], NOW)
    qa.freeze(db, _ctx(), now=utc('2026-11-23T16:30:00Z'), environ={})
    with db._get_cursor() as cur:
        # 21.11 отвечали двое (у Бековой 2 из 3 — 67 %): в проверку не идёт, как у Wazzup.
        snap_id, count = qa.pick(cur, date_from='2026-11-20', date_to='2026-11-26')
        assert count == 2 and snap_id
        assert qa.pick(cur, date_from='2026-11-21', date_to='2026-11-21')[1] == 0
        assert qa.pick(cur, date_from='2026-11-21', date_to='2026-11-21', min_share=0.6)[1] == 1
        assert qa.pick(cur, operator_id=2)[1] == 1
        assert qa.pick(cur, operator_id=1)[1] == 1
        assert qa.pick(cur, date_from='2026-11-22', date_to='2026-11-22')[1] == 1
        assert qa.pick(cur, min_messages=3)[1] == 1
        assert qa.pick(cur, max_messages=2)[1] == 1
        assert qa.pick(cur, department_id=1)[1] == 2
        assert qa.pick(cur, department_id=5)[1] == 0
        cur.execute("SELECT id FROM c2d_chat_snapshots WHERE wz_chat_id = 'c4'")
        evaluated = cur.fetchone()[0]
        cur.execute("INSERT INTO calls (c2d_snapshot_id, evaluator_id) VALUES (%s, 9)", (evaluated,))
        assert qa.pick(cur, exclude='any')[1] == 1
        assert qa.pick(cur, exclude='mine', evaluator_id=9)[1] == 1
        assert qa.pick(cur, exclude='mine', evaluator_id=1)[1] == 2
        assert qa.pick(cur, exclude='none')[1] == 2
        assert qa.pick(cur, exclude_ids=[evaluated], exclude='none')[1] == 1
