import ast
import unittest
from pathlib import Path


BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"


def _function(name):
    module = ast.parse(BOT_PATH.read_text(encoding="utf-8-sig"))
    return next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


class AuthTokenTimestampTests(unittest.TestCase):
    def test_jwt_builders_use_timezone_aware_utc_now(self):
        for function_name in ("_build_access_token", "_build_refresh_token"):
            with self.subTest(function_name=function_name):
                function = _function(function_name)
                now_assignment = next(
                    node for node in function.body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "now" for target in node.targets)
                )
                self.assertIsInstance(now_assignment.value, ast.Call)
                self.assertIsInstance(now_assignment.value.func, ast.Attribute)
                self.assertEqual(now_assignment.value.func.attr, "now")
                self.assertEqual(
                    ast.unparse(now_assignment.value.args[0]),
                    "timezone.utc",
                )


if __name__ == "__main__":
    unittest.main()
