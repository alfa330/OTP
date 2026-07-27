import ast
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
VIEW_PATH = ROOT / "src" / "components" / "wazzup" / "WazzupChatsView.jsx"


def _database_members():
    source = DATABASE_PATH.read_text(encoding="utf-8-sig")
    module = ast.parse(source)
    database_class = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    cte_node = next(
        node
        for node in database_class.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_WAZZUP_ANALYTICS_CTE"
            for target in node.targets
        )
    )
    method_node = next(
        node
        for node in database_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "wazzup_operator_analytics"
    )
    namespace = {}
    exec(textwrap.dedent(ast.get_source_segment(source, method_node)), namespace)
    return ast.literal_eval(cte_node.value), namespace["wazzup_operator_analytics"]


class _CursorContext:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _FakeCursor:
    def __init__(self):
        self.executions = []

    def execute(self, query, params=None):
        self.executions.append((" ".join(query.split()), params))

    def fetchall(self):
        return []

    def fetchone(self):
        return (12, 34, 56.0, 44.0, 7)


class WazzupAnalyticsPeriodTests(unittest.TestCase):
    def test_period_uses_almaty_midnights_and_daily_dialogs(self):
        cte, method = _database_members()
        cursor = _FakeCursor()

        class FakeDatabase:
            _WAZZUP_ANALYTICS_CTE = cte
            wazzup_operator_analytics = method

            def _get_cursor(self):
                return _CursorContext(cursor)

        result = FakeDatabase().wazzup_operator_analytics(
            date_from="2026-07-20",
            date_to="2026-07-26",
        )

        self.assertEqual(len(cursor.executions), 2)
        item_query, item_params = cursor.executions[0]
        summary_query, summary_params = cursor.executions[1]
        self.assertEqual(item_params, ["2026-07-20", "2026-07-26"])
        self.assertEqual(summary_params, ["2026-07-20", "2026-07-26"])
        self.assertIn("WHERE NOT m.is_deleted", item_query)
        self.assertIn(
            "m.dt >= (%s::date::timestamp AT TIME ZONE 'Asia/Almaty')",
            item_query,
        )
        self.assertIn(
            "m.dt < ((%s::date + 1)::timestamp AT TIME ZONE 'Asia/Almaty')",
            item_query,
        )
        self.assertIn(
            "COUNT(DISTINCT (local_date, channel_id, chat_id)) AS dialogs_count",
            item_query,
        )
        self.assertIn(
            "COUNT(DISTINCT (grp, local_date, channel_id, chat_id))",
            summary_query,
        )
        self.assertEqual(result["summary"]["chats"], 12)
        self.assertEqual(result["summary"]["messages"], 34)

    def test_presets_use_inclusive_day_count(self):
        source = VIEW_PATH.read_text(encoding="utf-8-sig")
        self.assertIn(
            "const presetStart = (days) => daysAgo(Math.max(0, days - 1));",
            source,
        )
        self.assertIn("useState(presetStart(30))", source)
        self.assertIn("from: presetStart(days)", source)


if __name__ == "__main__":
    unittest.main()
