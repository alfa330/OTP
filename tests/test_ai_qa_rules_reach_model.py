"""Разбор модератора доходит до ИИ (жалоба модератора 23.10.2026, звонок 11457 и
переписка 61480): поиск правил по смыслу, правила самого разговора — всегда,
копии не занимают мест, даты у прецедентов, повторный разбор правит прежнее
правило, «Сформулировать» видит разговор."""
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock
from unittest.mock import MagicMock, patch

import psycopg2

from call_qa import api
from call_qa.evaluation import evaluator, fingerprint
from call_qa.rag import knowledge, refine, store

import source_cache

ROOT = Path(__file__).resolve().parents[1]


RUN_ID = "84a25693-c571-42bc-87c8-e1cfe3d5f95d"
PROVIDER = {"provider": "test", "model": "m", "dim": 2, "config_hash": "c" * 64}
QUERY_BATCH = {"status": "ok", "vectors": [[.1, .2]], "provider": PROVIDER,
               "chunks": [{"chunk_idx": 0, "start": 0, "end": 10, "chars": 10}],
               "transcript_chars": 10, "latency_ms": 4}


class _Cursor:
    """Отдаёт строки по тексту запроса и сверяет число %s с параметрами."""

    def __init__(self, answers):
        self.answers = answers
        self.executed = []
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        if params is not None and not isinstance(params, dict):
            placeholders = sql.count("%s")
            if placeholders != len(params):
                raise AssertionError(f"плейсхолдеров {placeholders}, параметров {len(params)}")
        self.executed.append((" ".join(sql.split()), params))
        self._rows = []
        for marker, rows in self.answers:
            if marker in sql:
                if isinstance(rows, Exception):
                    raise rows
                self._rows = list(rows)
                break

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def close(self):
        pass


class _Connection:
    def __init__(self, answers=()):
        self.cur = _Cursor(list(answers))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self.cur

    def close(self):
        pass


def _found(rule_id, rank, *, verdict="N/A", text=None, similarity=.8, lexical_rank=None,
           created=datetime(2026, 10, 20, 5, 0, tzinfo=timezone.utc)):
    """Строка основного запроса (_BATCH_COLUMNS), порядок — как отдал SQL."""
    return ("c17", 17, rule_id, str(rank), "canonical", None, "КО_Альтернатива",
            f"ситуация {rule_id}", None, verdict, text or f"правило про случай {rule_id * 3}", None, 1,
            "h" * 64, 0, similarity, rank, lexical_rank, None, rank, created)


def _own(rule_id, *, verdict="N/A", text=None, similarity=.66,
         created=datetime(2026, 10, 23, 4, 10, tzinfo=timezone.utc)):
    """Строка запроса правил самого разговора (_OWN_COLUMNS), новые сверху."""
    return ("c17", 17, rule_id, "900", "canonical", None, "КО_Альтернатива",
            f"ситуация {rule_id}", None, verdict, text or f"правило про случай {rule_id * 3}", None, 2,
            "o" * 64, similarity, created)


def _retrieve(found, own=(), *, subject=True, own_error=None):
    answers = [("own_versions AS", own_error or own), ("dense_ranked AS", found)]
    conn = _Connection(answers)
    with mock.patch.object(store.config, "connect_ro", return_value=conn):
        result = store.retrieve_for_criteria_batch(
            direction_id=73, criteria=[{"idx": 17, "criterion_id": "c17"}],
            query_batch=QUERY_BATCH, min_similarity=.68, query_text="Индрайвер",
            knowledge_snapshot_id=464,
            subject_kind="imported_call" if subject else None,
            subject_id=11457 if subject else None)
    return result, conn


class RetrievalOrderTests(unittest.TestCase):
    def test_sql_orders_by_meaning_not_by_word_overlap(self):
        sql = " ".join(store._BATCH_RETRIEVAL_SQL.split())
        # Слияние рангов «смысл + слова» (RRF) поднимало правило с любым совпавшим
        # словом над самым близким по смыслу — его больше нет.
        self.assertNotIn("1.0 / (60", sql)
        self.assertNotIn("fused", sql)
        self.assertIn("d.dense_rank AS final_rank", sql)
        self.assertIn("ORDER BY d.requested_criterion_idx, d.dense_rank", sql)
        self.assertIn("pv.created_at AS version_created_at", sql)

    def test_three_closest_by_meaning_without_copies(self):
        found = [
            _found("a", 1, text="Направить к сторонней организации достаточно"),
            # Копия «a»: тот же вердикт, та же формулировка — места не занимает.
            _found("b", 2, text="Направить к сторонней  организации достаточно."),
            _found("c", 3, verdict="Correct"),
            _found("d", 4, lexical_rank=1),
            _found("e", 5),
            _found("f", 6, similarity=.5),
        ]
        result, conn = _retrieve(found, subject=False)
        hits = [hit["rule_id"] for hit in result["hits_by_criterion"][17]]
        self.assertEqual(hits, ["a", "c", "d"])
        reasons = {c["rule_id"]: c["reject_reason"] for c in result["trace"]["candidates"]}
        self.assertEqual(reasons, {"a": None, "b": "duplicate_rule", "c": None, "d": None,
                                   "e": "top_k_exceeded", "f": "below_threshold"})
        # Без разговора — один запрос, правила разговора не ищутся.
        self.assertEqual(len(conn.cur.executed), 1)
        self.assertEqual(result["trace"]["config"]["pipeline"], store.PIPELINE_VERSION)

    def test_rules_from_the_conversation_itself_are_always_in_and_extra(self):
        found = [_found("near", 1), _found("own-new", 2, text="Назвать организацию — корректно"),
                 _found("x", 3), _found("y", 4),
                 _found("z", 5)]
        own = [
            _own("own-new", text="Назвать организацию — корректно"),
            _own("own-copy", text="Назвать организацию — корректно",
                 created=datetime(2026, 10, 23, 3, 54, tzinfo=timezone.utc)),
            _own("own-old", text="Направление в поддержку стороннего сервиса — достаточно",
                 created=datetime(2026, 10, 23, 3, 50, tzinfo=timezone.utc)),
        ]
        result, conn = _retrieve(found, own)
        hits = result["hits_by_criterion"][17]
        # Свои — первыми (новые сверху, без копии), затем три по смыслу без них.
        self.assertEqual([hit["rule_id"] for hit in hits], ["own-new", "own-old", "near", "x", "y"])
        self.assertEqual([hit["own_rule"] for hit in hits], [True, True, False, False, False])
        by_rule = {c["rule_id"]: c for c in result["trace"]["candidates"]}
        self.assertEqual(by_rule["own-copy"]["reject_reason"], "duplicate_rule")
        self.assertFalse(by_rule["own-copy"]["included"])
        self.assertEqual(by_rule["z"]["reject_reason"], "top_k_exceeded")
        # Своё правило из окна поиска сохраняет ранг по смыслу; из-за окна — ранг за ним.
        self.assertEqual(by_rule["own-new"]["dense_rank"], 2)
        self.assertEqual((by_rule["own-copy"]["rank"], by_rule["own-old"]["rank"]), (6, 7))
        self.assertIsNone(by_rule["own-old"]["dense_rank"])
        # Трасса уходит в jsonb и в манифест пакета.
        json.dumps(result["trace"])
        self.assertEqual(hits[0]["created_at"], "2026-10-20T05:00:00+00:00")
        own_sql, own_params = conn.cur.executed[1]
        self.assertIn("c.subject_kind=%s AND c.call_id=%s", own_sql)
        self.assertEqual(own_params[2:4], ("imported_call", 11457))
        self.assertEqual(result["trace"]["query"]["sql_queries"], 2)

    def test_own_rules_failure_keeps_the_regular_search(self):
        result, _ = _retrieve([_found("near", 1)],
                              own_error=psycopg2.errors.UndefinedColumn("no column"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual([hit["rule_id"] for hit in result["hits_by_criterion"][17]], ["near"])
        self.assertTrue(result["trace"]["errors"][0].startswith("own rules:"))

    def test_pipeline_version_is_one_value_everywhere(self):
        self.assertEqual(fingerprint.RETRIEVAL_PIPELINE_VERSION, store.PIPELINE_VERSION)
        contract = {"provider": "p", "model": "m", "dim": 2, "config_hash": "c" * 64,
                    "config": {}}
        with patch("call_qa.embeddings.provider.configured_contract", return_value=contract):
            self.assertEqual(api._retrieval_config(enabled=True)["pipeline"], store.PIPELINE_VERSION)


class PromptTests(unittest.TestCase):
    def test_precedent_carries_its_almaty_date(self):
        criteria = [{"idx": 17, "criterion_id": "c17"}]
        hits = {17: [{"rule_id": "r1", "similarity": .79, "situation": "s",
                      "correct_verdict": "N/A", "reason": "r",
                      "created_at": "2026-10-22T21:30:00+00:00"},
                     {"rule_id": "r2", "similarity": .7, "situation": "s",
                      "correct_verdict": "N/A", "reason": "r"}]}
        text, by_criterion = evaluator._render_rag_context(criteria, hits, "ok")
        # 21:30 UTC 22.10 — уже 23.10 по Алматы.
        self.assertIn("[критерий 17; правило r1; сходство 0.790; от 23.10.2026]", text)
        self.assertIn("[критерий 17; правило r2; сходство 0.700]", text)
        self.assertEqual(by_criterion["17"], text)

    def test_header_tells_the_model_the_latest_precedent_wins(self):
        body = evaluator.build_eval_body("[S1] алло", {"id": 73}, [], use_rag=True,
                                         rag_text="(подходящих правил выше порога релевантности нет)",
                                         model="glm-5.3-flash")
        user = json.dumps(body, ensure_ascii=False)
        self.assertIn("действует более поздний по дате", user)

    def test_evaluation_passes_the_conversation_to_the_search(self):
        direction = {"id": 73, "criteria": [{"idx": 17, "criterion_id": "c17", "name": "КО",
                                             "description": "d", "eval_source": "transcript"}]}
        with patch.object(evaluator.cc, "apply_to_direction"), \
             patch.object(evaluator.store, "embed_query_chunks", return_value=QUERY_BATCH), \
             patch.object(evaluator.store, "retrieve_for_criteria_batch",
                          return_value={"status": "no_match", "hits_by_criterion": {17: []},
                                        "trace": {"status": "no_match", "candidates": []}}) as search, \
             patch.object(evaluator.store, "bump_use_count"), \
             patch.object(evaluator.llm, "post_body",
                          return_value={"per_criterion": [{"idx": 17, "verdict": "N/A",
                                                           "confidence": .9, "evidence_quote": "",
                                                           "comment": ""}],
                                        "overall_comment": ""}):
            evaluator.evaluate("[S1] алло", direction, use_rag=True, knowledge_snapshot_id=464,
                               subject_kind="imported_call", subject_id=11457)
        self.assertEqual(search.call_args.kwargs["subject_kind"], "imported_call")
        self.assertEqual(search.call_args.kwargs["subject_id"], 11457)


def _item(**overrides):
    item = {"criterion_id": "c17", "criterion_idx": 17, "criterion_name": "КО",
            "ai_verdict": "Incorrect", "correct_verdict": "N/A",
            "reason": "По стороннему агрегатору альтернатива не требуется",
            "situation": "Водитель спрашивает про InDrive", "not_covered": None,
            "excerpt": "", "excerpt_verified": False, "evidence_status": "no_evidence",
            "excerpt_start": None, "excerpt_end": None}
    item.update(overrides)
    return item


def _own_row(rule_id, status="active", *, version_id=463, text="Назвать организацию — корректно",
             situation="Вопрос про сторонний сервис", verdict="N/A"):
    """Строка _OWN_REVIEW_RULES_SQL."""
    return (rule_id, status, version_id, situation, text, None, verdict, None)


class RepeatReviewTests(unittest.TestCase):
    """Повторный разбор того же разговора тем же проверяющим правит его правило."""

    def _save(self, own_rows, item=None, *, revise=None):
        payload = {"id": 11457, "direction_id": 73, "_evaluation_run_id": RUN_ID,
                   "_scale_revision_id": 18, "_evaluation_model": "glm",
                   "_transcript_hash": "t" * 64, "_authoritative_transcript_text": "[S1] алло",
                   "transcript": []}
        conn = _Connection([("FOR UPDATE OF r", own_rows)])
        create_case = MagicMock(return_value="case-id")
        create_rule = MagicMock(return_value={"rule_id": "new-rule", "rule_version_id": 9,
                                              "rule_version": 1})
        revise = revise or MagicMock(return_value={"rule_id": own_rows[0][0] if own_rows else None,
                                                   "rule_version_id": 470, "rule_version": 2})
        transition = MagicMock()
        release = MagicMock()
        publish = MagicMock()
        activate = MagicMock(side_effect=lambda direction, ids, actor: set(ids))
        rollout = {"mode": "active", "approval": {"valid": True, "manual": True}}
        with patch.object(api, "_validated_adjudication_items",
                          return_value=(payload, [item or _item()])), \
             patch.object(api.config, "connect_rw", return_value=conn), \
             patch("call_qa.rag.similar.embed_texts", return_value=[[.1, .2], [.3, .4]]), \
             patch("call_qa.embeddings.provider.get_provider",
                   return_value=MagicMock(metadata={"provider": "t", "model": "m", "dim": 2})), \
             patch.object(knowledge, "create_adjudication_case", create_case), \
             patch.object(knowledge, "create_draft_policy_rule", create_rule), \
             patch.object(knowledge, "revise_policy_rule", revise), \
             patch.object(knowledge, "transition_policy_rule", transition), \
             patch.object(knowledge, "release_auto_activation", release), \
             patch.object(knowledge, "publish_direction_snapshots", publish), \
             patch.object(knowledge, "record_rule_embedding"), \
             patch.object(knowledge, "ensure_embedding_model", return_value=None), \
             patch("call_qa.rag.similar.find_similar", return_value={"items": [], "degraded": False}), \
             patch.object(api, "_activate_review_rules", activate), \
             patch.object(api, "_rag_rollout", return_value=rollout), \
             patch.object(api, "_claim_review_outcome"), \
             patch.object(api, "_record_rule_review_feedback"):
            saved = api._save_adjudications_locked(
                11457, 73, [{}], reviewer_id=169, evaluation_run_id=RUN_ID,
                scale_revision_id=18, subject_kind="imported_call")
        return saved, conn, create_case, create_rule, revise, transition, release, publish, activate

    def test_new_text_becomes_a_new_version_of_the_latest_own_rule(self):
        own = [_own_row("5af1cc30", version_id=467), _own_row("1243f553", version_id=464),
               _own_row("871f27b1", "draft", version_id=463)]
        saved, conn, case, create_rule, revise, transition, release, publish, activate = \
            self._save(own)
        create_rule.assert_not_called()
        kwargs = revise.call_args.kwargs
        self.assertEqual(kwargs["rule_id"], "5af1cc30")
        self.assertEqual(kwargs["expected_version_id"], 467)
        self.assertFalse(kwargs["publish_snapshot"])
        self.assertEqual(kwargs["changes"]["rule_text"], _item()["reason"])
        self.assertEqual(kwargs["changes"]["situation"], _item()["situation"])
        self.assertEqual(kwargs["changes"]["correct_verdict"], "N/A")
        # Прежние попытки того же разговора выведены из оценки.
        self.assertEqual([c.kwargs["rule_id"] for c in transition.call_args_list],
                         ["1243f553", "871f27b1"])
        self.assertTrue(all(c.kwargs["to_status"] == "deprecated" for c in transition.call_args_list))
        self.assertEqual(release.call_args.kwargs["rule_ids"], ["1243f553", "871f27b1"])
        # Новая версия включится сама, как новое правило разбора.
        sql = " ".join(statement for statement, _ in conn.cur.executed)
        self.assertIn("SAVEPOINT revise_own_rule", sql)
        self.assertIn("""metadata = metadata || '{"auto_activate": true}'::jsonb""", sql)
        publish.assert_called_once()
        self.assertEqual(activate.call_args.args[1], ["5af1cc30"])
        self.assertEqual(saved["rules"][0]["rule_id"], "5af1cc30")
        self.assertTrue(saved["rules"][0]["revised"])
        self.assertEqual(case.call_args.kwargs["metadata"]["revises_rule_id"], "5af1cc30")
        # Свои правила ищутся по разговору, проверяющему и критерию, под блокировкой.
        own_sql, params = next((s, p) for s, p in conn.cur.executed if "FOR UPDATE OF r" in s)
        self.assertEqual(params, ("imported_call", 11457, 169, 169, 73, "c17"))

    def test_same_text_again_links_to_the_own_rule(self):
        own = [_own_row("1243f553", text=_item()["reason"], situation=_item()["situation"])]
        saved, conn, case, create_rule, revise, transition, *_ = self._save(own)
        revise.assert_not_called()
        create_rule.assert_not_called()
        transition.assert_not_called()
        self.assertEqual(saved["linked"], 1)
        self.assertEqual(case.call_args.kwargs["metadata"]["duplicate_of_rule_id"], "1243f553")

    def test_version_conflict_falls_back_to_a_new_rule(self):
        own = [_own_row("1243f553")]
        revise = MagicMock(side_effect=psycopg2.IntegrityError("duplicate content"))
        saved, conn, case, create_rule, _, transition, release, publish, _ = \
            self._save(own, revise=revise)
        create_rule.assert_called_once()
        transition.assert_not_called()
        publish.assert_not_called()
        sql = " ".join(statement for statement, _ in conn.cur.executed)
        self.assertIn("ROLLBACK TO SAVEPOINT revise_own_rule", sql)
        self.assertEqual(saved["rules"][0]["rule_id"], "new-rule")
        self.assertFalse(saved["rules"][0]["revised"])

    def test_first_review_of_a_conversation_creates_a_rule(self):
        saved, conn, case, create_rule, revise, *_ = self._save([])
        create_rule.assert_called_once()
        revise.assert_not_called()
        self.assertNotIn("revises_rule_id", case.call_args.kwargs["metadata"])

    def test_no_reviewer_no_own_rule_lookup(self):
        conn = _Connection()
        self.assertEqual(api._own_review_rules(conn, subject_kind="call", call_id=1,
                                               direction_id=73, criterion_id="c17",
                                               reviewer_id=None), [])
        self.assertEqual(conn.cur.executed, [])

    def test_run_state_names_a_collapsed_copy(self):
        state = api._rule_run_state(
            "r1", candidates={"r1": {"included": False, "reject_reason": "duplicate_rule",
                                     "similarity": .79}},
            in_snapshot=set(), rag_enabled=True, trace_status="ok")
        self.assertEqual(state["code"], "duplicate")


class RefineSeesTheConversationTests(unittest.TestCase):
    def _refine(self, transcript):
        direction = {"criteria": [{"idx": 17, "name": "КО", "description": "d"}]}
        with patch("call_qa.evaluation.criteria.load_direction", return_value=direction), \
             patch.object(refine.llm, "claude_json", return_value={"situation": "s"}) as call:
            refine.refine_adjudication(direction_id=73, criterion_idx=17,
                                       correct_verdict="N/A", reason="InDrive не наш",
                                       transcript=transcript)
        return call.call_args.kwargs

    def test_rule_is_written_from_the_conversation(self):
        kwargs = self._refine("[S1] Индрайверге көмектесе алмаймыз")
        self.assertIn("РАЗГОВОР, ПО КОТОРОМУ ИСПРАВЛЯЮТ ИИ:\n[S1] Индрайверге", kwargs["user"])
        self.assertIn("на ЭТОМ разговоре давать", kwargs["system"])

    def test_long_conversation_is_cut(self):
        user = self._refine("а" * (refine._TRANSCRIPT_LIMIT + 50))["user"]
        self.assertIn("…(разговор обрезан)", user)
        self.assertNotIn("а" * (refine._TRANSCRIPT_LIMIT + 1), user)

    def test_without_conversation_prompt_stays_as_before(self):
        self.assertNotIn("РАЗГОВОР", self._refine(None)["user"])

    def test_api_passes_the_transcript(self):
        with patch("call_qa.rag.refine.refine_adjudication", return_value={}) as call:
            api.refine_adjudication({"direction_id": 73, "criterion_idx": 17,
                                     "correct_verdict": "N/A"}, transcript="текст")
        self.assertEqual(call.call_args.kwargs["transcript"], "текст")

    def test_run_transcript_belongs_to_the_same_conversation(self):
        self.assertIsNone(api.run_transcript_text(11457, "imported_call", "not-a-uuid"))
        conn = _Connection([("FROM ai_evaluation_runs", [(55,)])])
        with patch.object(api.config, "connect_ro", return_value=conn), \
             patch.object(api.runtime_store, "get_transcript_by_id",
                          return_value={"text": "[S1] алло"}) as transcript:
            text = api.run_transcript_text(11457, "imported_call", RUN_ID)
        self.assertEqual(text, "[S1] алло")
        transcript.assert_called_once_with(55)
        sql, params = conn.cur.executed[0]
        self.assertIn("WHERE id = %s AND call_id = %s AND subject_kind = %s", sql)
        self.assertEqual(params, (RUN_ID, 11457, "imported_call"))
        conn = _Connection([("FROM ai_evaluation_runs", [])])
        with patch.object(api.config, "connect_ro", return_value=conn):
            self.assertIsNone(api.run_transcript_text(11457, "imported_call", RUN_ID))


class RefineRouteTests(unittest.TestCase):
    """Ручка «Сформулировать» — запуском (извлечена из bot_schedule2.py)."""

    @classmethod
    def setUpClass(cls):
        source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        cls.node = next(node for node in source_cache.parse(source).body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "api_ai_qa_adjudicate_refine")

    def _route(self, body, *, in_scope=True):
        class _App:
            def route(self, *a, **k):
                return lambda fn: fn
        namespace = {
            "app": _App(), "require_api_key": lambda fn: fn,
            "request": SimpleNamespace(method="POST", get_json=lambda force=False, silent=False: body),
            "jsonify": lambda payload: payload,
            "_build_cors_preflight_response": lambda: "preflight",
            "_ai_qa_guard": lambda: (169, None),
            "_ai_qa_direction_scope": lambda uid: None,
            "_ai_qa_direction_department_allowed": lambda uid, direction_id: True,
            "_ai_qa_subject_kind": lambda value, default=None: value or default,
            "logging": SimpleNamespace(exception=lambda *a, **k: None),
        }
        module = ast.Module(body=[self.node], type_ignores=[])
        exec(compile(module, "<refine-route>", "exec"), namespace)
        with patch.object(api, "call_in_scope", return_value=in_scope) as scope, \
             patch.object(api, "subject_direction_id", return_value=73), \
             patch.object(api, "run_transcript_text", return_value="[S1] алло") as transcript, \
             patch.object(api, "refine_adjudication", return_value={"rule": "r"}) as call:
            out = namespace["api_ai_qa_adjudicate_refine"]()
        return out, scope, transcript, call

    def test_rule_is_formulated_from_the_run_transcript(self):
        out, scope, transcript, call = self._route({
            "direction_id": 73, "criterion_idx": 17, "correct_verdict": "N/A",
            "call_id": 11457, "subject_kind": "imported_call", "evaluation_run_id": RUN_ID})
        self.assertEqual(out, ({"status": "success", "proposal": {"rule": "r"}}, 200))
        scope.assert_called_once_with(11457, None, "imported_call")
        transcript.assert_called_once_with(11457, "imported_call", RUN_ID)
        self.assertEqual(call.call_args.kwargs["transcript"], "[S1] алло")

    def test_foreign_conversation_is_refused_before_reading_it(self):
        out, _, transcript, call = self._route({
            "direction_id": 73, "criterion_idx": 17, "correct_verdict": "N/A",
            "call_id": 11457, "subject_kind": "imported_call", "evaluation_run_id": RUN_ID},
            in_scope=False)
        self.assertEqual(out[1], 403)
        transcript.assert_not_called()
        call.assert_not_called()

    def test_old_client_without_conversation_still_works(self):
        out, _, transcript, call = self._route({"direction_id": 73, "criterion_idx": 17,
                                                "correct_verdict": "N/A"})
        self.assertEqual(out[1], 200)
        transcript.assert_not_called()
        self.assertIsNone(call.call_args.kwargs["transcript"])


class CardSendsTheConversationTests(unittest.TestCase):
    def test_refine_request_carries_conversation_and_run(self):
        source = (ROOT / "src/components/call_qa/CallQaView.jsx").read_text(encoding="utf-8")
        start = source.index("/api/ai-qa/adjudicate/refine")
        request = source[start:source.index("}, { headers: headers() })", start)]
        for field in ("call_id: callData.id", "subject_kind: callData.subject_kind",
                      "evaluation_run_id: callData._evaluation_run_id"):
            self.assertIn(field, request)


if __name__ == "__main__":
    unittest.main()
