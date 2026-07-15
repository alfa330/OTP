from contextlib import nullcontext
import unittest
from unittest.mock import MagicMock, patch

from call_qa import api
from call_qa.rag import knowledge


RUN_ID = "84a25693-c571-42bc-87c8-e1cfe3d5f95d"
FINGERPRINT = "f" * 64
TRANSCRIPT = "[S1] Здравствуйте, меня зовут Анна.\n[S2] Добрый день."
TRANSCRIPT_HASH = api.content_hash(TRANSCRIPT)


def _source(**overrides):
    payload = {
        "id": 10,
        "direction_id": 72,
        "criteria": [{
            "idx": 0, "criterion_id": "d72-greeting", "name": "Приветствие",
            "source": "transcript", "ai": "Incorrect",
        }],
        "_evaluation_run_id": RUN_ID,
        "_scale_revision_id": 7,
        "_evaluation_fingerprint": FINGERPRINT,
        "_transcript_hash": TRANSCRIPT_HASH,
    }
    source = {
        "id": RUN_ID, "call_id": 10, "direction_id": 72,
        "transcript_cache_id": 3, "transcript_hash": TRANSCRIPT_HASH,
        "evaluation_fingerprint": FINGERPRINT, "scale_revision_id": 7,
        "status": "succeeded", "run_kind": "standard", "model": "qa-model",
        "payload": payload, "per_criterion": [], "is_latest": True,
        "transcript": {
            "id": 3, "transcript_hash": TRANSCRIPT_HASH, "text": TRANSCRIPT,
            "segments": [{
                "speaker": "operator", "seg": [{"t": "Здравствуйте, меня зовут Анна."}],
            }],
        },
        "scale": {
            "id": 7, "direction_id": 72, "content_hash": "s" * 64,
            "criteria": [{
                "criterion_id": "d72-greeting", "criterion_idx": 0,
                "criterion_name": "Приветствие", "eval_source": "transcript",
            }],
        },
    }
    source.update(overrides)
    return source


def _valid_item(**overrides):
    item = {
        "criterion_id": "d72-greeting",
        "criterion_idx": 0,
        "criterion_name": "Подменённое имя",
        "ai_verdict": "Correct",
        "correct_verdict": "Correct",
        "reason": "Оператор представился",
        "excerpt": "Здравствуйте меня зовут Анна",
        "excerpt_verified": True,
        "evidence_status": "verified",
    }
    item.update(overrides)
    return item


class AdjudicationValidationTests(unittest.TestCase):
    def _validate(self, item=None, *, source=None, call_id=10, direction_id=72,
                  scale_revision_id=7, fingerprint=FINGERPRINT):
        # A regression guard: neither the mutable projection nor the current
        # direction/criterion order may participate in adjudication.
        with patch.object(api.runtime_store, "get_adjudication_source",
                          return_value=source or _source()), \
             patch.object(api, "_cache_get", side_effect=AssertionError("mutable cache used")), \
             patch.object(api.criteria_mod, "load_direction",
                          side_effect=AssertionError("current scale used")):
            return api._validated_adjudication_items(
                call_id, direction_id, [] if item is None else [item],
                evaluation_run_id=RUN_ID, scale_revision_id=scale_revision_id,
                evaluation_fingerprint=fingerprint)

    def test_uses_authoritative_immutable_criterion_and_ai_verdict(self):
        payload, rows = self._validate(_valid_item())
        self.assertEqual(rows[0]["criterion_name"], "Приветствие")
        self.assertEqual(rows[0]["ai_verdict"], "Incorrect")
        self.assertEqual(rows[0]["criterion_id"], "d72-greeting")
        self.assertEqual(payload["_evaluation_run_id"], RUN_ID)
        self.assertEqual(payload["_scale_revision_id"], 7)
        self.assertIsInstance(rows[0]["excerpt_start"], int)

    def test_stale_run_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "устарела"):
            self._validate(_valid_item(), source=_source(is_latest=False))

    def test_already_reviewed_run_is_rejected_before_embedding(self):
        with self.assertRaisesRegex(ValueError, "уже проверена"):
            self._validate(_valid_item(), source=_source(review_outcome="confirmed"))

    def test_fingerprint_is_required(self):
        with self.assertRaisesRegex(ValueError, "evaluation_fingerprint обязателен"):
            self._validate(_valid_item(), fingerprint=None)

    def test_call_direction_scale_and_fingerprint_must_match_run(self):
        cases = (
            ({"call_id": 11}, "call_id"),
            ({"direction_id": 73}, "направление"),
            ({"scale_revision_id": 8}, "scale_revision_id"),
            ({"fingerprint": "0" * 64}, "evaluation_fingerprint"),
        )
        for kwargs, message in cases:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, message):
                self._validate(_valid_item(), **kwargs)

    def test_criterion_id_and_exact_historical_idx_must_match(self):
        with self.assertRaisesRegex(ValueError, "устарел|не совпадает"):
            self._validate(_valid_item(criterion_idx=1))
        with self.assertRaisesRegex(ValueError, "criterion_id обязателен"):
            self._validate(_valid_item(criterion_id=None))

    def test_empty_reason_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "обоснование"):
            self._validate(_valid_item(reason=""))

    def test_model_quote_absent_from_immutable_transcript_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "не найдена"):
            self._validate(_valid_item(excerpt="Здравствуйте меня зовут Мария"))

    def test_transcript_content_hash_must_match_run(self):
        broken = _source(transcript={
            "id": 3, "transcript_hash": TRANSCRIPT_HASH, "text": "другой текст",
            "segments": [],
        })
        with self.assertRaisesRegex(ValueError, "immutable transcript"):
            self._validate(_valid_item(), source=broken)

    def test_confirm_without_corrections_is_still_bound_to_run(self):
        payload, rows = self._validate()
        self.assertEqual(rows, [])
        self.assertEqual(payload["_evaluation_fingerprint"], FINGERPRINT)

    def test_cached_card_binding_is_hydrated_from_exact_scale_manifest(self):
        card = {"criteria": [{
            "idx": 0, "name": "Приветствие", "source": "transcript",
        }]}
        cached_run = {
            "id": RUN_ID, "scale_revision_id": 7,
            "evaluation_fingerprint": FINGERPRINT,
            "scale_manifest": [{
                "criterion_id": "d72-greeting", "criterion_idx": 0,
                "criterion_name": "Приветствие", "eval_source": "transcript",
            }],
        }
        self.assertTrue(api._hydrate_cached_card_binding(card, cached_run))
        self.assertEqual(card["criteria"][0]["criterion_id"], "d72-greeting")
        self.assertEqual(card["_evaluation_run_id"], RUN_ID)
        self.assertEqual(card["_scale_revision_id"], 7)

    def test_incompatible_cached_card_is_not_hydrated(self):
        card = {"criteria": [{
            "idx": 0, "name": "Другой критерий", "source": "transcript",
        }]}
        cached_run = {
            "id": RUN_ID, "scale_revision_id": 7,
            "evaluation_fingerprint": FINGERPRINT,
            "scale_manifest": [{
                "criterion_id": "d72-greeting", "criterion_idx": 0,
                "criterion_name": "Приветствие", "eval_source": "transcript",
            }],
        }
        self.assertFalse(api._hydrate_cached_card_binding(card, cached_run))

    def test_save_persists_run_scale_not_current_reordered_scale(self):
        payload = {
            "id": 10, "direction_id": 72, "_evaluation_run_id": RUN_ID,
            "_scale_revision_id": 7, "_evaluation_model": "qa-model",
            "_transcript_hash": TRANSCRIPT_HASH,
            "_authoritative_transcript_text": TRANSCRIPT, "transcript": [],
        }
        validated = [{
            "criterion_id": "d72-greeting", "criterion_idx": 0,
            "criterion_name": "Приветствие", "ai_verdict": "Incorrect",
            "correct_verdict": "Correct", "reason": "Оператор представился",
            "situation": None, "not_covered": None, "excerpt": "",
            "excerpt_verified": False, "evidence_status": "no_evidence",
            "excerpt_start": None, "excerpt_end": None,
        }]

        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def execute(self, *_): pass
            def fetchone(self): return None

        class Connection:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def cursor(self): return Cursor()
            def close(self): pass

        provider = MagicMock(metadata={"provider": "test", "model": "m", "dim": 2})
        provider.embed_document.return_value = [[0.1, 0.2]]
        create_case = MagicMock(return_value="case-id")
        with patch.object(api, "_validated_adjudication_items",
                          return_value=(payload, validated)), \
             patch.object(api.config, "connect_rw", return_value=Connection()), \
             patch("call_qa.embeddings.provider.get_provider", return_value=provider), \
             patch.object(knowledge, "rule_document_text", return_value="document"), \
             patch.object(knowledge, "create_adjudication_case", create_case), \
             patch.object(knowledge, "create_draft_policy_rule",
                          return_value={"rule_version_id": 9}), \
             patch.object(knowledge, "record_rule_embedding"), \
             patch.object(knowledge, "ensure_knowledge_context",
                          side_effect=AssertionError("current scale used")), \
             patch.object(api.criteria_mod, "load_direction",
                          side_effect=AssertionError("current scale used")), \
             patch.object(api, "_claim_review_outcome"), \
             patch.object(api, "_record_review_outcome"), \
             patch.object(api, "_record_rule_review_feedback"):
            saved = api._save_adjudications_locked(
                10, 72, [{}], evaluation_run_id=RUN_ID, scale_revision_id=7)
        self.assertEqual(saved, 1)
        self.assertEqual(create_case.call_args.kwargs["scale_revision_id"], 7)
        self.assertEqual(create_case.call_args.kwargs["evaluation_run_id"], RUN_ID)

    def test_review_claim_rejects_repeat_submit_atomically(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        with self.assertRaisesRegex(ValueError, "уже проверена"):
            api._claim_review_outcome(
                cursor, call_id=10, outcome="confirmed", reviewer_id=5,
                payload={"direction_id": 72, "criteria": []}, model="qa-model")
        sql = " ".join(cursor.execute.call_args.args[0].split())
        self.assertIn("ON CONFLICT (call_id,model) DO UPDATE", sql)
        self.assertIn("WHERE ai_evaluation_meta.review_outcome IS NULL", sql)
        self.assertIn("RETURNING id", sql)

    def test_case_dedup_identity_includes_evaluation_run(self):
        captured = {}

        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def execute(self, *_): pass
            def fetchone(self): return ("case-id",)

        class Connection:
            def cursor(self): return Cursor()

        def digest(value):
            captured.update(value)
            return "d" * 64

        with patch.object(knowledge, "content_hash", side_effect=digest):
            knowledge.create_adjudication_case(
                Connection(), direction_id=72, criterion_id="d72-greeting",
                correct_verdict="Correct", evidence_excerpt="", reason="rule",
                call_id=10, evaluation_run_id=RUN_ID, evidence_status="no_evidence")
        self.assertEqual(captured["evaluation_run_id"], RUN_ID)

    def test_save_fails_closed_when_refresh_lock_is_unavailable(self):
        with patch.object(api.runtime_store, "distributed_call_lock",
                          return_value=nullcontext(False)), \
             patch.object(api, "_save_adjudications_locked") as save_locked, \
             self.assertRaisesRegex(RuntimeError, "заблокировать evaluation_run"):
            api.save_adjudications(
                10, 72, [], evaluation_run_id=RUN_ID, scale_revision_id=7)
        save_locked.assert_not_called()

    def test_evaluation_publisher_fails_closed_when_lock_is_unavailable(self):
        with patch.object(api.runtime_store, "distributed_call_lock",
                          return_value=nullcontext(False)), \
             patch.object(api, "_evaluate_and_cache") as evaluate, \
             self.assertRaisesRegex(RuntimeError, "безопасной оценки"):
            api.review_payload(10)
        evaluate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
