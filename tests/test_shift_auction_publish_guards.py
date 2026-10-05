# -*- coding: utf-8 -*-
"""Публикация аукциона не стирает графики, когда сохранять нечего.

04.10.2026 в 21:03 СВ нажала «Сохранить в графики» при тумблере на «Линии»
вместо «Чата». Аукцион линии с сентября висел на остановленном периоде
14–20.09, смен в нём никто не брал, статус был «завершён» — и сервер стёр у 36
операторов Основы смены и выходные за 14–20.09, а пересчёт часов обнулил
отработанное, снял отметки опозданий и удалил штрафы.

Два запрета, оба ДО первой очистки дня и для обоих направлений:
- прошедший период не публикуется (у прошедших дней уже есть часы);
- аукцион, где никто не взял ни одной смены, не публикуется — даже если
  кто-то отметил выходные: сохранить смены нечего, а очистка снесла бы неделю.
"""
import ast
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
ROUTES_PATH = ROOT / "bot_schedule2.py"
VIEW_PATH = ROOT / "src" / "components" / "resources" / "ShiftAuctionView.jsx"

NOW = datetime(2026, 10, 4, 21, 3, 37)
DIRECTIONS = ("line", "chat")


def _load_publish():
    """Настоящий метод публикации без подъёма модуля (в конце database.py — Database())."""
    module = source_cache.parse(DATABASE_PATH.read_text(encoding="utf-8-sig"))
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "Database")
    method = next(
        n for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "publish_shift_auction_test_to_work_schedules"
    )
    namespace = {
        "SHIFT_AUCTION_MODE_LINE": "line",
        "normalize_shift_auction_mode": lambda value: "chat" if value == "chat" else "line",
        "shift_auction_settings_row_id": lambda mode: 2 if mode == "chat" else 1,
    }
    exec(compile(ast.Module([method], []), str(DATABASE_PATH), "exec"), namespace)
    return namespace[method.name]


class ReachedClearing(Exception):
    """Метод дошёл до очистки дней — значит, запреты его пропустили."""


class FakeCursor:
    def __init__(self, claims, day_offs):
        self.claims = claims
        self.day_offs = day_offs
        self.statements = []
        self._rows = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        if "FROM shift_auction_test_access" in sql:
            self._rows = [(True, None, None, None, NOW - timedelta(hours=1), 20)]
        elif "FROM shift_auction_historical_claims hc" in sql and "UNION ALL" in sql:
            self._rows = list(self.claims)
        elif "FROM shift_auction_test_day_offs" in sql:
            self._rows = list(self.day_offs)
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeDatabase:
    SHIFT_AUCTION_CLAIM_DATE_SQL = "sh.shift_date"
    publish = _load_publish()

    def __init__(self, lot_dates, claims, day_offs=(), now=NOW):
        self.lot_dates = lot_dates
        self.now = now
        self.cursor = FakeCursor(claims, day_offs)
        self.cleared = []

    @contextmanager
    def _get_cursor(self):
        yield self.cursor

    def _shift_auction_run_bounds_tx(self, cursor, plan_id, starts_at, ends_at):
        return starts_at, ends_at

    def _get_shift_auction_test_status(self, *args):
        return "closed"

    def _get_shift_auction_lot_dates_tx(self, cursor, direction_mode="line"):
        return list(self.lot_dates)

    def _almaty_now(self):
        return self.now

    def _get_shift_auction_participant_ids_tx(self, cursor, direction_mode="line"):
        return {101, 102}

    def _schedule_change_actor(self, *args, **kwargs):
        return {"id": 55, "name": "СВ", "role": "sv", "source": "auction"}

    def _snapshot_schedule_days_tx(self, cursor, keys):
        return {}

    def _get_shift_auction_operator_blocked_date_map_tx(self, *args, **kwargs):
        return {}

    def _load_day_shift_breaks_tx(self, *args, **kwargs):
        return []

    def _clear_day_schedule_tx(self, cursor, operator_id, day):
        self.cleared.append((operator_id, day))
        raise ReachedClearing()

    def run(self, mode):
        return self.publish(updated_by=55, direction_mode=mode)


def _week(first):
    return [first + timedelta(days=i) for i in range(7)]


SEPTEMBER = _week(date(2026, 9, 14))
NEXT_WEEK = _week(date(2026, 10, 5))
CLAIM = (101, date(2026, 10, 6), "09:00", "18:00")


class PublishGuardsTests(unittest.TestCase):
    def assertRefused(self, db, mode, code):
        with self.assertRaises(ValueError) as caught:
            db.run(mode)
        self.assertEqual(str(caught.exception), code)
        self.assertEqual(db.cleared, [], "запрет обязан сработать до первой очистки дня")

    def assertPublishes(self, db, mode):
        with self.assertRaises(ReachedClearing):
            db.run(mode)

    def test_incident_past_period_is_refused_before_any_day_is_cleared(self):
        for mode in DIRECTIONS:
            with self.subTest(mode=mode):
                self.assertRefused(FakeDatabase(SEPTEMBER, claims=[]), mode, "AUCTION_PERIOD_ALREADY_PASSED")

    def test_past_period_is_refused_even_with_claims(self):
        claim = (101, date(2026, 9, 15), "09:00", "18:00")
        for mode in DIRECTIONS:
            with self.subTest(mode=mode):
                self.assertRefused(FakeDatabase(SEPTEMBER, claims=[claim]), mode, "AUCTION_PERIOD_ALREADY_PASSED")

    def test_auction_without_claims_is_refused_before_any_day_is_cleared(self):
        for mode in DIRECTIONS:
            with self.subTest(mode=mode):
                self.assertRefused(FakeDatabase(NEXT_WEEK, claims=[]), mode, "AUCTION_NOTHING_CLAIMED")

    def test_day_offs_alone_do_not_unlock_publication(self):
        """Одни отмеченные выходные — не повод стереть всем неделю (в инциденте их было 8)."""
        day_offs = [(101, date(2026, 10, 7)), (102, date(2026, 10, 8))]
        for mode in DIRECTIONS:
            with self.subTest(mode=mode):
                self.assertRefused(
                    FakeDatabase(NEXT_WEEK, claims=[], day_offs=day_offs), mode, "AUCTION_NOTHING_CLAIMED"
                )

    def test_normal_publication_still_reaches_the_schedule(self):
        for mode in DIRECTIONS:
            with self.subTest(mode=mode):
                db = FakeDatabase(NEXT_WEEK, claims=[CLAIM])
                self.assertPublishes(db, mode)
                self.assertEqual(len(db.cleared), 1)

    def test_last_day_of_the_period_is_today_still_publishable(self):
        """Граница: период, который заканчивается сегодня, ещё не прошёл."""
        claim = (101, NOW.date(), "09:00", "18:00")
        for mode in DIRECTIONS:
            with self.subTest(mode=mode):
                self.assertPublishes(FakeDatabase(_week(NOW.date() - timedelta(days=6)), claims=[claim]), mode)

    def test_period_that_ended_yesterday_is_refused(self):
        claim = (101, NOW.date() - timedelta(days=1), "09:00", "18:00")
        for mode in DIRECTIONS:
            with self.subTest(mode=mode):
                self.assertRefused(
                    FakeDatabase(_week(NOW.date() - timedelta(days=7)), claims=[claim]),
                    mode, "AUCTION_PERIOD_ALREADY_PASSED",
                )

    def test_guards_answer_409_with_human_messages(self):
        """С кодом 200 фронт ушёл бы в ветку успеха: «Графики сохранены: 0 смен»."""
        routes = ROUTES_PATH.read_text(encoding="utf-8-sig")
        self.assertIn(
            '"AUCTION_PERIOD_ALREADY_PASSED": ("Период аукциона уже прошёл — сохранять его в графики нельзя", 409),',
            routes,
        )
        self.assertIn(
            '"AUCTION_NOTHING_CLAIMED": ("В аукционе никто не взял ни одной смены — сохранять в графики нечего", 409),',
            routes,
        )


class PublishConfirmTextTests(unittest.TestCase):
    """В окне подтверждения видно, ЧТО публикуется: направление, период, сколько людей."""

    def setUp(self):
        self.source = VIEW_PATH.read_text(encoding="utf-8-sig")
        start = self.source.index("const handlePublishAuction = useCallback(")
        self.handler = self.source[start:self.source.index("}, [", start)]
        self.deps = self.source[self.source.index("}, [", start):self.source.index("]);", start)]

    def test_confirm_names_direction_period_and_participants(self):
        confirm = self.handler[self.handler.index("window.confirm("):self.handler.index("if (!confirmed) return;")]
        self.assertIn("AUCTION_DIRECTION_LABELS[direction]", confirm)
        self.assertIn("formatAuctionPeriodLabel(settings.selected_period)", confirm)
        self.assertIn("settings.selected_operator_ids.length", confirm)

    def test_confirm_text_follows_the_toggle(self):
        """Без этих зависимостей окно показывало бы направление прошлого рендера."""
        for name in ("direction", "settings.selected_period", "settings.selected_operator_ids.length",
                     "settingsDirection"):
            self.assertIn(f" {name},", self.deps.replace("[", " ") + ",", name)

    def test_no_confirm_while_settings_belong_to_the_other_direction(self):
        """После переключения тумблера, пока снапшот нового направления не пришёл,
        в settings лежат период и состав прошлого: окно назвало бы «Чат», а показало
        бы даты и людей линии. Публикация обязана остановиться ДО окна."""
        guard = "if (settingsDirection !== direction) {"
        self.assertIn(guard, self.handler)
        self.assertLess(self.handler.index(guard), self.handler.index("window.confirm("))
        after_guard = self.handler[self.handler.index(guard):self.handler.index("window.confirm(")]
        self.assertIn("return;", after_guard)

    def test_settings_direction_is_reset_on_switch_and_set_by_snapshot(self):
        switch = self.source[self.source.index("const handleSwitchDirection = useCallback("):]
        switch = switch[:switch.index("}, [direction]);")]
        self.assertIn("setSettingsDirection(null);", switch)
        apply = self.source[self.source.index("const applySnapshot = useCallback("):]
        apply = apply[:apply.index("const fetchSnapshot = useCallback(")]
        self.assertIn(
            "if (safe.direction_mode) setSettingsDirection(normalizeAuctionDirection(safe.direction_mode));",
            apply,
        )


if __name__ == "__main__":
    unittest.main()
