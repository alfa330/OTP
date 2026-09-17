# -*- coding: utf-8 -*-
"""Отбивка «Табло ОП» — третье направление отбивки табло.

Проводка (по исходнику монолита, как в ChatBroadcastWiringTests):
  * направление 'op' есть в SZOV_BROADCAST_DIRECTIONS — иначе ручки настройки ответят 400;
  * джоба op_broadcast_job стоит в планировщике по своему расписанию;
  * предпросмотр и тестовая отправка ветвятся на ОП;
  * гейт настройки смотрит на главу отдела ПРОДАЖ, когда направление — ОП;
  * ограничение направлений в БД знает 'op' и умеет снять старое из двух значений.

Правило отклонений — исполняемое: функции вырезаются из монолита и гоняются на
снимках. Норма та же, что красит плитки; проценты считаются только при выборке
не меньше OP_BROADCAST_MIN_CALLS.
"""

import ast
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DB_PATH = ROOT / "database.py"

HELPERS = {"_op_broadcast_deviations", "_op_broadcast_percent",
           "_op_broadcast_text", "_op_broadcast_duration", "_op_broadcast_totals_line",
           "_op_broadcast_attach_period"}


def _module():
    return source_cache.parse(BOT_PATH.read_text(encoding="utf-8-sig"))


def _namespace(min_calls=20, sl_min=80.0, stale=600):
    functions = [node for node in _module().body
                 if isinstance(node, ast.FunctionDef) and node.name in HELPERS]
    assert len(functions) == len(HELPERS)
    namespace = {
        "OP_BROADCAST_MIN_CALLS": min_calls,
        "OP_BROADCAST_SL_MIN_PERCENT": sl_min,
        "OP_BROADCAST_LIVE_STALE_SECONDS": stale,
        "_szov_wallboard_int": lambda v: int(v or 0),
        "timedelta": timedelta,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace


def snapshot(arrived=100, missed=4, sl=0.9, live_age=30, online=5, talking=2, on_break=1):
    answered = arrived - missed
    return {
        'stamp': '15.09 18:00',
        'ar_min_percent': 3, 'ar_max_percent': 5,
        'totals': {'arrived': arrived, 'answered': answered, 'missed': missed,
                   'ar': (missed / arrived) if arrived else None, 'sl': sl,
                   'avg_talk_seconds': 65, 'outgoing': 12},
        'now': {'operators_online': online, 'operators_talking': talking,
                'operators_on_break': on_break},
        'bridge': {'live_age_seconds': live_age, 'connected': True},
    }


class DeviationTests(unittest.TestCase):
    def setUp(self):
        self.ns = _namespace()
        self.deviations = self.ns["_op_broadcast_deviations"]

    def test_all_in_norm_is_silent(self):
        self.assertEqual(self.deviations(snapshot()), [])

    def test_ar_above_corridor(self):
        notes = self.deviations(snapshot(arrived=100, missed=9))
        self.assertEqual(len(notes), 1)
        self.assertIn('9,0 %', notes[0])
        self.assertIn('до 5 %', notes[0])

    def test_ar_below_corridor_is_also_a_deviation(self):
        notes = self.deviations(snapshot(arrived=100, missed=1))
        self.assertEqual(len(notes), 1)
        self.assertIn('ниже коридора', notes[0])

    def test_sl_below_threshold(self):
        notes = self.deviations(snapshot(sl=0.7))
        self.assertEqual(len(notes), 1)
        self.assertIn('SL 70,0 %', notes[0])

    def test_unknown_sl_is_not_a_deviation(self):
        # Станция не отдаёт момент ответа → снимок отдаёт SL как None; писать в чат
        # «SL 0 % при норме 80 %» на этом было бы ложной тревогой в каждой отбивке.
        self.assertEqual(self.deviations(snapshot(sl=None)), [])
        text = self.ns["_op_broadcast_text"](snapshot(sl=None))
        self.assertIn('SL —', text)

    def test_small_sample_does_not_wake_anyone(self):
        # Утро: 7 входящих, 1 потерян — 14 % AR, но это не показатель.
        self.assertEqual(self.deviations(snapshot(arrived=7, missed=1, sl=0.5)), [])

    def test_silent_bridge_is_a_deviation(self):
        notes = self.deviations(snapshot(live_age=1200))
        self.assertEqual(len(notes), 1)
        self.assertIn('молчит 20 мин', notes[0])

    def test_bridge_without_live_data_is_a_deviation(self):
        notes = self.deviations(snapshot(live_age=None))
        self.assertEqual(len(notes), 1)
        self.assertIn('не присылал живых данных', notes[0])

    def test_text_has_header_totals_and_people_and_no_per_line_rows(self):
        """Разрез по линиям снят целиком (владелец, 16.09.2026): ни на экране, ни в тексте."""
        text = self.ns["_op_broadcast_text"](snapshot())
        self.assertTrue(text.startswith('<b>Табло ОП</b> (15.09 18:00):'))
        self.assertIn('За день: входящих 100 · принято 96 · потеряно 4', text)
        self.assertIn('Онлайн 5 · в разговоре 2 · на перерыве 1', text)
        self.assertNotIn('Линия', text)
        self.assertNotIn('def _op_broadcast_lines', BOT_PATH.read_text(encoding="utf-8-sig"))


def hourly(day_hours):
    """Разрез по часам снимка: {час: (входящих, потеряно, SL)}, остальные часы пустые."""
    rows = []
    for hour in range(24):
        arrived, missed, sl = day_hours.get(hour, (0, 0, None))
        rows.append({'hour': hour, 'arrived': arrived, 'answered': arrived - missed, 'missed': missed,
                     'ar': (missed / arrived) if arrived else None, 'sl': sl,
                     'avg_talk_seconds': 60 if arrived else None, 'outgoing': 0})
    return rows


class PeriodTests(unittest.TestCase):
    """Владелец 17.09.2026: рядом с итогами дня — показатели последнего полного часа N−1…N."""

    def setUp(self):
        self.ns = _namespace()
        self.attach = self.ns["_op_broadcast_attach_period"]
        self.asked = []

    def day_parts(self, day):
        self.asked.append(day)
        return {'day': day.isoformat(),
                'totals': {'arrived': 400, 'answered': 380, 'missed': 20, 'ar': 0.05, 'sl': 0.81},
                'hourly': hourly({23: (12, 1, 0.75)})}

    def today(self, day='2026-09-17'):
        data = snapshot()
        data.update(day=day, hourly=hourly({9: (30, 3, 0.7), 10: (8, 0, 1.0)}))
        return data

    def test_scheduled_send_reports_the_hour_that_just_ended(self):
        out = self.attach(self.today(), datetime(2026, 9, 17, 10, 0, 1), self.day_parts)
        self.assertEqual((out['hour_label'], out['day_label'], out['day_closed']),
                         ('09:00–10:00', 'За день', False))
        self.assertEqual(out['hour_totals']['arrived'], 30)
        self.assertEqual(out['totals']['arrived'], 100)          # итоги дня — из снимка как есть
        self.assertEqual(self.asked, [])

    def test_manual_send_mid_hour_reports_the_last_full_hour(self):
        out = self.attach(self.today(), datetime(2026, 9, 17, 10, 37), self.day_parts)
        self.assertEqual(out['hour_label'], '09:00–10:00')
        self.assertEqual(out['hour_totals']['missed'], 3)

    def test_midnight_reports_the_day_that_ended(self):
        # В 00:00 снимок уже живёт новыми сутками: итог дня и час 23–24 берутся расчётом 16.09.
        out = self.attach(self.today('2026-09-17'), datetime(2026, 9, 17, 0, 0, 3), self.day_parts)
        self.assertEqual(self.asked, [date(2026, 9, 16)])
        self.assertEqual((out['day'], out['day_label'], out['hour_label'], out['hour_day']),
                         ('2026-09-16', 'За 16.09', '23:00–24:00', '16.09'))
        self.assertEqual((out['totals']['arrived'], out['hour_totals']['arrived']), (400, 12))
        self.assertEqual(out['now'], snapshot()['now'])          # люди — «на сейчас», из снимка

    def test_midnight_with_the_snapshot_still_on_the_old_day_needs_no_recount(self):
        out = self.attach(self.today('2026-09-16'), datetime(2026, 9, 17, 0, 0, 3), self.day_parts)
        self.assertEqual(self.asked, [])
        self.assertTrue(out['day_closed'])
        self.assertEqual(out['day_label'], 'За 16.09')

    def test_text_has_a_line_for_the_day_and_for_the_hour(self):
        out = self.attach(self.today(), datetime(2026, 9, 17, 10, 0), self.day_parts)
        text = self.ns["_op_broadcast_text"](out)
        self.assertIn('За день: входящих 100 · принято 96 · потеряно 4 · AR 4,0 % · SL 90,0 %', text)
        self.assertIn('За 09:00–10:00: входящих 30 · принято 27 · потеряно 3 · AR 10,0 % · SL 70,0 %', text)
        self.assertLess(text.index('За день'), text.index('За 09:00–10:00'))

    def test_hour_is_not_a_deviation_on_its_own(self):
        # Отклонения — по итогам дня, как и раньше: плохой час в хороший день режим
        # «только при отклонениях» не будит.
        data = self.today()
        data['hourly'] = hourly({9: (30, 12, 0.3)})
        out = self.attach(data, datetime(2026, 9, 17, 10, 0), self.day_parts)
        self.assertEqual(self.ns["_op_broadcast_deviations"](out), [])

    def test_snapshot_saved_before_hourly_metrics_gives_dashes_not_a_crash(self):
        data = self.today()
        data['hourly'] = [{'hour': h, 'arrived': 0, 'answered': 0, 'missed': 0, 'outgoing': 0}
                          for h in range(24)]
        out = self.attach(data, datetime(2026, 9, 17, 10, 0), self.day_parts)
        self.assertIn('За 09:00–10:00: входящих 0 · принято 0 · потеряно 0 · AR — · SL —',
                      self.ns["_op_broadcast_text"](out))


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.source = BOT_PATH.read_text(encoding="utf-8-sig")

    def test_direction_is_registered(self):
        self.assertIn("SZOV_BROADCAST_DIRECTION_OP = 'op'", self.source)
        self.assertIn("SZOV_BROADCAST_DIRECTIONS = (SZOV_BROADCAST_DIRECTION_LINE, "
                      "SZOV_BROADCAST_DIRECTION_CHAT,\n                             "
                      "SZOV_BROADCAST_DIRECTION_OP)", self.source)

    def test_job_is_scheduled_on_its_own_times(self):
        self.assertIn("for _hour, _minute in _op_broadcast_send_times():", self.source)
        self.assertIn("id=f'op_wallboard_broadcast_{_hour:02d}{_minute:02d}'", self.source)

    def test_preview_and_test_send_branch_on_op(self):
        self.assertIn("if direction == SZOV_BROADCAST_DIRECTION_OP:\n        return _op_broadcast_preview()",
                      self.source)
        self.assertIn("SZOV_BROADCAST_DIRECTION_OP: _op_broadcast_send,", self.source)

    def test_guard_looks_at_sales_head_for_op(self):
        guard = self.source[self.source.index("def _szov_broadcast_guard"):]
        guard = guard[:guard.index("\n\n\n")]
        self.assertIn("SZOV_BROADCAST_DIRECTION_OP", guard)
        self.assertIn("_op_wallboard_department_id", guard)

    def test_snapshot_is_shared_with_the_wallboard(self):
        self.assertIn("_op_wallboard_snapshot = _op_wallboard_bp.snapshot", self.source)
        collect = self.source[self.source.index("def _op_broadcast_collect"):]
        self.assertIn("globals().get('_op_wallboard_snapshot')", collect[:600])

    def test_past_day_and_hour_image_are_wired(self):
        self.assertIn("_op_wallboard_day_parts = _op_wallboard_bp.day_parts", self.source)
        collect = self.source[self.source.index("def _op_broadcast_collect"):]
        collect = collect[:collect.index("\n\n\n")]
        self.assertIn("globals().get('_op_wallboard_day_parts')", collect)
        self.assertIn("return _op_broadcast_attach_period(data, now, day_parts)", collect)
        prepare = self.source[self.source.index("async def _op_broadcast_prepare("):]
        prepare = prepare[:prepare.index("\n\n\n")]
        self.assertIn("('op_board.png', _op_render_wallboard_png)", prepare)
        self.assertIn("('op_hour.png', _op_render_hour_png)", prepare)
        preview = self.source[self.source.index("def _op_broadcast_preview"):]
        self.assertIn("'hour': _op_render_hour_png", preview[:1200])


class SchemaTests(unittest.TestCase):
    def test_direction_constraint_knows_op_and_migrates_the_old_one(self):
        source = DB_PATH.read_text(encoding="utf-8-sig")
        self.assertEqual(source.count("CHECK (direction IN ('osnova', 'chat', 'op'))"), 2)
        self.assertNotIn("CHECK (direction IN ('osnova', 'chat'))", source)
        self.assertIn("position('''op''' IN pg_get_constraintdef(oid)) = 0", source)
        self.assertIn("DROP CONSTRAINT szov_wallboard_broadcast_chats_direction", source)

    def test_db_layer_accepts_op_direction(self):
        """Кнопка «Отбивка» на табло ОП отвечала 500: ручка и CHECK знали 'op', а список
        направлений в database.py — нет, и get_szov_broadcast_chats('op') бросал ValueError."""
        source = DB_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("SZOV_BROADCAST_DIRECTIONS = ('osnova', 'chat', 'op')", source)
        self.assertNotIn("SZOV_BROADCAST_DIRECTIONS = ('osnova', 'chat')\n", source)


if __name__ == '__main__':
    unittest.main()
