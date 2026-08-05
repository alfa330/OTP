# -*- coding: utf-8 -*-
"""Контракт Excel-выгрузки детализации лидов TEZ ОП."""

import ast
import copy
import io
import os
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8-sig")
BOT_TREE = ast.parse(BOT_SOURCE)
TOP_LEVEL_FUNCTIONS = {
    node.name: node
    for node in BOT_TREE.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
DATABASE_PATH = ROOT / "database.py"
DATABASE_SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")
DATABASE_TREE = ast.parse(DATABASE_SOURCE)


def _load_export_function(namespace):
    """Загружает route и его локальные helper-функции без импорта bot_schedule2."""
    pending = ["tez_leads_export"]
    selected = {}
    while pending:
        name = pending.pop()
        if name in selected or name in namespace:
            continue
        node = TOP_LEVEL_FUNCTIONS.get(name)
        if node is None:
            continue
        selected[name] = node
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                called_name = child.func.id
                if called_name in TOP_LEVEL_FUNCTIONS:
                    pending.append(called_name)

    nodes = []
    for original in BOT_TREE.body:
        if (
            isinstance(original, (ast.FunctionDef, ast.AsyncFunctionDef))
            and original.name in selected
        ):
            cloned = copy.deepcopy(original)
            cloned.decorator_list = []
            nodes.append(cloned)
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    result = dict(namespace)
    exec(compile(module, str(BOT_PATH), "exec"), result)
    return result["tez_leads_export"]


def _load_top_level_function(name, namespace=None):
    original = TOP_LEVEL_FUNCTIONS.get(name)
    if original is None:
        raise AssertionError(f"Missing function in bot_schedule2.py: {name}")
    cloned = copy.deepcopy(original)
    cloned.decorator_list = []
    module = ast.Module(body=[cloned], type_ignores=[])
    ast.fix_missing_locations(module)
    result = dict(namespace or {})
    exec(compile(module, str(BOT_PATH), "exec"), result)
    return result[name]


def _load_database_method(name):
    database_class = next(
        node
        for node in DATABASE_TREE.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    original = next(
        node
        for node in database_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    cloned = copy.deepcopy(original)
    cloned.decorator_list = []
    module = ast.Module(body=[cloned], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {}
    exec(compile(module, str(DATABASE_PATH), "exec"), namespace)
    return namespace[name], ast.get_source_segment(DATABASE_SOURCE, original)


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_tez_leads_detail(self, year, month, **kwargs):
        self.calls.append((year, month, kwargs))
        return self.rows


class _QuietLogging:
    @staticmethod
    def exception(*_args, **_kwargs):
        return None


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows


class _FakeDatabase:
    def __init__(self, row):
        self.cursor = _FakeCursor([row])

    @staticmethod
    def _tez_leads_detail_filters(_status=None, _operator_id=None, _search=None):
        return "", []

    def _get_cursor(self):
        return self.cursor


class TezLeadsExcelExportTests(unittest.TestCase):
    def _export(self, rows):
        db = _FakeDb(rows)

        def send_file(stream, **kwargs):
            return {
                "content": stream.getvalue(),
                "kwargs": kwargs,
            }

        export = _load_export_function(
            {
                "_tez_leads_require_manager": lambda: (17, None),
                "_tez_leads_period_from_request": lambda: (2026, 7),
                "db": db,
                "jsonify": lambda value: value,
                "logging": _QuietLogging,
                "Workbook": Workbook,
                "Font": Font,
                "PatternFill": PatternFill,
                "Alignment": Alignment,
                "BytesIO": io.BytesIO,
                "timedelta": timedelta,
                "send_file": send_file,
            }
        )
        response = export()
        workbook = load_workbook(io.BytesIO(response["content"]), data_only=True)
        return db, workbook.active

    @staticmethod
    def _detail_row(**overrides):
        row = {
            "lead_id": "8d0c0346-9563-46c3-b757-696414b19bf2",
            "phone": "77010000001",
            "full_name": "Тестовый водитель",
            "status": "success",
            "status_rule": "same_month",
            "upload_count": 1,
            "first_order_at": "2026-07-20T12:00:00",
            "operator_id": 17,
            "operator_name": "Оператор ТЕЗ",
            "call_at": "2026-07-18T10:15:00",
            "success_date": "2026-07-20",
            "rule_code": "same_month",
            "month_first_order_at": "2026-07-20T12:00:00",
            "prev_month_first_order_at": None,
            "source_file_name": "Лиды Алматы июль.xlsx",
            "talk_duration_seconds": 3723,
        }
        row.update(overrides)
        return row

    def test_export_contains_source_and_native_talk_duration_from_detail_payload(self):
        db, sheet = self._export([self._detail_row()])

        self.assertEqual(
            db.calls,
            [(2026, 7, {"limit": 50000})],
            "Excel должен строиться из полной выборки get_tez_leads_detail",
        )
        headers = [cell.value for cell in sheet[1]]
        self.assertIn("Источник", headers)
        self.assertIn("Время разговора", headers)

        source_column = headers.index("Источник") + 1
        duration_column = headers.index("Время разговора") + 1
        self.assertEqual(sheet.cell(2, source_column).value, "Лиды Алматы июль.xlsx")
        # Длительность — число секунд, а не формат времени: отчёт считают и
        # фильтруют в секундах, в них же задан порог успешки.
        self.assertEqual(sheet.cell(2, duration_column).value, 3723)
        self.assertEqual(sheet.cell(2, duration_column).number_format, "0")

    def test_missing_talk_duration_is_an_empty_excel_cell(self):
        _, sheet = self._export(
            [self._detail_row(source_file_name="", talk_duration_seconds=None)]
        )
        headers = [cell.value for cell in sheet[1]]
        source_column = headers.index("Источник") + 1
        duration_column = headers.index("Время разговора") + 1

        self.assertIsNone(sheet.cell(2, duration_column).value)
        self.assertIn(sheet.cell(2, source_column).value, (None, ""))

    def test_user_controlled_text_is_not_exported_as_an_excel_formula(self):
        _, sheet = self._export(
            [
                self._detail_row(
                    full_name="=1+1",
                    source_file_name='=HYPERLINK("https://example.invalid")',
                )
            ]
        )
        headers = [cell.value for cell in sheet[1]]
        source_column = headers.index("Источник") + 1

        self.assertEqual(sheet.cell(2, 1).value, "=1+1")
        self.assertEqual(sheet.cell(2, 1).data_type, "s")
        self.assertEqual(
            sheet.cell(2, source_column).value,
            '=HYPERLINK("https://example.invalid")',
        )
        self.assertEqual(sheet.cell(2, source_column).data_type, "s")

    def test_phone_column_is_text_formatted(self):
        """«Телефон» должен быть текстом и по типу, и по формату ячейки.

        С форматом «Общий» Excel при первой же правке превращает 77012345678 в
        число (длинные номера — в экспоненту), и номер перестаёт совпадать при
        поиске и сверке с базой.
        """
        _, sheet = self._export([self._detail_row()])
        headers = [cell.value for cell in sheet[1]]
        phone_column = headers.index("Телефон") + 1
        cell = sheet.cell(2, phone_column)
        self.assertEqual(cell.data_type, "s")
        self.assertEqual(cell.number_format, "@")
        # Длительность разговора — по-прежнему число, а не текст.
        duration_column = headers.index("Время разговора") + 1
        self.assertEqual(sheet.cell(2, duration_column).number_format, "0")

    def test_active_prev_month_exports_previous_reason_without_hiding_current_trip(self):
        _, sheet = self._export(
            [
                self._detail_row(
                    status="already_working",
                    status_rule="active_prev_month",
                    rule_code=None,
                    first_order_at="2026-07-03T22:47:00",
                    month_first_order_at="2026-07-03T22:47:00",
                    prev_month_first_order_at="2026-06-30T23:27:00",
                    call_at="2026-07-02T13:58:00",
                )
            ]
        )

        headers = [cell.value for cell in sheet[1]]
        previous_column = headers.index("Поездка в прошлом месяце") + 1
        current_column = headers.index("Поездка в отчётном месяце") + 1
        rule_column = headers.index("Правило") + 1

        self.assertEqual(sheet.cell(2, previous_column).value, "2026-06-30 23:27:00")
        self.assertEqual(sheet.cell(2, current_column).value, "2026-07-03 22:47:00")
        self.assertIn("прошлом месяце", sheet.cell(2, rule_column).value)


class TezLeadsDisplayFilenameTests(unittest.TestCase):
    def test_unicode_basename_and_control_character_sanitizing(self):
        display_filename = _load_top_level_function(
            "_tez_leads_display_filename",
            namespace={"os": os, "re": re},
        )

        self.assertEqual(
            display_filename("Лиды Алматы июль.xlsx"),
            "Лиды Алматы июль.xlsx",
        )
        self.assertEqual(
            display_filename(r"C:\Users\operator\Downloads\Лиды Алматы.xlsx"),
            "Лиды Алматы.xlsx",
        )
        self.assertEqual(
            display_filename("/tmp/tez-imports/Лиды Алматы.csv"),
            "Лиды Алматы.csv",
        )
        self.assertEqual(
            display_filename("Лиды\x00 Алматы\r\nиюль\t.xlsx"),
            "Лиды Алматыиюль.xlsx",
        )


class TezLeadsDetailPayloadTests(unittest.TestCase):
    def test_detail_exposes_source_filename_and_binotel_talk_duration(self):
        detail_method, source = _load_database_method("get_tez_leads_detail")
        db = _FakeDatabase(
            (
                "8d0c0346-9563-46c3-b757-696414b19bf2",
                "77010000001",
                "Тестовый водитель",
                "success",
                "same_month",
                1,
                None,
                17,
                "Оператор ТЕЗ",
                None,
                None,
                "same_month",
                None,
                "Лиды Алматы июль.xlsx",
                187,
            )
        )

        result = detail_method(db, 2026, 7)

        self.assertEqual(result[0]["source_file_name"], "Лиды Алматы июль.xlsx")
        self.assertEqual(result[0]["talk_duration_seconds"], 187)
        self.assertIn("source_batch.file_name", db.cursor.sql)
        self.assertIn("success_call.billsec", db.cursor.sql)
        self.assertIn("ELSE lc.billsec", db.cursor.sql)
        self.assertIn(
            "success_call.general_call_id = s.call_general_id",
            db.cursor.sql,
        )
        self.assertIn("'source_file_name'", source)
        self.assertIn("'talk_duration_seconds'", source)

    def test_detail_keeps_previous_reason_and_current_trip_as_separate_fields(self):
        detail_method, source = _load_database_method("get_tez_leads_detail")
        current_trip = datetime(2026, 7, 3, 22, 47)
        previous_trip = datetime(2026, 6, 30, 23, 27)
        db = _FakeDatabase(
            (
                "8d0c0346-9563-46c3-b757-696414b19bf2",
                "77015412280",
                "Тестовый водитель",
                "already_working",
                "active_prev_month",
                1,
                current_trip,
                None,
                "",
                None,
                None,
                None,
                previous_trip,
                "Лиды ОП.xlsx",
                None,
            )
        )

        row = detail_method(db, 2026, 7)[0]

        # first_order_at остаётся для старых клиентов, но новое явное
        # поле не даёт спутать его с причиной active_prev_month.
        self.assertEqual(row["first_order_at"], "2026-07-03T22:47:00")
        self.assertEqual(row["month_first_order_at"], "2026-07-03T22:47:00")
        self.assertEqual(row["prev_month_first_order_at"], "2026-06-30T23:27:00")
        self.assertIn("'month_first_order_at'", source)


if __name__ == "__main__":
    unittest.main()
