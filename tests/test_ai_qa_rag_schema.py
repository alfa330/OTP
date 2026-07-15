from pathlib import Path
import unittest

from call_qa.rag.migrate import split_sql_statements


ROOT = Path(__file__).resolve().parents[1]


class RagSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = (ROOT / "call_qa" / "rag" / "schema.sql").read_text(encoding="utf-8-sig")

    def test_schema_has_immutable_artifacts_and_normalized_trace(self):
        for table in (
            "ai_transcript_cache", "ai_evaluation_runs", "qa_adjudication_cases",
            "qa_policy_rules", "qa_policy_rule_versions", "qa_knowledge_snapshots",
            "qa_retrieval_runs", "qa_retrieval_hits", "qa_gold_labels",
            "qa_rag_experiments", "qa_reindex_jobs",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", self.schema)
        self.assertIn("qa_reject_append_only_mutation", self.schema)
        self.assertNotIn("UNIQUE (call_id, evaluation_fingerprint)", self.schema)

    def test_snapshot_pins_embedding_and_schema_has_ann_indexes(self):
        self.assertIn("embedding_id      bigint NOT NULL", self.schema)
        self.assertIn("JOIN qa_policy_rule_embeddings e ON e.id=sr.embedding_id", self.schema)
        self.assertIn("USING hnsw", self.schema)
        self.assertIn("embedding::vector(768)", self.schema)
        self.assertIn("uq_reindex_job_active", self.schema)
        self.assertIn("fk_policy_current_version_owner", self.schema)

    def test_active_rules_require_verified_evidence_and_ready_index(self):
        view = self.schema.split("CREATE OR REPLACE VIEW qa_active_policy_rules", 1)[1] \
            .split("CREATE OR REPLACE VIEW", 1)[0]
        self.assertIn("evidence_status IN ('verified','no_evidence')", view)
        self.assertIn("index_status = 'ready'", view)

    def test_sql_splitter_keeps_plpgsql_function_body_whole(self):
        sql = (
            "CREATE TABLE x(a text);\n"
            "CREATE FUNCTION f() RETURNS void AS $body$\n"
            "BEGIN PERFORM 1; PERFORM 'a;b'; END;\n"
            "$body$ LANGUAGE plpgsql;"
        )
        statements = split_sql_statements(sql)
        self.assertEqual(len(statements), 2)
        self.assertIn("PERFORM 1;", statements[1])
        self.assertIn("'a;b'", statements[1])


if __name__ == "__main__":
    unittest.main()
