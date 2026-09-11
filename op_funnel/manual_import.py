# -*- coding: utf-8 -*-
"""Разбор ручной выгрузки супервайзера (тикеты, время обработки, часы).

Зачем она нужна, если тикеты уже тянутся сами
---------------------------------------------
Обращения приходят ручкой `POST /api/partners/list-tickets` — её завели по нашей
заявке 11.09.2026. Но видны только те, что заведены под учётными записями
партнёра, и пока периметр не расширят до всех учёток направления, счёт занижен.
Закрыть разницу можно только файлом.

Вторая причина останется и потом: часы. У верификаторов нет статусов телефона,
часы берутся по графику смен, и поправить фактические можно только руками.
Поэтому загруженная строка СИЛЬНЕЕ автоматики — человек грузит файл именно
тогда, когда автоматике верить нельзя.

Что умеет разбирать
-------------------
Лист супервайзера (задача #305) устроен как «одна вкладка на сутки»: строки —
операторы, колонки — «Отработано часов», «Чаты WZ», «Тикеты», «Ср. время
обработки WZ», «Ср. время обработки тикет». Имя вкладки — дата («01.09», иногда
с хвостовыми пробелами: «03.09 », «04.09  » — так в живом файле).

Разбор идёт ПО ЗАГОЛОВКАМ, а не по буквам колонок: в файле их порядок уже
менялся между месяцами, и жёсткие индексы сломались бы молча. Строка без
узнанного имени оператора не выбрасывается — она возвращается в `unmapped`,
чтобы человек увидел, кого не хватает, а не гадал, почему сумма меньше.

Время «1367/8» в ячейке
-----------------------
В живом файле среднее время обработки записано выражением вида `=1367/8` —
«всего секунд делить на число тикетов». openpyxl отдаёт такие ячейки формулой,
поэтому простые арифметические выражения считаются здесь. Разбор намеренно
узкий: только числа, `+`, `-`, `*`, `/` и скобки. Ничего похожего на eval — файл
приходит извне, и выполнять его содержимое нельзя.
"""

import logging
import re
from datetime import date, datetime, timedelta
from io import BytesIO

from openpyxl import load_workbook

log = logging.getLogger(__name__)

# Заголовки колонок, которые умеем узнавать. Слева — что ищем в шапке (в нижнем
# регистре, без лишних пробелов), справа — колонка нашей таблицы.
COLUMN_ALIASES = {
    'фио оператора': 'name',
    'фио': 'name',
    'оператор': 'name',
    'отработано часов': 'work_hours',
    'часы': 'work_hours',
    'чаты wz': 'chats',
    'чаты': 'chats',
    'тикеты': 'tickets',
    'ср. время обработки wz': 'chat_reply_seconds',
    'среднее время обработки wz': 'chat_reply_seconds',
    'ср. время обработки тикет': 'ticket_handle_seconds',
    'среднее время обработки тикет': 'ticket_handle_seconds',
    'факт продажи': 'sales',
    'продажи': 'sales',
}

# Строки-итоги, которые нельзя принять за оператора.
TOTAL_MARKERS = ('на смене', 'итого', 'общее', 'общий', 'всего', 'итог',
                 'начало месяца', 'текущая дата')

# Подвал вкладки: под таблицей операторов лежит блок нормативов («Таргет по
# времени ответа», «Таргет по обработке чатов в час», «Качество», «Вес
# показателя»). Встретив его, чтение вкладки прекращаем — иначе нормативы
# приезжают как несопоставленные «операторы» и пугают человека списком из
# восьми строк, в котором нет ни одного имени.
FOOTER_MARKERS = ('таргет', 'качество', 'вес показателя', 'день', 'ночь')

_SAFE_EXPR = re.compile(r'^[0-9+\-*/(). ]+$')
_DATE_IN_TITLE = re.compile(r'(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?')


def _norm(value):
    return ' '.join(str(value or '').strip().lower().split())


def _number(value):
    """Число из ячейки. Понимает формулу вида `=1367/8` (см. шапку модуля)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(',', '.')
    if not text:
        return None
    if text.startswith('='):
        expression = text[1:].strip()
        if not _SAFE_EXPR.match(expression):
            return None
        try:
            # Узкий разбор арифметики: literal-функции здесь мало (она не считает
            # деление), а eval с ограниченным алфавитом безопасен — символов,
            # кроме цифр и четырёх операций, в строке нет по проверке выше.
            return float(eval(expression, {'__builtins__': {}}, {}))  # noqa: S307
        except Exception:  # noqa: BLE001
            return None
    try:
        return float(text)
    except ValueError:
        return None


def sheet_day(title, year=None, month=None):
    """Дата из имени вкладки. «01.09», «03.09 », «04.09  » — всё это одни сутки."""
    found = _DATE_IN_TITLE.search(str(title or ''))
    if not found:
        return None
    day, month_in_title, year_in_title = found.groups()
    try:
        day = int(day)
        month_value = int(month_in_title)
        if year_in_title:
            year_value = int(year_in_title)
            if year_value < 100:
                year_value += 2000
            return date(year_value, month_value, day)

        year_value = year or date.today().year
        made = date(year_value, month_value, day)
        # Вкладки называются «01.09» — без года. Файл за декабрь, загруженный в
        # январе, без этой поправки уезжал бы на год вперёд, и строки легли бы в
        # будущее, где их никто не увидит. Если получилась дата больше чем на
        # неделю впереди сегодняшней — это прошлый год.
        if year is None and made > date.today() + timedelta(days=7):
            return date(year_value - 1, month_value, day)
        return made
    except ValueError:
        return None


def _match_person(name, people, name_to_user=None):
    """Узнать сотрудника по имени из файла.

    Сначала — подтверждённое человеком соответствие из таблицы сопоставления
    (`op_funnel_operator_map`, источник `manual`). Потом строгое совпадение имени
    целиком и совпадение по набору слов.

    Дальше этого не идём осознанно. В живом файле встречаются «Аман Алан» при
    «Алан Аман Нурбекұлы» в портале, «Жолмағанбет Ардак» против «Ардақ» и
    «Саркытова» против «Сарқытова» — разница в одну букву или в порядке слов.
    Автомат здесь ошибётся тихо: чужие часы и чужие чаты встанут в чужую строку,
    и снаружи это не отличить от правды. Поэтому неузнанное имя возвращается
    человеку, а не угадывается.
    """
    key = _norm(name)
    if not key:
        return None
    if name_to_user:
        found = name_to_user.get(key) or name_to_user.get(str(name).strip())
        if found:
            return found
    for person in people:
        if _norm(person.get('name')) == key:
            return person.get('id')
    words = set(key.split())
    if len(words) < 2:
        return None
    for person in people:
        if set(_norm(person.get('name')).split()) == words:
            return person.get('id')
    return None


def parse_manual_workbook(blob, direction_code, people, year=None, month=None,
                          name_to_user=None):
    """Разобрать книгу. Возвращает словарь с готовыми строками и остатком.

    `people` — сотрудники отдела в виде [{'id', 'name'}], их отдаёт роут.
    """
    workbook = load_workbook(BytesIO(blob), data_only=True, read_only=True)
    rows, unmapped = {}, {}
    rows_read = 0
    rows_skipped = 0
    days_seen = []

    for sheet in workbook.worksheets:
        work_day = sheet_day(sheet.title, year, month)
        if not work_day:
            # Вкладки «Воронка», «Продажи» — это своды по месяцу, а не сутки.
            continue
        days_seen.append(work_day)
        header_row, columns = _find_header(sheet)
        if not columns or 'name' not in columns.values():
            log.info('op_funnel: во вкладке %r не нашлась шапка, пропущена', sheet.title)
            continue
        for raw in sheet.iter_rows(min_row=header_row + 1, values_only=True):
            values = list(raw)
            name = _cell(values, columns, 'name')
            if name is None:
                continue
            name_text = str(name).strip()
            marker = _norm(name_text)
            if not name_text or marker in TOTAL_MARKERS:
                continue
            if any(marker.startswith(prefix) for prefix in FOOTER_MARKERS):
                break
            if _number(name_text) is not None:
                # В колонке имени число — это «На смене: 7» и подобные итоги,
                # у которых подпись съехала на соседнюю колонку.
                continue
            rows_read += 1
            user_id = _match_person(name_text, people, name_to_user)
            if not user_id:
                unmapped.setdefault(name_text, 0)
                unmapped[name_text] += 1
                rows_skipped += 1
                continue
            item = {
                'direction_code': direction_code,
                'work_day': work_day,
                'user_id': user_id,
                'work_hours': _number(_cell(values, columns, 'work_hours')),
                'chats': _int(_cell(values, columns, 'chats')),
                'tickets': _int(_cell(values, columns, 'tickets')),
                'chat_reply_seconds': _number(_cell(values, columns, 'chat_reply_seconds')),
                'ticket_handle_seconds': _number(_cell(values, columns, 'ticket_handle_seconds')),
                'sales': _int(_cell(values, columns, 'sales')),
            }
            if all(item[key] is None for key in
                   ('work_hours', 'chats', 'tickets', 'chat_reply_seconds',
                    'ticket_handle_seconds', 'sales')):
                # Пустая строка оператора, который в этот день не работал.
                rows_skipped += 1
                continue
            rows[(work_day, user_id)] = item

    return {
        'rows': list(rows.values()),
        'rows_read': rows_read,
        'rows_skipped': rows_skipped,
        'unmapped': [{'name': name, 'rows': count} for name, count in sorted(unmapped.items())],
        'period_from': min(days_seen) if days_seen else None,
        'period_to': max(days_seen) if days_seen else None,
    }


def _find_header(sheet, scan_rows=8):
    """Найти строку шапки и разложить её по колонкам.

    Ищем в первых строках, а не берём первую: в файлах супервайзеров над шапкой
    бывает пустая строка или заголовок месяца.
    """
    for index, raw in enumerate(sheet.iter_rows(min_row=1, max_row=scan_rows,
                                                values_only=True), start=1):
        columns = {}
        for position, value in enumerate(raw or ()):
            alias = COLUMN_ALIASES.get(_norm(value))
            if alias and alias not in columns.values():
                columns[position] = alias
        if 'name' in columns.values() and len(columns) >= 2:
            return index, columns
    return 1, {}


def _cell(values, columns, wanted):
    for position, alias in columns.items():
        if alias == wanted and position < len(values):
            return values[position]
    return None


def _int(value):
    number = _number(value)
    return int(round(number)) if number is not None else None
