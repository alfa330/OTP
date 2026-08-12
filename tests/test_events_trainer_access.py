"""Тренеру раздел «Ивенты» открыт на чтение.

Бэкенд уже считает тренера глобальным зрителем ленты, а публиковать ему не
даёт; ломался только фронт: два гарда «чистого тренера» выбрасывали его из
раздела обратно в «Опросы», хотя пункт меню в сайдбаре был виден всем ролям.
"""
import ast
import textwrap
import unittest
from pathlib import Path

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "src" / "App.jsx"
BOT_PATH = ROOT / "bot_schedule2.py"
DEPARTMENT_VIEWS_PATH = ROOT / "src" / "utils" / "departmentViews.js"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _function_source(path, function_name):
    source = _read(path)
    module = source_cache.parse(source)
    node = next(
        item for item in module.body
        if isinstance(item, ast.FunctionDef) and item.name == function_name
    )
    return textwrap.dedent(ast.get_source_segment(source, node))


class EventsTrainerAccessTests(unittest.TestCase):
    def test_trainer_view_list_is_shared_and_includes_events(self):
        source = _read(APP_PATH)

        self.assertIn("const TRAINER_ALLOWED_VIEWS = Object.freeze([", source)
        list_start = source.index("const TRAINER_ALLOWED_VIEWS = Object.freeze([")
        list_end = source.index("]);", list_start)
        trainer_views = source[list_start:list_end]
        self.assertIn("'events'", trainer_views)
        self.assertIn("'surveys'", trainer_views)

        # Оба гарда читают один список: пока их было два, раздел, добавленный в
        # один, молча отбрасывался вторым.
        self.assertEqual(source.count("TRAINER_ALLOWED_VIEWS.includes("), 2)
        self.assertNotIn(
            "const trainerAllowedViews = new Set(",
            source,
        )
        self.assertNotIn(
            "isPlainTrainer && !['surveys'",
            source,
        )

    def test_events_section_renders_in_the_trainer_branch(self):
        source = _read(APP_PATH)

        branch_start = source.index("{(isDepartmentManager || isPlainTrainer) && (")
        branch_end = source.index(
            "{(currentUserRole === 'operator' || currentUserRole === 'trainee')",
            branch_start,
        )
        trainer_branch = source[branch_start:branch_end]
        self.assertIn('view === "events"', trainer_branch)
        self.assertIn("<EventsView", trainer_branch)

    def test_department_guard_treats_events_as_universal(self):
        source = _read(DEPARTMENT_VIEWS_PATH)

        self.assertIn("const UNIVERSAL_VIEWS = new Set(['events']);", source)

    def test_backend_lets_trainer_read_all_events_but_not_publish(self):
        viewer_scope = _function_source(BOT_PATH, "_events_viewer_scope")
        self.assertIn("role == 'trainer'", viewer_scope)

        can_publish = _function_source(BOT_PATH, "_events_can_publish")
        self.assertNotIn("trainer", can_publish)


if __name__ == "__main__":
    unittest.main()
