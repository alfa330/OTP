# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: SESSION_NOTIFY_TEST_PORT — порт локального Postgres.

Тумблер «Уведомления о доступе» в разделе «Сессии» (владелец, 24.11.2026): строка в
`session_access_notify_optin` — включено, выключение строку удаляет. Здесь на настоящем
Postgres сверяются методы `Database` и DDL таблицы — оба берутся из кода как есть
(DDL вырезается из db/schema.py), своих копий в тесте нет.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема внутри одной
транзакции, в конце откат. Люди выдуманы.

    SESSION_NOTIFY_TEST_PORT=55441 python -m pytest tests/test_session_access_notify_postgres.py
"""
import ast
import os
import re
import textwrap
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests import source_cache

PORT = os.environ.get('SESSION_NOTIFY_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (SESSION_NOTIFY_TEST_PORT)')

ROOT = Path(__file__).resolve().parents[1]
SOURCE = source_cache.database_flat_path().read_text(encoding='utf-8-sig')
_LINES = SOURCE.splitlines(keepends=True)
METHODS = ('_session_access_notify_state', 'get_session_access_notify_state',
           'set_session_access_notify', 'list_session_access_notify_recipients')


def _method(name):
    database_class = next(node for node in source_cache.parse(SOURCE).body
                          if isinstance(node, ast.ClassDef) and node.name == 'Database')
    node = next(item for item in database_class.body
                if isinstance(item, ast.FunctionDef) and item.name == name)
    namespace = {}
    exec(textwrap.dedent(''.join(_LINES[node.lineno - 1:node.end_lineno])), namespace)
    return namespace[name]


def _optin_ddl():
    schema = (ROOT / 'db' / 'schema.py').read_text(encoding='utf-8')
    match = re.search(r'CREATE TABLE IF NOT EXISTS session_access_notify_optin \(.*?\n\s*\);',
                      schema, flags=re.S)
    assert match, 'DDL таблицы подписки не найден в db/schema.py'
    return match.group(0)


@pytest.fixture
def db():
    import psycopg2

    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user=os.environ.get('PGUSER', 'postgres'),
                            dbname='postgres', password=os.environ.get('PGPASSWORD'))
    cur = conn.cursor()
    schema = 't_session_notify_' + uuid.uuid4().hex
    cur.execute('CREATE SCHEMA ' + schema)
    cur.execute('SET LOCAL search_path TO ' + schema)
    cur.execute('''
        CREATE TABLE users (id integer PRIMARY KEY, name text, role text, status text,
                            telegram_id bigint);
        INSERT INTO users (id, name, role, status, telegram_id) VALUES
            (1, 'Супер Админов', 'super_admin', 'working', 9001),
            (2, 'Админ Без Статуса', 'admin', NULL, 9002),
            (3, 'Админ Без Телеграма', 'admin', 'working', NULL),
            (4, 'Админ Уволенный', 'admin', 'fired', 9004),
            (5, 'Админ Увольняется', 'admin', 'dismissal', 9005),
            (6, 'Бывший Админ', 'operator', 'working', 9006),
            (7, 'Админ В Отпуске', 'admin', 'vacation', 9007);
    ''')
    cur.execute(_optin_ddl())

    class Double:
        @contextmanager
        def _get_cursor(self):
            yield cur

    for name in METHODS:
        setattr(Double, name, _method(name))
    double = Double()
    double.cursor = cur
    try:
        yield double
    finally:
        conn.rollback()
        conn.close()


def _rows(cur):
    cur.execute('SELECT user_id FROM session_access_notify_optin ORDER BY user_id')
    return [row[0] for row in cur.fetchall()]


def test_off_by_default_for_everyone(db):
    for user_id in (1, 2, 3):
        assert db.get_session_access_notify_state(user_id)['enabled'] is False
    assert db.list_session_access_notify_recipients() == []


def test_switching_on_is_one_row_however_many_times(db):
    first = db.set_session_access_notify(1, True)
    second = db.set_session_access_notify(1, True)
    assert first == second == {'enabled': True, 'telegram_linked': True}
    assert _rows(db.cursor) == [1]
    db.cursor.execute('SELECT enabled_at IS NOT NULL FROM session_access_notify_optin')
    assert db.cursor.fetchone()[0], 'время включения ставит сама база'


def test_switching_off_deletes_the_row(db):
    db.set_session_access_notify(1, True)
    assert db.set_session_access_notify(1, False) == {'enabled': False, 'telegram_linked': True}
    assert _rows(db.cursor) == [], '«не подписан» — отсутствие строки, а не FALSE'
    assert db.set_session_access_notify(1, False)['enabled'] is False, 'повторное выключение не ошибка'


def test_state_says_whether_telegram_is_linked(db):
    assert db.set_session_access_notify(3, True) == {'enabled': True, 'telegram_linked': False}
    assert db.get_session_access_notify_state(2) == {'enabled': False, 'telegram_linked': True}


def test_toggles_are_personal(db):
    db.set_session_access_notify(1, True)
    assert db.get_session_access_notify_state(2)['enabled'] is False
    db.set_session_access_notify(2, True)
    db.set_session_access_notify(1, False)
    assert _rows(db.cursor) == [2]


def test_recipients_are_working_subscribers_with_telegram(db):
    for user_id in (1, 2, 3, 4, 5, 6, 7):
        db.set_session_access_notify(user_id, True)
    recipients = db.list_session_access_notify_recipients()
    # Без Telegram (3), уволенные (4) и увольняющиеся (5) — нет. Роль сверяет
    # вызывающий тем же правилом, что пускает в раздел, поэтому бывший админ (6)
    # приходит со своей ролью, а отпуск (7) отправке не мешает.
    assert recipients == [
        {'user_id': 1, 'role': 'super_admin', 'telegram_id': 9001},
        {'user_id': 2, 'role': 'admin', 'telegram_id': 9002},
        {'user_id': 6, 'role': 'operator', 'telegram_id': 9006},
        {'user_id': 7, 'role': 'admin', 'telegram_id': 9007},
    ]


def test_unsubscribed_admins_are_not_recipients(db):
    db.set_session_access_notify(2, True)
    assert [r['user_id'] for r in db.list_session_access_notify_recipients()] == [2]


def test_unknown_user_reads_as_off(db):
    assert db.get_session_access_notify_state(999) == {'enabled': False, 'telegram_linked': False}


def test_deleted_user_takes_his_subscription_with_him(db):
    db.set_session_access_notify(2, True)
    db.cursor.execute('DELETE FROM users WHERE id = 2')
    assert _rows(db.cursor) == []


def test_subscription_of_a_missing_user_is_refused(db):
    import psycopg2

    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        db.set_session_access_notify(999, True)
