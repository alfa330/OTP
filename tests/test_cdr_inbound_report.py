# -*- coding: utf-8 -*-
"""«Отчёт по входу» (cdr/inbound_report.py): расчёт по дням и очередям и книга.

Что здесь закреплено — решения владельца 11.11.2026 и форма образца:

* звонком считается только входящий, дошедший до очереди, а показатели — тем же
  правилом, что на «Табло ОП» (иначе отчёт и стена спорили бы об одном дне);
* план звонков — факт той же очереди в тот же день недели неделей раньше;
* «ОСС», часы и «CR%» портал не заполняет: клетки пустые, доли часов — формулами,
  которые молчат, пока часы не вписаны;
* лист на день; на нём только очереди, в которые в этот день звонили (владелец,
  12.11.2026), в порядке образца; очередь не из образца со звонками не теряется;
* номеров линий в модуле отчёта нет: репозиторий публичный, таблица номеров компании
  лежит в конфиге линий в bot_schedule2.py и приходит в книгу аргументом.

Номера в тестах учебные (7XX555XXXX).
"""

import ast
import io
import os
import unittest
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from io import BytesIO
from unittest import mock

from openpyxl import load_workbook

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from cdr import inbound_report as R, queries, routes, touches as T
from op_wallboard import snapshot
from test_numbers import keys as test_keys


def call(day, queue, *, talk=0, wait=None, clock='10:00:00', missed=False):
    """Входящий в виде `queries.incoming_touches_compact`."""
    answered = talk > 0 and not missed
    return {
        'call_day': day, 'started_at': '%s %s' % (day, clock), 'answered_at': '',
        'ext': '6101' if answered else '', 'queue': queue,
        'call_type': T.TYPE_IN if answered else T.TYPE_IN_MISSED,
        'result': 'Разговор' if answered else 'Занято',
        'talk_seconds': talk if answered else 0, 'dial_seconds': 0, 'linkedid': '',
        'queued_at': '', 'wait_seconds': wait,
        'talk_measured_seconds': talk if answered else None, 'hangup_side': '',
    }


MON, TUE = '2026-11-02', '2026-11-03'
PREV_MON = '2026-10-26'

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Учебные номера линий: у iTaxi три номера (три строки листа), у Jana один.
TEST_LINES = {'3001': ('77015550001', '77015550002', '77015550003'), '3034': ('77015550034',)}
TEMPLATE_QUEUES = [queue for queue, _park in R.QUEUES]


def company_lines():
    """Таблица номеров из конфига линий монолита — разбором исходника: импорт
    bot_schedule2 поднимает всё приложение."""
    with io.open(os.path.join(ROOT, 'bot_schedule2.py'), encoding='utf-8') as handle:
        source = handle.read()
    start = source.index('_CDR_INBOUND_REPORT_LINES = {')
    end = source.index('\n    }\n', start) + len('\n    }')
    return source, ast.literal_eval(source[source.index('{', start):end])


def queue_row(sheet, queue):
    """Первая строка очереди на листе — по колонке «Очередь»."""
    return next(row for row in range(R.FIRST_DATA_ROW, sheet.max_row + 1)
                if sheet.cell(row=row, column=3).value == int(queue))


def ref(key, row):
    """Адрес клетки по ключу колонки: буквы меняются, когда в отчёт добавляют столбцы."""
    return '%s%d' % (R._LETTER[key], row)


def cell_of(sheet, key, row):
    return sheet[ref(key, row)]


def sample_touches():
    return [
        # Понедельник, Jana: два приняты (ожидание 5 и 40 с), один потерян.
        call(MON, '3034', talk=100, wait=5),
        call(MON, '3034', talk=51, wait=40),
        call(MON, '3034', wait=30, missed=True),
        # Понедельник, iTaxi: один принят за 10 с.
        call(MON, '3001', talk=60, wait=10),
        # Неделей раньше Jana приняла четыре звонка — это план понедельника.
        call(PREV_MON, '3034', talk=10, wait=1), call(PREV_MON, '3034', talk=10, wait=1),
        call(PREV_MON, '3034', talk=10, wait=1), call(PREV_MON, '3034', talk=10, wait=1),
        # Вторник пустой у Jana; у очереди не из образца — один звонок.
        call(TUE, '3042', talk=30, wait=3),
    ]


def row_of(table, queue):
    return next(row for row in table['rows'] if row['queue'] == queue)


class TemplateTests(unittest.TestCase):

    def test_queues_are_the_owner_sample(self):
        self.assertEqual(len(TEMPLATE_QUEUES), 22)
        self.assertEqual(len(set(TEMPLATE_QUEUES)), 22)
        self.assertEqual(TEMPLATE_QUEUES[0], '3000')
        self.assertEqual(TEMPLATE_QUEUES[-1], '3077')

    def test_report_module_holds_no_phone_numbers(self):
        # Репозиторий публичный: таблица номеров компании допускается только в
        # bot_schedule2.py (tests/test_no_personal_data_in_repo.py). 11.11.2026 она лежала
        # здесь, и страж уронил прогон main.
        with io.open(os.path.join(ROOT, 'cdr', 'inbound_report.py'), encoding='utf-8') as handle:
            self.assertNotRegex(handle.read(), r'(?<![0-9])(?:\+?7|8)7[0-9]{9}(?![0-9])')

    def test_company_lines_live_in_the_line_config_and_reach_the_report(self):
        source, lines = company_lines()
        # Те же очереди, что в образце, и строки 3–46 листа: по строке на номер.
        self.assertEqual(sorted(lines), sorted(TEMPLATE_QUEUES))
        numbers = [number for queue in TEMPLATE_QUEUES for number in lines[queue]]
        self.assertEqual(len(numbers), 44)
        self.assertEqual(len(set(numbers)), 44)
        for number in numbers:
            self.assertRegex(number, r'^7\d{10}$')
        self.assertIn('inbound_line_numbers=_CDR_INBOUND_REPORT_LINES,', source)
        # День, когда звонили во все очереди образца, — лист один в один с образцом.
        everywhere = [call(MON, queue, talk=30, wait=1) for queue in TEMPLATE_QUEUES]
        stream, _days = R.build_workbook(everywhere, day_from=date(2026, 11, 2),
                                         day_to=date(2026, 11, 2), line_numbers=lines)
        sheet = load_workbook(stream)['02.11']
        self.assertEqual([sheet['B%d' % row].value for row in range(3, 47)],
                         [int(number) for number in numbers])
        self.assertEqual(sheet['A47'].value, 'Итого')

    def test_headers_follow_the_sample(self):
        titles = [title for _key, title, _norm, _width, _fmt in R.COLUMNS]
        self.assertEqual(titles, [
            'Дата', 'Номера', 'Очередь', 'Парк', 'План звонки', 'Факт звонки', '% совпадения',
            'Принято', 'AR%', 'Сбросы на ожидании до 5 сек', 'Сбросы на ожидании от 5 сек',
            'ОСС', 'План часов', 'Факт часов', '% выработки часов', '% невыходов', 'AHT',
            'SL/20сек', 'CR%'])
        # Образец кончается на «AR%» и продолжается с «ОСС»: два столбца сбросов — между ними.
        keys = [key for key, _title, _norm, _width, _fmt in R.COLUMNS]
        self.assertEqual(keys[keys.index('ar') + 1:keys.index('occupancy')],
                         ['drops_short', 'drops_long'])
        norms = {title: norm for _key, title, norm, _width, _fmt in R.COLUMNS if norm}
        self.assertEqual(norms, {'AR%': 'норма >5%', 'ОСС': 'норма >67%', 'SL/20сек': 'норма 80%'})


class DayTablesTests(unittest.TestCase):

    def setUp(self):
        self.tables = R.day_tables(sample_touches(), date(2026, 11, 2), date(2026, 11, 3))
        self.monday, self.tuesday = self.tables

    def test_one_table_per_day_of_period(self):
        self.assertEqual([table['day'] for table in self.tables],
                         [date(2026, 11, 2), date(2026, 11, 3)])

    def test_queue_numbers_are_counted_like_the_wallboard(self):
        jana = row_of(self.monday, '3034')
        self.assertEqual((jana['fact'], jana['answered']), (3, 2))
        self.assertAlmostEqual(jana['ar'], 1 / 3)
        # В срок (≤ 20 с) принят один из трёх ДОШЕДШИХ до очереди, а не из двух принятых.
        self.assertAlmostEqual(jana['sl'], 1 / 3)
        # Разговор усекается до секунды, как на стене: (100 + 51) / 2 = 75,5 → 75.
        self.assertEqual(jana['aht'], 75)
        # Те же три звонка стена считает так же — правило одно.
        own = snapshot.aggregate([t for t in sample_touches()
                                  if t['call_day'] == MON and t['queue'] == '3034'])['totals']
        self.assertEqual((jana['fact'], jana['answered'], jana['ar'], jana['sl'], jana['aht']),
                         (own['arrived'], own['answered'], own['ar'], own['sl'],
                          own['avg_talk_seconds']))

    def test_drops_are_split_by_the_wait_in_the_queue(self):
        touches = [
            call(MON, '3034', talk=60, wait=2),            # принят — не сброс
            call(MON, '3034', wait=0, missed=True),        # бросил сразу
            call(MON, '3034', wait=4, missed=True),        # 4 с — «до 5»
            call(MON, '3034', wait=5, missed=True),        # ровно 5 с — уже «от 5»
            call(MON, '3034', wait=120, missed=True),
            call(MON, '3034', wait=None, missed=True),     # ожидание неизвестно — никуда
        ]
        self.assertEqual(R.drops(touches), (2, 2))
        table = R.day_tables(touches, date(2026, 11, 2), date(2026, 11, 2))[0]
        jana = row_of(table, '3034')
        self.assertEqual((jana['fact'], jana['answered']), (6, 1))
        self.assertEqual((jana['drops_short'], jana['drops_long']), (2, 2))
        self.assertEqual((table['total']['drops_short'], table['total']['drops_long']), (2, 2))

    def test_no_drops_is_zero_not_blank(self):
        itaxi = row_of(self.monday, '3001')
        self.assertEqual((itaxi['drops_short'], itaxi['drops_long']), (0, 0))
        jana = row_of(self.monday, '3034')
        self.assertEqual((jana['drops_short'], jana['drops_long']), (0, 1))
        self.assertEqual(jana['fact'] - jana['answered'], jana['drops_short'] + jana['drops_long'])

    def test_plan_is_the_same_weekday_a_week_before(self):
        jana = row_of(self.monday, '3034')
        self.assertEqual(jana['plan'], 4)
        self.assertAlmostEqual(jana['match'], 3 / 4)
        # У iTaxi неделей раньше звонков не было: плана нет, и делить не на что.
        itaxi = row_of(self.monday, '3001')
        self.assertIsNone(itaxi['plan'])
        self.assertIsNone(itaxi['match'])

    def test_queues_without_calls_are_hidden(self):
        # Из 22 очередей образца в понедельник звонили в две — на листе только они,
        # в порядке образца (3001 раньше 3034).
        self.assertEqual([row['queue'] for row in self.monday['rows']], ['3001', '3034'])
        self.assertEqual([row['queue'] for row in self.tuesday['rows']], ['3042'])
        for row in self.monday['rows'] + self.tuesday['rows']:
            self.assertGreater(row['fact'], 0)

    def test_queue_with_a_plan_but_no_calls_today_is_hidden_too(self):
        # 09.11 — понедельник: план есть (факт 02.11), а звонков в этот день нет.
        table = R.day_tables(sample_touches(), date(2026, 11, 9), date(2026, 11, 9))[0]
        self.assertEqual(table['rows'], [])
        self.assertIsNone(table['total']['fact'])
        self.assertIsNone(table['total']['plan'])

    def test_total_is_the_whole_day_not_an_average_of_rows(self):
        total = self.monday['total']
        self.assertEqual((total['fact'], total['answered'], total['plan']), (4, 3, 4))
        self.assertAlmostEqual(total['ar'], 1 / 4)
        self.assertAlmostEqual(total['sl'], 2 / 4)
        # (100 + 51 + 60) / 3 = 70,3 → 70; среднее строк дало бы (75 + 60) / 2 = 67.
        self.assertEqual(total['aht'], 70)
        self.assertEqual(sum(row['fact'] or 0 for row in self.monday['rows']), total['fact'])

    def test_total_plan_is_the_sum_of_shown_rows(self):
        # Неделей раньше звонили ещё и в «Ноль такси», сегодня — нет: очередь скрыта, и её
        # план в «Итого» не входит, иначе колонка не сходилась бы со своим итогом.
        touches = sample_touches() + [call(PREV_MON, '3041', talk=20, wait=2)]
        table = R.day_tables(touches, date(2026, 11, 2), date(2026, 11, 2))[0]
        self.assertNotIn('3041', [row['queue'] for row in table['rows']])
        self.assertEqual(table['total']['plan'], 4)
        self.assertEqual(sum(row['plan'] or 0 for row in table['rows']), table['total']['plan'])

    def test_queue_outside_the_sample_is_shown_after_the_sample_ones(self):
        extra = row_of(self.tuesday, '3042')
        self.assertEqual((extra['fact'], extra['park'], extra['numbers']), (1, 'Достойный', ()))
        self.assertEqual(self.tuesday['total']['fact'], 1)
        touches = [call(MON, '3042', talk=30, wait=3), call(MON, '2999', talk=30, wait=3),
                   call(MON, '3077', talk=30, wait=3), call(MON, '3000', talk=30, wait=3)]
        table = R.day_tables(touches, date(2026, 11, 2), date(2026, 11, 2))[0]
        self.assertEqual([row['queue'] for row in table['rows']], ['3000', '3077', '2999', '3042'])

    def test_line_numbers_come_from_outside(self):
        tables = R.day_tables(sample_touches(), date(2026, 11, 2), date(2026, 11, 2),
                              line_numbers=TEST_LINES)
        self.assertEqual(row_of(tables[0], '3001')['numbers'], TEST_LINES['3001'])
        self.assertEqual(row_of(tables[0], '3034')['numbers'], TEST_LINES['3034'])
        # Без номеров в конфиге — строка с пустой клеткой, а не пропуск очереди.
        self.assertEqual(row_of(self.monday, '3001')['numbers'], ())

    def test_extra_queue_from_the_plan_week_only_is_not_a_row(self):
        touches = sample_touches() + [call(PREV_MON, '3033', talk=5, wait=1)]
        tables = R.day_tables(touches, date(2026, 11, 2), date(2026, 11, 2))
        self.assertNotIn('3033', [row['queue'] for row in tables[0]['rows']])

    def test_transferred_call_belongs_to_its_first_queue(self):
        tables = R.day_tables([call(MON, '3034,3001', talk=20, wait=2)],
                              date(2026, 11, 2), date(2026, 11, 2))
        self.assertEqual([row['queue'] for row in tables[0]['rows']], ['3034'])
        self.assertEqual(row_of(tables[0], '3034')['fact'], 1)
        self.assertEqual(tables[0]['total']['fact'], 1)


class WorkbookTests(unittest.TestCase):

    def setUp(self):
        stream, self.days = R.build_workbook(
            sample_touches(), day_from=date(2026, 11, 2), day_to=date(2026, 11, 3),
            generated_at=datetime(2026, 11, 11, 12, 30), generated_by='Проверяющий',
            line_numbers=TEST_LINES)
        self.book = load_workbook(stream)
        self.sheet = self.book['02.11']
        # В понедельник звонили в iTaxi (три строки номеров) и Jana: строки 3–6.
        self.jana = queue_row(self.sheet, '3034')
        self.last = 6
        self.total = 7

    def test_sheet_per_day_then_notes(self):
        self.assertEqual(self.days, 2)
        self.assertEqual(self.book.sheetnames, ['02.11', '03.11', R.NOTES_SHEET])

    def test_header_rows(self):
        self.assertEqual(self.sheet['A1'].value, 'Дата')
        self.assertEqual(self.sheet['F1'].value, 'Факт звонки')
        self.assertEqual(self.sheet['I2'].value, 'норма >5%')
        self.assertEqual(cell_of(self.sheet, 'sl', 2).value, 'норма 80%')
        self.assertEqual(cell_of(self.sheet, 'drops_short', 1).value, 'Сбросы на ожидании до 5 сек')
        merged = {str(area) for area in self.sheet.merged_cells.ranges}
        # Колонка без нормы — одна клетка на обе строки шапки; с нормой — две.
        self.assertIn('F1:F2', merged)
        self.assertNotIn('I1:I2', merged)

    def test_queue_block_spans_its_numbers(self):
        merged = {str(area) for area in self.sheet.merged_cells.ranges}
        # У iTaxi три номера: строки 3–5.
        self.assertEqual([self.sheet['B%d' % row].value for row in (3, 4, 5)],
                         [int(number) for number in TEST_LINES['3001']])
        self.assertEqual(self.jana, 6)
        self.assertEqual(self.sheet['B%d' % self.jana].value, int(TEST_LINES['3034'][0]))
        # Очередь без номеров (вторник, «Достойный») — одна строка с пустой клеткой.
        tuesday = self.book['03.11']
        self.assertEqual(queue_row(tuesday, '3042'), 3)
        self.assertIsNone(tuesday['B3'].value)
        self.assertEqual(tuesday['A4'].value, 'Итого')
        self.assertEqual(self.sheet['C3'].value, 3001)
        self.assertEqual(self.sheet['D3'].value, 'iTaxi вход')
        for key in ('queue', 'park', 'plan', 'fact', 'match', 'answered', 'ar', 'drops_short',
                    'drops_long', 'aht', 'sl'):
            self.assertIn('%s:%s' % (ref(key, 3), ref(key, 5)), merged)
        # Дата и дневные колонки (часы, ОСС, CR%) — одной клеткой на все очереди.
        for key in ('date', 'occupancy', 'hours_plan', 'hours_fact', 'hours_done', 'hours_missed',
                    'cr'):
            self.assertIn('%s:%s' % (ref(key, 3), ref(key, self.last)), merged)
        self.assertEqual(self.sheet['A3'].value, datetime(2026, 11, 2))

    def test_portal_numbers_are_values_not_formulas(self):
        def cell(key):
            return cell_of(self.sheet, key, self.jana)

        self.assertEqual((cell('plan').value, cell('fact').value, cell('answered').value,
                          cell('aht').value), (4, 3, 2, 75))
        self.assertAlmostEqual(cell('match').value, 0.75)
        self.assertAlmostEqual(cell('ar').value, 1 / 3)
        self.assertAlmostEqual(cell('sl').value, 1 / 3)
        self.assertEqual(cell('ar').number_format, '0.0%')
        self.assertEqual(cell('match').number_format, '0%')
        # Один потерянный, ждал 30 секунд: «от 5 сек».
        self.assertEqual((cell('drops_short').value, cell('drops_long').value), (0, 1))

    def test_hours_are_blank_and_formulas_wait_for_them(self):
        plan, fact, done = ref('hours_plan', 3), ref('hours_fact', 3), ref('hours_done', 3)
        for address in (plan, fact):
            self.assertIsNone(self.sheet[address].value, address)
        self.assertEqual(self.sheet[done].value,
                         '=IF(OR(%s="",%s=""),"",%s/%s)' % (plan, fact, fact, plan))
        self.assertEqual(cell_of(self.sheet, 'hours_missed', 3).value,
                         '=IF(OR(%s="",%s=""),"",(%s-%s)/%s)' % (plan, fact, plan, fact, plan))
        # Итог повторяет дневное значение и не показывает ноль на пустой клетке.
        self.assertEqual(cell_of(self.sheet, 'hours_plan', self.total).value,
                         '=IF(%s="","",%s)' % (plan, plan))
        self.assertEqual(cell_of(self.sheet, 'hours_done', self.total).value,
                         '=IF(%s="","",%s)' % (done, done))

    def test_occ_and_cr_are_blank_without_staffing(self):
        for key in ('occupancy', 'cr'):
            for row in (3, self.total):
                self.assertIsNone(cell_of(self.sheet, key, row).value, (key, row))
        self.assertIsNone(cell_of(self.sheet, 'cr', 3).comment)

    def test_occ_and_cr_are_day_values_with_their_source_in_a_note(self):
        staffing = {date(2026, 11, 2): {
            'shifts_plan': 14, 'shifts_fact': 8, 'cr': 8 / 14,
            'online_seconds': 61 * 3600 + 720, 'busy_seconds': 18 * 3600 + 1440, 'occ': 0.3}}
        stream, _days = R.build_workbook(
            sample_touches(), day_from=date(2026, 11, 2), day_to=date(2026, 11, 3),
            line_numbers=TEST_LINES, staffing=staffing)
        book = load_workbook(stream)
        sheet = book['02.11']
        occ, cr = cell_of(sheet, 'occupancy', 3), cell_of(sheet, 'cr', 3)
        self.assertAlmostEqual(occ.value, 0.3)
        self.assertAlmostEqual(cr.value, 8 / 14)
        # В «Итого» — те же числа, а не формула: файл читают и там, где формул не считают.
        self.assertAlmostEqual(cell_of(sheet, 'occupancy', self.total).value, 0.3)
        self.assertAlmostEqual(cell_of(sheet, 'cr', self.total).value, 8 / 14)
        self.assertEqual((occ.number_format, cr.number_format), ('0.0%', '0%'))
        self.assertEqual(cr.comment.text, 'Смен по графику: 14, отработано: 8')
        self.assertEqual(occ.comment.text,
                         'На линии: 61,2 ч, из них в разговоре и наборе номера: 18,4 ч')
        # День, по которому показателей нет, остаётся пустым и без примечаний.
        tuesday = book['03.11']
        self.assertIsNone(cell_of(tuesday, 'cr', 3).value)
        self.assertIsNone(cell_of(tuesday, 'occupancy', 3).comment)

    def test_day_without_shifts_keeps_cr_blank_but_shows_occ(self):
        staffing = {date(2026, 11, 2): {
            'shifts_plan': 0, 'shifts_fact': 0, 'cr': None,
            'online_seconds': 7200, 'busy_seconds': 3600, 'occ': 0.5}}
        stream, _days = R.build_workbook(
            sample_touches(), day_from=date(2026, 11, 2), day_to=date(2026, 11, 2),
            staffing=staffing)
        sheet = load_workbook(stream)['02.11']
        self.assertIsNone(cell_of(sheet, 'cr', 3).value)
        self.assertIsNone(cell_of(sheet, 'cr', 3).comment)
        self.assertAlmostEqual(cell_of(sheet, 'occupancy', 3).value, 0.5)

    def test_total_row(self):
        def cell(key):
            return cell_of(self.sheet, key, self.total)

        self.assertEqual(self.sheet.max_row, self.total)
        self.assertEqual(cell('date').value, 'Итого')
        self.assertIn('A%d:D%d' % (self.total, self.total),
                      {str(area) for area in self.sheet.merged_cells.ranges})
        self.assertEqual((cell('plan').value, cell('fact').value, cell('answered').value,
                          cell('aht').value), (4, 4, 3, 70))
        self.assertAlmostEqual(cell('ar').value, 0.25)
        self.assertAlmostEqual(cell('sl').value, 0.5)
        self.assertEqual((cell('drops_short').value, cell('drops_long').value), (0, 1))
        self.assertTrue(cell('date').font.bold)

    def test_ar_data_bar_covers_queue_rows(self):
        ranges = [str(rule.sqref) for rule in self.sheet.conditional_formatting]
        self.assertEqual(ranges, ['I3:I%d' % self.last])

    def test_day_without_any_call_keeps_the_date_and_manual_cells(self):
        stream, _days = R.build_workbook(sample_touches(), day_from=date(2026, 11, 9),
                                         day_to=date(2026, 11, 9), line_numbers=TEST_LINES)
        sheet = load_workbook(stream)['09.11']
        self.assertEqual(sheet['A3'].value, datetime(2026, 11, 9))
        self.assertEqual(sheet['D3'].value, 'Звонков не было')
        self.assertIsNone(sheet['C3'].value)
        self.assertEqual(sheet['A4'].value, 'Итого')
        self.assertIsNone(sheet['F4'].value)
        self.assertTrue(str(cell_of(sheet, 'hours_done', 3).value).startswith('=IF(OR('))

    def test_notes_explain_what_is_counted(self):
        notes = self.book[R.NOTES_SHEET]
        text = '\n'.join('%s|%s' % (row[0] or '', row[1] or '')
                         for row in notes.iter_rows(values_only=True))
        self.assertIn('Период|02.11.2026 — 03.11.2026', text)
        self.assertIn('Собрал|Проверяющий', text)
        self.assertIn('заявки автообзвона', text)
        self.assertIn('тот же день недели неделей раньше', text)
        # Доля названа формулой «что ÷ что», а не одним словом.
        self.assertIn('÷ Факт звонки', text)
        self.assertIn('÷ смены, поставленные операторам группы «Основа»', text)
        self.assertIn('не меньше 90 %', text)
        self.assertIn('на листе «Смены»', text)
        # Показателей смен в этой книге нет — нет и листа со сменами.
        self.assertNotIn(R.SHIFTS_SHEET, self.book.sheetnames)
        self.assertIn('÷ всё их время на линии за день', text)
        self.assertIn('Сбросы на ожидании|Непринятые звонки (Факт звонки − Принято)', text)
        self.assertIn('меньше 5 секунд и 5 секунд и больше', text)
        self.assertIn('Вписать вручную|«План часов» и «Факт часов»', text)
        self.assertNotIn('Нет данных станции', text)
        self.assertNotIn('не посчитаны', text)

    def test_failed_staffing_is_said_in_the_notes(self):
        stream, _days = R.build_workbook(
            sample_touches(), day_from=date(2026, 11, 2), day_to=date(2026, 11, 2),
            staffing=None, staffing_failed=True)
        book = load_workbook(stream)
        text = '\n'.join(str(row[0] or '') for row in book[R.NOTES_SHEET].iter_rows(values_only=True))
        self.assertIn('ОСС и CR% не посчитаны', text)
        self.assertIsNone(cell_of(book['02.11'], 'cr', 3).value)

    def test_missing_days_are_named(self):
        stream, _days = R.build_workbook(
            sample_touches(), day_from=date(2026, 11, 2), day_to=date(2026, 11, 3),
            missing_days=[date(2026, 10, 27), date(2026, 10, 26)])
        notes = load_workbook(stream)[R.NOTES_SHEET]
        text = '\n'.join(str(row[1] or '') for row in notes.iter_rows(values_only=True))
        self.assertIn('За 26.10.2026, 27.10.2026.', text)

    def test_filename_matches_the_frontend(self):
        self.assertEqual(R.report_filename(date(2026, 11, 2), date(2026, 11, 2)),
                         'Отчёт по входу 02.11.2026.xlsx')
        self.assertEqual(R.report_filename(date(2026, 11, 1), date(2026, 11, 10)),
                         'Отчёт по входу 01.11.2026 — 10.11.2026.xlsx')


class Recorder:
    """Курсор, который помнит запросы и ничего не возвращает."""

    def __init__(self, rows=()):
        self.calls = []
        self.rows = list(rows)

    def execute(self, sql, params=None):
        self.calls.append((' '.join(sql.split()), params))

    def fetchall(self):
        return self.rows


class QueryTests(unittest.TestCase):

    def test_only_incoming_that_reached_a_queue_and_no_test_numbers(self):
        cursor = Recorder()
        self.assertEqual(
            queries.incoming_touches_compact(cursor, date(2026, 10, 26), date(2026, 11, 3)), [])
        sql, params = cursor.calls[0]
        self.assertIn("call_day BETWEEN %s AND %s", sql)
        self.assertIn("call_type IN (%s, %s) AND queue <> ''", sql)
        self.assertEqual(params, (date(2026, 10, 26), date(2026, 11, 3),
                                  T.TYPE_IN, T.TYPE_IN_MISSED))
        self.assertIn(' '.join(test_keys.sql_not_test('cdr_touches.phone', digits=True).split()),
                      sql)

    def test_row_has_the_wallboard_shape_plus_the_day(self):
        row = (date(2026, 11, 2), datetime(2026, 11, 2, 10, 0, 0), None, '6101', T.TYPE_IN,
               'Разговор', 75, 80, '3034', '1762059600.1', datetime(2026, 11, 2, 10, 0, 5), 4,
               71, 'client')
        touch = queries.incoming_touches_compact(Recorder([row]), date(2026, 11, 2),
                                                 date(2026, 11, 2))[0]
        self.assertEqual(touch['call_day'], '2026-11-02')
        self.assertEqual(touch['started_at'], '2026-11-02 10:00:00')
        self.assertEqual((touch['queue'], touch['wait_seconds'], touch['talk_measured_seconds']),
                         ('3034', 4, 71))
        # Форма та же, что у касаний табло, — отчёт считает их его же функцией.
        day_row = row[1:]
        self.assertEqual({k: v for k, v in touch.items() if k != 'call_day'},
                         queries.day_touches_compact(Recorder([day_row]), date(2026, 11, 2))[0])


TODAY = date(2026, 11, 11)
ADMIN = {'user_id': 1, 'name': 'Админ', 'role': 'super_admin', 'department_id': None,
         'department_code': None, 'headed_department_ids': [], 'headed_department_codes': []}


class _FakeDb:
    def _get_cursor(self):
        @contextmanager
        def scope():
            yield Recorder([(1,)])
        return scope()


class _Queries:
    """Двойник cdr.queries: доступ, очередь суток и входящие периода."""

    def __init__(self, ctx=ADMIN, absent=()):
        self.ctx = ctx
        self.absent = {day.isoformat() for day in absent}
        self.enqueued = []
        self.asked = None

    def load_access_context(self, cursor, user_id):
        return dict(self.ctx)

    def day_states(self, cursor, day_from, day_to):
        days = [day_from + timedelta(days=i) for i in range((day_to - day_from).days + 1)]
        return [{'day': day.isoformat(), 'status': 'done', 'rows_fetched': 100, 'touches': 10,
                 'complete': True, 'claimed_at': None, 'finished_at': None, 'attempts': 1,
                 'error': None}
                for day in days if day.isoformat() not in self.absent
                and day.isoformat() not in {d.isoformat() for d in self.enqueued}]

    def enqueue_days(self, cursor, days, requested_by=None):
        self.enqueued.extend(days)
        return len(days)

    def agent_state(self, cursor):
        return {'last_seen_at': '2026-11-11T12:00:00'}

    def incoming_touches_compact(self, cursor, day_from, day_to):
        self.asked = (day_from, day_to)
        return sample_touches()

    def today_almaty(self):
        return TODAY

    def now_almaty(self):
        return datetime(2026, 11, 11, 12, 0, 0)


class _Staffing:
    """Двойник cdr.staffing_queries: двое в «Основе», у обоих смена 02.11 с 09 до 18.

    Первый отработал её целиком, второй ушёл в 15:00 — отработана одна смена из двух."""

    def __init__(self, operator_ids=(1, 2), broken=False):
        self.operator_ids = list(operator_ids)
        self.broken = broken
        self.windows = {}

    def roster(self, cursor, model, day_from, day_to):
        if self.broken:
            raise RuntimeError('таблица графиков недоступна')
        self.windows['roster'] = (model, day_from, day_to)
        return list(self.operator_ids)

    def memberships(self, cursor, operator_ids, day_from, day_to):
        return {operator_id: [{'id': operator_id, 'start_date': date(2026, 1, 1),
                               'end_date': None, 'group_id': 36, 'model': 'op_osnova'}]
                for operator_id in operator_ids}

    def operator_exts(self, cursor, operator_ids):
        return {operator_id: '61%02d' % operator_id for operator_id in operator_ids}

    def operator_names(self, cursor, operator_ids):
        return {1: 'Иванова Айгерим', 2: 'Карабек Ержан'}

    def scheduled_shifts(self, cursor, operator_ids, day_from, day_to):
        return [{'operator_id': operator_id, 'day': date(2026, 11, 2),
                 'start_time': time(9, 0), 'end_time': time(18, 0),
                 'shift_type': 'regular', 'breaks': [(780, 840)]}
                for operator_id in operator_ids]

    def phone_events(self, cursor, operator_ids, since, until):
        self.windows['events'] = (since, until)

        def day(logout):
            return [(datetime(2026, 11, 2, 9, 0), 'готов'), (datetime(2026, 11, 2, 9, 30), 'занят'),
                    (datetime(2026, 11, 2, 10, 30), 'готов'), (datetime(2026, 11, 2, 13, 0), 'перерыв'),
                    (datetime(2026, 11, 2, 14, 0), 'готов'), (datetime(2026, 11, 2, logout, 0), 'выключен')]
        return {1: day(18), 2: day(15)}

    def human_call_moments(self, cursor, exts, day_from, day_to):
        self.windows['calls'] = (day_from, day_to)
        return {}


@unittest.skipIf(Flask is None, 'flask не установлен')
class RouteTests(unittest.TestCase):
    """Ручка исполняется целиком: свой Flask и двойники `queries` и `staffing_queries`,
    как в tests/test_cdr_lead_routes.py."""

    def client(self, double, staffing=None):
        patcher = mock.patch.object(routes, 'queries', double)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.staffing = staffing or _Staffing()
        staffing_patch = mock.patch.object(routes, 'staffing_queries', self.staffing)
        staffing_patch.start()
        self.addCleanup(staffing_patch.stop)
        schema_patch = mock.patch.object(routes.schema, 'schema_is_ready', return_value=True)
        schema_patch.start()
        self.addCleanup(schema_patch.stop)
        app = Flask(__name__)
        app.register_blueprint(routes.build_cdr_blueprint(
            db=_FakeDb(), require_api_key=lambda fn: fn,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (1, None, None),
            inbound_line_numbers=TEST_LINES))
        app.config['TESTING'] = True
        return app.test_client()

    def get(self, double, staffing=None, **params):
        params.setdefault('date_from', '2026-11-02')
        params.setdefault('date_to', '2026-11-03')
        return self.client(double, staffing).get('/api/cdr/reports/inbound', query_string=params)

    def test_cr_and_occ_come_from_schedules_and_phone_statuses(self):
        response = self.get(_Queries())
        self.assertEqual(response.status_code, 200)
        book = load_workbook(BytesIO(response.data))
        sheet = book['02.11']
        # Из двух смен «Основы» отработана одна: второй ушёл за три часа до конца.
        self.assertAlmostEqual(cell_of(sheet, 'cr', 3).value, 0.5)
        self.assertEqual(cell_of(sheet, 'cr', 3).comment.text, 'Смен по графику: 2, отработано: 1')
        # На линии 8 ч и 5 ч, в разговоре по часу у каждого: 2 ÷ 13.
        self.assertAlmostEqual(cell_of(sheet, 'occupancy', 3).value, 2 / 13)
        # 03.11 смен в графике нет и на линии никого не было — пусто, а не ноль.
        self.assertIsNone(cell_of(book['03.11'], 'cr', 3).value)
        self.assertIsNone(cell_of(book['03.11'], 'occupancy', 3).value)
        # Состав — группа «Основа»; события телефонов — с запасом на сутки до и после.
        self.assertEqual(self.staffing.windows['roster'],
                         ('op_osnova', date(2026, 11, 2), date(2026, 11, 3)))
        self.assertEqual(self.staffing.windows['events'],
                         (datetime(2026, 11, 1), datetime(2026, 11, 5)))
        self.assertEqual(self.staffing.windows['calls'], (date(2026, 11, 1), date(2026, 11, 4)))
        notes = '\n'.join(str(row[0] or '') for row in book[R.NOTES_SHEET].iter_rows(values_only=True))
        self.assertNotIn('не посчитаны', notes)

    def test_shifts_sheet_names_whose_shift_counted(self):
        book = load_workbook(BytesIO(self.get(_Queries()).data))
        rows = list(book[R.SHIFTS_SHEET].iter_rows(values_only=True))
        self.assertEqual(rows[0], ('Дата', 'Оператор', 'Смена', 'План на линии, ч', 'На линии, ч',
                                   'Доля', 'Засчитана'))
        self.assertEqual(rows[1], (datetime(2026, 11, 2), 'Иванова Айгерим', '09:00–18:00',
                                   8.0, 8.0, 1.0, 'да'))
        self.assertEqual(rows[2], (datetime(2026, 11, 2), 'Карабек Ержан', '09:00–18:00',
                                   8.0, 5.0, 0.625, 'нет'))
        self.assertEqual(len(rows), 3)

    def test_group_without_people_leaves_the_cells_blank(self):
        response = self.get(_Queries(), _Staffing(operator_ids=()))
        book = load_workbook(BytesIO(response.data))
        self.assertNotIn(R.SHIFTS_SHEET, book.sheetnames)
        self.assertIsNone(cell_of(book['02.11'], 'cr', 3).value)
        notes = '\n'.join(str(row[0] or '') for row in book[R.NOTES_SHEET].iter_rows(values_only=True))
        self.assertNotIn('не посчитаны', notes)

    def test_broken_staffing_does_not_take_the_calls_report_down(self):
        response = self.get(_Queries(), _Staffing(broken=True))
        self.assertEqual(response.status_code, 200)
        book = load_workbook(BytesIO(response.data))
        sheet = book['02.11']
        jana = queue_row(sheet, '3034')
        self.assertEqual(sheet['F%d' % jana].value, 3)
        self.assertIsNone(cell_of(sheet, 'cr', 3).value)
        notes = '\n'.join(str(row[0] or '') for row in book[R.NOTES_SHEET].iter_rows(values_only=True))
        self.assertIn('ОСС и CR% не посчитаны', notes)

    def test_workbook_for_the_period_with_the_plan_week(self):
        double = _Queries()
        response = self.get(double)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, routes.XLSX_MIME)
        self.assertIn("filename*=UTF-8''", response.headers['Content-Disposition'])
        # План — факт недельной давности: входящие читаются с 26.10, а не с 02.11.
        self.assertEqual(double.asked, (date(2026, 10, 26), date(2026, 11, 3)))
        book = load_workbook(BytesIO(response.data))
        self.assertEqual(book.sheetnames, ['02.11', '03.11', R.SHIFTS_SHEET, R.NOTES_SHEET])
        sheet = book['02.11']
        jana = queue_row(sheet, '3034')
        self.assertEqual((sheet['E%d' % jana].value, sheet['F%d' % jana].value), (4, 3))
        # Номера линий доходят до книги из того, что передано фабрике раздела.
        self.assertEqual(sheet['B3'].value, int(TEST_LINES['3001'][0]))
        # Все сутки на месте — мосту ставить нечего и оговорки в файле нет.
        self.assertEqual(double.enqueued, [])
        notes = '\n'.join(str(row[0] or '') for row in book[R.NOTES_SHEET].iter_rows(values_only=True))
        self.assertIn('Собрано', notes)
        self.assertNotIn('Нет данных станции', notes)

    def test_missing_day_is_requested_from_the_bridge_and_named_in_the_file(self):
        double = _Queries(absent=[date(2026, 10, 27)])
        response = self.get(double)
        self.assertEqual(response.status_code, 200)
        # Суток недельной давности нет: они ушли мосту в очередь, а файл говорит, чего нет.
        self.assertEqual(double.enqueued, [date(2026, 10, 27)])
        notes = load_workbook(BytesIO(response.data))[R.NOTES_SHEET]
        text = '\n'.join(str(row[1] or '') for row in notes.iter_rows(values_only=True))
        self.assertIn('За 27.10.2026.', text)

    def test_section_filters_do_not_reach_the_report(self):
        double = _Queries()
        self.assertEqual(self.get(double, ext='6474', call_type='Исходящий', park='Нет такого')
                         .status_code, 200)
        self.assertEqual(double.asked, (date(2026, 10, 26), date(2026, 11, 3)))

    def test_closed_for_those_without_the_section(self):
        response = self.get(_Queries(ctx=dict(ADMIN, role='operator', department_code='op')))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'CDR_SECTION_CLOSED')

    def test_period_limit_is_the_section_one(self):
        response = self.get(_Queries(), date_from='2026-06-01', date_to='2026-11-03')
        self.assertEqual(response.status_code, 400)
        self.assertIn('слишком много', response.get_json()['error'])


if __name__ == '__main__':
    unittest.main()
