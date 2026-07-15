# -*- coding: utf-8 -*-
"""Метрики доверия ИИ-оценки: матрица «человек × ИИ», точность тревог, полнота,
надёжность зачёта. Считаются в call_qa.api._verdict_metrics — чистая функция."""
import unittest

from call_qa.api import _reviewed_metrics, _verdict_metrics


def _crit(idx, ai, source="transcript", name=None):
    return {"idx": idx, "ai": ai, "source": source, "name": name or f"Критерий {idx}"}


class VerdictMetricsTests(unittest.TestCase):
    def test_full_agreement(self):
        rows = [([_crit(0, "Correct"), _crit(1, "N/A")], ["Correct", "N/A"], "Поток")]
        m = _verdict_metrics(rows)
        self.assertEqual(m["agreement"], 100)
        self.assertEqual(m["total"], 2)
        self.assertIsNone(m["alarm_precision"])  # тревог не было — не 0%
        self.assertEqual(m["correct_reliability"]["pct"], 100)

    def test_false_alarm_and_miss(self):
        rows = [(
            [_crit(0, "Incorrect"),   # человек: Correct → ложная тревога
             _crit(1, "Incorrect"),   # человек: Error → подтверждённая тревога
             _crit(2, "Correct")],    # человек: Incorrect → пропуск
            ["Correct", "Error", "Incorrect"], "Основа",
        )]
        m = _verdict_metrics(rows)
        self.assertEqual(m["alarm_precision"], {"pct": 50, "hits": 1, "total": 2})
        self.assertEqual(m["recall"], {"pct": 50, "hits": 1, "total": 2})
        self.assertEqual(m["matrix"]["Correct"]["Incorrect"], 1)   # ложная тревога
        self.assertEqual(m["matrix"]["Incorrect"]["Correct"], 1)   # пропуск
        row = m["by_criterion"]
        self.assertEqual(sum(r["false_alarms"] for r in row), 1)
        self.assertEqual(sum(r["misses"] for r in row), 1)

    def test_deficiency_is_not_a_mismatch(self):
        # У человека частичный зачёт, у ИИ такого вердикта нет — не считаем расхождением.
        rows = [([_crit(0, "Incorrect")], ["Deficiency"], "Поток")]
        m = _verdict_metrics(rows)
        self.assertEqual(m["total"], 0)
        self.assertEqual(m["deficiency"], 1)
        self.assertIsNone(m["alarm_precision"])

    def test_ignores_pending_and_non_transcript(self):
        rows = [([
            _crit(0, "Pending"),                       # сбой/нет вердикта — не сверяем
            _crit(1, "Correct", source="system_api"),  # не по разговору — не сверяем
            _crit(5, "Correct"),                       # выходит за длину scores — нет эталона
        ], ["Correct", "Correct"], "Поток")]
        self.assertEqual(_verdict_metrics(rows)["total"], 0)

    def test_by_criterion_split_by_direction(self):
        # Один и тот же критерий в разных направлениях — разные строки (шкалы разные).
        rows = [
            ([_crit(0, "Correct", name="Приветствие")], ["Correct"], "Поток"),
            ([_crit(0, "Incorrect", name="Приветствие")], ["Correct"], "Основа"),
        ]
        rowsm = _verdict_metrics(rows)["by_criterion"]
        self.assertEqual(len(rowsm), 2)
        self.assertEqual({r["direction"] for r in rowsm}, {"Поток", "Основа"})

    def test_alarm_worse_than_naive_visible(self):
        # 9 «Верно» + 1 ложная тревога: согласие 90%, но точность тревог 0% — метрики
        # должны разделять эти сигналы (главный урок против одной цифры «согласие»).
        crits = [_crit(i, "Correct") for i in range(9)] + [_crit(9, "Incorrect")]
        m = _verdict_metrics([(crits, ["Correct"] * 10, "Поток")])
        self.assertEqual(m["agreement"], 90)
        self.assertEqual(m["alarm_precision"]["pct"], 0)


class _ReviewedCursor:
    def __init__(self, meta_rows, correction_rows):
        self.meta_rows = meta_rows
        self.correction_rows = correction_rows
        self.current = []
        self.queries = []

    def execute(self, sql, _params=None):
        normalized = " ".join(sql.split())
        self.queries.append(normalized)
        self.current = self.meta_rows if "FROM ai_evaluation_meta" in normalized else self.correction_rows

    def fetchall(self):
        return list(self.current)


class ReviewedMetricsTests(unittest.TestCase):
    def test_confirmed_review_ignores_old_correction(self):
        crit = [_crit(0, "Incorrect")]
        cur = _ReviewedCursor([(10, "confirmed", crit)], [(10, 0, "Correct")])
        reviewed = _reviewed_metrics(cur)
        self.assertEqual(reviewed["endorsed"], 1)
        self.assertEqual(reviewed["corrected"], 0)
        self.assertEqual(reviewed["alarm_precision"], {"pct": 100, "hits": 1, "total": 1})

    def test_adjudicated_review_keeps_soft_deleted_correction_in_history(self):
        crit = [_crit(0, "Incorrect")]
        cur = _ReviewedCursor([(10, "adjudicated", crit)], [(10, 0, "Correct")])
        reviewed = _reviewed_metrics(cur)
        self.assertEqual(reviewed["endorsed"], 0)
        self.assertEqual(reviewed["corrected"], 1)
        self.assertEqual(reviewed["alarm_precision"], {"pct": 0, "hits": 0, "total": 1})
        correction_query = next(q for q in cur.queries if "FROM qa_adjudications" in q)
        self.assertNotIn("is_active", correction_query)


if __name__ == "__main__":
    unittest.main()
