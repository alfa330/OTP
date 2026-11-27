# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: IT_TICKET_TEST_PORT — порт локального Postgres.

Заявка в IT из «Задач» (владелец 27.11.2026). Настоящие методы Database
(склейка database.py и db/*.py из tests/source_cache) на живом курсоре:
  • is_task_helpdesk_member — кто из людей доски ITH пользуется ИИ заявок;
  • record_it_ticket/list_it_tickets — строка журнала помнит задачу;
  • колонка task_id заводится той же строкой миграции, что в db/schema.py.
Только 127.0.0.1, своя схема со случайным именем, в конце удаляется.

    IT_TICKET_TEST_PORT=55437 python -m pytest tests/test_it_ticket_helpdesk_postgres.py

Люди выдуманы.
"""
import ast
import contextlib
import json
import os
import re
import textwrap
import uuid

import pytest

import task_projects
from tests import source_cache

PORT = os.environ.get('IT_TICKET_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (IT_TICKET_TEST_PORT)')

DATABASE_SOURCE = source_cache.database_flat_path().read_text(encoding='utf-8-sig')
SCHEMA_SOURCE = (source_cache.ROOT / 'db' / 'schema.py').read_text(encoding='utf-8-sig')

SV, OPERATOR, DEV, STRANGER = 1, 2, 3, 4
ITH, BE, OLD_ITH = 10, 11, 12

BASE_DDL = f'''
    CREATE TABLE task_projects (id integer PRIMARY KEY, code text NOT NULL, name text NOT NULL,
                                is_archived boolean NOT NULL DEFAULT FALSE);
    CREATE TABLE task_project_members (project_id integer NOT NULL, user_id integer NOT NULL,
                                       access_level text NOT NULL DEFAULT 'member',
                                       is_executor boolean NOT NULL DEFAULT FALSE,
                                       PRIMARY KEY (project_id, user_id));
    -- Как до 27.11.2026: журнал без task_id — колонку заводит миграция ниже.
    CREATE TABLE it_tickets (
        id SERIAL PRIMARY KEY,
        profile VARCHAR(16) NOT NULL DEFAULT 'op',
        category VARCHAR(255), subcategory VARCHAR(255), priority VARCHAR(16),
        title VARCHAR(500), body TEXT NOT NULL,
        fields JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        channel_id INTEGER, channel_chat_id BIGINT, channel_title VARCHAR(255),
        telegram_message_id BIGINT,
        status VARCHAR(16) NOT NULL DEFAULT 'sent',
        created_by INTEGER, created_by_name VARCHAR(255),
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    -- Ключ доски с пробелами и строчными — сверка обязана его узнать; старая
    -- доска ITH в архиве никого не пускает.
    INSERT INTO task_projects (id, code, name, is_archived) VALUES
        ({ITH}, ' ith ', 'IT Helpdesk', FALSE),
        ({BE}, 'BE', 'IT разработка — Backend', FALSE);
    INSERT INTO task_project_members (project_id, user_id, access_level, is_executor) VALUES
        ({ITH}, {SV}, 'board', FALSE),
        ({ITH}, {OPERATOR}, 'member', TRUE),
        ({BE}, {DEV}, 'member', TRUE);
'''

NAMES = ['is_task_helpdesk_member', 'record_it_ticket', 'list_it_tickets']


def _schema_alter():
    """Строка миграции task_id ровно как в db/schema.py."""
    match = re.search(r'ALTER TABLE it_tickets ADD COLUMN IF NOT EXISTS task_id[^;]*;', SCHEMA_SOURCE)
    assert match, 'в db/schema.py нет миграции it_tickets.task_id'
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
    missing = set(NAMES) - set(body)
    assert not missing, f'в Database нет: {sorted(missing)}'
    namespace = {'json': json, 'task_projects': task_projects, 'contextlib': contextlib}
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
    name = 'it_helpdesk_test_' + uuid.uuid4().hex[:10]
    with _cursor('public') as base:
        base.execute('CREATE SCHEMA ' + name)
    try:
        with _cursor(name) as cursor:
            cursor.execute(BASE_DDL)
            cursor.execute(_schema_alter())
            # Второй старт пода: миграция на готовой схеме — пустая операция.
            cursor.execute(_schema_alter())
            DB._cursor = cursor
            yield cursor
    finally:
        DB._cursor = None
        with _cursor('public') as base:
            base.execute('DROP SCHEMA %s CASCADE' % name)


def test_only_helpdesk_board_people_count(cur):
    assert DB.is_task_helpdesk_member(SV) is True, 'уровень «видит только свои» — тоже человек доски'
    assert DB.is_task_helpdesk_member(OPERATOR) is True
    assert DB.is_task_helpdesk_member(DEV) is False, 'человек другой доски'
    assert DB.is_task_helpdesk_member(STRANGER) is False


def test_archived_helpdesk_lets_nobody_in(cur):
    cur.execute('UPDATE task_projects SET is_archived = TRUE WHERE id = %s', (ITH,))
    assert DB.is_task_helpdesk_member(OPERATOR) is False


def test_renamed_board_is_no_longer_the_helpdesk(cur):
    cur.execute("UPDATE task_projects SET code = 'ITS' WHERE id = %s", (ITH,))
    assert DB.is_task_helpdesk_member(OPERATOR) is False


def test_journal_row_remembers_the_task(cur):
    row = DB.record_it_ticket(
        profile='szov', category='Телефония и Oktell', subcategory='Нет исходящих', priority='high',
        title='Нет исходящих, РМ 12', body='Тема: Телефония\n\n🔧 Что: нет исходящих',
        fields={'workplace': 'РМ 12 (Тех поддержка)'}, channel_id=None, channel_chat_id=None,
        channel_title=None, telegram_message_id=None, status='task', created_by=SV,
        created_by_name='А Супервайзер', task_id=501,
    )
    assert row['id'] > 0
    old = DB.record_it_ticket(
        profile='op', category='', subcategory='', priority='medium', title='', body='старая заявка в канал',
        fields={}, channel_id=None, channel_chat_id=-100, channel_title='Тест', telegram_message_id=77,
        status='sent', created_by=SV, created_by_name='А Супервайзер',
    )
    items = {item['id']: item for item in DB.list_it_tickets(limit=10)}
    assert items[row['id']]['task_id'] == 501
    assert items[row['id']]['status'] == 'task'
    assert items[old['id']]['task_id'] is None, 'у старых заявок задачи нет'
    cur.execute('SELECT fields FROM it_tickets WHERE id = %s', (row['id'],))
    assert cur.fetchone()[0] == {'workplace': 'РМ 12 (Тех поддержка)'}
