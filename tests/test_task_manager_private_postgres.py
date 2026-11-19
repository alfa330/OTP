# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: TASK_PROJECTS_TEST_PORT — порт локального Postgres.

«Задачи»: сотрудник маркетинга не видит задачи своего руководителя, к которым сам не
причастен (решение владельца 19.11.2026). Раньше «Участник» доски видел всю доску, и
сотрудник маркетинга читал на доске MKT личные задачи главы отдела.

Руководитель — глава отдела смотрящего и «Руководитель» из его карточки
(users.supervisor_id). Задача руководителя — где он постановщик, поручитель или любой
из исполнителей (владелец выбрал прятать и задачи, которые руководитель дал коллегам).
Свои задачи и те, где он наблюдатель, сотрудник видит по-прежнему. Другим отделам
правило не нужно: участник доски HR видит задачи главы HR, как и раньше.

Настоящие фрагменты и методы Database (вытащены AST'ом из склейки database.py и
db/*.py, как в соседних *_postgres тестах) на живом курсоре поверх маленьких таблиц:
только 127.0.0.1, своя схема со случайным именем, в конце она удаляется.

    TASK_PROJECTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_task_manager_private_postgres.py

Люди выдуманы.
"""
import ast
import contextlib
import os
import textwrap
import uuid
from typing import List, Optional

import pytest

import task_projects
from tests import source_cache

PORT = os.environ.get('TASK_PROJECTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (TASK_PROJECTS_TEST_PORT)')

DATABASE_SOURCE = source_cache.database_flat_path().read_text(encoding='utf-8-sig')

# Отделы: маркетинг, его вторая запись с кодом « Marketing » (регистр и пробелы — SQL
# обязан узнать код так же, как гард), HR — контрольный отдел без правила.
MARKETING, MARKETING_UNTRIMMED, HR = 50, 51, 60
# Глава маркетинга (admin, возглавляет обе записи маркетинга); сотрудники маркетинга:
# A — «Руководитель» в карточке тот же глава, B — без «Руководителя» (руководитель —
# только глава отдела), C — «Руководитель» в карточке другой человек (BOSS);
# D — из записи « Marketing »; BOSS — суперадмин вне маркетинга; DEV — исполнитель
# не из маркетинга; глава HR и кадровик HR; ADMIN — админ раздела.
HEAD, EMP_A, EMP_B, EMP_C, EMP_D, BOSS, DEV, HR_HEAD, HR_EMP, ADMIN = 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
MKT_BOARD, HR_BOARD = 100, 200

BASE_DDL = f'''
    CREATE TABLE departments (id integer PRIMARY KEY, code text NOT NULL, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, role text NOT NULL, department_id integer,
                        supervisor_id integer);
    CREATE TABLE tasks (id integer PRIMARY KEY, created_by integer, assigned_to integer NOT NULL,
                        requested_by_id integer, project_id integer, project_number integer,
                        section_id integer);
    CREATE TABLE task_assignees (task_id integer NOT NULL, user_id integer NOT NULL,
                                 position integer NOT NULL DEFAULT 0);
    CREATE TABLE task_projects (id integer PRIMARY KEY, code text NOT NULL);
    CREATE TABLE task_project_members (project_id integer NOT NULL, user_id integer NOT NULL,
                                       access_level text NOT NULL, added_by integer,
                                       PRIMARY KEY (project_id, user_id));
    CREATE TABLE task_watchers (task_id integer NOT NULL, user_id integer NOT NULL,
                                PRIMARY KEY (task_id, user_id));
    INSERT INTO departments (id, code, name, head_user_id) VALUES
        ({MARKETING}, 'marketing', 'Маркетинг', {HEAD}),
        ({MARKETING_UNTRIMMED}, ' Marketing ', 'Маркетинг (старая запись)', {HEAD}),
        ({HR}, 'hr', 'HR', {HR_HEAD});
    INSERT INTO users (id, role, department_id, supervisor_id) VALUES
        ({HEAD}, 'admin', {MARKETING}, NULL),
        ({EMP_A}, 'marketing_manager', {MARKETING}, {HEAD}),
        ({EMP_B}, 'marketing_manager', {MARKETING}, NULL),
        ({EMP_C}, 'marketing_manager', {MARKETING}, {BOSS}),
        ({EMP_D}, 'marketing_manager', {MARKETING_UNTRIMMED}, NULL),
        ({BOSS}, 'super_admin', NULL, NULL),
        ({DEV}, 'admin', NULL, NULL),
        ({HR_HEAD}, 'admin', {HR}, NULL),
        ({HR_EMP}, 'hr_manager', {HR}, {HR_HEAD}),
        ({ADMIN}, 'admin', NULL, NULL);
    INSERT INTO task_projects (id, code) VALUES ({MKT_BOARD}, 'MKT'), ({HR_BOARD}, 'HR');
    -- Как на проде: глава и все сотрудники маркетинга — «Участники» доски MKT.
    -- Сотрудник A ещё и участник доски HR: глава HR ему не руководитель.
    INSERT INTO task_project_members (project_id, user_id, access_level) VALUES
        ({MKT_BOARD}, {HEAD}, 'member'), ({MKT_BOARD}, {EMP_A}, 'member'),
        ({MKT_BOARD}, {EMP_B}, 'member'), ({MKT_BOARD}, {EMP_C}, 'member'),
        ({MKT_BOARD}, {EMP_D}, 'member'),
        ({HR_BOARD}, {HR_HEAD}, 'member'), ({HR_BOARD}, {HR_EMP}, 'member'),
        ({HR_BOARD}, {EMP_A}, 'member');
    -- Доска MKT:
    -- 301 — глава себе (его личная задача); 302 — глава сотруднику A;
    -- 303 — глава человеку не из маркетинга; 304 — сотрудник B главе;
    -- 305 — B коллеге C (между коллегами); 306 — B поставил C, глава — второй
    --       исполнитель (только в составе task_assignees, больше нигде);
    -- 307 — B поставил C, поручитель — глава; 308 — глава себе, A — наблюдатель;
    -- 309 — BOSS себе (руководитель только для C); 310 — B поставил главе, состава
    --       ещё нет (окно деплоя: исполнитель только в колонке assigned_to);
    -- 311 — постановщик удалён (created_by NULL), исполнитель B.
    -- Доска HR: 401 — глава HR себе.
    INSERT INTO tasks (id, created_by, assigned_to, requested_by_id, project_id, project_number) VALUES
        (301, {HEAD}, {HEAD}, NULL, {MKT_BOARD}, 1),
        (302, {HEAD}, {EMP_A}, NULL, {MKT_BOARD}, 2),
        (303, {HEAD}, {DEV}, NULL, {MKT_BOARD}, 3),
        (304, {EMP_B}, {HEAD}, NULL, {MKT_BOARD}, 4),
        (305, {EMP_B}, {EMP_C}, NULL, {MKT_BOARD}, 5),
        (306, {EMP_B}, {EMP_C}, NULL, {MKT_BOARD}, 6),
        (307, {EMP_B}, {EMP_C}, {HEAD}, {MKT_BOARD}, 7),
        (308, {HEAD}, {HEAD}, NULL, {MKT_BOARD}, 8),
        (309, {BOSS}, {BOSS}, NULL, {MKT_BOARD}, 9),
        (310, {EMP_B}, {HEAD}, NULL, {MKT_BOARD}, 10),
        (311, NULL, {EMP_B}, NULL, {MKT_BOARD}, 11),
        (401, {HR_HEAD}, {HR_HEAD}, NULL, {HR_BOARD}, 1);
    INSERT INTO task_assignees (task_id, user_id, position) VALUES
        (301, {HEAD}, 0), (302, {EMP_A}, 0), (303, {DEV}, 0), (304, {HEAD}, 0),
        (305, {EMP_C}, 0), (306, {EMP_C}, 0), (306, {HEAD}, 1), (307, {EMP_C}, 0),
        (308, {HEAD}, 0), (309, {BOSS}, 0), (311, {EMP_B}, 0), (401, {HR_HEAD}, 0);
    INSERT INTO task_watchers (task_id, user_id) VALUES (308, {EMP_A});
'''

MKT_TASKS = list(range(301, 312))

NAMES = [
    '_TASK_ASSIGNEE_EXISTS_SQL', '_TASK_COLLEAGUE_SV_SQL', '_TASK_PROJECT_MEMBER_SQL', '_TASK_WATCHER_SQL',
    '_normalize_task_department_filter', '_task_department_condition',
    '_normalize_task_project_filter', '_normalize_task_section_filter', '_task_scope_filters',
    '_task_observable_tx', '_task_assignee_tuples', '_task_visible_for_requester',
]
MODULE_NAMES = {
    'normalize_role_value', 'role_has_min', 'ROLE_ALIASES', 'ROLE_HIERARCHY', '_UNSET',
    'BACK_OFFICE_EMPLOYEE_ROLES', 'TASK_EMPLOYEE_ROLES', 'TASK_PERSONAL_SCOPE_ROLES',
    'TASK_MANAGER_PRIVATE_DEPARTMENT_CODES', '_TASK_MANAGER_PRIVATE_SQL',
}


def _build_database():
    module = source_cache.parse(DATABASE_SOURCE)
    namespace = {'Optional': Optional, 'List': List, 'task_projects': task_projects}
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
    name = 'task_manager_private_' + uuid.uuid4().hex[:10]
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


def test_marketing_employee_does_not_see_tasks_of_the_department_head(cur):
    """A: свои (302), наблюдает (308), между коллегами (305, 311), задача чужого
    руководителя (309) и доска HR (401: глава HR ему не руководитель) — видны.
    Задачи, где глава постановщик (301, 303), исполнитель (304, 310 — по колонке,
    306 — по составу) или поручитель (307), — нет."""
    assert _visible(cur, EMP_A, 'marketing_manager') == [302, 305, 308, 309, 311, 401]


def test_department_head_is_the_manager_even_without_the_card_field(cur):
    """B без «Руководителя» в карточке: руководитель — глава отдела. Задачи, которые B
    поставил сам (304, 305, 306, 307, 310), видны, даже если в них есть глава."""
    assert _visible(cur, EMP_B, 'marketing_manager') == [304, 305, 306, 307, 309, 310, 311]


def test_card_manager_is_hidden_too(cur):
    """C: в карточке «Руководитель» — BOSS, глава отдела тоже его руководитель."""
    assert _visible(cur, EMP_C, 'marketing_manager') == [305, 306, 307, 311]


def test_department_code_is_matched_without_case_and_spaces(cur):
    assert _visible(cur, EMP_D, 'marketing_manager') == [305, 309, 311]


def test_head_still_sees_the_whole_board(cur):
    """Глава сам себе не руководитель; в «Задачах» он идёт под 'sv'."""
    assert _visible(cur, HEAD, 'sv') == MKT_TASKS


def test_head_follows_the_same_rule_towards_his_own_card_manager(cur):
    """Правило буквальное и для главы: «Руководитель» в его карточке — его руководитель.
    Задачу BOSS на доске MKT, в которой глава не участвует (309), он не видит; задачи,
    где участвует сам, и задачи сотрудников без BOSS — видит, как раньше."""
    cur.execute('UPDATE users SET supervisor_id = %s WHERE id = %s', (BOSS, HEAD))
    assert _visible(cur, HEAD, 'sv') == [task for task in MKT_TASKS if task != 309]


def test_other_departments_keep_the_whole_board(cur):
    """Правило — только для маркетинга: кадровик видит задачу главы HR на доске HR."""
    assert _visible(cur, HR_EMP, 'hr_manager') == [401]
    assert _visible(cur, ADMIN, 'admin') == MKT_TASKS + [401]


def test_board_filter_counts_the_same(cur):
    """Счётчик доски в переключателе и выбранная доска — тем же базовым охватом."""
    assert _visible(cur, EMP_A, 'marketing_manager', project_id=str(MKT_BOARD)) == [302, 305, 308, 309, 311]


def test_card_by_link_follows_the_list(cur):
    """Что не видно в списке, не открывается по ссылке: отчёты, уточнения, файлы и фото
    читают через _task_observable_tx. Участник задачи сюда не доходит — его пускает
    _task_visible_for_requester, поэтому проверяем только задачи, где смотрящий не участник."""
    viewers = [(EMP_A, 'marketing_manager'), (EMP_B, 'marketing_manager'), (EMP_C, 'marketing_manager'),
               (EMP_D, 'marketing_manager'), (HR_EMP, 'hr_manager')]
    cur.execute('SELECT id, created_by, requested_by_id, assigned_to FROM tasks ORDER BY id')
    tasks = cur.fetchall()
    cur.execute('SELECT task_id, user_id FROM task_assignees')
    assignees = {}
    for task_id, user_id in cur.fetchall():
        assignees.setdefault(task_id, []).append(user_id)
    checked = 0
    for viewer, role in viewers:
        visible = set(_visible(cur, viewer, role))
        for task_id, created_by, requested_by, assigned_to in tasks:
            people = assignees.get(task_id, []) + [assigned_to]
            if DB._task_visible_for_requester(role, viewer, created_by, people, requested_by):
                continue
            expected = task_id in visible
            assert DB._task_observable_tx(cur, role, viewer, task_id, created_by) is expected, (viewer, task_id)
            checked += 1
    assert checked >= 30


def test_rule_cuts_only_the_board_member_path(cur):
    """Сняли с задачи главы наблюдателя — она у него пропала; передали задачу главы
    сотруднику — у него она видна, у коллеги по-прежнему нет."""
    cur.execute('DELETE FROM task_watchers WHERE task_id = 308')
    assert 308 not in _visible(cur, EMP_A, 'marketing_manager')
    cur.execute('UPDATE tasks SET assigned_to = %s WHERE id = 301', (EMP_B,))
    cur.execute('UPDATE task_assignees SET user_id = %s WHERE task_id = 301', (EMP_B,))
    assert 301 in _visible(cur, EMP_B, 'marketing_manager')
    assert 301 not in _visible(cur, EMP_A, 'marketing_manager')
