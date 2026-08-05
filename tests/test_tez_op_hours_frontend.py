import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "src" / "App.jsx"


def _read_app():
    return APP_PATH.read_text(encoding="utf-8-sig")


class TezOpHoursFrontendTests(unittest.TestCase):
    """Source-level contract for the TEZ OP metrics in «Учет часов»."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read_app()

    def test_tez_op_tabs_are_next_to_calls_and_successes(self):
        start = self.src.index("const VIEW_TABS = useMemo(")
        end = self.src.index("return TABS;", start)
        block = self.src[start:end]

        expected_tabs = (
            "{ key: 'dial_time', label: 'Время набора', unit: 'ч' }",
            "{ key: 'talk_time', label: 'В разговоре', unit: 'ч' }",
            "{ key: 'chats', label: 'Чаты', unit: 'шт' }",
            "{ key: 'tez_successes', label: 'Успешки', unit: 'шт' }",
        )
        positions = [block.index(tab) for tab in expected_tabs]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("if (isTezOpContext)", block)
        self.assertIn("if (t.key === 'calls')", block)

    def test_work_and_indicator_groups_are_logically_split(self):
        groups_start = self.src.index("const WORKHOURS_METRIC_GROUPS = useMemo(")
        groups_end = self.src.index("}, [VIEW_TABS]);", groups_start)
        groups = self.src[groups_start:groups_end]

        self.assertIn(
            "buildGroup('Работа', 'fa-clock', 'blue', ['work_time', 'break_time'])",
            groups,
        )
        self.assertIn(
            "buildGroup('Показатели', 'fa-chart-line', 'cyan', "
            "['calls', 'dial_time', 'talk_time', 'chats', 'efficiency', "
            "'tez_successes', 'avg_score', 'response_time'])",
            groups,
        )
        self.assertNotIn("buildGroup('Метрики'", groups)
        self.assertIn("const isIndicatorsGroup = group.label === 'Показатели';", self.src)
        self.assertIn("isChatModel && isIndicatorsGroup", self.src)
        self.assertIn("Показатели → Синхронизация", self.src)
        self.assertNotIn("Метрики → Отчёты", self.src)

    def test_data_presence_filter_knows_new_daily_and_aggregate_fields(self):
        start = self.src.index("const hasAnyHoursIndicators = (op) =>")
        end = self.src.index("const filteredOperators", start)
        block = self.src[start:end]

        for aggregate_key in ("'total_dial_time'", "'total_talk_time'", "'total_chats'"):
            self.assertIn(aggregate_key, block)
        for daily_field in ("dayData.dial_time", "dayData.talk_time", "dayData.chats"):
            self.assertIn(daily_field, block)

    def test_daily_count_metrics_are_integers_and_times_keep_two_decimals(self):
        start = self.src.index("function renderCellByMetricWithStyleAndMarker")
        end = self.src.index("function getTrainingsFor", start)
        block = self.src[start:end]

        self.assertIn("metricKey === 'calls' || metricKey === 'chats'", block)
        self.assertIn("Math.round(Number(val || 0))", block)
        self.assertIn("num ? num.toFixed(2) : '0.00'", block)

    def test_operator_and_footer_totals_use_api_aggregates(self):
        for expression in (
            "getHoursMetricTotal(op, 'total_calls', 'calls')",
            "getHoursMetricTotal(op, 'total_dial_time', 'dial_time')",
            "getHoursMetricTotal(op, 'total_talk_time', 'talk_time')",
            "getHoursMetricTotal(op, 'total_chats', 'chats')",
        ):
            self.assertIn(expression, self.src)

        self.assertIn("isTezOpContext ? 'Всего звонков'", self.src)
        self.assertIn("Math.round(callsTotal).toLocaleString('ru-RU')", self.src)
        self.assertIn("Math.round(chatsTotal).toLocaleString('ru-RU')", self.src)
        self.assertIn("footerTotals.sumDialTime", self.src)
        self.assertIn("footerTotals.sumTalkTime", self.src)
        self.assertIn("footerTotals.sumChats", self.src)
        self.assertIn("Math.round(footerTotals.sumCalls).toLocaleString('ru-RU')", self.src)

    def test_missing_aggregate_falls_back_to_sum_of_daily_values(self):
        start = self.src.index("const getHoursMetricTotal =")
        end = self.src.index("const hasAnyHoursIndicators", start)
        block = self.src[start:end]

        self.assertIn("const aggregateValue = aggregates[aggregateKey]", block)
        self.assertIn("Object.values(daily).reduce", block)
        self.assertIn("Number(dayData[dailyKey])", block)

    def test_success_rows_show_count_with_completion_and_footer_keeps_all_totals(self):
        # Владелец (2026-08-05): в строке оператора нужно и само число успешек,
        # и процент выполнения — раньше число пряталось в подсказке.
        header_anchor = self.src.index("{/* Right summary headers */}")
        header_start = self.src.index("{selectedTab === 'tez_successes' && (", header_anchor)
        header_end = self.src.index("{selectedTab === 'avg_score'", header_start)
        header = self.src[header_start:header_end]
        self.assertIn("Успешки и выполнение плана", header)
        self.assertEqual(header.count("hoursSummaryColClass"), 1)

        rows_anchor = self.src.index("{/* Right summary values */}")
        rows_start = self.src.index("{selectedTab === 'tez_successes' && (() => {", rows_anchor)
        rows_end = self.src.index("{selectedTab === 'avg_score'", rows_start)
        rows = self.src[rows_start:rows_end]
        self.assertIn("const pct = plan > 0 ? (total / plan) * 100 : null;", rows)
        self.assertIn("{pct == null ? '—' : `${pct.toFixed(0)}%`}", rows)
        self.assertIn("{total}", rows)
        self.assertEqual(rows.count("hoursSummaryColClass"), 1)

        footer_totals_start = self.src.index("const footerTotals = useMemo(")
        footer_totals_end = self.src.index("// Red → amber → green gradient", footer_totals_start)
        footer_totals = self.src[footer_totals_start:footer_totals_end]
        self.assertIn("let sumTezSuccesses = 0;", footer_totals)
        self.assertIn("tezSuccessMap?.[String(op.operator_id)]", footer_totals)
        self.assertIn("sumTezSuccesses,", footer_totals)
        self.assertIn("month, tezSuccessMap]", footer_totals)

        footer_anchor = self.src.index("{/* FOOTER: итоговые строки */}")
        footer_start = self.src.index("{selectedTab === 'tez_successes' && (() => {", footer_anchor)
        footer_end = self.src.index("{selectedTab === 'efficiency'", footer_start)
        footer = self.src[footer_start:footer_end]
        footer_row = self.src[footer_anchor:footer_end]
        self.assertIn("footerTotals.sumTezSuccesses", footer)
        self.assertIn("footerTotals.sumTezPlan", footer)
        self.assertIn("Общий план", footer_row)
        self.assertIn("Выполнение", footer)
        self.assertIn("const pct = plan > 0 ? (total / plan) * 100 : null;", footer)


if __name__ == "__main__":
    unittest.main()
