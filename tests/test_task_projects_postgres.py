# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: TASK_PROJECTS_TEST_PORT — порт локального Postgres.

Проекты-доски «Задач» (задача #401). В tests/test_task_projects.py SQL виден
только маркерами, а что он ЗНАЧИТ — кто видит задачу в списке, кто читает её
отчёты и файлы, что находит ссылка по ключу — исполняется здесь: настоящие
фрагменты и методы Database (вытащены AST'ом, как в соседних тестах) на живом
курсоре поверх маленьких таблиц задач. Без импорта приложения и без боевых
учёток: только 127.0.0.1, своя схема со случайным именем, в конце она удаляется.

    TASK_PROJECTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_task_projects_postgres.py

Люди выдуманы.
"""
import ast
import contextlib
import os
import textwrap
import uuid
from pathlib import Path
from typing import List, Optional

import pytest

import task_projects
from tests import source_cache

PORT = os.environ.get('TASK_PROJECTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (TASK_PROJECTS_TEST_PORT)')

ROOT = Path(__file__).resolve().parents[1]
# Склейка database.py и миксинов db/*.py (tests/source_cache): класс Database разнесён 02.11.2026.
DATABASE_SOURCE = source_cache.database_flat_path().read_text(encoding='utf-8-sig')

# Люди: два СВ одного отдела, участник доски HR, «доска» HR, посторонний тренер, админ.
SV_AUTHOR, SV_COLLEAGUE, HR_MEMBER, HR_BOARD, OUTSIDER, ADMIN = 1, 2, 3, 4, 5, 6
HR, ACC = 10, 20

BASE_DDL = '''
    CREATE TABLE users (id integer PRIMARY KEY, role text NOT NULL, department_id integer);
    CREATE TABLE tasks (id integer PRIMARY KEY, created_by integer, assigned_to integer NOT NULL,
                        requested_by_id integer, project_id integer, project_number integer,
                        section_id integer);
    CREATE TABLE task_assignees (task_id integer NOT NULL, user_id integer NOT NULL);
    CREATE TABLE task_projects (id integer PRIMARY KEY, code text NOT NULL);
    CREATE TABLE task_project_members (project_id integer NOT NULL, user_id integer NOT NULL,
                                       access_level text NOT NULL, added_by integer,
                                       PRIMARY KEY (project_id, user_id));
    CREATE TABLE task_watchers (task_id integer NOT NULL, user_id integer NOT NULL, PRIMARY KEY (task_id, user_id));
    CREATE TABLE task_key_aliases (code text NOT NULL, number integer NOT NULL, task_id integer NOT NULL,
                                   PRIMARY KEY (code, number));
    INSERT INTO users (id, role, department_id) VALUES
        (1, 'sv', 100), (2, 'sv', 100), (3, 'hr_manager', 200), (4, 'hr_manager', 200),
        (5, 'trainer', 300), (6, 'admin', 300);
    INSERT INTO task_projects (id, code) VALUES (10, 'HR'), (20, 'ACC');
    INSERT INTO task_project_members (project_id, user_id, access_level) VALUES
        (10, 3, 'member'), (10, 4, 'board'), (20, 4, 'board');
    -- 101: задача проекта HR от СВ — конфиденциальная, коллега-СВ её не видит;
    -- 102: старая задача того же СВ без проекта — коллега видит (#298);
    -- 103: задача HR, где «доска» — наблюдатель; 104: задача ACC, где он наблюдатель,
    --      но на доске ACC его нет (сняли); 105: задача HR, поставленная ему самому.
    INSERT INTO tasks (id, created_by, assigned_to, requested_by_id, project_id, project_number) VALUES
        (101, 1, 3, NULL, 10, 1), (102, 1, 6, NULL, NULL, NULL), (103, 6, 3, NULL, 10, 2),
        (104, 6, 6, NULL, 20, 7), (105, 6, 4, NULL, 10, 3);
    INSERT INTO task_assignees (task_id, user_id) VALUES (101, 3), (102, 6), (103, 3), (104, 6), (105, 4);
    INSERT INTO task_watchers (task_id, user_id) VALUES (103, 4), (104, 5);
    -- Перенесённая задача: была HR-9, теперь ACC-7 (104).
    INSERT INTO task_key_aliases (code, number, task_id) VALUES ('HR', 9, 104);
    -- Раздел доски HR «Подбор» (id 1): в нём задачи 101 и 105.
    UPDATE tasks SET section_id = 1 WHERE id IN (101, 105);
'''

NAMES = [
    '_TASK_ASSIGNEE_EXISTS_SQL', '_TASK_COLLEAGUE_SV_SQL', '_TASK_PROJECT_MEMBER_SQL', '_TASK_WATCHER_SQL',
    '_TASK_KEY_MATCH_SQL', '_normalize_task_department_filter', '_task_department_condition',
    '_normalize_task_project_filter', '_normalize_task_section_filter', '_task_scope_filters',
    '_task_observable_tx', '_replace_task_project_members_tx',
]


def _build_database():
    module = source_cache.parse(DATABASE_SOURCE)
    namespace = {'Optional': Optional, 'List': List, 'task_projects': task_projects}
    wanted = {'normalize_role_value', 'role_has_min', 'ROLE_ALIASES', 'ROLE_HIERARCHY', '_UNSET',
              'BACK_OFFICE_EMPLOYEE_ROLES', 'TASK_PERSONAL_SCOPE_ROLES'}
    for node in module.body:
        names = ([node.name] if isinstance(node, ast.FunctionDef)
                 else [t.id for t in getattr(node, 'targets', []) if isinstance(t, ast.Name)])
        if set(names) & wanted:
            exec(ast.get_source_segment(DATABASE_SOURCE, node), namespace)
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
    name = 'task_projects_test_' + uuid.uuid4().hex[:10]
    with _cursor('public') as base:
        base.execute('CREATE SCHEMA ' + name)
    try:
        with _cursor(name) as cursor:
            cursor.execute(BASE_DDL)
            yield cursor
    finally:
        with _cursor('public') as base:
            base.execute('DROP SCHEMA %s CASCADE' % name)


def _visible(cur, viewer, role, **filters):
    conditions, params, _person_board, _rest = DB._task_scope_filters(viewer, role, **filters)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ''
    cur.execute(f'SELECT t.id FROM tasks t {where} ORDER BY t.id', tuple(params))
    return [row[0] for row in cur.fetchall()]


def test_member_sees_the_whole_board_and_board_level_only_own_and_watched(cur):
    # Участник видит всю доску HR, в том числе чужие задачи (105 поставлена «доске»).
    assert _visible(cur, HR_MEMBER, 'hr_manager') == [101, 103, 105]
    # 104 — наблюдатель, но доступа к доске ACC у него нет: задача не видна.
    assert _visible(cur, HR_BOARD, 'hr_manager') == [103, 105]
    assert _visible(cur, OUTSIDER, 'trainer') == []
    assert _visible(cur, ADMIN, 'admin') == [101, 102, 103, 104, 105]


def test_colleague_sv_path_stays_outside_projects(cur):
    """#298 — для задач без проекта; задачу проекта коллега-СВ не видит (#401)."""
    assert _visible(cur, SV_COLLEAGUE, 'sv') == [102]
    assert _visible(cur, SV_AUTHOR, 'sv') == [101, 102]


def test_project_filter_narrows_the_base_scope(cur):
    assert _visible(cur, ADMIN, 'admin', project_id=str(HR)) == [101, 103, 105]
    assert _visible(cur, ADMIN, 'admin', project_id='none') == [102]
    assert _visible(cur, HR_MEMBER, 'hr_manager', project_id=str(ACC)) == []


def test_section_filter_narrows_the_board(cur):
    """Разделы доски: фильтр по разделу и «Без раздела» — тем же базовым охватом."""
    assert _visible(cur, ADMIN, 'admin', project_id=str(HR), section_id='1') == [101, 105]
    assert _visible(cur, ADMIN, 'admin', project_id=str(HR), section_id='none') == [103]
    # Раздел не расширяет видимость: «доска» видит в разделе только своё.
    assert _visible(cur, HR_BOARD, 'hr_manager', section_id='1') == [105]


def test_read_path_matches_the_list(cur):
    """Что видно в списке, то и читается (отчёты, уточнения, файлы, фото), — и наоборот."""
    cases = [
        (HR_MEMBER, 'hr_manager', 101, True),
        (HR_BOARD, 'hr_manager', 103, True),
        (HR_BOARD, 'hr_manager', 101, False),
        (OUTSIDER, 'trainer', 104, False),   # наблюдатель без доступа к доске
        (SV_COLLEAGUE, 'sv', 102, True),     # коллега-СВ, задача без проекта
        (SV_COLLEAGUE, 'sv', 101, False),    # коллега-СВ, задача проекта
    ]
    for viewer, role, task_id, expected in cases:
        cur.execute('SELECT created_by FROM tasks WHERE id = %s', (task_id,))
        created_by = cur.fetchone()[0]
        assert DB._task_observable_tx(cur, role, viewer, task_id, created_by) is expected, (viewer, task_id)


def test_board_removal_closes_the_watched_task(cur):
    cur.execute('DELETE FROM task_project_members WHERE project_id = %s AND user_id = %s', (HR, HR_BOARD))
    assert _visible(cur, HR_BOARD, 'hr_manager') == [105]   # своя задача остаётся
    assert DB._task_observable_tx(cur, 'hr_manager', HR_BOARD, 103, 6) is False


def test_board_removal_drops_watches_of_this_board_only(cur):
    """Сняли человека с доски — его наблюдение за задачами ЭТОЙ доски снимается;
    за задачами других досок — нет (там решает их собственный состав)."""
    cur.execute('INSERT INTO task_watchers (task_id, user_id) VALUES (101, 5)')
    DB._replace_task_project_members_tx(cur, HR, [{'user_id': HR_MEMBER, 'access_level': 'member'}], ADMIN)
    cur.execute('SELECT task_id, user_id FROM task_watchers ORDER BY task_id, user_id')
    # 103 (HR, «доска» 4) и 101 (HR, посторонний 5) — сняты; 104 (ACC, 5) — на месте.
    assert cur.fetchall() == [(104, 5)]
    cur.execute('SELECT user_id, access_level FROM task_project_members WHERE project_id = %s', (HR,))
    assert cur.fetchall() == [(HR_MEMBER, 'member')]


def test_key_finds_current_and_previous_keys(cur):
    def by_key(code, number):
        cur.execute('SELECT t.id FROM tasks t WHERE ' + DB._TASK_KEY_MATCH_SQL + ' ORDER BY t.id',
                    (code, number, code, number))
        return [row[0] for row in cur.fetchall()]

    assert by_key('HR', 2) == [103]
    assert by_key('ACC', 7) == [104]
    assert by_key('HR', 9) == [104]   # прежний ключ перенесённой задачи
    assert by_key('HR', 7) == []      # номер чужого проекта — не наш
