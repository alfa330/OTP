"""«Табель учета рабочего времени»: расчёт без базы — план, статусы, минуты, правки, итоги.

Числа взяты из сверки с образцом HR за октябрь 2026: 5/2 — 21 плановый день (26.10 —
перенос праздника), опоздание — минусом под ячейкой, переработка — в комментарий."""
import unittest
from datetime import date, time

from hr_office import rules, timesheet

OCTOBER = rules.month_days(date(2026, 10, 1), date(2026, 10, 31))
HOLIDAYS = {date(2026, 10, 25), date(2026, 10, 26)}


def fact(late=0, over=0, present=True, plan_hours=None):
    return {'late': late, 'over': over, 'present': present, 'planned': plan_hours is not None,
            'plan_hours': plan_hours}


def row(person=None, plan=None, periods=(), facts=None, edits=None, note=None, until=date(2026, 10, 31),
        fact_until=date(2026, 10, 31)):
    person = {'user_id': 1, 'name': 'Тестов Тест', 'status': 'working', **(person or {})}
    if plan is None:
        plan, _source, _pattern = timesheet.person_plan(person, OCTOBER, HOLIDAYS, None, set(), facts or {},
                                                        fact_until, periods)
    return timesheet.build_person_row(person, OCTOBER, holidays=HOLIDAYS, until=until, fact_until=fact_until,
                                      plan=plan, plan_source='calendar', pattern='5/2', periods=periods,
                                      facts=facts or {}, edits=edits or {}, note=note)


class PlanTests(unittest.TestCase):
    def test_calendar_five_two_october_2026_is_21_days(self):
        plan, source, pattern = timesheet.person_plan({}, OCTOBER, HOLIDAYS, None, set(), {}, date(2026, 10, 31))
        self.assertEqual((len(plan), source, pattern), (21, 'calendar', '5/2'))
        self.assertNotIn(date(2026, 10, 26), plan)
        self.assertEqual(sum(plan.values()), 168)

    def test_day_hours_override(self):
        plan, *_ = timesheet.person_plan({'day_hours': 4}, OCTOBER, HOLIDAYS, None, set(), {}, date(2026, 10, 31))
        self.assertEqual(sum(plan.values()), 84)

    def test_icore_shifts_win_and_follow_shift_length(self):
        shifts = [(date(2026, 10, 3), time(9, 0), time(18, 0)), (date(2026, 10, 4), time(9, 0), time(21, 0))]
        plan, source, _ = timesheet.person_plan({}, OCTOBER, HOLIDAYS, shifts, set(), {}, date(2026, 10, 31))
        self.assertEqual(source, 'icore')
        self.assertEqual(plan, {date(2026, 10, 3): 8.0, date(2026, 10, 4): 11.0})

    def test_personal_schedule_from_otmetki_for_past_days(self):
        # Почасовик: план есть только по вторникам по 2 часа — календарь 5/2 его бы исказил.
        facts = {day: fact(plan_hours=2.0) for day in OCTOBER if day.weekday() == 1}
        plan, source, _ = timesheet.person_plan({}, OCTOBER, HOLIDAYS, None, set(), facts, date(2026, 10, 31))
        self.assertEqual(source, 'otmetki')
        self.assertEqual(set(plan), set(facts))
        self.assertTrue(all(hours == 2.0 for hours in plan.values()))

    def test_vacation_days_keep_calendar_norm_for_plan(self):
        facts = {day: fact(plan_hours=8.0) for day in OCTOBER if day.weekday() < 5 and day.day > 2 and day not in HOLIDAYS}
        periods = [('annual_leave', date(2026, 10, 1), date(2026, 10, 2))]
        plan, *_ = timesheet.person_plan({}, OCTOBER, HOLIDAYS, None, set(), facts, date(2026, 10, 31), periods)
        self.assertEqual(len(plan), 21)


class CellTests(unittest.TestCase):
    def test_statuses_become_letters_and_weekends_v(self):
        result = row(periods=[('annual_leave', date(2026, 10, 1), date(2026, 10, 2)),
                              ('bs', date(2026, 10, 12), date(2026, 10, 12)),
                              ('sick_leave', date(2026, 10, 13), date(2026, 10, 13))])
        cells = result['cells']
        self.assertEqual([cells[0]['v'], cells[1]['v'], cells[2]['v']], ['О', 'О', 'В'])
        self.assertEqual(cells[11]['v'], 'А')
        self.assertEqual(cells[12]['v'], 'Б')
        self.assertEqual(cells[25]['v'], 'В')   # 26.10 — перенос праздника
        self.assertEqual(result['totals']['absences'], 4)
        self.assertEqual(result['totals']['shifts'], 17)

    def test_lateness_is_negative_under_the_cell_and_overwork_goes_to_comment(self):
        facts = {date(2026, 10, 5): fact(late=31), date(2026, 10, 6): fact(over=45)}
        result = row(facts=facts)
        self.assertEqual(result['cells'][4]['m'], -31)
        self.assertIsNone(result['cells'][5]['m'])
        self.assertEqual(result['totals']['minutes'], -31)
        self.assertIn('Переработка 45 мин', result['note_auto'])

    def test_manual_edit_wins_and_is_marked(self):
        result = row(edits={date(2026, 10, 5): {'value': '4', 'minutes': 'в офисе'}})
        cell = result['cells'][4]
        self.assertEqual((cell['v'], cell['cv'], cell['m'], cell['edited']), ('4', '8', 'в офисе', True))
        self.assertEqual(result['totals']['hours'], 164)

    def test_days_before_hire_are_empty_and_comment_tells_why(self):
        result = row(person={'hire_date': date(2026, 10, 12)})
        self.assertEqual(result['cells'][10]['v'], '')
        self.assertEqual(result['cells'][11]['v'], '8')
        self.assertIn('Прием 12.10.2026', result['note_auto'])
        self.assertEqual(result['plan_days'], 21)   # норма месяца, как в образце HR

    def test_days_after_until_are_empty(self):
        result = row(until=date(2026, 10, 15))
        self.assertEqual(result['cells'][14]['v'], '8')
        self.assertEqual(result['cells'][15]['v'], '')

    def test_no_marks_on_planned_day_is_flagged_for_people_with_card(self):
        result = row(person={'has_card': True}, facts={date(2026, 10, 6): fact(present=False)})
        self.assertTrue(result['cells'][4]['no_marks'])
        self.assertTrue(result['cells'][5]['no_marks'])


class DismissalTests(unittest.TestCase):
    def test_dismissal_day_is_the_last_working_day(self):
        result = row(person={'status': 'fired'}, periods=[('dismissal', date(2026, 10, 15), None)])
        self.assertEqual(result['cells'][14]['v'], '8')     # 15.10 — последний рабочий
        self.assertEqual(result['cells'][15]['v'], '')
        self.assertIn('Увольнение 15.10.2026', result['note_auto'])

    def test_open_dismissal_of_working_person_is_ignored(self):
        # На проде у действующего СВ с весны висит открытое «Увольнение» — табель не пустеет.
        result = row(person={'status': 'working'}, periods=[('dismissal', date(2026, 3, 1), None)])
        self.assertEqual(result['cells'][20]['v'], '8')

    def test_closed_dismissal_blanks_only_its_days(self):
        result = row(person={'status': 'working'}, periods=[('dismissal', date(2026, 10, 5), date(2026, 10, 11))])
        self.assertEqual(result['cells'][4]['v'], '8')      # 05.10 — последний день перед уходом
        self.assertEqual(result['cells'][5]['v'], '')
        self.assertEqual(result['cells'][11]['v'], '8')     # 12.10 — вернулся


class MatchingTests(unittest.TestCase):
    """Тёзка из другого отдела не отдаёт свои опоздания человеку из табеля."""

    def test_namesake_outside_roster_does_not_leak_into_roster(self):
        from db.group_late import GroupLateMixin

        class FakeDb(GroupLateMixin):
            # Таблица свёртки казахских букв живёт в Database — та же, что у «Отметок».
            _GLB_NAME_FOLD = str.maketrans({'ә': 'а', 'ғ': 'г', 'қ': 'к', 'ң': 'н', 'ө': 'о', 'ұ': 'у', 'ү': 'у',
                                            'һ': 'х', 'і': 'и', 'ё': 'е', 'й': 'и', 'ь': '', 'ъ': ''})

        everyone = [{'user_id': 1, 'name': 'Иванова Анна Петровна', 'status': 'working'},
                    {'user_id': 2, 'name': 'Иванова Анна Сергеевна', 'status': 'working'}]
        rows = [{'employee_id': 'wp-2', 'employee': 'Иванова Анна Сергеевна', 'date': '2026-10-05', 'late_minutes': 40}]
        cards = [{'ext_id': 'wp-2', 'full_name': 'Иванова Анна Сергеевна'}]
        facts, owners = timesheet.match_rows_to_people(FakeDb, {1}, everyone, rows, cards, {})
        self.assertEqual(facts, {})
        self.assertEqual(owners, set())


class RosterTests(unittest.TestCase):
    PEOPLE = [
        {'user_id': 1, 'project_value': 'ЦО', 'status': 'working', 'hire_date': None, 'dismissal_from': None},
        {'user_id': 2, 'project_value': 'Регионы', 'status': 'working', 'hire_date': None, 'dismissal_from': None},
        {'user_id': 3, 'project_value': 'ЦО', 'status': 'working', 'hire_date': date(2026, 11, 3), 'dismissal_from': None},
        {'user_id': 4, 'project_value': 'ЦО', 'status': 'fired', 'hire_date': None, 'dismissal_from': date(2026, 10, 20)},
        {'user_id': 5, 'project_value': 'ЦО', 'status': 'fired', 'hire_date': None, 'dismissal_from': date(2026, 9, 1)},
        {'user_id': 6, 'project_value': 'ЦО', 'status': 'fired', 'hire_date': None, 'dismissal_from': None},
    ]

    def test_project_hire_and_dismissal_bounds(self):
        ids = [p['user_id'] for p in timesheet.roster(self.PEOPLE, 'ЦО', date(2026, 10, 1), date(2026, 10, 31))]
        self.assertEqual(ids, [1, 4])

    def test_chosen_people_ignore_project(self):
        ids = [p['user_id'] for p in timesheet.roster(self.PEOPLE, 'ЦО', date(2026, 10, 1), date(2026, 10, 31), [2])]
        self.assertEqual(ids, [2])


class WorkbookTests(unittest.TestCase):
    def test_workbook_has_two_rows_per_person_and_formulas(self):
        from io import BytesIO
        from openpyxl import load_workbook
        person = row(facts={date(2026, 10, 5): fact(late=10)})
        model = {'title': 'Октябрь 2026', 'project': 'ЦО',
                 'days': [{'date': d.isoformat(), 'day': d.day, 'weekday': rules.WEEKDAYS_UPPER[d.weekday()],
                           'weekend': d.weekday() >= 5, 'holiday': d in HOLIDAYS} for d in OCTOBER],
                 'groups': [{'name': 'HR отдел', 'people': [dict(person, contract_label='ГПХ ИП «Тест»',
                                                                  job_title='HR менеджер')]}]}
        sheet = load_workbook(BytesIO(timesheet.workbook(model))).active
        self.assertEqual(sheet.cell(row=2, column=4).value, 'Ф.И.О должностного лица')
        self.assertEqual(sheet.cell(row=4, column=4).value, 'Тестов Тест')
        self.assertEqual(sheet.cell(row=5, column=13).value, -10)    # минуты — строкой ниже, 5-е число
        self.assertEqual(sheet.cell(row=4, column=40).value, '=COUNT(I4:AM4)')
        self.assertEqual(sheet.cell(row=4, column=43).value, '=SUM(I5:AM5)')


if __name__ == '__main__':
    unittest.main()
