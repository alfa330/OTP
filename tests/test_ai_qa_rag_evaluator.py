import unittest
from unittest import mock

from call_qa.evaluation import evaluator


class FrozenRagContextTests(unittest.TestCase):
    def setUp(self):
        self.direction = {
            "id": 72,
            "criteria": [{
                "idx": 0, "criterion_id": "criterion:greeting", "name": "Greeting",
                "description": "Say hello", "weight": 100, "is_critical": False,
                "eval_source": "transcript",
            }],
        }
        self.prepared = {
            "rag_text": "frozen policy context",
            "retrieval_trace": {"status": "ok", "candidates": [], "criteria": []},
            "matched_criterion_idxs": [],
        }

    def test_complete_primary_does_not_retrieve_or_call_llm_again(self):
        primary = {"per_criterion": [{
            "idx": 0, "verdict": "Correct", "confidence": .9,
            "evidence_quote": "hello", "comment": "ok",
        }], "overall_comment": ""}
        with mock.patch.object(evaluator.cc, "apply_to_direction"), \
             mock.patch.object(evaluator.config, "CLAUDE_MODEL_BULK", "same"), \
             mock.patch.object(evaluator.config, "CLAUDE_MODEL_HARD", "same"), \
             mock.patch.object(evaluator, "_prepare_rag", side_effect=AssertionError("retrieval repeated")), \
             mock.patch.object(evaluator, "_claude_eval") as claude:
            result = evaluator.evaluate(
                "hello", self.direction, prepared_rag=self.prepared,
                primary_result=primary, primary_llm_meta={"stage": "batch_bulk"})
        claude.assert_not_called()
        self.assertEqual(result["retrieval_trace"]["status"], "ok")
        self.assertEqual(result["per_criterion"][0]["verdict"], "Correct")

    def test_missing_retry_reuses_exact_frozen_prompt(self):
        retry = {"per_criterion": [{
            "idx": 0, "verdict": "Correct", "confidence": .9,
            "evidence_quote": "hello", "comment": "ok",
        }], "overall_comment": ""}
        with mock.patch.object(evaluator.cc, "apply_to_direction"), \
             mock.patch.object(evaluator.config, "CLAUDE_MODEL_BULK", "same"), \
             mock.patch.object(evaluator.config, "CLAUDE_MODEL_HARD", "same"), \
             mock.patch.object(evaluator, "_prepare_rag", side_effect=AssertionError("retrieval repeated")), \
             mock.patch.object(evaluator, "_claude_eval", return_value=retry) as claude:
            evaluator.evaluate(
                "hello", self.direction, prepared_rag=self.prepared,
                primary_result={"per_criterion": [], "overall_comment": ""})
        self.assertEqual(claude.call_count, 1)
        self.assertEqual(claude.call_args.kwargs["rag_text"], "frozen policy context")

    def test_missing_retry_receives_only_its_criterion_policy_chunks(self):
        direction = {
            "id": 72,
            "criteria": [
                {**self.direction["criteria"][0]},
                {"idx": 1, "criterion_id": "criterion:payment", "name": "Payment",
                 "description": "Explain payment", "weight": 100, "is_critical": False,
                 "eval_source": "transcript"},
            ],
        }
        prepared = {
            **self.prepared,
            "rag_text": "greeting rule\n\npayment rule",
            "rag_text_by_criterion": {"0": "greeting rule", "1": "payment rule"},
        }
        primary = {"per_criterion": [{
            "idx": 0, "verdict": "Correct", "confidence": .9,
            "evidence_quote": "hello", "comment": "ok",
        }], "overall_comment": ""}
        retry = {"per_criterion": [{
            "idx": 1, "verdict": "Correct", "confidence": .9,
            "evidence_quote": "payment", "comment": "ok",
        }], "overall_comment": ""}
        with mock.patch.object(evaluator.cc, "apply_to_direction"), \
             mock.patch.object(evaluator.config, "CLAUDE_MODEL_BULK", "same"), \
             mock.patch.object(evaluator.config, "CLAUDE_MODEL_HARD", "same"), \
             mock.patch.object(evaluator, "_prepare_rag", side_effect=AssertionError("retrieval repeated")), \
             mock.patch.object(evaluator, "_claude_eval", return_value=retry) as claude:
            evaluator.evaluate("hello", direction, prepared_rag=prepared, primary_result=primary)
        self.assertEqual(claude.call_args.kwargs["rag_text"], "payment rule")
        self.assertNotIn("greeting rule", claude.call_args.kwargs["rag_text"])


if __name__ == "__main__":
    unittest.main()
