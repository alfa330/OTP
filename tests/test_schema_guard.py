# -*- coding: utf-8 -*-
"""Курсор миграции схемы (db/schema_guard.py): разбор пачек и переписывание «пустых» DDL.

Без базы: что режется, что оборачивается проверкой каталога и что уходит как есть. То, что
обёрнутый оператор даёт ту же схему и не держит блокировок, проверяет
tests/test_schema_guard_postgres.py на настоящем Postgres.
"""
import re

import pytest

from db import schema_guard
from db.schema_guard import guard_sql, guard_statement, split_statements


def _codes(sql):
    return [' '.join(code.split()) for _, code in split_statements(sql)]


def _guarded(sql):
    pieces = split_statements(sql)
    assert pieces is not None and len([p for p in pieces if p[1].strip()]) == 1, sql
    raw, code = next(p for p in pieces if p[1].strip())
    return guard_statement(raw, code)


# ── Разбор пачки ─────────────────────────────────────────────────────────────

def test_split_keeps_semicolons_inside_strings_bodies_and_comments():
    sql = (
        "CREATE TABLE t (a text DEFAULT 'x;y', \"odd;name\" int);\n"
        "-- комментарий; с точкой с запятой\n"
        "INSERT INTO t VALUES (E'it\\'s;fine', 1);\n"
        "/* внешний /* вложенный; */ хвост; */ SELECT 1;\n"
        "CREATE FUNCTION f() RETURNS int AS $$ BEGIN RETURN 1; END $$ LANGUAGE plpgsql;\n"
        "DO $body$ BEGIN PERFORM 1; END $body$"
    )
    assert _codes(sql) == [
        "CREATE TABLE t (a text DEFAULT 'x;y', \"odd;name\" int)",
        "INSERT INTO t VALUES (E'it\\'s;fine', 1)",
        'SELECT 1',
        'CREATE FUNCTION f() RETURNS int AS $$ BEGIN RETURN 1; END $$ LANGUAGE plpgsql',
        'DO $body$ BEGIN PERFORM 1; END $body$',
    ]
    # Исходные куски склеиваются обратно байт в байт.
    assert ';'.join(raw for raw, _ in split_statements(sql)) == sql


@pytest.mark.parametrize('sql', [
    "SELECT 'не закрыта",
    'SELECT "не закрыт',
    'DO $$ BEGIN PERFORM 1; END',
    'SELECT 1 /* не закрыт',
])
def test_split_refuses_unterminated_text(sql):
    assert split_statements(sql) is None
    assert guard_sql(sql) == (sql, 0)


def test_dollar_inside_identifier_or_parameter_is_not_a_quote():
    assert _codes('SELECT a$b$ FROM t; SELECT $1') == ['SELECT a$b$ FROM t', 'SELECT $1']


# ── Какие операторы оборачиваются ────────────────────────────────────────────

@pytest.mark.parametrize('sql,fragments', [
    ('ALTER TABLE users ADD COLUMN IF NOT EXISTS city VARCHAR(255);',
     ["to_regclass('users')", "attname = 'city'"]),
    ('ALTER TABLE Users ADD IF NOT EXISTS City text',
     ["to_regclass('Users')", "attname = 'city'"]),
    ('ALTER TABLE IF EXISTS ONLY public.t ADD COLUMN IF NOT EXISTS "MixedCase" int',
     ["to_regclass('public.t')", "attname = 'MixedCase'"]),
    ("ALTER TABLE t ADD COLUMN IF NOT EXISTS s VARCHAR(20) CHECK (s IN ('a', 'b, c') OR s IS NULL),"
     " ADD COLUMN IF NOT EXISTS n NUMERIC(10, 2) DEFAULT 0",
     ["attname = 's'", "attname = 'n'"]),
    ('ALTER TABLE t DROP COLUMN IF EXISTS raw CASCADE', ["NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute"]),
    ('ALTER TABLE t DROP CONSTRAINT IF EXISTS t_k', ["conname = 't_k'"]),
    ('ALTER TABLE t ALTER COLUMN c DROP NOT NULL', ['NOT attnotnull']),
    ('ALTER TABLE t ALTER c SET NOT NULL', ['AND attnotnull', 'NOT convalidated']),
    ('ALTER TABLE t ALTER COLUMN c DROP DEFAULT', ['NOT atthasdef']),
    ('CREATE INDEX IF NOT EXISTS idx_t_a ON t(a)', ["relname = 'idx_t_a'", "to_regclass('t')"]),
    ('CREATE UNIQUE INDEX IF NOT EXISTS uq_t ON ONLY t USING btree (a, b) WHERE a <> \'\'',
     ["relname = 'uq_t'"]),
    ('CREATE INDEX IF NOT EXISTS idx_trgm ON t USING gin (a gin_trgm_ops)', ["relname = 'idx_trgm'"]),
    ('DROP TRIGGER IF EXISTS trg_old ON t', ["tgname = 'trg_old'"]),
])
def test_noop_capable_statements_are_wrapped(sql, fragments):
    block = _guarded(sql)
    assert block is not None and block.startswith('DO $otp_guard$')
    for fragment in fragments:
        assert fragment in block
    # Сам оператор уходит в EXECUTE дословно.
    raw = next(raw for raw, code in split_statements(sql) if code.strip())
    assert '$otp_ddl$' + raw + '$otp_ddl$' in block


@pytest.mark.parametrize('sql', [
    'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx ON t(a)',   # вне транзакции — не наш случай
    'CREATE INDEX idx ON t(a)',                               # без IF NOT EXISTS — ошибка должна остаться
    'ALTER TABLE t ADD CONSTRAINT k CHECK (a > 0)',
    'ALTER TABLE t ADD COLUMN IF NOT EXISTS a int, ADD CONSTRAINT k CHECK (a > 0)',
    'ALTER TABLE t ALTER COLUMN a SET DEFAULT 0',
    'ALTER TABLE t RENAME COLUMN a TO b',
    'ALTER TABLE t ADD COLUMN a int',
    'DROP TRIGGER trg ON t',
    'CREATE TABLE IF NOT EXISTS t (a int)',
    'DO $$ BEGIN ALTER TABLE t ADD COLUMN IF NOT EXISTS a int; END $$',
    'UPDATE t SET a = 1',
])
def test_other_statements_go_as_written(sql):
    assert guard_sql(sql) == (sql, 0)


def test_long_names_are_truncated_like_postgres():
    name = 'idx_' + 'x' * 70
    block = _guarded('CREATE INDEX IF NOT EXISTS %s ON t(a)' % name)
    assert "relname = '%s'" % name[:63] in block


def test_quoted_identifier_with_doubled_quote():
    block = _guarded('ALTER TABLE t ADD COLUMN IF NOT EXISTS "a""b" int')
    assert "attname = 'a\"b'" in block


def test_tag_is_changed_when_statement_contains_it():
    sql = "ALTER TABLE t ADD COLUMN IF NOT EXISTS c text DEFAULT $otp_ddl$x$otp_ddl$"
    block = _guarded(sql)
    assert '$otp_ddl1$' in block


# ── Пачки ────────────────────────────────────────────────────────────────────

def test_batch_wraps_only_noop_capable_statements_in_place():
    sql = (
        "CREATE TABLE IF NOT EXISTS t (id int);\n"
        "ALTER TABLE t ADD COLUMN IF NOT EXISTS a int;\n"
        "UPDATE t SET a = 1 WHERE a IS NULL;\n"
        "CREATE INDEX IF NOT EXISTS idx_t_a ON t(a);\n"
    )
    new, count = guard_sql(sql)
    assert count == 2
    pieces = [' '.join(code.split()) for _, code in split_statements(new) if code.strip()]
    assert len(pieces) == 4
    assert pieces[0] == 'CREATE TABLE IF NOT EXISTS t (id int)'
    assert pieces[1].startswith('DO $otp_guard$') and 'ADD COLUMN IF NOT EXISTS a int' in pieces[1]
    assert pieces[2] == 'UPDATE t SET a = 1 WHERE a IS NULL'
    assert pieces[3].startswith('DO $otp_guard$') and 'CREATE INDEX IF NOT EXISTS idx_t_a' in pieces[3]
    # Повторная обёртка ничего не меняет: DO-блок — не «пустой» DDL.
    assert guard_sql(new) == (new, 0)


def test_trigger_drop_and_create_become_one_block_with_fingerprint():
    sql = (
        "DROP TRIGGER IF EXISTS trg_x ON t;\n"
        "CREATE TRIGGER trg_x AFTER INSERT OR UPDATE ON t FOR EACH ROW EXECUTE FUNCTION f();\n"
        "SELECT 1;"
    )
    new, count = guard_sql(sql)
    assert count == 1
    pieces = [code for _, code in split_statements(new) if code.strip()]
    assert len(pieces) == 2 and ' '.join(pieces[1].split()) == 'SELECT 1'
    block = pieces[0]
    assert block.startswith('DO $otp_guard$')
    assert "tgname = 'trg_x'" in block and "obj_description(oid, 'pg_trigger')" in block
    assert 'DROP TRIGGER IF EXISTS trg_x ON t' in block and 'CREATE TRIGGER trg_x AFTER INSERT' in block
    fingerprint = re.search(r"IS '(otp-ddl:[0-9a-f]{16})'", block).group(1)
    create_code = 'CREATE TRIGGER trg_x AFTER INSERT OR UPDATE ON t FOR EACH ROW EXECUTE FUNCTION f()'
    assert fingerprint == schema_guard.ddl_fingerprint(create_code)
    assert new.endswith('\nSELECT 1;')


@pytest.mark.parametrize('create', [
    'CREATE TRIGGER trg_y AFTER INSERT ON t FOR EACH ROW EXECUTE FUNCTION f()',  # другое имя
    'CREATE TRIGGER trg_x AFTER INSERT ON other FOR EACH ROW EXECUTE FUNCTION f()',  # другая таблица
    'CREATE CONSTRAINT TRIGGER trg_x AFTER INSERT ON t FOR EACH ROW EXECUTE FUNCTION f()',
])
def test_not_a_pair_keeps_create_as_written(create):
    sql = 'DROP TRIGGER IF EXISTS trg_x ON t;\n' + create
    new, count = guard_sql(sql)
    assert count == 1  # только одиночный DROP (пуст, если триггера нет)
    assert new.endswith(';\n' + create)


# ── Курсор и выключатель ─────────────────────────────────────────────────────

@pytest.mark.parametrize('value,enabled', [(None, True), ('1', True), ('0', False), ('off', False),
                                           ('false', False), ('NO', False)])
def test_switch(monkeypatch, value, enabled):
    if value is None:
        monkeypatch.delenv(schema_guard.GUARD_ENV, raising=False)
    else:
        monkeypatch.setenv(schema_guard.GUARD_ENV, value)
    factory = schema_guard.schema_cursor_factory()
    assert (factory is schema_guard.GuardedSchemaCursor) is enabled
    assert (factory is None) is (not enabled)


class _Recorder:
    def __init__(self):
        self.calls = []

    def execute(self, query, vars=None):
        self.calls.append(query)

    def fetchone(self):
        raise AssertionError('при выключенном курсоре каталог не читается')

    def fetchall(self):
        raise AssertionError('при выключенном курсоре каталог не читается')


def test_switch_off_rebuilds_constraints_and_triggers_every_time_like_before(monkeypatch):
    monkeypatch.setenv(schema_guard.GUARD_ENV, '0')
    ddl = 'ALTER TABLE t DROP CONSTRAINT IF EXISTS k; ALTER TABLE t ADD CONSTRAINT k CHECK (a > 0);'
    cursor = _Recorder()
    assert schema_guard.replace_constraints(cursor, 't', ('k',), ddl) is True
    assert cursor.calls == [ddl]
    create = 'CREATE TRIGGER trg AFTER INSERT ON t FOR EACH ROW EXECUTE FUNCTION f();'
    cursor = _Recorder()
    assert schema_guard.replace_trigger(cursor, 't', 'trg', create) is True
    assert len(cursor.calls) == 2 and 'DROP TRIGGER IF EXISTS' in repr(cursor.calls[0])
    assert cursor.calls[1] == create


# ── Пересборка ограничений и триггеров по отпечатку ──────────────────────────

def test_check_literals_match_source_and_postgres_deparse():
    source = "employment_type IN ('gph', 'of', 'smz') OR employment_type IS NULL"
    deparsed = ("CHECK ((((employment_type)::text = ANY ((ARRAY['gph'::character varying, "
                "'of'::character varying, 'smz'::character varying])::text[])) OR (employment_type IS NULL)))")
    assert schema_guard._check_literals(source) == schema_guard._check_literals(deparsed)
    numeric = "rate IS NULL OR rate IN (1.00, 0.75, 0.50)"
    assert schema_guard._check_literals(numeric) == schema_guard._check_literals(
        'CHECK (((rate IS NULL) OR (rate = ANY (ARRAY[1.00, 0.75, 0.50]))))')
    assert schema_guard._check_literals(numeric) != schema_guard._check_literals(
        'CHECK (((rate IS NULL) OR (rate = ANY (ARRAY[1.00, 0.75]))))')


def test_added_checks_reads_every_constraint_of_the_ddl():
    ddl = """
        ALTER TABLE users DROP CONSTRAINT IF EXISTS a_check, DROP CONSTRAINT IF EXISTS b_check;
        ALTER TABLE users
        ADD CONSTRAINT a_check CHECK (x IN ('1)', '2') OR x IS NULL),
        ADD CONSTRAINT B_Check CHECK ((y > 0));
    """
    assert schema_guard._added_checks(ddl) == {
        'a_check': "x IN ('1)', '2') OR x IS NULL",
        'b_check': '(y > 0)',
    }


def test_trigger_definition_normalization():
    deparsed = ('CREATE TRIGGER trg AFTER UPDATE OF status ON public.users FOR EACH ROW '
                'EXECUTE FUNCTION revoke()')
    source = """
        CREATE TRIGGER trg
        AFTER UPDATE OF status ON users
        FOR EACH ROW
        EXECUTE FUNCTION revoke();
    """
    assert schema_guard._normalized(deparsed) == schema_guard._normalized(source)
