# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: TASK_PROJECTS_TEST_PORT — порт локального Postgres.

Правка задачи не снимает отметок чек-листа (HR-2, 24.11.2026). Здесь — что
_sync_task_checklist_items_tx на деле делает со строками task_checklist_items:
тот же чек-лист не трогает ни одной строки; узнанный пункт остаётся той же строкой
(id, отметка, кто и когда отметил, итог); перестановка проходит мимо
UNIQUE (task_id, position); новый пункт — без отметки, убранный — удалён.
Таблица — настоящая DDL из db/schema.py, методы — настоящие методы Database
(вытащены AST'ом из склейки database.py и db/*.py). Только 127.0.0.1, своя схема
со случайным именем, в конце она удаляется.

    TASK_PROJECTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_task_checklist_sync_postgres.py
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

import task_checklist
from tests import source_cache

PORT = os.environ.get('TASK_PROJECTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (TASK_PROJECTS_TEST_PORT)')

ROOT = Path(__file__).resolve().parents[1]
DATABASE_SOURCE = source_cache.database_flat_path().read_text(encoding='utf-8-sig')
SCHEMA_SOURCE = (ROOT / 'db' / 'schema.py').read_text(encoding='utf-8')


def _schema_ddl():
    create = re.search(r'(CREATE TABLE IF NOT EXISTS task_checklist_items \(.*?\);)', SCHEMA_SOURCE, flags=re.S)
    alter = re.search(r'(ALTER TABLE task_checklist_items ADD COLUMN IF NOT EXISTS result_note TEXT;)', SCHEMA_SOURCE)
    assert create and alter
    return create.group(1) + '\n' + alter.group(1)


BASE_DDL = '''
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL);
    CREATE TABLE tasks (id integer PRIMARY KEY);
    INSERT INTO users (id, name) VALUES (2, 'А Отметивший'), (40, 'Б Постановщик');
    INSERT INTO tasks (id) VALUES (458), (459);
''' + _schema_ddl()

NAMES = ['_normalize_task_checklist_items', '_insert_task_checklist_items', '_sync_task_checklist_items_tx']
HR2 = ['диспетчерская', 'бэкенд', 'корп почта', 'платрум', 'iCore', 'обходной', 'прокси', 'акт приема-передачи']
DONE_AT = datetime(2026, 11, 24, 12, 59, 32)
CREATED_AT = datetime(2026, 11, 23, 9, 25, 42)


def _build_database():
    from psycopg2.extras import execute_values
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
    namespace = {'task_checklist': task_checklist, 'execute_values': execute_values}
    exec('class FakeDatabase:\n' + ''.join(textwrap.indent(body[name], '    ') + '\n' for name in NAMES), namespace)
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
    name = 'checklist_sync_test_' + uuid.uuid4().hex[:10]
    with _cursor('public') as base:
        base.execute('CREATE SCHEMA ' + name)
    try:
        with _cursor(name) as cursor:
            cursor.execute(BASE_DDL)
            yield cursor
    finally:
        with _cursor('public') as base:
            base.execute('DROP SCHEMA %s CASCADE' % name)


def _rows(cur, task_id=458):
    cur.execute('''
        SELECT id, title, position, is_required, is_done, completed_by, completed_at, result_note,
               created_at, updated_at
        FROM task_checklist_items WHERE task_id = %s ORDER BY position
    ''', (task_id,))
    return [dict(zip(('id', 'title', 'position', 'is_required', 'is_done', 'completed_by', 'completed_at',
                      'result_note', 'created_at', 'updated_at'), row)) for row in cur.fetchall()]


def _hr2(cur):
    """Чек-лист HR-2 как на проде 24.11.2026: восемь пунктов, четыре отмечены, у одного итог."""
    DB._insert_task_checklist_items(cur, 458, [{'title': title} for title in HR2])
    cur.execute("UPDATE task_checklist_items SET created_at = %s, updated_at = %s WHERE task_id = 458",
                (CREATED_AT, CREATED_AT))
    cur.execute('''
        UPDATE task_checklist_items SET is_done = TRUE, completed_by = 2, completed_at = %s, updated_at = %s
        WHERE task_id = 458 AND title IN ('диспетчерская', 'бэкенд', 'платрум', 'iCore')
    ''', (DONE_AT, DONE_AT))
    cur.execute("UPDATE task_checklist_items SET result_note = 'доступ закрыт' WHERE title = 'iCore'")
    # Чужая задача с тем же названием пункта — правка HR-2 её не касается.
    DB._insert_task_checklist_items(cur, 459, [{'title': 'iCore'}])
    return _rows(cur)


def test_same_checklist_without_ids_touches_no_row(cur):
    before = _hr2(cur)
    other_before = _rows(cur, 459)
    # Старая форма: тот же чек-лист строками, без id — так его слала правка «добавить исполнителя».
    assert DB._sync_task_checklist_items_tx(cur, 458, [{'title': title, 'is_required': True} for title in HR2]) is False
    assert _rows(cur) == before
    assert _rows(cur, 459) == other_before


def test_same_checklist_with_ids_touches_no_row(cur):
    before = _hr2(cur)
    items = [{'id': row['id'], 'title': row['title'], 'is_required': True} for row in before]
    assert DB._sync_task_checklist_items_tx(cur, 458, items) is False
    assert _rows(cur) == before


def test_edit_keeps_marks_renames_adds_and_removes(cur):
    before = {row['title']: row for row in _hr2(cur)}
    items = [
        {'id': before['диспетчерская']['id'], 'title': 'диспетчерская'},
        {'id': before['бэкенд']['id'], 'title': 'бэкенд и база'},        # переименован, отмечен
        {'id': before['корп почта']['id'], 'title': 'корп почта'},
        {'title': 'платрум'},                                             # без id — по названию
        {'id': before['iCore']['id'], 'title': 'iCore', 'is_required': False},
        {'title': 'Telegram-бот'},                                        # новый
        {'id': before['прокси']['id'], 'title': 'прокси'},
        # «обходной» и «акт приема-передачи» убраны
    ]
    assert DB._sync_task_checklist_items_tx(cur, 458, items) is True
    after = _rows(cur)
    assert [row['title'] for row in after] == [
        'диспетчерская', 'бэкенд и база', 'корп почта', 'платрум', 'iCore', 'Telegram-бот', 'прокси']
    assert [row['position'] for row in after] == list(range(7))
    by_title = {row['title']: row for row in after}
    for title, old in (('диспетчерская', 'диспетчерская'), ('бэкенд и база', 'бэкенд'), ('платрум', 'платрум'),
                       ('iCore', 'iCore')):
        row, was = by_title[title], before[old]
        assert row['id'] == was['id']
        assert (row['is_done'], row['completed_by'], row['completed_at']) == (True, 2, DONE_AT)
        assert row['created_at'] == CREATED_AT
    assert by_title['iCore']['result_note'] == 'доступ закрыт'
    assert by_title['iCore']['is_required'] is False
    # updated_at — только у пунктов, где поменялись название или обязательность.
    assert by_title['диспетчерская']['updated_at'] == DONE_AT
    assert by_title['платрум']['updated_at'] == DONE_AT
    assert by_title['бэкенд и база']['updated_at'] > DONE_AT
    assert by_title['iCore']['updated_at'] > DONE_AT
    assert by_title['корп почта']['updated_at'] == CREATED_AT
    new = by_title['Telegram-бот']
    assert new['id'] not in {row['id'] for row in before.values()}
    assert (new['is_done'], new['completed_by'], new['completed_at']) == (False, None, None)
    cur.execute('SELECT count(*) FROM task_checklist_items WHERE id = ANY(%s)',
                ([before['обходной']['id'], before['акт приема-передачи']['id']],))
    assert cur.fetchone()[0] == 0
    assert [row['title'] for row in _rows(cur, 459)] == ['iCore']


def test_reverse_order_passes_unique_position(cur):
    before = _hr2(cur)
    reversed_items = [{'id': row['id'], 'title': row['title']} for row in reversed(before)]
    assert DB._sync_task_checklist_items_tx(cur, 458, reversed_items) is True
    after = _rows(cur)
    assert [row['id'] for row in after] == [row['id'] for row in reversed(before)]
    assert [row['position'] for row in after] == list(range(8))
    marks = {row['id']: (row['is_done'], row['completed_by'], row['completed_at'], row['result_note'])
             for row in before}
    assert {row['id']: (row['is_done'], row['completed_by'], row['completed_at'], row['result_note'])
            for row in after} == marks
    # Переезд на другое место — не правка пункта: updated_at прежний.
    assert {row['id']: row['updated_at'] for row in after} == {row['id']: row['updated_at'] for row in before}


def test_insert_into_middle_shifts_kept_items(cur):
    before = _hr2(cur)
    items = [{'id': row['id'], 'title': row['title']} for row in before]
    items.insert(0, {'title': 'первым делом'})
    assert DB._sync_task_checklist_items_tx(cur, 458, items) is True
    after = _rows(cur)
    assert [row['title'] for row in after] == ['первым делом', *HR2]
    assert [row['id'] for row in after[1:]] == [row['id'] for row in before]
    assert [row['position'] for row in after] == list(range(9))


def test_empty_list_removes_checklist_of_this_task_only(cur):
    _hr2(cur)
    assert DB._sync_task_checklist_items_tx(cur, 458, []) is True
    assert _rows(cur) == []
    assert [row['title'] for row in _rows(cur, 459)] == ['iCore']
    # Снова пусто — уже не правка.
    assert DB._sync_task_checklist_items_tx(cur, 458, []) is False


def test_foreign_task_item_id_is_not_taken(cur):
    _hr2(cur)
    foreign_id = _rows(cur, 459)[0]['id']
    assert DB._sync_task_checklist_items_tx(cur, 458, [{'id': foreign_id, 'title': 'чужой пункт'}]) is True
    assert [row['title'] for row in _rows(cur)] == ['чужой пункт']
    assert _rows(cur)[0]['id'] != foreign_id
    assert [row['title'] for row in _rows(cur, 459)] == ['iCore']


# ── Две правки чек-листа одной задачи разом (двойная отправка формы, две вкладки) ──
# Без блокировки задачи обе правки видели прежние пункты и вставляли новый на одно
# место: вторая падала на UNIQUE (task_id, position), человек получал 500.

@pytest.fixture
def committed_schema():
    """Схема с чек-листом HR-2, уже записанным: его видят другие соединения."""
    name = 'checklist_sync_conc_' + uuid.uuid4().hex[:10]
    with _cursor('public') as base:
        base.execute('CREATE SCHEMA ' + name)
    try:
        with _cursor(name) as cursor:
            cursor.execute(BASE_DDL)
            _hr2(cursor)
        yield name
    finally:
        with _cursor('public') as base:
            base.execute('DROP SCHEMA %s CASCADE' % name)


def _connection(schema):
    import psycopg2
    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres', connect_timeout=3)
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout TO '10s'")
        cur.execute('SET search_path TO ' + schema)
    conn.commit()
    return conn


def _race(schema, first_items, second_items):
    """Первая правка держит транзакцию, вторая стартует следом; возвращает итог второй."""
    import threading
    first, second = _connection(schema), _connection(schema)
    outcome = {}

    def run_second():
        try:
            with second.cursor() as cur:
                outcome['changed'] = DB._sync_task_checklist_items_tx(cur, 458, second_items)
            second.commit()
        except Exception as error:  # noqa: BLE001 — итог проверяет тест
            outcome['error'] = error
            second.rollback()

    try:
        with first.cursor() as cur:
            assert DB._sync_task_checklist_items_tx(cur, 458, first_items) is True
        thread = threading.Thread(target=run_second)
        thread.start()
        thread.join(1.0)
        outcome['waited'] = thread.is_alive()
        first.commit()
        thread.join(15)
        assert not thread.is_alive()
        return outcome
    finally:
        first.close()
        second.close()


def _final_titles(schema):
    with _cursor(schema) as cur:
        return [row['title'] for row in _rows(cur)], _rows(cur)


def test_double_submit_of_same_edit_takes_turns(committed_schema):
    items = [{'title': title} for title in HR2] + [{'title': 'пропуск'}]
    outcome = _race(committed_schema, items, items)
    assert 'error' not in outcome, outcome.get('error')
    # Вторая ждала первую и увидела её пункт: делать нечего.
    assert outcome == {'waited': True, 'changed': False}
    titles, rows = _final_titles(committed_schema)
    assert titles == [*HR2, 'пропуск']
    assert [row['position'] for row in rows] == list(range(9))
    assert sum(row['is_done'] for row in rows) == 4


def test_two_tabs_editing_checklist_take_turns(committed_schema):
    # Вкладка A добавила «пропуск», вкладка B, открытая раньше, — «ноутбук». Кто сохранил
    # последним, тот и прав (как у любого поля формы), но без 500 и без потери отметок.
    base = [{'title': title} for title in HR2]
    outcome = _race(committed_schema, base + [{'title': 'пропуск'}], base + [{'title': 'ноутбук'}])
    assert 'error' not in outcome, outcome.get('error')
    assert outcome == {'waited': True, 'changed': True}
    titles, rows = _final_titles(committed_schema)
    assert titles == [*HR2, 'ноутбук']
    assert [row['position'] for row in rows] == list(range(9))
    assert sum(row['is_done'] for row in rows) == 4
