# -*- coding: utf-8 -*-
"""Выгрузка раздела «Отметки» по ТЗ #307: столбец «График» и учёт по часам.

* п. 4 — вместо двух столбцов плана («время прихода (план)», «время ухода
  (план)») один столбец «График», и в нём отрезок времени, а не название:
  у записей Clockster название почти всегда «Default» и ни на что не отвечает;
* п. 2 — у часовика в ленте отметок сразу итог отработанного, а не каждая
  отметка по отдельности, и опоздание ему не считается;
* п. 5 — свой график дополняет выгрузку тем же правилом, что и экран: числа
  сверяют в одном окне, и расхождение экрана с файлом было бы худшим исходом.
"""

import unittest
from datetime import datetime
from pathlib import Path

from group_late import plan_rules
from group_late.config import TZ
from group_late.reports import _apply_plan_rules, _schedule_cell

ROOT = Path(__file__).resolve().parents[1]
REPORTS_SRC = (ROOT / "group_late" / "reports.py").read_text(encoding="utf-8-sig")
EMPTY_LOOKUP = {"by_id": {}, "by_external_id": {}, "by_name": {}}
SATURDAY = "2026-09-12"


def _dt(hour, minute=0):
    return datetime(2026, 9, 12, hour, minute, tzinfo=TZ)


def _rule(**over):
    base = {"id": 1, "scope": "department", "target": "Центральный офис", "mode": "schedule",
            "weekdays": [], "date_from": None, "date_to": None, "time_start": "10:00",
            "time_end": "19:00", "break_minutes": 45, "hours_norm": None, "enabled": True}
    base.update(over)
    return base


class HeaderTests(unittest.TestCase):
    def _headers(self):
        start = REPORTS_SRC.index("headers = [")
        return REPORTS_SRC[start:REPORTS_SRC.index("]", start)]

    def test_plan_columns_are_gone(self):
        headers = self._headers()
        self.assertNotIn("Время прихода (план)", headers)
        self.assertNotIn("Время ухода (план)", headers)

    def test_schedule_column_stays_single(self):
        self.assertEqual(self._headers().count('"График"'), 1)

    def test_fact_columns_remain(self):
        headers = self._headers()
        self.assertIn("Время прихода (факт)", headers)
        self.assertIn("Время ухода (факт)", headers)


class ScheduleCellTests(unittest.TestCase):
    def test_plan_reads_as_a_time_span(self):
        self.assertEqual(_schedule_cell({"scheduleName": "Default"}, _dt(10), _dt(19)), "10:00–19:00")

    def test_name_is_only_a_fallback(self):
        self.assertEqual(_schedule_cell({"scheduleName": "Сменный"}, None, None), "Сменный")
        self.assertEqual(_schedule_cell({}, None, None), "—")

    def test_rule_label_wins(self):
        span = {"scheduleName": "Default", "planRuleLabel": "По часам · норма 8:00"}
        self.assertEqual(_schedule_cell(span, None, None), "По часам · норма 8:00")


class ApplyRulesTests(unittest.TestCase):
    def test_rule_fills_only_a_day_without_a_shift(self):
        records = [
            {"employeeId": "c:1", "employeeName": "А", "departmentName": "Центральный офис",
             "workTimeStart": None},
            {"employeeId": "c:2", "employeeName": "Б", "departmentName": "Центральный офис",
             "workTimeStart": "2026-09-12T09:00:00+05:00", "workTimeEnd": "2026-09-12T18:00:00+05:00"},
        ]
        out = _apply_plan_rules(records, [_rule()], [], EMPTY_LOOKUP, SATURDAY)
        self.assertEqual(out[0]["workTimeStart"], "2026-09-12T10:00:00+05:00")
        self.assertEqual(out[0]["breakSeconds"], 45 * 60)
        # Настоящая смена не перебивается.
        self.assertEqual(out[1]["workTimeStart"], "2026-09-12T09:00:00+05:00")
        self.assertNotIn("planRuleLabel", out[1])

    def test_hours_rule_drops_the_plan(self):
        records = [{"employeeId": "c:1", "employeeName": "А", "departmentName": "Центральный офис",
                    "workTimeStart": "2026-09-12T09:00:00+05:00", "workTimeEnd": "2026-09-12T18:00:00+05:00"}]
        out = _apply_plan_rules(records, [_rule(scope="employee", target="c:1", mode="hours",
                                                hours_norm=8)], [], EMPTY_LOOKUP, SATURDAY)
        self.assertEqual(out[0]["planMode"], plan_rules.MODE_HOURS)
        self.assertIsNone(out[0]["workTimeStart"])

    def test_weekend_plan_adds_people_missing_in_sources(self):
        roster = [{"ext_id": "c:9", "full_name": "Сидоров Сидор",
                   "department_name": "Центральный офис", "source": "clockster"}]
        out = _apply_plan_rules([], [_rule(weekdays=[6])], roster, EMPTY_LOOKUP, SATURDAY)
        self.assertEqual([r["employeeName"] for r in out], ["Сидоров Сидор"])
        self.assertEqual(out[0]["workTimeStart"], "2026-09-12T10:00:00+05:00")

    def test_no_rules_changes_nothing(self):
        records = [{"employeeId": "c:1", "workTimeStart": None}]
        self.assertIs(_apply_plan_rules(records, [], [], EMPTY_LOOKUP, SATURDAY), records)


class HoursRowTests(unittest.TestCase):
    def test_marks_cell_shows_the_total_for_hours_people(self):
        self.assertIn('marks_cell = (f"Отработано {work_time_str}', REPORTS_SRC)

    def test_worked_time_of_hours_people_comes_from_all_marks(self):
        self.assertIn("work_seconds = _presence_seconds(raw_marks)", REPORTS_SRC)

    def test_rules_are_read_once_per_export(self):
        # Внутри цикла дней это были бы два запроса в базу на каждый день периода.
        self.assertEqual(REPORTS_SRC.count("= _plan_inputs(db)"), 1)
        loop_start = REPORTS_SRC.index("while current_date <= end_date:")
        self.assertLess(REPORTS_SRC.index("= _plan_inputs(db)"), loop_start)


if __name__ == "__main__":
    unittest.main()
