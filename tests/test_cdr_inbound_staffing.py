# -*- coding: utf-8 -*-
"""«CR%» и «ОСС» «Отчёта по входу» (cdr/inbound_staffing.py).

Закреплены правила владельца от 12.11.2026: план — смены из «Графиков работы», факт —
смена засчитана, если оператор был на линии не меньше 90 % положенного; ОСС — доля
времени на линии, занятая звонком. И решения, без которых цифра врала бы: перерывы по
графику не идут в знаменатель, чужая группа и незаконченная смена — не в счёт.
"""

import unittest
from datetime import date, datetime, time

from cdr import inbound_staffing as S, staffing_queries

DAY = date(2026, 11, 10)
NOW = datetime(2026, 11, 12, 12, 0, 0)


def at(clock, day=DAY):
    hours, minutes = clock.split(':')
    return datetime(day.year, day.month, day.day, int(hours), int(minutes))


def shift(operator_id, start='09:00', end='18:00', breaks=((780, 840),), day=DAY,
          shift_type='regular'):
    """Смена из графика: 09–18 с часовым обедом 13–14 — восемь часов на линии."""
    def clock(text):
        hours, minutes = text.split(':')
        return time(int(hours), int(minutes))
    return {'operator_id': operator_id, 'day': day, 'start_time': clock(start),
            'end_time': clock(end), 'shift_type': shift_type, 'breaks': list(breaks)}


def member(model='op_osnova', start=date(2026, 1, 1), end=None, member_id=1):
    return {'id': member_id, 'start_date': start, 'end_date': end, 'group_id': 36, 'model': model}


def full_day_events(logout='18:00'):
    """Обычный день: на линии с 09:00, разговор 09:10–09:40, обед 13–14, выход."""
    return [(at('09:00'), 'готов'), (at('09:10'), 'занят'), (at('09:40'), 'готов'),
            (at('13:00'), 'перерыв'), (at('14:00'), 'готов'), (at(logout), 'выключен')]


class OnlineIntervalsTests(unittest.TestCase):

    def test_on_line_is_waiting_dialing_and_talking(self):
        intervals = S.online_intervals(full_day_events())
        self.assertEqual(intervals, [
            (at('09:00'), at('09:10'), False),
            (at('09:10'), at('09:40'), True),
            (at('09:40'), at('13:00'), False),
            (at('14:00'), at('18:00'), False),
        ])

    def test_dialing_is_busy_and_pauses_are_not_on_line(self):
        events = [(at('09:00'), 'исход'), (at('09:05'), 'соединение'), (at('09:06'), 'занят'),
                  (at('09:10'), 'исход'), (at('10:00'), 'тренинг'), (at('11:00'), 'тех причина'),
                  (at('11:30'), 'исход'), (at('12:00'), 'выключен')]
        intervals = S.online_intervals(events)
        self.assertEqual([(start.strftime('%H:%M'), end.strftime('%H:%M'), busy)
                          for start, end, busy in intervals],
                         [('09:00', '09:05', False), ('09:05', '09:06', True),
                          ('09:06', '09:10', True), ('09:10', '10:00', False),
                          ('11:30', '12:00', False)])

    def test_overlap_is_clipped_to_the_window(self):
        intervals = S.online_intervals(full_day_events())
        self.assertEqual(S.overlap_seconds(intervals, at('09:00'), at('18:00')), 8 * 3600)
        self.assertEqual(S.overlap_seconds(intervals, at('12:30'), at('14:30')), 3600)
        self.assertEqual(S.overlap_seconds(intervals, at('09:00'), at('18:00'), busy_only=True), 1800)
        self.assertEqual(S.overlap_seconds(intervals, at('19:00'), at('20:00')), 0)
        self.assertEqual(S.overlap_seconds([], at('09:00'), at('18:00')), 0)


class ShiftTests(unittest.TestCase):

    def test_scheduled_breaks_are_not_planned_line_time(self):
        self.assertEqual(S.planned_seconds(shift(1)), 8 * 3600)
        self.assertEqual(S.planned_seconds(shift(1, breaks=())), 9 * 3600)
        # Перерыв, вылезающий за смену, режется её границами.
        self.assertEqual(S.planned_seconds(shift(1, breaks=((1050, 1110),))), 9 * 3600 - 1800)

    def test_night_shift_crosses_midnight(self):
        night = shift(1, start='20:00', end='08:00', breaks=((1560, 1590),))
        self.assertEqual(S.shift_bounds(night), (at('20:00'), datetime(2026, 11, 11, 8, 0)))
        self.assertEqual(S.planned_seconds(night), 12 * 3600 - 1800)
        # Тот же перерыв 02:00–02:30, записанный «по часам», — те же полчаса.
        self.assertEqual(S.planned_seconds(shift(1, start='20:00', end='08:00',
                                                 breaks=((120, 150),))), 12 * 3600 - 1800)

    def test_adherence_is_line_time_over_planned_time(self):
        self.assertEqual(S.shift_adherence(shift(1), S.online_intervals(full_day_events())), 1.0)
        # Ушёл в 16:00: шесть часов из восьми.
        left_early = S.online_intervals(full_day_events(logout='16:00'))
        self.assertAlmostEqual(S.shift_adherence(shift(1), left_early), 0.75)
        self.assertEqual(S.shift_adherence(shift(1), []), 0.0)

    def test_working_through_a_break_does_not_exceed_the_plan(self):
        # Весь день на линии, обед не брал: девять часов при восьми положенных.
        events = [(at('09:00'), 'готов'), (at('12:00'), 'занят'), (at('12:05'), 'готов'),
                  (at('15:00'), 'занят'), (at('15:05'), 'готов'), (at('18:00'), 'выключен')]
        intervals = S.online_intervals(events)
        self.assertEqual(S.overlap_seconds(intervals, at('09:00'), at('18:00')), 9 * 3600)
        self.assertEqual(S.shift_adherence(shift(1), intervals), 1.0)

    def test_phone_left_without_a_person_is_not_line_time(self):
        # Статус за всю смену никто не подтвердил ни звонком, ни сменой статуса: телефон
        # вернул его сам после сна компьютера. Правило «Активности операторов».
        events = [(at('09:00'), 'готов'), (at('18:00'), 'выключен')]
        self.assertEqual(S.online_intervals(events), [])
        self.assertEqual(S.shift_adherence(shift(1), S.online_intervals(events)), 0.0)


class DayStaffingTests(unittest.TestCase):

    def stats(self, shifts, events, memberships=None, now=NOW, days=(DAY,)):
        intervals = {operator_id: S.online_intervals(items) for operator_id, items in events.items()}
        if memberships is None:
            memberships = {operator_id: [member()]
                           for operator_id in set(events) | {s['operator_id'] for s in shifts}}
        return S.day_staffing(list(days), shifts=shifts, intervals_by_operator=intervals,
                              memberships=memberships, now=now)

    def test_cr_is_worked_shifts_over_scheduled_shifts(self):
        result = self.stats([shift(1), shift(2), shift(3)],
                            {1: full_day_events(), 2: full_day_events(logout='16:00')})[DAY]
        # Первый отработал смену, второй ушёл на два часа раньше, третий не вышел вовсе.
        self.assertEqual((result['shifts_plan'], result['shifts_fact']), (3, 1))
        self.assertAlmostEqual(result['cr'], 1 / 3)

    def test_every_planned_shift_is_listed_with_its_share(self):
        rows = self.stats([shift(2), shift(1), shift(3, start='08:00', end='17:00', breaks=())],
                          {1: full_day_events(), 2: full_day_events(logout='16:00')})[DAY]['shifts']
        # По началу смены, при равном — по оператору.
        self.assertEqual([row['operator_id'] for row in rows], [3, 1, 2])
        first, worked, left_early = rows
        self.assertEqual((first['start'], first['end']), (at('08:00'), at('17:00')))
        self.assertEqual((first['planned_seconds'], first['online_seconds'], first['adherence'],
                          first['counted']), (9 * 3600, 0, 0.0, False))
        self.assertEqual((worked['planned_seconds'], worked['online_seconds'], worked['adherence'],
                          worked['counted']), (8 * 3600, 8 * 3600, 1.0, True))
        self.assertEqual((left_early['online_seconds'], left_early['counted']), (6 * 3600, False))
        self.assertAlmostEqual(left_early['adherence'], 0.75)

    def test_ninety_percent_is_enough(self):
        # Опоздал на 45 минут: 7 ч 15 мин из 8 ч = 90,6 %.
        late = [(at('09:45'), 'готов'), (at('13:00'), 'перерыв'), (at('14:00'), 'готов'),
                (at('18:00'), 'выключен')]
        self.assertEqual(self.stats([shift(1)], {1: late})[DAY]['shifts_fact'], 1)
        # Опоздал на 50 минут: 89,6 % — уже нет.
        later = [(at('09:50'), 'готов')] + late[1:]
        self.assertEqual(self.stats([shift(1)], {1: later})[DAY]['shifts_fact'], 0)

    def test_occ_is_busy_time_over_line_time(self):
        result = self.stats([shift(1)], {1: full_day_events()})[DAY]
        self.assertEqual((result['online_seconds'], result['busy_seconds']), (8 * 3600, 1800))
        self.assertAlmostEqual(result['occ'], 1800 / (8 * 3600))

    def test_occ_counts_line_time_without_a_scheduled_shift(self):
        result = self.stats([], {1: full_day_events()})[DAY]
        self.assertIsNone(result['cr'])
        self.assertAlmostEqual(result['occ'], 1800 / (8 * 3600))

    def test_nothing_to_divide_by_is_blank_not_zero(self):
        result = self.stats([], {})[DAY]
        self.assertIsNone(result['cr'])
        self.assertIsNone(result['occ'])

    def test_other_group_on_that_day_is_not_the_line(self):
        memberships = {
            1: [member()],
            # Со 2 ноября переведён в «Поток»: смена 10.11 уже не «Основы».
            2: [member(end=date(2026, 11, 1)), member('op_potok', date(2026, 11, 2), member_id=2)],
        }
        result = self.stats([shift(1), shift(2)], {1: full_day_events(), 2: full_day_events()},
                            memberships)[DAY]
        self.assertEqual((result['shifts_plan'], result['shifts_fact']), (1, 1))
        self.assertEqual(result['online_seconds'], 8 * 3600)

    def test_only_regular_shifts_are_planned_for_the_line(self):
        shifts = [shift(1), shift(2, shift_type='office_practice'), shift(3, shift_type='phone_shift')]
        result = self.stats(shifts, {1: full_day_events()})[DAY]
        self.assertEqual((result['shifts_plan'], result['shifts_fact']), (1, 1))

    def test_unfinished_shift_is_neither_plan_nor_fact(self):
        result = self.stats([shift(1), shift(2)], {1: full_day_events()}, now=at('15:00'))[DAY]
        self.assertEqual(result['shifts_plan'], 0)
        self.assertIsNone(result['cr'])

    def test_night_shift_belongs_to_the_day_it_starts(self):
        next_day = date(2026, 11, 11)
        events = [(at('20:00'), 'готов'), (at('23:30'), 'занят'), (at('23:40'), 'готов'),
                  (at('03:00', next_day), 'занят'), (at('03:10', next_day), 'готов'),
                  (at('08:00', next_day), 'выключен')]
        result = self.stats([shift(1, start='20:00', end='08:00', breaks=())], {1: events},
                            days=(DAY, next_day))
        self.assertEqual((result[DAY]['shifts_plan'], result[DAY]['shifts_fact']), (1, 1))
        self.assertEqual(result[next_day]['shifts_plan'], 0)
        # Занятость — по календарным суткам: четыре часа вечером, восемь после полуночи.
        self.assertEqual(result[DAY]['online_seconds'], 4 * 3600)
        self.assertEqual(result[next_day]['online_seconds'], 8 * 3600)
        self.assertEqual((result[DAY]['busy_seconds'], result[next_day]['busy_seconds']), (600, 600))

    def test_days_outside_the_request_are_ignored(self):
        result = self.stats([shift(1, day=date(2026, 11, 9))], {1: full_day_events()})
        self.assertEqual(list(result), [DAY])
        self.assertEqual(result[DAY]['shifts_plan'], 0)


class Recorder:
    def __init__(self, rows=()):
        self.calls = []
        self.rows = list(rows)

    def execute(self, sql, params=None):
        self.calls.append((' '.join(sql.split()), params))

    def fetchall(self):
        return self.rows


class QueryTests(unittest.TestCase):

    def test_roster_is_the_group_model_in_the_period(self):
        cursor = Recorder([(7,), (3,)])
        self.assertEqual(staffing_queries.roster(cursor, 'op_osnova', DAY, DAY), [3, 7])
        sql, params = cursor.calls[0]
        self.assertIn("LOWER(COALESCE(g.calculation_model_code, '')) = %s", sql)
        self.assertEqual(params, ('op_osnova', DAY, DAY))

    def test_shifts_come_with_their_breaks(self):
        row = (5, DAY, time(9, 0), time(18, 0), 'regular', [780, 900], [840, 915])
        shifts = staffing_queries.scheduled_shifts(Recorder([row]), [5], DAY, DAY)
        self.assertEqual(shifts, [{'operator_id': 5, 'day': DAY, 'start_time': time(9, 0),
                                   'end_time': time(18, 0), 'shift_type': 'regular',
                                   'breaks': [(780, 840), (900, 915)]}])
        self.assertEqual(S.planned_seconds(shifts[0]), 9 * 3600 - 3600 - 900)

    def test_only_live_phone_events(self):
        cursor = Recorder([(5, at('09:00'), 'готов'), (5, at('09:10'), 'занят')])
        events = staffing_queries.phone_events(cursor, [5], at('00:00'), at('23:59'))
        self.assertEqual(events, {5: [(at('09:00'), 'готов'), (at('09:10'), 'занят')]})
        self.assertIn('client_event_id IS NOT NULL', cursor.calls[0][0])

    def test_human_calls_are_outgoing_and_answered_incoming_without_test_numbers(self):
        from cdr import touches as T
        from test_numbers import keys as test_keys
        cursor = Recorder([('6101', at('09:10'))])
        moments = staffing_queries.human_call_moments(cursor, ['6101', None], DAY, DAY)
        self.assertEqual(moments, {'6101': [at('09:10')]})
        sql, params = cursor.calls[0]
        self.assertEqual(params, (DAY, DAY, ['6101'], T.TYPE_OUT, T.TYPE_IN))
        self.assertIn(' '.join(test_keys.sql_not_test('cdr_touches.phone', digits=True).split()), sql)
        # Без номеров в базу не ходим.
        idle = Recorder()
        self.assertEqual(staffing_queries.human_call_moments(idle, [], DAY, DAY), {})
        self.assertEqual(idle.calls, [])


if __name__ == '__main__':
    unittest.main()
