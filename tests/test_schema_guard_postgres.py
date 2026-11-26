# -*- coding: utf-8 -*-
"""Курсор миграции на настоящем Postgres: та же схема, те же ошибки, без лишних блокировок.

Opt-in, как соседние тесты блокировок: OTP_SCHEMA_LOCK_TEST_PORT — порт одноразового
локального кластера (initdb -A trust). Каждый тест заводит и удаляет свои схемы.
Полный прогон _init_db на готовой схеме — OTP_FRESH_SCHEMA_TEST_PORT (создаёт свою базу).

Каждая форма проверяется парой одинаковых схем: в одной оператор идёт как написан, в другой —
через GuardedSchemaCursor. Каталог после обеих обязан совпасть, ошибка — с тем же SQLSTATE,
а на уже применённой схеме обёрнутый оператор не держит ни одной сильной блокировки таблицы.
"""
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip('psycopg2')
from psycopg2 import sql as pg_sql  # noqa: E402

from db import schema_guard  # noqa: E402

PORT = os.environ.get('OTP_SCHEMA_LOCK_TEST_PORT')
FRESH_PORT = os.environ.get('OTP_FRESH_SCHEMA_TEST_PORT')
ROOT = Path(__file__).resolve().parents[1]
STRONG = ('ShareLock', 'ShareRowExclusiveLock', 'ExclusiveLock', 'AccessExclusiveLock')


def _connect(port, schema=None, dbname='postgres'):
    options = '-c statement_timeout=10000' + (' -c search_path=' + schema if schema else '')
    conn = psycopg2.connect(host='127.0.0.1', port=int(port), user='postgres', password='', dbname=dbname,
                            connect_timeout=3, application_name='otp-schema-guard-test', options=options)
    return conn


class _Twins:
    """Две схемы с одинаковой подготовкой: «как написано» и «через курсор миграции»."""

    def __init__(self, port):
        self.port = port
        self.admin = _connect(port)
        self.admin.autocommit = True
        suffix = uuid.uuid4().hex[:10]
        self.schemas = {'plain': 'otp_guard_plain_' + suffix, 'guarded': 'otp_guard_g_' + suffix}
        with self.admin.cursor() as cur:
            for name in self.schemas.values():
                cur.execute(pg_sql.SQL('CREATE SCHEMA {}').format(pg_sql.Identifier(name)))

    def setup(self, sql):
        for name in self.schemas.values():
            conn = _connect(self.port, name)
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
            conn.close()

    def run(self, kind, sql, lock_timeout_ms=None):
        """(SQLSTATE ошибки или None, сильные блокировки таблиц схемы до коммита)."""
        schema = self.schemas[kind]
        conn = _connect(self.port, schema)
        factory = schema_guard.GuardedSchemaCursor if kind == 'guarded' else None
        cur = conn.cursor(cursor_factory=factory) if factory else conn.cursor()
        try:
            if lock_timeout_ms:
                cur.execute("SELECT set_config('lock_timeout', %s, true)", ('%dms' % lock_timeout_ms,))
            cur.execute(sql)
        except psycopg2.Error as exc:
            conn.rollback()
            conn.close()
            return exc.pgcode, set()
        probe = conn.cursor()
        probe.execute("""
            SELECT c.relname, l.mode FROM pg_locks l
              JOIN pg_class c ON c.oid = l.relation
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE l.pid = pg_backend_pid() AND l.granted AND n.nspname = %s AND l.mode = ANY(%s)
        """, (schema, list(STRONG)))
        locks = set(probe.fetchall())
        conn.commit()
        conn.close()
        return None, locks

    def snapshot(self, kind):
        schema = self.schemas[kind]
        cur = self.admin.cursor()
        out = {}
        queries = {
            'columns': """SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
                                 pg_get_expr(d.adbin, d.adrelid), a.attidentity
                            FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
                            JOIN pg_namespace n ON n.oid = c.relnamespace
                            LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                           WHERE n.nspname = %s AND a.attnum > 0 AND NOT a.attisdropped AND c.relkind = 'r'
                           ORDER BY 1, 2""",
            'constraints': """SELECT c.relname, con.conname, con.contype, pg_get_constraintdef(con.oid)
                                FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
                                JOIN pg_namespace n ON n.oid = c.relnamespace
                               WHERE n.nspname = %s ORDER BY 1, 2""",
            'indexes': """SELECT tablename, indexname, indexdef FROM pg_indexes
                           WHERE schemaname = %s ORDER BY 1, 2""",
            'triggers': """SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid), t.tgenabled
                             FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
                             JOIN pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = %s AND NOT t.tgisinternal ORDER BY 1, 2""",
        }
        for key, query in queries.items():
            cur.execute(query, (schema,))
            out[key] = [tuple(re.sub(re.escape(schema), 'S', str(v)) for v in row) for row in cur.fetchall()]
        return out

    def close(self):
        with self.admin.cursor() as cur:
            for name in self.schemas.values():
                cur.execute(pg_sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(pg_sql.Identifier(name)))
        self.admin.close()


@pytest.fixture
def twins():
    if not PORT:
        pytest.skip('OTP_SCHEMA_LOCK_TEST_PORT — порт одноразового локального Postgres')
    instance = _Twins(PORT)
    try:
        yield instance
    finally:
        instance.close()


BASE = """
    CREATE TABLE u (id serial PRIMARY KEY);
    CREATE TABLE t (id serial PRIMARY KEY, a text, n int NOT NULL DEFAULT 0, gen int GENERATED ALWAYS AS (id * 2) STORED,
                    ident int GENERATED BY DEFAULT AS IDENTITY,
                    CONSTRAINT t_a_check CHECK (a <> ''));
    CREATE INDEX idx_t_a ON t(a);
    CREATE FUNCTION f() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;
    CREATE TRIGGER trg_t AFTER INSERT ON t FOR EACH ROW EXECUTE FUNCTION f();
"""

# (оператор, пуст ли он на BASE). Для «не пустых» блокировки — дело Postgres, сверяем итог.
CASES = [
    ("ALTER TABLE t ADD COLUMN IF NOT EXISTS a text CHECK (a <> 'x')", True),
    ("ALTER TABLE t ADD COLUMN IF NOT EXISTS a text, ADD COLUMN IF NOT EXISTS n int", True),
    ("ALTER TABLE t ADD COLUMN IF NOT EXISTS a text, ADD COLUMN IF NOT EXISTS b int REFERENCES u(id) "
     "ON DELETE SET NULL, ADD COLUMN IF NOT EXISTS c varchar(5) NOT NULL DEFAULT 'x' CHECK (c IN ('x', 'y'))", False),
    ("ALTER TABLE t ADD IF NOT EXISTS s serial", False),
    ("ALTER TABLE t DROP COLUMN IF EXISTS nope", True),
    ("ALTER TABLE t DROP COLUMN IF EXISTS a CASCADE", False),
    ("ALTER TABLE t DROP CONSTRAINT IF EXISTS nope", True),
    ("ALTER TABLE t DROP CONSTRAINT IF EXISTS t_a_check", False),
    ("ALTER TABLE t ALTER COLUMN a DROP NOT NULL", True),
    ("ALTER TABLE t ALTER COLUMN n DROP NOT NULL", False),
    ("ALTER TABLE t ALTER COLUMN n SET NOT NULL", True),
    ("ALTER TABLE t ALTER COLUMN a SET NOT NULL", False),
    ("ALTER TABLE t ALTER COLUMN a DROP DEFAULT", True),
    ("ALTER TABLE t ALTER COLUMN n DROP DEFAULT", False),
    ("ALTER TABLE t ALTER COLUMN ident DROP DEFAULT", False),     # identity: ошибка у обоих
    ("ALTER TABLE t ALTER COLUMN gen DROP DEFAULT", False),       # generated: ошибка у обоих
    ("ALTER TABLE t ALTER COLUMN nope DROP NOT NULL", False),     # нет колонки: ошибка у обоих
    ("ALTER TABLE missing ADD COLUMN IF NOT EXISTS a int", False),  # нет таблицы: ошибка у обоих
    ("ALTER TABLE IF EXISTS missing ADD COLUMN IF NOT EXISTS a int", False),
    ("ALTER TABLE missing DROP COLUMN IF EXISTS a", False),
    ("ALTER TABLE missing DROP CONSTRAINT IF EXISTS k", False),
    ("ALTER TABLE missing ALTER COLUMN a DROP NOT NULL", False),
    ("DROP TRIGGER IF EXISTS trg ON missing", False),
    ("CREATE INDEX IF NOT EXISTS idx_t_a ON t(n)", True),  # имя занято — Postgres тоже пропускает
    ("CREATE UNIQUE INDEX IF NOT EXISTS uq_t_n ON t(n) WHERE n > 0", False),
    ("CREATE INDEX IF NOT EXISTS idx_missing ON missing(a)", False),
    ("DROP TRIGGER IF EXISTS nope ON t", True),
    ("DROP TRIGGER IF EXISTS trg_t ON t", False),
    ("ALTER TABLE t ADD COLUMN IF NOT EXISTS a text; ALTER TABLE t DROP COLUMN IF EXISTS a; "
     "ALTER TABLE t ADD COLUMN IF NOT EXISTS a int", False),  # решение — в момент исполнения, после соседей
    ("CREATE INDEX IF NOT EXISTS idx_t_a ON t(a); DROP INDEX idx_t_a; CREATE INDEX IF NOT EXISTS idx_t_a ON t(n)",
     False),
]


@pytest.mark.parametrize('statement,noop', CASES)
def test_guarded_statement_gives_the_same_schema_and_error(twins, statement, noop):
    twins.setup(BASE)
    plain_error, _ = twins.run('plain', statement)
    guarded_error, guarded_locks = twins.run('guarded', statement)
    assert guarded_error == plain_error
    assert twins.snapshot('guarded') == twins.snapshot('plain')
    if noop:
        assert plain_error is None
        assert guarded_locks == set(), 'пустой оператор взял блокировку: %s' % guarded_locks


def test_set_not_null_on_nulls_fails_the_same_way(twins):
    twins.setup(BASE + "INSERT INTO t (a) VALUES (NULL);")
    statement = 'ALTER TABLE t ALTER COLUMN a SET NOT NULL'
    assert twins.run('guarded', statement)[0] == twins.run('plain', statement)[0] == '23502'


def test_lock_timeout_inside_guard_keeps_its_sqlstate(twins):
    # Повтор миграции (_init_db_with_retry) различает конфликт по SQLSTATE 55P03.
    twins.setup(BASE)
    reader = _connect(twins.port, twins.schemas['guarded'])
    reader.cursor().execute('SELECT * FROM t')
    try:
        error, _ = twins.run('guarded', 'ALTER TABLE t ADD COLUMN IF NOT EXISTS fresh int', lock_timeout_ms=300)
        assert error == '55P03'
        # А пустой оператор под тем же читателем проходит: блокировка ему не нужна.
        error, locks = twins.run('guarded', 'ALTER TABLE t ADD COLUMN IF NOT EXISTS a int', lock_timeout_ms=300)
        assert error is None and locks == set()
    finally:
        reader.rollback()
        reader.close()


def test_trigger_pair_is_rebuilt_once_and_then_left_alone(twins):
    twins.setup(BASE)
    pair = ("DROP TRIGGER IF EXISTS trg_t ON t;\n"
            "CREATE TRIGGER trg_t AFTER INSERT OR UPDATE ON t FOR EACH ROW WHEN (NEW.n > 0) EXECUTE FUNCTION f();")
    assert twins.run('plain', pair)[0] is None
    error, first_locks = twins.run('guarded', pair)
    assert error is None and ('t', 'AccessExclusiveLock') in first_locks  # отпечатка не было — пересобран
    assert [row[:3] for row in twins.snapshot('guarded')['triggers']] == \
        [row[:3] for row in twins.snapshot('plain')['triggers']]
    error, locks = twins.run('guarded', pair)
    assert error is None and locks == set()
    changed = pair.replace('NEW.n > 0', 'NEW.n > 1')
    assert twins.run('plain', changed)[0] is None
    error, locks = twins.run('guarded', changed)
    assert error is None and ('t', 'AccessExclusiveLock') in locks
    assert twins.snapshot('guarded')['triggers'] == twins.snapshot('plain')['triggers']


CHECK_DDL = """
    ALTER TABLE t DROP CONSTRAINT IF EXISTS t_a_check;
    ALTER TABLE t ADD CONSTRAINT t_a_check CHECK (a IN ('x', 'y') OR a IS NULL);
"""


def _replace(twins, ddl):
    conn = _connect(twins.port, twins.schemas['guarded'])
    try:
        cur = conn.cursor(cursor_factory=schema_guard.GuardedSchemaCursor)
        rebuilt = schema_guard.replace_constraints(cur, 't', ('t_a_check',), ddl)
        probe = conn.cursor()
        probe.execute("""SELECT l.mode FROM pg_locks l WHERE l.pid = pg_backend_pid() AND l.granted
                          AND l.relation = to_regclass('t') AND l.mode = ANY(%s)""", (list(STRONG),))
        locks = {row[0] for row in probe.fetchall()}
        conn.commit()
    finally:
        conn.close()
    return rebuilt, locks


def _constraint_comment(twins):
    with twins.admin.cursor() as cur:
        cur.execute("""SELECT obj_description(oid, 'pg_constraint') FROM pg_constraint
                        WHERE conrelid = to_regclass(%s) AND conname = 't_a_check'""",
                    (twins.schemas['guarded'] + '.t',))
        return cur.fetchone()[0]


def test_replace_constraints_adopts_rebuilds_on_change_and_skips_otherwise(twins):
    # Как на проде до 26.11: ограничение поставлено этим же текстом, отпечатка нет.
    twins.setup(BASE + CHECK_DDL)
    assert _constraint_comment(twins) is None
    assert _replace(twins, CHECK_DDL) == (False, set())          # принято как есть, только COMMENT
    assert _constraint_comment(twins) == schema_guard.ddl_fingerprint(CHECK_DDL)
    assert _replace(twins, CHECK_DDL) == (False, set())          # отпечаток совпал
    changed = CHECK_DDL.replace("('x', 'y')", "('x', 'y', 'z')")
    rebuilt, locks = _replace(twins, changed)
    assert rebuilt and 'AccessExclusiveLock' in locks
    with twins.admin.cursor() as cur:
        cur.execute("""SELECT pg_get_constraintdef(oid), obj_description(oid, 'pg_constraint') FROM pg_constraint
                        WHERE conrelid = to_regclass(%s) AND conname = 't_a_check'""",
                    (twins.schemas['guarded'] + '.t',))
        definition, comment = cur.fetchone()
    assert "'z'" in definition and comment == schema_guard.ddl_fingerprint(changed)
    assert _replace(twins, changed) == (False, set())


def test_replace_constraints_does_not_adopt_a_different_check(twins):
    twins.setup(BASE + CHECK_DDL.replace("('x', 'y')", "('x')"))
    rebuilt, locks = _replace(twins, CHECK_DDL)
    assert rebuilt and 'AccessExclusiveLock' in locks


def test_replace_trigger_adopts_same_definition_and_rebuilds_changed(twins):
    twins.setup(BASE)
    conn = _connect(twins.port, twins.schemas['guarded'])
    try:
        cur = conn.cursor()
        same = 'CREATE TRIGGER trg_t\n    AFTER INSERT ON t\n    FOR EACH ROW\n    EXECUTE FUNCTION f();'
        assert schema_guard.replace_trigger(cur, 't', 'trg_t', same) is False   # принят, только COMMENT
        cur.execute("SELECT obj_description(oid, 'pg_trigger') FROM pg_trigger WHERE tgname = 'trg_t'"
                    " AND tgrelid = to_regclass('t')")
        assert cur.fetchone()[0] == schema_guard.ddl_fingerprint(same)
        assert schema_guard.replace_trigger(cur, 't', 'trg_t', same) is False   # отпечаток совпал
        assert schema_guard.replace_trigger(cur, 't', 'trg_t', same.replace('INSERT', 'UPDATE')) is True
        cur.execute("SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE tgname = 'trg_t'"
                    " AND tgrelid = to_regclass('t')")
        assert 'AFTER UPDATE' in cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()


# ── Настоящий _init_db на развёрнутой схеме ──────────────────────────────────

HOT_TABLES = ('users', 'user_sessions', 'directions', 'departments', 'work_hours', 'work_shifts',
              'operator_profiles', 'user_hr_profiles', 'szov_wallboard_snapshot')

_PROBE = r"""
import json, logging, sys, time
from contextlib import contextmanager
time.tzset = getattr(time, 'tzset', lambda: None)
import db.core
captured = []
orig = db.core.CoreMixin._get_cursor

@contextmanager
def probe(self, **kwargs):
    with orig(self, **kwargs) as cursor:
        yield cursor
        if 'cursor_factory' in kwargs:
            cursor.execute('''SELECT c.relname, l.mode FROM pg_locks l JOIN pg_class c ON c.oid = l.relation
                               WHERE l.pid = pg_backend_pid() AND l.granted AND c.relkind IN ('r', 'p')''')
            captured.append(cursor.fetchall())

db.core.CoreMixin._get_cursor = probe
import database
print('LOCKS_JSON:' + json.dumps(captured[-1]), flush=True)
"""


def _locks_from(stdout):
    for line in stdout.splitlines():
        if line.startswith('LOCKS_JSON:'):
            return json.loads(line[len('LOCKS_JSON:'):])
    return None


@pytest.mark.skipif(not FRESH_PORT, reason='OTP_FRESH_SCHEMA_TEST_PORT — порт одноразового локального Postgres')
def test_startup_on_ready_schema_does_not_lock_hot_tables():
    """26.11.2026: миграция на каждом старте пода держала users и user_sessions эксклюзивно —
    дедлоки с проверкой входа. На готовой схеме горячие таблицы не должны блокироваться вовсе."""
    name = 'otp_guard_' + uuid.uuid4().hex[:12]
    admin = _connect(FRESH_PORT)
    admin.autocommit = True
    admin.cursor().execute('CREATE DATABASE ' + name)
    try:
        env = dict(os.environ, POSTGRES_HOST='127.0.0.1', POSTGRES_PORT=FRESH_PORT, POSTGRES_DB=name,
                   POSTGRES_USER='postgres', POSTGRES_PASSWORD='', POSTGRES_POOL_MIN_CONN='1',
                   POSTGRES_POOL_MAX_CONN='4', RAG_TRACE_REQUIRED='false', PYTHONUTF8='1')
        env.pop(schema_guard.GUARD_ENV, None)
        runs = []
        for _ in range(2):  # первый — разворот пустой базы, второй — старт на готовой схеме
            run = subprocess.run([sys.executable, '-X', 'utf8', '-c', _PROBE], cwd=ROOT, env=env,
                                 capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
            locks = _locks_from(run.stdout)
            assert run.returncode == 0 and locks is not None, (run.stdout[-1500:], run.stderr[-3000:])
            runs.append((locks, run.stderr))
        locks, stderr = runs[1]
        strong = {(table, mode) for table, mode in locks if table in HOT_TABLES and mode in STRONG}
        assert strong == set(), (strong, stderr[-3000:])
        applied = re.findall(r'Схема: \d+ DDL сверены с каталогом без блокировки таблиц, применено (\d+)', stderr)
        assert applied, stderr[-2000:]
    finally:
        admin.cursor().execute('DROP DATABASE IF EXISTS %s WITH (FORCE)' % name)
        admin.close()
