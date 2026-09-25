"""ООЗ — отдел обработки запросов (задача #359).

Сотрудник ООЗ заводится оператором и зачисляется в группу, но на линию не
выходит: в портале у него только «Рассылки», а в карточке нет направления,
SIP-номера, практики, обучения во фронт офисе и ID таксипро. Зато есть
«Должность».

Поведение карты разделов и предикатов полей гоняет настоящий Node
(tests/request_processing_department_views.test.mjs); здесь — мост к нему,
серверная половина (ручка создания сотрудника не должна требовать направление)
и проводка в карточке, списке и меню.
"""
import ast
import re
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DEPARTMENT_VIEWS_PATH = ROOT / "src" / "utils" / "departmentViews.js"
APP_PATH = ROOT / "src" / "App.jsx"
MODAL_PATH = ROOT / "src" / "components" / "modals" / "UserEditModal.jsx"
MOBILE_EMPLOYEES_PATH = ROOT / "src" / "components" / "employees" / "EmployeesMobileView.jsx"
NODE_TEST = ROOT / "tests" / "request_processing_department_views.test.mjs"

CODE = "request_processing_department"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _function_source(path, function_name):
    source = _read(path)
    module = source_cache.parse(source)
    function_node = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return textwrap.dedent(ast.get_source_segment(source, function_node))


class RequestProcessingViewsRuntimeTests(unittest.TestCase):

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_request_processing_views_runtime(self):
        completed = subprocess.run(
            [shutil.which("node") or "node", "--test", str(NODE_TEST)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


class RequestProcessingBackendTests(unittest.TestCase):
    """Сервер проверяет direction_id сам: скрыть поле на фронте мало."""

    def test_backend_set_mirrors_the_frontend(self):
        bot = _read(BOT_PATH)
        self.assertIn(
            "EMPLOYEE_DIRECTION_HIDDEN_DEPARTMENT_CODES = frozenset({'request_processing_department'})",
            bot,
        )
        views = _read(DEPARTMENT_VIEWS_PATH)
        self.assertIn(
            "const EMPLOYEE_DIRECTION_HIDDEN_DEPARTMENTS = new Set(['request_processing_department']);",
            views,
        )

    def test_direction_helper_runtime(self):
        departments = {
            1: {'code': 'szov'}, 367: {'code': 'op'}, 560: {'code': 'tez'},
            909: {'code': 'front_office'}, 1499: {'code': 'hr'},
            2008: {'code': CODE},
        }

        class _Db:
            def get_department_by_id(self, department_id):
                return departments.get(department_id)

        namespace = {'db': _Db()}
        exec("OPERATOR_FIELDS_HIDDEN_DEPARTMENT_CODES = frozenset({'accounting', 'hr', 'marketing'})", namespace)
        exec("EMPLOYEE_DIRECTION_HIDDEN_DEPARTMENT_CODES = frozenset({'request_processing_department'})", namespace)
        exec(_function_source(BOT_PATH, "_department_hides_employee_direction"), namespace)
        exec(_function_source(BOT_PATH, "_department_hides_operator_line_fields"), namespace)
        hides_direction = namespace["_department_hides_employee_direction"]
        hides_line = namespace["_department_hides_operator_line_fields"]

        self.assertTrue(hides_direction(2008))
        self.assertTrue(hides_direction('2008'), 'id строкой из JSON')
        # Группа у ООЗ есть: «линейные» поля целиком не сняты.
        self.assertFalse(hides_line(2008))
        for department_id in (1, 367, 560, 909, 1499):
            self.assertFalse(hides_direction(department_id), departments[department_id]['code'])
        # Отдел неизвестен — проверку не снимаем.
        self.assertFalse(hides_direction(None))
        self.assertFalse(hides_direction(999999))
        self.assertFalse(hides_direction('не число'))

    def test_add_user_resolves_department_before_the_direction_check(self):
        endpoint = _function_source(BOT_PATH, "add_user")
        resolved_at = endpoint.index(
            "direction_hidden = line_fields_hidden or _department_hides_employee_direction(department_id)")
        checked_at = endpoint.index('"error": "Missing required field: direction_id"')
        self.assertLess(resolved_at, checked_at)
        # Группу сервер по-прежнему принимает у оператора любого отдела.
        self.assertIn("if role in ('operator', 'trainee'):\n            group_raw = data.get('group_id')", endpoint)


class RequestProcessingCardTests(unittest.TestCase):
    """Карточка: оба режима (создание и правка) — поле, забытое в одном, всплыло
    бы у того, кто открыл уже заведённого сотрудника."""

    def test_modal_flags(self):
        modal = _read(MODAL_PATH)
        for line in (
            "const showDirectionField = showOperatorLineFields && !departmentCodeHidesEmployeeDirection(effectiveDeptCode);",
            "const showSipField = showOperatorLineFields && !departmentCodeHidesEmployeeSipInput(effectiveDeptCode);",
            "const showInternshipField = !departmentCodeHidesEmployeeInternship(effectiveDeptCode);",
            "const showTaxiproIdField = !departmentCodeHidesEmployeeTaxiproId(effectiveDeptCode);",
        ):
            self.assertIn(line, modal)

    def test_every_hidden_field_is_gated_in_both_modes(self):
        modal = _read(MODAL_PATH)
        cases = (
            ("{showInternshipField && (", "<span>Проходил практику в компании</span>"),
            ("{showTaxiproIdField && (", ">ID таксипро</label>"),
            ("&& showDirectionField && (", ">Направление</label>"),
            ("{showSipField && (", ">SIP номер</label>"),
            ("{showFrontOfficeTraining && (", "<span>Был во фронт офисе на обучении</span>"),
        )
        for gate, label in cases:
            with self.subTest(label=label):
                self.assertEqual(2, modal.count(label), 'поле в обоих режимах карточки')
                self.assertEqual(2, modal.count(gate))
                # Каждое вхождение подписи — внутри своего гейта: ближайший
                # гейт выше подписи стоит не дальше пары десятков строк.
                for match in re.finditer(re.escape(label), modal):
                    head = modal[:match.start()]
                    gate_at = head.rfind(gate)
                    self.assertGreater(gate_at, 0, label)
                    self.assertLess(head.count('\n', gate_at), 25, label)

    def test_direction_is_not_required_where_it_is_hidden(self):
        modal = _read(MODAL_PATH)
        self.assertIn("if (isOperatorUser && showDirectionField && !editedUser.direction_id) {", modal)
        # Группа остаётся обязательной — ради неё владелец и заводит группу ООЗ.
        self.assertIn(
            "if (isCreateMode && isOperatorUser && showOperatorLineFields && !editedUser.group_id) {",
            modal,
        )


class RequestProcessingEmployeeListTests(unittest.TestCase):

    def test_columns_follow_the_department(self):
        app = _read(APP_PATH)
        self.assertIn("direction: !departmentCodeHidesEmployeeDirection(code),", app)
        self.assertIn("internship: !departmentCodeHidesEmployeeInternship(code),", app)
        self.assertIn("taxiproId: !departmentCodeHidesEmployeeTaxiproId(code),", app)
        self.assertIn("...(isOperatorVariant && employeeDeptFields.direction ? [", app)
        self.assertIn("...(employeeDeptFields.internship ? [", app)
        self.assertIn("...(employeeDeptFields.taxiproId ? [", app)

    def test_bulk_edit_drops_direction(self):
        app = _read(APP_PATH)
        self.assertIn("showDirection: manageUsersDeptFields.direction,", app)
        mobile = _read(MOBILE_EMPLOYEES_PATH)
        self.assertIn("{bulk.showGroupAndDirection && bulk.showDirection !== false && (", mobile)


class RequestProcessingMenuTests(unittest.TestCase):
    """Пункт «Рассылки» у рядового: без него раздел открывался бы сотруднику ООЗ
    только прямым адресом."""

    def test_rank_and_file_branch_has_mailings(self):
        app = _read(APP_PATH).replace('\r\n', '\n')
        marker = "{isRankAndFileRole(currentUserRole) && !isScopedDepartmentHead && ("
        parts = app.split(marker)
        self.assertEqual(3, len(parts), 'ветки рядового изменились — проверь тест')
        menu = parts[1].split("\n" + " " * 40 + "</>\n" + " " * 36 + ")}")[0]
        self.assertIn("{canAccessDriverMailings && (", menu)
        self.assertIn("handleSidebarViewNavigation(e, 'driver_mailings')", menu)
        # Гейт — именной предикат, не карта отдела: карта пускает в раздел
        # весь ООЗ, а кнопку отправки — только названных людей.
        self.assertNotIn("departmentAllowsView(user, 'driver_mailings')", menu)

    def test_screen_is_rendered_outside_role_branches(self):
        app = _read(APP_PATH)
        self.assertIn('{( view === "driver_mailings" && canAccessDriverMailings && (', app)
        self.assertIn("if (view === 'driver_mailings' && canAccessDriverMailings) return;", app)


if __name__ == "__main__":
    unittest.main()
