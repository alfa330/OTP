from datetime import datetime, timezone
import unittest

from call_qa.evaluation.benchmark import (
    classification_metrics, evaluate_quality_gates, paired_rag_report,
    retrieval_metrics, validate_temporal_split,
)


class RagBenchmarkTests(unittest.TestCase):
    def test_alarm_metrics(self):
        metrics = classification_metrics(
            ["Incorrect", "Incorrect", "Correct", "N/A"],
            ["Incorrect", "Correct", "Incorrect", "N/A"],
        )
        self.assertEqual((metrics["tp"], metrics["fp"], metrics["fn"]), (1, 1, 1))
        self.assertEqual(metrics["alarm_precision_pct"], 50.0)
        self.assertEqual(metrics["recall_pct"], 50.0)

    def test_retrieval_metrics_include_no_answer_false_hits(self):
        metrics = retrieval_metrics([
            {"relevant_rule_ids": ["r2"], "hit_rule_ids": ["r1", "r2"], "retrieval_ms": 100},
            {"relevant_rule_ids": [], "hit_rule_ids": ["r9"], "retrieval_ms": 300},
            {"relevant_rule_ids": [], "hit_rule_ids": [], "retrieval_ms": 200},
        ], k=3)
        self.assertEqual(metrics["recall_at_k_pct"], 100.0)
        self.assertEqual(metrics["precision_at_k_pct"], 50.0)
        self.assertEqual(metrics["mrr"], 0.5)
        self.assertEqual(metrics["false_hit_rate_pct"], 50.0)
        self.assertEqual(metrics["latency_p95_ms"], 290.0)

    def test_paired_report_counts_improvement_and_harm(self):
        report = paired_rag_report([
            {"gold_verdict": "Incorrect", "off_verdict": "Correct", "on_verdict": "Incorrect"},
            {"gold_verdict": "Correct", "off_verdict": "Correct", "on_verdict": "Incorrect"},
            {"gold_verdict": "N/A", "off_verdict": "N/A", "on_verdict": "N/A"},
        ])
        self.assertEqual(report["improved"], 1)
        self.assertEqual(report["harmed"], 1)
        self.assertEqual(report["changed"], 2)

    def test_temporal_split_prevents_leakage(self):
        utc = timezone.utc
        cutoff = datetime(2026, 1, 10, tzinfo=utc)
        validate_temporal_split(
            knowledge_cutoff_at=cutoff,
            rule_created_at=datetime(2026, 1, 9, tzinfo=utc),
            call_created_at=datetime(2026, 1, 11, tzinfo=utc),
        )
        with self.assertRaises(ValueError):
            validate_temporal_split(
                knowledge_cutoff_at=cutoff,
                rule_created_at=datetime(2026, 1, 11, tzinfo=utc),
                call_created_at=datetime(2026, 1, 12, tzinfo=utc),
            )

    def test_quality_gates_are_all_explicit(self):
        report = {
            "pairs": 30,
            "delta": {"alarm_precision_pp": 12, "recall_pp": -1},
            "retrieval": {"false_hit_rate_pct": 4, "latency_p95_ms": 450},
            "efficiency": {},
        }
        result = evaluate_quality_gates(report)
        self.assertTrue(result["passed"])
        self.assertTrue(all(result["checks"].values()))

    def test_quality_gate_rejects_tiny_sample(self):
        report = {
            "pairs": 3,
            "delta": {"alarm_precision_pp": 12, "recall_pp": -1},
            "retrieval": {"false_hit_rate_pct": 4, "latency_p95_ms": 450},
        }
        result = evaluate_quality_gates(report)
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["sample_size"])


if __name__ == "__main__":
    unittest.main()
