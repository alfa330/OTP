import ast
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DATABASE_PATH = ROOT / "database.py"
DEPARTMENT_VIEWS_PATH = ROOT / "src" / "utils" / "departmentViews.js"
APP_PATH = ROOT / "src" / "App.jsx"
CALL_EVALUATION_PATH = ROOT / "src" / "call_evaluation" / "main.jsx"


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
        self.assertIn("const TEZ_SUPERVISOR_VIEWS = TEZ_MANAGER_VIEWS.filter((view) => view !== 'monitoring_scale');", source)
        self.assertIn("'manage_operators'", source)
        self.assertIn("'qr_access'", source)
        self.assertIn("'call_evaluation'", source)
        self.assertIn("'call_division'", source)
        self.assertIn("'monitoring_scale'", source)
        self.assertIn("'sv_hours'", source)
        self.assertIn("'tasks'", source)

    def test_department_head_uses_sv_allowlist_in_restricted_departments(self):
        source = _read(DEPARTMENT_VIEWS_PATH)

        self.assertIn("if (normalizeRole(user?.role) === 'super_admin') return null;", source)
        self.assertIn("if (isAdminLikeRole(user?.role) && !isDepartmentHead(user)) return null;", source)
        self.assertIn("const role = isDepartmentHead(user) ? 'head' : normalizeRole(user?.role);", source)
        self.assertIn("head: TEZ_MANAGER_VIEWS", source)
        self.assertIn("sv: TEZ_SUPERVISOR_VIEWS", source)
        self.assertNotIn("isAdminLikeRole(user?.role) || isDepartmentHead(user)", source)
        self.assertIn("const VIEW_ALIASES", source)
        self.assertIn("sv_list: 'manage_operators'", source)
        self.assertIn("manage_users: 'manage_operators'", source)
        self.assertIn("const alias = VIEW_ALIASES[viewKey];", source)
        self.assertIn("alias && isDepartmentHead(user) && allow.includes(alias)", source)

    def test_sales_department_manager_allowlist_keeps_monitoring_scale(self):
        source = _read(DEPARTMENT_VIEWS_PATH)

        self.assertIn("head: SALES_HEAD_VIEWS", source)
        self.assertIn("sv: SALES_SUPERVISOR_VIEWS", source)
        self.assertIn("'monitoring_scale'", source[source.index("const SALES_HEAD_VIEWS"):source.index("const VIEW_ALIASES")])
        self.assertNotIn("'monitoring_scale'", source[source.index("const SALES_SUPERVISOR_VIEWS"):source.index("const SALES_HEAD_VIEWS")])

    def test_monitoring_scale_is_hidden_from_regular_supervisors(self):
        source = _read(APP_PATH)

        self.assertIn("isDepartmentHeadUser && departmentAllowsView(user, 'monitoring_scale')", source)
        self.assertIn('view === "monitoring_scale" && !isAdminLikeRole && isDepartmentManager && isDepartmentHeadUser', source)
        self.assertIn("view === 'monitoring_scale' && !isAdminLikeRole && !isDepartmentHeadUser", source)

    def test_department_manager_employee_accounting_keeps_supervisors(self):
        source = _read(APP_PATH)

        self.assertIn("const manageOperatorRoles = isDepartmentManager", source)
        self.assertIn("new Set(['operator', 'trainee', 'sv', 'supervisor'])", source)
        self.assertIn("manageOperatorRoles.has(normalizeRole(u?.role))", source)
        self.assertIn("const canUseAdminEmployeeAccounting = isAdminLikeRole || (isDepartmentHeadUser && departmentAllowsView(user, 'manage_operators'));", source)
        self.assertIn("const isDepartmentHeadAdminEmployeeView = canUseAdminEmployeeAccounting && isDepartmentHeadUser && ['sv_list', 'manage_users'].includes(view);", source)
        self.assertIn("handleSidebarViewNavigation(e, 'sv_list'", source)
        self.assertIn("handleSidebarViewNavigation(e, 'manage_users'", source)
        self.assertIn("setView('manage_users')", source)
        self.assertNotIn("manageOperatorsRoleView", source)
        self.assertIn("const operatorUsers = useMemo(() => (", source)
        self.assertIn("['operator', 'trainee'].includes(normalizeRole(employee?.role))", source)
        self.assertIn("buildUpcomingBirthdays(operatorUsers", source)
        self.assertIn("const filteredUsers = operatorUsers.filter", source)
        self.assertIn("const allUsers = (Array.isArray(operatorUsers) ? operatorUsers : [])", source)
        self.assertIn("const filteredByStatus = (operatorUsers || [])", source)

    def test_hours_accounting_groups_are_scoped_for_department_head(self):
        source = _read(APP_PATH)

        self.assertIn("const hoursDepartmentScopeId = isHoursDepartmentHead ? headedDepartmentId(user) : null;", source)
        self.assertIn("group?.department_id ?? group?.departmentId", source)
        self.assertIn("Number(hoursDepartmentScopeId)", source)

    def test_call_evaluation_department_head_gets_admin_journal_with_department_scope(self):
        source = _read(CALL_EVALUATION_PATH)

        self.assertIn("const isScopedDepartmentHead = isDepartmentHead && canonicalRole !== 'super_admin';", source)
        self.assertIn("const isAdminRole = isBaseAdminRole || isDepartmentHead;", source)
        self.assertIn("const isGlobalAdminRole = isBaseAdminRole && !isScopedDepartmentHead;", source)
        self.assertIn("const canManageFeedbackReportSetting = isGlobalAdminRole;", source)
        self.assertIn("isAdminRole && (!isScopedDepartmentHead || selectedSupervisor)", source)
        self.assertIn("const scopeId = shouldUseSelectedSupervisor ? selectedSupervisor : userId;", source)


class TezDepartmentBackendScopeTests(unittest.TestCase):
    def test_task_and_survey_guards_treat_department_head_as_scoped_manager(self):
        task_guard = _function_source(BOT_PATH, "_task_route_guard")
        surveys_guard = _function_source(BOT_PATH, "_surveys_route_guard")
        effective_role = _function_source(BOT_PATH, "_effective_scoped_manager_role")

        self.assertIn("headed_dept_id = _headed_department_id(requester_id)", task_guard)
        self.assertIn("g.effective_task_role", task_guard)
        self.assertIn("g.task_scope_department_id", task_guard)
        self.assertIn("_is_global_admin_requester(g.effective_task_role, requester_id)", task_guard)
        self.assertIn("not _is_super_admin_role(role_norm)", effective_role)
        self.assertIn("role = 'sv'", surveys_guard)
        self.assertIn("g.survey_scope_department_id", surveys_guard)
        self.assertIn("not _is_super_admin_role(role)", surveys_guard)

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

    def test_operator_direction_schedule_is_scoped_to_own_department(self):
        endpoint = _function_source(BOT_PATH, "get_direction_work_schedules")
        schedule_query = _function_source(DATABASE_PATH, "get_operators_with_shifts", class_name="Database")

        self.assertIn("requester_department_id = db.get_user_department_id(requester_id)", endpoint)
        self.assertIn("department_id=requester_department_id", endpoint)
        self.assertIn("department_id=None", schedule_query)
        self.assertIn("department_filter_id = int(department_id) if department_id is not None else None", schedule_query)
        self.assertGreaterEqual(schedule_query.count("u.department_id = %s"), 2)

    def test_department_head_admin_role_is_not_treated_as_global_admin_for_lists(self):
        departments = _function_source(BOT_PATH, "api_admin_departments")
        users = _function_source(BOT_PATH, "get_admin_users")
        sv_list = _function_source(BOT_PATH, "get_sv_list")
        directions = _function_source(BOT_PATH, "get_directions")
        sv_data = _function_source(BOT_PATH, "get_sv_data")
        groups = _function_source(BOT_PATH, "list_groups_endpoint")

        self.assertIn("headed_dept_id = _headed_department_id(requester_id)", departments)
        self.assertIn("db.get_department_by_id(headed_dept_id)", departments)
        self.assertIn("visible_roles.extend(['sv', 'supervisor'])", users)
        self.assertIn("_is_global_admin_requester(requester_role, requester_id)", sv_list)
        self.assertIn("_is_global_admin_requester(role, requester_id)", directions)
        self.assertIn("_is_global_admin_requester(requester_role, requester_id)", sv_data)
        self.assertIn("headed_dept_id = _headed_department_id(requester_id)", groups)
        self.assertIn("department_id=headed_dept_id", groups)
        self.assertIn("_is_global_admin_requester(role, requester_id)", groups)

    def test_call_evaluation_admin_actions_keep_department_scope(self):
        create_request = _function_source(BOT_PATH, "_create_reevaluation_request")
        resolve_request = _function_source(BOT_PATH, "_resolve_reevaluation_request")
        requests = _function_source(BOT_PATH, "get_call_reevaluation_requests")
        delete_draft = _function_source(BOT_PATH, "delete_draft_evaluation")
        receive_eval = _function_source(BOT_PATH, "receive_call_evaluation")

        self.assertIn("_headed_department_id(requester_id) is not None", create_request)
        self.assertIn("_ensure_call_access_for_requester(call_ctx.get('operator_id'), requester, requester_id)", create_request)
        self.assertIn("headed_dept_id = _headed_department_id(approver_user_id)", resolve_request)
        self.assertIn("_ensure_call_access_for_requester(call_ctx.get('operator_id'), approver, approver_user_id)", resolve_request)
        self.assertIn("is_global_admin = _is_global_admin_requester(requester_role, requester_id)", requests)
        self.assertIn("department_id = int(headed_dept_id)", requests)
        self.assertIn("_ensure_call_access_for_requester(operator_id, requester, requester_id)", delete_draft)
        self.assertIn("requester_headed_dept = _headed_department_id(requester_id)", receive_eval)
        self.assertIn("_ensure_call_access_for_requester(operator[0], requester, requester_id)", receive_eval)
        self.assertIn("_ensure_call_access_for_requester(previous_call.get(\"operator_id\"), requester, requester_id)", receive_eval)


if __name__ == "__main__":
    unittest.main()
