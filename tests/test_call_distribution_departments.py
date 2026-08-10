"""«Деление звонков» по отделам: скоуп, селектор отделов и движок Binotel.

Часть проверок исполняет реальные функции: они вытаскиваются из bot_schedule2.py
через AST и выполняются с подставленными зависимостями — импортировать модуль
целиком нельзя, он поднимает бота и планировщик (тот же приём, что в остальных
тестах этого каталога, но там ограничились сверкой исходника).
"""
import ast
import textwrap
import unittest
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DATABASE_PATH = ROOT / "database.py"
APP_PATH = ROOT / "src" / "App.jsx"


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


def _load_functions(names, namespace):
    """Выполняет перечисленные функции bot_schedule2.py в подготовленном namespace."""
    module = ast.parse(_read(BOT_PATH))
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<bot>", "exec"), namespace)
    missing = set(names) - set(namespace)
    if missing:
        raise AssertionError(f"не найдены функции: {sorted(missing)}")
    return namespace


SZOV = {"id": 1, "code": "szov", "name": "СЗоВ", "source": "oktell", "has_activity": True}
TEZ = {"id": 560, "code": "tez", "name": "Тез КЦ", "source": "binotel", "has_activity": True}
OP = {"id": 367, "code": "op", "name": "Отдел продаж", "source": None, "has_activity": True}
FRONT = {"id": 909, "code": "front_office", "name": "Фронт офисы", "source": None, "has_activity": False}
ALL_OPTIONS = [SZOV, TEZ, OP, FRONT]


class DepartmentResolutionTests(unittest.TestCase):
    """Кто какой отдел видит: админ переключается, глава и СВ прибиты к своему."""

    def _resolver(self, *, global_admin, own_department_id=None):
        ns = {
            "_is_global_admin_requester": lambda role, requester_id=None: global_admin,
            "_department_scope_id_for_requester": lambda requester_id: own_department_id,
            "_call_distribution_department_id_by_code": lambda code: 1 if code == "szov" else None,
            "OKTELL_CALL_DISTRIBUTION_DEPARTMENT_CODE": "szov",
        }
        _load_functions({"_call_distribution_resolve_department"}, ns)
        return ns["_call_distribution_resolve_department"]

    def test_admin_defaults_to_szov(self):
        resolve = self._resolver(global_admin=True)
        dept_id, options = resolve(1, "admin", None, list(ALL_OPTIONS))
        self.assertEqual(dept_id, 1)
        self.assertEqual([o["code"] for o in options], ["szov", "tez", "op"])

    def test_admin_switches_department(self):
        resolve = self._resolver(global_admin=True)
        dept_id, _ = resolve(1, "admin", "560", list(ALL_OPTIONS))
        self.assertEqual(dept_id, 560)

    def test_admin_selector_hides_departments_without_calls(self):
        """Фронт-офисы не слушают звонки: ни телефонии, ни оценок — в селектор не лезут."""
        resolve = self._resolver(global_admin=True)
        _dept_id, options = resolve(1, "admin", None, list(ALL_OPTIONS))
        self.assertNotIn("front_office", [o["code"] for o in options])

    def test_admin_falls_back_when_requested_department_is_hidden(self):
        resolve = self._resolver(global_admin=True)
        dept_id, _ = resolve(1, "admin", 909, list(ALL_OPTIONS))
        self.assertEqual(dept_id, 1)

    def test_head_is_pinned_to_own_department(self):
        """Чужой department_id в ссылке не открывает чужой отдел, а молча даёт свой."""
        resolve = self._resolver(global_admin=False, own_department_id=560)
        dept_id, options = resolve(42, "admin", 1, list(ALL_OPTIONS))
        self.assertEqual(dept_id, 560)
        self.assertEqual([o["code"] for o in options], ["tez"])

    def test_supervisor_sees_only_own_department(self):
        resolve = self._resolver(global_admin=False, own_department_id=367)
        dept_id, options = resolve(77, "sv", None, list(ALL_OPTIONS))
        self.assertEqual(dept_id, 367)
        self.assertEqual([o["id"] for o in options], [367])


class BinotelCandidateFilterTests(unittest.TestCase):
    """Отбор кандидатов Binotel: только отвеченные разговоры с записью."""

    def setUp(self):
        self.ns = _load_functions(
            {"_binotel_eval_call_is_recorded", "_binotel_eval_call_month", "_binotel_eval_month_days"},
            {"datetime": datetime, "calendar": __import__("calendar"), "dt_date": date,
             "ZoneInfo": __import__("zoneinfo").ZoneInfo},
        )

        class _Binotel:
            RECORDED_STATUSES = {"uploaded"}
            RECORDED_DISPOSITIONS = {"ANSWER", "ANSWERED", "SUCCESS", "VM-SUCCESS"}

        self.binotel = _Binotel

    def _recorded(self, **call):
        base = {"billsec": 60, "general_call_id": "1", "recording_status": "uploaded", "disposition": "ANSWER"}
        base.update(call)
        return self.ns["_binotel_eval_call_is_recorded"](base, self.binotel)

    def test_answered_call_with_upload_is_taken(self):
        self.assertTrue(self._recorded())

    def test_unanswered_call_is_dropped(self):
        self.assertFalse(self._recorded(billsec=0, disposition="CANCEL"))

    def test_call_without_recording_is_dropped(self):
        self.assertFalse(self._recorded(recording_status="pending"))

    def test_missing_recording_status_falls_back_to_disposition(self):
        self.assertTrue(self._recorded(recording_status="", disposition="ANSWER"))
        self.assertFalse(self._recorded(recording_status="", disposition="CANCEL"))

    def test_call_without_id_is_dropped(self):
        self.assertFalse(self._recorded(general_call_id=None))

    def test_call_month_parsed_from_panel_format(self):
        self.assertEqual(self.ns["_binotel_eval_call_month"]("05.08.2026 13:45:01"), "2026-08")
        self.assertIsNone(self.ns["_binotel_eval_call_month"]("не дата"))

    def test_past_month_is_scanned_whole(self):
        days = self.ns["_binotel_eval_month_days"]("2026-01")
        self.assertEqual(len(days), 31)
        self.assertEqual(days[0], date(2026, 1, 1))

    def test_future_days_are_not_requested(self):
        """Будущие дни — пустой ответ и лишняя секунда лимита Binotel."""
        current = datetime.now().strftime("%Y-%m")
        days = self.ns["_binotel_eval_month_days"](current)
        self.assertTrue(days)
        self.assertLessEqual(days[-1], date.today())


class CallDistributionBackendWiringTests(unittest.TestCase):
    def test_status_is_scoped_to_resolved_department(self):
        status = _function_source(BOT_PATH, "call_distribution_status")

        self.assertIn("_call_distribution_resolve_department(", status)
        self.assertIn("op_id in department_member_ids", status)
        self.assertIn('"departments": options', status)
        self.assertIn('"can_run": bool((is_admin or is_head) and source)', status)
        self.assertIn('"run_job": _call_distribution_job_snapshot(department_id)', status)
        # Экран больше не прибит к отделу Oktell.
        self.assertNotIn("oktell_member_ids", status)

    def test_run_dispatches_by_department_source(self):
        run = _function_source(BOT_PATH, "call_distribution_run")

        self.assertIn("_call_distribution_resolve_department(", run)
        self.assertIn("if source == 'binotel':", run)
        self.assertIn("_start_binotel_distribution_job(department_id, month, requester_id)", run)
        self.assertIn("sync_oktell_evaluation_calls(", run)
        self.assertIn("department_id=department_id", run)
        # Отдел без телефонии не запускает распределение молча «в никуда».
        self.assertIn("нет интеграции с телефонией", run)

    def test_binotel_engine_mirrors_oktell_norm_logic(self):
        worker = _function_source(BOT_PATH, "sync_binotel_evaluation_calls")

        self.assertIn("need = max(0, norm - (evaluated_real + pending))", worker)
        self.assertIn("db.get_operator_call_evaluation_targets_for_month(op_ids, mstr)", worker)
        self.assertIn("db.get_operator_score_aggregates_for_month(mstr, op_ids)", worker)
        self.assertIn("db.get_imported_call_keys_for_month(mstr)", worker)
        self.assertIn("BINOTEL_EVAL_SYNC_LOCK.acquire(blocking=False)", worker)
        # Норма закрыта -> месяц не стоит ни одного запроса к телефонии.
        self.assertIn("if not need_by_op:", worker)
        self.assertIn("client.list_calls_for_day(day)", worker)
        # Аудио-эндпоинт узнаёт источник по суффиксу notes (docstring там же требует).
        self.assertIn('notes=f"distribution:', worker)
        self.assertIn(':binotel"', worker)

    def test_binotel_distribution_runs_after_tez_status_sync(self):
        source = _read(BOT_PATH)
        self.assertIn("sync_binotel_evaluation_calls(triggered_by='scheduler-after-status')", source)

    def test_department_activity_helper_counts_journal_and_pool(self):
        helper = _function_source(
            DATABASE_PATH, "get_departments_with_call_evaluation_activity", class_name="Database")
        self.assertIn("FROM calls c", helper)
        self.assertIn("FROM imported_calls ic", helper)
        self.assertIn("c.is_draft = FALSE", helper)


class CallDistributionFrontendTests(unittest.TestCase):
    def test_screen_reads_department_meta(self):
        source = _read(APP_PATH)

        self.assertIn("setCdDepartments(Array.isArray(d.departments) ? d.departments : [])", source)
        self.assertIn("setCdCanRun(Boolean(d.can_run))", source)
        self.assertIn("setCdJob(d.run_job || null)", source)
        # Селектор — только когда отделов больше одного (у главы и СВ он один).
        self.assertIn("{cdDepartments.length > 1 && (", source)
        self.assertIn("changeCdDepartment(e.target.value)", source)

    def test_run_button_uses_shared_endpoint_and_source_gate(self):
        source = _read(APP_PATH)

        self.assertIn("/api/call_distribution/run", source)
        self.assertIn("{cdCanRun && (", source)
        self.assertIn("if (r.data?.status === 'started')", source)
        self.assertIn("const cdJobRunning = cdJob?.status === 'running';", source)


if __name__ == "__main__":
    unittest.main()
