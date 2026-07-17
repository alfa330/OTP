# -*- coding: utf-8 -*-
"""Вердикт «Недочёт» (Deficiency) в ИИ-оценке: частичный зачёт в скоринге,
коэрсия для критериев без недочёта, пометка в промпте, отображение оценки СВ."""
import unittest

from call_qa.api import _ai_score, _human_display_verdict, _norm_verdict
from call_qa.evaluation.evaluator import _criteria_block, _needs_escalation, assemble_results


def _direction(criteria):
    return {"criteria": criteria}


def _crit(idx, weight, *, critical=False, deficiency=None, source="transcript"):
    return {"idx": idx, "criterion_id": f"c{idx}", "name": f"Критерий {idx}",
            "description": "Требование", "weight": weight, "is_critical": critical,
            "deficiency": deficiency, "eval_source": source}


def _row(idx, verdict, source="transcript"):
    return {"idx": idx, "verdict": verdict, "source": source}


class AiScoreDeficiencyTests(unittest.TestCase):
    def test_deficiency_gives_partial_weight(self):
        direction = _direction([
            _crit(0, 60),
            _crit(1, 40, deficiency={"weight": 15, "description": "мелкая неточность"}),
        ])
        result = {"per_criterion": [_row(0, "Correct"), _row(1, "Deficiency")]}
        self.assertEqual(_ai_score(direction, result), 75)  # 60 + 15

    def test_deficiency_without_scale_support_gives_zero_credit(self):
        # После коэрсии в evaluator такого не бывает, но скоринг не должен дарить вес.
        direction = _direction([_crit(0, 60), _crit(1, 40, deficiency=None)])
        result = {"per_criterion": [_row(0, "Correct"), _row(1, "Deficiency")]}
        self.assertEqual(_ai_score(direction, result), 60)

    def test_critical_incorrect_still_zeroes(self):
        direction = _direction([
            _crit(0, 100, deficiency={"weight": 50, "description": "x"}),
            _crit(1, 0, critical=True),
        ])
        result = {"per_criterion": [_row(0, "Deficiency"), _row(1, "Incorrect")]}
        self.assertEqual(_ai_score(direction, result), 0)


class VerdictNormalizationTests(unittest.TestCase):
    def test_norm_verdict_deficiency_variants(self):
        for raw in ("Deficiency", "deficiency", "Недочёт", "недочет"):
            self.assertEqual(_norm_verdict(raw), "Deficiency")

    def test_human_display_keeps_error_distinct(self):
        self.assertEqual(_human_display_verdict("Error"), "Error")
        self.assertEqual(_human_display_verdict("error"), "Error")
        self.assertEqual(_human_display_verdict("Deficiency"), "Deficiency")
        self.assertIsNone(_human_display_verdict(None))
        self.assertIsNone(_human_display_verdict(""))


class EvaluatorDeficiencyTests(unittest.TestCase):
    def test_criteria_block_marks_deficiency_support(self):
        block = _criteria_block([
            _crit(0, 60),
            _crit(1, 40, deficiency={"weight": 15, "description": "мелкая неточность"}),
        ])
        self.assertNotIn("НЕДОЧЁТ ДОПУСТИМ", block.split("\n1.")[0])
        self.assertIn("НЕДОЧЁТ ДОПУСТИМ (вердикт Deficiency): мелкая неточность", block)

    def test_assemble_coerces_unsupported_deficiency_to_incorrect(self):
        direction = _direction([_crit(0, 40, deficiency=None)])
        by_idx = {0: {"verdict": "Deficiency", "confidence": 0.9,
                      "evidence_quote": "", "comment": "чуть-чуть не так"}}
        rows = assemble_results(direction, by_idx, {0: "m"})["per_criterion"]
        self.assertEqual(rows[0]["verdict"], "Incorrect")
        self.assertIn("недочёт не предусмотрен", rows[0]["comment"])

    def test_assemble_keeps_supported_deficiency(self):
        direction = _direction([_crit(0, 40, deficiency={"weight": 10, "description": "x"})])
        by_idx = {0: {"verdict": "Deficiency", "confidence": 0.9,
                      "evidence_quote": "", "comment": "мелочь"}}
        rows = assemble_results(direction, by_idx, {0: "m"})["per_criterion"]
        self.assertEqual(rows[0]["verdict"], "Deficiency")
        self.assertEqual(rows[0]["comment"], "мелочь")

    def test_deficiency_escalates_like_incorrect(self):
        crit = _crit(0, 40, deficiency={"weight": 10, "description": "x"})
        self.assertTrue(_needs_escalation({"verdict": "Deficiency", "confidence": 0.95}, crit))


if __name__ == "__main__":
    unittest.main()
