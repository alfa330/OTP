# -*- coding: utf-8 -*-
"""Состав группы за месяц для «Аналитики» журнала оценок (`get_group_operators_for_month`).

Задача #347: фронт делит строки на «Активные» / «Переведённые» / «Уволенные» по
`group_segments`. Поэтому членство «конец раньше начала» — перевод, отменённый в
момент заведения (на проде 16 штук, 3 из них в сентябре 2026), — обязано отсекаться
на сервере: иначе человек попадал в состав группы, где не провёл ни дня, и фронт
записывал его там в «Переведённые».

Боевые модули в тестах не импортируются (на импорте поднимается пул к БД), поэтому
метод достаётся через `ast` — как в соседних наборах.
"""

import ast
import calendar
import textwrap
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_SOURCE = (ROOT / "database.py").read_text(encoding="utf-8-sig")
DATABASE_CLASS = next(
    node
    for node in source_cache.parse(DATABASE_SOURCE).body
    if isinstance(node, ast.ClassDef) and node.name == "Database"
)


def _load_method(name):
    method = next(
        node
        for node in DATABASE_CLASS.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    namespace = {"calendar": calendar, "date": date, "datetime": datetime}
    exec(textwrap.dedent(ast.get_source_segment(DATABASE_SOURCE, method)), namespace)
    return namespace[name]


get_group_operators_for_month = _load_method("get_group_operators_for_month")


def _roster_row(op_id, name, status="working"):
    """Строка ростера в порядке колонок SELECT (23 штуки)."""
    row = [None] * 23
    row[0] = op_id
    row[1] = name
    row[8] = status
    row[22] = "operator"
    return tuple(row)


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def fetchall(self):
        return self.rows


class _FakeDb:
    def __init__(self, rows, segments):
        self.cursor = _Cursor(rows)
        self.segments = segments

    @contextmanager
    def _get_cursor(self):
        yield self.cursor

    def _resolve_user_field_as_of_tx(self, cursor, op_ids, field, ref_dt):
        return {}

    def _load_dismissal_dates_tx(self, cursor, op_ids):
        return {}

    def _dismissal_date_iso(self, dismissal_dates, op_id):
        return None

    def _load_segments_by_operator_tx(self, cursor, op_ids, start, end):
        return self.segments


def _seg(group_id, start_day, end_day, is_current=False):
    return {
        "group_id": group_id,
        "group_name": f"группа {group_id}",
        "direction_name": None,
        "calculation_model_code": None,
        "start_day": start_day,
        "end_day": end_day,
        "is_current": is_current,
    }


class GroupRosterInvertedMembershipTests(unittest.TestCase):
    def test_roster_sql_skips_memberships_that_end_before_they_start(self):
        db = _FakeDb([], {})
        get_group_operators_for_month(db, 10, "2026-09")
        roster_sql = db.cursor.sql[0]
        self.assertIn("FROM group_operator_memberships gom", roster_sql)
        self.assertIn("(gom.end_date IS NULL OR gom.end_date >= gom.start_date)", roster_sql)

    def test_empty_segment_is_not_a_day_in_another_group(self):
        # Сагидоллаев Нурмахан, июль 2026: «ЯР 17.07–16.07» отменён, с 17.07 — в Основе.
        db = _FakeDb(
            [_roster_row(261, "Сагидоллаев Нурмахан")],
            {261: [_seg(99, 17, 16), _seg(10, 17, 31, is_current=True)]},
        )
        [op] = get_group_operators_for_month(db, 10, "2026-07")
        self.assertEqual([s["group_id"] for s in op["group_segments"]], [10])
        self.assertFalse(op["has_other_group_in_month"])

    def test_real_transfer_keeps_both_segments(self):
        # Абдрахманова Айман, сентябрь 2026: Основа 01–07.09, с 08.09 — Чат менеджер.
        db = _FakeDb(
            [_roster_row(330, "Абдрахманова Айман Карасаевна")],
            {330: [_seg(10, 1, 7), _seg(6, 8, 30, is_current=True)]},
        )
        [op] = get_group_operators_for_month(db, 10, "2026-09")
        self.assertEqual([s["group_id"] for s in op["group_segments"]], [10, 6])
        self.assertTrue(op["has_other_group_in_month"])

    def test_one_day_membership_is_kept(self):
        # Начало = конец — это один день в группе, а не пустое членство.
        db = _FakeDb(
            [_roster_row(7, "Оператор")],
            {7: [_seg(10, 5, 5), _seg(6, 6, 30, is_current=True)]},
        )
        [op] = get_group_operators_for_month(db, 10, "2026-09")
        self.assertEqual([s["group_id"] for s in op["group_segments"]], [10, 6])


if __name__ == "__main__":
    unittest.main()
