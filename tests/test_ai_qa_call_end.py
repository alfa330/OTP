# -*- coding: utf-8 -*-
"""ИИ знает, кто завершил звонок и когда: сторона из телефонии идёт в запрос оценки
и в её отпечаток, рядом — последние реплики и время от каждой до отбоя (конец
записи). Неизвестная сторона не меняет ни запроса, ни отпечатка — прежние оценки
таких звонков остаются свежими."""
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

    def test_without_timing_the_line_is_the_former_one_byte_for_byte(self):
        # Время до отбоя неизвестно (второй проход Gemini, старая расшифровка без
        # меток голоса) — вход оценки прежний, как до 05.11.2026.
        former = ("\n\nКТО ЗАВЕРШИЛ ЗВОНОК (данные телефонии, а не вывод из транскрипта): "
                  "{}. Учитывай это в критериях о завершении разговора.")
        for party, said in (("operator", "оператор"), ("client", "клиент"),
                            ("system", "станция (обрыв связи или сбой), не оператор и не клиент"),
                            ("transfer", "звонок переведён на другого сотрудника")):
            self.assertEqual(call_end.prompt_block(party), former.format(said), party)
            self.assertEqual(call_end.prompt_block(party, []), former.format(said), party)

    BYE = {"text": "Жақсы, уақыт бөлгеніңізге рақмет. Сау болыңыз.", "before_end_ms": 2610}
    TIMELINE = [{"spk": "1", "goodbye": BYE, "last": BYE},
                {"spk": "2", "goodbye": None, "last": {"text": "Рақмет.", "before_end_ms": 380}}]

    def test_timing_shows_who_hung_up_and_each_voice_goodbye(self):
        block = call_end.prompt_block("client", self.TIMELINE)
        self.assertIn("(данные телефонии и записи, а не вывод из транскрипта): "
                      "трубку положил клиент.\n", block)
        self.assertIn("Конец звонка по записи:\n"
                      "— голос S1: попрощался («Жақсы, уақыт бөлгеніңізге рақмет. Сау болыңыз.») "
                      "за 2,6 с до отбоя\n"
                      "— голос S2: не прощался; последняя реплика («Рақмет.») — за 0,4 с до отбоя\n",
                      block)
        self.assertIn("трубку положил оператор.", call_end.prompt_block("operator", self.TIMELINE))

    def test_talking_after_the_goodbye_is_shown(self):
        # Оператор попрощался и потом ещё говорил («Аха») — видно и прощание, и хвост
        # (звонок 13627 на A/B 05.11).
        voice = {"spk": "1", "goodbye": {"text": "Аха, сау болыңыз.", "before_end_ms": 3490},
                 "last": {"text": "Аха.", "before_end_ms": 370}}
        self.assertIn("— голос S1: попрощался («Аха, сау болыңыз.») за 3,5 с до отбоя, после "
                      "этого говорил ещё («Аха.») — за 0,4 с до отбоя\n",
                      call_end.prompt_block("client", [voice]))

    def test_the_same_moment_after_a_json_round_trip_is_said_once(self):
        # манифест пакета хранит конец звонка в JSON — ссылки на один и тот же момент нет
        import json
        restored = json.loads(json.dumps(self.TIMELINE))
        self.assertEqual(call_end.prompt_block("client", restored),
                         call_end.prompt_block("client", self.TIMELINE))
        self.assertNotIn("после этого говорил ещё", call_end.prompt_block("client", restored))

    OWNER_RULE = ("уточнить, остались ли у клиента вопросы, оператор должен до своего прощания — "
                  "если это было нужно, а оператор попрощался, не уточнив, это ошибка, как бы "
                  "быстро после этого ни положили трубку.")

    def test_owner_rule_ask_before_goodbye_when_the_client_hung_up(self):
        """Владелец, 05.11.2026: «оператор должен уточнить доп. вопросы до прощания».
        Не ошибка — только когда оператор не успел дойти до прощания."""
        block = call_end.prompt_block("client", self.TIMELINE)
        self.assertIn("Как учитывать в критериях о завершении разговора: " + self.OWNER_RULE
                      + " Не ошибка, только если оператор не успел дойти до прощания: клиент "
                      "прервал разговор или сам попрощался и положил трубку, не дав оператору "
                      "ответить.", block)

    def test_owner_rule_ask_before_goodbye_when_the_operator_hung_up(self):
        block = call_end.prompt_block("operator", self.TIMELINE)
        self.assertIn("Как учитывать в критериях о завершении разговора: " + self.OWNER_RULE
                      + " Трубку положил сам оператор — клиент его не прерывал.", block)

    def test_no_seconds_threshold_excuses_a_goodbye_without_asking(self):
        # Первая версия правила прощала оператору быстрый отбой клиента (< 2 с после
        # прощания); владелец отменил: спрашивать надо было до прощания.
        for party in ("client", "operator"):
            block = call_end.prompt_block(party, self.TIMELINE)
            self.assertNotIn("меньше чем через", block, party)
            self.assertNotIn("даже если до прощания он не спросил", block, party)
        self.assertFalse(hasattr(call_end, "NO_TIME_TO_ASK_MS"))

    def test_station_drop_and_transfer_ignore_timing(self):
        # Ни одна из сторон не нажимала отбой — правило про прощание к ним не относится.
        for party in ("system", "transfer"):
            self.assertEqual(call_end.prompt_block(party, self.TIMELINE),
                             call_end.prompt_block(party), party)


class TimelineTests(unittest.TestCase):
    """Конец звонка по голосам: запись идёт от ответа до отбоя, отбой — конец записи."""

    @staticmethod
    def _line(spk, text, end_ms):
        return {"spk": spk, "seg": [{"t": text, "end_time_ms": end_ms}], "end_ms": end_ms}

    def test_each_voice_gets_its_last_goodbye_and_last_line(self):
        lines = [self._line("1", "Сәлеметсіз бе", 1200), self._line("2", "Иә", 2500),
                 self._line("1", "Тағы сұрақ бар ма?", 5000), self._line("2", "Жоқ", 6100),
                 self._line("1", " Рақмет, сау болыңыз. ", 7300),
                 self._line("2", "Сау болыңыз", 7600)]
        bye_1 = {"text": "Рақмет, сау болыңыз.", "before_end_ms": 500}
        bye_2 = {"text": "Сау болыңыз", "before_end_ms": 200}
        self.assertEqual(call_end.timeline(lines, 7800), [
            {"spk": "1", "goodbye": bye_1, "last": bye_1},
            {"spk": "2", "goodbye": bye_2, "last": bye_2},
        ])

    def test_goodbye_then_more_talk_keeps_the_goodbye_moment(self):
        lines = [self._line("1", "Сұрақ болмаса, сәттілік. Аха, сау болыңыз.", 4000),
                 self._line("2", "Жақсы, сау болыңыз.", 7100), self._line("1", "Аха.", 7120)]
        got = call_end.timeline(lines, 7490)
        self.assertEqual(got[0]["goodbye"]["before_end_ms"], 3490)
        self.assertEqual(got[0]["last"], {"text": "Аха.", "before_end_ms": 370})

    def test_a_voice_without_goodbye(self):
        # «Рақмет» — не прощание: спасибо говорят и посреди разговора
        lines = [self._line("1", "Жақсы, сау болыңыз.", 5000), self._line("2", "Рақмет.", 6000)]
        got = call_end.timeline(lines, 6400)
        self.assertIsNone(got[1]["goodbye"])
        self.assertEqual(got[1]["last"], {"text": "Рақмет.", "before_end_ms": 400})

    def test_goodbye_phrases(self):
        for text in ("До свидания", "Всего доброго!", "Хорошего дня", "Спасибо за уделённое время",
                     "сау болыңыз", "Сау бол", "Қош болыңыз", "Сәттілік!",
                     "Уақыт бөлгеніңізге рақмет", "Көріскенше"):
            got = call_end.timeline([self._line("1", text, 1000)], 1500)
            self.assertIsNotNone(got[0]["goodbye"], text)
        for text in ("Рақмет", "Спасибо", "Хорошо", "Жақсы", "Алло"):
            got = call_end.timeline([self._line("1", text, 1000)], 1500)
            self.assertIsNone(got[0]["goodbye"], text)

    def test_only_the_last_lines_are_looked_at(self):
        lines = ([self._line("3", "Сау болыңыз", 100)]
                 + [self._line("1" if i % 2 else "2", f"реплика {i}", 1000 + i * 100)
                    for i in range(6)])
        self.assertEqual([voice["spk"] for voice in call_end.timeline(lines, 2000)], ["2", "1"])

    def test_lines_assembled_by_soniox_are_read_too(self):
        # soniox.assemble: text/end_time_ms вместо seg/end_ms
        lines = [{"spk": "2", "text": "Сау болыңыз", "end_time_ms": 4000}]
        moment = {"text": "Сау болыңыз", "before_end_ms": 300}
        self.assertEqual(call_end.timeline(lines, 4300),
                         [{"spk": "2", "goodbye": moment, "last": moment}])

    def test_a_long_line_keeps_its_end(self):
        text = "а" * 200 + " сау болыңыз"
        got = call_end.timeline([self._line("1", text, 9000)], 9500)[0]["goodbye"]["text"]
        self.assertTrue(got.startswith("…"))
        self.assertTrue(got.endswith("сау болыңыз"))
        self.assertLessEqual(len(got), 91)

    def test_unknown_timing_gives_nothing(self):
        lines = [self._line("1", "Сау болыңыз", 7000)]
        self.assertIsNone(call_end.timeline(lines, None))
        self.assertIsNone(call_end.timeline(lines, 0))
        self.assertIsNone(call_end.timeline(lines, "x"))
        self.assertIsNone(call_end.timeline([], 7000))
        self.assertIsNone(call_end.timeline(None, 7000))
        # длительность взята по последнему слову — тишины до отбоя не измерить
        self.assertIsNone(call_end.timeline(lines, 7000))
        # запись короче последнего слова больше чем на полсекунды — длительность не та
        self.assertIsNone(call_end.timeline(lines, 6400))

    def test_slight_overrun_counts_as_hang_up_right_away(self):
        got = call_end.timeline([self._line("2", "Рақмет", 7300)], 7000)
        self.assertEqual(got[0]["last"]["before_end_ms"], 0)

    def test_a_line_without_a_voice_mark_hides_the_timing(self):
        # без метки «S…» модель не поймёт, чья это реплика
        lines = [self._line("1", "Сау болыңыз", 5000), self._line(None, "Рақмет", 6000)]
        self.assertIsNone(call_end.timeline(lines, 6500))

    def test_empty_lines_are_skipped(self):
        lines = [self._line("1", "Сау болыңыз", 5000), self._line("2", "   ", 6000)]
        moment = {"text": "Сау болыңыз", "before_end_ms": 1500}
        self.assertEqual(call_end.timeline(lines, 6500),
                         [{"spk": "1", "goodbye": moment, "last": moment}])

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
                             self._user(call_end_party="operator", subject_kind=kind,
                                        call_end_timeline=PromptBlockTests.TIMELINE), kind)

    def test_timing_stands_next_to_the_transcript(self):
        user = self._user(call_end_party="client", call_end_timeline=PromptBlockTests.TIMELINE)
        self.assertIn("[S1] алло" + call_end.prompt_block("client", PromptBlockTests.TIMELINE),
                      user)
        self.assertTrue(user.endswith("Оцени по всем перечисленным критериям."))

    def test_every_model_pass_sees_the_party_and_the_timing(self):
        """Первый проход, повтор недостающих и эскалация на HARD-модель — без
        стороны и времени у одного из них критерий завершения оценивался бы вслепую."""
        source = (ROOT / "call_qa" / "evaluation" / "evaluator.py").read_text(encoding="utf-8")
        evaluate = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef) and node.name == "evaluate")
        passes = [node for node in ast.walk(evaluate)
                  if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_claude_eval"]
        self.assertEqual(len(passes), 3)
        for node in passes:
            keywords = {kw.arg: ast.unparse(kw.value) for kw in node.keywords}
            self.assertEqual(keywords.get("call_end_party"), "call_end_party")
            self.assertEqual(keywords.get("call_end_timeline"), "call_end_timeline")

    def test_claude_eval_hands_the_timing_to_the_body(self):
        with mock.patch.object(evaluator, "build_eval_body", return_value={}) as body, \
                mock.patch.object(evaluator.llm, "post_body", return_value={}):
            evaluator._claude_eval("[S1] алло", {"id": 74}, [], asr_low_spans=None,
                                   use_rag=False, model="m", call_end_party="client",
                                   call_end_timeline=PromptBlockTests.TIMELINE)
        self.assertEqual(body.call_args.kwargs["call_end_timeline"], PromptBlockTests.TIMELINE)


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

    def test_card_computes_the_timing_from_soniox_lines_and_the_recording(self):
        body = self._function(self.api, "_evaluate_and_cache")
        self.assertIn('call_end.timeline(lines, source.get("duration_ms"))', body)
        self.assertIn('not source.get("asr")', body)          # второй проход Gemini — без времени
        self.assertIn("config.AUDIO_SUBJECT_KINDS", body)
        # «конец записи = отбой» сверен только на записях FreePBX отдела продаж
        self.assertIn("department_code in call_end.TIMELINE_DEPARTMENTS", body)
        self.assertEqual(call_end.TIMELINE_DEPARTMENTS, frozenset({config.OP_DEPARTMENT_CODE}))
        # модель, обе тени
        self.assertEqual(body.count("call_end_timeline=call_end_timeline"), 3)
        shadow = self._function(self.api, "_run_shadow_variant")
        self.assertIn("call_end_timeline=call_end_timeline", shadow)

    def test_card_and_batch_read_the_same_lines(self):
        """Карточка восстанавливает метки голоса старых расшифровок по токенам
        (_resolve_call_source → speaker_roles.with_speaker_ids); пакет — так же, иначе
        одна и та же запись шла бы в модель с временем и без."""
        self.assertIn("speaker_roles.with_speaker_ids(transcript_record.get(\"segments\"),",
                      self.api)
        submit = self._function(self.batch, "submit_batch")
        self.assertIn('speaker_roles.with_speaker_ids(rec.get("segments"), rec.get("toks"))', submit)
        self.assertIn('rec.get("duration_ms")', submit)
        self.assertIn('not rec.get("second_pass")', submit)
        self.assertIn("department_code in call_end.TIMELINE_DEPARTMENTS", submit)
        self.assertIn("call_end_timeline=end_timeline", submit)
        self.assertIn('"call_end_timeline": end_timeline', submit)
        results = self._function(self.batch, "process_results")
        self.assertIn('call_end_timeline=entry.get("call_end_timeline")', results)
        asr = self._function(self.batch, "asr_stage")
        self.assertIn('"duration_ms": cached.get("duration_ms")', asr)
        self.assertIn('"duration_ms": duration_ms', asr)
        self.assertIn('"second_pass": True', asr)


if __name__ == "__main__":
    unittest.main()
