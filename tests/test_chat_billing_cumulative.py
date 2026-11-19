# -*- coding: utf-8 -*-
"""«Биллинг чатов → Таксопарки»: второй лист выгрузки «Накопительный итог» (владелец 19.11.2026).

«Накопительный сделай просто вторым листом, первый лист как обычно». Вид — скриншота таблицы СЗоВ,
колонки — биллинга чатов (выбор владельца: AR, «Сброса на IVR» и средней оценки у чатов нет). Что
сторожится настоящими функциями bot_schedule2 (через ast) и настоящим get_chat_billing_report:
  * первый лист — прежний «Ежедневный отчёт» ячейка в ячейку, второй — «Накопительный итог»;
  * шапка образца: тёмно-синяя, белый жирный Calibri 11, по центру; рамок нет;
  * парки по убыванию поступивших, Global скрыт, как на первом листе; имя парка всегда текст;
  * SL от поступивших, минуты — до десятых с половиной вверх; нечего считать — прочерк;
  * итог — из сумм видимых строк, а не среднее строк; «Итого за период» жирным целиком;
  * форматы, выравнивание и ширины колонок в пикселях; окно времени в имени листа; пустой период;
  * ручка: накопительный — с 1-го числа месяца по последний выбранный день, первый лист — за период.
Данные выдуманные: репозиторий публичный.
"""

import ast
import copy
import logging
import math
import re
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from io import BytesIO
from types import SimpleNamespace

import xlsxwriter
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from resource_fte.chat import CHAT_BILLING_METRICS, attach_chat_billing_plan, get_chat_billing_report
from tests import source_cache

BOT_PATH = source_cache.monolith_flat_path()

FUNCTIONS = (
    "_chat_billing_round_half_up", "_chat_billing_minutes", "_chat_billing_minutes_label",
    "_chat_billing_export_ratio", "_chat_billing_day_comment", "_chat_billing_daily_values",
    "_chat_billing_daily_visible_day", "_chat_billing_daily_workbook",
    "_chat_billing_cumulative_values", "_chat_billing_cumulative_sheet_title",
    "_chat_billing_cumulative_sheet", "api_resource_fte_chat_billing_export",
)
CONSTS = (
    "_CHAT_BILLING_MONTHS_RU", "_CHAT_BILLING_DAILY_COLUMNS", "_CHAT_BILLING_DAILY_DAY_KEYS",
    "_CHAT_BILLING_DAILY_BASE_FMT", "_CHAT_BILLING_DAILY_RED_HEADER_KEYS", "_CHAT_BILLING_DAILY_HIDDEN_PARKS",
    "_CHAT_BILLING_CUMULATIVE_COLUMNS", "_CHAT_BILLING_CUMULATIVE_SHEET", "_CHAT_BILLING_CUMULATIVE_HEADER_FILL",
)
# Что функции берут из модуля — настоящие объекты под ТЕМИ ЖЕ именами; тест ниже сверяет, что модуль
# их так и называет (иначе подстановка спрятала бы NameError выгрузки).
MODULE_NAMES = {
    "math": math, "re": re, "datetime": datetime, "BytesIO": BytesIO, "logging": logging,
    "xlsxwriter": xlsxwriter, "get_column_letter": get_column_letter,
    "CHAT_BILLING_METRICS": CHAT_BILLING_METRICS,
}
# Ширины колонок второго листа в пикселях: «Поступило», «Обслужено» и «SL» — образца, остальные —
# по тексту подписей и названий парков чата.
PIXELS = [130, 75, 81, 75, 52, 226, 230]
HEADER = ["Таксопарк", "Поступило", "Обслужено", "Без ответа", "SL",
          "Ср. реакция на 1 сообщение, мин", "Ср. время ответа внутри чата, мин"]


def _namespace():
    module = source_cache.parse(BOT_PATH.read_text(encoding="utf-8-sig"))
    body = []
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            node = copy.deepcopy(node)  # дерево общее на весь набор (tests/source_cache.py)
            node.decorator_list = []    # @app.route и @require_api_key — Flask здесь не нужен
            body.append(node)
        elif isinstance(node, ast.Assign) and any(getattr(t, "id", None) in CONSTS for t in node.targets):
            body.append(node)
    namespace = dict(MODULE_NAMES)
    exec(compile(ast.Module(body=body, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    missing = [name for name in FUNCTIONS + CONSTS if name not in namespace]
    if missing:
        raise AssertionError("не найдено в bot_schedule2.py: %s" % missing)
    return namespace


class _RowsDb:
    """Отдаёт заданные строки c2d_requests настоящему get_chat_billing_report."""

    def __init__(self, rows):
        self.rows = rows

    @contextmanager
    def _get_cursor(self):
        rows = self.rows

        class _Cursor:
            def execute(self, sql, params=None):
                pass

            def fetchall(self):
                return list(rows)

        yield _Cursor()


def _row(day, park, chats, answered, answered_sl, reply_sum, inner_sum, inner_replied):
    return (day, park, "", chats, answered, answered_sl, reply_sum, inner_sum, inner_replied)


NOV_13, NOV_14 = date(2026, 11, 13), date(2026, 11, 14)
ROWS = [
    # iTaxi за два дня: 300 чатов, 150 отвечены (150 без ответа), 120 в цель. Реакция ровно 135 с на
    # отвеченный = 2,25 мин → 2,3 (банковское дало бы 2,2); ответ внутри чата — 150 с = 2,5 мин.
    _row(NOV_13, "Техподдержка iTaxi", 200, 100, 80, 100 * 135.0, 93 * 150.0, 93),
    _row(NOV_14, "Техподдержка iTaxi", 100, 50, 40, 50 * 135.0, 47 * 150.0, 47),
    # Jana первая по алфавиту и по отвеченным — порядок «iTaxi, Jana» даёт только сортировка по чатам.
    _row(NOV_13, "Jana Taxi", 200, 199, 190, 199 * 60.0, 199 * 180.0, 199),
    # Global, как и на первом листе, в файл не идёт.
    _row(NOV_13, "Global", 10, 10, 10, 600.0, 600.0, 10),
    # Без отвеченных: ни реакции, ни ответа внутри чата — прочерки, а не нули.
    _row(NOV_14, "Тест-парк", 3, 0, 0, 0.0, 0.0, 0),
    # Имя с «=» — текст, а не формула.
    _row(NOV_14, "=Парк", 1, 1, 1, 60.0, 0.0, 0),
]


def _report(rows=ROWS, start=NOV_13, end=NOV_14):
    return get_chat_billing_report(_RowsDb(rows), start, end)


PARAMS = {"start_day": NOV_13, "end_day": NOV_14, "minute_from": 0, "minute_to": 1439, "sl_seconds": 60}


class ChatCumulativeSheetTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ns = _namespace()
        daily = attach_chat_billing_plan(_report(), {"2026-11-13": 1000}, {})
        build = cls.ns["_chat_billing_daily_workbook"]
        cls.plain = build(PARAMS, daily).getvalue()
        cls.content = build(PARAMS, daily, cumulative=_report()).getvalue()
        cls.book = load_workbook(BytesIO(cls.content))
        cls.ws = cls.book["Накопительный итог"]

    def row(self, number):
        return [self.ws.cell(number, column).value for column in range(1, 8)]

    def test_injected_names_are_the_modules_own(self):
        module = source_cache.parse(BOT_PATH.read_text(encoding="utf-8-sig"))
        bound = set()
        for node in module.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                bound.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
            elif isinstance(node, ast.Assign):
                bound.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                bound.add(node.name)
        self.assertEqual(sorted(set(MODULE_NAMES) - bound), [])

    def test_first_sheet_is_the_usual_daily_report(self):
        self.assertEqual(self.book.sheetnames, ["Ноябрь 2026", "Накопительный итог"])
        plain = load_workbook(BytesIO(self.plain))
        self.assertEqual(plain.sheetnames, ["Ноябрь 2026"])
        before, after = plain["Ноябрь 2026"], self.book["Ноябрь 2026"]
        self.assertEqual(before.max_row, after.max_row)
        for row_before, row_after in zip(before.iter_rows(), after.iter_rows()):
            self.assertEqual([(c.value, c.number_format) for c in row_before],
                             [(c.value, c.number_format) for c in row_after])
        self.assertEqual(sorted(map(str, before.merged_cells.ranges)), sorted(map(str, after.merged_cells.ranges)))

    def test_header_is_the_samples(self):
        self.assertEqual(self.row(1), HEADER)
        for column in range(1, 8):
            cell = self.ws.cell(1, column)
            self.assertEqual(cell.fill.fgColor.rgb[-6:], "1F4E78")
            self.assertEqual((cell.font.color.rgb[-6:], cell.font.b), ("FFFFFF", True))
            self.assertEqual((cell.font.name, cell.font.sz), ("Calibri", 11))
            self.assertEqual((cell.alignment.horizontal, cell.alignment.vertical), ("center", "center"))
            self.assertIsNone(cell.border.left.style)  # рамок нет — в образце только сетка

    def test_parks_by_chats_without_global(self):
        self.assertEqual([self.ws.cell(row, 1).value for row in range(2, 7)],
                         ["Техподдержка iTaxi", "Jana Taxi", "Тест-парк", "=Парк", "Итого за период"])
        self.assertEqual(self.ws.max_row, 6)
        self.assertEqual(self.ws.cell(5, 1).data_type, "s")

    def test_park_values(self):
        itaxi, jana, empty, equals = self.row(2), self.row(3), self.row(4), self.row(5)
        self.assertEqual(itaxi[:4], ["Техподдержка iTaxi", 300, 150, 150])
        self.assertAlmostEqual(itaxi[4], 120 / 300)  # SL — от поступивших
        self.assertEqual(itaxi[5:], [2.3, 2.5])
        self.assertEqual(jana[1:4], [200, 199, 1])
        self.assertEqual(jana[5:], [1.0, 3.0])
        self.assertEqual(empty[1:], [3, 0, 3, 0, "—", "—"])
        self.assertEqual(equals[1:], [1, 1, 0, 1, 1.0, "—"])

    def test_total_is_from_sums_of_visible_rows(self):
        total = self.row(6)
        # Без Global: 504 чата, а не 514.
        self.assertEqual(total[:4], ["Итого за период", 504, 350, 154])
        self.assertAlmostEqual(total[4], 311 / 504)
        # Реакция — 32 250 с на 350 отвеченных = 1,5 мин; среднее строк дало бы 1,4, а с весом
        # отвеченных — 1,6. Ответ внутри чата — 56 820 с на 339 = 2,8 мин.
        self.assertEqual(total[5:], [1.5, 2.8])
        for column in range(1, 8):
            cell = self.ws.cell(6, column)
            self.assertTrue(cell.font.b)
            self.assertEqual((cell.font.name, cell.font.sz), ("Calibri", 11))
        self.assertFalse(self.ws.cell(2, 2).font.b)

    def test_formats_alignment_and_widths(self):
        for row in (2, 6):
            self.assertEqual([self.ws.cell(row, column).number_format for column in range(2, 8)],
                             ["#,##0", "#,##0", "#,##0", "0%", "General", "General"])
            self.assertIsNone(self.ws.cell(row, 1).alignment.horizontal)  # парк — слева, как в образце
            for column in range(2, 8):
                cell = self.ws.cell(row, column)
                self.assertEqual((cell.alignment.horizontal, cell.alignment.vertical), ("center", "center"))
        # Прочерк — тоже по центру.
        self.assertEqual(self.ws.cell(4, 6).alignment.horizontal, "center")
        widths = [self.ws.column_dimensions[get_column_letter(column)].width for column in range(1, 8)]
        # Excel переводит ширину файла в пиксели так (ECMA-376, 18.3.1.13; цифра Calibri 11 — 7 пикселей).
        self.assertEqual([int(((256 * width + int(128 / 7)) / 256) * 7) for width in widths], PIXELS)

    def test_values_are_numbers_not_formulas(self):
        sheet = load_workbook(BytesIO(self.content))["Накопительный итог"]
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                self.assertNotEqual(cell.data_type, "f")

    def test_sheet_title_carries_a_partial_time_window(self):
        title = self.ns["_chat_billing_cumulative_sheet_title"]
        self.assertEqual(title(0, 1439), "Накопительный итог")
        self.assertEqual(title(480, 1200), "Накопительный итог 08.00-20.00")
        self.assertLessEqual(len(title(510, 1215)), 31)  # предел имени листа в Excel
        daily = attach_chat_billing_plan(_report(), {}, {})
        content = self.ns["_chat_billing_daily_workbook"](
            {**PARAMS, "minute_from": 480, "minute_to": 1200}, daily, cumulative=_report()).getvalue()
        self.assertEqual(load_workbook(BytesIO(content)).sheetnames, ["Ноябрь 2026", "Накопительный итог 08.00-20.00"])

    def test_empty_period(self):
        empty = {"days": [], "parks": [], "totals": {}}
        content = self.ns["_chat_billing_daily_workbook"](PARAMS, empty, cumulative=empty).getvalue()
        book = load_workbook(BytesIO(content))
        self.assertEqual(book.sheetnames, ["Ежедневный отчет", "Накопительный итог"])
        ws = book["Накопительный итог"]
        self.assertEqual([ws.cell(1, column).value for column in range(1, 8)], HEADER)
        self.assertEqual(ws["A2"].value, "За выбранный период и окно времени обращений не нашлось")
        # Только Global — тоже пусто: в файл он не идёт.
        only_global = _report([_row(NOV_13, "Global", 5, 5, 5, 300.0, 300.0, 5)])
        content = self.ns["_chat_billing_daily_workbook"](PARAMS, empty, cumulative=only_global).getvalue()
        self.assertEqual(load_workbook(BytesIO(content))["Накопительный итог"]["A2"].value,
                         "За выбранный период и окно времени обращений не нашлось")


class ChatCumulativeRouteTest(unittest.TestCase):

    def setUp(self):
        self.ns = _namespace()
        self.calls = []
        self.period = (date(2026, 11, 13), date(2026, 11, 19))

        def daily(db, start, end, **window):
            self.calls.append(("daily", start, end, window))
            return attach_chat_billing_plan(_report(), {}, {})

        def report(db, start, end, **window):
            self.calls.append(("report", start, end, window))
            return _report()

        def operators(db, start, end, **window):
            self.calls.append(("operators", start, end, window))
            return {"days": [], "operators": [], "totals": {}}

        self.ns.update(
            _resource_fte_route_guard=lambda: (1, None, None),
            _chat_billing_parse_request_args=lambda: (
                {"start_day": self.period[0], "end_day": self.period[1], "minute_from": 0,
                 "minute_to": 1439, "sl_seconds": 60}, None, None),
            get_chat_billing_daily=daily,
            get_chat_billing_report=report,
            get_chat_billing_operators=operators,
            _chat_billing_export_workbook=lambda *args: _EmptyBook(),
            db=object(),
            jsonify=lambda body: body,
            send_file=lambda output, **kwargs: ("файл", kwargs["download_name"], output),
        )

    def _call(self, **args):
        self.ns["request"] = SimpleNamespace(method="GET", args=args)
        return self.ns["api_resource_fte_chat_billing_export"]()

    def test_park_export_adds_the_month_to_date_sheet(self):
        kind, name, output = self._call(mode="park")
        window = {"minute_from": 0, "minute_to": 1439, "sl_seconds": 60}
        # Первый лист — за выбранный период, накопительный — с 1-го числа месяца последнего дня.
        self.assertEqual(self.calls, [("report", date(2026, 11, 1), date(2026, 11, 19), window),
                                      ("daily", date(2026, 11, 13), date(2026, 11, 19), window)])
        self.assertEqual(name, "chat_billing_park_2026-11-13_2026-11-19.xlsx")
        book = load_workbook(output)
        self.assertEqual(book.sheetnames, ["Ноябрь 2026", "Накопительный итог"])
        self.assertEqual(book["Накопительный итог"]["A6"].value, "Итого за период")

    def test_month_is_the_last_days(self):
        self.period = (date(2026, 10, 25), date(2026, 11, 5))
        self._call(mode="park")
        self.assertEqual(self.calls[0][:3], ("report", date(2026, 11, 1), date(2026, 11, 5)))
        self.assertEqual(self.calls[1][:3], ("daily", date(2026, 10, 25), date(2026, 11, 5)))

    def test_other_modes_do_not_build_it(self):
        self._call(mode="operator")
        self.assertEqual([call[0] for call in self.calls], ["operators"])


class _EmptyBook:
    def save(self, output):
        output.write(b"book")


if __name__ == "__main__":
    unittest.main()
