# -*- coding: utf-8 -*-
"""Выгрузки «Кадров» в Excel один в один с файлами HR (просьба владельца 12.11.2026: «выгрузки
сделать 1 в 1 как в предоставленных файлах»):

  * табель — как лист « 2026» «Приложения 2 - Табель»: шапка в две строки (числа и дни недели над
    днями, подписи столбцов — во второй), на человека две строки (дни и минуты под ними), №, должность,
    ФИО, ТД/ГПХ, график, план дней и часов и итоги объединены на обе строки, отдел — подписью
    поперёк своих строк в столбце A, «Прием дд/мм/гггг» — поверх дней до приема;
  * список по компаниям — как лист « 2026» «Приложения 4»: отдел (проект), №, должность, ФИО, ТД/ГПХ.

Оформление не задаётся здесь цветами и шрифтами: каждая ячейка берёт стиль у ячейки-образца шаблона
(templates/tabel.xlsx, templates/companies.xlsx — листы HR без людей), книга — тему HR. Поэтому
Times New Roman 16, сиреневая шапка, голубые выходные и зелёный/жёлтый отпуск — ровно те же, что у HR.
Сами строки-образцы в файл не попадают.
"""

from copy import copy
from datetime import date
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import PatternFill
from openpyxl.utils import get_column_letter

from . import rules

TEMPLATES_DIR = Path(__file__).resolve().parent / 'templates'
TABEL_TEMPLATE = TEMPLATES_DIR / 'tabel.xlsx'
COMPANIES_TEMPLATE = TEMPLATES_DIR / 'companies.xlsx'

# Табель HR: A — отдел, B № , C должность, D ФИО, E ТД/ГПХ, F график, G план дней, H план часов,
# I… — дни месяца, за ними пять итогов. В образце 31 день (I…AM) и итоги AN…AR.
FIRST_DAY_COL = 9
PROTO_FIRST_TOTAL_COL = 40
TOTALS = 5
PERSON_TOP_HEIGHT = 20.25
PERSON_BOTTOM_HEIGHT = 21.0
# «Б» в образце HR не встретилась: берём оформление «О» и розовую заливку — как в печати раздела.
SICK_FILL = 'FFF4B6B6'
# Ячейки-образцы шаблона табеля.
PROTO_DAY_VALUE = {'number': 'I3', rules.CODE_DAY_OFF: 'K3', rules.CODE_UNPAID: 'Q3', rules.CODE_LEAVE: 'I5'}
PROTO_MINUTES = 'I4'
PROTO_COMMENT = 'AR5'


def _column_widths(sheet):
    """{номер столбца: ширина} — openpyxl хранит ширину диапазоном столбцов (min…max)."""
    out = {}
    for dim in sheet.column_dimensions.values():
        if dim.width is None or not dim.customWidth:
            continue
        low, high = dim.min or 0, dim.max or 0
        if not low:
            continue
        for index in range(low, high + 1):
            out[index] = dim.width
    return out


class _Painter:
    """Пишет ячейку нового листа с оформлением ячейки-образца той же книги."""

    def __init__(self, proto, sheet):
        self.proto = proto
        self.sheet = sheet

    def put(self, row, column, value, source):
        cell = self.sheet.cell(row=row, column=column)
        cell._style = copy(self.proto[source]._style)
        if value is not None:
            cell.value = value
        return cell


def _sheet_view(target, proto, freeze):
    target.sheet_format = copy(proto.sheet_format)
    target.sheet_view.zoomScale = proto.sheet_view.zoomScale
    target.sheet_view.showGridLines = proto.sheet_view.showGridLines
    target.freeze_panes = freeze
    target.page_margins = copy(proto.page_margins)
    target.page_setup.paperSize = target.PAPERSIZE_A4


def plain_company_label(label):
    """«ГПХ ИП «Алиева»» → «ГПХ ИП Алиева»: в таблицах HR компании пишутся без кавычек-ёлочек."""
    return ' '.join(str(label or '').replace('«', ' ').replace('»', ' ').split())


def _number(value):
    """«8», «7,5» → число: в табеле HR часы — числа, иначе не сойдутся COUNT и SUM итогов."""
    parsed = rules.number_or_none(value)
    if parsed is None:
        return None
    return int(parsed) if float(parsed).is_integer() else parsed


def _out_runs(cells):
    """Подряд идущие дни до приема / после увольнения без значения — [(начало, конец, вид)].
    Ячейку, которую HR заполнил руками, в такую полосу не включаем."""
    runs = []
    start, current = None, None
    for index, cell in enumerate(list(cells) + [{}]):
        kind = cell.get('out') if cell.get('out') and not cell.get('v') and not cell.get('edited') else None
        if start is not None and kind != current:
            runs.append((start, index - 1, current))
            start, current = None, None
        if kind and start is None:
            start, current = index, kind
    return runs


def _out_text(kind, person):
    when = person.get('hired_on') if kind == 'hire' else person.get('dismissed_on')
    try:
        day = date.fromisoformat(str(when)[:10]) if when else None
    except ValueError:
        day = None
    stamp = day.strftime('%d/%m/%Y') if day else ''
    return (f'Прием {stamp}' if kind == 'hire' else f'Увольнение {stamp}').strip()


def _comment(person):
    """Комментарий — как у HR: своя запись кадровика, иначе «Переработка N мин». «Прием» и
    «Увольнение» стоят в днях месяца, во втором месте их не повторяем."""
    if person.get('note'):
        return person['note']
    if person.get('overwork'):
        return f"Переработка {int(person['overwork'])} мин"
    return None


def timesheet_workbook(model):
    """Табель месяца → xlsx по образцу HR (лист « 2026» «Приложения 2»)."""
    workbook = load_workbook(TABEL_TEMPLATE)
    proto = workbook['Табель']
    sheet = workbook.create_sheet((model.get('title') or 'Табель')[:31])
    paint = _Painter(proto, sheet)
    days = model['days']
    last_day = FIRST_DAY_COL + len(days) - 1
    first_total = last_day + 1
    widths = _column_widths(proto)

    # Ширины: A…H как у HR, дни — по своему месту в образце, итоги — по месту итогов образца.
    for column in range(1, FIRST_DAY_COL):
        if column in widths:
            sheet.column_dimensions[get_column_letter(column)].width = widths[column]
    for offset in range(len(days)):
        source = FIRST_DAY_COL + offset
        if source in widths:
            sheet.column_dimensions[get_column_letter(FIRST_DAY_COL + offset)].width = widths[source]
    for offset in range(TOTALS):
        source = PROTO_FIRST_TOTAL_COL + offset
        if source in widths:
            sheet.column_dimensions[get_column_letter(first_total + offset)].width = widths[source]

    # Шапка: строка 1 — «Отдел», числа месяца; строка 2 — подписи столбцов и дни недели.
    for row in (1, 2):
        for column in range(1, FIRST_DAY_COL):
            letter = get_column_letter(column)
            paint.put(row, column, proto[f'{letter}{row}'].value if not (row == 2 and column == 1) else None,
                      f'{letter}{row}')
        for offset, day in enumerate(days):
            off = day.get('weekend') or day.get('holiday')
            source = f"{'K' if off else 'I'}{row}"
            paint.put(row, FIRST_DAY_COL + offset, day['day'] if row == 1 else day['weekday'], source)
        for offset in range(TOTALS):
            source = f'{get_column_letter(PROTO_FIRST_TOTAL_COL + offset)}{row}'
            paint.put(row, first_total + offset, proto[source].value, source)
    sheet.merge_cells('A1:A2')
    for row in (1, 2):
        if proto.row_dimensions[row].height:
            sheet.row_dimensions[row].height = proto.row_dimensions[row].height

    first_letter = get_column_letter(FIRST_DAY_COL)
    last_letter = get_column_letter(last_day)
    sick_fill = PatternFill('solid', fgColor=SICK_FILL)
    row = 3
    number = 0
    for group in model.get('groups') or []:
        people = group.get('people') or []
        if not people:
            continue
        group_first = row
        for person in people:
            number += 1
            top, bottom = row, row + 1
            plan_days = person.get('plan_days')
            plan_hours = person.get('plan_hours')
            hours_value = (f'=G{top}*8' if plan_days and plan_hours is not None
                           and float(plan_hours) == float(plan_days) * 8 else _number(plan_hours))
            values = {2: number, 3: person.get('job_title') or None, 4: person.get('name'),
                      5: plain_company_label(person.get('contract_label')) or None,
                      6: person.get('schedule') or None, 7: plan_days, 8: hours_value}
            # Первая пара строк листа — как строки 3–4 образца (без верхней границы под шапкой),
            # дальше — как 5–6 (с тонкой верхней): так у HR.
            proto_top = 3 if number == 1 else 5
            for column in range(2, FIRST_DAY_COL):
                letter = get_column_letter(column)
                paint.put(top, column, values.get(column), f'{letter}{proto_top}')
                paint.put(bottom, column, None, f'{letter}{proto_top + 1}')
                sheet.merge_cells(start_row=top, start_column=column, end_row=bottom, end_column=column)
            cells = person.get('cells') or []
            for offset, cell in enumerate(cells):
                column = FIRST_DAY_COL + offset
                raw = cell.get('v')
                numeric = _number(raw) if raw not in (None, '') else None
                if numeric is not None:
                    value, source = numeric, PROTO_DAY_VALUE['number']
                else:
                    value = raw or None
                    source = PROTO_DAY_VALUE.get(raw, PROTO_DAY_VALUE['number'])
                target = paint.put(top, column, value, source)
                if raw == rules.CODE_SICK:
                    paint.put(top, column, value, PROTO_DAY_VALUE[rules.CODE_LEAVE])
                    target.fill = sick_fill
                minutes = cell.get('m')
                if isinstance(minutes, str) and minutes.lstrip('+-').isdigit():
                    minutes = int(minutes)
                paint.put(bottom, column, minutes if minutes not in (None, '') else None, PROTO_MINUTES)
            for start, end, kind in _out_runs(cells):
                first = FIRST_DAY_COL + start
                sheet.cell(row=top, column=first).value = _out_text(kind, person)
                if end > start:
                    sheet.merge_cells(start_row=top, start_column=first, end_row=top, end_column=FIRST_DAY_COL + end)
            span = f'{first_letter}{top}:{last_letter}{top}'
            formulas = [f'=COUNT({span})', f'=SUM({span})',
                        f'=(COUNTIFS({span},"{rules.CODE_LEAVE}")+(COUNTIFS({span},"{rules.CODE_UNPAID}")'
                        f'+(COUNTIFS({span},"{rules.CODE_SICK}"))))',
                        f'=SUM({first_letter}{bottom}:{last_letter}{bottom})']
            for offset in range(TOTALS):
                column = first_total + offset
                letter = get_column_letter(PROTO_FIRST_TOTAL_COL + offset)
                if offset < len(formulas):
                    paint.put(top, column, formulas[offset], f'{letter}{proto_top}')
                else:
                    comment = _comment(person)
                    # У HR комментарий с текстом — Times New Roman 20, пустая ячейка — как строка образца.
                    paint.put(top, column, comment, PROTO_COMMENT if comment else f'{letter}{proto_top}')
                paint.put(bottom, column, None, f'{letter}{proto_top + 1}')
                sheet.merge_cells(start_row=top, start_column=column, end_row=bottom, end_column=column)
            sheet.row_dimensions[top].height = PERSON_TOP_HEIGHT
            sheet.row_dimensions[bottom].height = PERSON_BOTTOM_HEIGHT
            row += 2
        paint.put(group_first, 1, group.get('name') or 'Без отдела', 'A3')
        for inner in range(group_first + 1, row):
            paint.put(inner, 1, None, 'A3')
        sheet.merge_cells(start_row=group_first, start_column=1, end_row=row - 1, end_column=1)

    _sheet_view(sheet, proto, 'I3')
    # Печать: шапка повторяется на каждой странице, по ширине — один лист (у HR печать не настроена).
    sheet.print_title_rows = '1:2'
    sheet.page_setup.orientation = 'landscape'
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    workbook.remove(proto)
    workbook.active = 0
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def companies_workbook(groups):
    """Список по компаниям → xlsx по образцу HR (лист « 2026» «Приложения 4»).
    groups: [(проект, [{'job_title', 'name', 'label'}])]; № внутри проекта — заново, как у HR."""
    workbook = load_workbook(COMPANIES_TEMPLATE)
    proto = workbook['Список']
    sheet = workbook.create_sheet('Список по компаниям')
    paint = _Painter(proto, sheet)
    widths = _column_widths(proto)
    for column in range(1, 6):
        if column in widths:
            sheet.column_dimensions[get_column_letter(column)].width = widths[column]
    for row in (1, 2):
        for column in range(1, 6):
            letter = get_column_letter(column)
            paint.put(row, column, proto[f'{letter}{row}'].value if not (row == 2 and column == 1) else None,
                      f'{letter}{row}')
        if proto.row_dimensions[row].height:
            sheet.row_dimensions[row].height = proto.row_dimensions[row].height
    sheet.merge_cells('A1:A2')
    row = 3
    for project, people in groups:
        if not people:
            continue
        group_first = row
        for number, person in enumerate(people, 1):
            top, bottom = row, row + 1
            values = {2: number, 3: person.get('job_title') or None, 4: person.get('name'),
                      5: plain_company_label(person.get('label')) or None}
            for column in range(2, 6):
                letter = get_column_letter(column)
                paint.put(top, column, values[column], f'{letter}3')
                paint.put(bottom, column, None, f'{letter}4')
                sheet.merge_cells(start_row=top, start_column=column, end_row=bottom, end_column=column)
            sheet.row_dimensions[top].height = PERSON_TOP_HEIGHT
            sheet.row_dimensions[bottom].height = PERSON_BOTTOM_HEIGHT
            row += 2
        paint.put(group_first, 1, project or 'Без проекта', 'A3')
        for inner in range(group_first + 1, row):
            paint.put(inner, 1, None, 'A3')
        sheet.merge_cells(start_row=group_first, start_column=1, end_row=row - 1, end_column=1)
    _sheet_view(sheet, proto, 'A3')
    sheet.print_title_rows = '1:2'
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    workbook.remove(proto)
    workbook.active = 0
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
