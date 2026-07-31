"""Регрессии выбора формата Excel в «Расчёт часов → Биллинг»."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_PATH = ROOT / "src" / "components" / "resources" / "ResourceFteView.jsx"
BOT_PATH = ROOT / "bot_schedule2.py"


class BillingExportChoiceTests(unittest.TestCase):
    def test_frontend_offers_general_and_efficiency_exports(self):
        source = FRONTEND_PATH.read_text(encoding="utf-8-sig")
        self.assertIn('<option value="general">Общая (текущая)</option>', source)
        self.assertIn(
            '<option value="efficiency">По эффективности операторов</option>',
            source,
        )
        self.assertIn("report_type: billingExportType", source)
        self.assertIn("operator_efficiency_${billingApplied.from}_${billingApplied.to}.xlsx", source)
        self.assertIn(
            "Excel по эффективности считается за полные дни; фильтр времени не применяется",
            source,
        )
        self.assertIn("billingExportType === 'general' && !billingReport", source)

    def test_backend_routes_efficiency_to_grouped_workbook(self):
        source = BOT_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("report_type not in ('general', 'efficiency')", source)
        self.assertIn("_oktell_billing_parse_date_args()", source)
        self.assertIn("db.get_billing_operator_efficiency_report(", source)
        self.assertIn("department_id=department_id", source)
        self.assertIn("allow_all=allow_all", source)
        self.assertIn("Не удалось определить отдел пользователя", source)
        self.assertIn("_oktell_billing_efficiency_workbook(params, report)", source)
        self.assertIn('"operator_efficiency_"', source)


class BillingServiceLevelFormulaTests(unittest.TestCase):
    """SL = отвеченные за порог ожидания в очереди / все звонки, попавшие в очередь."""

    def test_frontend_divides_sl_by_queue_arrivals(self):
        source = FRONTEND_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("const slRatio = safeRatio(item.served_sl, item.arrived);", source)
        self.assertIn("safeRatio(billingTotals.served_sl, billingTotals.arrived)", source)
        self.assertIn("safeRatio(day.totals?.served_sl, day.totals?.arrived)", source)
        self.assertNotIn("served_sl, item.served", source)
        self.assertNotIn("served_sl, billingTotals.served", source)
        self.assertNotIn("served_sl, day.totals?.served)", source)

    def test_backend_export_divides_sl_by_queue_arrivals(self):
        source = BOT_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("_oktell_billing_ratio(item.get('served_sl'), arrived)", source)
        self.assertNotIn("_oktell_billing_ratio(item.get('served_sl'), served)", source)


if __name__ == "__main__":
    unittest.main()
