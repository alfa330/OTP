# -*- coding: utf-8 -*-
"""Статусы операторов Syntony (/operator-statuses, 24.11.2026): разбор интервала, правила
присутствия и шаг сторожа на настоящем Postgres (SYNTONY_CHATS_TEST_PORT) с подставной Syntony.

Форма ответа — по документу Syntony, присланному владельцем 24.11.2026 (живого метода в тот
день ещё не было: 404). Люди, id и парки выдуманы.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from syntony_chats import client, statuses, store, sync  # noqa: E402
from test_syntony_chats_sync import ENV, FakeSyntony, count, db, needs_pg  # noqa: E402,F401

UTC = timezone.utc
NOW = datetime(2025, 11, 17, 10, 0, tzinfo=UTC)        # 15:00 Алматы


def interval(number, key, started, ended=None, *, name='Анна Иванова', partner=('101',),
             operator='op-1', park='Центр регистрации', label=None):
    return {
        'id': 'st-%04d' % number,
        'operator': {'id': operator, 'fullName': name, 'partnerUserIds': list(partner)},
        'park': {'id': 'park-' + park, 'name': park},
        'status': {'key': key, 'label': label or statuses.STATUS_LABELS.get(key, key)},
        'startedAt': started,
        'endedAt': ended,
        'durationSeconds': 60,
    }


def parsed(*items):
    rows = [statuses.interval_row(item, NOW) for item in items]
    return [statuses.Interval(id=row['id'], operator_id=row['operator_id'],
                              operator_name=row['operator_name'],
                              partner_ids=tuple(row['partner_user_ids']), park_name=row['park_name'],
                              key=row['status_key'], label=row['status_label'],
                              started_at=row['started_at'], ended_at=row['ended_at'])
            for row in rows]


ROSTER = [{'id': 101, 'name': 'Иванова Анна', 'status': 'active'},
          {'id': 102, 'name': 'Петров Пётр', 'status': 'active'}]


def resolve(name):
    return {'Пётр Петров': ROSTER[1]}.get(name)


MATCH = statuses.matcher(ROSTER, resolve)


# ── Разбор ────────────────────────────────────────────────────────────────────

def test_interval_row_takes_documented_fields_and_keeps_unknown_in_extra():
    item = interval(1, 'available', '2025-11-17T03:58:12Z', '2025-11-17T06:10:40Z')
    item['reason'] = 'новое поле'
    row = statuses.interval_row(item, NOW)
    assert row['id'] == 'st-0001'
    assert (row['operator_id'], row['operator_name'], row['partner_user_ids']) == (
        'op-1', 'Анна Иванова', ['101'])
    assert (row['status_key'], row['status_label'], row['park_name']) == (
        'available', 'Онлайн', 'Центр регистрации')
    assert row['started_at'] == datetime(2025, 11, 17, 3, 58, 12, tzinfo=UTC)
    assert row['ended_at'] == datetime(2025, 11, 17, 6, 10, 40, tzinfo=UTC)
    assert row['extra'] == {'reason': 'новое поле'}


def test_interval_without_id_or_start_is_skipped_and_null_end_is_open():
    assert statuses.interval_row({'startedAt': '2025-11-17T03:58:12Z'}, NOW) is None
    assert statuses.interval_row({'id': 'x'}, NOW) is None
    assert statuses.interval_row('мусор', NOW) is None
    row = statuses.interval_row(interval(2, 'BREAK', '2025-11-17T03:58:12Z'), NOW)
    assert row['ended_at'] is None and row['status_key'] == 'break'


def test_client_sends_period_and_cursor():
    fake = FakeSyntony()
    fake.status_pages = {None: ([], None)}
    config = client.load_config(ENV)
    client.operator_statuses_page(config, NOW - timedelta(hours=1), NOW, 'cur-2', 500, session=fake)
    [call] = fake.calls
    assert call['path'] == '/operator-statuses'
    assert call['params'] == {'from': '2025-11-17T09:00:00Z', 'to': '2025-11-17T10:00:00Z',
                              'limit': 500, 'cursor': 'cur-2'}
    assert call['allow_redirects'] is False


# ── Присутствие ───────────────────────────────────────────────────────────────

def test_partner_id_finds_operator_and_name_is_fallback():
    by_partner, by_name, stranger = parsed(
        interval(1, 'available', '2025-11-17T09:00:00Z', name='Иное имя в Syntony'),
        interval(2, 'available', '2025-11-17T09:00:00Z', name='Пётр Петров', partner=()),
        interval(3, 'available', '2025-11-17T09:00:00Z', name='Чужой Оператор', partner=('999',)))
    assert MATCH(by_partner)['id'] == 101
    assert MATCH(by_name)['id'] == 102
    assert MATCH(stranger) is None


def test_current_status_prefers_online_in_any_park_and_ignores_closed():
    items = parsed(
        interval(1, 'break', '2025-11-17T09:30:00Z', park='Таксопарк Честный'),
        interval(2, 'available', '2025-11-17T09:40:00Z', park='Центр регистрации'),
        interval(3, 'training', '2025-11-17T08:00:00Z', '2025-11-17T09:00:00Z',
                 name='Пётр Петров', partner=()),
        interval(4, 'busy', '2025-11-17T09:50:00Z', name='Пётр Петров', partner=(),
                 label='Занят звонком'))
    now = statuses.current(items, MATCH, NOW)
    assert now[101]['key'] == 'available' and now[101]['label'] == 'Онлайн'
    assert now[101]['since'] == datetime(2025, 11, 17, 9, 40, tzinfo=UTC)
    # Подпись Syntony — как есть; закрытый интервал тренинга не текущий.
    assert now[102]['key'] == 'busy' and now[102]['label'] == 'Занят звонком'


def test_no_open_interval_means_not_in_system_since_last_end():
    items = parsed(interval(1, 'available', '2025-11-17T06:00:00Z', '2025-11-17T07:00:00Z'),
                   interval(2, 'break', '2025-11-17T07:00:00Z', '2025-11-17T07:20:00Z'))
    assert statuses.current(items, MATCH, NOW) == {}
    assert statuses.last_seen(items, MATCH) == {101: datetime(2025, 11, 17, 7, 20, tzinfo=UTC)}


def test_only_online_holds_the_line_and_parks_merge_not_add():
    day_start = datetime(2025, 11, 16, 19, 0, tzinfo=UTC)               # 00:00 Алматы 17.11
    items = parsed(
        # 09:10–09:40 и 09:30–10:20 по Алматы в двух парках — одна линия 09:10–10:20.
        interval(1, 'available', '2025-11-17T04:10:00Z', '2025-11-17T04:40:00Z'),
        interval(2, 'available', '2025-11-17T04:30:00Z', '2025-11-17T05:20:00Z', park='Таксопарк Честный'),
        interval(3, 'break', '2025-11-17T05:20:00Z', '2025-11-17T05:35:00Z'),
        interval(4, 'busy', '2025-11-17T05:35:00Z', '2025-11-17T05:50:00Z'),
        # Открытый онлайн с 14:55 — до «сейчас» (15:00).
        interval(5, 'available', '2025-11-17T09:55:00Z', name='Пётр Петров', partner=()))
    hours = statuses.line_seconds_by_hour(items, MATCH, day_start, 16, until=NOW)
    assert hours[9] == {101: 50 * 60}
    assert hours[10] == {101: 20 * 60}
    assert 11 not in hours                                              # перерыв и «занят» — не линия
    assert hours[14] == {102: 5 * 60}


# ── Сторож ────────────────────────────────────────────────────────────────────

@needs_pg
def test_first_pull_reads_week_back_then_follows_open_interval_until_closed(db):
    fake = FakeSyntony()
    fake.status_pages = {
        None: ([interval(1, 'available', '2025-11-17T03:58:12Z', '2025-11-17T06:10:40Z')], 'p2'),
        'p2': ([interval(2, 'break', '2025-11-17T06:10:40Z')], None),
    }
    summary = sync.run_once(db, environ=ENV, now=NOW, sleep=lambda s: None, session=fake)
    written, pages, closed = summary['statuses']
    assert written == 2 and closed == 0
    status_calls = [c for c in fake.calls if c['path'] == '/operator-statuses']
    # Первый забор — с полуночи Алматы неделей раньше, сутками-срезами до «сейчас».
    assert status_calls[0]['params']['from'] == '2025-11-09T19:00:00Z'
    assert status_calls[-1]['params']['to'] == '2025-11-17T10:00:00Z'
    assert pages == len(status_calls)
    with db._get_cursor() as cur:
        marks = store.get_marks(cur)
    assert marks[statuses.MARK_STATUSES] == NOW
    # «Статусы есть с» — по самому раннему полученному интервалу, а не по началу окна.
    assert marks[statuses.MARK_STATUSES_FROM] == datetime(2025, 11, 17, 3, 58, 12, tzinfo=UTC)

    # Следующий прогон — с открытого перерыва (06:10:40), а не с отметки минус 5 минут: пока
    # интервал открыт, Syntony его переписывает. Перерыв закрылся.
    later = NOW + timedelta(minutes=3)
    fake.calls.clear()
    fake.status_pages = {None: ([interval(2, 'break', '2025-11-17T06:10:40Z', '2025-11-17T09:59:00Z')], None)}
    summary = sync.run_once(db, environ=ENV, now=later, sleep=lambda s: None, session=fake)
    [call] = [c for c in fake.calls if c['path'] == '/operator-statuses']
    assert call['params']['from'] == '2025-11-17T06:10:40Z'
    assert summary['statuses'][0] == 1
    assert db.one("SELECT ended_at FROM syntony_operator_statuses WHERE id = 'st-0002'")[0] == (
        datetime(2025, 11, 17, 9, 59, tzinfo=UTC))
    # Повтор того же ничего не переписывает.
    summary = sync.run_once(db, environ=ENV, now=later + timedelta(minutes=3), sleep=lambda s: None,
                            session=fake)
    assert summary['statuses'][0] == 0


@needs_pg
def test_open_interval_missing_from_read_slice_is_closed(db):
    fake = FakeSyntony()
    fake.status_pages = {None: ([interval(1, 'available', '2025-11-17T09:00:00Z')], None)}
    sync.run_once(db, environ=ENV, now=NOW, sleep=lambda s: None, session=fake)
    # Срез дочитан, в нём есть другие интервалы, а этого нет — человек из статуса ушёл.
    fake.status_pages = {None: ([interval(2, 'break', '2025-11-17T09:58:00Z', name='Пётр Петров',
                                          partner=())], None)}
    later = NOW + timedelta(minutes=3)
    summary = sync.run_once(db, environ=ENV, now=later, sleep=lambda s: None, session=fake)
    assert summary['statuses'][2] == 1
    row = db.one("SELECT ended_at, extra FROM syntony_operator_statuses WHERE id = 'st-0001'")
    assert row[0] == later and row[1] == {'closed_by': 'absent'}


@needs_pg
def test_empty_slice_closes_nothing(db):
    # При живой Syntony интервалы, начатые в срезе, приходят всегда: пустой ответ — сбой, а не
    # «все ушли». Иначе живой онлайн закрылся бы и при фильтре «по началу» не вернулся.
    fake = FakeSyntony()
    fake.status_pages = {None: ([interval(1, 'available', '2025-11-17T09:00:00Z')], None)}
    sync.run_once(db, environ=ENV, now=NOW, sleep=lambda s: None, session=fake)
    fake.status_pages = {None: ([], None)}
    summary = sync.run_once(db, environ=ENV, now=NOW + timedelta(minutes=3), sleep=lambda s: None,
                            session=fake)
    assert summary['statuses'][2] == 0
    assert db.one("SELECT ended_at FROM syntony_operator_statuses WHERE id = 'st-0001'")[0] is None


@needs_pg
def test_history_from_mid_day_does_not_claim_the_whole_week(db):
    # Метод выложили в 14:00 — истории раньше нет. Часы до этого считаются по ответам, а не
    # нулями: «статусы есть с» = первый полученный интервал.
    from syntony_chats import szov
    fake = FakeSyntony()
    fake.status_pages = {None: ([interval(1, 'available', '2025-11-17T09:00:00Z')], None)}
    sync.run_once(db, environ=ENV, now=NOW, sleep=lambda s: None, session=fake)
    with db._get_cursor() as cur:
        state = statuses.state(cur)
    assert state['from'] == datetime(2025, 11, 17, 9, 0, tzinfo=UTC)
    day_start = datetime(2025, 11, 16, 19, 0, tzinfo=UTC)
    assert not szov.statuses_cover(state, day_start, NOW - timedelta(minutes=5))
    assert szov.statuses_cover(state, datetime(2025, 11, 17, 9, 0, tzinfo=UTC), NOW)


@needs_pg
def test_first_pull_without_intervals_dates_statuses_from_now(db):
    fake = FakeSyntony()
    fake.status_pages = {None: ([], None)}
    sync.run_once(db, environ=ENV, now=NOW, sleep=lambda s: None, session=fake)
    with db._get_cursor() as cur:
        assert statuses.state(cur)['from'] == NOW


class _FailingSecondSlice(FakeSyntony):
    """Первый срез отдаёт, второй — сбой Syntony."""

    def get(self, url, params=None, **kwargs):
        if url.endswith('/operator-statuses'):
            self.status_calls = getattr(self, 'status_calls', 0) + 1
            if self.status_calls == 2:
                from test_syntony_chats_sync import _Response
                return _Response(503, {'error': 'messaging_unavailable'})
        return super().get(url, params=params, **kwargs)


@needs_pg
def test_mark_never_goes_back_when_reread_fails_midway(db):
    # Открытый с позавчера интервал: прогон перечитывает его сутками-срезами из прошлого.
    # Сбой на втором срезе не должен уводить отметку «дочитано до» назад — иначе табло
    # до следующего прогона решило бы, что статусов нет.
    with db._get_cursor() as cur:
        statuses.upsert(cur, statuses.interval_row(
            interval(1, 'offline_work', '2025-11-15T08:00:00Z'), NOW))
        store.set_mark(cur, statuses.MARK_STATUSES, NOW - timedelta(minutes=3))
        store.set_mark(cur, statuses.MARK_STATUSES_FROM, NOW - timedelta(days=5))
    fake = _FailingSecondSlice()
    fake.status_pages = {None: ([interval(1, 'offline_work', '2025-11-15T08:00:00Z')], None)}
    summary = sync.run_once(db, environ=ENV, now=NOW, sleep=lambda s: None, session=fake)
    assert summary['statuses_stopped'] == 'messaging_unavailable'
    with db._get_cursor() as cur:
        assert store.get_marks(cur)[statuses.MARK_STATUSES] == NOW - timedelta(minutes=3)


@needs_pg
def test_404_with_any_body_backs_off_for_15_minutes(db):
    from test_syntony_chats_sync import _Response
    fake = FakeSyntony()
    fake.errors.append(('/operator-statuses',
                        _Response(404, {'statusCode': 404, 'message': 'Cannot GET', 'error': 'Not Found'})))
    summary = sync.run_once(db, environ=ENV, now=NOW, sleep=lambda s: None, session=fake)
    assert summary['statuses_stopped'] == 'Not Found'
    with db._get_cursor() as cur:
        assert store.get_marks(cur)[sync.MARK_STATUSES_RETRY] == NOW + statuses.UNAVAILABLE_RETRY


@needs_pg
def test_missing_method_does_not_block_chats_and_is_retried_in_15_minutes(db):
    fake = FakeSyntony()                                                # метода нет — 404
    summary = sync.run_once(db, environ=ENV, now=NOW, sleep=lambda s: None, session=fake)
    assert summary['statuses_stopped'] == 'not_found'
    assert 'feed' in summary and 'conversations' in summary
    fake.calls.clear()
    summary = sync.run_once(db, environ=ENV, now=NOW + timedelta(minutes=3), sleep=lambda s: None,
                            session=fake)
    assert 'statuses' not in summary and 'statuses_stopped' not in summary
    assert '/operator-statuses' not in [c['path'] for c in fake.calls]
    fake.status_pages = {None: ([], None)}
    summary = sync.run_once(db, environ=ENV, now=NOW + timedelta(minutes=16), sleep=lambda s: None,
                            session=fake)
    assert summary['statuses'][0] == 0
    assert count(db, 'syntony_operator_statuses') == 0


@needs_pg
def test_cleanup_drops_old_closed_intervals_only(db):
    with db._get_cursor() as cur:
        for number, started, ended in ((1, '2025-09-01T08:00:00Z', '2025-09-01T09:00:00Z'),
                                       (2, '2025-09-01T08:00:00Z', None),
                                       (3, '2025-11-16T08:00:00Z', '2025-11-16T09:00:00Z')):
            statuses.upsert(cur, statuses.interval_row(interval(number, 'available', started, ended), NOW))
    assert sync.cleanup(db, now=NOW, days=45) == (0, 0, 1)
    assert count(db, 'syntony_operator_statuses') == 2


@needs_pg
def test_window_load_and_state(db):
    with db._get_cursor() as cur:
        for item in (interval(1, 'available', '2025-11-17T04:00:00Z', '2025-11-17T05:00:00Z'),
                     interval(2, 'break', '2025-11-17T05:00:00Z'),
                     interval(3, 'available', '2025-11-15T04:00:00Z', '2025-11-15T05:00:00Z')):
            statuses.upsert(cur, statuses.interval_row(item, NOW))
        loaded = statuses.load_window(cur, datetime(2025, 11, 16, 19, tzinfo=UTC), NOW)
        assert sorted(item.id for item in loaded) == ['st-0001', 'st-0002']
        assert loaded[0].partner_ids == ('101',)
        assert statuses.state(cur) == {'since': None, 'from': None}
        last = statuses.load_last_seen(cur, datetime(2025, 11, 10, tzinfo=UTC))
        assert [(item.operator_id, item.ended_at) for item in last] == [
            ('op-1', datetime(2025, 11, 17, 5, 0, tzinfo=UTC))]
