# -*- coding: utf-8 -*-

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tez_op_productivity as productivity  # noqa: E402


def _unix(year, month, day, hour=0, minute=0, second=0):
    return int(datetime(
        year, month, day, hour, minute, second, tzinfo=timezone.utc
    ).timestamp())


def _call(
    call_id,
    start_time,
    employee_name="Оператор Один",
    waitsec=0,
    billsec=0,
):
    return {
        "general_call_id": str(call_id),
        "start_time": start_time,
        "employee_name": employee_name,
        "waitsec": waitsec,
        "billsec": billsec,
    }


class FakeDb:
    def __init__(self, internal_numbers):
        self.internal_numbers = internal_numbers
        self.lookup_calls = []
        self.replace_calls = []

    def get_tez_op_binotel_internal_numbers(self, start, end):
        self.lookup_calls.append((start, end))
        return self.internal_numbers

    def replace_tez_op_call_metrics(self, start, end, rows):
        self.replace_calls.append((start, end, rows))
        return {"rows": len(rows)}


class FakeClient:
    def __init__(self, calls_by_internal=None, errors_by_internal=None):
        self.calls_by_internal = calls_by_internal or {}
        self.errors_by_internal = errors_by_internal or {}
        self.requests = []

    def list_calls_by_internal_number(self, internal_number, start_ts, stop_ts):
        self.requests.append((str(internal_number), start_ts, stop_ts))
        error = self.errors_by_internal.get(str(internal_number))
        if error is not None:
            raise error
        return list(self.calls_by_internal.get(str(internal_number), []))


class TezOpProductivityTests(unittest.TestCase):
    CONFIG = {
        "api_key": "test-key",
        "api_secret": "test-secret",
        "tz": "Asia/Almaty",
        "base_url": "https://api.binotel.com/api/4.0",
    }

    @staticmethod
    def _resolver(employee_name, call_date):
        return {
            "Оператор Один": 101,
            "Оператор Два": 202,
        }.get(employee_name)

    def test_shared_sip_is_distinct_and_calls_are_deduplicated_globally(self):
        duplicated = _call(
            "g-shared",
            _unix(2026, 7, 10, 6),
            waitsec=12,
            billsec=60,
        )
        db = FakeDb(["901", 901, {"internalNumber": "902"}, "902"])
        client = FakeClient({
            "901": [
                duplicated,
                _call("g-901", _unix(2026, 7, 10, 7), waitsec=3, billsec=30),
            ],
            "902": [
                dict(duplicated),
                _call(
                    "g-902",
                    _unix(2026, 7, 10, 8),
                    employee_name="Оператор Два",
                    waitsec=5,
                    billsec=40,
                ),
            ],
        })

        summary = productivity.run_sync(
            db,
            self._resolver,
            "2026-07-10",
            "2026-07-10",
            binotel_client=client,
            config=self.CONFIG,
        )

        self.assertEqual(summary["status"], "success")
        self.assertEqual([request[0] for request in client.requests], ["901", "902"])
        self.assertEqual(summary["fetched_calls"], 4)
        self.assertEqual(summary["unique_calls"], 3)
        self.assertEqual(summary["duplicate_calls"], 1)
        self.assertEqual(len(db.replace_calls), 1)
        rows = db.replace_calls[0][2]
        by_operator = {row["operator_id"]: row for row in rows}
        self.assertEqual(by_operator[101]["calls"], 2)
        self.assertEqual(by_operator[202]["calls"], 1)

    def test_calls_split_by_almaty_calendar_day(self):
        db = FakeDb(["901"])
        client = FakeClient({
            "901": [
                # 18:30 UTC = 23:30 Asia/Almaty, 1 July.
                _call("g-before", _unix(2026, 7, 1, 18, 30)),
                # 19:30 UTC = 00:30 Asia/Almaty, 2 July.
                _call("g-after", _unix(2026, 7, 1, 19, 30)),
            ],
        })
        resolved_dates = []

        def resolver(name, call_date):
            resolved_dates.append(call_date.isoformat())
            return 101

        summary = productivity.run_sync(
            db,
            resolver,
            "2026-07-01",
            "2026-07-02",
            binotel_client=client,
            config=self.CONFIG,
        )

        self.assertEqual(summary["status"], "success")
        rows = db.replace_calls[0][2]
        self.assertEqual([row["day"] for row in rows], [
            "2026-07-01",
            "2026-07-02",
        ])
        self.assertEqual([row["calls"] for row in rows], [1, 1])
        self.assertEqual(resolved_dates, ["2026-07-01", "2026-07-02"])

    def test_wait_and_bill_seconds_are_summed_and_converted_to_hours(self):
        db = FakeDb(["901"])
        client = FakeClient({
            "901": [
                _call("g1", _unix(2026, 7, 5, 5), waitsec=10, billsec=60),
                _call("g2", _unix(2026, 7, 5, 6), waitsec="20", billsec="120"),
                _call("g3", _unix(2026, 7, 5, 7), waitsec=-5, billsec=30),
            ],
        })

        summary = productivity.run_sync(
            db,
            self._resolver,
            "2026-07-05",
            "2026-07-05",
            binotel_client=client,
            config=self.CONFIG,
        )

        self.assertEqual(summary["status"], "success")
        row = db.replace_calls[0][2][0]
        self.assertEqual(row["calls"], 3)
        self.assertEqual(row["dial_seconds"], 30)
        self.assertEqual(row["talk_seconds"], 210)
        self.assertAlmostEqual(row["dial_time"], 30 / 3600, places=6)
        self.assertAlmostEqual(row["talk_time"], 210 / 3600, places=6)

    def test_unknown_employee_and_missing_date_are_skipped(self):
        db = FakeDb(["901"])
        client = FakeClient({
            "901": [
                _call(
                    "unknown",
                    _unix(2026, 7, 6, 6),
                    employee_name="Неизвестный",
                    waitsec=10,
                    billsec=20,
                ),
                _call("no-date", None, waitsec=5, billsec=5),
            ],
        })

        summary = productivity.run_sync(
            db,
            self._resolver,
            "2026-07-06",
            "2026-07-06",
            binotel_client=client,
            config=self.CONFIG,
        )

        self.assertEqual(summary["status"], "success")
        self.assertEqual(summary["skipped_unknown_operator"], 1)
        self.assertEqual(summary["skipped_no_date"], 1)
        self.assertEqual(summary["rows"], 0)
        # Успешная пустая выгрузка всё равно очищает устаревшие строки периода.
        self.assertEqual(db.replace_calls, [("2026-07-06", "2026-07-06", [])])

    def test_fetch_error_does_not_save_partial_rows(self):
        db = FakeDb(["901", "902"])
        client = FakeClient(
            calls_by_internal={
                "901": [_call("g1", _unix(2026, 7, 7, 6), billsec=30)],
            },
            errors_by_internal={
                "902": RuntimeError("simulated upstream failure"),
            },
        )

        summary = productivity.run_sync(
            db,
            self._resolver,
            "2026-07-07",
            "2026-07-07",
            binotel_client=client,
            config=self.CONFIG,
        )

        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["error"], "binotel_fetch_failed")
        self.assertFalse(summary["saved"])
        self.assertEqual(summary["fetched_internal_numbers"], 1)
        self.assertEqual(db.replace_calls, [])

    def test_default_period_is_yesterday_and_today_in_almaty(self):
        start, end = productivity.default_date_range(
            now=datetime(2026, 7, 28, 20, 30, tzinfo=timezone.utc)
        )
        # 20:30 UTC = 01:30 следующего дня в Asia/Almaty.
        self.assertEqual((start, end), ("2026-07-28", "2026-07-29"))

    def test_config_readiness_requires_both_api_credentials(self):
        self.assertTrue(productivity.api_ready({
            "api_key": "k",
            "api_secret": "s",
        }))
        self.assertFalse(productivity.api_ready({
            "api_key": "k",
            "api_secret": "",
        }))

    def test_missing_credentials_skips_without_touching_database(self):
        db = FakeDb(["901"])

        summary = productivity.run_sync(
            db,
            self._resolver,
            "2026-07-08",
            "2026-07-08",
            config={
                "api_key": "",
                "api_secret": "",
                "tz": "Asia/Almaty",
            },
        )

        self.assertEqual(summary["status"], "skipped")
        self.assertEqual(summary["reason"], "no_credentials")
        self.assertEqual(db.lookup_calls, [])
        self.assertEqual(db.replace_calls, [])

    def test_concurrent_sync_is_rejected_by_lock(self):
        db = FakeDb(["901"])
        client = FakeClient()
        productivity._SYNC_LOCK.acquire()
        try:
            summary = productivity.run_sync(
                db,
                self._resolver,
                "2026-07-09",
                "2026-07-09",
                binotel_client=client,
                config=self.CONFIG,
            )
        finally:
            productivity._SYNC_LOCK.release()

        self.assertEqual(summary["status"], "skipped")
        self.assertEqual(summary["reason"], "locked")
        self.assertEqual(db.lookup_calls, [])
        self.assertEqual(db.replace_calls, [])


if __name__ == "__main__":
    unittest.main()
