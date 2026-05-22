import ast
import unittest
from pathlib import Path


BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"


def _function_source(name):
    source = BOT_PATH.read_text(encoding="utf-8-sig")
    module = ast.parse(source)
    function = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.get_source_segment(source, function)


class SupervisorDirectoryPayloadTests(unittest.TestCase):
    def test_supervisor_list_full_payload_is_admin_only(self):
        source = _function_source("get_sv_list")

        self.assertIn("include_full_profile = _is_admin_role(requester_role)", source)
        self.assertIn("taxipro_id,", source)
        self.assertIn('"has_proxy": bool(sv[28]) if sv[28] is not None else False', source)
        self.assertIn('"proxy_card_number": sv[29] or ""', source)
        self.assertIn('"proxy_status": sv[30] or ""', source)
        self.assertIn('"taxipro_id": sv[34] or ""', source)

        limited_query = source.split(
            "SELECT id, name, hours_table_url, role, hire_date, status, avatar_bucket, avatar_blob_path",
            1,
        )[1].split("sv_data = [", 1)[0]
        self.assertNotIn("taxipro_id", limited_query)

    def test_add_user_accepts_supervisor_role(self):
        source = _function_source("add_user")

        self.assertIn("role not in ('operator', 'trainee', 'trainer', 'sv', 'admin')", source)
        self.assertIn("elif role in ('trainer', 'sv')", source)
        self.assertIn("elif role == 'sv':\n            login_prefix = 'sv'", source)
        self.assertIn("if role == 'trainee' or not has_proxy:", source)


if __name__ == "__main__":
    unittest.main()
