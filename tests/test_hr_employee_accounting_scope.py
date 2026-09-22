"""«Учет сотрудников» у отдела кадров — просмотр по всей компании.

Задача владельца 22.09.2026: открыть раздел отделу `hr` так, чтобы кадровик мог
СМОТРЕТЬ данные и не мог их менять. Решения, которые здесь сторожатся:

* охват — вся компания, а не свой отдел. Кадровый учёт ведётся по всей фирме,
  своим отделом кадровик видел бы трёх человек (то же решение, что у «Отметок»,
  задача #273). Граница отдела снимается и с ГЛАВЫ отдела кадров;
* правка остаётся ровно та, что была: глава правит свой отдел, рядовой кадровик
  не правит никого. Отдельной проверки «кадровик не пишет» в пишущих ручках нет
  и быть не должно — его туда не пускают прежние условия (админ / СВ / глава), и
  тест следит, чтобы они не разъехались;
* признак доступа — ЧЛЕНСТВО В ОТДЕЛЕ, а не роль: у кадровика роль `hr_manager`
  с уровнем как у оператора.

Зеркала: `_is_employee_accounting_observer` в bot_schedule2.py и
`canViewEmployeeAccountingForUser` в src/App.jsx.
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


# ─────────────────────────── Сам предикат ───────────────────────────

class ObserverPredicateTests(unittest.TestCase):
    """Кто считается кадровиком на бэкенде."""

    def _call(self, *, department_code, headed_department_id=None):
        class _DB:
            def get_user_department_id(self, _user_id):
                return 77 if department_code is not None else None

            def get_department_by_id(self, _department_id):
                return {"id": 77, "code": department_code}

        predicate = _load_function(
            BOT_PATH,
            "_is_employee_accounting_observer",
            {
                "db": _DB(),
                # Код отдела берём из модуля, а не переписываем литералом:
                # переименуй его кто-нибудь — и тест должен поехать вместе.
                "EMPLOYEE_ACCOUNTING_OBSERVER_DEPARTMENT_CODE": _module_constant(
                    BOT_PATH, "EMPLOYEE_ACCOUNTING_OBSERVER_DEPARTMENT_CODE"
                ),
                # Настоящий flask.g недоступен: подменяем объектом, на который
                # так же можно повесить атрибут-кеш.
                "g": SimpleNamespace(),
            },
        )
        return predicate(10)

    def test_member_of_hr_is_an_observer(self):
        self.assertTrue(self._call(department_code="hr"))

    def test_code_is_matched_case_insensitively(self):
        self.assertTrue(self._call(department_code="HR"))
        self.assertTrue(self._call(department_code=" Hr "))

    def test_other_departments_are_not_observers(self):
        for code in ("szov", "op", "tez", "front_office", "accounting", "marketing", "it"):
            with self.subTest(code=code):
                self.assertFalse(self._call(department_code=code))

    def test_user_without_a_department_is_not_an_observer(self):
        self.assertFalse(self._call(department_code=None))

    def test_role_is_never_asked(self):
        """Признак — отдел. Роль в теле функции не упоминается вовсе.

        Проверка по роли открыла бы раздел человеку, которого из отдела кадров
        уже перевели, — и закрыла бы кадровику, которого завели оператором.
        """
        source = _function_source(BOT_PATH, "_is_employee_accounting_observer")
        body = source[source.index('"""', source.index('"""') + 3):]
        self.assertNotIn("_normalize_user_role", body)
        self.assertNotIn("role", body.replace("hr_manager", ""))


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

    def _call(self, *, role, headed=(), observer=False, rows=None):
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
                "_is_employee_accounting_observer": lambda _user_id: observer,
            },
        )
        return endpoint(), captured

    def test_hr_manager_is_allowed_in(self):
        result, _ = self._call(role="hr_manager", observer=True, rows=[])
        self.assertEqual(_status(result), 200)

    def test_hr_manager_outside_the_department_stays_forbidden(self):
        result, _ = self._call(role="hr_manager", observer=False, rows=[])
        self.assertEqual(_status(result), 403)

    def test_observer_sees_every_department(self):
        rows = [
            _admin_users_row(1, role="operator", department_id=1),
            _admin_users_row(2, role="operator", department_id=367),
            _admin_users_row(3, role="hr_manager", department_id=1499),
        ]
        result, _ = self._call(role="hr_manager", observer=True, rows=rows)
        self.assertEqual(_status(result), 200)
        self.assertEqual([item["id"] for item in _payload(result)["users"]], [1, 2, 3])

    def test_head_of_hr_is_not_narrowed_to_their_own_department(self):
        """Граница отдела снимается и с главы: список у него тот же, вся фирма.

        Право правки это не трогает — его держит _requester_can_access_target_user
        на пишущих ручках, см. WritesStayClosedTests.
        """
        rows = [
            _admin_users_row(1, role="operator", department_id=1),
            _admin_users_row(3, role="hr_manager", department_id=1499),
        ]
        result, _ = self._call(role="admin", headed=(1499,), observer=True, rows=rows)
        self.assertEqual([item["id"] for item in _payload(result)["users"]], [1, 3])

    def test_head_of_another_department_is_still_narrowed(self):
        rows = [
            _admin_users_row(1, role="operator", department_id=1),
            _admin_users_row(2, role="operator", department_id=367),
        ]
        result, _ = self._call(role="admin", headed=(367,), observer=False, rows=rows)
        self.assertEqual([item["id"] for item in _payload(result)["users"]], [2])

    def test_observer_gets_supervisors_in_the_role_filter(self):
        """СВ — такие же сотрудники фирмы; без них список кадровика неполон."""
        _, captured = self._call(role="hr_manager", observer=True, rows=[])
        self.assertIn("sv", captured["visible_roles"])
        self.assertIn("supervisor", captured["visible_roles"])

    def test_observer_never_gets_admins(self):
        _, captured = self._call(role="hr_manager", observer=True, rows=[])
        self.assertNotIn("admin", captured["visible_roles"])

    def test_operator_moved_into_hr_skips_the_thin_projection(self):
        """Ловушка порядка: операторская ветка стоит выше и отдаёт 7 полей.

        Кадровика могли завести ролью 'operator' до появления hr_manager. Пройди
        он в ту ветку — получил бы список без телефонов, статусов и отдела, то
        есть «раздел открыли, а данных нет».
        """
        rows = [_admin_users_row(1, role="operator", department_id=1)]
        result, _ = self._call(role="operator", observer=True, rows=rows)
        self.assertEqual(_status(result), 200)
        self.assertIn("department_id", _payload(result)["users"][0])

    def test_plain_operator_still_gets_the_thin_projection(self):
        rows = [(1, "User 1", "operator", 1.0, None, None, None, None)]
        result, _ = self._call(role="operator", observer=False, rows=rows)
        self.assertEqual(_status(result), 200)
        self.assertNotIn("department_id", _payload(result)["users"][0])


# ─────────────────── Справочник отделов и история ───────────────────

class DepartmentsDirectoryTests(unittest.TestCase):
    """/api/admin/departments: кадровику нужен весь справочник — под фильтр."""

    def _call(self, *, role, headed=(), observer=False):
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
                "_is_employee_accounting_observer": lambda _user_id: observer,
            },
        )
        return endpoint()

    def test_hr_manager_gets_the_whole_directory(self):
        result = self._call(role="hr_manager", observer=True)
        self.assertEqual(_status(result), 200)
        self.assertEqual(len(_payload(result)["departments"]), 2)

    def test_head_of_hr_gets_the_whole_directory_too(self):
        """Иначе над списком всей компании висел бы фильтр из одного пункта."""
        result = self._call(role="admin", headed=(1499,), observer=True)
        self.assertEqual(len(_payload(result)["departments"]), 2)

    def test_head_of_another_department_still_gets_only_their_own(self):
        result = self._call(role="admin", headed=(1,), observer=False)
        self.assertEqual(len(_payload(result)["departments"]), 1)

    def test_plain_employee_is_still_forbidden(self):
        result = self._call(role="operator", observer=False)
        self.assertEqual(_status(result), 403)


class UserHistoryTests(unittest.TestCase):
    """/api/user/history: история — то же чтение, только по одному человеку."""

    def _call(self, *, role, target_role="operator", headed=(), observer=False):
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
                "_is_employee_accounting_observer": lambda _user_id: observer,
            },
        )
        return endpoint()

    def test_hr_manager_reads_any_employee_history(self):
        result = self._call(role="hr_manager", observer=True)
        self.assertEqual(_status(result), 200)

    def test_head_of_hr_reads_history_outside_their_department(self):
        """Список у главы — вся фирма; «История» обязана открываться там же."""
        result = self._call(role="admin", headed=(1499,), observer=True)
        self.assertEqual(_status(result), 200)

    def test_admin_history_stays_closed_to_hr(self):
        result = self._call(role="hr_manager", target_role="admin", observer=True)
        self.assertEqual(_status(result), 403)

    def test_plain_employee_is_still_forbidden(self):
        result = self._call(role="operator", observer=False)
        self.assertEqual(_status(result), 403)


class UsersReportScopeTests(unittest.TestCase):
    """Выгрузка в Excel: тот же охват, что на экране."""

    def _call(self, *, role, headed=(), observer=False):
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
                "_is_employee_accounting_observer": lambda _user_id: observer,
            },
        )
        return endpoint(), calls

    def test_hr_manager_exports_the_whole_company(self):
        result, calls = self._call(role="hr_manager", observer=True)
        self.assertEqual(_status(result), 200)
        self.assertIsNone(calls[0]["department_ids"])

    def test_head_of_hr_exports_the_whole_company(self):
        """Ветка кадровика обязана стоять ДО ветки главы отдела."""
        _, calls = self._call(role="admin", headed=(1499,), observer=True)
        self.assertIsNone(calls[0]["department_ids"])

    def test_head_of_another_department_still_exports_only_their_own(self):
        _, calls = self._call(role="admin", headed=(367,), observer=False)
        self.assertEqual(calls[0]["department_ids"], [367])


# ───────────────────────── Запись закрыта ─────────────────────────

class WritesStayClosedTests(unittest.TestCase):
    """Раздел открыт чтением. Пишущие ручки о кадровике знать не должны.

    Рядового кадровика не пускает прежнее условие «админ / СВ / глава»: роль
    hr_manager не входит ни в одно из них. Тест следит, чтобы условие не
    ослабили и чтобы кадровика не вписали туда «заодно».
    """

    WRITE_ENDPOINTS = (
        "admin_update_user",
        "admin_bulk_update_users",
        "add_user",
        "admin_promote_to_supervisor",
    )

    def test_no_write_endpoint_knows_about_the_observer(self):
        for name in self.WRITE_ENDPOINTS:
            with self.subTest(endpoint=name):
                self.assertNotIn(
                    "_is_employee_accounting_observer",
                    _function_source(BOT_PATH, name),
                )

    def test_point_and_bulk_edits_still_require_admin_sv_or_head(self):
        guard = ("if requester_role not in ('super_admin', 'admin', 'sv') and headed_dept_id is None:")
        for name in ("admin_update_user", "admin_bulk_update_users"):
            with self.subTest(endpoint=name):
                self.assertIn(guard, _function_source(BOT_PATH, name))

    def test_creating_an_employee_still_requires_admin_sv_or_head(self):
        self.assertIn(
            "if requester_role not in ('super_admin', 'admin', 'sv') and requester_headed_dept is None:",
            _function_source(BOT_PATH, "add_user"),
        )

    def test_head_of_hr_keeps_the_department_boundary_on_writes(self):
        """Расширился охват чтения, а не правки: граница держится тут."""
        helper = _function_source(BOT_PATH, "_requester_can_access_target_user")
        self.assertIn("headed_dept_ids = _headed_department_ids(requester_id)", helper)
        self.assertIn("int(target_department_id) in headed_dept_ids", helper)
        self.assertNotIn("_is_employee_accounting_observer", helper)


# ──────────────────────────── Фронтенд ────────────────────────────

class FrontendPerimeterTests(unittest.TestCase):
    """Зеркало периметра в src/App.jsx."""

    @classmethod
    def setUpClass(cls):
        cls.app = _read(APP_PATH)

    def test_perimeter_is_a_department_code_not_a_role(self):
        self.assertIn(
            "const EMPLOYEE_ACCOUNTING_OBSERVER_DEPARTMENT_CODES = new Set(['hr']);",
            self.app,
        )
        self.assertIn(
            "const canViewEmployeeAccountingForUser = (userLike) => "
            "EMPLOYEE_ACCOUNTING_OBSERVER_DEPARTMENT_CODES.has(",
            self.app,
        )
        predicate_at = self.app.index("const canViewEmployeeAccountingForUser")
        predicate = self.app[predicate_at:self.app.index(");", predicate_at)]
        self.assertNotIn("normalizeRole", predicate)
        self.assertIn("department_code", predicate)

    def test_backend_mirror_is_named_in_the_comment(self):
        """Расходиться им нельзя: пункт был бы виден, а сервер отвечал 403."""
        comment_at = self.app.index("EMPLOYEE_ACCOUNTING_OBSERVER_DEPARTMENT_CODES")
        comment = self.app[comment_at - 1400:comment_at]
        self.assertIn("EMPLOYEE_ACCOUNTING_OBSERVER_DEPARTMENT_CODE", comment)
        self.assertIn("_is_employee_accounting_observer", comment)

    def test_section_is_gated_by_its_own_predicate_not_the_allowlist(self):
        """У отдела кадров есть allowlist, и без раннего return гард видимости
        выбрасывал бы кадровика в «Профиль»."""
        self.assertIn(
            "if (view === 'manage_users' && isEmployeeAccountingObserver) return;",
            self.app,
        )

    def test_sidebar_item_lives_in_the_common_block(self):
        """У роли hr_manager своей ветки в сайдбаре нет — в ролевых блоках
        пункт не отрисовался бы вовсе (та же причина, что у «Отметок»)."""
        self.assertIn(
            "{isEmployeeAccountingObserver && !isAdminLikeRole && !isDepartmentManager "
            "&& !isPlainTrainer && (",
            self.app,
        )
        menu_at = self.app.index("{isEmployeeAccountingObserver && !isAdminLikeRole")
        first_role_branch = self.app.index("{isAdminLikeRole && (\n                                        <>")
        self.assertLess(menu_at, first_role_branch)

    def test_sidebar_item_is_not_duplicated_for_the_head(self):
        """Глава отдела кадров проходит как isDepartmentManager и получает
        пункт в своей ветке — условие выше обязано его исключать."""
        self.assertEqual(
            self.app.count("<span className=\"sidebar-text\">Учет сотрудников</span>"),
            6,
        )

    def test_section_renders_for_the_observer(self):
        self.assertIn(
            "const isEmployeeAccountingObserverView = isEmployeeAccountingObserver "
            "&& view === 'manage_users';",
            self.app,
        )
        self.assertIn(
            "{(isAdminLikeRole || isDepartmentHeadAdminEmployeeView "
            "|| isEmployeeAccountingObserverView) && (",
            self.app,
        )

    def test_section_data_is_fetched_for_the_observer(self):
        self.assertIn("} else if (isEmployeeAccountingObserver) {", self.app)
        branch_at = self.app.index("} else if (isEmployeeAccountingObserver) {")
        branch = self.app[branch_at:self.app.index("}\n", self.app.index("fetchDepartments();", branch_at))]
        self.assertIn("fetchUsers();", branch)
        self.assertIn("fetchDepartments();", branch)

    def test_trainees_are_not_dropped_from_the_observer_list(self):
        """Узкий набор ролей съел бы стажёров — их в фирме больше, чем кадровиков."""
        self.assertIn(
            "const manageOperatorRoles = (isDepartmentManager || isEmployeeAccountingObserver)",
            self.app,
        )

    def test_department_filter_is_available_to_the_observer(self):
        self.assertIn(
            "const canFilterByDepartment = isAdminLikeRole || isPlainTrainer "
            "|| isEmployeeAccountingObserver;",
            self.app,
        )


class FrontendReadOnlyTests(unittest.TestCase):
    """Что кадровик в разделе НЕ может."""

    @classmethod
    def setUpClass(cls):
        cls.app = _read(APP_PATH)
        cls.modal = _read(MODAL_PATH)

    def test_edit_right_is_computed_per_employee(self):
        self.assertIn(
            "const canEditEmployeeRecord = useCallback((employee) => (\n"
            "                !isEmployeeAccountingObserver "
            "|| canEditEmployeeRecordForUser(user, employee)\n"
            "            ), [isEmployeeAccountingObserver, user]);",
            self.app,
        )

    def test_only_the_head_edits_and_only_their_own_department(self):
        helper_at = self.app.index("const canEditEmployeeRecordForUser = (userLike, employee) => {")
        helper = self.app[helper_at:self.app.index("\n};", helper_at)]
        self.assertIn("if (!isDepartmentHead(userLike)) return false;", helper)
        self.assertIn("Number(employee.department_id) === Number(headed)", helper)

    def test_creating_is_left_to_the_head(self):
        self.assertIn(
            "const canCreateEmployeeRecord = !isEmployeeAccountingObserver || isDepartmentHeadUser;",
            self.app,
        )
        self.assertIn("{canCreateEmployeeRecord && (", self.app)
        self.assertIn(
            "onAdd: canCreateEmployeeRecord ? openCreateManageUsersEmployee : null,",
            self.app,
        )

    def test_bulk_edit_is_off_for_the_whole_hr_department(self):
        """Из полусотни отмеченных строк глава может изменить только своих —
        остальные вернулись бы ошибкой по одной."""
        self.assertIn("const canBulkEditEmployees = !isEmployeeAccountingObserver;", self.app)
        self.assertIn("if (!canBulkEditEmployees) return;", self.app)
        self.assertIn("selection: !canBulkEditEmployees ? null : {", self.app)

    def test_row_menu_keeps_only_reading_for_the_observer(self):
        self.assertIn("{canEditEmployeeRecord(u) ? 'Править' : 'Карточка'}", self.app)
        self.assertIn("{canEditEmployeeRecord(u) && (", self.app)
        self.assertIn(
            "isAdminLikeRole && canEditEmployeeRecord(employee) && {",
            self.app,
        )

    def test_head_of_hr_creates_in_their_own_department(self):
        """Сервер всё равно заведёт человека в отдел главы: подставив отдел из
        фильтра, форма выбрала бы чужую роль по чужому коду отдела."""
        self.assertIn(
            'const createDeptId = isScopedDepartmentHead ? "" : (manageUsersDeptFilter || "");',
            self.app,
        )

    def test_card_opens_read_only_only_for_an_existing_employee(self):
        """Форму создания запирать нечем: отдела у черновика ещё нет."""
        self.assertIn(
            "readOnly={Boolean(userToEdit?.id) && !canEditEmployeeRecord(userToEdit)}",
            self.app,
        )

    def test_card_locks_every_field_with_one_flag(self):
        self.assertIn(
            "const fieldsLocked = isLoading || !!createdCredentials || readOnly;",
            self.modal,
        )
        # Ни одно поле не осталось на старом запоре: иначе карточка кадровика
        # сохранила бы правку в одной вкладке из четырёх.
        self.assertEqual(self.modal.count("disabled={isLoading || !!createdCredentials}"), 0)
        body = self.modal[self.modal.index("const fieldsLocked"):]
        self.assertEqual(body.count("disabled={isLoading}"), 1)  # кнопка «Отмена»

    def test_card_is_titled_as_a_card_not_as_editing(self):
        """Карандаш и слово «Редактировать» обещали бы правку, которой нет."""
        self.assertIn(
            '{isCreateMode ? "Добавить сотрудника" : (readOnly ? "Карточка сотрудника" : "Редактировать сотрудника")}',
            self.modal,
        )
        self.assertIn("readOnly ? 'fa-id-card' : 'fa-pen'", self.modal)

    def test_read_only_fields_do_not_look_like_inputs(self):
        """Иначе экран выглядит формой, которая молча ничего не сохраняет."""
        self.assertIn("${readOnly ? ' uem-view' : ''}", self.modal)
        # Правила — в ОБЩЕМ стиле: слой user-edit-mobile.css заперт на
        # body.mobile-shell целиком (tests/test_employees_mobile.py), а режим
        # просмотра одинаков на телефоне и на компьютере.
        css = _read(ROOT / "src" / "styles.css")
        self.assertIn('.uem-view input:not([type="checkbox"]):disabled,', css)
        self.assertNotIn(
            "uem-view",
            _read(ROOT / "src" / "components" / "modals" / "user-edit-mobile.css"),
        )
        # Вес селектора (0,2,1) против (0,1,0) у утилит Tailwind: при равном
        # весе выигрывали бы они — утилиты лежат в бандле ниже.
        self.assertNotIn(".uem-view input:disabled {", css)

    def test_card_has_no_save_button_and_says_why(self):
        self.assertIn("{!createdCredentials && readOnly && (", self.modal)
        self.assertIn("Карточка открыта на просмотр — изменения не сохраняются.", self.modal)
        self.assertIn("{!createdCredentials && !readOnly && isMobileShell && (", self.modal)
        self.assertIn("{!createdCredentials && !readOnly && !isMobileShell && (", self.modal)

    def test_account_tab_is_hidden_in_read_only(self):
        """Вкладка — это ввод нового логина и пароля; текущих она не показывает."""
        self.assertIn("...(readOnly ? [] : [{", self.modal)

    def test_card_shows_group_and_direction_of_a_foreign_department(self):
        """Справочники приходят по отделу смотрящего, а карточку кадровик
        открывает на всю компанию: без подмеса значение показалось бы пустым."""
        self.assertIn("{userToEdit?.group_name || `Группа #${editedUser.group_id}`}", self.modal)
        self.assertIn("{userToEdit?.direction || `Направление #${editedUser.direction_id}`}", self.modal)

    def test_save_is_refused_in_code_too(self):
        save_at = self.modal.index("const handleSave = async () => {")
        self.assertIn("if (readOnly) return;", self.modal[save_at:save_at + 500])


if __name__ == "__main__":
    unittest.main()
