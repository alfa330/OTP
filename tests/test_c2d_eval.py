"""Тесты раздела «Оценка чатов ЧМ» (Chat2Desk): нормализация заявок request_stats,
медиа-ссылки/время API, валидация цитат и критериев оценки."""
import ast
import csv
import json
import os
import re
import unittest
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"


def _c2d_namespace():
    wanted_assignments = {
        "CHAT2DESK_API_TOKEN",
        "CHAT2DESK_AUTH_SCHEME",
        "CHAT2DESK_SYNC_TIMEZONE",
        "CHAT2DESK_STORAGE_BASE_URL",
        "C2D_EVAL_MAX_MESSAGE_PAGES",
        "_KZ_TO_RU_FOLD",
    }
    wanted_functions = {
        "_env_bool",
        "_env_int",
        "_status_import_normalize_operator_name",
        "_status_import_operator_name_variants",
        "_status_import_parse_datetime",
        "_chat_metrics_parse_date",
        "_chat_metrics_parse_number",
        "_chat_report_name_tokens",
        "_chat_report_tokens_match",
        "_chat_report_resolve_operator",
        "_chat_report_parse_dt",
        "_chat2desk_sync_timezone",
        "_chat2desk_parse_datetime",
        "_chat2desk_row_first",
        "_chat2desk_build_request_rows",
        "_c2d_parse_api_datetime",
        "_c2d_media_url",
        "_c2d_normalize_message",
        "_c2d_eval_normalize_quotes",
        "_c2d_eval_normalize_criteria",
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
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace


OPERATORS = [
    (11, "Серикбаева Асель Болатовна"),
    (12, "Нурланова Жанеля Ерлановна"),
]


class C2dEvalHelpersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = _c2d_namespace()
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

    # ── request_stats -> c2d_requests ──

    def test_build_request_rows_full_row(self):
        rows = self.ns["_chat2desk_build_request_rows"]("2026-07-16", [{
            "request_id": 75038611,
            "request_start": "2026-07-16 23:59:00",
            "request_end": "2026-07-17 00:03:27",
            "request_time": 267,
            "reaction_time": "12",
            "request_type": "common",
            "transport": "wa_dialog",
            "channel_id": 2137,
            "channel_name": "Техподдержка iTaxi",
            "client_id": 63897687,
            "client_name": "Dimash",
            "phone": "77052995356",
            "operator_id": 42617,
            "operator_name": "Асель Серикбаева",
            "incoming_messages": 5,
            "outgoing_messages": "7",
            "rating_scale_score": "4.0",
            "rating_text": "Отлично",
        }], self.lookup, self.index)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["request_id"], 75038611)
        self.assertEqual(str(row["day"]), "2026-07-16")  # день из request_start
        self.assertEqual(row["operator_id"], 11)  # сматчен по имени
        self.assertEqual(row["c2d_operator_name"], "Асель Серикбаева")
        self.assertEqual(row["c2d_operator_id"], 42617)
        self.assertEqual(row["incoming_messages"], 5)
        self.assertEqual(row["outgoing_messages"], 7)
        self.assertEqual(row["rating_score"], 4.0)
        self.assertEqual(row["transport"], "wa_dialog")
        self.assertEqual(row["client_phone"], "77052995356")

    def test_build_request_rows_skips_and_unmatched(self):
        rows = self.ns["_chat2desk_build_request_rows"]("2026-07-16", [
            {"operator_name": "Кто-то", "request_start": "2026-07-16 10:00:00"},  # без request_id
            {"request_id": 2, "operator_name": "Неизвестный Оператор",
             "request_start": "2026-07-16 10:00:00"},
            {"request_id": 3, "operator_name": "", "request_start": ""},
        ], self.lookup, self.index)
        self.assertEqual([r["request_id"] for r in rows], [2, 3])
        self.assertIsNone(rows[0]["operator_id"])  # не сматчен — сохранён без оператора
        self.assertEqual(str(rows[1]["day"]), "2026-07-16")  # fallback на day_str

    # ── медиа и время API ──

    def test_media_url(self):
        media = self.ns["_c2d_media_url"]
        base = self.ns["CHAT2DESK_STORAGE_BASE_URL"]
        self.assertEqual(
            media("companies/company_12097/messages/2026-7/x.jpg"),
            f"{base}/companies/company_12097/messages/2026-7/x.jpg")
        self.assertEqual(media("https://example.com/a.jpg"), "https://example.com/a.jpg")
        self.assertIsNone(media(""))
        self.assertIsNone(media(None))

    def test_parse_api_datetime_utc_to_almaty(self):
        parsed = self.ns["_c2d_parse_api_datetime"](
            "2026-07-17T10:45:31 UTC", ZoneInfo("Asia/Almaty"))
        self.assertEqual(parsed, datetime(2026, 7, 17, 15, 45, 31))  # UTC+5, naive

    def test_normalize_message(self):
        msg = self.ns["_c2d_normalize_message"]({
            "id": 657538100,
            "type": "from_client",
            "text": None,
            "created": "2026-07-17T10:48:57 UTC",
            "photo": "companies/company_12097/messages/2026-7/a.jpg",
            "attachments": [{"name": "a.jpg", "link": "https://storage-02.chat2desk.kz/a.jpg"},
                            {"no_link": True}],
            "request_id": 75050526,
        }, ZoneInfo("Asia/Almaty"))
        self.assertEqual(msg["id"], 657538100)
        self.assertEqual(msg["text"], "")
        self.assertTrue(msg["photo"].startswith("https://"))
        self.assertEqual(len(msg["attachments"]), 1)  # битые вложения отброшены
        self.assertEqual(msg["created"], "2026-07-17T15:48:57")
        self.assertEqual(msg["requestId"], 75050526)

    # ── цитаты ──

    def test_quotes_valid_and_normalized(self):
        messages = [{"id": 1, "text": "Здравствуйте!  Чем могу\nпомочь вам сегодня?"}]
        quotes, err = self.ns["_c2d_eval_normalize_quotes"](
            [{"messageId": 1, "text": "чем могу помочь", "comment": "нет приветствия по скрипту"}],
            messages)
        self.assertIsNone(err)
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0]["messageId"], 1)
        self.assertEqual(quotes[0]["comment"], "нет приветствия по скрипту")

    def test_quotes_unknown_message_and_mismatch(self):
        messages = [{"id": 1, "text": "Добрый день"}]
        _, err = self.ns["_c2d_eval_normalize_quotes"](
            [{"messageId": 99, "text": "Добрый день"}], messages)
        self.assertIsNotNone(err)
        _, err = self.ns["_c2d_eval_normalize_quotes"](
            [{"messageId": 1, "text": "такого текста нет"}], messages)
        self.assertIsNotNone(err)

    def test_quotes_empty_entries_skipped(self):
        quotes, err = self.ns["_c2d_eval_normalize_quotes"](
            [{"messageId": 1, "text": "  "}, "мусор"], [{"id": 1, "text": "х"}])
        self.assertIsNone(err)
        self.assertEqual(quotes, [])

    # ── критерии ──

    def test_criteria_average_and_partial(self):
        criteria, score, err = self.ns["_c2d_eval_normalize_criteria"]([
            {"key": "greeting", "label": "Приветствие", "score": 5},
            {"key": "solution", "label": "Решение", "score": 4},
            {"key": "literacy", "label": "Грамотность", "score": None},  # не заполнен
        ])
        self.assertIsNone(err)
        self.assertEqual(len(criteria), 2)
        self.assertEqual(score, 4.5)

    def test_criteria_validation_errors(self):
        _, _, err = self.ns["_c2d_eval_normalize_criteria"]([{"key": "a", "score": 6}])
        self.assertIsNotNone(err)
        _, _, err = self.ns["_c2d_eval_normalize_criteria"]([{"key": "a", "score": "abc"}])
        self.assertIsNotNone(err)
        _, _, err = self.ns["_c2d_eval_normalize_criteria"]([])
        self.assertIsNotNone(err)


if __name__ == "__main__":
    unittest.main()
