import sys
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
APP_PATH = ROOT / "src" / "App.jsx"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import clockster_attendance_sync as cas  # noqa: E402


def _read(path):
    return path.read_text(encoding="utf-8-sig")


class ClocksterModuleTests(unittest.TestCase):
    """Модуль выгрузки clockster_attendance_sync (без сети и БД)."""

    def test_config_defaults(self):
        cfg = cas.get_config(env_file="__no_such_env__")
        self.assertEqual(cfg["api_url"].rstrip("/") or cas.DEFAULT_API_URL, cfg["api_url"] or cas.DEFAULT_API_URL)
        self.assertIn("token", cfg)
        self.assertIn("days_back", cfg)

    def test_parse_mark_datetime_normalizes_to_local(self):
        # Отметка с оффсетом +05:00 — локальное время сохраняется как есть (naive).
        parsed = cas._parse_mark_datetime("2026-07-22T09:01:30+05:00")
        self.assertEqual(parsed, datetime(2026, 7, 22, 9, 1, 30))
        # Иной оффсет конвертируется в местный пояс (+05).
        parsed_utc = cas._parse_mark_datetime("2026-07-22T04:01:30+00:00")
        self.assertEqual(parsed_utc, datetime(2026, 7, 22, 9, 1, 30))
        self.assertIsNone(cas._parse_mark_datetime(""))
        self.assertIsNone(cas._parse_mark_datetime("not-a-date"))

    def test_build_attendance_rows(self):
        marks = [
            {
                "user": {"id": 1, "last_name": "Ешан", "first_name": "Алмас", "middle_name": ""},
                "datetime": "2026-07-22T08:35:03+05:00",
                "status": 1,
                "source": "device",
            },
            {
                "user": {"id": 1, "last_name": "Ешан", "first_name": "Алмас"},
                "datetime": "2026-07-21T21:00:00+05:00",
                "status": "0",
                "source": "device",
            },
            # битые строки: без имени, без даты, без статуса — пропускаются
            {"user": {}, "datetime": "2026-07-22T08:00:00+05:00", "status": 1},
            {"user": {"last_name": "Тест", "first_name": "Тест"}, "datetime": "", "status": 1},
            {"user": {"last_name": "Тест", "first_name": "Тест"},
             "datetime": "2026-07-22T08:00:00+05:00", "status": None},
        ]
        rows = cas.build_attendance_rows(marks)
        self.assertEqual(len(rows), 2)
        # сортировка по времени внутри сотрудника
        self.assertEqual(rows[0]["event_at"], datetime(2026, 7, 21, 21, 0, 0))
        self.assertEqual(rows[0]["status"], 0)
        self.assertEqual(rows[1]["event_at"], datetime(2026, 7, 22, 8, 35, 3))
        self.assertEqual(rows[1]["status"], 1)
        self.assertEqual(rows[1]["employee_name"], "Ешан Алмас")
        self.assertEqual(rows[1]["clockster_user_id"], 1)

    def test_run_sync_skips_without_token(self):
        summary = cas.run_sync(lambda rows: {"ok": True}, config={"api_url": cas.DEFAULT_API_URL, "token": "", "days_back": 2})
        self.assertTrue(summary.get("skipped"))
        self.assertEqual(summary.get("reason"), "no_credentials")

    def test_default_date_range_format(self):
        start, end = cas.default_date_range(2)
        self.assertRegex(start, r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(end, r"^\d{4}-\d{2}-\d{2}$")
        self.assertLessEqual(start, end)


class ClocksterImporterSourceTests(unittest.TestCase):
    """Импортёр и обвязка в bot_schedule2.py (исходные ассерты)."""

    def setUp(self):
        self.src = _read(BOT_PATH)

    def test_importer_scopes_to_sales_department(self):
        self.assertIn("CLOCKSTER_SYNC_DEPARTMENT_CODE", self.src)
        self.assertIn("def _clockster_attendance_sync_importer(", self.src)
        func = self.src[self.src.index("def _clockster_attendance_sync_importer("):][:4000]
        # Матчинг ограничен сотрудниками отдела продаж — иначе replace-семантика
        # импорта стёрла бы Oktell-статусы СЗоВ, отметившихся в Clockster.
        self.assertIn("get_department_member_ids", func)
        self.assertIn("restrict_to_ids=member_ids", func)
        self.assertIn("_status_import_resolve_operator_matches", func)
        self.assertIn("db.save_operator_status_import", func)
        self.assertIn("'clockster'", func)

    def test_mark_states_map_to_operator_profile(self):
        # Приход — рабочий статус операторского профиля, уход — офлайн.
        self.assertIn("CLOCKSTER_MARK_IN_STATE = 'Готов'", self.src)
        self.assertIn("CLOCKSTER_MARK_OUT_STATE = 'Выключен'", self.src)

    def test_manual_endpoint(self):
        self.assertIn("def sync_work_schedules_statuses_clockster():", self.src)
        endpoint = self.src[self.src.index("def sync_work_schedules_statuses_clockster():"):][:3500]
        self.assertIn("больше 10 дней", endpoint)
        self.assertIn("CLOCKSTER_API_TOKEN", endpoint)
        self.assertIn("STATUS_IMPORT_LOCK", endpoint)
        self.assertIn("clockster_attendance_sync.run_sync", endpoint)
        self.assertIn("_clockster_attendance_sync_importer", endpoint)

    def test_daily_job_registered(self):
        self.assertIn("clockster_attendance_sync_daily", self.src)
        self.assertIn("async def clockster_attendance_sync_job():", self.src)


class ClocksterFrontendTests(unittest.TestCase):
    """Кнопка синка в планировщике графиков (App.jsx)."""

    def setUp(self):
        self.src = _read(APP_PATH)

    def test_sync_button_for_sales_planner(self):
        self.assertIn("sync_statuses_clockster", self.src)
        self.assertIn("syncPlannerClocksterStatuses", self.src)
        self.assertIn("Отметки Clockster", self.src)
        # Кнопка видна планировщику отдела продаж и админам.
        self.assertIn("(isOpPlanner || isAdminLikePlanner)", self.src)

    def test_clipped_to_endpoint_limit(self):
        func = self.src[self.src.index("const syncPlannerClocksterStatuses = async ()"):][:3500]
        self.assertIn("CLOCKSTER_SYNC_MAX_DAYS", func)
        self.assertIn("slice(-CLOCKSTER_SYNC_MAX_DAYS)", func)


if __name__ == "__main__":
    unittest.main()
