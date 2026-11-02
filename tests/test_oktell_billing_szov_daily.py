# -*- coding: utf-8 -*-
"""«Биллинг Oktell → Таксопарки» в формате ежедневного отчёта СЗоВ (задача #380, п. 4).

Постановщик просил второй формат выгрузки «Таксопарков» — «точно как их таблица», прежний
оставить. Что сторожится настоящими функциями bot_schedule2 (через ast), подменены только Oktell
и Flask:
  * состав и порядок парков файла, имя парка с долей — как в файле, лишние парки — в конце;
  * план = доля × поступившие того же дня неделей раньше; нет той недели — нет плана;
  * ATT — усечение, оценка — до сотых с половиной вверх (как пишет образец);
  * книга: подписи, объединения дня, формулы файла с посчитанными значениями, заливки, форматы,
    условное форматирование, блок за период через три строки;
  * ручка: формат только у «Таксопарков», данные — с запасом в неделю назад.
Данные выдуманные: репозиторий публичный, образец СЗоВ сюда не кладётся.
"""

import ast
import copy
import logging
import re
import unittest
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import xlsxwriter
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from tests import source_cache

BOT_PATH = source_cache.monolith_flat_path()

FUNCTIONS = (
    "_oktell_billing_int", "_oktell_billing_fraction_sec", "_oktell_billing_metrics_row",
    "_oktell_billing_build_report", "_oktell_billing_line_key", "_oktell_billing_szov_rating",
    "_oktell_billing_szov_att", "_oktell_billing_szov_daily", "_oktell_billing_szov_daily_workbook",
    "api_resource_fte_oktell_billing_export",
)
CONSTS = (
    "OKTELL_BILLING_SZOV_PARK_SHARES", "OKTELL_BILLING_SZOV_PLAN_BASE_DAYS_BACK", "_OKTELL_BILLING_SZOV_COLUMNS",
    "_OKTELL_BILLING_SZOV_PERIOD_SUM_FORMATS", "_OKTELL_BILLING_SZOV_DAY_KEYS", "_OKTELL_BILLING_SZOV_TOTAL_FILL",
    "_OKTELL_BILLING_SZOV_RATING_TOTAL_FILL", "_OKTELL_BILLING_SZOV_METRICS", "_OKTELL_BILLING_METRICS",
    "_CHAT_BILLING_MONTHS_RU", "_OKTELL_BILLING_LINE_TAIL_MIN", "_OKTELL_BILLING_LINE_LABELS",
)
# Что функции берут из модуля. Подставляются настоящие объекты под ТЕМИ ЖЕ именами, а тест ниже
# сверяет, что модуль их так и называет: подставленный `date` спрятал бы NameError выгрузки
# (в модуле он импортирован как dt_date).
MODULE_NAMES = {
    "re": re, "datetime": datetime, "timedelta": timedelta, "dt_date": date, "BytesIO": BytesIO,
    "xlsxwriter": xlsxwriter, "get_column_letter": get_column_letter, "logging": logging,
}

OCT_1, OCT_2 = "2026-10-01", "2026-10-02"
WEEK_BEFORE = "2026-09-24"


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


def _raw(day, park, arrived, served, talk=0.0, rating_sum=0, rating_count=0):
    return {"report_date": day, "taxi_park": park, "arrived": arrived, "served": served, "served_sl": served,
            "greet_drop": 0, "talk_seconds": talk, "wait_ok_seconds": 0, "wait_lost_seconds": 0,
            "total_seconds": talk, "rating_sum": rating_sum, "rating_count": rating_count}


RAW_ROWS = [
    # Неделей раньше 01.10 — база его плана: 100 + 20 поступивших.
    _raw(WEEK_BEFORE, "iTaxi", 100, 95, 25000),
    _raw(WEEK_BEFORE, "Новый парк", 20, 20, 4000),
    _raw(OCT_1, "iTaxi", 50, 45, 45 * 250 + 10, rating_sum=74, rating_count=16),  # 4,625 → 4,63
    # У Oktell «Бизнес партнер», в файле — «Бизнес Партнер».
    _raw(OCT_1, "Бизнес партнер", 4, 4, 800, rating_sum=5, rating_count=1),
    _raw(OCT_1, "Новый парк", 3, 2, 300),
    # Неделей раньше 02.10 звонков нет — плана на 02.10 нет.
    _raw(OCT_2, "iTaxi", 10, 10, 2000),
]

SHARE = dict((name, share) for name, share in _namespace()["OKTELL_BILLING_SZOV_PARK_SHARES"])


class SzovDailyDataTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ns = _namespace()

    def _daily(self, start=date(2026, 10, 1), end=date(2026, 10, 2)):
        report = self.ns["_oktell_billing_build_report"](RAW_ROWS)
        return self.ns["_oktell_billing_szov_daily"](report, start, end)

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

    def test_rating_is_rounded_half_up_to_hundredths(self):
        rating = self.ns["_oktell_billing_szov_rating"]
        self.assertEqual(rating(74, 16), 4.63)   # банковское округление дало бы 4,62
        self.assertEqual(rating(81, 17), 4.76)
        self.assertEqual(rating(5, 1), 5.0)
        self.assertIsNone(rating(0, 0))

    def test_att_is_truncated(self):
        att = self.ns["_oktell_billing_szov_att"]
        self.assertEqual(att({"served": 45, "talk_seconds": 45 * 250 + 44.9}), 250)
        self.assertIsNone(att({"served": 0, "talk_seconds": 10}))

    def test_parks_follow_the_file_and_unknown_ones_go_last(self):
        daily = self._daily()
        names = [item["park"] for item in daily["days"][0]["parks"]]
        self.assertEqual(names[:20], [name for name, _share in self.ns["OKTELL_BILLING_SZOV_PARK_SHARES"]])
        self.assertEqual(names[20:], ["Новый парк"])
        self.assertEqual([[item["park"] for item in day["parks"]] for day in daily["days"]], [names, names])
        partner = next(item for item in daily["days"][0]["parks"] if item["park"] == "Бизнес Партнер")
        self.assertEqual((partner["arrived"], partner["share"]), (4, SHARE["Бизнес Партнер"]))

    def test_plan_is_share_times_last_week_arrivals(self):
        daily = self._daily()
        first, second = daily["days"]
        self.assertEqual([day["date"] for day in daily["days"]], [date(2026, 10, 1), date(2026, 10, 2)])
        self.assertEqual(first["base"], 120)
        itaxi = first["parks"][0]
        self.assertAlmostEqual(itaxi["plan"], SHARE["iTaxi"] * 120)
        self.assertIsNone(first["parks"][-1]["plan"])  # у парка без доли плана нет
        self.assertIsNone(second["base"])
        self.assertTrue(all(item["plan"] is None for item in second["parks"]))
        self.assertEqual(daily["base"], 120)

    def test_period_base_is_the_sum_of_day_bases(self):
        rows = RAW_ROWS + [_raw("2026-09-26", "iTaxi", 30, 30, 6000), _raw("2026-10-03", "iTaxi", 5, 5, 1000)]
        report = self.ns["_oktell_billing_build_report"](rows)
        daily = self.ns["_oktell_billing_szov_daily"](report, date(2026, 10, 1), date(2026, 10, 3))
        self.assertEqual([day["base"] for day in daily["days"]], [120, None, 30])
        self.assertEqual(daily["base"], 150)  # план периода парка = доля × сумма баз дней
        itaxi = daily["parks"][0]
        self.assertAlmostEqual(itaxi["plan"], SHARE["iTaxi"] * 150)

    def test_period_sums_days_of_the_period_only(self):
        period = {item["park"]: item for item in self._daily()["parks"]}
        self.assertEqual((period["iTaxi"]["arrived"], period["iTaxi"]["served"]), (60, 55))
        self.assertAlmostEqual(period["iTaxi"]["plan"], SHARE["iTaxi"] * 120)
        self.assertEqual(period["Новый парк"]["arrived"], 3)  # 20 звонков 24.09 — не период
        self.assertIsNone(period["Новый парк"]["plan"])


class SzovDailyWorkbookTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ns = _namespace()
        report = cls.ns["_oktell_billing_build_report"](RAW_ROWS)
        daily = cls.ns["_oktell_billing_szov_daily"](report, date(2026, 10, 1), date(2026, 10, 2))
        content = cls.ns["_oktell_billing_szov_daily_workbook"](daily).getvalue()
        cls.formulas = load_workbook(BytesIO(content)).active
        cls.values = load_workbook(BytesIO(content), data_only=True).active

    def test_header_is_the_files(self):
        ws = self.formulas
        self.assertEqual([ws.cell(1, column).value for column in range(1, 15)], [
            "Дата", "Название очереди", "Нужно удалить", "План (звонки)", "факт (звонки)",
            "% совпадения прогноза", "ATT, сек", "принято", "% упущенных",
            "Сред оценка водителей в разрезе парков", "План по часам ", "Факт по часам", "Разница",
            "Комментарии"])
        self.assertEqual(ws["A1"].fill.fgColor.rgb, "FFDCDCDC")
        self.assertEqual(ws["C1"].font.color.rgb, "FFFF0000")
        self.assertEqual(ws.row_dimensions[1].height, 47.25)
        self.assertEqual(round(ws.column_dimensions["A"].width, 2), 22.71)
        self.assertEqual(ws.title, "Октябрь 2026")

    def test_day_block_is_merged_like_the_file(self):
        merged = {str(item) for item in self.formulas.merged_cells.ranges}
        # 01.10 — строки 2..22 (20 парков файла и «Новый парк»), итог — 23.
        for column in "AKLMN":
            self.assertIn(f"{column}2:{column}22", merged)
            self.assertIn(f"{column}24:{column}44", merged)
            self.assertIn(f"{column}49:{column}69", merged)
        self.assertEqual(len(merged), 15)
        self.assertEqual(self.values["A2"].value, datetime(2026, 10, 1))

    def test_park_rows_carry_formulas_and_values(self):
        f, v = self.formulas, self.values
        self.assertEqual(f["B2"].value, "iTaxi")
        self.assertEqual(f["D2"].value, "=C2*$C$23")
        self.assertAlmostEqual(v["D2"].value, SHARE["iTaxi"] * 120)
        self.assertEqual((v["E2"].value, v["G2"].value, v["H2"].value, v["J2"].value), (50, 250, 45, 4.63))
        self.assertEqual(f["F2"].value, "=E2/D2")
        self.assertEqual(f["I2"].value, "=(E2-H2)/E2")
        self.assertAlmostEqual(v["I2"].value, 5 / 50)
        # Парк без звонков — пустые ячейки, а не нули; % совпадения при плане — 0 %, как в файле.
        self.assertEqual(f["B3"].value, "Jana")
        self.assertEqual([v[f"{column}3"].value for column in "EGHIJ"], [None] * 5)
        self.assertEqual(v["F3"].value, 0)
        # Доля 0 — план 0, а % совпадения не считается.
        self.assertEqual(f["B19"].value, "iTaxiVip")
        self.assertEqual((f["D19"].value, v["D19"].value, f["F19"].value), ("=C19*$C$23", 0, None))
        self.assertEqual(f["B17"].value, "Бизнес Партнер")
        # Парк без доли: ни доли, ни плана.
        self.assertEqual([f[f"{column}22"].value for column in "BCDF"], ["Новый парк", None, None, None])
        self.assertEqual(f["M2"].value, '=IFERROR((L2-K2)/K2,"")')
        self.assertIsNone(f["K2"].value)
        self.assertIsNone(f["N2"].value)

    def test_day_total_row(self):
        f, v = self.formulas, self.values
        self.assertEqual([f[f"{column}23"].value for column in "ABDEFGHIJ"], [
            "=A2", "Итого", "=SUM(D2:D22)", "=SUM(E2:E22)", "=E23/D23",
            "=SUMPRODUCT(G2:G22,H2:H22)/SUM(H2:H22)", "=SUM(H2:H22)", "=(E23-H23)/E23", "=AVERAGE(J2:J22)"])
        self.assertEqual(v["C23"].value, 120)
        self.assertEqual((v["E23"].value, v["H23"].value), (57, 51))
        self.assertAlmostEqual(v["G23"].value, (250 * 45 + 200 * 4 + 150 * 2) / 51)
        self.assertAlmostEqual(v["J23"].value, (4.63 + 5.0) / 2)
        self.assertEqual(f["B23"].fill.fgColor.rgb, "FF9DC3E6")
        self.assertEqual(f["J23"].fill.fgColor.rgb, "FF8FAADC")
        self.assertTrue(f["E23"].font.b)
        self.assertFalse(f["A23"].font.b)
        self.assertEqual(f["I23"].number_format, "0.0%")

    def test_day_without_last_week_has_no_plan(self):
        f = self.formulas
        self.assertEqual(f["B45"].value, "Итого")
        self.assertEqual([f[f"{column}45"].value for column in "CDF"], [None, None, None])
        self.assertIsNone(f["D24"].value)
        self.assertEqual(f["E45"].value, "=SUM(E24:E44)")

    def test_period_block_three_rows_below(self):
        f, v = self.formulas, self.values
        self.assertEqual([f.cell(row, 1).value for row in (46, 47, 48)], [None, None, None])
        self.assertEqual(v["A49"].value, "01.10.2026-02.10.2026")
        self.assertEqual(f["D49"].value, "=SUMIF($B$2:$B$45,B49,$D$2:$D$45)")
        self.assertEqual(f["E49"].value, "=SUMIF($B$2:$B$45,B49,$E$2:$E$45)")
        self.assertAlmostEqual(v["D49"].value, SHARE["iTaxi"] * 120)
        self.assertEqual((v["E49"].value, v["H49"].value, v["G49"].value, v["J49"].value),
                         (60, 55, int((45 * 250 + 10 + 2000) / 55), 4.63))
        self.assertEqual((f["E49"].number_format, f["E49"].font.sz), ("General", 11))
        self.assertEqual(f["A70"].value, "=A49")
        self.assertEqual(v["C70"].value, 120)
        self.assertEqual(f["I70"].number_format, "0%")

    def test_conditional_formatting_like_the_file(self):
        rules = {}
        for cells, items in self.formulas.conditional_formatting._cf_rules.items():
            ordered = sorted(items, key=lambda rule: rule.priority)
            rules[str(cells.sqref)] = [(rule.operator, rule.formula, rule.dxf.font.color.rgb) for rule in ordered]
        self.assertEqual(rules["G2:G23"], [("greaterThanOrEqual", ["200"], "FFFF0000")])
        self.assertEqual(rules["I2:I23"], [("greaterThanOrEqual", ["0.1"], "FFFF0000")])
        self.assertEqual(rules["J2:J23"], [("lessThanOrEqual", ["4.8"], "FFFF0000"),
                                           ("lessThan", ["4.5"], "FF9C0006")])
        self.assertEqual(rules["J49:J70"], [("lessThan", ["4.5"], "FF9C0006"),
                                            ("lessThanOrEqual", ["4.8"], "FFFF0000")])

    def test_empty_period_and_sheet_title_across_months(self):
        build = self.ns["_oktell_billing_szov_daily_workbook"]
        daily = self.ns["_oktell_billing_szov_daily"]({"days": []}, date(2026, 9, 30), date(2026, 10, 1))
        ws = load_workbook(BytesIO(build(daily).getvalue())).active
        self.assertEqual(ws.title, "30.09.2026-01.10.2026")
        self.assertEqual(ws["A2"].value, "За выбранный период и окно времени звонков не нашлось")


class SzovDailyRouteTest(unittest.TestCase):

    def setUp(self):
        self.ns = _namespace()
        self.fetched = []

        def fetch(start, end, minute_from, minute_to, sl_seconds, group_by):
            self.fetched.append((start, end, group_by))
            return RAW_ROWS

        self.ns.update(
            _resource_fte_route_guard=lambda: (1, None, None),
            _oktell_billing_parse_request_args=lambda: (
                {"start_day": date(2026, 10, 1), "end_day": date(2026, 10, 2), "minute_from": 0,
                 "minute_to": 1439}, None, None),
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

    def test_szov_format_takes_a_week_more_and_its_own_file(self):
        kind, name, output = self._call(report_type="szov_daily", mode="park")
        self.assertEqual(self.fetched, [(date(2026, 9, 24), date(2026, 10, 2), "park")])
        self.assertEqual(name, "oktell_billing_park_szov_2026-10-01_2026-10-02.xlsx")
        self.assertEqual(load_workbook(output).active["A1"].value, "Дата")

    def test_general_park_export_is_unchanged(self):
        kind, name, output = self._call(report_type="general", mode="park")
        self.assertEqual(self.fetched, [(date(2026, 10, 1), date(2026, 10, 2), "park")])
        self.assertEqual((name, output.getvalue()), ("oktell_billing_park_2026-10-01_2026-10-02.xlsx", b"general"))

    def test_szov_format_only_for_parks(self):
        body, status = self._call(report_type="szov_daily", mode="line")
        self.assertEqual(status, 400)
        self.assertIn("Таксопарков", body["error"])
        self.assertEqual(self.fetched, [])
        body, status = self._call(report_type="другое", mode="park")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
