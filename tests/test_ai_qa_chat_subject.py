# -*- coding: utf-8 -*-
"""Эпизод переписки как субъект ИИ-оценки: порог атрибуции, вложения, изоляция от звонков."""
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from call_qa import config, media, providers, subjects
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

    def test_card_line_plus_stamp_reconstructs_the_model_line(self):
        """Модель цитирует строку вместе с «[дд.мм чч:мм]», а карточка держит время
        отдельным полем. Проверка цитаты в карточке обязана собирать их обратно —
        иначе цитату ИИ нельзя подтвердить в один клик (её принимает только сервер)."""
        built = self._build({"m3": {"status": "ready", "annotation": "скриншот"}})
        model_lines = built["text"].split("\n\n", 1)[1].splitlines()
        rebuilt = [f"[{line['ts']}] " + "".join(seg["t"] for seg in line["seg"])
                   for line in built["lines"]]
        self.assertEqual(rebuilt, model_lines)

    def test_card_prefixes_the_stamp_before_matching_a_quote(self):
        card = (ROOT / "src" / "components" / "call_qa" / "CallReviewCard.jsx").read_text(
            encoding="utf-8")
        transcript_text = card.split("const transcriptText = useMemo(", 1)[1][:400]
        self.assertIn("line.ts", transcript_text)
        self.assertIn("`[${line.ts}] ${body}`", transcript_text)

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
            _msg("m3", mtype="document", uri="https://s/?filename=a.docx"),
            _msg("m4", mtype="image", uri=None),
            _msg("m5", text="просто текст"),
        ]
        plan = media.plan(messages)
        self.assertEqual([item["message_id"] for item in plan], ["m1", "m2"])
        # Провайдер описания выводится из имени модели, а не зашит строкой: он
        # входит в config_hash, то есть в идентичность расшифровки.
        self.assertEqual(plan[0]["provider"], media.vision_provider())
        self.assertEqual(plan[0]["model"], config.CLAUDE_MODEL_VISION)
        self.assertEqual(plan[1]["provider"], "soniox")

    def test_plan_is_capped_per_episode(self):
        messages = [_msg(f"m{i}", mtype="image", uri=f"https://s/{i}.jpg") for i in range(50)]
        with mock.patch.object(config, "MEDIA_MAX_PER_EPISODE", 5):
            self.assertEqual(len(media.plan(messages)), 5)

    def test_vision_request_on_claude_keeps_its_block_shape(self):
        """Путь Anthropic никуда не делся — возврат туда это одна переменная."""
        with mock.patch.object(config, "CLAUDE_MODEL_VISION", "claude-sonnet-5"):
            body = media.image_request_body("ZmFrZQ==", "image/jpeg")
        self.assertEqual(body["model"], "claude-sonnet-5")
        # описание картинки не нуждается в рассуждении: effort low + thinking off
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["output_config"]["effort"], config.CLAUDE_VISION_EFFORT)
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        blocks = body["messages"][0]["content"]
        self.assertEqual(blocks[0]["type"], "image")
        self.assertEqual(blocks[0]["source"]["media_type"], "image/jpeg")
        # короткий системный блок не достигает порога кэширования — не притворяемся
        self.assertNotIn("cache_control", body["system"][0])

    def test_vision_request_on_glm_becomes_an_image_url_block(self):
        """У Z.ai своя форма вложения, и ошибиться в ней тихо нельзя: строкой
        вместо объекта она отвечает 400 «image_url format error»."""
        with mock.patch.object(config, "CLAUDE_MODEL_VISION", "glm-5.3-flash"):
            body = media.image_request_body("ZmFrZQ==", "image/jpeg")
        self.assertEqual(body["_provider"], providers.ZAI)
        blocks = body["payload"]["messages"][1]["content"]
        self.assertEqual(blocks[0]["type"], "image_url")
        self.assertEqual(blocks[0]["image_url"], {"url": "data:image/jpeg;base64,ZmFrZQ=="})
        self.assertEqual(blocks[1]["type"], "text")
        # схему Z.ai не соблюдает — её место занимает json_object плюс промпт
        self.assertEqual(body["payload"]["response_format"], {"type": "json_object"})
        # «мышление» у GLM не выключается, уровень берётся из effort
        self.assertEqual(body["payload"]["reasoning_effort"], config.CLAUDE_VISION_EFFORT)

    def test_video_is_readable_only_where_the_model_can_see_it(self):
        """Claude видео не читал вовсе, и сообщение с роликом попадало в транскрипт
        пустым. GLM его читает — значит и периметр «что вообще расшифровываем»
        зависит от модели, а не зашит списком."""
        with mock.patch.object(config, "CLAUDE_MODEL_VISION", "glm-5.3-flash"):
            self.assertTrue(media.annotatable("video", "https://s/a.mp4"))
            plan = media.plan([_msg("m1", mtype="video", uri="https://s/a.mp4")])
            self.assertEqual(plan[0]["media_kind"], "video")
            self.assertEqual(plan[0]["provider"], providers.ZAI)
        with mock.patch.object(config, "CLAUDE_MODEL_VISION", "claude-sonnet-5"):
            self.assertFalse(media.annotatable("video", "https://s/a.mp4"))
            self.assertEqual(media.plan([_msg("m1", mtype="video", uri="https://s/a.mp4")]), [])

    def test_video_request_carries_its_own_prompt_and_limit(self):
        with mock.patch.object(config, "CLAUDE_MODEL_VISION", "glm-5.3-flash"):
            body = media.video_request_body("QUJD", "video/mp4")
        self.assertEqual(body["payload"]["max_tokens"], config.VIDEO_MAX_TOKENS)
        system = body["payload"]["messages"][0]["content"]
        # промпт обязан называть поля: схему Z.ai не соблюдает, и держит контракт
        # только текст. Первая версия была написана прозой — модель вернула рассказ,
        # а расшифровка сохранилась как «изображение без описания», молча.
        for field in ("description", "visible_text", "kind"):
            self.assertIn(field, system)

    def test_answer_without_content_is_a_failure_not_an_annotation(self):
        empty = {"description": "  ", "visible_text": "", "kind": "other"}
        self.assertTrue(media.annotation_is_empty(empty))
        self.assertFalse(media.annotation_is_empty({"description": "чек", "visible_text": ""}))
        with mock.patch.object(media, "_download", return_value=(b"PNG-bytes", "image/png")),  \
             mock.patch.object(media.llm, "post_body", return_value=dict(empty)):
            result = media._annotate_image({"content_uri": "https://s/a.png"})
        self.assertEqual(result["status"], "failed")
        self.assertIn("без описания", result["error"])

    def test_bulk_media_goes_local_when_provider_has_no_batch(self):
        """У Z.ai пакетного режима для этой модели нет вовсе — ночной прогон обязан
        уйти на локальный путь, а не постучаться в Batch API Anthropic без ключа."""
        with mock.patch.object(config, "CLAUDE_MODEL_VISION", "glm-5.3-flash"),              mock.patch.object(media, "_annotate_media_locally",
                               return_value={"m1": {"status": "ready"}}) as local,              mock.patch.object(media.llm, "_headers",
                               side_effect=AssertionError("Anthropic не должен вызываться")):
            out = media.annotate_media_batch([{"message_id": "m1"}], log=lambda *_: None)
        self.assertEqual(out, {"m1": {"status": "ready"}})
        self.assertEqual(local.call_count, 1)

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

    def test_batch_custom_ids_are_namespaced_per_kind(self):
        image = {"message_id": "m1", "media_kind": "image", "content_uri": "https://s/a.jpg",
                 "source_hash": "c" * 64, "provider": "anthropic",
                 "model": "claude-sonnet-5", "config_hash": "d" * 64}
        doc = dict(image, message_id="m2", media_kind="document",
                   content_uri="https://s/x/?filename=a.pdf", source_hash="e" * 64)

        def _dl(url, *, limit=None):
            return (b"%PDF-1.4 fake", "application/pdf") if "pdf" in url else (b"fake", "image/jpeg")

        with mock.patch.object(media, "_download", side_effect=_dl):
            requests_out, by_id = media.batch_media_requests([image, doc])
        ids = [r["custom_id"] for r in requests_out]
        self.assertEqual(len(ids), 2)
        self.assertTrue(any(i.startswith("img-") for i in ids))
        self.assertTrue(any(i.startswith("doc-") for i in ids))
        for custom_id in ids:
            self.assertIn(custom_id, by_id)

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


class PdfDocumentTests(unittest.TestCase):
    """PDF-вложения: 99% «документов» у Верификаторов — это PDF."""

    def test_pdf_is_routed_to_the_model_and_other_files_are_not(self):
        cases = {
            "https://store/x/?filename=spravka.pdf": "document",
            "https://store/x/?filename=SPRAVKA.PDF": "document",
            "https://store/x/?filename=photo.jpg": "image",
            "https://store/x/?filename=photo.png": "image",
            "https://store/x/?filename=cert.p12": "file",
            "https://store/x/?filename=report.docx": "file",
            "https://store/x/": "file",
        }
        for uri, expected in cases.items():
            with self.subTest(uri=uri):
                self.assertEqual(media._kind_of("document", uri), expected)
        # сертификат и docx читать нечем — в расшифровку они не идут
        self.assertFalse(media.annotatable("document", "https://store/x/?filename=cert.p12"))
        self.assertTrue(media.annotatable("document", "https://store/x/?filename=a.pdf"))

    def test_plan_picks_the_right_reader_per_attachment(self):
        messages = [
            _msg("m1", mtype="document", uri="https://s/?filename=a.pdf"),
            _msg("m2", mtype="document", uri="https://s/?filename=b.jpg"),
            _msg("m3", mtype="document", uri="https://s/?filename=c.p12"),
            _msg("m4", mtype="audio", uri="https://s/?filename=v.ogg"),
        ]
        plan = {item["message_id"]: item for item in media.plan(messages)}
        self.assertEqual(set(plan), {"m1", "m2", "m4"})
        self.assertEqual(plan["m1"]["media_kind"], "document")
        self.assertEqual(plan["m1"]["provider"], media.vision_provider())
        self.assertEqual(plan["m2"]["media_kind"], "image")
        self.assertEqual(plan["m4"]["provider"], "soniox")
        # у документа своя конфигурация → своя идентичность расшифровки
        self.assertNotEqual(plan["m1"]["config_hash"], plan["m2"]["config_hash"])

    def test_document_request_on_claude_uses_the_pdf_document_block(self):
        with mock.patch.object(config, "CLAUDE_MODEL_VISION", "claude-sonnet-5"):
            body = media.document_request_body("JVBERi0=")
        self.assertEqual(body["model"], "claude-sonnet-5")
        self.assertEqual(body["max_tokens"], config.DOCUMENT_MAX_TOKENS)
        block = body["messages"][0]["content"][0]
        self.assertEqual(block["type"], "document")
        self.assertEqual(block["source"]["type"], "base64")
        self.assertEqual(block["source"]["media_type"], "application/pdf")
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")

    def test_document_request_on_glm_becomes_a_file_url_block(self):
        """PDF нельзя слать как картинку: на image_url Z.ai отвечает 400
        «图片输入格式/解析错误». Для файлов свой тип блока."""
        with mock.patch.object(config, "CLAUDE_MODEL_VISION", "glm-5.3-flash"):
            body = media.document_request_body("JVBERi0=")
        block = body["payload"]["messages"][1]["content"][0]
        self.assertEqual(block["type"], "file_url")
        self.assertEqual(block["file_url"], {"url": "data:application/pdf;base64,JVBERi0="})

    def test_non_pdf_masquerading_as_pdf_is_rejected_before_paying(self):
        """Расширение в ссылке может врать — проверяем сигнатуру файла."""
        with mock.patch.object(media, "_download", return_value=(b"PK\x03\x04zip", "application/zip")), \
             mock.patch.object(media.llm, "post_body",
                               side_effect=AssertionError("модель не должна вызываться")):
            result = media._annotate_document({"content_uri": "https://s/?filename=a.pdf"})
        self.assertEqual(result["status"], "failed")
        self.assertIn("не PDF", result["error"])

    def test_pdf_has_its_own_size_limit(self):
        captured = {}

        def _dl(url, *, limit=None):
            captured["limit"] = limit
            return (b"%PDF-1.4", "application/pdf")

        with mock.patch.object(media, "_download", side_effect=_dl), \
             mock.patch.object(media.llm, "post_body",
                               return_value={"description": "справка", "visible_text": "",
                                             "kind": "document", "_llm_meta": {}}):
            media._annotate_document({"content_uri": "https://s/?filename=a.pdf"})
        self.assertEqual(captured["limit"], config.MEDIA_PDF_MAX_BYTES)
        self.assertGreater(config.MEDIA_PDF_MAX_BYTES, config.MEDIA_MAX_BYTES)

    def test_truncated_document_answer_is_a_failure(self):
        reply = {"description": "договор", "visible_text": "х" * 100, "kind": "document",
                 "_llm_meta": {"stop_reason": "max_tokens", "usage": {}}}
        with mock.patch.object(media, "_download", return_value=(b"%PDF-1.4", "application/pdf")), \
             mock.patch.object(media.llm, "post_body", return_value=reply):
            result = media._annotate_document({"content_uri": "https://s/?filename=a.pdf"})
        self.assertEqual(result["status"], "failed")
        self.assertIn("обрезан", result["error"])

    def test_document_content_lands_in_the_transcript(self):
        messages = [
            _msg("m1", text="Вот справка", minute=1),
            _msg("m2", mtype="document", uri="https://s/?filename=a.pdf", minute=2),
            _msg("m3", echo=True, text="Принял", user_id=7, minute=3),
        ]
        built = subjects.build_wz_transcript(
            _episode(), messages,
            {"m2": {"status": "ready", "annotation": "справка о доходах, ИИН 900101300000"}})
        self.assertIn("[документ (PDF): справка о доходах, ИИН 900101300000]", built["text"])
        line = next(l for l in built["lines"] if l.get("media"))
        self.assertEqual(line["media"]["kind"], "document")

    def test_unreadable_document_keeps_the_placeholder(self):
        """Сертификат/docx читать нечем — заглушка, а не «не удалось получить»."""
        messages = [_msg("m1", mtype="document", uri="https://s/?filename=cert.p12", minute=1)]
        built = subjects.build_wz_transcript(_episode(), messages, {})
        line = built["lines"][0]["seg"][0]["t"]
        self.assertIn("[документ]", line)
        self.assertNotIn("не удалось получить", line)
        self.assertNotIn("не расшифровано", line)


class SubjectPromptTests(unittest.TestCase):
    """Промпт зависит от субъекта, но у звонка обязан остаться неизменным."""

    # Отпечаток промпта звонка до появления второго субъекта. prompt_hash входит в
    # evaluation_fingerprint: изменится — все сохранённые оценки звонков станут
    # «устаревшими» и потребуют переоценки за деньги.
    CALL_PROMPT_HASH = "36eeb3fd743d6d67a2888e7344c1c232a25a24c5312a9e02e70c9f22e4921f46"

    def setUp(self):
        self.crits = [{"idx": 0, "name": "X", "description": "Y",
                       "is_critical": False, "deficiency": None}]

    def test_call_prompt_is_byte_identical(self):
        from call_qa.evaluation import evaluator
        from call_qa.evaluation.fingerprint import content_hash
        self.assertEqual(content_hash(evaluator.build_system(self.crits)),
                         self.CALL_PROMPT_HASH)
        self.assertEqual(
            content_hash(evaluator.build_system(self.crits, config.SUBJECT_CALL)),
            self.CALL_PROMPT_HASH)

    def test_chat_prompt_differs_and_drops_call_only_wording(self):
        from call_qa.evaluation import evaluator
        chat = evaluator.build_system(self.crits, config.SUBJECT_WZ_EPISODE)
        call = evaluator.build_system(self.crits, config.SUBJECT_CALL)
        self.assertNotEqual(chat, call)
        # у переписки нет диаризации и «звонка»
        self.assertNotIn("[S1]/[S2]", chat)
        self.assertIn("[S1]/[S2]", call)
        self.assertIn("чате WhatsApp", chat)
        self.assertIn("Роли определять не нужно", chat)

    def test_chat_prompt_keeps_asr_leniency_only_for_voice_transcripts(self):
        """Голосовые в чате расшифровывает Soniox — там ошибки распознавания реальны,
        а в набранном тексте орфография на операторе (критерий «Грамотность»)."""
        from call_qa.evaluation import evaluator
        chat = evaluator.build_system(self.crits, config.SUBJECT_WZ_EPISODE)
        self.assertIn("орфография и пунктуация в них полностью на ответственности оператора",
                      chat)
        self.assertIn("[голосовое, расшифровка:", chat)
        self.assertIn("получен автоматическим распознаванием речи", chat)

    def test_transcript_label_matches_the_subject(self):
        from call_qa.evaluation import evaluator
        direction = {"id": 71, "name": "Верификатор", "criteria": self.crits,
                     "scale_hash": "h"}
        call_body = evaluator.build_eval_body(
            "т", direction, self.crits, use_rag=False, model="m")
        chat_body = evaluator.build_eval_body(
            "т", direction, self.crits, use_rag=False, model="m",
            subject_kind=config.SUBJECT_WZ_EPISODE)
        self.assertIn("ТРАНСКРИПТ ЗВОНКА:", call_body["messages"][0]["content"])
        self.assertIn("ПЕРЕПИСКА В ЧАТЕ:", chat_body["messages"][0]["content"])
        self.assertNotIn("ТРАНСКРИПТ ЗВОНКА:", chat_body["messages"][0]["content"])


class QueueRowTests(unittest.TestCase):
    """Очередь ревью: одна строка на оба субъекта, с баллом ИИ."""

    def setUp(self):
        self.queue = (ROOT / "src" / "components" / "call_qa" / "QueueList.jsx").read_text(
            encoding="utf-8")

    def test_queue_returns_the_ai_score(self):
        """Балл — то, по чему выбирают, что открывать первым; без него в очереди
        видно только флаги, а они есть почти у каждой карточки."""
        api = (ROOT / "call_qa" / "api.py").read_text(encoding="utf-8")
        head = api[api.index("def review_queue_list"):api.index("def review_queue_count")]
        self.assertIn("rc.payload->'ai_score'", head)
        self.assertIn("rc.payload->'score_breakdown'", head)
        self.assertIn('"ai_score": r[13]', head)
        self.assertIn('"unchecked_weight"', head)

    def test_row_shows_score_and_the_part_it_did_not_check(self):
        self.assertIn("c.ai_score", self.queue)
        self.assertIn("c.unchecked_weight", self.queue)
        self.assertIn("зачтено без проверки", self.queue)

    def test_queue_lives_in_one_place_only(self):
        """Очередь ревью одна на оба субъекта. Во вкладке «Чаты» второй очереди
        быть не должно — там только чаты."""
        chat = (ROOT / "src" / "components" / "call_qa" / "ChatQueue.jsx").read_text(
            encoding="utf-8")
        view = (ROOT / "src" / "components" / "call_qa" / "CallQaView.jsx").read_text(
            encoding="utf-8")
        self.assertIn("from './QueueList'", view)
        self.assertIn("<QueueList items=", view)
        self.assertNotIn("QueueList", chat)
        self.assertNotIn("review-queue", chat)

    def test_reason_labels_carry_a_hint(self):
        """Короткая подпись на бейдже, полная формулировка — в подсказке."""
        from call_qa.review.queue import REASON_PRIORITY
        for key in REASON_PRIORITY:
            self.assertIn(f"{key}:", self.queue)
        self.assertIn("hint:", self.queue)
        self.assertIn("VISIBLE_REASONS", self.queue)

    def test_evaluated_chats_stay_reachable_after_the_rename(self):
        """Вкладка «Оценки» стала «Звонками» и фильтрует звонки — оценённые чаты
        обязаны остаться видимыми в своей вкладке."""
        view = (ROOT / "src" / "components" / "call_qa" / "CallQaView.jsx").read_text(
            encoding="utf-8")
        chat = (ROOT / "src" / "components" / "call_qa" / "ChatQueue.jsx").read_text(
            encoding="utf-8")
        self.assertIn("label: 'Звонки'", view)
        self.assertIn('subject="call"', view)
        self.assertIn('subject="wz_episode"', chat)


class ScoreBreakdownTests(unittest.TestCase):
    """Балл зачитывает непроверяемые критерии — это должно быть видно."""

    def _direction(self):
        return {"id": 71, "name": "Верификатор", "criteria": [
            {"idx": 0, "name": "Приветствие", "weight": 10, "is_critical": False,
             "deficiency": None},
            {"idx": 1, "name": "Регистрация", "weight": 30, "is_critical": False,
             "deficiency": None},
            {"idx": 2, "name": "КО", "weight": 0, "is_critical": True, "deficiency": None},
        ]}

    def test_unchecked_weight_is_reported_separately(self):
        from call_qa.api import _ai_score, _score_breakdown
        result = {"per_criterion": [
            {"idx": 0, "verdict": "Correct", "source": "transcript"},
            {"idx": 1, "verdict": "Pending", "source": "system_api"},
            {"idx": 2, "verdict": "Correct", "source": "transcript"},
        ]}
        direction = self._direction()
        breakdown = _score_breakdown(direction, result)
        # балл не меняется — «Регистрация» по-прежнему зачтена
        self.assertEqual(_ai_score(direction, result), 40)
        # но видно, что 30 из них ИИ не проверял
        self.assertEqual(breakdown["unchecked_weight"], 30)
        self.assertEqual(breakdown["verified_weight"], 10)
        self.assertEqual([c["name"] for c in breakdown["unchecked"]], ["Регистрация"])

    def test_nothing_unchecked_when_everything_is_evaluated(self):
        from call_qa.api import _score_breakdown
        result = {"per_criterion": [
            {"idx": 0, "verdict": "Correct", "source": "transcript"},
            {"idx": 1, "verdict": "Incorrect", "source": "transcript"},
            {"idx": 2, "verdict": "Correct", "source": "transcript"},
        ]}
        breakdown = _score_breakdown(self._direction(), result)
        self.assertEqual(breakdown["unchecked_weight"], 0)
        self.assertEqual(breakdown["unchecked"], [])
        self.assertEqual(breakdown["verified_weight"], 40)

    def test_card_shows_the_unchecked_badge(self):
        card = (ROOT / "src" / "components" / "call_qa" / "CallReviewCard.jsx").read_text(
            encoding="utf-8")
        self.assertIn("score_breakdown?.unchecked_weight", card)
        self.assertIn("не проверено", card)


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
