# -*- coding: utf-8 -*-
"""Clockster как второй источник отметок (задача #273).

В Clockster отмечается центральный офис, которого в Workpace нет вовсе. Источник
подключён ТОЛЬКО НА ЧТЕНИЕ: интеграцию статусов операторов через Clockster убрали
01.08.2026 после «висящих приходов», и здесь она не возвращается — сторожим это
отдельным тестом, потому что соблазн переиспользовать готовый клиент велик.

Сеть не трогаем: проверяем чистый нормализатор на слепке ответа /schedules.
"""

import unittest
from pathlib import Path

from group_late import clockster
from group_late.helpers import parse_dt

ROOT = Path(__file__).resolve().parents[1]
CLOCKSTER_SRC = (ROOT / "group_late" / "clockster.py").read_text(encoding="utf-8-sig")

# Слепок реального ответа (03.09.2026), укороченный до нужных полей.
SCHEDULE_ROWS = [
    {
        "user": {"id": 264594, "code": "77", "first_name": "Ару",
                 "middle_name": "Серікқызы", "last_name": "Омарова"},
        "dates": {
            "2026-09-01": {
                "in": "2026-09-01T11:34:00+05:00",
                "out": "2026-09-01T20:35:39+05:00",
                "attendance": [
                    {"datetime": "2026-09-01T11:34:00+05:00", "status": 1, "source": "frontend",
                     "location": {"id": 3641, "title": "ЦО"}},
                    {"datetime": "2026-09-01T20:35:39+05:00", "status": 0, "source": "device",
                     "location": {"id": 3641, "title": "ЦО"}},
                ],
                "schedule": {
                    "id": 14818759, "title": "Рабочее расписание", "type": "work",
                    "time_start": "10:00:00", "time_end": "19:00:00", "timezone": "+05:00",
                    "time_planned": 28800, "break_time": 3600, "leave_type": None,
                    "location": {"id": 3641, "title": "ЦО"},
                    "department": {"id": 3488, "title": "Центральный офис"},
                    "position": {"id": 63707, "title": "Модератор"},
                },
            },
            # Выходной: план есть, но это не работа — неявкой быть не должен.
            "2026-09-02": {
                "in": None, "out": None, "attendance": [],
                "schedule": {"id": 1, "title": "Выходной", "type": "leave",
                             "time_start": "00:00:00", "time_end": "00:00:00",
                             "timezone": "+05:00", "leave_type": "day_off",
                             "break_time": 0},
            },
            # Ночная смена: конец раньше начала — значит следующим днём.
            "2026-09-03": {
                "in": None, "out": None, "attendance": [],
                "schedule": {"id": 2, "title": "Ночь", "type": "work",
                             "time_start": "22:00:00", "time_end": "07:00:00",
                             "timezone": "+05:00", "break_time": 3600},
            },
            # Ни плана, ни факта — такого дня у человека нет.
            "2026-09-04": {"in": None, "out": None, "attendance": [], "schedule": None},
        },
    },
]

USERS = [{"id": 264594, "code": "77", "phone": "+77010000000",
          "position": {"id": 63707, "title": "Модератор"},
          "location": {"id": 3641, "title": "ЦО"},
          "department": {"id": 3488, "title": "Центральный офис"}}]


class NormalizerTests(unittest.TestCase):
    def setUp(self):
        lookup = clockster.build_user_lookup(USERS)
        self.records, self.marks = clockster.to_records(SCHEDULE_ROWS, lookup)
        self.by_date = {r["date"]: r for r in self.records}

    def test_empty_day_is_dropped(self):
        self.assertNotIn("2026-09-04", self.by_date)

    def test_leave_day_without_marks_is_dropped(self):
        # Выходной и отпуск не должны превращаться в «Не явился» и попадать в
        # неявки кадрового отчёта. Дня без плана и без отметок в отчёте просто нет —
        # так же ведёт себя и Workpace, у которого на такой день нет смены.
        self.assertNotIn("2026-09-02", self.by_date)

    def test_leave_day_with_marks_survives_without_plan(self):
        # А вот если человек в свой выходной всё же отметился, день нужен: это
        # работа вне графика, и кадровик обязан её увидеть.
        rows = [{
            "user": {"id": 1, "first_name": "Иван", "last_name": "Иванов"},
            "dates": {"2026-09-05": {
                "in": "2026-09-05T09:00:00+05:00", "out": None,
                "attendance": [{"datetime": "2026-09-05T09:00:00+05:00", "status": 1}],
                "schedule": {"id": 9, "title": "Выходной", "type": "leave",
                             "time_start": "09:00:00", "time_end": "18:00:00",
                             "timezone": "+05:00", "leave_type": "day_off"},
            }},
        }]
        records, marks = clockster.to_records(rows)
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]["workTimeStart"])
        self.assertEqual(len(marks), 1)

    def test_work_day_carries_plan_fact_and_break(self):
        day = self.by_date["2026-09-01"]
        self.assertEqual(parse_dt(day["workTimeStart"]).strftime("%H:%M"), "10:00")
        self.assertEqual(parse_dt(day["workTimeEnd"]).strftime("%H:%M"), "19:00")
        self.assertEqual(parse_dt(day["inMark"]).strftime("%H:%M"), "11:34")
        self.assertEqual(parse_dt(day["outMark"]).strftime("%H:%M"), "20:35")
        self.assertEqual(day["breakSeconds"], 3600)
        self.assertEqual(day["positionName"], "Модератор")
        self.assertEqual(day["departmentName"], "Центральный офис")
        self.assertEqual(day["locationName"], "ЦО")
        self.assertEqual(day["scheduleName"], "Рабочее расписание")
        self.assertEqual(day["markSystem"], "clockster")

    def test_night_shift_ends_next_day(self):
        # Без переноса конца план кончался бы раньше начала, и весь день читался
        # бы как ранний уход на сутки.
        day = self.by_date["2026-09-03"]
        start, end = parse_dt(day["workTimeStart"]), parse_dt(day["workTimeEnd"])
        self.assertLess(start, end)
        self.assertEqual(end.strftime("%Y-%m-%d %H:%M"), "2026-09-04 07:00")

    def test_mark_codes_are_inverted_to_the_workpace_convention(self):
        # У Workpace markType 0 — вход, у Clockster status 1 — приход. Если не
        # перевернуть, приход и уход поменяются местами.
        kinds = sorted((parse_dt(m["markDate"]).strftime("%H:%M"), m["markType"])
                       for m in self.marks)
        self.assertEqual(kinds, [("11:34", 0), ("20:35", 1)])

    def test_marks_are_not_reported_as_suspicious(self):
        # У Workpace status 0 значит «терминал не подтвердил» и рождает событие
        # «подозрительная отметка». У Clockster такого флага нет.
        self.assertTrue(all(m["status"] == 1 for m in self.marks))

    def test_employee_id_is_namespaced(self):
        # Идентификаторы двух источников не должны столкнуться: у Workpace это
        # GUID карточки, у Clockster — числовой id.
        self.assertTrue(all(r["employeeId"].startswith("clockster:") for r in self.records))

    def test_full_name_order_matches_ours(self):
        self.assertEqual(self.records[0]["employeeName"], "Омарова Ару Серікқызы")


class ReadOnlyTests(unittest.TestCase):
    """Интеграцию статусов операторов не возвращаем — только чтение."""

    def test_client_never_writes_anywhere(self):
        # Проверяем КОД, а не документацию: в заголовке модуля эти таблицы названы
        # как раз затем, чтобы объяснить, что в них ничего не пишется.
        code = CLOCKSTER_SRC.split('"""', 2)[2]
        for forbidden in ("operator_status_events", "operator_status_segments",
                          "daily_hours", "attendance_mark_overrides",
                          "INSERT", "UPDATE ", "DELETE", "cursor", "psycopg2"):
            self.assertNotIn(forbidden, code, forbidden)

    def test_client_only_reads_over_http(self):
        self.assertNotIn(".post(", CLOCKSTER_SRC)
        self.assertNotIn(".put(", CLOCKSTER_SRC)
        self.assertNotIn(".delete(", CLOCKSTER_SRC)

    def test_window_is_clamped_to_the_api_limit(self):
        # date_end дальше трёх месяцев даёт 422; урезаем молча, иначе выгрузка
        # падает на чужом ограничении.
        start, end = clockster._clamp_window("2026-01-01", "2026-12-31")
        self.assertEqual((end - start).days, clockster.MAX_WINDOW_DAYS)

    def test_window_survives_reversed_dates(self):
        start, end = clockster._clamp_window("2026-09-10", "2026-09-01")
        self.assertLess(start, end)


if __name__ == "__main__":
    unittest.main()
