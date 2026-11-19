# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: TASK_PROJECTS_TEST_PORT — порт локального Postgres.

Какие задачи бот присылает сразу после входа (19.11.2026): человек в составе исполнителей,
«Поставлена» или «Возвращена», не в бэклоге и не своя себе; ближний срок первым, всего —
до LIMIT. Настоящий метод Database (вытащен AST'ом из склейки database.py и db/*.py) на
живом курсоре поверх маленьких таблиц; CHECK статуса — из настоящей схемы db/schema.py.
Только 127.0.0.1, своя схема со случайным именем, в конце она удаляется.

    TASK_PROJECTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_task_waiting_digest_postgres.py

Люди и задачи выдуманы.
"""
import ast
import contextlib
import os
import re
import textwrap
import uuid
from datetime import datetime
from pathlib import Path

import pytest

import task_projects
from tests import source_cache

PORT = os.environ.get('TASK_PROJECTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (TASK_PROJECTS_TEST_PORT)')

ROOT = Path(__file__).resolve().parents[1]
DATABASE_SOURCE = source_cache.database_flat_path().read_text(encoding='utf-8-sig')
SCHEMA_SOURCE = (ROOT / 'db' / 'schema.py').read_text(encoding='utf-8')

TASK_STATUS_CHECK = re.search(
    r"CREATE TABLE IF NOT EXISTS tasks \(.*?status VARCHAR\(32\) NOT NULL DEFAULT 'assigned' "
    r"CHECK \(status IN \(([^)]*)\)\)", SCHEMA_SOURCE, flags=re.S).group(1)

ME, BOSS, OTHER = 10, 20, 30

BASE_DDL = f'''
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL);
    CREATE TABLE task_projects (id integer PRIMARY KEY, code varchar(16) NOT NULL);
    CREATE TABLE tasks (
        id integer PRIMARY KEY,
        subject varchar(255) NOT NULL,
        status varchar(32) NOT NULL DEFAULT 'assigned' CHECK (status IN ({TASK_STATUS_CHECK})),
        priority varchar(16) NOT NULL DEFAULT 'normal',
        due_at timestamp,
        created_by integer,
        is_backlog boolean NOT NULL DEFAULT FALSE,
        project_id integer,
        project_number integer,
        created_at timestamp NOT NULL
    );
    CREATE TABLE task_assignees (task_id integer NOT NULL, user_id integer NOT NULL,
                                 position integer NOT NULL DEFAULT 0, PRIMARY KEY (task_id, user_id));
    INSERT INTO users (id, name) VALUES ({ME}, 'Исполнитель Тестовый'), ({BOSS}, 'Постановщик Тестовый'),
                                        ({OTHER}, 'Коллега Тестовый');
    INSERT INTO task_projects (id, code) VALUES (9, 'pr');
'''


def _build_database():
    module = source_cache.parse(DATABASE_SOURCE)
    database_class = next(node for node in module.body
                          if isinstance(node, ast.ClassDef) and node.name == 'Database')
    node = next(item for item in database_class.body
                if isinstance(item, ast.FunctionDef) and item.name == 'get_tasks_waiting_for_assignee')
    lines = DATABASE_SOURCE.splitlines(keepends=True)
    method = textwrap.dedent(''.join(lines[node.lineno - 1:node.end_lineno]))
    namespace = {'contextlib': contextlib, 'task_projects': task_projects}
    exec('class FakeDatabase:\n'
         '    _cursor = None\n'
         '    def _get_cursor(self):\n'
         '        return contextlib.nullcontext(self._cursor)\n'
         + textwrap.indent(method, '    '), namespace)
    return namespace['FakeDatabase']()


DB = _build_database()


@contextlib.contextmanager
def _cursor(schema):
    import psycopg2
    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                            password=os.environ.get('PGPASSWORD'), connect_timeout=3)
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
    name = 'waiting_digest_test_' + uuid.uuid4().hex[:10]
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


def _task(cur, task_id, *, status='assigned', assignees=(ME,), creator=BOSS, backlog=False, due=None,
          created=datetime(2026, 11, 19, 12, 0), priority='normal', project=None, number=None):
    cur.execute(
        'INSERT INTO tasks (id, subject, status, priority, due_at, created_by, is_backlog, project_id,'
        ' project_number, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
        (task_id, f'Задача {task_id}', status, priority, due, creator, backlog, project, number, created))
    for position, user_id in enumerate(assignees):
        cur.execute('INSERT INTO task_assignees (task_id, user_id, position) VALUES (%s, %s, %s)',
                    (task_id, user_id, position))


def test_only_tasks_that_wait_for_the_person(cur):
    _task(cur, 1)                                        # поставлена — ждёт
    _task(cur, 2, status='returned')                     # возвращена — ждёт
    _task(cur, 3, status='in_progress')                  # уже в работе — знает
    _task(cur, 4, status='completed')
    _task(cur, 5, status='accepted')
    _task(cur, 6, backlog=True)                          # бэклог исполнителя не тревожит
    _task(cur, 7, creator=ME)                            # своя себе — знает и так
    _task(cur, 8, creator=None)                          # постановщика нет (удалён) — ждёт
    _task(cur, 9, assignees=(OTHER,))                    # чужая
    _task(cur, 10, assignees=(OTHER, ME))                # соисполнитель — ждёт
    digest = DB.get_tasks_waiting_for_assignee(ME, limit=10)
    assert digest['total'] == 4
    assert sorted(item['id'] for item in digest['items']) == [1, 2, 8, 10]
    assert DB.get_tasks_waiting_for_assignee(OTHER)['total'] == 2  # 9 и 10


def test_nearest_deadline_first_then_newest_and_limit_keeps_the_total(cur):
    # Задачи со сроком — самые старые: и «новые первыми» поверх срока, и срок по убыванию
    # дали бы другой порядок.
    _task(cur, 1, created=datetime(2026, 11, 18, 9, 0))
    _task(cur, 2, created=datetime(2026, 11, 19, 9, 0))
    _task(cur, 3, due=datetime(2026, 11, 30, 18, 0), created=datetime(2026, 11, 16, 9, 0))
    _task(cur, 4, due=datetime(2026, 11, 21, 18, 0), created=datetime(2026, 11, 15, 9, 0))
    digest = DB.get_tasks_waiting_for_assignee(ME, limit=3)
    assert [item['id'] for item in digest['items']] == [4, 3, 2]
    assert digest['total'] == 4


def test_item_fields_for_the_message(cur):
    _task(cur, 1, project=9, number=11, priority='urgent', due=datetime(2026, 11, 25, 18, 0), status='returned')
    [item] = DB.get_tasks_waiting_for_assignee(ME)['items']
    assert item == {
        'id': 1, 'subject': 'Задача 1', 'status': 'returned', 'priority': 'urgent',
        'due_at': datetime(2026, 11, 25, 18, 0), 'key': 'PR-11', 'creator_name': 'Постановщик Тестовый',
    }


def test_nobody_waiting_gives_zero(cur):
    _task(cur, 1, status='in_progress')
    assert DB.get_tasks_waiting_for_assignee(ME) == {'total': 0, 'items': []}
