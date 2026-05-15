import ast
import unittest
from pathlib import Path


DATABASE_PATH = Path(__file__).resolve().parents[1] / "database.py"


def _database_class():
    module = ast.parse(DATABASE_PATH.read_text(encoding="utf-8-sig"))
    return next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )


def _method(name):
    return next(
        node for node in _database_class().body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


class ShiftAuctionLockingTests(unittest.TestCase):
    def test_operator_lock_uses_transaction_scoped_advisory_lock(self):
        method = _method("_lock_shift_auction_operator_tx")
        statements = [
            node for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
        ]

        self.assertEqual(len(statements), 1)
        self.assertIsInstance(statements[0].args[0], ast.Constant)
        self.assertEqual(
            statements[0].args[0].value,
            "SELECT pg_advisory_xact_lock(%s, %s)",
        )

    def test_operator_mutations_lock_before_processing(self):
        for method_name in (
            "claim_shift_auction_test_lot",
            "release_shift_auction_test_lot",
            "set_shift_auction_test_day_off",
        ):
            with self.subTest(method_name=method_name):
                method = _method(method_name)
                with_node = next(
                    node for node in method.body
                    if isinstance(node, ast.With)
                )
                first_statement = with_node.body[0]

                self.assertIsInstance(first_statement, ast.Expr)
                self.assertIsInstance(first_statement.value, ast.Call)
                self.assertIsInstance(first_statement.value.func, ast.Attribute)
                self.assertEqual(
                    first_statement.value.func.attr,
                    "_lock_shift_auction_operator_tx",
                )


if __name__ == "__main__":
    unittest.main()
