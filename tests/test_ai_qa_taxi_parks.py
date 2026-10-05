"""Таксопарк относится к конкретному разговору, а не ко всем контактам номера.

SELECT исполняются на SQLite с заменой только параметров PostgreSQL ANY:
проверяем реальные связи таблиц, одинаковые id разных субъектов и переводы CDR.
"""
import sqlite3
import unittest
from unittest import mock

from call_qa import taxi_parks as parks


CONN_ID = "12345678-1234-1234-1234-123456789abc"


class Cursor:
    def __init__(self, connection):
        self.cursor = connection.cursor()

    def execute(self, sql, params=()):
        if "ANY(%s)" in sql:
            values = params[0]
            sql = sql.replace("= ANY(%s)", "IN (" + ",".join("?" for _ in values) + ")")
            params = values
        self.cursor.execute(sql.replace("%s", "?"), params)

    def fetchall(self):
        return self.cursor.fetchall()

    def close(self):
        self.cursor.close()


class TaxiParksTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.create_function("btrim", 1, lambda value: value.strip() if value else value)
        self.db.create_function("split_part", 3, lambda value, sep, index: value.split(sep)[index - 1])
        self.db.executescript("""
            CREATE TABLE departments (id INTEGER PRIMARY KEY, code TEXT);
            CREATE TABLE directions (id INTEGER PRIMARY KEY, department_id INTEGER);
            CREATE TABLE users (id INTEGER PRIMARY KEY, direction_id INTEGER, department_id INTEGER);
            CREATE TABLE calls (id INTEGER PRIMARY KEY, direction_id INTEGER,
                imported_call_id INTEGER, c2d_snapshot_id INTEGER, phone_number TEXT);
            CREATE TABLE imported_calls (id INTEGER PRIMARY KEY, operator_id INTEGER,
                external_id TEXT, phone_number TEXT);
            CREATE TABLE wazzup_episodes (id INTEGER PRIMARY KEY, operator_user_id INTEGER,
                contact_phone TEXT, channel_id TEXT);
            CREATE TABLE c2d_chat_snapshots (id INTEGER PRIMARY KEY, operator_id INTEGER,
                client_phone TEXT, channel_name TEXT, wz_channel_id TEXT, source TEXT);
            CREATE TABLE cdr_touches (linkedid TEXT, phone TEXT, call_type TEXT, queue TEXT,
                line_number TEXT, call_day DATE);
            INSERT INTO departments VALUES (1, 'op'), (2, 'szov'), (3, 'tez');
            INSERT INTO directions VALUES (10, 1), (20, 2), (30, 3);
            INSERT INTO users VALUES (1, 10, 1), (2, 20, 2), (3, 30, 3);
            INSERT INTO calls VALUES (1, 10, 1, NULL, '+7 (777) 000-00-01'),
                (2, 20, 2, NULL, '77770000002'), (3, 30, 3, NULL, '77770000003'),
                (4, 10, NULL, NULL, '77770000001'), (5, 10, 1, NULL, '  ');
            INSERT INTO imported_calls VALUES (1, 1, 'cdr-1', '77770000001'),
                (2, 2, '12345678-1234-1234-1234-123456789abc', '77770000002'),
                (3, 3, '123456', '77770000003');
            INSERT INTO wazzup_episodes VALUES (1, 1, '77770000001', 'wz-1'),
                (2, 1, '77770000001', 'missing');
            INSERT INTO c2d_chat_snapshots VALUES (1, 2, '77770000001', ' Qazaq ', NULL, 'chat2desk');
            INSERT INTO cdr_touches VALUES
                ('cdr-1', '7770000001', 'Входящий', '3001', '7000000000', '2026-10-05'),
                ('cdr-1', '7770000099', 'Входящий', '3034', '7000000001', '2026-10-05');
        """)
        parks._oktell_cache.clear()
        self.oktell = mock.Mock(return_value=[{"conn_id": CONN_ID, "taxi_park": "Jana"}])
        self.wazzup = mock.Mock(return_value=[{"channelId": "wz-1", "name": "Global"}])
        self.connection = mock.Mock()
        self.connection.cursor.side_effect = lambda: Cursor(self.db)
        self.patch = mock.patch.object(parks.config, "connect_ro", return_value=self.connection)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def attach(self, items, department=None):
        parks.attach_taxi_parks(items, department=department, oktell_query=self.oktell,
                               wazzup_channels=self.wazzup,
                               park_label=lambda name: {"Jana": "Jana такси"}.get(name, name))
        return [item.get("taxi_park") for item in items]

    def test_same_ids_and_phone_do_not_mix_subjects_or_parks(self):
        items = [{"id": 1, "subject": kind} for kind in
                 ("call", "imported_call", "wz_episode", "c2d_snapshot")]
        self.assertEqual(self.attach(items), ["iTaxi", "iTaxi", "Global", "Qazaq"])
        self.oktell.assert_not_called()
        self.wazzup.assert_called_once_with()

    def test_oktell_journal_and_import_share_batch_and_cache(self):
        items = [{"id": 2, "subject": "call"}, {"id": 2, "subject_kind": "imported_call"}]
        self.assertEqual(self.attach(items), ["Jana такси", "Jana такси"])
        self.assertEqual(self.attach(items), ["Jana такси", "Jana такси"])
        self.oktell.assert_called_once()
        sql = self.oktell.call_args.args[0]
        self.assertEqual(sql.count(CONN_ID), 1)
        self.assertIn("s.IdChain", sql)
        self.connection.close.assert_called()

    def test_missing_link_is_not_inferred_from_another_call_with_same_phone(self):
        items = [{"id": 4, "subject_kind": "call"}]
        self.assertEqual(self.attach(items), [None])
        self.assertIn("taxi_park", items[0])

    def test_journal_blank_phone_uses_linked_import(self):
        self.assertEqual(self.attach([{"id": 5}]), ["iTaxi"])

    def test_cdr_transfers_match_full_client_number(self):
        items = [{"id": "cdr-1", "subject": "cdr_touch", "phone": phone}
                 for phone in ("87770000001", "77770000099", "999")]
        self.assertEqual(self.attach(items, "op"), ["iTaxi", "Jana такси", None])

    def test_outgoing_uses_line_not_autodial_queue(self):
        self.db.execute("INSERT INTO cdr_touches VALUES (?, ?, ?, ?, ?, ?)",
                        ("cdr-2", "7770000001", "Исходящий", "4001", "7000000000",
                         parks.queries.today_almaty().isoformat()))
        self.db.execute("UPDATE cdr_touches SET call_day = ? WHERE linkedid = 'cdr-1'",
                        (parks.queries.today_almaty().isoformat(),))
        self.assertEqual(self.attach([{"id": "cdr-2", "subject": "cdr_touch",
                                       "phone": "77770000001"}], "op"), ["iTaxi"])

    def test_other_department_does_not_get_a_park_field(self):
        items = [{"id": 3, "subject_kind": "call"}]
        self.attach(items)
        self.assertNotIn("taxi_park", items[0])
        self.connection.reset_mock()
        self.attach(items, "tez")
        self.connection.cursor.assert_not_called()

    def test_unavailable_external_sources_leave_evaluations_usable(self):
        self.oktell.side_effect = RuntimeError("offline")
        self.wazzup.side_effect = RuntimeError("offline")
        items = [{"id": 2, "subject": "call", "ai_score": 85},
                 {"id": 1, "subject": "wz_episode", "ai_score": 90},
                 {"id": 1, "subject": "c2d_snapshot", "ai_score": 95}]
        with self.assertLogs(level="WARNING"):
            self.assertEqual(self.attach(items), [None, None, "Qazaq"])
        self.assertEqual([item["ai_score"] for item in items], [85, 90, 95])

    def test_missing_wazzup_name_does_not_show_channel_identifier(self):
        self.assertEqual(self.attach([{"id": 2, "subject": "wz_episode"}]), [None])

    def test_proxy_sql_accepts_only_uuids(self):
        self.assertIsNone(parks.oktell_parks_sql(["x'); DROP TABLE calls; --"]))
        sql = parks.oktell_parks_sql([CONN_ID.upper(), "' OR 1=1 --"])
        self.assertIn(CONN_ID, sql)
        self.assertNotIn("OR 1=1", sql)


if __name__ == "__main__":
    unittest.main()
