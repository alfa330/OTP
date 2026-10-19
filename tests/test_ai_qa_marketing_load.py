# -*- coding: utf-8 -*-
"""Нагрузка маркетинговой части ИИ-оценки на базу (19.10.2026).

Замер 15–19.10.2026: связыватель пополнял словарь парков и каналов полным проходом сделок
со сверкой КАЖДОЙ строки со словарём (~4–5 с, ~85 раз в сутки), а панель фильтров на каждое
открытие пересчитывала по всей таблице перечни этапов, причин и ответственных (~3 с) —
вместе пятая часть времени базы. Тесты держат: словарь сверяет со справочником разные
написания, а не строки; общие перечни берутся из кэша процесса и пересчитываются по сроку
или при правке сопоставления ответственных.
"""
import re
import unittest
from unittest import mock

from call_qa import api
from call_qa.marketing import linker


class _Cursor:
    def __init__(self, rows=None):
        self.queries = []
        self.rows = rows or []

    def execute(self, sql, params=None):
        self.queries.append((" ".join(str(sql).split()), params))

    def fetchall(self):
        return list(self.rows)


class DictionaryLearningTests(unittest.TestCase):
    def test_spellings_are_deduplicated_before_the_dictionary_check(self):
        """Проверка `aliases ?` идёт по разным написаниям (подзапрос DISTINCT), а не по
        каждой сделке: иначе Postgres сверял со словарём все ~270 тыс. строк."""
        cursor = _Cursor()
        with mock.patch.object(linker, "normalise_park_dictionary", return_value=0):
            linker.learn_dictionary(cursor)
        selects = [sql for sql, _params in cursor.queries if "FROM op_funnel_leads" in sql]
        self.assertEqual(2, len(selects))
        for sql in selects:
            inner = re.search(r"FROM \(SELECT DISTINCT lower\(btrim\((\w+)\)\) AS raw "
                              r"FROM op_funnel_leads WHERE btrim\(COALESCE\(\1, ''\)\) <> ''\) v", sql)
            self.assertIsNotNone(inner, sql)
            self.assertIn("d.aliases ? v.raw", sql)
            # Сверка со словарём — снаружи подзапроса, по его строкам.
            self.assertGreater(sql.index("NOT EXISTS"), sql.index(") v"))
        self.assertEqual([("park",), ("channel",)],
                         [params for sql, params in cursor.queries if "FROM op_funnel_leads" in sql])


class MarketingListsCacheTests(unittest.TestCase):
    def setUp(self):
        api._marketing_lists_cache.clear()

    def test_rows_come_from_the_cache_within_the_ttl(self):
        cursor = _Cursor(rows=[("Новый", 10)])
        with mock.patch.object(api.time, "monotonic", side_effect=[1000.0, 1100.0]):
            first = api._marketing_list_rows(cursor, "stages", "SELECT 1")
            second = api._marketing_list_rows(cursor, "stages", "SELECT 1")
        self.assertEqual(first, second)
        self.assertEqual(1, len(cursor.queries))

    def test_rows_are_reread_after_the_ttl(self):
        cursor = _Cursor(rows=[("Новый", 10)])
        ttl = api._MARKETING_LISTS_TTL_SECONDS
        with mock.patch.object(api.time, "monotonic", side_effect=[1000.0, 1000.0 + ttl + 1]):
            api._marketing_list_rows(cursor, "stages", "SELECT 1")
            api._marketing_list_rows(cursor, "stages", "SELECT 1")
        self.assertEqual(2, len(cursor.queries))

    def test_a_new_version_rereads_at_once(self):
        """Правка сопоставления ответственных обязана появиться в списке сразу."""
        cursor = _Cursor(rows=[("amo", "7", 3, "Иванова Айгерим", 5)])
        with mock.patch.object(api.time, "monotonic", side_effect=[1000.0, 1001.0, 1002.0]):
            api._marketing_list_rows(cursor, "handlers", "SELECT 1", version=(10, "t1"))
            api._marketing_list_rows(cursor, "handlers", "SELECT 1", version=(10, "t1"))
            api._marketing_list_rows(cursor, "handlers", "SELECT 1", version=(11, "t2"))
        self.assertEqual(2, len(cursor.queries))

    def test_the_three_global_lists_go_through_the_cache(self):
        """Этапы, причины и ответственные — через кэш; счётчики разговоров — нет."""
        import inspect
        source = inspect.getsource(api.marketing_options)
        for key in ('"stages"', '"reasons"', '"handlers"'):
            self.assertIn(f"_marketing_list_rows(\n            cur, {key}", source)
        self.assertIn("version=map_version", source)
        self.assertIn("SELECT COUNT(*), MAX(updated_at) FROM op_funnel_operator_map", source)
        # Счётчики по зрителю считаются запросом каждый раз (counted/by_value).
        self.assertIn("def counted(", source)
        self.assertNotIn("_marketing_list_rows(cur, \"calls\"", source)


if __name__ == "__main__":
    unittest.main()
