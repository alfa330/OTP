# -*- coding: utf-8 -*-
"""Карточка водителя из CRM для «Учёта воды» — разбор заказов недели по тарифам.

С 01.10.2026 `driver-info` отдаёт `orders.last_7_days`: сами заказы того же
скользящего окна, что счётчик `week`, у каждого `category` — тариф заказа.
Раздел показывает из них заказы на тарифах программы; право на воду при этом
считается, как и раньше, по всем тарифам.
"""

import unittest
from datetime import date

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


class PublicCardTests(unittest.TestCase):
    def test_breakdown_goes_to_the_screen_but_the_order_list_does_not(self):
        found = water_driver.normalize(crm(['business'] * 2 + ['econom']))
        card = water_driver.public(found)
        self.assertEqual(card['orders']['week_by_tariff'], {'business': 2, 'econom': 1})
        # Сами заказы (id, цены) остаются в снимке на сервере, на экран — только счёт.
        self.assertNotIn('info', card)
        self.assertNotIn('iin', card)
        self.assertNotIn('order-0', repr(card))


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
