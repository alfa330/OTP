import ast
import copy
import importlib.util
import re
import unittest
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DATABASE_PATH = ROOT / "database.py"
FRONTEND_PATH = ROOT / "src" / "call_evaluation" / "main.jsx"
BINOTEL_PATH = ROOT / "tez_binotel_calls.py"


def _load_module_from_path(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {module_name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tez_binotel_calls = _load_module_from_path(
    "_call_end_party_tez_binotel_calls",
    BINOTEL_PATH,
)


@lru_cache(maxsize=None)
def _source(path):
    return Path(path).read_text(encoding="utf-8-sig")


@lru_cache(maxsize=None)
def _tree(path):
    return ast.parse(_source(path), filename=str(path))


def _function_node(path, function_name, class_name=None):
    tree = _tree(path)
    body = tree.body
    if class_name is not None:
        class_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        body = class_node.body
    return next(
        node
        for node in body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )


def _function_source(path, function_name, class_name=None):
    return ast.get_source_segment(
        _source(path),
        _function_node(path, function_name, class_name=class_name),
    )


def _load_bot_function(function_name, globals_dict=None):
    node = copy.deepcopy(_function_node(BOT_PATH, function_name))
    node.decorator_list = []
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = dict(globals_dict or {})
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace[function_name]


class BinotelCallEndPartyTests(unittest.TestCase):
    def test_direct_party_values_are_normalized(self):
        cases = [
            ("operator", 0, "operator"),
            (" Employee ", 1, "operator"),
            ("internal_number", 0, "operator"),
            ("client", 1, "client"),
            ("EXTERNAL", 0, "client"),
            ("subscriber", 1, "client"),
            ("system", 0, "system"),
            ("IVR", 1, "system"),
        ]
        for raw, call_type, expected in cases:
            with self.subTest(raw=raw, call_type=call_type):
                self.assertEqual(
                    tez_binotel_calls.normalize_call_end_party(raw, call_type),
                    expected,
                )

    def test_caller_and_callee_are_resolved_by_direction(self):
        incoming = tez_binotel_calls.CALL_TYPE_INCOMING
        outgoing = tez_binotel_calls.CALL_TYPE_OUTGOING
        cases = [
            ("caller", incoming, "client"),
            ("caller", outgoing, "operator"),
            ("callee", incoming, "operator"),
            ("callee", outgoing, "client"),
            ("originator", incoming, "client"),
            ("recipient", outgoing, "client"),
        ]
        for raw, call_type, expected in cases:
            with self.subTest(raw=raw, call_type=call_type):
                self.assertEqual(
                    tez_binotel_calls.normalize_call_end_party(raw, call_type),
                    expected,
                )

    def test_missing_or_unrecognized_value_is_never_guessed_from_direction(self):
        for raw in (None, "", "   ", "unmapped-provider-value"):
            for call_type in (
                tez_binotel_calls.CALL_TYPE_INCOMING,
                tez_binotel_calls.CALL_TYPE_OUTGOING,
                -1,
            ):
                with self.subTest(raw=raw, call_type=call_type):
                    self.assertEqual(
                        tez_binotel_calls.normalize_call_end_party(raw, call_type),
                        "unknown",
                    )

    def test_normalized_call_keeps_raw_binotel_evidence(self):
        normalized = tez_binotel_calls.BinotelApiClient._normalize_call(
            {
                "generalCallID": "42",
                "callType": str(tez_binotel_calls.CALL_TYPE_OUTGOING),
                "whoHungUp": " Caller ",
            }
        )
        self.assertEqual(normalized["call_end_party"], "operator")
        self.assertEqual(normalized["call_end_raw"], "Caller")

        missing = tez_binotel_calls.BinotelApiClient._normalize_call(
            {"generalCallID": "43", "callType": "1"}
        )
        self.assertEqual(missing["call_end_party"], "unknown")
        self.assertEqual(missing["call_end_raw"], "")


class OktellCallEndPartyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.normalize = staticmethod(_load_bot_function("_oktell_call_end_party"))

    def test_subscriber_hangup_matrix(self):
        cases = [
            (1, 0, 2, "operator"),
            (1, 1, 2, "client"),
            (5, 0, 2, "client"),
            (5, 1, 2, "operator"),
            ("1", "0", "2", "operator"),
            ("5", "1", "2", "operator"),
        ]
        for connection_type, stop_side, reason_stop, expected in cases:
            with self.subTest(
                connection_type=connection_type,
                stop_side=stop_side,
                reason_stop=reason_stop,
            ):
                self.assertEqual(
                    self.normalize(connection_type, stop_side, reason_stop),
                    expected,
                )

    def test_system_reasons_do_not_depend_on_side_metadata(self):
        for reason_stop in (1, 3, 4, 5):
            for connection_type, stop_side in ((1, 0), (5, 1), (None, None)):
                with self.subTest(
                    reason_stop=reason_stop,
                    connection_type=connection_type,
                    stop_side=stop_side,
                ):
                    self.assertEqual(
                        self.normalize(connection_type, stop_side, reason_stop),
                        "system",
                    )

    def test_flash_is_transfer_even_without_side_metadata(self):
        for connection_type, stop_side in ((1, 0), (5, 1), (None, None)):
            with self.subTest(
                connection_type=connection_type,
                stop_side=stop_side,
            ):
                self.assertEqual(
                    self.normalize(connection_type, stop_side, 6),
                    "transfer",
                )

    def test_invalid_or_unsupported_subscriber_metadata_is_unknown(self):
        cases = [
            (None, 0, 2),
            (1, None, 2),
            (1, 2, 2),
            (2, 0, 2),
            (1, 0, 7),
            ("bad", 0, 2),
            (1, "bad", 2),
            (1, 0, None),
        ]
        for connection_type, stop_side, reason_stop in cases:
            with self.subTest(
                connection_type=connection_type,
                stop_side=stop_side,
                reason_stop=reason_stop,
            ):
                self.assertEqual(
                    self.normalize(connection_type, stop_side, reason_stop),
                    "unknown",
                )


class CallEndPartyBackendContractTests(unittest.TestCase):
    def test_oktell_queries_select_stop_side_reason_and_connection_type(self):
        sample = _function_source(BOT_PATH, "_oktell_eval_sample_sql")
        self.assertIn("s.ConnectionType AS ct", sample)
        self.assertIn("s.StopSide AS stop_side", sample)
        self.assertIn("s.ReasonStop AS reason_stop", sample)
        self.assertIn("q.stop_side, q.reason_stop", sample)

        backfill_query = _function_source(
            BOT_PATH, "_oktell_call_end_party_rows_sql"
        )
        self.assertIn("s.ConnectionType AS ct", backfill_query)
        self.assertIn("s.StopSide AS stop_side", backfill_query)
        self.assertIn("s.ReasonStop AS reason_stop", backfill_query)

    def test_journal_union_appends_party_and_import_id_without_shifting_old_fields(self):
        method = _function_source(
            DATABASE_PATH, "get_call_evaluations", class_name="Database"
        )
        self.assertIn("c.call_end_party", method)
        self.assertIn("c.imported_call_id", method)
        self.assertIn("ic.call_end_party", method)
        self.assertIn("ic.id AS imported_call_id", method)
        self.assertIn('"chat_quotes": row[50]', method)
        self.assertIn('"call_end_party": row[51]', method)
        self.assertIn('"imported_call_id": row[52]', method)

    def test_evaluation_api_accepts_only_import_identity_not_client_party(self):
        endpoint = _function_source(BOT_PATH, "receive_call_evaluation")
        self.assertIn("request.form.get('imported_call_id')", endpoint)
        self.assertIn("imported_call_id=imported_call_id", endpoint)
        self.assertNotIn("request.form.get('call_end_party')", endpoint)

        method = _function_source(
            DATABASE_PATH, "add_call_evaluation", class_name="Database"
        )
        self.assertIn("WHERE id = %s AND operator_id = %s AND month = %s", method)
        self.assertIn("call_end_party, imported_call_id", method)
        self.assertIn("call_end_party = %s", method)
        self.assertIn("imported_call_id = %s", method)
        self.assertNotIn("call_end_party = COALESCE(%s, call_end_party)", method)
        self.assertNotIn(
            "imported_call_id = COALESCE(%s, imported_call_id)",
            method,
        )

    def test_version_endpoint_serializes_party(self):
        endpoint = _function_source(BOT_PATH, "get_call_versions")
        self.assertIn("c.call_end_party", endpoint)
        self.assertIn('"call_end_party": version[10]', endpoint)

    def test_unknown_oktell_backfill_uses_after_external_id_cursor(self):
        db_method_node = _function_node(
            DATABASE_PATH,
            "get_unknown_oktell_call_external_ids",
            class_name="Database",
        )
        arg_names = [arg.arg for arg in db_method_node.args.args]
        self.assertIn("after_external_id", arg_names)

        db_method = _function_source(
            DATABASE_PATH,
            "get_unknown_oktell_call_external_ids",
            class_name="Database",
        )
        self.assertIn("call_end_party_checked_at", db_method)
        self.assertIn("INTERVAL '7 days'", db_method)
        self.assertIn("NULLS FIRST", db_method)
        self.assertRegex(
            db_method,
            re.compile(r"LOWER\s*\(\s*external_id\s*\)\s*>\s*%s", re.I),
        )

        backfill = _function_source(BOT_PATH, "backfill_oktell_call_end_parties")
        self.assertIn("after_external_id", backfill)
        self.assertNotRegex(
            backfill,
            re.compile(r"if\s+batch_updated\s*==\s*0\s*:\s*break"),
        )

    def test_unresolvable_first_page_does_not_block_later_oktell_rows(self):
        first_id = "00000000-0000-0000-0000-000000000001"
        second_id = "00000000-0000-0000-0000-000000000002"
        resolvable_id = "00000000-0000-0000-0000-000000000003"

        class FakeDatabase:
            def __init__(self):
                self.cursors = []
                self.updates = []
                self.checked = []

            def get_unknown_oktell_call_external_ids(
                self, limit=10000, after_external_id=None
            ):
                self.cursors.append(after_external_id)
                if after_external_id is None:
                    return [first_id, second_id]
                if after_external_id == second_id:
                    return [resolvable_id]
                return []

            def update_imported_call_end_parties(self, party_by_external_id):
                self.updates.append(dict(party_by_external_id))
                return len(party_by_external_id)

            def mark_oktell_call_end_parties_checked(self, external_ids):
                self.checked.extend(external_ids)
                return len(external_ids)

        rows = {
            first_id: {
                "conn_id": first_id,
                "ct": 1,
                "stop_side": 0,
                "reason_stop": None,
            },
            second_id: {
                "conn_id": second_id,
                "ct": 1,
                "stop_side": 0,
                "reason_stop": 7,
            },
            resolvable_id: {
                "conn_id": resolvable_id,
                "ct": 1,
                "stop_side": 0,
                "reason_stop": 2,
            },
        }
        fake_db = FakeDatabase()
        normalize = _load_bot_function("_oktell_call_end_party")
        backfill = _load_bot_function(
            "backfill_oktell_call_end_parties",
            {
                "db": fake_db,
                "_oktell_api_ready": lambda: True,
                "_oktell_call_end_party_rows_sql": lambda conn_ids: list(conn_ids),
                "_oktell_query": lambda conn_ids: [rows[conn_id] for conn_id in conn_ids],
                "_oktell_call_end_party": normalize,
            },
        )

        result = backfill(batch_size=2, max_batches=3)

        self.assertEqual(result["updated"], 1)
        self.assertEqual(
            fake_db.updates,
            [{}, {resolvable_id: "operator"}],
        )
        self.assertEqual(
            fake_db.cursors[:3],
            [None, second_id, resolvable_id],
        )
        self.assertEqual(
            fake_db.checked,
            [first_id, second_id, resolvable_id],
        )

    def test_distribution_retry_cannot_downgrade_known_party_to_unknown(self):
        method = _function_source(
            DATABASE_PATH, "import_calls_from_distribution", class_name="Database"
        )
        compact = " ".join(method.split())
        self.assertIn(
            "NULLIF(EXCLUDED.call_end_party, 'unknown')",
            compact,
        )
        self.assertIn("imported_calls.call_end_party", compact)


class CallEndPartyFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source(FRONTEND_PATH)

    def test_null_is_hidden_but_unknown_has_an_explicit_label(self):
        self.assertIn(
            "if (value === null || value === undefined || String(value).trim() === '') return null;",
            self.source,
        )
        self.assertIn("if (!normalizedParty) return null;", self.source)
        self.assertIn("label: 'Не определено'", self.source)
        self.assertIn(
            "description: 'Кто завершил звонок, не определено'",
            self.source,
        )

    def test_api_mapping_and_optimistic_transition_keep_party_and_import_id(self):
        self.assertIn("callEndParty: ev.call_end_party ?? null", self.source)
        self.assertIn(
            "importedCallId: ev.imported_call_id ?? (ev.is_imported ? ev.id : null)",
            self.source,
        )
        self.assertIn(
            "fd.append('imported_call_id', String(importedCallId))",
            self.source,
        )
        self.assertIn(
            "callEndParty: data.callEndParty ?? resolveCallEndParty(editingEval)",
            self.source,
        )

    def test_badge_is_present_in_journal_details_modals_requests_and_history(self):
        self.assertGreaterEqual(self.source.count("<CallEndedBadge"), 9)
        required_fragments = [
            '<CallEndedBadge party={resolveCallEndParty(call)} className="call-ended-cell-badge" />',
            '<CallEndedBadge party={resolveCallEndParty(call)} />',
            '<CallEndedBadge party={resolveCallEndParty(existingEvaluation)} className="call-ended-header-badge" />',
            '<CallEndedBadge party={resolveCallEndParty(requestItem)} className="call-ended-cell-badge" />',
            '<CallEndedBadge party={resolveCallEndParty(requestItem)} />',
            '<CallEndedBadge party={resolveCallEndParty(v)} />',
        ]
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.source)


if __name__ == "__main__":
    unittest.main()
