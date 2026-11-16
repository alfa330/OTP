"""Решения модератора по самому разговору — отдельным блоком, а не прецедентом
(жалоба модератора 16.11.2026, звонок imported_call-15650).

Модератор трижды подтвердила, что продиктованный номер — номер техподдержки
«Ноль такси», а ИИ на каждой переоценке снимал штраф с исправленного критерия и
ставил то же замечание по номеру в соседний (17 → 15 → 17 → 15): её правила шли
среди прецедентов РАЗБОРЫ, к которым промпт велит применять прецедент «ТОЛЬКО к его
критерию», а справка из вики («действующие данные») с другим номером весила больше.
"""
import ast
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from call_qa.evaluation import evaluator

ROOT = Path(__file__).resolve().parents[1]
# Модель Anthropic: в теле запроса сообщение пользователя — одна строка.
MODEL = "claude-test-model"
ASK = "Оцени по всем перечисленным критериям."
NO_RULES = "(подходящих правил выше порога релевантности нет)"

CRITERIA = [
    {"idx": 15, "criterion_id": "c15", "name": "КО_Достоверность предоставляемой информации",
     "description": "d", "eval_source": "transcript"},
    {"idx": 17, "criterion_id": "c17", "name": "КО_Альтернативное решение",
     "description": "d", "eval_source": "transcript"},
]

OWN_15 = {"id": "own15", "rule_id": "own15", "similarity": .73, "own_rule": True,
          "situation": "номер телефона озвучил корректно.", "correct_verdict": "Correct",
          "reason": "номер телефона озвучил корректно.", "not_covered": None, "excerpt": None,
          "created_at": "2026-11-13T03:55:07+00:00"}
OWN_17 = {"id": "own17", "rule_id": "own17", "similarity": .78, "own_rule": True,
          "situation": "Водитель спросил про лимит, оператор продиктовал номер техподдержки",
          "correct_verdict": "Correct",
          "reason": "Данный номер принадлежит Ноль такси тех поддержке",
          "not_covered": "Не оправдывает перепутанный сервис", "excerpt": "",
          # Имя критерия в правиле — на момент разбора; в блок идёт имя из шкалы.
          "criterion_name": "КО_Альтернативное решение (прежнее имя)",
          "created_at": "2026-11-13T03:50:09+00:00"}
OTHER_15 = {"id": "near15", "rule_id": "near15", "similarity": .82,
            "situation": "Оператор назвал парк другого города",
            "correct_verdict": "Correct", "reason": "Жана такси в Таразе — верно",
            "created_at": "2026-11-10T03:56:13+00:00"}
HITS = {15: [OWN_15, OTHER_15], 17: [OWN_17]}


def _user(body) -> str:
    return body["messages"][0]["content"]


class RenderTests(unittest.TestCase):
    def test_own_rules_leave_the_precedents(self):
        text, by_criterion = evaluator._render_rag_context(CRITERIA, HITS, "ok")
        self.assertIn("правило near15", text)
        self.assertNotIn("own15", text)
        self.assertNotIn("own17", text)
        self.assertNotIn("Ноль такси", text)
        # У критерия остались только решения модератора — прецедентов по нему нет.
        self.assertEqual(by_criterion["17"], NO_RULES)
        self.assertIn("правило near15", by_criterion["15"])

    def test_own_block_lists_every_decision_with_the_scale_name_and_date(self):
        own = evaluator._render_own_decisions(CRITERIA, HITS)
        self.assertEqual(own, (
            "[критерий 15 «КО_Достоверность предоставляемой информации»; решение от 13.11.2026]\n"
            "правильно: Correct — потому что: номер телефона озвучил корректно.\n\n"
            "[критерий 17 «КО_Альтернативное решение»; решение от 13.11.2026]\n"
            "правильно: Correct — потому что: Данный номер принадлежит Ноль такси тех поддержке\n"
            "  ситуация: Водитель спросил про лимит, оператор продиктовал номер техподдержки"))
        # Границы правила пишутся для чужих разговоров — по этому модератор уже решил.
        self.assertNotIn("Не оправдывает", own)
        self.assertNotIn("near15", own)

    def test_own_block_quotes_the_moderators_fragment(self):
        hit = {**OWN_15, "excerpt": "[S2] 708. 267. 54 87."}
        own = evaluator._render_own_decisions(CRITERIA, {15: [hit]})
        self.assertIn("фрагмент разговора, на который сослался модератор: «[S2] 708. 267. 54 87.»",
                      own)

    def test_no_own_rules_no_block(self):
        self.assertEqual(evaluator._render_own_decisions(CRITERIA, {15: [OTHER_15]}), "")
        self.assertEqual(evaluator._render_own_decisions(CRITERIA, {}), "")

    def test_header_makes_the_decision_binding_for_every_criterion(self):
        header = evaluator._OWN_HEADER
        self.assertTrue(header.startswith("РЕШЕНИЯ МОДЕРАТОРА ПО ЭТОМУ РАЗГОВОРУ"))
        for phrase in ("именно этот разговор", "не прецеденты из других разговоров",
                       "ставь вердикт, который решил модератор", "для ВСЕХ критериев",
                       "выдержками из вики", "не переноси то же замечание в другой критерий",
                       "действует более позднее"):
            self.assertIn(phrase, header)


class BodyTests(unittest.TestCase):
    def test_block_goes_after_the_conversation_before_the_ask(self):
        own = evaluator._render_own_decisions(CRITERIA, HITS)
        body = evaluator.build_eval_body(
            "[S1] Нөмірін айтып жібер\n[S2] 708. 267. 54 87.", {"id": 73}, [], use_rag=True,
            model=MODEL, rag_text="R", own_text=own, asr_low_spans=[{"text": "сі"}],
            subject_kind="imported_call", wiki_text="[1; к критерию 15; статья «Номера телефонов»]")
        user = _user(body)
        transcript = user.index("ТРАНСКРИПТ ЗВОНКА:\n[S1] Нөмірін")
        low = user.index("НЕУВЕРЕННЫЕ ФРАГМЕНТЫ РАСПОЗНАВАНИЯ")
        block = user.index(evaluator._OWN_HEADER)
        self.assertLess(user.index("ВЫДЕРЖКИ ИЗ ВИКИ"), transcript)
        self.assertLess(transcript, low)
        self.assertLess(low, block)
        self.assertTrue(user.endswith(f"{evaluator._OWN_HEADER}\n{own}\n\n{ASK}"))

    def test_message_without_own_decisions_is_byte_for_byte_the_old_one(self):
        expected = (f"{evaluator._RAG_HEADER}\nR\n\nТРАНСКРИПТ ЗВОНКА:\n[S1] алло\n\n{ASK}")
        for own in (None, "", "  \n"):
            body = evaluator.build_eval_body("[S1] алло", {"id": 73}, [], use_rag=True,
                                             model=MODEL, rag_text="R", own_text=own)
            self.assertEqual(_user(body), expected)

    def test_rag_off_means_no_decisions_either(self):
        body = evaluator.build_eval_body("[S1] алло", {"id": 73}, [], use_rag=False,
                                         model=MODEL, rag_text="R", own_text="OWN")
        self.assertNotIn("РЕШЕНИЯ МОДЕРАТОРА", _user(body))
        self.assertNotIn("OWN", _user(body))

    def test_search_inside_the_body_builds_the_block_from_the_same_hits(self):
        with patch.object(evaluator, "_prepare_rag",
                          return_value=("R", {"status": "ok"}, {17: [OWN_17]})) as search:
            body = evaluator.build_eval_body("[S1] алло", {"id": 73}, CRITERIA, use_rag=True,
                                             model=MODEL, subject_kind="imported_call",
                                             subject_id=15650)
        self.assertEqual(search.call_args.kwargs["subject_id"], 15650)
        user = _user(body)
        self.assertIn(f"{evaluator._OWN_HEADER}\n[критерий 17 «КО_Альтернативное решение»", user)
        self.assertIn("Данный номер принадлежит Ноль такси тех поддержке", user)


class PrepareTests(unittest.TestCase):
    def test_prepared_context_carries_the_block_and_stays_json(self):
        with patch.object(evaluator, "_prepare_rag",
                          return_value=("R", {"status": "ok"}, HITS)):
            prepared = evaluator.prepare_rag_context(
                {"id": 73}, CRITERIA, "[S1] алло", use_rag=True,
                subject_kind="imported_call", subject_id=15650)
        self.assertEqual(prepared["own_text"], evaluator._render_own_decisions(CRITERIA, HITS))
        self.assertEqual(prepared["matched_criterion_idxs"], [15, 17])
        self.assertNotIn("own15", prepared["rag_text_by_criterion"]["15"])
        self.assertEqual(prepared["rag_text_by_criterion"]["17"], NO_RULES)
        json.dumps(prepared, ensure_ascii=False)  # уходит в манифест пакета

    def test_rag_off_prepared_context_has_no_block(self):
        prepared = evaluator.prepare_rag_context({"id": 73}, CRITERIA, "t", use_rag=False)
        self.assertEqual(prepared["own_text"], "")


class EvaluateWiringTests(unittest.TestCase):
    """Каждый проход оценки — первый, повтор недостающих, сильная модель — получает
    решения модератора ЦЕЛИКОМ: факт, подтверждённый по одному критерию, касается всех."""

    PREPARED = {"rag_text": "R", "retrieval_trace": {}, "matched_criterion_idxs": [15, 17],
                "rag_text_by_criterion": {"15": "R15", "17": "R17"}, "own_text": "OWN"}

    def _run(self, prepared):
        calls = []

        def fake(transcript, direction, criteria, **kw):
            calls.append({"stage": kw["stage"], "idxs": [c["idx"] for c in criteria],
                          "own_text": kw.get("own_text"), "rag_text": kw.get("rag_text")})
            verdict = {"confidence": .9, "evidence_quote": "", "comment": ""}
            if kw["stage"] == "bulk":
                return {"per_criterion": [{"idx": 15, "verdict": "Correct", **verdict}],
                        "overall_comment": ""}
            if kw["stage"] == "bulk_retry":
                return {"per_criterion": [{"idx": 17, "verdict": "Incorrect", **verdict}],
                        "overall_comment": ""}
            return {"per_criterion": [{"idx": 15, "verdict": "Correct", **verdict},
                                      {"idx": 17, "verdict": "Correct", **verdict}],
                    "overall_comment": ""}

        direction = {"id": 73, "criteria": [dict(c) for c in CRITERIA]}
        with patch.object(evaluator.cc, "apply_to_direction"), \
             patch.object(evaluator, "_claude_eval", side_effect=fake), \
             patch.object(evaluator.config, "CLAUDE_MODEL_BULK", "bulk-model"), \
             patch.object(evaluator.config, "CLAUDE_MODEL_HARD", "hard-model"):
            result = evaluator.evaluate("[S1] алло", direction, prepared_rag=prepared,
                                        use_wiki=False, subject_kind="imported_call",
                                        subject_id=15650)
        return calls, result

    def test_every_pass_gets_the_whole_block(self):
        calls, result = self._run(self.PREPARED)
        self.assertEqual([c["stage"] for c in calls], ["bulk", "bulk_retry", "hard"])
        self.assertEqual([c["own_text"] for c in calls], ["OWN", "OWN", "OWN"])
        # Прецеденты при этом — срезом по критериям прохода, как и раньше.
        self.assertEqual(calls[1]["rag_text"], "R17")
        self.assertEqual({c["idx"]: c["verdict"] for c in result["per_criterion"]},
                         {15: "Correct", 17: "Correct"})

    def test_batch_manifest_before_the_block_still_evaluates(self):
        prepared = {k: v for k, v in self.PREPARED.items() if k != "own_text"}
        calls, _ = self._run(prepared)
        self.assertEqual([c["own_text"] for c in calls], ["", "", ""])

    def test_the_model_request_itself_carries_the_block(self):
        """Вся цепочка до тела запроса: evaluate → _claude_eval → build_eval_body."""
        bodies = []

        def post(body, **_):
            bodies.append(body)
            verdict = {"verdict": "Correct", "confidence": .9, "evidence_quote": "", "comment": ""}
            return {"per_criterion": [{"idx": 15, **verdict}, {"idx": 17, **verdict}],
                    "overall_comment": ""}

        direction = {"id": 73, "criteria": [dict(c) for c in CRITERIA]}
        with patch.object(evaluator.cc, "apply_to_direction"), \
             patch.object(evaluator.llm, "post_body", side_effect=post), \
             patch.object(evaluator.config, "CLAUDE_MODEL_BULK", MODEL), \
             patch.object(evaluator.config, "CLAUDE_MODEL_HARD", MODEL):
            evaluator.evaluate("[S1] алло", direction, prepared_rag=self.PREPARED,
                               use_wiki=False, subject_kind="imported_call", subject_id=15650)
        self.assertEqual(len(bodies), 1)
        self.assertIn(f"{evaluator._OWN_HEADER}\nOWN\n\n{ASK}", _user(bodies[0]))


class BatchWiringTests(unittest.TestCase):
    def test_batch_request_body_carries_the_block(self):
        tree = ast.parse((ROOT / "call_qa" / "batch_eval.py").read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "build_eval_body"]
        self.assertEqual(len(calls), 1)
        keywords = {kw.arg: ast.unparse(kw.value) for kw in calls[0].keywords}
        self.assertEqual(keywords.get("own_text"), "prepared.get('own_text')")
        self.assertEqual(keywords.get("rag_text"), "prepared['rag_text']")


if __name__ == "__main__":
    unittest.main()
