"""Behavioral permission tests for department-head employee editing.

The production bot module cannot be imported safely in unit tests because it
initializes external integrations at import time.  These tests follow the
existing suite's pattern: extract only the functions under test through AST
and supply isolated request/database doubles.
"""

import ast
import copy
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace


BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"


def _load_functions(*names, namespace=None):
    module = ast.parse(BOT_PATH.read_text(encoding="utf-8-sig"))
    by_name = {
        node.name: node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = set(names) - set(by_name)
    if missing:
        raise AssertionError(f"Missing functions in bot_schedule2.py: {sorted(missing)}")

    selected = []
    for name in names:
        node = copy.deepcopy(by_name[name])
        node.decorator_list = []
        selected.append(node)

    result = dict(namespace or {})
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"),
        result,
    )
    return result


def _user(user_id, role, *, telegram_id=None, supervisor_id=None):
    # Only indexes read by the permission functions are material here:
    # 0=id, 1=telegram_id, 2=name, 3=role, 6=supervisor_id.
    return (user_id, telegram_id, f"User {user_id}", role, None, None, supervisor_id)


def _response_status(result):
    if isinstance(result, tuple):
        return result[1]
    return 200


class _PermissionDB:
    def __init__(self, users=None, departments=None, directions=None, groups=None):
        self.users = dict(users or {})
        self.departments = dict(departments or {})
        self.directions = list(directions or [])
        self.groups = dict(groups or {})
        self.login_updates = []
        self.user_updates = []
        self.group_moves = []

    def get_user(self, *, id):
        return self.users.get(int(id))

    def get_user_department_id(self, user_id):
        return self.departments.get(int(user_id))

    def get_direction_department_id(self, direction_id):
        direction_id = int(direction_id)
        direction = next(
            (item for item in self.directions if int(item["id"]) == direction_id),
            None,
        )
        return direction.get("department_id") if direction else None

    def get_directions(self, department_id=None):
        result = [item for item in self.directions if item.get("is_active", True)]
        if department_id is not None:
            result = [
                item
                for item in result
                if int(item.get("department_id")) == int(department_id)
            ]
        return result

    def get_user_by_login(self, _login):
        return None

    def update_operator_login(self, user_id, supervisor_id, new_login):
        self.login_updates.append((user_id, supervisor_id, new_login))
        return True

    def update_user(self, user_id, field, value, *, changed_by):
        self.user_updates.append((user_id, field, value, changed_by))
        return True

    def get_group(self, group_id):
        return self.groups.get(int(group_id))

    def add_operator_to_group(self, group_id, operator_id, start_date=None, assigned_by=None):
        self.group_moves.append((int(group_id), int(operator_id), assigned_by))


class AuthenticatedLoginChangeTests(unittest.TestCase):
    def _call_change_login(self, headers):
        requester = _user(1, "operator")
        target = _user(2, "operator")
        fake_db = _PermissionDB(
            users={1: requester, 2: target},
            departments={1: 7, 2: 8},
        )
        fake_request = SimpleNamespace(
            headers=headers,
            get_json=lambda: {"user_id": 2, "new_login": "target-new-login"},
        )
        namespace = _load_functions(
            "_get_authenticated_requester",
            "change_login",
            namespace={
                "db": fake_db,
                "g": SimpleNamespace(user_id=1, auth_user=requester),
                "request": fake_request,
                "jsonify": lambda payload: payload,
                "_normalize_user_role": lambda role: str(role or "").strip().lower(),
                "_headed_department_id": lambda _user_id: None,
                "_is_super_admin_role": lambda role: role == "super_admin",
                "_is_admin_role": lambda role: role in ("admin", "super_admin"),
                "_requester_can_access_target_user": lambda *_args, **_kwargs: False,
                "requests": SimpleNamespace(post=lambda *_args, **_kwargs: None),
                "API_TOKEN": "test-token",
                "logging": logging,
            },
        )
        result = namespace["change_login"]()
        return result, fake_db

    def test_missing_user_id_header_does_not_turn_target_into_requester(self):
        result, fake_db = self._call_change_login(headers={})

        self.assertEqual(_response_status(result), 403)
        self.assertEqual(fake_db.login_updates, [])

    def test_mismatched_user_id_header_is_rejected(self):
        result, fake_db = self._call_change_login(headers={"X-User-Id": "2"})

        self.assertEqual(_response_status(result), 403)
        self.assertEqual(fake_db.login_updates, [])


class TargetUserScopeBehaviorTests(unittest.TestCase):
    def _build_scope_checker(self, *, headed_departments, departments, scopes=None):
        fake_db = _PermissionDB(departments=departments)
        headed_departments = dict(headed_departments)
        scopes = dict(scopes or {})

        def headed_department_id(user_id):
            raw = headed_departments.get(int(user_id))
            if isinstance(raw, (set, tuple, list, frozenset)):
                return next(iter(raw), None)
            return raw

        def headed_department_ids(user_id):
            raw = headed_departments.get(int(user_id))
            if raw is None:
                return set()
            if isinstance(raw, (set, tuple, list, frozenset)):
                return {int(value) for value in raw}
            return {int(raw)}

        namespace = _load_functions(
            "_target_user_supervisor_id",
            "_requester_can_access_target_user",
            namespace={
                "db": fake_db,
                "_normalize_user_role": lambda role: str(role or "").strip().lower(),
                "_headed_department_id": headed_department_id,
                "_headed_department_ids": headed_department_ids,
                "_department_scope_id_for_requester": lambda user_id: scopes.get(int(user_id)),
                "_is_super_admin_role": lambda role: role == "super_admin",
                "_is_admin_role": lambda role: role in ("admin", "super_admin"),
                "_is_supervisor_role": lambda role: role in ("sv", "supervisor"),
                "_is_global_admin_requester": lambda role, user_id: (
                    role in ("admin", "super_admin")
                    and (role == "super_admin" or headed_department_id(user_id) is None)
                ),
            },
        )
        return namespace["_requester_can_access_target_user"]

    def test_pure_supervisor_without_department_can_only_edit_direct_reports(self):
        can_access = self._build_scope_checker(
            headed_departments={},
            departments={},
            scopes={},
        )
        requester = _user(10, "sv")

        self.assertTrue(can_access(requester, 10, _user(20, "operator", supervisor_id=10)))
        self.assertFalse(can_access(requester, 10, _user(21, "operator", supervisor_id=11)))

    def test_formal_head_scope_overrides_every_supported_base_role(self):
        can_access = self._build_scope_checker(
            headed_departments={10: 7},
            departments={20: 7, 21: 8, 22: 7},
        )

        for requester_role in ("operator", "trainer", "sv", "admin"):
            requester = _user(10, requester_role)
            for target_role in ("operator", "trainee", "trainer", "sv"):
                with self.subTest(requester_role=requester_role, target_role=target_role):
                    self.assertTrue(
                        can_access(
                            requester,
                            10,
                            _user(20, target_role),
                            supervisor_target_roles=("operator", "trainee", "trainer", "sv"),
                        )
                    )
                    self.assertFalse(
                        can_access(
                            requester,
                            10,
                            _user(21, target_role),
                            supervisor_target_roles=("operator", "trainee", "trainer", "sv"),
                        )
                    )

            self.assertFalse(
                can_access(requester, 10, _user(22, "admin")),
                "A scoped head must not manage admin accounts",
            )

    def test_formal_head_can_manage_each_active_headed_department(self):
        can_access = self._build_scope_checker(
            headed_departments={10: {7, 9}},
            departments={20: 7, 21: 9, 22: 8},
        )
        requester = _user(10, "operator")

        self.assertTrue(can_access(requester, 10, _user(20, "operator")))
        self.assertTrue(can_access(requester, 10, _user(21, "operator")))
        self.assertFalse(can_access(requester, 10, _user(22, "operator")))


class ScopedRelationAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.requester = _user(10, "trainer")
        self.target = _user(20, "operator", supervisor_id=31)
        self.db = _PermissionDB(
            users={
                10: self.requester,
                20: self.target,
                30: _user(30, "sv"),
                31: _user(31, "sv"),
                32: _user(32, "operator"),
            },
            departments={10: 7, 20: 7, 30: 8, 31: 7, 32: 7},
            directions=[
                {"id": 40, "department_id": 8, "is_active": True},
                {"id": 41, "department_id": 7, "is_active": True},
                {"id": 42, "department_id": 7, "is_active": False},
            ],
            groups={
                50: {"id": 50, "status": "active", "department_id": 7},
                51: {"id": 51, "status": "active", "department_id": 8},
                52: {"id": 52, "status": "archived", "department_id": 7},
            },
        )

    def _load_namespace(self, request_payload=None, endpoint="admin_update_user"):
        normalize_role = lambda role: str(role or "").strip().lower()
        namespace = _load_functions(
            "_validate_scoped_user_relation_update",
            endpoint,
            namespace={
                "db": self.db,
                "request": SimpleNamespace(get_json=lambda: request_payload),
                "jsonify": lambda payload: payload,
                "logging": logging,
                "_get_authenticated_requester": lambda: (10, self.requester, None),
                "_normalize_user_role": normalize_role,
                "_headed_department_id": lambda user_id: 7 if int(user_id) == 10 else None,
                "_is_global_admin_requester": lambda _role, _user_id: False,
                "_is_super_admin_role": lambda role: normalize_role(role) == "super_admin",
                "_is_admin_role": lambda role: normalize_role(role) in ("admin", "super_admin"),
                "_is_supervisor_role": lambda role: normalize_role(role) in ("sv", "supervisor"),
                "_requester_can_access_target_user": lambda *_args, **_kwargs: True,
                "_is_supervisor_rate_change_day": lambda: True,
                "_is_valid_kz_phone": lambda _value: True,
                "normalize_proxy_status_value": lambda value: value,
            },
        )
        return namespace

    def test_relation_helper_accepts_only_active_same_department_values(self):
        validate = self._load_namespace()["_validate_scoped_user_relation_update"]

        self.assertIsNone(validate("trainer", 10, self.target, "supervisor_id", 31))
        self.assertIsNotNone(validate("trainer", 10, self.target, "supervisor_id", 30))
        self.assertIsNotNone(validate("trainer", 10, self.target, "supervisor_id", 32))
        self.assertIsNone(validate("trainer", 10, self.target, "direction_id", 41))
        self.assertIsNotNone(validate("trainer", 10, self.target, "direction_id", 40))
        self.assertIsNotNone(validate("trainer", 10, self.target, "direction_id", 42))

    def test_single_update_endpoint_rejects_foreign_relations_before_write(self):
        for field, value in (("direction_id", 40),):
            with self.subTest(field=field):
                self.db.user_updates.clear()
                namespace = self._load_namespace(
                    {"user_id": 20, "field": field, "value": value}
                )

                result = namespace["admin_update_user"]()

                self.assertEqual(_response_status(result), 403)
                self.assertEqual(self.db.user_updates, [])

    def test_single_update_endpoint_blocks_direct_supervisor_change(self):
        # СВ оператора — производное от его группы (каскад при смене группы/СВ
        # группы). Прямая правка запрещена даже для СВ своего отдела.
        for value in (30, 31):
            with self.subTest(value=value):
                self.db.user_updates.clear()
                namespace = self._load_namespace(
                    {"user_id": 20, "field": "supervisor_id", "value": value}
                )

                result = namespace["admin_update_user"]()

                self.assertEqual(_response_status(result), 400)
                self.assertEqual(self.db.user_updates, [])

    def test_single_update_endpoint_writes_same_department_relations(self):
        for field, value in (("direction_id", 41),):
            with self.subTest(field=field):
                self.db.user_updates.clear()
                namespace = self._load_namespace(
                    {"user_id": 20, "field": field, "value": value}
                )

                result = namespace["admin_update_user"]()

                self.assertEqual(_response_status(result), 200)
                self.assertEqual(self.db.user_updates, [(20, field, value, 10)])

    def test_bulk_update_endpoint_keeps_relation_assignments_in_department(self):
        for field, foreign_value, own_value in (
            ("direction_id", 40, 41),
        ):
            with self.subTest(field=field, scope="foreign"):
                self.db.user_updates.clear()
                namespace = self._load_namespace(
                    {"user_ids": [20], "changes": {field: foreign_value}},
                    endpoint="admin_bulk_update_users",
                )

                result = namespace["admin_bulk_update_users"]()
                payload = result[0]

                self.assertEqual(_response_status(result), 200)
                self.assertEqual(payload["updated_count"], 0)
                self.assertEqual(payload["failed_user_ids"], [20])
                self.assertEqual(self.db.user_updates, [])

            with self.subTest(field=field, scope="own"):
                self.db.user_updates.clear()
                namespace = self._load_namespace(
                    {"user_ids": [20], "changes": {field: own_value}},
                    endpoint="admin_bulk_update_users",
                )

                result = namespace["admin_bulk_update_users"]()
                payload = result[0]

                self.assertEqual(_response_status(result), 200)
                self.assertEqual(payload["updated_count"], 1)
                self.assertEqual(payload["failed_user_ids"], [])
                self.assertEqual(self.db.user_updates, [(20, field, own_value, 10)])

    def test_bulk_update_endpoint_rejects_supervisor_field(self):
        # supervisor_id массово не меняется — перевод идёт через group_id.
        namespace = self._load_namespace(
            {"user_ids": [20], "changes": {"supervisor_id": 31}},
            endpoint="admin_bulk_update_users",
        )

        result = namespace["admin_bulk_update_users"]()

        self.assertEqual(_response_status(result), 400)
        self.assertEqual(self.db.user_updates, [])
        self.assertEqual(self.db.group_moves, [])

    def test_bulk_update_endpoint_moves_operators_into_department_group(self):
        with self.subTest(scope="own"):
            self.db.group_moves.clear()
            namespace = self._load_namespace(
                {"user_ids": [20], "changes": {"group_id": 50}},
                endpoint="admin_bulk_update_users",
            )

            result = namespace["admin_bulk_update_users"]()
            payload = result[0]

            self.assertEqual(_response_status(result), 200)
            self.assertEqual(payload["updated_count"], 1)
            self.assertEqual(payload["failed_user_ids"], [])
            self.assertEqual(payload["applied_fields"], ["group_id"])
            self.assertEqual(self.db.group_moves, [(50, 20, 10)])
            # членство меняется через add_operator_to_group, не прямой записью в users
            self.assertEqual(self.db.user_updates, [])

        with self.subTest(scope="foreign"):
            self.db.group_moves.clear()
            namespace = self._load_namespace(
                {"user_ids": [20], "changes": {"group_id": 51}},
                endpoint="admin_bulk_update_users",
            )

            result = namespace["admin_bulk_update_users"]()

            self.assertEqual(_response_status(result), 403)
            self.assertEqual(self.db.group_moves, [])

        with self.subTest(scope="archived"):
            self.db.group_moves.clear()
            namespace = self._load_namespace(
                {"user_ids": [20], "changes": {"group_id": 52}},
                endpoint="admin_bulk_update_users",
            )

            result = namespace["admin_bulk_update_users"]()

            self.assertEqual(_response_status(result), 400)
            self.assertEqual(self.db.group_moves, [])

        with self.subTest(scope="non_operator_target"):
            self.db.group_moves.clear()
            namespace = self._load_namespace(
                {"user_ids": [31], "changes": {"group_id": 50}},
                endpoint="admin_bulk_update_users",
            )

            result = namespace["admin_bulk_update_users"]()
            payload = result[0]

            self.assertEqual(_response_status(result), 200)
            self.assertEqual(payload["updated_count"], 0)
            self.assertEqual(payload["failed_user_ids"], [31])
            self.assertEqual(self.db.group_moves, [])


class OperatorScopeHelpersTests(unittest.TestCase):
    """Скоуп глав отделов в хелперах звонков/оценок/графиков: глава нескольких
    отделов должен получать объединение возглавляемых отделов, а не только
    первый по алфавиту; супер-админ остаётся глобальным."""

    def _build_namespace(self, *function_names, headed_departments, departments, member_ids=None):
        headed_departments = {
            int(user_id): {int(value) for value in values}
            for user_id, values in dict(headed_departments).items()
        }
        departments = dict(departments)
        member_ids = {int(k): set(v) for k, v in dict(member_ids or {}).items()}
        normalize_role = lambda role: str(role or "").strip().lower()

        def headed_department_ids(user_id):
            return headed_departments.get(int(user_id), set())

        def headed_department_id(user_id):
            return next(iter(sorted(headed_department_ids(user_id))), None)

        fake_db = SimpleNamespace(
            get_user_department_id=lambda user_id: departments.get(int(user_id)),
            get_user=lambda *, id: _user(int(id), "operator"),
            get_department_member_ids=lambda department_id: member_ids.get(int(department_id), set()),
        )
        return _load_functions(
            "_operator_item_id",
            *function_names,
            namespace={
                "db": fake_db,
                "_normalize_user_role": normalize_role,
                "_headed_department_id": headed_department_id,
                "_headed_department_ids": headed_department_ids,
                "_department_scope_id_for_requester": lambda user_id: (
                    headed_department_id(user_id) or departments.get(int(user_id))
                ),
                "_is_super_admin_role": lambda role: normalize_role(role) == "super_admin",
                "_is_admin_role": lambda role: normalize_role(role) in ("admin", "super_admin"),
                "_is_supervisor_role": lambda role: normalize_role(role) in ("sv", "supervisor"),
                "_is_global_admin_requester": lambda role, user_id: (
                    normalize_role(role) in ("admin", "super_admin")
                    and (
                        normalize_role(role) == "super_admin"
                        or headed_department_id(user_id) is None
                    )
                ),
                "_trainer_work_schedule_member_ids": lambda: {901, 902},
            },
        )

    def test_authorize_operator_scope_covers_all_headed_departments(self):
        namespace = self._build_namespace(
            "_authorize_operator_scope",
            headed_departments={10: {7, 9}},
            departments={20: 7, 21: 9, 22: 8, 23: None},
        )
        authorize = namespace["_authorize_operator_scope"]
        requester = _user(10, "sv")

        self.assertTrue(authorize(requester, 10, 20))
        self.assertTrue(authorize(requester, 10, 21))
        self.assertFalse(authorize(requester, 10, 22))
        self.assertFalse(authorize(requester, 10, 23))

    def test_ensure_call_access_covers_all_headed_departments(self):
        namespace = self._build_namespace(
            "_ensure_call_access_for_requester",
            headed_departments={10: {7, 9}},
            departments={20: 7, 21: 9, 22: 8},
        )
        ensure = namespace["_ensure_call_access_for_requester"]
        requester = _user(10, "trainer")

        self.assertTrue(ensure(20, requester, 10))
        self.assertTrue(ensure(21, requester, 10))
        self.assertFalse(ensure(22, requester, 10))

    def test_super_admin_head_keeps_global_operator_scope(self):
        namespace = self._build_namespace(
            "_authorize_operator_scope",
            headed_departments={10: {7}},
            departments={22: 8},
        )
        authorize = namespace["_authorize_operator_scope"]

        self.assertTrue(authorize(_user(10, "super_admin"), 10, 22))

    def test_work_schedule_scope_unions_headed_departments(self):
        namespace = self._build_namespace(
            "_filter_operators_for_requester_scope",
            headed_departments={10: {7, 9}},
            departments={},
            member_ids={7: {20}, 9: {21}},
        )
        filter_scope = namespace["_filter_operators_for_requester_scope"]
        items = [{"id": 20}, {"id": 21}, {"id": 22}]

        # Глава двух отделов (даже с базовой ролью trainer) получает
        # объединение отделов, а не тренерский скоуп СЗоВ+ОП.
        filtered = filter_scope(_user(10, "trainer"), 10, items)
        self.assertEqual([item["id"] for item in filtered], [20, 21])

    def test_plain_trainer_keeps_trainer_work_schedule_scope(self):
        namespace = self._build_namespace(
            "_filter_operators_for_requester_scope",
            headed_departments={},
            departments={},
        )
        filter_scope = namespace["_filter_operators_for_requester_scope"]
        items = [{"id": 901}, {"id": 20}]

        filtered = filter_scope(_user(10, "trainer"), 10, items)
        self.assertEqual([item["id"] for item in filtered], [901])


if __name__ == "__main__":
    unittest.main()
