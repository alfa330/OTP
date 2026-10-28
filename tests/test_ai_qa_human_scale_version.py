"""Оценка человека по прежней версии шкалы не ложится на критерии новой.

28.10.2026 шкалу обзвона АВР сменили (11 критериев → 13), а 64 звонка к тому
времени уже проверила модератор: её строки журнала и «Моя оценка» перевешены на
архивную строку шкалы. Вердикты человека позиционные (scores[idx]), поэтому после
«Переоценить» карточка, списки, согласие ИИ↔человек и сводка не должны прикладывать
их к новым критериям — только к оценке ИИ по тем же критериям.
"""
import ast
import unittest
from unittest import mock

from call_qa import api, config, subjects
from tests import source_cache

OLD = ["Предложение подписать", "Помощь пройти подписание", "Проверка, что документы подписаны"]
NEW = ["Проверка данных в ЭДО", "Предложение подписать", "Помощь пройти подписание",
       "Подтверждение подписания"]
NAMES = {96: NEW, 97: OLD}   # 96 — живая строка шкалы, 97 — её архив (прежняя версия)


def _card(names, kind=config.SUBJECT_IMPORTED_CALL, direction_id=96):
    return {"id": 10775, "subject_kind": kind, "direction_id": direction_id,
            "criteria": [{"idx": i, "name": name, "ai": "Correct"} for i, name in enumerate(names)]}


def _journal(direction_id, scores, score=85):
    return {"id": 501, "scores": scores, "criterion_comments": ["коммент"] * len(scores),
            "score": score, "evaluator_id": 1, "evaluator": "Омарова Ару",
            "datetime": "27.10.2026 18:42", "direction_id": direction_id}


def _review(direction_id, scores, score=85):
    return {"id": 9, "direction_id": direction_id, "scores": scores,
            "criterion_comments": [""] * len(scores), "score": score, "comment": "",
            "comment_visible_to_operator": True, "counted_in_quality": True,
            "journal_call_id": 501, "updated_at": None}


class CardTests(unittest.TestCase):
    def _attach(self, payload, journal=None, review=None, names=NAMES):
        def lookup(ids):
            return {i: names[i] for i in ids if i is not None and i in names}

        with mock.patch.object(api, "journal_review_for_subject", return_value=journal), \
                mock.patch.object(api.human_review_mod, "get_review", return_value=review), \
                mock.patch.object(api, "_criteria_names_by_direction", side_effect=lookup), \
                mock.patch.object(api, "_attach_scale"):
            return api._attach_human_review(payload, reviewer_id=1)

    def test_previous_scale_review_is_not_laid_over_new_criteria(self):
        old = ["Correct", "Incorrect", "Correct"]
        payload = self._attach(_card(NEW), _journal(97, old), _review(97, old))
        self.assertEqual([c["human"] for c in payload["criteria"]], [None] * len(NEW))
        self.assertIsNone(payload["human_score"])
        self.assertFalse(payload["has_human_review"])
        self.assertIsNone(payload["human_review"])
        # «Моя оценка» по старым критериям не заполняет панель новой карточки.
        self.assertIsNone(payload["my_review"])
        # Но о строке журнала проверяющий знает: балл, кто и когда.
        self.assertEqual(payload["previous_scale_review"],
                         {"call_id": 501, "score": 85, "evaluator": "Омарова Ару",
                          "datetime": "27.10.2026 18:42"})

    def test_card_on_the_same_criteria_shows_the_review(self):
        # Карточка, ещё не переоценённая (прогон по прежней шкале), и оценка по ней же.
        old = ["Correct", "Incorrect", "Correct"]
        payload = self._attach(_card(OLD), _journal(97, old), _review(97, old))
        self.assertEqual([c["human"] for c in payload["criteria"]], ["Correct", "Incorrect", "Correct"])
        self.assertEqual(payload["human_score"], 85)
        self.assertTrue(payload["has_human_review"])
        self.assertIsNone(payload["previous_scale_review"])
        self.assertEqual(payload["my_review"]["scores"], old)

    def test_new_review_after_the_change_is_shown(self):
        new = ["Correct", "Correct", "Incorrect", "Correct"]
        payload = self._attach(_card(NEW), _journal(96, new, score=75), _review(96, new, score=75))
        self.assertEqual(payload["human_score"], 75)
        self.assertEqual(payload["my_review"]["score"], 75)
        self.assertIsNone(payload["previous_scale_review"])

    def test_own_review_of_old_scale_hides_even_without_journal(self):
        payload = self._attach(_card(NEW), None, _review(97, ["Correct"] * 3))
        self.assertIsNone(payload["my_review"])
        self.assertIsNone(payload["previous_scale_review"])

    def test_state_payload_without_names_compares_with_the_live_scale(self):
        # human_review_state собирает карточку из одних idx — имена берутся у живой шкалы.
        state = {"id": 10775, "subject_kind": config.SUBJECT_IMPORTED_CALL, "direction_id": 96,
                 "criteria": [{"idx": i} for i in range(len(NEW))]}
        payload = self._attach(dict(state, criteria=[dict(c) for c in state["criteria"]]),
                               _journal(96, ["Correct"] * 4, score=100))
        self.assertEqual(payload["human_score"], 100)
        payload = self._attach(dict(state, criteria=[dict(c) for c in state["criteria"]]),
                               _journal(97, ["Correct"] * 3))
        self.assertIsNone(payload["human_score"])
        self.assertEqual(payload["previous_scale_review"]["score"], 85)

    def test_journal_call_is_its_own_human_review(self):
        # У звонка журнала оценка человека — сама строка: сверять нечего, и имена
        # шкал даже не читаются.
        with mock.patch.object(api, "journal_review_for_subject",
                               return_value=_journal(97, ["Correct", "Incorrect", "Correct"])), \
                mock.patch.object(api.human_review_mod, "get_review", return_value=None), \
                mock.patch.object(api, "_criteria_names_by_direction",
                                  side_effect=AssertionError("для звонка журнала не нужно")), \
                mock.patch.object(api, "_attach_scale"):
            payload = api._attach_human_review(_card(NEW[:3], kind=config.SUBJECT_CALL), reviewer_id=1)
        self.assertEqual(payload["human_score"], 85)
        self.assertIsNone(payload["previous_scale_review"])

    def test_failed_scale_lookup_keeps_the_review(self):
        with mock.patch.object(api, "journal_review_for_subject",
                               return_value=_journal(97, ["Correct"] * 3)), \
                mock.patch.object(api.human_review_mod, "get_review", return_value=None), \
                mock.patch.object(api, "_criteria_names_by_direction", side_effect=RuntimeError("нет базы")), \
                mock.patch.object(api, "_attach_scale"), \
                self.assertLogs(level="ERROR"):
            payload = api._attach_human_review(_card(NEW), reviewer_id=1)
        self.assertEqual(payload["human_score"], 85)

    def test_state_after_save_reports_previous_scale_review(self):
        # _attach_human_review дописывает карточку на месте — так и подменяем.
        with mock.patch.object(api, "_attach_human_review",
                               side_effect=lambda p, reviewer_id=None: p.update(
                                   previous_scale_review={"score": 85})):
            state = api.human_review_state(config.SUBJECT_IMPORTED_CALL, 10775, 96, 4, 1)
        self.assertEqual(state["previous_scale_review"], {"score": 85})


class SqlTests(unittest.TestCase):
    MARK = "jsonb_path_query_array(hd.criteria, '$[*].name')"

    def test_condition_compares_names_in_order(self):
        sql = subjects.same_criteria_sql("hc.direction_id", "rc.payload->'criteria'")
        self.assertIn("hd.id = hc.direction_id", sql)
        self.assertIn(self.MARK + " = jsonb_path_query_array(rc.payload->'criteria', '$[*].name')", sql)

    def test_human_score_checks_every_branch_but_the_journal_call(self):
        sql = api._SUBJECT_HUMAN_SCORE
        # Ветки: звонок из АТС, Chat2Desk, Wazzup, ChatApp, «Моя оценка».
        self.assertEqual(sql.count(self.MARK), 5)
        self.assertIn("hd.id = hr.direction_id", sql)
        first_branch = sql[:sql.index("(SELECT")]
        self.assertIn("c.score", first_branch)
        self.assertNotIn(self.MARK, first_branch)

    def test_agreement_benchmark_checks_every_human_source(self):
        sql = api._RAW_BENCHMARK_SQL
        self.assertEqual(sql.count(self.MARK), 5)
        self.assertEqual(sql.count("jsonb_path_query_array(t.criteria, '$[*].name')"), 5)

    def test_subject_loader_takes_human_score_of_the_evaluation_scale(self):
        node = source_cache.function_node(subjects.__file__, "_load_imported_call")
        source = ast.get_source_segment(source_cache.read(subjects.__file__), node)
        self.assertIn('same_criteria_sql("c.direction_id",', source)
        self.assertIn('"COALESCE(dq.criteria, d.criteria)"', source)


if __name__ == "__main__":
    unittest.main()
