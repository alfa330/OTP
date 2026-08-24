"""Контракт второго провайдера оценки — Vertex (Gemini).

Проверяется ровно то, на чём этот путь может сломаться молча: выбор провайдера по
имени модели, чистка схемы под Vertex, гашение «мышления», учёт токенов (мысли —
это выход, кеш — не вход) и два обязательных отката: протухший кеш и модель, не
принимающая thinkingConfig. Плюс главное свойство пакетного пути на Gemini —
результат должен укладываться в ту же форму, что отдаёт Anthropic, иначе весь
конвейер после батча пришлось бы переписывать.
"""
import json
import unittest
from unittest import mock

from call_qa import config, llm, providers
from call_qa.evaluation import runtime_store


SCHEMA = {
    "type": "object",
    "properties": {
        "per_criterion": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "idx": {"type": "integer"},
                "verdict": {"type": "string",
                            "enum": ["Correct", "Incorrect", "N/A", "Deficiency"]},
            },
            "required": ["idx", "verdict"],
            "additionalProperties": False,
        }},
    },
    "required": ["per_criterion"],
    "additionalProperties": False,
}


class ProviderSelectionTests(unittest.TestCase):
    def test_gemini_goes_to_vertex_everything_else_to_anthropic(self):
        self.assertEqual(providers.provider_for("gemini-3.7-flash"), providers.VERTEX)
        self.assertEqual(providers.provider_for("claude-opus-4-8"), providers.ANTHROPIC)
        self.assertEqual(providers.provider_for(""), providers.ANTHROPIC)

    def test_composite_model_tag_is_resolved(self):
        """В базе модель лежит тегом bulk+hard, и подпись прогона обязана совпасть
        с тем, кто на самом деле считал."""
        self.assertEqual(
            providers.provider_for_tag("gemini-3.7-flash+gemini-3.7-flash"), providers.VERTEX)
        self.assertEqual(
            providers.provider_for_tag("claude-opus-4-8+claude-opus-4-8"), providers.ANTHROPIC)

    def test_mixed_tag_counts_as_anthropic(self):
        # Смешанная пара — не наш штатный режим; безопаснее подписать её прежним
        # провайдером, чем объявить прогон целиком «vertex».
        self.assertEqual(
            providers.provider_for_tag("claude-opus-4-8+gemini-3.7-flash"), providers.ANTHROPIC)


class SchemaTests(unittest.TestCase):
    def test_additional_properties_removed_but_contract_kept(self):
        """Vertex отвергает additionalProperties с 400, а enum и required — это и есть
        та часть контракта, ради которой структурный вывод затевался."""
        cleaned = providers.gemini_schema(SCHEMA)
        self.assertNotIn("additionalProperties", cleaned)
        item = cleaned["properties"]["per_criterion"]["items"]
        self.assertNotIn("additionalProperties", item)
        self.assertEqual(item["required"], ["idx", "verdict"])
        self.assertEqual(item["properties"]["verdict"]["enum"],
                         ["Correct", "Incorrect", "N/A", "Deficiency"])


class BodyTests(unittest.TestCase):
    def _body(self, **kw):
        return llm.build_body(model="gemini-3.7-flash", system="СИСТЕМА",
                              user="ТЕКСТ", schema=SCHEMA, **kw)

    def test_llm_dispatches_to_vertex_by_model_name(self):
        self.assertEqual(self._body()["_provider"], providers.VERTEX)
        self.assertNotIn("_provider", llm.build_body(
            model="claude-opus-4-8", system="s", user="u", schema=SCHEMA))

    def test_system_block_is_kept_outside_payload(self):
        """Системный блок уезжает либо в system_instruction, либо в явный кеш —
        решается в момент отправки, когда известно, жив ли кеш."""
        body = self._body(cache_system=True)
        self.assertEqual(body["_system"], "СИСТЕМА")
        self.assertNotIn("system_instruction", body["payload"])

    def test_thinking_suppressed_by_default(self):
        cfg = self._body()["payload"]["generationConfig"]
        self.assertEqual(cfg["thinkingConfig"], {"thinkingBudget": 0})
        self.assertEqual(cfg["responseMimeType"], "application/json")

    def test_ttl_translated_from_anthropic_form(self):
        self.assertEqual(self._body(cache_system=True, cache_ttl="1h")["_cache_ttl_s"], 3600)
        self.assertEqual(self._body(cache_system=True, cache_ttl="5m")["_cache_ttl_s"], 300)
        self.assertEqual(self._body(cache_system=True)["_cache_ttl_s"],
                         config.VERTEX_CACHE_TTL_S)

    def test_attachments_are_refused_loudly(self):
        # media.py шлёт список content-блоков с картинками; у Vertex-адаптера их
        # формы нет, и молчаливое падение здесь стоило бы описания вложения.
        with self.assertRaises(NotImplementedError):
            llm.build_body(model="gemini-3.7-flash", system="s",
                           user=[{"type": "image"}], schema=SCHEMA)


class UsageTests(unittest.TestCase):
    def test_thoughts_are_output_and_cache_is_not_input(self):
        """Токены «мышления» тарифицируются как выход; без этого смета занижала бы
        счёт втрое на моделях, где гашение не срабатывает. Кешированные токены
        нельзя оставлять во входе — их считают по другой ставке."""
        usage = providers._usage({"usageMetadata": {
            "promptTokenCount": 5248, "candidatesTokenCount": 1500,
            "thoughtsTokenCount": 700, "cachedContentTokenCount": 4155,
        }})
        self.assertEqual(usage["input_tokens"], 5248 - 4155)
        self.assertEqual(usage["output_tokens"], 1500 + 700)
        self.assertEqual(usage["cache_read_input_tokens"], 4155)


class PostBodyTests(unittest.TestCase):
    """Границу «сеть + авторизация» подменяем целиком.

    Первый заход подменял только `_post`, а адрес запроса собирался по-настоящему —
    то есть тест требовал сервисного аккаунта. Локально он есть в .env.codex.local, и
    тесты проходили; в CI его нет, и три теста упали на `нет
    GOOGLE_APPLICATION_CREDENTIALS_CONTENT`. Проверяем поведение адаптера, а не наличие
    ключей, поэтому базовый адрес тоже фиксируем.
    """

    def setUp(self):
        base = mock.patch.object(providers._VERTEX, "base",
                                 return_value="https://example.invalid/v1beta1/projects/p/locations/global")
        base.start()
        self.addCleanup(base.stop)

    def _answer(self, text="{}"):
        return {"candidates": [{"finishReason": "STOP",
                                "content": {"parts": [{"text": text}]}}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2},
                "responseId": "req-1", "modelVersion": "gemini-3.7-flash"}

    def test_empty_answer_is_a_failure_not_an_empty_evaluation(self):
        """Обрезанный по maxOutputTokens ответ приходит с пустым content. Отдав его
        наверх как {}, мы записали бы звонку зачёт по всем критериям."""
        answer = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}]}
        with mock.patch.object(providers, "_post", return_value=answer), \
                mock.patch.object(providers, "_cached_content_name", return_value=None):
            with self.assertRaises(providers.VertexError):
                providers.post_body(llm.build_body(
                    model="gemini-3.7-flash", system="s", user="u", schema=SCHEMA))

    def test_stale_cache_retries_without_it(self):
        """Кеш живёт час и может исчезнуть на стороне Google. Этот отказ иначе
        выглядел бы как «модель сломалась»."""
        calls = []

        def fake_post(url, payload, **kw):
            calls.append(payload)
            if "cachedContent" in payload:
                raise providers.VertexError(404, "cachedContents not found")
            return self._answer('{"ok": true}')

        with mock.patch.object(providers, "_post", side_effect=fake_post), \
                mock.patch.object(providers, "_cached_content_name", return_value="caches/1"), \
                mock.patch.object(providers, "_forget_cache") as forget:
            result = providers.post_body(llm.build_body(
                model="gemini-3.7-flash", system="СИСТЕМА", user="u",
                schema=SCHEMA, cache_system=True))
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["system_instruction"]["parts"][0]["text"], "СИСТЕМА")
        forget.assert_called_once_with("caches/1")

    def test_model_rejecting_thinking_config_is_retried_without_it(self):
        calls = []

        def fake_post(url, payload, **kw):
            calls.append(json.loads(json.dumps(payload)))
            if "thinkingConfig" in payload["generationConfig"]:
                raise providers.VertexError(400, "thinkingConfig is not supported")
            return self._answer('{"ok": 1}')

        with mock.patch.object(providers, "_post", side_effect=fake_post), \
                mock.patch.object(providers, "_cached_content_name", return_value=None):
            providers.post_body(llm.build_body(
                model="gemini-3.7-flash", system="s", user="u", schema=SCHEMA))
        self.assertEqual(len(calls), 2)
        self.assertNotIn("thinkingConfig", calls[1]["generationConfig"])


class CostTests(unittest.TestCase):
    def test_price_env_prefix_follows_provider(self):
        """Считать расход Gemini по ставкам CLAUDE_* значило бы завысить счёт
        на порядок."""
        usage = {"input_tokens": 1_000_000, "output_tokens": 0,
                 "cache_read_tokens": 0, "cache_write_tokens": 0}
        prices = {"CLAUDE_INPUT_USD_PER_MTOK": "5", "CLAUDE_OUTPUT_USD_PER_MTOK": "25",
                  "CLAUDE_CACHE_READ_USD_PER_MTOK": "0.5",
                  "CLAUDE_CACHE_WRITE_USD_PER_MTOK": "10",
                  "GEMINI_INPUT_USD_PER_MTOK": "0.75", "GEMINI_OUTPUT_USD_PER_MTOK": "3.75",
                  "GEMINI_CACHE_READ_USD_PER_MTOK": "0.075",
                  "GEMINI_CACHE_WRITE_USD_PER_MTOK": "0"}
        with mock.patch.object(runtime_store.config, "env", side_effect=prices.get):
            self.assertAlmostEqual(
                runtime_store._estimate_cost(usage, "claude-opus-4-8+claude-opus-4-8"), 5.0)
            self.assertAlmostEqual(
                runtime_store._estimate_cost(usage, "gemini-3.7-flash+gemini-3.7-flash"), 0.75)

    def test_missing_prices_leave_cost_empty(self):
        with mock.patch.object(runtime_store.config, "env", return_value=None):
            self.assertIsNone(runtime_store._estimate_cost(
                {"input_tokens": 1, "output_tokens": 1,
                 "cache_read_tokens": 0, "cache_write_tokens": 0}, "gemini-3.7-flash"))


class MultilineEnvTests(unittest.TestCase):
    def test_service_account_json_spanning_lines_is_read(self):
        """JSON сервисного аккаунта лежит в .env.codex.local с переносами, и
        построчный разбор давал по ключу ровно «{» — Vertex с машины разработчика
        не работал вовсе, а выглядело это как «провайдер молча отвалился»."""
        text = ('OTHER=1\n'
                'GOOGLE_APPLICATION_CREDENTIALS_CONTENT={\n'
                '  "type": "service_account",\n'
                '  "project_id": "demo"\n'
                '}\n'
                'AFTER=2\n')
        with mock.patch("builtins.open", mock.mock_open(read_data=text)):
            config._dev_env.cache_clear()
            parsed = config._dev_env()
        config._dev_env.cache_clear()
        self.assertEqual(parsed["AFTER"], "2")
        self.assertEqual(
            json.loads(parsed["GOOGLE_APPLICATION_CREDENTIALS_CONTENT"])["project_id"], "demo")


if __name__ == "__main__":
    unittest.main()
