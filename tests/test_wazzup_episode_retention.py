import ast
import logging
from contextlib import contextmanager
from pathlib import Path
import unittest


DATABASE_PATH = Path(__file__).resolve().parents[1] / "database.py"


def _database_class():
    wanted = {
        "_mark_journal_evaluated_wazzup_episodes_tx",
        "cleanup_wazzup_messages",
        "cleanup_c2d_eval_data",
    }
    source = DATABASE_PATH.read_text(encoding="utf-8-sig")
    module = ast.parse(source)
    source_class = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    methods = [
        node for node in source_class.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    if {node.name for node in methods} != wanted:
        missing = sorted(wanted - {node.name for node in methods})
        raise AssertionError(f"Missing Database methods: {missing}")

    test_class = ast.ClassDef(
        name="Database",
        bases=[],
        keywords=[],
        body=methods,
        decorator_list=[],
    )
    test_module = ast.fix_missing_locations(
        ast.Module(body=[test_class], type_ignores=[])
    )
    namespace = {"logging": logging}
    exec(compile(test_module, str(DATABASE_PATH), "exec"), namespace)
    return namespace["Database"]


Database = _database_class()


class _FakeCursor:
    def __init__(self, rowcounts):
        self._rowcounts = iter(rowcounts)
        self.rowcount = 0
        self.executions = []

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        self.executions.append((normalized, params))
        self.rowcount = next(self._rowcounts)


def _database_with_cursor(cursor):
    database = Database()

    @contextmanager
    def _get_cursor():
        yield cursor

    database._get_cursor = _get_cursor
    return database


class WazzupEpisodeRetentionTests(unittest.TestCase):
    def test_cleanup_deletes_old_unprotected_episodes_with_same_horizon(self):
        cursor = _FakeCursor([3, 7, 2, 11])
        result = _database_with_cursor(cursor).cleanup_wazzup_messages(
            retention_days=30
        )

        self.assertEqual(
            result,
            {
                "messages": 7,
                "chats": 2,
                "episodes": 11,
                "journal_marked": 3,
            },
        )
        self.assertEqual(
            [params for sql, params in cursor.executions if sql.startswith("DELETE")],
            [(30,), (30,), (30,)],
        )
        episode_sql = cursor.executions[-1][0]
        self.assertIn("DELETE FROM wazzup_episodes e", episode_sql)
        self.assertIn(
            "e.ended_at < now() - make_interval(days => %s)", episode_sql
        )
        self.assertIn("e.journal_evaluated_at IS NULL", episode_sql)

    def test_successful_and_legacy_ai_results_protect_episode(self):
        cursor = _FakeCursor([0, 0, 0, 0])
        _database_with_cursor(cursor).cleanup_wazzup_messages()
        episode_sql = cursor.executions[-1][0]

        self.assertIn("FROM ai_evaluation_runs r", episode_sql)
        self.assertIn("r.subject_kind = 'wz_episode'", episode_sql)
        self.assertIn("r.call_id = e.id", episode_sql)
        self.assertIn("r.status = 'succeeded'", episode_sql)
        self.assertIn("FROM ai_evaluation_meta m", episode_sql)
        self.assertIn("m.subject_kind = 'wz_episode'", episode_sql)
        self.assertIn("FROM ai_review_cache rc", episode_sql)
        self.assertIn("rc.subject_kind = 'wz_episode'", episode_sql)
        self.assertNotIn("ai_transcript_cache", episode_sql)

    def test_final_journal_evaluation_gets_durable_marker(self):
        cursor = _FakeCursor([5])

        marked = Database._mark_journal_evaluated_wazzup_episodes_tx(cursor)

        self.assertEqual(marked, 5)
        marker_sql = cursor.executions[0][0]
        self.assertIn("UPDATE wazzup_episodes e", marker_sql)
        self.assertIn("SET journal_evaluated_at = CURRENT_TIMESTAMP", marker_sql)
        self.assertIn("JOIN calls c ON c.c2d_snapshot_id = s.id", marker_sql)
        self.assertIn("s.source = 'wazzup'", marker_sql)
        self.assertIn("COALESCE(c.is_draft, FALSE) = FALSE", marker_sql)
        self.assertIn("e.channel_id = evaluated.wz_channel_id", marker_sql)
        self.assertIn("e.chat_id = evaluated.wz_chat_id", marker_sql)
        self.assertIn("e.started_at = evaluated.episode_start", marker_sql)

    def test_live_draft_temporarily_protects_episode_but_is_not_marked(self):
        cursor = _FakeCursor([0, 0, 0, 0])
        _database_with_cursor(cursor).cleanup_wazzup_messages()

        marker_sql = cursor.executions[0][0]
        episode_sql = cursor.executions[-1][0]
        self.assertIn("COALESCE(c.is_draft, FALSE) = FALSE", marker_sql)
        self.assertIn("JOIN calls c ON c.c2d_snapshot_id = s.id", episode_sql)
        self.assertNotIn("COALESCE(c.is_draft, FALSE) = FALSE", episode_sql)
        self.assertIn("s.wz_channel_id = e.channel_id", episode_sql)
        self.assertIn("s.wz_chat_id = e.chat_id", episode_sql)
        self.assertIn("s.episode_start = e.started_at", episode_sql)

    def test_snapshot_cleanup_marks_final_evaluation_before_deleting_link(self):
        cursor = _FakeCursor([4, 6, 8])
        result = _database_with_cursor(cursor).cleanup_c2d_eval_data(
            requests_days=40,
            snapshots_days=120,
        )

        self.assertEqual(
            result,
            {
                "requests": 6,
                "snapshots": 8,
                "wazzup_journal_marked": 4,
            },
        )
        self.assertTrue(
            cursor.executions[0][0].startswith("UPDATE wazzup_episodes e")
        )
        self.assertIn(
            "DELETE FROM c2d_chat_snapshots",
            cursor.executions[2][0],
        )
        self.assertEqual(cursor.executions[1][1], (40,))
        self.assertEqual(cursor.executions[2][1], (120,))

    def test_schema_has_durable_marker_and_snapshot_lookup_index(self):
        source = DATABASE_PATH.read_text(encoding="utf-8-sig")

        self.assertIn(
            "ADD COLUMN IF NOT EXISTS journal_evaluated_at TIMESTAMPTZ",
            source,
        )
        self.assertIn(
            "CREATE INDEX IF NOT EXISTS idx_calls_c2d_snapshot",
            source,
        )
        self.assertIn(
            "WHERE c2d_snapshot_id IS NOT NULL",
            source,
        )


if __name__ == "__main__":
    unittest.main()
