import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_SOURCE = (ROOT / "database.py").read_text(encoding="utf-8-sig")


class ChatScoreSnapshotFallbackTests(unittest.TestCase):
    def test_group_hours_overlay_keeps_live_weighted_score_for_legacy_null_snapshot(self):
        start = DATABASE_SOURCE.index("# Закрытый месяц (после 10-го числа следующего)")
        end = DATABASE_SOURCE.index("return {\"month\": month, \"days_in_month\": days, \"operators\": operators}", start)
        block = DATABASE_SOURCE[start:end]

        self.assertIn('if r[13] is not None', block)
        self.assertIn('_live_aggregates.get("chat_avg_score")', block)

    def test_snapshot_reader_fallback_is_weighted(self):
        start = DATABASE_SOURCE.index("def get_month_snapshot(self, month, group_id=None):")
        end = DATABASE_SOURCE.index("def _recalculate_auto_daily_hours_tx", start)
        block = DATABASE_SOURCE[start:end]

        self.assertIn("COALESCE(\n                           s.chat_avg_score", block)
        self.assertIn("SUM(cmm.score_sum)", block)
        self.assertIn("SUM(cmm.score_count)", block)
        self.assertIn("COUNT(cmm.avg_score)", block)
        self.assertIn("gom.group_id = s.group_id", block)
        self.assertIn("gom.start_date <= cmm.day", block)
        self.assertNotIn("AVG(cmm.avg_score)", block)


if __name__ == "__main__":
    unittest.main()
