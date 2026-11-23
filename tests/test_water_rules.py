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
                        'subtract': 3, 'partial': False}

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

    def test_count_below_the_trips_before_the_issue_is_no_data_not_zero(self):
        """За даты CRM отдала меньше заказов, чем было до выдачи по её же
        снимку: данные противоречат друг другу — «сейчас 0» было бы выдумкой."""
        verdict = evaluate(self.drv, self.history, period=dict(self.request, count=1))
        self.assertIsNone(verdict['trips'])
        self.assertIn('CRM не отдала число поездок с прошлой выдачи', verdict['reasons'][0])
        self.assertEqual(evaluate(self.drv, self.history, period=dict(self.request, count=3))['trips'], 0)

    def test_unknown_trips_before_the_issue_skip_the_issue_day(self):
        """Снимок без `orders.today`: заказы дня выдачи до и после неё не
        различить — день не считаем вовсе. Лучше недосчитать, чем засчитать
        поездки, сделанные до выдачи."""
        history = [issue(days_ago=10, orders_today=None)]
        self.assertEqual(rules.period_request(self.drv, history, SETTINGS, today=TODAY),
                         dict(self.request, date_from='2026-09-21', subtract=0, partial=True))

    def test_other_account_skips_the_issue_day(self):
        """`orders.today` в снимке — заказы ДРУГОГО аккаунта, вычитать их нельзя."""
        history = [issue(days_ago=10, orders_today=3, account='b' * 32)]
        request = rules.period_request(self.drv, history, SETTINGS, today=TODAY)
        self.assertEqual(request, dict(self.request, date_from='2026-09-21', subtract=0, partial=True))
        self.assertTrue(evaluate(self.drv, history, period=dict(request, count=20))['allowed'])

    def test_count_without_the_issue_day_says_so_in_the_refusal(self):
        """Без дня выдачи число — нижняя граница: в отказе это сказано, а не
        выдано за точный счёт."""
        history = [issue(days_ago=10, orders_today=3, account='b' * 32)]
        request = rules.period_request(self.drv, history, SETTINGS, today=TODAY)
        verdict = evaluate(self.drv, history, period=dict(request, count=16))
        self.assertEqual(verdict['reasons'], [
            'Менее 20 поездок с прошлой выдачи — сейчас 16, не считая дня прошлой выдачи'])

    def test_issue_today_with_unknown_trips_before_it_is_no_data(self):
        """Перерыв 0 дней, выдача сегодня на другом аккаунте, списка заказов в
        ответе нет: заказы после неё не посчитать — «нет данных», а не ноль."""
        history = [dict(issue(account='b' * 32), created_at=datetime(2026, 9, 30, 8, 0))]
        drv = driver(total=500, week=30)
        settings = {'cooldown_days': 0}
        self.assertIsNone(rules.period_request(drv, history, dict(SETTINGS, **settings), today=TODAY))
        verdict = evaluate(drv, history, settings=settings)
        self.assertIsNone(verdict['trips'])
        self.assertEqual(verdict['reasons'], [
            'CRM не отдала число поездок с прошлой выдачи — право не проверить. '
            'Попробуйте ещё раз через минуту'])

    def test_old_crm_answer_without_the_list_asks_for_the_period(self):
        """Ответ CRM без списка заказов: поездок после выдачи не видно и на
        седьмой день — только запросом за даты."""
        history = [issue(days_ago=7, orders_today=5)]
        drv = driver(total=500, week=40)
        self.assertEqual(rules.period_request(drv, history, SETTINGS, today=TODAY),
                         {'issue_id': 1, 'date_from': '2026-09-23', 'date_to': '2026-09-30',
                          'subtract': 5, 'partial': False})

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

    def test_truncated_list_without_readable_times_is_not_counted_as_zero(self):
        history = [issue(days_ago=7)]
        drv = driver(total=5000, week=640, times=[], truncated=True)
        drv['order_times'] = ['вчера', '', 'нет']
        self.assertIsNotNone(rules.period_request(drv, history, SETTINGS, today=TODAY))
        self.assertIsNone(evaluate(drv, history)['trips'])

    def test_issue_exactly_at_the_window_start_is_counted_from_the_list(self):
        history = [issue(days_ago=7)]
        since = history[0]['created_at']
        drv = driver(total=500, week=21, times=orders_between(since, NOW, 21), window_from=since)
        self.assertIsNone(rules.period_request(drv, history, SETTINGS, today=TODAY))
        self.assertEqual((evaluate(drv, history)['trips'], evaluate(drv, history)['trips_basis']),
                         (21, rules.BASIS_SINCE_LAST))

    def test_exactly_the_threshold_in_the_week_after_an_old_issue_needs_no_request(self):
        history = [issue(days_ago=10)]
        drv = driver(total=500, week=20, times=orders_between(NOW - timedelta(days=7), NOW, 20))
        self.assertIsNone(rules.period_request(drv, history, SETTINGS, today=TODAY))
        verdict = evaluate(drv, history)
        self.assertTrue(verdict['allowed'])
        self.assertEqual((verdict['trips'], verdict['trips_basis']), (20, rules.BASIS_WEEK))

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


def backfill(drv, history=(), moment=None, fk_confirmed=None, settings=None, kind='welcome',
             prev=None, prev_account=None, period=None):
    return rules.backfill_verdict(drv, list(history), dict(SETTINGS, **(settings or {})), kind=kind,
                                  moment=moment, prev=prev, prev_account=prev_account,
                                  fk_confirmed=fk_confirmed, period=period)


class BackfillVerdictTests(unittest.TestCase):
    """Владелец 23.11.2026: супер-админ вносит приветственный блок задним числом,
    «если офисник забыл нажать кнопку, а водитель уже выполняет заказы». Снято
    одно условие — «ни одного заказа»; остальные — как у стойки."""

    def test_driver_with_orders_can_get_the_forgotten_welcome_block(self):
        drv = driver(total=8, week=8, tariffs=('business', 'econom'))
        self.assertFalse(evaluate(drv)['kind'] == 'welcome')   # у стойки — уже не новый
        verdict = backfill(drv, moment=NOW, fk_confirmed=True)
        self.assertTrue(verdict['allowed'], verdict['reasons'])
        self.assertEqual((verdict['kind'], verdict['blocks_max'], verdict['trips'], verdict['trips_basis']),
                         ('welcome', 1, 0, rules.BASIS_NEW))
        self.assertEqual(verdict['fk'], rules.FK_CONFIRMED)

    def test_welcome_is_still_once_per_person(self):
        verdict = backfill(driver(total=8), [issue('welcome', days_ago=3)], moment=NOW, fk_confirmed=True)
        self.assertFalse(verdict['allowed'])
        self.assertEqual(verdict['reasons'], ['Приветственный блок уже выдан 27.09.2026 в офисе «Офис Алматы №1»'])

    def test_still_only_on_program_tariffs(self):
        verdict = backfill(driver(total=8, tariffs=('econom', 'comfort')), moment=NOW, fk_confirmed=True)
        self.assertFalse(verdict['allowed'])
        self.assertIn('Тариф не входит в программу', verdict['reasons'][0])

    def test_fk_rules_are_those_of_the_counter(self):
        drv = driver(total=8)
        self.assertTrue(backfill(drv)['allowed'])          # предпросмотр: отметка ещё впереди
        self.assertEqual(backfill(drv)['fk'], rules.FK_UNKNOWN)
        self.assertEqual(backfill(drv, moment=NOW, fk_confirmed=False)['reasons'],
                         ['Подтвердите, что водитель прошёл ФК'])
        passed = dict(drv, photo_control={'status': 'passed'})
        self.assertTrue(backfill(passed, moment=NOW, fk_confirmed=False)['allowed'])
        failed = dict(drv, photo_control={'status': 'rejected'})
        self.assertEqual(backfill(failed, moment=NOW, fk_confirmed=True)['reasons'],
                         ['ФК не пройден — в CRM статус «rejected»'])

    def test_no_welcome_after_an_earlier_issue_on_this_account(self):
        """Этот аккаунт уже получал воду за активность — тогда он был не новым."""
        earlier = issue('activity', days_ago=5)
        verdict = backfill(driver(total=50), [earlier], moment=NOW, fk_confirmed=True,
                           prev=earlier, prev_account=earlier)
        self.assertEqual(verdict['reasons'], ['На этом аккаунте вода уже выдавалась 25.09.2026 — приветственный '
                                              'блок может быть только раньше первой выдачи'])

    def test_earlier_issue_on_another_account_does_not_stop_the_welcome(self):
        """Новый парк — новый аккаунт без заказов: у стойки приветственный ему
        положен, хотя на старом аккаунте человек получал воду за активность."""
        other = issue('activity', days_ago=40, account='z' * 32)
        verdict = backfill(driver(total=5), [other], moment=NOW, fk_confirmed=True, prev=other)
        self.assertTrue(verdict['allowed'], verdict['reasons'])

    def test_welcome_before_a_later_activity_block_is_fine(self):
        """Забыли нажать в понедельник, в субботу выдали за активность у стойки —
        понедельник вносится: вода тогда и правда ушла новому водителю."""
        history = [issue('activity', days_ago=1)]
        verdict = backfill(driver(total=50), history, moment=NOW - timedelta(days=5), fk_confirmed=True)
        self.assertTrue(verdict['allowed'], verdict['reasons'])

    def test_blocks_follow_the_welcome_setting(self):
        verdict = backfill(driver(total=8), moment=NOW, fk_confirmed=True, settings={'welcome_blocks': 3})
        self.assertEqual(verdict['blocks_max'], 3)


WINDOW = NOW - timedelta(days=7)   # начало списка CRM «7 дней»: 23.09 11:00


def in_window(count, start=None, end=datetime(2026, 9, 29, 12, 0)):
    """count заказов между start и end, но не раньше начала списка CRM: в
    настоящем ответе более ранних заказов нет."""
    return orders_between(max(start or WINDOW, WINDOW), end, count)


def complete_driver(times, total=None, first=None, **kwargs):
    """Водитель со списком «7 дней»; first — его первый заказ в CRM. По
    умолчанию первый заказ — самый ранний в списке: в списке все заказы."""
    drv = driver(total=len(times) if total is None else total, week=len(times), times=times, **kwargs)
    drv['orders']['first_order_at'] = _crm_time(first or min(times))
    return drv


class BackfillActivityTests(unittest.TestCase):
    """«Да, сделай» (23.11.2026) — тот же внос задним числом для блока за
    активность. Условия стойки — на момент выдачи: тариф, дни с прошлой
    выдачи, поездки. Запрет — только доказанный: поездок меньше при полном
    списке заказов."""

    MOMENT = datetime(2026, 9, 29, 12, 0)

    def test_needs_the_moment_first(self):
        verdict = backfill(driver(total=50), kind='activity')
        self.assertFalse(verdict['allowed'])
        self.assertTrue(verdict['needs_moment'])
        self.assertEqual(verdict['reasons'], [])

    def test_trips_since_the_previous_issue_up_to_the_moment(self):
        prev = issue('activity', days_ago=8)            # 22.09 12:00 — раньше начала списка
        times = in_window(22) + orders_between(self.MOMENT, NOW, 30)
        # Первый заказ позже начала списка — в списке все заказы: число точное.
        verdict = backfill(complete_driver(times), [prev], moment=self.MOMENT, kind='activity', prev=prev)
        self.assertTrue(verdict['allowed'], verdict['reasons'])
        self.assertEqual((verdict['trips'], verdict['trips_basis'], verdict['trips_exact']),
                         (22, rules.BASIS_SINCE_LAST, True))
        # Заказы были и раньше окна — то же число уже только «не меньше».
        older = complete_driver(times, first=datetime(2025, 1, 1, 9, 0))
        verdict = backfill(older, [prev], moment=self.MOMENT, kind='activity', prev=prev)
        self.assertTrue(verdict['allowed'], verdict['reasons'])
        self.assertEqual((verdict['trips'], verdict['trips_exact']), (22, False))

    def test_proven_shortage_of_trips_is_refused(self):
        prev = issue('activity', days_ago=8)
        times = in_window(12) + orders_between(self.MOMENT, NOW, 30)
        verdict = backfill(complete_driver(times), [prev], moment=self.MOMENT, kind='activity', prev=prev)
        self.assertEqual(verdict['reasons'], ['Менее 20 поездок с прошлой выдачи до этой — было 12'])
        # Заказы после выдачи не засчитываются ей задним числом.
        self.assertEqual(verdict['trips'], 12)

    def test_unknown_trips_are_not_a_refusal_but_a_lower_bound(self):
        """Прошлая выдача старше окна CRM: заказов до начала списка не видно —
        «не меньше N», а не отказ."""
        prev = issue('activity', days_ago=12)
        times = orders_between(NOW - timedelta(days=7), self.MOMENT, 5)
        drv = driver(total=900, week=5, times=times)
        verdict = backfill(drv, [prev], moment=self.MOMENT, kind='activity', prev=prev)
        self.assertTrue(verdict['allowed'], verdict['reasons'])
        self.assertEqual((verdict['trips'], verdict['trips_exact']), (5, False))
        self.assertEqual(verdict['trips_from'], '2026-09-23T11:00:00')

    def test_without_a_previous_issue_the_week_before_the_moment_counts(self):
        verdict = backfill(complete_driver(in_window(25)), moment=self.MOMENT, kind='activity')
        self.assertTrue(verdict['allowed'], verdict['reasons'])
        self.assertEqual((verdict['trips'], verdict['trips_basis'], verdict['trips_exact']),
                         (25, rules.BASIS_WEEK, True))
        few = complete_driver(in_window(6, start=self.MOMENT - timedelta(days=3)))
        self.assertEqual(backfill(few, moment=self.MOMENT, kind='activity')['reasons'],
                         ['Менее 20 поездок за 7 дней до этой выдачи — было 6'])
        # Заказ ровно за 7 суток до выдачи — ещё в счёт, раньше — уже нет.
        edge = complete_driver([self.MOMENT - timedelta(days=7), self.MOMENT - timedelta(days=7, seconds=1)],
                               first=self.MOMENT - timedelta(days=8))
        edge['orders']['window_from'] = _crm_time(self.MOMENT - timedelta(days=8))
        self.assertEqual(backfill(edge, moment=self.MOMENT, kind='activity')['trips'], 1)

    def test_days_since_the_previous_issue_are_checked_at_the_moment(self):
        prev = issue('activity', days_ago=4)             # 26.09 → к 29.09 прошло 3 дня
        times = in_window(30, start=prev['created_at'])
        verdict = backfill(complete_driver(times), [prev], moment=self.MOMENT, kind='activity', prev=prev)
        self.assertIn('К этой выдаче с прошлой (26.09.2026) прошло 3 дня из 7', verdict['reasons'])
        one = issue('activity', days_ago=2)               # 28.09 → прошёл 1 день
        verdict = backfill(complete_driver(times), [one], moment=self.MOMENT, kind='activity', prev=one)
        self.assertIn('К этой выдаче с прошлой (28.09.2026) прошёл 1 день из 7', verdict['reasons'])

    def test_activity_needs_no_fk_and_follows_its_blocks_setting(self):
        times = in_window(25)
        verdict = backfill(complete_driver(times), moment=self.MOMENT, kind='activity', fk_confirmed=False,
                           settings={'activity_blocks': 2})
        self.assertTrue(verdict['allowed'], verdict['reasons'])
        self.assertEqual((verdict['fk'], verdict['blocks_max']), (None, 2))

    def test_activity_is_on_program_tariffs_only(self):
        times = in_window(25)
        drv = complete_driver(times, tariffs=('econom',))
        self.assertIn('Тариф не входит в программу', backfill(drv, moment=self.MOMENT, kind='activity')['reasons'][0])


class BackfillPeriodTests(unittest.TestCase):
    """Поездки блока за активность, которых нет в списке CRM «7 дней»: блок
    выдают не чаще раза в неделю, и отрезок «с прошлой выдачи до этой» почти
    всегда старше списка. Его досчитывает CRM за даты — как у стойки."""

    MOMENT = datetime(2026, 9, 29, 12, 0)      # окно списка — с 23.09 11:00

    def prev(self, days_ago=10, account=ACCOUNT, orders_today=3):
        return issue('activity', days_ago=days_ago, account=account, orders_today=orders_today)

    def driver_with_week(self, before_moment=5, after_moment=2, first=datetime(2024, 1, 1)):
        """Список: before_moment заказов в окне до момента, after_moment — в день
        момента после него (12:00 → 13:00, 14:00 …)."""
        times = in_window(before_moment) + [self.MOMENT + timedelta(hours=n + 1) for n in range(after_moment)]
        return complete_driver(times, first=first)

    def verdict(self, drv, prev, period=None):
        return backfill(drv, [prev] if prev else [], moment=self.MOMENT, kind='activity', prev=prev,
                        period=period)

    def test_request_spans_the_two_issue_days_minus_what_is_known_at_both_ends(self):
        prev = self.prev()                         # 20.09 12:00, в снимке 3 заказа того дня до неё
        request = rules.backfill_period_request(self.driver_with_week(), SETTINGS, moment=self.MOMENT, prev=prev)
        self.assertEqual(request, {'issue_id': 1, 'date_from': '2026-09-20', 'date_to': '2026-09-29',
                                   'subtract': 3 + 2, 'bound': 'exact'})

    def test_exact_answer_decides_both_ways(self):
        prev = self.prev()
        drv = self.driver_with_week()
        request = rules.backfill_period_request(drv, SETTINGS, moment=self.MOMENT, prev=prev)
        short = self.verdict(drv, prev, dict(request, count=3 + 2 + 14))
        self.assertEqual(short['reasons'], ['Менее 20 поездок с прошлой выдачи до этой — было 14'])
        self.assertEqual((short['trips'], short['trips_exact']), (14, True))
        enough = self.verdict(drv, prev, dict(request, count=3 + 2 + 26))
        self.assertTrue(enough['allowed'], enough['reasons'])
        self.assertEqual((enough['trips'], enough['trips_exact']), (26, True))

    def test_without_an_answer_it_is_a_lower_bound_not_a_refusal(self):
        prev = self.prev()
        drv = self.driver_with_week()
        request = rules.backfill_period_request(drv, SETTINGS, moment=self.MOMENT, prev=prev)
        for period in (None, dict(request, count=None), dict(request, count=4),   # 4 < вычета: CRM противоречит себе
                       dict(request, date_from='2026-09-21', count=40)):          # ответ на другой запрос
            verdict = self.verdict(drv, prev, period)
            self.assertTrue(verdict['allowed'], (period, verdict['reasons']))
            self.assertEqual((verdict['trips'], verdict['trips_exact']), (5, False))

    def test_other_account_or_no_snapshot_gives_an_upper_bound(self):
        """Заказов дня прошлой выдачи до неё не знаем (другой аккаунт, снимок без
        today): день идёт целиком — число «не больше». Меньше порога — отказ
        доказан; больше — не доказано ни то ни другое: «не меньше» по списку."""
        for prev in (self.prev(account='z' * 32), self.prev(orders_today=None)):
            drv = self.driver_with_week()
            request = rules.backfill_period_request(drv, SETTINGS, moment=self.MOMENT, prev=prev)
            self.assertEqual(request, {'issue_id': 1, 'date_from': '2026-09-20', 'date_to': '2026-09-29',
                                       'subtract': 2, 'bound': 'upper'})
            refused = self.verdict(drv, prev, dict(request, count=2 + 12))
            self.assertEqual(refused['reasons'], ['Менее 20 поездок с прошлой выдачи до этой — было не больше 12'])
            unknown = self.verdict(drv, prev, dict(request, count=2 + 30))
            self.assertTrue(unknown['allowed'], unknown['reasons'])
            self.assertEqual((unknown['trips'], unknown['trips_exact'], unknown['trips_upper']), (5, False, 30))

    def test_moment_older_than_the_list_counts_its_whole_day(self):
        """Момент раньше начала списка — заказов его дня после него не знаем:
        день идёт целиком, вычитается только известное — «не больше»."""
        prev = self.prev(days_ago=20)                  # 10.09 12:00, в снимке 3 заказа до неё
        old_moment = datetime(2026, 9, 20, 12, 0)
        request = rules.backfill_period_request(self.driver_with_week(), SETTINGS, moment=old_moment, prev=prev)
        self.assertEqual(request, {'issue_id': 1, 'date_from': '2026-09-10', 'date_to': '2026-09-20',
                                   'subtract': 3, 'bound': 'upper'})
        # Без прошлой выдачи — тоже «не больше» за целые дни недели до момента.
        week = rules.backfill_period_request(self.driver_with_week(), SETTINGS, moment=old_moment, prev=None)
        self.assertEqual(week, {'issue_id': None, 'date_from': '2026-09-13', 'date_to': '2026-09-20',
                                'subtract': 0, 'bound': 'upper'})
        refused = backfill(self.driver_with_week(), moment=old_moment, kind='activity', period=dict(week, count=2))
        self.assertEqual(refused['reasons'], ['Менее 20 поездок за 7 дней до этой выдачи — было не больше 2'])

    def test_without_a_previous_issue_the_dates_give_an_upper_bound(self):
        """Без прошлой выдачи «7 суток до выдачи» по датам не отрезать: первый
        день идёт целиком — число «не больше». Меньше порога — доказанный отказ."""
        drv = self.driver_with_week()
        request = rules.backfill_period_request(drv, SETTINGS, moment=self.MOMENT, prev=None)
        self.assertEqual(request, {'issue_id': None, 'date_from': '2026-09-22', 'date_to': '2026-09-29',
                                   'subtract': 2, 'bound': 'upper'})
        refused = self.verdict(drv, None, dict(request, count=2 + 11))
        self.assertEqual(refused['reasons'], ['Менее 20 поездок за 7 дней до этой выдачи — было не больше 11'])
        unknown = self.verdict(drv, None, dict(request, count=2 + 30))
        self.assertTrue(unknown['allowed'], unknown['reasons'])
        self.assertEqual((unknown['trips'], unknown['trips_exact'], unknown['trips_upper']), (5, False, 30))

    def test_exact_number_is_asked_even_when_the_list_already_has_the_threshold(self):
        """Внос задним числом редок: точное число в колонку «Заказы» журнала
        дороже одного запроса. А вот «не меньше» или «не больше» при пороге,
        уже набранном по списку, ничего не добавят — их не спрашиваем."""
        prev = self.prev()
        busy = self.driver_with_week(before_moment=21)
        self.assertEqual(rules.backfill_period_request(busy, SETTINGS, moment=self.MOMENT, prev=prev)['bound'],
                         'exact')
        self.assertIsNone(rules.backfill_period_request(busy, SETTINGS, moment=self.MOMENT,
                                                        prev=self.prev(account='z' * 32)))
        self.assertIsNone(rules.backfill_period_request(busy, SETTINGS, moment=self.MOMENT, prev=None))
        # С правилом поездок выключенным — тоже: число нужно журналу, не отказу.
        self.assertEqual(rules.backfill_period_request(self.driver_with_week(), dict(SETTINGS, min_trips=0),
                                                       moment=self.MOMENT, prev=prev)['bound'], 'exact')
        self.assertIsNone(rules.backfill_period_request(self.driver_with_week(), dict(SETTINGS, min_trips=0),
                                                        moment=self.MOMENT, prev=None))

    def test_no_request_when_the_list_is_exact_or_there_is_no_moment(self):
        prev = self.prev()
        # Новый водитель — в списке все заказы.
        self.assertIsNone(rules.backfill_period_request(self.driver_with_week(first=WINDOW), SETTINGS,
                                                        moment=self.MOMENT, prev=prev))
        self.assertIsNone(rules.backfill_period_request(self.driver_with_week(), SETTINGS, moment=None, prev=prev))


class OrdersAtTests(unittest.TestCase):
    """Заказы на момент выдачи задним числом — для снимка, от которого следующая
    выдача считает «поездки с прошлой»."""

    def test_orders_of_the_issue_day_before_it_and_total_then(self):
        moment = datetime(2026, 9, 29, 12, 0)
        times = [datetime(2026, 9, 28, 18, 0), datetime(2026, 9, 29, 10, 3), datetime(2026, 9, 29, 10, 49),
                 datetime(2026, 9, 29, 12, 0), datetime(2026, 9, 30, 9, 0)]
        drv = driver(total=40, week=5, times=times)
        # До 12:00 29.09 — два заказа того дня; ровно в 12:00 — уже не «до».
        self.assertEqual(rules.orders_at(drv, moment), (2, 38))

    def test_issue_before_the_window_is_no_data(self):
        drv = driver(total=40, week=5, times=[NOW - timedelta(hours=1)])
        self.assertEqual(rules.orders_at(drv, NOW - timedelta(days=9)), (None, None))

    def test_day_start_outside_the_window_keeps_total_but_not_today(self):
        """Окно начинается в 11:00 23.09: выдача 23.09 в 15:00 — заказы того дня
        до 11:00 в списке не видны, `today` не выдумываем."""
        drv = driver(total=40, week=1, times=[datetime(2026, 9, 23, 16, 0)])
        self.assertEqual(rules.orders_at(drv, datetime(2026, 9, 23, 15, 0)), (None, 39))

    def test_old_crm_answer_without_the_list_is_no_data(self):
        self.assertEqual(rules.orders_at(driver(total=40, week=5), NOW), (None, None))

    def test_new_driver_list_is_complete_even_before_the_window(self):
        """Первый заказ позже начала окна — до окна заказов не было: день выдачи
        за границей окна всё равно считается точно."""
        drv = driver(total=1, week=1, times=[datetime(2026, 9, 23, 16, 0)])
        drv['orders']['first_order_at'] = '2026-09-23 16:00:00'
        # До 15:00 заказов не было: ни в тот день, ни всего.
        self.assertEqual(rules.orders_at(drv, datetime(2026, 9, 23, 15, 0)), (0, 0))

    def test_truncated_list_counts_only_from_its_oldest_order(self):
        times = [datetime(2026, 9, 29, 9, 0), datetime(2026, 9, 29, 11, 0)]
        drv = driver(total=900, week=500, times=times, truncated=True)
        self.assertEqual(rules.orders_at(drv, datetime(2026, 9, 29, 10, 0)), (None, 899))


if __name__ == '__main__':
    unittest.main()
