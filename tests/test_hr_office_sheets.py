"""Выгрузки «Кадров» один в один с файлами HR (просьба владельца 12.11.2026) и страницы списков.

Табель — как лист « 2026» «Приложения 2 - Табель», список по компаниям — как лист « 2026»
«Приложения 4»: оформление каждой ячейки берётся у ячейки-образца шаблона (templates/tabel.xlsx,
templates/companies.xlsx), в шаблонах — ни одного человека. Люди здесь выдуманные."""
import contextlib
import io
import unittest
import zipfile
from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest import mock

from flask import Flask, jsonify
from openpyxl import load_workbook

from hr_office import documents, rules, service, sheets, timesheet
from hr_office.routes import build_hr_office_blueprint

OCTOBER = rules.month_days(date(2026, 10, 1), date(2026, 10, 31))
NOVEMBER = rules.month_days(date(2026, 11, 1), date(2026, 11, 30))
HOLIDAYS = {date(2026, 10, 25), date(2026, 10, 26)}


def days_of(month_days):
    return [{'date': d.isoformat(), 'day': d.day, 'weekday': rules.WEEKDAYS_UPPER[d.weekday()],
             'weekend': d.weekday() >= 5, 'holiday': d in HOLIDAYS} for d in month_days]


def person_row(month_days=OCTOBER, *, hire=None, note=None, facts=None, periods=(), name='Тестова Анна Сергеевна'):
    person = {'user_id': 7, 'name': name, 'status': 'working', 'hire_date': hire}
    plan, _source, _pattern = timesheet.person_plan(person, month_days, HOLIDAYS, None, set(), facts or {},
                                                    month_days[-1], periods)
    built = timesheet.build_person_row(person, month_days, holidays=HOLIDAYS, until=month_days[-1],
                                       fact_until=month_days[-1], plan=plan, plan_source='calendar', pattern='5/2',
                                       periods=periods, facts=facts or {}, edits={}, note=note)
    return dict(built, job_title='HR менеджер', contract_label='ГПХ ИП «Тест»')


def workbook(groups, month_days=OCTOBER, title='Октябрь 2026'):
    model = {'title': title, 'project': 'ЦО', 'days': days_of(month_days), 'groups': groups}
    return load_workbook(BytesIO(timesheet.workbook(model)))


def fill_rgb(cell):
    color = cell.fill.fgColor
    return color.rgb if color.type == 'rgb' else (f'theme{color.theme}', round(color.tint or 0, 2))


class TemplatesHoldNoPeopleTests(unittest.TestCase):
    """Шаблоны собраны из файлов HR с реальными людьми — в репозиторий (публичный) уходит только оформление."""

    def test_only_layout_left(self):
        for path, title in ((sheets.TABEL_TEMPLATE, 'Табель'), (sheets.COMPANIES_TEMPLATE, 'Список')):
            book = load_workbook(path)
            self.assertEqual(book.sheetnames, [title], path)   # лист «Октябрь» с окладами убран
            self.assertEqual(book.properties.creator, 'iCORE')
            sheet = book.active
            for row in sheet.iter_rows():
                for cell in row:
                    self.assertIsNone(getattr(cell, 'comment', None), cell.coordinate)
                    if isinstance(cell.value, str):
                        self.assertNotRegex(cell.value, r'(?<!\d)\d{12}(?!\d)', cell.coordinate)
                        self.assertNotRegex(cell.value, r'KZ[0-9A-Z]{18}', cell.coordinate)
            names = {sheet.cell(row=r, column=4).value for r in range(3, sheet.max_row + 1)} - {None}
            self.assertEqual(names, {'Фамилия Имя Отчество'}, path)
            with zipfile.ZipFile(path) as archive:
                parts = archive.namelist()
            self.assertFalse([part for part in parts if 'comments' in part or 'custom' in part or 'externalLink' in part])


class TimesheetSheetTests(unittest.TestCase):
    def test_header_and_person_rows_as_hr_sheet(self):
        sheet = workbook([{'name': 'HR отдел', 'people': [person_row(facts={date(2026, 10, 5): {
            'late': 31, 'over': 0, 'present': True, 'planned': False, 'plan_hours': None}})]}]).active
        self.assertEqual(sheet.title, 'Октябрь 2026')
        self.assertEqual([sheet.cell(row=2, column=c).value for c in range(3, 9)],
                         ['Должность', 'Ф.И.О должностного лица', 'ТД/ГПХ', 'график', 'кол-во рабочих дней план',
                          'план часов'])
        self.assertEqual([sheet.cell(row=2, column=c).value for c in range(40, 45)],
                         ['кол-во отработанных смен', 'КОЛИЧЕСТВО ОТРАБОТАННЫХ ЧАСОВ', 'кол-во пропусков/отпуск/бс/б',
                          'итого минут опозданий/переработок', 'Комментарии, переработки'])
        self.assertEqual((sheet['A1'].value, sheet['I1'].value, sheet['I2'].value, sheet['AM1'].value), ('Отдел', 1, 'ЧТ', 31))
        self.assertIn('A1:A2', {str(m) for m in sheet.merged_cells.ranges})
        # Оформление HR: Times New Roman 16, сиреневая шапка, голубые выходные, светло-зелёный план.
        self.assertEqual((sheet['D3'].font.name, sheet['D3'].font.sz), ('Times New Roman', 16))
        self.assertEqual(fill_rgb(sheet['C2']), 'FFB3A2C7')
        self.assertEqual(fill_rgb(sheet['K1']), ('theme8', 0.4))      # 3 октября — суббота
        self.assertEqual(fill_rgb(sheet['F3']), 'FFD7E4BD')
        # Человек — две строки: №, должность, ФИО… объединены по вертикали; отдел — подписью поперёк.
        merged = {str(m) for m in sheet.merged_cells.ranges}
        for column in 'BCDEFGH':
            self.assertIn(f'{column}3:{column}4', merged)
        for column in ('AN', 'AO', 'AP', 'AQ', 'AR'):
            self.assertIn(f'{column}3:{column}4', merged)
        self.assertIn('A3:A4', merged)
        self.assertEqual((sheet['A3'].value, sheet['A3'].alignment.text_rotation), ('HR отдел', 90))
        self.assertEqual((sheet['B3'].value, sheet['E3'].value, sheet['F3'].value, sheet['G3'].value, sheet['H3'].value),
                         (1, 'ГПХ ИП Тест', '5/2', 21, '=G3*8'))
        # День — число (COUNT и SUM итогов считают его), выходной «В» — в стиле выходного HR.
        self.assertEqual((sheet['I3'].value, sheet['K3'].value), (8, 'В'))
        self.assertEqual(fill_rgb(sheet['K3']), ('theme8', 0.4))
        self.assertEqual(sheet['M4'].value, -31)                      # минуты — под 5-м числом
        self.assertEqual((sheet['AN3'].value, sheet['AO3'].value, sheet['AQ3'].value),
                         ('=COUNT(I3:AM3)', '=SUM(I3:AM3)', '=SUM(I4:AM4)'))
        self.assertEqual(sheet['AP3'].value, '=(COUNTIFS(I3:AM3,"О")+(COUNTIFS(I3:AM3,"А")+(COUNTIFS(I3:AM3,"Б"))))')
        self.assertEqual((sheet.row_dimensions[3].height, sheet.row_dimensions[4].height), (20.25, 21.0))
        self.assertEqual(sheet.freeze_panes, 'I3')

    def test_statuses_take_hr_colors(self):
        periods = [('annual_leave', date(2026, 10, 1), date(2026, 10, 2)), ('bs', date(2026, 10, 9), date(2026, 10, 9)),
                   ('sick_leave', date(2026, 10, 12), date(2026, 10, 12))]
        sheet = workbook([{'name': 'Отдел', 'people': [person_row(periods=periods)]}]).active
        self.assertEqual((sheet['I3'].value, fill_rgb(sheet['I3'])), ('О', 'FF92D050'))
        self.assertEqual((sheet['Q3'].value, fill_rgb(sheet['Q3'])), ('А', 'FFFFFF00'))
        self.assertEqual((sheet['T3'].value, fill_rgb(sheet['T3'])), ('Б', sheets.SICK_FILL))

    def test_hire_mid_month_is_written_over_days_before(self):
        sheet = workbook([{'name': 'Отдел', 'people': [person_row(hire=date(2026, 10, 12))]}]).active
        self.assertEqual(sheet['I3'].value, 'Прием 12/10/2026')        # как у HR: «Прием 12/10/2026»
        self.assertIn('I3:S3', {str(m) for m in sheet.merged_cells.ranges})
        self.assertEqual(sheet['T3'].value, 8)                          # 12-е — уже рабочий день
        self.assertIsNone(sheet['AR3'].value)                           # «Прием» в комментарий не повторяется

    def test_comment_is_overwork_or_hr_note(self):
        first = dict(person_row(), overwork=2259)
        second = dict(person_row(name='Пробный Иван Петрович', note='в офисе'), overwork=120)
        sheet = workbook([{'name': 'Отдел', 'people': [first, second]}]).active
        self.assertEqual(sheet['AR3'].value, 'Переработка 2259 мин')
        self.assertEqual(sheet['AR3'].font.sz, 20)                       # у HR комментарий — 20-м кеглем
        self.assertEqual(sheet['AR5'].value, 'в офисе')
        self.assertEqual(sheet['B5'].value, 2)

    def test_short_month_moves_totals_next_to_last_day(self):
        sheet = workbook([{'name': 'Отдел', 'people': [person_row(NOVEMBER)]}], NOVEMBER, 'Ноябрь 2026').active
        self.assertEqual((sheet['AL1'].value, sheet['AM2'].value), (30, 'кол-во отработанных смен'))
        self.assertEqual(sheet['AM3'].value, '=COUNT(I3:AL3)')
        self.assertEqual(sheet['AQ3'].value, None)                      # 5-й итог — комментарий, пустой

    def test_groups_number_through_the_sheet(self):
        sheet = workbook([{'name': 'Коммерческий отдел', 'people': [person_row(), person_row(name='Второва Вера')]},
                          {'name': 'HR отдел', 'people': [person_row(name='Третьякова Ольга')]}]).active
        self.assertEqual([sheet.cell(row=r, column=2).value for r in (3, 5, 7)], [1, 2, 3])
        merged = {str(m) for m in sheet.merged_cells.ranges}
        self.assertIn('A3:A6', merged)
        self.assertIn('A7:A8', merged)


class CompaniesSheetTests(unittest.TestCase):
    def test_list_as_hr_appendix_four(self):
        groups = service.companies_groups([
            {'name': 'Тестова Анна', 'job_title': 'Региональный супервайзер', 'employment_type': 'gph',
             'company': 'ИП «Тест»', 'project': 'Регионы'},
            {'name': 'Иванова Ирина', 'job_title': 'СММ менеджер', 'employment_type': 'gph', 'company': 'ИП «Тест»',
             'project': 'ЦО'},
            {'name': 'Борисов Борис', 'job_title': 'Бухгалтер', 'employment_type': 'of', 'company': 'ТОО «Фирма»',
             'project': 'ЦО'},
        ])
        self.assertEqual([name for name, _people in groups], ['ЦО', 'Регионы'])     # порядок справочника проектов
        sheet = load_workbook(BytesIO(sheets.companies_workbook(groups))).active
        self.assertEqual(sheet.title, 'Список по компаниям')
        self.assertEqual([sheet.cell(row=2, column=c).value for c in range(3, 6)],
                         ['Должность', 'Ф.И.О должностного лица', 'ТД/ГПХ'])
        self.assertEqual([(sheet.cell(row=r, column=2).value, sheet.cell(row=r, column=4).value,
                           sheet.cell(row=r, column=5).value) for r in (3, 5, 7)],
                         [(1, 'Борисов Борис', 'ТД ТОО Фирма'), (2, 'Иванова Ирина', 'ГПХ ИП Тест'),
                          (1, 'Тестова Анна', 'ГПХ ИП Тест')])         # № внутри проекта — заново
        merged = {str(m) for m in sheet.merged_cells.ranges}
        self.assertTrue({'A1:A2', 'A3:A6', 'A7:A8', 'D3:D4'} <= merged)
        self.assertEqual((sheet['A3'].value, sheet['A7'].value), ('ЦО', 'Регионы'))
        self.assertEqual((sheet['D3'].font.name, sheet['D3'].font.sz, fill_rgb(sheet['C2'])),
                         ('Times New Roman', 16, 'FFB3A2C7'))


class ActsFormatTests(unittest.TestCase):
    """АВР — форма HR: формат даты и суммы шаблона, печать и масштаб — на каждом листе пачки."""

    def test_every_sheet_keeps_hr_form_settings(self):
        template = load_workbook(documents.AVR_TEMPLATE).active
        act = {'person': {'name': 'Тестова Анна', 'iin': None}, 'company': {'name': 'Тест', 'kind': 'ip'},
               'contract': {}, 'number': 'АВР-1/2026', 'act_date': date(2026, 10, 31),
               'period_from': date(2026, 10, 1), 'period_to': date(2026, 10, 31), 'work_name': 'Услуги',
               'amount': Decimal('250000')}
        book = load_workbook(BytesIO(documents.acts_workbook([act, dict(act, person={'name': 'Второва Вера'})])))
        for sheet in book.worksheets:
            self.assertEqual(sheet['AR20'].number_format, template['AR20'].number_format)
            self.assertEqual(sheet['AM15'].number_format, template['AM15'].number_format)
            self.assertEqual(sheet.print_title_rows, '$17:$19')
            self.assertEqual((sheet.page_setup.orientation, sheet.page_setup.fitToHeight), ('landscape', 0))
            self.assertEqual(sheet.sheet_view.zoomScale, template.sheet_view.zoomScale)


def summary(user_id, name, *, contract=True, missing=(), hire=None, project='ЦО', department_id=1, amount=None,
            role='operator'):
    return {'user_id': user_id, 'name': name, 'has_contract': contract, 'missing': list(missing), 'hire_date': hire,
            'project': project, 'department_id': department_id, 'department': f'Отдел {department_id}',
            'job_title': 'Оператор', 'amount': amount, 'role': role, 'employment_type': 'gph', 'company': 'ИП «Тест»'}


class PageTests(unittest.TestCase):
    """Списки страницами, как «Учет сотрудников»: страница, всего и счётчики вкладок с сервера."""
    PEOPLE = [summary(i, f'Человек {i:03d}', contract=i % 3 != 0, missing=('ИИН',) if i % 2 else (),
                      hire=f'2026-10-{(i % 28) + 1:02d}') for i in range(1, 121)]
    CTX = {'user_id': 1, 'is_hr': True, 'is_super': False}

    def _people(self, _cursor, _ctx, *, query='', project='', department_id=None, scope='hiring'):
        rows = [p for p in self.PEOPLE if not project or p['project'] == project]
        return [p for p in rows if query.lower() in p['name'].lower()] if query else rows

    def test_page_bounds(self):
        self.assertEqual(service.page_bounds(None, None), (0, 50))
        self.assertEqual(service.page_bounds('-5', '1000'), (0, 200))
        self.assertEqual(service.page_bounds('x', '0'), (0, 1))

    def test_hiring_page_slices_and_counts(self):
        with mock.patch.object(service, 'list_people', self._people):
            page = service.hiring_page(None, self.CTX, offset=50, limit=50)
            self.assertEqual((page['total'], len(page['people'])), (120, 50))
            self.assertEqual(page['people'][0]['name'], 'Человек 051')
            self.assertEqual(page['counts'], {'all': 120, 'new': 40, 'data': 40})
            waiting = service.hiring_page(None, self.CTX, view='new', limit=200)['people']
            self.assertEqual(len(waiting), 40)
            self.assertEqual(waiting, sorted(waiting, key=lambda p: p['hire_date'], reverse=True))
            searched = service.hiring_page(None, self.CTX, query='Человек 01')
            self.assertEqual((searched['total'], searched['counts']['all']), (10, 120))   # счётчики — без поиска

    def test_acts_page_returns_selection_of_all_pages(self):
        people = [summary(i, f'Человек {i:03d}', department_id=1 + i % 2, amount=1000 * i) for i in range(1, 76)]
        with mock.patch.object(service, 'list_people', lambda *_a, **_k: [dict(p) for p in people]), \
                mock.patch.object(service.queries, 'last_acts', lambda _cursor, _ids: {}):
            page = service.acts_page(None, {'user_id': 1, 'is_hr': True}, department_id='2', offset=0, limit=10)
        self.assertEqual((page['total'], len(page['people']), len(page['selection'])), (38, 10, 38))
        self.assertEqual(page['selection'][0], {'user_id': 1, 'amount': 1000})
        self.assertEqual([item['value'] for item in page['departments']], [1, 2])
        self.assertEqual(page['people'][0]['work_name'], 'Услуги оператора')


class _Cursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        self.log.append((sql, params))

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _Db:
    def __init__(self):
        self.log = []

    @contextlib.contextmanager
    def _get_cursor(self):
        yield _Cursor(self.log)


class ExportRouteTests(unittest.TestCase):
    def _client(self, db):
        app = Flask(__name__)
        app.register_blueprint(build_hr_office_blueprint(
            db=db, require_api_key=lambda handler: handler, build_cors_preflight_response=lambda: jsonify({}),
            resolve_requester=lambda: (1, (1, 'Кто-то', 'login', 'super_admin'), None),
            viewer_context=lambda user_id, requester: {
                'user_id': user_id, 'role': 'super_admin', 'is_hr': False, 'is_super': True, 'is_admin': False,
                'is_global_admin': False, 'is_supervisor': False, 'department_id': None, 'headed_department_ids': []},
            update_user_field=lambda *_args: None))
        return app.test_client()

    def test_people_list_export_is_excel_and_logged(self):
        db = _Db()
        people = [summary(1, 'Тестова Анна'), summary(2, 'Иванова Ирина', project='Регионы')]
        with mock.patch.object(service, 'hiring_people', lambda *_a, **_k: people):
            response = self._client(db).get('/api/hr_office/people/export?project=ЦО')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Content-Disposition', response.headers.get('Access-Control-Expose-Headers', ''))
        sheet = load_workbook(io.BytesIO(response.data)).active
        self.assertEqual(sheet['D3'].value, 'Тестова Анна')
        logged = [params for sql, params in db.log if 'hr_audit_log' in sql]
        self.assertTrue(logged and any('Выгружен список по компаниям: 2 сотрудника' in str(p) for p in logged))

    def test_empty_list_is_404(self):
        with mock.patch.object(service, 'hiring_people', lambda *_a, **_k: []):
            response = self._client(_Db()).get('/api/hr_office/people/export')
        self.assertEqual((response.status_code, response.get_json()['code']), (404, 'HR_LIST_EMPTY'))

    def test_people_page_route_passes_window(self):
        seen = {}

        def fake(_cursor, _ctx, **kwargs):
            seen.update(kwargs)
            return {'people': [], 'total': 0, 'counts': {}, 'offset': kwargs['offset'], 'limit': kwargs['limit']}
        with mock.patch.object(service, 'hiring_page', fake):
            response = self._client(_Db()).get('/api/hr_office/people?offset=100&limit=50&view=new&q=Тест')
        self.assertEqual(response.status_code, 200)
        self.assertEqual((seen['offset'], seen['limit'], seen['view'], seen['query']), (100, 50, 'new', 'Тест'))


if __name__ == '__main__':
    unittest.main()
