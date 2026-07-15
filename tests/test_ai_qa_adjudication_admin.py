# -*- coding: utf-8 -*-
"""Правка/удаление разборов (RAG): доступ строго у супер-админа, embedding
пересчитывается при правке, валидация патча."""
from pathlib import Path
import ast
from types import SimpleNamespace
import unittest
from unittest import mock

from call_qa import api as call_qa_api
from call_qa.api import _clean_adjudication_patch
from call_qa.rag import knowledge
from call_qa.rag import store

ROOT = Path(__file__).resolve().parents[1]


class CleanPatchTests(unittest.TestCase):
    def test_allows_only_editable_fields(self):
        patch = _clean_adjudication_patch({
            "correct_verdict": "N/A", "reason": " новое правило ", "situation": "",
            "id": 999, "call_id": 1, "embedding": "hack", "created_by": 7,
        })
        self.assertEqual(patch, {"correct_verdict": "N/A", "reason": "новое правило", "situation": None})

    def test_invalid_verdict_rejected(self):
        with self.assertRaises(ValueError):
            _clean_adjudication_patch({"correct_verdict": "Deficiency"})

    def test_empty_reason_rejected(self):
        with self.assertRaises(ValueError):
            _clean_adjudication_patch({"reason": "   "})

    def test_empty_patch_rejected(self):
        with self.assertRaises(ValueError):
            _clean_adjudication_patch({})
        with self.assertRaises(ValueError):
            _clean_adjudication_patch({"embedding": "x"})

    def test_optional_fields_empty_string_becomes_null(self):
        patch = _clean_adjudication_patch({"not_covered": "", "situation": "звонок-возврат"})
        self.assertEqual(patch, {"not_covered": None, "situation": "звонок-возврат"})

    def test_rejects_non_object_body_and_non_string_fields(self):
        for body in (None, [], "reason", 1):
            with self.subTest(body=body), self.assertRaises(ValueError):
                _clean_adjudication_patch(body)

    def test_stale_canonical_edit_is_rejected_before_embedding(self):
        source = {
            "direction_id": 73, "rule_status": "active", "rule_version_id": 12,
            "content_hash": "new-hash",
        }
        with mock.patch.object(call_qa_api, "_canonical_rule_source", return_value=source), \
             mock.patch("call_qa.rag.store.embed_document_text") as embed, \
             mock.patch.object(call_qa_api.config, "connect_rw") as connect, \
             self.assertRaisesRegex(knowledge.KnowledgeConflict, "изменено другим пользователем"):
            call_qa_api.update_adjudication("00000000-0000-0000-0000-000000000001", {
                "reason": "новое правило",
                "expected_rule_version_id": 11,
                "expected_content_hash": "old-hash",
            }, actor_id=7)
        embed.assert_not_called()
        connect.assert_not_called()
        for body in ({"reason": 123}, {"situation": ["x"]}, {"correct_verdict": True}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                _clean_adjudication_patch(body)


class _FakeCursor:
    def __init__(self, rows=(), error=None):
        self.rows = list(rows)
        self.error = error
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        if self.error:
            raise self.error

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class _FakeConnection:
    def __init__(self, rows=(), error=None):
        self.cur = _FakeCursor(rows, error)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


class StoreMutationTests(unittest.TestCase):
    def test_missing_update_closes_connection(self):
        conn = _FakeConnection()
        with mock.patch.object(store.config, "connect_rw", return_value=conn):
            self.assertFalse(store.update_adjudication(404, {"reason": "новое правило"}))
        self.assertTrue(conn.closed)

    def test_embedding_failure_aborts_semantic_update(self):
        conn = _FakeConnection([("Критерий", "ситуация", "цитата", "старое", None, "Correct", None)])
        with mock.patch.object(store.config, "connect_rw", return_value=conn), \
             mock.patch.object(store, "_embed", return_value=None), \
             self.assertRaises(store.AdjudicationEmbeddingUnavailable):
            store.update_adjudication(7, {"reason": "новое правило"})
        self.assertTrue(conn.closed)
        self.assertFalse(any(sql.startswith("UPDATE qa_adjudications") for sql, _ in conn.cur.executed))

    def test_semantic_update_embeds_outside_lock_and_closes_connections(self):
        read_conn = _FakeConnection([("Критерий", "ситуация", "цитата", "старое", None, "Correct", None)])
        write_conn = _FakeConnection([(7,)])
        with mock.patch.object(store.config, "connect_rw", side_effect=[read_conn, write_conn]), \
             mock.patch.object(store, "_embed", return_value=[0.1, 0.2]):
            self.assertTrue(store.update_adjudication(7, {"reason": "новое правило"}))
        self.assertTrue(read_conn.closed)
        self.assertTrue(write_conn.closed)
        all_sql = " ".join(sql for conn in (read_conn, write_conn) for sql, _ in conn.cur.executed)
        self.assertNotIn("FOR UPDATE", all_sql)
        self.assertIn("embedding=%s::vector", all_sql)
        self.assertIn("IS NOT DISTINCT FROM", all_sql)

    def test_verdict_only_change_does_not_call_embed(self):
        read_conn = _FakeConnection([("Критерий", "ситуация", "цитата", "правило", None, "Correct", None)])
        write_conn = _FakeConnection([(7,)])
        with mock.patch.object(store.config, "connect_rw", side_effect=[read_conn, write_conn]), \
             mock.patch.object(store, "_embed") as embed:
            self.assertTrue(store.update_adjudication(7, {
                "correct_verdict": "Incorrect", "reason": "правило",
                "situation": "ситуация", "not_covered": None,
            }))
        embed.assert_not_called()

    def test_concurrent_change_returns_conflict_without_retry(self):
        read_conn = _FakeConnection([("Критерий", "ситуация", "цитата", "старое", None, "Correct", None)])
        write_conn = _FakeConnection()
        with mock.patch.object(store.config, "connect_rw", side_effect=[read_conn, write_conn]) as connect, \
             mock.patch.object(store, "_embed", return_value=[0.1, 0.2]), \
             self.assertRaises(store.AdjudicationConflict):
            store.update_adjudication(7, {"reason": "новое правило"})
        self.assertEqual(connect.call_count, 2)
        write_sql, write_params = write_conn.cur.executed[-1]
        self.assertIn("correct_verdict IS NOT DISTINCT FROM %s", write_sql)
        self.assertIn("situation_tag IS NOT DISTINCT FROM %s", write_sql)
        self.assertIn("Correct", write_params)

    def test_delete_is_soft_and_closes_connection(self):
        conn = _FakeConnection([(7,)])
        with mock.patch.object(store.config, "connect_rw", return_value=conn):
            self.assertTrue(store.delete_adjudication(7))
        self.assertTrue(conn.closed)
        sql = " ".join(statement for statement, _ in conn.cur.executed)
        self.assertIn("SET is_active=FALSE", sql)
        self.assertNotIn("DELETE FROM qa_adjudications", sql)


class AdminAccessContractTests(unittest.TestCase):
    """Контракт исходников: управление разборами — только супер-админ."""

    @classmethod
    def setUpClass(cls):
        cls.api_src = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        cls.store_src = (ROOT / "call_qa" / "rag" / "store.py").read_text(encoding="utf-8-sig")
        cls.view_src = (ROOT / "src" / "components" / "call_qa" / "CallQaView.jsx").read_text(encoding="utf-8-sig")
        cls.rag_src = (ROOT / "src" / "components" / "call_qa" / "AdjudicationsRag.jsx").read_text(encoding="utf-8-sig")
        cls.schema_src = (ROOT / "call_qa" / "rag" / "schema.sql").read_text(encoding="utf-8-sig")

    def test_manage_route_uses_admin_guard(self):
        self.assertIn("'/api/ai-qa/adjudications/<int:adj_id>'", self.api_src)
        self.assertIn("def _ai_qa_admin_guard():", self.api_src)
        route = self.api_src.split("def api_ai_qa_adjudication_manage", 1)[1].split("@app.route", 1)[0]
        self.assertIn("_ai_qa_admin_guard()", route)

    def test_admin_guard_is_super_admin_only(self):
        guard = self.api_src.split("def _ai_qa_admin_guard():", 1)[1].split("\n@app.route", 1)[0]
        self.assertIn("_is_super_admin_role(role)", guard)
        # Ни доп-доступ (183), ни глава отдела не проходят в управление базой правил.
        self.assertNotIn("AI_QA_EXTRA_ACCESS_USER_IDS", guard)
        self.assertNotIn("_headed_department_id", guard)

    def test_update_recomputes_embedding(self):
        upd = self.store_src.split("def update_adjudication", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("vec = _embed", upd)
        self.assertIn("vec=vec", upd)
        self.assertIn("EDITABLE_ADJ_FIELDS", upd)

    def test_frontend_gates_manage_by_super_admin(self):
        self.assertIn("normalizeRole(user?.role) === 'super_admin'", self.view_src)
        self.assertIn("canManage={canManageRag}", self.view_src)
        self.assertIn("canManage && (", self.rag_src)
        self.assertIn("window.confirm", self.rag_src)
        self.assertIn("expected_rule_version_id", self.rag_src)
        self.assertIn("expected_content_hash", self.rag_src)

    def test_soft_delete_is_filtered_from_rag_but_kept_for_review_history(self):
        self.assertIn("is_active        boolean NOT NULL DEFAULT true", self.schema_src)
        self.assertIn("WHERE a.is_active", (ROOT / "call_qa" / "api.py").read_text(encoding="utf-8-sig"))
        self.assertIn("AND is_active", self.store_src)


class AdminGuardBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        node = next(item for item in tree.body
                    if isinstance(item, ast.FunctionDef) and item.name == "_ai_qa_admin_guard")
        node.decorator_list = []
        cls.code = compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                           str(ROOT / "bot_schedule2.py"), "exec")

    def _guard(self, role):
        user = (1, "Имя", "login", role)
        ns = {
            "g": SimpleNamespace(user_id=1),
            "db": SimpleNamespace(get_user=lambda **_: user),
            "_normalize_user_role": lambda value: str(value or "").strip().lower().replace("super-admin", "super_admin"),
            "_is_super_admin_role": lambda value: value in ("super_admin", "superadmin", "super admin"),
            "jsonify": lambda value: value,
        }
        exec(self.code, ns)
        return ns["_ai_qa_admin_guard"]()

    def test_super_admin_allowed(self):
        self.assertEqual(self._guard("super_admin"), (1, None))

    def test_admin_and_extra_access_role_forbidden(self):
        for role in ("admin", "sv", "operator"):
            with self.subTest(role=role):
                requester, error = self._guard(role)
                self.assertIsNone(requester)
                self.assertEqual(error[1], 403)


if __name__ == "__main__":
    unittest.main()
