# -*- coding: utf-8 -*-
"""Метрики отметок из ТЗ #273: порог отсутствия и время в работе без обеда.

Раздел «Отметки» показывает кадровому учёту три числа: все отметки за день, время
в работе и минуты опоздания. Здесь сторожатся два правила, которые легко сломать
обратно, потому что оба выглядят как «просто константа»:

* порог отсутствия — 15 минут, а не 10: столько компания даёт на дорогу до
  терминала, и с этой минуты не отметившийся считается опоздавшим;
* обед вычитается из времени в работе, но НЕ у всех: у смены короче пяти часов
  перерыв не планируется вовсе, и плоский час уводил бы её в минус.

Отчёт не собираем: он тянет Workpace по два запроса на каждый день периода.
Проверяем чистые функции и то, что отчёт правда ими пользуется.
"""

import unittest
from pathlib import Path

from group_late import config
from group_late.helpers import lunch_seconds, net_work_seconds

ROOT = Path(__file__).resolve().parents[1]
REPORTS_SRC = (ROOT / "group_late" / "reports.py").read_text(encoding="utf-8-sig")
LATENESS_SRC = (ROOT / "group_late" / "lateness.py").read_text(encoding="utf-8-sig")

HOUR = 3600


class MissingThresholdTests(unittest.TestCase):
    def test_threshold_is_fifteen_minutes(self):
        self.assertEqual(config.MISSING_IN_AFTER_MINUTES, 15)

    def test_threshold_has_a_single_consumer(self):
        # Порог читает только опрос. Если он появится где-то ещё, значения
        # разъедутся: у отчёта своё определение опоздания (LATE_THRESHOLD_MINUTES).
        self.assertEqual(LATENESS_SRC.count("config.MISSING_IN_AFTER_MINUTES"), 1)
        self.assertNotIn("MISSING_IN_AFTER_MINUTES", REPORTS_SRC)

    def test_lateness_threshold_untouched(self):
        # Порог опоздания обслуживает и ранний уход: подняв его «под опоздания»,
        # мы разрешили бы уходить раньше без нарушения.
        self.assertEqual(config.LATE_THRESHOLD_MINUTES, 1)


class LunchDeductionTests(unittest.TestCase):
    def test_full_shift_loses_exactly_the_lunch(self):
        self.assertEqual(lunch_seconds(9 * HOUR), config.LUNCH_BREAK_MINUTES * 60)
        self.assertEqual(net_work_seconds(9 * HOUR), 8 * HOUR)

    def test_real_break_of_the_person_wins(self):
        # Полчаса по расписанию — вычитаем полчаса, а не общий час.
        self.assertEqual(lunch_seconds(9 * HOUR, 1800), 1800)
        self.assertEqual(net_work_seconds(9 * HOUR, 1800), 9 * HOUR - 1800)

    def test_zero_break_means_no_deduction(self):
        # У расписания без перерыва вычитать нечего — это не «нет данных».
        self.assertEqual(lunch_seconds(9 * HOUR, 0), 0)
        self.assertEqual(net_work_seconds(9 * HOUR, 0), 9 * HOUR)

    def test_broken_break_value_falls_back_to_the_rule(self):
        for junk in ("", "нет", None):
            self.assertEqual(lunch_seconds(9 * HOUR, junk), config.LUNCH_BREAK_MINUTES * 60, junk)

    def test_real_break_never_exceeds_the_span(self):
        self.assertLessEqual(lunch_seconds(6 * HOUR, 99 * HOUR), 6 * HOUR)
        self.assertGreaterEqual(net_work_seconds(6 * HOUR, 99 * HOUR), 0)

    def test_short_shift_keeps_everything(self):
        # 4 часа — короче порога, перерыв не планируется, вычитать нечего.
        self.assertEqual(lunch_seconds(4 * HOUR), 0)
        self.assertEqual(net_work_seconds(4 * HOUR), 4 * HOUR)

    def test_boundary_is_inclusive(self):
        edge = config.LUNCH_BREAK_MIN_WORK_MINUTES * 60
        self.assertEqual(lunch_seconds(edge - 60), 0)
        self.assertEqual(lunch_seconds(edge), config.LUNCH_BREAK_MINUTES * 60)

    def test_never_goes_negative(self):
        # Главный дефект, от которого сторожим: отрицательного времени в работе
        # в отчёте быть не может ни при каком отрезке.
        for span in (0, -HOUR, 1, 59, HOUR, 5 * HOUR, 24 * HOUR):
            self.assertGreaterEqual(net_work_seconds(span), 0, span)
            self.assertLessEqual(lunch_seconds(span), max(0, span), span)

    def test_zero_and_negative_spans_are_empty(self):
        self.assertEqual(net_work_seconds(0), 0)
        self.assertEqual(net_work_seconds(-1), 0)
        self.assertEqual(lunch_seconds(0), 0)


class ReportUsesTheDeductionTests(unittest.TestCase):
    def test_report_computes_work_time_through_the_helper(self):
        self.assertIn("work_seconds = _net_work_seconds(span_sec, planned_break)", REPORTS_SRC)
        self.assertIn("lunch_sec = _lunch_seconds(span_sec, planned_break)", REPORTS_SRC)

    def test_report_prefers_the_real_break_of_the_person(self):
        # Обед берётся из расписания человека, когда источник его знает: у части
        # людей он не час, и плоское правило соврало бы именно им.
        self.assertIn('planned_break = (span or {}).get("breakSeconds")', REPORTS_SRC)

    def test_report_shows_position_and_source_system(self):
        # Требование ТЗ: кадровик ищет по должности и видит, где смотреть первоисточник.
        self.assertIn('"Должность"', REPORTS_SRC)
        self.assertIn('"Система"', REPORTS_SRC)
        self.assertIn('"Клокстер" if row_span.get("markSystem") == CLOCKSTER_SYSTEM', REPORTS_SRC)

    def test_norm_is_net_of_lunch_too(self):
        # Иначе у отработавшего ровно по графику «Отклонение» показывало бы −1:00.
        self.assertIn(
            "norm_sec = (plan_out_dt - plan_in_dt).total_seconds() - lunch_sec",
            REPORTS_SRC,
        )

    def test_columns_say_that_lunch_is_deducted(self):
        # Число без подписи читается как «отработал столько», и HR сверяет его
        # с табелем; подпись обязана говорить, что обед уже вычтен.
        self.assertIn('"Отработано (без обеда)"', REPORTS_SRC)
        self.assertIn('"Всего отработано без обеда (HH:MM)"', REPORTS_SRC)


if __name__ == "__main__":
    unittest.main()
