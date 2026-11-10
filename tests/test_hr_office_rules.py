"""ТЗ HR «iCore 4» («Прием», табель, АВР): правила без базы — статусы, табель, деньги, должность, даты."""
import json
import shutil
import subprocess
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from hr_office import rules

ROOT = Path(__file__).resolve().parents[1]


class StatusLabelsTests(unittest.TestCase):
    def test_labels_follow_employment_type(self):
        self.assertEqual(rules.status_label('annual_leave', 'of'), 'Трудовой отпуск')
        self.assertEqual(rules.status_label('annual_leave', 'gph'), 'Оплачиваемый отпуск')
        self.assertEqual(rules.status_label('bs', 'of'), 'Отпуск без содержания')
        self.assertEqual(rules.status_label('bs', 'gph'), 'Приостановление договора')
        self.assertEqual(rules.status_label('bs', 'smz'), 'Приостановление договора')
        self.assertEqual(rules.status_label('sick_leave', 'of'), 'Больничный')

    def test_unknown_type_gets_neutral_labels_and_all_options(self):
        self.assertEqual(rules.status_label('bs', None), 'Б/С')
        self.assertEqual(rules.status_label('annual_leave', ''), 'Отпуск')
        values = [item['value'] for item in rules.status_options(None)]
        self.assertEqual(values, ['working', 'annual_leave', 'bs', 'sick_leave'])

    def test_legacy_unpaid_leave_is_bs(self):
        self.assertEqual(rules.status_label('unpaid_leave', 'gph'), 'Приостановление договора')

    def test_options_by_type_per_tz_table(self):
        self.assertEqual([item['value'] for item in rules.status_options('of')],
                         ['working', 'annual_leave', 'bs', 'sick_leave'])
        # ТЗ п. 8: «Б» — только у ТД; у СМЗ — только «Приостановление договора».
        self.assertEqual([item['value'] for item in rules.status_options('gph')], ['working', 'annual_leave', 'bs'])
        self.assertEqual([item['value'] for item in rules.status_options('smz')], ['working', 'bs'])

    def test_current_status_stays_selectable_even_if_type_has_no_such_option(self):
        values = [item['value'] for item in rules.status_options('gph', current='sick_leave')]
        self.assertIn('sick_leave', values)


class TimesheetCodesTests(unittest.TestCase):
    def test_period_codes_match_tz_table(self):
        self.assertEqual(rules.PERIOD_CODES['annual_leave'], 'О')
        self.assertEqual(rules.PERIOD_CODES['bs'], 'А')
        self.assertEqual(rules.PERIOD_CODES['sick_leave'], 'Б')

    def test_clean_cell_value(self):
        self.assertEqual(rules.clean_cell_value('8'), ('8', None))
        self.assertEqual(rules.clean_cell_value('7,5'), ('7.5', None))
        self.assertEqual(rules.clean_cell_value('в'), ('В', None))
        self.assertEqual(rules.clean_cell_value('B'), ('В', None))  # латинская B
        self.assertEqual(rules.clean_cell_value(''), ('', None))
        self.assertIsNotNone(rules.clean_cell_value('25')[1])
        self.assertIsNotNone(rules.clean_cell_value('x')[1])

    def test_clean_minutes_value(self):
        self.assertEqual(rules.clean_minutes_value('-15'), (-15, None))
        self.assertEqual(rules.clean_minutes_value('+40'), (40, None))
        self.assertEqual(rules.clean_minutes_value('в офисе'), ('в офисе', None))
        self.assertEqual(rules.clean_minutes_value(''), (None, None))

    def test_calendar_workday_respects_holidays_and_pattern(self):
        holidays = {date(2026, 10, 26)}
        self.assertTrue(rules.calendar_workday(date(2026, 10, 23), '5/2', holidays))      # пт
        self.assertFalse(rules.calendar_workday(date(2026, 10, 24), '5/2', holidays))     # сб
        self.assertTrue(rules.calendar_workday(date(2026, 10, 24), '6/1', holidays))
        self.assertFalse(rules.calendar_workday(date(2026, 10, 26), '5/2', holidays))     # перенос праздника
        self.assertIsNone(rules.calendar_workday(date(2026, 10, 23), '2/2', holidays))


class ProjectsTests(unittest.TestCase):
    def test_default_project_by_department(self):
        self.assertEqual(rules.default_project('front_office'), 'Регионы')
        self.assertEqual(rules.default_project('car_rental'), 'iTaxi Rent')
        self.assertEqual(rules.default_project('szov'), 'Колл-центр')
        self.assertEqual(rules.default_project('accounting'), 'ЦО')
        # Служебные учётки без отдела не попадают в ЦО сами собой.
        self.assertIsNone(rules.default_project(None))

    def test_project_field_wins_over_department(self):
        self.assertEqual(rules.project_of({'project': 'ЦО', 'department_code': 'front_office'}), 'ЦО')

    def test_clean_project_aliases(self):
        self.assertEqual(rules.clean_project('Аренда авто'), 'iTaxi Rent')
        self.assertEqual(rules.clean_project('центральный офис'), 'ЦО')
        self.assertIsNone(rules.clean_project('Неизвестно'))


class CompanyKeyTests(unittest.TestCase):
    def test_spellings_of_one_company_share_a_key(self):
        keys = {rules.company_key(name) for name in ('ИП Тестова', 'ИП "Тестова"', 'ИП ТЕСТОВА', 'ИП «Тестова»')}
        self.assertEqual(keys, {'тестова'})
        self.assertEqual(rules.company_key('TOO "Stock Company"'), rules.company_key('ТОО Stock Company'))

    def test_display_name_and_id_label(self):
        self.assertEqual(rules.company_display_name({'name': 'Eazy Go Rent', 'kind': 'ip'}), 'ИП «Eazy Go Rent»')
        self.assertEqual(rules.company_id_label('ip'), 'ИИН')
        self.assertEqual(rules.company_id_label('too'), 'БИН')


class IinTests(unittest.TestCase):
    def test_valid_iin_with_matching_birth_date(self):
        value, error, warning = rules.validate_iin('900101400013', date(1990, 1, 1), 'female')
        self.assertEqual((value, error, warning), ('900101400013', None, None))

    def test_checksum_error(self):
        value, error, _warning = rules.validate_iin('900101400017')
        self.assertIsNone(value)
        self.assertIn('контрольной', error)

    def test_birth_date_mismatch_is_warning_not_error(self):
        value, error, warning = rules.validate_iin('900101400013', date(1991, 5, 5), 'female')
        self.assertEqual(value, '900101400013')
        self.assertIsNone(error)
        self.assertIn('дат', warning)


class MoneyTests(unittest.TestCase):
    def test_number_words(self):
        self.assertEqual(rules.number_words(0), 'ноль')
        self.assertEqual(rules.number_words(21000), 'двадцать одна тысяча')
        self.assertEqual(rules.number_words(2000), 'две тысячи')
        self.assertEqual(rules.number_words(5000), 'пять тысяч')
        self.assertEqual(rules.number_words(250000), 'двести пятьдесят тысяч')
        self.assertEqual(rules.number_words(1250300), 'один миллион двести пятьдесят тысяч триста')
        self.assertEqual(rules.number_words(111), 'сто одиннадцать')

    def test_amount_words_with_tiyn(self):
        self.assertEqual(rules.amount_words(Decimal('250000.50')), 'двести пятьдесят тысяч 50 тиын')

    def test_money_text(self):
        self.assertEqual(rules.money_text(Decimal('250000')), '250 000')
        self.assertEqual(rules.money_text(Decimal('1250000.5')), '1 250 000,50')

    def test_clean_amount(self):
        self.assertEqual(rules.clean_amount('250 000'), (Decimal('250000.00'), None))
        self.assertIsNotNone(rules.clean_amount('abc')[1])
        self.assertEqual(rules.clean_amount(''), (None, None))


class JobGenitiveTests(unittest.TestCase):
    CASES = {
        'HR менеджер': 'Услуги HR менеджера',
        'Менеджер по аренде': 'Услуги менеджера по аренде',
        'Младший финансовый аналитик': 'Услуги младшего финансового аналитика',
        'СММ менеджер': 'Услуги СММ менеджера',
        'МЕНЕДЖЕР ПО КЛИНИНГУ': 'Услуги менеджера по клинингу',
        'Fullstack разработчик': 'Услуги Fullstack разработчика',
        'Техничка': 'Услуги технички',
        'Руководитель отдела продаж': 'Услуги руководителя отдела продаж',
        'Маркетолог-контекстолог': 'Услуги маркетолога-контекстолога',
        'Специалист СБ': 'Услуги специалиста СБ',
    }

    def test_known_titles(self):
        for title, expected in self.CASES.items():
            with self.subTest(title=title):
                self.assertEqual(rules.act_work_name(title), expected)


class DatesAndNumbersTests(unittest.TestCase):
    def test_date_long(self):
        self.assertEqual(rules.date_long(date(2026, 11, 10)), '«10» ноября 2026 г.')

    def test_act_period_text(self):
        self.assertEqual(rules.act_period_text(date(2026, 10, 1), date(2026, 10, 31)), 'октябрь 2026 г.')
        self.assertEqual(rules.act_period_text(date(2026, 11, 1), date(2026, 11, 10)), '01.11.2026 – 10.11.2026')

    def test_default_act_period_is_month_start_to_today(self):
        self.assertEqual(rules.default_act_period(date(2026, 11, 10)), (date(2026, 11, 1), date(2026, 11, 10)))
        self.assertEqual(rules.previous_month_period(date(2026, 11, 10)), (date(2026, 10, 1), date(2026, 10, 31)))

    def test_numbers(self):
        self.assertEqual(rules.contract_number(15, 2026), '15/2026')
        self.assertEqual(rules.act_number(3, 2026), 'АВР-3/2026')

    def test_short_name(self):
        self.assertEqual(rules.short_name('Иванова Анна Сергеевна'), 'Иванова А.С.')
        self.assertEqual(rules.short_name('Тестова Айым Ерланқызы'), 'Тестова А.Е.')


class FrontMirrorTests(unittest.TestCase):
    """src/utils/employmentStatus.js — зеркало rules.status_label/status_options: карточка
    «Учета сотрудников», «Прием» и «Отметки» должны подписывать статус одинаково."""

    @unittest.skipUnless(shutil.which('node'), 'Node.js is required')
    def test_js_mirror_matches_python(self):
        script = (
            "import('./src/utils/employmentStatus.js').then((m) => {"
            "const out = {};"
            "for (const kind of ['of', 'gph', 'smz', '']) {"
            "  out[kind] = { options: m.employmentStatusOptions(kind, 'working'),"
            "    labels: ['annual_leave', 'bs', 'sick_leave', 'unpaid_leave', 'working', 'fired']"
            "      .map((code) => m.employmentStatusLabel(code, kind)) };"
            "}"
            "console.log(JSON.stringify(out)); });"
        )
        completed = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script], cwd=ROOT,
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        js = json.loads(completed.stdout)
        for kind in ('of', 'gph', 'smz', ''):
            with self.subTest(kind=kind):
                self.assertEqual(js[kind]['options'], rules.status_options(kind or None, 'working'))
                self.assertEqual(js[kind]['labels'], [rules.status_label(code, kind or None) for code in
                                                      ('annual_leave', 'bs', 'sick_leave', 'unpaid_leave',
                                                       'working', 'fired')])


if __name__ == '__main__':
    unittest.main()
