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
    def test_supervisor_list_full_payload_is_admin_or_department_head_only(self):
        source = _function_source("get_sv_list")

        # Полный профиль СВ: глобальный админ или глава отдела; глава при этом
        # обязан получать только СВ своих отделов (фильтр по headed_dept_ids).
        self.assertIn("is_global_admin = _is_global_admin_requester(requester_role, requester_id)", source)
        self.assertIn("include_full_profile = is_global_admin or bool(headed_dept_ids)", source)
        self.assertIn("if headed_dept_ids and not is_global_admin:", source)
        self.assertIn("if supervisor[39] is not None and int(supervisor[39]) in headed_dept_ids", source)
        self.assertIn("taxipro_id,", source)
        self.assertIn('"has_proxy": bool(sv[29]) if sv[29] is not None else False', source)
        self.assertIn('"proxy_card_number": sv[30] or ""', source)
        self.assertIn('"proxy_status": sv[31] or ""', source)
        self.assertIn('"taxipro_id": sv[35] or ""', source)

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
