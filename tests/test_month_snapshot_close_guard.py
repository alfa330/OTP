import ast
import re
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_SOURCE = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")


def _load_recompute_method():
    source = DATABASE_PATH.read_text(encoding="utf-8-sig")
    module = ast.parse(source)
    database_class = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    method = next(
        node
        for node in database_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "recompute_month_snapshot"
    )
    namespace = {"re": re}
    exec(
        compile(
            textwrap.dedent(ast.get_source_segment(source, method)),
            str(DATABASE_PATH),
            "exec",
        ),
        namespace,
    )
    return namespace["recompute_month_snapshot"]


class _FakeDatabase:
    recompute_month_snapshot = _load_recompute_method()

    def __init__(self, closed):
        self.closed = closed
        self.freeze_calls = 0

    def _is_month_closed(self, _month):
        return self.closed

    def _get_cursor(self):
        outer = self

        class _CursorContext:
            def __enter__(self):
                return object()

            def __exit__(self, *_args):
                return False

        return _CursorContext()

    def _freeze_month_to_snapshots_tx(self, _cursor, month):
        self.freeze_calls += 1
        return {"month": month}


class MonthSnapshotCloseGuardTests(unittest.TestCase):
    def test_open_month_cannot_be_frozen(self):
        db = _FakeDatabase(closed=False)
        with self.assertRaisesRegex(ValueError, "Month is still open"):
            db.recompute_month_snapshot("2026-08")
        self.assertEqual(db.freeze_calls, 0)

    def test_closed_month_can_be_recomputed(self):
        db = _FakeDatabase(closed=True)
        self.assertEqual(db.recompute_month_snapshot("2026-06"), {"month": "2026-06"})
        self.assertEqual(db.freeze_calls, 1)

    def test_endpoint_returns_conflict_for_open_month(self):
        start = BOT_SOURCE.index("def recompute_month_snapshot_endpoint():")
        end = BOT_SOURCE.index("@app.route('/api/admin/snapshots'", start)
        block = BOT_SOURCE[start:end]
        self.assertIn("except ValueError as e:", block)
        self.assertIn('return jsonify({"error": str(e)}), 409', block)


if __name__ == "__main__":
    unittest.main()
