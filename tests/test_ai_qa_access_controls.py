from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AiQaAccessControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
        cls.api_source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")

    def test_frontend_allows_super_admin_and_moldir_user_id(self):
        self.assertIn("const AI_QA_EXTRA_ACCESS_USER_IDS = new Set([183]);", self.app_source)
        self.assertIn("normalizeRole(userLike?.role) === 'super_admin'", self.app_source)
        self.assertIn("AI_QA_EXTRA_ACCESS_USER_IDS.has(Number(userLike?.id))", self.app_source)
        self.assertIn("const canAccessAiQaSection = canAccessAiQaForUser(user);", self.app_source)
        self.assertIn('view === "ai_qa" && canAccessAiQaSection', self.app_source)

    def test_backend_allows_moldir_user_id(self):
        self.assertIn("AI_QA_EXTRA_ACCESS_USER_IDS = {183}", self.api_source)
        self.assertIn("int(requester_id) in AI_QA_EXTRA_ACCESS_USER_IDS", self.api_source)
        self.assertIn("if _is_super_admin_role(role):", self.api_source)


if __name__ == "__main__":
    unittest.main()
