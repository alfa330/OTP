"""«Учет сотрудников» целиком — поимённо, людям вне отдела кадров.

Решение владельца 24.11.2026: двум главам отделов (бухгалтерии и отдела
аналитики, обе с ролью admin) «выдать полный доступ для раздела учет
сотрудников». До допуска раздел у них был главы отдела — только свои люди.

Что сторожится:
1. набор EMPLOYEE_ACCOUNTING_USER_GRANTS один на сервере и во фронте;
2. допуск держится за отдел, в котором человек числится, и за его id;
3. в ручках самого раздела человек с допуском равен кадровику;
4. общие справочники (сотрудники, СВ, отделы, направления, группы) расширяются
   только по запросу раздела — ?scope=employee_accounting; те же ручки читают
   «Графики работы», «Опросы», «Группы», «Журнал оценок», и там у главы
   остаётся её отдел;
5. всё, что сверх раздела даёт членство в отделе кадров (графики любого
   отдела, «Кадры», тренинги, планы операторов), допуск НЕ открывает.
"""
import ast
import copy
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = source_cache.monolith_flat_path()
APP_PATH = ROOT / "src" / "App.jsx"
MODAL_PATH = ROOT / "src" / "components" / "modals" / "UserEditModal.jsx"
DEPARTMENT_VIEWS_PATH = ROOT / "src" / "utils" / "departmentViews.js"

GRANTED = {"accounting": {467}, "analytik": {229}}


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _module():
    return source_cache.parse(_read(BOT_PATH))


def _top_function(name):
    return next(
        item for item in _module().body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )


def _load_function(name, namespace):
    node = copy.deepcopy(_top_function(name))
    node.decorator_list = []
    result = dict(namespace)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(BOT_PATH), "exec"), result)
    return result[name]


def _calls(name):
    """Имена функций, которые вызывает функция монолита."""
    return {
        node.func.id
        for node in ast.walk(_top_function(name))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _module_assignment(name):
    for item in _module().body:
        if isinstance(item, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in item.targets):
            return item.value
    raise AssertionError(f"{name} не найдена в монолите")


def _backend_grants():
    """EMPLOYEE_ACCOUNTING_USER_GRANTS: {'код': frozenset({id, ...})} — разбор без exec."""
    value = _module_assignment("EMPLOYEE_ACCOUNTING_USER_GRANTS")
    grants = {}
    for key, item in zip(value.keys, value.values):
        assert isinstance(item, ast.Call) and item.func.id == "frozenset", ast.dump(item)
        grants[ast.literal_eval(key)] = set(ast.literal_eval(item.args[0]))
    return grants


def _frontend_grants():
    source = _read(DEPARTMENT_VIEWS_PATH)
    block = source.split("const EMPLOYEE_ACCOUNTING_USER_GRANTS = {", 1)[1].split("};", 1)[0]
    return {
        code: {int(value) for value in re.findall(r"\d+", ids)}
        for code, ids in re.findall(r"(\w+):\s*\[([^\]]*)\]", block)
    }


def _user_row(user_id, role):
    """Кортеж users в том виде, в каком его читают ручки: [0] id, [3] роль."""
    row = [None] * 8
    row[0] = user_id
    row[2] = f"User {user_id}"
    row[3] = role
    return tuple(row)


class _Request:
    def __init__(self, args=None, method="GET"):
        self.args = dict(args or {})
        self.method = method


# ─────────────────────────── Набор ───────────────────────────

class MirrorTests(unittest.TestCase):
    def test_backend_set_is_the_owners_decision(self):
        self.assertEqual(_backend_grants(), GRANTED)

    def test_frontend_mirrors_the_backend(self):
        self.assertEqual(_frontend_grants(), _backend_grants())

    def test_scope_value_is_one_string_on_both_sides(self):
        backend = ast.literal_eval(_module_assignment("EMPLOYEE_ACCOUNTING_LISTS_SCOPE"))
        frontend = re.search(
            r"export const EMPLOYEE_ACCOUNTING_LISTS_SCOPE = '([^']+)';", _read(DEPARTMENT_VIEWS_PATH)
        ).group(1)
        self.assertEqual(backend, "employee_accounting")
        self.assertEqual(frontend, backend)


# ─────────────────────────── Предикаты ───────────────────────────

class GrantPredicateTests(unittest.TestCase):
    """_has_employee_accounting_user_grant: id из набора и отдел, где человек числится."""

    def _predicate(self, department_code, calls=None):
        calls = [] if calls is None else calls

        class _DB:
            def get_user_department(self, user_id):
                calls.append(user_id)
                return (77, department_code)

        return _load_function("_has_employee_accounting_user_grant", {
            "db": _DB(),
            "g": SimpleNamespace(),
            "EMPLOYEE_ACCOUNTING_USER_GRANTS": {code: frozenset(ids) for code, ids in _backend_grants().items()},
        }), calls

    def test_each_grantee_passes_in_their_own_department(self):
        for code, ids in GRANTED.items():
            for user_id in ids:
                with self.subTest(code=code, user_id=user_id):
                    predicate, _ = self._predicate(code)
                    self.assertTrue(predicate(user_id))
                    self.assertTrue(predicate(str(user_id)))

    def test_department_code_is_compared_case_insensitively(self):
        predicate, _ = self._predicate(" Accounting ")
        self.assertTrue(predicate(467))

    def test_transfer_to_another_department_drops_the_grant(self):
        for code in ("hr", "szov", "analytik", None):
            with self.subTest(code=code):
                predicate, _ = self._predicate(code)
                self.assertFalse(predicate(467))

    def test_people_outside_the_set_never_reach_the_database(self):
        predicate, calls = self._predicate("accounting")
        for user_id in (1, 228, 456, 499, None, "abc"):
            with self.subTest(user_id=user_id):
                self.assertFalse(predicate(user_id))
        self.assertEqual(calls, [])

    def test_answer_is_remembered_for_the_request(self):
        predicate, calls = self._predicate("accounting")
        self.assertTrue(predicate(467))
        self.assertTrue(predicate(467))
        self.assertEqual(calls, [467])


class FullAccessPredicateTests(unittest.TestCase):
    def _call(self, *, hr, grant):
        predicate = _load_function("_has_full_employee_accounting", {
            "_is_employee_accounting_manager": lambda _uid: hr,
            "_has_employee_accounting_user_grant": lambda _uid: grant,
        })
        return predicate(10)

    def test_hr_or_the_grant_opens_the_whole_section(self):
        self.assertTrue(self._call(hr=True, grant=False))
        self.assertTrue(self._call(hr=False, grant=True))
        self.assertFalse(self._call(hr=False, grant=False))


class ListsScopePredicateTests(unittest.TestCase):
    """_sees_employee_accounting_lists: кадровику всегда, допуску — по запросу раздела."""

    def _call(self, *, hr, grant, args):
        predicate = _load_function("_sees_employee_accounting_lists", {
            "request": _Request(args),
            "EMPLOYEE_ACCOUNTING_LISTS_SCOPE": "employee_accounting",
            "_is_employee_accounting_manager": lambda _uid: hr,
            "_has_employee_accounting_user_grant": lambda _uid: grant,
        })
        return predicate(10)

    def test_hr_sees_the_whole_company_without_asking(self):
        self.assertTrue(self._call(hr=True, grant=False, args={}))

    def test_grant_widens_only_when_the_section_asks(self):
        self.assertTrue(self._call(hr=False, grant=True, args={"scope": "employee_accounting"}))
        self.assertTrue(self._call(hr=False, grant=True, args={"scope": " Employee_Accounting "}))
        self.assertFalse(self._call(hr=False, grant=True, args={}))
        self.assertFalse(self._call(hr=False, grant=True, args={"scope": "something_else"}))

    def test_asking_does_not_help_without_the_grant(self):
        self.assertFalse(self._call(hr=False, grant=False, args={"scope": "employee_accounting"}))


# ─────────────────────── Граница по людям ───────────────────────

class TargetAccessTests(unittest.TestCase):
    """_requester_can_access_target_user: допуск — только в ручках раздела."""

    def _call(self, *, target_role="operator", grant=True, section=False, include=True,
              headed=(1500,), target_department=1):
        headed = frozenset(headed)

        class _DB:
            def get_user_department_id(self, _user_id):
                return target_department

        helper = _load_function("_requester_can_access_target_user", {
            "db": _DB(),
            "_normalize_user_role": lambda v: str(v or "").strip().lower(),
            "_headed_department_ids": lambda _uid: headed,
            "_is_super_admin_role": lambda v: str(v or "").lower() == "super_admin",
            "_is_admin_role": lambda v: str(v or "").lower() in ("admin", "super_admin"),
            "_is_supervisor_role": lambda v: str(v or "").lower() in ("sv", "supervisor"),
            "_is_global_admin_requester": lambda v, _uid=None: (
                str(v or "").lower() == "super_admin" or (str(v or "").lower() == "admin" and not headed)),
            "_department_scope_id_for_requester": lambda _uid: None,
            "_target_user_supervisor_id": lambda _t: None,
            "_is_employee_accounting_manager": lambda _uid: False,
            "_has_employee_accounting_user_grant": lambda _uid: grant,
        })
        return helper(_user_row(467, "admin"), 467, _user_row(42, target_role),
                      include_employee_accounting=include, employee_accounting_section=section)

    def test_section_handles_reach_anyone_like_hr(self):
        for target_role in ("operator", "trainee", "trainer", "sv", "admin"):
            with self.subTest(target_role=target_role):
                self.assertTrue(self._call(target_role=target_role, section=True))

    def test_other_handles_keep_the_boundary_of_her_own_department(self):
        # Графики, планы, тренинги, отчёт по часам — ручки без флага раздела.
        self.assertFalse(self._call(section=False, target_department=1))
        self.assertTrue(self._call(section=False, target_department=1500))
        self.assertFalse(self._call(section=False, target_role="admin", target_department=1500))

    def test_line_manager_actions_stay_closed_even_from_the_section(self):
        # include_employee_accounting=False — штраф, перерывы, агрегация.
        self.assertFalse(self._call(section=True, include=False, target_department=1))

    def test_flag_without_the_grant_changes_nothing(self):
        self.assertFalse(self._call(grant=False, section=True, target_department=1))
        self.assertTrue(self._call(grant=False, section=True, target_department=1500))


class GroupsTests(unittest.TestCase):
    """Группы: карточке — всей компании, «Учёту часов» (/api/sv/data) — её отдела."""

    def _call(self, *, lists):
        asked = []

        class _DB:
            def list_groups(self, include_archived=False, department_id=None):
                asked.append(department_id)
                return [{"department_id": department_id}]

        helper = _load_function("_scoped_groups_for_requester", {
            "db": _DB(),
            "_is_employee_accounting_manager": lambda _uid: False,
            "_has_employee_accounting_user_grant": lambda _uid: True,
            "_is_super_admin_role": lambda v: v == "super_admin",
            "_headed_department_choice": lambda _uid, _arg: None,
            "_is_global_admin_requester": lambda _role, _uid: False,
            "_is_supervisor_role": lambda v: v in ("sv", "supervisor"),
        })
        helper(467, "admin", 1500, employee_accounting_lists=lists)
        return asked

    def test_card_gets_every_department(self):
        self.assertEqual(self._call(lists=True), [None])

    def test_hours_keep_her_department(self):
        self.assertEqual(self._call(lists=False), [1500])

    def test_hours_endpoint_does_not_pass_the_flag(self):
        tree = ast.parse(_read(ROOT / "supervisors" / "routes.py"))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "_scoped_groups_for_requester"
        ]
        self.assertTrue(calls)
        for call in calls:
            self.assertNotIn("employee_accounting_lists", [keyword.arg for keyword in call.keywords])


class DepartmentsDirectoryTests(unittest.TestCase):
    """/api/admin/departments главе с допуском: весь справочник — только разделу."""

    def _call(self, args):
        departments = [{"id": 1, "code": "szov"}, {"id": 1500, "code": "accounting"},
                       {"id": 2134, "code": "analytik"}]

        class _DB:
            def get_departments(self):
                return list(departments)

            def get_department_by_id(self, department_id):
                return next((d for d in departments if d["id"] == department_id), None)

        request = _Request(args)
        sees_lists = _load_function("_sees_employee_accounting_lists", {
            "request": request,
            "EMPLOYEE_ACCOUNTING_LISTS_SCOPE": "employee_accounting",
            "_is_employee_accounting_manager": lambda _uid: False,
            "_has_employee_accounting_user_grant": lambda uid: uid == 467,
        })
        endpoint = _load_function("api_admin_departments", {
            "db": _DB(),
            "jsonify": lambda payload: payload,
            "logging": SimpleNamespace(error=lambda *_a, **_k: None),
            "request": request,
            "_build_cors_preflight_response": lambda: None,
            "_get_authenticated_requester": lambda: (467, _user_row(467, "admin"), None),
            "_normalize_user_role": lambda value: str(value or "").strip().lower(),
            "_headed_department_ids": lambda _uid: frozenset({1500}),
            "_is_super_admin_role": lambda value: value == "super_admin",
            "_is_admin_role": lambda value: value in ("admin", "super_admin"),
            "_is_marketing_observer": lambda *_a, **_k: False,
            "_is_work_schedule_view_only": lambda _uid: False,
            "_sees_employee_accounting_lists": sees_lists,
        })
        result = endpoint()
        return [item["id"] for item in (result[0] if isinstance(result, tuple) else result)["departments"]]

    def test_section_gets_every_department(self):
        self.assertEqual(self._call({"scope": "employee_accounting"}), [1, 1500, 2134])

    def test_other_sections_keep_her_department(self):
        self.assertEqual(self._call({}), [1500])


# ───────────────────── Группы: перевод и всё остальное ─────────────────────

GROUPS = {1: {"id": 1, "department_id": 1, "status": "active"},
          7: {"id": 7, "department_id": 1500, "status": "active"}}


def _group_namespace(*, grant=True, hr=False, calls=None):
    calls = [] if calls is None else calls

    class _DB:
        def get_group(self, group_id):
            return GROUPS.get(int(group_id))

        def headed_department_id_for_user(self, _uid):
            return 1500

        def get_user(self, id):
            return _user_row(int(id), "operator")

        def add_operator_to_group(self, group_id, operator_id, **kwargs):
            calls.append(("add", group_id, operator_id, kwargs.get("start_date")))
            return None

        def remove_operator_from_group(self, group_id, operator_id, end_date=None):
            calls.append(("remove", group_id, operator_id))

    return {
        "db": _DB(),
        "jsonify": lambda payload: payload,
        "logging": SimpleNamespace(error=lambda *_a, **_k: None),
        "_is_global_admin_requester": lambda _role, _uid: False,
        "_is_employee_accounting_manager": lambda _uid: hr,
        "_has_employee_accounting_user_grant": lambda _uid: grant,
        "_headed_department_ids": lambda _uid: frozenset({1500}),
    }


class GroupScopeTests(unittest.TestCase):
    """_ensure_group_in_requester_scope: группа чужого отдела — только переводу из карточки."""

    def _call(self, group_id, **kwargs):
        flags = {key: kwargs.pop(key) for key in ("grant", "hr") if key in kwargs}
        helper = _load_function("_ensure_group_in_requester_scope", _group_namespace(**flags))
        result = helper(group_id, 467, "admin", **kwargs)
        return None if result is None else result[1]

    def test_card_move_reaches_a_group_of_any_department(self):
        self.assertIsNone(self._call(1, employee_accounting_section=True))

    def test_everything_else_keeps_her_department(self):
        self.assertEqual(self._call(1), 403)
        self.assertEqual(self._call(1, employee_accounting_section=False), 403)
        self.assertIsNone(self._call(7))

    def test_flag_without_the_grant_changes_nothing(self):
        self.assertEqual(self._call(1, grant=False, employee_accounting_section=True), 403)

    def test_hr_reaches_any_group_as_before(self):
        self.assertIsNone(self._call(1, grant=False, hr=True))


class GroupMoveEndpointTests(unittest.TestCase):
    """POST /api/admin/groups/<id>/operators у допуска: зачислить — да, убрать и задним числом — нет."""

    def _call(self, group_id, body):
        calls = []
        namespace = _group_namespace(calls=calls)
        scope = _load_function("_ensure_group_in_requester_scope", namespace)
        namespace.update({
            "request": SimpleNamespace(get_json=lambda *a, **k: dict(body)),
            "_ensure_group_operator_manager": lambda: (467, _user_row(467, "admin"), "admin", None),
            "_is_plain_supervisor_requester": lambda _uid, _role: False,
            "_ensure_group_in_supervisor_scope": lambda *_a: None,
            "_ensure_group_in_requester_scope": scope,
            "_requester_can_access_target_user": lambda *_a, **_k: False,
        })
        endpoint = _load_function("add_group_operator_endpoint", namespace)
        result = endpoint(group_id)
        return (result[1] if isinstance(result, tuple) else 200), calls

    def test_card_move_into_a_group_of_another_department(self):
        status, calls = self._call(1, {"operator_id": 54, "sync_direction": True})
        self.assertEqual(status, 200)
        self.assertEqual(calls, [("add", 1, 54, None)])

    def test_removing_from_a_group_of_another_department_is_refused(self):
        status, calls = self._call(1, {"operator_id": 54, "remove": True})
        self.assertEqual(status, 403)
        self.assertEqual(calls, [])

    def test_backdated_move_into_another_department_is_refused(self):
        status, calls = self._call(1, {"operator_id": 54, "start_date": "2026-11-01"})
        self.assertEqual(status, 403)
        self.assertEqual(calls, [])

    def test_her_own_groups_work_as_before(self):
        status, calls = self._call(7, {"operator_id": 54, "remove": True})
        self.assertEqual(status, 200)
        self.assertEqual(calls, [("remove", 7, 54)])


class StatusFromTheCardTests(unittest.TestCase):
    """Статус и снятие ЧС: карточка (без диапазона) — любой человек; сетка (с диапазоном) — граница главы."""

    def _flags(self, name, body, resolver_name="_resolve_scoped_operator_for_requester"):
        seen = []

        def resolve(user_data, requester_id, operator_id, **kwargs):
            seen.append(kwargs.get("employee_accounting_section", False))
            return _user_row(int(operator_id), "operator"), None

        class _DB:
            def save_schedule_status_period(self, **_k):
                return {"id": 1}

            def remove_schedule_dismissal_blacklist(self, *_a, **_k):
                return [{"id": 1}]

            def get_operator_with_shifts(self, *_a):
                return {"shifts": []}

        endpoint = _load_function(name, {
            "db": _DB(),
            "jsonify": lambda payload: payload,
            "logging": SimpleNamespace(error=lambda *_a, **_k: None),
            "request": SimpleNamespace(get_json=lambda *a, **k: dict(body)),
            "_resolve_work_schedule_editor": lambda: (467, _user_row(467, "admin"), None),
            "_resolve_management_requester": lambda: (467, _user_row(467, "admin"), None),
            "_can_remove_dismissal_blacklist": lambda *_a: True,
            "_normalize_user_role": lambda value: str(value or "").strip().lower(),
            resolver_name: resolve,
        })
        endpoint()
        return seen

    def test_status_from_the_card_reaches_anyone(self):
        body = {"operator_id": 6, "status_code": "annual_leave", "start_date": "2026-12-01", "end_date": "2026-12-05"}
        self.assertEqual(self._flags("save_work_schedule_status_period", body), [True])

    def test_status_from_the_grid_keeps_her_department(self):
        body = {"operator_id": 6, "status_code": "annual_leave", "start_date": "2026-12-01", "end_date": "2026-12-05",
                "range_start": "2026-12-01", "range_end": "2026-12-31"}
        self.assertEqual(self._flags("save_work_schedule_status_period", body), [False])

    def test_blacklist_from_the_card_and_from_the_grid(self):
        self.assertEqual(self._flags("remove_work_schedule_dismissal_blacklist", {"operator_id": 6}), [True])
        self.assertEqual(self._flags("remove_work_schedule_dismissal_blacklist", {
            "operator_id": 6, "range_start": "2026-12-01", "range_end": "2026-12-31"}), [False])

    def test_deleting_a_status_is_not_opened(self):
        segment = ast.get_source_segment(_read(BOT_PATH), _top_function("delete_work_schedule_status_period"))
        self.assertNotIn("employee_accounting_section", segment)


# ─────────────────────── Ручки раздела ───────────────────────

class SectionHandlesTests(unittest.TestCase):
    """Каждая ручка раздела спрашивает раздел целиком, а не только отдел кадров."""

    FULL_SECTION = (
        "get_manager_candidates", "admin_update_user", "admin_bulk_update_users",
        "admin_promote_to_supervisor", "admin_demote_to_operator", "add_user",
        "get_users_report", "_ensure_group_operator_manager",
        # user_profile/routes.py, вклеенный в монолит для тестов
        "_resolve_avatar_target_user", "get_user_history", "change_password", "change_login",
    )
    SHARED_LISTS = (
        "get_admin_users", "api_admin_departments", "get_sv_list", "get_directions",
        "list_groups_endpoint",
    )
    # Ручки, где цель — любой человек компании: правка, массовая правка, перевод в
    # операторы и в группу, статус сотрудника и снятие ЧС из карточки. Удаление
    # статуса — работа сетки графиков, карточка его не зовёт: там флага нет.
    SECTION_TARGETS = {
        "admin_update_user", "admin_bulk_update_users", "admin_demote_to_operator",
        "add_group_operator_endpoint", "save_work_schedule_status_period",
        "remove_work_schedule_dismissal_blacklist",
    }

    def test_section_handles_ask_for_the_whole_section(self):
        for name in self.FULL_SECTION:
            with self.subTest(name=name):
                calls = _calls(name)
                self.assertIn("_has_full_employee_accounting", calls)
                self.assertNotIn("_is_employee_accounting_manager", calls)

    def test_shared_lists_widen_only_on_the_sections_request(self):
        for name in self.SHARED_LISTS:
            with self.subTest(name=name):
                calls = _calls(name)
                self.assertIn("_sees_employee_accounting_lists", calls)
                self.assertNotIn("_has_full_employee_accounting", calls)
                self.assertNotIn("_is_employee_accounting_manager", calls)

    def test_only_section_handles_open_the_people_boundary(self):
        opened = set()
        for item in _module().body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(item):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "employee_accounting_section":
                        continue
                    value = keyword.value
                    # Помощники лишь передают свой параметр дальше — это не открытие.
                    if isinstance(value, ast.Name) and value.id == "employee_accounting_section":
                        continue
                    # Открывает всё, кроме явного False: и True, и условие.
                    if not (isinstance(value, ast.Constant) and value.value is False):
                        opened.add(item.name)
        self.assertEqual(opened, self.SECTION_TARGETS)

    def test_group_transfer_opens_only_the_move_itself(self):
        source = ast.get_source_segment(_read(BOT_PATH), _top_function("add_group_operator_endpoint"))
        # Только зачисление из карточки: «убрать из группы» и дата задним числом —
        # работа «Групп» (GroupsView), там у допуска граница главы.
        self.assertIn("card_move = not remove and not data.get('start_date')", source)
        self.assertIn("_ensure_group_in_requester_scope(group_id, rid, _role, employee_accounting_section=card_move)", source)
        for name in ("archive_group_endpoint", "rename_group_endpoint", "reuse_group_endpoint",
                     "change_group_model_endpoint", "add_group_supervisor_endpoint", "group_members_endpoint"):
            with self.subTest(name=name):
                segment = ast.get_source_segment(_read(BOT_PATH), _top_function(name))
                self.assertNotIn("employee_accounting_section", segment)

    def test_blueprints_get_the_whole_section_predicate(self):
        facade = source_cache.read(ROOT / "bot_schedule2.py")
        self.assertIn("_has_full_employee_accounting=_has_full_employee_accounting,", facade)
        self.assertIn("has_full_employee_accounting=_has_full_employee_accounting,", facade)
        self.assertNotIn("_is_employee_accounting_manager=_is_employee_accounting_manager", facade)


class HrOnlyExtrasTests(unittest.TestCase):
    """Что сверх раздела даёт отдел кадров — допуску не достаётся."""

    HR_ONLY = (
        "_filter_operators_for_requester_scope",  # сетка графиков всех отделов
        "_resolve_work_schedule_viewer",
        "_resolve_work_schedule_editor",
        "get_work_schedule_break_rules",
        "_hr_office_viewer",  # «Прием», «Табель», «АВР»
        "_hr_office_flags_for",
    )

    def test_hr_extras_still_ask_only_for_the_department(self):
        for name in self.HR_ONLY:
            with self.subTest(name=name):
                calls = _calls(name)
                self.assertIn("_is_employee_accounting_manager", calls)
                self.assertFalse(calls & {"_has_full_employee_accounting", "_sees_employee_accounting_lists",
                                          "_has_employee_accounting_user_grant"})

    def test_schedule_target_scope_is_not_opened_by_default(self):
        args = _top_function("_resolve_scoped_operator_for_requester").args
        defaults = dict(zip([a.arg for a in args.kwonlyargs], args.kw_defaults))
        self.assertIs(defaults["employee_accounting_section"].value, False)
        loader = _top_function("_load_target_user_with_scope").args
        defaults = dict(zip([a.arg for a in loader.kwonlyargs], loader.kw_defaults))
        self.assertIs(defaults["employee_accounting_section"].value, False)


# ─────────────────────────── Фронт ───────────────────────────

class FrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _read(APP_PATH)
        cls.modal = _read(MODAL_PATH)
        cls.views = _read(DEPARTMENT_VIEWS_PATH)

    def test_section_flag_is_the_whole_section_and_hr_extras_stay_with_hr(self):
        self.assertIn("const isEmployeeAccountingManager = hasFullEmployeeAccounting(user);", self.app)
        self.assertIn("const isHrDepartmentMember = managesEmployeeAccounting(user);", self.app)
        # Графики любого отдела — отделу кадров, а не допуску.
        self.assertIn("const plannerViewerIsHrScheduler = managesEmployeeAccounting(user)", self.app)
        self.assertIn("{isHrDepartmentMember ? 'Графики работы' : 'Мои смены'}", self.app)
        self.assertIn("&& !isHrDepartmentMember\n", self.app)
        self.assertIn("const managesEmployeeAccountingRequester = hasFullEmployeeAccounting(user);", self.modal)

    def test_whole_section_predicate_is_hr_or_the_grant(self):
        self.assertIn(
            "export const hasFullEmployeeAccounting = (user) =>\n"
            "    managesEmployeeAccounting(user) || hasEmployeeAccountingUserGrant(user);",
            self.views,
        )

    def test_shared_lists_are_asked_with_the_section_scope(self):
        for marker in ("const fetchUsers = async", "const fetchSvList = async",
                       "const fetchDirections = async", "const fetchDepartments = async"):
            with self.subTest(fetch=marker):
                body = self.app.split(marker, 1)[1].split("};\n", 1)[0]
                self.assertIn("...employeeAccountingListsParams,", body)
        groups = self.app.split("const fetchUserModalGroups = async () => {", 1)[1].split("};\n", 1)[0]
        self.assertIn(": employeeAccountingListsParams),", groups)

    def test_other_sections_get_the_heads_slice(self):
        for raw in ("operators={users}", "initialOperators={users}", "directions={directions}",
                    "departments={departments}", "svList={svList}"):
            with self.subTest(prop=raw):
                self.assertNotIn(raw, self.app)
        self.assertEqual(self.app.count("initialOperators={usersOutsideEmployeeAccounting}"), 4)
        self.assertEqual(self.app.count("svList={svListOutsideEmployeeAccounting}"), 2)
        self.assertIn("(Array.isArray(svListOutsideEmployeeAccounting) ? svListOutsideEmployeeAccounting : [])",
                      self.app)
        # Сами срезы — чистые функции departmentViews.js, их поведение держит
        # tests/employee_accounting_user_grants.test.mjs.
        self.assertIn("() => outsideEmployeeAccountingDepartmentIds(user),", self.app)
        self.assertIn("() => keepOutsideEmployeeAccounting(users, outsideEmployeeAccountingIds),", self.app)
        self.assertIn("() => departmentsOutsideEmployeeAccounting(departments, user),", self.app)

    def test_every_supervisor_list_request_carries_the_section_scope(self):
        """Повышение и понижение перечитывают список СВ сами — тоже с пометкой раздела."""
        requests = self.app.split("axios.get(`${API_BASE_URL}/api/admin/sv_list`")[1:]
        self.assertEqual(len(requests), 3)
        for chunk in requests:
            self.assertIn("...employeeAccountingListsParams,", chunk.split("});", 1)[0])

    def test_moving_to_another_department_is_saved_from_the_card(self):
        body = self.app.split("const saveUserChanges = ", 1)[1]
        self.assertIn("if (isAdminLikeRole || isEmployeeAccountingManager) {\n"
                      "                        const nextDept = editedUser.department_id", body)

    def test_export_lets_the_whole_company_viewer_pick_a_department(self):
        self.assertIn("const usersReportPicksDepartment = isAdminLikeRole || isEmployeeAccountingManager;", self.app)
        self.assertIn("if (usersReportPicksDepartment && exportOptions.departmentId) {", self.app)
        self.assertIn("{usersReportPicksDepartment ? (\n", self.app)
        self.assertNotIn("departmentId: isAdminLikeRole ? (manageUsersDeptFilter || '') : ''", self.app)


if __name__ == "__main__":
    unittest.main()
