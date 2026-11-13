# -*- coding: utf-8 -*-
"""Аукцион смен Тез КЦ (задача #124): готовые смены файлом и правила прогона.

Отделу Тез КЦ «Расчёт ресурсов» смены не генерирует — руководитель загружает тот же
Excel, что и в «Графики работы», а операторы ТП и ОП линий выбирают смены в аукционе.
Решения владельца 12.11.2026: имена и ставки строк файла не учитываются — берутся сами
смены; норма часов как в СЗоВ (40/30/20 ч по ставке, остаток — «Режимом добора»);
клетка «11*15 18*20» — две отдельные смены, поэтому в день можно взять несколько смен
без пересечения.

Живой прогон на настоящем Postgres (копия стендовой базы, настоящие методы Database и
блюпринт через Flask test client) делался отдельно: загрузка → сверка → запись →
запуск → взятие смен → повторная загрузка → публикация в графики, плюс права по
отделам. Здесь — разбор файла на настоящих функциях импорта графиков и инварианты в
исходниках, чтобы правка не сняла их молча. Люди и даты выдуманы.
"""
import ast
import re
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

from shift_auction.upload import ShiftAuctionUploadError, parse_shift_auction_upload
from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
DATABASE_SOURCE = source_cache.database_flat_path().read_text(encoding="utf-8-sig")
DATABASE_MODULE = source_cache.parse(DATABASE_SOURCE)
BOT_SOURCE = source_cache.monolith_flat_path().read_text(encoding="utf-8-sig")
ROUTES_SOURCE = (ROOT / "shift_auction" / "routes.py").read_text(encoding="utf-8")
VIEW_SOURCE = (ROOT / "src" / "components" / "resources" / "ShiftAuctionView.jsx").read_text(encoding="utf-8")
SHEET_SOURCE = (ROOT / "src" / "components" / "resources" / "ShiftAuctionUploadSheet.jsx").read_text(encoding="utf-8")
DEPARTMENT_VIEWS_SOURCE = (ROOT / "src" / "utils" / "departmentViews.js").read_text(encoding="utf-8")
APP_SOURCE = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8")


def _bot_parsers():
    """Настоящий разбор клеток импорта графиков — тот же, что получает блюпринт."""
    tree = source_cache.parse(BOT_SOURCE)
    body = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(target, "id", "") == "WORK_SCHEDULE_EXCEL_SHIFT_RE" for target in node.targets):
            body.append(node)
        if isinstance(node, ast.FunctionDef) and node.name in (
                "_parse_schedule_excel_header_date", "_parse_schedule_excel_cell_value"):
            body.append(node)
    namespace = {"re": re, "datetime": datetime, "dt_date": date}
    exec(compile(ast.Module(body=body, type_ignores=[]), "<bot_parsers>", "exec"), namespace)
    return namespace["_parse_schedule_excel_header_date"], namespace["_parse_schedule_excel_cell_value"]


PARSE_HEADER, PARSE_CELL = _bot_parsers()
MONDAY = date(2030, 11, 18)


def _workbook(rows, header_row=1, extra=None, monday=MONDAY):
    """Файл вида «График ТП»: ФИО, Ставка, семь дат; люди выдуманы."""
    book = Workbook()
    sheet = book.active
    if header_row > 1:
        sheet.cell(1, 1, "График техподдержки")
    sheet.cell(header_row, 1, "ФИО")
    sheet.cell(header_row, 2, "Ставка")
    for index in range(7):
        sheet.cell(header_row, 3 + index, datetime.combine(monday + timedelta(days=index), datetime.min.time()))
    for offset, (rate, cells) in enumerate(rows, start=1):
        sheet.cell(header_row + offset, 1, f"Сотрудник {offset}")
        sheet.cell(header_row + offset, 2, rate)
        for index, value in enumerate(cells):
            sheet.cell(header_row + offset, 3 + index, value)
    for (row, column), value in (extra or {}).items():
        sheet.cell(row, column, value)
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _parse(data):
    return parse_shift_auction_upload(data, PARSE_HEADER, PARSE_CELL)


class UploadParsingTests(unittest.TestCase):
    """Каждая смена клетки — смена аукциона; имена и ставки не учитываются."""

    def test_every_cell_shift_becomes_a_shift_and_names_are_ignored(self):
        parsed = _parse(_workbook([
            (0.75, ["11*15 18*20", "21*09", "Выходной", "13*19/30", "", "9*18", "выходной"]),
            (1, ["9*19", "9*19", "9*19", "Выходной", "Выходной", "12*21", "12*21"]),
        ]))
        self.assertEqual(parsed["header_row"], 1)
        self.assertEqual(len(parsed["dates"]), 7)
        self.assertEqual(parsed["day_off_cells"], 4)
        self.assertEqual(parsed["invalid_cells"], [])
        monday = MONDAY.isoformat()
        tuesday = (MONDAY + timedelta(days=1)).isoformat()
        thursday = (MONDAY + timedelta(days=3)).isoformat()
        shifts = [(s["date"], s["start"], s["end"]) for s in parsed["shifts"]]
        # «11*15 18*20» — две отдельные смены (решение владельца 12.11.2026).
        self.assertIn((monday, "11:00", "15:00"), shifts)
        self.assertIn((monday, "18:00", "20:00"), shifts)
        # Ночь «21*09» и «13*19/30» — как у импорта графиков.
        self.assertIn((tuesday, "21:00", "09:00"), shifts)
        self.assertIn((thursday, "13:00", "19:30"), shifts)
        # 5 смен в первой строке («11*15 18*20» — две) и 5 во второй.
        self.assertEqual(len(shifts), 10)
        # Ни имени, ни ставки в результате нет — только смены и адрес клетки.
        self.assertEqual(set(parsed["shifts"][0]), {"date", "start", "end", "cell"})
        self.assertNotIn("Сотрудник", repr(parsed))

    def test_unrecognized_cells_are_listed_not_dropped(self):
        parsed = _parse(_workbook(
            [(0.5, ["9*13", "утро", "", "", "", "", ""])],
            extra={(3, 1): "Итого", (3, 3): 40, (3, 4): "9*9"},
        ))
        cells = {item["cell"]: item for item in parsed["invalid_cells"]}
        self.assertEqual(set(cells), {"D2", "C3", "D3"})
        self.assertEqual(cells["D2"]["value"], "утро")
        self.assertIn("совпадают", cells["D3"]["error"])
        self.assertEqual([(s["date"], s["start"]) for s in parsed["shifts"]], [(MONDAY.isoformat(), "09:00")])

    def test_header_row_may_sit_below_a_title(self):
        parsed = _parse(_workbook([(1, ["9*18", "", "", "", "", "", ""])], header_row=2))
        self.assertEqual(parsed["header_row"], 2)
        self.assertEqual(len(parsed["shifts"]), 1)

    def test_broken_files_get_human_errors(self):
        for data, code in ((b"", "AUCTION_UPLOAD_FILE_EMPTY"), (b"not an excel", "AUCTION_UPLOAD_UNREADABLE")):
            with self.subTest(code=code):
                with self.assertRaises(ShiftAuctionUploadError) as caught:
                    _parse(data)
                self.assertEqual(caught.exception.code, code)
                self.assertTrue(caught.exception.message)
        book = Workbook()
        book.active.cell(1, 1, "ФИО")
        buffer = BytesIO()
        book.save(buffer)
        with self.assertRaises(ShiftAuctionUploadError) as caught:
            _parse(buffer.getvalue())
        self.assertEqual(caught.exception.code, "AUCTION_UPLOAD_NO_DATES")


_REGISTRY_CONSTANTS = (
    "SHIFT_AUCTION_DIRECTION_NAME",
    "SHIFT_AUCTION_DEPARTMENT_CODE",
    "SHIFT_AUCTION_MODE_LINE",
    "SHIFT_AUCTION_MODE_CHAT",
    "SHIFT_AUCTION_MODE_TEZ_TP",
    "SHIFT_AUCTION_MODE_TEZ_OP",
    "SHIFT_AUCTION_MODES",
    "SHIFT_AUCTION_CHAT_DIRECTION_PATTERN",
    "SHIFT_AUCTION_TEZ_DEPARTMENT_CODE",
    "SHIFT_AUCTION_MODE_DEPARTMENT_CODE",
    "SHIFT_AUCTION_MODE_DIRECTION_NAME",
    "SHIFT_AUCTION_MODE_LABELS",
    "SHIFT_AUCTION_UPLOAD_MODES",
    "SHIFT_AUCTION_SEVERAL_SHIFTS_PER_DAY_MODES",
    "SHIFT_AUCTION_NIGHT_SHIFTS",
    "SHIFT_AUCTION_DEPARTMENT_LABELS",
    "SHIFT_AUCTION_SETTINGS_ROW_ID",
)


def _registry():
    namespace = {"re": re}
    for node in DATABASE_MODULE.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in _REGISTRY_CONSTANTS for target in node.targets):
            exec(ast.get_source_segment(DATABASE_SOURCE, node), namespace)
    for node in DATABASE_MODULE.body:
        if isinstance(node, ast.FunctionDef) and node.name in (
                "_normalize_shift_auction_scope_value", "normalize_shift_auction_mode",
                "shift_auction_modes_for_departments", "shift_auction_night_shifts",
                "shift_auction_direction_rules"):
            exec(ast.get_source_segment(DATABASE_SOURCE, node), namespace)
    return namespace


class DirectionRegistryTests(unittest.TestCase):
    """Каталог прогонов: Тез КЦ — две линии со своими правилами, СЗоВ не сдвинут."""

    def setUp(self):
        self.ns = _registry()

    def test_rules_per_direction(self):
        rules = self.ns["shift_auction_direction_rules"]
        line, chat, tp, op = rules("line"), rules("chat"), rules("tez_tp"), rules("tez_op")
        # СЗоВ как было: линия — смены плана, целиком, одна в день; чат — части и несколько в день.
        self.assertEqual((line["shift_source"], line["partial_claims"], line["several_shifts_per_day"]), ("plan", False, False))
        self.assertEqual((chat["shift_source"], chat["partial_claims"], chat["several_shifts_per_day"]), ("plan", True, True))
        # Тез КЦ: смены из файла, целиком, несколько в день без пересечения.
        for item, label in ((tp, "ТП линия"), (op, "ОП линия")):
            self.assertEqual(item["label"], label)
            self.assertEqual(item["department_code"], "tez")
            self.assertEqual((item["shift_source"], item["partial_claims"], item["several_shifts_per_day"]), ("upload", False, True))
            self.assertIn({"start": "21:00", "end": "09:00"}, item["night_shifts"])
        # Ночь СЗоВ — ровно «20*08» (решение 29.08.2026): «21*09» туда не добавлена.
        self.assertEqual(line["night_shifts"], [{"start": "20:00", "end": "08:00"}])
        self.assertEqual(chat["night_shifts"], [{"start": "20:00", "end": "08:00"}])

    def test_modes_follow_the_department(self):
        modes = self.ns["shift_auction_modes_for_departments"]
        self.assertEqual(modes(["tez"]), ("tez_tp", "tez_op"))
        self.assertEqual(modes(["SZOV"]), ("line", "chat"))
        self.assertEqual(modes(["szov", "tez"]), ("line", "chat", "tez_tp", "tez_op"))
        self.assertEqual(modes(["op", "", None]), ())


def _night_helpers():
    tree = source_cache.parse(DATABASE_SOURCE)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Database")
    wanted = {"_shift_auction_hhmm", "_is_shift_auction_night", "_shift_auction_adjacent_night_date"}
    consts = [n for n in cls.body if isinstance(n, ast.Assign)
              and any(getattr(t, "id", "").startswith("SHIFT_AUCTION_NIGHT") for t in n.targets)]
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    module = ast.Module(body=[ast.ClassDef(name="D", bases=[], keywords=[], body=consts + methods,
                                           decorator_list=[])], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"datetime": datetime, "timedelta": timedelta,
                 "shift_auction_night_shifts": _registry()["shift_auction_night_shifts"]}
    exec(compile(module, "<night>", "exec"), namespace)
    return namespace["D"]


class TezNightRuleTests(unittest.TestCase):
    """Две ночи подряд нельзя и у Теза — его ночь «21*09»; у СЗоВ ночь прежняя."""

    def setUp(self):
        self.D = _night_helpers()

    def test_night_definition_depends_on_the_direction(self):
        self.assertTrue(self.D._is_shift_auction_night("21:00", "09:00", "tez_tp"))
        self.assertTrue(self.D._is_shift_auction_night("20:00", "08:00", "tez_op"))
        self.assertFalse(self.D._is_shift_auction_night("21:00", "09:00", "line"))
        self.assertFalse(self.D._is_shift_auction_night("21:00", "09:00"))
        self.assertTrue(self.D._is_shift_auction_night("20:00", "08:00"))

    def test_adjacent_tez_nights_are_blocked_only_in_tez(self):
        claimed = [{"shift_date": "2030-11-18", "start_time": "21:00", "end_time": "09:00"}]
        self.assertEqual(
            self.D._shift_auction_adjacent_night_date(claimed, "2030-11-19", "21:00", "09:00", direction_mode="tez_tp"),
            "2030-11-18")
        self.assertIsNone(self.D._shift_auction_adjacent_night_date(claimed, "2030-11-19", "21:00", "09:00"))
        self.assertIsNone(
            self.D._shift_auction_adjacent_night_date(claimed, "2030-11-20", "21:00", "09:00", direction_mode="tez_tp"))


def _method(name):
    start = DATABASE_SOURCE.index(f"    def {name}(")
    end = DATABASE_SOURCE.find("\n    def ", start + 10)
    return DATABASE_SOURCE[start:end if end > 0 else None]


class ServerWiringTests(unittest.TestCase):
    """Где правила обязаны быть подключены."""

    def test_every_night_check_names_the_direction(self):
        marker = "if self._shift_auction_adjacent_night_date("
        call_sites = []
        start = DATABASE_SOURCE.find(marker)
        while start >= 0:
            call_sites.append(DATABASE_SOURCE[start:DATABASE_SOURCE.index('raise ValueError("NIGHT_SHIFT_ALREADY_ADJACENT")', start)])
            start = DATABASE_SOURCE.find(marker, start + len(marker))
        # Взятие, «свой график», добор лота и добор смены плана.
        self.assertEqual(len(call_sites), 4)
        for call in call_sites:
            with self.subTest(call=call[:80]):
                self.assertIn("direction_mode=mode", call)

    def test_settings_event_goes_to_its_own_direction(self):
        body = _method("update_shift_auction_test_access")
        event = body[body.index('"settings_updated"'):]
        self.assertIn("}, direction_mode=mode)", event[:event.index("_invalidate_shift_auction_runtime_caches")])

    def test_restart_wipes_only_its_own_day_off_journal(self):
        body = _method("restart_shift_auction_test")
        chunk = body[body.index("event_type IN ('day_off_selected', 'day_off_removed')"):]
        chunk = chunk[:chunk.index('""", (')]
        self.assertIn("COALESCE(direction_mode, 'line') = %s", chunk)

    def test_admin_assign_stays_inside_the_operator_direction(self):
        body = _method("admin_claim_shift_for_operator")
        self.assertIn("AND COALESCE(direction_mode, 'line') = %s", body)
        self.assertIn("if self._shift_auction_mode_for_plan_tx(cursor, resolved_plan_id) != mode:", body)

    def test_upload_writes_plan_and_lots_in_one_transaction(self):
        body = _method("save_shift_auction_upload")
        self.assertIn("lock=True", body)
        self.assertIn("raise ValueError(state[\"blocked_code\"])", body)
        self.assertIn('raise ValueError("AUCTION_UPLOAD_CHANGED")', body)
        self.assertIn("self._save_resource_saved_schedule_tx(", body)
        # Совпавший файл не пишется вовсе.
        self.assertIn('if state["is_unchanged"]:', body)
        # Один курсор на всё: план и пересев лотов под одной транзакцией.
        self.assertEqual(body.count("with self._get_cursor() as cursor:"), 1)

    def test_reseed_cannot_swallow_a_concurrent_claim(self):
        """Пересев активного периода: лоты блокируются ДО подсчёта выбора, а стираются
        только заблокированные — взятие между сверкой и пересевом не пропадёт молча."""
        state = _method("_shift_auction_upload_state_tx")
        lock = state.index("SELECT id FROM shift_auction_test_lots WHERE COALESCE(direction_mode, 'line') = %s FOR UPDATE")
        count = state.index("(status = 'claimed' OR claimed_by IS NOT NULL)")
        self.assertLess(lock, count)
        save = _method("save_shift_auction_upload")
        self.assertIn('"DELETE FROM shift_auction_test_lots WHERE id = ANY(%s)"', save)
        self.assertNotIn("DELETE FROM shift_auction_test_lots WHERE COALESCE(direction_mode, 'line') = %s", save)

    def test_past_days_never_enter_the_auction(self):
        """Публикация переписывает каждый день периода: прошедшие дни файла (по сегодняшний)
        в аукцион не попадают, иначе итоги стёрли бы уже отработанные смены."""
        state = _method("_shift_auction_upload_state_tx")
        self.assertEqual(state.count("if day <= today:"), 2)  # и шапка дат, и смены
        self.assertIn('"skipped_past": {', state)
        self.assertLess(state.index("today = self._almaty_now().date()"), state.index("days = {}"))
        self.assertIn("preview.skipped_past", SHEET_SOURCE)

    def test_upload_refuses_periods_people_already_use(self):
        body = _method("_shift_auction_upload_state_tx")
        for code in ("AUCTION_UPLOAD_PERIOD_PUBLISHED", "AUCTION_UPLOAD_PERIOD_CLAIMED", "AUCTION_UPLOAD_PERIOD_PAST",
                     "AUCTION_UPLOAD_EMPTY", "AUCTION_UPLOAD_NOT_SUPPORTED"):
            with self.subTest(code=code):
                self.assertIn(code, body)
        self.assertIn("shift_auction_historical_claims WHERE plan_id = %s", body)
        self.assertIn("shift_auction_published_periods WHERE plan_id = %s", body)

    def test_night_shift_keeps_its_morning_hour(self):
        """«21*09» не режется до 08:00: потолок плана — сутки от начала смены."""
        body = _method("_normalize_resource_saved_schedule_shifts")
        self.assertIn("min(end_minute, start_minute + 24 * 60)", body)
        self.assertNotIn("min(end_minute, 32 * 60)", body)

    def test_post_claim_notice_goes_to_the_direction_managers(self):
        body = _method("get_admin_post_claim_notify_recipients")
        self.assertIn("shift_auction_modes_for_departments(", body)
        self.assertIn("if mode not in own_modes:", body)


class RouteWiringTests(unittest.TestCase):
    def _function(self, name):
        start = ROUTES_SOURCE.index(f"    def {name}(")
        end = ROUTES_SOURCE.index("\n    def ", start + 10)
        return ROUTES_SOURCE[start:end]

    def test_staff_scope_by_department(self):
        staff = self._function("_shift_auction_staff_modes")
        self.assertIn("if role == 'super_admin':", staff)
        self.assertIn("shift_auction_modes_for_departments(_shift_auction_staff_department_codes(requester_id))", staff)
        manager = self._function("_is_shift_auction_manager")
        self.assertIn("if mode not in _shift_auction_staff_modes(requester_id, role):", manager)
        self.assertIn("SHIFT_AUCTION_MODE_DEPARTMENT_CODE[mode] in _shift_auction_staff_department_codes(requester_id)", manager)

    def test_every_manager_check_names_the_direction(self):
        calls = re.findall(r"_is_shift_auction_manager\(([^)]*)\)", ROUTES_SOURCE)
        uses = [args for args in calls if not args.startswith("requester_id, requester_role, direction_mode=None")]
        self.assertGreaterEqual(len(uses), 9)
        for args in uses:
            with self.subTest(args=args):
                self.assertEqual(len([part for part in args.split(",") if part.strip()]), 3)

    def test_upload_route_guards(self):
        start = ROUTES_SOURCE.index("def api_shift_auction_upload_shifts")
        route = ROUTES_SOURCE[start:ROUTES_SOURCE.index("@bp.route", start)]
        self.assertIn("_is_shift_auction_manager(requester_id, requester_role, direction_mode)", route)
        self.assertIn("if direction_mode not in SHIFT_AUCTION_UPLOAD_MODES:", route)
        self.assertIn("AUCTION_UPLOAD_PREVIEW_REQUIRED", route)
        self.assertIn("SHIFT_AUCTION_UPLOAD_MAX_BYTES + 1", route)

    def test_admin_lot_actions_stay_in_own_runs(self):
        for marker in ("db.shift_auction_mode_for_lot(", "db.shift_auction_mode_for_operator(operator_id, include_pilot=True)"):
            with self.subTest(marker=marker):
                start = ROUTES_SOURCE.index(marker)
                self.assertIn("_shift_auction_staff_modes(requester_id, requester_role)", ROUTES_SOURCE[start:start + 600])

    def test_parsers_reach_the_blueprint_lazily(self):
        """Разбор клеток объявлен в монолите ниже сборки блюпринта — только обёрткой."""
        self.assertIn("_parse_schedule_excel_cell_value=lambda raw_value: _parse_schedule_excel_cell_value(raw_value)", BOT_SOURCE)
        self.assertIn("_parse_schedule_excel_header_date=lambda cell: _parse_schedule_excel_header_date(cell)", BOT_SOURCE)


class FrontendWiringTests(unittest.TestCase):
    def test_toggle_and_rules_come_from_the_server(self):
        self.assertIn("normalizeAuctionDirectionCatalog(safe.available_directions)", VIEW_SOURCE)
        # Тумблер — направления выбранного отдела; у кого отдел один (СЗоВ), это весь каталог.
        self.assertIn("options={departmentDirections.map((item) => ({ value: item.key, label: item.label }))}", VIEW_SOURCE)
        self.assertIn("{departmentDirections.map((item) => {", VIEW_SOURCE)
        self.assertIn("const isUploadDirection = directionRules.shift_source === 'upload';", VIEW_SOURCE)
        self.assertIn("isAuctionNightShift(lot.start_time, lot.end_time, directionNightShifts)", VIEW_SOURCE)
        # Руководитель Теза с «Линией» из прошлого сеанса переезжает на свой прогон.
        self.assertIn("setDirection((current) => (allowedKeys.includes(current) ? current : serverDirection));", VIEW_SOURCE)

    def test_upload_is_offered_only_where_shifts_come_from_a_file(self):
        self.assertIn("{isUploadDirection ? (", VIEW_SOURCE)
        self.assertIn("canManage && !isUploadDirection && typeof onOpenResourceGeneration === 'function'", VIEW_SOURCE)
        self.assertIn("open={isUploadOpen && isUploadDirection}", VIEW_SOURCE)
        self.assertIn("uploadDirection={isUploadDirection}", VIEW_SOURCE)

    def test_sheet_confirms_exactly_what_was_reconciled(self):
        self.assertIn("api/shift_auction/upload_shifts", SHEET_SOURCE)
        self.assertIn("base_fingerprint: preview.base_fingerprint || '',", SHEET_SOURCE)
        self.assertIn("confirm: '1',", SHEET_SOURCE)
        self.assertIn("const canConfirm = Boolean(preview && !blocked && !unchanged && preview.shift_count > 0 && !busy);", SHEET_SOURCE)
        # Пояснение формата — под «i», а не строкой в окне.
        self.assertIn("<InfoHint", SHEET_SOURCE)

    def test_tez_people_do_not_get_the_section_during_the_pilot(self):
        """Пилот (12.11.2026): аукцион Теза пока только у суперадминов — меню Теза прежнее."""
        operator = DEPARTMENT_VIEWS_SOURCE[DEPARTMENT_VIEWS_SOURCE.index("const TEZ_OPERATOR_VIEWS = "):]
        operator = operator[:operator.index("];")]
        manager = DEPARTMENT_VIEWS_SOURCE[DEPARTMENT_VIEWS_SOURCE.index("const TEZ_MANAGER_VIEWS = ["):]
        manager = manager[:manager.index("];")]
        self.assertNotIn("'shift_auction'", operator)
        self.assertNotIn("'shift_auction'", manager)
        # Суперадмину с выбранным в меню «Тез КЦ» пункт нужен на месте.
        self.assertIn("shift_auction: ['szov', 'tez'],", APP_SOURCE)



# ---- Поведение сверки и записи на настоящем ShiftAuctionMixin (разбор 12.11.2026) ----
#
# Тексты исходника ловят не всё: смысловая порча, оставляющая нужную строку на месте,
# прошла бы. Здесь настоящие методы сверки и записи гоняются на подставном курсоре —
# без базы, ответы по подстроке SQL.

class _Cursor:
    def __init__(self, plan_row=None, existing=(), active_plan=None, active_dates=None):
        self.plan_row = plan_row
        self.existing = list(existing)
        self.active_plan = active_plan
        self.active_dates = active_dates
        self.sql = []
        self._last = ""

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self._last = sql

    def fetchone(self):
        sql = self._last
        if "FROM directions d" in sql:
            return (7, "ТП линия")
        if "FROM shift_auction_test_access s" in sql:
            return (self.active_plan,)
        if "FROM resource_saved_schedule_plans p" in sql:
            return self.plan_row
        if "SELECT date_from, date_to FROM resource_saved_schedule_plans WHERE id = %s" in sql:
            return self.active_dates
        if "shift_auction_published_periods" in sql:
            return None
        if "COUNT(*)::int FROM shift_auction_historical_claims" in sql:
            return (0,)
        return None

    def fetchall(self):
        if "FROM resource_saved_schedule_shifts" in self._last:
            return self.existing
        return []


def _mixin(cursor, today=date(2030, 11, 1)):
    from db.shift_auction import ShiftAuctionMixin

    class _Db(ShiftAuctionMixin):
        @contextmanager
        def _get_cursor(self):
            yield cursor

        def _get_work_schedule_break_rules_for_direction_tx(self, cursor_, name):
            return []

        def _compute_auto_shift_breaks_minutes(self, *args, **kwargs):
            return []

        def _almaty_now(self):
            return datetime.combine(today, datetime.min.time()).replace(hour=12)

    return _Db()


PARSED = {
    "dates": ["2030-11-18", "2030-11-19"],
    "shifts": [
        {"date": "2030-11-18", "start": "09:00", "end": "18:00", "cell": "C2"},
        {"date": "2030-11-19", "start": "21:00", "end": "09:00", "cell": "D2"},
    ],
}


class UploadReconcileBehaviourTests(unittest.TestCase):
    def test_first_upload_marks_nothing_as_added(self):
        """Первая загрузка периода — сравнивать не с чем: «добавится» на каждой смене = шум."""
        cursor = _Cursor()
        state = _mixin(cursor)._shift_auction_upload_state_tx(cursor, "tez_tp", PARSED)
        self.assertIsNone(state["changes"])
        self.assertEqual([day["added"] for day in state["days"]], [[], []])
        self.assertEqual(state["shift_count"], 2)

    def test_reupload_marks_only_the_new_shift(self):
        cursor = _Cursor(plan_row=(5, {}, None, ""), existing=[(date(2030, 11, 18), 540, 1080)])
        state = _mixin(cursor)._shift_auction_upload_state_tx(cursor, "tez_tp", PARSED)
        self.assertEqual(state["changes"], {"added": 1, "removed": 0, "unchanged": 1})
        self.assertEqual(state["days"][1]["added"], [{"start": "21:00", "end": "09:00"}])
        self.assertEqual(state["days"][0]["added"], [])

    def test_past_days_are_skipped_not_auctioned(self):
        cursor = _Cursor()
        state = _mixin(cursor, today=date(2030, 11, 18))._shift_auction_upload_state_tx(cursor, "tez_tp", PARSED)
        self.assertEqual(state["skipped_past"], {"shift_count": 1, "date_from": "2030-11-18", "date_to": "2030-11-18"})
        self.assertEqual([day["date"] for day in state["days"]], ["2030-11-19"])
        self.assertIsNone(state["blocked_code"])
        cursor = _Cursor()
        state = _mixin(cursor, today=date(2030, 11, 19))._shift_auction_upload_state_tx(cursor, "tez_tp", PARSED)
        self.assertEqual(state["blocked_code"], "AUCTION_UPLOAD_PERIOD_PAST")

    def test_overlap_with_the_running_period_is_reported(self):
        cursor = _Cursor(active_plan=9, active_dates=(date(2030, 11, 16), date(2030, 11, 22)))
        state = _mixin(cursor)._shift_auction_upload_state_tx(cursor, "tez_tp", PARSED)
        self.assertEqual(state["overlaps_active"], {"date_from": "2030-11-16", "date_to": "2030-11-22"})
        cursor = _Cursor(active_plan=9, active_dates=(date(2030, 11, 23), date(2030, 11, 29)))
        state = _mixin(cursor)._shift_auction_upload_state_tx(cursor, "tez_tp", PARSED)
        self.assertIsNone(state["overlaps_active"])


class UploadConfirmBehaviourTests(unittest.TestCase):
    def test_stale_reconciliation_is_refused_before_any_write(self):
        cursor = _Cursor()
        with self.assertRaises(ValueError) as caught:
            _mixin(cursor).save_shift_auction_upload(
                "tez_tp", PARSED, updated_by=1, base_fingerprint="stale0000000000000000000")
        self.assertEqual(str(caught.exception), "AUCTION_UPLOAD_CHANGED")
        self.assertFalse(any(
            sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in cursor.sql))

    def test_blocked_period_is_refused(self):
        cursor = _Cursor()
        with self.assertRaises(ValueError) as caught:
            _mixin(cursor).save_shift_auction_upload("line", PARSED, updated_by=1, base_fingerprint="x")
        self.assertEqual(str(caught.exception), "AUCTION_UPLOAD_NOT_SUPPORTED")


class ReviewFixesWiringTests(unittest.TestCase):
    """Находки разбора 12.11.2026 — где они закрыты."""

    def test_mutating_routes_refuse_a_foreign_direction(self):
        self.assertEqual(ROUTES_SOURCE.count("strict=True,"), 9)
        resolver = ROUTES_SOURCE[ROUTES_SOURCE.index("def _resolve_shift_auction_direction"):]
        resolver = resolver[:resolver.index("\n    def ")]
        self.assertIn('if strict and requested not in (None, \'\'):', resolver)
        self.assertIn('raise ValueError("DIRECTION_NOT_ALLOWED")', resolver)
        self.assertIn('"DIRECTION_NOT_ALLOWED": (', ROUTES_SOURCE)

    def test_operator_outside_every_run_is_checked_as_the_line(self):
        """Человеку вне прогонов метод подставляет линию (так было у СЗоВ) — ручка сверяет
        права как на линию: руководитель одного Теза через него смену СЗоВ не назначит."""
        self.assertIn("if operator_mode not in _shift_auction_staff_modes(requester_id, requester_role):", ROUTES_SOURCE)

    def test_self_schedule_answers_with_the_operator_run(self):
        start = ROUTES_SOURCE.index("def api_shift_auction_self_schedule")
        route = ROUTES_SOURCE[start:ROUTES_SOURCE.index("@bp.route", start)]
        self.assertIn("direction_mode=db.shift_auction_mode_for_operator(requester_id) or SHIFT_AUCTION_MODE_LINE", route)

    def test_confirm_writes_exactly_the_reconciled_file(self):
        start = ROUTES_SOURCE.index("def api_shift_auction_upload_shifts")
        route = ROUTES_SOURCE[start:ROUTES_SOURCE.index("@bp.route", start)]
        self.assertIn("if previewed_sha256 != file_sha256:", route)
        self.assertIn("AUCTION_UPLOAD_FILE_CHANGED", route)
        self.assertIn("file_sha256: preview.file_sha256 || ''", SHEET_SOURCE)
        # Отказ «поменялось» — сразу новая сверка тем же файлом, а не мёртвая кнопка.
        self.assertIn("code === 'AUCTION_UPLOAD_CHANGED' || code === 'AUCTION_UPLOAD_FILE_CHANGED'", SHEET_SOURCE)
        self.assertIn("sizeKey={preview ? 'preview' : 'pick'}", SHEET_SOURCE)

    def test_generation_link_is_hidden_on_phone_too(self):
        self.assertIn("if (canManage && !isUploadDirection && typeof onOpenResourceGeneration === 'function') {", VIEW_SOURCE)
        self.assertIn("canManage && !isUploadDirection && typeof onOpenResourceGeneration === 'function' ? (", VIEW_SOURCE)

    def test_requested_direction_is_applied_once(self):
        self.assertIn("if (appliedRequestedDirectionKeyRef.current === requestedDirectionKey) return;", VIEW_SOURCE)
        self.assertIn("handleSwitchDirectionRef.current(requestedDirection);", VIEW_SOURCE)
        self.assertIn("if (!matchedPeriod?.id && requestedDirectionIsForeign) {", VIEW_SOURCE)

    def test_tez_instructions_do_not_promise_szov_lengths(self):
        self.assertIn("(uploadDirection ? adaptInstructionStepsForUpload(baseSteps) : baseSteps)", VIEW_SOURCE)
        table = VIEW_SOURCE[VIEW_SOURCE.index("const UPLOAD_DIRECTION_TEXT_FIXES = ["):]
        table = table[:table.index("];")]
        for source_text in re.findall(r"\['([^']+)', '", table):
            with self.subTest(source_text=source_text[:40]):
                # Каждая замена обязана находить свой текст в шагах — иначе она молча не сработает.
                self.assertGreaterEqual(VIEW_SOURCE.count(source_text), 2)



class PilotAndDepartmentSelectorTests(unittest.TestCase):
    """Пилот только для суперадминов и выбор отдела «СЗоВ / Тез КЦ» (12.11.2026)."""

    def test_tez_runs_are_super_admin_only(self):
        ns = _registry()
        exec(next(ast.get_source_segment(DATABASE_SOURCE, node) for node in DATABASE_MODULE.body
                  if isinstance(node, ast.Assign) and any(getattr(target, "id", "") == "SHIFT_AUCTION_SUPER_ADMIN_ONLY_MODES"
                                                          for target in node.targets)), ns)
        self.assertEqual(ns["SHIFT_AUCTION_SUPER_ADMIN_ONLY_MODES"], ("tez_tp", "tez_op"))
        staff = ROUTES_SOURCE[ROUTES_SOURCE.index("    def _shift_auction_staff_modes("):]
        staff = staff[:staff.index("\n    def ")]
        self.assertIn("if role == 'super_admin':\n            return SHIFT_AUCTION_MODES", staff)
        self.assertIn("if mode not in SHIFT_AUCTION_SUPER_ADMIN_ONLY_MODES", staff)

    def test_operator_of_a_pilot_run_is_not_in_the_auction(self):
        body = _method("_shift_auction_mode_for_operator_tx")
        self.assertIn("if mode in SHIFT_AUCTION_SUPER_ADMIN_ONLY_MODES and not include_pilot:\n            return None", body)
        # Назначить смену человеку пилота может только суперадмин — там пилот виден.
        self.assertIn("include_pilot=True) or SHIFT_AUCTION_MODE_LINE", _method("admin_claim_shift_for_operator"))
        self.assertIn("if mode in SHIFT_AUCTION_SUPER_ADMIN_ONLY_MODES:\n                raise ValueError(\"NOT_TEST_PARTICIPANT\")",
                      _method("post_auction_claim_saved_shift"))

    def test_szov_assign_keeps_the_line_fallback(self):
        """СЗоВ не задет: человеку вне прогонов метод по-прежнему подставляет линию."""
        self.assertIn("db.shift_auction_mode_for_operator(operator_id, include_pilot=True) or SHIFT_AUCTION_MODE_LINE",
                      ROUTES_SOURCE)

    def test_department_is_chosen_in_the_sidebar_menu(self):
        """13.11.2026 владелец: выбор «СЗоВ / Тез КЦ» — меню пункта сайдбара, как линия и
        чат у «Расчета ресурсов», а не переключатель внутри раздела."""
        start = APP_SOURCE.index('<SidebarDeptScope section="shift_auction" activeCode={activeDeptCode}>')
        item = APP_SOURCE[start:APP_SOURCE.index("</SidebarDeptScope>", start)]
        # Меню — только у суперадмина; у админов пункт прежний и ведёт в раздел сразу.
        self.assertIn("{isSuperAdmin ? (", item)
        self.assertIn("aria-expanded={showSidebarAuctionDropdown}", item)
        self.assertIn('aria-label="Отдел аукциона"', item)
        self.assertIn("{AUCTION_DEPARTMENTS.map((department, index) => (", item)
        self.assertIn("openShiftAuctionDepartment(e, department.code);", item)
        self.assertIn("onClick={(e) => handleSidebarViewNavigation(e, 'shift_auction')}", item)
        # Ветки СВ, оператора и главы отдела меню не получили.
        self.assertEqual(APP_SOURCE.count("aria-expanded={showSidebarAuctionDropdown}"), 1)
        # Внутри раздела второго переключателя отдела нет — только подпись открытого.
        self.assertNotIn("Отдел аукциона", VIEW_SOURCE)
        self.assertIn("{departmentTitle ? `Аукцион смен · ${departmentTitle}` : 'Аукцион смен'}", VIEW_SOURCE)
        self.assertIn("const showsDepartmentTitle = role === 'super_admin' || auctionDepartments.length > 1;", VIEW_SOURCE)

    def test_auction_menu_never_hangs_open_with_another_one(self):
        """Иначе два меню сайдбара висят открытыми одновременно и накладываются."""
        block = APP_SOURCE[APP_SOURCE.index("const handleToggleAuctionDropdown"):]
        block = block[:block.index("};")]
        for setter in ("setShowSidebarAccountDropdown(false)", "setShowSidebarEmployeesDropdown(false)",
                       "setShowSidebarResourceDropdown(false)", "setShowSidebarDeptFilter(false)"):
            with self.subTest(setter=setter):
                self.assertIn(setter, block)
        for other in ("handleToggleDropdown", "handleToggleEmployeesDropdown",
                      "handleToggleResourceDropdown", "handleToggleDeptFilterDropdown"):
            other_block = APP_SOURCE[APP_SOURCE.index(f"const {other} = "):]
            other_block = other_block[:other_block.index("};")]
            with self.subTest(other=other):
                self.assertIn("setShowSidebarAuctionDropdown(false)", other_block)
        self.assertIn("if (sidebarAuctionRef.current && !sidebarAuctionRef.current.contains(e.target)) {", APP_SOURCE)

    def test_sidebar_request_switches_an_open_section_once(self):
        handler = APP_SOURCE[APP_SOURCE.index("const openShiftAuctionDepartment = useCallback("):]
        handler = handler[:handler.index("}, [navigateToView, openAppViewInNewTab]);")]
        self.assertIn("setShiftAuctionInitialPeriod({ department: code, nonce: Date.now() });", handler)
        # Ctrl-клик: новая вкладка откроется на сохранённом направлении выбранного отдела.
        self.assertIn("if (direction) storeAuctionDirection(direction);", handler)
        # Свежий раздел сразу на выбранном отделе — без кадра прошлого.
        self.assertIn("const requestedDepartmentDirection = auctionDirectionForDepartment(initialPeriod?.department);", VIEW_SOURCE)
        self.assertIn("if (appliedRequestedDepartmentKeyRef.current === requestedDepartmentKey) return;", VIEW_SOURCE)
        self.assertIn("handleSwitchDepartmentRef.current(requestedDepartment);", VIEW_SOURCE)
        self.assertIn("onDepartmentShown={setShiftAuctionDepartmentShown}", APP_SOURCE)

    def test_menu_departments_match_the_server_registry(self):
        """Пункты меню и их прогоны — те же пары, что SHIFT_AUCTION_MODE_DEPARTMENT_CODE."""
        memory = (ROOT / "src" / "components" / "resources" / "shiftAuctionDirectionMemory.js").read_text(encoding="utf-8")
        js_pairs = {}
        for code, label, directions in re.findall(
                r"\{ code: '(\w+)', label: '([^']+)', directions: \[([^\]]+)\] \}", memory):
            for constant in re.findall(r"SHIFT_AUCTION_MODE_\w+", directions):
                js_pairs[_registry()[constant]] = code
            self.assertEqual(_registry()["SHIFT_AUCTION_DEPARTMENT_LABELS"][code], label)
        self.assertEqual(js_pairs, dict(_registry()["SHIFT_AUCTION_MODE_DEPARTMENT_CODE"]))
        rules = _registry()["shift_auction_direction_rules"]
        self.assertEqual(rules("line")["department_label"], "СЗоВ")
        self.assertEqual(rules("tez_op")["department_label"], "Тез КЦ")


if __name__ == "__main__":
    unittest.main()
