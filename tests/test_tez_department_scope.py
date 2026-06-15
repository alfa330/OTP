import ast
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DATABASE_PATH = ROOT / "database.py"
DEPARTMENT_VIEWS_PATH = ROOT / "src" / "utils" / "departmentViews.js"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _function_source(path, function_name, class_name=None):
    source = _read(path)
    module = ast.parse(source)
    body = module.body
    if class_name:
        class_node = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        body = class_node.body
    function_node = next(
        node for node in body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return textwrap.dedent(ast.get_source_segment(source, function_node))


class TezDepartmentFrontendScopeTests(unittest.TestCase):
    def test_tez_allowlist_has_separate_operator_and_manager_views(self):
        source = _read(DEPARTMENT_VIEWS_PATH)

        self.assertIn("tez: {", source)
        self.assertIn("const TEZ_OPERATOR_VIEWS", source)
        self.assertIn("'profile'", source)
        self.assertIn("'evaluation'", source)
        self.assertIn("'hours'", source)
        self.assertIn("'work_schedules'", source)
        self.assertIn("'surveys'", source)
        self.assertIn("'salary'", source)
        self.assertIn("const TEZ_MANAGER_VIEWS", source)
        self.assertIn("'manage_operators'", source)
        self.assertIn("'call_evaluation'", source)
        self.assertIn("'call_division'", source)
        self.assertIn("'monitoring_scale'", source)
        self.assertIn("'sv_hours'", source)
        self.assertIn("'tasks'", source)

    def test_department_head_uses_sv_allowlist_in_restricted_departments(self):
        source = _read(DEPARTMENT_VIEWS_PATH)

        self.assertIn("if (isAdminLikeRole(user?.role)) return null;", source)
        self.assertIn("const role = isDepartmentHead(user) ? 'sv' : normalizeRole(user?.role);", source)
        self.assertNotIn("isAdminLikeRole(user?.role) || isDepartmentHead(user)", source)


class TezDepartmentBackendScopeTests(unittest.TestCase):
    def test_task_and_survey_guards_treat_department_head_as_scoped_manager(self):
        task_guard = _function_source(BOT_PATH, "_task_route_guard")
        surveys_guard = _function_source(BOT_PATH, "_surveys_route_guard")

        self.assertIn("headed_dept_id = _headed_department_id(requester_id)", task_guard)
        self.assertIn("g.effective_task_role", task_guard)
        self.assertIn("g.task_scope_department_id", task_guard)
        self.assertIn("role = 'sv'", surveys_guard)
        self.assertIn("g.survey_scope_department_id", surveys_guard)

    def test_surveys_and_activity_queries_accept_department_scope(self):
        visible_ops = _function_source(
            DATABASE_PATH,
            "_get_visible_operator_ids_for_requester_tx",
            class_name="Database",
        )
        surveys = _function_source(DATABASE_PATH, "get_surveys_for_management", class_name="Database")
        tech = _function_source(DATABASE_PATH, "get_operator_technical_issues", class_name="Database")
        offline = _function_source(DATABASE_PATH, "get_operator_offline_activities", class_name="Database")

        self.assertIn("scope_department_id=None", visible_ops)
        self.assertIn("AND department_id = %s", visible_ops)
        self.assertIn("op.department_id = %s", surveys)
        self.assertIn("scope_department_id=None", tech)
        self.assertIn("op.department_id = %s", tech)
        self.assertIn("scope_department_id=None", offline)
        self.assertIn("op.department_id = %s", offline)


if __name__ == "__main__":
    unittest.main()
