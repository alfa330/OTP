# -*- coding: utf-8 -*-
"""Статья вики файлом Word (.docx).

ЗАЧЕМ. Решение владельца 08.10.2026: статьи вики нужно уметь скачивать, «в
ворд формате», и право на это — у администраторов и выше (wiki/access.py:
may_export). Файл уходит из портала вместе с человеком — в письмо, в печать,
подрядчику, — поэтому он обязан быть ЧИТАЕМЫМ САМ ПО СЕБЕ: без вики под рукой,
без картинок по ссылке, без «см. на экране».

ЧТО ЭТО. Тело статьи хранится HTML'ом, прошедшим серверный санитайзер
(wiki/sanitize.py). Белый список санитайзера и есть полный перечень того, что
может встретиться в теле, — ровно по нему здесь и расписаны правила перевода:

  * заголовки h1–h6 → стили «Заголовок 1–6» (у статьи свой заголовок стилем
    «Название», поэтому уровни не сдвигаются);
  * списки ul/ol → «Маркированный список»/«Нумерованный список» трёх уровней;
    у КАЖДОГО ol своя нумерация с нужного start — иначе Word продолжил бы
    счёт сквозь всю статью (это штатное поведение стиля «List Number»);
  * таблицы → таблица со сеткой, объединённые ячейки (colspan/rowspan)
    объединяются и в Word, ширины колонок берутся из colwidth редактора;
  * картинки → вшиваются в файл. Файлы бакета лежат в WebP (wiki/images.py),
    а Word WebP не ест — кадр переводится в PNG (с прозрачностью) или JPEG
    (без неё) и ужимается до 1600 px по длинной стороне: это документ, а не
    архив. Картинки base64 из старой вики вшиваются как есть;
  * оформительские блоки (wiki-blocks.css): плашка → одноклеточная таблица с
    фоном и левой гранью тона; карточки и показатели → таблица в N колонок;
    галерея → полоса кадров до трёх в ряд; вводка → абзацы покрупнее и серее;
  * раскрывающиеся блоки <details> и <div data-wiki-collapsible> → заголовок
    жирным и содержимое следом: в бумажном документе раскрывать нечего;
  * кнопка тренажёра → одна строка курсивом со ссылкой на статью: сам
    тренажёр в Word не унести, а молча выбросить его — потерять то, что автор
    поставил намеренно;
  * ссылки → гиперссылки. Внутренняя ссылка на статью (?view=wiki&article=…)
    достраивается до адреса портала, который присылает интерфейс: сервер
    своего адреса не знает, а фронт живёт на GitHub Pages с базовым путём.

ЧЕГО ЗДЕСЬ НЕТ — КАРТИНОК ПО ЧУЖИМ АДРЕСАМ. Файл бакета достаётся через
fetch_image, который даёт вызывающий (routes_articles) и который проверяет
доступ тем же правилом, что ручка /file/<id>. Картинку по внешнему http-адресу
сервер НЕ скачивает: адрес пишет автор статьи, и заставить сервер ходить по
произвольным адресам из текста — классический SSRF. Вместо кадра в документе
остаётся ссылка на него.

Модуль чистый: ни базы, ни Flask. Все зависимости (python-docx, bs4, Pillow)
подключены так, чтобы их отсутствие не уронило импорт пакета вики — проверка
available() стоит в роуте, который на отказ отвечает 503 словами.
"""

import base64
import io
import logging
import re
from datetime import datetime
from urllib.parse import quote

from .file_urls import FILE_REF

try:
    from bs4 import BeautifulSoup, NavigableString, Tag
except ImportError:  # pragma: no cover — окружение без зависимости
    BeautifulSoup = NavigableString = Tag = None

try:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Emu, Pt, RGBColor
except ImportError:  # pragma: no cover — окружение без зависимости
    Document = None

logger = logging.getLogger(__name__)

DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

# Лист A4 с полями по 2 см — так печатают документы в компании. Ширина колонки
# текста = 17 см; от неё считаются проценты ширины картинок и колонки таблиц.
PAGE_WIDTH_CM = 21.0
PAGE_HEIGHT_CM = 29.7
MARGIN_CM = 2.0

# Кадр длиннее этого по длинной стороне ужимается: экран статьи — колонка около
# 760 px, печать A4 — 17 см, то есть 1600 px это уже ~240 dpi. Больше — только
# вес файла.
MAX_IMAGE_SIDE = 1600
# Выше этого картинку по высоте не рисуем: страница A4 минус поля — 25,7 см, и
# кадр во всю высоту листа с подписью не влезает. Ширина при этом ужимается
# пропорционально.
MAX_IMAGE_HEIGHT_CM = 22.0
# Предохранитель памяти, как MAX_PIXELS в wiki/images.py: кадр больше этого в
# документ не идёт, вместо него — пометка.
MAX_IMAGE_PIXELS = 40 * 1000 * 1000
# Больше этого байт картинка в документ не вшивается даже после пережатия.
MAX_IMAGE_BYTES = 25 * 1024 * 1024

# 1 px CSS = 1/96 дюйма = 9525 EMU; в пунктах — 0,75 pt.
EMU_PER_PX = 9525
PT_PER_PX = 0.75

BODY_FONT = 'Calibri'
BODY_SIZE_PT = 11
MONO_FONT = 'Consolas'
LINK_COLOR = '0563C1'
MUTED_COLOR = '64748B'      # slate-500: подписи и служебные строки
QUOTE_COLOR = '475569'      # slate-600: цитаты и вводка
RULE_COLOR = 'CBD5E1'       # slate-300: линии
HEADER_FILL = 'F1F5F9'      # slate-100: шапка таблицы, фон кода

# Тона оформительских блоков — те же, что в wiki-blocks.css: (фон, грань, чернила).
TONES = {
    'info': ('EEF2FF', 'C7D2FE', '4338CA'),
    'ok': ('ECFDF5', 'A7F3D0', '047857'),
    'warn': ('FFFBEB', 'FDE68A', 'B45309'),
    'danger': ('FEF2F2', 'FECACA', 'B91C1C'),
    'tip': ('F5F3FF', 'DDD6FE', '6D28D9'),
    'neutral': ('F8FAFC', 'E2E8F0', '475569'),
    'dark': ('0F172A', '1E293B', 'A5B4FC'),
}
DEFAULT_TONE = 'info'
# На тёмной плашке обычный текст — светлый, иначе он не читается.
DARK_TEXT = 'E2E8F0'

_MONTHS = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
           'августа', 'сентября', 'октября', 'ноября', 'декабря')

STATUS_WORDS = {'draft': 'Черновик', 'archived': 'В архиве'}

_INLINE_TAGS = frozenset({
    'span', 'a', 'strong', 'b', 'em', 'i', 'u', 's', 'strike', 'mark', 'sub', 'sup',
    'small', 'code', 'br', 'img',
})
_HEADINGS = {'h1': 1, 'h2': 2, 'h3': 3, 'h4': 4, 'h5': 5, 'h6': 6}

_WS_RE = re.compile(r'[ \t\r\n\f]+')
_DATA_URI_RE = re.compile(r'^data:(image/[a-z0-9.+-]+)?(;[^,]*)?,(.*)$', re.I | re.S)
_ARTICLE_HREF_RE = re.compile(r'^\?view=wiki&article=([^&#]+)', re.I)
_ABS_HREF_RE = re.compile(r'^(https?://|mailto:|tel:)', re.I)
_PORTAL_RE = re.compile(r'^https?://[^\s?#]+$', re.I)
_HEX_RE = re.compile(r'^#?([0-9a-f]{3}|[0-9a-f]{6})$', re.I)
_RGB_RE = re.compile(r'^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', re.I)
_NAMED_COLORS = {
    'black': '000000', 'white': 'FFFFFF', 'red': 'DC2626', 'green': '16A34A',
    'blue': '2563EB', 'gray': '6B7280', 'grey': '6B7280', 'orange': 'EA580C',
    'yellow': 'FACC15', 'purple': '7C3AED', 'navy': '1E3A8A', 'teal': '0D9488',
}
_FONT_ALIASES = {
    'monospace': MONO_FONT, 'courier': 'Courier New', 'serif': 'Times New Roman',
    'sans-serif': None, 'system-ui': None, 'inherit': None,
}
# Запрещённые в имени файла знаки (Windows строже всех) плюс управляющие.
_FILENAME_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
FILENAME_MAX = 100


def available():
    """Собраны ли зависимости выгрузки. Роут на False отвечает 503 словами."""
    return Document is not None and BeautifulSoup is not None


def file_name(title):
    """Имя файла из названия статьи: кириллица остаётся, служебные знаки — нет.

    Имя уезжает в Content-Disposition и на диск человеку; знаки, запрещённые
    в именах Windows, заменяются пробелом, хвостовые точки снимаются (Windows
    их отбрасывает молча, и файл «Статья...docx» превратился бы в «Статья»).
    Потолок — 100 знаков: длиннее названия и так не читаются в проводнике.
    Та же формула на клиенте — src/components/wiki/articleDownload.js.
    """
    name = _FILENAME_BAD.sub(' ', str(title or ''))
    name = _WS_RE.sub(' ', name).strip().rstrip('.').strip()
    name = name[:FILENAME_MAX].rstrip(' .') or 'Статья'
    return name + '.docx'


def portal_address(value):
    """Адрес портала из запроса — только http(s), без строки запроса и якоря.

    Нужен единственно для внутренних ссылок на статьи: фронт живёт на GitHub
    Pages с базовым путём, и сервер этого адреса не знает. Чужое значение
    отбрасывается — тогда ссылки на статьи остаются текстом, документ при
    этом собирается.
    """
    candidate = str(value or '').strip()
    if len(candidate) > 300 or not _PORTAL_RE.match(candidate):
        return ''
    return candidate


def date_words(value):
    """«8 октября 2026» из datetime или ISO-строки; пусто — если дата не разобралась."""
    moment = value
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.strip()[:19])
        except ValueError:
            return ''
    if not isinstance(moment, datetime):
        try:
            return '%d %s %d' % (moment.day, _MONTHS[moment.month - 1], moment.year)
        except (AttributeError, IndexError, TypeError):
            return ''
    return '%d %s %d' % (moment.day, _MONTHS[moment.month - 1], moment.year)


def build_document(article, *, section_paths=(), portal_url='', fetch_image=None,
                   exported_at=None):
    """Статья → (BytesIO с .docx, сводка).

    article        — словарь из wiki.articles.get_article (title, summary, content,
                     status, author_name, updated_at, slug).
    section_paths  — [[«Пространство», «Раздел», «Подраздел»], …] — где лежит.
    portal_url     — адрес страницы портала для внутренних ссылок ('' — не ставить).
    fetch_image    — (file_id) -> (bytes, content_type) | None для картинок бакета.
    exported_at    — момент выгрузки (datetime); None — в документ не пишется.

    Сводка: {'images': вшито, 'missing': не достали, 'external': чужие адреса}.
    """
    if not available():
        raise RuntimeError('Выгрузка в Word недоступна: нужны python-docx и beautifulsoup4')
    builder = _Builder(fetch_image=fetch_image, portal_url=portal_url)
    document = builder.build(article, section_paths=section_paths, exported_at=exported_at)
    stream = io.BytesIO()
    document.save(stream)
    stream.seek(0)
    return stream, dict(builder.stats)


# ── Разбор стилей и цветов ──────────────────────────────────────────────────

def _style_map(node):
    """{свойство: значение} из атрибута style. Пустой словарь — если стиля нет."""
    out = {}
    for declaration in str(node.get('style') or '').split(';'):
        if ':' not in declaration:
            continue
        prop, _, value = declaration.partition(':')
        prop, value = prop.strip().lower(), value.strip()
        if prop and value:
            out[prop] = value
    return out


def _hex_color(value):
    """CSS-цвет → 'RRGGBB' или None. Прозрачный и неразобранный — None."""
    text = str(value or '').strip().lower()
    if not text or text in ('transparent', 'inherit', 'initial', 'none'):
        return None
    match = _HEX_RE.match(text)
    if match:
        digits = match.group(1)
        if len(digits) == 3:
            digits = ''.join(ch * 2 for ch in digits)
        return digits.upper()
    match = _RGB_RE.match(text)
    if match:
        parts = [min(255, int(part)) for part in match.groups()]
        return '%02X%02X%02X' % tuple(parts)
    return _NAMED_COLORS.get(text)


def _font_size_pt(value):
    """font-size из CSS → пункты; None — если разобрать нельзя. Границы 6…40."""
    text = str(value or '').strip().lower()
    match = re.match(r'^([\d.]+)\s*(px|pt|em|rem|%)?$', text)
    if not match:
        return None
    try:
        number = float(match.group(1))
    except ValueError:
        return None
    unit = match.group(2) or 'px'
    if unit == 'px':
        points = number * PT_PER_PX
    elif unit == 'pt':
        points = number
    elif unit in ('em', 'rem'):
        points = number * BODY_SIZE_PT
    else:
        points = number * BODY_SIZE_PT / 100.0
    if points <= 0:
        return None
    return round(min(40.0, max(6.0, points)), 1)


def _font_name(value):
    """Первое семейство из font-family; обобщённые имена — по карте, 'sans-serif' — None."""
    first = str(value or '').split(',')[0].strip().strip('\'"').strip()
    if not first:
        return None
    key = first.lower()
    if key in _FONT_ALIASES:
        return _FONT_ALIASES[key]
    return first[:48]


def _alignment(value):
    text = str(value or '').strip().lower()
    if text == 'center':
        return WD_ALIGN_PARAGRAPH.CENTER
    if text == 'right':
        return WD_ALIGN_PARAGRAPH.RIGHT
    if text == 'justify':
        return WD_ALIGN_PARAGRAPH.JUSTIFY
    if text == 'left':
        return WD_ALIGN_PARAGRAPH.LEFT
    return None


def _int_attr(node, name, default=1):
    try:
        return max(1, int(str(node.get(name) or '').strip()))
    except ValueError:
        return default


def _tone_of(node):
    tone = str(node.get('data-tone') or '').strip().lower()
    return tone if tone in TONES else DEFAULT_TONE


def _plain_text(node):
    """Текст узла одной строкой — для подписей и имён."""
    return _WS_RE.sub(' ', node.get_text(' ') if isinstance(node, Tag) else str(node)).strip()


# ── Низкоуровневые правки OOXML ─────────────────────────────────────────────

def _shade_cell(cell, fill):
    tcPr = cell._tc.get_or_add_tcPr()
    for old in tcPr.findall(qn('w:shd')):
        tcPr.remove(old)
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), fill)
    tcPr.insert_element_before(
        shd, 'w:noWrap', 'w:tcMar', 'w:textDirection', 'w:tcFitText', 'w:vAlign', 'w:hideMark')


def _cell_borders(cell, **sides):
    """Границы ячейки: side=(вид, толщина в 1/8 pt, цвет). 'nil' — убрать."""
    tcPr = cell._tc.get_or_add_tcPr()
    borders = tcPr.find(qn('w:tcBorders'))
    if borders is None:
        borders = OxmlElement('w:tcBorders')
        tcPr.insert_element_before(
            borders, 'w:shd', 'w:noWrap', 'w:tcMar', 'w:textDirection', 'w:tcFitText',
            'w:vAlign', 'w:hideMark')
    for side in ('top', 'left', 'bottom', 'right'):
        if side not in sides:
            continue
        for old in borders.findall(qn('w:' + side)):
            borders.remove(old)
        value, size, color = sides[side]
        element = OxmlElement('w:' + side)
        element.set(qn('w:val'), value)
        element.set(qn('w:sz'), str(size))
        element.set(qn('w:space'), '0')
        element.set(qn('w:color'), color)
        borders.append(element)


def _paragraph_border(paragraph, side, size, color):
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = pPr.find(qn('w:pBdr'))
    if pBdr is None:
        pBdr = OxmlElement('w:pBdr')
        pPr.insert_element_before(
            pBdr, 'w:shd', 'w:tabs', 'w:suppressAutoHyphens', 'w:kinsoku', 'w:wordWrap',
            'w:overflowPunct', 'w:topLinePunct', 'w:autoSpaceDE', 'w:autoSpaceDN', 'w:bidi',
            'w:adjustRightInd', 'w:snapToGrid', 'w:spacing', 'w:ind', 'w:contextualSpacing',
            'w:mirrorIndents', 'w:suppressOverlap', 'w:jc', 'w:textDirection',
            'w:textAlignment', 'w:textboxTightWrap', 'w:outlineLvl', 'w:divId', 'w:cnfStyle',
            'w:rPr', 'w:sectPr', 'w:pPrChange')
    element = OxmlElement('w:' + side)
    element.set(qn('w:val'), 'single')
    element.set(qn('w:sz'), str(size))
    element.set(qn('w:space'), '4')
    element.set(qn('w:color'), color)
    pBdr.append(element)


def _paragraph_shading(paragraph, fill):
    pPr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), fill)
    pPr.insert_element_before(
        shd, 'w:tabs', 'w:suppressAutoHyphens', 'w:kinsoku', 'w:wordWrap', 'w:overflowPunct',
        'w:topLinePunct', 'w:autoSpaceDE', 'w:autoSpaceDN', 'w:bidi', 'w:adjustRightInd',
        'w:snapToGrid', 'w:spacing', 'w:ind', 'w:contextualSpacing', 'w:mirrorIndents',
        'w:suppressOverlap', 'w:jc', 'w:textDirection', 'w:textAlignment',
        'w:textboxTightWrap', 'w:outlineLvl', 'w:divId', 'w:cnfStyle', 'w:rPr', 'w:sectPr',
        'w:pPrChange')


def _run_shading(run, fill):
    rPr = run._r.get_or_add_rPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), fill)
    rPr.insert_element_before(
        shd, 'w:fitText', 'w:vertAlign', 'w:rtl', 'w:cs', 'w:em', 'w:lang',
        'w:eastAsianLayout', 'w:specVanish', 'w:oMath')


def _apply_numbering(paragraph, num_id, level=0):
    pPr = paragraph._p.get_or_add_pPr()
    numPr = pPr.get_or_add_numPr()
    numPr.get_or_add_numId().val = num_id
    numPr.get_or_add_ilvl().val = level


def _style_run(run, fmt):
    """Инлайновое форматирование на run — то, что накопили вложенные теги."""
    font = run.font
    if fmt.get('bold'):
        font.bold = True
    if fmt.get('italic'):
        font.italic = True
    if fmt.get('underline'):
        font.underline = True
    if fmt.get('strike'):
        font.strike = True
    if fmt.get('sup'):
        font.superscript = True
    elif fmt.get('sub'):
        font.subscript = True
    if fmt.get('mono'):
        font.name = MONO_FONT
    elif fmt.get('font'):
        font.name = fmt['font']
    if fmt.get('size'):
        font.size = Pt(fmt['size'])
    color = fmt.get('color')
    if fmt.get('href') and not color:
        color = LINK_COLOR
        font.underline = True
    if color:
        font.color.rgb = RGBColor.from_string(color)
    if fmt.get('fill'):
        _run_shading(run, fmt['fill'])
    elif fmt.get('highlight'):
        font.highlight_color = WD_COLOR_INDEX.YELLOW


# ── Картинки ────────────────────────────────────────────────────────────────

def _pil():
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover — окружение без Pillow
        return None
    return Image


def prepare_image(data, content_type=''):
    """Байты картинки → (байты для Word, ширина px, высота px) или None.

    PNG/JPEG/GIF в разумных габаритах уходят как есть — пережимать их нечем
    улучшить. Всё остальное (прежде всего WebP из бакета) переводится: с
    прозрачностью — в PNG, без — в JPEG; длинная сторона ужимается до
    MAX_IMAGE_SIDE. У анимации берётся первый кадр.
    """
    Image = _pil()
    if not Image or not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as source:
            width, height = source.size
            if width * height > MAX_IMAGE_PIXELS:
                return None
            kind = (source.format or '').upper()
            animated = getattr(source, 'is_animated', False)
            if (kind in ('PNG', 'JPEG', 'GIF') and max(width, height) <= MAX_IMAGE_SIDE
                    and not animated and len(data) <= MAX_IMAGE_BYTES):
                return data, width, height
            if animated:
                source.seek(0)
            frame = source.convert('RGBA') if _has_alpha(source) else source.convert('RGB')
            side = max(frame.size)
            if side > MAX_IMAGE_SIDE:
                scale = float(MAX_IMAGE_SIDE) / float(side)
                size = (max(1, int(round(frame.width * scale))),
                        max(1, int(round(frame.height * scale))))
                frame = frame.resize(size, getattr(Image, 'Resampling', Image).LANCZOS)
            out = io.BytesIO()
            if frame.mode == 'RGBA':
                frame.save(out, format='PNG', optimize=True)
            else:
                frame.save(out, format='JPEG', quality=88, optimize=True)
            encoded = out.getvalue()
            if len(encoded) > MAX_IMAGE_BYTES:
                return None
            return encoded, frame.width, frame.height
    except Exception:  # noqa: BLE001 — битый кадр не повод ронять документ
        logger.info('wiki: картинка не разобралась для Word (%s, %d байт)',
                    content_type or '?', len(data))
        return None


def _has_alpha(image):
    if image.mode in ('RGBA', 'LA', 'PA'):
        return True
    return image.mode == 'P' and 'transparency' in image.info


def _decode_data_uri(src):
    match = _DATA_URI_RE.match(str(src or '').strip())
    if not match:
        return None
    if ';base64' not in (match.group(2) or '').lower():
        return None
    try:
        return base64.b64decode(re.sub(r'\s+', '', match.group(3)), validate=False)
    except (ValueError, TypeError):
        return None


# ── Сборка документа ────────────────────────────────────────────────────────

class _Builder:
    def __init__(self, *, fetch_image=None, portal_url=''):
        self.fetch_image = fetch_image
        self.portal_url = portal_url or ''
        self.stats = {'images': 0, 'missing': 0, 'external': 0}
        self.document = None
        self.slug = ''
        self._image_cache = {}

    # ── Каркас ──────────────────────────────────────────────────────────
    def build(self, article, *, section_paths=(), exported_at=None):
        document = Document()
        self.document = document
        self.slug = str(article.get('slug') or '')
        self._page_setup(document)

        title = str(article.get('title') or 'Статья').strip() or 'Статья'
        document.core_properties.title = title
        document.core_properties.author = str(article.get('author_name') or '')
        document.core_properties.comments = 'Вики iCORE'

        document.add_paragraph(title, style='Title')
        summary = str(article.get('summary') or '').strip()
        if summary:
            document.add_paragraph(summary, style='Subtitle')
        self._meta_line(document, article, section_paths)
        self._footer(document, title, exported_at)

        soup = BeautifulSoup(str(article.get('content') or ''), 'html.parser')
        ctx = self._root_ctx(document)
        self._blocks(soup, ctx)
        self._trim_trailing_empty(document)
        return document

    def _page_setup(self, document):
        section = document.sections[0]
        section.page_width = Cm(PAGE_WIDTH_CM)
        section.page_height = Cm(PAGE_HEIGHT_CM)
        for side in ('left_margin', 'right_margin', 'top_margin', 'bottom_margin'):
            setattr(section, side, Cm(MARGIN_CM))
        normal = document.styles['Normal']
        normal.font.name = BODY_FONT
        normal.font.size = Pt(BODY_SIZE_PT)
        normal.paragraph_format.space_after = Pt(6)

    def _content_width(self):
        section = self.document.sections[0]
        return int(section.page_width - section.left_margin - section.right_margin)

    def _root_ctx(self, container):
        return {
            'container': container,
            'width': self._content_width(),
            'list_depth': 0,
            'fmt': {},
            'pfmt': {},
            'plain_headings': False,
            'reuse': [],
            'empties': [False],
        }

    def _meta_line(self, document, article, section_paths):
        parts = []
        status = STATUS_WORDS.get(str(article.get('status') or ''))
        if status:
            parts.append(status)
        places = [' › '.join(str(name) for name in path if name)
                  for path in (section_paths or ()) if path]
        if places:
            parts.append(('Раздел: ' if len(places) == 1 else 'Разделы: ') + '; '.join(places))
        updated = date_words(article.get('updated_at'))
        if updated:
            parts.append('Обновлено ' + updated)
        author = str(article.get('author_name') or '').strip()
        if author:
            parts.append('Автор: ' + author)
        if not parts:
            return
        paragraph = document.add_paragraph()
        run = paragraph.add_run(' · '.join(parts))
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor.from_string(MUTED_COLOR)
        paragraph.paragraph_format.space_after = Pt(12)

    def _footer(self, document, title, exported_at):
        footer = document.sections[0].footer
        footer.is_linked_to_previous = False
        paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        words = '«%s» — вики iCORE' % title
        stamp = date_words(exported_at) if exported_at else ''
        if stamp:
            words += ', выгружено ' + stamp
        run = paragraph.add_run(words)
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor.from_string(MUTED_COLOR)
        link = self._article_url(self.slug)
        if link:
            tail = paragraph.add_run(' · ')
            tail.font.size = Pt(8)
            tail.font.color.rgb = RGBColor.from_string(MUTED_COLOR)
            self._hyperlink(paragraph, link, [('открыть в вики', {'size': 8})])

    def _trim_trailing_empty(self, document):
        """Пустые абзацы в конце документа — лишний лист при печати; снимаем."""
        body = document.element.body
        children = [child for child in body if child.tag in (qn('w:p'), qn('w:tbl'))]
        while children and children[-1].tag == qn('w:p') \
                and not ''.join(children[-1].itertext()).strip() \
                and not children[-1].findall('.//' + qn('w:drawing')):
            body.remove(children.pop())

    # ── Ссылки ──────────────────────────────────────────────────────────
    def _article_url(self, slug):
        if not self.portal_url or not slug:
            return ''
        return '%s?view=wiki&article=%s' % (self.portal_url, quote(str(slug), safe=''))

    def _resolve_href(self, href):
        """Абсолютный адрес для гиперссылки или '' — тогда остаётся текст."""
        value = str(href or '').strip()
        if not value:
            return ''
        if _ABS_HREF_RE.match(value):
            return value
        match = _ARTICLE_HREF_RE.match(value)
        if match:
            return self._article_url(match.group(1))
        return ''

    def _hyperlink(self, paragraph, url, pieces):
        """Гиперссылка в абзаце: pieces — [(текст, fmt), …]."""
        part = paragraph.part
        r_id = part.relate_to(url, RT.HYPERLINK, is_external=True)
        hyperlink = OxmlElement('w:hyperlink')
        hyperlink.set(qn('r:id'), r_id)
        hyperlink.set(qn('w:history'), '1')
        paragraph._p.append(hyperlink)
        for text, fmt in pieces:
            run = paragraph.add_run(text)
            _style_run(run, dict(fmt, href=url))
            hyperlink.append(run._r)

    # ── Блоки ───────────────────────────────────────────────────────────
    def _blocks(self, parent, ctx):
        """Дети узла: блоки — каждый своим правилом, подряд идущие инлайны — абзацем."""
        inline = []
        for child in list(parent.children):
            if isinstance(child, NavigableString):
                if child.strip() or inline:
                    inline.append(child)
                continue
            if not isinstance(child, Tag):
                continue
            if child.name in _INLINE_TAGS and not (child.name == 'img' and not inline):
                inline.append(child)
                continue
            if inline:
                self._paragraph(inline, ctx)
                inline = []
            self._block(child, ctx)
        if inline:
            self._paragraph(inline, ctx)

    def _block(self, node, ctx):
        name = node.name
        if name == 'p':
            self._paragraph(list(node.children), ctx, node=node)
        elif name in _HEADINGS:
            self._heading(node, ctx)
        elif name in ('ul', 'ol'):
            self._list(node, ctx, ordered=(name == 'ol'))
        elif name == 'li':
            # li вне списка — испорченная разметка; показываем как абзац.
            self._blocks(node, ctx)
        elif name == 'table':
            self._table(node, ctx)
        elif name == 'blockquote':
            self._blockquote(node, ctx)
        elif name == 'pre':
            self._pre(node, ctx)
        elif name == 'hr':
            self._rule(ctx)
        elif name == 'img':
            self._paragraph([node], ctx, node=node)
        elif name == 'figure':
            self._figure(node, ctx)
        elif name == 'figcaption':
            self._paragraph(list(node.children), ctx, node=node, style='Caption')
        elif name == 'details':
            self._details(node, ctx)
        elif name == 'summary':
            self._paragraph(list(node.children), ctx, node=node, fmt=dict(ctx['fmt'], bold=True))
        elif name == 'div':
            self._div(node, ctx)
        elif name in ('thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'colgroup', 'col', 'caption'):
            # Куски таблицы вне таблицы — испорченная разметка; текст не теряем.
            self._blocks(node, ctx)
        else:
            self._blocks(node, ctx)

    def _div(self, node, ctx):
        if node.has_attr('data-wiki-trainer'):
            self._trainer(node, ctx)
            return
        if node.has_attr('data-wiki-collapsible'):
            title = str(node.get('data-title') or '').strip()
            if title:
                self._emit(ctx, [('text', title, dict(ctx['fmt'], bold=True))])
            self._blocks(node, ctx)
            return
        kind = str(node.get('data-wiki-block') or '').strip().lower()
        if kind == 'lead':
            self._blocks(node, dict(ctx, fmt=dict(ctx['fmt'], size=12.5, color=QUOTE_COLOR)))
        elif kind in ('note', 'card', 'stat'):
            self._note(node, ctx)
        elif kind in ('cards', 'stats'):
            self._grid(node, ctx, stats=(kind == 'stats'))
        elif kind == 'gallery':
            self._gallery(node, ctx)
        else:
            self._blocks(node, ctx)

    # ── Абзацы и инлайны ────────────────────────────────────────────────
    def _paragraph(self, children, ctx, node=None, style=None, fmt=None):
        items = []
        base = dict(fmt if fmt is not None else ctx['fmt'])
        if node is not None and node.name != 'img':
            base = self._fmt_from_style(node, base)
        for child in children:
            self._inline(child, base, items)
        align = None
        if node is not None:
            align = _alignment(_style_map(node).get('text-align'))
            if node.name == 'img':
                align = _alignment(node.get('data-align')) or align
            elif len(children) == 1 and isinstance(children[0], Tag) and children[0].name == 'img':
                align = _alignment(children[0].get('data-align')) or align
        self._emit(ctx, items, style=style, align=align)

    def _fmt_from_style(self, node, fmt):
        """Инлайновое форматирование из тега и его style."""
        out = dict(fmt)
        name = node.name
        if name in ('strong', 'b'):
            out['bold'] = True
        elif name in ('em', 'i'):
            out['italic'] = True
        elif name == 'u':
            out['underline'] = True
        elif name in ('s', 'strike'):
            out['strike'] = True
        elif name == 'sup':
            out['sup'] = True
        elif name == 'sub':
            out['sub'] = True
        elif name == 'small':
            out['size'] = round((out.get('size') or BODY_SIZE_PT) * 0.85, 1)
        elif name == 'code':
            out['mono'] = True
            out.setdefault('fill', HEADER_FILL)
        elif name == 'mark':
            fill = _hex_color(node.get('data-color'))
            if fill:
                out['fill'] = fill
            else:
                out['highlight'] = True
        styles = _style_map(node)
        if styles:
            color = _hex_color(styles.get('color'))
            if color:
                out['color'] = color
            fill = _hex_color(styles.get('background-color'))
            if fill and fill != 'FFFFFF':
                out['fill'] = fill
            size = _font_size_pt(styles.get('font-size'))
            if size:
                out['size'] = size
            weight = styles.get('font-weight', '').lower()
            if weight in ('bold', 'bolder', '600', '700', '800', '900'):
                out['bold'] = True
            if styles.get('font-style', '').lower() == 'italic':
                out['italic'] = True
            decoration = styles.get('text-decoration', '').lower()
            if 'underline' in decoration:
                out['underline'] = True
            if 'line-through' in decoration:
                out['strike'] = True
            font = _font_name(styles.get('font-family'))
            if font:
                out['font'] = font
        return out

    def _inline(self, child, fmt, items):
        if isinstance(child, NavigableString):
            if type(child).__name__ in ('Comment', 'Doctype', 'Declaration', 'ProcessingInstruction'):
                return
            items.append(('text', str(child), fmt))
            return
        if not isinstance(child, Tag):
            return
        if child.name == 'br':
            items.append(('br',))
            return
        if child.name == 'img':
            items.append(('img', child))
            return
        inner = self._fmt_from_style(child, fmt)
        if child.name == 'a':
            href = self._resolve_href(child.get('href'))
            if href:
                inner['href'] = href
        for grandchild in child.children:
            self._inline(grandchild, inner, items)

    def _new_paragraph(self, ctx, style=None):
        reuse = ctx.get('reuse')
        if reuse:
            paragraph = reuse.pop()
            if style:
                paragraph.style = self.document.styles[style]
            return paragraph
        return ctx['container'].add_paragraph(style=style)

    def _emit(self, ctx, items, *, style=None, align=None, pre=False, num_id=None,
              level=0):
        """Абзац из накопленных кусков: пробелы схлопываются как в HTML."""
        normalized = _normalize_items(items, pre=pre)
        has_content = any(item[0] != 'text' or item[1] for item in normalized)
        empties = ctx['empties']
        if not has_content:
            # Пустые абзацы редактора — расстояние между блоками; два подряд
            # складываем в один, чтобы документ не зиял дырами.
            if empties[0] or not ctx['container'].__class__.__name__ == 'Document':
                return None
            empties[0] = True
            return self._new_paragraph(ctx, style=style)
        empties[0] = False
        paragraph = self._new_paragraph(ctx, style=style)
        if align is not None:
            paragraph.alignment = align
        self._apply_pfmt(paragraph, ctx['pfmt'])
        if num_id is not None:
            _apply_numbering(paragraph, num_id, level)
        self._fill_runs(paragraph, normalized, ctx)
        return paragraph

    def _apply_pfmt(self, paragraph, pfmt):
        if not pfmt:
            return
        if pfmt.get('indent'):
            paragraph.paragraph_format.left_indent = pfmt['indent']
        if pfmt.get('border_left'):
            _paragraph_border(paragraph, 'left', 18, pfmt['border_left'])
        if pfmt.get('fill'):
            _paragraph_shading(paragraph, pfmt['fill'])
        if pfmt.get('space_after') is not None:
            paragraph.paragraph_format.space_after = pfmt['space_after']

    def _fill_runs(self, paragraph, items, ctx):
        index = 0
        while index < len(items):
            item = items[index]
            if item[0] == 'br':
                paragraph.add_run().add_break()
                index += 1
                continue
            if item[0] == 'img':
                self._picture(item[1], paragraph, ctx)
                index += 1
                continue
            href = item[2].get('href')
            if href:
                group = []
                while index < len(items) and items[index][0] == 'text' \
                        and items[index][2].get('href') == href:
                    group.append((items[index][1], items[index][2]))
                    index += 1
                self._hyperlink(paragraph, href, group)
                continue
            run = paragraph.add_run(item[1])
            _style_run(run, item[2])
            index += 1

    # ── Заголовки, списки, цитаты, код ──────────────────────────────────
    def _heading(self, node, ctx):
        level = _HEADINGS[node.name]
        if ctx.get('plain_headings'):
            fmt = dict(ctx['fmt'], bold=True)
            if ctx.get('heading_color'):
                fmt['color'] = ctx['heading_color']
            if ctx.get('heading_size'):
                fmt['size'] = ctx['heading_size']
            self._paragraph(list(node.children), ctx, node=node, fmt=fmt)
            return
        self._paragraph(list(node.children), ctx, node=node, style='Heading %d' % level,
                        fmt=dict(ctx['fmt']))

    def _list(self, node, ctx, *, ordered):
        depth = min(ctx['list_depth'] + 1, 3)
        suffix = '' if depth == 1 else ' %d' % depth
        style = ('List Number' if ordered else 'List Bullet') + suffix
        num_id = self._restart_numbering(style, _int_attr(node, 'start', 1)) if ordered else None
        inner = dict(ctx, list_depth=depth)
        for child in node.children:
            if not isinstance(child, Tag):
                continue
            if child.name == 'li':
                self._list_item(child, inner, style, num_id, depth)
            elif child.name in ('ul', 'ol'):
                self._list(child, inner, ordered=(child.name == 'ol'))
            else:
                self._block(child, inner)

    def _restart_numbering(self, style_name, start):
        """Новый экземпляр нумерации для стиля — счёт с start, независимо от соседей."""
        numbering = self.document.part.numbering_part.numbering_definitions._numbering
        style = self.document.styles[style_name]
        pPr = style.element.pPr
        num_pr = pPr.numPr if pPr is not None else None
        if num_pr is None or num_pr.numId is None:
            return None
        try:
            base = numbering.num_having_numId(num_pr.numId.val)
        except KeyError:
            return None
        new_num = numbering.add_num(base.abstractNumId.val)
        new_num.add_lvlOverride(ilvl=0).add_startOverride(max(1, int(start or 1)))
        return new_num.numId

    def _list_item(self, node, ctx, style, num_id, depth):
        inline = []
        first_done = False
        continue_style = 'List Continue' + ('' if depth == 1 else ' %d' % depth)

        def flush(block_node=None):
            nonlocal first_done, inline
            if not inline and block_node is None:
                return
            children = inline if block_node is None else list(block_node.children)
            base = ctx['fmt'] if block_node is None else self._fmt_from_style(block_node, ctx['fmt'])
            items = []
            for child in children:
                self._inline(child, base, items)
            align = _alignment(_style_map(block_node).get('text-align')) if block_node is not None else None
            if not first_done:
                self._emit(ctx, items, style=style, align=align, num_id=num_id)
                first_done = True
            else:
                self._emit(ctx, items, style=continue_style, align=align)
            inline = []

        for child in node.children:
            if isinstance(child, NavigableString):
                if child.strip() or inline:
                    inline.append(child)
                continue
            if not isinstance(child, Tag):
                continue
            if child.name in _INLINE_TAGS:
                inline.append(child)
                continue
            flush()
            if child.name == 'p':
                flush(child)
            elif child.name in ('ul', 'ol'):
                if not first_done:
                    # Пункт без своего текста: маркер всё равно нужен, иначе
                    # вложенный список приклеится к предыдущему пункту.
                    self._emit(ctx, [('text', '​', ctx['fmt'])], style=style, num_id=num_id)
                    first_done = True
                self._list(child, ctx, ordered=(child.name == 'ol'))
            else:
                if not first_done:
                    self._emit(ctx, [('text', '​', ctx['fmt'])], style=style, num_id=num_id)
                    first_done = True
                self._block(child, ctx)
        flush()

    def _blockquote(self, node, ctx):
        inner = dict(ctx,
                     fmt=dict(ctx['fmt'], color=ctx['fmt'].get('color') or QUOTE_COLOR, italic=True),
                     pfmt=dict(ctx['pfmt'], indent=Cm(0.75), border_left=RULE_COLOR))
        self._blocks(node, inner)

    def _pre(self, node, ctx):
        text = node.get_text()
        items = []
        fmt = dict(ctx['fmt'], mono=True, size=9.5)
        lines = text.replace('\r\n', '\n').strip('\n').split('\n')
        for index, line in enumerate(lines):
            if index:
                items.append(('br',))
            items.append(('text', line, fmt))
        inner = dict(ctx, pfmt=dict(ctx['pfmt'], fill=HEADER_FILL))
        self._emit(inner, items, pre=True)

    def _rule(self, ctx):
        paragraph = self._new_paragraph(ctx)
        _paragraph_border(paragraph, 'bottom', 6, RULE_COLOR)
        paragraph.paragraph_format.space_after = Pt(6)
        run = paragraph.add_run('')
        run.font.size = Pt(4)
        ctx['empties'][0] = False

    def _figure(self, node, ctx):
        """Картинка (или несколько) и подпись к ней стилем «Название объекта»."""
        caption = node.find('figcaption', recursive=False)
        body = [child for child in node.children
                if not (isinstance(child, Tag) and child.name == 'figcaption')]
        holder = BeautifulSoup('', 'html.parser').new_tag('div')
        for child in body:
            holder.append(child.extract())
        self._blocks(holder, ctx)
        if caption is not None:
            self._paragraph(list(caption.children), ctx, node=caption, style='Caption')

    def _details(self, node, ctx):
        summary = node.find('summary', recursive=False)
        if summary is not None:
            self._paragraph(list(summary.children), ctx, node=summary,
                            fmt=dict(ctx['fmt'], bold=True))
        rest = [child for child in node.children
                if not (isinstance(child, Tag) and child.name == 'summary')]
        wrapper = BeautifulSoup('', 'html.parser')
        holder = wrapper.new_tag('div')
        for child in rest:
            holder.append(child.extract() if hasattr(child, 'extract') else child)
        self._blocks(holder, ctx)

    def _trainer(self, node, ctx):
        label = str(node.get('data-label') or '').strip() or _plain_text(node) or 'тренажёр'
        fmt = dict(ctx['fmt'], italic=True, color=MUTED_COLOR)
        items = [('text', 'Тренажёр «%s» — проходят в статье на портале' % label, fmt)]
        link = self._article_url(self.slug)
        if link:
            items.append(('text', ': ', fmt))
            items.append(('text', 'открыть', dict(fmt, href=link)))
        self._emit(ctx, items)

    # ── Оформительские блоки ────────────────────────────────────────────
    def _note(self, node, ctx):
        tone = _tone_of(node)
        table = ctx['container'].add_table(rows=1, cols=1)
        table.autofit = False
        table.alignment = WD_TABLE_ALIGNMENT.LEFT
        cell = table.cell(0, 0)
        self._set_widths(table, [ctx['width']])
        self._fill_card(cell, node, ctx, tone, width=ctx['width'],
                        stat=(str(node.get('data-wiki-block')) == 'stat'))
        self._after_table(ctx)

    def _grid(self, node, ctx, *, stats):
        cards = [child for child in node.children
                 if isinstance(child, Tag) and child.name == 'div'
                 and str(child.get('data-wiki-block') or '') in ('card', 'stat')]
        # Текст между карточками (редактор такого не делает, но разметка
        # правится и руками) — обычными абзацами перед сеткой.
        for child in node.children:
            if isinstance(child, Tag) and child not in cards:
                self._block(child, ctx)
        if not cards:
            return
        cols = min(max(1, min(3, _int_attr(node, 'data-cols', 2))), len(cards))
        rows = (len(cards) + cols - 1) // cols
        table = ctx['container'].add_table(rows=rows, cols=cols)
        table.autofit = False
        width = ctx['width'] // cols
        self._set_widths(table, [width] * cols)
        numbered = str(node.get('data-numbered') or '').lower() == 'true'
        for index, card in enumerate(cards):
            cell = table.cell(index // cols, index % cols)
            number = (index + 1) if numbered else None
            self._fill_card(cell, card, ctx, _tone_of(card), width=width, stat=stats,
                            number=number)
        self._after_table(ctx)

    def _fill_card(self, cell, node, ctx, tone, *, width, stat=False, number=None):
        fill, line, ink = TONES[tone]
        _shade_cell(cell, fill)
        _cell_borders(cell, top=('nil', 0, 'auto'), right=('nil', 0, 'auto'),
                      bottom=('nil', 0, 'auto'), left=('single', 24, ink))
        text_color = DARK_TEXT if tone == 'dark' else None
        base_fmt = dict(ctx['fmt'])
        if text_color:
            base_fmt['color'] = text_color
        inner = dict(ctx, container=cell, width=max(width - Cm(0.6), Cm(2)), list_depth=0,
                     fmt=base_fmt, pfmt={'space_after': Pt(3)}, plain_headings=True,
                     heading_color=ink, heading_size=(16 if stat else None),
                     reuse=[cell.paragraphs[0]], empties=[True])
        if number is not None:
            heading = node.find(list(_HEADINGS), recursive=False)
            target = heading if heading is not None else node
            prefix = BeautifulSoup('', 'html.parser').new_string('%d. ' % number)
            target.insert(0, prefix)
        if stat:
            inner['fmt'] = dict(base_fmt, size=9, color=text_color or MUTED_COLOR)
        self._blocks(node, inner)
        self._drop_trailing_empty(cell)

    def _gallery(self, node, ctx):
        images = node.find_all('img')
        rest = [child for child in node.children if isinstance(child, Tag) and child.name != 'img'
                and not child.find('img')]
        for child in rest:
            self._block(child, ctx)
        if not images:
            return
        cols = min(3, len(images))
        rows = (len(images) + cols - 1) // cols
        table = ctx['container'].add_table(rows=rows, cols=cols)
        table.autofit = False
        width = ctx['width'] // cols
        self._set_widths(table, [width] * cols)
        for index, image in enumerate(images):
            cell = table.cell(index // cols, index % cols)
            paragraph = cell.paragraphs[0]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            box = dict(ctx, container=cell, width=max(width - Cm(0.4), Cm(2)),
                       max_height=Cm(9), fit_box=True)
            self._picture(image, paragraph, box)
            alt = str(image.get('alt') or '').strip()
            if alt:
                caption = cell.add_paragraph(style='Caption')
                caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = caption.add_run(alt)
                run.font.size = Pt(8)
        self._after_table(ctx)

    def _after_table(self, ctx):
        """После таблицы — пустая строка-отступ, иначе таблицы и текст слипаются."""
        container = ctx['container']
        if container.__class__.__name__ == 'Document':
            spacer = container.add_paragraph()
            spacer.paragraph_format.space_after = Pt(0)
            ctx['empties'][0] = True
        else:
            # В ячейке python-docx сам добавляет абзац после вложенной таблицы.
            ctx['empties'][0] = True

    # ── Таблицы ─────────────────────────────────────────────────────────
    def _table_rows(self, table):
        rows = []
        for child in table.children:
            if not isinstance(child, Tag):
                continue
            if child.name == 'tr':
                rows.append(child)
            elif child.name in ('thead', 'tbody', 'tfoot'):
                rows.extend(tr for tr in child.children if isinstance(tr, Tag) and tr.name == 'tr')
        return rows

    def _table(self, node, ctx):
        caption = node.find('caption', recursive=False)
        if caption is not None:
            self._paragraph(list(caption.children), ctx, node=caption, style='Caption')
        rows = self._table_rows(node)
        if not rows:
            return
        grid = {}        # (r, c) -> (origin_r, origin_c)
        origins = {}     # (r, c) -> (cell_tag, rowspan, colspan)
        n_cols = 0
        for r, tr in enumerate(rows):
            c = 0
            for cell in tr.children:
                if not isinstance(cell, Tag) or cell.name not in ('td', 'th'):
                    continue
                while (r, c) in grid:
                    c += 1
                rowspan = min(_int_attr(cell, 'rowspan', 1), len(rows) - r)
                colspan = _int_attr(cell, 'colspan', 1)
                for dr in range(rowspan):
                    for dc in range(colspan):
                        grid[(r + dr, c + dc)] = (r, c)
                origins[(r, c)] = (cell, rowspan, colspan)
                c += colspan
            n_cols = max(n_cols, c)
        n_cols = max(n_cols, max((c for _, c in grid), default=-1) + 1)
        if not n_cols:
            return

        table = ctx['container'].add_table(rows=len(rows), cols=n_cols)
        table.style = self.document.styles['Table Grid']
        table.autofit = False
        widths = self._column_widths(node, rows, n_cols, ctx['width'])
        self._set_widths(table, widths)

        header_rows = set()
        thead = node.find('thead', recursive=False)
        if thead is not None:
            for index, tr in enumerate(rows):
                if tr.parent is thead:
                    header_rows.add(index)

        for (r, c), (cell_tag, rowspan, colspan) in origins.items():
            target = table.cell(r, c)
            if rowspan > 1 or colspan > 1:
                target = target.merge(table.cell(r + rowspan - 1, c + colspan - 1))
                self._keep_one_paragraph(target)
            width = sum(widths[c:c + colspan])
            is_header = cell_tag.name == 'th' or r in header_rows
            if is_header:
                _shade_cell(target, HEADER_FILL)
            styles = _style_map(cell_tag)
            fill = _hex_color(styles.get('background-color'))
            if fill and fill != 'FFFFFF':
                _shade_cell(target, fill)
            fmt = dict(ctx['fmt'], bold=True) if is_header else dict(ctx['fmt'])
            inner = dict(ctx, container=target, width=max(width - Cm(0.4), Cm(1)),
                         list_depth=0, fmt=fmt, pfmt={'space_after': Pt(2)},
                         plain_headings=True, heading_color=None, heading_size=None,
                         reuse=[target.paragraphs[0]], empties=[True])
            align = _alignment(styles.get('text-align'))
            if align is not None:
                target.paragraphs[0].alignment = align
            self._blocks(cell_tag, inner)
            if align is not None:
                for paragraph in target.paragraphs:
                    paragraph.alignment = align
            self._drop_trailing_empty(target)
        self._after_table(ctx)

    def _column_widths(self, table, rows, n_cols, available):
        """Ширины колонок в EMU: из colgroup/col или colwidth редактора, иначе поровну."""
        px = [None] * n_cols
        colgroup = table.find('colgroup', recursive=False)
        if colgroup is not None:
            index = 0
            for col in colgroup.find_all('col', recursive=False):
                value = _style_map(col).get('width') or col.get('width')
                span = _int_attr(col, 'span', 1)
                for _ in range(span):
                    if index < n_cols:
                        px[index] = _px(value)
                    index += 1
        if any(value is None for value in px) and rows:
            c = 0
            for cell in rows[0].children:
                if not isinstance(cell, Tag) or cell.name not in ('td', 'th'):
                    continue
                colspan = _int_attr(cell, 'colspan', 1)
                parts = [_px(part) for part in str(cell.get('colwidth') or '').split(',')]
                for offset in range(colspan):
                    if c + offset < n_cols and px[c + offset] is None:
                        value = parts[offset] if offset < len(parts) else None
                        px[c + offset] = value
                c += colspan
        if all(value for value in px):
            total = float(sum(px))
            return [int(available * value / total) for value in px]
        return [available // n_cols] * n_cols

    def _set_widths(self, table, widths):
        for index, width in enumerate(widths):
            if index >= len(table.columns):
                break
            table.columns[index].width = Emu(width)
            for cell in table.columns[index].cells:
                cell.width = Emu(width)

    def _keep_one_paragraph(self, cell):
        paragraphs = cell.paragraphs
        for paragraph in paragraphs[1:]:
            if not paragraph.text.strip():
                paragraph._p.getparent().remove(paragraph._p)

    def _drop_trailing_empty(self, cell):
        paragraphs = cell.paragraphs
        while len(paragraphs) > 1 and not paragraphs[-1].text.strip() \
                and not paragraphs[-1]._p.findall('.//' + qn('w:drawing')):
            paragraphs[-1]._p.getparent().remove(paragraphs[-1]._p)
            paragraphs = cell.paragraphs

    # ── Картинки ────────────────────────────────────────────────────────
    def _image_bytes(self, src):
        """(байты, тип) по адресу картинки или None. Чужие адреса не скачиваются."""
        value = str(src or '').strip()
        if not value:
            return None
        if value.lower().startswith('data:'):
            data = _decode_data_uri(value)
            return (data, '') if data else None
        match = FILE_REF.search(value)
        if match and self.fetch_image:
            file_id = match.group(1).lower()
            if file_id in self._image_cache:
                return self._image_cache[file_id]
            try:
                found = self.fetch_image(file_id)
            except Exception:  # noqa: BLE001 — одна битая картинка не роняет документ
                logger.exception('wiki: картинка %s не достана для Word', file_id)
                found = None
            self._image_cache[file_id] = found
            return found
        return None

    def _picture(self, img, paragraph, ctx):
        src = str(img.get('src') or '').strip()
        is_external = bool(src) and src.lower().startswith(('http://', 'https://')) \
            and not FILE_REF.search(src)
        found = None if is_external else self._image_bytes(src)
        prepared = prepare_image(found[0], found[1]) if found else None
        if not prepared:
            if is_external:
                self.stats['external'] += 1
                self._hyperlink(paragraph, src, [('[изображение по ссылке]',
                                                  {'italic': True, 'size': 9})])
            else:
                self.stats['missing'] += 1
                run = paragraph.add_run('[изображение недоступно]')
                _style_run(run, {'italic': True, 'size': 9, 'color': MUTED_COLOR})
            return False
        data, px_w, px_h = prepared
        width = self._image_width(img, px_w, ctx['width'])
        height = int(width * px_h / float(max(px_w, 1)))
        max_height = ctx.get('max_height') or Cm(MAX_IMAGE_HEIGHT_CM)
        if height > max_height:
            height = int(max_height)
            width = int(height * px_w / float(max(px_h, 1)))
        run = paragraph.add_run()
        run.add_picture(io.BytesIO(data), width=Emu(width), height=Emu(height))
        self.stats['images'] += 1
        return True

    def _image_width(self, img, px_w, available):
        """Ширина кадра в EMU: процент редактора, пиксели старой разметки или своя."""
        percent = None
        raw = str(img.get('data-width') or '').strip()
        if raw.isdigit():
            percent = int(raw)
        styles = _style_map(img)
        style_width = styles.get('width', '')
        if percent is None and style_width.endswith('%'):
            try:
                percent = int(float(style_width[:-1]))
            except ValueError:
                percent = None
        if percent:
            percent = min(100, max(10, percent))
            return int(available * percent / 100.0)
        px = _px(style_width) or _px(img.get('width'))
        if px:
            return min(int(px * EMU_PER_PX), available)
        return min(int(px_w * EMU_PER_PX), available)


def _px(value):
    """Число пикселей из '150', '150px', ' 150 '; None — если это не пиксели."""
    text = str(value or '').strip().lower()
    match = re.match(r'^(\d+(?:\.\d+)?)\s*(px)?$', text)
    if not match:
        return None
    number = float(match.group(1))
    return int(number) if number > 0 else None


def _normalize_items(items, *, pre=False):
    """Схлопывает пробелы как браузер: подряд идущие — в один, по краям строк — долой."""
    out = []
    at_line_start = True
    for item in items:
        if item[0] == 'br':
            _rstrip_last(out)
            out.append(item)
            at_line_start = True
            continue
        if item[0] == 'img':
            out.append(item)
            at_line_start = False
            continue
        text = item[1]
        if not pre:
            text = _WS_RE.sub(' ', text)
            if at_line_start:
                text = text.lstrip(' ')
            if out and out[-1][0] == 'text' and out[-1][1].endswith(' ') and text.startswith(' '):
                text = text[1:]
        if not text:
            continue
        out.append(('text', text, item[2]))
        at_line_start = False
    if not pre:
        _rstrip_last(out)
    return [item for item in out if item[0] != 'text' or item[1]]


def _rstrip_last(out):
    if out and out[-1][0] == 'text':
        stripped = out[-1][1].rstrip(' ')
        out[-1] = ('text', stripped, out[-1][2])
