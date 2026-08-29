# -*- coding: utf-8 -*-
"""В чате можно добрать часы в том же дне, если они не перекрывают уже взятое.

Решение владельца 29.08.2026: «человек после выбора смены в том же дне имеет
право брать ещё часы, которые не перекрывают уже взятую смену. Главное — не
превышал свой недельный лимит».

До этого действовал жёсткий календарный лимит `DAY_ALREADY_HAS_SHIFT`: одна
смена в день, и снимал его только режим добора — но он снимает заодно и норму.
Для чата нужна другая пара: лимит дня снят, норма ОСТАЛАСЬ.

Проверяем настоящий метод `Database._shift_auction_claim_conflict` (через ast,
без подъёма модуля) и текстом — что оба пути выдачи смены им пользуются, а
недельный потолок для чата продолжает считаться.
"""
import ast
import os
import unittest
from datetime import datetime, time as dt_time, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE = os.path.join(REPO_ROOT, "database.py")
VIEW = os.path.join(REPO_ROOT, "src", "components", "resources", "ShiftAuctionView.jsx")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _load_conflict_helper():
    """Настоящие методы Database без подъёма модуля (пул и схема нам не нужны)."""
    tree = ast.parse(_read(DATABASE))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Database")
    wanted = {"_shift_auction_day_ordinal", "_shift_auction_claim_conflict",
              "_schedule_interval_minutes"}
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    missing = wanted - {m.name for m in methods}
    assert not missing, f"в Database нет методов: {missing}"
    module = ast.Module(
        body=[ast.ClassDef(name="D", bases=[], keywords=[], body=methods, decorator_list=[])],
        type_ignores=[])
    ast.fix_missing_locations(module)
    # `_schedule_interval_minutes` опирается на модульные имена database.py —
    # берём их оттуда же, а не пересказываем: пересказ разошёлся бы молча.
    module_tree = ast.parse(_read(DATABASE))
    helpers = [n for n in module_tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "_time_to_minutes"]
    assert helpers, "в database.py нет _time_to_minutes"
    namespace = {"datetime": datetime, "timedelta": timedelta, "dt_time": dt_time, "re": __import__("re")}
    helper_module = ast.Module(body=helpers, type_ignores=[])
    ast.fix_missing_locations(helper_module)
    exec(compile(helper_module, "<helpers>", "exec"), namespace)
    exec(compile(module, "<claim-conflict>", "exec"), namespace)
    return namespace["D"]()


def _row(day, start, end):
    return {"shift_date": day, "start_time": start, "end_time": end}


D1 = "2026-09-01"
D2 = "2026-09-02"


class ClaimConflictTests(unittest.TestCase):
    def setUp(self):
        self.db = _load_conflict_helper()

    def _conflict(self, claims, day, start_minute, end_minute):
        return self.db._shift_auction_claim_conflict(claims, day, start_minute, end_minute)

    def test_shifts_back_to_back_do_not_conflict(self):
        """09:00–15:00 и 15:00–21:00 — это и есть «добрать часы в тот же день»."""
        claims = [_row(D1, "09:00", "15:00")]
        self.assertIsNone(self._conflict(claims, D1, 15 * 60, 21 * 60))

    def test_overlapping_shift_conflicts(self):
        claims = [_row(D1, "09:00", "15:00")]
        conflict = self._conflict(claims, D1, 12 * 60, 18 * 60)
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["start_time"], "09:00")

    def test_night_tail_blocks_next_morning(self):
        """Ночь 20:00–08:00 кончается в СЛЕДУЮЩЕМ дне.

        У утренней смены того дня другая `shift_date`, поэтому сравнение внутри
        суток такую пару пропускает — и человек оказывался в двух сменах разом.
        """
        claims = [_row(D1, "20:00", "08:00")]
        self.assertIsNotNone(self._conflict(claims, D2, 6 * 60, 14 * 60))
        # После 08:00 день свободен.
        self.assertIsNone(self._conflict(claims, D2, 9 * 60, 18 * 60))

    def test_day_shift_and_night_of_the_same_day_fit_together(self):
        claims = [_row(D1, "09:00", "15:00")]
        self.assertIsNone(self._conflict(claims, D1, 20 * 60, 32 * 60))

    def test_broken_rows_are_skipped(self):
        self.assertIsNone(self._conflict(None, D1, 9 * 60, 15 * 60))
        self.assertIsNone(self._conflict([_row("", "", "")], D1, 9 * 60, 15 * 60))
        self.assertIsNone(self._conflict([_row(D1, "09:00", "15:00")], "не дата", 9 * 60, 15 * 60))


class ClaimPathTests(unittest.TestCase):
    """Куда правило обязано быть подключено — и где обязано остаться прежним."""

    def setUp(self):
        self.source = _read(DATABASE)

    def _method(self, name):
        start = self.source.index(f"    def {name}(")
        end = self.source.index("\n    def ", start + 10)
        return self.source[start:end]

    def test_claim_lifts_the_daily_limit_for_chat_and_topup(self):
        body = self._method("claim_shift_auction_test_lot")
        self.assertIn(
            "allows_extra_shifts_per_day = is_topup_mode or mode == SHIFT_AUCTION_MODE_CHAT",
            body,
        )
        self.assertIn("_shift_auction_claim_conflict(", body)
        self.assertIn('raise ValueError("SHIFT_OVERLAPS_EXISTING")', body)
        # Календарный лимит остаётся для линии вне добора.
        self.assertIn('raise ValueError("DAY_ALREADY_HAS_SHIFT")', body)

    def test_weekly_norm_still_guards_chat(self):
        """Добор снимает норму, чат — нет: у него потолок обязан остаться."""
        body = self._method("claim_shift_auction_test_lot")
        norm_check = body[body.index("norm_allowance_minutes = "):]
        norm_check = norm_check[:norm_check.index('raise ValueError("SHIFT_NORM_EXCEEDED")')]
        self.assertIn("not is_topup_mode", norm_check)
        self.assertNotIn("SHIFT_AUCTION_MODE_CHAT", norm_check,
                         "норма не должна отключаться для чата — это её единственный потолок")

    def test_self_schedule_follows_the_same_rule(self):
        """«Свой график» — второй путь, которым смена попадает человеку в день."""
        body = self._method("self_schedule_shift_auction_shift")
        self.assertIn("if mode == SHIFT_AUCTION_MODE_CHAT:", body)
        self.assertIn("_shift_auction_claim_conflict(own_claims, date_key, start_min, end_min)", body)
        self.assertIn('raise ValueError("DAY_ALREADY_HAS_SHIFT")', body)


class FrontendMirrorTests(unittest.TestCase):
    """Сетка обязана гасить ровно то же, что откажет сервер, — и не больше."""

    def setUp(self):
        self.source = _read(VIEW)
        start = self.source.index("const claimBlockReasonByLotId = useMemo(")
        self.region = self.source[start:self.source.index("useEffect(", start)]

    def test_daily_lock_is_not_applied_to_chat(self):
        self.assertIn("if (isTopupActive || supportsPartialClaim) {", self.region)
        lock = self.region[self.region.index("if (isTopupActive || supportsPartialClaim) {"):]
        lock = lock[:lock.index("const netMinutes")]
        self.assertIn("} else if (lot.shift_date && myClaimedDateSet.has(lot.shift_date)) {", lock)

    def test_chat_keeps_the_norm_reasons(self):
        """Из чатовой ветки нельзя выходить: ниже считается недельный потолок."""
        self.assertIn("if (isTopupActive) return;", self.region)
        self.assertIn("Норма уже набрана", self.region)
        self.assertIn("Превысит лимит на", self.region)

    def test_partial_lot_is_not_greyed_out_by_the_whole_window(self):
        """Часть смены можно взять ровно на остаток нормы — гасить лот нельзя."""
        self.assertIn("const canTakePart = supportsPartialClaim", self.region)
        self.assertRegex(self.region, r"!canTakePart\s*\n\s*&& myAuctionWorkload\.normMinutes > 0")

    def test_taken_part_occupies_only_its_own_hours(self):
        """Занято фактическое окно, а не окно лота — иначе день не добрать."""
        self.assertIn("const myClaimedIntervals = useMemo(", self.source)
        self.assertIn("start_time: getAuctionLotEffectiveStartTime(lot)", self.source)
        option = self.source[self.source.index("const buildPostAuctionClaimOption"):]
        option = option[:option.index("const getSelectionMinuteRange")]
        self.assertIn("getAuctionLotEffectiveStartTime(item) || item.start_time || item.start", option)

    def test_rule_lives_in_a_tested_module(self):
        self.assertIn(
            "import { findAuctionClaimConflict } from './shiftAuctionClaimRules';",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
