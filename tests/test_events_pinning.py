# -*- coding: utf-8 -*-
"""Закрепление постов: закреплённый не должен попадать в постраничную выдачу.

Почему это стоит отдельного теста. Лента «Ивентов» листается курсором по id
(`e.id < before_id`), и пост, поднятый наверх вопреки порядку id, ломает
пагинацию: он либо задваивается на первом экране, либо выпадает со следующей
страницы. Регресс здесь тихий — ошибки не будет, просто лента начнёт врать.

Приём: настоящий SQL достаётся из Database.list_events, а не переписывается в
тесте. Импортировать database.py нельзя — на Windows он падает на time.tzset,
а в конце файла поднимает пул к боевой базе, — поэтому метод вырезается через
ast и исполняется с подставным self, который просто запоминает запросы.
Дальше запомненные тексты гоняются на синтетических постах: CTE перекрывает
одноимённую таблицу, соединение read-only, прод не трогается.
"""

import ast
import contextlib
import textwrap
import unittest
from pathlib import Path

from tests import prod_db

ROOT = Path(__file__).resolve().parents[1]

# Три поста, из них закреплён средний — так проверяется и исключение, и то,
# что порядок оставшихся не поехал.
_STUB = """
WITH events AS (
    SELECT id::int, author_id::int, NULL::int AS department_id,
           title::text, body::text, NULL::timestamp AS created_at,
           is_pinned::boolean
      FROM (VALUES (1, NULL::int, 'Первый', NULL::text, FALSE),
                   (2, NULL::int, 'Закреплённый', NULL::text, TRUE),
                   (3, NULL::int, 'Третий', NULL::text, FALSE))
           AS t(id, author_id, title, body, is_pinned)
),
users AS (SELECT NULL::int AS id, NULL::text AS name,
                 NULL::text AS avatar_bucket, NULL::text AS avatar_blob_path),
departments AS (SELECT NULL::int AS id, NULL::text AS name)
"""


class _CapturingCursor:
    """Запоминает запросы вместо исполнения — нужен только текст SQL."""

    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))

    def fetchall(self):
        return []


class _FakeDatabase:
    def __init__(self, cursor):
        self._cursor = cursor

    def _get_cursor(self):
        @contextlib.contextmanager
        def cm():
            yield self._cursor
        return cm()

    def _event_row_basic(self, row):
        return {'id': row[0], 'is_pinned': bool(row[10])}

    def _attach_event_aggregates(self, cursor, events, viewer_id=None):
        pass


def _load_list_events():
    source = (ROOT / 'database.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == 'Database')
    method = next(node for node in cls.body
                  if isinstance(node, ast.FunctionDef) and node.name == 'list_events')
    namespace = {}
    exec(compile(textwrap.dedent(ast.get_source_segment(source, method)),
                 'list_events', 'exec'), namespace)
    return namespace['list_events']


class EventsPinningSqlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.conn = prod_db.connection()

        capturing = _CapturingCursor()
        _load_list_events()(_FakeDatabase(capturing), viewer_id=1,
                            viewer_department_id=None, is_global=True,
                            before_id=None, limit=12)
        cls.calls = capturing.calls

    def _ids(self, index):
        sql, params = self.calls[index]
        cursor = self.conn.cursor()
        try:
            cursor.execute(_STUB + sql.strip().rstrip(';'), params)
            return [row[0] for row in cursor.fetchall()]
        finally:
            prod_db.rollback()
            cursor.close()

    def test_first_page_runs_two_queries(self):
        """Лента и закреплённые — два запроса, а не выборка с сортировкой."""
        self.assertEqual(2, len(self.calls))

    def test_pinned_excluded_from_paged_feed(self):
        self.assertNotIn(2, self._ids(0),
                         'закреплённый попал в постраничную выдачу — '
                         'на первом экране он задвоится')

    def test_paged_feed_keeps_id_order(self):
        self.assertEqual([3, 1], self._ids(0))

    def test_pinned_query_returns_only_pinned(self):
        self.assertEqual([2], self._ids(1))

    def test_pinned_not_requested_for_next_pages(self):
        """Со второй страницы закреплённые не запрашиваются вовсе."""
        capturing = _CapturingCursor()
        _load_list_events()(_FakeDatabase(capturing), viewer_id=1,
                            viewer_department_id=None, is_global=True,
                            before_id=5, limit=12)
        self.assertEqual(1, len(capturing.calls))

    def test_visibility_is_shared_between_both_queries(self):
        """Оба запроса обязаны фильтровать по отделам одинаково.

        Разъедься они — закреплённый пост показался бы отделу, которому не
        предназначен, и это самый неприятный из возможных здесь дефектов.
        """
        capturing = _CapturingCursor()
        _load_list_events()(_FakeDatabase(capturing), viewer_id=1,
                            viewer_department_id=7, is_global=False,
                            before_id=None, limit=12)
        feed_sql, feed_params = capturing.calls[0]
        pinned_sql, pinned_params = capturing.calls[1]
        self.assertIn('event_departments', feed_sql)
        self.assertIn('event_departments', pinned_sql)
        self.assertEqual([7, 7], list(pinned_params))
        self.assertEqual([7, 7], list(feed_params[:2]))


if __name__ == '__main__':
    unittest.main()
