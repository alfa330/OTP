# -*- coding: utf-8 -*-
"""Очередь суток раздела «Касания»: что переспрашиваем у моста и что нет.

Правила очереди — единственное, что стоит между «человек нажал обновить» и
«станцию долбят по кругу». Здесь закрыты решения, которые ломаются молча:

  * закрытые сутки (`done` + `complete`) больше не перезапрашиваются никогда —
    иначе каждый вход в раздел заново качал бы весь месяц;
  * незакрытые сутки (`done`, но `complete = false`) перезапрашиваются всегда:
    станция дописала в них звонки после нашего чтения;
  * сутки в будущем не запрашиваются вовсе — станции нечего о них рассказать, а
    строка «ошибка» на завтрашней дате читается как поломка раздела;
  * задание, взятое мостом, не отбирается, пока не протухло, — иначе два моста
    читали бы одни сутки одновременно;
  * отметка времени из Postgres приходит С ЗОНОЙ, а сравнивается с utcnow():
    вычитание осведомлённого времени из наивного бросает TypeError и роняет всю
    очередь целиком.

Ни базы, ни сети: `cdr.sync` — чистый модуль.
"""

import unittest
from datetime import date, datetime, timedelta, timezone

from cdr import sync

TODAY = date(2026, 8, 25)
NOW = datetime(2026, 8, 25, 12, 0, 0)


def state(day, status='done', complete=True, claimed_at=None, attempts=1):
    return {'day': day, 'status': status, 'complete': complete,
            'claimed_at': claimed_at, 'rows_fetched': 0, 'touches': 0,
            'finished_at': None, 'attempts': attempts, 'error': None}


def needing(states, day_from, day_to):
    return [d.isoformat() for d in
            sync.days_needing_sync(states, day_from, day_to, TODAY, NOW)]


def awaiting(states, day_from, day_to):
    return [d.isoformat() for d in
            sync.days_awaiting(states, day_from, day_to, TODAY)]


class PeriodTests(unittest.TestCase):
    def test_period_includes_both_ends(self):
        days = sync.days_in_period(date(2026, 8, 23), date(2026, 8, 25))
        self.assertEqual([d.isoformat() for d in days],
                         ['2026-08-23', '2026-08-24', '2026-08-25'])

    def test_bad_date_says_what_it_expected(self):
        with self.assertRaises(ValueError) as caught:
            sync.parse_day('25.08.2026', 'дату начала')
        self.assertIn('ГГГГ-ММ-ДД', str(caught.exception))
        self.assertIn('дату начала', str(caught.exception))


class QueueTests(unittest.TestCase):
    def test_unknown_days_are_requested(self):
        self.assertEqual(needing([], date(2026, 8, 23), date(2026, 8, 24)),
                         ['2026-08-23', '2026-08-24'])

    def test_closed_days_are_never_requested_again(self):
        states = [state('2026-08-23'), state('2026-08-24')]
        self.assertEqual(needing(states, date(2026, 8, 23), date(2026, 8, 24)), [])

    def test_unfinished_day_is_requested_again(self):
        """Сутки читались, пока ещё не кончились — станция дописала в них звонки."""
        states = [state('2026-08-24', complete=False)]
        self.assertEqual(needing(states, date(2026, 8, 24), date(2026, 8, 24)),
                         ['2026-08-24'])

    def test_today_is_always_requested_again(self):
        states = [state('2026-08-25', complete=False)]
        self.assertEqual(needing(states, TODAY, TODAY), ['2026-08-25'])

    def test_future_days_are_not_requested(self):
        self.assertEqual(needing([], TODAY, date(2026, 8, 28)), ['2026-08-25'])

    def test_error_is_retried(self):
        states = [state('2026-08-24', status='error', complete=False)]
        self.assertEqual(needing(states, date(2026, 8, 24), date(2026, 8, 24)),
                         ['2026-08-24'])

    def test_fresh_claim_is_left_alone(self):
        claimed = (NOW - timedelta(minutes=2)).isoformat()
        states = [state('2026-08-24', status='running', complete=False,
                        claimed_at=claimed)]
        self.assertEqual(needing(states, date(2026, 8, 24), date(2026, 8, 24)), [],
                         'мост занят этими сутками прямо сейчас')

    def test_abandoned_claim_is_taken_back(self):
        claimed = (NOW - timedelta(minutes=sync.STALE_MINUTES + 1)).isoformat()
        states = [state('2026-08-24', status='running', complete=False,
                        claimed_at=claimed)]
        self.assertEqual(needing(states, date(2026, 8, 24), date(2026, 8, 24)),
                         ['2026-08-24'])

    def test_running_without_a_timestamp_is_abandoned(self):
        """Строка `running` без времени взятия пережила перезапуск до того, как
        отметку успели поставить. Считаем брошенной — иначе она зависнет навсегда."""
        states = [state('2026-08-24', status='running', complete=False)]
        self.assertEqual(needing(states, date(2026, 8, 24), date(2026, 8, 24)),
                         ['2026-08-24'])


class AttemptCapTests(unittest.TestCase):
    """Без потолка попыток портал и мост зацикливаются: раздел опрашивается раз в
    три секунды, каждый опрос возвращает упавшие сутки в очередь, мост снова идёт
    на станцию и снова падает — и так пока открыта вкладка."""

    def test_error_is_retried_while_attempts_remain(self):
        states = [state('2026-08-24', status='error', complete=False, attempts=2)]
        self.assertEqual(needing(states, date(2026, 8, 24), date(2026, 8, 24)),
                         ['2026-08-24'])

    def test_stuck_running_is_also_capped(self):
        """Мост, который берёт сутки и не закрывает их (портал отверг тело),
        иначе читал бы станцию по кругу вечно и молча: статус `error` в этом
        сценарии никто не выставляет."""
        claimed = (NOW - timedelta(minutes=sync.STALE_MINUTES + 1)).isoformat()
        states = [state('2026-08-24', status='running', complete=False,
                        claimed_at=claimed, attempts=sync.MAX_ATTEMPTS)]
        day = date(2026, 8, 24)
        self.assertEqual(needing(states, day, day), [])
        self.assertEqual(awaiting(states, day, day), [])

    def test_exhausted_error_stops_being_requested(self):
        states = [state('2026-08-24', status='error', complete=False,
                        attempts=sync.MAX_ATTEMPTS)]
        self.assertEqual(needing(states, date(2026, 8, 24), date(2026, 8, 24)), [])

    def test_exhausted_error_stops_being_awaited(self):
        """Иначе полоса прогресса крутится вечно на сутках, которых на станции
        просто нет, — а список отказов человеку уже показан отдельно."""
        states = [state('2026-08-24', status='error', complete=False,
                        attempts=sync.MAX_ATTEMPTS)]
        self.assertEqual(awaiting(states, date(2026, 8, 24), date(2026, 8, 24)), [])


class AwaitingTests(unittest.TestCase):
    """«Чего ещё нет» — не то же самое, что «что переспросить»."""

    def test_day_in_work_is_awaited_but_not_requested(self):
        claimed = (NOW - timedelta(minutes=1)).isoformat()
        states = [state('2026-08-24', status='running', complete=False,
                        claimed_at=claimed)]
        day = date(2026, 8, 24)
        self.assertEqual(needing(states, day, day), [],
                         'мост уже читает эти сутки, переспрашивать нечего')
        self.assertEqual(awaiting(states, day, day), ['2026-08-24'],
                         'но готовыми они не стали — прогресс не 100%')

    def test_closed_day_is_not_awaited(self):
        states = [state('2026-08-24')]
        self.assertEqual(awaiting(states, date(2026, 8, 24), date(2026, 8, 24)), [])

    def test_today_is_settled_once_the_data_arrived(self):
        """Сегодняшние сутки НИКОГДА не получают complete (станция дописывает в
        них звонки до полуночи). Если ждать complete, ожидание не кончится: период
        по умолчанию включает сегодня, и полоса прогресса крутилась бы вечно, а
        кнопка «обновить» оставалась бы заблокированной."""
        states = [state('2026-08-25', complete=False)]
        self.assertEqual(awaiting(states, TODAY, TODAY), [])
        self.assertEqual(needing(states, TODAY, TODAY), ['2026-08-25'],
                         'но перечитывать их всё равно надо')

    def test_past_day_without_complete_is_still_awaited(self):
        states = [state('2026-08-24', complete=False)]
        self.assertEqual(awaiting(states, date(2026, 8, 24), date(2026, 8, 24)),
                         ['2026-08-24'])

    def test_future_days_are_never_awaited(self):
        self.assertEqual(awaiting([], TODAY, date(2026, 8, 28)), ['2026-08-25'])


class TimestampTests(unittest.TestCase):
    """Postgres отдаёт TIMESTAMPTZ с зоной. Сравнение с наивным utcnow() без
    приведения бросает TypeError и роняет очередь целиком."""

    def test_aware_timestamp_does_not_explode(self):
        aware = datetime(2026, 8, 25, 11, 58, tzinfo=timezone.utc)
        self.assertFalse(sync.is_stale(aware, NOW))

    def test_aware_timestamp_with_offset_is_converted(self):
        # 17:00+05:00 — это 12:00 UTC, то есть «прямо сейчас», а не пять часов назад.
        aware = datetime(2026, 8, 25, 17, 0, tzinfo=timezone(timedelta(hours=5)))
        self.assertFalse(sync.is_stale(aware, NOW))

    def test_naive_timestamp_still_works(self):
        self.assertFalse(sync.is_stale(datetime(2026, 8, 25, 11, 58), NOW))

    def test_garbage_is_treated_as_abandoned(self):
        for value in (None, '', 'вчера', 123):
            self.assertTrue(sync.is_stale(value, NOW), repr(value))


class WindowTests(unittest.TestCase):
    """Часовой хвост: без него звонок, начатый в 23:59, распадается на два
    обрубка — часть плеч в одних сутках, часть в других."""

    def test_window_covers_the_day_plus_an_hour(self):
        from_dt, to_dt = sync.window_for(date(2026, 8, 24))
        self.assertEqual(from_dt, '2026-08-24T00:00:00')
        self.assertEqual(to_dt, '2026-08-25T01:00:00')

    def test_tail_is_an_hour_and_this_is_deliberate(self):
        self.assertEqual(sync.TAIL_HOURS, 1)


class LimitTests(unittest.TestCase):
    def test_period_cap_is_a_quarter(self):
        """92 суток — это примерно четверть часа работы моста. Дальше человек
        уже не ждёт у экрана."""
        self.assertEqual(sync.MAX_PERIOD_DAYS, 92)


if __name__ == '__main__':
    unittest.main()
