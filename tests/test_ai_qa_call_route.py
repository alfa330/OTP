"""Маршрут звонка в карточке ИИ-оценки: входящий/исходящий, номер клиента, наш номер.

SELECT исполняются на SQLite (как в test_ai_qa_taxi_parks.py): проверяем настоящие
связи таблиц, выбор касания при переводе и то, что у каждой телефонии берётся своё,
а чего она не знает — не выдумывается.
"""
import sqlite3
import unittest
from unittest import mock

from call_qa import call_route as route
from call_qa import taxi_parks as parks


CONN_IN = "12345678-1234-1234-1234-123456789abc"
CONN_OUT = "87654321-4321-4321-4321-cba987654321"


class Cursor:
    def __init__(self, connection):
        self.cursor = connection.cursor()

    def execute(self, sql, params=()):
        if "ANY(%s)" in sql:
            values = params[0]
            sql = sql.replace("= ANY(%s)", "IN (" + ",".join("?" for _ in values) + ")")
            params = values
        self.cursor.execute(sql.replace("%s", "?"), params)

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    def close(self):
        self.cursor.close()


class CallRouteTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.create_function("btrim", 1, lambda value: value.strip() if value else value)
        self.db.executescript(f"""
            CREATE TABLE departments (id INTEGER PRIMARY KEY, code TEXT);
            CREATE TABLE directions (id INTEGER PRIMARY KEY, department_id INTEGER);
            CREATE TABLE users (id INTEGER PRIMARY KEY, direction_id INTEGER, department_id INTEGER);
            CREATE TABLE calls (id INTEGER PRIMARY KEY, direction_id INTEGER,
                imported_call_id INTEGER, c2d_snapshot_id INTEGER, phone_number TEXT);
            CREATE TABLE imported_calls (id INTEGER PRIMARY KEY, operator_id INTEGER,
                external_id TEXT, phone_number TEXT, notes TEXT);
            CREATE TABLE cdr_touches (linkedid TEXT, phone TEXT, call_type TEXT, queue TEXT,
                line_number TEXT, call_day DATE);
            CREATE TABLE tez_lead_calls (general_call_id TEXT PRIMARY KEY, call_type INTEGER);
            INSERT INTO departments VALUES (1, 'op'), (2, 'szov'), (3, 'tez');
            INSERT INTO directions VALUES (10, 1), (20, 2), (30, 3);
            INSERT INTO users VALUES (1, 10, 1), (2, 20, 2), (3, 30, 3);
            INSERT INTO imported_calls VALUES
                (1, 1, '1729000000.11', '77010000001', 'aiqa:auto:cdr'),
                (2, 1, '1729000000.22', '77010000002', 'random:5:cdr'),
                (3, 2, '{CONN_IN}', '77070000003', 'aiqa:auto:oktell'),
                (4, 2, '{CONN_OUT}', '87070000004', 'distribution:oktell'),
                (5, 3, '9876543', '77470000005', 'aiqa:auto:binotel'),
                (6, 2, '{CONN_IN}', '77070000003', ''),
                (7, 3, '1111111', '77470000007', 'aiqa:auto:binotel');
            INSERT INTO calls VALUES (1, 10, NULL, NULL, '+7 (701) 000-00-09'),
                (2, 10, 2, NULL, '8 701 999 99 99'), (3, 10, NULL, NULL, 'chat_15');
            INSERT INTO cdr_touches VALUES
                ('1729000000.11', '7010000001', 'Входящий', '3001', '7272000001', '2026-10-25'),
                ('1729000000.22', '7019999999', 'Исходящий', '', '7272000002', '2026-10-25'),
                ('1729000000.22', '7010000002', 'Исходящий', '', '7272000003', '2026-10-25');
            INSERT INTO tez_lead_calls VALUES ('9876543', 1);
        """)
        parks._oktell_cache.clear()
        self.addCleanup(parks._oktell_cache.clear)
        self.oktell = mock.Mock(return_value=[
            {"conn_id": CONN_IN, "taxi_park": "Jana", "ct": 5,
             "a_out": "77070000003", "b_out": "77071112233", "a_dialed": "77071112233"},
            {"conn_id": CONN_OUT, "taxi_park": None, "ct": "1",
             "a_out": "6341", "b_out": "77070000004", "a_dialed": ""},
        ])
        self.connection = mock.Mock()
        self.connection.cursor.side_effect = lambda: Cursor(self.db)
        patch = mock.patch.object(route.config, "connect_ro", return_value=self.connection)
        patch.start()
        self.addCleanup(patch.stop)
        patch_parks = mock.patch.object(parks.config, "connect_ro", return_value=self.connection)
        patch_parks.start()
        self.addCleanup(patch_parks.stop)

    def load(self, kind, call_id):
        return route.load(kind, call_id, oktell_query=self.oktell)

    def test_sales_incoming_has_client_and_dialed_line(self):
        self.assertEqual(self.load("imported_call", 1),
                         {"direction": "in", "client": "7010000001", "line": "7272000001"})
        self.oktell.assert_not_called()

    def test_sales_transfer_takes_the_leg_with_this_client(self):
        self.assertEqual(self.load("imported_call", 2),
                         {"direction": "out", "client": "7010000002", "line": "7272000003"})

    def test_journal_call_shows_number_of_linked_station_call(self):
        # Номер, набранный в журнале руками, уступает номеру из АТС.
        self.assertEqual(self.load("call", 2),
                         {"direction": "out", "client": "7010000002", "line": "7272000003"})

    def test_journal_call_without_station_link_shows_only_client(self):
        self.assertEqual(self.load("call", 1),
                         {"direction": None, "client": "7010000009", "line": None})

    def test_not_a_phone_is_not_shown(self):
        self.assertIsNone(self.load("call", 3))

    def test_szov_incoming_line_is_the_number_client_dialed(self):
        self.assertEqual(self.load("imported_call", 3),
                         {"direction": "in", "client": "7070000003", "line": "7071112233"})

    def test_szov_outgoing_has_no_own_number_in_oktell(self):
        # На A-стороне исходящего — внутренний номер оператора, а не номер компании.
        self.assertEqual(self.load("imported_call", 4),
                         {"direction": "out", "client": "7070000004", "line": None})

    def test_old_szov_row_without_source_tail_is_recognised_by_uuid(self):
        self.assertEqual(self.load("imported_call", 6)["line"], "7071112233")

    def test_route_reuses_taxi_park_request_to_oktell(self):
        parks.attach_taxi_parks([{"id": 3, "subject_kind": "imported_call"}],
                                oktell_query=self.oktell, wazzup_channels=lambda: [])
        self.load("imported_call", 3)
        self.oktell.assert_called_once()

    def test_tez_direction_from_binotel_mirror_without_line(self):
        self.assertEqual(self.load("imported_call", 5),
                         {"direction": "out", "client": "7470000005", "line": None})

    def test_call_missing_in_mirror_keeps_client(self):
        self.assertEqual(self.load("imported_call", 7),
                         {"direction": None, "client": "7470000007", "line": None})

    def test_oktell_offline_leaves_client_number(self):
        self.oktell.side_effect = RuntimeError("offline")
        with self.assertLogs(level="WARNING"):
            self.assertEqual(self.load("imported_call", 3),
                             {"direction": None, "client": "7070000003", "line": None})

    def test_database_failure_does_not_break_the_card(self):
        self.connection.cursor.side_effect = RuntimeError("db down")
        with self.assertLogs(level="WARNING"):
            self.assertIsNone(self.load("imported_call", 1))

    def test_chats_and_unknown_subjects_have_no_route(self):
        for kind in ("wz_episode", "c2d_snapshot", "ca_episode", "bogus"):
            self.assertIsNone(self.load(kind, 1))
        self.connection.cursor.assert_not_called()

    def test_missing_subject_has_no_route(self):
        self.assertIsNone(self.load("imported_call", 404))


class HelpersTests(unittest.TestCase):
    def test_phone_keeps_ten_kazakh_digits_and_foreign_numbers(self):
        self.assertEqual(route.phone("+7 (701) 234-56-78"), "7012345678")
        self.assertEqual(route.phone("87012345678"), "7012345678")
        self.assertEqual(route.phone("7012345678"), "7012345678")
        self.assertEqual(route.phone("996555123456"), "996555123456")
        self.assertIsNone(route.phone("6341"))
        self.assertIsNone(route.phone("chat_15"))
        self.assertIsNone(route.phone(None))

    def test_source_by_import_tail_then_by_identifier(self):
        self.assertEqual(route.source_of("aiqa:auto:cdr", "x"), "cdr")
        self.assertEqual(route.source_of("random:12:binotel", "x"), "binotel")
        self.assertEqual(route.source_of("distribution:oktell", "x"), "oktell")
        self.assertEqual(route.source_of("", CONN_IN), "oktell")
        self.assertEqual(route.source_of("random:5", "1729000000.11"), "cdr")
        self.assertEqual(route.source_of(None, "9876543"), "binotel")
        self.assertIsNone(route.source_of(None, "abc"))
        self.assertIsNone(route.source_of("", ""))


if __name__ == "__main__":
    unittest.main()
