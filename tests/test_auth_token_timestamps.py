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

    def test_sensitive_qr_builder_uses_timezone_aware_utc_expiry(self):
        function = _function("_build_sensitive_qr_token")
        expires_assignment = next(
            node for node in function.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "expires_at" for target in node.targets)
        )

        self.assertIsInstance(expires_assignment.value, ast.BinOp)
        now_call = expires_assignment.value.left
        self.assertIsInstance(now_call, ast.Call)
        self.assertIsInstance(now_call.func, ast.Attribute)
        self.assertEqual(now_call.func.attr, "now")
        self.assertEqual(ast.unparse(now_call.args[0]), "timezone.utc")

        utcnow_calls = [
            node for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and ast.unparse(node.func.value) == "datetime"
            and node.func.attr == "utcnow"
        ]
        self.assertEqual(utcnow_calls, [])

    def test_sensitive_qr_response_serializes_aware_utc_as_z(self):
        function_source = ast.unparse(_function("request_sensitive_access_qr"))
        self.assertIn('expires_at.isoformat().replace(\'+00:00\', \'Z\')', function_source)
        self.assertNotIn('expires_at.isoformat() + \'Z\'', function_source)


if __name__ == "__main__":
    unittest.main()
