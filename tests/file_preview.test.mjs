// Просмотр вложений без скачивания (src/components/file_preview): разбор файлов.
//
// Пакеты Office собираются прямо в тесте через JSZip — без двоичных образцов в
// репозитории. Проверяется то, что человек увидит в окне: даты Excel датами,
// а не числом дней; нумерация списков Word; объединённые ячейки; кириллица
// в CSV из Windows; что скрипт из файла не попадёт в ссылку.
import test from 'node:test';
import assert from 'node:assert/strict';
import JSZip from 'jszip';

import {
    fileTypeLabel, formatFileSize, mediaTypeOf, officeKindFromEntries, plural, previewKind, sniffKind, unsupportedReason,
} from '../src/components/file_preview/fileKinds.js';
import { decodeTextBytes, prettyJson } from '../src/components/file_preview/textDecode.js';
import { attr, child, findAll, parseXml, textContent } from '../src/components/file_preview/xmlTree.js';
import {
    columnName, excelSerialToParts, formatCell, formatDateValue, formatNumber, isDateFormat,
} from '../src/components/file_preview/xlsxFormat.js';
import { CHAR_PX, parseXlsx, readSheet, sheetToTsv } from '../src/components/file_preview/xlsxParse.js';
import { formatListNumber, parseDocx } from '../src/components/file_preview/docxParse.js';
import { parsePptx } from '../src/components/file_preview/pptxParse.js';
import { createPreviewCache } from '../src/components/file_preview/previewCache.js';
import {
    DEFAULT_THEME, INDEXED_COLORS, applyTint, readBookStyles, readThemeColors, resolveColor, strongerBorder,
} from '../src/components/file_preview/xlsxStyle.js';
import { applyConditionalRules, readConditionalRules } from '../src/components/file_preview/xlsxConditional.js';
import { UnsupportedFormula, compileFormula, compareValues } from '../src/components/file_preview/xlsxFormula.js';
import {
    MOCKUP_CSP, MOCKUP_ESCAPE_MESSAGE, MOCKUP_SANDBOX, acceptMockupEscape, buildHtmlPreview, createLeaveWatch,
    htmlViewModes, prepareMockupHtml, shownHtmlMode,
} from '../src/components/file_preview/htmlMockup.js';

const NBSP = ' ';
const W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"';
const REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
const PNG = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13]);

const pack = async (files) => {
    const zip = new JSZip();
    Object.entries(files).forEach(([path, content]) => zip.file(path, content));
    return zip.generateAsync({ type: 'uint8array' });
};
const runText = (block) => block.runs.map((run) => run.text ?? '').join('');

/* ── Вид файла ── */

test('вид файла — по расширению, без расширения — по типу из базы', () => {
    assert.equal(previewKind({ file_name: 'Отчёт.PDF' }).kind, 'pdf');
    assert.equal(previewKind({ file_name: 'ТЗ.docx' }).kind, 'docx');
    assert.equal(previewKind({ file_name: 'план.xlsx' }).kind, 'xlsx');
    assert.equal(previewKind({ file_name: 'deck.pptx' }).kind, 'pptx');
    assert.equal(previewKind({ file_name: 'выгрузка.csv' }).kind, 'csv');
    assert.equal(previewKind({ file_name: 'старый.doc' }).kind, 'legacy');
    assert.equal(previewKind({ file_name: 'архив.zip' }).kind, 'archive');
    assert.equal(previewKind({ file_name: 'png', content_type: 'image/png' }).kind, 'image');
    assert.equal(previewKind({ file_name: 'blob', content_type: 'application/pdf' }).kind, 'pdf');
    assert.equal(previewKind({ file_name: 'x', content_type: 'application/octet-stream' }).kind, 'unknown');
    // Текст читается обычным шрифтом, код и журналы — моноширинным.
    assert.equal(previewKind({ file_name: 'заметка.txt' }).mono, false);
    assert.equal(previewKind({ file_name: 'server.log' }).mono, true);
});

test('файл без расширения и без типа узнаётся по первым байтам', () => {
    const ascii = (text) => Uint8Array.from([...text].map((char) => char.charCodeAt(0)));
    assert.equal(sniffKind(ascii('%PDF-1.7\n')), 'pdf');
    assert.equal(sniffKind(PNG), 'image');
    assert.equal(sniffKind(Uint8Array.from([0xff, 0xd8, 0xff, 0xe0])), 'image');
    assert.equal(sniffKind(Uint8Array.from([0x50, 0x4b, 0x03, 0x04])), 'zip');
    assert.equal(sniffKind(Uint8Array.from([0xd0, 0xcf, 0x11, 0xe0, 0xa1])), 'legacy');
    assert.equal(sniffKind(Uint8Array.from([0, 0, 0, 24, ...ascii('ftypheic')])), 'image');
    assert.equal(sniffKind(Uint8Array.from([0, 0, 0, 24, ...ascii('ftypisom')])), 'video');
    assert.equal(sniffKind(new TextEncoder().encode('Привет, это текст')), 'text');
    assert.equal(sniffKind(Uint8Array.from([1, 2, 0, 4, 5])), 'unknown');
    assert.equal(officeKindFromEntries(['[Content_Types].xml', 'word/document.xml']), 'docx');
    assert.equal(officeKindFromEntries(['xl/workbook.xml']), 'xlsx');
    assert.equal(officeKindFromEntries(['ppt/presentation.xml']), 'pptx');
    assert.equal(officeKindFromEntries(['readme.txt']), 'archive');
});

test('подписи: тип, размер как в Finder, падежи', () => {
    assert.equal(fileTypeLabel('docx', 'docx'), 'Документ Word');
    assert.equal(fileTypeLabel('legacy', 'xls'), 'Файл XLS');
    assert.equal(formatFileSize(845), '845 Б');
    assert.equal(formatFileSize(12 * 1024), '12 КБ');
    assert.equal(formatFileSize(1.25 * 1024 * 1024), '1,3 МБ');
    assert.equal(formatFileSize(0), '');
    assert.equal(plural(1, 'страница', 'страницы', 'страниц'), 'страница');
    assert.equal(plural(3, 'страница', 'страницы', 'страниц'), 'страницы');
    assert.equal(plural(12, 'страница', 'страницы', 'страниц'), 'страниц');
    assert.equal(plural(21, 'страница', 'страницы', 'страниц'), 'страница');
    assert.match(unsupportedReason('legacy', 'doc'), /\.docx/);
    assert.equal(mediaTypeOf('SVG'), 'image/svg+xml');
});

/* ── Текст ── */

test('текст: UTF-8, метка BOM, UTF-16 и кириллица Windows-1251', () => {
    assert.equal(decodeTextBytes(new TextEncoder().encode('Ёлка')).text, 'Ёлка');
    assert.equal(decodeTextBytes(Uint8Array.from([0xef, 0xbb, 0xbf, 0x41])).text, 'A');
    assert.equal(decodeTextBytes(Uint8Array.from([0xff, 0xfe, 0x1f, 0x04, 0x40, 0x04])).text, 'Пр');
    // «Привет;Мир» в windows-1251 — так сохраняет CSV русский Excel.
    const cp1251 = Uint8Array.from([0xcf, 0xf0, 0xe8, 0xe2, 0xe5, 0xf2, 0x3b, 0xcc, 0xe8, 0xf0]);
    const decoded = decodeTextBytes(cp1251);
    assert.equal(decoded.encoding, 'windows-1251');
    assert.equal(decoded.text, 'Привет;Мир');
    assert.equal(prettyJson('{"a":[1,2]}'), '{\n  "a": [\n    1,\n    2\n  ]\n}');
    assert.equal(prettyJson('не json'), 'не json');
});

test('XML: сущности, CDATA, пробел внутри <t> сохраняется, между тегами — нет', () => {
    const root = parseXml('<?xml version="1.0"?><a x="1 &amp; 2"><w:t xml:space="preserve"> да </w:t>\n  <b><![CDATA[<сырой>]]></b><c>&#1071;&lt;</c></a>');
    const a = child(root, 'a');
    assert.equal(attr(a, 'x'), '1 & 2');
    assert.equal(textContent(child(a, 't')), ' да ');
    assert.equal(textContent(child(a, 'b')), '<сырой>');
    assert.equal(textContent(child(a, 'c')), 'Я<');
    assert.equal(a.children.filter((item) => typeof item === 'string').length, 0);
    assert.equal(findAll(root, 't').length, 1);
    assert.equal(attr(child(a, 't'), 'space'), 'preserve');
});

/* ── Excel: значения ── */

test('дни Excel → даты, включая выдуманное 29.02.1900 и систему 1904', () => {
    const day = (serial, date1904) => {
        const { year, month, day: d } = excelSerialToParts(serial, date1904);
        return `${year}-${month}-${d}`;
    };
    assert.equal(day(1), '1900-1-1');
    assert.equal(day(59), '1900-2-28');
    assert.equal(day(61), '1900-3-1');
    assert.equal(day(45000), '2023-3-15');
    assert.equal(day(46339), '2026-11-13');
    assert.equal(day(0, true), '1904-1-1');
    const time = excelSerialToParts(46339.5 + 1 / 1440);
    assert.equal(`${time.hours}:${time.minutes}`, '12:1');
});

test('формат ячейки: дата датой, время, проценты, тенге, общий', () => {
    assert.equal(isDateFormat('dd.mm.yyyy'), true);
    assert.equal(isDateFormat('[$-419]d mmmm yyyy'), true);
    assert.equal(isDateFormat('#,##0.00 "₸"'), false);
    assert.equal(isDateFormat('0.00%'), false);
    assert.equal(isDateFormat('General'), false);
    assert.equal(isDateFormat('mm:ss.0'), true);
    assert.equal(formatCell('46339', 'n', 'dd.mm.yyyy'), '13.11.2026');
    assert.equal(formatCell('46339.75', 'n', 'dd.mm.yyyy hh:mm'), '13.11.2026 18:00');
    assert.equal(formatCell('0.395833333', 'n', 'h:mm'), '09:30');
    assert.equal(formatCell('1.5', 'n', '[h]:mm:ss'), '36:00:00');
    assert.equal(formatDateValue(46339, 'mmmm yyyy'), 'ноябрь 2026');
    assert.equal(formatCell('0.125', 'n', '0.0%'), '12,5%');
    assert.equal(formatCell('1500.5', 'n', '#,##0.00 "₸"'), `1${NBSP}500,50 ₸`);
    assert.equal(formatCell('-1234', 'n', '#,##0'), `-1${NBSP}234`);
    assert.equal(formatCell('3.5', 'n', 'General'), '3,5');
    assert.equal(formatCell('77011234567', 'n', 'General'), '77011234567');
    assert.equal(formatCell('0.1', 'n', undefined), '0,1');
    assert.equal(formatCell('1', 'b'), 'ИСТИНА');
    assert.equal(formatCell('#DIV/0!', 'e'), '#DIV/0!');
    assert.equal(formatCell('00123', 'n', '@'), '00123');
    assert.equal(formatNumber(2, '[$₸-43F] #,##0'), '₸ 2');
    assert.equal(columnName(0), 'A');
    assert.equal(columnName(27), 'AB');
});

/* ── Excel: книга ── */

const workbookPackage = (sheetXml, extra = {}) => pack({
    '[Content_Types].xml': '<Types/>',
    '_rels/.rels': `<Relationships><Relationship Id="r1" Type="${REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>`,
    'xl/workbook.xml': `<workbook xmlns:r="${REL}"><workbookPr/><sheets>
        <sheet name="План" sheetId="1" r:id="rId1"/>
        <sheet name="Секрет" sheetId="2" state="hidden" r:id="rId2"/>
        <sheet name="Второй" sheetId="3" r:id="rId3"/></sheets></workbook>`,
    'xl/_rels/workbook.xml.rels': `<Relationships>
        <Relationship Id="rId1" Type="${REL}/worksheet" Target="worksheets/sheet1.xml"/>
        <Relationship Id="rId2" Type="${REL}/worksheet" Target="worksheets/sheet2.xml"/>
        <Relationship Id="rId3" Type="${REL}/worksheet" Target="/xl/worksheets/sheet3.xml"/>
        <Relationship Id="rId4" Type="${REL}/sharedStrings" Target="sharedStrings.xml"/>
        <Relationship Id="rId5" Type="${REL}/styles" Target="styles.xml"/></Relationships>`,
    'xl/sharedStrings.xml': '<sst><si><t>Дата</t></si><si><r><t>Сум</t></r><r><t>ма</t></r></si><si><t>Итого &amp; всё</t></si><si><t>Ф</t><rPh><t>фонетика</t></rPh></si></sst>',
    'xl/styles.xml': `<styleSheet><numFmts><numFmt numFmtId="164" formatCode="#,##0.00&quot; ₸&quot;"/></numFmts>
        <fonts><font/><font><b/></font></fonts>
        <cellXfs><xf numFmtId="0" fontId="0"/><xf numFmtId="14" fontId="0"/><xf numFmtId="164" fontId="0"/><xf numFmtId="0" fontId="1"/><xf numFmtId="9" fontId="0"/></cellXfs></styleSheet>`,
    'xl/worksheets/sheet1.xml': sheetXml,
    'xl/worksheets/sheet2.xml': '<worksheet><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>скрыто</t></is></c></row></sheetData></worksheet>',
    'xl/worksheets/sheet3.xml': '<worksheet><sheetData><row r="2"><c r="B2"><v>3.14159</v></c></row></sheetData></worksheet>',
    ...extra,
});

const SHEET = `<worksheet><dimension ref="A1:D9"/><sheetData>
    <row r="1"><c r="A1" t="s" s="3"><v>0</v></c><c r="B1" t="s" s="3"><v>1</v></c><c r="D1" t="s"><v>3</v></c></row>
    <row r="2"><c r="A2" s="1"><v>46339</v></c><c r="B2" s="2"><v>1500.5</v></c><c r="C2" s="4"><v>0.5</v></c></row>
    <row r="3"><c r="A3" s="1"/><c r="B3"><f>B2*2</f><v>3001</v></c></row>
    <row r="5"><c r="A5" t="s"><v>2</v></c></row>
    </sheetData><mergeCells count="1"><mergeCell ref="A5:C5"/></mergeCells></worksheet>`;

test('книга Excel: листы по порядку, скрытый пропущен, значения как в Excel', async () => {
    const book = await parseXlsx(await workbookPackage(SHEET));
    assert.deepEqual(book.sheets.map((sheet) => sheet.name), ['План', 'Второй']);
    assert.equal(book.active, 0, 'без bookViews — первый лист');
    const [plan, second] = book.sheets;
    assert.equal(plan.rows[0][0].text, 'Дата');
    assert.equal(plan.rows[0][0].bold, true);
    assert.equal(plan.rows[0][1].text, 'Сумма');
    assert.equal(plan.rows[0][3].text, 'Ф', 'фонетика не попадает в текст');
    assert.equal(plan.rows[1][0].text, '13.11.2026');
    assert.equal(plan.rows[1][1].text, `1${NBSP}500,50 ₸`);
    assert.equal(plan.rows[1][1].numeric, true);
    assert.equal(plan.rows[1][2].text, '50%');
    assert.equal(plan.rows[2][0], undefined, 'пустая ячейка со стилем — пустая');
    assert.equal(plan.rows[2][1].text, '3001', 'формула показывает посчитанное значение');
    assert.equal(plan.rows[4][0].text, 'Итого & всё');
    assert.deepEqual(plan.merges, [{ row: 4, col: 0, rowSpan: 1, colSpan: 3 }]);
    assert.equal(plan.rowCount, 5);
    assert.equal(plan.colCount, 4);
    assert.equal(plan.totalRows, 9);
    assert.equal(second.rows[1][1].text, '3,14159');
    assert.equal(sheetToTsv(plan).split('\n')[1], `13.11.2026\t1${NBSP}500,50 ₸\t50%`);
});

test('длинный лист читается до предела строк и помечается обрезанным', () => {
    const rows = Array.from({ length: 50 }, (_, index) => `<row r="${index + 1}"><c r="A${index + 1}"><v>${index}</v></c></row>`).join('');
    const sheet = readSheet(`<worksheet><dimension ref="A1:A50"/><sheetData>${rows}</sheetData></worksheet>`, { maxRows: 10 });
    assert.equal(sheet.rowCount, 10);
    assert.equal(sheet.truncatedRows, true);
    assert.equal(sheet.totalRows, 50);
});

test('книга открывается на активном листе, скрытые листы не сбивают счёт', async () => {
    const pkg = await workbookPackage(SHEET);
    const zip = await JSZip.loadAsync(pkg);
    const workbook = (await zip.file('xl/workbook.xml').async('string')).replace('<workbookPr/>', '<workbookPr/><bookViews><workbookView activeTab="2"/></bookViews>');
    zip.file('xl/workbook.xml', workbook);
    const book = await parseXlsx(await zip.generateAsync({ type: 'uint8array' }));
    assert.equal(book.sheets[book.active].name, 'Второй', 'activeTab 2 — третий лист книги, второй видимый');
});

test('книга 1904 года и битый файл', async () => {
    const pkg = await workbookPackage(SHEET);
    const zip = await JSZip.loadAsync(pkg);
    zip.file('xl/workbook.xml', (await zip.file('xl/workbook.xml').async('string')).replace('<workbookPr/>', '<workbookPr date1904="1"/>'));
    const book = await parseXlsx(await zip.generateAsync({ type: 'uint8array' }));
    assert.equal(book.sheets[0].rows[1][0].text, '14.11.2030');
    await assert.rejects(parseXlsx(Uint8Array.from([1, 2, 3])), { name: 'PreviewError' });
});

/* ── Word ── */

const DOCUMENT = `<w:document ${W} xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><w:body>
  <w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>Регламент</w:t></w:r></w:p>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/><w:jc w:val="center"/></w:pPr><w:r><w:t>Общие положения</w:t></w:r></w:p>
  <w:p><w:r><w:t xml:space="preserve">Ответить </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>за 30 секунд</w:t></w:r><w:r><w:rPr><w:b w:val="0"/></w:rPr><w:t>, вежливо</w:t></w:r><w:r><w:tab/><w:t>таб</w:t></w:r></w:p>
  <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Первый</w:t></w:r></w:p>
  <w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Вложенный</w:t></w:r></w:p>
  <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Второй</w:t></w:r></w:p>
  <w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Снова вложенный</w:t></w:r></w:p>
  <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>Маркер</w:t></w:r></w:p>
  <w:p><w:hyperlink r:id="rIdLink"><w:r><w:t>сайт</w:t></w:r></w:hyperlink><w:hyperlink r:id="rIdEvil"><w:r><w:t>ловушка</w:t></w:r></w:hyperlink></w:p>
  <w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>PAGE</w:instrText></w:r><w:r><w:t>7</w:t></w:r><w:del><w:r><w:delText>удалено</w:delText></w:r></w:del><w:ins><w:r><w:t>вставлено</w:t></w:r></w:ins></w:p>
  <w:p><w:r><mc:AlternateContent><mc:Choice><w:drawing><a:blip r:embed="rIdImg"/></w:drawing></mc:Choice><mc:Fallback><w:pict/></mc:Fallback></mc:AlternateContent></w:r></w:p>
  <w:tbl>
    <w:tr><w:trPr><w:tblHeader/></w:trPr><w:tc><w:p><w:r><w:t>Город</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Цена</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Срок</w:t></w:r></w:p></w:tc></w:tr>
    <w:tr><w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>Алматы</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>500</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>1 день</w:t></w:r></w:p></w:tc></w:tr>
    <w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>по запросу</w:t></w:r></w:p></w:tc></w:tr>
  </w:tbl>
  <w:sectPr/>
</w:body></w:document>`;

const docxPackage = () => pack({
    '[Content_Types].xml': '<Types/>',
    '_rels/.rels': `<Relationships><Relationship Id="r1" Type="${REL}/officeDocument" Target="word/document.xml"/></Relationships>`,
    'word/document.xml': DOCUMENT,
    'word/_rels/document.xml.rels': `<Relationships>
        <Relationship Id="rIdStyles" Type="${REL}/styles" Target="styles.xml"/>
        <Relationship Id="rIdNum" Type="${REL}/numbering" Target="numbering.xml"/>
        <Relationship Id="rIdLink" Type="${REL}/hyperlink" Target="https://example.com/a?b=1" TargetMode="External"/>
        <Relationship Id="rIdEvil" Type="${REL}/hyperlink" Target="javascript:alert(1)" TargetMode="External"/>
        <Relationship Id="rIdImg" Type="${REL}/image" Target="media/image1.png"/></Relationships>`,
    'word/styles.xml': `<w:styles ${W}>
        <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/></w:style>
        <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style></w:styles>`,
    'word/numbering.xml': `<w:numbering ${W}>
        <w:abstractNum w:abstractNumId="10"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
            <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%1.%2)"/></w:lvl></w:abstractNum>
        <w:abstractNum w:abstractNumId="20"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/><w:lvlText w:val=""/></w:lvl></w:abstractNum>
        <w:num w:numId="1"><w:abstractNumId w:val="10"/></w:num>
        <w:num w:numId="2"><w:abstractNumId w:val="20"/></w:num></w:numbering>`,
    'word/media/image1.png': PNG,
});

test('Word: «Название», заголовок, жирный, табуляция и выравнивание', async () => {
    const doc = await parseDocx(await docxPackage());
    const [title, heading, body] = doc.blocks;
    assert.equal(title.heading, 0);
    assert.equal(runText(title), 'Регламент');
    assert.equal(heading.heading, 1);
    assert.equal(heading.align, 'center');
    assert.deepEqual(body.runs.map((run) => [run.text, run.bold]), [
        ['Ответить ', false], ['за 30 секунд', true], [', вежливо\tтаб', false],
    ]);
});

test('Word: нумерация списков как в Word — 1., 1.a), 2., 2.a) заново; маркеры', async () => {
    const doc = await parseDocx(await docxPackage());
    const lists = doc.blocks.filter((block) => block.list).map((block) => [block.list.label, block.list.level, runText(block)]);
    assert.deepEqual(lists, [
        ['1.', 0, 'Первый'], ['1.a)', 1, 'Вложенный'], ['2.', 0, 'Второй'], ['2.a)', 1, 'Снова вложенный'], ['•', 0, 'Маркер'],
    ]);
    assert.equal(formatListNumber(4, 'upperRoman'), 'IV');
    assert.equal(formatListNumber(3, 'russianLower'), 'в');
});

test('Word: ссылки только http(s), коды полей и удалённый текст не видны, картинка из пакета', async () => {
    const doc = await parseDocx(await docxPackage());
    const links = doc.blocks.flatMap((block) => block.runs || []).filter((run) => run.text === 'сайт' || run.text === 'ловушка');
    assert.equal(links.find((run) => run.text === 'сайт').href, 'https://example.com/a?b=1');
    assert.equal(links.find((run) => run.text === 'ловушка').href, null, 'javascript: не становится ссылкой');
    const fields = doc.blocks.find((block) => block.runs?.some((run) => run.text?.includes('вставлено')));
    assert.equal(runText(fields), '7вставлено');
    const image = doc.blocks.flatMap((block) => block.runs || []).find((run) => run.image);
    assert.equal(image.image, 'word/media/image1.png');
    assert.deepEqual([...doc.media.keys()], ['word/media/image1.png']);
    assert.equal(doc.media.get('word/media/image1.png').type, 'image/png');
});

test('Word: таблица с объединением по вертикали и по горизонтали, текст для копирования', async () => {
    const doc = await parseDocx(await docxPackage());
    const table = doc.blocks.find((block) => block.type === 'table');
    assert.equal(table.rows[0].header, true);
    const shape = table.rows.map((row) => row.cells.map((cell) => [runText(cell.blocks[0]), cell.colSpan, cell.rowSpan]));
    assert.deepEqual(shape, [
        [['Город', 1, 1], ['Цена', 1, 1], ['Срок', 1, 1]],
        [['Алматы', 1, 2], ['500', 1, 1], ['1 день', 1, 1]],
        [['по запросу', 2, 1]],
    ]);
    assert.match(doc.text, /^Регламент\nОбщие положения\nОтветить за 30 секунд, вежливо\tтаб\n1\. Первый\n {2}1\.a\) Вложенный/);
    assert.match(doc.text, /Город\tЦена\tСрок\nАлматы\t500\t1 день\nпо запросу/);
});

test('Word: не документ — понятная ошибка', async () => {
    await assert.rejects(parseDocx(await pack({ 'a.txt': 'x' })), { name: 'PreviewError' });
});

/* ── PowerPoint ── */

const P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"';
const slide = (body) => `<p:sld ${P}><p:cSld><p:spTree>${body}</p:spTree></p:cSld></p:sld>`;
const shape = (type, paragraphs) => `<p:sp><p:nvSpPr><p:nvPr>${type ? `<p:ph type="${type}"/>` : ''}</p:nvPr></p:nvSpPr><p:txBody>${paragraphs}</p:txBody></p:sp>`;

test('PowerPoint: слайды в порядке показа, заголовок, пункты, таблица и картинка', async () => {
    const bytes = await pack({
        '_rels/.rels': `<Relationships><Relationship Id="r1" Type="${REL}/officeDocument" Target="ppt/presentation.xml"/></Relationships>`,
        'ppt/presentation.xml': `<p:presentation ${P}><p:sldIdLst><p:sldId id="257" r:id="rId3"/><p:sldId id="256" r:id="rId2"/></p:sldIdLst></p:presentation>`,
        'ppt/_rels/presentation.xml.rels': `<Relationships>
            <Relationship Id="rId2" Type="${REL}/slide" Target="slides/slide1.xml"/>
            <Relationship Id="rId3" Type="${REL}/slide" Target="slides/slide2.xml"/></Relationships>`,
        'ppt/slides/slide2.xml': slide(shape('ctrTitle', '<a:p><a:r><a:t>План на ноябрь</a:t></a:r></a:p>')
            + shape('subTitle', '<a:p><a:r><a:t>Отдел продаж</a:t></a:r></a:p>')),
        'ppt/slides/slide1.xml': slide(shape('title', '<a:p><a:r><a:t>Итоги</a:t></a:r></a:p>')
            + shape('body', '<a:p><a:r><a:t>Звонков: 120</a:t></a:r></a:p><a:p><a:pPr lvl="1"/><a:r><a:t>из них</a:t></a:r><a:r><a:t> входящих</a:t></a:r></a:p><a:p><a:pPr><a:buNone/></a:pPr><a:r><a:t>Без маркера</a:t></a:r></a:p>')
            + shape('sldNum', '<a:p><a:fld><a:t>2</a:t></a:fld></a:p>')
            + '<p:graphicFrame><a:graphic><a:graphicData><a:tbl><a:tr><a:tc><a:txBody><a:p><a:r><a:t>Город</a:t></a:r></a:p></a:txBody></a:tc><a:tc><a:txBody><a:p><a:r><a:t>План</a:t></a:r></a:p></a:txBody></a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>'
            + '<p:pic><p:blipFill><a:blip r:embed="rIdPic"/></p:blipFill></p:pic>'),
        'ppt/slides/_rels/slide1.xml.rels': `<Relationships><Relationship Id="rIdPic" Type="${REL}/image" Target="../media/image1.png"/></Relationships>`,
        'ppt/media/image1.png': PNG,
    });
    const deck = await parsePptx(bytes);
    assert.deepEqual(deck.slides.map((item) => [item.number, item.title]), [[1, 'План на ноябрь'], [2, 'Итоги']]);
    assert.equal(deck.slides[0].blocks[0].subtitle, true);
    const [text, table, image] = deck.slides[1].blocks;
    assert.deepEqual(text.paragraphs.map((item) => [item.text, item.level, item.bullet]), [
        ['Звонков: 120', 0, true], ['из них входящих', 1, true], ['Без маркера', 0, false],
    ]);
    assert.deepEqual(table.rows, [['Город', 'План']]);
    assert.equal(image.path, 'ppt/media/image1.png');
    assert.ok(deck.media.has('ppt/media/image1.png'));
    assert.ok(!deck.text.includes('\n2\n'), 'номер слайда из колонтитула не попадает в текст');
    assert.match(deck.text, /^Слайд 1\. План на ноябрь\nОтдел продаж\n\nСлайд 2\. Итоги\n• Звонков: 120\n {2}• из них входящих\nБез маркера\nГород\tПлан$/);
});

/* ── Память просмотра ── */

test('память просмотра: повторное открытие из памяти, старое вытесняется по объёму', () => {
    const cache = createPreviewCache(10);
    const blob = (size) => ({ size });
    cache.put('a', blob(4));
    cache.put('b', blob(4));
    assert.ok(cache.get('a'));
    cache.put('c', blob(4));
    assert.equal(cache.get('b'), null, 'вытеснен самый давний по использованию');
    assert.ok(cache.get('a'));
    assert.ok(cache.get('c'));
    cache.put('huge', blob(11));
    assert.equal(cache.get('huge'), null, 'файл больше всей памяти не кладётся');
    assert.equal(cache.size, 8);
});

/* ── HTML как макет ── */

test('HTML узнаётся по расширению, по типу и по началу файла', () => {
    assert.equal(previewKind({ file_name: 'Макет главной.html' }).kind, 'html');
    assert.equal(previewKind({ file_name: 'old.HTM' }).kind, 'html');
    assert.equal(previewKind({ file_name: 'page', content_type: 'text/html; charset=utf-8' }).kind, 'html');
    assert.equal(fileTypeLabel('html', 'html'), 'Страница HTML');
    const bytes = (text) => new TextEncoder().encode(text);
    assert.equal(sniffKind(bytes('<!DOCTYPE html><html><body>x</body></html>')), 'html');
    assert.equal(sniffKind(bytes('\uFEFF<!-- макет -->\n<html lang="ru">')), 'html');
    assert.equal(sniffKind(bytes('<?xml version="1.0"?>\n<!doctype html>')), 'html');
    assert.equal(sniffKind(bytes('Просто текст про <html> внутри')), 'text');
});

const injected = (html) => html.indexOf('<meta http-equiv="Content-Security-Policy"');

test('макет: защита встаёт первой — после doctype, только если перед ним одни пробелы', () => {
    const page = '\n  <!DOCTYPE html>\n<html lang="ru"><head><script>window.a=1</script></head><body>x</body></html>';
    const out = prepareMockupHtml(page);
    assert.equal(injected(out), '\n  <!DOCTYPE html>'.length, 'doctype остаётся первым — страница в своём режиме');
    assert.ok(injected(out) < out.indexOf('<script>window.a=1'), 'CSP раньше любого скрипта файла');
    assert.equal(injected(prepareMockupHtml('<div>без doctype</div>')), 0);
    assert.equal(injected(prepareMockupHtml('﻿<!doctype html><p>')), '<!doctype html>'.length, 'BOM не мешает');
    assert.equal(injected(prepareMockupHtml('<!doctypehtml><p>')), '<!doctypehtml>'.length);
});

test('макет: пролог, который разборщик читает иначе, не прячет скрипт до защиты (разбор 13.11, H2)', () => {
    // Каждый из этих прологов разборщик HTML закрывает раньше, чем кажется
    // регулярному выражению, и <script> оказывается настоящим — до нашей CSP.
    const tricks = [
        '<!--><script>fetch("//evil")</script>--><!doctype html><p>a1</p>',
        '<?x ><script>fetch("//evil")</script>?><!doctype html><p>a2</p>',
        '<!-- x --!><script>fetch("//evil")</script> --><!doctype html><p>a3</p>',
        '<!---><script>fetch("//evil")</script>--><!doctype html><p>a4</p>',
        ' <!doctype html><script>fetch("//evil")</script>',
        '<!-- обычный комментарий --><!doctype html><script>fetch("//evil")</script>',
        '<?xml version="1.0"?><!doctype html><script>fetch("//evil")</script>',
    ];
    tricks.forEach((page) => {
        const out = prepareMockupHtml(page);
        assert.equal(injected(out), 0, `защита первой: ${page.slice(0, 24)}`);
        assert.ok(injected(out) < out.indexOf('fetch("//evil")'));
    });
});

test('макет: обновление страницы через meta refresh вырезано, сеть и вложенные страницы закрыты', () => {
    const out = prepareMockupHtml('<!doctype html><head><META HTTP-EQUIV=Refresh CONTENT="0;url=https://evil.example"><meta http-equiv="refresh" content="5"></head>');
    assert.doesNotMatch(out, /refresh/i);
    for (const directive of ["connect-src 'none'", "form-action 'none'", "frame-src 'none'", "object-src 'none'", "worker-src 'none'"]) {
        assert.ok(MOCKUP_CSP.includes(directive), directive);
    }
    assert.ok(out.includes(`content="${MOCKUP_CSP}"`));
    assert.ok(out.includes('<meta name="referrer" content="no-referrer">'));
    // Клетка: только скрипты. Ни своего происхождения, ни форм, ни окон, ни перехода вкладки.
    assert.equal(MOCKUP_SANDBOX, 'allow-scripts');
});

test('макет: файл из тысяч незакрытых <meta не подвешивает вкладку', () => {
    // Ни одного http-equiv, но слово «refresh» есть: каждый «<meta » квадратичное
    // выражение просматривало бы до конца файла (десятки секунд); с пределом
    // длины тега — доли секунды даже под нагрузкой всего набора тестов.
    const page = `<!doctype html><title>refresh</title>${'<meta '.repeat(120000)}`;
    const started = performance.now();
    prepareMockupHtml(page);
    assert.ok(performance.now() - started < 3000, 'подготовка линейная, а не квадратичная');
});

test('макет: «Код» и «Скопировать код» — исходник, а клетке — документ с защитой; 1251 не ломается', () => {
    const cp1251 = Uint8Array.from([0xcf, 0xf0, 0xe8, 0xe2, 0xe5, 0xf2]);
    const bytes = new Uint8Array([...new TextEncoder().encode('<!doctype html><meta charset="windows-1251"><h1>'), ...cp1251,
        ...new TextEncoder().encode('</h1>')]);
    const preview = buildHtmlPreview(decodeTextBytes(bytes).text);
    assert.equal(preview.kind, 'html');
    assert.match(preview.text, /<h1>Привет<\/h1>/);
    assert.ok(!preview.text.includes('Content-Security-Policy'), 'в «Коде» нет нашей обвязки');
    assert.match(preview.srcDoc, /<h1>Привет<\/h1>/, 'srcdoc — строка: meta charset файла её не перекодирует');
    assert.ok(preview.srcDoc.includes('Content-Security-Policy'));
    assert.equal('url' in preview, false, 'никакого blob:-адреса в происхождении портала (разбор 13.11, H1)');
    assert.equal(previewKind({ file_name: 'data', content_type: 'application/xml' }).kind, 'text', 'XML — не макет');
});

test('макет: уход со страницы — первый показ, возврат, затем без скриптов; новый iframe считает заново', () => {
    const watch = createLeaveWatch();
    const first = {};
    assert.equal(watch.load(first), 'shown');
    assert.equal(watch.load(first), 'returned', 'вторая загрузка того же iframe — страница ушла');
    const second = {};
    assert.equal(watch.load(second), 'shown', 'новый iframe (возврат) — снова показ, а не уход');
    assert.equal(watch.load(second), 'scripts-off', 'ушла второй раз — без скриптов');
    const third = {};
    assert.equal(watch.load(third), 'shown');
    assert.equal(watch.load(third), 'scripts-off');
});

test('макет: Esc принимается только от своего iframe, когда фокус в нём', () => {
    const contentWindow = {};
    const frame = { contentWindow };
    const message = (source, type = MOCKUP_ESCAPE_MESSAGE) => ({ source, data: { type } });
    assert.equal(acceptMockupEscape(message(contentWindow), frame, frame), true);
    assert.equal(acceptMockupEscape(message(contentWindow), frame, { tagName: 'BODY' }), false, 'по таймеру, без нажатия в макете');
    assert.equal(acceptMockupEscape(message({}), frame, frame), false, 'чужое окно');
    assert.equal(acceptMockupEscape(message(contentWindow, 'other'), frame, frame), false);
    assert.equal(acceptMockupEscape(message(contentWindow), null, null), false);
    assert.equal(acceptMockupEscape({ source: null, data: { type: MOCKUP_ESCAPE_MESSAGE } }, { contentWindow: null }, { contentWindow: null }), false);
});

test('макет: на телефоне вида «Телефон» нет — показывается макет', () => {
    assert.deepEqual(htmlViewModes(false), ['desktop', 'phone', 'code']);
    assert.deepEqual(htmlViewModes(true), ['desktop', 'code']);
    assert.equal(shownHtmlMode('phone', true), 'desktop');
    assert.equal(shownHtmlMode('phone', false), 'phone');
    assert.equal(shownHtmlMode('code', true), 'code');
    assert.equal(shownHtmlMode('чужое', false), 'desktop');
});

test('макет: сторож не пускает по ссылкам и формам, якоря прокручивает сам, Esc уходит родителю', () => {
    const script = /<script>([\s\S]*?)<\/script>/.exec(prepareMockupHtml('<p>x</p>'))[1];
    const listeners = {};
    const scrolled = [];
    const window = {
        origin: 'null',
        top: {},
        addEventListener: (type, handler, capture) => { assert.ok(!capture, 'всплытие — после обработчиков страницы'); listeners[type] = handler; },
        scrollTo: (options) => scrolled.push(['top', options.top]),
    };
    const section = { scrollIntoView: () => scrolled.push(['tarify']) };
    const named = { scrollIntoView: () => scrolled.push(['named']) };
    const document = {
        getElementById: (id) => (id === 'tarify' ? section : null),
        getElementsByName: (name) => (name === 'старт' ? [named] : []),
    };
    const sent = [];
    const parent = { postMessage: (data, origin) => sent.push([data, origin]) };
    new Function('window', 'document', 'parent', script)(window, document, parent);
    const click = (href, defaultPrevented = false) => {
        let prevented = false;
        const link = { getAttribute: () => href };
        listeners.click({ defaultPrevented, target: { closest: () => link }, preventDefault: () => { prevented = true; } });
        return prevented;
    };
    assert.equal(click('#tarify'), true, 'якорь: переход браузера отменён…');
    assert.deepEqual(scrolled.pop(), ['tarify'], '…а раздел прокручен сторожем');
    assert.equal(click('#%D1%81%D1%82%D0%B0%D1%80%D1%82'), true);
    assert.deepEqual(scrolled.pop(), ['named'], 'якорь по name и в процентной записи');
    assert.equal(click('#'), true);
    assert.deepEqual(scrolled.pop(), ['top', 0], '«#» — в начало страницы, как в браузере');
    assert.equal(click('#нет-такого'), true);
    assert.equal(scrolled.length, 0, 'якорь без цели никуда не ведёт');
    assert.equal(click('https://evil.example/login'), true);
    assert.equal(click('page2.html'), true);
    assert.equal(click('javascript:alert(1)'), true);
    assert.equal(click('screen-2', true), false, 'страница обработала щелчок сама (роутер прототипа) — не вмешиваемся');
    let noLink = false;
    listeners.click({ target: { closest: () => null }, preventDefault: () => { noLink = true; } });
    assert.equal(noLink, false, 'щелчок не по ссылке макет не трогает');
    let submitted = true;
    listeners.submit({ defaultPrevented: false, preventDefault: () => { submitted = false; } });
    assert.equal(submitted, false);
    listeners.keydown({ key: 'Enter' });
    listeners.keydown({ key: 'Escape', defaultPrevented: true });
    listeners.keydown({ key: 'Escape' });
    assert.deepEqual(sent, [[{ type: MOCKUP_ESCAPE_MESSAGE }, '*']]);
});

test('макет: открытый вне клетки (верхней вкладкой или с настоящим происхождением) файл не исполняется', () => {
    const script = /<script>([\s\S]*?)<\/script>/.exec(prepareMockupHtml('<p>x</p>'))[1];
    for (const window of [{ origin: 'https://portal.example' }, { origin: 'null' }]) {
        if (!('top' in window)) window.top = window;
        let stopped = false;
        let listened = false;
        window.stop = () => { stopped = true; };
        window.addEventListener = () => { listened = true; };
        const document = { documentElement: { innerHTML: '<head>…</head>' } };
        new Function('window', 'document', 'parent', script)(window, document, {});
        assert.equal(stopped, true, `разбор остановлен (${window.origin})`);
        assert.equal(document.documentElement.innerHTML, '', 'страница очищена');
        assert.equal(listened, false);
    }
});

/* ── Excel: цвета, рамки, скрытое, условное форматирование (снимок «2026.xlsx», 13.11) ── */

const channelsClose = (actual, expected, tolerance = 3) => {
    const a = [1, 3, 5].map((at) => parseInt(actual.slice(at, at + 2), 16));
    const b = [1, 3, 5].map((at) => parseInt(expected.slice(at, at + 2), 16));
    return a.every((value, index) => Math.abs(value - b[index]) <= tolerance);
};

test('цвета Excel: тема с оттенком, старая палитра, прямой код, «авто»', () => {
    // «Синий, акцент 1, светлее 80 %» и «темнее 25 %» из палитры Office — эталоны самого Excel.
    assert.ok(channelsClose(applyTint('#4472C4', 0.7999816888943144), '#DAE3F3'), applyTint('#4472C4', 0.7999816888943144));
    assert.ok(channelsClose(applyTint('#4472C4', -0.249977111117893), '#2F5597'), applyTint('#4472C4', -0.249977111117893));
    assert.equal(applyTint('#FFFF00', 0), '#FFFF00');
    const palette = { theme: DEFAULT_THEME, indexed: INDEXED_COLORS };
    const node = (attrs) => ({ name: 'color', local: 'color', attrs, children: [] });
    assert.equal(resolveColor(node({ rgb: 'FFFFFF00' }), palette), '#FFFF00');
    assert.equal(resolveColor(node({ theme: '0' }), palette), '#FFFFFF', 'theme 0 — фон (lt1), не текст');
    assert.equal(resolveColor(node({ theme: '1' }), palette), '#000000', 'theme 1 — текст (dk1)');
    assert.equal(resolveColor(node({ indexed: '5' }), palette), '#FFFF00');
    assert.equal(resolveColor(node({ indexed: '64' }), palette), null, 'системный цвет — по умолчанию');
    assert.equal(resolveColor(node({ auto: '1' }), palette), null);
    const theme = readThemeColors(parseXml(`<a:theme xmlns:a="x"><a:themeElements><a:clrScheme name="t">
        <a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>
        <a:dk2><a:srgbClr val="1F497D"/></a:dk2><a:lt2><a:srgbClr val="EEECE1"/></a:lt2>
        <a:accent1><a:srgbClr val="4F81BD"/></a:accent1><a:accent2><a:srgbClr val="C0504D"/></a:accent2>
        <a:accent3><a:srgbClr val="9BBB59"/></a:accent3><a:accent4><a:srgbClr val="8064A2"/></a:accent4>
        <a:accent5><a:srgbClr val="4BACC6"/></a:accent5><a:accent6><a:srgbClr val="F79646"/></a:accent6>
        <a:hlink><a:srgbClr val="0000FF"/></a:hlink><a:folHlink><a:srgbClr val="800080"/></a:folHlink>
    </a:clrScheme></a:themeElements></a:theme>`));
    assert.deepEqual(theme.slice(0, 5), ['#FFFFFF', '#000000', '#EEECE1', '#1F497D', '#4F81BD']);
});

const STYLES_XML = `<styleSheet>
  <numFmts count="1"><numFmt numFmtId="164" formatCode="0.0"/></numFmts>
  <fonts>
    <font><sz val="11"/><color theme="1"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><color theme="1"/></font>
    <font><i/><u/><sz val="14"/><color rgb="FFC00000"/></font>
  </fonts>
  <fills>
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"><fgColor rgb="FF808080"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFFFFF00"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor theme="4" tint="0.79998168889431442"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders>
    <border><left/><right/><top/><bottom/></border>
    <border><left style="thin"><color indexed="64"/></left><right style="thin"><color indexed="64"/></right><top style="thin"><color indexed="64"/></top><bottom style="thin"><color indexed="64"/></bottom></border>
    <border><left/><right style="thick"><color rgb="FF000000"/></right><top/><bottom style="mediumDashed"><color rgb="FFFF0000"/></bottom></border>
  </borders>
  <cellXfs>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" applyFill="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="1"><alignment horizontal="center"/></xf>
    <xf numFmtId="9" fontId="2" fillId="3" borderId="2"><alignment horizontal="left" indent="2"/></xf>
    <xf numFmtId="0" fontId="0" fillId="1" borderId="0"/>
  </cellXfs>
  <dxfs>
    <dxf><font><color rgb="FF9C0006"/></font><fill><patternFill><bgColor rgb="FFFFC7CE"/></patternFill></fill></dxf>
    <dxf><font><b/><color rgb="FFFFFFFF"/></font></dxf>
    <dxf><fill><patternFill><bgColor rgb="FFC6EFCE"/></patternFill></fill></dxf>
  </dxfs>
</styleSheet>`;

test('стили книги: заливки, шрифты, рамки, выравнивание, форматы условного форматирования', () => {
    const book = readBookStyles(parseXml(STYLES_XML), DEFAULT_THEME);
    const [plain, header, number, accent, gray] = book.xfs;
    assert.equal(plain.fill, null);
    assert.equal(plain.border, null);
    assert.equal(header.fill, '#FFFF00');
    assert.equal(header.bold, true);
    assert.deepEqual([header.align, header.vAlign, header.wrap], ['center', 'middle', true]);
    assert.equal(header.border.left.css, '1px solid #000000');
    assert.equal(number.code, '0.0');
    assert.ok(channelsClose(accent.fill, '#DAE3F3'), 'тема + оттенок');
    assert.deepEqual([accent.font.italic, accent.font.underline, accent.font.size, accent.font.color], [true, true, 14, '#C00000']);
    assert.equal(accent.indent, 2);
    assert.equal(accent.border.right.css, '3px solid #000000');
    assert.equal(accent.border.bottom.css, '2px dashed #FF0000');
    assert.equal(gray.fill, null, 'gray125 — служебная заливка, не цвет');
    assert.deepEqual(book.dxfs[0], { fill: '#FFC7CE', color: '#9C0006', bold: null, italic: null, underline: null, strike: null });
    assert.equal(book.dxfs[1].bold, true);
    assert.equal(book.dxfs[1].color, '#FFFFFF');
    assert.equal(book.dxfs[2].fill, '#C6EFCE');
    assert.equal(strongerBorder(header.border.right, accent.border.right), accent.border.right, 'жирная побеждает тонкую');
});

const cellXml = (ref, value, style = 0, type = '') => (value === null
    ? `<c r="${ref}" s="${style}"/>`
    : `<c r="${ref}" s="${style}"${type ? ` t="${type}"` : ''}><v>${value}</v></c>`);

test('лист: скрытые строки и столбцы, ширины, высоты, сетка, пустые ячейки с оформлением', () => {
    const book = readBookStyles(parseXml(STYLES_XML), DEFAULT_THEME);
    const xml = `<worksheet><sheetViews><sheetView showGridLines="0" workbookViewId="0"/></sheetViews>
      <sheetFormatPr baseColWidth="10" defaultRowHeight="16"/>
      <cols><col min="1" max="1" width="12.5" customWidth="1"/><col min="3" max="3" width="9" hidden="1"/><col min="4" max="5" width="20" customWidth="1"/></cols>
      <sheetData>
        <row r="1" ht="45" customHeight="1">${cellXml('A1', 'Смена', 1, 'str')}${cellXml('B1', null, 1)}${cellXml('C1', 'скрыт', 1, 'str')}</row>
        <row r="2" hidden="1">${cellXml('A2', 'Аман', 0, 'str')}${cellXml('B2', '#DIV/0!', 0, 'e')}</row>
        <row r="3">${cellXml('A3', 'Алуа', 2, 'str')}${cellXml('B3', '16.25', 2)}${cellXml('D3', 'TRUE', 0, 'str')}${cellXml('E3', '1', 0, 'b')}</row>
        <row r="4">${cellXml('A4', null, 0)}${cellXml('B4', null, 4)}</row>
      </sheetData></worksheet>`;
    const sheet = readSheet(xml, { styles: book });
    assert.equal(sheet.gridLines, false);
    assert.deepEqual(sheet.visibleRows, [0, 2], 'скрытая строка 2 и строка без оформления не видны');
    assert.deepEqual(sheet.visibleCols, [0, 1, 3, 4], 'скрытый столбец C пропущен');
    assert.equal(sheet.colWidths[0], Math.round(12.5 * CHAR_PX));
    assert.equal(sheet.colWidths[3], Math.round(20 * CHAR_PX));
    assert.equal(sheet.rowHeights[0], 60, '45 пунктов = 60 px');
    assert.equal(sheet.defaultRowHeight, Math.round(16 * 4 / 3));
    assert.ok(sheet.rows[0][1], 'пустая ячейка с заливкой видна');
    assert.equal(sheet.rows[0][1].look.bg, '#FFFF00');
    assert.equal(sheet.rows[3], undefined, 'пустая ячейка без оформления не создаёт строку');
    const number = sheet.rows[2][1];
    assert.equal(number.text, '16,3');
    assert.equal(number.look.align, 'center', 'своё выравнивание у числа сильнее «числа вправо»');
    assert.equal(sheet.rows[2][4].look.align, 'center', 'логическое без выравнивания — по центру, как в Excel');
    assert.equal(sheet.rows[0][0].look.vAlign, 'middle');
    assert.equal(sheet.rows[2][3].look.vAlign, 'bottom', 'по умолчанию в Excel — по нижнему краю');
    assert.equal(sheetToTsv(sheet), 'Смена\nАлуа\t16,3\tTRUE\tИСТИНА', 'копия — без скрытых строки и столбца');
});

test('лист: рамка на стыке рисуется один раз, побеждает более заметная', () => {
    const book = readBookStyles(parseXml(STYLES_XML), DEFAULT_THEME);
    const xml = `<worksheet><sheetData>
        <row r="1">${cellXml('A1', '1', 1)}${cellXml('B1', '2', 3)}${cellXml('C1', '3', 1)}</row>
        <row r="2">${cellXml('A2', '4', 1)}${cellXml('B2', '5', 0)}</row>
      </sheetData></worksheet>`;
    const sheet = readSheet(xml, { styles: book });
    const edge = (row, col, side) => sheet.borders.get(`${row}:${col}`)?.[side]?.css;
    assert.equal(edge(0, 0, 'left'), '1px solid #000000', 'у первого столбца левая линия — своя');
    assert.equal(edge(0, 0, 'top'), '1px solid #000000');
    assert.equal(edge(0, 1, 'right'), '3px solid #000000', 'толстая правая B1 сильнее тонкой левой C1');
    assert.equal(edge(0, 1, 'bottom'), '2px dashed #FF0000');
    assert.equal(edge(0, 0, 'bottom'), '1px solid #000000', 'верх A2 стал низом A1');
    assert.equal(edge(1, 0, 'top'), undefined);
});

test('условное форматирование: цветовая шкала с процентилем и обратная шкала', () => {
    const book = readBookStyles(parseXml(STYLES_XML), DEFAULT_THEME);
    const values = [10, 20, 30, 40, 50];
    const rows = values.map((value, index) => `<row r="${index + 3}">${cellXml(`H${index + 3}`, value)}${cellXml(`J${index + 3}`, value)}</row>`).join('');
    const xml = `<worksheet><sheetData>${rows}<row r="8">${cellXml('H8', '#DIV/0!', 0, 'e')}</row></sheetData>
      <conditionalFormatting sqref="H3:H8"><cfRule type="colorScale" priority="2"><colorScale>
        <cfvo type="min"/><cfvo type="percentile" val="50"/><cfvo type="max"/>
        <color rgb="FFF8696B"/><color rgb="FFFFEB84"/><color rgb="FF63BE7B"/></colorScale></cfRule></conditionalFormatting>
      <conditionalFormatting sqref="J3:J7"><cfRule type="colorScale" priority="1"><colorScale>
        <cfvo type="min"/><cfvo type="max"/><color rgb="FF63BE7B"/><color rgb="FFF8696B"/></colorScale></cfRule></conditionalFormatting>
    </worksheet>`;
    const sheet = readSheet(xml, { styles: book });
    const bg = (row, col) => sheet.rows[row][col].look.bg;
    assert.equal(bg(2, 7), '#F8696B', 'минимум — красный');
    assert.equal(bg(4, 7), '#FFEB84', 'медиана — жёлтый');
    assert.equal(bg(6, 7), '#63BE7B', 'максимум — зелёный');
    assert.ok(channelsClose(bg(3, 7), '#FCAA78', 2), `между красным и жёлтым: ${bg(3, 7)}`);
    assert.equal(sheet.rows[7][7].look.bg, null, 'ошибка шкалой не красится');
    assert.equal(bg(2, 9), '#63BE7B', 'обратная шкала: меньше — лучше');
    assert.equal(bg(6, 9), '#F8696B');
});

test('условное форматирование: процентиль — по рангу, а не по размаху; старшее правило побеждает в каждом свойстве', () => {
    const book = readBookStyles(parseXml(STYLES_XML), DEFAULT_THEME);
    // 1, 2, 3, 4, 100: 50-й процентиль — 3 (жёлтый), а «50 % размаха» было бы 50,5.
    const values = [1, 2, 3, 4, 100];
    const rows = values.map((value, index) => `<row r="${index + 1}">${cellXml(`A${index + 1}`, value)}${cellXml(`B${index + 1}`, value)}</row>`).join('');
    const xml = `<worksheet><sheetData>${rows}</sheetData>
      <conditionalFormatting sqref="A1:A5"><cfRule type="colorScale" priority="9"><colorScale>
        <cfvo type="min"/><cfvo type="percentile" val="50"/><cfvo type="max"/>
        <color rgb="FFF8696B"/><color rgb="FFFFEB84"/><color rgb="FF63BE7B"/></colorScale></cfRule></conditionalFormatting>
      <conditionalFormatting sqref="B1:B5"><cfRule type="cellIs" dxfId="0" priority="1" operator="greaterThan"><formula>2</formula></cfRule>
        <cfRule type="cellIs" dxfId="2" priority="2" operator="greaterThan"><formula>0</formula></cfRule>
        <cfRule type="cellIs" dxfId="1" priority="3" operator="greaterThan"><formula>50</formula></cfRule></conditionalFormatting>
    </worksheet>`;
    const sheet = readSheet(xml, { styles: book });
    assert.equal(sheet.rows[2][0].look.bg, '#FFEB84', 'значение 3 — медиана');
    const b = (row) => sheet.rows[row][1].look;
    assert.equal(b(0).bg, '#C6EFCE', '1: только младшее правило — его заливка');
    assert.equal(b(3).bg, '#FFC7CE', '4: оба правила, заливка старшего');
    assert.equal(b(3).color, '#9C0006');
    assert.deepEqual([b(4).bg, b(4).color, b(4).bold], ['#FFC7CE', '#9C0006', true],
        '100: заливка и цвет — от старшего, жирный — от третьего (старшие его не задают)');
});

test('условное форматирование: «значение ячейки», формулы со ссылками, ошибки, первые N, гистограмма, stopIfTrue', () => {
    const book = readBookStyles(parseXml(STYLES_XML), DEFAULT_THEME);
    const xml = `<worksheet><sheetData>
        <row r="3">${cellXml('B3', '-0.25')}${cellXml('C3', '150')}${cellXml('D3', '#DIV/0!', 0, 'e')}${cellXml('E3', '5')}</row>
        <row r="4">${cellXml('B4', '0.33')}${cellXml('C4', '90')}${cellXml('D4', '7')}${cellXml('E4', '9')}</row>
        <row r="5">${cellXml('B5', '0.05')}${cellXml('C5', '120')}${cellXml('D5', '8')}${cellXml('E5', '1')}</row>
        <row r="20">${cellXml('D20', '120')}</row>
      </sheetData>
      <conditionalFormatting sqref="B3:B5"><cfRule type="cellIs" dxfId="0" priority="1" operator="lessThan"><formula>0</formula></cfRule></conditionalFormatting>
      <conditionalFormatting sqref="C3:C5"><cfRule type="cellIs" dxfId="2" priority="2" stopIfTrue="1" operator="greaterThan"><formula>$D$20</formula></cfRule>
        <cfRule type="expression" dxfId="0" priority="3"><formula>$C3&lt;100</formula></cfRule>
        <cfRule type="cellIs" dxfId="1" priority="4" operator="between"><formula>100</formula><formula>200</formula></cfRule></conditionalFormatting>
      <conditionalFormatting sqref="D3:D5"><cfRule type="containsErrors" dxfId="1" priority="5"><formula>ISERROR(D3)</formula></cfRule></conditionalFormatting>
      <conditionalFormatting sqref="E3:E5"><cfRule type="top10" dxfId="2" priority="6" rank="1"/><cfRule type="dataBar" priority="7"><dataBar><cfvo type="min"/><cfvo type="max"/><color rgb="FF638EC6"/></dataBar></cfRule></conditionalFormatting>
      <conditionalFormatting sqref="B3:B5"><cfRule type="expression" dxfId="2" priority="8"><formula>Лист2!A1&gt;0</formula></cfRule></conditionalFormatting>
    </worksheet>`;
    const sheet = readSheet(xml, { styles: book });
    const look = (row, col) => sheet.rows[row][col].look;
    assert.deepEqual([look(2, 1).bg, look(2, 1).color], ['#FFC7CE', '#9C0006'], 'отрицательное — красным');
    assert.equal(look(3, 1).bg, null);
    assert.equal(look(2, 2).bg, '#C6EFCE', '150 > $D$20 (120)');
    assert.equal(look(2, 2).bold, false, 'stopIfTrue: младшее правило (between → жирный) не применяется');
    assert.equal(look(3, 2).bg, '#FFC7CE', '$C4<100 — относительная строка правила');
    assert.equal(look(4, 2).bold, true, '120 — между 100 и 200, правило 4');
    assert.equal(look(4, 2).bg, null);
    assert.equal(look(2, 3).color, '#FFFFFF', 'ошибка «спрятана» белым — как в файле');
    assert.equal(look(3, 3).color, '#000000', 'без ошибки — свой цвет шрифта из стиля (theme 1, чёрный)');
    assert.equal(look(3, 4).bg, '#C6EFCE', 'первый по величине');
    assert.equal(look(2, 4).bar.share, 0.5);
    assert.equal(look(4, 4).bar.share, 0);
    assert.equal(look(3, 4).bar.color, '#638EC6');
});

test('формулы правил: ссылки со сдвигом, функции, сравнение по правилам Excel, неизвестное — отказ', () => {
    const grid = { '0:0': 5, '0:1': 'Да', '1:0': 15, '1:1': null, '2:0': { error: '#N/A' } };
    const value = (row, col) => (`${row}:${col}` in grid ? grid[`${row}:${col}`] : null);
    const run = (formula, rowOffset = 0, colOffset = 0) => compileFormula(formula)({ value, rowOffset, colOffset });
    assert.equal(run('A1*2+1'), 11);
    assert.equal(run('A1>10', 1), true, 'относительная строка сдвинута на 1');
    assert.equal(run('$A$1>10', 1), false, 'абсолютная — нет');
    assert.equal(run('AND(A1>1,B1="да")'), true, 'текст без учёта регистра');
    assert.equal(run('OR(ISBLANK(B2),A1=0)'), true);
    assert.equal(run('AVERAGE($A$1:$A$2)'), 10);
    assert.equal(run('IF(A1>3,"много","мало")'), 'много');
    assert.equal(run('ISERROR(A3)'), true);
    assert.deepEqual(run('A3+1'), { error: '#N/A' }, 'ошибка проходит насквозь');
    assert.equal(run('-A1^2'), 25, 'унарный минус старше степени, как в Excel');
    assert.equal(run('10%'), 0.1);
    assert.equal(compareValues(5, 'a') < 0, true, 'число меньше текста');
    assert.equal(compareValues(null, 0), 0, 'пусто = 0');
    assert.throws(() => compileFormula('Лист2!A1>0'), UnsupportedFormula);
    assert.throws(() => compileFormula('VLOOKUP(A1,B:C,2,0)'), UnsupportedFormula);
});
