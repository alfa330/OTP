# -*- coding: utf-8 -*-
"""Точечная правка и заполнение таблиц: модель называет, программа меняет.

ЗАЧЕМ ЭТОТ МОДУЛЬ ПОЯВИЛСЯ. Обновление статьи документом сначала работало так:
таблицы статьи уходили под маркеры и возвращались дословно. Гарантия «ни одна
строка не потеряется» соблюдалась идеально — и оказалась бесполезной там, где
она нужнее всего. Замер на проде 12.08.2026, статья «Реестр акций»: документ
менял срок акции 50п-5к с 14 на 20 дней, и модель, не имея возможности тронуть
клетку, приписала РЯДОМ вторую таблицу. В статье стало и «14 дней», и «20 дней»
одновременно — то есть обновление превратилось в порчу данных, причём тихую.

Отсюда решение: модель НЕ переписывает таблицу, а присылает список правок вида
«таблица 1, строка 3, клетка 6: было → стало». Меняет клетки программа. Так
разделение труда честное — модель читает и сопоставляет (в этом она сильна),
а сетку правит код (здесь ошибка недопустима).

ЗАЩИТЫ, каждая от своей беды:

  1. У правки обязательно указано СТАРОЕ значение, и оно сверяется с клеткой.
     Не совпало — правка не применяется, а уходит вопросом редактору. Это
     защита от съехавшей на строку нумерации: модель ошибётся в номере, но не
     в том, что она читала. Пустая клетка — тоже значение: старое пишется
     словом «пусто», и правка, ожидавшая текст, в пустую клетку не ляжет.
  2. Строки можно ДОБАВЛЯТЬ, но нельзя удалять. Удаление приходит вопросом:
     «в документе больше нет строки N — удалить?». Пропавшая из документа акция
     чаще означает «документ про другое», чем «акцию отменили», а тихо снесённая
     строка регламента стоит денег.
  3. Число клеток в добавляемой строке приводится к сетке таблицы — с учётом
     объединённых клеток: лишние не вставляются (и об этом спрашивают),
     недостающие добираются пустыми. Иначе браузер нарисует сетку с уехавшими
     колонками.
  4. ССЫЛКИ переживают правку клетки. Раньше не переживали: замена значения
     стирала содержимое клетки целиком, а вместе с ним тег <a>. В таблицах вики
     ссылка это половина смысла — «ССЫЛКА НА ФОРМУ ДЛЯ ПОПОЛНЕНИЯ», «Добавить
     водителя», форма Google на каждый парк; потеря такой ссылки превращает
     строку в бесполезную. Теперь адреса собираются до правки и возвращаются
     обратно: по совпадению ярлыка, а если ярлык модель переписала — ссылка
     дописывается в конец клетки и об этом задаётся вопрос. Молча потерять
     адрес нельзя, даже ценой неаккуратной клетки.
  5. ОФОРМЛЕНИЕ И ХВОСТ КЛЕТКИ переживают правку (27.11.2026). Раньше новое
     значение записывалось в клетку целиком: жирный, выделение цветом и
     выравнивание текста пропадали, а если модель присылала ОБРЕЗАННОЕ старое
     значение (длинные клетки она видит усечёнными), клетка заменялась этим
     обрезком — и всё, что стояло после него, исчезало. Теперь меняется только
     изменившийся кусок, на месте; не получилось — клетка собирается заново
     внутри прежних обёрток (абзац с выравниванием, жирный, цвет).

ЗАПОЛНЕНИЕ (27.11.2026, «обучи ИИ делать такие таблицы и заполнять их»):
строка целиком («Т1 С4: Алматы | 5 % | Эконом» — только в пустые клетки),
вставка строки после N-й и новая колонка — справа или после клетки шапки.
Номера в правках — всегда по таблице, какой её видела модель: сначала
применяются правки клеток, потом колонки (справа налево, чтобы вставка не
сдвигала следующую), потом строки — после запомненной исходной строки.
"""

import re

from bs4 import BeautifulSoup, NavigableString

# «Т1 С3 К6: было => стало», номера строк и клеток с единицы, как в разметке
# для модели. Разделитель => выбран из-за того, что стрелку → модели пишут
# по-разному (→, ->, ==>), а слова «было/стало» они охотно переставляют.
# Латинские T, C, K рядом с кириллицей — модели иногда пишут их вперемешку, и
# правка, не узнанная из-за одной буквы, пропадала бы молча.
_T = r'^[ТT]\s*(\d+)\s*[,;]?\s*'
_ARROW = r'(?:==>|=>|-->|->|→|⇒)'
_CELL_RE = re.compile(
    _T + r'[СC]\s*(\d+)\s*[,;]?\s*[КK]\s*(\d+)\s*:\s*(.+?)\s*' + _ARROW + r'\s*(.+)$',
    re.I)
_ROW_RE = re.compile(_T + r'[СC]\s*(\d+)\s*:\s*(.*)$', re.I)
# «после С3» — как в образце; «после строки 3» — как модели пишут по-русски.
_ADD_RE = re.compile(
    _T + r'\+\s*СТРОК[АУ]\s*(?:после\s+(?:[СC]|строки)\s*(\d+)\s*)?:\s*(.+)$', re.I)
_COL_RE = re.compile(
    _T + r'\+\s*КОЛОНК[АУ]\s*(?:после\s+(?:[КK]|клетки|колонки)\s*(\d+)\s*)?:\s*(.+)$', re.I)
_DEL_RE = re.compile(_T + r'-\s*СТРОК[АУ]\s*(\d+)\s*:?\s*(.*)$', re.I)
# Подпись объединения из serialize («[на 2 колонки]») — подсказка модели, а не
# значение клетки. Переписанная в правку, она легла бы в клетку текстом.
_SPAN_NOTE = re.compile(r'\s*\[на \d+ (?:колонк|строк)\w*(?:, на \d+ (?:колонк|строк)\w*)?\]',
                        re.I)
# Строка, начатая как правка («Т1 С4 …», «Т1 +…»), но не подошедшая ни под
# один вид. Пояснение вроде «Т1 — правок нет» правкой не считается: вопрос
# человеку о нём был бы шумом.
_LOOKS_LIKE_PATCH = re.compile(r'^[ТT]\s*\d+.*(?:[СCКK]\s*\d|\+|=>|→|->)', re.I)

PATCH_BLOCK_RE = re.compile(
    r'^[ \t]*ПРАВКИ\s+ТАБЛИЦ[ \t]*:[ \t]*\r?\n?(.*?)'
    r'(?=^[ \t]*(?:ИЗМЕНЕНИЯ|ВОПРОСЫ|СТАТЬЯ)[ \t]*:|\Z)',
    re.I | re.M | re.S)

_BULLET = re.compile(r'^\s*(?:[-*•]|\d+[.)])\s*')

# Старое значение пустой клетки. «—» сюда не входит: в таблицах вики прочерк —
# частое настоящее значение («—» в колонке «Депозит»), и путать его с пустотой
# нельзя. Но если клетка и правда пуста, а модель написала прочерк, это та же
# пустота — см. _matches.
_EMPTY_WORDS = ('пусто', '(пусто)', 'пустая', '(пустая)', 'пустая клетка', '<пусто>')
_DASHES = ('—', '-', '–')

PATCH_LIMIT = 120
_MAX_SPAN = 40

PATCH_RULES = """
ПРАВКИ ТАБЛИЦ — единственный способ изменить или заполнить таблицу статьи. Сами
таблицы не переписывай: в тексте статьи они стоят маркерами [[ТАБЛИЦА-N]],
маркеры оставь на местах. Номера строк (С) и клеток (К) бери из «СОДЕРЖИМОГО
ТАБЛИЦ». Каждая правка — отдельной строкой, в одном из видов:

Т1 С3 К6: 31.08.2026 г. => 30.09.2026 г.
    заменить клетку: таблица 1, строка 3, клетка 6. СТАРОЕ значение обязательно
    и должно совпадать с тем, что ты видишь в клетке, иначе правка отклоняется.
Т1 С4 К2: пусто => 5 %
    заполнить пустую клетку: старое значение у неё — слово «пусто». Очистить
    клетку — наоборот: «Т1 С4 К2: 5 % => пусто», только по прямому указанию.
Т1 С4: Алматы | 5 % | Эконом
    заполнить строку 4 по клеткам слева направо. Ложится ТОЛЬКО в пустые
    клетки; занятую клетку меняй правкой со старым значением. Клетку, которую
    заполнять не нужно, оставь пустой между | |.
Т1 +СТРОКА: Осенний бонус | 3 000 тг за 30 поездок | По умолчанию | Все парки
    добавить строку в конец таблицы 1; клетки через | в порядке колонок.
Т1 +СТРОКА после С3: Караганда | 4 % | Эконом
    вставить строку сразу после строки 3.
Т1 +КОЛОНКА: Телефон | +7 700 000 00 01 | +7 700 000 00 02
    добавить колонку справа: первое значение — подпись в шапке (строка 1),
    дальше по одному значению на каждую следующую строку, по порядку.
Т1 +КОЛОНКА после К1: Офис | ул. Абая 1 | пр. Республики 5
    то же, но новая колонка встаёт сразу после клетки 1 шапки.
Т1 -СТРОКА 12: акции нет в новом документе
    НЕ удаляет строку, а задаёт вопрос редактору. Пиши так, если строка исчезла
    из документа: решает человек.

Значения — только из статьи, документа или указания: чего там нет, клетку не
заполняй. Слово в клетке можно выделить: <strong>…</strong>, <em>…</em>.
Оформление, которое в клетке уже есть, сохраняется само — повторять его не нужно.
Если таблицы менять не нужно, напиши «ПРАВКИ ТАБЛИЦ: нет».

ССЫЛКИ. В клетках они показаны как «ярлык (адрес)». Если правишь такую клетку,
переноси ссылку в том же виде — «ярлык (адрес)»; менять адрес можно только когда
новый адрес прямо дан в документе. Ярлык без адреса означает потерянную ссылку.
"""


def _rows_of(table):
    """Строки ЭТОЙ таблицы. Строки таблицы, вложенной в клетку, — не её строки."""
    return [row for row in table.find_all('tr') if row.find_parent('table') is table]


def _cells_of(row):
    return row.find_all(('td', 'th'), recursive=False)


def _text(node):
    return ' '.join(node.get_text(' ', strip=True).split())


_MEDIA = ('img', 'video', 'table')


def _is_empty(cell):
    """Пустая клетка: ни текста, ни картинки. Клетка с одним значком — не пустая.

    Текст у неё пустой, и без этой оговорки заполнение строки стёрло бы значок
    молча — ровно так, как раньше правка значения стирала ссылку.
    """
    return not _text(cell) and cell.find(_MEDIA) is None


def _span(value, limit):
    try:
        number = int(str(value or '').strip() or 1)
    except ValueError:
        number = 1
    return max(1, min(max(1, limit), number))


def _layout(table):
    """Сетка таблицы по правилам HTML: (строки, клетки по строкам, занятость, ширина).

    Клетка встаёт в первую свободную позицию строки: позиции, занятые
    объединением сверху (rowspan), пропускаются. Ровно так раскладывает таблицу
    браузер, и только по этой сетке можно сказать, в какую колонку попадёт
    вставленная клетка.
    """
    rows = _rows_of(table)
    taken, entries = {}, []
    for r, row in enumerate(rows):
        c, row_entries = 0, []
        for cell in _cells_of(row):
            while (r, c) in taken:
                c += 1
            entry = {'cell': cell, 'row': r, 'col': c,
                     'colspan': _span(cell.get('colspan'), _MAX_SPAN),
                     'rowspan': _span(cell.get('rowspan'), len(rows) - r)}
            for dr in range(entry['rowspan']):
                for dc in range(entry['colspan']):
                    taken[(r + dr, c + dc)] = entry
            row_entries.append(entry)
            c += entry['colspan']
        entries.append(row_entries)
    width = max((c for _r, c in taken), default=-1) + 1
    return rows, entries, taken, width


# Сетку и строки таблицы считает и authoring (подсказка модели, ремонт рваных
# строк) — одной и той же функцией, иначе подсказка и правка разойдутся.
rows_of = _rows_of
layout = _layout


def _column_widths(entries, width):
    """Ширина каждой колонки сетки из colwidth клеток (редактор пишет её туда)."""
    widths = [None] * width
    for row in entries:
        for entry in row:
            parts = [part for part in str(entry['cell'].get('colwidth') or '').split(',')
                     if part.strip().isdigit()]
            for offset, part in enumerate(parts[:entry['colspan']]):
                col = entry['col'] + offset
                if col < width and widths[col] is None:
                    widths[col] = int(part)
    return widths


def _plural(number, one, few, many):
    tail = number % 100
    if 11 <= tail <= 14:
        return many
    tail = number % 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


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

# Выделение, которое модель вправе написать в значении клетки. Остальная
# разметка в значении — текст: клетка не место для заголовков и блоков.
_INLINE_RE = re.compile(r'<\s*(/?)\s*(strong|b|em|i|u|s|mark)\s*>|<\s*br\s*/?\s*>', re.I)
# Во что может быть завёрнуто ВСЁ содержимое клетки — эти обёртки правка
# значения переносит на новое значение.
_WRAPPERS = ('strong', 'b', 'em', 'i', 'u', 's', 'mark', 'span', 'code')


def _anchors_of(cell):
    """Ссылки клетки: (адрес, ярлык, все атрибуты) — до правки."""
    out = []
    for anchor in cell.find_all('a'):
        href = str(anchor.get('href') or '').strip()
        if href:
            out.append((href, _text(anchor), dict(anchor.attrs)))
    return out


def _unlink(text, anchors):
    """«ярлык (адрес)» известной ссылки → ярлык; «(адрес)» → голый адрес."""
    text = str(text or '')
    for href, label, _attrs in anchors:
        text = text.replace('%s (%s)' % (label, href), label or href)
    return re.sub(r'\s*\((https?://[^)\s]+|mailto:[^)\s]+)\)',
                  lambda m: ' ' + m.group(1), text)


def _plain(text):
    """Значение без разметки выделения — для сравнения с клеткой."""
    return ' '.join(_INLINE_RE.sub(' ', str(text or '')).split())


def _anchor(soup, href, label, attrs=None):
    # href первым: так ссылка выглядит одинаково с теми, что пишет редактор.
    anchor = soup.new_tag('a', href=href)
    for name, value in (attrs or {}).items():
        if name != 'href':
            anchor[name] = value
    anchor.string = label or href
    return anchor


def _meaningful(node):
    return [child for child in node.children
            if getattr(child, 'name', None) or str(child).strip()]


def _wrappers(cell):
    """Обёртки, в которых стоит ВСЁ содержимое клетки: (абзац или None, [выделения]).

    Абзац у клетки свой у каждой (так её хранит редактор), и в нём живёт
    выравнивание текста. Выделения — только сплошные: «<strong>Итого</strong>»
    да, «<strong>Срок:</strong> 14 дней» нет — там жирный у части, и навязать
    его всему новому значению значило бы выдумать оформление.
    """
    block, inline = None, []
    node = cell
    kids = _meaningful(node)
    if len(kids) == 1 and getattr(kids[0], 'name', None) == 'p':
        block = node = kids[0]
        kids = _meaningful(node)
    while len(kids) == 1 and getattr(kids[0], 'name', None) in _WRAPPERS:
        inline.append(kids[0])
        node = kids[0]
        kids = _meaningful(node)
    return block, inline


def _clone(soup, tag):
    copy = soup.new_tag(tag.name)
    copy.attrs = dict(tag.attrs)
    return copy


def _append_text(soup, container, text, known, kept):
    """Текст в узел; голые адреса становятся ссылками (иначе по ним не щёлкнуть)."""
    position = 0
    for match in _URL_RE.finditer(text):
        before = text[position:match.start()]
        if before:
            container.append(before)
        href = match.group(0).rstrip('.,;')
        found = next((item for item in known.values() if item[0] == href), None)
        if found:
            container.append(_anchor(soup, href, found[2], found[1]))
        else:
            container.append(_anchor(soup, href, href))
        kept.add(href)
        tail = match.group(0)[len(href):]
        if tail:
            container.append(tail)
        position = match.end()
    rest = text[position:]
    if rest:
        container.append(rest)


def _build(soup, container, text, known, kept):
    """Значение с выделением (<strong>, <em>, <br>…) — в узлы внутри container."""
    stack = [container]
    position = 0
    for match in _INLINE_RE.finditer(text):
        _append_text(soup, stack[-1], text[position:match.start()], known, kept)
        position = match.end()
        if match.group(2) is None:
            stack[-1].append(soup.new_tag('br'))
            continue
        name = match.group(2).lower()
        if match.group(1):
            for depth in range(len(stack) - 1, 0, -1):
                if stack[depth].name == name:
                    del stack[depth:]
                    break
            continue
        tag = soup.new_tag(name)
        stack[-1].append(tag)
        stack.append(tag)
    _append_text(soup, stack[-1], text[position:], known, kept)


def _relink(soup, cell, anchors, kept):
    """Прежние ссылки, ярлык которых остался в тексте, — обратно ссылками, на месте."""
    for href, label, attrs in anchors:
        if href in kept or not label:
            continue
        for node in list(cell.find_all(string=True)):
            if node.find_parent('a') is not None:
                continue
            raw = str(node)
            index = raw.lower().find(label.lower())
            if index == -1:
                continue
            anchor = _anchor(soup, href, raw[index:index + len(label)], attrs)
            before, after = raw[:index], raw[index + len(label):]
            node.replace_with(anchor)
            if before:
                anchor.insert_before(before)
            if after:
                anchor.insert_after(after)
            kept.add(href)
            break


def _fill_cell(soup, cell, value, anchors):
    """Записать значение клетки заново — внутри её прежних обёрток.

    Порядок: сначала адреса, которые модель написала прямо в тексте (в форме
    «ярлык (адрес)» или просто адресом), затем ярлыки прежних ссылок. Возвращает
    множество сохранённых адресов, чтобы вызывающий увидел потерянные.

    Выделение, которое модель написала сама (<strong>…</strong>), сильнее
    прежнего: тогда переносится только абзац клетки, а не её жирный.
    """
    text = _unlink(value, anchors)
    block, inline = _wrappers(cell)
    if _INLINE_RE.search(text):
        inline = []
    chain = [_clone(soup, tag) for tag in ([block] if block is not None else []) + inline]
    cell.clear()
    container = cell
    for tag in chain:
        container.append(tag)
        container = tag
    known = {label.lower(): (href, attrs, label) for href, label, attrs in anchors if label}
    kept = set()
    _build(soup, container, text, known, kept)
    _relink(soup, cell, anchors, kept)
    return kept


def _patch_in_place(cell, current, expected):
    """Поменять в клетке только изменившийся кусок. True — получилось.

    Так переживает правку всё, что в клетке стоит вокруг значения: жирное
    «Срок:», ссылка на условия, цвет. Кусок расширяется до целых слов, иначе
    правка «14 → 15» искала бы одинокую «4», а она бывает и в «40». Ссылки этим
    путём не трогаются никогда: переписанный ярлык ссылки — повод спросить
    человека (см. apply), а не молча сменить подпись.
    """
    # Регистр здесь — значение: «алматы → Алматы» тоже правка.
    if _norm(current) == _norm(expected):
        return True
    limit = min(len(current), len(expected))
    start = 0
    while start < limit and current[start] == expected[start]:
        start += 1
    end = 0
    while end < limit - start and current[-1 - end] == expected[-1 - end]:
        end += 1
    while start > 0 and not current[start - 1].isspace():
        start -= 1
    while end > 0 and not current[len(current) - end].isspace():
        end -= 1
    old = current[start:len(current) - end]
    new = expected[start:len(expected) - end]
    if not old.strip() or current.count(old) != 1:
        return False
    nodes = [node for node in cell.find_all(string=True)
             if node.find_parent('a') is None and old in str(node)]
    if len(nodes) != 1 or str(nodes[0]).count(old) != 1:
        return False
    node = nodes[0]
    before = str(node)
    replacement = NavigableString(before.replace(old, new, 1))
    node.replace_with(replacement)
    if _norm(_text(cell)) == _norm(expected):
        return True
    replacement.replace_with(NavigableString(before))
    return False


def _norm(value):
    return ' '.join(str(value or '').split())


def _squash(value):
    return _norm(value).lower()


def _matches(was, current):
    """Старое значение правки сходится с клеткой?

    Достаточно вхождения — модель обрезает длинные клетки. Пустая клетка
    сходится только с «пусто» (или прочерком, которым модели часто пишут
    пустоту): раньше пустая строка входила в любое значение, и правка с
    чужим старым значением ложилась в пустую клетку соседней строки.
    """
    w, c = _squash(was), _squash(current)
    if w in _EMPTY_WORDS:
        return not c
    if not c:
        return w in _DASHES
    return w in c or c in w


def _splice(current, was, now):
    """Новое значение клетки ЦЕЛИКОМ: обрезанное старое меняется внутри клетки."""
    w = _squash(was)
    if not current or w in _EMPTY_WORDS or w in _DASHES or _squash(current) in w:
        return now
    index = current.lower().find(' '.join(str(was).split()).lower())
    if index == -1:
        return now
    return current[:index] + now + current[index + len(' '.join(str(was).split())):]


def _cell_view(cell, max_cell):
    text = _text_with_links(cell)[:max_cell]
    if not text:
        text = '(картинка)' if cell.find(_MEDIA) is not None else '(пусто)'
    spans = []
    colspan = _span(cell.get('colspan'), _MAX_SPAN)
    rowspan = _span(cell.get('rowspan'), _MAX_SPAN)
    if colspan > 1:
        spans.append('на %d %s' % (colspan, _plural(colspan, 'колонку', 'колонки', 'колонок')))
    if rowspan > 1:
        spans.append('на %d %s' % (rowspan, _plural(rowspan, 'строку', 'строки', 'строк')))
    return text + (' [%s]' % ', '.join(spans) if spans else '')


def serialize(tables, *, max_rows=60, max_cell=160):
    """Таблицы в вид, по которому модель может назвать номера строк и клеток.

    Номера физические: строка — порядковый <tr> с единицы, клетка — порядковая
    клетка внутри строки. Объединённые клетки нарочно не разворачиваются, а
    подписываются («[на 2 колонки]»): модель видит ровно то, что потом увидит
    патчер, и рассинхронизации нет. Пустая клетка показана словом «(пусто)» —
    им же модель и называет её старое значение.
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
            body = ' | '.join('К%d=%s' % (position, _cell_view(cell, max_cell))
                              for position, cell in enumerate(cells, start=1))
            lines.append('  С%d%s: %s' % (row_index, head, body))
        if len(rows) > max_rows:
            lines.append('  … ещё %d строк' % (len(rows) - max_rows))
        out.append('\n'.join(lines))
    return '\n\n'.join(out)


def _clean_value(text):
    return _SPAN_NOTE.sub('', str(text or '')).strip()


def _values(text):
    return [_clean_value(part) for part in str(text or '').split('|')]


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
            now = _clean_value(cell.group(5))
            # «=> пусто» — очистить клетку. Значение «пусто» в неё не пишется.
            if _squash(now) in _EMPTY_WORDS:
                now = ''
            patches.append({'kind': 'cell', 'table': int(cell.group(1)),
                            'row': int(cell.group(2)), 'col': int(cell.group(3)),
                            'was': _clean_value(cell.group(4)), 'now': now})
            continue
        add = _ADD_RE.match(line)
        if add:
            patch = {'kind': 'add', 'table': int(add.group(1)),
                     'values': _values(add.group(3))}
            if add.group(2):
                patch['after'] = int(add.group(2))
            patches.append(patch)
            continue
        column = _COL_RE.match(line)
        if column:
            patch = {'kind': 'col', 'table': int(column.group(1)),
                     'values': _values(column.group(3))}
            if column.group(2):
                patch['after'] = int(column.group(2))
            patches.append(patch)
            continue
        drop = _DEL_RE.match(line)
        if drop:
            patches.append({'kind': 'ask_delete', 'table': int(drop.group(1)),
                            'row': int(drop.group(2)),
                            'reason': drop.group(3).strip()})
            continue
        row = _ROW_RE.match(line)
        # «Т1 С4: Алматы => Астана» — правка клетки без номера клетки. Вписать
        # её в строку как есть значило бы положить в клетку стрелку.
        if row and not re.search(r'=>|-->|->|→', row.group(3)):
            patches.append({'kind': 'row', 'table': int(row.group(1)),
                            'row': int(row.group(2)), 'values': _values(row.group(3))})
            continue
        if _LOOKS_LIKE_PATCH.match(line):
            patches.append({'kind': 'bad', 'line': line[:120]})
    return patches[:PATCH_LIMIT]


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


def _new_cell(soup, name, value, width=None):
    cell = soup.new_tag(name)
    if width:
        cell['colwidth'] = str(width)
    _fill_cell(soup, cell, value, [])
    return cell


def _px(style, name):
    match = re.search(r'(?:^|;)\s*%s\s*:\s*(\d+)px' % name, str(style or ''))
    return int(match.group(1)) if match else None


def _set_px(style, name, value):
    return re.sub(r'((?:^|;)\s*%s\s*:\s*)\d+px' % name,
                  lambda m: '%s%dpx' % (m.group(1), value), str(style or ''), count=1)


def _widen_layout(soup, table, position, width_px):
    """Колонка вставлена в позицию сетки — поправить <colgroup> и ширину таблицы.

    Редактор пересчитает их сам при открытии статьи, но витрина рисует ровно
    сохранённое: без своей <col> колонка зафиксированной таблицы получила бы
    остаток ширины — бывает, что ноль.
    """
    group = next((g for g in table.find_all('colgroup') if g.find_parent('table') is table), None)
    if group is not None:
        cols = group.find_all('col', recursive=False)
        col = soup.new_tag('col')
        col['style'] = 'width: %dpx' % width_px if width_px else 'min-width: 40px'
        if position < len(cols):
            cols[position].insert_before(col)
        else:
            group.append(col)
    style = table.get('style')
    for name in ('width', 'min-width'):
        current = _px(style, name)
        if current is not None:
            table['style'] = _set_px(style, name, current + (width_px or 40))
            break


def _add_column(soup, table, position, values, number):
    """Вставить колонку в позицию сетки. Возвращает вопросы человеку.

    Клетка, объединённая ЧЕРЕЗ место вставки (заголовок раздела на всю ширину,
    «Депозит» над двумя колонками), не разрезается, а расширяется на новую
    колонку. Клетка ставится после последней клетки строки, стоящей левее места
    вставки; позиции, занятые объединением сверху, браузер пропустит сам.
    """
    rows, entries, taken, width = _layout(table)
    widths = _column_widths(entries, width)
    known = [w for w in widths if w]
    new_width = int(round(sum(known) / len(known))) if known else None
    questions, widened = [], set()
    values = list(values)
    for r, row in enumerate(rows):
        value = values[r] if r < len(values) else ''
        row_entries = entries[r]
        spanning = None
        if 0 < position < width and taken.get((r, position - 1)) is taken.get((r, position)):
            spanning = taken.get((r, position - 1))
        elif (position >= width and len(row_entries) == 1
              and row_entries[0]['col'] == 0 and row_entries[0]['colspan'] >= width > 1):
            spanning = row_entries[0]
        if spanning is not None:
            if id(spanning) not in widened:
                widened.add(id(spanning))
                cell = spanning['cell']
                cell['colspan'] = str(spanning['colspan'] + 1)
                parts = str(cell.get('colwidth') or '').split(',')
                if new_width and all(p.strip().isdigit() for p in parts) and cell.get('colwidth'):
                    parts.insert(min(len(parts), position - spanning['col']), str(new_width))
                    cell['colwidth'] = ','.join(p.strip() for p in parts)
            if _plain(value):
                questions.append('Таблица %d, строка %d: клетка объединена через новую колонку — '
                                 'значение «%s» не вставлено, впишите его вручную'
                                 % (number, r + 1, _plain(value)[:60]))
            continue
        left = [e for e in row_entries if e['col'] < position]
        name = 'th' if row_entries and all(e['cell'].name == 'th' for e in row_entries) else 'td'
        cell = _new_cell(soup, name, value, new_width)
        if left:
            left[-1]['cell'].insert_after(cell)
        else:
            row.insert(0, cell)
    if len(values) > len(rows):
        questions.append('Для новой колонки таблицы %d ИИ прислал значений больше, чем в ней '
                         'строк: %s — не вставлено'
                         % (number, ', '.join('«%s»' % _plain(v)[:40] for v in values[len(rows):][:3])))
    _widen_layout(soup, table, position, new_width)
    return questions


def _insert_row(soup, table, after_row, values, number):
    """Вставить строку после after_row (узел). Возвращает (новая строка, вопросы).

    Объединение сверху, которое тянется ниже места вставки, удлиняется на
    новую строку: вставка внутрь объединённого блока не должна его разрезать.
    """
    rows, entries, taken, width = _layout(table)
    r = next(i for i, row in enumerate(rows) if row is after_row)
    crossing, seen = [], set()
    for c in range(width):
        entry = taken.get((r, c))
        if entry and id(entry) not in seen and entry['row'] + entry['rowspan'] - 1 > r:
            seen.add(id(entry))
            crossing.append(entry)
    covered = {c for e in crossing for c in range(e['col'], e['col'] + e['colspan'])}
    free = [c for c in range(width) if c not in covered]
    widths = _column_widths(entries, width)
    values = list(values)
    row = soup.new_tag('tr')
    for index, c in enumerate(free):
        row.append(_new_cell(soup, 'td', values[index] if index < len(values) else '', widths[c]))
    for entry in crossing:
        entry['cell']['rowspan'] = str(entry['rowspan'] + 1)
    after_row.insert_after(row)
    questions = []
    extra = [v for v in values[len(free):] if _plain(v)]
    if extra:
        questions.append('Для новой строки таблицы %d ИИ прислал значений больше, чем в ней '
                         'колонок: %s — не вставлено'
                         % (number, ', '.join('«%s»' % _plain(v)[:40] for v in extra[:3])))
    return row, questions


def _first_value(values):
    return next((_plain(v) for v in values if _plain(v)), '')


def apply(tables, patches):
    """Применить правки. Возвращает (таблицы, изменения, вопросы, отклонённые).

    Таблицы возвращаются НОВЫМ списком: исходные строки не мутируются, чтобы
    вызывающий мог сравнить до и после.
    """
    soups = [BeautifulSoup(str(html or ''), 'html.parser') for html in tables]
    changes, questions, rejected = [], [], []

    def table_of(number):
        if 1 <= number <= len(soups):
            return soups[number - 1].find('table')
        return None

    # Исходные строки и шапка каждой таблицы — номера в правках относятся к ним.
    originals = {}
    for number in range(1, len(soups) + 1):
        table = table_of(number)
        if table is not None:
            rows, entries, _taken, width = _layout(table)
            originals[number] = (rows, entries, width)

    columns, additions = [], []
    for order, patch in enumerate(patches):
        kind = patch.get('kind')
        if kind == 'bad':
            questions.append('ИИ прислал правку таблицы, которую не удалось разобрать: «%s» — '
                             'проверьте таблицы вручную' % patch.get('line', ''))
            continue
        number = patch.get('table', 0)
        table = table_of(number)
        if table is None:
            rejected.append('таблицы %s нет' % number)
            continue
        soup = soups[number - 1]
        rows = _rows_of(table)

        if kind == 'cell':
            if not 1 <= patch['row'] <= len(rows):
                rejected.append('в таблице %d нет строки %d' % (number, patch['row']))
                continue
            cells = _cells_of(rows[patch['row'] - 1])
            if not 1 <= patch['col'] <= len(cells):
                rejected.append('в таблице %d строке %d нет колонки %d'
                                % (number, patch['row'], patch['col']))
                continue
            cell = cells[patch['col'] - 1]
            current = _text(cell)
            anchors = _anchors_of(cell)
            was = ' '.join(_unlink(patch['was'], anchors).split())
            # Сверка со СТАРЫМ значением: защита от съехавшей нумерации.
            # Клетка с картинкой и без текста — не пустая, «пусто» с ней не сходится.
            if not _matches(was, current) or (not current and not _is_empty(cell)):
                questions.append(
                    'Правка таблицы %d, строка %d, колонка %d не применена: там '
                    '«%s», а ИИ ожидал «%s». Проверьте вручную.'
                    % (number, patch['row'], patch['col'],
                       current[:60] or 'пусто', patch['was'][:60]))
                continue
            now = ' '.join(_unlink(patch['now'], anchors).split())
            value = _splice(current, was, now)
            in_place = (not _INLINE_RE.search(value) and not _URL_RE.search(value)
                        and _patch_in_place(cell, current, value))
            if not in_place and cell.find(_MEDIA) is not None:
                # Клетку пришлось бы собрать заново, а картинку из неё модель не
                # видит и вернуть не может. Тихо стереть значок нельзя.
                questions.append(
                    'Правка таблицы %d, строка %d, колонка %d не применена: в клетке '
                    'есть картинка, и её пришлось бы удалить. Впишите «%s» вручную.'
                    % (number, patch['row'], patch['col'], _plain(now)[:60]))
                continue
            if not in_place:
                kept = _fill_cell(soup, cell, value, anchors)
                lost = [(href, label) for href, label, _a in anchors if href not in kept]
                for href, label in lost:
                    # Ссылку не бросаем: дописываем в конец клетки и спрашиваем.
                    # Пустая клетка вместо формы Google дороже неаккуратной.
                    cell.append(' ')
                    cell.append(_anchor(soup, href, label or href))
                    questions.append(
                        'В клетке (таблица %d, строка %d, колонка %d) была ссылка «%s» — '
                        'ИИ её не перенёс, она дописана в конец клетки. Проверьте место.'
                        % (number, patch['row'], patch['col'], label or href))
            after = _text(cell)
            if not current:
                changes.append('таблица %d, строка %d: заполнено «%s»'
                               % (number, patch['row'], after[:60]))
            elif not after:
                changes.append('таблица %d, строка %d: очищено «%s»'
                               % (number, patch['row'], current[:60]))
            elif _norm(after) != _norm(current):
                was_excerpt, now_excerpt = _diff_excerpt(current, after)
                changes.append('таблица %d, строка %d: «%s» → «%s»'
                               % (number, patch['row'], was_excerpt, now_excerpt))

        elif kind == 'row':
            if not 1 <= patch['row'] <= len(rows):
                rejected.append('в таблице %d нет строки %d' % (number, patch['row']))
                continue
            cells = _cells_of(rows[patch['row'] - 1])
            filled, extra = [], []
            for index, value in enumerate(patch['values']):
                plain = _plain(value)
                if not plain or _squash(plain) in _EMPTY_WORDS:
                    continue
                if index >= len(cells):
                    extra.append(plain)
                    continue
                cell = cells[index]
                current = _text(cell)
                if _is_empty(cell):
                    _fill_cell(soup, cell, value, [])
                    filled.append(plain)
                elif _squash(current) != _squash(plain):
                    questions.append(
                        'Таблица %d, строка %d, клетка %d: там уже «%s», а ИИ предлагал '
                        '«%s» — не заменено. Проверьте вручную.'
                        % (number, patch['row'], index + 1, current[:60] or 'картинка',
                           plain[:60]))
            if filled:
                changes.append('таблица %d, строка %d: заполнено %s'
                               % (number, patch['row'],
                                  ', '.join('«%s»' % v[:40] for v in filled[:6])))
            if extra:
                questions.append(
                    'Для строки %d таблицы %d ИИ прислал значений больше, чем в ней клеток: '
                    '%s — не вставлено' % (patch['row'], number,
                                           ', '.join('«%s»' % v[:40] for v in extra[:3])))

        elif kind == 'ask_delete':
            label = ''
            if 1 <= patch['row'] <= len(rows):
                cells = _cells_of(rows[patch['row'] - 1])
                label = _text(cells[0])[:60] if cells else ''
            questions.append(
                'Строку «%s» (таблица %d, строка %d) ИИ предлагает удалить: %s. '
                'Удалить её?' % (label, number, patch['row'],
                                 patch['reason'] or 'нет в новом документе'))

        elif kind == 'col':
            _rows, entries, width = originals[number]
            after = patch.get('after')
            if after is None:
                position = width
            elif entries and 1 <= after <= len(entries[0]):
                head = entries[0][after - 1]
                position = head['col'] + head['colspan']
            else:
                rejected.append('в шапке таблицы %d нет клетки %s' % (number, after))
                continue
            columns.append((position, order, number, patch))

        elif kind == 'add':
            after = patch.get('after')
            original_rows = originals[number][0]
            if after is not None and not 1 <= after <= len(original_rows):
                rejected.append('в таблице %d нет строки %d' % (number, after))
                continue
            additions.append((number, patch))

    # Колонки — справа налево: вставка левее не сдвигает те, что правее. Две
    # колонки в одно место — в обратном порядке правок, тогда первая окажется
    # левее, как модель их и перечислила.
    for position, _order, number, patch in sorted(columns, key=lambda item: (-item[0], -item[1])):
        table = table_of(number)
        questions.extend(_add_column(soups[number - 1], table, position,
                                     patch['values'], number))
        label = _first_value(patch['values'][:1]) or 'без подписи'
        changes.append('таблица %d: добавлена колонка «%s»' % (number, label[:60]))

    # Строки — после ИСХОДНОЙ строки, которую назвала модель. Несколько вставок
    # после одной строки встают друг за другом, в порядке правок.
    last_after = {}
    for number, patch in additions:
        table = table_of(number)
        after = patch.get('after')
        original_rows = originals[number][0]
        rows_now = _rows_of(table)
        if not rows_now:
            rejected.append('в таблице %d нет строк' % number)
            continue
        if after is None or after >= len(original_rows):
            reference = rows_now[-1]
        else:
            source = original_rows[after - 1]
            reference = last_after.get(id(source), source)
        row, asked = _insert_row(soups[number - 1], table, reference, patch['values'], number)
        questions.extend(asked)
        if after is not None and after < len(original_rows):
            last_after[id(original_rows[after - 1])] = row
            changes.append('таблица %d: после строки %d вставлена строка «%s»'
                           % (number, after, _first_value(patch['values'])[:60]))
        else:
            changes.append('таблица %d: добавлена строка «%s»'
                           % (number, _first_value(patch['values'])[:60]))

    return [str(soup) for soup in soups], changes, questions, rejected
