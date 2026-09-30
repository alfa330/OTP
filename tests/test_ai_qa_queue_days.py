# -*- coding: utf-8 -*-
"""Очередь ревью по дням (call_qa.api.review_queue_days / review_queue_day).

Стережём:
* сводка дня и строки дня — из ОДНОЙ выборки (_queue_items): «ждут 5» на
  карточке дня обязано совпасть с числом строк под ней;
* вторая половина сводки (оценено / проверено / средние баллы) — по всем
  оценённым разговорам дня, одним сгруппированным запросом, только по дням, где
  кто-то ждёт;
* дни — от свежих к старым, день без даты — последним и не теряется;
* ручка очереди понимает ?day=, а сводка — отдельная ручка с тем же скоупом.
"""
import ast
import datetime as dt
import unittest
from pathlib import Path
from unittest import mock

from call_qa import api
from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]


class _Cursor:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def _item(i, day, reasons, score, *, direction="Основа ОП", operator=7, time="12:00"):
    return {"id": i, "subject": "call", "day": day, "reasons": reasons, "ai_score": score,
            "direction": direction, "operator": f"Оператор {operator}", "_operator_id": operator,
            "datetime": f"23.09 {time}", "_sev": 0 if "critical" in reasons else 1}


ITEMS = [
    _item(1, "2026-09-23", ["critical", "lowconf", "pending"], 0),
    _item(2, "2026-09-23", ["lowconf", "pending"], 88, direction="Поток", operator=8, time="15:30"),
    _item(5, "2026-09-23", ["lowconf", "pending"], 77, time="09:05"),
    _item(3, "2026-09-21", ["lowconf", "pending"], 91),
    _item(4, None, ["ok"], None),
]


class DaysSummaryTests(unittest.TestCase):
    def _run(self, totals_rows, items=ITEMS):
        cursor = _Cursor(totals_rows)
        with mock.patch.object(api.config, "connect_ro", return_value=_Conn(cursor)), \
                mock.patch.object(api, "_queue_items", return_value=[dict(i) for i in items]) as queue:
            out = api.review_queue_days(filters={"q": None})
        return out, cursor, queue

    def test_day_summary_joins_open_items_with_day_totals(self):
        rows = [(dt.date(2026, 9, 23), 5, 3, 1, 70.456, 80.0, 2),
                (dt.date(2026, 9, 21), 1, 0, 0, 91.0, None, 0)]
        out, cursor, queue = self._run(rows)
        self.assertEqual(out["total"], 5)
        self.assertFalse(out["truncated"])
        self.assertEqual([d["day"] for d in out["days"]], ["2026-09-23", "2026-09-21", "none"])
        first = out["days"][0]
        self.assertEqual(first["open"], 3)
        self.assertEqual(first["critical"], 1)
        # Причины — по серьёзности, как метки строки.
        self.assertEqual(list(first["reasons"]), ["critical", "lowconf", "pending"])
        self.assertEqual(first["directions"], [{"name": "Основа ОП", "n": 2}, {"name": "Поток", "n": 1}])
        self.assertEqual(first["operators"], 2)
        self.assertEqual(first["open_ai_min"], 0)
        self.assertEqual((first["evaluated"], first["reviewed"], first["corrected"]), (5, 3, 1))
        self.assertEqual((first["ai_avg"], first["human_avg"], first["human_n"]), (70.5, 80.0, 2))
        # Сводка берёт выборку очереди без колонок сделки: они ей не нужны.
        self.assertFalse(queue.call_args.kwargs["with_deals"])
        # Итоги — одним запросом и только по дням, где кто-то ждёт.
        sql, params = cursor.sql[-1]
        self.assertIn("GROUP BY day", sql)
        self.assertIn("= ANY(%s::date[])", sql)
        self.assertEqual(params[-1], ["2026-09-23", "2026-09-21"])

    def test_day_without_totals_falls_back_to_what_is_known(self):
        out, _, _ = self._run([])
        none_day = out["days"][-1]
        self.assertEqual(none_day["day"], "none")
        self.assertEqual((none_day["open"], none_day["evaluated"], none_day["reviewed"]), (1, 1, 0))
        self.assertIsNone(none_day["ai_avg"])
        second = out["days"][1]
        self.assertEqual(second["ai_avg"], 91.0)

    def test_empty_or_impossible_queue(self):
        for items in ([], None):
            with mock.patch.object(api.config, "connect_ro", return_value=_Conn(_Cursor([]))), \
                    mock.patch.object(api, "_queue_items", return_value=items):
                self.assertEqual(api.review_queue_days(), {"days": [], "total": 0, "truncated": False})

    def test_truncated_when_the_fetch_cap_is_hit(self):
        many = [_item(i, "2026-09-23", ["ok"], 90) for i in range(3)]
        with mock.patch.object(api, "_QUEUE_FETCH_CAP", 3):
            out, _, _ = self._run([], items=many)
        self.assertTrue(out["truncated"])


class DayPageTests(unittest.TestCase):
    def test_day_page_is_a_slice_of_the_same_queue(self):
        with mock.patch.object(api.config, "connect_ro", return_value=_Conn(_Cursor([]))), \
                mock.patch.object(api, "_queue_items",
                                  side_effect=lambda *a, **k: [dict(i) for i in ITEMS]), \
                mock.patch.object(api, "_flag_stale_evaluations", side_effect=lambda page: [
                    p.update(stale=False) for p in page]):
            page = api.review_queue_day("2026-09-23")
            first = api.review_queue_day("2026-09-23", limit=1)
            none_page = api.review_queue_day("none")
        self.assertEqual(page["total"], 3)
        # Сначала серьёзное, дальше — по времени разговора, как лента дня.
        self.assertEqual([i["id"] for i in page["items"]], [1, 5, 2])
        self.assertEqual([i["id"] for i in first["items"]], [1])
        self.assertNotIn("_operator_id", page["items"][0])
        self.assertNotIn("_sev", page["items"][0])
        self.assertEqual([i["id"] for i in none_page["items"]], [4])


class RouteWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        cls.tree = source_cache.parse(cls.src)

    def _function(self, name):
        node = next(item for item in self.tree.body
                    if isinstance(item, ast.FunctionDef) and item.name == name)
        return ast.get_source_segment(self.src, node)

    def test_days_route_uses_the_queue_scope(self):
        body = self._function("api_ai_qa_review_queue_days")
        for piece in ("_ai_qa_guard()", "_ai_qa_requested_department(requester_id)",
                      "_ai_qa_direction_scope(requester_id)", "_ai_qa_list_filters()",
                      "review_queue_days(allowed_direction_ids=scope"):
            self.assertIn(piece, body)
        self.assertIn("'/api/ai-qa/review-queue/days'", self.src)

    def test_queue_route_serves_one_day(self):
        body = self._function("api_ai_qa_review_queue")
        self.assertIn("request.args.get('day')", body)
        self.assertIn("review_queue_day(day,", body)
        self.assertIn(r"re.fullmatch(r'\d{4}-\d{2}-\d{2}', day)", body)


if __name__ == "__main__":
    unittest.main()
