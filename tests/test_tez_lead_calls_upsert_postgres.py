# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: TEZ_CALLS_TEST_PORT — порт локального Postgres.

Upsert звонков Binotel (`Database.save_tez_lead_calls`) решает, за кем остаётся звонок
при повторной выкачке. Binotel отдаёт имя ТЕКУЩЕГО сотрудника линии и для старых
звонков (линию передают новому оператору переименованием), а удалённого сотрудника —
пустым. Строковые проверки SQL этого поведения не увидят, поэтому здесь крутится сам
метод на настоящей таблице.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема внутри одной
транзакции, в конце откат. Таблица — тем же CREATE, что в database.py.

    TEZ_CALLS_TEST_PORT=55433 python -m pytest tests/test_tez_lead_calls_upsert_postgres.py
"""
import ast
import contextlib
import os
import textwrap
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests import source_cache

PORT = os.environ.get('TEZ_CALLS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (TEZ_CALLS_TEST_PORT)')

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'database.py').read_text(encoding='utf-8-sig')
_LINES = SOURCE.splitlines(keepends=True)

ALMATY = timezone(timedelta(hours=5))
# Прежний и новый сотрудник одной линии — вымышленные.
OLD_NAME, OLD_ID = 'Иванова Айгерим', 41001
NEW_NAME, NEW_ID = 'Карабек Ержан', 41002


def _segment(node):
    lines = list(_LINES[node.lineno - 1:node.end_lineno])
    lines[0] = lines[0][node.col_offset:]
    return ''.join(lines)


def _table_ddl(name):
    """CREATE TABLE таблицы из database.py — по балансу скобок, без копии в тесте."""
    start = SOURCE.index('CREATE TABLE IF NOT EXISTS ' + name + ' (')
    depth = 0
    for pos in range(SOURCE.index('(', start), len(SOURCE)):
        depth += {'(': 1, ')': -1}.get(SOURCE[pos], 0)
        if depth == 0:
            return SOURCE[start:pos + 1]
    raise AssertionError('CREATE TABLE ' + name + ' не закрыт')


def _database_double(cur):
    """Настоящий save_tez_lead_calls поверх курсора теста."""
    from psycopg2.extras import execute_values

    module = source_cache.parse(SOURCE)
    database = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'Database')
    node = next(n for n in database.body
                if isinstance(n, ast.FunctionDef) and n.name == 'save_tez_lead_calls')
    namespace = {'execute_values': execute_values}
    exec(textwrap.dedent(_segment(node)), namespace)

    class Double:
        save_tez_lead_calls = namespace['save_tez_lead_calls']

        @contextlib.contextmanager
        def _get_cursor(self):
            yield cur

    return Double()


@pytest.fixture
def cursor():
    import psycopg2

    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres')
    cur = conn.cursor()
    schema = 't_tez_calls_' + uuid.uuid4().hex
    cur.execute('CREATE SCHEMA ' + schema)
    cur.execute('SET LOCAL search_path TO ' + schema)
    cur.execute('CREATE TABLE users (id integer PRIMARY KEY)')
    cur.execute('INSERT INTO users VALUES (%s), (%s)', (OLD_ID, NEW_ID))
    cur.execute(_table_ddl('tez_lead_calls'))
    try:
        yield cur
    finally:
        conn.rollback()
        conn.close()


def _call(name, operator_id, billsec=42, call_type=1):
    """Звонок так, как его готовит tez.lead_service._prepare_calls."""
    return {
        'general_call_id': '7000000001',
        'phone_norm': '77000000101',
        'started_at': datetime(2026, 8, 12, 15, 0, tzinfo=ALMATY),
        'call_type': call_type,
        'billsec': billsec,
        'waitsec': 3,
        'disposition': 'ANSWER',
        'internal_number': '901',
        'employee_name': name,
        'employee_email': '',
        'operator_id': operator_id,
        'is_qualifying': call_type == 1 and billsec >= 10 and operator_id is not None,
    }


def _saved(cur):
    cur.execute("SELECT employee_name, operator_id, is_qualifying, billsec FROM tez_lead_calls")
    rows = cur.fetchall()
    assert len(rows) == 1
    return rows[0]


def test_renamed_line_does_not_move_the_call_to_the_new_holder(cursor):
    db = _database_double(cursor)
    db.save_tez_lead_calls([_call(OLD_NAME, OLD_ID)])
    # Линию отдали новому сотруднику, и Binotel вернул тот же звонок под его именем.
    db.save_tez_lead_calls([_call(NEW_NAME, NEW_ID)])

    assert _saved(cursor) == (OLD_NAME, OLD_ID, True, 42)


def test_renamed_line_does_not_hand_an_unattributed_call_to_the_new_holder(cursor):
    db = _database_double(cursor)
    # Прежний сотрудник в тот день не был в группе ОП — оператора у звонка нет.
    db.save_tez_lead_calls([_call(OLD_NAME, None)])
    db.save_tez_lead_calls([_call(NEW_NAME, NEW_ID)])

    assert _saved(cursor) == (OLD_NAME, None, False, 42)


def test_known_operator_survives_an_employee_binotel_no_longer_returns(cursor):
    db = _database_double(cursor)
    db.save_tez_lead_calls([_call(OLD_NAME, OLD_ID)])
    # Сотрудника удалили у Binotel: имя приходит пустым, оператор не определяется.
    db.save_tez_lead_calls([_call('', None)])

    assert _saved(cursor) == (OLD_NAME, OLD_ID, True, 42)


def test_operator_is_filled_in_under_the_same_name(cursor):
    db = _database_double(cursor)
    db.save_tez_lead_calls([_call(OLD_NAME, None)])
    # Членство в группе поправили задним числом — тот же сотрудник теперь разрешается.
    db.save_tez_lead_calls([_call(OLD_NAME, OLD_ID)])

    assert _saved(cursor) == (OLD_NAME, OLD_ID, True, 42)


def test_call_without_an_employee_gets_the_first_known_one(cursor):
    db = _database_double(cursor)
    db.save_tez_lead_calls([_call('', None)])
    db.save_tez_lead_calls([_call(NEW_NAME, NEW_ID)])

    assert _saved(cursor) == (NEW_NAME, NEW_ID, True, 42)


def test_call_outcome_is_refreshed_for_the_operator_who_keeps_the_call(cursor):
    db = _database_double(cursor)
    # Первая выкачка застала звонок незавершённым.
    db.save_tez_lead_calls([_call(OLD_NAME, OLD_ID, billsec=0)])
    db.save_tez_lead_calls([_call(NEW_NAME, NEW_ID, billsec=37)])

    assert _saved(cursor) == (OLD_NAME, OLD_ID, True, 37)
