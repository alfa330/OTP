# -*- coding: utf-8 -*-
"""Кэш дней раздела «Отметки» (ТЗ #307, п. 1: период просмотра не ограничен).

Кэш — это не второй расчёт, а та же строка, отложенная до следующего открытия.
Поэтому здесь сторожатся две вещи: строка из базы выглядит ровно так же, как
живая, и прошедшие дни берутся из базы, а сегодняшний — всегда живьём.

Сеть и база подменены: проверяется маршрутизация дней, а не Workpace.
"""

import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from group_late import attendance, attendance_cache
from group_late.config import TZ


def _cached_record(day, **over):
    base = {
        "day": day, "employee_id": "clockster:1", "seq": 0, "employee_name": "Иванов Иван",
        "department_name": "Центральный офис", "position_name": "Аналитик",
        "location_name": "ЦО", "schedule_name": "10:00–19:00", "system": "clockster",
        "plan_in": datetime(day.year, day.month, day.day, 10, 0, tzinfo=TZ),
        "plan_out": datetime(day.year, day.month, day.day, 19, 0, tzinfo=TZ),
        "fact_in": datetime(day.year, day.month, day.day, 10, 15, tzinfo=TZ),
        "fact_out": None, "late_minutes": 15, "early_out_minutes": 0,
        "work_seconds": 0, "present_seconds": 0, "lunch_seconds": 0,
        "status": attendance.STATUS_LATE, "plan_mode": "schedule", "plan_source": None,
        "hours_norm": None,
        "marks": [{"at": f"{day.isoformat()}T10:15:00+05:00", "kind": "in",
                   "system": "clockster", "suspicious": False, "location": "ЦО"}],
    }
    base.update(over)
    return base


class FakeDb:
    def __init__(self, built=(), cached=()):
        self.built = set(built)
        self.cached = list(cached)
        self.stored = []
        self.forgotten = []

    def glb_attendance_built_days(self, start, end, **criteria):
        self.built_criteria = criteria
        return {day for day in self.built if start <= day <= end}

    def glb_read_attendance_rows(self, start, end, query=None, days=None):
        wanted = set(days) if days is not None else None
        return [r for r in self.cached if start <= r["day"] <= end
                and (wanted is None or r["day"] in wanted)]

    def glb_store_attendance_day(self, day, rows, sources=None, engine_version=1):
        self.stored_versions = getattr(self, "stored_versions", []) + [engine_version]
        self.stored.append((day, len(rows)))
        self.built.add(day)
        return len(rows)

    def glb_latest_attendance_rows(self, day_before):
        self.latest_before = day_before
        marked = [r for r in self.cached if r["day"] < day_before and (r["fact_in"] or r["fact_out"])]
        last_day = {}
        for record in marked:
            last_day[record["employee_id"]] = max(last_day.get(record["employee_id"], record["day"]), record["day"])
        return [r for r in marked if r["day"] == last_day[r["employee_id"]]]

    def glb_forget_attendance_days(self, day_from=None, day_to=None):
        self.forgotten.append((day_from, day_to))
        self.built = {d for d in self.built if not (day_from <= d <= day_to)}

    def glb_plan_rules(self, enabled_only=False):
        return []

    def glb_attendance_directory(self, department=None):
        return {"departments": [], "employees": []}


def _live_row(day, name="Живой Живой", department="КЦ 3"):
    return {"date": day.isoformat(), "employee_id": f"wp:{name}", "employee": name,
            "department": department, "status": attendance.STATUS_OK, "marks": []}


class RowShapeTests(unittest.TestCase):
    def test_cached_row_has_the_live_shape(self):
        """Иначе экран ветвился бы на «строку из базы» и «строку из API»."""
        live_keys = set(attendance.build_rows(
            [{"employeeId": "x", "employeeName": "X", "date": "2026-09-10"}], [],
            {"by_id": {}, "by_external_id": {}, "by_name": {}}, "2026-09-10")[0])
        cached_keys = set(attendance_cache.row_from_cache(_cached_record(date(2026, 9, 10))))
        self.assertTrue(live_keys <= cached_keys, live_keys - cached_keys)

    def test_times_come_back_in_company_timezone(self):
        row = attendance_cache.row_from_cache(_cached_record(date(2026, 9, 10)))
        self.assertEqual(row["fact_in"], "2026-09-10T10:15:00+05:00")
        self.assertIsNone(row["fact_out"])

    def test_labels_are_rebuilt_from_codes(self):
        row = attendance_cache.row_from_cache(_cached_record(date(2026, 9, 10)))
        self.assertEqual(row["status_label"], "Опоздание")
        self.assertEqual(row["system_label"], "Клокстер")

    def test_old_days_get_the_balance_without_rebuild(self):
        # Недоработка не хранится в кэше — она выводится из плана и факта строки.
        day = date(2026, 9, 10)
        row = attendance_cache.row_from_cache(_cached_record(
            day, fact_out=datetime(2026, 9, 10, 19, 0, tzinfo=TZ),
            work_seconds=7 * 3600 + 45 * 60, lunch_seconds=3600))
        self.assertEqual((row["underwork_minutes"], row["overwork_minutes"]), (15, 0))

    def test_last_mark_is_taken_from_marks(self):
        row = attendance_cache.row_from_cache(_cached_record(date(2026, 9, 10)))
        self.assertEqual(row["last_mark_at"], "2026-09-10T10:15:00+05:00")


class DayRoutingTests(unittest.TestCase):
    TODAY = date(2026, 9, 14)

    def setUp(self):
        now = datetime(2026, 9, 14, 12, 0, tzinfo=TZ)
        patcher = mock.patch.object(attendance_cache, "_now", return_value=now)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_past_days_are_read_from_cache_and_today_live(self):
        yesterday = self.TODAY - timedelta(days=1)
        db = FakeDb(built=[yesterday], cached=[_cached_record(yesterday)])
        with mock.patch.object(attendance_cache.attendance, "collect",
                               return_value={"rows": [_live_row(self.TODAY)],
                                             "clockster_error": None}) as collect:
            result = attendance_cache.rows_for(db, yesterday, self.TODAY)
        # В Workpace сходили ровно за сегодняшний день — вчерашний из базы.
        self.assertEqual(collect.call_count, 1)
        self.assertEqual(collect.call_args.args[1:3], (self.TODAY, self.TODAY))
        self.assertEqual(sorted(r["date"] for r in result["rows"]),
                         [yesterday.isoformat(), self.TODAY.isoformat()])
        self.assertEqual(db.stored, [])

    def test_missing_past_day_is_built_once_and_stored(self):
        day = self.TODAY - timedelta(days=2)
        db = FakeDb()
        with mock.patch.object(attendance_cache.attendance, "collect",
                               return_value={"rows": [_live_row(day)], "clockster_error": None}):
            result = attendance_cache.rows_for(db, day, day)
        self.assertEqual(db.stored, [(day, 1)])
        self.assertEqual(result["built_days"], 1)
        self.assertEqual(result["pending_days"], [])

    def test_today_is_never_stored(self):
        # Сегодняшний день ещё идёт: положив его в кэш, мы заморозили бы утро.
        db = FakeDb()
        with mock.patch.object(attendance_cache.attendance, "collect",
                               return_value={"rows": [_live_row(self.TODAY)], "clockster_error": None}):
            attendance_cache.rows_for(db, self.TODAY, self.TODAY)
        self.assertEqual(db.stored, [])

    def test_too_many_missing_days_are_reported_not_dropped(self):
        """Молча обрезанный период читается как «в эти дни никто не отмечался»."""
        start = self.TODAY - timedelta(days=40)
        end = self.TODAY - timedelta(days=1)
        db = FakeDb()
        with mock.patch.object(attendance_cache.attendance, "collect",
                               return_value={"rows": [], "clockster_error": None}):
            result = attendance_cache.rows_for(db, start, end)
        self.assertEqual(result["built_days"], attendance_cache.BUILD_MAX_DAYS_PER_REQUEST)
        self.assertEqual(len(result["pending_days"]), 40 - attendance_cache.BUILD_MAX_DAYS_PER_REQUEST)
        # Добирают свежие дни: хвост трёхмесячной давности подождёт.
        self.assertEqual(result["pending_days"][-1],
                         (end - timedelta(days=attendance_cache.BUILD_MAX_DAYS_PER_REQUEST)).isoformat())

    def test_refresh_forgets_the_period_first(self):
        day = self.TODAY - timedelta(days=3)
        db = FakeDb(built=[day], cached=[_cached_record(day)])
        with mock.patch.object(attendance_cache.attendance, "collect",
                               return_value={"rows": [_live_row(day)], "clockster_error": None}):
            attendance_cache.rows_for(db, day, day, refresh=True)
        self.assertEqual(db.forgotten, [(day, day)])
        self.assertEqual(db.stored, [(day, 1)])

    def test_department_filter_uses_the_live_matching(self):
        yesterday = self.TODAY - timedelta(days=1)
        db = FakeDb(built=[yesterday], cached=[
            _cached_record(yesterday),
            _cached_record(yesterday, employee_id="wp:2", employee_name="Петров", department_name="КЦ 3"),
        ])
        result = attendance_cache.rows_for(db, yesterday, yesterday, department="кц 3")
        self.assertEqual([r["employee"] for r in result["rows"]], ["Петров"])


class LatestMarksTests(unittest.TestCase):
    """Режим «Последние отметки» (30.09.2026): без периода, один человек — одна строка."""

    TODAY = date(2026, 9, 14)

    def setUp(self):
        now = datetime(2026, 9, 14, 12, 0, tzinfo=TZ)
        patcher = mock.patch.object(attendance_cache, "_now", return_value=now)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _latest(self, db, live_rows=(), **kwargs):
        with mock.patch.object(attendance_cache.attendance, "collect",
                               return_value={"rows": list(live_rows), "clockster_error": None}) as collect:
            result = attendance_cache.latest_rows(db, **kwargs)
        return result, collect

    def test_each_person_once_on_the_day_of_the_last_mark(self):
        yesterday = self.TODAY - timedelta(days=1)
        old, older = self.TODAY - timedelta(days=3), self.TODAY - timedelta(days=5)
        db = FakeDb(built=[yesterday], cached=[
            _cached_record(older), _cached_record(old),
            _cached_record(older, employee_id="wp:2", employee_name="Петров"),
        ])
        today_row = {**_live_row(self.TODAY, name="Петров"), "employee_id": "wp:2",
                     "last_mark_at": f"{self.TODAY.isoformat()}T09:02:00+05:00"}
        result, _ = self._latest(db, [today_row])
        by_person = {r["employee_id"]: r["date"] for r in result["rows"]}
        self.assertEqual(by_person, {"clockster:1": old.isoformat(), "wp:2": self.TODAY.isoformat()})

    def test_day_without_a_mark_does_not_hide_an_older_one(self):
        # Сегодня человек ещё не пришёл («ждём прихода») — он всё равно в списке,
        # днём своей последней отметки: «день не важен».
        old = self.TODAY - timedelta(days=4)
        db = FakeDb(built=[self.TODAY - timedelta(days=1)], cached=[_cached_record(old)])
        pending = {**_live_row(self.TODAY, name="Иванов Иван"), "employee_id": "clockster:1",
                   "status": attendance.STATUS_PENDING, "last_mark_at": None}
        result, _ = self._latest(db, [pending])
        self.assertEqual([r["date"] for r in result["rows"]], [old.isoformat()])

    def test_yesterday_and_today_go_through_the_period_rules(self):
        # Вчерашний день бывает неокончательным (уход ночной смены лежит
        # сегодня) — он идёт через rows_for, а из кэша «последних» берётся
        # только то, что старше, иначе строка задвоилась бы.
        yesterday = self.TODAY - timedelta(days=1)
        db = FakeDb(built=[yesterday], cached=[_cached_record(yesterday)])
        result, collect = self._latest(db)
        self.assertEqual(db.latest_before, yesterday)
        self.assertEqual(collect.call_args.args[1:3], (self.TODAY, self.TODAY))
        self.assertEqual([r["date"] for r in result["rows"]], [yesterday.isoformat()])

    def test_department_filter_reaches_the_older_days(self):
        old = self.TODAY - timedelta(days=3)
        db = FakeDb(built=[self.TODAY - timedelta(days=1)], cached=[
            _cached_record(old),
            _cached_record(old, employee_id="wp:2", employee_name="Петров", department_name="КЦ 3"),
        ])
        result, _ = self._latest(db, department="кц 3")
        self.assertEqual([r["employee"] for r in result["rows"]], ["Петров"])

    def test_mark_time_comes_back_in_company_timezone(self):
        # По дате этого времени экран делит список на дни, а в кэше отметка
        # лежит как пришла из источника — бывает и без пояса.
        old = self.TODAY - timedelta(days=3)
        record = _cached_record(old, marks=[{"at": f"{old.isoformat()}T19:00:12", "kind": "out"}])
        db = FakeDb(built=[self.TODAY - timedelta(days=1)], cached=[record])
        result, _ = self._latest(db)
        self.assertEqual(result["rows"][0]["last_mark_at"], f"{old.isoformat()}T19:00:12+05:00")

    def test_recent_sort_puts_the_freshest_mark_first(self):
        old = self.TODAY - timedelta(days=3)
        db = FakeDb(built=[self.TODAY - timedelta(days=1)], cached=[_cached_record(old)])
        today_row = {**_live_row(self.TODAY, name="Петров"),
                     "last_mark_at": f"{self.TODAY.isoformat()}T08:18:00+05:00"}
        result, _ = self._latest(db, [today_row])
        ordered = attendance.sort_rows(result["rows"], "recent")
        self.assertEqual([r["employee"] for r in ordered], ["Петров", "Иванов Иван"])


if __name__ == "__main__":
    unittest.main()
