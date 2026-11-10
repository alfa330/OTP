"""«Прием», «Табель учета рабочего времени», «АВР»: доступ по ролям, проверка полей, загрузка таблиц HR, подключение к серверу.

ФИО в тестах выдуманы: таблицы HR с настоящими людьми в репозиторий не кладём."""
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

from hr_office import importer, rules, service

ROOT = Path(__file__).resolve().parents[1]


def ctx(**kwargs):
    base = {'user_id': 1, 'role': 'operator', 'is_hr': False, 'is_super': False, 'is_admin': False,
            'is_supervisor': False, 'department_id': 10, 'headed_department_ids': []}
    base.update(kwargs)
    return base


class AccessTests(unittest.TestCase):
    def test_hr_and_super_get_everything(self):
        for viewer in (ctx(is_hr=True, role='hr_manager'), ctx(is_super=True, role='super_admin')):
            caps = service.capabilities(viewer)
            self.assertTrue(all(caps[key] for key in ('hiring', 'timesheet', 'acts', 'acts_all', 'companies')))

    def test_global_admin_without_department_role_gets_no_personal_data(self):
        caps = service.capabilities(ctx(is_admin=True, role='admin'))
        self.assertFalse(caps['hiring'])
        self.assertFalse(caps['timesheet'])
        self.assertFalse(caps['acts'])

    def test_heads_and_supervisors_get_only_acts(self):
        for viewer in (ctx(headed_department_ids=[10]), ctx(is_supervisor=True, role='sv')):
            caps = service.capabilities(viewer)
            self.assertEqual((caps['hiring'], caps['timesheet'], caps['acts'], caps['acts_all']),
                             (False, False, True, False))

    def test_operator_gets_nothing(self):
        self.assertFalse(any(service.capabilities(ctx()).values()))
        with self.assertRaises(service.HrError):
            service.require(ctx(), 'acts')

    def test_acts_scope(self):
        head = ctx(headed_department_ids=[10], role='admin', is_admin=True)
        sv = ctx(is_supervisor=True, role='sv', department_id=10)
        own_operator = {'department_id': 10, 'role': 'operator'}
        own_admin = {'department_id': 10, 'role': 'admin'}
        other = {'department_id': 11, 'role': 'operator'}
        self.assertTrue(service.in_acts_scope(head, own_operator))
        self.assertTrue(service.in_acts_scope(head, own_admin))          # глава — и на админов своего отдела
        self.assertFalse(service.in_acts_scope(head, other))
        self.assertTrue(service.in_acts_scope(sv, own_operator))
        self.assertFalse(service.in_acts_scope(sv, own_admin))          # СВ — только линия
        self.assertFalse(service.in_acts_scope(sv, other))
        self.assertFalse(service.in_acts_scope(head, {'department_id': 10, 'role': 'super_admin'}))


class StatusScopeTests(unittest.TestCase):
    def test_who_sees_whose_status(self):
        operator = {'department_id': 10, 'role': 'operator'}
        admin = {'department_id': 10, 'role': 'admin'}
        other = {'department_id': 11, 'role': 'operator'}
        for viewer in (ctx(is_hr=True), ctx(is_super=True), ctx(is_global_admin=True, is_admin=True)):
            self.assertTrue(service.in_status_scope(viewer, other))
        head = ctx(headed_department_ids=[10], is_admin=True)
        self.assertTrue(service.in_status_scope(head, operator))
        self.assertFalse(service.in_status_scope(head, admin))       # как в «Графиках работы»
        self.assertFalse(service.in_status_scope(head, other))
        sv = ctx(is_supervisor=True, department_id=10)
        self.assertTrue(service.in_status_scope(sv, operator))
        self.assertFalse(service.in_status_scope(sv, other))
        self.assertFalse(service.in_status_scope(ctx(), operator))
        self.assertFalse(service.capabilities(ctx(is_admin=True))['statuses'])   # админ-глава без отдела? — нет


class FakeCursor:
    """Курсор без базы: отдаёт заготовленный период и пишет, что выполнилось."""

    def __init__(self, period_row):
        self.period_row = period_row
        self.sql = []
        self._last = None

    def execute(self, sql, params=None):
        self.sql.append((' '.join(sql.split()), params))
        self._last = sql

    def fetchone(self):
        return self.period_row if 'FROM operator_schedule_status_periods' in self._last else None


class FakeDb:
    def __init__(self):
        self.saved = []
        self.synced = []

    def _save_schedule_status_period_tx(self, cursor, operator_id, status, start, **kwargs):
        self.saved.append((operator_id, status, start, kwargs))

    def _sync_user_statuses_from_schedule_periods_tx(self, cursor, operator_ids=None):
        self.synced.append(operator_ids)


class ChangeStatusTests(unittest.TestCase):
    TODAY = date(2026, 11, 10)
    PERSON = {'user_id': 5, 'name': 'Тестов Тест', 'role': 'operator', 'department_id': 10,
              'employment_type': 'gph', 'contract_employment_type': 'gph'}

    def run_change(self, payload, period_row):
        cursor, db = FakeCursor(period_row), FakeDb()
        original = service.queries.get_person
        service.queries.get_person = lambda _cursor, _uid: dict(self.PERSON)
        try:
            service.change_status(cursor, db, ctx(is_hr=True, user_id=1), 5, payload, self.TODAY)
        finally:
            service.queries.get_person = original
        return cursor, db

    def test_working_closes_started_period_on_yesterday_keeping_the_record(self):
        row = (7, 'bs', date(2026, 11, 1), date(2026, 11, 30), 'по заявке', True)
        cursor, db = self.run_change({'status': 'working', 'period_id': 7}, row)
        writes = [(sql, params) for sql, params in cursor.sql if sql.startswith(('UPDATE', 'DELETE'))]
        self.assertEqual(len(writes), 1)
        self.assertTrue(writes[0][0].startswith('UPDATE operator_schedule_status_periods SET end_date'))
        self.assertEqual(writes[0][1], (date(2026, 11, 9), 7))
        self.assertEqual(db.saved, [])
        self.assertEqual(db.synced, [[5]])
        journal_rows = [params for sql, params in cursor.sql if sql.startswith('INSERT INTO hr_audit_log')]
        self.assertEqual(len(journal_rows), 1)
        self.assertIn('закончен 09.11.2026', journal_rows[0][3])

    def test_working_removes_future_period(self):
        row = (7, 'annual_leave', date(2026, 11, 16), date(2026, 11, 20), None, False)
        cursor, _db = self.run_change({'status': 'working', 'period_id': 7}, row)
        self.assertTrue(any(sql.startswith('DELETE') for sql, _ in cursor.sql))

    def test_shortening_updates_dates_of_the_same_record(self):
        row = (7, 'annual_leave', date(2026, 11, 5), date(2026, 11, 20), None, False)
        cursor, db = self.run_change({'status': 'annual_leave', 'period_id': 7, 'start': '2026-11-05',
                                      'end': '2026-11-15'}, row)
        self.assertTrue(any(sql.startswith('UPDATE operator_schedule_status_periods SET start_date') for sql, _ in cursor.sql))
        self.assertFalse(any(sql.startswith('DELETE') for sql, _ in cursor.sql))
        self.assertEqual(db.saved, [])

    def test_extending_replaces_the_period_keeping_comment_and_tenure_flag(self):
        row = (7, 'bs', date(2026, 11, 5), date(2026, 11, 20), 'длительный', True)
        cursor, db = self.run_change({'status': 'bs', 'period_id': 7, 'start': '2026-11-05', 'end': '2026-12-20'}, row)
        self.assertTrue(any(sql.startswith('DELETE') for sql, _ in cursor.sql))
        self.assertEqual(db.saved[0][1], 'bs')
        self.assertEqual(db.saved[0][3]['comment'], 'длительный')
        self.assertTrue(db.saved[0][3]['exclude_from_tenure'])

    def test_dismissal_is_not_touched(self):
        row = (7, 'dismissal', date(2026, 3, 1), None, 'причина', False)
        with self.assertRaises(service.HrError) as caught:
            self.run_change({'status': 'working', 'period_id': 7}, row)
        self.assertEqual(caught.exception.status, 409)

    def test_status_not_for_this_employment_type(self):
        with self.assertRaises(service.HrError):
            self.run_change({'status': 'sick_leave', 'start': '2026-11-11', 'end': '2026-11-12'}, None)


class ValidationTests(unittest.TestCase):
    def test_company_requisites(self):
        with self.assertRaises(service.HrError) as caught:
            service.clean_company({'name': 'ИП Тест', 'bin': '123', 'iik': 'XX', 'kbe': '1'})
        self.assertEqual(set(caught.exception.fields), {'bin', 'iik', 'kbe'})
        fields = service.clean_company({'name': 'ТОО «Тест»', 'kind': 'too', 'bin': '9001 0130 0015'})
        self.assertEqual((fields['name'], fields['bin']), ('Тест', '900101300015'))

    def test_person_fields(self):
        person = {'birth_date': date(1990, 1, 1), 'gender': 'female'}
        data, contract, errors, warnings = service.clean_person({
            'data': {'iin': '900101400017', 'doc_issued_on': '2999-01-01', 'project': 'Марс', 'day_hours': '30'},
            'contract': {'employment_type': '', 'company_id': None, 'amount': 'abc', 'job_title': ''},
        }, person, date(2026, 11, 10))
        self.assertEqual(set(errors), {'iin', 'doc_issued_on', 'project', 'day_hours', 'employment_type',
                                       'company_id', 'amount', 'job_title'})

    def test_person_fields_ok(self):
        person = {'birth_date': date(1990, 1, 1), 'gender': 'female'}
        data, contract, errors, warnings = service.clean_person({
            'data': {'iin': '900101400013', 'project': 'ЦО', 'schedule_pattern': '5.2', 'day_hours': '8'},
            'contract': {'employment_type': 'ГПХ', 'company_id': '3', 'amount': '250 000', 'job_title': 'HR менеджер'},
        }, person, date(2026, 11, 10))
        self.assertEqual(errors, {})
        self.assertEqual((data['schedule_pattern'], data['day_hours'], contract['employment_type']), ('5/2', 8.0, 'gph'))


def workbook_bytes(rows, header=('Отдел', '№', 'Должность', 'Ф.И.О должностного лица', 'ТД/ГПХ', 'график',
                                  'кол-во рабочих дней план', 'план часов')):
    book = Workbook()
    sheet = book.active
    for column, text in enumerate(header, start=1):
        sheet.cell(row=2, column=column, value=text)
    for index, values in enumerate(rows):
        for column, value in enumerate(values, start=1):
            sheet.cell(row=3 + index * 2, column=column, value=value)
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


class ImporterTests(unittest.TestCase):
    def test_parse_contract(self):
        self.assertEqual(importer.parse_contract('ГПХ ИП Тестова'), ('gph', 'ИП Тестова'))
        self.assertEqual(importer.parse_contract('ТД Taxi Technologies'), ('of', 'Taxi Technologies'))
        self.assertEqual(importer.parse_contract('Ресурс Плюс KZ'), (None, 'Ресурс Плюс KZ'))
        self.assertEqual(importer.parse_contract('ГПХ'), ('gph', ''))

    def test_read_rows_list_by_companies(self):
        content = workbook_bytes([
            ('ЦО', 1, 'HR директор', 'Тестова Анна', 'ГПХ ИП Тестовая', '5/2', 21, 168),
            (None, 2, 'Сисадмин', 'Сидоров Сидр', 'ТД Тест Компани', '2/2', 15, 150),
            ('Регионы', 1, 'Менеджер', 'Петров Пётр', '', None, None, None),
        ])
        _sheet, rows = importer.read_rows(content)
        self.assertEqual([row['group'] for row in rows], ['ЦО', 'ЦО', 'Регионы'])
        self.assertEqual(rows[0]['employment_type'], 'gph')
        self.assertEqual(rows[0]['day_hours'], 8)
        self.assertEqual(rows[1]['day_hours'], 10)
        self.assertEqual(rows[1]['schedule'], '2/2')
        self.assertEqual(importer.detect_mode(rows), 'project')

    def test_match_people(self):
        people = [{'user_id': 1, 'name': 'Тестова Анна Сергеевна'}, {'user_id': 2, 'name': 'Иванов Иван'},
                  {'user_id': 3, 'name': 'Иванов Иван Петрович'}, {'user_id': 4, 'name': 'Сәрсенбай Төлеу'},
                  {'user_id': 5, 'name': 'Петров Пётр Сергеевич'}]
        rows = [{'name': 'Анна Тестова'}, {'name': 'Иванов Иван'}, {'name': 'Иванов Иван Петрович'},
                {'name': 'Нет Такого'}, {'name': 'Сарсенбай Толеу'}, {'name': 'Петров Пётр Иванович'}]
        importer.match_people(rows, people)
        # Тёзка с другим отчеством («Петров Пётр Сергеевич» на строку «… Иванович») не подставляется.
        self.assertEqual([row['state'] for row in rows], ['found', 'ambiguous', 'found', 'missing', 'found', 'missing'])
        self.assertEqual(rows[2]['match']['user_id'], 3)

    def test_name_without_patronymic_in_icore_still_matches(self):
        people = [{'user_id': 7, 'name': 'Петров Пётр'}]
        self.assertEqual([p['user_id'] for p in importer.candidates_by_name('Петров Пётр Иванович', people)], [7])

    def test_alias_company_is_canonical(self):
        self.assertEqual(importer.canonical_company('ТТ'), ('Taxi Technologies', 'taxi technologies'))
        self.assertEqual(importer.canonical_company('ИП "Тестова", ТОО Другая')[1], 'тестова')

    def test_companies_are_resolved_by_key(self):
        rows = [{'company': 'ИП "Тестовая"'}, {'company': 'ТТ'}, {'company': 'Новая Компания'}]
        new = importer.resolve_companies(rows, [{'id': 1, 'name': 'Тестовая', 'kind': 'ip', 'is_active': True},
                                                {'id': 2, 'name': 'Taxi Technologies', 'kind': 'too', 'is_active': True}])
        # «ТТ» в таблицах HR — сокращение Taxi Technologies.
        self.assertEqual([row['company_id'] for row in rows], [1, 2, None])
        self.assertEqual(new, ['Новая Компания'])

    def test_rejects_table_without_fio_column(self):
        with self.assertRaises(importer.ImportError_):
            importer.read_rows(workbook_bytes([('x', 1, 'y', 'z')], header=('a', 'b', 'c', 'd')))


class WiringTests(unittest.TestCase):
    """Раздел подключён к серверу и к порталу: флаги профиля, Blueprint, пункт меню и вид."""

    def test_server_registers_blueprint_and_profile_flags(self):
        source = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        self.assertIn('build_hr_office_blueprint', source)
        self.assertIn('"hr_office_access": hr_office_access', source)
        self.assertIn('"hr_acts_access": hr_acts_access', source)
        self.assertIn('"hr_office_features": hr_office_features', source)

    def test_portal_has_one_section_with_tz_tabs(self):
        """Решение владельца 10.11.2026: «три отдельных раздела делать не нужно, а сделать один единый,
        который будет раздельно отображаться у каждого» — один пункт меню «Кадры», вкладки внутри."""
        source = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
        self.assertIn("import('./components/hr_office/HrOfficeView')", source)
        self.assertEqual(source.count('<span className="sidebar-text">Кадры</span>'), 1)
        for gone in ('>Прием</span>', '>Табель учета рабочего времени</span>', '>АВР</span>',
                     "'hr_intake'", "'hr_timesheet'", "'hr_acts'"):
            self.assertNotIn(gone, source)
        self.assertIn("(requestedViewFromUrl !== 'hr_office' || canAccessHrOfficeSection)", source)
        self.assertIn("handleSidebarViewNavigation(e, 'hr_office')", source)
        self.assertIn('const canAccessHrOfficeForUser = (userLike) => userLike?.hr_office_access === true'
                      ' || userLike?.hr_acts_access === true;', source)

    def test_tabs_are_named_as_in_tz(self):
        """Вкладки — дословно разделы ТЗ: «Прием» (п. 1), «Табель учета рабочего времени» (п. 6), «АВР» (п. 11)."""
        source = (ROOT / 'src' / 'components' / 'hr_office' / 'HrOfficeView.jsx').read_text(encoding='utf-8')
        titles = [line.split("'")[1] for line in source.splitlines() if line.strip().startswith("title: '")]
        self.assertEqual(titles, ['Прием', 'Табель учета рабочего времени', 'АВР'])
        self.assertIn('>Кадры</h1>', source)
        self.assertIn('available.length > 1 && (', source)            # одна вкладка — без переключателя

    def test_history_labels_for_masked_fields(self):
        source = (ROOT / 'src' / 'components' / 'modals' / 'historyFieldLabels.js').read_text(encoding='utf-8')
        for key in ('hr_iin', 'hr_document', 'hr_addresses', 'hr_contract', 'hr_amount'):
            self.assertIn(f'{key}:', source)

    def test_rules_project_list_is_what_ui_offers(self):
        self.assertEqual(rules.PROJECTS[:5], ('ЦО', 'iTaxi Rent', 'Eazy Go', 'Eazy Coffee', 'Регионы'))


if __name__ == '__main__':
    unittest.main()
