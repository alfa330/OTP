import ast
import math
import re
import textwrap
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"


def _function_source(path, function_name, class_name=None):
    source = path.read_text(encoding="utf-8-sig")
    module = ast.parse(source)
    body = module.body
    if class_name:
        class_node = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        body = class_node.body
    node = next(
        item for item in body
        if isinstance(item, ast.FunctionDef) and item.name == function_name
    )
    return textwrap.dedent(ast.get_source_segment(source, node))


class _BreakRuleDummy:
    def _normalize_break_durations_list(self, value):
        return [int(item) for item in (value or []) if int(item) > 0]

    def _is_chat_manager_direction(self, direction_name):
        return str(direction_name or "").strip().lower() in {"чат менеджер", "chat manager"}


class _MergeDummy:
    def _schedule_interval_minutes(self, start_time_value, end_time_value):
        def to_minutes(value):
            hh, mm = str(value).split(":", 1)
            return int(hh) * 60 + int(mm)

        start_min = to_minutes(start_time_value)
        end_min = to_minutes(end_time_value)
        if end_min <= start_min:
            end_min += 24 * 60
        return start_min, end_min

    def _normalize_schedule_time(self, value, field_name):
        return value


class _BreakAdjustDummy(_MergeDummy):
    def __init__(self, occupied=None, cross_gap=0):
        self.occupied = occupied or []
        self.cross_gap = int(cross_gap or 0)

    def _load_occupied_break_intervals_for_operator_date_tx(self, cursor, operator_id, shift_date):
        return list(self.occupied)

    def _get_operator_direction_name_tx(self, cursor, operator_id):
        return "Направление"

    def _get_break_cross_operator_gap_for_direction_tx(self, cursor, direction_name):
        return self.cross_gap


class _TechReasonDummy:
    pass


BREAK_ADJUST_METHODS = (
    "_break_intervals_overlap",
    "_merge_break_intervals",
    "_pad_break_intervals_for_cross_gap",
    "_break_layout_spacing",
    "_break_start_bounds_for_index",
    "_break_total_overlap_minutes",
    "_find_best_break_start",
    "_adjust_shift_breaks_against_occupied_tx",
)


def _make_break_adjust_dummy(occupied=None):
    namespace = {"math": math}
    for function_name in BREAK_ADJUST_METHODS:
        exec(_function_source(DATABASE_PATH, function_name, class_name="Database"), namespace)

    dummy = _BreakAdjustDummy(occupied=occupied)
    for function_name in BREAK_ADJUST_METHODS:
        setattr(dummy, function_name, namespace[function_name].__get__(dummy, _BreakAdjustDummy))
    return dummy


class WorkScheduleBreakRuleTests(unittest.TestCase):
    def test_phone_shift_type_normalization_and_merge_priority(self):
        namespace = {
            "Any": Any,
            "Dict": Dict,
            "List": List,
            "Optional": Optional,
            "WORK_SHIFT_TYPE_REGULAR": "regular",
            "WORK_SHIFT_TYPE_OFFICE_PRACTICE": "office_practice",
            "WORK_SHIFT_TYPE_PHONE_SHIFT": "phone_shift",
            "WORK_SHIFT_TYPE_ALLOWED": {"regular", "office_practice", "phone_shift"},
            "WORK_SHIFT_TYPE_PRIORITY": {
                "regular": 0,
                "office_practice": 1,
                "phone_shift": 2,
            },
        }
        for function_name in (
            "_time_to_minutes",
            "_minutes_to_time",
            "_normalize_work_shift_type_value",
            "_work_shift_type_priority",
            "_merge_shifts_for_date",
        ):
            exec(_function_source(DATABASE_PATH, function_name), namespace)

        normalize = namespace["_normalize_work_shift_type_value"]
        merge = namespace["_merge_shifts_for_date"]

        self.assertEqual(normalize("phone_shift"), "phone_shift")
        self.assertEqual(normalize("phones"), "phone_shift")
        self.assertEqual(normalize("office_practice"), "office_practice")

        result = merge([
            {"id": 1, "start": "09:00", "end": "13:00", "shift_type": "regular"},
            {"id": 2, "start": "10:00", "end": "12:00", "shift_type": "phone_shift"},
            {"id": 3, "start": "12:00", "end": "14:00", "shift_type": "office_practice"},
        ])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["start"], "09:00")
        self.assertEqual(result[0]["end"], "14:00")
        self.assertEqual(result[0]["shift_type"], "phone_shift")

    def test_phone_shift_training_is_exposed_as_offline_activity_source(self):
        recalc_source = _function_source(
            DATABASE_PATH,
            "_recalculate_auto_daily_hours_tx",
            class_name="Database",
        )
        list_source = _function_source(
            DATABASE_PATH,
            "get_operator_offline_activities",
            class_name="Database",
        )
        summary_source = _function_source(
            DATABASE_PATH,
            "get_hours_summary",
            class_name="Database",
        )
        loader_source = _function_source(
            DATABASE_PATH,
            "_load_phone_shift_training_offline_intervals_by_operator_day_tx",
            class_name="Database",
        )
        manual_loader_source = _function_source(
            DATABASE_PATH,
            "_load_phone_shift_manual_training_offline_intervals_by_operator_day_tx",
            class_name="Database",
        )

        self.assertIn("WORK_SHIFT_TYPE_PHONE_SHIFT", recalc_source)
        self.assertIn("phone_shift_training_intervals", recalc_source)
        self.assertIn("_schedule_auto_subtract_intervals", recalc_source)
        self.assertIn("WORK_SHIFT_PHONE_SHIFT_OFFLINE_COMMENT", loader_source)
        self.assertIn("work_shift_training_status", loader_source)
        self.assertIn("training_phone_shift", manual_loader_source)
        self.assertIn("counted_only", manual_loader_source)
        self.assertIn("manual_phone_training_counted_totals", summary_source)
        self.assertIn("manual_phone_training_map", list_source)
        self.assertIn("_load_phone_shift_training_offline_intervals_by_operator_day_tx", list_source)
        self.assertIn("'read_only': True", list_source)

    def test_database_custom_direction_rules_disable_default_fallback_for_gaps(self):
        namespace = {}
        exec(_function_source(DATABASE_PATH, "_pick_break_durations_for_shift", class_name="Database"), namespace)
        pick = namespace["_pick_break_durations_for_shift"]
        rules = [
            {"minMinutes": 330, "maxMinutes": 390, "breakDurations": [15]},
        ]

        self.assertEqual(pick(_BreakRuleDummy(), 300, direction_name="Основа", direction_rules=rules), [])
        self.assertEqual(pick(_BreakRuleDummy(), 300, direction_name="Основа", direction_rules=[]), [15])

    def test_tech_reason_status_detection_accepts_chat2desk_aliases(self):
        namespace = {"re": re}
        exec(_function_source(DATABASE_PATH, "_schedule_auto_compact_status_key", class_name="Database"), namespace)
        exec(_function_source(DATABASE_PATH, "_schedule_auto_is_tech_reason_status_key", class_name="Database"), namespace)
        dummy = _TechReasonDummy()
        dummy._schedule_auto_compact_status_key = namespace["_schedule_auto_compact_status_key"].__get__(dummy, _TechReasonDummy)
        is_tech_reason = namespace["_schedule_auto_is_tech_reason_status_key"]

        self.assertTrue(is_tech_reason(dummy, "тех причина"))
        self.assertTrue(is_tech_reason(dummy, "tech_break"))
        self.assertTrue(is_tech_reason(dummy, "status.tech_break"))

    def test_import_simulation_custom_direction_rules_disable_default_fallback_for_gaps(self):
        namespace = {}
        for function_name in (
            "_ws_normalize_direction_key",
            "_ws_normalize_break_durations",
            "_ws_is_chat_manager_direction",
            "_ws_pick_break_durations_for_shift",
        ):
            exec(_function_source(BOT_PATH, function_name), namespace)
        pick = namespace["_ws_pick_break_durations_for_shift"]
        rules_map = {
            "основа": [
                {"minMinutes": 330, "maxMinutes": 390, "breakDurations": [15]},
            ]
        }

        self.assertEqual(pick(300, direction_value="Основа", break_rules_map=rules_map), [])
        self.assertEqual(pick(300, direction_value="Основа", break_rules_map={}), [15])

    def test_database_break_adjustment_keeps_breaks_away_from_shift_edges_and_each_other(self):
        dummy = _make_break_adjust_dummy()

        result = dummy._adjust_shift_breaks_against_occupied_tx(
            cursor=None,
            operator_id=1,
            shift_date="2026-05-21",
            start_time="12:00",
            end_time="21:00",
            breaks=[
                {"start": 12 * 60 + 10, "end": 12 * 60 + 25},
                {"start": 12 * 60 + 25, "end": 12 * 60 + 40},
                {"start": 16 * 60 + 15, "end": 16 * 60 + 45},
            ],
        )

        self.assertEqual([item["end"] - item["start"] for item in result], [15, 15, 30])
        self.assertGreaterEqual(result[0]["start"] - 12 * 60, 90)
        self.assertGreaterEqual(21 * 60 - result[-1]["end"], 90)
        self.assertTrue(all(b["start"] - a["end"] >= 45 for a, b in zip(result, result[1:])))

    def test_database_break_adjustment_prefers_good_overlap_slot_over_edge_violation(self):
        dummy = _make_break_adjust_dummy(occupied=[{"start": 16 * 60 + 15, "end": 16 * 60 + 45}])

        result = dummy._adjust_shift_breaks_against_occupied_tx(
            cursor=None,
            operator_id=1,
            shift_date="2026-05-21",
            start_time="12:00",
            end_time="21:00",
            breaks=[
                {"start": 14 * 60 + 10, "end": 14 * 60 + 25},
                {"start": 16 * 60 + 15, "end": 16 * 60 + 45},
                {"start": 18 * 60 + 40, "end": 18 * 60 + 55},
            ],
        )

        lunch = result[1]
        self.assertFalse(dummy._break_intervals_overlap(lunch, {"start": 16 * 60 + 15, "end": 16 * 60 + 45}))
        self.assertGreaterEqual(lunch["start"] - result[0]["end"], 45)
        self.assertGreaterEqual(result[2]["start"] - lunch["end"], 45)

    def test_database_break_adjustment_keeps_cross_operator_gap_from_other_operators(self):
        # Перерыв коллеги 16:15-16:45 и требование держать 10 минут между
        # перерывами разных операторов: свой обед не должен встать вплотную.
        colleague_break = {"start": 16 * 60 + 15, "end": 16 * 60 + 45}
        dummy = _make_break_adjust_dummy(occupied=[colleague_break])

        result = dummy._adjust_shift_breaks_against_occupied_tx(
            cursor=None,
            operator_id=1,
            shift_date="2026-08-04",
            start_time="12:00",
            end_time="21:00",
            breaks=[
                {"start": 14 * 60 + 10, "end": 14 * 60 + 25},
                {"start": 16 * 60 + 15, "end": 16 * 60 + 45},
                {"start": 18 * 60 + 40, "end": 18 * 60 + 55},
            ],
            cross_gap_minutes=10,
        )

        self.assertEqual([item["end"] - item["start"] for item in result], [15, 30, 15])
        for item in result:
            distance = (
                item["start"] - colleague_break["end"]
                if item["start"] >= colleague_break["end"]
                else colleague_break["start"] - item["end"]
            )
            self.assertGreaterEqual(distance, 10)

    def test_database_break_adjustment_without_gap_setting_keeps_previous_layout(self):
        colleague_break = {"start": 16 * 60 + 15, "end": 16 * 60 + 45}
        breaks = [
            {"start": 14 * 60 + 10, "end": 14 * 60 + 25},
            {"start": 16 * 60 + 15, "end": 16 * 60 + 45},
            {"start": 18 * 60 + 40, "end": 18 * 60 + 55},
        ]

        without_gap = _make_break_adjust_dummy(occupied=[colleague_break])._adjust_shift_breaks_against_occupied_tx(
            cursor=None,
            operator_id=1,
            shift_date="2026-08-04",
            start_time="12:00",
            end_time="21:00",
            breaks=[dict(item) for item in breaks],
            cross_gap_minutes=0,
        )
        legacy = _make_break_adjust_dummy(occupied=[colleague_break])._adjust_shift_breaks_against_occupied_tx(
            cursor=None,
            operator_id=1,
            shift_date="2026-08-04",
            start_time="12:00",
            end_time="21:00",
            breaks=[dict(item) for item in breaks],
        )

        self.assertEqual(without_gap, legacy)

    def test_database_cross_gap_padding_merges_touching_intervals(self):
        dummy = _make_break_adjust_dummy()
        padded = dummy._pad_break_intervals_for_cross_gap(
            [{"start": 600, "end": 630}, {"start": 645, "end": 675}],
            10,
        )
        self.assertEqual(padded, [{"start": 590, "end": 685}])
        self.assertEqual(
            dummy._pad_break_intervals_for_cross_gap([{"start": 600, "end": 630}], 0),
            [{"start": 600, "end": 630}],
        )

    def test_import_simulation_pads_other_operators_breaks_by_direction_gap(self):
        namespace = {}
        for function_name in (
            "_ws_normalize_direction_key",
            "_ws_merge_intervals",
            "_ws_cross_operator_gap_for_direction",
            "_ws_add_days_str",
            "_ws_parse_date_str",
            "_ws_build_occupied_intervals_for_date",
        ):
            exec(_function_source(BOT_PATH, function_name), namespace)
        namespace["datetime"] = __import__("datetime").datetime
        namespace["timedelta"] = __import__("datetime").timedelta
        build = namespace["_ws_build_occupied_intervals_for_date"]

        operators = [
            {
                "id": 1,
                "direction": "Чат менеджер",
                "shifts": {"2026-08-04": [{"breaks": [{"start": 600, "end": 630}]}]},
            },
            {
                "id": 2,
                "direction": "Чат менеджер",
                "shifts": {"2026-08-04": [{"breaks": [{"start": 700, "end": 730}]}]},
            },
        ]

        def scope_key(direction_value):
            return f"dir:{str(direction_value or '').strip().lower()}"

        without_gap = build(
            all_operators=operators,
            date_str="2026-08-04",
            exclude_op_id=1,
            direction_scope="Чат менеджер",
            get_scope_key=scope_key,
        )
        self.assertEqual(without_gap, [{"start": 700, "end": 730}])

        with_gap = build(
            all_operators=operators,
            date_str="2026-08-04",
            exclude_op_id=1,
            direction_scope="Чат менеджер",
            get_scope_key=scope_key,
            break_gaps_map={"чат менеджер": 10},
        )
        self.assertEqual(with_gap, [{"start": 690, "end": 740}])

    def test_import_simulation_uses_strictest_gap_inside_direction_group(self):
        namespace = {}
        for function_name in (
            "_ws_normalize_direction_key",
            "_ws_merge_intervals",
            "_ws_cross_operator_gap_for_direction",
            "_ws_add_days_str",
            "_ws_parse_date_str",
            "_ws_build_occupied_intervals_for_date",
        ):
            exec(_function_source(BOT_PATH, function_name), namespace)
        namespace["datetime"] = __import__("datetime").datetime
        namespace["timedelta"] = __import__("datetime").timedelta
        build = namespace["_ws_build_occupied_intervals_for_date"]

        operators = [
            {"id": 1, "direction": "Основа", "shifts": {}},
            {
                "id": 2,
                "direction": "Чат менеджер",
                "shifts": {"2026-08-04": [{"breaks": [{"start": 700, "end": 730}]}]},
            },
        ]

        # Оба направления в одной группе перерывов.
        def scope_key(_direction_value):
            return "group:основа|чат менеджер"

        occupied = build(
            all_operators=operators,
            date_str="2026-08-04",
            exclude_op_id=1,
            direction_scope="Основа",
            get_scope_key=scope_key,
            break_gaps_map={"чат менеджер": 15},
        )
        self.assertEqual(occupied, [{"start": 685, "end": 745}])

    def test_frontend_break_simulation_receives_cross_operator_gap(self):
        app_source = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")

        self.assertIn("getPlannerBreakCrossGapForDirection", app_source)
        self.assertIn("crossOperatorGapMinutes", app_source)
        # Все три места локальной симуляции перерывов должны знать про промежуток.
        self.assertEqual(
            app_source.count(
                "getPlannerBreakRuleRangesForDirection, getPlannerBreakCrossGapForDirection)"
            ),
            3,
        )

    def test_auction_publish_merges_touching_claimed_shifts_before_saving(self):
        namespace = {}
        exec(_function_source(DATABASE_PATH, "_minutes_to_time"), namespace)
        exec(
            _function_source(
                DATABASE_PATH,
                "_merge_shift_auction_claimed_shifts_for_publish",
                class_name="Database"
            ),
            namespace
        )
        merge = namespace["_merge_shift_auction_claimed_shifts_for_publish"]

        result = merge(_MergeDummy(), [
            {"start_time": "15:00", "end_time": "19:00"},
            {"start_time": "19:00", "end_time": "23:00"},
            {"start_time": "08:00", "end_time": "12:00"},
            {"start_time": "13:00", "end_time": "14:00"},
        ])

        self.assertEqual(result, [
            {"start_time": "08:00", "end_time": "12:00"},
            {"start_time": "13:00", "end_time": "14:00"},
            {"start_time": "15:00", "end_time": "23:00"},
        ])

    def test_post_auction_claim_merges_full_touching_shift_chain(self):
        namespace = {}
        exec(
            _function_source(
                DATABASE_PATH,
                "_resolve_post_auction_merged_shift_range",
                class_name="Database"
            ),
            namespace
        )
        resolve = namespace["_resolve_post_auction_merged_shift_range"]

        start_min, end_min, merge_ids = resolve(_MergeDummy(), [
            (1, "11:00", "15:00"),
            (2, "15:00", "19:00"),
            (3, "23:30", "01:00"),
        ], 19 * 60, 23 * 60)

        self.assertEqual((start_min, end_min), (11 * 60, 23 * 60))
        self.assertEqual(merge_ids, [2, 1])

    def test_post_auction_merge_preserves_overnight_tail_when_saving(self):
        namespace = {}
        exec(_function_source(DATABASE_PATH, "_minutes_to_time"), namespace)
        minutes_to_time = namespace["_minutes_to_time"]

        self.assertEqual(minutes_to_time(32 * 60), "08:00")
        for method_name in ("post_auction_claim_lot", "post_auction_claim_saved_shift"):
            source = _function_source(DATABASE_PATH, method_name, class_name="Database")
            self.assertIn("_minutes_to_time(merged_end_min)", source)
            self.assertNotIn("if merged_end_min < 24 * 60 else '00:00'", source)

    def test_post_auction_claim_rejects_overlap_with_existing_shift(self):
        namespace = {}
        exec(
            _function_source(
                DATABASE_PATH,
                "_resolve_post_auction_merged_shift_range",
                class_name="Database"
            ),
            namespace
        )
        resolve = namespace["_resolve_post_auction_merged_shift_range"]

        with self.assertRaisesRegex(ValueError, "SHIFT_OVERLAPS_EXISTING"):
            resolve(_MergeDummy(), [(1, "18:30", "21:00")], 19 * 60, 23 * 60)


if __name__ == "__main__":
    unittest.main()
