"""Журнал «Приема», «Табеля учета рабочего времени» и «АВР»: что и как записывается.

Значения ИИН, документа, адресов и сумм договора в журнал не попадают — только факт правки."""
import unittest
from datetime import date
from pathlib import Path

from hr_office import journal

ROOT = Path(__file__).resolve().parents[1]


class SummaryTests(unittest.TestCase):
    def test_personal_values_are_not_written(self):
        changes = journal.changed_data_fields(
            {'iin': None, 'registration_address': None, 'project': None},
            {'iin': '900101400013', 'registration_address': 'г. Алматы, ул. Тестовая, 1', 'project': 'ЦО'})
        text = journal.person_summary('ГПХ · ИП «Тест» · № 1/2026', True, changes)
        self.assertIn('Оформление: ГПХ · ИП «Тест» · № 1/2026', text)
        self.assertIn('ИИН', text)
        self.assertIn('адрес регистрации', text)
        self.assertIn('проект ЦО', text)          # проект — не личные данные, показываем значение
        self.assertNotIn('900101400013', text)
        self.assertNotIn('Тестовая', text)

    def test_nothing_changed_gives_no_entry(self):
        self.assertEqual(journal.person_summary('ГПХ', False, []), '')
        self.assertEqual(journal.changed_data_fields({'day_hours': 8.0}, {'day_hours': 8}), [])

    def test_status_texts(self):
        self.assertEqual(journal.status_summary('annual_leave', 'gph', date(2026, 11, 5), date(2026, 11, 15)),
                         'Статус «Оплачиваемый отпуск» 05.11.2026 – 15.11.2026')
        self.assertEqual(journal.status_summary('working', 'of', closed_on=date(2026, 11, 9), previous='bs'),
                         'Статус «Отпуск без содержания» закончен 09.11.2026 — работает')
        self.assertEqual(journal.status_summary('working', 'gph', removed=True, previous='bs'),
                         'Статус «Приостановление договора» снят')

    def test_cell_texts(self):
        self.assertEqual(journal.cell_summary(date(2026, 10, 12), 'value', None, 'А', computed='8'),
                         '12.10.2026: «8» → «А»')
        self.assertEqual(journal.cell_summary(date(2026, 10, 12), 'minutes', '-15', '', computed='-15'),
                         '12.10.2026, под ячейкой: «-15» → «пусто»')
        self.assertEqual(journal.cell_summary(date(2026, 10, 12), 'value', 'А', None, computed='8', reset=True),
                         '12.10.2026: «А» → расчёт')

    def test_import_texts(self):
        self.assertEqual(journal.import_summary('Список.xlsx', ' 2026', 9, 14, ['Тест Компани'], same=40, missing=5),
                         'Загружена таблица «Список.xlsx», лист « 2026»: изменено у 9 сотрудников (14 полей), '
                         'без изменений — 40, нет в iCORE — 5, новых компаний — 1')
        self.assertEqual(journal.import_summary('Т.xlsx', '', 1, 1, []),
                         'Загружена таблица «Т.xlsx»: изменено у 1 сотрудника (1 поле)')
        self.assertIn('у 21 сотрудника (22 поля)', journal.import_summary('Т.xlsx', '', 21, 22, []))

    def test_person_changes_are_exact_but_hide_personal_values(self):
        data_changes = journal.changed_data_fields(
            {'iin': '900101400013', 'doc_number': '1', 'registration_address': 'старый', 'project': 'Регионы',
             'day_hours': 8.0},
            {'iin': '900101400021', 'doc_number': '2', 'registration_address': 'новый', 'project': 'ЦО',
             'day_hours': 8})
        contract = journal.contract_changes(
            {'employment_type': 'ГПХ', 'company': 'ИП «А»', 'number': '1/2026', 'signed_on': None, 'job_title': 'Курьер'},
            {'employment_type': 'ТД', 'company': 'ТОО «Б»', 'number': '1/2026', 'signed_on': None, 'job_title': 'Курьер'})
        changes = journal.person_changes(data_changes, contract=contract, amount_changed=True)
        self.assertEqual(changes, [
            {'label': 'Вид оформления', 'old': 'ГПХ', 'new': 'ТД'},          # номер и должность не менялись —
            {'label': 'Компания', 'old': 'ИП «А»', 'new': 'ТОО «Б»'},          # их в записи нет
            {'label': 'Сумма договора', 'hidden': True},
            {'label': 'ИИН', 'hidden': True},
            {'label': 'Удостоверение личности', 'hidden': True},
            {'label': 'Адрес регистрации', 'hidden': True},
            {'label': 'Проект', 'old': 'Регионы', 'new': 'ЦО'},
        ])
        text = repr(changes)
        for secret in ('900101400013', '900101400021', 'старый', 'новый'):
            self.assertNotIn(secret, text)

    def test_numbers_from_database_read_like_numbers(self):
        """Часы приходят из базы numeric: «8.00» в журнале выглядело как сбой — пишем «8» и «7,5»."""
        from decimal import Decimal
        changes = journal.person_changes(journal.changed_data_fields({'day_hours': Decimal('8.00')}, {'day_hours': 7.5}))
        self.assertEqual(changes, [{'label': 'Часов в день', 'old': '8', 'new': '7,5'}])

    def test_amount_alone_is_journaled(self):
        """Сменили только сумму договора — запись в журнале есть (раньше её не было вовсе)."""
        self.assertEqual(journal.person_summary('ГПХ', False, [], amount_changed=True), 'изменены: сумма договора')


class WiringTests(unittest.TestCase):
    """Каждое действие разделов оставляет запись в журнале."""

    def test_every_action_writes_to_the_journal(self):
        routes = (ROOT / 'hr_office' / 'routes.py').read_text(encoding='utf-8')
        service = (ROOT / 'hr_office' / 'service.py').read_text(encoding='utf-8')
        for action in ("action='company'", "action='documents'", "action='template'", "action='cell'",
                       "action='note'", "action='close'", "action='reopen'"):
            self.assertIn(action, routes)
        for action in ("action='person'", "action='status'", "action='act'", "action='import'"):
            self.assertIn(action, service)
        self.assertIn("@route('/journal')", routes)

    def test_journal_button_in_each_section(self):
        for name in ('HrHiringTab.jsx', 'HrTimesheetTab.jsx', 'HrActsTab.jsx'):
            source = (ROOT / 'src' / 'components' / 'hr_office' / name).read_text(encoding='utf-8')
            self.assertIn('<HrJournalSheet', source)
            self.assertIn('Журнал', source)

    def test_windows_start_at_the_top_edge(self):
        """Окна раздела — дети столбика экрана. У столбика на space-y-3 окно получало его
        margin-top: затемнение начиналось с 12-го пикселя, над ним белая полоса (замер 10.11.2026,
        корень окна top=12, высота 944 из 956). У столбика с gap такого отступа нет."""
        for name, windows in (('HrHiringTab.jsx', ('<HrPersonSheet', '<HrCompaniesSheet', '<HrTemplatesSheet',
                                                   '<HrJournalSheet', '<HrImportSheet')),
                              ('HrTimesheetTab.jsx', ('<CellEditor', '<NoteEditor', '<HrJournalSheet', '<IosModal')),
                              ('HrActsTab.jsx', ('<HrJournalSheet',))):
            body = (ROOT / 'src' / 'components' / 'hr_office' / name).read_text(encoding='utf-8')
            body = body[body.rindex('\n    return (\n'):]  # возврат самого экрана, не строки списка
            root = body.split('<div className="', 1)[1].split('"', 1)[0]
            self.assertEqual(root.split()[:3], ['flex', 'flex-col', 'gap-3'], name)
            self.assertNotIn('space-y-', root, name)
            for window in windows:
                self.assertIn(window, body, f'{name}: {window}')


if __name__ == '__main__':
    unittest.main()
