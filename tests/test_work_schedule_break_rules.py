import ast
import textwrap
import unittest
from pathlib import Path


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


class WorkScheduleBreakRuleTests(unittest.TestCase):
    def test_database_custom_direction_rules_disable_default_fallback_for_gaps(self):
        namespace = {}
        exec(_function_source(DATABASE_PATH, "_pick_break_durations_for_shift", class_name="Database"), namespace)
        pick = namespace["_pick_break_durations_for_shift"]
        rules = [
            {"minMinutes": 330, "maxMinutes": 390, "breakDurations": [15]},
        ]

        self.assertEqual(pick(_BreakRuleDummy(), 300, direction_name="Основа", direction_rules=rules), [])
        self.assertEqual(pick(_BreakRuleDummy(), 300, direction_name="Основа", direction_rules=[]), [15])

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
