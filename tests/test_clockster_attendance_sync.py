import sys
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
APP_PATH = ROOT / "src" / "App.jsx"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import clockster_attendance_sync as cas  # noqa: E402


def _read(path):
    return path.read_text(encoding="utf-8-sig")


class ClocksterModuleTests(unittest.TestCase):
    """Модуль выгрузки clockster_attendance_sync (без сети и БД)."""

    def test_config_defaults(self):
        cfg = cas.get_config(env_file="__no_such_env__")
        self.assertEqual(cfg["api_url"].rstrip("/") or cas.DEFAULT_API_URL, cfg["api_url"] or cas.DEFAULT_API_URL)
        self.assertIn("token", cfg)
        self.assertIn("days_back", cfg)

    def test_parse_mark_datetime_normalizes_to_local(self):
        # Отметка с оффсетом +05:00 — локальное время сохраняется как есть (naive).
        parsed = cas._parse_mark_datetime("2026-07-22T09:01:30+05:00")
        self.assertEqual(parsed, datetime(2026, 7, 22, 9, 1, 30))
        # Иной оффсет конвертируется в местный пояс (+05).
        parsed_utc = cas._parse_mark_datetime("2026-07-22T04:01:30+00:00")
        self.assertEqual(parsed_utc, datetime(2026, 7, 22, 9, 1, 30))
        self.assertIsNone(cas._parse_mark_datetime(""))
        self.assertIsNone(cas._parse_mark_datetime("not-a-date"))

    def test_build_attendance_rows(self):
        marks = [
            {
                "user": {"id": 1, "last_name": "Ешан", "first_name": "Алмас", "middle_name": ""},
                "datetime": "2026-07-22T08:35:03+05:00",
                "status": 1,
                "source": "device",
            },
            {
                "user": {"id": 1, "last_name": "Ешан", "first_name": "Алмас"},
                "datetime": "2026-07-21T21:00:00+05:00",
                "status": "0",
                "source": "device",
            },
            # битые строки: без имени, без даты, без статуса — пропускаются
            {"user": {}, "datetime": "2026-07-22T08:00:00+05:00", "status": 1},
            {"user": {"last_name": "Тест", "first_name": "Тест"}, "datetime": "", "status": 1},
            {"user": {"last_name": "Тест", "first_name": "Тест"},
             "datetime": "2026-07-22T08:00:00+05:00", "status": None},
        ]
        rows = cas.build_attendance_rows(marks)
        self.assertEqual(len(rows), 2)
        # сортировка по времени внутри сотрудника
        self.assertEqual(rows[0]["event_at"], datetime(2026, 7, 21, 21, 0, 0))
        self.assertEqual(rows[0]["status"], 0)
        self.assertEqual(rows[1]["event_at"], datetime(2026, 7, 22, 8, 35, 3))
        self.assertEqual(rows[1]["status"], 1)
        self.assertEqual(rows[1]["employee_name"], "Ешан Алмас")
        self.assertEqual(rows[1]["clockster_user_id"], 1)

    def test_run_sync_skips_without_token(self):
        summary = cas.run_sync(lambda rows: {"ok": True}, config={"api_url": cas.DEFAULT_API_URL, "token": "", "days_back": 2})
        self.assertTrue(summary.get("skipped"))
        self.assertEqual(summary.get("reason"), "no_credentials")

    def test_default_date_range_format(self):
        start, end = cas.default_date_range(2)
        self.assertRegex(start, r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(end, r"^\d{4}-\d{2}-\d{2}$")
        self.assertLessEqual(start, end)


class ClocksterImporterSourceTests(unittest.TestCase):
    """Импортёр и обвязка в bot_schedule2.py (исходные ассерты)."""

    def setUp(self):
        self.src = _read(BOT_PATH)

    def test_importer_scopes_to_sales_department(self):
        self.assertIn("CLOCKSTER_SYNC_DEPARTMENT_CODE", self.src)
        self.assertIn("def _clockster_attendance_sync_importer(", self.src)
        func = self.src[self.src.index("def _clockster_attendance_sync_importer("):][:9000]
        # Матчинг ограничен сотрудниками отдела продаж — иначе replace-семантика
        # импорта стёрла бы Oktell-статусы СЗоВ, отметившихся в Clockster.
        self.assertIn("get_department_member_ids", func)
        self.assertIn("restrict_to_ids=member_ids", func)
        self.assertIn("_status_import_resolve_operator_matches", func)
        self.assertIn("db.save_operator_status_import", func)
        self.assertIn("'clockster'", func)

    def test_importer_applies_manual_overrides(self):
        # Терминал один на вход/выход — тип отметки правится руками; ре-синк
        # должен воспроизводить правки (delete/set_kind/add), а не откатывать их.
        func = self.src[self.src.index("def _clockster_attendance_sync_importer("):][:9000]
        self.assertIn("list_attendance_mark_overrides", func)
        self.assertIn("'delete'", func)
        self.assertIn("'set_kind'", func)
        self.assertIn("'add'", func)
        self.assertIn("'clockster-manual'", func)

    def test_importer_closes_open_marks(self):
        func = self.src[self.src.index("def _clockster_attendance_sync_importer("):][:9000]
        self.assertIn("db.normalize_attendance_marks", func)
        self.assertIn("auto_closed_marks", func)

    def test_mark_states_map_to_operator_profile(self):
        # Приход — рабочий статус операторского профиля, уход — офлайн.
        self.assertIn("CLOCKSTER_MARK_IN_STATE = 'Готов'", self.src)
        self.assertIn("CLOCKSTER_MARK_OUT_STATE = 'Выключен'", self.src)

    def test_manual_endpoint(self):
        self.assertIn("def sync_work_schedules_statuses_clockster():", self.src)
        endpoint = self.src[self.src.index("def sync_work_schedules_statuses_clockster():"):][:3500]
        self.assertIn("больше 10 дней", endpoint)
        self.assertIn("CLOCKSTER_API_TOKEN", endpoint)
        self.assertIn("STATUS_IMPORT_LOCK", endpoint)
        self.assertIn("clockster_attendance_sync.run_sync", endpoint)
        self.assertIn("_clockster_attendance_sync_importer", endpoint)

    def test_daily_job_registered(self):
        self.assertIn("clockster_attendance_sync_daily", self.src)
        self.assertIn("async def clockster_attendance_sync_job():", self.src)

    def test_attendance_marks_endpoints(self):
        # CRUD отметок для модалки таймлайна: список, добавление, смена типа, удаление.
        self.assertIn("def get_attendance_marks_endpoint():", self.src)
        self.assertIn("def add_attendance_mark_endpoint():", self.src)
        self.assertIn("def update_attendance_mark_endpoint(event_id):", self.src)
        self.assertIn("def delete_attendance_mark_endpoint(event_id):", self.src)
        # Общий гард: админ или СВ/глава отдела продаж; оператор — только из ОП.
        self.assertIn("def _attendance_marks_guard(operator_id):", self.src)
        guard = self.src[self.src.index("def _attendance_marks_guard(operator_id):"):][:2000]
        self.assertIn("_clockster_department_id()", guard)
        self.assertIn("только для отдела продаж", guard)
        # Граница отдела через scope-хелпер: глава ОП без своего department_id
        # должен проходить, глава чужого отдела — нет.
        self.assertIn("_is_global_admin_requester(role, requester_id)", guard)
        self.assertIn("_department_scope_id_for_requester(requester_id) != dept_id", guard)


class ClocksterMarkOverridesDbTests(unittest.TestCase):
    """Слой БД для ручных правок отметок (database.py, исходные ассерты)."""

    def setUp(self):
        self.src = (ROOT / "database.py").read_text(encoding="utf-8-sig")

    def test_overrides_table_and_constants(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS attendance_mark_overrides", self.src)
        self.assertIn("UNIQUE(operator_id, event_at)", self.src)
        self.assertIn("ATTENDANCE_MARK_IN_STATUS_KEY = 'готов'", self.src)
        self.assertIn("ATTENDANCE_MARK_OUT_STATUS_KEY = 'выключен'", self.src)
        self.assertIn("ATTENDANCE_MARK_MANUAL_NOTE = 'clockster-manual'", self.src)

    def test_crud_methods(self):
        self.assertIn("def list_attendance_mark_overrides(self, operator_ids, date_from, date_to):", self.src)
        self.assertIn("def get_attendance_marks(self, operator_id, day):", self.src)
        self.assertIn("def apply_attendance_mark_change(self, operator_id, action, event_id=None, event_at=None,", self.src)

    def test_apply_rebuilds_and_recalculates(self):
        func = self.src[self.src.index("def apply_attendance_mark_change("):][:7000]
        # Правка сразу перестраивает сегменты и пересчитывает часы за день±1.
        self.assertIn("_normalize_attendance_marks_tx", func)
        self.assertIn("_rebuild_operator_status_segments_tx", func)
        self.assertIn("_recalculate_auto_daily_hours_tx", func)
        # Ручная отметка: смена типа сохраняет 'add'-оверрайд, удаление — снимает его.
        self.assertIn("'add' if was_manual else 'set_kind'", func)
        self.assertIn("DELETE FROM attendance_mark_overrides", func)

    def test_auto_close_of_open_marks(self):
        # Приход без парной отметки ухода закрывается сам — иначе «готов» течёт
        # в следующие сутки и тянет несуществующие часы на чужие смены.
        self.assertIn("ATTENDANCE_MARK_AUTO_NOTE = 'clockster-auto'", self.src)
        self.assertIn("ATTENDANCE_MARK_MAX_OPEN_HOURS = 12", self.src)
        self.assertIn("def _normalize_attendance_marks_tx(self, cursor, operator_ids, date_from, date_to):", self.src)
        self.assertIn("def normalize_attendance_marks(self, operator_ids, date_from, date_to):", self.src)
        func = self.src[self.src.index("def _normalize_attendance_marks_tx("):][:5000]
        # Идемпотентность: прошлые авто-уходы удаляются и пересоздаются.
        self.assertIn("DELETE FROM operator_status_events", func)
        self.assertIn("FROM work_shifts", func)
        # Закрываем концом смены, охватывающей приход; иначе — по лимиту часов.
        self.assertIn("s['end_at'] > mark_at", func)
        self.assertIn("close_at = shift_end or (mark_at + max_open)", func)
        # Реальная следующая отметка раньше границы — не вмешиваемся.
        self.assertIn("next_mark['event_at'] <= close_at", func)
        # И отдельно: уход отмечен, но позже конца смены (переработка) — реальные
        # данные важнее авто-закрытия, иначе уход подменялся бы концом смены.
        self.assertIn("not next_mark['is_in'] and next_mark['event_at'] <= mark_at + max_open", func)

    def test_auto_marks_are_not_editable(self):
        # Авто-закрытие не правится руками: правильный путь — добавить свою
        # отметку ухода, тогда авто-закрытие исчезает при нормализации.
        func = self.src[self.src.index("def apply_attendance_mark_change("):][:7000]
        self.assertIn("ATTENDANCE_MARK_AUTO_NOTE", func)
        self.assertIn("автоматическое закрытие смены", func)


class ClocksterFrontendTests(unittest.TestCase):
    """Кнопка синка в планировщике графиков (App.jsx)."""

    def setUp(self):
        self.src = _read(APP_PATH)

    def test_sync_button_for_sales_planner(self):
        self.assertIn("sync_statuses_clockster", self.src)
        self.assertIn("syncPlannerClocksterStatuses", self.src)
        self.assertIn("Отметки Clockster", self.src)
        # Кнопка видна планировщику отдела продаж и админам.
        self.assertIn("(isOpPlanner || isAdminLikePlanner)", self.src)

    def test_op_planner_covers_department_head(self):
        # СВ и глава отдела ОП: у главы свой department_id может быть не проставлен,
        # поэтому учитываем и возглавляемый отдел.
        self.assertIn("plannerHeadedDepartmentCode", self.src)
        self.assertIn("plannerDepartmentCode === 'op' || plannerHeadedDepartmentCode === 'op'", self.src)

    def test_clipped_to_endpoint_limit(self):
        func = self.src[self.src.index("const syncPlannerClocksterStatuses = async ()"):][:3500]
        self.assertIn("CLOCKSTER_SYNC_MAX_DAYS", func)
        self.assertIn("slice(-CLOCKSTER_SYNC_MAX_DAYS)", func)

    def test_attendance_tab_in_day_modal(self):
        # Основное место правки — таб «Отметки» в модалке дня (панель таймлайна
        # рисует ту же секцию для контекста). Таб только у операторов ОП.
        self.assertIn("const modalShowAttendanceTab = modalShowTabs && canManageAttendanceMarks && attendanceMarks.available;", self.src)
        self.assertIn("{ key: 'attendance', label: 'Отметки', icon: 'fa-door-open' }", self.src)
        self.assertIn("modalTabAttendance && (", self.src)
        # Одна реализация секции на оба места.
        self.assertIn("const renderAttendanceMarksSection = ", self.src)
        self.assertEqual(self.src.count("renderAttendanceMarksSection"), 3)
        # Отметки грузятся при открытии модалки дня, а не только панели таймлайна.
        self.assertIn("!modalState.open || !modalState.opId || !modalState.date", self.src)
        # Пропал таб — не остаёмся на пустой вкладке, но только когда загрузка
        # завершена (иначе правка отметки выкидывала бы со вкладки).
        self.assertIn("modalActiveTab === 'attendance' && !modalShowAttendanceTab && !attendanceMarks.loading", self.src)

    def test_tab_survives_mark_edit(self):
        # Правка отметки перезапрашивает список; доступность секции при этом должна
        # сохраняться, иначе таб «Отметки» схлопывается и модалку кидает на «Смены».
        func = self.src[self.src.index("const fetchAttendanceMarks = async (opId, dateKey)"):][:2000]
        self.assertIn("available: prev.key === key ? prev.available : false", func)

    def test_attendance_marks_section_in_timeline_modal(self):
        # Секция отметок: список за день, смена типа (приход↔уход), добавление и
        # удаление — только для ОП/админов; та же секция и в панели таймлайна.
        self.assertIn("canManageAttendanceMarks", self.src)
        self.assertIn("Отметки за день", self.src)
        self.assertIn("const fetchAttendanceMarks = async (opId, dateKey)", self.src)
        self.assertIn("const toggleAttendanceMarkKind = (mark)", self.src)
        self.assertIn("const deleteAttendanceMark = (mark)", self.src)
        self.assertIn("const addAttendanceMark = ()", self.src)
        self.assertIn("/api/attendance_marks", self.src)

    def test_section_uses_project_ios_design_system(self):
        # Секция собрана из примитивов components/ui/ios, а не собственной вёрстки:
        # так таб выглядит как остальной сайт (macOS/iOS).
        self.assertIn("from './components/ui/ios'", self.src)
        func_start = self.src.index("const renderAttendanceMarksSection = ")
        func = self.src[func_start:func_start + 14000]
        for primitive in ("APPLE_FONT", "iosCard", "iosGroupLabel", "iosInput", "iosBtnPrimary", "IosBadge"):
            self.assertIn(primitive, func)
        # Сегментированный контрол типа вместо <select> и сгруппированный список.
        self.assertIn("divide-y divide-slate-100", func)
        self.assertIn("rounded-xl bg-slate-100 p-0.5", func)
        self.assertNotIn("<select", func)

    def test_long_hints_hidden_behind_info_button(self):
        # Пояснения свёрнуты под «ⓘ» — в списке важны сами отметки, а не текст.
        self.assertIn("const [attendanceHintOpen, setAttendanceHintOpen]", self.src)
        func_start = self.src.index("const renderAttendanceMarksSection = ")
        func = self.src[func_start:func_start + 14000]
        self.assertIn("setAttendanceHintOpen(v => !v)", func)
        self.assertIn("attendanceHintOpen && (", func)
        self.assertIn("fa-circle-info", func)
        # Тексты живут только внутри свёрнутого блока: снаружи их быть не должно.
        hint_start = func.index("attendanceHintOpen && (")
        hint_end = func.index("</div>\n                        )}", hint_start)
        hint_block = func[hint_start:hint_end]
        for phrase in ("один на вход и выход", "ночной синхронизации", "пересечение", "авто-закрытие исчезнет"):
            self.assertIn(phrase, hint_block)
            self.assertEqual(func.count(phrase), 1, f"{phrase!r} должен встречаться только в подсказке")

    def test_section_shown_only_after_successful_load(self):
        # Отдел ОПЕРАТОРА знает только бэкенд (в планировщике у оператора есть
        # направление, но не отдел), поэтому секция рендерится строго по факту 200 —
        # иначе админ видел бы её мелькание на операторах СЗоВ/ТЭЗ.
        self.assertIn("available: false });", self.src)
        func = self.src[self.src.index("const fetchAttendanceMarks = async (opId, dateKey)"):][:2000]
        # Новый оператор/дата — до ответа секции нет; успех — available: true.
        self.assertIn("available: prev.key === key ? prev.available : false", func)
        self.assertIn("loading: false, error: '', available: true", func)


if __name__ == "__main__":
    unittest.main()
