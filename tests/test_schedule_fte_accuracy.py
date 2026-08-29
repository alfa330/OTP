# -*- coding: utf-8 -*-
"""Точность графика по FTE: генератор не должен заливать неделю лишними сменами.

Владелец 29.08.2026: «на один час перелимит может достигать 6-7 FTE, надо
довести генерацию для чат менеджеров до такого же попадания по FTE, как у линии».

Три дефекта, найденные прогоном настоящего генератора на боевых данных, все — в
солвере _run_shift_preview_cp_sat_fixed_mix_refine_strategy, который и выдаёт
итоговый график:

1. Число смен каждой ставки задавалось РАВЕНСТВОМ. Убрать лишнюю смену там, где
   спрос закрыт, решатель не мог — только переставить её на другой час. Отсюда
   весь перелимит. Это же объясняет, почему подбор весов цели ничего не менял:
   количество держалось ограничением, а не целевой функцией.
2. Перебор «сколько всего смен» состоял из одного шага вниз.
3. Недельного потолка по ставке не было вовсе: смен ставки могло оказаться
   больше, чем человеко-смен этой ставки, — такой график некому выдать.

Замер до/после (боевые недели, режим без учёта ёмкости):
  ЛИНИЯ 31.08: перелимит 61.0 → 12.5, MAE/час 0.429 → 0.140, худший день +31.9% → +1.2%
  ЧАТ   31.08: перелимит 80.5 → 45.5, MAE/час 0.515 → 0.312, худший час +8.0 → +2.5
"""
import ast
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATION = os.path.join(REPO_ROOT, "resource_fte", "schedule_generation.py")

from resource_fte.schedule_generation import (  # noqa: E402
    SHIFT_PREVIEW_FIXED_MIX_TOTAL_STEPS,
    _shift_preview_fits_weekly_capacity,
    _shift_preview_proportional_results,
)


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _function_source(name):
    src = _read(GENERATION)
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} не найдена")


def _shift(rate):
    return {"template": {"rate": rate}}


class FixedCountRelaxationTests(unittest.TestCase):
    """Главная правка: «не больше», а не «ровно столько»."""

    def test_per_rate_count_is_an_upper_bound(self):
        source = _function_source("_run_shift_preview_cp_sat_fixed_mix_refine_strategy")
        self.assertIn(
            "model.Add(sum(selected_vars[index] for index in indexes) "
            "<= effective_rate_counts.get(rate_key, 0))",
            source,
            "число смен ставки должно быть потолком, иначе лишнюю смену не убрать")
        self.assertNotIn(
            "model.Add(sum(selected_vars[index] for index in indexes) "
            "== effective_rate_counts.get(rate_key, 0))",
            source,
            "равенство вернулось — перелимит вернётся вместе с ним")

    def test_deficit_still_has_a_hard_ceiling(self):
        """Послабление безопасно ровно потому, что дефицит ограничен сверху.

        Без этого потолка «поставить меньше смен» стало бы способом обнулить
        перелимит ценой дыр в покрытии.
        """
        source = _function_source("_run_shift_preview_cp_sat_fixed_mix_refine_strategy")
        self.assertIn("model.Add(sum(deficit_vars) <=", source)
        self.assertIn("sum(over_vars) * 1000", source,
                      "перелимит обязан оставаться дороже дефицита в этой цели")


class WeeklyCapacityTests(unittest.TestCase):
    """Смен ставки не может быть больше, чем человеко-смен этой ставки."""

    CAPACITY = {
        "1": {"rate": 1.0, "weekly_shift_capacity": 40, "daily_shift_capacity": 8, "mix_count": 8},
        "0.75": {"rate": 0.75, "weekly_shift_capacity": 25, "daily_shift_capacity": 5, "mix_count": 5},
    }

    def test_fits_capacity_accepts_a_schedule_within_the_slots(self):
        selected = [_shift(1.0)] * 40 + [_shift(0.75)] * 25
        self.assertTrue(_shift_preview_fits_weekly_capacity(selected, self.CAPACITY))

    def test_fits_capacity_rejects_more_shifts_than_people(self):
        """Ровно тот случай, что нашёлся на неделе 10.08: 83 смены при 65 слотах."""
        selected = [_shift(1.0)] * 48 + [_shift(0.75)] * 35
        self.assertFalse(_shift_preview_fits_weekly_capacity(selected, self.CAPACITY))

    def test_zero_capacity_is_treated_as_unlimited(self):
        """Ноль — это «ёмкость не задана» (режим планирования от спроса), а не запрет."""
        self.assertTrue(_shift_preview_fits_weekly_capacity(
            [_shift(1.0)] * 200, {"1": {"rate": 1.0, "weekly_shift_capacity": 0}}))

    def test_solver_caps_rate_counts_by_weekly_capacity(self):
        source = _function_source("_run_shift_preview_cp_sat_fixed_mix_refine_strategy")
        self.assertIn('"weekly_shift_capacity"', source,
                      "решатель обязан знать недельную ёмкость ставки")


class FeasibilityBeatsProportionTests(unittest.TestCase):
    """Невыполнимый график отбраковывается РАНЬШЕ непропорционального.

    Непропорциональный график хотя бы существует, а превышающий ёмкость — нет.
    """

    CAPACITY = {
        "1": {"rate": 1.0, "weekly_shift_capacity": 10, "daily_shift_capacity": 2, "mix_count": 2},
        "0.75": {"rate": 0.75, "weekly_shift_capacity": 10, "daily_shift_capacity": 2, "mix_count": 2},
    }

    @staticmethod
    def _result(selected, over):
        return {"selected": selected, "totals": {"overFteHours": over, "deficitFteHours": 0.0}}

    def test_over_capacity_result_is_dropped(self):
        good = self._result([_shift(1.0)] * 5 + [_shift(0.75)] * 5, 40.0)
        bad = self._result([_shift(1.0)] * 30 + [_shift(0.75)] * 30, 5.0)
        kept = _shift_preview_proportional_results([good, bad], self.CAPACITY)
        self.assertIn(good, kept)
        self.assertNotIn(bad, kept,
                         "график сверх ёмкости нельзя оставлять даже с лучшим перелимитом")

    def test_nothing_fits_falls_back_instead_of_returning_empty(self):
        """Пустой список обрушил бы выбор лучшего результата."""
        over_capacity = [self._result([_shift(1.0)] * 30, 5.0)]
        self.assertEqual(_shift_preview_proportional_results(over_capacity, self.CAPACITY),
                         over_capacity)

    def test_feasibility_runs_before_the_proportional_filter(self):
        source = _function_source("_shift_preview_proportional_results")
        feasible = source.index("_shift_preview_fits_weekly_capacity")
        proportional = source.index("SHIFT_PREVIEW_RATE_MIX_MAX_DEVIATION")
        self.assertLess(feasible, proportional,
                        "сначала выполнимость, потом пропорция")


class TotalsLadderTests(unittest.TestCase):
    """Перебор «сколько всего смен» должен давать ранжированию из чего выбирать."""

    def test_ladder_has_several_steps(self):
        self.assertGreaterEqual(len(SHIFT_PREVIEW_FIXED_MIX_TOTAL_STEPS), 3,
                                "одного шага не хватает: с ним хороший результат "
                                "не находится и линия деградирует")
        self.assertEqual(sorted(SHIFT_PREVIEW_FIXED_MIX_TOTAL_STEPS),
                         list(SHIFT_PREVIEW_FIXED_MIX_TOTAL_STEPS),
                         "шаги должны идти по возрастанию")
        self.assertTrue(all(step > 0 for step in SHIFT_PREVIEW_FIXED_MIX_TOTAL_STEPS))

    def test_ladder_is_used_when_building_candidate_totals(self):
        source = _function_source("_select_shift_preview_strategy")
        self.assertIn("SHIFT_PREVIEW_FIXED_MIX_TOTAL_STEPS", source)
        self.assertIn("candidate_totals.add(min_total - step)", source)

    def test_ladder_keeps_the_rate_proportion(self):
        """Сокращаем набор целиком, а не за счёт одной ставки.

        _shift_preview_rate_mix_target_counts раскладывает ЛЮБОЙ итог по составу
        штата, поэтому меньше работают все ставки поровну.
        """
        from resource_fte.schedule_generation import _shift_preview_rate_mix_target_counts
        capacity = {
            "1": {"rate": 1.0, "mix_count": 8, "weekly_shift_capacity": 40},
            "0.75": {"rate": 0.75, "mix_count": 4, "weekly_shift_capacity": 20},
            "0.5": {"rate": 0.5, "mix_count": 0, "weekly_shift_capacity": 0},
        }
        full = _shift_preview_rate_mix_target_counts(60, capacity)
        cut = _shift_preview_rate_mix_target_counts(30, capacity)
        self.assertEqual(sum(full.values()), 60)
        self.assertEqual(sum(cut.values()), 30)
        # доля ставки 1.0 держится около 8/12 при любом итоге
        self.assertAlmostEqual(full["1"] / sum(full.values()), 8 / 12, places=1)
        self.assertAlmostEqual(cut["1"] / sum(cut.values()), 8 / 12, places=1)


if __name__ == "__main__":
    unittest.main()
