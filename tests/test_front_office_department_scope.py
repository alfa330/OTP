import ast
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DEPARTMENT_VIEWS_PATH = ROOT / "src" / "utils" / "departmentViews.js"
APP_PATH = ROOT / "src" / "App.jsx"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _function_source(path, function_name):
    source = _read(path)
    module = ast.parse(source)
    function_node = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return textwrap.dedent(ast.get_source_segment(source, function_node))


class FrontOfficeViewAllowlistTests(unittest.TestCase):
    """Отдел «Фронт офисы» (front_office): менеджеры — только учёт сотрудников
    и графики работы; сотрудники — только профиль и «Мои смены»."""

    def test_front_office_allowlist_entry(self):
        source = _read(DEPARTMENT_VIEWS_PATH)

        self.assertIn("const FRONT_OFFICE_OPERATOR_VIEWS = ['profile', 'work_schedules'];", source)
        self.assertIn("const FRONT_OFFICE_MANAGER_VIEWS = ['manage_operators', 'work_schedules'];", source)
        self.assertIn("front_office: {", source)

        entry = source.split("front_office: {", 1)[1].split("},", 1)[0]
        self.assertIn("operator: FRONT_OFFICE_OPERATOR_VIEWS", entry)
        self.assertIn("trainee: FRONT_OFFICE_OPERATOR_VIEWS", entry)
        self.assertIn("head: FRONT_OFFICE_MANAGER_VIEWS", entry)
        self.assertIn("sv: FRONT_OFFICE_MANAGER_VIEWS", entry)

    def test_colleague_schedule_hiding_helper(self):
        source = _read(DEPARTMENT_VIEWS_PATH)

        self.assertIn("const COLLEAGUE_SCHEDULES_HIDDEN_DEPARTMENTS = new Set(['front_office']);", source)
        self.assertIn("export const departmentHidesColleagueSchedules = (user) =>", source)
        self.assertIn("COLLEAGUE_SCHEDULES_HIDDEN_DEPARTMENTS.has(code)", source)


class FrontOfficeMyShiftsFrontendTests(unittest.TestCase):
    """«Мои смены» оператора front_office: без табов «Замены»/«Смены коллег»
    и без кнопок «Обменять» — оператор видит только собственные смены."""

    def test_app_derives_hidden_flag_from_department(self):
        source = _read(APP_PATH)

        self.assertIn(
            "import { departmentAllowsView, departmentHidesColleagueSchedules, departmentRestrictsViews, firstAllowedView } from './utils/departmentViews';",
            source,
        )
        self.assertIn(
            "const operatorColleagueShiftsHidden = isOperatorSelfSchedules && departmentHidesColleagueSchedules(user);",
            source,
        )

    def test_tabs_and_swap_buttons_are_hidden(self):
        source = _read(APP_PATH)

        # Переключатель табов (Смены/Замены/Смены коллег) скрыт целиком.
        switcher_start = source.index("setShowSwapCreateModal(false);\n                                                        setSwapCandidatesSearch('');")
        switcher_region = source[switcher_start - 2000:switcher_start]
        self.assertIn("{!operatorColleagueShiftsHidden && (", switcher_region)

        # Обе кнопки «Обменять» обёрнуты в гвард.
        self.assertGreaterEqual(source.count("{!operatorColleagueShiftsHidden && ("), 3)
        button_positions = []
        search_from = 0
        while True:
            pos = source.find("onClick={() => handleOpenSwapModalForOwnShift(dayCard, seg)}", search_from)
            if pos == -1:
                break
            button_positions.append(pos)
            search_from = pos + 1
        self.assertEqual(len(button_positions), 2)
        for pos in button_positions:
            self.assertIn("{!operatorColleagueShiftsHidden && (", source[pos - 400:pos])

    def test_hidden_operator_cannot_stay_on_colleague_tabs(self):
        source = _read(APP_PATH)

        self.assertIn(
            "if (operatorColleagueShiftsHidden && operatorSelfTab !== 'schedule') {",
            source,
        )
        self.assertIn("setOperatorSelfTab('schedule');", source)

    def test_colleague_data_is_not_fetched_when_hidden(self):
        source = _read(APP_PATH)

        self.assertIn(
            "if (!isOperatorSelfSchedules || !user || operatorColleagueShiftsHidden) return;",
            source,
        )
        self.assertIn(
            "if (!isOperatorSelfSchedules || !user || operatorColleagueShiftsHidden || operatorSelfTab !== 'direction') return;",
            source,
        )


class FrontOfficeBackendScopeTests(unittest.TestCase):
    """Бэкенд зеркалит фронтовое скрытие: операторам front_office эндпоинты
    смен направления и обменов сменами возвращают 403."""

    def test_hidden_departments_helper(self):
        source = _read(BOT_PATH)
        self.assertIn("COLLEAGUE_SCHEDULES_HIDDEN_DEPARTMENT_CODES = {'front_office'}", source)

        helper = _function_source(BOT_PATH, "_operator_colleague_schedules_hidden")
        self.assertIn("db.get_user_department(requester_id)", helper)
        self.assertIn("COLLEAGUE_SCHEDULES_HIDDEN_DEPARTMENT_CODES", helper)

    def test_direction_and_swap_endpoints_are_blocked(self):
        for function_name in (
            "get_direction_work_schedules",
            "get_shift_swap_candidates",
            "shift_swap_requests",
            "respond_shift_swap_request",
        ):
            with self.subTest(function=function_name):
                endpoint = _function_source(BOT_PATH, function_name)
                self.assertIn("_operator_colleague_schedules_hidden(requester_id)", endpoint)
                self.assertIn("403", endpoint)

    def test_own_schedule_endpoint_stays_available(self):
        endpoint = _function_source(BOT_PATH, "get_my_work_schedules")
        self.assertNotIn("_operator_colleague_schedules_hidden", endpoint)


if __name__ == "__main__":
    unittest.main()
