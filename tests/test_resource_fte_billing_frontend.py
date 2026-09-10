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


class BillingLineKeyTests(unittest.TestCase):
    """Задача #308: ключ группировки линии и ключ её показа должны быть ОДИН И ТОТ ЖЕ.

    Пока бэкенд группировал по сырому ANumberDialed, а фронт показывал последние 10 цифр,
    один номер расползался на несколько строк отчёта, а из мусорного значения получался
    несуществующий номер. Правило «10 цифр, иначе линия не определена» живёт в двух местах —
    здесь сторожим, чтобы они не разъехались."""

    def test_frontend_requires_ten_digits(self):
        source = FRONTEND_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("const billingLineKey = (line) => {", source)
        self.assertIn("return digits.length >= 10 ? digits.slice(-10) : '';", source)
        # подпись и номер обязаны ходить через общий ключ, а не резать строку по-своему
        self.assertIn("BILLING_LINE_LABELS[billingLineKey(line)]", source)
        self.assertIn("const digits = billingLineKey(line);", source)
        self.assertNotIn("String(line || '').replace(/\\D/g, '').slice(-10)", source)

    def test_backend_requires_ten_digits(self):
        source = BOT_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("def _oktell_billing_line_key(value):", source)
        self.assertIn("return digits[-10:] if len(digits) >= 10 else ''", source)
        # группировка сводки и строка детализации берут тот же ключ, что и подпись
        self.assertIn(
            "key = (park, _oktell_billing_line_key(raw.get('line_number'))) if include_line",
            source,
        )
        self.assertIn("'line': _oktell_billing_line_key(raw.get('line_number')),", source)
        self.assertNotIn("re.sub(r'\\D', '', str(line or ''))[-10:]", source)

    def test_line_label_maps_match(self):
        """Подписи линий продублированы во фронте и в выгрузке — списки обязаны совпадать."""
        import re

        front = FRONTEND_PATH.read_text(encoding="utf-8-sig")
        back = BOT_PATH.read_text(encoding="utf-8-sig")
        front_block = re.search(r"const BILLING_LINE_LABELS = \{(.*?)\n\};", front, re.S)
        back_block = re.search(r"_OKTELL_BILLING_LINE_LABELS = \{(.*?)\n\}", back, re.S)
        self.assertIsNotNone(front_block, "не нашли BILLING_LINE_LABELS во фронте")
        self.assertIsNotNone(back_block, "не нашли _OKTELL_BILLING_LINE_LABELS в бэкенде")
        front_map = dict(re.findall(r"'?(\d{10})'?:\s*'([^']*)'", front_block.group(1)))
        back_map = dict(re.findall(r"'(\d{10})':\s*'([^']*)'", back_block.group(1)))
        self.assertTrue(front_map, "не разобрали подписи линий во фронте")
        self.assertEqual(front_map, back_map)
        # ключ — ровно 10 цифр национального номера, иначе поиск подписи не сработает
        for digits in front_map:
            self.assertTrue(digits.startswith("7"), digits)


if __name__ == "__main__":
    unittest.main()
