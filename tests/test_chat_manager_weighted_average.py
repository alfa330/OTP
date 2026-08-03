import ast
import textwrap
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"


def _load_metrics_reader():
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
        if isinstance(node, ast.FunctionDef)
        and node.name == "_load_chat_manager_metrics_by_operator_day_tx"
    )
    namespace = {}
    exec(
        compile(
            textwrap.dedent(ast.get_source_segment(source, method)),
            str(DATABASE_PATH),
            "exec",
        ),
        namespace,
    )
    return namespace["_load_chat_manager_metrics_by_operator_day_tx"]


class _RowsCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.query = None
        self.params = None

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchall(self):
        return list(self.rows)


class _FakeDatabase:
    _load_chat_manager_metrics_by_operator_day_tx = _load_metrics_reader()


class ChatManagerWeightedAverageTests(unittest.TestCase):
    def test_month_total_weights_each_rating_not_each_day(self):
        cursor = _RowsCursor([
            (20, date(2026, 7, 1), 0, 5.0, None, 0, 5.0, 1, 0, 0),
            (20, date(2026, 7, 2), 0, 4.0, None, 0, 36.0, 9, 0, 0),
        ])

        daily, totals = _FakeDatabase()._load_chat_manager_metrics_by_operator_day_tx(
            cursor,
            [20],
            date(2026, 7, 1),
            date(2026, 7, 31),
        )

        self.assertEqual(daily[20]["1"]["avg_score"], 5.0)
        self.assertEqual(daily[20]["2"]["avg_score"], 4.0)
        self.assertEqual(totals[20]["avg_score"], 4.1)
        self.assertNotEqual(totals[20]["avg_score"], 4.5)

    def test_legacy_average_only_day_has_weight_one(self):
        cursor = _RowsCursor([
            (20, date(2026, 7, 1), 0, 4.0, None, 0, 36.0, 9, 0, 0),
            (20, date(2026, 7, 2), 0, 5.0, None, 0, None, None, 0, 0),
        ])

        _daily, totals = _FakeDatabase()._load_chat_manager_metrics_by_operator_day_tx(
            cursor,
            [20],
            date(2026, 7, 1),
            date(2026, 7, 31),
        )

        self.assertEqual(totals[20]["avg_score"], 4.1)

    def test_month_total_keeps_raw_precision_for_salary_thresholds(self):
        cursor = _RowsCursor([
            (20, date(2026, 7, 1), 0, 4.895, None, 0, 489.5, 100, 0, 0),
        ])

        _daily, totals = _FakeDatabase()._load_chat_manager_metrics_by_operator_day_tx(
            cursor,
            [20],
            date(2026, 7, 1),
            date(2026, 7, 31),
        )

        self.assertAlmostEqual(totals[20]["avg_score"], 4.895, places=12)
        self.assertNotEqual(totals[20]["avg_score"], round(4.895, 2))


if __name__ == "__main__":
    unittest.main()
