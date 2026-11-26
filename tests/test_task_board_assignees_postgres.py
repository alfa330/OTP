# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: TASK_PROJECTS_TEST_PORT — порт локального Postgres.

Исполнители и участники доски (владелец 26.11.2026): задачу доски поручают только её
исполнителям, участник исполнителем не становится. В tests/test_task_board_assignees.py
правило гоняется на подставленном курсоре; здесь — что значит его SQL: кто считается
исполнителем доски (уволенные — нет), что правило делает с доской без людей, с доской
без исполнителей, с большим составом и с правкой задачи, и как сохраняется роль.
Настоящие методы Database (AST'ом из склейки) на живом курсоре в своей схеме со
случайным именем — в конце она удаляется. Только 127.0.0.1, люди выдуманы.

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

# Доски: HR и FIN со своим составом, 30 — без людей, 40 — люди есть, исполнителей нет.
HR, FIN, EMPTY, WATCH = 10, 20, 30, 40
ADMIN, ANNA, BORIS, VERA, GLEB, FIRED, DINA = 1, 2, 3, 4, 5, 6, 7

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
                                       is_executor boolean NOT NULL DEFAULT FALSE,
                                       PRIMARY KEY (project_id, user_id));
    INSERT INTO users (id, status) VALUES (1, 'working'), (2, 'working'), (3, NULL), (4, 'working'),
                                          (5, 'working'), (6, 'fired'), (7, 'working');
    -- HR: Анна — исполнитель, Борис — исполнитель с «только свои», уволенный — исполнитель,
    -- Дина — участник; FIN: Вера — исполнитель; WATCH: только Дина, участник.
    INSERT INTO task_project_members (project_id, user_id, access_level, is_executor) VALUES
        (10, 2, 'member', TRUE), (10, 3, 'board', TRUE), (10, 6, 'member', TRUE), (10, 7, 'member', FALSE),
        (20, 4, 'member', TRUE), (40, 7, 'member', FALSE);
    INSERT INTO task_projects (id) VALUES (10), (20), (30), (40);
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
    name = 'task_board_roles_test_' + uuid.uuid4().hex[:10]
    with _cursor('public') as base:
        base.execute('CREATE SCHEMA ' + name)
    try:
        with _cursor(name) as cursor:
            cursor.execute(BASE_DDL)
            yield cursor
    finally:
        with _cursor('public') as base:
            base.execute('DROP SCHEMA %s CASCADE' % name)




def test_executors_are_marked_people_without_the_fired(cur):
    # Исполнитель — любого уровня видимости; участник и уволенный — нет.
    assert DB._task_project_assignable_ids_tx(cur, HR) == {ANNA, BORIS}
    assert DB._task_project_assignable_ids_tx(cur, FIN) == {VERA}
    # Доска без людей — None: задачу поручают как раньше.
    assert DB._task_project_assignable_ids_tx(cur, EMPTY) is None
    assert DB._task_project_assignable_ids_tx(cur, None) is None
    # Люди есть, исполнителей нет — пустое множество, а не «как раньше».
    assert DB._task_project_assignable_ids_tx(cur, WATCH) == set()


def test_rule_on_real_roster(cur):
    DB._require_task_project_assignees_tx(cur, HR, [ANNA, BORIS])
    with pytest.raises(ValueError, match='ASSIGNEE_NOT_PROJECT_EXECUTOR'):
        DB._require_task_project_assignees_tx(cur, HR, [ANNA, VERA])
    with pytest.raises(ValueError, match='ASSIGNEE_NOT_PROJECT_EXECUTOR'):
        DB._require_task_project_assignees_tx(cur, HR, [FIRED])
    # Участник доски в её составе, но исполнителем его не сделать.
    with pytest.raises(ValueError, match='ASSIGNEE_NOT_PROJECT_EXECUTOR'):
        DB._require_task_project_assignees_tx(cur, HR, [ANNA, DINA])
    # Доска с людьми без исполнителей — никому.
    with pytest.raises(ValueError, match='ASSIGNEE_NOT_PROJECT_EXECUTOR'):
        DB._require_task_project_assignees_tx(cur, WATCH, [DINA])
    with pytest.raises(ValueError, match='ASSIGNEE_NOT_PROJECT_EXECUTOR'):
        DB._require_task_project_assignees_tx(cur, WATCH, [VERA])
    # Доска без людей — как раньше, но больше десяти — нет.
    DB._require_task_project_assignees_tx(cur, EMPTY, [BORIS, VERA])
    with pytest.raises(ValueError, match='TOO_MANY_ASSIGNEES'):
        DB._require_task_project_assignees_tx(cur, EMPTY, list(range(100, 111)))
    # Правка задачи 300: Глеб не из состава, но уже в задаче — не перепроверяется.
    DB._require_task_project_assignees_tx(cur, HR, [GLEB, ANNA], current_ids=[GLEB, ANNA])


def test_everyone_on_a_big_board_is_one_task(cur):
    """Двенадцать исполнителей доски — одной задачей: потолок десяти их не касается."""
    people = list(range(100, 112))
    cur.execute('INSERT INTO users (id, status) SELECT g, %s FROM unnest(%s::int[]) AS g', ('working', people))
    DB._replace_task_project_members_tx(cur, FIN, [
        {'user_id': person_id, 'access_level': 'member', 'is_executor': True} for person_id in people
    ], ADMIN)
    DB._require_task_project_assignees_tx(cur, FIN, people)
    with pytest.raises(ValueError, match='ASSIGNEE_NOT_PROJECT_EXECUTOR'):
        DB._require_task_project_assignees_tx(cur, FIN, people + [ANNA])


def _roles(cur, project_id):
    cur.execute('SELECT user_id, access_level, is_executor FROM task_project_members'
                ' WHERE project_id = %s ORDER BY user_id', (project_id,))
    return [tuple(row) for row in cur.fetchall()]


def test_saving_the_roster_writes_the_role_only_when_sent(cur):
    """Окно «Проекты» шлёт роль каждому; старая вкладка без роли её не сбрасывает."""
    # Роль прислали — пишется; новый без роли — участник (умолчание колонки).
    DB._replace_task_project_members_tx(cur, HR, [
        {'user_id': ANNA, 'access_level': 'member', 'is_executor': False},
        {'user_id': BORIS, 'access_level': 'member'},
        {'user_id': DINA, 'access_level': 'board', 'is_executor': True},
        {'user_id': VERA, 'access_level': 'member'},
    ], ADMIN)
    assert _roles(cur, HR) == [
        (ANNA, 'member', False),   # исполнителя сделали участником
        (BORIS, 'member', True),   # без поля — роль прежняя, а уровень сменился
        (VERA, 'member', False),   # новый без поля — участник
        (DINA, 'board', True),     # участника сделали исполнителем «только свои»
    ]
    # Уволенного сняли с доски — строка ушла, как и прежде.
    assert FIRED not in {row[0] for row in _roles(cur, HR)}
    assert DB._task_project_assignable_ids_tx(cur, HR) == {BORIS, DINA}
    # Повтор того же состава ничего не меняет.
    DB._replace_task_project_members_tx(cur, HR, [
        {'user_id': ANNA, 'access_level': 'member', 'is_executor': False},
        {'user_id': BORIS, 'access_level': 'member', 'is_executor': True},
        {'user_id': DINA, 'access_level': 'board', 'is_executor': True},
        {'user_id': VERA, 'access_level': 'member', 'is_executor': False},
    ], ADMIN)
    assert _roles(cur, HR) == [(ANNA, 'member', False), (BORIS, 'member', True),
                               (VERA, 'member', False), (DINA, 'board', True)]


def test_extra_assignable_on_real_sql(cur):
    """Сверх получателей: нынешний состав задачи и исполнители доски — тем, кто ставит на неё."""
    DB._get_cursor = lambda: contextlib.nullcontext(cur)
    try:
        # Борис — исполнитель HR с «только свои»: исполнители доски ему доступны, участница Дина — нет.
        assert DB.get_task_extra_assignable_ids(BORIS, 'sv', project_id=HR) == {ANNA, BORIS}
        # Дина — участник HR: ставит задачи на доску, исполнителям их поручает.
        assert DB.get_task_extra_assignable_ids(DINA, 'sv', project_id=HR) == {ANNA, BORIS}
        # Вера не в составе HR — обхода нет; админ раздела — есть.
        assert DB.get_task_extra_assignable_ids(VERA, 'sv', project_id=HR) == set()
        assert DB.get_task_extra_assignable_ids(VERA, 'admin', project_id=HR) == {ANNA, BORIS}
        # Доска без исполнителей ничего не добавляет, доска без людей — тоже (и не падает на None).
        assert DB.get_task_extra_assignable_ids(DINA, 'sv', project_id=WATCH) == set()
        assert DB.get_task_extra_assignable_ids(VERA, 'admin', project_id=EMPTY) == set()
        # Правка задачи 300: её нынешний состав остаётся, даже без доступа к доске.
        assert DB.get_task_extra_assignable_ids(VERA, 'sv', task_id=300) == {GLEB, ANNA}
        assert DB.get_task_extra_assignable_ids(BORIS, 'sv', task_id=300) == {GLEB, ANNA, BORIS}
        # Доска в архиве — новых через неё не ставят.
        cur.execute('UPDATE task_projects SET is_archived = TRUE WHERE id = %s', (HR,))
        assert DB.get_task_extra_assignable_ids(BORIS, 'sv', task_id=300) == {GLEB, ANNA}
    finally:
        del DB._get_cursor
