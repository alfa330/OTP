# -*- coding: utf-8 -*-
"""Две ночные смены подряд брать нельзя + добор больше не склеивает свои же смены.

Решение владельца 29.08.2026 по чат-аукциону: ночь — это смена «20*08», и две
такие подряд недопустимы. Первая кончается в 08:00, вторая начинается в 20:00
ТОГО ЖЕ дня: двенадцать часов между сменами и двое суток без нормального сна.

Календарная проверка «на этот день уже выбрана смена» этого не ловит — у ночей
разные даты начала, хвост первой лежит в дне второй. Поэтому правило отдельное.

Заодно закрыта дыра добора: он сверялся только с частями ТОЙ ЖЕ исходной смены,
а свои же другие лоты того дня не смотрел. В проде это дало 06.09 у одного
человека сразу 20:00–00:00 и 20:00–01:00 — два лота в аукционе, одна смена в
графике (их молча склеил _resolve_post_auction_merged_shift_range) и двойной
счёт в норме.
"""
import ast
import os
import unittest
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE = os.path.join(REPO_ROOT, "database.py")
BACKEND = os.path.join(REPO_ROOT, "bot_schedule2.py")
AUCTION_VIEW = os.path.join(REPO_ROOT, "src", "components", "resources", "ShiftAuctionView.jsx")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _load_night_helpers():
    """Настоящие методы Database без подъёма модуля.

    `import database` на этой машине не поднять: в конце модуля создаётся
    Database() — схема и пул коннектов. Поэтому достаём нужные методы через ast
    и исполняем их в своём namespace: проверяется реальный код, а не копия.
    """
    tree = ast.parse(_read(DATABASE))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Database")
    wanted = {"_shift_auction_hhmm", "_is_shift_auction_night",
              "_shift_auction_adjacent_night_date"}
    consts = []
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "").startswith("SHIFT_AUCTION_NIGHT") for t in node.targets):
            consts.append(node)
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    missing = wanted - {m.name for m in methods}
    assert not missing, f"в Database нет методов: {missing}"
    module = ast.Module(
        body=[ast.ClassDef(name="D", bases=[], keywords=[],
                           body=consts + methods, decorator_list=[])],
        type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"datetime": datetime, "timedelta": timedelta}
    exec(compile(module, "<night>", "exec"), namespace)
    return namespace["D"]


def _row(day, start, end):
    return {"shift_date": day, "start_time": start, "end_time": end}


class NightDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.D = _load_night_helpers()

    def test_night_is_exactly_20_to_08(self):
        """Владелец назвал ночью смену «20*08» — только её.

        Вечерние 19:30–02:00 и 22:00–02:00 тоже уходят за полночь, но ночными
        не считаются: запрет на них закрыл бы половину графика.
        """
        self.assertTrue(self.D._is_shift_auction_night("20:00", "08:00"))
        for start, end in (("19:30", "02:00"), ("22:00", "02:00"), ("20:00", "00:00"),
                           ("20:00", "01:00"), ("17:30", "00:00"), ("08:00", "17:00")):
            self.assertFalse(self.D._is_shift_auction_night(start, end),
                             f"{start}–{end} не должна считаться ночью")

    def test_hhmm_accepts_time_objects_and_strings(self):
        """Источники дают и time, и строку 'HH:MM', и datetime."""
        from datetime import time as dt_time
        self.assertEqual(self.D._shift_auction_hhmm(dt_time(20, 0)), "20:00")
        self.assertEqual(self.D._shift_auction_hhmm("20:00:00"), "20:00")
        self.assertEqual(self.D._shift_auction_hhmm(None), "")


class AdjacentNightTests(unittest.TestCase):
    def setUp(self):
        self.D = _load_night_helpers()

    def _check(self, claimed, day, start="20:00", end="08:00"):
        return self.D._shift_auction_adjacent_night_date(claimed, day, start, end)

    def test_next_day_night_is_blocked(self):
        self.assertEqual(
            self._check([_row("2026-09-01", "20:00", "08:00")], "2026-09-02"),
            "2026-09-01")

    def test_previous_day_night_is_blocked(self):
        """Смены разбирают в произвольном порядке — запрет обязан смотреть обе стороны."""
        self.assertEqual(
            self._check([_row("2026-09-03", "20:00", "08:00")], "2026-09-02"),
            "2026-09-03")

    def test_night_through_a_day_is_allowed(self):
        self.assertIsNone(self._check([_row("2026-09-01", "20:00", "08:00")], "2026-09-03"))

    def test_evening_shift_next_door_is_not_a_night(self):
        """19:30–02:00 рядом с ночью не мешает: это не «20*08»."""
        self.assertIsNone(self._check([_row("2026-09-01", "19:30", "02:00")], "2026-09-02"))

    def test_day_shift_after_a_night_is_not_blocked_by_this_rule(self):
        """Правило именно про ДВЕ НОЧИ, а не про любой соседний день."""
        self.assertIsNone(
            self._check([_row("2026-09-01", "20:00", "08:00")], "2026-09-02",
                        start="08:00", end="17:00"))

    def test_first_night_of_the_week_is_allowed(self):
        self.assertIsNone(self._check([], "2026-09-02"))

    def test_broken_dates_do_not_crash(self):
        """Данные приходят из разных источников — мусор не должен ронять разбор."""
        self.assertIsNone(self._check([_row("", "20:00", "08:00")], "2026-09-02"))
        self.assertIsNone(self._check([_row("2026-09-01", "20:00", "08:00")], "не дата"))


class NightRuleIsEnforcedEverywhereTests(unittest.TestCase):
    """Правило обязано стоять во ВСЕХ путях, которыми смена достаётся оператору."""

    @staticmethod
    def _method(name):
        src = _read(DATABASE)
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.get_source_segment(src, node) or ""
        raise AssertionError(f"{name} не найдена")

    def test_every_claim_path_checks_adjacent_night(self):
        for name in ("claim_shift_auction_test_lot",
                     "post_auction_claim_lot",
                     "post_auction_claim_saved_shift",
                     "self_schedule_shift_auction_shift"):
            self.assertIn("_shift_auction_adjacent_night_date", self._method(name),
                          f"{name} не проверяет соседнюю ночь")
            self.assertIn("NIGHT_SHIFT_ALREADY_ADJACENT", self._method(name),
                          f"{name} не отдаёт код ошибки про две ночи")

    def test_topup_mode_does_not_bypass_the_night_rule(self):
        """В доборе снят лимит «одна смена в день», но не право на отдых.

        Проверка обязана стоять ПОСЛЕ ветки «день не заперт»/else, иначе она
        обошлась бы вместе с дневным лимитом. Ветка теперь общая у добора и чата
        (29.08.2026: в чате можно добрать часы в том же дне) — правило отдыха
        одинаково обязательно в обоих.
        """
        source = self._method("claim_shift_auction_test_lot")
        topup = source.index("if allows_extra_shifts_per_day:")
        night = source.index("_shift_auction_adjacent_night_date")
        day_limit = source.index("DAY_ALREADY_HAS_SHIFT")
        self.assertGreater(night, day_limit,
                           "проверка ночей должна идти после дневного лимита")
        self.assertGreater(night, topup,
                           "проверка ночей не должна оставаться внутри ветки добора")

    def test_error_has_a_human_message(self):
        backend = _read(BACKEND)
        self.assertIn('"NIGHT_SHIFT_ALREADY_ADJACENT": ("Две ночные смены подряд', backend)


class PostAuctionOverlapHoleTests(unittest.TestCase):
    """Добор обязан сверяться со ВСЕМИ своими сменами дня, а не с одной сменой."""

    @staticmethod
    def _method(name):
        src = _read(DATABASE)
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.get_source_segment(src, node) or ""
        raise AssertionError(f"{name} не найдена")

    def test_post_auction_checks_own_other_claims_that_day(self):
        for name in ("post_auction_claim_lot", "post_auction_claim_saved_shift"):
            source = self._method(name)
            self.assertIn("_get_shift_auction_operator_claimed_intervals_tx", source,
                          f"{name} не читает свои же смены дня")
            self.assertIn("SHIFT_OVERLAPS_EXISTING", source)

    def test_saved_shift_checks_before_inserting_the_claim(self):
        """После вставки откат стоил бы исключения, а строка добора уже в таблице."""
        source = self._method("post_auction_claim_saved_shift")
        check = source.index("_get_shift_auction_operator_claimed_intervals_tx")
        insert = source.index("INSERT INTO shift_auction_historical_claims")
        self.assertLess(check, insert, "проверка своих смен должна идти до вставки")


class NightRuleFrontendTests(unittest.TestCase):
    def test_grid_greys_the_neighbour_night_with_a_reason(self):
        """Иначе смена выглядит доступной, а клик отдаёт отказ с сервера."""
        view = _read(AUCTION_VIEW)
        self.assertIn("const AUCTION_NIGHT_START_HHMM = '20:00'", view)
        self.assertIn("const AUCTION_NIGHT_END_HHMM = '08:00'", view)
        self.assertIn("две ночи подряд нельзя", view)

    def test_front_and_back_agree_on_what_a_night_is(self):
        """Разъехавшиеся определения = серым красится не то, что запрещает сервер."""
        view = _read(AUCTION_VIEW)
        database = _read(DATABASE)
        self.assertIn("SHIFT_AUCTION_NIGHT_START_HHMM = '20:00'", database)
        self.assertIn("SHIFT_AUCTION_NIGHT_END_HHMM = '08:00'", database)
        self.assertIn("AUCTION_NIGHT_START_HHMM = '20:00'", view)
        self.assertIn("AUCTION_NIGHT_END_HHMM = '08:00'", view)

    def test_night_rule_runs_before_the_topup_branch_on_the_front_too(self):
        """На витрине добор тоже не должен обходить запрет."""
        view = _read(AUCTION_VIEW)
        block = view[view.index("if (settings.rate_lock_enabled)"):]
        night = block.index("две ночи подряд нельзя")
        topup = block.index("if (isTopupActive)")
        self.assertLess(night, topup, "проверка ночей должна идти до ветки добора")


if __name__ == "__main__":
    unittest.main()
