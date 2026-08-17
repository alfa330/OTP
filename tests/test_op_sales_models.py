import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
APP_PATH = ROOT / "src" / "App.jsx"
DEPARTMENT_VIEWS_PATH = ROOT / "src" / "utils" / "departmentViews.js"
MONITORING_SCALE_PATH = ROOT / "src" / "components" / "monitoring" / "MonitoringScaleView.jsx"

OP_SALES_MODEL_CODES = ("op_verificator", "op_yandex_reg", "op_osnova", "op_potok")


def _read(path):
    return path.read_text(encoding="utf-8-sig")


class OpSalesModelRegistryTests(unittest.TestCase):
    """Реестр моделей направлений ОП в database.py (исходные ассерты, без БД)."""

    def setUp(self):
        self.src = _read(DATABASE_PATH)

    def test_constants_and_allowed(self):
        self.assertIn("CALCULATION_MODEL_OP_VERIFICATOR = 'op_verificator'", self.src)
        self.assertIn("CALCULATION_MODEL_OP_YANDEX_REG = 'op_yandex_reg'", self.src)
        self.assertIn("CALCULATION_MODEL_OP_OSNOVA = 'op_osnova'", self.src)
        self.assertIn("CALCULATION_MODEL_OP_POTOK = 'op_potok'", self.src)
        self.assertIn("CALCULATION_MODEL_OP_SALES_CODES = {", self.src)
        # Коды ОП входят в ALLOWED через объединение — литерал TEZ-хвоста не трогаем.
        self.assertIn("} | CALCULATION_MODEL_OP_SALES_CODES", self.src)

    def test_catalog_includes_op_sales_models(self):
        for const in (
            "CALCULATION_MODEL_OP_VERIFICATOR",
            "CALCULATION_MODEL_OP_YANDEX_REG",
            "CALCULATION_MODEL_OP_OSNOVA",
            "CALCULATION_MODEL_OP_POTOK",
        ):
            self.assertIn(f"dict(CALCULATION_MODEL_DESCRIPTIONS[{const}])", self.src)

    def test_metrics_are_only_hours_and_fines(self):
        # Выделяем список _CALC_METRICS_OP_SALES и проверяем, что в нём ровно
        # две метрики: отработанные часы (ручной ввод) и штрафы.
        start = self.src.index("_CALC_METRICS_OP_SALES = [")
        block = self.src[start:self.src.index("]", start)]
        keys = re.findall(r"_calc_metric\('([^']+)'", block)
        self.assertEqual(keys, ["work_time", "fines"])
        self.assertIn("'daily_hours.work_time'", block)
        self.assertIn("'daily_fines'", block)
        # Все четыре модели ОП используют этот минимальный набор.
        for const in (
            "CALCULATION_MODEL_OP_VERIFICATOR",
            "CALCULATION_MODEL_OP_YANDEX_REG",
            "CALCULATION_MODEL_OP_OSNOVA",
            "CALCULATION_MODEL_OP_POTOK",
        ):
            self.assertIn(f"{const}: list(_CALC_METRICS_OP_SALES)", self.src)


class OpSalesHoursTabsTests(unittest.TestCase):
    """Учёт часов: вкладки реестровых моделей строятся из calculation_model_metrics."""

    def setUp(self):
        self.src = _read(APP_PATH)

    def test_legacy_model_codes_set(self):
        self.assertIn(
            "const LEGACY_TAB_MODEL_CODES = new Set(['', 'operator', 'chat_manager', 'tez_line', 'tez_op']);",
            self.src,
        )

    def test_view_tabs_filtered_by_registry(self):
        start = self.src.index("const VIEW_TABS = useMemo(")
        block = self.src[start:start + 1500]
        self.assertIn("!LEGACY_TAB_MODEL_CODES.has(activeCalcModelCode)", block)
        self.assertIn("calcModelMetrics?.[activeCalcModelCode]", block)
        self.assertIn("TABS.filter((t) => registryKeys.has(t.key))", block)
        # Зависимости мемо включают модель и реестр.
        deps = self.src[start:self.src.index("]);", start) + 3]
        self.assertIn("activeCalcModelCode, calcModelMetrics]", deps)

    def test_selected_tab_falls_back_to_work_time(self):
        # Пропавшая после смены модели вкладка откатывается на work_time.
        self.assertIn("VIEW_TABS.some((t) => t.key === selectedTab)", self.src)
        self.assertIn("setSelectedTab('work_time')", self.src)


class OpSalesSupervisorHoursAccessTests(unittest.TestCase):
    """«Учет часов» (sv_hours) открыт супервайзерам отдела продаж."""

    def test_sales_supervisor_allowlist_has_sv_hours(self):
        src = _read(DEPARTMENT_VIEWS_PATH)
        start = src.index("const SALES_SUPERVISOR_VIEWS = [")
        block = src[start:src.index("];", start)]
        self.assertIn("'sv_hours'", block)
        # sv_hours идёт после четвёртой позиции: SALES_HEAD_VIEWS строится из
        # slice(0, 4) + monitoring_scale + slice(4) — ранняя вставка сломала бы head-набор.
        head = re.findall(r"'([a-z_]+)'", block)
        self.assertGreaterEqual(head.index("sv_hours"), 4)


class OpSalesOperatorHoursAccessTests(unittest.TestCase):
    """«Мои часы» (hours) открыт операторам и стажёрам отдела продаж."""

    def setUp(self):
        src = _read(DEPARTMENT_VIEWS_PATH)
        start = src.index("const SALES_OPERATOR_VIEWS = [")
        self.views = re.findall(r"'([a-z_]+)'", src[start:src.index("];", start)])
        self.src = src

    def test_operator_allowlist_has_hours(self):
        self.assertIn("hours", self.views)

    def test_salary_stays_first(self):
        # firstAllowedView берёт allow[0]: раздел по умолчанию не должен съехать.
        self.assertEqual(self.views[0], "salary")

    def test_operator_and_trainee_share_the_list(self):
        block = self.src[self.src.index("    op: {"):self.src.index("    front_office: {")]
        self.assertIn("operator: SALES_OPERATOR_VIEWS,", block)
        self.assertIn("trainee: SALES_OPERATOR_VIEWS,", block)


class OpSalesSalaryCalculatorWiringTests(unittest.TestCase):
    """Калькуляторы ОП «Основа» и «Поток»: сами формулы проверяют
    tests/salary_osnova.test.mjs и tests/salary_potok.test.mjs, здесь — что
    раздел до них вообще доходит."""

    def setUp(self):
        self.app = _read(APP_PATH)
        self.calculator = _read(ROOT / "src" / "components" / "salary" / "SalaryCalculatorOsnova.jsx")
        self.potok_calculator = _read(ROOT / "src" / "components" / "salary" / "SalaryCalculatorPotok.jsx")
        self.result_card = _read(ROOT / "src" / "components" / "salary" / "SalaryCalculationResult.jsx")

    def test_sales_department_has_a_calculator(self):
        # Без 'op' в списке весь раздел подменяется заглушкой SalaryComingSoon.
        start = self.app.index("const SALARY_CALCULATOR_READY_DEPARTMENT_CODES = new Set(")
        block = self.app[start:self.app.index(")", start)]
        self.assertIn("'op'", block)

    def test_salary_view_loads_hours_for_own_model(self):
        # Модель оператора живёт в часах месяца. Раздел «Зарплата» их не запрашивал,
        # поэтому модель откатывалась в 'operator' и оператору ОП «Основа»
        # показывалась заглушка «калькулятор скоро» — в «Зарплату» он попадает
        # сразу при входе, минуя «Мои часы».
        self.assertIn("if (view === 'salary' && (isOpSalaryDept || isTezSalaryDept)) {", self.app)
        effect = self.app[self.app.index("if (view === 'salary' && (isOpSalaryDept || isTezSalaryDept)) {"):]
        self.assertIn("fetchHoursData();", effect[:400])
        # Флаги отдела обязаны быть в зависимостях: иначе эффект не перезапустится,
        # когда список отделов доедет и isOpSalaryDept станет true.
        deps = effect[:effect.index("useEffect(", 1)] if "useEffect(" in effect[1:] else effect[:3000]
        self.assertIn("view, isOpSalaryDept, isTezSalaryDept]", deps)

    def test_unknown_model_does_not_fall_back_to_the_stub(self):
        # Пока часы не загрузились, модель неизвестна — подменять раздел заглушкой
        # нельзя, иначе оператор видит «скоро» на каждом входе.
        self.assertIn("const isOwnCalculationModelKnown = Boolean(ownHoursRow);", self.app)
        gate = self.app[self.app.index("const showOpCalculator = isOpSalaryDept"):]
        gate = gate[:gate.index(";") + 1]
        self.assertIn("!isOwnCalculationModelKnown", gate)
        self.assertIn("isOwnOpModelSupported", gate)

    def test_own_hours_are_prefilled_without_visiting_hours_section(self):
        self.assertIn("const opAutoPrefillKeyRef = useRef('');", self.app)
        effect = self.app[self.app.index("const opAutoPrefillKeyRef = useRef('');"):]
        effect = effect[:effect.index("}, [view, showOpCalculator")]
        # Один раз на месяц — иначе перезапрос часов затирал бы введённое руками.
        self.assertIn("if (opAutoPrefillKeyRef.current === prefillKey) return;", effect)
        self.assertIn("setOpCalculatorPrefillNonce", effect)
        for field in ("hoursNorm:", "hoursWorked:", "quality:", "fines:", "bonuses:"):
            self.assertIn(field, effect)

    def test_only_supported_models_get_a_calculator(self):
        # Верификатору и Яндекс-регистрации формулы ОП не подходят — им остаётся
        # заглушка, а СВ и главе доступны обе вкладки.
        self.assertIn("const OP_SALARY_CALCULATOR_MODELS = new Set(['op_osnova', 'op_potok']);", self.app)
        self.assertIn("const showOpCalculator = isOpSalaryDept", self.app)
        self.assertIn("isOpSalaryDept && !showOpCalculator", self.app)

    def test_operator_sees_his_own_model_without_tabs(self):
        # Вкладку модели оператор не выбирает: в своей зарплате ошибиться нельзя.
        self.assertIn("const opSalaryModel = (isOwnSalaryOperator && isOwnOpModelSupported)", self.app)
        self.assertIn("? ownCalculationModelCode", self.app)
        self.assertIn("!(isOwnSalaryOperator && isOwnOpModelSupported) && (", self.app)
        # Коды моделей должны переживать нормализацию сохранённой вкладки.
        start = self.app.index("const SALARY_CALCULATOR_TYPES = new Set(")
        types = self.app[start:self.app.index(")", start)]
        self.assertIn("'op_osnova'", types)
        self.assertIn("'op_potok'", types)

    def test_potok_calculator_is_wired(self):
        self.assertIn("import('./components/salary/SalaryCalculatorPotok')", self.app)
        self.assertIn("opSalaryModel === 'op_potok' ? (", self.app)
        self.assertIn("salaryResult.model === 'op_potok'", self.result_card)
        self.assertIn("PotokCalculationResult", self.result_card)
        # Оба потока продаж и обе удерживаемые суммы должны вводиться.
        for state in ("setChurnSales", "setFocusSales", "setHourlyRate", "setFines", "setWithholding", "setIsNewbie"):
            self.assertIn(state, self.potok_calculator)

    def test_hours_section_knows_potok_model(self):
        self.assertIn("const isPotokModel = !hasMixedCalculationModels && calculationModelCode === 'op_potok';", self.app)
        self.assertIn("const isOpSalesModel = isOsnovaModel || isPotokModel;", self.app)
        self.assertIn("Открыть калькулятор «Поток»", self.app)
        # Переход из часов работает для обеих моделей ОП одной веткой.
        handler = self.app[self.app.index("const openSalaryCalculatorWithHours = () => {"):]
        self.assertIn("if (isOpSalesModel) {", handler[:4000])

    def test_hours_button_opens_op_calculator_before_generic_branches(self):
        # Ветка моделей ОП обязана стоять раньше isTezModel и общей: иначе оператор
        # «Основы»/«Потока» молча уезжает в калькулятор СЗоВ.
        handler = self.app[self.app.index("const openSalaryCalculatorWithHours = () => {"):]
        handler = handler[:handler.index("const estimatedSalaryHint")] if "const estimatedSalaryHint" in handler else handler[:8000]
        osnova_at = handler.index("if (isOpSalesModel) {")
        self.assertLess(osnova_at, handler.index("if (isTezModel) {"))
        self.assertLess(osnova_at, handler.index("if (isChatModel) {"))
        self.assertIn("setOpCalculatorPrefillNonce", handler)

    def test_result_card_dispatches_osnova_model(self):
        self.assertIn("salaryResult.model === 'op_osnova'", self.result_card)
        self.assertIn("OsnovaCalculationResult", self.result_card)

    def test_calculator_is_lazy_and_month_aware(self):
        self.assertIn("import('./components/salary/SalaryCalculatorOsnova')", self.app)
        # Норма на 1 FTE зависит от месяца (176/168/160) — месяц обязан доезжать.
        self.assertIn("month={selectedMonth}", self.app)
        self.assertIn("opFteNormHoursForMonth(month)", self.calculator)

    def test_calculator_inputs_cover_every_formula_argument(self):
        for state in (
            "setHoursWorked", "setHoursNorm", "setDeals", "setPlanPerFte",
            "setNormHoursFte", "setNightShift", "setIsNewbie", "setQuality",
            "setFines", "setBonuses",
        ):
            self.assertIn(state, self.calculator)


class OpSalesDirectionsEditorTests(unittest.TestCase):
    """Редактор направлений (шкала мониторинга) знает коды моделей ОП —
    иначе normalizeCalculationModelCode сбрасывал бы их в operator при сохранении."""

    def test_monitoring_scale_has_op_sales_models(self):
        src = _read(MONITORING_SCALE_PATH)
        for code in OP_SALES_MODEL_CODES:
            self.assertIn(f"code: '{code}'", src)


if __name__ == "__main__":
    unittest.main()
