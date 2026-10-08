# -*- coding: utf-8 -*-
"""Повторное распознавание слабых записей (call_qa.asr.second_pass, asr.gemini).

Стережём:
* Gemini зовётся, только когда Soniox не уверен (ниже порога) И оценка
  действительно будет считаться: открытие карточки с готовой оценкой денег не
  тратит и оценку «устаревшей» не делает;
* готовая расшифровка второго прохода берётся из кэша всегда и не пересчитывается —
  Gemini от прогона к прогону слышит по-разному;
* любой сбой второго прохода (ошибка модели, оборванный ответ, запись больше лимита,
  неправдоподобный объём текста) оставляет оценку на расшифровке Soniox;
* Gemini не достаётся то, где слышать нечего, и её текст не бывает длиннее записи:
  на секунде звука она сочиняет разговор целиком (звонок 7528), и такая расшифровка
  не берётся ни свежей, ни из кэша, а сама секундная запись не оценивается вовсе;
* расшифровка Gemini приходит в конвейер в той же форме, что у Soniox: «[S1] …» для
  оценщика, реплики с меткой голоса для карточки;
* карточка помечает «распознано повторно» по той расшифровке, которая показана.
"""
import inspect
import json
import logging
import os
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from unittest import mock

from call_qa import api, batch_eval, config, providers, subjects
from call_qa.asr import gemini, second_pass
from call_qa.evaluation import runtime_store
from call_qa.evaluation.fingerprint import content_hash, transcript_fingerprint

# Настоящие функции кэша: подделки ниже проверяют вызов по их подписи — иначе
# опечатка в аргументе ушла бы в широкий except второго прохода и в проде он молча
# не работал бы, а тесты оставались зелёными.
REAL_GET_TRANSCRIPT = runtime_store.get_transcript
REAL_PUT_TRANSCRIPT = runtime_store.put_transcript


def strict(real, fake):
    return mock.create_autospec(real, side_effect=fake)

MODEL = "gemini-3.8-flash"
SONIOX_TEXT = ("[S1] Здравствуйте, я Тимур, чем могу помочь вам сегодня\n"
               "[S2] Здравствуйте, моё такси привезло внука, трансфер нужен был")
SEGMENTS = [
    {"speaker": "S1", "role": "оператор", "lang": "mixed", "start_ms": 0, "end_ms": 5000,
     "text": "Здравствуйте, есімім Алия болады. Чем могу помочь вам?"},
    {"speaker": "S2", "role": "клиент", "lang": "kk", "start_ms": 5300, "end_ms": 14000,
     "text": "Сәлеметсіз бе, таксопаркке тіркелейін деп едім."},
    {"speaker": "S1", "role": "оператор", "lang": "kk", "start_ms": 14500, "end_ms": 16000,
     "text": "Қай қаладансыз?"},
]
# Звонок 7528 от 06.10.2026, как он лежит на проде: оператор снял трубку и через секунду
# положил. В записи 0,84 с, Soniox услышал одно слово с уверенностью 0,703 — а Gemini на
# том же звуке написала 17 реплик на 49 секунд (здесь первые и последняя).
SHORT_FIRST = {"mean_conf": 0.703, "text": "[S1] Жоқ.", "duration_ms": 840}
FABRICATED = [
    {"speaker": "S1", "role": "оператор", "lang": "ru", "start_ms": 0, "end_ms": 3700,
     "text": "Здравствуйте, «Евроколёса». Чем могу помочь?"},
    {"speaker": "S2", "role": "клиент", "lang": "ru", "start_ms": 6700, "end_ms": 15500,
     "text": "заказывал диски и резину на Camry шестьдесят четвёртый стиль. Они пришли, нет?"},
    {"speaker": "S1", "role": "оператор", "lang": "ru", "start_ms": 40100, "end_ms": 43700,
     "text": "Так. Dunlop. Да, всё на складе, можете забирать."},
    {"speaker": "S1", "role": "оператор", "lang": "ru", "start_ms": 48300, "end_ms": 49000,
     "text": "Всего доброго."},
]


def spoken(words, end_ms):
    """Одна реплика Gemini из стольких-то слов, кончается на такой-то миллисекунде."""
    return [{"speaker": "S1", "role": "оператор", "lang": "ru", "start_ms": 0, "end_ms": end_ms,
             "text": " ".join(["слово"] * words)}]


def stored_second_pass(segments):
    """Расшифровка второго прохода, как она лежит в кэше."""
    assembled = gemini.assemble([dict(s) for s in segments])
    return {"id": 77, "transcript_hash": content_hash(assembled["text"]), "text": assembled["text"],
            "segments": assembled["lines"], "tokens": [], "languages": assembled["languages"],
            "mean_conf": 0.703, "low_conf_spans": [],
            "payload": {"asr_config": gemini.config_identity(MODEL), "first_pass": {"mean_conf": 0.703}}}


def answer(segments, finish="STOP", extra_parts=()):
    raw = [{"start": "00:00.0", "end": "00:05.0", **{k: s[k] for k in ("speaker", "role", "lang", "text")}}
           for s in segments]
    parts = list(extra_parts) + [{"text": json.dumps({"segments": raw}, ensure_ascii=False)}]
    return {"candidates": [{"finishReason": finish, "content": {"parts": parts}}],
            "usageMetadata": {"promptTokenCount": 1893, "candidatesTokenCount": 1100},
            "modelVersion": MODEL}


class GeminiEngineTests(unittest.TestCase):
    def test_timecodes(self):
        for raw, expected in (("01:07.4", 67400), ("1:01:07,4", 3667400), ("67.4", 67400),
                              ("00:05", 5000), ("", None), (None, None), ("скоро", None)):
            with self.subTest(raw=raw):
                self.assertEqual(gemini._milliseconds(raw), expected)

    def test_segments_are_normalised(self):
        got = gemini.parse_segments(answer([
            {"speaker": " S1 ", "role": "оператор", "lang": "KK", "text": "  Сәлеметсіз   бе  "},
            {"speaker": "S2", "role": "", "lang": "", "text": "   "},          # пустая реплика
        ], extra_parts=[{"text": "рассуждение", "thought": True}]))
        self.assertEqual(got, [{"speaker": "S1", "role": "оператор", "lang": "kk",
                                "start_ms": 0, "end_ms": 5000, "text": "Сәлеметсіз бе"}])

    def test_truncated_or_empty_answer_is_an_error(self):
        # По обрезанному разговору оценку ставить нельзя — это ошибка, а не короткая расшифровка.
        with self.assertRaisesRegex(gemini.GeminiAsrError, "оборван"):
            gemini.parse_segments(answer(SEGMENTS, finish="MAX_TOKENS"))
        with self.assertRaisesRegex(gemini.GeminiAsrError, "нет реплик"):
            gemini.parse_segments(answer([]))
        with self.assertRaisesRegex(gemini.GeminiAsrError, "не JSON"):
            gemini.parse_segments({"candidates": [{"content": {"parts": [{"text": "не json"}]}}]})
        with self.assertRaisesRegex(gemini.GeminiAsrError, "не JSON"):
            gemini.parse_segments({})

    def test_assemble_matches_the_soniox_shape(self):
        ivr_first = [{"speaker": "IVR", "role": "IVR", "lang": "ru", "start_ms": 0, "end_ms": 900,
                      "text": "Ваш звонок записывается"}] + SEGMENTS
        got = gemini.assemble(ivr_first)
        # Голоса — числа по порядку появления, как у Soniox, даже если Gemini назвала «IVR».
        self.assertEqual([line["spk"] for line in got["lines"]], ["1", "2", "3", "2"])
        self.assertEqual(got["text"].splitlines()[0], "[S1] Ваш звонок записывается")
        self.assertEqual(got["text"].splitlines()[1],
                         "[S2] Здравствуйте, есімім Алия болады. Чем могу помочь вам?")
        line = got["lines"][2]
        self.assertEqual(line, {"spk": "3", "start_ms": 5300, "end_ms": 14000,
                                "seg": [{"t": SEGMENTS[1]["text"], "start_time_ms": 5300,
                                         "end_time_ms": 14000}]})
        # Языковой состав — по буквам; «mixed» делится поровну.
        self.assertEqual(sum(got["languages"].values()), 100)
        self.assertEqual(set(got["languages"]), {"kk", "ru"})
        mixed_only = gemini.assemble([{**SEGMENTS[0], "lang": "mixed"}])
        self.assertEqual(mixed_only["languages"], {"kk": 50, "ru": 50})
        mixed_and_russian = gemini.assemble([{**SEGMENTS[0], "lang": "mixed", "text": "абвгдежз"},
                                             {**SEGMENTS[0], "lang": "ru", "text": "абвгдежз"}])
        self.assertEqual(mixed_and_russian["languages"], {"ru": 75, "kk": 25})
        # Реплика без времени — без ключей времени, а не с None: карточка не рисует кнопку.
        untimed = gemini.assemble([{**SEGMENTS[2], "start_ms": None, "end_ms": None}])["lines"][0]
        self.assertEqual(untimed, {"spk": "1", "seg": [{"t": "Қай қаладансыз?"}]})

    def _transcribe(self, answers):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "audio.wav")
            with open(path, "wb") as audio:
                audio.write(b"RIFF" + b"\0" * 60)
            with mock.patch.object(providers, "vertex_generate", side_effect=answers) as call:
                try:
                    return gemini.transcribe_file(path, model=MODEL), call
                except Exception as exc:        # noqa: BLE001 — тест смотрит и на ошибки
                    return exc, call

    def test_request_carries_the_audio_and_no_thinking(self):
        got, call = self._transcribe([answer(SEGMENTS)])
        self.assertEqual(len(got["segments"]), 3)
        model, payload = call.call_args.args
        self.assertEqual(model, MODEL)
        part = payload["contents"][0]["parts"][0]["inlineData"]
        self.assertEqual(part["mimeType"], "audio/wav")
        self.assertTrue(part["data"])
        self.assertEqual(payload["generationConfig"]["thinkingConfig"], {"thinkingBudget": 0})
        self.assertEqual(payload["generationConfig"]["temperature"], 0)
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        self.assertIs(payload["generationConfig"]["responseSchema"], gemini.SCHEMA)
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 32768)
        # Повторов на 429/5xx внутри попытки немного: это потолок ожидания карточки.
        self.assertEqual(call.call_args.kwargs["tries"], 2)
        self.assertEqual(call.call_args.kwargs["timeout"], config.ASR_SECOND_PASS_TIMEOUT)
        # Всё, за что заплачено, сохраняется.
        self.assertEqual(got["meta"]["usage"]["promptTokenCount"], 1893)
        self.assertEqual(got["meta"]["model_version"], MODEL)
        self.assertEqual(got["meta"]["failed_attempts"], [])

    def test_empty_answer_is_retried_and_counted(self):
        got, call = self._transcribe([answer([]), answer(SEGMENTS)])
        self.assertEqual(call.call_count, 2)
        self.assertEqual(len(got["meta"]["failed_attempts"]), 1)
        failure, call = self._transcribe([answer([])] * 3)
        self.assertIsInstance(failure, gemini.GeminiAsrError)
        self.assertEqual(call.call_count, 3)

    def test_model_that_rejects_thinking_config_is_retried_without_it(self):
        got, call = self._transcribe([providers.VertexError(400, "thinking_config is not supported"),
                                      answer(SEGMENTS)])
        self.assertEqual(len(got["segments"]), 3)
        self.assertNotIn("thinkingConfig", call.call_args.args[1]["generationConfig"])
        # Другая ошибка Vertex наружу уходит как есть — решать будет second_pass.
        failure, _ = self._transcribe([providers.VertexError(429, "quota")])
        self.assertIsInstance(failure, providers.VertexError)

    def test_waiting_has_a_ceiling(self):
        # Первая попытка длилась дольше двух таймаутов — вторую не начинаем: карточка и
        # ночная выборка ждут, и лучше остаться на Soniox, чем висеть десятки минут.
        clock = iter([0.0, 10_000.0, 10_000.0])      # начало; проверка перед второй попыткой
        with mock.patch.object(gemini.time, "perf_counter", side_effect=lambda: next(clock)):
            failure, call = self._transcribe([answer([]), answer(SEGMENTS)])
        self.assertIsInstance(failure, gemini.GeminiAsrError)
        self.assertEqual(call.call_count, 1)

    def test_fenced_json_is_accepted(self):
        raw = json.dumps({"segments": [{"start": "00:01.0", "end": "00:02.0", "speaker": "S1",
                                        "role": "оператор", "lang": "ru", "text": "Здравствуйте"}]},
                         ensure_ascii=False)
        for text in (f"```json\n{raw}\n```", f"```{raw}```", f"  {raw}  "):
            with self.subTest(text=text[:12]):
                got = gemini.parse_segments({"candidates": [{"finishReason": "STOP",
                                                             "content": {"parts": [{"text": text}]}}]})
                self.assertEqual(got[0]["text"], "Здравствуйте")
                self.assertEqual(got[0]["start_ms"], 1000)

    def test_vertex_request_address(self):
        with mock.patch.object(providers, "_post", return_value={"ok": True}) as post, \
                mock.patch.object(providers._VERTEX, "base", side_effect=lambda region: f"https://v/{region}"):
            self.assertEqual(providers.vertex_generate(MODEL, {"x": 1}, timeout=12, tries=2), {"ok": True})
            providers.vertex_generate(MODEL, {"x": 1}, region="us-central1")
        first, second = post.call_args_list
        self.assertEqual(first.args, (f"https://v/{config.VERTEX_LLM_REGION}/publishers/google/models/"
                                      f"{MODEL}:generateContent", {"x": 1}))
        self.assertEqual(first.kwargs, {"timeout": 12, "tries": 2})
        self.assertTrue(second.args[0].startswith("https://v/us-central1/"))
        self.assertEqual(second.kwargs, {"timeout": config.VERTEX_TIMEOUT, "tries": config.VERTEX_TRIES})

    def test_identity_follows_the_prompt_and_model(self):
        base = gemini.config_identity(MODEL)
        self.assertEqual(base["provider"], "gemini")
        self.assertNotEqual(content_hash(base), content_hash(gemini.config_identity("gemini-3.7-flash")))
        with mock.patch.object(gemini, "PROMPT", gemini.PROMPT + " "):
            self.assertNotEqual(content_hash(base), content_hash(gemini.config_identity(MODEL)))


class _Store:
    """Кэш расшифровок в памяти: get_transcript/put_transcript с той же семантикой «кто первый»."""

    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.puts = []

    def get(self, *, call_id, audio_fingerprint_value, asr_provider, asr_model, asr_config_hash,
            subject_kind=config.SUBJECT_CALL):
        return self.rows.get((subject_kind, call_id, asr_provider))

    def put(self, **kw):
        self.puts.append(kw)
        key = (kw["subject_kind"], kw["call_id"], kw["asr_provider"])
        self.rows.setdefault(key, {"id": 900 + len(self.rows), "transcript_hash": kw["transcript_hash"],
                                   "text": kw["text"], "segments": kw["segments"], "tokens": [],
                                   "payload": kw["payload"], "languages": kw["languages"],
                                   "mean_conf": kw["mean_conf"],
                                   "low_conf_spans": kw["low_conf_spans"]})
        return self.rows[key]["id"]


class SecondPassPolicyTests(unittest.TestCase):
    def setUp(self):
        self.store = _Store()
        self.transcribe = mock.Mock(return_value={"segments": [dict(s) for s in SEGMENTS],
                                                  "meta": {"model": MODEL, "usage": {"promptTokenCount": 1893}}})
        self.downloads = []
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(second_pass.runtime_store, "get_transcript",
                                              strict(REAL_GET_TRANSCRIPT, self.store.get)))
        stack.enter_context(mock.patch.object(second_pass.runtime_store, "put_transcript",
                                              strict(REAL_PUT_TRANSCRIPT, self.store.put)))
        stack.enter_context(mock.patch.object(second_pass.gemini, "transcribe_file", self.transcribe))
        stack.enter_context(mock.patch.object(config, "ASR_SECOND_PASS_BELOW", 0.90))
        stack.enter_context(mock.patch.object(config, "ASR_SECOND_PASS_MODEL", MODEL))

    def _download(self, audio_path, dest, size=1000):
        self.downloads.append(audio_path)
        with open(dest, "wb") as audio:
            audio.write(b"\0" * size)

    def _resolve(self, mean_conf=0.88, may=True, text=SONIOX_TEXT, download=None, kind=config.SUBJECT_IMPORTED_CALL,
                 duration_ms=52000):
        asked = mock.Mock(return_value=may)
        got = second_pass.resolve(
            call_id=7, subject_kind=kind, audio_path="bucket/uploads/a.wav", audio_fingerprint="f" * 64,
            first={"mean_conf": mean_conf, "text": text, "duration_ms": duration_ms, "transcript_cache_id": 11},
            may_transcribe=asked, download=download or self._download)
        return got, asked

    def _answers(self, segments):
        self.transcribe.reset_mock()
        self.transcribe.return_value = {"segments": [dict(s) for s in segments], "meta": {}}
        self.store.rows.clear()
        self.store.puts.clear()

    def test_threshold(self):
        for mean_conf, weak in ((0.88, True), (0.899, True), (0.90, False), (0.95, False), (None, False)):
            with self.subTest(mean_conf=mean_conf):
                self.assertEqual(second_pass.is_weak(mean_conf), weak)
        with mock.patch.object(config, "ASR_SECOND_PASS_BELOW", 0):
            self.assertFalse(second_pass.is_weak(0.2))          # 0 выключает второй проход

    def test_confident_recording_is_left_alone(self):
        got, asked = self._resolve(mean_conf=0.94)
        self.assertIsNone(got)
        asked.assert_not_called()
        self.transcribe.assert_not_called()
        self.assertEqual(self.store.puts, [])

    def test_weak_recording_is_retranscribed_and_stored(self):
        got, _ = self._resolve()
        self.assertEqual(self.downloads, ["bucket/uploads/a.wav"])
        self.transcribe.assert_called_once()
        self.assertTrue(self.transcribe.call_args.args[0].endswith("audio.wav"))   # расширение записи
        self.assertEqual(got["asm"]["text"].splitlines()[0],
                         "[S1] Здравствуйте, есімім Алия болады. Чем могу помочь вам?")
        # Реплику водителя Soniox услышал иначе («такси привезло внука…») — она ненадёжна:
        # уходит оценщику как неуверенный фрагмент и подсвечивается в карточке.
        doubtful = [{"text": SEGMENTS[1]["text"], "note": second_pass.DOUBT_NOTE,
                     "start_time_ms": 5300, "end_time_ms": 14000}]
        self.assertEqual(got["asm"]["low_conf_spans"], doubtful)
        self.assertEqual([bool(line["seg"][0].get("u")) for line in got["lines"]], [False, True, False])
        self.assertEqual([line["spk"] for line in got["lines"]], ["1", "2", "1"])
        self.assertEqual([line["speaker"] for line in got["lines"]], ["operator", "client", "operator"])
        self.assertEqual(got["source_model"], MODEL)
        self.assertEqual(got["source_config"], gemini.config_identity(MODEL))
        self.assertEqual(got["transcript_hash"], content_hash(got["asm"]["text"]))
        self.assertEqual(got["asr"], {"engine": "gemini", "model": MODEL, "first_pass_conf": 0.88})
        put = self.store.puts[0]
        self.assertEqual((put["asr_provider"], put["asr_model"], put["subject_kind"]),
                         ("gemini", MODEL, config.SUBJECT_IMPORTED_CALL))
        self.assertEqual(put["asr_config_hash"], content_hash(gemini.config_identity(MODEL)))
        self.assertEqual(put["low_conf_spans"], doubtful)
        # Уверенность первого прохода — мера качества звука: по ней работает «Слабый звук».
        self.assertEqual(put["mean_conf"], 0.88)
        self.assertEqual(put["duration_ms"], 52000)
        self.assertEqual(put["languages"], got["asm"]["languages"])
        self.assertEqual(set(put["languages"]), {"kk", "ru"})
        self.assertIsNone(put["tokens"])
        self.assertEqual(put["payload"]["first_pass"],
                         {"provider": "soniox", "model": config.SONIOX_MODEL, "mean_conf": 0.88,
                          "transcript_cache_id": 11})
        self.assertEqual(put["payload"]["asr_meta"]["usage"], {"promptTokenCount": 1893})
        self.assertEqual(put["payload"]["segments"][0]["role"], "оператор")   # сырьё Gemini сохранено

    def test_opening_a_card_never_pays_for_gemini(self):
        got, asked = self._resolve(may=False)
        self.assertIsNone(got)
        asked.assert_called_once()
        self.transcribe.assert_not_called()
        self.assertEqual(self.downloads, [])

    def test_stored_second_pass_is_reused_without_asking(self):
        first, _ = self._resolve()
        self.transcribe.reset_mock()
        again, asked = self._resolve(may=False)
        asked.assert_not_called()                 # кэш важнее вопроса «будет ли оценка»
        self.transcribe.assert_not_called()
        self.assertEqual(again["transcript_cache_id"], first["transcript_cache_id"])
        self.assertEqual(again["asm"]["text"], first["asm"]["text"])

    def test_racing_writer_wins_and_its_text_is_used(self):
        # Пока мы распознавали, другой прогон уже положил свою расшифровку: в кэше остаётся
        # она, и оцениваем мы её — иначе прогон сослался бы на текст, которого в кэше нет.
        winner = {"id": 77, "transcript_hash": "w" * 64, "text": "[S1] чужая расшифровка",
                  "segments": [{"spk": "1", "speaker": "operator", "seg": [{"t": "чужая расшифровка"}]}],
                  "payload": {"asr_config": gemini.config_identity(MODEL)}, "languages": {"ru": 100},
                  "mean_conf": 0.88}
        real_put = self.store.put

        def put_after_winner(**kw):
            self.store.rows[(kw["subject_kind"], kw["call_id"], kw["asr_provider"])] = winner
            return real_put(**kw)

        with mock.patch.object(second_pass.runtime_store, "put_transcript",
                               strict(REAL_PUT_TRANSCRIPT, put_after_winner)):
            got, _ = self._resolve()
        self.assertEqual(got["transcript_cache_id"], 77)
        self.assertEqual(got["asm"]["text"], "[S1] чужая расшифровка")

    def test_any_failure_keeps_the_first_pass(self):
        # Сбой модели — предупреждение; сбой кода или окружения — ошибка с трейсбеком,
        # чтобы тихо отключившийся в проде второй проход было видно в логах.
        cases = {
            "ошибка модели": dict(transcribe=gemini.GeminiAsrError("нет ответа"), level="WARNING"),
            "квота Vertex": dict(transcribe=providers.VertexError(429, "quota"), level="WARNING"),
            "нет записи": dict(download=mock.Mock(side_effect=RuntimeError("404")), level="ERROR"),
            "ошибка в коде": dict(transcribe=TypeError("unexpected keyword"), level="ERROR"),
            "запись больше лимита": dict(max_bytes=10, level="WARNING"),
            "оборвала разговор": dict(segments=[{**SEGMENTS[2]}], level="WARNING"),
            "зациклилась": dict(segments=[dict(SEGMENTS[0])] * 12, level="WARNING"),
        }
        for name, case in cases.items():
            with self.subTest(case=name), ExitStack() as stack:
                self.store.rows.clear()
                self.store.puts.clear()
                self.transcribe.reset_mock(side_effect=True)
                if "transcribe" in case:
                    self.transcribe.side_effect = case["transcribe"]
                if "segments" in case:
                    self.transcribe.return_value = {"segments": case["segments"], "meta": {}}
                if "max_bytes" in case:
                    stack.enter_context(mock.patch.object(config, "ASR_SECOND_PASS_MAX_BYTES", case["max_bytes"]))
                with self.assertLogs(level="WARNING") as logs:
                    got, _ = self._resolve(download=case.get("download"))
                self.assertIsNone(got)
                self.assertEqual(self.store.puts, [])
                record = logs.records[-1]
                self.assertEqual(record.levelname, case["level"])
                self.assertEqual(bool(record.exc_info), case["level"] == "ERROR")
                if "max_bytes" in case or "download" in case:
                    self.transcribe.assert_not_called()

    def test_short_first_pass_is_not_compared_by_volume(self):
        got, _ = self._resolve(text="[S1] Алло\n[S2] Да")
        self.assertIsNotNone(got)

    def test_second_of_audio_never_reaches_gemini(self):
        got, asked = self._resolve(**SHORT_FIRST)
        self.assertIsNone(got)
        asked.assert_not_called()                 # не «оценка не считается», а вовсе не кандидат
        self.transcribe.assert_not_called()
        self.assertEqual(self.downloads, [])
        self.assertEqual(self.store.puts, [])
        # Граница — config.AI_QA_MIN_RECORDING_S, ровно на ней запись уже разговор.
        for duration_ms, goes in ((4999, False), (5000, True)):
            with self.subTest(duration_ms=duration_ms):
                self._answers(spoken(3, 4000))
                got, _ = self._resolve(text="[S1] Жоқ.", duration_ms=duration_ms)
                self.assertEqual(self.transcribe.call_count, int(goes))
                self.assertEqual(got is not None, goes)
        with mock.patch.object(config, "AI_QA_MIN_RECORDING_S", 0.0):       # 0 выключает правило
            self._answers(spoken(3, 800))
            got, _ = self._resolve(**SHORT_FIRST)
            self.assertIsNotNone(got)

    def test_stored_fabrication_of_a_short_recording_is_not_used(self):
        # Звонок 7528 оценён до правки: выдуманный разговор уже лежит в кэше второго прохода.
        self.store.rows[(config.SUBJECT_IMPORTED_CALL, 7, "gemini")] = stored_second_pass(FABRICATED)
        for may in (False, True):
            with self.subTest(may=may):
                got, asked = self._resolve(may=may, **SHORT_FIRST)
                self.assertIsNone(got)
                asked.assert_not_called()
                self.transcribe.assert_not_called()

    def test_transcript_longer_than_the_recording_is_rejected(self):
        # Запись шесть секунд, реплики — до сорок девятой: это не расшифровка этой записи.
        self._answers(FABRICATED)
        with self.assertLogs(level="WARNING") as logs:
            got, _ = self._resolve(text="[S1] Жоқ.", duration_ms=6000)
        self.assertIsNone(got)
        self.assertEqual(self.store.puts, [])                 # и в кэш выдумка не ложится
        self.assertEqual(logs.records[-1].levelname, "WARNING")
        self.assertIn("реплики до 49.0 с при записи в 6.0 с", logs.output[-1])
        # Время конца у Gemini плавает — допуск 20 % и 2 с: 10 с записи → до 14,0 с.
        # Последней по времени бывает и не последняя по счёту реплика.
        for end_ms, fits in ((14000, True), (14001, False)):
            with self.subTest(end_ms=end_ms):
                self._answers(spoken(3, 1000) + spoken(3, end_ms) + spoken(3, 9000))
                got, _ = self._resolve(text="[S1] Жоқ.", duration_ms=10000)
                self.assertEqual(got is not None, fits)
                self.assertEqual(len(self.store.puts), int(fits))
        # Реплика без времени длину не оценивает и проверку не роняет.
        self._answers([{**spoken(3, 0)[0], "start_ms": None, "end_ms": None}])
        got, _ = self._resolve(text="[S1] Жоқ.", duration_ms=10000)
        self.assertIsNotNone(got)

    def test_more_words_than_the_recording_can_hold_is_rejected(self):
        # Время уложено в запись, но столько за шесть секунд не сказать: до пяти слов в секунду.
        self._answers(spoken(20, 2000) + spoken(10, 5500))
        got, _ = self._resolve(text="[S1] Жоқ.", duration_ms=6000)
        self.assertIsNotNone(got)
        self._answers(spoken(21, 2000) + spoken(10, 5500))
        with self.assertLogs(level="WARNING") as logs:
            got, _ = self._resolve(text="[S1] Жоқ.", duration_ms=6000)
        self.assertIsNone(got)
        self.assertEqual(self.store.puts, [])
        self.assertIn("31 слов при записи в 6.0 с", logs.output[-1])

    def test_stored_transcript_that_does_not_fit_is_ignored(self):
        # Мерка у расшифровки из кэша та же, что у свежей: запись 20 с, реплики до 49-й.
        self.store.rows[(config.SUBJECT_IMPORTED_CALL, 7, "gemini")] = stored_second_pass(FABRICATED)
        with self.assertLogs(level="WARNING") as logs:
            got, asked = self._resolve(may=False, duration_ms=20000)
        self.assertIsNone(got)
        asked.assert_not_called()
        self.transcribe.assert_not_called()
        self.assertIn("реплики до 49.0 с при записи в 20.0 с", logs.output[-1])
        # Та же строка при записи в 49 секунд — обычная готовая расшифровка.
        got, _ = self._resolve(may=False, duration_ms=49000)
        self.assertEqual(got["transcript_cache_id"], 77)

    def test_racing_writer_that_does_not_fit_is_not_used(self):
        # Обогнавший нас прогон положил выдумку (старый код при выкладке) — её не берём.
        winner = stored_second_pass(FABRICATED)
        real_put = self.store.put

        def put_after_winner(**kw):
            self.store.rows[(kw["subject_kind"], kw["call_id"], kw["asr_provider"])] = winner
            return real_put(**kw)

        self._answers(spoken(3, 4000))
        with mock.patch.object(second_pass.runtime_store, "put_transcript",
                               strict(REAL_PUT_TRANSCRIPT, put_after_winner)), \
                self.assertLogs(level="WARNING"):
            got, _ = self._resolve(text="[S1] Жоқ.", duration_ms=6000)
        self.assertIsNone(got)

    def test_without_recording_length_the_first_pass_must_be_substantial(self):
        # Старые расшифровки звонков журнала хранят длину 0. Без длины вторую расшифровку
        # проверить можно только объёмом первой — а на паре слов сверять нечего.
        ten = "[S1] альфа бета гамма дельта эпсилон\n[S2] дзета ита тета йота каппа"
        nine = "[S1] альфа бета гамма дельта эпсилон\n[S2] дзета ита тета йота 708163 семь"
        for duration_ms in (None, 0):
            for text, goes in (("[S1] Алло\n[S2] Да", False), (nine, False), (ten, True)):
                with self.subTest(duration_ms=duration_ms, text=text):
                    self._answers(spoken(8, 49000))          # время не проверить — и не проверяем
                    got, asked = self._resolve(text=text, duration_ms=duration_ms)
                    self.assertEqual(got is not None, goes)
                    self.assertEqual(self.transcribe.call_count, int(goes))
                    self.assertEqual(asked.call_count, int(goes))

    def test_doubtful_lines_are_where_the_two_passes_disagree(self):
        def line(text):
            return {"spk": "1", "seg": [{"t": text}]}

        first = ("[S1] Здравствуйте, такси сервис, чем могу помочь вам сегодня\n"
                 "[S2] Моё такси привезло внука, трансфер нужен был срочно\n"
                 "[S1] Ваш номер 708163 записала, ожидайте звонка")
        lines = [line("Здравствуйте, такси сервис, чем могу вам помочь?"),          # подтверждено
                 line("Хочу подключиться к вашему таксопарку, как это сделать?"),   # услышано иначе
                 line("Алло, слышно?"),                                             # короткая — не сверяем
                 line("Ваш номер семь ноль восемь один шесть три записала, ожидайте звонка")]
        self.assertEqual(second_pass.doubtful_lines(lines, first), [1])
        # Числа не сравниваются: Soniox пишет цифрами, Gemini словами — реплика с номером
        # не становится ненадёжной оттого, что услышана одинаково.
        self.assertEqual(second_pass.doubtful_lines([line("Номер семь ноль восемь один шесть три")],
                                                    "[S1] Номер 708163"), [])
        # Ровно половина слов подтверждена — ещё надёжно; меньше половины — уже нет.
        self.assertEqual(second_pass.doubtful_lines([line("альфа бета гамма дельта")],
                                                    "[S1] альфа бета икс игрек"), [])
        self.assertEqual(second_pass.doubtful_lines([line("альфа бета гамма дельта")],
                                                    "[S1] альфа икс игрек зет"), [0])
        self.assertEqual(second_pass.doubtful_lines([line("альфа бета гамма дельта эпсилон")],
                                                    "[S1] альфа бета икс игрек зет"), [0])     # 2 из 5
        # Реплику короче четырёх слов не сверяем, даже если не совпало ничего.
        self.assertEqual(second_pass.doubtful_lines([line("икс игрек зет")],
                                                    "[S1] альфа бета гамма дельта"), [])
        # Нарезка реплик у распознавателей разная — сверка идёт по словам всей записи.
        split = [line("Здравствуйте, такси сервис"), line("чем могу помочь вам сегодня, слушаю")]
        self.assertEqual(second_pass.doubtful_lines(split, first), [])
        # Без первого прохода сверять не с чем — ничего не помечаем.
        self.assertEqual(second_pass.doubtful_lines(lines, ""), [])
        # Регистр не в счёт: распознаватели по-разному ставят заглавные.
        self.assertEqual(second_pass.doubtful_lines([line("ЗДРАВСТВУЙТЕ ТАКСИ СЕРВИС ЧЕМ МОГУ")], first), [])

    def test_card_note_reads_the_shown_transcript(self):
        self._resolve()
        row = self.store.rows[(config.SUBJECT_IMPORTED_CALL, 7, "gemini")]
        self.assertEqual(second_pass.card_note(row["payload"]),
                         {"engine": "gemini", "model": MODEL, "first_pass_conf": 0.88})
        for payload in ({"asr_config": {"provider": "soniox"}}, {}, None, "строка"):
            with self.subTest(payload=payload):
                self.assertIsNone(second_pass.card_note(payload))


def _soniox_record(mean_conf, duration_ms=52000):
    return {"id": 11, "transcript_hash": content_hash(SONIOX_TEXT), "text": SONIOX_TEXT,
            "segments": [{"spk": "1", "speaker": "operator", "seg": [{"t": "Здравствуйте, я Тимур"}]},
                         {"spk": "2", "speaker": "client", "seg": [{"t": "моё такси привезло внука"}]}],
            "tokens": [], "payload": {"asr_config": {"provider": "soniox"}}, "languages": {"ru": 100},
            "mean_conf": mean_conf, "low_conf_spans": [{"text": "Тимур", "min_conf": 0.18}],
            "duration_ms": duration_ms}


class CallSourceTests(unittest.TestCase):
    SUBJECT = {"id": 7, "kind": config.SUBJECT_IMPORTED_CALL, "audio_path": "bucket/uploads/a.wav",
               "call_end_party": "client"}

    def _source(self, mean_conf, second=None, **kw):
        with mock.patch.object(api, "_audio_object_fingerprint", return_value="f" * 64), \
                mock.patch.object(api.runtime_store, "asr_config", return_value={"provider": "soniox"}), \
                mock.patch.object(api.runtime_store, "get_transcript", return_value=_soniox_record(mean_conf)), \
                mock.patch.object(api.second_pass, "resolve", return_value=second) as resolve:
            return api._resolve_call_source(self.SUBJECT, "model", **kw), resolve

    def test_first_pass_is_the_source_by_default(self):
        source, resolve = self._source(0.95)
        self.assertEqual(source["asm"]["text"], SONIOX_TEXT)
        self.assertEqual(source["source_model"], config.SONIOX_MODEL)
        self.assertIsNone(source["asr"])
        first = resolve.call_args.kwargs["first"]
        self.assertEqual(first, {"mean_conf": 0.95, "text": SONIOX_TEXT, "duration_ms": 52000,
                                 "transcript_cache_id": 11})
        # Без явного разрешения источник Gemini не зовёт: умолчание — «оценка не считается».
        self.assertFalse(resolve.call_args.kwargs["may_transcribe"]())
        self.assertIs(resolve.call_args.kwargs["download"], api._download)
        # Длину записи источник отдаёт дальше: по ней секундная запись не оценивается.
        self.assertEqual(source["duration_ms"], 52000)

    def test_second_pass_replaces_the_source(self):
        second = {"asm": {"text": "[S1] есімім Алия", "languages": {"kk": 100}, "mean_conf": 0.88,
                          "low_conf_spans": []},
                  "lines": [{"spk": "1", "speaker": "operator", "seg": [{"t": "есімім Алия"}]}],
                  "transcript_cache_id": 77, "transcript_hash": "h" * 64, "source_model": MODEL,
                  "source_config": gemini.config_identity(MODEL),
                  "asr": {"engine": "gemini", "model": MODEL, "first_pass_conf": 0.88}}
        allow = mock.Mock(return_value=True)
        source, resolve = self._source(0.88, second=second, may_transcribe=allow)
        self.assertIs(resolve.call_args.kwargs["may_transcribe"], allow)
        self.assertEqual(source["asm"]["text"], "[S1] есімім Алия")
        self.assertEqual(source["transcript_cache_id"], 77)
        self.assertEqual(source["asr"]["engine"], "gemini")
        self.assertEqual(source["duration_ms"], 52000)           # длина — у записи, не у расшифровки
        # Запись та же, расшифровка другая — и отпечаток оценки обязан это видеть.
        self.assertEqual(source["source_identity"], "f" * 64)
        self.assertEqual(source["extra"], {"call_end_party": "client"})
        soniox_fp = transcript_fingerprint(audio_fingerprint="f" * 64, asr_model=config.SONIOX_MODEL,
                                           asr_config={"provider": "soniox"}, transcript=SONIOX_TEXT)
        gemini_fp = transcript_fingerprint(audio_fingerprint=source["source_identity"],
                                           asr_model=source["source_model"],
                                           asr_config=source["source_config"],
                                           transcript=source["asm"]["text"])
        self.assertNotEqual(soniox_fp, gemini_fp)

    def test_every_source_resolver_accepts_the_permission(self):
        # _evaluate_and_cache отдаёт may_transcribe любому источнику; источник без этого
        # параметра уронил бы оценку всех его субъектов (у переписки — каждой).
        for kind, resolver in api._SOURCE_RESOLVERS.items():
            with self.subTest(kind=kind):
                self.assertIn("may_transcribe", inspect.signature(resolver).parameters)

    def test_will_evaluate(self):
        with mock.patch.object(api.runtime_store, "latest_evaluation_fingerprint") as latest:
            self.assertTrue(api._will_evaluate(7, config.SUBJECT_IMPORTED_CALL, True))
            latest.assert_not_called()                        # «Переоценить» — без запроса в базу
            latest.return_value = None
            self.assertTrue(api._will_evaluate(7, config.SUBJECT_IMPORTED_CALL, False))   # первая оценка
            latest.return_value = "abc"
            self.assertFalse(api._will_evaluate(7, config.SUBJECT_IMPORTED_CALL, False))  # готовая оценка
            latest.side_effect = api.runtime_store.RuntimeSchemaUnavailable("нет схемы")
            self.assertFalse(api._will_evaluate(7, config.SUBJECT_IMPORTED_CALL, False))


class CardFlowTests(unittest.TestCase):
    """Сквозной путь review_payload → _evaluate_and_cache; заглушены база, хранилище и модели."""

    CRITS = [{"idx": 0, "criterion_id": "c0", "name": "Приветствие", "source": "transcript",
              "ai": "Correct", "conf": 0.9, "evidence": "Здравствуйте, я Тимур", "comment": ""}]

    def _open(self, *, refresh, has_run, mean_conf=0.88, second_pass_row=None, duration_ms=52000):
        kind = config.SUBJECT_IMPORTED_CALL
        store = _Store({(kind, 7, "soniox"): _soniox_record(mean_conf, duration_ms)})
        if second_pass_row:
            store.rows[(kind, 7, "gemini")] = second_pass_row
        transcribe = mock.Mock(return_value={"segments": [dict(s) for s in SEGMENTS], "meta": {"model": MODEL}})
        evaluate = mock.Mock(return_value={
            "per_criterion": [{"idx": 0, "name": "Приветствие", "source": "transcript", "verdict": "Correct",
                               "confidence": 0.9, "evidence_quote": "есімім Алия болады", "comment": ""}],
            "overall_comment": "", "retrieval_trace": {}, "_llm_meta": {}})
        cache_put = mock.Mock()
        self.mocks = {"transcribe": transcribe, "evaluate": evaluate, "cache_put": cache_put}
        run = {"id": "run-1", "is_latest": True, "evaluation_fingerprint": "fp",
               "transcript_cache_id": second_pass_row["id"] if second_pass_row else 11,
               "payload": {"id": 7, "subject_kind": kind, "criteria": list(self.CRITS)}}
        subject = {"id": 7, "kind": kind, "audio_path": "bucket/uploads/a.wav", "direction_id": 73,
                   "operator": "Алия", "datetime": None, "direction": "Основа ОП"}
        conn = mock.MagicMock()
        conn.__enter__.return_value = conn
        saved = mock.Mock()
        self.mocks["saved"] = saved

        def download(_audio_path, dest):
            with open(dest, "wb") as audio:
                audio.write(b"\0" * 100)

        def by_id(transcript_cache_id):
            return next(row for row in store.rows.values() if row["id"] == transcript_cache_id)

        def identity(*, transcript_hash, **_kw):
            return (f"fp-{transcript_hash[:8]}", {"call_end_party": "unknown", "model_config": {},
                                                  "prompt_hash": "p", "output_schema_hash": "o",
                                                  "criterion_config_hash": "c"}, {})

        patches = [
            mock.patch.object(config, "ASR_SECOND_PASS_BELOW", 0.90),
            mock.patch.object(config, "ASR_SECOND_PASS_MODEL", MODEL),
            mock.patch.object(api.subjects_mod, "load", return_value=subject),
            mock.patch.object(api.subjects_mod, "require_evaluable"),
            mock.patch.object(api.subjects_mod, "eligibility", return_value={"detail": None}),
            mock.patch.object(api, "_audio_object_fingerprint", return_value="f" * 64),
            mock.patch.object(api, "_download", download),
            mock.patch.object(api.runtime_store, "asr_config", return_value={"provider": "soniox"}),
            mock.patch.object(api.runtime_store, "get_transcript", strict(REAL_GET_TRANSCRIPT, store.get)),
            mock.patch.object(api.runtime_store, "put_transcript", strict(REAL_PUT_TRANSCRIPT, store.put)),
            mock.patch.object(api.runtime_store, "get_transcript_by_id", by_id),
            mock.patch.object(api.runtime_store, "get_cached_evaluation", return_value=run if has_run else None),
            mock.patch.object(api.runtime_store, "get_latest_evaluation", return_value=run if has_run else None),
            mock.patch.object(api.runtime_store, "latest_evaluation_fingerprint",
                              return_value="fp" if has_run else None),
            mock.patch.object(api.runtime_store, "save_evaluation_run", saved),
            mock.patch.object(api.runtime_store, "distributed_call_lock", return_value=nullcontext(True)),
            mock.patch.object(api.criteria_mod, "load_direction",
                              return_value={"id": 73, "criteria": [], "scale_hash": "s"}),
            mock.patch.object(api.cc, "apply_to_direction"),
            mock.patch.object(api, "_direction_department_code", return_value="op"),
            mock.patch.object(api.config, "connect_rw", return_value=conn),
            mock.patch("call_qa.rag.knowledge.ensure_knowledge_context",
                       return_value={"scale_revision_id": 1,
                                     "snapshot": {"id": 1, "content_hash": "c", "knowledge_revision": 1}}),
            mock.patch.object(api, "_rag_rollout", return_value={"rag_enabled": False, "shadow_enabled": False}),
            mock.patch.object(api, "_evaluation_identity", side_effect=identity),
            mock.patch.object(api, "_hydrate_cached_card_binding", return_value=True),
            mock.patch.object(api, "_normalise_legacy_ai_verdicts", return_value=False),
            mock.patch.object(api, "_cache_get", return_value={"cached": True}),
            mock.patch.object(api, "_cache_put", cache_put),
            mock.patch.object(api, "_meta_upsert"),
            mock.patch.object(api, "_signed_url", return_value=None),
            mock.patch.object(api, "_attach_human_review", side_effect=lambda p, reviewer_id=None: p),
            mock.patch.object(api, "_attach_ai_review", side_effect=lambda p: p),
            mock.patch.object(api.evaluator, "evaluate", evaluate),
            mock.patch.object(second_pass.gemini, "transcribe_file", transcribe),
        ]
        with ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            payload = api.review_payload(7, refresh=refresh, subject_kind=kind)
        return payload, transcribe, evaluate, saved

    def test_card_with_a_ready_evaluation_stays_on_the_first_pass(self):
        payload, transcribe, evaluate, _ = self._open(refresh=False, has_run=True)
        transcribe.assert_not_called()
        evaluate.assert_not_called()
        self.assertTrue(payload["_cached"])
        self.assertFalse(payload["_stale"])               # отпечаток прежний: оценка не «устарела»
        self.assertIsNone(payload["asr"])
        self.assertEqual(payload["asr_mean_conf"], 0.88)

    def test_card_of_a_run_made_on_the_second_pass_shows_it_without_a_new_call(self):
        # Звонок уже оценён по расшифровке Gemini: карточка открывается из кэша, показывает
        # ту же расшифровку с пометкой и Gemini больше не зовёт.
        row = {"id": 77, "transcript_hash": "g" * 64, "text": "[S1] есімім Алия болады",
               "segments": [{"spk": "1", "speaker": "operator", "seg": [{"t": "есімім Алия болады"}]},
                            {"spk": "2", "speaker": "client", "seg": [{"t": "тіркелейін деп едім", "u": True}]}],
               "tokens": [], "languages": {"kk": 100}, "mean_conf": 0.88,
               "low_conf_spans": [{"text": "тіркелейін деп едім", "note": second_pass.DOUBT_NOTE}],
               "payload": {"asr_config": gemini.config_identity(MODEL),
                           "first_pass": {"mean_conf": 0.88}}}
        payload, transcribe, evaluate, _ = self._open(refresh=False, has_run=True, second_pass_row=row)
        transcribe.assert_not_called()
        evaluate.assert_not_called()
        self.assertTrue(payload["_cached"])
        self.assertFalse(payload["_stale"])
        self.assertEqual(payload["asr"], {"engine": "gemini", "model": MODEL, "first_pass_conf": 0.88})
        self.assertEqual(payload["transcript"][0]["seg"][0]["t"], "есімім Алия болады")
        self.assertTrue(payload["transcript"][1]["seg"][0]["u"])

    def test_first_evaluation_of_a_weak_call_runs_on_gemini(self):
        payload, transcribe, evaluate, saved = self._open(refresh=False, has_run=False)
        transcribe.assert_called_once()
        self.assertIn("есімім Алия болады", evaluate.call_args.args[0])       # оценщик читает Gemini
        self.assertNotIn("Тимур", evaluate.call_args.args[0])
        # Реплика, которую Soniox услышал иначе, уходит оценщику как «не штрафовать»
        # и помечена в карточке.
        spans = evaluate.call_args.kwargs["asr_low_spans"]
        self.assertEqual([(span["text"], span["note"]) for span in spans],
                         [(SEGMENTS[1]["text"], second_pass.DOUBT_NOTE)])
        self.assertEqual([bool(line["seg"][0].get("u")) for line in payload["transcript"]],
                         [False, True, False])
        self.assertEqual(payload["asr"], {"engine": "gemini", "model": MODEL, "first_pass_conf": 0.88})
        self.assertEqual(payload["asr_mean_conf"], 0.88)
        self.assertEqual(payload["transcript"][0]["seg"][0]["t"], SEGMENTS[0]["text"])
        self.assertEqual(payload["speaker_roles"], {"operator": ["1"], "method": "evidence"})
        run = saved.call_args.kwargs
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["transcript_cache_id"], payload["_transcript_cache_id"])
        self.assertNotEqual(run["transcript_cache_id"], 11)                   # прогон — на расшифровке Gemini

    def test_reevaluate_button_brings_gemini_to_an_old_call(self):
        payload, transcribe, evaluate, _ = self._open(refresh=True, has_run=True)
        transcribe.assert_called_once()
        evaluate.assert_called_once()
        self.assertEqual(payload["asr"]["engine"], "gemini")

    def test_confident_call_never_reaches_gemini(self):
        payload, transcribe, evaluate, _ = self._open(refresh=True, has_run=False, mean_conf=0.95)
        transcribe.assert_not_called()
        self.assertIn("Тимур", evaluate.call_args.args[0])
        self.assertIsNone(payload["asr"])

    def test_second_of_audio_is_not_evaluated(self):
        # Звонок 7528: ни первой оценки, ни «Переоценить», ни готовой оценки из кэша —
        # иначе убранная из очереди оценка вернулась бы первым же открытием карточки.
        fabricated = stored_second_pass(FABRICATED)
        for refresh, has_run, row in ((False, False, None), (True, True, None), (False, True, None),
                                      (False, True, fabricated)):
            with self.subTest(refresh=refresh, has_run=has_run, stored=bool(row)):
                with self.assertRaises(subjects.SubjectNotEvaluable) as refused:
                    self._open(refresh=refresh, has_run=has_run, mean_conf=0.703, duration_ms=840,
                               second_pass_row=row)
                self.assertEqual(str(refused.exception),
                                 "запись длится 0,8 с — короче 5 с, оценивать нечего")
                self.assertEqual(refused.exception.reason, subjects.REASON_SHORT_RECORDING)
                self.assertEqual(refused.exception.detail, {"duration_ms": 840, "min_duration_s": 5.0})
                for name in ("transcribe", "evaluate", "saved", "cache_put"):
                    self.mocks[name].assert_not_called()

    def test_recording_of_unknown_length_is_evaluated_as_before(self):
        # Старые расшифровки звонков журнала хранят длину 0 — это «неизвестно», а не «ноль».
        for duration_ms in (0, None, 5000):
            with self.subTest(duration_ms=duration_ms):
                payload, _, evaluate, saved = self._open(refresh=False, has_run=False, mean_conf=0.95,
                                                         duration_ms=duration_ms)
                evaluate.assert_called_once()
                self.assertEqual(saved.call_args.kwargs["status"], "succeeded")
                self.assertFalse(payload["_cached"])


class BatchStageTests(unittest.TestCase):
    def test_batch_evaluates_weak_calls_on_the_second_pass(self):
        second = {"asm": {"text": "[S1] есімім Алия", "languages": {"kk": 100}, "mean_conf": 0.88,
                          "low_conf_spans": []},
                  "lines": [{"spk": "1", "speaker": "operator", "seg": [{"t": "есімім Алия"}]}],
                  "transcript_cache_id": 77, "transcript_hash": "h" * 64, "source_model": MODEL,
                  "source_config": gemini.config_identity(MODEL), "asr": {"engine": "gemini"}}
        call = {"id": 7, "audio_path": "bucket/uploads/a.wav", "direction_id": 73}
        with tempfile.TemporaryDirectory() as workdir, \
                mock.patch.object(batch_eval, "_audio_object_fingerprint", return_value="f" * 64), \
                mock.patch.object(batch_eval.runtime_store, "asr_config", return_value={"provider": "soniox"}), \
                mock.patch.object(batch_eval.runtime_store, "get_transcript", return_value=_soniox_record(0.88)), \
                mock.patch.object(batch_eval.second_pass, "resolve", return_value=second) as resolve:
            done = batch_eval.asr_stage([call], workdir, workers=1)
            with open(os.path.join(workdir, "transcripts.jsonl"), encoding="utf-8") as checkpoint:
                saved = [json.loads(line) for line in checkpoint]
        rec = next(iter(done.values()))
        self.assertEqual(rec["asm"]["text"], "[S1] есімім Алия")
        self.assertEqual((rec["source_model"], rec["transcript_cache_id"], rec["toks"]), (MODEL, 77, []))
        self.assertEqual(rec["segments"], second["lines"])
        # Пакет оценивает сам — второй проход ему разрешён; контрольная точка хранит первый.
        self.assertTrue(resolve.call_args.kwargs["may_transcribe"]())
        self.assertEqual(resolve.call_args.kwargs["first"]["mean_conf"], 0.88)
        self.assertEqual(resolve.call_args.kwargs["first"]["duration_ms"], 52000)
        self.assertEqual(saved[0]["asm"]["text"], SONIOX_TEXT)
        self.assertEqual(saved[0]["source_model"], config.SONIOX_MODEL)


if __name__ == "__main__":
    unittest.main()
