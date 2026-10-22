"""Startup must release all schema locks before retrying a blocked migration.

Load selected methods without importing database.py: importing the monolith
initializes the application pool and must never happen in these unit tests.
"""
import ast
import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import psycopg2
import psycopg2.errors
import pytest

from tests import source_cache


PATH = Path(__file__).resolve().parents[1] / 'database.py'


def pg_error(code):
    # Locally constructed psycopg2 errors lack a server-supplied SQLSTATE.
    cls = type('ServerError', (psycopg2.errors.lookup(code),), {
        'pgcode': property(lambda self: code),
    })
    return cls('simulated PostgreSQL error ' + code)


def database_class(events):
    methods = [source_cache.function_copy(PATH, name, 'Database') for name in (
        '_get_cursor', '_init_db_with_retry', '_init_wiki_schema_tx',
    )]
    original = next(node for node in source_cache.tree(PATH).body
                    if isinstance(node, ast.ClassDef) and node.name == 'Database')
    attempts = next(node for node in original.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name)
                            and target.id == 'SCHEMA_INIT_RETRY_ATTEMPTS'
                            for target in node.targets))
    cls = ast.ClassDef(name='Database', bases=[], keywords=[],
                       body=[attempts, *methods], decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[
        source_cache.function_copy(PATH, '_is_schema_lock_conflict'), cls,
    ], type_ignores=[]))
    namespace = dict(contextmanager=contextmanager, psycopg2=psycopg2,
                     logging=logging, time=SimpleNamespace(
                         sleep=lambda delay: events.append(('sleep', delay))))
    exec(compile(module, str(PATH), 'exec'), namespace)
    return namespace['Database']


class Connection:
    def __init__(self, events):
        self.events = events

    def cursor(self):
        return SimpleNamespace(
            execute=lambda sql: self.events.append(sql),
            close=lambda: self.events.append('cursor.close'),
        )

    def commit(self):
        self.events.append('commit')

    def rollback(self):
        self.events.append('rollback')


def database(events):
    db = database_class(events)()

    @contextmanager
    def connection():
        yield Connection(events)

    @contextmanager
    def schema_lock():
        events.append('advisory.acquire')
        try:
            yield
        finally:
            events.append('advisory.release')

    db._get_connection = connection
    db._schema_init_lock = schema_lock
    db._reset_pool_after_transient_error = lambda: events.append('pool.reset')
    return db


def init_outcomes(db, events, outcomes):
    outcomes = iter(outcomes)

    def init():
        events.append('init')
        with db._get_cursor():
            error = next(outcomes)
            if error is not None:
                raise error

    db._init_db = init


@pytest.mark.parametrize('code', ['55P03', '40P01'])
def test_lock_conflict_rolls_back_and_unlocks_before_retry_without_resetting_pool(code):
    events = []
    db = database(events)
    init_outcomes(db, events, [pg_error(code), None])
    db._init_db_with_retry()
    assert events == [
        'advisory.acquire', 'init', 'rollback', 'cursor.close',
        'advisory.release', ('sleep', 1.0),
        'advisory.acquire', 'init', 'commit', 'cursor.close', 'advisory.release',
    ]


@pytest.mark.parametrize('error_type', [psycopg2.OperationalError, psycopg2.InterfaceError])
def test_connection_failure_still_resets_pool_before_retry(error_type):
    events = []
    db = database(events)
    init_outcomes(db, events, [error_type('connection lost'), None])
    db._init_db_with_retry()
    assert events.count('pool.reset') == 1
    assert events.index('rollback') < events.index('pool.reset') < events.index(('sleep', 1.0))
    assert events.count('init') == 2
    assert events.count('commit') == 1


def test_permanent_schema_error_is_rolled_back_without_retry():
    events = []
    db = database(events)
    error = pg_error('42601')
    init_outcomes(db, events, [error])
    with pytest.raises(psycopg2.ProgrammingError) as caught:
        db._init_db_with_retry()
    assert caught.value is error
    assert events == ['advisory.acquire', 'init', 'rollback', 'cursor.close', 'advisory.release']


@pytest.mark.parametrize('code', ['55P03', '40P01'])
def test_persistent_lock_conflict_exhausts_bounded_retries_and_preserves_error(code):
    events = []
    db = database(events)
    error = pg_error(code)
    attempts = db.SCHEMA_INIT_RETRY_ATTEMPTS
    init_outcomes(db, events, [error] * attempts)
    with pytest.raises(psycopg2.Error) as caught:
        db._init_db_with_retry()
    assert caught.value is error
    assert events.count('rollback') == attempts
    assert events.count('advisory.release') == attempts
    assert [event for event in events if isinstance(event, tuple)] == [
        ('sleep', min(5.0, float(attempt))) for attempt in range(1, attempts)
    ]
    assert 'pool.reset' not in events and 'commit' not in events
    assert events[-1] == 'advisory.release'


@pytest.mark.parametrize('code', ['55P03', '40P01', None])
def test_optional_schema_only_isolates_non_lock_errors(monkeypatch, code):
    events = []
    db = database(events)
    error = pg_error(code) if code else ValueError('optional feature unavailable')
    schema = ModuleType('wiki.schema')

    def fail(cursor):
        raise error

    schema.init_wiki_schema = fail
    monkeypatch.setitem(sys.modules, 'wiki.schema', schema)

    def init():
        with db._get_cursor() as cursor:
            cursor.execute('earlier schema changes')
            db._init_wiki_schema_tx(cursor)

    if code:
        with pytest.raises(psycopg2.Error) as caught:
            init()
        assert caught.value is error
        assert 'rollback' in events and 'commit' not in events
        assert 'ROLLBACK TO SAVEPOINT wiki_schema' not in events
    else:
        init()
        assert 'ROLLBACK TO SAVEPOINT wiki_schema' in events
        assert 'commit' in events and 'rollback' not in events
    assert events[-1] == 'cursor.close'


@pytest.mark.parametrize('module_name,savepoint', [
    ('baiga', 'baiga_trgm'), ('parcels', 'parcels_trgm'),
])
@pytest.mark.parametrize('code', ['55P03', '40P01', '42501'])
def test_optional_extension_lock_conflict_propagates_but_permission_error_is_local(module_name, savepoint, code):
    from importlib import import_module

    module = import_module(module_name + '.schema')
    statements = []
    error = pg_error(code)

    def execute(sql):
        statements.append(sql)
        if sql == 'CREATE EXTENSION IF NOT EXISTS pg_trgm':
            raise error

    cursor = SimpleNamespace(execute=execute)
    init = getattr(module, 'init_' + module_name + '_schema')
    if code == '42501':
        init(cursor)
        assert 'ROLLBACK TO SAVEPOINT ' + savepoint in statements
    else:
        with pytest.raises(psycopg2.Error) as caught:
            init(cursor)
        assert caught.value is error
        assert 'ROLLBACK TO SAVEPOINT ' + savepoint not in statements


@pytest.mark.parametrize('code', ['55P03', '40P01', None])
def test_legacy_backfill_stops_on_lock_conflict_but_skips_malformed_row(monkeypatch, code):
    from payments import legacy

    monkeypatch.setattr(legacy, '_encrypt_cards', lambda cursor: 0)
    monkeypatch.setattr(legacy, 'rotate_cards', lambda cursor: 0)
    monkeypatch.setattr(legacy, 'pending', lambda cursor: [1, 2])
    visited, statements = [], []
    error = pg_error(code) if code else ValueError('malformed legacy row')

    def migrate_one(cursor, request_id):
        visited.append(request_id)
        if request_id == 1:
            raise error

    monkeypatch.setattr(legacy, '_migrate_one', migrate_one)
    cursor = SimpleNamespace(execute=statements.append)
    if code:
        with pytest.raises(psycopg2.Error) as caught:
            legacy.migrate(cursor)
        assert caught.value is error
        assert visited == [1]
        assert 'ROLLBACK TO SAVEPOINT payments_legacy_one' not in statements
    else:
        assert legacy.migrate(cursor) == 1
        assert visited == [1, 2]
        assert 'ROLLBACK TO SAVEPOINT payments_legacy_one' in statements
