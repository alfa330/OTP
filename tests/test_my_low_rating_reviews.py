"""«Мои оценки» → проверки низких оценок у чат-менеджера.

Оператор видит решения ОКК только по СВОИМ низким оценкам и только после
QR-подтверждения доступа. Тесты стерегут обе границы плюс состав полей:
в ответ не должны утекать поля проверяющей стороны.
"""

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DB_PATH = ROOT / "database.py"


def _bot_function(name):
    module = ast.parse(BOT_PATH.read_text(encoding="utf-8-sig"))
    return next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _db_method(name):
    module = ast.parse(DB_PATH.read_text(encoding="utf-8-sig"))
    database_class = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    return next(
        node for node in database_class.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _load_operator_item():
    """Достаём чистые статические методы без импорта database.py целиком."""
    namespace = {}
    for name in ("_low_rating_extract_source_details", "_low_rating_operator_item"):
        node = _db_method(name)
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<db>", "exec"), namespace)

    class DatabaseShim:
        _low_rating_extract_source_details = staticmethod(namespace["_low_rating_extract_source_details"])

    namespace["Database"] = DatabaseShim
    return namespace["_low_rating_operator_item"]


class MyLowRatingsEndpointScopeTests(unittest.TestCase):
    def setUp(self):
        self.function = _bot_function("my_low_rating_reviews")
        self.source = ast.unparse(self.function)

    def test_rows_are_always_scoped_to_the_requester(self):
        call = next(
            node for node in ast.walk(self.function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "list_operator_low_rating_reviews"
        )
        operator_kwarg = next(kw for kw in call.keywords if kw.arg == "operator_id")
        self.assertEqual(ast.unparse(operator_kwarg.value), "requester_id")
        # Никакого operator_id из запроса: чужие низкие оценки недостижимы by design.
        self.assertNotIn("operator_id", [
            ast.unparse(node.args[0]) if node.args else ""
            for node in ast.walk(self.function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and ast.unparse(node.func.value) == "request.args"
        ])

    def test_without_qr_access_no_rows_are_returned(self):
        guard = next(
            node for node in ast.walk(self.function)
            if isinstance(node, ast.If) and ast.unparse(node.test) == "not granted"
        )
        payload = next(
            node for node in ast.walk(guard)
            if isinstance(node, ast.Dict)
            and any(isinstance(key, ast.Constant) and key.value == "rows" for key in node.keys)
        )
        rows_value = payload.values[
            next(i for i, key in enumerate(payload.keys) if getattr(key, "value", None) == "rows")
        ]
        self.assertEqual(ast.unparse(rows_value), "[]")
        # Гейт стоит ДО обращения к данным.
        guard_line = guard.lineno
        fetch_line = next(
            node.lineno for node in ast.walk(self.function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "list_operator_low_rating_reviews"
        )
        self.assertLess(guard_line, fetch_line)

    def test_access_is_required_for_operator_role(self):
        self.assertIn("_is_sensitive_access_unlocked(requester_id, session_id)", self.source)
        self.assertIn("role == 'operator'", self.source)


class OperatorLowRatingItemTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.build = _load_operator_item()
        self.entry = {
            "reviewer_name": "Проверяющий ОКК",
            "status": "invalid",
            "comment": "Клиент оценил не оператора",
            "updated_at": "2026-07-31T10:00:00",
        }
        self.item = {
            "id": "abc",
            "operator_id": 190,
            "operator_name": "Оператор",
            "phone_number": "77000000000",
            "taxi_park": "Халык",
            "rated_at": "2026-07-30T09:54:55",
            "day": "2026-07-30",
            "score": 3.0,
            "raw_payload": {"client_comment": "долго отвечали", "rating_text": "3"},
            "department_id": 1,
            "department_name": "СЗоВ",
            "review_entries": [self.entry],
            "my_review_status": "valid",
            "my_review_comment": "личный вердикт проверяющего",
            "has_review_conflict": True,
            "final_status": None,
        }

    def test_pending_rating_hides_reviewer_votes(self):
        result = self.build(self.item)
        self.assertEqual(result["state"], "pending")
        self.assertEqual(result["decisions"], [])

    def test_resolved_rating_shows_every_decision(self):
        self.item["final_status"] = "invalid"
        self.item["final_source"] = "consensus"
        result = self.build(self.item)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(result["final_status"], "invalid")
        self.assertEqual(
            result["decisions"],
            [{
                "reviewer_name": "Проверяющий ОКК",
                "status": "invalid",
                "comment": "Клиент оценил не оператора",
                "updated_at": "2026-07-31T10:00:00",
            }],
        )
        self.assertEqual(result["client_comment"], "долго отвечали")

    def test_reviewer_side_fields_never_leak(self):
        self.item["final_status"] = "valid"
        result = self.build(self.item)
        for forbidden in (
            "my_review_status", "my_review_comment", "has_review_conflict",
            "can_finalize", "review_entries", "department_id", "department_name",
            "operator_id", "operator_name", "review_count",
        ):
            self.assertNotIn(forbidden, result)


if __name__ == "__main__":
    unittest.main()
