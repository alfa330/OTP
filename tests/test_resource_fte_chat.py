# -*- coding: utf-8 -*-
"""Расчет ресурсов · Чат: модель, границы вводных и проводка раздела во фронте."""
import os
import re
import unittest
from contextlib import contextmanager
from datetime import date

from resource_fte.chat import (
    CHAT_SETTINGS_LIMITS,
    DEFAULT_CHAT_SETTINGS,
    MAX_BASE_LOOKBACK_WEEKS,
    _base_week_starts,
    _covered_base_week_starts,
    _week_start,
    build_chat_forecast,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JSX = os.path.join(REPO_ROOT, "src", "App.jsx")
CHAT_VIEW = os.path.join(REPO_ROOT, "src", "components", "resources", "ResourceChatFteView.jsx")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class FakeCursor:
    """Курсор, отдающий заранее заданные ответы по порядку типов запросов."""

    def __init__(self, covered_days, hourly_rows):
        self.covered_days = covered_days
        self.hourly_rows = hourly_rows
        self._result = []

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        if "SELECT MAX(day)" in text:
            self._result = [(max(self.covered_days),)] if self.covered_days else [(None,)]
        elif "SELECT DISTINCT day" in text:
            self._result = [(date.fromisoformat(item),) for item in sorted(self.covered_days)]
        elif "EXTRACT(HOUR FROM request_start)" in text:
            self._result = list(self.hourly_rows)
        else:
            self._result = []

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class FakeDb:
    def __init__(self, covered_days, hourly_rows):
        self.covered_days = covered_days
        self.hourly_rows = hourly_rows

    @contextmanager
    def _get_cursor(self):
        yield FakeCursor(self.covered_days, self.hourly_rows)


class ChatForecastModelTests(unittest.TestCase):
    def test_week_start_snaps_any_day_to_monday(self):
        for day in range(31, 32):
            self.assertEqual(_week_start(date(2026, 8, day)), date(2026, 8, 31))
        self.assertEqual(_week_start(date(2026, 9, 6)), date(2026, 8, 31))
        self.assertEqual(_week_start(date(2026, 9, 7)), date(2026, 9, 7))

    def test_base_weeks_are_the_weeks_before_the_target(self):
        self.assertEqual(
            _base_week_starts(date(2026, 8, 31), 2),
            [date(2026, 8, 24), date(2026, 8, 17)],
        )

    def test_incomplete_week_is_skipped_and_lookup_goes_deeper(self):
        """Главная ловушка: свежая неделя обрывается на середине.

        Если её не пропустить, понедельник считается по двум неделям, а пятница —
        по одной, и прогноз перекашивает.
        """
        import datetime as dt
        covered = set()
        for offset in range(0, 17):   # 10.08 … 26.08 — неделя 24.08 обрывается на 26-м
            covered.add((date(2026, 8, 10) + dt.timedelta(days=offset)).isoformat())
        # 24.08–26.08 есть, 27.08–30.08 нет → неделя 24.08 неполная
        chosen = _covered_base_week_starts(date(2026, 8, 31), 2, covered)
        self.assertEqual(chosen["used"], [date(2026, 8, 17), date(2026, 8, 10)])
        self.assertEqual([item["week_start"] for item in chosen["skipped"]], ["2026-08-24"])

    def test_without_any_complete_week_falls_back_instead_of_returning_nothing(self):
        chosen = _covered_base_week_starts(date(2026, 8, 31), 2, set())
        self.assertEqual(len(chosen["used"]), 2)
        self.assertEqual(chosen["used"][0], date(2026, 8, 24))

    def test_lookback_is_bounded(self):
        self.assertGreaterEqual(MAX_BASE_LOOKBACK_WEEKS, 4)
        self.assertLessEqual(MAX_BASE_LOOKBACK_WEEKS, 26)

    def _forecast_with(self, capacity, chats_per_hour=10):
        import datetime as dt
        covered, hourly = set(), []
        for week_start in (date(2026, 8, 17), date(2026, 8, 10)):
            for offset in range(7):
                day = week_start + dt.timedelta(days=offset)
                covered.add(day.isoformat())
                for hour in range(24):
                    hourly.append((day, hour, chats_per_hour))
        db = FakeDb(covered, hourly)
        settings = dict(DEFAULT_CHAT_SETTINGS, capacity_per_hour=capacity)
        return build_chat_forecast(db, "2026-08-31", settings)

    def test_requirement_is_volume_divided_by_capacity(self):
        forecast = self._forecast_with(capacity=10.0, chats_per_hour=10)
        day = forecast["days"][0]
        self.assertEqual(len(day["hourly_forecast"]), 24)
        for row in day["hourly_forecast"]:
            self.assertAlmostEqual(row["forecast_fte"], row["forecast_chats"] / 10.0, places=4)

    def test_lower_capacity_needs_more_people(self):
        loose = self._forecast_with(capacity=17.0)
        tight = self._forecast_with(capacity=8.5)
        self.assertGreater(
            tight["totals"]["operators_with_shrinkage"],
            loose["totals"]["operators_with_shrinkage"],
        )

    def test_no_average_handling_time_anywhere_in_the_model(self):
        """AHT в чатовой модели быть не должно — это её смысл.

        request_time меряет время жизни обращения, а не работу оператора, поэтому
        в РАСЧЁТЕ его быть не может. В комментариях — можно и нужно: там объясняется,
        почему именно, поэтому проверяем только исполняемый код.
        """
        import ast
        source = _read(os.path.join(REPO_ROOT, "resource_fte", "chat.py"))
        tree = ast.parse(source)
        # выкидываем докстринги, комментарии ast и так не хранит
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    node.body = body[1:] or [ast.Pass()]
        code = ast.dump(tree).lower()
        for forbidden in ("aht", "request_time", "occ", "'ur'"):
            self.assertNotIn(
                forbidden, code,
                f"в исполняемом коде чатовой модели не должно быть {forbidden}",
            )

    def test_settings_limits_cover_every_default(self):
        for key in DEFAULT_CHAT_SETTINGS:
            self.assertIn(key, CHAT_SETTINGS_LIMITS, f"нет границ для вводной {key}")
            low, high = CHAT_SETTINGS_LIMITS[key]
            value = DEFAULT_CHAT_SETTINGS[key]
            self.assertGreaterEqual(value, low)
            self.assertLessEqual(value, high)


class ChatSectionFrontendTests(unittest.TestCase):
    """Раздел должен быть доступен из МЕНЮ, а не только по прямому URL."""

    def test_menu_item_exists_in_both_role_branches(self):
        source = _read(APP_JSX)
        # см. память sidebar-item-must-be-in-two-branches: пункт объявляется
        # отдельно в админской ветке и в ветке главы отдела
        hits = source.count("handleSidebarViewNavigation(e, 'resource_fte_chat'")
        self.assertEqual(hits, 2, "пункт «Чат» должен быть в обеих ветках сайдбара")
        line_hits = source.count("handleSidebarViewNavigation(e, 'resource_fte'")
        self.assertEqual(line_hits, 2, "пункт «Линия» должен быть в обеих ветках сайдбара")

    def test_section_opens_a_chooser_like_the_account_menu(self):
        source = _read(APP_JSX)
        self.assertIn("showSidebarResourceDropdown", source)
        self.assertIn("handleToggleResourceDropdown", source)
        self.assertIn("stableSidebarHandleToggleResourceDropdown", source)
        self.assertEqual(
            source.count("aria-expanded={showSidebarResourceDropdown}"), 2,
            "у кнопки раздела в обеих ветках должен быть aria-expanded",
        )

    def test_dropdown_is_mutually_exclusive_with_the_other_two(self):
        """Иначе два меню висят открытыми одновременно и накладываются."""
        source = _read(APP_JSX)
        block = source[source.index("const handleToggleResourceDropdown"):]
        block = block[:block.index("};")]
        self.assertIn("setShowSidebarAccountDropdown(false)", block)
        self.assertIn("setShowSidebarEmployeesDropdown(false)", block)

    def test_chat_view_is_registered_and_gated_like_the_line_view(self):
        source = _read(APP_JSX)
        self.assertIn("resource_fte_chat: 'Resource FTE (chat)'", source)
        self.assertIn(
            "(view === 'resource_fte' || view === 'resource_fte_chat') && !canAccessResourceFteSection",
            source,
            "доступ к чату должен закрываться тем же гейтом, что и линия",
        )
        self.assertEqual(
            source.count('view === "resource_fte_chat" && canAccessResourceFteSection'), 2,
            "компонент должен рендериться в обеих ролевых ветках",
        )

    def test_chat_view_states_that_handling_time_is_not_used(self):
        source = _read(CHAT_VIEW)
        self.assertIn("Расчет ресурсов · Чат", source)
        self.assertIn("ответ внутри чата", source)

    def test_chat_reuses_the_ready_made_visual_and_planner(self):
        """Чат должен жить на готовом визуале и функционале линии, а не на своём.

        Иначе два раздела расходятся: у линии правят вкладки и планировщик,
        а чат остаётся с самописной страницей.
        """
        source = _read(CHAT_VIEW)
        self.assertIn("import ResourceSchedulePlanner from './ResourceSchedulePlanner'", source)
        self.assertIn("<ResourceSchedulePlanner", source)
        for tab in ("'Обзор'", "'Прогнозы'", "'Графики'", "'Настройки'"):
            self.assertIn(tab, source, f"нет вкладки {tab}")

    def test_chat_planner_points_at_chat_endpoints(self):
        """Без своего префикса планировщик чата сохранял бы график в линию."""
        source = _read(CHAT_VIEW)
        self.assertIn("const CHAT_API_PREFIX = '/api/resource_fte/chat'", source)
        self.assertIn("apiPrefix={CHAT_API_PREFIX}", source)
        planner = _read(os.path.join(REPO_ROOT, "src", "components", "resources",
                                     "ResourceSchedulePlanner.jsx"))
        self.assertIn("apiPrefix = '/api/resource_fte'", planner,
                      "у планировщика должен быть префикс с дефолтом линии")
        self.assertNotIn("`${apiRoot}/api/resource_fte/", planner,
                         "в планировщике не должно остаться жёстких путей")

    def test_chat_has_no_telephony_tabs(self):
        """«Звонки» и «Биллинг Oktell» к чату отношения не имеют.

        Проверяем СПИСОК вкладок, а не весь файл: в комментарии их отсутствие
        как раз объясняется, и по всему тексту проверка ловила бы объяснение.
        """
        source = _read(CHAT_VIEW)
        tabs_block = source[source.index("const VIEW_TABS = ["):]
        tabs_block = tabs_block[:tabs_block.index("];")]
        for forbidden in ("oktell", "Звонки", "Биллинг"):
            self.assertNotIn(forbidden.lower(), tabs_block.lower(),
                             f"вкладки «{forbidden}» в чате быть не должно")
        self.assertEqual(tabs_block.count("key:"), 4, "у чата ровно четыре вкладки")

    def test_saved_schedule_is_scoped_by_direction(self):
        """Планы линии и чата лежат в одной таблице — без фильтра они смешаются."""
        schema = _read(os.path.join(REPO_ROOT, "database.py"))
        self.assertIn("ADD COLUMN IF NOT EXISTS direction_mode", schema)
        self.assertIn("direction_mode VARCHAR(10) NOT NULL DEFAULT 'line'", schema)
        self.assertIn("AND direction_mode = %s", schema)
        backend = _read(os.path.join(REPO_ROOT, "bot_schedule2.py"))
        chunk = backend[backend.index("def api_resource_fte_chat_saved_schedule"):]
        chunk = chunk[:chunk.index("@app.route('/api/resource_fte/chat/schedule_preview'")]
        # считаем только исполняемые строки: в докстринге направление тоже упомянуто
        code_lines = [ln for ln in chunk.splitlines()
                      if "direction_mode='chat'" in ln and not ln.strip().startswith("#")
                      and '"""' not in ln and "Без `" not in ln]
        self.assertEqual(len(code_lines), 2,
                         "и чтение, и запись плана чата должны идти со своим направлением; "
                         f"нашлось: {code_lines}")

    def test_chat_view_uses_faicon_free_lucide_icons_only(self):
        """В разделе нет FaIcon — значит и незамапленных fa-токенов быть не может."""
        source = _read(CHAT_VIEW)
        self.assertNotIn("FaIcon", source)
        self.assertNotIn("fas fa-", source)

    def test_menu_icons_are_mapped_fa_tokens(self):
        """Немаппленный fa-токен молча рисуется кружком — проверяем оба новых."""
        source = _read(APP_JSX)
        for token in ("fas fa-headset", "fas fa-comments"):
            self.assertIn(token, source)

    def test_backend_routes_are_declared(self):
        backend = _read(os.path.join(REPO_ROOT, "bot_schedule2.py"))
        for route in (
            "/api/resource_fte/chat/overview",
            "/api/resource_fte/chat/settings",
            "/api/resource_fte/chat/schedule_preview",
        ):
            self.assertIn(route, backend, f"нет ручки {route}")
        for handler in (
            "def api_resource_fte_chat_overview",
            "def api_resource_fte_chat_settings",
            "def api_resource_fte_chat_schedule_preview",
        ):
            chunk = backend[backend.index(handler):]
            chunk = chunk[:chunk.index("@app.route", 10)]
            self.assertIn("_resource_fte_route_guard()", chunk,
                          f"{handler} должен проходить общий гард доступа")
            self.assertIn("_build_cors_preflight_response()", chunk,
                          f"{handler} должен отвечать на preflight")

    def test_chat_schedule_uses_the_same_generator_as_the_line(self):
        """Смены для чата строит тот же алгоритм — иначе разъедутся правила смен."""
        backend = _read(os.path.join(REPO_ROOT, "bot_schedule2.py"))
        chunk = backend[backend.index("def api_resource_fte_chat_schedule_preview"):]
        chunk = chunk[:chunk.index("@app.route('/api/resource_fte/schedule_preview'")]
        self.assertIn("_generate_schedule_preview_from_forecast", chunk)
        self.assertIn("_normalize_shift_templates", chunk)


class ChatSettingsTableTests(unittest.TestCase):
    def test_settings_table_is_created_with_guards(self):
        schema = _read(os.path.join(REPO_ROOT, "database.py"))
        self.assertIn("CREATE TABLE IF NOT EXISTS resource_chat_settings", schema)
        self.assertIn("resource_chat_settings_singleton", schema)
        self.assertIn("resource_chat_settings_capacity_check", schema)
        self.assertIn("INSERT INTO resource_chat_settings", schema)

    def test_settings_are_separate_from_the_line_table(self):
        """У линии и чата разные вводные; смешивать их в одной строке нельзя."""
        schema = _read(os.path.join(REPO_ROOT, "database.py"))
        chunk = schema[schema.index("CREATE TABLE IF NOT EXISTS resource_settings"):]
        chunk = chunk[:chunk.index("CREATE TABLE IF NOT EXISTS resource_chat_settings")]
        self.assertNotIn("capacity_per_hour", chunk)
        self.assertNotIn("target_reply_seconds", chunk)


if __name__ == "__main__":
    unittest.main()
