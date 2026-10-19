# -*- coding: utf-8 -*-
"""Проекция ai_review_cache и журнал ai_evaluation_meta — без копий (19.10.2026).

ИИ-оценка давала ~19 МБ прироста базы в сутки, и заметную часть — копии: каждая карточка
в ai_review_cache несла трассу поиска правил (~35 тыс. символов) и метаданные вызовов LLM,
которые один раз лежат в прогоне (ai_evaluation_runs.payload), и транскрипт (~28 тыс.),
который один раз лежит в ai_transcript_cache. Журнал оценки хранил третью полную копию
критериев с цитатами, а читает из неё только счёт доверия (source, ai, idx).

Модульная часть проверяет, что пишется. Необязательная часть на настоящем Postgres
(AI_QA_TEST_PORT — порт локального кластера) исполняет SQL сводки дня: транскрипт
приходит из ai_transcript_cache, когда в карточке его уже нет.

    AI_QA_TEST_PORT=55438 python -m pytest tests/test_ai_qa_projection_slim.py
"""
import contextlib
import os
import unittest
import uuid
from unittest import mock

import pytest

from call_qa import api
from call_qa.digest import data as digest_data


def _card(**extra):
    card = {
        "id": 501, "subject_kind": "imported_call", "direction_id": 3,
        "criteria": [{"idx": 0, "criterion_id": "c-0", "name": "Приветствие", "ai": "Correct",
                      "conf": 0.93, "source": "transcript", "is_critical": False,
                      "evidence": "Здравствуйте, служба поддержки", "comment": "Поздоровался",
                      "model": "glm"}],
        "ai_score": 100, "score_breakdown": {"unchecked_weight": 0},
        "transcript": [{"speaker": "S1", "start_ms": 0,
                        "seg": [{"t": "Здравствуйте", "start_time_ms": 0}]}],
        "_transcript_cache_id": 77, "_evaluation_run_id": "run-1",
        "_retrieval_trace": {"status": "ok", "candidates": [{"rule_id": "r", "reason": "x" * 300}]},
        "_llm_meta": {"calls": [{"usage": {"input_tokens": 1}}]},
        "evaluation": {"run_id": "run-1"},
    }
    card.update(extra)
    return card


class ProjectionPayloadTests(unittest.TestCase):
    def test_copies_of_trace_and_llm_meta_are_not_kept(self):
        slim = api._projection_payload(_card())
        self.assertNotIn("_retrieval_trace", slim)
        self.assertNotIn("_llm_meta", slim)
        # Всё, что читают списки, очередь и сводка, на месте.
        for key in ("criteria", "ai_score", "score_breakdown", "_evaluation_run_id",
                    "_transcript_cache_id", "evaluation", "direction_id"):
            self.assertIn(key, slim)

    def test_transcript_goes_only_when_it_can_be_found_elsewhere(self):
        self.assertNotIn("transcript", api._projection_payload(_card()))
        # Ссылки на кэш распознавания нет — транскрипт брать неоткуда, остаётся.
        self.assertIn("transcript", api._projection_payload(_card(_transcript_cache_id=None)))
        # Старый встроенный ASR (_asm) читает путь миграции _resolve_call_source
        # вместе с транскриптом карточки — такую карточку не трогаем.
        self.assertIn("transcript", api._projection_payload(_card(_asm={"text": "…"})))
        # True — тоже int в Python; за ссылку на строку кэша его принимать нельзя.
        self.assertIn("transcript", api._projection_payload(_card(_transcript_cache_id=True)))

    def test_the_card_itself_is_not_changed(self):
        """После записи проекции тот же dict уходит во фронт — с транскриптом и трассой."""
        card = _card()
        api._projection_payload(card)
        self.assertIn("transcript", card)
        self.assertIn("_retrieval_trace", card)
        self.assertIn("_llm_meta", card)


class _Conn:
    def __init__(self):
        self.executed = []

    def cursor(self):
        conn = self

        class _Cur:
            def execute(self, sql, params=None):
                conn.executed.append((sql, params))

            def fetchone(self):
                return (1,)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Cur()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def _adapted(value):
    return getattr(value, "adapted", value)


class WritersTests(unittest.TestCase):
    def test_cache_put_writes_the_projection(self):
        conn = _Conn()
        card = _card()
        with mock.patch.object(api.config, "connect_rw", return_value=conn):
            api._cache_put(501, "glm", card, subject_kind="imported_call")
        sql, params = conn.executed[0]
        self.assertIn("INSERT INTO ai_review_cache", sql)
        written = _adapted(params[3])
        self.assertNotIn("_retrieval_trace", written)
        self.assertNotIn("transcript", written)
        self.assertEqual(card["criteria"], written["criteria"])
        self.assertIn("transcript", card, "исходная карточка не должна меняться")

    def test_meta_keeps_only_what_trust_metrics_read(self):
        slim = api._meta_criteria(_card())
        self.assertEqual([{"idx": 0, "criterion_id": "c-0", "source": "transcript",
                           "ai": "Correct", "is_critical": False}], slim)

    def test_all_three_meta_writers_store_the_slim_criteria(self):
        expected = api._meta_criteria(_card())
        for call in ("upsert", "record", "claim"):
            conn = _Conn()
            with mock.patch.object(api.config, "connect_rw", return_value=conn):
                if call == "upsert":
                    api._meta_upsert(501, "glm", _card(), subject_kind="imported_call")
                elif call == "record":
                    api._record_review_outcome(501, "confirmed", 7, payload=_card(), model="glm",
                                               subject_kind="imported_call")
                else:
                    api._claim_review_outcome(conn.cursor(), call_id=501, outcome="confirmed",
                                              reviewer_id=7, payload=_card(), model="glm",
                                              subject_kind="imported_call")
            sql, params = conn.executed[0]
            self.assertIn("INSERT INTO ai_evaluation_meta", sql, call)
            self.assertEqual(expected, _adapted(params[4]), call)

    def test_slim_criteria_are_enough_for_trust_metrics(self):
        """Счёт доверия читает из журнала только source, ai, idx — урезанной строки ему
        хватает, и счёт тот же, что по полной карточке."""
        full = _card()["criteria"]
        slim = api._meta_criteria(_card())

        def metrics(criteria):
            cur = mock.MagicMock()
            cur.fetchall.side_effect = [[(501, "confirmed", criteria, "imported_call")], []]
            return api._reviewed_metrics(cur, subject_keys={("imported_call", 501)})

        self.assertEqual(metrics(full), metrics(slim))
        self.assertEqual(1, metrics(slim)["endorsed"])


# ── Настоящий SQL сводки дня ─────────────────────────────────────────────────

PORT = os.environ.get("AI_QA_TEST_PORT")


@contextlib.contextmanager
def _schema():
    import psycopg2
    conn = psycopg2.connect(host="127.0.0.1", port=int(PORT or 0), user="postgres",
                            dbname="postgres", password=os.environ.get("PGPASSWORD"))
    conn.autocommit = True
    name = "aiqa_" + uuid.uuid4().hex[:10]
    cur = conn.cursor()
    cur.execute(f"CREATE SCHEMA {name}")
    cur.execute(f"SET search_path TO {name}")
    try:
        cur.execute("""CREATE TABLE ai_review_cache (subject_kind text, call_id bigint,
                       model text, payload jsonb, created_at timestamptz DEFAULT now())""")
        # Свои payload и call_id у кэша распознавания — именно они ловят голое имя
        # колонки в подзапросе.
        cur.execute("""CREATE TABLE ai_transcript_cache (id bigint PRIMARY KEY, call_id bigint,
                       subject_kind text, segments jsonb, payload jsonb)""")
        yield cur
    finally:
        cur.execute(f"DROP SCHEMA {name} CASCADE")
        conn.close()


@pytest.mark.skipif(not PORT, reason="needs local PostgreSQL (AI_QA_TEST_PORT)")
class DigestTranscriptSqlTests(unittest.TestCase):
    def test_transcript_comes_from_the_card_or_from_the_asr_cache(self):
        from psycopg2.extras import Json
        line = lambda text: [{"speaker": "S1", "start_ms": 1000, "seg": [{"t": text}]}]  # noqa: E731
        with _schema() as cur:
            cur.execute("INSERT INTO ai_transcript_cache VALUES (77, 999, 'imported_call', %s, %s)",
                        (Json(line("Из кэша распознавания")),
                         Json({"asr_meta": {}, "transcript": "чужой ключ"})))
            rows = {
                1: _card(transcript=line("Из старой карточки")),         # старая карточка
                2: api._projection_payload(_card()),                      # новая, без транскрипта
                3: api._projection_payload(_card(_transcript_cache_id=404)),  # строки кэша нет
            }
            for call_id, payload in rows.items():
                cur.execute("INSERT INTO ai_review_cache VALUES ('imported_call', %s, 'glm', %s)",
                            (call_id, Json(payload)))
            talks = [{"kind": "imported_call", "model": "glm", "id": call_id,
                      "family": digest_data.FAMILY_CALLS, "subject_direction": 3}
                     for call_id in rows]
            digest_data.attach_details(cur, talks, with_transcripts=True)
            by_id = {t["id"]: t for t in talks}
            self.assertEqual("[00:01] Г1: Из старой карточки", by_id[1]["transcript"])
            self.assertEqual("[00:01] Г1: Из кэша распознавания", by_id[2]["transcript"])
            self.assertEqual("", by_id[3]["transcript"])
            self.assertEqual(100, by_id[2]["ai_score"])
            # Без транскриптов (экран сводки) — тот же запрос, транскрипт пустой.
            slim_talks = [dict(t) for t in talks]
            for t in slim_talks:
                t.pop("transcript", None)
            digest_data.attach_details(cur, slim_talks, with_transcripts=False)
            self.assertTrue(all(t["transcript"] == "" for t in slim_talks))


if __name__ == "__main__":
    unittest.main()
