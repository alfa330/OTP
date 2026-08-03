import unittest
from unittest import mock

from call_qa import config, llm
from call_qa.evaluation.fingerprint import content_hash


class LlmBodyContractTests(unittest.TestCase):
    def test_effort_sent_in_same_output_config_as_structured_format(self):
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        with mock.patch.object(llm.config, "CLAUDE_EFFORT", "medium"):
            body = llm.build_body(
                model="claude-opus-4-8", system="system", user="user", schema=schema)
        self.assertEqual(body["output_config"]["effort"], "medium")
        self.assertEqual(body["output_config"]["format"]["schema"], schema)


class PromptCacheTtlTests(unittest.TestCase):
    """TTL prompt-кеша системного блока.

    Batch API не гарантирует порядок обработки, поэтому сортировка заявок по
    направлению не удерживает запись живой: при дефолтных 5 минутах системный блок
    (промпт оценщика + критерии) пишется заново почти на каждом звонке. Замер
    2026-08 дал 25% попаданий и +35% к счёту, поэтому пакетный путь обязан
    просить час.
    """

    def _body(self, **kw):
        return llm.build_body(model="claude-opus-4-8", system="system", user="user",
                              schema={"type": "object"}, **kw)

    def test_ttl_omitted_without_cache(self):
        self.assertNotIn("cache_control", self._body()["system"][0])

    def test_interactive_call_keeps_api_default_ttl(self):
        # Одиночная оценка при открытии карточки не окупает удвоенную запись.
        cache = self._body(cache_system=True)["system"][0]["cache_control"]
        self.assertEqual(cache, {"type": "ephemeral"})

    def test_batch_call_requests_one_hour(self):
        cache = self._body(cache_system=True, cache_ttl="1h")["system"][0]["cache_control"]
        self.assertEqual(cache, {"type": "ephemeral", "ttl": "1h"})

    def test_ttl_ignored_when_caching_disabled(self):
        self.assertNotIn("cache_control", self._body(cache_ttl="1h")["system"][0])

    def test_batch_pipeline_asks_for_a_long_lived_entry(self):
        # Регрессия: если конфиг обнулят, батч молча вернётся к 5 минутам.
        self.assertEqual(config.CLAUDE_CACHE_TTL_BATCH, "1h")

    def test_ttl_does_not_leak_into_evaluation_identity(self):
        """Правка TTL не должна вызывать переоценку уже оценённых звонков.

        prompt_hash считается от текста системного блока, а model_config — от
        моделей и effort; cache_control не входит ни туда, ни туда.
        """
        default = self._body(cache_system=True)
        hour = self._body(cache_system=True, cache_ttl="1h")
        self.assertEqual(content_hash(default["system"][0]["text"]),
                         content_hash(hour["system"][0]["text"]))
        # различие тел ограничено ровно одним ключом
        self.assertEqual(default["messages"], hour["messages"])
        self.assertEqual(default["output_config"], hour["output_config"])
        self.assertEqual(
            set(hour["system"][0]["cache_control"]) - set(default["system"][0]["cache_control"]),
            {"ttl"})


if __name__ == "__main__":
    unittest.main()
