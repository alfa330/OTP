import ast
import json
import textwrap
import unittest
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"


def _database_method_source(name):
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
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return textwrap.dedent(ast.get_source_segment(source, method))


def _load_database_method(name):
    namespace = {
        "date": date,
        "json": json,
        "timedelta": timedelta,
    }
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
    def __init__(self, fetchone_values=None, fetchall_values=None):
        self.executions = []
        self._fetchone_values = iter(fetchone_values or [])
        self._fetchall_values = iter(fetchall_values or [])

    def execute(self, query, params=None):
        self.executions.append((query, params))

    def fetchone(self):
        return next(self._fetchone_values)

    def fetchall(self):
        return next(self._fetchall_values)


class ShiftSwapDepartmentScopeTests(unittest.TestCase):
    def test_candidate_query_is_scoped_to_requester_department(self):
        method = _load_database_method("get_shift_swap_candidates")
        cursor = _FakeCursor(
            fetchone_values=[(1, "operator", 10, "Основа", 42)],
            fetchall_values=[[]],
        )

        class FakeDatabase:
            get_shift_swap_candidates = method

            def _normalize_swap_time_range_for_date(self, **_kwargs):
                return {
                    "swapDateObj": date(2026, 7, 13),
                    "startMin": 9 * 60,
                    "endMin": 18 * 60,
                }

            def _get_cursor(self):
                return _CursorContext(cursor)

        result = FakeDatabase().get_shift_swap_candidates(
            requester_operator_id=1,
            swap_date="2026-07-13",
            start_time="09:00",
            end_time="18:00",
        )

        self.assertEqual(result, [])
        candidate_query, candidate_params = cursor.executions[1]
        self.assertIn("AND u.department_id = %s", candidate_query)
        self.assertEqual(candidate_params, (1, 42))

    def test_candidate_lookup_fails_closed_without_requester_department(self):
        method = _load_database_method("get_shift_swap_candidates")
        cursor = _FakeCursor(
            fetchone_values=[(1, "operator", 10, "Основа", None)],
        )

        class FakeDatabase:
            get_shift_swap_candidates = method

            def _normalize_swap_time_range_for_date(self, **_kwargs):
                return {
                    "swapDateObj": date(2026, 7, 13),
                    "startMin": 9 * 60,
                    "endMin": 18 * 60,
                }

            def _get_cursor(self):
                return _CursorContext(cursor)

        with self.assertRaisesRegex(ValueError, "не задан отдел"):
            FakeDatabase().get_shift_swap_candidates(
                requester_operator_id=1,
                swap_date="2026-07-13",
                start_time="09:00",
                end_time="18:00",
            )

        self.assertEqual(len(cursor.executions), 1)

    def test_direct_cross_department_request_is_rejected(self):
        method = _load_database_method("create_shift_swap_request")
        cursor = _FakeCursor(
            fetchall_values=[[
                (1, "operator", 10, "Инициатор", "working", "Основа", 42),
                (2, "operator", 11, "Кандидат", "working", "СМЗ", 99),
            ]],
        )

        class FakeDatabase:
            create_shift_swap_request = method

            def _normalize_swap_time_range_for_date(self, **_kwargs):
                return {
                    "swapDateObj": date(2026, 7, 13),
                    "endDateObj": date(2026, 7, 13),
                    "startMin": 9 * 60,
                    "endMin": 18 * 60,
                    "startTime": "09:00",
                    "endTime": "18:00",
                }

            def _swap_parse_payload_segments(self, *_args, **_kwargs):
                return []

            def _get_cursor(self):
                return _CursorContext(cursor)

        with self.assertRaisesRegex(ValueError, "из вашего отдела"):
            FakeDatabase().create_shift_swap_request(
                requester_operator_id=1,
                target_operator_id=2,
                swap_date="2026-07-13",
                start_time="09:00",
                end_time="18:00",
            )

        participant_query, _params = cursor.executions[0]
        self.assertIn("u.department_id", participant_query)
        self.assertIn("FOR SHARE OF u", participant_query)

    def test_accept_rechecks_departments_for_existing_pending_requests(self):
        method = _load_database_method("respond_shift_swap_request")
        cursor = _FakeCursor(
            fetchone_values=[
                (2, "operator"),
                (17, 1, 2, date(2026, 7, 13), date(2026, 7, 13), "pending", {}),
            ],
            fetchall_values=[[(1, 42), (2, 99)]],
        )

        class FakeDatabase:
            respond_shift_swap_request = method

            def _get_cursor(self):
                return _CursorContext(cursor)

        with self.assertRaises(ValueError):
            FakeDatabase().respond_shift_swap_request(
                request_id=17,
                responder_operator_id=2,
                action="accept",
            )

        department_query, department_params = cursor.executions[2]
        self.assertIn("SELECT id, department_id", department_query)
        self.assertIn("FOR SHARE", department_query)
        self.assertEqual(department_params, ([1, 2],))


if __name__ == "__main__":
    unittest.main()
