# -*- coding: utf-8 -*-
"""Смены Clockster из ленты отметок (ТЗ iCore 3, п. 4).

Последовательности взяты из реальных отметок 28.08–28.09.2026 (ФИО убраны):
каждая — случай, который прежний расчёт по клетке Clockster считал неверно, или
случай, на котором ломалось наивное «первая — приход, следующая — уход» по всей
ленте подряд.
"""

import unittest
from datetime import date, datetime, timedelta

from group_late import attendance, clockster, clockster_shifts as cs
from group_late.config import TZ
from group_late.helpers import parse_dt

IN, OUT = cs.IN, cs.OUT


def _at(day, hhmm):
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ)


def _marks(*items):
    """('22 21:55', IN) … → лента отметок."""
    out = []
    for when, terminal in items:
        day, hhmm = when.split()
        out.append({"at": _at(int(day), hhmm), "terminal": terminal})
    return out


def _plan(day, start, end):
    begin = _at(day, start)
    finish = _at(day, end)
    if finish <= begin:
        finish += timedelta(days=1)
    return {date(2026, 9, day): (begin, finish, 3, 4)}


def _hm(value):
    return value.strftime("%d %H:%M") if value else None


def _main(shifts, day):
    shift = cs.main_shift(shifts, date(2026, 9, day))
    return (_hm(shift.arrival), _hm(shift.departure)) if shift else None


class NightShiftTests(unittest.TestCase):
    def test_evening_shift_until_after_midnight_is_one_shift(self):
        # Смена до 00:00: уход в 00:02 лежит в следующем календарном дне.
        # Раньше оба дня получали ноль часов.
        shifts = cs.build_shifts(_marks(("22 13:39", IN), ("23 00:02", OUT), ("23 14:54", IN),
                                        ("24 00:01", OUT)))
        self.assertEqual(_main(shifts, 22), ("22 13:39", "23 00:02"))
        self.assertEqual(_main(shifts, 23), ("23 14:54", "24 00:01"))

    def test_morning_mark_starts_a_new_day(self):
        # Руководитель, который работает 08–09 → 21, пропустил утреннюю отметку:
        # Clockster перевернул типы («21:55 приход», «08:18 уход», «21:54 приход»).
        # Это не ночная смена 21:55 → 08:18, а день 08:18 → 21:54.
        shifts = cs.build_shifts(_marks(("22 21:55", IN), ("23 08:18", OUT), ("23 21:54", IN),
                                        ("24 08:31", OUT), ("24 20:54", IN)))
        self.assertEqual(_main(shifts, 23), ("23 08:18", "23 21:54"))
        self.assertEqual(_main(shifts, 24), ("24 08:31", "24 20:54"))
        self.assertEqual(_main(shifts, 22), ("22 21:55", None))

    def test_morning_arrival_does_not_close_the_evening(self):
        shifts = cs.build_shifts(_marks(("06 20:04", IN), ("07 10:38", IN), ("07 20:03", OUT)))
        self.assertEqual(_main(shifts, 7), ("07 10:38", "07 20:03"))
        self.assertEqual(_main(shifts, 6), ("06 20:04", None))

    def test_day_shift_is_not_extended_into_the_next_morning(self):
        shifts = cs.build_shifts(_marks(("16 09:06", IN), ("16 21:03", OUT), ("17 09:12", IN)))
        self.assertEqual(_main(shifts, 16), ("16 09:06", "16 21:03"))
        self.assertEqual(_main(shifts, 17), ("17 09:12", None))

    def test_planned_night_shift_is_one_shift(self):
        shifts = cs.build_shifts(_marks(("04 15:55", IN), ("05 02:05", OUT)), _plan(4, "17:00", "02:00"))
        self.assertEqual(_main(shifts, 4), ("04 15:55", "05 02:05"))
        self.assertIsNone(_main(shifts, 5))

    def test_departure_after_the_plan_window_closes_the_shift(self):
        # План 15:00–19:15, приход есть, уход в 01:02 — уже за окном графика.
        plans = _plan(19, "15:00", "19:15")
        shifts = cs.build_shifts(_marks(("19 15:42", IN), ("20 01:02", OUT), ("20 10:16", IN),
                                        ("20 21:01", OUT)), plans)
        self.assertEqual(_main(shifts, 19), ("19 15:42", "20 01:02"))
        self.assertEqual(_main(shifts, 20), ("20 10:16", "20 21:01"))

    def test_lone_mark_near_the_end_of_the_plan_is_a_departure(self):
        # Одинокая вечерняя отметка при графике 09:00–18:00 — уход, а не приход
        # с опозданием на полдня.
        shifts = cs.build_shifts(_marks(("22 18:40", IN)), _plan(22, "09:00", "18:00"))
        self.assertEqual(_main(shifts, 22), (None, "22 18:40"))
        shifts = cs.build_shifts(_marks(("22 08:44", IN)), _plan(22, "09:00", "18:00"))
        self.assertEqual(_main(shifts, 22), ("22 08:44", None))


class LoneMarkTests(unittest.TestCase):
    """Одна отметка в дне с графиком — приход («первая — приход»); уход — только
    после конца графика и если её ничто не закрыло."""

    def test_late_arrival_is_an_arrival_not_an_absence(self):
        shifts = cs.build_shifts(_marks(("22 13:40", IN)), _plan(22, "09:00", "18:00"))
        self.assertEqual(_main(shifts, 22), ("22 13:40", None))

    def test_later_mark_of_the_same_day_closes_the_shift(self):
        # 14:00 в окне графика, 23:30 — уже за окном: одна смена 14:00 → 23:30.
        shifts = cs.build_shifts(_marks(("22 14:00", IN), ("22 23:30", OUT)), _plan(22, "09:00", "18:00"))
        self.assertEqual(_main(shifts, 22), ("22 14:00", "22 23:30"))

    def test_mark_before_the_plan_end_opens_an_evening_shift(self):
        # График 14:00–18:15, одна отметка 17:59, уход в 00:01 следующего дня.
        shifts = cs.build_shifts(_marks(("19 17:59", IN), ("20 00:01", OUT)), _plan(19, "14:00", "18:15"))
        self.assertEqual(_main(shifts, 19), ("19 17:59", "20 00:01"))

    def test_night_after_a_day_plan_closes_with_the_only_morning_mark(self):
        # Ночной работник с дневным графиком: 19:25 после конца графика, утром
        # одна отметка «уход» в 09:01 — это смена 19:25 → 09:01, а не прогул.
        shifts = cs.build_shifts(_marks(("05 19:25", IN), ("06 09:01", OUT)), _plan(5, "14:00", "18:15"))
        self.assertEqual(_main(shifts, 5), ("05 19:25", "06 09:01"))
        self.assertIsNone(_main(shifts, 6))

    def test_night_without_plan_closes_with_the_only_morning_departure(self):
        shifts = cs.build_shifts(_marks(("12 20:46", IN), ("13 09:02", OUT)))
        self.assertEqual(_main(shifts, 12), ("12 20:46", "13 09:02"))

    def test_morning_arrival_typed_in_does_not_close_the_night(self):
        shifts = cs.build_shifts(_marks(("12 20:46", IN), ("13 09:02", IN)))
        self.assertEqual(_main(shifts, 12), ("12 20:46", None))
        self.assertEqual(_main(shifts, 13), ("13 09:02", None))

    def test_known_cost_evening_only_then_morning_only(self):
        # Цена правила (см. п. 3 модуля): дневной работник отметился только
        # вечером, а на следующий день только утром — выходит ночная смена.
        shifts = cs.build_shifts(_marks(("22 19:02", IN), ("23 08:57", OUT)))
        self.assertEqual(_main(shifts, 22), ("22 19:02", "23 08:57"))

    def test_lone_mark_after_the_plan_stays_a_departure_when_nothing_closes_it(self):
        shifts = cs.build_shifts(_marks(("22 18:40", IN), ("23 10:00", IN), ("23 19:00", OUT)),
                                 _plan(22, "09:00", "18:00"))
        self.assertEqual(_main(shifts, 22), (None, "22 18:40"))
        self.assertEqual(_main(shifts, 23), ("23 10:00", "23 19:00"))


class OrderInsteadOfTerminalTests(unittest.TestCase):
    def test_two_arrivals_are_an_arrival_and_a_departure(self):
        # Терминал назвал уход приходом — раньше это был день без ухода, 0 часов.
        shifts = cs.build_shifts(_marks(("14 10:45", IN), ("14 20:27", IN)), _plan(14, "10:00", "19:00"))
        self.assertEqual(_main(shifts, 14), ("14 10:45", "14 20:27"))
        roles = [role for _mark, role in cs.main_shift(shifts, date(2026, 9, 14)).roles()]
        self.assertEqual(roles, [IN, OUT])

    def test_two_departures_are_an_arrival_and_a_departure(self):
        shifts = cs.build_shifts(_marks(("24 10:19", OUT), ("24 19:17", OUT)), _plan(24, "10:00", "19:00"))
        self.assertEqual(_main(shifts, 24), ("24 10:19", "24 19:17"))

    def test_repeat_touch_is_one_mark(self):
        # 20:24 «приход» и через минуту поправка администратора «уход».
        shifts = cs.build_shifts(_marks(("04 10:25", IN), ("04 20:24", IN), ("04 20:25", OUT)),
                                 _plan(4, "10:00", "19:00"))
        self.assertEqual(_main(shifts, 4), ("04 10:25", "04 20:25"))

    def test_same_day_marks_pair_by_order_without_a_plan(self):
        shifts = cs.build_shifts(_marks(("09 09:00", IN), ("09 13:00", IN), ("09 14:00", OUT),
                                        ("09 18:00", OUT)))
        shift = cs.main_shift(shifts, date(2026, 9, 9))
        self.assertEqual((_hm(shift.arrival), _hm(shift.departure)), ("09 09:00", "09 18:00"))
        self.assertEqual([role for _mark, role in shift.roles()], [IN, OUT, IN, OUT])


class GuardTests(unittest.TestCase):
    def test_midnight_arrival_is_not_a_nineteen_hour_shift(self):
        # 00:01 — хвост вчерашнего дня, 19:19 — приход вечерней смены.
        shifts = cs.build_shifts(_marks(("15 00:01", IN), ("15 19:19", IN), ("16 00:03", OUT),
                                        ("16 22:10", IN)))
        self.assertEqual(_main(shifts, 15), ("15 19:19", "16 00:03"))

    def test_early_morning_departure_with_nothing_to_close(self):
        # Хвост вчерашней смены, чей приход не отмечен, — уход без прихода, а не
        # приход в 00:04 (терминал мог назвать его и приходом).
        shifts = cs.build_shifts(_marks(("28 00:04", IN), ("28 09:00", IN), ("28 18:00", OUT)))
        self.assertEqual(_main(shifts, 28), ("28 09:00", "28 18:00"))
        lone = [s for s in shifts if s.departure_only]
        self.assertEqual(len(lone), 1)
        self.assertEqual(_hm(lone[0].departure), "28 00:04")

    def test_early_arrival_without_plan_stays_an_arrival(self):
        shifts = cs.build_shifts(_marks(("10 05:45", IN), ("10 14:00", OUT)))
        self.assertEqual(_main(shifts, 10), ("10 05:45", "10 14:00"))

    def test_too_long_span_is_not_a_shift(self):
        shifts = cs.build_shifts(_marks(("10 06:30", IN), ("10 23:30", OUT)))
        self.assertEqual(_main(shifts, 10), ("10 23:30", None))

    def test_every_mark_belongs_to_exactly_one_shift(self):
        marks = _marks(("22 21:55", IN), ("23 00:18", OUT), ("23 21:54", IN), ("06 20:04", IN),
                       ("07 10:38", IN), ("07 20:03", OUT), ("15 00:01", IN), ("15 19:19", IN),
                       ("16 00:03", OUT), ("04 10:25", IN), ("04 20:24", IN), ("04 20:25", OUT))
        shifts = cs.build_shifts(marks, _plan(4, "10:00", "19:00"))
        seen = [id(mark) for shift in shifts for mark, _role in shift.roles()]
        self.assertEqual(sorted(seen), sorted(id(mark) for mark in marks))


def _cell(day, hhmm_status, schedule=None):
    return {"in": None, "out": None, "schedule": schedule, "attendance": [
        {"datetime": f"2026-09-{day:02d}T{hhmm}:00+05:00", "status": status, "source": "device"}
        for hhmm, status in hhmm_status]}


class ToRecordsTests(unittest.TestCase):
    """Запись дня и отметки в форме, которую понимают экран и выгрузка."""

    def setUp(self):
        # Clockster кладёт отметку смены через полночь в клетки обоих соседних дней.
        rows = [{"user": {"id": 7, "first_name": "Сотрудник", "last_name": "Вечерний"}, "dates": {
            "2026-09-22": _cell(22, [("13:39", clockster.MARK_IN)]),
            "2026-09-23": _cell(23, [("00:02", clockster.MARK_OUT), ("14:54", clockster.MARK_IN)]),
            "2026-09-24": _cell(24, [("00:01", clockster.MARK_OUT)]),
        }}]
        self.records, self.marks = clockster.to_records(rows)
        self.by_date = {r["date"]: r for r in self.records}

    def test_evening_shift_gets_its_departure_on_the_arrival_day(self):
        day = self.by_date["2026-09-22"]
        self.assertTrue(day["factFromShifts"])
        self.assertEqual(parse_dt(day["inMark"]).strftime("%d %H:%M"), "22 13:39")
        self.assertEqual(parse_dt(day["outMark"]).strftime("%d %H:%M"), "23 00:02")

    def test_day_of_a_lone_morning_departure_has_no_record(self):
        # Уход 24-го в 00:01 закрыл смену 23-го — своего дня у него нет.
        self.assertNotIn("2026-09-24", self.by_date)

    def test_marks_carry_the_shift_day_role_and_terminal_type(self):
        by_time = {parse_dt(m["markDate"]).strftime("%d %H:%M"): m for m in self.marks}
        departure = by_time["23 00:02"]
        self.assertEqual(departure["shiftDate"], "2026-09-22")
        self.assertEqual(departure["markRole"], OUT)
        self.assertEqual(departure["markType"], OUT)
        self.assertEqual(by_time["23 14:54"]["shiftDate"], "2026-09-23")

    def test_mark_repeated_in_two_cells_is_taken_once(self):
        rows = [{"user": {"id": 8, "first_name": "А", "last_name": "Б"}, "dates": {
            "2026-09-04": _cell(4, [("15:55", clockster.MARK_IN), ("02:05", clockster.MARK_OUT)]),
        }}]
        rows[0]["dates"]["2026-09-04"]["attendance"][1]["datetime"] = "2026-09-05T02:05:00+05:00"
        rows[0]["dates"]["2026-09-05"] = _cell(5, [("02:05", clockster.MARK_OUT)])
        _records, marks = clockster.to_records(rows)
        self.assertEqual(len(marks), 2)

    def test_retyped_mark_keeps_the_terminal_type_for_supervisor_hours(self):
        # Часы СВ (#352) читают тип терминала (`markType`), а раздел — роль.
        schedule = {"type": "work", "time_start": "10:00:00", "time_end": "19:00:00",
                    "timezone": "+05:00", "boundary_start": 3, "boundary_end": 4}
        rows = [{"user": {"id": 9, "first_name": "В", "last_name": "Г"}, "dates": {
            "2026-09-14": _cell(14, [("10:45", clockster.MARK_IN), ("20:27", clockster.MARK_IN)], schedule),
        }}]
        records, marks = clockster.to_records(rows)
        late = [m for m in marks if parse_dt(m["markDate"]).hour == 20][0]
        self.assertEqual((late["markType"], late["markRole"]), (IN, OUT))
        self.assertEqual(parse_dt(records[0]["outMark"]).strftime("%H:%M"), "20:27")


class EngineTests(unittest.TestCase):
    """Экран и выгрузка не перебирают сырые отметки поверх сведённой смены."""

    EMPTY_LOOKUP = {"by_id": {}, "by_external_id": {}, "by_name": {}}

    def test_previous_night_departure_is_not_this_days_departure(self):
        record = {"employeeId": "clockster:7", "employeeName": "Ночной Сотрудник",
                  "date": "2026-09-23", "workTimeStart": None, "workTimeEnd": None,
                  "inMark": "2026-09-23T21:54:00+05:00", "outMark": None,
                  "factFromShifts": True, "markSystem": "clockster"}
        stray = {"employeeId": "clockster:7", "markDate": "2026-09-23T08:18:00+05:00",
                 "markType": OUT, "markRole": OUT, "markSystem": "clockster", "status": 1}
        [row] = attendance.build_rows([record], [stray], self.EMPTY_LOOKUP, "2026-09-23",
                                      datetime(2026, 9, 24, 12, 0, tzinfo=TZ))
        self.assertIsNone(row["fact_out"])
        self.assertEqual(row["marks"][0]["terminal_kind"], "out")

    def test_late_arrival_with_one_mark_is_late_not_absent(self):
        # Раньше одинокая отметка во второй половине графика становилась
        # «уходом без прихода», и день опоздавшего шёл прогулом.
        schedule = {"type": "work", "time_start": "09:00:00", "time_end": "18:00:00",
                    "timezone": "+05:00", "boundary_start": 3, "boundary_end": 4}
        rows = [{"user": {"id": 11, "first_name": "Д", "last_name": "Е"}, "dates": {
            "2026-09-22": _cell(22, [("13:40", clockster.MARK_IN)], schedule)}}]
        records, marks = clockster.to_records(rows)
        [row] = attendance.build_rows(records, marks, self.EMPTY_LOOKUP, "2026-09-22",
                                      datetime(2026, 9, 23, 12, 0, tzinfo=TZ))
        self.assertEqual(row["status"], attendance.STATUS_LATE)
        self.assertEqual(row["late_minutes"], 280)

    def test_night_shift_row_counts_its_hours(self):
        record = {"employeeId": "clockster:7", "employeeName": "Ночной Сотрудник",
                  "date": "2026-09-22", "workTimeStart": None, "workTimeEnd": None,
                  "inMark": "2026-09-22T21:55:00+05:00", "outMark": "2026-09-23T08:18:00+05:00",
                  "factFromShifts": True, "markSystem": "clockster"}
        [row] = attendance.build_rows([record], [], self.EMPTY_LOOKUP, "2026-09-22",
                                      datetime(2026, 9, 24, 12, 0, tzinfo=TZ))
        # 10 ч 23 мин присутствия минус час обеда.
        self.assertEqual(row["work_seconds"], (10 * 60 + 23 - 60) * 60)


if __name__ == "__main__":
    unittest.main()
