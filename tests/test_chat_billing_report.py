# -*- coding: utf-8 -*-
"""«Биллинг чатов» (задача #343): оценка, время ответа, первая реакция в минутах,
«Группировка» по часам и выгрузка по таксопаркам."""
import ast
import math
import re
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from resource_fte.chat import (
    CHAT_BILLING_DETAIL_EXPORT_LIMIT,
    CHAT_BILLING_METRICS,
    build_chat_billing_grouping,
    get_chat_billing_details,
    get_chat_billing_grouping,
    get_chat_billing_grouping_by_park,
    get_chat_billing_operators,
    get_chat_billing_report,
)
from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
VIEW_PATH = ROOT / "src" / "components" / "resources" / "ResourceFteView.jsx"
METRICS_JS_PATH = ROOT / "src" / "components" / "resources" / "chatBillingMetrics.js"

FUNCTION_NAMES = (
    "_chat_billing_round_half_up",
    "_chat_billing_minutes",
    "_chat_billing_export_ratio",
    "_chat_billing_export_metrics",
    "_chat_billing_export_detail_values",
    "_chat_billing_sheet_title",
    "_chat_billing_hour_label",
    "_chat_billing_grouping_workbook",
    "_chat_billing_export_workbook",
)
CONST_NAMES = (
    "_CHAT_BILLING_EXPORT_PCT_FMT",
    "_CHAT_BILLING_EXPORT_RATING_FMT",
    "_CHAT_BILLING_EXPORT_SUMMARY_COLUMNS",
    "_CHAT_BILLING_EXPORT_DETAIL_COLUMNS",
    "_CHAT_BILLING_SHEET_FORBIDDEN",
)


def _namespace():
    module = source_cache.parse(BOT_PATH.read_text(encoding="utf-8"))
    selected = [node for node in module.body
                if isinstance(node, ast.FunctionDef) and node.name in FUNCTION_NAMES]
    missing = set(FUNCTION_NAMES) - {node.name for node in selected}
    if missing:
        raise AssertionError("Не найдены функции в bot_schedule2.py: %s" % sorted(missing))
    consts = [node for node in module.body
              if isinstance(node, ast.Assign)
              and any(getattr(t, "id", "") in CONST_NAMES for t in node.targets)]
    ns = {
        "math": math, "re": re, "datetime": datetime, "Workbook": Workbook,
        "Alignment": Alignment, "Border": Border, "Font": Font, "PatternFill": PatternFill,
        "Side": Side, "get_column_letter": get_column_letter,
        "CHAT_BILLING_DETAIL_EXPORT_LIMIT": CHAT_BILLING_DETAIL_EXPORT_LIMIT,
    }
    exec(compile(ast.Module(body=consts + selected, type_ignores=[]), str(BOT_PATH), "exec"), ns)
    return ns


def _aggregates(chats, answered, answered_sl, reply_sum, inner_sum, inner_replied,
                rating_sum, rated):
    return (chats, answered, answered_sl, reply_sum, inner_sum, inner_replied, rating_sum, rated)


class _RowsCursor:
    """Отдаёт заданные строки и помнит запросы — чтобы проверить и SQL, и разбор."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(str(sql).split()), list(params or [])))

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return (len(self.rows), 99)


class _RowsDb:
    def __init__(self, rows):
        self.cursor = _RowsCursor(rows)

    @contextmanager
    def _get_cursor(self):
        yield self.cursor


GROUPING_ROWS = [
    (date(2026, 9, 16), 9, "Jana Taxi", *_aggregates(10, 9, 7, 540.0, 1200.0, 8, 13.0, 3)),
    (date(2026, 9, 16), 9, "Ноль такси", *_aggregates(4, 4, 4, 100.0, 300.0, 4, 10.0, 2)),
    (date(2026, 9, 16), 23, "Jana Taxi", *_aggregates(2, 1, 0, 200.0, 0.0, 0, 0.0, 0)),
    (date(2026, 9, 17), 0, "Jana Taxi", *_aggregates(1, 1, 1, 30.0, 60.0, 1, 5.0, 1)),
]


class ChatBillingMetricsTests(unittest.TestCase):
    def test_every_cut_parses_the_same_eight_aggregates(self):
        db = _RowsDb([(date(2026, 9, 16), "Jana Taxi", "",
                       *_aggregates(10, 9, 7, 540.4, 1200.6, 8, 13.0, 3))])
        report = get_chat_billing_report(db, date(2026, 9, 16), date(2026, 9, 16), sl_seconds=45)
        self.assertEqual(report["totals"], {
            "chats": 10, "answered": 9, "no_reply": 1, "answered_sl": 7,
            "first_reply_seconds": 540, "inner_reply_seconds": 1201, "inner_replied": 8,
            "rating_sum": 13.0, "rated": 3,
        })
        sql, params = db.cursor.calls[0]
        # Порог SL — первый параметр: он стоит в SELECT раньше условий WHERE.
        self.assertEqual(params[0], 45)
        for fragment in ("SUM(r.average_replies_time)", "COUNT(r.average_replies_time)",
                         "SUM(r.rating_score)", "COUNT(r.rating_score)"):
            self.assertIn(fragment, sql)

        operators = get_chat_billing_operators(_RowsDb([
            (date(2026, 9, 16), "Асель", *_aggregates(5, 5, 4, 100.0, 250.0, 5, 24.0, 5), 30, 20),
        ]), date(2026, 9, 16), date(2026, 9, 16))
        self.assertEqual(operators["totals"]["rated"], 5)
        self.assertEqual(operators["totals"]["inner_reply_seconds"], 250)
        self.assertEqual(operators["totals"]["incoming_messages"], 30)
        self.assertEqual(operators["totals"]["outgoing_messages"], 20)

    def test_detail_row_carries_number_reply_time_and_rating(self):
        started = datetime(2026, 9, 16, 23, 59, 21)
        db = _RowsDb([(1, started, "Salam taxi", "whatsapp", "Бауржан", "77076825368",
                       "Ерланов Темирлан", 24, 37.5, 5.0, 6, 4)])
        row = get_chat_billing_details(db, date(2026, 9, 16), date(2026, 9, 16))["rows"][0]
        self.assertEqual(row["client"], "Бауржан")
        self.assertEqual(row["client_number"], "77076825368")
        self.assertEqual(row["inner_reply_seconds"], 37.5)
        self.assertEqual(row["rating"], 5.0)

    def test_metric_keys_are_the_ones_the_screen_reads(self):
        source = VIEW_PATH.read_text(encoding="utf-8") + METRICS_JS_PATH.read_text(encoding="utf-8")
        for key in ("inner_reply_seconds", "inner_replied", "rating_sum", "rated"):
            self.assertIn(key, CHAT_BILLING_METRICS)
            self.assertIn(key, source, f"витрина не читает {key}")


class ChatBillingGroupingTests(unittest.TestCase):
    def test_hours_cover_the_window_and_totals_match(self):
        report = build_chat_billing_grouping(GROUPING_ROWS)
        self.assertEqual([day["date"] for day in report["days"]], ["2026-09-16", "2026-09-17"])
        self.assertEqual([len(day["hours"]) for day in report["days"]], [24, 24])
        nine = report["days"][0]["hours"][9]
        self.assertEqual((nine["hour"], nine["chats"], nine["rated"]), (9, 14, 5))
        # Пустой час — нули, а не пропуск строки.
        self.assertEqual(report["days"][0]["hours"][10]["chats"], 0)
        day_sum = sum(day["totals"]["chats"] for day in report["days"])
        self.assertEqual(day_sum, report["totals"]["chats"])
        self.assertEqual(report["totals"]["chats"], 17)

    def test_park_filter_keeps_the_full_park_list(self):
        report = build_chat_billing_grouping(GROUPING_ROWS, park="Ноль такси")
        self.assertEqual(report["park"], "Ноль такси")
        self.assertEqual([item["park"] for item in report["parks"]], ["Jana Taxi", "Ноль такси"])
        self.assertEqual(report["totals"]["chats"], 4)
        self.assertEqual([day["date"] for day in report["days"]], ["2026-09-16"])

    def test_time_window_trims_hours(self):
        report = build_chat_billing_grouping(GROUPING_ROWS, minute_from=8 * 60, minute_to=22 * 60 + 59)
        self.assertEqual([item["hour"] for item in report["days"][0]["hours"]], list(range(8, 23)))
        self.assertEqual(report["totals"]["chats"], 14)

    def test_one_query_serves_every_park_sheet(self):
        db = _RowsDb(GROUPING_ROWS)
        reports = get_chat_billing_grouping_by_park(db, date(2026, 9, 16), date(2026, 9, 17))
        self.assertEqual([park for park, _ in reports], [None, "Jana Taxi", "Ноль такси"])
        self.assertEqual(len(db.cursor.calls), 1)
        self.assertEqual(sum(report["totals"]["chats"] for park, report in reports[1:]),
                         reports[0][1]["totals"]["chats"])
        single = get_chat_billing_grouping_by_park(_RowsDb(GROUPING_ROWS), date(2026, 9, 16),
                                                   date(2026, 9, 17), park="Jana Taxi")
        self.assertEqual([park for park, _ in single], ["Jana Taxi"])
        self.assertIn("EXTRACT(HOUR FROM r.request_start)",
                      _grouping_sql(date(2026, 9, 16)))


def _grouping_sql(day):
    db = _RowsDb([])
    get_chat_billing_grouping(db, day, day)
    return db.cursor.calls[0][0]


class ChatBillingExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = _namespace()
        cls.params = {
            "start_day": date(2026, 9, 16), "end_day": date(2026, 9, 17),
            "minute_from": 0, "minute_to": 1439, "sl_seconds": 60,
        }

    def test_minutes_round_half_up_like_the_screen(self):
        # Те же значения проверяет tests/chat_billing_metrics.test.mjs.
        minutes = self.ns["_chat_billing_minutes"]
        cases = {135: 2.3, 150: 2.5, 420: 7.0, 720: 12.0, 3: 0.1, 2: 0.0, 1047: 17.5}
        for seconds, expected in cases.items():
            self.assertEqual(minutes(seconds), expected, seconds)
        self.assertIsNone(minutes(None))
        self.assertEqual(self.ns["_chat_billing_round_half_up"](4.555, 2), 4.56)

    def test_row_values_follow_the_columns(self):
        titles = [title for _, title, _, _ in self.ns["_CHAT_BILLING_EXPORT_SUMMARY_COLUMNS"]]
        self.assertEqual(titles, ["Поступило", "Обслужено", "Без ответа", "Ср. первая реакция, мин",
                                  "SL", "Ср. время ответа, мин", "Ср. оценка"])
        values = self.ns["_chat_billing_export_metrics"]({
            "chats": 10, "answered": 8, "no_reply": 2, "answered_sl": 5,
            "first_reply_seconds": 1200, "inner_reply_seconds": 900, "inner_replied": 6,
            "rating_sum": 13.0, "rated": 3,
        })
        self.assertEqual(values, [10, 8, 2, 2.5, 0.5, 2.5, 4.33])
        empty = self.ns["_chat_billing_export_metrics"]({"chats": 1, "answered": 0})
        self.assertEqual(empty[3:], [None, 0.0, None, None])

    def test_screen_and_file_use_the_same_column_labels(self):
        source = VIEW_PATH.read_text(encoding="utf-8")
        block = source[source.index("const CHAT_BILLING_COLUMNS = ["):]
        block = block[:block.index("];")]
        screen = re.findall(r"label: '([^']+)'", block)
        file_titles = [title for _, title, _, _ in self.ns["_CHAT_BILLING_EXPORT_SUMMARY_COLUMNS"]]
        self.assertEqual(screen, file_titles)

    def test_detail_values_are_minutes_and_keep_the_number(self):
        titles = [title for _, title, _ in self.ns["_CHAT_BILLING_EXPORT_DETAIL_COLUMNS"]]
        row = {"started_at": "2026-09-16 10:00:00", "park": "Jana Taxi", "client_number": "77000000108",
               "client": "Dimash", "operator": "Асель", "first_reply_seconds": 150,
               "answered_sl": 0, "inner_reply_seconds": 45, "rating": 5.0,
               "incoming_messages": 3, "outgoing_messages": 2}
        values = dict(zip(titles, self.ns["_chat_billing_export_detail_values"](row)))
        self.assertEqual(values["Номер клиента"], "77000000108")
        self.assertEqual(values["Первая реакция, мин"], 2.5)
        self.assertEqual(values["Время ответа, мин"], 0.8)
        self.assertEqual(values["Оценка"], 5.0)

    def test_sheet_titles_are_valid_and_unique(self):
        title = self.ns["_chat_billing_sheet_title"]
        used = set()
        self.assertEqual(title("Такси24/Нур: [VIP]*", used), "Такси24 Нур   VIP")
        self.assertEqual(title("A" * 40, used), "A" * 31)
        self.assertEqual(title("a" * 40, used), "a" * 27 + " (2)")
        self.assertEqual(title("", used), "Без парка")

    def _load(self, reports):
        output = BytesIO()
        self.ns["_chat_billing_grouping_workbook"](self.params, reports).save(output)
        output.seek(0)
        return load_workbook(output)

    def test_grouping_workbook_has_a_sheet_per_park(self):
        reports = [(None, build_chat_billing_grouping(GROUPING_ROWS))]
        overall = reports[0][1]
        reports += [(item["park"], build_chat_billing_grouping(GROUPING_ROWS, park=item["park"]))
                    for item in overall["parks"]]
        workbook = self._load(reports)
        self.assertEqual(workbook.sheetnames, ["Все таксопарки", "Jana Taxi", "Ноль такси"])

        rows = [[cell.value for cell in row] for row in workbook["Jana Taxi"].iter_rows()]
        self.assertEqual(rows[0][0], "Jana Taxi")
        header = ["Час", "Поступило", "Обслужено", "Без ответа", "Ср. первая реакция, мин",
                  "SL", "Ср. время ответа, мин", "Ср. оценка"]
        self.assertIn(header, rows)
        nine = next(row for row in rows if row[0] == "09:00–10:00")
        # 540 сек на 9 отвеченных = 1 мин; 1200 сек на 8 = 2,5 мин; 13 на 3 = 4,33.
        self.assertEqual(nine, [nine[0], 10, 9, 1, 1.0, 0.7, 2.5, 4.33])
        empty_hour = next(row for row in rows if row[0] == "10:00–11:00")
        self.assertEqual(empty_hour[1:], [0, 0, 0, "—", "—", "—", "—"])
        labels = [row[0] for row in rows]
        self.assertEqual(labels.count("Итого за день"), 2)
        self.assertEqual(labels.count("Итого за период"), 1)
        self.assertIn("16.09.2026, среда", labels)

    def test_grouping_workbook_for_one_park_has_only_it(self):
        workbook = self._load([("Ноль такси", build_chat_billing_grouping(GROUPING_ROWS, park="Ноль такси"))])
        self.assertEqual(workbook.sheetnames, ["Ноль такси"])
        labels = [row[0] for row in workbook["Ноль такси"].iter_rows(values_only=True)]
        # Один день — итога за период нет: он повторил бы итог дня.
        self.assertNotIn("Итого за период", labels)

    def test_export_route_accepts_grouping_and_park(self):
        backend = BOT_PATH.read_text(encoding="utf-8")
        block = backend[backend.index("def api_resource_fte_chat_billing_export"):]
        block = block[:block.index("@app.route('/api/resource_fte/chat/day/")]
        self.assertIn("mode not in ('park', 'operator', 'detail', 'grouping')", block)
        self.assertIn("get_chat_billing_grouping_by_park(", block)
        self.assertIn("park=_chat_billing_park_arg()", block)
        self.assertIn("@app.route('/api/resource_fte/chat/billing_grouping', methods=['GET', 'OPTIONS'])",
                      backend)


if __name__ == "__main__":
    unittest.main()
