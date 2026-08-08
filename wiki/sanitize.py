"""Серверная санитизация HTML статей.

Зачем она вообще. В OTP серверной санитизации нет нигде: DOMPurify работает
только на клиенте, то есть защищает того, кто отправляет, а не того, кто потом
читает. В исходной вике sanitize-html применялся исключительно к импорту по
URL, а содержимое из редактора уходило в базу как есть. Для общего портала с
1000+ сотрудников это готовый stored-XSS: достаточно одного человека с правом
править статью.

Списки не выдуманы, а посчитаны по дампу прод-базы вики (46 статей):
  теги      — 26 реально встречающихся + технически необходимые редактору;
  атрибуты  — style (2756 раз), colspan/rowspan (по 1913), colwidth (1198),
              class (328), data-color (251), href/target/rel (по 192),
              шесть data-* раскрывающихся блоков (по 35), src (21);
  стили     — font-size (1822), color (1359), font-family (978),
              background-color (251), text-align (234), width (189),
              min-width (133).

Поэтому style НЕ вырезается: без него контент визуально разваливается. Вместо
запрета — белый список CSS-свойств. Исполняемого кода из CSS в современных
браузерах не получить, а свойства вроде position/z-index/transform убраны,
чтобы правкой статьи нельзя было закрыть собой интерфейс портала.
"""

import re

try:
    import nh3
except ImportError:  # pragma: no cover — окружение без зависимости
    nh3 = None

# Теги: всё, что встречается в контенте, плюс то, без чего редактор не сможет
# сохранить обычную статью (code/pre/hr/подстрочные знаки/секции таблицы).
ALLOWED_TAGS = {
    'p', 'span', 'div', 'br', 'hr',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'strong', 'b', 'em', 'i', 'u', 's', 'strike', 'mark', 'sub', 'sup', 'small',
    'ul', 'ol', 'li', 'blockquote', 'code', 'pre', 'figure', 'figcaption',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'colgroup', 'col', 'caption',
    'a', 'img',
    'details', 'summary',
}

# Атрибуты, общие для всех тегов.
_COMMON_ATTRS = {'class', 'style', 'id', 'title', 'lang', 'dir'}

# Кастомные узлы редактора. Без них 35 раскрывающихся блоков в контенте
# превратятся в простые абзацы, а 251 выделение потеряет цвет.
_WIKI_DATA_ATTRS = {
    'data-wiki-collapsible', 'data-wiki-collapsible-group',
    'data-title', 'data-default-open', 'data-allow-multiple',
    'data-required-for-ack', 'data-id', 'data-icon', 'data-size',
    'data-layout', 'data-color',
}

ALLOWED_ATTRIBUTES = {tag: set(_COMMON_ATTRS) for tag in ALLOWED_TAGS}
# rel сознательно НЕ в списке: nh3 управляет им сам через link_rel и
# отказывается работать, если атрибут разрешён явно.
ALLOWED_ATTRIBUTES['a'] |= {'href', 'target'}
ALLOWED_ATTRIBUTES['img'] |= {'src', 'alt', 'width', 'height', 'loading'}
ALLOWED_ATTRIBUTES['td'] |= {'colspan', 'rowspan', 'colwidth'}
ALLOWED_ATTRIBUTES['th'] |= {'colspan', 'rowspan', 'colwidth'}
ALLOWED_ATTRIBUTES['col'] |= {'span', 'width'}
ALLOWED_ATTRIBUTES['details'] |= {'open'} | _WIKI_DATA_ATTRS
ALLOWED_ATTRIBUTES['summary'] |= _WIKI_DATA_ATTRS
ALLOWED_ATTRIBUTES['div'] |= _WIKI_DATA_ATTRS
ALLOWED_ATTRIBUTES['span'] |= _WIKI_DATA_ATTRS
ALLOWED_ATTRIBUTES['mark'] |= _WIKI_DATA_ATTRS

# Схемы ссылок. data: разрешена только для картинок — в дампе так пришли четыре
# base64-изображения. В href схема data: — известный вектор (data:text/html),
# поэтому она отсекается отдельно, ниже в фильтре атрибутов.
ALLOWED_SCHEMES = {'http', 'https', 'mailto', 'tel', 'data'}

# CSS-свойства, которые сохраняем. Всё остальное отбрасывается молча.
ALLOWED_CSS = {
    'font-size', 'font-family', 'font-weight', 'font-style',
    'color', 'background-color', 'text-align', 'text-decoration',
    'width', 'min-width', 'max-width', 'height', 'vertical-align',
    'padding', 'padding-left', 'padding-right', 'padding-top', 'padding-bottom',
    'margin-left', 'margin-right',
    'border', 'border-color', 'border-width', 'border-style',
    'line-height', 'white-space',
}

# Значения, которые не должны попасть в CSS ни при каких обстоятельствах.
_CSS_FORBIDDEN = re.compile(
    r'(expression\s*\(|javascript\s*:|behavior\s*:|-moz-binding|@import|url\s*\()',
    re.I,
)


def _clean_style(value):
    """Оставляет только разрешённые CSS-свойства."""
    kept = []
    for declaration in str(value or '').split(';'):
        if ':' not in declaration:
            continue
        prop, _, val = declaration.partition(':')
        prop = prop.strip().lower()
        val = val.strip()
        if prop not in ALLOWED_CSS or not val:
            continue
        if _CSS_FORBIDDEN.search(val):
            continue
        kept.append('%s: %s' % (prop, val))
    return '; '.join(kept)


def _attribute_filter(tag, attribute, value):
    """Вызывается nh3 на каждый атрибут. None — выбросить."""
    if attribute == 'style':
        cleaned = _clean_style(value)
        return cleaned or None

    if attribute == 'href':
        # data: в ссылке — это data:text/html, то есть исполняемая страница.
        # В картинке та же схема безвредна, поэтому запрет точечный.
        if str(value or '').strip().lower().startswith('data:'):
            return None

    if attribute == 'src' and tag == 'img':
        src = str(value or '').strip().lower()
        if src.startswith('data:') and not src.startswith('data:image/'):
            return None

    if attribute == 'class':
        # Классы оставляем (в контенте их 328), но не даём вырваться из потока
        # статьи и накрыть собой интерфейс портала.
        classes = [c for c in str(value or '').split()
                   if not re.match(r'^(fixed|absolute|sticky|z-\[?\d)', c)]
        return ' '.join(classes) or None

    return value


def sanitize_html(html):
    """Очищает HTML статьи. Пустой ввод возвращает пустую строку.

    Если nh3 недоступен (окружение без зависимости), содержимое НЕ пропускается
    как есть: молча сохранить неочищенный HTML хуже, чем отказать.
    """
    if not html:
        return ''
    if nh3 is None:
        raise RuntimeError(
            'Не установлен nh3 — сохранять HTML без санитизации нельзя. '
            'Добавьте nh3 в requirements.txt.'
        )
    return nh3.clean(
        str(html),
        tags=ALLOWED_TAGS,
        attributes={tag: set(attrs) for tag, attrs in ALLOWED_ATTRIBUTES.items()},
        url_schemes=ALLOWED_SCHEMES,
        attribute_filter=_attribute_filter,
        link_rel='noopener noreferrer',
        strip_comments=True,
    )


_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')


def to_plain_text(html, limit=None):
    """Текст без разметки — для поиска и для превью.

    Заодно выбрасывает base64-картинки: в проде вики они дают 81 % объёма
    контента, и тащить их в поисковый индекс бессмысленно.
    """
    if not html:
        return ''
    text = re.sub(r'src\s*=\s*"data:[^"]*"', '', str(html))
    text = _TAG_RE.sub(' ', text)
    text = (text.replace('&nbsp;', ' ').replace('&amp;', '&')
                .replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"'))
    text = _WS_RE.sub(' ', text).strip()
    return text[:limit] if limit else text
