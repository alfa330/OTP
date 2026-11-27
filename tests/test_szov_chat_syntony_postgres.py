# -*- coding: utf-8 -*-
"""Сборка табло «Чат», почасового отчёта и дня выгрузки СЗоВ по Syntony (szov.py) на настоящем
Postgres: таблицы вебхука/сторожа API, отметки сторожа (syntony_chat_sync_marks), запрос окна.

SYNTONY_CHATS_TEST_PORT — порт локального кластера (пароль — PGPASSWORD). Своя схема со
случайным именем, в конце удаляется. Люди и номера выдуманы.

    SYNTONY_CHATS_TEST_PORT=55433 PGPASSWORD=… python -m pytest tests/test_szov_chat_syntony_postgres.py
"""

import contextlib
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from syntony_chats import client, rules, schema, store, sync, szov  # noqa: E402

PORT = os.environ.get('SYNTONY_CHATS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (SYNTONY_CHATS_TEST_PORT)')

LOCAL = timezone(timedelta(hours=5))
SZOV_CHANNEL = 'b40df588-75cc-4562-be29-b1e73580fd49'     # из szov.SZOV_CHANNEL_IDS
OP_CHANNEL = 'op-channel-0001'


def utc(text):
    return rules.parse_time(text)


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
    name = 'syntony_sync_' + uuid.uuid4().hex[:10]
    admin = _Db('public')
    with admin._get_cursor() as cur:
        cur.execute('CREATE SCHEMA ' + name)
    handle = _Db(name)
    with handle._get_cursor() as cur:
        schema.init_syntony_chats_schema(cur)
        # График работы: по нему часы биллинга режут «Закрытие чатов» сменой (billing.shift_spans).
        cur.execute("""
            CREATE TABLE work_shifts (
                id SERIAL PRIMARY KEY, operator_id INTEGER NOT NULL, shift_date DATE NOT NULL,
                start_time TIME NOT NULL, end_time TIME NOT NULL,
                shift_type VARCHAR(32) NOT NULL DEFAULT 'regular')
        """)
    try:
        yield handle
    finally:
        with admin._get_cursor() as cur:
            cur.execute('DROP SCHEMA %s CASCADE' % name)


def api_message(mid, conversation, created, *, direction='inbound', sender='contact', author=None,
                status=None):
    return {'id': mid, 'conversationId': conversation, 'direction': direction, 'senderType': sender,
            'origin': 'contact' if sender == 'contact' else ('crm_operator' if sender == 'human' else sender),
            'author': {'id': 'u-' + author, 'fullName': author} if author else None,
            'contentType': 'text', 'text': 'текст', 'createdAt': created,
            'status': status or ('received' if direction == 'inbound' else 'sent'),
            'statusAt': created}


def api_conversation(cid, created, channel=SZOV_CHANNEL, *, handoff=None, phone='77010000001'):
    return {'id': cid, 'park': {'id': 'p1', 'name': 'Ноль такси'},
            'channel': {'id': channel, 'type': 'whatsapp', 'provider': 'whatsmeow',
                        'name': 'WhatsApp', 'phoneNumber': '77000000000'},
            'contact': {'id': 'k-' + cid, 'phone': phone, 'name': 'Водитель'},
            'status': 'active', 'assignedTo': None, 'messageCount': 2,
            'createdAt': created, 'lastMessageAt': created, 'lastInboundAt': created,
            'closedAt': None, 'closedReason': None, 'leadId': None, 'handoff': handoff}


NOW = utc('2026-11-20T10:00:00Z')         # 15:00 по Алматы


# ── Сборка табло и отчёта из базы ────────────────────────────────────────────────────────────

PEOPLE = {
    'Бекова Динара': {'id': 2, 'name': 'Бекова Динара', 'status': 'working'},
    'Ахметова Айгерим Болатқызы': {'id': 1, 'name': 'Ахметова Айгерим', 'status': 'working'},
}
ROSTER = [{'id': 1, 'name': 'Ахметова Айгерим', 'status': 'working'},
          {'id': 2, 'name': 'Бекова Динара', 'status': 'working'},
          {'id': 3, 'name': 'Жумабаева Сауле', 'status': 'working'}]


def _seed(db):
    """Сутки 20.11 по Алматы (UTC+5): два чата СЗоВ, тестовый номер и номер ОП."""
    rows = [
        # c1 — начат 14:00, первая реакция 2 мин (Бекова), ещё ход 14:30 → 14:34.
        ('c1', '2026-11-20T09:00:00Z', SZOV_CHANNEL, None, '77010000001', [
            ('a1', '2026-11-20T09:00:00Z', 'driver', None),
            ('a2', '2026-11-20T09:02:00Z', 'human', 'Бекова Динара'),
            ('a3', '2026-11-20T09:30:00Z', 'driver', None),
            ('a4', '2026-11-20T09:34:00Z', 'human', 'Бекова Динара'),
        ]),
        # c2 — ИИ передал в 14:41, человек ответил в 14:50: первая реакция 9 мин.
        ('c2', '2026-11-20T09:40:00Z', SZOV_CHANNEL, {'requestedAt': '2026-11-20T09:41:00Z'},
         '77010000002', [
            ('b1', '2026-11-20T09:40:00Z', 'driver', None),
            ('b2', '2026-11-20T09:41:00Z', 'ai', None),
            ('b3', '2026-11-20T09:50:00Z', 'human', 'Ахметова Айгерим Болатқызы'),
        ]),
        # тестовый номер — не чат; номер ОП — не чат СЗоВ, но ответ держит человека онлайн.
        ('t1', '2026-11-20T09:10:00Z', SZOV_CHANNEL, None, '77019999999', [
            ('t1m', '2026-11-20T09:10:00Z', 'driver', None),
        ]),
        ('o1', '2026-11-20T09:55:00Z', OP_CHANNEL, None, '77010000003', [
            ('o1a', '2026-11-20T09:55:00Z', 'driver', None),
            ('o1b', '2026-11-20T09:56:00Z', 'human', 'Бекова Динара'),
        ]),
        # Ночь на номере ОП, 00:01 Алматы: с этого момента данные за 20.11 есть целиком.
        ('n1', '2026-11-19T19:01:00Z', OP_CHANNEL, None, '77010000004', [
            ('n1a', '2026-11-19T19:01:00Z', 'driver', None),
        ]),
    ]
    with db._get_cursor() as cur:
        for cid, created, channel, handoff, phone, messages in rows:
            store.upsert_conversation(cur, rules.conversation_row(
                api_conversation(cid, created, channel, handoff=handoff, phone=phone), NOW))
            for mid, moment, kind, author in messages:
                direction = 'inbound' if kind == 'driver' else 'outbound'
                sender = {'driver': 'contact', 'human': 'human', 'ai': 'ai'}[kind]
                store.upsert_message(cur, rules.message_row(
                    api_message(mid, cid, moment, direction=direction, sender=sender,
                                author=author), NOW, rules.VIA_API))
        # Сторож прошёл ленту и диалоги в 15:00: отметка — последнее полученное минус 5 минут.
        _mark(cur, sync.MARK_FEED, NOW - sync.OVERLAP, written_at=NOW)
        _mark(cur, sync.MARK_CONVERSATIONS, NOW - sync.OVERLAP, written_at=NOW)


def _mark(cur, name, mark, *, written_at):
    store.set_mark(cur, name, mark)
    cur.execute('UPDATE syntony_chat_sync_marks SET updated_at = %s WHERE name = %s',
                (written_at, name))


def _ctx():
    return szov.Context(
        resolve=PEOPLE.get, roster=ROSTER,
        is_test_sql=lambda expr: "(%s = '77019999999')" % expr)


def test_wallboard_snapshot_from_db(db):
    _seed(db)
    now = utc('2026-11-20T10:00:00Z')         # 15:00 Алматы
    with mock.patch.dict(os.environ, {client.ENV_KEY: 'sck_' + 'a' * 32}):
        snapshot = szov.wallboard_snapshot(db, _ctx(), now=now)
    assert snapshot['source'] == 'syntony' and snapshot['statuses'] is False
    today = snapshot['today']
    assert (today['chats'], today['chats_new']) == (2, 2)
    assert today['first_reply_seconds'] == (120 + 540) / 2
    # внутри: c1 — (2 + 4) / 2 = 3 мин; c2 — ход 14:40 закрыл ИИ, человеку ходов нет.
    assert today['inner_reply_seconds'] == 180
    now_block = snapshot['now']
    # Бекова ответила в 14:56 на номере ОП — онлайн; Ахметова — 14:50, 10 мин назад — онлайн.
    # По чату у каждой — при равенстве по ФИО.
    assert [item['name'] for item in now_block['operators']] == ['Ахметова Айгерим', 'Бекова Динара']
    assert [item['chats'] for item in now_block['operators']] == [1, 1]
    # Ответы 14:02, 14:34, 14:56 — разрывы больше 15 мин: текущая полоса с 14:56.
    assert now_block['operators'][1]['since'] == '2026-11-20 14:56:00'
    assert now_block['operators_online'] == 2 and now_block['operators_offline'] == 1
    assert now_block['offline_operators'] == [{'operator_id': 3, 'name': 'Жумабаева Сауле', 'since': ''}]
    assert now_block['operators_busy'] is None and now_block['open_chats'] is None
    hours = {row['hour']: row for row in snapshot['hourly']}
    assert len(snapshot['hourly']) == 16 and hours[15]['partial']
    assert (hours[14]['chats'], hours[14]['chats_new']) == (2, 2)
    assert hours[14]['operators_online'] == 2
    assert snapshot['diagnostics']['coverage_error'] is None


def test_wallboard_raises_when_data_froze(db):
    _seed(db)
    later = utc('2026-11-20T10:30:00Z')       # догрузка 30 мин назад, свежих сообщений нет
    with mock.patch.dict(os.environ, {client.ENV_KEY: 'sck_' + 'a' * 32}):
        with pytest.raises(RuntimeError, match='Syntony не отвечает 30 мин: чаты не догружаются'):
            szov.wallboard_snapshot(db, _ctx(), now=later)
    # Без ключа истории (только вебхук) судить не по чему — снимок собирается.
    with mock.patch.dict(os.environ, {client.ENV_KEY: ''}):
        assert szov.wallboard_snapshot(db, _ctx(), now=later)['today']['chats'] == 2


def test_wallboard_waits_for_the_first_pull(db):
    # Сторож ещё ни разу не прошёл ленту: показывать нечего, даже если вебхук что-то принёс.
    _seed(db)
    with db._get_cursor() as cur:
        cur.execute('DELETE FROM syntony_chat_sync_marks')
    with mock.patch.dict(os.environ, {client.ENV_KEY: 'sck_' + 'a' * 32}):
        with pytest.raises(RuntimeError, match='первая загрузка чатов ещё не прошла'):
            szov.wallboard_snapshot(db, _ctx(), now=utc('2026-11-20T09:57:00Z'))


def test_wallboard_waits_while_the_feed_catches_up(db):
    # Прогон был минуту назад, но лента дочитана только до 10:00 — сутки неполные.
    _seed(db)
    with db._get_cursor() as cur:
        _mark(cur, sync.MARK_FEED, utc('2026-11-20T05:00:00Z'), written_at=NOW)
    with mock.patch.dict(os.environ, {client.ENV_KEY: 'sck_' + 'a' * 32}):
        with pytest.raises(RuntimeError, match='чаты ещё догружаются — пока есть по 20.11 10:00'):
            szov.wallboard_snapshot(db, _ctx(), now=NOW + timedelta(minutes=1))


def test_stalled_conversation_snapshots_are_stale_too(db):
    # Лента идёт, а снимки диалогов 20 минут не пишутся: новые чаты не знают своего номера.
    _seed(db)
    with db._get_cursor() as cur:
        _mark(cur, sync.MARK_FEED, NOW + timedelta(minutes=14), written_at=NOW + timedelta(minutes=19))
    with mock.patch.dict(os.environ, {client.ENV_KEY: 'sck_' + 'a' * 32}):
        with pytest.raises(RuntimeError, match='Syntony не отвечает 20 мин'):
            szov.wallboard_snapshot(db, _ctx(), now=NOW + timedelta(minutes=20))


def test_wallboard_says_when_the_day_is_incomplete(db):
    # Данные начались в 14:00 (ночного сообщения нет) — сутки неполные, табло говорит словами.
    _seed(db)
    with db._get_cursor() as cur:
        cur.execute("DELETE FROM syntony_chat_messages WHERE id = 'n1a'")
    with mock.patch.dict(os.environ, {client.ENV_KEY: 'sck_' + 'a' * 32}):
        with pytest.raises(RuntimeError, match='Syntony: сутки неполные — чаты есть с 20.11.2026 14:00'):
            szov.wallboard_snapshot(db, _ctx(), now=NOW)


def test_evening_turn_answered_after_midnight_stays_in_yesterday(db):
    # Водитель писал вчера в 22:00 и сегодня в 00:05, ответили в 00:20: ход начат вчера —
    # в сегодняшнее «внутри чата» он не идёт (окно читается с запасом в 6 часов).
    _seed(db)
    with db._get_cursor() as cur:
        store.upsert_conversation(cur, rules.conversation_row(
            api_conversation('y1', '2026-11-19T16:50:00Z', phone='77010000005'), NOW))
        for mid, moment, kind, author in (('y1a', '2026-11-19T17:00:00Z', 'driver', None),
                                          ('y1b', '2026-11-19T19:05:00Z', 'driver', None),
                                          ('y1c', '2026-11-19T19:20:00Z', 'human', 'Бекова Динара')):
            direction = 'inbound' if kind == 'driver' else 'outbound'
            sender = 'contact' if kind == 'driver' else 'human'
            store.upsert_message(cur, rules.message_row(
                api_message(mid, 'y1', moment, direction=direction, sender=sender, author=author),
                NOW, rules.VIA_API))
    with mock.patch.dict(os.environ, {client.ENV_KEY: 'sck_' + 'a' * 32}):
        today = szov.wallboard_snapshot(db, _ctx(), now=NOW)['today']
    assert (today['chats'], today['chats_new']) == (3, 2)        # писал сегодня, начат вчера
    assert today['inner_reply_seconds'] == 180                    # как без y1


def test_puller_switched_off_means_no_staleness_verdict(db):
    _seed(db)
    with mock.patch.dict(os.environ, {client.ENV_KEY: 'sck_' + 'a' * 32,
                                      sync.ENV_ENABLED: 'false'}):
        assert szov.wallboard_snapshot(db, _ctx(), now=utc('2026-11-20T12:00:00Z'))['today']['chats'] == 2


def test_hourly_report_from_db(db):
    _seed(db)
    with mock.patch.dict(os.environ, {client.ENV_KEY: 'sck_' + 'a' * 32}):
        data = szov.hourly_report(db, _ctx(), now=utc('2026-11-20T10:00:30Z'))
    assert data['hour_label'] == '14:00–15:00' and not data['is_previous_day']
    assert (data['chats_hour'], data['chats_new_hour']) == (2, 2)
    assert (data['chats_day'], data['chats_new_day']) == (2, 2)
    assert data['first_reply_hour'] == 330
    assert data['offline_count'] == 1
    assert [(item['name'], item['presence']) for item in data['operators']] == [
        ('Ахметова Айгерим', 'online'), ('Бекова Динара', 'online')]
    by_name = {item['name']: item for item in data['operators']}
    assert by_name['Бекова Динара']['chats'] == 1
    assert by_name['Бекова Динара']['first_reply_day'] == 120
    assert by_name['Ахметова Айгерим']['first_reply_day'] == 540


def test_midnight_report_is_about_yesterday(db):
    _seed(db)
    with mock.patch.dict(os.environ, {client.ENV_KEY: ''}):
        data = szov.hourly_report(db, _ctx(), now=utc('2026-11-20T19:00:20Z'))   # 00:00 21.11
    assert data['is_previous_day'] and data['day'] == '2026-11-20'
    assert data['hour_label'] == '23:00–00:00'
    assert data['chats_day'] == 2 and data['chats_hour'] == 0


def test_day_block_and_coverage_gap(db):
    _seed(db)
    day = datetime(2026, 11, 20).date()
    hourly, totals, error = szov.day_block(db, _ctx(), day, now=utc('2026-11-21T06:00:00Z'))
    assert error is None and len(hourly) == 24
    assert (totals['chats'], totals['chats_new']) == (2, 2)
    # Итоги по сотрудникам за сутки — для листов «оператор × час» выгрузки.
    assert {item['name']: item['chats'] for item in totals['operators_day']} == {
        'Ахметова Айгерим': 1, 'Бекова Динара': 1}
    # Сутки раньше начала загрузки — «нет данных», а не нули.
    hourly, totals, error = szov.day_block(db, _ctx(), datetime(2026, 11, 17).date(),
                                           now=utc('2026-11-21T06:00:00Z'))
    assert hourly == [] and error == 'Нет данных Syntony за эти сутки: чаты есть с 20.11.2026 00:01'


def test_day_without_its_first_hours_is_no_data(db):
    # Данные начались посреди суток (сторож запущен днём, вебхук до него молчал): такие сутки
    # неполные — «нет данных», а не заниженные числа.
    _seed(db)
    with db._get_cursor() as cur:
        cur.execute("DELETE FROM syntony_chat_messages WHERE id = 'n1a'")
    hourly, _totals, error = szov.day_block(db, _ctx(), datetime(2026, 11, 20).date(),
                                            now=utc('2026-11-21T06:00:00Z'))
    assert hourly == [] and error.endswith('чаты есть с 20.11.2026 14:00')
