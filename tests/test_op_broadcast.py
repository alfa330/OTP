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
import asyncio
import re
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DB_PATH = ROOT / "database.py"

HELPERS = {"_op_broadcast_deviations", "_op_broadcast_percent",
           "_op_broadcast_text", "_op_broadcast_duration", "_op_broadcast_table",
           "_op_broadcast_attach_period", "_op_broadcast_caption"}
CONSTANTS = {"_OP_BROADCAST_TABLE_ROWS"}


def _module():
    return source_cache.parse(BOT_PATH.read_text(encoding="utf-8-sig"))


def _namespace(min_calls=20, sl_min=80.0, stale=600):
    functions = [node for node in _module().body
                 if isinstance(node, ast.FunctionDef) and node.name in HELPERS]
    assert len(functions) == len(HELPERS)
    constants = [node for node in _module().body
                 if isinstance(node, ast.Assign)
                 and any(getattr(target, 'id', None) in CONSTANTS for target in node.targets)]
    assert len(constants) == len(CONSTANTS)
    namespace = {
        "OP_BROADCAST_MIN_CALLS": min_calls,
        "OP_BROADCAST_SL_MIN_PERCENT": sl_min,
        "OP_BROADCAST_LIVE_STALE_SECONDS": stale,
        "_szov_wallboard_int": lambda v: int(v or 0),
        "timedelta": timedelta,
    }
    exec(compile(ast.Module(body=constants + functions, type_ignores=[]), str(BOT_PATH), "exec"),
         namespace)
    return namespace


def table(text):
    """Таблица из <pre> отбивки: (заголовки столбцов, {показатель: [значения]}, строки как есть).
    Столбцы разделены двумя и более пробелами — внутри «4,0 %» пробел один."""
    block = text[text.index('<pre>') + len('<pre>'):text.index('</pre>')].strip('\r\n').splitlines()
    header = re.split(r'\s{2,}', block[0].strip())
    rows = {}
    for line in block[1:]:
        label, *values = re.split(r'\s{2,}', line.strip())
        rows[label] = values
    return header, rows, block


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
        self.assertEqual(table(text)[1]['SL'], ['—'])

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
        header, rows, _ = table(text)
        self.assertEqual(header, ['День'])
        self.assertEqual((rows['Входящих'], rows['Принято'], rows['Потеряно']), (['100'], ['96'], ['4']))
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

    def test_text_is_a_table_of_the_day_and_the_hour(self):
        """Владелец 17.09.2026: текст — таблицей, как в отбивке по лидам."""
        out = self.attach(self.today(), datetime(2026, 9, 17, 10, 0), self.day_parts)
        text = self.ns["_op_broadcast_text"](out)
        header, rows, block = table(text)
        self.assertEqual(header, ['День', '09–10'])
        self.assertEqual(list(rows), ['Входящих', 'Принято', 'Потеряно', 'AR', 'SL', 'Разговор',
                                      'Исходящих'])
        self.assertEqual((rows['Входящих'], rows['Принято'], rows['Потеряно']),
                         (['100', '30'], ['96', '27'], ['4', '3']))
        self.assertEqual((rows['AR'], rows['SL']), (['4,0 %', '10,0 %'], ['90,0 %', '70,0 %']))
        self.assertEqual((rows['Разговор'], rows['Исходящих']), (['1:05', '1:00'], ['12', '0']))
        # Столбцы выровнены по правому краю, строка помещается в телефон.
        self.assertEqual(len({len(line) for line in block}), 1)
        self.assertLessEqual(max(len(line) for line in block), 32)

    def test_table_goes_first_then_deviations_then_people(self):
        data = self.today()
        data['totals'] = dict(data['totals'], sl=0.7)
        text = self.ns["_op_broadcast_text"](self.attach(data, datetime(2026, 9, 17, 10, 0), self.day_parts))
        self.assertTrue(text.startswith('<b>Табло ОП</b> (15.09 18:00):\n\n<pre>'))
        self.assertLess(text.index('</pre>'), text.index('Обратите внимание: SL 70,0 %'))
        self.assertLess(text.index('Обратите внимание'), text.index('Онлайн 5 · в разговоре 2'))

    def test_midnight_table_is_titled_by_the_day_that_ended(self):
        out = self.attach(self.today('2026-09-17'), datetime(2026, 9, 17, 0, 0, 3), self.day_parts)
        header, rows, _ = table(self.ns["_op_broadcast_text"](out))
        self.assertEqual(header, ['16.09', '23–24'])
        self.assertEqual(rows['Входящих'], ['400', '12'])

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
        rows = table(self.ns["_op_broadcast_text"](out))[1]
        self.assertEqual((rows['Входящих'], rows['AR'], rows['SL'], rows['Разговор']),
                         (['100', '0'], ['4,0 %', '—'], ['90,0 %', '—'], ['1:05', '—']))


class CaptionTests(unittest.TestCase):
    """Владелец 17.09.2026: в отбивке ОП — только картинки и «Обратите внимание», если есть.
    Заголовок, таблица и строка о людях из подписи убраны: цифры уже на картинках."""

    def setUp(self):
        self.ns = _namespace()
        self.caption = self.ns["_op_broadcast_caption"]

    def test_no_deviations_means_no_caption(self):
        self.assertEqual(self.caption(snapshot()), '')

    def test_caption_is_only_the_deviations(self):
        data = snapshot(arrived=100, missed=9, sl=0.7)
        self.assertEqual(self.caption(data).split('\n'), self.ns["_op_broadcast_deviations"](data))
        for noise in ('Табло ОП', '<pre>', 'Онлайн', 'Входящих'):
            self.assertNotIn(noise, self.caption(data))


class _FakeInputFile:
    def __init__(self, file, filename=None):
        self.filename = filename


class _FakeMediaGroup:
    def __init__(self):
        self.photos = []

    def attach_photo(self, photo, caption=None, parse_mode=None):
        self.photos.append((photo.filename, caption, parse_mode))


class _FakeBot:
    def __init__(self, fail_media=False):
        self.calls = []
        self.fail_media = fail_media

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None):
        if self.fail_media:
            raise RuntimeError('photo rejected')
        self.calls.append(('photo', photo.filename, caption, parse_mode))

    async def send_media_group(self, chat_id, group):
        if self.fail_media:
            raise RuntimeError('album rejected')
        self.calls.append(('album', group.photos))

    async def send_message(self, chat_id, text, parse_mode=None):
        self.calls.append(('message', text, parse_mode))


class DeliveryTests(unittest.TestCase):
    """Общая доставка отбивок: пустая подпись — картинки без подписи, а не ошибка Telegram."""

    def deliver(self, bot, text, media):
        node = next(n for n in _module().body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "_szov_broadcast_deliver")
        types = type('types', (), {'InputFile': _FakeInputFile, 'MediaGroup': _FakeMediaGroup})
        namespace = {"bot": bot, "types": types, "BytesIO": lambda blob: blob,
                     "logging": type('log', (), {'error': staticmethod(lambda *a, **k: None)})}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(BOT_PATH), "exec"), namespace)
        return asyncio.run(namespace["_szov_broadcast_deliver"](-1, text, media))

    def test_album_without_caption(self):
        bot = _FakeBot()
        self.deliver(bot, '', [('op_board.png', b'1'), ('op_hour.png', b'2')])
        self.assertEqual(bot.calls, [('album', [('op_board.png', None, None), ('op_hour.png', None, None)])])

    def test_album_with_deviations_caption_on_the_first_picture(self):
        bot = _FakeBot()
        self.deliver(bot, 'Обратите внимание: SL 70,0 %', [('op_board.png', b'1'), ('op_hour.png', b'2')])
        self.assertEqual(bot.calls, [('album', [('op_board.png', 'Обратите внимание: SL 70,0 %', 'HTML'),
                                                ('op_hour.png', None, None)])])

    def test_failed_pictures_without_text_do_not_send_an_empty_message(self):
        bot = _FakeBot(fail_media=True)
        with self.assertRaises(RuntimeError):
            self.deliver(bot, '', [('op_board.png', b'1'), ('op_hour.png', b'2')])
        self.assertEqual(bot.calls, [])

    def test_failed_pictures_with_text_still_send_the_text(self):
        # Так живут «Линия» и «Чат»: их текст никогда не пустой, и поведение не поменялось.
        bot = _FakeBot(fail_media=True)
        self.deliver(bot, '<b>Табло</b>', [('board.png', b'1')])
        self.assertEqual(bot.calls, [('message', '<b>Табло</b>', 'HTML')])


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
        # Подпись — только отклонения; полный текст — лишь когда картинки не собрались.
        self.assertIn("text = _op_broadcast_caption(data) if media else _op_broadcast_text(data)", prepare)
        self.assertLess(prepare.index("_op_render_hour_png"), prepare.index("text = "))
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
