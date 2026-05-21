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


if __name__ == "__main__":
    unittest.main()
