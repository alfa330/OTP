# -*- coding: utf-8 -*-
"""Хвост ночной смены обязан попадать в СВОЙ день, а не в день лота.

16.09.2026, чат. Ночь 16.09 «20*08» разобрали кусками: 22:00–00:00 взял один
человек, 00:00–08:00 — другой. Второй кусок физически лежит в ЧЕТВЕРГЕ, но
хранится он двумя временами без даты, а дату ему давала исходная смена — среда.
В график он уехал строкой «среда 00:00–08:00», там склеился с дневной сменой
получателя после передачи (вышло 00:00–20:30, 20,5 часа), в среде оказались две
ночи, а ночь на четверг осталась без человека.

Правило одно на весь аукцион: смена принадлежит дню своего НАЧАЛА, и у куска,
который начинается после полуночи, этот день — следующий.
"""
import ast
import unittest
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path

from tests import source_cache

DATABASE_PATH = Path(__file__).resolve().parents[1] / "database.py"


def _source():
    return DATABASE_PATH.read_text(encoding="utf-8-sig")


def _database_class():
    module = source_cache.parse(_source())
    return next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )


def _method(name):
    return next(
        node for node in _database_class().body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _method_source(name):
    return ast.get_source_segment(_source(), _method(name))


def _calls(method_name, callee_suffix):
    """Вызовы вида self.<...callee_suffix>(...) внутри метода."""
    found = []
    for node in ast.walk(_method(method_name)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == callee_suffix:
                found.append(node)
    return found


def _kwarg(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return ast.unparse(kw.value)
    return None


def _load_conflict_helpers():
    """Настоящие методы Database без подъёма модуля.

    `import database` в тестах не поднять: в конце модуля создаётся Database() —
    это DDL и пул коннектов. Достаём нужные куски через ast и исполняем в своём
    namespace, чтобы проверялся реальный код, а не его копия.
    """
    source = _source()
    module = source_cache.parse(source)
    cls = next(n for n in module.body
               if isinstance(n, ast.ClassDef) and n.name == "Database")
    wanted = {"_shift_auction_day_ordinal", "_shift_auction_claim_conflict",
              "_schedule_interval_minutes"}
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    missing = wanted - {m.name for m in methods}
    assert not missing, f"в Database нет методов: {missing}"
    helper = next(n for n in module.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_time_to_minutes")
    holder = ast.ClassDef(name="D", bases=[], keywords=[], body=methods, decorator_list=[])
    compiled = ast.Module(body=[helper, holder], type_ignores=[])
    ast.fix_missing_locations(compiled)
    namespace = {"datetime": datetime, "timedelta": timedelta, "date": date,
                 "dt_time": dt_time}
    exec(compile(compiled, "<tail>", "exec"), namespace)
    return namespace["D"]()


class ClaimDateSqlTests(unittest.TestCase):
    """Дата куска считается ОДНИМ выражением и подставляется в оба запроса."""

    def test_expression_shifts_only_the_after_midnight_piece(self):
        source = _source()
        self.assertIn("SHIFT_AUCTION_CLAIM_DATE_SQL", source)
        expression = " ".join(
            source.split("SHIFT_AUCTION_CLAIM_DATE_SQL = (")[1].split(")")[0].split()
        )
        # Сдвиг только у смены через полночь и только у куска, который начинается
        # раньше её старта: 00:00 ночи 20:00–08:00 — это уже следующий день.
        self.assertIn("sh.shift_date +", expression)
        self.assertIn("sh.end_time <= sh.start_time", expression)
        self.assertIn("hc.claimed_start_time < sh.start_time", expression)

    def test_both_readers_use_the_expression(self):
        for method_name in ("_get_shift_auction_operator_claimed_intervals_tx",
                            "publish_shift_auction_test_to_work_schedules"):
            body = _method_source(method_name)
            self.assertIn("{self.SHIFT_AUCTION_CLAIM_DATE_SQL}", body,
                          f"{method_name} берёт дату куска у исходной смены")
            self.assertNotIn("to_char(sh.shift_date, 'YYYY-MM-DD') AS shift_date", body)
        publish = _method_source("publish_shift_auction_test_to_work_schedules")
        self.assertNotIn("SELECT hc.claimed_by, sh.shift_date,", publish)


class PostAuctionWriteDayTests(unittest.TestCase):
    """Добор пишет смену, выходной, перерывы и часы в день НАЧАЛА куска."""

    METHODS = ("post_auction_claim_lot", "post_auction_claim_saved_shift")

    def test_shift_is_saved_on_its_own_day(self):
        for method_name in self.METHODS:
            calls = _calls(method_name, "_save_shift_tx")
            self.assertTrue(calls, f"{method_name} не сохраняет смену")
            for call in calls:
                self.assertEqual(_kwarg(call, "shift_date"), "shift_start_date",
                                 f"{method_name}: смена уедет в день лота")

    def test_day_context_follows_the_shift(self):
        for method_name in self.METHODS:
            body = _method_source(method_name)
            self.assertIn("_load_day_shift_breaks_tx(cursor, operator_id, shift_start_date)", body)
            self.assertIn("_snapshot_schedule_days_tx(cursor, [(operator_id, shift_start_date)])", body)
            self.assertIn("(operator_id, shift_start_date)", body)
            for call in _calls(method_name, "_recalculate_auto_daily_hours_tx"):
                self.assertEqual(_kwarg(call, "start_date"), "shift_start_date")
                self.assertEqual(_kwarg(call, "end_date"), "shift_start_date")

    def test_merge_compares_minutes_inside_the_target_day(self):
        # Минуты куска приходят сквозными от начала лота (00:00 ночи — это 1440),
        # а смены дня лежат внутри суток: без возврата внутрь суток склейка
        # промахнётся мимо соседней смены.
        for method_name in self.METHODS:
            body = _method_source(method_name)
            self.assertIn("day_start_min = new_start_min - claim_day_shift * 24 * 60", body)
            calls = _calls(method_name, "_resolve_post_auction_merged_shift_range")
            self.assertTrue(calls)
            for call in calls:
                self.assertEqual([ast.unparse(a) for a in call.args[1:]],
                                 ["day_start_min", "day_end_min"])


class PublishTailTests(unittest.TestCase):
    """Публикация не теряет хвост ночи ни на выходном, ни за краем периода."""

    def test_day_off_does_not_swallow_a_claimed_tail(self):
        body = _method_source("publish_shift_auction_test_to_work_schedules")
        self.assertIn("if (operator_id, lot_date) in day_off_dates and not claimed_shifts:", body)

    def test_tail_beyond_the_period_is_still_saved(self):
        body = _method_source("publish_shift_auction_test_to_work_schedules")
        self.assertIn("day not in lot_date_set", body)
        self.assertIn('"shift_date": extra_date,', body)


class ClaimConflictAcrossMidnightTests(unittest.TestCase):
    """Настоящая проверка пересечения на датах, которые теперь приходят верными."""

    def setUp(self):
        self.db = _load_conflict_helpers()

    def test_tail_blocks_the_morning_of_its_own_day(self):
        # Хвост ночи со среды на четверг: строка приходит датой ЧЕТВЕРГА.
        tail = {"shift_date": "2026-09-17", "start_time": "00:00", "end_time": "08:00"}
        # Четверг 07:00–15:00 пересекается с хвостом — брать нельзя.
        self.assertIsNotNone(
            self.db._shift_auction_claim_conflict([tail], "2026-09-17", 7 * 60, 15 * 60))
        # Среда 07:00–15:00 с ним не пересекается: до фикса хвост лежал в среде и
        # ровно эту смену ошибочно запрещал.
        self.assertIsNone(
            self.db._shift_auction_claim_conflict([tail], "2026-09-16", 7 * 60, 15 * 60))

    def test_night_lot_piece_is_compared_in_through_time(self):
        # Кусок 00:00–08:00 приходит из окна лота минутами 1440–1920 с датой лота.
        evening = {"shift_date": "2026-09-16", "start_time": "20:00", "end_time": "00:00"}
        self.assertIsNone(
            self.db._shift_auction_claim_conflict([evening], "2026-09-16", 1440, 1920))
        night = {"shift_date": "2026-09-16", "start_time": "20:00", "end_time": "08:00"}
        self.assertIsNotNone(
            self.db._shift_auction_claim_conflict([night], "2026-09-16", 1440, 1920))


if __name__ == "__main__":
    unittest.main()
