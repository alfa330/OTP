/* Книга Excel (.xlsx) → листы для просмотра — с видом, как в Excel.

   Лист читается сканером строк по тексту XML, а не деревом: на листе бывают
   десятки тысяч строк, а показываем мы первые несколько тысяч — дальше
   сканер просто останавливается. Значения — уже строками для экрана
   (xlsxFormat). Вид (снимок владельца 13.11.2026: «проверь, что не так… нужно
   передавать цвета»): скрытые строки и столбцы не показываются, ширины
   столбцов и высоты строк — из файла, заливки, шрифт, рамки и выравнивание —
   из стилей ячеек (xlsxStyle), цветовые шкалы и прочее условное
   форматирование считаются по значениям (xlsxConditional). */
import { attr, decodeEntities, findAll, findFirst, relId } from './xmlTree.js';
import { mainPartPath, openPackage, PreviewError, readRels, readText, readXml, relOfType } from './ooxml.js';
import { columnIndex, formatCell } from './xlsxFormat.js';
import { DEFAULT_THEME, readBookStyles, readThemeColors, strongerBorder } from './xlsxStyle.js';
import { applyConditionalRules, readConditionalRules } from './xlsxConditional.js';

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

const ROW = /<(?:\w+:)?row\b([^>]*?)(?:\/>|>([\s\S]*?)<\/(?:\w+:)?row>)/g;
const CELL = /<(?:\w+:)?c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/(?:\w+:)?c>)/g;
const VALUE = /<(?:\w+:)?v(?:\s[^>]*)?>([\s\S]*?)<\/(?:\w+:)?v>/;
const INLINE = /<(?:\w+:)?is>([\s\S]*?)<\/(?:\w+:)?is>/;
const COL = /<(?:\w+:)?col\b([^>]*?)\/?>/g;
const attrOf = (raw, name) => {
    const match = new RegExp(`(?:^|\\s)${name}\\s*=\\s*"([^"]*)"`).exec(raw || '');
    return match ? decodeEntities(match[1]) : undefined;
};
const isOn = (value) => ['1', 'true'].includes(String(value));

/* Ширина столбца Excel хранится в «символах»: 1 символ ≈ 7 px шрифта Calibri 11.
   Наш шрифт чуть шире — берём 7,5 px, чтобы текст, умещавшийся в Excel, умещался и здесь. */
export const CHAR_PX = 7.5;
const POINT_PX = 4 / 3;

const NO_STYLE = { code: 'General', font: {}, fill: null, border: null, align: null, vAlign: null, wrap: false, indent: 0, bold: false };

function bookOf(styles) {
    if (Array.isArray(styles)) return { xfs: styles, dxfs: [], palette: undefined, baseSize: 11 };
    return styles || { xfs: [], dxfs: [], palette: undefined, baseSize: 11 };
}

/** Вид ячейки: стиль + условное форматирование. */
function cellLook(xf, cf = {}, cell, baseSize) {
    const font = xf.font || {};
    const size = font.size && baseSize && font.size !== baseSize ? Math.round((font.size / baseSize) * 100) / 100 : null;
    const typed = cell?.err || typeof cell?.v === 'boolean';
    return {
        bg: cf.fill || xf.fill || null,
        color: cf.color || font.color || null,
        bold: cf.bold ?? Boolean(font.bold),
        italic: cf.italic ?? Boolean(font.italic),
        underline: cf.underline ?? Boolean(font.underline),
        strike: cf.strike ?? Boolean(font.strike),
        size,
        // Без своего выравнивания Excel ставит числа вправо, логические и ошибки — по центру.
        align: xf.align || (cell?.numeric ? 'right' : typed ? 'center' : null),
        vAlign: xf.vAlign || 'bottom',
        wrap: Boolean(xf.wrap),
        indent: xf.indent || 0,
        bar: cf.bar || null,
    };
}

export function readSheet(xml, { shared = [], styles = [], date1904 = false, maxRows = MAX_ROWS, maxCols = MAX_COLS } = {}) {
    const book = bookOf(styles);
    const xfs = book.xfs || [];
    const rows = [];
    const hiddenRows = [];
    const rowHeights = [];
    let rowCount = 0;
    let colCount = 0;
    let truncatedRows = false;
    let truncatedCols = false;

    const format = /<(?:\w+:)?sheetFormatPr\b([^>]*)>/.exec(xml)?.[1] || '';
    const defaultRowHeight = Math.round((Number(attrOf(format, 'defaultRowHeight')) || 15) * POINT_PX);
    const defaultColChars = Number(attrOf(format, 'defaultColWidth'));
    const defaultColWidth = Math.round(Number.isFinite(defaultColChars) && defaultColChars > 0
        ? defaultColChars * CHAR_PX
        : (((Number(attrOf(format, 'baseColWidth')) || 8) * 7 + 5) * CHAR_PX) / 7);
    const view = /<(?:\w+:)?sheetView\b([^>]*)>/.exec(xml)?.[1] || '';
    const gridLines = !['0', 'false'].includes(String(attrOf(view, 'showGridLines') ?? '1'));

    const colWidths = [];
    const hiddenCols = [];
    const colsBlock = /<(?:\w+:)?cols>([\s\S]*?)<\/(?:\w+:)?cols>/.exec(xml)?.[1] || '';
    const colPattern = new RegExp(COL.source, 'g');
    let colMatch;
    while ((colMatch = colPattern.exec(colsBlock))) {
        const min = Number(attrOf(colMatch[1], 'min')) - 1;
        const max = Math.min(Number(attrOf(colMatch[1], 'max')) - 1, maxCols - 1);
        if (!Number.isFinite(min) || !Number.isFinite(max)) continue;
        const width = Number(attrOf(colMatch[1], 'width'));
        const hidden = isOn(attrOf(colMatch[1], 'hidden')) || width === 0;
        for (let col = Math.max(min, 0); col <= max; col += 1) {
            if (Number.isFinite(width) && width > 0) colWidths[col] = Math.max(16, Math.round(width * CHAR_PX));
            if (hidden) hiddenCols[col] = true;
        }
    }

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
        if (isOn(attrOf(rowMatch[1], 'hidden'))) hiddenRows[rowIndex] = true;
        const height = Number(attrOf(rowMatch[1], 'ht'));
        if (Number.isFinite(height) && height > 0) rowHeights[rowIndex] = Math.round(height * POINT_PX);
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
            if (colIndex >= maxCols) {
                if (raw !== '') truncatedCols = true;
                continue;
            }
            const styleIndex = Number(attrOf(cellMatch[1], 's') || 0);
            const style = xfs[styleIndex] || NO_STYLE;
            const text = raw === '' ? '' : formatCell(raw, type, style.code, date1904);
            // Пустая ячейка видна, только если у неё есть заливка или рамка — как в Excel.
            if (text === '' && !style.fill && !style.border) continue;
            const numeric = type === 'n' && raw !== '' && Number.isFinite(Number(raw));
            const cell = {
                text,
                numeric,
                bold: Boolean(style.bold),
                align: style.align || (numeric ? 'right' : null),
                s: styleIndex,
            };
            if (type === 'e') cell.err = raw;
            else if (numeric) cell.v = Number(raw);
            else if (type === 'b') cell.v = raw === '1' || raw === 'true';
            else if (text !== '') cell.v = text;
            cells[colIndex] = cell;
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

    // Условное форматирование — по значениям ячеек этого листа.
    const cellAt = (row, col) => rows[row]?.[col];
    const conditional = applyConditionalRules(readConditionalRules(xml, book), { rowCount, colCount, cellAt });

    const visibleRows = [];
    for (let row = 0; row < rowCount; row += 1) if (!hiddenRows[row]) visibleRows.push(row);
    const visibleCols = [];
    for (let col = 0; col < colCount; col += 1) if (!hiddenCols[col]) visibleCols.push(col);

    // Вид ячеек и рамки. Линия на стыке рисуется один раз — правой/нижней
    // границей левой/верхней ячейки, более заметная из двух, как в Excel.
    const borders = new Map();
    const borderAt = (row, col) => {
        const key = `${row}:${col}`;
        if (!borders.has(key)) borders.set(key, {});
        return borders.get(key);
    };
    const prevRow = new Map(visibleRows.map((row, index) => [row, visibleRows[index - 1]]));
    const prevCol = new Map(visibleCols.map((col, index) => [col, visibleCols[index - 1]]));
    rows.forEach((cells, row) => {
        if (!cells || hiddenRows[row]) return;
        cells.forEach((cell, col) => {
            if (!cell) return;
            const xf = xfs[cell.s] || NO_STYLE;
            const cf = conditional.get(`${row}:${col}`) || {};
            cell.look = cellLook(xf, cf, cell, book.baseSize);
            if (cf.bold !== undefined) cell.bold = cf.bold;
            if (hiddenCols[col] || !xf.border) return;
            const { left, right, top, bottom } = xf.border;
            if (right) borderAt(row, col).right = strongerBorder(borderAt(row, col).right, right);
            if (bottom) borderAt(row, col).bottom = strongerBorder(borderAt(row, col).bottom, bottom);
            if (left) {
                const before = prevCol.get(col);
                if (before === undefined) borderAt(row, col).left = strongerBorder(borderAt(row, col).left, left);
                else borderAt(row, before).right = strongerBorder(borderAt(row, before).right, left);
            }
            if (top) {
                const above = prevRow.get(row);
                if (above === undefined) borderAt(row, col).top = strongerBorder(borderAt(row, col).top, top);
                else borderAt(above, col).bottom = strongerBorder(borderAt(above, col).bottom, top);
            }
        });
    });

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
        styled: true,
        hiddenRows,
        hiddenCols,
        visibleRows,
        visibleCols,
        colWidths,
        rowHeights,
        defaultColWidth,
        defaultRowHeight,
        gridLines,
        borders,
    };
}

/** Лист в текст для буфера обмена: строки через перевод строки, ячейки через табуляцию — вставляется в Excel и Google Таблицы ячейками. */
export function sheetToTsv(sheet) {
    // Скрытые строки и столбцы не копируются — как копирование отфильтрованной таблицы в Excel.
    const lines = [];
    for (let row = 0; row < sheet.rowCount; row += 1) {
        if (sheet.hiddenRows?.[row]) continue;
        const cells = sheet.rows[row] || [];
        const values = [];
        for (let col = 0; col < sheet.colCount; col += 1) {
            if (sheet.hiddenCols?.[col]) continue;
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
    const themeRel = relOfType(rels, '/theme');
    const [sharedXml, stylesXml, themeXml] = await Promise.all([
        readText(zip, sharedRel ? sharedRel.target : 'xl/sharedStrings.xml'),
        readXml(zip, stylesRel ? stylesRel.target : 'xl/styles.xml'),
        readXml(zip, themeRel ? themeRel.target : 'xl/theme/theme1.xml'),
    ]);
    const shared = readSharedStrings(sharedXml);
    const styles = readBookStyles(stylesXml, themeXml ? readThemeColors(themeXml) : DEFAULT_THEME);
    // Книги с Mac старых версий считают дни от 1904 года.
    const date1904Flag = attr(findFirst(workbook, 'workbookPr'), 'date1904');
    const date1904 = date1904Flag !== undefined && !['0', 'false'].includes(String(date1904Flag).toLowerCase());
    const sheets = [];
    // Книга открывается на том листе, на котором её сохранили, — как в Excel.
    const activeTab = Number(attr(findFirst(workbook, 'workbookView'), 'activeTab')) || 0;
    let active = 0;
    for (const [position, node] of findAll(findFirst(workbook, 'sheets'), 'sheet').entries()) {
        const state = attr(node, 'state');
        if (state === 'hidden' || state === 'veryHidden') continue;
        if (position === activeTab) active = sheets.length;
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
    return { sheets, active };
}
