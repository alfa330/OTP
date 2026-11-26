# -*- coding: utf-8 -*-
"""SZOV-11: показатели чатников СЗоВ из Syntony в «Учёте часов» (syntony_chats/employees.py).

Решения владельца 25.11.2026: чаты и время ответа — как во вкладке «Чатники» биллинга
(Syntony + «Ноль такси» из Chat2Desk), одно число «Время ответа» — первый ответ; статусы —
отрезками дня, часы как у Chat2Desk (работа — только «Онлайн»); сутки без статусов Syntony — по
Chat2Desk. Правила проверяются без базы, запись — на настоящем Postgres
(SYNTONY_CHATS_TEST_PORT, своя схема со случайным именем) настоящими методами сохранения
из db/chat_ratings.py и db/operator_status.py. Люди, номера и парки выдуманы.

    SYNTONY_CHATS_TEST_PORT=55433 PGPASSWORD=… python -m pytest tests/test_syntony_chat_employees.py
"""

import ast
import contextlib
import json
import math
import os
import sys
import uuid
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from syntony_chats import billing, employees, schema, statuses, szov  # noqa: E402
from test_live_status_overlay import _Cursor, _Db as _StatusDbBase, _mixin  # noqa: E402

UTC = timezone.utc
ALMATY = szov.LOCAL_TZ
FIRST_DAY = date(2026, 11, 18)
DAY = date(2026, 11, 20)
NOW = datetime(2026, 11, 25, 0, 0, tzinfo=UTC)          # 05:00 Алматы 25.11


def local(day, hour, minute=0, second=0):
    """Момент по Алматы (с поясом) — так время приходит из Syntony после разбора."""
    return datetime.combine(day, dt_time(hour, minute, second), tzinfo=ALMATY)


def wall(day, hour, minute=0, second=0):
    """То же стенными часами — как оно лежит в отрезках статусов."""
    return datetime.combine(day, dt_time(hour, minute, second))


def interval(number, key, started, ended=None, *, partner=('101',), name='Анна Иванова',
             operator='op-1', park='Центр регистрации', label=None):
    return statuses.Interval(id='st-%04d' % number, operator_id=operator, operator_name=name,
                             partner_ids=tuple(partner), park_name=park, key=key,
                             label=label or statuses.STATUS_LABELS.get(key, key),
                             started_at=started, ended_at=ended)


ROSTER = [{'id': 101, 'name': 'Иванова Анна', 'status': 'active'},
          {'id': 102, 'name': 'Петров Пётр', 'status': 'active'},
          {'id': 103, 'name': 'Сидорова Мария', 'status': 'active'},
          {'id': 105, 'name': 'Ахметов Ержан', 'status': 'active'}]
ROSTER_IDS = {101, 102, 103, 105}


def resolve(name):
    return {'Пётр Петров': ROSTER[1], 'Ержан Ахметов': ROSTER[3]}.get(name)


class Ctx:
    roster = ROSTER
    resolve = staticmethod(resolve)


MATCH = statuses.matcher(ROSTER, resolve)


# ── Ключи статусов и профиль чат-менеджера ────────────────────────────────────

def test_syntony_keys_land_in_chat_manager_profile_as_owner_decided():
    from db import core
    work, brk = core.CHAT_MANAGER_WORK_STATUS_KEYS, core.CHAT_MANAGER_BREAK_STATUS_KEYS
    training, ignored = core.CHAT_MANAGER_TRAINING_STATUS_KEYS, core.CHAT_MANAGER_NON_WORK_STATUS_KEYS
    assert employees.KEY_MAP['available'] in work
    assert employees.KEY_MAP['break'] in brk
    assert employees.KEY_MAP['training'] in training
    # «Занят», «Техническая причина», «Офлайн работа» — видны, но в часы не идут (как у Chat2Desk).
    assert employees.KEY_MAP['busy'] in ignored
    for key in ('busy', 'technical', 'offline_work'):
        mapped = employees.KEY_MAP[key]
        assert mapped not in work | brk | training
    # «Техническая причина» — тот же ключ, что «тех причина» Chat2Desk: оправдывает опоздание.
    assert employees.KEY_MAP['technical'].replace(' ', '') == 'техпричина'
    # Подписи таймлайна — по-русски, как в Syntony.
    assert core.SCHEDULE_STATUS_KEY_LABELS['онлайн'] == 'Онлайн'
    assert core.SCHEDULE_STATUS_KEY_LABELS['офлайн работа'] == 'Офлайн работа'
    assert core.SCHEDULE_STATUS_KEY_LABELS['перерыв'] == 'Перерыв'
    assert core.SCHEDULE_STATUS_KEY_LABELS['занят'] == 'Занят'


def test_frontend_labels_follow_syntony_names():
    source = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
    block = source[source.index('const plannerStatusKeyLabelMap = {'):]
    block = block[:block.index('};')]
    assert "'онлайн': 'Онлайн'" in block
    assert "'офлайн работа': 'Офлайн работа'" in block


def test_unknown_syntony_status_keeps_its_label_and_stays_out_of_hours():
    item = interval(1, 'lunch', local(DAY, 12), label='Обед ')
    assert employees.status_key(item) == 'обед'
    assert employees.status_key(interval(2, None, local(DAY, 12), label='')) == 'статус syntony'
    assert employees.status_key(interval(3, 'AVAILABLE', local(DAY, 12))) == 'онлайн'
    # Длинная подпись режется до нормализации: ключ в базе без пробела на конце, и сверка суток
    # не должна вечно видеть разницу.
    long_label = 'а' * 119 + ' бб'
    key = employees.status_key(interval(4, 'x', local(DAY, 12), label=long_label))
    assert key == 'а' * 119 and key == ' '.join(key.split())


def test_anchor_label_is_known_to_both_sides():
    from db import core
    assert core.SCHEDULE_STATUS_KEY_LABELS[employees.OFFLINE_KEY] == 'Не в сети'
    source = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
    assert "'не в сети': 'Не в сети'" in source
    work = core.CHAT_MANAGER_WORK_STATUS_KEYS | core.CHAT_MANAGER_BREAK_STATUS_KEYS
    assert employees.OFFLINE_KEY not in work | core.CHAT_MANAGER_TRAINING_STATUS_KEYS
    assert employees.OFFLINE_KEY not in core.CHAT_MANAGER_LOGIN_STATUS_KEYS


# ── Сведение интервалов человека ──────────────────────────────────────────────

def test_flatten_clips_to_the_day_and_keeps_gaps():
    start, end = szov.local_day_bounds(DAY)
    items = [interval(1, 'available', local(DAY - timedelta(days=1), 22), local(DAY, 2)),
             interval(2, 'break', local(DAY, 2, 30), local(DAY, 3))]
    assert employees.flatten(items, start, end) == [
        (start, local(DAY, 2), 'онлайн'), (local(DAY, 2, 30), local(DAY, 3), 'перерыв')]


def test_online_in_one_park_wins_over_other_statuses_in_another():
    start, end = szov.local_day_bounds(DAY)
    items = [interval(1, 'available', local(DAY, 9), local(DAY, 13), park='Парк А'),
             interval(2, 'busy', local(DAY, 10), local(DAY, 11), park='Парк Б'),
             interval(3, 'break', local(DAY, 12, 30), local(DAY, 14), park='Парк Б')]
    assert employees.flatten(items, start, end) == [
        (local(DAY, 9), local(DAY, 13), 'онлайн'), (local(DAY, 13), local(DAY, 14), 'перерыв')]


def test_equal_rank_takes_the_earlier_started_and_merges_neighbours():
    start, end = szov.local_day_bounds(DAY)
    items = [interval(1, 'available', local(DAY, 9), local(DAY, 10), park='Парк А'),
             interval(2, 'available', local(DAY, 9, 30), local(DAY, 11), park='Парк Б'),
             interval(3, 'lunch', local(DAY, 11), local(DAY, 12), label='Обед', park='Парк А'),
             interval(4, 'обед', local(DAY, 11, 30), local(DAY, 12), label='Обед', park='Парк Б')]
    assert employees.flatten(items, start, end) == [
        (local(DAY, 9), local(DAY, 11), 'онлайн'), (local(DAY, 11), local(DAY, 12), 'обед')]


def test_open_interval_runs_to_the_end_of_closed_day_and_tiny_pieces_drop():
    start, end = szov.local_day_bounds(DAY)
    items = [interval(1, 'training', local(DAY, 23, 0), None),
             interval(2, 'break', local(DAY, 8, 0, 0), local(DAY, 8, 0, 0) + timedelta(milliseconds=400))]
    assert employees.flatten(items, start, end) == [(local(DAY, 23), end, 'тренинг')]


def test_day_segments_are_wall_time_only_roster_and_matched_like_the_wallboard():
    items = [interval(1, 'available', local(DAY, 9), local(DAY, 12)),
             interval(2, 'available', local(DAY, 20), None, partner=(), name='Пётр Петров',
                      operator='op-2'),
             interval(3, 'available', local(DAY, 9), local(DAY, 10), partner=('999',),
                      name='Чужой Человек', operator='op-9')]
    segments = employees.day_segments(items, MATCH, DAY, ROSTER_IDS)
    assert segments == {
        101: [(wall(DAY, 9), wall(DAY, 12), 'онлайн')],
        102: [(wall(DAY, 20), datetime.combine(DAY + timedelta(days=1), dt_time(0)), 'онлайн')],
    }
    # Не чатник из справочника — не пишется, даже если сопоставился.
    assert employees.day_segments(items, MATCH, DAY, {102}) == {102: segments[102]}


def test_import_rows_look_like_binotel_intervals_plus_offline_anchor():
    midnight = datetime.combine(DAY + timedelta(days=1), dt_time(0))
    events, segments = employees.import_rows(
        DAY, {101: [(wall(DAY, 9), wall(DAY, 12), 'онлайн')],
              102: [(wall(DAY, 22), midnight, 'онлайн')]}, cleared=[103, 101])
    assert events == [
        {'operator_id': 101, 'event_at': wall(DAY, 9), 'status_key': 'онлайн',
         'state_note': '', 'event_kind': 'status'},
        # Опора «не в сети» — обычное событие в конце последнего интервала суток.
        {'operator_id': 101, 'event_at': wall(DAY, 12), 'status_key': 'не в сети',
         'state_note': '', 'event_kind': 'status', 'is_authoritative': False},
        # До полуночи онлайн — опоры нет: человек ещё в сети, следующие сутки начнутся с него.
        {'operator_id': 102, 'event_at': wall(DAY, 22), 'status_key': 'онлайн',
         'state_note': '', 'event_kind': 'status'},
        # Снятому — опора в начале суток.
        {'operator_id': 103, 'event_at': wall(DAY, 0), 'status_key': 'не в сети',
         'state_note': '', 'event_kind': 'status', 'is_authoritative': False},
    ]
    assert segments[0] == {'operator_id': 101, 'status_date': '2026-11-20', 'start_at': wall(DAY, 9),
                           'end_at': wall(DAY, 12), 'duration_sec': 3 * 3600, 'status_key': 'онлайн',
                           'state_note': ''}
    assert len(segments) == 2


def test_statuses_go_only_to_chat_managers_on_that_date():
    class Db:
        def get_operator_calculation_models_as_of(self, operator_ids, as_of):
            assert as_of == DAY
            return {101: 'chat_manager', 102: 'operator', 103: None}
    assert employees.chat_manager_ids_on(Db(), {101, 102, 103, 105}, DAY) == {101}
    assert employees.chat_manager_ids_on(Db(), set(), DAY) == set()


def test_status_day_changes_rewrite_only_what_differs():
    same = [(wall(DAY, 9), wall(DAY, 12), 'онлайн')]
    target = {101: same, 102: [(wall(DAY, 20), wall(DAY, 21), 'онлайн')], 105: same}
    actual = {101: list(same), 102: [(wall(DAY, 20), wall(DAY, 22), 'online')], 103: same,
              104: same, 105: [(wall(DAY, 9), wall(DAY, 10), 'готов')], 106: same}
    replace, clear = employees.status_day_changes(target, actual, written={101, 104, 106},
                                                  live={105, 106})
    assert replace == [102]          # 101 уже такой, 105 с живым телефоном не трогаем
    # 104 — свои прежние отрезки Syntony, которых Syntony больше не отдаёт: снимаются;
    # 103 — Chat2Desk без интервалов Syntony: остаётся как есть; 106 — с телефоном.
    assert clear == [104]


# ── Чаты и время ответа ───────────────────────────────────────────────────────

ALL = employees.FIELDS_ALL
CHATS = employees.FIELDS_CHATS


def test_first_write_replaces_chat2desk_numbers_then_only_changes_are_written():
    totals = {101: {'chats': 5, 'first_sum': 270.0, 'first_n': 3}}
    # Первый раз: 101 — Syntony; 103 — строка Chat2Desk без данных Syntony обнуляется;
    # 104 — не чатник СЗоВ, не трогаем; 106 — ни строки, ни данных — писать нечего.
    writes = employees.metric_writes(totals, {}, {101, 103, 104}, ROSTER_IDS | {106})
    assert writes == [(101, totals[101], ALL), (103, employees.ZERO, ALL)]
    marks = {operator_id: item for operator_id, item, _fields in writes}
    assert employees.metric_writes(totals, marks, {101, 103}, ROSTER_IDS) == []
    changed = {101: {'chats': 6, 'first_sum': 270.0, 'first_n': 3}}
    assert employees.metric_writes(changed, marks, {101, 103}, ROSTER_IDS) == [
        (101, changed[101], ALL)]
    # Ушли данные (пересборка суток) — пишется ноль.
    assert employees.metric_writes({}, {101: totals[101]}, {101}, ROSTER_IDS) == [
        (101, employees.ZERO, ALL)]


def test_manual_reply_time_without_mark_keeps_its_time_only_chats_are_written():
    totals = {101: {'chats': 5, 'first_sum': 270.0, 'first_n': 3}}
    existing = {101: True, 103: True, 102: False}
    assert employees.metric_writes(totals, {}, existing, ROSTER_IDS) == [
        (101, totals[101], CHATS), (102, employees.ZERO, ALL), (103, employees.ZERO, CHATS)]
    # Есть отметка — правило прежнее: поменялись данные — переписываются обе колонки.
    marks = {101: {'chats': 4, 'first_sum': 270.0, 'first_n': 3}}
    assert employees.metric_writes(totals, marks, existing, {101}) == [(101, totals[101], ALL)]


def test_metric_item_carries_reply_time_key_only_when_it_is_written():
    item = employees.metric_item(DAY, 101, {'chats': 5, 'first_sum': 270.0, 'first_n': 3})
    assert item == {'operator_id': 101, 'day': '2026-11-20', 'chats_count': 5,
                    'avg_response_time_seconds': 90.0, 'source': 'syntony', 'first_reply_count': 3}
    empty = employees.metric_item(DAY, 103, employees.ZERO)
    assert 'avg_response_time_seconds' in empty and empty['avg_response_time_seconds'] is None
    chats_only = employees.metric_item(DAY, 101, {'chats': 5, 'first_sum': 270.0, 'first_n': 3}, CHATS)
    assert 'avg_response_time_seconds' not in chats_only and chats_only['manual_reply_time_kept']
    assert employees.average_reply({'first_sum': 100.0, 'first_n': 3}) == 33.33


# Решение владельца 26.11.2026: первый ответ у Syntony — по дню начала диалога, чат — по дню ответа.
# «Ответил назавтра» давало строку «0 чатов, время ответа 25 ч», а месячное время ответа — простое
# среднее дневных — росло в разы (24.11: 530 → 4242 с). Сутки без чатов во время ответа не идут.
LATE = {'chats': 0, 'first_sum': 90074.0, 'first_n': 1}


def test_day_without_chats_gets_no_reply_time_and_no_new_row():
    assert employees.day_reply_time(LATE) is None
    assert employees.day_reply_time({'chats': 2, 'first_sum': 90.0, 'first_n': 2}) == 45.0
    assert employees.day_reply_time(employees.ZERO) is None
    assert employees.metric_item(DAY, 102, LATE)['avg_response_time_seconds'] is None
    # Строки нет и чатов нет — заводить её ради времени ответа незачем.
    assert employees.metric_writes({102: LATE}, {}, {}, ROSTER_IDS) == []
    # Строка есть (Chat2Desk) — заменяется как раньше: чатов 0, времени ответа нет.
    assert employees.metric_writes({102: LATE}, {}, {102: False}, ROSTER_IDS) == [(102, LATE, ALL)]


def test_reply_time_written_before_the_rule_is_cleared_once():
    marks = {102: dict(LATE)}
    # Итоги те же, но строка ещё держит время ответа при нуле чатов — переписать, один раз.
    assert employees.metric_writes({102: LATE}, marks, {102: False}, ROSTER_IDS, {102}) == [
        (102, LATE, ALL)]
    assert employees.metric_writes({102: LATE}, marks, {102: False}, ROSTER_IDS) == []
    # Не чатник СЗоВ — не трогается и так.
    assert employees.metric_writes({102: LATE}, marks, {102: False}, {101}, {102}) == []


def test_unmatched_replies_are_matched_again_by_todays_roster():
    totals = {(DAY, 101): {'chats': 2, 'first_sum': 60.0, 'first_n': 1}}
    unmatched = {(DAY, 'Пётр Петров'): {'chats': 3, 'first_sum': 90.0, 'first_n': 2},
                 (DAY, 'Салтанат'): {'chats': 9, 'first_sum': 10.0, 'first_n': 1}}
    employees.merge_unmatched(totals, unmatched, resolve, ROSTER_IDS)
    assert totals == {(DAY, 101): {'chats': 2, 'first_sum': 60.0, 'first_n': 1},
                      (DAY, 102): {'chats': 3, 'first_sum': 90.0, 'first_n': 2}}
    # Не чатник СЗоВ — не прибавляется, ZERO не портится.
    employees.merge_unmatched(totals, unmatched, resolve, {101})
    assert (DAY, 102) in totals and employees.ZERO == {'chats': 0, 'first_sum': 0.0, 'first_n': 0}


def test_chat2desk_skips_everybody_when_the_roster_failed():
    metrics = [{'operator_id': 101}, {'operator_id': '999'}, {'operator_id': None}]
    assert employees.c2d_metric_owners({101}, metrics) == {101}
    assert employees.c2d_metric_owners(None, metrics) == {101, 999}
    assert employees.c2d_metric_owners(set(), []) == set()


def test_chat2desk_daily_metrics_keep_only_other_columns_for_chat_managers():
    metrics = [{'operator_id': 101, 'chats_count': 70}, {'operator_id': 999, 'chats_count': 4}]
    fields = ['avg_response_time_seconds', 'avg_score', 'chats_count', 'score_count', 'score_sum']
    batches = employees.split_c2d_daily_metrics(metrics, fields, ROSTER_IDS)
    assert batches == [([metrics[1]], fields), ([metrics[0]], ['avg_score', 'score_count', 'score_sum'])]
    # Только чаты и время ответа — чатникам писать нечего.
    assert employees.split_c2d_daily_metrics(metrics[:1], ['chats_count', 'avg_response_time_seconds'],
                                             ROSTER_IDS) == []


def test_chat2desk_status_rows_of_chat_managers_drop_on_covered_days():
    rows = [{'operator_id': 101}, {'operator_id': '102'}, {'operator_id': 999}, 'мусор']
    assert employees.drop_roster_rows(rows, ROSTER_IDS) == [{'operator_id': 999}, 'мусор']


def test_covered_days_need_statuses_from_the_start_to_the_end_of_the_day():
    state = {'since': (local(DAY + timedelta(days=1), 3), NOW), 'from': local(DAY, 0, 10)}
    days = [DAY - timedelta(days=1), DAY, DAY + timedelta(days=1)]
    assert employees.covered_days(state, days, first_day=FIRST_DAY) == {DAY}
    assert employees.covered_days(state, days, first_day=DAY + timedelta(days=1)) == set()
    assert employees.covered_days({'since': None, 'from': None}, days, first_day=FIRST_DAY) == set()


def test_first_status_pull_reaches_back_to_the_cutover():
    def day_start(moment):
        return datetime.combine(moment.astimezone(ALMATY).date(), dt_time(0), tzinfo=ALMATY)
    late = datetime(2026, 11, 28, 6, 0, tzinfo=UTC)
    # За сутки до перехода — ради ночных смен с 17 на 18.11.
    assert statuses.first_start(late, day_start, environ={}) == local(FIRST_DAY - timedelta(days=1), 0)
    early = datetime(2026, 11, 20, 6, 0, tzinfo=UTC)
    assert statuses.first_start(early, day_start, environ={}) == day_start(early) - timedelta(days=7)
    assert statuses.first_start(late, day_start, environ={'CHAT_BILLING_SYNTONY_FROM': '2026-11-26'}) \
        == day_start(late) - timedelta(days=7)
    far = datetime(2027, 3, 1, 6, 0, tzinfo=UTC)
    assert statuses.first_start(far, day_start, environ={}) == far - statuses.MAX_AGE


def test_billing_restaffs_complete_reply_days_once_statuses_cover_them():
    journal = {FIRST_DAY + timedelta(days=offset): {'complete': True, 'staff_source': 'status'}
               for offset in range(7)}                                  # 18–24.11 собраны
    journal[DAY] = {'complete': True, 'staff_source': 'replies'}
    journal[DAY + timedelta(days=2)] = {'complete': True, 'staff_source': 'replies'}
    journal[date(2026, 11, 25)] = {'complete': False, 'staff_source': 'replies'}   # сегодня
    state = {'since': (NOW, NOW), 'from': local(DAY, 0)}
    restaff = billing.restaff_days(journal, state, now=NOW, keep_days=30)
    assert restaff == {DAY, DAY + timedelta(days=2)}
    assert billing.restaff_days(journal, {'since': (NOW, NOW), 'from': local(DAY, 12)}, now=NOW,
                                keep_days=30) == {DAY + timedelta(days=2)}
    # Переписка хранится 4 дня — сутки 20.11 пересобрать уже не по чему.
    assert billing.restaff_days(journal, state, now=NOW, keep_days=4) == {DAY + timedelta(days=2)}
    assert billing.days_to_build(journal, first_day=FIRST_DAY, now=NOW, restaff=restaff) == [
        date(2026, 11, 25), DAY + timedelta(days=2), DAY]
    # Без статусов — как раньше: собранные сутки не пересобираются.
    assert billing.days_to_build(journal, first_day=FIRST_DAY, now=NOW) == [date(2026, 11, 25)]


# ── Общее сохранение статусов: «снять сутки» без новых строк ──────────────────

def test_status_import_clears_listed_days_even_without_new_rows():
    calls = []

    class Db(_mixin({'execute_values': lambda *_a, **_k: None, 'json': json, 'uuid': uuid}),
             _StatusDbBase):
        def _get_cursor(self):
            @contextlib.contextmanager
            def manager():
                yield self.cursor
            return manager()

        def _recalculate_auto_daily_hours_tx(self, **kwargs):
            calls.append(kwargs)
            return {}

    db = Db()
    db.cursor = _Cursor([])
    db.save_operator_status_import(events=[], segments=[], keep_live_events=True,
                                   summary={'meta': {'segments_authoritative': True}},
                                   clear_operator_days=[(103, DAY), ('x', DAY), (104, '2026-11-21')])
    deletes = [(q, p) for q, p in db.cursor.queries if q.startswith('DELETE FROM')]
    assert ('DELETE FROM operator_status_events WHERE operator_id = %s AND event_date >= %s '
            'AND event_date <= %s AND client_event_id IS NULL', (103, DAY, DAY)) in deletes
    assert ('DELETE FROM operator_status_segments WHERE operator_id = %s AND status_date >= %s '
            'AND status_date <= %s', (104, date(2026, 11, 21), date(2026, 11, 21))) in deletes
    assert len(deletes) == 4
    assert calls and calls[0]['operator_ids'] == [103, 104]


# ── На настоящем Postgres ─────────────────────────────────────────────────────

PORT = os.environ.get('SYNTONY_CHATS_TEST_PORT')
needs_pg = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (SYNTONY_CHATS_TEST_PORT)')

BASE_DDL = """
    CREATE TABLE test_phone_numbers (id SERIAL PRIMARY KEY, phone_key VARCHAR(10) NOT NULL UNIQUE);
    CREATE TABLE c2d_requests (
        request_id BIGINT PRIMARY KEY, day DATE, request_start TIMESTAMP, request_end TIMESTAMP,
        request_type TEXT, channel_id INTEGER, channel_name TEXT, transport TEXT, client_name TEXT,
        client_phone TEXT, assigned_phone TEXT, operator_id INTEGER, c2d_operator_name TEXT,
        reaction_time INTEGER, average_replies_time DOUBLE PRECISION, rating_score DOUBLE PRECISION,
        incoming_messages INTEGER, outgoing_messages INTEGER);
    CREATE TABLE chat_manager_daily_metrics (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(), operator_id INTEGER NOT NULL, day DATE NOT NULL,
        chats_count INTEGER NOT NULL DEFAULT 0, avg_score FLOAT, avg_response_time_seconds FLOAT,
        transfer_chat_count INTEGER NOT NULL DEFAULT 0, imported_by INTEGER, source_batch_id UUID,
        raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb, score_sum FLOAT, score_count INTEGER,
        whatsapp_chats_count INTEGER NOT NULL DEFAULT 0,
        name_requests_chats_count INTEGER NOT NULL DEFAULT 0, raw_score_sum FLOAT,
        raw_score_count INTEGER, created_at TIMESTAMP NOT NULL DEFAULT now(),
        updated_at TIMESTAMP NOT NULL DEFAULT now(), UNIQUE (operator_id, day));
    CREATE TABLE operator_status_events (
        id BIGSERIAL PRIMARY KEY, operator_id INTEGER NOT NULL, event_at TIMESTAMP NOT NULL,
        event_date DATE NOT NULL, status_key VARCHAR(128) NOT NULL, state_note VARCHAR(255),
        event_kind VARCHAR(16) NOT NULL DEFAULT 'status', imported_by INTEGER,
        is_authoritative BOOLEAN NOT NULL DEFAULT FALSE, client_event_id TEXT);
    CREATE TABLE operator_status_segments (
        id BIGSERIAL PRIMARY KEY, operator_id INTEGER NOT NULL, status_date DATE NOT NULL,
        start_at TIMESTAMP NOT NULL, end_at TIMESTAMP NOT NULL, duration_sec INTEGER NOT NULL,
        status_key VARCHAR(128) NOT NULL, state_note VARCHAR(255), imported_by INTEGER,
        is_authoritative BOOLEAN NOT NULL DEFAULT FALSE,
        CHECK (end_at > start_at), CHECK (duration_sec > 0));
    CREATE TABLE operator_status_import_batches (
        id UUID PRIMARY KEY, imported_by INTEGER, source_rows INTEGER, valid_events INTEGER,
        matched_events INTEGER, segments_saved INTEGER, deleted_events INTEGER,
        deleted_segments INTEGER, invalid_rows_count INTEGER, parse_errors_count INTEGER,
        operators_count INTEGER, open_tail_events INTEGER, zero_or_negative_transitions INTEGER,
        date_from DATE, date_to DATE, meta_json JSONB);
"""


def _real_metrics_save_class():
    """Класс с НАСТОЯЩИМ save_chat_manager_daily_metrics из db/chat_ratings.py."""
    from psycopg2.extras import Json, execute_values
    path = ROOT / 'db' / 'chat_ratings.py'
    source = path.read_text(encoding='utf-8-sig')
    module = ast.parse(source)
    mixin = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'ChatRatingsMixin')
    method = next(n for n in mixin.body
                  if isinstance(n, ast.FunctionDef) and n.name == 'save_chat_manager_daily_metrics')
    cls = ast.ClassDef(name='MetricsSave', bases=[], keywords=[], body=[method], decorator_list=[])
    namespace = {'Json': Json, 'execute_values': execute_values, 'math': math, 'uuid': uuid,
                 'datetime': datetime, 'date': date}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(path), 'exec'),
         namespace)
    return namespace['MetricsSave']


def _real_status_save_class():
    from psycopg2.extras import execute_values
    return _mixin({'execute_values': execute_values, 'json': json, 'uuid': uuid})


def _real_clip_class():
    """Настоящая обрезка пересборки по готовым интервалам (db/resource_fte.py) — у базы
    test_live_status_overlay она заглушена, а находка «хвост Chat2Desk в сутках Syntony» живёт
    именно в ней."""
    path = ROOT / 'db' / 'resource_fte.py'
    source = path.read_text(encoding='utf-8-sig')
    module = ast.parse(source)
    mixin = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'ResourceFteMixin')
    method = next(n for n in mixin.body
                  if isinstance(n, ast.FunctionDef) and n.name == '_clip_part_after_authoritative')
    cls = ast.ClassDef(name='RealClip', bases=[], keywords=[], body=[method], decorator_list=[])
    namespace = {'datetime': datetime}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(path), 'exec'),
         namespace)
    return namespace['RealClip']


class PgDb(_real_metrics_save_class(), _real_clip_class(), _real_status_save_class(), _StatusDbBase):
    """Настоящие методы сохранения и пересборки поверх своей схемы; пересчёт часов и месяца —
    записью вызова; модель расчёта на дату — чат-менеджер у всех, кроме not_chat."""

    def __init__(self, name):
        self.name = name
        self.recalculated = []
        self.not_chat = set()

    def get_operator_calculation_models_as_of(self, operator_ids, as_of):
        return {int(operator_id): ('operator' if int(operator_id) in self.not_chat else 'chat_manager')
                for operator_id in operator_ids}

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

    def _recalculate_chat_manager_score_adjustments_tx(self, cursor, pairs):
        return {}

    def _aggregate_month_from_daily_tx(self, cursor, operator_id, month_key):
        return {}

    def _recalculate_auto_daily_hours_tx(self, cursor=None, operator_ids=None, start_date=None,
                                         end_date=None, **_kwargs):
        self.recalculated.append((sorted(operator_ids or []), start_date, end_date))
        return {'updated_days': 0, 'aggregated_months': 0}

    def rows(self, sql, params=()):
        with self._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def run(self, sql, params=()):
        with self._get_cursor() as cur:
            cur.execute(sql, params)


@pytest.fixture
def pg():
    if not PORT:
        pytest.skip('needs local PostgreSQL (SYNTONY_CHATS_TEST_PORT)')
    name = 'szov11_' + uuid.uuid4().hex[:10]
    db = PgDb(name)
    import psycopg2
    admin = psycopg2.connect(host='127.0.0.1', port=int(PORT),
                             user=os.environ.get('SYNTONY_CHATS_TEST_USER', 'postgres'),
                             dbname='postgres', connect_timeout=3)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + name)
    try:
        with db._get_cursor() as cur:
            schema.init_syntony_chats_schema(cur)
            cur.execute(BASE_DDL)
        yield db
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + name + ' CASCADE')
        admin.close()


def _seed_billing(db):
    db.run("""
        INSERT INTO syntony_chat_billing_days (day, complete, chats, staff_source, error) VALUES
            ('2026-11-20', TRUE, 3, 'replies', NULL), ('2026-11-21', FALSE, 0, NULL, 'нет данных')
    """)
    db.run("""
        INSERT INTO syntony_chat_billing_operators (day, conversation_id, operator_key, operator_id,
            operator_name, park, started_at, replied, first_reply_seconds) VALUES
            ('2026-11-20', 'c1', 'Иванова Анна', 101, 'Иванова Анна', 'Халык', '2026-11-20 09:00', TRUE, 60),
            ('2026-11-20', 'c2', 'Иванова Анна', 101, 'Иванова Анна', 'Халык', '2026-11-20 10:00', TRUE, NULL),
            ('2026-11-20', 'c3', 'Иванова Анна', 101, 'Иванова Анна', 'Халык', '2026-11-20 11:00', TRUE, 90),
            ('2026-11-20', 'c4', 'Петров Пётр', 102, 'Петров Пётр', 'Халык', '2026-11-20 23:50', FALSE, 30),
            ('2026-11-20', 'c5', 'Салтанат', NULL, 'Салтанат', 'Халык', '2026-11-20 12:00', TRUE, 15),
            ('2026-11-21', 'c6', 'Иванова Анна', 101, 'Иванова Анна', 'Халык', '2026-11-21 09:00', TRUE, 10),
            -- 105 в справочник добавили после сборки суток: ответ лежит без сотрудника.
            ('2026-11-20', 'c8', 'Ержан Ахметов', NULL, 'Ержан Ахметов', 'Халык', '2026-11-20 13:00', TRUE, 40)
    """)
    db.run("INSERT INTO test_phone_numbers (phone_key) VALUES ('7011112233')")
    db.run("""
        INSERT INTO c2d_requests (request_id, day, request_start, request_type, channel_id,
            channel_name, client_phone, operator_id, reaction_time) VALUES
            (1, '2026-11-20', '2026-11-20 09:10', 'common', 10908, 'Ноль такси', '77019990001', 101, 120),
            (2, '2026-11-20', '2026-11-20 09:20', 'common', 10908, 'Ноль такси', '77019990002', 101, NULL),
            (3, '2026-11-20', '2026-11-20 09:30', 'common', 2137, 'Техподдержка iTaxi', '77019990003', 101, 5),
            (4, '2026-11-20', '2026-11-20 09:40', 'common', 10908, 'Ноль такси', '77011112233', 102, 7),
            (5, '2026-11-20', '2026-11-20 09:50', 'common', 10908, 'Ноль такси', '77019990005', 999, 9)
    """)
    db.run("""
        INSERT INTO chat_manager_daily_metrics (operator_id, day, chats_count,
            avg_response_time_seconds, transfer_chat_count, raw_payload) VALUES
            (101, '2026-11-20', 70, 133.73, 5, '{"source": "chat2desk_events"}'),
            (103, '2026-11-20', 12, 300, 1, '{"day": "2026-11-20", "chats_count": 12}'),
            (105, '2026-11-20', 0, 25, 0,
             '{"day": "2026-11-20", "operator_id": 105, "avg_response_time_seconds": 25}'),
            (999, '2026-11-20', 4, 50, 0, '{}')
    """)


def _metrics(db):
    return {row[0]: row[1:] for row in db.rows("""
        SELECT operator_id, chats_count, avg_response_time_seconds, transfer_chat_count
          FROM chat_manager_daily_metrics WHERE day = '2026-11-20' ORDER BY operator_id""")}


@needs_pg
def test_operator_day_totals_are_the_billing_chatters_tab(pg, monkeypatch):
    _seed_billing(pg)
    from resource_fte.chat import chat_billing_operator_day_totals
    # По умолчанию «Ноль такси» 10908 из Chat2Desk — по 19.11 (db67b1e7): 20.11 тот же номер уже
    # в чатах дня Syntony. 101: три диалога Syntony, первый ответ — 60 и 90; 102: чат после
    # полуночи — только первый ответ.
    monkeypatch.delenv('CHAT_BILLING_C2D_KEEP_CHANNEL_IDS', raising=False)
    with pg._get_cursor() as cur:
        totals = chat_billing_operator_day_totals(cur, DAY, DAY, syntony_from=FIRST_DAY)
    assert totals == {
        (DAY, 101): {'chats': 3, 'first_sum': 150.0, 'first_n': 2},
        (DAY, 102): {'chats': 0, 'first_sum': 30.0, 'first_n': 1},
    }
    # Номер, который ещё не переехал (без последнего дня), идёт и после перехода — как во вкладке
    # «Чатники»: 101 — плюс два обращения «Ноль такси» (Техподдержка после 18.11 не берётся),
    # первый ответ — 60, 90 и 120; тестовый номер «Ноль такси» не в счёт; 999 — тоже сотрудник
    # для биллинга, фильтр чатников — дальше.
    monkeypatch.setenv('CHAT_BILLING_C2D_KEEP_CHANNEL_IDS', '10908')
    with pg._get_cursor() as cur:
        totals = chat_billing_operator_day_totals(cur, DAY, DAY, syntony_from=FIRST_DAY)
    assert totals == {
        (DAY, 101): {'chats': 5, 'first_sum': 270.0, 'first_n': 3},
        (DAY, 102): {'chats': 0, 'first_sum': 30.0, 'first_n': 1},
        (DAY, 999): {'chats': 1, 'first_sum': 9.0, 'first_n': 1},
    }
    # Срок номера кончился до этих суток — его обращения не берутся.
    monkeypatch.setenv('CHAT_BILLING_C2D_KEEP_CHANNEL_IDS', '10908:2026-11-19')
    with pg._get_cursor() as cur:
        assert chat_billing_operator_day_totals(cur, DAY, DAY, syntony_from=FIRST_DAY)[(DAY, 101)] == {
            'chats': 3, 'first_sum': 150.0, 'first_n': 2}


@needs_pg
def test_metrics_replace_chat2desk_once_and_keep_manual_edits(pg, monkeypatch):
    _seed_billing(pg)
    monkeypatch.delenv('CHAT_BILLING_C2D_KEEP_CHANNEL_IDS', raising=False)
    first = employees.refresh_metrics(pg, Ctx, first_day=FIRST_DAY, now=NOW)
    assert first == [{'day': '2026-11-20', 'written': 3}]
    metrics = _metrics(pg)
    assert metrics[101] == (3, 75.0, 5)          # переводы Chat2Desk остаются
    # 102 ответил на диалог 23:50 уже после полуночи: чатов в эти сутки нет, первый ответ есть —
    # строка не заводится, время ответа таким суткам не пишется (решение 26.11).
    assert 102 not in metrics
    assert metrics[103] == (0, None, 1)          # цифры Chat2Desk сняты
    # 105: ответ досопоставлен по ФИО нынешним справочником; время ответа СВ поставил руками —
    # оно остаётся, переписаны только чаты.
    assert metrics[105] == (1, 25.0, 0)
    assert metrics[999] == (4, 50.0, 0)          # не чатник СЗоВ — не тронут
    payload = pg.rows("SELECT raw_payload FROM chat_manager_daily_metrics "
                      "WHERE operator_id = 101 AND day = '2026-11-20'")[0][0]
    assert payload['source'] == 'syntony' and payload['first_reply_count'] == 2
    # Сутки с ошибкой биллинга не пишутся вовсе.
    assert pg.rows("SELECT count(*) FROM chat_manager_daily_metrics WHERE day = '2026-11-21'")[0][0] == 0

    assert employees.refresh_metrics(pg, Ctx, first_day=FIRST_DAY, now=NOW) == []
    # Ручная правка СВ живёт, пока данные Syntony те же.
    pg.run("UPDATE chat_manager_daily_metrics SET avg_response_time_seconds = 45 "
           "WHERE operator_id = 101 AND day = '2026-11-20'")
    assert employees.refresh_metrics(pg, Ctx, first_day=FIRST_DAY, now=NOW) == []
    assert _metrics(pg)[101] == (3, 45.0, 5)
    # Данные поменялись — строка переписывается.
    pg.run("""INSERT INTO syntony_chat_billing_operators (day, conversation_id, operator_key,
                  operator_id, operator_name, started_at, replied, first_reply_seconds)
              VALUES ('2026-11-20', 'c7', 'Иванова Анна', 101, 'Иванова Анна', '2026-11-20 15:00', TRUE, 30)""")
    assert employees.refresh_metrics(pg, Ctx, first_day=FIRST_DAY, now=NOW) == [
        {'day': '2026-11-20', 'written': 1}]
    assert _metrics(pg)[101] == (4, 60.0, 5)

    # Откат на Chat2Desk записал 101 свои цифры и снял отметку — вернулась Syntony, переписала.
    pg.run("UPDATE chat_manager_daily_metrics SET chats_count = 70, avg_response_time_seconds = 133.73, "
           "raw_payload = '{\"chats_count\": 70}' WHERE operator_id = 101 AND day = '2026-11-20'")
    assert employees.refresh_metrics(pg, Ctx, first_day=FIRST_DAY, now=NOW) == []
    assert employees.forget_marks(pg, DAY, [101, None, 'x']) == 1
    assert employees.refresh_metrics(pg, Ctx, first_day=FIRST_DAY, now=NOW) == [
        {'day': '2026-11-20', 'written': 1}]
    assert _metrics(pg)[101] == (4, 60.0, 5)
    assert employees.forget_marks(pg, DAY, []) == 0


@needs_pg
def test_reply_time_without_chats_written_before_the_rule_is_cleared(pg, monkeypatch):
    """Строки, которые прогон записал до правила 26.11 (на проде — 24.11 у двоих чатников),
    чинятся следующим прогоном сами, хотя итоги Syntony не менялись; ручная правка СВ стоит."""
    _seed_billing(pg)
    monkeypatch.delenv('CHAT_BILLING_C2D_KEEP_CHANNEL_IDS', raising=False)
    pg.run("""
        INSERT INTO chat_manager_daily_metrics (operator_id, day, chats_count,
            avg_response_time_seconds, transfer_chat_count, raw_payload) VALUES
            (102, '2026-11-20', 0, 30, 0,
             '{"day": "2026-11-20", "operator_id": 102, "chats_count": 0, "source": "syntony",
               "first_reply_count": 1, "avg_response_time_seconds": 30}')
    """)
    pg.run("""INSERT INTO syntony_employee_metric_marks (day, operator_id, chats, first_sum, first_n)
              VALUES ('2026-11-20', 102, 0, 30, 1)""")
    with pg._get_cursor() as cur:
        existing, reply_without_chats = employees._existing_metric_rows(cur, DAY, DAY, ROSTER_IDS)
    # 105 — ручная строка СВ (0 чатов и время ответа): её правило не трогает.
    assert existing[DAY] == {101: False, 102: False, 103: False, 105: True}
    assert reply_without_chats == {DAY: {102}}

    assert employees.refresh_metrics(pg, Ctx, first_day=FIRST_DAY, now=NOW) == [
        {'day': '2026-11-20', 'written': 4}]
    metrics = _metrics(pg)
    assert metrics[102] == (0, None, 0)
    assert metrics[105] == (1, 25.0, 0)
    # Починили — дальше сутки молчат.
    assert employees.refresh_metrics(pg, Ctx, first_day=FIRST_DAY, now=NOW) == []
    assert _metrics(pg)[102] == (0, None, 0)


def _seed_statuses(db):
    db.run("""
        INSERT INTO syntony_chat_sync_marks (name, mark) VALUES
            ('statuses_since', %s), ('statuses_from', %s)
    """, (NOW, local(FIRST_DAY, 0)))
    rows = [
        # 101: два парка, онлайн перекрывает «Занят»; перерыв, онлайн, тренинг.
        ('s1', 'op-1', 'Анна Иванова', ['101'], 'Парк А', 'available', local(DAY, 9), local(DAY, 13)),
        ('s2', 'op-1', 'Анна Иванова', ['101'], 'Парк Б', 'busy', local(DAY, 10), local(DAY, 11)),
        ('s3', 'op-1', 'Анна Иванова', ['101'], 'Парк А', 'break', local(DAY, 13), local(DAY, 13, 30)),
        ('s4', 'op-1', 'Анна Иванова', ['101'], 'Парк А', 'available', local(DAY, 13, 30), local(DAY, 18)),
        ('s5', 'op-1', 'Анна Иванова', ['101'], 'Парк А', 'training', local(DAY, 18), local(DAY, 18, 30)),
        # 102: по ФИО, онлайн с 20:00 до часу ночи — сутки делятся в полночь.
        ('s6', 'op-2', 'Пётр Петров', [], 'Парк А', 'available', local(DAY, 20),
         local(DAY + timedelta(days=1), 1)),
        # Не чатник СЗоВ.
        ('s7', 'op-9', 'Чужой Человек', ['999'], 'Парк А', 'available', local(DAY, 9), local(DAY, 10)),
        # 105 — в Syntony был, но у него в эти сутки телефон iCORE Phone: сутки не трогаем.
        ('s8', 'op-5', 'Ержан Ахметов', ['105'], 'Парк А', 'available', local(DAY, 9), local(DAY, 12)),
    ]
    for row in rows:
        db.run("""
            INSERT INTO syntony_operator_statuses (id, operator_id, operator_name, partner_user_ids,
                park_name, status_key, status_label, started_at, ended_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, row[:6] + (statuses.STATUS_LABELS.get(row[5]),) + row[6:])
    # Сутки по Chat2Desk: у 101 и 103 — выгрузка, у 105 — живой телефон.
    db.run("""
        INSERT INTO operator_status_events (operator_id, event_at, event_date, status_key, client_event_id)
        VALUES (101, '2026-11-20 08:00', '2026-11-20', 'busy', NULL),
               (103, '2026-11-20 09:00', '2026-11-20', 'online', NULL),
               (105, '2026-11-20 09:00', '2026-11-20', 'готов', 'phone-1')
    """)
    db.run("""
        INSERT INTO operator_status_segments (operator_id, status_date, start_at, end_at, duration_sec, status_key)
        VALUES (101, '2026-11-20', '2026-11-20 08:00', '2026-11-20 20:00', 43200, 'busy'),
               (103, '2026-11-20', '2026-11-20 09:00', '2026-11-20 18:00', 32400, 'online'),
               (105, '2026-11-20', '2026-11-20 09:00', '2026-11-20 18:00', 32400, 'готов')
    """)


def _segments(db):
    out = {}
    for operator_id, start_at, end_at, key, authoritative in db.rows("""
            SELECT operator_id, start_at, end_at, status_key, is_authoritative
              FROM operator_status_segments WHERE status_date = '2026-11-20'
             ORDER BY operator_id, start_at"""):
        out.setdefault(operator_id, []).append((start_at, end_at, key, authoritative))
    return out


@needs_pg
def test_statuses_replace_chat2desk_day_and_second_run_is_silent(pg):
    _seed_statuses(pg)
    results = employees.refresh_statuses(pg, Ctx, first_day=FIRST_DAY, now=NOW)
    assert results == [{'day': '2026-11-20', 'replaced': 2, 'cleared': 0, 'segments': 5,
                        'skipped_live': 1},
                       {'day': '2026-11-21', 'replaced': 1, 'cleared': 0, 'segments': 1,
                        'skipped_live': 0}]
    night = pg.rows("SELECT operator_id, start_at, end_at, status_key FROM operator_status_segments "
                    "WHERE status_date = '2026-11-21'")
    assert night == [(102, wall(DAY + timedelta(days=1), 0), wall(DAY + timedelta(days=1), 1), 'онлайн')]
    segments = _segments(pg)
    assert segments[101] == [
        (wall(DAY, 9), wall(DAY, 13), 'онлайн', True),
        (wall(DAY, 13), wall(DAY, 13, 30), 'перерыв', True),
        (wall(DAY, 13, 30), wall(DAY, 18), 'онлайн', True),
        (wall(DAY, 18), wall(DAY, 18, 30), 'тренинг', True),
    ]
    assert segments[102] == [(wall(DAY, 20), datetime.combine(DAY + timedelta(days=1), dt_time(0)),
                              'онлайн', True)]
    # 103 в Syntony за эти сутки нет — его сутки остаются по Chat2Desk.
    assert segments[103] == [(wall(DAY, 9), wall(DAY, 18), 'online', False)]
    assert segments[105] == [(wall(DAY, 9), wall(DAY, 18), 'готов', False)]   # телефон не тронут
    events = pg.rows("SELECT operator_id, status_key, client_event_id FROM operator_status_events "
                     "ORDER BY operator_id, event_at")
    assert (101, 'busy', None) not in events and (103, 'online', None) in events
    assert (105, 'готов', 'phone-1') in events
    # Опоры «не в сети» — обычные события в конце последнего интервала суток (у 102 20.11 онлайн
    # до полуночи — опора только в 01:00 следующих суток).
    anchors = pg.rows("SELECT operator_id, event_at, is_authoritative FROM operator_status_events "
                      "WHERE status_key = 'не в сети' ORDER BY operator_id, event_at")
    assert anchors == [(101, wall(DAY, 18, 30), False), (102, wall(DAY + timedelta(days=1), 1), False)]
    assert pg.rows("SELECT count(*) FROM operator_status_events WHERE status_key = 'онлайн' "
                   "AND NOT is_authoritative")[0][0] == 0
    assert pg.recalculated and pg.recalculated[0][0] == [101, 102]

    assert employees.refresh_statuses(pg, Ctx, first_day=FIRST_DAY, now=NOW) == []
    # Сутки до перехода и незакрытые сегодняшние не переносятся.
    assert employees.refresh_statuses(pg, Ctx, first_day=DAY + timedelta(days=1), now=NOW) == []

    # Syntony перестала отдавать интервал 102 (исправила у себя) — его отрезки Syntony снимаются
    # в обоих сутках, Chat2Desk у него взять неоткуда — сутки пустые.
    pg.run("DELETE FROM syntony_operator_statuses WHERE id = 's6'")
    assert employees.refresh_statuses(pg, Ctx, first_day=FIRST_DAY, now=NOW) == [
        {'day': '2026-11-20', 'replaced': 0, 'cleared': 1, 'segments': 0, 'skipped_live': 1},
        {'day': '2026-11-21', 'replaced': 0, 'cleared': 1, 'segments': 0, 'skipped_live': 0}]
    assert 102 not in _segments(pg)
    assert employees.refresh_statuses(pg, Ctx, first_day=FIRST_DAY, now=NOW) == []


@needs_pg
def test_neighbour_rebuild_does_not_drag_chat2desk_into_syntony_days(pg):
    """Находка разбора 25.11: пересборка по событиям (соседние сутки по Chat2Desk, «Пересчитать»)
    брала опорой давний статус Chat2Desk и тянула его на хвост суток Syntony и начало следующих.
    Здесь — настоящие _rebuild_operator_status_segments_tx и _clip_part_after_authoritative."""
    _seed_statuses(pg)
    employees.refresh_statuses(pg, Ctx, first_day=FIRST_DAY, now=NOW)
    # У 101 Chat2Desk вечером накануне и утром после: 21.11 у него в Syntony ничего нет.
    pg.run("""
        INSERT INTO operator_status_events (operator_id, event_at, event_date, status_key) VALUES
            (101, '2026-11-19 21:00', '2026-11-19', 'online'),
            (101, '2026-11-21 09:00', '2026-11-21', 'online')
    """)
    with pg._get_cursor() as cur:
        pg._rebuild_operator_status_segments_tx(cur, [101], DAY - timedelta(days=1), DAY + timedelta(days=1))
    rows = pg.rows("""SELECT status_date, start_at, end_at, status_key, is_authoritative
                        FROM operator_status_segments WHERE operator_id = 101 ORDER BY start_at""")
    online_not_syntony = [row for row in rows if row[3] == 'online' and row[0] >= DAY]
    assert online_not_syntony == []                       # ни хвоста 20.11, ни начала 21.11
    assert (DAY, wall(DAY, 18, 30), datetime.combine(DAY + timedelta(days=1), dt_time(0)),
            'не в сети', False) in rows                     # после пересборки — «не в сети», не часы
    assert (DAY + timedelta(days=1), wall(DAY + timedelta(days=1), 0), wall(DAY + timedelta(days=1), 9),
            'не в сети', False) in rows
    assert [row for row in rows if row[0] == DAY and row[4]] == [
        (DAY, wall(DAY, 9), wall(DAY, 13), 'онлайн', True),
        (DAY, wall(DAY, 13), wall(DAY, 13, 30), 'перерыв', True),
        (DAY, wall(DAY, 13, 30), wall(DAY, 18), 'онлайн', True),
        (DAY, wall(DAY, 18), wall(DAY, 18, 30), 'тренинг', True)]
    # Следующий прогон возвращает сутки Syntony к их виду, 21.11 у 101 остаётся по Chat2Desk.
    assert employees.refresh_statuses(pg, Ctx, first_day=FIRST_DAY, now=NOW) == [
        {'day': '2026-11-20', 'replaced': 1, 'cleared': 0, 'segments': 4, 'skipped_live': 1}]
    assert [row[:4] for row in pg.rows("""
        SELECT status_date, start_at, end_at, status_key, is_authoritative FROM operator_status_segments
         WHERE operator_id = 101 AND status_date = '2026-11-20' AND NOT is_authoritative""")] == []
    assert employees.refresh_statuses(pg, Ctx, first_day=FIRST_DAY, now=NOW) == []


@needs_pg
def test_statuses_skip_people_without_chat_manager_model_on_that_date(pg):
    _seed_statuses(pg)
    pg.not_chat = {102}
    assert employees.refresh_statuses(pg, Ctx, first_day=FIRST_DAY, now=NOW) == [
        {'day': '2026-11-20', 'replaced': 1, 'cleared': 0, 'segments': 4, 'skipped_live': 1}]
    assert 102 not in _segments(pg)
    assert employees.status_people(pg, Ctx, [DAY, DAY + timedelta(days=1)], first_day=FIRST_DAY) == {
        DAY: {101}}


@needs_pg
def test_status_people_tells_the_chat2desk_import_whom_to_skip(pg):
    _seed_statuses(pg)
    days = [FIRST_DAY - timedelta(days=1), DAY, DAY + timedelta(days=1), date(2026, 11, 24)]
    # 17.11 — до перехода; 24.11 покрыты, но интервалов нет; 105 в Syntony был, но у него в эти
    # сутки живой телефон: Syntony его сутки не переносит — значит, и Chat2Desk за него пишет,
    # как раньше.
    assert employees.status_people(pg, Ctx, days, first_day=FIRST_DAY) == {
        DAY: {101, 102}, DAY + timedelta(days=1): {102}}
    assert employees.status_people(pg, Ctx, [], first_day=FIRST_DAY) == {}
