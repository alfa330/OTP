"""Разбор строк курсора.

Курсор проекта (conn.cursor() без cursor_factory) возвращает КОРТЕЖИ. Раздел
падал на проде с «cannot convert dictionary update sequence element #0 to a
sequence», потому что код ждал словари — а тестами это не ловилось, ведь базы
в тестах нет. Здесь база и не нужна: достаточно курсора-заглушки, который
ведёт себя как настоящий.
"""

from oktell_guard import queries


class TupleCursor:
    """Ведёт себя как курсор проекта: description + кортежи."""

    def __init__(self, columns, rows):
        self.description = [(name,) for name in columns]
        self._rows = list(rows)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def test_tuple_row_becomes_dict_by_column_names():
    cursor = TupleCursor(['id', 'name', 'role'], [(7, 'Руслан', 'super_admin')])
    assert queries.fetch_one(cursor) == {'id': 7, 'name': 'Руслан', 'role': 'super_admin'}


def test_fetch_all_maps_every_row():
    cursor = TupleCursor(['id', 'kicks'], [(1, 3), (2, 0)])
    assert queries.fetch_all(cursor) == [{'id': 1, 'kicks': 3}, {'id': 2, 'kicks': 0}]


def test_empty_result_is_none_not_crash():
    cursor = TupleCursor(['id'], [])
    assert queries.fetch_one(cursor) is None
    assert queries.fetch_all(cursor) == []


def test_dict_rows_also_work():
    """Если курсор когда-нибудь переведут на словари, ничего не сломается."""
    cursor = TupleCursor(['id'], [{'id': 5, 'name': 'уже словарь'}])
    assert queries.fetch_one(cursor) == {'id': 5, 'name': 'уже словарь'}


def test_access_context_survives_tuple_rows():
    """Тот самый путь, который отдавал 500 на каждом открытии раздела."""
    cursor = TupleCursor(
        ['id', 'name', 'role', 'department_code', 'is_department_head', 'headed_department_code'],
        [(7, 'Руслан', 'super_admin', '', False, '')],
    )
    ctx = queries.access_context(cursor, 7)
    assert ctx['role'] == 'super_admin'


def test_access_context_prefers_headed_department():
    cursor = TupleCursor(
        ['id', 'name', 'role', 'department_code', 'is_department_head', 'headed_department_code'],
        [(9, 'Глава', 'admin', 'op', True, 'szov')],
    )
    ctx = queries.access_context(cursor, 9)
    assert ctx['department_code'] == 'szov'


def test_settings_and_release_readers_use_the_same_path():
    settings_cursor = TupleCursor(['id', 'enabled', 'threshold_s'], [(1, True, 180)])
    assert queries.get_settings(settings_cursor)['threshold_s'] == 180

    release_cursor = TupleCursor(['id', 'version', 'sha256'], [(1, '1.0.0', 'a' * 64)])
    assert queries.current_release(release_cursor)['version'] == '1.0.0'

    empty = TupleCursor(['id'], [])
    assert queries.get_settings(empty) == {}
    assert queries.current_release(empty) is None
