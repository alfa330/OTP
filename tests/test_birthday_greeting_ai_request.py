"""Контракт запроса к ИИ за поздравлением с днём рождения.

С 22.08.2026 Gemini в OTP ходит через Vertex, а не по ключу AI Studio. AI Studio
молча урезал maxOutputTokens до потолка модели, Vertex отвечает 400 на всё, что
выше 65536. Поздравление слало 1 500 000 — и больше месяца каждый именинник
видел вместо текста «ai_failed». Тест держит лимит в границах, которые
принимает Vertex, и проверяет, что успешный ответ доходит до кеша.

`ai_feedback.service` тянет `database`, который на импорте зовёт `time.tzset()`
(только Linux) и поднимает пул к базе. Подменяем зависимость заглушкой.
"""
import copy
import importlib
import sys
import types
import unittest
from unittest import mock

# Верхняя граница Vertex для gemini-2.5-flash: «from 1 (inclusive) to 65537 (exclusive)».
VERTEX_MAX_OUTPUT_TOKENS = 65536


def _load_service():
    """Поднимает ai_feedback.service заново, с заглушкой вместо настоящей базы.

    Сбрасывать надо ОБЕ половины — запись в sys.modules и атрибут пакета, иначе
    модуль вернётся из кэша, не выполнившись заново, и заглушка не применится.
    """
    import ai_feedback

    sys.modules.pop("ai_feedback.service", None)
    if hasattr(ai_feedback, "service"):
        del ai_feedback.service
    stub = types.ModuleType("database")
    stub.db = mock.MagicMock()
    stub.IT_TICKET_CATALOG = {}
    with mock.patch.dict(sys.modules, {"database": stub}):
        service = importlib.import_module("ai_feedback.service")
    return service


service = _load_service()


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise service.httpx.HTTPStatusError("boom", request=None, response=self)


class BirthdayGreetingRequestTests(unittest.IsolatedAsyncioTestCase):
    USER = {"id": 7, "name": "Тестовый Сотрудник", "role": "operator",
            "direction": "Основа", "gender": "unknown", "hire_date": "2024-03-15"}

    async def _generate(self, response):
        sent = []

        class FakeClient:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *a):
                return False

            async def post(self_inner, url, json=None, headers=None):
                sent.append(copy.deepcopy(json))
                return response

        with mock.patch.object(service.httpx, "AsyncClient", lambda *a, **kw: FakeClient()), \
                mock.patch.object(service, "GEMINI_API_KEY", "gem_test"), \
                mock.patch.object(service.db, "get_ai_birthday_greeting_cache", return_value=None), \
                mock.patch.object(service.db, "save_ai_birthday_greeting_cache") as save:
            result = await service.generate_birthday_greeting_with_ai(self.USER, "2026-09-24")
        return result, sent, save

    async def test_max_output_tokens_within_vertex_range(self):
        _, sent, _ = await self._generate(_FakeResponse(
            200, {"candidates": [{"content": {"parts": [{"text": '{"greeting": "С днём рождения!"}'}]}}]}))
        self.assertEqual(len(sent), 1)
        limit = sent[0]["generationConfig"]["maxOutputTokens"]
        self.assertGreaterEqual(limit, 1)
        self.assertLessEqual(limit, VERTEX_MAX_OUTPUT_TOKENS,
                             "Vertex отвечает 400 на maxOutputTokens выше 65536")

    async def test_greeting_parsed_and_cached(self):
        result, _, save = await self._generate(_FakeResponse(
            200, {"candidates": [{"content": {"parts": [
                {"text": '```json\n{"greeting": "С днём рождения!"}\n```'}]}}]}))
        self.assertEqual(result, {"greeting": "С днём рождения!"})
        save.assert_called_once_with(7, "2026-09-24", {"greeting": "С днём рождения!"})

    async def test_http_error_returns_none_and_skips_cache(self):
        result, _, save = await self._generate(_FakeResponse(400, text="bad"))
        self.assertIsNone(result)
        save.assert_not_called()


class SharedGenerationConfigTests(unittest.TestCase):
    def test_shared_config_within_vertex_range(self):
        # Тем же конфигом пользуется и месячная обратная связь.
        self.assertLessEqual(service.generation_config["maxOutputTokens"], VERTEX_MAX_OUTPUT_TOKENS)


if __name__ == "__main__":
    unittest.main()
