# -*- coding: utf-8 -*-
"""Эпизод переписки как субъект ИИ-оценки: порог атрибуции, вложения, изоляция от звонков."""
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from call_qa import config, media, subjects
from call_qa.evaluation import runtime_store
from call_qa.review import queue as review_queue

ROOT = Path(__file__).resolve().parents[1]
ALMATY = ZoneInfo("Asia/Almaty")


def _episode(**over):
    base = {
        "kind": config.SUBJECT_WZ_EPISODE, "id": 42,
        "channel_id": "ch", "chat_id": "chat", "chat_type": "whatsapp",
        "contact_name": "Клиент", "contact_phone": "77000000000",
        "started_at": datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc),
        "ended_at": datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc),
        "messages_count": 6, "inbound_count": 3, "outbound_count": 3,
        "human_outbound_count": 3, "episode_kind": "dialog",
        "operator_user_id": 7, "operator": "Оператор Один", "operator_share": 1.0,
        "authors": [], "force_closed": False, "raw_transcript": "", "context_tail": None,
        "direction_id": 71, "direction": "Верификатор",
        "eligible_direction_ids": [71], "datetime": "20.07.2026, 15:00",
        "human_score": None,
    }
    base.update(over)
    return base


def _msg(mid, *, echo=False, text=None, mtype="text", uri=None, user_id=None,
         name="Оператор Один", bot=False, minute=0):
    return {"message_id": mid, "dt": datetime(2026, 7, 20, 8, minute, tzinfo=timezone.utc),
            "is_echo": echo, "type": mtype, "text": text, "content_uri": uri,
            "author_name": name, "author_id": f"a{mid}", "is_bot": bot,
            "is_deleted": False, "user_id": user_id, "matched_name": name,
            "channel_id": "ch", "chat_id": "chat"}


class OperatorShareGateTests(unittest.TestCase):
    """Порог 90%: чат с несколькими отвечавшими операторами оценить нельзя."""

    def test_single_operator_episode_is_evaluable(self):
        verdict = subjects.eligibility(_episode(operator_share=1.0, human_outbound_count=3))
        self.assertTrue(verdict["ok"])
        self.assertEqual(verdict["detail"]["operator_share_pct"], 100)

    def test_share_exactly_at_threshold_is_evaluable(self):
        with mock.patch.object(config, "WZ_MIN_OPERATOR_SHARE", 0.9):
            verdict = subjects.eligibility(_episode(operator_share=0.9, human_outbound_count=10))
        self.assertTrue(verdict["ok"], verdict.get("message"))

    def test_share_below_threshold_is_rejected_with_numbers(self):
        with mock.patch.object(config, "WZ_MIN_OPERATOR_SHARE", 0.9):
            verdict = subjects.eligibility(_episode(operator_share=0.857, human_outbound_count=7))
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["reason"], subjects.REASON_SHARE)
        # Причина должна называть числа: проверяющий видит, почему чат отклонён.
        self.assertIn("86%", verdict["message"])
        self.assertIn("7", verdict["message"])
        self.assertIn("90%", verdict["message"])

    def test_unattributed_episode_is_rejected(self):
        verdict = subjects.eligibility(_episode(operator_user_id=None, operator_share=None))
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["reason"], subjects.REASON_NO_OPERATOR)

    def test_non_dialog_episode_is_rejected(self):
        verdict = subjects.eligibility(_episode(episode_kind="unanswered"))
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["reason"], subjects.REASON_KIND)

    def test_foreign_direction_is_rejected(self):
        verdict = subjects.eligibility(_episode(direction_id=73, direction="Основа ОП"))
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["reason"], subjects.REASON_DIRECTION)

    def test_too_few_operator_messages_is_rejected(self):
        with mock.patch.object(config, "WZ_MIN_OPERATOR_MESSAGES", 2):
            verdict = subjects.eligibility(_episode(operator_share=1.0, human_outbound_count=1))
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["reason"], subjects.REASON_FEW_MESSAGES)

    def test_require_evaluable_raises_with_reason(self):
        with self.assertRaises(subjects.SubjectNotEvaluable) as ctx:
            subjects.require_evaluable(_episode(operator_share=0.5, human_outbound_count=4))
        self.assertEqual(ctx.exception.reason, subjects.REASON_SHARE)
        self.assertEqual(ctx.exception.detail["operator_share_pct"], 50)

    def test_calls_are_always_evaluable(self):
        self.assertTrue(subjects.eligibility({"kind": config.SUBJECT_CALL, "id": 1})["ok"])


class ChatTranscriptTests(unittest.TestCase):
    """Текст для модели и строки карточки строятся из одного прохода."""

    def setUp(self):
        self.messages = [
            _msg("m1", text="Здравствуйте", minute=1),
            _msg("m2", echo=True, text="Добрый день", user_id=7, minute=2),
            _msg("m3", mtype="image", uri="https://store/x.jpg", minute=3),
            _msg("m4", echo=True, text="Принял", user_id=9, name="Оператор Два", minute=4),
            _msg("m5", echo=True, text="Акция", bot=True, name="Autocontact", minute=5),
        ]

    def _build(self, annotations=None):
        return subjects.build_wz_transcript(_episode(), self.messages, annotations or {})

    def test_evaluated_operator_is_named_and_others_are_marked(self):
        built = self._build()
        self.assertIn("ОЦЕНИВАЕМЫЙ ОПЕРАТОР: Оператор Один", built["text"])
        self.assertIn("Оператор (Оператор Один)", built["text"])
        # чужой сотрудник и рассылка не должны выглядеть как оцениваемый оператор
        self.assertIn("Другой сотрудник (Оператор Два)", built["text"])
        self.assertIn("Рассылка (Autocontact)", built["text"])
        speakers = [line["speaker"] for line in built["lines"]]
        self.assertEqual(speakers, ["client", "operator", "client", "other_operator", "bot"])

    def test_card_lines_and_model_text_carry_the_same_words(self):
        built = self._build({"m3": {"status": "ready", "annotation": "скриншот оплаты"}})
        card_text = "\n".join("".join(seg["t"] for seg in line["seg"]) for line in built["lines"])
        for fragment in ("Здравствуйте", "Добрый день", "скриншот оплаты", "Принял"):
            self.assertIn(fragment, card_text)
            self.assertIn(fragment, built["text"])

    def test_ready_image_annotation_replaces_placeholder(self):
        built = self._build({"m3": {"status": "ready", "annotation": "скриншот оплаты на 5000 тенге"}})
        self.assertIn("[фото: скриншот оплаты на 5000 тенге]", built["text"])
        self.assertNotIn("[фото]\n", built["text"])

    def test_failed_annotation_tells_model_not_to_penalise(self):
        built = self._build({"m3": {"status": "unavailable", "error": "HTTP 404"}})
        self.assertIn("не удалось получить вложение", built["text"])
        self.assertIn("не оценивать содержание", built["text"])

    def test_media_url_reaches_the_card_line(self):
        built = self._build()
        media_line = next(line for line in built["lines"] if line.get("media"))
        self.assertEqual(media_line["media"]["kind"], "image")
        self.assertEqual(media_line["media"]["url"], "https://store/x.jpg")

    def test_local_timezone_is_used_for_stamps(self):
        built = self._build()
        expected = self.messages[0]["dt"].astimezone(ALMATY).strftime("%d.%m %H:%M")
        self.assertEqual(built["lines"][0]["ts"], expected)


class TranscriptIdentityTests(unittest.TestCase):
    """Появление расшифровки вложения обязано менять идентичность транскрипта."""

    def test_media_plan_participates_in_source_identity(self):
        plan = [{"message_id": "m3", "media_kind": "image", "source_hash": "a" * 64,
                 "provider": "anthropic", "model": "claude-sonnet-5", "config_hash": "b" * 64}]
        without = subjects.wz_source_config(media_plan=[], media_source="messages")
        with_media = subjects.wz_source_config(media_plan=plan, media_source="messages")
        episode = _episode()
        self.assertNotEqual(subjects.wz_source_identity(episode, without),
                            subjects.wz_source_identity(episode, with_media))

    def test_identity_is_keyed_on_chat_not_on_serial_id(self):
        cfg = subjects.wz_source_config(media_plan=[], media_source="messages")
        same_chat_other_id = subjects.wz_source_identity(_episode(id=999), cfg)
        self.assertEqual(subjects.wz_source_identity(_episode(), cfg), same_chat_other_id)
        other_chat = subjects.wz_source_identity(_episode(chat_id="another"), cfg)
        self.assertNotEqual(same_chat_other_id, other_chat)

    def test_expired_media_source_gives_a_different_identity(self):
        episode = _episode()
        fresh = subjects.wz_source_config(media_plan=[], media_source="messages")
        expired = subjects.wz_source_config(media_plan=[], media_source="expired")
        self.assertNotEqual(subjects.wz_source_identity(episode, fresh),
                            subjects.wz_source_identity(episode, expired))


class MediaAnnotationTests(unittest.TestCase):
    def test_plan_only_covers_readable_media(self):
        messages = [
            _msg("m1", mtype="image", uri="https://s/a.jpg"),
            _msg("m2", mtype="audio", uri="https://s/a.ogg"),
            _msg("m3", mtype="document", uri="https://s/a.pdf"),
            _msg("m4", mtype="image", uri=None),
            _msg("m5", text="просто текст"),
        ]
        plan = media.plan(messages)
        self.assertEqual([item["message_id"] for item in plan], ["m1", "m2"])
        self.assertEqual(plan[0]["provider"], "anthropic")
        self.assertEqual(plan[0]["model"], config.CLAUDE_MODEL_VISION)
        self.assertEqual(plan[1]["provider"], "soniox")

    def test_plan_is_capped_per_episode(self):
        messages = [_msg(f"m{i}", mtype="image", uri=f"https://s/{i}.jpg") for i in range(50)]
        with mock.patch.object(config, "MEDIA_MAX_PER_EPISODE", 5):
            self.assertEqual(len(media.plan(messages)), 5)

    def test_vision_request_is_cheap_and_structured(self):
        body = media.image_request_body("ZmFrZQ==", "image/jpeg")
        self.assertEqual(body["model"], config.CLAUDE_MODEL_VISION)
        # описание картинки не нуждается в рассуждении: effort low + thinking off
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["output_config"]["effort"], config.CLAUDE_VISION_EFFORT)
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        blocks = body["messages"][0]["content"]
        self.assertEqual(blocks[0]["type"], "image")
        self.assertEqual(blocks[0]["source"]["media_type"], "image/jpeg")
        # короткий системный блок не достигает порога кэширования — не притворяемся
        self.assertNotIn("cache_control", body["system"][0])

    def test_thinking_is_omitted_for_models_that_reject_disabling_it(self):
        with mock.patch.object(config, "CLAUDE_MODEL_VISION", "claude-fable-5"):
            self.assertIsNone(media._vision_thinking())
        with mock.patch.object(config, "CLAUDE_MODEL_VISION", "claude-opus-5"), \
             mock.patch.object(config, "CLAUDE_VISION_EFFORT", "xhigh"):
            self.assertIsNone(media._vision_thinking())
        with mock.patch.object(config, "CLAUDE_MODEL_VISION", "claude-opus-5"), \
             mock.patch.object(config, "CLAUDE_VISION_EFFORT", "low"):
            self.assertEqual(media._vision_thinking(), {"type": "disabled"})
        with mock.patch.object(config, "CLAUDE_MODEL_VISION", "claude-sonnet-5"):
            self.assertEqual(media._vision_thinking(), {"type": "disabled"})

    def test_truncated_vision_answer_is_a_failure_not_a_description(self):
        """Обрезанный на середине документ нельзя выдавать за описание."""
        reply = {"description": "паспорт", "visible_text": "ИИН 123", "kind": "document",
                 "_llm_meta": {"stop_reason": "max_tokens", "usage": {}}}
        with mock.patch.object(media, "_download", return_value=(b"x", "image/jpeg")), \
             mock.patch.object(media.llm, "post_body", return_value=reply):
            result = media._annotate_image({"content_uri": "https://s/a.jpg"})
        self.assertEqual(result["status"], "failed")
        self.assertIn("обрезан", result["error"])

    def test_unavailable_attachment_is_not_downloaded_again(self):
        """404 на стороне Wazzup — повторная попытка даст тот же 404."""
        item = {"message_id": "m1", "media_kind": "image", "content_uri": "https://s/a.jpg",
                "source_hash": "e" * 64, "provider": "anthropic",
                "model": "claude-sonnet-5", "config_hash": "f" * 64}
        with mock.patch.object(media, "plan", return_value=[item]), \
             mock.patch.object(media, "_lookup",
                               return_value={("m1", "e" * 64): {"status": "unavailable",
                                                                "error": "HTTP 404"}}), \
             mock.patch.object(media, "_annotate_one",
                               side_effect=AssertionError("не должно скачиваться")):
            out = media.annotate([{}])
        self.assertEqual(out["m1"]["status"], "unavailable")

    def test_interactive_enrichment_has_a_budget(self):
        """Открытие карточки не должно ждать расшифровки двух десятков вложений."""
        items = [{"message_id": f"m{i}", "media_kind": "image", "content_uri": f"https://s/{i}",
                  "source_hash": f"{i:064d}", "provider": "anthropic",
                  "model": "claude-sonnet-5", "config_hash": "a" * 64} for i in range(20)]
        calls = []

        def _fake(item):
            calls.append(item)
            return {"status": "ready", "annotation": "x"}

        with mock.patch.object(media, "plan", return_value=items), \
             mock.patch.object(media, "_lookup", return_value={}), \
             mock.patch.object(config, "MEDIA_MAX_INTERACTIVE", 3), \
             mock.patch.object(media, "_store", lambda *a, **kw: None), \
             mock.patch.object(media, "_annotate_one", side_effect=_fake):
            media.annotate([{}])
        self.assertEqual(len(calls), 3)

    def test_batch_requests_are_chunked(self):
        """Картинка в base64 весит мегабайты: одним POST месяц не отправить."""
        requests_out = [{"custom_id": f"img-{i}", "params": {"x": "y" * 1000}}
                        for i in range(10)]
        with mock.patch.object(config, "MEDIA_BATCH_MAX_ITEMS", 4), \
             mock.patch.object(config, "MEDIA_BATCH_MAX_BYTES", 10 ** 9):
            chunks = list(media._chunk_requests(requests_out))
        self.assertEqual([len(c) for c in chunks], [4, 4, 2])
        with mock.patch.object(config, "MEDIA_BATCH_MAX_ITEMS", 1000), \
             mock.patch.object(config, "MEDIA_BATCH_MAX_BYTES", 3000):
            chunks = list(media._chunk_requests(requests_out))
        self.assertGreater(len(chunks), 1)
        self.assertEqual(sum(len(c) for c in chunks), 10)

    def test_batch_poll_has_a_deadline(self):
        """Подвисший батч не должен вешать ночной прогон навсегда."""
        stuck = mock.Mock()
        stuck.raise_for_status.return_value = None
        stuck.json.return_value = {"processing_status": "in_progress"}
        with mock.patch.object(media.httpx, "get", return_value=stuck), \
             mock.patch.object(media.time, "sleep", lambda *_: None):
            with self.assertRaises(TimeoutError):
                media._poll_batch("batch-1", {}, poll_interval=0, deadline_s=-1,
                                  log=lambda *_: None)

    def test_batch_custom_ids_are_namespaced(self):
        item = {"message_id": "m1", "media_kind": "image", "content_uri": "https://s/a.jpg",
                "source_hash": "c" * 64, "provider": "anthropic",
                "model": "claude-sonnet-5", "config_hash": "d" * 64}
        with mock.patch.object(media, "_download", return_value=(b"fake", "image/jpeg")):
            requests_out, by_id = media.batch_image_requests([item])
        self.assertEqual(len(requests_out), 1)
        self.assertTrue(requests_out[0]["custom_id"].startswith("img-"))
        self.assertIn(requests_out[0]["custom_id"], by_id)

    def test_missing_media_is_marked_unavailable_not_failed(self):
        import httpx
        response = httpx.Response(404, request=httpx.Request("GET", "https://s/a.jpg"))
        with mock.patch.object(media, "_download",
                               side_effect=httpx.HTTPStatusError("404", request=response.request,
                                                                 response=response)):
            result = media._annotate_one({"media_kind": "image", "content_uri": "https://s/a.jpg"})
        self.assertEqual(result["status"], "unavailable")

    def test_oversized_media_is_not_downloaded_whole(self):
        headers = {"content-length": str(config.MEDIA_MAX_BYTES + 1)}

        class _Stream:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *_):
                return False
            def raise_for_status(self_inner):
                return None
            @property
            def headers(self_inner):
                return headers
            def iter_bytes(self_inner):
                raise AssertionError("не должно скачиваться")

        with mock.patch.object(media.httpx, "stream", return_value=_Stream()):
            with self.assertRaises(ValueError):
                media._download("https://s/big.jpg")


class SubjectIsolationTests(unittest.TestCase):
    """Числовые id звонков и эпизодов пересекаются — ключи не должны их путать."""

    def test_advisory_lock_namespace_differs_per_subject(self):
        self.assertNotEqual(runtime_store._LOCK_CLASSID[config.SUBJECT_CALL],
                            runtime_store._LOCK_CLASSID[config.SUBJECT_WZ_EPISODE])

    def test_canary_bucket_for_calls_is_unchanged(self):
        from call_qa.api import _canary_bucket
        from call_qa.evaluation.fingerprint import content_hash
        legacy = int(content_hash({"call_id": 42, "direction_id": 71})[:8], 16) % 100
        self.assertEqual(_canary_bucket(71, 42), legacy)
        self.assertEqual(_canary_bucket(71, 42, config.SUBJECT_CALL), legacy)

    def test_canary_bucket_for_chats_is_separate(self):
        from call_qa.api import _canary_bucket
        from call_qa.evaluation.fingerprint import content_hash
        expected = int(content_hash({"subject_kind": config.SUBJECT_WZ_EPISODE,
                                     "subject_id": 42, "direction_id": 71})[:8], 16) % 100
        self.assertEqual(_canary_bucket(71, 42, config.SUBJECT_WZ_EPISODE), expected)

    def test_adjudication_case_digest_for_calls_stays_byte_identical(self):
        """Дайджест звонка — ключ дедупликации; менять его нельзя."""
        from call_qa.rag import knowledge
        captured = {}

        class _Cur:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *_):
                return False
            def execute(self_inner, sql, params=None):
                if "INSERT INTO qa_adjudication_cases" in sql:
                    captured["digest"] = params[23]
                    captured["subject_kind"] = params[6]
                self_inner._row = ("case-id",)
            def fetchone(self_inner):
                return self_inner._row

        class _Conn:
            def cursor(self_conn):
                return _Cur()

        base = dict(direction_id=71, criterion_id="d71-x", correct_verdict="Correct",
                    evidence_excerpt="", reason="потому что", evidence_status="no_evidence",
                    call_id=42)
        knowledge.create_adjudication_case(_Conn(), **base)
        call_digest, call_kind = captured["digest"], captured["subject_kind"]
        knowledge.create_adjudication_case(_Conn(), **base,
                                          subject_kind=config.SUBJECT_WZ_EPISODE)
        self.assertEqual(call_kind, config.SUBJECT_CALL)
        self.assertEqual(captured["subject_kind"], config.SUBJECT_WZ_EPISODE)
        self.assertNotEqual(call_digest, captured["digest"])
        # эталон дайджеста звонка: считается без ключа subject_kind
        from call_qa.evaluation.fingerprint import content_hash
        self.assertEqual(call_digest, content_hash({
            "direction_id": 71, "criterion_id": "d71-x", "call_id": 42,
            "evaluation_run_id": None, "correct_verdict": "Correct",
            "evidence_excerpt": "", "evidence_status": "no_evidence",
            "reason": "потому что", "situation": None, "not_covered": None,
            "transcript_hash": None, "legacy_adjudication_id": None,
        }))

    def test_invalid_subject_kind_is_rejected_before_the_database(self):
        from call_qa.rag import knowledge
        with self.assertRaises(knowledge.KnowledgeValidationError):
            knowledge.create_adjudication_case(
                None, direction_id=71, criterion_id="d71-x", correct_verdict="Correct",
                evidence_excerpt="", reason="r", evidence_status="no_evidence",
                subject_kind="chat")

    def test_legacy_unique_index_is_not_recreated_by_the_migration(self):
        """Второй прогон миграции не должен падать на дубле (звонок №N и эпизод №N)."""
        schema = (ROOT / "call_qa" / "rag" / "schema.sql").read_text(encoding="utf-8-sig")
        self.assertNotIn("CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_eval_call_model", schema)

    def test_primary_key_swap_is_a_single_statement(self):
        from call_qa.rag.migrate import split_sql_statements
        schema = (ROOT / "call_qa" / "rag" / "schema.sql").read_text(encoding="utf-8-sig")
        pk = [x for x in split_sql_statements(schema) if "ai_review_cache_pkey" in x]
        self.assertEqual(len(pk), 1)
        self.assertIn("DROP CONSTRAINT", pk[0])
        self.assertIn("ADD CONSTRAINT", pk[0])

    def test_schema_keys_include_subject_kind(self):
        schema = (ROOT / "call_qa" / "rag" / "schema.sql").read_text(encoding="utf-8-sig")
        self.assertIn("ADD COLUMN IF NOT EXISTS subject_kind", schema)
        self.assertIn("PRIMARY KEY (subject_kind, call_id, model)", schema)
        self.assertIn("uq_ai_eval_subject_model", schema)
        self.assertIn("DROP INDEX IF EXISTS uq_ai_eval_call_model", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS wz_media_annotations", schema)
        # расшифровки вложений переживают ретеншн сырых сообщений: без FK на них
        annotations = schema.split("CREATE TABLE IF NOT EXISTS wz_media_annotations", 1)[1] \
            .split(");", 1)[0]
        self.assertNotIn("REFERENCES wazzup_messages", annotations)


class ChatReviewReasonTests(unittest.TestCase):
    def test_unreadable_media_sends_chat_to_human_review(self):
        reasons = review_queue.review_reasons([], None, {"total": 3, "ready": 2, "failed": 1})
        self.assertIn("media", reasons)

    def test_fully_read_media_adds_no_reason(self):
        reasons = review_queue.review_reasons([], None, {"total": 3, "ready": 3, "failed": 0})
        self.assertNotIn("media", reasons)


if __name__ == "__main__":
    unittest.main()
