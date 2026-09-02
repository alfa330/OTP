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
from tests import source_cache

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
        "CHAT2DESK_STATISTICS_REPORT_REQUEST_STATS",
        "CHAT2DESK_STATISTICS_REPORT_RATING",
        "CHAT2DESK_STATISTICS_REPORT_OPERATOR_STATS",
        "CHAT2DESK_RATING_SOURCE_KEY_PREFIX",
        "CHAT2DESK_RATING_SHIFT_TOLERANCE_SECONDS",
        "_KZ_TO_RU_FOLD",
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
        "_chat_metrics_import_parse_csv",
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
        "_chat2desk_request_end_index",
        "_chat2desk_rating_time_shifts",
        "_chat2desk_low_rating_payload",
        "_chat2desk_build_metrics_from_statistics_rows",
        "_chat2desk_sync_target_days",
    }

    module = source_cache.parse(BOT_PATH.read_text(encoding="utf-8"))
    selected = []
    for node in module.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & wanted_assignments:
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            selected.append(node)

    import logging as _logging
    namespace = {
        "csv": csv,
        "json": json,
        "os": os,
        "re": re,
        "logging": _logging,
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
    (1, "Досанбаев Ерсын Тестулы"),
    (2, "Тестбаев Асан Тестович"),
    (3, "Асанов Оралхан Тестович"),
    (4, "Сынакова Жанель Тестовна"),
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

    def test_legacy_csv_rejects_ready_average_score(self):
        parse = self.ns["_chat_metrics_import_parse_csv"]
        csv_text = (
            "operator_name,date,avg_score\n"
            "Досанбаев Ерсын Тестулы,2026-06-10,4.95\n"
        )

        with self.assertRaisesRegex(ValueError, "Импорт готовой средней оценки запрещён"):
            parse(csv_text, self.lookup)

    def test_legacy_csv_ignores_ready_average_when_other_metrics_exist(self):
        parse = self.ns["_chat_metrics_import_parse_csv"]
        csv_text = (
            "operator_name,date,avg_score,avg_response_time_seconds,transfer_chat_count\n"
            "Досанбаев Ерсын Тестулы,2026-06-10,1.00,42,3\n"
        )

        result = parse(csv_text, self.lookup)

        self.assertEqual(
            result["update_fields"],
            ["avg_response_time_seconds", "transfer_chat_count"],
        )
        self.assertEqual(len(result["metrics"]), 1)
        metric = result["metrics"][0]
        self.assertNotIn("avg_score", metric)
        self.assertEqual(metric["avg_response_time_seconds"], 42.0)
        self.assertEqual(metric["transfer_chat_count"], 3)

    def test_resolve_operator_reordered_and_abbreviated(self):
        resolve = self.ns["_chat_report_resolve_operator"]
        # перестановка ФИО (Имя Фамилия)
        self.assertEqual(resolve("Ерсын Досанбаев", self.lookup, self.index)[0], 1)
        self.assertEqual(resolve("Асан Тестбаев", self.lookup, self.index)[0], 2)
        # полный ФИО Фамилия Имя Отчество
        self.assertEqual(resolve("Тестбаев Асан Тестович", self.lookup, self.index)[0], 2)
        # сокращённые имена (Имя + префикс фамилии)
        self.assertEqual(resolve("Оралхан Асан", self.lookup, self.index)[0], 3)
        self.assertEqual(resolve("Жанель С", self.lookup, self.index)[0], 4)
        # неизвестный — None
        self.assertEqual(resolve("Иван Иванов", self.lookup, self.index)[0], None)

    def test_tokens_match_prefix(self):
        match = self.ns["_chat_report_tokens_match"]
        self.assertTrue(match(["оралхан", "асан"], ["асанов", "оралхан", "тестович"]))
        self.assertTrue(match(["жанель", "с"], ["сынакова", "жанель", "тестовна"]))
        self.assertFalse(match(["иван", "петр"], ["асанов", "оралхан", "тестович"]))

    def test_score_report_sum_and_count(self):
        parse = self.ns["_chat_report_parse"]
        header = ["operator_name", "created_at", "rating_scale_score"]
        rows = [
            ["Ерсын Досанбаев", "2026-06-10 23:40:45", "5"],
            ["Ерсын Досанбаев", "2026-06-10 21:00:00", "4"],
            ["Ерсын Досанбаев", "2026-06-11 09:00:00", "3"],
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
            ["2026-06-01 00:02:16", "2026-05-31 23:59:12", "Тестбаев Асан Тестович", "Whatsapp"],
            ["2026-06-01 00:00:17", "2026-05-31 18:26:23", "Тестбаев Асан Тестович", "Звонок"],
            ["2026-06-01 10:00:00", "2026-05-31 20:00:00", "Тестбаев Асан Тестович", "Whatsapp"],
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
        rows = [["Оралхан Асан", "950"], ["Жанель С", "925"]]
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
            parse(["Name", "Requests"], [["Оралхан Асан", "950"]], self.lookup, self.index)

    def test_combined_score_and_response_report_imports_both_metrics(self):
        parse = self.ns["_chat_report_parse"]
        header = [
            "operator_name", "created_at", "rating_scale_score",
            "request_start", "request_end", "reaction_time",
        ]
        rows = [
            ["Асан Тестбаев", "2026-06-10 09:00:00", "5", "2026-06-10 10:00:00", "2026-06-10 10:00:10", "10"],
            ["Асан Тестбаев", "2026-06-10 09:05:00", "3", "2026-06-10 10:05:00", "2026-06-10 10:05:20", "20"],
            ["Асан Тестбаев", "2026-06-11 09:00:00", "4", "2026-06-11 10:00:00", "2026-06-11 10:00:30", "30"],
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
        # rated_at уходит в базу уже разобранным и приведённым к Asia/Almaty, а
        # не сырой строкой: иначе время и day считаются в разных поясах.
        self.assertEqual(low["rated_at"], datetime(2026, 6, 10, 13, 0, 0))
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

    # ── Дубли низких оценок Chat2Desk (задача #267) ──────────────────────────
    # Вендор с вечера 01.09.2026 отдаёт закрытый день со сдвигом created_at на
    # 5 часов. Время входило в source_key, поэтому сдвинутая копия не склеивалась
    # и раздел показывал одно обращение дважды.

    @staticmethod
    def _rating_row(created_at, **over):
        row = {
            "operator_name": OPERATORS[1][1],
            "created_at": created_at,
            "rating_scale_score": "1",
            "rating_scale_id": 1158,
            "rating_id": 1042,
            "phone": "77027121150",
            "channel_name": "Jana Taxi",
            "request_id": 75762949,
            "valuation_request_id": 75762541,
        }
        row.update(over)
        return row

    def test_rating_source_key_ignores_shifted_created_at(self):
        """Сдвинутое время не должно менять ключ: заявка та же — оценка та же."""
        key = self.ns["_chat2desk_rating_source_key"]
        straight = self._rating_row("2026-09-01 00:34:55")
        shifted = self._rating_row("2026-09-01 05:34:55", assigned_phone="")
        self.assertEqual(
            key(straight, "2026-09-01", 1.0),
            key(shifted, "2026-09-01", 1.0),
        )
        # И ключ не зависит от того, какой day ему передали: сдвиг уводил часть
        # копий в соседние сутки, а склеиться они всё равно обязаны.
        self.assertEqual(
            key(straight, "2026-08-31", 1.0),
            key(shifted, "2026-09-01", 1.0),
        )
        self.assertTrue(key(straight, "2026-09-01", 1.0).startswith("c2d-rating:"))

    def test_rating_source_key_keeps_different_ratings_apart(self):
        """Обратная опасность: более узкий ключ не должен склеивать разные оценки."""
        key = self.ns["_chat2desk_rating_source_key"]
        base = self._rating_row("2026-09-01 00:34:55")
        keys = {
            key(base, "2026-09-01", 1.0),
            # другая шкала (клиент переоценил обращение)
            key(self._rating_row("2026-09-01 00:34:55", rating_scale_id=1156,
                                 rating_scale_score="3"), "2026-09-01", 3.0),
            # другая заявка того же клиента
            key(self._rating_row("2026-09-01 00:34:55", request_id=75762950,
                                 valuation_request_id=75762542), "2026-09-01", 1.0),
            # другой оператор
            key(self._rating_row("2026-09-01 00:34:55", operator_id=42815), "2026-09-01", 1.0),
        }
        self.assertEqual(len(keys), 4)

    def test_rating_time_straightened_by_request_stats(self):
        """Время оценки выпрямляется по request_stats, а day считается уже по нему.

        Сдвинутая на 5 часов вечерняя оценка иначе уезжает в следующие сутки —
        именно так 39 боевых пар разошлись по разным дням.
        """
        build = self.ns["_chat2desk_build_metrics_from_statistics_rows"]
        # Настоящее время оценки — 2026-08-31 21:40:00 (Алматы). Вендор отдал +5ч.
        rating = self._rating_row("2026-09-01 02:40:00", request_id=75760512,
                                  valuation_request_id=75755421)
        request_stats = [{
            "operator_name": OPERATORS[1][1],
            "request_id": 75760512,
            "request_start": "2026-08-31 21:30:00",
            "request_end": "2026-08-31 21:40:00",
            "reaction_time": "30",
        }]
        res = build("2026-09-01", None, [rating], self.lookup, self.index,
                    request_stats_rows=request_stats)
        self.assertEqual(res["low_rating_count"], 1)
        low = res["low_ratings"][0]
        self.assertEqual(low["rated_at"], datetime(2026, 8, 31, 21, 40, 0))
        self.assertEqual(low["day"], "2026-08-31")
        # Балл дня тоже должен лечь в 31 августа, а не в 1 сентября.
        self.assertEqual({m["day"] for m in res["metrics"] if m.get("score_count")}, {"2026-08-31"})
        self.assertEqual(res["rating_time_shifts"], {"18000": 1})

    def test_rating_time_uses_previous_day_request_stats(self):
        """Опора берётся и за предыдущие сутки: окно отчёта rating сдвинуто."""
        build = self.ns["_chat2desk_build_metrics_from_statistics_rows"]
        rating = self._rating_row("2026-09-01 02:40:00")
        prev_stats = [{
            "operator_name": OPERATORS[1][1],
            "request_id": 75762949,
            "request_end": "2026-08-31 21:40:00",
            "reaction_time": "30",
        }]
        res = build("2026-09-01", None, [rating], self.lookup, self.index,
                    request_stats_rows=[], prev_request_stats_rows=prev_stats)
        self.assertEqual(res["low_ratings"][0]["rated_at"], datetime(2026, 8, 31, 21, 40, 0))

    def test_rating_time_left_alone_when_vendor_is_healthy(self):
        """Когда сдвига нет, время не трогаем — вычитать 5 часов вслепую нельзя."""
        build = self.ns["_chat2desk_build_metrics_from_statistics_rows"]
        rating = self._rating_row("2026-09-02 19:15:15")
        request_stats = [{
            "operator_name": OPERATORS[1][1],
            "request_id": 75762949,
            "request_end": "2026-09-02 19:15:15",
            "reaction_time": "30",
        }]
        res = build("2026-09-02", None, [rating], self.lookup, self.index,
                    request_stats_rows=request_stats)
        self.assertEqual(res["low_ratings"][0]["rated_at"], datetime(2026, 9, 2, 19, 15, 15))
        self.assertEqual(res["low_ratings"][0]["day"], "2026-09-02")
        self.assertEqual(res["rating_time_shifts"], {"0": 1})

    def test_rating_shift_is_per_row_not_per_day(self):
        """В одном ответе бывают и целые строки, и сдвинутые — сдвиг построчный."""
        shifts = self.ns["_chat2desk_rating_time_shifts"]
        index = self.ns["_chat2desk_request_end_index"]([
            {"request_id": 1, "request_end": "2026-09-02 19:15:15"},
            {"request_id": 2, "request_end": "2026-09-01 21:40:00"},
        ])
        rows = [
            self._rating_row("2026-09-02 19:15:15", request_id=1),
            self._rating_row("2026-09-02 02:40:00", request_id=2),
        ]
        self.assertEqual(shifts(rows, index), [0, 18000])

    def test_rating_shift_falls_back_to_dominant_and_tolerates_rounding(self):
        """Оценка много позже конца диалога не опора — ей достаётся сдвиг дня.

        Заодно: источник иногда округляет секунду, 17999 с — тот же сдвиг.
        """
        shifts = self.ns["_chat2desk_rating_time_shifts"]
        index = self.ns["_chat2desk_request_end_index"]([
            {"request_id": 1, "request_end": "2026-09-01 10:00:00"},
            {"request_id": 2, "request_end": "2026-09-01 11:00:01"},
            {"request_id": 3, "request_end": "2026-09-01 12:00:00"},
        ])
        rows = [
            self._rating_row("2026-09-01 15:00:00", request_id=1),   # ровно +5ч
            self._rating_row("2026-09-01 16:00:00", request_id=2),   # +5ч без секунды
            self._rating_row("2026-09-01 21:31:07", request_id=3),   # опоздала, не опора
            self._rating_row("2026-09-01 20:00:00", request_id=99),  # заявки нет вовсе
        ]
        self.assertEqual(shifts(rows, index), [18000, 18000, 18000, 18000])

    def test_duplicate_rating_in_one_response_counted_once(self):
        """Вендор кладёт обе копии в один ответ: вторая не должна ни удваивать
        средний балл дня, ни уезжать второй строкой в базу (там она уронила бы
        execute_values на «cannot affect row a second time»)."""
        build = self.ns["_chat2desk_build_metrics_from_statistics_rows"]
        request_stats = [{
            "operator_name": OPERATORS[1][1],
            "request_id": 75762949,
            "request_end": "2026-09-01 00:34:55",
            "reaction_time": "30",
        }]
        rows = [
            self._rating_row("2026-09-01 00:34:55"),
            self._rating_row("2026-09-01 05:34:55", assigned_phone="77078544502"),
        ]
        res = build("2026-09-01", None, rows, self.lookup, self.index,
                    request_stats_rows=request_stats)
        self.assertEqual(res["low_rating_count"], 1)
        self.assertEqual(res["duplicate_rating_rows"], 1)
        day = {(m["operator_id"], m["day"]): m for m in res["metrics"]}[(2, "2026-09-01")]
        self.assertEqual(day["score_count"], 1)
        self.assertEqual(day["score_sum"], 1.0)

    def test_rating_without_request_ids_keeps_time_in_key(self):
        """Без номеров заявки склеивать нечем — там время в ключе остаётся."""
        key = self.ns["_chat2desk_rating_source_key"]
        bare = {
            "operator_name": OPERATORS[1][1],
            "rating_scale_score": "1",
            "rating_id": 1042,
            "created_at": "2026-09-01 00:34:55",
        }
        other = dict(bare, created_at="2026-09-01 05:34:55")
        first = key(bare, "2026-09-01", 1.0)
        self.assertNotEqual(first, key(other, "2026-09-01", 1.0))
        self.assertFalse(first.startswith("c2d-rating:"))

    def test_response_time_uses_request_stats_not_operator_replies(self):
        """Время ответа считается по request_stats (одна строка на заявку), а не по
        operator_replies, который завышает reaction_time для переданных заявок.
        Также проверяем, что на working_reaction_time не откатываемся."""
        build = self.ns["_chat2desk_build_metrics_from_statistics_rows"]
        operator_name = OPERATORS[1][1]
        # request_stats: настоящие значения reaction_time -> среднее 40
        request_stats = [
            {"operator_name": operator_name, "request_start": "2026-06-10 10:00:00", "reaction_time": "30"},
            {"operator_name": operator_name, "request_start": "2026-06-10 10:05:00", "reaction_time": "50"},
            # пустой reaction_time, но есть working_reaction_time — строка должна быть исключена
            {"operator_name": operator_name, "request_start": "2026-06-10 10:10:00",
             "reaction_time": "", "working_reaction_time": "9999"},
            # рейтинговая строка без оператора/реакции — игнорируется
            {"operator_name": "", "request_start": "2026-06-10 10:11:00", "reaction_time": "", "request_type": "rating"},
        ]
        # operator_replies со «взорванным» reaction_time — НЕ должен учитываться
        inflated_replies = [
            {"operator_name": operator_name, "request_start": "2026-06-10 09:00:00", "reaction_time": "18000"},
        ]

        res = build("2026-06-10", inflated_replies, [], self.lookup, self.index,
                    request_stats_rows=request_stats)
        self.assertEqual(res["detected_type"], "response_time")
        self.assertEqual(res["update_fields"], ["avg_response_time_seconds"])
        self.assertEqual(res["api_rows"]["request_stats"], 4)
        by_key = {(m["operator_id"], m["day"]): m for m in res["metrics"]}
        d10 = by_key[(2, "2026-06-10")]
        # (30 + 50) / 2 = 40 — без 18000 (operator_replies) и без 9999 (working_reaction_time)
        self.assertEqual(d10["avg_response_time_seconds"], 40.0)

    def test_request_stats_missing_response_clears_stale_value(self):
        build = self.ns["_chat2desk_build_metrics_from_statistics_rows"]
        operator_name = OPERATORS[0][1]
        other_operator_name = OPERATORS[1][1]
        request_stats = [
            {
                "operator_name": other_operator_name,
                "request_start": "2026-06-13 10:00:00",
                "reaction_time": "30",
            },
            {
                "operator_name": "",
                "request_start": "2026-06-13 04:47:13",
                "reaction_time": "",
                "request_id": "74362227",
            },
        ]
        operator_stats = [
            {
                "operator_name": operator_name,
                "date": "2026-06-13",
                "requests_took_part": 0,
                "requests_replied_first": 0,
            },
            {
                "operator_name": other_operator_name,
                "date": "2026-06-13",
                "requests_took_part": 1,
                "requests_replied_first": 1,
            },
        ]

        res = build(
            "2026-06-13",
            [{"operator_name": operator_name, "request_start": "2026-06-13 04:47:13", "reaction_time": "224750"}],
            [],
            self.lookup,
            self.index,
            operator_stats_rows=operator_stats,
            request_stats_rows=request_stats,
        )
        self.assertIn("avg_response_time_seconds", res["update_fields"])
        by_key = {(m["operator_id"], m["day"]): m for m in res["metrics"]}
        self.assertIsNone(by_key[(1, "2026-06-13")]["avg_response_time_seconds"])
        self.assertEqual(by_key[(1, "2026-06-13")]["chats_count"], 0)
        self.assertEqual(by_key[(2, "2026-06-13")]["avg_response_time_seconds"], 30.0)

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
            ["Асан Тестбаев", "2026-06-10 10:00:00", "2026-06-10 10:00:10", "10"],
            ["Асан Тестбаев", "2026-06-10 23:58:55", "2026-06-11 00:42:08", "6"],
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
