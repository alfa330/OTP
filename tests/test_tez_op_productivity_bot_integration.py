# -*- coding: utf-8 -*-

import ast
import logging
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8-sig")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tez_op_productivity  # noqa: E402


def _extract_function(name, namespace):
    tree = ast.parse(BOT_SOURCE)
    node = next(
        item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == name
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace[name]


class TezOpProductivityBotUnitTests(unittest.TestCase):
    def test_wrapper_passes_period_db_resolver_and_trigger(self):
        fake_db = object()
        resolver = object()
        wrapper = _extract_function(
            "sync_tez_op_productivity_metrics",
            {
                "db": fake_db,
                "_tez_op_operator_resolver": lambda: resolver,
                "logging": logging,
            },
        )

        with mock.patch.object(
            tez_op_productivity,
            "run_sync",
            return_value={"status": "success", "saved": True},
        ) as run_sync:
            result = wrapper(
                "2026-07-01",
                "2026-07-10",
                triggered_by="test",
            )

        run_sync.assert_called_once_with(
            fake_db,
            resolver,
            start="2026-07-01",
            end="2026-07-10",
            logger=mock.ANY,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["triggered_by"], "test")

    def test_wrapper_omits_period_for_module_default_window(self):
        wrapper = _extract_function(
            "sync_tez_op_productivity_metrics",
            {
                "db": "db",
                "_tez_op_operator_resolver": lambda: "resolver",
                "logging": logging,
            },
        )

        with mock.patch.object(
            tez_op_productivity,
            "run_sync",
            return_value={"status": "skipped", "reason": "no_credentials"},
        ) as run_sync:
            result = wrapper(triggered_by="scheduler")

        self.assertIsNone(run_sync.call_args.kwargs["start"])
        self.assertIsNone(run_sync.call_args.kwargs["end"])
        self.assertEqual(result["triggered_by"], "scheduler")


class TezOpProductivityBotSourceTests(unittest.TestCase):
    def test_manual_status_endpoint_returns_phone_metrics_without_breaking_status_result(self):
        start = BOT_SOURCE.index("def sync_work_schedules_statuses_binotel():")
        end = BOT_SOURCE.index(
            "\n@app.route('/api/hours/sync_calls_oktell'",
            start,
        )
        endpoint = BOT_SOURCE[start:end]

        self.assertIn("summary = _tez_status_sync_importer(csv_text)", endpoint)
        self.assertIn("sync_tez_op_productivity_metrics(", endpoint)
        self.assertIn("triggered_by='manual-status-sync'", endpoint)
        self.assertIn('"phone_metrics": phone_metrics', endpoint)
        self.assertIn('"import": summary', endpoint)
        self.assertIn('"status": "success"', endpoint)
        self.assertIn("STATUS_IMPORT_LOCK.release()", endpoint)
        self.assertIn('"status": "failed"', endpoint)

    def test_productivity_job_is_separate_and_uses_default_period(self):
        start = BOT_SOURCE.index("async def tez_op_productivity_sync_job():")
        end = BOT_SOURCE.index("async def clockster_attendance_sync_job():", start)
        job = BOT_SOURCE[start:end]

        self.assertIn("run_in_executor", job)
        self.assertIn("sync_tez_op_productivity_metrics(", job)
        self.assertIn("triggered_by='scheduler'", job)
        self.assertNotIn("tez_status_sync.run_sync", job)

    def test_productivity_job_runs_at_0115_almaty_between_other_jobs(self):
        registration = (
            "scheduler.add_job(\n"
            "        tez_op_productivity_sync_job,\n"
            "        CronTrigger(hour=1, minute=15, "
            "timezone=ZoneInfo('Asia/Almaty')),\n"
            "        id='tez_op_productivity_daily'"
        )
        self.assertIn(registration, BOT_SOURCE)
        self.assertLess(
            BOT_SOURCE.index("id='tez_status_sync_daily'"),
            BOT_SOURCE.index("id='tez_op_productivity_daily'"),
        )
        self.assertLess(
            BOT_SOURCE.index("id='tez_op_productivity_daily'"),
            BOT_SOURCE.index("id='tez_op_successes_daily'"),
        )


if __name__ == "__main__":
    unittest.main()
