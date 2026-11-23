# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: TASK_PROJECTS_TEST_PORT — порт локального Postgres.

«Задачи» рядовым IT (решение владельца 16.11.2026). В
tests/test_it_department_tasks_access.py гард и зеркала; здесь — что значит SQL
слоя данных: кому рядовой IT может поручить задачу, кто может поручить ему, что
он видит в списке и кого админ ставит на доску. Настоящие методы Database
(вытащены AST'ом из склейки database.py и db/*.py) на живом курсоре поверх
маленьких таблиц; только 127.0.0.1, своя схема со случайным именем, в конце она
удаляется.

    TASK_PROJECTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_it_department_tasks_postgres.py

Люди выдуманы.
"""
import ast
import contextlib
import os
import textwrap
import uuid
from collections import defaultdict
from typing import List, Optional

import pytest

import task_projects
from tests import source_cache

PORT = os.environ.get('TASK_PROJECTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (TASK_PROJECTS_TEST_PORT)')

DATABASE_SOURCE = source_cache.database_flat_path().read_text(encoding='utf-8-sig')

IT, SZOV, HR = 1933, 1, 1499
# Отдел с кодом « IT » (регистр и пробелы): гард сверяет код через strip().lower(),
# и SQL обязан узнать его так же.
IT_UNTRIMMED = 1934
# Глава IT (админ), рядовые IT, уволенный IT, оператор и СВ линии, кадровик, админ,
# рядовой отдела « IT ».
IT_HEAD, IT_OPERATOR, IT_TRAINEE, IT_FIRED, LINE_OPERATOR, LINE_SV, HR_MANAGER, ADMIN = 1, 2, 3, 4, 5, 6, 7, 8
IT_UNTRIMMED_OPERATOR = 9
ITH = 4

BASE_DDL = f'''
    CREATE TABLE departments (id integer PRIMARY KEY, code text NOT NULL, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL,
                        department_id integer, supervisor_id integer, status text,
                        avatar_bucket text, avatar_blob_path text);
    CREATE TABLE tasks (id integer PRIMARY KEY, created_by integer, assigned_to integer NOT NULL,
                        requested_by_id integer, project_id integer, project_number integer,
                        section_id integer);
    CREATE TABLE task_assignees (task_id integer NOT NULL, user_id integer NOT NULL,
                                 position integer NOT NULL DEFAULT 0);
    CREATE TABLE task_projects (id integer PRIMARY KEY, code text NOT NULL, name text NOT NULL,
                                department_id integer, is_archived boolean NOT NULL DEFAULT FALSE);
    CREATE TABLE task_project_members (project_id integer NOT NULL, user_id integer NOT NULL,
                                       access_level text NOT NULL, added_by integer,
                                       PRIMARY KEY (project_id, user_id));
    CREATE TABLE task_project_sections (id integer PRIMARY KEY, project_id integer NOT NULL,
                                        name text NOT NULL, position integer NOT NULL DEFAULT 0);
    CREATE TABLE task_watchers (task_id integer NOT NULL, user_id integer NOT NULL,
                                PRIMARY KEY (task_id, user_id));
    INSERT INTO departments (id, code, name, head_user_id) VALUES
        ({IT}, 'it', 'IT', {IT_HEAD}), ({SZOV}, 'szov', 'СЗоВ', NULL), ({HR}, 'hr', 'HR', NULL),
        ({IT_UNTRIMMED}, ' IT ', 'IT (старая запись)', NULL);
    -- У специалиста IT в карточке СВ линии — так бывает после перевода из СЗоВ;
    -- у оператора линии СВ свой.
    INSERT INTO users (id, name, role, department_id, supervisor_id, status) VALUES
        ({IT_HEAD}, 'А Глава', 'admin', {IT}, NULL, 'working'),
        ({IT_OPERATOR}, 'Б Специалист', 'operator', {IT}, {LINE_SV}, 'working'),
        ({IT_TRAINEE}, 'В Стажёр', 'trainee', {IT}, NULL, 'working'),
        ({IT_FIRED}, 'Г Уволенный', 'operator', {IT}, NULL, 'fired'),
        ({LINE_OPERATOR}, 'Д Оператор', 'operator', {SZOV}, {LINE_SV}, 'working'),
        ({LINE_SV}, 'Е Супервайзер', 'sv', {SZOV}, NULL, 'working'),
        ({HR_MANAGER}, 'Ж Кадровик', 'hr_manager', {HR}, NULL, 'working'),
        ({ADMIN}, 'З Админ', 'admin', {SZOV}, NULL, 'working'),
        ({IT_UNTRIMMED_OPERATOR}, 'И Специалист', 'operator', {IT_UNTRIMMED}, NULL, 'working');
    INSERT INTO task_projects (id, code, name, department_id) VALUES ({ITH}, 'ITH', 'IT Helpdesk', {IT});
    INSERT INTO task_project_members (project_id, user_id, access_level) VALUES
        ({ITH}, {IT_HEAD}, 'member'), ({ITH}, {IT_OPERATOR}, 'member');
    -- 201: задача доски ITH стажёру — специалист видит её участником доски;
    -- 202: админ поручил специалисту; 203: задача СВ линии себе — не его;
    -- 204: специалист сам поставил главе; 205: старая задача оператору линии
    -- (так поручали до списка получателей) — путь «СВ своего оператора».
    INSERT INTO tasks (id, created_by, assigned_to, requested_by_id, project_id, project_number) VALUES
        (201, {IT_HEAD}, {IT_TRAINEE}, NULL, {ITH}, 1),
        (202, {ADMIN}, {IT_OPERATOR}, NULL, NULL, NULL),
        (203, {LINE_SV}, {LINE_SV}, NULL, NULL, NULL),
        (204, {IT_OPERATOR}, {IT_HEAD}, {IT_OPERATOR}, NULL, NULL),
        (205, {ADMIN}, {LINE_OPERATOR}, NULL, NULL, NULL);
    INSERT INTO task_assignees (task_id, user_id) VALUES
        (201, {IT_TRAINEE}), (202, {IT_OPERATOR}), (203, {LINE_SV}), (204, {IT_HEAD}),
        (205, {LINE_OPERATOR});
'''

NAMES = [
    '_TASK_ASSIGNEE_EXISTS_SQL', '_TASK_COLLEAGUE_SV_SQL', '_TASK_PROJECT_MEMBER_SQL', '_TASK_WATCHER_SQL',
    '_normalize_task_department_filter', '_task_department_condition',
    '_normalize_task_project_filter', '_normalize_task_section_filter', '_task_scope_filters',
    '_task_projects_admin', 'get_task_recipients', 'get_task_projects',
    '_task_assignee_tuples', '_task_assignee_scope_tx', '_task_visible_for_requester',
]
MODULE_NAMES = {
    'normalize_role_value', 'role_has_min', 'ROLE_ALIASES', 'ROLE_HIERARCHY', '_UNSET',
    'BACK_OFFICE_EMPLOYEE_ROLES', 'TASK_EMPLOYEE_DEPARTMENT_CODES', 'TASK_EMPLOYEE_ROLES',
    'TASK_PERSONAL_SCOPE_ROLES', 'TASK_RECIPIENT_ROLES', '_TASK_RECIPIENT_ROLES_SQL',
    '_TASK_EMPLOYEE_SQL', 'TASK_MANAGER_PRIVATE_DEPARTMENT_CODES', '_TASK_MANAGER_PRIVATE_SQL',
    'TASK_OPEN_ASSIGNEE_DEPARTMENT_CODES', '_TASK_OPEN_ASSIGNEE_SQL',
}


def _build_database():
    module = source_cache.parse(DATABASE_SOURCE)
    namespace = {'Optional': Optional, 'List': List, 'task_projects': task_projects,
                 'defaultdict': defaultdict, 'contextlib': contextlib}
    for node in module.body:
        names = ([node.name] if isinstance(node, ast.FunctionDef)
                 else [t.id for t in getattr(node, 'targets', []) if isinstance(t, ast.Name)])
        if set(names) & MODULE_NAMES:
            exec(ast.get_source_segment(DATABASE_SOURCE, node), namespace)
    missing_module = MODULE_NAMES - set(namespace)
    assert not missing_module, f'в модуле нет: {sorted(missing_module)}'
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
    cursor_helper = (
        '_cursor = None\n'
        'def _get_cursor(self):\n'
        '    return contextlib.nullcontext(self._cursor)\n'
    )
    exec('class FakeDatabase:\n'
         + textwrap.indent(cursor_helper, '    ')
         + ''.join(textwrap.indent(body[name], '    ') + '\n' for name in NAMES),
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
    name = 'it_tasks_test_' + uuid.uuid4().hex[:10]
    with _cursor('public') as base:
        base.execute('CREATE SCHEMA ' + name)
    try:
        with _cursor(name) as cursor:
            cursor.execute(BASE_DDL)
            DB._cursor = cursor
            yield cursor
    finally:
        DB._cursor = None
        with _cursor('public') as base:
            base.execute('DROP SCHEMA %s CASCADE' % name)


def _recipients(viewer, role, scope_department_id):
    return sorted(person['id'] for person in DB.get_task_recipients(viewer, role, scope_department_id))


def _visible(cur, viewer, role):
    conditions, params, _person_board, _rest = DB._task_scope_filters(viewer, role)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ''
    cur.execute(f'SELECT t.id FROM tasks t {where} ORDER BY t.id', tuple(params))
    return [row[0] for row in cur.fetchall()]


def test_it_operator_assigns_within_it_and_to_admins(cur):
    # Гард отдаёт рядовому IT его роль и границу своего отдела; рядовой отдела
    # « IT » в списке — IT открыт всем отделам (TASK_OPEN_ASSIGNEE_DEPARTMENT_CODES).
    assert _recipients(IT_OPERATOR, 'operator', IT) == [
        IT_HEAD, IT_OPERATOR, IT_TRAINEE, ADMIN, IT_UNTRIMMED_OPERATOR,
    ]
    assert _recipients(IT_TRAINEE, 'trainee', IT) == [
        IT_HEAD, IT_OPERATOR, IT_TRAINEE, ADMIN, IT_UNTRIMMED_OPERATOR,
    ]


def test_head_and_admin_can_assign_to_it_staff(cur):
    # Глава IT идёт под 'sv' своего отдела (_effective_scoped_manager_role).
    assert _recipients(IT_HEAD, 'sv', IT) == [IT_HEAD, IT_OPERATOR, IT_TRAINEE, ADMIN, IT_UNTRIMMED_OPERATOR]
    # Админ — всех получателей компании; оператор линии и уволенный — нет. Рядовой
    # отдела с кодом « IT » — есть: код сверяется, как в гарде.
    assert _recipients(ADMIN, 'admin', None) == [
        IT_HEAD, IT_OPERATOR, IT_TRAINEE, LINE_SV, HR_MANAGER, ADMIN, IT_UNTRIMMED_OPERATOR,
    ]


def test_any_department_assigns_to_it_staff(cur):
    # 23.11.2026: IT открыт для поручений всем отделам (TASK_OPEN_ASSIGNEE_DEPARTMENT_CODES):
    # СВ линии и кадровик видят рядовых IT, и отдела « IT » тоже — код сверяется, как в
    # гарде. Уволенного IT нет, а граница чужих отделов цела: кадровик не видит СВ линии,
    # СВ линии — кадровика, оператора линии не видит никто.
    assert _recipients(LINE_SV, 'sv', SZOV) == [
        IT_HEAD, IT_OPERATOR, IT_TRAINEE, LINE_SV, ADMIN, IT_UNTRIMMED_OPERATOR,
    ]
    assert _recipients(HR_MANAGER, 'hr_manager', HR) == [
        IT_HEAD, IT_OPERATOR, IT_TRAINEE, HR_MANAGER, ADMIN, IT_UNTRIMMED_OPERATOR,
    ]


def test_it_operator_sees_own_tasks_and_his_board(cur):
    # 201 — участник доски ITH, 202 — исполнитель, 204 — постановщик; 203 — чужая.
    assert _visible(cur, IT_OPERATOR, 'operator') == [201, 202, 204]
    # Стажёр не в составе доски: видит только поручённую ему.
    assert _visible(cur, IT_TRAINEE, 'trainee') == [201]


def test_board_switcher_and_admin_candidates(cur):
    mine = DB.get_task_projects(IT_OPERATOR, 'operator')
    assert [(p['code'], p['access'], p['task_count']) for p in mine['projects']] == [('ITH', 'member', 1)]
    assert mine['can_manage'] is False
    assert mine['candidates'] == []
    # Админ ставит рядовых IT на доску; оператора линии и уволенного в списке нет.
    admin = DB.get_task_projects(ADMIN, 'admin')
    assert sorted(person['id'] for person in admin['candidates']) == [
        IT_HEAD, IT_OPERATOR, IT_TRAINEE, LINE_SV, HR_MANAGER, ADMIN, IT_UNTRIMMED_OPERATOR,
    ]


def test_supervisor_path_does_not_reach_it_staff(cur):
    # «СВ видит задачу своего оператора» (_task_visible_for_requester): рядовой IT
    # живёт по правилам бэк-офиса, и СВ из его карточки — тем более СВ чужого
    # отдела — по ссылке его задачу не открывает и не принимает.
    it_scope = DB._task_assignee_scope_tx(cur, 202)
    assert it_scope == [(IT_OPERATOR, 'operator', None)]
    assert DB._task_visible_for_requester('sv', LINE_SV, ADMIN, it_scope) is False
    # Прежнее поведение линии не тронуто: СВ своего оператора задачу видит.
    line_scope = DB._task_assignee_scope_tx(cur, 205)
    assert line_scope == [(LINE_OPERATOR, 'operator', LINE_SV)]
    assert DB._task_visible_for_requester('sv', LINE_SV, ADMIN, line_scope) is True
