# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: TASK_PROJECTS_TEST_PORT — порт локального Postgres.

Люди доски (владелец 24.11.2026): задачу доски поручают только людям из её состава.
В tests/test_task_board_assignees.py правило гоняется на подставленном курсоре; здесь —
что значит его SQL: кто считается человеком доски (уволенные — нет), что правило
делает с доской без людей, с большим составом и с правкой задачи. Настоящие методы
Database (AST'ом из склейки) на живом курсоре в своей схеме со случайным именем — в
конце она удаляется. Только 127.0.0.1, люди выдуманы.

    TASK_PROJECTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_task_board_assignees_postgres.py
"""
import ast
import contextlib
import os
import textwrap
import uuid
from typing import Optional

import pytest

import task_projects
from tests import source_cache

PORT = os.environ.get('TASK_PROJECTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (TASK_PROJECTS_TEST_PORT)')

DATABASE_SOURCE = source_cache.database_flat_path().read_text(encoding='utf-8-sig')

# Доски: HR и FIN со своим составом, 30 — без людей.
HR, FIN, EMPTY = 10, 20, 30
ADMIN, ANNA, BORIS, VERA, GLEB, FIRED = 1, 2, 3, 4, 5, 6

BASE_DDL = '''
    CREATE TABLE users (id integer PRIMARY KEY, status text);
    CREATE TABLE tasks (id integer PRIMARY KEY, project_id integer);
    CREATE TABLE task_projects (id integer PRIMARY KEY, is_archived boolean NOT NULL DEFAULT FALSE);
    CREATE TABLE task_assignees (task_id integer NOT NULL, user_id integer NOT NULL, position integer NOT NULL,
                                 PRIMARY KEY (task_id, user_id));
    CREATE TABLE task_watchers (task_id integer NOT NULL, user_id integer NOT NULL,
                                PRIMARY KEY (task_id, user_id));
    CREATE TABLE task_project_members (project_id integer NOT NULL, user_id integer NOT NULL,
                                       access_level text NOT NULL, added_by integer,
                                       PRIMARY KEY (project_id, user_id));
    INSERT INTO users (id, status) VALUES (1, 'working'), (2, 'working'), (3, NULL), (4, 'working'),
                                          (5, 'working'), (6, 'fired');
    -- HR: Анна — участник, Борис — «доска», уволенный — участник; FIN: Вера.
    INSERT INTO task_project_members (project_id, user_id, access_level) VALUES
        (10, 2, 'member'), (10, 3, 'board'), (10, 6, 'member'), (20, 4, 'member');
    INSERT INTO task_projects (id) VALUES (10), (20), (30);
    -- 300: задача доски HR на Глебе (5, не из состава — поставлена раньше) и Анне (2).
    INSERT INTO tasks (id, project_id) VALUES (300, 10);
    INSERT INTO task_assignees (task_id, user_id, position) VALUES (300, 5, 0), (300, 2, 1);
'''

NAMES = ['TASK_MAX_ASSIGNEES', '_task_project_assignable_ids_tx', '_require_task_project_assignees_tx',
         '_replace_task_project_members_tx', '_task_projects_admin', '_require_task_project_tx',
         '_task_assignee_ids_tx', 'get_task_extra_assignable_ids']


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
    name = 'task_board_people_test_' + uuid.uuid4().hex[:10]
    with _cursor('public') as base:
        base.execute('CREATE SCHEMA ' + name)
    try:
        with _cursor(name) as cursor:
            cursor.execute(BASE_DDL)
            yield cursor
    finally:
        with _cursor('public') as base:
            base.execute('DROP SCHEMA %s CASCADE' % name)


def test_board_people_are_its_roster_without_the_fired(cur):
    # Любой уровень — и участник, и «доска»; уволенный — нет.
    assert DB._task_project_assignable_ids_tx(cur, HR) == {ANNA, BORIS}
    assert DB._task_project_assignable_ids_tx(cur, FIN) == {VERA}
    # Доска без людей — пусто: задачу поручают как раньше.
    assert DB._task_project_assignable_ids_tx(cur, EMPTY) == set()
    assert DB._task_project_assignable_ids_tx(cur, None) == set()


def test_rule_on_real_roster(cur):
    DB._require_task_project_assignees_tx(cur, HR, [ANNA, BORIS])
    with pytest.raises(ValueError, match='ASSIGNEE_NOT_PROJECT_EXECUTOR'):
        DB._require_task_project_assignees_tx(cur, HR, [ANNA, VERA])
    with pytest.raises(ValueError, match='ASSIGNEE_NOT_PROJECT_EXECUTOR'):
        DB._require_task_project_assignees_tx(cur, HR, [FIRED])
    # Доска без людей — как раньше, но больше десяти — нет.
    DB._require_task_project_assignees_tx(cur, EMPTY, [BORIS, VERA])
    with pytest.raises(ValueError, match='TOO_MANY_ASSIGNEES'):
        DB._require_task_project_assignees_tx(cur, EMPTY, list(range(100, 111)))
    # Правка задачи 300: Глеб не из состава, но уже в задаче — не перепроверяется.
    DB._require_task_project_assignees_tx(cur, HR, [GLEB, ANNA], current_ids=[GLEB, ANNA])


def test_everyone_on_a_big_board_is_one_task(cur):
    """Двенадцать человек в составе доски — одной задачей: потолок десяти их не касается."""
    people = list(range(100, 112))
    cur.execute('INSERT INTO users (id, status) SELECT g, %s FROM unnest(%s::int[]) AS g', ('working', people))
    DB._replace_task_project_members_tx(cur, FIN, [
        {'user_id': person_id, 'access_level': 'member'} for person_id in people
    ], ADMIN)
    DB._require_task_project_assignees_tx(cur, FIN, people)
    with pytest.raises(ValueError, match='ASSIGNEE_NOT_PROJECT_EXECUTOR'):
        DB._require_task_project_assignees_tx(cur, FIN, people + [ANNA])


def test_extra_assignable_on_real_sql(cur):
    """Сверх получателей: нынешний состав задачи и люди доски — тем, кто ставит на неё."""
    DB._get_cursor = lambda: contextlib.nullcontext(cur)
    try:
        # Борис — «доска» HR: люди доски ему доступны.
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
