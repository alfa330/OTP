import ast
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DATABASE_PATH = ROOT / "database.py"
VIEW_PATH = ROOT / "src" / "components" / "technical" / "TechnicalIssuesView.jsx"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _function_source(path, function_name, class_name=None):
    source = _read(path)
    module = ast.parse(source)
    body = module.body
    if class_name:
        class_node = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        body = class_node.body
    function_node = next(
        node for node in body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return textwrap.dedent(ast.get_source_segment(source, function_node))


class TechnicalIssueBatchEditTests(unittest.TestCase):
    def test_api_exposes_scoped_batch_update(self):
        source = _function_source(BOT_PATH, "update_technical_issue_batch")

        self.assertIn("methods=['PUT', 'PATCH']", _read(BOT_PATH))
        self.assertIn("_effective_management_scope", source)
        self.assertIn("direction_ids=direction_ids", source)
        self.assertIn("scope_department_id=scope_department_id", source)

    def test_batch_update_rebuilds_all_rows_atomically(self):
        source = _function_source(
            DATABASE_PATH,
            "update_operator_technical_issue_batch",
            class_name="Database",
        )

        self.assertIn("WHERE ti.batch_id = %s::uuid", source)
        self.assertIn("direction_ids=selected_direction_ids", source)
        self.assertIn("_find_shift_overlap_intervals_for_technical_issue_tx", source)
        self.assertIn("DELETE FROM operator_technical_issues WHERE batch_id", source)
        self.assertIn("execute_values", source)
        self.assertIn("scope_department_id=scope_department_id", source)
        self.assertIn("requested_start_time", source)
        self.assertIn("requested_end_time", source)

    def test_massive_delete_removes_the_whole_batch(self):
        source = _function_source(
            DATABASE_PATH,
            "delete_operator_technical_issue",
            class_name="Database",
        )

        self.assertIn("delete_as_batch", source)
        self.assertIn("DELETE FROM operator_technical_issues WHERE batch_id", source)
        self.assertIn("'deleted_as_batch': delete_as_batch", source)

    def test_journal_groups_batches_and_offers_editing(self):
        source = _read(VIEW_PATH)

        self.assertIn("const buildJournalEntries = (rows) =>", source)
        self.assertIn("massiveByBatch.get(batchId)", source)
        self.assertIn("is_massive_group: true", source)
        self.assertIn("entry.requested_start_time", source)
        self.assertIn("Редактировать массовую техпричину", source)
        self.assertIn("axios.put(`${apiBaseUrl}/api/technical_issues/${editingId}`", source)


if __name__ == "__main__":
    unittest.main()
