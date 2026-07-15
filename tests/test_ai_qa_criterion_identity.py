import unittest

from call_qa.evaluation.criteria import criterion_identity, scale_fingerprint


class CriterionIdentityTests(unittest.TestCase):
    def test_identity_does_not_depend_on_array_position(self):
        raw = {"name": "Приветствие", "value": "Оператор должен поздороваться"}
        self.assertEqual(criterion_identity(72, raw), criterion_identity(72, raw))

    def test_explicit_identity_wins(self):
        self.assertEqual(criterion_identity(72, {"id": "greeting-v2", "name": "X"}), "greeting-v2")

    def test_same_name_in_another_direction_is_distinct(self):
        raw = {"name": "Приветствие"}
        self.assertNotEqual(criterion_identity(72, raw), criterion_identity(73, raw))

    def test_scale_hash_changes_when_requirement_changes(self):
        base = [{"criterion_id": "greeting", "name": "Приветствие", "description": "A",
                 "weight": 5, "is_critical": False, "deficiency": None}]
        changed = [{**base[0], "description": "B"}]
        self.assertNotEqual(scale_fingerprint(72, "Основа", base),
                            scale_fingerprint(72, "Основа", changed))


if __name__ == "__main__":
    unittest.main()
