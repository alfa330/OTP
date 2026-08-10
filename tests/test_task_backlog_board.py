"""Бэклог + канбан + таймлайн раздела «Задачи».

Проверяем контракт без БД: миграции/поля в database.py, роуты в bot_schedule2.py,
разрешение drag&drop и раскладку колонок во фронте, а также разбор длительностей в CLI.
"""
import importlib.util
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
APP_PATH = ROOT / "bot_schedule2.py"
WORKSPACE_PATH = ROOT / "src" / "components" / "tasks" / "TaskBoardWorkspace.jsx"
TASKS_VIEW_PATH = ROOT / "src" / "components" / "tasks" / "TasksView.jsx"
CUSTOM_SELECT_PATH = ROOT / "src" / "components" / "ui" / "CustomSelect.jsx"
APP_JSX_PATH = ROOT / "src" / "App.jsx"
BOARD_QUERY_PATH = ROOT / "src" / "components" / "tasks" / "boardQuery.js"
CLI_PATH = ROOT / "scripts" / "task_board.py"
SKILL_PATH = ROOT / ".claude" / "skills" / "task-board" / "SKILL.md"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("task_board_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TaskSchemaTests(unittest.TestCase):
    """Колонки бэклога/таймлайна добавляются идемпотентными ALTER'ами."""

    def setUp(self):
        self.src = _read(DATABASE_PATH)

    def test_board_columns_added(self):
        self.assertIn("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS is_backlog BOOLEAN NOT NULL DEFAULT FALSE;", self.src)
        self.assertIn("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS backlog_rank DOUBLE PRECISION;", self.src)
        self.assertIn("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS planned_start_at TIMESTAMP;", self.src)
        self.assertIn("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS started_at TIMESTAMP;", self.src)
        self.assertIn("ADD COLUMN IF NOT EXISTS estimate_minutes INTEGER", self.src)

    def test_backlog_index_and_started_at_backfill(self):
        self.assertIn("idx_tasks_backlog_rank", self.src)
        # Фактический старт восстанавливается из истории статусов и только для пустых значений.
        backfill_start = self.src.index("SET started_at = h.first_started")
        backfill = self.src[backfill_start:backfill_start + 400]
        self.assertIn("WHERE status_code = 'in_progress'", backfill)
        self.assertIn("t.started_at IS NULL", backfill)

    def test_no_new_task_status_values(self):
        # Бэклог — флаг поверх 'assigned', а не новый статус: CHECK не расширяем.
        self.assertIn(
            "status VARCHAR(32) NOT NULL DEFAULT 'assigned' CHECK (status IN "
            "('assigned', 'in_progress', 'completed', 'accepted', 'returned'))",
            self.src,
        )
        self.assertNotIn("'backlog',", self.src.split("CREATE TABLE IF NOT EXISTS tasks (")[1][:900])


class TaskDbLayerTests(unittest.TestCase):
    def setUp(self):
        self.src = _read(DATABASE_PATH)

    def test_create_task_accepts_board_fields(self):
        signature_start = self.src.index("    def create_task(")
        signature = self.src[signature_start:self.src.index("):", signature_start)]
        for field in ("is_backlog=False", "estimate_minutes=None", "planned_start_at=None", "due_at=None"):
            self.assertIn(field, signature)

    def test_new_backlog_item_goes_to_queue_tail(self):
        self.assertIn("SELECT COALESCE(MAX(backlog_rank), 0) + 1 FROM tasks WHERE is_backlog = TRUE", self.src)

    def test_edit_task_supports_absolute_due_at(self):
        signature_start = self.src.index("    def edit_task(")
        signature = self.src[signature_start:self.src.index("):", signature_start)]
        for field in ("due_at=_UNSET", "estimate_minutes=_UNSET", "planned_start_at=_UNSET"):
            self.assertIn(field, signature)

    def test_board_state_method_permissions(self):
        start = self.src.index("    def update_task_board_state(")
        block = self.src[start:self.src.index("    def delete_task(", start)]
        # Двигать карточку и оценивать может исполнитель, сроки — только постановщик/админ.
        self.assertIn("if not (is_creator or is_assignee or is_admin):", block)
        self.assertIn("raise PermissionError(\"ONLY_CREATOR_CAN_PLAN\")", block)
        self.assertIn("raise ValueError(\"BACKLOG_ONLY_FOR_ASSIGNED\")", block)
        self.assertIn("\"was_backlog\": current_is_backlog", block)

    def test_in_progress_records_start_and_clears_backlog(self):
        start = self.src.index("elif target_status == 'in_progress':")
        block = self.src[start:start + 700]
        self.assertIn("is_backlog = FALSE", block)
        self.assertIn("started_at = COALESCE(started_at,", block)

    def test_get_tasks_exposes_board_fields_and_filter(self):
        self.assertIn("if backlog_norm and backlog_norm not in {'only', 'exclude'}:", self.src)
        self.assertIn("INVALID_TASK_BACKLOG_FILTER", self.src)
        self.assertIn("t.is_backlog = TRUE", self.src)
        self.assertIn("t.is_backlog = FALSE", self.src)
        self.assertIn('"is_backlog": bool(row[31]),', self.src)
        self.assertIn('"backlog_rank": float(row[32]) if row[32] is not None else None,', self.src)
        self.assertIn('"estimate_minutes": row[33],', self.src)
        self.assertIn('"started_at": self._task_dt_to_iso(row[35]),', self.src)

    def test_backlog_sorted_by_rank(self):
        self.assertIn('"t.backlog_rank ASC NULLS LAST, t.created_at DESC, t.id DESC"', self.src)

    def test_global_admin_task_visibility_is_not_limited_to_own_tasks(self):
        start = self.src.index("    def get_tasks_for_requester(")
        block = self.src[start:self.src.index("    def update_task_status(", start)]
        self.assertIn("if role_has_min(role, 'admin'):\n            pass", block)
        self.assertIn("if only_my_flag:", block)

    def test_regulation_templates_skip_backlog(self):
        start = self.src.index("    def materialize_due_regulation_tasks(")
        block = self.src[start:start + 1500]
        self.assertIn("AND t.is_backlog = FALSE", block)


class TaskApiTests(unittest.TestCase):
    def setUp(self):
        self.src = _read(APP_PATH)

    def test_board_endpoint_registered(self):
        self.assertIn("@app.route('/api/tasks/board', methods=['POST', 'OPTIONS'])", self.src)
        self.assertIn("def handle_tasks_board():", self.src)

    def test_board_endpoint_is_batch_and_capped(self):
        start = self.src.index("def handle_tasks_board():")
        block = self.src[start:self.src.index("@app.route('/api/tasks/<int:task_id>/status'", start)]
        self.assertIn("raw_items = data.get('items')", block)
        self.assertIn("Too many items (max 200)", block)
        self.assertIn("db.update_task_board_state(**kwargs)", block)
        # Вынос из бэклога = момент, когда исполнителя наконец уведомляют.
        self.assertIn("result.get('was_backlog') and not result.get('is_backlog')", block)
        self.assertIn("event='promoted'", block)

    def test_backlog_creation_does_not_notify_assignee(self):
        self.assertIn("if assignee_chat_id and not is_backlog:", self.src)

    def test_get_accepts_backlog_filter(self):
        self.assertIn("backlog_filter = (request.args.get('backlog') or '').strip().lower() or None", self.src)
        self.assertIn("backlog=backlog_filter", self.src)

    def test_patch_accepts_absolute_deadline_and_estimate(self):
        self.assertIn("has_due_at = 'due_at' in data", self.src)
        self.assertIn('edit_kwargs["due_at"] = data.get(\'due_at\')', self.src)
        self.assertIn('edit_kwargs["estimate_minutes"] = estimate_minutes', self.src)
        self.assertIn('edit_kwargs["planned_start_at"] = data.get(\'planned_start_at\')', self.src)

    def test_estimate_parser_supports_both_shapes(self):
        start = self.src.index("def _parse_task_estimate_minutes(source):")
        block = self.src[start:start + 900]
        self.assertIn("direct = source.get('estimate_minutes')", block)
        self.assertIn("source.get('estimate_hours')", block)


class WorkspaceFrontendTests(unittest.TestCase):
    def setUp(self):
        self.src = _read(WORKSPACE_PATH)

    def test_five_board_columns(self):
        start = self.src.index("export const BOARD_COLUMNS = [")
        block = self.src[start:self.src.index("];", start)]
        ids = re.findall(r"id: '([a-z]+)'", block)
        self.assertEqual(ids, ["backlog", "todo", "progress", "review", "done"])

    def test_column_mapping_matches_status_model(self):
        start = self.src.index("export const columnOfTask = (task) => {")
        block = self.src[start:self.src.index("};", start)]
        self.assertIn("if (task?.is_backlog) return 'backlog';", block)
        self.assertIn("case 'returned':", block)
        self.assertIn("return 'progress';", block)
        self.assertIn("case 'completed':", block)
        self.assertIn("return 'review';", block)
        self.assertIn("case 'accepted':", block)
        self.assertIn("return 'done';", block)

    def test_drop_resolution_respects_permissions(self):
        start = self.src.index("export const resolveBoardDrop =")
        block = self.src[start:self.src.index("export const rankForPosition", start)]
        # Взять в работу — только исполнитель; принять/вернуть — проверяющий.
        self.assertIn("Взять задачу в работу может только исполнитель", block)
        self.assertIn("Отметить выполненной может только исполнитель", block)
        self.assertIn("action: 'accepted'", block)
        self.assertIn("action: 'returned'", block)
        self.assertIn("action: 'reopened'", block)
        self.assertIn("В бэклог можно вернуть только не начатую задачу", block)

    def test_wip_limit_present(self):
        self.assertIn("wipExceeded", self.src)
        self.assertIn("Превышен лимит одновременной работы", self.src)

    def test_timeline_reports_plan_and_fact(self):
        start = self.src.index("export const timelineSpanOf =")
        block = self.src[start:self.src.index("const TimelineView", start)]
        self.assertIn("plannedStart", block)
        self.assertIn("actualStart", block)
        self.assertIn("actualMs", block)
        self.assertIn("leadMs", block)
        # Медианы, а не средние: одна забытая задача не должна ломать метрику.
        self.assertIn("const median = (values)", self.src)
        self.assertIn("Медианный цикл", self.src)

    def test_normal_priority_has_no_dot(self):
        start = self.src.index("const PRIORITY_DOT = {")
        block = self.src[start:self.src.index("};", start)]
        self.assertIn("critical:", block)
        self.assertIn("urgent:", block)
        self.assertNotIn("normal:", block)

    def test_admin_can_open_a_named_employee_board(self):
        self.assertIn("import CustomSelect from '../ui/CustomSelect';", self.src)
        self.assertIn("people = [],", self.src)
        self.assertIn("{isAdmin ? (", self.src)
        self.assertIn("value: `person:${person.id}`", self.src)
        # Выборку доски делает сервер, клиент её не фильтрует.
        self.assertIn("params.person_scope = 'any';", _read(BOARD_QUERY_PATH))
        self.assertIn('ariaLabel="Выбор доски сотрудника"', self.src)

    def test_board_sort_defaults_to_freshness_and_is_stable(self):
        self.assertIn("const [boardSort, setBoardSort] = useState('freshness');", self.src)
        start = self.src.index("export const compareBoardTasks =")
        block = self.src[start:self.src.index("const startOfDay", start)]
        self.assertIn("sortMode === 'importance'", block)
        self.assertIn("critical: 0, urgent: 1, normal: 2", self.src)
        self.assertIn("rightCreatedAt - leftCreatedAt", block)
        self.assertIn("Number(right?.id || 0) - Number(left?.id || 0)", block)

    def test_every_kanban_column_uses_selected_sort(self):
        start = self.src.index("const tasksByColumn = useMemo(() => {")
        block = self.src[start:self.src.index("}, [scopedTasks, dropContext, boardSort]);", start)]
        self.assertIn("compareBoardTasks(left.task, right.task, boardSort)", block)
        self.assertIn("BOARD_COLUMNS.forEach((column) => buckets[column.id].sort(compareEntries));", block)

    def test_manual_backlog_order_remains_separate(self):
        start = self.src.index("const backlogTasks = useMemo(")
        # Границу берём по следующему объявлению, а не по тексту комментария рядом:
        # комментарий уже один раз протух и увёл тест за собой.
        block = self.src[start:self.src.index("const tasksByColumn = useMemo(", start)]
        self.assertIn("a?.backlog_rank", block)
        self.assertIn("return aRank - bRank;", block)


class TasksViewIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.src = _read(TASKS_VIEW_PATH)

    def test_workspace_tabs_registered(self):
        start = self.src.index("const WORKSPACE_TABS = [")
        block = self.src[start:self.src.index("];", start)]
        ids = re.findall(r"id: '([a-z]+)'", block)
        self.assertEqual(ids, ["overview", "backlog", "board", "timeline"])

    def test_workspace_is_mounted_with_handlers(self):
        self.assertIn("import TaskBoardWorkspace from './TaskBoardWorkspace';", self.src)
        self.assertIn("import { boardQueryParams } from './boardQuery';", self.src)
        self.assertIn("<TaskBoardWorkspace", self.src)
        for prop in (
            "onBoardUpdate={updateBoardItems}",
            "onStatusAction={handleBoardStatusAction}",
            "onCreateBacklogItem={openBacklogCreate}",
            "recipients={recipients}",
            "loadTasks={loadBoardTasks}",
        ):
            self.assertIn(prop, self.src)

    def test_board_updates_go_to_board_endpoint(self):
        self.assertIn("`${apiBaseUrl}/api/tasks/board`", self.src)

    def test_status_actions_with_comment_open_modals(self):
        start = self.src.index("const handleBoardStatusAction = useCallback(")
        block = self.src[start:start + 600]
        self.assertIn("openCompleteModal(task)", block)
        self.assertIn("openStatusModal(task, action)", block)

    def test_create_form_carries_backlog_and_estimate(self):
        self.assertIn("isBacklog: false,", self.src)
        self.assertIn("estimateMinutes: '',", self.src)
        self.assertIn("body.append('is_backlog', values.isBacklog ? '1' : '0');", self.src)
        self.assertIn("estimate_minutes: numberFieldValue(values.estimateMinutes),", self.src)

    def test_workspace_uses_site_font_not_section_font(self):
        self.assertIn(".tv-root .tb-scope, .tv-root .tb-scope *", self.src)

    def test_board_controls_use_shared_custom_ios_select(self):
        select_src = _read(CUSTOM_SELECT_PATH)
        self.assertIn("variant = 'default'", select_src)
        self.assertIn("const isIos = variant === 'ios';", select_src)
        self.assertIn("fontFamily: isIos ? APPLE_FONT : undefined", select_src)
        self.assertIn("const moveActive = (direction) =>", select_src)
        self.assertIn("event.key === 'ArrowDown' || event.key === 'ArrowUp'", select_src)
        self.assertIn("aria-activedescendant", select_src)
        self.assertIn("requestAnimationFrame(() => btnRef.current?.focus())", select_src)
        self.assertIn('variant="ios"', _read(WORKSPACE_PATH))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_cli_module()

    def test_duration_parsing(self):
        parse = self.module.parse_duration_to_minutes
        self.assertEqual(parse("90"), 90)
        self.assertEqual(parse("90m"), 90)
        self.assertEqual(parse("4h"), 240)
        self.assertEqual(parse("3d4h"), 3 * 1440 + 240)
        self.assertEqual(parse("2ч30м"), 150)
        self.assertIsNone(parse(""))
        with self.assertRaises(SystemExit):
            parse("скоро")

    def test_deadline_form_split_respects_api_bounds(self):
        payload = self.module.split_minutes_for_form(3 * 1440 + 4 * 60 + 30)
        self.assertEqual(payload["deadline_days"], "3")
        self.assertEqual(payload["deadline_hours"], "4")
        self.assertEqual(payload["deadline_minutes"], "30")
        self.assertLessEqual(int(payload["deadline_hours"]), 23)
        self.assertLessEqual(int(payload["deadline_minutes"]), 59)

    def test_date_only_deadline_lands_at_end_of_day(self):
        self.assertTrue(self.module.parse_due_argument("2026-08-05").endswith("T18:00:00"))
        self.assertEqual(self.module.parse_due_argument("2026-08-05 14:30"), "2026-08-05T14:30:00")
        with self.assertRaises(SystemExit):
            self.module.parse_due_argument("послезавтра")

    def test_column_mapping_matches_frontend(self):
        column_of = self.module.column_of
        self.assertEqual(column_of({"is_backlog": True, "status": "assigned"}), "backlog")
        self.assertEqual(column_of({"status": "assigned"}), "todo")
        self.assertEqual(column_of({"status": "returned"}), "progress")
        self.assertEqual(column_of({"status": "in_progress"}), "progress")
        self.assertEqual(column_of({"status": "completed"}), "review")
        self.assertEqual(column_of({"status": "accepted"}), "done")

    def test_bearer_only_auth_documented_in_code(self):
        # Cookies после логина обязательно сбрасываем — иначе прод даёт 403 Invalid request origin.
        self.assertIn("self.session.cookies.clear()", _read(CLI_PATH))


class TaskReportSchemaTests(unittest.TestCase):
    """Журнал отчётов о проделанной работе."""

    def setUp(self):
        self.src = _read(DATABASE_PATH)

    def test_reports_table(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS task_reports (", self.src)
        start = self.src.index("CREATE TABLE IF NOT EXISTS task_reports (")
        block = self.src[start:self.src.index('"""', start)]
        self.assertIn("task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE", block)
        self.assertIn("CHECK (kind IN ('progress', 'completion'))", block)
        self.assertIn("body TEXT NOT NULL", block)
        self.assertIn("spent_minutes INTEGER CHECK (spent_minutes IS NULL OR spent_minutes >= 0)", block)
        self.assertIn("idx_task_reports_task", self.src)

    def test_legacy_summaries_are_backfilled_once(self):
        start = self.src.index("INSERT INTO task_reports (task_id, author_id, kind, body, created_at, updated_at)")
        block = self.src[start:start + 900]
        self.assertIn("'completion'", block)
        # Идемпотентность: второй прогон не должен плодить дубли.
        self.assertIn("NOT EXISTS", block)
        self.assertIn("r.task_id = t.id AND r.kind = 'completion'", block)


class TaskReportDbTests(unittest.TestCase):
    def setUp(self):
        self.src = _read(DATABASE_PATH)

    def test_create_report_permissions(self):
        start = self.src.index("    def create_task_report(")
        block = self.src[start:self.src.index("    def update_task_report(", start)]
        self.assertIn('raise ValueError("REPORT_BODY_REQUIRED")', block)
        self.assertIn('raise PermissionError("ONLY_TASK_PARTICIPANT")', block)
        # Итоговый отчёт держит tasks.completion_summary в синхроне (Telegram/виджеты/выгрузки).
        self.assertIn("if kind_norm == 'completion':", block)
        self.assertIn("SET completion_summary = %s", block)

    def test_only_author_can_edit_report(self):
        start = self.src.index("    def update_task_report(")
        block = self.src[start:self.src.index("    def delete_task_report(", start)]
        self.assertIn('raise PermissionError("ONLY_REPORT_AUTHOR")', block)
        self.assertNotIn("role_has_min(role, 'admin')", block)

    def test_admin_may_delete_but_summary_falls_back(self):
        start = self.src.index("    def delete_task_report(")
        block = self.src[start:self.src.index("    def update_task_board_state(", start)]
        self.assertIn("if not (is_author or role_has_min(role, 'admin')):", block)
        self.assertIn("ORDER BY created_at DESC, id DESC", block)

    def test_completion_creates_report_with_spent(self):
        start = self.src.index("            completion_report_id = None")
        block = self.src[start:start + 700]
        self.assertIn("action_norm == 'completed' and (completion_summary_norm or spent_minutes_norm)", block)
        self.assertIn("'completion'", block)
        self.assertIn("spent_minutes_norm", block)

    def test_tasks_payload_carries_reports_and_spent_total(self):
        self.assertIn('"reports": task_reports,', self.src)
        self.assertIn('"spent_minutes": spent_total or None', self.src)
        self.assertIn("spent_total = sum(int(item.get('spent_minutes') or 0) for item in task_reports)", self.src)


class TaskReportApiTests(unittest.TestCase):
    def setUp(self):
        self.src = _read(APP_PATH)

    def test_report_routes_registered(self):
        self.assertIn("@app.route('/api/tasks/<int:task_id>/reports', methods=['GET', 'POST', 'OPTIONS'])", self.src)
        self.assertIn("@app.route('/api/tasks/reports/<int:report_id>', methods=['PATCH', 'DELETE', 'OPTIONS'])", self.src)

    def test_report_errors_mapped_to_status_codes(self):
        start = self.src.index("TASK_REPORT_ERRORS = {")
        block = self.src[start:self.src.index("}", start)]
        self.assertIn("'ONLY_REPORT_AUTHOR'", block)
        self.assertIn("'ONLY_TASK_PARTICIPANT'", block)
        self.assertIn("'REPORT_BODY_REQUIRED'", block)

    def test_status_route_accepts_report_alias_and_spent(self):
        start = self.src.index("def update_task_status(task_id):")
        block = self.src[start:start + 4000]
        self.assertIn("source.get('completion_summary') or source.get('report')", block)
        self.assertIn("spent_minutes = _parse_task_spent_minutes(source)", block)
        self.assertIn("spent_minutes=spent_minutes if action == 'completed' else None", block)

    def test_report_notification_exists(self):
        self.assertIn("def _build_task_report_notification_html(", self.src)
        self.assertIn("📄 Итоговый отчёт по задаче", self.src)
        self.assertIn("📝 Отчёт о проделанной работе", self.src)

    def test_completion_notification_reports_spent(self):
        start = self.src.index("    if action_norm == 'completed':\n        lines.append(f\"<b>Файлов:</b>")
        block = self.src[start:start + 700]
        self.assertIn("Затрачено", block)
        self.assertIn("Отчёт о проделанной работе", block)


class TaskReportFrontendTests(unittest.TestCase):
    def setUp(self):
        self.src = _read(TASKS_VIEW_PATH)

    def test_reports_block_rendered_in_drawer(self):
        self.assertIn("const TaskReportsBlock = ({", self.src)
        self.assertIn("<TaskReportsBlock", self.src)
        self.assertIn("Отчёты о работе", self.src)

    def test_report_handlers_hit_report_endpoints(self):
        self.assertIn("`${apiBaseUrl}/api/tasks/${taskId}/reports`", self.src)
        self.assertIn("`${apiBaseUrl}/api/tasks/reports/${reportId}`", self.src)

    def test_only_author_sees_edit_controls(self):
        start = self.src.index("const isAuthor = Number(report?.author_id || 0) === currentUserId;")
        block = self.src[start:start + 1200]
        self.assertIn("{isAuthor && !isEditing && (", block)

    def test_completion_requires_report_text(self):
        start = self.src.index("const submitComplete = useCallback(")
        block = self.src[start:start + 700]
        self.assertIn("if (!completionSummary.trim())", block)
        self.assertIn("body.append('spent_minutes'", block)

    def test_completion_modal_asks_for_report_and_spent(self):
        self.assertIn("Отчёт о проделанной работе *", self.src)
        self.assertIn("Затрачено времени", self.src)

    def test_no_duplicate_summary_block(self):
        # Итоги показываем один раз — через журнал; отдельного текстового блока быть не должно.
        self.assertNotIn('<p className="tv-block-label">Итоги выполнения</p>', self.src)
        self.assertIn('<p className="tv-block-label">Файлы результата</p>', self.src)

    def test_pinned_widget_also_asks_for_report(self):
        # Иначе сдача из виджета молча обходила бы журнал отчётов.
        start = self.src.index("if (btn.action === 'completed') {\n                        setCompleteDraft")
        block = self.src[start:start + 3200]
        self.assertIn("setCompleteDraft({ body: '', spent: '' })", block)
        self.assertIn("Отчёт о проделанной работе", block)
        self.assertIn("report: completeDraft.body.trim()", block)
        self.assertIn("spentMinutes: parseSpentInput(completeDraft.spent)", block)

    def test_spent_input_parser(self):
        start = self.src.index("const parseSpentInput = (raw) => {")
        block = self.src[start:self.src.index("const formatSpentMinutes", start)]
        self.assertIn("d: 1440, д: 1440, h: 60, ч: 60, m: 1, м: 1", block)


class PinnedWidgetReportWiringTests(unittest.TestCase):
    def test_run_action_forwards_report_and_spent(self):
        src = _read(APP_JSX_PATH)
        start = src.index("const runPinnedTaskAction = useCallback(async (task, action, options = {}) => {")
        block = src[start:src.index("}, [user?.id, withAccessTokenHeader, showToast]);", start)]
        self.assertIn("if (options?.report) payload.report = String(options.report).trim();", block)
        self.assertIn("if (options?.spentMinutes) payload.spent_minutes = Number(options.spentMinutes);", block)
        # Виджет ждёт результат, чтобы не закрывать форму отчёта при ошибке.
        self.assertIn("return true;", block)
        self.assertIn("return false;", block)


class EffortChipTests(unittest.TestCase):
    def setUp(self):
        self.src = _read(WORKSPACE_PATH)

    def test_effort_chip_compares_fact_to_estimate(self):
        start = self.src.index("export const effortChipOf = (task) => {")
        block = self.src[start:self.src.index("const checklistProgress", start)]
        self.assertIn("spent > estimate ? 'soon' : 'normal'", block)
        self.assertIn("formatDurationMinutes(spent)} / ${formatDurationMinutes(estimate)}", block)

    def test_timeline_tracks_estimate_accuracy(self):
        self.assertIn("estimateAccuracy", self.src)
        self.assertIn("Факт к оценке", self.src)
        self.assertIn("if (estimate > 0 && spent > 0) estimateRatios.push(spent / estimate);", self.src)


class TaskComposerTests(unittest.TestCase):
    """Форма задачи: на виду только суть, остальное добавляется чипами."""

    def setUp(self):
        self.src = _read(TASKS_VIEW_PATH)

    def test_sections_are_declarative(self):
        start = self.src.index("const COMPOSER_SECTIONS = [")
        block = self.src[start:self.src.index("\n];", start)]
        ids = re.findall(r"id: '([a-z]+)'", block)
        self.assertEqual(
            ids,
            ['priority', 'tag', 'deadline', 'estimate', 'origin', 'checklist', 'recurrence', 'files'],
        )
        # У каждой секции есть признак заполненности — от него зависит авто-раскрытие при правке.
        self.assertEqual(len(re.findall(r"hasValue:", block)), len(ids))

    def test_closing_a_chip_clears_its_value(self):
        start = self.src.index("const toggleSection = (section) => {")
        block = self.src[start:start + 700]
        self.assertIn("if (section.clear) onChange(section.clear());", block)
        self.assertIn("if (section.requiresFiles) onFilesChange?.([]);", block)

    def test_filled_sections_open_on_edit(self):
        start = self.src.index("const [openIds, setOpenIds] = useState(() => new Set(")
        block = self.src[start:start + 260]
        self.assertIn("section.hasValue(values, files)", block)

    def test_only_essentials_are_always_visible(self):
        start = self.src.index("const TaskComposerForm = ({")
        block = self.src[start:self.src.index("const TaskRow = React.memo", start)]
        # Тема, описание и исполнитель — вне раскрываемых секций.
        self.assertIn('className="tv-composer-title"', block)
        self.assertIn('className="tv-composer-description"', block)
        self.assertIn('className="tv-composer-assignee"', block)
        for gated in ("isOpen('priority')", "isOpen('tag')", "isOpen('deadline')",
                      "isOpen('estimate')", "isOpen('origin')", "isOpen('checklist')",
                      "isOpen('recurrence')"):
            self.assertIn(gated, block)

    def test_both_modals_share_the_composer(self):
        self.assertEqual(self.src.count("<TaskComposerForm"), 2)
        # Флаг бэклога уместен только при создании — правка его не отправляет.
        self.assertEqual(self.src.count("showBacklogToggle\n"), 1)

    def test_files_chip_only_when_uploads_supported(self):
        start = self.src.index("const supportsFiles = typeof onFilesChange === 'function';")
        block = self.src[start:start + 320]
        self.assertIn("!section.requiresFiles || supportsFiles", block)


class TaskOriginTests(unittest.TestCase):
    """Задачи себе + «кто поручил» (пусто = своя инициатива)."""

    def test_columns_added(self):
        src = _read(DATABASE_PATH)
        self.assertIn("ADD COLUMN IF NOT EXISTS requested_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL;", src)
        self.assertIn("ADD COLUMN IF NOT EXISTS requested_by_name VARCHAR(160);", src)

    def test_origin_normalizer_prefers_id(self):
        src = _read(DATABASE_PATH)
        start = src.index("    def _normalize_task_origin(self, requested_by_id, requested_by_name):")
        block = src[start:src.index("    def _parse_task_datetime", start)]
        # Свободный текст читаем только когда сотрудник не выбран.
        self.assertIn("if origin_id is None and requested_by_name not in (None, _UNSET):", block)
        self.assertIn('raise ValueError("INVALID_REQUESTED_BY")', block)

    def test_self_is_always_a_valid_recipient(self):
        src = _read(DATABASE_PATH)
        start = src.index("    def get_task_recipients(self, requester_id, requester_role")
        block = src[start:src.index("    def _task_now", start)]
        self.assertIn("OR u.id = %s", block)
        self.assertIn("params.append(requester_id)", block)

    def test_payload_exposes_resolved_origin(self):
        src = _read(DATABASE_PATH)
        self.assertIn("LEFT JOIN users origin_user ON origin_user.id = t.requested_by_id", src)
        self.assertIn("COALESCE(origin_user.name, t.requested_by_name)", src)
        self.assertIn("\"source\": 'user' if row[36] else 'external'", src)

    def test_api_accepts_origin_on_create_and_patch(self):
        src = _read(APP_PATH)
        self.assertIn("requested_by_id_raw = (request.form.get('requested_by_id')", src)
        self.assertIn("requested_by_id=requested_by_id_raw", src)
        self.assertIn("has_origin = 'requested_by_id' in data or 'requested_by_name' in data", src)
        self.assertIn('edit_kwargs["requested_by_id"] = data.get(\'requested_by_id\')', src)

    def test_form_carries_origin(self):
        src = _read(TASKS_VIEW_PATH)
        self.assertIn("requestedById: '',", src)
        self.assertIn("requestedByName: '',", src)
        self.assertIn("requested_by_id: values.requestedById ? String(values.requestedById) : ''", src)
        # Кнопка «Себе» рядом с исполнителем.
        self.assertIn('className={`tv-composer-self', src)
        self.assertIn("onChange({ assignedTo: String(currentUserId) })", src)

    def test_self_task_without_origin_reads_as_initiative(self):
        src = _read(TASKS_VIEW_PATH)
        start = src.index("const originLabel     = (assigneeId && assigneeId === creatorId && !task?.requested_by)")
        block = src[start:start + 200]
        self.assertIn("'Своя инициатива'", block)
        self.assertIn("'Постановщик'", block)

    def test_cli_supports_self_and_origin(self):
        module = _load_cli_module()
        parser = module.build_parser()
        args = parser.parse_args(['create', 'Тема', '--self', '--from', '169'])
        self.assertTrue(args.self_assign)
        self.assertEqual(args.from_id, 169)
        self.assertEqual(module._origin_label(args), 'поручил id 169')

        external = parser.parse_args(['create', 'Тема', '--self', '--from-name', 'директор'])
        self.assertEqual(module._origin_label(external), 'поручил: директор')

        own = parser.parse_args(['create', 'Тема', '--self'])
        self.assertEqual(module._origin_label(own), 'своя инициатива')

    def test_cli_requires_an_assignee(self):
        module = _load_cli_module()
        args = module.build_parser().parse_args(['create', 'Тема'])

        class FakeClient:
            user_id = 2
        with self.assertRaises(SystemExit):
            module._resolve_assignee(FakeClient(), args)
        args.self_assign = True
        self.assertEqual(module._resolve_assignee(FakeClient(), args), 2)

    def test_log_command_walks_the_whole_flow(self):
        cli = _read(CLI_PATH)
        start = cli.index("def cmd_log(client, args):")
        block = cli[start:cli.index("def cmd_deadline", start)]
        # Создать → в работу → (промежуточные отчёты) → сдать отчётом → принять.
        self.assertIn("steps = [('in_progress', {})]", block)
        self.assertIn("for text in (args.progress or []):", block)
        self.assertIn("steps.append(('completed', {", block)
        self.assertIn("if not args.keep_open:", block)
        self.assertIn("steps.append(('accepted', {}))", block)
        # Без явной оценки берём её равной факту, иначе «факт к оценке» соврёт.
        self.assertIn("if args.spent and 'estimate_minutes' not in fields:", block)


class DeadlineReminderTests(unittest.TestCase):
    """Напоминания в Telegram перед дедлайном — заметки и задачи, максимум за сутки."""

    def setUp(self):
        self.db = _read(DATABASE_PATH)
        self.app = _read(APP_PATH)
        self.view = _read(TASKS_VIEW_PATH)

    def test_columns_on_both_tables_capped_at_one_day(self):
        for table in ('tasks', 'task_notes'):
            marker = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS reminder_minutes_before INTEGER"
            self.assertIn(marker, self.db)
            block = self.db[self.db.index(marker):self.db.index(marker) + 320]
            self.assertIn("reminder_minutes_before <= 1440", block)
            self.assertIn(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS reminder_sent_at TIMESTAMP;", self.db)

    def test_max_is_a_single_day(self):
        self.assertIn("TASK_REMINDER_MAX_MINUTES = 24 * 60", self.db)
        start = self.db.index("    def _normalize_task_reminder_minutes(self, reminder_minutes_before):")
        block = self.db[start:start + 700]
        self.assertIn("if minutes < 0 or minutes > self.TASK_REMINDER_MAX_MINUTES:", block)
        self.assertIn('raise ValueError("INVALID_REMINDER")', block)
        self.assertIn("return minutes or None", block)

    def test_pending_query_skips_done_and_overdue(self):
        start = self.db.index("    def collect_due_task_reminders(self, limit=100):")
        block = self.db[start:self.db.index("    def mark_task_reminder_sent", start)]
        # Заметки: только незавершённые, с чатом, дедлайн ещё впереди.
        self.assertIn("AND n.is_done = FALSE", block)
        self.assertIn("AND n.due_at > %s", block)
        self.assertIn("n.due_at - (n.reminder_minutes_before * INTERVAL '1 minute') <= %s", block)
        # Задачи: не закрытые и не в бэклоге.
        self.assertIn("AND t.status NOT IN ('completed', 'accepted')", block)
        self.assertIn("AND t.is_backlog = FALSE", block)
        self.assertIn("AND t.due_at > %s", block)
        # Только тем, у кого есть Telegram.
        self.assertEqual(block.count("u.telegram_id IS NOT NULL"), 2)

    def test_reminder_resets_when_schedule_moves(self):
        # Задачи (доска) и заметки: съехал срок или интервал — напоминаем заново.
        board = self.db[self.db.index("    def update_task_board_state("):]
        board = board[:board.index("    def delete_task(")]
        self.assertIn("reset_sent = ('deadline' in changed_fields) or ('reminder' in changed_fields)", board)
        self.assertIn("reminder_sent_at = NULL,", board)

        notes = self.db[self.db.index("    def update_task_note("):]
        notes = notes[:notes.index("    def delete_task_note(")]
        self.assertIn("reset_sent = (due_at_new != current[5]) or (reminder_new != current_reminder)", notes)

    def test_no_deadline_means_no_reminder(self):
        self.assertGreaterEqual(self.db.count("if due_at_new is None:\n                reminder_new = None"), 1)
        start = self.db.index("    def create_task_note(")
        block = self.db[start:start + 1400]
        self.assertIn("if due_at_norm else None", block)

    def test_sender_marks_only_after_success(self):
        start = self.app.index("def send_due_task_reminders():")
        block = self.app[start:self.app.index("async def run_task_reminders_async", start)]
        self.assertIn("if response.status_code != 200:", block)
        # Отметка ставится строго после успешной отправки, иначе напоминание потеряется.
        marked = block.index("db.mark_task_reminder_sent")
        failed = block.index("continue")
        self.assertLess(failed, marked)

    def test_scheduler_job_registered(self):
        self.assertIn("id='task_deadline_reminders'", self.app)
        start = self.app.index("id='task_deadline_reminders'")
        block = self.app[start - 300:start + 200]
        self.assertIn("CronTrigger(minute='*/5'", block)
        self.assertIn("max_instances=1", block)
        self.assertIn("coalesce=True", block)

    def test_api_accepts_reminder_everywhere(self):
        self.assertIn("reminder_raw = (request.form.get('reminder_minutes_before')", self.app)
        self.assertIn('edit_kwargs["reminder_minutes_before"] = data.get(\'reminder_minutes_before\')', self.app)
        self.assertIn('kwargs["reminder_minutes_before"] = raw_item.get(\'reminder_minutes_before\')', self.app)
        self.assertIn("reminder_minutes_before=data.get('reminder_minutes_before')", self.app)
        self.assertIn("Напоминание можно поставить максимум за сутки до дедлайна", self.app)

    def test_frontend_options_stop_at_a_day(self):
        start = self.view.index("const REMINDER_OPTIONS = [")
        block = self.view[start:self.view.index("];", start)]
        values = [int(v) for v in re.findall(r"value: (\d+)", block)]
        self.assertEqual(max(values), 1440)
        self.assertIn("const REMINDER_MAX_MINUTES = 1440;", self.view)
        self.assertIn("const REMINDER_DEFAULT_MINUTES = 1440;", self.view)

    def test_setting_a_deadline_defaults_to_day_before(self):
        # Заметка: поставили срок — предлагаем напомнить за день.
        self.assertIn("reminder_minutes_before: nextDue", self.view)
        self.assertIn("|| REMINDER_DEFAULT_MINUTES", self.view)
        # Задача: чип «Дедлайн» открывается с тем же значением по умолчанию.
        self.assertIn("open: (v) => ({ reminderMinutes: v.reminderMinutes || String(REMINDER_DEFAULT_MINUTES) })", self.view)

    def test_reminder_select_disabled_without_deadline(self):
        self.assertIn("disabled={isSaving || !normalizedDraft.due_at}", self.view)
        self.assertIn("disabled={disabled || deadlineMinutesOfForm(values) <= 0}", self.view)

    def test_cli_reminder_parsing(self):
        module = _load_cli_module()
        self.assertEqual(module.parse_reminder_argument('1d'), 1440)
        self.assertEqual(module.parse_reminder_argument('3h'), 180)
        self.assertEqual(module.parse_reminder_argument('за день'), 1440)
        self.assertEqual(module.parse_reminder_argument('off'), 0)
        self.assertEqual(module.parse_reminder_argument(''), 0)
        # Больше суток — отказ, а не молчаливое усечение.
        with self.assertRaises(SystemExit):
            module.parse_reminder_argument('2d')


class ReviewAuthorityTests(unittest.TestCase):
    """Итог принимает поручитель, а не исполнитель."""

    def setUp(self):
        self.db = _read(DATABASE_PATH)
        self.app = _read(APP_PATH)

    def test_authority_prefers_requester_over_creator(self):
        start = self.db.index("    def _task_review_authority(self, created_by, assigned_to, requested_by):")
        block = self.db[start:self.db.index("    def _task_can_review", start)]
        self.assertIn("if requested_by is not None:\n            return int(requested_by)", block)
        self.assertIn("if created_by is not None:\n            return int(created_by)", block)

    def test_assignee_cannot_accept_own_work(self):
        start = self.db.index("    def _task_can_review(self, role, requester_id, created_by, assigned_to, requested_by):")
        block = self.db[start:self.db.index("\n    def ", start + 10)]
        # Своя инициатива — можно, иначе исполнителю отказ.
        self.assertIn("if authority is not None and authority == requester_id:", block)
        self.assertIn("if is_assignee:\n            return False", block)
        # Порядок важен: проверка «я и есть приёмщик» идёт до отказа исполнителю.
        self.assertLess(block.index("authority == requester_id"), block.index("if is_assignee:\n            return False"))

    def test_status_route_uses_the_new_rule(self):
        self.assertIn(
            "is_reviewer = self._task_can_review(role, requester_id, created_by, assigned_to, requested_by)",
            self.db,
        )
        # Прежняя формула, где админ мог принять свою же работу, убрана.
        self.assertNotIn("or (role == 'sv' and not is_assignee)\n            )", self.db)

    def test_requester_can_see_the_task(self):
        start = self.db.index("    def _task_visible_for_requester(")
        block = self.db[start:self.db.index("    def _task_review_authority", start)]
        self.assertIn("if requested_by is not None and int(requested_by) == requester_id:", block)
        # И в списке задач для СВ/тренера.
        self.assertIn("(t.created_by = %s OR t.assigned_to = %s OR t.requested_by_id = %s)", self.db)

    def test_requester_gets_notifications_and_a_call_to_action(self):
        self.assertIn('"kind": "requester"', self.app)
        self.assertIn("requester.telegram_id", self.app)
        self.assertIn("Задача ждёт вашей приёмки", self.app)

    def test_frontend_mirrors_the_rule(self):
        view = _read(TASKS_VIEW_PATH)
        start = view.index("export const canReviewTask = (task, currentUserId, currentUserRole) => {")
        block = view[start:view.index("const buildTaskActionButtons", start)]
        self.assertIn("const authorityId = requesterId || creatorId;", block)
        self.assertIn("if (authorityId && authorityId === currentUserId) return true;", block)
        self.assertIn("if (isAssignee) return false;", block)
        self.assertIn("const canReview  = canReviewTask(task, currentUserId, currentUserRole);", view)

        board = _read(WORKSPACE_PATH)
        self.assertIn("const authorityId = requesterId || creatorId;", board)
        self.assertIn("Итог принимает тот, кто поручил задачу", board)


class ReportReadabilityTests(unittest.TestCase):
    """Отчёт пишется для коллеги, а не для разработчика."""

    def setUp(self):
        self.src = _read(SKILL_PATH)

    def test_skill_demands_plain_language(self):
        self.assertIn("Отчёт читает коллега, а не разработчик", self.src)
        self.assertIn("понятен\nчеловеку не из разработки", self.src.replace('\r\n', '\n'))

    def test_skill_shows_bad_and_good_examples(self):
        # Таблица «нельзя / надо» — самая проверяемая часть требования.
        self.assertIn("| Нельзя | Надо |", self.src)
        self.assertIn("reminder_minutes_before", self.src)
        self.assertIn("за сколько предупредить", self.src)

    def test_skill_forbids_internal_names_in_reports(self):
        self.assertIn(
            "Никаких имён полей, таблиц, функций, файлов, коммитов, названий тестов и команд сборки.",
            self.src,
        )

    def test_skill_tells_me_not_to_accept_my_own_work(self):
        self.assertIn("Приёмку итога делает поручитель", self.src)
        self.assertIn("сдав работу (`status <id> completed`), я **останавливаюсь**", self.src)


class SkillExpectationsTests(unittest.TestCase):
    """В скилле зафиксированы постоянные требования владельца."""

    def setUp(self):
        self.src = _read(SKILL_PATH)

    def test_noise_rule_is_explicit(self):
        self.assertIn("Визуальный и информационный шум", self.src)
        self.assertIn("Цвет только там, где он несёт смысл", self.src)
        self.assertIn("Не дублировать одну и ту же информацию", self.src)

    def test_style_points_at_shared_primitives(self):
        self.assertIn("src/components/ui/ios.jsx", self.src)
        self.assertIn("FullscreenSheet.jsx", self.src)

    def test_optimization_is_required(self):
        self.assertIn("Оптимизация — обязательна", self.src)
        self.assertIn("Батч вместо N запросов", self.src)
        self.assertIn("миграции идемпотентные", self.src)

    def test_best_practice_research_expected(self):
        self.assertIn("Best practice", self.src)
        self.assertIn("медианы вместо средних", self.src)

    def test_workflow_rules_captured(self):
        self.assertIn("без трейлера `Co-Authored-By`", self.src)
        self.assertIn("Весь UI-текст по-русски", self.src)
        self.assertIn("npm run build", self.src)


class DoneColumnTests(unittest.TestCase):
    """
    «Готово» ничего не прячет. Архив на 7 дней убрали вместе с догрузкой в колонке:
    при постраничной выдаче он врал бы про остаток — скрытые карточки считались
    загруженными, и «не показано N» было бы меньше правды.
    """

    def setUp(self):
        self.src = _read(WORKSPACE_PATH)

    def test_board_has_no_hidden_cards(self):
        for gone in ('DONE_ARCHIVE_DAYS', 'isDoneArchived', 'archivedDone', 'archiveOpen'):
            self.assertNotIn(gone, self.src, f'{gone} остался — на доске снова есть скрытые карточки')
        self.assertNotIn('Скрыть архив', self.src)

    def test_done_column_is_an_ordinary_column(self):
        start = self.src.index("  const tasksByColumn = useMemo(() => {")
        block = self.src[start:self.src.index("}, [scopedTasks, dropContext, boardSort]);", start)]
        # Никаких особых правил для 'done': та же раскладка и тот же порядок.
        self.assertNotIn("'done'", block)
        self.assertIn("buckets[columnId].push(", block)
        self.assertIn("compareBoardTasks(left.task, right.task, boardSort)", block)

    def test_cli_has_no_archive_either(self):
        cli = _read(CLI_PATH)
        for gone in ('DONE_ARCHIVE_DAYS', 'is_done_archived', '--archive'):
            self.assertNotIn(gone, cli, f'{gone} остался в CLI — правило разъехалось с доской')
        # Момент приёмки всё ещё нужен: по нему «Готово» сортируется.
        self.assertIn("def accepted_at(task):", cli)

    def test_skill_documents_the_new_rule(self):
        skill = _read(SKILL_PATH)
        self.assertNotIn('--archive', skill)
        self.assertIn('Посмотреть ещё', skill)
        self.assertIn('Архива у «Готово» больше нет', skill)


class TimelineFullscreenTests(unittest.TestCase):
    def setUp(self):
        self.src = _read(WORKSPACE_PATH)

    def test_fullscreen_uses_shared_sheet_via_portal(self):
        self.assertIn("import FullscreenSheet from '../common/FullscreenSheet';", self.src)
        start = self.src.index("  if (expanded) {")
        block = self.src[start:start + 700]
        self.assertIn("createPortal(", block)
        self.assertIn("document.body", block)
        self.assertIn("wide", block)
        self.assertIn('title="Таймлайн задач"', block)

    def test_same_markup_in_both_modes(self):
        # Контент собирается один раз и лишь оборачивается — двойного DOM таблицы нет.
        self.assertIn("const content = (", self.src)
        self.assertEqual(self.src.count("Медианный цикл"), 1)

    def test_row_area_grows_in_fullscreen(self):
        self.assertIn("expanded ? 'max-h-[calc(100vh-280px)]' : 'max-h-[520px]'", self.src)

    def test_sheet_supports_wide_content(self):
        sheet = _read(ROOT / "src" / "components" / "common" / "FullscreenSheet.jsx")
        self.assertIn("wide = false", sheet)
        self.assertIn("${wide ? '' : 'max-w-5xl'}", sheet)


class ReportCliTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_cli_module()

    def test_report_commands_registered(self):
        parser = self.module.build_parser()
        subparsers = next(
            action for action in parser._actions if isinstance(action, __import__('argparse')._SubParsersAction)
        )
        for command in ('report', 'reports', 'status', 'board', 'backlog'):
            self.assertIn(command, subparsers.choices)

    def test_completed_without_report_is_rejected(self):
        args = self.module.build_parser().parse_args(['status', '412', 'completed'])
        with self.assertRaises(SystemExit):
            self.module.cmd_status(None, args)

    def test_report_accepts_spent_and_final(self):
        args = self.module.build_parser().parse_args(
            ['report', '412', 'сделал', '--spent', '2h30m', '--final']
        )
        self.assertTrue(args.final)
        self.assertEqual(self.module.parse_duration_to_minutes(args.spent), 150)


class ActionNeedsBadgeTests(unittest.TestCase):
    """Уведомления «задача ждёт вас»: раздел, бейдж сайдбара и SQL считают одно и то же."""

    ACTION_NEEDS_PATH = ROOT / "src" / "components" / "tasks" / "taskActionNeeds.js"

    def test_sql_summary_mirrors_frontend_rules(self):
        src = _read(DATABASE_PATH)
        start = src.index("    def get_task_action_needs_summary(self, requester_id):")
        block = src[start:src.index("    def get_tasks_for_requester(", start)]
        # Просрочка — только у исполнителя и только по живым статусам.
        self.assertIn("t.status IN ('assigned', 'in_progress', 'returned')", block)
        self.assertIn("t.due_at < %s", block)
        # Приёмку ждёт поручитель, а если его нет — постановщик.
        self.assertIn("COALESCE(t.requested_by_id, t.created_by) = %s", block)
        self.assertIn("t.status = 'completed'", block)
        # Бэклог не считается: это очередь планирования.
        self.assertEqual(block.count("t.is_backlog = FALSE"), 3)
        self.assertIn("taskActionNeeds.js", block)

    def test_acceptance_is_a_notice_in_all_three_copies(self):
        """«Работу приняли» обязана совпасть в SQL бейджа, в клиенте и в колоколе."""
        db_src = _read(DATABASE_PATH)
        start = db_src.index("    def get_task_action_needs_summary(self, requester_id):")
        block = db_src[start:db_src.index("    TASK_ACTION_NEED_KINDS", start)]
        self.assertIn("t.status = 'accepted'", block)
        self.assertIn('"accepted": int(row[4] or 0)', block)
        self.assertIn(
            "TASK_ACTION_NEED_KINDS = ('overdue', 'returned', 'review', 'fresh', 'accepted')",
            db_src,
        )

        client = _read(self.ACTION_NEEDS_PATH)
        self.assertIn("'accepted'", client)
        self.assertIn("if (status === 'accepted' && isAssignee)", client)

        bell = _read(ROOT / "notifications" / "sources.py")
        self.assertIn("'accepted': 'Работу приняли'", bell)

    def test_history_is_marked_seen_once_on_rollout(self):
        """Иначе при первом запуске всплыла бы вся история принятых задач —
        на боевой базе это 73 штуки у одного человека."""
        db_src = _read(DATABASE_PATH)
        start = db_src.index("    def _backfill_task_accepted_reads_tx(self, cursor):")
        block = db_src[start:db_src.index("    def _init_bell_notify_schema_tx", start)]
        # Идемпотентность по состоянию: отметка уже есть — бэкфилл не повторяем,
        # иначе он гасил бы и новые принятия, ради которых всё делалось.
        self.assertIn("SELECT 1 FROM task_action_reads WHERE kind = 'accepted' LIMIT 1", block)
        self.assertIn("if cursor.fetchone() is not None:", block)
        self.assertIn("INSERT INTO task_action_reads", block)
        self.assertIn("self._backfill_task_accepted_reads_tx(cursor)", db_src)

    def test_endpoint_is_guarded_like_the_rest_of_the_section(self):
        src = _read(APP_PATH)
        self.assertIn("@app.route('/api/tasks/action_required', methods=['GET', 'OPTIONS'])", src)
        start = src.index("def get_task_action_required_count():")
        block = src[start:src.index("@app.route('/api/tasks/notes'", start)]
        self.assertIn("_task_route_guard()", block)
        self.assertIn("db.get_task_action_needs_summary(requester_id)", block)

    def test_read_marker_table_is_per_user_and_per_task(self):
        src = _read(DATABASE_PATH)
        start = src.index("CREATE TABLE IF NOT EXISTS task_action_reads (")
        block = src[start:src.index(");", start)]
        self.assertIn("user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE", block)
        self.assertIn("task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE", block)
        self.assertIn("kind VARCHAR(16) NOT NULL", block)
        self.assertIn("PRIMARY KEY (user_id, task_id)", block)

    def test_seen_notifications_leave_the_counter(self):
        src = _read(DATABASE_PATH)
        start = src.index("    def get_task_action_needs_summary(self, requester_id):")
        block = src[start:src.index("    def mark_task_action_seen(", start)]
        self.assertIn("LEFT JOIN task_action_reads r ON r.task_id = t.id AND r.user_id = %s", block)
        # У каждой причины своя проверка отметки: сменилась причина или задачу
        # тронули после просмотра — уведомление снова считается.
        for kind in ("overdue", "returned", "review", "fresh"):
            self.assertIn(
                f"AND (r.task_id IS NULL OR r.kind <> '{kind}' OR r.seen_at < t.updated_at)",
                block,
            )

    def test_marking_seen_is_idempotent_and_returns_fresh_count(self):
        src = _read(DATABASE_PATH)
        start = src.index("    def mark_task_action_seen(self, requester_id, task_id, kind):")
        block = src[start:src.index("    def get_tasks_for_requester(", start)]
        self.assertIn("ON CONFLICT (user_id, task_id) DO UPDATE", block)
        self.assertIn("INVALID_TASK_ACTION_KIND", block)
        self.assertIn("return self.get_task_action_needs_summary(requester_id)", block)
        # Задача отдаёт свою отметку клиенту, иначе раздел не знает о просмотре.
        self.assertIn("LEFT JOIN task_action_reads action_read", src)
        self.assertIn('"action_seen": ({', src)

    def test_seen_endpoint_is_registered(self):
        src = _read(APP_PATH)
        self.assertIn(
            "@app.route('/api/tasks/<int:task_id>/action_seen', methods=['POST', 'OPTIONS'])",
            src,
        )
        start = src.index("def mark_task_action_seen(task_id):")
        block = src[start:src.index("@app.route('/api/tasks/notes'", start)]
        self.assertIn("_task_route_guard()", block)
        self.assertIn("db.mark_task_action_seen(requester_id, task_id, payload.get('kind'))", block)
        self.assertIn('"Invalid action kind"', block)

    def test_section_marks_notification_seen_on_open(self):
        src = _read(TASKS_VIEW_PATH)
        self.assertIn("countUnseenActionNeeds", src)
        # Бейдж считает только новые.
        self.assertIn("onActionNeedsChange(actionNeedsUnseen)", src)
        start = src.index("const markActionNeedsSeen = useCallback(")
        block = src[start:src.index("// Переход из уведомления", start)]
        self.assertIn("/api/tasks/${need.task.id}/action_seen", block)
        self.assertIn("actionNeedSeenKey(need.task, need.kind)", block)
        self.assertIn("markActionNeedsSeen(need);", src)

    def test_card_shows_blinking_at_marker_until_read(self):
        src = _read(WORKSPACE_PATH)
        self.assertIn("const ActionAtMarker = ({ kind }) => {", src)
        self.assertIn("{actionNeed && !actionNeed.seen && <ActionAtMarker kind={actionNeed.kind} />}", src)
        view = _read(TASKS_VIEW_PATH)
        self.assertIn(".tb-at-marker", view)
        self.assertIn("@keyframes tbAtBlink", view)
        # Прочтение = открытие карточки, каким бы путём её ни открыли.
        self.assertIn("const need = actionNeedOf(drawerTask);", view)
        self.assertIn("prefers-reduced-motion", view)

    def test_frontend_rules_are_exclusive_and_skip_backlog(self):
        src = _read(self.ACTION_NEEDS_PATH)
        self.assertIn("if (!isAssignee || task?.is_backlog) return null;", src)
        self.assertIn("return { kind: 'overdue', dueAt };", src)
        self.assertIn("return { kind: 'returned', dueAt };", src)
        self.assertIn("return { kind: 'review', dueAt };", src)
        self.assertIn("return { kind: 'fresh', dueAt };", src)

    def test_section_shows_notifications_and_jumps_to_the_card(self):
        src = _read(TASKS_VIEW_PATH)
        self.assertIn("collectTaskActionNeeds", src)
        self.assertIn("<TaskInbox", src)
        self.assertIn("needs={actionNeeds}", src)
        self.assertIn("onOpen={openActionNeed}", src)
        # Клик по уведомлению: доска → подсветка карточки → карточка задачи.
        start = src.index("const openActionNeed = useCallback((need) => {")
        block = src[start:src.index("}, [markActionNeedsSeen, selectWorkspaceTab]);", start)]
        self.assertIn("selectWorkspaceTab('board')", block)
        self.assertIn("setBoardFocus({ taskId: Number(task.id), token: Date.now() })", block)
        self.assertIn("setDrawerTask(task)", block)

    def test_board_reveals_the_focused_card(self):
        src = _read(WORKSPACE_PATH)
        self.assertIn("focusRequest = null,", src)
        # Карточку надо показать, даже если она вне текущей доски сотрудника.
        self.assertIn("setScope('my');", src)
        self.assertIn("cardRef.current?.scrollIntoView(", src)
        self.assertIn("tb-card-focus", src)

    def test_sidebar_badge_is_wired(self):
        src = _read(APP_JSX_PATH)
        self.assertIn("const renderTasksSidebarButtonInner = () => (", src)
        # Пункт «Задачи» рендерится в нескольких ветках меню — бейдж нужен во всех.
        self.assertEqual(src.count("{renderTasksSidebarButtonInner()}"), 3)
        self.assertIn("tasksActionRequiredCount,", src)
        self.assertIn("onActionNeedsChange={handleTasksActionNeedsChange}", src)

    def test_sidebar_badge_does_not_poll_in_background(self):
        """Число приносит сводка колокола (она событийная: вход и возврат фокуса
        с троттлингом REFRESH_GAP_MS — см. tests/test_four_you_access_controls),
        а при открытом разделе его ведёт сам раздел по загруженному списку.
        Отдельного опроса /api/tasks/action_required у бейджа больше нет."""
        src = _read(APP_JSX_PATH)
        self.assertNotIn("fetchTasksActionRequiredCount", src)
        self.assertNotIn("TASKS_BADGE_MIN_GAP_MS", src)
        start = src.index("const stableNotificationsCounts = useCallback(")
        block = src[start:src.index("const sidebarTree = useMemo(", start)]
        # Питание из колокола, но раздел открыт — число не трогаем: снимок,
        # снятый до действия пользователя, перетёр бы более свежее значение.
        self.assertIn("'tasks' in counts && currentView !== 'tasks'", block)
        self.assertIn("setTasksActionRequiredCount", block)

    def test_mount_zero_does_not_clear_the_bell(self):
        """Транзиентный ноль на маунте раздела (tasks=[] до ответа сервера) не
        должен уходить в App: там count===0 гасит источник 'tasks' в колоколе,
        и простой вход в раздел стирал бы задачи из панели на 5+ минут."""
        src = _read(TASKS_VIEW_PATH)
        start = src.index("if (!hasLoadedTasks) return;")
        block = src[start:src.index("}, [hasLoadedTasks, actionNeedsUnseen, onActionNeedsChange]);", start)]
        self.assertIn("onActionNeedsChange(actionNeedsUnseen)", block)
        app_src = _read(APP_JSX_PATH)
        self.assertIn("if (next === 0) markBellSourceRead('tasks');", app_src)
        # Симметричный сброс фокус-запроса — как у вики: иначе после клика по
        # задаче в колоколе каждый обычный вход в раздел открывал бы её снова.
        self.assertIn("if (view !== 'tasks') setTaskFocusRequest(null);", app_src)


class BoardPaginationTests(unittest.TestCase):
    """Доска грузит страницу, а не всю базу: сотрудник — своим запросом, общая — по количеству."""

    def test_board_query_is_scoped_per_board(self):
        src = _read(BOARD_QUERY_PATH)
        start = src.index("export const boardQueryParams = (")
        block = src[start:]
        self.assertIn("if (mode === 'backlog') params.backlog = 'only';", block)
        self.assertIn("if (scope === 'my') params.mine = 'any';", block)
        self.assertIn("else if (scope === 'assigned') params.mine = 'assignee';", block)
        self.assertIn("params.person_scope = 'any';", block)
        self.assertIn("const params = { limit, offset };", block)

    def test_chunk_sizes_are_a_closed_list(self):
        # Поведение проверяется в tests/board_query.test.mjs, здесь — что модуль на месте.
        src = _read(BOARD_QUERY_PATH)
        self.assertIn("export const BOARD_CHUNK_SIZES = [20, 40, 60];", src)
        self.assertIn("BOARD_CHUNK_SIZES.includes(parsed)", src)
        self.assertIn("import { boardQueryParams } from './boardQuery';", _read(TASKS_VIEW_PATH))

    def test_server_caps_page_size_and_unbounded_requests(self):
        app_src = _read(APP_PATH)
        self.assertIn("TASKS_MAX_PAGE_SIZE = int(os.getenv('TASKS_MAX_PAGE_SIZE', '200'))", app_src)
        self.assertIn("if limit < 1 or limit > TASKS_MAX_PAGE_SIZE:", app_src)
        db_src = _read(DATABASE_PATH)
        self.assertIn("TASKS_MAX_ROWS = 500", db_src)
        start = db_src.index("        if limit is None or str(limit).strip() == '':")
        block = db_src[start:start + 500]
        # Запрос без limit и запрос с огромным limit одинаково упираются в потолок.
        self.assertIn("limit_norm = self.TASKS_MAX_ROWS", block)
        self.assertIn("limit_norm = min(limit_norm, self.TASKS_MAX_ROWS)", block)

    def test_mine_filter_covers_board_scopes(self):
        src = _read(DATABASE_PATH)
        self.assertIn("if mine_norm == 'assignee':", src)
        self.assertIn('base_conditions.append("t.assigned_to = %s")', src)
        self.assertIn("elif mine_norm == 'creator':", src)
        self.assertIn("INVALID_TASK_MINE_FILTER", src)
        self.assertIn("INVALID_TASK_MINE_FILTER", _read(APP_PATH))

    def test_person_board_summary_counts_the_whole_board(self):
        src = _read(DATABASE_PATH)
        # Фильтр доски сотрудника уходит в базовые условия — иначе сводка считала бы отдел.
        self.assertIn("if person_id_norm is not None and person_scope_norm == 'any':", src)
        self.assertIn("person_board_id = person_id_norm", src)
        self.assertIn("AS overdue_count", src)
        self.assertIn("AS delegated_count", src)
        view = _read(WORKSPACE_PATH)
        start = view.index("  const focusStats = useMemo(() => {")
        block = view[start:view.index("  const dropContext = useMemo(", start)]
        self.assertIn("boardSummary", block)
        self.assertNotIn("scopedTasks.forEach", block)

    def test_section_no_longer_pulls_every_task(self):
        src = _read(TASKS_VIEW_PATH)
        start = src.index("  const fetchTasks = useCallback(async () => {")
        block = src[start:src.index("  const loadBoardTasks = useCallback(", start)]
        self.assertIn("params: { only_my: 1 }", block)
        # Цифры «Обзора» приходят серверной сводкой, а не считаются по списку.
        self.assertIn("total:      Number(tasksSummary?.total || 0),", src)
        self.assertIn("setTasksSummary(data?.summary || null);", src)
        # Диплинк на задачу вне выборки догружается точечно.
        self.assertIn("params: { task_id: taskId, limit: 1 }", src)

    def test_importance_sort_is_applied_across_the_whole_board(self):
        db_src = _read(DATABASE_PATH)
        start = db_src.index("            importance_sql = (")
        block = db_src[start:db_src.index("            task_query = f", start)]
        # Тот же порядок, что в compareBoardTasks: важность, затем свежесть и id.
        self.assertIn("CASE t.priority WHEN 'critical' THEN 0 WHEN 'urgent' THEN 1 ELSE 2 END ASC", block)
        self.assertIn("t.created_at DESC, t.id DESC", block)
        # Ручная очередь бэклога сортировке не подчиняется.
        self.assertIn("if backlog_norm == 'only':", block)
        self.assertIn("elif sort_norm == 'importance':", block)
        self.assertIn("INVALID_TASK_SORT", db_src)
        self.assertIn("INVALID_TASK_SORT", _read(APP_PATH))

        self.assertIn("if (sort === 'importance') params.sort = 'importance';", _read(BOARD_QUERY_PATH))
        view = _read(WORKSPACE_PATH)
        # Порядок задаётся сервером, поэтому смена сортировки перезапрашивает выборку.
        self.assertIn("sort: boardSort,", view)
        self.assertIn("const changeBoardSort = useCallback((next) => {", view)
        self.assertIn("resetColumns();", view)

    def test_columns_show_one_chunk_and_hand_the_rest_to_the_window(self):
        src = _read(WORKSPACE_PATH)
        # Колонка = свой срез статусов, поэтому её остаток считается в базе.
        query_src = _read(BOARD_QUERY_PATH)
        start = query_src.index("export const BOARD_COLUMN_QUERY = {")
        block = query_src[start:query_src.index("export const BOARD_CHUNK_SIZES", start)]
        self.assertIn("progress: { status: 'in_progress,returned', backlog: 'exclude' }", block)
        self.assertIn("backlog:  { backlog: 'only' }", block)

        src = _read(WORKSPACE_PATH)
        self.assertIn("hidden: Math.max(0, total - loaded)", src)
        # Остаток — не кнопка догрузки, а вход в окно статуса.
        self.assertIn("Посмотреть ещё · не показано {meta.hidden}", src)
        self.assertIn("onClick={() => onBrowseColumn?.(column)}", src)
        self.assertNotIn("append: true", src)
        # В колонке всегда ровно одна порция, копить карточки в ней незачем.
        self.assertIn("BOARD_COLUMNS.forEach((column) => fetchColumn(column.id, { limit: chunkSize }));", src)
        # Пагинатор остаётся только там, где список один.
        self.assertIn("{!isBoardMode && (", src)
        self.assertIn("const BoardPager = ({", src)

    def test_repeating_the_same_choice_does_not_blank_the_board(self):
        src = _read(WORKSPACE_PATH)
        # Повторный клик по уже выбранному значению обнулял колонки, а ключ
        # перезагрузки не менялся — доска оставалась в вечном скелетоне.
        self.assertIn("if (next === scope) return;", src)
        self.assertIn("if (next === boardSort) return;", src)
        self.assertIn("if (normalized === chunkSize) return;", src)
        # Любой сброс всё равно обязан привести к загрузке.
        self.assertIn("setColumnsResetToken((prev) => prev + 1);", src)
        self.assertIn(
            "const boardReloadKey = `${scope}|${boardSort}|${reloadToken}|${chunkSize}|${columnsResetToken}`;",
            src,
        )
        # На первом рендере сброс не нужен — колонки и так грузятся с нуля.
        self.assertIn("if (previousModeRef.current === mode) return;", src)

    def test_failed_column_load_does_not_stick_in_skeleton(self):
        src = _read(WORKSPACE_PATH)
        start = src.index("  const fetchColumn = useCallback(async (columnId, {")
        block = src[start:src.index("  const boardReloadKey =", start)]
        self.assertIn("} finally {", block)
        self.assertIn("loading: false", block.split("} finally {")[1])

    def test_status_filter_accepts_a_list(self):
        src = _read(DATABASE_PATH)
        self.assertIn("status_values = [part.strip().lower() for part in str(status or '').split(',') if part.strip()]", src)
        self.assertIn('filtered_conditions.append("t.status = ANY(%s)")', src)


class TaskQueryBuilderTests(unittest.TestCase):
    """
    Сборка условий выборки. Порядок здесь ломается молча: список фильтров
    инициализировался после первых append'ов, и раздел «Бэклог» отвечал 500
    (UnboundLocalError: filtered_conditions). Проверяем по AST, а не по тексту.
    """

    def setUp(self):
        import ast
        tree = ast.parse(_read(DATABASE_PATH))
        self.func = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == 'get_tasks_for_requester'
        )
        self.ast = ast

    def _first_use_is_assignment(self, name):
        for node in self.ast.walk(self.func):
            if isinstance(node, self.ast.Name) and node.id == name:
                return isinstance(node.ctx, self.ast.Store)
            if isinstance(node, self.ast.Attribute) and isinstance(node.value, self.ast.Name)                     and node.value.id == name:
                return False
        self.fail(f"{name} не встречается в get_tasks_for_requester")

    def test_locals_are_assigned_before_use(self):
        for name in ('base_conditions', 'base_params', 'filtered_conditions', 'filtered_params'):
            self.assertTrue(
                self._first_use_is_assignment(name),
                f"{name} используется раньше, чем создан — запрос упадёт на первом же фильтре",
            )

    def test_filtered_starts_from_base(self):
        src = _read(DATABASE_PATH)
        start = src.index("    def get_tasks_for_requester(")
        block = src[start:src.index("        base_where_sql = ", start)]
        init_at = block.index("filtered_conditions = list(base_conditions)")
        # Фильтры выборки добавляются только после копирования базовых условий.
        for filter_marker in (
            'filtered_conditions.append("t.status = %s")',
            'filtered_conditions.append("t.is_backlog = TRUE")',
            'filtered_conditions.append("t.id = %s")',
        ):
            self.assertGreater(block.index(filter_marker), init_at, filter_marker)
        # Доска сотрудника уходит в базовые условия до копирования.
        self.assertLess(block.index("person_board_id = person_id_norm"), init_at)

    def test_summary_is_optional(self):
        src = _read(DATABASE_PATH)
        self.assertIn("include_summary=True", src)
        self.assertIn("if include_summary:", src)
        app_src = _read(APP_PATH)
        self.assertIn("include_summary = (request.args.get('summary') or '')", app_src)
        # Колонки доски просят сводку ровно один раз за загрузку.
        view = _read(WORKSPACE_PATH)
        self.assertIn("withSummary: columnId === BOARD_COLUMNS[0].id,", view)
        # Окно статуса листает страницами и сводку не трогает вовсе.
        self.assertIn("withSummary: false,", view)

    def test_board_loader_passes_the_column_through(self):
        src = _read(TASKS_VIEW_PATH)
        start = src.index("  const loadBoardTasks = useCallback(async (")
        block = src[start:src.index("  useEffect(() => {", start)]
        # Без column все пять запросов были одинаковыми, а колонки показывали одно и то же.
        self.assertIn("column", block.split(')')[0])
        self.assertIn("boardQueryParams({ scope, mode, sort, column, limit, offset, withSummary })", block)

    def test_column_queries_are_covered_by_indexes(self):
        src = _read(DATABASE_PATH)
        self.assertIn("idx_tasks_status_created", src)
        self.assertIn("ON tasks(status, created_at DESC, id DESC)", src)
        self.assertIn("ON tasks(assigned_to, created_at DESC, id DESC)", src)
        self.assertIn("ON tasks(created_by, created_at DESC, id DESC)", src)


class ColumnBrowserTests(unittest.TestCase):
    """Окно статуса: дни по вертикали, карточки по горизонтали, догрузка прокруткой."""

    def setUp(self):
        self.src = _read(WORKSPACE_PATH)
        start = self.src.index("const ColumnBrowser = ({")
        self.block = self.src[start:self.src.index("const BoardCard = ({", start)]

    def test_window_opens_from_the_column_footer(self):
        self.assertIn("<ColumnBrowser", self.src)
        self.assertIn("onBrowseColumn={setBrowsedColumn}", self.src)
        self.assertIn("column={browsedColumn}", self.src)
        # Окно живёт в общей полноэкранной обёртке и рисуется в портал.
        self.assertIn("createPortal(", self.block)
        self.assertIn("<FullscreenSheet", self.block)

    def test_days_go_down_and_cards_go_right(self):
        self.assertIn("groupTasksByDay(tasks)", self.block)
        # Карточки дня — горизонтальная лента фиксированной ширины.
        self.assertIn('className="-mx-1 flex gap-2.5 overflow-x-auto px-1 pb-2"', self.block)
        self.assertIn('className="w-[268px] shrink-0"', self.block)
        # Карточка вне доски не перетаскивается.
        self.assertIn("const isDraggable = typeof onDragStart === 'function';", self.src)
        self.assertIn("draggable={isDraggable}", self.src)

    def test_task_opens_over_the_window_without_closing_it(self):
        # Окно живёт ниже карточки задачи и её модалок: закрыл задачу — остался там,
        # где смотрел. Раньше окно закрывалось, и приходилось открывать его заново.
        self.assertIn("const COLUMN_BROWSER_Z = ", self.src)
        self.assertIn("z={COLUMN_BROWSER_Z}", self.block)
        self.assertIn("onOpen={onOpenTask}", self.block)
        self.assertNotIn("const openTask = useCallback", self.block)
        # Esc при открытой задаче закрывает задачу, а не оба слоя.
        self.assertIn("closeOnEscape={!isTaskOpen}", self.block)
        self.assertIn("isTaskOpen={Boolean(drawerTask)}", _read(TASKS_VIEW_PATH))
        sheet = _read(ROOT / "src" / "components" / "common" / "FullscreenSheet.jsx")
        self.assertIn("closeOnEscape = true,", sheet)
        self.assertIn("if (e.key === 'Escape' && closeOnEscape) onClose?.();", sheet)

    def test_window_stays_below_the_task_layers(self):
        """
        Инвариант слоёв: окно статуса ниже оверлея задачи, самой карточки и её
        модалок. Иначе клик по карточке в окне открывает задачу под окном —
        визуально «ничего не произошло».
        """
        browser_z = int(re.search(r"const COLUMN_BROWSER_Z = (\d+);", self.src).group(1))
        view = _read(TASKS_VIEW_PATH)

        def z_of(selector):
            block = view[view.index(selector):]
            return int(re.search(r"z-index:\s*(\d+);", block).group(1))

        overlay_z = z_of("  .tv-overlay {")
        drawer_z = z_of("  .tv-drawer {")
        modal_z = z_of("  .tv-modal-overlay {")
        self.assertLess(browser_z, overlay_z, 'окно перекрыло бы затемнение задачи')
        self.assertLess(browser_z, drawer_z, 'карточка задачи открылась бы под окном')
        self.assertLess(browser_z, modal_z, 'модалки задачи открылись бы под окном')

    def test_window_leaves_the_sidebar_visible(self):
        """
        Окно занимает область контента, а не весь экран: сайдбар остаётся видимым
        и рабочим. Раньше окно либо уезжало под сайдбар, либо накрывало его целиком.
        """
        self.assertIn(
            "const COLUMN_BROWSER_OFFSET_LEFT = 'var(--app-sidebar-offset, 0px)';",
            self.src,
        )
        self.assertIn("offsetLeft={COLUMN_BROWSER_OFFSET_LEFT}", self.block)

        sheet = _read(ROOT / "src" / "components" / "common" / "FullscreenSheet.jsx")
        self.assertIn("offsetLeft = null,", sheet)
        self.assertIn("{ left: offsetLeft, transition: 'left 0.3s ease' }", sheet)

        styles = _read(ROOT / "src" / "styles.css")
        # Переменная обязана жить на :root — портал в document.body не видит
        # переменные, объявленные на .main-content.
        self.assertRegex(styles, r":root\s*\{\s*--app-sidebar-offset:\s*256px;")
        self.assertRegex(styles, r"body\.sidebar-collapsed\s*\{\s*--app-sidebar-offset:\s*80px;")
        # На мобильном обнуляются оба селектора: body-класс перебил бы один :root.
        self.assertRegex(styles, r":root,\s*body\.sidebar-collapsed\s*\{\s*--app-sidebar-offset:\s*0px;")
        # Старое имя переменной сохранено: его читает панель в LmsView.
        self.assertIn("--main-content-sidebar-offset: var(--app-sidebar-offset);", styles)
        self.assertIn('style={{ left: "var(--main-content-sidebar-offset, 0px)" }}',
                      _read(ROOT / "src" / "components" / "lms" / "LmsView.jsx"))

        app_src = _read(APP_JSX_PATH)
        self.assertIn("document.body.classList.toggle('sidebar-collapsed', sidebarCollapsed);", app_src)
        # Именно layout-эффект: сайдбар по умолчанию свёрнут, обычный useEffect
        # дал бы кадр с отступом развёрнутого — скачок контента при загрузке.
        start = app_src.index("document.body.classList.toggle('sidebar-collapsed'")
        self.assertIn("useLayoutEffect(() => {", app_src[start - 400:start])

    def test_open_window_lifts_the_sidebar_above_itself(self):
        """
        Отступа мало: сайдбар шире, чем занимает. Кнопка сворачивания висит за его
        правым краем (-right-4), а свёрнутый сайдбар разворачивается по наведению —
        и то и другое попадало под окно, которое рисуется выше навигации. Пока окно
        открыто, сайдбар поднимается над ним, но остаётся ниже карточки задачи.
        """
        sheet = _read(ROOT / "src" / "components" / "common" / "FullscreenSheet.jsx")
        self.assertIn("document.body.classList.add('sheet-beside-sidebar');", sheet)
        # Layout-эффект: сдвиг окна попадает в первый кадр, класс обязан успеть туда же,
        # иначе кадр рисуется с сайдбаром под окном и кнопка сворачивания мигает.
        start = sheet.index("document.body.classList.add('sheet-beside-sidebar')")
        self.assertIn("useLayoutEffect(() => {", sheet[start - 400:start])
        # Счётчик, а не флаг: закрытие одного окна не снимает класс, пока открыто другое.
        self.assertIn("sheetsBesideSidebar = Math.max(0, sheetsBesideSidebar - 1);", sheet)
        self.assertIn(
            "if (sheetsBesideSidebar === 0) document.body.classList.remove('sheet-beside-sidebar');",
            sheet,
        )
        # Класс вешает только окно со смещением: на весь экран окно и должно накрывать сайдбар.
        self.assertIn("if (!open || !offsetLeft) return undefined;", sheet)

        styles = _read(ROOT / "src" / "styles.css")
        # Дополнение мобильного запроса, а не «769px»: при дробной ширине (зум, масштаб
        # ОС) на 768.5px не сработал бы ни один из двух, и правка молча отключалась бы.
        mobile_start = styles.rindex("@media (max-width:", 0, styles.index("--app-sidebar-offset: 0px;"))
        mobile = re.match(r"@media \(max-width: (\d+)px\) \{", styles[mobile_start:])
        lifted = re.search(
            r"@media not all and \(max-width: (\d+)px\) \{\s*body\.sheet-beside-sidebar \.sidebar \{\s*z-index:\s*(\d+);",
            styles,
        )
        self.assertIsNotNone(lifted, 'сайдбар не поднимается над открытым окном')
        self.assertEqual(lifted.group(1), mobile.group(1), 'между мобильным и десктопным режимом осталась щель')
        lifted_z = int(lifted.group(2))

        app_src = _read(APP_JSX_PATH)
        sidebar_z = int(re.search(r'className=\{`sidebar fixed[^`]*?\bz-(\d+)\b', app_src).group(1))
        browser_z = int(re.search(r"const COLUMN_BROWSER_Z = (\d+);", self.src).group(1))
        view = _read(TASKS_VIEW_PATH)
        overlay_z = int(re.search(r"z-index:\s*(\d+);", view[view.index("  .tv-overlay {"):]).group(1))
        # Поповер «Оценка и срок» портируется в body, поэтому подъём сайдбара обязан
        # учитывать и его: иначе развёрнутый по наведению сайдбар накрывает пресеты.
        popover_z = int(re.search(r'className="fixed z-\[(\d+)\]', self.src).group(1))

        self.assertGreater(lifted_z, sidebar_z, 'подъём ничего не меняет')
        self.assertGreater(lifted_z, browser_z, 'сайдбар остался под окном')
        self.assertLess(lifted_z, overlay_z, 'сайдбар торчал бы поверх открытой задачи')
        self.assertGreater(popover_z, lifted_z, 'поднятый сайдбар накрыл поповер планирования')
        self.assertLess(popover_z, overlay_z, 'поповер перекрыл бы открытую задачу')

    def test_window_stays_above_app_navigation(self):
        """
        Второй конец того же инварианта: опустив окно под карточку задачи, легко
        уронить его под сайдбар — именно это и случилось. Сравниваем с реальными
        значениями навигации, а не с числами в тексте теста.
        """
        browser_z = int(re.search(r"const COLUMN_BROWSER_Z = (\d+);", self.src).group(1))
        app_src = _read(APP_JSX_PATH)
        sidebar_z = int(re.search(r'className=\{`sidebar fixed[^`]*?\bz-(\d+)\b', app_src).group(1))
        styles = _read(ROOT / "src" / "styles.css")

        def css_z(selector):
            block = styles[styles.index(selector):]
            return int(re.search(r"z-index:\s*(\d+);", block).group(1))

        self.assertGreater(browser_z, sidebar_z, 'окно уехало под сайдбар')
        self.assertGreater(browser_z, css_z(".hamburger-btn {"), 'окно уехало под гамбургер')
        self.assertGreater(browser_z, css_z(".sidebar-overlay {"), 'окно уехало под затемнение меню')

    def test_scroll_loads_the_next_page(self):
        self.assertIn("new IntersectionObserver(", self.block)
        self.assertIn("loadPage(tasks.length)", self.block)
        self.assertIn("const hasMore = tasks.length < total;", self.block)
        # Догрузка склеивает страницы по id и не стреляет параллельно сама в себя.
        self.assertIn("if (loadingRef.current) return;", self.block)
        self.assertIn("new Map([...prev, ...incoming].map((task) => [task.id, task]))", self.block)


class BoardPeopleAndBacklogActionTests(unittest.TestCase):
    """Список досок и ручной возврат задачи в бэклог."""

    def test_board_people_exclude_fired_and_carry_department(self):
        src = _read(DATABASE_PATH)
        start = src.index("    def get_task_board_people(self, requester_id, requester_role):")
        block = src[start:src.index("    def _task_now(self):", start)]
        self.assertIn("COALESCE(u.status, 'working') <> 'fired'", block)
        self.assertIn("LEFT JOIN departments d ON d.id = u.department_id", block)
        # СВ и тренер видят только тех, с кем пересеклись по задачам.
        self.assertIn("(t.created_by = %s OR t.assigned_to = %s OR t.requested_by_id = %s)", block)
        self.assertIn("@app.route('/api/tasks/board_people', methods=['GET', 'OPTIONS'])", _read(APP_PATH))

    def test_board_selector_groups_people_by_department(self):
        src = _read(WORKSPACE_PATH)
        self.assertIn("groupLabel: person.department || 'Без отдела',", src)
        # Список приходит с сервера, а не собирается из загруженных карточек.
        start = src.index("  const boardPeople = useMemo(() => people")
        block = src[start:src.index("  const adminScopeOptions = useMemo(", start)]
        self.assertNotIn("effectiveTasks", block)
        select_src = _read(CUSTOM_SELECT_PATH)
        self.assertIn("const startsGroup = groupLabel && groupLabel !== (filtered[index - 1]?.groupLabel || '');", select_src)
        view = _read(TASKS_VIEW_PATH)
        self.assertIn("/api/tasks/board_people", view)
        self.assertIn("people={boardPeople}", view)

    def test_assigned_task_can_be_sent_back_to_backlog(self):
        src = _read(TASKS_VIEW_PATH)
        # Те же права, что и у переноса карточки мышью: постановщик или админ.
        self.assertIn("if ((isAdmin || isCreator) && s === 'assigned' && !task?.is_backlog)", src)
        self.assertIn("action: 'to_backlog', label: 'В бэклог'", src)
        self.assertIn("if (btn.action === 'to_backlog') { onMoveToBacklog?.(task); return; }", src)
        start = src.index("  const moveTaskToBacklog = useCallback(async (task) => {")
        block = src[start:src.index("  const openBacklogCreate = useCallback(", start)]
        self.assertIn("updateBoardItems([{ task_id: taskId, is_backlog: true }])", block)
        # В виджете закреплённой задачи планирование не показываем.
        self.assertIn("!['edit', 'delete', 'to_backlog'].includes(btn.action)", src)


class SkillTests(unittest.TestCase):
    def test_skill_describes_connection(self):
        src = _read(SKILL_PATH)
        self.assertIn("name: task-board", src)
        self.assertIn("scripts/task_board.py", src)
        self.assertIn("POST /api/tasks/board", src)
        self.assertIn("BACKLOG_ONLY_FOR_ASSIGNED", src)

    def test_skill_documents_reports(self):
        src = _read(SKILL_PATH)
        self.assertIn("task_reports", src)
        self.assertIn("/api/tasks/<id>/reports", src)
        self.assertIn("только автор", src)
        # Агенту прямо запрещено выдумывать трудозатраты.
        self.assertIn("Выдумывать трудозатраты нельзя", src)


if __name__ == "__main__":
    unittest.main()
