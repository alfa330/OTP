"""Контракт запроса к ИИ для тикетов в IT-отдел.

Проверяем то, от чего напрямую зависит скорость ответа:
  • у Gemini 2.5-flash* явно выключено «мышление» (иначе на каждый запрос уходят
    лишние секунды на невидимые рассуждения);
  • ответ запрашивается сразу в JSON по схеме (без ограждений ```json и разбора текста);
  • в промпт уходит блок только нужного режима, а не оба сразу;
  • цепочка провайдеров ставит Groq вперёд, когда его ключ задан.

`ai_feed_back_service` тянет `database`, который на импорте зовёт `time.tzset()` (только
Linux). Подменяем зависимость заглушкой, чтобы тест шёл и на Windows-машине разработчика.
"""
import copy
import sys
import types
import unittest
from unittest import mock


def _load_service():
    if "ai_feed_back_service" in sys.modules:
        del sys.modules["ai_feed_back_service"]
    stub = types.ModuleType("database")
    stub.db = mock.MagicMock()
    stub.IT_TICKET_CATALOG = {
        "op": {"label": "ОП", "categories": [{"name": "Телефония", "items": ["Нет звука"]}]},
        "szov": {"label": "СЗоВ", "categories": []},
    }
    with mock.patch.dict(sys.modules, {"database": stub}):
        import ai_feed_back_service as service
    return service


service = _load_service()


class GeminiGenerationConfigTests(unittest.TestCase):
    def test_thinking_disabled_for_flash_models(self):
        for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
            with self.subTest(model=model):
                config = service._gemini_generation_config(model)
                self.assertEqual(config["thinkingConfig"], {"thinkingBudget": 0})

    def test_thinking_not_touched_for_models_without_switch(self):
        # gemini-2.5-pro не умеет нулевой бюджет, gemini-2.0-flash не думает вовсе —
        # у обеих параметр вернул бы 400.
        for model in ("gemini-2.5-pro", "gemini-2.0-flash"):
            with self.subTest(model=model):
                self.assertNotIn("thinkingConfig", service._gemini_generation_config(model))

    def test_structured_json_output_requested(self):
        config = service._gemini_generation_config("gemini-2.5-flash-lite")
        self.assertEqual(config["responseMimeType"], "application/json")
        self.assertIs(config["responseSchema"], service.IT_TICKET_RESPONSE_SCHEMA)

    def test_lightest_model_goes_first(self):
        self.assertEqual(service.DEFAULT_GEMINI_MODEL_CHAIN[0], "gemini-2.5-flash-lite")


class ProviderChainTests(unittest.TestCase):
    def test_groq_first_when_key_present(self):
        with mock.patch.object(service, "GROQ_API_KEY", "gsk_test"), \
                mock.patch.object(service, "GEMINI_API_KEY", "gem_test"), \
                mock.patch.dict(service.os.environ, {}, clear=False):
            service.os.environ.pop("IT_TICKET_AI_CHAIN", None)
            chain = service._it_ticket_provider_chain()
        self.assertEqual(chain[0][0], "groq")
        self.assertIn("gemini", [provider for provider, _ in chain])

    def test_gemini_only_when_groq_key_absent(self):
        with mock.patch.object(service, "GROQ_API_KEY", None), \
                mock.patch.object(service, "GEMINI_API_KEY", "gem_test"):
            service.os.environ.pop("IT_TICKET_AI_CHAIN", None)
            chain = service._it_ticket_provider_chain()
        self.assertTrue(chain)
        self.assertTrue(all(provider == "gemini" for provider, _ in chain))

    def test_env_override_parsed(self):
        with mock.patch.dict(service.os.environ,
                             {"IT_TICKET_AI_CHAIN": "groq:llama-3.3-70b-versatile, gemini:gemini-2.0-flash, bogus"}):
            chain = service._it_ticket_provider_chain()
        self.assertEqual(chain, [("groq", "llama-3.3-70b-versatile"), ("gemini", "gemini-2.0-flash")])

    def test_empty_chain_without_any_key(self):
        with mock.patch.object(service, "GROQ_API_KEY", None), \
                mock.patch.object(service, "GEMINI_API_KEY", None):
            service.os.environ.pop("IT_TICKET_AI_CHAIN", None)
            self.assertEqual(service._it_ticket_provider_chain(), [])


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


def _gemini_ok(text):
    return _FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


class GeminiFallbackTests(unittest.IsolatedAsyncioTestCase):
    """400 на ускоряющих полях не должен ронять фичу — та же модель переспрашивается «как раньше»."""

    async def _run(self, responses):
        sent = []

        class FakeClient:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *a):
                return False

            async def post(self_inner, url, json=None, headers=None):
                # Снимок: тело переиспользуется между попытками, httpx сериализует его
                # в момент отправки — без копии тест увидел бы только последнюю версию.
                sent.append(copy.deepcopy(json))
                return responses.pop(0)

        with mock.patch.object(service.httpx, "AsyncClient", lambda *a, **kw: FakeClient()), \
                mock.patch.object(service, "GEMINI_API_KEY", "gem_test"):
            result = await service._gemini_generate_once("gemini-2.5-flash", "prompt", 5.0, 1)
        return result, sent

    async def test_retries_without_speed_fields_on_400(self):
        (result, try_next), sent = await self._run([
            _FakeResponse(400, text="Unknown name responseSchema"),
            _gemini_ok('{"status": "ready"}'),
        ])
        self.assertEqual(result, {"status": "ready"})
        self.assertFalse(try_next)
        self.assertEqual(len(sent), 2, "должно быть ровно два запроса: ускоренный и запасной")
        self.assertIn("responseSchema", sent[0]["generationConfig"])
        self.assertIn("thinkingConfig", sent[0]["generationConfig"])
        self.assertNotIn("responseSchema", sent[1]["generationConfig"])
        self.assertNotIn("thinkingConfig", sent[1]["generationConfig"])

    async def test_second_400_gives_up_but_lets_chain_continue(self):
        (result, try_next), sent = await self._run([
            _FakeResponse(400, text="bad"),
            _FakeResponse(400, text="bad again"),
        ])
        self.assertEqual(len(sent), 2)
        self.assertEqual(result, {"error": "ai_failed"})
        self.assertTrue(try_next, "после отказа модели должна пробоваться следующая в цепочке")

    async def test_json_fences_still_parsed_on_fallback_config(self):
        (result, _), _ = await self._run([
            _FakeResponse(400, text="bad"),
            _gemini_ok('```json\n{"status": "draft"}\n```'),
        ])
        self.assertEqual(result, {"status": "draft"})


class ChainWalkTests(unittest.IsolatedAsyncioTestCase):
    async def test_moves_to_next_model_when_first_is_overloaded(self):
        calls = []

        async def fake_gemini(model, prompt, timeout, attempts):
            calls.append(model)
            if len(calls) == 1:
                return {"error": "ai_unavailable", "status": 503}, True
            return {"status": "ready"}, False

        with mock.patch.object(service, "_gemini_generate_once", side_effect=fake_gemini), \
                mock.patch.object(service, "GROQ_API_KEY", None), \
                mock.patch.object(service, "GEMINI_API_KEY", "gem_test"):
            service.os.environ.pop("IT_TICKET_AI_CHAIN", None)
            result = await service._call_ai_json("prompt")
        self.assertEqual(result, {"status": "ready"})
        self.assertEqual(len(calls), 2)

    async def test_returns_none_without_any_key(self):
        with mock.patch.object(service, "GROQ_API_KEY", None), \
                mock.patch.object(service, "GEMINI_API_KEY", None):
            service.os.environ.pop("IT_TICKET_AI_CHAIN", None)
            self.assertIsNone(await service._call_ai_json("prompt"))


class PromptAssemblyTests(unittest.IsolatedAsyncioTestCase):
    async def _prompt_for(self, mode, instructions=""):
        captured = {}

        async def fake_call(prompt, *a, **kw):
            captured["prompt"] = prompt
            return {"status": "ready", "category": "Телефония"}

        with mock.patch.object(service, "_call_ai_json", side_effect=fake_call), \
                mock.patch.object(service.db, "get_combined_it_ticket_instructions",
                                  return_value=instructions), \
                mock.patch.object(service, "_effective_it_catalog",
                                  return_value=service.IT_TICKET_CATALOG):
            await service.generate_it_ticket_with_ai(mode, {
                "profile": "op", "description": "нет звука на РМ 12", "fields": {},
            })
        return captured["prompt"]

    async def test_only_active_mode_block_is_sent(self):
        draft = await self._prompt_for("draft")
        self.assertIn(service.IT_TICKET_PROMPT_DRAFT.strip(), draft)
        self.assertNotIn(service.IT_TICKET_PROMPT_FINALIZE.strip(), draft)

        finalize = await self._prompt_for("finalize")
        self.assertIn(service.IT_TICKET_PROMPT_FINALIZE.strip(), finalize)
        self.assertNotIn(service.IT_TICKET_PROMPT_DRAFT.strip(), finalize)

    async def test_empty_admin_instructions_block_omitted(self):
        prompt = await self._prompt_for("draft", instructions="")
        self.assertNotIn("АКТУАЛЬНЫЕ ИНСТРУКЦИИ", prompt)

    async def test_admin_instructions_included_when_present(self):
        prompt = await self._prompt_for("draft", instructions="Oktell заменён на X")
        self.assertIn("Oktell заменён на X", prompt)

    async def test_draft_may_return_ready_in_one_call(self):
        # Первый проход должен уметь сразу отдать готовую заявку, если критичное уже известно —
        # иначе супервайзер всегда ждёт два запроса вместо одного.
        self.assertIn('"ready"', service.IT_TICKET_PROMPT_DRAFT)


if __name__ == "__main__":
    unittest.main()
