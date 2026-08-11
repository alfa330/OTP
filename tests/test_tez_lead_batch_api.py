import ast
import copy
import unittest
import uuid
from pathlib import Path
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8-sig")
BOT_TREE = source_cache.parse(BOT_SOURCE)


def _load_functions(*names, namespace=None):
    wanted = set(names)
    nodes = []
    for node in BOT_TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            cloned = copy.deepcopy(node)
            cloned.decorator_list = []
            nodes.append(cloned)
    found = {node.name for node in nodes}
    if found != wanted:
        raise AssertionError(f"Missing functions in bot_schedule2.py: {sorted(wanted - found)}")
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    result = dict(namespace or {})
    exec(compile(module, str(BOT_PATH), "exec"), result)
    return result


class FakeRequest:
    def __init__(self, *, raw=b"{}", payload=None, is_json=True, parse_error=None):
        self._raw = raw
        self._payload = payload
        self.is_json = is_json
        self._parse_error = parse_error

    def get_data(self, cache=True):
        return self._raw

    def get_json(self, silent=False):
        if self._parse_error is not None:
            raise self._parse_error
        return self._payload


def _jsonify(payload):
    return payload


class QuietLogging:
    @staticmethod
    def exception(*_args, **_kwargs):
        return None

    @staticmethod
    def error(*_args, **_kwargs):
        return None


class BatchReasonValidationTests(unittest.TestCase):
    def _parse(self, request, required):
        ns = _load_functions(
            "_tez_leads_parse_batch_reason",
            namespace={
                "request": request,
                "jsonify": _jsonify,
                "TEZ_LEADS_BATCH_REASON_MAX_LENGTH": 2000,
            },
        )
        return ns["_tez_leads_parse_batch_reason"](require_reason=required)

    def assert_bad_request(self, request, required=False):
        reason, error = self._parse(request, required)
        self.assertIsNone(reason)
        self.assertIsNotNone(error)
        self.assertEqual(error[1], 400)
        self.assertIsInstance(error[0], dict)
        self.assertIn("error", error[0])

    def test_delete_requires_non_empty_string_reason(self):
        self.assert_bad_request(FakeRequest(payload={}), required=True)
        self.assert_bad_request(FakeRequest(payload={"reason": " \r\n "}), required=True)
        self.assert_bad_request(FakeRequest(payload={"reason": None}), required=True)
        self.assert_bad_request(FakeRequest(payload={"reason": ["wrong"]}), required=True)

    def test_delete_accepts_and_trims_reason_up_to_limit(self):
        reason, error = self._parse(
            FakeRequest(payload={"reason": "  ошибочная загрузка  "}),
            required=True,
        )
        self.assertIsNone(error)
        self.assertEqual(reason, "ошибочная загрузка")

        reason, error = self._parse(
            FakeRequest(payload={"reason": "x" * 2000}),
            required=True,
        )
        self.assertIsNone(error)
        self.assertEqual(len(reason), 2000)

    def test_reason_over_limit_is_rejected(self):
        self.assert_bad_request(
            FakeRequest(payload={"reason": "x" * 2001}),
            required=True,
        )
        self.assert_bad_request(
            FakeRequest(payload={"reason": "x" * 2001}),
            required=False,
        )

    def test_restore_allows_absent_or_empty_string_reason(self):
        reason, error = self._parse(
            FakeRequest(raw=b"", payload=None, is_json=False),
            required=False,
        )
        self.assertIsNone(error)
        self.assertEqual(reason, "")

        reason, error = self._parse(
            FakeRequest(payload={"reason": ""}),
            required=False,
        )
        self.assertIsNone(error)
        self.assertEqual(reason, "")

    def test_malformed_non_object_and_non_json_bodies_are_rejected(self):
        self.assert_bad_request(
            FakeRequest(raw=b"{", parse_error=ValueError("bad json")),
            required=False,
        )
        for payload in (None, ["reason"], "reason", 1):
            with self.subTest(payload=payload):
                self.assert_bad_request(
                    FakeRequest(raw=b"x", payload=payload),
                    required=False,
                )
        self.assert_bad_request(
            FakeRequest(raw=b"reason=x", payload=None, is_json=False),
            required=False,
        )


class FakeBatchDb:
    def __init__(self, metadata):
        self.metadata = metadata
        self.calls = []

    def get_tez_lead_batch_metadata(self, batch_id):
        self.calls.append(batch_id)
        if isinstance(self.metadata, Exception):
            raise self.metadata
        return self.metadata


class BatchManagerGuardTests(unittest.TestCase):
    BATCH_ID = "b3a96f0b-5b4c-49b5-a95c-f530714e5ddf"

    def _guard(
        self,
        *,
        role="sv",
        metadata=None,
        requester_id=7,
        headed_department_id=None,
        scope_department_id=10,
        auth_error=None,
        requester_as_dict=False,
    ):
        db = FakeBatchDb(
            {"id": self.BATCH_ID, "department_id": 10, "year": 2026, "month": 7}
            if metadata is None
            else metadata
        )
        requester = (
            {"id": requester_id, "role": role}
            if requester_as_dict
            else (requester_id, None, None, role)
        )

        def get_requester():
            if auth_error:
                return None, None, auth_error
            return requester_id, requester, None

        def is_global_admin(normalized_role, _requester_id):
            return normalized_role == "super_admin" or (
                normalized_role == "admin" and headed_department_id is None
            )

        ns = _load_functions(
            "_tez_leads_batch_uuid",
            "_tez_leads_require_batch_manager",
            namespace={
                "uuid": uuid,
                "jsonify": _jsonify,
                "logging": QuietLogging,
                "db": db,
                "_get_authenticated_requester": get_requester,
                "_normalize_user_role": lambda value: str(value or "").strip().lower(),
                "_headed_department_id": lambda _rid: headed_department_id,
                "_is_global_admin_requester": is_global_admin,
                "_is_supervisor_role": lambda value: value == "sv",
                "_department_scope_id_for_requester": lambda _rid: scope_department_id,
            },
        )
        return ns["_tez_leads_require_batch_manager"], db

    def test_authentication_and_manager_role_are_required_before_lookup(self):
        guard, db = self._guard(auth_error=("Unauthorized", 401))
        result = guard(self.BATCH_ID)
        self.assertEqual(result[3][1], 401)
        self.assertEqual(db.calls, [])

        guard, db = self._guard(role="operator")
        result = guard(self.BATCH_ID)
        self.assertEqual(result[3][1], 403)
        self.assertEqual(db.calls, [])

    def test_invalid_and_unknown_uuid_return_400_and_404(self):
        guard, db = self._guard()
        result = guard("not-a-uuid")
        self.assertEqual(result[3][1], 400)
        self.assertEqual(db.calls, [])

        guard, db = self._guard(metadata=False)
        db.metadata = None
        result = guard(self.BATCH_ID)
        self.assertEqual(result[3][1], 404)
        self.assertEqual(db.calls, [self.BATCH_ID])

    def test_non_global_manager_must_match_batch_department(self):
        guard, _ = self._guard(role="sv", scope_department_id=10)
        requester_id, uid, metadata, error = guard(self.BATCH_ID)
        self.assertIsNone(error)
        self.assertEqual(requester_id, 7)
        self.assertEqual(uid, self.BATCH_ID)
        self.assertEqual(metadata["department_id"], 10)

        guard, _ = self._guard(role="sv", scope_department_id=11)
        self.assertEqual(guard(self.BATCH_ID)[3][1], 403)

    def test_department_head_is_allowed_but_remains_scoped(self):
        guard, _ = self._guard(
            role="trainer",
            headed_department_id=10,
            scope_department_id=10,
            requester_as_dict=True,
        )
        self.assertIsNone(guard(self.BATCH_ID)[3])

        guard, _ = self._guard(
            role="admin",
            headed_department_id=11,
            scope_department_id=11,
        )
        self.assertEqual(guard(self.BATCH_ID)[3][1], 403)

    def test_legacy_null_department_is_global_admin_only(self):
        metadata = {
            "id": self.BATCH_ID,
            "department_id": None,
            "year": 2026,
            "month": 7,
        }
        guard, _ = self._guard(role="sv", metadata=metadata)
        self.assertEqual(guard(self.BATCH_ID)[3][1], 403)

        guard, _ = self._guard(role="super_admin", metadata=metadata)
        self.assertIsNone(guard(self.BATCH_ID)[3])

    def test_metadata_failure_is_json_500(self):
        guard, _ = self._guard(metadata=RuntimeError("db unavailable"))
        result = guard(self.BATCH_ID)
        self.assertEqual(result[3][1], 500)
        self.assertEqual(result[3][0], {"error": "Не удалось проверить загрузку"})


class BatchRouteWiringTests(unittest.TestCase):
    def _source(self, function_name):
        node = next(
            item for item in BOT_TREE.body
            if isinstance(item, ast.FunctionDef) and item.name == function_name
        )
        return ast.get_source_segment(BOT_SOURCE, node)

    def test_all_batch_routes_use_scoped_resource_guard(self):
        for name in (
            "tez_leads_batch_delete",
            "tez_leads_batch_restore",
            "tez_leads_batch_history",
        ):
            with self.subTest(route=name):
                source = self._source(name)
                self.assertIn("_tez_leads_require_batch_manager(batch_id)", source)
                self.assertNotIn("_tez_leads_require_manager()", source)

    def test_delete_and_restore_use_strict_reason_contract(self):
        delete_source = self._source("tez_leads_batch_delete")
        restore_source = self._source("tez_leads_batch_restore")
        self.assertIn(
            "_tez_leads_parse_batch_reason(require_reason=True)",
            delete_source,
        )
        self.assertIn(
            "_tez_leads_parse_batch_reason(require_reason=False)",
            restore_source,
        )
        self.assertNotIn("request.values", delete_source)
        self.assertNotIn("request.values", restore_source)

    def test_delete_log_does_not_include_reason(self):
        delete_source = self._source("tez_leads_batch_delete")
        logging_call = delete_source[delete_source.index("logging.info"):]
        self.assertNotIn("reason or", logging_call)
        self.assertNotIn(", reason", logging_call)


if __name__ == "__main__":
    unittest.main()
