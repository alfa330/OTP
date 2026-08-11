"""«Мои оценки» → проверки низких оценок у чат-менеджера.

Оператор видит решения ОКК только по СВОИМ низким оценкам и только после
QR-подтверждения доступа. Тесты стерегут обе границы плюс состав полей:
в ответ не должны утекать поля проверяющей стороны.
"""

import ast
import copy
import unittest
from pathlib import Path
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DB_PATH = ROOT / "database.py"


def _bot_function(name):
    module = source_cache.parse(BOT_PATH.read_text(encoding="utf-8-sig"))
    return next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _db_method(name):
    module = source_cache.parse(DB_PATH.read_text(encoding="utf-8-sig"))
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
        node = copy.deepcopy(_db_method(name))
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

    def test_rows_are_gated_by_qr_access(self):
        """Строки — только по QR: без подтверждения в ответе пустой список."""
        payload = next(
            node for node in ast.walk(self.function)
            if isinstance(node, ast.Dict)
            and any(isinstance(key, ast.Constant) and key.value == "rows" for key in node.keys)
        )
        rows_value = payload.values[
            next(i for i, key in enumerate(payload.keys) if getattr(key, "value", None) == "rows")
        ]
        self.assertIsInstance(rows_value, ast.IfExp)
        self.assertEqual(ast.unparse(rows_value.test), "granted")
        self.assertEqual(ast.unparse(rows_value.orelse), "[]")

    def test_summary_is_returned_even_without_qr(self):
        """Сводка (сколько оценок, сколько снято) показывается до сканирования QR."""
        payload = next(
            node for node in ast.walk(self.function)
            if isinstance(node, ast.Dict)
            and any(isinstance(key, ast.Constant) and key.value == "rows" for key in node.keys)
        )
        # summary приезжает распаковкой **result и ничем не перекрывается,
        # значит счётчики есть в ответе при любом granted.
        self.assertIn("**result", ast.unparse(payload))
        self.assertNotIn("summary", [getattr(key, "value", None) for key in payload.keys])
        # Ранних выходов с обнулённой сводкой в функции нет.
        self.assertNotIn("'summary': {'total': 0", self.source)

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


class LowRatingChatAccessTests(unittest.TestCase):
    """Переписку своей низкой оценки оператор открывает только по QR и только свою."""

    def setUp(self):
        self.function = _bot_function("chat_manager_low_rating_review_chat")
        self.source = ast.unparse(self.function)

    def test_non_reviewer_can_open_only_own_rating(self):
        self.assertIn("is_own_rating = int(current.get('operator_id') or 0) == int(requester_id)", self.source)
        guard = next(
            node for node in ast.walk(self.function)
            if isinstance(node, ast.If) and ast.unparse(node.test) == "not is_reviewer"
        )
        own_check = next(
            node for node in ast.walk(guard)
            if isinstance(node, ast.If) and ast.unparse(node.test) == "not is_own_rating"
        )
        self.assertIn("403", ast.unparse(own_check))

    def test_operator_chat_requires_unlocked_session(self):
        guard = next(
            node for node in ast.walk(self.function)
            if isinstance(node, ast.If) and ast.unparse(node.test) == "not is_reviewer"
        )
        self.assertIn("_is_sensitive_access_unlocked", ast.unparse(guard))
        self.assertIn("role == 'operator'", ast.unparse(guard))

    def test_bad_review_id_never_reaches_the_query(self):
        """Нечисловой id раньше падал 500-й с трейсом — теперь это 404 до похода в БД."""
        guard = next(
            node for node in ast.walk(self.function)
            if isinstance(node, ast.If) and ast.unparse(node.test) == "not _is_low_rating_review_id(review_id)"
        )
        self.assertIn("404", ast.unparse(guard))
        guard_line = guard.lineno
        fetch_line = next(
            node.lineno for node in ast.walk(self.function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get_chat_manager_low_rating_review"
        )
        self.assertLess(guard_line, fetch_line)

    def test_reviewer_department_scope_is_preserved(self):
        self.assertIn("_department_scope_id_for_requester(requester_id)", self.source)
        self.assertIn("_is_global_admin_requester(requester_role, requester_id)", self.source)


if __name__ == "__main__":
    unittest.main()
