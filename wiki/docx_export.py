# -*- coding: utf-8 -*-
"""Статья вики файлом Word (.docx).

ЗАЧЕМ. Решение владельца 08.10.2026: статьи вики нужно уметь скачивать, «в
ворд формате», и право на это — у администраторов и выше (wiki/access.py:
may_export). Файл уходит из портала вместе с человеком — в письмо, в печать,
подрядчику, — поэтому он обязан быть ЧИТАЕМЫМ САМ ПО СЕБЕ: без вики под рукой,
без картинок по ссылке, без «см. на экране».

КАК ВЫГЛЯДИТ. Уточнение владельца 09.10.2026: «скаченный документ выглядит прям
как иишный, сделай чтобы он был максимально красивым как в статье… в стиле
ios/macos». «Иишный» вид давал шаблон python-docx — это заготовка Word 2010:
название синим с синей чертой, курсивный синий подзаголовок, синие заголовки
Calibri Light, чёрная сетка таблиц. Так выглядит любой документ, собранный
скриптом, и человек узнаёт это с первого взгляда. Здесь документ собирается
по правилам самой статьи — src/components/wiki/wiki-theme.css и
wiki-blocks.css: та же палитра (slate портала + индиго как акцент), та же
иерархия кеглей, те же блоки.

  * Шрифт — Helvetica Neue: системный шрифт Mac/iOS, то есть ровно то, чем
    сайт набран на экране владельца (.wiki-scope: -apple-system, SF Pro).
    Calibri шаблона на Mac вне Office нет вовсе — предпросмотр в Finder и
    Pages рисовали документ Times New Roman. В таблице шрифтов документа
    стоит подсказка подстановки (altName) и PANOSE Segoe UI: на Windows, где
    Helvetica Neue нет, Word возьмёт системный Segoe UI — тот же «стиль
    системы», только своей платформы. Шрифт в файл не вшивается: лицензия
    Helvetica Neue этого не позволяет, а свободный шрифт выглядел бы чужим
    и в Word на Mac, и в Pages.
  * Тема документа (theme1.xml) переписана под ту же палитру и шрифт: даже
    если читатель применит к абзацу встроенный стиль Word, которого здесь
    нет, он получит индиго и Helvetica Neue, а не синий Calibri Light.
  * Заголовки h1–h6 → «Заголовок 1–6» (у статьи свой заголовок стилем
    «Название», поэтому уровни не сдвигаются); у h1 — акцентная метка слева
    и линия снизу, как на сайте. Оглавление — как на сайте: по h1–h3, когда их
    хотя бы два, ссылками на заголовки; это ещё и настоящее поле TOC, то
    есть «Обновить поле» в Word добавит к нему номера страниц.
  * Списки ul/ol → «Маркированный список»/«Нумерованный список» трёх
    уровней; у КАЖДОГО ol своя нумерация с нужного start — иначе Word
    продолжил бы счёт сквозь всю статью. Шаги — номер белым на акцентной
    плашке, галочки и крестики — цветным знаком вместо точки, чипы — строкой
    бордюрных ярлыков, а не столбцом из двадцати пунктов.
  * Таблицы — тонкая сетка slate-200, шапка капителью на светлом фоне,
    чередование строк, повтор шапки на каждой странице; объединённые ячейки
    (colspan/rowspan) объединяются и в Word, ширины колонок — из colwidth.
  * Картинки вшиваются в файл со скруглёнными углами и тонкой рамкой, как в
    статье. Файлы бакета лежат в WebP (wiki/images.py), а Word WebP не ест —
    кадр переводится в PNG (с прозрачностью) или JPEG и ужимается до 1600 px
    по длинной стороне: это документ, а не архив. base64 из старой вики
    вшивается как есть.
  * Оформительские блоки (wiki-blocks.css): плашка — тон, рамка тона и значок
    тона (нарисован Pillow по тем же контурам, что маски на сайте: значок —
    единственное, что отличает предупреждение от совета до чтения текста);
    карточки — сетка с зазорами, цветная грань у тона, номер кружком;
    показатели — крупное число акцентом и тихая подпись; галерея — серая
    полоса кадров (вертикальные по три в ряд, альбомные по одному); вводка —
    крупнее, на акцентной подложке с гранью слева.
  * Раскрывающиеся блоки <details> и <div data-wiki-collapsible> — рамка с
    заголовком и телом: в бумажном документе раскрывать нечего.
  * Кнопка тренажёра → акцентная карточка со ссылкой на статью: сам
    тренажёр в Word не унести, а молча выбросить его — потерять то, что автор
    поставил намеренно.
  * Ссылки → гиперссылки индиго с бледной линией, как на сайте. Внутренняя
    ссылка на статью (?view=wiki&article=…) достраивается до адреса портала,
    который присылает интерфейс: сервер своего адреса не знает, а фронт живёт
    на GitHub Pages с базовым путём.
  * Подвал — название, дата выгрузки, ссылка на статью и «Стр. N из M».

ЧЕГО ЗДЕСЬ НЕТ — КАРТИНОК ПО ЧУЖИМ АДРЕСАМ. Файл бакета достаётся через
fetch_image, который даёт вызывающий (routes_articles) и который проверяет
доступ тем же правилом, что ручка /file/<id>. Картинку по внешнему http-адресу
сервер НЕ скачивает: адрес пишет автор статьи, и заставить сервер ходить по
произвольным адресам из текста — классический SSRF. Вместо кадра в документе
остаётся ссылка на него.

ЛОВУШКИ OOXML, ИЗ-ЗА КОТОРЫХ КОД ТАКОЙ. Порядок детей в w:pPr/w:rPr/w:tcPr/
w:tblPr обязателен по схеме — иначе Word объявляет файл повреждённым, —
поэтому всё вставляется через _set_child со списком порядка. Ширину таблицы
надо задавать явно (w:tblW) и фиксированной раскладкой: без этого Word
считает её «по содержимому», и плашка на три слова получается шириной в три
слова. Две таблицы подряд без абзаца между ними Word сливает в одну — после
каждой стоит абзац-отступ. Зазоры между карточками — пустые столбцы и строки
сетки, а не tblCellSpacing: его не рисует ни QuickLook, ни Pages.

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
    from docx.enum.style import WD_STYLE_TYPE
    from docx.enum.table import WD_ROW_HEIGHT_RULE, WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
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

# 1 px CSS = 1/96 дюйма = 9525 EMU; в пунктах — 0,75 pt. 1 см = 567 twip.
EMU_PER_PX = 9525
PT_PER_PX = 0.75
TWIPS_PER_CM = 567

# ── Шрифты ──────────────────────────────────────────────────────────────────
# Helvetica Neue — системный шрифт Mac/iOS, на котором сайт и набран на экране
# владельца. На Windows его нет: подсказка подстановки (altName + PANOSE Segoe
# UI) ведёт Word к Segoe UI — системному шрифту той платформы.
BODY_FONT = 'Helvetica Neue'
BODY_FALLBACK = 'Segoe UI'
BODY_PANOSE = '020B0502040204020203'       # PANOSE Segoe UI: к нему и подбирать
MONO_FONT = 'Menlo'
MONO_FALLBACK = 'Consolas'
MONO_PANOSE = '020B0609020204030204'       # PANOSE Consolas
# Знаки галочки/крестика: Helvetica Neue их не содержит, и Word подставит
# глиф из символьного шрифта. На Windows это Segoe UI Symbol; на Mac Word сам
# найдёт Apple Symbols. Шрифт назван явно, чтобы на Windows выбор был не случайным.
SYMBOL_FONT = 'Segoe UI Symbol'
BODY_SIZE_PT = 11

# ── Палитра — токены .wiki-scope (wiki-theme.css), без своих оттенков ───────
INK = '0F172A'            # --wiki-ink: заголовки, текст блоков
TEXT = '334155'           # .wiki-prose: цвет абзаца (slate-700)
INK_SOFT = '475569'       # --wiki-ink-soft: подписи, шапка таблицы, цитата
MUTED = '64748B'          # slate-500: аннотация под названием
INK_MUTE = '94A3B8'       # --wiki-ink-mute: служебные строки, подвал
LINE = 'E2E8F0'           # --wiki-line: рамки, линии
LINE_SOFT = 'F1F5F9'      # --wiki-line-soft: фон цитаты, линия внутри блока
SURFACE_ALT = 'F8FAFC'    # --wiki-surface-alt: шапка таблицы, чипы, галерея
ACCENT = '4F46E5'         # --wiki-accent: ссылки, метка h1, номера шагов
ACCENT_SOFT = 'EEF2FF'    # --wiki-accent-soft: вводка
ACCENT_LINE = 'C7D2FE'    # --wiki-accent-line: грань цитаты, линия ссылки
CODE_FILL = '0F172A'      # .wiki-prose pre
CODE_TEXT = 'E2E8F0'
MARK_FILL = 'FEF3C7'      # .wiki-prose mark
CHECK_COLOR = '059669'    # галочка в списке
CROSS_COLOR = 'DC2626'    # крестик в списке

# Имена, под которыми палитру знают тесты и соседний код.
LINK_COLOR = ACCENT
MUTED_COLOR = MUTED
QUOTE_COLOR = INK_SOFT
RULE_COLOR = LINE
HEADER_FILL = SURFACE_ALT

# Кегли. Пропорции — от статьи (16 px = 11 pt): название 28 px, h1 1.875rem,
# h2 1.375rem, h3 1.125rem, вводка 1.125rem, показатель 1.75rem.
TITLE_SIZE = 24
SUBTITLE_SIZE = 12
META_SIZE = 9
HEADING_SIZES = {1: 17, 2: 14.5, 3: 12.5, 4: 11, 5: 10.5, 6: 10.5}
LEAD_SIZE = 12.5
STAT_SIZE = 18
CAPTION_SIZE = 9
CODE_SIZE = 9.5
TABLE_SIZE = 10
TABLE_HEAD_SIZE = 8.5
CHIP_SIZE = 9.5
TOC_SIZE = 9.5
FOOTER_SIZE = 8
BADGE_SIZE = 8.5
LINE_SPACING = 1.3

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
# Карточка этих тонов красится целиком (грань чернилами и фон тона); у остальных
# только грань цветом линии — шесть цветных прямоугольников подряд превращают
# раздел в мозаику (wiki-blocks.css).
CARD_FILLED_TONES = ('ok', 'warn', 'danger', 'tip')
# На тёмной плашке обычный текст — светлый, жирный — белый, ссылка — бледная.
DARK_TEXT = 'CBD5E1'
DARK_STRONG = 'FFFFFF'
DARK_LINK = 'C7D2FE'
DARK_CODE_FILL = '2A3650'

# Подписи типа статьи — те, что сайт показывает бейджем (articleTypes.js):
# обычная статья подписи не получает, как и там.
TYPE_WORDS = {
    'regulation': 'Регламент',
    'instruction': 'Инструкция',
    'job_description': 'Должностная инструкция',
    'tool_description': 'Описание инструмента',
    'trainer': 'Тренажёр',
}
# Тон бейджа: (фон, рамка, чернила). Серый — у ярлыков и статуса «в архиве».
BADGE_TONES = {
    'blue': ('EEF2FF', 'C7D2FE', '4338CA'),
    'amber': ('FFFBEB', 'FDE68A', 'B45309'),
    'green': ('ECFDF5', 'A7F3D0', '047857'),
    'slate': ('F8FAFC', 'E2E8F0', '475569'),
}
TYPE_TONES = {'regulation': 'blue', 'job_description': 'blue', 'trainer': 'green'}
STATUS_WORDS = {'draft': 'Черновик', 'archived': 'В архиве'}
STATUS_TONES = {'draft': 'amber', 'archived': 'slate'}

_MONTHS = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
           'августа', 'сентября', 'октября', 'ноября', 'декабря')

_INLINE_TAGS = frozenset({
    'span', 'a', 'strong', 'b', 'em', 'i', 'u', 's', 'strike', 'mark', 'sub', 'sup',
    'small', 'code', 'br', 'img',
})
_HEADINGS = {'h1': 1, 'h2': 2, 'h3': 3, 'h4': 4, 'h5': 5, 'h6': 6}
TOC_LEVELS = ('h1', 'h2', 'h3')     # оглавление витрины: querySelectorAll('h1, h2, h3')
TOC_MIN_ENTRIES = 2                 # витрина рисует оглавление при toc.length > 1
# Отступ уровня списка (padding-left 1.5rem на сайте) и место под номер шага.
LIST_INDENT = 252000                # 0,7 см в EMU
STEP_INDENT = 360000                # 1,0 см

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

# Порядок детей по схеме OOXML — иначе Word объявляет файл повреждённым.
_PPR_ORDER = (
    'w:pStyle', 'w:keepNext', 'w:keepLines', 'w:pageBreakBefore', 'w:framePr',
    'w:widowControl', 'w:numPr', 'w:suppressLineNumbers', 'w:pBdr', 'w:shd', 'w:tabs',
    'w:suppressAutoHyphens', 'w:kinsoku', 'w:wordWrap', 'w:overflowPunct',
    'w:topLinePunct', 'w:autoSpaceDE', 'w:autoSpaceDN', 'w:bidi', 'w:adjustRightInd',
    'w:snapToGrid', 'w:spacing', 'w:ind', 'w:contextualSpacing', 'w:mirrorIndents',
    'w:suppressOverlap', 'w:jc', 'w:textDirection', 'w:textAlignment',
    'w:textboxTightWrap', 'w:outlineLvl', 'w:divId', 'w:cnfStyle', 'w:rPr', 'w:sectPr',
    'w:pPrChange',
)
_RPR_ORDER = (
    'w:rStyle', 'w:rFonts', 'w:b', 'w:bCs', 'w:i', 'w:iCs', 'w:caps', 'w:smallCaps',
    'w:strike', 'w:dstrike', 'w:outline', 'w:shadow', 'w:emboss', 'w:imprint', 'w:noProof',
    'w:snapToGrid', 'w:vanish', 'w:webHidden', 'w:color', 'w:spacing', 'w:w', 'w:kern',
    'w:position', 'w:sz', 'w:szCs', 'w:highlight', 'w:u', 'w:effect', 'w:bdr', 'w:shd',
    'w:fitText', 'w:vertAlign', 'w:rtl', 'w:cs', 'w:em', 'w:lang', 'w:eastAsianLayout',
    'w:specVanish', 'w:oMath',
)
_TCPR_ORDER = (
    'w:cnfStyle', 'w:tcW', 'w:gridSpan', 'w:hMerge', 'w:vMerge', 'w:tcBorders', 'w:shd',
    'w:noWrap', 'w:tcMar', 'w:textDirection', 'w:tcFitText', 'w:vAlign', 'w:hideMark',
    'w:headers', 'w:cellIns', 'w:cellDel', 'w:cellMerge', 'w:tcPrChange',
)
_TBLPR_ORDER = (
    'w:tblStyle', 'w:tblpPr', 'w:tblOverlap', 'w:bidiVisual', 'w:tblStyleRowBandSize',
    'w:tblStyleColBandSize', 'w:tblW', 'w:jc', 'w:tblCellSpacing', 'w:tblInd',
    'w:tblBorders', 'w:shd', 'w:tblLayout', 'w:tblCellMar', 'w:tblLook', 'w:tblCaption',
    'w:tblDescription', 'w:tblPrChange',
)
_TRPR_ORDER = (
    'w:cnfStyle', 'w:divId', 'w:gridBefore', 'w:gridAfter', 'w:wBefore', 'w:wAfter',
    'w:cantSplit', 'w:trHeight', 'w:tblHeader', 'w:tblCellSpacing', 'w:jc', 'w:hidden',
    'w:ins', 'w:del', 'w:trPrChange',
)
_STYLE_ORDER = (
    'w:name', 'w:aliases', 'w:basedOn', 'w:next', 'w:link', 'w:autoRedefine', 'w:hidden',
    'w:uiPriority', 'w:semiHidden', 'w:unhideWhenUsed', 'w:qFormat', 'w:locked',
    'w:personal', 'w:personalCompose', 'w:personalReply', 'w:rsid', 'w:pPr', 'w:rPr',
    'w:tblPr', 'w:trPr', 'w:tcPr', 'w:tblStylePr',
)
_LVL_ORDER = (
    'w:start', 'w:numFmt', 'w:lvlRestart', 'w:pStyle', 'w:isLgl', 'w:suff', 'w:lvlText',
    'w:lvlPicBulletId', 'w:legacy', 'w:lvlJc', 'w:pPr', 'w:rPr',
)
_THEME_FONT_ATTRS = ('w:asciiTheme', 'w:hAnsiTheme', 'w:eastAsiaTheme', 'w:cstheme')


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
                     status, article_type, tags, author_name, updated_at, slug).
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


def _twips(length):
    """EMU → twip (единица ширин таблиц и отступов в OOXML)."""
    return int(round(int(length) / 635.0))


# ── Низкоуровневые правки OOXML ─────────────────────────────────────────────

def _set_child(parent, element, order):
    """Поставить element в parent на место по схеме, сняв прежний того же имени.

    Word требует детей w:pPr/w:rPr/w:tcPr/w:tblPr строго в порядке схемы;
    append в конец даёт «файл повреждён». Один помощник на все случаи.
    """
    tag = element.tag
    for old in parent.findall(tag):
        parent.remove(old)
    names = [qn(name) for name in order]
    try:
        rank = names.index(tag)
    except ValueError:
        parent.append(element)
        return element
    for child in parent:
        if child.tag in names and names.index(child.tag) > rank:
            child.addprevious(element)
            return element
    parent.append(element)
    return element


def _element(tag, **attrs):
    element = OxmlElement(tag)
    for key, value in attrs.items():
        if value is not None:
            element.set(qn('w:' + key), str(value))
    return element


def _shd(fill):
    return _element('w:shd', val='clear', color='auto', fill=fill)


def _border(side, value='single', size=4, color=LINE, space=0):
    return _element('w:' + side, val=value, sz=size, space=space, color=color)


def _shade_cell(cell, fill):
    _set_child(cell._tc.get_or_add_tcPr(), _shd(fill), _TCPR_ORDER)


def _cell_borders(cell, **sides):
    """Границы ячейки: side=(вид, толщина в 1/8 pt, цвет). 'nil' — убрать."""
    tcPr = cell._tc.get_or_add_tcPr()
    borders = tcPr.find(qn('w:tcBorders'))
    if borders is None:
        borders = _set_child(tcPr, OxmlElement('w:tcBorders'), _TCPR_ORDER)
    for side in ('top', 'left', 'bottom', 'right'):
        if side not in sides:
            continue
        for old in borders.findall(qn('w:' + side)):
            borders.remove(old)
        value, size, color = sides[side]
        borders.append(_border(side, value, size, color))


def _cell_margins(cell, top, left, bottom, right):
    """Поля ячейки в twip — своя пара на каждую сторону (значок уже текста)."""
    tcMar = OxmlElement('w:tcMar')
    for side, value in (('top', top), ('left', left), ('bottom', bottom), ('right', right)):
        tcMar.append(_element('w:' + side, w=value, type='dxa'))
    _set_child(cell._tc.get_or_add_tcPr(), tcMar, _TCPR_ORDER)


def _cell_valign(cell, value='top'):
    _set_child(cell._tc.get_or_add_tcPr(), _element('w:vAlign', val=value), _TCPR_ORDER)


def _paragraph_border(paragraph, side, size, color, space=4, value='single'):
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = pPr.find(qn('w:pBdr'))
    if pBdr is None:
        pBdr = _set_child(pPr, OxmlElement('w:pBdr'), _PPR_ORDER)
    for old in pBdr.findall(qn('w:' + side)):
        pBdr.remove(old)
    element = _border(side, value, size, color, space)
    # Порядок сторон внутри pBdr тоже по схеме.
    _set_child(pBdr, element, ('w:top', 'w:left', 'w:bottom', 'w:right', 'w:between', 'w:bar'))


def _paragraph_shading(paragraph, fill):
    _set_child(paragraph._p.get_or_add_pPr(), _shd(fill), _PPR_ORDER)


def _paragraph_spacing(paragraph, *, before=None, after=None, line=None):
    fmt = paragraph.paragraph_format
    if before is not None:
        fmt.space_before = before
    if after is not None:
        fmt.space_after = after
    if line is not None:
        fmt.line_spacing = line


def _run_shading(run, fill):
    _set_child(run._r.get_or_add_rPr(), _shd(fill), _RPR_ORDER)


def _run_border(run, color, size=4, space=2):
    _set_child(run._r.get_or_add_rPr(),
               _element('w:bdr', val='single', sz=size, space=space, color=color), _RPR_ORDER)


def _run_tracking(run, twentieths):
    """Межбуквенный интервал в двадцатых пункта: −8 ≈ −0.4 pt (tracking названия)."""
    _set_child(run._r.get_or_add_rPr(), _element('w:spacing', val=twentieths), _RPR_ORDER)


def _run_underline_color(run, color):
    u = run._r.get_or_add_rPr().find(qn('w:u'))
    if u is not None:
        u.set(qn('w:color'), color)


def _set_fonts(rPr, name):
    """w:rFonts на все алфавиты одним именем; атрибуты темы снимаются — они
    сильнее явного имени, и без этого заголовки остались бы Calibri Light."""
    rFonts = rPr.find(qn('w:rFonts'))
    if rFonts is None:
        rFonts = _set_child(rPr, OxmlElement('w:rFonts'), _RPR_ORDER)
    for attr in _THEME_FONT_ATTRS:
        if rFonts.get(qn(attr)) is not None:
            del rFonts.attrib[qn(attr)]
    for attr in ('w:ascii', 'w:hAnsi', 'w:cs', 'w:eastAsia'):
        rFonts.set(qn(attr), name)


def _apply_numbering(paragraph, num_id, level=0):
    pPr = paragraph._p.get_or_add_pPr()
    numPr = pPr.get_or_add_numPr()
    numPr.get_or_add_numId().val = num_id
    numPr.get_or_add_ilvl().val = level


def _bookmark(paragraph, name, number):
    """Закладка вокруг содержимого абзаца — цель ссылки из оглавления."""
    p = paragraph._p
    start = _element('w:bookmarkStart', id=number, name=name)
    end = _element('w:bookmarkEnd', id=number)
    index = 1 if p.pPr is not None else 0
    p.insert(index, start)
    p.append(end)


def _field_run(paragraph, kind):
    run = paragraph.add_run()
    run._r.append(_element('w:fldChar', fldCharType=kind))
    return run


def _instr_run(paragraph, text):
    run = paragraph.add_run()
    instr = OxmlElement('w:instrText')
    instr.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    instr.text = text
    run._r.append(instr)
    return run


def _simple_field(paragraph, instr, cached, fmt):
    """Поле PAGE/NUMPAGES с подставленным значением — его видно и там, где
    поля не пересчитывают (QuickLook, телефон)."""
    field = _element('w:fldSimple', instr=instr)
    paragraph._p.append(field)
    run = paragraph.add_run(cached)
    _style_run(run, fmt)
    field.append(run._r)


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
    if fmt.get('caps'):
        font.all_caps = True
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
    if fmt.get('bold') and fmt.get('bold_color') and not fmt.get('own_color'):
        color = fmt['bold_color']
    if fmt.get('href') and not fmt.get('own_color'):
        color = fmt.get('link_color') or LINK_COLOR
        font.underline = True
        _run_underline_color(run, fmt.get('link_line') or ACCENT_LINE)
    if color:
        font.color.rgb = RGBColor.from_string(color)
    if fmt.get('tracking'):
        _run_tracking(run, fmt['tracking'])
    if fmt.get('border'):
        _run_border(run, fmt['border'])
    if fmt.get('fill'):
        _run_shading(run, fmt['fill'])


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


# Значки блоков — те же контуры, что маски в wiki-blocks.css (сетка 24×24,
# линия 2). Рисуются Pillow с восьмикратным запасом и ужимаются: SVG Word не
# всякий умеет, а PNG в 48 px на 0,5 см — это 240 dpi.
ICON_PX = 48
ICON_CM = 0.48
_ICON_SUPERSAMPLE = 8


def icon_png(kind, color):
    """PNG значка kind цветом color ('RRGGBB') или None без Pillow."""
    Image = _pil()
    if Image is None:
        return None
    from PIL import ImageDraw
    px = ICON_PX * _ICON_SUPERSAMPLE
    unit = px / 24.0
    stroke = int(round(2.0 * unit))
    # Рисуется МАСКА, а не цветная картинка: при ужатии RGBA-кадра края
    # смешиваются с прозрачным чёрным и краска тона плывёт. Маска ужимается
    # сама по себе, а цвет накладывается сплошным — ровно как mask на сайте.
    ink = 255
    image = Image.new('L', (px, px), 0)
    draw = ImageDraw.Draw(image)

    def point(x, y):
        return (x * unit, y * unit)

    def cap(x, y):
        radius = stroke / 2.0
        draw.ellipse([x * unit - radius, y * unit - radius, x * unit + radius, y * unit + radius],
                     fill=ink)

    def polyline(points):
        draw.line([point(x, y) for x, y in points], fill=ink, width=stroke, joint='curve')
        for x, y in points:
            cap(x, y)

    def circle(cx, cy, r):
        draw.ellipse([point(cx - r, cy - r), point(cx + r, cy + r)], outline=ink, width=stroke)

    def dot(cx, cy):
        draw.ellipse([point(cx - 1.15, cy - 1.15), point(cx + 1.15, cy + 1.15)], fill=ink)

    if kind == 'ok':
        circle(12, 12, 10)
        polyline([(8.5, 12.5), (11, 15), (15.5, 10)])
    elif kind == 'warn':
        polyline([(12, 3.4), (2.6, 19.4), (21.4, 19.4), (12, 3.4), (2.6, 19.4)])
        polyline([(12, 9), (12, 13)])
        dot(12, 17)
    elif kind == 'danger':
        circle(12, 12, 10)
        polyline([(9, 9), (15, 15)])
        polyline([(15, 9), (9, 15)])
    elif kind == 'tip':
        draw.arc([point(6.5, 2.5), point(17.5, 13.5)], start=135, end=405, fill=ink, width=stroke)
        polyline([(16.4, 11.9), (15, 14.3)])
        polyline([(7.6, 11.9), (9, 14.3)])
        polyline([(9, 18), (15, 18)])
        polyline([(10, 21.5), (14, 21.5)])
    elif kind == 'neutral':
        draw.rounded_rectangle([point(3, 4), point(21, 20)], radius=3 * unit, outline=ink,
                               width=stroke)
        polyline([(8, 9), (16, 9)])
        polyline([(8, 13), (13, 13)])
    elif kind == 'dark':
        polyline([(4, 4), (4, 11)])
        draw.arc([point(4, 7), point(12, 15)], start=90, end=180, fill=ink, width=stroke)
        polyline([(8, 15), (20, 15)])
        polyline([(15, 10), (20, 15), (15, 20)])
    elif kind == 'play':
        draw.polygon([point(7.5, 4.5), point(19.5, 12), point(7.5, 19.5)], fill=ink)
    else:  # info
        circle(12, 12, 10)
        polyline([(12, 16), (12, 12)])
        dot(12, 8)

    mask = image.resize((ICON_PX, ICON_PX), getattr(Image, 'Resampling', Image).LANCZOS)
    rgb = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
    bands = [Image.new('L', (ICON_PX, ICON_PX), channel) for channel in rgb]
    icon = Image.merge('RGBA', bands + [mask])
    out = io.BytesIO()
    icon.save(out, format='PNG', optimize=True)
    return out.getvalue()


# ── Оформление документа: стили, тема, шрифты, нумерация ────────────────────

class _Design:
    """Стили, тема и шрифты документа — по правилам статьи, не шаблона Word."""

    def __init__(self, document):
        self.document = document
        self.styles = document.styles
        self._define_defaults()
        self._define_paragraph_styles()
        self._define_table_style()
        self._patch_theme()
        self._patch_font_table()
        self._drop_template_thumbnail()

    # ── Стили ───────────────────────────────────────────────────────────
    def _style(self, name, kind=None, builtin=False):
        try:
            return self.styles[name]
        except KeyError:
            return self.styles.add_style(name, kind or WD_STYLE_TYPE.PARAGRAPH, builtin=builtin)

    def _define_defaults(self):
        defaults = self.styles.element.find(qn('w:docDefaults'))
        if defaults is not None:
            rPrDefault = defaults.find(qn('w:rPrDefault'))
            rPr = rPrDefault.find(qn('w:rPr')) if rPrDefault is not None else None
            if rPr is not None:
                _set_fonts(rPr, BODY_FONT)
                lang = rPr.find(qn('w:lang'))
                if lang is None:
                    lang = _set_child(rPr, OxmlElement('w:lang'), _RPR_ORDER)
                # Язык проверки правописания — русский: иначе Word подчёркивает
                # каждое слово статьи как ошибку в английском.
                lang.set(qn('w:val'), 'ru-RU')

    def _reset(self, style):
        element = style.element
        if element.pPr is not None:
            element.remove(element.pPr)
        if element.rPr is not None:
            element.remove(element.rPr)

    def _text(self, style, *, size, color=TEXT, bold=False, italic=False, font=BODY_FONT,
              tracking=None, caps=False):
        rPr = style.element.get_or_add_rPr()
        _set_fonts(rPr, font)
        style.font.size = Pt(size)
        style.font.bold = bold or None
        style.font.italic = italic or None
        style.font.all_caps = caps or None
        style.font.color.rgb = RGBColor.from_string(color)
        if tracking:
            _set_child(rPr, _element('w:spacing', val=tracking), _RPR_ORDER)

    def _para(self, style, *, before=0, after=0, line=LINE_SPACING, keep_next=False,
              left=None, hanging=None, contextual=False):
        fmt = style.paragraph_format
        fmt.space_before = Pt(before)
        fmt.space_after = Pt(after)
        fmt.line_spacing = line
        fmt.keep_with_next = True if keep_next else None
        fmt.keep_together = True if keep_next else None
        if left is not None:
            fmt.left_indent = left
        if hanging is not None:
            fmt.first_line_indent = -hanging
        if contextual:
            _set_child(style.element.get_or_add_pPr(), _element('w:contextualSpacing'), _PPR_ORDER)

    def _define_paragraph_styles(self):
        styles = self.styles
        normal = styles['Normal']
        self._reset(normal)
        self._text(normal, size=BODY_SIZE_PT, color=TEXT)
        self._para(normal, after=8)

        title = styles['Title']
        self._reset(title)
        self._text(title, size=TITLE_SIZE, color=INK, bold=True, tracking=-8)
        self._para(title, after=4, line=1.1, keep_next=True)

        subtitle = styles['Subtitle']
        self._reset(subtitle)
        self._text(subtitle, size=SUBTITLE_SIZE, color=MUTED)
        self._para(subtitle, after=6, line=1.35, keep_next=True)

        meta = self._style('Wiki Meta')
        meta.base_style = normal
        self._text(meta, size=META_SIZE, color=INK_MUTE)
        self._para(meta, after=0, line=1.3, keep_next=True)

        badges = self._style('Wiki Badges')
        badges.base_style = normal
        self._text(badges, size=BADGE_SIZE, color=INK_SOFT)
        self._para(badges, after=6, line=1.5, keep_next=True)

        # Заголовки: slate-900 жирным, без синего; у первого уровня метка слева
        # и линия снизу (.wiki-prose h1). Отбивка сверху больше, чем снизу: так
        # заголовок прилипает к своему тексту, а не к предыдущему.
        spacing = {1: (18, 8), 2: (16, 4), 3: (12, 3), 4: (10, 2), 5: (8, 2), 6: (8, 2)}
        tracking = {1: -8, 2: -5, 3: -3}
        for level, size in HEADING_SIZES.items():
            heading = styles['Heading %d' % level]
            self._reset(heading)
            self._text(heading, size=size, color=INK if level < 6 else INK_SOFT, bold=True,
                       tracking=tracking.get(level))
            before, after = spacing[level]
            self._para(heading, before=before, after=after, line=1.2, keep_next=True)
            pPr = heading.element.get_or_add_pPr()
            _set_child(pPr, _element('w:outlineLvl', val=level - 1), _PPR_ORDER)
            if level == 1:
                pBdr = _set_child(pPr, OxmlElement('w:pBdr'), _PPR_ORDER)
                pBdr.append(_border('left', 'single', 24, ACCENT, space=10))
                pBdr.append(_border('bottom', 'single', 4, LINE, space=6))

        caption = styles['Caption']
        self._reset(caption)
        self._text(caption, size=CAPTION_SIZE, color=MUTED)
        self._para(caption, before=2, after=12, line=1.3)
        caption.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER

        quote = styles['Quote']
        self._reset(quote)
        self._text(quote, size=BODY_SIZE_PT, color=INK_SOFT, italic=True)
        self._para(quote, before=4, after=12, line=1.35)

        lead = self._style('Wiki Lead')
        lead.base_style = normal
        self._text(lead, size=LEAD_SIZE, color=INK)
        self._para(lead, after=0, line=1.4)

        code = self._style('Wiki Code')
        code.base_style = normal
        self._text(code, size=CODE_SIZE, color=CODE_TEXT, font=MONO_FONT)
        self._para(code, before=4, after=12, line=1.3)

        chips = self._style('Wiki Chips')
        chips.base_style = normal
        self._text(chips, size=CHIP_SIZE, color=INK)
        self._para(chips, before=2, after=10, line=1.75)

        spacer = self._style('Wiki Spacer')
        spacer.base_style = normal
        self._text(spacer, size=6, color=TEXT)
        self._para(spacer, after=6, line=1.0)

        # Оглавление — встроенные имена Word: обновив поле, читатель получит
        # те же стили, а не серый шаблонный список.
        toc_label = self._style('TOC Heading', builtin=True)
        self._reset(toc_label)
        toc_label.base_style = normal
        self._text(toc_label, size=8, color=INK_MUTE, bold=True, caps=True, tracking=12)
        self._para(toc_label, before=0, after=4, line=1.3, keep_next=True)
        for level in (1, 2, 3):
            entry = self._style('toc %d' % level, builtin=True)
            self._reset(entry)
            entry.base_style = normal
            self._text(entry, size=TOC_SIZE, color=INK_SOFT)
            self._para(entry, after=2, line=1.3, left=Cm(0.35 * (level - 1)))
            pBdr = _set_child(entry.element.get_or_add_pPr(), OxmlElement('w:pBdr'), _PPR_ORDER)
            pBdr.append(_border('left', 'single', 4, LINE, space=8))

        # Списки: точка slate, отступы как у статьи (padding-left 1.5rem ≈ 0,7 см
        # на уровень), пункты теснее абзацев. Нумерация к стилям привязывается
        # ниже, в _Numbering.
        for depth in (1, 2, 3):
            suffix = '' if depth == 1 else ' %d' % depth
            for base in ('List Bullet', 'List Number'):
                style = styles[base + suffix]
                self._reset(style)
                self._para(style, after=3, line=LINE_SPACING, left=Cm(0.7 * depth),
                           hanging=Cm(0.7), contextual=True)
            cont = styles['List Continue' + suffix]
            self._reset(cont)
            self._para(cont, after=3, line=LINE_SPACING, left=Cm(0.7 * depth), contextual=True)

        table_text = self._style('Wiki Table Text')
        table_text.base_style = normal
        self._text(table_text, size=TABLE_SIZE, color=TEXT)
        self._para(table_text, after=2, line=1.25)

        # Подвал и колонтитул — тихие.
        footer = styles['Footer']
        self._reset(footer)
        self._text(footer, size=FOOTER_SIZE, color=INK_MUTE)
        self._para(footer, after=0, line=1.2)

    def _define_table_style(self):
        """Стиль «Wiki Table»: сетка slate-200, поля как у ячеек статьи."""
        style = self._style('Wiki Table', WD_STYLE_TYPE.TABLE)
        element = style.element
        for child in list(element):
            if child.tag in (qn('w:tblPr'), qn('w:tcPr'), qn('w:pPr'), qn('w:rPr')):
                element.remove(child)
        tblPr = OxmlElement('w:tblPr')
        borders = OxmlElement('w:tblBorders')
        for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            borders.append(_border(side, 'single', 4, LINE))
        tblPr.append(borders)
        margins = OxmlElement('w:tblCellMar')
        for side, value in (('top', 85), ('left', 113), ('bottom', 85), ('right', 113)):
            margins.append(_element('w:' + side, w=value, type='dxa'))
        tblPr.append(margins)
        _set_child(element, tblPr, _STYLE_ORDER)
        style.base_style = self.styles['Normal Table']

    # ── Тема, шрифты, эскиз ─────────────────────────────────────────────
    def _part_blob(self, reltype):
        try:
            part = self.document.part.part_related_by(reltype)
        except KeyError:
            return None, b''
        return part, part.blob

    def _patch_theme(self):
        """Шрифты и цвета темы — под статью. Любой встроенный стиль Word,
        который здесь не переопределён, возьмёт их из темы, а не синий Office."""
        part, blob = self._part_blob(RT.THEME)
        if part is None:
            return
        text = blob.decode('utf-8')
        text = re.sub(r'(<a:(?:latin)\s+typeface=")[^"]*(")', r'\g<1>' + BODY_FONT + r'\2', text)
        for name, color in (('dk2', INK), ('lt2', SURFACE_ALT), ('accent1', ACCENT),
                            ('accent2', '0F766E'), ('accent3', 'B45309'), ('accent4', '6D28D9'),
                            ('accent5', '0369A1'), ('accent6', 'B91C1C'), ('hlink', ACCENT),
                            ('folHlink', '6D28D9')):
            text = re.sub(r'(<a:%s>\s*<a:srgbClr val=")[0-9A-Fa-f]{6}(")' % name,
                          r'\g<1>' + color + r'\2', text)
        part._blob = text.encode('utf-8')

    def _patch_font_table(self):
        """Таблица шрифтов: подсказки подстановки для машин без Helvetica Neue/Menlo."""
        part, blob = self._part_blob(RT.FONT_TABLE)
        if part is None:
            return
        text = blob.decode('utf-8')
        fonts = ''
        for name, alt, panose, family, pitch in (
                (BODY_FONT, BODY_FALLBACK, BODY_PANOSE, 'swiss', 'variable'),
                (MONO_FONT, MONO_FALLBACK, MONO_PANOSE, 'modern', 'fixed'),
                (SYMBOL_FONT, 'Apple Symbols', '020B0502040204020203', 'swiss', 'variable')):
            if ('w:name="%s"' % name) in text:
                continue
            fonts += (
                '<w:font w:name="%s"><w:altName w:val="%s"/><w:panose1 w:val="%s"/>'
                '<w:charset w:val="00"/><w:family w:val="%s"/><w:pitch w:val="%s"/>'
                '<w:sig w:usb0="E00002FF" w:usb1="4000ACFF" w:usb2="00000009" w:usb3="00000000" '
                'w:csb0="0000019F" w:csb1="00000000"/></w:font>' % (name, alt, panose, family, pitch))
        part._blob = text.replace('</w:fonts>', fonts + '</w:fonts>').encode('utf-8')

    def _drop_template_thumbnail(self):
        """Эскиз docProps/thumbnail.jpeg из шаблона python-docx — чужая картинка;
        Finder показал бы её вместо первой страницы статьи."""
        rels = self.document.part.package.rels
        for rId, rel in list(rels.items()):
            if rel.reltype == RT.THUMBNAIL:
                del rels[rId]
                rels._target_parts_by_rId.pop(rId, None)


class _Numbering:
    """Свои определения нумерации: точки, номера, шаги, галочки, крестики."""

    def __init__(self, document):
        self.document = document
        self.root = document.part.numbering_part.numbering_definitions._numbering
        existing = [int(node.get(qn('w:abstractNumId')))
                    for node in self.root.findall(qn('w:abstractNum'))]
        self._next_abstract = max(existing + [0]) + 1
        self.bullets = self._abstract([
            self._level(0, 'bullet', '•', left=0.7, hanging=0.7, color=INK_SOFT),
            self._level(1, 'bullet', '–', left=1.4, hanging=0.7, color=INK_SOFT),
            self._level(2, 'bullet', '•', left=2.1, hanging=0.7, color=INK_SOFT, size=8),
        ])
        self.numbers = self._abstract([
            self._level(depth, 'decimal', '%%%d.' % (depth + 1), left=0.7 * (depth + 1),
                        hanging=0.7, color=TEXT) for depth in range(3)
        ])
        # Шаг: номер белым на акцентной плашке — как кружок с цифрой на сайте.
        self.steps = self._abstract([
            self._level(0, 'decimal', ' %1 ', left=1.0, hanging=1.0, color='FFFFFF',
                        bold=True, size=9, fill=ACCENT),
        ])
        self.checks = self._abstract([
            self._level(0, 'bullet', '✓', left=0.75, hanging=0.75, color=CHECK_COLOR,
                        bold=True, font=SYMBOL_FONT),
        ])
        self.crosses = self._abstract([
            self._level(0, 'bullet', '✕', left=0.75, hanging=0.75, color=CROSS_COLOR,
                        bold=True, font=SYMBOL_FONT),
        ])
        bullets_num = self.root.add_num(self.bullets).numId
        numbers_num = self.root.add_num(self.numbers).numId
        self.checks_num = self.root.add_num(self.checks).numId
        self.crosses_num = self.root.add_num(self.crosses).numId
        for depth in (1, 2, 3):
            suffix = '' if depth == 1 else ' %d' % depth
            self._bind(document.styles['List Bullet' + suffix], bullets_num, depth - 1)
            self._bind(document.styles['List Number' + suffix], numbers_num, depth - 1)

    def _bind(self, style, num_id, level):
        pPr = style.element.get_or_add_pPr()
        numPr = pPr.get_or_add_numPr()
        numPr.get_or_add_numId().val = num_id
        numPr.get_or_add_ilvl().val = level

    def _level(self, ilvl, fmt, text, *, left, hanging, color, bold=False, size=None,
               fill=None, font=BODY_FONT):
        lvl = _element('w:lvl', ilvl=ilvl)
        lvl.append(_element('w:start', val=1))
        lvl.append(_element('w:numFmt', val=fmt))
        lvl.append(_element('w:lvlText', val=text))
        lvl.append(_element('w:lvlJc', val='left'))
        pPr = OxmlElement('w:pPr')
        pPr.append(_element('w:ind', left=int(left * TWIPS_PER_CM), hanging=int(hanging * TWIPS_PER_CM)))
        lvl.append(pPr)
        rPr = OxmlElement('w:rPr')
        rFonts = OxmlElement('w:rFonts')
        for attr in ('w:ascii', 'w:hAnsi', 'w:cs', 'w:eastAsia'):
            rFonts.set(qn(attr), font)
        rPr.append(rFonts)
        if bold:
            rPr.append(OxmlElement('w:b'))
        rPr.append(_element('w:color', val=color))
        if size:
            rPr.append(_element('w:sz', val=int(size * 2)))
            rPr.append(_element('w:szCs', val=int(size * 2)))
        if fill:
            rPr.append(_shd(fill))
        lvl.append(rPr)
        return lvl

    def _abstract(self, levels):
        number = self._next_abstract
        self._next_abstract += 1
        node = _element('w:abstractNum', abstractNumId=number)
        node.append(_element('w:multiLevelType', val='hybridMultilevel'))
        for lvl in levels:
            node.append(lvl)
        first_num = self.root.find(qn('w:num'))
        if first_num is not None:
            first_num.addprevious(node)
        else:
            self.root.append(node)
        return number

    def restart(self, abstract_id, start=1):
        """Новый экземпляр нумерации — счёт с start, независимо от соседей."""
        num = self.root.add_num(abstract_id)
        num.add_lvlOverride(ilvl=0).add_startOverride(max(1, int(start or 1)))
        return num.numId


# ── Сборка документа ────────────────────────────────────────────────────────

class _Builder:
    def __init__(self, *, fetch_image=None, portal_url=''):
        self.fetch_image = fetch_image
        self.portal_url = portal_url or ''
        self.stats = {'images': 0, 'missing': 0, 'external': 0}
        self.document = None
        self.numbering = None
        self.slug = ''
        self._image_cache = {}
        self._prepared_cache = {}
        self._icon_cache = {}
        self._toc = []            # [(id(node), уровень, текст)]
        self._toc_index = {}      # id(node) → номер закладки
        self._bookmarks = 0

    # ── Каркас ──────────────────────────────────────────────────────────
    def build(self, article, *, section_paths=(), exported_at=None):
        document = Document()
        self.document = document
        self.slug = str(article.get('slug') or '')
        _Design(document)
        self.numbering = _Numbering(document)
        self._page_setup(document)

        title = str(article.get('title') or 'Статья').strip() or 'Статья'
        self._properties(document, article, title, exported_at)

        soup = BeautifulSoup(str(article.get('content') or ''), 'html.parser')
        self._collect_toc(soup)

        self._badges(document, article)
        document.add_paragraph(title, style='Title')
        summary = str(article.get('summary') or '').strip()
        if summary:
            document.add_paragraph(summary, style='Subtitle')
        self._meta_line(document, article, section_paths)
        self._header_rule(document)
        self._toc_block(document)
        self._footer(document, title, exported_at)

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
        section.footer_distance = Cm(1.0)
        section.header_distance = Cm(1.0)

    def _properties(self, document, article, title, exported_at):
        props = document.core_properties
        props.title = title
        props.author = str(article.get('author_name') or '')
        props.comments = 'Вики iCORE'
        props.subject = str(article.get('summary') or '').strip()
        props.category = TYPE_WORDS.get(str(article.get('article_type') or ''), '')
        tags = [str(tag) for tag in (article.get('tags') or []) if str(tag).strip()]
        props.keywords = ', '.join(tags)
        if isinstance(exported_at, datetime):
            # Иначе в свойствах файла остаётся «создан в 2013» из шаблона.
            props.created = exported_at
            props.modified = exported_at
        props.last_modified_by = ''
        props.revision = 1

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

    # ── Шапка: бейджи, сводка, линия, оглавление, подвал ────────────────
    def _badge_runs(self, paragraph, words):
        """Ярлыки шапки — как IosBadge на сайте: тон, рамка, мелкий текст."""
        for index, (word, tone) in enumerate(words):
            if index:
                paragraph.add_run('  ')
            fill, line, ink = BADGE_TONES[tone]
            run = paragraph.add_run(' %s ' % word)
            _style_run(run, {'size': BADGE_SIZE, 'color': ink, 'fill': fill, 'border': line,
                             'bold': True})

    def _badges(self, document, article):
        words = []
        status = str(article.get('status') or '')
        if status in STATUS_WORDS:
            words.append((STATUS_WORDS[status], STATUS_TONES[status]))
        kind = str(article.get('article_type') or '')
        if kind in TYPE_WORDS:
            words.append((TYPE_WORDS[kind], TYPE_TONES.get(kind, 'slate')))
        for tag in (article.get('tags') or []):
            tag = _WS_RE.sub(' ', str(tag or '')).strip()
            if tag:
                words.append((tag, 'slate'))
        if not words:
            return
        self._badge_runs(document.add_paragraph(style='Wiki Badges'), words)

    def _meta_line(self, document, article, section_paths):
        parts = []
        author = str(article.get('author_name') or '').strip()
        if author:
            parts.append('Автор: ' + author)
        updated = date_words(article.get('updated_at'))
        if updated:
            parts.append('Обновлено ' + updated)
        places = [' › '.join(str(name) for name in path if name)
                  for path in (section_paths or ()) if path]
        if places:
            parts.append(('Раздел: ' if len(places) == 1 else 'Разделы: ') + '; '.join(places))
        if not parts:
            return
        paragraph = document.add_paragraph(style='Wiki Meta')
        for index, part in enumerate(parts):
            if index:
                dot = paragraph.add_run('  ·  ')
                dot.font.color.rgb = RGBColor.from_string('CBD5E1')
            paragraph.add_run(part)

    def _header_rule(self, document):
        """Линия под шапкой — border-b карточки статьи на сайте."""
        last = document.paragraphs[-1]
        _paragraph_border(last, 'bottom', 4, LINE, space=10)
        last.paragraph_format.space_after = Pt(16)

    def _collect_toc(self, soup):
        for node in soup.find_all(list(TOC_LEVELS)):
            text = _plain_text(node)
            if not text:
                continue
            self._toc.append((id(node), _HEADINGS[node.name], text))
        if len(self._toc) < TOC_MIN_ENTRIES:
            self._toc = []
            return
        self._toc_index = {key: index + 1 for index, (key, _level, _text) in enumerate(self._toc)}

    def _toc_block(self, document):
        """«Содержание» — как на сайте, ссылками на заголовки, внутри поля TOC."""
        if not self._toc:
            return
        document.add_paragraph('Содержание', style='TOC Heading')
        top = min(level for _key, level, _text in self._toc)
        last_index = len(self._toc) - 1
        for index, (key, level, text) in enumerate(self._toc):
            depth = min(3, max(1, level - top + 1))
            paragraph = document.add_paragraph(style='toc %d' % depth)
            if index == 0:
                _field_run(paragraph, 'begin')
                _instr_run(paragraph, ' TOC \\o "1-3" \\h \\z \\u ')
                _field_run(paragraph, 'separate')
            self._anchor_link(paragraph, '_wiki_h%d' % self._toc_index[key], text,
                              {'size': TOC_SIZE, 'color': INK_SOFT})
            if index == last_index:
                _field_run(paragraph, 'end')
        # Воздух после оглавления — отдельным абзацем: отступ «после» у
        # последней строки предпросмотр перед таблицей вводки не рисует.
        document.add_paragraph(style='Wiki Spacer')

    def _anchor_link(self, paragraph, anchor, text, fmt):
        hyperlink = _element('w:hyperlink', anchor=anchor, history=1)
        paragraph._p.append(hyperlink)
        run = paragraph.add_run(text)
        _style_run(run, fmt)
        hyperlink.append(run._r)

    def _footer(self, document, title, exported_at):
        footer = document.sections[0].footer
        footer.is_linked_to_previous = False
        paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        paragraph.style = document.styles['Footer']
        _paragraph_border(paragraph, 'top', 4, LINE_SOFT, space=6)
        paragraph.paragraph_format.tab_stops.add_tab_stop(Emu(self._content_width()),
                                                          WD_TAB_ALIGNMENT.RIGHT)
        fmt = {'size': FOOTER_SIZE, 'color': INK_MUTE}
        words = '«%s» · вики iCORE' % title
        stamp = date_words(exported_at) if exported_at else ''
        if stamp:
            words += ' · выгружено ' + stamp
        _style_run(paragraph.add_run(words), fmt)
        link = self._article_url(self.slug)
        if link:
            _style_run(paragraph.add_run(' · '), fmt)
            self._hyperlink(paragraph, link, [('открыть в вики', dict(fmt, link_color=INK_SOFT,
                                                                     link_line=LINE))])
        _style_run(paragraph.add_run('\tСтр. '), fmt)
        _simple_field(paragraph, 'PAGE', '1', fmt)
        _style_run(paragraph.add_run(' из '), fmt)
        _simple_field(paragraph, 'NUMPAGES', '1', fmt)

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
            self._details(node, ctx, title=str(node.get('data-title') or '').strip())
            return
        kind = str(node.get('data-wiki-block') or '').strip().lower()
        if kind == 'lead':
            self._lead(node, ctx)
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
        return self._emit(ctx, items, style=style, align=align)

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
            out['size'] = round((out.get('size') or BODY_SIZE_PT) * 0.875, 1)
            out.setdefault('fill', out.get('code_fill') or LINE_SOFT)
            if not out.get('code_fill'):
                out.setdefault('border', LINE)
        elif name == 'mark':
            out['fill'] = _hex_color(node.get('data-color')) or MARK_FILL
            if not out.get('own_color'):
                out['color'] = INK
        styles = _style_map(node)
        if styles:
            color = _hex_color(styles.get('color'))
            if color:
                out['color'] = color
                out['own_color'] = True
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
        paragraph = self._new_paragraph(ctx, style=style or ctx.get('pstyle'))
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
        if pfmt.get('fill'):
            _paragraph_shading(paragraph, pfmt['fill'])
        if pfmt.get('space_after') is not None:
            paragraph.paragraph_format.space_after = pfmt['space_after']
        if pfmt.get('space_before') is not None:
            paragraph.paragraph_format.space_before = pfmt['space_before']
        if pfmt.get('line'):
            paragraph.paragraph_format.line_spacing = pfmt['line']

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
                fmt['tracking'] = -6
            inner = dict(ctx, pfmt=dict(ctx['pfmt'], space_after=Pt(ctx.get('heading_after', 3))))
            paragraph = self._paragraph(list(node.children), inner, node=node, fmt=fmt)
        else:
            paragraph = self._paragraph(list(node.children), ctx, node=node,
                                        style='Heading %d' % level, fmt=dict(ctx['fmt']))
        number = self._toc_index.get(id(node))
        if paragraph is not None and number:
            self._bookmarks += 1
            _bookmark(paragraph, '_wiki_h%d' % number, self._bookmarks)

    def _list(self, node, ctx, *, ordered):
        """Список. Отступы считаются здесь и ставятся на абзац явно — так
        вложенный список стоит от текста своего пункта и в Word, и в
        предпросмотре, который отступы уровня нумерации не читает."""
        variant = str(node.get('data-variant') or '').strip().lower()
        if variant == 'chips':
            self._chips(node, ctx)
            return
        depth = min(ctx['list_depth'] + 1, 3)
        suffix = '' if depth == 1 else ' %d' % depth
        style = ('List Number' if ordered else 'List Bullet') + suffix
        num_id = None
        level = 0
        hang = LIST_INDENT
        inner = dict(ctx, list_depth=depth, plain_headings=True, heading_color=INK)
        if variant == 'steps' and depth == 1:
            num_id = self.numbering.restart(self.numbering.steps, _int_attr(node, 'start', 1))
            # Шаги стоят просторнее пунктов: между ними на сайте 1.75rem.
            inner['pfmt'] = dict(ctx['pfmt'], space_after=Pt(8))
            hang = STEP_INDENT
        elif variant == 'checks' and depth == 1:
            num_id = self.numbering.checks_num
        elif variant == 'crosses' and depth == 1:
            num_id = self.numbering.crosses_num
        elif ordered:
            num_id = self.numbering.restart(self.numbering.numbers, _int_attr(node, 'start', 1))
            level = depth - 1
        base = int(ctx.get('indent_base') or 0)
        inner['item_indent'] = (base + hang, hang)
        inner['indent_base'] = base + hang
        for child in node.children:
            if not isinstance(child, Tag):
                continue
            if child.name == 'li':
                self._list_item(child, inner, style, num_id, level, depth)
            elif child.name in ('ul', 'ol'):
                self._list(child, inner, ordered=(child.name == 'ol'))
            else:
                self._block(child, inner)

    def _list_item(self, node, ctx, style, num_id, level, depth):
        inline = []
        first_done = False
        continue_style = 'List Continue' + ('' if depth == 1 else ' %d' % depth)
        text_pos, hang = ctx['item_indent']

        def emit(items, *, align=None):
            nonlocal first_done
            if not first_done:
                paragraph = self._emit(ctx, items, style=style, align=align, num_id=num_id,
                                       level=level)
                hanging = True
                first_done = True
            else:
                paragraph = self._emit(ctx, items, style=continue_style, align=align)
                hanging = False
            if paragraph is not None:
                paragraph.paragraph_format.left_indent = Emu(text_pos)
                paragraph.paragraph_format.first_line_indent = Emu(-hang if hanging else 0)
            return paragraph

        def flush(block_node=None, bold=False):
            nonlocal inline
            if not inline and block_node is None:
                return
            children = inline if block_node is None else list(block_node.children)
            base = ctx['fmt'] if block_node is None else self._fmt_from_style(block_node, ctx['fmt'])
            if bold:
                base = dict(base, bold=True, color=INK)
            items = []
            for child in children:
                self._inline(child, base, items)
            align = _alignment(_style_map(block_node).get('text-align')) if block_node is not None else None
            emit(items, align=align)
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
            elif child.name in _HEADINGS:
                # Заголовок шага/пункта — жирная строка с отступом пункта, а не
                # «Заголовок 4» без отступа (он выпал бы из списка).
                flush(child, bold=True)
            elif child.name in ('ul', 'ol'):
                if not first_done:
                    # Пункт без своего текста: маркер всё равно нужен, иначе
                    # вложенный список приклеится к предыдущему пункту.
                    emit([('text', '​', ctx['fmt'])])
                self._list(child, ctx, ordered=(child.name == 'ol'))
            else:
                if not first_done:
                    emit([('text', '​', ctx['fmt'])])
                self._block(child, dict(ctx, pfmt=dict(ctx['pfmt'], indent=Emu(text_pos))))
        flush()

    def _chips(self, node, ctx):
        """Чипы — строкой ярлыков в рамке, как на сайте, а не столбцом пунктов."""
        words = [_plain_text(li) for li in node.find_all('li', recursive=False)]
        words = [word for word in words if word]
        if not words:
            return
        paragraph = self._new_paragraph(ctx, style='Wiki Chips')
        for index, word in enumerate(words):
            if index:
                paragraph.add_run('  ')
            run = paragraph.add_run(' %s ' % word)
            _style_run(run, dict(ctx['fmt'], size=CHIP_SIZE, color=ctx['fmt'].get('color') or INK,
                                 fill=SURFACE_ALT, border=LINE))
        ctx['empties'][0] = False

    def _box(self, ctx, *, fill, line=None, left=None, margins=(150, 220, 150, 220), width=None,
             keep=False):
        """Блок с подложкой — ячейка таблицы. Заливку АБЗАЦА предпросмотр
        macOS и Pages не рисуют, заливку ячейки рисуют все; к тому же ячейка
        держит внутренние поля, как padding на сайте. Возвращает (таблица, ячейка)."""
        width = width or ctx['width']
        edge = {'top': line, 'left': line, 'bottom': line, 'right': line,
                'insideH': None, 'insideV': None}
        table = self._frame(ctx, 1, 1, [width], margins=margins, fill=fill, borders=edge,
                            cant_split=keep)
        cell = table.cell(0, 0)
        if left:
            size, color = left
            _cell_borders(cell, left=('single', size, color))
        return table, cell

    def _blockquote(self, node, ctx):
        """Цитата: светлая подложка, акцентная грань слева, курсив (.wiki-prose blockquote)."""
        _table, cell = self._box(ctx, fill=LINE_SOFT, left=(24, ACCENT_LINE),
                                 margins=(150, 260, 150, 220), keep=self._can_keep_whole(node))
        inner = self._cell_ctx(cell, ctx, ctx['width'] - Cm(0.9), pstyle='Quote',
                               fmt=dict(ctx['fmt'], color=ctx['fmt'].get('color') or QUOTE_COLOR,
                                        italic=True),
                               pfmt={'space_after': Pt(4)})
        self._blocks(node, inner)
        self._drop_trailing_empty(cell)
        self._space_after_block(ctx)

    def _lead(self, node, ctx):
        """Вводка: крупнее, на акцентной подложке с гранью слева (.wiki-prose [lead])."""
        _table, cell = self._box(ctx, fill=ACCENT_SOFT, left=(24, ACCENT),
                                 margins=(170, 280, 170, 260), keep=self._can_keep_whole(node))
        inner = self._cell_ctx(cell, ctx, ctx['width'] - Cm(1.0), pstyle='Wiki Lead',
                               fmt=dict(ctx['fmt'], size=LEAD_SIZE,
                                        color=ctx['fmt'].get('color') or INK),
                               pfmt={'space_after': Pt(4), 'line': 1.4})
        self._blocks(node, inner)
        self._drop_trailing_empty(cell)
        self._space_after_block(ctx)

    def _pre(self, node, ctx):
        """Код: тёмная плашка и светлый моноширинный текст (.wiki-prose pre)."""
        text = node.get_text()
        items = []
        fmt = dict(ctx['fmt'], mono=True, size=CODE_SIZE, color=CODE_TEXT)
        lines = text.replace('\r\n', '\n').strip('\n').split('\n')
        for index, line in enumerate(lines):
            if index:
                items.append(('br',))
            items.append(('text', line, fmt))
        _table, cell = self._box(ctx, fill=CODE_FILL, margins=(170, 240, 170, 240))
        inner = self._cell_ctx(cell, ctx, ctx['width'] - Cm(0.9), pfmt={'space_after': Pt(0), 'line': 1.3})
        self._emit(inner, items, style='Wiki Code', pre=True)
        self._space_after_block(ctx)

    def _rule(self, ctx):
        paragraph = self._new_paragraph(ctx)
        _paragraph_border(paragraph, 'bottom', 4, RULE_COLOR, space=1)
        _paragraph_spacing(paragraph, before=Pt(8), after=Pt(16), line=1.0)
        run = paragraph.add_run('')
        run.font.size = Pt(2)
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

    # ── Оформительские блоки ────────────────────────────────────────────
    def _space_after_block(self, ctx):
        """Воздух после блока — отступ-абзац. После таблицы он ещё и обязателен:
        две таблицы подряд Word сливает в одну."""
        container = ctx['container']
        if container.__class__.__name__ == 'Document':
            container.add_paragraph(style='Wiki Spacer')
        ctx['empties'][0] = True

    _after_table = _space_after_block

    def _frame(self, ctx, rows, cols, widths, *, margins=(140, 200, 140, 200), fill=None,
               borders=None, cant_split=False, row_heights=None):
        """Таблица-рамка с явной шириной, фиксированной раскладкой и полями ячеек.

        Без w:tblW и tblLayout=fixed Word считает ширину «по содержимому», и
        плашка на три слова получается шириной в три слова.
        """
        table = ctx['container'].add_table(rows=rows, cols=cols)
        table.style = None
        table.autofit = False
        table.alignment = WD_TABLE_ALIGNMENT.LEFT
        tblPr = table._tbl.tblPr
        total = sum(widths)
        _set_child(tblPr, _element('w:tblW', w=_twips(total), type='dxa'), _TBLPR_ORDER)
        _set_child(tblPr, _element('w:tblLayout', type='fixed'), _TBLPR_ORDER)
        if borders is not None:
            element = OxmlElement('w:tblBorders')
            for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
                value = borders.get(side)
                element.append(_border(side, 'single', 4, value) if value else _border(side, 'nil', 0, 'auto'))
            _set_child(tblPr, element, _TBLPR_ORDER)
        if margins:
            element = OxmlElement('w:tblCellMar')
            for side, value in zip(('top', 'left', 'bottom', 'right'), margins):
                element.append(_element('w:' + side, w=value, type='dxa'))
            _set_child(tblPr, element, _TBLPR_ORDER)
        self._set_widths(table, widths)
        for index, row in enumerate(table.rows):
            trPr = row._tr.get_or_add_trPr()
            if cant_split:
                _set_child(trPr, OxmlElement('w:cantSplit'), _TRPR_ORDER)
            if row_heights and row_heights[index]:
                row.height = row_heights[index]
                row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
            if fill:
                for cell in row.cells:
                    _shade_cell(cell, fill)
        return table

    def _cell_ctx(self, cell, ctx, width, **extra):
        inner = dict(ctx, container=cell, width=width, list_depth=0,
                     pfmt={'space_after': Pt(3)}, plain_headings=True, heading_color=INK,
                     heading_size=None, reuse=[cell.paragraphs[0]], empties=[True])
        inner.pop('pstyle', None)
        inner.update(extra)
        return inner

    def _icon_run(self, paragraph, kind, color, size_cm=ICON_CM):
        """Значок тона картинкой в начале абзаца. Без Pillow значка нет — текст цел."""
        key = (kind, color)
        if key not in self._icon_cache:
            self._icon_cache[key] = icon_png(kind, color)
        data = self._icon_cache[key]
        if not data:
            return False
        run = paragraph.add_run()
        run.add_picture(io.BytesIO(data), width=Cm(size_cm), height=Cm(size_cm))
        return True

    def _can_keep_whole(self, node):
        """Держать блок на одной странице можно, пока он короткий: строку с
        cantSplit длиннее листа Word обрезает."""
        return node.find('img') is None and len(node.find_all(['p', 'li', 'h4', 'tr'])) <= 12

    def _note(self, node, ctx):
        """Плашка: значок тона слева, заголовок чернилами тона, тело на фоне тона."""
        tone = _tone_of(node)
        fill, line, ink = TONES[tone]
        kind = str(node.get('data-wiki-block') or '')
        if kind in ('card', 'stat'):
            # Одиночная карточка/показатель вне сетки — рисуем как сетку из одной.
            holder = BeautifulSoup('', 'html.parser').new_tag(
                'div', attrs={'data-wiki-block': 'cards' if kind == 'card' else 'stats', 'data-cols': '1'})
            node.wrap(holder)
            self._grid(holder, ctx, stats=(kind == 'stat'))
            return
        icon_col = Cm(0.95)
        widths = [icon_col, ctx['width'] - icon_col]
        table = self._frame(ctx, 1, 2, widths, margins=(150, 190, 150, 220), fill=fill,
                            borders={'top': line, 'left': line, 'bottom': line, 'right': line,
                                     'insideH': None, 'insideV': None},
                            cant_split=self._can_keep_whole(node))
        icon_cell, cell = table.cell(0, 0), table.cell(0, 1)
        _cell_margins(icon_cell, 165, 190, 150, 0)
        _cell_valign(icon_cell, 'top')
        icon = icon_cell.paragraphs[0]
        icon.paragraph_format.space_after = Pt(0)
        self._icon_run(icon, tone, ink)
        dark = tone == 'dark'
        base_fmt = dict(ctx['fmt'])
        if dark:
            base_fmt.update(color=DARK_TEXT, bold_color=DARK_STRONG, link_color=DARK_LINK,
                            link_line='4C5C7A', code_fill=DARK_CODE_FILL)
        inner = self._cell_ctx(cell, ctx, widths[1] - Cm(0.75), fmt=base_fmt,
                               heading_color=ink, heading_after=2)
        self._blocks(node, inner)
        self._drop_trailing_empty(cell)
        self._space_after_block(ctx)

    def _grid(self, node, ctx, *, stats):
        """Карточки и показатели: сетка с зазорами — пустые столбцы и строки."""
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
        default_cols = 3 if stats else 2
        cols = min(max(1, min(3, _int_attr(node, 'data-cols', default_cols))), len(cards))
        rows = (len(cards) + cols - 1) // cols
        gap = Cm(0.3)
        card_width = (ctx['width'] - gap * (cols - 1)) // cols
        widths = []
        for index in range(cols):
            if index:
                widths.append(gap)
            widths.append(card_width)
        heights = []
        for index in range(rows * 2 - 1):
            heights.append(gap if index % 2 else None)
        table = self._frame(ctx, rows * 2 - 1, len(widths), widths, margins=(150, 200, 150, 200),
                            borders={}, cant_split=False, row_heights=heights)
        slots = {((index // cols) * 2, (index % cols) * 2) for index in range(len(cards))}
        for r, row in enumerate(table.rows):
            for c, cell in enumerate(row.cells):
                if (r, c) not in slots:
                    self._blank_cell(cell)
        numbered = str(node.get('data-numbered') or '').lower() == 'true'
        for index, card in enumerate(cards):
            cell = table.cell((index // cols) * 2, (index % cols) * 2)
            trPr = cell._tc.getparent().get_or_add_trPr()
            if self._can_keep_whole(card):
                _set_child(trPr, OxmlElement('w:cantSplit'), _TRPR_ORDER)
            number = (index + 1) if numbered else None
            self._fill_card(cell, card, ctx, _tone_of(card), width=card_width, stat=stats,
                            number=number, explicit_tone=card.has_attr('data-tone'))
        self._space_after_block(ctx)

    def _blank_cell(self, cell):
        """Пустая ячейка сетки: ни рамки, ни высоты сверх зазора."""
        paragraph = cell.paragraphs[0]
        _paragraph_spacing(paragraph, before=Pt(0), after=Pt(0), line=1.0)
        run = paragraph.add_run('')
        run.font.size = Pt(1)

    def _fill_card(self, cell, node, ctx, tone, *, width, stat=False, number=None,
                   explicit_tone=False):
        fill, line, ink = TONES[tone]
        number_color = ACCENT
        if stat:
            _shade_cell(cell, SURFACE_ALT)
            _cell_borders(cell, top=('single', 4, LINE), right=('single', 4, LINE),
                          bottom=('single', 4, LINE), left=('single', 4, LINE))
            if explicit_tone:
                number_color = ink
        elif explicit_tone and tone in CARD_FILLED_TONES:
            _shade_cell(cell, fill)
            _cell_borders(cell, top=('single', 4, line), right=('single', 4, line),
                          bottom=('single', 4, line), left=('single', 18, ink))
        else:
            # Карточка без тона на сайте белая с тенью; на бумаге тень не
            # нарисовать, и её место занимает подложка в полтона.
            _shade_cell(cell, SURFACE_ALT)
            _cell_borders(cell, top=('single', 4, LINE), right=('single', 4, LINE),
                          bottom=('single', 4, LINE),
                          left=('single', 18, line) if explicit_tone else ('single', 4, LINE))
        base_fmt = dict(ctx['fmt'])
        if not stat:
            base_fmt['size'] = TABLE_SIZE
        inner = self._cell_ctx(cell, ctx, width - Cm(0.75), fmt=base_fmt,
                               heading_color=(number_color if stat else INK),
                               heading_size=(STAT_SIZE if stat else None), heading_after=2)
        if stat:
            inner['fmt'] = dict(base_fmt, size=CAPTION_SIZE, color=INK_SOFT)
        if number is not None:
            first = cell.paragraphs[0]
            badge = first.add_run(' %d ' % number)
            _style_run(badge, {'bold': True, 'size': 8.5, 'color': 'FFFFFF', 'fill': ACCENT})
            first.add_run('  ')
            # Номер остаётся в первом абзаце: заголовок карточки идёт следом в
            # той же строке, а без заголовка — текст.
        self._blocks(node, inner)
        self._drop_trailing_empty(cell)

    def _gallery(self, node, ctx):
        """Галерея — серая полоса кадров (.wiki-prose [gallery]); на бумаге листать
        нечем, поэтому кадры стоят все: вертикальные по три в ряд, альбомные по одному."""
        images = node.find_all('img')
        rest = [child for child in node.children if isinstance(child, Tag) and child.name != 'img'
                and not child.find('img')]
        for child in rest:
            self._block(child, ctx)
        if not images:
            return
        prepared = [self._prepared(image) for image in images]
        portrait = sum(1 for item in prepared if item and item[2] > item[1])
        landscape = sum(1 for item in prepared if item and item[2] <= item[1])
        if portrait and not landscape:
            cols = 3
        elif landscape and not portrait:
            cols = 1
        else:
            cols = 2
        cols = min(cols, len(images))
        rows = (len(images) + cols - 1) // cols
        gap = 140
        width = ctx['width'] // cols
        table = self._frame(ctx, rows, cols, [width] * cols, margins=(gap, gap, gap, gap),
                            fill=SURFACE_ALT, borders={}, cant_split=False)
        frame_width = max(width - Emu(gap * 635 * 2), Cm(2))
        for index, image in enumerate(images):
            cell = table.cell(index // cols, index % cols)
            paragraph = cell.paragraphs[0]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _paragraph_spacing(paragraph, after=Pt(2), line=1.0)
            box = dict(ctx, container=cell, width=frame_width, max_height=Cm(9 if cols > 1 else 16),
                       fit_box=True)
            self._picture(image, paragraph, box)
            alt = str(image.get('alt') or '').strip()
            if alt:
                caption = cell.add_paragraph(style='Caption')
                caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
                _paragraph_spacing(caption, before=Pt(2), after=Pt(0))
                run = caption.add_run(alt)
                run.font.size = Pt(8)
        self._space_after_block(ctx)

    def _details(self, node, ctx, title=None):
        """Раскрывающийся блок — рамка с заголовком и телом (.wiki-prose details)."""
        summary = None
        if title is None:
            summary = node.find('summary', recursive=False)
            title = _plain_text(summary) if summary is not None else ''
        accent = str(node.get('data-required-for-ack') or '').lower() == 'true'
        line = ACCENT_LINE if accent else LINE
        table = self._frame(ctx, 1, 1, [ctx['width']], margins=(150, 220, 150, 220), fill='FFFFFF',
                            borders={'top': line, 'left': line, 'bottom': line, 'right': line,
                                     'insideH': None, 'insideV': None},
                            cant_split=self._can_keep_whole(node))
        cell = table.cell(0, 0)
        inner = self._cell_ctx(cell, ctx, ctx['width'] - Cm(0.8))
        if title:
            head = cell.paragraphs[0]
            inner['reuse'] = []
            if summary is not None:
                items = []
                for child in summary.children:
                    self._inline(child, dict(ctx['fmt'], bold=True, color=INK), items)
                self._fill_runs(head, _normalize_items(items), inner)
            else:
                _style_run(head.add_run(title), dict(ctx['fmt'], bold=True, color=INK))
            _paragraph_border(head, 'bottom', 4, LINE_SOFT, space=6)
            _paragraph_spacing(head, after=Pt(8))
            inner['empties'] = [False]
        rest = [child for child in node.children
                if not (isinstance(child, Tag) and child.name == 'summary')]
        holder = BeautifulSoup('', 'html.parser').new_tag('div')
        for child in rest:
            holder.append(child.extract() if hasattr(child, 'extract') else child)
        self._blocks(holder, inner)
        self._drop_trailing_empty(cell)
        self._space_after_block(ctx)

    def _trainer(self, node, ctx):
        """Кнопка тренажёра — акцентная карточка со ссылкой (.wiki-trainer-embed)."""
        label = str(node.get('data-label') or '').strip() or _plain_text(node) or 'тренажёр'
        raw = str(node.get('data-width') or '').strip()
        percent = int(raw) if raw.isdigit() else 60
        width = max(Cm(6), int(ctx['width'] * min(100, max(10, percent)) / 100.0))
        table = self._frame(ctx, 1, 1, [width], margins=(150, 220, 150, 220), fill=ACCENT,
                            borders={}, cant_split=True)
        cell = table.cell(0, 0)
        head = cell.paragraphs[0]
        _paragraph_spacing(head, after=Pt(2), line=1.2)
        if self._icon_run(head, 'play', 'FFFFFF', size_cm=0.36):
            head.add_run('  ')
        _style_run(head.add_run(label), {'bold': True, 'color': 'FFFFFF', 'size': BODY_SIZE_PT})
        line = cell.add_paragraph()
        _paragraph_spacing(line, after=Pt(0), line=1.2)
        fmt = {'size': 8.5, 'color': DARK_LINK}
        _style_run(line.add_run('Тренажёр проходят в статье на портале'), fmt)
        link = self._article_url(self.slug)
        if link:
            _style_run(line.add_run(' · '), fmt)
            self._hyperlink(line, link, [('открыть', dict(fmt, link_color='FFFFFF', link_line=DARK_LINK))])
        self._space_after_block(ctx)

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

        widths = self._column_widths(node, rows, n_cols, ctx['width'])
        table = self._frame(ctx, len(rows), n_cols, widths, margins=(85, 113, 85, 113),
                            borders={side: LINE for side in ('top', 'left', 'bottom', 'right',
                                                             'insideH', 'insideV')},
                            cant_split=True)
        table.style = self.document.styles['Wiki Table']

        header_rows = set()
        thead = node.find('thead', recursive=False)
        for index, tr in enumerate(rows):
            cells = [cell for cell in tr.children if isinstance(cell, Tag) and cell.name in ('td', 'th')]
            if (thead is not None and tr.parent is thead) or (cells and all(cell.name == 'th' for cell in cells)):
                header_rows.add(index)
        if header_rows and header_rows == set(range(len(header_rows))):
            for index in header_rows:
                trPr = table.rows[index]._tr.get_or_add_trPr()
                _set_child(trPr, OxmlElement('w:tblHeader'), _TRPR_ORDER)
        body_rows = [index for index in range(len(rows)) if index not in header_rows]
        striped = {index for position, index in enumerate(body_rows) if position % 2 == 1}

        for (r, c), (cell_tag, rowspan, colspan) in origins.items():
            target = table.cell(r, c)
            if rowspan > 1 or colspan > 1:
                target = target.merge(table.cell(r + rowspan - 1, c + colspan - 1))
                self._keep_one_paragraph(target)
            width = sum(widths[c:c + colspan])
            is_header = cell_tag.name == 'th' or r in header_rows
            if is_header:
                _shade_cell(target, HEADER_FILL)
            elif r in striped:
                _shade_cell(target, SURFACE_ALT)
            styles = _style_map(cell_tag)
            fill = _hex_color(styles.get('background-color'))
            if fill and fill != 'FFFFFF':
                _shade_cell(target, fill)
            if is_header:
                fmt = dict(ctx['fmt'], bold=True, caps=True, size=TABLE_HEAD_SIZE, color=INK_SOFT,
                           tracking=6)
            else:
                fmt = dict(ctx['fmt'], size=TABLE_SIZE)
            inner = self._cell_ctx(target, ctx, max(width - Cm(0.4), Cm(1)), fmt=fmt,
                                   pfmt={'space_after': Pt(2)}, pstyle='Wiki Table Text',
                                   heading_color=INK)
            align = _alignment(styles.get('text-align'))
            if align is not None:
                target.paragraphs[0].alignment = align
            self._blocks(cell_tag, inner)
            if align is not None:
                for paragraph in target.paragraphs:
                    paragraph.alignment = align
            self._drop_trailing_empty(target)
        self._space_after_block(ctx)

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

    def _is_external(self, src):
        return bool(src) and src.lower().startswith(('http://', 'https://')) and not FILE_REF.search(src)

    def _prepared(self, img):
        """(байты, ширина, высота) кадра для Word или None; один раз на адрес."""
        src = str(img.get('src') or '').strip()
        if self._is_external(src):
            return None
        key = src[:4096]
        if key not in self._prepared_cache:
            found = self._image_bytes(src)
            self._prepared_cache[key] = prepare_image(found[0], found[1]) if found else None
        return self._prepared_cache[key]

    def _picture(self, img, paragraph, ctx):
        src = str(img.get('src') or '').strip()
        if self._is_external(src):
            self.stats['external'] += 1
            self._hyperlink(paragraph, src, [('[изображение по ссылке]', {'italic': True, 'size': 9})])
            return False
        prepared = self._prepared(img)
        if not prepared:
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
        shape = run.add_picture(io.BytesIO(data), width=Emu(width), height=Emu(height))
        self._round_picture(shape, width, height)
        # Кадр не отрывается от подписи под ним.
        paragraph.paragraph_format.keep_with_next = True
        self.stats['images'] += 1
        return True

    def _round_picture(self, shape, width, height):
        """Скруглённые углы и тонкая рамка — как у картинки в статье (radius 12px,
        border 1px slate-200). Геометрия roundRect: adj — доля короткой стороны."""
        spPr = shape._inline.find('.//' + qn('pic:spPr'))
        if spPr is None:
            return
        geometry = spPr.find(qn('a:prstGeom'))
        if geometry is None:
            return
        geometry.set('prst', 'roundRect')
        for old in geometry.findall(qn('a:avLst')):
            geometry.remove(old)
        avLst = OxmlElement('a:avLst')
        guide = OxmlElement('a:gd')
        guide.set('name', 'adj')
        radius = Cm(0.3)
        shorter = max(1, min(width, height))
        guide.set('fmla', 'val %d' % min(50000, int(100000 * radius / float(shorter))))
        avLst.append(guide)
        geometry.append(avLst)
        for old in spPr.findall(qn('a:ln')):
            spPr.remove(old)
        line = OxmlElement('a:ln')
        line.set('w', '6350')
        solid = OxmlElement('a:solidFill')
        color = OxmlElement('a:srgbClr')
        color.set('val', LINE)
        solid.append(color)
        line.append(solid)
        geometry.addnext(line)

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
