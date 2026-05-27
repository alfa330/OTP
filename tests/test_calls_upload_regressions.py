import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
APP_PATH = ROOT / "src" / "App.jsx"


def _database_method_source(name):
    source = DATABASE_PATH.read_text(encoding="utf-8-sig")
    module = ast.parse(source)
    database_class = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    method = next(
        node for node in database_class.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.get_source_segment(source, method)


class CallsUploadRegressionTests(unittest.TestCase):
    def test_auto_daily_recalculation_preserves_imported_calls(self):
        source = _database_method_source("_recalculate_auto_daily_hours_tx")

        self.assertNotIn("calls = EXCLUDED.calls", source)
        self.assertIn("imported call counts must survive", source)

    def test_calls_preview_does_not_restrict_upload_to_selected_day(self):
        source = APP_PATH.read_text(encoding="utf-8-sig")

        self.assertNotIn("form.append('date', selectedDayUpload.dateStr);", source)
        self.assertNotIn("form.append('date', selectedDayUpload.dateStr)", source)

    def test_daily_fines_have_lookup_indexes_for_daily_hours_payloads(self):
        source = DATABASE_PATH.read_text(encoding="utf-8-sig")

        self.assertIn("idx_daily_fines_operator_day", source)
        self.assertIn("idx_daily_fines_daily_hours_id", source)


if __name__ == "__main__":
    unittest.main()
