import unittest
from unittest.mock import patch

from call_qa import api
from call_qa.evaluation import criterion_config as cc


class PgError(RuntimeError):
    def __init__(self, pgcode):
        super().__init__(pgcode)
        self.pgcode = pgcode


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        compact = " ".join(sql.split())
        self.connection.executed.append((compact, params))
        if "SELECT criterion_idx,criterion_id" in compact:
            if self.connection.stable_error:
                raise self.connection.stable_error
            self.rows = list(self.connection.stable_rows)
        elif "SELECT criterion_idx,eval_source" in compact:
            self.rows = list(self.connection.legacy_rows)
        else:
            self.rows = []

    def executemany(self, sql, values):
        compact = " ".join(sql.split())
        values = list(values)
        self.connection.executemany_calls.append((compact, values))
        if self.connection.executemany_error:
            raise self.connection.executemany_error

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, *, stable_rows=(), legacy_rows=(), stable_error=None,
                 executemany_error=None):
        self.stable_rows = stable_rows
        self.legacy_rows = legacy_rows
        self.stable_error = stable_error
        self.executemany_error = executemany_error
        self.executed = []
        self.executemany_calls = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class CriterionConfigPersistenceTests(unittest.TestCase):
    def test_atomic_reorder_preserves_metadata_by_stable_identity(self):
        conn = FakeConnection(stable_rows=[
            (0, "greeting", "transcript", "Correct", "greeting note"),
            (1, "payment", "system_api", "Pending", "payment note"),
        ])

        saved = cc.replace_config(72, 901, [
            {"criterion_idx": 0, "criterion_id": "payment", "eval_source": "manual"},
            {"criterion_idx": 1, "criterion_id": "greeting", "eval_source": "transcript"},
        ], conn=conn)

        self.assertEqual(saved, 2)
        self.assertEqual(conn.rollbacks, 0)
        self.assertEqual(len(conn.executemany_calls), 1)
        _, values = conn.executemany_calls[0]
        self.assertEqual(values, [
            (72, 0, "payment", 901, "manual", "Pending", "payment note"),
            (72, 1, "greeting", 901, "transcript", "Correct", "greeting note"),
        ])
        deletes = [params for sql, params in conn.executed
                   if sql.startswith("DELETE FROM criterion_config")]
        self.assertEqual(deletes, [(72,)])

    def test_constraint_failure_does_not_fall_back_to_legacy_write(self):
        conn = FakeConnection(executemany_error=PgError("23505"))

        with self.assertRaises(PgError):
            cc.replace_config(72, 901, [
                {"criterion_idx": 0, "criterion_id": "greeting",
                 "eval_source": "transcript"},
            ], conn=conn)

        self.assertEqual(conn.rollbacks, 0)
        self.assertEqual(len(conn.executemany_calls), 1)

    def test_undefined_column_is_the_only_legacy_write_fallback(self):
        conn = FakeConnection(
            stable_error=PgError("42703"),
            legacy_rows=[(0, "transcript", "Correct", "legacy note")],
        )

        cc.replace_config(72, 901, [
            {"criterion_idx": 0, "criterion_id": "greeting", "eval_source": "manual"},
        ], conn=conn)

        self.assertEqual(conn.rollbacks, 1)
        self.assertEqual(len(conn.executemany_calls), 1)
        sql, values = conn.executemany_calls[0]
        self.assertNotIn("scale_revision_id", sql)
        self.assertIn("direction_id,criterion_idx,eval_source", sql)
        self.assertEqual(values, [(72, 0, "manual", "Correct", "legacy note")])

    @patch.object(cc, "_read_rows")
    def test_load_config_propagates_non_schema_errors(self, read_rows):
        read_rows.side_effect = PgError("08006")
        with self.assertRaises(PgError):
            cc.load_config(72)


class CriteriaConfigApiTests(unittest.TestCase):
    @patch("call_qa.rag.knowledge.sync_scale_revision", return_value=901)
    @patch.object(cc, "replace_config", return_value=2)
    @patch.object(cc, "apply_to_direction")
    @patch.object(api.criteria_mod, "load_direction")
    @patch.object(api.config, "connect_rw")
    def test_api_replaces_complete_current_scale_with_revision(
            self, connect_rw, load_direction, apply_to_direction, replace_config,
            sync_scale_revision):
        conn = FakeConnection()
        connect_rw.return_value = conn
        direction = {
            "id": 72,
            "name": "Продажи",
            "scale_hash": "a" * 64,
            "criteria": [
                {"idx": 0, "criterion_id": "payment", "name": "Оплата",
                 "eval_source": "system_api"},
                {"idx": 1, "criterion_id": "greeting", "name": "Приветствие",
                 "eval_source": "transcript"},
            ],
        }
        load_direction.return_value = direction
        apply_to_direction.side_effect = lambda value: value

        saved = api.criteria_config_set(72, [
            {"criterion_idx": 0, "criterion_id": "payment", "eval_source": "manual"},
        ])

        self.assertEqual(saved, 1)
        self.assertEqual(conn.commits, 1)
        self.assertTrue(conn.closed)
        sync_scale_revision.assert_called_once()
        replace_config.assert_called_once_with(72, 901, [
            {"criterion_idx": 0, "criterion_id": "payment", "eval_source": "manual"},
            {"criterion_idx": 1, "criterion_id": "greeting", "eval_source": "transcript"},
        ], conn=conn)


if __name__ == "__main__":
    unittest.main()
