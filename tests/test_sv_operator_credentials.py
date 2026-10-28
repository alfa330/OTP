"""Логин и пароль оператора меняет любой СВ его отдела (задача #380).

Постановка СЗоВ от 06.10.2026: «предоставить всем супервайзерам доступ к изменению
логинов и паролей операторов». До правки СВ менял их только тем, у кого сам записан
руководителем (`users.supervisor_id`), остальным операторам отдела сервер отвечал 403.
Теперь граница — отдел, как у остальных правок СВ в карточке сотрудника
(`_requester_can_access_target_user`). Круг целей не расширяли: только операторы.

Ручки и правило доступа исполняются настоящие, из bot_schedule2.py; подменены лишь
база и то, что ходит в неё за главой отдела и кадровиком.
"""
import ast
import copy
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests import source_cache


BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"

REAL_HELPERS = (
    "_normalize_user_role",
    "_is_super_admin_role",
    "_is_supervisor_role",
    "_is_global_admin_requester",
    "_department_scope_id_for_requester",
    "_target_user_supervisor_id",
    "_requester_can_access_target_user",
)

SV_ID = 10
OTHER_SV_ID = 11
OPERATOR_ID = 42


def _module():
    return source_cache.parse(BOT_PATH.read_text(encoding="utf-8-sig"))


def _module_level_names():
    names = set()
    for item in _module().body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(item.name)
        elif isinstance(item, ast.Assign):
            names.update(t.id for t in item.targets if isinstance(t, ast.Name))
        elif isinstance(item, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).split(".")[0] for alias in item.names)
    return names


def _user_row(user_id, role, supervisor_id=None):
    """Кортеж users в том виде, в каком его отдаёт db.get_user: [3] роль, [6] руководитель."""
    row = [None] * 8
    row[0] = user_id
    row[2] = f"User {user_id}"
    row[3] = role
    row[6] = supervisor_id
    return tuple(row)


def _status(result):
    return result[1] if isinstance(result, tuple) else 200


class _FakeDb:
    def __init__(self, users, departments):
        self.users = {row[0]: row for row in users}
        self.departments = departments
        self.changed = []

    def get_user(self, *, id):
        return self.users.get(int(id))

    def get_user_by_login(self, _login):
        return None

    def get_user_department_id(self, user_id):
        return self.departments.get(int(user_id))

    def update_operator_login(self, user_id, _supervisor_id, value):
        self.changed.append(("login", user_id, value))
        return True

    def update_user_password(self, user_id, value):
        self.changed.append(("password", user_id, value))
        return True


def _fakes(fake_db, requester, payload):
    return {
        "db": fake_db,
        "jsonify": lambda body: body,
        "logging": SimpleNamespace(error=lambda *_a, **_k: None),
        "request": SimpleNamespace(get_json=lambda: payload),
        "requests": SimpleNamespace(post=lambda *_a, **_k: None),
        "API_TOKEN": "x",
        "_get_authenticated_requester": lambda: (requester[0], requester, None),
        "_headed_department_id": lambda _uid: None,
        "_headed_department_ids": lambda _uid: frozenset(),
        "_is_employee_accounting_manager": lambda _uid: False,
        "_is_admin_role": lambda role: str(role or "").strip().lower() in ("admin", "super_admin"),
    }


def _call(endpoint, *, target, departments):
    requester = _user_row(SV_ID, "sv")
    fake_db = _FakeDb([requester, target], departments)
    key = "new_login" if endpoint == "change_login" else "new_password"
    namespace = _fakes(fake_db, requester, {"user_id": target[0], key: "new-value-1"})
    wanted = set(REAL_HELPERS) | {endpoint}
    nodes = []
    for item in _module().body:
        if isinstance(item, ast.FunctionDef) and item.name in wanted:
            node = copy.deepcopy(item)
            node.decorator_list = []
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace[endpoint](), fake_db.changed


class SupervisorCredentialsTests(unittest.TestCase):
    ENDPOINTS = ("change_password", "change_login")

    def test_any_operator_of_own_department(self):
        """Оператор записан за другим СВ того же отдела — менять можно."""
        target = _user_row(OPERATOR_ID, "operator", supervisor_id=OTHER_SV_ID)
        for endpoint in self.ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                result, changed = _call(endpoint, target=target,
                                        departments={SV_ID: 1, OPERATOR_ID: 1})
                self.assertEqual(_status(result), 200, result)
                self.assertEqual(len(changed), 1)

    def test_operator_of_another_department_stays_closed(self):
        target = _user_row(OPERATOR_ID, "operator", supervisor_id=OTHER_SV_ID)
        for endpoint in self.ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                result, changed = _call(endpoint, target=target,
                                        departments={SV_ID: 1, OPERATOR_ID: 2})
                self.assertEqual(_status(result), 403)
                self.assertIn("своего отдела", result[0]["error"])
                self.assertEqual(changed, [])

    def test_only_operators(self):
        """Просили про операторов: стажёры и другие СВ отдела по-прежнему закрыты."""
        for role in ("trainee", "sv"):
            target = _user_row(OPERATOR_ID, role, supervisor_id=SV_ID)
            for endpoint in self.ENDPOINTS:
                with self.subTest(role=role, endpoint=endpoint):
                    result, changed = _call(endpoint, target=target,
                                            departments={SV_ID: 1, OPERATOR_ID: 1})
                    self.assertEqual(_status(result), 403)
                    self.assertEqual(changed, [])

    def test_sv_without_department_keeps_only_own_operators(self):
        """Старые учётки без отдела: границы отдела нет — остаются прямые подчинённые."""
        for supervisor_id, expected in ((SV_ID, 200), (OTHER_SV_ID, 403)):
            target = _user_row(OPERATOR_ID, "operator", supervisor_id=supervisor_id)
            for endpoint in self.ENDPOINTS:
                with self.subTest(supervisor_id=supervisor_id, endpoint=endpoint):
                    result, _changed = _call(endpoint, target=target,
                                             departments={OPERATOR_ID: 1})
                    self.assertEqual(_status(result), expected)

    def test_stubs_stand_for_real_module_names(self):
        """Подмена с опечаткой в имени спрятала бы NameError: ручка ловит любое исключение
        и отвечает 500, а не падает."""
        stubbed = set(_fakes(None, _user_row(SV_ID, "sv"), {}))
        self.assertEqual(stubbed - _module_level_names(), set())


if __name__ == "__main__":
    unittest.main()
