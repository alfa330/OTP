/* Книга Excel (.xlsx) → листы для просмотра.

   Лист читается сканером строк по тексту XML, а не деревом: на листе бывают
   десятки тысяч строк, а показываем мы первые несколько тысяч — дальше
   сканер просто останавливается. Значения — уже строками для экрана
   (xlsxFormat), числа помечены, чтобы стоять по правому краю, как в Excel. */
import { attr, child, childrenOf, decodeEntities, findAll, findFirst, onOff, relId } from './xmlTree.js';
import { mainPartPath, openPackage, PreviewError, readRels, readText, readXml, relOfType } from './ooxml.js';
import { BUILTIN_FORMATS, columnIndex, formatCell } from './xlsxFormat.js';

export { columnName } from './xlsxFormat.js';

export const MAX_ROWS = 5000;
export const MAX_COLS = 200;

const parseRef = (ref) => {
    const match = /^\$?([A-Z]{1,3})\$?(\d+)$/.exec(String(ref || '').toUpperCase());
    return match ? { col: columnIndex(match[1]), row: Number(match[2]) - 1 } : null;
};

/* Текст общих строк и строк в ячейке: все <t>, кроме фонетики (rPh). */
const TEXT_PIECE = /<(?:\w+:)?t(?:\s[^>]*)?>([\s\S]*?)<\/(?:\w+:)?t>|<(?:\w+:)?t\s*\/>/g;
const stripPhonetic = (xml) => xml.replace(/<(?:\w+:)?rPh\b[\s\S]*?<\/(?:\w+:)?rPh>/g, '');
const richText = (xml) => {
    let out = '';
    const pattern = new RegExp(TEXT_PIECE.source, 'g');
    let match;
    const source = stripPhonetic(xml);
    while ((match = pattern.exec(source))) out += decodeEntities(match[1] || '');
    return out;
};

export function readSharedStrings(xml) {
    const out = [];
    if (!xml) return out;
    const pattern = /<(?:\w+:)?si>([\s\S]*?)<\/(?:\w+:)?si>|<(?:\w+:)?si\s*\/>/g;
    let match;
    while ((match = pattern.exec(xml))) out.push(match[1] ? richText(match[1]) : '');
    return out;
}

function readStyles(xml) {
    if (!xml) return [];
    const formats = new Map();
    findAll(xml, 'numFmt').forEach((fmt) => formats.set(Number(attr(fmt, 'numFmtId')), attr(fmt, 'formatCode') || ''));
    const fonts = childrenOf(findFirst(xml, 'fonts'), 'font').map((font) => onOff(child(font, 'b')));
    return childrenOf(findFirst(xml, 'cellXfs'), 'xf').map((xf) => {
        const id = Number(attr(xf, 'numFmtId') || 0);
        const horizontal = attr(child(xf, 'alignment'), 'horizontal');
        return {
            code: formats.get(id) ?? BUILTIN_FORMATS[id] ?? 'General',
            bold: Boolean(fonts[Number(attr(xf, 'fontId') || 0)]),
            align: ['center', 'right', 'left', 'centerContinuous'].includes(horizontal) ? horizontal.replace('Continuous', '') : null,
        };
    });
}

const ROW = /<(?:\w+:)?row\b([^>]*?)(?:\/>|>([\s\S]*?)<\/(?:\w+:)?row>)/g;
const CELL = /<(?:\w+:)?c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/(?:\w+:)?c>)/g;
const VALUE = /<(?:\w+:)?v(?:\s[^>]*)?>([\s\S]*?)<\/(?:\w+:)?v>/;
const INLINE = /<(?:\w+:)?is>([\s\S]*?)<\/(?:\w+:)?is>/;
const attrOf = (raw, name) => {
    const match = new RegExp(`(?:^|\\s)${name}\\s*=\\s*"([^"]*)"`).exec(raw || '');
    return match ? decodeEntities(match[1]) : undefined;
};

export function readSheet(xml, { shared = [], styles = [], date1904 = false, maxRows = MAX_ROWS, maxCols = MAX_COLS } = {}) {
    const rows = [];
    let rowCount = 0;
    let colCount = 0;
    let truncatedRows = false;
    let truncatedCols = false;
    const rowPattern = new RegExp(ROW.source, 'g');
    let rowMatch;
    let nextRow = 0;
    while ((rowMatch = rowPattern.exec(xml))) {
        const declared = Number(attrOf(rowMatch[1], 'r'));
        const rowIndex = Number.isFinite(declared) && declared > 0 ? declared - 1 : nextRow;
        nextRow = rowIndex + 1;
        if (rowIndex >= maxRows) {
            truncatedRows = true;
            break;
        }
        if (!rowMatch[2]) continue;
        const cellPattern = new RegExp(CELL.source, 'g');
        let cellMatch;
        let nextCol = 0;
        const cells = [];
        while ((cellMatch = cellPattern.exec(rowMatch[2]))) {
            const ref = parseRef(attrOf(cellMatch[1], 'r'));
            const colIndex = ref ? ref.col : nextCol;
            nextCol = colIndex + 1;
            const body = cellMatch[2] || '';
            const type = attrOf(cellMatch[1], 't') || 'n';
            let raw;
            if (type === 'inlineStr') {
                const inline = INLINE.exec(body);
                raw = inline ? richText(inline[1]) : '';
            } else {
                const value = VALUE.exec(body);
                raw = value ? decodeEntities(value[1]) : '';
                if (type === 's') raw = shared[Number(raw)] ?? '';
            }
            if (raw === '') continue;
            if (colIndex >= maxCols) {
                truncatedCols = true;
                continue;
            }
            const style = styles[Number(attrOf(cellMatch[1], 's') || 0)] || {};
            const text = formatCell(raw, type, style.code, date1904);
            if (text === '') continue;
            const numeric = type === 'n' && Number.isFinite(Number(raw));
            cells[colIndex] = {
                text,
                numeric,
                bold: Boolean(style.bold),
                align: style.align || (numeric ? 'right' : null),
            };
            colCount = Math.max(colCount, colIndex + 1);
        }
        if (cells.length) {
            rows[rowIndex] = cells;
            rowCount = Math.max(rowCount, rowIndex + 1);
        }
    }
    const merges = [];
    const mergePattern = /<(?:\w+:)?mergeCell\b[^>]*?\bref="([^"]+)"/g;
    let mergeMatch;
    while ((mergeMatch = mergePattern.exec(xml))) {
        const [from, to] = mergeMatch[1].split(':').map(parseRef);
        if (!from || !to || from.row >= rowCount || from.col >= colCount) continue;
        merges.push({ row: from.row, col: from.col, rowSpan: Math.min(to.row, rowCount - 1) - from.row + 1, colSpan: Math.min(to.col, colCount - 1) - from.col + 1 });
    }
    // Полный размер листа для подписи «показаны первые N строк из M».
    const dimension = /<(?:\w+:)?dimension\b[^>]*?\bref="([^"]+)"/.exec(xml);
    const end = dimension ? parseRef(dimension[1].split(':').pop()) : null;
    return {
        rows,
        rowCount,
        colCount,
        merges,
        truncatedRows,
        truncatedCols,
        totalRows: end ? Math.max(end.row + 1, rowCount) : rowCount,
    };
}

/** Лист в текст для буфера обмена: строки через перевод строки, ячейки через табуляцию — вставляется в Excel и Google Таблицы ячейками. */
export function sheetToTsv(sheet) {
    const lines = [];
    for (let row = 0; row < sheet.rowCount; row += 1) {
        const cells = sheet.rows[row] || [];
        const values = [];
        for (let col = 0; col < sheet.colCount; col += 1) {
            values.push(String(cells[col]?.text ?? '').replace(/[\t\r\n]+/g, ' '));
        }
        lines.push(values.join('\t').replace(/\t+$/, ''));
    }
    return lines.join('\n').replace(/\n+$/, '');
}

export async function parseXlsx(bytes, options = {}) {
    const zip = await openPackage(bytes);
    const workbookPath = await mainPartPath(zip, 'xl/workbook.xml');
    const workbook = await readXml(zip, workbookPath);
    if (!findFirst(workbook, 'sheets')) throw new PreviewError('В файле нет листов Excel.');
    const rels = await readRels(zip, workbookPath);
    const sharedRel = relOfType(rels, '/sharedStrings');
    const stylesRel = relOfType(rels, '/styles');
    const [sharedXml, stylesXml] = await Promise.all([
        readText(zip, sharedRel ? sharedRel.target : 'xl/sharedStrings.xml'),
        readXml(zip, stylesRel ? stylesRel.target : 'xl/styles.xml'),
    ]);
    const shared = readSharedStrings(sharedXml);
    const styles = readStyles(stylesXml);
    // Книги с Mac старых версий считают дни от 1904 года.
    const date1904Flag = attr(findFirst(workbook, 'workbookPr'), 'date1904');
    const date1904 = date1904Flag !== undefined && !['0', 'false'].includes(String(date1904Flag).toLowerCase());
    const sheets = [];
    for (const node of findAll(findFirst(workbook, 'sheets'), 'sheet')) {
        const state = attr(node, 'state');
        if (state === 'hidden' || state === 'veryHidden') continue;
        const rel = rels.get(relId(node));
        const name = attr(node, 'name') || `Лист ${sheets.length + 1}`;
        if (!rel || !/\/worksheet$/.test(rel.type)) {
            sheets.push({ name, rows: [], rowCount: 0, colCount: 0, merges: [], note: 'На этом листе диаграмма — её видно в Excel.' });
            continue;
        }
        // eslint-disable-next-line no-await-in-loop
        const xml = await readText(zip, rel.target);
        sheets.push({ name, ...readSheet(xml || '', { shared, styles, date1904, ...options }) });
    }
    if (!sheets.length) throw new PreviewError('В книге нет видимых листов.');
    return { sheets };
}
