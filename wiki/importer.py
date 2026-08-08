"""Импорт документов в статьи: DOCX, PDF, XLSX/CSV, TXT/MD.

Порт wiki2.0 services/parser.ts на наш стек. Соответствие библиотек:
    mammoth (JS)  -> mammoth (Python, тот же автор и тот же style_map)
    pdf-parse     -> pypdf
    SheetJS       -> openpyxl (прямого аналога sheet_to_html в Python нет,
                     таблицу собираем сами)
    cheerio       -> BeautifulSoup

Три отличия от оригинала, все осознанные:

1. Картинки из DOCX уезжают в GCS, а не на диск. В оригинале они писались в
   backend/uploads/, а диск на Render эфемерный — по дампу прода видно, что
   одна такая ссылка уже 404, а остальные картинки редакторы вставляли base64,
   обходя механизм.

2. Нет таблицы сессий импорта. В оригинале документ загружался, складывался в
   document_import_sessions, редактировался во внешнем ONLYOFFICE и только
   потом становился статьёй. ONLYOFFICE у нас нет (в проде вики он тоже
   выключен), а джобы очистки сессий не было вовсе — на момент дампа семь из
   восьми висели в статусе active с конца июля. Здесь файл разбирается в HTML
   и сразу отдаётся в редактор: сохраняет уже человек.

3. Результат прогоняется через тот же санитайзер, что и ручная правка. В
   оригинале HTML из DOCX не санитайзился вообще — документ Word мог принести
   в базу произвольную разметку.
"""

import io
import os
import re
import uuid

from .sanitize import sanitize_html, to_plain_text

MAX_FILE_BYTES = 25 * 1024 * 1024

SUPPORTED = {
    '.docx': 'Word', '.doc': 'Word',
    '.pdf': 'PDF',
    '.xlsx': 'Excel', '.xlsm': 'Excel', '.csv': 'CSV',
    '.txt': 'Текст', '.md': 'Markdown',
}

# Заголовки Word -> наши. Список повторяет оригинал: без него Word-документ
# превращается в сплошную простыню абзацев, и оглавление статьи не строится.
_STYLE_MAP = [
    "p[style-name='Heading 1'] => h1:fresh",
    "p[style-name='Heading 2'] => h2:fresh",
    "p[style-name='Heading 3'] => h3:fresh",
    "p[style-name='Heading 4'] => h4:fresh",
    "p[style-name='Title'] => h1:fresh",
    "p[style-name='Subtitle'] => h2:fresh",
    "p[style-name='Заголовок 1'] => h1:fresh",
    "p[style-name='Заголовок 2'] => h2:fresh",
    "p[style-name='Заголовок 3'] => h3:fresh",
]


class ImportError_(Exception):
    """Ошибка разбора документа, которую можно показать человеку."""


def _paragraphs_to_html(text):
    """Текст -> абзацы. Пустая строка разделяет абзацы, одиночный перевод — <br>."""
    blocks = []
    for block in re.split(r'\n\s*\n', str(text or '')):
        block = block.strip()
        if not block:
            continue
        escaped = (block.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
        blocks.append('<p>%s</p>' % escaped.replace('\n', '<br>'))
    return ''.join(blocks)


def _title_from_filename(name):
    base = os.path.splitext(os.path.basename(str(name or '')))[0]
    base = re.sub(r'[_\-]+', ' ', base).strip()
    return base[:255] or 'Импортированный документ'


# ─────────────────────────────────────────────────────────────────────────────
# DOCX
# ─────────────────────────────────────────────────────────────────────────────

def _convert_docx(data, *, store_image):
    import mammoth

    images = []

    def convert_image(image):
        """Каждая картинка Word уходит в GCS и получает постоянный адрес.

        store_image возвращает URL вида /api/wiki/file/<uuid> — он не протухает,
        в отличие от подписанной ссылки, и проверяет доступ при каждом запросе.
        """
        try:
            with image.open() as stream:
                blob = stream.read()
            url = store_image(blob, image.content_type or 'image/png')
            if not url:
                return {'src': ''}
            images.append(url)
            return {'src': url}
        except Exception:
            return {'src': ''}

    result = mammoth.convert_to_html(
        io.BytesIO(data),
        style_map='\n'.join(_STYLE_MAP),
        convert_image=mammoth.images.img_element(convert_image),
    )
    raw_text = mammoth.extract_raw_text(io.BytesIO(data)).value
    warnings = [str(m) for m in (result.messages or [])][:20]
    return result.value, raw_text, images, warnings


# ─────────────────────────────────────────────────────────────────────────────
# PDF
# ─────────────────────────────────────────────────────────────────────────────

def _convert_pdf(data):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if getattr(reader, 'is_encrypted', False):
        raise ImportError_('PDF защищён паролем — снимите защиту и попробуйте снова')

    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or '')
        except Exception:
            pages.append('')

    text = '\n\n'.join(p.strip() for p in pages if p.strip())
    if not text.strip():
        raise ImportError_(
            'В PDF нет текстового слоя — похоже, это скан. '
            'Такой файл можно приложить к статье, но не превратить в текст'
        )
    return _paragraphs_to_html(text), text, [], []


# ─────────────────────────────────────────────────────────────────────────────
# Таблицы
# ─────────────────────────────────────────────────────────────────────────────

def _cell_to_text(value):
    if value is None:
        return ''
    text = str(value).strip()
    return (text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _convert_xlsx(data):
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    parts, plain, sheet_names = [], [], []

    for sheet in workbook.worksheets:
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            continue
        sheet_names.append(sheet.title)
        parts.append('<h3>%s</h3>' % _cell_to_text(sheet.title))

        head, body = rows[0], rows[1:]
        html = ['<table><thead><tr>']
        html += ['<th>%s</th>' % _cell_to_text(c) for c in head]
        html.append('</tr></thead><tbody>')
        for row in body:
            html.append('<tr>')
            html += ['<td>%s</td>' % _cell_to_text(c) for c in row]
            html.append('</tr>')
        html.append('</tbody></table>')
        parts.append(''.join(html))

        for row in rows:
            plain.append(' '.join(str(c) for c in row if c is not None))

    workbook.close()
    if not parts:
        raise ImportError_('Файл не содержит данных')
    return ''.join(parts), '\n'.join(plain), [], []


def _convert_csv(data):
    import csv

    for encoding in ('utf-8-sig', 'cp1251', 'utf-8'):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ImportError_('Не удалось определить кодировку файла')

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ';' if sample.count(';') > sample.count(',') else ','

    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        raise ImportError_('Файл пуст')

    html = ['<table><thead><tr>']
    html += ['<th>%s</th>' % _cell_to_text(c) for c in rows[0]]
    html.append('</tr></thead><tbody>')
    for row in rows[1:]:
        html.append('<tr>')
        html += ['<td>%s</td>' % _cell_to_text(c) for c in row]
        html.append('</tr>')
    html.append('</tbody></table>')

    plain = '\n'.join(' '.join(row) for row in rows)
    return ''.join(html), plain, [], []


# ─────────────────────────────────────────────────────────────────────────────
# Точка входа
# ─────────────────────────────────────────────────────────────────────────────

def convert(filename, data, *, store_image=None):
    """Документ -> материал для редактора.

    Возвращает {title, content, summary, images, warnings, kind}.
    content уже прошёл санитизацию — тем же кодом, что и ручная правка.
    """
    if not data:
        raise ImportError_('Пустой файл')
    if len(data) > MAX_FILE_BYTES:
        raise ImportError_('Файл больше %d МБ' % (MAX_FILE_BYTES // (1024 * 1024)))

    ext = os.path.splitext(str(filename or ''))[1].lower()
    if ext not in SUPPORTED:
        raise ImportError_(
            'Формат не поддерживается. Можно: %s' % ', '.join(sorted(set(SUPPORTED.values())))
        )

    if ext in ('.docx', '.doc'):
        if store_image is None:
            raise ImportError_('Нет хранилища для картинок документа')
        html, plain, images, warnings = _convert_docx(data, store_image=store_image)
    elif ext == '.pdf':
        html, plain, images, warnings = _convert_pdf(data)
    elif ext in ('.xlsx', '.xlsm'):
        html, plain, images, warnings = _convert_xlsx(data)
    elif ext == '.csv':
        html, plain, images, warnings = _convert_csv(data)
    else:
        text = data.decode('utf-8', errors='replace')
        html, plain, images, warnings = _paragraphs_to_html(text), text, [], []

    # Тот же санитайзер, что и для ручной правки: документ Word вполне может
    # принести произвольную разметку, а в оригинале импорт не чистился вовсе.
    clean = sanitize_html(html)
    summary = to_plain_text(clean, limit=280)

    return {
        'title': _title_from_filename(filename),
        'content': clean,
        'summary': summary,
        'images': images,
        'warnings': warnings,
        'kind': SUPPORTED[ext],
        'plain_length': len(plain or ''),
    }


def blob_path_for(original_name, content_type=''):
    """Путь в бакете. Раскладываем по дате, как это делает LMS."""
    import datetime

    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.basename(str(original_name or 'file')))[:80]
    if not safe or safe.startswith('.'):
        safe = 'file' + (safe or '')
    today = datetime.datetime.now().strftime('%Y/%m/%d')
    return 'wiki/files/%s/%s_%s' % (today, uuid.uuid4().hex, safe)
