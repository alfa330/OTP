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
from datetime import date, timedelta
from pathlib import Path


BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"

FUNCTION_NAMES = [
    "_oktell_billing_parse_time",
    "_oktell_billing_sql",
    "_oktell_fetch_billing_rows",
    "_oktell_billing_build_report",
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
        "timedelta": timedelta,
        "_OKTELL_GREETING_ABANDON": "Бросили трубку на приветствии",
        "_OKTELL_FAILED_CALL": "Неудачный звонок",
        "_OKTELL_BILLING_METRICS": (
            "arrived", "served", "lost", "served_sl", "greet_drop",
            "talk_seconds", "wait_ok_seconds", "wait_lost_seconds", "total_seconds",
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
