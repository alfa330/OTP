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


# Заглушка для скоупного зрителя: 11 — обычный для всех, 12 — закреплён для
# отдела 7, 13 — закреплён для всех, 14 — закреплён для отдела 9.
_SCOPED_STUB = """
WITH events AS (
    SELECT id::int, author_id::int, NULL::int AS department_id,
           title::text, body::text, NULL::timestamp AS created_at,
           is_pinned::boolean
      FROM (VALUES (11, NULL::int, 'Обычный', NULL::text, FALSE),
                   (12, NULL::int, 'Наш закреплённый', NULL::text, TRUE),
                   (13, NULL::int, 'Общий закреплённый', NULL::text, TRUE),
                   (14, NULL::int, 'Чужой закреплённый', NULL::text, TRUE))
           AS t(id, author_id, title, body, is_pinned)
),
event_departments AS (
    SELECT event_id::int, department_id::int
      FROM (VALUES (12, 7), (14, 9)) AS t(event_id, department_id)
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

    def _scoped_ids(self, index, viewer_department_id=7):
        """Прогоняет запрос скоупного зрителя на постах с адресацией по отделам."""
        capturing = _CapturingCursor()
        _load_list_events()(_FakeDatabase(capturing), viewer_id=1,
                            viewer_department_id=viewer_department_id,
                            is_global=False, before_id=None, limit=12)
        sql, params = capturing.calls[index]
        cursor = self.conn.cursor()
        try:
            cursor.execute(_SCOPED_STUB + sql.strip().rstrip(';'), params)
            return [row[0] for row in cursor.fetchall()]
        finally:
            prod_db.rollback()
            cursor.close()

    def test_pinned_respects_department_targeting(self):
        """Закреплённый пост чужого отдела не должен всплыть наверх ленты.

        Проверяется ПОВЕДЕНИЕ, а не наличие подстроки event_departments в
        запросе: закрепление поднимает пост поверх всего, что человек видит, и
        показать так чужому отделу — самый неприятный из возможных здесь
        дефектов. Тест на вхождение подстроки прошёл бы и при сломанном
        условии.
        """
        # 12 — закреплён для отдела 7 (наш), 13 — закреплён для всех,
        # 14 — закреплён для отдела 9 (чужой).
        self.assertEqual([13, 12], self._scoped_ids(1),
                         'наверху только адресованные нам и общие, в порядке id')

    def test_pinned_hidden_from_foreign_department(self):
        self.assertEqual([14, 13], self._scoped_ids(1, viewer_department_id=9))

    def test_scoped_feed_excludes_pinned_and_foreign(self):
        """У скоупного зрителя обычная лента тоже без закреплённых и без чужих."""
        self.assertEqual([11], self._scoped_ids(0))

    def test_both_queries_get_the_same_visibility_parameters(self):
        """Условие видимости обязано собираться один раз на оба запроса."""
        capturing = _CapturingCursor()
        _load_list_events()(_FakeDatabase(capturing), viewer_id=1,
                            viewer_department_id=7, is_global=False,
                            before_id=None, limit=12)
        _feed_sql, feed_params = capturing.calls[0]
        _pinned_sql, pinned_params = capturing.calls[1]
        self.assertEqual([7, 7], list(pinned_params))
        self.assertEqual([7, 7], list(feed_params[:2]))


if __name__ == '__main__':
    unittest.main()
