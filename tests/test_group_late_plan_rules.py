# -*- coding: utf-8 -*-
"""Свой график смен раздела «Отметки» (ТЗ #307, пп. 2 и 5).

Главное правило, которое здесь и сторожится: наш график ДОПОЛНЯЕТ Workpace и
Clockster, а не спорит с ними. Он ставится только в дни без смены — ради «плана
на выходной» правила и заведены. Стоит ему начать перебивать настоящую смену, и
одно правило «пн–пт 10:00–19:00» на отдел сделает опоздавшим весь колл-центр с
его сменным графиком.

Сеть не трогаем: всё, что проверяется, — чистые функции над слепками.
"""

import unittest
from datetime import datetime

from group_late import attendance, plan_rules
from group_late.config import TZ
from group_late.helpers import presence_seconds

EMPTY_LOOKUP = {"by_id": {}, "by_external_id": {}, "by_name": {}}
# 2026-09-11 — пятница, 2026-09-12 — суббота.
FRIDAY = "2026-09-11"
SATURDAY = "2026-09-12"


def _iso(hour, minute=0, day=11):
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ).isoformat()


def _rule(**over):
    base = {
        "id": 1, "scope": "department", "target": "Центральный офис",
        "target_label": "Центральный офис", "mode": "schedule", "weekdays": [],
        "date_from": None, "date_to": None, "time_start": "10:00", "time_end": "19:00",
        "break_minutes": 60, "hours_norm": None, "note": None, "enabled": True,
    }
    base.update(over)
    return base


def _span(**over):
    base = {
        "employeeId": "clockster:1", "employeeName": "Иванов Иван",
        "departmentName": "Центральный офис", "date": FRIDAY,
        "workTimeStart": None, "workTimeEnd": None,
        "inMark": None, "outMark": None, "markSystem": "clockster",
        "positionName": "Аналитик", "scheduleName": "Default",
    }
    base.update(over)
    return base


def _mark(hour, minute=0, kind=0, day=11, emp="clockster:1"):
    return {"employeeId": emp, "employeeName": "Иванов Иван",
            "markDate": _iso(hour, minute, day), "markType": kind, "status": 1,
            "markSystem": "clockster"}


class SelectRuleTests(unittest.TestCase):
    def test_department_rule_matches_by_name(self):
        rule = plan_rules.select_rule([_rule()], "clockster:1", "Иванов Иван",
                                      "Центральный офис", FRIDAY)
        self.assertIsNotNone(rule)

    def test_other_department_is_not_touched(self):
        self.assertIsNone(plan_rules.select_rule(
            [_rule()], "wp-7", "Петров Пётр", "Регионы", FRIDAY))

    def test_employee_rule_beats_department_rule(self):
        rules = [_rule(id=1), _rule(id=2, scope="employee", target="clockster:1",
                                    time_start="12:00", time_end="21:00")]
        rule = plan_rules.select_rule(rules, "clockster:1", "Иванов Иван",
                                      "Центральный офис", FRIDAY)
        self.assertEqual(rule["id"], 2)

    def test_employee_rule_matches_by_full_name_too(self):
        # Карточку в Workpace заводят заново при смене фамилии, и правило,
        # привязанное только к идентификатору, потерялось бы молча.
        rule = plan_rules.select_rule(
            [_rule(scope="employee", target="Иванов Иван")],
            "wp-99", "иванов иван", "Регионы", FRIDAY)
        self.assertIsNotNone(rule)

    def test_weekdays_limit_the_rule(self):
        weekend = _rule(weekdays=[6, 7])
        self.assertIsNone(plan_rules.select_rule([weekend], "clockster:1", "И", "Центральный офис", FRIDAY))
        self.assertIsNotNone(plan_rules.select_rule([weekend], "clockster:1", "И", "Центральный офис", SATURDAY))

    def test_empty_weekdays_mean_every_day(self):
        # Иначе правило без выбранных дней выглядело бы заведённым, но не работало.
        self.assertIsNotNone(plan_rules.select_rule([_rule(weekdays=[])], "clockster:1", "И",
                                                    "Центральный офис", SATURDAY))

    def test_date_window_is_inclusive_and_closed(self):
        rule = _rule(date_from="2026-09-12", date_to="2026-09-12")
        self.assertIsNone(plan_rules.select_rule([rule], "clockster:1", "И", "Центральный офис", FRIDAY))
        self.assertIsNotNone(plan_rules.select_rule([rule], "clockster:1", "И", "Центральный офис", SATURDAY))

    def test_disabled_rule_is_ignored(self):
        self.assertIsNone(plan_rules.select_rule([_rule(enabled=False)], "clockster:1", "И",
                                                 "Центральный офис", FRIDAY))


class PlanBoundsTests(unittest.TestCase):
    def test_bounds_are_local_times_of_the_day(self):
        start, end = plan_rules.plan_bounds(_rule(), FRIDAY)
        self.assertEqual(start.strftime("%Y-%m-%d %H:%M"), "2026-09-11 10:00")
        self.assertEqual(end.strftime("%Y-%m-%d %H:%M"), "2026-09-11 19:00")

    def test_night_shift_ends_next_day(self):
        # Без переноса план кончался бы раньше, чем начался, и весь день читался
        # как ранний уход.
        start, end = plan_rules.plan_bounds(_rule(time_start="21:00", time_end="06:00"), FRIDAY)
        self.assertEqual(end.strftime("%Y-%m-%d %H:%M"), "2026-09-12 06:00")
        self.assertGreater(end, start)

    def test_hours_rule_has_no_bounds(self):
        self.assertEqual(plan_rules.plan_bounds(_rule(mode="hours", hours_norm=8), FRIDAY),
                         (None, None))


class FillOnlyTests(unittest.TestCase):
    """Правило дополняет источники и не перебивает настоящую смену."""

    def test_rule_fills_the_day_without_a_shift(self):
        rows = attendance.build_rows(
            [_span()], [_mark(10, 5)], EMPTY_LOOKUP, FRIDAY,
            now_local=datetime(2026, 9, 11, 23, 0, tzinfo=TZ), rules=[_rule()])
        self.assertEqual(rows[0]["plan_in"], _iso(10))
        self.assertEqual(rows[0]["plan_source"], "rule")
        self.assertEqual(rows[0]["schedule"], "10:00–19:00")

    def test_real_shift_wins_over_the_rule(self):
        rows = attendance.build_rows(
            [_span(workTimeStart=_iso(9), workTimeEnd=_iso(18))], [], EMPTY_LOOKUP, FRIDAY,
            now_local=datetime(2026, 9, 11, 23, 0, tzinfo=TZ), rules=[_rule()])
        self.assertEqual(rows[0]["plan_in"], _iso(9))
        self.assertIsNone(rows[0]["plan_source"])

    def test_lateness_is_counted_from_the_rule(self):
        rows = attendance.build_rows(
            [_span()], [_mark(10, 20)], EMPTY_LOOKUP, FRIDAY,
            now_local=datetime(2026, 9, 11, 23, 0, tzinfo=TZ), rules=[_rule()])
        self.assertEqual(rows[0]["late_minutes"], 20)
        self.assertEqual(rows[0]["status"], attendance.STATUS_LATE)


class RosterRowsTests(unittest.TestCase):
    """План на выходной обязан создавать строку сам.

    Иначе в субботу человека нет ни в Workpace, ни в Clockster, и он не значится
    неявкой — он просто отсутствует в таблице, что читается как «выходной»."""

    ROSTER = [{"ext_id": "clockster:9", "full_name": "Сидоров Сидор",
               "department_name": "Центральный офис", "position_name": "Курьер",
               "source": "clockster"}]

    def test_person_without_any_source_row_appears(self):
        rows = attendance.build_rows(
            [], [], EMPTY_LOOKUP, SATURDAY,
            now_local=datetime(2026, 9, 12, 23, 0, tzinfo=TZ),
            rules=[_rule(weekdays=[6])], roster=self.ROSTER)
        self.assertEqual([r["employee"] for r in rows], ["Сидоров Сидор"])
        self.assertEqual(rows[0]["status"], attendance.STATUS_ABSENT)
        self.assertEqual(rows[0]["system"], "clockster")

    def test_nobody_is_added_twice(self):
        rows = attendance.build_rows(
            [_span(employeeId="clockster:9", employeeName="Сидоров Сидор", date=SATURDAY)],
            [], EMPTY_LOOKUP, SATURDAY,
            now_local=datetime(2026, 9, 12, 23, 0, tzinfo=TZ),
            rules=[_rule(weekdays=[6])], roster=self.ROSTER)
        self.assertEqual(len(rows), 1)

    def test_without_rules_roster_adds_nothing(self):
        # Состав сам по себе не повод рисовать строку: иначе в таблицу попадут
        # все, кто в этот день и не должен был выходить.
        rows = attendance.build_rows([], [], EMPTY_LOOKUP, SATURDAY,
                                     now_local=None, rules=[], roster=self.ROSTER)
        self.assertEqual(rows, [])


class HoursModeTests(unittest.TestCase):
    """Учёт по часам: у человека нет графика, и опаздывать ему не к чему."""

    HOURS_RULE = _rule(scope="employee", target="clockster:1", mode="hours", hours_norm=8,
                       time_start=None, time_end=None)

    def _row(self, marks, span=None):
        rows = attendance.build_rows(
            [span or _span(workTimeStart=_iso(9), workTimeEnd=_iso(18))], marks,
            EMPTY_LOOKUP, FRIDAY, now_local=datetime(2026, 9, 11, 23, 0, tzinfo=TZ),
            rules=[self.HOURS_RULE])
        return rows[0]

    def test_plan_is_dropped_and_lateness_is_zero(self):
        row = self._row([_mark(10, 15)])
        self.assertIsNone(row["plan_in"])
        self.assertEqual(row["late_minutes"], 0)
        self.assertEqual(row["plan_mode"], "hours")
        self.assertEqual(row["hours_norm"], 8)

    def test_worked_time_counts_every_mark(self):
        """Пример из приложения к ТЗ: ушёл в 13:23, вернулся в 14:31.

        Раньше время считалось от первого входа до последнего выхода минус час
        обеда — 07:44, будто он никуда не уходил. На месте он был 07:36."""
        marks = [_mark(10, 15, 0), _mark(13, 23, 1), _mark(14, 31, 0), _mark(18, 59, 1)]
        row = self._row(marks)
        self.assertEqual(row["work_seconds"], 7 * 3600 + 36 * 60)
        self.assertEqual(row["present_seconds"], 7 * 3600 + 36 * 60)

    def test_schedule_column_says_how_it_is_counted(self):
        self.assertEqual(self._row([_mark(10)])["schedule"], "По часам · норма 8:00")


class PresenceTests(unittest.TestCase):
    def test_pairs_are_summed(self):
        marks = [_mark(10, 15, 0), _mark(13, 23, 1), _mark(14, 31, 0), _mark(18, 59, 1)]
        self.assertEqual(presence_seconds(marks), 7 * 3600 + 36 * 60)

    def test_unclosed_entry_is_dropped(self):
        # Досчитывать вход до конца суток значило бы придумать уход.
        self.assertEqual(presence_seconds([_mark(10, 0, 0)]), 0)

    def test_exit_without_entry_is_ignored(self):
        self.assertEqual(presence_seconds([_mark(18, 0, 1)]), 0)

    def test_double_entry_keeps_the_first(self):
        # Терминал не увидел выхода; взять второй вход значило бы съесть уже
        # отработанное время.
        marks = [_mark(10, 0, 0), _mark(11, 0, 0), _mark(12, 0, 1)]
        self.assertEqual(presence_seconds(marks), 2 * 3600)

    def test_empty_is_zero(self):
        self.assertEqual(presence_seconds([]), 0)
        self.assertEqual(presence_seconds(None), 0)


class RecentSortTests(unittest.TestCase):
    """Раздел открывается «от самых недавних» (ТЗ #307, п. 1)."""

    ROWS = [
        {"employee": "Старый", "last_mark_at": _iso(9), "date": FRIDAY},
        {"employee": "Свежий", "last_mark_at": _iso(21), "date": FRIDAY},
        {"employee": "Без отметок", "last_mark_at": None, "plan_in": None, "date": FRIDAY},
    ]

    def test_newest_first(self):
        order = [r["employee"] for r in attendance.sort_rows(self.ROWS, "recent")]
        self.assertEqual(order[:2], ["Свежий", "Старый"])

    def test_rows_without_marks_go_last(self):
        self.assertEqual(attendance.sort_rows(self.ROWS, "recent")[-1]["employee"], "Без отметок")

    def test_plan_stands_in_for_a_missing_mark(self):
        rows = [{"employee": "Ждём", "last_mark_at": None, "plan_in": _iso(20), "date": FRIDAY},
                {"employee": "Давно", "last_mark_at": _iso(8), "date": FRIDAY}]
        self.assertEqual(attendance.sort_rows(rows, "recent")[0]["employee"], "Ждём")


if __name__ == "__main__":
    unittest.main()
