"""Синк звонков Oktell в «Учёт часов»: окно ночного прогона и сборка строк для записи.

18.10.2026 прокси Oktell не принимал соединения примерно с 15:00 до утра: прогоны 17:20,
20:20, 23:20 и ночной за 18.10 упали, и звонки дня у всех операторов остались на срезе
14:20 (у оператора со сменой 15–20 — ноль). Ночной прогон теперь дозабирает дни, по которым
после их окончания не прошло ни одного удачного синка (история — oktell_calls_sync_runs),
а добранные дни не трогает, чтобы не стирать ручные правки звонков.

Функции извлекаются из bot_schedule2.py через AST и исполняются со стабами, как в
test_oktell_operator_day_gate.py: модуль целиком импортировать нельзя.
"""

import ast
import asyncio
import logging
import threading
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from tests import source_cache


BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"


def _module():
    return source_cache.parse(BOT_PATH.read_text(encoding="utf-8"))


def _extract(names, **extra):
    wanted = set(names)
    selected = [
        node for node in _module().body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted
    ]
    missing = wanted - {node.name for node in selected}
    if missing:
        raise AssertionError("Не найдены функции в bot_schedule2.py: %s" % sorted(missing))
    ns = {"datetime": datetime, "timedelta": timedelta, "logging": logging, "time": time}
    ns.update(extra)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"), ns)
    return ns


def _frozen_datetime(now):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    return FrozenDateTime


class _HistoryDB:
    """История прогонов: какие дни окна недобраны, и что синк в неё записал."""

    def __init__(self, unfinished=(), fail=False):
        self.unfinished = list(unfinished)
        self.fail = fail
        self.asked = []
        self.runs = []

    def get_oktell_calls_unfinished_days(self, date_from, date_to):
        self.asked.append((date_from, date_to))
        if self.fail:
            raise RuntimeError("history is unavailable")
        return [d for d in self.unfinished if date_from <= d <= date_to]

    def record_oktell_calls_sync_run(self, **kwargs):
        self.runs.append(kwargs)


class NightlyRangeTests(unittest.TestCase):
    def _range(self, today, db, lookback=7, max_range=31, now=None):
        ns = _extract(
            ["_oktell_calls_nightly_range"],
            OKTELL_CALLS_NIGHTLY_LOOKBACK_DAYS=lookback,
            OKTELL_CALLS_MAX_RANGE_DAYS=max_range,
            db=db,
        )
        if now is not None:
            ns["datetime"] = _frozen_datetime(now)
        return ns["_oktell_calls_nightly_range"](today)

    def test_ordinary_night_takes_only_yesterday(self):
        # Добранные дни не повторяем: ручные правки звонков СВ за позавчера и раньше остаются.
        db = _HistoryDB()
        self.assertEqual(self._range(date(2026, 10, 20), db), ("2026-10-19", "2026-10-19"))
        self.assertEqual(db.asked, [(date(2026, 10, 13), date(2026, 10, 18))])

    def test_night_after_the_outage_rereads_the_stuck_day(self):
        # Ночной прогон 19.10 за 18.10 упал; следующий, 20.10, обязан забрать 18.10 снова.
        db = _HistoryDB(unfinished=[date(2026, 10, 18)])
        self.assertEqual(self._range(date(2026, 10, 20), db), ("2026-10-18", "2026-10-19"))

    def test_window_crosses_month_boundary(self):
        db = _HistoryDB(unfinished=[date(2026, 10, 30)])
        self.assertEqual(self._range(date(2026, 11, 2), db), ("2026-10-30", "2026-11-01"))

    def test_unreadable_history_falls_back_to_yesterday(self):
        db = _HistoryDB(fail=True)
        self.assertEqual(self._range(date(2026, 10, 20), db), ("2026-10-19", "2026-10-19"))

    def test_window_never_exceeds_the_sync_period_limit(self):
        # Окно шире лимита периода уронило бы ночной прогон на проверке периода.
        db = _HistoryDB(unfinished=[date(2026, 10, 13)])
        self.assertEqual(self._range(date(2026, 10, 20), db, max_range=2), ("2026-10-19", "2026-10-19"))
        self.assertEqual(db.asked, [(date(2026, 10, 18), date(2026, 10, 18))])

    def test_lookback_of_one_day_does_not_read_history(self):
        db = _HistoryDB(unfinished=[date(2026, 10, 18)])
        self.assertEqual(self._range(date(2026, 10, 20), db, lookback=1), ("2026-10-19", "2026-10-19"))
        self.assertEqual(db.asked, [])

    def test_without_today_uses_server_local_date(self):
        db = _HistoryDB()
        result = self._range(None, db, now=datetime(2026, 10, 20, 5, 25))
        self.assertEqual(result, ("2026-10-19", "2026-10-19"))


class SchedulerJobsTests(unittest.TestCase):
    def test_nightly_job_passes_the_range_to_the_sync(self):
        calls = []

        def fake_sync(**kwargs):
            calls.append(kwargs)
            return {"status": "success"}

        ns = _extract(
            ["run_oktell_operator_calls_sync_async"],
            asyncio=asyncio,
            executor_pool=None,
            _oktell_calls_nightly_range=lambda: ("2026-10-18", "2026-10-19"),
            sync_oktell_operator_calls=fake_sync,
        )
        result = asyncio.run(ns["run_oktell_operator_calls_sync_async"]())

        self.assertEqual(result, {"status": "success"})
        self.assertEqual(calls, [{
            "date_from": "2026-10-18", "date_to": "2026-10-19", "triggered_by": "scheduler",
        }])

    def test_intraday_job_syncs_today(self):
        calls = []
        ns = _extract(
            ["run_oktell_operator_calls_today_sync_async"],
            asyncio=asyncio,
            executor_pool=None,
            sync_oktell_operator_calls=lambda **kwargs: calls.append(kwargs) or {"status": "success"},
        )
        ns["datetime"] = _frozen_datetime(datetime(2026, 10, 18, 17, 20))
        asyncio.run(ns["run_oktell_operator_calls_today_sync_async"]())
        self.assertEqual(calls, [{"day": "2026-10-18", "triggered_by": "scheduler-intraday"}])

    def test_jobs_are_registered_with_their_own_functions(self):
        registered = {}
        for node in ast.walk(_module()):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_job" and node.args):
                continue
            job_id = next((kw.value.value for kw in node.keywords
                           if kw.arg == "id" and isinstance(kw.value, ast.Constant)), None)
            if job_id and isinstance(node.args[0], ast.Name):
                registered[job_id] = node.args[0].id
        self.assertEqual(registered.get("oktell_operator_calls_nightly"), "run_oktell_operator_calls_sync_async")
        self.assertEqual(registered.get("oktell_operator_calls_intraday"), "run_oktell_operator_calls_today_sync_async")


class FetchCallCountsTests(unittest.TestCase):
    def _fetch(self, date_from, date_to, rows_for_window):
        queries = []

        def fake_query(sql, timeout=None):
            queries.append(sql)
            return rows_for_window(sql)

        ns = _extract(
            ["_oktell_call_count_sql", "_oktell_fetch_call_counts"],
            OKTELL_CALLS_CHUNK_DAYS=7,
            OKTELL_API_PAGE_SIZE=1000,
            _oktell_query=fake_query,
        )
        rows = ns["_oktell_fetch_call_counts"](date_from, date_to)
        return rows, queries

    def test_three_days_are_one_query_with_exclusive_upper_bound(self):
        rows, queries = self._fetch(date(2026, 10, 17), date(2026, 10, 19), lambda sql: [{"n": 1}])
        self.assertEqual(len(queries), 1)
        self.assertIn("os.DateStart >= '20261017' AND os.DateStart < '20261020'", queries[0])
        self.assertEqual(rows, [{"n": 1}])

    def test_capped_window_is_refetched_day_by_day_without_duplicates(self):
        def rows_for_window(sql):
            if "'20261017' AND os.DateStart < '20261020'" in sql:
                return [{"window": True}] * 1000
            return [{"day": sql.split("os.DateStart >= '")[1][:8]}]

        rows, queries = self._fetch(date(2026, 10, 17), date(2026, 10, 19), rows_for_window)
        self.assertEqual(len(queries), 4)
        self.assertEqual(rows, [{"day": "20261017"}, {"day": "20261018"}, {"day": "20261019"}])

    def test_a_month_is_split_into_week_windows(self):
        _rows, queries = self._fetch(date(2026, 10, 1), date(2026, 10, 31), lambda sql: [])
        self.assertEqual(len(queries), 5)
        self.assertIn("'20261029' AND os.DateStart < '20261101'", queries[-1])


class _FakeDB(_HistoryDB):
    def __init__(self, save_result=None, save_error=None):
        super().__init__()
        self.saved = []
        self.save_result = save_result if save_result is not None else {"aggregated_months": 0}
        self.save_error = save_error

    def get_all_operators(self):
        return [(613,), (77,), (518,)]

    def upsert_daily_calls(self, rows):
        self.saved.append(list(rows))
        if self.save_error:
            raise self.save_error
        return self.save_result


class SyncOperatorCallsTests(unittest.TestCase):
    NAMES = {
        # Одна сотрудница под двумя написаниями в Oktell — обе строки ложатся на id 613.
        "Садыкова Алида Айбековна": [{"id": 613}],
        "Садыкова Алида": [{"id": 613}],
        "Нұрғанат Нұрдана": [{"id": 77}],
        # Чат-менеджер с 19.10: его день 19.10 не операторский.
        "Палуанов Рахимжан": [{"id": 518}],
        "Тёзка Неоднозначная": [{"id": 1}, {"id": 2}],
    }

    def _ns(self, fetch, db):
        return _extract(
            ["_oktell_parse_date", "_oktell_calls_target_days", "_oktell_calls_record_run",
             "sync_oktell_operator_calls"],
            OKTELL_CALLS_MAX_RANGE_DAYS=31,
            OKTELL_CALLS_SYNC_LOCK=threading.Lock(),
            STATUS_IMPORT_INVALID_ROWS_PREVIEW_LIMIT=30,
            _oktell_calls_sync_enabled=lambda: True,
            _oktell_api_ready=lambda: True,
            _oktell_fetch_call_counts=fetch,
            _status_import_build_operator_lookup=lambda restrict_to_ids=None: {},
            _status_import_resolve_operator_matches=lambda name, lookup: list(self.NAMES.get(name, [])),
            _oktell_operator_model_gate=lambda d_from, d_to, ids: (
                set(ids), lambda op_id, day: not (op_id == 518 and day >= "2026-10-19")),
            db=db,
        )

    def _assert_lock_released(self, ns):
        lock = ns["OKTELL_CALLS_SYNC_LOCK"]
        self.assertTrue(lock.acquire(blocking=False))
        lock.release()

    def test_rows_are_summed_per_operator_day_and_written_in_one_call(self):
        rows = [
            {"OperatorName": "Садыкова Алида Айбековна", "CallDate": "2026-10-18", "CallCount": 120},
            {"OperatorName": "Садыкова Алида", "CallDate": "2026-10-18", "CallCount": 6},
            {"OperatorName": "Нұрғанат Нұрдана", "CallDate": "2026-10-18", "CallCount": 232},
            {"OperatorName": "Палуанов Рахимжан", "CallDate": "2026-10-18", "CallCount": 6},
            {"OperatorName": "Палуанов Рахимжан", "CallDate": "2026-10-19", "CallCount": 14},
            {"OperatorName": "Тёзка Неоднозначная", "CallDate": "2026-10-18", "CallCount": 9},
            {"OperatorName": "Неизвестный Человек", "CallDate": "2026-10-18", "CallCount": 3},
        ]
        db = _FakeDB(save_result={"saved_days": 3, "changed_days": 2, "aggregated_months": 2})
        ns = self._ns(lambda d_from, d_to: rows, db)

        result = ns["sync_oktell_operator_calls"](date_from="2026-10-18", date_to="2026-10-19")

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(db.saved), 1)
        self.assertEqual(sorted(db.saved[0]), [
            (77, "2026-10-18", 232),
            (518, "2026-10-18", 6),
            (613, "2026-10-18", 126),
        ])
        self.assertEqual(result["source_rows"], 7)
        self.assertEqual(result["updated"], 3)
        self.assertEqual(result["operators_count"], 3)
        self.assertEqual(result["skipped_non_operator_day"], 1)
        self.assertEqual(result["unmatched"], 2)
        self.assertEqual(result["aggregated_months"], 2)
        self.assertEqual(
            [item["reason"] for item in result["unmatched_preview"]],
            ["Найдено несколько операторов с таким именем", "Оператор не найден"],
        )
        # Слияние двух имён на одного оператора видно, а не молчит.
        self.assertEqual(result["merged"], 1)
        self.assertEqual(result["merged_preview"], [{
            "operator_id": 613, "day": "2026-10-18", "calls": 126,
            "names": ["Садыкова Алида", "Садыкова Алида Айбековна"],
        }])
        # Удачный прогон записан в историю: по ней ночью ищутся недобранные дни.
        self.assertEqual(len(db.runs), 1)
        run = db.runs[0]
        self.assertEqual((run["status"], run["date_from"], run["date_to"], run["updated_days"]),
                         ("success", date(2026, 10, 18), date(2026, 10, 19), 3))
        self.assertEqual(run["triggered_by"], "scheduler")
        self.assertLessEqual(run["started_at"], run["finished_at"])
        self._assert_lock_released(ns)

    def test_refused_proxy_fails_the_run_without_writing(self):
        def refused(d_from, d_to):
            raise ConnectionRefusedError(111, "Connection refused")

        db = _FakeDB()
        ns = self._ns(refused, db)

        result = ns["sync_oktell_operator_calls"](day="2026-10-18")

        self.assertEqual(result["status"], "failed")
        self.assertIn("Connection refused", result["error"])
        self.assertEqual(db.saved, [])
        # Неудачный прогон тоже в истории: день остаётся недобранным, и ночь его дозаберёт.
        self.assertEqual([(r["status"], r["date_from"]) for r in db.runs], [("failed", date(2026, 10, 18))])
        self.assertIn("Connection refused", db.runs[0]["error"])
        # Лок отпущен: следующий прогон не упрётся в «уже идёт».
        self._assert_lock_released(ns)

    def test_failed_database_write_fails_the_run(self):
        rows = [{"OperatorName": "Нұрғанат Нұрдана", "CallDate": "2026-10-18", "CallCount": 232}]
        db = _FakeDB(save_error=RuntimeError("database is down"))
        ns = self._ns(lambda d_from, d_to: rows, db)

        result = ns["sync_oktell_operator_calls"](day="2026-10-18")

        self.assertEqual(result["status"], "failed")
        self.assertEqual([r["status"] for r in db.runs], ["failed"])
        self._assert_lock_released(ns)

    def test_history_failure_does_not_break_the_sync(self):
        rows = [{"OperatorName": "Нұрғанат Нұрдана", "CallDate": "2026-10-18", "CallCount": 232}]
        db = _FakeDB()

        def broken_record(**kwargs):
            raise RuntimeError("history table is missing")

        db.record_oktell_calls_sync_run = broken_record
        ns = self._ns(lambda d_from, d_to: rows, db)

        result = ns["sync_oktell_operator_calls"](day="2026-10-18")

        self.assertEqual(result["status"], "success")
        self.assertEqual(db.saved, [[(77, "2026-10-18", 232)]])


if __name__ == "__main__":
    unittest.main()
