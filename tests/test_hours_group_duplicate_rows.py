# -*- coding: utf-8 -*-
"""Оператор дважды в «Учёте часов» группы (12.11.2026).

Состав группы за месяц читается из записей членства, а записей одного человека в
группе за месяц бывает две: ушёл и вернулся, или перевод исправили в день зачисления.
Во втором случае прежнее членство закрывалось «днём раньше новой даты» — раньше своего
начала («с 09.11 по 08.11») — и всё равно считалось частью месяца. На проде так
оператора завели в группу с датой приёма, через час перевели в другую и через четыре
минуты вернули: в первой группе он стал двумя строками, во второй — пустой строкой.

Два правила:
* «Учёт часов» (экран и Excel по группе) — одна строка на человека;
* перевод отменяет членство, которое к новой дате ещё не началось и за которым нет
  учтённых часов, а не закрывает его датой раньше начала.

Методы `Database` достаются через `ast`: боевой модуль на импорте поднимает пул к БД.
"""

import ast
import contextlib
import textwrap
import unittest
from datetime import date, datetime

from tests import source_cache


DATABASE_PATH = source_cache.database_flat_path()
DATABASE_SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")
DATABASE_MODULE = source_cache.parse(DATABASE_SOURCE)
DATABASE_CLASS = next(
    node
    for node in DATABASE_MODULE.body
    if isinstance(node, ast.ClassDef) and node.name == "Database"
)


def _method_source(name):
    method = next(
        node
        for node in DATABASE_CLASS.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return textwrap.dedent(ast.get_source_segment(DATABASE_SOURCE, method))


def _class_constant(name):
    for node in DATABASE_CLASS.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Database.{name} not found")


def _bind(instance, name, **globals_):
    namespace = {"logging": __import__("logging"), "datetime": datetime, **globals_}
    exec(_method_source(name), namespace)
    return namespace[name].__get__(instance, type(instance))


# ---------------------------------------------------------------------------
# «Учёт часов» группы: одна строка на человека
# ---------------------------------------------------------------------------

def _operator_row(op_id, name):
    # Порядок колонок — как в SELECT get_daily_hours_by_supervisor_month.
    return (op_id, name, 1.0, "working", 77, date(2026, 11, 9), "Основа", "operator",
            0, 0, 0, 0, 0, 0, 0, 0)


def _daily_row(op_id, day, calls):
    return (op_id, date(2026, 11, day), 0.0, 0.0, 0.0, calls, 0.0,
            0, None, None, 0, 0, {})


class _HoursCursor:
    """Отвечает на запросы «Учёта часов» по фрагменту SQL."""

    def __init__(self, operator_rows, daily_rows):
        self.operator_rows = operator_rows
        self.daily_rows = daily_rows
        self._rows = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        if "FROM group_operator_memberships gom JOIN groups g" in flat or (
            "FROM users u LEFT JOIN work_hours w" in flat
        ):
            self._rows = list(self.operator_rows)
        elif "FROM daily_hours d" in flat:
            self._rows = list(self.daily_rows)
        else:
            self._rows = []

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


class _HoursDatabase:
    def __init__(self, cursor):
        self.cursor = cursor
        self.get_daily_hours_by_supervisor_month = _bind(
            self,
            "get_daily_hours_by_supervisor_month",
            CALCULATION_MODEL_CHAT_MANAGER="chat_manager",
            CALCULATION_MODEL_TEZ_OP="tez_op",
        )

    @contextlib.contextmanager
    def _get_cursor(self):
        yield self.cursor

    def _is_month_closed(self, month):
        return False

    def _load_segments_by_operator_tx(self, cursor, op_ids, start, end):
        return {}

    def _load_dismissal_dates_tx(self, cursor, op_ids):
        return {}

    def _dismissal_date_iso(self, dismissal_dates, op_id):
        return None

    def _coerce_daily_extra_metrics(self, raw):
        return dict(raw or {})

    def _normalize_calculation_model_code(self, code, direction_name=None):
        return code or "operator"

    def _load_technical_issues_by_operator_day_tx(self, **kwargs):
        return {}, {}

    def _load_offline_activities_by_operator_day_tx(self, **kwargs):
        return {}, {}

    def _load_training_hours_by_operator_tx(self, **kwargs):
        return {}

    def _load_chat_manager_metrics_by_operator_day_tx(self, **kwargs):
        return {}, {}


class GroupHoursOneRowPerPersonTests(unittest.TestCase):
    def _operators(self, operator_rows, group_id=8):
        daily = [_daily_row(1001, 10, 13), _daily_row(1001, 11, 17)]
        db = _HoursDatabase(_HoursCursor(operator_rows, daily))
        result = db.get_daily_hours_by_supervisor_month(None, "2026-11", group_id=group_id)
        return result["operators"]

    def test_two_memberships_in_one_month_give_one_row(self):
        # Пустая запись «с 09.11 по 08.11» и настоящая «с 09.11» — две строки выборки.
        rows = [
            _operator_row(1002, "Оператор А"),
            _operator_row(1001, "Оператор Б"),
            _operator_row(1001, "Оператор Б"),
        ]

        operators = self._operators(rows)

        self.assertEqual([op["operator_id"] for op in operators], [1002, 1001])
        # Итоги человека не удваиваются и не теряются.
        operator = operators[1]
        self.assertEqual(operator["aggregates"]["total_calls"], 30)
        self.assertEqual(sorted(operator["daily"]), ["10", "11"])

    def test_order_by_name_is_kept(self):
        rows = [
            _operator_row(1003, "Оператор А"),
            _operator_row(1001, "Оператор Б"),
            _operator_row(1001, "Оператор Б"),
            _operator_row(1002, "Оператор В"),
        ]

        operators = self._operators(rows)

        self.assertEqual([op["name"] for op in operators], ["Оператор А", "Оператор Б", "Оператор В"])


# ---------------------------------------------------------------------------
# Перевод не оставляет членство «конец раньше начала»
# ---------------------------------------------------------------------------

class _MoveCursor:
    def __init__(self, cancelled=()):
        self.calls = []
        self._cancelled = list(cancelled)
        self._rows = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append((flat, params))
        self._rows = list(self._cancelled) if flat.startswith("DELETE FROM group_operator_memberships") else []

    def fetchone(self):
        return None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def index_of(self, fragment):
        for i, (flat, _) in enumerate(self.calls):
            if fragment in flat:
                return i
        raise AssertionError(f"SQL не выполнен: {fragment!r}")

    def params_of(self, fragment):
        return [params for flat, params in self.calls if fragment in flat]


class _MoveDatabase:
    def __init__(self, cursor):
        self.cursor = cursor
        self.log = []
        self._DAILY_HOURS_NON_EMPTY = _class_constant("_DAILY_HOURS_NON_EMPTY")
        self._add_operator_to_group_tx = _bind(self, "_add_operator_to_group_tx")

    def _group_active_supervisor_id_tx(self, cursor, group_id):
        return 77

    def _set_operators_supervisor_tx(self, cursor, operator_ids, supervisor_id):
        self.log.append(("supervisor", len(cursor.calls)))

    def _sync_operator_direction_from_group_tx(self, cursor, group_id, operator_id, changed_by=None):
        return None

    def _stamp_orphan_group_ids_tx(self, cursor, operator_id=None):
        self.log.append(("stamp", len(cursor.calls), operator_id))


class MoveCancelsNotStartedMembershipTests(unittest.TestCase):
    def _move(self, cancelled=(), start_date="2026-11-09"):
        cursor = _MoveCursor(cancelled)
        db = _MoveDatabase(cursor)
        db._add_operator_to_group_tx(cursor, 3, 1001, start_date=start_date, assigned_by=22)
        return cursor, db

    def test_not_started_membership_without_hours_is_cancelled_before_closing(self):
        cursor, _ = self._move()

        deleted = cursor.index_of("DELETE FROM group_operator_memberships")
        closed = cursor.index_of("UPDATE group_operator_memberships SET end_date")
        self.assertLess(deleted, closed, "закрытие «днём раньше» успело бы раньше отмены")
        flat, params = cursor.calls[deleted]
        self.assertIn("gom.start_date >= COALESCE(%s::date, CURRENT_DATE)", flat)
        self.assertIn("gom.end_date IS NULL AND gom.group_id <> %s", flat)
        self.assertEqual(params, (1001, 3, "2026-11-09"))

    def test_membership_with_counted_hours_is_never_cancelled(self):
        cursor, _ = self._move()

        flat = cursor.calls[cursor.index_of("DELETE FROM group_operator_memberships")][0]
        self.assertIn("AND NOT EXISTS ( SELECT 1 FROM daily_hours dh", flat)
        self.assertIn("dh.group_id = gom.group_id", flat)
        self.assertIn("dh.day >= gom.start_date", flat)
        self.assertIn("AND (" + _class_constant("_DAILY_HOURS_NON_EMPTY") + ")", flat)

    def test_empty_days_of_cancelled_membership_go_to_the_new_group(self):
        cursor, db = self._move(cancelled=[(8, date(2026, 11, 9))])

        released = cursor.index_of("UPDATE daily_hours SET group_id = NULL")
        self.assertEqual(
            cursor.params_of("UPDATE daily_hours SET group_id = NULL"),
            [(1001, 8, date(2026, 11, 9))],
        )
        stamp = next(entry for entry in db.log if entry[0] == "stamp")
        self.assertGreater(stamp[1], released, "дни отдаются новой группе после снятия старой")
        self.assertEqual(stamp[2], 1001)

    def test_nothing_cancelled_touches_no_days(self):
        cursor, _ = self._move(cancelled=[])

        self.assertEqual(cursor.params_of("UPDATE daily_hours SET group_id = NULL"), [])

    def test_move_without_date_compares_with_today(self):
        cursor, _ = self._move(start_date=None)

        self.assertEqual(cursor.params_of("DELETE FROM group_operator_memberships"), [(1001, 3, None)])


if __name__ == "__main__":
    unittest.main()
