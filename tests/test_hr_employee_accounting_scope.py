"""«Учет сотрудников» у отдела кадров — весь раздел по всей компании.

Задача владельца 22.09.2026, в два захода одного дня:

1. «нужно его открыть для Hr направления... просматривать данные без
   возможности их изменить». Тогда раздел выдали на просмотр;
2. «открой доступ к редактированию и к другим вариантам сотрудников, то есть
   это админы, сотрудники и супервайзеры. то есть у hr пусть будет такая
   возможность». На вопрос, касается ли это логинов и паролей админов, ответ
   был «без исключений»; на вопрос про «Тренеров» — «да, все четыре списка»;
   на вопрос про заведение новых и переводы — «да, полный набор».

Отсюда правило, которое здесь сторожится: **по людям у кадровика границ нет** —
ни по отделу, ни по должности цели. Раздел при этом остаётся ОДНИМ: других
разделов портала этот периметр не открывает.

Единственное, чего кадровику не дали, — завести НОВОГО админа: выдачу
администраторских прав владелец не называл, а бэкенд её и так не пускает.

Признак доступа — ЧЛЕНСТВО В ОТДЕЛЕ, а не роль: у кадровика роль `hr_manager`
с уровнем как у оператора. Зеркала: `_is_employee_accounting_manager` в
bot_schedule2.py и `managesEmployeeAccounting` в src/utils/departmentViews.js.
"""
import ast
import copy
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
APP_PATH = ROOT / "src" / "App.jsx"
MODAL_PATH = ROOT / "src" / "components" / "modals" / "UserEditModal.jsx"
DEPARTMENT_VIEWS_PATH = ROOT / "src" / "utils" / "departmentViews.js"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _load_function(path, name, namespace):
    module = source_cache.parse(_read(path))
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


def _function_source(path, name):
    module = source_cache.parse(_read(path))
    node = next(
        item
        for item in module.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )
    return ast.get_source_segment(_read(path), node)


def _module_constant(path, name):
    module = source_cache.parse(_read(path))
    for item in module.body:
        if isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(item.value)
    raise AssertionError(f"{name} не найдена в {path.name}")


def _status(result):
    return result[1] if isinstance(result, tuple) else 200


def _payload(result):
    return result[0] if isinstance(result, tuple) else result


def _user_row(user_id, role, *, telegram_id=None):
    """Кортеж users в том виде, в каком его читают ручки: [0] id, [3] роль."""
    row = [None] * 8
    row[0] = user_id
    row[1] = telegram_id
    row[2] = f"User {user_id}"
    row[3] = role
    return tuple(row)


# ─────────────────────────── Сам предикат ───────────────────────────

class ManagerPredicateTests(unittest.TestCase):
    """Кто считается кадровиком на бэкенде."""

    def _call(self, *, department_code):
        class _DB:
            def get_user_department_id(self, _user_id):
                return 77 if department_code is not None else None

            def get_department_by_id(self, _department_id):
                return {"id": 77, "code": department_code}

        predicate = _load_function(
            BOT_PATH,
            "_is_employee_accounting_manager",
            {
                "db": _DB(),
                # Код отдела берём из модуля, а не переписываем литералом:
                # переименуй его кто-нибудь — и тест должен поехать вместе.
                "EMPLOYEE_ACCOUNTING_DEPARTMENT_CODE": _module_constant(
                    BOT_PATH, "EMPLOYEE_ACCOUNTING_DEPARTMENT_CODE"
                ),
                # Настоящий flask.g недоступен: подменяем объектом, на который
                # так же можно повесить атрибут-кеш.
                "g": SimpleNamespace(),
            },
        )
        return predicate(10)

    def test_member_of_hr_is_a_manager(self):
        self.assertTrue(self._call(department_code="hr"))

    def test_code_is_matched_case_insensitively(self):
        self.assertTrue(self._call(department_code="HR"))
        self.assertTrue(self._call(department_code=" Hr "))

    def test_other_departments_are_not_managers(self):
        for code in ("szov", "op", "tez", "front_office", "accounting", "marketing", "it"):
            with self.subTest(code=code):
                self.assertFalse(self._call(department_code=code))

    def test_user_without_a_department_is_not_a_manager(self):
        self.assertFalse(self._call(department_code=None))

    def test_role_is_never_asked(self):
        """Признак — отдел. Роль в теле функции не упоминается вовсе.

        Проверка по роли открыла бы раздел человеку, которого из отдела кадров
        уже перевели, — и закрыла бы кадровику, которого завели оператором.
        """
        source = _function_source(BOT_PATH, "_is_employee_accounting_manager")
        body = source[source.index('"""', source.index('"""') + 3):]
        self.assertNotIn("_normalize_user_role", body)
        self.assertNotIn("role", body.replace("hr_manager", ""))


class TargetAccessTests(unittest.TestCase):
    """Общий узел: через него ходят почти все пишущие ручки раздела."""

    def _call(self, *, requester_role, target_role, manager, headed=(), target_department=9):
        headed = {int(v) for v in headed}

        class _DB:
            def get_user_department_id(self, _user_id):
                return target_department

        helper = _load_function(
            BOT_PATH,
            "_requester_can_access_target_user",
            {
                "db": _DB(),
                "_normalize_user_role": lambda v: str(v or "").strip().lower(),
                "_headed_department_ids": lambda _uid: frozenset(headed),
                "_is_super_admin_role": lambda v: str(v or "").lower() == "super_admin",
                "_is_admin_role": lambda v: str(v or "").lower() in ("admin", "super_admin"),
                "_is_supervisor_role": lambda v: str(v or "").lower() in ("sv", "supervisor"),
                "_is_global_admin_requester": lambda v, _uid=None: (
                    str(v or "").lower() == "super_admin"
                    or (str(v or "").lower() == "admin" and not headed)
                ),
                "_department_scope_id_for_requester": lambda _uid: None,
                "_target_user_supervisor_id": lambda _t: None,
                "_is_employee_accounting_manager": lambda _uid: manager,
            },
        )
        return helper(_user_row(10, requester_role), 10, _user_row(42, target_role))

    def test_hr_reaches_anyone_including_admins(self):
        for target_role in ("operator", "trainee", "trainer", "sv", "admin", "super_admin"):
            with self.subTest(target_role=target_role):
                self.assertTrue(self._call(
                    requester_role="hr_manager", target_role=target_role, manager=True))

    def test_head_of_hr_is_not_locked_into_their_department(self):
        """Ветка кадровика обязана стоять ДО ветки главы отдела."""
        self.assertTrue(self._call(
            requester_role="admin", target_role="operator", manager=True, headed=(1499,)))

    def test_head_of_another_department_is_still_locked(self):
        self.assertFalse(self._call(
            requester_role="admin", target_role="operator", manager=False,
            headed=(367,), target_department=9))

    def test_plain_employee_still_reaches_nobody(self):
        self.assertFalse(self._call(
            requester_role="hr_manager", target_role="operator", manager=False))


# ─────────────────────── Список сотрудников ───────────────────────

def _admin_users_row(user_id, *, role, department_id):
    """Строка ответа /api/admin/users — 54 колонки, читаются по индексам."""
    row = [None] * 54
    row[0] = user_id
    row[1] = f"User {user_id}"
    row[6] = role
    row[7] = "working"
    row[8] = 1.0
    row[48] = department_id
    return row


class AdminUsersScopeTests(unittest.TestCase):
    """/api/admin/users: кого пускают и кого отдают."""

    def _call(self, *, role, headed=(), manager=False, rows=None):
        headed = {int(value) for value in headed}
        captured = {}

        class _Cursor:
            def execute(self, _sql, params=None):
                captured["visible_roles"] = list(params[0]) if params else []

            def fetchall(self):
                return list(rows or [])

        class _DB:
            @contextmanager
            def _get_cursor(self):
                yield _Cursor()

        endpoint = _load_function(
            BOT_PATH,
            "get_admin_users",
            {
                "db": _DB(),
                "jsonify": lambda payload: payload,
                "logging": SimpleNamespace(error=lambda *_a, **_k: None),
                "BACK_OFFICE_EMPLOYEE_ROLES": frozenset(
                    {"hr_manager", "accounting_manager", "marketing_manager"}
                ),
                "PROXY_STATUS_LABELS": {},
                "_build_avatar_signed_url": lambda *_a: None,
                "_get_authenticated_requester": lambda: (10, (10, None, "User 10", role), None),
                "_normalize_user_role": lambda value: str(value or "").strip().lower(),
                "_is_admin_role": lambda value: str(value or "").lower() in ("admin", "super_admin"),
                "_is_super_admin_role": lambda value: str(value or "").lower() == "super_admin",
                "_headed_department_ids": lambda _user_id: frozenset(headed),
                "_department_scope_id_for_requester": lambda _user_id: None,
                "_is_employee_accounting_manager": lambda _user_id: manager,
            },
        )
        return endpoint(), captured

    def test_hr_manager_is_allowed_in(self):
        result, _ = self._call(role="hr_manager", manager=True, rows=[])
        self.assertEqual(_status(result), 200)

    def test_hr_manager_outside_the_department_stays_forbidden(self):
        result, _ = self._call(role="hr_manager", manager=False, rows=[])
        self.assertEqual(_status(result), 403)

    def test_manager_sees_every_department(self):
        rows = [
            _admin_users_row(1, role="operator", department_id=1),
            _admin_users_row(2, role="operator", department_id=367),
            _admin_users_row(3, role="hr_manager", department_id=1499),
        ]
        result, _ = self._call(role="hr_manager", manager=True, rows=rows)
        self.assertEqual(_status(result), 200)
        self.assertEqual([item["id"] for item in _payload(result)["users"]], [1, 2, 3])

    def test_head_of_hr_is_not_narrowed_to_their_own_department(self):
        rows = [
            _admin_users_row(1, role="operator", department_id=1),
            _admin_users_row(3, role="hr_manager", department_id=1499),
        ]
        result, _ = self._call(role="admin", headed=(1499,), manager=True, rows=rows)
        self.assertEqual([item["id"] for item in _payload(result)["users"]], [1, 3])

    def test_head_of_another_department_is_still_narrowed(self):
        rows = [
            _admin_users_row(1, role="operator", department_id=1),
            _admin_users_row(2, role="operator", department_id=367),
        ]
        result, _ = self._call(role="admin", headed=(367,), manager=False, rows=rows)
        self.assertEqual([item["id"] for item in _payload(result)["users"]], [2])

    def test_manager_gets_supervisors_and_admins_in_the_role_filter(self):
        """У кадровика четыре списка — «Супервайзеры» и «Админы» в их числе."""
        _, captured = self._call(role="hr_manager", manager=True, rows=[])
        for role in ("sv", "supervisor", "admin"):
            with self.subTest(role=role):
                self.assertIn(role, captured["visible_roles"])

    def test_super_admins_are_in_nobody_list(self):
        """Ни у супер-админа, ни у кадровика: список админов их не показывает."""
        for role, manager in (("super_admin", False), ("hr_manager", True)):
            with self.subTest(role=role):
                _, captured = self._call(role=role, manager=manager, rows=[])
                self.assertNotIn("super_admin", captured["visible_roles"])

    def test_operator_moved_into_hr_skips_the_thin_projection(self):
        """Ловушка порядка: операторская ветка стоит выше и отдаёт 7 полей."""
        rows = [_admin_users_row(1, role="operator", department_id=1)]
        result, _ = self._call(role="operator", manager=True, rows=rows)
        self.assertEqual(_status(result), 200)
        self.assertIn("department_id", _payload(result)["users"][0])

    def test_plain_operator_still_gets_the_thin_projection(self):
        rows = [(1, "User 1", "operator", 1.0, None, None, None, None)]
        result, _ = self._call(role="operator", manager=False, rows=rows)
        self.assertEqual(_status(result), 200)
        self.assertNotIn("department_id", _payload(result)["users"][0])


# ──────────────────────────── Правка ────────────────────────────

class UpdateUserTests(unittest.TestCase):
    """/api/admin/update_user: кадровик правит любого, включая админа."""

    def _call(self, *, requester_role, target_role="operator", manager=False,
              headed=(), field="phone", value="+77010000001"):
        headed = {int(v) for v in headed}
        saved = []

        class _DB:
            def get_user(self, *, id):
                return _user_row(int(id), target_role)

            def get_user_by_name(self, _name):
                return None

            def get_department_by_id(self, _department_id):
                return {"id": 9, "code": "op"}

            def update_user(self, user_id, field, value, changed_by=None):
                saved.append((user_id, field, value, changed_by))
                return True

        endpoint = _load_function(
            BOT_PATH,
            "admin_update_user",
            {
                "db": _DB(),
                "jsonify": lambda payload: payload,
                "logging": SimpleNamespace(error=lambda *_a, **_k: None),
                "request": SimpleNamespace(get_json=lambda: {
                    "user_id": 42, "field": field, "value": value}),
                "_is_valid_kz_phone": lambda _v: True,
                "normalize_proxy_status_value": lambda v: v,
                "_get_authenticated_requester": lambda: (10, _user_row(10, requester_role), None),
                "_normalize_user_role": lambda v: str(v or "").strip().lower(),
                "_headed_department_id": lambda _uid: (min(headed) if headed else None),
                "_is_admin_role": lambda v: str(v or "").lower() in ("admin", "super_admin"),
                "_is_global_admin_requester": lambda v, _uid=None: (
                    str(v or "").lower() == "super_admin"
                    or (str(v or "").lower() == "admin" and not headed)
                ),
                "_requester_can_access_target_user": lambda *_a, **_k: manager,
                # Подменена ОТКАЗОМ намеренно: так видно, что кадровик её минует.
                "_validate_scoped_user_relation_update": lambda *_a, **_k: (
                    ("Target user has no department", 403)
                ),
                "_is_supervisor_rate_change_day": lambda: False,
                "_is_employee_accounting_manager": lambda _uid: manager,
            },
        )
        return endpoint(), saved

    def test_hr_edits_an_ordinary_employee(self):
        result, saved = self._call(requester_role="hr_manager", manager=True)
        self.assertEqual(_status(result), 200)
        self.assertEqual(saved[0][1], "phone")

    def test_hr_edits_an_admin(self):
        """«Без исключений» — прямой ответ владельца про админов."""
        for target_role in ("admin", "super_admin"):
            with self.subTest(target_role=target_role):
                result, _ = self._call(
                    requester_role="hr_manager", target_role=target_role, manager=True)
                self.assertEqual(_status(result), 200)

    def test_hr_moves_a_person_between_departments(self):
        result, saved = self._call(
            requester_role="hr_manager", manager=True, field="department_id", value=9)
        self.assertEqual(_status(result), 200)
        self.assertEqual(saved[0][1], "department_id")

    def test_hr_changes_the_rate_any_day(self):
        result, saved = self._call(
            requester_role="hr_manager", manager=True, field="rate", value=0.5)
        self.assertEqual(_status(result), 200)
        self.assertEqual(saved[0][2], 0.5)

    def test_hr_skips_the_scoped_relation_check(self):
        """Проверка «направление из отдела сотрудника» — про суженного управленца.

        У части людей отдел не проставлен вовсе, и она отбивала бы правку с
        «Target user has no department».
        """
        result, _ = self._call(
            requester_role="hr_manager", manager=True, field="direction_id", value=3)
        self.assertEqual(_status(result), 200)

    def test_outsider_is_still_refused(self):
        result, _ = self._call(requester_role="operator", manager=False)
        self.assertEqual(_status(result), 403)

    def test_admin_target_stays_closed_to_a_plain_admin(self):
        """Кадровика впустили не «заодно всем» — обычному админу по-прежнему нет."""
        result, _ = self._call(
            requester_role="admin", target_role="admin", manager=False)
        self.assertEqual(_status(result), 403)


class CredentialsTests(unittest.TestCase):
    """Логин и пароль: «без исключений», включая админов."""

    def _call(self, endpoint_name, *, requester_role, target_role, manager, field_value):
        payload_key = "new_login" if endpoint_name == "change_login" else "new_password"
        changed = []

        class _DB:
            def get_user(self, *, id):
                return _user_row(int(id), target_role)

            def get_user_by_login(self, _login):
                return None

            # Ручка логина зовёт именно update_operator_login — у неё своя
            # сигнатура с supervisor_id посередине.
            def update_operator_login(self, user_id, _supervisor_id, value):
                changed.append((user_id, value))
                return True

            def update_user_password(self, user_id, value):
                changed.append((user_id, value))
                return True

        endpoint = _load_function(
            BOT_PATH,
            endpoint_name,
            {
                "db": _DB(),
                "jsonify": lambda payload: payload,
                "logging": SimpleNamespace(error=lambda *_a, **_k: None),
                "request": SimpleNamespace(get_json=lambda: {
                    "user_id": 42, payload_key: field_value}),
                "requests": SimpleNamespace(post=lambda *_a, **_k: None),
                "API_TOKEN": "x",
                "_get_authenticated_requester": lambda: (10, _user_row(10, requester_role), None),
                "_normalize_user_role": lambda v: str(v or "").strip().lower(),
                "_headed_department_id": lambda _uid: None,
                "_is_super_admin_role": lambda v: str(v or "").lower() == "super_admin",
                "_is_admin_role": lambda v: str(v or "").lower() in ("admin", "super_admin"),
                "_is_supervisor_role": lambda v: str(v or "").lower() in ("sv", "supervisor"),
                "_requester_can_access_target_user": lambda *_a, **_k: manager,
                "_is_employee_accounting_manager": lambda _uid: manager,
            },
        )
        return endpoint(), changed

    def test_hr_resets_password_of_an_admin(self):
        result, changed = self._call(
            "change_password", requester_role="hr_manager", target_role="admin",
            manager=True, field_value="Новый-пароль-1")
        self.assertEqual(_status(result), 200)
        self.assertTrue(changed)

    def test_hr_changes_login_of_an_admin(self):
        result, changed = self._call(
            "change_login", requester_role="hr_manager", target_role="admin",
            manager=True, field_value="new_login_1")
        self.assertEqual(_status(result), 200)
        self.assertTrue(changed)

    def test_outsider_is_still_refused(self):
        result, changed = self._call(
            "change_password", requester_role="hr_manager", target_role="operator",
            manager=False, field_value="Новый-пароль-1")
        self.assertEqual(_status(result), 403)
        self.assertFalse(changed)


class CreateAndTransferTests(unittest.TestCase):
    """Заводить новых и переводить между категориями — «полный набор»."""

    def test_hr_creates_supervisors_and_picks_the_department(self):
        add_user = _function_source(BOT_PATH, "add_user")
        self.assertIn(
            "if role == 'sv' and not _is_admin_role(requester_role) and not personnel_manager:",
            add_user,
        )
        # Отдел нового сотрудника кадровик выбирает сам: сервер переписывает его
        # только СУЖЕННОМУ управленцу.
        self.assertIn(
            "if _is_global_admin_requester(requester_role, requester_id) or personnel_manager:",
            add_user,
        )

    def test_creating_an_admin_stays_with_the_super_admin(self):
        """Единственное, чего кадровику не дали: выдачу админских прав."""
        add_user = _function_source(BOT_PATH, "add_user")
        self.assertIn(
            "if role == 'admin' and requester_role != 'super_admin':",
            add_user,
        )
        self.assertNotIn(
            "role == 'admin' and requester_role != 'super_admin' and not personnel_manager",
            add_user,
        )
        # И кнопки такой у кадровика нет — иначе она кончалась бы отказом.
        self.assertIn("canAdd: isSuperAdmin,", _read(APP_PATH))

    def test_promote_and_demote_are_open(self):
        promote = _function_source(BOT_PATH, "admin_promote_to_supervisor")
        self.assertIn("_is_employee_accounting_manager(requester_id)", promote)
        demote = _function_source(BOT_PATH, "admin_demote_to_operator")
        self.assertIn("_is_employee_accounting_manager(requester_id)", demote)

    def test_bulk_edit_is_open(self):
        bulk = _function_source(BOT_PATH, "admin_bulk_update_users")
        self.assertIn("personnel_manager = _is_employee_accounting_manager(requester_id)", bulk)
        # Группа чужого отдела кадровику не «чужая»: своих групп у него нет.
        self.assertIn(
            "if not _is_global_admin_requester(requester_role, requester_id) and not personnel_manager:",
            bulk,
        )


class CatalogsTests(unittest.TestCase):
    """Справочники, без которых карточка показывала бы пустые селекты."""

    def test_directions_are_open_and_unscoped(self):
        endpoint = _function_source(BOT_PATH, "get_directions")
        self.assertIn(
            "employee_accounting_manager = _is_employee_accounting_manager(requester_id)",
            endpoint,
        )
        self.assertIn(
            "if _is_global_admin_requester(role, requester_id) or employee_accounting_manager:",
            endpoint,
        )

    def test_groups_are_open_and_unscoped(self):
        helper = _function_source(BOT_PATH, "_scoped_groups_for_requester")
        self.assertIn("if _is_employee_accounting_manager(requester_id):", helper)
        # Ветка обязана стоять ДО ветки главы отдела: у отдела кадров своих
        # групп нет, и та отдала бы пустой список.
        self.assertLess(
            helper.index("_is_employee_accounting_manager(requester_id)"),
            helper.index("if headed_dept_id is not None"),
        )

    def test_moving_between_groups_is_open(self):
        self.assertIn(
            "or _is_employee_accounting_manager(requester_id)):",
            _function_source(BOT_PATH, "_ensure_group_operator_manager"),
        )
        self.assertIn(
            "_is_global_admin_requester(role, requester_id) or _is_employee_accounting_manager(requester_id)",
            _function_source(BOT_PATH, "_ensure_group_in_requester_scope"),
        )

    def test_supervisor_list_is_not_narrowed_for_hr(self):
        """Иначе глава отдела кадров видит пустых «Супервайзеров», а рядовой
        кадровик — полный список: два разных экрана у равных прав."""
        endpoint = _function_source(BOT_PATH, "get_sv_list")
        self.assertIn(
            "if headed_dept_ids and not is_global_admin and not employee_accounting_manager:",
            endpoint,
        )
        self.assertIn(
            "include_full_profile = is_global_admin or bool(headed_dept_ids) or employee_accounting_manager",
            endpoint,
        )

    def test_avatar_is_open(self):
        self.assertIn(
            "if _is_employee_accounting_manager(requester_id):",
            _function_source(BOT_PATH, "_resolve_avatar_target_user"),
        )


# ─────────────────── Чтение: списки, история, выгрузка ───────────────────

class DepartmentsDirectoryTests(unittest.TestCase):
    """/api/admin/departments: кадровику нужен весь справочник."""

    def _call(self, *, role, headed=(), manager=False):
        headed = {int(value) for value in headed}
        all_departments = [{"id": 1, "code": "szov"}, {"id": 1499, "code": "hr"}]

        class _DB:
            def get_departments(self):
                return list(all_departments)

            def get_department_by_id(self, department_id):
                return next((d for d in all_departments if d["id"] == department_id), None)

        endpoint = _load_function(
            BOT_PATH,
            "api_admin_departments",
            {
                "db": _DB(),
                "jsonify": lambda payload: payload,
                "logging": SimpleNamespace(error=lambda *_a, **_k: None),
                "request": SimpleNamespace(method="GET"),
                "_build_cors_preflight_response": lambda: None,
                "_get_authenticated_requester": lambda: (10, (10, None, "User 10", role), None),
                "_normalize_user_role": lambda value: str(value or "").strip().lower(),
                "_headed_department_ids": lambda _user_id: frozenset(headed),
                "_is_super_admin_role": lambda value: str(value or "").lower() == "super_admin",
                "_is_admin_role": lambda value: str(value or "").lower() in ("admin", "super_admin"),
                "_is_marketing_observer": lambda *_a, **_k: False,
                "_is_employee_accounting_manager": lambda _user_id: manager,
            },
        )
        return endpoint()

    def test_hr_manager_gets_the_whole_directory(self):
        result = self._call(role="hr_manager", manager=True)
        self.assertEqual(_status(result), 200)
        self.assertEqual(len(_payload(result)["departments"]), 2)

    def test_head_of_hr_gets_the_whole_directory_too(self):
        result = self._call(role="admin", headed=(1499,), manager=True)
        self.assertEqual(len(_payload(result)["departments"]), 2)

    def test_head_of_another_department_still_gets_only_their_own(self):
        result = self._call(role="admin", headed=(1,), manager=False)
        self.assertEqual(len(_payload(result)["departments"]), 1)

    def test_plain_employee_is_still_forbidden(self):
        result = self._call(role="operator", manager=False)
        self.assertEqual(_status(result), 403)


class UserHistoryTests(unittest.TestCase):
    """/api/user/history: история — то же чтение, только по одному человеку."""

    def _call(self, *, role, target_role="operator", headed=(), manager=False):
        headed = {int(value) for value in headed}

        class _DB:
            def get_user(self, *, id):
                return (int(id), None, f"User {id}", target_role)

            def get_user_history(self, _user_id):
                return [{"field": "phone"}]

        endpoint = _load_function(
            BOT_PATH,
            "get_user_history",
            {
                "db": _DB(),
                "jsonify": lambda payload: payload,
                "logging": SimpleNamespace(error=lambda *_a, **_k: None),
                "request": SimpleNamespace(args={"user_id": "42"}),
                "_get_authenticated_requester": lambda: (10, (10, None, "User 10", role), None),
                "_normalize_user_role": lambda value: str(value or "").strip().lower(),
                "_headed_department_id": lambda _user_id: min(headed) if headed else None,
                "_is_super_admin_role": lambda value: str(value or "").lower() == "super_admin",
                "_is_admin_role": lambda value: str(value or "").lower() in ("admin", "super_admin"),
                "_is_supervisor_role": lambda value: str(value or "").lower() in ("sv", "supervisor"),
                "_requester_can_access_target_user": lambda *_a, **_k: False,
                "_is_employee_accounting_manager": lambda _user_id: manager,
            },
        )
        return endpoint()

    def test_hr_manager_reads_any_employee_history(self):
        result = self._call(role="hr_manager", manager=True)
        self.assertEqual(_status(result), 200)

    def test_head_of_hr_reads_history_outside_their_department(self):
        result = self._call(role="admin", headed=(1499,), manager=True)
        self.assertEqual(_status(result), 200)

    def test_plain_employee_is_still_forbidden(self):
        result = self._call(role="operator", manager=False)
        self.assertEqual(_status(result), 403)


class UsersReportScopeTests(unittest.TestCase):
    """Выгрузка в Excel: тот же охват, что на экране."""

    def _call(self, *, role, headed=(), manager=False):
        headed = {int(value) for value in headed}
        calls = []

        class _DB:
            def get_user(self, *, id):
                return (int(id), None, f"User {id}", role)

            def get_department_by_id(self, _department_id):
                return None

            def generate_users_report(self, **kwargs):
                calls.append(kwargs)
                return "report.xlsx", b"PK"

        import re as _re
        from io import BytesIO

        endpoint = _load_function(
            BOT_PATH,
            "get_users_report",
            {
                "BytesIO": BytesIO,
                "db": _DB(),
                "jsonify": lambda payload: payload,
                "logging": SimpleNamespace(error=lambda *_a, **_k: None),
                "re": _re,
                "request": SimpleNamespace(headers={"X-User-Id": "10"}, args={}),
                "send_file": lambda _stream, **kwargs: kwargs,
                "_normalize_user_role": lambda value: str(value or "").strip().lower(),
                "_is_global_admin_requester": lambda value, _id: (
                    str(value or "").lower() == "super_admin"
                    or (str(value or "").lower() == "admin" and not headed)
                ),
                "_headed_department_ids": lambda _user_id: frozenset(headed),
                "_headed_department_id": lambda _user_id: min(headed) if headed else None,
                "_department_scope_id_for_requester": lambda _user_id: None,
                "_is_employee_accounting_manager": lambda _user_id: manager,
            },
        )
        return endpoint(), calls

    def test_hr_manager_exports_the_whole_company(self):
        result, calls = self._call(role="hr_manager", manager=True)
        self.assertEqual(_status(result), 200)
        self.assertIsNone(calls[0]["department_ids"])

    def test_head_of_hr_exports_the_whole_company(self):
        _, calls = self._call(role="admin", headed=(1499,), manager=True)
        self.assertIsNone(calls[0]["department_ids"])

    def test_head_of_another_department_still_exports_only_their_own(self):
        _, calls = self._call(role="admin", headed=(367,), manager=False)
        self.assertEqual(calls[0]["department_ids"], [367])


# ──────────────────────────── Фронтенд ────────────────────────────

class FrontendPerimeterTests(unittest.TestCase):
    """Зеркало периметра и раздел в портале."""

    @classmethod
    def setUpClass(cls):
        cls.app = _read(APP_PATH)
        cls.views = _read(DEPARTMENT_VIEWS_PATH)

    def test_perimeter_is_a_department_code_not_a_role(self):
        self.assertIn(
            "const EMPLOYEE_ACCOUNTING_DEPARTMENTS = new Set(['hr']);",
            self.views,
        )
        helper = self.views[self.views.index("export const managesEmployeeAccounting"):]
        helper = helper[:helper.index(";") + 1]
        self.assertIn("departmentCodeOf(user)", helper)
        self.assertNotIn("normalizeRole", helper)

    def test_perimeter_lives_where_both_readers_can_reach_it(self):
        """Портал решает по нему, показывать ли раздел, карточка — можно ли
        выбрать отдел. Держи его в App.jsx — карточка бы его не увидела."""
        self.assertIn("managesEmployeeAccounting", self.app)
        self.assertIn("managesEmployeeAccounting", _read(MODAL_PATH))

    def test_backend_mirror_is_named_next_to_the_predicate(self):
        """Расходиться им нельзя: пункт был бы виден, а сервер отвечал 403."""
        comment_at = self.views.index("const EMPLOYEE_ACCOUNTING_DEPARTMENTS = new Set")
        comment = self.views[comment_at - 2200:comment_at]
        self.assertIn("EMPLOYEE_ACCOUNTING_DEPARTMENT_CODE", comment)
        self.assertIn("_is_employee_accounting_manager", comment)

    def test_all_four_lists_are_open(self):
        """Владелец назвал админов, сотрудников и супервайзеров, на вопрос про
        тренеров ответил «да, все четыре списка»."""
        menu_at = self.app.index("const EMPLOYEE_ACCOUNTING_MANAGER_MENU = [")
        menu = self.app[menu_at:self.app.index("];", menu_at)]
        for view in ("sv_list", "manage_users", "manage_trainers", "manage_admins"):
            with self.subTest(view=view):
                self.assertIn(f"view: '{view}'", menu)

    def test_section_is_gated_by_its_own_predicate_not_the_allowlist(self):
        self.assertIn(
            "if (isEmployeeAccountingManager\n"
            "                    && EMPLOYEE_ACCOUNTING_MANAGER_MENU.some((item) => item.view === view)) return;",
            self.app,
        )

    def test_the_guard_puts_hr_before_the_department_head_branch(self):
        """У отдела кадров упрощённый учёт, и ветка главы увела бы его из
        «Супервайзеров» в «Сотрудников»."""
        guard_at = self.app.index("// Гард видимости разделов по отделу (Этап 10)")
        hr_at = self.app.index("if (isEmployeeAccountingManager\n", guard_at)
        head_at = self.app.index(
            "['manage_operators', 'manage_users', 'sv_list', 'manage_trainers'].includes(view)",
            guard_at,
        )
        self.assertLess(hr_at, head_at)

    def test_one_menu_branch_for_the_whole_department(self):
        """Рядовой и глава ходят по одной ветке: права у них совпадают, а две
        копии меню разъехались бы при первой правке."""
        self.assertIn("{isEmployeeAccountingManager && !isAdminLikeRole && (", self.app)
        self.assertIn(
            "{isDepartmentHeadUser && !isEmployeeAccountingManager "
            "&& departmentUsesSimpleEmployeeAccounting(user) && (",
            self.app,
        )
        self.assertIn(
            "{isDepartmentHeadUser && !isEmployeeAccountingManager "
            "&& !departmentUsesSimpleEmployeeAccounting(user) && (",
            self.app,
        )

    def test_section_renders_for_the_manager(self):
        self.assertIn(
            "const isEmployeeAccountingManagerView = isEmployeeAccountingManager\n"
            "                && EMPLOYEE_ACCOUNTING_MANAGER_MENU.some((item) => item.view === view);",
            self.app,
        )
        self.assertIn(
            "{(isAdminLikeRole || isDepartmentHeadAdminEmployeeView "
            "|| isEmployeeAccountingManagerView) && (",
            self.app,
        )
        self.assertIn(
            "{view === 'manage_admins' && (isSuperAdmin || isEmployeeAccountingManager) "
            "&& renderEmployeeDirectorySection({",
            self.app,
        )

    def test_section_data_is_fetched_for_the_manager(self):
        """Кадровик правит карточки: без справочников селекты стоят пустыми."""
        branch_at = self.app.index("} else if (isEmployeeAccountingManager) {")
        branch = self.app[branch_at:self.app.index("}\n", self.app.index("fetchDepartments();", branch_at))]
        for call in ("fetchSvList();", "fetchUsers();", "fetchDirections();", "fetchDepartments();"):
            with self.subTest(call=call):
                self.assertIn(call, branch)

    def test_trainees_are_not_dropped_from_the_list(self):
        self.assertIn(
            "const manageOperatorRoles = (isDepartmentManager || isEmployeeAccountingManager)",
            self.app,
        )

    def test_department_filter_is_available(self):
        self.assertIn(
            "const canFilterByDepartment = isAdminLikeRole || isPlainTrainer "
            "|| isEmployeeAccountingManager;",
            self.app,
        )

    def test_department_directory_is_not_narrowed_for_hr(self):
        """Иначе над списком всей компании висел бы фильтр из одного пункта."""
        self.assertIn(
            "const nextDepartments = (scopedDepartmentId != null && !isEmployeeAccountingManager)",
            self.app,
        )


class FrontendCardTests(unittest.TestCase):
    """Карточка сотрудника у кадровика."""

    @classmethod
    def setUpClass(cls):
        cls.app = _read(APP_PATH)
        cls.modal = _read(MODAL_PATH)

    def test_department_is_not_locked_to_hr(self):
        """Без этого «Отдел» запирался бы на HR и завести человека на линию
        кадровик не смог бы вовсе."""
        self.assertIn(
            "const managesEmployeeAccountingRequester = managesEmployeeAccounting(user);",
            self.modal,
        )
        self.assertIn(
            "const isUnscopedRequester = isAdminLikeRequester || managesEmployeeAccountingRequester;",
            self.modal,
        )
        self.assertIn("const requesterScopeDeptId = isUnscopedRequester", self.modal)
        self.assertIn(
            "const isDeptScoped = !isUnscopedRequester && requesterScopeDeptId != null;",
            self.modal,
        )

    def test_creating_uses_the_department_filter(self):
        """Сервер отдел кадровику не переписывает — форма обязана совпадать."""
        self.assertIn(
            "const createDeptId = (isScopedDepartmentHead && !isEmployeeAccountingManager)",
            self.app,
        )

    def test_no_read_only_mode_is_left_behind(self):
        """Режим просмотра жил ровно до второго решения того же дня. Оставь его —
        и в карточке осталась бы ветка, в которую никто не попадает."""
        self.assertNotIn("readOnly", self.modal)
        self.assertNotIn("uem-view", _read(ROOT / "src" / "styles.css"))
        self.assertNotIn("canEditEmployeeRecord", self.app)

    def test_groups_are_filtered_by_the_employee_department(self):
        """Иначе в селекте группы всех отделов сразу."""
        self.assertIn(
            "if (!isUnscopedRequester || effectiveDeptId == null) return active;",
            self.modal,
        )

    def test_card_shows_group_and_direction_of_a_foreign_department(self):
        """Справочники приходят отфильтрованными по отделу сотрудника: значение
        вне выборки подмешиваем отдельной опцией, иначе поле показывает пустоту."""
        self.assertIn("{userToEdit?.group_name || `Группа #${editedUser.group_id}`}", self.modal)
        self.assertIn("{userToEdit?.direction || `Направление #${editedUser.direction_id}`}", self.modal)


if __name__ == "__main__":
    unittest.main()
