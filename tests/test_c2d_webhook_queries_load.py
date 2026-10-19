# -*- coding: utf-8 -*-
"""Запросы к ленте вебхуков Chat2Desk без полных проходов недели событий (19.10.2026).

EXPLAIN на проде: «жив ли поток» (MIN/MAX/COUNT одним SELECT), поиск клиента по точному
номеру (у client_phone своего индекса нет) и исключение «Реестра тестовых номеров»
(хеш-соединение вместо поиска в индексе хвоста) каждый раз проходили всю таблицу — по
1–2 с на вызов; «открытые чаты» проходили её трижды (~4 с). Тесты держат форму запросов,
при которой Postgres идёт по индексам, и неизменность ответа.
"""
import ast
import re
import unittest
from contextlib import contextmanager
from pathlib import Path

from driver_chats import queries as dc_queries
from test_numbers import keys as test_keys
from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
DATABASE_SOURCE = (ROOT / "database.py").read_text(encoding="utf-8-sig")
TAIL_INDEX_EXPR = "right(regexp_replace(client_phone, '\\D', '', 'g'), 9)"


def _method_source(name):
    module = source_cache.parse(DATABASE_SOURCE)
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "Database")
    node = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return "\n".join(DATABASE_SOURCE.splitlines()[node.lineno - 1:node.end_lineno])


class WebhookHealthTests(unittest.TestCase):
    def test_min_max_and_today_are_separate_index_lookups(self):
        source = " ".join(_method_source("get_c2d_webhook_health").split())
        self.assertIn("SELECT (SELECT MIN(event_at) FROM c2d_webhook_events), "
                      "(SELECT MAX(event_at) FROM c2d_webhook_events), "
                      "(SELECT COUNT(*) FROM c2d_webhook_events WHERE day = CURRENT_DATE)::int",
                      source)
        self.assertNotIn("FILTER (WHERE day = CURRENT_DATE)", source)


class ClosedRequestsIndexTests(unittest.TestCase):
    def test_closed_requests_have_a_partial_index(self):
        """«Закрывалось ли обращение» в «открытых чатах» — по маленькому индексу."""
        ddl = " ".join(DATABASE_SOURCE.split())
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_c2d_webhook_events_closed "
                      "ON c2d_webhook_events(request_id) "
                      "WHERE hook_type IN ('close_request', 'close_dialog');", ddl)
        query = " ".join(_method_source("get_c2d_open_chats_by_operator").split())
        self.assertIn("c.hook_type IN ('close_request', 'close_dialog')", query)


class WebhookClientLookupTests(unittest.TestCase):
    def test_exact_number_is_searched_through_the_tail_index(self):
        sql = " ".join(dc_queries._WEBHOOK_CLIENT_SQL.split())
        # Выражение хвоста — ДОСЛОВНО как в индексе, иначе он не возьмётся.
        self.assertIn(TAIL_INDEX_EXPR + " = ANY(%(tails)s)", sql)
        self.assertIn("client_phone = ANY(%(variants)s)", sql)
        self.assertIn("client_id IS NOT NULL", sql)
        index = re.search(r"idx_c2d_webhook_events_phone_tail\s+ON c2d_webhook_events \(\s*([^\n]+)\)\s*\n",
                          DATABASE_SOURCE)
        self.assertEqual(TAIL_INDEX_EXPR, index.group(1).strip())

    def test_tails_of_all_variants_are_passed(self):
        captured = {}

        class Cursor:
            def execute(self, sql, params):
                captured.update(params)

            def fetchone(self):
                return (41001,)

        variants = ["+77000000101", "77000000101", "87000000101", "7000000101"]
        self.assertEqual(41001, dc_queries.webhook_client_id(Cursor(), variants))
        self.assertEqual(variants, captured["variants"])
        self.assertEqual(["000000101"], captured["tails"])
        self.assertIsNone(dc_queries.webhook_client_id(Cursor(), []))


class TestNumbersSubqueryTests(unittest.TestCase):
    def test_each_registry_number_is_a_separate_index_lookup(self):
        """LATERAL с барьером OFFSET 0: без барьера планировщик разворачивает подзапрос
        в хеш-соединение с полным проходом ленты."""
        sql = " ".join(test_keys.C2D_WEBHOOK_TEST_REQUESTS_SQL.split())
        self.assertIn("CROSS JOIN LATERAL (SELECT _tw.request_id, _tw.client_phone "
                      "FROM c2d_webhook_events _tw", sql)
        self.assertIn("_tw.client_id IS NOT NULL OFFSET 0) _tw", sql)
        self.assertIn("right(regexp_replace(_tw.client_phone, '\\D', '', 'g'), 9) = right(_tpn.phone_key, 9)", sql)
        self.assertIn("right(regexp_replace(_tw.client_phone, '\\D', '', 'g'), 10) = _tpn.phone_key", sql)


if __name__ == "__main__":
    unittest.main()
