# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: GROUP_MODEL_TEST_PORT — порт локального Postgres.

У группы без модели расчёта сотрудник считается по модели своего направления. Правило
живёт в двух местах: в резолвере на дату (`_load_operator_calculation_models_tx`) и в
SQL-выражении сегментов (`_GROUP_MEMBER_MODEL_SQL`). Разойдись они — «Учёт часов» и
статусы считали бы одного человека разными моделями, и строковые проверки этого не
увидят. Здесь оба ответа сверяются на одной базе.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема внутри одной
транзакции, в конце откат. Запуск на стенде из `.claude/skills/verify` (пароль базы
стенда — через PGPASSWORD, как у любого клиента Postgres):

    GROUP_MODEL_TEST_PORT=55433 python -m pytest tests/test_group_optional_model_postgres.py
"""
import ast
import calendar
import os
import textwrap
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from tests import source_cache

PORT = os.environ.get('GROUP_MODEL_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (GROUP_MODEL_TEST_PORT)')

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'database.py').read_text(encoding='utf-8-sig')
MODULE = source_cache.parse(SOURCE)
DATABASE_CLASS = next(n for n in MODULE.body if isinstance(n, ast.ClassDef) and n.name == 'Database')

_MODULE_NAMES = {
    'CALCULATION_MODEL_OPERATOR', 'CALCULATION_MODEL_CHAT_MANAGER', 'CALCULATION_MODEL_TEZ_LINE',
    'CALCULATION_MODEL_TEZ_OP', 'CALCULATION_MODEL_OP_VERIFICATOR', 'CALCULATION_MODEL_OP_YANDEX_REG',
    'CALCULATION_MODEL_OP_OSNOVA', 'CALCULATION_MODEL_OP_POTOK', 'CALCULATION_MODEL_OP_SALES_CODES',
    'CALCULATION_MODEL_ALLOWED',
}
_METHODS = (
    '_normalize_calculation_model_code',
    '_load_operator_calculation_models_tx',
    '_load_segments_by_operator_tx',
    '_get_operator_month_segments_tx',
    '_group_effective_direction_id_tx',
)
_CLASS_SQL = ('_GROUP_MEMBER_MODEL_SQL', '_GROUP_EFFECTIVE_DIRECTION_SQL')


_LINES = SOURCE.splitlines(keepends=True)


def _segment(node):
    """Исходник оператора по номерам строк: `ast.get_source_segment` на этом файле
    стоит 0,3 с за вызов."""
    lines = list(_LINES[node.lineno - 1:node.end_lineno])
    lines[0] = lines[0][node.col_offset:]
    return ''.join(lines)


def _database_double():
    """Настоящие методы и настоящее выражение лестницы — без пула и без схемы приложения."""
    namespace = {'Optional': Optional, 'List': List, 'Dict': Dict, 'Any': Any,
                 'calendar': calendar, 'date': date}
    for node in MODULE.body:
        is_constant = isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in _MODULE_NAMES for t in node.targets)
        is_normalizer = isinstance(node, ast.FunctionDef) and node.name == 'normalize_calculation_model_code'
        if is_constant or is_normalizer:
            exec(_segment(node), namespace)

    class Double:
        def _aggregate_segment_from_daily_tx(self, *_args, **_kwargs):
            return {}

    for node in DATABASE_CLASS.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in _CLASS_SQL for t in node.targets):
            exec(_segment(node), namespace)
            for name in _CLASS_SQL:
                if name in namespace:
                    setattr(Double, name, namespace[name])
        if isinstance(node, ast.FunctionDef) and node.name in _METHODS:
            exec(textwrap.dedent(_segment(node)), namespace)
            setattr(Double, node.name, namespace[node.name])
    return Double()


@pytest.fixture
def cursor():
    import psycopg2

    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres')
    cur = conn.cursor()
    schema = 't_group_model_' + uuid.uuid4().hex
    cur.execute('CREATE SCHEMA ' + schema)
    cur.execute('SET LOCAL search_path TO ' + schema)
    cur.execute('''
        CREATE TABLE directions (id integer PRIMARY KEY, name text, calculation_model_code text,
                             department_id integer, is_active boolean NOT NULL DEFAULT TRUE,
                             canonical_id integer);
        CREATE TABLE users (id integer PRIMARY KEY, name text, direction_id integer);
        CREATE TABLE groups (id integer PRIMARY KEY, name text, department_id integer,
                             direction_id integer, calculation_model_code text);
        CREATE TABLE group_operator_memberships (id serial PRIMARY KEY, group_id integer,
                             operator_id integer, start_date date, end_date date);
        INSERT INTO directions (id, name, calculation_model_code, department_id, is_active, canonical_id) VALUES
            (20, 'Чат менеджер', 'chat_manager', 1, TRUE, NULL),
            (21, 'Основа', 'operator', 1, TRUE, NULL),
            (73, 'Основа ОП', 'op_osnova', 367, TRUE, NULL),
            (74, 'Поток', 'op_potok', 367, TRUE, NULL),
            (79, 'ТП линия, прежняя версия', 'operator', 560, FALSE, 83),
            (83, 'ТП линия', 'operator', 560, TRUE, NULL),
            (87, 'Регионы', 'operator', 909, TRUE, NULL),
            (95, 'Закрытое направление', 'operator', 2000, FALSE, NULL);
        INSERT INTO groups (id, name, department_id, direction_id, calculation_model_code) VALUES
            (1, 'С моделью', 1, NULL, 'operator'),
            (2, 'Без модели', 1, NULL, NULL),
            (3, 'Без модели, с направлением', 1, 20, NULL),
            (10, 'Фронт-офис без модели', 909, NULL, NULL),
            (11, 'Фронт-офис с операторской', 909, NULL, 'operator'),
            (12, 'Фронт-офис с чужой моделью', 909, NULL, 'chat_manager'),
            (13, 'ОП: модель без направления', 367, NULL, 'op_potok'),
            (14, 'ОП без модели', 367, NULL, NULL),
            (15, 'Бэк-офис без направлений', 2008, NULL, NULL),
            (16, 'На прежней версии направления', 560, 79, 'tez_line'),
            (17, 'Отдел с одним закрытым направлением', 2000, NULL, NULL),
            (18, 'Без отдела', NULL, NULL, NULL);
        INSERT INTO users VALUES
            (4, 'Чат', 20), (5, 'Основа', 21), (6, 'Без направления', NULL),
            (7, 'Поток', 74), (8, 'Чат в группе с моделью', 20), (9, 'Основа в группе чатов', 21);
        INSERT INTO group_operator_memberships (group_id, operator_id, start_date, end_date) VALUES
            (1, 4, '2026-08-01', '2026-09-09'), (2, 4, '2026-09-10', NULL),
            (2, 5, '2026-08-01', NULL), (2, 6, '2026-08-01', NULL), (2, 7, '2026-08-01', NULL),
            (1, 8, '2026-08-01', NULL), (3, 9, '2026-08-01', NULL);
    ''')
    try:
        yield cur
    finally:
        conn.rollback()
        conn.close()


MEMBERS = [4, 5, 6, 7, 8, 9]


def _segment_models(segments):
    return {op: [(s['group_id'], s['calculation_model_code']) for s in items]
            for op, items in segments.items()}


def test_member_of_a_group_without_a_model_is_counted_by_own_direction(cursor):
    db = _database_double()

    segments = _segment_models(
        db._load_segments_by_operator_tx(cursor, MEMBERS, date(2026, 9, 1), date(2026, 9, 30)))

    assert segments == {
        # до 9-го — группа с операторской моделью, с 10-го — группа без модели: чат по направлению
        4: [(1, 'operator'), (2, 'chat_manager')],
        5: [(2, 'operator')],
        # ни модели группы, ни направления — операторская, как и раньше для неизвестного
        6: [(2, 'operator')],
        7: [(2, 'op_potok')],
        # своя модель группы старше направления сотрудника
        8: [(1, 'operator')],
        # считается направление СОТРУДНИКА, а не направление, записанное в группе
        9: [(3, 'operator')],
    }


def test_segments_agree_with_the_period_resolver(cursor):
    db = _database_double()
    segments = db._load_segments_by_operator_tx(cursor, MEMBERS, date(2026, 9, 1), date(2026, 9, 30))

    for day in (date(2026, 9, 5), date(2026, 9, 20)):
        resolved = db._load_operator_calculation_models_tx(cursor, MEMBERS, as_of=day)
        from_segments = {
            op: next(s['calculation_model_code'] for s in items
                     if s['start_day'] <= day.day <= s['end_day'])
            for op, items in segments.items()
        }
        assert from_segments == resolved, day

    assert db._load_operator_calculation_models_tx(cursor, [4], as_of=date(2026, 9, 5)) == {4: 'operator'}
    assert db._load_operator_calculation_models_tx(cursor, [4], as_of=date(2026, 9, 20)) == {4: 'chat_manager'}


def test_month_segments_of_one_operator_use_the_same_ladder(cursor):
    db = _database_double()

    by_operator = {
        op: [(s['group_id'], s['calculation_model_code'])
             for s in db._get_operator_month_segments_tx(cursor, op, '2026-09')]
        for op in MEMBERS
    }

    batch = _segment_models(
        db._load_segments_by_operator_tx(cursor, MEMBERS, date(2026, 9, 1), date(2026, 9, 30)))
    assert by_operator == batch


def test_effective_direction_of_a_group_without_a_model(cursor):
    """Направление, которое получит оператор при переводе в группу."""
    db = _database_double()

    effective = {gid: db._group_effective_direction_id_tx(cursor, gid)
                 for gid in (1, 2, 3, 10, 11, 12, 13, 14, 15, 16, 17, 18)}

    assert effective == {
        # прежние правила: единственное направление отдела с моделью группы
        1: 21, 11: 87, 13: 74,
        # модель группы не совпала ни с одним направлением отдела — не угадываем
        12: None,
        # своё направление группы; с прежней версии — на живую строку
        3: 20, 16: 83,
        # без модели: направление берётся, только когда оно в отделе одно
        10: 87,
        2: None, 14: None,
        # направлений нет, единственное закрыто, отдела нет
        15: None, 17: None, 18: None,
    }


def _drop_not_null_block():
    """Текст блока DO из _init_db — тот самый, что уходит в cursor.execute."""
    init = next(n for n in DATABASE_CLASS.body if isinstance(n, ast.FunctionDef) and n.name == '_init_db')
    (block,) = [
        node.value for node in ast.walk(init)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and 'ALTER TABLE groups ALTER COLUMN calculation_model_code DROP NOT NULL' in node.value
    ]
    return block


def test_migration_frees_the_model_column_once(cursor):
    block = _drop_not_null_block()
    schema = 't_group_model_ddl_' + uuid.uuid4().hex
    cursor.execute('CREATE SCHEMA ' + schema)
    cursor.execute('SET LOCAL search_path TO ' + schema)
    cursor.execute("""
        CREATE TABLE groups (id serial PRIMARY KEY, name text,
                             calculation_model_code VARCHAR(32) NOT NULL DEFAULT 'operator');
        CREATE TABLE group_month_snapshots (id serial PRIMARY KEY, group_id integer,
                             calculation_model_code VARCHAR(32) NOT NULL DEFAULT 'operator');
        CREATE TABLE group_operator_month_snapshots (id serial PRIMARY KEY,
                             calculation_model_code VARCHAR(32) NOT NULL DEFAULT 'operator');
    """)

    def nullable():
        cursor.execute("""
            SELECT table_name, is_nullable FROM information_schema.columns
             WHERE table_schema = current_schema() AND column_name = 'calculation_model_code'
             ORDER BY table_name
        """)
        return dict(cursor.fetchall())

    assert nullable() == {'groups': 'NO', 'group_month_snapshots': 'NO',
                          'group_operator_month_snapshots': 'NO'}

    cursor.execute(block)
    cursor.execute(block)  # повторный старт ничего не ломает

    # снимок сотрудника хранит уже разрешённую модель — его колонку не трогаем
    assert nullable() == {'groups': 'YES', 'group_month_snapshots': 'YES',
                          'group_operator_month_snapshots': 'NO'}
    cursor.execute("INSERT INTO groups (name, calculation_model_code) VALUES ('Группа ООЗ', NULL) RETURNING id")
    group_id = cursor.fetchone()[0]
    cursor.execute("INSERT INTO group_month_snapshots (group_id, calculation_model_code) VALUES (%s, NULL)",
                   (group_id,))
    # строка без колонки по-прежнему получает прежний дефолт
    cursor.execute("INSERT INTO groups (name) VALUES ('Старая вставка') RETURNING calculation_model_code")
    assert cursor.fetchone()[0] == 'operator'

