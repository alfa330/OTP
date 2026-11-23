# -*- coding: utf-8 -*-
"""«Задачи» рядовым сотрудникам IT — решение владельца 16.11.2026.

У IT нет собственной должности, как у Бухгалтерии или HR: рядовой заведён
'operator' или 'trainee' — той же строкой, что оператор линии, которому раздел
закрыт. Поэтому пускает ОТДЕЛ (TASK_EMPLOYEE_DEPARTMENT_CODES), а охват у
пропущенного личный, как у рядового бэк-офиса: свои задачи и доски, в состав
которых его добавили. Правило живёт в трёх местах — гард (bot_schedule2.py),
слой данных (db/core.py) и меню (src/utils/departmentViews.js), — и тесты ниже
держат их вместе.

Гард гоняем НАСТОЯЩИЙ (_task_route_guard и вся его цепочка) над подставленной
базой: строка, поставленная не туда, по тексту читалась бы как правильная.
Люди и отделы выдуманы.
"""

import ast
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = source_cache.monolith_flat_path()
DATABASE_PATH = source_cache.database_flat_path()
DB_CORE_PATH = ROOT / "db" / "core.py"
DB_TASKS_PATH = ROOT / "db" / "tasks.py"
DEPARTMENT_VIEWS_PATH = ROOT / "src" / "utils" / "departmentViews.js"

# Цепочка гарда в порядке объявления: константы раньше функций, которые их читают.
GUARD_CHAIN = (
    "ROLE_HIERARCHY",
    "BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT_CODE",
    "BACK_OFFICE_EMPLOYEE_ROLES",
    "BACK_OFFICE_TASK_EMPLOYEE_ROLES",
    "TASK_EMPLOYEE_DEPARTMENT_CODES",
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

IT, SZOV, HR = 1933, 1, 1499
DEPARTMENTS = {IT: {"code": "it"}, SZOV: {"code": "szov"}, HR: {"code": "hr"}}

_PARSED = {}


def _segment(name, path=BOT_PATH):
    """Исходник функции или присваивания верхнего уровня."""
    key = str(path)
    if key not in _PARSED:
        source = path.read_text(encoding="utf-8-sig")
        nodes = {}
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef):
                nodes[node.name] = node
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        nodes[target.id] = node
        _PARSED[key] = (source.splitlines(), nodes)
    lines, nodes = _PARSED[key]
    node = nodes[name]
    return "\n".join(lines[node.lineno - 1:node.end_lineno])


def _literal(name, path):
    """Значение константы-литерала (frozenset/tuple из строк) без импорта модуля."""
    namespace = {}
    exec(_segment(name, path), namespace)
    return namespace[name]


class _Db:
    def __init__(self, user_departments, broken=False):
        self._user_departments = user_departments
        self._broken = broken

    def get_user_department_id(self, user_id):
        return self._user_departments.get(int(user_id))

    def get_department_by_id(self, department_id):
        return DEPARTMENTS.get(int(department_id))

    def get_user_department(self, user_id):
        if self._broken:
            raise RuntimeError("база недоступна")
        department_id = self._user_departments.get(int(user_id))
        return department_id, (DEPARTMENTS.get(department_id) or {}).get("code")


def _make_guard(users, heads, broken_db=False):
    """Настоящий _task_route_guard: users — {id: (роль, отдел)}, heads — {id: отдел}."""
    current = {}
    namespace = {
        "g": None,
        "db": _Db({user_id: department for user_id, (_role, department) in users.items()},
                  broken=broken_db),
        "jsonify": lambda payload: payload,
        "_resolve_requester": lambda: (current["id"], current["row"], None),
        "_headed_department_id": lambda requester_id: heads.get(int(requester_id or 0)),
    }
    for name in GUARD_CHAIN:
        exec(_segment(name), namespace)

    def enter(user_id):
        role, _department = users[user_id]
        current["id"] = user_id
        current["row"] = (user_id, None, "login", role)
        namespace["g"] = SimpleNamespace()
        return namespace["_task_route_guard"](), namespace["g"]

    return enter, namespace


class ItRankAndFileGuardTests(unittest.TestCase):
    USERS = {
        51: ("operator", IT),       # специалист IT
        52: ("trainee", IT),        # стажёр IT
        53: ("operator", SZOV),     # оператор линии
        54: ("operator", None),     # оператор без отдела
        55: ("hr_manager", IT),     # должность бэк-офиса в чужом отделе
        56: ("admin", IT),          # глава IT — админ
        57: ("operator", IT),       # глава IT с ролью оператора
        58: ("operator", HR),       # старая учётка HR на 'operator'
        59: ("sv", IT),             # СВ IT
    }
    HEADS = {56: IT, 57: IT}

    def setUp(self):
        self.enter, self.namespace = _make_guard(self.USERS, self.HEADS)

    def _assert_denied(self, user_id):
        result, _g = self.enter(user_id)
        self.assertEqual(403, result[3], f"{user_id} не должен попасть в «Задачи»")

    def test_it_operator_enters_with_personal_scope(self):
        for user_id, role in ((51, "operator"), (52, "trainee")):
            result, g = self.enter(user_id)
            self.assertEqual(user_id, result[0])
            self.assertIsNone(result[2])
            # Слой данных знает его под своей же ролью — она в личном охвате.
            self.assertEqual(role, g.effective_task_role)
            self.assertTrue(g.task_scope_is_personal)
            self.assertEqual(IT, g.task_scope_department_id)

    def test_line_operator_and_strangers_stay_outside(self):
        self._assert_denied(53)   # оператор линии — та же роль, другой отдел
        self._assert_denied(54)   # без отдела
        self._assert_denied(55)   # 'hr_manager' в IT — не должность этого отдела

    def test_database_failure_closes_the_section(self):
        enter, _namespace = _make_guard(self.USERS, self.HEADS, broken_db=True)
        result, _g = enter(51)
        self.assertEqual(403, result[3], "сбой базы не открывает раздел")

    def test_heads_and_supervisors_keep_their_scope(self):
        # Глава IT идёт под 'sv' своего отдела — как глава любого отдела, а не
        # личным охватом рядового, даже если в карточке у него 'operator'.
        for user_id in (56, 57):
            result, g = self.enter(user_id)
            self.assertIsNone(result[2])
            self.assertEqual("sv", g.effective_task_role)
            self.assertFalse(g.task_scope_is_personal)
            self.assertEqual(IT, g.task_scope_department_id)
        result, g = self.enter(59)
        self.assertIsNone(result[2])
        self.assertEqual("sv", g.effective_task_role)
        self.assertFalse(g.task_scope_is_personal)

    def test_back_office_legacy_operator_is_unchanged(self):
        # Старая учётка HR на 'operator' по-прежнему идёт под должностью отдела.
        result, g = self.enter(58)
        self.assertIsNone(result[2])
        self.assertEqual("hr_manager", g.effective_task_role)
        self.assertTrue(g.task_scope_is_personal)

    def test_bell_follows_the_same_rule(self):
        # Колокол спрашивает тот же _can_access_tasks (сверено в
        # test_back_office_department_scope) — значит, и источник «Задачи»
        # у рядового IT открыт, а у оператора линии скрыт.
        can = self.namespace["_can_access_tasks"]
        self.assertTrue(can("operator", 51))
        self.assertTrue(can("trainee", 52))
        self.assertFalse(can("operator", 53))


class ItDepartmentMirrorTests(unittest.TestCase):
    """Набор отделов и ролей — в трёх местах; разъедутся они молча."""

    def _frontend_set(self, name):
        source = DEPARTMENT_VIEWS_PATH.read_text(encoding="utf-8")
        match = re.search(rf"const {name} = new Set\(\[([^\]]*)\]\);", source)
        self.assertIsNotNone(match, f"{name} не найден в departmentViews.js")
        return set(re.findall(r"'([a-z_]+)'", match.group(1)))

    def _frontend_list(self, name):
        source = DEPARTMENT_VIEWS_PATH.read_text(encoding="utf-8")
        match = re.search(rf"const {name} = \[([^\]]*)\];", source)
        self.assertIsNotNone(match, f"{name} не найден в departmentViews.js")
        return set(re.findall(r"'([a-z_]+)'", match.group(1)))

    def test_departments_match_everywhere(self):
        bot = set(_literal("TASK_EMPLOYEE_DEPARTMENT_CODES", BOT_PATH))
        core = set(_literal("TASK_EMPLOYEE_DEPARTMENT_CODES", DB_CORE_PATH))
        front = self._frontend_set("TASK_EMPLOYEE_DEPARTMENTS")
        self.assertEqual({"it"}, bot)
        self.assertEqual(bot, core)
        self.assertEqual(bot, front)

    def test_roles_match_everywhere(self):
        core = set(_literal("TASK_EMPLOYEE_ROLES", DB_CORE_PATH))
        front = self._frontend_list("TASK_EMPLOYEE_ROLES")
        self.assertEqual({"operator", "trainee"}, core)
        self.assertEqual(core, front)
        guard = _segment("_back_office_task_employee_role")
        self.assertIn("role in ('operator', 'trainee') and code in TASK_EMPLOYEE_DEPARTMENT_CODES", guard)
        # Обе роли гард пропускает дальше первой проверки, а слой данных держит
        # в личном охвате: иначе раздел открылся бы и молча показал ноль задач.
        bot = {}
        for name in ("BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT_CODE", "BACK_OFFICE_EMPLOYEE_ROLES",
                     "BACK_OFFICE_TASK_EMPLOYEE_ROLES"):
            exec(_segment(name), bot)
        self.assertLessEqual(core, set(bot["BACK_OFFICE_TASK_EMPLOYEE_ROLES"]))
        data = {}
        for name in ("BACK_OFFICE_EMPLOYEE_ROLES", "TASK_EMPLOYEE_ROLES", "TASK_PERSONAL_SCOPE_ROLES"):
            exec(_segment(name, DB_CORE_PATH), data)
        self.assertLessEqual(core, set(data["TASK_PERSONAL_SCOPE_ROLES"]))

    def test_employee_fragment_is_built_from_the_constants(self):
        namespace = {}
        for name in ("TASK_EMPLOYEE_DEPARTMENT_CODES", "TASK_EMPLOYEE_ROLES",
                     "_TASK_EMPLOYEE_SQL"):
            exec(_segment(name, DB_CORE_PATH), namespace)
        self.assertEqual(
            "(u.role IN ('operator', 'trainee') AND LOWER(BTRIM(d.code)) IN ('it'))",
            namespace["_TASK_EMPLOYEE_SQL"],
        )
        # Фрагмент стоит в обоих списках людей: кому поручить задачу и кого
        # админ ставит на доску (ITH и прочие доски отдела)...
        tasks_source = DB_TASKS_PATH.read_text(encoding="utf-8")
        self.assertEqual(2, tasks_source.count("OR {_TASK_EMPLOYEE_SQL}"))
        # ...и в составе исполнителей для проверок доступа: путь «СВ видит
        # задачу своего оператора» рядового IT не касается (поведение — в
        # test_it_department_tasks_postgres.py).
        self.assertIn("CASE WHEN {_TASK_EMPLOYEE_SQL} THEN NULL ELSE u.supervisor_id END", tasks_source)

    def test_it_staff_is_open_to_every_department(self):
        # 23.11.2026: HR не мог поставить задачу Helpdesk — граница «админы + свой
        # отдел» отсекала рядовых IT. IT открыт для поручений всем отделам.
        namespace = {}
        for name in ("TASK_OPEN_ASSIGNEE_DEPARTMENT_CODES", "_TASK_OPEN_ASSIGNEE_SQL"):
            exec(_segment(name, DB_CORE_PATH), namespace)
        self.assertEqual({"it"}, set(namespace["TASK_OPEN_ASSIGNEE_DEPARTMENT_CODES"]))
        self.assertEqual("LOWER(BTRIM(d.code)) IN ('it')", namespace["_TASK_OPEN_ASSIGNEE_SQL"])
        # Снимается именно граница отдела в списке исполнителей; правило ролей
        # (кого из отдела можно выбрать) остаётся прежним. Список исполнителей —
        # и проверка состава при создании и правке задачи (_task_assignees_outside_scope).
        tasks_source = DB_TASKS_PATH.read_text(encoding="utf-8")
        start = tasks_source.index("    def get_task_recipients(")
        end = tasks_source.index("\n    def ", start + 1)
        recipients = tasks_source[start:end]
        self.assertIn("OR u.department_id = %s\"\n", recipients)
        self.assertIn("f\" OR {_TASK_OPEN_ASSIGNEE_SQL})\"", recipients)
        self.assertEqual(1, tasks_source.count("{_TASK_OPEN_ASSIGNEE_SQL}"))


if __name__ == "__main__":
    unittest.main()
