# -*- coding: utf-8 -*-
"""Расчет ресурсов · Чат: модель, границы вводных и проводка раздела во фронте."""
import os
import re
import unittest
from contextlib import contextmanager
from datetime import date

from resource_fte.chat import (
    CHAT_ONLINE_STATUS_KEY,
    CHAT_RATES,
    CHAT_SHIFT_TEMPLATE_LABELS,
    CHAT_SETTINGS_LIMITS,
    DEFAULT_CHAT_SETTINGS,
    MAX_BASE_LOOKBACK_WEEKS,
    _base_week_starts,
    _covered_base_week_starts,
    _online_hours_tx,
    _reply_stats_tx,
    _week_start,
    build_chat_forecast,
    get_chat_shift_templates,
    resolve_chat_capacity,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JSX = os.path.join(REPO_ROOT, "src", "App.jsx")
CHAT_VIEW = os.path.join(REPO_ROOT, "src", "components", "resources", "ResourceChatFteView.jsx")
LINE_VIEW = os.path.join(REPO_ROOT, "src", "components", "resources", "ResourceFteView.jsx")


def _chat_config_region(source):
    """Чатовая часть общего компонента — между метками в ResourceFteView.jsx.

    После слияния (решение владельца 28.08.2026 «переиспользовать раздел линии,
    но под чат направление») оба направления живут в одном файле. Телефонные
    показатели в нём законны — они принадлежат линии. Поэтому стражи чата читают
    не весь файл, а объявленную чатовую область.
    """
    start = source.index("// >>> CHAT_DIRECTION_CONFIG_START")
    end = source.index("// <<< CHAT_DIRECTION_CONFIG_END")
    return source[start:end]


def _executable(region):
    """Та же область без комментариев.

    В комментариях чатовой области как раз объясняется, ЧЕГО в ней нет («не
    звонки», «Oktell не при чём»), поэтому запреты на слова проверяются только
    по исполняемым строкам — иначе страж ловил бы собственное объяснение.
    Строчных `//` внутри кода области нет: путь ручки один и без схемы.
    """
    return "\n".join(line for line in region.splitlines()
                      if not line.strip().startswith("//"))


def _chat_tabs_block(source):
    block = source[source.index("const VIEW_TABS_CHAT = ["):]
    return block[:block.index("];")]
CHAT_MODEL = os.path.join(REPO_ROOT, "resource_fte", "chat.py")
BACKEND = os.path.join(REPO_ROOT, "bot_schedule2.py")


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


class RecordingCursor:
    """Курсор, который ничего не отдаёт, но помнит, чем его позвали.

    Так проверяется САМ запрос — откуда берётся факт и по какой цели считается
    попадание, — не поднимая базу.
    """

    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(str(sql).split()), tuple(params or ())))

    def fetchone(self):
        return None

    def fetchall(self):
        return []


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

    def test_chat_has_only_two_rates(self):
        """В чате ставки только 1 и 0,75 — половинной нет (решение владельца)."""
        self.assertEqual(set(CHAT_RATES), {1.0, 0.75})
        self.assertEqual(set(CHAT_SHIFT_TEMPLATE_LABELS), {1.0, 0.75})
        payload = get_chat_shift_templates()
        self.assertEqual({item["rate"] for item in payload["templates"]}, {1.0, 0.75})
        self.assertEqual({item["rate"] for item in payload["rates"]}, {1.0, 0.75})

    def test_shift_templates_come_from_the_owner_file(self):
        """Смены чата — из боевого «График чат (6).xlsx», а не дефолты линии.

        У линии есть 7*16 и 9*18, которых в чате нет, а в чате есть 12*21,
        которого нет в дефолтах. Если наборы совпадут — значит взяли не тот.
        """
        from resource_fte.schedule_generation import DEFAULT_RESOURCE_SHIFT_TEMPLATE_LABELS
        chat_full = set(CHAT_SHIFT_TEMPLATE_LABELS[1.0])
        line_full = set(DEFAULT_RESOURCE_SHIFT_TEMPLATE_LABELS[1.0])
        self.assertIn("12*21", chat_full, "смена из файла владельца потеряна")
        self.assertNotIn("7*16", chat_full, "смены линии в чат попадать не должны")
        self.assertNotIn("9*18", chat_full, "смены линии в чат попадать не должны")
        self.assertNotEqual(chat_full, line_full)
        self.assertEqual(get_chat_shift_templates()["source"], "График чат (6).xlsx")

    def test_every_chat_shift_parses_and_has_sane_duration(self):
        for item in get_chat_shift_templates()["templates"]:
            hours = item["durationMinutes"] / 60
            if item["rate"] == 1.0:
                self.assertTrue(9 <= hours <= 12, f"{item['label']}: {hours} ч")
            else:
                self.assertAlmostEqual(hours, 6.5, places=1, msg=item["label"])

    def test_settings_limits_cover_every_default(self):
        """Границы нужны каждой ЧИСЛОВОЙ вводной.

        У отметки времени замера и у режима округления диапазона нет по природе, а
        коэффициенты кривой первого ответа до замера пусты — сравнивать None с числом
        нечего. Проверяем то, что действительно можно выйти за край опечаткой.
        """
        for key, value in DEFAULT_CHAT_SETTINGS.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            self.assertIn(key, CHAT_SETTINGS_LIMITS, f"нет границ для вводной {key}")
            low, high = CHAT_SETTINGS_LIMITS[key]
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
        """Заголовок и объяснение расчёта переехали в конфигурацию направления."""
        chat_region = _chat_config_region(_read(LINE_VIEW))
        self.assertIn("Расчет ресурсов · Чат", chat_region)
        self.assertIn("ответ внутри чата", chat_region)
        wrapper = _read(CHAT_VIEW)
        self.assertIn("Расчет ресурсов · Чат", wrapper)
        self.assertIn("ответ внутри чата", wrapper)

    def test_chat_reuses_the_ready_made_visual_and_planner(self):
        """Чат должен жить на готовом визуале и функционале линии, а не на своём.

        Иначе два раздела расходятся: у линии правят вкладки и планировщик,
        а чат остаётся с самописной страницей. Поэтому витрина чата — тонкая
        обёртка, а не вторая реализация: своих компонентов у неё нет вовсе.
        """
        wrapper = _read(CHAT_VIEW)
        self.assertIn("import ResourceFteView from './ResourceFteView'", wrapper)
        self.assertIn('direction="chat"', wrapper)
        # Ни состояния, ни запросов, ни своей вёрстки вкладок: всё, что обёртка
        # умеет, — отрисовать общий раздел с чужим направлением.
        for own in ("useState", "useEffect", "useMemo", "axios", "fetch(",
                    "localStorage", "<ResponsiveContainer", "<table", "<div",
                    "key:", "label:", "icon:", "'Обзор'", "'Прогнозы'"):
            self.assertNotIn(own, wrapper,
                             f"обёртка снова обрастает своей реализацией: {own}")
        self.assertEqual(wrapper.count("<ResourceFteView"), 1,
                         "обёртка должна рендерить общий раздел ровно один раз")
        code = _executable(wrapper)
        code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
        self.assertLessEqual(len([ln for ln in code.splitlines() if ln.strip()]), 25,
                             "обёртка перестала быть тонкой")
        self.assertFalse(
            os.path.exists(os.path.join(REPO_ROOT, "src", "components", "resources",
                                        "resourceChatShared.jsx")),
            "копия общих компонентов должна быть удалена, иначе дубли разъедутся")

        line = _read(LINE_VIEW)
        self.assertIn("import ResourceSchedulePlanner from './ResourceSchedulePlanner'", line)
        self.assertIn("<ResourceSchedulePlanner", line)
        chat_region = _chat_config_region(line)
        for tab in ("'Обзор'", "'Прогнозы'", "'Графики'", "'Настройки'"):
            self.assertIn(tab, chat_region, f"нет вкладки {tab}")

    def test_chat_templates_endpoint_serves_chat_set(self):
        """Ручка чата обязана отдавать ЧАТОВЫЕ шаблоны, а не набор линии."""
        backend = _read(os.path.join(REPO_ROOT, "bot_schedule2.py"))
        chunk = backend[backend.index("def api_resource_fte_chat_shift_templates"):]
        chunk = chunk[:chunk.index("@app.route", 10)]
        self.assertIn("get_chat_shift_templates()", chunk)
        self.assertNotIn("get_resource_shift_templates()", chunk)

    def test_chat_planner_points_at_chat_endpoints(self):
        """Без своего префикса планировщик чата сохранял бы график в линию."""
        source = _read(LINE_VIEW)
        chat_region = _chat_config_region(source)
        self.assertIn("const CHAT_API_PREFIX = '/api/resource_fte/chat'", chat_region)
        self.assertIn("apiPrefix: CHAT_API_PREFIX", chat_region)
        # Префикс уходит в планировщик и во ВСЕ общие запросы витрины, а не только туда.
        self.assertIn("apiPrefix={cfg.apiPrefix}", source)
        for endpoint in ("overview", "day/${date}", "settings", "recalculate",
                         "operator_availability", "analytics"):
            self.assertIn(f"${{cfg.apiPrefix}}/{endpoint}", source,
                          f"ручка {endpoint} не ходит по префиксу направления")
        # Жёсткий путь допустим только у того, чего у чата нет вовсе.
        line_only = ("/api/resource_fte/upload", "/api/resource_fte/sync_oktell",
                     "/api/resource_fte/oktell_billing")
        for chunk in source.split("`${apiRoot}/api/resource_fte")[1:]:
            path = "/api/resource_fte" + chunk.split("`")[0]
            self.assertTrue(any(path.startswith(item) for item in line_only),
                            f"жёсткий путь мимо направления: {path}")
        planner = _read(os.path.join(REPO_ROOT, "src", "components", "resources",
                                     "ResourceSchedulePlanner.jsx"))
        self.assertIn("apiPrefix = '/api/resource_fte'", planner,
                      "у планировщика должен быть префикс с дефолтом линии")
        self.assertNotIn("`${apiRoot}/api/resource_fte/", planner,
                         "в планировщике не должно остаться жёстких путей")

    def test_chat_has_its_own_tab_set_without_telephony(self):
        """У чата свой набор вкладок: «Звонки» и «Биллинг Oktell» ему чужие.

        Проверяем СПИСОК вкладок, а не весь файл: в комментарии их отсутствие
        как раз объясняется, и по всему тексту проверка ловила бы объяснение.
        Ключи сверяем поимённо: счётчик `key:` пропустил бы подмену вкладки —
        выкинуть «Чаты» и вернуть «Звонки» можно, не меняя их числа.
        """
        source = _read(LINE_VIEW)
        tabs_block = _chat_tabs_block(source)
        for forbidden in ("oktell", "Звонки", "Потери"):
            self.assertNotIn(forbidden.lower(), tabs_block.lower(),
                             f"вкладки «{forbidden}» в чате быть не должно")
        keys = re.findall(r"key: '([a-z_]+)'", tabs_block)
        core = ["overview", "next_week", "schedule_planner", "losses", "settings"]
        self.assertEqual([key for key in keys if key != "billing"], core,
                         f"набор и порядок вкладок чата изменился: {keys}")
        self.assertIn("label: 'Чаты'", tabs_block,
                      "вкладка аналитики чатов должна называться «Чаты»")
        # Ключи общие с линией: внешний переход initialDashboardView на 'losses'
        # обязан приводить на вкладку, а не в пустоту.
        line_keys = re.findall(r"key: '([a-z_]+)'", source[source.index("const VIEW_TABS = ["):]
                               [:source[source.index("const VIEW_TABS = ["):].index("];")])
        self.assertTrue(set(keys) <= set(line_keys),
                        f"у чата появился ключ вкладки, которого нет у линии: {keys}")

    def test_chat_billing_tab_is_tied_to_its_backend_routes(self):
        """Вкладка «Биллинг» у чата и её ручки включаются ТОЛЬКО вместе.

        Сервисный слой биллинга чата готов (`get_chat_billing_report` и соседи) и
        уже импортирован в bot_schedule2, но HTTP-маршрутов под него нет. Вкладка
        без маршрутов — кнопка «Сформировать», которая всегда отдаёт 404; признак
        `hasBilling: true` без маршрутов — то же самое. Обратная поломка не менее
        реальна: маршруты добавят, а вкладку включить забудут, и работа пропадёт.
        Поэтому сторожим совпадение, а не одну из сторон.
        """
        backend = _read(BACKEND)
        routes_ready = all(
            f"@app.route('/api/resource_fte/chat/{name}'" in backend
            for name in ("billing", "billing_operators", "billing_details", "billing_export")
        )
        chat_region = _chat_config_region(_read(LINE_VIEW))
        tabs_block = _chat_tabs_block(_read(LINE_VIEW))
        self.assertEqual("key: 'billing'" in tabs_block, routes_ready,
                         "вкладка «Биллинг» у чата и её маршруты должны появиться вместе")
        self.assertEqual("hasBilling: true" in chat_region, routes_ready,
                         "признак hasBilling должен совпадать с наличием маршрутов")
        # Сервисный слой не должен потеряться, пока маршруты ждут своей очереди.
        model = _read(CHAT_MODEL)
        for fn in ("def get_chat_billing_report", "def get_chat_billing_operators",
                   "def get_chat_billing_details"):
            self.assertIn(fn, model, "расчёт биллинга чата удалять нельзя — он готов")

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


class ChatCapacityDerivationTests(unittest.TestCase):
    """Ёмкость больше не вводится руками — она ВЫВОДИТСЯ из цели по сервису.

    Раньше «17 чатов в час» было числом из головы: поправить цель ответа было
    некому и незачем, расчёт её не замечал. Теперь цель — единственный рычаг,
    поэтому её связь с ёмкостью надо сторожить.
    """

    def test_default_goal_reproduces_the_calibrated_capacity(self):
        """Цель 5 минут по замеренной кривой даёт ~17,3 — то же, что считали руками."""
        value = resolve_chat_capacity(dict(DEFAULT_CHAT_SETTINGS))["value"]
        self.assertGreaterEqual(value, 17.2)
        self.assertLessEqual(value, 17.4)
        self.assertEqual(
            resolve_chat_capacity(dict(DEFAULT_CHAT_SETTINGS, target_reply_seconds=300))["value"],
            value,
        )

    def test_capacity_ignores_the_stored_number(self):
        """`capacity_per_hour` в настройках — кэш последнего вывода, а не вводная.

        Если подсунуть туда другое число и ёмкость поедет — значит рычагом снова
        стало ручное поле, и цель ответа опять декоративная.
        """
        derived = resolve_chat_capacity(dict(DEFAULT_CHAT_SETTINGS))["value"]
        spoofed = resolve_chat_capacity(dict(DEFAULT_CHAT_SETTINGS, capacity_per_hour=3.0))
        self.assertEqual(spoofed["value"], derived)
        self.assertEqual(spoofed["source"], "inside_chat")

    def test_capacity_grows_with_a_softer_goal(self):
        """Больше времени на ответ — больше чатов на человека. Иначе знак перепутан."""
        values = [
            resolve_chat_capacity(dict(DEFAULT_CHAT_SETTINGS, target_reply_seconds=seconds))["value"]
            for seconds in (120, 180, 240, 300, 360)
        ]
        for softer, tighter in zip(values[1:], values):
            self.assertGreater(softer, tighter, f"ёмкость не монотонна по цели: {values}")

    def test_manual_capacity_overrides_the_derived_one(self):
        """Ручное переопределение остаётся — но оно должно быть ВИДНЫМ (`source`)."""
        manual = resolve_chat_capacity(dict(DEFAULT_CHAT_SETTINGS, capacity_manual=9.0))
        self.assertAlmostEqual(manual["value"], 9.0, places=4)
        self.assertEqual(manual["source"], "manual")
        self.assertGreater(manual["inside_chat"], 17.0, "вывод из цели обязан остаться в ответе")
        back = resolve_chat_capacity(dict(DEFAULT_CHAT_SETTINGS, capacity_manual=None))
        self.assertEqual(back["source"], "inside_chat")

    def test_measured_first_reply_curve_binds_by_the_tighter_goal(self):
        """Из двух целей связывает жёсткая, и в `source` написано какая.

        Без этого нельзя объяснить в интерфейсе, откуда взялось число, — а
        необъяснимое число владелец правит вслепую.
        """
        tight = resolve_chat_capacity(
            dict(DEFAULT_CHAT_SETTINGS, first_reply_curve_a=1.0, first_reply_curve_b=0.2))
        self.assertEqual(tight["source"], "first_reply")
        self.assertAlmostEqual(tight["value"], min(tight["inside_chat"], tight["first_reply"]),
                               places=4)
        loose = resolve_chat_capacity(
            dict(DEFAULT_CHAT_SETTINGS, first_reply_curve_a=1.0, first_reply_curve_b=0.02))
        self.assertEqual(loose["source"], "inside_chat")
        self.assertAlmostEqual(loose["value"], loose["inside_chat"], places=4)

    def test_unreachable_first_reply_goal_is_reported_not_silently_clamped(self):
        """Замер уже показывал: 60 с не берутся ни при каком штате (было ~357 с).

        Если такую кривую молча зажать к нижней границе, модель начнёт требовать
        людей под недостижимую цель. Поэтому — признак наружу, рычаг прежний.
        """
        resolved = resolve_chat_capacity(
            dict(DEFAULT_CHAT_SETTINGS, first_reply_curve_a=5.9, first_reply_curve_b=0.05))
        self.assertTrue(resolved["first_reply_target_unreachable"])
        self.assertEqual(resolved["source"], "inside_chat")
        self.assertAlmostEqual(resolved["value"], resolved["inside_chat"], places=4)
        self.assertLess(resolved["first_reply"], 0,
                        "недостижимость должна быть видна числом, а не спрятана")

    def test_first_reply_curve_is_empty_until_measured(self):
        """Коэффициенты первого ответа берутся ЗАМЕРОМ, выдумывать их нельзя."""
        self.assertIsNone(DEFAULT_CHAT_SETTINGS["first_reply_curve_a"])
        self.assertIsNone(DEFAULT_CHAT_SETTINGS["first_reply_curve_b"])
        self.assertIsNone(DEFAULT_CHAT_SETTINGS["first_reply_curve_fitted_at"])
        self.assertEqual(resolve_chat_capacity(dict(DEFAULT_CHAT_SETTINGS))["first_reply"], None)


class ChatFactSourceTests(unittest.TestCase):
    """Откуда берутся ФАКТ и «в цель» — на этом раздел легко сделать декоративным."""

    def test_worked_hours_come_from_online_segments_not_from_the_shift_plan(self):
        """График говорит, кого поставили; онлайн-сегменты — кто был в строю.

        На графике смен разница между прогнозом и фактом обнуляется по построению,
        и вкладка «Прогнозы» начинает показывать идеальное покрытие.
        """
        cursor = RecordingCursor()
        _online_hours_tx(cursor, date(2026, 8, 1), date(2026, 8, 14))
        sql, params = cursor.calls[0]
        self.assertIn("operator_status_segments", sql)
        self.assertIn("s.status_key = %s", sql)
        self.assertEqual(CHAT_ONLINE_STATUS_KEY, "online")
        self.assertIn("online", params)
        for planned in ("work_schedules", "shift", "schedule_plan"):
            self.assertNotIn(planned, sql.lower(),
                             f"факт чатнико-часов не должен приходить из {planned}")

    def test_in_target_is_measured_against_the_first_reply_goal(self):
        """«В цель» в чате — про ПЕРВЫЙ ответ: только он лежит в нашей базе.

        Цель «ответа внутри чата» живёт в API Chat2Desk и работает рычагом
        ёмкости; мерить ею факт нечем.
        """
        cursor = RecordingCursor()
        _reply_stats_tx(cursor, date(2026, 8, 1), date(2026, 8, 14), 60)
        sql, params = cursor.calls[0]
        self.assertIn("reaction_time <= %s", sql)
        self.assertEqual(params[0], 60)
        self.assertNotIn("request_time", sql)

    def test_every_reply_call_passes_the_first_reply_goal(self):
        """Подставить сюда цель «ответа внутри чата» — и доля в цель станет липовой."""
        import ast
        tree = ast.parse(_read(CHAT_MODEL))
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == "_reply_stats_tx"]
        self.assertGreaterEqual(len(calls), 4, "вызовы статистики ответов пропали")
        for call in calls:
            dumped = ast.dump(call.args[-1])
            self.assertIn("target_first", dumped)
            self.assertNotIn("target_reply_seconds", dumped)

    def test_reaction_time_is_the_only_measure_of_the_reply(self):
        """Парная половина AST-стража: request_time запрещён, reaction_time обязателен."""
        source = _read(CHAT_MODEL)
        self.assertIn("reaction_time", source)

    def test_no_discount_for_unanswered_chats(self):
        """Обработки требуют 100 % чатов — в отличие от линии с её 5 % потерь.

        Чат не «теряется»: он висит открытым, пока клиент не получит ответ. Любой
        множитель доли принятых занизил бы потребность в людях.
        """
        import ast
        tree = ast.parse(_read(CHAT_MODEL))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    node.body = body[1:] or [ast.Pass()]
        code = ast.dump(tree).lower()
        for forbidden in ("answer_rate", "accepted", "lost_calls", "abandon"):
            self.assertNotIn(forbidden, code,
                             f"скидки на непринятые в чате нет — уберите {forbidden}")


class ChatBackendRoutesTests(unittest.TestCase):
    def test_new_chat_routes_are_declared_with_guard_and_preflight(self):
        """Ручка без гарда открыта всем, без preflight — не отвечает браузеру."""
        backend = _read(BACKEND)
        pairs = (
            ("/api/resource_fte/chat/day/<string:report_date>", "api_resource_fte_chat_day"),
            ("/api/resource_fte/chat/operator_availability",
             "api_resource_fte_chat_operator_availability"),
            ("/api/resource_fte/chat/analytics", "api_resource_fte_chat_analytics"),
            ("/api/resource_fte/chat/recalculate", "api_resource_fte_chat_recalculate"),
            ("/api/resource_fte/chat/fit_first_reply", "api_resource_fte_chat_fit_first_reply"),
        )
        for route, handler in pairs:
            self.assertIn(f"@app.route('{route}'", backend, f"нет ручки {route}")
            chunk = backend[backend.index(f"def {handler}"):]
            chunk = chunk[:chunk.index("@app.route", 10)]
            self.assertIn("_resource_fte_route_guard()", chunk,
                          f"{handler} должен проходить общий гард доступа")
            self.assertIn("_build_cors_preflight_response()", chunk,
                          f"{handler} должен отвечать на preflight")

    def test_chat_routes_stay_ahead_of_the_line_ones(self):
        """Порядок объявления решает, чья ручка поймает путь.

        Чатовые `saved_schedule` и `schedule_preview` обязаны стоять до линейных,
        иначе план чата уедет в линию.
        """
        backend = _read(BACKEND)
        order = [
            "@app.route('/api/resource_fte/chat/saved_schedule'",
            "@app.route('/api/resource_fte/chat/schedule_preview'",
            "@app.route('/api/resource_fte/schedule_preview'",
            "@app.route('/api/resource_fte/saved_schedule'",
        ]
        positions = [backend.index(item) for item in order]
        self.assertEqual(positions, sorted(positions), f"порядок блоков нарушен: {order}")
        # новые ручки встали до чатового saved_schedule и не разорвали пару блоков
        for handler in ("api_resource_fte_chat_analytics", "api_resource_fte_chat_recalculate"):
            self.assertLess(backend.index(f"def {handler}"), positions[0])

    def test_chat_overview_takes_both_periods(self):
        """Без периода истории вкладка «Обзор» показывала бы всегда одно окно."""
        backend = _read(BACKEND)
        chunk = backend[backend.index("def api_resource_fte_chat_overview"):]
        chunk = chunk[:chunk.index("@app.route", 10)]
        for arg in ("week_start", "period_end", "date_from", "date_to"):
            self.assertIn(f"'{arg}'", chunk, f"обзор чата не принимает {arg}")


class ChatFrontendMetricsTests(unittest.TestCase):
    """Витрина чата не должна обрасти телефонными показателями.

    Раздел один на два направления, поэтому «весь файл» больше не область
    проверки: AHT, OCC и минуты нагрузки в нём законны — они принадлежат линии.
    Сторожим две вещи: чатовую область конфигурации (там телефонного быть не
    может) и то, что КАЖДЫЙ телефонный показатель в разметке закрыт признаком
    возможностей направления, а не «если значение пустое» — пустое значение
    бывает и у линии в плохой день.
    """

    def test_no_telephony_metrics_in_the_chat_direction_config(self):
        """AHT, occupancy и utilization в чате не считаются и считаться не могут.

        Список вкладок это не ловит: карточку «AHT периода» можно положить в любую
        из них, и никто не заметит — она просто нарисует пустое место.
        """
        forbidden_words = (r"\bAHT\b", r"\bOCC\w*", r"\bUR\b",
                           r"occupanc\w*", r"utilizat\w*",
                           r"мин\w*\s+нагрузки", r"нагрузк\w*,\s*мин")
        # Ключи тумблеров и полей — но не имена признаков `hasAht: false`, которыми
        # направление как раз ОБЪЯВЛЯЕТ, что такого показателя у него нет.
        forbidden_keys = ("forecastKpiAht", "forecastTableAht", "forecastKpiOccUr",
                          "forecastKpiAnswerRate", "forecastChartWorkload",
                          "forecastTableWorkload", "aht_", "_aht")
        sources = {
            "конфигурация чата": _chat_config_region(_read(LINE_VIEW)),
            os.path.basename(CHAT_VIEW): _read(CHAT_VIEW),
        }
        for name, source in sources.items():
            for pattern in forbidden_words:
                self.assertIsNone(
                    re.search(pattern, source, flags=re.IGNORECASE),
                    f"{name}: телефонный показатель {pattern}",
                )
            for key in forbidden_keys:
                self.assertNotIn(key, source, f"{name}: мёртвый ключ линии {key}")

    def test_every_telephony_metric_is_gated_by_a_capability_flag(self):
        """Показатель, погашенный «по пустому значению», вернулся бы незамеченным.

        Проверенная ловушка: тултипы делят на answer_rate и на эффективные минуты,
        которых у чата нет вовсе, и рисуют честный ноль вместо «нет показателя».
        """
        line = _read(LINE_VIEW)
        gated = (
            "cfg.hasAht && displayOptions.forecastKpiAht",
            "cfg.hasAht && displayOptions.forecastTableAht",
            "cfg.hasAnswerRate && displayOptions.forecastKpiAnswerRate",
            "cfg.hasOccUr && displayOptions.forecastKpiOccUr",
            "cfg.hasWorkloadMinutes && displayOptions.forecastChartWorkload",
            "cfg.hasWorkloadMinutes && displayOptions.forecastTableWorkload",
        )
        for guard in gated:
            self.assertIn(guard, line, f"показатель не закрыт признаком: {guard}")
        for flag in ("hasAht", "hasOccUr", "hasAnswerRate", "hasWorkloadMinutes",
                     "hasLosses", "hasUpload", "hasOktellSync", "hasBilling",
                     "hasDirectionPicker", "hasShiftAuction"):
            self.assertIn(f"{flag}: false", _chat_config_region(line),
                          f"в конфигурации чата не выключен {flag}")

    def test_chat_view_counts_only_the_two_chat_rates(self):
        """Ставки чата — 1 и 0,75; общая ручка доступности приносит и 0,5.

        Если её не отфильтровать, карточка «Доступно» посчитает штат по ставкам,
        которых в направлении нет, и разойдётся с плашкой над ней. Набор берём из
        исходника и сверяем с моделью: два разных списка разъедутся молча.
        """
        line = _read(LINE_VIEW)
        chat_region = _chat_config_region(line)
        match = re.search(r"const CHAT_RATE_VALUES = \[([^\]]*)\]", chat_region)
        self.assertIsNotNone(match, "набор ставок чата должен быть объявлен списком")
        values = {float(item) for item in match.group(1).replace(" ", "").split(",") if item}
        self.assertEqual(values, {1.0, 0.75})
        self.assertEqual(values, set(CHAT_RATES), "фронт и модель разошлись по ставкам")
        self.assertIn("rates: CHAT_RATE_VALUES,", chat_region)
        # Отбор идёт по объявленному набору, и только у направления со списком
        # ставок: у линии `rates: null`, и весь этот путь для неё выключен.
        self.assertIn("isChatRate(rate)", line)
        self.assertIn("if (!cfg.rates) return null;", line)
        line_region = line[line.index("const LINE_DIRECTION = {"):line.index("const DIRECTION_CONFIG = {")]
        self.assertIn("rates: null,", line_region)

    def test_chat_never_calls_its_units_calls(self):
        """Единицы приходят из `cfg.unit`, а не переименовываются в разметке.

        Дерево рендера общее с линией, поэтому подпись легко остаётся телефонной:
        поля адаптера так и называются `forecast_calls`. На экране чата слова
        «звонки» быть не должно ни в одной подписи.
        """
        chat_code = _executable(_chat_config_region(_read(LINE_VIEW)))
        for word in ("звонк", "Звонк", "зв."):
            self.assertNotIn(word, chat_code, f"в подписях чата осталось «{word}»")
        self.assertIn("'чатов'", chat_code, "единицы чата должны быть объявлены явно")
        self.assertIn("cfg.unit.", _read(LINE_VIEW),
                      "подписи должны читаться из словаря направления")

    def test_chat_keeps_its_own_display_preferences(self):
        """Ключ линии тянет за собой мёртвые `forecastKpi*` телефонии.

        Переиспользовать его нельзя: у пользователя, ходившего на линию, чат
        поднялся бы с чужим набором колонок. Ключ обязан приходить из конфигурации
        направления и попадать И в чтение, И в запись — иначе первый же рендер
        чата затрёт настройки линии.
        """
        line = _read(LINE_VIEW)
        chat_region = _chat_config_region(line)
        self.assertIn(
            "const CHAT_DISPLAY_PREFERENCES_STORAGE_KEY = 'otp_resource_chat_display_v1'",
            chat_region)
        self.assertNotIn("otp_resource_fte_display_v1", chat_region)
        self.assertIn("const DISPLAY_PREFERENCES_STORAGE_KEY = 'otp_resource_fte_display_v1'",
                      line, "ключ линии сменился — сверьте оба раздела")
        self.assertIn("loadDisplayOptions(cfg.storageKey, cfg.defaultDisplayOptions)", line)
        self.assertIn("window.localStorage.setItem(cfg.storageKey", line)

    def test_chat_frontend_never_builds_a_date_through_utc(self):
        """`toISOString` уводит дату на день назад в Asia/Almaty.

        Проверено: сдвиг недели с 2026-08-24 давал 2026-08-30 вместо 2026-08-31.
        Даты собираются из getFullYear/getMonth/getDate — через `addDaysIso` и
        `toIsoDate`; у чата это включено признаком `localDates`.
        """
        line = _read(LINE_VIEW)
        self.assertNotIn("toISOString", _chat_config_region(line))
        self.assertNotIn("toISOString", _read(CHAT_VIEW))
        self.assertIn("localDates: true", _chat_config_region(line))
        self.assertIn("cfg.localDates ? toIsoDate(new Date()) : todayIso()", line)
        # стартовая неделя прогноза тоже идёт через readToday, иначе ночью она
        # уезжала бы на неделю назад ещё до первого запроса
        self.assertIn("getNextWeekStartIso(readToday())", line)
        self.assertIn("addDaysIso", line)
        self.assertIn("getFullYear()", line)

    def test_line_direction_keeps_its_current_behaviour(self):
        """Линия в проде: её ветка конфигурации обязана отдавать сегодняшние значения.

        Прежний страж запрещал само упоминание чата в этом файле. После решения
        владельца о едином компоненте он запрещал бы ровно то, что просили сделать,
        поэтому сторожим не отсутствие чата, а неизменность ЛИНИИ.
        """
        line = _read(LINE_VIEW)
        line_region = line[line.index("const LINE_DIRECTION = {"):]
        line_region = line_region[:line_region.index("const DIRECTION_CONFIG = {")]
        self.assertIn("apiPrefix: '/api/resource_fte',", line_region)
        self.assertIn("storageKey: DISPLAY_PREFERENCES_STORAGE_KEY,", line_region)
        self.assertIn("tabs: VIEW_TABS,", line_region)
        self.assertIn("displayGroups: DISPLAY_GROUPS,", line_region)
        self.assertIn("defaultDisplayOptions: DEFAULT_DISPLAY_OPTIONS,", line_region)
        self.assertIn("adaptOverview: (payload) => payload,", line_region)
        self.assertIn("adaptDay: (day) => day,", line_region)
        for flag in ("hasAht", "hasOccUr", "hasAnswerRate", "hasWorkloadMinutes",
                     "hasLosses", "hasUpload", "hasOktellSync", "hasBilling",
                     "hasDirectionPicker", "hasShiftAuction", "hasHistoryPairs",
                     "hasUpliftProjection", "hasActualPeakHours", "hasWeekPicker"):
            self.assertIn(f"{flag}: true", line_region,
                          f"у линии выключили возможность {flag} — это регрессия прода")
        # Подписи линии — ровно те слова, что стояли на экране до слияния. Дерево
        # рендера теперь общее и берёт их из `cfg.unit`, так что опечатка здесь
        # молча переименовала бы прод, и ни один тест разметки этого не увидел бы.
        for literal in ("title: 'Расчет ресурсов / FTE',",
                        "lossesTabTitle: 'Аналитика звонков',",
                        "historyPickerLabel: 'Период анализа',",
                        "manyCap: 'Звонки',", "many: 'звонков',", "short: 'зв.',",
                        "fteWord: 'FTE',", "fteHoursCap: 'FTE-часы периода',",
                        "dayFteCaption: 'FTE прогноз',", "operators: 'Операторы',"):
            self.assertIn(literal, line_region, f"подпись линии изменилась: {literal}")
        # Имена параметров и ключей графиков тоже прежние: разъедутся — линия
        # запросит другое окно и нарисует пустые ряды.
        self.assertIn(
            "overviewParams: { forecastFrom: 'forecast_date_from', forecastTo: 'forecast_date_to' },",
            line_region)
        self.assertIn(
            "trendKeys: { calls: 'chartCalls', forecastFte: 'chartFte', actualFte: 'chartActual' },",
            line_region)
        # Даты линии остаются прежними: переход на локальные компоненты чинит её
        # ночной дефект, но МЕНЯЕТ поведение, поэтому включается только у чата.
        self.assertIn("localDates: false", line_region)
        self.assertIn("direction = 'line',", line, "направление по умолчанию — линия")
        # Незнакомое значение пропа тоже обязано приводить на линию, а не ронять
        # раздел на `cfg.tabs` от undefined.
        self.assertIn("DIRECTION_CONFIG[direction] || DIRECTION_CONFIG.line", line)
        self.assertIn("const DIRECTION_CONFIG = { line: LINE_DIRECTION, chat: CHAT_DIRECTION }", line)

        # контрольные строки чужих тестов — сторожим их и отсюда, чтобы поломка
        # всплыла в наборе чата, а не в чужом
        self.assertIn("billingSlRatio >= 0.8 ? 'emerald'", line.replace("\n", " "))
        self.assertIn('<option value="general">Общая (текущая)</option>', line)
        self.assertIn("const slRatio = safeRatio(item.served_sl, item.arrived);", line)


PLANNER = os.path.join(REPO_ROOT, "src", "components", "resources", "ResourceSchedulePlanner.jsx")


class ChatPlannerBoundaryTests(unittest.TestCase):
    """Планировщик один на два направления — границы между ними держат эти стражи."""

    def test_shift_templates_are_stored_per_direction(self):
        """Общий ключ хранилища давал чату шаблоны ЛИНИИ.

        Наборы разные (в чате нет 7*16 и 9*18, зато есть 12*21), а на одном ключе
        чат читал закешированные шаблоны линии, до своей ручки не доходил вовсе
        и предлагал ставку 0,5, которой в чате не существует.
        """
        planner = _read(PLANNER)
        self.assertIn("const templateStorageKey = (apiPrefix) =>", planner)
        self.assertIn("loadStoredTemplates(apiPrefix)", planner)
        self.assertIn("storeTemplates(normalized, apiPrefix)", planner)
        self.assertIn("removeItem(templateStorageKey(apiPrefix))", planner)
        # Голого обращения к общему ключу не должно остаться нигде: единственные два
        # места — объявление константы и сам построитель ключа направления. Их вырезаем
        # и смотрим, что в остальном файле ключ не упоминается.
        start = planner.index("const templateStorageKey")
        end = planner.index(");", start) + 2
        rest = planner[:start] + planner[end:]
        rest = "\n".join(
            line for line in rest.splitlines() if "const TEMPLATE_STORAGE_KEY" not in line
        )
        self.assertNotIn(
            "TEMPLATE_STORAGE_KEY", rest,
            "общий ключ шаблонов используется напрямую — чат подхватит смены линии")

    def test_chat_does_not_touch_the_line_shift_auction(self):
        """Аукцион у чата будет ОТДЕЛЬНЫЙ — здешний трогать нельзя.

        Решение владельца 28.08.2026: после генерации график чата никуда не
        отправляется. Значит из чатового планировщика убраны и переключатель
        источника «Аукцион», и кнопка перехода, и запрос снапшота.
        """
        planner = _read(PLANNER)
        self.assertIn("enableShiftAuction = true,", planner)
        # Запрос к общему аукциону закрыт флагом.
        fetch_guard = planner[planner.index("if (!enableShiftAuction) return;"):]
        self.assertIn("coverageSource !== 'auction'", fetch_guard[:200])
        # Переключатель источника и кнопка перехода — тоже.
        self.assertIn("...(enableShiftAuction ? [['auction', 'Аукцион']] : [])", planner)
        self.assertIn("enableShiftAuction && typeof onOpenShiftAuction === 'function'", planner)

        line = _read(LINE_VIEW)
        self.assertIn("enableShiftAuction={cfg.hasShiftAuction}", line)
        chat_region = _chat_config_region(line)
        self.assertIn("hasShiftAuction: false", chat_region)
        # И ни одного прямого обращения к аукциону из чатовой части.
        self.assertNotIn("shift_auction", chat_region)
        self.assertNotIn("shift_auction", _read(CHAT_VIEW))


class ChatAuctionIsolationTests(unittest.TestCase):
    """Аукцион смен — только у линии, и граница держится НА СЕРВЕРЕ, а не только в интерфейсе."""

    def test_auction_period_queries_exclude_chat_plans(self):
        """Планы обоих направлений лежат в одной таблице.

        Без фильтра по direction_mode сохранённая неделя ЧАТА вставала в список периодов
        аукциона ЛИНИИ неотличимой строкой — те же даты, тот же пустой заголовок, — и
        оператор разбирал бы чатовые смены. Прежний страж проверял только .jsx, то есть
        направление «чат не ходит в аукцион», и эту — обратную — утечку не видел.
        """
        db = _read(os.path.join(REPO_ROOT, "database.py"))

        for fn in ("_get_shift_auction_available_periods_tx",
                   "_get_shift_auction_period_row_tx"):
            start = db.index(f"def {fn}")
            chunk = db[start:db.index("def ", start + 10)]
            self.assertIn("resource_saved_schedule_plans", chunk,
                          f"{fn}: выборка периодов должна читать планы")
            self.assertIn("COALESCE(p.direction_mode, 'line') = 'line'", chunk,
                          f"{fn}: аукциону линии видны только планы линии")

    def test_chat_saves_its_plan_under_its_own_direction(self):
        """Сохранение из чата обязано помечать план как чатовый — иначе фильтр выше бесполезен."""
        backend = _read(BACKEND)
        start = backend.index("def api_resource_fte_chat_saved_schedule")
        chunk = backend[start:backend.index("@app.route", start)]
        self.assertIn("direction_mode='chat'", chunk)


if __name__ == "__main__":
    unittest.main()
