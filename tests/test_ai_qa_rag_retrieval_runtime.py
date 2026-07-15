from datetime import datetime, timedelta, timezone
import unittest
from unittest import mock

from call_qa.evaluation import runtime_store
from call_qa.rag import store


class _Cursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def close(self):
        pass


class _Connection:
    def __init__(self, rows=()):
        self.cur = _Cursor(rows)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


class RetrievalTests(unittest.TestCase):
    def test_batch_retrieval_is_one_set_based_query_with_threshold(self):
        rule_id = "24f8ad15-888b-4ee0-8f91-5ebd3b301b31"
        rows = [
            ("c1", 0, rule_id, "11", "canonical", None, "Greeting", "late greeting",
             "hello", "Correct", "allowed after name check", None, 2, "a" * 64,
             1, .82, 1, 1, .12, .032, 1),
            ("c1", 0, "55d67ad5-61d1-4efe-90a9-2cfc0d6dce08", "12", "canonical", None,
             "Greeting", "unrelated", "bye", "Incorrect", "different case", None, 1,
             "b" * 64, 0, .31, 2, None, None, .016, 2),
        ]
        conn = _Connection(rows)
        query_batch = {"status": "ok", "vectors": [[.1, .2]],
                       "provider": {"provider": "test", "model": "m", "dim": 2,
                                    "config_hash": "c" * 64},
                       "chunks": [{"chunk_idx": 0, "start": 0, "end": 10, "chars": 10}],
                       "transcript_chars": 10, "latency_ms": 4}
        with mock.patch.object(store.config, "connect_ro", return_value=conn):
            result = store.retrieve_for_criteria_batch(
                direction_id=72, criteria=[{"idx": 0, "criterion_id": "c1"}],
                query_batch=query_batch, min_similarity=.68, query_text="late greeting")
        self.assertEqual(len(conn.cur.executed), 1)
        self.assertIn("jsonb_to_recordset", conn.cur.executed[0][0])
        self.assertEqual(result["status"], "ok")
        self.assertEqual([hit["rule_id"] for hit in result["hits_by_criterion"][0]], [rule_id])
        self.assertTrue(result["trace"]["candidates"][0]["included"])
        self.assertEqual(result["trace"]["candidates"][1]["reject_reason"], "below_threshold")
        self.assertEqual(result["trace"]["query"]["sql_queries"], 1)

    def test_embedding_degradation_never_queries_recent_rows(self):
        query_batch = {"status": "degraded", "vectors": [], "chunks": [],
                       "error": "provider down", "latency_ms": 1}
        with mock.patch.object(store.config, "connect_ro") as connect:
            result = store.retrieve_for_criteria_batch(
                direction_id=72, criteria=[{"idx": 0, "criterion_id": "c1"}],
                query_batch=query_batch)
        connect.assert_not_called()
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["hits_by_criterion"][0], [])

    def test_long_call_samples_the_middle_not_only_head_and_tail(self):
        text = "0123456789" * 5000
        with mock.patch.object(store, "_EMBED_CHUNK_CHARS", 500), \
             mock.patch.object(store, "_EMBED_CHUNK_OVERLAP", 50), \
             mock.patch.object(store, "_EMBED_MAX_CHUNKS", 9):
            manifest = store._chunk_manifest(text)
        self.assertEqual(len(manifest), 9)
        centers = [(item["start"] + item["end"]) / 2 for item in manifest]
        self.assertLess(abs(centers[len(centers) // 2] - len(text) / 2), len(text) * .1)
        self.assertEqual(manifest[0]["start"], 0)
        self.assertEqual(manifest[-1]["end"], len(text))


class RuntimeTraceTests(unittest.TestCase):
    def test_llm_failure_without_trace_is_not_mislabeled_as_retrieval_failure(self):
        cur = _Cursor()
        now = datetime.now(timezone.utc)
        runtime_store._insert_retrieval_trace(
            cur, trace={}, evaluation_run_id="84a25693-c571-42bc-87c8-e1cfe3d5f95d",
            call_id=10, direction_id=72, knowledge_snapshot_id=4,
            transcript_hash="a" * 64, fallback_config={"enabled": True},
            completed_at=now, evaluation_succeeded=False,
        )
        params = cur.executed[0][1]
        self.assertEqual(params[9], "skipped")
        self.assertIsNone(params[10])

    def test_distributed_lock_uses_primary_and_reports_acquisition(self):
        conn = _Connection()
        with mock.patch.object(runtime_store.config, "connect_rw", return_value=conn), \
             mock.patch.object(runtime_store.config, "connect_ro") as connect_ro:
            with runtime_store.distributed_call_lock(10) as acquired:
                self.assertTrue(acquired)
        connect_ro.assert_not_called()
        sql = " ".join(statement for statement, _ in conn.cur.executed)
        self.assertIn("pg_advisory_lock", sql)
        self.assertIn("pg_advisory_unlock", sql)

    def test_adjudication_source_loads_exact_immutable_run_transcript_and_scale(self):
        run_id = "84a25693-c571-42bc-87c8-e1cfe3d5f95d"
        now = datetime.now(timezone.utc)
        run_row = (
            run_id, 10, 72, 3, "a" * 64, "b" * 64, 7,
            "succeeded", "force", "qa-model", {"criteria": []}, [], now,
            3, "a" * 64, "immutable transcript", [],
            7, 72, "c" * 64, True, None,
        )
        scale_row = (
            "d72-greeting", 4, "Приветствие", "description", 1, True,
            None, "transcript", {},
        )
        conn = _Connection([run_row, scale_row])
        with mock.patch.object(runtime_store.config, "connect_rw", return_value=conn):
            source = runtime_store.get_adjudication_source(run_id)
        self.assertEqual(source["id"], run_id)
        self.assertEqual(source["transcript"]["text"], "immutable transcript")
        self.assertEqual(source["scale"]["criteria"][0]["criterion_id"], "d72-greeting")
        self.assertTrue(source["is_latest"])
        sql = " ".join(statement for statement, _ in conn.cur.executed)
        self.assertIn("FROM ai_evaluation_runs e", sql)
        self.assertIn("NOT EXISTS", sql)
        self.assertNotIn("newer.model=e.model", sql)
        self.assertIn("FROM qa_scale_revision_criteria", sql)
        self.assertNotIn("ai_review_cache", sql)

    def test_adjudication_source_rejects_invalid_run_id_without_db(self):
        with mock.patch.object(runtime_store.config, "connect_rw") as connect:
            self.assertIsNone(runtime_store.get_adjudication_source("not-a-uuid"))
        connect.assert_not_called()

    def test_degraded_rag_run_is_not_a_reusable_cache_hit(self):
        conn = _Connection()
        with mock.patch.object(runtime_store.config, "connect_ro", return_value=conn):
            self.assertIsNone(runtime_store.get_cached_evaluation(
                call_id=10, evaluation_fingerprint="b" * 64))
        sql = conn.cur.executed[0][0]
        self.assertIn("retrieval_config->>'enabled'", sql)
        self.assertIn("_retrieval_trace", sql)
        self.assertIn("degraded", sql)

    def test_cached_run_exposes_binding_manifest_and_global_freshness(self):
        run_id = "84a25693-c571-42bc-87c8-e1cfe3d5f95d"
        manifest = [{"criterion_id": "c1", "criterion_idx": 0}]
        conn = _Connection([(
            run_id, {"criteria": []}, 1, 2, 7, datetime.now(timezone.utc),
            "standard", 3, None, "b" * 64, manifest, True,
        )])
        with mock.patch.object(runtime_store.config, "connect_ro", return_value=conn):
            result = runtime_store.get_cached_evaluation(
                call_id=10, evaluation_fingerprint="b" * 64)
        self.assertEqual(result["id"], run_id)
        self.assertEqual(result["evaluation_fingerprint"], "b" * 64)
        self.assertEqual(result["scale_manifest"], manifest)
        self.assertTrue(result["is_latest"])

    def test_run_and_normalized_trace_are_inserted_atomically(self):
        conn = _Connection()
        now = datetime.now(timezone.utc)
        rule_id = "24f8ad15-888b-4ee0-8f91-5ebd3b301b31"
        payload = {"criteria": [], "_retrieval_trace": {
            "status": "ok", "config": {"top_k": 3}, "latency_ms": 12,
            "query": {"chunks": [{"chunk_idx": 0, "start": 0, "end": 20}]},
            "candidates": [{"criterion_id": "c1", "rule_id": rule_id,
                            "rule_version_id": 7, "rank": 1, "dense_rank": 1,
                            "similarity": .8, "fused_score": .03, "included": True,
                            "source_type": "canonical"}],
        }}
        with mock.patch.object(runtime_store.config, "connect_rw", return_value=conn):
            runtime_store.save_evaluation_run(
                run_id="84a25693-c571-42bc-87c8-e1cfe3d5f95d", call_id=10,
                direction_id=72, transcript_cache_id=3, transcript_hash="a" * 64,
                evaluation_fingerprint="b" * 64,
                fingerprint_components={"fingerprint_version": 2, "model_config": {}},
                run_kind="standard", model="m", model_config_hash="c" * 64,
                prompt_hash="d" * 64, output_schema_hash="e" * 64,
                output_schema_version="v2", criteria_hash="f" * 64,
                criterion_config_hash="1" * 64, scale_revision_id=2,
                knowledge_snapshot_id=4, knowledge_revision=5,
                retrieval_config={"enabled": True}, status="succeeded",
                per_criterion=[], payload=payload, started_at=now,
                completed_at=now + timedelta(milliseconds=30))
        sql = " ".join(statement for statement, _ in conn.cur.executed)
        self.assertIn("INSERT INTO ai_evaluation_runs", sql)
        self.assertIn("INSERT INTO qa_retrieval_runs", sql)
        self.assertIn("INSERT INTO qa_retrieval_hits", sql)
        self.assertIn("successful_evaluation_count", sql)
        self.assertTrue(conn.closed)


if __name__ == "__main__":
    unittest.main()
