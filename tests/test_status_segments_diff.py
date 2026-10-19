# -*- coding: utf-8 -*-
"""Пересборка сегментов статусов пишет только разницу (19.10.2026).

Событие iCORE Phone пересобирает три дня оператора. Раньше на каждое событие окно
удалялось и вставлялось заново целиком (~300 строк), хотя событие меняет 1–3 сегмента:
~1,5 млн удалённых и вставленных строк в сутки, 15% WAL базы. Тест исполняет НАСТОЯЩИЙ
_rebuild_operator_status_segments_tx и проверяет, что лежащее и совпавшее не трогается,
лишнее удаляется по id, недостающее вставляется — и что авторитетные строки в сравнение
не попадают вовсе.
"""
import ast
import textwrap
import unittest
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path

from tests import source_cache

DATABASE_PATH = Path(__file__).resolve().parents[1] / "database.py"

DAY = date.today() - timedelta(days=5)          # старше 48 ч: открытого хвоста до «сейчас» нет
AT = lambda h, m=0: datetime.combine(DAY, dt_time(h, m))  # noqa: E731
OP = 7


def _rebuild_method():
    source = DATABASE_PATH.read_text(encoding="utf-8-sig")
    module = source_cache.parse(source)
    database_class = next(node for node in module.body
                          if isinstance(node, ast.ClassDef) and node.name == "Database")
    node = next(node for node in database_class.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "_rebuild_operator_status_segments_tx")
    captured = {"inserted": []}

    def fake_execute_values(_cursor, _query, values, **_kwargs):
        captured["inserted"].extend(values)

    namespace = {
        "CHAT_MANAGER_ACTION_STATUS_KEYS": set(),
        "STATUS_EVENTS_RETENTION_DAYS": 45,
        "STATUS_IMPORT_INSERT_PAGE_SIZE": 100,
        "date": date, "datetime": datetime, "dt_time": dt_time, "timedelta": timedelta,
        "execute_values": fake_execute_values,
    }
    lines = source.splitlines()
    exec(textwrap.dedent("\n".join(lines[node.lineno - 1:node.end_lineno])), namespace)
    return namespace["_rebuild_operator_status_segments_tx"], captured


class _Db:
    STATUS_SEGMENT_OPERATOR_LOCK_NAMESPACE = 915904142

    @staticmethod
    def _normalize_schedule_date(value):
        return value

    @staticmethod
    def _lock_operator_status_segments_tx(_cursor, _operator_ids):
        return None

    @staticmethod
    def _normalize_import_status_key(value):
        return str(value or "").strip().lower()

    @staticmethod
    def _status_import_event_kind_from_key(_key, kind):
        return kind or "status"

    @staticmethod
    def _canonicalize_status_transition_events(events):
        return list(events or [])

    @staticmethod
    def _split_status_segment_datetimes_by_day(start_value, end_value):
        return [{"status_date": start_value.date(), "start_at": start_value, "end_at": end_value,
                 "duration_sec": int((end_value - start_value).total_seconds())}]


class _Cursor:
    """События дня: 09:00 «готов», 12:00 «перерыв», 12:30 «готов», 18:00 «вышел» —
    это три сегмента. `existing` — что уже лежит в таблице (id + 8 колонок ключа)."""

    def __init__(self, existing):
        self.existing = existing
        self.rows = []
        self.queries = []
        self.rowcount = 0

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        self.queries.append((normalized, params))
        if "WITH candidates AS" in normalized:
            self.rows = [(1, OP, AT(9), "готов", None, "status"),
                         (2, OP, AT(12), "перерыв", None, "status"),
                         (3, OP, AT(12, 30), "готов", None, "status"),
                         (4, OP, AT(18), "вышел", None, "status")]
        elif normalized.startswith("SELECT id, operator_id, status_date"):
            self.rows = list(self.existing)
        else:
            self.rows = []

    def fetchall(self):
        return list(self.rows)

    def deleted_ids(self):
        return [params[0] for query, params in self.queries
                if query.startswith("DELETE FROM operator_status_segments")]


def _seg(start, end, key):
    return (OP, DAY, start, end, int((end - start).total_seconds()), key, None, None)


COMPUTED = [_seg(AT(9), AT(12), "готов"), _seg(AT(12), AT(12, 30), "перерыв"),
            _seg(AT(12, 30), AT(18), "готов")]


class StatusSegmentsDiffTests(unittest.TestCase):
    def run_rebuild(self, existing):
        method, captured = _rebuild_method()
        cursor = _Cursor(existing)
        result = method(_Db(), cursor, [OP], DAY - timedelta(days=1), DAY + timedelta(days=1))
        return result, cursor, captured["inserted"]

    def test_nothing_changed_nothing_written(self):
        """Повтор пересборки над тем же — ни одного удаления и ни одной вставки."""
        existing = [(100 + i, *row) for i, row in enumerate(COMPUTED)]
        result, cursor, inserted = self.run_rebuild(existing)
        self.assertEqual([], inserted)
        self.assertEqual([], cursor.deleted_ids())
        # Сводки импорта видят прежний смысл: сколько сегментов окна заменено.
        self.assertEqual(3, result["segments_saved"])
        self.assertEqual(3, result["deleted_segments"])
        self.assertEqual(0, result["segments_inserted"])
        self.assertEqual(0, result["segments_removed"])

    def test_only_the_difference_is_written(self):
        """Лежал старый хвост (12:30–17:00) и лишний сегмент — их удаляем по id, пишем
        недостающий 12:30–18:00; совпавшие 09:00–12:00 и 12:00–12:30 не трогаем."""
        existing = [(100, *COMPUTED[0]), (101, *COMPUTED[1]),
                    (102, *_seg(AT(12, 30), AT(17), "готов")),
                    (103, *_seg(AT(19), AT(20), "готов"))]
        result, cursor, inserted = self.run_rebuild(existing)
        self.assertEqual([COMPUTED[2]], inserted)
        self.assertEqual([[102, 103]], cursor.deleted_ids())
        self.assertEqual(4, result["deleted_segments"])
        self.assertEqual(1, result["segments_inserted"])
        self.assertEqual(2, result["segments_removed"])

    def test_any_differing_column_counts_as_a_change(self):
        """Ключ сравнения — вся строка: другой статус, заметка, длительность или автор
        импорта — это другой сегмент, и он переписывается, как при полной замене."""
        variants = [
            (OP, DAY, AT(9), AT(12), 10800, "занят", None, None),
            (OP, DAY, AT(9), AT(12), 10800, "готов", "заметка", None),
            (OP, DAY, AT(9), AT(12), 10799, "готов", None, None),
            (OP, DAY, AT(9), AT(12), 10800, "готов", None, 55),
        ]
        for variant in variants:
            existing = [(100, *variant), (101, *COMPUTED[1]), (102, *COMPUTED[2])]
            _result, cursor, inserted = self.run_rebuild(existing)
            self.assertEqual([[100]], cursor.deleted_ids(), variant)
            self.assertEqual([COMPUTED[0]], inserted, variant)

    def test_duplicates_are_matched_one_to_one(self):
        """Два одинаковых лежащих сегмента при одном посчитанном — лишний удаляется."""
        existing = [(100, *COMPUTED[0]), (101, *COMPUTED[0]), (102, *COMPUTED[1]),
                    (103, *COMPUTED[2])]
        _result, cursor, inserted = self.run_rebuild(existing)
        self.assertEqual([], inserted)
        self.assertEqual(1, len(cursor.deleted_ids()[0]))
        self.assertIn(cursor.deleted_ids()[0][0], (100, 101))

    def test_authoritative_rows_are_outside_the_comparison(self):
        """Выборка лежащих — только неавторитетные строки окна этих операторов; DELETE
        идёт по id из неё, а не условием по окну."""
        _result, cursor, _inserted = self.run_rebuild([])
        existing_sql, params = next((q, p) for q, p in cursor.queries
                                    if q.startswith("SELECT id, operator_id, status_date"))
        self.assertIn("COALESCE(is_authoritative, FALSE) = FALSE", existing_sql)
        self.assertIn("operator_id = ANY(%s)", existing_sql)
        self.assertEqual(([OP], DAY - timedelta(days=1), DAY + timedelta(days=1)), params)
        self.assertEqual([], cursor.deleted_ids())


if __name__ == "__main__":
    unittest.main()
