import ast
import unittest
from pathlib import Path
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
DATABASE_SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")
MODULE = source_cache.parse(DATABASE_SOURCE)
DATABASE_CLASS = next(
    node
    for node in MODULE.body
    if isinstance(node, ast.ClassDef) and node.name == "Database"
)


def _method_source(name):
    method = next(
        node
        for node in DATABASE_CLASS.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.get_source_segment(DATABASE_SOURCE, method)


class ChatScoreSqlContractTests(unittest.TestCase):
    def test_all_month_consumers_use_weighted_score_formula(self):
        method_names = (
            "get_operator_stats",
            "get_hours_summary",
            "_aggregate_month_from_daily_tx",
            "_aggregate_segment_from_daily_tx",
        )
        for method_name in method_names:
            with self.subTest(method=method_name):
                source = _method_source(method_name)
                self.assertIn("SUM(score_sum)", source)
                self.assertIn("SUM(score_count)", source)
                self.assertIn("COUNT(avg_score)", source)
                self.assertNotIn("AVG(avg_score)", source)


if __name__ == "__main__":
    unittest.main()
