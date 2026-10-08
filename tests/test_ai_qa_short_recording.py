# -*- coding: utf-8 -*-
"""Запись короче пяти секунд — не разговор (звонок 7528 от 06.10.2026).

Оператор снял трубку и через секунду положил: в записи 0,84 с и одно слово. В CDR
звонок значился на 28 с (billsec плеча очереди — вместе с автоинформатором и
ожиданием), прошёл в ежедневную выборку «от 5 с», а Gemini на секунде звука написала
разговор в 17 реплик, который оценщик оценил на 94.

Стережём:
* запись короче config.AI_QA_MIN_RECORDING_S не оценивается; неизвестная длина (None и
  0 — так её хранят старые расшифровки звонков журнала) отказом не считается;
* «Оценить случайный звонок» такой звонок не предлагает: карточка ответила бы отказом;
* выборка отдела продаж меряет разговор точным значением из журнала очередей, когда
  станция его назвала, и им же подписывает звонок;
* ссылка на запись подписывается типом по расширению файла, как в журнале.

Сам второй проход и карточка — в test_ai_qa_asr_second_pass.py.
"""
import ast
import copy
import unittest
from pathlib import Path
from unittest import mock

from call_qa import api, config, subjects
from cdr import queries as cdr_queries
from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]


class RecordingLengthGateTests(unittest.TestCase):
    def test_boundary(self):
        for duration_ms, refused in ((280, True), (840, True), (4999, True), (4999.9, True),
                                     (5000, False), (5001, False), (52000, False)):
            with self.subTest(duration_ms=duration_ms):
                if refused:
                    with self.assertRaises(subjects.SubjectNotEvaluable):
                        subjects.require_recording_length(duration_ms)
                else:
                    subjects.require_recording_length(duration_ms)

    def test_unknown_length_is_not_a_refusal(self):
        # У переписки длины нет вовсе, у старых расшифровок звонков журнала она 0.
        for duration_ms in (None, 0, 0.0):
            with self.subTest(duration_ms=duration_ms):
                subjects.require_recording_length(duration_ms)

    def test_refusal_says_how_long_the_recording_is(self):
        with self.assertRaises(subjects.SubjectNotEvaluable) as refused:
            subjects.require_recording_length(4480)
        self.assertEqual(str(refused.exception), "запись длится 4,4 с — короче 5 с, оценивать нечего")
        self.assertEqual(refused.exception.reason, "recording_too_short")
        self.assertEqual(refused.exception.detail, {"duration_ms": 4480, "min_duration_s": 5.0})
        # Десятые отброшены, не округлены: 4,999 с — это «4,9», а не «5,0 с — короче 5 с».
        with self.assertRaises(subjects.SubjectNotEvaluable) as almost:
            subjects.require_recording_length(4999)
        self.assertEqual(str(almost.exception), "запись длится 4,9 с — короче 5 с, оценивать нечего")
        # Отказ по существу, а не «не найдено»: карточка отвечает 409 с причиной,
        # ночная выборка сразу освобождает место (daily_sample._evaluate_one).
        self.assertIsInstance(refused.exception, ValueError)

    def test_threshold_is_configurable_and_zero_turns_it_off(self):
        with mock.patch.object(config, "AI_QA_MIN_RECORDING_S", 2.5):
            subjects.require_recording_length(2500)
            with self.assertRaises(subjects.SubjectNotEvaluable) as refused:
                subjects.require_recording_length(2499)
            self.assertEqual(str(refused.exception),
                             "запись длится 2,4 с — короче 2,5 с, оценивать нечего")
            self.assertEqual(refused.exception.detail, {"duration_ms": 2499, "min_duration_s": 2.5})
        with mock.patch.object(config, "AI_QA_MIN_RECORDING_S", 0.0):
            subjects.require_recording_length(280)

    def test_default_matches_the_daily_sample_window(self):
        # Обе границы — «короче пяти секунд не разговор»; разойдутся — секундная запись
        # снова пройдёт в выборку мимо одной из них.
        self.assertEqual(config.AI_QA_MIN_RECORDING_S, 5.0)
        self.assertEqual(config.AI_QA_DAILY_SAMPLE_MIN_DURATION_S, 5)


class _Cursor:
    """Запоминает запросы и отвечает по очереди заготовленными строками."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.rows = []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        self.rows = self.answers.pop(0) if self.answers else []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


class RandomCallPickTests(unittest.TestCase):
    def _pick(self, answers):
        cursor = _Cursor(answers)
        conn = mock.Mock()
        conn.cursor.return_value = cursor
        with mock.patch.object(config, "connect_ro", return_value=conn), \
                mock.patch.object(api, "_scoped_qa_family", return_value=[73, 173]):
            picked = api.random_call(None, department="op")
        return picked, [call for call in cursor.calls if "FROM imported_calls ic" in call[0]]

    def test_recognised_short_recording_is_not_offered(self):
        picked, calls = self._pick([[], [(7560, "Основа ОП", "Оператор", "06.10 12:13")]])
        self.assertEqual(picked["id"], 7560)
        sql, params = calls[0]
        self.assertIn("AND NOT EXISTS (SELECT 1 FROM ai_transcript_cache tc "
                      "WHERE tc.subject_kind = 'imported_call' AND tc.call_id = ic.id "
                      "AND tc.duration_ms > 0 AND tc.duration_ms < %s)", sql)
        # Порог — тот же, по которому карточка отказывает, в миллисекундах; параметров
        # ровно столько, сколько мест под них.
        self.assertEqual(params[-1], config.AI_QA_MIN_RECORDING_S * 1000)
        self.assertEqual(params[-1], 5000)
        self.assertEqual(sql.count("%s"), len(params))
        self.assertEqual(params[:1], ([73, 173],))
        self.assertEqual(params[-2], config.CLAUDE_MODEL)

    def test_threshold_follows_the_setting(self):
        with mock.patch.object(config, "AI_QA_MIN_RECORDING_S", 8.0):
            _, calls = self._pick([[], [(1, "Основа ОП", "Оператор", "06.10 12:13")]])
        self.assertEqual(calls[0][1][-1], 8000)


class SignedUrlTypeTests(unittest.TestCase):
    def test_type_follows_the_file_extension(self):
        for name, expected in (("uploads/freepbx-1791254671.1291639.wav", "audio/wav"),
                               ("uploads/FREEPBX-1.WAV", "audio/wav"),
                               ("uploads/tez-0c9d.mp3", "audio/mpeg"),
                               ("uploads/x.gsm", "audio/gsm"),
                               ("uploads/x.ogg", "audio/ogg"), ("uploads/x.oga", "audio/ogg"),
                               ("uploads/без-расширения", "audio/mpeg"), (None, "audio/mpeg")):
            with self.subTest(name=name):
                self.assertEqual(api._audio_response_type(name), expected)

    def test_same_rule_as_the_journal(self):
        # Журнал подписывает ссылки своей функцией в монолите (импортировать его нельзя);
        # правило у двух разделов обязано быть одно — исполняем обе и сверяем ответы.
        node = copy.deepcopy(source_cache.function_node(ROOT / "bot_schedule2.py",
                                                        "_signed_audio_response_type"))
        namespace = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "bot_schedule2.py", "exec"), namespace)
        journal = namespace["_signed_audio_response_type"]
        for name in ("a.wav", "a.WAV", "a.mp3", "a.gsm", "a.ogg", "a.oga", "a.m4a", "a.wav.mp3",
                     "wav", "a", "", None):
            with self.subTest(name=name):
                self.assertEqual(api._audio_response_type(name), journal(name))

    def _signed(self, audio_path):
        blob = mock.Mock()
        blob.generate_signed_url.return_value = "https://signed"
        client = mock.Mock()
        client.bucket.return_value.blob.return_value = blob
        with mock.patch.object(config, "google_sa_info", return_value={"project_id": "p"}), \
                mock.patch("google.oauth2.service_account.Credentials.from_service_account_info"), \
                mock.patch("google.cloud.storage.Client", return_value=client):
            url = api._signed_url(audio_path)
        return url, client, blob

    def test_link_is_signed_with_the_type_of_the_file(self):
        url, client, blob = self._signed("my-bucket/uploads/freepbx-1.2.wav")
        self.assertEqual(url, "https://signed")
        client.bucket.assert_called_once_with("my-bucket")
        client.bucket.return_value.blob.assert_called_once_with("uploads/freepbx-1.2.wav")
        self.assertEqual(blob.generate_signed_url.call_args.kwargs["response_type"], "audio/wav")
        _, _, blob = self._signed("my-bucket/uploads/tez-1.mp3")
        self.assertEqual(blob.generate_signed_url.call_args.kwargs["response_type"], "audio/mpeg")


class SampleDayCallsTests(unittest.TestCase):
    TALK = "coalesce(talk_measured_seconds, talk_seconds)"

    def _run(self, **kw):
        cursor = _Cursor([[("1791254671.1291639", "6699", "7001112233", None, "Входящий", 12,
                            "http://rec/q-3001-x.wav", "operator")]])
        rows = cdr_queries.sample_day_calls(cursor, ["6699"], "2026-10-06", ["Входящий"], **kw)
        sql, params = cursor.calls[0]
        return rows, sql, params

    def test_window_is_measured_by_the_exact_talk(self):
        rows, sql, params = self._run(min_talk=5, max_talk=300)
        for condition in (f"AND {self.TALK} > 0", f"AND {self.TALK} >= %(min_talk)s",
                          f"AND {self.TALK} <= %(max_talk)s"):
            with self.subTest(condition=condition):
                self.assertIn(condition, sql)
        # Ни одно условие и ни одна колонка не читают раздутый talk_seconds в обход точного.
        self.assertEqual(sql.count("talk_seconds"), sql.count(self.TALK))
        self.assertEqual((params["min_talk"], params["max_talk"]), (5, 300))
        # Точным же разговором звонок и подписывается (imported_calls.duration_sec).
        self.assertIn(f"call_type, {self.TALK}, recording_url", sql)
        self.assertEqual(rows[0]["talk_seconds"], 12)
        self.assertEqual(rows[0]["hangup_side"], "operator")

    def test_no_upper_bound_by_default(self):
        _, sql, params = self._run(min_talk=5)
        self.assertNotIn("max_talk", sql)
        self.assertNotIn("max_talk", params)
        self.assertEqual(sql.count("talk_seconds"), sql.count(self.TALK))


if __name__ == "__main__":
    unittest.main()
