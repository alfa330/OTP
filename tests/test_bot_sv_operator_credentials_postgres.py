# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: SV_CREDENTIALS_TEST_PORT — порт локального Postgres.

`Database.get_active_operators_for_access` — список «Управления операторами🔑» в боте (#380):
работающие операторы отделов СВ, а у СВ без отдела — только записанные за ним. Смысл запроса
(CASE по пустому массиву отделов, отсев уволенных, имя группы по СВ) сверяется здесь на настоящем
Postgres; тест обработчиков бота держит тот же смысл в подменной базе.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема внутри одной транзакции,
в конце откат. Запуск на стенде из `.claude/skills/verify` (пароль — через PGPASSWORD):

    SV_CREDENTIALS_TEST_PORT=55433 python -m pytest tests/test_bot_sv_operator_credentials_postgres.py
"""
import ast
import os
import textwrap
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests import source_cache

PORT = os.environ.get('SV_CREDENTIALS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (SV_CREDENTIALS_TEST_PORT)')

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'database.py').read_text(encoding='utf-8-sig')
_LINES = SOURCE.splitlines(keepends=True)


def _method(name):
    database_class = next(node for node in source_cache.parse(SOURCE).body
                          if isinstance(node, ast.ClassDef) and node.name == 'Database')
    node = next(item for item in database_class.body if isinstance(item, ast.FunctionDef) and item.name == name)
    namespace = {}
    exec(textwrap.dedent(''.join(_LINES[node.lineno - 1:node.end_lineno])), namespace)
    return namespace[name]


@pytest.fixture
def db():
    import psycopg2

    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                            password=os.environ.get('PGPASSWORD'))
    cur = conn.cursor()
    schema = 't_sv_credentials_' + uuid.uuid4().hex
    cur.execute('CREATE SCHEMA ' + schema)
    cur.execute('SET LOCAL search_path TO ' + schema)
    cur.execute('''
        CREATE TABLE users (id integer PRIMARY KEY, name text, role text, status text,
                            supervisor_id integer, department_id integer);
        INSERT INTO users (id, name, role, status, supervisor_id, department_id) VALUES
            (10, 'СВ Первая', 'sv', 'working', NULL, 1),
            (11, 'СВ Вторая', 'sv', 'working', NULL, 1),
            (12, 'СВ Третья', 'sv', 'working', NULL, 2),
            (13, 'СВ Без отдела', 'sv', 'working', NULL, NULL),
            (101, 'Оператор Один', 'operator', 'working', 10, 1),
            (102, 'Оператор Два', 'operator', NULL, 10, 1),
            (103, 'Оператор Три', 'operator', 'vacation', 11, 1),
            (104, 'Оператор Уволенный', 'operator', 'fired', 11, 1),
            (105, 'Оператор Увольняется', 'operator', 'dismissal', 11, 1),
            (106, 'Оператор Без СВ', 'operator', 'working', NULL, 1),
            (107, 'Стажёр Семь', 'trainee', 'working', 10, 1),
            (201, 'Оператор ОП', 'operator', 'working', 12, 2),
            (301, 'Оператор Старый', 'operator', 'working', 13, NULL),
            (302, 'Оператор Чужой', 'operator', 'working', 13, 2);
    ''')

    class Double:
        executed = 0

        @contextmanager
        def _get_cursor(self):
            Double.executed += 1
            yield cur

    Double.get_active_operators_for_access = _method('get_active_operators_for_access')
    try:
        yield Double()
    finally:
        conn.rollback()
        conn.close()


def _rows(items):
    return [(item['id'], item['supervisor_id'], item['supervisor_name']) for item in items]


def test_department_operators_working_by_name_with_their_group(db):
    assert _rows(db.get_active_operators_for_access(department_ids=[1])) == [
        (106, None, None),        # Оператор Без СВ
        (102, 10, 'СВ Первая'),   # Оператор Два — статус не заполнен, значит работает
        (101, 10, 'СВ Первая'),   # Оператор Один
        (103, 11, 'СВ Вторая'),   # Оператор Три — в отпуске, но не уволен
    ]


def test_several_departments_and_supervisor_ignored_when_departments_given(db):
    ids = [item['id'] for item in db.get_active_operators_for_access(department_ids=[2, 1], supervisor_id=13)]
    assert sorted(ids) == [101, 102, 103, 106, 201, 302]


def test_without_department_only_own_operators_wherever_they_are(db):
    assert _rows(db.get_active_operators_for_access(supervisor_id=13)) == [
        (301, 13, 'СВ Без отдела'), (302, 13, 'СВ Без отдела')]
    assert db.get_active_operators_for_access(department_ids=[], supervisor_id=13) == \
        db.get_active_operators_for_access(supervisor_id=13)


def test_nothing_asked_nothing_queried(db):
    before = type(db).executed
    assert db.get_active_operators_for_access() == []
    assert db.get_active_operators_for_access(department_ids=[]) == []
    assert type(db).executed == before
