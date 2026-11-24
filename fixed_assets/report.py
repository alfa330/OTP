# -*- coding: utf-8 -*-
"""Выгрузка раздела «Основные средства» в xlsx: лист «Реестр ОС» и лист «История».

Чистая сборка книги: на вход — строки из queries, на выход — байты. Инвентарные и серийные
номера лежат текстом (числом «000457» потеряло бы нули), поэтому у этих колонок гасится
зелёный уголок «Число сохранено как текст» — тем же помощником, что у остальных выгрузок.
"""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


_HEAD_FILL = PatternFill('solid', fgColor='F1F5F9')
_HEAD_FONT = Font(bold=True, color='334155')


def file_name(today):
    return 'Основные средства %s.xlsx' % today.strftime('%d.%m.%Y')


COST_TITLE = 'Стоимость, ₸'


def asset_rows(assets, *, with_cost):
    """[(заголовок, ширина)] и строки листа «Реестр ОС»."""
    head = [('Инвентарный №', 18), ('Наименование', 42), ('Категория', 24), ('Серийный №', 22), ('Штрихкод', 18),
            ('Статус', 14), ('МОЛ', 32), ('Отдел', 26), ('Место', 30), ('На складе', 11),
            ('Принято к учёту', 16)]
    if with_cost:
        head.append((COST_TITLE, 16))
    head.append(('Открытый запрос', 26))
    rows = []
    for asset in assets:
        open_request = asset.get('open_request') or {}
        row = [asset.get('inventory_number'), asset.get('name'), asset.get('category_name'),
               asset.get('serial_number'), asset.get('barcode'), asset.get('status_label'), asset.get('mol_name'),
               asset.get('department_name'), asset.get('location'), 'да' if asset.get('on_stock') else '',
               asset.get('acquired_on')]
        if with_cost:
            row.append(float(asset['cost']) if asset.get('cost') is not None else None)
        row.append('№ %s · %s' % (open_request['id'], open_request.get('kind_label')) if open_request.get('id') else '')
        rows.append(row)
    return head, rows


def event_text(event):
    """Что произошло — одной строкой, как в ленте истории."""
    payload = event.get('payload') or {}
    parts = []
    if event['kind'] == 'moved':
        parts.append('%s → %s' % ((payload.get('from') or {}).get('mol_name') or '—',
                                  (payload.get('to') or {}).get('mol_name') or '—'))
    if event['kind'] == 'barcode_set':
        # Как в ленте истории: какой штрихкод был и какой стал (снятый — только прежний).
        before, after = payload.get('from'), payload.get('to')
        parts.append('%s → %s' % (before, after) if before and after else (after or before or ''))
    if event['kind'] == 'corrected':
        parts += ['%s: %s → %s' % (change.get('label'), change.get('from') if change.get('from') is not None else '—',
                                   change.get('to') if change.get('to') is not None else '—')
                  for change in payload.get('changes') or []]
    for key in ('reason', 'comment', 'note'):
        if payload.get(key):
            parts.append(str(payload[key]))
    if payload.get('handed_by_name'):
        parts.append('оригинал передал: %s' % payload['handed_by_name'])
    names = [item.get('name') for item in payload.get('files') or [] if item.get('name')]
    if names:
        parts.append('файлы: ' + ', '.join(names))
    return '; '.join(parts)


def event_rows(events):
    head = [('Дата и время', 19), ('Событие', 36), ('ОС', 40), ('Инвентарный №', 18), ('Запрос', 28),
            ('Кто', 30), ('Подробности', 70)]
    rows = []
    for event in events:
        payload = event.get('payload') or {}
        request_text = ''
        if event.get('request_id'):
            request_text = '№ %s%s' % (event['request_id'],
                                       ' · %s' % event['request_kind_label'] if event.get('request_kind_label') else '')
        rows.append([event.get('created_at'), event.get('label'),
                     event.get('asset_name') or event.get('items_title') or payload.get('user_name') or '',
                     event.get('inventory_number') or '', request_text, event.get('actor_name') or 'Система',
                     event_text(event)])
    return head, rows


def _sheet(sheet, head, rows, *, date_format=None, money_column=None):
    for index, (title, width) in enumerate(head, start=1):
        cell = sheet.cell(row=1, column=index, value=title)
        cell.font, cell.fill = _HEAD_FONT, _HEAD_FILL
        cell.alignment = Alignment(vertical='center', wrap_text=True)
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row_index, row in enumerate(rows, start=2):
        for column, value in enumerate(row, start=1):
            cell = sheet.cell(row=row_index, column=column, value=value)
            cell.alignment = Alignment(vertical='top', wrap_text=True)
            if date_format and hasattr(value, 'strftime'):
                cell.number_format = date_format
            if money_column and column == money_column and value is not None:
                cell.number_format = '#,##0.00'
    sheet.freeze_panes = 'A2'
    if rows:
        sheet.auto_filter.ref = 'A1:%s%d' % (get_column_letter(len(head)), len(rows) + 1)


def build(assets, events, *, with_cost=False, excel_text_warning=None):
    book = Workbook()
    registry = book.active
    registry.title = 'Реестр ОС'
    head, rows = asset_rows(assets, with_cost=with_cost)
    # Столбец стоимости — по заголовку, а не номером: столбцы реестра добавляются (штрихкод сдвинул его).
    money_column = next((index for index, (title, _width) in enumerate(head, 1) if title == COST_TITLE), None)
    _sheet(registry, head, rows, date_format='DD.MM.YYYY', money_column=money_column if with_cost else None)
    history = book.create_sheet('История')
    head, events_list = event_rows(events)
    _sheet(history, head, events_list, date_format='DD.MM.YYYY HH:MM')
    stream = BytesIO()
    book.save(stream)
    stream.seek(0)
    if excel_text_warning and rows:
        # Инвентарный (A), серийный (D) номера и штрихкод (E) — текстом: гасим «Число сохранено как текст».
        stream = excel_text_warning(stream, 'A2:A%d D2:E%d' % (len(rows) + 1, len(rows) + 1))
        stream.seek(0)
    return stream.getvalue()
