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

    def test_status_mapping_work_break_inactive(self):
        resolve = self.ns["_status_import_resolve_tez_status"]
        self.assertEqual(resolve("active")["kind"], "work")
        self.assertEqual(resolve("work in crm")["kind"], "work")
        self.assertEqual(resolve("break in work")["kind"], "break")
        # inactive («выход из системы») сохраняем как сегмент (kind != ignore),
        # чтобы офлайн был виден на таймлайне.
        self.assertEqual(resolve("inactive")["kind"], "status")
        self.assertEqual(resolve("inactive")["key"], "inactive")
        # хвостовые пробелы из выгрузки нормализуются
        self.assertEqual(resolve("active ")["key"], "active")

    def test_segments_built_include_inactive_and_multiday_split(self):
        parsed = self.ns["_status_import_parse_tez_csv"](SAMPLE_TEZ_CSV, self.lookup)
        self.assertEqual(parsed["source_rows"], 5)
        # inactive больше НЕ игнорируется — он сохраняется как сегмент (офлайн).
        self.assertEqual(parsed["ignored_events_count"], 0)
        self.assertEqual(parsed["invalid_rows_count"], 0)
        self.assertEqual(parsed["operators_count"], 1)
        # inactive(1) + active(1) + break(1) + crm(1) + active-многодневный(01,02,03 -> 3) = 7
        self.assertEqual(len(parsed["segments"]), 7)
        self.assertEqual(
            sorted({s["status_key"] for s in parsed["segments"]}),
            ["active", "break in work", "inactive", "work in crm"],
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

    def test_matching_is_by_name(self):
        # Один и тот же внутренний номер может принадлежать разным операторам,
        # поэтому матчим по ФИО из выгрузки, а не по номеру.
        csv_text = (
            "internal number;employee name;started at;stopped at;seconds in status;status\n"
            "903;Тест Оператор;10:01 02-06-2026;12:40 02-06-2026;9491;active \n"
        )
        parsed = self.ns["_status_import_parse_tez_csv"](csv_text, self.lookup)
        self.assertEqual(parsed["operators_count"], 1)
        self.assertEqual(parsed["invalid_rows_count"], 0)
        self.assertEqual(int(parsed["segments"][0]["operator_id"]), 1)


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

    def test_binotel_endpoint_and_name_matching(self):
        bot_src = _read(BOT_PATH)
        self.assertIn("def sync_work_schedules_statuses_binotel():", bot_src)
        endpoint = bot_src[bot_src.index("def sync_work_schedules_statuses_binotel():"):][:3000]
        self.assertIn("больше 10 дней", endpoint)
        self.assertIn("tez_status_sync", endpoint)
        self.assertIn("_tez_status_sync_importer", endpoint)
        # daily scheduled job registered
        self.assertIn("tez_status_sync_daily", bot_src)
        # sip-матчинг откатан: в TEZ-парсере больше нет sip_lookup
        self.assertNotIn("_status_import_build_sip_lookup", bot_src)
        self.assertNotIn("sip_lookup", bot_src)


def _build_binotel_operator_lookup(ns, operators):
    """Мини-версия _status_import_build_operator_lookup для тестов: имя -> оператор(ы)."""
    variants = ns["_status_import_operator_name_variants"]
    lookup = {}
    for op in operators:
        for key in variants(op["name"]):
            lookup.setdefault(key, [])
            if not any(int(item["id"]) == op["id"] for item in lookup[key]):
                lookup[key].append(op)
    return lookup


class TezBinotelRandomCallTests(unittest.TestCase):
    """«Случайный звонок» TEZ через Binotel API 4.0: матчинг по имени + запись."""

    @classmethod
    def setUpClass(cls):
        cls.ns = _tez_status_import_namespace()
        import sys
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import tez_binotel_calls as tb
        cls.tb = tb

    def test_normalize_call_exposes_employee_and_recording(self):
        raw = {
            "generalCallID": "6629469101", "callType": "1", "billsec": "197",
            "waitsec": "15", "startTime": "1780580938",
            "internalNumber": "907", "externalNumber": "77478586700",
            "disposition": "answer", "recordingStatus": "uploaded",
            "employeeData": {"name": "Ермахан Жасмин Бахтияркызы",
                             "email": "ermahanjasmin94@gmail.com"},
        }
        c = self.tb.BinotelApiClient._normalize_call(raw)
        self.assertEqual(c["general_call_id"], "6629469101")
        self.assertEqual(c["call_type"], 1)
        self.assertEqual(c["billsec"], 197)
        self.assertEqual(c["disposition"], "ANSWER")  # нормализуется в верхний регистр
        # employeeData раньше выбрасывался — теперь имя/почта доступны для матчинга.
        self.assertEqual(c["employee_name"], "Ермахан Жасмин Бахтияркызы")
        self.assertEqual(c["employee_email"], "ermahanjasmin94@gmail.com")
        self.assertEqual(c["recording_status"], "uploaded")

    def test_normalize_call_without_employee_data(self):
        c = self.tb.BinotelApiClient._normalize_call({"callID": "42", "billsec": 10})
        self.assertEqual(c["general_call_id"], "42")  # gid c fallback на callID
        self.assertEqual(c["employee_name"], "")
        self.assertEqual(c["employee_email"], "")
        self.assertEqual(c["recording_status"], "")

    def test_recorded_statuses_constant(self):
        self.assertIn("uploaded", self.tb.RECORDED_STATUSES)

    def test_shared_sip_is_disambiguated_by_name(self):
        # Прод: внутренний номер 925 закреплён СРАЗУ за двумя операторами. Матчинг по
        # имени должен развести их звонки, а не свалить всё на одного (это и была
        # ошибка sip-матчинга, которую чиним).
        ops = [
            {"id": 365, "name": "Нурмухан Акерке Асылкызы"},   # sip 925
            {"id": 367, "name": "Сагатова Аружан Алтайкызы"},  # sip 925 (тот же!)
            {"id": 337, "name": "Ермахан Жасмин Бахтияркызы"},  # sip 907
        ]
        lookup = _build_binotel_operator_lookup(self.ns, ops)
        resolve = self.ns["_status_import_resolve_operator_matches"]
        self.assertEqual([o["id"] for o in resolve("Нурмухан Акерке Асылкызы", lookup)], [365])
        self.assertEqual([o["id"] for o in resolve("Сагатова Аружан Алтайкызы", lookup)], [367])
        self.assertEqual([o["id"] for o in resolve("Ермахан Жасмин Бахтияркызы", lookup)], [337])
        # Чужое/неизвестное имя не привязываем ни к кому.
        self.assertEqual(resolve("Кто То Неизвестный", lookup), [])

    def test_random_call_matches_by_name_and_uses_recording_status(self):
        bot_src = _read(BOT_PATH)
        self.assertIn("def _binotel_random_call(", bot_src)
        func = bot_src[bot_src.index("def _binotel_random_call("):][:4500]
        # Матчинг звонка с оператором — по имени тем же резолвером, что и в ветке Oktell.
        self.assertIn("_status_import_build_operator_lookup", func)
        self.assertIn("_status_import_resolve_operator_matches", func)
        self.assertIn("employee_name", func)
        # Наличие записи берём из recordingStatus, а не только угадываем по disposition.
        self.assertIn("recording_status", func)
        self.assertIn("RECORDED_STATUSES", func)

    def test_too_frequent_wait_seconds_parsing(self):
        f = self.tb._too_frequent_wait_seconds
        self.assertEqual(f({"message": "Requests are too frequent. You can do this request after 7 sec."}), 7)
        self.assertEqual(f({"error": "too frequent"}), 10)  # без числа — дефолт
        self.assertIsNone(f({"message": "some other error"}))

    def test_post_retries_on_rate_limit(self):
        import unittest.mock as mock
        tb = self.tb
        n = {"c": 0}

        class FakeResp:
            status_code = 200
            def __init__(self, payload): self._p = payload
            def json(self): return self._p

        class FakeSession:
            def post(self, url, json=None, timeout=None):
                n["c"] += 1
                if n["c"] == 1:
                    return FakeResp({"status": "error",
                                     "message": "Requests are too frequent. You can do this request after 2 sec."})
                return FakeResp({"status": "success", "callDetails": {}})

        client = tb.BinotelApiClient("k", "s")
        client.session = FakeSession()
        with mock.patch.object(tb.time, "sleep", lambda *a, **k: None):
            payload = client._post("stats/x", {})
        self.assertEqual(payload.get("status"), "success")
        self.assertEqual(n["c"], 2)  # один повтор после «too frequent»

    def test_client_has_rate_limit_config(self):
        self.assertTrue(hasattr(self.tb, "MIN_REQUEST_INTERVAL"))
        self.assertTrue(hasattr(self.tb, "RATE_LIMIT_MAX_RETRIES"))
        self.assertIn("uploaded", self.tb.RECORDED_STATUSES)

    def test_random_call_supports_count_and_7day_cap(self):
        bot_src = _read(BOT_PATH)
        func = bot_src[bot_src.index("def _binotel_random_call("):]
        func = func[:func.index("\ndef fetch_random_evaluation_call(")]
        self.assertIn("count=1", func)              # параметр количества в сигнатуре
        self.assertIn("MAX_WINDOW_DAYS", func)       # лимит периода 7 дней
        self.assertIn("created_list", func)          # создаём несколько звонков
        self.assertIn('"calls"', func)               # ответ содержит список
        # Диспетч ограничивает count и прокидывает его в обе ветки.
        self.assertIn("RANDOM_CALL_MAX_COUNT", bot_src)
        disp = bot_src[bot_src.index("def fetch_random_evaluation_call("):][:5000]
        self.assertIn("count=count", disp)           # проброс в TEZ-ветку
        self.assertIn("len(created_list) >= count", bot_src)  # Oktell-ветка тоже по count

    def test_audio_endpoint_has_on_demand_fetch(self):
        bot_src = _read(BOT_PATH)
        self.assertIn("def _binotel_store_record(", bot_src)   # синхронное ядро докачки
        ep_start = bot_src.index("def get_imported_call_audio_file(")
        ep_end = bot_src.index("\n@app.route('/api/admin/shuffle'", ep_start)
        ep = bot_src[ep_start:ep_end]
        self.assertIn("_binotel_store_record(", ep)            # докачка по требованию
        self.assertIn("AUDIO_NOT_READY", ep)


if __name__ == "__main__":
    unittest.main()
