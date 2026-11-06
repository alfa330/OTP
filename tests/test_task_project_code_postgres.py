# -*- coding: utf-8 -*-
"""Смена ключа доски (владелец 06.11.2026) — на настоящем Postgres.

HR-125 становится HRD-125: номера задач остаются, прежние ключи уходят в
task_key_aliases и по-прежнему открывают задачи — ищет их тот же
Database._TASK_KEY_MATCH_SQL, что у ссылок ?task=HR-125, поиска и CLI. Чужой
нынешний ключ и ключ, которым звались задачи другой доски, взять нельзя.

Настоящие методы Database вытащены AST'ом из склейки database.py и db/*.py (как в
соседних тестах), курсор — живой, своя схема со случайным именем, в конце удаляется.

    TASK_PROJECTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_task_project_code_postgres.py
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
ADMIN = 1
HR, ACC = 10, 20

BASE_DDL = '''
    CREATE TABLE task_projects (
        id SERIAL PRIMARY KEY,
        code VARCHAR(10) NOT NULL UNIQUE CHECK (code ~ '^[A-Z][A-Z0-9]{1,9}$'),
        name VARCHAR(120) NOT NULL,
        department_id INTEGER,
        next_number INTEGER NOT NULL DEFAULT 1,
        is_archived BOOLEAN NOT NULL DEFAULT FALSE,
        created_by INTEGER,
        updated_at TIMESTAMP NOT NULL DEFAULT now()
    );
    CREATE TABLE tasks (id INTEGER PRIMARY KEY, subject TEXT,
                        project_id INTEGER REFERENCES task_projects(id), project_number INTEGER);
    CREATE TABLE task_key_aliases (
        code VARCHAR(10) NOT NULL, number INTEGER NOT NULL,
        task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        project_id INTEGER REFERENCES task_projects(id) ON DELETE SET NULL,
        created_at TIMESTAMP NOT NULL DEFAULT now(),
        PRIMARY KEY (code, number)
    );
    INSERT INTO task_projects (id, code, name, next_number) VALUES (10, 'HR', 'HR отдел', 4), (20, 'ACC', 'Бухгалтерия', 8);
    -- HR-1, HR-3 на доске HR; HR-2 перенесли в бухгалтерию (стала ACC-7, старый ключ — в псевдонимах).
    INSERT INTO tasks VALUES (1, 'Подбор', 10, 1), (3, 'Адаптация', 10, 3), (2, 'Перенесённая', 20, 7),
                             (5, 'Акт сверки', 20, 5), (9, 'Без проекта', NULL, NULL);
    INSERT INTO task_key_aliases (code, number, task_id, project_id) VALUES ('HR', 2, 2, 10);
'''

METHODS = ['create_task_project', 'update_task_project', '_rename_task_project_code_tx', '_task_projects_admin']
CLASS_ATTRS = ['_TASK_KEY_MATCH_SQL']
MODULE_NAMES = {'normalize_role_value', 'role_has_min', 'ROLE_ALIASES', 'ROLE_HIERARCHY', '_UNSET'}

STUBS = '''
@contextlib.contextmanager
def _get_cursor(self):
    yield self.cursor

def _normalize_task_project_department_tx(self, cursor, department_id):
    return int(department_id) if department_id else None

def _replace_task_project_members_tx(self, cursor, project_id, members, added_by):
    return []

def _replace_task_project_sections_tx(self, cursor, project_id, sections):
    return []
'''


def _build_database_class():
    module = source_cache.parse(DATABASE_SOURCE)
    namespace = {'contextlib': contextlib, 'task_projects': task_projects, 'Optional': Optional, 'List': List}
    for node in module.body:
        names = ([node.name] if isinstance(node, ast.FunctionDef)
                 else [t.id for t in getattr(node, 'targets', []) if isinstance(t, ast.Name)])
        if set(names) & MODULE_NAMES:
            exec(ast.get_source_segment(DATABASE_SOURCE, node), namespace)
    database_class = next(node for node in module.body
                          if isinstance(node, ast.ClassDef) and node.name == 'Database')
    lines = DATABASE_SOURCE.splitlines(keepends=True)
    parts = {}
    for node in database_class.body:
        if isinstance(node, ast.FunctionDef) and node.name in METHODS:
            # С декоратором: _task_projects_admin — staticmethod.
            first = min([node.lineno] + [item.lineno for item in node.decorator_list])
            parts[node.name] = textwrap.dedent(''.join(lines[first - 1:node.end_lineno]))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in CLASS_ATTRS:
                    parts[target.id] = textwrap.dedent(ast.get_source_segment(DATABASE_SOURCE, node))
    missing = set(METHODS + CLASS_ATTRS) - set(parts)
    assert not missing, f'в Database нет: {sorted(missing)}'
    body = ''.join(textwrap.indent(parts[name], '    ') + '\n' for name in CLASS_ATTRS + METHODS)
    exec('class FakeDatabase:\n' + body + textwrap.indent(STUBS, '    '), namespace)
    return namespace['FakeDatabase']


FakeDatabase = _build_database_class()


@contextlib.contextmanager
def _connection(schema):
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
def db():
    name = 'task_project_code_' + uuid.uuid4().hex[:10]
    with _connection('public') as base:
        base.execute('CREATE SCHEMA ' + name)
    try:
        with _connection(name) as cursor:
            cursor.execute(BASE_DDL)
            database = FakeDatabase()
            database.cursor = cursor
            yield database
    finally:
        with _connection('public') as base:
            base.execute('DROP SCHEMA %s CASCADE' % name)


def _by_key(db, key):
    """Задачи по ключу — тем же условием, что у ссылки ?task=HR-125."""
    code, number = task_projects.parse_task_key(key)
    db.cursor.execute(f'SELECT t.id FROM tasks t WHERE {db._TASK_KEY_MATCH_SQL} ORDER BY t.id',
                      (code, number, code, number))
    return [row[0] for row in db.cursor.fetchall()]


def _code(db, project_id):
    db.cursor.execute('SELECT code FROM task_projects WHERE id = %s', (project_id,))
    return db.cursor.fetchone()[0]


def test_rename_keeps_numbers_and_old_keys_open_the_same_tasks(db):
    result = db.update_task_project(HR, ADMIN, 'admin', code='hrd')
    assert result['code'] == 'HRD' and result['previous_code'] == 'HR'
    assert _code(db, HR) == 'HRD'
    # Новые ключи — те же номера.
    assert _by_key(db, 'HRD-1') == [1]
    assert _by_key(db, 'HRD-3') == [3]
    # Старые ссылки ведут туда же, а HR-2 — по-прежнему к перенесённой в бухгалтерию.
    assert _by_key(db, 'HR-1') == [1]
    assert _by_key(db, 'hr-3') == [3]
    assert _by_key(db, 'HR-2') == [2]
    assert _by_key(db, 'HRD-2') == []


def test_same_code_and_unrelated_edits_write_no_aliases(db):
    db.update_task_project(HR, ADMIN, 'admin', code='HR', name='HR и кадры')
    db.cursor.execute('SELECT count(*) FROM task_key_aliases')
    assert db.cursor.fetchone()[0] == 1


def test_current_code_of_another_board_is_refused(db):
    with pytest.raises(ValueError, match='PROJECT_CODE_TAKEN'):
        db.update_task_project(HR, ADMIN, 'admin', code='ACC')
    assert _code(db, HR) == 'HR'


def test_code_that_named_another_boards_tasks_is_refused(db):
    # HR → HRD, затем бухгалтерия хочет ключ HR: её ACC-5 стала бы HR-5, а старая HR-1 — чужая.
    db.update_task_project(HR, ADMIN, 'admin', code='HRD')
    with pytest.raises(ValueError, match='PROJECT_CODE_USED_BEFORE'):
        db.update_task_project(ACC, ADMIN, 'admin', code='HR')
    with pytest.raises(ValueError, match='PROJECT_CODE_USED_BEFORE'):
        db.create_task_project(ADMIN, 'admin', code='HR', name='Новый HR')
    assert _by_key(db, 'HR-1') == [1]


def test_board_can_take_its_own_previous_code_back(db):
    db.update_task_project(HR, ADMIN, 'admin', code='HRD')
    db.update_task_project(HR, ADMIN, 'admin', code='HR')
    assert _code(db, HR) == 'HR'
    assert _by_key(db, 'HR-1') == [1]
    assert _by_key(db, 'HRD-1') == [1]
    # HR-2 — всё ещё перенесённая в бухгалтерию, а не что-то с доски HR.
    assert _by_key(db, 'HR-2') == [2]


def test_tasks_moved_away_keep_their_aliases_after_rename(db):
    # ACC → BUH: ACC-7 (бывшая HR-2) и ACC-5 открываются и по старым ключам.
    db.update_task_project(ACC, ADMIN, 'admin', code='BUH')
    assert _by_key(db, 'ACC-7') == [2]
    assert _by_key(db, 'ACC-5') == [5]
    assert _by_key(db, 'BUH-7') == [2]
    assert _by_key(db, 'HR-2') == [2]


def test_invalid_code_and_rights(db):
    with pytest.raises(ValueError, match='INVALID_PROJECT_CODE'):
        db.update_task_project(HR, ADMIN, 'admin', code='1HR')
    with pytest.raises(PermissionError, match='PROJECT_MANAGE_FORBIDDEN'):
        db.update_task_project(HR, 7, 'sv', code='HRD')
    assert _code(db, HR) == 'HR'


def test_alias_without_owner_counts_as_foreign(db):
    # Строки, записанные до колонки project_id (стенды #401), — чей ключ, неизвестно:
    # такой ключ не отдаём никому, лучше отказ, чем две задачи по одной ссылке.
    db.cursor.execute("INSERT INTO task_key_aliases (code, number, task_id) VALUES ('OLD', 1, 5)")
    with pytest.raises(ValueError, match='PROJECT_CODE_USED_BEFORE'):
        db.update_task_project(ACC, ADMIN, 'admin', code='OLD')


@pytest.fixture
def shared_schema():
    """Схема с закоммиченными таблицами — для двух соединений разом."""
    name = 'task_project_race_' + uuid.uuid4().hex[:10]
    with _connection('public') as base:
        base.execute('CREATE SCHEMA ' + name)
    with _connection(name) as cursor:
        cursor.execute(BASE_DDL)
    try:
        yield name
    finally:
        with _connection('public') as base:
            base.execute('DROP SCHEMA %s CASCADE' % name)


def _open(schema):
    import psycopg2
    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres', connect_timeout=3)
    cur = conn.cursor()
    cur.execute("SET statement_timeout TO '10s'")
    cur.execute('SET search_path TO ' + schema)
    database = FakeDatabase()
    database.cursor = cur
    return conn, database


def test_key_changes_take_turns(shared_schema):
    """Пока доска HR переименовывается в HRD, новая доска «HR» ждёт и получает отказ.

    Без замка вторая транзакция не видела бы ещё не записанных псевдонимов HR-1, HR-3
    и забрала бы ключ HR — старая ссылка HR-1 и новая HR-1 вели бы к разным задачам.
    """
    import threading
    first_conn, first = _open(shared_schema)
    second_conn, second = _open(shared_schema)
    outcome = {}

    def create_hr():
        try:
            second.create_task_project(ADMIN, 'admin', code='HR', name='Новый HR')
            outcome['result'] = 'created'
        except ValueError as error:
            outcome['result'] = str(error)
        finally:
            second_conn.rollback()

    try:
        first.update_task_project(HR, ADMIN, 'admin', code='HRD')   # замок взят, не закоммичено
        racer = threading.Thread(target=create_hr)
        racer.start()
        racer.join(timeout=1.0)
        assert racer.is_alive(), 'вторая транзакция не ждала первую'
        first_conn.commit()
        racer.join(timeout=8)
        assert outcome.get('result') == 'PROJECT_CODE_USED_BEFORE'
    finally:
        first_conn.close()
        second_conn.close()
