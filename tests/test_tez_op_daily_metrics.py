import ast
import textwrap
import unittest
from contextlib import contextmanager
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DATABASE_PATH = ROOT / "database.py"


def _database_method(method_name):
    source = DATABASE_PATH.read_text(encoding="utf-8-sig")
    module = ast.parse(source)
    database_class = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    node = next(
        node for node in database_class.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )
    namespace = {
        "CALCULATION_MODEL_TEZ_OP": "tez_op",
        "Json": lambda value: value,
        "execute_values": lambda *args, **kwargs: None,
    }
    exec(textwrap.dedent(ast.get_source_segment(source, node)), namespace)
    return namespace[method_name]


class _FakeCursor:
    def __init__(self, aggregate_rows=None):
        self.aggregate_rows = list(aggregate_rows or [])
        self.executions = []
        self.rowcount = 0
        self._selected = []

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        self.executions.append((normalized, params))
        if "SELECT map.user_id" in normalized:
            self._selected = list(self.aggregate_rows)
            self.rowcount = len(self._selected)
        else:
            self._selected = []
            self.rowcount = 2 if normalized.startswith("UPDATE daily_hours") else 0

    def fetchall(self):
        return list(self._selected)


class TezOpChatRollupTests(unittest.TestCase):
    def _db(self, cursor):
        method = _database_method("refresh_tez_op_chat_metrics")

        class FakeDatabase:
            _CHATAPP_HUMAN_OUT = (
                "m.side = 'out' AND COALESCE(m.app_sender, '') <> 'system' "
                "AND COALESCE(m.app_id, '') IN ('webchat', '')"
            )
            refresh_tez_op_chat_metrics = method

            @staticmethod
            def _normalize_schedule_date(value):
                if isinstance(value, date):
                    return value
                return datetime.strptime(str(value), "%Y-%m-%d").date()

        @contextmanager
        def fake_cursor():
            yield cursor

        instance = FakeDatabase()
        instance._get_cursor = fake_cursor
        instance._get_operator_group_id_tx = lambda _cursor, operator_id, day: 700 + int(operator_id)
        return instance

    def test_chat_is_counted_for_every_operator_who_wrote(self):
        day = date(2026, 7, 20)
        cursor = _FakeCursor([(11, day, 3), (22, day, 3)])
        captured = {}

        def fake_execute_values(_cursor, query, values, page_size=None):
            captured["query"] = " ".join(query.split())
            captured["values"] = list(values)

        method = _database_method("refresh_tez_op_chat_metrics")
        method.__globals__["execute_values"] = fake_execute_values
        db = self._db(cursor)
        db.refresh_tez_op_chat_metrics = method.__get__(db, type(db))
        result = db.refresh_tez_op_chat_metrics(day, day)

        select_sql = next(sql for sql, _ in cursor.executions if "SELECT map.user_id" in sql)
        self.assertIn(
            "COUNT(DISTINCT (m.license_id, m.messenger_type, m.chat_id))",
            select_sql,
        )
        self.assertIn("COALESCE(m.is_deleted, FALSE) = FALSE", select_sql)
        self.assertIn("COALESCE(map.is_bot, FALSE) = FALSE", select_sql)
        self.assertIn("m.side = 'out'", select_sql)
        self.assertIn("AT TIME ZONE 'Asia/Almaty'", select_sql)
        self.assertEqual([row[0] for row in captured["values"]], [11, 22])
        self.assertEqual(result["saved_days"], 2)
        self.assertEqual(result["chats"], 6)

    def test_refresh_clears_old_values_before_upsert(self):
        day = date(2026, 7, 21)
        cursor = _FakeCursor([])
        execute_values = mock.Mock()
        method = _database_method("refresh_tez_op_chat_metrics")
        method.__globals__["execute_values"] = execute_values
        db = self._db(cursor)
        db.refresh_tez_op_chat_metrics = method.__get__(db, type(db))
        result = db.refresh_tez_op_chat_metrics(day, day)

        first_sql, first_params = cursor.executions[0]
        self.assertTrue(first_sql.startswith("UPDATE daily_hours"))
        self.assertIn("'{chats}'", first_sql)
        self.assertIn("ORDER BY gom.start_date DESC", first_sql)
        self.assertEqual(first_params, (day, day, "tez_op"))
        execute_values.assert_not_called()
        self.assertEqual(result["cleared_days"], 2)
        self.assertEqual(result["saved_days"], 0)

    def test_chat_model_uses_latest_active_group_before_direction_fallback(self):
        day = date(2026, 7, 22)
        cursor = _FakeCursor([])
        method = _database_method("refresh_tez_op_chat_metrics")
        method.__globals__["execute_values"] = mock.Mock()
        db = self._db(cursor)
        db.refresh_tez_op_chat_metrics = method.__get__(db, type(db))

        db.refresh_tez_op_chat_metrics(day, day)

        select_sql = next(sql for sql, _ in cursor.executions if "SELECT map.user_id" in sql)
        self.assertIn("LEFT JOIN LATERAL", select_sql)
        self.assertIn("active_group.calculation_model_code", select_sql)
        self.assertIn("ORDER BY gom.start_date DESC LIMIT 1", select_sql)
        self.assertNotIn(
            "OR LOWER(COALESCE(d.calculation_model_code, ''))",
            select_sql,
        )


class TezOpCallStorageTests(unittest.TestCase):
    def test_group_hours_query_formats_json_default_without_positional_placeholder(self):
        source = DATABASE_PATH.read_text(encoding="utf-8-sig")
        start = source.index("def get_daily_hours_by_supervisor_month(")
        end = source.index(
            "\n    def get_daily_hours_for_all_month(",
            start,
        )
        method_module = ast.parse(textwrap.dedent(source[start:end]))
        daily_sql = next(
            node.value.value
            for node in ast.walk(method_module)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_daily_sql"
                for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
        )

        formatted = daily_sql.format(group_filter="AND d.group_id = %s")

        self.assertIn("COALESCE(d.extra_metrics, '{}'::jsonb)", formatted)
        self.assertIn("AND d.group_id = %s", formatted)

    def test_call_metrics_do_not_write_status_owned_talk_column(self):
        source = DATABASE_PATH.read_text(encoding="utf-8-sig")
        start = source.index("def replace_tez_op_call_metrics(")
        end = source.index("\n    def refresh_tez_op_chat_metrics(", start)
        method = source[start:end]
        self.assertIn('"talk_time": item["talk_time"]', method)
        self.assertIn("extra_metrics = COALESCE(daily_hours.extra_metrics", method)
        self.assertNotIn("talk_time = EXCLUDED.talk_time", method)
        self.assertIn("_aggregate_month_from_daily_tx(cursor, operator_id, month_key)", method)

    def test_tez_phone_totals_are_used_by_month_and_snapshot_aggregates(self):
        source = DATABASE_PATH.read_text(encoding="utf-8-sig")
        month_start = source.index("def _aggregate_month_from_daily_tx(")
        segment_start = source.index("def _aggregate_segment_from_daily_tx(", month_start)
        segment_end = source.index("\n    def _get_operator_month_segments_tx(", segment_start)
        month_method = source[month_start:segment_start]
        segment_method = source[segment_start:segment_end]
        self.assertIn("extra_metrics->>'talk_time'", month_method)
        self.assertIn("CALCULATION_MODEL_TEZ_OP", month_method)
        self.assertIn("extra_metrics->>'dial_time'", segment_method)
        self.assertIn("extra_metrics->>'chats'", segment_method)
        self.assertIn('"total_dial_time"', segment_method)
        self.assertIn('"total_chats"', segment_method)
        self.assertIn('("total_dial_time", "DOUBLE PRECISION")', source)
        self.assertIn('("total_chats", "INTEGER")', source)

    def test_tez_registry_exposes_requested_metrics(self):
        source = DATABASE_PATH.read_text(encoding="utf-8-sig")
        start = source.index("CALCULATION_MODEL_TEZ_OP: _CALC_METRICS_HEAD + [")
        end = source.index("] + _CALC_METRICS_TAIL", start)
        registry = source[start:end]
        self.assertIn("_calc_metric('calls', 'Звонки'", registry)
        self.assertIn("_calc_metric('dial_time', 'Время набора'", registry)
        self.assertIn("_calc_metric('talk_time', 'В разговоре'", registry)
        self.assertIn("_calc_metric('chats', 'Чаты'", registry)
        self.assertIn("_calc_metric('tez_successes', 'Успешки'", registry)


class TezOpChatSyncSafetyTests(unittest.TestCase):
    def test_partial_chatapp_fetch_cannot_replace_daily_metrics(self):
        bot_source = BOT_PATH.read_text(encoding="utf-8-sig")
        sync = bot_source[
            bot_source.index("def sync_chatapp_data("):
            bot_source.index("\ndef _chatapp_automatch_authors(", bot_source.index("def sync_chatapp_data("))
        ]
        failure_gate = sync.index("if failed_chats:")
        metric_refresh = sync.index("db.refresh_tez_op_chat_metrics(")
        self.assertIn("failed_chats += 1", sync)
        self.assertIn("raise RuntimeError(", sync[failure_gate:metric_refresh])
        self.assertLess(failure_gate, metric_refresh)

    def test_author_mapping_reports_refresh_warning_after_mapping_is_saved(self):
        bot_source = BOT_PATH.read_text(encoding="utf-8-sig")
        route = bot_source[
            bot_source.index("def api_chatapp_authors_map():"):
            bot_source.index(
                "\n@app.route('/api/ca_eval/pick'",
                bot_source.index("def api_chatapp_authors_map():"),
            )
        ]
        self.assertLess(
            route.index("db.upsert_chatapp_author_map("),
            route.index("db.refresh_tez_op_chat_metrics("),
        )
        self.assertIn('"status": "success"', route)
        self.assertIn('"error": "refresh_failed"', route)
        self.assertIn('"warning":', route)


class TezStatusAuthoritativeSegmentTests(unittest.TestCase):
    def test_binotel_status_import_marks_exact_segments_authoritative(self):
        bot_source = BOT_PATH.read_text(encoding="utf-8-sig")
        importer = bot_source[
            bot_source.index("def _tez_status_sync_importer("):
            bot_source.index("\n\n# Отметки Clockster", bot_source.index("def _tez_status_sync_importer("))
        ]
        self.assertIn("'segments_authoritative': True", importer)

        db_source = DATABASE_PATH.read_text(encoding="utf-8-sig")
        save = db_source[
            db_source.index("def save_operator_status_import("):
            db_source.index("\n    def append_operator_status_event(", db_source.index("def save_operator_status_import("))
        ]
        self.assertIn("and not authoritative_segments", save)
        self.assertIn(
            "if normalized_segments and (not normalized_events or authoritative_segments):",
            save,
        )
        self.assertIn("'is_authoritative'", save)

    def test_manual_tez_import_marks_exact_segments_authoritative(self):
        bot_source = BOT_PATH.read_text(encoding="utf-8-sig")
        route = bot_source[
            bot_source.index("def import_work_schedules_statuses_csv():"):
            bot_source.index(
                "\n@app.route('/api/work_schedules/sync_statuses_chat2desk'",
                bot_source.index("def import_work_schedules_statuses_csv():"),
            )
        ]
        self.assertIn("is_tez_format = _status_import_header_is_tez", route)
        self.assertIn("is_tez_format = _status_import_csv_text_is_tez", route)
        self.assertIn("'segments_authoritative': bool(is_tez_format)", route)

    def test_event_retention_ignores_authoritative_events_as_generic_anchors(self):
        source = DATABASE_PATH.read_text(encoding="utf-8-sig")
        purge = source[
            source.index("def purge_old_operator_status_events("):
            source.index(
                "\n    def _status_label_from_key(",
                source.index("def purge_old_operator_status_events("),
            )
        ]
        keep_cte = purge[purge.index("WITH keep AS"):purge.index("victims AS")]
        self.assertIn("COALESCE(is_authoritative, FALSE) = FALSE", keep_cte)

    def test_generic_rebuild_preserves_gap_between_authoritative_days(self):
        source = DATABASE_PATH.read_text(encoding="utf-8-sig")
        module = ast.parse(source)
        database_class = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "Database"
        )
        node = next(
            node for node in database_class.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_rebuild_operator_status_segments_tx"
        )
        captured = {"values": []}

        def fake_execute_values(_cursor, _query, values, **_kwargs):
            captured["values"].extend(values)

        namespace = {
            "CHAT_MANAGER_ACTION_STATUS_KEYS": set(),
            "STATUS_EVENTS_RETENTION_DAYS": 45,
            "STATUS_IMPORT_INSERT_PAGE_SIZE": 100,
            "date": date,
            "datetime": datetime,
            "dt_time": dt_time,
            "timedelta": timedelta,
            "execute_values": fake_execute_values,
        }
        exec(textwrap.dedent(ast.get_source_segment(source, node)), namespace)
        method = namespace["_rebuild_operator_status_segments_tx"]
        start_day = date.today() - timedelta(days=2)
        gap_day = start_day + timedelta(days=1)
        end_day = start_day + timedelta(days=2)
        start_at = datetime.combine(start_day, dt_time(9, 0))
        stop_at = datetime.combine(end_day, dt_time(18, 0))

        class Cursor:
            def __init__(self):
                self.rows = []
                self.rowcount = 0
                self.queries = []

            def execute(self, query, _params=None):
                normalized = " ".join(str(query).split())
                self.queries.append(normalized)
                self.rowcount = 0
                if "WITH candidates AS" in normalized:
                    # Оба события принадлежат exact start/stop-источнику и
                    # должны быть исключены самим SQL. Без predicate тестовый
                    # cursor вернул бы их и rebuild растянул бы статус на gap_day.
                    if "COALESCE(is_authoritative, FALSE) = FALSE" in normalized:
                        self.rows = []
                    else:
                        self.rows = [
                            (1, 7, start_at, "active", None, "status"),
                            (2, 7, stop_at, "inactive", None, "status"),
                        ]
                elif (
                    "FROM operator_status_segments" in normalized
                    and "is_authoritative" in normalized
                    and normalized.startswith("SELECT")
                ):
                    self.rows = [(7, start_day), (7, end_day)]
                else:
                    self.rows = []
                    if normalized.startswith("DELETE FROM operator_status_segments"):
                        self.rowcount = 4

            def fetchall(self):
                return list(self.rows)

        class FakeDb:
            STATUS_SEGMENT_OPERATOR_LOCK_NAMESPACE = 915904142

            @staticmethod
            def _normalize_schedule_date(value):
                return value

            @staticmethod
            def _lock_operator_status_segments_tx(_cursor, _operator_ids):
                return None

            @staticmethod
            def _normalize_import_status_key(value):
                return str(value or "").strip().lower()

            @staticmethod
            def _status_import_event_kind_from_key(_key, kind):
                return kind or "status"

            @staticmethod
            def _canonicalize_status_transition_events(events):
                return list(events or [])

            @staticmethod
            def _split_status_segment_datetimes_by_day(start_value, end_value):
                return [{
                    "status_date": start_value.date(),
                    "start_at": start_value,
                    "end_at": end_value,
                    "duration_sec": int((end_value - start_value).total_seconds()),
                }]

        cursor = Cursor()
        result = method(FakeDb(), cursor, [7], start_day, end_day)

        self.assertEqual(result["segments_saved"], 0)
        self.assertEqual(captured["values"], [])
        self.assertNotIn(
            gap_day,
            {
                value[1]
                for value in captured["values"]
            },
        )
        candidates_sql = next(
            query for query in cursor.queries
            if "WITH candidates AS" in query
        )
        self.assertIn("COALESCE(is_authoritative, FALSE) = FALSE", candidates_sql)
        self.assertIn(
            "ORDER BY operator_id, event_at ASC, id DESC",
            candidates_sql,
        )
        delete_sql = next(
            query for query in cursor.queries
            if query.startswith("DELETE FROM operator_status_segments")
        )
        self.assertIn("COALESCE(is_authoritative, FALSE) = FALSE", delete_sql)


if __name__ == "__main__":
    unittest.main()
