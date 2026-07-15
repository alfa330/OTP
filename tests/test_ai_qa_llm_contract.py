import unittest
from unittest import mock

from call_qa import llm


class LlmBodyContractTests(unittest.TestCase):
    def test_effort_sent_in_same_output_config_as_structured_format(self):
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        with mock.patch.object(llm.config, "CLAUDE_EFFORT", "medium"):
            body = llm.build_body(
                model="claude-opus-4-8", system="system", user="user", schema=schema)
        self.assertEqual(body["output_config"]["effort"], "medium")
        self.assertEqual(body["output_config"]["format"]["schema"], schema)


if __name__ == "__main__":
    unittest.main()
