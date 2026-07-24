import ast
import logging
import unittest
import uuid
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
        "requests": fake_requests,
        "db": fake_db,
        "OKTELL_API_URL": "http://89.107.98.195:8085/query",
        "OKTELL_API_TOKEN": "secret-token",
        "OKTELL_API_TIMEOUT_SECONDS": 60,
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
            f"http://89.107.98.195:8085/record/{CONN_ID}",
        )

    def test_download_uses_api_key_and_accepts_mp3(self):
        audio = b"\xff\xe3" + b"\x00" * 32
        ns = _oktell_audio_namespace(FakeResponse(content=audio))

        self.assertEqual(ns["_oktell_download_record"](CONN_ID), (audio, "audio/mpeg"))
        url, kwargs = ns["_fake_requests"].calls[0]
        self.assertEqual(url, f"http://89.107.98.195:8085/record/{CONN_ID}")
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


if __name__ == "__main__":
    unittest.main()
