"""Выгрузка реестра заявок в xlsx (дополнение Дмитриевой, п. 8: «все поля, включая скрытые»).

Колонки — поля заявки по ТЗ «Закуп и оплата» (п. 4, п. 5): тип заявки, этап,
компания, тип объекта, способ оплаты, варианты поставщиков, согласование,
получение, закрывающие документы.

Соглашения те же, что у выгрузок «Посылок» и «Касаний»:

* первым идёт лист «Контекст» — когда собрано, что отобрано, сколько строк;
* даты — датами с форматом, суммы — числами: иначе не отсортировать и не
  построить сводную;
* БИН, номер карты, номер счёта и договора — ТЕКСТОМ (число теряет ведущие нули
  и уезжает в экспоненту), а зелёный уголок «Число сохранено как текст» гасится
  тегом <ignoredErrors>; сама функция приходит аргументом, потому что живёт в
  монолите;
* подписи статусов, этапов и источников — те же слова, что на экране
  (paymentsMeta.js); второй словарь тех же кодов неизбежен, его сторожит тест.
"""

from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import workflow

ALMATY = ZoneInfo('Asia/Almaty')

HEADER_FILL = PatternFill('solid', fgColor='1F2937')
HEADER_FONT = Font(bold=True, color='FFFFFF')
HEADER_ALIGN = Alignment(horizontal='center', vertical='center', wrap_text=True)
TITLE_FONT = Font(bold=True, size=13)
WRAP_TOP = Alignment(wrap_text=True, vertical='top')

DAY_FORMAT = 'DD.MM.YYYY'
DATETIME_FORMAT = 'DD.MM.YYYY HH:MM'
MONEY_FORMAT = '#,##0.00'

STATE_LABELS = {
    'active': 'В работе',
    'clarification': 'Требуется уточнение',
    'overdue': 'Просрочена',
    'done': 'Закрыта',
    'rejected': 'Отклонена',
    'cancelled': 'Отменена',
}

# (заголовок, ключ, вид) — вид: 'text' | 'money' | 'date' | 'datetime' | 'plain'
COLUMNS = (
    ('№', 'id', 'plain'),
    ('Дата заявки', 'created_at', 'datetime'),
    ('Тип заявки', 'kind_label', 'plain'),
    ('Состояние', 'state_label', 'plain'),
    ('Этап', 'stage_text', 'plain'),
    ('Текущий исполнитель', 'current_assignee_name', 'plain'),
    ('Инициатор', 'initiator_name', 'plain'),
    ('Руководитель', 'manager_name', 'plain'),
    ('Подразделение', 'department_name', 'plain'),
    ('Компания-плательщик', 'legal_entity_name', 'plain'),
    ('Проект', 'project_name', 'plain'),
    ('Таксопарк / филиал / регион', 'branch', 'plain'),
    ('Наименование закупа', 'expense_name', 'plain'),
    ('Описание и обоснование', 'justification', 'plain'),
    ('Тип объекта', 'object_label', 'plain'),
    ('Категория учёта', 'accounting_label', 'plain'),
    ('Позиции', 'items_text', 'plain'),
    ('Сумма', 'amount', 'money'),
    ('Согласованная сумма', 'amount_approved', 'money'),
    ('Сумма возврата', 'refund_amount', 'money'),
    ('Дата возврата', 'refund_on', 'date'),
    ('Итоговая сумма', 'total_amount', 'money'),
    ('Категория закупа', 'category_name', 'plain'),
    ('Подкатегория', 'subcategory_name', 'plain'),
    ('Поставщик', 'counterparty_name', 'plain'),
    ('БИН поставщика', 'counterparty_bin', 'text'),
    ('Варианты поставщиков', 'offers_text', 'plain'),
    ('Альтернативы', 'alternatives_label', 'plain'),
    ('Обоснование выбора поставщика', 'supplier_choice_reason', 'plain'),
    ('Договор', 'contract_number', 'text'),
    ('Способ оплаты', 'method_label', 'plain'),
    ('Получатель по карте', 'card_label', 'plain'),
    ('Карта', 'card_mask', 'text'),
    ('Назначение платежа', 'payment_purpose', 'plain'),
    ('Период оплаты', 'payment_period', 'plain'),
    ('Срок', 'due_on', 'date'),
    ('Согласовал', 'approved_by_name', 'plain'),
    ('Дата согласования', 'approved_at', 'datetime'),
    ('Основание согласования', 'approver_label', 'plain'),
    ('Номер счёта', 'invoice_number', 'text'),
    ('Дата счёта', 'invoice_date', 'date'),
    ('Дата оплаты', 'paid_on', 'date'),
    ('Оплачено', 'paid_amount', 'money'),
    ('Дата получения', 'received_on', 'date'),
    ('Получено', 'received_quantity', 'plain'),
    ('Закрывающие документы', 'docs_label', 'plain'),
    ('Примечания', 'notes', 'plain'),
    ('Причина отклонения', 'rejected_reason', 'plain'),
    ('Закрыта', 'closed_at', 'datetime'),
)

WIDTHS = {
    '№': 7, 'Дата заявки': 16, 'Тип заявки': 20, 'Состояние': 20, 'Этап': 26, 'Наименование закупа': 34,
    'Описание и обоснование': 40, 'Позиции': 40, 'Сумма': 15, 'Итоговая сумма': 15, 'Поставщик': 26,
    'Варианты поставщиков': 46, 'Обоснование выбора поставщика': 36, 'Назначение платежа': 34,
    'Основание согласования': 34, 'Закрывающие документы': 22, 'Примечания': 30,
}


def _clean(value):
    if value is None:
        return None
    text = str(value)
    text = ILLEGAL_CHARACTERS_RE.sub('', text)
    return text


def _text_cell(ws, value):
    cell = WriteOnlyCell(ws, value=_clean(value) if value not in (None, '') else None)
    cell.number_format = '@'
    cell.alignment = WRAP_TOP
    return cell


def _plain_cell(ws, value):
    text = _clean(value) if value not in (None, '') else None
    # Текст, начинающийся с «=», Excel считает формулой — оставляем текстом.
    cell = WriteOnlyCell(ws, value=text)
    if text and text.startswith('='):
        cell.number_format = '@'
        cell.data_type = 's'
    cell.alignment = WRAP_TOP
    return cell


def _money_cell(ws, value):
    cell = WriteOnlyCell(ws, value=float(value) if value not in (None, '') else None)
    cell.number_format = MONEY_FORMAT
    return cell


def _date_cell(ws, value, fmt):
    parsed = value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = None
    cell = WriteOnlyCell(ws, value=parsed)
    cell.number_format = fmt
    return cell


def enrich(request, items, offers=()):
    """Поля, которых нет в строке базы: подписи и производные суммы."""
    row = dict(request)
    row['state_label'] = STATE_LABELS.get(request.get('state'), request.get('state'))
    row['kind_label'] = workflow.REQUEST_KIND_LABELS.get(request.get('request_kind'), '')
    # Этап — у живой заявки; у закрытой он уже сказан состоянием.
    row['stage_text'] = (workflow.STAGE_LABELS.get(request.get('stage'), '')
                         if request.get('status') == 'active' else '')
    row['object_label'] = workflow.OBJECT_TYPE_LABELS.get(request.get('object_type'), '')
    row['accounting_label'] = workflow.ACCOUNTING_CATEGORY_LABELS.get(request.get('accounting_category'), '')
    row['method_label'] = workflow.PAYMENT_METHOD_LABELS.get(request.get('payment_method'), '')
    amount = workflow.to_decimal(request.get('amount'))
    refund = workflow.to_decimal(request.get('refund_amount'))
    row['total_amount'] = amount - refund
    # Строка читается как в счёте: «что — сколько × цена = сумма строки»; сумма
    # расхода складывается из этих сумм.
    row['items_text'] = '\n'.join(
        '%s — %s%s × %s = %s' % (item.get('name'), _qty(item.get('quantity')),
                                 (' ' + item['unit']) if item.get('unit') else '',
                                 _plain_money(item.get('unit_price')),
                                 _plain_money(workflow.item_total(item)))
        for item in (items or []))
    # Варианты поставщиков — как их сравнивал инициатор; рекомендуемый помечен.
    row['offers_text'] = '\n'.join(
        '%s%s — %s%s' % ('★ ' if offer.get('is_recommended') else '', offer.get('supplier_name') or '—',
                         _plain_money(offer.get('amount')) if offer.get('amount') is not None else 'цена не указана',
                         ('; ' + offer['terms']) if offer.get('terms') else '')
        for offer in (offers or []))
    if request.get('no_alternatives'):
        reason = workflow.NO_ALTERNATIVES_LABELS.get(request.get('no_alternatives_reason'), '')
        comment = request.get('no_alternatives_comment') or ''
        row['alternatives_label'] = 'Отсутствуют: %s' % '; '.join(part for part in (reason, comment) if part)
    else:
        row['alternatives_label'] = ''
    # Номер карты в выгрузку не попадает — только маска (п. 5.2: полный номер
    # доступен по правам, а файл выгрузки уходит из раздела).
    if request.get('payment_method') == workflow.METHOD_CARD:
        row['card_label'] = ' · '.join(part for part in (
            workflow.CARD_RECIPIENT_LABELS.get(request.get('card_recipient'), ''),
            request.get('card_holder_name') or '') if part)
    else:
        row['card_label'] = ''
        row['card_mask'] = ''
    basis = request.get('route_basis') or {}
    row['approver_label'] = basis.get('basis') or ''
    row['docs_label'] = (workflow.CLOSING_DOC_LABELS.get(request.get('closing_docs_status'), '')
                         if request.get('paid_on') or request.get('received_on') else '')
    return row


def _plain_money(value):
    """Сумма без знака валюты — в тексте ячейки он повторялся бы в каждой строке."""
    return workflow.fmt_money(value).replace('\u00a0₸', '')


def _qty(value):
    number = workflow.to_decimal(value, default=None)
    if number is None:
        return ''
    if number == number.to_integral():
        return str(int(number))
    return str(number.normalize())


def build_workbook(requests, items_by_request, *, offers_by_request=None, generated_at=None, filters_text='',
                   total=None, truncated=False, text_warning_patch=None):
    generated_at = generated_at or datetime.now(ALMATY)
    workbook = Workbook(write_only=True)

    context = workbook.create_sheet('Контекст')
    context.column_dimensions['A'].width = 28
    context.column_dimensions['B'].width = 80
    title = WriteOnlyCell(context, value='Реестр заявок на закуп и оплату')
    title.font = TITLE_FONT
    context.append([title])
    context.append(['Собрано', generated_at.strftime('%d.%m.%Y %H:%M')])
    context.append(['Отбор', filters_text or 'все заявки'])
    context.append(['Строк в файле', len(requests)])
    if total is not None and total != len(requests):
        context.append(['Всего по отбору', total])
    if truncated:
        context.append(['Внимание', 'Файл обрезан по потолку строк — сузьте отбор'])
    context.append([])
    context.append(['Как читать', 'Позиции: «товар — количество × цена за единицу = сумма строки». '
                                  'Сумма — сумма строк; итоговая сумма = сумма − возврат. '
                                  'Варианты поставщиков: рекомендуемый отмечен звёздочкой. '
                                  'Карта — только последние четыре цифры: полного номера в выгрузке нет. '
                                  'БИН и номера документов лежат текстом.'])

    sheet = workbook.create_sheet('Заявки')
    sheet.freeze_panes = 'C2'
    for index, (header, _key, _kind) in enumerate(COLUMNS, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = WIDTHS.get(header, 18)
    header_cells = []
    for header, _key, _kind in COLUMNS:
        cell = WriteOnlyCell(sheet, value=header)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGN
        header_cells.append(cell)
    sheet.append(header_cells)

    for request in requests:
        row = enrich(request, items_by_request.get(request['id']) or [],
                     (offers_by_request or {}).get(request['id']) or [])
        cells = []
        for _header, key, kind in COLUMNS:
            value = row.get(key)
            if kind == 'text':
                cells.append(_text_cell(sheet, value))
            elif kind == 'money':
                cells.append(_money_cell(sheet, value))
            elif kind == 'date':
                cells.append(_date_cell(sheet, value, DAY_FORMAT))
            elif kind == 'datetime':
                cells.append(_date_cell(sheet, value, DATETIME_FORMAT))
            else:
                cells.append(_plain_cell(sheet, value))
        sheet.append(cells)

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)

    if text_warning_patch and requests:
        text_columns = [get_column_letter(i) for i, (_h, _k, kind) in enumerate(COLUMNS, start=1)
                        if kind == 'text']
        last_row = len(requests) + 1
        sqref = ' '.join('%s2:%s%s' % (col, col, last_row) for col in text_columns)
        # Лист «Заявки» — второй по порядку создания.
        stream = text_warning_patch(stream, sqref, sheet_path='xl/worksheets/sheet2.xml')
    return stream


def file_name(generated_at=None):
    generated_at = generated_at or datetime.now(ALMATY)
    return 'Заявки на закуп и оплату %s.xlsx' % generated_at.strftime('%Y-%m-%d')
