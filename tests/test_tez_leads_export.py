# -*- coding: utf-8 -*-
"""Контракт Excel-выгрузки детализации лидов TEZ ОП."""

import ast
import copy
import io
import os
import re
import sys
import unittest
from datetime import date as dt_date, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BOT_PATH = ROOT / "bot_schedule2.py"
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8-sig")
BOT_TREE = source_cache.parse(BOT_SOURCE)
TOP_LEVEL_FUNCTIONS = {
    node.name: node
    for node in BOT_TREE.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
DATABASE_PATH = ROOT / "database.py"
DATABASE_SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")
DATABASE_TREE = source_cache.parse(DATABASE_SOURCE)


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
    """Выгрузка тянет ДВА периода: отчётный месяц и предыдущий (второй лист).

    prev_rows задаётся отдельно, иначе один и тот же набор строк вернулся бы на
    оба листа и тест не отличил бы их друг от друга.
    """

    def __init__(self, rows, prev_rows=None):
        self.rows = rows
        self.prev_rows = [] if prev_rows is None else prev_rows
        self.calls = []

    def get_tez_leads_detail(self, year, month, **kwargs):
        self.calls.append((year, month, kwargs))
        return self.rows if (year, month) == (2026, 7) else self.prev_rows


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
    def _export_bytes(self, rows, prev_rows=None):
        db = _FakeDb(rows, prev_rows)

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
                "datetime": datetime,
                "dt_date": dt_date,
                "timedelta": timedelta,
                "send_file": send_file,
                "get_column_letter": get_column_letter,
                "ZipFile": ZipFile,
                "ZIP_DEFLATED": ZIP_DEFLATED,
            }
        )
        response = export()
        return db, response["content"]

    def _export(self, rows, prev_rows=None):
        db, content = self._export_bytes(rows, prev_rows)
        workbook = load_workbook(io.BytesIO(content), data_only=True)
        return db, workbook.active

    def _export_book(self, rows, prev_rows=None):
        db, content = self._export_bytes(rows, prev_rows)
        return db, load_workbook(io.BytesIO(content), data_only=True)

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
            [(2026, 7, {"limit": 50000}), (2026, 6, {"limit": 50000})],
            "Excel строится из полной выборки: отчётный месяц + база предыдущего",
        )
        headers = [cell.value for cell in sheet[1]]
        self.assertIn("Источник", headers)
        self.assertIn("Время разговора в отчётном месяце", headers)

        source_column = headers.index("Источник") + 1
        duration_column = headers.index("Время разговора в отчётном месяце") + 1
        self.assertEqual(sheet.cell(2, source_column).value, "Лиды Алматы июль.xlsx")
        # Длительность — число секунд, а не формат времени: отчёт считают и
        # фильтруют в секундах, в них же задан порог успешки.
        self.assertEqual(sheet.cell(2, duration_column).value, 3723)
        self.assertEqual(sheet.cell(2, duration_column).number_format, "0")

    # ── Второй лист: база прошлого месяца ───────────────────────────────────

    def test_previous_month_base_goes_to_a_second_sheet(self):
        """Владелец: в выгрузку текущего месяца добавить базу прошлого целиком,
        отдельным листом и с пометкой, за какой месяц она действует."""
        prev = self._detail_row(phone="77010000002", status="in_progress",
                                status_rule=None, success_date=None,
                                month_first_order_at=None, first_order_at=None)
        db, book = self._export_book([self._detail_row()], [prev])

        self.assertEqual(book.sheetnames, ["Лиды 07.2026", "База 06.2026"])
        sheet = book["База 06.2026"]
        headers = [cell.value for cell in sheet[1]]
        self.assertEqual(headers[0], "Действие базы")
        self.assertEqual(sheet.cell(2, 1).value, "Июнь 2026")
        # Дальше идут те же колонки, что и на основном листе, но со сдвигом.
        self.assertEqual(headers[1:], [cell.value for cell in book["Лиды 07.2026"][1]])
        self.assertEqual(sheet.cell(2, headers.index("Телефон") + 1).value, "77010000002")

    def test_successes_paid_in_the_previous_month_are_excluded(self):
        """Успешки, засчитанные и выплаченные прошлым месяцем, в лист не идут."""
        paid = self._detail_row(phone="77010000003", success_date="2026-06-15")
        db, book = self._export_book([self._detail_row()], [paid])
        self.assertEqual(book.sheetnames, ["Лиды 07.2026"])

    def test_lead_counted_later_stays_in_the_previous_month_base(self):
        """А вот лид прошлого месяца, ставший успешкой уже в этом (перенос),
        остаётся: тем месяцем по нему не платили."""
        carried = self._detail_row(phone="77010000004", success_date="2026-07-03")
        db, book = self._export_book([self._detail_row()], [carried])
        sheet = book["База 06.2026"]
        headers = [cell.value for cell in sheet[1]]
        self.assertEqual(sheet.cell(2, headers.index("Телефон") + 1).value, "77010000004")
        self.assertEqual(sheet.cell(2, headers.index("Дата успешки") + 1).value, "2026-07-03")

    def test_previous_month_sheet_buckets_calls_by_its_own_month(self):
        """Звонок 25 июня на листе июня — это «в отчётном месяце», а не «в прошлом»:
        иначе на втором листе все звонки уехали бы в чужую колонку."""
        prev = self._detail_row(phone="77010000005", success_date=None,
                                call_at="2026-06-25T10:00:00", talk_duration_seconds=61)
        db, book = self._export_book([self._detail_row()], [prev])
        sheet = book["База 06.2026"]
        headers = [cell.value for cell in sheet[1]]
        own = headers.index("Звонок в отчётном месяце") + 1
        prev_col = headers.index("Звонок в прошлом месяце") + 1
        self.assertEqual(sheet.cell(2, own).value, "2026-06-25 10:00:00")
        # Пустая текстовая ячейка читается обратно как None.
        self.assertFalse(sheet.cell(2, prev_col).value)
        self.assertEqual(sheet.cell(2, own + 1).value, 61)

    def test_carried_success_shows_its_call_like_an_ordinary_one(self):
        """Перенесённая успешка: лид июня, поездка и звонок — в июле.

        Раскладка от месяца ЛИСТА оставляла обе пары колонок пустыми, и строка
        выходила успешкой без основания. Считаем от месяца зачёта — звонок
        встаёт в те же колонки, что и у обычной июльской успешки.
        """
        carried = self._detail_row(
            phone="77010000006",
            success_date="2026-07-14",
            call_at="2026-07-09T11:20:00",
            talk_duration_seconds=95,
        )
        _, book = self._export_book([self._detail_row()], [carried])
        sheet = book["База 06.2026"]
        col = self._call_columns(sheet)

        self.assertEqual(sheet.cell(2, col["month_call"]).value, "2026-07-09 11:20:00")
        self.assertEqual(sheet.cell(2, col["month_duration"]).value, 95)
        self.assertFalse(sheet.cell(2, col["prev_call"]).value)
        self.assertFalse(sheet.cell(2, col["prev_duration"]).value)

    def test_carried_success_on_the_month_seam_fills_the_previous_pair(self):
        """Звонок 25 июня при поездке 3 июля — окно «последние 7 дней прошлого
        месяца» относительно МЕСЯЦА ЗАЧЁТА, а не месяца листа."""
        carried = self._detail_row(
            phone="77010000007",
            success_date="2026-07-03",
            status_rule="prev_month_last7",
            rule_code="prev_month_last7",
            call_at="2026-06-25T10:00:00",
            talk_duration_seconds=61,
        )
        _, book = self._export_book([self._detail_row()], [carried])
        sheet = book["База 06.2026"]
        col = self._call_columns(sheet)

        self.assertEqual(sheet.cell(2, col["prev_call"]).value, "2026-06-25 10:00:00")
        self.assertEqual(sheet.cell(2, col["prev_duration"]).value, 61)
        self.assertFalse(sheet.cell(2, col["month_call"]).value)

    def test_call_reference_month_falls_back_to_the_sheet(self):
        """Без даты успешки (и при мусоре в ней) раскладка остаётся прежней —
        от месяца листа, иначе сломался бы весь второй лист."""
        reference = _load_top_level_function("_tez_leads_call_reference_month")
        self.assertEqual(reference(None, 2026, 6), (2026, 6))
        self.assertEqual(reference("", 2026, 6), (2026, 6))
        self.assertEqual(reference("не дата", 2026, 6), (2026, 6))
        self.assertEqual(reference("2026-13-01", 2026, 6), (2026, 6))
        self.assertEqual(reference("2026-06-30", 2026, 6), (2026, 6))
        self.assertEqual(reference("2026-07-03", 2026, 6), (2026, 7))
        self.assertEqual(reference("2027-01-05", 2026, 12), (2027, 1))

    def test_missing_talk_duration_is_an_empty_excel_cell(self):
        _, sheet = self._export(
            [self._detail_row(source_file_name="", talk_duration_seconds=None)]
        )
        headers = [cell.value for cell in sheet[1]]
        source_column = headers.index("Источник") + 1
        duration_column = headers.index("Время разговора в отчётном месяце") + 1

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
        duration_column = headers.index("Время разговора в отчётном месяце") + 1
        self.assertEqual(sheet.cell(2, duration_column).number_format, "0")

    def test_phone_column_has_no_number_stored_as_text_warning(self):
        """Текстовый формат не должен тащить за собой значок ошибки Excel.

        Без <ignoredErrors> Excel рисует зелёный уголок «Число сохранено как
        текст» на каждой строке телефона — формат при этом правильный, но
        выгрузка выглядит битой.
        """
        _, content = self._export_bytes([self._detail_row(), self._detail_row()])
        with ZipFile(io.BytesIO(content)) as book:
            sheet_xml = book.read("xl/worksheets/sheet1.xml").decode("utf-8")

        self.assertIn('numberStoredAsText="1"', sheet_xml)
        self.assertIn('sqref="B2:B3"', sheet_xml)
        # Узел обязан стоять после pageMargins — иначе Excel считает книгу битой.
        self.assertLess(
            sheet_xml.index("<pageMargins"), sheet_xml.index("<ignoredErrors")
        )
        # Файл после правки zip остаётся читаемым книгой, а не только текстом.
        sheet = load_workbook(io.BytesIO(content), data_only=True).active
        self.assertEqual(sheet.cell(2, 2).value, "77010000001")

    def test_empty_export_is_not_patched(self):
        _, content = self._export_bytes([])
        with ZipFile(io.BytesIO(content)) as book:
            sheet_xml = book.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertNotIn("<ignoredErrors", sheet_xml)

    def _call_columns(self, sheet):
        headers = [cell.value for cell in sheet[1]]
        return {
            "prev_call": headers.index("Звонок в прошлом месяце") + 1,
            "prev_duration": headers.index("Время разговора в прошлом месяце") + 1,
            "month_call": headers.index("Звонок в отчётном месяце") + 1,
            "month_duration": headers.index("Время разговора в отчётном месяце") + 1,
        }

    def test_call_inside_reporting_month_fills_only_the_reporting_pair(self):
        _, sheet = self._export(
            [self._detail_row(call_at="2026-07-18T10:15:00", talk_duration_seconds=44)]
        )
        col = self._call_columns(sheet)

        self.assertEqual(sheet.cell(2, col["month_call"]).value, "2026-07-18 10:15:00")
        self.assertEqual(sheet.cell(2, col["month_duration"]).value, 44)
        self.assertIsNone(sheet.cell(2, col["prev_call"]).value)
        self.assertIsNone(sheet.cell(2, col["prev_duration"]).value)

    def test_call_in_last_seven_days_of_previous_month_fills_only_the_previous_pair(self):
        """Окно считается от конца месяца: в июне это 24–30, а не «после 24-го»."""
        _, sheet = self._export(
            [self._detail_row(call_at="2026-06-24T09:00:00", talk_duration_seconds=61)]
        )
        col = self._call_columns(sheet)

        self.assertEqual(sheet.cell(2, col["prev_call"]).value, "2026-06-24 09:00:00")
        self.assertEqual(sheet.cell(2, col["prev_duration"]).value, 61)
        self.assertIsNone(sheet.cell(2, col["month_call"]).value)
        self.assertIsNone(sheet.cell(2, col["month_duration"]).value)

    def test_call_before_the_window_lands_in_neither_pair(self):
        """23 июня — на день раньше окна, звонок успешку дать не может."""
        _, sheet = self._export(
            [
                self._detail_row(
                    call_at="2026-06-23T23:59:00",
                    status="not_counted",
                    status_rule="call_before_last7",
                    talk_duration_seconds=300,
                )
            ]
        )
        col = self._call_columns(sheet)

        for column in col.values():
            self.assertIsNone(sheet.cell(2, column).value)
        # Причина при этом остаётся видимой — иначе строка нечитаема.
        headers = [cell.value for cell in sheet[1]]
        rule_column = headers.index("Правило") + 1
        self.assertIn("раньше", sheet.cell(2, rule_column).value)

    def test_call_without_a_date_leaves_both_pairs_empty(self):
        _, sheet = self._export(
            [self._detail_row(call_at=None, talk_duration_seconds=None)]
        )
        col = self._call_columns(sheet)

        for column in col.values():
            self.assertIsNone(sheet.cell(2, column).value)

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


class TezLeadsCallBucketTests(unittest.TestCase):
    """Раскладка звонка по месяцам считается тем же окном, что и успешка."""

    def setUp(self):
        self.bucket = _load_top_level_function(
            "_tez_leads_call_bucket",
            namespace={"datetime": datetime, "dt_date": dt_date},
        )

    def test_reporting_month(self):
        self.assertEqual(self.bucket("2026-07-01T00:00:00", 2026, 7), "month")
        self.assertEqual(self.bucket("2026-07-31T23:59:00", 2026, 7), "month")

    def test_previous_month_window_is_counted_from_the_month_end(self):
        # Июнь (30 дней) -> окно 24–30, февраль-2026 (28 дней) -> 22–28.
        self.assertEqual(self.bucket("2026-06-24T00:00:00", 2026, 7), "prev")
        self.assertIsNone(self.bucket("2026-06-23T23:59:00", 2026, 7))
        self.assertEqual(self.bucket("2026-02-22T10:00:00", 2026, 3), "prev")
        self.assertIsNone(self.bucket("2026-02-21T10:00:00", 2026, 3))

    def test_january_looks_at_december_of_the_previous_year(self):
        self.assertEqual(self.bucket("2025-12-25T08:00:00", 2026, 1), "prev")
        self.assertIsNone(self.bucket("2025-12-24T08:00:00", 2026, 1))
        self.assertEqual(self.bucket("2026-01-05T08:00:00", 2026, 1), "month")

    def test_missing_or_broken_date_is_not_placed_anywhere(self):
        self.assertIsNone(self.bucket(None, 2026, 7))
        self.assertIsNone(self.bucket("", 2026, 7))
        self.assertIsNone(self.bucket("не дата", 2026, 7))

    def test_window_start_matches_the_success_rule(self):
        """Окно не должно разъехаться с расчётом успешки — источник правды один."""
        from tez_op_leads import call_window_for_period

        for year, month in ((2026, 1), (2026, 3), (2026, 7), (2026, 12)):
            start, _ = call_window_for_period(year, month)
            self.assertEqual(
                self.bucket(datetime.combine(start, datetime.min.time()).isoformat(),
                            year, month),
                "prev",
                f"первый день окна {start} обязан попасть в прошлый месяц",
            )
            before = start - timedelta(days=1)
            self.assertIsNone(
                self.bucket(datetime.combine(before, datetime.min.time()).isoformat(),
                            year, month),
                f"день до окна {before} не должен попадать никуда",
            )


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
                "77000000105",
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
