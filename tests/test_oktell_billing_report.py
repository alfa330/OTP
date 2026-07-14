"""Тесты «Биллинг Oktell» (Расчет ресурсов).

Проверяют SQL-билдер, разбор времени HH:MM, агрегацию день×таксопарк и
чанковую выгрузку с фолбэком по одному дню при упоре в лимит прокси (1000 строк).

Приём тот же, что в test_oktell_operator_day_gate.py: функции извлекаются из
bot_schedule2.py через AST и исполняются в изолированном namespace со стабами
(модуль целиком импортировать нельзя — сетевые/БД сайд-эффекты на импорте).
"""

import ast
import re
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path


BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"

FUNCTION_NAMES = [
    "_oktell_billing_parse_time",
    "_oktell_billing_minute_filter",
    "_oktell_billing_sql",
    "_oktell_fetch_billing_rows",
    "_oktell_billing_int",
    "_oktell_billing_sec",
    "_oktell_billing_build_report",
    "_oktell_billing_operator_calls_sql",
    "_oktell_billing_operator_states_sql",
    "_oktell_fetch_billing_operator_rows",
    "_oktell_billing_build_operator_report",
]


def _extract_namespace(oktell_query=None, page_size=1000, chunk_days=7):
    module = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    wanted = set(FUNCTION_NAMES)
    selected = [
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    found = {node.name for node in selected}
    missing = wanted - found
    if missing:
        raise AssertionError("Не найдены функции в bot_schedule2.py: %s" % sorted(missing))
    ns = {
        "re": re,
        "datetime": datetime,
        "timedelta": timedelta,
        "_OKTELL_GREETING_ABANDON": "Бросили трубку на приветствии",
        "_OKTELL_FAILED_CALL": "Неудачный звонок",
        "_OKTELL_BILLING_METRICS": (
            "arrived", "served", "lost", "served_sl", "greet_drop",
            "talk_seconds", "wait_ok_seconds", "wait_lost_seconds", "total_seconds",
        ),
        "_OKTELL_BILLING_OPERATOR_METRICS": (
            "served", "talk_seconds", "talk_in_seconds", "talk_out_seconds", "postproc_seconds",
            "hold_seconds", "wait_seconds", "pause_seconds", "dial_seconds",
        ),
        "OKTELL_API_PAGE_SIZE": page_size,
        "OKTELL_RESOURCE_CHUNK_DAYS": chunk_days,
        "_oktell_query": oktell_query or (lambda sql: []),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"), ns)
    return ns


class ParseTimeTests(unittest.TestCase):
    def setUp(self):
        self.ns = _extract_namespace()

    def test_valid_times(self):
        parse = self.ns["_oktell_billing_parse_time"]
        self.assertEqual(parse("00:00"), 0)
        self.assertEqual(parse("08:30"), 510)
        self.assertEqual(parse("23:59"), 1439)
        self.assertEqual(parse("9:05"), 545)
        self.assertEqual(parse(" 10:00 "), 600)

    def test_invalid_times(self):
        parse = self.ns["_oktell_billing_parse_time"]
        for bad in ("24:00", "12:60", "1200", "12", "", None, "ab:cd", "12:5"):
            self.assertIsNone(parse(bad), bad)


class SqlBuilderTests(unittest.TestCase):
    def setUp(self):
        self.ns = _extract_namespace()

    def test_full_day_has_no_minute_filter(self):
        sql = self.ns["_oktell_billing_sql"]("20260701", "20260702", 0, 1439, 20)
        self.assertNotIn("DATEPART(MINUTE", sql)
        self.assertIn("t.dt_insert >= '20260701'", sql)
        self.assertIn("t.dt_insert < '20260702'", sql)
        self.assertIn("GROUP BY CONVERT(varchar(10), t.dt_insert, 23), t.taxi_park", sql)

    def test_time_window_adds_minute_filter(self):
        sql = self.ns["_oktell_billing_sql"]("20260701", "20260702", 480, 1200, 20)
        self.assertIn("BETWEEN 480 AND 1200", sql)
        self.assertIn("DATEPART(HOUR, t.dt_insert) * 60 + DATEPART(MINUTE, t.dt_insert)", sql)

    def test_base_filters_and_sl(self):
        sql = self.ns["_oktell_billing_sql"]("20260701", "20260702", 0, 1439, 25)
        self.assertIn("t.route = 'incoming'", sql)
        self.assertIn("t.taxi_park <> ''", sql)
        self.assertIn("N'Неудачный звонок'", sql)
        self.assertIn("t.LenQueue <= 25", sql)
        # прокси не принимает несколько statement'ов
        self.assertNotIn(";", sql)

    def test_park_mode_has_no_line_join(self):
        sql = self.ns["_oktell_billing_sql"]("20260701", "20260702", 0, 1439, 20, "park")
        self.assertNotIn("A_Stat_Connections_1x1", sql)
        self.assertNotIn("line_number", sql)

    def test_line_mode_joins_dialed_number(self):
        sql = self.ns["_oktell_billing_sql"]("20260701", "20260708", 0, 1439, 20, "line")
        self.assertIn("MIN(ANumberDialed) AS line_number", sql)
        self.assertIn("ConnectionType = 4", sql)
        # окно по TimeStart расширено на день назад (звонок может начаться до полуночи)
        self.assertIn("TimeStart >= '20260630'", sql)
        self.assertIn("TimeStart < '20260708'", sql)
        self.assertIn("TRY_CAST(t.chainid AS uniqueidentifier)", sql)
        self.assertIn("GROUP BY CONVERT(varchar(10), t.dt_insert, 23), t.taxi_park, COALESCE(c.line_number, N'')", sql)
        self.assertNotIn(";", sql)

    def test_operator_sql_builders(self):
        calls_sql = self.ns["_oktell_billing_operator_calls_sql"]("20260701", "20260702", 480, 1200)
        self.assertIn("A_Cube_CC_Cat_OperatorInfo", calls_sql)
        self.assertIn("TRY_CAST(t.id_operator AS uniqueidentifier)", calls_sql)
        self.assertIn("t.call_result IN (5)", calls_sql)
        self.assertIn("BETWEEN 480 AND 1200", calls_sql)
        self.assertNotIn(";", calls_sql)
        states_sql = self.ns["_oktell_billing_operator_states_sql"]("20260701", "20260702", 480, 1200)
        self.assertIn("A_Cube_CC_OperatorStates", states_sql)
        for state_alias in ("talk_in_seconds", "talk_out_seconds", "postproc_seconds",
                            "hold_seconds", "wait_seconds", "pause_seconds", "dial_seconds"):
            self.assertIn(state_alias, states_sql)
        self.assertIn("DATEPART(HOUR, s.DateTimeStart)", states_sql)
        self.assertNotIn(";", states_sql)


class BuildReportTests(unittest.TestCase):
    def setUp(self):
        self.ns = _extract_namespace()

    @staticmethod
    def _row(report_date, park, arrived=0, served=0, **extra):
        row = {
            "report_date": report_date,
            "taxi_park": park,
            "arrived": arrived,
            "served": served,
            "served_sl": 0,
            "greet_drop": 0,
            "talk_seconds": 0,
            "wait_ok_seconds": 0,
            "wait_lost_seconds": 0,
            "total_seconds": 0,
        }
        row.update(extra)
        return row

    def test_grouping_totals_and_sorting(self):
        build = self.ns["_oktell_billing_build_report"]
        report = build([
            self._row("2026-07-02", "Amanat", arrived=5, served=4, talk_seconds=100.6),
            self._row("2026-07-01", "iTaxi", arrived=50, served=45, served_sl=40, talk_seconds=1000),
            self._row("2026-07-01", "Amanat", arrived=10, served=9, talk_seconds=200),
        ])
        self.assertEqual([day["date"] for day in report["days"]], ["2026-07-01", "2026-07-02"])
        first_day = report["days"][0]
        # сортировка по «поступило» убыванием
        self.assertEqual([p["park"] for p in first_day["parks"]], ["iTaxi", "Amanat"])
        self.assertEqual(first_day["totals"]["arrived"], 60)
        self.assertEqual(first_day["totals"]["served"], 54)
        self.assertEqual(first_day["totals"]["lost"], 6)
        # итоги за период по паркам
        parks = {p["park"]: p for p in report["parks"]}
        self.assertEqual(parks["Amanat"]["arrived"], 15)
        self.assertEqual(parks["Amanat"]["talk_seconds"], 301)  # 200 + round(100.6)
        self.assertEqual(report["totals"]["arrived"], 65)
        self.assertEqual(report["totals"]["served_sl"], 40)

    def test_lost_clamped_and_bad_rows_skipped(self):
        build = self.ns["_oktell_billing_build_report"]
        report = build([
            self._row("2026-07-01", "Amanat", arrived=3, served=5),  # served > arrived
            self._row("", "iTaxi", arrived=10, served=10),           # нет даты
            self._row("2026-07-01", "", arrived=10, served=10),      # пустой парк
            self._row("2026-07-01", "Global", arrived=None, served=None, talk_seconds="oops"),
        ])
        self.assertEqual(len(report["days"]), 1)
        parks = {p["park"]: p for p in report["days"][0]["parks"]}
        self.assertEqual(set(parks), {"Amanat", "Global"})
        self.assertEqual(parks["Amanat"]["lost"], 0)
        self.assertEqual(parks["Global"]["talk_seconds"], 0)
        self.assertEqual(report["totals"]["arrived"], 3)

    def test_empty_input(self):
        report = self.ns["_oktell_billing_build_report"]([])
        self.assertEqual(report, {"days": [], "parks": [], "totals": {
            "arrived": 0, "served": 0, "lost": 0, "served_sl": 0, "greet_drop": 0,
            "talk_seconds": 0, "wait_ok_seconds": 0, "wait_lost_seconds": 0, "total_seconds": 0,
        }})

    def test_line_grouping_splits_park(self):
        build = self.ns["_oktell_billing_build_report"]
        report = build([
            self._row("2026-07-01", "Честный", arrived=10, served=9, line_number="+77003330402"),
            self._row("2026-07-01", "Честный", arrived=3, served=3, line_number="+77001110200"),
            self._row("2026-07-01", "iTaxi", arrived=50, served=45, line_number="+77075050880"),
        ], include_line=True)
        day_rows = report["days"][0]["parks"]
        self.assertEqual(
            [(r["park"], r["line"]) for r in day_rows],
            [("iTaxi", "+77075050880"), ("Честный", "+77003330402"), ("Честный", "+77001110200")],
        )
        # итог дня объединяет обе линии парка
        self.assertEqual(report["days"][0]["totals"]["arrived"], 63)
        parks = {(p["park"], p["line"]) for p in report["parks"]}
        self.assertEqual(len(parks), 3)

    def test_without_line_flag_lines_merge(self):
        build = self.ns["_oktell_billing_build_report"]
        report = build([
            self._row("2026-07-01", "Честный", arrived=10, served=9, line_number="+77003330402"),
            self._row("2026-07-01", "Честный", arrived=3, served=3, line_number="+77001110200"),
        ], include_line=False)
        rows = report["days"][0]["parks"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["arrived"], 13)
        self.assertNotIn("line", rows[0])


class BuildOperatorReportTests(unittest.TestCase):
    def setUp(self):
        self.ns = _extract_namespace()

    def test_merge_calls_and_states(self):
        build = self.ns["_oktell_billing_build_operator_report"]
        calls = [
            {"report_date": "2026-07-01", "operator_name": "Иванова А.", "served": 30, "talk_seconds": 6000.4},
            {"report_date": "2026-07-02", "operator_name": "Иванова А.", "served": 20, "talk_seconds": 4000},
        ]
        states = [
            {"report_date": "2026-07-01", "operator_name": "Иванова А.", "talk_in_seconds": 5900,
             "talk_out_seconds": 0, "postproc_seconds": 100, "hold_seconds": 50,
             "wait_seconds": 3000, "pause_seconds": 1000, "dial_seconds": 20},
            {"report_date": "2026-07-01", "operator_name": "Петров Б.", "talk_in_seconds": 0,
             "talk_out_seconds": 2000, "postproc_seconds": 40, "hold_seconds": 0,
             "wait_seconds": 500, "pause_seconds": 0, "dial_seconds": 100},
        ]
        report = build(calls, states)
        self.assertEqual([d["date"] for d in report["days"]], ["2026-07-01", "2026-07-02"])
        day1 = {r["operator"]: r for r in report["days"][0]["operators"]}
        self.assertEqual(day1["Иванова А."]["served"], 30)
        self.assertEqual(day1["Иванова А."]["talk_seconds"], 6000)
        self.assertEqual(day1["Иванова А."]["hold_seconds"], 50)
        # Петров без входящих звонков, но с исходящими разговорами — остаётся
        self.assertEqual(day1["Петров Б."]["served"], 0)
        self.assertEqual(day1["Петров Б."]["talk_out_seconds"], 2000)
        period = {r["operator"]: r for r in report["operators"]}
        self.assertEqual(period["Иванова А."]["served"], 50)
        self.assertEqual(report["totals"]["served"], 50)

    def test_technical_accounts_dropped(self):
        build = self.ns["_oktell_billing_build_operator_report"]
        states = [
            {"report_date": "2026-07-01", "operator_name": "admin", "talk_in_seconds": 0,
             "talk_out_seconds": 0, "postproc_seconds": 0, "hold_seconds": 0,
             "wait_seconds": 65000, "pause_seconds": 5000, "dial_seconds": 0},
        ]
        report = build([], states)
        self.assertEqual(report["days"], [])
        self.assertEqual(report["operators"], [])

    def test_operator_fetch_runs_two_queries_per_chunk(self):
        calls = []

        def fake_query(sql):
            calls.append(sql)
            return [{"n": len(calls)}]

        ns = _extract_namespace(oktell_query=fake_query, chunk_days=7)
        calls_rows, states_rows = ns["_oktell_fetch_billing_operator_rows"](
            date(2026, 7, 1), date(2026, 7, 10), 0, 1439)
        # 10 дней = 2 чанка, по 2 запроса на чанк
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(calls_rows), 2)
        self.assertEqual(len(states_rows), 2)
        self.assertIn("Call_Systems_hst", calls[0])
        self.assertIn("A_Cube_CC_OperatorStates", calls[1])


class FetchChunkingTests(unittest.TestCase):
    def test_chunks_are_serial_windows(self):
        calls = []

        def fake_query(sql):
            calls.append(sql)
            return [{"report_date": "x"}]

        ns = _extract_namespace(oktell_query=fake_query, chunk_days=7)
        rows = ns["_oktell_fetch_billing_rows"](date(2026, 7, 1), date(2026, 7, 16), 0, 1439, 20)
        # 16 дней при чанке 7 -> окна 01-07, 08-14, 15-16
        self.assertEqual(len(calls), 3)
        self.assertIn("'20260701'", calls[0])
        self.assertIn("'20260708'", calls[0])
        self.assertIn("'20260708'", calls[1])
        self.assertIn("'20260715'", calls[1])
        self.assertIn("'20260715'", calls[2])
        self.assertIn("'20260717'", calls[2])
        self.assertEqual(len(rows), 3)

    def test_truncated_window_falls_back_to_days(self):
        calls = []

        def fake_query(sql):
            calls.append(sql)
            # первое (оконное) обращение упирается в лимит, дневные — нет
            if len(calls) == 1:
                return [{"n": i} for i in range(5)]
            return [{"n": "day"}]

        ns = _extract_namespace(oktell_query=fake_query, page_size=5, chunk_days=7)
        rows = ns["_oktell_fetch_billing_rows"](date(2026, 7, 1), date(2026, 7, 3), 0, 1439, 20)
        # 1 оконный + 3 дневных запроса; строки берутся только из дневных
        self.assertEqual(len(calls), 4)
        self.assertEqual(rows, [{"n": "day"}] * 3)

    def test_single_day_truncated_keeps_page(self):
        calls = []

        def fake_query(sql):
            calls.append(sql)
            return [{"n": i} for i in range(5)]

        ns = _extract_namespace(oktell_query=fake_query, page_size=5, chunk_days=7)
        rows = ns["_oktell_fetch_billing_rows"](date(2026, 7, 1), date(2026, 7, 1), 0, 1439, 20)
        # окно из одного дня не дробится (a == b) — страница остаётся как есть
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(rows), 5)


if __name__ == "__main__":
    unittest.main()
