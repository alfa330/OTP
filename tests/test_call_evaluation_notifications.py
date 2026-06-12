import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"
MAIN_JSX_PATH = ROOT / "src" / "call_evaluation" / "main.jsx"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


class CallEvaluationNotificationSchemaTests(unittest.TestCase):
    def test_admin_profiles_has_call_evaluation_notification_fields(self):
        source = _read(DATABASE_PATH)
        self.assertIn("call_evaluation_notify_enabled BOOLEAN NOT NULL DEFAULT FALSE", source)
        self.assertIn("call_evaluation_notify_department_id INTEGER REFERENCES departments(id)", source)
        self.assertIn("idx_admin_profiles_call_eval_notify_dept", source)

    def test_recipients_are_scoped_by_operator_department(self):
        source = _read(DATABASE_PATH)
        module = ast.parse(source)
        func = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "Database"
        )
        method = next(
            node for node in func.body
            if isinstance(node, ast.FunctionDef) and node.name == "get_call_evaluation_notify_recipients"
        )
        body = ast.get_source_segment(source, method)
        self.assertIn("SELECT department_id FROM users WHERE id = %s", body)
        self.assertIn("ap.call_evaluation_notify_department_id = %s", body)
        self.assertIn("d.head_user_id = u.id", body)


class CallEvaluationNotificationBackendTests(unittest.TestCase):
    def test_settings_endpoint_exists(self):
        source = _read(BOT_PATH)
        self.assertIn("@app.route('/api/call_evaluation/notification_settings', methods=['GET', 'PUT', 'OPTIONS'])", source)
        self.assertIn("def call_evaluation_notification_settings():", source)
        self.assertIn("Only admins and department heads", source)

    def test_send_evaluation_notification_uses_subscribers_not_admin_id(self):
        source = _read(BOT_PATH)
        module = ast.parse(source)
        func = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "send_telegram_notification"
        )
        body = ast.get_source_segment(source, func)
        self.assertIn("get_call_evaluation_notify_recipients(operator_id)", body)
        self.assertNotIn("os.getenv('ADMIN_ID')", body)
        self.assertIn("for recipient in recipients", body)

    def test_reevaluation_requests_support_department_scope(self):
        source = _read(BOT_PATH)
        self.assertIn("department_id=department_id", source)
        self.assertIn("is_department_head", source)
        self.assertIn("Only admins or department heads can decide reevaluation requests", source)
        self.assertIn("Only admins or this department head can decide reevaluation requests", source)
        self.assertIn("LEFT JOIN departments d ON d.head_user_id = u.id", source)


class CallEvaluationNotificationFrontendTests(unittest.TestCase):
    def test_journal_has_notification_control(self):
        source = _read(MAIN_JSX_PATH)
        self.assertIn("/api/call_evaluation/notification_settings", source)
        self.assertIn("canManageEvaluationNotifications", source)
        self.assertIn("evaluation-notify-control", source)
        self.assertIn("Получать уведомления", source)


if __name__ == "__main__":
    unittest.main()
