# -*- coding: utf-8 -*-
"""Карточка водителя из CRM для «Учёта воды» — разбор заказов недели по тарифам.

С 01.10.2026 `driver-info` отдаёт `orders.last_7_days`: сами заказы того же
скользящего окна, что счётчик `week`, у каждого `category` — тариф заказа.
Раздел показывает из них заказы на тарифах программы; право на воду при этом
считается, как и раньше, по всем тарифам.
"""

import unittest
from datetime import date

import requests

from water import driver as water_driver
from water import rules

ACCOUNT = 'a' * 32


def order(category, number):
    return {
        'yandex_order_id': 'order-%d' % number, 'price': 1500, 'category': category,
        'payment_method': 'cash', 'mileage': 8400,
        'booked_at': '2026-09-30 11:40:19', 'ended_at': '2026-09-30 12:05:00',
    }


def crm(categories=None, truncated=False, week=None, last_7_days=True):
    """Ответ CRM той формы, что в документации и живом снимке от 01.10.2026."""
    categories = list(categories or [])
    orders = {
        'today': 2, 'week': len(categories) if week is None else week,
        'month': 90, 'total': 2400,
        'first_order_at': '2022-03-12 09:00:00', 'last_order_at': '2026-10-01 11:45:41',
        'latest_orders': [order(code, n) for n, code in enumerate(categories[:3])],
    }
    if last_7_days:
        orders['last_7_days'] = {
            'from': '2026-09-24 12:00:48', 'to': '2026-10-01 12:00:48',
            'count': len(categories), 'truncated': truncated,
            'items': [order(code, n) for n, code in enumerate(categories)],
        }
    return {
        'account_id': ACCOUNT,
        'driver': {'last_name': 'Тестов', 'first_name': 'Тест', 'phone': '+77010000000',
                   'iin': '900101300123'},
        'park': {'name': 'Парк', 'yandex_id': 'b' * 32},
        'car': {'model': 'Toyota Camry', 'license_plate': '001AAA02',
                'tariffs': ['business', 'comfort', 'econom']},
        'employment': {'work_status': 'working', 'created_date': '2022-03-01'},
        'orders': orders,
    }


class WeekByTariffTests(unittest.TestCase):
    def test_orders_of_the_week_are_counted_per_tariff(self):
        found = water_driver.normalize(crm(['business'] * 3 + ['econom'] * 5 + ['ultimate']))
        self.assertEqual(found['orders']['week_by_tariff'],
                         {'business': 3, 'econom': 5, 'ultimate': 1})
        self.assertFalse(found['orders']['week_truncated'])
        self.assertEqual(found['orders']['week'], 9)

    def test_tariff_code_is_matched_case_and_space_insensitive(self):
        found = water_driver.normalize(crm(['Business', ' business ', 'BUSINESS']))
        self.assertEqual(found['orders']['week_by_tariff'], {'business': 3})

    def test_order_without_tariff_counts_to_no_tariff(self):
        found = water_driver.normalize(crm(['business', None, '', '  ']))
        self.assertEqual(found['orders']['week_by_tariff'], {'business': 1})

    def test_garbage_items_are_skipped_not_fatal(self):
        data = crm(['business'])
        data['orders']['last_7_days']['items'] += [None, 'business', 42]
        found = water_driver.normalize(data)
        self.assertEqual(found['orders']['week_by_tariff'], {'business': 1})

    def test_week_without_orders_is_an_empty_breakdown_not_missing_data(self):
        found = water_driver.normalize(crm([]))
        self.assertEqual(found['orders']['week_by_tariff'], {})

    def test_old_crm_answer_without_the_list_means_no_data(self):
        # Снимки по 30.09.2026 включительно: счётчики есть, списка недели нет.
        found = water_driver.normalize(crm(['econom'] * 4, last_7_days=False))
        self.assertIsNone(found['orders']['week_by_tariff'])
        self.assertFalse(found['orders']['week_truncated'])
        self.assertEqual(found['orders']['week'], 4)

    def test_broken_list_means_no_data(self):
        for broken in (None, 'items', {'business': 3}):
            data = crm(['business'])
            data['orders']['last_7_days']['items'] = broken
            with self.subTest(items=broken):
                self.assertIsNone(water_driver.normalize(data)['orders']['week_by_tariff'])
        data = crm(['business'])
        data['orders']['last_7_days'] = []
        self.assertIsNone(water_driver.normalize(data)['orders']['week_by_tariff'])

    def test_orders_null_means_no_data(self):
        data = crm(['business'])
        data['orders'] = None
        found = water_driver.normalize(data)
        self.assertIsNone(found['orders']['week_by_tariff'])
        self.assertIsNone(found['orders']['week'])

    def test_truncated_list_is_flagged(self):
        found = water_driver.normalize(crm(['business'] * 2, truncated=True, week=640))
        self.assertTrue(found['orders']['week_truncated'])
        self.assertEqual(found['orders']['week_by_tariff'], {'business': 2})

    def test_only_a_real_true_marks_the_list_truncated(self):
        for value in ('true', 1, None, 'false'):
            data = crm(['business'])
            data['orders']['last_7_days']['truncated'] = value
            with self.subTest(truncated=value):
                self.assertFalse(water_driver.normalize(data)['orders']['week_truncated'])


class OrderTimesTests(unittest.TestCase):
    """Время заказов за 7 дней — по нему считаются поездки после прошлой выдачи."""

    def test_booked_time_of_every_order_and_the_window_start(self):
        found = water_driver.normalize(crm(['business', 'econom']))
        self.assertEqual(found['order_times'], ['2026-09-30 11:40:19'] * 2)
        self.assertEqual(found['orders']['window_from'], '2026-09-24 12:00:48')

    def test_end_time_when_the_booking_time_is_missing(self):
        data = crm(['business'] * 2)
        data['orders']['last_7_days']['items'][0]['booked_at'] = None
        found = water_driver.normalize(data)
        self.assertEqual(found['order_times'], ['2026-09-30 12:05:00', '2026-09-30 11:40:19'])

    def test_orders_without_time_or_garbage_are_skipped(self):
        data = crm(['business'])
        data['orders']['last_7_days']['items'] += [None, 'business', {'category': 'econom'}]
        self.assertEqual(water_driver.normalize(data)['order_times'], ['2026-09-30 11:40:19'])

    def test_no_list_means_no_data_not_zero_trips(self):
        found = water_driver.normalize(crm(['econom'] * 4, last_7_days=False))
        self.assertIsNone(found['order_times'])
        self.assertIsNone(found['orders']['window_from'])
        data = crm(['business'])
        data['orders'] = None
        self.assertIsNone(water_driver.normalize(data)['order_times'])

    def test_empty_week_is_an_empty_list(self):
        self.assertEqual(water_driver.normalize(crm([]))['order_times'], [])


class _Response:
    def __init__(self, status=200, payload=None, text=None):
        self.status_code = status
        self._payload = payload
        self._text = text

    def json(self):
        if self._text is not None:
            raise ValueError('not json')
        return self._payload


class _Session:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({'url': url, 'json': json, 'headers': headers, 'timeout': timeout})
        if self.error:
            raise self.error
        return self.response


class OrdersCountTests(unittest.TestCase):
    """`POST /api/partners/orders` — заказы аккаунта за календарные даты."""

    CONFIG = {'url': 'https://backend.yataxi.kz/api/partners/driver-info', 'token': 't0ken'}

    def count(self, session, account=ACCOUNT, config=None):
        return water_driver.orders_count(account, '2026-10-14', '2026-10-21',
                                         config=config or self.CONFIG, session=session)

    def ok(self, data):
        return _Session(_Response(payload={'from': '2026-10-14', 'to': '2026-10-21', 'data': data}))

    def test_count_of_the_account_and_the_request_form(self):
        session = self.ok({ACCOUNT: {'orders_count': 182, 'first_order_at': '2026-10-14 10:58:11',
                                     'last_order_at': '2026-10-21 10:26:38'}})
        self.assertEqual(self.count(session, account=ACCOUNT.upper()), 182)
        call = session.calls[0]
        self.assertEqual(call['url'], 'https://backend.yataxi.kz/api/partners/orders')
        self.assertEqual(call['json'], {'account_ids': [ACCOUNT], 'from': '2026-10-14',
                                        'to': '2026-10-21'})
        self.assertEqual(call['headers']['X-Integration-Token'], 't0ken')
        self.assertTrue(call['timeout'])

    def test_zero_is_a_real_answer(self):
        self.assertEqual(self.count(self.ok({ACCOUNT: {'orders_count': 0}})), 0)

    def test_key_is_matched_without_case(self):
        self.assertEqual(self.count(self.ok({ACCOUNT.upper(): {'orders_count': '7'}})), 7)

    def test_stand_stub_gets_the_request_too(self):
        """Стенд подменяет CRM одной CRM_DRIVER_INFO_URL — запрос за даты идёт туда же."""
        session = self.ok({ACCOUNT: {'orders_count': 1}})
        self.count(session, config={'url': 'http://127.0.0.1:5103/api/partners/driver-info/',
                                    'token': 'stand'})
        self.assertEqual(session.calls[0]['url'], 'http://127.0.0.1:5103/api/partners/orders')

    def test_failures_are_errors_not_zero(self):
        cases = (
            ('crm_unavailable', _Session(error=requests.ConnectionError('down'))),
            ('crm_unavailable', _Session(_Response(503, {'error': 'orders_unavailable'}))),
            ('crm_error', _Session(_Response(422, {'message': 'bad dates'}))),
            ('crm_error', _Session(_Response(401, {'error': 'unauthorized'}))),
            ('crm_bad_payload', _Session(_Response(200, text='<html>'))),
            ('crm_bad_payload', self.ok({})),
            ('crm_bad_payload', self.ok({'d' * 32: {'orders_count': 5}})),
            ('crm_bad_payload', self.ok({ACCOUNT: {'orders_count': None}})),
            ('crm_bad_payload', self.ok({ACCOUNT: {'orders_count': -1}})),
            ('crm_bad_payload', self.ok({ACCOUNT: 5})),
            ('crm_bad_payload', _Session(_Response(200, ['data']))),
        )
        for code, session in cases:
            with self.subTest(code=code, response=session.response):
                with self.assertRaises(water_driver.DriverLookupError) as caught:
                    self.count(session)
                self.assertEqual(caught.exception.code, code)

    def test_without_a_token_nothing_is_sent(self):
        session = self.ok({ACCOUNT: {'orders_count': 1}})
        with self.assertRaises(water_driver.DriverLookupError) as caught:
            self.count(session, config={'url': self.CONFIG['url'], 'token': ''})
        self.assertEqual(caught.exception.code, 'not_configured')
        self.assertEqual(session.calls, [])


class PublicCardTests(unittest.TestCase):
    def test_breakdown_goes_to_the_screen_but_the_order_list_does_not(self):
        found = water_driver.normalize(crm(['business'] * 2 + ['econom']))
        card = water_driver.public(found)
        self.assertEqual(card['orders']['week_by_tariff'], {'business': 2, 'econom': 1})
        # Сами заказы (id, цены) остаются в снимке на сервере, на экран — только счёт.
        self.assertNotIn('info', card)
        self.assertNotIn('iin', card)
        self.assertNotIn('order-0', repr(card))
        # Время заказов — серверу для счёта поездок, на экран не едет.
        self.assertIn('order_times', found)
        self.assertNotIn('order_times', card)
        self.assertNotIn('2026-09-30 11:40:19', repr(card))


class RuleIsUnchangedTests(unittest.TestCase):
    """Разбивка недели — для экрана. Правило «20 поездок» считает все тарифы."""

    SETTINGS = {
        'min_trips': 20, 'cooldown_days': 7, 'welcome_blocks': 1, 'activity_blocks': 1,
        'tariffs': ['business', 'ultimate'],
    }

    def test_week_of_econom_trips_still_counts_for_the_activity_block(self):
        found = water_driver.normalize(crm(['econom'] * 25))
        self.assertEqual(found['orders']['week_by_tariff'], {'econom': 25})
        verdict = rules.evaluate(found, [], self.SETTINGS, today=date(2026, 10, 1), stock=10)
        self.assertTrue(verdict['allowed'])
        self.assertEqual(verdict['trips'], 25)
        self.assertEqual(verdict['trips_basis'], rules.BASIS_WEEK)


if __name__ == '__main__':
    unittest.main()
