# -*- coding: utf-8 -*-
"""Показатели ОП TEZ в «Моих часах», модалках дня и калькуляторе.

Проверяем три вещи:
  1) бэкенд отдаёт оператору его успешки по дням и общий план отдела;
  2) фронт различает модель tez_op (раньше все модели схлопывались в operator)
     и считает превью зарплаты по формуле ОП, а не по операторской;
  3) карточка результата TEZ переехала на общий компонент со СЗоВ.
"""

import ast
import textwrap
import unittest
from pathlib import Path
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
APP_PATH = ROOT / "src" / "App.jsx"
RESULT_CARD_PATH = ROOT / "src" / "components" / "salary" / "SalaryCalculationResult.jsx"
TEZ_CALCULATOR_PATH = ROOT / "src" / "components" / "salary" / "SalaryCalculatorTez.jsx"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _database_method_source(name):
    source = _read(DATABASE_PATH)
    module = source_cache.parse(source)
    database_class = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    method = next(
        node
        for node in database_class.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return textwrap.dedent(ast.get_source_segment(source, method))


def _load_database_method(name):
    namespace = {}
    exec(_database_method_source(name), namespace)
    return namespace[name]


class _CursorContext:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executions = []

    def execute(self, query, params=None):
        self.executions.append((query, params))

    def fetchall(self):
        return self.rows


class SuccessesByOperatorDayTests(unittest.TestCase):
    """Сужение агрегата успешек до одного оператора — источник данных «Моих часов»."""

    def _run(self, rows, **kwargs):
        method = _load_database_method("get_tez_successes_operator_day")
        cursor = _FakeCursor(rows)

        class FakeDatabase:
            _TEZ_GROUP_FILTER_SQL = "\n AND EXISTS (SELECT 1 FROM group_operator_memberships gom WHERE gom.group_id = %s)\n"
            get_tez_successes_operator_day = method

            def _get_cursor(self):
                return _CursorContext(cursor)

        result = FakeDatabase().get_tez_successes_operator_day(2026, 7, **kwargs)
        return result, cursor

    def test_operator_filter_narrows_query_and_params(self):
        result, cursor = self._run([(349, 3, 1), (349, 4, 2)], operator_id=349)

        self.assertEqual(len(cursor.executions), 1)
        query, params = cursor.executions[0]
        normalized = " ".join(query.split())
        self.assertIn("AND s.operator_id = %s", normalized)
        self.assertNotIn("group_operator_memberships", normalized)
        self.assertEqual(params, (2026, 7, 349))
        self.assertEqual(
            result,
            [
                {"operator_id": 349, "day": 3, "successes": 1},
                {"operator_id": 349, "day": 4, "successes": 2},
            ],
        )

    def test_group_and_operator_filters_keep_parameter_order(self):
        """Группа подставляется раньше оператора — иначе %s разъедутся по местам."""
        _, cursor = self._run([], group_id=35, operator_id=349)
        _, params = cursor.executions[0]
        self.assertEqual(params, (2026, 7, 35, 349))

    def test_without_operator_behaviour_unchanged(self):
        _, cursor = self._run([], group_id=35)
        query, params = cursor.executions[0]
        self.assertNotIn("s.operator_id = %s", " ".join(query.split()))
        self.assertEqual(params, (2026, 7, 35))


class OperatorMonthPayloadTests(unittest.TestCase):
    """Ответ /api/sv/daily_hours для самого оператора: успешки + план отдела."""

    def setUp(self):
        self.src = _database_method_source("get_daily_hours_for_operator_month")

    def test_department_id_is_selected_for_plan_lookup(self):
        self.assertIn("u.department_id,", self.src)
        self.assertIn(
            "name, rate, hire_date, direction_name, calculation_model_raw, department_id,",
            self.src,
        )

    def test_successes_are_fetched_only_for_tez_op_model(self):
        tez_branch = self.src.split("if effective_calculation_model_code == CALCULATION_MODEL_TEZ_OP:")[1]
        self.assertIn(
            "self.get_tez_successes_operator_day(year, mon, operator_id=operator_id)",
            tez_branch,
        )
        self.assertIn("self.get_department_monthly_plan(int(department_id), year, mon)", tez_branch)

    def test_successes_land_in_days_totals_and_plan(self):
        self.assertIn("entry['tez_successes'] = count", self.src)
        self.assertIn('"tez_successes_by_day": tez_successes_by_day', self.src)
        self.assertIn('"tez_plan_per_fte": tez_plan_per_fte', self.src)
        self.assertIn('"total_tez_successes": int(total_tez_successes)', self.src)

    def test_defaults_are_empty_for_other_models(self):
        """Без модели ОП поля остаются пустыми, лишних запросов нет."""
        prologue = self.src.split("if effective_calculation_model_code == CALCULATION_MODEL_TEZ_OP:")[0]
        self.assertIn("tez_successes_by_day = {}", prologue)
        self.assertIn("total_tez_successes = 0", prologue)
        self.assertIn("tez_plan_per_fte = None", prologue)


class WorkHoursModelResolutionTests(unittest.TestCase):
    """Раньше «Мои часы» знали только operator/chat_manager — ОП выглядел как СЗоВ."""

    def setUp(self):
        self.src = _read(APP_PATH)

    def test_known_model_codes_are_preserved(self):
        self.assertIn("const WORK_HOURS_KNOWN_MODEL_CODES = new Set([", self.src)
        for code in ("tez_line", "tez_op", "op_verificator", "op_yandex_reg", "op_osnova", "op_potok"):
            self.assertIn(f"'{code}'", self.src)
        self.assertIn(
            "return WORK_HOURS_KNOWN_MODEL_CODES.has(code) ? code : 'operator';",
            self.src,
        )

    def test_hours_view_detects_tez_models(self):
        self.assertIn(
            "const isTezOpModel = !hasMixedCalculationModels && calculationModelCode === 'tez_op';",
            self.src,
        )
        self.assertIn(
            "const isTezLineModel = !hasMixedCalculationModels && calculationModelCode === 'tez_line';",
            self.src,
        )


class MyHoursOpMetricsTests(unittest.TestCase):
    """«Мои часы» для ОП: успешки вместо интенсивности звонков и своя формула ЗП."""

    def setUp(self):
        self.src = _read(APP_PATH)

    def test_successes_and_plan_replace_calls_per_hour(self):
        self.assertIn("Успешки и корректировки", self.src)
        self.assertIn("Успешки за месяц", self.src)
        self.assertIn("План успешек", self.src)
        self.assertIn("Выполнение плана", self.src)

    def test_plan_uses_shared_tez_op_rules(self):
        self.assertIn("const tezPlanResult = isTezOpModel", self.src)
        self.assertIn("planPerFte: op.tez_plan_per_fte,", self.src)
        self.assertIn("hireDate: op.hire_date,", self.src)

    def test_salary_preview_uses_tez_formulas(self):
        self.assertIn("const estimatedTezSalary = isTezOpModel", self.src)
        self.assertIn("? calculateTezOpSalary({", self.src)
        self.assertIn("? calculateTezLineSalary({", self.src)
        self.assertIn(
            "const estimatedSalary = estimatedTezSalary",
            self.src,
        )
        # Импорт проверяем посимвольно, а не одним литералом: список моделей
        # растёт (добавилась ОП «Основа»), и жёсткая строка ломалась бы на каждой.
        import_line = self.src[self.src.index("import { calculateOperatorSalary"):]
        import_line = import_line[:import_line.index("from './utils/salaryFormula'")]
        for symbol in (
            "calculateOperatorSalary",
            "calculateChatSalary",
            "resolveMonthlySalaryQuality",
            "calculateTezOpMonthlyPlan",
            "calculateTezOpSalary",
            "calculateTezLineSalary",
        ):
            self.assertIn(symbol, import_line)

    def test_calculator_opens_on_own_tez_model_with_prefill(self):
        self.assertIn("setTezCalculatorPrefill({", self.src)
        self.assertIn("setCalculatorType(isTezOpModel ? 'tez_op' : 'tez_line');", self.src)
        self.assertIn("hoursPrefill={tezCalculatorPrefill}", self.src)
        self.assertIn("hoursPrefillNonce={tezCalculatorPrefillNonce}", self.src)

    def test_tez_operator_lands_on_own_calculator_tab(self):
        """Вкладка калькулятора по умолчанию = модель самого оператора."""
        self.assertIn("const tezCalculatorTypePickedRef = useRef(false);", self.src)
        self.assertIn(
            "if (!isTezSalaryDept || tezCalculatorTypePickedRef.current) return;",
            self.src,
        )
        self.assertIn(
            "const own = TEZ_SALARY_CALCULATOR_TYPES.has(ownCalculationModelCode)",
            self.src,
        )
        # Ручной выбор вкладки выключает автоподстановку. Вкладки строятся из
        # каталога отделов, поэтому проверяем обработчик, а не два литерала.
        self.assertIn(
            "if (activeSalaryDeptCode === 'tez') tezCalculatorTypePickedRef.current = true;",
            self.src,
        )
        self.assertIn("setCalculatorType(model.key);", self.src)


class ProfileTilesTests(unittest.TestCase):
    """Профиль ОП: вместо прослушек и среднего балла — успешки и план."""

    def setUp(self):
        self.src = _read(APP_PATH)

    def test_profile_detects_tez_op_model(self):
        self.assertIn(
            "const profileIsTezOp = resolveWorkHoursMonthModelInfo(hoursOp).modelCode === 'tez_op';",
            self.src,
        )

    def test_profile_shows_successes_and_plan_for_op(self):
        self.assertIn("Успешки / план", self.src)
        self.assertIn("Выполнение плана", self.src)
        self.assertIn("const profilePlanResult = profileIsTezOp", self.src)
        self.assertIn("planPerFte: hoursOp?.tez_plan_per_fte,", self.src)
        self.assertIn("'План не задан'", self.src)

    def test_other_models_keep_the_score_tile(self):
        """У остальных моделей остаётся средний балл.

        Плитки «Прослушано / нужно» здесь больше нет ни у кого: план проверок
        сотруднику не показывается (решение владельца 03.09.2026, задача #272,
        сторожит tests/test_review_counts_hidden_from_operators.py).
        """
        self.assertIn("Ср. балл", self.src)
        self.assertNotIn("Прослушано / нужно", self.src)

    def test_hours_and_norm_tiles_are_shared(self):
        """Часы и норма показываются всем — они вне ветвления по модели."""
        head, _, tail = self.src.partition("const profileIsTezOp")
        block = tail.split("Быстрые действия")[0]
        self.assertEqual(block.count('Часов<'), 1)
        self.assertEqual(block.count('>Норма<'), 1)


class DayDetailSuccessesTests(unittest.TestCase):
    """Успешки в детализации дня — и в «Моих часах», и в «Учёте часов»."""

    def setUp(self):
        self.src = _read(APP_PATH)

    def test_my_hours_day_modal_keeps_metrics_and_adds_successes(self):
        self.assertIn(
            "const selectedDayIsTezOp = selectedDay?.calculationModelCode === 'tez_op';",
            self.src,
        )
        self.assertIn("const tezSuccessesForDay = Number(", self.src)
        self.assertIn("hoursData?.tez_successes_by_day?.[String(selectedDay.day)]", self.src)
        self.assertIn("<div className=\"text-sm font-medium text-gray-800\">Успешки</div>", self.src)
        # прежние показатели дня остаются на месте
        for label in ('label: "Перерыв"', 'label: "Звонки"', 'label: "Эффективность"', 'label: "Разговор"'):
            self.assertIn(label, self.src)
        # и добавляются собственные показатели ОП
        self.assertIn('label: "Набор"', self.src)
        self.assertIn('label: "Чаты"', self.src)

    def test_sv_hours_cell_modal_shows_read_only_successes(self):
        self.assertIn("рассчитываются автоматически", self.src)
        self.assertIn(
            "{tezSuccessMap?.[String(cellModel.operator_id)]?.[String(cellModel.day)] || 0}",
            self.src,
        )


class TezResultCardTests(unittest.TestCase):
    """Итог расчёта TEZ показывается в той же карточке, что у СЗоВ."""

    def setUp(self):
        self.card = _read(RESULT_CARD_PATH)
        self.calculator = _read(TEZ_CALCULATOR_PATH)

    def test_calculator_reuses_shared_result_card(self):
        self.assertIn("import SalaryCalculationResult from './SalaryCalculationResult';", self.calculator)
        self.assertIn("<SalaryCalculationResult", self.calculator)
        # собственная плоская карточка результата убрана
        self.assertNotIn("Результат расчёта", self.calculator)

    def test_card_branches_on_tez_models(self):
        self.assertIn(
            "if (salaryResult.model === 'tez_op' || salaryResult.model === 'tez_line') {",
            self.card,
        )

    def test_szov_layout_elements_are_shared(self):
        for marker in (
            "Копировать итого",
            "Компоненты выплаты",
            "Детали расчёта",
            "Итого к выплате",
        ):
            self.assertIn(marker, self.card)

    def test_tez_specific_rows_present(self):
        for marker in (
            "Бонус за успешки",
            "Надбавка за стаж",
            "% сделок",
            "Удержано 50%",
            "Штрафы",
        ):
            self.assertIn(marker, self.card)

    def test_legacy_models_keep_kpi_layout(self):
        self.assertIn("Сводка по KPI и выплатам", self.card)
        self.assertIn("Баллы KPI", self.card)
        self.assertIn("Коэффициент премии", self.card)


if __name__ == "__main__":
    unittest.main()
