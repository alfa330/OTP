# -*- coding: utf-8 -*-
"""Файл Excel табеля — по образцу из ТЗ (лист «табель»).

Как в образце: Times New Roman, шапка в две строки (дни недели и числа), сб и вс
подсвечены, слева зелёные «Город — Должность — Ф.И.О — график — дни план», справа
фиолетовые итоги; город — одной объединённой ячейкой на всех своих сотрудников;
«р» красным, «в» на бирюзовом, «Б/С» на жёлтом, «О» на зелёном. Цвета сняты с
образца пипеткой — это стандартная палитра Excel.

Отличие от образца одно и по ТЗ: на сотрудника три строки, а не две — статус
дня, минуты опоздания и минуты раннего ухода (ТЗ п. 4 требует ранний уход по
дням и отдельным итогом). Итоги — формулами (СЧЁТЕСЛИ, СУММ) с заранее
посчитанным значением: Excel пересчитает их при правке, а превью на телефоне и в
Telegram, которое формулы не считает, всё равно покажет числа.
"""

from io import BytesIO

import xlsxwriter
from xlsxwriter.utility import xl_range

from . import rules

FONT = 'Times New Roman'
SIZE = 11

HEAD_GREEN = '#C6E0B4'
WEEKEND_HEAD = '#FFF2CC'
TOTAL_HEAD = '#B3A2C7'
BODY_GREEN = '#D7E4BD'
DAY_OFF_FILL = '#B7E1CD'
BS_FILL = '#FFFF00'
VACATION_FILL = '#92D050'
SICK_FILL = '#BDD7EE'
ABSENT_FILL = '#FFC7CE'
ABSENT_FONT = '#9C0006'
INCOMPLETE_FILL = '#FFEB9C'
WORK_FONT = '#FF0000'
LEGEND_FONT = '#595959'

ROWS_PER_PERSON = 3
HEADER_ROWS = 2
LEFT_HEADERS = ('Город', 'Должность', 'Ф.И.О должностного лица', 'график', 'кол-во рабочих дней план')
TOTAL_HEADERS = ('кол-во отработанных дней', 'кол-во пропущенных дней',
                 'итого минут опозданий', 'итого минут раннего ухода')
LEFT_WIDTHS = (12.5, 10.7, 24, 7.5, 9)
DAY_WIDTH = 3.7
TOTAL_WIDTH = 11
SHEET_NAME = 'табель'

CODE_STYLE = {
    rules.CODE_WORK: {'font_color': WORK_FONT},
    rules.CODE_DAY_OFF: {'bg_color': DAY_OFF_FILL},
    rules.CODE_BS: {'bg_color': BS_FILL},
    rules.CODE_VACATION: {'bg_color': VACATION_FILL},
    rules.CODE_SICK: {'bg_color': SICK_FILL},
    rules.CODE_ABSENT: {'bg_color': ABSENT_FILL, 'font_color': ABSENT_FONT},
}

LEGEND = ('Обозначения: р — рабочий день по отметкам (на жёлтом — нет отметки прихода или ухода); '
          'в — выходной; Б/С — без содержания; О — отпуск; Б — больничный; н — смена без отметок. '
          'Строки сотрудника: день, опоздание (мин), ранний уход (мин).')


class _Formats:
    """Кэш форматов: в xlsxwriter формат — объект книги, плодить копии незачем."""

    def __init__(self, workbook):
        self.workbook = workbook
        self.cache = {}

    def get(self, **props):
        base = {'font_name': FONT, 'font_size': SIZE, 'border': 1, 'valign': 'vcenter'}
        base.update({key: value for key, value in props.items() if value is not None})
        key = tuple(sorted(base.items()))
        if key not in self.cache:
            self.cache[key] = self.workbook.add_format(base)
        return self.cache[key]


def _is_marked_day(day, holidays):
    return day.weekday() >= 5 or day in holidays


def build(model):
    """Модель листа (rules.build_model) → байты .xlsx."""
    out = BytesIO()
    workbook = xlsxwriter.Workbook(out, {'in_memory': True, 'strings_to_formulas': False})
    sheet = workbook.add_worksheet(SHEET_NAME)
    fmt = _Formats(workbook)

    days = model['days']
    holidays = set(model.get('holidays') or ())
    first_day_col = len(LEFT_HEADERS)
    last_day_col = first_day_col + len(days) - 1
    first_total_col = last_day_col + 1

    for col, width in enumerate(LEFT_WIDTHS):
        sheet.set_column(col, col, width)
    sheet.set_column(first_day_col, last_day_col, DAY_WIDTH)
    sheet.set_column(first_total_col, first_total_col + len(TOTAL_HEADERS) - 1, TOTAL_WIDTH)
    sheet.set_row(0, 20)
    sheet.set_row(1, 34)

    # ── Шапка ──────────────────────────────────────────────────────────────
    for col, title in enumerate(LEFT_HEADERS):
        if col == 0:
            style = fmt.get(bg_color=HEAD_GREEN, align='center', valign='vcenter')
        else:
            style = fmt.get(bg_color=HEAD_GREEN, align='left', valign='bottom', text_wrap=True)
        sheet.merge_range(0, col, 1, col, title, style)
    for index, day in enumerate(days):
        col = first_day_col + index
        fill = WEEKEND_HEAD if _is_marked_day(day, holidays) else None
        sheet.write_string(0, col, rules.WEEKDAY_SHORT[day.weekday()], fmt.get(bg_color=fill, align='center'))
        sheet.write_number(1, col, day.day, fmt.get(bg_color=fill, align='center'))
    for offset, title in enumerate(TOTAL_HEADERS):
        sheet.merge_range(0, first_total_col + offset, 1, first_total_col + offset, title,
                          fmt.get(bg_color=TOTAL_HEAD, align='left', valign='bottom', text_wrap=True))

    # ── Сотрудники по городам ──────────────────────────────────────────────
    row = HEADER_ROWS
    for city in model['cities']:
        people = city['people']
        city_first = row
        city_last = row + len(people) * ROWS_PER_PERSON - 1
        for position, person in enumerate(people):
            last_in_city = position == len(people) - 1
            _write_person(sheet, fmt, person, row, days, first_day_col, first_total_col, last_in_city)
            row += ROWS_PER_PERSON
        city_style = fmt.get(align='center', valign='vcenter', text_wrap=True, bottom=2)
        if city_last > city_first:
            sheet.merge_range(city_first, 0, city_last, 0, city['city'], city_style)
        else:
            sheet.write_string(city_first, 0, city['city'], city_style)

    # ── Обозначения ────────────────────────────────────────────────────────
    legend_row = row + 1
    sheet.write_string(legend_row, 0, LEGEND, workbook.add_format(
        {'font_name': FONT, 'font_size': 10, 'font_color': LEGEND_FONT}))

    sheet.freeze_panes(HEADER_ROWS, first_day_col)
    sheet.set_landscape()
    sheet.set_paper(9)
    sheet.fit_to_pages(1, 0)
    sheet.repeat_rows(0, HEADER_ROWS - 1)
    sheet.set_margins(left=0.3, right=0.3, top=0.4, bottom=0.4)
    workbook.close()
    return out.getvalue()


def _write_person(sheet, fmt, person, row, days, first_day_col, first_total_col, last_in_city):
    status_row, late_row, early_row = row, row + 1, row + 2
    bottom = 2 if last_in_city else None

    sheet.merge_range(row, 1, early_row, 1, person['job_title'],
                      fmt.get(align='left', valign='vcenter', bottom=bottom))
    sheet.merge_range(row, 2, early_row, 2, person['name'],
                      fmt.get(align='left', valign='vcenter', text_wrap=True, bottom=bottom))
    sheet.merge_range(row, 3, early_row, 3, person['schedule'] or '',
                      fmt.get(bg_color=BODY_GREEN, align='right', valign='vcenter', bottom=bottom))
    sheet.merge_range(row, 4, early_row, 4, '',
                      fmt.get(bg_color=BODY_GREEN, align='right', valign='vcenter', bottom=bottom))
    sheet.write_number(row, 4, int(person['plan'] or 0),
                       fmt.get(bg_color=BODY_GREEN, align='right', valign='vcenter', bottom=bottom))

    for index, cell in enumerate(person['cells']):
        col = first_day_col + index
        style = dict(CODE_STYLE.get(cell['code'], {}))
        if cell['code'] == rules.CODE_WORK and cell.get('incomplete'):
            style['bg_color'] = INCOMPLETE_FILL
        if cell['code']:
            sheet.write_string(status_row, col, cell['code'], fmt.get(align='left', **style))
        else:
            sheet.write_blank(status_row, col, None, fmt.get(align='left'))
        _write_minutes(sheet, fmt, late_row, col, cell.get('late'))
        _write_minutes(sheet, fmt, early_row, col, cell.get('early'), bottom=bottom)

    last_day_col = first_day_col + len(days) - 1
    status_range = xl_range(status_row, first_day_col, status_row, last_day_col)
    late_range = xl_range(late_row, first_day_col, late_row, last_day_col)
    early_range = xl_range(early_row, first_day_col, early_row, last_day_col)
    totals = person['totals']
    formulas = (
        (f'=COUNTIF({status_range},"{rules.CODE_WORK}")', totals['worked']),
        (f'=COUNTIF({status_range},"{rules.CODE_BS}")+COUNTIF({status_range},"{rules.CODE_ABSENT}")',
         totals['missed']),
        (f'=SUM({late_range})', totals['late']),
        (f'=SUM({early_range})', totals['early']),
    )
    for offset, (formula, value) in enumerate(formulas):
        col = first_total_col + offset
        style = fmt.get(bg_color=BODY_GREEN, align='center', valign='vcenter', bottom=bottom)
        sheet.merge_range(row, col, early_row, col, '', style)
        sheet.write_formula(row, col, formula, style, int(value or 0))


def _write_minutes(sheet, fmt, row, col, minutes, bottom=None):
    style = fmt.get(align='left', bottom=bottom)
    if minutes:
        sheet.write_number(row, col, int(minutes), style)
    else:
        sheet.write_blank(row, col, None, style)
