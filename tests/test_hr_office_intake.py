"""«Прием», ТЗ HR «iCore 4», п. 2–4: данные для документов вводит тот, кто заводит учётку; вид
оформления и компанию выбирает кадровик (по умолчанию п. 3); документы — на виду в карточке.

ФИО и ИИН в тестах выдуманы: 900101400013 — собранный по правилам тестовый ИИН."""
import re
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from hr_office import queries, rules, schema, service

ROOT = Path(__file__).resolve().parents[1]
BIRTH = date(1990, 1, 1)


class CleanIntakeTests(unittest.TestCase):
    def test_nothing_entered_is_nothing(self):
        self.assertEqual(service.clean_intake({}), ({}, {}, []))
        self.assertEqual(service.clean_intake({'doc_type': 'id_card', 'iin': '  '}), ({}, {}, []))
        self.assertEqual(service.clean_intake(None), ({}, {}, []))

    def test_valid_data_is_taken_and_blanks_dropped(self):
        data, errors, warnings = service.clean_intake(
            {'iin': '900101400013', 'doc_number': '123 456 789', 'doc_issuer': 'МВД РК', 'doc_issued_on': '2020-05-01',
             'registration_address': 'г. Алматы, ул. Тестовая, 1', 'actual_address': '', 'citizenship': 'Казахстан'},
            birth_date=BIRTH, gender='female', today=date(2026, 11, 10))
        self.assertEqual((errors, warnings), ({}, []))
        self.assertEqual(data['iin'], '900101400013')
        self.assertEqual(data['doc_type'], 'id_card')              # номер без вида — удостоверение
        self.assertEqual(data['doc_number'], '123456789')
        self.assertEqual(data['doc_issued_on'], date(2020, 5, 1))
        self.assertNotIn('actual_address', data)                    # пустое не пишется

    def test_only_document_fields_are_taken(self):
        """Через форму создания учётки — только данные документа: проект, часы и прочее табельное
        здесь не записать (их ведёт кадровик в «Приеме»)."""
        data, errors, _warnings = service.clean_intake(
            {'iin': '900101400013', 'project': 'ЦО', 'day_hours': '8', 'schedule_pattern': '2/2', 'amount': '1'},
            birth_date=BIRTH, gender='female')
        self.assertEqual((data, errors), ({'iin': '900101400013'}, {}))

    def test_errors_are_named_for_the_creation_window(self):
        _data, errors, _warnings = service.clean_intake(
            {'iin': '123', 'doc_issued_on': '2030-01-01'}, birth_date=BIRTH, gender='female', today=date(2026, 11, 10))
        self.assertEqual(set(errors), {'iin', 'doc_issued_on'})
        text = service.intake_error_text(errors)
        self.assertTrue(text.startswith('Проверьте «Документы для приема» на вкладке «Данные»'))
        self.assertIn('ИИН — ', text)
        self.assertIn('Дата выдачи — ', text)

    def test_iin_against_birth_date_is_a_warning(self):
        _data, errors, warnings = service.clean_intake({'iin': '900101400013'}, birth_date=date(1991, 2, 3),
                                                       gender='female')
        self.assertEqual(errors, {})
        self.assertIn('дат', warnings[0])


class SaveIntakeTests(unittest.TestCase):
    def test_writes_data_and_logs_without_values(self):
        calls = []
        with mock.patch.multiple(
                queries,
                upsert_employee_data=lambda _c, user_id, fields, actor: calls.append(('data', user_id, fields)),
                write_history=lambda _c, user_id, actor, field, old, new: calls.append(('history', field, old, new)),
                audit=lambda _c, **kwargs: calls.append(('audit', kwargs))):
            labels = service.save_intake(None, 42, {'iin': '900101400013', 'doc_type': 'id_card', 'doc_number': '123456789',
                                                    'registration_address': 'г. Алматы, ул. Тестовая, 1'}, 7)
        self.assertEqual(labels, ['ИИН', 'Удостоверение личности', 'Адрес регистрации'])
        self.assertEqual([call[1] for call in calls if call[0] == 'history'], ['hr_iin', 'hr_document', 'hr_addresses'])
        (_, entry), = [call for call in calls if call[0] == 'audit']
        self.assertEqual((entry['section'], entry['action'], entry['user_id'], entry['actor_id']), ('intake', 'intake', 42, 7))
        self.assertIn('внесены данные для документов: ИИН, удостоверение личности, адрес регистрации', entry['summary'])
        self.assertTrue(all(change['hidden'] and change['note'] == 'внесено' for change in entry['details']['changes']))
        logged = repr([call for call in calls if call[0] in ('history', 'audit')])
        for secret in ('900101400013', '0013', '123456789', 'Тестовая'):
            self.assertNotIn(secret, logged)

    def test_nothing_to_save(self):
        self.assertEqual(service.save_intake(None, 42, {}, 7), [])


class FakeCursor:
    def __init__(self, names):
        self.names = list(names)
        self.inserted = []

    def execute(self, sql, params=None):
        if sql.startswith('INSERT INTO hr_companies'):
            self.inserted.append(params)
            self.names.append(params[0])

    def fetchall(self):
        return [(name,) for name in self.names]


class DefaultCompaniesTests(unittest.TestCase):
    """ТЗ п. 3: компании умолчаний есть в справочнике всегда — на проде он при выкладке пуст."""

    def test_missing_defaults_are_added_once(self):
        cursor = FakeCursor(['ИП «Eazy Go Rent»', 'Тест Компани'])
        self.assertEqual(schema.seed_default_companies(cursor), ['GaBa'])
        self.assertEqual(cursor.inserted, [('GaBa', 'ip')])
        self.assertEqual(schema.seed_default_companies(cursor), [])         # второй запуск — ничего

    def test_defaults_point_to_seeded_companies(self):
        keys = {rules.company_key(name) for name, _kind in schema.DEFAULT_COMPANIES}
        for code in ('op', 'szov', 'tez'):
            self.assertIn(rules.default_hiring(code)[1], keys)
            self.assertTrue(rules.DEFAULT_HIRING_FOR[code])


class SummaryTests(unittest.TestCase):
    def person(self, **fields):
        base = {'user_id': 5, 'name': 'Новикова Нина', 'role': 'operator', 'department_id': 2, 'department_code': 'op',
                'department_name': 'ОП', 'project_value': 'Колл-центр', 'contract_id': None, 'company_name': None,
                'employment_type': None, 'contract_employment_type': None, 'contract_company_id': None,
                'job_title': None, 'contract_job_title': None, 'hire_date': date(2026, 11, 9)}
        base.update(fields)
        return base

    def test_new_hire_shows_the_default(self):
        egr = {'id': 1, 'name': 'Eazy Go Rent', 'kind': 'ip', 'is_active': True}
        summary = service.person_summary(self.person(), {1: egr}, queries.company_index([egr]))
        self.assertFalse(summary['has_contract'])
        self.assertEqual(summary['default_hiring'], {'employment_type': 'gph', 'company': 'ИП «Eazy Go Rent»',
                                                     'for': 'ОП и СЗоВ'})

    def test_no_default_once_there_is_a_company_or_contract(self):
        index = queries.company_index([])
        self.assertIsNone(service.person_summary(self.person(company_name='ИП Алиева'), {}, index)['default_hiring'])
        self.assertIsNone(service.person_summary(self.person(contract_id=3), {}, index)['default_hiring'])
        self.assertIsNone(service.person_summary(self.person(department_code='hr'), {}, index)['default_hiring'])


class AddUserEndpointTests(unittest.TestCase):
    """Настоящая ручка создания учётки (bot_schedule2.add_user) на подменённой базе — обвязка
    tests/test_analytics_department_direction.py."""

    def payload(self, **intake):
        from tests.test_analytics_department_direction import SZOV, _operator
        return _operator(department_id=SZOV, direction_id=70, group_id=10, birth_date='1990-01-01',
                         gender='female', hr_intake=intake)

    def test_bad_iin_stops_creation(self):
        from tests.test_analytics_department_direction import HR_MANAGER, _add_user
        status, body, db = _add_user(self.payload(iin='123'), HR_MANAGER)
        self.assertEqual(status, 400)
        self.assertIn('«Документы для приема»', body['error'])
        self.assertIn('iin', body['fields'])
        self.assertEqual(db.created, [])                     # учётка без данных не осталась

    def test_data_is_saved_right_after_creation(self):
        import contextlib
        from tests import test_analytics_department_direction as harness
        saved = []
        with mock.patch.object(harness._Db, '_get_cursor', create=True, new=lambda self: contextlib.nullcontext('cur')), \
                mock.patch.object(service, 'save_intake',
                                  side_effect=lambda cursor, user_id, data, actor: saved.append((user_id, data, actor))):
            status, body, db = harness._add_user(self.payload(iin='900101400013', doc_number='012345678',
                                                              registration_address='г. Алматы, ул. Тестовая, 1'),
                                                 harness.HR_MANAGER)
        self.assertEqual((status, body['intake_saved'], body['intake_warnings']), (200, True, []))
        (user_id, data, actor), = saved
        self.assertEqual((user_id, actor), (body['id'], harness.HR_MANAGER['id']))
        self.assertEqual(set(data), {'iin', 'doc_type', 'doc_number', 'registration_address'})
        self.assertNotIn('hr_intake', db.created[0])          # в users ничего из этого не уходит

    def test_without_document_data_nothing_changes(self):
        from tests.test_analytics_department_direction import HR_MANAGER, SZOV, _add_user, _operator
        status, body, _db = _add_user(_operator(department_id=SZOV, direction_id=70, group_id=10), HR_MANAGER)
        self.assertEqual((status, body['intake_saved']), (200, None))


class WiringTests(unittest.TestCase):
    def test_account_creation_takes_document_data(self):
        """add_user: проверка данных для документов — ДО создания учётки, запись — после."""
        source = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        body = source.split('def add_user():', 1)[1].split("@app.route('/api/admin/directions'", 1)[0]
        check, insert, save = (body.index('hr_office_service.clean_intake('), body.index('user_id = db.create_user('),
                               body.index('hr_office_service.save_intake('))
        self.assertLess(check, insert)
        self.assertLess(insert, save)
        self.assertIn('"intake_saved": intake_saved', body)

    def test_create_form_sends_and_shows_document_data(self):
        app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
        self.assertIn('hr_intake: editedUser.hr_intake || undefined', app)
        modal = (ROOT / 'src' / 'components' / 'modals' / 'UserEditModal.jsx').read_text(encoding='utf-8')
        create = modal[modal.index('{isCreateMode &&('):modal.index('{!isCreateMode && (')]
        self.assertIn('data-hr-intake', create)
        for key in ('iin', 'doc_number', 'doc_issuer', 'doc_issued_on', 'citizenship', 'birth_place',
                    'registration_address', 'actual_address'):
            self.assertIn(f"setIntake('{key}'", create)
        # Пилот: блок «Документы для приема» — только учёткам пилота (hrFeatures).
        self.assertLess(create.index('{hrFeatures && ('), create.index('data-hr-intake'))
        # Вид оформления и компанию при создании выбирает кадровик в «Приеме» — не свободным текстом;
        # вне пилота окно создания — как до ТЗ, с полями «Наименование ТОО/ИП» и «Оформлен как».
        note = create.index('data-hr-hiring-note')
        self.assertLess(create.index('{hrFeatures ? ('), note)
        self.assertLess(note, create.index('Наименование ТОО/ИП'))
        edit = modal[modal.index('{!isCreateMode && ('):]
        self.assertIn('Наименование ТОО/ИП', edit)                           # в заведённой карточке — как было

    def test_frontend_defaults_mirror_the_server(self):
        source = (ROOT / 'src' / 'utils' / 'employmentStatus.js').read_text(encoding='utf-8')
        for code, (kind, key) in rules.DEFAULT_HIRING.items():
            row = re.search(rf"{code}: \{{ type: '([^']+)', company: '([^']+)', for: '([^']+)' \}}", source)
            self.assertIsNotNone(row, code)
            self.assertEqual(row.group(1), rules.EMPLOYMENT_SHORT[kind])
            self.assertEqual(rules.company_key(row.group(2)), key)
            self.assertEqual(row.group(3), rules.DEFAULT_HIRING_FOR[code])

    def test_intake_card_shows_documents_first(self):
        card = (ROOT / 'src' / 'components' / 'hr_office' / 'HrPersonSheet.jsx').read_text(encoding='utf-8')
        self.assertLess(card.index('data-hr-documents'), card.index('>Оформление<'))
        self.assertIn('data-hr-default-hiring', card)
        self.assertIn('data-hr-intake-by', card)
        tab = (ROOT / 'src' / 'components' / 'hr_office' / 'HrHiringTab.jsx').read_text(encoding='utf-8')
        self.assertIn("{ value: 'new', label: 'Ждут оформления' }", tab)


if __name__ == '__main__':
    unittest.main()
