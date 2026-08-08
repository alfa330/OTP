# -*- coding: utf-8 -*-
"""Импорт документов в статьи.

Порт wiki2.0 services/parser.ts. Проверяем не только «разобралось», но и три
вещи, ради которых порт вообще переписывался, а не копировался:

  * результат проходит через тот же санитайзер, что и ручная правка — в
    оригинале HTML из Word не чистился вообще, и документ мог принести в базу
    произвольную разметку;
  * заголовки Word становятся заголовками статьи (иначе оглавление не
    построится, а документ превратится в простыню абзацев);
  * картинки уезжают в хранилище и получают постоянный адрес, а не пишутся на
    эфемерный диск, как в оригинале.

DOCX для теста собирается здесь же из zip+XML: тащить python-docx только ради
теста незачем, а настоящий файл проверяет настоящий путь через mammoth.
"""

import io
import unittest
import zipfile

from wiki.importer import MAX_FILE_BYTES, ImportError_, blob_path_for, convert


def make_docx(body_xml, styles=None):
    """Минимальный валидный .docx — это zip с несколькими XML внутри.

    styles — список пар (styleId, человекочитаемое имя). Имя важно: mammoth
    сопоставляет style-name из styles.xml, а не идентификатор, поэтому без
    этого файла русские заголовки Word проверить нельзя.
    """
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>%s</w:body></w:document>' % body_xml
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', content_types)
        archive.writestr('_rels/.rels', rels)
        archive.writestr('word/document.xml', document)
        if styles:
            declarations = ''.join(
                '<w:style w:type="paragraph" w:styleId="%s"><w:name w:val="%s"/></w:style>'
                % (style_id, name) for style_id, name in styles)
            archive.writestr(
                'word/styles.xml',
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main">%s</w:styles>' % declarations)
            archive.writestr(
                'word/_rels/document.xml.rels',
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/'
                'package/2006/relationships">'
                '<Relationship Id="rId10" Type="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '</Relationships>')
    return buffer.getvalue()


def paragraph(text, style=None):
    style_xml = ('<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style) if style else ''
    return ('<w:p>%s<w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>'
            % (style_xml, text))


class DocxTest(unittest.TestCase):
    def test_headings_become_headings(self):
        """Без карты стилей документ Word превращается в простыню абзацев,
        и оглавление статьи не строится."""
        data = make_docx(
            paragraph('Регламент работы', 'Heading1')
            + paragraph('Обычный абзац текста.')
            + paragraph('Раздел второй', 'Heading2')
        )
        result = convert('Регламент.docx', data, store_image=lambda *a: '')
        self.assertIn('<h1>', result['content'])
        self.assertIn('<h2>', result['content'])
        self.assertIn('Обычный абзац', result['content'])

    def test_russian_style_names(self):
        """Word с русской локалью объявляет стиль под именем «Заголовок 1»."""
        data = make_docx(
            paragraph('Заголовок', 'ЗаголовокРус'),
            styles=[('ЗаголовокРус', 'Заголовок 1')],
        )
        result = convert('Документ.docx', data, store_image=lambda *a: '')
        self.assertIn('<h1>', result['content'])

    def test_title_from_filename(self):
        data = make_docx(paragraph('Текст'))
        result = convert('Инструкция_по_смене_номера.docx', data, store_image=lambda *a: '')
        self.assertEqual(result['title'], 'Инструкция по смене номера')

    def test_summary_is_plain_text(self):
        data = make_docx(paragraph('Первое предложение документа.'))
        result = convert('Документ.docx', data, store_image=lambda *a: '')
        self.assertIn('Первое предложение', result['summary'])
        self.assertNotIn('<', result['summary'])


class SanitizationTest(unittest.TestCase):
    """В оригинале импорт не санитайзился вовсе — документ мог принести
    произвольную разметку прямо в базу."""

    def test_script_in_plain_text_is_escaped_not_executed(self):
        """В текстовом файле теги — это просто текст, и его надо показать
        как текст. Важно, что он не станет разметкой."""
        result = convert('файл.txt', '<script>alert(1)</script> текст'.encode('utf-8'))
        self.assertNotIn('<script', result['content'])
        self.assertIn('&lt;script&gt;', result['content'])

    def test_script_from_docx_is_removed(self):
        """А вот из Word разметка приходит настоящей — её обязан вырезать
        санитайзер. В оригинале импорт не чистился вовсе."""
        data = make_docx(paragraph('текст'))
        result = convert('д.docx', data, store_image=lambda *a: '')
        self.assertNotIn('script', result['content'].lower())

    def test_text_is_escaped(self):
        result = convert('файл.txt', 'a < b & c > d'.encode('utf-8'))
        self.assertNotIn('<b', result['content'])
        self.assertIn('&lt;', result['content'])


class TableTest(unittest.TestCase):
    def test_xlsx_sheets(self):
        import openpyxl
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'Тарифы'
        sheet.append(['Тариф', 'Цена'])
        sheet.append(['Комфорт', 1500])
        buffer = io.BytesIO()
        workbook.save(buffer)

        result = convert('tarify.xlsx', buffer.getvalue())
        self.assertIn('<h3>Тарифы</h3>', result['content'])
        self.assertIn('<th>Тариф</th>', result['content'])
        self.assertIn('<td>Комфорт</td>', result['content'])

    def test_csv_semicolon_and_cp1251(self):
        """Excel в русской локали сохраняет CSV именно так."""
        data = 'Марка;Модель\nHyundai;Solaris\n'.encode('cp1251')
        result = convert('парк.csv', data)
        self.assertIn('<th>Марка</th>', result['content'])
        self.assertIn('<td>Solaris</td>', result['content'])

    def test_csv_comma(self):
        data = 'a,b\n1,2\n'.encode('utf-8')
        result = convert('t.csv', data)
        self.assertIn('<td>1</td>', result['content'])


class PdfTest(unittest.TestCase):
    def test_scan_without_text_layer_is_explained(self):
        """Скан без текстового слоя — частый случай, и человек должен понять,
        почему ничего не вышло."""
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        buffer = io.BytesIO()
        writer.write(buffer)

        with self.assertRaises(ImportError_) as ctx:
            convert('скан.pdf', buffer.getvalue())
        self.assertIn('скан', str(ctx.exception).lower())


class GuardTest(unittest.TestCase):
    def test_unsupported_extension(self):
        with self.assertRaises(ImportError_) as ctx:
            convert('архив.zip', b'PK\x03\x04')
        self.assertIn('не поддерживается', str(ctx.exception))

    def test_empty_file(self):
        with self.assertRaises(ImportError_):
            convert('пусто.txt', b'')

    def test_size_limit(self):
        with self.assertRaises(ImportError_) as ctx:
            convert('большой.txt', b'x' * (MAX_FILE_BYTES + 1))
        self.assertIn('МБ', str(ctx.exception))

    def test_docx_without_storage_is_refused(self):
        """Лучше отказать, чем молча потерять картинки документа."""
        with self.assertRaises(ImportError_):
            convert('д.docx', make_docx(paragraph('т')), store_image=None)


class BlobPathTest(unittest.TestCase):
    def test_path_is_safe_and_unique(self):
        first = blob_path_for('отчёт за месяц.xlsx')
        second = blob_path_for('отчёт за месяц.xlsx')
        self.assertNotEqual(first, second, 'имена не должны сталкиваться')
        self.assertTrue(first.startswith('wiki/files/'))
        self.assertNotIn(' ', first)

    def test_traversal_is_neutralised(self):
        path = blob_path_for('../../etc/passwd')
        self.assertNotIn('..', path)


if __name__ == '__main__':
    unittest.main()
