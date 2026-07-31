"""Вложение к заявке в IT-отдел.

`bot_schedule2` целиком не импортируется (тянет `database`, а тот на импорте зовёт
`time.tzset()` — только Linux), поэтому, как и в других тестах по этому модулю,
берём исходник функции. Для `_tag_send_attachment` не ограничиваемся поиском строк,
а выполняем извлечённый код с заглушками — так проверяется реальное поведение.
"""
import ast
import io
import textwrap
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
MODAL_PATH = ROOT / "src" / "components" / "technical" / "ITTicketModal.jsx"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _function_source(path, function_name):
    source = _read(path)
    module = ast.parse(source)
    node = next(
        n for n in module.body
        if isinstance(n, ast.FunctionDef) and n.name == function_name
    )
    return textwrap.dedent(ast.get_source_segment(source, node))


class _FakeUpload:
    """Минимальный аналог werkzeug FileStorage."""

    def __init__(self, filename, data=b"x", mimetype="application/octet-stream"):
        self.filename = filename
        self.stream = io.BytesIO(data)
        self.mimetype = mimetype


def _load_send_attachment(captured):
    """Выполняет _tg_send_attachment в изолированном пространстве имён."""
    import os as real_os

    def fake_post(url, data=None, files=None, timeout=None):
        captured.update(url=url, data=data, files=files, timeout=timeout)
        return types.SimpleNamespace(
            status_code=200,
            json=lambda: {"ok": True, "result": {"message_id": 77}},
        )

    ns = {
        "os": real_os,
        "requests": types.SimpleNamespace(post=fake_post),
        "ICORE_TICKET_IMAGE_EXT": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"},
    }
    real_os.environ.setdefault("BOT_TOKEN", "test-token")
    exec(_function_source(BOT_PATH, "_tg_send_attachment"), ns)  # noqa: S102
    return ns["_tg_send_attachment"]


class SendAttachmentBehaviourTests(unittest.TestCase):
    def test_screenshot_goes_as_photo(self):
        captured = {}
        send = _load_send_attachment(captured)
        result, err = send(-100123, _FakeUpload("screen.PNG", mimetype="image/png"))
        self.assertIsNone(err)
        self.assertEqual(result, {"message_id": 77})
        self.assertTrue(captured["url"].endswith("/sendPhoto"))
        self.assertIn("photo", captured["files"])

    def test_other_files_go_as_document(self):
        for name in ("report.pdf", "log.txt", "data.xlsx", "noext"):
            with self.subTest(name=name):
                captured = {}
                send = _load_send_attachment(captured)
                _result, err = send(-100123, _FakeUpload(name))
                self.assertIsNone(err)
                self.assertTrue(captured["url"].endswith("/sendDocument"))
                self.assertIn("document", captured["files"])

    def test_attached_as_reply_to_the_ticket_message(self):
        # Подпись у медиа ограничена 1024 символами, а текст заявки длиннее —
        # поэтому файл идёт ответом к сообщению, а не подписью к нему.
        captured = {}
        send = _load_send_attachment(captured)
        send(-100123, _FakeUpload("screen.png"), reply_to_message_id=42)
        self.assertEqual(captured["data"]["reply_to_message_id"], 42)

    def test_path_is_stripped_from_filename(self):
        captured = {}
        send = _load_send_attachment(captured)
        send(-100123, _FakeUpload("C:/Users/User/secret/screen.png"))
        self.assertEqual(captured["files"]["photo"][0], "screen.png")

    def test_telegram_error_is_reported(self):
        import os as real_os

        def fake_post(*_a, **_kw):
            return types.SimpleNamespace(
                status_code=400,
                json=lambda: {"ok": False, "description": "CHAT_NOT_FOUND"},
            )

        ns = {
            "os": real_os,
            "requests": types.SimpleNamespace(post=fake_post),
            "ICORE_TICKET_IMAGE_EXT": {".png"},
        }
        exec(_function_source(BOT_PATH, "_tg_send_attachment"), ns)  # noqa: S102
        result, err = ns["_tg_send_attachment"](-1, _FakeUpload("a.png"))
        self.assertIsNone(result)
        self.assertEqual(err, "CHAT_NOT_FOUND")


class SendEndpointContractTests(unittest.TestCase):
    def setUp(self):
        self.source = _function_source(BOT_PATH, "it_ticket_send")

    def test_accepts_multipart_and_keeps_json_path(self):
        self.assertIn("request.files.get('attachment')", self.source)
        self.assertIn("request.get_json(silent=True)", self.source)
        self.assertIn("json.loads(payload.get('fields')", self.source)

    def test_oversized_file_rejected_before_sending(self):
        self.assertIn("IT_TICKET_ATTACH_MAX_BYTES", self.source)
        self.assertIn("больше 20 МБ", self.source)

    def test_failed_attachment_does_not_lose_the_ticket(self):
        # Текст заявки уже в чате — откатывать нечего, поэтому про неудачу
        # сообщаем отдельным полем, а не ошибкой всей отправки.
        self.assertIn("attachment_error", self.source)
        after_send = self.source.split("message_id = result.get('message_id')", 1)[1]
        self.assertIn("_tg_send_attachment", after_send)
        self.assertIn("\"status\": \"success\"", after_send)

    def test_limit_matches_telegram_bot_upload_cap(self):
        self.assertIn("IT_TICKET_ATTACH_MAX_BYTES = 20 * 1024 * 1024", _read(BOT_PATH))


class ModalContractTests(unittest.TestCase):
    def setUp(self):
        self.source = _read(MODAL_PATH)

    def test_form_is_reset_on_open(self):
        # Модалка не размонтируется при закрытии: без сброса повторное открытие
        # показывало бы прошлый отправленный тикет.
        self.assertIn("Каждое открытие — чистая форма", self.source)
        for setter in ("setPreviewText('')", "setAiFields([])", "setFieldValues({})",
                       "setAttachment(null)", "setTriedSend(false)"):
            with self.subTest(setter=setter):
                self.assertIn(setter, self.source)

    def test_required_fields_block_send_and_are_highlighted(self):
        self.assertIn("missingRequired", self.source)
        self.assertIn("if (missingRequired.length > 0)", self.source)
        self.assertIn("ring-rose-400", self.source)
        self.assertIn("invalid={triedSend && missingKeys.has(f.key)}", self.source)

    def test_attachment_sent_as_multipart(self):
        self.assertIn("new FormData()", self.source)
        self.assertIn("form.append('attachment', attachment, attachment.name)", self.source)
        self.assertIn("attachment_error", self.source)

    def test_workplace_picker_has_select_all_and_clear(self):
        self.assertIn("Все РМ", self.source)
        self.assertIn("Очистить", self.source)
        self.assertIn("toggleAllActive", self.source)

    def test_checks_become_the_answer_about_what_was_tried(self):
        # Отмеченные проверки должны сами заполнять «что уже пробовали» — иначе
        # супервайзеру пришлось бы пересказывать это руками.
        self.assertIn("triedFieldKey", self.source)
        self.assertIn("Пробовали, не помогло:", self.source)
        self.assertIn("toggleCheck", self.source)
        self.assertIn("Попробуйте сначала", self.source)

    def test_checks_hidden_when_ai_returned_none(self):
        self.assertIn("aiChecks.length > 0 && (", self.source)

    def test_workplace_picker_picks_up_ai_preselection(self):
        # Значение от ИИ («РМ 16») может прийти после первого рендера, а раскладка
        # кабинетов — подгрузиться позже; и то и другое должно отметиться на схеме.
        self.assertIn("initSelectedFromValue(incoming, list)", self.source)
        self.assertIn("syncRef", self.source)


if __name__ == "__main__":
    unittest.main()
