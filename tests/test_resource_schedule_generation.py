import unittest

from resource_fte.schedule_generation import (
    _generate_schedule_preview_from_forecast,
    _shift_preview_rate_mix_target_counts,
    _run_shift_preview_greedy_strategy,
    _select_best_shift_preview_result,
    _select_default_schedule_preview_variant,
    get_resource_shift_templates,
)


def _forecast_payload():
    hourly = []
    for hour in range(24):
        hourly.append(
            {
                "hour": hour,
                "forecast_fte": 1.0 if 7 <= hour < 22 else 0.0,
                "incident_uplift_fte": 0.0,
            }
        )
    return {
        "week_start": "2026-05-25",
        "week_end": "2026-05-25",
        "period_start": "2026-05-25",
        "period_end": "2026-05-25",
        "days": [
            {
                "forecast_date": "2026-05-25",
                "weekday": 0,
                "short": "Mon",
                "label": "Monday",
                "hourly_forecast": hourly,
            }
        ],
    }


def _tiny_operator_capacity():
    return {
        "active_operator_count": 1,
        "current_operator_fte": 1.0,
        "selected_direction_ids": [70],
        "rate_capacity": [
            {
                "rate": 1.0,
                "count": 1,
                "daily_shift_capacity": 1,
                "weekly_shift_capacity": 1,
            },
            {
                "rate": 0.75,
                "count": 0,
                "daily_shift_capacity": 0,
                "weekly_shift_capacity": 0,
            },
            {
                "rate": 0.5,
                "count": 0,
                "daily_shift_capacity": 0,
                "weekly_shift_capacity": 0,
            },
        ],
    }


class ResourceScheduleGenerationTests(unittest.TestCase):
    def test_strategy_selection_prefers_lower_overcoverage_inside_default_coverage_band(self):
        target = [1.0 for _ in range(1000)]
        exact = {
            "totals": {"deficitFteHours": 0.0, "overFteHours": 40.0},
            "selected": [{} for _ in range(20)],
        }
        lower_over = {
            "totals": {"deficitFteHours": 2.0, "overFteHours": 10.0},
            "selected": [{} for _ in range(18)],
        }

        self.assertIs(
            _select_best_shift_preview_result([exact, lower_over], target),
            lower_over,
        )

    def test_strategy_selection_rejects_lower_overcoverage_below_default_coverage_band(self):
        target = [1.0 for _ in range(1000)]
        exact = {
            "totals": {"deficitFteHours": 0.0, "overFteHours": 40.0},
            "selected": [{} for _ in range(20)],
        }
        too_sparse = {
            "totals": {"deficitFteHours": 3.0, "overFteHours": 0.0},
            "selected": [{} for _ in range(17)],
        }

        self.assertIs(
            _select_best_shift_preview_result([exact, too_sparse], target),
            exact,
        )

    def test_default_variant_prefers_lower_overcoverage_inside_default_coverage_band(self):
        template_variant = {
            "key": "templates",
            "summary": {"neededFteHours": 1000.0, "deficitFteHours": 0.0, "overFteHours": 40.0},
            "generation": {"qualityScore": 118.0},
        }
        freeform_variant = {
            "key": "freeform",
            "summary": {"neededFteHours": 1000.0, "deficitFteHours": 2.0, "overFteHours": 10.0},
            "generation": {"qualityScore": 230.0},
        }

        self.assertIs(
            _select_default_schedule_preview_variant([template_variant, freeform_variant]),
            freeform_variant,
        )

    def test_greedy_selection_keeps_shift_rates_in_percentage_mix(self):
        rate_capacity = {
            "1": {
                "rate": 1.0,
                "mix_count": 1,
                "daily_shift_capacity": 20,
                "weekly_shift_capacity": 20,
            },
            "0.75": {
                "rate": 0.75,
                "mix_count": 1,
                "daily_shift_capacity": 20,
                "weekly_shift_capacity": 20,
            },
            "0.5": {
                "rate": 0.5,
                "mix_count": 2,
                "daily_shift_capacity": 20,
                "weekly_shift_capacity": 20,
            },
        }
        candidates = []
        for rate_key, rate in (("1", 1.0), ("0.75", 0.75), ("0.5", 0.5)):
            candidates.append(
                {
                    "dayIndex": 0,
                    "rateKey": rate_key,
                    "template": {"id": rate_key, "rate": rate},
                    "source": "unit_test",
                    "preferenceScore": 0.0,
                    "vector": [1.0],
                    "activeVector": [(0, 1.0)],
                    "presenceVector": [1.0],
                    "activePresenceVector": [(0, 1.0)],
                }
            )

        result = _run_shift_preview_greedy_strategy(
            [20.0],
            candidates,
            rate_capacity,
            {
                "name": "precision",
                "need_weight": 10.0,
                "over_weight": 2.0,
                "active_weight": 0.015,
                "day_deficit_weight": 0.12,
                "min_score": 0.0,
                "min_covered_need": 0.05,
                "prune_mode": "strict",
            },
        )
        counts = {1.0: 0, 0.75: 0, 0.5: 0}
        for item in result["selected"]:
            counts[item["template"]["rate"]] += 1

        self.assertEqual(counts, {1.0: 5, 0.75: 5, 0.5: 10})

    def test_rate_mix_target_counts_preserve_department_percentages(self):
        self.assertEqual(
            _shift_preview_rate_mix_target_counts(
                410,
                {
                    "1": {"mix_count": 2},
                    "0.75": {"mix_count": 14},
                    "0.5": {"mix_count": 33},
                },
            ),
            {"1": 17, "0.75": 117, "0.5": 276},
        )

    def test_default_preview_plans_from_forecast_need_instead_of_staff_cap(self):
        preview = _generate_schedule_preview_from_forecast(
            _forecast_payload(),
            get_resource_shift_templates()["templates"],
            _tiny_operator_capacity(),
        )

        self.assertEqual(preview["capacity"]["constraintMode"], "forecast_demand")
        self.assertEqual(preview["summary"]["deficitFteHours"], 0)
        self.assertGreater(
            sum(item["weeklyShiftsUsed"] for item in preview["capacity"]["rates"]),
            sum(item["weeklyShiftCapacity"] for item in preview["capacity"]["rates"]),
        )

    def test_preview_can_still_opt_into_operator_capacity_constraint(self):
        preview = _generate_schedule_preview_from_forecast(
            _forecast_payload(),
            get_resource_shift_templates()["templates"],
            _tiny_operator_capacity(),
            respect_operator_capacity=True,
        )

        self.assertEqual(preview["capacity"]["constraintMode"], "operator_capacity")
        self.assertGreater(preview["summary"]["deficitFteHours"], 0)

    def test_preview_uses_work_schedule_overnight_carry_in_for_first_day(self):
        forecast = _forecast_payload()
        for row in forecast["days"][0]["hourly_forecast"]:
            row["forecast_fte"] = 1.0 if row["hour"] < 8 else 0.0

        preview = _generate_schedule_preview_from_forecast(
            forecast,
            get_resource_shift_templates()["templates"],
            _tiny_operator_capacity(),
            carry_in_shifts=[
                {
                    "startMinute": 20 * 60,
                    "endMinute": 28 * 60,
                    "overnight": True,
                }
            ],
        )

        self.assertEqual(preview["carryIn"]["shiftCount"], 1)
        self.assertEqual(preview["carryIn"]["coveredFteHours"], 4.0)
        self.assertEqual(preview["days"][0]["coverage"][0]["covered"], 1.0)
        self.assertEqual(preview["days"][0]["coverage"][3]["covered"], 1.0)
        self.assertTrue(preview["days"][0]["shifts"][0]["isLocked"])
        self.assertTrue(preview["days"][0]["shifts"][0]["excludeFromAuction"])


if __name__ == "__main__":
    unittest.main()
