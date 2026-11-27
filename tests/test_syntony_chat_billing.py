# -*- coding: utf-8 -*-
"""Биллинг чата по Syntony и статусы операторов на табло СЗоВ «Чат» — на настоящем Postgres.

Решения владельца 24.11.2026: биллинг «как на табло» (поступило — диалоги, где водитель писал
в эти сутки; обслужено — где ответил сотрудник; первый ответ и SL — по начатым в эти сутки),
Syntony с 18.11, раньше — Chat2Desk; линию держит только статус «Онлайн».

SYNTONY_CHATS_TEST_PORT — порт локального кластера (пароль — PGPASSWORD). Сутки 20.11 —
те же, что у tests/test_szov_chat_syntony_postgres.py. Люди и номера выдуманы.
"""

import os
import sys
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from resource_fte import chat as billing_chat  # noqa: E402
from syntony_chats import billing, client, statuses, store, szov  # noqa: E402
from syntony_chats import rules, sync  # noqa: E402
from test_szov_chat_syntony_postgres import (  # noqa: E402,F401
    NOW, PORT, ROSTER, SZOV_CHANNEL, _ctx, _mark, _seed, api_conversation, api_message, db, utc)

pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (SYNTONY_CHATS_TEST_PORT)')

KEY_ENV = {client.ENV_KEY: 'sck_' + 'a' * 32}
DAY = date(2026, 11, 20)


def _interval(number, partner, key, started, ended=None, name='Имя в Syntony'):
    return {'id': 'st-%d' % number,
            'operator': {'id': 'op-%s' % partner, 'fullName': name, 'partnerUserIds': [str(partner)]},
            'park': {'id': 'p1', 'name': 'Центр регистрации'},
            'status': {'key': key, 'label': statuses.STATUS_LABELS[key]},
            'startedAt': started, 'endedAt': ended, 'durationSeconds': 0}


def _seed_statuses(db, *, written_at=NOW):
    """Бекова (2) онлайн с 14:30, Ахметова (1) с 14:55 на перерыве после онлайна 14:00–14:55,
    Жумабаева (3) была онлайн до 13:00 и ушла."""
    with db._get_cursor() as cur:
        for item in (_interval(1, 2, 'available', '2026-11-20T09:30:00Z'),
                     _interval(2, 1, 'available', '2026-11-20T09:00:00Z', '2026-11-20T09:55:00Z'),
                     _interval(3, 1, 'break', '2026-11-20T09:55:00Z'),
                     _interval(4, 3, 'available', '2026-11-20T06:00:00Z', '2026-11-20T08:00:00Z')):
            statuses.upsert(cur, statuses.interval_row(item, NOW))
        _mark(cur, statuses.MARK_STATUSES, NOW, written_at=written_at)
        _mark(cur, statuses.MARK_STATUSES_FROM, utc('2026-11-12T19:00:00Z'), written_at=written_at)


# ── Табло: статусы Syntony ────────────────────────────────────────────────────────────────

def test_wallboard_shows_syntony_statuses_when_they_are_fresh(db):
    _seed(db)
    _seed_statuses(db)
    with mock.patch.dict(os.environ, KEY_ENV):
        snapshot = szov.wallboard_snapshot(db, _ctx(), now=NOW)
    assert snapshot['statuses'] is True
    now_block = snapshot['now']
    # Сверху онлайн, ниже перерыв; статус — подписью Syntony, время — в этом статусе.
    assert [(item['name'], item['status'], item['status_key']) for item in now_block['operators']] == [
        ('Бекова Динара', 'Онлайн', 'online'), ('Ахметова Айгерим', 'Перерыв', 'break')]
    assert [item['seconds'] for item in now_block['operators']] == [30 * 60, 5 * 60]
    assert now_block['operators'][0]['since'] == '2026-11-20 14:30:00'
    assert [item['chats'] for item in now_block['operators']] == [1, 1]
    assert (now_block['operators_online'], now_block['operators_on_break'],
            now_block['operators_on_holiday'], now_block['operators_other']) == (1, 1, 0, 0)
    # Отдельного «Занят» у Syntony нет — её «Занят» считается закрытием чатов.
    assert now_block['operators_busy'] is None
    # Жумабаева ушла в 13:00 — «не в системе с» по концу её интервала.
    assert now_block['offline_operators'] == [
        {'operator_id': 3, 'name': 'Жумабаева Сауле', 'since': '2026-11-20 13:00:00'}]
    # Люди на линии по часам — по статусу «Онлайн»: в 14:00–15:00 обе (Ахметова 55 мин,
    # Бекова 30 мин), в 11:00–12:00 — Жумабаева.
    hours = {row['hour']: row for row in snapshot['hourly']}
    assert snapshot['diagnostics']['line_source'] == 'statuses'
    assert [(p['name'], p['seconds']) for p in hours[14]['operators']] == [
        ('Ахметова Айгерим', 55 * 60), ('Бекова Динара', 30 * 60)]
    assert hours[11]['operators_online'] == 1 and hours[13]['operators_online'] == 0


def _seed_closing(db):
    """Жумабаева (3) вернулась в 14:40 в «Занят» Syntony — закрывает чаты."""
    with db._get_cursor() as cur:
        statuses.upsert(cur, statuses.interval_row(
            _interval(5, 3, 'busy', '2026-11-20T09:40:00Z'), NOW))


def _shift(db, operator_id, day, start, end):
    """Смена в графике работы (work_shifts) — по ней часы биллинга режут «Закрытие чатов»."""
    with db._get_cursor() as cur:
        cur.execute('INSERT INTO work_shifts (operator_id, shift_date, start_time, end_time) '
                    'VALUES (%s, %s, %s, %s)', (operator_id, day, start, end))


def test_busy_is_closing_chats_on_the_wallboard_and_stays_off_the_line(db):
    """Решение владельца 27.11.2026: «Занят» Syntony у чатников — «Закрытие чатов»: на табло
    так подписан и окрашен (как закрытие чатов Chat2Desk), считается отдельным счётчиком, в
    людей на линии и в «Онлайн» не идёт."""
    _seed(db)
    _seed_statuses(db)
    _seed_closing(db)
    with mock.patch.dict(os.environ, KEY_ENV):
        snapshot = szov.wallboard_snapshot(db, _ctx(), now=NOW)
        report = szov.hourly_report(db, _ctx(), now=NOW)
    now_block = snapshot['now']
    assert [(item['name'], item['status'], item['status_key']) for item in now_block['operators']] == [
        ('Бекова Динара', 'Онлайн', 'online'), ('Ахметова Айгерим', 'Перерыв', 'break'),
        ('Жумабаева Сауле', 'Закрытие чатов', 'holiday')]
    assert now_block['operators'][2]['seconds'] == 20 * 60
    assert (now_block['operators_online'], now_block['operators_on_break'],
            now_block['operators_on_holiday'], now_block['operators_other'],
            now_block['operators_offline']) == (1, 1, 1, 0, 0)
    assert now_block['operators_busy'] is None
    hours = {row['hour']: row for row in snapshot['hourly']}
    assert [p['name'] for p in hours[14]['operators']] == ['Ахметова Айгерим', 'Бекова Динара']
    # Почасовой отчёт: закрытие чатов — статусом, не онлайн.
    assert report['on_status'] == [{'name': 'Ахметова Айгерим', 'status': 'Перерыв'},
                                   {'name': 'Жумабаева Сауле', 'status': 'Закрытие чатов'}]
    by_name = {item['name']: (item['status'], item['presence']) for item in report['operators']}
    assert by_name['Жумабаева Сауле'] == ('закрытие чатов', 'status')


def test_stale_statuses_fall_back_to_replies(db):
    # Сторож 20 минут не дочитывал статусы — верить им нельзя: онлайн снова по ответам.
    _seed(db)
    _seed_statuses(db, written_at=NOW - timedelta(minutes=20))
    with mock.patch.dict(os.environ, KEY_ENV):
        snapshot = szov.wallboard_snapshot(db, _ctx(), now=NOW)
    assert snapshot['statuses'] is False
    assert {item['status'] for item in snapshot['now']['operators']} == {'Онлайн'}
    assert snapshot['diagnostics']['line_source'] == 'replies'


def test_hourly_report_uses_statuses(db):
    _seed(db)
    _seed_statuses(db)
    with mock.patch.dict(os.environ, KEY_ENV):
        report = szov.hourly_report(db, _ctx(), now=NOW)
    assert [item['name'] for item in report['online']] == ['Бекова Динара']
    assert report['on_status'] == [{'name': 'Ахметова Айгерим', 'status': 'Перерыв'}]
    assert report['offline_count'] == 1
    by_name = {item['name']: (item['status'], item['presence']) for item in report['operators']}
    assert by_name['Бекова Динара'] == ('онлайн', 'online')
    assert by_name['Ахметова Айгерим'] == ('перерыв', 'status')


# ── Сборка суток биллинга ─────────────────────────────────────────────────────────────────

def _rows(db, table, order='conversation_id'):
    with db._get_cursor() as cur:
        cur.execute('SELECT * FROM %s ORDER BY %s' % (table, order))
        columns = [c[0] for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def test_build_day_follows_wallboard_rules(db):
    _seed(db)
    with mock.patch.dict(os.environ, KEY_ENV):
        result = billing.build_day(db, _ctx(), DAY, now=NOW)
    assert result == {'day': '2026-11-20', 'chats': 2, 'complete': False, 'staff_source': 'replies'}
    c1, c2 = _rows(db, 'syntony_chat_billing_chats')
    # c1: первый ответ 2 мин, внутри (2 + 4) / 2 = 3 мин, отвечала Бекова; парк — как у Chat2Desk.
    assert (c1['conversation_id'], c1['park'], c1['transport']) == ('c1', 'Ноль такси', 'WhatsApp')
    assert (c1['is_new'], c1['answered'], c1['first_reply_seconds'], c1['inner_reply_seconds']) == (
        True, True, 120, 180)
    assert (c1['operator_id'], c1['incoming_messages'], c1['outgoing_messages']) == (2, 2, 2)
    assert c1['started_at'] == datetime(2026, 11, 20, 14, 0)          # стенные часы Алматы
    assert c1['id'] >= 5 * 10 ** 12
    # c2: ИИ передал в 14:41, человек ответил в 14:50 — 9 мин; ход закрыл ИИ — внутри чата нет.
    assert (c2['first_reply_seconds'], c2['inner_reply_seconds'], c2['operator_id']) == (540, None, 1)
    people = _rows(db, 'syntony_chat_billing_operators')
    assert [(p['conversation_id'], p['operator_id'], p['replied'], p['first_reply_seconds'])
            for p in people] == [('c1', 2, True, 120), ('c2', 1, True, 540)]
    # Статусов нет — часы на линии по ответам за 15 минут: у Бековой ответы 14:02, 14:34 и
    # 14:56 → 14:02–14:17, 14:34–14:49 и 14:56–15:00 («сейчас»), у Ахметовой 14:50–15:00.
    staff = {(row['hour'], row['operator_id']): row['online_seconds']
             for row in _rows(db, 'syntony_chat_billing_staff', 'hour, operator_id')}
    assert staff[(14, 2)] == (15 + 15 + 4) * 60 and staff[(14, 1)] == 10 * 60
    assert {row['source'] for row in _rows(db, 'syntony_chat_billing_staff', 'hour')} == {'replies'}


def test_rebuild_keeps_row_ids_and_drops_gone_chats(db):
    _seed(db)
    with mock.patch.dict(os.environ, KEY_ENV):
        billing.build_day(db, _ctx(), DAY, now=NOW)
        first = {row['conversation_id']: row['id'] for row in _rows(db, 'syntony_chat_billing_chats')}
        with db._get_cursor() as cur:
            cur.execute("DELETE FROM syntony_chat_messages WHERE conversation_id = 'c2'")
        billing.build_day(db, _ctx(), DAY, now=NOW + timedelta(minutes=10))
    again = {row['conversation_id']: row['id'] for row in _rows(db, 'syntony_chat_billing_chats')}
    assert again == {'c1': first['c1']}
    assert [p['conversation_id'] for p in _rows(db, 'syntony_chat_billing_operators')] == ['c1']


def test_build_day_takes_line_hours_from_statuses_when_they_cover_the_day(db):
    _seed(db)
    _seed_statuses(db)
    with mock.patch.dict(os.environ, KEY_ENV):
        result = billing.build_day(db, _ctx(), DAY, now=NOW)
    assert result['staff_source'] == billing.STAFF_FROM_STATUSES == 'status_work'
    staff = {(row['hour'], row['operator_id']): row['online_seconds']
             for row in _rows(db, 'syntony_chat_billing_staff', 'hour, operator_id')}
    assert staff == {(11, 3): 3600, (12, 3): 3600, (14, 1): 55 * 60, (14, 2): 30 * 60}
    assert {row['source'] for row in _rows(db, 'syntony_chat_billing_staff', 'hour')} == {'status_work'}


def test_billing_hours_count_closing_chats_inside_the_shift(db):
    """Решения владельца 27.11.2026: закрытие чатов («Занят» Syntony) засчитывается в часы
    биллинга — «Факт по сотрудникам» и «Часы работы», — но только внутри смены по графику:
    забытый на ночь «Занят» (34 часа у чатника 22–24.11) в часы не идёт. «Онлайн» — целиком."""
    _seed(db)
    _seed_statuses(db)
    _seed_closing(db)
    staff_of = lambda: {(row['hour'], row['operator_id']): row['online_seconds']  # noqa: E731
                        for row in _rows(db, 'syntony_chat_billing_staff', 'hour, operator_id')}
    # Смены в графике у Жумабаевой нет — её «Занят» с 14:40 в часы не идёт; онлайн 11–13 — идёт.
    with mock.patch.dict(os.environ, KEY_ENV):
        billing.build_day(db, _ctx(), DAY, now=NOW)
    assert staff_of() == {(11, 3): 3600, (12, 3): 3600, (14, 1): 55 * 60, (14, 2): 30 * 60}
    # Смена до 14:50 — из «Занят» 14:40–15:00 в часы идут 10 минут.
    _shift(db, 3, DAY, dtime(10, 0), dtime(14, 50))
    with mock.patch.dict(os.environ, KEY_ENV):
        billing.build_day(db, _ctx(), DAY, now=NOW)
    assert staff_of() == {(11, 3): 3600, (12, 3): 3600, (14, 1): 55 * 60, (14, 2): 30 * 60,
                          (14, 3): 10 * 60}
    # Тот же факт читает экран биллинга: три человека в 14:00, 1 ч 35 мин работы в этом часу.
    with db._get_cursor() as cur:
        fact_rows = billing_chat._chat_billing_fact_staff_tx(cur, DAY, DAY)
    report = billing_chat.build_chat_billing_staff(DAY, DAY, fact_rows, {})[DAY.isoformat()]
    assert report['fact_heads'][14] == 3
    assert report['fact_hours'][14] == round((55 + 30 + 10) / 60, 4)


def test_night_shift_from_yesterday_covers_closing_chats_after_midnight(db):
    """Смена 22:00–06:00 накануне — та же смена и после полуночи: «Занят» в 01:00–01:30 идёт в
    часы суток 20.11, а в 07:00–07:30 (смены уже нет) — нет."""
    _seed(db)
    with db._get_cursor() as cur:
        for item in (_interval(11, 3, 'busy', '2026-11-19T20:00:00Z', '2026-11-19T20:30:00Z'),
                     _interval(12, 3, 'busy', '2026-11-20T02:00:00Z', '2026-11-20T02:30:00Z')):
            statuses.upsert(cur, statuses.interval_row(item, NOW))
        _mark(cur, statuses.MARK_STATUSES, NOW, written_at=NOW)
        _mark(cur, statuses.MARK_STATUSES_FROM, utc('2026-11-12T19:00:00Z'), written_at=NOW)
    _shift(db, 3, DAY - timedelta(days=1), dtime(22, 0), dtime(6, 0))
    with mock.patch.dict(os.environ, KEY_ENV):
        result = billing.build_day(db, _ctx(), DAY, now=NOW)
    assert result['staff_source'] == 'status_work'
    staff = {(row['hour'], row['operator_id']): row['online_seconds']
             for row in _rows(db, 'syntony_chat_billing_staff', 'hour, operator_id')}
    assert staff == {(1, 3): 30 * 60}


def test_days_built_by_online_only_are_rebuilt_with_closing_chats(db):
    """Сутки, собранные до правила (staff_source 'status' — только «Онлайн»), пересобираются
    сами следующим прогоном, а потом больше не трогаются."""
    _seed(db)
    _seed_statuses(db)
    _seed_closing(db)
    _shift(db, 3, DAY, dtime(9, 0), dtime(18, 0))
    later = NOW + timedelta(days=1)                       # 21.11 15:00: сутки 20.11 закрыты
    with db._get_cursor() as cur:
        _mark(cur, statuses.MARK_STATUSES, later, written_at=later)
    with mock.patch.dict(os.environ, KEY_ENV), \
            mock.patch.object(szov, 'feed_caught_up', return_value=True):
        billing.build_day(db, _ctx(), DAY, now=later)
        with db._get_cursor() as cur:
            # Как было собрано до 27.11.2026: часы только по «Онлайн».
            cur.execute("UPDATE syntony_chat_billing_days SET staff_source = 'status'")
            cur.execute("DELETE FROM syntony_chat_billing_staff WHERE operator_id = 3 AND hour >= 14")
            cur.execute("UPDATE syntony_chat_billing_staff SET source = 'status'")
        [journal] = _rows(db, 'syntony_chat_billing_days', 'day')
        assert journal['complete'] is True and journal['staff_source'] == 'status'
        results = billing.refresh(db, _ctx(), first_day=DAY, now=later)
        assert {'day': '2026-11-20', 'chats': 2, 'complete': True,
                'staff_source': 'status_work'} in results
        staff = {(row['hour'], row['operator_id']): row['online_seconds']
                 for row in _rows(db, 'syntony_chat_billing_staff', 'hour, operator_id')}
        assert staff[(14, 3)] == 20 * 60
        again = billing.refresh(db, _ctx(), first_day=DAY, now=later + timedelta(minutes=10))
    assert [item['day'] for item in again] == ['2026-11-21']   # только сегодняшние сутки


def test_day_without_data_is_not_written_as_zeros(db):
    _seed(db)
    with mock.patch.dict(os.environ, KEY_ENV):
        result = billing.build_day(db, _ctx(), date(2026, 11, 10), now=NOW)
    assert result['error'].startswith('Нет данных Syntony за эти сутки')
    assert _rows(db, 'syntony_chat_billing_chats') == []
    [journal] = _rows(db, 'syntony_chat_billing_days', 'day')
    assert journal['complete'] is False and journal['error']


def test_days_to_build_today_then_unfinished_days_three_at_a_time():
    now = utc('2026-11-24T10:00:00Z')
    journal = {date(2026, 11, 23): {'complete': True}}
    assert billing.days_to_build(journal, first_day=date(2026, 11, 18), now=now) == [
        date(2026, 11, 24), date(2026, 11, 22), date(2026, 11, 21), date(2026, 11, 20)]
    # Старше 29 дней не пересобрать — переписка удалена.
    assert billing.days_to_build({}, first_day=date(2026, 10, 1), now=now)[-1] >= date(2026, 10, 26)


# ── Выборка биллинга: Chat2Desk до 18.11, Syntony с 18.11 ─────────────────────────────────

def _chat2desk_tables(db):
    with db._get_cursor() as cur:
        cur.execute("""
            CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE test_phone_numbers (phone_key TEXT PRIMARY KEY);
            CREATE TABLE c2d_requests (
                request_id BIGINT PRIMARY KEY, day DATE, request_start TIMESTAMP,
                request_type TEXT, transport TEXT, channel_name TEXT, client_name TEXT,
                client_phone TEXT, assigned_phone TEXT, c2d_operator_name TEXT, channel_id BIGINT,
                operator_id INTEGER, reaction_time INTEGER, average_replies_time REAL,
                rating_score REAL, incoming_messages INTEGER, outgoing_messages INTEGER);
        """)
        cur.execute("INSERT INTO users VALUES (1, 'Ахметова Айгерим'), (2, 'Бекова Динара')")
        rows = [
            # 17.11 — Chat2Desk: один отвечен за минуту, второй без ответа.
            (101, '2026-11-17', '2026-11-17 10:00:00', 'Ноль такси', 555, 1, 60, 90.0),
            (102, '2026-11-17', '2026-11-17 11:00:00', 'Ноль такси', 555, None, None, None),
            # 20.11 — остаток Chat2Desk переехавшего номера: в биллинг не идёт.
            (103, '2026-11-20', '2026-11-20 12:00:00', 'Ноль такси 2', 555, 2, 30, 30.0),
            # 19.11 — номер 10908 ещё в Chat2Desk (решение 24.11.2026): идёт.
            (105, '2026-11-19', '2026-11-19 13:00:00', 'Ноль такси', 10908, 2, 90, 45.0),
            # 20.11 — тот же 10908, но его уже принимает Syntony: каждое сообщение водителя
            # приходит в обе системы, и это был бы тот же чат второй раз (решение 25.11.2026).
            (104, '2026-11-20', '2026-11-20 13:00:00', 'Ноль такси', 10908, 2, 200, None),
        ]
        for request_id, day, start, park, channel_id, operator_id, reaction, inner in rows:
            cur.execute("""
                INSERT INTO c2d_requests VALUES (%s, %s, %s, 'common', 'wa_gupshup', %s, 'Клиент',
                    '77010000009', NULL, 'Учётка', %s, %s, %s, %s, NULL, 3, 2)
            """, (request_id, day, start, park, channel_id, operator_id, reaction, inner))


def test_billing_reads_chat2desk_before_and_syntony_after_the_switch(db):
    _seed(db)
    _chat2desk_tables(db)
    with mock.patch.dict(os.environ, KEY_ENV):
        billing.build_day(db, _ctx(), DAY, now=NOW)
    report = billing_chat.get_chat_billing_report(db, date(2026, 11, 17), DAY, sl_seconds=120)
    days = {item['date']: item['totals'] for item in report['days']}
    assert days['2026-11-17'] == {
        'chats': 2, 'answered': 1, 'no_reply': 1, 'answered_sl': 1, 'first_reply_seconds': 60,
        'first_replied': 1, 'sl_base': 2, 'inner_reply_seconds': 90, 'inner_replied': 1}
    # 19.11 — после перехода, но номер 10908 ещё в Chat2Desk (105): идёт.
    assert days['2026-11-19'] == {
        'chats': 1, 'answered': 1, 'no_reply': 0, 'answered_sl': 1, 'first_reply_seconds': 90,
        'first_replied': 1, 'sl_base': 1, 'inner_reply_seconds': 45, 'inner_replied': 1}
    # 20.11 — только Syntony: 10908 (104) уже приходит и туда, остаток переехавшего (103) — тоже нет.
    assert days['2026-11-20'] == {
        'chats': 2, 'answered': 2, 'no_reply': 0, 'answered_sl': 1, 'first_reply_seconds': 660,
        'first_replied': 2, 'sl_base': 2, 'inner_reply_seconds': 180, 'inner_replied': 1}
    assert [item['park'] for item in report['parks']] == ['Ноль такси']

    # Разрез по людям — обе половины: Бековой засчитаны 105 (Chat2Desk, 19.11) и c1 (Syntony).
    people = billing_chat.get_chat_billing_operators(db, DAY - timedelta(days=1), DAY,
                                                     sl_seconds=120)
    assert {item['operator']: (item['chats'], item['first_reply_seconds'], item['outgoing_messages'])
            for item in people['operators']} == {
        'Бекова Динара': (2, 210, 4), 'Ахметова Айгерим': (1, 540, 1)}

    details = billing_chat.get_chat_billing_details(db, date(2026, 11, 17), DAY, sl_seconds=120)
    assert details['pagination']['total'] == 5
    assert details['snapshot_id'] >= 5 * 10 ** 12
    assert [(row['park'], row['first_reply_seconds'], row['no_reply'])
            for row in details['rows']][:2] == [('Ноль такси', 540, 0), ('Ноль такси', 120, 0)]
    # Снимок держит выборку: чат дня, собранный позже, на уже открытую детализацию не влияет.
    again = billing_chat.get_chat_billing_details(db, date(2026, 11, 17), DAY, sl_seconds=120,
                                                  snapshot_id=details['snapshot_id'] - 1)
    assert again['pagination']['total'] == 4

    grouping = billing_chat.get_chat_billing_grouping(db, DAY - timedelta(days=1), DAY,
                                                      sl_seconds=120)
    hours = {(day['date'], item['hour']): item for day in grouping['days'] for item in day['hours']}
    assert (hours[('2026-11-20', 14)]['chats'], hours[('2026-11-20', 14)]['answered'],
            hours[('2026-11-19', 13)]['chats']) == (2, 2, 1)
    # 104 (10908 за 20.11, 13:00) в часы не попадает.
    assert hours[('2026-11-20', 13)]['chats'] == 0
    # Окно времени суток режет по началу чата у обеих половин.
    morning = billing_chat.get_chat_billing_report(db, date(2026, 11, 17), DAY,
                                                   minute_from=0, minute_to=10 * 60 + 30)
    assert morning['totals']['chats'] == 1

    with db._get_cursor() as cur:
        base = billing_chat._chat_billing_plan_base_tx(cur, date(2026, 11, 27), date(2026, 11, 27), 0, 1439)
        fact = billing_chat._chat_billing_fact_staff_tx(cur, DAY, DAY)
    # План 27.11 — факт 20.11: два чата Syntony, без второй копии 10908 (иначе завышался и план).
    assert base == {'2026-11-27': 2}
    assert {(row[1], row[2]) for row in fact} >= {(14, 1), (14, 2)}


def test_test_number_added_later_leaves_billing(db):
    _seed(db)
    _chat2desk_tables(db)
    with mock.patch.dict(os.environ, KEY_ENV):
        billing.build_day(db, _ctx(), DAY, now=NOW)
    from test_numbers import keys as test_keys
    with db._get_cursor() as cur:
        cur.execute('INSERT INTO test_phone_numbers VALUES (%s)', (test_keys.phone_key('77010000002'),))
    report = billing_chat.get_chat_billing_report(db, DAY - timedelta(days=1), DAY)
    assert report['totals']['chats'] == 2              # 105 Chat2Desk за 19.11 и c1 Syntony


def test_no_kept_numbers_means_syntony_only_after_the_switch(db):
    _seed(db)
    _chat2desk_tables(db)
    with mock.patch.dict(os.environ, dict(KEY_ENV, CHAT_BILLING_C2D_KEEP_CHANNEL_IDS='')):
        billing.build_day(db, _ctx(), DAY, now=NOW)
        report = billing_chat.get_chat_billing_report(db, date(2026, 11, 17), DAY)
    days = {item['date']: item['totals']['chats'] for item in report['days']}
    assert days == {'2026-11-17': 2, '2026-11-20': 2}


def _add_conversation(db, cid, created, messages, phone):
    with db._get_cursor() as cur:
        store.upsert_conversation(cur, rules.conversation_row(
            api_conversation(cid, created, SZOV_CHANNEL, phone=phone), NOW))
        for mid, moment, kind, author in messages:
            direction = 'inbound' if kind == 'driver' else 'outbound'
            sender = {'driver': 'contact', 'human': 'human', 'ai': 'ai'}[kind]
            store.upsert_message(cur, rules.message_row(
                api_message(mid, cid, moment, direction=direction, sender=sender, author=author),
                NOW, rules.VIA_API))


def test_ai_only_chat_is_no_reply_and_yesterdays_dialog_has_no_first_reply(db):
    _seed(db)
    _chat2desk_tables(db)
    # c3 — водитель в 13:00, ответил только ИИ: «без ответа», но новый — в базе SL.
    _add_conversation(db, 'c3', '2026-11-20T08:00:00Z', [
        ('c3a', '2026-11-20T08:00:00Z', 'driver', None),
        ('c3b', '2026-11-20T08:01:00Z', 'ai', None),
    ], '77010000005')
    # c4 — начат вчера в 17:00, сегодня водитель пишет в 12:00, Бекова отвечает через 3 мин:
    # обслужен, ответ внутри чата 3 мин, а первого ответа у него сегодня нет.
    _add_conversation(db, 'c4', '2026-11-19T12:00:00Z', [
        ('c4a', '2026-11-19T12:00:00Z', 'driver', None),
        ('c4b', '2026-11-19T12:05:00Z', 'human', 'Бекова Динара'),
        ('c4c', '2026-11-20T07:00:00Z', 'driver', None),
        ('c4d', '2026-11-20T07:03:00Z', 'human', 'Бекова Динара'),
    ], '77010000006')
    with mock.patch.dict(os.environ, dict(KEY_ENV, CHAT_BILLING_C2D_KEEP_CHANNEL_IDS='')):
        billing.build_day(db, _ctx(), DAY, now=NOW)
        report = billing_chat.get_chat_billing_report(db, DAY, DAY, sl_seconds=120)
        people = billing_chat.get_chat_billing_operators(db, DAY, DAY, sl_seconds=120)
    rows = {row['conversation_id']: row for row in _rows(db, 'syntony_chat_billing_chats')}
    assert (rows['c3']['is_new'], rows['c3']['answered'], rows['c3']['first_reply_seconds']) == (
        True, False, None)
    assert (rows['c4']['is_new'], rows['c4']['answered'], rows['c4']['first_reply_seconds'],
            rows['c4']['inner_reply_seconds']) == (False, True, None, 180)
    assert report['totals'] == {
        'chats': 4, 'answered': 3, 'no_reply': 1, 'answered_sl': 1, 'first_reply_seconds': 660,
        'first_replied': 2, 'sl_base': 3, 'inner_reply_seconds': 360, 'inner_replied': 2}
    # Бековой засчитаны оба чата, где она отвечала сегодня; первый ответ — только по c1.
    bekova = next(item for item in people['operators'] if item['operator'] == 'Бекова Динара')
    assert (bekova['chats'], bekova['first_replied'], bekova['first_reply_seconds']) == (2, 1, 120)


def test_day_is_not_complete_while_the_feed_is_behind(db):
    # Сторож встал в 14:55 20.11, а сутки собираются 21.11 в 13:00 — запас прошёл, но лента
    # дальше не дочитана: сутки остаются «не собраны целиком» и будут пересобраны.
    _seed(db)
    later = utc('2026-11-21T08:00:00Z')
    with mock.patch.dict(os.environ, KEY_ENV):
        assert billing.build_day(db, _ctx(), DAY, now=later)['complete'] is False
        with db._get_cursor() as cur:
            _mark(cur, sync.MARK_FEED, later - sync.OVERLAP, written_at=later)
            _mark(cur, sync.MARK_CONVERSATIONS, later - sync.OVERLAP, written_at=later)
        assert billing.build_day(db, _ctx(), DAY, now=later)['complete'] is True

