# -*- coding: utf-8 -*-
"""Выгрузка отчёта «Карточки клиента» в xlsx (задача #335, п. 11).

Листы: «Сводка», «Причины», «Города», «По дням», «Операторы», «Пояснения».

Каждая доля подписана формулой по буквам строк и словами — от чего она считается
(правило портала после «93 %», которые не поняли дважды): на «Сводке» строки названы
буквами, а доли на остальных листах ссылаются на «Б — заполнено карточек». Карточка с
двумя причинами входит в строки обеих, поэтому доли причин в сумме бывают больше 100 % —
это написано прямо под таблицей, а не оставлено на догадку.

Числа лежат числами, доли — числом с форматом процента: файл пересчитывают и фильтруют.
"""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill('solid', fgColor='1F2937')
HEADER_FONT = Font(bold=True, color='FFFFFF')
HEADER_ALIGN = Alignment(horizontal='center', vertical='center', wrap_text=True)
TITLE_FONT = Font(bold=True, size=13)
BOLD = Font(bold=True)
NOTE_FONT = Font(italic=True, color='6B7280')
WRAP_TOP = Alignment(wrap_text=True, vertical='top')
PERCENT = '0.0%'

SHEET_SUMMARY = 'Сводка'
SHEET_REASONS = 'Причины'
SHEET_CITIES = 'Города'
SHEET_DAYS = 'По дням'
SHEET_OPERATORS = 'Операторы'
SHEET_NOTES = 'Пояснения'

CITY_UNKNOWN = 'Город не указан'
NO_SUBTOPIC = 'без подтемы'


def report_filename(department_name, date_from, date_to):
    """«Карточка клиента Тез КЦ 01.11.2026–12.11.2026.xlsx»."""
    name = ILLEGAL_CHARACTERS_RE.sub('', str(department_name or '')).strip()
    for char in '\\/:*?"<>|':
        name = name.replace(char, ' ')
    return 'Карточка клиента %s %s–%s.xlsx' % (
        name, date_from.strftime('%d.%m.%Y'), date_to.strftime('%d.%m.%Y'))


def _clean(value):
    return ILLEGAL_CHARACTERS_RE.sub('', value) if isinstance(value, str) else value


def _widths(sheet, widths):
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def _header(sheet, row, titles):
    for column, title in enumerate(titles, start=1):
        cell = sheet.cell(row=row, column=column, value=title)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGN
    sheet.row_dimensions[row].height = 32


def _share(part, whole):
    return (float(part) / float(whole)) if whole else None


def _minutes(seconds):
    """Секунды → «1 мин 05 с»; None — прочерк."""
    if seconds is None:
        return '—'
    total = int(round(float(seconds)))
    return '%d мин %02d с' % (total // 60, total % 60) if total >= 60 else '%d с' % total


def summary_rows(data):
    """Строки «Сводки»: (буква, подпись, значение, формат | None, пояснение)."""
    totals, requests = data['totals'], data['requests']
    done, calls = totals['done'], totals['calls']
    return [
        ('А', 'Состоявшихся звонков с карточкой', calls, None,
         'Все звонки периода, по которым телефон открыл карточку. Звонки без разговора и без записи не считаются.'),
        ('Б', 'Заполнено карточек (из А)', done, None, 'Оператор выбрал аккаунт и причину и сохранил карточку.'),
        ('В', 'Не заполнено карточек (из А)', totals['pending'], None, 'А = Б + В.'),
        ('Г', 'Доля заполненных = Б ÷ А', _share(done, calls), PERCENT, 'От всех состоявшихся звонков.'),
        ('Д', 'Входящих (из А)', totals['incoming'], None, ''),
        ('Е', 'Исходящих (из А)', totals['outgoing'], None, 'А = Д + Е.'),
        ('Ж', 'Обращений водителей (из Б)', totals['drivers'], None, 'По виду выбранного аккаунта.'),
        ('З', 'Обращений клиентов (из Б)', totals['clients'], None, 'Б = Ж + З.'),
        ('И', 'Разных людей обратилось', totals['people'], None,
         'По аккаунту, а где его нет — по номеру. Один человек с пятью звонками — один.'),
        ('К', 'Сохранено позже срока постобработки (из Б)', totals['late'], None,
         'Карточку сохранили после того, как вышло время постобработки.'),
        ('Л', 'Доля сохранённых позже срока = К ÷ Б', _share(totals['late'], done), PERCENT,
         'От заполненных карточек, не от всех звонков.'),
        ('М', 'Среднее время на карточку после звонка', _minutes(totals['avg_wrapup_sec']), None,
         'От конца звонка до сохранения. Карточки, сохранённые ещё во время разговора, в среднее не входят.'),
        ('Н', 'Запросов в другие отделы', requests['total'], None, 'Созданы в периоде.'),
        ('О', 'Из них без решения (из Н)', requests['active'], None, 'Ждут ответа или ответ есть, но запрос не закрыт.'),
        ('П', 'Среднее время до первого ответа отдела', _minutes(requests['avg_first_reply_sec']), None,
         'По запросам, на которые ответили.'),
    ]


def build_workbook(data, *, department_name, date_from, date_to, generated_by='', generated_at=None):
    """Книга отчёта. date_to — последний день периода включительно. Возвращает поток."""
    workbook = Workbook()
    done = data['totals']['done']

    # ── Сводка ───────────────────────────────────────────────────────────────
    sheet = workbook.active
    sheet.title = SHEET_SUMMARY
    _widths(sheet, (6, 52, 18, 90))
    sheet.cell(row=1, column=1, value=_clean('Карточка клиента — %s' % (department_name or ''))).font = TITLE_FONT
    sheet.cell(row=2, column=1, value='Период: %s – %s' % (date_from.strftime('%d.%m.%Y'),
                                                           date_to.strftime('%d.%m.%Y')))
    _header(sheet, 4, ('', 'Показатель', 'Значение', 'Как считается'))
    for offset, (letter, label, value, number_format, note) in enumerate(summary_rows(data)):
        row = 5 + offset
        sheet.cell(row=row, column=1, value=letter).font = BOLD
        sheet.cell(row=row, column=2, value=label)
        cell = sheet.cell(row=row, column=3, value=value if value is not None else '—')
        if number_format and value is not None:
            cell.number_format = number_format
        cell.alignment = Alignment(horizontal='right')
        sheet.cell(row=row, column=4, value=note).alignment = WRAP_TOP
    sheet.freeze_panes = 'A5'

    # ── Причины ──────────────────────────────────────────────────────────────
    sheet = workbook.create_sheet(SHEET_REASONS)
    _widths(sheet, (44, 44, 14, 22))
    _header(sheet, 1, ('Категория', 'Подтема', 'Карточек', 'Доля = карточек ÷ Б'))
    row = 2
    for category in data['categories']:
        sheet.cell(row=row, column=1, value=_clean(category['title'])).font = BOLD
        sheet.cell(row=row, column=2, value='вся категория').font = NOTE_FONT
        sheet.cell(row=row, column=3, value=category['count']).font = BOLD
        cell = sheet.cell(row=row, column=4, value=_share(category['count'], done))
        cell.number_format = PERCENT
        cell.font = BOLD
        row += 1
        for sub in category['subtopics']:
            if sub['id'] is None and len(category['subtopics']) == 1:
                continue  # у категории нет подтем — строка повторила бы саму категорию
            sheet.cell(row=row, column=1, value=_clean(category['title']))
            sheet.cell(row=row, column=2, value=_clean(sub['title']) if sub['id'] is not None else NO_SUBTOPIC)
            sheet.cell(row=row, column=3, value=sub['count'])
            sheet.cell(row=row, column=4, value=_share(sub['count'], done)).number_format = PERCENT
            row += 1
    sheet.cell(row=row + 1, column=1, value=(
        'Б — заполнено карточек за период: %d (лист «Сводка»). Карточка с двумя причинами входит в строки обеих, '
        'поэтому доли в сумме бывают больше 100 %%. Число у категории — карточки с любой её подтемой, '
        'без двойного счёта.' % done)).font = NOTE_FONT
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = 'A1:D%d' % max(row - 1, 1)

    # ── Города ───────────────────────────────────────────────────────────────
    sheet = workbook.create_sheet(SHEET_CITIES)
    _widths(sheet, (40, 14, 22))
    _header(sheet, 1, ('Город', 'Карточек', 'Доля = карточек ÷ Б'))
    row = 2
    for city in data['cities']:
        sheet.cell(row=row, column=1, value=_clean(city['city']) or CITY_UNKNOWN)
        sheet.cell(row=row, column=2, value=city['count'])
        sheet.cell(row=row, column=3, value=_share(city['count'], done)).number_format = PERCENT
        row += 1
    sheet.cell(row=row + 1, column=1, value=(
        'Город — город аккаунта на момент обращения. Сумма строк = Б (%d): у карточки один город.' % done)
    ).font = NOTE_FONT
    sheet.freeze_panes = 'A2'

    # ── По дням ──────────────────────────────────────────────────────────────
    sheet = workbook.create_sheet(SHEET_DAYS)
    _widths(sheet, (14, 22, 22, 26))
    _header(sheet, 1, ('Дата', 'Состоявшихся звонков', 'Заполнено карточек', 'Доля заполненных = заполнено ÷ звонков'))
    for offset, day in enumerate(data['days']):
        row = 2 + offset
        sheet.cell(row=row, column=1, value=day['date']).number_format = 'DD.MM.YYYY'
        sheet.cell(row=row, column=2, value=day['calls'])
        sheet.cell(row=row, column=3, value=day['done'])
        sheet.cell(row=row, column=4, value=_share(day['done'], day['calls'])).number_format = PERCENT
    sheet.freeze_panes = 'A2'

    # ── Операторы ────────────────────────────────────────────────────────────
    sheet = workbook.create_sheet(SHEET_OPERATORS)
    _widths(sheet, (36, 14, 14, 16, 26, 20, 24))
    _header(sheet, 1, ('Оператор', 'Звонков', 'Заполнено', 'Не заполнено',
                       'Доля заполненных = заполнено ÷ звонков', 'Сохранено позже срока',
                       'Среднее время на карточку'))
    for offset, operator in enumerate(data['operators']):
        row = 2 + offset
        sheet.cell(row=row, column=1, value=_clean(operator['name']))
        sheet.cell(row=row, column=2, value=operator['calls'])
        sheet.cell(row=row, column=3, value=operator['done'])
        sheet.cell(row=row, column=4, value=operator['pending'])
        sheet.cell(row=row, column=5, value=_share(operator['done'], operator['calls'])).number_format = PERCENT
        sheet.cell(row=row, column=6, value=operator['late'])
        sheet.cell(row=row, column=7, value=_minutes(operator['avg_wrapup_sec']))
    sheet.freeze_panes = 'A2'

    # ── Пояснения ────────────────────────────────────────────────────────────
    sheet = workbook.create_sheet(SHEET_NOTES)
    _widths(sheet, (30, 110))
    lines = [
        ('Что в файле', 'Отчёт по карточкам клиента: причины обращений, подтемы, города, дни и операторы.'),
        ('Период', '%s – %s включительно, время Алматы. Звонок относится к дню, когда он начался.'
         % (date_from.strftime('%d.%m.%Y'), date_to.strftime('%d.%m.%Y'))),
        ('Что такое карточка', 'Запись об одном звонке: телефон открывает её, когда звонок поступил или начат. '
                               'Оператор выбирает аккаунт Тез и причину обращения.'),
        ('Что не считается', 'Звонки, которые не состоялись (не ответили, и оператор ничего не записал), '
                             'в отчёт не входят.'),
        ('Не заполнено', 'Звонок состоялся, а карточка не сохранена: причины у неё нет, поэтому в листах '
                         '«Причины» и «Города» её нет — только в «Сводке», «По дням» и «Операторах».'),
        ('Доли', 'У каждой доли в заголовке написано, что на что делится. Б — строка «Заполнено карточек» '
                 'листа «Сводка».'),
        ('Выгрузил', generated_by or ''),
        ('Выгружен', generated_at.strftime('%d.%m.%Y %H:%M') if generated_at else ''),
    ]
    for offset, (label, value) in enumerate(lines, start=1):
        sheet.cell(row=offset, column=1, value=label).font = BOLD
        sheet.cell(row=offset, column=2, value=_clean(value)).alignment = WRAP_TOP

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream
