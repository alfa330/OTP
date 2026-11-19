# -*- coding: utf-8 -*-
"""Табель фронт-офисов: правила дня, итоги, норма и график (front_office_timesheet/rules.py).

ТЗ «выгрузка табеля» (06.11.2026), образец — лист «табель». Люди выдуманы.
"""

import unittest
from datetime import date, datetime, time, timedelta

from front_office_timesheet import rules
from group_late.config import TZ


def dt(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute)).replace(tzinfo=TZ)


def fact(day, arrive=None, leave=None, extra_marks=()):
    marks = []
    if arrive:
        marks.append({'at': dt(day, *arrive).isoformat(), 'kind': 'in'})
    if leave:
        marks.append({'at': dt(day, *leave).isoformat(), 'kind': 'out'})
    marks.extend({'at': dt(day, *at).isoformat(), 'kind': kind} for at, kind in extra_marks)
    return rules.merge_facts([{
        'fact_in': dt(day, *arrive).isoformat() if arrive else None,
        'fact_out': dt(day, *leave).isoformat() if leave else None,
        'marks': marks,
    }])


DAY = date(2026, 10, 14)
SPAN = (dt(DAY, 9), dt(DAY, 19))
UNTIL = date(2026, 10, 31)


class DayCellTests(unittest.TestCase):
    def test_arrival_makes_a_work_day_with_late_minutes_of_the_screen(self):
        cell = rules.day_cell(DAY, fact_until=UNTIL, fact=fact(DAY, (9, 7), (19, 0)), span=SPAN, has_card=True)
        self.assertEqual(cell, {'code': 'р', 'late': 7, 'early': 0, 'incomplete': False})

    def test_seconds_round_down_like_the_attendance_section(self):
        arrive = dt(DAY, 9, 0) + timedelta(seconds=59)
        merged = rules.merge_facts([{'fact_in': arrive.isoformat(), 'fact_out': dt(DAY, 19).isoformat(),
                                     'marks': [{'at': arrive.isoformat(), 'kind': 'in'},
                                               {'at': dt(DAY, 19).isoformat(), 'kind': 'out'}]}])
        self.assertEqual(rules.day_cell(DAY, fact_until=UNTIL, fact=merged, span=SPAN)['late'], 0)

    def test_early_leave_counts_against_the_current_shift_end(self):
        cell = rules.day_cell(DAY, fact_until=UNTIL, fact=fact(DAY, (9, 0), (18, 15)), span=SPAN, has_card=True)
        self.assertEqual((cell['code'], cell['early'], cell['incomplete']), ('р', 45, False))

    def test_lunch_without_evening_mark_is_not_an_early_leave(self):
        """Вышел на обед, вернулся, вечером не отметился: «уход» был обедом (ТЗ п. 4)."""
        merged = fact(DAY, (9, 0), (13, 0), extra_marks=[((14, 0), 'in')])
        cell = rules.day_cell(DAY, fact_until=UNTIL, fact=merged, span=SPAN, has_card=True)
        self.assertEqual(cell, {'code': 'р', 'late': 0, 'early': 0, 'incomplete': True})

    def test_lunch_with_evening_mark_keeps_the_real_leave(self):
        merged = fact(DAY, (9, 0), (18, 30), extra_marks=[((13, 0), 'out'), ((14, 0), 'in')])
        cell = rules.day_cell(DAY, fact_until=UNTIL, fact=merged, span=SPAN, has_card=True)
        self.assertEqual((cell['early'], cell['incomplete']), (30, False))

    def test_no_exit_mark_is_incomplete_without_minutes(self):
        cell = rules.day_cell(DAY, fact_until=UNTIL, fact=fact(DAY, (9, 20)), span=SPAN, has_card=True)
        self.assertEqual(cell, {'code': 'р', 'late': 20, 'early': 0, 'incomplete': True})

    def test_exit_only_is_a_work_day_without_late(self):
        cell = rules.day_cell(DAY, fact_until=UNTIL, fact=fact(DAY, leave=(17, 0)), span=SPAN, has_card=True)
        self.assertEqual(cell, {'code': 'р', 'late': 0, 'early': 120, 'incomplete': True})

    def test_arrival_without_shift_is_a_plain_work_day(self):
        saturday = date(2026, 10, 17)
        merged = fact(saturday, (10, 0), (15, 0))
        cell = rules.day_cell(saturday, fact_until=UNTIL, fact=merged, span=None, day_off=True, has_card=True)
        self.assertEqual(cell, {'code': 'р', 'late': 0, 'early': 0, 'incomplete': False})

    def test_arrival_wins_over_a_period_that_kept_the_worked_shift(self):
        cell = rules.day_cell(DAY, fact_until=UNTIL, fact=fact(DAY, (9, 0), (19, 0)), span=SPAN,
                              period='bs', has_card=True)
        self.assertEqual(cell['code'], 'р')

    def test_periods_day_off_absence_and_blank(self):
        self.assertEqual(rules.day_cell(DAY, fact_until=UNTIL, period='bs')['code'], 'Б/С')
        self.assertEqual(rules.day_cell(DAY, fact_until=UNTIL, period='annual_leave')['code'], 'О')
        self.assertEqual(rules.day_cell(DAY, fact_until=UNTIL, period='sick_leave')['code'], 'Б')
        self.assertEqual(rules.day_cell(DAY, fact_until=UNTIL, period='dismissal', span=SPAN, has_card=True)['code'], '')
        self.assertEqual(rules.day_cell(DAY, fact_until=UNTIL, day_off=True)['code'], 'в')
        self.assertEqual(rules.day_cell(DAY, fact_until=UNTIL, span=SPAN, has_card=True)['code'], 'н')
        self.assertEqual(rules.day_cell(DAY, fact_until=UNTIL)['code'], '')

    def test_no_absence_without_a_workpace_card(self):
        """Без карточки Workpace отметок взять негде: неявка каждый день была бы неправдой."""
        self.assertEqual(rules.day_cell(DAY, fact_until=UNTIL, span=SPAN, has_card=False)['code'], '')

    def test_today_and_future_show_only_plan(self):
        until = DAY - timedelta(days=1)
        self.assertEqual(rules.day_cell(DAY, fact_until=until, fact=fact(DAY, (9, 0)), span=SPAN,
                                        has_card=True)['code'], '')
        self.assertEqual(rules.day_cell(DAY, fact_until=until, span=SPAN, has_card=True)['code'], '')
        self.assertEqual(rules.day_cell(DAY, fact_until=until, day_off=True)['code'], 'в')
        self.assertEqual(rules.day_cell(DAY, fact_until=until, period='bs')['code'], 'Б/С')

    def test_night_shift_leave_is_not_checked(self):
        span = rules.shift_span(DAY, time(21, 0), time(7, 0))
        self.assertEqual(span[1].date(), DAY + timedelta(days=1))
        cell = rules.day_cell(DAY, fact_until=UNTIL, fact=fact(DAY, (21, 5), (23, 0)), span=span, has_card=True)
        self.assertEqual((cell['late'], cell['early']), (5, 0))

    def test_last_fact_day_is_yesterday_or_month_end(self):
        self.assertEqual(rules.last_fact_day(date(2026, 11, 30), date(2026, 11, 6)), date(2026, 11, 5))
        self.assertEqual(rules.last_fact_day(date(2026, 10, 31), date(2026, 11, 6)), date(2026, 10, 31))


class MergeFactsTests(unittest.TestCase):
    def test_two_cards_give_one_day_not_doubled_minutes(self):
        """Две карточки Workpace — две строки «Отметок» за день; берётся ранний
        приход и поздний уход, а не сумма."""
        first = {'fact_in': dt(DAY, 9, 10).isoformat(), 'fact_out': None,
                 'marks': [{'at': dt(DAY, 9, 10).isoformat(), 'kind': 'in'}]}
        second = {'fact_in': None, 'fact_out': dt(DAY, 19, 2).isoformat(),
                  'marks': [{'at': dt(DAY, 19, 2).isoformat(), 'kind': 'out'}]}
        merged = rules.merge_facts([first, second])
        self.assertEqual(merged['fact_in'], dt(DAY, 9, 10))
        self.assertEqual(merged['fact_out'], dt(DAY, 19, 2))
        cell = rules.day_cell(DAY, fact_until=UNTIL, fact=merged, span=SPAN, has_card=True)
        self.assertEqual(cell, {'code': 'р', 'late': 10, 'early': 0, 'incomplete': False})

    def test_utc_marks_land_on_their_local_day(self):
        """Время в кэше — с поясом; приход 04:30 UTC — это 09:30 в Алматы."""
        merged = rules.merge_facts([{'fact_in': '2026-10-14T04:30:00+00:00', 'fact_out': None, 'marks': []}])
        self.assertEqual(merged['fact_in'], dt(DAY, 9, 30))

    def test_rows_without_marks_are_no_fact(self):
        self.assertIsNone(rules.merge_facts([{'fact_in': None, 'fact_out': None, 'marks': []}]))


class ScheduleAndNormTests(unittest.TestCase):
    def weekdays(self, start, end, holidays=()):
        return {start + timedelta(days=i) for i in range((end - start).days + 1)
                if (start + timedelta(days=i)).weekday() < 5 and (start + timedelta(days=i)) not in holidays}

    def test_five_day_week_reads_as_5_2(self):
        holidays = {date(2026, 10, 26)}
        shifts = self.weekdays(date(2026, 9, 1), date(2026, 10, 31), holidays)
        self.assertEqual(rules.schedule_label(shifts, date(2026, 9, 1), date(2026, 10, 31), holidays), '5.2')

    def test_six_day_week_reads_as_6_1(self):
        start, end = date(2026, 9, 1), date(2026, 10, 31)
        shifts = {start + timedelta(days=i) for i in range((end - start).days + 1)
                  if (start + timedelta(days=i)).weekday() < 6}
        self.assertEqual(rules.schedule_label(shifts, start, end), '6.1')

    def test_weeks_without_a_rhythm_give_no_label(self):
        """Недели с одной-двумя сменами — дыра в данных, а не график."""
        start, end = date(2026, 9, 7), date(2026, 9, 27)
        shifts = {date(2026, 9, 7), date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 22)}
        self.assertEqual(rules.schedule_label(shifts, start, end), '')
        self.assertEqual(rules.schedule_label(set(), start, end), '')

    def test_uneven_weeks_read_as_their_median(self):
        """Прод, октябрь 2026: глава ставит то 4, то 6 смен в неделю, обычная — 5."""
        start = date(2026, 10, 5)
        per_week = [6, 4, 5, 5, 6]
        shifts = set()
        for index, count in enumerate(per_week):
            monday = start + timedelta(days=7 * index)
            shifts.update(monday + timedelta(days=offset) for offset in range(count))
        end = start + timedelta(days=7 * len(per_week) - 1)
        self.assertEqual(rules.schedule_label(shifts, start, end), '5.2')
        shifts_partial = shifts | {date(2026, 9, 28)}
        self.assertEqual(rules.schedule_label(shifts_partial, date(2026, 9, 28), end), '5.2')

    def test_even_split_takes_the_lower_week(self):
        """Недели 6, 4, 5, 6: «6.1» дало бы норму 26 при 21 смене — ложные пропуски."""
        start = date(2026, 10, 5)
        shifts = set()
        for index, count in enumerate([6, 4, 5, 6]):
            monday = start + timedelta(days=7 * index)
            shifts.update(monday + timedelta(days=offset) for offset in range(count))
        self.assertEqual(rules.schedule_label(shifts, start, start + timedelta(days=27)), '5.2')

    def test_leave_weeks_do_not_spoil_the_label(self):
        holidays = set()
        shifts = self.weekdays(date(2026, 9, 1), date(2026, 9, 30))
        excluded = {date(2026, 9, 21) + timedelta(days=i) for i in range(10)}
        shifts -= excluded
        self.assertEqual(rules.schedule_label(shifts, date(2026, 9, 1), date(2026, 9, 30), holidays, excluded), '5.2')

    def test_norm_is_the_calendar_of_the_schedule(self):
        """Образец: сентябрь 2026, 5.2 → 22 (и при Б/С на весь месяц), 6.1 → 26."""
        september = rules.month_days(date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(rules.norm_days('5.2', september), 22)
        self.assertEqual(rules.norm_days('6.1', september), 26)
        october = rules.month_days(date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(rules.norm_days('5.2', october, {date(2026, 10, 25), date(2026, 10, 26)}), 21)
        self.assertEqual(rules.norm_days('', october, (), {date(2026, 10, 1), date(2026, 10, 2)}), 2)


class OrderFiltersTotalsTests(unittest.TestCase):
    def test_cities_go_like_the_sample(self):
        cities = ['Актобе', '', 'Шымкент', 'Атырау', 'Алматы', 'Астана', 'Актау']
        ordered = sorted(cities, key=rules.city_order_key)
        self.assertEqual(ordered, ['Алматы', 'Астана', 'Шымкент', 'Актау', 'Актобе', 'Атырау', ''])

    def test_managers_first_then_other_positions(self):
        people = [{'name': 'Б', 'job_title': 'техперсонал'}, {'name': 'В', 'job_title': ''},
                  {'name': 'А', 'job_title': 'Менеджер'}]
        ordered = [p['name'] for p in sorted(people, key=rules.person_order_key)]
        self.assertEqual(ordered, ['А', 'В', 'Б'])
        self.assertEqual(rules.job_title_of({'job_title': '  '}), 'менеджер')

    def test_filters(self):
        person = {'user_id': 7, 'city': 'Астана', 'job_title': ''}
        no_city = {'user_id': 8, 'city': '', 'job_title': 'техперсонал'}
        self.assertTrue(rules.matches_filters(person, rules.clean_filters()))
        self.assertTrue(rules.matches_filters(person, rules.clean_filters(cities=['астана'])))
        self.assertFalse(rules.matches_filters(person, rules.clean_filters(cities=['Алматы'])))
        self.assertTrue(rules.matches_filters(no_city, rules.clean_filters(cities=[rules.NO_CITY_VALUE])))
        self.assertTrue(rules.matches_filters(person, rules.clean_filters(user_ids=['7', 'x'])))
        self.assertFalse(rules.matches_filters(person, rules.clean_filters(user_ids=['8'])))
        self.assertTrue(rules.matches_filters(person, rules.clean_filters(job_titles=['Менеджер'])))
        self.assertFalse(rules.matches_filters(no_city, rules.clean_filters(job_titles=['менеджер'])))

    def test_totals_count_work_and_missed_days(self):
        cells = [
            {'code': 'р', 'late': 5, 'early': 0}, {'code': 'р', 'late': 0, 'early': 30},
            {'code': 'Б/С', 'late': 0, 'early': 0}, {'code': 'н', 'late': 0, 'early': 0},
            {'code': 'О', 'late': 0, 'early': 0}, {'code': 'в', 'late': 0, 'early': 0},
        ]
        self.assertEqual(rules.person_totals(cells), {'worked': 2, 'missed': 2, 'late': 5, 'early': 30})


class BuildModelTests(unittest.TestCase):
    def inputs(self, **overrides):
        start, end = date(2026, 10, 1), date(2026, 10, 31)
        people = [
            {'user_id': 1, 'name': 'Альфа Анна', 'city': 'Астана', 'job_title': '', 'active': True},
            {'user_id': 2, 'name': 'Бета Болат', 'city': 'Алматы', 'job_title': '', 'active': True},
            {'user_id': 3, 'name': 'Гамма Гуля', 'city': 'Алматы', 'job_title': 'техперсонал', 'active': False},
        ]
        spans = {(uid, day): rules.shift_span(day, time(9), time(19))
                 for uid in (1, 2, 3) for day in rules.month_days(start, end) if day.weekday() < 5}
        data = {
            'month': '2026-10', 'start': start, 'end': end, 'window_start': date(2026, 9, 1),
            'fact_until': date(2026, 10, 31), 'people': people, 'spans': spans,
            'shift_days': {uid: {day for (u, day) in spans if u == uid} for uid in (1, 2, 3)},
            'days_off': set(), 'periods': {}, 'membership': {}, 'facts': {}, 'has_card': {1, 2, 3},
            'holidays': set(),
        }
        data.update(overrides)
        return data

    def test_grouping_by_city_and_order(self):
        model = rules.build_model(self.inputs())
        self.assertEqual([city['city'] for city in model['cities']], ['Алматы', 'Астана'])
        self.assertEqual([p['name'] for p in model['cities'][0]['people']], ['Бета Болат', 'Гамма Гуля'])
        self.assertEqual(len(model['days']), 31)

    def test_membership_envelope_blanks_days_outside_the_department(self):
        model = rules.build_model(self.inputs(membership={2: (date(2026, 10, 15), None)}))
        person = model['cities'][0]['people'][0]
        self.assertEqual(person['cells'][0]['code'], '')       # 1 октября — ещё не в отделе
        self.assertEqual(person['cells'][14]['code'], 'н')     # 15 октября — смена без отметок

    def test_mid_month_hire_still_reads_5_2_and_gets_the_full_norm(self):
        """Пришла 15 октября (четверг): неполная первая неделя графика не ломает —
        как в образце, план у пришедшего в середине месяца полный."""
        holidays = {date(2026, 10, 25), date(2026, 10, 26)}
        hired = date(2026, 10, 15)
        shift_days = {day for day in rules.month_days(hired, date(2026, 11, 13))
                      if day.weekday() < 5 and day not in holidays}
        spans = {(4, day): rules.shift_span(day, time(9), time(19)) for day in shift_days
                 if day <= date(2026, 10, 31)}
        inputs = self.inputs(
            people=[{'user_id': 4, 'name': 'Дельта Дана', 'city': 'Атырау', 'job_title': '', 'active': True,
                     'hire_date': hired}],
            spans=spans, shift_days={4: shift_days}, membership={4: (hired, None)}, holidays=holidays,
            label_end=date(2026, 11, 13), has_card={4},
        )
        person = rules.build_model(inputs)['cities'][0]['people'][0]
        self.assertEqual((person['schedule'], person['plan']), ('5.2', 21))

    def test_sunday_holiday_keeps_the_week(self):
        """25.10.2026 — воскресенье: неделя 19–25 октября остаётся в счёте."""
        week = {date(2026, 10, 19) + timedelta(days=i) for i in range(5)}
        label = rules.schedule_label(week, date(2026, 10, 19), date(2026, 10, 25), {date(2026, 10, 25)})
        self.assertEqual(label, '5.2')

    def test_screen_payload_carries_day_flags_and_mark_times(self):
        day = date(2026, 10, 14)
        facts = {(1, day): fact(day, (9, 12), (13, 0), extra_marks=[((14, 0), 'in')]),
                 (2, day): fact(day, (9, 0), (18, 30))}
        model = rules.build_model(self.inputs(facts=facts, holidays={date(2026, 10, 26)},
                                              fact_until=date(2026, 10, 20)))
        payload = rules.model_payload(model)
        self.assertEqual(payload['title'], 'октябрь 2026')
        self.assertEqual(payload['days'][25], {'date': '2026-10-26', 'day': 26, 'weekday': 'пн', 'weekend': False,
                                               'holiday': True, 'future': True})
        people = {p['name']: p for city in payload['cities'] for p in city['people']}
        anna = people['Альфа Анна']['cells'][13]
        self.assertEqual((anna['arrive'], anna['leave'], anna['incomplete']), ('09:12', None, True))
        bolat = people['Бета Болат']['cells'][13]
        self.assertEqual((bolat['arrive'], bolat['leave'], bolat['early']), ('09:00', '18:30', 30))
        self.assertFalse(people['Гамма Гуля']['active'])

    def test_filters_reach_the_model(self):
        model = rules.build_model(self.inputs(), rules.clean_filters(cities=['Астана']))
        self.assertEqual([city['city'] for city in model['cities']], ['Астана'])



class PeriodTests(unittest.TestCase):
    """Просьба владельца 19.11.2026: табель не только за месяц, а за период —
    от одного дня до месяца («месяц это максимум»)."""

    def test_a_month_counts_from_the_start_day(self):
        cases = {
            date(2026, 11, 1): date(2026, 11, 30),    # с 1-го — ровно этот месяц, 30 дней
            date(2026, 10, 1): date(2026, 10, 31),
            date(2027, 2, 1): date(2027, 2, 28),
            date(2026, 10, 15): date(2026, 11, 14),
            date(2026, 12, 15): date(2027, 1, 14),    # через новый год
            date(2026, 10, 31): date(2026, 11, 30),   # 31-го в ноябре нет — последний день
            date(2027, 1, 31): date(2027, 2, 28),
            date(2028, 1, 30): date(2028, 2, 29),
        }
        for start, end in cases.items():
            self.assertEqual(rules.period_max_end(start), end, start)
            self.assertLessEqual((end - start).days + 1, 31, start)

    def test_dates_win_over_the_month_and_a_whole_month_keeps_its_name(self):
        period = rules.resolve_period(month='2026-10', date_from='2026-11-05', date_to='2026-11-05')
        self.assertEqual(period, {'start': date(2026, 11, 5), 'end': date(2026, 11, 5), 'month': None})
        self.assertEqual(rules.resolve_period(date_from='2026-11-01', date_to='2026-11-30')['month'], '2026-11')
        self.assertEqual(rules.resolve_period(month='2026-10'),
                         {'start': date(2026, 10, 1), 'end': date(2026, 10, 31), 'month': '2026-10'})

    def test_longer_than_a_month_reversed_and_broken_dates_are_refused(self):
        bad = [
            (dict(date_from='2026-11-01', date_to='2026-12-01'), 'TIMESHEET_PERIOD_TOO_LONG'),
            (dict(date_from='2026-10-15', date_to='2026-11-15'), 'TIMESHEET_PERIOD_TOO_LONG'),
            (dict(date_from='2026-11-05', date_to='2026-11-04'), 'TIMESHEET_BAD_PERIOD'),
            (dict(date_from='2026-11-31', date_to='2026-12-01'), 'TIMESHEET_BAD_PERIOD'),
            (dict(date_from='2026-11-05'), 'TIMESHEET_BAD_PERIOD'),
            (dict(month='2026-13'), 'TIMESHEET_BAD_MONTH'),
            (dict(), 'TIMESHEET_BAD_MONTH'),
            # Крайние годы — 400, а не 500 на «месяце от начала» и окне графика.
            (dict(date_from='9999-12-15', date_to='9999-12-20'), 'TIMESHEET_BAD_PERIOD'),
            (dict(date_from='0001-01-01', date_to='0001-01-02'), 'TIMESHEET_BAD_PERIOD'),
            (dict(month='0001-01'), 'TIMESHEET_BAD_MONTH'),
            (dict(month='9999-12'), 'TIMESHEET_BAD_MONTH'),
        ]
        for kwargs, code in bad:
            with self.assertRaises(rules.PeriodError, msg=kwargs) as caught:
                rules.resolve_period(**kwargs)
            self.assertEqual(caught.exception.code, code, kwargs)
        with self.assertRaises(rules.PeriodError) as too_long:
            rules.resolve_period(date_from='2026-10-15', date_to='2026-11-15')
        self.assertIn('по 14.11.2026', too_long.exception.message)
        self.assertEqual(rules.resolve_period(date_from='2026-10-15', date_to='2026-11-14')['end'],
                         date(2026, 11, 14))

    def test_titles_and_file_labels(self):
        cases = [
            ((date(2026, 10, 1), date(2026, 10, 31)), 'октябрь 2026', '2026-10'),
            ((date(2026, 11, 5), date(2026, 11, 5)), '5 ноября 2026', '05.11.2026'),
            ((date(2026, 11, 1), date(2026, 11, 15)), '1–15 ноября 2026', '01.11.2026 — 15.11.2026'),
            ((date(2026, 10, 25), date(2026, 11, 5)), '25 октября – 5 ноября 2026', '25.10.2026 — 05.11.2026'),
            ((date(2026, 12, 25), date(2027, 1, 5)), '25 декабря 2026 – 5 января 2027', '25.12.2026 — 05.01.2027'),
        ]
        for (start, end), title, label in cases:
            self.assertEqual(rules.period_title(start, end), title)
            self.assertEqual(rules.period_file_label(start, end), label)


class PeriodModelTests(unittest.TestCase):
    def inputs(self, start, end, **overrides):
        people = [{'user_id': 1, 'name': 'Альфа Анна', 'city': 'Астана', 'job_title': '', 'active': True}]
        shift_days = {day for day in rules.month_days(date(2026, 9, 1), date(2026, 12, 13)) if day.weekday() < 5}
        spans = {(1, day): rules.shift_span(day, time(9), time(19)) for day in shift_days if start <= day <= end}
        data = {
            'month': rules.whole_month_of(start, end), 'start': start, 'end': end,
            'window_start': date(2026, 9, 1), 'label_end': date(2026, 12, 13),
            'fact_until': date(2026, 11, 18), 'people': people, 'spans': spans, 'shift_days': {1: shift_days},
            'days_off': set(), 'periods': {}, 'membership': {}, 'facts': {}, 'has_card': {1}, 'holidays': set(),
        }
        data.update(overrides)
        return data

    def test_days_norm_and_totals_are_of_the_period(self):
        """1–15 ноября: 15 колонок, норма 5.2 — будни периода, итоги — по ним же."""
        start, end = date(2026, 11, 1), date(2026, 11, 15)
        day = date(2026, 11, 3)
        model = rules.build_model(self.inputs(start, end, facts={(1, day): fact(day, (9, 10), (19, 0))}))
        person = model['cities'][0]['people'][0]
        self.assertEqual([d.day for d in model['days']], list(range(1, 16)))
        self.assertEqual((person['schedule'], person['plan']), ('5.2', 10))
        self.assertEqual(len(person['cells']), 15)
        self.assertEqual(person['totals'], {'worked': 1, 'missed': 9, 'late': 10, 'early': 0})
        payload = rules.model_payload(model)
        self.assertEqual((payload['month'], payload['date_from'], payload['date_to'], payload['title']),
                         (None, '2026-11-01', '2026-11-15', '1–15 ноября 2026'))

    def test_one_day_and_a_period_across_months(self):
        one = rules.build_model(self.inputs(date(2026, 11, 4), date(2026, 11, 4)))
        person = one['cities'][0]['people'][0]
        self.assertEqual((len(one['days']), person['plan'], person['cells'][0]['code']), (1, 1, 'н'))
        across = rules.build_model(self.inputs(date(2026, 10, 26), date(2026, 11, 8)))
        self.assertEqual([d.day for d in across['days']][4:8], [30, 31, 1, 2])
        self.assertEqual(across['cities'][0]['people'][0]['plan'], 10)
        self.assertEqual(rules.model_payload(across)['title'], '26 октября – 8 ноября 2026')


if __name__ == '__main__':
    unittest.main()
