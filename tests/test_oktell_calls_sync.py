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


BOT_PATH = source_cache.monolith_flat_path()


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
    def __init__(self, save_result=None, save_error=None, events=None):
        super().__init__()
        self.saved = []
        self.phone_saved = []
        self.save_result = save_result if save_result is not None else {"aggregated_months": 0}
        self.save_error = save_error
        self.events = events if events is not None else []

    def get_all_operators(self):
        return [(613,), (77,), (518,)]

    def upsert_daily_calls(self, rows):
        self.events.append("oktell_written")
        self.saved.append(list(rows))
        if self.save_error:
            raise self.save_error
        return self.save_result

    def upsert_daily_phone_calls(self, rows, day_from=None, day_to=None):
        self.events.append("phone_written")
        self.phone_saved.append((list(rows), day_from, day_to))
        return {"aggregated_months": 0}


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

    def _ns(self, fetch, db, dial_list=None):
        return _extract(
            ["_oktell_parse_date", "_oktell_calls_target_days", "_oktell_calls_record_run",
             "_softphone_calls_for_hours", "_sync_softphone_calls", "sync_oktell_operator_calls"],
            # Раздел «Удаленный КЦ»: None — не поднят, о звонках из телефона ничего не известно.
            _dial_list_service=dial_list,
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


class _FakeDialList:
    """Раздел «Удаленный КЦ» глазами синка: сколько звонков из телефона у кого за день."""

    def __init__(self, calls=None, error=None):
        self.calls = dict(calls or {})
        self.error = error
        self.asked = []

    def hours_calls(self, day_from, day_to):
        self.asked.append((day_from, day_to))
        if self.error:
            raise self.error
        return dict(self.calls)


class SoftphoneCallsTests(unittest.TestCase):
    """Задача SZOV-4 (владелец 10.11.2026: «все звонки нужно учитывать»). Оператор СЗоВ на обзвоне
    звонит из iCORE Phone: часы идут по телефону, а «Звонки» дня писал один Oktell — «звонков в
    час» у него падал. Звонки телефона пишутся в то же число, но отдельно от звонков Oktell и до
    него: 10–11.11.2026 шлюз Oktell не отвечал сутки, прогон падал — и звонки телефона,
    ехавшие одним числом со звонками Oktell, не записались ни разу."""

    # Те же стабы, что у синка Oktell (сами его тесты здесь второй раз не идут). Люди выдуманы:
    # 77 — оператор и в Oktell, и в телефоне; 613 — только в телефоне; 518 — чат-менеджер.
    NAMES = {"Обзвонова Айша": [{"id": 77}]}
    _ns = SyncOperatorCallsTests._ns
    _assert_lock_released = SyncOperatorCallsTests._assert_lock_released
    OKTELL = [{"OperatorName": "Обзвонова Айша", "CallDate": "2026-11-10", "CallCount": 40}]
    DAY = date(2026, 11, 10)
    DAY_BEFORE = date(2026, 11, 9)

    def _sync(self, dial_list, rows=None, db=None, fetch=None, **period):
        db = db or _FakeDB()

        def default_fetch(d_from, d_to):
            db.events.append("oktell_asked")
            return list(self.OKTELL if rows is None else rows)

        ns = self._ns(fetch or default_fetch, db, dial_list=dial_list)
        return ns["sync_oktell_operator_calls"](**(period or {"day": "2026-11-10"})), db, ns

    def test_softphone_calls_are_written_apart_from_oktell_calls(self):
        dial_list = _FakeDialList({
            (77, "2026-11-10"): 120,      # с утра линия Oktell, после обеда обзвон
            (613, "2026-11-10"): 95,      # весь день на обзвоне: в Oktell звонков нет вовсе
        })
        result, db, ns = self._sync(dial_list)

        self.assertEqual(result["status"], "success")
        # Звонки телефона — своей записью, с периодом; звонки Oktell — своей, одними числами Oktell:
        # сумму собирает база, которая помнит часть телефона у дня.
        self.assertEqual(db.phone_saved, [(
            [(77, "2026-11-10", 120), (613, "2026-11-10", 95)], self.DAY_BEFORE, self.DAY)])
        self.assertEqual(db.saved, [[(77, "2026-11-10", 40)]])
        self.assertEqual((result["phone_calls"], result["phone_days"], result["phone_operators"]), (215, 2, 2))
        self.assertEqual((result["updated"], result["operators_count"]), (1, 1))
        self.assertEqual(db.runs[0]["updated_days"], 1)
        self._assert_lock_released(ns)

    def test_softphone_calls_are_written_before_oktell_is_asked(self):
        dial_list = _FakeDialList({(77, "2026-11-10"): 120})
        _result, db, _ns = self._sync(dial_list)

        self.assertEqual(db.events, ["phone_written", "oktell_asked", "oktell_written"])

    def test_oktell_outage_does_not_stop_softphone_calls(self):
        def refused(d_from, d_to):
            raise ConnectionRefusedError(111, "Connection refused")

        dial_list = _FakeDialList({(77, "2026-11-10"): 112, (613, "2026-11-10"): 95})
        result, db, ns = self._sync(dial_list, fetch=refused)

        # Прогон упал — день для Oktell остался недобранным, ночь его перечитает…
        self.assertEqual(result["status"], "failed")
        self.assertIn("Connection refused", result["error"])
        self.assertEqual(db.saved, [])
        self.assertEqual([r["status"] for r in db.runs], ["failed"])
        # …а звонки телефона уже записаны, и прогон об этом говорит.
        self.assertEqual(db.phone_saved, [(
            [(77, "2026-11-10", 112), (613, "2026-11-10", 95)], self.DAY_BEFORE, self.DAY)])
        self.assertEqual((result["phone_calls"], result["phone_days"], result["phone_operators"]), (207, 2, 2))
        self._assert_lock_released(ns)

    def test_one_day_run_also_takes_the_day_before(self):
        # Дневной прогон «сегодня» дописывает и вчера: звонки после последнего прогона того дня и
        # день, чей ночной прогон не прошёл, не ждут следующей ночи.
        dial_list = _FakeDialList({(77, "2026-11-09"): 7, (77, "2026-11-10"): 120})
        result, db, _ns = self._sync(dial_list)

        self.assertEqual(dial_list.asked, [(self.DAY_BEFORE, self.DAY)])
        self.assertEqual(db.phone_saved, [(
            [(77, "2026-11-09", 7), (77, "2026-11-10", 120)], self.DAY_BEFORE, self.DAY)])
        self.assertEqual((result["phone_calls"], result["phone_days"], result["phone_operators"]), (127, 2, 1))
        # Oktell спрошен только за свой день.
        self.assertEqual(db.saved, [[(77, "2026-11-10", 40)]])

    def test_longer_period_is_taken_as_asked(self):
        dial_list = _FakeDialList({(77, "2026-11-03"): 7})
        _result, db, _ns = self._sync(dial_list, rows=[], date_from="2026-11-03", date_to="2026-11-10")

        self.assertEqual(dial_list.asked, [(date(2026, 11, 3), self.DAY)])
        self.assertEqual(db.phone_saved, [([(77, "2026-11-03", 7)], date(2026, 11, 3), self.DAY)])

    def test_gate_of_the_day_before_does_not_widen_the_oktell_lookup(self):
        gates = []
        lookups = []
        db = _FakeDB()
        ns = self._ns(lambda d_from, d_to: [], db, dial_list=_FakeDialList({(518, "2026-11-09"): 30}))

        def gate(d_from, d_to, ids):
            gates.append((d_from, d_to))
            # 518 — оператор до 09.11 включительно, с 10.11 чат-менеджер.
            return ({518} if d_from <= self.DAY_BEFORE else set(),
                    lambda op_id, day: not (op_id == 518 and day >= "2026-11-10"))

        ns["_oktell_operator_model_gate"] = gate
        ns["_status_import_build_operator_lookup"] = lambda restrict_to_ids=None: lookups.append(
            set(restrict_to_ids)) or {}
        ns["sync_oktell_operator_calls"](day="2026-11-10")

        self.assertEqual(gates, [(self.DAY, self.DAY), (self.DAY_BEFORE, self.DAY)])
        # Сопоставление имён Oktell — по гейту своего дня: вчерашний оператор в него не попал…
        self.assertEqual(lookups, [set()])
        # …а его вчерашние звонки из телефона записаны по гейту вчерашнего дня.
        self.assertEqual(db.phone_saved, [([(518, "2026-11-09", 30)], self.DAY_BEFORE, self.DAY)])

    def test_chat_manager_day_takes_no_softphone_calls(self):
        # 518 с 19.10 чат-менеджер: его день не операторский — ни звонки Oktell, ни звонки телефона.
        dial_list = _FakeDialList({(518, "2026-11-10"): 30, (77, "2026-11-10"): 5})
        result, db, _ns = self._sync(dial_list)

        self.assertEqual(db.phone_saved, [([(77, "2026-11-10", 5)], self.DAY_BEFORE, self.DAY)])
        self.assertEqual((result["phone_calls"], result["phone_days"], result["phone_operators"]), (5, 1, 1))

    def test_no_softphone_calls_still_pass_the_period(self):
        # Строк нет, а период передан: день, где звонки телефона были записаны и пропали
        # (отказы АТС, рубильник), база по нему обнулит.
        dial_list = _FakeDialList({(613, "2026-11-10"): 0})
        result, db, _ns = self._sync(dial_list, rows=[])

        self.assertEqual(db.phone_saved, [([], self.DAY_BEFORE, self.DAY)])
        self.assertEqual(db.saved, [[]])
        self.assertEqual((result["updated"], result["phone_calls"], result["phone_days"]), (0, 0, 0))

    def test_without_the_dial_out_section_stored_softphone_calls_are_left_alone(self):
        # Раздел не поднят — о звонках ничего не известно: не пишем ничего, даже пустой период,
        # иначе уже записанные звонки телефона обнулились бы.
        result, db, _ns = self._sync(None)

        self.assertEqual(result["status"], "success")
        self.assertEqual(db.phone_saved, [])
        self.assertEqual(db.saved, [[(77, "2026-11-10", 40)]])
        self.assertEqual((result["phone_calls"], result["phone_days"], result["phone_operators"]), (0, 0, 0))

    def test_unread_softphone_calls_fail_the_run_before_oktell(self):
        dial_list = _FakeDialList(error=RuntimeError("dial_list_attempts is unavailable"))
        result, db, ns = self._sync(dial_list)

        self.assertEqual(result["status"], "failed")
        self.assertIn("dial_list_attempts is unavailable", result["error"])
        self.assertEqual((db.phone_saved, db.saved, db.events), ([], [], []))
        self.assertEqual([r["status"] for r in db.runs], ["failed"])
        self.assertEqual((result["phone_calls"], result["phone_days"]), (0, 0))
        self._assert_lock_released(ns)

    def test_manual_sync_says_what_was_written(self):
        source = BOT_PATH.read_text(encoding="utf-8")
        route = source.split("@app.route('/api/hours/sync_calls_oktell'", 1)[1].split("\n@app.route(", 1)[0]
        self.assertIn('"Звонки операторов синхронизированы из Oktell и iCORE Phone"', route)
        self.assertIn("if result.get('phone_calls') else \"Звонки операторов синхронизированы из Oktell\"", route)
        # Звонки Oktell не прошли, а звонки телефона записаны — страница перечитывает «Учёт часов»
        # (ответ 200), а не показывает ошибку всей кнопки.
        partial = route.split("if status == 'failed':", 1)[1].split("return jsonify({\"error\"", 1)[0]
        self.assertIn("if result.get('phone_days'):", partial)
        self.assertIn('"status": "partial"', partial)
        self.assertIn('"Звонки Oktell обновить не удалось. Звонки из iCORE Phone записаны"', partial)
        self.assertIn("}), 200", partial)
        self.assertIn("updated=int(result.get('phone_days') or 0)", partial)
        self.assertIn("operators_count=int(result.get('phone_operators') or 0)", partial)


if __name__ == "__main__":
    unittest.main()
