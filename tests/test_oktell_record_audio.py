import ast
import logging
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlparse


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
FRONTEND_PATH = ROOT / "src" / "call_evaluation" / "main.jsx"
CONN_ID = "2b1f8608-1dc7-4059-945a-043b6d3aad9c"


class FakeResponse:
    def __init__(self, status_code=200, content=b"", content_type="audio/mpeg", text=""):
        self.status_code = status_code
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.text = text


class FakeRequests:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class FakeDb:
    def __init__(self):
        self.updated = []

    def set_imported_call_audio_path(self, imported_id, audio_path):
        self.updated.append((imported_id, audio_path))


def _oktell_audio_namespace(response=None):
    wanted = {
        "_oktell_normalize_conn_id",
        "_oktell_record_url",
        "_oktell_download_record",
        "_oktell_fetch_record_to_gcs",
        "_oktell_store_record",
        "_oktell_prepare_distribution_audio",
    }
    module = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    selected = [
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    fake_requests = FakeRequests(response or FakeResponse())
    fake_db = FakeDb()
    ns = {
        "uuid": uuid,
        "quote": quote,
        "urlparse": urlparse,
        "logging": logging,
        "threading": threading,
        "time": time,
        "ThreadPoolExecutor": ThreadPoolExecutor,
        "requests": fake_requests,
        "db": fake_db,
        "OKTELL_API_URL": "http://oktell-proxy.test:8085/query",
        "OKTELL_API_TOKEN": "secret-token",
        "OKTELL_API_TIMEOUT_SECONDS": 60,
        "OKTELL_AUDIO_FETCH_WORKERS": 4,
        "_oktell_api_ready": lambda: True,
        "_upload_external_record_to_gcs": lambda *_args, **_kwargs: "bucket/Uploads/test.mp3",
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"), ns)
    ns["_fake_requests"] = fake_requests
    ns["_fake_db"] = fake_db
    return ns


class OktellRecordAudioTests(unittest.TestCase):
    def test_record_url_is_derived_from_query_url(self):
        ns = _oktell_audio_namespace()
        self.assertEqual(
            ns["_oktell_record_url"](CONN_ID),
            f"http://oktell-proxy.test:8085/record/{CONN_ID}",
        )

    def test_download_uses_api_key_and_accepts_mp3(self):
        audio = b"\xff\xe3" + b"\x00" * 32
        ns = _oktell_audio_namespace(FakeResponse(content=audio))

        self.assertEqual(ns["_oktell_download_record"](CONN_ID), (audio, "audio/mpeg"))
        url, kwargs = ns["_fake_requests"].calls[0]
        self.assertEqual(url, f"http://oktell-proxy.test:8085/record/{CONN_ID}")
        self.assertEqual(kwargs["headers"], {"X-API-Key": "secret-token"})
        self.assertEqual(kwargs["timeout"], 60)

    def test_download_returns_none_for_missing_record(self):
        ns = _oktell_audio_namespace(FakeResponse(status_code=404))
        self.assertIsNone(ns["_oktell_download_record"](CONN_ID))

    def test_download_rejects_non_audio_response(self):
        response = FakeResponse(content=b'{"status":"ok"}', content_type="application/json")
        ns = _oktell_audio_namespace(response)
        with self.assertRaisesRegex(RuntimeError, "application/json"):
            ns["_oktell_download_record"](CONN_ID)

    def test_fetch_uses_stable_gcs_filename_and_store_updates_row(self):
        ns = _oktell_audio_namespace()
        captured = {}
        ns["_oktell_download_record"] = lambda _conn_id: (b"\xff\xe3\x00", "audio/mpeg")

        def upload(audio_bytes, filename, content_type):
            captured.update(
                audio_bytes=audio_bytes,
                filename=filename,
                content_type=content_type,
            )
            return "bucket/Uploads/oktell.mp3"

        ns["_upload_external_record_to_gcs"] = upload
        self.assertEqual(
            ns["_oktell_store_record"](17, CONN_ID),
            "bucket/Uploads/oktell.mp3",
        )
        self.assertEqual(captured["filename"], f"oktell-{CONN_ID}.mp3")
        self.assertEqual(captured["content_type"], "audio/mpeg")
        self.assertEqual(
            ns["_fake_db"].updated,
            [(17, "bucket/Uploads/oktell.mp3")],
        )

    def test_random_call_requires_audio_before_import(self):
        source = BOT_PATH.read_text(encoding="utf-8")
        start = source.index("def fetch_random_evaluation_call(")
        end = source.index("\n@app.route('/api/audio/", start)
        endpoint = source[start:end]
        fetch_pos = endpoint.index("_oktell_fetch_record_to_gcs(conn_id)")
        import_pos = endpoint.index("db.import_single_random_call(", fetch_pos)

        self.assertLess(fetch_pos, import_pos)
        self.assertIn('notes=f"random:{requester_id}:oktell"', endpoint)
        self.assertIn("audio_path=audio_path", endpoint)
        self.assertIn('"audio_pending": False', endpoint)

    def test_old_imports_can_fetch_oktell_audio_on_demand(self):
        source = BOT_PATH.read_text(encoding="utf-8")
        start = source.index("def get_imported_call_audio_file(")
        end = source.index("\n@app.route('/api/admin/shuffle'", start)
        endpoint = source[start:end]
        self.assertIn("_oktell_store_record(imported_id, str(ext_id))", endpoint)
        self.assertIn("OKTELL_CALL_DISTRIBUTION_DEPARTMENT_CODE", endpoint)

        frontend = FRONTEND_PATH.read_text(encoding="utf-8")
        effect_start = frontend.index("// Для старых импортов audio_path")
        effect = frontend[effect_start:effect_start + 900]
        self.assertNotIn("!existingEvaluation?.audio_path", effect)
        self.assertIn("getImportedAudioUrl(existingEvaluation.id, userId)", effect)

    def test_attached_import_audio_hides_upload_and_duration_check(self):
        frontend = FRONTEND_PATH.read_text(encoding="utf-8")
        self.assertIn("const hasAttachedImportedAudio = !!(", frontend)
        self.assertIn(
            "!existingEvaluation?.isReevaluation && !hasAttachedImportedAudio",
            frontend,
        )
        self.assertIn(
            "!hasAttachedImportedAudio && (expectedDuration || actualDuration)",
            frontend,
        )
        self.assertIn("(!hasAttachedImportedAudio && durationMismatch)", frontend)

    def test_distribution_keeps_only_calls_with_ready_audio(self):
        ns = _oktell_audio_namespace()
        ready_id = CONN_ID
        missing_id = "5026d1f7-e9b9-41bd-87df-aa3f83b360d5"
        failed_id = "b1e5a329-f84c-4ad8-bee4-ccfdd9a0afcc"

        def fetch(conn_id):
            if conn_id == ready_id:
                return f"bucket/Uploads/oktell-{conn_id}.mp3"
            if conn_id == failed_id:
                raise RuntimeError("proxy unavailable")
            return None

        ns["_oktell_fetch_record_to_gcs"] = fetch
        payload = {
            "month": "2026-07",
            "distribution": [{
                "operator": "Test Operator",
                "calls": [
                    {"id": ready_id, "phone": "77010000001"},
                    {"id": missing_id, "phone": "77010000002"},
                    {"id": failed_id, "phone": "77010000003"},
                    {"id": "not-a-uuid", "phone": "77010000004"},
                ],
            }],
        }

        prepared, result = ns["_oktell_prepare_distribution_audio"](payload)
        calls = prepared["distribution"][0]["calls"]
        self.assertEqual([call["id"] for call in calls], [ready_id])
        self.assertEqual(
            calls[0]["audioPath"],
            f"bucket/Uploads/oktell-{ready_id}.mp3",
        )
        self.assertEqual(calls[0]["notes"], "distribution:oktell")
        self.assertEqual(result["requested"], 4)
        self.assertEqual(result["ready"], 1)
        self.assertEqual(result["missing"], 1)
        self.assertEqual(result["failed"], 2)

    def test_distribution_downloads_use_bounded_queue(self):
        ns = _oktell_audio_namespace()
        ns["OKTELL_AUDIO_FETCH_WORKERS"] = 2
        lock = threading.Lock()
        active = {"value": 0, "peak": 0}

        def fetch(conn_id):
            with lock:
                active["value"] += 1
                active["peak"] = max(active["peak"], active["value"])
            time.sleep(0.01)
            with lock:
                active["value"] -= 1
            return f"bucket/Uploads/oktell-{conn_id}.mp3"

        ns["_oktell_fetch_record_to_gcs"] = fetch
        call_ids = [str(uuid.UUID(int=index + 1)) for index in range(8)]
        payload = {
            "month": "2026-07",
            "distribution": [{
                "operator": "Test Operator",
                "calls": [{"id": conn_id} for conn_id in call_ids],
            }],
        }

        _prepared, result = ns["_oktell_prepare_distribution_audio"](payload)
        self.assertEqual(result["ready"], 8)
        self.assertLessEqual(active["peak"], 2)

    def test_distribution_database_import_persists_audio_path(self):
        source = (ROOT / "database.py").read_text(encoding="utf-8-sig")
        module = ast.parse(source)
        database_class = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "Database"
        )
        method = next(
            node for node in database_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "import_calls_from_distribution"
        )
        method_source = ast.get_source_segment(source, method)
        self.assertIn("audio_path = c.get('audioPath') or c.get('audio_path')", method_source)
        self.assertIn("audio_path = COALESCE(EXCLUDED.audio_path, imported_calls.audio_path)", method_source)

    def test_both_distribution_paths_prepare_audio(self):
        source = BOT_PATH.read_text(encoding="utf-8")
        manual_start = source.index("def shuffle_imported_calls(")
        manual_end = source.index("\n@app.route('/api/eval_calls/sync_oktell'", manual_start)
        manual = source[manual_start:manual_end]
        self.assertIn("_oktell_prepare_distribution_audio(payload)", manual)

        sync_start = source.index("def sync_oktell_evaluation_calls(")
        sync_end = source.index("\ndef _ws_time_to_minutes", sync_start)
        sync_worker = source[sync_start:sync_end]
        self.assertIn("_oktell_prepare_distribution_audio({", sync_worker)
        self.assertIn("'audio_ready': grand_audio_ready", sync_worker)


if __name__ == "__main__":
    unittest.main()
