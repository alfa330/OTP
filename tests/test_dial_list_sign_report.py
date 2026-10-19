# -*- coding: utf-8 -*-
"""Отчёт «Подписание водителей по городам» в Telegram-группы (владелец, 19.10.2026).

Без базы и сети: строки отчёта и их порядок, «подписано» как на картинке владельца (подписал +
на проверке у Яндекса), слоты 10:00 / 19:00, вид картинки (размер, цвета долей, шапка), ручки
раздела и джоба планировщика. Настоящий SQL, выбор групп и «один отчёт на слот» — в
tests/test_dial_list_sign_report_postgres.py.
"""
import io
import os
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import routes as dial_routes  # noqa: E402
from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402
from dial_list import sign_report, signing  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
ALMATY = timezone(timedelta(hours=5))


def _at(day, hh, mm=0, ss=0):
    return datetime(2026, 10, day, hh, mm, ss, tzinfo=ALMATY)


class BuildTest(unittest.TestCase):
    def test_rows_shares_and_order(self):
        report = sign_report.build([
            ('Тараз', 908, 402),
            ('Костанай', 967, 631),
            ('', 5, 5),                       # без города — в конце, хотя доля 100 %
            ('Жанаозен', 1, 0),
            ('Экибастуз', 11, 6),
        ])
        cities = report['cities']
        self.assertEqual([c['city'] for c in cities],
                         ['Костанай', 'Экибастуз', 'Тараз', 'Жанаозен', sign_report.UNKNOWN_CITY])
        kostanay = cities[0]
        self.assertEqual((kostanay['total'], kostanay['signed'], kostanay['unsigned']), (967, 631, 336))
        self.assertEqual(sign_report.fmt_pct(kostanay['signed_pct']), '65.3%')
        self.assertEqual(sign_report.fmt_pct(kostanay['unsigned_pct']), '34.7%')
        self.assertFalse(cities[-1]['known'])
        self.assertEqual(report['totals'], {
            'total': 1892, 'signed': 1044, 'unsigned': 848,
            'signed_pct': 100.0 * 1044 / 1892, 'unsigned_pct': 100.0 * 848 / 1892,
        })

    def test_same_share_bigger_city_first_then_name(self):
        cities = sign_report.build([('Б', 10, 5), ('А', 10, 5), ('В', 20, 10)])['cities']
        self.assertEqual([c['city'] for c in cities], ['В', 'А', 'Б'])

    def test_city_spelling_spaces_merge_and_empty_rows_drop(self):
        cities = sign_report.build([('Усть-Каменогорск', 3, 1), (' Усть-Каменогорск  ', 2, 2), ('Ноль', 0, 0)])
        self.assertEqual([(c['city'], c['total'], c['signed']) for c in cities['cities']],
                         [('Усть-Каменогорск', 5, 3)])

    def test_empty_base(self):
        report = sign_report.build([])
        self.assertEqual(report['cities'], [])
        self.assertEqual(report['totals']['total'], 0)
        self.assertEqual(report['totals']['signed_pct'], 0.0)

    def test_owner_picture_numbers(self):
        """Колонка 19.10 файла владельца: «Подписано» 11 473 = Подписан + на подписании у Яндекса +
        Подписаны; на картинке — 48.3 % и 51.7 %."""
        report = sign_report.build([('Алматы', 7948, 4003), ('Шымкент', 2617, 929), ('Прочие', 13206, 6541)])
        totals = report['totals']
        self.assertEqual((totals['total'], totals['signed'], totals['unsigned']), (23771, 11473, 12298))
        self.assertEqual(sign_report.fmt_pct(totals['signed_pct']), '48.3%')
        self.assertEqual(sign_report.fmt_pct(totals['unsigned_pct']), '51.7%')
        self.assertEqual(sign_report.fmt_int(totals['total']), '23 771')


class SignedMeaningTest(unittest.TestCase):
    def test_sql_counts_processing_as_signed_and_skips_test_numbers(self):
        sql = sign_report._BY_CITY_SQL
        self.assertIn('l.signed_at IS NOT NULL OR l.sign_status = %(processing)s', sql)
        self.assertIn(sign_report.test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL, sql)
        self.assertIn('GROUP BY l.city', sql)

    def test_collect_passes_processing_status(self):
        seen = {}

        class Cur:
            def execute(self, sql, params):
                seen.update(params)

            def fetchall(self):
                return [('Астана', 3, 2), (None, 1, 0)]

        rows = sign_report.collect(Cur(), '1954', '2026-10-01')
        self.assertEqual(seen['processing'], signing.PROCESSING)
        self.assertEqual(seen['department_id'], 1954)
        self.assertEqual(rows, [('Астана', 3, 2), ('', 1, 0)])


class SlotTest(unittest.TestCase):
    def test_slots(self):
        cases = {
            _at(19, 9, 59): None,                      # вчерашние 19:00 — давно
            _at(19, 10, 0): _at(19, 10, 0),
            _at(19, 10, 59, 59): _at(19, 10, 0),
            _at(19, 11, 0, 1): None,                   # опоздание больше часа — не шлём
            _at(19, 19, 0): _at(19, 19, 0),
            _at(19, 19, 40): _at(19, 19, 0),
            _at(19, 23, 30): None,
            _at(20, 0, 30): None,                      # после полуночи — не вчерашние 19:00
        }
        for now, expected in cases.items():
            with self.subTest(now=now.isoformat()):
                self.assertEqual(sign_report.slot_for(now), expected)

    def test_slot_from_utc_moment(self):
        # 05:00 UTC = 10:00 по Алматы.
        self.assertEqual(sign_report.slot_for(datetime(2026, 10, 19, 5, 0, tzinfo=timezone.utc)), _at(19, 10, 0))

    def test_hours_are_owner_ones(self):
        self.assertEqual(sign_report.SLOT_HOURS, (10, 19))


class RenderTest(unittest.TestCase):
    def _report(self, n=23):
        rows = [('Город %02d' % i, 100 + i, (100 + i) * (n - i) // n) for i in range(n)]
        rows.append(('Жанаозен', 1, 0))
        return sign_report.build(rows)

    def test_png_size_and_colors(self):
        from PIL import Image

        report = self._report()
        png = sign_report.render_png(report, _at(19, 10, 0))
        self.assertTrue(png.startswith(b'\x89PNG'))
        image = Image.open(io.BytesIO(png)).convert('RGB')
        scale = sign_report.SCALE
        self.assertEqual(image.size, (sign_report.WIDTH * scale, sign_report.height_for(report) * scale))
        # Шапка таблицы — тёмно-синяя полоса под плашками.
        head_y = (sign_report.TOP + sign_report.CARD_H + 20 + sign_report.HEAD_H // 2) * scale
        self.assertEqual(image.getpixel((sign_report.PAD * scale + 60, head_y)), sign_report.NAVY)
        # Первая строка — серая «зебра», вторая — белая.
        first_mid = int((sign_report.table_top() + 4) * scale)
        self.assertEqual(image.getpixel((sign_report.PAD * scale + 4, first_mid)), sign_report.ZEBRA)
        second_mid = int((sign_report.table_top() + sign_report.ROW_H + 4) * scale)
        self.assertEqual(image.getpixel((sign_report.PAD * scale + 4, second_mid)), sign_report.WHITE)
        # У первой строки (доля 100 %) полоса залита зелёным, у Жанаозена (0 %) — пустая дорожка.
        bar_mid = lambda row: int((sign_report.table_top() + row * sign_report.ROW_H + sign_report.ROW_H / 2) * scale)  # noqa: E731
        self.assertEqual(image.getpixel(((sign_report.BAR_X + 20) * scale, bar_mid(0))), sign_report.GREEN)
        last = len(report['cities']) - 1
        self.assertEqual(image.getpixel(((sign_report.BAR_X + 20) * scale, bar_mid(last))), sign_report.TRACK)

    def test_share_colors(self):
        self.assertEqual(sign_report.share_color(65.3), sign_report.GREEN)
        self.assertEqual(sign_report.share_color(40.0), sign_report.GREEN)
        self.assertEqual(sign_report.share_color(39.7), sign_report.AMBER)
        self.assertEqual(sign_report.share_color(20.0), sign_report.AMBER)
        self.assertEqual(sign_report.share_color(0.0), sign_report.RED)

    def test_fonts_are_shipped_with_repo(self):
        from crm.card import FONTS_DIR
        for weight in ('Regular', 'Bold'):
            self.assertTrue(os.path.exists(os.path.join(FONTS_DIR, 'Roboto-%s.ttf' % weight)))


class WiringTest(unittest.TestCase):
    def test_schema_has_city_and_chats_table_before_indexes(self):
        ddl = dial_schema.DDL
        city = next(i for i, s in enumerate(ddl) if 'ADD COLUMN IF NOT EXISTS city ' in s)
        table = next(i for i, s in enumerate(ddl) if 'CREATE TABLE IF NOT EXISTS dial_list_sign_report_chats' in s)
        first_index = next(i for i, s in enumerate(ddl) if s.lstrip().startswith('CREATE INDEX')
                           or s.lstrip().startswith('CREATE UNIQUE INDEX'))
        self.assertLess(city, first_index)
        self.assertLess(table, first_index)
        self.assertIn('PRIMARY KEY (department_id, chat_id)', ddl[table])

    def test_routes_and_scheduler(self):
        routes = (ROOT / 'dial_list' / 'routes.py').read_text(encoding='utf-8')
        self.assertIn("'/api/dial_list/departments/<int:department_id>/sign_report/chats'", routes)
        self.assertIn("'/api/dial_list/departments/<int:department_id>/sign_report/preview'", routes)
        bot = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        start = bot.index('scheduler.add_job(\n        dial_list_sign_report_job,')
        block = bot[start:bot.index(')\n', bot.index("id='dial_list_sign_report'", start))]
        self.assertIn("CronTrigger(hour='10,19', minute=0, timezone=ZoneInfo('Asia/Almaty'))", block)
        self.assertIn('next_run_time=', block)
        self.assertIn("_dial_list_service.request_sign_report('планировщик')", bot)

    def test_report_is_not_for_tez(self):
        svc = dial_service.DialListService.__new__(dial_service.DialListService)
        svc.department_settings = lambda department_id: {'profile': 'tez', '_period': None}
        with self.assertRaises(dial_service.DialListError) as caught:
            svc.sign_report_chats(560)
        self.assertEqual(caught.exception.status, 400)

    def test_request_runs_once_at_a_time(self):
        svc = dial_service.DialListService.__new__(dial_service.DialListService)
        import threading
        svc._sign_report_lock = threading.Lock()
        svc._sign_report_running = False
        gate, calls = threading.Event(), []

        def slow(reason=''):
            calls.append(reason)
            gate.wait(5)

        svc.send_sign_reports = slow
        self.assertTrue(svc.request_sign_report('первый'))
        self.assertFalse(svc.request_sign_report('второй'))
        gate.set()
        for _ in range(100):
            if not svc._sign_report_running:
                break
            import time
            time.sleep(0.02)
        self.assertFalse(svc._sign_report_running)
        self.assertEqual(calls, ['первый'])

    def test_send_retries_once_and_returns_error_text(self):
        svc = dial_service.DialListService.__new__(dial_service.DialListService)
        svc.SIGN_REPORT_RETRY_SEC = 0
        answers = [(None, 'Bad Gateway'), ({'message_id': 5}, None)]
        svc._telegram_send = lambda chat_id, png: answers.pop(0)
        self.assertEqual(svc._send_sign_report(-100, b'png'), '')
        answers[:] = [(None, 'Forbidden: bot was kicked'), (None, 'Forbidden: bot was kicked')]
        self.assertEqual(svc._send_sign_report(-100, b'png'), 'Forbidden: bot was kicked')

    def test_preview_route_is_png_for_manager(self):
        from flask import Flask

        class Svc:
            def __init__(self):
                self.calls = []

            def sign_report_png(self, department_id):
                self.calls.append(department_id)
                return b'\x89PNGfake'

            def manager_scope(self, *args, **kwargs):
                return None

        fake = Svc()
        app = Flask(__name__)
        bp = dial_routes.build_dial_list_blueprint(
            db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (2, (2, 'admin', 'Админ', 'admin', None, None, None, 'admin'), None),
            is_admin_role=lambda role: role == 'admin', headed_department_ids=lambda user_id: [],
            service=fake)
        app.register_blueprint(bp)
        response = app.test_client().get('/api/dial_list/departments/1954/sign_report/preview')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/png')
        self.assertEqual(response.headers.get('Cache-Control'), 'no-store')
        self.assertEqual(fake.calls, [1954])


if __name__ == '__main__':
    unittest.main()
