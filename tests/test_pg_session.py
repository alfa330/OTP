"""Пояс сессии PostgreSQL закреплён в UTC у каждого соединения портала (common/pg_session.py).

Повод — 24.11.2026: копия прода на Managed PostgreSQL в Yandex Cloud отдавала TIMESTAMPTZ с
`+03:00` (пояс сессии по умолчанию — Europe/Moscow), Render — с `+00:00`. Код считает базу в UTC,
поэтому пояс ставит само соединение. Живая проверка — если задан порт временного Postgres
(OTP_FRESH_SCHEMA_TEST_PORT, как у tests/test_schema_fresh_database.py)."""
import os
import re
import unittest
from pathlib import Path

from common import pg_session

ROOT = Path(__file__).resolve().parent.parent


class _FakeCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        self.log.append(('execute', sql, params))

    def close(self):
        self.log.append(('close',))


class _FakeConnection:
    def __init__(self):
        self.log = []

    def cursor(self):
        return _FakeCursor(self.log)

    def commit(self):
        self.log.append(('commit',))


class PinSessionTimeZoneTest(unittest.TestCase):
    def test_sets_utc_and_commits_right_away(self):
        """Без COMMIT пояс откатился бы с первой же упавшей транзакцией соединения пула."""
        conn = _FakeConnection()
        pg_session.pin_session_time_zone(conn)
        self.assertEqual(conn.log, [('execute', 'SET TIME ZONE %s', ('UTC',)), ('close',), ('commit',)])

    def test_connection_factory_is_a_psycopg2_connection(self):
        import psycopg2.extensions
        self.assertTrue(issubclass(pg_session.UtcConnection, psycopg2.extensions.connection))


class EveryConnectionPathUsesItTest(unittest.TestCase):
    """Все места, где портал открывает соединения: пул, отдельные соединения LISTEN, «ИИ-оценка»."""

    def test_pool(self):
        source = (ROOT / 'db' / 'core.py').read_text(encoding='utf-8')
        body = source[source.index('def get_pool('):]
        body = body[:body.index('return POOL')]
        self.assertIn("'connection_factory': UtcConnection", body)
        self.assertIn('ThreadedConnectionPool(MIN_CONN, MAX_CONN, **conn_params)', body)

    def test_listen_and_auction_connections(self):
        source = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        body = source[source.index('def _build_postgres_connection_params('):]
        body = body[:body.index('\ndef ')]
        self.assertIn("'connection_factory': UtcConnection", body)
        # Каждое отдельное соединение строится из этих параметров, а не своим набором.
        for match in re.finditer(r'psycopg2\.connect\(([^)]*)\)', source):
            self.assertIn('_build_postgres_connection_params(', match.group(1), match.group(0))

    def test_call_qa(self):
        source = (ROOT / 'call_qa' / 'config.py').read_text(encoding='utf-8')
        connects = re.findall(r'psycopg2\.connect\([^)]*\)', source)
        self.assertTrue(connects)
        for call in connects:
            self.assertIn('connection_factory=UtcConnection', call)


@unittest.skipUnless(os.environ.get('OTP_FRESH_SCHEMA_TEST_PORT'), 'нужен временный Postgres')
class LivePostgresTest(unittest.TestCase):
    def test_session_is_utc_even_when_server_default_is_not(self):
        import psycopg2
        from psycopg2.pool import ThreadedConnectionPool
        params = dict(host='127.0.0.1', port=int(os.environ['OTP_FRESH_SCHEMA_TEST_PORT']),
                      user='postgres', dbname='postgres')
        pool = ThreadedConnectionPool(1, 2, connection_factory=pg_session.UtcConnection, **params)
        try:
            conn = pool.getconn()
            cur = conn.cursor()
            with self.assertRaises(psycopg2.Error):
                cur.execute('SELECT 1/0')
            conn.rollback()
            cur.execute("SHOW timezone")
            self.assertEqual(cur.fetchone()[0], 'UTC')
            cur.execute("SELECT TIMESTAMPTZ '2026-11-20 15:44:00+00'::text")
            self.assertEqual(cur.fetchone()[0], '2026-11-20 15:44:00+00')
        finally:
            pool.closeall()


if __name__ == '__main__':
    unittest.main()
