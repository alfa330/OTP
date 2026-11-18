# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: TASK_PROJECTS_TEST_PORT — порт локального Postgres.

Карточки уволенных (владелец, 17.11.2026): суперадмин переносит карточку, где уволен
исполнитель, постановщик или поручитель (задача 332, 18.11), в любую колонку доски одним шагом. Здесь — что метод на деле
пишет в базу: статус и бэклог ровно той колонки, место в очереди, старт работы,
строку истории (её CHECK взят из настоящей схемы db/schema.py), и кому перенос
закрыт. Настоящие методы Database (вытащены AST'ом из склейки database.py и
db/*.py) на живом курсоре поверх маленьких таблиц; только 127.0.0.1, своя схема со
случайным именем, в конце она удаляется.

    TASK_PROJECTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_task_fired_move_postgres.py

Люди выдуманы.
"""
import ast
import contextlib
import os
import re
import textwrap
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pytest

from tests import source_cache

PORT = os.environ.get('TASK_PROJECTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (TASK_PROJECTS_TEST_PORT)')

ROOT = Path(__file__).resolve().parents[1]
DATABASE_SOURCE = source_cache.database_flat_path().read_text(encoding='utf-8-sig')
SCHEMA_SOURCE = (ROOT / 'db' / 'schema.py').read_text(encoding='utf-8')


def _schema_check(pattern):
    """Список значений CHECK из настоящей схемы: запись статуса, которого схема не
    знает, упала бы на проде при первом же переносе."""
    match = re.search(pattern, SCHEMA_SOURCE, flags=re.S)
    assert match, pattern
    return match.group(1)


TASK_STATUS_CHECK = _schema_check(
    r"CREATE TABLE IF NOT EXISTS tasks \(.*?status VARCHAR\(32\) NOT NULL DEFAULT 'assigned' CHECK \(status IN \(([^)]*)\)\)")
HISTORY_STATUS_CHECK = _schema_check(
    r"CREATE TABLE IF NOT EXISTS task_status_history \(.*?status_code VARCHAR\(32\) NOT NULL CHECK \(status_code IN \(([^)]*)\)\)")

SUPER, ADMIN, FIRED, LEAVING, ACTIVE, BOSS, CHIEF = 1, 2, 3, 4, 5, 6, 7

BASE_DDL = f'''
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL, status text);
    CREATE TABLE tasks (
        id integer PRIMARY KEY,
        status varchar(32) NOT NULL DEFAULT 'assigned' CHECK (status IN ({TASK_STATUS_CHECK})),
        created_by integer,
        requested_by_id integer,
        is_backlog boolean NOT NULL DEFAULT FALSE,
        backlog_rank double precision,
        started_at timestamp,
        completed_at timestamp,
        completed_by integer,
        completion_summary text,
        due_at timestamp,
        updated_at timestamp NOT NULL DEFAULT TIMESTAMP '2026-11-01 09:00:00'
    );
    CREATE TABLE task_action_reads (user_id integer NOT NULL, task_id integer NOT NULL,
                                    kind varchar(16) NOT NULL, seen_at timestamp NOT NULL,
                                    PRIMARY KEY (user_id, task_id));
    CREATE TABLE task_assignees (task_id integer NOT NULL, user_id integer NOT NULL,
                                 position integer NOT NULL DEFAULT 0);
    CREATE TABLE task_status_history (
        id serial PRIMARY KEY,
        task_id integer NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        status_code varchar(32) NOT NULL CHECK (status_code IN ({HISTORY_STATUS_CHECK})),
        changed_by integer,
        comment text,
        changed_at timestamp NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
    );
    -- Статусы пишут по-разному: «уволен» и «увольнение», бывает с пробелами и в другом регистре.
    INSERT INTO users (id, name, role, status) VALUES
        ({SUPER}, 'А Суперадмин', 'super_admin', 'working'),
        ({ADMIN}, 'Б Админ', 'admin', 'working'),
        ({FIRED}, 'В Уволенный', 'operator', ' Fired '),
        ({LEAVING}, 'Г Увольняется', 'operator', 'dismissal'),
        ({ACTIVE}, 'Д Работает', 'operator', 'working'),
        ({BOSS}, 'Е Уволенная СВ', 'sv', 'fired'),
        ({CHIEF}, 'Ж Постановщик', 'sv', 'working');
'''

NAMES = [
    '_TASK_FIRED_STATUSES', '_TASK_BOARD_COLUMN_STATES', 'TASK_FIRED_MOVE_COMMENTS',
    '_task_board_column_of', '_task_dt_to_iso', '_task_now', '_task_own_move_kind', 'move_fired_employee_task',
]
MODULE_NAMES = {'normalize_role_value', 'role_has_min', 'ROLE_ALIASES', 'ROLE_HIERARCHY'}

# Колонка доски → (статус, бэклог): таблица фронта (BOARD_COLUMN_STATE) и сервера.
COLUMNS = {
    'todo': ('assigned', False),
    'backlog': ('assigned', True),
    'progress': ('in_progress', False),
    'returned': ('returned', False),
    'review': ('completed', False),
    'done': ('accepted', False),
}


def _build_database():
    module = source_cache.parse(DATABASE_SOURCE)
    namespace = {'contextlib': contextlib, 'Optional': Optional, 'List': List, 'datetime': datetime}
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
    name = 'fired_move_test_' + uuid.uuid4().hex[:10]
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


def _task(cur, task_id, status='assigned', *, assignees=(FIRED,), is_backlog=False, rank=None, started_at=None,
          creator=CHIEF, requester=None, due_at=None):
    cur.execute(
        'INSERT INTO tasks (id, status, created_by, requested_by_id, is_backlog, backlog_rank, started_at, due_at)'
        ' VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
        (task_id, status, creator, requester, is_backlog, rank, started_at, due_at),
    )
    for position, user_id in enumerate(assignees):
        cur.execute('INSERT INTO task_assignees (task_id, user_id, position) VALUES (%s, %s, %s)',
                    (task_id, user_id, position))


def _state(cur, task_id):
    cur.execute('SELECT status, is_backlog, backlog_rank, started_at, updated_at FROM tasks WHERE id = %s', (task_id,))
    return cur.fetchone()


def _history(cur, task_id):
    cur.execute('SELECT status_code, changed_by, comment FROM task_status_history WHERE task_id = %s ORDER BY id',
                (task_id,))
    return cur.fetchall()


def test_schema_knows_every_target_status():
    # Перенос пишет статус колонки и в задачу, и в историю — оба CHECK обязаны их знать.
    for status, _backlog in COLUMNS.values():
        assert f"'{status}'" in TASK_STATUS_CHECK
        assert f"'{status}'" in HISTORY_STATUS_CHECK
    assert DB._TASK_BOARD_COLUMN_STATES == COLUMNS


def test_every_column_from_every_column(cur):
    # Из каждой колонки в каждую другую: карточка встаёт ровно в ту, куда её бросили.
    starts = [(column, state) for column, state in COLUMNS.items()]
    task_id = 100
    for from_column, (from_status, from_backlog) in starts:
        for to_column, (to_status, to_backlog) in COLUMNS.items():
            if to_column == from_column:
                continue
            task_id += 1
            _task(cur, task_id, from_status, is_backlog=from_backlog, rank=5.0 if from_backlog else None)
            result = DB.move_fired_employee_task(task_id, SUPER, 'super_admin', to_column)
            label = f'{from_column} → {to_column}'
            status, is_backlog, _rank, started, _updated = _state(cur, task_id)
            assert (status, is_backlog) == (to_status, to_backlog), label
            # Старт работы ставит только «В работе» (засев без старта).
            assert (started is not None) == (to_column == 'progress'), label
            assert DB._task_board_column_of(status, is_backlog) == to_column, label
            assert result['updated'] is True, label
            assert (result['status'], result['is_backlog'], result['column']) == (to_status, to_backlog, to_column)
            if to_status != from_status:
                assert _history(cur, task_id) == [(to_status, SUPER, DB.TASK_FIRED_MOVE_COMMENTS['assignee'])], label
                assert result['history_id'] is not None, label
            else:
                # Бэклог ↔ «Новая» статус не меняют — в истории пусто, как и с доски.
                assert _history(cur, task_id) == [], label
                assert result['history_id'] is None, label


def test_same_column_writes_nothing(cur):
    _task(cur, 1, 'in_progress')
    before = _state(cur, 1)
    result = DB.move_fired_employee_task(1, SUPER, 'super_admin', 'progress')
    assert result['updated'] is False and result['history_id'] is None
    assert _state(cur, 1) == before
    assert _history(cur, 1) == []


def test_backlog_queue_place(cur):
    # Место в очереди: без него — в конец бэклога; прежнее место сохраняется.
    _task(cur, 1, 'assigned', is_backlog=True, rank=7.0, assignees=(ACTIVE,))
    _task(cur, 2, 'in_progress')
    _task(cur, 3, 'completed', rank=2.5)
    # Вне бэклога место в очереди хранится от прошлого захода — на конец очереди не влияет.
    _task(cur, 4, 'in_progress', rank=50.0, assignees=(ACTIVE,))
    result = DB.move_fired_employee_task(2, SUPER, 'super_admin', 'backlog')
    assert _state(cur, 2)[:3] == ('assigned', True, 8.0)
    assert result['backlog_rank'] == 8.0
    DB.move_fired_employee_task(3, SUPER, 'super_admin', 'backlog')
    assert _state(cur, 3)[:3] == ('assigned', True, 2.5)
    # Из бэклога в «Новую» — без истории, место в очереди остаётся до следующего захода.
    DB.move_fired_employee_task(2, SUPER, 'super_admin', 'todo')
    assert _state(cur, 2)[:3] == ('assigned', False, 8.0)


def test_work_start_is_stamped_once(cur):
    started = datetime(2026, 10, 1, 10, 0)
    _task(cur, 1, 'assigned')
    _task(cur, 2, 'accepted', started_at=started)
    _task(cur, 3, 'assigned')
    DB.move_fired_employee_task(1, SUPER, 'super_admin', 'progress')
    assert _state(cur, 1)[3] is not None
    DB.move_fired_employee_task(2, SUPER, 'super_admin', 'progress')
    assert _state(cur, 2)[3] == started
    # Сразу в «Готово» — работа не начиналась, старта нет.
    DB.move_fired_employee_task(3, SUPER, 'super_admin', 'done')
    assert _state(cur, 3)[3] is None
    # Отметка «изменена» сдвигается: доска и сортировка «Сначала новые» её видят.
    assert _state(cur, 3)[4] > datetime(2026, 11, 1, 9, 0)


def test_one_fired_assignee_is_enough_and_both_statuses_count(cur):
    _task(cur, 1, 'assigned', assignees=(ACTIVE, FIRED))
    _task(cur, 2, 'in_progress', assignees=(LEAVING,))
    assert DB.move_fired_employee_task(1, SUPER, 'super_admin', 'done')['status'] == 'accepted'
    assert DB.move_fired_employee_task(2, SUPER, 'super_admin', 'review')['status'] == 'completed'


def test_who_cannot_move(cur):
    _task(cur, 1, 'assigned')
    _task(cur, 2, 'assigned', assignees=(ACTIVE,))
    # Все работают, поручитель тоже — переносить по-новому нечего.
    _task(cur, 3, 'assigned', assignees=(ACTIVE,), requester=SUPER)
    for role in ('admin', 'sv', 'operator', 'superadmin_typo'):
        with pytest.raises(PermissionError, match='ONLY_SUPER_ADMIN_MOVES'):
            DB.move_fired_employee_task(1, ADMIN, role, 'done')
    # Псевдоним роли — тот же суперадмин.
    assert DB.move_fired_employee_task(1, SUPER, 'superadmin', 'progress')['updated'] is True
    for task_id in (2, 3):
        with pytest.raises(PermissionError, match='NO_FIRED_EMPLOYEE'):
            DB.move_fired_employee_task(task_id, SUPER, 'super_admin', 'done')
        # И в ту же колонку: ответ «уже там» про чужую задачу — тоже ответ о ней.
        with pytest.raises(PermissionError, match='NO_FIRED_EMPLOYEE'):
            DB.move_fired_employee_task(task_id, SUPER, 'super_admin', 'todo')
    with pytest.raises(ValueError, match='TASK_NOT_FOUND'):
        DB.move_fired_employee_task(999, SUPER, 'super_admin', 'done')
    for column in ('archive', '', None, 'DONE '):
        if column == 'DONE ':
            # Регистр и пробелы колонки не меняют.
            assert DB.move_fired_employee_task(1, SUPER, 'super_admin', column)['status'] == 'accepted'
            continue
        with pytest.raises(ValueError, match='INVALID_COLUMN'):
            DB.move_fired_employee_task(1, SUPER, 'super_admin', column)
    # Отказы ничего не записали.
    assert _state(cur, 2)[:2] == ('assigned', False)
    assert _history(cur, 2) == [] and _history(cur, 3) == []


def test_move_locks_the_task_row():
    # Перенос и сдача в одну секунду без замка записали бы оба шага поверх друг
    # друга (тот же замок, что у update_task_status). На стенде проверено: перенос
    # ждёт открытую транзакцию со строкой задачи и видит уже записанное.
    method = ast.get_source_segment(DATABASE_SOURCE, next(
        node for node in ast.walk(source_cache.parse(DATABASE_SOURCE))
        if isinstance(node, ast.FunctionDef) and node.name == 'move_fired_employee_task'))
    select = method[method.index('SELECT t.status'):method.index('row = cursor.fetchone()')]
    assert 'FOR UPDATE' in select
    # Замок берётся раньше любых проверок состояния и записи.
    assert method.index('FOR UPDATE') < method.index('NO_FIRED_EMPLOYEE') < method.index('UPDATE tasks')


def test_fired_creator_or_requester_is_enough(cur):
    # Задача 332: поставила уволенная СВ, исполнители — суперадмины, задача сдана.
    # Принять может только постановщик, свою работу себе не принимают — висела навсегда.
    _task(cur, 1, 'completed', assignees=(SUPER, ACTIVE), creator=BOSS)
    result = DB.move_fired_employee_task(1, SUPER, 'super_admin', 'done')
    assert (result['status'], result['updated']) == ('accepted', True)
    assert _history(cur, 1) == [('accepted', SUPER, DB.TASK_FIRED_MOVE_COMMENTS['creator'])]
    # Уволен поручитель (он принимает итог) — то же самое, подпись своя.
    _task(cur, 2, 'completed', assignees=(ACTIVE,), requester=LEAVING)
    DB.move_fired_employee_task(2, SUPER, 'super_admin', 'progress')
    assert _history(cur, 2) == [('in_progress', SUPER, DB.TASK_FIRED_MOVE_COMMENTS['requester'])]
    # Уволены и исполнитель, и постановщик — подпись про исполнителя: его карточка.
    _task(cur, 3, 'assigned', assignees=(FIRED,), creator=BOSS)
    DB.move_fired_employee_task(3, SUPER, 'super_admin', 'done')
    assert _history(cur, 3) == [('accepted', SUPER, DB.TASK_FIRED_MOVE_COMMENTS['assignee'])]
    # Постановщика нет вовсе (учётку удалили) — это не «уволен».
    _task(cur, 4, 'assigned', assignees=(ACTIVE,), creator=None)
    with pytest.raises(PermissionError, match='NO_FIRED_EMPLOYEE'):
        DB.move_fired_employee_task(4, SUPER, 'super_admin', 'done')


def _reads(cur, task_id):
    cur.execute('SELECT r.user_id, r.kind, r.seen_at = t.updated_at FROM task_action_reads r'
                ' JOIN tasks t ON t.id = r.task_id WHERE r.task_id = %s ORDER BY r.user_id', (task_id,))
    return cur.fetchall()


def test_move_author_gets_no_bell_item_about_own_step(cur):
    # Задача 332: суперадмин-исполнитель закрыл сданную задачу уволенной СВ. Колокол
    # иначе сказал бы ему же «Работу приняли». Отметка — тем же временем, что правка
    # задачи: «seen_at < updated_at» колокола ложно. Соисполнителю — сообщить.
    _task(cur, 1, 'completed', assignees=(SUPER, ACTIVE), creator=BOSS)
    DB.move_fired_employee_task(1, SUPER, 'super_admin', 'done')
    assert _reads(cur, 1) == [(SUPER, 'accepted', True)]
    # Поручитель сам отправил на проверку — «ждёт вашей приёмки» о своём шаге не нужно.
    _task(cur, 2, 'in_progress', assignees=(FIRED,), requester=SUPER)
    DB.move_fired_employee_task(2, SUPER, 'super_admin', 'review')
    assert _reads(cur, 2) == [(SUPER, 'review', True)]
    # Исполнитель вернул себе в «Новую» и в «Возвращено»; просроченная — «просрочена».
    _task(cur, 3, 'completed', assignees=(SUPER,), creator=BOSS)
    DB.move_fired_employee_task(3, SUPER, 'super_admin', 'todo')
    assert _reads(cur, 3) == [(SUPER, 'fresh', True)]
    DB.move_fired_employee_task(3, SUPER, 'super_admin', 'returned')
    assert _reads(cur, 3) == [(SUPER, 'returned', True)]
    _task(cur, 4, 'completed', assignees=(SUPER,), creator=BOSS, due_at=datetime(2026, 1, 1, 9, 0))
    DB.move_fired_employee_task(4, SUPER, 'super_admin', 'progress')
    assert _reads(cur, 4) == [(SUPER, 'overdue', True)]
    # Не участник и не о себе — ничего не отмечаем: его колокол это не касается.
    _task(cur, 5, 'completed')
    DB.move_fired_employee_task(5, SUPER, 'super_admin', 'done')
    assert _reads(cur, 5) == []
    # В бэклоге причин нет — отмечать нечего.
    _task(cur, 6, 'completed', assignees=(SUPER,), creator=BOSS)
    DB.move_fired_employee_task(6, SUPER, 'super_admin', 'backlog')
    assert _reads(cur, 6) == []
