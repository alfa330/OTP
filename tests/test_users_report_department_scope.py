"""Department isolation tests for the employee/operator Excel export."""

import ast
import copy
import re
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DATABASE_PATH = ROOT / "database.py"
FRONTEND_PATH = ROOT / "src" / "App.jsx"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _load_function(path, name, namespace):
    module = ast.parse(_read(path))
    node = next(
        item
        for item in module.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )
    node = copy.deepcopy(node)
    node.decorator_list = []
    result = dict(namespace)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), result)
    return result[name]


def _response_status(result):
    return result[1] if isinstance(result, tuple) else 200


class _UsersReportDB:
    def __init__(self, role, departments=None):
        self.role = role
        self.departments = dict(departments or {})
        self.generate_calls = []

    def get_user(self, *, id):
        return (int(id), None, f"User {id}", self.role)

    def get_department_by_id(self, department_id):
        return self.departments.get(int(department_id))

    def generate_users_report(self, **kwargs):
        self.generate_calls.append(kwargs)
        return "users_report.xlsx", b"PK-test"


class UsersReportEndpointScopeTests(unittest.TestCase):
    def _call(self, *, role, headed=(), query=None, departments=None):
        headed = {int(value) for value in headed}
        fake_db = _UsersReportDB(role, departments=departments)
        fake_request = SimpleNamespace(
            headers={"X-User-Id": "10"},
            args=dict(query or {}),
        )

        def is_global_admin(requester_role, _requester_id):
            normalized = str(requester_role or "").strip().lower()
            return normalized == "super_admin" or (normalized == "admin" and not headed)

        endpoint = _load_function(
            BOT_PATH,
            "get_users_report",
            {
                "BytesIO": BytesIO,
                "db": fake_db,
                "jsonify": lambda payload: payload,
                "logging": SimpleNamespace(error=lambda *_args, **_kwargs: None),
                "re": re,
                "request": fake_request,
                "send_file": lambda _stream, **kwargs: kwargs,
                "_normalize_user_role": lambda value: str(value or "").strip().lower(),
                "_is_global_admin_requester": is_global_admin,
                "_headed_department_ids": lambda _user_id: frozenset(headed),
                "_headed_department_id": lambda _user_id: min(headed) if headed else None,
            },
        )
        return endpoint(), fake_db

    def test_global_admin_can_select_one_active_department(self):
        result, fake_db = self._call(
            role="admin",
            query={"department_id": "8"},
            departments={8: {"id": 8, "name": "Sales", "is_active": True}},
        )

        self.assertEqual(_response_status(result), 200)
        self.assertEqual(fake_db.generate_calls[0]["department_ids"], [8])

    def test_global_admin_without_selection_keeps_all_departments(self):
        result, fake_db = self._call(role="admin")

        self.assertEqual(_response_status(result), 200)
        self.assertIsNone(fake_db.generate_calls[0]["department_ids"])

    def test_every_formal_department_head_role_is_scoped_to_all_headed_departments(self):
        for role in ("operator", "trainer", "sv", "admin"):
            with self.subTest(role=role):
                result, fake_db = self._call(
                    role=role,
                    headed={9, 7},
                    query={"department_id": "999"},
                )

                self.assertEqual(_response_status(result), 200)
                self.assertEqual(fake_db.generate_calls[0]["department_ids"], [7, 9])

    def test_super_admin_remains_global_when_also_heading_a_department(self):
        result, fake_db = self._call(
            role="super_admin",
            headed={7},
            query={"department_id": "8"},
            departments={8: {"id": 8, "is_active": True}},
        )

        self.assertEqual(_response_status(result), 200)
        self.assertEqual(fake_db.generate_calls[0]["department_ids"], [8])

    def test_plain_employee_cannot_generate_the_report(self):
        result, fake_db = self._call(role="operator")

        self.assertEqual(_response_status(result), 403)
        self.assertEqual(fake_db.generate_calls, [])

    def test_invalid_missing_and_inactive_admin_department_are_rejected(self):
        scenarios = (
            ("not-a-number", {}, 400),
            ("8", {}, 404),
            ("8", {8: {"id": 8, "is_active": False}}, 404),
        )
        for department_id, departments, expected_status in scenarios:
            with self.subTest(department_id=department_id, expected_status=expected_status):
                result, fake_db = self._call(
                    role="admin",
                    query={"department_id": department_id},
                    departments=departments,
                )
                self.assertEqual(_response_status(result), expected_status)
                self.assertEqual(fake_db.generate_calls, [])


class UsersReportImplementationContractTests(unittest.TestCase):
    def test_database_query_applies_a_bound_department_filter(self):
        source = _read(DATABASE_PATH)
        module = ast.parse(source)
        database_class = next(
            node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "Database"
        )
        method = next(
            node for node in database_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "generate_users_report"
        )
        method_source = ast.get_source_segment(source, method)

        self.assertIn("department_ids", [arg.arg for arg in method.args.args])
        self.assertIn("u.department_id = ANY(%s)", method_source)
        self.assertIn("query_params.append(normalized_department_ids)", method_source)
        self.assertIn("{department_filter_sql}", method_source)

    def test_frontend_exposes_admin_department_selection_and_sends_it(self):
        source = _read(FRONTEND_PATH)

        self.assertIn("departmentId: ''", source)
        self.assertIn("departmentId: isAdminLikeRole ? (manageUsersDeptFilter || '') : ''", source)
        self.assertIn("params.set('department_id', exportOptions.departmentId)", source)
        self.assertIn("Будут выгружены только операторы вашего отдела.", source)
        self.assertIn('<option value="">Все отделы</option>', source)


if __name__ == "__main__":
    unittest.main()
