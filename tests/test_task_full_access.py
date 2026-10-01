# -*- coding: utf-8 -*-
"""Поимённый доступ ко всем задачам для админа — главы отдела.

Админ, возглавляющий отдел, идёт в «Задачи» под 'sv' своего отдела и видит
только задачи, где он участник, плюс задачи СВ отдела.
TASKS_FULL_ACCESS_USER_IDS снимает эту границу поимённо.

Гоняем НАСТОЯЩИЙ _task_route_guard с подставленной базой, а не ищем строки в
исходнике: ветка, поставленная не туда — до проверки доступа или так, что
следующие присваивания её перетирают, — по тексту читалась бы как правильная.
Люди и отделы здесь выдуманные; настоящая учётка проверяется только по id.
"""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"

# Цепочка гарда в порядке объявления: константы раньше функций, которые их читают.
GUARD_CHAIN = (
    "ROLE_HIERARCHY",
    "BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT_CODE",
    "BACK_OFFICE_EMPLOYEE_ROLES",
    "BACK_OFFICE_TASK_EMPLOYEE_ROLES",
    "TASKS_FULL_ACCESS_USER_IDS",
    "_normalize_user_role",
    "_get_role_level",
    "_has_min_role",
    "_is_admin_role",
    "_is_super_admin_role",
    "_is_global_admin_requester",
    "_back_office_employee_role",
    "_back_office_task_employee_role",
    "_can_access_tasks",
    "_effective_scoped_manager_role",
    "_department_scope_id_for_requester",
    "_has_full_task_access",
    "_task_route_guard",
)

SZOV = 70001
OTHER = 70002
DEPARTMENTS = {SZOV: {"code": "szov"}, OTHER: {"code": "op"}}

_PARSED = {}


def _segment(name):
    """Исходник функции или присваивания верхнего уровня bot_schedule2.py."""
    if not _PARSED:
        source = BOT_PATH.read_text(encoding="utf-8-sig")
        nodes = {}
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef):
                nodes[node.name] = node
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        nodes[target.id] = node
        _PARSED["lines"] = source.splitlines()
        _PARSED["nodes"] = nodes
    node = _PARSED["nodes"][name]
    return "\n".join(_PARSED["lines"][node.lineno - 1:node.end_lineno])


class _Db:
    def __init__(self, user_departments):
        self._user_departments = user_departments

    def get_user_department_id(self, user_id):
        return self._user_departments.get(int(user_id))

    def get_department_by_id(self, department_id):
        return DEPARTMENTS.get(int(department_id))


def _make_guard(users, heads, full_access_ids=None):
    """Настоящий _task_route_guard над подставленной базой.

    users — {id: (роль, отдел)}, heads — {id: возглавляемый отдел}.
    full_access_ids подменяет список; None оставляет тот, что в коде.
    Возвращает «войти как id» → (ответ гарда, g после него).
    """
    current = {}
    namespace = {
        "g": None,
        "db": _Db({user_id: department for user_id, (_role, department) in users.items()}),
        "jsonify": lambda payload: payload,
        "_resolve_requester": lambda: (current["id"], current["row"], None),
        "_headed_department_id": lambda requester_id: heads.get(int(requester_id or 0)),
    }
    for name in GUARD_CHAIN:
        exec(_segment(name), namespace)
    if full_access_ids is not None:
        namespace["TASKS_FULL_ACCESS_USER_IDS"] = set(full_access_ids)

    def enter(user_id):
        role, _department = users[user_id]
        current["id"] = user_id
        current["row"] = (user_id, None, "login", role)
        namespace["g"] = SimpleNamespace()
        return namespace["_task_route_guard"](), namespace["g"]

    return enter


class TaskFullAccessGuardTests(unittest.TestCase):
    def setUp(self):
        users = {
            41001: ("admin", SZOV),        # глава отдела, в списке
            41002: ("admin", OTHER),       # глава другого отдела, не в списке
            41003: ("admin", SZOV),        # админ, отдел не возглавляет
            41004: ("sv", SZOV),           # СВ, вписанный в список
            41005: ("super_admin", SZOV),  # супер-админ и глава отдела
            41006: ("operator", SZOV),     # оператор линии, вписанный в список
        }
        heads = {41001: SZOV, 41002: OTHER, 41005: SZOV}
        self.enter = _make_guard(users, heads, full_access_ids={41001, 41004, 41006})

    def test_listed_department_head_sees_every_task(self):
        result, g = self.enter(41001)
        self.assertEqual(41001, result[0])
        self.assertIsNone(result[2])
        self.assertEqual("admin", g.effective_task_role)
        self.assertIsNone(g.task_scope_department_id)
        self.assertFalse(g.task_scope_is_personal)

    def test_listed_head_is_scoped_exactly_like_admin_without_department(self):
        # Обещание списка — «как админ, который отдел не возглавляет». Сверяем
        # весь портрет охвата в g, а не одно поле: лишний или забытый атрибут
        # здесь и вылезет. Кэши запроса (g._headed_dept_cache и подобные) не
        # охват: путь админа без отдела их заводит, ветка списка — нет.
        def scope(ns):
            return {key: value for key, value in vars(ns).items() if not key.startswith('_')}

        _, listed = self.enter(41001)
        _, plain = self.enter(41003)
        self.assertEqual(scope(plain), scope(listed))

    def test_other_department_heads_keep_their_boundary(self):
        _, g = self.enter(41002)
        self.assertEqual("sv", g.effective_task_role)
        self.assertEqual(OTHER, g.task_scope_department_id)

    def test_list_does_not_raise_a_role(self):
        _, g = self.enter(41004)
        self.assertEqual("sv", g.effective_task_role)
        self.assertEqual(SZOV, g.task_scope_department_id)
        # Раздела список не открывает: оператор линии получает тот же отказ.
        result, _ = self.enter(41006)
        self.assertEqual(403, result[3])

    def test_super_admin_is_untouched(self):
        _, g = self.enter(41005)
        self.assertEqual("super_admin", g.effective_task_role)
        self.assertIsNone(g.task_scope_department_id)

    def test_list_in_code_opens_all_tasks_for_account_1(self):
        # Учётка 1 — админ, глава отдела, та, под которой человек входит
        # (у уволенного дубля с тем же ФИО сессий нет). Список из кода, не подмена.
        enter = _make_guard({1: ("admin", SZOV)}, {1: SZOV})
        _, g = enter(1)
        self.assertEqual("admin", g.effective_task_role)
        self.assertIsNone(g.task_scope_department_id)
        self.assertFalse(g.task_scope_is_personal)


if __name__ == "__main__":
    unittest.main()
