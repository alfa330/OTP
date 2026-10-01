# -*- coding: utf-8 -*-
"""ИИ знает, кто завершил звонок: сторона из телефонии идёт в запрос оценки и в её
отпечаток, а неизвестная не меняет ни того, ни другого — прежние оценки таких
звонков остаются свежими."""
import ast
import unittest
from pathlib import Path
from unittest import mock

from call_qa import api as call_qa_api
from call_qa import call_end, config
from call_qa.evaluation import evaluator

ROOT = Path(__file__).resolve().parents[1]


class PromptBlockTests(unittest.TestCase):
    def test_unknown_party_adds_nothing(self):
        for value in (None, "", "unknown", "кто-то"):
            self.assertEqual(call_end.prompt_block(value), "", value)

    def test_known_party_is_told_as_a_telephony_fact(self):
        block = call_end.prompt_block("operator")
        self.assertIn("КТО ЗАВЕРШИЛ ЗВОНОК", block)
        self.assertIn("данные телефонии", block)
        self.assertIn("оператор", block)
        self.assertIn("клиент", call_end.prompt_block(" CLIENT "))

    def test_first_known_value_wins(self):
        # звонок журнала без своей стороны берёт её у связанного импорта
        self.assertEqual(call_end.normalise_call_end_party("unknown", "client"), "client")
        self.assertEqual(call_end.normalise_call_end_party(None, ""), "unknown")


class EvalBodyTests(unittest.TestCase):
    def _user(self, **kw):
        with mock.patch.object(evaluator, "build_system", return_value="SYS"), \
                mock.patch.object(evaluator.llm, "build_body", side_effect=lambda **body: body):
            body = evaluator.build_eval_body("[S1] алло", {"id": 74}, [], use_rag=False,
                                             model="m", **kw)
        return body["user"]

    def test_unknown_party_leaves_the_request_byte_for_byte(self):
        base = self._user()
        self.assertEqual(self._user(call_end_party="unknown"), base)
        self.assertEqual(self._user(call_end_party=None), base)

    def test_known_party_stands_next_to_the_transcript(self):
        user = self._user(call_end_party="client")
        self.assertIn("[S1] алло" + call_end.prompt_block("client"), user)
        self.assertTrue(user.endswith("Оцени по всем перечисленным критериям."))

    def test_known_party_does_not_touch_the_system_prompt(self):
        # системный блок закреплён эталонным хэшем и кэшируется провайдером
        with mock.patch.object(evaluator, "build_system", return_value="SYS") as system, \
                mock.patch.object(evaluator.llm, "build_body", side_effect=lambda **body: body):
            body = evaluator.build_eval_body("[S1] алло", {"id": 74}, [], use_rag=False,
                                             model="m", call_end_party="operator")
        self.assertEqual(body["system"], "SYS")
        self.assertNotIn("call_end_party", system.call_args.kwargs)

    def test_a_chat_has_no_hangup(self):
        for kind in config.CHAT_SUBJECT_KINDS:
            self.assertNotIn("КТО ЗАВЕРШИЛ",
                             self._user(call_end_party="operator", subject_kind=kind), kind)

    def test_every_model_pass_sees_the_party(self):
        """Первый проход, повтор недостающих и эскалация на HARD-модель — без
        стороны у одного из них критерий завершения оценивался бы вслепую."""
        source = (ROOT / "call_qa" / "evaluation" / "evaluator.py").read_text(encoding="utf-8")
        evaluate = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef) and node.name == "evaluate")
        passes = [node for node in ast.walk(evaluate)
                  if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_claude_eval"]
        self.assertEqual(len(passes), 3)
        for node in passes:
            self.assertIn("call_end_party", {kw.arg for kw in node.keywords})


class FingerprintTests(unittest.TestCase):
    DIRECTION = {"criteria": [], "scale_hash": "s"}

    def _identity(self, **kw):
        return call_qa_api._evaluation_identity(
            transcript_hash="t", direction=self.DIRECTION,
            knowledge_snapshot={"content_hash": "k"}, use_rag=False, **kw)

    def test_unknown_party_keeps_existing_fingerprints(self):
        base = self._identity()[0]
        self.assertEqual(self._identity(call_end_party="unknown")[0], base)
        self.assertEqual(self._identity(call_end_party=None)[0], base)

    def test_a_known_party_is_another_evaluation(self):
        prints = {self._identity(call_end_party=party)[0]
                  for party in (None, "operator", "client", "system", "transfer")}
        self.assertEqual(len(prints), 5)

    def test_a_chat_ignores_the_party(self):
        for kind in config.CHAT_SUBJECT_KINDS:
            self.assertEqual(self._identity(subject_kind=kind, call_end_party="operator")[0],
                             self._identity(subject_kind=kind)[0], kind)

    def test_components_keep_the_bare_transcript(self):
        _, components, _ = self._identity(subject_kind=config.SUBJECT_IMPORTED_CALL,
                                          call_end_party="client")
        self.assertEqual(components["source_transcript_hash"], "t")
        self.assertEqual(components["call_end_party"], "client")
        self.assertEqual(components["transcript_hash"], call_end.identity("t", "client"))


class QueueStalenessTests(unittest.TestCase):
    """Очередь и карточка обязаны сходиться в пометке «устарела», в том числе когда
    сторона стала известна уже после оценки (ночной бэкфилл Binotel)."""

    CTX = {"direction": {"id": 74, "criteria": [], "scale_hash": "s"}, "mode": "shadow",
           "canary_percent": 0, "snapshot_hash": "k", "department_code": None}

    def _run(self, party, *, legacy=False):
        fingerprint, components, _ = call_qa_api._evaluation_identity(
            transcript_hash="t", direction=self.CTX["direction"],
            knowledge_snapshot={"content_hash": "k"}, use_rag=False,
            subject_kind=config.SUBJECT_IMPORTED_CALL, call_end_party=party)
        components = dict(components)
        if legacy:      # прогон до учёта стороны: ни голого транскрипта, ни стороны
            components.pop("source_transcript_hash")
            components.pop("call_end_party")
        return fingerprint, components

    def _stale(self, run, party_now):
        fingerprint, components = run
        items = [{"id": 1, "subject": config.SUBJECT_IMPORTED_CALL, "_direction_id": 74,
                  "_run_fp": fingerprint, "_run_components": components,
                  "_call_end_party": party_now}]
        with mock.patch.object(call_qa_api, "_direction_identity_context", return_value=self.CTX):
            call_qa_api._flag_stale_evaluations(items)
        return items[0]["stale"]

    def test_old_run_stays_fresh_while_the_party_is_unknown(self):
        self.assertFalse(self._stale(self._run(None, legacy=True), None))
        self.assertFalse(self._stale(self._run(None, legacy=True), "unknown"))

    def test_old_run_goes_stale_once_the_party_is_known(self):
        self.assertTrue(self._stale(self._run(None, legacy=True), "client"))

    def test_run_with_the_party_is_fresh(self):
        self.assertFalse(self._stale(self._run("client"), "client"))
        self.assertTrue(self._stale(self._run("client"), "operator"))

    def test_queue_reads_the_party_like_the_card_loader(self):
        source = (ROOT / "call_qa" / "api.py").read_text(encoding="utf-8")
        self.assertIn("{_SUBJECT_OPERATOR_ID}, {end_col}", source)
        self.assertIn('"deal": _deal_row(r, 18)', source)
        self.assertIn('"_call_end_party": r[17]', source)
        self.assertIn("x.id = c.imported_call_id", call_qa_api._SUBJECT_CALL_END_PARTY)
        self.assertIn("_call_end_party", call_qa_api._QUEUE_PRIVATE_KEYS)


class WiringTests(unittest.TestCase):
    """Сторона доходит до модели всеми путями оценки."""

    @classmethod
    def setUpClass(cls):
        cls.api = (ROOT / "call_qa" / "api.py").read_text(encoding="utf-8")
        cls.batch = (ROOT / "call_qa" / "batch_eval.py").read_text(encoding="utf-8")

    def _function(self, source, name):
        node = next(item for item in ast.parse(source).body
                    if isinstance(item, ast.FunctionDef) and item.name == name)
        return ast.get_source_segment(source, node)

    def test_card_evaluation_and_shadow_pass_it_on(self):
        body = self._function(self.api, "_evaluate_and_cache")
        self.assertIn('call_end_party = subject.get("call_end_party")', body)
        self.assertEqual(body.count("call_end_party=call_end_party"), 3)   # отпечаток + две тени
        self.assertIn('call_end_party=fingerprint_components["call_end_party"]', body)
        shadow = self._function(self.api, "_run_shadow_variant")
        self.assertIn('call_end_party=components["call_end_party"]', shadow)

    def test_batch_signs_and_sends_the_same_party(self):
        self.assertIn('call_end_party=call.get("call_end_party")', self.batch)
        self.assertIn('call_end_party=components["call_end_party"]', self.batch)
        self.assertIn('"call_end_party": normalise_call_end_party(r[7], r[8])', self.batch)


if __name__ == "__main__":
    unittest.main()
