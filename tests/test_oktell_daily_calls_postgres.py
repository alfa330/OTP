# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: OKTELL_CALLS_TEST_PORT — порт локального Postgres.

Синк звонков из Oktell пишет ТОЛЬКО daily_hours.calls (`Database.upsert_daily_calls`) и
пересчитывает месячный итог там, где число изменилось. 18.10.2026 прокси Oktell не отвечал
до утра, день застыл на срезе 14:20, а повторный синк чинил день, но не месяц: итог и
«звонки в час» оставались прежними до следующего пересчёта часов. Ночной прогон теперь
дозабирает дни, которые синк так и не добрал (`get_oktell_calls_unfinished_days` по истории
`oktell_calls_sync_runs`). Здесь настоящие методы и настоящая DDL истории на настоящем SQL.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема внутри одной
транзакции, в конце откат. Запуск:

    OKTELL_CALLS_TEST_PORT=55461 python -m pytest tests/test_oktell_daily_calls_postgres.py
"""
import ast
import contextlib
import logging
import os
import re
import textwrap
import uuid
from datetime import date, datetime
from pathlib import Path

import pytest

from tests import source_cache

PORT = os.environ.get('OKTELL_CALLS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (OKTELL_CALLS_TEST_PORT)')

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'database.py').read_text(encoding='utf-8-sig')
MODULE = source_cache.parse(SOURCE)
DATABASE_CLASS = next(n for n in MODULE.body if isinstance(n, ast.ClassDef) and n.name == 'Database')
_LINES = SOURCE.splitlines(keepends=True)
_MODULE_SQL = ('MEMBERSHIP_MONTH_OVERLAP_SQL', 'MEMBERSHIP_DAY_DISTANCE_SQL')
_METHODS = ('upsert_daily_calls', '_get_operator_group_id_tx',
            'record_oktell_calls_sync_run', 'get_oktell_calls_unfinished_days')


def _segment(node):
    lines = list(_LINES[node.lineno - 1:node.end_lineno])
    lines[0] = lines[0][node.col_offset:]
    return ''.join(lines)


def _runs_ddl():
    """DDL истории прогонов ровно как в _init_db."""
    table = re.search(r'CREATE TABLE IF NOT EXISTS oktell_calls_sync_runs \(.*?\);', SOURCE, re.S)
    index = re.search(r'CREATE INDEX IF NOT EXISTS idx_oktell_calls_sync_runs_dates.*?;', SOURCE, re.S)
    return table.group(0) + '\n' + index.group(0)


def _database_double(cursor, aggregated, failing_months=()):
    """Настоящие методы синка; месячный пересчёт — запись вызова (или сбой для failing_months)."""
    from psycopg2.extras import execute_values

    namespace = {'datetime': datetime, 'execute_values': execute_values, 'logging': logging}
    for node in MODULE.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in _MODULE_SQL for t in node.targets):
            exec(_segment(node), namespace)

    class Double:
        group_lookups = 0

        @contextlib.contextmanager
        def _get_cursor(self):
            yield cursor

        def aggregate_month_from_daily(self, operator_id, month):
            if (operator_id, month) in failing_months:
                raise RuntimeError('aggregation failed')
            aggregated.append((operator_id, month))

    for node in DATABASE_CLASS.body:
        if isinstance(node, ast.FunctionDef) and node.name in _METHODS:
            exec(textwrap.dedent(_segment(node)), namespace)
            setattr(Double, node.name, namespace[node.name])

    real_group_lookup = Double._get_operator_group_id_tx

    def counted_group_lookup(self, cur, operator_id, as_of):
        Double.group_lookups += 1
        return real_group_lookup(self, cur, operator_id, as_of)

    Double._get_operator_group_id_tx = counted_group_lookup
    return Double()


@pytest.fixture
def cursor():
    import psycopg2

    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres')
    cur = conn.cursor()
    schema = 't_oktell_calls_' + uuid.uuid4().hex
    cur.execute('CREATE SCHEMA ' + schema)
    cur.execute('SET LOCAL search_path TO ' + schema)
    cur.execute(_runs_ddl())
    cur.execute('''
        CREATE TABLE groups (id integer PRIMARY KEY, name text);
        CREATE TABLE group_operator_memberships (id serial PRIMARY KEY, group_id integer NOT NULL,
                                                 operator_id integer NOT NULL, start_date date NOT NULL,
                                                 end_date date);
        CREATE TABLE daily_hours (
            id serial PRIMARY KEY,
            operator_id integer NOT NULL,
            day date NOT NULL,
            work_time float NOT NULL DEFAULT 0,
            talk_time float NOT NULL DEFAULT 0,
            calls integer NOT NULL DEFAULT 0,
            late_minutes integer NOT NULL DEFAULT 0,
            group_id integer,
            extra_metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
            auto_aggregated boolean NOT NULL DEFAULT FALSE,
            UNIQUE (operator_id, day)
        );
        INSERT INTO groups VALUES (6, 'Чат менеджеры'), (8, 'Основа');
        INSERT INTO group_operator_memberships (group_id, operator_id, start_date, end_date) VALUES
            (8, 613, '2026-10-12', NULL),
            (8, 77, '2026-01-01', NULL),
            (8, 518, '2026-09-23', '2026-10-18'), (6, 518, '2026-10-19', NULL),
            (8, 47, '2026-09-01', NULL);
        -- Строки дня уже собраны пересчётом часов по статусам; звонки — срез 14:20.
        INSERT INTO daily_hours (operator_id, day, work_time, talk_time, calls, late_minutes,
                                 group_id, extra_metrics, auto_aggregated) VALUES
            (613, '2026-10-17', 3.77, 2.42, 120, 0, 8, '{}', TRUE),
            (613, '2026-10-18', 4.53, 2.93, 0, 1, 8, '{"note": 1}', TRUE),
            (77, '2026-10-18', 7.10, 4.00, 88, 0, NULL, '{}', TRUE),
            (47, '2026-09-30', 6.00, 3.00, 10, 0, 5, '{}', TRUE);
    ''')
    try:
        yield cur
    finally:
        conn.rollback()
        conn.close()


def _row(cur, operator_id, day):
    cur.execute('''
        SELECT work_time, talk_time, calls, late_minutes, group_id, extra_metrics, auto_aggregated
        FROM daily_hours WHERE operator_id = %s AND day = %s
    ''', (operator_id, day))
    return cur.fetchone()


def test_writes_only_calls_and_recounts_months_where_calls_changed(cursor):
    aggregated = []
    db = _database_double(cursor, aggregated)

    result = db.upsert_daily_calls([
        (613, '2026-10-18', 126),   # застывший ноль -> настоящее число
        (613, '2026-10-17', 120),   # тот же срез, что уже записан
        (77, date(2026, 10, 18), 232),
        (518, '2026-10-18', 6),     # строки ещё не было
        (47, '2026-09-30', 28),     # прошлый месяц тоже пересчитывается
    ])

    assert result == {'saved_days': 5, 'changed_days': 4, 'aggregated_months': 4, 'aggregate_errors': 0}
    assert aggregated == [(47, '2026-09'), (77, '2026-10'), (518, '2026-10'), (613, '2026-10')]
    # Группа на дату запрашивается только для новой строки (518) и строки без группы (77).
    assert type(db).group_lookups == 2
    # Часы, опоздание, метрики и пометка пересчёта — как были; поменялось только число звонков.
    assert _row(cursor, 613, '2026-10-18') == (4.53, 2.93, 126, 1, 8, {'note': 1}, True)
    assert _row(cursor, 613, '2026-10-17') == (3.77, 2.42, 120, 0, 8, {}, True)
    # Пустая группа дополняется по членству на дату, заполненная не перетирается.
    assert _row(cursor, 77, '2026-10-18')[2:5] == (232, 0, 8)
    assert _row(cursor, 47, '2026-09-30')[2:5] == (28, 0, 5)
    # Новая строка: только звонки, остальное по умолчанию; группа — та, что была 18.10.
    assert _row(cursor, 518, '2026-10-18') == (0.0, 0.0, 6, 0, 8, {}, False)


def test_repeat_of_the_same_numbers_recounts_nothing(cursor):
    aggregated = []
    db = _database_double(cursor, aggregated)
    rows = [(613, '2026-10-17', 120), (77, '2026-10-18', 88)]

    assert db.upsert_daily_calls(rows) == {
        'saved_days': 2, 'changed_days': 0, 'aggregated_months': 0, 'aggregate_errors': 0}
    assert aggregated == []


def test_failed_month_recount_keeps_everyones_calls(cursor):
    # Сбой пересчёта месяца у одного оператора не откатывает звонки остальных.
    aggregated = []
    db = _database_double(cursor, aggregated, failing_months={(77, '2026-10')})

    result = db.upsert_daily_calls([(613, '2026-10-18', 126), (77, '2026-10-18', 232)])

    assert result == {'saved_days': 2, 'changed_days': 2, 'aggregated_months': 1, 'aggregate_errors': 1}
    assert aggregated == [(613, '2026-10')]
    assert _row(cursor, 613, '2026-10-18')[2] == 126
    assert _row(cursor, 77, '2026-10-18')[2] == 232


def test_zero_calls_for_a_missing_row_is_not_a_change(cursor):
    aggregated = []
    db = _database_double(cursor, aggregated)

    result = db.upsert_daily_calls([(613, '2026-10-20', 0)])

    assert result == {'saved_days': 1, 'changed_days': 0, 'aggregated_months': 0, 'aggregate_errors': 0}
    assert aggregated == []
    assert _row(cursor, 613, '2026-10-20')[2] == 0


def test_empty_input_touches_nothing(cursor):
    aggregated = []
    db = _database_double(cursor, aggregated)

    assert db.upsert_daily_calls(iter(())) == {
        'saved_days': 0, 'changed_days': 0, 'aggregated_months': 0, 'aggregate_errors': 0}
    cursor.execute('SELECT COUNT(*) FROM daily_hours')
    assert cursor.fetchone()[0] == 4
    assert aggregated == []


def _record(db, started_at, date_from, date_to, status, triggered_by='scheduler'):
    db.record_oktell_calls_sync_run(
        started_at=started_at, finished_at=started_at, date_from=date_from, date_to=date_to,
        triggered_by=triggered_by, status=status, updated_days=0,
        error=None if status == 'success' else 'Connection refused')


def test_without_history_no_day_is_unfinished(cursor):
    # До первой записи истории прогоны не писались: такие дни ночью не трогаем.
    db = _database_double(cursor, [])
    assert db.get_oktell_calls_unfinished_days(date(2026, 10, 10), date(2026, 10, 19)) == []


def test_unfinished_days_follow_the_18_october_outage(cursor):
    db = _database_double(cursor, [])
    # История начинается 15.10: только внутридневной прогон, ночного за 15.10 нет.
    _record(db, datetime(2026, 10, 15, 11, 20), date(2026, 10, 15), date(2026, 10, 15), 'success')
    # 16.10 и 17.10 добраны ночными прогонами после окончания дня.
    _record(db, datetime(2026, 10, 17, 5, 25), date(2026, 10, 16), date(2026, 10, 16), 'success')
    _record(db, datetime(2026, 10, 18, 5, 25), date(2026, 10, 17), date(2026, 10, 17), 'success')
    # 18.10: удачный срез 14:20 — ещё до конца дня; ночной прогон 19.10 упал.
    _record(db, datetime(2026, 10, 18, 14, 20), date(2026, 10, 18), date(2026, 10, 18), 'success')
    _record(db, datetime(2026, 10, 18, 17, 20), date(2026, 10, 18), date(2026, 10, 18), 'failed')
    _record(db, datetime(2026, 10, 19, 5, 25), date(2026, 10, 18), date(2026, 10, 18), 'failed')

    unfinished = db.get_oktell_calls_unfinished_days(date(2026, 10, 10), date(2026, 10, 19))
    # До 15.10 истории нет — не в счёт; 19.10 ещё никто не забирал после его окончания.
    assert unfinished == [date(2026, 10, 15), date(2026, 10, 18), date(2026, 10, 19)]

    # Ручной синк за 18.10 после окончания дня добирает его.
    _record(db, datetime(2026, 10, 20, 10, 0), date(2026, 10, 18), date(2026, 10, 18), 'success', 'manual')
    assert db.get_oktell_calls_unfinished_days(date(2026, 10, 16), date(2026, 10, 18)) == []


def test_run_record_keeps_short_fields(cursor):
    db = _database_double(cursor, [])
    db.record_oktell_calls_sync_run(
        started_at=datetime(2026, 10, 19, 5, 25), finished_at=datetime(2026, 10, 19, 5, 25, 1),
        date_from=date(2026, 10, 18), date_to=date(2026, 10, 18), triggered_by='scheduler',
        status='failed', updated_days=0, error='x' * 5000)
    cursor.execute('SELECT triggered_by, status, updated_days, LENGTH(error) FROM oktell_calls_sync_runs')
    assert cursor.fetchone() == ('scheduler', 'failed', 0, 1000)
