# -*- coding: utf-8 -*-
"""Нарезка статьи вики на куски для ИИ-помощника.

Резать приходится по HTML (полю content), а НЕ по content_plain: там каждый тег
заменён одним пробелом (wiki/sanitize.py:174), поэтому ни абзацев, ни заголовков
в нём нет — структурно резать нечего.

Границы кусков — h1/h2/h3. h4 в границы НЕ входит намеренно: в корпусе на
10.08.2026 их 260 при 144 h1, и 130 из них сидят в одной статье «Брендирование».
Слепой сплит по h1–h4 даёт 411 сегментов с медианой 165 символов — такие куски
бесполезны и модели, и человеку. Нарезка по h1–h3 со склейкой мелких секций даёт
около 180 кусков с медианой примерно 1 000 символов.

Мелкие секции склеиваются со следующими, а не выбрасываются: заголовок сам по
себе несёт смысл («Залог», «Ограничения»), и он остаётся строкой внутри куска.

Таблицы разворачиваются в строки «поле: значение» ДО снятия тегов (см.
wiki/ai/tables.py) и рвутся только по границе строки таблицы.
"""

import re

from bs4 import BeautifulSoup

from .tables import serialize_table

# Целевой и предельный размер куска в символах. Цель ~1 100: на замерах вопросов
# операторов кусок такого размера содержит законченную мысль и оставляет запас
# на 5-8 кусков в контексте при минутном лимите провайдера.
TARGET_CHARS = 1100
HARD_CAP_CHARS = 2000
MIN_CHARS = 200

_HEADING_LEVELS = {'h1': 1, 'h2': 2, 'h3': 3, 'h4': 4}
_BOUNDARY_LEVELS = (1, 2, 3)
_CONTAINERS = {'div', 'section', 'article', 'main', 'details', 'body', 'html'}
_SKIP = {'script', 'style', 'img', 'svg', 'figure', 'br', 'hr', 'input'}
_ACK_TRUE = re.compile(r'^(true|1)$', re.I)


def _is_ack_required(tag):
    value = tag.get('data-required-for-ack')
    if value is None:
        return False
    if value is True:                       # булев атрибут без значения
        return True
    return bool(_ACK_TRUE.match(str(value).strip()))


def _clean(text):
    return ' '.join(str(text or '').split())


# Схемы, у которых адрес имеет смысл вне интерфейса портала. Относительные
# ссылки (/api/wiki/file/...) сюда НЕ входят: ответ помощника открывается с
# домена GitHub Pages, и такой адрес там указывал бы в никуда.
_USEFUL_SCHEME = ('http://', 'https://', 'mailto:', 'tel:')


def _inline_links(soup):
    """Вписать адрес ссылки в текст: «ярлык (адрес)».

    Без этого адрес до помощника не доходит ВООБЩЕ: куски строятся из текста, а
    get_text() выбрасывает href. В корпусе 210 ссылок в 21 статье, и все они для
    помощника выглядели как обычные слова — отсюда «прямой ссылки в статьях нет»
    на вопрос, ответ на который в статье есть.

    Адрес дописывается только когда он полезен получателю: пустые и «#» ссылки
    пропускаются (в статье про акции такая ровно одна, и она именно «#»), как и
    внутренние относительные адреса.
    """
    for anchor in soup.find_all('a'):
        href = str(anchor.get('href') or '').strip()
        if not href.lower().startswith(_USEFUL_SCHEME):
            continue
        label = ' '.join(anchor.get_text(' ', strip=True).split())
        # У mailto/tel ярлык обычно и есть сам адрес — сравниваем без схемы,
        # иначе выходит «help@itaxi.kz (mailto:help@itaxi.kz)».
        bare = href.split(':', 1)[1] if href.lower().startswith(('mailto:', 'tel:')) else href
        if href in label or bare in label:
            continue
        anchor.string = f'{label} ({href})' if label else href


def parse_blocks(html):
    """HTML → плоская последовательность блоков в порядке документа.

    Блок — словарь: kind ('heading' | 'text'), level (для заголовков),
    text, ack (лежит ли блок внутри обязательного к ознакомлению).
    Отдельный проход, чтобы нарезку можно было проверить без разбора HTML.
    """
    soup = BeautifulSoup(str(html or ''), 'html.parser')
    _inline_links(soup)
    blocks = []

    def walk(node, ack):
        for child in node.children:
            name = getattr(child, 'name', None)
            if name is None:                        # текстовый узел
                text = _clean(child)
                if text:
                    blocks.append({'kind': 'text', 'level': 0,
                                   'text': text, 'ack': ack})
                continue
            if name in _SKIP:
                continue

            child_ack = ack or _is_ack_required(child)

            if name in _HEADING_LEVELS:
                text = _clean(child.get_text(' ', strip=True))
                if text:
                    blocks.append({'kind': 'heading',
                                   'level': _HEADING_LEVELS[name],
                                   'text': text, 'ack': child_ack})
                continue

            if name == 'table':
                for line in serialize_table(child):
                    blocks.append({'kind': 'text', 'level': 0,
                                   'text': line, 'ack': child_ack})
                continue

            if name == 'summary':
                # Заголовок раскрывашки: в путь заголовков попадает, но границей
                # куска не служит — иначе 35 раскрывашек нарубили бы огрызков.
                text = _clean(child.get_text(' ', strip=True))
                if text:
                    blocks.append({'kind': 'heading', 'level': 4,
                                   'text': text, 'ack': child_ack})
                continue

            if name == 'details' and child.find('summary') is None:
                # Разметка редактора держит заголовок раскрывашки в data-title и
                # не всегда рисует <summary>. Без этой ветки такой блок попадал
                # бы в индекс без своего названия.
                title = _clean(child.get('data-title'))
                if title:
                    blocks.append({'kind': 'heading', 'level': 4,
                                   'text': title, 'ack': child_ack})
                walk(child, child_ack)
                continue

            if name in _CONTAINERS:
                walk(child, child_ack)
                continue

            text = _clean(child.get_text(' ', strip=True))
            if text:
                blocks.append({'kind': 'text', 'level': 0,
                               'text': text, 'ack': child_ack})

    walk(soup, False)
    return blocks


def _split_long(line):
    """Строка длиннее предела — режем по границам слов, а не по символам."""
    words = line.split(' ')
    out, current = [], ''
    for word in words:
        if current and len(current) + 1 + len(word) > HARD_CAP_CHARS:
            out.append(current)
            current = word
        else:
            current = f'{current} {word}'.strip()
    if current:
        out.append(current)
    return out


def _dedupe(lines):
    """Убрать повторы подряд идущих одинаковых строк.

    Разметка раскрывашек держит название и в data-title, и внутри блока, поэтому
    заголовок приходит дважды. Для модели повтор не несёт ничего, а токены в
    контексте — самый дефицитный ресурс (лимит провайдера минутный).
    """
    out = []
    for line in lines:
        if not out or _clean(out[-1]).lower() != _clean(line).lower():
            out.append(line)
    return out


def _emit(lines, path, ack, chunks):
    """Накопленные строки → один или несколько кусков."""
    pieces, current, size = [], [], 0
    for line in _dedupe(lines):
        for part in ([line] if len(line) <= HARD_CAP_CHARS else _split_long(line)):
            if current and size + len(part) + 1 > HARD_CAP_CHARS:
                pieces.append(current)
                current, size = [], 0
            current.append(part)
            size += len(part) + 1
            if size >= TARGET_CHARS:
                pieces.append(current)
                current, size = [], 0
    if current:
        pieces.append(current)

    for piece in pieces:
        text = '\n'.join(piece).strip()
        if text:
            chunks.append({'heading_path': path, 'text': text,
                           'requires_ack': ack})


def chunk_article(html, fallback_text=None):
    """Статью — в список кусков: heading_path, text, requires_ack.

    chunk_idx не проставляется здесь: он нумеруется при записи в базу, чтобы
    нарезка оставалась чистой функцией и её можно было гонять в тестах.

    fallback_text — content_plain статьи. Нужен для статей-инструментов, у которых
    тело пустое, а текст для поиска засеян отдельно: «Классификатор авто» (id 36)
    имеет content длиной 0 и content_plain на 205 символов, и без этой ветки
    выпадала бы из индекса целиком. Она же единственная статья на проде, под
    которую заведены правила доступа, — потеря была бы незаметной и обидной.
    """
    blocks = parse_blocks(html)
    if not blocks and _clean(fallback_text):
        blocks = [{'kind': 'text', 'level': 0,
                   'text': _clean(fallback_text), 'ack': False}]
    chunks = []
    stack = []                  # [(level, text)] — текущий путь заголовков
    lines, size, ack = [], 0, False
    path = None                 # None = содержимое куска ещё не началось

    def path_now():
        return ' > '.join(text for _level, text in stack)

    for block in blocks:
        if block['kind'] == 'heading':
            level = block['level']
            # Границей служат только h1-h3, и только если накоплено достаточно:
            # иначе секция из одного заголовка уехала бы отдельным куском.
            if level in _BOUNDARY_LEVELS and size >= MIN_CHARS:
                _emit(lines, path or path_now(), ack, chunks)
                lines, size, ack, path = [], 0, False, None
            stack = [item for item in stack if item[0] < level]
            stack.append((level, block['text']))
            lines.append(block['text'])
            size += len(block['text']) + 1
            continue

        # Путь фиксируется по первому СОДЕРЖАТЕЛЬНОМУ блоку, а не по первой
        # строке буфера: буфер начинается с самого заголовка, и если брать путь
        # там, вложенные h2/h3 в него не попадут — кусок под «Аренда > Условия >
        # Залог» получил бы путь «Аренда» и потерял свой единственный контекст.
        if path is None:
            path = path_now()
        lines.append(block['text'])
        size += len(block['text']) + 1
        ack = ack or block['ack']
        if size >= HARD_CAP_CHARS:
            _emit(lines, path, ack, chunks)
            lines, size, ack, path = [], 0, False, None

    _emit(lines, path or path_now(), ack, chunks)
    return chunks
