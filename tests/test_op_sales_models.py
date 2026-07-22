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


class OpSalesDirectionsEditorTests(unittest.TestCase):
    """Редактор направлений (шкала мониторинга) знает коды моделей ОП —
    иначе normalizeCalculationModelCode сбрасывал бы их в operator при сохранении."""

    def test_monitoring_scale_has_op_sales_models(self):
        src = _read(MONITORING_SCALE_PATH)
        for code in OP_SALES_MODEL_CODES:
            self.assertIn(f"code: '{code}'", src)


if __name__ == "__main__":
    unittest.main()
