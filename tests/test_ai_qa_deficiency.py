# -*- coding: utf-8 -*-
"""Вердикт «Недочёт» (Deficiency) в ИИ-оценке: частичный зачёт в скоринге,
коэрсия для критериев без недочёта, пометка в промпте, отображение оценки СВ.
У критического критерия недочёт — вычет установленного числа баллов из итога
вместо обнуления: так считают ИИ, «Моя оценка», журнал и калибровка."""
import ast
import unittest
from pathlib import Path

from call_qa import human_review as hr
from call_qa.api import _ai_score, _human_display_verdict, _norm_verdict
from call_qa.evaluation.evaluator import _criteria_block, _needs_escalation, assemble_results
from tests import source_cache

BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"


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


def _calibration_total():
    """bot_schedule2._compute_total_score_from_criteria — монолит не импортируется."""
    namespace = {}
    nodes = [source_cache.function_node(BOT_PATH, name) for name in (
        "_normalize_calibration_score_value", "_compute_total_score_from_criteria")]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace["_compute_total_score_from_criteria"]


class CriticalDeficiencyTests(unittest.TestCase):
    """Недочёт критического критерия: снимает установленные баллы, не обнуляет."""

    def setUp(self):
        self.direction = _direction([
            _crit(0, 60),
            _crit(1, 40, deficiency={"weight": 15, "description": "мелкая неточность"}),
            _crit(2, 0, critical=True, deficiency={"weight": 10, "description": "повышенный тон"}),
            _crit(3, 0, critical=True),
        ])

    def _ai(self, *verdicts):
        return _ai_score(self.direction, {"per_criterion": [
            _row(i, v) for i, v in enumerate(verdicts)]})

    def test_ai_score_subtracts_penalty(self):
        self.assertEqual(self._ai("Correct", "Correct", "Correct", "Correct"), 100)
        self.assertEqual(self._ai("Correct", "Correct", "Deficiency", "Correct"), 90)
        # Частичный зачёт и вычет складываются: 60 + 15 − 10.
        self.assertEqual(self._ai("Correct", "Deficiency", "Deficiency", "Correct"), 65)

    def test_ai_score_never_goes_below_zero(self):
        direction = _direction([
            _crit(0, 100),
            _crit(1, 0, critical=True, deficiency={"weight": 30, "description": "x"}),
        ])
        result = {"per_criterion": [_row(0, "Incorrect"), _row(1, "Deficiency")]}
        self.assertEqual(_ai_score(direction, result), 0)

    def test_critical_incorrect_still_zeroes_despite_deficiency_option(self):
        self.assertEqual(self._ai("Correct", "Correct", "Incorrect", "Correct"), 0)
        self.assertEqual(self._ai("Correct", "Correct", "Deficiency", "Incorrect"), 0)

    def test_several_critical_deficiencies_add_up(self):
        direction = _direction([
            _crit(0, 100),
            _crit(1, 0, critical=True, deficiency={"weight": 10, "description": "x"}),
            _crit(2, 0, critical=True, deficiency={"weight": 5, "description": "y"}),
        ])
        result = {"per_criterion": [_row(0, "Correct"), _row(1, "Deficiency"), _row(2, "Deficiency")]}
        self.assertEqual(_ai_score(direction, result), 85)

    def test_assemble_keeps_deficiency_only_where_the_scale_has_it(self):
        by_idx = {2: {"verdict": "Deficiency", "confidence": 0.9, "evidence_quote": "", "comment": "тон"},
                  3: {"verdict": "Deficiency", "confidence": 0.9, "evidence_quote": "", "comment": "тон"}}
        rows = assemble_results(self.direction, by_idx, {2: "m", 3: "m"})["per_criterion"]
        self.assertEqual(rows[2]["verdict"], "Deficiency")
        # Без недочёта в шкале — ошибка, то есть критическая: балл 0, а не молчаливый зачёт.
        self.assertEqual(rows[3]["verdict"], "Incorrect")

    def test_criteria_block_marks_critical_deficiency(self):
        block = _criteria_block(self.direction["criteria"])
        critical = block[block.index("2. Критерий 2"):block.index("3. Критерий 3")]
        self.assertIn("(КРИТИЧЕСКИЙ)", critical)
        self.assertIn("НЕДОЧЁТ ДОПУСТИМ (вердикт Deficiency): повышенный тон", critical)
        self.assertNotIn("НЕДОЧЁТ ДОПУСТИМ", block[block.index("3. Критерий 3"):])

    def test_human_review_rules(self):
        criteria = self.direction["criteria"]
        self.assertEqual(hr.critical_deficiency_penalty(criteria[2]), 10)
        # У взвешенного критерия вес недочёта — зачёт, а не вычет.
        self.assertEqual(hr.critical_deficiency_penalty(criteria[1]), 0)
        self.assertEqual(hr.critical_deficiency_penalty(criteria[3]), 0)
        self.assertEqual(hr.verdict_from_ai(criteria[2], "Deficiency"), "Deficiency")
        self.assertEqual(hr.verdict_from_ai(criteria[2], "Incorrect"), "Error")
        self.assertEqual(hr.normalise_scores(criteria, ["Correct", "Correct", "недочёт", "Correct"]),
                         ["Correct", "Correct", "Deficiency", "Correct"])
        with self.assertRaisesRegex(ValueError, "недопустим"):
            hr.normalise_scores(criteria, ["Correct", "Correct", "Correct", "Deficiency"])
        self.assertEqual(hr.score_of(criteria, ["Correct", "Correct", "Deficiency", "Correct"]), 90)
        self.assertEqual(hr.score_of(criteria, ["Incorrect", "Incorrect", "Deficiency", "Correct"]), 0)
        self.assertEqual(hr.score_of(criteria, ["Correct", "Correct", "Deficiency", "Error"]), 0)

    def test_calibration_total(self):
        total = _calibration_total()
        # Калибровка читает шкалу как журнал — camelCase из directions.criteria.
        scale = [
            {"name": "A", "weight": 60, "isCritical": False},
            {"name": "B", "weight": 40, "isCritical": False, "deficiency": {"weight": 15}},
            {"name": "C", "weight": 0, "isCritical": True, "deficiency": {"weight": 10}},
            {"name": "D", "weight": 0, "isCritical": True},
        ]
        self.assertEqual(total(scale, ["Correct", "Correct", "Correct", "Correct"]), (100.0, False))
        self.assertEqual(total(scale, ["Correct", "Deficiency", "Deficiency", "Correct"]), (65.0, False))
        self.assertEqual(total(scale, ["Incorrect", "Incorrect", "Deficiency", "Correct"]), (0.0, False))
        self.assertEqual(total(scale, ["Correct", "Correct", "Deficiency", "Error"]), (0.0, True))


if __name__ == "__main__":
    unittest.main()
