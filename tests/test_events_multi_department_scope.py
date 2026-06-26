import ast
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DATABASE_PATH = ROOT / "database.py"
EVENTS_VIEW_PATH = ROOT / "src" / "components" / "events" / "EventsView.jsx"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _function_source(path, function_name, class_name=None):
    source = _read(path)
    module = ast.parse(source)
    body = module.body
    if class_name:
        class_node = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        body = class_node.body
    function_node = next(
        node for node in body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return textwrap.dedent(ast.get_source_segment(source, function_node))


class EventsMultiDepartmentScopeTests(unittest.TestCase):
    def test_database_schema_has_event_departments_join_table_and_backfill(self):
        source = _read(DATABASE_PATH)

        self.assertIn("CREATE TABLE IF NOT EXISTS event_departments", source)
        self.assertIn("PRIMARY KEY (event_id, department_id)", source)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_event_departments_dept", source)
        self.assertIn("INSERT INTO event_departments (event_id, department_id)", source)
        self.assertIn("SELECT id, department_id FROM events WHERE department_id IS NOT NULL", source)

    def test_database_visibility_uses_join_table_for_feed_and_unread_count(self):
        list_events = _function_source(DATABASE_PATH, "list_events", class_name="Database")
        count_unread = _function_source(DATABASE_PATH, "count_unread_events", class_name="Database")

        for source in (list_events, count_unread):
            self.assertIn("NOT EXISTS (SELECT 1 FROM event_departments ed WHERE ed.event_id = e.id)", source)
            self.assertIn("e.department_id IS NULL OR e.department_id = %s", source)
            self.assertIn(
                "EXISTS (SELECT 1 FROM event_departments ed WHERE ed.event_id = e.id AND ed.department_id = %s)",
                source,
            )

    def test_database_payload_and_permissions_fallback_to_legacy_department(self):
        aggregate_source = _function_source(DATABASE_PATH, "_attach_event_aggregates", class_name="Database")
        author_source = _function_source(DATABASE_PATH, "get_event_author_and_departments", class_name="Database")

        self.assertIn('ev.get("_legacy_department_id") is not None', aggregate_source)
        self.assertIn('ev["department_ids"].append(int(ev["_legacy_department_id"]))', aggregate_source)
        self.assertIn("SELECT author_id, department_id FROM events WHERE id = %s", author_source)
        self.assertIn("if not dept_ids and legacy_department_id is not None", author_source)

    def test_database_create_event_writes_all_target_departments(self):
        source = _function_source(DATABASE_PATH, "create_event", class_name="Database")

        self.assertIn("def create_event(self, author_id, title, body, department_ids=None):", source)
        self.assertIn("legacy_department_id = dept_ids[0] if len(dept_ids) == 1 else None", source)
        self.assertIn("INSERT INTO events (author_id, department_id, title, body)", source)
        self.assertIn("INSERT INTO event_departments (event_id, department_id)", source)
        self.assertIn("ON CONFLICT DO NOTHING", source)

    def test_backend_create_route_accepts_array_contract_and_legacy_field(self):
        source = _function_source(BOT_PATH, "api_events")

        self.assertIn("request.form.get('department_ids')", source)
        self.assertIn("request.form.get('department_id')", source)
        self.assertIn("_events_resolve_target_departments(", source)
        self.assertIn("db.create_event(requester_id, title, body, target_dept_ids)", source)

    def test_backend_payload_keeps_new_arrays_and_legacy_single_fields(self):
        source = _function_source(BOT_PATH, "_events_event_payload")

        self.assertIn('"department_ids": department_ids', source)
        self.assertIn('"department_names": department_names', source)
        self.assertIn('"department_id": department_ids[0] if department_ids else None', source)
        self.assertIn('"department_name": legacy_department_name', source)

    def test_frontend_composer_sends_department_ids_array(self):
        source = _read(EVENTS_VIEW_PATH)

        self.assertIn("const DepartmentMultiSelect", source)
        self.assertIn("const [selectedDeptIds, setSelectedDeptIds]", source)
        self.assertIn("form.append('department_ids', JSON.stringify(selectedDeptIds))", source)
        self.assertNotIn("import CustomSelect", source)


if __name__ == "__main__":
    unittest.main()
