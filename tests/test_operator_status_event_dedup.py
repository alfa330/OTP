import ast
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo


DATABASE_PATH = Path(__file__).resolve().parents[1] / "database.py"
APP_PATH = Path(__file__).resolve().parents[1] / "src" / "App.jsx"


def _database_class():
    wanted_methods = {
        "_normalize_import_status_key",
        "_status_transition_signature",
        "_canonicalize_status_transition_events",
        "_merge_adjacent_status_timeline_segments",
        "_status_import_event_kind_from_key",
        "_lock_operator_status_segments_tx",
        "append_operator_status_event",
    }
    module = ast.parse(DATABASE_PATH.read_text(encoding="utf-8-sig"))
    source_class = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    methods = [
        node for node in source_class.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted_methods
    ]
    if {node.name for node in methods} != wanted_methods:
        missing = sorted(wanted_methods - {node.name for node in methods})
        raise AssertionError(f"Missing Database methods: {missing}")

    constant = ast.parse(
        "STATUS_SEGMENT_OPERATOR_LOCK_NAMESPACE = 915904142"
    ).body[0]
    test_class = ast.ClassDef(
        name="Database",
        bases=[],
        keywords=[],
        body=[constant, *methods],
        decorator_list=[],
    )
    test_module = ast.fix_missing_locations(
        ast.Module(body=[test_class], type_ignores=[])
    )
    namespace = {
        "CHAT_MANAGER_ACTION_STATUS_KEYS": {
            "transfer chat", "take chat", "передача чата", "взятие чата"
        },
        "ZoneInfo": ZoneInfo,
        "datetime": datetime,
        "timedelta": timedelta,
    }
    exec(compile(test_module, str(DATABASE_PATH), "exec"), namespace)
    return namespace["Database"]


Database = _database_class()


class _FakeCursor:
    def __init__(self, duplicate_row=None, previous_state=None, inserted_id=101):
        self.duplicate_row = duplicate_row
        self.previous_state = previous_state
        self.inserted_id = inserted_id
        self.statements = []
        self._fetchone = None

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        self.statements.append((normalized, params))
        if "pg_advisory_xact_lock" in normalized:
            self._fetchone = None
        elif (
            "SELECT id FROM operator_status_events" in normalized
            and "client_event_id = %s" in normalized
        ):
            self._fetchone = self.duplicate_row
        elif "SELECT status_key, state_note" in normalized:
            self._fetchone = self.previous_state
        elif "INSERT INTO operator_status_events" in normalized:
            self._fetchone = (self.inserted_id,)
        else:
            raise AssertionError(f"Unexpected SQL: {normalized}")

    def fetchone(self):
        return self._fetchone


def _database_with_cursor(cursor):
    database = Database()

    @contextmanager
    def _get_cursor():
        yield cursor

    database._get_cursor = _get_cursor
    database.rebuild_calls = []
    database.auto_calls = []
    database._rebuild_operator_status_segments_tx = (
        lambda **kwargs: database.rebuild_calls.append(kwargs)
        or {"segments_saved": 1}
    )
    database._recalculate_auto_daily_hours_tx = (
        lambda **kwargs: database.auto_calls.append(kwargs)
        or {"updated": 1}
    )
    return database


class StatusEventCanonicalizationTests(unittest.TestCase):
    def setUp(self):
        self.database = Database()

    def test_same_timestamp_keeps_last_then_collapses_same_state(self):
        t0 = datetime(2026, 7, 29, 14, 23, 46)
        events = [
            {"id": 1, "event_at": t0, "status_key": "готов", "state_note": None},
            {"id": 2, "event_at": t0 + timedelta(seconds=5), "status_key": "занят", "state_note": None},
            {"id": 3, "event_at": t0 + timedelta(seconds=5), "status_key": "готов", "state_note": None},
            {"id": 4, "event_at": t0 + timedelta(seconds=9), "status_key": " Готов ", "state_note": ""},
            {"id": 5, "event_at": t0 + timedelta(seconds=20), "status_key": "занят", "state_note": None},
            {"id": 6, "event_at": t0 + timedelta(seconds=30), "status_key": "готов", "state_note": None},
        ]

        canonical = self.database._canonicalize_status_transition_events(events)

        self.assertEqual(
            [(item["id"], item["status_key"]) for item in canonical],
            [(1, "готов"), (5, "занят"), (6, "готов")],
        )

    def test_real_a_b_a_and_note_changes_are_preserved(self):
        t0 = datetime(2026, 7, 29, 10, 0, 0)
        events = [
            {"id": 1, "event_at": t0, "status_key": "готов", "state_note": "desk-1"},
            {"id": 2, "event_at": t0 + timedelta(seconds=1), "status_key": "занят", "state_note": None},
            {"id": 3, "event_at": t0 + timedelta(seconds=2), "status_key": "готов", "state_note": "desk-1"},
            {"id": 4, "event_at": t0 + timedelta(seconds=3), "status_key": "готов", "state_note": "desk-2"},
        ]

        canonical = self.database._canonicalize_status_transition_events(events)

        self.assertEqual([item["id"] for item in canonical], [1, 2, 3, 4])

    def test_read_timeline_merges_only_touching_identical_states(self):
        segments = [
            {"start": "2026-07-29T10:00:00", "end": "2026-07-29T10:01:00",
             "durationSec": 60, "stateKey": "готов", "stateNote": ""},
            {"start": "2026-07-29T10:01:00", "end": "2026-07-29T10:02:00",
             "durationSec": 60, "stateKey": " ГОТОВ ", "stateNote": None},
            {"start": "2026-07-29T10:03:00", "end": "2026-07-29T10:04:00",
             "durationSec": 60, "stateKey": "готов", "stateNote": ""},
            {"start": "2026-07-29T10:04:00", "end": "2026-07-29T10:05:00",
             "durationSec": 60, "stateKey": "готов", "stateNote": "manual"},
            {"start": "2026-07-29T10:05:00", "end": "2026-07-29T10:06:00",
             "durationSec": 60, "stateKey": "занят", "stateNote": ""},
        ]

        merged = self.database._merge_adjacent_status_timeline_segments(segments)

        self.assertEqual(len(merged), 4)
        self.assertEqual(merged[0]["end"], "2026-07-29T10:02:00")
        self.assertEqual(merged[0]["durationSec"], 120)
        self.assertEqual(merged[1]["start"], "2026-07-29T10:03:00")
        self.assertEqual(merged[2]["stateNote"], "manual")
        self.assertEqual(merged[3]["stateKey"], "занят")


class AppendStatusEventTests(unittest.TestCase):
    def test_same_client_event_id_remains_transport_duplicate(self):
        cursor = _FakeCursor(duplicate_row=(77,), previous_state=("занят", None))
        database = _database_with_cursor(cursor)

        result = database.append_operator_status_event(
            322, datetime(2026, 7, 29, 14, 25), "готов",
            client_event_id="event-1"
        )

        self.assertTrue(result["duplicate"])
        self.assertIn("pg_advisory_xact_lock", cursor.statements[0][0])
        self.assertFalse(any("SELECT status_key, state_note" in q for q, _ in cursor.statements))
        self.assertFalse(database.rebuild_calls)

    def test_new_guid_with_same_effective_state_is_semantic_noop(self):
        cursor = _FakeCursor(previous_state=("  ГОТОВ  ", "  "))
        database = _database_with_cursor(cursor)

        result = database.append_operator_status_event(
            322, datetime(2026, 7, 29, 14, 25), "готов",
            state_note=None, client_event_id="event-2"
        )

        self.assertFalse(result["duplicate"])
        self.assertTrue(result["noop"])
        semantic_sql = next(
            query for query, _ in cursor.statements
            if "SELECT status_key, state_note" in query
        )
        self.assertIn("event_at <= %s", semantic_sql)
        self.assertIn("ORDER BY event_at DESC, id DESC", semantic_sql)
        self.assertFalse(any("INSERT INTO operator_status_events" in q for q, _ in cursor.statements))
        self.assertFalse(database.rebuild_calls)

    def test_real_transition_is_inserted_and_rebuilt(self):
        cursor = _FakeCursor(previous_state=("готов", None), inserted_id=901)
        database = _database_with_cursor(cursor)

        result = database.append_operator_status_event(
            322, datetime(2026, 7, 29, 14, 25), "занят",
            client_event_id="event-3"
        )

        self.assertFalse(result["duplicate"])
        self.assertNotIn("noop", result)
        self.assertEqual(result["event_id"], 901)
        self.assertTrue(any("INSERT INTO operator_status_events" in q for q, _ in cursor.statements))
        self.assertEqual(len(database.rebuild_calls), 1)
        self.assertEqual(len(database.auto_calls), 1)

    def test_action_event_is_never_collapsed_as_status_noop(self):
        cursor = _FakeCursor(previous_state=("take chat", None), inserted_id=902)
        database = _database_with_cursor(cursor)

        result = database.append_operator_status_event(
            322, datetime(2026, 7, 29, 14, 25), "take chat",
            event_kind="action", client_event_id="event-4"
        )

        self.assertEqual(result["event_id"], 902)
        self.assertFalse(any("SELECT status_key, state_note" in q for q, _ in cursor.statements))


class OperatorStatusFrontendPrecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_PATH.read_text(encoding="utf-8-sig")

    def test_status_timeline_keeps_milliseconds(self):
        self.assertGreaterEqual(
            self.source.count("getMilliseconds() / 60000"),
            4,
        )

    def test_status_journal_shows_second_precision(self):
        self.assertIn(
            "plannerStatusFormatDuration(Number(seg?.durationSec) > 0 ? Number(seg.durationSec) : segDurationMin * 60)",
            self.source,
        )
        self.assertIn(
            "plannerStatusFormatMinuteTime(seg.startMin)",
            self.source,
        )
        self.assertIn(
            "plannerStatusFormatMinuteTime(seg.endMin)",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
