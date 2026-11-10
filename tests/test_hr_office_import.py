"""Загрузка таблиц HR в «Прием»: сверка с тем, что уже записано, и запись только подтверждённого.

Просьба владельца 10.11.2026: «если уже такое есть — зачем загружать во второй раз; сверяет,
говорит, что где поменяется, и подтвердить». ФИО в тестах выдуманы."""
import re
import unittest
from io import BytesIO
from unittest import mock

from openpyxl import Workbook

from hr_office import queries, reconcile, rules, service

HR = {'user_id': 900, 'role': 'hr_manager', 'is_hr': True, 'is_super': False, 'is_admin': False,
      'is_supervisor': False, 'department_id': 1, 'headed_department_ids': []}


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


def person(user_id, name, **fields):
    base = {'user_id': user_id, 'name': name, 'role': 'operator', 'status': 'working', 'department_id': 3,
            'department_code': 'hr', 'department_name': 'HR', 'project': None, 'timesheet_group': None,
            'job_title': None, 'employment_type': None, 'company_name': None, 'contract_id': None,
            'contract_company_id': None, 'contract_employment_type': None, 'contract_number': None,
            'contract_signed_on': None, 'contract_liability_number': None, 'contract_amount': None,
            'contract_job_title': None, 'schedule_pattern': None, 'day_hours': None}
    base.update(fields)
    base['project_value'] = rules.project_of(base)
    return base


COMPANY = {'id': 5, 'name': 'Тестовая', 'kind': 'ip', 'is_active': True}
ANNA = person(1, 'Тестова Анна Сергеевна', project='ЦО', job_title='HR директор', employment_type='gph',
              company_name='ИП «Тестовая»', contract_id=11, contract_company_id=5, contract_employment_type='gph',
              contract_number='3/2026', contract_amount=250000, contract_job_title='HR директор',
              schedule_pattern='5/2', day_hours=8.0)
SIDOR = person(2, 'Сидоров Сидр', department_code='op')     # проекта нет — по отделу «Колл-центр»
FILE = workbook_bytes([
    ('ЦО', 1, 'HR директор', 'Тестова Анна', 'ГПХ ИП "Тестовая"', '5/2', 21, 168),
    (None, 2, 'Сисадмин', 'Сидоров Сидр', 'ТД Тест Компани', '2/2', 15, 150),
    (None, 3, 'Курьер', 'Нет Такого', 'ГПХ', '5/2', 21, 168),
])


class FakeDb:
    """Подмена запросов: люди и компании — из списков, запись — в журнал вызовов."""

    def __init__(self, people, companies=(COMPANY,), previous=None):
        self.people = {item['user_id']: dict(item) for item in people}
        self.companies = [dict(item) for item in companies]
        self.previous = previous
        self.calls = []
        self.next_company_id = 77

    def list_people(self, _cursor, *, user_ids=None, **_kwargs):
        items = list(self.people.values())
        if user_ids is not None:
            items = [item for item in items if item['user_id'] in set(user_ids)]
        return [dict(item) for item in items]

    def list_companies(self, _cursor, include_inactive=True):
        return [dict(item) for item in self.companies]

    def save_company(self, _cursor, _company_id, fields, _actor):
        self.calls.append(('company', fields['name'], fields['kind']))
        self.companies.append({'id': self.next_company_id, **fields})
        return self.next_company_id

    def patch(self):
        return mock.patch.multiple(
            queries,
            list_people=self.list_people,
            list_companies=self.list_companies,
            last_import=lambda _cursor, digest: self.previous,
            company_name_taken=lambda _cursor, name, exclude_id=None: False,
            save_company=self.save_company,
            upsert_employee_data=lambda _cursor, user_id, fields, _actor: self.calls.append(('data', user_id, fields)),
            save_contract=lambda _cursor, user_id, fields, _actor: self.calls.append(('contract', user_id, fields)),
            write_history=lambda _cursor, user_id, _actor, field, old, new: self.calls.append(('history', user_id, field)),
            audit=lambda _cursor, **kwargs: self.calls.append(('audit', kwargs)),
        )

    def of(self, kind):
        return [call for call in self.calls if call[0] == kind]


def confirm_all(row):
    # Как клиент: «станет пустым» — следствие, его не подтверждают отдельно.
    return {change['field']: change['old'] for change in row.get('changes') or [] if not change.get('derived')}


def apply_rows(preview, *, overrides=None, skip=()):
    rows = []
    for row in preview['rows']:
        item = {key: row.get(key) for key in ('line', 'name', 'group', 'job_title', 'employment_type', 'company',
                                             'schedule', 'day_hours')}
        item['user_id'] = (row.get('match') or {}).get('user_id')
        item['confirm'] = confirm_all(row)
        item['skip'] = item['user_id'] in skip
        item.update((overrides or {}).get(item['user_id'], {}))
        rows.append(item)
    return rows


class ReconcileTests(unittest.TestCase):
    def test_same_values_are_not_a_change(self):
        """Регистр и пробелы должности, кавычки компании, «ТТ», 8 и 8.0 часов — не разница."""
        now = reconcile.current(person(1, 'А', job_title='HR  директор', employment_type='gph', schedule_pattern='5/2',
                                       day_hours=8, company_name='ТОО Taxi Technologies'), None)
        wanted = reconcile.incoming({'group': 'ЦО', 'job_title': 'hr директор', 'employment_type': 'gph',
                                     'schedule': '5/2', 'day_hours': 8.0}, mode='project', project_for_groups=None,
                                    company={'name': 'Taxi Technologies', 'kind': 'too'})
        self.assertEqual(reconcile.diff(now, wanted), [])

    def test_project_by_department_counts_as_current(self):
        """У кого проект не задан, «Прием» показывает проект по отделу — с ним и сверяем."""
        now = reconcile.current(person(1, 'А', department_code='front_office'), None)
        self.assertEqual(reconcile.diff(now, reconcile.incoming({'group': 'Регионы'}, mode='project',
                                                                project_for_groups=None, company=None)), [])
        change = reconcile.diff(now, reconcile.incoming({'group': 'ЦО'}, mode='project',
                                                        project_for_groups=None, company=None))
        self.assertEqual(change, [{'field': 'project', 'label': 'Проект', 'old': 'Регионы', 'new': 'ЦО'}])

    def test_empty_cell_never_clears(self):
        now = reconcile.current(person(1, 'А', job_title='Менеджер', schedule_pattern='2/2'), None)
        wanted = reconcile.incoming({'group': 'Регионы', 'job_title': '', 'schedule': None}, mode='project',
                                    project_for_groups=None, company=None)
        # Должность и график в таблице пустые — они остаются; меняется только проект.
        self.assertEqual([change['field'] for change in reconcile.diff(now, wanted)], ['project'])

    def test_fill_and_replace(self):
        # Отдел hr — проект по отделу «ЦО», он же выбран для табеля: проект не меняется.
        now = reconcile.current(person(1, 'А', job_title='Курьер'), None)
        wanted = reconcile.incoming({'group': 'HR отдел', 'job_title': 'Менеджер', 'day_hours': 7.5},
                                    mode='group', project_for_groups='ЦО', company=None)
        self.assertEqual(reconcile.diff(now, wanted), [
            {'field': 'timesheet_group', 'label': 'Отдел в табеле', 'old': '', 'new': 'HR отдел'},
            {'field': 'job_title', 'label': 'Должность', 'old': 'Курьер', 'new': 'Менеджер'},
            {'field': 'day_hours', 'label': 'Часов в день', 'old': '', 'new': '7,5'},
        ])

    def test_only_confirmed_and_unchanged_old_is_taken(self):
        changes = [{'field': 'project', 'label': 'Проект', 'old': 'Регионы', 'new': 'ЦО'},
                   {'field': 'job_title', 'label': 'Должность', 'old': '', 'new': 'Менеджер'},
                   {'field': 'schedule_pattern', 'label': 'График', 'old': '5/2', 'new': '2/2'}]
        take, late = reconcile.confirmed_changes(changes, {'project': 'Регионы', 'job_title': '',
                                                           'schedule_pattern': '6/1'})
        self.assertEqual([change['field'] for change in take], ['project', 'job_title'])
        self.assertEqual([change['field'] for change in late], ['schedule_pattern'])   # «было» поменялось
        self.assertEqual(reconcile.confirmed_changes(changes, None), ([], changes))      # без подтверждения — ничего


class PreviewTests(unittest.TestCase):
    def test_rows_are_compared_with_icore(self):
        db = FakeDb([ANNA, SIDOR])
        with db.patch():
            preview = service.import_preview(None, HR, FILE)
        anna, sidor, nobody = preview['rows']
        self.assertEqual(anna['status'], 'same')
        self.assertEqual(anna['changes'], [])
        self.assertIn('HR директор', anna['now'])
        self.assertEqual(sidor['status'], 'changed')
        self.assertEqual([(c['field'], c['old'], c['new']) for c in sidor['changes']], [
            ('project', 'Колл-центр', 'ЦО'), ('job_title', '', 'Сисадмин'), ('employment_type', '', 'ТД'),
            ('company', '', 'ТОО «Тест Компани»'), ('schedule_pattern', '', '2/2'), ('day_hours', '', '10')])
        self.assertTrue(sidor['changes'][3]['new_company'])
        self.assertEqual(nobody['state'], 'missing')
        self.assertEqual(preview['counts'], {'changed': 1, 'same': 1, 'ambiguous': 0, 'missing': 1, 'fields': 6})
        self.assertRegex(preview['file_sha256'], r'^[0-9a-f]{64}$')
        self.assertIsNone(preview['previous'])
        self.assertEqual(db.calls, [])                       # предпросмотр ничего не пишет

    def test_same_file_again_is_recognised(self):
        from datetime import datetime
        db = FakeDb([ANNA, SIDOR], previous={'at': datetime(2026, 11, 10, 15, 42), 'actor_name': 'Тестова Арай'})
        with db.patch():
            preview = service.import_preview(None, HR, FILE)
        self.assertEqual(preview['previous'], {'at': '2026-11-10T15:42:00', 'actor_name': 'Тестова Арай'})

    def test_namesakes_get_their_own_comparison(self):
        twin_a = person(3, 'Сидоров Сидр Петрович', job_title='Сисадмин', employment_type='of')
        twin_b = person(4, 'Сидоров Сидр Иванович')
        db = FakeDb([ANNA, twin_a, twin_b])
        with db.patch():
            row = service.import_preview(None, HR, FILE)['rows'][1]
        self.assertEqual(row['state'], 'ambiguous')
        by_id = {candidate['user_id']: candidate for candidate in row['candidates']}
        self.assertNotIn('job_title', [c['field'] for c in by_id[3]['changes']])
        self.assertIn('job_title', [c['field'] for c in by_id[4]['changes']])

    def test_timesheet_file_takes_project_from_choice(self):
        content = workbook_bytes([('HR отдел', 1, 'HR директор', 'Тестова Анна', 'ГПХ ИП Тестовая', '5/2', 21, 168)])
        db = FakeDb([ANNA])
        with db.patch():
            preview = service.import_preview(None, HR, content, project='Регионы')
        self.assertEqual((preview['mode'], preview['project']), ('group', 'Регионы'))
        self.assertEqual([(c['field'], c['old'], c['new']) for c in preview['rows'][0]['changes']],
                         [('project', 'ЦО', 'Регионы'), ('timesheet_group', '', 'HR отдел')])


class ApplyTests(unittest.TestCase):
    def preview(self, db):
        with db.patch():
            return service.import_preview(None, HR, FILE)

    def apply(self, db, preview, **kwargs):
        payload = {'mode': preview['mode'], 'project': preview['project'], 'create_companies': True,
                   'file_name': 'Список.xlsx', 'sheet': ' 2026', 'file_sha256': preview['file_sha256'],
                   'rows': apply_rows(preview, **kwargs)}
        with db.patch():
            return service.import_apply(None, HR, payload)

    def test_writes_only_what_changes_and_logs_everyone(self):
        db = FakeDb([ANNA, SIDOR])
        updates, result = self.apply(db, self.preview(db))
        self.assertEqual((result['changed'], result['fields'], result['same'], result['missing']), (1, 6, 1, 1))
        # Анна совпала целиком — её строки ничего не пишут.
        self.assertTrue(all(call[1] == 2 for call in db.of('data') + db.of('contract')))
        self.assertEqual(db.of('data'), [('data', 2, {'project': 'ЦО', 'schedule_pattern': '2/2', 'day_hours': 10.0})])
        self.assertEqual(db.of('company'), [('company', 'Тест Компани', 'too')])
        contract = db.of('contract')[0][2]
        self.assertEqual((contract['company_id'], contract['employment_type'], contract['job_title']),
                         (77, 'of', 'Сисадмин'))
        self.assertEqual(updates, {2: {'employment_type': 'of', 'company_name': 'ТОО «Тест Компани»',
                                       'job_title': 'Сисадмин'}})
        (_, entry), = db.of('audit')
        details = entry['details']
        self.assertEqual(entry['action'], 'import')
        self.assertEqual([person['user_id'] for person in details['people']], [2])
        self.assertEqual(details['people'][0]['changes'][0], {'field': 'project', 'label': 'Проект',
                                                              'old': 'Колл-центр', 'new': 'ЦО'})
        self.assertEqual(details['same'], [{'user_id': 1, 'name': 'Тестова Анна Сергеевна'}])
        self.assertEqual(details['missing'], ['Нет Такого'])
        self.assertEqual(details['companies_created'], ['Тест Компани'])
        self.assertRegex(details['file_sha256'], r'^[0-9a-f]{64}$')
        self.assertIn('изменено у 1 сотрудника (6 полей)', entry['summary'])

    def test_stale_old_value_is_not_overwritten(self):
        """HR видел «было: Регионы», а к записи там уже другое — поле не пишется, оно в расхождениях."""
        db = FakeDb([ANNA, SIDOR])
        preview = self.preview(db)
        stale = dict(confirm_all(preview['rows'][1]), project='Регионы')
        _updates, result = self.apply(db, preview, overrides={2: {'confirm': stale}})
        self.assertEqual(result['conflicts'], [{'user_id': 2, 'name': 'Сидоров Сидр', 'fields': ['Проект']}])
        self.assertNotIn('project', db.of('data')[0][2])

    def test_second_press_writes_nothing(self):
        """Повторное «Подтвердить» (или та же таблица ещё раз) — всё уже совпадает: ни записи, ни журнала."""
        db = FakeDb([ANNA, person(2, 'Сидоров Сидр', project='ЦО', job_title='Сисадмин', employment_type='of',
                                  company_name='ТОО «Тест Компани»', schedule_pattern='2/2', day_hours=10)],
                    companies=(COMPANY, {'id': 77, 'name': 'Тест Компани', 'kind': 'too', 'is_active': True}))
        preview = self.preview(db)
        self.assertEqual(preview['counts']['changed'], 0)
        updates, result = self.apply(db, preview)
        self.assertEqual((updates, result['changed'], result['same']), ({}, 0, 2))
        self.assertEqual(db.calls, [])

    def test_unchecked_rows_and_no_new_companies(self):
        db = FakeDb([ANNA, SIDOR])
        preview = self.preview(db)
        _updates, result = self.apply(db, preview, skip=(2,))
        self.assertEqual((result['changed'], result['skipped']), (0, 1))
        self.assertEqual(db.calls, [])
        db = FakeDb([ANNA, SIDOR])
        with db.patch():
            payload = {'mode': 'project', 'create_companies': False, 'rows': apply_rows(self.preview(db))}
            service.import_apply(None, HR, payload)
        self.assertEqual(db.of('company'), [])               # не заводим — и компания не меняется
        self.assertIsNone(db.of('contract')[0][2]['company_id'])

    def test_unpicked_namesakes_are_not_called_missing(self):
        """Тёзки, между которыми не выбрали, в журнале — отдельно от «нет в iCORE»."""
        db = FakeDb([ANNA, SIDOR])
        preview = self.preview(db)
        rows = apply_rows(preview)
        rows.append({'line': 99, 'name': 'Петров Пётр', 'state': 'ambiguous', 'user_id': None, 'confirm': {}})
        with db.patch():
            _updates, result = service.import_apply(None, HR, {'mode': 'project', 'create_companies': True, 'rows': rows})
        self.assertEqual((result['missing'], result['unresolved']), (1, 1))
        (_, entry), = db.of('audit')
        self.assertEqual((entry['details']['missing'], entry['details']['unresolved']), (['Нет Такого'], ['Петров Пётр']))

    def test_rows_without_confirmation_are_not_written(self):
        db = FakeDb([ANNA, SIDOR])
        preview = self.preview(db)
        _updates, result = self.apply(db, preview, overrides={2: {'confirm': None}})
        self.assertEqual(result['changed'], 0)
        self.assertEqual(len(result['conflicts'][0]['fields']), 6)
        self.assertEqual(db.calls, [])


class ReviewFindingsTests(unittest.TestCase):
    """Находки разбора 10.11.2026: каждое — сценарий, в котором загрузка писала не то, что показала."""

    def run_upload(self, db, content=FILE, project=None, create=True, overrides=None):
        with db.patch():
            preview = service.import_preview(None, HR, content, project=project)
            payload = {'mode': preview['mode'], 'project': preview['project'], 'create_companies': create,
                       'rows': apply_rows(preview, overrides=overrides)}
            updates, result = service.import_apply(None, HR, payload)
        return preview, updates, result

    def test_two_rows_for_one_person_are_not_written(self):
        """Вторая строка считалась по снимку до записи первой и откатывала её — теперь не пишется ни одна."""
        content = workbook_bytes([
            ('ЦО', 1, 'Сисадмин', 'Сидоров Сидр Петрович', 'ТД ИП "Тестовая"', '5/2', 21, 168),
            ('ЦО', 2, 'Курьер', 'Сидоров Сидр Иванович', '', None, None, None),
        ])
        db = FakeDb([person(2, 'Сидоров Сидр', contract_id=21, contract_company_id=5, contract_employment_type='gph',
                            contract_number='3/2026', contract_amount=250000, contract_job_title='Оператор')])
        _preview, updates, result = self.run_upload(db, content)
        self.assertEqual((result['changed'], len(result['duplicates'])), (0, 2))
        self.assertEqual((updates, db.of('contract'), db.of('data')), ({}, [], []))

    def test_new_contract_shows_and_logs_what_it_drops(self):
        """Смена вида оформления — новый договор: номер, дата и сумма прежнего не переходят, и сверка
        с журналом это говорят, а не молчат."""
        from datetime import date
        anna = dict(ANNA, contract_signed_on=date(2026, 2, 1), contract_liability_number='3/2026-МО')
        content = workbook_bytes([('ЦО', 1, 'HR директор', 'Тестова Анна', 'ТД ИП "Тестовая"', '5/2', 21, 168)])
        db = FakeDb([anna])
        preview, _updates, result = self.run_upload(db, content)
        lines = {change['field']: change for change in preview['rows'][0]['changes']}
        self.assertEqual(lines['employment_type']['new'], 'ТД')
        self.assertEqual((lines['contract_number']['old'], lines['contract_number']['new']), ('3/2026', ''))
        self.assertEqual(lines['contract_signed_on']['old'], '01.02.2026')
        self.assertTrue(lines['contract_amount']['hidden'])
        self.assertNotIn('250', repr(lines['contract_amount']))
        self.assertEqual(preview['counts']['fields'], 1)                 # «станет пустым» — не поле HR
        contract = db.of('contract')[0][2]
        self.assertEqual((contract['employment_type'], contract['number'], contract['amount']), ('of', None, None))
        (_, entry), = db.of('audit')
        logged = [change['field'] for change in entry['details']['people'][0]['changes']]
        self.assertEqual(logged, ['employment_type', 'contract_number', 'contract_signed_on', 'contract_amount'])
        self.assertEqual(result['fields'], 1)

    def test_job_only_keeps_the_contract_number(self):
        """Договор без компании, а компания — текстом карточки: правка одной должности не заводит
        новый договор и не теряет номер и сумму."""
        lonely = person(3, 'Петров Пётр', employment_type='gph', company_name='ИП Тестовая', contract_id=31,
                        contract_company_id=None, contract_employment_type='gph', contract_number='9/2026',
                        contract_amount=100000, contract_job_title='Курьер')
        content = workbook_bytes([('ЦО', 1, 'Старший курьер', 'Петров Пётр', '', None, None, None)])
        db = FakeDb([lonely])
        preview, _updates, _result = self.run_upload(db, content)
        self.assertEqual([change['field'] for change in preview['rows'][0]['changes']], ['job_title'])
        contract = db.of('contract')[0][2]
        self.assertEqual((contract['company_id'], contract['number'], contract['amount'], contract['job_title']),
                         (None, '9/2026', 100000, 'Старший курьер'))

    def test_job_without_contract_goes_only_to_the_card(self):
        """Нет договора, нет ни вида, ни компании — пустой договор не заводится (иначе «Прием» перестал
        бы писать «нет: договор»), должность уходит в карточку «Учета сотрудников»."""
        content = workbook_bytes([('ЦО', 1, 'Курьер', 'Сидоров Сидр', '', None, None, None)])
        db = FakeDb([person(2, 'Сидоров Сидр')])
        _preview, updates, _result = self.run_upload(db, content)
        self.assertEqual(db.of('contract'), [])
        self.assertEqual(updates, {2: {'job_title': 'Курьер'}})

    def test_archived_or_too_long_company_is_not_offered(self):
        archived = {'id': 8, 'name': 'Архивная', 'kind': 'too', 'is_active': False}
        long_name = 'Очень ' * 40 + 'Длинная'
        content = workbook_bytes([
            ('ЦО', 1, 'HR директор', 'Тестова Анна', 'ГПХ ТОО Архивная', '5/2', 21, 168),
            ('ЦО', 2, 'Сисадмин', 'Сидоров Сидр', f'ТД ТОО {long_name}', None, None, None),
        ])
        db = FakeDb([ANNA, SIDOR], companies=(COMPANY, archived))
        preview, _updates, _result = self.run_upload(db, content)
        anna, sidor = preview['rows']
        self.assertEqual(anna['status'], 'same')
        self.assertIn('не действует', anna['notes'][0])
        self.assertNotIn('company', [change['field'] for change in sidor['changes']])
        self.assertIn('длиннее 200', sidor['notes'][0])
        self.assertEqual(db.of('company'), [])
        self.assertTrue(all(call[2]['company_id'] != 8 for call in db.of('contract')))

    def test_company_created_by_the_upload_is_not_a_false_conflict(self):
        """Строка выше заводит «Новую» — у строки ниже, где «Новая» записана текстом карточки,
        «было» не меняется посреди записи и подтверждённое пишется."""
        content = workbook_bytes([
            ('ЦО', 1, 'HR директор', 'Тестова Анна', 'ГПХ ИП Новая', '5/2', 21, 168),
            ('ЦО', 2, 'Сисадмин', 'Сидоров Сидр', 'ГПХ ИП "Тестовая"', None, None, None),
        ])
        bota = person(2, 'Сидоров Сидр', company_name='ИП Новая', employment_type='gph', project='ЦО',
                      job_title='Сисадмин')
        db = FakeDb([ANNA, bota])
        _preview, _updates, result = self.run_upload(db, content)
        self.assertEqual(result['conflicts'], [])
        self.assertEqual(result['changed'], 2)

    def test_tt_in_the_card_is_taxi_technologies(self):
        tt = {'id': 6, 'name': 'Taxi Technologies', 'kind': 'too', 'is_active': True}
        content = workbook_bytes([('ЦО', 1, 'Сисадмин', 'Сидоров Сидр', 'ТД ТТ', None, None, None)])
        timur = person(2, 'Сидоров Сидр', company_name='ТТ', employment_type='of', project='ЦО', job_title='Сисадмин')
        db = FakeDb([timur], companies=(COMPANY, tt))
        preview, _updates, _result = self.run_upload(db, content)
        self.assertEqual(preview['rows'][0]['status'], 'same')
        # В справочнике её ещё нет: «ТТ» карточки и «ТТ» таблицы — та же компания, заводить нечего.
        db = FakeDb([timur])
        preview, _updates, _result = self.run_upload(db, content)
        self.assertEqual(preview['rows'][0]['status'], 'same')
        self.assertEqual(db.of('company'), [])

    def test_timesheet_again_keeps_its_project(self):
        """Тот же табель второй раз: проект — тот, с которым его загружали, а не «ЦО» (иначе весь КЦ
        «поменял бы проект», и HR, поверив сверке, перевёл бы его в ЦО)."""
        from datetime import datetime
        content = workbook_bytes([('Линия', 1, 'HR директор', 'Тестова Анна', 'ГПХ ИП Тестовая', '5/2', 21, 168)])
        kc = dict(ANNA, project='Колл-центр', project_value='Колл-центр', timesheet_group='Линия')
        db = FakeDb([kc], previous={'at': datetime(2026, 11, 10, 15, 0), 'actor_name': 'HR', 'project': 'Колл-центр'})
        with db.patch():
            preview = service.import_preview(None, HR, content)
        self.assertEqual((preview['project'], preview['counts']['changed']), ('Колл-центр', 0))
        db = FakeDb([kc])                                     # файла не было — проект большинства его людей
        with db.patch():
            preview = service.import_preview(None, HR, content)
        self.assertEqual(preview['project'], 'Колл-центр')
        with db.patch():
            preview = service.import_preview(None, HR, content, project='ЦО')
        self.assertEqual(preview['project'], 'ЦО')             # выбор HR — главнее

    def test_failed_cards_are_named(self):
        db = FakeDb([ANNA, SIDOR])
        _preview, updates, result = self.run_upload(db)
        self.assertEqual(result['names'], {2: 'Сидоров Сидр'})
        self.assertIn(2, updates)


class CardJournalTests(unittest.TestCase):
    """Сохранение карточки в «Приеме»: история и журнал — без цифр ИИН и без ложных правок."""

    def save(self, card, payload):
        calls = []
        companies = [COMPANY]
        with mock.patch.multiple(
                queries,
                get_person=lambda _cursor, user_id, include_fired=True: dict(card),
                get_company=lambda _cursor, company_id: next((c for c in companies if c['id'] == int(company_id)), None),
                list_companies=lambda _cursor, include_inactive=True: list(companies),
                upsert_employee_data=lambda _cursor, user_id, fields, _actor: calls.append(('data', fields)),
                write_history=lambda _cursor, user_id, _actor, field, old, new: calls.append(('history', field, old, new)),
                save_contract=lambda _cursor, user_id, fields, _actor: calls.append(('contract', fields)),
                next_number=lambda _cursor, company_id, year, kind: 4,
                audit=lambda _cursor, **kwargs: calls.append(('audit', kwargs))):
            service.save_person(None, HR, card['user_id'], payload, __import__('datetime').date(2026, 11, 10))
        return calls

    def test_iin_history_has_no_digits(self):
        from datetime import date
        card = dict(ANNA, birth_date=date(1990, 1, 1), gender='female', iin='900101400021')
        calls = self.save(card, {'data': {'iin': '900101400013'}})
        history = [call for call in calls if call[0] == 'history' and call[1] == 'hr_iin']
        self.assertEqual(history, [('history', 'hr_iin', None, 'изменён')])
        logged = repr([call for call in calls if call[0] in ('history', 'audit')])
        for digits in ('900101400013', '900101400021', '0013', '0021'):
            self.assertNotIn(digits, logged, 'в историю или журнал ушли цифры ИИН')

    def test_default_document_type_is_not_an_edit(self):
        calls = self.save(dict(ANNA, doc_type=None), {'data': {'doc_type': 'id_card', 'project': 'Регионы'}})
        self.assertNotIn('hr_document', [call[1] for call in calls if call[0] == 'history'])
        (_, entry), = [call for call in calls if call[0] == 'audit']
        self.assertEqual([change['label'] for change in entry['details']['changes']], ['Проект'])

    def test_first_contract_logs_only_real_differences(self):
        """Договора не было, карточка уже показывала ГПХ / ИП «Тестовая» / HR директор: в журнале —
        только номер и дата от автонумерации, а не «— → ГПХ» про то, что не менялось."""
        card = person(7, 'Тестова Анна', employment_type='gph', company_name='ИП «Тестовая»', job_title='HR директор')
        calls = self.save(card, {'contract': {'employment_type': 'gph', 'company_id': 5, 'job_title': 'HR директор',
                                              'auto_number': True}})
        (_, entry), = [call for call in calls if call[0] == 'audit']
        self.assertEqual([change['label'] for change in entry['details']['changes']], ['Номер договора', 'Дата договора'])


class WiringTests(unittest.TestCase):
    def test_act_journal_has_no_amount(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / 'hr_office' / 'service.py').read_text(encoding='utf-8')
        audit = source[source.index("section='acts', action='act'"):]
        self.assertNotIn('money_text', audit[:audit.index('acts.append')])

    def test_company_name_is_limited(self):
        with self.assertRaises(service.HrError) as caught:
            service.clean_company({'name': 'Т' * 201, 'kind': 'too'})
        self.assertIn('name', caught.exception.fields)

    def test_preview_route_passes_project(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / 'hr_office' / 'routes.py').read_text(encoding='utf-8')
        self.assertIn("project=request.form.get('project')", source)
        self.assertTrue(re.search(r"last_import\(cursor, digest\)", (Path(__file__).resolve().parents[1]
                                                                     / 'hr_office' / 'service.py').read_text(encoding='utf-8')))


if __name__ == '__main__':
    unittest.main()
