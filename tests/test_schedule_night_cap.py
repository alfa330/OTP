# -*- coding: utf-8 -*-
"""Генератор не ставит две ночи 20*08 на один день.

Решение владельца 29.08.2026 по чат-направлению: «по две ночи в один день стоят,
такое недопустимо». Появлялось это уже НА ГЕНЕРАЦИИ, до всякого аукциона.

Почему получалось. Глубокую ночь 03:00–07:00 не покрывает ни один другой шаблон
(`_is_freeform_regular_shift_allowed` запрещает обычным сменам туда лезть), а
дефицит в эти часы штрафуется втрое с лишним дороже обычного:
DEEP_NIGHT_NEED_WEIGHT 3.5 × CP_SAT_DEFICIT_WEIGHT 100 против OVER_WEIGHT 5 —
отношение 70:1. У чата потребность в 03–06 равна 1,2–1,8 чатника, стабильно выше
единицы, и солверу выгоднее поставить вторую ДВЕНАДЦАТИЧАСОВУЮ смену, чем
оставить непокрытыми доли человека под утро.

Второй источник той же беды — проход «под прирост»: он идёт по тем же кандидатам
поверх основного набора и раньше не знал, что основной уже поставил ночь.
"""
import ast
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATION = os.path.join(REPO_ROOT, "resource_fte", "schedule_generation.py")

from resource_fte.schedule_generation import (  # noqa: E402
    FREEFORM_NIGHT_SHIFT_END_MINUTE,
    FREEFORM_NIGHT_SHIFT_START_MINUTE,
    MAX_NIGHT_SHIFTS_PER_DAY,
    _is_night_shift_template,
    _select_shift_preview_strategy,
    _shift_preview_candidate_upper_bound,
)


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _night_template():
    return {
        "startMinute": FREEFORM_NIGHT_SHIFT_START_MINUTE,
        "endMinute": FREEFORM_NIGHT_SHIFT_END_MINUTE,
        "label": "20*08",
        "rate": 1.0,
    }


def _candidate(template, day_index=0, total_hours=24):
    """Кандидат с вектором на ВЕСЬ период.

    Длина обязана совпадать с длиной цели: обрезка вектора роняет обрезку
    отобранного (`_shift_preview_prune_selected`) по IndexError.
    """
    vector = [0.0] * total_hours
    start_hour = day_index * 24 + int(template.get("startMinute", 0)) // 60
    end_hour = day_index * 24 + int(template.get("endMinute", 0)) // 60
    for hour in range(start_hour, min(end_hour, total_hours)):
        vector[hour] = 1.0
    active = [(index, value) for index, value in enumerate(vector) if value > 0]
    return {
        "dayIndex": day_index,
        "template": template,
        "rateKey": "1",
        "source": "templates",
        "preferenceScore": 0.0,
        "vector": vector,
        "activeVector": active,
        "presenceVector": list(vector),
        "activePresenceVector": list(active),
    }


class NightTemplateDetectionTests(unittest.TestCase):
    def test_only_20_08_counts_as_night(self):
        self.assertTrue(_is_night_shift_template(_night_template()))
        # 17*02 тоже уходит за полночь, но ночью 20*08 не является.
        self.assertFalse(_is_night_shift_template(
            {"startMinute": 17 * 60, "endMinute": 26 * 60}))
        self.assertFalse(_is_night_shift_template(
            {"startMinute": 20 * 60, "endMinute": 24 * 60}))
        self.assertFalse(_is_night_shift_template({}))
        self.assertFalse(_is_night_shift_template(None))

    def test_cap_is_one(self):
        self.assertEqual(MAX_NIGHT_SHIFTS_PER_DAY, 1)


class NightUpperBoundTests(unittest.TestCase):
    """Граница кандидата — общая точка всех трёх CP-SAT-солверов."""

    CAPACITY = {"1": {"daily_shift_capacity": 9, "weekly_shift_capacity": 40}}

    def test_night_candidate_is_capped_at_one(self):
        bound = _shift_preview_candidate_upper_bound(
            _candidate(_night_template(), total_hours=48), self.CAPACITY, [5.0] * 48)
        self.assertEqual(bound, MAX_NIGHT_SHIFTS_PER_DAY)

    def test_ordinary_candidate_keeps_its_bound(self):
        """Предел точечный: обычные смены обязаны остаться множественными."""
        ordinary = {"startMinute": 8 * 60, "endMinute": 17 * 60, "label": "8*17", "rate": 1.0}
        bound = _shift_preview_candidate_upper_bound(
            _candidate(ordinary, total_hours=48), self.CAPACITY, [5.0] * 48)
        self.assertGreater(bound, 1, "обычную смену ограничивать одной штукой нельзя")

    def test_zero_capacity_still_wins_over_the_night_cap(self):
        """Нет ставки — нет смены, даже ночной."""
        bound = _shift_preview_candidate_upper_bound(
            _candidate(_night_template(), total_hours=48),
            {"1": {"daily_shift_capacity": 0, "weekly_shift_capacity": 0}},
            [5.0] * 48)
        self.assertEqual(bound, 0)


class NightAcrossPassesTests(unittest.TestCase):
    """Проход «под прирост» идёт поверх основного — предел обязан это учитывать."""

    def test_already_selected_night_removes_the_candidate(self):
        night = _night_template()
        candidates = [_candidate(night, day_index=0, total_hours=72),
                      _candidate(night, day_index=1, total_hours=72)]
        result = _select_shift_preview_strategy(
            [1.0] * 72,
            candidates,
            {"1": {"daily_shift_capacity": 9, "weekly_shift_capacity": 40}},
            initial_selected=[{"dayIndex": 0, "template": night}],
        )
        chosen_days = {
            int(item.get("dayIndex"))
            for item in (result.get("best") or {}).get("selected") or []
            if _is_night_shift_template(item.get("template"))
        }
        self.assertNotIn(0, chosen_days,
                         "на день, где ночь уже стоит, вторую ставить нельзя")

    def test_free_day_still_gets_its_night(self):
        """Отсекаем ровно занятый день, а не ночи вообще."""
        night = _night_template()
        candidates = [_candidate(night, day_index=1, total_hours=72)]
        result = _select_shift_preview_strategy(
            [0.0] * 24 + [1.0] * 48,
            candidates,
            {"1": {"daily_shift_capacity": 9, "weekly_shift_capacity": 40}},
            initial_selected=[{"dayIndex": 0, "template": night}],
        )
        chosen = [
            item for item in (result.get("best") or {}).get("selected") or []
            if _is_night_shift_template(item.get("template"))
        ]
        self.assertTrue(chosen, "свободный день должен получить свою ночь")


class NightCapIsWiredEverywhereTests(unittest.TestCase):
    """Предел стоит во всех отборах: жадный, границы CP-SAT и фильтр пула."""

    @staticmethod
    def _function(name):
        src = _read(GENERATION)
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.get_source_segment(src, node) or ""
        raise AssertionError(f"{name} не найдена")

    def test_greedy_counts_nights_per_day(self):
        """У жадного отбора своих границ на шаблон нет — только на ставку."""
        source = self._function("_run_shift_preview_greedy_strategy")
        self.assertIn("night_usage", source)
        self.assertIn("MAX_NIGHT_SHIFTS_PER_DAY", source)
        self.assertIn("_is_night_shift_template(best.get(\"template\"))", source,
                      "счётчик ночей должен расти при фиксации выбора")

    def test_upper_bound_caps_nights(self):
        source = self._function("_shift_preview_candidate_upper_bound")
        self.assertIn("MAX_NIGHT_SHIFTS_PER_DAY", source)

    def test_strategy_drops_night_candidates_on_busy_days(self):
        source = self._function("_select_shift_preview_strategy")
        self.assertIn("MAX_NIGHT_SHIFTS_PER_DAY", source)
        self.assertIn("initial_selected", source)

    def test_uplift_pass_knows_the_base_selection(self):
        """Без этого догон ставил вторую ночь на день, где первая уже стоит."""
        source = self._function("_build_schedule_preview_variant")
        self.assertIn("initial_selected=base_selected", source)


if __name__ == "__main__":
    unittest.main()
