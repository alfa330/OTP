"""Выгрузка реестра «Почты» в xlsx (ТЗ п. 13: реестр, построчное содержимое, история).

Листы: «Контекст» (первым — когда и по каким фильтрам собрано: реестр живой, и через
неделю файл без этого листа не прочитать), «Отправления», «Содержимое», «История».

Соглашения портала: телефон, трек, номер отправления, индекс и номер документа лежат
ТЕКСТОМ (числом теряются ведущие нули), зелёный уголок «Число сохранено как текст»
гасит `<ignoredErrors>` — хелпер приходит аргументом из монолита. Даты — настоящими
датами с форматом, чтобы сортировались. Книга потоковая (`write_only`): ширины и
закрепление задаются ДО первой строки.

Числа срока считаются тем же правилом, что на экране (rules.deadline_state и
working_days_left): иначе в разделе было бы «просрочено 2 дня», а в файле 3.
"""

from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import rules

HEADER_FILL = PatternFill('solid', fgColor='1F2937')
HEADER_FONT = Font(bold=True, color='FFFFFF')
HEADER_ALIGN = Alignment(horizontal='center', vertical='center', wrap_text=True)
TITLE_FONT = Font(bold=True, size=13)
BOLD = Font(bold=True)
DAY_FORMAT = 'DD.MM.YYYY'
DATETIME_FORMAT = 'DD.MM.YYYY HH:MM'
NUMBER_FORMAT = '0.###'

SHEET_CONTEXT = 'Контекст'
SHEET_SHIPMENTS = 'Отправления'
SHEET_ITEMS = 'Содержимое'
SHEET_EVENTS = 'История'

# key, заголовок, ширина, вид (text — строкой с форматом @, date, datetime, number, int).
SHIPMENT_COLUMNS = (
    ('number', '№ отправления', 17, 'text'),
    ('status_label', 'Статус', 22, None),
    ('created_at', 'Создано', 16, 'datetime'),
    ('ready_at', 'Готово к отправке', 16, 'datetime'),
    ('handed_at', 'Передано курьеру', 16, 'datetime'),
    ('delivered_on', 'Доставлено', 12, 'date'),
    ('reconciled_at', 'Сверено', 16, 'datetime'),
    ('route', 'Направление', 26, None),
    ('sender_office_name', 'Офис отправки', 24, None),
    ('sender_name', 'Отправитель', 24, None),
    ('sender_phone', 'Телефон отправителя', 17, 'text'),
    ('recipient_office_name', 'Офис получения', 24, None),
    ('recipient_name', 'Получатель', 24, None),
    ('recipient_phone', 'Телефон получателя', 17, 'text'),
    ('recipient_address', 'Адрес получателя', 34, None),
    ('recipient_postcode', 'Индекс', 9, 'text'),
    ('items_summary', 'Содержимое', 36, None),
    ('qty_total', 'Заявлено', 10, 'int'),
    ('received_total', 'Получено', 10, 'int'),
    ('track_number', 'Трек-номер', 16, 'text'),
    ('zone', 'Зона', 16, None),
    ('sla', 'Срок, раб. дней', 13, None),
    ('due_on', 'Крайний срок', 12, 'date'),
    ('days_left', 'Дней до срока / просрочки', 14, 'int'),
    ('problem', 'Проблема', 30, None),
    ('delivery_type_label', 'Доставка', 11, None),
    ('urgency_label', 'Срочность', 11, None),
    ('places', 'Мест', 7, 'int'),
    ('weight_kg', 'Вес, кг', 9, 'number'),
    ('volume_weight_kg', 'Объёмный вес, кг', 11, 'number'),
    ('declared_value', 'Объявленная ценность', 13, 'number'),
    ('payer_name', 'Юрлицо-плательщик', 26, None),
    ('created_by_name', 'Кто создал', 24, None),
    ('comment', 'Комментарий', 34, None),
)
TEXT_SHIPMENT_KEYS = tuple(key for key, _t, _w, kind in SHIPMENT_COLUMNS if kind == 'text')

ITEM_COLUMNS = (
    ('number', '№ отправления', 17, 'text'),
    ('position', 'Строка', 7, 'int'),
    ('doc_type_label', 'Тип документа', 18, None),
    ('other_description', 'Что именно отправляется', 34, None),
    ('period_from', 'Период с', 12, 'date'),
    ('period_to', 'Период по', 12, 'date'),
    ('qty', 'Количество', 11, 'int'),
    ('copy_kind_label', 'Оригинал / копия', 12, None),
    ('doc_number', '№ документа', 14, 'text'),
    ('confidential', 'Конфиденциально', 12, None),
    ('received_qty', 'Получено', 10, 'int'),
    ('line_status_label', 'Сверка', 16, None),
    ('line_note', 'Комментарий сверки', 30, None),
)

EVENT_COLUMNS = (
    ('number', '№ отправления', 17, 'text'),
    ('at', 'Когда', 16, 'datetime'),
    ('actor_name', 'Кто', 24, None),
    ('event_label', 'Событие', 20, None),
    ('from_label', 'Было', 22, None),
    ('to_label', 'Стало', 22, None),
    ('details', 'Подробности', 40, None),
    ('note', 'Комментарий', 36, None),
)


def clean(value):
    """Управляющие символы роняют сохранение всей книги — вычищаем их пробелом."""
    if value is None:
        return ''
    return ILLEGAL_CHARACTERS_RE.sub(' ', str(value))


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    return None


def _as_datetime(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if value:
        try:
            return datetime.fromisoformat(str(value)[:19])
        except ValueError:
            return None
    return None


def _cell(sheet, value, kind):
    if kind == 'date':
        value = _as_date(value)
        cell = WriteOnlyCell(sheet, value=value)
        if value is not None:
            cell.number_format = DAY_FORMAT
        return cell
    if kind == 'datetime':
        value = _as_datetime(value)
        cell = WriteOnlyCell(sheet, value=value)
        if value is not None:
            cell.number_format = DATETIME_FORMAT
        return cell
    if kind in ('number', 'int'):
        if value is None or value == '':
            return WriteOnlyCell(sheet, value=None)
        number = float(value)
        cell = WriteOnlyCell(sheet, value=int(number) if kind == 'int' else number)
        if kind == 'number':
            cell.number_format = NUMBER_FORMAT
        return cell
    text = clean(value)
    # Тип строкой явно: «=…» в комментарии иначе стал бы формулой.
    cell = WriteOnlyCell(sheet, value=text)
    cell.data_type = 's'
    if kind == 'text' and text:
        cell.number_format = '@'
    return cell


def _setup(sheet, columns):
    sheet.freeze_panes = 'A2'
    for index, (_key, _title, width, _kind) in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    header = []
    for _key, title, _width, _kind in columns:
        cell = WriteOnlyCell(sheet, value=title)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGN
        header.append(cell)
    sheet.append(header)


def _append(sheet, columns, values):
    sheet.append([_cell(sheet, values.get(key), kind) for key, _t, _w, kind in columns])


def _problem_text(items):
    parts = []
    for item in items:
        status = item.get('line_status')
        if status and status != 'ok':
            label = rules.DOC_TYPE_LABELS.get(item.get('doc_type'), '')
            # «Иное» — его описанием (ТЗ п. 3): две строки «Иное» иначе не различить.
            if item.get('doc_type') == 'other' and item.get('other_description'):
                label = item['other_description']
            parts.append('%s: %s, пришло %s из %s' % (label, rules.LINE_STATUS_LABELS.get(status, status).lower(),
                                                     item.get('received_qty'), item.get('qty')))
    return '; '.join(parts)


def _event_details(event):
    payload = event.get('payload') or {}
    parts = []
    if payload.get('track_number'):
        parts.append('трек %s' % payload['track_number'])
    if payload.get('handed_at'):
        moment = _as_datetime(payload['handed_at'])
        parts.append('передано %s' % (moment.strftime('%d.%m.%Y %H:%M') if moment else payload['handed_at']))
    if payload.get('planned_pickup_on'):
        day = _as_date(payload['planned_pickup_on'])
        parts.append('забор %s' % (day.strftime('%d.%m.%Y') if day else payload['planned_pickup_on']))
    if payload.get('delivered_on'):
        day = _as_date(payload['delivered_on'])
        parts.append('вручено %s' % (day.strftime('%d.%m.%Y') if day else payload['delivered_on']))
    if payload.get('due_on'):
        day = _as_date(payload['due_on'])
        parts.append('срок до %s' % (day.strftime('%d.%m.%Y') if day else payload['due_on']))
    for name, (old, new) in (payload.get('changes') or {}).items():
        parts.append('%s: %s → %s' % (rules.FIELD_LABELS.get(name, name), _shown(old), _shown(new)))
    if payload.get('items_changed'):
        before, after = payload.get('items_before'), payload.get('items_after')
        if before is not None and after is not None:
            parts.append('содержимое: было «%s» → стало «%s»' % ('; '.join(before) or '—', '; '.join(after) or '—'))
        else:
            parts.append('изменено содержимое')
    if payload.get('file_kind'):
        parts.append(_file_text(payload.get('file_kind'), payload.get('filename')))
    for item in payload.get('files') or []:
        parts.append(_file_text(item.get('kind'), item.get('filename')))
    if payload.get('discrepancies'):
        parts.append('расхождений: %s' % payload['discrepancies'])
    return '; '.join(parts)


def _shown(value):
    if value in (None, ''):
        return '—'
    moment = _as_datetime(value) if isinstance(value, str) and len(value) > 10 and 'T' in value else None
    if moment:
        return moment.strftime('%d.%m.%Y %H:%M')
    day = _as_date(value) if isinstance(value, str) and len(value) == 10 and value[4:5] == '-' else None
    return day.strftime('%d.%m.%Y') if day else str(value)


def _file_text(kind, filename):
    label = rules.FILE_KIND_LABELS.get(kind, 'файл')
    # Имя убрано для того, кому вложения закрыты (queries.redact_events) — остаётся вид файла.
    return '%s «%s»' % (label, filename) if filename else label.lower()


def build_workbook(rows, items_by_shipment, events_by_shipment, *, zones, today, holidays, generated_at,
                   generated_by='', filters_note='', total=None, text_warning_patch=None):
    zone_labels = {zone['code']: zone['label'] for zone in zones or []}
    workbook = Workbook(write_only=True)
    context = workbook.create_sheet(SHEET_CONTEXT)
    shipments_sheet = workbook.create_sheet(SHEET_SHIPMENTS)
    items_sheet = workbook.create_sheet(SHEET_ITEMS)
    events_sheet = workbook.create_sheet(SHEET_EVENTS)

    # «Контекст»
    context.column_dimensions['A'].width = 34
    context.column_dimensions['B'].width = 60
    title = WriteOnlyCell(context, value='Почта — реестр отправлений')
    title.font = TITLE_FONT
    context.append([title])
    context.append([])
    shown = len(rows)
    total = shown if total is None else total
    lines = [
        ('Собрано', generated_at.strftime('%d.%m.%Y %H:%M') + ' (время Алматы)'),
        ('Кто выгрузил', generated_by or '—'),
        ('Отбор', filters_note or 'весь доступный реестр'),
        ('Отправлений в файле', str(shown)),
    ]
    if total > shown:
        lines.append(('Внимание', 'по отбору найдено %d, в файл вошли первые %d — сузьте отбор' % (total, shown)))
    lines.append(('Срок доставки', 'в рабочих днях от фактической передачи курьеру, по зоне маршрута; '
                                   'праздники РК не считаются (ТЗ п. 7)'))
    for label, value in lines:
        key = WriteOnlyCell(context, value=label)
        key.font = BOLD
        context.append([key, _cell(context, value, None)])
    context.append([])
    head = WriteOnlyCell(context, value='Статус')
    head.font = BOLD
    count_head = WriteOnlyCell(context, value='Отправлений')
    count_head.font = BOLD
    context.append([head, count_head])
    by_status = {}
    for row in rows:
        by_status[row['status']] = by_status.get(row['status'], 0) + 1
    for code in rules.STATUSES:
        if by_status.get(code):
            context.append([_cell(context, rules.STATUS_LABELS[code], None), by_status[code]])

    # «Отправления»
    _setup(shipments_sheet, SHIPMENT_COLUMNS)
    for row in rows:
        items = items_by_shipment.get(row['id'], [])
        state = rules.deadline_state(row['status'], row.get('due_on'), today, holidays)
        sla = ''
        if row.get('sla_max_days') is not None:
            low, high = row.get('sla_min_days'), row['sla_max_days']
            sla = str(high) if low in (None, high) else '%s–%s' % (low, high)
        values = dict(row)
        values.update({
            'status_label': rules.STATUS_LABELS.get(row['status'], row['status']),
            'route': '%s → %s' % (row.get('sender_city') or '—', row.get('recipient_city') or '—'),
            'zone': (zone_labels.get(row.get('zone_code')) or row.get('zone_code') or '')
                    + (' (вручную)' if row.get('zone_manual') else ''),
            'sla': sla,
            'days_left': state['days'] if state else None,
            'problem': _problem_text(items),
            'delivery_type_label': rules.DELIVERY_TYPE_LABELS.get(row.get('delivery_type'), ''),
            'urgency_label': rules.URGENCY_LABELS.get(row.get('urgency'), ''),
        })
        _append(shipments_sheet, SHIPMENT_COLUMNS, values)
    if rows:
        shipments_sheet.auto_filter.ref = 'A1:%s%d' % (get_column_letter(len(SHIPMENT_COLUMNS)), len(rows) + 1)

    # «Содержимое»
    _setup(items_sheet, ITEM_COLUMNS)
    item_rows = 0
    for row in rows:
        for item in items_by_shipment.get(row['id'], []):
            values = dict(item)
            values.update({
                'number': row['number'],
                'doc_type_label': rules.DOC_TYPE_LABELS.get(item.get('doc_type'), item.get('doc_type')),
                'copy_kind_label': rules.COPY_KIND_LABELS.get(item.get('copy_kind'), ''),
                'confidential': 'да' if item.get('is_confidential') else '',
                'line_status_label': rules.LINE_STATUS_LABELS.get(item.get('line_status'), ''),
            })
            _append(items_sheet, ITEM_COLUMNS, values)
            item_rows += 1

    # «История»
    _setup(events_sheet, EVENT_COLUMNS)
    for row in rows:
        for event in events_by_shipment.get(row['id'], []):
            _append(events_sheet, EVENT_COLUMNS, {
                'number': row['number'],
                'at': event.get('at'),
                'actor_name': event.get('actor_name'),
                'event_label': rules.EVENT_LABELS.get(event.get('kind'), event.get('kind')),
                'from_label': rules.STATUS_LABELS.get(event.get('from_status'), ''),
                'to_label': rules.STATUS_LABELS.get(event.get('to_status'), ''),
                'details': _event_details(event),
                'note': event.get('note'),
            })

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    if text_warning_patch and rows:
        stream = _patch_text(text_warning_patch, stream, SHIPMENT_COLUMNS, len(rows), 'xl/worksheets/sheet2.xml')
    if text_warning_patch and item_rows:
        # «Содержимое» — третий лист: номер документа («00123») тоже лежит текстом.
        stream = _patch_text(text_warning_patch, stream, ITEM_COLUMNS, item_rows, 'xl/worksheets/sheet3.xml')
    return stream


def _patch_text(patch, stream, columns, count, sheet_path):
    """Гасит «Число сохранено как текст» у текстовых колонок листа (тег <ignoredErrors>)."""
    letters = [get_column_letter(number) for number, (_key, _t, _w, kind) in enumerate(columns, start=1)
               if kind == 'text']
    sqref = ' '.join('{0}2:{0}{1}'.format(letter, count + 1) for letter in letters)
    try:
        return patch(stream, sqref, sheet_path=sheet_path)
    except Exception:  # noqa: BLE001 — значок в углу не повод не отдать файл
        stream.seek(0)
        return stream


def filename(today):
    return 'Почта_реестр_%s.xlsx' % today.strftime('%Y-%m-%d')
