# -*- coding: utf-8 -*-
"""Отчёт за день «Воронки найма» (HR-3): чистые правила без базы — канал карточки, строка «из них» на конец дня, этап
на момент касания, день из запроса, подписи. Расчёт целиком на Postgres — tests/test_hiring_day_report_postgres.py."""

import unittest
from datetime import date, datetime

from hiring import day_report, rules


class ChannelTests(unittest.TestCase):
    def test_owner_channels(self):
        # hh и «Таргет» — по источнику, даже внесённые руками (до моста hh рекрутеры заводили отклики hh сами).
        self.assertEqual(day_report.channel_of('hh'), 'hh')
        self.assertEqual(day_report.channel_of('hh', created_by=5), 'hh')
        self.assertEqual(day_report.channel_of('target'), 'target')
        self.assertEqual(day_report.channel_of('target', created_by=5), 'target')
        # «Вручную» — внесённое человеком с любым другим источником: Kaspi, OLX, «Вручную», «Приведи друга».
        for code in ('kaspi', 'olx', 'manual', 'referral', 'whatsapp'):
            self.assertEqual(day_report.channel_of(code, created_by=7), 'manual', code)
        # Само пришедшее: номер WhatsApp найма без рекламы — свой канал, прочие площадки — «Другие».
        self.assertEqual(day_report.channel_of('whatsapp'), 'whatsapp')
        self.assertEqual(day_report.channel_of('kaspi'), 'other')
        self.assertEqual(day_report.channel_of('olx'), 'other')
        self.assertEqual(day_report.channel_of(None), 'other')
        # Загруженная база — по партии, хотя её грузит человек.
        self.assertEqual(day_report.channel_of('base_bought', created_by=3, batch_id=9), 'base')

    def test_always_shown_and_labels(self):
        self.assertEqual(day_report.ALWAYS_SHOWN, ('hh', 'target', 'whatsapp', 'manual'))
        self.assertEqual(set(day_report.CHANNEL_LABELS), set(day_report.CHANNELS))


class BucketTests(unittest.TestCase):
    def test_end_of_day_bucket(self):
        bucket = day_report.end_of_day_bucket
        self.assertEqual(bucket('invited_office', 'expected'), 'office')
        self.assertEqual(bucket('invited_office', 'came'), 'office')
        for stage in ('invited_training', 'on_training', 'on_line', 'hired', 'hired_10', 'dropped'):
            self.assertEqual(bucket(stage), 'training', stage)
        self.assertEqual(bucket('refused', 'ours'), 'refused_ours')
        self.assertEqual(bucket('refused', 'candidate'), 'refused_candidate')
        self.assertEqual(bucket('refused', None), 'refused_ours')
        self.assertEqual(bucket('no_response'), 'no_answer')
        self.assertEqual(bucket('in_work', None, 'no_answer', False), 'no_answer')
        self.assertEqual(bucket('in_work', None, 'no_answer', True), 'in_work')     # написал в WhatsApp после звонка
        self.assertEqual(bucket('in_work', None, 'dial'), 'in_work')
        self.assertEqual(bucket('in_work'), 'in_work')                             # не звонили — на связи в WhatsApp
        for stage in ('responses', 'contacts', None):
            self.assertIsNone(bucket(stage), stage)                                 # вернулся в пул — в отчёте нет

    def test_rows_and_parts(self):
        parts = [key for key, _label, part, _hint in day_report.ROWS if part]
        self.assertEqual(tuple(parts), day_report.BUCKETS)
        self.assertEqual(day_report.ROW_KEYS[:2], ('arrived', 'done'))
        self.assertEqual(day_report.ROW_KEYS[-1], 'interviews')
        keys = {key for key, _label, _hint in day_report.RESULT_COLUMNS}
        self.assertEqual(keys, {'done'} | set(day_report.BUCKETS))
        self.assertEqual([key for key, _l, _h in day_report.EFFICIENCY_COLUMNS],
                         ['calls', 'talked', 'avg_talk_sec', 'talk_sec', 'whatsapp'])
        # Подсказка «В работе» отвечает на вопрос владельца, когда карточка получает этот статус.
        hint = dict((key, hint) for key, _label, _part, hint in day_report.ROWS)['in_work']
        self.assertIn('Взять в работу', hint)


class StageAtTests(unittest.TestCase):
    def test_stage_at_moment(self):
        history = [(datetime(2026, 11, 20, 9, 0), 'responses', None), (datetime(2026, 11, 20, 9, 5), 'in_work', None),
                   (datetime(2026, 11, 21, 10, 0), 'invited_office', 'expected')]
        self.assertIsNone(day_report.stage_at(history, datetime(2026, 11, 20, 8, 59)))
        self.assertEqual(day_report.stage_at(history, datetime(2026, 11, 20, 9, 5))[1], 'in_work')   # в ту же секунду
        self.assertEqual(day_report.stage_at(history, datetime(2026, 11, 21, 9, 59))[1], 'in_work')
        self.assertEqual(day_report.stage_at(history, datetime(2026, 11, 22))[1], 'invited_office')
        self.assertIsNone(day_report.stage_at([], datetime(2026, 11, 22)))


class DayTests(unittest.TestCase):
    def test_parse_day(self):
        today = date(2026, 11, 23)
        self.assertEqual(day_report.parse_day('2026-11-20', today), date(2026, 11, 20))
        self.assertEqual(day_report.parse_day('2026-11-20T10:00', today), date(2026, 11, 20))
        self.assertEqual(day_report.parse_day('2026-12-01', today), today)           # отчёта за будущее нет
        for value in (None, '', 'вчера', '2026-13-40'):
            self.assertEqual(day_report.parse_day(value, today), today, value)

    def test_titles_and_clock(self):
        self.assertEqual(day_report.day_title(date(2026, 11, 23)), '23 ноября 2026, понедельник')
        self.assertEqual(day_report.day_title(date(2026, 3, 1)), '1 марта 2026, воскресенье')
        self.assertIsNone(day_report.talk_clock(None))
        self.assertEqual(day_report.talk_clock(0), '0:00')
        self.assertEqual(day_report.talk_clock(89), '1:29')
        self.assertEqual(day_report.talk_clock(89.6), '1:30')
        self.assertEqual(day_report.talk_clock(3725), '1:02:05')

    def test_note_limit_matches_rules_style(self):
        self.assertEqual(day_report.NOTE_MAX, 2000)
        self.assertEqual(rules.STAGE_LABELS['in_work'], 'В работе')


if __name__ == '__main__':
    unittest.main()
