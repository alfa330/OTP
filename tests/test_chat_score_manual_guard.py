import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
BOT_SOURCE = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
CHAT_SCORE_SOURCE = (ROOT / "src" / "utils" / "chatScore.js").read_text(encoding="utf-8-sig")


class ChatScoreManualGuardTests(unittest.TestCase):
    """Средняя оценка чатов меняется только импортом/синком Chat2Desk."""

    def test_hours_modal_shows_score_as_read_only(self):
        start = APP_SOURCE.index("Средняя оценка (1–5)")
        end = APP_SOURCE.index("{!isChatModel && (", start)
        block = APP_SOURCE[start:end]

        self.assertIn("readOnly", block)
        self.assertIn("disabled", block)
        self.assertIn("Рассчитывается автоматически по данным Chat2Desk", block)
        self.assertNotIn("updateChatMetricField('avg_score'", block)

    def test_group_day_payload_does_not_send_score(self):
        start = APP_SOURCE.index("// Ручные чат-метрики (для чат-модели)")
        end = APP_SOURCE.index("month: cellModel.month", start)
        block = APP_SOURCE[start:end]

        self.assertIn("avg_response_time_seconds:", block)
        self.assertIn("transfer_chat_count:", block)
        self.assertNotIn("avg_score:", block)

    def test_backend_group_day_upload_cannot_overwrite_score(self):
        start = BOT_SOURCE.index("def upload_group_day():")
        end = BOT_SOURCE.index("@app.route", start)
        block = BOT_SOURCE[start:end]

        self.assertIn("средняя оценка приходят только из отчётов/синка Chat2Desk", block)
        self.assertNotIn("metric_payload['avg_score']", block)
        self.assertNotIn("metric_payload['score_sum']", block)
        self.assertNotIn("metric_payload['score_count']", block)

    def test_report_import_still_updates_weighted_score_fields(self):
        self.assertIn(
            "CHAT_REPORT_TYPE_SCORE: {'score_sum', 'score_count', 'avg_score'}",
            BOT_SOURCE,
        )
        self.assertIn("metric['score_sum'] = round(bucket['score_sum'], 4)", BOT_SOURCE)
        self.assertIn("metric['score_count'] = int(bucket['score_count'])", BOT_SOURCE)

    def test_hours_total_uses_weighted_score_not_mean_of_daily_averages(self):
        start = APP_SOURCE.index("{selectedTab === 'avg_score' && (() => {")
        end = APP_SOURCE.index("{selectedTab === 'response_time' && (() => {", start)
        block = APP_SOURCE[start:end]

        self.assertIn("getWeightedChatAverage(op)", block)
        self.assertNotIn("vals.reduce", block)

        self.assertIn("chatMetrics.score_sum", CHAT_SCORE_SOURCE)
        self.assertIn("chatMetrics.score_count", CHAT_SCORE_SOURCE)
        self.assertIn("scoreSum / scoreCount", CHAT_SCORE_SOURCE)
        self.assertIn(
            "import { calculateWeightedChatAverage, getChatScoreContribution } from './utils/chatScore';",
            APP_SOURCE,
        )

    def test_split_salary_uses_score_sum_and_count(self):
        start = APP_SOURCE.index("const dualSalaryResults = (() => {")
        end = APP_SOURCE.index("const totalBase = aggregated.reduce", start)
        block = APP_SOURCE[start:end]

        self.assertIn("getChatScoreContribution(cm)", block)
        self.assertNotIn("safeNum(cm.avg_score)", block)

    def test_regular_chat_salary_uses_monthly_weighted_aggregate(self):
        start = APP_SOURCE.index("const chatAvgScore = safeNum(op.aggregates?.chat_avg_score)")
        end = APP_SOURCE.index("const estimatedTezSalary", start)
        block = APP_SOURCE[start:end]

        self.assertIn("avgScore: chatAvgScore", block)


if __name__ == "__main__":
    unittest.main()
