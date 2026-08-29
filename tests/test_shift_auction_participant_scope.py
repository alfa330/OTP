import ast
import re
import unittest
from pathlib import Path
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
FRONTEND_PATH = ROOT / "src" / "components" / "resources" / "ShiftAuctionView.jsx"
DATABASE_SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")
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


class ShiftAuctionParticipantScopeTests(unittest.TestCase):
    def _operational_row_predicate(self):
        namespace = {
            "re": re,
            "SHIFT_AUCTION_ACTIVE_OPERATOR_STATUS": "working",
            "SHIFT_AUCTION_DIRECTION_NAME": "Основа",
            "SHIFT_AUCTION_MODE_LINE": "line",
            "SHIFT_AUCTION_MODE_CHAT": "chat",
        }
        exec(_module_function_source("_normalize_shift_auction_scope_value"), namespace)
        exec(_module_function_source("normalize_shift_auction_mode"), namespace)
        exec(_module_function_source("_is_shift_auction_operational_participant_row"), namespace)
        return namespace["_is_shift_auction_operational_participant_row"]

    def test_operational_row_predicate_requires_working_osnova(self):
        predicate = self._operational_row_predicate()

        self.assertTrue(predicate((1, "A", "operator", "working", 1, 70, " Основа ")))
        for status in ("bs", "unpaid_leave", "sick_leave", "annual_leave", "fired", ""):
            with self.subTest(status=status):
                self.assertFalse(predicate((1, "A", "operator", status, 1, 70, "Основа")))
        self.assertFalse(predicate((1, "A", "operator", "working", 1, 73, "Основа ОП")))
        # Направление по умолчанию — линия: чат-менеджер в её прогресс не попадает.
        self.assertFalse(predicate((1, "A", "operator", "working", 1, 69, "Чат менеджер")))

    def test_operational_row_predicate_switches_with_the_direction(self):
        predicate = self._operational_row_predicate()

        self.assertTrue(predicate((1, "A", "operator", "working", 1, 69, "Чат менеджер"), "chat"))
        self.assertTrue(predicate((1, "A", "operator", "working", 1, 76, " ТП Чат "), "chat"))
        # Линия в прогресс аукциона чата не заходит, и наоборот.
        self.assertFalse(predicate((1, "A", "operator", "working", 1, 70, "Основа"), "chat"))
        self.assertFalse(predicate((1, "A", "operator", "working", 1, 69, "Чат менеджер"), "line"))
        # Нераспознанный режим обязан вести на линию, а не открывать чужой прогон.
        self.assertTrue(predicate((1, "A", "operator", "working", 1, 70, "Основа"), "junk"))

    def test_direction_scope_sql_keeps_the_department_boundary(self):
        # Условие направления собрано в одном месте — проверяем сам генератор, а не
        # каждую его копию по методам.
        namespace = {
            "re": re,
            "SHIFT_AUCTION_DIRECTION_NAME": "Основа",
            "SHIFT_AUCTION_DEPARTMENT_CODE": "szov",
            "SHIFT_AUCTION_CHAT_DIRECTION_PATTERN": "%чат%",
            "SHIFT_AUCTION_MODE_LINE": "line",
            "SHIFT_AUCTION_MODE_CHAT": "chat",
        }
        exec(_module_function_source("_normalize_shift_auction_scope_value"), namespace)
        exec(_module_function_source("normalize_shift_auction_mode"), namespace)
        exec(_module_function_source("shift_auction_direction_scope_sql"), namespace)
        build = namespace["shift_auction_direction_scope_sql"]

        self.assertIn("SHIFT_AUCTION_DIRECTION_NAME = 'Основа'", DATABASE_SOURCE)

        line_sql, line_params = build("line")
        self.assertIn("BTRIM(COALESCE(d.name, '')) = %s", line_sql)
        self.assertNotIn("LOWER(BTRIM(COALESCE(d.name, ''))) = %s", line_sql)
        self.assertIn("LOWER(BTRIM(COALESCE(dep.code, ''))) = %s", line_sql)
        self.assertEqual(line_params, ("Основа", "szov"))

        chat_sql, chat_params = build("chat")
        self.assertIn("BTRIM(COALESCE(d.name, '')) ILIKE %s", chat_sql)
        # Граница отдела обязана остаться и у чата: «ТП чат» живёт в отделе Тез.
        self.assertIn("LOWER(BTRIM(COALESCE(dep.code, ''))) = %s", chat_sql)
        self.assertEqual(chat_params, ("%чат%", "szov"))

        # Неизвестный режим — линия, а не отсутствие фильтра.
        self.assertEqual(build("junk"), build("line"))

    def test_put_validates_exact_szov_osnova_but_keeps_temporary_statuses(self):
        source = _method_source("update_shift_auction_test_access")

        self.assertIn("shift_auction_direction_scope_sql(mode)", source)
        self.assertIn("{scope_sql}", source)
        self.assertIn("JOIN directions d ON d.id = u.direction_id", source)
        self.assertIn("JOIN departments dep ON dep.id = d.department_id", source)
        self.assertIn("COALESCE(u.status, '') NOT IN ('fired', 'dismissal')", source)
        self.assertNotIn("COALESCE(u.status, '') = 'working'", source)
        # Состав одного направления не должен стирать состав второго.
        self.assertIn(
            "DELETE FROM shift_auction_test_participants WHERE COALESCE(direction_mode, 'line') = %s",
            source,
        )

    def test_snapshot_access_and_cache_apply_the_same_selectable_scope(self):
        for method_name in (
            "_get_shift_auction_participant_ids_tx",
            "_build_shift_auction_snapshot_common_tx",
            "get_shift_auction_test_access",
        ):
            with self.subTest(method_name=method_name):
                source = _method_source(method_name)
                self.assertIn("shift_auction_direction_scope_sql(mode)", source)
                self.assertIn("{scope_sql}", source)
                self.assertIn("COALESCE(p.direction_mode, 'line') = %s", source)
                self.assertIn("NOT IN ('fired', 'dismissal')", source)
        self.assertIn(
            "_get_shift_auction_participant_ids_tx",
            _method_source("_get_shift_auction_participant_ids_cached_tx"),
        )

    def test_workloads_use_only_operational_participants(self):
        source = _method_source("_get_shift_auction_participant_workloads_tx")

        self.assertIn("_is_shift_auction_operational_participant_row", source)
        self.assertIn("operational_participant_rows", source)
        self.assertNotIn("for row in participant_rows:", source)

    def test_viewer_time_group_is_derived_from_the_active_week_only(self):
        source = _method_source("get_shift_auction_test_snapshot")

        # The group list covers every configurable week (the manager's form needs
        # it), so the viewer's own window must be filtered by the active plan.
        self.assertIn('group.get("plan_id") == active_plan_id', source)
        self.assertIn('current_id in (group.get("operator_ids") or [])', source)
        # Derived from the cached snapshot list — no per-operator query.
        self.assertNotIn("_get_shift_auction_operator_group_window_tx", source)

    def test_period_preview_returns_only_scoped_operator_ids(self):
        source = _method_source("get_shift_auction_period_preview")

        self.assertIn('"selected_operator_ids": sorted(operator_info.keys())', source)
        self.assertNotIn('"selected_operator_ids": operator_ids', source)

    def test_operator_mutations_recheck_scoped_participation(self):
        for method_name in (
            "claim_shift_auction_test_lot",
            "release_shift_auction_test_lot",
            "post_auction_claim_lot",
            "post_auction_claim_saved_shift",
            "set_shift_auction_test_day_off",
        ):
            with self.subTest(method_name=method_name):
                source = _method_source(method_name)
                self.assertIn("_get_shift_auction_participant_ids_tx", source)
                self.assertIn("NOT_TEST_PARTICIPANT", source)

        historical_source = _method_source("post_auction_claim_saved_shift")
        self.assertIn("JOIN departments dep ON dep.id = d.department_id", historical_source)
        self.assertIn("shift_auction_direction_scope_sql(mode)", historical_source)
        self.assertIn("{scope_sql}", historical_source)

    def test_operator_mutations_take_the_direction_from_the_person(self):
        # Режим НЕ приходит с клиента: иначе подменённым параметром можно было бы
        # действовать в чужом аукционе.
        for method_name in (
            "claim_shift_auction_test_lot",
            "release_shift_auction_test_lot",
            "post_auction_claim_lot",
            "set_shift_auction_test_day_off",
        ):
            with self.subTest(method_name=method_name):
                source = _method_source(method_name)
                self.assertIn("_shift_auction_mode_for_operator_tx(cursor, operator_id)", source)
                self.assertNotIn("direction_mode=SHIFT_AUCTION_MODE_LINE)", source.split("\n")[0])

        # Пост-аукционный добор адресован опубликованной неделе — режим берётся с неё.
        saved_shift_source = _method_source("post_auction_claim_saved_shift")
        self.assertIn("_shift_auction_mode_for_plan_tx(cursor, schedule_plan_id)", saved_shift_source)

    def test_frontend_guards_dirty_selection_and_uses_operational_rows(self):
        source = FRONTEND_PATH.read_text(encoding="utf-8")

        self.assertIn("auctionDraftDirtyRef.current", source)
        self.assertIn("shouldHydrateShiftAuctionDraft", source)
        self.assertIn("markAuctionDraftDirty", source)
        self.assertIn("updateDraftNote", source)
        self.assertIn("updateDraftSchedulePlanId", source)
        self.assertIn("operators={operationalMonitoredOperators}", source)
        self.assertIn("workloads={operationalMonitoredParticipantWorkloads}", source)


if __name__ == "__main__":
    unittest.main()
