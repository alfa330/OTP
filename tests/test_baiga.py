# -*- coding: utf-8 -*-
"""Раздел «Списки Байги» (#356): права, разбор файла, поиск, выгрузка, ручки.

Что сторожится:
  * периметр (решение владельца 05.10.2026): супер-админ и глава «Маркетинга»
    ведут раздел; главы и супервайзеры ОП и СЗоВ читают; операторы ОП и СЗоВ и
    сотрудники «Маркетинга» читают после QR; остальным закрыто; меню во фронте
    отвечает так же, как сервер;
  * разбор файла по п. 3 и п. 7 постановки: колонки по названию, период из
    имени (оба вида) или из «Даты», ошибки блокируют, предупреждения — нет,
    у каждой — лист и строка Excel;
  * поиск без учёта казахских букв (Жусип = Жүсіп) и раскладки в номере ВУ,
    пользовательский текст в SQL не попадает;
  * выгрузка недели без фильтров загружается обратно и совпадает построчно;
  * загрузка недели атомарна: занятая неделя без подтверждения — отказ без
    единой записи, недописанные строки — откат;
  * каждая выгрузка и скачивание исходника — в журнал;
  * раздел подключён: схема, Blueprint, пункт меню, гард видимости.

Все ФИО, номера ВУ и ID здесь ВЫДУМАНЫ: настоящий файл недели — персональные
данные водителей, и в публичный репозиторий он не идёт ни целиком, ни строкой.

SQL здесь не проверяется — его проверяет прогон на стенде с базой.
"""

import json
import re
import shutil
import subprocess
import sys
import unittest
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from functools import wraps
from io import BytesIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from openpyxl import Workbook, load_workbook  # noqa: E402

from baiga import access, filters, parse, queries, report, routes, schema  # noqa: E402
from parcels import access as parcels_access  # noqa: E402

APP_JSX = ROOT / 'src' / 'App.jsx'
META_JS = ROOT / 'src' / 'components' / 'baiga' / 'baigaMeta.js'
DATABASE_PY = ROOT / 'database.py'
BOT_PY = ROOT / 'bot_schedule2.py'

NAME = 'Список байги - 21.09.2026 - 27.09.2026.xlsx'
HEADER = ['Позиция', 'Дата', 'Неделя', 'Водитель', 'Наименование приза', 'Сумма', 'Поездок',
          'Город', 'Таксопарк', 'Номер ВУ', 'ID водителя']


def _read(path):
    return path.read_text(encoding='utf-8-sig')


def person(role='operator', department_code='marketing', headed_codes=(), user_id=10):
    return {
        'user_id': user_id, 'name': 'Сотрудник %d' % user_id, 'role': role,
        'department_id': 909, 'department_code': department_code, 'city': 'Алматы',
        'headed_department_ids': [909] if headed_codes else [],
        'headed_department_codes': list(headed_codes),
    }


def fake_id(number):
    return '%032x' % (0xbaa9a000 + number)


def line(position, name=None, prize='Не указано', amount=None, trips=40, city='Алматы',
         park='Тестовый парк (Алматы)', license_value=None, driver=None, date_text='21.09.26', week=39):
    return [position, date_text, week, name or 'Тестов%d Т. Т.' % position, prize,
            amount if amount is not None else 300000 - position * 1000, trips, city, park,
            license_value if license_value is not None else 'ZZ%06d' % position,
            driver if driver is not None else fake_id(position)]


def book(sheets):
    """{имя листа: [строки]} → байты xlsx. Первая строка листа — как дали."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        worksheet = workbook.create_sheet(title)
        for row in rows:
            worksheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def standard_book():
    return book({
        'Алматы-Каскелен': [HEADER, line(1, prize='200 000 тенге'), line(2, prize='5000 тенге'),
                            line(3, city='Шымкент')],
        'МОТО БАЙГА': [HEADER, line(1, name='Жүсіпов А. Б.', prize='10 000 тенге', driver=fake_id(101),
                                    license_value='ZZ000101')],
    })


# ─────────────────────────────────────────────────────────────────────────────
# Права
# ─────────────────────────────────────────────────────────────────────────────

FULL = {'can_open': True, 'can_export': True, 'can_manage': True}
READ = {'can_open': True, 'can_export': False, 'can_manage': False}
CLOSED = {'can_open': False, 'can_export': False, 'can_manage': False}

# Аналитик из именного списка (решение владельца 06.10.2026) — только id.
ANALYST_ID = 540

# Сетка для сверок «все сочетания»: роли портала, отделы раздела и чужие,
# главенство — своё, чужое и двойное.
GRID_ROLES = ('super_admin', 'admin', 'sv', 'supervisor', 'trainer', 'operator', 'trainee',
              'marketing_manager', 'accounting_manager')
GRID_DEPARTMENTS = ('marketing', 'op', 'szov', 'tez', 'front_office', None)
GRID_HEADS = ((), ('marketing',), ('op',), ('szov',), ('tez',), ('op', 'szov'), ('tez', 'marketing'),
              ('tez', 'szov'))


def grid():
    return [(role, code, heads) for role in GRID_ROLES for code in GRID_DEPARTMENTS for heads in GRID_HEADS]


class SwitchTests(unittest.TestCase):
    """Выключатель «только супер-админ»: снят 05.10.2026, но обязан работать."""

    def test_switch_is_off_and_closes_everyone_else_when_on(self):
        self.assertFalse(access.PILOT_SUPER_ADMIN_ONLY)
        with mock.patch.object(access, 'PILOT_SUPER_ADMIN_ONLY', True):
            self.assertEqual(access.capabilities(person(role='super_admin', department_code=None)), FULL)
            for kwargs in ({'department_code': 'szov'}, {'department_code': 'op'}, {'role': 'marketing_manager'},
                           {'role': 'sv', 'department_code': 'op'},
                           {'role': 'admin', 'department_code': 'szov', 'headed_codes': ('szov',)},
                           {'role': 'admin', 'department_code': 'marketing', 'headed_codes': ('marketing',)}):
                self.assertEqual(access.capabilities(person(**kwargs)), CLOSED, kwargs)

    def test_switch_and_analysts_are_the_same_on_both_sides(self):
        app = _read(APP_JSX)
        self.assertIn('const BAIGA_PILOT_SUPER_ADMIN_ONLY = %s;' % str(access.PILOT_SUPER_ADMIN_ONLY).lower(), app)
        ids = ', '.join(str(user_id) for user_id in sorted(access.ANALYST_USER_IDS))
        self.assertIn('const BAIGA_ANALYST_USER_IDS = new Set([%s]);' % ids, app)


class AccessTests(unittest.TestCase):
    """Круг доступа — решение владельца 05.10.2026."""

    def setUp(self):
        patcher = mock.patch.object(access, 'PILOT_SUPER_ADMIN_ONLY', False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def caps(self, **kwargs):
        return access.capabilities(person(**kwargs))

    def test_super_admin_does_everything(self):
        self.assertEqual(self.caps(role='super_admin', department_code=None), FULL)

    def test_marketing_head_has_full_access_whatever_the_base_role(self):
        for role in ('admin', 'sv', 'operator', 'marketing_manager'):
            for own in ('marketing', None):
                self.assertEqual(self.caps(role=role, department_code=own, headed_codes=('marketing',)), FULL,
                                 (role, own))

    def test_op_and_szov_heads_read_without_export(self):
        for code in ('op', 'szov'):
            for role in ('admin', 'sv', 'operator'):
                ctx = person(role=role, department_code=code, headed_codes=(code,))
                self.assertEqual(access.capabilities(ctx), READ, (code, role))
                self.assertFalse(access.requires_sensitive_qr(ctx), (code, role))
        # Глава узнаётся по главенству, а не по своему отделу в карточке.
        self.assertEqual(self.caps(role='admin', department_code=None, headed_codes=('szov',)), READ)

    def test_supervisors_of_op_and_szov_read_without_qr(self):
        for code in ('op', 'szov'):
            for role in ('sv', 'supervisor'):
                ctx = person(role=role, department_code=code)
                self.assertEqual(access.capabilities(ctx), READ, (code, role))
                self.assertFalse(access.requires_sensitive_qr(ctx), (code, role))
        for code in ('marketing', 'tez', 'front_office', None):
            self.assertEqual(self.caps(role='sv', department_code=code), CLOSED, code)

    def test_operators_and_marketing_staff_read_behind_qr(self):
        for kwargs in ({'department_code': 'op'}, {'department_code': 'szov'},
                       {'role': 'marketing_manager', 'department_code': 'marketing'},
                       {'department_code': 'marketing'}):
            ctx = person(**kwargs)
            self.assertEqual(access.capabilities(ctx), READ, kwargs)
            self.assertTrue(access.requires_sensitive_qr(ctx), kwargs)

    def test_everyone_else_is_closed(self):
        for kwargs in (
                {'role': 'trainer', 'department_code': 'szov'}, {'role': 'trainer', 'department_code': 'op'},
                {'role': 'trainer', 'department_code': 'marketing'},
                # Стажёра общий QR-замок не спрашивает — значит, и раздел ему закрыт.
                {'role': 'trainee', 'department_code': 'szov'}, {'role': 'trainee', 'department_code': 'op'},
                # Админ, не возглавляющий отдел, в круг не назван — где бы ни числился.
                {'role': 'admin', 'department_code': None}, {'role': 'admin', 'department_code': 'szov'},
                {'role': 'admin', 'department_code': 'op'}, {'role': 'admin', 'department_code': 'marketing'},
                # Главы прочих отделов — тоже.
                {'role': 'admin', 'department_code': 'tez', 'headed_codes': ('tez',)},
                {'role': 'admin', 'department_code': 'front_office', 'headed_codes': ('front_office',)},
                # Глава чужого отдела, числящийся оператором ОП: замок его не
                # спрашивает (он глава), а без замка рядового в раздел не пускают.
                {'role': 'operator', 'department_code': 'op', 'headed_codes': ('tez',)},
                {'department_code': 'tez'}, {'department_code': 'front_office'}, {'department_code': None},
                {'role': 'sv', 'department_code': 'tez'}):
            self.assertEqual(self.caps(**kwargs), CLOSED, kwargs)

    def test_department_codes_are_compared_without_case_and_spaces(self):
        self.assertEqual(self.caps(department_code=' OP '), READ)
        self.assertEqual(self.caps(role='sv', department_code='SZOV'), READ)
        self.assertEqual(self.caps(role='admin', department_code=None, headed_codes=(' Marketing ',)), FULL)

    def test_analyst_by_id_manages(self):
        with mock.patch.object(access, 'ANALYST_USER_IDS', frozenset({77})):
            self.assertEqual(self.caps(department_code='tez', user_id=77), FULL)
            self.assertEqual(self.caps(department_code='tez', user_id=78), CLOSED)

    def test_named_analyst_has_the_whole_section_without_qr(self):
        """Решение владельца 06.10.2026 про сотрудника отдела аналитики:
        «без qr, полный доступ к разделу». Числится он оператором, и общий
        замок спросил бы у него код."""
        self.assertEqual(access.ANALYST_USER_IDS, frozenset({ANALYST_ID}))
        analyst = person(role='operator', department_code='analytik', user_id=ANALYST_ID)
        self.assertEqual(access.capabilities(analyst), FULL)
        self.assertFalse(access.requires_sensitive_qr(analyst))
        # Выдано человеку, а не отделу: сосед по отделу и его глава раздела не видят.
        for kwargs in ({'user_id': ANALYST_ID + 1}, {'role': 'trainee', 'user_id': ANALYST_ID + 1},
                       {'role': 'admin', 'headed_codes': ('analytik',), 'user_id': 229}):
            self.assertEqual(self.caps(department_code='analytik', **kwargs), CLOSED, kwargs)

    def test_analyst_is_never_asked_for_qr_whatever_the_role_and_department(self):
        """Замок снят с человека из списка, а не с его должности: на любом
        сочетании роли, отдела и главенства он входит без кода и ведёт раздел,
        а тот же профиль с чужим id живёт по общему правилу."""
        with mock.patch.object(access, 'ANALYST_USER_IDS', frozenset({77})):
            asked_without_the_list = 0
            for role, code, heads in grid():
                named = person(role=role, department_code=code, headed_codes=heads, user_id=77)
                self.assertEqual(access.capabilities(named), FULL, (role, code, heads))
                self.assertFalse(access.requires_sensitive_qr(named), (role, code, heads))
                other = person(role=role, department_code=code, headed_codes=heads, user_id=78)
                self.assertEqual(access.requires_sensitive_qr(other), parcels_access.requires_sensitive_qr(other),
                                 (role, code, heads))
                asked_without_the_list += access.requires_sensitive_qr(other)
            # Сетка не вырождена: среди профилей есть те, кого замок спрашивает.
            self.assertGreater(asked_without_the_list, 10)

    def test_analyst_id_is_read_as_a_number(self):
        with mock.patch.object(access, 'ANALYST_USER_IDS', frozenset({77})):
            for user_id, named in (('77', True), (77.0, True), (None, False), ('', False), ('77a', False)):
                ctx = dict(person(department_code='tez'), user_id=user_id)
                self.assertEqual(access.can_manage(ctx), named, repr(user_id))
                self.assertEqual(access.requires_sensitive_qr(ctx), not named, repr(user_id))

    def test_switch_closes_the_analyst_too(self):
        with mock.patch.object(access, 'PILOT_SUPER_ADMIN_ONLY', True):
            analyst = person(role='operator', department_code='analytik', user_id=ANALYST_ID)
            self.assertEqual(access.capabilities(analyst), CLOSED)

    def test_nobody_but_the_named_circle_enters_without_qr(self):
        """Главное свойство раздела: мимо QR-замка входят только супер-админ,
        главы трёх отделов и супервайзеры ОП и СЗоВ — на всех сочетаниях роли,
        отдела и главенства."""
        entered = 0
        for role, code, heads in grid():
            ctx = person(role=role, department_code=code, headed_codes=heads)
            if not access.can_open_section(ctx):
                continue
            entered += 1
            if access.requires_sensitive_qr(ctx):
                # Рядовой: только чтение и только отделы раздела.
                self.assertEqual(access.capabilities(ctx), READ, (role, code, heads))
                self.assertIn(code, access.SECTION_DEPARTMENT_CODES, (role, code, heads))
                continue
            named = (role == 'super_admin'
                     or bool(set(heads) & set(access.SECTION_DEPARTMENT_CODES))
                     or (role in ('sv', 'supervisor') and code in access.READ_DEPARTMENT_CODES))
            self.assertTrue(named, (role, code, heads))
        self.assertGreater(entered, 50)

    def test_only_full_access_exports_and_manages(self):
        for role, code, heads in grid():
            caps = access.capabilities(person(role=role, department_code=code, headed_codes=heads))
            full = role == 'super_admin' or 'marketing' in heads
            self.assertEqual((caps['can_export'], caps['can_manage']), (full, full), (role, code, heads))


# ─────────────────────────────────────────────────────────────────────────────
# Разбор файла
# ─────────────────────────────────────────────────────────────────────────────

class ParseHappyPathTests(unittest.TestCase):

    def setUp(self):
        self.result = parse.parse_workbook(standard_book(), NAME)

    def test_week_is_read_whole(self):
        result = self.result
        self.assertTrue(result['ok'])
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['period'], {'start': date(2026, 9, 21), 'end': date(2026, 9, 27),
                                            'source': 'name', 'week': 39})
        self.assertEqual([(sheet['name'], sheet['rows']) for sheet in result['sheets']],
                         [('Алматы-Каскелен', 3), ('МОТО БАЙГА', 1)])
        self.assertEqual(result['stats'], {'rows': 4, 'drivers': 4, 'prize_rows': 3, 'prize_total': 215000})

    def test_sheet_name_becomes_zachet_and_row_keeps_excel_number(self):
        first = self.result['rows'][0]
        self.assertEqual((first['zachet'], first['row_number'], first['sheet_order']), ('Алматы-Каскелен', 2, 0))
        moto = self.result['rows'][-1]
        self.assertEqual((moto['zachet'], moto['sheet_order']), ('МОТО БАЙГА', 1))

    def test_date_stays_text_as_in_the_file(self):
        row = self.result['rows'][0]
        self.assertEqual((row['date_text'], row['row_date'], row['week_number']), ('21.09.26', date(2026, 9, 21), 39))

    def test_prize_number_is_extracted(self):
        prizes = [(row['prize_name'], row['has_prize'], row['prize_amount']) for row in self.result['rows']]
        self.assertEqual(prizes[0], ('200 000 тенге', True, 200000))
        self.assertEqual(prizes[2], ('Не указано', False, None))

    def test_kazakh_letters_are_folded_for_search(self):
        moto = self.result['rows'][-1]
        self.assertEqual(moto['driver_name'], 'Жүсіпов А. Б.')
        self.assertIn('жусипов', moto['search_text'])
        self.assertIn(fake_id(101), moto['search_text'])

    def test_rows_are_not_in_the_public_summary(self):
        summary = parse.public_summary(self.result)
        self.assertNotIn('rows', summary)
        self.assertEqual(summary['period']['start'], '2026-09-21')


class ParseFormatTests(unittest.TestCase):

    def test_columns_are_found_by_name_not_place(self):
        order = [5, 0, 10, 3, 1, 2, 4, 6, 7, 8, 9]
        header = [HEADER[i] for i in order] + [None, None]
        row = line(1)
        content = book({'Астана': [['Итоги Байги'], header, [row[i] for i in order] + [None, None]]})
        result = parse.parse_workbook(content, NAME)
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(result['rows'][0]['row_number'], 3)
        self.assertEqual(result['rows'][0]['driver_id'], fake_id(1))

    def test_license_written_as_a_number_is_read_as_text(self):
        content = book({'Астана': [HEADER, line(1, license_value=9912345678)]})
        row = parse.parse_workbook(content, NAME)['rows'][0]
        self.assertEqual((row['license'], row['license_key']), ('9912345678', '9912345678'))

    def test_period_from_underscored_name(self):
        self.assertEqual(parse.period_from_name('Список_байги_-_14_09_2026_-_20_09_2026.xlsx'),
                         (date(2026, 9, 14), date(2026, 9, 20)))
        self.assertEqual(parse.period_from_name('-_21.09.2026_-_27.09.2026.xlsx'),
                         (date(2026, 9, 21), date(2026, 9, 27)))
        self.assertIsNone(parse.period_from_name('байга итог.xlsx'))
        self.assertIsNone(parse.period_from_name('xlsx'))

    def test_range_notation_is_not_read_as_year_2027(self):
        """«21.09-27.09.2026»: кусок «21.09-27» раньше читался как 21.09.2027."""
        self.assertEqual(parse.dates_from_name('Список байги 21.09-27.09.2026.xlsx'),
                         [date(2026, 9, 21), date(2026, 9, 27)])
        self.assertEqual(parse.dates_from_name('Байга 1.09-7.09.2026.xlsx'), [date(2026, 9, 1), date(2026, 9, 7)])

    def test_period_must_match_the_date_column(self):
        week = [date(2026, 9, 21)] * 5
        period, error = parse.resolve_period('Список байги - 21.09.2026 - 27.09.2026.xlsx', week)
        self.assertEqual((period['start'], period['end'], period['source'], error),
                         (date(2026, 9, 21), date(2026, 9, 27), 'name', None))
        _, error = parse.resolve_period('Список байги - 14.09.2026 - 20.09.2026.xlsx', week)
        self.assertIn('не совпадает с колонкой «Дата»', error)
        _, error = parse.resolve_period('Список байги - 20.09.2026 - 27.09.2026.xlsx', week)
        self.assertIn('не из 7 дней', error)

    def test_single_date_in_name_is_start_or_end_by_the_data(self):
        week = [date(2026, 9, 21)] * 3
        start, _ = parse.resolve_period('Список байги - 21.09.2026.xlsx', week)
        end, _ = parse.resolve_period('Список байги - 27.09.2026.xlsx', week)
        self.assertEqual((start['start'], start['end']), (date(2026, 9, 21), date(2026, 9, 27)))
        self.assertEqual((end['start'], end['end']), (date(2026, 9, 21), date(2026, 9, 27)))
        _, error = parse.resolve_period('Список байги - 02.10.2026.xlsx', week)
        self.assertIn('не совпадает', error)

    def test_period_from_data_takes_the_common_date_not_a_typo(self):
        period, error = parse.resolve_period('байга итог.xlsx', [date(2026, 9, 21)] * 5 + [date(2025, 9, 21)])
        self.assertEqual((period['start'], period['source'], error), (date(2026, 9, 21), 'data', None))

    def test_wrong_period_in_name_blocks_the_upload(self):
        result = parse.parse_workbook(standard_book(), 'Список байги 21.09.2027 - 27.09.2027.xlsx')
        self.assertFalse(result['ok'])
        self.assertIn('не совпадает', result['errors'][0]['message'])

    def test_period_from_dates_when_name_has_none(self):
        result = parse.parse_workbook(standard_book(), 'байга итог.xlsx')
        self.assertTrue(result['ok'])
        self.assertEqual((result['period']['start'], result['period']['end'], result['period']['source']),
                         (date(2026, 9, 21), date(2026, 9, 27), 'data'))
        self.assertEqual([group['kind'] for group in result['warnings']], ['period_from_data'])

    def test_not_an_xlsx_is_refused_with_a_reason(self):
        cases = [(b'', 'BAIGA_FILE_EMPTY'), (b'\xd0\xcf\x11\xe0' + b'0' * 20, 'BAIGA_FILE_XLS'),
                 (b'just text', 'BAIGA_FILE_NOT_XLSX'), (b'PK\x03\x04broken', 'BAIGA_FILE_BROKEN')]
        for content, code in cases:
            with self.assertRaises(parse.ParseError) as caught:
                parse.parse_workbook(content, NAME)
            self.assertEqual(caught.exception.code, code)
        with mock.patch.object(parse, 'MAX_FILE_BYTES', 10):
            with self.assertRaises(parse.ParseError) as caught:
                parse.parse_workbook(standard_book(), NAME)
            self.assertEqual(caught.exception.code, 'BAIGA_FILE_TOO_BIG')


class ParseErrorTests(unittest.TestCase):

    def test_numbers_beyond_the_columns_are_errors_not_a_500_later(self):
        row = line(1)
        row[6] = 99999999999
        errors = self.errors([HEADER, row])
        self.assertEqual((errors[0]['message'], errors[0]['column']), ('Слишком большое число', 'Поездок'))

    def test_canonical_title_beats_an_alias_and_duplicates_block(self):
        header = ['ID'] + HEADER
        data = ['777'] + line(1)
        result = parse.parse_workbook(book({'Астана': [header, data]}), NAME)
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(result['rows'][0]['driver_id'], fake_id(1))
        errors = self.errors([HEADER + ['Сумма'], line(1) + [5]])
        self.assertEqual(errors[0]['message'], 'Колонка встречается дважды: «Сумма»')

    def test_sheet_with_data_but_without_header_blocks(self):
        result = parse.parse_workbook(book({'Астана': [HEADER, line(1)], 'МОТО БАЙГА': [line(2), line(3)]}), NAME)
        self.assertFalse(result['ok'])
        self.assertEqual(result['errors'][0]['sheet'], 'МОТО БАЙГА')

    def test_broken_sheet_is_a_file_error_not_a_500(self):
        stream = BytesIO(standard_book())
        source = zipfile.ZipFile(stream)
        broken = BytesIO()
        with zipfile.ZipFile(broken, 'w') as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename == 'xl/worksheets/sheet1.xml':
                    data = data[:len(data) // 2]
                target.writestr(item, data)
        with self.assertRaises(parse.ParseError) as caught:
            parse.parse_workbook(broken.getvalue(), NAME)
        self.assertEqual(caught.exception.code, 'BAIGA_FILE_BROKEN')

    def test_password_protected_file_says_so(self):
        content = b'\xd0\xcf\x11\xe0' + b'\x00' * 64 + 'EncryptedPackage'.encode('utf-16-le')
        with self.assertRaises(parse.ParseError) as caught:
            parse.parse_workbook(content, NAME)
        self.assertEqual(caught.exception.code, 'BAIGA_FILE_ENCRYPTED')

    def errors(self, rows, title='Астана'):
        result = parse.parse_workbook(book({title: rows}), NAME)
        self.assertFalse(result['ok'])
        return result['errors']

    def test_missing_column_names_the_sheet(self):
        errors = self.errors([HEADER[:-1], line(1)[:-1]])
        self.assertEqual(errors, [{'message': 'Нет колонок: «ID водителя»', 'sheet': 'Астана', 'row': 1}])

    def test_empty_required_cell_names_sheet_row_and_column(self):
        row = line(1)
        row[3] = None
        errors = self.errors([HEADER, line(1, driver=fake_id(9)), row])
        self.assertEqual(errors, [{'message': 'Пустая ячейка', 'sheet': 'Астана', 'row': 3, 'column': 'Водитель'}])

    def test_not_a_number_and_bad_date_block(self):
        bad = line(2, date_text='31.02.26')
        bad[5] = '12а'
        errors = self.errors([HEADER, line(1), bad])
        self.assertEqual([(error['row'], error['column']) for error in errors], [(3, 'Сумма'), (3, 'Дата')])

    def test_whole_numbers_with_spaces_are_numbers(self):
        row = line(1)
        row[5] = '1 234 567'
        result = parse.parse_workbook(book({'Астана': [HEADER, row]}), NAME)
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(result['rows'][0]['amount'], 1234567)
        for text in ('12.5', '-3'):
            row[6] = text
            result = parse.parse_workbook(book({'Астана': [HEADER, row]}), NAME)
            self.assertFalse(result['ok'], text)

    def test_file_without_any_table_is_an_error(self):
        result = parse.parse_workbook(book({'Лист1': [['просто текст']]}), NAME)
        self.assertFalse(result['ok'])
        self.assertIn('нет таблицы Байги', result['errors'][0]['message'])


class ParseWarningTests(unittest.TestCase):

    def kinds(self, sheets, name=NAME):
        result = parse.parse_workbook(book(sheets), name)
        self.assertTrue(result['ok'], result['errors'])
        return {group['kind']: group for group in result['warnings']}

    def test_warnings_do_not_block_and_name_the_row(self):
        warnings = self.kinds({
            'Астана': [HEADER, line(1, license_value='ZZ0000001'), line(2, driver='abc'),
                       line(3, prize='Смартфон'), line(4, date_text='14.09.26'),
                       line(5, driver=fake_id(1), license_value='ZZ000005')],
            'Пустой': [['заметки аналитика'], [], ['ещё заметка']],
        })
        self.assertEqual(set(warnings), {'license', 'driver_id', 'prize', 'date_period', 'duplicate',
                                         'sheet_skipped'})
        self.assertEqual(warnings['duplicate']['items'][0],
                         {'message': 'Уже есть: лист «Астана», строка 2', 'sheet': 'Астана', 'row': 6,
                          'column': 'ID водителя'})
        self.assertEqual(warnings['license']['items'][0]['row'], 2)
        self.assertEqual(warnings['sheet_skipped']['items'][0]['sheet'], 'Пустой')

    def test_word_prize_counts_as_prize_without_amount(self):
        has, amount, recognised = parse.prize_amount('Смартфон')
        self.assertEqual((has, amount, recognised), (True, None, False))
        self.assertEqual(parse.prize_amount('Не указано'), (False, None, True))
        self.assertEqual(parse.prize_amount('10 000 тенге'), (True, 10000, True))
        self.assertEqual(parse.prize_amount('2000\u00a0тенге'), (True, 2000, True))

    def test_cyrillic_letters_in_license_are_the_same_number(self):
        self.assertEqual(parse.license_key('АВ 123-456'), 'AB123456')
        self.assertTrue(parse.is_standard_license(parse.license_key('ав123456')))


# ─────────────────────────────────────────────────────────────────────────────
# Поиск
# ─────────────────────────────────────────────────────────────────────────────

class FilterTests(unittest.TestCase):

    def test_empty_filters_select_everything(self):
        where, params = filters.where(filters.normalize({}))
        self.assertEqual((where, params), ('TRUE', {}))

    def test_user_text_never_reaches_sql(self):
        chosen = filters.normalize({'q': "Жүсіп'; DROP TABLE x; --", 'city': "Шымкент'--",
                                    'zachet': 'Астана', 'park': 'Парк', 'prize': '5000 тенге'})
        where, params = filters.where(chosen)
        self.assertNotIn('DROP', where)
        self.assertNotIn('Шымкент', where)
        self.assertNotIn('%%', where)
        self.assertEqual(params['city'], "Шымкент'--")
        self.assertEqual(params['w0'], '%жусип\';%')

    def test_kazakh_letters_and_case_do_not_matter(self):
        _, params = filters.where(filters.normalize({'q': 'ЖҮСІП'}))
        self.assertEqual(params['w0'], '%жусип%')

    def test_license_typed_in_russian_layout_is_also_searched_in_latin(self):
        where, params = filters.where(filters.normalize({'q': 'ав123'}))
        self.assertEqual((params['w0'], params['w0l']), ('%ав123%', '%ab123%'))
        self.assertIn(' OR ', where)

    def test_like_wildcards_are_escaped(self):
        _, params = filters.where(filters.normalize({'q': '100%_'}))
        self.assertEqual(params['w0'], '%100\\%\\_%')

    def test_ranges_and_prize_modes(self):
        chosen = filters.normalize({'amount_min': '500 000', 'amount_max': 100, 'position_max': '10',
                                    'prize': 'any', 'trips_min': 'много'})
        where, params = filters.where(chosen)
        self.assertEqual((params['amount_min'], params['amount_max'], params['position_max']), (100, 500000, 10))
        self.assertNotIn('trips_min', params)
        self.assertIn('r.has_prize', where)
        where, _ = filters.where(filters.normalize({'prize': 'none'}))
        self.assertEqual(where, 'NOT r.has_prize')

    def test_period_is_a_date(self):
        _, params = filters.where(filters.normalize({'period': '2026-09-21'}))
        self.assertEqual(params['period'], date(2026, 9, 21))
        self.assertNotIn('period', filters.where(filters.normalize({'period': 'вчера'}))[1])

    def test_pasted_list_is_split_like_a_human_means_it(self):
        text = '%s, ZZ000002\nAB 123456\n\nZZ000003 ZZ000004;%s' % (fake_id(1), fake_id(1).upper())
        self.assertEqual(filters.split_tokens(text),
                         [fake_id(1), 'ZZ000002', 'AB 123456', 'ZZ000003', 'ZZ000004', fake_id(1).upper()])

    def test_pasted_list_accepts_fleet_links_and_cyrillic_licenses(self):
        link = 'https://fleet.yandex.kz/contractors?park_id=%s&contractor_id=%s' % (fake_id(900), fake_id(5))
        drivers, licenses, key_of = filters.classify_tokens([fake_id(1).upper(), link, 'ав123456', '9912345678'])
        self.assertIn(fake_id(1), drivers)
        self.assertIn(fake_id(5), drivers)
        self.assertNotIn(fake_id(900), drivers)
        self.assertEqual(licenses, ['9912345678', 'AB123456'])
        self.assertEqual(key_of[link], [('driver', fake_id(5))])

    def test_id_only_list_does_not_match_empty_licences(self):
        """Заглушка [''] находила каждую строку с пустым номером ВУ — чужих водителей."""
        where, params = filters.where(filters.normalize({'list': fake_id(1)}))
        self.assertEqual(where, '(r.driver_key = ANY(%(list_ids)s))')
        self.assertEqual(params, {'list_ids': [fake_id(1)]})
        self.assertNotIn('', params['list_ids'])

    def test_non_standard_id_is_found_as_an_id(self):
        drivers, licenses, _ = filters.classify_tokens(['7d1f0c2e9a'])
        self.assertIn('7d1f0c2e9a', drivers)
        self.assertEqual(filters.not_found(['7d1f0c2e9a'], {'7d1f0c2e9a'}, set()), [])

    def test_driver_card_looks_up_the_exact_id(self):
        where, params = filters.where(filters.normalize({'driver': 'ABC12'}))
        self.assertEqual((where, params), ('r.driver_key = %(driver)s', {'driver': 'abc12'}))

    def test_zachet_sort_is_one_order_across_weeks(self):
        sql, key, _ = filters.order_by('zachet', 'asc')
        self.assertTrue(sql.startswith('MIN(r.sheet_order) OVER (PARTITION BY r.zachet) ASC NULLS LAST, '
                                       'r.zachet ASC NULLS LAST'))
        self.assertEqual(key, 'zachet')

    def test_not_found_is_reported_as_pasted(self):
        tokens = [fake_id(1), 'ав123456', 'ZZ000009']
        self.assertEqual(filters.not_found(tokens, {fake_id(1)}, {'AB123456'}), ['ZZ000009'])

    def test_sort_is_whitelisted(self):
        sql, key, direction = filters.order_by('amount', 'desc')
        self.assertTrue(sql.startswith('r.amount DESC NULLS LAST'))
        self.assertEqual((key, direction), ('amount', 'desc'))
        sql, key, direction = filters.order_by('r.id; DROP', 'asc')
        self.assertTrue(sql.startswith('r.period_start DESC'))
        self.assertEqual((key, direction), ('week', 'desc'))

    def test_page_sizes_are_fixed(self):
        self.assertEqual(filters.page_args({'size': 500, 'page': 3}), (3, 500))
        self.assertEqual(filters.page_args({'size': 7, 'page': -1}), (1, 50))

    def test_journal_keeps_filters_without_the_list_itself(self):
        chosen = filters.normalize({'city': 'Шымкент', 'list': 'ZZ000001\nZZ000002', 'period': '2026-09-21'})
        self.assertEqual(filters.public(chosen),
                         {'city': 'Шымкент', 'period': '2026-09-21', 'list': {'count': 2}})


# ─────────────────────────────────────────────────────────────────────────────
# Выгрузка
# ─────────────────────────────────────────────────────────────────────────────

class ReportTests(unittest.TestCase):

    def setUp(self):
        self.content = standard_book()
        self.parsed = parse.parse_workbook(self.content, NAME)
        self.rows = sorted(self.parsed['rows'], key=lambda row: (row['sheet_order'], row['row_number']))

    def test_week_without_filters_loads_back_row_for_row(self):
        out = report.build(self.rows, report.MODE_SHEETS)
        name = report.file_name(date(2026, 9, 21), date(2026, 9, 27))
        self.assertEqual(name, NAME)
        back = parse.parse_workbook(out, name)
        self.assertTrue(back['ok'], back['errors'])
        keys = [key for key in self.rows[0] if key != 'row_date']
        self.assertEqual([[row[key] for key in keys] for row in back['rows']],
                         [[row[key] for key in keys] for row in self.rows])
        source = load_workbook(BytesIO(self.content))
        exported = load_workbook(BytesIO(out))
        self.assertEqual(source.sheetnames, exported.sheetnames)
        for left, right in zip(source.worksheets, exported.worksheets):
            self.assertEqual([[str(value) for value in row] for row in left.iter_rows(values_only=True)],
                             [[str(value) for value in row] for row in right.iter_rows(values_only=True)])

    def test_ids_and_licenses_are_text_header_frozen_with_filter(self):
        worksheet = load_workbook(BytesIO(report.build(self.rows))).worksheets[0]
        self.assertEqual(worksheet.freeze_panes, 'A2')
        self.assertEqual(worksheet.auto_filter.ref, 'A1:K4')
        self.assertEqual((worksheet['K2'].number_format, worksheet['J2'].number_format), ('@', '@'))
        self.assertIsInstance(worksheet['F2'].value, int)

    def test_green_corners_are_suppressed_on_every_sheet(self):
        archive = zipfile.ZipFile(BytesIO(report.build(self.rows)))
        sheets = sorted(name for name in archive.namelist() if name.startswith('xl/worksheets/sheet'))
        self.assertEqual(len(sheets), 2)
        for name in sheets:
            xml = archive.read(name).decode('utf-8')
            self.assertIn('numberStoredAsText="1" twoDigitTextYear="1"', xml)
            self.assertLess(xml.find('<pageMargins'), xml.find('<ignoredErrors'))

    def test_single_sheet_keeps_file_columns_and_adds_zachet_last(self):
        worksheet = load_workbook(BytesIO(report.build(self.rows, report.MODE_SINGLE))).worksheets[0]
        self.assertEqual(worksheet.title, report.SINGLE_SHEET_TITLE)
        self.assertEqual([cell.value for cell in worksheet[1]], HEADER + ['Зачёт'])
        self.assertEqual(worksheet.max_row, 5)

    def test_text_starting_with_equals_is_not_a_formula(self):
        # Исходник — со СТРОКОВЫМИ ячейками: append() у openpyxl сам сделал бы
        # из «=1+2» формулу без значения, а в файле аналитика это текст.
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = 'Астана'
        for number, values in enumerate([HEADER, line(1, name='=1+2', prize='=HYPERLINK("x")', park='=A1')], 1):
            for column, value in enumerate(values, 1):
                cell = worksheet.cell(row=number, column=column, value=value)
                if isinstance(value, str):
                    cell.data_type = 's'
        stream = BytesIO()
        workbook.save(stream)
        rows = parse.parse_workbook(stream.getvalue(), NAME)['rows']
        self.assertEqual(rows[0]['driver_name'], '=1+2')
        out = report.build(rows)
        archive = zipfile.ZipFile(BytesIO(out))
        self.assertNotIn('<f>', archive.read('xl/worksheets/sheet1.xml').decode('utf-8'))
        back = parse.parse_workbook(out, NAME)['rows'][0]
        self.assertEqual((back['driver_name'], back['prize_name'], back['park']), ('=1+2', '=HYPERLINK("x")', '=A1'))

    def test_sheet_titles_ignore_case_and_stay_within_31(self):
        rows = parse.parse_workbook(book({'МОТО БАЙГА': [HEADER, line(1)]}), NAME)['rows']
        twin = dict(rows[0], zachet='Мото Байга', driver_key='other')
        self.assertEqual(len(report.group_by_zachet([rows[0], twin])), 1)
        titles = report._sheet_titles(['А' * 40, 'а' * 40])
        self.assertEqual(len(set(t.casefold() for t in titles)), 2)
        self.assertTrue(all(len(title) <= 31 for title in titles))

    def test_file_name_for_many_weeks(self):
        self.assertEqual(report.file_name(), 'Список байги - все недели.xlsx')


# ─────────────────────────────────────────────────────────────────────────────
# Схема
# ─────────────────────────────────────────────────────────────────────────────

class _Cursor:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append(' '.join(sql.split()))


class SchemaTests(unittest.TestCase):

    def test_tables_go_first_and_everything_is_idempotent(self):
        cursor = _Cursor()
        schema.init_baiga_schema(cursor)
        kinds = ['table' if 'CREATE TABLE' in sql else 'other' for sql in cursor.statements]
        self.assertEqual(kinds[:4], ['table'] * 4)
        self.assertNotIn('table', kinds[4:])
        ddl = [sql for sql in cursor.statements if 'SAVEPOINT' not in sql]
        self.assertTrue(all('IF NOT EXISTS' in sql for sql in ddl))

    def test_one_active_upload_per_week(self):
        ddl = ' '.join(' '.join(statement.split()) for statement in schema._STATEMENTS)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS uq_baiga_uploads_active_week "
                      "ON baiga_uploads(campaign, period_start) WHERE status = 'active'", ddl)

    def test_trigram_index_cannot_break_the_section(self):
        class Failing(_Cursor):
            def execute(self, sql, params=None):
                super().execute(sql, params)
                if 'pg_trgm' in sql and 'EXTENSION' in sql:
                    raise RuntimeError('нет расширения')

        cursor = Failing()
        schema.init_baiga_schema(cursor)
        self.assertEqual(cursor.statements[-1], 'ROLLBACK TO SAVEPOINT baiga_trgm')


# ─────────────────────────────────────────────────────────────────────────────
# Ручки — на подменённом слое запросов
# ─────────────────────────────────────────────────────────────────────────────

class Store:
    """Память вместо базы: ровно те функции queries, что зовут роуты."""

    def __init__(self):
        self.uploads = {}
        self.files = {}
        self.rows = []
        self.exports = []
        self.writes = []
        self.short_write = False
        self.export_result = None

    # экран и поиск
    def list_weeks(self, cursor, campaign):
        return [dict(upload) for upload in self.uploads.values() if upload['status'] == 'active']

    def filter_options(self, cursor):
        return {'zachets': sorted({row['zachet'] for row in self.rows}), 'cities': [], 'parks': [], 'prizes': []}

    def totals(self, cursor, where_sql, params):
        return {'rows': len(self.rows), 'drivers': len({row['driver_key'] for row in self.rows}),
                'prize_rows': 0, 'prize_total': 0}

    @staticmethod
    def _screen_row(row):
        # Тот же список полей, что у настоящей выборки (queries.ROW_FIELDS).
        return {name: row.get(name) for name in queries.ROW_FIELDS}

    def search(self, cursor, where_sql, params, order_sql, limit, offset):
        self.last_search = (where_sql, params, order_sql, limit, offset)
        return [self._screen_row(row) for row in self.rows[offset:offset + limit]]

    def zachet_summary(self, cursor, where_sql, params):
        names = []
        for row in self.rows:
            if row['zachet'] not in names:
                names.append(row['zachet'])
        return [{'name': name, 'rows': sum(1 for row in self.rows if row['zachet'] == name),
                 'prize_rows': 0, 'prize_total': 0} for name in names]

    def find_overlapping_uploads(self, cursor, campaign, start, end):
        return [dict(upload) for upload in self.uploads.values()
                if upload['status'] == 'active' and upload['period_start'] != start
                and upload['period_start'] <= end and upload['period_end'] >= start]

    def found_keys(self, cursor, where_sql, params):
        return {row['driver_key'] for row in self.rows}, {row['license_key'] for row in self.rows}

    def export_rows(self, cursor, where_sql, params, order_sql, limit):
        self.last_export = (where_sql, params, order_sql, limit)
        rows = self.export_result if self.export_result is not None else self.rows
        return [self._screen_row(row) for row in rows][:limit + 1]

    # загрузка
    def lock_week(self, cursor, campaign, period_start):
        self.writes.append(('lock', period_start))

    def find_active_upload(self, cursor, campaign, period_start):
        for upload in self.uploads.values():
            if upload['status'] == 'active' and upload['period_start'] == period_start:
                return dict(upload, file_sha256=upload.get('sha'))
        return None

    def insert_upload(self, cursor, result, actor):
        upload_id = max(self.uploads, default=0) + 1
        self.uploads[upload_id] = {
            'id': upload_id, 'status': 'active', 'period_start': result['period']['start'],
            'period_end': result['period']['end'], 'rows_count': result['stats']['rows'],
            'file_name': result['file_name'], 'uploaded_by_name': actor['name'],
            'uploaded_at': datetime(2026, 10, 2, 12, 0), 'sha': result['sha256'],
        }
        self.writes.append(('insert_upload', upload_id))
        return upload_id

    def insert_file(self, cursor, upload_id, content):
        self.files[upload_id] = content
        self.writes.append(('insert_file', upload_id))

    def insert_rows(self, cursor, upload_id, period_start, rows):
        self.writes.append(('insert_rows', upload_id))
        for row in rows:
            self.rows.append(dict(row, upload_id=upload_id, period_start=period_start, id=len(self.rows) + 1))
        return len(rows) - 1 if self.short_write else len(rows)

    def close_upload(self, cursor, upload_id, status, actor, replaced_by=None):
        self.uploads[upload_id]['status'] = status
        self.rows = [row for row in self.rows if row.get('upload_id') != upload_id]
        self.files.pop(upload_id, None)
        self.writes.append(('close', upload_id, status))

    def mark_replaced_by(self, cursor, upload_id, replaced_by):
        self.uploads[upload_id]['replaced_by'] = replaced_by

    def get_upload(self, cursor, upload_id, lock=False):
        upload = self.uploads.get(int(upload_id))
        return dict(upload) if upload else None

    def read_file(self, cursor, upload_id):
        return self.files.get(int(upload_id))

    def list_uploads(self, cursor, limit=200):
        return [dict(upload) for upload in self.uploads.values()]

    def list_exports(self, cursor, limit=200):
        return list(self.exports)

    def log_export(self, cursor, **kwargs):
        self.exports.append(kwargs)


class FakeDb:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def _get_cursor(self):
        try:
            yield object()
        except Exception:
            self.rollbacks += 1
            raise
        self.commits += 1


@unittest.skipIf(Flask is None, 'Flask не установлен')
class _Base(unittest.TestCase):

    def setUp(self):
        self.store = Store()
        self.db = FakeDb()
        self.viewer = person(role='super_admin', department_code=None)
        self.qr_granted = False
        self.qr_error = None
        self.authorized = False
        patches = {'load_access_context': lambda cursor, user_id: dict(self.viewer)}
        for name in ('list_weeks', 'filter_options', 'totals', 'search', 'found_keys', 'export_rows',
                     'zachet_summary', 'find_overlapping_uploads',
                     'lock_week', 'find_active_upload', 'insert_upload', 'insert_file', 'insert_rows',
                     'close_upload', 'mark_replaced_by', 'get_upload', 'read_file', 'list_uploads',
                     'list_exports', 'log_export'):
            patches[name] = getattr(self.store, name)
        patcher = mock.patch.multiple(queries, **patches)
        patcher.start()
        self.addCleanup(patcher.stop)
        ready = mock.patch.object(schema, 'schema_is_ready', lambda cursor: True)
        ready.start()
        self.addCleanup(ready.stop)
        # Ручки проверяются со снятым выключателем; сам выключатель — SwitchTests.
        pilot = mock.patch.object(access, 'PILOT_SUPER_ADMIN_ONLY', False)
        pilot.start()
        self.addCleanup(pilot.stop)

        # Подмены строгие, как настоящие слои: без декоратора авторизации
        # запрашивающего нет, а QR подтверждён КОНКРЕТНОМУ человеку. С тождеством
        # вместо них снятый декоратор и вопрос «подтверждён ли None» проходили.
        def require_api_key(handler):
            @wraps(handler)
            def wrapper(*args, **kwargs):
                self.authorized = True
                try:
                    return handler(*args, **kwargs)
                finally:
                    self.authorized = False
            return wrapper

        def resolve_requester():
            if not self.authorized:
                return None, None, ('Unauthorized', 401)
            return self.viewer['user_id'], None, None

        def sensitive_access_granted(user_id):
            if self.qr_error:
                raise self.qr_error
            return self.qr_granted and user_id == self.viewer['user_id']

        app = Flask(__name__)
        app.register_blueprint(routes.build_baiga_blueprint(
            db=self.db,
            require_api_key=require_api_key,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=resolve_requester,
            sensitive_access_granted=sensitive_access_granted,
        ))
        app.config['TESTING'] = True
        self.client = app.test_client()

    def upload(self, content=None, name=NAME, replace=False, path='/api/baiga/uploads'):
        data = {'file': (BytesIO(content if content is not None else standard_book()), 'week.xlsx'),
                'file_name': name}
        if replace:
            data['replace'] = '1'
        return self.client.post(path, data=data, content_type='multipart/form-data')

    def writes(self):
        return [write for write in self.store.writes if write[0] != 'lock']


class GateTests(_Base):

    # Все ручки раздела — чтобы гейт проверялся на каждой, а не на «экране».
    def every_route(self):
        return [
            self.client.get('/api/baiga'),
            self.client.post('/api/baiga/rows', json={}),
            self.client.post('/api/baiga/export', json={}),
            self.upload(path='/api/baiga/uploads/preview'),
            self.upload(),
            self.client.delete('/api/baiga/uploads/1'),
            self.client.get('/api/baiga/uploads/1/file'),
            self.client.get('/api/baiga/journal'),
        ]

    def codes(self, responses):
        return [(response.status_code, (response.get_json() or {}).get('code')) for response in responses]

    def test_rank_and_file_need_qr_before_anything(self):
        for kwargs in ({'department_code': 'szov'}, {'department_code': 'op'},
                       {'role': 'marketing_manager', 'department_code': 'marketing'}):
            self.viewer = person(**kwargs)
            self.qr_granted = False
            self.assertEqual(set(self.codes(self.every_route())), {(403, 'SENSITIVE_ACCESS_REQUIRED')}, kwargs)
            self.qr_granted = True
            data = self.client.get('/api/baiga').get_json()
            self.assertEqual(data['capabilities'], READ, kwargs)
            self.assertEqual(self.client.post('/api/baiga/rows', json={}).status_code, 200, kwargs)
        self.assertEqual(self.writes(), [])

    def test_broken_qr_check_fails_closed(self):
        """Проверка QR упала (база, сеть) — рядовой получает отказ, а не данные."""
        self.upload()
        self.viewer = person(department_code='op')
        self.qr_granted = True
        self.qr_error = RuntimeError('база недоступна')
        for response in self.every_route():
            self.assertEqual(response.status_code, 500)
            self.assertNotIn('rows', response.get_json() or {})
        self.assertEqual(self.store.exports, [])

    def test_qr_is_asked_about_the_person_who_came(self):
        """Гейт спрашивает подтверждение по id пришедшего. Подмена отвечает «да»
        только на него: вопрос про None или про id отдела оставил бы оператора
        с подтверждённым QR за замком навсегда."""
        self.viewer = person(department_code='op', user_id=31)
        self.qr_granted = True
        self.assertEqual(self.client.get('/api/baiga').status_code, 200)
        self.assertEqual(self.client.post('/api/baiga/rows', json={}).status_code, 200)

    def test_without_the_auth_layer_nobody_gets_in(self):
        """Запрашивающего даёт только слой авторизации: ручка, оставшаяся без
        него, не должна отвечать данными."""
        self.assertEqual(self.client.get('/api/baiga').status_code, 200)
        bare = Flask('bare')
        bare.register_blueprint(routes.build_baiga_blueprint(
            db=self.db, require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (None, None, ('Unauthorized', 401)),
            sensitive_access_granted=lambda user_id: True))
        client = bare.test_client()
        self.assertEqual(client.get('/api/baiga').status_code, 401)
        self.assertEqual(client.post('/api/baiga/rows', json={}).status_code, 401)

    def test_closed_section_says_so_before_qr(self):
        for kwargs in ({'department_code': 'tez'}, {'role': 'trainee', 'department_code': 'szov'},
                       {'role': 'trainer', 'department_code': 'op'}, {'role': 'admin', 'department_code': None},
                       {'role': 'admin', 'department_code': 'tez', 'headed_codes': ('tez',)}):
            self.viewer = person(**kwargs)
            # Подтверждённый QR раздел не открывает: он про данные, а не про круг.
            for granted in (False, True):
                self.qr_granted = granted
                self.assertEqual(set(self.codes(self.every_route())), {(403, 'BAIGA_SECTION_CLOSED')},
                                 (kwargs, granted))
        self.assertEqual(self.writes(), [])

    def test_readers_search_but_cannot_export_or_upload(self):
        self.upload()
        before = list(self.store.writes)
        self.qr_granted = True
        for kwargs in ({'role': 'sv', 'department_code': 'szov'}, {'role': 'sv', 'department_code': 'op'},
                       {'role': 'admin', 'department_code': 'op', 'headed_codes': ('op',)},
                       {'role': 'admin', 'department_code': 'szov', 'headed_codes': ('szov',)},
                       {'department_code': 'op'}, {'role': 'marketing_manager', 'department_code': 'marketing'}):
            self.viewer = person(**kwargs)
            self.assertEqual(self.codes(self.every_route()), [
                (200, None), (200, None), (403, 'BAIGA_EXPORT_FORBIDDEN'),
                (403, 'BAIGA_MANAGE_FORBIDDEN'), (403, 'BAIGA_MANAGE_FORBIDDEN'),
                (403, 'BAIGA_MANAGE_FORBIDDEN'), (403, 'BAIGA_MANAGE_FORBIDDEN'),
                (403, 'BAIGA_MANAGE_FORBIDDEN'),
            ], kwargs)
        # Ни одной записи и ни одной строки журнала выгрузок от читающих.
        self.assertEqual(self.store.writes, before)
        self.assertEqual(self.store.exports, [])
        self.assertEqual(self.store.uploads[1]['status'], 'active')

    def test_heads_and_supervisors_are_not_asked_for_qr(self):
        self.qr_granted = False
        for kwargs in ({'role': 'sv', 'department_code': 'szov'}, {'role': 'sv', 'department_code': 'op'},
                       {'role': 'admin', 'department_code': 'op', 'headed_codes': ('op',)},
                       {'role': 'admin', 'department_code': 'marketing', 'headed_codes': ('marketing',)}):
            self.viewer = person(**kwargs)
            self.assertEqual(self.client.get('/api/baiga').status_code, 200, kwargs)

    def test_marketing_head_exports_uploads_and_reads_the_journal(self):
        self.viewer = person(role='admin', department_code='marketing', headed_codes=('marketing',))
        self.assertEqual(self.client.get('/api/baiga').get_json()['capabilities'], FULL)
        self.assertEqual(self.upload().status_code, 201)
        for row in self.store.rows:
            row['period_start'] = date(2026, 9, 21)
        self.assertEqual(self.client.post('/api/baiga/export', json={}).status_code, 200)
        self.assertEqual(self.client.get('/api/baiga/uploads/1/file').status_code, 200)
        self.assertEqual(len(self.client.get('/api/baiga/journal').get_json()['uploads']), 1)
        self.assertEqual(self.client.delete('/api/baiga/uploads/1').status_code, 200)

    def test_named_analyst_does_everything_and_the_qr_key_is_not_even_asked(self):
        """Аналитик — оператор по должности, но раздел выдан ему целиком и без
        замка. Проверка QR здесь сломана намеренно: тронь её гейт — ручки
        ответили бы 500, а не данными (ровно так падает рядовой в
        test_broken_qr_check_fails_closed)."""
        self.viewer = person(role='operator', department_code='analytik', user_id=ANALYST_ID)
        self.qr_granted = False
        self.qr_error = RuntimeError('QR у аналитика не спрашивают')
        self.assertEqual(self.client.get('/api/baiga').get_json()['capabilities'], FULL)
        self.assertEqual(self.client.post('/api/baiga/rows', json={}).status_code, 200)
        self.assertEqual(self.upload(path='/api/baiga/uploads/preview').status_code, 200)
        self.assertEqual(self.upload().status_code, 201)
        for row in self.store.rows:
            row['period_start'] = date(2026, 9, 21)
        self.assertEqual(self.client.post('/api/baiga/export', json={}).status_code, 200)
        self.assertEqual(self.client.get('/api/baiga/uploads/1/file').status_code, 200)
        self.assertEqual(len(self.client.get('/api/baiga/journal').get_json()['uploads']), 1)
        self.assertEqual(self.upload(replace=True).status_code, 201)
        self.assertEqual(self.client.delete('/api/baiga/uploads/2').status_code, 200)
        # В журнале выгрузок — он сам, а не безымянная запись.
        self.assertEqual({entry['actor']['user_id'] for entry in self.store.exports}, {ANALYST_ID})

    def test_analysts_neighbour_in_the_department_stays_out(self):
        """Допуск именной: коллега по отделу аналитики с подтверждённым QR
        получает «раздел закрыт» на каждой ручке."""
        self.viewer = person(role='operator', department_code='analytik', user_id=ANALYST_ID + 1)
        self.qr_granted = True
        self.assertEqual(set(self.codes(self.every_route())), {(403, 'BAIGA_SECTION_CLOSED')})
        self.assertEqual(self.writes(), [])


class ScreenAndSearchTests(_Base):

    def test_screen_is_one_request_with_limits(self):
        data = self.client.get('/api/baiga').get_json()
        self.assertTrue(data['schema_ready'])
        self.assertEqual(data['capabilities'], {'can_open': True, 'can_export': True, 'can_manage': True})
        self.assertEqual(data['limits']['page_sizes'], [50, 100, 500])

    def test_search_pages_and_reports_list_misses(self):
        self.upload()
        response = self.client.post('/api/baiga/rows', json={
            'filters': {'list': '%s\nZZ999999' % fake_id(1)}, 'page': 1, 'size': 50, 'sort': 'amount', 'dir': 'desc'})
        data = response.get_json()
        self.assertEqual(data['totals']['rows'], 4)
        self.assertEqual(data['list'], {'count': 2, 'not_found': ['ZZ999999'], 'not_found_total': 1})
        self.assertEqual((data['sort'], data['dir'], data['size']), ('amount', 'desc', 50))
        self.assertNotIn('search_text', data['rows'][0])
        self.assertEqual([item['name'] for item in data['zachets']], ['Алматы-Каскелен', 'МОТО БАЙГА'])

    def test_dates_are_iso_not_gmt(self):
        self.upload()
        data = self.client.get('/api/baiga').get_json()
        self.assertEqual(data['weeks'][0]['period_start'], '2026-09-21')
        self.assertEqual(data['weeks'][0]['uploaded_at'], '2026-10-02T12:00:00')


class UploadTests(_Base):

    def test_preview_writes_nothing_and_shows_the_week(self):
        response = self.upload(path='/api/baiga/uploads/preview')
        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data['ok'])
        self.assertNotIn('rows', data)
        self.assertEqual(data['period']['start'], '2026-09-21')
        self.assertIsNone(data['existing'])
        self.assertEqual(self.writes(), [])

    def test_upload_writes_week_file_and_rows(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.writes(), [('insert_upload', 1), ('insert_file', 1), ('insert_rows', 1)])
        self.assertEqual(len(self.store.rows), 4)
        self.assertEqual(self.store.uploads[1]['file_name'], NAME)

    def test_same_week_again_needs_confirmation_and_writes_nothing(self):
        self.upload()
        before = list(self.store.writes)
        response = self.upload()
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'BAIGA_WEEK_EXISTS'))
        self.assertTrue(response.get_json()['existing']['same_file'])
        self.assertEqual([write for write in self.store.writes if write not in before and write[0] != 'lock'], [])

    def test_confirmed_replacement_replaces_not_duplicates(self):
        self.upload()
        response = self.upload(replace=True)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.get_json()['replaced'])
        self.assertEqual(self.store.uploads[1]['status'], 'replaced')
        self.assertEqual(self.store.uploads[1]['replaced_by'], 2)
        self.assertEqual(len(self.store.rows), 4)
        self.assertNotIn(1, self.store.files)
        # Прежняя неделя закрыта ДО записи новой: иначе две активные загрузки.
        closes = [i for i, write in enumerate(self.store.writes) if write[0] == 'close']
        inserts = [i for i, write in enumerate(self.store.writes) if write == ('insert_upload', 2)]
        self.assertLess(closes[0], inserts[0])

    def test_file_with_errors_is_refused_without_writes(self):
        bad = book({'Астана': [HEADER, line(1, date_text='вчера')]})
        response = self.upload(bad)
        data = response.get_json()
        self.assertEqual((response.status_code, data['code']), (422, 'BAIGA_FILE_HAS_ERRORS'))
        self.assertEqual(data['preview']['errors'][0]['column'], 'Дата')
        self.assertEqual(self.writes(), [])

    def test_overlapping_week_is_refused_in_preview_and_upload(self):
        self.store.uploads[9] = {'id': 9, 'status': 'active', 'period_start': date(2026, 9, 22),
                                 'period_end': date(2026, 9, 28), 'rows_count': 5, 'file_name': 'x',
                                 'uploaded_by_name': 'x', 'uploaded_at': datetime(2026, 10, 1, 9, 0)}
        preview = self.upload(path='/api/baiga/uploads/preview').get_json()
        self.assertFalse(preview['ok'])
        self.assertIn('пересекается', preview['errors'][0]['message'])
        response = self.upload()
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'BAIGA_WEEK_OVERLAP'))
        self.assertEqual(self.writes(), [])

    def test_short_write_rolls_back(self):
        self.store.short_write = True
        response = self.upload()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.db.rollbacks, 1)

    def test_not_excel_and_missing_file(self):
        response = self.upload(b'hello')
        self.assertEqual((response.status_code, response.get_json()['code']), (422, 'BAIGA_FILE_NOT_XLSX'))
        response = self.client.post('/api/baiga/uploads', data={}, content_type='multipart/form-data')
        self.assertEqual(response.get_json()['code'], 'BAIGA_FILE_REQUIRED')

    def test_cyrillic_file_name_is_kept_as_is(self):
        self.upload(name='C:\\Users\\аналитик\\' + NAME)
        self.assertEqual(self.store.uploads[1]['file_name'], NAME)


class DeleteSourceJournalTests(_Base):

    def setUp(self):
        super().setUp()
        self.upload()

    def test_delete_week(self):
        response = self.client.delete('/api/baiga/uploads/1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.uploads[1]['status'], 'deleted')
        self.assertEqual(self.store.rows, [])
        again = self.client.delete('/api/baiga/uploads/1')
        self.assertEqual(again.get_json()['code'], 'BAIGA_UPLOAD_CLOSED')

    def test_source_download_is_journaled(self):
        response = self.client.get('/api/baiga/uploads/1/file')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.store.files[1])
        self.assertIn("filename*=UTF-8''", response.headers['Content-Disposition'])
        self.assertIn('Content-Disposition', response.headers['Access-Control-Expose-Headers'])
        self.assertEqual(self.store.exports[-1]['kind'], 'source')
        self.client.delete('/api/baiga/uploads/1')
        self.assertEqual(self.client.get('/api/baiga/uploads/1/file').get_json()['code'], 'BAIGA_FILE_GONE')

    def test_export_is_journaled_with_filters(self):
        response = self.client.post('/api/baiga/export', json={'filters': {'city': 'Шымкент'}, 'mode': 'single'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['X-Rows'], '4')
        self.assertIn('Content-Disposition', response.headers['Access-Control-Expose-Headers'])
        logged = self.store.exports[-1]
        self.assertEqual((logged['kind'], logged['mode'], logged['filters'], logged['rows_count']),
                         ('export', 'single', {'city': 'Шымкент'}, 4))

    def test_export_by_sheets_goes_in_file_order(self):
        self.client.post('/api/baiga/export', json={'mode': 'sheets', 'sort': 'amount'})
        self.assertEqual(self.store.last_export[2], 'r.period_start, r.sheet_order, r.row_number, r.id')

    def test_export_limit_and_empty_selection(self):
        with mock.patch.object(report, 'EXPORT_LIMIT', 3):
            self.store.last_export = None
            response = self.client.post('/api/baiga/export', json={})
            self.assertEqual(response.get_json()['code'], 'BAIGA_EXPORT_TOO_BIG')
            # Отказ — по счёту, без выборки строк в память.
            self.assertIsNone(self.store.last_export)
        self.store.rows = []
        self.assertEqual(self.client.post('/api/baiga/export', json={}).get_json()['code'], 'BAIGA_EXPORT_EMPTY')
        self.assertEqual([entry for entry in self.store.exports if entry['kind'] == 'export'], [])

    def test_journal_lists_uploads(self):
        data = self.client.get('/api/baiga/journal').get_json()
        self.assertEqual(len(data['uploads']), 1)


# ─────────────────────────────────────────────────────────────────────────────
# Проводка
# ─────────────────────────────────────────────────────────────────────────────

class WiringTests(unittest.TestCase):

    def setUp(self):
        self.app = _read(APP_JSX)

    def test_schema_is_deployed(self):
        source = _read(DATABASE_PY)
        thermo = source.index('            self._init_thermoboxes_schema_tx(cursor)\n')
        baiga = source.index('            self._init_baiga_schema_tx(cursor)\n')
        self.assertLess(thermo, baiga)
        self.assertIn('def _init_baiga_schema_tx(self, cursor):', source)
        self.assertIn('SAVEPOINT baiga_schema', source)

    def test_blueprint_is_registered_with_the_qr_key(self):
        source = _read(BOT_PY)
        block = source.split('from baiga.routes import build_baiga_blueprint')[1].split('except Exception')[0]
        self.assertIn('app.register_blueprint(build_baiga_blueprint(', block)
        # Все три: без авторизации раздел не знает, кто пришёл, а с чужим
        # resolve_requester отвечал бы не тому человеку.
        self.assertIn('        require_api_key=require_api_key,\n', block)
        self.assertIn('        resolve_requester=_resolve_requester,\n', block)
        self.assertIn('sensitive_access_granted=_sensitive_access_granted_for_user', block)
        self.assertIn('            @require_api_key\n', _read(ROOT / 'baiga' / 'routes.py'))

    def test_lazy_view_behind_the_qr_gate(self):
        self.assertIn("const BaigaView = lazyWithRetry(() => import('./components/baiga/BaigaView'));", self.app)
        # Условие целиком и порядок веток: с подстрокой проходила и инверсия
        # замка («!sensitiveSectionsLocked ?»), при которой раздел не открылся бы
        # ни у кого из круга.
        start = '{view === "baiga" && canAccessBaigaSection && (baigaLocked ? ('
        self.assertEqual(self.app.count(start), 1)
        self.assertEqual(self.app.count('{view === "baiga" && '), 1)
        block = self.app.split(start)[1].split('</Suspense>')[0]
        gate, otherwise, section = (block.index('<SensitiveSectionGate'), block.index(') : ('),
                                    block.index('<BaigaView'))
        self.assertLess(gate, otherwise)
        self.assertLess(otherwise, section)
        # Замок у раздела свой: общий спросил бы код и у аналитика из списка.
        self.assertIn('const baigaLocked = baigaQrRequiredFor(user) && !sensitiveAccess.granted;', self.app)
        self.assertIn('const baigaChecking = baigaLocked && !sensitiveAccess.checked;', self.app)
        self.assertIn('checking={baigaChecking}', block[:otherwise])
        self.assertNotIn('sensitiveSections', block[:section])
        # Доступ к экрану считает предикат раздела, а не роль напрямую.
        self.assertIn('const canAccessBaigaSection = canAccessBaigaSectionForUser(user);', self.app)

    def test_menu_item_is_declared_once_in_the_common_part(self):
        self.assertEqual(self.app.count("handleSidebarViewNavigation(e, 'baiga')"), 1)
        item = self.app.split("handleSidebarViewNavigation(e, 'baiga')")[1][:700]
        self.assertIn('Списки Байги', item)
        # Пункт показывает ровно условие доступа — ни шире, ни уже.
        wrapper = self.app.split("handleSidebarViewNavigation(e, 'baiga')")[0][-420:]
        self.assertIn('{canAccessBaigaSection && (\n'
                      '                                    <SidebarDeptScope section="baiga" '
                      'activeCode={activeDeptCode}>\n', wrapper)
        self.assertLess(self.app.index("handleSidebarViewNavigation(e, 'thermoboxes')"),
                        self.app.index("handleSidebarViewNavigation(e, 'baiga')"))

    def test_department_lists_are_the_same_on_both_sides(self):
        self.assertIn("const BAIGA_MANAGE_DEPARTMENT_CODE = '%s';" % access.MANAGE_DEPARTMENT_CODE, self.app)
        self.assertIn('const BAIGA_READ_DEPARTMENT_CODES = [%s];'
                      % ', '.join("'%s'" % code for code in access.READ_DEPARTMENT_CODES), self.app)
        self.assertIn('const BAIGA_SECTION_DEPARTMENT_CODES = '
                      '[BAIGA_MANAGE_DEPARTMENT_CODE, ...BAIGA_READ_DEPARTMENT_CODES];', self.app)
        self.assertEqual(access.SECTION_DEPARTMENT_CODES, ('marketing', 'op', 'szov'))

    def _front_answers(self, users, analysts=None, predicate='canAccessBaigaSectionForUser', pilot=None):
        """Настоящий предикат из App.jsx, выполненный node, — поведение, а не текст.

        analysts — подставить свой список аналитиков вместо боевого.
        predicate — что спросить: пункт меню или замок раздела.
        pilot — подставить своё значение выключателя «только супер-админ»."""
        node = shutil.which('node')
        if not node:
            self.skipTest('node недоступен')
        app = self.app
        normalize = re.search(r'^const normalizeDepartmentCode = .*$', app, re.M).group(0)
        start = app.index('const aiQaHeadDepartmentCodesOf = (userLike) => {')
        head_codes = app[start:app.index('\n};\n', start) + 3]
        start = app.index('const SENSITIVE_QR_GATED_ROLES = ')
        qr_lock = app[start:app.index('\n);\n', app.index('const sensitiveSectionQrRequiredFor = ')) + 3]
        start = app.index('const BAIGA_MANAGE_DEPARTMENT_CODE = ')
        section = app[start:app.index('\n);\n', app.index('const baigaQrRequiredFor = ')) + 3]
        self.assertIn('const canAccessBaigaSectionForUser = ', section)
        if analysts is not None:
            section, replaced = re.subn(r'const BAIGA_ANALYST_USER_IDS = new Set\(\[[^\]]*\]\);',
                                        'const BAIGA_ANALYST_USER_IDS = new Set(%s);' % json.dumps(list(analysts)),
                                        section)
            self.assertEqual(replaced, 1)
        if pilot is not None:
            section, replaced = re.subn(r'const BAIGA_PILOT_SUPER_ADMIN_ONLY = (?:true|false);',
                                        'const BAIGA_PILOT_SUPER_ADMIN_ONLY = %s;' % str(bool(pilot)).lower(),
                                        section)
            self.assertEqual(replaced, 1)
        script = '\n'.join((
            "import { normalizeRole, isDepartmentHead, isSupervisorRole } from '%s';"
            % (ROOT / 'src' / 'utils' / 'roles.js').as_uri(),
            normalize, head_codes, qr_lock, section,
            'const users = %s;' % json.dumps(users),
            'process.stdout.write(JSON.stringify(users.map((u) => %s(u))));' % predicate,
        ))
        out = subprocess.run([node, '--input-type=module', '-e', script], capture_output=True, check=True)
        answers = json.loads(out.stdout.decode('utf-8'))
        self.assertEqual(len(answers), len(users))
        return answers

    @staticmethod
    def _front_user(role, code, heads, user_id=10):
        return {'id': user_id, 'role': role, 'department_code': code,
                'headed_department_id': 1 if heads else None, 'headed_department_codes': list(heads)}

    def test_predicate_answers_like_the_server(self):
        """Пункт меню и сервер обязаны совпадать: иначе человек видит пункт, а
        раздел отвечает отказом, — или доступ выдан, а пункта нет."""
        cases = grid()
        front = self._front_answers([self._front_user(*case) for case in cases])
        shown = 0
        for (role, code, heads), answer in zip(cases, front):
            ctx = person(role=role, department_code=code, headed_codes=heads)
            self.assertEqual(answer, access.can_open_section(ctx), (role, code, heads))
            shown += answer
        # Сетка не вырождена: открыто заметной части и далеко не всем.
        self.assertGreater(shown, 50)
        self.assertLess(shown, len(cases) - 50)

    def test_predicate_ignores_case_and_spaces_in_codes_like_the_server(self):
        cases = [('operator', ' OP ', ()), ('sv', 'SZOV', ()), ('operator', 'Marketing', ()),
                 ('admin', None, (' Marketing ',)), ('admin', None, ('OP',)), ('SV', 'op', ()),
                 ('Supervisor', 'szov', ())]
        front = self._front_answers([self._front_user(*case) for case in cases])
        self.assertEqual(front, [True] * len(cases))
        for role, code, heads in cases:
            self.assertTrue(access.can_open_section(person(role=role, department_code=code, headed_codes=heads)),
                            (role, code, heads))

    def test_analyst_by_id_gets_the_menu_item_like_on_the_server(self):
        users = [self._front_user('operator', 'tez', (), user_id=user_id) for user_id in (77, 78)]
        self.assertEqual(self._front_answers(users, analysts=(77,)), [True, False])
        with mock.patch.object(access, 'ANALYST_USER_IDS', frozenset({77})):
            self.assertEqual([access.can_open_section(person(department_code='tez', user_id=user_id))
                              for user_id in (77, 78)], [True, False])

    def test_named_analyst_sees_the_menu_item_and_no_lock(self):
        """Боевой список, без подстановок: сотрудник отдела аналитики видит
        пункт и входит без замка, его сосед по отделу — нет."""
        users = [self._front_user('operator', 'analytik', (), user_id=user_id)
                 for user_id in (ANALYST_ID, ANALYST_ID + 1)]
        self.assertEqual(self._front_answers(users), [True, False])
        self.assertEqual(self._front_answers(users, predicate='baigaQrRequiredFor'), [False, True])
        # Id в профиле бывает и строкой — пункт и замок от этого не меняются.
        as_text = [dict(users[0], id=str(ANALYST_ID))]
        self.assertEqual(self._front_answers(as_text), [True])
        self.assertEqual(self._front_answers(as_text, predicate='baigaQrRequiredFor'), [False])

    def test_switch_closes_the_analyst_on_the_screen_like_on_the_server(self):
        """Выключатель «только супер-админ» стоит раньше списка аналитиков на
        обеих сторонах: включили — пункта меню нет и у аналитика, а сервер
        отвечает ему «раздел закрыт». Разойдись порядок — человек видел бы
        пункт, за которым каждая ручка отвечает отказом."""
        users = [self._front_user('operator', 'analytik', (), user_id=ANALYST_ID),
                 self._front_user('super_admin', None, ()),
                 self._front_user('admin', 'marketing', ('marketing',)),
                 self._front_user('operator', 'op', ())]
        self.assertEqual(self._front_answers(users, pilot=True), [False, True, False, False])
        self.assertEqual(self._front_answers(users, pilot=False), [True, True, True, True])
        with mock.patch.object(access, 'PILOT_SUPER_ADMIN_ONLY', True):
            self.assertEqual(
                [access.can_open_section(person(role='operator', department_code='analytik', user_id=ANALYST_ID)),
                 access.can_open_section(person(role='super_admin', department_code=None)),
                 access.can_open_section(person(role='admin', department_code='marketing', headed_codes=('marketing',))),
                 access.can_open_section(person(department_code='op'))],
                [False, True, False, False])

    def test_lock_on_the_screen_answers_like_the_server(self):
        """Замок экрана и замок сервера обязаны совпадать у каждого, кому раздел
        открыт: иначе экран рисует раздел, а ручки отвечают «нужен QR», — или
        человек видит замок там, где сервер пускает. Сверяем на всех сочетаниях
        роли, отдела и главенства — для человека из списка и для чужого id."""
        compared = asked = 0
        # По одному id за запуск node: сетка на оба id разом не влезает в
        # предел длины одного аргумента командной строки (Linux, 128 КиБ).
        for user_id in (77, 78):
            users = [self._front_user(role, code, heads, user_id=user_id) for role, code, heads in grid()]
            opened = self._front_answers(users, analysts=(77,))
            locked = self._front_answers(users, analysts=(77,), predicate='baigaQrRequiredFor')
            with mock.patch.object(access, 'ANALYST_USER_IDS', frozenset({77})):
                for (role, code, heads), is_open, is_locked in zip(grid(), opened, locked):
                    ctx = person(role=role, department_code=code, headed_codes=heads, user_id=user_id)
                    self.assertEqual(is_open, access.can_open_section(ctx), (role, code, heads, user_id))
                    if not is_open:
                        continue
                    self.assertEqual(is_locked, access.requires_sensitive_qr(ctx), (role, code, heads, user_id))
                    compared += 1
                    asked += is_locked
        # Сетка не вырождена: есть и те, кого замок спрашивает, и те, кого нет.
        self.assertGreater(asked, 5)
        self.assertGreater(compared - asked, 400)

    def test_menu_never_leads_past_the_qr_lock(self):
        """Должность, которой портал QR не выдаёт (кадровик), пункта не видит:
        сервер свёл бы её к оператору и спросил бы код, который взять негде."""
        cases = [('hr_manager', code, ()) for code in access.SECTION_DEPARTMENT_CODES]
        front = self._front_answers([self._front_user(*case) for case in cases])
        self.assertEqual(front, [False] * len(cases))
        for role, code, heads in cases:
            ctx = person(role=role, department_code=code, headed_codes=heads)
            self.assertTrue(not access.can_open_section(ctx) or access.requires_sensitive_qr(ctx), code)

    def test_qr_status_is_asked_when_the_section_opens(self):
        """Без этого оператор, не заходивший до «Списков Байги» в другой закрытый
        раздел, остаётся на «Проверяем доступ…» без кнопки QR."""
        self.assertIn("|| view === 'baiga' || view === 'driver_mailings') {\n"
                      "                    fetchSensitiveAccessStatus();", self.app)

    def test_direct_link_without_access_leads_away_not_to_a_blank_screen(self):
        """У СЗоВ нет allowlist'а разделов, и гард отдела закрытого стажёра не
        уведёт: без своей ветки ссылка ?view=baiga оставляла пустой экран."""
        effect = self.app.split("if (view === 'baiga' && !canAccessBaigaSection) {")[1]
        branch, rest = effect.split('\n                }\n', 1)
        self.assertIn("redirectToView('hours');", branch)
        self.assertIn("redirectToView('sv_list');", branch)
        deps = rest.split('}, [', 1)[1].split(']);', 1)[0]
        self.assertIn('canAccessBaigaSection', deps)

    def test_subtitle_promises_export_only_to_those_who_have_it(self):
        view = _read(ROOT / 'src' / 'components' / 'baiga' / 'BaigaView.jsx')
        self.assertIn('subtitle={capabilities.can_export', view)
        self.assertNotIn('аналитик загрузит', view)

    def test_buttons_follow_the_capabilities(self):
        """Выгрузка — по can_export, вкладка журнала, загрузка и её окно — по
        can_manage; сам журнал открывается только с can_manage."""
        view = _read(ROOT / 'src' / 'components' / 'baiga' / 'BaigaView.jsx')
        self.assertIn("const capabilities = screen?.capabilities || {};", view)
        self.assertEqual(view.count("{capabilities.can_export && tab === 'search' && ("), 1)
        self.assertEqual(view.count('{capabilities.can_manage && ('), 3)
        self.assertIn("if (tab === 'journal' && capabilities.can_manage) {", view)
        self.assertEqual(len(re.findall(r'capabilities\.can_\w+', view)),
                         len(re.findall(r'capabilities\.can_(?:export|manage)\b', view)))

    def test_guards_and_registries(self):
        self.assertIn("if (view === 'baiga' && canAccessBaigaSection) return;", self.app)
        self.assertIn("    baiga: ['marketing', 'op', 'szov'],", self.app)
        self.assertIn("    baiga: 'Baiga lists',", self.app)
        self.assertIn("canAccessBaigaSection && deptAllowsInner('baiga'),", self.app)
        trainer = self.app.split('const TRAINER_ALLOWED_VIEWS = Object.freeze([')[1].split(']);')[0]
        self.assertNotIn("'baiga'", trainer)
        deps = self.app.split("if (view === 'baiga' && canAccessBaigaSection) return;")[1]
        deps = deps.split('}, [')[1].split(']);')[0]
        self.assertIn('canAccessBaigaSection', deps)

    def test_section_params_leave_the_address_with_the_section(self):
        for fn in ('const buildAppViewUrl = (nextView) => {', 'const syncAppViewWithUrl = (nextView) => {'):
            body = self.app.split(fn)[1].split('\n};')[0]
            self.assertIn("if (nextView !== 'baiga') stripBaigaParams(url);", body)

    def test_mirrors_of_server_constants(self):
        meta = _read(META_JS)
        self.assertIn('export const PAGE_SIZES = [%s];' % ', '.join(map(str, filters.PAGE_SIZES)), meta)
        self.assertIn("export const PRIZE_ANY = '%s';" % filters.PRIZE_ANY, meta)
        self.assertIn("export const PRIZE_NONE = '%s';" % filters.PRIZE_NONE, meta)
        for key in filters.SORT_COLUMNS:
            self.assertRegex(meta, r"\b%s: '" % key)
        for key in filters.RANGES:
            self.assertIn("'%s'" % key, meta)

    def test_menu_icons_are_components_not_elements(self):
        folder = ROOT / 'src' / 'components' / 'baiga'
        for path in folder.glob('*.jsx'):
            source = _read(path)
            for menu in re.findall(r'(?s)const \w*[mM]enuItems = \[(.*?)\];', source):
                self.assertNotRegex(menu, r'icon:\s*<', path.name)


if __name__ == '__main__':
    unittest.main()
