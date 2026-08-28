"""Контракт третьего провайдера оценки — Z.ai (GLM), штатного с 28.08.2026.

Проверяется ровно то, на чём этот путь может сломаться МОЛЧА, и каждый случай здесь —
не гипотеза, а поведение, снятое запросами на 140 звонках Основа ОП:

* выбор провайдера по имени модели и подпись составного тега `bulk+hard`;
* уровень рассуждений уходит в КАЖДЫЙ запрос (у GLM «мышление» не отключается вовсе,
  а вендорское умолчание `max` — худшее по качеству и в пять раз дороже);
* строгая JSON-схема в запрос НЕ уходит: Z.ai её принимает, но не соблюдает, и ответ
  приезжает обёрнутым в markdown-ограду;
* пустой content — это НЕУДАЧА, а не пустая оценка: молча вернув {}, мы записали бы
  звонку зачёт по всем критериям;
* токены рассуждений УЖЕ включены в completion_tokens — прибавлять их значило бы
  удвоить счёт;
* себестоимость считается по префиксу ZAI_, а не по ставкам Claude (разница в 100 раз
  по выходному токену).
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
    def test_glm_goes_to_zai(self):
        self.assertEqual(providers.provider_for("glm-5.3-flash"), providers.ZAI)
        self.assertEqual(providers.provider_for("gemini-3.7-flash"), providers.VERTEX)
        self.assertEqual(providers.provider_for("claude-opus-4-8"), providers.ANTHROPIC)
        self.assertEqual(providers.provider_for(""), providers.ANTHROPIC)

    def test_composite_tag_is_resolved(self):
        """В базе модель лежит тегом bulk+hard, и подпись прогона обязана совпасть
        с тем, кто на самом деле считал."""
        self.assertEqual(
            providers.provider_for_tag("glm-5.3-flash+glm-5.3-flash"), providers.ZAI)

    def test_mixed_tag_counts_as_anthropic(self):
        # Смешанная пара — не штатный режим; подписываем прежним провайдером, а не
        # объявляем прогон целиком чужим. Заодно страховка от «эскалация ушла в
        # Anthropic, а журнал говорит zai».
        self.assertEqual(
            providers.provider_for_tag("glm-5.3-flash+claude-opus-4-8"), providers.ANTHROPIC)
        self.assertEqual(
            providers.provider_for_tag("glm-5.3-flash+gemini-3.7-flash"), providers.ANTHROPIC)


class BodyTests(unittest.TestCase):
    def _body(self, **kw):
        return llm.build_body(model="glm-5.3-flash", system="СИСТЕМА",
                              user="ТЕКСТ", schema=SCHEMA, **kw)

    def test_llm_dispatches_to_zai_by_model_name(self):
        self.assertEqual(self._body()["_provider"], providers.ZAI)

    def test_reasoning_effort_is_always_sent(self):
        """У GLM-5.3-Flash «мышление» не отключается (400, код 1210), а умолчание
        вендора `max` даёт 388 с и 18 626 выходных токенов на звонок при худшем
        качестве. Уровень обязан уходить в каждый запрос явно."""
        payload = self._body()["payload"]
        self.assertEqual(payload["reasoning_effort"], config.ZAI_REASONING_EFFORT)
        self.assertIn(config.ZAI_REASONING_EFFORT, ("low", "high", "max"))

    def test_reasoning_effort_can_be_overridden_per_call(self):
        self.assertEqual(self._body(thinking="low")["payload"]["reasoning_effort"], "low")

    def test_foreign_thinking_values_fall_back_to_config(self):
        """`thinking` у Vertex — число (бюджет токенов), у media.py — словарь
        {'type': 'disabled'}. Без фильтра сюда приехала бы строка «{'type':
        'disabled'}», Z.ai ответил бы 400 кодом 1210, и выглядело бы это как
        поломка модели, а не как чужой параметр не в том поле."""
        for foreign in (0, 1024, {"type": "disabled"}, "medium", "none", ""):
            with self.subTest(foreign=foreign):
                self.assertEqual(self._body(thinking=foreign)["payload"]["reasoning_effort"],
                                 config.ZAI_REASONING_EFFORT)

    def test_strict_schema_is_not_sent(self):
        """Z.ai принимает json_schema+strict, но не соблюдает: ответ приезжает в
        ```json-ограде и разбор падает. Работает только json_object, а сама схема
        описана в системном промпте оценщика."""
        payload = self._body()["payload"]
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertNotIn("json_schema", json.dumps(payload))

    def test_system_goes_into_messages(self):
        payload = self._body()["payload"]
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "СИСТЕМА"})
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "ТЕКСТ"})

    def test_cache_flags_are_ignored(self):
        """Кеш промпта у Z.ai автоматический (попадание в 90 % запросов на прогоне
        140 звонков) — настраивать нечего, и лишних ключей в теле быть не должно."""
        body = self._body(cache_system=True, cache_ttl="1h")
        self.assertNotIn("_cache_ttl_s", body)
        self.assertNotIn("cache_control", json.dumps(body["payload"]))

    def test_attachments_are_refused_loudly(self):
        # media.py шлёт список content-блоков с картинками; у этого адаптера их формы
        # нет, и молчаливое падение здесь стоило бы описания вложения.
        with self.assertRaises(NotImplementedError):
            llm.build_body(model="glm-5.3-flash", system="s",
                           user=[{"type": "image"}], schema=SCHEMA)


class UsageTests(unittest.TestCase):
    def test_reasoning_already_inside_completion_tokens(self):
        """Замер: completion_tokens 49, из них reasoning_tokens 27. Прибавить их к
        выходу ещё раз значило бы удвоить счёт — в отличие от Vertex, где мысли
        приходят отдельным счётчиком."""
        usage = providers._zai_usage({"usage": {
            "prompt_tokens": 5511, "completion_tokens": 3956,
            "completion_tokens_details": {"reasoning_tokens": 1500},
            "prompt_tokens_details": {"cached_tokens": 4015},
        }})
        self.assertEqual(usage["input_tokens"], 5511 - 4015)
        self.assertEqual(usage["output_tokens"], 3956)
        self.assertEqual(usage["cache_read_input_tokens"], 4015)
        self.assertEqual(usage["thoughts_tokens"], 1500)


class PostBodyTests(unittest.TestCase):
    """Границу «сеть + ключ» подменяем целиком: проверяем поведение адаптера, а не
    наличие ключа в окружении (в CI его нет)."""

    def setUp(self):
        key = mock.patch.object(config, "zai_key", return_value="test-key")
        key.start()
        self.addCleanup(key.stop)

    def _response(self, status=200, content='{"per_criterion": []}', reasoning="", finish="stop"):
        answer = {
            "id": "req-1", "model": "glm-5.3-flash",
            "choices": [{"finish_reason": finish,
                         "message": {"content": content, "reasoning_content": reasoning}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }
        response = mock.Mock()
        response.status_code = status
        response.json.return_value = answer
        response.text = json.dumps(answer)
        return response

    def _post(self, response):
        client = mock.Mock()
        client.post.return_value = response
        return mock.patch.object(providers, "_zai_http", return_value=client), client

    def _body(self):
        return llm.build_body(model="glm-5.3-flash", system="s", user="u", schema=SCHEMA)

    def test_parses_answer_and_reports_meta(self):
        patch, client = self._post(self._response())
        with patch:
            parsed = llm.post_body(self._body(), include_meta=True)
        self.assertEqual(parsed["per_criterion"], [])
        meta = parsed["_llm_meta"]
        self.assertEqual(meta["provider"], providers.ZAI)
        self.assertEqual(meta["reasoning_effort"], config.ZAI_REASONING_EFFORT)
        self.assertEqual(client.post.call_args.args[0], config.ZAI_URL)

    def test_empty_content_is_a_failure_not_an_empty_evaluation(self):
        """На reasoning_effort=max модель отдаёт HTTP 200 с нулём символов, потратив
        весь лимит на рассуждения. Вернув {}, мы поставили бы звонку зачёт по всем
        критериям — ровно тот режим отказа, что ловили в голосовом тренажёре."""
        patch, _ = self._post(self._response(content="", reasoning="х" * 3200,
                                             finish="length"))
        with patch, self.assertRaises(providers.ZaiError) as ctx:
            llm.post_body(self._body())
        self.assertIn("пустой ответ", str(ctx.exception))

    def test_markdown_fence_is_a_failure_not_silent_garbage(self):
        patch, _ = self._post(self._response(content='```json\n{"per_criterion": []}\n```'))
        with patch, self.assertRaises(providers.ZaiError) as ctx:
            llm.post_body(self._body())
        self.assertIn("не JSON", str(ctx.exception))

    def test_caller_timeout_is_a_floor_not_a_ceiling(self):
        """Интерактивная оценка зашивает 120 с (evaluator.py:230) — они калибровались
        под Claude и Gemini, где звонок считается за 16-45 с. У GLM на high медиана
        91 с, p90 116 с, максимум 155 с: с чужим лимитом карточка отваливалась бы по
        таймауту на каждом десятом длинном разговоре."""
        patch, client = self._post(self._response())
        with patch:
            llm.post_body(self._body(), timeout=120.0)
        self.assertEqual(client.post.call_args.kwargs["timeout"], config.ZAI_TIMEOUT)
        self.assertGreater(config.ZAI_TIMEOUT, 120.0)

    def test_longer_caller_timeout_is_respected(self):
        patch, client = self._post(self._response())
        with patch:
            llm.post_body(self._body(), timeout=config.ZAI_TIMEOUT + 60)
        self.assertEqual(client.post.call_args.kwargs["timeout"], config.ZAI_TIMEOUT + 60)

    def test_retries_on_rate_limit_then_succeeds(self):
        """1302 «rate limit» и 1305 «сервис перегружен» приходят по HTTP 429."""
        client = mock.Mock()
        client.post.side_effect = [self._response(status=429), self._response()]
        with mock.patch.object(providers, "_zai_http", return_value=client), \
                mock.patch.object(config, "ZAI_RETRY_BASE_S", 0):
            parsed = llm.post_body(self._body())
        self.assertEqual(parsed["per_criterion"], [])
        self.assertEqual(client.post.call_count, 2)


class CostTests(unittest.TestCase):
    def test_zai_priced_by_its_own_prefix(self):
        """Выходной токен GLM в 100 раз дешевле Opus. Посчитать его по CLAUDE_*
        значило бы показать в журнале прогонов чужой счёт."""
        prices = {
            "ZAI_INPUT_USD_PER_MTOK": "0.075", "ZAI_OUTPUT_USD_PER_MTOK": "0.25",
            "ZAI_CACHE_READ_USD_PER_MTOK": "0.015", "ZAI_CACHE_WRITE_USD_PER_MTOK": "0",
        }
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                 "cache_read_tokens": 1_000_000, "cache_write_tokens": 0}
        with mock.patch.object(config, "env", side_effect=lambda k, d=None: prices.get(k, d)):
            cost = runtime_store._estimate_cost(usage, "glm-5.3-flash+glm-5.3-flash")
        self.assertAlmostEqual(cost, 0.075 + 0.25 + 0.015, places=6)

    def test_missing_prices_leave_cost_empty(self):
        with mock.patch.object(config, "env", side_effect=lambda k, d=None: None):
            self.assertIsNone(runtime_store._estimate_cost(
                {"input_tokens": 1, "output_tokens": 1,
                 "cache_read_tokens": 0, "cache_write_tokens": 0}, "glm-5.3-flash"))


if __name__ == "__main__":
    unittest.main()
