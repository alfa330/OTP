"""Удаление неоценённого импортированного звонка должно чистить и запись в GCS.

Раньше `DELETE /api/call_evaluations/<id>` стирал только строку в `imported_calls`,
а mp3 навсегда оставался в бакете без единой ссылки из БД. При этом путь может быть
общим: `add_call_evaluation` наследует `imported_calls.audio_path` в `calls.audio_path`,
поэтому блоб можно удалять только когда на него больше никто не ссылается.
"""

import ast
import copy
import logging
import unittest
from functools import lru_cache
from pathlib import Path
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"

AUDIO_PATH = "my-app-audio-uploads/Uploads/oktell-1234.mp3"


@lru_cache(maxsize=None)
def _source():
    return BOT_PATH.read_text(encoding="utf-8-sig")


@lru_cache(maxsize=None)
def _tree():
    return source_cache.parse(_source(), filename=str(BOT_PATH))


def _load_bot_functions(names, globals_dict):
    """Грузит функции из bot_schedule2 в общий namespace, срезая декораторы Flask."""
    nodes = []
    for name in names:
        node = copy.deepcopy(next(
            item for item in _tree().body
            if isinstance(item, ast.FunctionDef) and item.name == name
        ))
        node.decorator_list = []
        nodes.append(node)
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), globals_dict)
    return globals_dict


class _FakeBlob:
    def __init__(self, ref, events, fail=False):
        self.ref = ref
        self.events = events
        self.fail = fail

    def delete(self):
        if self.fail:
            raise RuntimeError("GCS unavailable")
        self.events.append(f"blob-delete:{self.ref}")


class _FakeBucket:
    def __init__(self, name, events, fail=False):
        self.name = name
        self.events = events
        self.fail = fail

    def blob(self, blob_path):
        return _FakeBlob(f"{self.name}/{blob_path}", self.events, fail=self.fail)


class _FakeGcsClient:
    def __init__(self, events, fail=False):
        self.events = events
        self.fail = fail

    def bucket(self, bucket_name):
        return _FakeBucket(bucket_name, self.events, fail=self.fail)


class _FakeCursor:
    def __init__(self, imported_row, events, calls_ref=False, imported_ref=False):
        self.imported_row = imported_row
        self.events = events
        self.calls_ref = calls_ref
        self.imported_ref = imported_ref
        self.executions = []
        self._result = None

    def execute(self, query, params=None):
        normalized = " ".join(query.split())
        self.executions.append((normalized, params))
        if normalized.startswith("SELECT operator_id") and "FROM imported_calls" in normalized:
            self._result = self.imported_row
        elif normalized.startswith("DELETE FROM imported_calls"):
            self.events.append("row-delete")
            self._result = None
        elif normalized.startswith("SELECT 1 FROM calls WHERE audio_path"):
            self._result = (1,) if self.calls_ref else None
        elif normalized.startswith("SELECT 1 FROM imported_calls WHERE audio_path"):
            self._result = (1,) if self.imported_ref else None
        else:
            raise AssertionError(f"Unexpected query: {normalized}")

    def fetchone(self):
        return self._result


class _CursorContext:
    def __init__(self, cursor, events):
        self.cursor = cursor
        self.events = events

    def __enter__(self):
        return self.cursor

    def __exit__(self, exc_type, exc_value, traceback):
        self.events.append("commit")
        return False


def _call_delete(status="not_evaluated", audio_path=AUDIO_PATH,
                 calls_ref=False, imported_ref=False, gcs_fails=False):
    events = []
    cursor = _FakeCursor((7, status, audio_path), events,
                         calls_ref=calls_ref, imported_ref=imported_ref)

    class FakeDb:
        def get_user(self, id=None):
            return (id, "Админ", None, "admin")

        def _get_cursor(self):
            return _CursorContext(cursor, events)

    class FakeRequest:
        headers = {"X-User-Id": "1"}

    namespace = {
        "logging": logging,
        "db": FakeDb(),
        "request": FakeRequest(),
        "jsonify": lambda payload: payload,
        "get_gcs_client": lambda: _FakeGcsClient(events, fail=gcs_fails),
        "_normalize_user_role": lambda role: role,
        "_is_admin_role": lambda role: role == "admin",
        "_headed_department_id": lambda _user_id: None,
        "_ensure_call_access_for_requester": lambda *_args: True,
    }
    _load_bot_functions(["delete_draft_evaluation", "_delete_call_record_blob"], namespace)
    response = namespace["delete_draft_evaluation"](42)
    return response, events, cursor


class ImportedCallDeleteAudioCleanupTests(unittest.TestCase):
    def test_orphan_record_is_removed_from_gcs(self):
        response, events, cursor = _call_delete()

        self.assertEqual(response, ({"status": "success", "message": "Imported call deleted"}, 200))
        self.assertIn(f"blob-delete:{AUDIO_PATH}", events)
        # audio_path обязан попасть в выборку — иначе чистить нечего.
        self.assertIn("audio_path", cursor.executions[0][0])

    def test_blob_is_deleted_only_after_the_row_delete_is_committed(self):
        _response, events, _cursor = _call_delete()

        self.assertEqual(
            events,
            ["row-delete", "commit", f"blob-delete:{AUDIO_PATH}"],
        )

    def test_record_shared_with_an_evaluation_is_kept(self):
        # calls.audio_path наследуется от импортированного звонка: удалить блоб
        # значит оставить оценку без записи.
        _response, events, _cursor = _call_delete(calls_ref=True)

        self.assertNotIn(f"blob-delete:{AUDIO_PATH}", events)
        self.assertIn("row-delete", events)

    def test_record_shared_with_another_imported_call_is_kept(self):
        _response, events, _cursor = _call_delete(imported_ref=True)

        self.assertNotIn(f"blob-delete:{AUDIO_PATH}", events)

    def test_call_without_stored_record_touches_no_bucket(self):
        _response, events, cursor = _call_delete(audio_path=None)

        self.assertEqual(events, ["row-delete", "commit"])
        self.assertTrue(all(
            not query.startswith("SELECT 1 FROM") for query, _params in cursor.executions
        ))

    def test_evaluated_call_is_neither_deleted_nor_stripped_of_audio(self):
        response, events, _cursor = _call_delete(status="evaluated")

        self.assertEqual(response, ({"error": "Cannot delete evaluated imported call"}, 400))
        self.assertNotIn("row-delete", events)
        self.assertNotIn(f"blob-delete:{AUDIO_PATH}", events)

    def test_gcs_failure_does_not_fail_the_request(self):
        response, events, _cursor = _call_delete(gcs_fails=True)

        self.assertEqual(response, ({"status": "success", "message": "Imported call deleted"}, 200))
        self.assertIn("row-delete", events)


class DeleteCallRecordBlobTests(unittest.TestCase):
    def _load_helper(self, events, fail=False):
        namespace = {
            "logging": logging,
            "get_gcs_client": lambda: _FakeGcsClient(events, fail=fail),
        }
        _load_bot_functions(["_delete_call_record_blob"], namespace)
        return namespace["_delete_call_record_blob"]

    def test_bucket_and_blob_are_split_on_the_first_slash(self):
        events = []
        helper = self._load_helper(events)

        self.assertTrue(helper("my-bucket/Uploads/nested/name.mp3"))
        self.assertEqual(events, ["blob-delete:my-bucket/Uploads/nested/name.mp3"])

    def test_malformed_paths_are_skipped_without_touching_gcs(self):
        for path in (None, "", "   ", "bucket-only", "/leading-slash"):
            events = []
            helper = self._load_helper(events)
            with self.subTest(path=path):
                self.assertFalse(helper(path))
                self.assertEqual(events, [])

    def test_gcs_error_is_swallowed(self):
        events = []
        helper = self._load_helper(events, fail=True)

        self.assertFalse(helper(AUDIO_PATH))
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
