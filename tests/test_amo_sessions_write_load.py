# -*- coding: utf-8 -*-
"""Холостая запись: выгрузка amoCRM и продление сессий (19.10.2026).

Замер 15–19.10.2026: выгрузка сделок amoCRM (раз в три часа и перед каждой отбивкой, все
сделки за 16 дней) переписывала и неизменившиеся сделки — ~110 тыс. UPDATE в сутки, ~600 МБ
WAL за 4 дня; продление last_seen_at сессии раз в минуту на каждую вкладку — ~130 тыс.
UPDATE в сутки, ~670 МБ WAL. Тесты держат: неизменившаяся сделка не переписывается (при
любой изменившейся колонке — переписывается), сессия продлевается не чаще раза в 5 минут.

Необязательная часть на настоящем Postgres — AMO_TEST_PORT (порт локального кластера).
"""
import ast
import os
import re
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
DATABASE_SOURCE = (ROOT / "database.py").read_text(encoding="utf-8-sig")
BOT_SOURCE = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
UPDATED_COLUMNS = ("name", "created_at", "created_date", "stage", "responsible", "tags",
                   "utm_source", "utm_medium", "utm_campaign", "utm_term", "registered",
                   "trip_done", "robot_closed")


def _method(name):
    module = source_cache.parse(DATABASE_SOURCE)
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "Database")
    node = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return node, "\n".join(DATABASE_SOURCE.splitlines()[node.lineno - 1:node.end_lineno])


class AmoLeadsNoopGuardTests(unittest.TestCase):
    def test_every_updated_column_is_in_the_guard(self):
        _node, source = _method("upsert_amo_leads")
        sql = " ".join(source.split())
        guard = sql[sql.index("WHERE (amo_leads."):]
        left, right = guard.split("IS DISTINCT FROM")
        for column in UPDATED_COLUMNS:
            self.assertIn(f"{column} = EXCLUDED.{column}", sql)
            self.assertIn(f"amo_leads.{column}", left)
            self.assertIn(f"EXCLUDED.{column}", right)
        # Сравниваются ровно те колонки, что переписывает SET, кроме отметки выгрузки.
        set_columns = re.findall(r"(\w+) = EXCLUDED\.\w+", sql)
        self.assertEqual(sorted(UPDATED_COLUMNS), sorted(set_columns))
        self.assertNotIn("synced_at", guard)


class SessionTouchIntervalTests(unittest.TestCase):
    def test_default_interval_is_five_minutes(self):
        self.assertIn("JWT_SESSION_TOUCH_INTERVAL_SECONDS = max(0, int(os.getenv("
                      "'JWT_SESSION_TOUCH_INTERVAL_SECONDS', '300')))", BOT_SOURCE)

    def test_ip_or_browser_change_still_touches_at_once(self):
        start = BOT_SOURCE.index("def _should_touch_user_session")
        body = BOT_SOURCE[start:BOT_SOURCE.index("\ndef ", start + 10)]
        self.assertIn('if ip_address and ip_address != session.get("ip_address"):', body)
        self.assertIn('if user_agent and user_agent != session.get("user_agent"):', body)
        self.assertIn(">= JWT_SESSION_TOUCH_INTERVAL_SECONDS", body)


PORT = os.environ.get("AMO_TEST_PORT")


@pytest.mark.skipif(not PORT, reason="needs local PostgreSQL (AMO_TEST_PORT)")
class AmoLeadsUpsertSqlTests(unittest.TestCase):
    def test_unchanged_deal_is_not_rewritten(self):
        import uuid

        import psycopg2
        from psycopg2.extras import execute_values

        node, _source = _method("upsert_amo_leads")
        namespace = {"execute_values": execute_values}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "database.py", "exec"), namespace)
        upsert = namespace["upsert_amo_leads"]
        ddl = re.search(r"CREATE TABLE IF NOT EXISTS amo_leads \((.*?)\);", DATABASE_SOURCE, re.S).group(1)
        schema = "amo_" + uuid.uuid4().hex[:10]
        conn = psycopg2.connect(host="127.0.0.1", port=int(PORT), user="postgres", dbname="postgres",
                                password=os.environ.get("PGPASSWORD"))
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"SET search_path TO {schema}")
        try:
            cur.execute(f"CREATE TABLE amo_leads ({ddl})")

            class Db:
                @contextmanager
                def _get_cursor(self):
                    yield cur

            def deal(lead_id, stage="Новая заявка"):
                return {"lead_id": lead_id, "name": f"Сделка {lead_id}",
                        "created_at": datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc),
                        "created_date": date(2026, 10, 18), "stage": stage, "responsible": "Менеджер",
                        "tags": "", "utm_source": "", "utm_medium": "", "utm_campaign": "", "utm_term": "",
                        "registered": 0, "trip_done": 0, "robot_closed": 0}

            def versions():
                cur.execute("SELECT lead_id, xmin::text, synced_at FROM amo_leads ORDER BY 1")
                return {row[0]: (row[1], row[2]) for row in cur.fetchall()}

            self.assertEqual(3, upsert(Db(), [deal(1), deal(2), deal(3)]))
            first = versions()
            self.assertEqual(3, upsert(Db(), [deal(1), deal(2), deal(3)]))
            self.assertEqual(first, versions(), "та же выгрузка не должна переписать ни одной строки")
            upsert(Db(), [deal(1), deal(2, stage="Успешно"), deal(3)])
            third = versions()
            self.assertEqual(first[1], third[1])
            self.assertEqual(first[3], third[3])
            self.assertNotEqual(first[2][0], third[2][0], "изменившаяся сделка переписывается")
            cur.execute("SELECT stage FROM amo_leads WHERE lead_id = 2")
            self.assertEqual("Успешно", cur.fetchone()[0])
        finally:
            cur.execute(f"DROP SCHEMA {schema} CASCADE")
            conn.close()


if __name__ == "__main__":
    unittest.main()
