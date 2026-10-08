"""Real template SQL against an explicitly chosen disposable local PostgreSQL.

Only 127.0.0.1 and WAZZUP_PILOT_TEST_PORT are accepted; app configuration and
production credentials are never loaded. Every test removes its own schema.
"""
import ast
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from flask import Blueprint, Flask

from tests import source_cache
from wazzup import templates


PORT = os.environ.get('WAZZUP_PILOT_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')
ROOT = Path(__file__).resolve().parents[1]


def _transaction_database():
    """Exercise the actual commit/rollback boundary without importing the app."""
    module = source_cache.parse((ROOT / 'database.py').read_text(encoding='utf-8-sig'))
    database = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == 'Database')
    method = next(node for node in database.body if isinstance(node, ast.FunctionDef) and node.name == '_get_cursor')
    source = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name='Database', bases=[], keywords=[], body=[method], decorator_list=[])], type_ignores=[]))
    namespace = {'contextmanager': contextmanager}
    exec(compile(source, str(ROOT / 'database.py'), 'exec'), namespace)
    return namespace['Database']


@pytest.fixture
def pg(monkeypatch):
    import psycopg2
    from psycopg2 import sql

    schema = 't_wazzup_templates_' + uuid.uuid4().hex
    connections = []
    database_class = _transaction_database()

    def connect():
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres',
                                dbname='postgres', connect_timeout=3)
        connections.append(conn)
        with conn.cursor() as cur:
            cur.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            cur.execute("SET statement_timeout TO '5s'")
        conn.commit()
        return conn

    connection = connect()
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        templates.init_template_schema(cursor)
    connection.commit()
    monkeypatch.setattr(templates, 'list_templates', lambda *args, **kwargs: dict(
        items=[], stale=False, sourceWarnings=[]))

    def client(conn=connection, before_commit=None):
        db = database_class()

        class Connection:
            cursor = conn.cursor
            rollback = conn.rollback

            def commit(self):
                if before_commit:
                    before_commit()
                conn.commit()

        @contextmanager
        def get_connection():
            yield Connection()

        db._get_connection = get_connection
        app = Flask(__name__)
        app.testing = True
        bp = Blueprint('templates-persistence', __name__, url_prefix='/pilot')
        templates.register_template_routes(bp, lambda: ([42, None, None, 'super_admin'], None),
                                          lambda function: function, lambda: ('', 204), db)
        app.register_blueprint(bp)
        return app.test_client()

    try:
        yield connection, connect, client
    finally:
        for other in connections[1:]:
            other.close()
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        connection.commit()
        connection.close()


def item(external_id='reply-1', **changes):
    return dict(dict(externalId=external_id, title='Ответ ' + external_id,
                     text='Первая строка\nВторая -> строка', files=[]), **changes)


def import_items(client, items, dry_run=False):
    return client.post('/pilot/templates/import', json=dict(account='op', dryRun=dry_run, items=items))


def snapshot(conn):
    with conn.cursor() as cursor:
        cursor.execute('SELECT id,account,external_id,title,body,attachment_count,imported_by,imported_at '
                       'FROM wazzup_imported_templates ORDER BY account,external_id')
        imported = cursor.fetchall()
        cursor.execute('SELECT * FROM wazzup_quick_templates ORDER BY id')
        local = cursor.fetchall()
    conn.commit()
    return imported, local


def assert_waiting_for_import_lock(observer, worker):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with observer.cursor() as cursor:
            cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=%s "
                           "AND locktype='advisory' AND NOT granted)", (worker.get_backend_pid(),))
            waiting = cursor.fetchone()[0]
        observer.commit()
        if waiting:
            return
        time.sleep(.01)
    pytest.fail('the concurrent import did not wait for the transaction advisory lock')


def test_schema_twice_preserves_both_catalogues_and_database_uniqueness(pg):
    import psycopg2

    conn, _, make_client = pg
    client = make_client()
    assert client.post('/pilot/templates', json=dict(title='Локальный', text='Привет')).status_code == 201
    assert import_items(client, [item()]).json['created'] == 1
    before = snapshot(conn)
    with conn.cursor() as cursor:
        templates.init_template_schema(cursor)
        templates.init_template_schema(cursor)
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() ORDER BY tablename")
        assert cursor.fetchall() == [('wazzup_imported_templates',), ('wazzup_quick_templates',)]
        cursor.execute("SELECT indexdef FROM pg_indexes WHERE schemaname=current_schema() "
                       "AND tablename='wazzup_imported_templates'")
        definitions = [row[0] for row in cursor.fetchall()]
        assert any('UNIQUE' in value and '(account, external_id)' in value for value in definitions)
    conn.commit()
    assert snapshot(conn) == before
    with pytest.raises(psycopg2.errors.UniqueViolation), conn.cursor() as cursor:
        cursor.execute("INSERT INTO wazzup_imported_templates "
                       "(id,account,external_id,title,body,imported_by) VALUES(%s,'op','reply-1','Duplicate','Text',42)",
                       (str(uuid.uuid4()),))
    conn.rollback()
    assert snapshot(conn) == before


def test_dry_run_and_unchanged_reimport_never_rewrite_rows_or_audit_fields(pg):
    conn, _, make_client = pg
    client = make_client()
    initial = snapshot(conn)
    dry = import_items(client, [item()], dry_run=True)
    assert dry.status_code == 200 and dry.json['created'] == 1 and dry.json['dryRun'] is True
    assert snapshot(conn) == initial
    assert import_items(client, [item()]).json['created'] == 1
    before = snapshot(conn)
    changed = {**item(), 'text': 'Исправленный текст'}
    preview = import_items(client, [changed, item('reply-2')], dry_run=True)
    assert preview.json['updated'] == 1 and preview.json['created'] == 1
    assert snapshot(conn) == before
    again = import_items(client, [item()])
    assert again.json['unchanged'] == 1 and again.json['created'] == 0 and again.json['updated'] == 0
    assert snapshot(conn) == before


def test_sql_failure_rolls_back_all_prior_rows_in_the_import(pg):
    import psycopg2

    conn, _, make_client = pg
    client = make_client()
    assert import_items(client, [item()]).status_code == 200
    with conn.cursor() as cursor:
        cursor.execute("ALTER TABLE wazzup_imported_templates ADD CONSTRAINT reject_test_marker "
                       "CHECK (body <> '__forced_database_failure__')")
    conn.commit()
    before = snapshot(conn)
    changed = {**item(), 'text': 'This update must be rolled back'}
    added = item('added-before-failure')
    failed = {**item('fails-last'), 'text': '__forced_database_failure__'}
    with pytest.raises(psycopg2.errors.CheckViolation):
        import_items(client, [changed, added, failed])
    assert snapshot(conn) == before
    assert import_items(client, [item('after-rollback')]).json['created'] == 1


def test_concurrent_upserts_serialize_counts_and_preserve_existing_ids(pg):
    conn, connect, make_client = pg
    assert import_items(make_client(), [item()]).status_code == 200
    original_id = snapshot(conn)[0][0][0]
    first, second = connect(), connect()
    before_commit, release = threading.Event(), threading.Event()

    def gate():
        before_commit.set()
        assert release.wait(4), 'test must release the first transaction'

    package = [{**item(), 'text': 'Updated in concurrent import'}, item('reply-2')]
    with ThreadPoolExecutor(max_workers=2) as executor:
        one = executor.submit(import_items, make_client(first, before_commit=gate), package)
        try:
            assert before_commit.wait(2)
            two = executor.submit(import_items, make_client(second), package)
            assert_waiting_for_import_lock(conn, second)
        finally:
            release.set()
        result_one, result_two = one.result(timeout=5), two.result(timeout=5)
    assert result_one.status_code == result_two.status_code == 200
    assert (result_one.json['created'], result_one.json['updated'], result_one.json['unchanged']) == (1, 1, 0)
    assert (result_two.json['created'], result_two.json['updated'], result_two.json['unchanged']) == (0, 0, 2)
    saved = snapshot(conn)[0]
    assert len(saved) == 2 and len({row[0] for row in saved}) == 2
    assert saved[0][0] == original_id and saved[0][4] == package[0]['text']
    assert import_items(make_client(), package).json['unchanged'] == 2
    assert snapshot(conn)[0] == saved


def test_concurrent_imports_cannot_exceed_catalogue_capacity(pg, monkeypatch):
    conn, connect, make_client = pg
    monkeypatch.setattr(templates, 'MAX_IMPORTED', 2)
    assert import_items(make_client(), [item()]).status_code == 200
    first, second = connect(), connect()
    before_commit, release = threading.Event(), threading.Event()

    def gate():
        before_commit.set()
        assert release.wait(4), 'test must release the first transaction'

    with ThreadPoolExecutor(max_workers=2) as executor:
        one = executor.submit(import_items, make_client(first, before_commit=gate), [item('reply-2')])
        try:
            assert before_commit.wait(2)
            two = executor.submit(import_items, make_client(second), [item('reply-3')])
            assert_waiting_for_import_lock(conn, second)
        finally:
            release.set()
        result_one, result_two = one.result(timeout=5), two.result(timeout=5)
    assert result_one.status_code == 200 and result_one.json['created'] == 1
    assert result_two.status_code == 409
    assert [row[2] for row in snapshot(conn)[0]] == ['reply-1', 'reply-2']
