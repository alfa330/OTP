"""Аукцион смен на двух направлениях: линия и чат.

Прогоны идут на ОБЩИХ таблицах и разделены колонкой ``direction_mode`` — ровно как
планы графиков. Здесь сторожатся границы, потеря которых означает, что один
аукцион читает или стирает данные второго, и правила чата: разбор смены по частям
и недельный потолок по ставке (1,0 → 40 ч, 0,75 → 30 ч).

Живой прогон на настоящем Postgres делался отдельно; эти тесты держат инварианты
в исходниках, чтобы правка не сняла их молча.
"""
import ast
import re
import unittest
from pathlib import Path
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"
VIEW_PATH = ROOT / "src" / "components" / "resources" / "ShiftAuctionView.jsx"

DATABASE_SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8-sig")
VIEW_SOURCE = VIEW_PATH.read_text(encoding="utf-8")
DATABASE_MODULE = source_cache.parse(DATABASE_SOURCE)


def _database_class():
    return next(
        node for node in DATABASE_MODULE.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )


def _method_source(name):
    method = next(
        node for node in _database_class().body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.get_source_segment(DATABASE_SOURCE, method)


def _module_function_source(name):
    function = next(
        node for node in DATABASE_MODULE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.get_source_segment(DATABASE_SOURCE, function)


def _exec_module_helpers():
    """Поднять чистые функции направления без импорта всего database.py."""
    namespace = {
        "re": re,
        "SHIFT_AUCTION_DIRECTION_NAME": "Основа",
        "SHIFT_AUCTION_DEPARTMENT_CODE": "szov",
        "SHIFT_AUCTION_CHAT_DIRECTION_PATTERN": "%чат%",
        "SHIFT_AUCTION_MODE_LINE": "line",
        "SHIFT_AUCTION_MODE_CHAT": "chat",
        "SHIFT_AUCTION_MODES": ("line", "chat"),
        "SHIFT_AUCTION_SETTINGS_ROW_ID": {"line": 1, "chat": 2},
    }
    for name in (
        "_normalize_shift_auction_scope_value",
        "normalize_shift_auction_mode",
        "shift_auction_settings_row_id",
        "shift_auction_direction_scope_sql",
        "shift_auction_mode_matches_direction",
    ):
        exec(_module_function_source(name), namespace)
    return namespace


class ShiftAuctionDirectionModeTests(unittest.TestCase):
    def test_unknown_direction_falls_back_to_the_line(self):
        """Опечатка в параметре обязана вести на линию, а не в пустой чат-прогон.

        Аукцион линии боевой: «не понял направление» не может означать отказ или
        подмену прогона.
        """
        ns = _exec_module_helpers()
        normalize = ns["normalize_shift_auction_mode"]

        self.assertEqual(normalize("chat"), "chat")
        self.assertEqual(normalize(" CHAT "), "chat")
        for value in ("line", "", None, "лиNия", "chatty", 0, [], "post_auction"):
            with self.subTest(value=value):
                self.assertEqual(normalize(value), "line")

    def test_settings_rows_are_separate_and_stable(self):
        ns = _exec_module_helpers()
        row_id = ns["shift_auction_settings_row_id"]
        # Линия остаётся первой строкой — это её исторические настройки в проде.
        self.assertEqual(row_id("line"), 1)
        self.assertEqual(row_id("chat"), 2)
        self.assertEqual(row_id("мусор"), 1)

    def test_operator_direction_keeps_the_department_boundary(self):
        """«ТП чат» живёт в отделе Тез и в аукцион СЗоВ попадать не должен."""
        ns = _exec_module_helpers()
        match = ns["shift_auction_mode_matches_direction"]

        self.assertEqual(match("Основа", "szov"), "line")
        self.assertEqual(match("Чат менеджер", "szov"), "chat")
        self.assertEqual(match(" чат менеджер ", "SZOV"), "chat")
        # Другой отдел — вне аукциона вовсе, даже если направление называется «чат».
        self.assertIsNone(match("ТП чат", "tez"))
        self.assertIsNone(match("Основа", "tez"))
        # Направление вне обоих наборов — тоже не участник.
        self.assertIsNone(match("Основа ОП", "szov"))
        self.assertIsNone(match("", "szov"))

    def test_every_shared_table_is_split_by_direction(self):
        """Таблицы у прогонов общие: без фильтра один затирает данные второго."""
        checks = {
            "update_shift_auction_test_access": (
                "DELETE FROM shift_auction_test_participants WHERE COALESCE(direction_mode, 'line') = %s",
                "DELETE FROM shift_auction_test_lots WHERE COALESCE(direction_mode, 'line') = %s",
                "DELETE FROM shift_auction_test_day_offs WHERE COALESCE(direction_mode, 'line') = %s",
            ),
            "restart_shift_auction_test": (
                "DELETE FROM shift_auction_test_lots WHERE COALESCE(direction_mode, 'line') = %s",
                "DELETE FROM shift_auction_test_day_offs WHERE COALESCE(direction_mode, 'line') = %s",
            ),
            "seed_shift_auction_test_lots": (
                "DELETE FROM shift_auction_test_lots WHERE COALESCE(direction_mode, 'line') = %s",
            ),
        }
        for method_name, fragments in checks.items():
            source = _method_source(method_name)
            for fragment in fragments:
                with self.subTest(method=method_name, fragment=fragment[:48]):
                    self.assertIn(fragment, source)

    def test_lot_reads_are_scoped_to_one_direction(self):
        for method_name in (
            "_get_shift_auction_lot_dates_tx",
            "_build_shift_auction_snapshot_common_tx",
            "get_shift_auction_test_export_data",
            "get_shift_auction_lots_for_planner_date",
        ):
            with self.subTest(method_name=method_name):
                source = _method_source(method_name)
                self.assertIn("direction_mode", source)
                self.assertRegex(source, r"COALESCE\(l?\.?direction_mode, 'line'\) = %s")

    def test_caches_are_kept_per_direction(self):
        """Один слот кеша на два прогона отдавал бы снимок линии на запрос чата."""
        for name in ("_shift_auction_snapshot_cache_slot", "_shift_auction_participant_cache_slot"):
            source = _module_function_source(name)
            self.assertIn("setdefault(", source)
            self.assertIn("normalize_shift_auction_mode(direction_mode)", source)

        invalidate = _module_function_source("_invalidate_shift_auction_runtime_caches")
        # None означает «оба направления»: этим пользуются места, меняющие данные обоих.
        self.assertIn("if direction_mode is None", invalidate)
        self.assertIn("SHIFT_AUCTION_MODES", invalidate)


class ChatPartialClaimTests(unittest.TestCase):
    """Чат разбирает смену по частям прямо в ходе аукциона."""

    def test_partial_claim_is_chat_only(self):
        source = _method_source("claim_shift_auction_test_lot")
        self.assertIn("wants_partial and mode != SHIFT_AUCTION_MODE_CHAT", source)
        self.assertIn('raise ValueError("PARTIAL_SHIFT_NOT_ALLOWED")', source)
        # Ответ на этот код у API должен быть человеческим, а не «ошибка аукциона».
        self.assertIn('"PARTIAL_SHIFT_NOT_ALLOWED"', BOT_SOURCE)

    def test_direction_is_derived_from_the_person_not_the_request(self):
        """Подменив параметр, оператор не должен попасть в чужой аукцион."""
        source = _method_source("claim_shift_auction_test_lot")
        self.assertIn("_shift_auction_mode_for_operator_tx(cursor, operator_id)", source)
        signature = source.split("\n")[0] + source.split("\n")[1]
        self.assertNotIn("direction_mode", signature)

        resolver_start = BOT_SOURCE.index("def _resolve_shift_auction_direction")
        resolver = BOT_SOURCE[resolver_start:BOT_SOURCE.index("def _requested_shift_auction_direction")]
        self.assertIn("_is_admin_role(role) or _is_supervisor_role(role)", resolver)
        self.assertIn("db.shift_auction_mode_for_operator(requester_id)", resolver)

    def test_lot_stays_open_while_a_free_gap_remains(self):
        """Иначе первый же взявший кусок закрыл бы смену для остальных."""
        source = _method_source("claim_shift_auction_test_lot")
        self.assertIn("keeps_lot_open = bool(is_partial_claim and remaining_after_claim)", source)
        self.assertIn("POST_AUCTION_MIN_REMAINDER_MINUTES", source)
        self.assertIn("SHIFT_AUCTION_CLAIM_STAGE_AUCTION", source)
        # Взятый кусок ложится отдельной строкой, а не переписывает лот.
        self.assertIn("INSERT INTO shift_auction_historical_claims", source)
        # Пересечение с чужим куском — отказ, а не тихое наложение.
        self.assertIn('raise ValueError("SHIFT_OVERLAPS_EXISTING")', source)

    def test_norm_counts_taken_parts_not_only_whole_lots(self):
        """Частично взятая смена оставляет лот 'available'.

        Пока норма считалась по одним claimed-лотам, такие часы не попадали в неё
        вовсе — оператор мог набрать сверх ставки, а день не считался занятым.
        """
        helper = _method_source("_get_shift_auction_operator_claimed_intervals_tx")
        self.assertIn("UNION ALL", helper)
        self.assertIn("shift_auction_historical_claims", helper)
        # Лот с частями не считается ещё и целиком — иначе двойной счёт.
        self.assertIn("NOT EXISTS (", helper)

        claim = _method_source("claim_shift_auction_test_lot")
        self.assertIn("_get_shift_auction_operator_claimed_intervals_tx", claim)
        self.assertIn('raise ValueError("SHIFT_NORM_EXCEEDED")', claim)

        day_off = _method_source("set_shift_auction_test_day_off")
        self.assertIn("_get_shift_auction_operator_claimed_intervals_tx", day_off)

    def test_weekly_norm_formula_gives_40_and_30_hours(self):
        """Потолок по ставке: 1,0 → 40 ч, 0,75 → 30 ч (постановка владельца).

        Отдельной константы нет — это та же норма, что и всегда:
        рабочие дни × 8 ч × ставка. Тест фиксирует, что формула даёт именно
        названные числа, чтобы её не «поправили» мимо требования.
        """
        namespace = {}
        exec(_method_source("_shift_auction_day_off_quota").replace("self, ", ""), namespace)
        exec(_method_source("_shift_auction_norm_workday_count")
             .replace("self, ", "").replace("self._shift_auction_day_off_quota", "_shift_auction_day_off_quota"),
             namespace)
        workdays = namespace["_shift_auction_norm_workday_count"](7, 0)
        self.assertEqual(workdays, 5, "в неделе с двумя выходными пять рабочих дней")
        self.assertEqual(round(workdays * 8 * 1.0), 40)
        self.assertEqual(round(workdays * 8 * 0.75), 30)
        self.assertEqual(round(workdays * 8 * 0.5), 20)

    def test_returning_a_taken_part_removes_only_your_own(self):
        source = _method_source("release_shift_auction_test_lot")
        self.assertIn("DELETE FROM shift_auction_historical_claims", source)
        self.assertIn("AND claimed_by = %s", source)
        self.assertIn("COALESCE(claim_stage, 'post_auction') = %s", source)
        self.assertIn("self.SHIFT_AUCTION_CLAIM_STAGE_AUCTION", source)

    def test_in_auction_parts_stay_out_of_my_extra_shifts_panel(self):
        """«Мои доп. смены» — про пост-аукционный добор с окном отмены 10 минут.

        Часть, взятая в самом аукционе, возвращается кнопкой «Вернуть», и в этой
        панели висела бы строкой с уже истёкшим окном.
        """
        source = _method_source("get_operator_recent_post_auction_claims")
        self.assertIn("COALESCE(hc.claim_stage, 'post_auction') = 'post_auction'", source)
        cancel = _method_source("operator_cancel_post_auction_claim")
        self.assertIn("COALESCE(claim_stage, 'post_auction') = 'post_auction'", cancel)

    def test_publishing_saves_taken_parts_and_not_whole_shifts(self):
        source = _method_source("publish_shift_auction_test_to_work_schedules")
        self.assertIn("UNION ALL", source)
        self.assertIn("hc.claimed_start_time, hc.claimed_end_time", source)
        # Лот с частями не публикуется ещё и целиком.
        self.assertIn("NOT EXISTS (", source)


class ChatAuctionFrontendTests(unittest.TestCase):
    def test_toggle_is_server_gated_and_operators_are_pinned(self):
        self.assertIn("const canSwitch = Boolean(safe.can_switch_direction);", VIEW_SOURCE)
        self.assertIn("setCanSwitchDirection(canSwitch);", VIEW_SOURCE)
        self.assertIn("{canSwitchDirection ? (", VIEW_SOURCE)
        # Оператору направление назначает сервер. Управляющему — НЕТ: у него есть
        # тумблер, и снапшот, приехавший после переключения, возвращал его на
        # прошлый прогон (см. tests/test_shift_auction_direction_toggle.py).
        self.assertIn(
            "if (safe.direction_mode && !canSwitch) setDirection(normalizeAuctionDirection(safe.direction_mode));",
            VIEW_SOURCE,
        )

    def test_switching_direction_resets_the_run_state(self):
        """Иначе на экране чата остались бы смены линии, а ETag отдал бы 304."""
        start = VIEW_SOURCE.index("const handleSwitchDirection")
        chunk = VIEW_SOURCE[start:VIEW_SOURCE.index("}, [direction]);", start)]
        for fragment in (
            "snapshotEtagRef.current = ''",
            "lastEventIdRef.current = 0",
            "setLots([])",
            "storeAuctionDirection(normalized)",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, chunk)

    def test_every_auction_request_carries_the_direction(self):
        """Запрос без направления молча уходит на линию — и чат видит чужой прогон."""
        self.assertIn("const withDirection = useCallback(", VIEW_SOURCE)
        # Поток событий переоткрывается при смене направления.
        self.assertIn("&direction=${encodeURIComponent(direction)}", VIEW_SOURCE)
        self.assertIn("}, [apiRoot, canOpenStream, direction, user?.id]);", VIEW_SOURCE)
        # Ни один вызов аукциона не должен остаться без direction.
        calls = re.findall(r"api/shift_auction/[a-z_/]+", VIEW_SOURCE)
        self.assertGreater(len(calls), 15, "вызовы аукциона не найдены — тест устарел")

    def test_partial_claim_modal_is_wired_to_the_chat_direction_only(self):
        self.assertIn("const supportsPartialClaim = direction === AUCTION_DIRECTION_CHAT;", VIEW_SOURCE)
        self.assertIn("if (supportsPartialClaim && !selection) {", VIEW_SOURCE)
        self.assertIn("auctionPartialClaimOptionsByLotId", VIEW_SOURCE)
        # Кнопка добора у оператора — тоже только в чате.
        self.assertIn("supportsPartialClaim && !canMonitor && canUseAuction && canClaim", VIEW_SOURCE)
        self.assertIn("Добрать часы", VIEW_SOURCE)

    def test_participant_picker_offers_only_the_direction_own_people(self):
        """В чат-аукционе выбирают из ЧАТНИКОВ, а не из операторов линии.

        Справочник сотрудников на фронте один на оба направления. Без передачи
        направления селектор показывал бы состав линии, сервер молча отбрасывал бы
        выбранных по своей границе — и выбор выглядел бы сохранённым, но не работал.
        """
        module = (ROOT / "src" / "components" / "resources" / "shiftAuctionParticipants.js").read_text(
            encoding="utf-8")
        self.assertIn("export const isShiftAuctionDirection = (value, directionMode", module)
        self.assertIn("SHIFT_AUCTION_CHAT_DIRECTION_TOKEN", module)

        # Направление доехало до всех трёх мест, где решается «свой ли это человек».
        self.assertIn(
            "normalizeShiftAuctionOperators(operators, settings.selected_operators, direction)",
            VIEW_SOURCE)
        self.assertIn(
            "normalizeShiftAuctionOperators(liveParticipants, monitoredOperators, direction)",
            VIEW_SOURCE)
        self.assertIn(
            "filterOperationalShiftAuctionOperators(resolvedMonitoredOperators, direction)",
            VIEW_SOURCE)

    def test_switching_direction_drops_the_previous_selection(self):
        """Отмеченные — люди другого направления; оставить их = отправить чужой состав."""
        start = VIEW_SOURCE.index("const handleSwitchDirection")
        chunk = VIEW_SOURCE[start:VIEW_SOURCE.index("}, [direction]);", start)]
        self.assertIn("setSelectedIds(new Set());", chunk)

    def test_partial_claims_count_as_my_shifts(self):
        """Часть смены не меняет lot.claimed_by — одного этого поля мало."""
        self.assertIn("const getMyAuctionClaimEntry = (lot, userId) =>", VIEW_SOURCE)
        self.assertIn("partial_claim: true", VIEW_SOURCE)
        self.assertIn(
            "const isAuctionLotPartiallyClaimed = (lot) => "
            "Boolean(lot?.post_auction_claimed || lot?.partial_claim);",
            VIEW_SOURCE,
        )
        self.assertIn(".map((lot) => getMyAuctionClaimEntry(lot, user?.id))", VIEW_SOURCE)


if __name__ == "__main__":
    unittest.main()
