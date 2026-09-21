# -*- coding: utf-8 -*-
"""Выгрузка журнала ознакомления в Excel (ТЗ #300, п.14).

Отдельный модуль, а не кусок routes.py: сборка книги — это чистая работа над
готовыми строками, без базы и без flask, и проверять её удобно без сервера.

Оформление взято у выгрузки задач (bot_schedule2.py: _task_export_fill_sheet)
целиком, а не придумано заново: человек открывает оба файла в одном Excel, и
две разные шапки в них читались бы как два разных продукта.
"""

from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import access as news_access

# (ключ, заголовок, ширина). Порядок — порядок чтения: сначала кто, потом что
# с ним произошло. Первые восемь — «минимальные поля выгрузки» из ТЗ дословно,
# остальные добавлены теми же данными, что уже есть в журнале на экране.
COLUMNS = (
    ('name', 'ФИО', 32),
    ('user_id', 'ID сотрудника', 14),
    ('department', 'Подразделение', 22),
    ('role', 'Должность', 18),
    ('published_at', 'Дата публикации', 18),
    ('shown_at', 'Первый показ', 18),
    ('confirmed_at', 'Дата ознакомления', 18),
    ('score', 'Результат теста', 16),
    ('attempts', 'Попыток', 10),
    ('trainer', 'Тренажёр', 12),
    ('wave', 'Волна', 9),
    ('wave_planned_at', 'Волна по плану', 18),
    ('wave_activated_at', 'Волна фактически', 18),
    ('status', 'Статус', 34),
)

_DATE_KEYS = ('published_at', 'shown_at', 'confirmed_at',
              'wave_planned_at', 'wave_activated_at')


def _moment(value):
    """ISO-строка журнала → datetime для ячейки. Не разобрали — пусто.

    Время в базе наивное (часы Алматы), таким и уходит в файл: перевод в UTC
    сдвинул бы «ознакомился в 09:14» на пять часов у всех сразу.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _value(key, row, post):
    if key == 'user_id':
        return row.get('user_id')
    if key == 'name':
        return row.get('name') or '—'
    if key == 'department':
        return row.get('department_name') or ''
    if key == 'role':
        return news_access.ROLE_TITLES.get(
            str(row.get('role') or '').lower(), row.get('role') or '')
    if key == 'published_at':
        return _moment(post.get('published_at'))
    if key in _DATE_KEYS:
        return _moment(row.get(key))
    if key == 'score':
        # «2 из 3» — так же, как на экране. Числом долю не пишем: в Excel её
        # немедленно сложат с другими долями и получат бессмыслицу.
        if row.get('last_total'):
            return '%s из %s' % (row.get('last_correct') or 0, row['last_total'])
        return ''
    if key == 'attempts':
        return row.get('attempts') or 0
    if key == 'trainer':
        return 'пройден' if row.get('trainer_passed_at') else ''
    if key == 'wave':
        return row.get('wave_no') or ''
    if key == 'status':
        # Подпись берётся у сервера, а не пишется здесь второй раз: файл и
        # экран обязаны называть одно состояние одним словом.
        label = news_access.PERSON_STATUS_LABELS.get(row.get('status'), '')
        return label + ('' if row.get('in_audience', True) else ' (уже не в адресатах)')
    return ''


def build(post, rows):
    """Книга Excel по журналу новости. Возвращает BytesIO, готовый к отдаче."""
    values = {key: [_value(key, row, post) for row in rows]
              for key, _title, _width in COLUMNS}
    # Колонку, пустую во ВСЕХ строках, не выводим: заголовок без единого
    # значения — шум, из-за которого лист приходится листать вбок. У новости
    # без теста, тренажёра и волн так уходит половина таблицы.
    keys = [key for key, _t, _w in COLUMNS
            if not rows or any(value not in (None, '', 0) for value in values[key])
            or key in ('user_id', 'attempts')]

    book = Workbook()
    sheet = book.active
    sheet.title = 'Ознакомление'

    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(fill_type='solid', fgColor='1F2937')
    for column, key in enumerate(keys, start=1):
        title, width = next((t, w) for k, t, w in COLUMNS if k == key)
        cell = sheet.cell(row=1, column=column, value=title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        sheet.column_dimensions[get_column_letter(column)].width = width

    for index in range(len(rows)):
        for column, key in enumerate(keys, start=1):
            cell = sheet.cell(row=index + 2, column=column, value=values[key][index])
            if key in _DATE_KEYS:
                cell.number_format = 'DD.MM.YYYY HH:MM'
            elif key in ('user_id', 'attempts', 'wave'):
                cell.number_format = '0'

    # Шапка закреплена и с фильтром: журнал на двести человек иначе не читается.
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = 'A1:%s%d' % (get_column_letter(len(keys)),
                                         max(len(rows) + 1, 2))

    stream = BytesIO()
    book.save(stream)
    stream.seek(0)
    return stream
