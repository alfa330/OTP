# -*- coding: utf-8 -*-
"""Таблицы статей → строки «поле: значение» для ИИ.

Зачем отдельный слой. wiki.sanitize.to_plain_text заменяет КАЖДЫЙ тег одним
пробелом, поэтому таблица «Город | Комиссия» превращается в поток значений:
«Город Комиссия Алматы 5% Астана 7%». Модель на таком уверенно склеивает не тот
город с не той комиссией, и ответ выглядит правдоподобно — то есть это худший
вид ошибки, чем отказ. В корпусе на 10.08.2026 таких таблиц 63 в 14 статьях.

Поэтому каждая строка таблицы разворачивается в самостоятельное утверждение:
«Город: Алматы; Комиссия: 5%». Такую строку модель не может перепутать, а
цитирование куска остаётся дословным.
"""

_MAX_CELL = 300          # клетка длиннее — обрезаем: это уже не таблица, а текст
_MAX_ROWS = 200          # защита от вырожденных таблиц-простыней


def _cell_text(cell):
    text = ' '.join(cell.get_text(' ', strip=True).split())
    if len(text) > _MAX_CELL:
        text = text[:_MAX_CELL].rstrip() + '…'
    return text


def _rows_of(table):
    """Строки таблицы как списки текстов клеток, в порядке документа."""
    out = []
    for row in table.find_all('tr'):
        cells = row.find_all(['th', 'td'], recursive=False)
        if not cells:                      # вложенные таблицы: берём что есть
            cells = row.find_all(['th', 'td'])
        texts = [_cell_text(c) for c in cells]
        if any(texts):
            out.append(texts)
        if len(out) >= _MAX_ROWS:
            break
    return out


def _looks_like_header(row_cells, table):
    """Первая строка — заголовок, если она из th или если ниже есть td-строки."""
    first = table.find('tr')
    if first is not None and first.find('th') is not None:
        return True
    # Без th: считаем заголовком, когда в первой строке нет чисел, а ниже есть.
    if len(row_cells) < 2:
        return False
    return not any(any(ch.isdigit() for ch in cell) for cell in row_cells)


def serialize_table(table):
    """Таблицу — в список текстовых строк, по одной на строку таблицы.

    Возвращает список строк, а не одну склеенную: нарезка на куски обязана
    уметь разорвать длинную таблицу по границе строки, а не посреди неё.
    """
    rows = _rows_of(table)
    if not rows:
        return []

    caption = ''
    cap = table.find('caption')
    if cap is not None:
        caption = ' '.join(cap.get_text(' ', strip=True).split())

    header = None
    if _looks_like_header(rows[0], table):
        header, rows = rows[0], rows[1:]

    lines = []
    if caption:
        lines.append('Таблица: ' + caption)
    if not rows:                            # только заголовок и ничего больше
        return lines + [' | '.join(header)] if header else lines

    for row in rows:
        if header:
            pairs = []
            for index, value in enumerate(row):
                if not value:
                    continue
                name = header[index] if index < len(header) else ''
                pairs.append(f'{name}: {value}' if name else value)
            line = '; '.join(pairs)
        else:
            line = ' | '.join(v for v in row if v)
        if line:
            lines.append(line)
    return lines
