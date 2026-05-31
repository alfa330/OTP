import ast
import unittest
from pathlib import Path


DATABASE_PATH = Path(__file__).resolve().parents[1] / "database.py"
BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"


def _source(path):
    return path.read_text(encoding="utf-8-sig")


def _database_class():
    module = ast.parse(_source(DATABASE_PATH))
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
    return ast.get_source_segment(_source(DATABASE_PATH), _method(name))


class PostAuctionRemainderTests(unittest.TestCase):
    def test_remainder_link_column_migration_exists(self):
        # The column linking a remainder lot to its parent must be created.
        self.assertIn(
            "ADD COLUMN IF NOT EXISTS remainder_of_lot_id INTEGER NULL",
            _source(DATABASE_PATH),
        )

    def test_remainder_helper_exists(self):
        names = {
            node.name for node in _database_class().body
            if isinstance(node, ast.FunctionDef)
        }
        self.assertIn("_create_post_auction_remainder_lots", names)
        self.assertIn("_break_overlaps_minute_range", names)

    def test_snapshot_exposes_claim_segments(self):
        # Single-lot model: the snapshot attaches the taken parts as claim_segments.
        db_source = _source(DATABASE_PATH)
        self.assertIn("AS claim_segments", db_source)
        self.assertIn('"claim_segments"', db_source)

    def test_partial_claim_keeps_single_lot(self):
        # No more separate remainder lots: post_auction_claim_lot must NOT split.
        method = _method("post_auction_claim_lot")
        calls = [
            node for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_create_post_auction_remainder_lots"
        ]
        self.assertEqual(len(calls), 0)
        # Instead it overlap-checks and only marks the lot claimed when fully covered.
        source = _method_source("post_auction_claim_lot")
        self.assertIn("SHIFT_OVERLAPS_EXISTING", source)
        self.assertIn("fully_covered", source)

    def test_unclaim_reconciles_remainder_lots(self):
        source = _method_source("admin_unclaim_shift")
        self.assertIn("remainder_of_lot_id", source)

    def test_journal_includes_post_auction_claims(self):
        source = _method_source("get_shift_auction_test_journal")
        self.assertIn("lot_post_auction_claimed", source)
        # Journal entries expose the claimed slice + a partial flag.
        self.assertIn("is_partial", source)
        self.assertIn("claim_start_time", source)

    def test_telegram_notification_shows_original_and_claimed_part(self):
        source = _source(BOT_PATH)
        self.assertIn("Взял часть", source)
        self.assertIn("is_partial", source)


class HistoricalPostAuctionRemainderTests(unittest.TestCase):
    """Remainder re-offering for already-published periods (e.g. the current week)."""

    def test_historical_claims_pk_includes_claimed_by(self):
        source = _source(DATABASE_PATH)
        # Old single-claim-per-shift constraint is dropped...
        self.assertIn(
            "DROP CONSTRAINT IF EXISTS shift_auction_historical_claims_pkey",
            source,
        )
        # ...and replaced by one keyed on the operator (disjoint partial claims).
        self.assertIn(
            "ADD PRIMARY KEY (plan_id, source_schedule_shift_id, claimed_by)",
            source,
        )

    def test_no_stale_two_column_conflict_target_remains(self):
        source = _source(DATABASE_PATH)
        # Every ON CONFLICT must now include claimed_by, or it would error at runtime.
        self.assertNotIn(
            "ON CONFLICT (plan_id, source_schedule_shift_id) DO NOTHING",
            source,
        )
        self.assertIn(
            "ON CONFLICT (plan_id, source_schedule_shift_id, claimed_by) DO NOTHING",
            source,
        )

    def test_saved_shift_claim_rejects_overlap_allows_disjoint(self):
        source = _method_source("post_auction_claim_saved_shift")
        # Gathers existing claims and rejects only on overlap (not mere existence).
        self.assertIn("existing_claims", source)
        self.assertIn("req_start_min", source)
        self.assertIn("ex_range", source)

    def test_preview_exposes_claim_segments_on_single_lot(self):
        preview = _method_source("get_shift_auction_period_preview")
        self.assertIn("claims_by_shift", preview)
        self.assertIn('lot["claim_segments"]', preview)

    def test_unclaim_targets_specific_operator(self):
        source = _method_source("admin_unclaim_shift")
        self.assertIn("target_claimed_by", source)
        # Delete is scoped to the operator so other partial claims survive.
        self.assertIn("AND claimed_by = %s", source)

    def test_admin_full_claim_guards_against_existing_claim(self):
        source = _method_source("admin_claim_shift_for_operator")
        self.assertIn("lot_id_int is None and resolved_plan_id and resolved_shift_id", source)

    def test_unclaim_endpoint_forwards_claimed_by(self):
        source = _source(BOT_PATH)
        self.assertIn("claimed_by=payload.get('claimed_by')", source)

    def test_consolidate_to_single_lot_on_init(self):
        names = {
            node.name for node in _database_class().body
            if isinstance(node, ast.FunctionDef)
        }
        self.assertIn("_consolidate_post_auction_lots", names)
        # Runs during schema init: drops legacy remainder lots, re-opens partial ones.
        self.assertIn("self._consolidate_post_auction_lots(cursor)", _source(DATABASE_PATH))
        src = _method_source("_consolidate_post_auction_lots")
        self.assertIn("DELETE FROM shift_auction_test_lots WHERE remainder_of_lot_id IS NOT NULL", src)
        self.assertIn("status = 'available'", src)


if __name__ == "__main__":
    unittest.main()
