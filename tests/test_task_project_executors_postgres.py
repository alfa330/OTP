# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: TASK_PROJECTS_TEST_PORT — порт локального Postgres.

Исполнители доски (владелец 24.11.2026). В tests/test_task_project_executors.py правило
гоняется на подставленном курсоре; здесь — что значит его SQL: кто считается
исполнителем доски, как отметка ложится в состав и что миграция колонки проходит
повторно. Настоящие методы Database (AST'ом из склейки) и настоящий ALTER из
db/schema.py, на живом курсоре в своей схеме со случайным именем — в конце она
удаляется. Только 127.0.0.1, люди выдуманы.

    TASK_PROJECTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_task_project_executors_postgres.py
"""
import ast
import contextlib
import os
import re
import textwrap
import uuid
from pathlib import Path
from typing import Optional

import pytest

import task_projects
from tests import source_cache

PORT = os.environ.get('TASK_PROJECTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (TASK_PROJECTS_TEST_PORT)')

ROOT = Path(__file__).resolve().parents[1]
DATABASE_SOURCE = source_cache.database_flat_path().read_text(encoding='utf-8-sig')
SCHEMA_SOURCE = (ROOT / 'db' / 'schema.py').read_text(encoding='utf-8')

HR, FIN = 10, 20
ADMIN, ANNA, BORIS, VERA, GLEB, FIRED = 1, 2, 3, 4, 5, 6

BASE_DDL = '''
    CREATE TABLE users (id integer PRIMARY KEY, status text);
    CREATE TABLE tasks (id integer PRIMARY KEY, project_id integer);
    CREATE TABLE task_projects (id integer PRIMARY KEY, is_archived boolean NOT NULL DEFAULT FALSE);
    CREATE TABLE task_assignees (task_id integer NOT NULL, user_id integer NOT NULL, position integer NOT NULL,
                                 PRIMARY KEY (task_id, user_id));
    CREATE TABLE task_watchers (task_id integer NOT NULL, user_id integer NOT NULL,
                                PRIMARY KEY (task_id, user_id));
    -- Состав доски — как до 24.11.2026: колонку исполнителя добавляет миграция ниже.
    CREATE TABLE task_project_members (project_id integer NOT NULL, user_id integer NOT NULL,
                                       access_level text NOT NULL, added_by integer,
                                       PRIMARY KEY (project_id, user_id));
    INSERT INTO users (id, status) VALUES (1, 'working'), (2, 'working'), (3, NULL), (4, 'working'),
                                          (5, 'working'), (6, 'fired');
    INSERT INTO task_project_members (project_id, user_id, access_level) VALUES
        (10, 2, 'member'), (10, 3, 'board'), (10, 6, 'member'), (20, 4, 'member');
    INSERT INTO task_projects (id) VALUES (10), (20);
    -- 300: задача доски HR на Глебе (5) и Анне (2).
    INSERT INTO tasks (id, project_id) VALUES (300, 10);
    INSERT INTO task_assignees (task_id, user_id, position) VALUES (300, 5, 0), (300, 2, 1);
'''

NAMES = ['TASK_MAX_ASSIGNEES', '_task_project_executor_ids_tx', '_require_task_project_assignees_tx',
         '_replace_task_project_members_tx', '_task_projects_admin', '_require_task_project_tx',
         '_task_assignee_ids_tx', 'get_task_extra_assignable_ids']


def _migration():
    """Тот самый ALTER из db/schema.py — без копии текста в тесте."""
    match = re.search(r'ALTER TABLE task_project_members\s+ADD COLUMN IF NOT EXISTS is_executor[^;]*;', SCHEMA_SOURCE)
    assert match, 'в db/schema.py нет миграции is_executor'
    return match.group(0)


def _build_database():
    module = source_cache.parse(DATABASE_SOURCE)
    database_class = next(node for node in module.body
                          if isinstance(node, ast.ClassDef) and node.name == 'Database')
    lines = DATABASE_SOURCE.splitlines(keepends=True)
    body = {}
    for node in database_class.body:
        if isinstance(node, ast.FunctionDef) and node.name in NAMES:
            first = min([node.lineno] + [item.lineno for item in node.decorator_list])
            body[node.name] = textwrap.dedent(''.join(lines[first - 1:node.end_lineno]))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in NAMES:
                    body[target.id] = textwrap.dedent(ast.get_source_segment(DATABASE_SOURCE, node))
    missing = set(NAMES) - set(body)
    assert not missing, f'в Database нет: {sorted(missing)}'
    namespace = {'task_projects': task_projects, 'Optional': Optional}
    wanted = {'normalize_role_value', 'role_has_min', 'ROLE_ALIASES', 'ROLE_HIERARCHY'}
    for node in module.body:
        names = ([node.name] if isinstance(node, ast.FunctionDef)
                 else [t.id for t in getattr(node, 'targets', []) if isinstance(t, ast.Name)])
        if set(names) & wanted:
            exec(ast.get_source_segment(DATABASE_SOURCE, node), namespace)
    exec('class FakeDatabase:\n' + ''.join(textwrap.indent(body[name], '    ') + '\n' for name in NAMES),
         namespace)
    return namespace['FakeDatabase']()


DB = _build_database()


@contextlib.contextmanager
def _cursor(schema):
    import psycopg2
    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres', connect_timeout=3)
    cur = conn.cursor()
    try:
        cur.execute("SET statement_timeout TO '10s'")
        cur.execute('SET search_path TO ' + schema)
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


@pytest.fixture
def cur():
    name = 'task_executors_test_' + uuid.uuid4().hex[:10]
    with _cursor('public') as base:
        base.execute('CREATE SCHEMA ' + name)
    try:
        with _cursor(name) as cursor:
            cursor.execute(BASE_DDL)
            # Дважды: миграция идемпотентна, вторая прогонка на старте ничего не ломает.
            cursor.execute(_migration())
            cursor.execute(_migration())
            yield cursor
    finally:
        with _cursor('public') as base:
            base.execute('DROP SCHEMA %s CASCADE' % name)


def _flags(cur, project_id):
    cur.execute('SELECT user_id, is_executor FROM task_project_members WHERE project_id = %s ORDER BY user_id',
                (project_id,))
    return cur.fetchall()


def test_migration_keeps_old_rows_and_marks_nobody(cur):
    assert _flags(cur, HR) == [(ANNA, False), (BORIS, False), (FIRED, False)]
    assert DB._task_project_executor_ids_tx(cur, HR) == set()


def test_settings_mark_executors_and_old_tab_keeps_them(cur):
    DB._replace_task_project_members_tx(cur, HR, [
        {'user_id': ANNA, 'access_level': 'member', 'is_executor': True},
        {'user_id': BORIS, 'access_level': 'board', 'is_executor': True},
        {'user_id': FIRED, 'access_level': 'member', 'is_executor': True},
        {'user_id': GLEB, 'access_level': 'board'},          # новый, без отметки — не исполнитель
    ], ADMIN)
    assert _flags(cur, HR) == [(ANNA, True), (BORIS, True), (GLEB, False), (FIRED, True)]
    # Уволенный исполнителем не считается: поручить ему нельзя.
    assert DB._task_project_executor_ids_tx(cur, HR) == {ANNA, BORIS}
    # Вкладка, открытая до отметки, шлёт состав без неё — исполнители на месте.
    DB._replace_task_project_members_tx(cur, HR, [
        {'user_id': ANNA, 'access_level': 'board'},
        {'user_id': BORIS, 'access_level': 'board', 'is_executor': False},
        {'user_id': GLEB, 'access_level': 'board'},
    ], ADMIN)
    assert _flags(cur, HR) == [(ANNA, True), (BORIS, False), (GLEB, False)]
    # Чужая доска не задета.
    assert _flags(cur, FIN) == [(VERA, False)]


def test_rule_on_real_executors(cur):
    DB._replace_task_project_members_tx(cur, HR, [
        {'user_id': ANNA, 'access_level': 'member', 'is_executor': True},
        {'user_id': BORIS, 'access_level': 'board', 'is_executor': False},
    ], ADMIN)
    DB._require_task_project_assignees_tx(cur, HR, [ANNA])
    with pytest.raises(ValueError, match='ASSIGNEE_NOT_PROJECT_EXECUTOR'):
        DB._require_task_project_assignees_tx(cur, HR, [ANNA, BORIS])
    # У доски без привязки — как раньше, но больше десяти — нет.
    DB._require_task_project_assignees_tx(cur, FIN, [BORIS, VERA])
    with pytest.raises(ValueError, match='TOO_MANY_ASSIGNEES'):
        DB._require_task_project_assignees_tx(cur, FIN, list(range(100, 111)))


def test_everyone_on_a_big_board_is_one_task(cur):
    """Двенадцать исполнителей доски — одной задачей: потолок десяти их не касается."""
    people = list(range(100, 112))
    cur.execute('INSERT INTO users (id, status) SELECT g, %s FROM unnest(%s::int[]) AS g', ('working', people))
    DB._replace_task_project_members_tx(cur, HR, [
        {'user_id': person_id, 'access_level': 'member', 'is_executor': True} for person_id in people
    ], ADMIN)
    DB._require_task_project_assignees_tx(cur, HR, people)
    with pytest.raises(ValueError, match='ASSIGNEE_NOT_PROJECT_EXECUTOR'):
        DB._require_task_project_assignees_tx(cur, HR, people + [VERA])


def test_extra_assignable_on_real_sql(cur):
    """Сверх получателей: нынешний состав задачи и исполнители доски — тем, кто ставит на неё."""
    DB._get_cursor = lambda: contextlib.nullcontext(cur)
    try:
        DB._replace_task_project_members_tx(cur, HR, [
            {'user_id': ANNA, 'access_level': 'member', 'is_executor': True},
            {'user_id': BORIS, 'access_level': 'board', 'is_executor': True},
        ], ADMIN)
        # Борис — «доска» HR: исполнители доски ему доступны.
        assert DB.get_task_extra_assignable_ids(BORIS, 'sv', project_id=HR) == {ANNA, BORIS}
        # Вера не в составе HR — обхода нет; админ раздела — есть.
        assert DB.get_task_extra_assignable_ids(VERA, 'sv', project_id=HR) == set()
        assert DB.get_task_extra_assignable_ids(VERA, 'admin', project_id=HR) == {ANNA, BORIS}
        # Правка задачи 300: её нынешний состав остаётся, даже без доступа к доске.
        assert DB.get_task_extra_assignable_ids(VERA, 'sv', task_id=300) == {GLEB, ANNA}
        assert DB.get_task_extra_assignable_ids(BORIS, 'sv', task_id=300) == {GLEB, ANNA, BORIS}
        # Доска в архиве — новых через неё не ставят.
        cur.execute('UPDATE task_projects SET is_archived = TRUE WHERE id = %s', (HR,))
        assert DB.get_task_extra_assignable_ids(BORIS, 'sv', task_id=300) == {GLEB, ANNA}
    finally:
        del DB._get_cursor
