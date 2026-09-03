# -*- coding: utf-8 -*-
"""Учёт часов уволенных: увольнение не должно стирать последнюю отработанную смену.

Часы за день считаются как пересечение смены из `work_shifts` со статусами из
`operator_status_segments`. Период статуса (увольнение, отпуск, больничный, Б/С)
снимал смены с даты начала ВКЛЮЧИТЕЛЬНО, а СВ ставит датой увольнения последний
рабочий день — поэтому смена за него удалялась, пересчёт получал пустое окно и
обнулял уже отработанные часы.

Правило: день, за который есть отработанное время, статус не снимает. «Отработано»
— часы или звонки в учёте часов за этот день либо рабочие статусы, пересекающиеся
со сменой (часы могли ещё не посчитаться, агрегация идёт следом за импортом).
Дни без отработанного времени снимаются как раньше — иначе отпуск задним числом
перестал бы чистить график.
"""

import ast
import textwrap
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
DATABASE_SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")
DATABASE_MODULE = source_cache.parse(DATABASE_SOURCE)
DATABASE_CLASS = next(
    node
    for node in DATABASE_MODULE.body
    if isinstance(node, ast.ClassDef) and node.name == "Database"
)

OPERATOR_WORK_KEYS = {'готов', 'занят', 'занята', 'перезвон', 'зарезервировано'}


def _method_source(name):
    method = next(
        node
        for node in DATABASE_CLASS.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return textwrap.dedent(ast.get_source_segment(DATABASE_SOURCE, method))


def _load_methods(*names):
    """Исполняет методы Database без импорта модуля (он поднимает пул к БД)."""
    namespace = {"date": date, "datetime": datetime, "timedelta": timedelta}
    for name in names:
        exec(_method_source(name), namespace)
    return namespace


class _FakeSelf:
    """Минимальный владелец методов: только то, что они вправду вызывают."""

    def __init__(self, work_keys=OPERATOR_WORK_KEYS, model_code='operator'):
        self._work_keys = set(work_keys)
        self._model_code = model_code

    def _normalize_schedule_date(self, value):
        if value is None:
            raise ValueError("shift_date is required")
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return datetime.strptime(str(value), '%Y-%m-%d').date()

    def _get_operator_calculation_model_tx(self, cursor, operator_id, as_of=None):
        return self._model_code

    def _status_profile_for_calculation_model(self, model_code):
        return {'work': self._work_keys}


class _ScriptedCursor:
    """Отвечает по порядку заготовленными ответами и запоминает запросы."""

    def __init__(self, answers):
        self._answers = list(answers)
        self.calls = []
        self.rowcount = 0
        self._current = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        self._current = self._answers.pop(0) if self._answers else []

    def fetchall(self):
        return list(self._current)

    def fetchone(self):
        return self._current[0] if self._current else None


class WorkedDayDetectionTests(unittest.TestCase):
    """_schedule_days_with_worked_time_tx — какие дни статус снимать не вправе."""

    def setUp(self):
        self.ns = _load_methods("_schedule_days_with_worked_time_tx")
        self.fake = _FakeSelf()

    def _run(self, answers, start='2026-08-31', end=None):
        cursor = _ScriptedCursor(answers)
        worked = self.ns["_schedule_days_with_worked_time_tx"](
            self.fake, cursor, 321, start, end
        )
        return worked, cursor

    def test_day_with_hours_in_the_section_is_kept(self):
        """Часы за день уже посчитаны — трогать день нельзя."""
        worked, cursor = self._run([
            [(date(2026, 8, 31),)],          # смены в периоде
            [(date(2026, 8, 31),)],          # часы/звонки за день есть
        ])
        self.assertEqual({date(2026, 8, 31)}, worked)
        # Раз день уже признан отработанным, статусы для него не спрашиваем.
        self.assertEqual(2, len(cursor.calls))

    def test_hours_query_looks_at_both_work_time_and_calls(self):
        """Звонки без часов — тот же признак: день отработан, а агрегация отстала."""
        _, cursor = self._run([
            [(date(2026, 8, 31),)],
            [],
            [],
        ])
        hours_sql, hours_params = cursor.calls[1]
        self.assertIn("FROM daily_hours", hours_sql)
        self.assertIn("COALESCE(work_time, 0) > 0 OR COALESCE(calls, 0) > 0", hours_sql)
        self.assertEqual((321, [date(2026, 8, 31)]), hours_params)

    def test_work_statuses_inside_the_shift_keep_the_day(self):
        """Часов ещё нет, но по статусам человек работал — день остаётся."""
        worked, cursor = self._run([
            [(date(2026, 8, 31),)],
            [],            # часов и звонков ещё нет
            [(1,)],        # рабочий статус пересекается со сменой
        ])
        self.assertEqual({date(2026, 8, 31)}, worked)
        status_sql, status_params = cursor.calls[2]
        self.assertIn("FROM work_shifts ws", status_sql)
        self.assertIn("JOIN operator_status_segments oss", status_sql)
        self.assertEqual(sorted(OPERATOR_WORK_KEYS), status_params[0])
        self.assertEqual((321, date(2026, 8, 31)), status_params[1:])

    def test_night_shift_window_reaches_past_midnight(self):
        """Смена 20:30-00:00 заканчивается уже следующим днём — иначе хвост потерян."""
        _, cursor = self._run([
            [(date(2026, 8, 31),)],
            [],
            [],
        ])
        status_sql, _ = cursor.calls[2]
        self.assertIn(
            "CASE WHEN ws.end_time <= ws.start_time"
            " THEN INTERVAL '1 day' ELSE INTERVAL '0 day' END",
            status_sql,
        )
        self.assertIn("oss.status_date >= ws.shift_date - 1", status_sql)
        self.assertIn("oss.status_date <= ws.shift_date + 1", status_sql)

    def test_day_without_any_work_is_not_kept(self):
        """Страж от переусердствования: неотработанный день статус снимает как раньше."""
        worked, _ = self._run([
            [(date(2026, 8, 31),)],
            [],
            [],
        ])
        self.assertEqual(set(), worked)

    def test_future_days_are_not_checked(self):
        """Отработать будущее нельзя, а у открытого увольнения период бесконечен."""
        worked, cursor = self._run([[]], start=(date.today() + timedelta(days=1)).isoformat())
        self.assertEqual(set(), worked)
        self.assertEqual([], cursor.calls)

    def test_open_period_is_clamped_to_today(self):
        _, cursor = self._run([[]], start='2026-08-31', end=None)
        _, params = cursor.calls[0]
        self.assertEqual(date.today(), params[2])

    def test_model_of_the_day_decides_which_statuses_count(self):
        """У ТЭЗ и чат-менеджеров рабочие статусы свои — берём профиль дня."""
        self.fake = _FakeSelf(work_keys={'active', 'work in crm'}, model_code='tez_line')
        _, cursor = self._run([
            [(date(2026, 8, 31),)],
            [],
            [],
        ])
        _, status_params = cursor.calls[2]
        self.assertEqual(['active', 'work in crm'], status_params[0])


class PeriodShiftDeletionTests(unittest.TestCase):
    """_delete_shifts_for_period_tx — отработанные дни исключаются из удаления."""

    def setUp(self):
        self.ns = _load_methods("_delete_shifts_for_period_tx")
        self.fake = _FakeSelf()

    def _delete(self, start='2026-08-31', end=None, keep_days=None):
        cursor = _ScriptedCursor([[], []])
        result = self.ns["_delete_shifts_for_period_tx"](
            self.fake, cursor, 321, start, end, keep_days
        )
        return result, cursor

    def test_worked_day_is_excluded_from_the_delete(self):
        result, cursor = self._delete(keep_days=[date(2026, 8, 31)])
        shifts_sql, shifts_params = cursor.calls[0]
        self.assertIn("DELETE FROM work_shifts", shifts_sql)
        self.assertIn("NOT (shift_date = ANY(%s::date[]))", shifts_sql)
        self.assertEqual([date(2026, 8, 31)], shifts_params[2])
        self.assertEqual([date(2026, 8, 31)], result['kept_shift_days'])

    def test_without_worked_days_the_filter_is_inert(self):
        """Пустой список превращается в NULL, а не в «не удалять ничего»."""
        result, cursor = self._delete(keep_days=[])
        _, shifts_params = cursor.calls[0]
        self.assertIsNone(shifts_params[2])
        self.assertEqual([], result['kept_shift_days'])

    def test_closed_period_keeps_both_bounds(self):
        _, cursor = self._delete(start='2026-08-10', end='2026-08-12', keep_days=[date(2026, 8, 10)])
        shifts_sql, shifts_params = cursor.calls[0]
        self.assertIn("shift_date >= %s", shifts_sql)
        self.assertIn("shift_date <= %s", shifts_sql)
        self.assertEqual((321, date(2026, 8, 10), date(2026, 8, 12)), shifts_params[:3])

    def test_days_off_are_still_cleared_for_kept_days(self):
        """Выходной и смена в один день не сосуществуют — фильтр к days_off не нужен."""
        _, cursor = self._delete(keep_days=[date(2026, 8, 31)])
        days_off_sql, _ = cursor.calls[1]
        self.assertIn("DELETE FROM days_off", days_off_sql)
        self.assertNotIn("day_off_date = ANY", days_off_sql)


class DismissalInterruptTests(unittest.TestCase):
    """_interrupt_dismissal_period_by_work_day_tx — смена в день увольнения его не отменяет."""

    def setUp(self):
        self.ns = _load_methods("_interrupt_dismissal_period_by_work_day_tx")
        self.fake = _FakeSelf()

    def _run(self, period_start, work_date, is_blacklist=False):
        cursor = _ScriptedCursor([[(7, period_start, None, is_blacklist)]])
        self.ns["_interrupt_dismissal_period_by_work_day_tx"](
            self.fake, cursor, 321, work_date
        )
        return cursor

    def test_shift_on_the_first_day_keeps_the_dismissal(self):
        """Иначе ручной возврат смены стирал дату и причину увольнения."""
        cursor = self._run(date(2026, 8, 31), date(2026, 8, 31))
        self.assertEqual(1, len(cursor.calls))
        self.assertNotIn("DELETE FROM operator_schedule_status_periods", cursor.calls[0][0])

    def test_shift_after_the_first_day_still_ends_the_dismissal(self):
        """Человек вернулся к работе — увольнение закрывается днём раньше смены."""
        cursor = self._run(date(2026, 8, 20), date(2026, 8, 31))
        update_sql, update_params = cursor.calls[1]
        self.assertIn("UPDATE operator_schedule_status_periods", update_sql)
        self.assertEqual((date(2026, 8, 30), 7), update_params)

    def test_blacklist_dismissal_still_cannot_be_interrupted_later(self):
        with self.assertRaises(ValueError):
            self._run(date(2026, 8, 20), date(2026, 8, 31), is_blacklist=True)

    def test_blacklist_dismissal_allows_its_own_last_worked_day(self):
        """ЧС тоже увольняют в последний рабочий день — смену за него не отбираем."""
        cursor = self._run(date(2026, 8, 31), date(2026, 8, 31), is_blacklist=True)
        self.assertEqual(1, len(cursor.calls))


class StatusPeriodWiringTests(unittest.TestCase):
    """save_schedule_status_period — порядок и связка с детектором отработанных дней."""

    def setUp(self):
        self.source = _method_source("save_schedule_status_period")

    def test_worked_days_are_computed_before_the_delete(self):
        detect = self.source.index("_schedule_days_with_worked_time_tx")
        delete = self.source.index("_delete_shifts_for_period_tx")
        self.assertLess(detect, delete)

    def test_delete_receives_the_worked_days(self):
        self.assertIn("keep_days=worked_days", self.source)

    def test_response_reports_kept_days(self):
        self.assertIn("serialized['keptWorkedDays']", self.source)

    def test_recalculation_still_runs_after_the_delete(self):
        delete = self.source.index("_delete_shifts_for_period_tx")
        recalc = self.source.index("_recalculate_auto_daily_hours_tx")
        self.assertLess(delete, recalc)


if __name__ == "__main__":
    unittest.main()
