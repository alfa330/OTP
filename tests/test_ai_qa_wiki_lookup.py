"""Оценщик сам ищет в вики (владелец, 23.10.2026: «одна из самых важных черт»):
план запросов моделью, периметр отдела, гибридный поиск помощника вики, потолки,
подписи выдержек, трасса и то, что сбой справки не роняет оценку."""
from datetime import datetime
import json
import unittest
from unittest.mock import MagicMock, patch

import psycopg2

from call_qa import api
from call_qa.evaluation import evaluator, wiki_lookup

CRITERIA = [
    {"idx": 5, "criterion_id": "c5", "name": "Информация по акциям", "description": "d",
     "eval_source": "transcript", "is_critical": False},
    {"idx": 6, "criterion_id": "c6", "name": "Грамотность", "description": "d",
     "eval_source": "transcript", "is_critical": False},
]
TRANSCRIPT = "[22.10 11:51] Оператор: Поздравляем! Вы стали партнёром таксопарка QAZAQ"


class _Cursor:
    def __init__(self, answers=()):
        self.answers = list(answers)
        self.executed = []
        self._rows = []

    def execute(self, sql, params=None):
        if isinstance(params, tuple) and sql.count("%s") != len(params):
            raise AssertionError("число %s не совпадает с параметрами")
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
        self.cur = _Cursor(answers)
        self.closed = 0

    def cursor(self):
        return self.cur

    def close(self):
        self.closed += 1


def _chunk(chunk_id, *, article_id=33, title="Все акции", slug="все-акции",
           heading="Актуальные акции 2026", text="№: 1; Парк: Все таксопарки, кроме Tenge",
           historical=False, score=1.0, similarity=.8):
    return {"chunk_id": chunk_id, "article_id": article_id, "title": title, "slug": slug,
            "chunk_idx": chunk_id, "heading_path": heading, "text": text, "requires_ack": False,
            "historical": historical, "score": score, "similarity": similarity, "found_by": [1]}


class PerimeterTests(unittest.TestCase):
    def test_department_spaces_and_ai_eligibility(self):
        cur = _Cursor([("FROM wiki_space_departments", [(33, "Все акции"), (1048, "Как подключить")])])
        scope = wiki_lookup.perimeter(cur, " OP ")
        self.assertEqual(scope["article_ids"], [33, 1048])
        self.assertEqual(scope["titles"][0], (33, "Все акции"))
        sql, params = cur.executed[0]
        self.assertEqual(params, {"code": "op"})
        for condition in ("lower(d.code) = %(code)s", "a.status = 'published'", "NOT a.strict_mode",
                          "NOT a.ai_opt_out", "sec2.ai_opt_out", "FROM wiki_ai_chunks"):
            self.assertIn(condition, sql)

    def test_no_department_no_query(self):
        cur = _Cursor()
        self.assertEqual(wiki_lookup.perimeter(cur, None), {"article_ids": [], "titles": []})
        self.assertEqual(cur.executed, [])


class PlanTests(unittest.TestCase):
    def _plan(self, answer):
        with patch.object(wiki_lookup.llm, "build_body", return_value={"body": 1}) as build, \
             patch.object(wiki_lookup.llm, "post_body", return_value=answer) as post:
            out = wiki_lookup.plan(criteria=CRITERIA, transcript=TRANSCRIPT,
                                   titles=[(33, "Все акции"), (10, "Транзакции")],
                                   subject_kind="wz_episode", model="glm-5.3-flash")
        return out, build, post

    def test_model_sees_criteria_titles_and_conversation_and_answers_with_queries(self):
        out, build, post = self._plan({
            "queries": [{"criterion_idx": 5, "query": "акции для новых водителей Qazaq", "reason": "r"},
                        {"criterion_idx": 5, "query": " акции для новых  водителей Qazaq ", "reason": "копия"},
                        {"criterion_idx": 9, "query": "чужой критерий", "reason": "r"},
                        {"criterion_idx": "x", "query": "битый номер", "reason": "r"},
                        {"criterion_idx": 6, "query": "аб", "reason": "слишком коротко"}],
            "_llm_meta": {"model": "glm", "usage": {"input_tokens": 10}, "latency_ms": 900}})
        self.assertEqual(out["queries"], [{"criterion_idx": 5, "query": "акции для новых водителей Qazaq",
                                           "reason": "r"}])
        self.assertEqual(out["meta"]["latency_ms"], 900)
        kwargs = build.call_args.kwargs
        self.assertEqual(kwargs["effort"], wiki_lookup.PLANNER_EFFORT)
        self.assertIn("- Все акции", kwargs["user"])
        self.assertIn("5. Информация по акциям", kwargs["user"])
        self.assertIn("ПЕРЕПИСКА", kwargs["user"])
        self.assertIn(TRANSCRIPT, kwargs["user"])
        self.assertIn(str(wiki_lookup.MAX_QUERIES), kwargs["system"])
        self.assertTrue(post.call_args.kwargs["include_meta"])

    def test_query_cap(self):
        many = [{"criterion_idx": 5, "query": f"запрос номер {n}", "reason": ""} for n in range(20)]
        out, *_ = self._plan({"queries": many})
        self.assertEqual(len(out["queries"]), wiki_lookup.MAX_QUERIES)

    def test_empty_plan_means_no_search(self):
        out, *_ = self._plan({"queries": []})
        self.assertEqual(out["queries"], [])


class SearchTests(unittest.TestCase):
    def _search(self, found_rows, queries=None, vectors=None, answers=()):
        cur = _Cursor([("FROM wiki_articles WHERE id = ANY", [(33, datetime(2026, 10, 6, 17, 46)),
                                                             (731, datetime(2026, 9, 17))]), *answers])
        calls = []

        def hybrid(cursor, **kwargs):
            calls.append(kwargs)
            return {"rows": found_rows, "branches": {}, "degraded": kwargs.get("query_vector") is None}
        with patch("wiki.ai.retrieve.search_hybrid", side_effect=hybrid):
            out = wiki_lookup.search(cur, article_ids=[33, 731],
                                     queries=queries or [{"criterion_idx": 5, "query": "акции Qazaq"}],
                                     vectors=vectors)
        return out, calls, cur

    def test_chunks_deduped_labelled_and_marked(self):
        rows = [_chunk(1), _chunk(1), _chunk(2, article_id=731, title="Архивные акции TEZ",
                                             slug="arhiv", heading="Архив", historical=True)]
        out, calls, _ = self._search(rows, vectors=[[.1, .2]])
        self.assertEqual([c["chunk_id"] for c in out["chunks"]], [1, 2])
        self.assertEqual(out["chunks"][0]["updated_at"], "2026-10-06T17:46:00")
        self.assertEqual(out["chunks"][0]["criterion_idx"], 5)
        self.assertIn("АРХИВ", out["chunks"][1]["stale_note"])
        self.assertEqual(calls[0]["query_vector"], [.1, .2])
        self.assertEqual(calls[0]["limit"], wiki_lookup.PER_QUERY)

    def test_without_vector_search_is_lexical(self):
        out, calls, _ = self._search([_chunk(1)], vectors=[None])
        self.assertIsNone(calls[0]["query_vector"])
        self.assertEqual(len(out["chunks"]), 1)

    def test_caps_on_chunks_and_chars(self):
        rows = [_chunk(n, text="x" * 3000) for n in range(1, 30)]
        with patch.object(wiki_lookup, "MAX_CHUNKS", 3):
            out, *_ = self._search(rows)
        self.assertEqual(len(out["chunks"]), 3)
        with patch.object(wiki_lookup, "MAX_CHARS", 7000):
            out, *_ = self._search(rows)
        self.assertEqual(len(out["chunks"]), 2)

    def test_queries_are_embedded_in_one_call(self):
        with patch("wiki.ai.embed.embed_queries", return_value=[[.1], [.2]]) as embed:
            out = wiki_lookup.embed_queries([{"criterion_idx": 5, "query": "акции"},
                                             {"criterion_idx": 6, "query": "тарифы"}])
        self.assertEqual(out["vectors"], [[.1], [.2]])
        self.assertFalse(out["degraded"])
        embed.assert_called_once_with(["акции", "тарифы"])

    def test_embedding_failure_degrades_not_breaks(self):
        with patch("wiki.ai.embed.embed_queries", side_effect=RuntimeError("vertex down")):
            out = wiki_lookup.embed_queries([{"criterion_idx": 5, "query": "акции"}])
        self.assertEqual(out["vectors"], [None])
        self.assertTrue(out["degraded"])
        self.assertIn("vertex down", out["errors"][0])
        with patch("wiki.ai.embed.embed_queries", return_value=[[.1]]):
            short = wiki_lookup.embed_queries([{"criterion_idx": 5, "query": "а"},
                                               {"criterion_idx": 5, "query": "б"}])
        self.assertTrue(short["degraded"])

    def test_fresh_excerpts_take_the_places_before_stale_ones(self):
        rows = [_chunk(1, heading="ВСЕ ПАРКИ АРХИВ"), _chunk(2, heading="Актуальные акции 2026"),
                _chunk(3, heading="Розыгрыши")]
        with patch.object(wiki_lookup, "MAX_CHUNKS", 2):
            out, *_ = self._search(rows)
        self.assertEqual([c["chunk_id"] for c in out["chunks"]], [2, 3])


class RenderTests(unittest.TestCase):
    def test_excerpt_label_has_criterion_article_section_date_and_staleness(self):
        chunks = [{**_chunk(1), "criterion_idx": 5, "updated_at": "2026-10-06T17:46:00", "stale_note": ""},
                  {**_chunk(2, title="Архивные акции TEZ", heading=""), "criterion_idx": 5,
                   "updated_at": None, "stale_note": "АРХИВ — сведения уже не действуют"}]
        text = wiki_lookup.render(chunks)
        self.assertIn("[1; к критерию 5; статья «Все акции», раздел «Актуальные акции 2026»; "
                      "обновлена 06.10.2026]\n№: 1; Парк: Все таксопарки, кроме Tenge", text)
        self.assertIn("[2; к критерию 5; ⚠ АРХИВ — сведения уже не действуют. статья «Архивные акции TEZ»]", text)

    def test_sources_one_line_per_article(self):
        chunks = [{**_chunk(1), "criterion_idx": 5, "updated_at": "2026-10-06", "stale_note": ""},
                  {**_chunk(2, heading="Архив"), "criterion_idx": 6, "updated_at": "2026-10-06",
                   "stale_note": "СРОК ИСТЁК 01.01.2026"}]
        out = wiki_lookup.sources(chunks)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["headings"], ["Актуальные акции 2026", "Архив"])
        self.assertEqual(out[0]["criterion_idxs"], [5, 6])
        self.assertTrue(out[0]["stale"])
        json.dumps(out)


class PrepareTests(unittest.TestCase):
    def _prepare(self, *, connections, plan=None, embed=None, search=None, department="op"):
        pool = iter(connections)
        with patch.object(wiki_lookup, "plan", side_effect=plan or (lambda **k: {"queries": [], "meta": {}})), \
             patch.object(wiki_lookup, "embed_queries", side_effect=embed or (lambda q: {"vectors": [None] * len(q), "errors": [], "degraded": False})), \
             patch.object(wiki_lookup, "search", side_effect=search or (lambda cur, **k: {"chunks": []})):
            return wiki_lookup.prepare(criteria=CRITERIA, transcript=TRANSCRIPT, subject_kind="wz_episode",
                                       department=department, model="glm", connect=lambda: next(pool))

    def test_full_path_and_short_connections(self):
        first = _Connection([("FROM wiki_space_departments", [(33, "Все акции")])])
        second = _Connection()
        chunk = {**_chunk(1), "criterion_idx": 5, "updated_at": "2026-10-06", "stale_note": ""}
        out = self._prepare(
            connections=[first, second],
            plan=lambda **k: {"queries": [{"criterion_idx": 5, "query": "акции Qazaq", "reason": ""}],
                              "meta": {"latency_ms": 500}},
            embed=lambda q: {"vectors": [[.1]], "errors": [], "degraded": False},
            search=lambda cur, **k: {"chunks": [chunk]})
        self.assertEqual(out["trace"]["status"], "ok")
        self.assertEqual(out["trace"]["perimeter"], {"articles": 1})
        self.assertEqual(out["trace"]["planner"], {"latency_ms": 500})
        self.assertEqual(len(out["trace"]["chunks"]), 1)
        self.assertIn("статья «Все акции»", out["wiki_text"])
        self.assertEqual(out["sources"][0]["title"], "Все акции")
        # Два коротких соединения: периметр и поиск; план и векторы — без базы.
        self.assertEqual((first.closed, second.closed), (1, 1))
        json.dumps(out)

    def test_no_queries_no_search_no_second_connection(self):
        first = _Connection([("FROM wiki_space_departments", [(33, "Все акции")])])
        out = self._prepare(connections=[first])
        self.assertEqual(out["trace"]["status"], "no_queries")
        self.assertEqual(out["wiki_text"], "")

    def test_no_perimeter_no_planner(self):
        planner = MagicMock()
        out = self._prepare(connections=[_Connection([("FROM wiki_space_departments", [])])], plan=planner)
        self.assertEqual(out["trace"]["status"], "no_perimeter")
        planner.assert_not_called()

    def test_without_department_nothing_is_touched(self):
        out = self._prepare(connections=[], department=None)
        self.assertEqual(out["trace"]["status"], "no_department")

    def test_failure_never_breaks_the_evaluation(self):
        boom = _Connection([("FROM wiki_space_departments", psycopg2.OperationalError("db down"))])
        out = self._prepare(connections=[boom])
        self.assertEqual(out["trace"]["status"], "failed")
        self.assertIn("db down", out["trace"]["errors"][0])
        self.assertEqual(out["wiki_text"], "")
        self.assertEqual(out["sources"], [])

    def test_disabled_by_config(self):
        with patch.object(wiki_lookup, "ENABLED", False):
            out = wiki_lookup.prepare(criteria=CRITERIA, transcript=TRANSCRIPT, subject_kind="wz_episode",
                                      department="op", model="glm", connect=lambda: self.fail("база не нужна"))
        self.assertEqual(out["trace"]["status"], "disabled")


class EvaluatorWiringTests(unittest.TestCase):
    def test_excerpts_go_between_rules_and_conversation(self):
        body = evaluator.build_eval_body(TRANSCRIPT, {"id": 71}, CRITERIA, use_rag=True,
                                         rag_text="(правил нет)", model="glm-5.3-flash",
                                         subject_kind="wz_episode", department="op",
                                         wiki_text="[1; к критерию 5; статья «Все акции»]\nБыстрый старт")
        user = body["payload"]["messages"][1]["content"]
        self.assertLess(user.index("РАЗБОРЫ"), user.index("ВЫДЕРЖКИ ИЗ ВИКИ"))
        self.assertLess(user.index("ВЫДЕРЖКИ ИЗ ВИКИ"), user.index("ПЕРЕПИСКА"))
        self.assertIn("верь более свежей", user)
        plain = evaluator.build_eval_body(TRANSCRIPT, {"id": 71}, CRITERIA, use_rag=True,
                                          rag_text="(правил нет)", model="glm-5.3-flash",
                                          subject_kind="wz_episode", department="op", wiki_text="  ")
        self.assertNotIn("ВЫДЕРЖКИ", plain["payload"]["messages"][1]["content"])

    def _evaluate(self, **kwargs):
        direction = {"id": 71, "criteria": [dict(c) for c in CRITERIA]}
        answer = {"per_criterion": [{"idx": 5, "verdict": "Incorrect", "confidence": .8,
                                     "evidence_quote": "", "comment": ""},
                                    {"idx": 6, "verdict": "Correct", "confidence": .9,
                                     "evidence_quote": "", "comment": ""}], "overall_comment": ""}
        prepared = {"wiki_text": "[1; к критерию 5; статья «Все акции»]\nБыстрый старт",
                    "trace": {"status": "ok", "queries": [{"criterion_idx": 5, "query": "q"}], "chunks": [{}]},
                    "sources": [{"title": "Все акции"}]}
        with patch.object(evaluator.cc, "apply_to_direction"), \
             patch.object(evaluator, "prepare_wiki_context", return_value=prepared) as prep, \
             patch.object(evaluator.llm, "post_body", return_value=answer) as post, \
             patch.object(evaluator.config, "CLAUDE_MODEL_HARD", "glm-5.3-flash"):
            result = evaluator.evaluate(TRANSCRIPT, direction, use_rag=False, subject_kind="wz_episode",
                                        department="op", **kwargs)
        return result, prep, post

    def test_evaluate_searches_once_and_feeds_every_model_call(self):
        result, prep, post = self._evaluate()
        prep.assert_called_once()
        self.assertEqual(prep.call_args.kwargs["department"], "op")
        self.assertIn("ВЫДЕРЖКИ ИЗ ВИКИ", post.call_args.args[0]["payload"]["messages"][1]["content"])
        self.assertEqual(result["wiki_trace"]["status"], "ok")
        self.assertEqual(result["wiki_sources"], [{"title": "Все акции"}])

    def test_prepared_wiki_from_the_batch_is_reused(self):
        ready = {"wiki_text": "", "trace": {"status": "absent"}, "sources": []}
        result, prep, _ = self._evaluate(prepared_wiki=ready)
        prep.assert_not_called()
        self.assertEqual(result["wiki_trace"], {"status": "absent"})

    def test_use_wiki_false_skips_the_search(self):
        result, prep, _ = self._evaluate(use_wiki=False)
        prep.assert_not_called()
        self.assertEqual(result["wiki_trace"], {"status": "disabled"})


class ApiTests(unittest.TestCase):
    def test_fingerprint_knows_the_wiki_search_version(self):
        direction = {"id": 71, "criteria": [], "scale_hash": "s" * 64, "guidance": "", "wiki": []}
        contract = {"provider": "p", "model": "m", "dim": 2, "config_hash": "c" * 64, "config": {}}
        with patch("call_qa.embeddings.provider.configured_contract", return_value=contract):
            _, components, _ = api._evaluation_identity(
                transcript_hash="t" * 64, direction=direction, knowledge_snapshot={"content_hash": "k" * 64},
                use_rag=True, subject_kind="wz_episode", department="op")
            with patch.object(api.wiki_lookup, "ENABLED", False):
                _, off, _ = api._evaluation_identity(
                    transcript_hash="t" * 64, direction=direction, knowledge_snapshot={"content_hash": "k" * 64},
                    use_rag=True, subject_kind="wz_episode", department="op")
        self.assertEqual(components["model_config"]["wiki_search"], wiki_lookup.VERSION)
        self.assertEqual(off["model_config"]["wiki_search"], "off")

    def test_summary_and_projection(self):
        summary = api._evaluation_summary(
            run_id="r", fingerprint="f" * 64, knowledge_snapshot={"knowledge_revision": 1},
            trace={"status": "ok"}, rollout={"mode": "active"},
            wiki_trace={"status": "ok", "queries": [{}, {}], "chunks": [{}], "latency_ms": 1200})
        self.assertEqual((summary["wiki_status"], summary["wiki_queries"], summary["wiki_chunks"], summary["wiki_ms"]),
                         ("ok", 2, 1, 1200))
        slim = api._projection_payload({"id": 1, "_wiki_trace": {"chunks": ["большой"]}, "wiki_sources": [{"title": "x"}]})
        self.assertNotIn("_wiki_trace", slim)
        self.assertEqual(slim["wiki_sources"], [{"title": "x"}])

    def test_card_payload_carries_trace_and_sources(self):
        from pathlib import Path
        source = Path(api.__file__).read_text(encoding="utf-8")
        self.assertIn('"_wiki_trace": result.get("wiki_trace") or {},\n        "wiki_sources": result.get("wiki_sources") or [],',
                      source)
        self.assertIn("wiki_trace=payload[\"_wiki_trace\"])", source)
        batch = Path(api.__file__).with_name("batch_eval.py").read_text(encoding="utf-8")
        self.assertIn("prepared_wiki = evaluator.prepare_wiki_context(", batch)
        self.assertIn('wiki_text=prepared_wiki["wiki_text"],', batch)
        self.assertIn('"prepared_wiki": prepared_wiki,', batch)
        self.assertIn('prepared_wiki=entry.get("prepared_wiki")', batch)


if __name__ == "__main__":
    unittest.main()
