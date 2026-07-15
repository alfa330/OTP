import unittest

from call_qa.evaluation.fingerprint import build_evaluation_fingerprint, canonical_json


class EvaluationFingerprintTests(unittest.TestCase):
    def _fp(self, **overrides):
        values = dict(
            transcript_hash="t", model="m", model_config={"effort": "high"},
            prompt_hash="p", output_schema_hash="o", scale_hash="s",
            criterion_config_hash="c", knowledge_snapshot_hash="k",
            retrieval_config={"top_k": 3, "threshold": 0.72},
        )
        values.update(overrides)
        return build_evaluation_fingerprint(**values)[0]

    def test_canonical_json_is_order_independent(self):
        self.assertEqual(canonical_json({"b": 2, "a": 1}), canonical_json({"a": 1, "b": 2}))

    def test_every_material_input_invalidates_cache(self):
        baseline = self._fp()
        variants = [
            self._fp(model="m2"),
            self._fp(prompt_hash="p2"),
            self._fp(scale_hash="s2"),
            self._fp(knowledge_snapshot_hash="k2"),
            self._fp(retrieval_config={"top_k": 4, "threshold": 0.72}),
            self._fp(transcript_hash="t2"),
        ]
        self.assertTrue(all(value != baseline for value in variants))


if __name__ == "__main__":
    unittest.main()
