# -*- coding: utf-8 -*-
"""«Бесхозные» дни часов: у оператора больше часов в «Моих часах», чем в «Учёте часов».

«Учёт часов» в режиме группы фильтрует дни по `daily_hours.group_id`, а «Мои часы»
показывают все дни оператора. Членство в группе почти всегда заводят позже, чем
человек реально вышел в группу, поэтому дни до `start_date` не покрывались ни одним
членством, получали `group_id = NULL` и молча выпадали из группового отчёта.

Правило: покрывающее членство приоритетнее (расстояние 0), иначе строка достаётся
ближайшему членству ТОГО ЖЕ МЕСЯЦА. Оператор без членства в этом месяце остаётся с
NULL — это уже «не завели в группу», лечится заведением членства.
"""

import ast
import textwrap
import unittest
from pathlib import Path
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
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


def _load_methods(*names):
    """Исполняет методы Database без импорта модуля (он поднимает пул к БД)."""
    namespace = {"logging": __import__("logging")}
    for node in DATABASE_MODULE.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id.startswith("MEMBERSHIP_")
            for target in node.targets
        ):
            exec(
                compile(ast.Module(body=[node], type_ignores=[]), str(DATABASE_PATH), "exec"),
                namespace,
            )
    for name in names:
        exec(_method_source(name), namespace)
    return namespace


class _CaptureCursor:
    def __init__(self, row=None):
        self.calls = []
        self.rowcount = 0
        self._row = row

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._row


class OrphanDayResolutionTests(unittest.TestCase):
    """_get_operator_group_id_tx — резолв группы на дату при записи дня."""

    def setUp(self):
        self.ns = _load_methods("_get_operator_group_id_tx")

    def _sql_for(self, day="2026-07-13"):
        cursor = _CaptureCursor(row=(8,))
        self.ns["_get_operator_group_id_tx"](None, cursor, 27, day)
        return cursor.calls[0]

    def test_candidates_are_memberships_overlapping_the_month(self):
        sql, params = self._sql_for()
        self.assertIn(
            "gom.start_date <= (date_trunc('month', %(day)s::date)"
            " + INTERVAL '1 month - 1 day')::date",
            sql,
        )
        self.assertIn(
            "gom.end_date IS NULL OR gom.end_date >= date_trunc('month', %(day)s::date)::date",
            sql,
        )
        self.assertEqual({"operator_id": 27, "day": "2026-07-13"}, params)

    def test_covering_membership_still_wins(self):
        """Покрывающий день интервал даёт расстояние 0 и стоит первым в ORDER BY."""
        sql, _ = self._sql_for()
        self.assertIn("ORDER BY CASE WHEN gom.start_date > %(day)s::date", sql)
        self.assertIn("ELSE 0 END, gom.start_date DESC", sql)

    def test_day_outside_every_membership_is_not_dropped(self):
        """Раньше WHERE отсекал день вне интервала — и он оставался без группы."""
        sql, _ = self._sql_for()
        self.assertNotIn("WHERE gom.operator_id = %(operator_id)s AND gom.start_date <= %s", sql)
        self.assertNotIn("gom.end_date >= %s", sql)


class OrphanDayStampingTests(unittest.TestCase):
    """_stamp_orphan_group_ids_tx — добор уже записанных дней без группы."""

    def setUp(self):
        self.ns = _load_methods("_stamp_orphan_group_ids_tx", "_backfill_null_group_id_tx")

    def _calls(self, operator_id=None):
        cursor = _CaptureCursor()
        self.ns["_stamp_orphan_group_ids_tx"](None, cursor, operator_id)
        return cursor.calls

    def test_updates_daily_hours_and_work_hours(self):
        daily, work_hours = self._calls()
        self.assertIn("UPDATE daily_hours dh SET group_id = sub.group_id", daily[0])
        self.assertIn("UPDATE work_hours w SET group_id = sub.group_id", work_hours[0])

    def test_idempotent_touches_only_rows_without_group(self):
        for sql, _ in self._calls():
            self.assertIn("group_id IS NULL", sql)

    def test_same_month_rule_matches_the_write_path(self):
        """Правило одно на оба места — иначе бэкфилл и запись разъедутся."""
        daily, work_hours = self._calls()
        self.assertIn(
            "gom.start_date <= (date_trunc('month', d.day) + INTERVAL '1 month - 1 day')::date",
            daily[0],
        )
        self.assertIn("ORDER BY d.id, CASE WHEN gom.start_date > d.day", daily[0])
        # work_hours привязан к месяцу, а не к дню — берём его последний день.
        self.assertIn("to_date(x.month || '-01', 'YYYY-MM-DD') + interval '1 month - 1 day'", work_hours[0])
        self.assertIn(
            "gom.start_date <= (date_trunc('month', me.day) + INTERVAL '1 month - 1 day')::date",
            work_hours[0],
        )

    def test_global_pass_has_no_operator_filter(self):
        for sql, params in self._calls():
            self.assertNotIn("operator_id = %(operator_id)s", sql)
            self.assertEqual({}, params)

    def test_scoped_pass_is_parameterised(self):
        daily, work_hours = self._calls(operator_id=27)
        self.assertIn("WHERE d.group_id IS NULL AND d.operator_id = %(operator_id)s", daily[0])
        self.assertIn("WHERE x.group_id IS NULL AND x.operator_id = %(operator_id)s", work_hours[0])
        self.assertEqual({"operator_id": 27}, daily[1])
        self.assertEqual({"operator_id": 27}, work_hours[1])

    def test_startup_backfill_delegates_to_the_same_rule(self):
        self.assertIn("_stamp_orphan_group_ids_tx(cursor)", _method_source("_backfill_null_group_id_tx"))
        self.assertIn("SAVEPOINT sp_null_group_backfill", DATABASE_SOURCE)

    def test_enrollment_picks_up_earlier_days_without_waiting_for_restart(self):
        self.assertIn(
            "_stamp_orphan_group_ids_tx(cursor, operator_id)",
            _method_source("add_operator_to_group"),
        )


class HoursViewsStayInSyncTests(unittest.TestCase):
    """Расхождение чинится на уровне данных, а не в одном из двух экранов."""

    def test_group_view_still_filters_days_by_group(self):
        self.assertIn('_daily_sql.format(group_filter="AND d.group_id = %s")', DATABASE_SOURCE)

    def test_my_hours_reads_all_operator_days(self):
        my_hours = _method_source("get_daily_hours_for_operator_month")
        self.assertIn("WHERE operator_id = %s AND day >= %s AND day <= %s", my_hours)
        self.assertNotIn("group_id = %s", my_hours)


if __name__ == "__main__":
    unittest.main()
