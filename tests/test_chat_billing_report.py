# -*- coding: utf-8 -*-
"""«Биллинг чатов» (задача #343): оценка, время ответа и первая реакция в минутах,
план чатов и сотрудники, выгрузки в формате отчётов СЗоВ — «Ежедневный отчёт по чатам»
и «Отчёт с группировкой по часам»."""
import ast
import copy
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
    _chat_billing_planned_staff,
    attach_chat_billing_grouping_staff,
    attach_chat_billing_plan,
    build_chat_billing_grouping,
    build_chat_billing_staff,
    get_chat_billing_details,
    get_chat_billing_grouping,
    get_chat_billing_grouping_by_park,
    get_chat_billing_operators,
    get_chat_billing_report,
)
from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DATABASE_PATH = ROOT / "database.py"
VIEW_PATH = ROOT / "src" / "components" / "resources" / "ResourceFteView.jsx"
METRICS_JS_PATH = ROOT / "src" / "components" / "resources" / "chatBillingMetrics.js"

FUNCTION_NAMES = (
    "_chat_billing_round_half_up",
    "_chat_billing_minutes",
    "_chat_billing_minutes_label",
    "_chat_billing_export_ratio",
    "_chat_billing_export_metrics",
    "_chat_billing_export_detail_values",
    "_chat_billing_sheet_title",
    "_chat_billing_day_comment",
    "_chat_billing_daily_values",
    "_chat_billing_daily_workbook",
    "_chat_billing_hourly_workbook",
    "_chat_billing_export_workbook",
)
CONST_NAMES = (
    "_CHAT_BILLING_EXPORT_PCT_FMT",
    "_CHAT_BILLING_EXPORT_RATING_FMT",
    "_CHAT_BILLING_EXPORT_SUMMARY_COLUMNS",
    "_CHAT_BILLING_EXPORT_DETAIL_COLUMNS",
    "_CHAT_BILLING_SHEET_FORBIDDEN",
    "_CHAT_BILLING_MONTHS_RU",
    "_CHAT_BILLING_DAILY_COLUMNS",
    "_CHAT_BILLING_DAILY_DAY_KEYS",
    "CHAT_BILLING_LOW_RATING",
    "_CHAT_BILLING_HOURLY_COLUMNS",
    "_CHAT_BILLING_HOURLY_STAFF_KEYS",
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


def _lot_parts_method():
    """Настоящий Database._hourly_lot_parts_for_date: план сотрудников режет смены им."""
    module = source_cache.parse(DATABASE_PATH.read_text(encoding="utf-8"))
    database = next(node for node in module.body
                    if isinstance(node, ast.ClassDef) and node.name == "Database")
    method = next(node for node in database.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_hourly_lot_parts_for_date")
    # Копия: source_cache отдаёт общий разобранный модуль, и снятый прямо с него
    # @staticmethod ломал соседние тесты, которые исполняют класс Database целиком.
    method = copy.deepcopy(method)
    method.decorator_list = []
    ns = {"date": date}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(DATABASE_PATH), "exec"), ns)
    return ns["_hourly_lot_parts_for_date"]


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


def _park_report():
    db = _RowsDb([
        (date(2026, 9, 16), "Jana Taxi", "", *_aggregates(60, 58, 50, 3480.0, 9000.0, 50, 90.0, 20)),
        (date(2026, 9, 16), "Ноль такси", "", *_aggregates(30, 30, 25, 900.0, 3000.0, 25, 36.0, 9)),
        (date(2026, 9, 17), "Jana Taxi", "", *_aggregates(40, 40, 38, 400.0, 2000.0, 20, 42.0, 10)),
    ])
    return get_chat_billing_report(db, date(2026, 9, 16), date(2026, 9, 17))


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
        # «Факт чатов» — обращения без автоматических опросов после чата: ручной отчёт
        # считал и строки request_type='rating', и завышал объём примерно вдвое.
        self.assertIn("r.request_type = %s", sql)
        self.assertIn("common", params)

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
        for key in ("inner_reply_seconds", "inner_replied", "rating_sum", "rated",
                    "plan_chats", "staff_planned", "staff_fact", "planned_hours", "fact_hours"):
            self.assertIn(key, source, f"витрина не читает {key}")
        for key in ("inner_reply_seconds", "inner_replied", "rating_sum", "rated"):
            self.assertIn(key, CHAT_BILLING_METRICS)


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
        db = _RowsDb([])
        get_chat_billing_grouping(db, date(2026, 9, 16), date(2026, 9, 16))
        self.assertIn("EXTRACT(HOUR FROM r.request_start)", db.cursor.calls[0][0])


class _PlannerDb:
    """План сотрудников: лоты аукциона чата и настоящий разбор смены по часам."""

    def __init__(self, lots):
        self.lots = lots
        self.requested = []
        self._hourly_lot_parts_for_date = _lot_parts_method()

    def get_shift_auction_lots_for_planner_date(self, day, direction_mode="line"):
        self.requested.append((day, direction_mode))
        return {"lots": self.lots}


def _lot(operator_id, shift_date, start, end, status="claimed", kind=""):
    return {"claimed_by": operator_id, "shift_date": shift_date, "start_time": start,
            "end_time": end, "status": status, "shift_kind": kind}


class ChatBillingStaffTests(unittest.TestCase):
    def test_planned_staff_skips_phone_and_unclaimed_and_unions_overlaps(self):
        db = _PlannerDb([
            _lot(1, "2026-09-12", "09:00", "18:00"),
            # Наложенная смена того же человека — часы не задваиваются, голова одна.
            _lot(1, "2026-09-12", "15:00", "21:30"),
            _lot(2, "2026-09-12", "10:00", "11:00", kind="phone"),
            _lot(3, "2026-09-12", "10:00", "11:00", status="available"),
            # Хвост ночной смены прошлых суток.
            _lot(4, "2026-09-11", "20:00", "02:00"),
        ])
        plan = _chat_billing_planned_staff(db, date(2026, 9, 12))
        self.assertEqual(db.requested, [(date(2026, 9, 12), "chat")])
        self.assertEqual(plan["heads"][:3], [1, 1, 0])
        self.assertEqual(plan["heads"][10], 1)
        self.assertEqual(plan["heads"][16], 1)
        self.assertEqual(plan["heads"][21], 1)
        self.assertEqual(sum(plan["hours"]), 2 + 12.5)
        self.assertIsNone(_chat_billing_planned_staff(_PlannerDb([]), date(2026, 9, 12)))

    def test_fact_heads_need_a_minute_online(self):
        rows = [
            (date(2026, 9, 12), 9, 1, 3600.0),
            (date(2026, 9, 12), 9, 2, 59.0),
            (date(2026, 9, 12), 10, 2, 60.0),
            # Строка соседних суток из ночной смены — не наша.
            (date(2026, 9, 11), 23, 1, 3600.0),
        ]
        staff = build_chat_billing_staff(date(2026, 9, 12), date(2026, 9, 13), rows,
                                         {"2026-09-12": {"heads": [2] * 24, "hours": [2.0] * 24}})
        day = staff["2026-09-12"]
        self.assertEqual(day["fact_heads"][9], 1)
        self.assertEqual(day["fact_heads"][10], 1)
        self.assertAlmostEqual(sum(day["fact_hours"]), (3600 + 59 + 60) / 3600, places=3)
        # Нет ни статусов, ни графика — прочерк, а не ноль людей.
        self.assertIsNone(staff["2026-09-13"]["fact_heads"])
        self.assertIsNone(staff["2026-09-13"]["planned_heads"])

    def test_plan_is_split_by_park_shares_and_keeps_park_order(self):
        plan = {"days": {"2026-09-16": [10.0] * 24, "2026-09-17": [5.0] * 24},
                "shares": {"Jana Taxi": 0.5, "Ноль такси": 0.3, "Tenge Taxi": 0.2}}
        staff = {"2026-09-16": {"planned_hours": [2.0] * 24, "fact_hours": [1.5] * 24}}
        report = attach_chat_billing_plan(_park_report(), plan, staff)
        day = report["days"][0]
        self.assertEqual([item["park"] for item in day["parks"]], ["Jana Taxi", "Ноль такси", "Tenge Taxi"])
        self.assertEqual([item["plan_chats"] for item in day["parks"]], [120.0, 72.0, 48.0])
        # Парк из прогноза без обращений остаётся строкой с планом и нулевым фактом.
        self.assertEqual(day["parks"][2]["chats"], 0)
        self.assertEqual(day["totals"]["plan_chats"], 240.0)
        self.assertEqual(day["staff"], {"planned_hours": 48.0, "fact_hours": 36.0})
        self.assertEqual(report["days"][1]["staff"], {"planned_hours": None, "fact_hours": None})
        # Во втором дне Ноль такси не писал, но строка на своём месте.
        self.assertEqual([item["park"] for item in report["days"][1]["parks"]],
                         ["Jana Taxi", "Ноль такси", "Tenge Taxi"])
        self.assertEqual(report["totals"]["plan_chats"], 360.0)
        self.assertEqual(report["parks"][0]["plan_chats"], 180.0)
        self.assertEqual(report["staff"], {"planned_hours": 48.0, "fact_hours": 36.0})

    def test_plan_follows_the_time_window(self):
        plan = {"days": {"2026-09-16": [1.0] * 24, "2026-09-17": [1.0] * 24}, "shares": {"Jana Taxi": 1.0}}
        report = attach_chat_billing_plan(_park_report(), plan, {}, minute_from=8 * 60, minute_to=19 * 60 + 59)
        self.assertEqual(report["days"][0]["totals"]["plan_chats"], 12.0)

    def test_grouping_staff_is_per_hour(self):
        report = build_chat_billing_grouping(GROUPING_ROWS)
        staff = {"2026-09-16": {"planned_heads": list(range(24)), "fact_heads": [3] * 24,
                                "planned_hours": [1.0] * 24, "fact_hours": [0.5] * 24}}
        attach_chat_billing_grouping_staff(report, staff)
        nine = report["days"][0]["hours"][9]
        self.assertEqual((nine["staff_planned"], nine["staff_fact"]), (9, 3))
        self.assertIsNone(report["days"][1]["hours"][0]["staff_planned"])
        self.assertEqual(report["days"][0]["staff"], {"planned_hours": 24.0, "fact_hours": 12.0})


class ChatBillingExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = _namespace()
        cls.params = {
            "start_day": date(2026, 9, 16), "end_day": date(2026, 9, 17),
            "minute_from": 0, "minute_to": 1439, "sl_seconds": 60,
        }

    def _load(self, workbook):
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return load_workbook(output)

    def test_minutes_round_half_up_like_the_screen(self):
        # Те же значения проверяет tests/chat_billing_metrics.test.mjs.
        minutes = self.ns["_chat_billing_minutes"]
        cases = {135: 2.3, 150: 2.5, 420: 7.0, 720: 12.0, 3: 0.1, 2: 0.0, 1047: 17.5}
        for seconds, expected in cases.items():
            self.assertEqual(minutes(seconds), expected, seconds)
        self.assertIsNone(minutes(None))
        self.assertEqual(self.ns["_chat_billing_round_half_up"](4.25, 1), 4.3)
        self.assertEqual(self.ns["_chat_billing_minutes_label"](288), "4,8")
        self.assertEqual(self.ns["_chat_billing_minutes_label"](420), "7")

    def test_operator_export_follows_the_screen_columns(self):
        titles = [title for _, title, _, _ in self.ns["_CHAT_BILLING_EXPORT_SUMMARY_COLUMNS"]]
        source = VIEW_PATH.read_text(encoding="utf-8")
        block = source[source.index("const CHAT_BILLING_COLUMN_SETS = {"):]
        block = block[block.index("  operator: ["):]
        block = block[:block.index("\n  ],")]
        self.assertEqual(re.findall(r"\['[a-z_]+', '([^']+)'\]", block), titles)
        values = self.ns["_chat_billing_export_metrics"]({
            "chats": 10, "answered": 8, "no_reply": 2, "answered_sl": 5,
            "first_reply_seconds": 1200, "inner_reply_seconds": 900, "inner_replied": 6,
            "rating_sum": 13.0, "rated": 3,
        })
        self.assertEqual(values, [10, 8, 2, 2.5, 0.5, 2.5, 4.3])

    def test_detail_values_are_minutes_and_keep_the_number(self):
        titles = [title for _, title, _ in self.ns["_CHAT_BILLING_EXPORT_DETAIL_COLUMNS"]]
        row = {"started_at": "2026-09-16 10:00:00", "park": "Jana Taxi", "client_number": "77000000108",
               "client": "Dimash", "operator": "Асель", "first_reply_seconds": 150,
               "answered_sl": 0, "inner_reply_seconds": 45, "rating": 5.0,
               "incoming_messages": 3, "outgoing_messages": 2}
        values = dict(zip(titles, self.ns["_chat_billing_export_detail_values"](row)))
        self.assertEqual(values["Номер клиента"], "77000000108")
        self.assertEqual(values["Реакция на 1 сообщение, мин"], 2.5)
        self.assertEqual(values["Время ответа внутри чата, мин"], 0.8)
        self.assertEqual(values["Оценка"], 5.0)

    def test_sheet_titles_are_valid_and_unique(self):
        title = self.ns["_chat_billing_sheet_title"]
        used = set()
        self.assertEqual(title("Такси24/Нур: [VIP]*", used), "Такси24 Нур   VIP")
        self.assertEqual(title("A" * 40, used), "A" * 31)
        self.assertEqual(title("a" * 40, used), "a" * 27 + " (2)")
        self.assertEqual(title("", used), "Без парка")

    def _daily_report(self):
        plan = {"days": {"2026-09-16": [10.0] * 24, "2026-09-17": [5.0] * 24},
                "shares": {"Jana Taxi": 0.5, "Ноль такси": 0.3, "Tenge Taxi": 0.2}}
        staff = {"2026-09-16": {"planned_hours": [2.5] * 24, "fact_hours": [2.0] * 24}}
        return attach_chat_billing_plan(_park_report(), plan, staff)

    def test_daily_workbook_follows_the_szov_sample(self):
        workbook = self._load(self.ns["_chat_billing_daily_workbook"](self.params, self._daily_report()))
        self.assertEqual(workbook.sheetnames, ["Сентябрь 2026"])
        ws = workbook.active
        rows = [list(row) for row in ws.iter_rows(values_only=True)]
        self.assertEqual(rows[0], [
            "Дата", "Название очереди", "План (чатов)", "факт (чатов)", "% совпадения прогноза",
            "Среднее время реакции на 1 сообщение оператора (в минутах) в разрезе парков",
            "Среднее время ответа внутри чата (в минутах) в разрезе парков",
            "Сред оценка водителей в разрезе парков", "План по часам", "Факт по часам",
            "% Пере/Недоработки", "Комментарии",
        ])
        self.assertNotIn("Нужно удалить", rows[0])
        jana = rows[1]
        # 3480 сек на 58 отвеченных = 1 мин; 9000 на 50 = 3 мин; 90 на 20 = 4,5.
        self.assertEqual(jana[:8], [datetime(2026, 9, 16), "Jana Taxi", 120.0, 60, 0.5, 1.0, 3.0, 4.5])
        self.assertEqual(jana[8:11], [60.0, 48.0, -0.2])
        self.assertIn("Ежедневный отчет по чатам за 16.09.2026\nПлан чатов: 240\nФакт чатов: 90", jana[11])
        tenge = rows[3]
        self.assertEqual(tenge[1:5], ["Tenge Taxi", 48.0, None, 0.0])
        total = rows[4]
        self.assertEqual(total[:5], [datetime(2026, 9, 16), None, 240.0, 90, 0.375])
        # Итог дня — по обращениям всех парков: (3480+900) / 88 отвеченных.
        self.assertEqual(total[5], round((3480 + 900) / 88 / 60, 1))
        self.assertIn("A2:A4", [str(item) for item in ws.merged_cells.ranges])
        self.assertIn("L2:L4", [str(item) for item in ws.merged_cells.ranges])
        self.assertEqual(ws["A5"].fill.fgColor.rgb[-6:], "9DC3E6")
        # Второй день: плана часов нет — пусто, а не ноль.
        self.assertEqual(rows[5][8:11], [None, None, None])

    def test_daily_workbook_marks_low_rating(self):
        report = self._daily_report()
        report["days"][0]["parks"][1]["rating_sum"] = 26.0  # 26 / 9 = 2,9
        ws = self._load(self.ns["_chat_billing_daily_workbook"](self.params, report)).active
        self.assertEqual(ws["H3"].value, 2.9)
        self.assertEqual(ws["H3"].font.color.rgb[-6:], "9C0006")
        self.assertNotEqual(str(getattr(ws["H2"].font.color, "rgb", ""))[-6:], "9C0006")

    def test_hourly_workbook_follows_the_szov_sample(self):
        overall = build_chat_billing_grouping(GROUPING_ROWS)
        attach_chat_billing_grouping_staff(overall, {
            "2026-09-16": {"planned_heads": [2] * 24, "fact_heads": [1] * 9 + [2] + [1] * 14,
                           "planned_hours": [2.0] * 24, "fact_hours": [1.0] * 24},
        })
        reports = [(None, overall)] + [
            (item["park"], build_chat_billing_grouping(GROUPING_ROWS, park=item["park"]))
            for item in overall["parks"]]
        workbook = self._load(self.ns["_chat_billing_hourly_workbook"](self.params, reports))
        self.assertEqual(workbook.sheetnames, ["Все таксопарки", "Jana Taxi", "Ноль такси"])

        ws = workbook["Все таксопарки"]
        rows = [list(row) for row in ws.iter_rows(values_only=True)]
        self.assertEqual(rows[0][1], datetime(2026, 9, 16))
        self.assertEqual(rows[0][7], datetime(2026, 9, 17))
        self.assertEqual(rows[1][:7], ["Время", "Поступившие чаты", "План(По сотрудникам)",
                                       "Факт(По сотрудникам)", "Разница",
                                       "Ср вр реакции на 1 сообщение", "Ср время ответа внутри чата"])
        self.assertIn("B1:G1", [str(item) for item in ws.merged_cells.ranges])
        nine = rows[2 + 9]
        # 640 сек на 13 отвеченных = 0,8 мин; 1500 сек на 12 ответивших = 2,1 мин.
        self.assertEqual(nine[:7], [9, 14, 2, 2, 0, 0.8, 2.1])
        eight = rows[2 + 8]
        self.assertEqual(eight[:5], [8, 0, 2, 1, -1])
        self.assertEqual(ws["E11"].fill.fgColor.rgb[-6:], "FF0000")
        self.assertIsNone(ws["E12"].fill.fgColor.rgb if ws["E12"].fill.fill_type else None)
        # Второй день без графика и статусов — прочерков нет, ячейки пустые.
        self.assertEqual(rows[2][8:11], [None, None, None])
        total = rows[26]
        self.assertEqual(total[:2], ["Общий итог", 16])

        park_rows = [list(row) for row in workbook["Jana Taxi"].iter_rows(values_only=True)]
        # У парка сотрудников нет — три колонки на день.
        self.assertEqual(park_rows[1][:4], ["Время", "Поступившие чаты", "Ср вр реакции на 1 сообщение",
                                            "Ср время ответа внутри чата"])
        self.assertEqual(park_rows[0][4], datetime(2026, 9, 17))

    def test_export_route_uses_the_szov_formats(self):
        backend = BOT_PATH.read_text(encoding="utf-8")
        block = backend[backend.index("def api_resource_fte_chat_billing_export"):]
        block = block[:block.index("@app.route('/api/resource_fte/chat/day/")]
        self.assertIn("mode not in ('park', 'operator', 'detail', 'grouping')", block)
        self.assertIn("_chat_billing_hourly_workbook(params, reports)", block)
        self.assertIn("_chat_billing_daily_workbook(params, get_chat_billing_daily(", block)
        self.assertIn("attach_chat_billing_grouping_staff(", block)
        self.assertIn("park=_chat_billing_park_arg()", block)
        # В модуле нет `import io` — только `from io import BytesIO`. Выгрузка биллинга
        # чата падала NameError на io.BytesIO() в любом разрезе с самого появления.
        self.assertNotIn("io.BytesIO", block)
        self.assertIn("output = BytesIO()", block)
        self.assertIn("\nfrom io import BytesIO", backend)
        route = backend[backend.index("def api_resource_fte_chat_billing_grouping"):]
        route = route[:route.index("# Выгрузка биллинга чата.")]
        self.assertIn("if not park:", route)
        self.assertIn("get_chat_billing_daily(db,", backend[backend.index("def api_resource_fte_chat_billing():"):])


if __name__ == "__main__":
    unittest.main()
