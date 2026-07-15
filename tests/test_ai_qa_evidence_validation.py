import unittest

from call_qa.review.evidence import (
    EvidenceValidationError,
    locate_excerpt,
    validate_evidence,
)


class EvidenceValidationTests(unittest.TestCase):
    def test_exact_match_ignores_case_punctuation_and_spacing(self):
        transcript = "Оператор: Здравствуйте, меня зовут Анна! Чем помочь?"
        start, end = locate_excerpt(transcript, "здравствуйте меня зовут анна")
        self.assertIn("Здравствуйте", transcript[start:end])
        self.assertIn("Анна", transcript[start:end])

    def test_small_asr_typo_uses_strict_fuzzy_match(self):
        transcript = "Здравствуйте меня зовут Алия я ваш персональный менеджер"
        offsets = locate_excerpt(
            transcript, "Здравствуйте меня зовут Алия я ваш персональнй менеджер")
        self.assertIsNotNone(offsets)

    def test_hallucinated_quote_is_rejected(self):
        self.assertIsNone(locate_excerpt("Клиент сразу завершил вызов", "Меня зовут Анна"))

    def test_verified_requires_reviewer_confirmation(self):
        with self.assertRaises(EvidenceValidationError):
            validate_evidence("привет клиент", excerpt="привет",
                              evidence_status="verified", excerpt_verified=False)

    def test_no_evidence_is_explicit_and_has_no_offsets(self):
        self.assertEqual(
            validate_evidence("нет ответа", excerpt="", evidence_status="no_evidence"),
            ("no_evidence", None, None),
        )


if __name__ == "__main__":
    unittest.main()
