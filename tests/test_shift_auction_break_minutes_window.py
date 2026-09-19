# -*- coding: utf-8 -*-
"""Часы куска смены считаются по перерывам ЭТОГО куска — как их видит оператор.

Боевой случай 19.09.2026 (чат, неделя 21–27.09): на экране «36 из 40 ч,
осталось 4 ч», человек берёт кусок 17:00–20:00 и получает
`SHIFT_NORM_EXCEEDED`. Одни и те же часы считались тремя способами:

* экран оператора (`getAuctionLotBreakMinutes`) — перерыв обрезан по куску;
* проверка нормы — у сохранённого куска перерывов не было ВООБЩЕ
  (`'[]'::jsonb` в выборке взятых интервалов), то есть кусок шёл валовым: 39 ч;
* свод для СВ — из куска вычитался ВЕСЬ перерыв смены: 35 ч.

Теперь единственное правило: перерыв считается ровно пересечением со взятым
окном. Проверяем настоящие методы `Database` (через ast, без подъёма модуля) и
текстом — что обе выборки отдают перерывы смены, а не пустой список.
"""
import ast
import os
import unittest
from datetime import time as dt_time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE = os.path.join(REPO_ROOT, "database.py")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _load_minutes_helper():
    """Настоящие методы Database без подъёма модуля (пул и схема тут не нужны)."""
    tree = ast.parse(_read(DATABASE))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Database")
    wanted = {"_shift_auction_break_minutes", "_shift_auction_lot_minutes",
              "_schedule_interval_minutes"}
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    missing = wanted - {m.name for m in methods}
    assert not missing, f"в Database нет методов: {missing}"
    module = ast.Module(
        body=[ast.ClassDef(name="D", bases=[], keywords=[], body=methods, decorator_list=[])],
        type_ignores=[])
    ast.fix_missing_locations(module)
    # `_schedule_interval_minutes` опирается на модульный `_time_to_minutes`
    # database.py — берём его оттуда же, пересказ разошёлся бы молча.
    helpers = [n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "_time_to_minutes"]
    assert helpers, "в database.py нет _time_to_minutes"
    namespace = {"dt_time": dt_time, "re": __import__("re")}
    helper_module = ast.Module(body=helpers, type_ignores=[])
    ast.fix_missing_locations(helper_module)
    exec(compile(helper_module, "<helpers>", "exec"), namespace)
    exec(compile(module, "<lot-minutes>", "exec"), namespace)
    return namespace["D"]()


def _brk(start, end):
    return {"start": start, "end": end}


# Боевой лот 13929 (сб 26.09, 16:00–01:00) со своими тремя перерывами.
EVENING_LOT_BREAKS = [_brk(1090, 1105), _brk(1215, 1245), _brk(1360, 1375)]


class LotMinutesTests(unittest.TestCase):
    def setUp(self):
        self.db = _load_minutes_helper()

    def _minutes(self, start, end, breaks):
        return self.db._shift_auction_lot_minutes(start, end, breaks)

    def test_whole_shift_counts_all_its_breaks(self):
        """Смена целиком — прежний ответ: 9 ч минус час перерывов."""
        minutes = self._minutes("16:00", "01:00", EVENING_LOT_BREAKS)
        self.assertEqual(minutes["duration_minutes"], 540)
        self.assertEqual(minutes["break_minutes"], 60)
        self.assertEqual(minutes["net_minutes"], 480)

    def test_part_counts_only_the_breaks_inside_it(self):
        """Кусок 17:00–20:00: его перерыв — только 18:10–18:25."""
        minutes = self._minutes("17:00", "20:00", EVENING_LOT_BREAKS)
        self.assertEqual(minutes["break_minutes"], 15)
        self.assertEqual(minutes["net_minutes"], 165)

    def test_part_without_breaks_stays_gross(self):
        """В куске 16:00–18:00 перерывов смены нет — вычитать нечего."""
        minutes = self._minutes("16:00", "18:00", EVENING_LOT_BREAKS)
        self.assertEqual(minutes["break_minutes"], 0)
        self.assertEqual(minutes["net_minutes"], 120)

    def test_break_on_the_edge_counts_only_its_overlap(self):
        """Перерыв 19:30–20:30 наполовину за границей куска — считаем половину."""
        minutes = self._minutes("17:00", "20:00", [_brk(19 * 60 + 30, 20 * 60 + 30)])
        self.assertEqual(minutes["break_minutes"], 30)
        self.assertEqual(minutes["net_minutes"], 150)

    def test_night_window_lifts_break_over_midnight(self):
        """Окно 21:00–08:00 живёт за полночь, перерыв 01:00–01:45 — его."""
        minutes = self._minutes("21:00", "08:00", [_brk(60, 105)])
        self.assertEqual(minutes["duration_minutes"], 660)
        self.assertEqual(minutes["break_minutes"], 45)

    def test_break_before_a_night_window_is_not_counted(self):
        """Перерыв 12:00–13:00 в окно 21:00–08:00 не попадает ни одной минутой."""
        minutes = self._minutes("21:00", "08:00", [_brk(12 * 60, 13 * 60)])
        self.assertEqual(minutes["break_minutes"], 0)

    def test_breaks_never_eat_more_than_the_window(self):
        minutes = self._minutes("17:00", "18:00", [_brk(0, 23 * 60)])
        self.assertEqual(minutes["net_minutes"], 0)
        self.assertLessEqual(minutes["break_minutes"], minutes["duration_minutes"])

    def test_broken_break_records_are_skipped(self):
        minutes = self._minutes("17:00", "20:00", ["не словарь", {"start": "нет", "end": 5}, None])
        self.assertEqual(minutes["break_minutes"], 0)
        self.assertEqual(minutes["net_minutes"], 180)


class SourceWiringTests(unittest.TestCase):
    """Перерывы обязаны доезжать до обеих выборок — иначе кусок снова валовой."""

    def setUp(self):
        self.source = _read(DATABASE)

    def _method(self, name):
        start = self.source.index(f"    def {name}(")
        end = self.source.index("\n    def ", start + 10)
        return self.source[start:end]

    def test_claimed_intervals_carry_the_shift_breaks(self):
        body = self._method("_get_shift_auction_operator_claimed_intervals_tx")
        self.assertIn("sh.breaks", body)
        self.assertNotIn("'[]'::jsonb AS breaks", body,
                         "взятый кусок без перерывов идёт в норму валовым")

    def test_claim_passes_the_whole_shift_breaks(self):
        """Кусок обрезает окно, а не предварительный отбор перерывов."""
        body = self._method("claim_shift_auction_test_lot")
        region = body[body.index("lot_breaks = lot[8]"):]
        region = region[:region.index("norm_allowance_minutes = ")]
        self.assertIn("candidate_minutes = self._shift_auction_lot_minutes(", region)
        self.assertIn("lot_breaks", region)
        self.assertNotIn("_break_overlaps_minute_range", region)
