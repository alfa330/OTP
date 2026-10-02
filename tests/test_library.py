"""«Библиотека» (задача #282): разбор EPUB, права, прогресс и проводка раздела.

Что держат тесты:

- разбор книги: номер главы у сервера совпадает с номером главы у epub.js
  (иначе страница внизу экрана врёт), адреса оглавления — те, по которым
  epub.js находит главу, страницы — 1800 знаков;
- отказы: не архив, DRM, пустая книга — понятной причиной, а не пятисоткой;
- права по ТЗ буквально: загрузка, удаление и мониторинг — только супер-админ
  и тренер;
- отделы: книга выдаётся одному или нескольким отделам, без отдела её не
  загрузить, читатель чужую по отделу и архивную книгу не откроет ни одной
  ручкой; удалить насовсем — только из архива;
- прогресс: 100 % и «Закончено» ставит только последняя страница;
- формула страницы одна на сервере и в ридере;
- раздел доходит до меню и до тренера;
- жанры: справочник ведут только управляющие, имя уникально без регистра,
  у книги их сколько угодно (и ни одного), неверный жанр — отказ до бакета;
- отделы к выдаче — пока только СЗоВ и ОП (решение владельца 02.10.2026).

Книги в тестах собираются здесь же из строк: реальные файлы книг в публичный
репозиторий не кладём.
"""

import contextlib
import io
import re
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest import mock

from library import epub, queries, routes, storage
from library.epub import CHARS_PER_PAGE, EpubError, page_for_offset, pages_for_chars, parse_epub

ROOT = Path(__file__).resolve().parents[1]

CONTAINER = (
    '<?xml version="1.0"?><container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
    '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
    '</rootfiles></container>'
)


def build_epub(files, *, container=CONTAINER):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        if container is not None:
            archive.writestr('META-INF/container.xml', container)
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def opf(manifest, spine, *, metadata='', spine_attrs=''):
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'{metadata or "<dc:title>Книга</dc:title><dc:creator>Автор</dc:creator><dc:language>ru</dc:language>"}'
        f'</metadata><manifest>{manifest}</manifest><spine{spine_attrs}>{spine}</spine></package>'
    )


def chapter(body):
    return f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>служебное</title><style>p{{}}</style></head><body>{body}</body></html>'


def png_bytes():
    # Минимальный валидный PNG 1×1 — без Pillow, чтобы тест не зависел от него.
    return bytes.fromhex(
        '89504e470d0a1a0a0000000d4948445200000001000000010806000000'
        '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082')


def epub3_book():
    manifest = (
        '<item id="nav" href="nav/toc.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '<item id="cover" href="images/cover.png" media-type="image/png" properties="cover-image"/>'
        '<item id="c1" href="Text/ch1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="notes" href="Text/notes.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c2" href="Text/ch%202.xhtml" media-type="application/xhtml+xml"/>'
    )
    spine = ('<itemref idref="c1"/><itemref idref="notes" linear="no"/>'
             '<itemref idref="ghost"/><itemref idref="c2"/>')
    # Оглавление лежит в подкаталоге nav/ — пути в нём относительные к НЕМУ.
    nav = (
        '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops"><body>'
        '<nav epub:type="landmarks"><ol><li><a href="../Text/ch1.xhtml">Ориентиры</a></li></ol></nav>'
        '<nav epub:type="toc"><ol>'
        '<li><a href="../Text/ch1.xhtml">Глава первая</a>'
        '<ol><li><a href="../Text/ch1.xhtml#part2">Вторая часть главы</a></li></ol></li>'
        '<li><a href="../Text/ch%202.xhtml">Глава вторая</a></li>'
        '</ol></nav></body></html>'
    )
    return build_epub({
        'OEBPS/content.opf': opf(manifest, spine),
        'OEBPS/nav/toc.xhtml': nav,
        'OEBPS/images/cover.png': png_bytes(),
        'OEBPS/Text/ch1.xhtml': chapter('<h1>Глава первая</h1><p>' + 'а' * 3000 + '</p>'
                                        '<p id="part2">' + 'б' * 2000 + '</p>'),
        'OEBPS/Text/notes.xhtml': chapter('<p>' + 'сноска ' * 900 + '</p>'),
        'OEBPS/Text/ch 2.xhtml': chapter('<h2>Глава вторая</h2><p>' + 'в   \n  ' * 1000 + '</p>'),
    })


class EpubParseTests(unittest.TestCase):
    def setUp(self):
        self.book = parse_epub(epub3_book(), filename='книга.epub')

    def test_metadata_and_cover(self):
        self.assertEqual('Книга', self.book['title'])
        self.assertEqual('Автор', self.book['author'])
        self.assertEqual('ru', self.book['language'])
        data, content_type = self.book['cover']
        self.assertEqual('image/png', content_type)
        self.assertTrue(data.startswith(b'\x89PNG'))

    def test_spine_keeps_epubjs_indexes(self):
        """epub.js держит в spine и нелинейную главу, и ссылку в пустоту;
        location.start.index обязан указывать на ту же главу у сервера."""
        spine = self.book['spine']
        self.assertEqual(4, len(spine))
        self.assertEqual(['Text/ch1.xhtml', 'Text/notes.xhtml', '', 'Text/ch%202.xhtml'],
                         [item['href'] for item in spine])
        self.assertEqual([True, False, False, True], [item['linear'] for item in spine])

    def test_nonlinear_chapters_do_not_count_toward_pages(self):
        spine = self.book['spine']
        first = len('Глава первая') + 3000 + 2000
        self.assertEqual(first, spine[0]['chars'])
        self.assertEqual(0, spine[1]['chars'])
        self.assertEqual(first, spine[3]['start'])
        self.assertEqual(first + spine[3]['chars'], self.book['total_chars'])

    def test_whitespace_collapses_like_the_browser(self):
        self.assertEqual(len('Глава вторая') + len('в ' * 1000), self.book['spine'][3]['chars'])

    def test_toc_is_resolved_against_manifest_hrefs(self):
        """Оглавление лежит в nav/, а epub.js ищет главу по href манифеста."""
        toc = self.book['toc']
        self.assertEqual(['Глава первая', 'Вторая часть главы', 'Глава вторая'],
                         [item['title'] for item in toc])
        self.assertEqual(['Text/ch1.xhtml', 'Text/ch1.xhtml#part2', 'Text/ch%202.xhtml'],
                         [item['href'] for item in toc])
        self.assertEqual([0, 1, 0], [item['depth'] for item in toc])
        self.assertEqual([0, len('Глава первая') + 3000, self.book['spine'][3]['start']],
                         [item['offset'] for item in toc])
        # Куда вести ридер: номер главы (в порядке spine, с пустыми) и якорь.
        self.assertEqual([(0, ''), (0, 'part2'), (3, '')], [(item['spine'], item['anchor']) for item in toc])

    def test_toc_pages_follow_anchors(self):
        toc = self.book['toc']
        anchor_offset = len('Глава первая') + 3000
        self.assertEqual(1, toc[0]['page'])
        self.assertEqual(anchor_offset // CHARS_PER_PAGE + 1, toc[1]['page'])
        self.assertEqual(self.book['spine'][3]['start'] // CHARS_PER_PAGE + 1, toc[2]['page'])
        self.assertEqual(pages_for_chars(self.book['total_chars']), self.book['total_pages'])

    def test_epub2_ncx_and_cover_meta(self):
        manifest = (
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            '<item id="my-cover" href="img/c.png" media-type="image/png"/>'
            '<item id="c1" href="c1.html" media-type="application/xhtml+xml"/>'
        )
        ncx = (
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
            '<navPoint id="p1"><navLabel><text>Начало</text></navLabel><content src="c1.html"/>'
            '<navPoint id="p2"><navLabel><text>Дальше</text></navLabel><content src="c1.html#far"/></navPoint>'
            '</navPoint></navMap></ncx>'
        )
        data = build_epub({
            'OEBPS/content.opf': opf(
                manifest, '<itemref idref="c1"/>', spine_attrs=' toc="ncx"',
                metadata='<dc:title>Старый формат</dc:title><meta name="cover" content="my-cover"/>'),
            'OEBPS/toc.ncx': ncx,
            'OEBPS/img/c.png': png_bytes(),
            'OEBPS/c1.html': chapter('<p>' + 'x' * 4000 + '<a id="far"/></p><p>' + 'y' * 10 + '</p>'),
        })
        book = parse_epub(data)
        self.assertEqual('Старый формат', book['title'])
        self.assertEqual('', book['author'])
        self.assertEqual('image/png', book['cover'][1])
        self.assertEqual([('Начало', 'c1.html', 0, 1), ('Дальше', 'c1.html#far', 1, 3)],
                         [(i['title'], i['href'], i['depth'], i['page']) for i in book['toc']])

    def test_toc_falls_back_to_chapter_headings(self):
        manifest = (
            '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="b" href="b.xhtml" media-type="application/xhtml+xml"/>'
        )
        data = build_epub({
            'OEBPS/content.opf': opf(manifest, '<itemref idref="a"/><itemref idref="b"/>'),
            'OEBPS/a.xhtml': chapter('<h1>Пролог</h1><p>' + 'т' * 2000 + '</p>'),
            'OEBPS/b.xhtml': chapter('<p>' + 'т' * 100 + '</p>'),
        })
        book = parse_epub(data, filename='Без названия.epub')
        self.assertEqual([('Пролог', 'a.xhtml', 1), ('Часть 2', 'b.xhtml', 2)],
                         [(i['title'], i['href'], i['page']) for i in book['toc']])

    def test_title_falls_back_to_file_name(self):
        manifest = '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
        data = build_epub({
            'OEBPS/content.opf': opf(manifest, '<itemref idref="a"/>', metadata='<dc:language>ru</dc:language>'),
            'OEBPS/a.xhtml': chapter('<p>текст</p>'),
        })
        self.assertEqual('Мёртвые души', parse_epub(data, filename='C:/книги/Мёртвые души.epub')['title'])

    def test_rejections_have_reasons(self):
        manifest = '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
        cases = {
            'not zip': (b'PK?? definitely not an archive', 'LIBRARY_EPUB_INVALID'),
            'no container': (build_epub({'OEBPS/a.xhtml': 'x'}, container=None), 'LIBRARY_EPUB_INVALID'),
            'no text': (build_epub({
                'OEBPS/content.opf': opf(manifest, '<itemref idref="a"/>'),
                'OEBPS/a.xhtml': chapter('<img src="scan.jpg"/>'),
            }), 'LIBRARY_EPUB_INVALID'),
            'drm': (build_epub({
                'OEBPS/content.opf': opf(manifest, '<itemref idref="a"/>'),
                'OEBPS/a.xhtml': chapter('<p>текст</p>'),
                'META-INF/encryption.xml': (
                    '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#">'
                    '<EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/>'
                    '</EncryptedData></encryption>'),
            }), 'LIBRARY_EPUB_DRM'),
        }
        for name, (data, code) in cases.items():
            with self.subTest(name):
                with self.assertRaises(EpubError) as caught:
                    parse_epub(data)
                self.assertEqual(code, caught.exception.code)
                self.assertTrue(caught.exception.message)

    def test_font_obfuscation_is_not_drm(self):
        manifest = '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
        data = build_epub({
            'OEBPS/content.opf': opf(manifest, '<itemref idref="a"/>'),
            'OEBPS/a.xhtml': chapter('<p>текст</p>'),
            'META-INF/encryption.xml': (
                '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#">'
                '<EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>'
                '</EncryptedData></encryption>'),
        })
        self.assertEqual('Книга', parse_epub(data)['title'])

    def test_damaged_chapter_is_a_reason_not_a_500(self):
        """Битый поток deflate внутри главы — zlib.error, а не OSError."""
        buffer = io.BytesIO()
        chapter_text = chapter('<p>' + 'текст главы ' * 400 + '</p>')
        manifest = '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('META-INF/container.xml', CONTAINER)
            archive.writestr('OEBPS/content.opf', opf(manifest, '<itemref idref="a"/>'))
            archive.writestr('OEBPS/a.xhtml', chapter_text)
        data = bytearray(buffer.getvalue())
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
            info = archive.getinfo('OEBPS/a.xhtml')
        start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        for shift in (5, 17, 40):
            broken = bytearray(data)
            broken[start + shift] ^= 0xFF
            with self.subTest(shift=shift):
                with self.assertRaises(EpubError) as caught:
                    parse_epub(bytes(broken))
                self.assertEqual('LIBRARY_EPUB_INVALID', caught.exception.code)

    def test_text_is_decoded_like_the_reader_does(self):
        """JSZip в ридере читает главы только как UTF-8. Один чужой байт не
        должен превращать главу в «UTF-16» с половиной знаков и без якорей."""
        manifest = '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
        body = ('<h1>Глава 1</h1><p>' + 'Русский текст главы. ' * 200 + '</p><p id="mid">конец</p>').encode('utf-8')
        for tail in (b'\xa0', b'\xa0\xab'):  # нечётная и чётная длина
            raw = b'<html><body>' + body + tail + b'</body></html>'
            data = build_epub({'OEBPS/content.opf': opf(manifest, '<itemref idref="a"/>'), 'OEBPS/a.xhtml': raw})
            with self.subTest(length_parity=len(raw) % 2):
                book = parse_epub(data)
                expected = len('Глава 1') + len('Русский текст главы. ' * 200) + len('конец')
                self.assertAlmostEqual(expected, book['spine'][0]['chars'], delta=5)
                self.assertEqual('Глава 1', book['toc'][0]['title'])

    def test_repeated_chapter_is_parsed_once(self):
        manifest = '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
        data = build_epub({
            'OEBPS/content.opf': opf(manifest, '<itemref idref="a"/>' * 5),
            'OEBPS/a.xhtml': chapter('<p>' + 'т' * 100 + '</p>'),
        })
        with mock.patch.object(epub, '_count_text', wraps=epub._count_text) as counter:
            book = parse_epub(data)
        self.assertEqual(1, counter.call_count)
        self.assertEqual(5, len(book['spine']))
        self.assertEqual(500, book['total_chars'])

    def test_spine_and_text_size_are_capped(self):
        manifest = '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
        data = build_epub({
            'OEBPS/content.opf': opf(manifest, '<itemref idref="a"/>' * 3),
            'OEBPS/a.xhtml': chapter('<p>' + 'т' * 100 + '</p>'),
        })
        with mock.patch.object(epub, 'MAX_SPINE_ITEMS', 2), self.assertRaises(EpubError):
            parse_epub(data)
        with mock.patch.object(epub, 'MAX_TOTAL_CHARS', 250), self.assertRaises(EpubError):
            parse_epub(data)

    def test_scanned_book_message_has_no_dead_end_advice(self):
        manifest = '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
        data = build_epub({
            'OEBPS/content.opf': opf(manifest, '<itemref idref="a"/>'),
            'OEBPS/a.xhtml': chapter('<img src="scan.jpg"/>'),
        })
        with self.assertRaises(EpubError) as caught:
            parse_epub(data)
        self.assertNotIn('PDF', caught.exception.message)

    def test_oversized_file_is_rejected_before_unzipping(self):
        with mock.patch.object(epub, 'MAX_EPUB_BYTES', 10):
            with self.assertRaises(EpubError) as caught:
                parse_epub(b'x' * 11)
        self.assertEqual('LIBRARY_EPUB_TOO_LARGE', caught.exception.code)

    def test_unpacked_size_is_capped(self):
        data = build_epub({'OEBPS/big.txt': 'a' * 5000})
        with mock.patch.object(epub, 'MAX_UNCOMPRESSED_BYTES', 1000):
            with self.assertRaises(EpubError):
                parse_epub(data)


# Одна и та же таблица — здесь и в tests/library_meta.test.mjs.
PAGE_CASES = (
    # (смещение, всего знаков, страница)
    (0, 5000, 1),
    (1799, 5000, 1),
    (1800, 5000, 2),
    (4999, 5000, 3),
    (5000, 5000, 3),
    (99999, 5000, 3),
    (-5, 5000, 1),
    (0, 0, 1),
)


class PageFormulaTests(unittest.TestCase):
    def test_page_table(self):
        for offset, total, page in PAGE_CASES:
            with self.subTest(offset=offset, total=total):
                self.assertEqual(page, page_for_offset(offset, total))

    def test_reader_uses_the_same_formula(self):
        """Номер в оглавлении считает сервер, номер внизу экрана — ридер."""
        source = (ROOT / 'src/components/library/libraryMeta.js').read_text(encoding='utf-8')
        self.assertIn('Math.floor(Math.max(0, Number(offset) || 0) / perPage) + 1', source)
        self.assertIn(f'|| {CHARS_PER_PAGE}', source)
        test_source = (ROOT / 'tests/library_meta.test.mjs').read_text(encoding='utf-8')
        for offset, total, page in PAGE_CASES:
            self.assertIn(f'[{offset}, {total}, {page}]', test_source)


class FakeDb:
    def __init__(self):
        self.cursors = 0

    @contextlib.contextmanager
    def _get_cursor(self):
        self.cursors += 1
        yield object()


class FakeBlob:
    def __init__(self, store, bucket, path):
        self.store, self.key = store, (bucket, path)
        self.cache_control = None

    def upload_from_string(self, data, content_type=None):
        self.store.uploaded[self.key] = (data, content_type)

    def download_as_bytes(self):
        if self.key not in self.store.uploaded:
            raise Exception('404 No such object')
        return self.store.uploaded[self.key][0]

    def delete(self):
        self.store.deleted.append(self.key)

    def generate_signed_url(self, **kwargs):
        return f'https://storage.example/{self.key[1]}?sig=1'


class FakeGcs:
    def __init__(self):
        self.uploaded = {}
        self.deleted = []

    def bucket(self, name):
        store = self

        class _Bucket:
            def blob(self, path):
                return FakeBlob(store, name, path)

        return _Bucket()


def client_for(role, *, gcs=None, requester_id=7):
    from flask import Flask

    fake = gcs if gcs is not None else FakeGcs()
    app = Flask(__name__)
    app.register_blueprint(routes.build_library_blueprint(
        db=FakeDb(),
        require_api_key=lambda f: f,
        build_cors_preflight_response=lambda: ('', 204),
        resolve_requester=lambda: (requester_id, (requester_id, None, 'Тест', role), None),
        normalize_role=lambda value: str(value or '').strip().lower(),
        gcs={'bucket_name': lambda: 'test-bucket', 'client': lambda: fake},
    ))
    return app.test_client(), fake


def book_row(**extra):
    row = {
        'id': 5, 'title': 'Книга', 'author': 'Автор', 'language': 'ru', 'bucket': 'test-bucket',
        'cover_blob': None, 'cover_type': None, 'total_pages': 285, 'created_at': None,
        'archived_at': None, 'department_ids': [1, 3], 'genre_ids': [2],
        'saved': False, 'percent': None, 'page': None, 'finished_at': None, 'progress_updated_at': None,
    }
    row.update(extra)
    return row


class PermissionTests(unittest.TestCase):
    """ТЗ: «Супер-админ / Тренер» — буквально, без админов и глав отделов."""

    def test_manager_roles_are_exactly_super_admin_and_trainer(self):
        self.assertEqual(frozenset({'super_admin', 'trainer'}), routes.MANAGER_ROLES)

    def test_catalog_reports_manage_right_by_role(self):
        departments = [{'id': 1, 'name': 'СЗоВ', 'active': True}]
        for role in ('trainer', 'super_admin'):
            with self.subTest(role=role), \
                    mock.patch.object(routes, 'schema_is_ready', return_value=True), \
                    mock.patch.object(queries, 'list_books', return_value=[book_row()]) as list_books, \
                    mock.patch.object(queries, 'list_genres', return_value=[{'id': 2, 'name': 'Психология'}]), \
                    mock.patch.object(queries, 'library_departments', return_value=departments):
                client, _ = client_for(role)
                response = client.get('/api/library')
                self.assertEqual(200, response.status_code)
                body = response.get_json()
                self.assertIs(True, body['can_manage'])
                self.assertEqual('not_started', body['books'][0]['progress']['status'])
                # Управляющему — все книги всех отделов и список отделов для выбора.
                self.assertIs(True, list_books.call_args.kwargs['manager'])
                self.assertEqual(departments, body['departments'])
                self.assertEqual([1, 3], body['books'][0]['department_ids'])
                self.assertEqual([2], body['books'][0]['genre_ids'])
                self.assertEqual([{'id': 2, 'name': 'Психология'}], body['genres'])
                self.assertIs(False, body['books'][0]['archived'])

    def test_section_is_closed_to_everyone_else_for_now(self):
        """Решение владельца 28.09.2026: пока раздел только у супер-админа и
        тренера — остальным закрыта каждая ручка, не только управление."""
        self.assertEqual(routes.MANAGER_ROLES, routes.READER_ROLES)
        requests = (
            ('get', '/api/library', {}),
            ('get', '/api/library/books/5', {}),
            ('get', '/api/library/books/5/file', {}),
            ('put', '/api/library/books/5/progress', {'json': {'position': '0:0', 'percent': 1, 'page': 1}}),
            ('put', '/api/library/books/5/saved', {'json': {'saved': True}}),
        )
        for role in ('operator', 'trainee', 'sv', 'admin', 'head', ''):
            client, _ = client_for(role)
            for method, url, kwargs in requests:
                with self.subTest(role=role, url=url):
                    response = getattr(client, method)(url, **kwargs)
                    self.assertEqual(403, response.status_code)
                    self.assertEqual('LIBRARY_FORBIDDEN', response.get_json()['code'])

    def test_upload_delete_and_monitoring_are_closed_to_readers(self):
        # Даже когда раздел откроют читателям (READER_ROLES шире) — управление
        # остаётся за супер-админом и тренером.
        with mock.patch.object(routes, 'READER_ROLES', frozenset({'operator', 'trainee', 'sv', 'admin'})):
            for role in ('operator', 'trainee', 'sv', 'admin'):
                with self.subTest(role=role):
                    client, _ = client_for(role)
                    self.assertEqual(403, client.post('/api/library/books', data={}).status_code)
                    self.assertEqual(403, client.patch('/api/library/books/5', json={'archived': True}).status_code)
                    self.assertEqual(403, client.delete('/api/library/books/5').status_code)
                    self.assertEqual(403, client.get('/api/library/analytics').status_code)
                    self.assertEqual(403, client.get('/api/library/analytics/summary').status_code)

    def test_upload_accepts_only_epub_extension(self):
        client, _ = client_for('trainer')
        response = client.post('/api/library/books', data={
            'file': (io.BytesIO(b'%PDF-1.4'), 'книга.pdf'),
        }, content_type='multipart/form-data')
        self.assertEqual(400, response.status_code)
        self.assertEqual('LIBRARY_EPUB_ONLY', response.get_json()['code'])

    def test_broken_epub_answers_400_with_reason(self):
        client, fake = client_for('super_admin')
        with mock.patch.object(queries, 'unknown_departments', return_value=[]):
            response = client.post('/api/library/books', data={
                'file': (io.BytesIO(b'not a zip'), 'book.epub'), 'department_ids': ['1'],
            }, content_type='multipart/form-data')
        self.assertEqual(400, response.status_code)
        self.assertEqual('LIBRARY_EPUB_INVALID', response.get_json()['code'])
        self.assertEqual({}, fake.uploaded)

    def test_upload_stores_file_and_cover_then_row(self):
        inserted = {}

        def insert_book(cursor, **kwargs):
            inserted.update(kwargs)
            return 5

        client, fake = client_for('trainer')
        with mock.patch.object(queries, 'insert_book', side_effect=insert_book), \
                mock.patch.object(queries, 'unknown_departments', return_value=[]), \
                mock.patch.object(queries, 'get_book', return_value=book_row(cover_blob='c', cover_type='image/png')):
            response = client.post('/api/library/books', data={
                'file': (io.BytesIO(epub3_book()), 'book.epub'), 'department_ids': ['3', '1', '3'],
            }, content_type='multipart/form-data')
        self.assertEqual(201, response.status_code, response.get_json())
        self.assertEqual('Книга', inserted['parsed']['title'])
        self.assertEqual(7, inserted['uploaded_by'])
        # Повтор поля формы — несколько отделов; дубли схлопываются.
        self.assertEqual([1, 3], inserted['department_ids'])
        # Жанр — по желанию: без поля книга ложится без жанра.
        self.assertEqual([], inserted['genre_ids'])
        paths = [path for _bucket, path in fake.uploaded]
        self.assertTrue(any(path.startswith('library/books/') for path in paths))
        self.assertTrue(any(path.startswith('library/covers/') for path in paths))
        self.assertEqual('application/epub+zip', fake.uploaded[('test-bucket', inserted['file_blob'])][1])

    def test_failed_insert_removes_uploaded_blobs(self):
        client, fake = client_for('trainer')
        with mock.patch.object(queries, 'insert_book', side_effect=RuntimeError('db down')), \
                mock.patch.object(queries, 'unknown_departments', return_value=[]):
            response = client.post('/api/library/books', data={
                'file': (io.BytesIO(epub3_book()), 'book.epub'), 'department_ids': ['1'],
            }, content_type='multipart/form-data')
        self.assertEqual(500, response.status_code)
        self.assertEqual(sorted(fake.uploaded), sorted(fake.deleted))

    def test_upload_requires_known_departments_before_any_write(self):
        """Без отдела книгу не загрузить, и отказ приходит ДО бакета: файл,
        сохранённый под отказом, остался бы в хранилище навсегда."""
        cases = (
            ({}, 'LIBRARY_DEPARTMENTS_REQUIRED', []),
            ({'department_ids': ['']}, 'LIBRARY_BAD_REQUEST', []),
            ({'department_ids': ['abc']}, 'LIBRARY_BAD_REQUEST', []),
            ({'department_ids': ['-1']}, 'LIBRARY_BAD_REQUEST', []),
            ({'department_ids': ['999']}, 'LIBRARY_DEPARTMENT_UNKNOWN', [999]),
        )
        for extra, code, unknown in cases:
            with self.subTest(extra=extra):
                client, fake = client_for('trainer')
                with mock.patch.object(queries, 'unknown_departments', return_value=unknown), \
                        mock.patch.object(queries, 'insert_book') as insert_book:
                    response = client.post('/api/library/books', data={
                        'file': (io.BytesIO(epub3_book()), 'book.epub'), **extra,
                    }, content_type='multipart/form-data')
                self.assertEqual(400, response.status_code)
                self.assertEqual(code, response.get_json()['code'])
                self.assertEqual({}, fake.uploaded)
                insert_book.assert_not_called()

    def test_delete_drops_blobs_after_the_row(self):
        client, fake = client_for('trainer')
        with mock.patch.object(queries, 'delete_book', return_value=(
                queries.DELETE_DONE, [('test-bucket', 'library/books/a.epub'), ('test-bucket', None)])):
            response = client.delete('/api/library/books/5')
        self.assertEqual(200, response.status_code)
        self.assertEqual([('test-bucket', 'library/books/a.epub')], fake.deleted)

    def test_only_an_archived_book_is_deleted(self):
        client, fake = client_for('trainer')
        with mock.patch.object(queries, 'delete_book', return_value=(queries.DELETE_NOT_ARCHIVED, [])):
            response = client.delete('/api/library/books/5')
        self.assertEqual(409, response.status_code)
        self.assertEqual('LIBRARY_DELETE_NOT_ARCHIVED', response.get_json()['code'])
        self.assertEqual([], fake.deleted)
        with mock.patch.object(queries, 'delete_book', return_value=(queries.DELETE_NOT_FOUND, [])):
            self.assertEqual(404, client.delete('/api/library/books/5').status_code)

    def test_book_file_is_cacheable_and_private(self):
        fake = FakeGcs()
        fake.uploaded[('test-bucket', 'library/books/a.epub')] = (b'PK-epub', 'application/epub+zip')
        client, _ = client_for('trainer', gcs=fake)
        with mock.patch.object(queries, 'open_book', return_value=285), \
                mock.patch.object(queries, 'book_file_ref', return_value={
                    'bucket': 'test-bucket', 'file_blob': 'library/books/a.epub', 'original_name': 'a.epub'}):
            response = client.get('/api/library/books/5/file')
        self.assertEqual(200, response.status_code)
        self.assertEqual(b'PK-epub', response.data)
        self.assertEqual('application/epub+zip', response.mimetype)
        self.assertIn('private', response.headers['Cache-Control'])


class ProgressTests(unittest.TestCase):
    def save(self, payload, *, total_pages=285):
        captured = {}

        def save_progress(cursor, user_id, book_id, **kwargs):
            captured.update(kwargs, user_id=user_id, book_id=book_id)
            return {'percent': kwargs['percent'], 'page': kwargs['page'],
                    'finished_at': 'x' if kwargs['at_end'] else None, 'progress_updated_at': None}

        client, _ = client_for('trainer')
        with mock.patch.object(queries, 'open_book', return_value=total_pages), \
                mock.patch.object(queries, 'save_progress', side_effect=save_progress), \
                mock.patch.object(queries, '_iso', side_effect=lambda value: value):
            response = client.put('/api/library/books/5/progress', json=payload)
        return response, captured

    def test_percent_stops_short_of_100_without_the_last_page(self):
        response, captured = self.save({'position': '3:0.25', 'percent': 100, 'page': 999})
        self.assertEqual(200, response.status_code)
        self.assertEqual(99.9, captured['percent'])
        self.assertEqual(285, captured['page'])
        self.assertFalse(captured['at_end'])
        self.assertEqual('in_progress', response.get_json()['progress']['status'])

    def test_last_page_finishes_the_book(self):
        response, captured = self.save({'position': '19:1', 'percent': 97.3, 'page': 280, 'at_end': True})
        self.assertEqual(100.0, captured['percent'])
        self.assertEqual(285, captured['page'])
        self.assertTrue(captured['at_end'])
        body = response.get_json()['progress']
        self.assertEqual('finished', body['status'])
        self.assertEqual(100.0, body['percent'])

    def test_bad_payloads(self):
        for payload in ({'position': 'javascript:1', 'percent': 1, 'page': 1},
                        {'position': '3:1.5', 'percent': 1, 'page': 1},
                        {'position': '3:0.' + '1' * 40, 'percent': 1, 'page': 1},
                        {'position': '-1:0.5', 'percent': 1, 'page': 1},
                        {'position': 'epubcfi(/6/2)', 'percent': 1, 'page': 1},
                        {'position': '3:0.5', 'percent': 'abc', 'page': 1},
                        {'position': '3:0.5', 'percent': 'nan', 'page': 1}):
            with self.subTest(payload=str(payload)[:40]):
                response, captured = self.save(payload)
                self.assertEqual(400, response.status_code)
                self.assertEqual({}, captured)

    def test_unknown_book(self):
        response, _ = self.save({'position': '0:0', 'percent': 1, 'page': 1}, total_pages=None)
        self.assertEqual(404, response.status_code)

    def test_saved_flag_must_be_boolean(self):
        client, _ = client_for('trainer')
        self.assertEqual(400, client.put('/api/library/books/5/saved', json={'saved': 'yes'}).status_code)
        with mock.patch.object(queries, 'open_book', return_value=285), \
                mock.patch.object(queries, 'set_saved') as set_saved:
            response = client.put('/api/library/books/5/saved', json={'saved': True})
        self.assertEqual({'status': 'success', 'saved': True}, response.get_json())
        set_saved.assert_called_once()


class DepartmentAccessTests(unittest.TestCase):
    """Читатель видит книги только своего отдела и не из архива — и каталогом,
    и каждой ручкой одной книги. Раздел пока закрыт для всех, кроме
    управляющих, поэтому дверь читателя проверяется с расширенным
    READER_ROLES — так, как её откроют."""

    def setUp(self):
        patcher = mock.patch.object(routes, 'READER_ROLES', frozenset({'super_admin', 'trainer', 'operator'}))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reader_catalog_is_filtered_by_the_server_and_has_no_department_list(self):
        genres = [{'id': 2, 'name': 'Психология'}]
        with mock.patch.object(routes, 'schema_is_ready', return_value=True), \
                mock.patch.object(queries, 'list_books', return_value=[]) as list_books, \
                mock.patch.object(queries, 'list_genres', return_value=genres), \
                mock.patch.object(queries, 'library_departments') as library_departments:
            client, _ = client_for('operator')
            body = client.get('/api/library').get_json()
        self.assertIs(False, list_books.call_args.kwargs['manager'])
        library_departments.assert_not_called()
        self.assertEqual([], body['departments'])
        self.assertIs(False, body['can_manage'])
        # Жанры — и читателю: по ним делится его полка.
        self.assertEqual(genres, body['genres'])

    def test_every_book_door_answers_404_for_a_book_of_another_department(self):
        requests = (
            ('get', '/api/library/books/5', {}),
            ('get', '/api/library/books/5/file', {}),
            ('put', '/api/library/books/5/saved', {'json': {'saved': True}}),
            ('put', '/api/library/books/5/progress', {'json': {'position': '0:0', 'percent': 1, 'page': 1}}),
        )
        for method, url, kwargs in requests:
            with self.subTest(url=url), \
                    mock.patch.object(queries, 'open_book', return_value=None) as open_book, \
                    mock.patch.object(queries, 'get_book') as get_book, \
                    mock.patch.object(queries, 'book_file_ref') as book_file_ref, \
                    mock.patch.object(queries, 'set_saved') as set_saved, \
                    mock.patch.object(queries, 'save_progress') as save_progress:
                client, _ = client_for('operator')
                response = getattr(client, method)(url, **kwargs)
                self.assertEqual(404, response.status_code)
                self.assertEqual('LIBRARY_BOOK_NOT_FOUND', response.get_json()['code'])
                self.assertEqual({'manager': False}, open_book.call_args.kwargs)
                for untouched in (get_book, book_file_ref, set_saved, save_progress):
                    untouched.assert_not_called()

    def test_reader_sql_hides_archive_and_other_departments(self):
        text = ' '.join(queries._READER_SEES.split())
        self.assertIn('b.archived_at IS NULL', text)
        self.assertIn('viewer.department_id = bd.department_id', text)
        self.assertIn('viewer.id = %s', text)


class BookUpdateTests(unittest.TestCase):
    def patch(self, payload, *, unknown=(), found=True):
        client, _ = client_for('trainer')
        with mock.patch.object(queries, 'unknown_departments', return_value=list(unknown)), \
                mock.patch.object(queries, 'update_book', return_value=found) as update_book, \
                mock.patch.object(queries, 'get_book', return_value=book_row(archived_at=datetime(2026, 9, 29, 12, 0))):
            response = client.patch('/api/library/books/5', json=payload)
        return response, update_book

    def test_departments_and_archive_change_in_one_request(self):
        response, update_book = self.patch({'department_ids': [3, 1], 'archived': True})
        self.assertEqual(200, response.status_code, response.get_json())
        self.assertEqual({'department_ids': [1, 3], 'genre_ids': None, 'archived': True},
                         update_book.call_args.kwargs)
        self.assertIs(True, response.get_json()['book']['archived'])

    def test_archive_alone_does_not_touch_departments(self):
        response, update_book = self.patch({'archived': False})
        self.assertEqual(200, response.status_code)
        self.assertEqual({'department_ids': None, 'genre_ids': None, 'archived': False},
                         update_book.call_args.kwargs)

    def test_bad_payloads_change_nothing(self):
        cases = (
            ({}, 'LIBRARY_BAD_REQUEST', ()),
            ({'archived': 'yes'}, 'LIBRARY_BAD_REQUEST', ()),
            ({'department_ids': []}, 'LIBRARY_DEPARTMENTS_REQUIRED', ()),
            ({'department_ids': '1,2'}, 'LIBRARY_BAD_REQUEST', ()),
            ({'department_ids': [True]}, 'LIBRARY_BAD_REQUEST', ()),
            ({'department_ids': [1.5]}, 'LIBRARY_BAD_REQUEST', ()),
            ({'department_ids': [1, 42]}, 'LIBRARY_DEPARTMENT_UNKNOWN', (42,)),
            ({'department_ids': ['²']}, 'LIBRARY_BAD_REQUEST', ()),
        )
        for payload, code, unknown in cases:
            with self.subTest(payload=payload):
                response, update_book = self.patch(payload, unknown=unknown)
                self.assertEqual(400, response.status_code)
                self.assertEqual(code, response.get_json()['code'])
                update_book.assert_not_called()

    def test_unknown_book(self):
        response, _ = self.patch({'archived': True}, found=False)
        self.assertEqual(404, response.status_code)

    def test_body_must_be_an_object(self):
        # Массив и строка — не 500: у строки `in` ищет подстроку, у массива
        # нет .get.
        for body in ([1], 'department_ids'):
            with self.subTest(body=body):
                response, update_book = self.patch(body)
                self.assertEqual(400, response.status_code)
                update_book.assert_not_called()

    def test_parse_ids(self):
        self.assertEqual([1, 2], routes.parse_ids(['2', 1, '1']))
        self.assertEqual([], routes.parse_ids([]))
        for bad in (None, '1', [None], ['1a'], [' '], [False], ['1' * 10], ['²'], ['①'], ['٣']):
            with self.subTest(bad=bad):
                self.assertIsNone(routes.parse_ids(bad))


class QueryViewTests(unittest.TestCase):
    def test_finished_book_shows_100_even_after_rereading(self):
        moment = datetime(2026, 9, 28, 23, 40, 5)
        view = queries.book_view(book_row(percent=4, page=3, finished_at=moment, progress_updated_at=moment),
                                 None)
        self.assertEqual('finished', view['progress']['status'])
        self.assertEqual(100.0, view['progress']['percent'])
        # Часы Алматы без пояса — как во всём портале (не «...GMT»).
        self.assertEqual('2026-09-28T23:40:05', view['progress']['finished_at'])

    def test_bucket_paths_never_leave_the_server(self):
        view = queries.reader_view(book_row(cover_blob='library/covers/x', file_blob='library/books/x',
                                            spine=[{'href': 'a', 'chars': 10, 'start': 0, 'linear': True}],
                                            toc=[], total_chars=10), 'https://signed')
        text = repr(view)
        self.assertNotIn('library/books/x', text)
        self.assertNotIn('library/covers/x', text)
        self.assertEqual([{'start': 0, 'chars': 10}], view['spine'])

    def test_analytics_escapes_like_wildcards(self):
        class Cursor:
            description = []

            def execute(self, sql, params):
                self.sql, self.params = sql, params

            def fetchall(self):
                return []

        cursor = Cursor()
        queries.analytics(cursor, query='50%_off')
        self.assertIn('%50\\%\\_off%', cursor.params)
        self.assertNotIn('%_off', cursor.sql)

    def test_analytics_says_when_the_book_was_read(self):
        """Мониторинг отвечает «когда читал»: начал, закончил, читал последний
        раз — все три из library_progress, часы Алматы без пояса."""
        from datetime import datetime
        names = ['user_id', 'user_name', 'login', 'employment_status', 'department_id', 'department_name',
                 'book_id', 'book_title', 'book_author', 'total_pages', 'percent', 'page',
                 'started_at', 'finished_at', 'updated_at']

        class Cursor:
            description = [(name,) for name in names]

            def execute(self, sql, params):
                self.sql = sql

            def fetchall(self):
                return [(7, 'Иванов Иван', 'ivanov', 'working', 3, 'СЗоВ', 12, 'Война и мир', 'Толстой', 480,
                         55.5, 266, datetime(2026, 9, 1, 9, 5), datetime(2026, 9, 20, 23, 40),
                         datetime(2026, 9, 28, 14, 2))]

        cursor = Cursor()
        rows, truncated = queries.analytics(cursor)
        self.assertIn('p.started_at', cursor.sql)
        self.assertFalse(truncated)
        row = rows[0]
        self.assertEqual('2026-09-01T09:05:00', row['started_at'])
        self.assertEqual('2026-09-20T23:40:00', row['finished_at'])
        self.assertEqual('2026-09-28T14:02:00', row['updated_at'])
        self.assertEqual(100.0, row['percent'])
        self.assertEqual('finished', row['status'])


class ScriptedCursor:
    """Курсор, отвечающий заготовленными строками по порядку запросов."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((' '.join(sql.split()), params))

    def fetchall(self):
        return self.results.pop(0)

    def fetchone(self):
        return self.results.pop(0)


class SummaryTests(unittest.TestCase):
    def test_overall_and_by_department_add_up(self):
        from decimal import Decimal
        cursor = ScriptedCursor(
            # чтение по отделу читателя: люди, в процессе, закончено, сумма процентов
            [(1, 3, 4, 2, Decimal('250.5')), (None, 1, 1, 0, Decimal('50')), (2, 1, 0, 1, Decimal('100'))],
            # книги на полке по отделам и всего (одна книга бывает у двух отделов)
            [(1, 5), (2, 2), (None, 6)],
            [(2, 'Отдел продаж'), (1, 'СЗоВ'), (9, 'Отдел без книг и читателей')],
        )
        summary = queries.analytics_summary(cursor)
        # 400.5 / 8 = 50.0625 -> 50.0 (вниз)
        self.assertEqual({'books': 6, 'readers': 5, 'in_progress': 5, 'finished': 3,
                          'avg_percent': 50.0}, summary['total'])
        names = [row['name'] for row in summary['departments']]
        # Отдел из одних нулей не показывается; люди без отдела — строкой в конце.
        self.assertEqual(['Отдел продаж', 'СЗоВ', 'Без отдела'], names)
        szov = summary['departments'][1]
        # 250.5 / 6 = 41.75 -> 41.7 (вниз, не «банковски» и не вверх)
        self.assertEqual({'id': 1, 'name': 'СЗоВ', 'books': 5, 'readers': 3, 'in_progress': 4,
                          'finished': 2, 'avg_percent': 41.7}, szov)
        for key in ('readers', 'in_progress', 'finished'):
            self.assertEqual(summary['total'][key], sum(row[key] for row in summary['departments']))
        self.assertIsNone(summary['departments'][-1]['books'])

    def test_one_department(self):
        cursor = ScriptedCursor([(1, 2, 1, 1, 130)], [(1, 5), (2, 2), (None, 6)])
        summary = queries.analytics_summary(cursor, department_id=1)
        self.assertIsNone(summary['departments'])
        self.assertEqual({'books': 5, 'readers': 2, 'in_progress': 1, 'finished': 1, 'avg_percent': 65.0},
                         summary['total'])
        self.assertEqual((1,), cursor.calls[0][1])
        self.assertIn('WHERE u.department_id = %s', cursor.calls[0][0])
        self.assertEqual(2, len(cursor.calls))

    def test_average_never_shows_100_while_someone_is_still_reading(self):
        """20 дочитавших и один на 99.9 % — это 99.99; обычное округление
        давало «100 %» рядом с «В процессе 1»."""
        from decimal import Decimal
        self.assertEqual(99.9, queries._reading_stats(21, 1, 20, Decimal('2099.9'))['avg_percent'])
        self.assertEqual(65.0, queries._reading_stats(2, 1, 1, Decimal('130'))['avg_percent'])
        self.assertEqual(100.0, queries._reading_stats(2, 0, 2, Decimal('200'))['avg_percent'])
        self.assertIsNone(queries._reading_stats()['avg_percent'])

    def test_empty_library(self):
        cursor = ScriptedCursor([], [(None, 0)], [])
        summary = queries.analytics_summary(cursor)
        self.assertEqual({'books': 0, 'readers': 0, 'in_progress': 0, 'finished': 0, 'avg_percent': None},
                         summary['total'])
        self.assertEqual([], summary['departments'])

    def test_summary_route_passes_the_department(self):
        client, _ = client_for('trainer')
        with mock.patch.object(routes, 'schema_is_ready', return_value=True), \
                mock.patch.object(queries, 'analytics_summary',
                                  return_value={'total': {'books': 1}, 'departments': None}) as summary:
            body = client.get('/api/library/analytics/summary?department_id=7').get_json()
            client.get('/api/library/analytics/summary?department_id=abc')
            # Юникод-«цифры» проходят isdigit(), но не int(): фильтр
            # отбрасывается, а не роняет ручку в 500.
            weird = client.get('/api/library/analytics/summary?department_id=²')
        self.assertEqual({'books': 1}, body['total'])
        self.assertEqual(200, weird.status_code)
        self.assertEqual([{'department_id': 7}, {'department_id': None}, {'department_id': None}],
                         [call.kwargs for call in summary.call_args_list])


class SchemaTests(unittest.TestCase):
    def test_old_books_get_the_uploader_department_only_once(self):
        """Книги до разделения по отделам получают отдел загрузившего — один
        раз, при создании таблицы. Позже книга без отделов — это книга, чей
        отдел удалили, и раздавать её молча нельзя."""
        from library import schema as library_schema
        for existed, backfilled in ((False, True), (True, False)):
            with self.subTest(existed=existed):
                cursor = ScriptedCursor((existed,))
                library_schema.init_library_schema(cursor)
                sql = [call[0] for call in cursor.calls]
                self.assertIn('to_regclass', sql[0])
                self.assertEqual(('public.library_book_departments',), cursor.calls[0][1])
                self.assertEqual(backfilled, any('INSERT INTO library_book_departments' in text for text in sql))

    def test_readiness_waits_for_the_last_table(self):
        """Готовность — по последней таблице схемы (жанры книги): по таблице
        постарше запросы с жанрами пошли бы в базу без них — 500."""
        from library import schema as library_schema
        cursor = ScriptedCursor((False,))
        self.assertFalse(library_schema.schema_is_ready(cursor))
        self.assertEqual(('public.library_book_genres',), cursor.calls[0][1])
        last_table = [text for text in library_schema._STATEMENTS if 'CREATE TABLE' in text][-1]
        self.assertIn('CREATE TABLE IF NOT EXISTS library_book_genres', last_table)

    def test_schema_is_idempotent_and_checks_percent(self):
        from library.schema import _STATEMENTS
        text = '\n'.join(_STATEMENTS)
        for table in ('library_books', 'library_saved', 'library_progress', 'library_book_departments',
                      'library_genres', 'library_book_genres'):
            self.assertIn(f'CREATE TABLE IF NOT EXISTS {table}', text)
        # Имя жанра уникально без учёта регистра; create_genre опирается на
        # этот индекс в ON CONFLICT ((LOWER(name))).
        self.assertIn('CREATE UNIQUE INDEX IF NOT EXISTS uq_library_genres_name ON library_genres (LOWER(name))', text)
        self.assertIn('genre_id INTEGER NOT NULL REFERENCES library_genres(id) ON DELETE CASCADE', text)
        self.assertIn('ADD COLUMN IF NOT EXISTS archived_at', text)
        self.assertNotIn('CREATE TABLE library', text)
        self.assertIn('CHECK (percent >= 0 AND percent <= 100)', text)
        self.assertNotRegex(text, r'--[^\n]*%(?!\(now\)s)')

    def test_schema_is_wired_into_database_init(self):
        source = (ROOT / 'database.py').read_text(encoding='utf-8')
        self.assertIn('self._init_library_schema_tx(cursor)', source)
        self.assertIn('from library.schema import init_library_schema', source)

    def test_blueprint_is_registered(self):
        source = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        self.assertIn('from library.routes import build_library_blueprint', source)


class StorageTests(unittest.TestCase):
    def test_blob_paths_are_namespaced(self):
        path = storage.blob_path_for('books', '../../Война и мир.epub')
        self.assertRegex(path, r'^library/books/\d{4}/\d{2}/\d{2}/[0-9a-f]{32}_[A-Za-z0-9._-]+$')
        self.assertNotIn('..', path.split('/', 5)[-1].split('_', 1)[0])


class FrontendWiringTests(unittest.TestCase):
    """Раздел доходит до меню, до экрана и до тренера."""

    @classmethod
    def setUpClass(cls):
        cls.app = (ROOT / 'src/App.jsx').read_text(encoding='utf-8')

    def test_menu_item_is_declared_once_for_all_roles(self):
        self.assertEqual(1, self.app.count("handleSidebarViewNavigation(e, 'library')"))
        wiki = self.app.index("handleSidebarViewNavigation(e, 'wiki')")
        library = self.app.index("handleSidebarViewNavigation(e, 'library')")
        lms = self.app.index("handleSidebarViewNavigation(e, 'lms')")
        self.assertLess(wiki, library)
        self.assertLess(library, lms)

    def test_menu_memo_depends_on_the_predicate(self):
        """Пункт меню живёт внутри sidebarTree = useMemo(...): без зависимости
        он не появился бы после смены пользователя."""
        start = self.app.index('const sidebarTree = useMemo(')
        deps_start = self.app.index('}, [', self.app.index("handleSidebarViewNavigation(e, 'library')"))
        deps = self.app[deps_start:self.app.index(']);', deps_start)]
        self.assertLess(start, deps_start)
        self.assertIn('canAccessLibrarySection', deps)
        self.assertIn('|| canAccessLibrarySection', self.app)

    def test_screen_is_rendered_behind_the_predicate(self):
        self.assertIn("{view === 'library' && canAccessLibrarySection && (", self.app)
        # Пока раздел только у супер-админа и тренера — по роли, не по отделу.
        self.assertIn("const canAccessLibrarySection = isSuperAdmin || currentUserRole === 'trainer';", self.app)
        self.assertIn("import('./components/library/LibraryView')", self.app)

    def test_trainer_is_not_thrown_out_of_the_section(self):
        match = re.search(r'const TRAINER_ALLOWED_VIEWS = Object\.freeze\(\[(.*?)\]\);', self.app, re.S)
        self.assertIsNotNone(match)
        self.assertIn("'library'", match.group(1))

    def test_sidebar_icon_exists(self):
        icons = (ROOT / 'src/components/common/FaIcon.jsx').read_text(encoding='utf-8')
        self.assertIn("'fa-book-bookmark': 'LibraryBig'", icons)
        self.assertIn('fa-book-bookmark', self.app)

    def test_reader_is_lazy_and_depends_on_jszip_not_epubjs(self):
        view = (ROOT / 'src/components/library/LibraryView.jsx').read_text(encoding='utf-8')
        # С повтором: устаревший чанк после выкладки не должен ронять портал.
        self.assertIn("const importReader = () => import('./LibraryReader');", view)
        self.assertIn('lazyWithRetry(importReader)', view)
        # Чанк ридера греется заранее, пока каталог на экране.
        self.assertIn('const warmReader = () => { importReader().catch(() => {}); };', view)
        self.assertIn('onPointerEnter={warmReader}', view)
        self.assertIn("import lazyWithRetry from './utils/lazyWithRetry';", self.app)
        self.assertNotIn('const lazyWithRetry', self.app)
        package = (ROOT / 'package.json').read_text(encoding='utf-8')
        self.assertIn('"jszip"', package)
        # epub.js снят: его iframe не отдавал соседнюю страницу для загиба, а
        # Safari — касания. Возвращать его нельзя без замены движка страниц.
        self.assertNotIn('"epubjs"', package)
        for path in (ROOT / 'src/components/library').iterdir():
            self.assertNotIn('epubjs', path.read_text(encoding='utf-8'), path.name)

    def test_show_toast_is_not_an_effect_dependency(self):
        """showToast из App.jsx — новая функция на каждый рендер."""
        for name in ('LibraryView.jsx', 'LibraryMonitoring.jsx'):
            source = (ROOT / 'src/components/library' / name).read_text(encoding='utf-8')
            self.assertNotRegex(source, r'\[[^\]]*\bshowToast\b[^\]]*\]\);', name)


class DepartmentsUiTests(unittest.TestCase):
    """Отделы, архив и статистика в интерфейсе (29.09.2026)."""

    @classmethod
    def setUpClass(cls):
        base = ROOT / 'src/components/library'
        cls.view = (base / 'LibraryView.jsx').read_text(encoding='utf-8')
        cls.monitoring = (base / 'LibraryMonitoring.jsx').read_text(encoding='utf-8')
        cls.sheet = (base / 'LibraryBookModal.jsx').read_text(encoding='utf-8')

    def test_publishing_sends_the_chosen_departments(self):
        self.assertIn("form.append('department_ids', String(id))", self.view)
        # Без отдела кнопка «Опубликовать» не нажимается — сервер отвечает тем же.
        self.assertIn('const canSubmit = ids.length > 0', self.sheet)

    def test_single_department_is_named_in_the_select_button(self):
        """CustomSelect отдаёт выбранное строками: строгое сравнение с числовым
        id давало в кнопке «Отдел № 70» вместо названия."""
        self.assertIn('item.id === Number(id)', self.sheet)

    def test_delete_lives_only_in_the_archive(self):
        self.assertEqual(1, self.view.count("key: 'delete'"))
        archived_branch = self.view.index('if (book.archived) {')
        shelf_branch = self.view.index("key: 'archive', label: 'В архив'")
        self.assertLess(archived_branch, self.view.index("key: 'delete'"))
        self.assertLess(self.view.index("key: 'delete'"), shelf_branch)

    def test_archive_and_monitoring_tabs_are_for_managers(self):
        self.assertIn("canManage && { value: LIBRARY_TABS.archive", self.view)
        self.assertIn("canManage && { value: LIBRARY_TABS.monitoring", self.view)

    def test_stale_scope_is_not_shown_under_a_new_department(self):
        """Пока считается новый отдел, цифры и строки прежнего не показываются
        под его подписью (разбор 29.09.2026)."""
        self.assertIn('summary.scope === departmentId', self.monitoring)
        self.assertIn('data.scope === departmentId ? data.rows : []', self.monitoring)

    def test_only_selectable_departments_are_clickable_in_stats(self):
        self.assertIn('selectableIds.has(row.id)', self.monitoring)
        self.assertIn('|| item.id === departmentId)', self.view)

    def test_publish_preselects_only_an_active_department(self):
        self.assertIn("current && current.active !== false ? [departmentId] : []", self.view)

    def test_busy_sheet_keeps_its_back_gesture_entry(self):
        # locked = загрузка книги ИЛИ создание жанра (разбор 02.10.2026).
        self.assertIn('const locked = busy || creating;', self.sheet)
        self.assertIn('if (locked) return false;', self.sheet)

    def test_tab_strip_has_no_negative_margin(self):
        """mobile-shell.css гасит -mx-* внутри .main-content, и полоса вкладок
        съезжала вправо от заголовка."""
        start = self.view.index('ref={tabsRef}')
        self.assertNotIn('-mx-', self.view[start:start + 200])

    def test_monitoring_follows_the_section_department(self):
        """Один выбор отдела на весь раздел: второй, свой, в мониторинге
        разъезжался бы с ним."""
        self.assertNotIn('ariaLabel="Отдел"', self.monitoring)
        self.assertIn('departmentId={departmentId}', self.view)
        self.assertIn('/api/library/analytics/summary', self.monitoring)
        self.assertIn("params.set('department_id', String(departmentId))", self.monitoring)


class GenreRouteTests(unittest.TestCase):
    """Справочник жанров (02.10.2026): ведут его только управляющие."""

    def test_create_returns_new_or_existing_genre(self):
        client, _ = client_for('trainer')
        for created, status in ((True, 201), (False, 200)):
            with self.subTest(created=created), \
                    mock.patch.object(queries, 'create_genre',
                                      return_value=({'id': 4, 'name': 'Психология'}, created)) as create:
                response = client.post('/api/library/genres', json={'name': '  Психология  '})
            self.assertEqual(status, response.status_code)
            self.assertEqual({'id': 4, 'name': 'Психология'}, response.get_json()['genre'])
            self.assertIs(created, response.get_json()['created'])
            # Имя приходит в запрос уже очищенным, автор — смотрящий.
            self.assertEqual(('Психология', 7), create.call_args.args[1:])

    def test_bad_names_change_nothing(self):
        client, _ = client_for('trainer')
        for body in ({}, {'name': ''}, {'name': '   '}, {'name': 'x' * 41}, {'name': 5}, ['Психология'], 'name'):
            with self.subTest(body=body), \
                    mock.patch.object(queries, 'create_genre') as create, \
                    mock.patch.object(queries, 'rename_genre') as rename:
                for response in (client.post('/api/library/genres', json=body),
                                 client.patch('/api/library/genres/4', json=body)):
                    self.assertEqual(400, response.status_code)
                    self.assertEqual('LIBRARY_GENRE_NAME', response.get_json()['code'])
                create.assert_not_called()
                rename.assert_not_called()

    def test_rename_outcomes(self):
        client, _ = client_for('trainer')
        cases = (
            ((queries.GENRE_RENAMED, {'id': 4, 'name': 'Бизнес'}), 200, None),
            ((queries.GENRE_NAME_TAKEN, None), 409, 'LIBRARY_GENRE_EXISTS'),
            ((queries.GENRE_NOT_FOUND, None), 404, 'LIBRARY_GENRE_NOT_FOUND'),
        )
        for outcome, status, code in cases:
            with self.subTest(outcome=outcome[0]), \
                    mock.patch.object(queries, 'rename_genre', return_value=outcome):
                response = client.patch('/api/library/genres/4', json={'name': 'Бизнес'})
            self.assertEqual(status, response.status_code)
            if code:
                self.assertEqual(code, response.get_json()['code'])
            else:
                self.assertEqual({'id': 4, 'name': 'Бизнес'}, response.get_json()['genre'])

    def test_rename_race_on_unique_index_is_409_not_500(self):
        class UniqueViolation(Exception):
            pgcode = '23505'

        client, _ = client_for('trainer')
        with mock.patch.object(queries, 'rename_genre', side_effect=UniqueViolation()):
            response = client.patch('/api/library/genres/4', json={'name': 'Бизнес'})
        self.assertEqual(409, response.status_code)
        self.assertEqual('LIBRARY_GENRE_EXISTS', response.get_json()['code'])
        with mock.patch.object(queries, 'rename_genre', side_effect=RuntimeError('db down')):
            self.assertEqual(500, client.patch('/api/library/genres/4', json={'name': 'Бизнес'}).status_code)

    def test_delete(self):
        client, _ = client_for('super_admin')
        with mock.patch.object(queries, 'delete_genre', return_value=True) as delete:
            self.assertEqual(200, client.delete('/api/library/genres/4').status_code)
        self.assertEqual(4, delete.call_args.args[1])
        with mock.patch.object(queries, 'delete_genre', return_value=False):
            response = client.delete('/api/library/genres/4')
        self.assertEqual(404, response.status_code)
        self.assertEqual('LIBRARY_GENRE_NOT_FOUND', response.get_json()['code'])

    def test_readers_cannot_touch_the_genre_list(self):
        with mock.patch.object(routes, 'READER_ROLES', frozenset({'operator', 'sv', 'admin'})), \
                mock.patch.object(queries, 'create_genre') as create, \
                mock.patch.object(queries, 'rename_genre') as rename, \
                mock.patch.object(queries, 'delete_genre') as delete:
            for role in ('operator', 'sv', 'admin'):
                with self.subTest(role=role):
                    client, _ = client_for(role)
                    for response in (client.post('/api/library/genres', json={'name': 'X'}),
                                     client.patch('/api/library/genres/4', json={'name': 'X'}),
                                     client.delete('/api/library/genres/4')):
                        self.assertEqual(403, response.status_code)
                        self.assertEqual('LIBRARY_MANAGE_FORBIDDEN', response.get_json()['code'])
            for untouched in (create, rename, delete):
                untouched.assert_not_called()


class BookGenreTests(unittest.TestCase):
    """Жанры книги: при загрузке и правке, неверные — отказ до записи."""

    def test_upload_passes_genres(self):
        inserted = {}

        def insert_book(cursor, **kwargs):
            inserted.update(kwargs)
            return 5

        client, _ = client_for('trainer')
        with mock.patch.object(queries, 'insert_book', side_effect=insert_book), \
                mock.patch.object(queries, 'unknown_departments', return_value=[]), \
                mock.patch.object(queries, 'unknown_genres', return_value=[]), \
                mock.patch.object(queries, 'get_book', return_value=book_row()):
            response = client.post('/api/library/books', data={
                'file': (io.BytesIO(epub3_book()), 'book.epub'), 'department_ids': ['1'],
                'genre_ids': ['4', '2', '4'],
            }, content_type='multipart/form-data')
        self.assertEqual(201, response.status_code, response.get_json())
        self.assertEqual([2, 4], inserted['genre_ids'])

    def test_bad_or_unknown_genre_stops_the_upload_before_the_bucket(self):
        for extra, code, unknown in (({'genre_ids': ['abc']}, 'LIBRARY_BAD_REQUEST', []),
                                     ({'genre_ids': ['99']}, 'LIBRARY_GENRE_UNKNOWN', [99])):
            with self.subTest(extra=extra):
                client, fake = client_for('trainer')
                with mock.patch.object(queries, 'unknown_departments', return_value=[]), \
                        mock.patch.object(queries, 'unknown_genres', return_value=unknown), \
                        mock.patch.object(queries, 'insert_book') as insert_book:
                    response = client.post('/api/library/books', data={
                        'file': (io.BytesIO(epub3_book()), 'book.epub'), 'department_ids': ['1'], **extra,
                    }, content_type='multipart/form-data')
                self.assertEqual(400, response.status_code)
                self.assertEqual(code, response.get_json()['code'])
                self.assertEqual({}, fake.uploaded)
                insert_book.assert_not_called()

    def patch(self, payload, *, unknown=()):
        client, _ = client_for('trainer')
        with mock.patch.object(queries, 'unknown_departments', return_value=[]), \
                mock.patch.object(queries, 'unknown_genres', return_value=list(unknown)), \
                mock.patch.object(queries, 'update_book', return_value=True) as update_book, \
                mock.patch.object(queries, 'get_book', return_value=book_row()):
            response = client.patch('/api/library/books/5', json=payload)
        return response, update_book

    def test_genres_alone_and_empty_genres(self):
        response, update_book = self.patch({'genre_ids': [4, 2]})
        self.assertEqual(200, response.status_code, response.get_json())
        self.assertEqual({'department_ids': None, 'genre_ids': [2, 4], 'archived': None},
                         update_book.call_args.kwargs)
        # Снять все жанры — можно: книга без жанра не ошибка, в отличие от отделов.
        response, update_book = self.patch({'genre_ids': []})
        self.assertEqual(200, response.status_code)
        self.assertEqual([], update_book.call_args.kwargs['genre_ids'])

    def test_bad_genres_change_nothing(self):
        for payload, code, unknown in (({'genre_ids': '1,2'}, 'LIBRARY_BAD_REQUEST', ()),
                                       ({'genre_ids': [True]}, 'LIBRARY_BAD_REQUEST', ()),
                                       ({'genre_ids': [3]}, 'LIBRARY_GENRE_UNKNOWN', (3,)),
                                       ({'department_ids': [1], 'genre_ids': [3]}, 'LIBRARY_GENRE_UNKNOWN', (3,))):
            with self.subTest(payload=payload):
                response, update_book = self.patch(payload, unknown=unknown)
                self.assertEqual(400, response.status_code)
                self.assertEqual(code, response.get_json()['code'])
                update_book.assert_not_called()


class GenreQueryTests(unittest.TestCase):
    def test_name_is_cleaned_like_the_client_does(self):
        self.assertEqual('Личная эффективность', queries.normalize_genre_name('  Личная \x00 эффективность\n '))
        self.assertEqual('x' * 40, queries.normalize_genre_name('x' * 40))
        for bad in ('x' * 41, '', '   ', None, 5, ['a']):
            with self.subTest(bad=bad):
                self.assertEqual('', queries.normalize_genre_name(bad))
        meta = (ROOT / 'src/components/library/libraryMeta.js').read_text(encoding='utf-8')
        self.assertIn(f'export const GENRE_NAME_MAX = {queries.GENRE_NAME_MAX};', meta)

    def test_create_returns_the_existing_genre_in_one_statement(self):
        cursor = ScriptedCursor((4, 'Психология', False))
        genre, created = queries.create_genre(cursor, 'психология', 7)
        self.assertEqual(({'id': 4, 'name': 'Психология'}, False), (genre, created))
        self.assertEqual(1, len(cursor.calls))
        # Имя существующего жанра не переписывается регистром нового.
        self.assertIn('ON CONFLICT ((LOWER(name))) DO UPDATE SET name = library_genres.name', cursor.calls[0][0])
        self.assertIn('(xmax = 0) AS created', cursor.calls[0][0])
        self.assertEqual(('психология', 7), cursor.calls[0][1])

    def test_rename_checks_other_genres_only(self):
        cursor = ScriptedCursor(None, (4, 'Бизнес'))
        self.assertEqual((queries.GENRE_RENAMED, {'id': 4, 'name': 'Бизнес'}),
                         queries.rename_genre(cursor, 4, 'Бизнес'))
        self.assertIn('id <> %s', cursor.calls[0][0])
        self.assertEqual(('Бизнес', 4), cursor.calls[0][1])
        cursor = ScriptedCursor((1,))
        self.assertEqual((queries.GENRE_NAME_TAKEN, None), queries.rename_genre(cursor, 4, 'Бизнес'))
        self.assertEqual(1, len(cursor.calls))

    def test_empty_genre_set_only_deletes(self):
        cursor = ScriptedCursor()
        queries.set_book_genres(cursor, 5, [])
        self.assertEqual(1, len(cursor.calls))
        self.assertIn('DELETE FROM library_book_genres', cursor.calls[0][0])
        cursor = ScriptedCursor()
        queries.set_book_genres(cursor, 5, [3, 1, 3])
        self.assertEqual((5, [1, 3]), cursor.calls[1][1])

    def test_genre_deleted_meanwhile_is_skipped_not_a_500(self):
        """Набор жанров проверен до записи, а при загрузке между проверкой и
        записью — файл в бакет. Жанр, удалённый в эту секунду, не должен
        ронять вставку внешним ключом (23503 -> 500, проверено на Postgres
        стенда): вставляются только живые жанры, под FOR KEY SHARE."""
        cursor = ScriptedCursor()
        queries.set_book_genres(cursor, 5, [4])
        insert = cursor.calls[1][0]
        self.assertIn('SELECT %s, g.id FROM library_genres g WHERE g.id = ANY(%s) FOR KEY SHARE', insert)
        self.assertNotIn('unnest', insert)

    def test_book_columns_carry_genres(self):
        text = ' '.join(queries._BOOK_COLUMNS.split())
        self.assertIn('FROM library_book_genres bg WHERE bg.book_id = b.id', text)
        self.assertEqual([2], queries.book_view(book_row())['genre_ids'])
        self.assertEqual([], queries.book_view(book_row(genre_ids=None))['genre_ids'])


class LibraryDepartmentCodesTests(unittest.TestCase):
    """Решение владельца 02.10.2026: «из доступных отделов сделать только
    пока СЗоВ и ОП» — по коду отдела, а не по названию."""

    def test_only_szov_and_op(self):
        self.assertEqual(('szov', 'op'), queries.LIBRARY_DEPARTMENT_CODES)

    def test_selection_list_marks_other_departments_with_books_as_not_selectable(self):
        cursor = ScriptedCursor([(1, 'СЗоВ', True), (70, 'Тез КЦ', False)])
        rows = queries.library_departments(cursor)
        self.assertEqual([{'id': 1, 'name': 'СЗоВ', 'active': True}, {'id': 70, 'name': 'Тез КЦ', 'active': False}],
                         rows)
        sql, params = cursor.calls[0]
        self.assertIn('LOWER(d.code) = ANY(%s)', sql)
        self.assertEqual((['szov', 'op'], ['szov', 'op']), params)

    def test_new_book_goes_only_to_listed_departments(self):
        cursor = ScriptedCursor([(1,)])
        self.assertEqual([70], queries.unknown_departments(cursor, [70, 1]))
        sql, params = cursor.calls[0]
        self.assertIn('LOWER(d.code) = ANY(%s)', sql)
        # Отдел, которому книга уже выдана, остаётся допустимым для неё.
        self.assertIn('bd.book_id = %s', sql)
        self.assertEqual(([1, 70], ['szov', 'op'], None), params)


class GenresUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = ROOT / 'src/components/library'
        cls.view = (base / 'LibraryView.jsx').read_text(encoding='utf-8')
        cls.sheet = (base / 'LibraryBookModal.jsx').read_text(encoding='utf-8')
        cls.genres = (base / 'LibraryGenresModal.jsx').read_text(encoding='utf-8')

    def test_publishing_and_editing_send_genres(self):
        self.assertIn("form.append('genre_ids', String(id))", self.view)
        self.assertIn('{ department_ids: departmentIds, genre_ids: genreIds }', self.view)
        self.assertIn('onSubmit?.({ departmentIds: ids, genreIds: genreIdsRef.current })', self.sheet)

    def test_save_creates_the_typed_genre_first(self):
        """Набрали жанр и сразу «Сохранить»: окно само создаёт набранное и
        сохраняет книгу уже с ним; не создался — книга не сохраняется."""
        self.assertIn('if (!(await commitDraft())) return;', self.sheet)
        self.assertIn('onClick={submit}', self.sheet)

    def test_cancel_never_creates_a_genre(self):
        """Уход фокуса жанр не создаёт: щелчок по «Отмене» уводит фокус из
        поля, и отменённое окно оставляло бы жанр в общем справочнике."""
        self.assertNotIn('onBlur', self.sheet)

    def test_window_is_locked_while_a_genre_is_created(self):
        """Пока жанр создаётся, «Отмена», крестик и жест «назад» не
        закрывают окно — иначе ответ после «Отмены» сохранил бы книгу."""
        self.assertIn('const locked = busy || creating;', self.sheet)
        self.assertIn('if (locked) return false;', self.sheet)
        self.assertIn('onClick={onClose} disabled={locked}', self.sheet)

    def test_failed_rename_does_not_grab_focus_back(self):
        """Поле, забирающее фокус после отказа, слало бы тот же запрос и тот
        же тост на каждый щелчок мимо него (разбор 02.10.2026)."""
        start = self.genres.index('const saveRename = async () => {')
        body = self.genres[start:self.genres.index('};', start)]
        self.assertNotIn('focus()', body)
        self.assertIn('if (error?.response?.status === 404) dropGenre(genre);', self.view)

    def test_genre_strip_lives_outside_monitoring(self):
        self.assertIn('tab !== LIBRARY_TABS.monitoring && !loading && genreChips.length > 0', self.view)

    def test_genre_management_is_for_managers(self):
        start = self.view.index('{canManage && schemaReady && (')
        self.assertLess(start, self.view.index('onClick={() => setGenresOpen(true)}'))
        self.assertIn("label: 'Отделы и жанры'", self.view)

    def test_genre_fields_do_not_send_twice(self):
        """Enter сохраняет, поле гаснет, браузер шлёт blur со старым
        состоянием — без флага в ref ушёл бы второй запрос."""
        self.assertIn('if (creatingRef.current) return false;', self.sheet)
        self.assertIn('if (busyRef.current || !editingRef.current) return;', self.genres)
        for source in (self.sheet, self.genres):
            self.assertNotIn('disabled={saving}', source)

    def test_department_select_lists_only_selectable_departments(self):
        self.assertIn('.filter((item) => item.active !== false || item.id === departmentId)', self.view)


class ReaderRulesTests(unittest.TestCase):
    """Правила ридера, которые легко сломать правкой «для красоты»."""

    @classmethod
    def setUpClass(cls):
        base = ROOT / 'src/components/library'
        cls.reader = (base / 'LibraryReader.jsx').read_text(encoding='utf-8')
        cls.engine = (base / 'readerEngine.js').read_text(encoding='utf-8')
        cls.loader = (base / 'epubLoader.js').read_text(encoding='utf-8')
        cls.meta = (base / 'libraryMeta.js').read_text(encoding='utf-8')

    def test_reader_sits_below_the_news_window(self):
        news = (ROOT / 'src/components/news/NewsOfDayModal.jsx').read_text(encoding='utf-8')
        news_z = int(re.search(r'fixed inset-0 z-\[(\d+)\]', news).group(1))
        reader_z = int(re.search(r'const READER_Z = (\d+);', self.reader).group(1))
        self.assertLess(reader_z, news_z)
        self.assertGreater(reader_z, 100)   # сайдбар портала

    def test_book_html_is_sanitized_and_offline(self):
        """Разметка книги — чужой HTML: скрипты и формы прочь, внешние адреса
        картинок и стилей тоже (книга не ходит в интернет)."""
        self.assertIn('DOMPurify.sanitize(body.innerHTML, PURIFY_CONFIG)', self.loader)
        forbid = re.search(r"FORBID_TAGS: \[(.*?)\]", self.loader, re.S).group(1)
        for tag in ('script', 'iframe', 'object', 'embed', 'form', 'base', 'link', 'style', 'foreignObject'):
            self.assertIn(f"'{tag}'", forbid)
        self.assertIn('attachShadow', self.engine)
        # Адреса переписываются на ОЧИЩЕННОМ дереве (RETURN_DOM_FRAGMENT), и
        # последний проход оставляет только blob: книги и #.
        self.assertIn('RETURN_DOM_FRAGMENT: true', self.loader)
        self.assertIn("if (/^blob:/i.test(value) || value.startsWith('#')) return;", self.loader)

    def test_blob_files_cannot_run_as_pages(self):
        """blob-адрес живёт в origin портала: SVG со скриптом, открытый в новой
        вкладке, исполнился бы от его имени. SVG — только очищенный, остальное
        — растровый тип или octet-stream."""
        self.assertIn('DOMPurify.sanitize(decodeText(bytes), SVG_PURIFY_CONFIG)', self.loader)
        self.assertIn("RASTER_TYPES.has(declared) ? declared : 'application/octet-stream'", self.loader)
        self.assertNotIn("'image/svg+xml', 'text/html'", self.loader)

    def test_turn_outcome_survives_interruption(self):
        """Новое действие во время доворота досчитывает ход тем исходом, к
        которому он шёл, — иначе отменённый свайп пролистывал страницу."""
        self.assertIn('this.settle(turn.completed);', self.engine)
        self.assertIn('turn.completed = completed;', self.engine)
        # Конец книги — только в линейной главе: сноски в конце его не дают.
        self.assertIn('places.some((place) => this.isLinear(place.section) && this.linearAfter(place.section) < 0', self.engine)

    def test_book_pages_have_folio_and_running_head(self):
        """Страница как в печатной книге: номер внизу, колонтитул сверху, на
        первой странице главы колонтитула нет."""
        self.assertIn('this.folio = make(', self.engine)
        self.assertIn('this.head = make(', self.engine)
        self.assertIn("if (chapter ? chapter.opening : view.page === 0) return '';", self.engine)
        # «Глава» — по пунктам оглавления внутри файла: в книге одним файлом
        # файл — это вся книга, и «Ещё N стр. в главе» считало бы до конца книги.
        self.assertIn("anchors.get(item.spine).push([at, tocIndex]);", self.engine)
        # Номера — из вёрстки всей книги, одни и те же на странице и в оглавлении.
        self.assertIn('pages={pagination?.toc || []}', self.reader)
        self.assertIn('localStorage.setItem(key', self.engine)

    def test_only_paging_forward_finishes_the_book(self):
        self.assertIn('if (position.atEnd && position.paged)', self.meta)
        self.assertIn("if (completed) this.emit(turn.forward ? 'forward' : 'back');", self.engine)
        self.assertIn("paged: kind === 'forward',", self.engine)
        start = self.engine.index('async jumpTo(')
        jump = self.engine[start:self.engine.index('paginationKey()', start)]
        self.assertIn('this.emit(kind);', jump)
        self.assertIn("if (place.kind === 'link') { sideTripRef.current = true; return; }", self.reader)

    def test_side_trip_and_refresh_are_not_saved(self):
        """Визит к сноске и пересчёт номеров страниц местом чтения не
        считаются: перекладка страниц во время визита записала бы сноски как
        место, где читатель остановился."""
        self.assertIn("if (place.kind === 'refresh') return;", self.reader)
        self.assertIn("if (place.kind === 'layout' && sideTripRef.current) return;", self.reader)

    def test_turned_leaf_stays_in_place_in_the_dom(self):
        """Оборот листа — transform и clip-path самой страницы. Перенос
        страницы с главой в другой контейнер заставлял браузер заново
        верстать главу на каждом ходе (секунды на книге одним файлом)."""
        appends = re.findall(r'\.append\(([^)]*)\)', self.engine)
        moved = [args for args in appends if '.el' in args]
        self.assertEqual(moved, ['view.el', 'this.measurer.el'])
        self.assertIn('matrixCss([-a, -b, c, d, a * W + e, b * W + f])', self.engine)

    def test_book_style_yields_to_reader_typography(self):
        """Стиль книги — первым, стиль страницы — после: при равной
        специфичности побеждает книжный абзац ридера, а правила книги с
        классами остаются за книгой."""
        self.assertIn('`<style data-book></style><style>${BASE_CSS}</style>`', self.engine)
        self.assertIn(':where(.lr-body) p { margin-top: 0; margin-bottom: 0; text-indent: 1.5em; }', self.engine)
        # Буквица и отдельные картинки — пометками загрузчика, не селектором.
        self.assertIn("paragraph.classList.add('lr-dropcap')", self.loader)
        self.assertIn("img.classList.add('lr-img-block')", self.loader)

    def test_resize_keeps_place_and_waits_for_turn(self):
        """Смена размера: ход досчитывается в старой геометрии, место берётся
        до перемера, во время подготовки перекладка откладывается."""
        layout = self.engine[self.engine.index('    layout(metrics) {'):]
        layout = layout[:layout.index('\n    }\n')]
        self.assertTrue(layout.lstrip().startswith('layout(metrics) {\n        if (this.turn) this.abortTurn();'))
        self.assertLess(layout.index('const keep'), layout.index('this.metrics = metrics;'))
        self.assertIn('if (this.preparing) this.pendingLayout = keep;', layout)

    def test_mouse_click_on_text_does_not_turn(self):
        """Мышью листают полем страницы и столом: щелчок по тексту, снятие
        выделения и выделение, отпущенное за книгой, страницу не листают."""
        self.assertIn('if (gesture.mouse) {', self.reader)
        self.assertIn("} else if (margin === 'outer') {", self.reader)
        self.assertIn('if (gesture.hadSelection ||', self.reader)
        self.assertIn('const pressed = deskPressRef.current;', self.reader)

    def test_position_format_matches_the_server(self):
        server = re.search(r"POSITION_RE = re\.compile\(r'(.+?)'\)", (ROOT / 'library/routes.py').read_text(encoding='utf-8')).group(1)
        client = re.search(r'const match = /(.+?)/\.exec', self.meta).group(1)
        self.assertEqual(server, client)

    def test_book_opens_like_a_book(self):
        """Открытие и закрытие — Web Animations (transform и opacity): полёт на
        framer с left/top/width/height шёл вёрсткой на каждом кадре и дёргался
        ровно тогда, когда книга распаковывалась. Ридер убирается только после
        того, как обложка вернулась в карточку, а карточка всё это время пустая —
        иначе обложка садилась бы на свою же копию."""
        self.assertIn("element.animate(keyframes, { fill: 'both', ...options })", self.reader)
        self.assertNotIn('onAnimationComplete', self.reader)
        self.assertIn('run().catch(() => {}).finally(() => onClose?.());', self.reader)
        # Листают и нажимают только в раскрытой книге, не посреди раскрытия.
        self.assertIn("const ready = phase === 'ready' && stage === 'open';", self.reader)
        # Лист, который ложится налево, — сама левая страница движка, не копия.
        self.assertIn('leaf: leaf ? leaf.el : null,', self.engine)
        view = (ROOT / 'src/components/library/LibraryView.jsx').read_text(encoding='utf-8')
        # Обложка карточки прячется в кадре, когда её подхватила летящая.
        self.assertIn('reading={book.id === liftedId}', view)
        self.assertIn('origin.onLift?.();', self.reader)
        # Закрытие замораживает движок до того, как взять лист.
        self.assertIn('if (engine) await engine.beginClosing();', self.reader)
        # Раскрывается только книга, которая действительно легла на место.
        self.assertIn('await engine.placed;', self.reader)
        self.assertNotIn('AnimatePresence', view)

    def test_upload_format_is_stated_before_choosing_a_file(self):
        """В каком виде нужна книга — подписью под кнопкой загрузки, до выбора
        файла, а не только сообщением об ошибке после."""
        view = (ROOT / 'src/components/library/LibraryView.jsx').read_text(encoding='utf-8')
        self.assertIn('Формат EPUB, до {maxMb} МБ', view)
        monitoring = (ROOT / 'src/components/library/LibraryMonitoring.jsx').read_text(encoding='utf-8')
        for label in ('Начал', 'Последнее чтение', 'formatDay(row.started_at)', 'finishedAt={row.finished_at}'):
            self.assertIn(label, monitoring)

    def test_chapter_start_has_no_leading_break(self):
        """Разрыв страницы перед началом главы WebKit превращает в пустую
        первую страницу — в Safari каждая глава начиналась справа от чистого
        листа."""
        self.assertIn("element.classList.add('lr-lead')", self.loader)
        self.assertIn('.lr-body .lr-lead { break-before: auto !important;', self.engine)

    def test_page_turns_live_in_our_dom(self):
        """Касания ловит сам лист (не iframe): так они доходят и в Safari."""
        self.assertIn('onPointerDown={onPointerDown}', self.reader)
        self.assertIn('touch-none', self.reader)
        self.assertNotIn('<iframe', self.reader + self.engine)


if __name__ == '__main__':
    unittest.main()
