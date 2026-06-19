import ast
import csv
import os
import re
import unicodedata
import unittest
from datetime import datetime, timedelta
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from openpyxl import Workbook


BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"


def _status_import_namespace():
    wanted_assignments = {
        "CHAT2DESK_STATUS_EVENT_MAP",
        "CHAT2DESK_ACTION_EVENT_MAP",
        "CHAT2DESK_IGNORED_STATUS_EVENTS",
        "CHAT2DESK_STATISTICS_REPORT_OPERATOR_EVENTS",
    }
    wanted_functions = {
        "_status_import_normalize_key",
        "_status_import_normalize_header",
        "_status_import_normalize_operator_name",
        "_status_import_operator_name_variants",
        "_status_import_parse_datetime",
        "_status_import_resolve_break_note_label",
        "_status_import_resolve_display_state",
        "_status_import_secure_filename_and_ext",
        "_status_import_build_operator_lookup",
        "_status_import_split_segment_by_day",
        "_xlsx_cell_ref_to_index",
        "_status_import_xlsx_rows",
        "_status_import_parse_csv",
        "_status_import_parse_xlsx",
        "_chat_metrics_parse_date",
        "_chat2desk_row_first",
        "_chat2desk_build_status_import_from_operator_events",
    }

    module = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    selected_nodes = []
    for node in module.body:
        if isinstance(node, ast.Assign):
            assigned_names = {
                target.id for target in node.targets
                if isinstance(target, ast.Name)
            }
            if assigned_names & wanted_assignments:
                selected_nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            selected_nodes.append(node)

    namespace = {
        "BytesIO": BytesIO,
        "ET": ET,
        "ZipFile": ZipFile,
        "StringIO": StringIO,
        "csv": csv,
        "datetime": datetime,
        "timedelta": timedelta,
        "os": os,
        "re": re,
        "secure_filename": lambda filename: re.sub(
            r"\s+",
            "_",
            re.sub(
                r"[^\w\s.-]",
                "",
                unicodedata.normalize("NFKD", str(filename or ""))
                .encode("ascii", "ignore")
                .decode("ascii"),
            ).strip("._ "),
        ),
        "STATUS_IMPORT_INVALID_ROWS_PREVIEW_LIMIT": 30,
        "STATUS_IMPORT_MAX_SOURCE_ROWS": 120000,
    }
    exec(
        compile(ast.Module(body=selected_nodes, type_ignores=[]), str(BOT_PATH), "exec"),
        namespace,
    )
    return namespace


class StatusImportChat2DeskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = _status_import_namespace()

    def test_new_export_event_aliases_are_canonicalized(self):
        resolve = self.ns["_status_import_resolve_display_state"]

        self.assertEqual(resolve("online", None)["key"], "online")
        self.assertEqual(resolve("tech_break", None)["key"], "тех причина")
        self.assertEqual(resolve("status.tech_break", None)["key"], "тех причина")
        self.assertEqual(resolve("take_chat", None)["key"], "take chat")
        self.assertEqual(resolve("transfer_chat", None)["key"], "transfer chat")

    def test_telephony_lookup_excludes_chat_managers(self):
        build_lookup = self.ns["_status_import_build_operator_lookup"]

        class FakeDb:
            @staticmethod
            def get_all_operators():
                return [
                    (1, "Phone Operator", 10, None, None, None, None, "Основа", "operator"),
                    (2, "Chat By Model", 20, None, None, None, None, "Поддержка", "chat_manager"),
                    (3, "Chat By Name", 30, None, None, None, None, "Чат менеджер", None),
                ]

        previous_db = self.ns.get("db")
        self.ns["db"] = FakeDb()
        try:
            all_lookup = build_lookup()
            telephony_lookup = build_lookup(exclude_chat_managers=True)
        finally:
            self.ns["db"] = previous_db

        normalize = self.ns["_status_import_normalize_operator_name"]
        self.assertIn(normalize("Phone Operator"), telephony_lookup)
        self.assertNotIn(normalize("Chat By Model"), telephony_lookup)
        self.assertNotIn(normalize("Chat By Name"), telephony_lookup)
        self.assertIn(normalize("Chat By Model"), all_lookup)
        self.assertIn(normalize("Chat By Name"), all_lookup)

    def test_operator_name_normalization_collapses_trailing_soft_sign(self):
        normalize_name = self.ns["_status_import_normalize_operator_name"]
        name_variants = self.ns["_status_import_operator_name_variants"]

        self.assertEqual(normalize_name("Асель Серикбаева"), "асел серикбаева")
        self.assertEqual(normalize_name("Асел Серикбаева"), "асел серикбаева")
        self.assertEqual(normalize_name("Игорь"), "игор")
        # Внутрисловный мягкий знак не трогаем.
        self.assertEqual(normalize_name("Татьяна"), "татьяна")

        db_variants = set(name_variants("Асель Серикбаева"))
        chat2desk_key = normalize_name("Асел Серикбаева")
        self.assertIn(chat2desk_key, db_variants)

    def test_chat2desk_offline_events_are_ignored(self):
        resolve = self.ns["_status_import_resolve_display_state"]

        for event in ("offline", "status.offline", "Status.Offline", "OFFLINE"):
            with self.subTest(event=event):
                resolved = resolve(event, None)
                self.assertEqual(resolved["kind"], "ignore")
                self.assertEqual(resolved["key"], "")

    def test_cyrillic_upload_filename_keeps_original_extension(self):
        filename_and_ext = self.ns["_status_import_secure_filename_and_ext"]

        self.assertEqual(
            filename_and_ext("статусы операторов.csv"),
            ("statuses.csv", ".csv"),
        )
        self.assertEqual(
            filename_and_ext("отчет.xlsx"),
            ("statuses.xlsx", ".xlsx"),
        )
        self.assertEqual(
            filename_and_ext("22.05-25.05.csv"),
            ("22.05-25.05.csv", ".csv"),
        )

    def test_new_export_xlsx_headers_are_supported(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append([
            "operator_id",
            "operator_name",
            "operator_role",
            "operator_groups",
            "event",
            "dialog_id",
            "created_at",
            "status_duration",
        ])
        sheet.append(["100", "Jane Doe", "agent", "", "online", "", "2026-05-11 09:00:00", "1800"])
        sheet.append(["100", "Jane Doe", "agent", "", "tech_break", "", "2026-05-11 09:30:00", "300"])
        sheet.append(["100", "Jane Doe", "agent", "", "offline", "", "2026-05-11 09:35:00", ""])
        sheet.append(["100", "Jane Doe", "agent", "", "take_chat", "42", "2026-05-11 09:36:00", ""])

        raw_bytes = BytesIO()
        workbook.save(raw_bytes)

        parse_xlsx = self.ns["_status_import_parse_xlsx"]
        normalize_name = self.ns["_status_import_normalize_operator_name"]
        parsed = parse_xlsx(
            raw_bytes.getvalue(),
            {normalize_name("Jane Doe"): [{"id": 1, "name": "Jane Doe"}]},
        )

        self.assertEqual(parsed["source_rows"], 4)
        self.assertEqual(parsed["valid_events"], 4)
        self.assertEqual(parsed["matched_events"], 3)
        self.assertEqual(parsed["invalid_rows_count"], 0)
        self.assertEqual(parsed.get("ignored_events_count"), 1)
        self.assertEqual(
            [event["status_key"] for event in parsed["events"]],
            ["online", "тех причина", "take chat"],
        )
        self.assertEqual(parsed["action_events_count"], 1)

    def test_chat2desk_operator_events_rows_can_feed_status_import(self):
        build = self.ns["_chat2desk_build_status_import_from_operator_events"]
        normalize_name = self.ns["_status_import_normalize_operator_name"]

        rows = [
            {
                "operator_id": 100,
                "operator_name": "Jane Doe",
                "event": "online",
                "created_at": "2026-06-06 09:00:00",
                "status_duration": 3600,
            },
            {
                "operator_id": 100,
                "operator_name": "Jane Doe",
                "event": "break",
                "created_at": "2026-06-06 10:00:00",
                "status_duration": 60,
            },
            {
                "operator_id": 100,
                "operator_name": "Jane Doe",
                "event": "take_chat",
                "created_at": "2026-06-06 10:01:00",
                "status_duration": "",
            },
            {
                "operator_id": 100,
                "operator_name": "Jane Doe",
                "event": "holiday",
                "created_at": "2026-06-06 18:00:00",
                "status_duration": 1800,
            },
            {
                "operator_id": 100,
                "operator_name": "Jane Doe",
                "event": "offline",
                "created_at": "2026-06-06 18:30:00",
                "status_duration": "",
            },
        ]

        parsed = build(
            "2026-06-06",
            rows,
            {normalize_name("Jane Doe"): [{"id": 1, "name": "Jane Doe"}]},
            preview_limit=10,
        )

        self.assertEqual(parsed["source_rows"], 5)
        self.assertEqual(parsed["valid_events"], 5)
        self.assertEqual(parsed["matched_events"], 4)
        self.assertEqual(parsed["invalid_rows_count"], 0)
        self.assertEqual(parsed["action_events_count"], 1)
        self.assertEqual(parsed["ignored_events_count"], 1)
        self.assertEqual(parsed["api_rows"]["operator_events"], 5)
        self.assertEqual(
            [event["status_key"] for event in parsed["events"]],
            ["online", "break", "take chat", "holiday"],
        )
        self.assertEqual(len(parsed["segments"]), 2)
        self.assertEqual(parsed["segments"][0]["status_key"], "online")
        self.assertEqual(parsed["segments"][0]["duration_sec"], 3600)
        self.assertEqual(parsed["segments"][1]["status_key"], "break")
        self.assertEqual(parsed["segments"][1]["duration_sec"], 28800)


if __name__ == "__main__":
    unittest.main()
