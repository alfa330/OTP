"""Порядок разворота схемы раздела: таблицы → ALTER'ы → индексы.

Стоит здесь не из любви к порядку. 17.08.2026 выкат раздела «Обращения» положил
прод: индекс по новому столбцу выполнился РАНЬШЕ, чем ALTER TABLE этот столбец
добавил. Весь разворот идёт под SAVEPOINT, поэтому откатились все миграции
разом, API отдавал 500, а схема осталась старой. На пустой базе (и в любом
тесте, который просто вызывает init) ошибка не воспроизводится — проверяется
именно ПОРЯДОК инструкций.
"""

import re

from oktell_guard.schema import init_oktell_guard_schema


class OrderCursor:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=None):
        # Комментарии режем ДО склейки: '--' действует до конца строки, и в
        # однострочном виде он спрятал бы за собой часть DDL — как от самой
        # проверки, так и от любого кода, который нормализует SQL.
        without_comments = ' '.join(part.split('--')[0] for part in str(sql).splitlines())
        self.statements.append(' '.join(without_comments.split()))

    def fetchone(self):
        return None

    def fetchall(self):
        return []


def statements():
    cursor = OrderCursor()
    init_oktell_guard_schema(cursor)
    return cursor.statements


def test_every_index_comes_after_its_table():
    seen_tables = set()
    for sql in statements():
        created = re.search(r'CREATE TABLE IF NOT EXISTS (\w+)', sql, re.IGNORECASE)
        if created:
            seen_tables.add(created.group(1).lower())
            continue
        index = re.search(r'CREATE (?:UNIQUE )?INDEX IF NOT EXISTS \w+ ON (\w+)', sql, re.IGNORECASE)
        if index:
            table = index.group(1).lower()
            assert table in seen_tables, f'индекс по таблице {table} создаётся раньше самой таблицы'


def test_index_columns_exist_by_that_moment():
    """Каждый столбец из индекса к этому моменту уже объявлен — в CREATE TABLE
    или в более раннем ALTER ... ADD COLUMN. Ровно это и сломалось на проде."""
    known: dict[str, set] = {}
    for sql in statements():
        created = re.search(r'CREATE TABLE IF NOT EXISTS (\w+) \((.*)\)', sql, re.IGNORECASE | re.DOTALL)
        if created:
            table = created.group(1).lower()
            body = created.group(2)
            columns = set(re.findall(r'(?:^|,)\s*(\w+)\s+[A-Z]', body))
            known[table] = {c.lower() for c in columns}
            continue
        altered = re.search(r'ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS (\w+)', sql, re.IGNORECASE)
        if altered:
            known.setdefault(altered.group(1).lower(), set()).add(altered.group(2).lower())
            continue
        index = re.search(r'CREATE (?:UNIQUE )?INDEX IF NOT EXISTS \w+\s+ON (\w+)\s*\((.*?)\)', sql, re.IGNORECASE)
        if index:
            table = index.group(1).lower()
            for column in re.findall(r'\b(\w+)\b', index.group(2)):
                token = column.lower()
                if token in ('lower', 'desc', 'asc'):
                    continue
                assert token in known.get(table, set()), (
                    f'индекс по {table}.{token} создаётся раньше, чем появляется столбец'
                )


def test_settings_row_is_inserted_after_its_table():
    order = statements()
    table_at = next(i for i, s in enumerate(order) if 'CREATE TABLE IF NOT EXISTS oktell_guard_settings' in s)
    insert_at = next(i for i, s in enumerate(order) if 'INSERT INTO oktell_guard_settings' in s)
    assert table_at < insert_at


def test_schema_is_idempotent_by_construction():
    """Init вызывается при каждом старте — все инструкции обязаны быть
    безопасными для повторного выполнения."""
    for sql in statements():
        if sql.upper().startswith('CREATE TABLE'):
            assert 'IF NOT EXISTS' in sql.upper()
        if 'CREATE INDEX' in sql.upper() or 'CREATE UNIQUE INDEX' in sql.upper():
            assert 'IF NOT EXISTS' in sql.upper()
        if sql.upper().startswith('INSERT INTO'):
            assert 'ON CONFLICT' in sql.upper()
