# -*- coding: utf-8 -*-
"""Строки раздела «Отметки»: статусы, поиск и сортировка (задача #273).

Определения опоздания и времени в работе живут в group_late/attendance.py и
больше нигде: в проекте опоздание уже считается по-разному в учёте часов и в
отбивках, и третье расхождение — между экраном и выгрузкой ОДНОГО раздела — было
бы худшим, потому что кадровик сверяет эти числа в одном окне.

Сеть не трогаем: всё, что здесь проверяется, — чистые функции над слепками.
"""

import unittest
from datetime import datetime

from group_late import attendance, config
from group_late.config import TZ

EMPTY_LOOKUP = {"by_id": {}, "by_external_id": {}, "by_name": {}}
DAY = "2026-09-01"


def _dt(hour, minute=0, day=1):
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ)


def _iso(hour, minute=0, day=1):
    return _dt(hour, minute, day).isoformat()


def _span(**over):
    base = {
        "employeeId": "clockster:1",
        "employeeName": "Иванов Иван",
        "departmentName": "Центральный офис",
        "date": DAY,
        "workTimeStart": _iso(9),
        "workTimeEnd": _iso(18),
        "inMark": None,
        "outMark": None,
        "markSystem": "clockster",
        "positionName": "Верификатор",
        "locationName": "ЦО",
        "scheduleName": "Рабочее расписание",
        "breakSeconds": 3600,
    }
    base.update(over)
    return base


def _mark(hour, minute=0, kind=0, emp="clockster:1"):
    return {"employeeId": emp, "employeeName": "Иванов Иван",
            "markDate": _iso(hour, minute), "markType": kind, "status": 1,
            "markSystem": "clockster"}


class MinutesTests(unittest.TestCase):
    def test_late_is_floored_not_rounded(self):
        # Пришедший в 09:00:59 при плане 09:00 не опоздал — так же считают отбивки.
        plan = _dt(9)
        self.assertEqual(attendance.late_minutes(plan, plan.replace(second=59)), 0)
        self.assertEqual(attendance.late_minutes(plan, _dt(9, 1)), 1)

    def test_early_arrival_is_not_negative_lateness(self):
        self.assertEqual(attendance.late_minutes(_dt(9), _dt(8, 30)), 0)

    def test_missing_side_gives_zero(self):
        self.assertEqual(attendance.late_minutes(None, _dt(9)), 0)
        self.assertEqual(attendance.late_minutes(_dt(9), None), 0)
        self.assertEqual(attendance.early_minutes(None, None), 0)

    def test_early_out_counts_only_leaving_before_plan(self):
        self.assertEqual(attendance.early_minutes(_dt(18), _dt(17, 30)), 30)
        self.assertEqual(attendance.early_minutes(_dt(18), _dt(18, 30)), 0)


class StatusTests(unittest.TestCase):
    def _one(self, records, marks, now):
        rows = attendance.build_rows(records, marks, EMPTY_LOOKUP, DAY, now)
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_on_time_day(self):
        row = self._one([_span(inMark=_iso(9), outMark=_iso(18))], [], _dt(20))
        self.assertEqual(row["status"], attendance.STATUS_OK)
        self.assertEqual(row["late_minutes"], 0)
        # 9 часов между отметками минус час обеда.
        self.assertEqual(row["work_seconds"], 8 * 3600)

    def test_late_day(self):
        row = self._one([_span(inMark=_iso(9, 40), outMark=_iso(18))], [], _dt(20))
        self.assertEqual(row["status"], attendance.STATUS_LATE)
        self.assertEqual(row["late_minutes"], 40)

    def test_absent_only_after_the_threshold(self):
        # Главный дефект существующей выгрузки: утренний отчёт помечал неявкой
        # всю вечернюю смену. До порога человек ещё не «не отметился».
        early = _dt(9, config.MISSING_IN_AFTER_MINUTES - 1)
        self.assertEqual(self._one([_span()], [], early)["status"], attendance.STATUS_OK)
        late = _dt(9, config.MISSING_IN_AFTER_MINUTES + 1)
        self.assertEqual(self._one([_span()], [], late)["status"], attendance.STATUS_ABSENT)

    def test_no_out_mark(self):
        row = self._one([_span(inMark=_iso(9))], [], _dt(23))
        self.assertEqual(row["status"], attendance.STATUS_NO_OUT)
        self.assertEqual(row["work_seconds"], 0)

    def test_day_without_plan_is_off_schedule(self):
        row = self._one([_span(workTimeStart=None, workTimeEnd=None, inMark=_iso(10))],
                        [], _dt(20))
        self.assertEqual(row["status"], attendance.STATUS_OFF_SCHEDULE)

    def test_fact_falls_back_to_raw_marks(self):
        # Workpace не привязывает к смене слишком ранний приход — без разбора
        # сырых отметок человек стал бы ложной неявкой.
        row = self._one([_span()], [_mark(8, 40, 0), _mark(18, 10, 1)], _dt(20))
        self.assertEqual(row["fact_in"], _iso(8, 40))
        self.assertEqual(row["fact_out"], _iso(18, 10))
        self.assertEqual(row["status"], attendance.STATUS_OK)

    def test_first_in_and_last_out_win(self):
        row = self._one([_span()], [_mark(9, 0, 0), _mark(13, 0, 0),
                                    _mark(14, 0, 1), _mark(18, 0, 1)], _dt(20))
        self.assertEqual(row["fact_in"], _iso(9))
        self.assertEqual(row["fact_out"], _iso(18))

    def test_marks_without_a_shift_still_show_up(self):
        # Отметился человек, которого в плане нет: работа вне графика не должна
        # пропадать из кадрового отчёта.
        rows = attendance.build_rows([], [_mark(10, 0, 0, emp="clockster:77")],
                                     EMPTY_LOOKUP, DAY, _dt(20))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], attendance.STATUS_OFF_SCHEDULE)
        self.assertEqual(len(rows[0]["marks"]), 1)

    def test_row_carries_everything_the_spec_asks_for(self):
        row = self._one([_span(inMark=_iso(9), outMark=_iso(18))], [], _dt(20))
        for field in ("position", "location", "schedule", "system_label",
                      "late_minutes", "work_seconds", "marks"):
            self.assertIn(field, row)
        self.assertEqual(row["system_label"], "Клокстер")
        self.assertEqual(row["schedule"], "Рабочее расписание")


class NonTerminalUsersTests(unittest.TestCase):
    """Неявка человека, который вообще не отмечается, — не неявка."""

    def _absent_row(self, emp="clockster:1"):
        rows = attendance.build_rows([_span(employeeId=emp)], [], EMPTY_LOOKUP, DAY, _dt(20))
        self.assertEqual(rows[0]["status"], attendance.STATUS_ABSENT)
        return rows

    def test_person_who_never_marks_is_downgraded(self):
        rows = attendance.mark_non_terminal_users(self._absent_row(), {"clockster:99"})
        self.assertEqual(rows[0]["status"], attendance.STATUS_NO_TERMINAL)
        self.assertEqual(rows[0]["status_label"], "Не отмечается")

    def test_person_who_does_mark_keeps_the_absence(self):
        rows = attendance.mark_non_terminal_users(self._absent_row(), {"clockster:1"})
        self.assertEqual(rows[0]["status"], attendance.STATUS_ABSENT)

    def test_unknown_terminal_users_downgrade_nobody(self):
        # Пустое множество значит «не знаем» — прятать настоящий прогул нельзя.
        rows = attendance.mark_non_terminal_users(self._absent_row(), set())
        self.assertEqual(rows[0]["status"], attendance.STATUS_ABSENT)

    def test_rows_are_never_dropped(self):
        rows = attendance.mark_non_terminal_users(self._absent_row(), {"clockster:99"})
        self.assertEqual(len(rows), 1)

    def test_workpace_rows_are_untouched(self):
        rows = attendance.build_rows([_span(markSystem="workpace")], [],
                                     EMPTY_LOOKUP, DAY, _dt(20))
        rows = attendance.mark_non_terminal_users(rows, {"clockster:99"})
        self.assertEqual(rows[0]["status"], attendance.STATUS_ABSENT)


class SearchAndSortTests(unittest.TestCase):
    ROWS = [
        {"employee": "Петров Пётр", "position": "Верификатор", "department": "Б",
         "location": "ЦО", "fact_in": "2026-09-01T10:00:00+05:00",
         "late_minutes": 60, "work_seconds": 100, "status": attendance.STATUS_LATE,
         "date": DAY, "system_label": "Клокстер", "schedule": "Р"},
        {"employee": "Иванов Иван", "position": "Оператор", "department": "А",
         "location": "TT", "fact_in": None,
         "late_minutes": 0, "work_seconds": 500, "status": attendance.STATUS_ABSENT,
         "date": DAY, "system_label": "Воркпейс", "schedule": "Д"},
    ]

    def test_search_matches_name(self):
        self.assertEqual(len(attendance.search_rows(self.ROWS, "иванов")), 1)

    def test_search_matches_position(self):
        # Постановка требует поиск по должности наравне с ФИО.
        found = attendance.search_rows(self.ROWS, "верифик")
        self.assertEqual([r["employee"] for r in found], ["Петров Пётр"])

    def test_empty_query_keeps_everything(self):
        self.assertEqual(len(attendance.search_rows(self.ROWS, "  ")), 2)

    def test_sort_by_department_and_location(self):
        self.assertEqual([r["department"] for r in attendance.sort_rows(self.ROWS, "department")],
                         ["А", "Б"])
        self.assertEqual([r["location"] for r in attendance.sort_rows(self.ROWS, "location")],
                         ["TT", "ЦО"])

    def test_sort_by_arrival_puts_empty_last(self):
        # Иначе первыми в списке идут те, у кого отметки вообще нет.
        self.assertEqual([r["fact_in"] for r in attendance.sort_rows(self.ROWS, "fact_in")][-1],
                         None)

    def test_sort_by_lateness_is_descending(self):
        self.assertEqual([r["late_minutes"] for r in attendance.sort_rows(self.ROWS, "late_minutes")],
                         [60, 0])

    def test_default_sort_puts_problems_first(self):
        self.assertEqual(attendance.sort_rows(self.ROWS, None)[0]["status"],
                         attendance.STATUS_ABSENT)

    def test_unknown_sort_falls_back(self):
        self.assertEqual(len(attendance.sort_rows(self.ROWS, "нет такой")), 2)


if __name__ == "__main__":
    unittest.main()
