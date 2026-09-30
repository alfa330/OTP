# -*- coding: utf-8 -*-
"""Право водителя на воду — правила из ТЗ и ответов постановщика, дословно.

Каждый тест — одна фраза постановки:
  * «новым водителем считается тот, кто был зарегистрирован и не выполнил
    заказ» — ноль заказов в CRM;
  * «приветственный блок даётся только если он будет работать по тарифам
    Business и Ultima»;
  * «следующий блок он может получить, когда сделает 20 поездок и не менее
    7 дней чтобы прошло; если за 5 дней сделает 20 поездок, то получит блок
    только когда пройдёт 7 дней»;
  * «1 раз в неделю при условии, что будет 20 поездок, 1 блок»;
  * причины отказа из ТЗ (раздел 4) — видны человеку словами.
"""

import unittest
from datetime import date, datetime

from water import rules

TODAY = date(2026, 9, 30)

SETTINGS = {
    'min_trips': 20, 'cooldown_days': 7, 'welcome_blocks': 1, 'activity_blocks': 1,
    'tariffs': ['business', 'ultimate'], 'low_threshold': 20, 'buy_threshold': 10,
}

ACCOUNT = 'a' * 32


def driver(total=0, week=0, tariffs=('econom', 'comfort', 'business'), account=ACCOUNT):
    return {'account_id': account, 'iin': '900101300123',
            'tariffs': list(tariffs), 'orders': {'total': total, 'week': week}}


def issue(kind='activity', days_ago=10, orders_total=100, account=ACCOUNT, office='Офис Алматы №1'):
    return {'id': 1, 'kind': kind, 'blocks': 1, 'orders_total': orders_total,
            'driver_account_id': account, 'office_name': office,
            'created_at': datetime(2026, 9, 30, 12, 0) - _days(days_ago)}


def _days(count):
    from datetime import timedelta
    return timedelta(days=count)


def evaluate(drv, history=(), settings=None, stock=50, today=TODAY):
    return rules.evaluate(drv, list(history), dict(SETTINGS, **(settings or {})),
                          today=today, stock=stock)


class NewDriverTests(unittest.TestCase):
    def test_zero_orders_is_a_new_driver_and_gets_the_welcome_block(self):
        verdict = evaluate(driver(total=0))
        self.assertEqual(verdict['kind'], 'welcome')
        self.assertTrue(verdict['allowed'])
        self.assertEqual(verdict['blocks_max'], 1)
        self.assertEqual(verdict['reasons'], [])

    def test_one_order_is_no_longer_new(self):
        verdict = evaluate(driver(total=1, week=1))
        self.assertEqual(verdict['kind'], 'activity')

    def test_welcome_only_on_program_tariffs(self):
        verdict = evaluate(driver(total=0, tariffs=('econom', 'comfort', 'comfort_plus')))
        self.assertFalse(verdict['allowed'])
        self.assertIn('Тариф не входит в программу', verdict['reasons'][0])
        self.assertIn('Business', verdict['reasons'][0])

    def test_ultima_premier_code_is_in_the_program(self):
        """Ultima в Алматы — тариф Premier, код Флита `ultimate`."""
        self.assertTrue(evaluate(driver(total=0, tariffs=('econom', 'ultimate')))['allowed'])

    def test_comfort_code_business_trap(self):
        """Ловушка кодов: на странице тарифов Яндекс Go «Комфорт» = `business`,
        а в CRM (коды Флита) Комфорт — `comfort`. Раздел читает CRM, и
        `comfort` в программу не входит."""
        self.assertFalse(evaluate(driver(total=0, tariffs=('comfort',)))['allowed'])

    def test_welcome_is_given_once(self):
        verdict = evaluate(driver(total=0), history=[issue(kind='welcome', days_ago=3, orders_total=0)])
        self.assertFalse(verdict['allowed'])
        self.assertIn('Приветственный блок уже выдан 27.09.2026', verdict['reasons'][0])
        self.assertIn('Офис Алматы №1', verdict['reasons'][0])

    def test_welcome_once_even_across_offices_and_months(self):
        verdict = evaluate(driver(total=0), history=[issue(kind='welcome', days_ago=90, orders_total=0)])
        self.assertFalse(verdict['allowed'])

    def test_fk_passed_in_crm_is_enough(self):
        drv = dict(driver(total=0), photo_control={'status': 'passed'})
        verdict = evaluate(drv)
        self.assertTrue(verdict['allowed'])
        self.assertEqual(verdict['fk'], rules.FK_PASSED)

    def test_fk_not_passed_in_crm_refuses(self):
        """«У них обязательно должен быть пройден ФК» (30.09.2026)."""
        drv = dict(driver(total=0), photo_control={'status': 'failed'})
        verdict = evaluate(drv)
        self.assertFalse(verdict['allowed'])
        self.assertIn('ФК не пройден — в CRM статус «failed»', verdict['reasons'])

    def test_unknown_fk_needs_staff_confirmation_at_issue(self):
        """CRM статуса не знает: в предпросмотре не отказ, а подтверждение
        впереди; на выдаче без отметки — отказ, с отметкой — можно."""
        preview = rules.evaluate(driver(total=0), [], SETTINGS, today=TODAY, stock=5)
        self.assertTrue(preview['allowed'])
        self.assertEqual(preview['fk'], rules.FK_UNKNOWN)
        refused = rules.evaluate(driver(total=0), [], SETTINGS, today=TODAY, stock=5, fk_confirmed=False)
        self.assertEqual(refused['reasons'], ['Подтвердите, что водитель прошёл ФК'])
        confirmed = rules.evaluate(driver(total=0), [], SETTINGS, today=TODAY, stock=5, fk_confirmed=True)
        self.assertTrue(confirmed['allowed'])
        self.assertEqual(confirmed['fk'], rules.FK_CONFIRMED)

    def test_activity_does_not_look_at_fk(self):
        drv = dict(driver(total=500, week=30), photo_control={'status': 'failed'})
        verdict = rules.evaluate(drv, [], SETTINGS, today=TODAY, stock=5, fk_confirmed=False)
        self.assertTrue(verdict['allowed'])
        self.assertIsNone(verdict['fk'])

    def test_new_driver_needs_no_trips(self):
        verdict = evaluate(driver(total=0, week=0))
        self.assertEqual(verdict['trips_basis'], rules.BASIS_NEW)
        self.assertTrue(verdict['allowed'])


class ActivityTests(unittest.TestCase):
    def test_first_activity_block_needs_twenty_trips_in_the_last_week(self):
        verdict = evaluate(driver(total=500, week=25))
        self.assertTrue(verdict['allowed'])
        self.assertEqual(verdict['kind'], 'activity')
        self.assertEqual(verdict['trips'], 25)
        self.assertEqual(verdict['trips_basis'], rules.BASIS_WEEK)

    def test_fewer_than_twenty_in_the_week_is_refused_with_the_number(self):
        verdict = evaluate(driver(total=500, week=12))
        self.assertFalse(verdict['allowed'])
        self.assertEqual(verdict['reasons'],
                         ['Менее 20 поездок за последние 7 дней — сейчас 12'])

    def test_exactly_twenty_is_enough(self):
        self.assertTrue(evaluate(driver(total=500, week=20))['allowed'])

    def test_twenty_trips_in_five_days_waits_for_the_seventh_day(self):
        """Дословный пример постановщика."""
        history = [issue(days_ago=5, orders_total=480)]
        verdict = evaluate(driver(total=500, week=30), history=history)
        self.assertFalse(verdict['allowed'])
        self.assertEqual(verdict['trips'], 20)
        self.assertEqual(verdict['trips_basis'], rules.BASIS_SINCE_LAST)
        self.assertEqual(verdict['reasons'],
                         ['С прошлой выдачи прошло 5 дней из 7 — следующая с 02.10.2026'])
        self.assertEqual(verdict['next_date'], '2026-10-02')

    def test_one_day_agrees_with_the_verb(self):
        """«прошёл 1 день», а не «прошло 1 день» — самый частый повторный визит."""
        history = [issue(days_ago=1, orders_total=480)]
        verdict = evaluate(driver(total=500, week=30), history=history)
        self.assertIn('С прошлой выдачи прошёл 1 день из 7', verdict['reasons'][0])

    def test_on_the_seventh_calendar_day_it_is_allowed(self):
        history = [issue(days_ago=7, orders_total=480)]
        self.assertTrue(evaluate(driver(total=500, week=5), history=history)['allowed'])

    def test_trips_are_counted_since_the_last_issue_not_the_week(self):
        """Прошло 10 дней, за последние 7 дней 25 поездок, но с прошлой выдачи
        всего 19 — правило постановщика: «когда сделает 20 поездок» с неё."""
        history = [issue(days_ago=10, orders_total=481)]
        verdict = evaluate(driver(total=500, week=25), history=history)
        self.assertFalse(verdict['allowed'])
        self.assertEqual(verdict['reasons'], ['Менее 20 поездок с прошлой выдачи — сейчас 19'])

    def test_welcome_counts_as_the_previous_issue(self):
        """После приветственного блока неделя и поездки считаются от него."""
        history = [issue(kind='welcome', days_ago=3, orders_total=0)]
        verdict = evaluate(driver(total=30, week=30), history=history)
        self.assertEqual(verdict['kind'], 'activity')
        self.assertEqual(verdict['trips'], 30)
        self.assertFalse(verdict['allowed'])
        self.assertIn('следующая с 04.10.2026', verdict['reasons'][0])

    def test_other_account_of_the_same_person_falls_back_to_the_week(self):
        """У другого парка свой счётчик заказов — сравнивать его нельзя."""
        history = [issue(days_ago=10, orders_total=4000, account='b' * 32)]
        verdict = evaluate(driver(total=50, week=22), history=history)
        self.assertTrue(verdict['allowed'])
        self.assertEqual(verdict['trips_basis'], rules.BASIS_WEEK)

    def test_counter_that_went_down_falls_back_to_the_week(self):
        history = [issue(days_ago=10, orders_total=900)]
        verdict = evaluate(driver(total=500, week=21), history=history)
        self.assertEqual(verdict['trips_basis'], rules.BASIS_WEEK)
        self.assertTrue(verdict['allowed'])

    def test_activity_is_also_limited_to_program_tariffs(self):
        verdict = evaluate(driver(total=500, week=40, tariffs=('econom',)))
        self.assertFalse(verdict['allowed'])
        self.assertIn('Тариф не входит в программу', verdict['reasons'][0])

    def test_all_reasons_are_listed_not_only_the_first(self):
        history = [issue(days_ago=2, orders_total=495)]
        verdict = evaluate(driver(total=500, week=5, tariffs=('econom',)), history=history, stock=0)
        self.assertEqual(len(verdict['reasons']), 4)


class SettingsAreParametersTests(unittest.TestCase):
    """«Предусмотреть возможность изменения количества заказов и перечня
    тарифов без переработки основной логики» — ТЗ 3.2."""

    def test_threshold_of_trips_is_a_setting(self):
        self.assertTrue(evaluate(driver(total=500, week=12), settings={'min_trips': 10})['allowed'])

    def test_zero_trips_threshold_disables_the_trips_check(self):
        self.assertTrue(evaluate(driver(total=500, week=0), settings={'min_trips': 0})['allowed'])

    def test_cooldown_is_a_setting(self):
        history = [issue(days_ago=3, orders_total=400)]
        self.assertTrue(evaluate(driver(total=500), history=history,
                                 settings={'cooldown_days': 3})['allowed'])
        self.assertTrue(evaluate(driver(total=500), history=history,
                                 settings={'cooldown_days': 0})['allowed'])

    def test_tariff_list_is_a_setting(self):
        self.assertTrue(evaluate(driver(total=0, tariffs=('comfort',)),
                                 settings={'tariffs': ['comfort']})['allowed'])

    def test_blocks_per_issue_are_settings(self):
        self.assertEqual(evaluate(driver(total=0), settings={'welcome_blocks': 2})['blocks_max'], 2)
        self.assertEqual(evaluate(driver(total=500, week=30),
                                  settings={'activity_blocks': 3})['blocks_max'], 3)


class StockTests(unittest.TestCase):
    def test_empty_office_refuses(self):
        verdict = evaluate(driver(total=0), stock=0)
        self.assertFalse(verdict['allowed'])
        self.assertEqual(verdict['reasons'], ['В офисе нет воды'])

    def test_without_an_office_water_is_not_checked(self):
        """Колл-центр проверяет право без офиса."""
        verdict = evaluate(driver(total=0), stock=None)
        self.assertTrue(verdict['allowed'])

    def test_blocks_are_capped_by_what_is_left(self):
        verdict = evaluate(driver(total=500, week=30), settings={'activity_blocks': 3}, stock=2)
        self.assertTrue(verdict['allowed'])
        self.assertEqual(verdict['blocks_max'], 2)


class BrokenCrmTests(unittest.TestCase):
    def test_missing_order_counter_never_makes_a_new_driver(self):
        """Мусор вместо счётчика не должен молча выдавать приветственный блок."""
        verdict = evaluate({'account_id': ACCOUNT, 'tariffs': ['business'], 'orders': {}})
        self.assertFalse(verdict['allowed'])
        self.assertIsNone(verdict['kind'])
        self.assertIn('CRM не отдала число заказов', verdict['reasons'][0])

    def test_missing_week_for_first_activity_is_refused(self):
        verdict = evaluate(driver(total=500, week=None))
        self.assertFalse(verdict['allowed'])
        self.assertIn('CRM не отдала число поездок', verdict['reasons'][0])


class StockStatusTests(unittest.TestCase):
    def test_threshold_is_inclusive(self):
        """«При достижении минимального остатка» — ровно на пороге уже закупка."""
        self.assertEqual(rules.stock_status(10, 20, 10), 'buy')
        self.assertEqual(rules.stock_status(11, 20, 10), 'low')
        self.assertEqual(rules.stock_status(20, 20, 10), 'low')
        self.assertEqual(rules.stock_status(21, 20, 10), 'enough')
        self.assertEqual(rules.stock_status(0, 0, 0), 'buy')


class WordsTests(unittest.TestCase):
    def test_russian_plural(self):
        self.assertEqual(rules.days_word(1), '1 день')
        self.assertEqual(rules.days_word(3), '3 дня')
        self.assertEqual(rules.days_word(5), '5 дней')
        self.assertEqual(rules.days_word(11), '11 дней')
        self.assertEqual(rules.days_word(21), '21 день')
        self.assertEqual(rules.blocks_word(2), '2 блока')
        self.assertEqual(rules.trips_word(14), '14 поездок')
        self.assertEqual(rules.blocks_genitive(1), '1 блока')
        self.assertEqual(rules.blocks_genitive(2), '2 блоков')

    def test_less_than_one_trip_reads_in_genitive(self):
        verdict = evaluate(driver(total=500, week=0), settings={'min_trips': 1})
        self.assertEqual(verdict['reasons'], ['Менее 1 поездки за последние 7 дней — сейчас 0'])


if __name__ == '__main__':
    unittest.main()
