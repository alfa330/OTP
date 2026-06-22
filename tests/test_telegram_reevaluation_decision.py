import ast
import unittest
from contextlib import contextmanager
from pathlib import Path


BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"


def _read_source():
    return BOT_PATH.read_text(encoding="utf-8-sig")


def _function_node(name):
    module = ast.parse(_read_source())
    return next(
        node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


def _function_source(name):
    source = _read_source()
    return ast.get_source_segment(source, _function_node(name))


class _Cursor:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount
        self.executions = []

    def execute(self, query, params):
        self.executions.append((query, params))


class _Database:
    def __init__(self, rowcount=1):
        self.cursor = _Cursor(rowcount=rowcount)

    @contextmanager
    def _get_cursor(self):
        yield self.cursor


def _load_comment_setter(database):
    node = _function_node("_set_reevaluation_decision_comment")
    namespace = {"db": database}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace["_set_reevaluation_decision_comment"]


class TelegramReevaluationDecisionTests(unittest.TestCase):
    def test_button_click_resolves_before_comment_state_is_created(self):
        node = _function_node("_prompt_reevaluation_decision_comment")
        finalize_line = None
        set_state_line = None

        for item in ast.walk(node):
            if not isinstance(item, ast.Call):
                continue
            if isinstance(item.func, ast.Name) and item.func.id == "_finalize_reevaluation_decision":
                finalize_line = item.lineno
            if (
                isinstance(item.func, ast.Attribute)
                and isinstance(item.func.value, ast.Name)
                and item.func.value.id == "state"
                and item.func.attr == "set_state"
            ):
                set_state_line = item.lineno

        self.assertIsNotNone(finalize_line)
        self.assertIsNotNone(set_state_line)
        self.assertLess(finalize_line, set_state_line)

    def test_skip_only_closes_optional_comment_step(self):
        source = _function_source("handle_reval_skip_comment")

        self.assertIn("await state.finish()", source)
        self.assertNotIn("_finalize_reevaluation_decision", source)
        self.assertNotIn("_resolve_call_reevaluation_request_for_telegram", source)

    def test_text_comment_updates_already_resolved_decision(self):
        source = _function_source("handle_reval_decision_comment")

        self.assertIn("_apply_call_reevaluation_comment_for_telegram", source)
        self.assertNotIn("_finalize_reevaluation_decision", source)

    def test_comment_setter_targets_approved_decision(self):
        database = _Database()
        setter = _load_comment_setter(database)

        self.assertTrue(setter(6218, "approved", "Проверено"))
        query, params = database.cursor.executions[0]
        normalized_query = " ".join(query.split())
        self.assertIn("SET sv_request_approve_comment = %s", normalized_query)
        self.assertIn("sv_request_approved = TRUE", normalized_query)
        self.assertEqual(params, ("Проверено", 6218))

    def test_comment_setter_targets_rejected_decision(self):
        database = _Database()
        setter = _load_comment_setter(database)

        self.assertTrue(setter(6190, "rejected", "Недостаточно оснований"))
        query, params = database.cursor.executions[0]
        normalized_query = " ".join(query.split())
        self.assertIn("SET sv_request_reject_comment = %s", normalized_query)
        self.assertIn("sv_request_rejected = TRUE", normalized_query)
        self.assertEqual(params, ("Недостаточно оснований", 6190))

    def test_comment_setter_ignores_empty_or_invalid_input(self):
        database = _Database()
        setter = _load_comment_setter(database)

        self.assertFalse(setter(1, "approved", "  "))
        self.assertFalse(setter(1, "unknown", "Комментарий"))
        self.assertEqual(database.cursor.executions, [])

    def test_comment_setter_reports_stale_decision(self):
        database = _Database(rowcount=0)
        setter = _load_comment_setter(database)

        self.assertFalse(setter(1, "approved", "Комментарий"))


if __name__ == "__main__":
    unittest.main()
