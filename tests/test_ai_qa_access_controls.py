from pathlib import Path
import ast
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load_function(source, function_name, namespace):
    tree = ast.parse(source)
    node = next(
        item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == function_name
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "<ai-qa-access>", "exec"), namespace)
    return namespace[function_name]


class _DepartmentDb:
    def __init__(self, departments):
        self.departments = departments

    def get_department_by_id(self, department_id):
        return self.departments.get(int(department_id))


class AiQaAccessControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
        cls.api_source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        cls.call_qa_view_source = (
            ROOT / "src" / "components" / "call_qa" / "CallQaView.jsx"
        ).read_text(encoding="utf-8-sig")
        cls.database_source = (ROOT / "database.py").read_text(encoding="utf-8-sig")

    def test_frontend_allows_super_admin_and_moldir_user_id(self):
        self.assertIn("const AI_QA_EXTRA_ACCESS_USER_IDS = new Set([183]);", self.app_source)
        self.assertIn("normalizeRole(userLike?.role) === 'super_admin'", self.app_source)
        self.assertIn("AI_QA_EXTRA_ACCESS_USER_IDS.has(Number(userLike?.id))", self.app_source)
        self.assertIn("const canAccessAiQaSection = canAccessAiQaForUser(user);", self.app_source)
        self.assertIn('view === "ai_qa" && canAccessAiQaSection', self.app_source)

    def test_frontend_allows_szov_department_head_for_ai_qa_and_verifier_chats(self):
        self.assertIn("const AI_QA_HEAD_DEPARTMENT_CODES = new Set(['op', 'szov']);", self.app_source)
        self.assertIn("const isAiQaDepartmentHead = (userLike) => (", self.app_source)
        self.assertIn("userLike?.headed_department_codes ?? userLike?.headedDepartmentCodes", self.app_source)
        self.assertIn("isAiQaDepartmentHead(userLike) ||", self.app_source)
        self.assertIn('(isAiQaDepartmentHead(user) || isOpSalesSupervisorForAiQa(user)) && (', self.app_source)
        self.assertIn("requestedViewFromUrl !== 'wazzup_chats' || canAccessAiQaSection", self.app_source)
        self.assertIn('view === "wazzup_chats" && canAccessAiQaSection', self.app_source)

    def test_backend_allows_moldir_user_id(self):
        self.assertIn("AI_QA_EXTRA_ACCESS_USER_IDS = {183}", self.api_source)
        self.assertIn("int(requester_id) in AI_QA_EXTRA_ACCESS_USER_IDS", self.api_source)
        self.assertIn("if _is_super_admin_role(role):", self.api_source)

    def test_backend_recognizes_op_and_szov_department_heads(self):
        departments = {
            367: {"id": 367, "code": "op"},
            501: {"id": 501, "code": "SZoV"},
            777: {"id": 777, "code": "tez"},
        }
        headed_by_user = {10: 367, 20: 501, 30: 777, 50: 777}
        all_headed_by_user = {10: {367}, 20: {501}, 30: {777}, 50: {777, 501}}
        fn = _load_function(
            self.api_source,
            "_is_ai_qa_department_head",
            {
                "db": _DepartmentDb(departments),
                "_headed_department_id": lambda user_id: headed_by_user.get(user_id),
                "_headed_department_ids": lambda user_id: frozenset(all_headed_by_user.get(user_id, set())),
                "AI_QA_OP_DEPARTMENT_ID": 367,
                "AI_QA_HEAD_DEPARTMENT_CODES": frozenset({"op", "szov"}),
            },
        )

        self.assertTrue(fn(10))
        self.assertTrue(fn(20))
        self.assertFalse(fn(30))
        self.assertFalse(fn(40))
        self.assertTrue(fn(50), "Access must consider every formally headed department")

    def test_user_payload_exposes_formal_head_department_codes(self):
        self.assertIn('"headed_department_code": headed_department_code', self.api_source)
        self.assertIn('"headed_department_codes": headed_department_codes', self.api_source)
        self.assertIn("SELECT id, name, code", self.database_source)
        self.assertIn('{"id": int(row[0]), "name": row[1] or "", "code": row[2] or ""}', self.database_source)

    def test_department_head_with_sv_base_role_keeps_full_ai_qa_tabs(self):
        self.assertIn("import { isDepartmentHead, normalizeRole }", self.call_qa_view_source)
        self.assertIn(
            "normalizeRole(user?.role) === 'sv' && !isDepartmentHead(user)",
            self.call_qa_view_source,
        )

    def test_verifier_chat_routes_share_ai_qa_access_guard(self):
        target_names = {
            "api_wazzup_channels",
            "api_wazzup_chats",
            "api_wazzup_chat_messages",
            "api_wazzup_authors",
            "api_wazzup_authors_map",
        }
        functions = {
            node.name: ast.get_source_segment(self.api_source, node)
            for node in ast.parse(self.api_source).body
            if isinstance(node, ast.FunctionDef) and node.name in target_names
        }
        for function_name in target_names:
            with self.subTest(function_name=function_name):
                self.assertIn("_ai_qa_guard()", functions[function_name])

    def test_backend_grants_full_scope_to_allowed_department_heads(self):
        guard_source = ast.get_source_segment(
            self.api_source,
            next(
                node for node in ast.parse(self.api_source).body
                if isinstance(node, ast.FunctionDef) and node.name == "_ai_qa_guard"
            ),
        )
        scope_source = ast.get_source_segment(
            self.api_source,
            next(
                node for node in ast.parse(self.api_source).body
                if isinstance(node, ast.FunctionDef) and node.name == "_ai_qa_direction_scope"
            ),
        )
        self.assertIn("if _is_ai_qa_department_head(requester_id):", guard_source)
        self.assertIn("if _is_ai_qa_department_head(requester_id):\n        return None", scope_source)

        class _AccessDb:
            @staticmethod
            def get_user(id=None):
                return (id, None, "Глава СЗоВ", "operator")

            @staticmethod
            def get_user_department_id(_user_id):
                return None

            @staticmethod
            def get_supervisor_direction_ids(*_args, **_kwargs):
                raise AssertionError("A department head must not receive supervisor scope")

        namespace = {
            "AI_QA_EXTRA_ACCESS_USER_IDS": set(),
            "AI_QA_OP_DEPARTMENT_ID": 367,
            "db": _AccessDb(),
            "g": SimpleNamespace(user_id=20),
            "jsonify": lambda payload: payload,
            "logging": SimpleNamespace(exception=lambda *_args, **_kwargs: None),
            "_normalize_user_role": lambda role: role,
            "_is_super_admin_role": lambda _role: False,
            "_is_ai_qa_department_head": lambda user_id: user_id == 20,
        }
        guard = _load_function(self.api_source, "_ai_qa_guard", namespace)
        scope = _load_function(self.api_source, "_ai_qa_direction_scope", namespace)
        self.assertEqual(guard(), (20, None))
        self.assertIsNone(scope(20))


if __name__ == "__main__":
    unittest.main()
