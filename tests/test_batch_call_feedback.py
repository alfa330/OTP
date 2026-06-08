import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"
MAIN_JSX_PATH = ROOT / "src" / "call_evaluation" / "main.jsx"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


class BatchCallFeedbackSchemaTests(unittest.TestCase):
    """Batch feedback links several call_feedbacks to ONE shared training, so the
    1:1 UNIQUE on call_feedbacks.training_id must be gone while call_id stays unique."""

    def test_training_id_is_not_unique(self):
        source = _read(DATABASE_PATH)
        self.assertIn(
            "training_id INTEGER REFERENCES trainings(id) ON DELETE SET NULL,",
            source,
        )
        self.assertNotIn(
            "training_id INTEGER UNIQUE REFERENCES trainings(id)",
            source,
        )

    def test_legacy_unique_constraint_is_dropped(self):
        source = _read(DATABASE_PATH)
        self.assertIn(
            "ALTER TABLE call_feedbacks DROP CONSTRAINT IF EXISTS call_feedbacks_training_id_key;",
            source,
        )

    def test_call_id_remains_unique(self):
        # One feedback per call must still hold (LEFT JOIN call_feedbacks ON call_id is 1:1).
        source = _read(DATABASE_PATH)
        self.assertIn(
            "call_id INTEGER NOT NULL UNIQUE REFERENCES calls(id) ON DELETE CASCADE,",
            source,
        )


class BatchCallFeedbackBackendTests(unittest.TestCase):
    def test_batch_endpoint_exists(self):
        source = _read(BOT_PATH)
        self.assertIn(
            "@app.route('/api/call_evaluations/feedback/batch', methods=['POST'])",
            source,
        )
        self.assertIn("def create_call_feedback_batch():", source)

    def test_batch_creates_single_shared_training(self):
        source = _read(BOT_PATH)
        module = ast.parse(source)
        func = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "create_call_feedback_batch"
        )
        body = ast.get_source_segment(source, func)
        # All evaluations in one batch belong to one operator and one training session.
        self.assertIn("must belong to the same operator", body)
        self.assertIn("shared_training_id", body)
        # Only evaluations without feedback may be batched.
        self.assertIn("Feedback already exists for calls", body)
        # Exactly one INSERT INTO trainings for the whole batch.
        self.assertEqual(body.count("INSERT INTO trainings"), 1)

    def test_single_endpoint_is_share_aware(self):
        source = _read(BOT_PATH)
        module = ast.parse(source)
        func = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "upsert_call_feedback"
        )
        body = ast.get_source_segment(source, func)
        # Editing one member of a shared training must not move it for the others.
        self.assertIn("is_shared_training", body)
        self.assertIn("keep_shared", body)
        self.assertIn("detached_from_training_id", body)


class BatchCallFeedbackFrontendTests(unittest.TestCase):
    def test_frontend_has_batch_modal_and_endpoint(self):
        source = _read(MAIN_JSX_PATH)
        self.assertIn("const BatchFeedbackModal", source)
        self.assertIn("/api/call_evaluations/feedback/batch", source)
        self.assertIn("batchMode", source)
        self.assertIn("selectedBatchIds", source)


if __name__ == "__main__":
    unittest.main()
