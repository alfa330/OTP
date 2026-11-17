# -*- coding: utf-8 -*-
"""Выгрузка «Отчёта по сменам» чата в Excel (SALES-1, #424): те же 14 колонок, что на экране.

Как у линии (activity_export.py): строки — ровно те, что отфильтрованы на экране, длительности и
время — ЗНАЧЕНИЯМИ времени (`[h]:mm:ss`), а не текстом. Листов три:

  * «Смены» — 14 колонок ТЗ в его порядке и его словами;
  * «Технические перерывы» — каждый с началом и окончанием (ТЗ: «технические перерывы должны
    фиксироваться с указанием времени начала и окончания»); на экране их показывает журнал смены;
  * «Как считается» — определения колонок словами, чтобы цифры сверялись без разработчика.
"""

from datetime import date, datetime, time, timedelta
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

COLUMNS = (
    ('Дата', 12),
    ('Оператор', 32),
    ('Группа', 14),
    ('Время входа в систему', 14),
    ('Среднее время первого ответа', 14),
    ('Общее время обработки чатов', 14),
    ('Активное время работы', 14),
    ('Время ожидания', 13),
    ('Время перерывов', 13),
    ('Время технического перерыва', 14),
    ('Количество взятых в работу чатов', 15),
    ('Количество обработанных чатов', 15),
    ('Время выхода из системы', 14),
    ('Фактически отработанное время', 15),
)
TECH_COLUMNS = (('Дата', 12), ('Оператор', 32), ('Начало', 12), ('Окончание', 12), ('Длительность', 13))

DATE_FORMAT = 'DD.MM.YYYY'
CLOCK_FORMAT = 'HH:MM:SS'
# ТЗ: «временные показатели — ЧЧ:ММ:СС» — часы двумя цифрами (00:01:20), а квадратные скобки не
# сворачивают сумму больше суток по кругу.
DURATION_FORMAT = '[hh]:mm:ss'

_HEADER_FILL = PatternFill('solid', fgColor='F1F5F9')
_HEADER_FONT = Font(bold=True, color='334155')
_MUTED_FONT = Font(italic=True, color='94A3B8')
_NOTE_FONT = Font(color='64748B', size=10)
_THIN = Side(style='thin', color='E2E8F0')
_LOGIN_COLUMN = 4
_LOGOUT_COLUMN = 13


def _clock(stamp_value):
    if not stamp_value:
        return None
    moment = datetime.strptime(str(stamp_value)[:19], '%Y-%m-%dT%H:%M:%S')
    return time(moment.hour, moment.minute, moment.second)


def _duration(seconds):
    return None if seconds is None else timedelta(seconds=int(seconds))


def _day(value):
    return datetime.strptime(str(value), '%Y-%m-%d').date() if value else None


def rule_rows():
    """Определения колонок словами — для листа «Как считается»; те же, что под «i» на экране."""
    return (
        ('Смена', 'От «Начать смену» до «Закончить смену» в «Чатах ОП». Если портал закрылся сам '
                  '(вкладку закрыли, компьютер уснул), а оператор вернулся раньше чем через 4 часа, — '
                  'это та же смена; после «Закончить смену» — только если вернулся в 15 минут. Смену, '
                  'оставленную открытой, закрывает тишина без сообщений и нажатий: 8 часов в «Активном», '
                  'после которых оператор сам вернулся к работе (ночью ждать чатов часами — это '
                  'ожидание), 4 часа в перерыве, тренинге или тех. паузе и 4 часа, после которых он так '
                  'и не вернулся. Смена относится к дате своего начала. В отчёте только завершённые — '
                  'те, что уже ничто не продлит; смена короче минуты без сообщений — не смена.'),
        ('Время входа в систему', 'Первое нажатие статуса смены («Начать смену»). Серым курсивом — '
                                  'прошлую смену не закрыли, и вход — первое сообщение после перерыва '
                                  'в работе.'),
        ('Среднее время первого ответа', 'Как «Ср. ответ» в разделе «Чаты ОП»: у чата за день одно '
                                         'значение — от первого сообщения клиента за этот день до первого '
                                         'исходящего после него; засчитано тому, кто ответил первым (если '
                                         'первой ушла рассылка — никому). Сумма этих времён, делённая на '
                                         'число чатов с его первым ответом; ответ относится к смене, в '
                                         'которую дан. У дневной смены цифра совпадает с «Чатами ОП» за этот '
                                         'день.'),
        ('Общее время обработки чатов', 'Обработка каждого чата: от сообщения клиента, на которое '
                                        'ответил оператор (или от реплики коллеги, после которой он '
                                        'подключился), но не больше 5 минут до ответа, — до его '
                                        'последнего сообщения; пауза в переписке дольше 15 минут '
                                        'обработку прерывает. Только время в «Активном». Чаты, которые '
                                        'шли параллельно, складываются.'),
        ('Активное время работы', 'То же время одной лентой на все чаты: минута, когда в работе было '
                                  'три чата, считается один раз. Только время в «Активном».'),
        ('Время ожидания', 'Время в системе без обработки чатов: «Активный», когда оператор не вёл ни '
                           'одного чата, и «Тренинг». Активное время, ожидание и тех. перерывы вместе — '
                           'фактически отработанное.'),
        ('Время перерывов', 'Сумма периодов статуса «Перерыв».'),
        ('Время технического перерыва', 'Сумма периодов статуса «Тех. пауза»; каждый — на листе '
                                        '«Технические перерывы» с началом и окончанием.'),
        ('Количество взятых в работу чатов', 'Разные чаты, где оператор в эту смену писал клиенту или '
                                             'нажал «Ответ не нужен» (нажатия записываются с 17.11.2026). '
                                             'Повторное открытие того же чата — не новый чат.'),
        ('Количество обработанных чатов', 'Из них те, где разговор закончил он: его последнее действие '
                                          '(сообщение или «Ответ не нужен») — последнее у сотрудников, '
                                          'пока переписка шла без пауз дольше 15 минут. Если клиент '
                                          'ответил и разговор продолжил коллега, чат обработал коллега; '
                                          'клиент вернулся через несколько часов — это новый разговор.'),
        ('Время выхода из системы', '«Закончить смену», закрытие смены системой (портал закрыт или '
                                    'статус не менялся 16 часов) или выход из iCORE Phone. Серым '
                                    'курсивом — выход не зафиксирован: стоит последнее действие смены.'),
        ('Фактически отработанное время', 'Выход минус вход минус обычные перерывы. Технические '
                                          'перерывы и тренинг не вычитаются; время вне системы внутри '
                                          'смены (портал был закрыт) в отработанное не входит.'),
    )


def _header(sheet, columns):
    for index, (title, width) in enumerate(columns, 1):
        cell = sheet.cell(row=1, column=index, value=title)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical='center')
        cell.border = Border(bottom=_THIN)
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.row_dimensions[1].height = 44
    sheet.freeze_panes = 'A2'


def _write(sheet, offset, values):
    for column, (value, number_format) in enumerate(values, 1):
        cell = sheet.cell(row=offset, column=column, value=value)
        if number_format and value is not None:
            cell.number_format = number_format


def workbook(rows, tech_breaks=(), *, month_label, generated_at):
    book = Workbook()
    sheet = book.active
    sheet.title = 'Смены'
    _header(sheet, COLUMNS)
    for offset, row in enumerate(rows, 2):
        _write(sheet, offset, (
            (_day(row.get('date')), DATE_FORMAT),
            (row.get('name') or '', None),
            (row.get('group_label') or '', None),
            (_clock(row.get('login_at')), CLOCK_FORMAT),
            (_duration(row.get('first_reply_seconds')), DURATION_FORMAT),
            (_duration(row.get('handling_seconds')), DURATION_FORMAT),
            (_duration(row.get('active_seconds')), DURATION_FORMAT),
            (_duration(row.get('waiting_seconds')), DURATION_FORMAT),
            (_duration(row.get('break_seconds')), DURATION_FORMAT),
            (_duration(row.get('tech_seconds')), DURATION_FORMAT),
            (int(row.get('taken_chats') or 0), '0'),
            (int(row.get('processed_chats') or 0), '0'),
            (_clock(row.get('logout_at')), CLOCK_FORMAT),
            (_duration(row.get('worked_seconds')), DURATION_FORMAT),
        ))
        if row.get('login_missing'):
            sheet.cell(row=offset, column=_LOGIN_COLUMN).font = _MUTED_FONT
        if row.get('logout_missing'):
            sheet.cell(row=offset, column=_LOGOUT_COLUMN).font = _MUTED_FONT

    last_row = max(1, len(rows) + 1)
    if rows:
        sheet.auto_filter.ref = 'A1:%s%d' % (get_column_letter(len(COLUMNS)), last_row)
    notes = ['%s · Верификаторы · сформировано %s' % (month_label, generated_at)]
    if not rows:
        notes.insert(0, 'За выбранный период завершённых смен нет.')
    if any(row.get('logout_missing') for row in rows):
        notes.append('Серым курсивом выход — смену не закрыли: стоит последнее действие смены.')
    if any(row.get('login_missing') for row in rows):
        notes.append('Серым курсивом вход — прошлую смену не закрыли: стоит первое сообщение.')
    offline = [row for row in rows if row.get('offline_seconds')]
    if offline:
        notes.append('В %d из %d смен оператор выходил из системы посреди смены (портал был закрыт): '
                     'это время в фактически отработанное не входит, поэтому там факт меньше, чем '
                     '«выход − вход − перерывы». Отрезки «Вне системы» — в журнале смены на экране.'
                     % (len(offline), len(rows)))
    for index, text in enumerate(notes):
        sheet.cell(row=last_row + 2 + index, column=1, value=text).font = _NOTE_FONT

    tech = book.create_sheet('Технические перерывы')
    _header(tech, TECH_COLUMNS)
    for offset, item in enumerate(tech_breaks or (), 2):
        _write(tech, offset, (
            (_day(item.get('date')), DATE_FORMAT),
            (item.get('name') or '', None),
            (_clock(item.get('start_at')), CLOCK_FORMAT),
            (_clock(item.get('end_at')), CLOCK_FORMAT),
            (_duration(item.get('seconds')), DURATION_FORMAT),
        ))
    if not tech_breaks:
        tech.cell(row=3, column=1, value='В выбранных сменах технических перерывов не было.').font = _NOTE_FONT

    rules = book.create_sheet('Как считается')
    rules.column_dimensions['A'].width = 34
    rules.column_dimensions['B'].width = 110
    for index, (title, text) in enumerate(rule_rows(), 1):
        name = rules.cell(row=index, column=1, value=title)
        name.font = Font(bold=True, color='334155')
        name.alignment = Alignment(vertical='top')
        body = rules.cell(row=index, column=2, value=text)
        body.alignment = Alignment(wrap_text=True, vertical='top')

    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def file_name(month_value):
    return 'smeny_chat_%s.xlsx' % (str(month_value or '').strip() or date.today().strftime('%Y-%m'))
