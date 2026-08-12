# -*- coding: utf-8 -*-
"""Точечная правка клеток таблицы: модель называет, программа меняет.

ЗАЧЕМ ЭТОТ МОДУЛЬ ПОЯВИЛСЯ. Обновление статьи документом сначала работало так:
таблицы статьи уходили под маркеры и возвращались дословно. Гарантия «ни одна
строка не потеряется» соблюдалась идеально — и оказалась бесполезной там, где
она нужнее всего. Замер на проде 12.08.2026, статья «Реестр акций»: документ
менял срок акции 50п-5к с 14 на 20 дней, и модель, не имея возможности тронуть
клетку, приписала РЯДОМ вторую таблицу. В статье стало и «14 дней», и «20 дней»
одновременно — то есть обновление превратилось в порчу данных, причём тихую.

Отсюда решение: модель НЕ переписывает таблицу, а присылает список правок вида
«таблица 1, строка 3, колонка 6: было → стало». Меняет клетки программа. Так
разделение труда честное — модель читает и сопоставляет (в этом она сильна),
а сетку правит код (здесь ошибка недопустима).

ТРИ ЗАЩИТЫ, каждая от своей беды:

  1. У правки обязательно указано СТАРОЕ значение, и оно сверяется с клеткой.
     Не совпало — правка не применяется, а уходит вопросом редактору. Это
     защита от съехавшей на строку нумерации: модель ошибётся в номере, но не
     в том, что она читала.
  2. Строки можно ДОБАВЛЯТЬ, но нельзя удалять. Удаление приходит вопросом:
     «в документе больше нет строки N — удалить?». Пропавшая из документа акция
     чаще означает «документ про другое», чем «акцию отменили», а тихо снесённая
     строка регламента стоит денег.
  3. Число клеток в добавляемой строке приводится к числу колонок таблицы:
     лишние отбрасываются, недостающие добираются пустыми. Иначе браузер
     нарисует сетку с уехавшими колонками.
  4. ССЫЛКИ переживают правку клетки. Раньше не переживали: замена значения
     стирала содержимое клетки целиком, а вместе с ним тег <a>. В таблицах вики
     ссылка это половина смысла — «ССЫЛКА НА ФОРМУ ДЛЯ ПОПОЛНЕНИЯ», «Добавить
     водителя», форма Google на каждый парк; потеря такой ссылки превращает
     строку в бесполезную. Теперь адреса собираются до правки и возвращаются
     обратно: по совпадению ярлыка, а если ярлык модель переписала — ссылка
     дописывается в конец клетки и об этом задаётся вопрос. Молча потерять
     адрес нельзя, даже ценой неаккуратной клетки.
"""

import re

from bs4 import BeautifulSoup

# «Т1 С3 К6: было => стало», номера строк и колонок с единицы, как в разметке
# для модели. Разделитель => выбран из-за того, что стрелку → модели пишут
# по-разному (→, ->, ==>), а слова «было/стало» они охотно переставляют.
_CELL_RE = re.compile(
    r'^Т\s*(\d+)\s*[,;]?\s*С\s*(\d+)\s*[,;]?\s*К\s*(\d+)\s*:\s*(.+?)\s*(?:=>|-->|->|→)\s*(.+)$',
    re.I)
_ADD_RE = re.compile(r'^Т\s*(\d+)\s*[,;]?\s*\+\s*СТРОКА\s*:\s*(.+)$', re.I)
_DEL_RE = re.compile(r'^Т\s*(\d+)\s*[,;]?\s*-\s*СТРОКА\s*(\d+)\s*:?\s*(.*)$', re.I)

PATCH_BLOCK_RE = re.compile(
    r'^[ \t]*ПРАВКИ\s+ТАБЛИЦ[ \t]*:[ \t]*\r?\n?(.*?)'
    r'(?=^[ \t]*(?:ИЗМЕНЕНИЯ|ВОПРОСЫ|СТАТЬЯ)[ \t]*:|\Z)',
    re.I | re.M | re.S)

_BULLET = re.compile(r'^\s*(?:[-*•]|\d+[.)])\s*')

PATCH_RULES = """
ПРАВКИ ТАБЛИЦ — единственный способ изменить таблицу. Сами таблицы не
переписывай: в тексте статьи они стоят маркерами [[ТАБЛИЦА-N]], маркеры оставь
на местах. Каждая правка — отдельной строкой, ровно в одном из трёх видов:

Т1 С3 К6: 31.08.2026 г. => 30.09.2026 г.
    заменить клетку: таблица 1, строка 3, колонка 6. СТАРОЕ значение обязательно
    и должно совпадать с тем, что ты видишь в клетке, иначе правка отклоняется.
Т1 +СТРОКА: Осенний бонус | 3 000 тг за 30 поездок | По умолчанию | Все парки | Добавить в форму | 01.09.2026 г.
    добавить строку в конец таблицы 1; клетки через | в порядке колонок.
Т1 -СТРОКА 12: акции нет в новом документе
    НЕ удаляет строку, а задаёт вопрос редактору. Пиши так, если строка исчезла
    из документа: решает человек.

Если таблицы менять не нужно, напиши «ПРАВКИ ТАБЛИЦ: нет».

ССЫЛКИ. В клетках они показаны как «ярлык (адрес)». Если правишь такую клетку,
переноси ссылку в том же виде — «ярлык (адрес)»; менять адрес можно только когда
новый адрес прямо дан в документе. Ярлык без адреса означает потерянную ссылку.
"""


def _rows_of(table):
    return table.find_all('tr')


def _cells_of(row):
    return row.find_all(('td', 'th'))


def _text(node):
    return ' '.join(node.get_text(' ', strip=True).split())


def _text_with_links(node):
    """Текст клетки, но со ссылками в виде «ярлык (адрес)».

    Модель обязана ВИДЕТЬ адрес: без него она не знает, что в клетке ссылка, и
    возвращает вместо неё простой текст. Форма записи та же, что у чанкера
    индекса (wiki/ai/chunker.py) — одна привычка на весь раздел.
    """
    clone = BeautifulSoup(str(node), 'html.parser')
    for anchor in clone.find_all('a'):
        href = str(anchor.get('href') or '').strip()
        label = ' '.join(anchor.get_text(' ', strip=True).split())
        if href and href not in label:
            anchor.string = '%s (%s)' % (label, href) if label else href
    return ' '.join(clone.get_text(' ', strip=True).split())


_URL_RE = re.compile(r'(https?://[^\s<>"\')\]]+|mailto:[^\s<>"\')\]]+)')


def _anchors_of(cell):
    """Ссылки клетки: (адрес, ярлык, все атрибуты) — до правки."""
    out = []
    for anchor in cell.find_all('a'):
        href = str(anchor.get('href') or '').strip()
        if href:
            out.append((href, _text(anchor), dict(anchor.attrs)))
    return out


def _fill_cell(soup, cell, value, anchors):
    """Записать новое значение клетки, сохранив ссылки.

    Порядок: сначала адреса, которые модель написала прямо в тексте (в форме
    «ярлык (адрес)» или просто адресом), затем ярлыки прежних ссылок. Возвращает
    множество сохранённых адресов, чтобы вызывающий увидел потерянные.
    """
    cell.clear()
    kept = set()

    # «ярлык (адрес)» → ссылка с этим ярлыком; сам адрес из текста убираем.
    text = str(value or '')
    for href, label, _attrs in anchors:
        text = text.replace('%s (%s)' % (label, href), label or href)
    text = re.sub(r'\s*\((https?://[^)\s]+|mailto:[^)\s]+)\)', lambda m: ' ' + m.group(1), text)

    known = {label.lower(): (href, attrs) for href, label, attrs in anchors if label}

    def add_link(href, label, attrs=None):
        anchor = soup.new_tag('a', href=href)
        for name, val in (attrs or {}).items():
            if name != 'href':
                anchor[name] = val
        anchor.string = label or href
        cell.append(anchor)
        kept.add(href)

    # Разбираем текст на куски: голые адреса становятся ссылками.
    position = 0
    for match in _URL_RE.finditer(text):
        before = text[position:match.start()]
        if before:
            cell.append(before)
        href = match.group(0).rstrip('.,;')
        label = next((lbl for lbl, (h, _a) in known.items() if h == href), '') or href
        add_link(href, label, dict(known.get(label.lower(), ('', {}))[1] or {}))
        position = match.end()
    tail = text[position:]
    if tail:
        cell.append(tail)

    # Прежние ссылки, ярлык которых остался в тексте: оборачиваем ярлык обратно.
    for href, label, attrs in anchors:
        if href in kept or not label:
            continue
        for node in list(cell.find_all(string=True)):
            index = str(node).lower().find(label.lower())
            if index == -1 or node.parent.name == 'a':
                continue
            raw = str(node)
            node.replace_with(raw[:index])
            anchor = soup.new_tag('a', href=href)
            for name, val in attrs.items():
                if name != 'href':
                    anchor[name] = val
            anchor.string = raw[index:index + len(label)]
            cell.append(anchor)
            rest = raw[index + len(label):]
            if rest:
                cell.append(rest)
            kept.add(href)
            break
    return kept


def _squash(value):
    return ' '.join(str(value or '').split()).lower().replace(' ', ' ')


def serialize(tables, *, max_rows=60, max_cell=160):
    """Таблицы в вид, по которому модель может назвать номера строк и колонок.

    Номера физические: строка — порядковый <tr> с единицы, колонка — порядковая
    клетка внутри строки. Объединённые клетки нарочно не разворачиваются:
    модель видит ровно то, что потом увидит патчер, и рассинхронизации нет.
    """
    out = []
    for index, html in enumerate(tables, start=1):
        table = BeautifulSoup(str(html or ''), 'html.parser').find('table')
        if table is None:
            continue
        rows = _rows_of(table)
        lines = ['ТАБЛИЦА %d (строк %d):' % (index, len(rows))]
        for row_index, row in enumerate(rows[:max_rows], start=1):
            cells = _cells_of(row)
            head = ' [шапка]' if cells and all(c.name == 'th' for c in cells) else ''
            body = ' | '.join('К%d=%s' % (position, _text_with_links(cell)[:max_cell])
                              for position, cell in enumerate(cells, start=1))
            lines.append('  С%d%s: %s' % (row_index, head, body))
        if len(rows) > max_rows:
            lines.append('  … ещё %d строк' % (len(rows) - max_rows))
        out.append('\n'.join(lines))
    return '\n\n'.join(out)


def parse(text):
    """Разобрать блок «ПРАВКИ ТАБЛИЦ». Возвращает список правок."""
    match = PATCH_BLOCK_RE.search(str(text or ''))
    if not match:
        return []
    patches = []
    for raw in (match.group(1) or '').splitlines():
        line = _BULLET.sub('', raw).strip()
        if not line or _squash(line) in ('нет', 'нет.', '—', '-'):
            continue
        cell = _CELL_RE.match(line)
        if cell:
            patches.append({'kind': 'cell', 'table': int(cell.group(1)),
                            'row': int(cell.group(2)), 'col': int(cell.group(3)),
                            'was': cell.group(4).strip(),
                            'now': cell.group(5).strip()})
            continue
        add = _ADD_RE.match(line)
        if add:
            values = [part.strip() for part in add.group(2).split('|')]
            patches.append({'kind': 'add', 'table': int(add.group(1)),
                            'values': values})
            continue
        drop = _DEL_RE.match(line)
        if drop:
            patches.append({'kind': 'ask_delete', 'table': int(drop.group(1)),
                            'row': int(drop.group(2)),
                            'reason': drop.group(3).strip()})
    return patches[:60]


def _diff_excerpt(before, after, width=60):
    """Показать в строке изменения именно то, ЧЕМ значения различаются.

    Иначе выходит бесполезное «было X → стало X»: длинные клетки обрезались по
    первым 60 знакам, а различие сидело дальше. Замер на проде это и выдал —
    строка правки выглядела как замена на саму себя.
    """
    before, after = str(before or ''), str(after or '')
    if before[:width] != after[:width]:
        return before[:width], after[:width]
    common = 0
    while (common < min(len(before), len(after))
           and before[common] == after[common]):
        common += 1
    start = max(0, common - width // 3)
    prefix = '…' if start else ''
    return (prefix + before[start:start + width],
            prefix + after[start:start + width])


def _column_count(rows):
    return max((len(_cells_of(row)) for row in rows), default=0)


def apply(tables, patches):
    """Применить правки. Возвращает (таблицы, изменения, вопросы, отклонённые).

    Таблицы возвращаются НОВЫМ списком: исходные строки не мутируются, чтобы
    вызывающий мог сравнить до и после.
    """
    soups, changes, questions, rejected = [], [], [], []
    for html in tables:
        soups.append(BeautifulSoup(str(html or ''), 'html.parser'))

    def table_of(number):
        if 1 <= number <= len(soups):
            return soups[number - 1].find('table')
        return None

    for patch in patches:
        table = table_of(patch.get('table', 0))
        if table is None:
            rejected.append('таблицы %s нет' % patch.get('table'))
            continue
        rows = _rows_of(table)

        if patch['kind'] == 'cell':
            if not 1 <= patch['row'] <= len(rows):
                rejected.append('в таблице %d нет строки %d'
                                % (patch['table'], patch['row']))
                continue
            cells = _cells_of(rows[patch['row'] - 1])
            if not 1 <= patch['col'] <= len(cells):
                rejected.append('в таблице %d строке %d нет колонки %d'
                                % (patch['table'], patch['row'], patch['col']))
                continue
            cell = cells[patch['col'] - 1]
            current = _text(cell)
            # Сверка со СТАРЫМ значением: защита от съехавшей нумерации.
            # Достаточно вхождения — модель обрезает длинные клетки.
            if _squash(patch['was']) not in _squash(current) \
                    and _squash(current) not in _squash(patch['was']):
                questions.append(
                    'Правка таблицы %d, строка %d, колонка %d не применена: там '
                    '«%s», а ИИ ожидал «%s». Проверьте вручную.'
                    % (patch['table'], patch['row'], patch['col'],
                       current[:60], patch['was'][:60]))
                continue
            anchors = _anchors_of(cell)
            kept = _fill_cell(soups[patch['table'] - 1], cell, patch['now'], anchors)
            lost = [(href, label) for href, label, _a in anchors if href not in kept]
            for href, label in lost:
                # Ссылку не бросаем: дописываем в конец клетки и спрашиваем.
                # Пустая клетка вместо формы Google дороже неаккуратной.
                anchor = soups[patch['table'] - 1].new_tag('a', href=href)
                anchor.string = label or href
                cell.append(' ')
                cell.append(anchor)
                questions.append(
                    'В клетке (таблица %d, строка %d, колонка %d) была ссылка «%s» — '
                    'ИИ её не перенёс, она дописана в конец клетки. Проверьте место.'
                    % (patch['table'], patch['row'], patch['col'], label or href))
            was_excerpt, now_excerpt = _diff_excerpt(current, patch['now'])
            changes.append('таблица %d, строка %d: «%s» → «%s»'
                           % (patch['table'], patch['row'],
                              was_excerpt, now_excerpt))

        elif patch['kind'] == 'add':
            width = _column_count(rows)
            values = list(patch['values'])[:width] if width else list(patch['values'])
            while width and len(values) < width:
                values.append('')
            new_row = soups[patch['table'] - 1].new_tag('tr')
            for value in values:
                cell = soups[patch['table'] - 1].new_tag('td')
                # Адрес в новой строке тоже обязан стать ссылкой, иначе он
                # приезжает текстом и по нему нельзя щёлкнуть.
                _fill_cell(soups[patch['table'] - 1], cell, value, [])
                new_row.append(cell)
            body = table.find('tbody') or table
            body.append(new_row)
            changes.append('таблица %d: добавлена строка «%s»'
                           % (patch['table'], (values[0] if values else '')[:60]))

        elif patch['kind'] == 'ask_delete':
            label = ''
            if 1 <= patch['row'] <= len(rows):
                cells = _cells_of(rows[patch['row'] - 1])
                label = _text(cells[0])[:60] if cells else ''
            questions.append(
                'Строку «%s» (таблица %d, строка %d) ИИ предлагает удалить: %s. '
                'Удалить её?' % (label, patch['table'], patch['row'],
                                 patch['reason'] or 'нет в новом документе'))

    return [str(soup) for soup in soups], changes, questions, rejected
