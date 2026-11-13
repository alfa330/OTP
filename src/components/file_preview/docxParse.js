/* Документ Word (.docx) → блоки для чтения в окне просмотра.

   Не вёрстка страницы Word, а то, что нужно прочесть документ: абзацы,
   заголовки, списки с той же нумерацией, таблицы с объединёнными ячейками,
   жирный/курсив/подчёркивание, ссылки и картинки. Разметку отдаём данными, а не
   HTML: окно строит из них React-элементы, и чужой файл не может подсунуть
   скрипт или внешнюю ссылку с javascript:.

   Блок абзаца: { type: 'p', heading (0 — «Название», 1–6 — заголовки), align, list: { level, label, ordered } | null,
                  runs: [{ text, bold, italic, underline, strike, vert, href } |
                         { image: путь } | { placeholder: 'Диаграмма' }] }
   Блок таблицы: { type: 'table', rows: [{ header, cells: [{ blocks, colSpan, rowSpan }] }] } */
import { attr, child, childrenOf, elements, findAll, findFirst, onOff, relId, textContent } from './xmlTree.js';
import { loadImages, mainPartPath, openPackage, PreviewError, readRels, readXml, relOfType } from './ooxml.js';

const ALIGN = { center: 'center', right: 'right', end: 'right', both: 'justify', distribute: 'justify' };
const ORDERED_FORMATS = new Set(['decimal', 'decimalZero', 'lowerLetter', 'upperLetter', 'lowerRoman', 'upperRoman',
    'russianLower', 'russianUpper', 'decimalEnclosedCircle', 'ordinal', 'cardinalText', 'ordinalText']);
const BULLETS = ['•', '◦', '▪'];

/* ── Стили: какой стиль абзаца — заголовок и какой у него список ── */
function readStyles(xml) {
    const raw = new Map();
    findAll(xml, 'style').forEach((style) => {
        if (attr(style, 'type') && attr(style, 'type') !== 'paragraph') return;
        const id = attr(style, 'styleId');
        if (!id) return;
        const pPr = child(style, 'pPr');
        const numPr = child(pPr, 'numPr');
        raw.set(id, {
            name: String(attr(child(style, 'name'), 'val') || '').toLowerCase(),
            basedOn: attr(child(style, 'basedOn'), 'val') || null,
            outline: attr(child(pPr, 'outlineLvl'), 'val'),
            numId: attr(child(numPr, 'numId'), 'val'),
            ilvl: attr(child(numPr, 'ilvl'), 'val'),
        });
    });
    const resolved = new Map();
    const resolve = (id, depth = 0) => {
        if (resolved.has(id)) return resolved.get(id);
        const style = raw.get(id);
        if (!style || depth > 12) return { heading: null, numId: null, ilvl: null };
        const parent = style.basedOn ? resolve(style.basedOn, depth + 1) : { heading: null, numId: null, ilvl: null };
        let heading = null;
        const named = /^(?:heading|заголовок)\s*(\d)$/.exec(style.name) || /^(?:heading|заголовок)\s*(\d)$/i.exec(id);
        if (named) heading = Number(named[1]);
        // «Название» документа — крупнее заголовка 1: уровень 0.
        else if (style.name === 'title' || /^title$/i.test(id)) heading = 0;
        else if (style.outline !== undefined && Number(style.outline) < 6) heading = Number(style.outline) + 1;
        else heading = parent.heading;
        const out = {
            heading,
            numId: style.numId !== undefined ? style.numId : parent.numId,
            ilvl: style.ilvl !== undefined ? style.ilvl : parent.ilvl,
        };
        resolved.set(id, out);
        return out;
    };
    const lookup = new Map();
    raw.forEach((_, id) => lookup.set(id, resolve(id)));
    return lookup;
}

/* ── Нумерация: формат и шаблон каждого уровня каждого списка ── */
function readNumbering(xml) {
    const abstract = new Map();
    findAll(xml, 'abstractNum').forEach((node) => {
        const levels = new Map();
        childrenOf(node, 'lvl').forEach((lvl) => {
            levels.set(Number(attr(lvl, 'ilvl') || 0), {
                format: attr(child(lvl, 'numFmt'), 'val') || 'decimal',
                text: attr(child(lvl, 'lvlText'), 'val') ?? '%1.',
                start: Number(attr(child(lvl, 'start'), 'val') || 1),
            });
        });
        abstract.set(attr(node, 'abstractNumId'), levels);
    });
    const nums = new Map();
    findAll(xml, 'num').forEach((node) => {
        const abstractId = attr(child(node, 'abstractNumId'), 'val');
        const overrides = childrenOf(node, 'lvlOverride');
        nums.set(attr(node, 'numId'), {
            levels: abstract.get(abstractId) || new Map(),
            // Свой отсчёт у списка с переопределённым началом; иначе списки одного
            // шаблона продолжают общий счёт — так нумерует и Word.
            counterKey: overrides.some((item) => child(item, 'startOverride')) ? `num:${attr(node, 'numId')}` : `abs:${abstractId}`,
        });
    });
    return nums;
}

const ROMAN = [[1000, 'm'], [900, 'cm'], [500, 'd'], [400, 'cd'], [100, 'c'], [90, 'xc'], [50, 'l'], [40, 'xl'], [10, 'x'], [9, 'ix'], [5, 'v'], [4, 'iv'], [1, 'i']];
const toRoman = (value) => {
    let rest = value;
    let out = '';
    ROMAN.forEach(([size, glyph]) => {
        while (rest >= size) {
            out += glyph;
            rest -= size;
        }
    });
    return out;
};
const LATIN = 'abcdefghijklmnopqrstuvwxyz';
const CYRILLIC = 'абвгдежзиклмнопрстуфхцчшщэюя';
const letters = (value, alphabet) => {
    const index = (value - 1) % alphabet.length;
    return alphabet[index].repeat(Math.floor((value - 1) / alphabet.length) + 1);
};

export function formatListNumber(value, format) {
    switch (format) {
    case 'lowerLetter': return letters(value, LATIN);
    case 'upperLetter': return letters(value, LATIN).toUpperCase();
    case 'lowerRoman': return toRoman(value);
    case 'upperRoman': return toRoman(value).toUpperCase();
    case 'russianLower': return letters(value, CYRILLIC);
    case 'russianUpper': return letters(value, CYRILLIC).toUpperCase();
    case 'decimalZero': return value < 10 ? `0${value}` : String(value);
    default: return String(value);
    }
}

/* ── Абзацы и строки ── */
const safeHref = (target) => (/^(https?:|mailto:|tel:)/i.test(String(target || '').trim()) ? String(target).trim() : null);

function chooseAlternate(node) {
    return child(node, 'Choice') || child(node, 'Fallback');
}

function readRun(run, ctx, out, extra, href) {
    const rPr = child(run, 'rPr');
    if (onOff(child(rPr, 'vanish'))) return;
    const format = {
        bold: onOff(child(rPr, 'b')),
        italic: onOff(child(rPr, 'i')),
        underline: Boolean(child(rPr, 'u')) && String(attr(child(rPr, 'u'), 'val') || 'single') !== 'none',
        strike: onOff(child(rPr, 'strike')) || onOff(child(rPr, 'dstrike')),
        vert: ['superscript', 'subscript'].includes(attr(child(rPr, 'vertAlign'), 'val')) ? attr(child(rPr, 'vertAlign'), 'val') : null,
        href,
    };
    const push = (text) => {
        if (text) out.push({ text, ...format });
    };
    const visit = (node) => {
        elements(node).forEach((item) => {
            switch (item.local) {
            case 't': push(textContent(item)); break;
            case 'tab': case 'ptab': push('\t'); break;
            case 'br': case 'cr': push('\n'); break;
            case 'noBreakHyphen': push('-'); break;
            case 'AlternateContent': {
                const branch = chooseAlternate(item);
                if (branch) visit(branch);
                break;
            }
            case 'drawing': case 'pict': case 'object': {
                const blips = [...findAll(item, 'blip').map((blip) => attr(blip, 'embed')),
                    ...findAll(item, 'imagedata').map((data) => relId(data))].filter(Boolean);
                let shown = false;
                blips.forEach((id) => {
                    const rel = ctx.rels.get(id);
                    if (rel && !rel.external) {
                        ctx.images.add(rel.target);
                        out.push({ image: rel.target });
                        shown = true;
                    }
                });
                const graphic = findFirst(item, 'graphicData');
                if (!shown && /\/chart$/.test(String(attr(graphic, 'uri') || ''))) out.push({ placeholder: 'Диаграмма' });
                findAll(item, 'txbxContent').forEach((box) => extra.push(...readBlocks(box, ctx)));
                break;
            }
            default: break; // rPr, fldChar, instrText (код поля), delText (удалённое) и прочее
            }
        });
    };
    visit(run);
}

function readInline(node, ctx, out, extra, href = null) {
    elements(node).forEach((item) => {
        switch (item.local) {
        case 'r': readRun(item, ctx, out, extra, href); break;
        case 'hyperlink': {
            const rel = ctx.rels.get(relId(item));
            readInline(item, ctx, out, extra, rel && rel.external ? safeHref(rel.target) : href);
            break;
        }
        case 'ins': case 'moveTo': case 'smartTag': case 'customXml': case 'fldSimple': case 'bdo': case 'dir':
            readInline(item, ctx, out, extra, href);
            break;
        case 'sdt': readInline(child(item, 'sdtContent'), ctx, out, extra, href); break;
        case 'AlternateContent': {
            const branch = chooseAlternate(item);
            if (branch) readInline(branch, ctx, out, extra, href);
            break;
        }
        case 'oMath': case 'oMathPara': {
            const formula = findAll(item, 't').map(textContent).join('');
            if (formula) out.push({ text: formula, italic: true });
            break;
        }
        default: break; // pPr, bookmarkStart, proofErr, del, moveFrom
        }
    });
}

const sameFormat = (a, b) => a.bold === b.bold && a.italic === b.italic && a.underline === b.underline
    && a.strike === b.strike && a.vert === b.vert && a.href === b.href;

function mergeRuns(runs) {
    const out = [];
    runs.forEach((run) => {
        const last = out[out.length - 1];
        if (run.text !== undefined && last && last.text !== undefined && sameFormat(last, run)) last.text += run.text;
        else out.push({ ...run });
    });
    return out;
}

function listLabel(ctx, numId, level) {
    const num = ctx.numbering.get(numId);
    if (!num) return { label: BULLETS[level % BULLETS.length], ordered: false };
    const def = num.levels.get(level) || { format: 'bullet', text: '' };
    const counters = ctx.counters.get(num.counterKey) || [];
    for (let index = 0; index <= level; index += 1) {
        if (counters[index] === undefined) counters[index] = (num.levels.get(index)?.start ?? 1) - 1;
    }
    counters[level] += 1;
    counters.length = level + 1; // более глубокие уровни начинают заново
    ctx.counters.set(num.counterKey, counters);
    if (def.format === 'bullet' || def.format === 'none' || !ORDERED_FORMATS.has(def.format)) {
        return { label: def.format === 'none' ? '' : BULLETS[level % BULLETS.length], ordered: false };
    }
    const label = String(def.text).replace(/%(\d)/g, (_, digit) => {
        const at = Number(digit) - 1;
        const levelDef = num.levels.get(at) || def;
        return formatListNumber(counters[at] ?? 1, levelDef.format);
    });
    return { label, ordered: true };
}

function readParagraph(p, ctx) {
    const pPr = child(p, 'pPr');
    const style = ctx.styles.get(attr(child(pPr, 'pStyle'), 'val')) || {};
    let heading = Number.isInteger(style.heading) ? style.heading : null;
    const outline = attr(child(pPr, 'outlineLvl'), 'val');
    if (outline !== undefined) heading = Number(outline) < 6 ? Number(outline) + 1 : null;
    const numPr = child(pPr, 'numPr');
    const numId = attr(child(numPr, 'numId'), 'val') ?? style.numId;
    const level = Number(attr(child(numPr, 'ilvl'), 'val') ?? style.ilvl ?? 0) || 0;
    const list = numId && numId !== '0' ? { level, ...listLabel(ctx, numId, level) } : null;
    const runs = [];
    const extra = [];
    readInline(p, ctx, runs, extra);
    const block = {
        type: 'p',
        heading: heading === null ? null : Math.min(heading, 6),
        align: ALIGN[attr(child(pPr, 'jc'), 'val')] || null,
        list,
        runs: mergeRuns(runs),
    };
    return [block, ...extra];
}

function tableRows(tbl) {
    const rows = [];
    elements(tbl).forEach((item) => {
        if (item.local === 'tr') rows.push(item);
        else if (item.local === 'sdt') rows.push(...tableRows(child(item, 'sdtContent')));
        else if (item.local === 'customXml') rows.push(...tableRows(item));
    });
    return rows;
}

function rowCells(tr) {
    const cells = [];
    elements(tr).forEach((item) => {
        if (item.local === 'tc') cells.push(item);
        else if (item.local === 'sdt') cells.push(...rowCells(child(item, 'sdtContent')));
        else if (item.local === 'customXml') cells.push(...rowCells(item));
    });
    return cells;
}

function readTable(tbl, ctx) {
    const rows = [];
    tableRows(tbl).forEach((tr) => {
        const trPr = child(tr, 'trPr');
        let column = Number(attr(child(trPr, 'gridBefore'), 'val') || 0);
        const cells = [];
        rowCells(tr).forEach((tc) => {
            const tcPr = child(tc, 'tcPr');
            const span = Math.max(1, Number(attr(child(tcPr, 'gridSpan'), 'val') || 1));
            const merge = child(tcPr, 'vMerge');
            if (merge && (attr(merge, 'val') || 'continue') === 'continue') {
                // Продолжение объединения по вертикали: растим ячейку выше.
                for (let index = rows.length - 1; index >= 0; index -= 1) {
                    const above = rows[index].cells.find((cell) => cell.column === column);
                    if (above) {
                        above.rowSpan += 1;
                        break;
                    }
                }
                column += span;
                return;
            }
            cells.push({ blocks: readBlocks(tc, ctx), colSpan: span, rowSpan: 1, column });
            column += span;
        });
        rows.push({ header: Boolean(child(trPr, 'tblHeader')), cells });
    });
    return { type: 'table', rows };
}

function readBlocks(container, ctx) {
    const blocks = [];
    elements(container).forEach((item) => {
        switch (item.local) {
        case 'p': blocks.push(...readParagraph(item, ctx)); break;
        case 'tbl': blocks.push(readTable(item, ctx)); break;
        case 'sdt': blocks.push(...readBlocks(child(item, 'sdtContent'), ctx)); break;
        case 'customXml': case 'ins': case 'moveTo': blocks.push(...readBlocks(item, ctx)); break;
        case 'AlternateContent': {
            const branch = chooseAlternate(item);
            if (branch) blocks.push(...readBlocks(branch, ctx));
            break;
        }
        default: break;
        }
    });
    return blocks;
}

/* ── Текст для «Скопировать»: абзацы строками, таблица — через табуляцию ── */
const runsText = (runs) => runs.map((run) => run.text ?? '').join('');

export function blocksToText(blocks) {
    const lines = [];
    blocks.forEach((block) => {
        if (block.type === 'table') {
            block.rows.forEach((row) => {
                lines.push(row.cells.map((cell) => blocksToText(cell.blocks).replace(/\s*\n\s*/g, ' ').trim()).join('\t'));
            });
            lines.push('');
            return;
        }
        const text = runsText(block.runs);
        const prefix = block.list ? `${'  '.repeat(block.list.level)}${block.list.label}${block.list.label ? ' ' : ''}` : '';
        lines.push(prefix + text);
    });
    return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

export async function parseDocx(bytes) {
    const zip = await openPackage(bytes);
    const documentPath = await mainPartPath(zip, 'word/document.xml');
    const xml = await readXml(zip, documentPath);
    const body = findFirst(xml, 'body');
    if (!body) throw new PreviewError('В файле нет текста документа Word.');
    const rels = await readRels(zip, documentPath);
    const stylesRel = relOfType(rels, '/styles');
    const numberingRel = relOfType(rels, '/numbering');
    const [stylesXml, numberingXml] = await Promise.all([
        readXml(zip, stylesRel ? stylesRel.target : 'word/styles.xml'),
        readXml(zip, numberingRel ? numberingRel.target : 'word/numbering.xml'),
    ]);
    const ctx = {
        rels,
        styles: stylesXml ? readStyles(stylesXml) : new Map(),
        numbering: numberingXml ? readNumbering(numberingXml) : new Map(),
        counters: new Map(),
        images: new Set(),
    };
    const blocks = readBlocks(body, ctx);
    const media = await loadImages(zip, ctx.images);
    return { blocks, media, text: blocksToText(blocks) };
}
