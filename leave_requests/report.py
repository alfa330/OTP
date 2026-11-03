# -*- coding: utf-8 -*-
"""Выгрузка журнала заявок на отпуск, Б/С и больничный в Excel.

Один лист — шапка и строки, без листа пояснений: журнал читают как таблицу
(просьба владельца 03.11.2026 — «без лишнего текста»). Даты — настоящими
датами, чтобы отбор и сортировка в Excel работали. Колонка «Отдел» есть, только
когда в выгрузке больше одного отдела, «Не в стаже» — когда такой Б/С в ней есть:
пустая колонка тоже шум.
"""

from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
HEADER_FILL = PatternFill('solid', fgColor='1F2937')
HEADER_FONT = Font(bold=True, color='FFFFFF')
HEADER_ALIGN = Alignment(horizontal='center', vertical='center', wrap_text=True)
CELL_ALIGN = Alignment(vertical='top')
WRAP_ALIGN = Alignment(vertical='top', wrap_text=True)
DATE_FORMAT = 'DD.MM.YYYY'
DATETIME_FORMAT = 'DD.MM.YYYY HH:MM'


def _date(value):
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date() if value else None
    except ValueError:
        return None


def _datetime(value):
    try:
        return datetime.strptime(str(value)[:16], '%Y-%m-%dT%H:%M') if value else None
    except ValueError:
        return None


def _name(person):
    return (person or {}).get('name')


def _supervisor_approved_by(item):
    for event in reversed(item.get('events') or []):
        if event.get('action') == 'supervisor_approved':
            return _name(event.get('actor'))
    return None


# (заголовок, ширина, значение, перенос строк)
_COLUMNS = (
    ('№', 7, lambda item: item['id'], False),
    ('Сотрудник', 30, lambda item: _name(item.get('operator')), False),
    ('Отдел', 18, lambda item: _name(item.get('department')), False),
    ('Группа', 18, lambda item: _name(item.get('group')), False),
    ('Вид', 18, lambda item: item.get('kindLabel'), False),
    ('С', 12, lambda item: _date(item.get('startDate')), False),
    ('По', 12, lambda item: _date(item.get('endDate')), False),
    ('Дней', 7, lambda item: item.get('days'), False),
    ('Статус', 24, lambda item: item.get('statusLabel'), False),
    ('Причина', 42, lambda item: item.get('reason'), True),
    ('Подана', 17, lambda item: _datetime(item.get('createdAt')), False),
    ('Согласование СВ', 26, _supervisor_approved_by, False),
    ('Кем решено', 26, lambda item: _name(item.get('decidedBy')), False),
    ('Когда решено', 17, lambda item: _datetime(item.get('decidedAt')), False),
    ('Комментарий', 36, lambda item: item.get('decisionComment'), True),
    ('Не в стаже', 11, lambda item: 'Да' if item.get('excludeFromTenure') else None, False),
)


def _columns(items):
    departments = {(item.get('department') or {}).get('id') for item in items}
    skip = set()
    if len(departments) <= 1:
        skip.add('Отдел')
    if not any(item.get('excludeFromTenure') for item in items):
        skip.add('Не в стаже')
    return [column for column in _COLUMNS if column[0] not in skip]


def journal_workbook(items):
    """Книга журнала: лист «Журнал», шапка закреплена, на ней фильтр."""
    columns = _columns(items)
    wb = Workbook()
    ws = wb.active
    ws.title = 'Журнал'
    for index, (header, width, _value, _wrap) in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=index, value=header)
        cell.font, cell.fill, cell.alignment = HEADER_FONT, HEADER_FILL, HEADER_ALIGN
        ws.column_dimensions[get_column_letter(index)].width = width
    for row, item in enumerate(items, start=2):
        for index, (_header, _width, value_of, wrap) in enumerate(columns, start=1):
            value = value_of(item)
            if isinstance(value, str):
                value = ILLEGAL_CHARACTERS_RE.sub('', value)
            cell = ws.cell(row=row, column=index, value=value)
            cell.alignment = WRAP_ALIGN if wrap else CELL_ALIGN
            if isinstance(value, datetime):
                cell.number_format = DATETIME_FORMAT
            elif isinstance(value, date):
                cell.number_format = DATE_FORMAT
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = 'A1:%s%d' % (get_column_letter(len(columns)), max(1, len(items) + 1))
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
