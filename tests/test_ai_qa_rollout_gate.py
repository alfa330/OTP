import unittest
from unittest import mock

from call_qa import api
from call_qa.evaluation.fingerprint import content_hash

EXPERIMENT_ID = "00000000-0000-0000-0000-000000000001"


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.params = None

    def execute(self, _sql, params=None):
        self.params = params

    def fetchone(self):
        return self.row


class RolloutGateTests(unittest.TestCase):
    @staticmethod
    def _metrics():
        return {
            "pairs": 30,
            "delta": {"alarm_precision_pp": 12, "recall_pp": -1},
            "retrieval": {"false_hit_rate_pct": 4, "latency_p95_ms": 450},
            "quality_gates": {"passed": True},
        }

    def _row(self, *, current_snapshot=41, foreign_labels=0, experiment_config=None):
        experiment_config = experiment_config or api.current_rag_experiment_config()
        return (
            self._metrics(), "succeeded", 41, experiment_config,
            content_hash(experiment_config), api.config.CLAUDE_MODEL,
            current_snapshot, 25, foreign_labels,
        )

    def test_accepts_only_passed_experiment_on_current_direction_snapshot(self):
        cur = _Cursor(self._row())
        state = api._approved_experiment_state(
            cur, direction_id=73, experiment_id=EXPERIMENT_ID)
        self.assertTrue(state["valid"])
        self.assertEqual(state["approved_snapshot_id"], 41)
        self.assertEqual(cur.params[:2], (73, 73))

    def test_rejects_approval_after_knowledge_snapshot_changes(self):
        cur = _Cursor(self._row(current_snapshot=42))
        state = api._approved_experiment_state(cur, direction_id=73, experiment_id=EXPERIMENT_ID)
        self.assertFalse(state["valid"])
        self.assertIn("изменилась", state["reason"])

    def test_rejects_mixed_direction_gold_set(self):
        cur = _Cursor(self._row(foreign_labels=1))
        state = api._approved_experiment_state(cur, direction_id=73, experiment_id=EXPERIMENT_ID)
        self.assertFalse(state["valid"])
        self.assertIn("направлению", state["reason"])

    def test_rejects_experiment_from_previous_runtime_config(self):
        old_config = {**api.current_rag_experiment_config(), "version": 0}
        cur = _Cursor(self._row(experiment_config=old_config))
        state = api._approved_experiment_state(cur, direction_id=73, experiment_id=EXPERIMENT_ID)
        self.assertFalse(state["valid"])
        self.assertIn("конфигурация", state["reason"])


class _FakeCursorCM:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        pass

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _FakeCursorCM(self._row)

    def close(self):
        pass


class RolloutManualOverrideTests(unittest.TestCase):
    """Ручное включение RAG (без paired experiment) — осознанное и обратимое."""

    def _resolve(self, row):
        with mock.patch.object(api.config, "connect_ro", return_value=_FakeConn(row)):
            return api._rag_rollout(direction_id=73, call_id=123)

    def test_manual_override_enables_active_without_experiment(self):
        # (rollout_mode, canary_percent, quality_gates, approved_experiment_id)
        row = ("active", 0,
               {"manual_override": {"by": 1, "reason": "срочно", "at": "2026-07-15T00:00:00+00:00"}},
               None)
        result = self._resolve(row)
        self.assertEqual(result["mode"], "active")
        self.assertTrue(result["rag_enabled"])
        self.assertTrue(result["approval"].get("manual"))

    def test_active_without_experiment_or_override_falls_back_to_shadow(self):
        row = ("active", 0, {}, None)
        result = self._resolve(row)
        self.assertEqual(result["mode"], "shadow")
        self.assertFalse(result["rag_enabled"])


if __name__ == "__main__":
    unittest.main()
