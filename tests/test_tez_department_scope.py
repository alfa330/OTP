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

    def test_technical_issue_selectors_are_scoped_for_department_managers(self):
        source = _read(ROOT / "src" / "components" / "technical" / "TechnicalIssuesView.jsx")

        self.assertIn("const scopeDepartmentId = isDepartmentHead(user)", source)
        self.assertIn("isSupervisorRole(role) ? Number(user?.department_id ?? user?.departmentId) : null", source)
        self.assertIn("Number(op?.department_id ?? op?.departmentId) === scopeDepartmentId", source)
        self.assertIn("directionDepartmentId === scopeDepartmentId", source)

    def test_call_evaluation_department_head_gets_admin_journal_with_department_scope(self):
        source = _read(CALL_EVALUATION_PATH)

        self.assertIn("const isScopedDepartmentHead = isDepartmentHead && canonicalRole !== 'super_admin';", source)
        self.assertIn("const isAdminRole = isBaseAdminRole || isDepartmentHead;", source)
        self.assertIn("const isGlobalAdminRole = isBaseAdminRole && !isScopedDepartmentHead;", source)
        self.assertIn("const canManageFeedbackReportSetting = isGlobalAdminRole || isDepartmentHead;", source)
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

    def test_management_schedule_list_is_filtered_to_requester_scope(self):
        endpoint = _function_source(BOT_PATH, "get_operators_with_schedules")

        self.assertIn("_filter_operators_for_requester_scope(user_data, user_id, operators)", endpoint)

    def test_hours_and_technical_issue_targets_use_department_scope(self):
        daily_hours = _function_source(BOT_PATH, "sv_daily_hours")
        monthly_report = _function_source(BOT_PATH, "get_monthly_report_hours")
        resolver = _function_source(
            DATABASE_PATH,
            "_resolve_technical_issue_operator_ids_tx",
            class_name="Database",
        )

        self.assertIn("is_global_admin = _is_global_admin_requester(role, requester_id)", daily_hours)
        self.assertIn("headed_dept_id is not None and not is_global_admin", daily_hours)
        self.assertIn("department_id=None if _is_global_admin_requester(role, requester_id) else scope_department_id", monthly_report)
        self.assertIn("Forbidden: supervisor is outside your department", monthly_report)
        self.assertIn("SELECT id, department_id", resolver)
        self.assertIn("Forbidden directions for sv", resolver)

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


class DepartmentHeadWriteScopeTests(unittest.TestCase):
    """Глава отдела (админ-роль + headed_department_id) НЕ должна считаться
    глобальным админом на write/assign-эндпоинтах: иначе её действия утекают
    в чужие отделы, а созданные ею операторы уходят в СЗоВ и пропадают из её списка."""

    def test_add_user_forces_headed_department_for_scoped_head(self):
        add_user = _function_source(BOT_PATH, "add_user")
        # Отдел нового сотрудника берётся из клиента только для ГЛОБАЛЬНОГО админа;
        # глава отдела/СВ принудительно получают свой отдел (requester_dept_id).
        self.assertIn("if _is_global_admin_requester(requester_role, requester_id):", add_user)
        self.assertIn("department_id = requester_dept_id", add_user)
        self.assertIn(
            "requester_dept_id = requester_headed_dept if requester_headed_dept is not None else db.get_user_department_id(requester_id)",
            add_user,
        )

    def test_admin_update_user_does_not_short_circuit_scope_for_head(self):
        admin_update_user = _function_source(BOT_PATH, "admin_update_user")
        self.assertIn(
            "if not _is_global_admin_requester(requester_role, requester_id) and not _requester_can_access_target_user(",
            admin_update_user,
        )
        self.assertIn(
            "if field == 'department_id' and not _is_global_admin_requester(requester_role, requester_id):",
            admin_update_user,
        )

    def test_admin_update_user_rate_gate_allows_department_head(self):
        admin_update_user = _function_source(BOT_PATH, "admin_update_user")
        # Глава отдела меняет ставку как админ — в любой день, без sv-ограничений;
        # «чистый» СВ по-прежнему ограничен 1-м числом месяца и своими операторами.
        self.assertIn(
            "if field == 'rate' and not _is_admin_role(requester_role) and requester_role != 'sv' and headed_dept_id is None:",
            admin_update_user,
        )
        self.assertIn(
            "if field == 'rate' and requester_role == 'sv' and headed_dept_id is None:",
            admin_update_user,
        )
        self.assertIn("_is_supervisor_rate_change_day()", admin_update_user)

    def test_department_head_assignment_endpoints_require_global_admin(self):
        set_head = _function_source(BOT_PATH, "api_admin_set_department_head")
        head_history = _function_source(BOT_PATH, "api_admin_department_head_history")
        self.assertIn("if not _is_global_admin_requester(requester_role, requester_id):", set_head)
        self.assertIn("if not _is_global_admin_requester(requester_role, requester_id):", head_history)

    def test_group_write_endpoints_are_department_scoped(self):
        scope_helper = _function_source(BOT_PATH, "_ensure_group_in_requester_scope")
        create_group = _function_source(BOT_PATH, "create_group_endpoint")
        add_operator = _function_source(BOT_PATH, "add_group_operator_endpoint")
        add_supervisor = _function_source(BOT_PATH, "add_group_supervisor_endpoint")
        archive_group = _function_source(BOT_PATH, "archive_group_endpoint")
        rename_group = _function_source(BOT_PATH, "rename_group_endpoint")
        reuse_group = _function_source(BOT_PATH, "reuse_group_endpoint")
        group_members = _function_source(BOT_PATH, "group_members_endpoint")
        recompute = _function_source(BOT_PATH, "recompute_month_snapshot_endpoint")

        self.assertIn("if _is_global_admin_requester(role, requester_id):", scope_helper)
        self.assertIn("grp.get('department_id') != headed_dept", scope_helper)
        self.assertIn(
            "department_id = data.get('department_id') if _is_global_admin_requester(role, requester_id) else headed_dept",
            create_group,
        )
        self.assertIn("_ensure_group_in_requester_scope(group_id, rid, _role)", add_operator)
        self.assertIn("_ensure_group_in_requester_scope(group_id, rid, _role)", add_supervisor)
        self.assertIn("_ensure_group_in_requester_scope(group_id, _rid, _role)", archive_group)
        self.assertIn("_ensure_group_in_requester_scope(group_id, _rid, _role)", rename_group)
        self.assertIn("_ensure_group_in_requester_scope(group_id, _rid, _role)", reuse_group)
        self.assertIn("_ensure_group_in_requester_scope(group_id, _rid, _role)", group_members)
        # Тяжёлый глобальный пересчёт снимков — только глобальный админ (главам запрещено).
        self.assertIn("if not _is_global_admin_requester(role, requester_id):", recompute)

    def test_work_schedule_break_endpoints_scope_department_head(self):
        save_rules = _function_source(BOT_PATH, "save_work_schedule_break_rules")
        recalc = _function_source(BOT_PATH, "recalculate_work_schedule_breaks")
        self.assertIn("if not _is_global_admin_requester(role, requester_id):", save_rules)
        self.assertIn(
            "elif not _is_global_admin_requester(_normalize_user_role(user_data[3]), requester_id):",
            recalc,
        )

    def test_call_division_is_department_scoped(self):
        status = _function_source(BOT_PATH, "call_distribution_status")
        sync_endpoint = _function_source(BOT_PATH, "sync_eval_calls_oktell")
        worker = _function_source(BOT_PATH, "sync_oktell_evaluation_calls")
        settings = _function_source(BOT_PATH, "call_distribution_settings_endpoint")

        self.assertIn(
            "scope_dept = None if _is_global_admin_requester(role, requester_id) else _department_scope_id_for_requester(requester_id)",
            status,
        )
        self.assertIn("if scope_member_ids is not None and op_id not in scope_member_ids:", status)
        self.assertIn("department_id=scope_dept", sync_endpoint)
        self.assertIn("def sync_oktell_evaluation_calls(", worker)
        self.assertIn("department_id=None", worker)
        self.assertIn("allowed_operator_ids", worker)
        self.assertIn("db.get_department_member_ids(department_id)", worker)
        self.assertIn("pool_counts = db.get_imported_calls_status_counts_by_operator(mstr)", worker)
        self.assertIn("journal = db.get_operator_score_aggregates_for_month(mstr, matched_op_ids) or {}", worker)
        self.assertIn("covered = evaluated_real + pending", worker)
        self.assertIn("need = max(0, target - covered)", worker)
        self.assertIn("can_edit = _is_global_admin_requester(role, requester_id)", settings)


class SupervisorOperatorListCompletenessTests(unittest.TestCase):
    """СВ должен видеть своих подопечных операторов с ПОЛНЫМИ данными в /api/admin/users,
    иначе фронт берёт тонкую проекцию svData.operators (без HR-полей) и карточка
    оператора открывается «пустой» при обновлении (напр. аватара)."""

    def test_get_admin_users_includes_supervised_operators_for_sv(self):
        users = _function_source(BOT_PATH, "get_admin_users")
        self.assertIn("if requester_role == 'sv' and headed_dept_id is None:", users)
        self.assertIn("_supervised_by_requester", users)
        self.assertIn(
            "u.get('department_id') == scope_dept or _supervised_by_requester(u)",
            users,
        )

    def test_manage_operators_edit_seeds_modal_from_full_admin_users(self):
        source = _read(APP_PATH)
        self.assertIn(
            "const fullOp = (Array.isArray(adminUsers) ? adminUsers : []).find((cand) => Number(cand?.id) === Number(op?.id)) || op;",
            source,
        )
        self.assertIn(
            "setUserToEdit({ ...fullOp, supervisor_id: fullOp?.supervisor_id ?? user?.id });",
            source,
        )


if __name__ == "__main__":
    unittest.main()
