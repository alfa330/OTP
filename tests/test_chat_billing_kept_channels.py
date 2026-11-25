# -*- coding: utf-8 -*-
"""Номера Chat2Desk, которые биллинг чата берёт и после перехода на Syntony, — по их последний день.

Решение владельца 25.11.2026: «Ноль такси» 10908 — тот же номер, что «Ноль такси» Syntony; с 20.11
каждое сообщение водителя приходит в обе системы, поэтому из Chat2Desk он идёт только за 18–19.11.
Без базы: разбор настройки и форма выборки (_chat_billing_source). Счёт на настоящем Postgres —
tests/test_syntony_chat_billing.py.
"""

import os
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from resource_fte import chat  # noqa: E402

ENV = chat.CHAT_BILLING_C2D_KEEP_CHANNEL_IDS_ENV
CUTOVER = date(2026, 11, 18)
KEPT_ONE_DAY = '(c.channel_id = %s AND c.day <= %s)'


def _source(day_from, day_to, setting=None):
    """Выборка за период; setting=None — настройки в окружении нет (значение по умолчанию)."""
    environ = {key: value for key, value in os.environ.items() if key != ENV}
    if setting is not None:
        environ[ENV] = setting
    with mock.patch.dict(os.environ, environ, clear=True):
        return chat._chat_billing_source(day_from, day_to, 0, 1439, syntony_from=CUTOVER)


class KeptChannelsSettingTests(unittest.TestCase):
    def test_default_keeps_zero_taxi_through_19_november(self):
        self.assertEqual(chat.chat_billing_c2d_keep_channels({}),
                         ((10908, date(2026, 11, 19)),))

    def test_empty_setting_keeps_nothing(self):
        self.assertEqual(chat.chat_billing_c2d_keep_channels({ENV: ''}), ())

    def test_id_without_date_has_no_end_and_date_is_inclusive_last_day(self):
        self.assertEqual(chat.chat_billing_c2d_keep_channels({ENV: '10908:2026-11-19, 777'}),
                         ((10908, date(2026, 11, 19)), (777, None)))

    def test_spaces_around_colon_do_not_turn_the_date_into_no_end(self):
        # «10908 : 2026-11-19» без склейки пробелов читалось бы как «10908» без конца — то есть
        # ровно тот двойной счёт, ради которого дата и заведена.
        self.assertEqual(chat.chat_billing_c2d_keep_channels({ENV: '10908 : 2026-11-19;555'}),
                         ((10908, date(2026, 11, 19)), (555, None)))

    def test_unreadable_date_drops_the_channel_instead_of_keeping_it_forever(self):
        with self.assertLogs(level='WARNING') as logs:
            kept = chat.chat_billing_c2d_keep_channels({ENV: '10908:19.11.2026,555'})
        self.assertEqual(kept, ((555, None),))
        self.assertIn('10908', '\n'.join(logs.output))


class BillingSourceShapeTests(unittest.TestCase):
    def test_after_the_last_kept_day_only_syntony_is_read(self):
        sql, params = _source(date(2026, 11, 20), date(2026, 11, 24))
        self.assertNotIn('c2d_requests', sql)
        self.assertIn('syntony_chat_billing_chats', sql)
        self.assertEqual(params, [date(2026, 11, 20), date(2026, 11, 24)])

    def test_period_through_the_switch_reads_kept_number_until_its_last_day(self):
        sql, params = _source(date(2026, 11, 17), date(2026, 11, 24))
        self.assertIn('c2d_requests', sql)
        self.assertEqual(sql.count(KEPT_ONE_DAY), 1)
        # Chat2Desk — 17…19.11: всё до перехода и 10908 по 19.11; Syntony — 18…24.11.
        self.assertEqual(params, [chat.CHAT_REQUEST_TYPE, date(2026, 11, 17), date(2026, 11, 19),
                                  CUTOVER, 10908, date(2026, 11, 19),
                                  CUTOVER, date(2026, 11, 24)])

    def test_before_the_switch_no_kept_condition(self):
        sql, params = _source(date(2026, 11, 10), date(2026, 11, 17))
        self.assertIn('c2d_requests', sql)
        self.assertNotIn('syntony_chat_billing_chats', sql)
        self.assertNotIn('c.channel_id', sql)
        self.assertEqual(params, [chat.CHAT_REQUEST_TYPE, date(2026, 11, 10), date(2026, 11, 17)])

    def test_open_ended_number_reads_chat2desk_to_the_end_of_the_period(self):
        sql, params = _source(date(2026, 11, 20), date(2026, 11, 24), '10908')
        self.assertIn('c.channel_id = %s)', sql)
        self.assertNotIn('c.day <= %s', sql)
        self.assertEqual(params[:6], [chat.CHAT_REQUEST_TYPE, date(2026, 11, 20),
                                      date(2026, 11, 24), CUTOVER, 10908,
                                      date(2026, 11, 20)])

    def test_number_that_ended_before_the_switch_adds_nothing_after_it(self):
        sql, _params = _source(date(2026, 11, 20), date(2026, 11, 24), '10908:2026-11-17')
        self.assertNotIn('c2d_requests', sql)

    def test_mixed_numbers_read_chat2desk_until_the_latest_end(self):
        sql, params = _source(date(2026, 11, 18), date(2026, 11, 30),
                              '10908:2026-11-19,555:2026-11-22')
        self.assertEqual(params[1:3], [date(2026, 11, 18), date(2026, 11, 22)])
        self.assertEqual(sql.count(KEPT_ONE_DAY), 2)

    def test_empty_setting_reads_chat2desk_only_before_the_switch(self):
        sql, params = _source(date(2026, 11, 17), date(2026, 11, 24), '')
        self.assertNotIn('c.channel_id', sql)
        self.assertEqual(params[:3], [chat.CHAT_REQUEST_TYPE, date(2026, 11, 17),
                                      date(2026, 11, 17)])


if __name__ == '__main__':
    unittest.main()
