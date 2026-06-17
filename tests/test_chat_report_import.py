import ast
import csv
import json
import os
import re
import unittest
import uuid
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"


def _chat_report_namespace():
    wanted_assignments = {
        "CHAT_REPORT_TYPE_SCORE",
        "CHAT_REPORT_TYPE_RESPONSE",
        "CHAT_REPORT_TYPE_WHATSAPP",
        "CHAT_REPORT_TYPE_NAME_REQUESTS",
        "CHAT_REPORT_TYPE_COMBINED",
        "CHAT_REPORT_TYPE_LABELS",
        "CHAT_REPORT_TYPE_FIELDS",
        "CHAT2DESK_API_TOKEN",
        "CHAT2DESK_AUTH_SCHEME",
        "CHAT2DESK_SYNC_TIMEZONE",
        "CHAT2DESK_STATISTICS_REPORT_REPLIES",
        "CHAT2DESK_STATISTICS_REPORT_RATING",
        "CHAT2DESK_STATISTICS_REPORT_OPERATOR_STATS",
    }
    wanted_functions = {
        "_env_bool",
        "_env_int",
        "_status_import_normalize_header",
        "_status_import_normalize_operator_name",
        "_status_import_operator_name_variants",
        "_status_import_parse_datetime",
        "_chat_metrics_parse_date",
        "_chat_metrics_parse_number",
        "_chat_metrics_parse_duration_seconds",
        "_chat_report_name_tokens",
        "_chat_report_tokens_match",
        "_chat_report_resolve_operator",
        "_chat_report_detect_types",
        "_chat_report_detect_type",
        "_chat_report_parse_dt",
        "_status_import_parse_datetime",
        "_chat_metrics_parse_date",
        "_chat_report_parse_surge_windows",
        "_chat_report_in_surge",
        "_chat_report_parse",
        "_chat2desk_api_token",
        "_chat2desk_authorization_header",
        "_chat2desk_sync_timezone",
        "_chat2desk_parse_datetime",
        "_chat2desk_metric_day",
        "_chat2desk_row_first",
        "_chat2desk_row_is_nonempty",
        "_chat2desk_operator_display_name",
        "_chat2desk_operator_by_id",
        "_chat2desk_rating_operator_name",
        "_chat2desk_rating_source_key",
        "_chat2desk_low_rating_payload",
        "_chat2desk_build_metrics_from_statistics_rows",
        "_chat2desk_sync_target_days",
    }

    module = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    selected = []
    for node in module.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & wanted_assignments:
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            selected.append(node)

    namespace = {
        "csv": csv,
        "json": json,
        "os": os,
        "re": re,
        "datetime": datetime,
        "timedelta": timedelta,
        "StringIO": StringIO,
        "ZoneInfo": ZoneInfo,
        "uuid": uuid,
        "STATUS_IMPORT_INVALID_ROWS_PREVIEW_LIMIT": 30,
        "CHAT2DESK_OPERATOR_LOOKUP_CACHE": {},
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace


OPERATORS = [
    (1, "Рахимжанов Бехруз Дилмуродулы"),
    (2, "Ерланов Темирлан Ильясович"),
    (3, "Идрисов Омар Канатович"),
    (4, "Нурланова Жанеля Болатовна"),
]


class ChatReportImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = _chat_report_namespace()
        cls.lookup = {}
        for oid, name in OPERATORS:
            for key in cls.ns["_status_import_operator_name_variants"](name):
                cls.lookup.setdefault(key, [])
                if not any(it["id"] == oid for it in cls.lookup[key]):
                    cls.lookup[key].append({"id": oid, "name": name})
        cls.index = []
        for oid, name in OPERATORS:
            toks = cls.ns["_chat_report_name_tokens"](name)
            if toks:
                cls.index.append({"id": oid, "name": name, "tokens": toks})

    def _norm_headers(self, headers):
        return [self.ns["_status_import_normalize_header"](h) for h in headers]

    def test_detect_type_by_columns(self):
        detect_types = self.ns["_chat_report_detect_types"]
        detect = self.ns["_chat_report_detect_type"]
        self.assertEqual(detect(self._norm_headers(["operator_name", "created_at", "rating_scale_score"])), "score")
        self.assertEqual(detect(self._norm_headers(["operator_name", "request_start", "request_end", "reaction_time"])), "response_time")
        self.assertEqual(detect(self._norm_headers(["Дата и время создания", "Дата и время обращения", "ФИО создателя", "Звонок или Чат"])), "whatsapp_chats")
        self.assertEqual(detect(self._norm_headers(["Name", "Requests"])), "name_requests")
        self.assertIsNone(detect(self._norm_headers(["foo", "bar"])))
        combined = self._norm_headers([
            "operator_name", "created_at", "rating_scale_score",
            "request_start", "request_end", "reaction_time",
        ])
        self.assertEqual(detect(combined), "combined")
        self.assertEqual(detect_types(combined), ["score", "response_time"])

    def test_resolve_operator_reordered_and_abbreviated(self):
        resolve = self.ns["_chat_report_resolve_operator"]
        # перестановка ФИО (Имя Фамилия)
        self.assertEqual(resolve("Бехруз Рахимжанов", self.lookup, self.index)[0], 1)
        self.assertEqual(resolve("Темирлан Ерланов", self.lookup, self.index)[0], 2)
        # полный ФИО Фамилия Имя Отчество
        self.assertEqual(resolve("Ерланов Темирлан Ильясович", self.lookup, self.index)[0], 2)
        # сокращённые имена (Имя + префикс фамилии)
        self.assertEqual(resolve("Омар Идр", self.lookup, self.index)[0], 3)
        self.assertEqual(resolve("Жанеля Н", self.lookup, self.index)[0], 4)
        # неизвестный — None
        self.assertEqual(resolve("Иван Иванов", self.lookup, self.index)[0], None)

    def test_tokens_match_prefix(self):
        match = self.ns["_chat_report_tokens_match"]
        self.assertTrue(match(["омар", "идр"], ["идрисов", "омар", "канатович"]))
        self.assertTrue(match(["жанеля", "н"], ["нурланова", "жанеля", "болатовна"]))
        self.assertFalse(match(["иван", "петр"], ["идрисов", "омар", "канатович"]))

    def test_score_report_sum_and_count(self):
        parse = self.ns["_chat_report_parse"]
        header = ["operator_name", "created_at", "rating_scale_score"]
        rows = [
            ["Бехруз Рахимжанов", "2026-06-10 23:40:45", "5"],
            ["Бехруз Рахимжанов", "2026-06-10 21:00:00", "4"],
            ["Бехруз Рахимжанов", "2026-06-11 09:00:00", "3"],
            ["Неизвестный Человек", "2026-06-10 10:00:00", "5"],
        ]
        res = parse(header, rows, self.lookup, self.index)
        self.assertEqual(res["detected_type"], "score")
        self.assertEqual(set(res["update_fields"]), {"score_sum", "score_count", "avg_score"})
        by_key = {(m["operator_id"], m["day"]): m for m in res["metrics"]}
        d10 = by_key[(1, "2026-06-10")]
        self.assertEqual(d10["score_count"], 2)
        self.assertEqual(d10["score_sum"], 9.0)
        self.assertEqual(d10["avg_score"], 4.5)
        self.assertEqual(by_key[(1, "2026-06-11")]["avg_score"], 3.0)
        self.assertEqual(res["unmatched_count"], 1)

    def test_whatsapp_report_counts_only_whatsapp_by_request_date(self):
        parse = self.ns["_chat_report_parse"]
        header = ["Дата и время создания", "Дата и время обращения", "ФИО создателя", "Звонок или Чат"]
        rows = [
            ["2026-06-01 00:02:16", "2026-05-31 23:59:12", "Ерланов Темирлан Ильясович", "Whatsapp"],
            ["2026-06-01 00:00:17", "2026-05-31 18:26:23", "Ерланов Темирлан Ильясович", "Звонок"],
            ["2026-06-01 10:00:00", "2026-05-31 20:00:00", "Ерланов Темирлан Ильясович", "Whatsapp"],
            ["2026-06-01 00:03:09", "2026-05-31 19:01:49", "Не указан", "Звонок"],
        ]
        res = parse(header, rows, self.lookup, self.index)
        self.assertEqual(res["detected_type"], "whatsapp_chats")
        self.assertEqual(res["update_fields"], ["whatsapp_chats_count"])
        by_key = {(m["operator_id"], m["day"]): m for m in res["metrics"]}
        # 2 строки Whatsapp по дате обращения 2026-05-31, звонок не считается
        self.assertEqual(by_key[(2, "2026-05-31")]["chats_count"], 2)
        self.assertEqual(by_key[(2, "2026-05-31")]["whatsapp_chats_count"], 2)

    def test_name_requests_uses_upload_date_and_abbrev_names(self):
        parse = self.ns["_chat_report_parse"]
        header = ["Name", "Requests"]
        rows = [["Омар Идр", "950"], ["Жанеля Н", "925"]]
        res = parse(header, rows, self.lookup, self.index, default_date="2026-06-01")
        self.assertEqual(res["detected_type"], "name_requests")
        self.assertEqual(res["update_fields"], ["name_requests_chats_count"])
        by_key = {(m["operator_id"], m["day"]): m for m in res["metrics"]}
        self.assertEqual(by_key[(3, "2026-06-01")]["chats_count"], 950)
        self.assertEqual(by_key[(3, "2026-06-01")]["name_requests_chats_count"], 950)
        self.assertEqual(by_key[(4, "2026-06-01")]["chats_count"], 925)
        self.assertEqual(by_key[(4, "2026-06-01")]["name_requests_chats_count"], 925)

    def test_name_requests_requires_upload_date(self):
        parse = self.ns["_chat_report_parse"]
        with self.assertRaises(ValueError):
            parse(["Name", "Requests"], [["Омар Идр", "950"]], self.lookup, self.index)

    def test_combined_score_and_response_report_imports_both_metrics(self):
        parse = self.ns["_chat_report_parse"]
        header = [
            "operator_name", "created_at", "rating_scale_score",
            "request_start", "request_end", "reaction_time",
        ]
        rows = [
            ["Темирлан Ерланов", "2026-06-10 09:00:00", "5", "2026-06-10 10:00:00", "2026-06-10 10:00:10", "10"],
            ["Темирлан Ерланов", "2026-06-10 09:05:00", "3", "2026-06-10 10:05:00", "2026-06-10 10:05:20", "20"],
            ["Темирлан Ерланов", "2026-06-11 09:00:00", "4", "2026-06-11 10:00:00", "2026-06-11 10:00:30", "30"],
        ]
        res = parse(header, rows, self.lookup, self.index)
        self.assertEqual(res["detected_type"], "combined")
        self.assertEqual(res["detected_types"], ["score", "response_time"])
        self.assertEqual(set(res["update_fields"]), {
            "score_sum", "score_count", "avg_score", "avg_response_time_seconds"
        })
        by_key = {(m["operator_id"], m["day"]): m for m in res["metrics"]}
        d10 = by_key[(2, "2026-06-10")]
        self.assertEqual(d10["score_count"], 2)
        self.assertEqual(d10["score_sum"], 8.0)
        self.assertEqual(d10["avg_score"], 4.0)
        self.assertEqual(d10["avg_response_time_seconds"], 15.0)
        self.assertEqual(by_key[(2, "2026-06-11")]["avg_score"], 4.0)
        self.assertEqual(by_key[(2, "2026-06-11")]["avg_response_time_seconds"], 30.0)

        surge = json.dumps([{"start": "2026-06-10T10:01", "end": "2026-06-10T10:10"}])
        res2 = parse(header, rows, self.lookup, self.index, surge_windows=surge)
        by_key2 = {(m["operator_id"], m["day"]): m for m in res2["metrics"]}
        d10_after_surge = by_key2[(2, "2026-06-10")]
        self.assertEqual(d10_after_surge["score_count"], 2)
        self.assertEqual(d10_after_surge["avg_score"], 4.0)
        self.assertEqual(d10_after_surge["avg_response_time_seconds"], 10.0)
        self.assertEqual(res2["excluded_surge_rows"], 1)

    def test_chat2desk_statistics_rows_import_score_and_response(self):
        build = self.ns["_chat2desk_build_metrics_from_statistics_rows"]
        operator_name = OPERATORS[1][1]
        replies = [
            {
                "operator_name": operator_name,
                "request_start": "2026-06-10 10:00:00",
                "reaction_time": "10",
            },
            {
                "operator_name": operator_name,
                "request_start": "2026-06-10 10:05:00",
                "reaction_time": "20",
            },
        ]
        ratings = [
            {
                "operator_name": operator_name,
                "created_at": "2026-06-10 12:00:00",
                "rating_scale_score": "5",
                "phone": "77010000001",
                "channel_name": "Jana Taxi",
                "rating_id": 1042,
                "request_id": 74120001,
                "valuation_request_id": 74110001,
            },
            {
                "operator_name": operator_name,
                "created_at": "2026-06-10 13:00:00",
                "rating_scale_score": "3",
                "phone": "77010000002",
                "channel_name": "Техподдержка iTaxi",
                "rating_id": 1042,
                "request_id": 74120002,
                "valuation_request_id": 74110002,
            },
        ]

        res = build("2026-06-10", replies, ratings, self.lookup, self.index)
        self.assertEqual(res["detected_type"], "combined")
        self.assertEqual(set(res["update_fields"]), {
            "score_sum", "score_count", "avg_score", "avg_response_time_seconds"
        })
        self.assertEqual(res["source_rows"], 4)
        self.assertEqual(res["api_rows"]["operator_replies"], 2)
        self.assertEqual(res["api_rows"]["rating"], 2)

        by_key = {(m["operator_id"], m["day"]): m for m in res["metrics"]}
        d10 = by_key[(2, "2026-06-10")]
        self.assertEqual(d10["avg_response_time_seconds"], 15.0)
        self.assertEqual(d10["score_sum"], 8.0)
        self.assertEqual(d10["score_count"], 2)
        self.assertEqual(d10["avg_score"], 4.0)
        self.assertEqual(res["low_rating_count"], 1)
        self.assertEqual(len(res["low_ratings"]), 1)
        low = res["low_ratings"][0]
        self.assertEqual(low["operator_id"], 2)
        self.assertEqual(low["operator_name"], operator_name)
        self.assertEqual(low["phone_number"], "77010000002")
        self.assertEqual(low["taxi_park"], "Техподдержка iTaxi")
        self.assertEqual(low["rated_at"], "2026-06-10 13:00:00")
        self.assertEqual(low["score"], 3.0)
        self.assertEqual(low["raw_payload"]["valuation_request_id"], 74110002)

        surge = json.dumps([{"start": "2026-06-10T10:01", "end": "2026-06-10T10:10"}])
        res2 = build("2026-06-10", replies, ratings, self.lookup, self.index, surge_windows=surge)
        d10_after_surge = {
            (m["operator_id"], m["day"]): m for m in res2["metrics"]
        }[(2, "2026-06-10")]
        self.assertEqual(d10_after_surge["avg_response_time_seconds"], 10.0)
        self.assertEqual(d10_after_surge["score_count"], 2)
        self.assertEqual(d10_after_surge["avg_score"], 4.0)
        self.assertEqual(res2["excluded_surge_rows"], 1)

    def test_chat2desk_operator_stats_imports_chat_count(self):
        build = self.ns["_chat2desk_build_metrics_from_statistics_rows"]
        operator_name = OPERATORS[1][1]
        operator_stats = [
            {
                "operator_name": operator_name,
                "date": "2026-06-10",
                "channel_name": "Support",
                "transport": "whatsapp",
                "requests_took_part": 3,
            },
            {
                "operator_name": operator_name,
                "date": "2026-06-10",
                "channel_name": "Support",
                "transport": "wa_dialog",
                "requests_took_part": "4",
            },
        ]

        res = build(
            "2026-06-10",
            [],
            [],
            self.lookup,
            self.index,
            operator_stats_rows=operator_stats,
        )

        self.assertEqual(res["detected_type"], "chats_count")
        self.assertEqual(res["update_fields"], ["chats_count"])
        self.assertEqual(res["source_rows"], 2)
        self.assertEqual(res["api_rows"]["operator_stats"], 2)
        by_key = {(m["operator_id"], m["day"]): m for m in res["metrics"]}
        self.assertEqual(by_key[(2, "2026-06-10")]["chats_count"], 7)

    def test_chat2desk_api_token_normalizes_common_env_copies(self):
        parse_token = self.ns["_chat2desk_api_token"]
        auth_header = self.ns["_chat2desk_authorization_header"]
        old_value = os.environ.get("CHAT2DESK_API_TOKEN")
        old_scheme = os.environ.get("CHAT2DESK_AUTH_SCHEME")
        try:
            os.environ["CHAT2DESK_API_TOKEN"] = 'Authorization: Bearer "abc123"'
            self.assertEqual(parse_token(), "abc123")
            os.environ.pop("CHAT2DESK_AUTH_SCHEME", None)
            self.assertEqual(auth_header(), "abc123")
            os.environ["CHAT2DESK_AUTH_SCHEME"] = "Bearer"
            self.assertEqual(auth_header(), "Bearer abc123")
            os.environ["CHAT2DESK_API_TOKEN"] = "'xyz789'"
            self.assertEqual(parse_token(), "xyz789")
        finally:
            if old_value is None:
                os.environ.pop("CHAT2DESK_API_TOKEN", None)
            else:
                os.environ["CHAT2DESK_API_TOKEN"] = old_value
            if old_scheme is None:
                os.environ.pop("CHAT2DESK_AUTH_SCHEME", None)
            else:
                os.environ["CHAT2DESK_AUTH_SCHEME"] = old_scheme

    def test_chat2desk_sync_target_days_accepts_period(self):
        target_days = self.ns["_chat2desk_sync_target_days"]
        self.assertEqual(
            [d.strftime("%Y-%m-%d") for d in target_days(date_from="2026-06-01", date_to="2026-06-03")],
            ["2026-06-01", "2026-06-02", "2026-06-03"],
        )
        self.assertEqual(
            [d.strftime("%Y-%m-%d") for d in target_days(day="2026-06-10")],
            ["2026-06-10"],
        )
        with self.assertRaises(ValueError):
            target_days(date_from="2026-06-10", date_to="2026-06-09")
        with self.assertRaises(ValueError):
            target_days(date_from="2026-06-01", date_to="2026-07-02")

    def test_response_time_average_and_surge_filter(self):
        parse = self.ns["_chat_report_parse"]
        header = ["operator_name", "request_start", "request_end", "reaction_time"]
        rows = [
            ["Темирлан Ерланов", "2026-06-10 10:00:00", "2026-06-10 10:00:10", "10"],
            ["Темирлан Ерланов", "2026-06-10 23:58:55", "2026-06-11 00:42:08", "6"],
        ]
        # без наплыва — среднее (10+6)/2 = 8
        res = parse(header, rows, self.lookup, self.index)
        by_key = {(m["operator_id"], m["day"]): m for m in res["metrics"]}
        self.assertEqual(by_key[(2, "2026-06-10")]["avg_response_time_seconds"], 8.0)
        self.assertEqual(res["update_fields"], ["avg_response_time_seconds"])
        # с окном наплыва 23:00–23:59 строка 23:58 исключается → среднее = 10
        surge = json.dumps([{"start": "2026-06-10T23:00", "end": "2026-06-10T23:59"}])
        res2 = parse(header, rows, self.lookup, self.index, surge_windows=surge)
        by_key2 = {(m["operator_id"], m["day"]): m for m in res2["metrics"]}
        self.assertEqual(by_key2[(2, "2026-06-10")]["avg_response_time_seconds"], 10.0)
        self.assertEqual(res2["excluded_surge_rows"], 1)


if __name__ == "__main__":
    unittest.main()
