import ast
import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path
from typing import List, Optional


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"
APP_PATH = ROOT / "src" / "App.jsx"
SALARY_FORMULA_PATH = ROOT / "src" / "utils" / "salaryFormula.js"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _module_function_node(path, name):
    module = ast.parse(_read(path))
    return next(
        node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


def _module_function_source(path, name):
    source = _read(path)
    return ast.get_source_segment(source, _module_function_node(path, name))


def _database_method_source(name):
    source = _read(DATABASE_PATH)
    module = ast.parse(source)
    database_class = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    method = next(
        node
        for node in database_class.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return textwrap.dedent(ast.get_source_segment(source, method))


def _load_database_method(name):
    namespace = {"List": List, "Optional": Optional}
    exec(_database_method_source(name), namespace)
    return namespace[name]


class _CursorContext:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executions = []

    def execute(self, query, params=None):
        self.executions.append((query, params))

    def fetchall(self):
        return self.rows


class OperatorScoreAggregateTests(unittest.TestCase):
    def test_query_uses_latest_finalized_version_and_keeps_raw_average(self):
        method = _load_database_method("get_operator_score_aggregates_for_month")
        cursor = _FakeCursor([(17, 2, 94.995, 2)])

        class FakeDatabase:
            get_operator_score_aggregates_for_month = method

            def _get_cursor(self):
                return _CursorContext(cursor)

        result = FakeDatabase().get_operator_score_aggregates_for_month(
            "2026-07",
            ["17", 17, "invalid"],
        )

        self.assertEqual(len(cursor.executions), 1)
        query, params = cursor.executions[0]
        normalized_query = " ".join(query.split())
        self.assertIn(
            "SELECT DISTINCT ON (operator_id, phone_number, month, appeal_date)",
            normalized_query,
        )
        self.assertIn("month = %s AND is_draft = FALSE", normalized_query)
        self.assertIn("operator_id = ANY(%s)", normalized_query)
        self.assertIn(
            "ORDER BY operator_id, phone_number, month, appeal_date, created_at DESC, id DESC",
            normalized_query,
        )
        self.assertEqual(params, ("2026-07", [17]))
        self.assertEqual(
            result[17],
            {
                "call_count": 2,
                "avg_score": round(94.995, 2),
                "avg_score_raw": 94.995,
                "evaluation_row_count": 2,
                "has_evaluation_data": True,
            },
        )

    def test_empty_operator_scope_short_circuits_without_query(self):
        method = _load_database_method("get_operator_score_aggregates_for_month")

        class FakeDatabase:
            get_operator_score_aggregates_for_month = method

            def _get_cursor(self):
                raise AssertionError("database must not be queried for an empty id scope")

        result = FakeDatabase().get_operator_score_aggregates_for_month(
            "2026-07",
            [None, "invalid"],
        )
        self.assertEqual(result, {})


class _FakeLogger:
    def __init__(self):
        self.exceptions = []

    def exception(self, message, *args):
        self.exceptions.append((message, args))


def _load_salary_metrics_helper(database, logger=None):
    node = _module_function_node(BOT_PATH, "_get_operator_month_salary_metrics")
    namespace = {"db": database, "logging": logger or _FakeLogger()}
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), str(BOT_PATH), "exec"),
        namespace,
    )
    return namespace["_get_operator_month_salary_metrics"]


class OperatorHoursSalaryMetricsBackendTests(unittest.TestCase):
    def test_helper_prefers_raw_average_and_is_month_scoped(self):
        class FakeDatabase:
            def __init__(self):
                self.calls = []

            def get_operator_score_aggregates_for_month(self, **kwargs):
                self.calls.append(kwargs)
                return {
                    42: {
                        "call_count": 3,
                        "avg_score": 95.0,
                        "avg_score_raw": 94.995,
                    }
                }

        database = FakeDatabase()
        helper = _load_salary_metrics_helper(database)

        self.assertEqual(
            helper(42, "2026-07"),
            {
                "month": "2026-07",
                "quality_average": 94.995,
                "quality_evaluation_count": 3,
                "quality_available": True,
            },
        )
        self.assertEqual(
            database.calls,
            [{"month": "2026-07", "operator_ids": [42]}],
        )

    def test_zero_is_a_real_quality_value_but_missing_data_is_not(self):
        class FakeDatabase:
            payload = {7: {"call_count": 1, "avg_score_raw": 0.0}}

            def get_operator_score_aggregates_for_month(self, **_kwargs):
                return self.payload

        database = FakeDatabase()
        helper = _load_salary_metrics_helper(database)

        zero_quality = helper(7, "2026-07")
        self.assertTrue(zero_quality["quality_available"])
        self.assertEqual(zero_quality["quality_average"], 0.0)
        self.assertEqual(zero_quality["quality_evaluation_count"], 1)

        database.payload = {}
        self.assertEqual(
            helper(7, "2026-07"),
            {
                "month": "2026-07",
                "quality_average": None,
                "quality_evaluation_count": 0,
                "quality_available": False,
            },
        )

    def test_quality_failure_does_not_break_hours_payload_contract(self):
        class FailingDatabase:
            def get_operator_score_aggregates_for_month(self, **_kwargs):
                raise RuntimeError("temporary database failure")

        logger = _FakeLogger()
        helper = _load_salary_metrics_helper(FailingDatabase(), logger)

        self.assertEqual(
            helper(11, "2026-07"),
            {
                "month": "2026-07",
                "quality_average": None,
                "quality_evaluation_count": 0,
                "quality_available": False,
            },
        )
        self.assertEqual(len(logger.exceptions), 1)

    def test_operator_daily_hours_route_attaches_salary_metrics(self):
        route_source = _module_function_source(BOT_PATH, "sv_daily_hours")
        operator_branch_start = route_source.index("if role == 'operator':")
        operator_branch_end = route_source.index("if _is_admin_role(role):", operator_branch_start)
        operator_branch = route_source[operator_branch_start:operator_branch_end]

        self.assertIn("db.get_daily_hours_for_operator_month(requester_id, month)", operator_branch)
        self.assertIn('operator_obj["salary_metrics"]', operator_branch)
        self.assertIn("_get_operator_month_salary_metrics(", operator_branch)
        self.assertIn("operator_id=requester_id", operator_branch)
        self.assertIn("month=month", operator_branch)


@unittest.skipUnless(shutil.which("node"), "Node.js is required for JS helper runtime test")
class MonthlySalaryQualityRuntimeTests(unittest.TestCase):
    def test_month_match_zero_quality_and_stale_month(self):
        script = r"""
            import { resolveMonthlySalaryQuality } from './src/utils/salaryFormula.js';

            const resolved = {
                current: resolveMonthlySalaryQuality({
                    month: '2026-07',
                    quality_average: 94.995,
                    quality_evaluation_count: 3,
                    quality_available: true,
                }, '2026-07'),
                zero: resolveMonthlySalaryQuality({
                    month: '2026-07',
                    quality_average: 0,
                    quality_evaluation_count: 1,
                    quality_available: true,
                }, '2026-07'),
                stale: resolveMonthlySalaryQuality({
                    month: '2026-06',
                    quality_average: 99,
                    quality_evaluation_count: 4,
                    quality_available: true,
                }, '2026-07'),
                missing: resolveMonthlySalaryQuality({
                    month: '2026-07',
                    quality_average: null,
                    quality_evaluation_count: 0,
                    quality_available: false,
                }, '2026-07'),
            };
            process.stdout.write(JSON.stringify(resolved));
        """
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        result = json.loads(completed.stdout)

        self.assertEqual(
            result["current"],
            {"available": True, "count": 3, "quality": 94.995},
        )
        self.assertEqual(
            result["zero"],
            {"available": True, "count": 1, "quality": 0},
        )
        self.assertEqual(
            result["stale"],
            {"available": False, "count": 0, "quality": 0},
        )
        self.assertEqual(
            result["missing"],
            {"available": False, "count": 0, "quality": 0},
        )


class OperatorHoursSalaryFrontendContractTests(unittest.TestCase):
    def test_hours_salary_block_uses_monthly_metrics_not_evaluation_page_state(self):
        source = _read(APP_PATH)
        block_start = source.index("const operatorUserRowForSalary")
        block_end = source.index("// --- отображаем ---", block_start)
        salary_block = source[block_start:block_end]

        self.assertIn(
            "resolveMonthlySalaryQuality(op.salary_metrics, selectedMonth)",
            salary_block,
        )
        self.assertIn("available: hasSalaryQuality", salary_block)
        self.assertIn("!hasSalaryQuality ? 'качество' : null", salary_block)
        self.assertIn("hasSalaryQuality ? salaryQuality.toFixed(2) : ''", salary_block)
        self.assertNotIn("operatorData", salary_block)
        self.assertNotIn("salaryEvaluations", salary_block)

    def test_app_imports_the_shared_monthly_quality_resolver(self):
        source = _read(APP_PATH)
        self.assertIn(
            "calculateOperatorSalary, calculateChatSalary, resolveMonthlySalaryQuality",
            source,
        )
        helper_source = _read(SALARY_FORMULA_PATH)
        self.assertIn("export function resolveMonthlySalaryQuality(", helper_source)


if __name__ == "__main__":
    unittest.main()
