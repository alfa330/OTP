# -*- coding: utf-8 -*-
"""Схема обязана разворачиваться на ПУСТОЙ базе, а не только поверх прода.

18.11.2026 девопс поднимал окружение с нуля, и старт падал: _init_db делал
ALTER TABLE resource_saved_schedule_plans (стр. 3877) раньше её CREATE TABLE
(стр. 4486). _init_db — одна транзакция, поэтому откатывалась вся схема. На
проде этого не видно: таблица там давно есть.

Следом на пустой базе молча откатывалась схема «Вики»: проверка свёрнутых
колонок приводила 'wiki_ai_chunks'::regclass раньше, чем таблицу создаёт
_AI_STATEMENTS, — а без wiki_offices не вставали и «Посылки», «Учёт воды» и
«Термокороба». Приложение при этом стартовало, разделы просто пропадали.

Без базы (CI) проверяется порядок в теле _init_db и вики-проверка. Полный
разворот на пустой базе — по OTP_FRESH_SCHEMA_TEST_PORT, порту одноразового
локального Postgres (initdb -A trust): так это видит девопс.
"""
import ast
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]

_IDENT = r'(?:public\.)?"?([a-z_][a-z0-9_]*)"?'
_CREATE = re.compile(r'CREATE\s+(?:UNLOGGED\s+)?TABLE\s+IF\s+NOT\s+EXISTS\s+' + _IDENT, re.I)
# ALTER TABLE IF EXISTS безопасен и до CREATE — его не считаем.
_USES = (
    ('ALTER TABLE', re.compile(r'ALTER\s+TABLE\s+(?!IF\s+EXISTS)(?:ONLY\s+)?' + _IDENT, re.I)),
    ('CREATE INDEX … ON', re.compile(
        r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+ON\s+'
        r'(?:ONLY\s+)?' + _IDENT, re.I)),
    ('REFERENCES', re.compile(r'REFERENCES\s+' + _IDENT, re.I)),
)


def _init_db_source():
    """(номер первой строки, текст) метода SchemaMixin._init_db."""
    text = (ROOT / 'db' / 'schema.py').read_text(encoding='utf-8')
    mixin = next(node for node in ast.parse(text).body
                 if isinstance(node, ast.ClassDef) and node.name == 'SchemaMixin')
    init_db = next(node for node in mixin.body
                   if isinstance(node, ast.FunctionDef) and node.name == '_init_db')
    lines = text.splitlines(keepends=True)
    return init_db.lineno, ''.join(lines[init_db.lineno - 1:init_db.end_lineno])


def tables_used_before_create(first_line, body):
    """Ссылки на таблицу выше её CREATE TABLE в том же теле — по порядку в тексте.

    Тело _init_db выполняется сверху вниз, поэтому порядок в тексте и есть
    порядок на пустой базе. Таблицы, которых тело не создаёт (их заводят
    модули разделов), здесь не судим."""
    created = {}
    for match in _CREATE.finditer(body):
        created.setdefault(match.group(1).lower(), match.start())
    found = []
    for kind, pattern in _USES:
        for match in pattern.finditer(body):
            table = match.group(1).lower()
            if table in created and match.start() < created[table]:
                found.append((match.start(), 'стр. %d: %s %s, а CREATE TABLE только на стр. %d' % (
                    first_line + body.count('\n', 0, match.start()), kind, table,
                    first_line + body.count('\n', 0, created[table]))))
    return [message for _, message in sorted(found)]


class InitDbOrderTest(unittest.TestCase):
    def test_no_table_is_touched_before_its_create(self):
        problems = tables_used_before_create(*_init_db_source())
        self.assertEqual(problems, [], 'на пустой базе _init_db упадёт: ' + '; '.join(problems))

    def test_guard_catches_the_original_defect(self):
        # Сам сторож: прежний порядок (ALTER и индекс выше CREATE) он обязан поймать.
        body = (
            '\n'
            'cursor.execute("ALTER TABLE plans ADD COLUMN IF NOT EXISTS mode TEXT;")\n'
            'cursor.execute("CREATE INDEX IF NOT EXISTS idx_plans_mode ON plans(mode);")\n'
            'cursor.execute("ALTER TABLE IF EXISTS later ADD COLUMN x INTEGER;")\n'
            'cursor.execute("CREATE TABLE IF NOT EXISTS plans (id SERIAL PRIMARY KEY);")\n'
            'cursor.execute("CREATE TABLE IF NOT EXISTS shifts (plan_id INTEGER REFERENCES plans(id));")\n'
            'cursor.execute("CREATE TABLE IF NOT EXISTS later (id INTEGER);")\n'
        )
        self.assertEqual(tables_used_before_create(10, body), [
            'стр. 11: ALTER TABLE plans, а CREATE TABLE только на стр. 14',
            'стр. 12: CREATE INDEX … ON plans, а CREATE TABLE только на стр. 14',
        ])

    def test_plan_direction_column_follows_the_plan_table(self):
        first_line, body = _init_db_source()
        create = body.index('CREATE TABLE IF NOT EXISTS resource_saved_schedule_plans (')
        alter = body.index('ALTER TABLE resource_saved_schedule_plans\n')
        self.assertLess(create, alter)
        self.assertIn('ADD COLUMN IF NOT EXISTS direction_mode', body[alter:alter + 200])


class _CatalogCursor:
    """Каталог Postgres в миниатюре для _regenerate_folded_columns.

    Как у сервера: приведение 'имя'::regclass к несуществующей таблице падает,
    to_regclass(имя) отдаёт NULL — и запрос возвращает ноль строк."""

    def __init__(self, existing, expression):
        self.existing = set(existing)
        self.expression = expression
        self.row = None

    def execute(self, sql, params=None):
        if 'pg_attrdef' not in sql:
            raise AssertionError('неожиданный запрос: %s' % sql.strip()[:120])
        table = params[0]
        if '::regclass' in sql and table not in self.existing:
            raise RuntimeError('UndefinedTable: relation "%s" does not exist' % table)
        self.row = (self.expression,) if table in self.existing else None

    def fetchone(self):
        return self.row


class WikiFoldedColumnsOnEmptyDatabaseTest(unittest.TestCase):
    def test_chunks_table_not_created_yet_is_skipped(self):
        from wiki import schema as wiki_schema
        from wiki.text import SQL_FOLD_FROM

        # На пустой базе к этому шагу wiki_articles уже есть, а wiki_ai_chunks — ещё нет.
        cursor = _CatalogCursor({'wiki_articles'}, "translate(title, '%s')" % SQL_FOLD_FROM)
        self.assertEqual(wiki_schema._regenerate_folded_columns(cursor), [])

    def test_check_runs_before_the_chunks_table_is_created(self):
        # Почему проверке нельзя падать на отсутствующей таблице: в init_wiki_schema
        # она стоит раньше _AI_STATEMENTS, которые и создают wiki_ai_chunks.
        source = (ROOT / 'wiki' / 'schema.py').read_text(encoding='utf-8')
        body = source[source.index('def init_wiki_schema('):]
        self.assertLess(body.index('_regenerate_folded_columns(cursor)'),
                        body.index('for statement in _AI_STATEMENTS:'))


FRESH_PORT = os.environ.get('OTP_FRESH_SCHEMA_TEST_PORT')


@unittest.skipUnless(FRESH_PORT, 'OTP_FRESH_SCHEMA_TEST_PORT — порт одноразового локального Postgres')
class FreshDatabaseTest(unittest.TestCase):
    """Настоящий старт схемы (import database) на только что созданной пустой базе."""

    def test_schema_comes_up_on_an_empty_database(self):
        import psycopg2

        name = 'otp_fresh_' + uuid.uuid4().hex[:12]
        admin = psycopg2.connect(host='127.0.0.1', port=int(FRESH_PORT), user='postgres', dbname='postgres')
        admin.autocommit = True
        admin.cursor().execute('CREATE DATABASE ' + name)
        try:
            env = dict(
                os.environ, POSTGRES_HOST='127.0.0.1', POSTGRES_PORT=FRESH_PORT, POSTGRES_DB=name,
                POSTGRES_USER='postgres', POSTGRES_PASSWORD='', POSTGRES_POOL_MIN_CONN='1',
                POSTGRES_POOL_MAX_CONN='4', RAG_TRACE_REQUIRED='false', PYTHONUTF8='1',
            )
            code = "import time; time.tzset = getattr(time, 'tzset', lambda: None); import database"
            run = subprocess.run(
                [sys.executable, '-X', 'utf8', '-c', code], cwd=ROOT, env=env, capture_output=True,
                text=True, encoding='utf-8', errors='replace', timeout=600,
            )
            self.assertEqual(run.returncode, 0, run.stderr[-3000:])
            # Раздел, чья схема откатилась под своим SAVEPOINT, пишет ERROR — а старт идёт дальше.
            errors = [line for line in run.stderr.splitlines() if line.startswith('ERROR:')]
            self.assertEqual(errors, [])
            check = psycopg2.connect(host='127.0.0.1', port=int(FRESH_PORT), user='postgres', dbname=name)
            try:
                cursor = check.cursor()
                for table in ('resource_saved_schedule_plans', 'wiki_ai_chunks', 'wiki_offices', 'parcels'):
                    cursor.execute('SELECT to_regclass(%s) IS NOT NULL', (table,))
                    self.assertTrue(cursor.fetchone()[0], table)
                cursor.execute("""SELECT 1 FROM information_schema.columns
                                   WHERE table_name = 'resource_saved_schedule_plans'
                                     AND column_name = 'direction_mode'""")
                self.assertIsNotNone(cursor.fetchone())
            finally:
                check.close()
        finally:
            admin.cursor().execute('DROP DATABASE IF EXISTS %s WITH (FORCE)' % name)
            admin.close()


if __name__ == '__main__':
    unittest.main()
