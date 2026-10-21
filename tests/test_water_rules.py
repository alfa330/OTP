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
from datetime import date, datetime, timedelta, timezone

from water import rules

TODAY = date(2026, 9, 30)
# Момент проверки: окно CRM «7 дней» — с 23.09 11:00 по 30.09 11:00.
NOW = datetime(2026, 9, 30, 11, 0)

SETTINGS = {
    'min_trips': 20, 'cooldown_days': 7, 'welcome_blocks': 1, 'activity_blocks': 1,
    'tariffs': ['business', 'ultimate'], 'low_threshold': 20, 'buy_threshold': 10,
}

ACCOUNT = 'a' * 32


def driver(total=0, week=0, tariffs=('econom', 'comfort', 'business'), account=ACCOUNT,
           times=None, window_from=None, truncated=False):
    """Карточка из water.driver.normalize. times — время заказов списка
    CRM «за 7 дней»; без них карточка как у старого ответа CRM (без списка)."""
    drv = {'account_id': account, 'iin': '900101300123',
           'tariffs': list(tariffs), 'orders': {'total': total, 'week': week}}
    if times is not None:
        drv['orders']['window_from'] = _crm_time(window_from or NOW - timedelta(days=7))
        drv['orders']['week_truncated'] = truncated
        drv['order_times'] = [_crm_time(moment) for moment in times]
    return drv


def _crm_time(moment):
    return moment.strftime('%Y-%m-%d %H:%M:%S')


def orders_between(start, end, count):
    """count заказов строго между start и end, через равные промежутки."""
    step = (end - start) / (count + 1)
    return [start + step * (n + 1) for n in range(count)]


def issue(kind='activity', days_ago=10, orders_total=100, account=ACCOUNT, office='Офис Алматы №1',
          orders_today=None):
    return {'id': 1, 'kind': kind, 'blocks': 1, 'orders_total': orders_total,
            'driver_account_id': account, 'office_name': office, 'orders_today': orders_today,
            'created_at': datetime(2026, 9, 30, 12, 0) - _days(days_ago)}


def _days(count):
    return timedelta(days=count)


def week_driver(history, after, before=0, total=500, window_from=None, **kwargs):
    """Водитель со списком заказов за 7 дней: after — после прошлой выдачи,
    before — до неё, но ещё внутри окна."""
    since = history[0]['created_at']
    start = window_from or NOW - timedelta(days=7)
    times = orders_between(since, NOW, after) + orders_between(start, since, before)
    return driver(total=total, week=len(times), times=times, window_from=start, **kwargs)


def evaluate(drv, history=(), settings=None, stock=50, today=TODAY, period=None):
    return rules.evaluate(drv, list(history), dict(SETTINGS, **(settings or {})),
                          today=today, stock=stock, period=period)


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
        history = [issue(days_ago=5)]
        verdict = evaluate(week_driver(history, after=20, before=10), history=history)
        self.assertFalse(verdict['allowed'])
        self.assertEqual(verdict['trips'], 20)
        self.assertEqual(verdict['trips_basis'], rules.BASIS_SINCE_LAST)
        self.assertEqual(verdict['reasons'],
                         ['С прошлой выдачи прошло 5 дней из 7 — следующая с 02.10.2026'])
        self.assertEqual(verdict['next_date'], '2026-10-02')

    def test_one_day_agrees_with_the_verb(self):
        """«прошёл 1 день», а не «прошло 1 день» — самый частый повторный визит."""
        history = [issue(days_ago=1)]
        verdict = evaluate(week_driver(history, after=20), history=history)
        self.assertIn('С прошлой выдачи прошёл 1 день из 7', verdict['reasons'][0])

    def test_on_the_seventh_calendar_day_it_is_allowed(self):
        history = [issue(days_ago=7)]
        verdict = evaluate(week_driver(history, after=20, before=2), history=history)
        self.assertTrue(verdict['allowed'])
        self.assertEqual(verdict['trips'], 20)

    def test_trips_are_counted_since_the_last_issue_not_the_week(self):
        """Седьмой день, окно CRM началось за 4 часа до прошлой выдачи: за 7 дней
        25 поездок, но 6 из них сделаны ДО выдачи — с неё всего 19."""
        history = [issue(days_ago=7)]
        drv = week_driver(history, after=19, before=6, window_from=datetime(2026, 9, 23, 8, 0))
        self.assertEqual(drv['orders']['week'], 25)
        verdict = evaluate(drv, history=history)
        self.assertFalse(verdict['allowed'])
        self.assertEqual(verdict['reasons'], ['Менее 20 поездок с прошлой выдачи — сейчас 19'])

    def test_order_at_the_moment_of_the_issue_is_not_after_it(self):
        history = [issue(days_ago=7)]
        drv = week_driver(history, after=19)
        drv['order_times'].append(_crm_time(history[0]['created_at']))
        self.assertEqual(evaluate(drv, history=history)['trips'], 19)

    def test_welcome_counts_as_the_previous_issue(self):
        """После приветственного блока неделя и поездки считаются от него."""
        history = [issue(kind='welcome', days_ago=3, orders_total=0)]
        verdict = evaluate(week_driver(history, after=30, total=30), history=history)
        self.assertEqual(verdict['kind'], 'activity')
        self.assertEqual(verdict['trips'], 30)
        self.assertFalse(verdict['allowed'])
        self.assertIn('следующая с 04.10.2026', verdict['reasons'][0])

    def test_half_year_counter_is_not_used(self):
        """Случай 21.10.2026, Астана: после выдачи 14.10 в 16:36 водитель сделал
        173 поездки, а счётчик CRM `total` (окно около полугода) вырос на 6 —
        старые заказы из него выпали. Разницей счётчика раздел насчитал 6 и
        отказал."""
        issued = datetime(2026, 10, 14, 16, 36, 32)
        history = [dict(issue(orders_total=3945, orders_today=8), created_at=issued)]
        now = datetime(2026, 10, 21, 16, 19, 56)
        window_from = now - timedelta(days=7)
        times = orders_between(datetime(2026, 10, 14, 16, 50), now, 173)
        drv = driver(total=3951, week=173, times=times, window_from=window_from,
                     tariffs=('business', 'ultimate'))
        today = date(2026, 10, 21)
        self.assertIsNone(rules.period_request(drv, history, SETTINGS, today=today))
        verdict = evaluate(drv, history=history, today=today)
        self.assertTrue(verdict['allowed'])
        self.assertEqual(verdict['trips'], 173)
        self.assertEqual(verdict['trips_basis'], rules.BASIS_SINCE_LAST)

    def test_issue_before_the_week_lets_a_full_week_suffice(self):
        """Выдача 10 дней назад: всё окно «7 дней» — после неё, и 22 поездки
        за неделю уже значат «не меньше 22 с прошлой выдачи»."""
        history = [issue(days_ago=10)]
        drv = driver(total=500, week=22, times=orders_between(NOW - timedelta(days=7), NOW, 22))
        self.assertIsNone(rules.period_request(drv, history, SETTINGS, today=TODAY))
        verdict = evaluate(drv, history=history)
        self.assertTrue(verdict['allowed'])
        self.assertEqual(verdict['trips'], 22)
        self.assertEqual(verdict['trips_basis'], rules.BASIS_WEEK)

    def test_other_account_of_the_same_person_counts_this_accounts_trips(self):
        """Выдача была на аккаунте другого парка: считаются поездки этого
        аккаунта после неё."""
        history = [issue(days_ago=3, account='b' * 32)]
        verdict = evaluate(week_driver(history, after=21), history=history,
                           settings={'cooldown_days': 3})
        self.assertTrue(verdict['allowed'])
        self.assertEqual(verdict['trips'], 21)
        self.assertEqual(verdict['trips_basis'], rules.BASIS_SINCE_LAST)

    def test_activity_is_also_limited_to_program_tariffs(self):
        verdict = evaluate(driver(total=500, week=40, tariffs=('econom',)))
        self.assertFalse(verdict['allowed'])
        self.assertIn('Тариф не входит в программу', verdict['reasons'][0])

    def test_all_reasons_are_listed_not_only_the_first(self):
        history = [issue(days_ago=2)]
        verdict = evaluate(week_driver(history, after=5, tariffs=('econom',)), history=history, stock=0)
        self.assertEqual(len(verdict['reasons']), 4)
        self.assertIn('Менее 20 поездок с прошлой выдачи — сейчас 5', verdict['reasons'])


class PeriodRequestTests(unittest.TestCase):
    """Выдача раньше окна «7 дней», и недели не хватает: поездки с неё знает
    только запрос CRM за даты — со дня выдачи по сегодня, минус заказы того
    дня до выдачи (`orders.today` в снимке CRM, который выдача хранит)."""

    def setUp(self):
        # 20.09 12:00, в тот день до выдачи — 3 заказа.
        self.history = [issue(days_ago=10, orders_today=3)]
        self.drv = driver(total=500, week=12, times=orders_between(NOW - timedelta(days=7), NOW, 12))
        self.request = {'issue_id': 1, 'date_from': '2026-09-20', 'date_to': '2026-09-30',
                        'subtract': 3}

    def test_request_is_from_the_issue_day_to_today(self):
        self.assertEqual(rules.period_request(self.drv, self.history, SETTINGS, today=TODAY),
                         self.request)

    def test_answer_counts_only_the_trips_after_the_issue(self):
        verdict = evaluate(self.drv, self.history, period=dict(self.request, count=24))
        self.assertTrue(verdict['allowed'])
        self.assertEqual(verdict['trips'], 21)
        self.assertEqual(verdict['trips_basis'], rules.BASIS_SINCE_LAST)
        refused = evaluate(self.drv, self.history, period=dict(self.request, count=22))
        self.assertEqual(refused['reasons'], ['Менее 20 поездок с прошлой выдачи — сейчас 19'])

    def test_without_an_answer_the_right_is_not_guessed(self):
        for period in (None, dict(self.request, count=None), dict(self.request, count='много')):
            with self.subTest(period=period):
                verdict = evaluate(self.drv, self.history, period=period)
                self.assertFalse(verdict['allowed'])
                self.assertIsNone(verdict['trips'])
                self.assertEqual(verdict['reasons'], [
                    'CRM не отдала число поездок с прошлой выдачи — право не проверить. '
                    'Попробуйте ещё раз через минуту'])

    def test_answer_to_another_request_is_not_taken(self):
        """История сменилась между запросом CRM и замком — например, прошлую
        выдачу отменили. Чужое число не засчитывается."""
        for key, value in (('issue_id', 2), ('date_from', '2026-09-21'),
                           ('date_to', '2026-09-29'), ('subtract', 0)):
            with self.subTest(key=key):
                period = dict(self.request, count=40, **{key: value})
                self.assertIsNone(evaluate(self.drv, self.history, period=period)['trips'])

    def test_count_below_the_trips_before_the_issue_is_zero(self):
        verdict = evaluate(self.drv, self.history, period=dict(self.request, count=1))
        self.assertEqual(verdict['trips'], 0)

    def test_unknown_trips_before_the_issue_skip_the_issue_day(self):
        """Снимок без `orders.today`: заказы дня выдачи до и после неё не
        различить — день не считаем вовсе. Лучше недосчитать, чем засчитать
        поездки, сделанные до выдачи."""
        history = [issue(days_ago=10, orders_today=None)]
        self.assertEqual(rules.period_request(self.drv, history, SETTINGS, today=TODAY),
                         dict(self.request, date_from='2026-09-21', subtract=0))

    def test_other_account_skips_the_issue_day(self):
        """`orders.today` в снимке — заказы ДРУГОГО аккаунта, вычитать их нельзя."""
        history = [issue(days_ago=10, orders_today=3, account='b' * 32)]
        self.assertEqual(rules.period_request(self.drv, history, SETTINGS, today=TODAY),
                         dict(self.request, date_from='2026-09-21', subtract=0))

    def test_old_crm_answer_without_the_list_asks_for_the_period(self):
        """Ответ CRM без списка заказов: поездок после выдачи не видно и на
        седьмой день — только запросом за даты."""
        history = [issue(days_ago=7, orders_today=5)]
        drv = driver(total=500, week=40)
        self.assertEqual(rules.period_request(drv, history, SETTINGS, today=TODAY),
                         {'issue_id': 1, 'date_from': '2026-09-23', 'date_to': '2026-09-30',
                          'subtract': 5})

    def test_truncated_list_is_complete_only_from_its_oldest_order(self):
        history = [issue(days_ago=7)]
        late = orders_between(datetime(2026, 9, 25), NOW, 500)
        drv = driver(total=5000, week=640, times=late, truncated=True)
        self.assertIsNotNone(rules.period_request(drv, history, SETTINGS, today=TODAY))
        early = orders_between(datetime(2026, 9, 23, 11, 30), NOW, 500)
        drv = driver(total=5000, week=640, times=early, truncated=True)
        self.assertIsNone(rules.period_request(drv, history, SETTINGS, today=TODAY))
        self.assertEqual(evaluate(drv, history)['trips'],
                         sum(1 for moment in early if moment > history[0]['created_at']))

    def test_no_request_while_the_days_have_not_passed(self):
        """Отказ по дням ясен и без CRM: ни запроса, ни причины «CRM не отдала»."""
        settings = dict(SETTINGS, cooldown_days=14)
        self.assertIsNone(rules.period_request(self.drv, self.history, settings, today=TODAY))
        verdict = evaluate(self.drv, self.history, settings={'cooldown_days': 14})
        self.assertEqual(verdict['reasons'],
                         ['С прошлой выдачи прошло 10 дней из 14 — следующая с 04.10.2026'])
        self.assertIsNone(verdict['trips'])

    def test_no_request_without_a_previous_issue_or_for_a_new_driver(self):
        self.assertIsNone(rules.period_request(self.drv, [], SETTINGS, today=TODAY))
        self.assertIsNone(rules.period_request(driver(total=0), self.history, SETTINGS, today=TODAY))
        self.assertIsNone(rules.period_request(driver(total=None), self.history, SETTINGS, today=TODAY))

    def test_no_request_when_the_trips_rule_is_off(self):
        settings = dict(SETTINGS, min_trips=0)
        self.assertIsNone(rules.period_request(self.drv, self.history, settings, today=TODAY))
        self.assertTrue(evaluate(self.drv, self.history, settings={'min_trips': 0})['allowed'])


class MomentTests(unittest.TestCase):
    def test_crm_and_database_times(self):
        expected = datetime(2026, 10, 14, 16, 36, 32)
        for value in ('2026-10-14 16:36:32', '2026-10-14T16:36:32', ' 2026-10-14 16:36:32 ',
                      expected, '2026-10-14T11:36:32+00:00',
                      datetime(2026, 10, 14, 11, 36, 32, tzinfo=timezone.utc)):
            with self.subTest(value=value):
                self.assertEqual(rules.moment_of(value), expected)
        self.assertEqual(rules.moment_of('2026-10-14T16:36:32.604603'),
                         datetime(2026, 10, 14, 16, 36, 32, 604603))
        self.assertEqual(rules.moment_of(date(2026, 10, 14)), datetime(2026, 10, 14))

    def test_garbage_is_none(self):
        for value in (None, '', '  ', 'вчера', '14.10.2026 16:36', 42):
            with self.subTest(value=value):
                self.assertIsNone(rules.moment_of(value))

    def test_unparsable_order_times_are_skipped(self):
        history = [issue(days_ago=7)]
        drv = week_driver(history, after=20)
        drv['order_times'] += ['', 'вчера', None]
        self.assertEqual(evaluate(drv, history)['trips'], 20)


class SettingsAreParametersTests(unittest.TestCase):
    """«Предусмотреть возможность изменения количества заказов и перечня
    тарифов без переработки основной логики» — ТЗ 3.2."""

    def test_threshold_of_trips_is_a_setting(self):
        self.assertTrue(evaluate(driver(total=500, week=12), settings={'min_trips': 10})['allowed'])

    def test_zero_trips_threshold_disables_the_trips_check(self):
        self.assertTrue(evaluate(driver(total=500, week=0), settings={'min_trips': 0})['allowed'])

    def test_cooldown_is_a_setting(self):
        history = [issue(days_ago=3)]
        drv = week_driver(history, after=25)
        self.assertTrue(evaluate(drv, history=history, settings={'cooldown_days': 3})['allowed'])
        self.assertTrue(evaluate(drv, history=history, settings={'cooldown_days': 0})['allowed'])

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


class ProgramOfficeTests(unittest.TestCase):
    """«Оставь города Алматы, Астана … и убрать офисы Wolt» (01.10.2026)."""

    def test_almaty_and_astana_offices_are_in_the_program(self):
        for city, name in (('Алматы', 'Офис Алматы №1'), ('Астана', 'Офис Астана'),
                           ('Алматы', 'Офис для подключения тарифа «Бизнес»'),
                           (' алматы ', 'Офис Алматы №2')):
            self.assertTrue(rules.is_program_office({'city': city, 'name': name}), (city, name))

    def test_wolt_offices_are_not(self):
        for name in ('Офис для подключения тарифа «Wolt', 'WOLT', 'Офис wolt'):
            self.assertFalse(rules.is_program_office({'city': 'Астана', 'name': name}), name)

    def test_astana_business_office_is_not(self):
        """«Убрать из селектора Астана офис для подключения Бизнес» (05.10.2026):
        один офис, по id справочника вики, — как бы его ни переименовали."""
        name = 'Офис для подключения тарифа «Бизнес»'
        self.assertEqual(rules.EXCLUDED_OFFICE_IDS, (44,))
        for renamed in (name, 'Офис Астана', 'Business', ''):
            self.assertFalse(rules.is_program_office({'id': 44, 'city': 'Астана', 'name': renamed}), renamed)

    def test_only_that_office_is_removed(self):
        """Офис Алматы с тем же названием, «Офис Астана» по тому же адресу и
        офис Астаны со словом «бизнес» в названии в учёт заводятся."""
        name = 'Офис для подключения тарифа «Бизнес»'
        for office_id, city, office_name in ((43, 'Алматы', name), (49, 'Астана', 'Офис Астана'),
                                             (90, 'Астана', 'Офис Астана №2 (Бизнес-центр «Москва»)')):
            self.assertTrue(rules.is_program_office({'id': office_id, 'city': city, 'name': office_name}),
                            (office_id, city, office_name))

    def test_other_cities_are_not(self):
        for city in ('Шымкент', 'Караганда', '', None):
            self.assertFalse(rules.is_program_office({'city': city, 'name': 'Офис'}), city)
        self.assertFalse(rules.is_program_office(None))


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
