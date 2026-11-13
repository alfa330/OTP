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
import { parseXlsx, readSheet, sheetToTsv } from '../src/components/file_preview/xlsxParse.js';
import { formatListNumber, parseDocx } from '../src/components/file_preview/docxParse.js';
import { parsePptx } from '../src/components/file_preview/pptxParse.js';
import { createPreviewCache } from '../src/components/file_preview/previewCache.js';
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

