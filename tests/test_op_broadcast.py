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
from pathlib import Path

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DB_PATH = ROOT / "database.py"

HELPERS = {"_op_broadcast_deviations", "_op_broadcast_percent", "_op_broadcast_lines",
           "_op_broadcast_text", "_op_broadcast_duration"}


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
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace


def snapshot(arrived=100, missed=4, sl=0.9, live_age=30, queues=None, online=5, talking=2,
             on_break=1):
    answered = arrived - missed
    return {
        'stamp': '15.09 18:00',
        'ar_min_percent': 3, 'ar_max_percent': 5,
        'totals': {'arrived': arrived, 'answered': answered, 'missed': missed,
                   'ar': (missed / arrived) if arrived else None, 'sl': sl,
                   'avg_talk_seconds': 65, 'outgoing': 12},
        'now': {'operators_online': online, 'operators_talking': talking,
                'operators_on_break': on_break},
        'queues': queues or [],
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

    def test_text_has_header_totals_and_lines(self):
        text = self.ns["_op_broadcast_text"](snapshot(queues=[
            {'queue': '3000', 'arrived': 60, 'answered': 58, 'missed': 2, 'ar': 2 / 60, 'sl': 0.9, 'outgoing': 0},
            {'queue': '3002', 'arrived': 40, 'answered': 38, 'missed': 2, 'ar': 0.05, 'sl': 0.85, 'outgoing': 0},
        ]))
        self.assertTrue(text.startswith('<b>Табло ОП</b> (15.09 18:00):'))
        self.assertIn('Входящих 100 · принято 96 · потеряно 4', text)
        self.assertIn('• Линия 3000:', text)
        self.assertIn('• Линия 3002:', text)

    def test_single_line_is_not_repeated_under_totals(self):
        text = self.ns["_op_broadcast_text"](snapshot(queues=[
            {'queue': '3000', 'arrived': 100, 'answered': 96, 'missed': 4, 'ar': 0.04, 'sl': 0.9, 'outgoing': 0}]))
        self.assertNotIn('• Линия', text)


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
