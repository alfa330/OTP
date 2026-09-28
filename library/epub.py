# -*- coding: utf-8 -*-
"""Разбор файла EPUB: описание книги, обложка, оглавление и разметка страниц.

Модуль — лист: zipfile, xml, html.parser и ничего больше, ни Flask, ни базы.
Проверяется на байтах без сервера (tests/test_library.py).

ЧТО ОТДАЁТ parse_epub. Словарь с названием, автором, языком, обложкой
(байты и тип), списком глав в порядке чтения (spine) с числом знаков в каждой,
оглавлением с номером страницы у каждого пункта и итогом — знаков и страниц.

ПОЧЕМУ СТРАНИЦЫ СЧИТАЕТ СЕРВЕР. Ридер (epub.js) умеет и сам резать книгу на
«локации», но для этого он открывает КАЖДУЮ главу в браузере читателя — на
толстой книге это секунды ожидания при каждом открытии, и у каждого читателя
заново. Здесь то же самое делается один раз при загрузке, а ридер только
складывает: начало текущей главы + доля пролистанного внутри неё.

ПОЧЕМУ 1800 ЗНАКОВ. Это стандартная (машинописная) страница русского
издательского дела — 1800 знаков с пробелами. Номер страницы от этого не
зависит ни от шрифта, ни от ширины экрана, и «стр. 40» у оператора с телефона
и у тренера за компьютером — одно и то же место книги.

ЧЕГО ЗДЕСЬ НЕТ. Книги с DRM (Adobe ADEPT, Readium LCP) не читаются ничем,
кроме своих программ, — такой файл отклоняется с понятной причиной, а не
ложится в каталог мёртвым грузом. Шрифтовая обфускация IDPF — не DRM, её
epub.js понимает, и она пропускается.
"""

import io
import posixpath
import re
import zipfile
import zlib
from html.parser import HTMLParser
from urllib.parse import unquote
from xml.etree import ElementTree

# Стандартная страница: 1800 знаков с пробелами. См. шапку модуля.
CHARS_PER_PAGE = 1800

# Вес файла книги. Художественная книга без картинок — сотни килобайт, с
# иллюстрациями — единицы мегабайт; 50 МБ — с большим запасом под альбом.
# Больше — скорее не книга, а архив, и тащить его в браузер читателя целиком
# (epub.js читает файл в память) нельзя.
MAX_EPUB_BYTES = 50 * 1024 * 1024

# Защита от «zip-бомбы»: файл весом в мегабайт, распаковывающийся в гигабайты.
# Читаем только то, что нужно (OPF, оглавление, главы), но и это ограничено.
MAX_UNCOMPRESSED_BYTES = 400 * 1024 * 1024
MAX_ENTRIES = 20000
# Одна глава — это текст; 30 МБ разметки в одном файле уже не глава.
MAX_DOCUMENT_BYTES = 30 * 1024 * 1024

# Обложка крупнее этого — не обложка, и держать её в памяти ради миниатюры
# карточки незачем.
MAX_COVER_BYTES = 15 * 1024 * 1024

# Пределы длины. Оглавление больше двух тысяч пунктов в боковой панели не
# прочтёт никто, а в базе оно лежит одним JSON.
MAX_TOC_ITEMS = 2000
# Глав в порядке чтения. Предел нужен не ради базы: одна и та же глава может
# стоять в spine сколько угодно раз, и без него файл в килобайты заставлял бы
# разбирать мегабайты текста по кругу.
MAX_SPINE_ITEMS = 5000
# Знаков в книге. «Война и мир» — около трёх миллионов; сто миллионов — уже не
# книга, а колонка total_chars (INTEGER) переполнилась бы на двух миллиардах.
MAX_TOTAL_CHARS = 100_000_000
MAX_TITLE_LENGTH = 300
MAX_AUTHOR_LENGTH = 300

# Алгоритмы шифрования, которые НЕ являются DRM: обфускация встроенных
# шрифтов (IDPF и Adobe). Текст книги они не трогают.
FONT_OBFUSCATION = frozenset({
    'http://www.idpf.org/2008/embedding',
    'http://ns.adobe.com/pdf/enc#RC',
})

_OPS_NS = 'http://www.idpf.org/2007/ops'
_WS = re.compile(r'\s+')

# Теги, текст которых читатель не видит: служебный заголовок документа,
# скрипты и стили. Их знаки в страницы не идут.
_SKIP_TAGS = frozenset({'head', 'script', 'style', 'title', 'svg:title'})
_HEADING_TAGS = frozenset({'h1', 'h2', 'h3'})


class EpubError(Exception):
    """Отказ, который можно показать человеку."""

    def __init__(self, message, code='LIBRARY_EPUB_INVALID'):
        super().__init__(message)
        self.message = message
        self.code = code


def _local(tag):
    """Имя тега без пространства имён: '{http://...}item' -> 'item'."""
    tag = str(tag or '')
    return tag.rsplit('}', 1)[-1] if '}' in tag else tag


def _clean(text, limit):
    value = _WS.sub(' ', str(text or '')).strip()
    return value[:limit].rstrip() if len(value) > limit else value


def _parse_xml(raw, what):
    try:
        return ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise EpubError(f'Файл повреждён: не читается {what}') from exc


class _TextCounter(HTMLParser):
    """Считает видимые знаки документа главы и запоминает, где стоят якоря.

    Пробелы схлопываются так же, как их схлопывает браузер: любая пачка
    пробельных символов — один пробел. Знаки между блоками («абзац</p><p>абзац»)
    не добавляются: страница — оценка объёма, а не вёрстка, и один знак на
    абзац погоды не делает.

    anchors — {id: смещение в знаках}: пункт оглавления ведёт не только на
    начало главы, но и на якорь внутри неё (у многих книг вся книга — один
    файл, а главы — якоря в нём).
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.count = 0
        self.anchors = {}
        self.heading = ''
        self._skip = 0
        self._space = True
        self._in_heading = 0
        self._heading_parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        for name, value in attrs:
            if name in ('id', 'name') and value and value not in self.anchors:
                self.anchors[value] = self.count
        if tag in _HEADING_TAGS and not self.heading:
            self._in_heading += 1

    def handle_startendtag(self, tag, attrs):
        # <a id="x"/> — якорь без содержимого: только запомнить место.
        for name, value in attrs:
            if name in ('id', 'name') and value and value not in self.anchors:
                self.anchors[value] = self.count

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag in _HEADING_TAGS and self._in_heading:
            self._in_heading -= 1
            if not self._in_heading and not self.heading:
                self.heading = _clean(' '.join(self._heading_parts), MAX_TITLE_LENGTH)

    def handle_data(self, data):
        if self._skip or not data:
            return
        text = _WS.sub(' ', data)
        if self._space and text.startswith(' '):
            text = text[1:]
        if not text:
            return
        self.count += len(text)
        self._space = text.endswith(' ')
        if self._in_heading:
            self._heading_parts.append(text)


def _decode(raw):
    """Текст документа — так же, как его увидит ридер.

    epub.js достаёт главы через JSZip, а тот читает их ТОЛЬКО как UTF-8 (объявление
    encoding в XML не смотрит). Поэтому и здесь UTF-8, а битые байты — знаком
    замены, как в ридере. Перебирать «utf-16 → cp1251» нельзя: UTF-16 без метки
    молча «читает» почти любой набор байтов чётной длины, и глава превращалась
    в иероглифы — половина знаков, ни якорей, ни заголовка. UTF-16 — только по
    метке порядка байтов.
    """
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16', errors='replace')
    return raw.decode('utf-8-sig', errors='replace')


def _count_text(raw):
    """(знаков, якоря, первый заголовок) документа главы."""
    text = _decode(raw)
    counter = _TextCounter()
    try:
        counter.feed(text)
        counter.close()
    except Exception:  # noqa: BLE001 — кривая разметка главы не должна ронять книгу
        pass
    return counter.count, counter.anchors, counter.heading


class _Archive:
    """Обёртка над zip с пределами на распаковку."""

    def __init__(self, data):
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, OSError, ValueError, RuntimeError) as exc:
            # RuntimeError — это и NotImplementedError («zip file version 16.9»
            # из испорченного заголовка): такой архив тоже не открыть.
            raise EpubError('Это не файл EPUB: он не открывается как архив книги') from exc
        infos = self.zip.infolist()
        if len(infos) > MAX_ENTRIES:
            raise EpubError('В архиве книги слишком много файлов')
        if sum(max(0, info.file_size) for info in infos) > MAX_UNCOMPRESSED_BYTES:
            raise EpubError('Книга слишком большая в распакованном виде')
        # Имена в zip регистрозависимы, а ссылки в кривых книгах — нет:
        # «Text/Chapter1.xhtml» против «text/chapter1.xhtml». Точное имя
        # ищется первым, запасной путь — без регистра.
        self.names = {info.filename: info for info in infos if not info.is_dir()}
        self.lower = {}
        for name in self.names:
            self.lower.setdefault(name.lower(), name)

    def resolve(self, path):
        path = str(path or '').lstrip('/')
        if path in self.names:
            return path
        return self.lower.get(path.lower())

    def read(self, path, limit=MAX_DOCUMENT_BYTES):
        name = self.resolve(path)
        if not name:
            return None
        info = self.names[name]
        if info.file_size > limit:
            return None
        try:
            with self.zip.open(info) as handle:
                data = handle.read(limit + 1)
        except (zipfile.BadZipFile, OSError, RuntimeError, EOFError, ValueError, zlib.error) as exc:
            # Файл в архиве есть, но не распаковывается (битый поток, пароль).
            # Это не «файла нет»: ридер споткнётся о ту же главу, поэтому книга
            # отклоняется целиком и с понятной причиной, а не пятисоткой.
            raise EpubError('Файл повреждён: часть книги не распаковывается. '
                            'Скачайте книгу заново') from exc
        return data if len(data) <= limit else None


def _join(base_dir, href):
    """Путь внутри архива: href относительно каталога base_dir, без #якоря."""
    path = unquote(str(href or '').split('#', 1)[0])
    joined = posixpath.normpath(posixpath.join(base_dir, path)) if base_dir else posixpath.normpath(path)
    return '' if joined in ('.', '') else joined.lstrip('/')


def _check_drm(archive):
    raw = archive.read('META-INF/encryption.xml', limit=2 * 1024 * 1024)
    if raw is None:
        if archive.resolve('META-INF/rights.xml'):
            raise EpubError('Книга защищена DRM и в браузере не откроется. '
                            'Нужен файл без защиты', code='LIBRARY_EPUB_DRM')
        return
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return
    for node in root.iter():
        if _local(node.tag) == 'EncryptionMethod':
            algorithm = str(node.get('Algorithm') or '').strip()
            if algorithm and algorithm not in FONT_OBFUSCATION:
                raise EpubError('Книга защищена DRM и в браузере не откроется. '
                                'Нужен файл без защиты', code='LIBRARY_EPUB_DRM')


def _rootfile(archive):
    raw = archive.read('META-INF/container.xml', limit=1024 * 1024)
    if raw is None:
        raise EpubError('Это не файл EPUB: в нём нет описания книги (container.xml)')
    root = _parse_xml(raw, 'описание книги (container.xml)')
    for node in root.iter():
        if _local(node.tag) == 'rootfile':
            path = str(node.get('full-path') or '').strip()
            media = str(node.get('media-type') or '').strip()
            if path and (not media or media == 'application/oebps-package+xml'):
                return path
    raise EpubError('Это не файл EPUB: в нём не указан пакет книги')


def _metadata(opf):
    title = ''
    authors = []
    language = ''
    cover_id = ''
    for node in opf.iter():
        name = _local(node.tag)
        if name == 'title' and not title:
            title = _clean(''.join(node.itertext()), MAX_TITLE_LENGTH)
        elif name == 'creator':
            author = _clean(''.join(node.itertext()), MAX_AUTHOR_LENGTH)
            if author and author not in authors:
                authors.append(author)
        elif name == 'language' and not language:
            language = _clean(''.join(node.itertext()), 20)
        elif name == 'meta' and str(node.get('name') or '').strip().lower() == 'cover':
            cover_id = cover_id or str(node.get('content') or '').strip()
    return title, _clean(', '.join(authors), MAX_AUTHOR_LENGTH), language, cover_id


def _manifest(opf, opf_dir):
    items = {}
    for node in opf.iter():
        if _local(node.tag) != 'item':
            continue
        item_id = str(node.get('id') or '').strip()
        href = str(node.get('href') or '').strip()
        if not item_id or not href:
            continue
        items[item_id] = {
            'id': item_id,
            'href': href,
            'path': _join(opf_dir, href),
            'media_type': str(node.get('media-type') or '').strip().lower(),
            'properties': set(str(node.get('properties') or '').split()),
        }
    return items


def _spine(opf, manifest):
    toc_id = ''
    refs = []
    for node in opf.iter():
        name = _local(node.tag)
        if name == 'spine':
            toc_id = str(node.get('toc') or '').strip()
        elif name == 'itemref':
            # Ссылка на несуществующий пункт манифеста НЕ пропускается: epub.js
            # держит её в spine пустой главой, и номера глав (location.start.index)
            # у ридера и здесь обязаны совпадать один в один.
            item = manifest.get(str(node.get('idref') or '').strip())
            # «Линейная» — ровно 'yes', как в epub.js (Section: item.linear === "yes").
            linear = (node.get('linear') or 'yes') == 'yes'
            refs.append({'item': item, 'linear': linear and item is not None})
    return refs, toc_id


def _guide_cover_page(opf, opf_dir):
    for node in opf.iter():
        if _local(node.tag) == 'reference' and str(node.get('type') or '').lower() == 'cover':
            return _join(opf_dir, node.get('href'))
    return ''


_IMG_SRC = re.compile(r'''<(?:img|image)\b[^>]*?(?:\bsrc|xlink:href|\bhref)\s*=\s*["']([^"']+)["']''', re.I)


def _cover(archive, opf, opf_dir, manifest, cover_meta_id, spine_refs):
    """(байты, тип) обложки или None. Порядок — от явного к догадке."""
    by_path = {item['path'].lower(): item for item in manifest.values()}

    def image_item(item):
        return item if item and item['media_type'].startswith('image/') else None

    candidate = next((item for item in manifest.values()
                      if 'cover-image' in item['properties'] and image_item(item)), None)
    if not candidate and cover_meta_id:
        candidate = image_item(manifest.get(cover_meta_id))
        if not candidate:
            # Бывает, что в meta name="cover" лежит не id, а путь к файлу.
            candidate = image_item(by_path.get(_join(opf_dir, cover_meta_id).lower()))
    if not candidate:
        candidate = next((item for item in manifest.values()
                          if image_item(item) and 'cover' in (item['id'] + ' ' + item['href']).lower()), None)
    if not candidate:
        # Страница обложки (из guide или первая в spine): первая картинка на ней.
        pages = [_guide_cover_page(opf, opf_dir)]
        pages.extend(ref['item']['path'] for ref in spine_refs[:1] if ref['item'])
        for page in filter(None, pages):
            raw = archive.read(page, limit=2 * 1024 * 1024)
            if not raw:
                continue
            match = _IMG_SRC.search(_decode(raw))
            if match:
                path = _join(posixpath.dirname(page), match.group(1))
                candidate = image_item(by_path.get(path.lower()))
                if candidate:
                    break
    if not candidate:
        return None
    data = archive.read(candidate['path'], limit=MAX_COVER_BYTES)
    if not data:
        return None
    return data, candidate['media_type']


def _nav_toc(archive, manifest):
    """Оглавление EPUB 3 (документ nav): [(заголовок, путь, якорь, глубина)]."""
    nav_item = next((item for item in manifest.values() if 'nav' in item['properties']), None)
    if not nav_item:
        return []
    raw = archive.read(nav_item['path'], limit=5 * 1024 * 1024)
    if not raw:
        return []
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return []
    navs = [node for node in root.iter() if _local(node.tag) == 'nav']
    toc_nav = next((node for node in navs
                    if 'toc' in str(node.get(f'{{{_OPS_NS}}}type') or node.get('epub:type') or '').split()), None)
    toc_nav = toc_nav if toc_nav is not None else (navs[0] if navs else None)
    if toc_nav is None:
        return []
    base = posixpath.dirname(nav_item['path'])
    entries = []

    def walk(ol, depth):
        for li in ol:
            if _local(li.tag) != 'li':
                continue
            label, href = '', ''
            for child in li:
                name = _local(child.tag)
                if name in ('a', 'span') and not label:
                    label = _clean(''.join(child.itertext()), MAX_TITLE_LENGTH)
                    href = str(child.get('href') or '').strip()
            if label:
                entries.append((label, href, base, depth))
            for child in li:
                if _local(child.tag) == 'ol':
                    walk(child, depth + 1)

    for child in toc_nav.iter():
        if _local(child.tag) == 'ol':
            walk(child, 0)
            break
    return entries


def _ncx_toc(archive, manifest, toc_id):
    """Оглавление EPUB 2 (toc.ncx)."""
    item = manifest.get(toc_id) if toc_id else None
    if not item:
        item = next((entry for entry in manifest.values()
                     if entry['media_type'] == 'application/x-dtbncx+xml'), None)
    if not item:
        return []
    raw = archive.read(item['path'], limit=5 * 1024 * 1024)
    if not raw:
        return []
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return []
    base = posixpath.dirname(item['path'])
    entries = []

    def walk(node, depth):
        for point in node:
            if _local(point.tag) != 'navPoint':
                continue
            label, href = '', ''
            for child in point:
                name = _local(child.tag)
                if name == 'navLabel':
                    label = _clean(''.join(child.itertext()), MAX_TITLE_LENGTH)
                elif name == 'content':
                    href = str(child.get('src') or '').strip()
            if label:
                entries.append((label, href, base, depth))
            walk(point, depth + 1)

    nav_map = next((node for node in root.iter() if _local(node.tag) == 'navMap'), None)
    if nav_map is not None:
        walk(nav_map, 0)
    return entries


def page_for_offset(offset, total_chars):
    """Номер страницы (с единицы) для смещения в знаках.

    Та же формула живёт в ридере (src/components/library/libraryMeta.js:
    pageForOffset) — тест сверяет их, иначе номер в оглавлении и номер внизу
    экрана разошлись бы на единицу.
    """
    total_pages = pages_for_chars(total_chars)
    page = int(max(0, offset) // CHARS_PER_PAGE) + 1
    return max(1, min(total_pages, page))


def pages_for_chars(total_chars):
    total_chars = max(0, int(total_chars or 0))
    return max(1, -(-total_chars // CHARS_PER_PAGE))


def parse_epub(data, *, filename=''):
    """Разбирает книгу. Бросает EpubError с причиной, понятной человеку."""
    if not data:
        raise EpubError('Файл пустой')
    if len(data) > MAX_EPUB_BYTES:
        raise EpubError(f'Файл больше {MAX_EPUB_BYTES // (1024 * 1024)} МБ', code='LIBRARY_EPUB_TOO_LARGE')
    archive = _Archive(data)
    _check_drm(archive)

    opf_path = archive.resolve(_rootfile(archive))
    raw_opf = archive.read(opf_path, limit=10 * 1024 * 1024) if opf_path else None
    if raw_opf is None:
        raise EpubError('Файл повреждён: нет пакета книги (OPF)')
    opf = _parse_xml(raw_opf, 'пакет книги (OPF)')
    opf_dir = posixpath.dirname(opf_path)

    title, author, language, cover_meta_id = _metadata(opf)
    manifest = _manifest(opf, opf_dir)
    spine_refs, toc_id = _spine(opf, manifest)
    if not any(ref['item'] for ref in spine_refs):
        raise EpubError('В книге нет ни одной главы для чтения')
    if len(spine_refs) > MAX_SPINE_ITEMS:
        raise EpubError('В книге слишком много глав')

    # Главы: знаки, якоря, смещение начала. Нелинейные (linear="no" — обычно
    # сноски и отдельная страница обложки) ридер при листании пропускает,
    # поэтому в сквозной счёт страниц они не входят.
    spine = []
    anchors_by_index = []
    headings = []
    offset = 0
    path_to_index = {}
    # Одна глава может стоять в spine несколько раз — разбираем её один раз.
    counted = {}
    for index, ref in enumerate(spine_refs):
        item = ref['item']
        if item and item['path'] not in counted:
            raw = archive.read(item['path'])
            counted[item['path']] = _count_text(raw) if raw is not None else (0, {}, '')
        chars, anchors, heading = counted[item['path']] if item else (0, {}, '')
        start = offset
        if ref['linear']:
            offset += chars
        spine.append({
            'href': item['href'] if item else '',
            'chars': chars if ref['linear'] else 0,
            'start': start,
            'linear': ref['linear'],
        })
        anchors_by_index.append(anchors)
        headings.append(heading)
        if item:
            path_to_index.setdefault(item['path'].lower(), index)
    total_chars = offset
    if total_chars <= 0:
        raise EpubError('В книге не нашлось текста — похоже, это сканы страниц. '
                        'Такую книгу в библиотеку загрузить нельзя')
    if total_chars > MAX_TOTAL_CHARS:
        raise EpubError('Книга слишком большая')
    total_pages = pages_for_chars(total_chars)

    raw_toc = _nav_toc(archive, manifest) or _ncx_toc(archive, manifest, toc_id)
    toc = []
    for label, href, base, depth in raw_toc[:MAX_TOC_ITEMS]:
        path = _join(base, href)
        fragment = unquote(href.split('#', 1)[1]) if '#' in href else ''
        index = path_to_index.get(path.lower())
        if index is None:
            continue
        entry_offset = spine[index]['start'] + anchors_by_index[index].get(fragment, 0)
        # Адрес для ридера — href главы ТОЧНО как в манифесте: epub.js ищет
        # главу по нему (Spine.spineByHref), а оглавление пишет пути
        # относительно своего файла, который лежит не всегда рядом с OPF.
        target = spine[index]['href'] + (f'#{fragment}' if fragment else '')
        toc.append({
            'title': label,
            'href': target,
            'depth': min(depth, 6),
            'page': page_for_offset(entry_offset, total_chars),
            # Глава и якорь — куда вести ридер (номер главы в том же порядке,
            # что у ридера: каждый itemref, даже пустой).
            'spine': index,
            'anchor': fragment,
            # Точное место пункта: по нему ридер понимает, в какой главе
            # читатель, — по странице нельзя, в начале книги на первой
            # странице сидят и титул, и содержание, и первая глава.
            'offset': entry_offset,
        })
    if not toc:
        # Оглавления нет или оно ведёт мимо глав — собираем из самих глав:
        # заголовок первого уровня, а если его нет — «Часть N».
        number = 0
        for index, entry in enumerate(spine):
            if not entry['linear'] or entry['chars'] <= 0:
                continue
            number += 1
            toc.append({
                'title': headings[index] or f'Часть {number}',
                'href': entry['href'],
                'depth': 0,
                'page': page_for_offset(entry['start'], total_chars),
                'spine': index,
                'anchor': '',
                'offset': entry['start'],
            })

    fallback_title = re.sub(r'\.epub$', '', posixpath.basename(str(filename or '')), flags=re.I)
    cover = _cover(archive, opf, opf_dir, manifest, cover_meta_id, spine_refs)
    return {
        'title': title or _clean(fallback_title, MAX_TITLE_LENGTH) or 'Без названия',
        'author': author,
        'language': language,
        'cover': cover,
        'spine': spine,
        'toc': toc,
        'total_chars': total_chars,
        'total_pages': total_pages,
    }
