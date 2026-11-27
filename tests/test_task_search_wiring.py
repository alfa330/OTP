# -*- coding: utf-8 -*-
"""Поиск задач с опечатками — проводка в базе и API (владелец 27.11.2026).

«Поиск будет искать по выбранной доске и проекту»: кандидаты поиска — ровно те
задачи, что список показал бы без поиска (охват доски, проект, раздел и
фильтры), а порядок и «сколько нашлось» решает task_search. Сам движок —
tests/test_task_search.py; здесь — что база и ручка зовут его правильно.

Тест SQL кандидатов на настоящем Postgres (как соседние *_postgres.py):

    TASK_PROJECTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_task_search_wiring.py
"""
import ast
import contextlib
import os
import textwrap
import unittest
import uuid
from datetime import datetime

import pytest

import task_projects
import task_search
from tests import source_cache

ROOT = source_cache.ROOT
DB_TASKS = (ROOT / 'db' / 'tasks.py').read_text(encoding='utf-8')
ROUTES = (ROOT / 'tasks' / 'routes.py').read_text(encoding='utf-8')
DATABASE_SOURCE = source_cache.database_flat_path().read_text(encoding='utf-8-sig')


def _method(name, source=DB_TASKS):
    start = source.index(f"    def {name}(")
    end = source.find("\n    def ", start + 10)
    return source[start:end if end > 0 else len(source)]


class ListingWiringTests(unittest.TestCase):
    def setUp(self):
        self.listing = _method('get_tasks_for_requester')

    def test_typo_tolerant_search_replaced_like(self):
        """LIKE опечатку не прощает — условием в SQL поиск больше не ставится."""
        self.assertNotIn("ILIKE", self.listing)
        self.assertIn("search_query = task_search.parse_query(search_text) if search_text else None", self.listing)
        self.assertIn("import task_search", DB_TASKS)

    def test_candidates_share_the_list_scope_and_filters(self):
        """Доска, проект, раздел, статус и прочие фильтры — те же условия, что у
        списка без поиска: кандидатов отбирает filtered_where_sql целиком."""
        self.assertIn(
            "search_hits, search_documents = self._task_search_hits(\n"
            "                    cursor, search_query, filtered_where_sql, filtered_params\n"
            "                )",
            self.listing,
        )
        # Условие поиска не добавляется после отбора кандидатов: иначе база и
        # ранжирование считали бы разные множества.
        scope_at = self.listing.index("filtered_conditions = list(base_conditions)")
        where_at = self.listing.index("filtered_where_sql = f\"WHERE")
        hits_at = self.listing.index("self._task_search_hits(")
        self.assertLess(scope_at, where_at)
        self.assertLess(where_at, hits_at)

    def test_count_and_page_follow_the_ranking(self):
        self.assertIn("total_filtered = len(search_hits)", self.listing)
        self.assertIn("page_hits = search_hits[offset_norm:offset_norm + limit_norm]", self.listing)
        self.assertIn('f"{filtered_where_sql} AND t.id = ANY(%s)"', self.listing)
        self.assertIn("task_rows.sort(key=lambda row: rank_of.get(row[0], len(rank_of)))", self.listing)
        # Страница поиска уже нарезана — LIMIT/OFFSET к ней не добавляются.
        page_branch = self.listing[self.listing.index("if page_hits is None:"):]
        self.assertIn('task_query += "\\n                LIMIT %s"', page_branch[:400])

    def test_summary_still_counts_the_whole_board(self):
        """Шапка доски — итоги всей доски, а не находок."""
        summary_at = self.listing.index("if include_summary:")
        self.assertIn("{base_where_sql}", self.listing[summary_at:summary_at + 2600])

    def test_rows_carry_the_match_and_the_answer_the_layout(self):
        self.assertIn('task["search_match"] = task_search.describe(search_query, hit, document)', self.listing)
        self.assertIn('payload["search"] = {', self.listing)
        self.assertIn('"search": payload.get("search"),', ROUTES)

    def test_candidates_query(self):
        block = _method('_task_search_hits')
        for column in ("t.subject", "LEFT(COALESCE(t.description, ''), %s)", "project.code, t.project_number",
                       "FROM task_key_aliases ka_s", "COALESCE(origin_user.name, t.requested_by_name, '')"):
            self.assertIn(column, block)
        self.assertIn("LIMIT %s", block)
        self.assertIn("task_search.DESCRIPTION_SEARCH_CHARS, *params, task_search.MAX_CANDIDATES", block)
        # Потолок кандидатов не молчит.
        self.assertIn("logging.warning(", block)

    def test_one_letter_query_is_no_search(self):
        """«а» — не поиск: список остаётся как без строки поиска."""
        self.assertIn("if search_query is not None and search_query.empty:", self.listing)


# ── Настоящий SQL кандидатов ────────────────────────────────────────────────

PORT = os.environ.get('TASK_PROJECTS_TEST_PORT')

DDL = '''
    CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
    CREATE TABLE task_projects (id INTEGER PRIMARY KEY, code VARCHAR(10) NOT NULL, name TEXT);
    CREATE TABLE tasks (
        id INTEGER PRIMARY KEY, subject TEXT NOT NULL, description TEXT, assigned_to INTEGER,
        created_by INTEGER, requested_by_id INTEGER, requested_by_name TEXT,
        project_id INTEGER, project_number INTEGER, created_at TIMESTAMP NOT NULL
    );
    CREATE TABLE task_assignees (task_id INTEGER, user_id INTEGER, position INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE task_key_aliases (code VARCHAR(10), number INTEGER, task_id INTEGER);
    INSERT INTO users VALUES (1, 'Иванов Иван'), (2, 'Жанна Ахметова'), (3, 'Петрова Анна'), (4, 'Сериков Нурлан');
    INSERT INTO task_projects VALUES (10, 'HR', 'HR отдел'), (20, 'ACC', 'Бухгалтерия');
    INSERT INTO tasks VALUES
        (1, 'Заполнить табель за ноябрь', repeat('х', 3000) || ' хвост', 1, 4, NULL, 'Внешний Заказчик', 10, 1, '2026-11-20 10:00'),
        (2, 'Акт сверки', 'Оплатить до пятницы', 2, 4, 3, NULL, 20, 5, '2026-11-21 10:00'),
        (3, 'Табель за октябрь', NULL, 3, 4, NULL, NULL, 10, 2, '2026-11-01 10:00'),
        (4, 'Без проекта: табель', NULL, 1, 1, NULL, NULL, NULL, NULL, '2026-11-25 10:00');
    INSERT INTO task_assignees VALUES (1, 2, 1), (1, 1, 0), (2, 2, 0), (3, 3, 0), (4, 1, 0);
    INSERT INTO task_key_aliases VALUES ('HR', 9, 2);
'''


def _build_class():
    module = source_cache.parse(DATABASE_SOURCE)
    database = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == 'Database')
    lines = DATABASE_SOURCE.splitlines(keepends=True)
    parts = []
    for node in database.body:
        if isinstance(node, ast.FunctionDef) and node.name == '_task_search_hits':
            parts.append(textwrap.dedent(''.join(lines[node.lineno - 1:node.end_lineno])))
        elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == '_TASK_ASSIGNEE_EXISTS_SQL' for target in node.targets):
            parts.append(textwrap.dedent(ast.get_source_segment(DATABASE_SOURCE, node)))
    assert len(parts) == 2, 'в Database нет _task_search_hits или _TASK_ASSIGNEE_EXISTS_SQL'
    namespace = {'task_search': task_search, 'task_projects': task_projects, 'logging': __import__('logging')}
    exec('class FakeDatabase:\n' + ''.join(textwrap.indent(part, '    ') + '\n' for part in parts), namespace)
    return namespace['FakeDatabase']


@contextlib.contextmanager
def _cursor(schema):
    import psycopg2
    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres', connect_timeout=3)
    cur = conn.cursor()
    try:
        cur.execute("SET statement_timeout TO '10s'")
        cur.execute('SET search_path TO ' + schema)
        yield cur
        conn.commit()
    finally:
        cur.close()
        conn.close()


@pytest.fixture
def pg():
    if not PORT:
        pytest.skip('needs local PostgreSQL (TASK_PROJECTS_TEST_PORT)')
    name = 'task_search_' + uuid.uuid4().hex[:10]
    with _cursor('public') as base:
        base.execute('CREATE SCHEMA ' + name)
    try:
        with _cursor(name) as cursor:
            cursor.execute(DDL)
            yield _build_class()(), cursor
    finally:
        with _cursor('public') as base:
            base.execute('DROP SCHEMA %s CASCADE' % name)


def test_candidates_are_the_scope_and_carry_every_searched_field(pg):
    database, cursor = pg
    hits, documents = database._task_search_hits(cursor, task_search.parse_query('табль'), '', [])
    # Все задачи без условий; порядок — ранжирование, при равенстве — свежие выше.
    assert [hit.id for hit in hits] == [4, 1, 3]
    first = documents[1]
    # Состав по порядку показа, затем постановщик и внешний поручитель.
    assert first.people == ('Иванов Иван', 'Жанна Ахметова', 'Сериков Нурлан', 'Внешний Заказчик')
    assert (first.key_code, first.key_number) == ('HR', 1)
    assert len(first.description) == task_search.DESCRIPTION_SEARCH_CHARS
    assert first.created_rank == datetime(2026, 11, 20, 10, 0).timestamp()
    # Прежний ключ (задачу переносили) — тоже ключ.
    assert documents[2].key_aliases == (('HR', 9),)
    assert documents[4].key_code == '' and documents[4].key_number == 0


def test_scope_condition_composes_with_the_candidates_query(pg):
    """Охват рядового — то же условие участия, что у списка (_TASK_ASSIGNEE_EXISTS_SQL)."""
    database, cursor = pg
    where = f"WHERE (t.created_by = %s OR {database._TASK_ASSIGNEE_EXISTS_SQL}) AND t.project_id = %s"
    hits, _documents = database._task_search_hits(cursor, task_search.parse_query('табель'), where, [2, 2, 2, 10])
    # Жанна (id 2) — соисполнитель только HR-1; ACC-5 на ней, но это другая доска.
    assert [hit.id for hit in hits] == [1]


def test_found_by_person_old_key_and_requester(pg):
    database, cursor = pg
    hits, _ = database._task_search_hits(cursor, task_search.parse_query('ахметова'), '', [])
    assert [hit.id for hit in hits] == [2, 1]  # исполнитель первым — свежее
    hits, _ = database._task_search_hits(cursor, task_search.parse_query('HR-9'), '', [])
    assert [hit.id for hit in hits] == [2]
    hits, _ = database._task_search_hits(cursor, task_search.parse_query('петрова'), '', [])
    assert {hit.id for hit in hits} == {2, 3}  # поручитель и исполнитель


if __name__ == '__main__':
    unittest.main()
