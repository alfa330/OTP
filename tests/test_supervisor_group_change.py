"""Задача #228: супервайзер меняет оператору ГРУППУ, а не НАПРАВЛЕНИЕ.

Боевой модуль бота импортировать в тестах нельзя (на импорте поднимаются
интеграции), поэтому — как и в соседних наборах — функции вытаскиваются через
AST, а окружение подменяется дублями.
"""

import ast
import copy
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests import source_cache


BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"


def _load_functions(*names, namespace=None):
    module = source_cache.parse(BOT_PATH.read_text(encoding="utf-8-sig"))
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


def _user(user_id, role, *, supervisor_id=None):
    # Значимы только читаемые индексы: 0=id, 3=role, 6=supervisor_id.
    return (user_id, None, f"User {user_id}", role, None, None, supervisor_id)


def _status(result):
    return result[1] if isinstance(result, tuple) else 200


def _payload(result):
    return result[0] if isinstance(result, tuple) else result


class _GroupsDB:
    def __init__(self, *, users, departments, groups, headed=None):
        self.users = dict(users)
        self.departments = dict(departments)
        self.groups = dict(groups)
        self.headed = dict(headed or {})
        self.adds = []
        self.removes = []
        self.user_updates = []

    # — пользователи —
    def get_user(self, *, id):
        return self.users.get(int(id))

    def get_user_department_id(self, user_id):
        return self.departments.get(int(user_id))

    def headed_department_id_for_user(self, user_id):
        return self.headed.get(int(user_id))

    def update_user(self, user_id, field, value, *, changed_by):
        self.user_updates.append((user_id, field, value, changed_by))
        return True

    def get_directions(self, department_id=None):
        return [{"id": 41, "department_id": 7, "is_active": True}]

    # — группы —
    def get_group(self, group_id):
        return self.groups.get(int(group_id))

    def add_operator_to_group(self, group_id, operator_id, start_date=None, assigned_by=None):
        self.adds.append((int(group_id), int(operator_id), assigned_by))

    def remove_operator_from_group(self, group_id, operator_id, end_date=None):
        self.removes.append((int(group_id), int(operator_id)))


def _groups_db(headed=None):
    return _GroupsDB(
        users={
            10: _user(10, "sv"),
            20: _user(20, "operator", supervisor_id=10),
            21: _user(21, "operator", supervisor_id=11),
            22: _user(22, "sv"),
        },
        departments={10: 7, 20: 7, 21: 8, 22: 7},
        groups={
            50: {"id": 50, "status": "active", "department_id": 7},
            51: {"id": 51, "status": "active", "department_id": 8},
            52: {"id": 52, "status": "archived", "department_id": 7},
        },
        headed=headed,
    )


class SupervisorMovesOperatorBetweenGroupsTests(unittest.TestCase):
    """Точечный перевод оператора в другую группу доступен обычному СВ."""

    def _call(self, group_id, request_payload, *, db=None, headed_dept=None):
        db = db if db is not None else _groups_db()
        requester = db.users[10]
        normalize_role = lambda role: "sv" if str(role or "").strip().lower() == "supervisor" \
            else str(role or "").strip().lower()
        namespace = _load_functions(
            "_ensure_group_operator_manager",
            "_is_plain_supervisor_requester",
            "_ensure_group_in_supervisor_scope",
            "_ensure_group_in_requester_scope",
            "_requester_can_access_target_user",
            "_target_user_supervisor_id",
            "add_group_operator_endpoint",
            namespace={
                "db": db,
                "request": SimpleNamespace(get_json=lambda: request_payload),
                "jsonify": lambda payload: payload,
                "logging": logging,
                "_get_authenticated_requester": lambda: (10, requester, None),
                "_normalize_user_role": normalize_role,
                "_headed_department_id": lambda user_id: headed_dept,
                "_headed_department_ids": lambda user_id: ({headed_dept} if headed_dept else set()),
                "_is_global_admin_requester": lambda _role, _user_id: False,
                "_is_super_admin_role": lambda role: normalize_role(role) == "super_admin",
                "_is_admin_role": lambda role: normalize_role(role) in ("admin", "super_admin"),
                "_is_supervisor_role": lambda role: normalize_role(role) == "sv",
                "_department_scope_id_for_requester": lambda user_id: db.departments.get(int(user_id)),
            },
        )
        return db, namespace["add_group_operator_endpoint"](group_id)

    def test_supervisor_moves_own_department_operator_into_active_group(self):
        db, result = self._call(50, {"operator_id": 20})

        self.assertEqual(_status(result), 200)
        self.assertEqual(db.adds, [(50, 20, 10)])

    def test_supervisor_cannot_move_into_other_department_group(self):
        db, result = self._call(51, {"operator_id": 20})

        self.assertEqual(_status(result), 403)
        self.assertEqual(db.adds, [])

    def test_supervisor_cannot_move_into_archived_group(self):
        db, result = self._call(52, {"operator_id": 20})

        self.assertEqual(_status(result), 400)
        self.assertEqual(db.adds, [])

    def test_supervisor_cannot_move_operator_of_other_department(self):
        db, result = self._call(50, {"operator_id": 21})

        self.assertEqual(_status(result), 403)
        self.assertEqual(db.adds, [])

    def test_supervisor_cannot_move_a_supervisor(self):
        db, result = self._call(50, {"operator_id": 22})

        self.assertEqual(_status(result), 403)
        self.assertEqual(db.adds, [])

    def test_supervisor_cannot_leave_operator_without_group(self):
        # Без группы у оператора нет ни СВ, ни учёта часов — это остаётся
        # за админом и главой отдела (раздел «Группы»).
        db, result = self._call(50, {"operator_id": 20, "remove": True})

        self.assertEqual(_status(result), 403)
        self.assertEqual(db.removes, [])

    def test_department_head_path_is_unchanged(self):
        db = _groups_db(headed={10: 7})
        db, result = self._call(50, {"operator_id": 20}, db=db, headed_dept=7)

        self.assertEqual(_status(result), 200)
        self.assertEqual(db.adds, [(50, 20, 10)])

    def test_department_head_still_removes_from_group(self):
        db = _groups_db(headed={10: 7})
        db, result = self._call(50, {"operator_id": 20, "remove": True}, db=db, headed_dept=7)

        self.assertEqual(_status(result), 200)
        self.assertEqual(db.removes, [(50, 20)])

    def test_department_head_stays_inside_own_department(self):
        db = _groups_db(headed={10: 7})
        db, result = self._call(51, {"operator_id": 20}, db=db, headed_dept=7)

        self.assertEqual(_status(result), 403)
        self.assertEqual(db.adds, [])


class SupervisorDirectionChangeDeniedTests(unittest.TestCase):
    """Направление у СВ забрали: его меняют админ и глава отдела."""

    def _call_update(self, request_payload, *, headed_dept=None):
        db = _groups_db(headed={10: headed_dept} if headed_dept else None)
        requester = db.users[10]
        normalize_role = lambda role: "sv" if str(role or "").strip().lower() == "supervisor" \
            else str(role or "").strip().lower()
        namespace = _load_functions(
            "_validate_scoped_user_relation_update",
            "admin_update_user",
            namespace={
                "db": db,
                "request": SimpleNamespace(get_json=lambda: request_payload),
                "jsonify": lambda payload: payload,
                "logging": logging,
                "_get_authenticated_requester": lambda: (10, requester, None),
                "_normalize_user_role": normalize_role,
                "_headed_department_id": lambda user_id: headed_dept,
                "_is_global_admin_requester": lambda _role, _user_id: False,
                "_is_super_admin_role": lambda role: normalize_role(role) == "super_admin",
                "_is_admin_role": lambda role: normalize_role(role) in ("admin", "super_admin"),
                "_is_supervisor_role": lambda role: normalize_role(role) == "sv",
                "_requester_can_access_target_user": lambda *_a, **_kw: True,
                "_is_supervisor_rate_change_day": lambda: True,
                "_is_valid_kz_phone": lambda _value: True,
                "normalize_proxy_status_value": lambda value: value,
            },
        )
        return db, namespace["admin_update_user"]()

    def test_plain_supervisor_cannot_change_direction(self):
        db, result = self._call_update({"user_id": 20, "field": "direction_id", "value": 41})

        self.assertEqual(_status(result), 403)
        self.assertIn("супервайзер меняет группу", _payload(result)["error"])
        self.assertEqual(db.user_updates, [])

    def test_department_head_still_changes_direction(self):
        db, result = self._call_update(
            {"user_id": 20, "field": "direction_id", "value": 41}, headed_dept=7
        )

        self.assertEqual(_status(result), 200)
        self.assertEqual(db.user_updates, [(20, "direction_id", 41, 10)])


if __name__ == "__main__":
    unittest.main()
