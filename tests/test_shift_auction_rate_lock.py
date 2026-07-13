import ast
import unittest
from datetime import time as dt_time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
ROUTES_PATH = ROOT / "bot_schedule2.py"
FRONTEND_PATH = ROOT / "src" / "components" / "resources" / "ShiftAuctionView.jsx"

DATABASE_SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")
DATABASE_MODULE = ast.parse(DATABASE_SOURCE)


def _database_class():
    return next(
        node for node in DATABASE_MODULE.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )


def _method(name):
    return next(
        node for node in _database_class().body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _method_source(name):
    return ast.get_source_segment(DATABASE_SOURCE, _method(name))


def _module_function_source(name):
    node = next(
        node for node in DATABASE_MODULE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.get_source_segment(DATABASE_SOURCE, node)


def _build_bucket_sandbox():
    """database.py is not importable on Windows (time.tzset), so the pure
    rate-bucket helpers are exec'ed standalone to be unit-tested for real."""
    namespace = {"dt_time": dt_time}
    exec(_module_function_source("_time_to_minutes"), namespace)

    class Holder:
        pass

    for method_name in (
        "_schedule_interval_minutes",
        "_shift_auction_lot_rate_bucket",
    ):
        method_namespace = dict(namespace)
        exec(_method_source(method_name), method_namespace)
        setattr(Holder, method_name, method_namespace[method_name])

    static_namespace = dict(namespace)
    # Drop the decorator line: exec of the bare function is enough here.
    static_source = _method_source("_shift_auction_rate_bucket").replace(
        "@staticmethod\n", ""
    )
    exec(static_source, static_namespace)
    Holder._shift_auction_rate_bucket = staticmethod(
        static_namespace["_shift_auction_rate_bucket"]
    )
    return Holder()


class ShiftAuctionRateBucketTests(unittest.TestCase):
    def setUp(self):
        self.db = _build_bucket_sandbox()

    def test_operator_rate_buckets(self):
        cases = {
            0.25: 0.5,
            0.5: 0.5,
            0.6: 0.75,
            0.75: 0.75,
            0.9: 1.0,
            1.0: 1.0,
            None: 0.5,
        }
        for rate, expected in cases.items():
            with self.subTest(rate=rate):
                self.assertEqual(self.db._shift_auction_rate_bucket(rate), expected)

    def test_lot_duration_buckets_match_frontend_grid(self):
        cases = [
            ("09:00", "13:00", 0.5),    # 4h
            ("09:00", "14:00", 0.5),    # 5h
            ("09:00", "14:30", 0.75),   # 5.5h boundary
            ("09:00", "15:30", 0.75),   # 6.5h
            ("09:00", "16:30", 1.0),    # 7.5h boundary
            ("08:00", "17:00", 1.0),    # 9h
            ("20:00", "08:00", 1.0),    # night 20*08 (overnight, 12h)
            ("17:00", "02:00", 1.0),    # overnight 9h
        ]
        for start, end, expected in cases:
            with self.subTest(start=start, end=end):
                self.assertEqual(
                    self.db._shift_auction_lot_rate_bucket(start, end),
                    expected,
                )


class ShiftAuctionRateLockContractTests(unittest.TestCase):
    def test_claim_checks_rate_lock_before_taking_the_lot(self):
        source = _method_source("claim_shift_auction_test_lot")
        self.assertIn("COALESCE(s.rate_lock_enabled, FALSE)", source)
        self.assertIn('raise ValueError("SHIFT_RATE_MISMATCH")', source)
        # The rejection must happen before the CAS update claims the row.
        self.assertLess(
            source.index("SHIFT_RATE_MISMATCH"),
            source.index("SET status = 'claimed'"),
        )

    def test_rate_lock_toggle_is_persisted_and_journaled(self):
        source = _method_source("set_shift_auction_test_rate_lock")
        self.assertIn("rate_lock_enabled = %s", source)
        self.assertIn("auction_rate_lock_updated", source)
        self.assertIn("FOR UPDATE", source)

    def test_rate_lock_event_invalidates_runtime_caches(self):
        source = _method_source("_insert_shift_auction_test_event")
        structural_start = source.index("structural_events")
        self.assertIn("auction_rate_lock_updated", source[structural_start:])

    def test_snapshots_expose_rate_lock_flag(self):
        for method_name in (
            "get_shift_auction_test_snapshot",
            "get_shift_auction_test_access",
        ):
            with self.subTest(method_name=method_name):
                source = _method_source(method_name)
                self.assertIn("COALESCE(rate_lock_enabled, FALSE)", source)
                self.assertIn('"rate_lock_enabled": rate_lock_enabled', source)

    def test_route_toggles_by_http_method_and_requires_manager(self):
        routes_source = ROUTES_PATH.read_text(encoding="utf-8-sig")
        route_start = routes_source.index("'/api/shift_auction/test_rate_lock'")
        route_slice = routes_source[route_start:route_start + 1600]
        self.assertIn("_is_shift_auction_manager", route_slice)
        self.assertIn("set_shift_auction_test_rate_lock", route_slice)
        self.assertIn("request.method == 'POST'", route_slice)
        self.assertIn("SHIFT_RATE_MISMATCH", routes_source)

    def test_frontend_blocks_foreign_rate_with_the_same_duration_rule(self):
        frontend_source = FRONTEND_PATH.read_text(encoding="utf-8")
        self.assertIn("settings.rate_lock_enabled", frontend_source)
        # The block reason must reuse the grid's duration-derived rate, not rate_min.
        block_start = frontend_source.index("if (settings.rate_lock_enabled) {")
        block_slice = frontend_source[block_start:block_start + 600]
        self.assertIn("getAuctionLotDurationRate(lot)", block_slice)
        self.assertNotIn("rate_min", block_slice)


if __name__ == "__main__":
    unittest.main()
