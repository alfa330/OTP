# -*- coding: utf-8 -*-
"""Ночная смена Clockster в выгрузке и на экране «Отметок» (ТЗ iCore 3, п. 4).

Выгрузка осталась прежней, изменено только то, что нужно смене через полночь:
Clockster берётся с запасом в день по краям периода, отметки ложатся в день
своей смены, сведённые приход и уход не перебиваются сырыми отметками, а у ухода
на следующий день стоит дата. Кэш экрана не пересчитывает историю задним числом
и не прячет сохранённый день, если пересборка не удалась.
"""

import io
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook

from group_late import attendance_cache, clockster, reports
from group_late.config import TZ

ROOT = Path(__file__).resolve().parents[1]
DB_SRC = (ROOT / "database.py").read_text(encoding="utf-8-sig")
VIEW_SRC = (ROOT / "src" / "components" / "group_late" / "GroupLateBotView.jsx").read_text(encoding="utf-8-sig")


def _cell(day, marks):
    return {"in": None, "out": None, "schedule": None, "attendance": [
        {"datetime": f"2026-09-{day:02d}T{hhmm}:00+05:00", "status": status, "source": "device"}
        for hhmm, status in marks]}


SCHEDULES = [{"user": {"id": 7, "first_name": "Сотрудник", "last_name": "Вечерний"}, "dates": {
    "2026-09-22": _cell(22, [("13:39", clockster.MARK_IN)]),
    "2026-09-23": _cell(23, [("00:02", clockster.MARK_OUT), ("14:54", clockster.MARK_IN)]),
    "2026-09-24": _cell(24, [("00:01", clockster.MARK_OUT)]),
}}]


class ReportTests(unittest.TestCase):
    def _report(self, start, end=None):
        workpace = mock.Mock()
        workpace.get_employees.return_value = []
        workpace.get_timetable_spans.return_value = []
        workpace.get_marks.return_value = []
        source = mock.Mock()
        source.get_schedules.return_value = SCHEDULES
        source.get_users.return_value = []
        with mock.patch.object(reports, "workpace_client", workpace), \
                mock.patch.object(reports, "clockster_client", source), \
                mock.patch.object(reports.config, "is_clockster_configured", return_value=True):
            data, _name, _text = reports.generate_report(start, end, db=None)
        self.source = source
        return load_workbook(io.BytesIO(data))

    def _line(self, wb, day):
        return [c.value for c in wb[day][4]]

    def test_evening_shift_until_after_midnight_is_counted(self):
        wb = self._report("2026-09-22")
        line = self._line(wb, "2026-09-22")
        self.assertEqual(line[6], "13:39")
        self.assertEqual(line[8], "00:02 (23.09)")
        self.assertEqual(line[10], "09:23")
        self.assertEqual(line[5], "13:39(Вход), 00:02(Выход, 23.09)")

    def test_previous_night_departure_is_not_the_next_days_departure(self):
        wb = self._report("2026-09-23")
        line = self._line(wb, "2026-09-23")
        self.assertEqual(line[6], "14:54")
        self.assertEqual(line[8], "00:01 (24.09)")
        self.assertNotIn("00:02", line[5])

    def test_sources_are_read_with_a_day_on_each_side(self):
        self._report("2026-09-22", "2026-09-23")
        start, end = self.source.get_schedules.call_args.args
        self.assertEqual((start, end), (date(2026, 9, 21), date(2026, 9, 24)))


class FakeDb:
    def __init__(self, stored, final):
        self.stored, self.final = set(stored), set(final)
        self.read_days = None

    def glb_attendance_built_days(self, start, end, **criteria):
        days = self.final if criteria else self.stored
        return {day for day in days if start <= day <= end}

    def glb_read_attendance_rows(self, start, end, query=None, days=None):
        self.read_days = sorted(days or [])
        return []

    def glb_plan_rules(self, enabled_only=False):
        return []


class CacheTests(unittest.TestCase):
    TODAY = date(2026, 9, 29)

    def setUp(self):
        patcher = mock.patch.object(attendance_cache, "_now",
                                    return_value=datetime(2026, 9, 29, 10, 0, tzinfo=TZ))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_failed_rebuild_serves_the_stored_day(self):
        # Вчерашний день собран ночью, до утреннего ухода ночной смены, — его
        # пересобирают. Если пересборка упала, день отдаётся из кэша, а не пропадает.
        yesterday = self.TODAY - timedelta(days=1)
        db = FakeDb(stored=[yesterday], final=[])
        with mock.patch.object(attendance_cache, "build_day", side_effect=RuntimeError("timeout")):
            result = attendance_cache.rows_for(db, yesterday, yesterday)
        self.assertEqual(result["pending_days"], [])
        self.assertEqual(db.read_days, [yesterday])

    def test_never_stored_day_that_fails_is_pending(self):
        day = self.TODAY - timedelta(days=2)
        db = FakeDb(stored=[], final=[])
        with mock.patch.object(attendance_cache, "build_day", side_effect=RuntimeError("timeout")):
            result = attendance_cache.rows_for(db, day, day)
        self.assertEqual(result["pending_days"], [day.isoformat()])

    def test_history_of_previous_rules_is_not_rebuilt(self):
        # Дни прежних правил окончательны: условие про версию стоит внутри «ИЛИ».
        self.assertIn('settled += " OR engine_version < %s"', DB_SRC)
        self.assertIn("ADD COLUMN IF NOT EXISTS engine_version SMALLINT NOT NULL DEFAULT 1", DB_SRC)

    def test_supervisor_hours_read_the_terminal_type(self):
        # Роль по порядку — для «Отметок»; правило пар СВ (#352) на типе терминала.
        self.assertIn("mark = {**mark, 'kind': mark['terminal_kind']}", DB_SRC)


class ScreenTests(unittest.TestCase):
    def test_next_day_departure_is_dated(self):
        self.assertIn("const otherFactDay = (iso, day) =>", VIEW_SRC)
        self.assertIn("<TimeCell fact={row.fact_out} day={row.date} />", VIEW_SRC)
        self.assertIn("value={fmtFactTime(row.fact_out, row.date)}", VIEW_SRC)


if __name__ == "__main__":
    unittest.main()
