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

    def fetchmany(self, size):
        # Сводка читает пачками: отдаём по одной строке, чтобы цикл пачек
        # действительно отработал больше одного раза.
        batch, self.rows = self.rows[:1], self.rows[1:]
        return batch

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
        # Сводка берёт выборку очереди без колонок сделки и облегчённую (без
        # обоснований критериев и балла человека): они ей не нужны.
        self.assertFalse(queue.call_args.kwargs["with_deals"])
        self.assertTrue(queue.call_args.kwargs["slim"])
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

    def test_truncated_only_at_the_summary_safety_cap(self):
        many = [_item(i, "2026-09-23", ["ok"], 90) for i in range(3)]
        # Рабочий потолок списка сводку больше не режет: с ежедневной выборкой он
        # выбрасывал бы старые дни вместе с критическими разговорами.
        with mock.patch.object(api, "_QUEUE_FETCH_CAP", 3):
            out, _, _ = self._run([], items=many)
        self.assertFalse(out["truncated"])
        with mock.patch.object(api, "_QUEUE_SUMMARY_CAP", 3):
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

    def test_impossible_day_is_an_empty_day_not_a_database_error(self):
        with mock.patch.object(api.config, "connect_ro") as connect, \
                mock.patch.object(api, "_queue_items") as queue:
            self.assertEqual(api.review_queue_day("2026-02-30"), {"items": [], "total": 0})
        connect.assert_not_called()
        queue.assert_not_called()

    def test_day_page_asks_the_queue_for_its_day_only(self):
        with mock.patch.object(api.config, "connect_ro", return_value=_Conn(_Cursor([]))), \
                mock.patch.object(api, "_flag_stale_evaluations", side_effect=lambda rows: None), \
                mock.patch.object(api, "_queue_items", return_value=[]) as queue:
            api.review_queue_day("2026-09-23")
            api.review_queue_day(api.QUEUE_NO_DAY)
        self.assertEqual([c.kwargs["day"] for c in queue.call_args_list],
                         ["2026-09-23", api.QUEUE_NO_DAY])

    def test_pages_of_a_day_add_up_without_gaps(self):
        """Страницы дня складываются в весь день без пропусков и повторов, а равные
        по времени строки (у переписок в подписи только дата) упорядочены самим
        разговором, а не порядком оценки ИИ, который «Переоценить» меняет."""
        day = [_item(i, "2026-09-23", ["critical"] if i in (7, 3) else ["lowconf"], 80,
                     time="10:00" if i % 2 else f"09:{i:02d}") for i in range(11, -1, -1)]
        with mock.patch.object(api.config, "connect_ro", return_value=_Conn(_Cursor([]))), \
                mock.patch.object(api, "_flag_stale_evaluations", side_effect=lambda rows: None), \
                mock.patch.object(api, "_queue_items", side_effect=lambda *a, **k: [dict(i) for i in day]):
            pages = [api.review_queue_day("2026-09-23", limit=5, offset=offset) for offset in (0, 5, 10)]
        ids = [item["id"] for page in pages for item in page["items"]]
        self.assertEqual(ids, [3, 7, 0, 2, 4, 6, 8, 10, 1, 5, 9, 11])


class QueueItemsColumnsTests(unittest.TestCase):
    """Колонки выборки очереди: день и id сотрудника встали перед колонками
    сделки — сдвиг на одну колонку молча переписал бы сделку в строке."""

    def test_row_maps_to_item_and_undated_row_gets_the_card_key(self):
        stamp = dt.datetime(2026, 9, 23, 7, 4, tzinfo=dt.timezone.utc)
        base = (5, "Основа ОП", "Оператор", "23.09 12:04", None, [], 0.9, stamp, 71, "fp", {},
                "imported_call", {}, 90, {"unchecked_weight": 10})
        dated = base + (dt.date(2026, 9, 23), 7, "client") + (None,) * api._DEAL_COLUMN_COUNT
        undated = ((6,) + base[1:] + (None, None, None) + ("D-1",)
                   + (None,) * (api._DEAL_COLUMN_COUNT - 1))
        cursor = _Cursor([dated, undated])
        items = api._queue_items(cursor, with_deals=False)
        by_id = {item["id"]: item for item in items}
        self.assertEqual(by_id[5]["day"], "2026-09-23")
        self.assertEqual(by_id[5]["_operator_id"], 7)
        # Кто завершил звонок «на сейчас» — для пометки «устарела» (как у карточки).
        self.assertEqual(by_id[5]["_call_end_party"], "client")
        self.assertIn(api._SUBJECT_CALL_END_PARTY, cursor.sql[-1][0])
        self.assertEqual(by_id[5]["unchecked_weight"], 10)
        self.assertEqual(by_id[5]["ai_score"], 90)
        self.assertIsNone(by_id[5]["deal"])
        # Без даты — тот же ключ, что у карточки «Без даты»: по нему фронт находит день.
        self.assertEqual(by_id[6]["day"], api.QUEUE_NO_DAY)
        self.assertEqual(by_id[6]["deal"]["id"], "D-1")
        sql = cursor.sql[-1][0]
        self.assertLess(sql.index(api._SUBJECT_DAY), sql.index("FROM ai_review_cache rc"))

    def test_day_is_filtered_in_sql_before_the_cap(self):
        cursor = _Cursor([])
        api._queue_items(cursor, with_deals=False, day="2026-09-23")
        sql, params = cursor.sql[-1]
        where = sql[sql.index("WHERE rc.model"):]
        self.assertIn(f"AND {api._SUBJECT_DAY} = %s", where)
        self.assertLess(where.index(f"AND {api._SUBJECT_DAY} = %s"), where.index("LIMIT %s"))
        self.assertEqual(params[-2:], ("2026-09-23", api._QUEUE_FETCH_CAP))
        api._queue_items(cursor, with_deals=False, day=api.QUEUE_NO_DAY)
        sql, params = cursor.sql[-1]
        self.assertIn(f"AND {api._SUBJECT_DAY} IS NULL", sql)
        self.assertNotIn(api.QUEUE_NO_DAY, params)

    def test_slim_summary_reads_only_what_the_reasons_need(self):
        stamp = dt.datetime(2026, 9, 23, 7, 4, tzinfo=dt.timezone.utc)
        slim_criteria = [{"ai": "Incorrect", "source": "transcript", "conf": 0.9, "is_critical": True}]
        row = ((5, "Основа ОП", "Оператор", "23.09 12:04", None, slim_criteria, 0.9, stamp, 73,
                None, None, "imported_call", {}, 40, None, dt.date(2026, 9, 23), 7, None)
               + (None,) * api._DEAL_COLUMN_COUNT)
        cursor = _Cursor([row, row[:15] + (dt.date(2026, 9, 22),) + row[16:]])
        items = api._queue_items(cursor, with_deals=False, slim=True)
        self.assertEqual(len(items), 2)                      # обе пачки дочитаны
        sql, params = cursor.sql[-1]
        self.assertNotIn("LEFT JOIN LATERAL", sql)            # отпечаток прогона не нужен
        self.assertNotIn(api._SUBJECT_HUMAN_SCORE, sql)       # балл человека не нужен
        self.assertNotIn(api._SUBJECT_CALL_END_PARTY, sql)    # и сторона завершения тоже
        self.assertIn(api._CRITERIA_FOR_REASONS, sql)
        self.assertEqual(params[-1], api._QUEUE_SUMMARY_CAP)
        # Причины считаются тем же правилом, что у строк дня.
        self.assertEqual(items[0]["reasons"], ["critical"])
        self.assertEqual(items[0]["day"], "2026-09-23")


class CompatModeTests(unittest.TestCase):
    """До миграции меты очередь не пустеет («Всё проверено» было бы неправдой), а
    показывает последние звонки одним днём «Без даты» — как прежний список."""

    def test_days_and_day_fall_back_to_recent_calls(self):
        compat = RuntimeError("нет таблицы")
        compat.pgcode = "42P01"
        recent = [{"id": 1, "direction": "Основа ОП", "operator": "А", "datetime": "23.09 10:00",
                   "human_score": None, "reasons": ["new"]},
                  {"id": 2, "direction": "Поток", "operator": "Б", "datetime": "23.09 11:00",
                   "human_score": None, "reasons": ["new"]}]
        with mock.patch.object(api.config, "connect_ro", return_value=_Conn(_Cursor([]))), \
                mock.patch.object(api, "_queue_items", side_effect=compat), \
                mock.patch.object(api, "_recent_calls_fallback", return_value=recent):
            summary = api.review_queue_days()
            day = api.review_queue_day(api.QUEUE_NO_DAY, limit=1)
            other = api.review_queue_day("2026-09-23")
        self.assertTrue(summary["compat"])
        self.assertEqual([d["day"] for d in summary["days"]], ["none"])
        self.assertEqual(summary["days"][0]["open"], 2)
        self.assertEqual(summary["days"][0]["reasons"], {"new": 2})
        self.assertEqual((day["total"], [i["id"] for i in day["items"]]), (2, [1]))
        self.assertEqual(other, {"items": [], "total": 0})


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
