# -*- coding: utf-8 -*-
"""«Биллинг Oktell → Таксопарки» с накопительным итогом (образец СЗоВ, 19.11.2026).

Владелец прислал таблицу СЗоВ за 01–18.11.2026 и попросил выгрузку «именно в этом формате».
Что сторожится настоящими функциями bot_schedule2 (через ast), подменены только Oktell и Flask:
  * девять колонок образца с его подписями, шапка — тёмно-синяя, белый жирный Calibri 11, по центру;
  * парки — по убыванию поступивших, подписи как на экране; «Итого за период» жирным;
  * AR и SL — от поступивших, разговор — усечение разговора принятых (не всего звонка), оценка — до
    сотых с половиной вверх;
  * итог — из сумм периода: ни среднее строк, ни среднее строк с весами не годятся;
  * форматы, ширины образца в пикселях и выравнивание; имя парка всегда текст; пустой период;
  * имя листа — период, при окне времени не на все сутки — и окно;
  * ручка: формат только у «Таксопарков», данные — с 1-го числа месяца по последний выбранный день
    (решение владельца 19.11.2026), свой файл; общая выгрузка по-прежнему за выбранный период.
Данные выдуманные: репозиторий публичный, таблица СЗоВ сюда не кладётся.
"""

import ast
import copy
import logging
import re
import unittest
from datetime import date, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from tests import source_cache

BOT_PATH = source_cache.monolith_flat_path()

FUNCTIONS = (
    "_oktell_billing_int", "_oktell_billing_fraction_sec", "_oktell_billing_metrics_row",
    "_oktell_billing_build_report", "_oktell_billing_line_key", "_oktell_billing_ratio",
    "_oktell_billing_szov_att", "_oktell_billing_szov_rating", "_oktell_billing_park_label",
    "_oktell_billing_cumulative_values", "_oktell_billing_cumulative_sheet_title",
    "_oktell_billing_cumulative_workbook", "api_resource_fte_oktell_billing_export",
)
CONSTS = (
    "_OKTELL_BILLING_METRICS", "_OKTELL_BILLING_PARK_LABELS", "_OKTELL_BILLING_LINE_LABELS",
    "_OKTELL_BILLING_LINE_TAIL_MIN", "_OKTELL_BILLING_CUMULATIVE_COLUMNS",
)
# Что функции берут из модуля — настоящие объекты под ТЕМИ ЖЕ именами; тест ниже сверяет, что модуль
# их так и называет (иначе подстановка спрятала бы NameError выгрузки).
MODULE_NAMES = {
    "re": re, "datetime": datetime, "timedelta": timedelta, "BytesIO": BytesIO, "logging": logging,
    "Workbook": Workbook, "Font": Font, "PatternFill": PatternFill, "Alignment": Alignment,
    "get_column_letter": get_column_letter,
}

DAY_1, DAY_2 = "2026-11-01", "2026-11-02"
# Ширины колонок образца в пикселях (замер по сетке картинки при 100 %).
SAMPLE_PIXELS = [113, 75, 81, 69, 52, 52, 116, 87, 76]


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


def _raw(day, park, arrived, served, served_sl, talk, greet_drop=0, rating_sum=0, rating_count=0):
    # Звонок целиком (total_seconds) длиннее разговора принятых: в нём ещё и потерянные, по 40 с.
    return {"report_date": day, "taxi_park": park, "arrived": arrived, "served": served,
            "served_sl": served_sl, "greet_drop": greet_drop, "talk_seconds": talk,
            "wait_ok_seconds": 0, "wait_lost_seconds": 0, "total_seconds": talk + 40 * (arrived - served),
            "rating_sum": rating_sum, "rating_count": rating_count}


RAW_ROWS = [
    # iTaxi за два дня: 350 + 150 поступивших, 260 + 120 принятых, 120 потерь. Разговор 380 × 250 + 379,9 с —
    # 250 усечением (округление дало бы 251). Оценки 74 / 16 = 4,625 → 4,63 (банковское дало бы 4,62).
    _raw(DAY_1, "iTaxi", 350, 260, 240, 260 * 250 + 300.0, greet_drop=20, rating_sum=60, rating_count=13),
    _raw(DAY_2, "iTaxi", 150, 120, 115, 120 * 250 + 79.9, greet_drop=5, rating_sum=14, rating_count=3),
    # У Oktell «Jana», на экране и в образце — «Jana такси». Она первая по алфавиту и по принятым, iTaxi —
    # по потерям; порядок строк «iTaxi, Jana, Халык-тест» даёт только сортировка по поступившим.
    _raw(DAY_1, "Jana", 400, 398, 390, 398 * 200 + 397, greet_drop=7, rating_sum=2, rating_count=1),
    # Все звонки потеряны: ни разговора, ни оценки — прочерки, а не нули.
    _raw(DAY_2, "Халык-тест", 3, 0, 0, 0, greet_drop=1),
]


def _book(ns, rows=RAW_ROWS, start=date(2026, 11, 1), end=date(2026, 11, 2), minute_from=0, minute_to=1439):
    report = ns["_oktell_billing_build_report"](rows)
    content = ns["_oktell_billing_cumulative_workbook"](report, start, end, minute_from, minute_to)
    return content.getvalue()


class CumulativeWorkbookTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ns = _namespace()
        cls.ws = load_workbook(BytesIO(_book(cls.ns))).active

    def row(self, number):
        return [self.ws.cell(number, column).value for column in range(1, 10)]

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

    def test_header_is_the_samples(self):
        self.assertEqual(self.row(1), [
            "Таксопарк", "Поступило", "Обслужено", "Потеряно", "AR", "SL", "Ср. разговор, сек",
            "Сброс на IVR", "Ср. оценка"])
        for column in range(1, 10):
            cell = self.ws.cell(1, column)
            self.assertEqual(cell.fill.fgColor.rgb, "001F4E78")
            self.assertEqual((cell.font.color.rgb, cell.font.b), ("00FFFFFF", True))
            self.assertEqual((cell.font.name, cell.font.sz), ("Calibri", 11))
            self.assertEqual((cell.alignment.horizontal, cell.alignment.vertical), ("center", "center"))
        self.assertEqual(self.ws.title, "01.11.2026-02.11.2026")

    def test_parks_by_arrivals_with_screen_labels(self):
        self.assertEqual([self.ws.cell(row, 1).value for row in range(2, 6)],
                         ["iTaxi", "Jana такси", "Халык-тест", "Итого за период"])
        self.assertEqual(self.ws.max_row, 5)

    def test_park_values(self):
        itaxi, jana, lost_only = self.row(2), self.row(3), self.row(4)
        self.assertEqual(itaxi[:4], ["iTaxi", 500, 380, 120])
        self.assertAlmostEqual(itaxi[4], 120 / 500)   # AR — от поступивших
        self.assertAlmostEqual(itaxi[5], 355 / 500)   # SL — тоже от поступивших
        # Разговор — разговор принятых, не весь звонок (с ним было бы 263), и усечением (не 251).
        self.assertEqual(itaxi[6:], [250, 25, 4.63])
        self.assertEqual(jana[1:4], [400, 398, 2])
        self.assertEqual(jana[6:], [200, 7, 2.0])
        self.assertEqual(lost_only[1:4], [3, 0, 3])
        self.assertAlmostEqual(lost_only[4], 1.0)
        self.assertEqual(lost_only[5:], [0, "—", 1, "—"])

    def test_total_is_from_period_sums(self):
        total = self.row(5)
        self.assertEqual(total[:4], ["Итого за период", 903, 778, 125])
        self.assertAlmostEqual(total[4], 125 / 903)
        self.assertAlmostEqual(total[5], 745 / 903)
        # Разговор — сумма периода / принятые: 175 376,9 / 778 = 225,4 → 225. Среднее ATT парков,
        # взвешенное по принятым (так считает итог книги СЗоВ), дало бы 224.
        self.assertEqual(total[6], 225)
        self.assertEqual(total[7], 33)
        # Оценка — 76 / 17 = 4,47. Среднее оценок парков дало бы 3,32, а то же с весом числа оценок — 4,48.
        self.assertEqual(total[8], 4.47)
        for column in range(1, 10):
            cell = self.ws.cell(5, column)
            self.assertTrue(cell.font.b)
            self.assertEqual((cell.font.name, cell.font.sz), ("Calibri", 11))
        self.assertFalse(self.ws.cell(4, 2).font.b)

    def test_formats_widths_and_alignment(self):
        expected = ["#,##0", "#,##0", "#,##0", "0%", "0%", "0", "0", "0.00"]
        for row in (2, 5):
            self.assertEqual([self.ws.cell(row, column).number_format for column in range(2, 10)], expected)
        widths = [self.ws.column_dimensions[get_column_letter(column)].width for column in range(1, 10)]
        self.assertEqual(widths, [16.14, 10.71, 11.57, 9.86, 7.43, 7.43, 16.57, 12.43, 10.86])
        # Excel переводит ширину файла в пиксели так (ECMA-376, 18.3.1.13; цифра Calibri 11 — 7 пикселей),
        # и выходят ровно колонки образца.
        self.assertEqual([int(((256 * width + int(128 / 7)) / 256) * 7) for width in widths], SAMPLE_PIXELS)
        for row in (2, 5):
            self.assertIsNone(self.ws.cell(row, 1).alignment.horizontal)  # парк — слева, как в образце
            for column in range(2, 10):
                cell = self.ws.cell(row, column)
                self.assertEqual((cell.alignment.horizontal, cell.alignment.vertical), ("center", "center"))
        # У прочерка числового формата нет — это текст.
        self.assertEqual(self.ws.cell(4, 9).number_format, "General")

    def test_values_are_numbers_not_formulas(self):
        for row in range(2, 6):
            for value in self.row(row)[1:]:
                self.assertFalse(isinstance(value, str) and value.startswith("="))

    def test_park_name_is_always_text(self):
        rows = [_raw(DAY_1, "=Такси", 5, 5, 5, 500), _raw(DAY_1, '=HYPERLINK("http://example.invalid","Парк")', 1, 1, 1, 60)]
        content = _book(self.ns, rows)
        ws = load_workbook(BytesIO(content)).active
        self.assertEqual([ws.cell(row, 1).value for row in (2, 3)],
                         ["=Такси", '=HYPERLINK("http://example.invalid","Парк")'])
        self.assertEqual([ws.cell(row, 1).data_type for row in (2, 3)], ["s", "s"])
        import zipfile
        sheet = zipfile.ZipFile(BytesIO(content)).read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertNotIn("<f>", sheet)

    def test_empty_period(self):
        build = self.ns["_oktell_billing_cumulative_workbook"]
        ws = load_workbook(BytesIO(build({"days": [], "parks": [], "totals": {}},
                                         date(2026, 10, 31), date(2026, 11, 1)).getvalue())).active
        self.assertEqual(ws.title, "31.10.2026-01.11.2026")
        self.assertEqual(ws["A1"].value, "Таксопарк")
        self.assertEqual(ws["A2"].value, "За выбранный период и окно времени звонков не нашлось")
        self.assertEqual(ws.max_row, 2)

    def test_sheet_title_carries_a_partial_time_window(self):
        title = self.ns["_oktell_billing_cumulative_sheet_title"]
        cases = {
            (date(2026, 11, 1), date(2026, 11, 18), 0, 1439): "01.11.2026-18.11.2026",
            (date(2026, 11, 1), date(2026, 11, 18), 480, 1200): "01.11-18.11.2026 08.00-20.00",
            (date(2025, 12, 31), date(2026, 1, 1), 510, 1215): "31.12.25-01.01.26 08.30-20.15",
            (date(2025, 12, 31), date(2026, 1, 1), 0, 1439): "31.12.2025-01.01.2026",
        }
        for args, expected in cases.items():
            self.assertEqual(title(*args), expected)
            self.assertLessEqual(len(expected), 31)  # предел имени листа в Excel
        ws = load_workbook(BytesIO(_book(self.ns, minute_from=480, minute_to=1200))).active
        self.assertEqual(ws.title, "01.11-02.11.2026 08.00-20.00")


class CumulativeRouteTest(unittest.TestCase):

    def setUp(self):
        self.ns = _namespace()
        self.fetched = []
        self.period = (date(2026, 11, 1), date(2026, 11, 2))
        self.window = (0, 1439)

        def fetch(start, end, minute_from, minute_to, sl_seconds, group_by):
            self.fetched.append((start, end, minute_from, minute_to, group_by))
            return RAW_ROWS

        self.ns.update(
            _resource_fte_route_guard=lambda: (1, None, None),
            _oktell_billing_parse_request_args=lambda: (
                {"start_day": self.period[0], "end_day": self.period[1], "minute_from": self.window[0],
                 "minute_to": self.window[1]}, None, None),
            _oktell_billing_parse_direction=lambda: ("incoming", None),
            _oktell_billing_parse_sl_seconds=lambda: (20, None),
            _oktell_fetch_billing_rows=fetch,
            _oktell_billing_export_workbook=lambda *args: BytesIO(b"general"),
            jsonify=lambda body: body,
            send_file=lambda output, **kwargs: ("файл", kwargs["download_name"], output),
        )

    def _call(self, **args):
        self.ns["request"] = SimpleNamespace(method="GET", args=args)
        return self.ns["api_resource_fte_oktell_billing_export"]()

    def test_cumulative_runs_from_the_first_of_the_month(self):
        # На экране 13–19.11 — в файле 01–19.11: начало периода с экрана не действует.
        self.period = (date(2026, 11, 13), date(2026, 11, 19))
        kind, name, output = self._call(report_type="cumulative", mode="park")
        self.assertEqual(self.fetched, [(date(2026, 11, 1), date(2026, 11, 19), 0, 1439, "park")])
        self.assertEqual(name, "oktell_billing_park_cumulative_2026-11-01_2026-11-19.xlsx")
        ws = load_workbook(output).active
        self.assertEqual(ws.title, "01.11.2026-19.11.2026")
        self.assertEqual((ws["A1"].value, ws["A5"].value, ws["B5"].value), ("Таксопарк", "Итого за период", 903))

    def test_cumulative_month_is_the_last_days(self):
        # Период через границу месяцев — месяц последнего дня, с его 1-го числа.
        self.period = (date(2026, 10, 25), date(2026, 11, 5))
        kind, name, output = self._call(report_type="cumulative", mode="park")
        self.assertEqual(self.fetched, [(date(2026, 11, 1), date(2026, 11, 5), 0, 1439, "park")])
        self.assertEqual(name, "oktell_billing_park_cumulative_2026-11-01_2026-11-05.xlsx")
        self.period = (date(2026, 12, 1), date(2026, 12, 1))
        self._call(report_type="cumulative", mode="park")
        self.assertEqual(self.fetched[-1][:2], (date(2026, 12, 1), date(2026, 12, 1)))

    def test_time_window_reaches_data_and_sheet(self):
        self.window = (480, 1200)
        kind, name, output = self._call(report_type="cumulative", mode="park")
        self.assertEqual(self.fetched, [(date(2026, 11, 1), date(2026, 11, 2), 480, 1200, "park")])
        self.assertEqual(load_workbook(output).active.title, "01.11-02.11.2026 08.00-20.00")

    def test_general_park_export_keeps_the_selected_period(self):
        self.period = (date(2026, 11, 13), date(2026, 11, 19))
        kind, name, output = self._call(report_type="general", mode="park")
        self.assertEqual(self.fetched, [(date(2026, 11, 13), date(2026, 11, 19), 0, 1439, "park")])
        self.assertEqual((name, output.getvalue()), ("oktell_billing_park_2026-11-13_2026-11-19.xlsx", b"general"))

    def test_cumulative_only_for_parks(self):
        for mode in ("line", "operator", "detail", "grouping"):
            body, status = self._call(report_type="cumulative", mode=mode)
            self.assertEqual(status, 400)
            self.assertEqual(body["error"], "Накопительный итог есть только у «Таксопарков»")
        self.assertEqual(self.fetched, [])
        body, status = self._call(report_type="накопительный", mode="park")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
