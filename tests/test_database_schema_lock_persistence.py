"""Opt-in PostgreSQL lock regression tests, using only an explicit loopback port.

Run against a disposable local cluster with OTP_SCHEMA_LOCK_TEST_PORT set.
No production configuration or database module imports are used. Each test owns
and removes a randomly named schema; the rest of the database is untouched.
"""
import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from pathlib import Path
import time
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import extensions, sql


TEST_PORT = os.environ.get("OTP_SCHEMA_LOCK_TEST_PORT")
pytestmark = pytest.mark.skipif(
    not TEST_PORT, reason="Set OTP_SCHEMA_LOCK_TEST_PORT for a disposable local PostgreSQL cluster"
)
ROOT = Path(__file__).resolve().parents[1]


class _PrefixComplete(BaseException):
    """Stop before the backfill, without making _get_cursor roll back the prefix."""


def _load_startup_methods():
    source = ROOT / "database.py"
    parsed = ast.parse(source.read_text(encoding="utf-8-sig"))
    database = next(node for node in parsed.body if isinstance(node, ast.ClassDef) and node.name == "Database")
    methods = [node for node in database.body
               if isinstance(node, ast.FunctionDef) and node.name in {"_get_cursor", "_init_db"}]
    assert len(methods) == 2
    selected = ast.ClassDef(name="StartupPrefix", bases=[], keywords=[], body=methods, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[selected], type_ignores=[]))
    namespace = {"contextmanager": contextmanager}
    exec(compile(module, str(source), "exec"), namespace)
    return namespace["StartupPrefix"]


class _PrefixCursor:
    def __init__(self, real, statements):
        self.real = real
        self.statements = statements

    def execute(self, query, params=None):
        normalized = " ".join(query.split())
        self.statements.append(normalized)
        if normalized.startswith("WITH RECURSIVE chain AS"):
            raise _PrefixComplete()
        return self.real.execute(query, params)

    def fetchone(self):
        return self.real.fetchone()

    def close(self):
        self.real.close()


class _TrackedConnection:
    def __init__(self, real):
        self.real = real
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return _PrefixCursor(self.real.cursor(), self.statements)

    def commit(self):
        self.commits += 1
        self.real.commit()

    def rollback(self):
        self.rollbacks += 1
        self.real.rollback()


class _Cluster:
    def __init__(self, port):
        self.port = port
        self.schema = "otp_schema_lock_" + uuid.uuid4().hex
        self.connections = []
        self.admin = self.connect(autocommit=True)
        with self.admin.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))

    def connect(self, *, autocommit=False):
        conn = psycopg2.connect(
            host="127.0.0.1", port=self.port, dbname="postgres", user="postgres", password="",
            connect_timeout=3, application_name="otp-schema-lock-regression",
            options="-c statement_timeout=5000 -c search_path=" + self.schema,
        )
        conn.autocommit = autocommit
        self.connections.append(conn)
        return conn

    def create_directions(self, *, canonical):
        with self.admin.cursor() as cursor:
            cursor.execute("CREATE TABLE departments (id serial PRIMARY KEY)")
            cursor.execute("""CREATE TABLE directions (
                id serial PRIMARY KEY, name text NOT NULL,
                previous_version_id integer, is_active boolean NOT NULL DEFAULT true)""")
            if canonical:
                cursor.execute("ALTER TABLE directions ADD COLUMN canonical_id integer REFERENCES directions(id)")
            cursor.execute("INSERT INTO directions(name) VALUES ('synthetic fixture')")

    def hold_reader(self):
        reader = self.connect()
        with reader.cursor() as cursor:
            cursor.execute("SELECT * FROM directions")
            assert cursor.fetchall()
        return reader

    def startup(self):
        tracked = _TrackedConnection(self.connect())
        startup = _load_startup_methods()()
        startup.SCHEMA_DDL_LOCK_TIMEOUT_MS = 1500

        @contextmanager
        def connection():
            yield tracked

        startup._get_connection = connection
        return startup, tracked

    def has_canonical(self):
        with self.admin.cursor() as cursor:
            cursor.execute("""SELECT EXISTS (
                SELECT 1 FROM pg_attribute WHERE attrelid = 'directions'::regclass
                AND attname = 'canonical_id' AND NOT attisdropped)""")
            return cursor.fetchone()[0]

    def wait_for_lock(self, pid, mode):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with self.admin.cursor() as cursor:
                cursor.execute("""SELECT EXISTS (
                    SELECT 1 FROM pg_locks WHERE pid = %s AND mode = %s AND NOT granted
                    AND relation = 'directions'::regclass)""", (pid, mode))
                if cursor.fetchone()[0]:
                    return
            time.sleep(0.01)
        pytest.fail("Expected a queued %s on directions" % mode)

    def close(self):
        for conn in self.connections:
            if conn is not self.admin:
                conn.close()
        try:
            with self.admin.cursor() as cursor:
                cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        finally:
            self.admin.close()


@pytest.fixture
def cluster():
    port = int(TEST_PORT)
    assert 1024 <= port <= 65535, "Use an explicit unprivileged local test port"
    instance = _Cluster(port)
    try:
        yield instance
    finally:
        instance.close()


def _run_prefix(startup, tracked):
    # _init_db is the actual extracted method. The only intercepted statement is
    # the backfill boundary. Commit the successfully executed prefix explicitly;
    # real PostgreSQL exceptions go through the actual _get_cursor rollback path.
    try:
        startup._init_db()
    except _PrefixComplete:
        tracked.commit()
        return
    pytest.fail("Startup no longer reached the expected canonical backfill boundary")


def _select_canary(conn):
    with conn.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM directions")
        result = cursor.fetchone()[0]
    conn.rollback()
    return result


def test_existing_canonical_column_skips_exclusive_lock_while_reader_is_open(cluster):
    cluster.create_directions(canonical=True)
    reader = cluster.hold_reader()
    startup, tracked = cluster.startup()
    _run_prefix(startup, tracked)

    assert reader.get_transaction_status() == extensions.TRANSACTION_STATUS_INTRANS
    assert not any(statement.startswith("ALTER TABLE directions") for statement in tracked.statements)
    assert tracked.statements[-1].startswith("WITH RECURSIVE chain AS")
    assert tracked.commits == 1
    assert tracked.rollbacks == 0
    assert cluster.has_canonical()


def test_missing_column_timeout_releases_read_queue_and_retry_persists_column(cluster):
    cluster.create_directions(canonical=False)
    reader = cluster.hold_reader()
    startup, tracked = cluster.startup()
    canary = cluster.connect()

    with ThreadPoolExecutor(max_workers=2) as executor:
        migration = executor.submit(_run_prefix, startup, tracked)
        cluster.wait_for_lock(tracked.real.get_backend_pid(), "AccessExclusiveLock")
        probe = executor.submit(_select_canary, canary)
        cluster.wait_for_lock(canary.get_backend_pid(), "AccessShareLock")
        with pytest.raises(psycopg2.Error) as caught:
            migration.result(timeout=5)
        assert caught.value.pgcode == "55P03"
        assert probe.result(timeout=3) == 1

    # The original reader still holds its transaction: it is the failed DDL's
    # rollback, rather than releasing the reader, that unblocked the canary.
    assert reader.get_transaction_status() == extensions.TRANSACTION_STATUS_INTRANS
    assert tracked.rollbacks == 1
    assert tracked.commits == 0
    assert tracked.real.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
    assert not cluster.has_canonical()

    reader.rollback()
    _run_prefix(startup, tracked)
    assert tracked.commits == 1
    assert tracked.rollbacks == 1
    assert cluster.has_canonical()
    assert tracked.real.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
    with tracked.real.cursor() as cursor:
        cursor.execute("SHOW lock_timeout")
        assert cursor.fetchone()[0] == "0", "Transaction-local DDL timeout must not leak after commit"
    tracked.real.rollback()
