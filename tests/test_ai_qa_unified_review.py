# -*- coding: utf-8 -*-
"""Одна оценка по критериям в карточке ИИ-оценки: серверная половина.

Что здесь стережём:

* вердикт модели вне схемы не проходит в карточку как есть. GLM (zai) пишет
  «Error» по критическим критериям — кнопки его не знали, а балл ИИ не
  обнулялся (в проде 23.09.2026: крит. ошибка, а «ИИ: 59»). Теперь это
  Incorrect, а нераспознанный вердикт считается невернувшимся и уходит на повтор;
* «как у ИИ» на сервере переводит «Error» старых прогонов так же, как фронт;
* карточка знает, разобран ли уже её прогон, — сохранение решает по этому,
  отправлять ли вместе с оценкой человека итог ревью ИИ.
"""
import unittest
from unittest import mock

from call_qa import api
from call_qa import human_review as hr
from call_qa.evaluation.evaluator import _collect_verdicts, _needs_escalation, assemble_results


def _crit(idx, weight, *, critical=False, source="transcript"):
    return {"idx": idx, "criterion_id": f"c{idx}", "name": f"Критерий {idx}",
            "description": "Требование", "weight": weight, "is_critical": critical,
            "deficiency": None, "eval_source": source}


class ModelVerdictTests(unittest.TestCase):
    def test_error_from_the_model_is_incorrect(self):
        by_idx = _collect_verdicts([{"idx": 16, "verdict": "Error", "confidence": 0.75}])
        self.assertEqual(by_idx[16]["verdict"], "Incorrect")
        # Такой вердикт теперь и эскалируется, как любой штраф.
        self.assertTrue(_needs_escalation(by_idx[16], _crit(16, None, critical=True)))

    def test_case_and_spaces_do_not_lose_the_verdict(self):
        by_idx = _collect_verdicts([
            {"idx": 0, "verdict": " correct "}, {"idx": 1, "verdict": "n/a"},
            {"idx": 2, "verdict": "DEFICIENCY"}, {"idx": 3, "verdict": "Incorrect"},
        ])
        self.assertEqual([by_idx[i]["verdict"] for i in range(4)],
                         ["Correct", "N/A", "Deficiency", "Incorrect"])

    def test_unknown_verdict_counts_as_not_returned(self):
        # Не угадываем: критерий уходит на повтор, а не получает чужой вердикт.
        self.assertEqual(_collect_verdicts([{"idx": 0, "verdict": "Maybe"},
                                            {"idx": 1, "verdict": None}]), {})

    def test_duplicates_compare_normalised_verdicts(self):
        by_idx = _collect_verdicts([
            {"idx": 0, "verdict": "Error", "confidence": 0.5},
            {"idx": 0, "verdict": "Incorrect", "confidence": 0.9},
        ])
        self.assertEqual(by_idx[0]["confidence"], 0.9)
        self.assertEqual(_collect_verdicts([{"idx": 1, "verdict": "Error"},
                                            {"idx": 1, "verdict": "Correct"}]), {})

    def test_critical_error_zeroes_the_ai_score(self):
        direction = {"criteria": [_crit(0, 100), _crit(1, None, critical=True)]}
        by_idx = _collect_verdicts([{"idx": 0, "verdict": "Correct", "confidence": 0.9},
                                    {"idx": 1, "verdict": "Error", "confidence": 0.8}])
        result = assemble_results(direction, by_idx, {0: "m", 1: "m"})
        self.assertEqual(result["per_criterion"][1]["verdict"], "Incorrect")
        self.assertEqual(api._ai_score(direction, result), 0)

    def test_input_rows_are_not_mutated(self):
        row = {"idx": 0, "verdict": "Error"}
        _collect_verdicts([row])
        self.assertEqual(row["verdict"], "Error")


class LegacyRunTests(unittest.TestCase):
    """Прогоны до нормализации: в снимке «Error» и балл, не узнавший крит. ошибку."""

    def _card(self, ai_score=59):
        return {"ai_score": ai_score, "criteria": [
            {"idx": 0, "ai": "Correct", "is_critical": False},
            {"idx": 16, "ai": "Error", "is_critical": True},
            {"idx": 21, "ai": "Error", "is_critical": False},
        ]}

    def test_error_is_read_as_incorrect_and_zeroes_the_score(self):
        card = self._card()
        self.assertTrue(api._normalise_legacy_ai_verdicts(card))
        self.assertEqual([c["ai"] for c in card["criteria"]], ["Correct", "Incorrect", "Incorrect"])
        self.assertEqual(card["ai_score"], 0)

    def test_incomplete_score_stays_absent_and_clean_runs_are_untouched(self):
        card = self._card(ai_score=None)
        api._normalise_legacy_ai_verdicts(card)
        self.assertIsNone(card["ai_score"])
        clean = {"ai_score": 80, "criteria": [{"idx": 0, "ai": "Incorrect", "is_critical": False}]}
        self.assertFalse(api._normalise_legacy_ai_verdicts(clean))
        self.assertEqual(clean["ai_score"], 80)
        # Некритическое «Error» балл не меняет: веса оно и раньше не давало.
        plain = {"ai_score": 70, "criteria": [{"idx": 0, "ai": "Error", "is_critical": False}]}
        self.assertTrue(api._normalise_legacy_ai_verdicts(plain))
        self.assertEqual(plain["ai_score"], 70)

    def test_score_and_queue_know_legacy_error(self):
        from call_qa.review import queue
        direction = {"criteria": [_crit(0, 100), _crit(1, None, critical=True)]}
        result = {"per_criterion": [{"idx": 0, "verdict": "Correct", "source": "transcript"},
                                    {"idx": 1, "verdict": "Error", "source": "transcript"}]}
        self.assertEqual(api._ai_score(direction, result), 0)
        reasons = queue.review_reasons([{"idx": 1, "ai": "Error", "is_critical": True,
                                         "source": "transcript", "conf": 0.9}], 0.95)
        self.assertIn("critical", reasons)


class ScaleShapeTests(unittest.TestCase):
    """Критичность и недочёт задают кнопки: их смена — тоже изменившаяся шкала."""

    def _attach(self, card_criteria, live_criteria):
        payload = {"direction_id": 1, "criteria": card_criteria}
        with mock.patch.object(api.criteria_mod, "load_direction",
                               return_value={"id": 1, "criteria": live_criteria}):
            api._attach_scale(payload)
        return payload

    def test_same_shape_is_not_a_change(self):
        crit = {"idx": 0, "name": "Лояльность", "is_critical": True, "deficiency": None, "weight": None}
        self.assertFalse(self._attach([dict(crit)], [dict(crit)])["scale_changed"])

    def test_criticality_or_deficiency_change_is_a_change(self):
        card = {"idx": 0, "name": "Лояльность", "is_critical": True, "deficiency": None}
        live = {"idx": 0, "name": "Лояльность", "is_critical": False, "deficiency": None, "weight": 5}
        self.assertTrue(self._attach([dict(card)], [live])["scale_changed"])
        card = {"idx": 0, "name": "Выявление", "is_critical": False, "deficiency": {"weight": 5}}
        live = {"idx": 0, "name": "Выявление", "is_critical": False, "deficiency": None, "weight": 10}
        self.assertTrue(self._attach([dict(card)], [live])["scale_changed"])

    def test_state_payload_without_flags_is_not_compared(self):
        # human_review_state собирает карточку из одних idx — сравнивать там нечего.
        live = {"idx": 0, "name": "Лояльность", "is_critical": True, "deficiency": None, "weight": None}
        payload = self._attach([{"idx": 0, "name": "Лояльность"}], [live])
        self.assertFalse(payload["scale_changed"])


class VerdictFromAiTests(unittest.TestCase):
    def test_legacy_error_maps_by_criticality(self):
        critical = {"idx": 3, "name": "К", "is_critical": True, "deficiency": None}
        plain = {"idx": 0, "name": "К", "is_critical": False, "deficiency": None}
        self.assertEqual(hr.verdict_from_ai(critical, "Error"), "Error")
        self.assertEqual(hr.verdict_from_ai(plain, "Error"), "Incorrect")


class _Cursor:
    def __init__(self, row, sink):
        self.row, self.sink = row, sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sink.append((sql, params))

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, row, sink):
        self.row, self.sink, self.closed = row, sink, False

    def cursor(self):
        return _Cursor(self.row, self.sink)

    def close(self):
        self.closed = True


class AiReviewStateTests(unittest.TestCase):
    def test_reviewed_run_is_reported_with_who_and_when(self):
        sink = []
        conn = _Conn(("adjudicated", "23.09.2026 17:33", "Супервайзер"), sink)
        with mock.patch.object(api.config, "connect_ro", return_value=conn):
            payload = api._attach_ai_review({"_evaluation_run_id": "run-1"})
        self.assertEqual(payload["ai_review"], {"outcome": "adjudicated",
                                                "reviewed_at": "23.09.2026 17:33",
                                                "reviewer": "Супервайзер"})
        self.assertTrue(conn.closed)
        sql, params = sink[0]
        # Связка та же, что у проверки разбора: мета прогона по его виду, id и модели.
        self.assertIn("m.model = e.model", sql)
        self.assertIn("m.subject_kind = e.subject_kind", sql)
        self.assertEqual(params, ("run-1",))

    def test_unreviewed_run_has_no_state(self):
        with mock.patch.object(api.config, "connect_ro", return_value=_Conn((None, None, None), [])):
            self.assertIsNone(api._attach_ai_review({"_evaluation_run_id": "run-1"})["ai_review"])
        with mock.patch.object(api.config, "connect_ro", return_value=_Conn(None, [])):
            self.assertIsNone(api._attach_ai_review({"_evaluation_run_id": "run-1"})["ai_review"])

    def test_no_run_means_no_query(self):
        with mock.patch.object(api.config, "connect_ro", side_effect=AssertionError("БД не нужна")):
            self.assertIsNone(api._attach_ai_review({})["ai_review"])

    def test_database_failure_does_not_break_the_card(self):
        with mock.patch.object(api.config, "connect_ro", side_effect=RuntimeError("нет базы")):
            payload = api._attach_ai_review({"_evaluation_run_id": "run-1", "id": 5})
        self.assertIsNone(payload["ai_review"])
        self.assertEqual(payload["id"], 5)

    def test_card_payload_carries_the_state(self):
        with mock.patch.object(api, "_evaluate_and_cache", return_value={"id": 1, "_evaluation_run_id": "r"}), \
                mock.patch.object(api, "_attach_human_review", side_effect=lambda p, reviewer_id=None: p), \
                mock.patch.object(api, "_attach_ai_review", side_effect=lambda p: {**p, "ai_review": {"outcome": "confirmed"}}), \
                mock.patch.object(api.runtime_store, "distributed_call_lock") as lock:
            lock.return_value.__enter__.return_value = True
            payload = api.review_payload(1, reviewer_id=7)
        self.assertEqual(payload["ai_review"], {"outcome": "confirmed"})


if __name__ == "__main__":
    unittest.main()
