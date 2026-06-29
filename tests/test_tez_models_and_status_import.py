import ast
import csv
import re
import unittest
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DATABASE_PATH = ROOT / "database.py"
SALARY_FORMULA_PATH = ROOT / "src" / "utils" / "salaryFormula.js"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _tez_status_import_namespace():
    """Загружает функции TEZ-парсера статусов из bot_schedule2.py в изолированное
    пространство имён (как делает test_status_import_chat2desk.py)."""
    wanted_assignments = {"TEZ_STATUS_IMPORT_MAP", "_KZ_TO_RU_FOLD"}
    wanted_functions = {
        "_status_import_normalize_key",
        "_status_import_normalize_header",
        "_status_import_normalize_operator_name",
        "_status_import_operator_name_variants",
        "_status_import_dedupe_operator_infos",
        "_status_import_resolve_operator_matches",
        "_status_import_parse_datetime",
        "_status_import_split_segment_by_day",
        "_status_import_resolve_tez_status",
        "_status_import_header_is_tez",
        "_status_import_parse_tez_rows",
        "_status_import_parse_tez_csv",
        "_status_import_csv_text_is_tez",
        "_status_import_normalize_sip",
    }
    module = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    selected = []
    for node in module.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & wanted_assignments:
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            selected.append(node)

    ns = {
        "csv": csv,
        "re": re,
        "datetime": datetime,
        "timedelta": timedelta,
        "StringIO": StringIO,
        "STATUS_IMPORT_INVALID_ROWS_PREVIEW_LIMIT": 30,
        "STATUS_IMPORT_MAX_SOURCE_ROWS": 120000,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"), ns)
    return ns


SAMPLE_TEZ_CSV = (
    "internal number;employee name;started at;stopped at;seconds in status;status\n"
    "903;Тест Оператор;00:00 01-06-2026;10:01 01-06-2026;36111;inactive \n"
    "903;Тест Оператор;10:01 01-06-2026;12:40 01-06-2026;9491;active \n"
    "903;Тест Оператор;12:40 01-06-2026;12:42 01-06-2026;165;break in work \n"
    "903;Тест Оператор;13:22 05-06-2026;13:25 05-06-2026;180;work in crm \n"
    "903;Тест Оператор;20:00 01-06-2026;10:01 03-06-2026;136850;active \n"
)


class TezStatusImportParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = _tez_status_import_namespace()
        normalize_name = cls.ns["_status_import_normalize_operator_name"]
        cls.lookup = {normalize_name("Тест Оператор"): [{"id": 1, "name": "Тест Оператор"}]}

    def test_format_detection(self):
        self.assertTrue(self.ns["_status_import_csv_text_is_tez"](SAMPLE_TEZ_CSV))
        chat_csv = "OperatorName;StateName;TimeChange\nИван;Готов;01.06.2026 10:00\n"
        self.assertFalse(self.ns["_status_import_csv_text_is_tez"](chat_csv))

    def test_tez_datetime_format(self):
        parsed = self.ns["_status_import_parse_datetime"]("00:00 01-06-2026")
        self.assertEqual(parsed, datetime(2026, 6, 1, 0, 0, 0))

    def test_status_mapping_work_break_ignore(self):
        resolve = self.ns["_status_import_resolve_tez_status"]
        self.assertEqual(resolve("active")["kind"], "work")
        self.assertEqual(resolve("work in crm")["kind"], "work")
        self.assertEqual(resolve("break in work")["kind"], "break")
        self.assertEqual(resolve("inactive")["kind"], "ignore")
        # хвостовые пробелы из выгрузки нормализуются
        self.assertEqual(resolve("active ")["key"], "active")

    def test_segments_built_inactive_ignored_and_multiday_split(self):
        parsed = self.ns["_status_import_parse_tez_csv"](SAMPLE_TEZ_CSV, self.lookup)
        self.assertEqual(parsed["source_rows"], 5)
        self.assertEqual(parsed["ignored_events_count"], 1)  # inactive
        self.assertEqual(parsed["invalid_rows_count"], 0)
        self.assertEqual(parsed["operators_count"], 1)
        # active(1) + break(1) + crm(1) + active-многодневный(01,02,03 -> 3) = 6
        self.assertEqual(len(parsed["segments"]), 6)
        self.assertEqual(
            sorted({s["status_key"] for s in parsed["segments"]}),
            ["active", "break in work", "work in crm"],
        )
        days = {s["status_date"] for s in parsed["segments"]}
        self.assertTrue({"2026-06-01", "2026-06-02", "2026-06-03"}.issubset(days))

    def test_unknown_operator_is_invalid(self):
        csv_text = (
            "internal number;employee name;started at;stopped at;seconds in status;status\n"
            "999;Нет Такого;10:01 01-06-2026;12:40 01-06-2026;9491;active \n"
        )
        parsed = self.ns["_status_import_parse_tez_csv"](csv_text, self.lookup)
        self.assertEqual(parsed["invalid_rows_count"], 1)
        self.assertEqual(len(parsed["segments"]), 0)

    def test_sip_matching_prefers_internal_number_over_name(self):
        # Имя в выгрузке заведомо «не то», но внутренний номер совпадает с sip оператора.
        csv_text = (
            "internal number;employee name;started at;stopped at;seconds in status;status\n"
            "903;Совсем Другое Имя;10:01 02-06-2026;12:40 02-06-2026;9491;active \n"
        )
        normalize_sip = self.ns["_status_import_normalize_sip"]
        sip_lookup = {normalize_sip("903"): [{"id": 342, "name": "Саттер Аруна Бекеткызы"}]}
        parsed = self.ns["_status_import_parse_tez_csv"](csv_text, {}, sip_lookup=sip_lookup)
        self.assertEqual(parsed["operators_count"], 1)
        self.assertEqual(parsed["invalid_rows_count"], 0)
        self.assertEqual(len(parsed["segments"]), 1)
        self.assertEqual(int(parsed["segments"][0]["operator_id"]), 342)
        # Без sip_lookup имя не матчится → строка невалидна.
        parsed2 = self.ns["_status_import_parse_tez_csv"](csv_text, {})
        self.assertEqual(parsed2["invalid_rows_count"], 1)
        self.assertEqual(len(parsed2["segments"]), 0)

    def test_normalize_sip_strips_nondigits_and_leading_zeros(self):
        normalize_sip = self.ns["_status_import_normalize_sip"]
        self.assertEqual(normalize_sip("0903"), "903")
        self.assertEqual(normalize_sip(" 903 "), "903")
        self.assertEqual(normalize_sip("sip:902"), "902")
        self.assertEqual(normalize_sip(""), "")


class TezCalculationModelRegistryTests(unittest.TestCase):
    """Реестр моделей TEZ в database.py (исходные ассерты, без БД)."""

    def setUp(self):
        self.src = _read(DATABASE_PATH)

    def test_constants_and_allowed(self):
        self.assertIn("CALCULATION_MODEL_TEZ_LINE = 'tez_line'", self.src)
        self.assertIn("CALCULATION_MODEL_TEZ_OP = 'tez_op'", self.src)
        self.assertIn("CALCULATION_MODEL_TEZ_CODES", self.src)
        self.assertIn("CALCULATION_MODEL_TEZ_LINE,\n    CALCULATION_MODEL_TEZ_OP\n}", self.src)

    def test_catalog_and_metrics_include_tez(self):
        self.assertIn("dict(CALCULATION_MODEL_DESCRIPTIONS[CALCULATION_MODEL_TEZ_LINE])", self.src)
        self.assertIn("dict(CALCULATION_MODEL_DESCRIPTIONS[CALCULATION_MODEL_TEZ_OP])", self.src)
        self.assertIn("CALCULATION_MODEL_TEZ_LINE: _CALC_METRICS_HEAD + _CALC_METRICS_TAIL", self.src)
        self.assertIn("CALCULATION_MODEL_TEZ_OP: _CALC_METRICS_HEAD + _CALC_METRICS_TAIL", self.src)

    def test_status_profile_branch_for_tez(self):
        self.assertIn("if code in CALCULATION_MODEL_TEZ_CODES:", self.src)
        self.assertIn("TEZ_WORK_STATUS_KEYS = {'active', 'work in crm', 'работа в crm'}", self.src)
        self.assertIn("TEZ_BREAK_STATUS_KEYS = {'break in work'}", self.src)
        self.assertIn("TEZ_IGNORED_STATUS_KEYS = {'inactive'}", self.src)
        profile = self.src[self.src.index("if code in CALCULATION_MODEL_TEZ_CODES:"):]
        profile = profile[:profile.index("return {", profile.index("return {") + 1)]
        self.assertIn("'work': TEZ_WORK_STATUS_KEYS", profile)
        self.assertIn("'break': TEZ_BREAK_STATUS_KEYS", profile)
        self.assertIn("'ignored': TEZ_IGNORED_STATUS_KEYS", profile)


class TezMonthlyPlanBackendTests(unittest.TestCase):
    def test_table_and_crud_present(self):
        src = _read(DATABASE_PATH)
        self.assertIn("CREATE TABLE IF NOT EXISTS operator_monthly_plans", src)
        self.assertIn("UNIQUE(operator_id, year, month)", src)
        self.assertIn("def get_operator_monthly_plan(self, operator_id, year, month):", src)
        self.assertIn("def upsert_operator_monthly_plan(", src)
        self.assertIn("def get_operator_monthly_plans_for_operators(", src)

    def test_endpoints_are_department_scoped(self):
        src = _read(BOT_PATH)
        self.assertIn("def get_operator_plan():", src)
        self.assertIn("def save_operator_plan():", src)
        save = src[src.index("def save_operator_plan():"):][:4000]
        # Запись плана — только управленец, в рамках своего отдела.
        self.assertIn("_load_target_user_with_scope", save)
        self.assertIn("allow_self=False", save)
        self.assertIn("_is_admin_role(requester[3])", save)
        self.assertIn("_is_supervisor_role(requester[3])", save)
        self.assertIn("_headed_department_id(requester_id) is not None", save)
        self.assertIn("db.upsert_operator_monthly_plan", save)

    def test_shared_department_plan_table_crud_and_endpoints(self):
        db_src = _read(DATABASE_PATH)
        self.assertIn("CREATE TABLE IF NOT EXISTS department_monthly_plans", db_src)
        self.assertIn("UNIQUE(department_id, year, month)", db_src)
        self.assertIn("def get_department_monthly_plan(self, department_id, year, month):", db_src)
        self.assertIn("def upsert_department_monthly_plan(", db_src)
        self.assertIn("plan_per_fte", db_src)

        bot_src = _read(BOT_PATH)
        self.assertIn("def get_department_plan():", bot_src)
        self.assertIn("def save_department_plan():", bot_src)
        save = bot_src[bot_src.index("def save_department_plan():"):][:3000]
        self.assertIn("db.upsert_department_monthly_plan", save)
        self.assertIn("_department_scope_id_for_requester(requester_id)", save)
        self.assertIn("Forbidden for this department", save)


class TezSalaryFormulaTests(unittest.TestCase):
    """Формулы ЗП TEZ в salaryFormula.js (исходные ассерты + ключевые пороги)."""

    def setUp(self):
        self.src = SALARY_FORMULA_PATH.read_text(encoding="utf-8")

    def test_functions_and_rates(self):
        self.assertIn("export function calculateTezLineSalary(", self.src)
        self.assertIn("export function calculateTezOpSalary(", self.src)
        self.assertIn("export const TEZ_NORM_HOURS = 176;", self.src)
        self.assertIn("export const TEZ_LINE_OKLAD = 100000;", self.src)
        self.assertIn("export const TEZ_OP_OKLAD = 150000;", self.src)

    def test_quality_and_seniority_thresholds(self):
        self.assertIn("if (q >= 96) return 1.0;", self.src)
        self.assertIn("if (q >= 86) return 0.8;", self.src)
        self.assertIn("if (q >= 76) return 0.6;", self.src)
        self.assertIn("if (q >= 70) return 0.4;", self.src)
        self.assertIn("if (m >= 18) return 0.30;", self.src)
        self.assertIn("if (m >= 3) return 0.10;", self.src)

    def test_op_uses_deal_percent_not_quality(self):
        op = self.src[self.src.index("export function calculateTezOpSalary("):]
        self.assertIn("dealPercent", op)
        self.assertIn("bonusDeals = oklad * dealPercent", op)


class TezBinotelSyncTests(unittest.TestCase):
    def test_sync_module_has_login_and_code_map(self):
        src = (ROOT / "tez_status_sync.py").read_text(encoding="utf-8")
        self.assertIn("logining[email]", src)
        self.assertIn("logining[password]", src)
        self.assertIn("analyticsEmployeesOnTimeline", src)
        self.assertIn("listOfEmployeesPresenceStates", src)
        # presence code -> status text
        self.assertIn("0: \"active\"", src)
        self.assertIn("1: \"work in crm\"", src)
        self.assertIn("3: \"break in work\"", src)
        self.assertIn("4: \"inactive\"", src)
        self.assertIn("def build_tez_status_csv", src)
        self.assertIn("def run_sync", src)

    def test_backend_sip_lookup_and_binotel_endpoint(self):
        db_src = _read(DATABASE_PATH)
        self.assertIn("def get_operator_sip_map(self):", db_src)
        bot_src = _read(BOT_PATH)
        self.assertIn("def _status_import_build_sip_lookup(", bot_src)
        self.assertIn("def _status_import_normalize_sip(", bot_src)
        self.assertIn("def sync_work_schedules_statuses_binotel():", bot_src)
        endpoint = bot_src[bot_src.index("def sync_work_schedules_statuses_binotel():"):][:3000]
        self.assertIn("больше 10 дней", endpoint)
        self.assertIn("tez_status_sync", endpoint)
        self.assertIn("_tez_status_sync_importer", endpoint)
        # daily scheduled job registered
        self.assertIn("tez_status_sync_daily", bot_src)


if __name__ == "__main__":
    unittest.main()
