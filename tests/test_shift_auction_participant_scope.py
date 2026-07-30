import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
FRONTEND_PATH = ROOT / "src" / "components" / "resources" / "ShiftAuctionView.jsx"
DATABASE_SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")
DATABASE_MODULE = ast.parse(DATABASE_SOURCE)


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
    def test_operational_row_predicate_requires_working_osnova(self):
        namespace = {
            "re": re,
            "SHIFT_AUCTION_ACTIVE_OPERATOR_STATUS": "working",
            "SHIFT_AUCTION_DIRECTION_NAME": "основа",
        }
        exec(_module_function_source("_normalize_shift_auction_scope_value"), namespace)
        exec(_module_function_source("_is_shift_auction_operational_participant_row"), namespace)
        predicate = namespace["_is_shift_auction_operational_participant_row"]

        self.assertTrue(predicate((1, "A", "operator", "working", 1, 70, " Основа ")))
        for status in ("bs", "unpaid_leave", "sick_leave", "annual_leave", "fired", ""):
            with self.subTest(status=status):
                self.assertFalse(predicate((1, "A", "operator", status, 1, 70, "Основа")))
        self.assertFalse(predicate((1, "A", "operator", "working", 1, 73, "Основа ОП")))

    def test_put_validates_exact_szov_osnova_but_keeps_temporary_statuses(self):
        source = _method_source("update_shift_auction_test_access")

        self.assertIn("JOIN directions d ON d.id = u.direction_id", source)
        self.assertIn("JOIN departments dep ON dep.id = d.department_id", source)
        self.assertIn("SHIFT_AUCTION_DIRECTION_NAME", source)
        self.assertIn("SHIFT_AUCTION_DEPARTMENT_CODE", source)
        self.assertIn("COALESCE(u.status, '') NOT IN ('fired', 'dismissal')", source)
        self.assertNotIn("COALESCE(u.status, '') = 'working'", source)

    def test_snapshot_access_and_cache_apply_the_same_selectable_scope(self):
        for method_name in (
            "_get_shift_auction_participant_ids_tx",
            "_build_shift_auction_snapshot_common_tx",
            "get_shift_auction_test_access",
        ):
            with self.subTest(method_name=method_name):
                source = _method_source(method_name)
                self.assertIn("SHIFT_AUCTION_DIRECTION_NAME", source)
                self.assertIn("SHIFT_AUCTION_DEPARTMENT_CODE", source)
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

    def test_favored_flag_is_derived_from_scoped_participants(self):
        source = _method_source("get_shift_auction_test_snapshot")

        self.assertIn("current_id in set(favored_operator_ids)", source)
        self.assertNotIn(
            "SELECT COALESCE(early_access, FALSE) FROM shift_auction_test_participants",
            source,
        )

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
        self.assertIn("SHIFT_AUCTION_DIRECTION_NAME", historical_source)
        self.assertIn("SHIFT_AUCTION_DEPARTMENT_CODE", historical_source)

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
