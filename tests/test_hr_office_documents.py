"""«Прием»: шаблоны документов, заполнение Word по меткам, АВР по форме Р-1.

Шаблоны лежат в публичном репозитории — в них не должно быть ни реквизитов компании,
ни ИИН, ни путей и связей с чужими файлами: всё это приходит из справочника и карточки.
Тест сторожит это ПРИЗНАКАМИ (12 цифр подряд, счёт KZ…, путь C:\\Users), а не списком
настоящих значений: такой список сам выложил бы их в репозиторий (разбор 10.11.2026)."""
import re
import unittest
import zipfile
from datetime import date
from decimal import Decimal
from io import BytesIO

from docx import Document
from docx.oxml.ns import qn
from openpyxl import load_workbook

from hr_office import documents

LEAK_PATTERNS = (
    re.compile(r'(?<!\d)\d{12}(?!\d)'),                 # ИИН/БИН
    re.compile(r'KZ\d{2}[0-9A-Z]{16}'),                   # счёт (ИИК)
    re.compile(r'[A-Z]{4}KZ[A-Z0-9]{2}(?![A-Za-z])'),     # БИК банка
    re.compile(r'[A-Za-z]:\\\\?Users', re.IGNORECASE),     # путь к файлу на чьём-то компьютере
)


def docx_text(content):
    document = Document(BytesIO(content))
    parts = [document.element.body]
    for section in document.sections:
        parts += [section.header._element, section.footer._element]
    return '\n'.join(''.join(node.text or '' for node in paragraph.iter(qn('w:t')))
                     for part in parts for paragraph in part.iter(qn('w:p')))


PERSON = {
    'name': 'Тестова Анна Сергеевна', 'gender': 'female', 'iin': '900101400013', 'doc_type': 'id_card',
    'doc_number': '012345678', 'doc_issuer': 'МВД РК', 'doc_issued_on': date(2020, 2, 1),
    'registration_address': 'г. Алматы, ул. Тестовая, 1', 'phone': '+77010000000', 'birth_date': date(1990, 1, 1),
    'citizenship': 'Казахстан', 'job_title': 'HR менеджер',
}
COMPANY = {
    'name': 'Тест Рент', 'kind': 'ip', 'bin': '900101300015', 'legal_address': 'г. Алматы, пр. Тестовый, 2',
    'bank_name': 'АО «Тест Банк»', 'iik': 'KZ000000000000000000', 'bik': 'TESTKZKA', 'kbe': '19',
    'in_person': 'в лице директора Тестовой Т.Т., действующей на основании талона', 'signer_name': 'Тестова Т.Т.',
    'signer_position': 'Директор', 'city': 'Алматы',
}
CONTRACT = {'number': '15/2026', 'signed_on': date(2026, 11, 10), 'amount': Decimal('250000'), 'job_title': 'HR менеджер'}


class TemplateHygieneTests(unittest.TestCase):
    def test_default_templates_have_no_requisites_or_paths(self):
        for kind, _title in documents.DOC_KINDS:
            with self.subTest(kind=kind):
                content = documents.default_template(kind)
                with zipfile.ZipFile(BytesIO(content)) as archive:
                    raw = '\n'.join(archive.read(name).decode('utf-8', 'ignore') for name in archive.namelist()
                                    if name.endswith('.xml') or name.endswith('.rels'))
                for pattern in LEAK_PATTERNS:
                    self.assertIsNone(pattern.search(raw), pattern.pattern)

    def test_default_templates_have_no_links_to_other_files(self):
        # В исходном договоре HR было поле LINK на Excel с рабочего стола сотрудника.
        for kind, _title in documents.DOC_KINDS:
            with self.subTest(kind=kind):
                with zipfile.ZipFile(BytesIO(documents.default_template(kind))) as archive:
                    xml = archive.read('word/document.xml')
                self.assertFalse(documents._has_external_field(xml))
                self.assertNotIn(b'instrText', xml)

    def test_default_templates_use_only_known_markers(self):
        for kind, _title in documents.DOC_KINDS:
            with self.subTest(kind=kind):
                report = documents.check_template(documents.default_template(kind), f'{kind}.docx')
                self.assertEqual(report['unknown'], [])
                self.assertIn('ФИО', report['markers'])

    def test_avr_template_has_no_stray_cells(self):
        sheet = load_workbook(documents.AVR_TEMPLATE).active
        self.assertIsNone(sheet['HH20'].value)


class FillTests(unittest.TestCase):
    def test_contract_is_filled_completely(self):
        values = documents.document_values(PERSON, COMPANY, CONTRACT, date(2026, 11, 10))
        text = docx_text(documents.fill_docx(documents.default_template('contract'), values))
        self.assertNotRegex(text, r'\{\{[^}]+\}\}')
        self.assertIn('ДОГОВОР ВОЗМЕЗДНОГО ОКАЗАНИЯ УСЛУГ № 15/2026', text)
        self.assertIn('ИП «Тест Рент», ИИН 900101300015, в лице директора Тестовой Т.Т.', text)
        self.assertIn('проживающая', text)          # род исполнителя — из карточки
        self.assertIn('250 000 (двести пятьдесят тысяч) тенге', text)
        self.assertIn('«10» ноября 2026 г.', text)

    def test_marker_split_across_runs_is_filled(self):
        document = Document()
        paragraph = document.add_paragraph()
        for piece in ('Исполнитель: {{Ф', 'ИО}}', ', ИИН {{ИИН}}'):
            paragraph.add_run(piece)
        buffer = BytesIO()
        document.save(buffer)
        filled = docx_text(documents.fill_docx(buffer.getvalue(), {'ФИО': 'Тестова А.С.', 'ИИН': '1'}))
        self.assertEqual(filled.strip(), 'Исполнитель: Тестова А.С., ИИН 1')

    def test_unknown_marker_stays_visible(self):
        document = Document()
        document.add_paragraph('Привет, {{ОПЕЧАТКА}}')
        buffer = BytesIO()
        document.save(buffer)
        self.assertIn('{{ОПЕЧАТКА}}', docx_text(documents.fill_docx(buffer.getvalue(), {'ФИО': 'x'})))

    def test_missing_data_leaves_blank_lines_to_fill_by_hand(self):
        values = documents.document_values({'name': 'Тестов Тест'}, {}, {}, date(2026, 11, 10))
        self.assertTrue(values['ИИН'].startswith('_'))
        self.assertTrue(values['СУММА'].startswith('_'))
        self.assertEqual(values['ИМЕНУЕМЫЙ'], 'именуемый(ая)')

    def test_bundle_for_gph_is_zip_of_four_documents(self):
        templates = [(kind, documents.default_template(kind)) for kind in documents.KINDS_BY_EMPLOYMENT['gph']]
        name, payload = documents.documents_bundle(PERSON, COMPANY, CONTRACT, templates, date(2026, 11, 10))
        self.assertTrue(name.endswith('.zip'))
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            self.assertEqual(len(archive.namelist()), 4)

    def test_single_document_is_plain_docx(self):
        name, payload = documents.documents_bundle(PERSON, COMPANY, CONTRACT,
                                                   [('questionnaire', documents.default_template('questionnaire'))],
                                                   date(2026, 11, 10))
        self.assertTrue(name.endswith('Анкета.docx'))
        self.assertIn('Фамилия Тестова', docx_text(payload))


class CheckTemplateTests(unittest.TestCase):
    def test_rejects_not_docx(self):
        with self.assertRaises(documents.DocumentError):
            documents.check_template(b'hello', 'a.docx')
        with self.assertRaises(documents.DocumentError):
            documents.check_template(documents.default_template('nda'), 'a.pdf')

    @staticmethod
    def _patched(member, transform):
        source = BytesIO(documents.default_template('nda'))
        target = BytesIO()
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, 'w') as patched:
            for item in original.infolist():
                data = original.read(item.filename)
                if item.filename == member:
                    data = transform(data)
                patched.writestr(item, data)
        return target.getvalue()

    def test_rejects_external_links_in_any_quotes(self):
        for quote in (b'"', b"'"):
            with self.subTest(quote=quote):
                content = self._patched('word/_rels/document.xml.rels', lambda data, q=quote: data.replace(
                    b'</Relationships>',
                    b'<Relationship Id="rIdX" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                    b'hyperlink" Target="http://example.com" TargetMode = ' + q + b'External' + q +
                    b'/></Relationships>'))
                with self.assertRaises(documents.DocumentError):
                    documents.check_template(content, 'a.docx')

    def test_rejects_link_and_include_fields(self):
        for field in (b' LINK Excel.Sheet.12 "C:\\x.xlsx" ', b' INCLUDEPICTURE "http://example.com/p.png" \\d '):
            with self.subTest(field=field):
                content = self._patched('word/document.xml', lambda data, f=field: data.replace(
                    b'</w:body>', b'<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>' + f +
                    b'</w:instrText></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p></w:body>', 1))
                with self.assertRaises(documents.DocumentError):
                    documents.check_template(content, 'a.docx')

    def test_rejects_macros(self):
        source = BytesIO(documents.default_template('nda'))
        target = BytesIO()
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, 'w') as patched:
            for item in original.infolist():
                patched.writestr(item, original.read(item.filename))
            patched.writestr('word/vbaProject.bin', b'macro')
        with self.assertRaises(documents.DocumentError):
            documents.check_template(target.getvalue(), 'a.docx')


class ActsWorkbookTests(unittest.TestCase):
    def test_one_sheet_per_person_with_form_fields(self):
        act = {'person': PERSON, 'company': COMPANY, 'contract': CONTRACT, 'number': 'АВР-1/2026',
               'act_date': date(2026, 10, 31), 'period_from': date(2026, 10, 1), 'period_to': date(2026, 10, 31),
               'work_name': 'Услуги HR менеджера', 'amount': Decimal('250000')}
        second = dict(act, person=dict(PERSON, name='Второва Вера'))
        book = load_workbook(BytesIO(documents.acts_workbook([act, second])))
        self.assertEqual(len(book.sheetnames), 2)
        sheet = book[book.sheetnames[0]]
        self.assertTrue(sheet['E9'].value.startswith('ИП «Тест Рент»'))
        self.assertEqual(sheet['AQ11'].value, '900101400013')
        self.assertEqual(sheet['F13'].value, 'Договор № 15/2026 от «10» ноября 2026 г.')
        self.assertEqual(sheet['AH15'].value, 'АВР-1/2026')
        self.assertEqual(sheet['C20'].value, 'Услуги HR менеджера')
        self.assertEqual(sheet['P20'].value, 'октябрь 2026 г.')
        self.assertEqual(sheet['AR20'].value, 250000.0)
        self.assertEqual(sheet['AR29'].value, 'Тестова Т.Т.')


if __name__ == '__main__':
    unittest.main()
