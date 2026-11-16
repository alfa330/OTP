# -*- coding: utf-8 -*-
"""Журнал сторожа линий Binotel — настоящим SQL.

LINE_WATCH_TEST_PORT — порт локального Postgres (своя схема со случайным именем, в конце
удаляется; ни боевых учёток, ни импорта приложения). Запуск:

    LINE_WATCH_TEST_PORT=55461 python -m pytest tests/test_line_watch_postgres.py

Что сторожится:
* схема ставится повторно без ошибок, журнал видит её по search_path;
* открытый случай у линии и вида один: повторное открытие продолжает строку, закрытый — нет;
* событие звонка одно на звонок, вид и линию: повтор вебхука ничего не добавляет;
* новый процесс поднимает только незакрытые расхождения, события звонков — не его;
* чистка убирает только закрытое и старое;
* сервис на живом журнале: расхождение открылось, пережило «перезапуск», закрылось.

Люди и линии выдуманы.
"""
import contextlib
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from line_watch import rules
from line_watch import schema as watch_schema
from line_watch.journal import Journal
from line_watch.service import LineWatchService

PORT = os.environ.get('LINE_WATCH_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (LINE_WATCH_TEST_PORT)')

UTC = timezone.utc
NOW = 1_794_500_000

BASE_DDL = '''
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL);
    INSERT INTO users (id, name) VALUES (1, 'Выдуманная Алия'), (3, 'Придуманный Ерлан');
'''


class _Db:
    def __init__(self, schema):
        self.schema = schema

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                                connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.schema)
            cur.execute("SET statement_timeout TO '10s'")
            cur.execute("SET TIME ZONE 'UTC'")
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

    def rows(self, sql, params=()):
        with self._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


@pytest.fixture
def db():
    schema = 't_line_watch_' + uuid.uuid4().hex
    base = _Db(schema)
    admin = base.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        watch_schema.init_line_watch_schema(cur)
        watch_schema.init_line_watch_schema(cur)          # повторный старт приложения
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


def snap(**over):
    base = {'operator_id': 1, 'direction': 'tp', 'phone': 'готов', 'presence': 'inactive', 'sip': 'online'}
    base.update(over)
    return base


def test_schema_is_seen_by_search_path(db):
    assert Journal(db).ready() is True
    with db._get_cursor() as cur:
        cur.execute('SET search_path TO pg_catalog')
        assert watch_schema.schema_is_ready(cur) is False


def test_open_case_is_single_per_line_and_kind(db):
    journal = Journal(db)
    first = journal.open(rules.KIND_CABINET_CLOSED, '915', snap(), {'phone_age': 600}, NOW - 600, NOW)
    again = journal.open(rules.KIND_CABINET_CLOSED, '915', snap(), {'phone_age': 660}, NOW - 600, NOW + 60)
    assert again == first
    other_kind = journal.open(rules.KIND_LINE_OFFLINE, '915', snap(presence='active', sip='offline'), {}, NOW, NOW)
    other_line = journal.open(rules.KIND_CABINET_CLOSED, '904', snap(operator_id=None), {}, NOW, NOW)
    assert len({first, other_kind, other_line}) == 3

    row = db.rows("""SELECT kind, line, operator_id, direction, started_at, last_seen_at, ended_at,
                            phone_status, presence, sip_status, details, general_call_id
                     FROM binotel_line_watch WHERE id = %s""", (first,))[0]
    assert row[:4] == (rules.KIND_CABINET_CLOSED, '915', 1, 'tp')
    assert row[4] == datetime.fromtimestamp(NOW - 600, UTC)
    assert row[5] == datetime.fromtimestamp(NOW + 60, UTC)          # повтор продлил, а не задвоил
    assert row[6] is None
    assert row[7:10] == ('готов', 'inactive', 'online')
    assert row[10] == {'phone_age': 600}
    assert row[11] is None

    journal.touch(first, snap(presence='work in crm'), {'phone_age': 900, 'would_set': 'active'}, NOW + 300)
    row = db.rows('SELECT presence, details, last_seen_at FROM binotel_line_watch WHERE id = %s', (first,))[0]
    assert row[0] == 'work in crm'
    assert row[1] == {'phone_age': 900, 'would_set': 'active'}
    assert row[2] == datetime.fromtimestamp(NOW + 300, UTC)

    journal.close(first, NOW + 400)
    journal.close(first, NOW + 999)                                  # второй раз конец не двигает
    assert db.rows('SELECT ended_at FROM binotel_line_watch WHERE id = %s', (first,))[0][0] == \
        datetime.fromtimestamp(NOW + 400, UTC)
    reopened = journal.open(rules.KIND_CABINET_CLOSED, '915', snap(), {}, NOW + 500, NOW + 500)
    assert reopened != first


def test_heal_result_is_kept(db):
    journal = Journal(db)
    row_id = journal.open(rules.KIND_CABINET_STALE, '929', snap(operator_id=3, direction='op', phone='выключен',
                                                               presence='active', sip='offline'), {}, NOW, NOW)
    journal.heal(row_id, 'set:inactive', 'записано', NOW + 5)
    assert db.rows('SELECT heal_action, heal_result, heal_at FROM binotel_line_watch WHERE id = %s',
                   (row_id,))[0] == ('set:inactive', 'записано', datetime.fromtimestamp(NOW + 5, UTC))


def test_call_event_is_single_per_call_kind_and_line(db):
    journal = Journal(db)
    events = [
        {'kind': rules.KIND_QUEUE_HELD, 'line': '915', 'operator_id': 1, 'direction': 'tp',
         'start': NOW - 120, 'end': NOW, 'phone': 'готов', 'presence': 'active', 'sip': 'online',
         'details': {'wait': 120, 'queue_status': 'телефон офлайн'}},
        {'kind': rules.KIND_GROUP_DROP, 'line': '802', 'operator_id': None, 'direction': 'op',
         'start': NOW, 'end': NOW, 'phone': 'готов', 'presence': None, 'sip': None,
         'details': {'ready': [{'line': '923', 'operator_id': 4, 'sip': 'online'}]}},
    ]
    assert journal.add_events('7000000001', events) == 2
    assert journal.add_events('7000000001', events) == 0            # Binotel повторил вебхук
    assert journal.add_events('7000000002', events[:1]) == 1        # другой звонок — своя строка
    rows = db.rows("""SELECT kind, line, general_call_id, ended_at, details FROM binotel_line_watch
                      ORDER BY id""")
    assert [(r[0], r[1], r[2]) for r in rows] == [
        (rules.KIND_QUEUE_HELD, '915', '7000000001'), (rules.KIND_GROUP_DROP, '802', '7000000001'),
        (rules.KIND_QUEUE_HELD, '915', '7000000002')]
    assert rows[0][3] == datetime.fromtimestamp(NOW, UTC)
    assert rows[0][4]['queue_status'] == 'телефон офлайн'
    # События звонков закрыты сразу и новым процессом как «открытые» не поднимаются.
    assert journal.load_open() == []


def test_new_process_sees_only_open_cases(db):
    journal = Journal(db)
    open_id = journal.open(rules.KIND_CABINET_STALE, '929', snap(operator_id=3), {}, NOW, NOW)
    closed_id = journal.open(rules.KIND_CABINET_CLOSED, '915', snap(), {}, NOW, NOW)
    journal.close(closed_id, NOW + 10)
    assert journal.load_open() == [(open_id, '929', rules.KIND_CABINET_STALE, None, None)]
    # Починка оставляет в строке момент и итог — по ним новый процесс не повторяет отказ.
    journal.heal(open_id, 'set:inactive', 'отказ: кабинет Binotel не принял пароль сотрудника', NOW + 30)
    assert journal.load_open() == [(open_id, '929', rules.KIND_CABINET_STALE, float(NOW + 30),
                                    'отказ: кабинет Binotel не принял пароль сотрудника')]


def test_cleanup_removes_only_old_closed(db):
    journal = Journal(db)
    old = (datetime.now(UTC) - timedelta(days=200)).timestamp()
    old_closed = journal.open(rules.KIND_CABINET_CLOSED, '915', snap(), {}, old, old)
    journal.close(old_closed, old + 60)
    old_open = journal.open(rules.KIND_CABINET_STALE, '928', snap(operator_id=None), {}, old, old)
    fresh = datetime.now(UTC).timestamp()
    fresh_closed = journal.open(rules.KIND_CABINET_CLOSED, '904', snap(operator_id=None), {}, fresh, fresh)
    journal.close(fresh_closed, fresh + 60)
    assert journal.cleanup() == 1
    assert sorted(r[0] for r in db.rows('SELECT id FROM binotel_line_watch')) == sorted([old_open, fresh_closed])


def test_service_on_live_journal(db):
    clock = {'now': float(NOW)}
    lines = {'929': {'presence': 'active', 'presence_at': NOW - 90000, 'sip': 'offline',
                     'sip_at': NOW - 80000, 'inuse_at': None, 'employee_id': 'e929'}}
    phones = {3: {'status_key': 'выключен', 'seconds': 80000}}

    def make():
        return LineWatchService(
            db, roster=lambda: [{'id': 3, 'sip_number': '929', 'direction': 'op'}],
            read_lines=lambda: dict(lines), live_statuses=lambda ids, as_of=None: dict(phones),
            now=lambda: clock['now'])

    assert list(make().tick()) == [('929', rules.KIND_CABINET_STALE)]
    row = db.rows('SELECT id, operator_id, started_at, details, ended_at FROM binotel_line_watch')[0]
    assert row[1] == 3
    assert row[2] == datetime.fromtimestamp(NOW - 80000, UTC)       # с момента, как телефон выключили
    assert row[3]['would_set'] == 'inactive'
    assert row[4] is None

    clock['now'] += 600
    assert list(make().tick()) == [('929', rules.KIND_CABINET_STALE)]    # другой процесс после выкладки
    assert len(db.rows('SELECT id FROM binotel_line_watch')) == 1

    lines['929']['presence'] = 'inactive'
    lines['929']['presence_at'] = int(clock['now'])
    clock['now'] += 60
    assert make().tick() == {}
    ended = db.rows('SELECT ended_at FROM binotel_line_watch WHERE id = %s', (row[0],))[0][0]
    assert ended == datetime.fromtimestamp(clock['now'], UTC)
