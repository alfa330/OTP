# -*- coding: utf-8 -*-
"""Очередь ревью и след ревью раздела «ИИ-оценка»: причины считаются из карточки,
подтверждение/разбор фиксируются в ai_evaluation_meta, use_count реально растёт."""
from pathlib import Path
import unittest

from call_qa.review import queue
from call_qa.rag import store

ROOT = Path(__file__).resolve().parents[1]


def _crit(idx=0, source="transcript", ai="Correct", conf=0.9, is_critical=False):
    return {"idx": idx, "source": source, "ai": ai, "conf": conf, "is_critical": is_critical}


class ReviewReasonsTests(unittest.TestCase):
    def test_clean_call_has_no_reasons(self):
        self.assertEqual(queue.review_reasons([_crit(), _crit(idx=1, ai="N/A")], 0.95), [])

    def test_critical_incorrect_flagged_first(self):
        crits = [_crit(ai="Incorrect", conf=0.3, is_critical=True), _crit(idx=1, ai="Pending", source="manual")]
        self.assertEqual(queue.review_reasons(crits, 0.9), ["critical", "lowconf", "pending"])

    def test_low_confidence_threshold_inclusive(self):
        self.assertEqual(queue.review_reasons([_crit(conf=0.6)]), ["lowconf"])
        self.assertEqual(queue.review_reasons([_crit(conf=0.61)]), [])

    def test_bad_asr_flagged(self):
        self.assertEqual(queue.review_reasons([_crit()], 0.4), ["asr"])

    def test_non_critical_incorrect_is_not_a_flag(self):
        # Уверенный Incorrect по НЕкритическому критерию — нормальный вердикт, не повод для ревью.
        self.assertEqual(queue.review_reasons([_crit(ai="Incorrect", conf=0.95)]), [])

    def test_pending_on_system_api_criteria(self):
        self.assertEqual(queue.review_reasons([_crit(source="system_api", ai="Pending", conf=None)]), ["pending"])


class EmbeddingChunkingTests(unittest.TestCase):
    def test_short_text_single_chunk(self):
        self.assertEqual(store._chunk_for_embedding("привет\nмир"), ["привет\nмир"])

    def test_long_text_covers_head_and_tail(self):
        lines = [f"[S1] реплика номер {i} " + "слово " * 80 for i in range(120)]
        chunks = store._chunk_for_embedding("\n".join(lines))
        self.assertLessEqual(len(chunks), store._EMBED_MAX_CHUNKS)
        self.assertTrue(all(len(c) <= store._EMBED_CHUNK_CHARS + 600 for c in chunks))
        self.assertIn("реплика номер 0", chunks[0])       # начало звонка сохранено
        self.assertIn("реплика номер 119", chunks[-1])    # хвост звонка сохранён


class ReviewFlowContractTests(unittest.TestCase):
    """Контракт исходников (стиль test_ai_qa_access_controls): ключевые механики на месте."""

    @classmethod
    def setUpClass(cls):
        cls.api_src = (ROOT / "call_qa" / "api.py").read_text(encoding="utf-8-sig")
        cls.store_src = (ROOT / "call_qa" / "rag" / "store.py").read_text(encoding="utf-8-sig")
        cls.eval_src = (ROOT / "call_qa" / "evaluation" / "evaluator.py").read_text(encoding="utf-8-sig")
        cls.batch_src = (ROOT / "call_qa" / "batch_eval.py").read_text(encoding="utf-8-sig")
        cls.runtime_src = (ROOT / "call_qa" / "evaluation" / "runtime_store.py").read_text(encoding="utf-8-sig")
        cls.view_src = (ROOT / "src" / "components" / "call_qa" / "CallQaView.jsx").read_text(encoding="utf-8-sig")
        cls.schema_src = (ROOT / "call_qa" / "rag" / "schema.sql").read_text(encoding="utf-8-sig")

    def test_confirm_leaves_trace(self):
        # Пустой items = подтверждение: оно тоже фиксируется и убирает звонок из очереди.
        self.assertIn('"adjudicated" if (items or []) else "confirmed"', self.api_src)
        self.assertIn("m.review_outcome IS NULL", self.api_src)

    def test_frontend_always_posts_review_result(self):
        self.assertNotIn("items.length &&", self.view_src.replace("call && items.length", ""))
        self.assertIn("'Подтверждено'", self.view_src)
        self.assertIn("карточка оставлена открытой", self.view_src)

    def test_frontend_binds_review_to_exact_run_scale_and_criterion(self):
        for field in ("evaluation_run_id: call._evaluation_run_id",
                      "scale_revision_id: call._scale_revision_id",
                      "evaluation_fingerprint: call._evaluation_fingerprint",
                      "criterion_id: c.criterion_id"):
            self.assertIn(field, self.view_src)

    def test_all_run_publishers_fail_closed_without_primary_lock(self):
        self.assertIn("conn = config.connect_rw()", self.runtime_src)
        self.assertIn("as lock_acquired", self.batch_src)
        self.assertIn("advisory lock unavailable while publishing batch run", self.batch_src)
        self.assertGreaterEqual(self.api_src.count("as lock_acquired"), 3)

    def test_use_count_incremented_on_retrieval(self):
        self.assertIn("def bump_use_count", self.store_src)
        self.assertIn("use_count = use_count + 1", self.store_src)
        self.assertIn("store.bump_use_count(used_ids)", self.eval_src)

    def test_schema_has_review_trace_columns(self):
        for col in ("review_reasons", "review_outcome", "reviewed_by", "reviewed_at"):
            self.assertIn(col, self.schema_src)
        self.assertIn("uq_ai_eval_call_model", self.schema_src)

    def test_refresh_reuses_transcript(self):
        self.assertIn('"_asm"', self.api_src)


if __name__ == "__main__":
    unittest.main()
