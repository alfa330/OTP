/* Вид ячеек книги Excel: цвета, шрифт, заливка, рамки, выравнивание.

   Просьба владельца 13.11.2026 (снимок «2026.xlsx» рядом с Excel): «нужно
   передавать цвета». В файле цвет хранится тремя способами — прямым кодом
   (rgb="FFFFFF00"), номером цвета темы с оттенком (theme="4" tint="0.6") и
   номером старой палитры (indexed="44"); всё приводится к #RRGGBB здесь.

   Модуль чистый — проверяется в node (tests/file_preview.test.mjs). */
import { attr, child, childrenOf, findFirst, onOff } from './xmlTree.js';
import { BUILTIN_FORMATS } from './xlsxFormat.js';

/* Старая палитра Excel (indexed 0–63); 64 и 65 — «системные» цвета текста и
   фона, то есть цвет по умолчанию. */
export const INDEXED_COLORS = [
    '000000', 'FFFFFF', 'FF0000', '00FF00', '0000FF', 'FFFF00', 'FF00FF', '00FFFF',
    '000000', 'FFFFFF', 'FF0000', '00FF00', '0000FF', 'FFFF00', 'FF00FF', '00FFFF',
    '800000', '008000', '000080', '808000', '800080', '008080', 'C0C0C0', '808080',
    '9999FF', '993366', 'FFFFCC', 'CCFFFF', '660066', 'FF8080', '0066CC', 'CCCCFF',
    '000080', 'FF00FF', 'FFFF00', '00FFFF', '800080', '800000', '008080', '0000FF',
    '00CCFF', 'CCFFFF', 'CCFFCC', 'FFFF99', '99CCFF', 'FF99CC', 'CC99FF', 'FFCC99',
    '3366FF', '33CCCC', '99CC00', 'FFCC00', 'FF9900', 'FF6600', '666699', '969696',
    '003366', '339966', '003300', '333300', '993300', '993366', '333399', '333333',
].map((hex) => `#${hex}`);

/* Тема Office по умолчанию — если в книге своей нет. Порядок — как номера theme
   в ячейках: 0 фон, 1 текст, 2 фон 2, 3 текст 2, 4–9 акценты, 10–11 ссылки. */
export const DEFAULT_THEME = ['#FFFFFF', '#000000', '#E7E6E6', '#44546A', '#4472C4', '#ED7D31',
    '#A5A5A5', '#FFC000', '#5B9BD5', '#70AD47', '#0563C1', '#954F72'];

// В файле темы цвета идут как dk1, lt1, dk2, lt2…, а номер theme в ячейке считает
// с lt1: тёмный и светлый поменяны местами — так устроен Excel.
const THEME_SLOTS = ['lt1', 'dk1', 'lt2', 'dk2', 'accent1', 'accent2', 'accent3', 'accent4',
    'accent5', 'accent6', 'hlink', 'folHlink'];

export function readThemeColors(themeXml) {
    const scheme = findFirst(themeXml, 'clrScheme');
    if (!scheme) return DEFAULT_THEME.slice();
    return THEME_SLOTS.map((slot, index) => {
        const node = child(scheme, slot);
        const rgb = attr(findFirst(node, 'srgbClr'), 'val');
        if (rgb) return `#${rgb.toUpperCase()}`;
        const system = findFirst(node, 'sysClr');
        const last = attr(system, 'lastClr');
        if (last) return `#${last.toUpperCase()}`;
        if (system) return attr(system, 'val') === 'window' ? '#FFFFFF' : '#000000';
        return DEFAULT_THEME[index];
    });
}

const clamp01 = (value) => Math.min(1, Math.max(0, value));

function toHls(hex) {
    const r = parseInt(hex.slice(1, 3), 16) / 255;
    const g = parseInt(hex.slice(3, 5), 16) / 255;
    const b = parseInt(hex.slice(5, 7), 16) / 255;
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const l = (max + min) / 2;
    if (max === min) return [0, l, 0];
    const d = max - min;
    const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    let h;
    if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    return [h / 6, l, s];
}

function fromHls([h, l, s]) {
    if (s === 0) {
        const v = Math.round(l * 255);
        return [v, v, v];
    }
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    const channel = (t) => {
        let x = t;
        if (x < 0) x += 1;
        if (x > 1) x -= 1;
        if (x < 1 / 6) return p + (q - p) * 6 * x;
        if (x < 1 / 2) return q;
        if (x < 2 / 3) return p + (q - p) * (2 / 3 - x) * 6;
        return p;
    };
    return [channel(h + 1 / 3), channel(h), channel(h - 1 / 3)].map((v) => Math.round(v * 255));
}

export const toHex = ([r, g, b]) => `#${[r, g, b].map((v) => Math.round(Math.min(255, Math.max(0, v)))
    .toString(16).padStart(2, '0')).join('').toUpperCase()}`;

/** Оттенок цвета темы: tint < 0 — темнее, > 0 — светлее (ECMA-376, яркость в HLS). */
export function applyTint(hex, tint) {
    const value = Number(tint);
    if (!Number.isFinite(value) || value === 0) return hex;
    const [h, l, s] = toHls(hex);
    const lum = value < 0 ? l * (1 + value) : l * (1 - value) + value;
    return toHex(fromHls([h, clamp01(lum), s]));
}

/** Цвет из <color>/<fgColor>/<bgColor> → #RRGGBB; null — «автоматический» (по умолчанию). */
export function resolveColor(node, palette) {
    if (!node) return null;
    if (['1', 'true'].includes(String(attr(node, 'auto')))) return null;
    let hex = null;
    const rgb = attr(node, 'rgb');
    const theme = attr(node, 'theme');
    const indexed = attr(node, 'indexed');
    if (rgb && /^[0-9A-Fa-f]{6,8}$/.test(rgb)) hex = `#${rgb.slice(-6).toUpperCase()}`;
    else if (theme !== undefined) hex = (palette?.theme || DEFAULT_THEME)[Number(theme)] || null;
    else if (indexed !== undefined) hex = (palette?.indexed || INDEXED_COLORS)[Number(indexed)] || null;
    if (!hex) return null;
    return applyTint(hex, attr(node, 'tint'));
}

/* Рамка: толщина и вид линии Excel → CSS. Вес — чтобы на стыке двух ячеек
   победила более заметная линия, как в Excel. */
const BORDER_LINES = {
    hair: [1, 'solid', 1], thin: [1, 'solid', 2], dotted: [1, 'dotted', 1], dashed: [1, 'dashed', 1],
    dashDot: [1, 'dashed', 1], dashDotDot: [1, 'dashed', 1], slantDashDot: [2, 'dashed', 3],
    medium: [2, 'solid', 4], mediumDashed: [2, 'dashed', 3], mediumDashDot: [2, 'dashed', 3],
    mediumDashDotDot: [2, 'dashed', 3], double: [3, 'double', 5], thick: [3, 'solid', 6],
};

function readBorderSide(node, palette) {
    const style = attr(node, 'style');
    if (!node || !style || style === 'none' || !BORDER_LINES[style]) return null;
    const [width, line, weight] = BORDER_LINES[style];
    const color = resolveColor(child(node, 'color'), palette) || (style === 'hair' ? '#BFBFBF' : '#000000');
    return { css: `${width}px ${line} ${color}`, weight: weight * 10 + width };
}

/** Из двух линий на стыке ячеек — более заметная. */
export const strongerBorder = (a, b) => (!a ? b : !b ? a : (b.weight > a.weight ? b : a));

const FILL_NONE = new Set(['none', 'gray125', 'gray0625']);

function readFill(fill, palette, dxf = false) {
    const pattern = child(fill, 'patternFill');
    if (pattern) {
        const type = attr(pattern, 'patternType') || (dxf ? 'solid' : 'none');
        if (FILL_NONE.has(type)) return null;
        // В форматах условного форматирования сплошная заливка лежит в bgColor.
        return dxf
            ? resolveColor(child(pattern, 'bgColor'), palette) || resolveColor(child(pattern, 'fgColor'), palette)
            : resolveColor(child(pattern, 'fgColor'), palette) || resolveColor(child(pattern, 'bgColor'), palette);
    }
    const gradient = child(fill, 'gradientFill');
    if (gradient) return resolveColor(child(childrenOf(gradient, 'stop')[0], 'color'), palette);
    return null;
}

function readFont(font, palette) {
    if (!font) return {};
    const underline = child(font, 'u');
    return {
        bold: onOff(child(font, 'b')),
        italic: onOff(child(font, 'i')),
        underline: Boolean(underline) && attr(underline, 'val') !== 'none',
        strike: onOff(child(font, 'strike')),
        size: Number(attr(child(font, 'sz'), 'val')) || null,
        color: resolveColor(child(font, 'color'), palette),
    };
}

const HORIZONTAL = { left: 'left', center: 'center', centerContinuous: 'center', right: 'right', justify: 'justify', distributed: 'justify', fill: 'left' };
const VERTICAL = { top: 'top', center: 'middle', bottom: 'bottom', justify: 'middle', distributed: 'middle' };

/** Стили книги: xfs — по номеру s у ячейки, dxfs — для условного форматирования. */
export function readBookStyles(stylesXml, themeColors = DEFAULT_THEME) {
    const custom = childrenOf(findFirst(findFirst(stylesXml, 'colors'), 'indexedColors'), 'rgbColor')
        .map((node) => `#${String(attr(node, 'rgb') || '000000').slice(-6).toUpperCase()}`);
    const palette = { theme: themeColors, indexed: custom.length ? custom : INDEXED_COLORS };
    if (!stylesXml) return { xfs: [], dxfs: [], palette, baseSize: 11 };
    const formats = new Map();
    childrenOf(findFirst(stylesXml, 'numFmts'), 'numFmt')
        .forEach((fmt) => formats.set(Number(attr(fmt, 'numFmtId')), attr(fmt, 'formatCode') || ''));
    const fonts = childrenOf(findFirst(stylesXml, 'fonts'), 'font').map((font) => readFont(font, palette));
    const fills = childrenOf(findFirst(stylesXml, 'fills'), 'fill').map((fill) => readFill(fill, palette));
    const borders = childrenOf(findFirst(stylesXml, 'borders'), 'border').map((border) => ({
        left: readBorderSide(child(border, 'left') || child(border, 'start'), palette),
        right: readBorderSide(child(border, 'right') || child(border, 'end'), palette),
        top: readBorderSide(child(border, 'top'), palette),
        bottom: readBorderSide(child(border, 'bottom'), palette),
    }));
    const xfs = childrenOf(findFirst(stylesXml, 'cellXfs'), 'xf').map((xf) => {
        const id = Number(attr(xf, 'numFmtId') || 0);
        const alignment = child(xf, 'alignment');
        const font = fonts[Number(attr(xf, 'fontId') || 0)] || {};
        const border = borders[Number(attr(xf, 'borderId') || 0)] || null;
        return {
            code: formats.get(id) ?? BUILTIN_FORMATS[id] ?? 'General',
            font,
            bold: Boolean(font.bold),
            fill: fills[Number(attr(xf, 'fillId') || 0)] || null,
            border: border && (border.left || border.right || border.top || border.bottom) ? border : null,
            align: HORIZONTAL[attr(alignment, 'horizontal')] || null,
            vAlign: VERTICAL[attr(alignment, 'vertical')] || null,
            wrap: ['1', 'true'].includes(String(attr(alignment, 'wrapText'))),
            indent: Number(attr(alignment, 'indent')) || 0,
        };
    });
    const dxfs = childrenOf(findFirst(stylesXml, 'dxfs'), 'dxf').map((dxf) => {
        const font = readFont(child(dxf, 'font'), palette);
        const fontNode = child(dxf, 'font');
        return {
            fill: readFill(child(dxf, 'fill'), palette, true),
            color: font.color || null,
            bold: child(fontNode, 'b') ? onOff(child(fontNode, 'b')) : null,
            italic: child(fontNode, 'i') ? onOff(child(fontNode, 'i')) : null,
            underline: child(fontNode, 'u') ? font.underline : null,
            strike: child(fontNode, 'strike') ? onOff(child(fontNode, 'strike')) : null,
        };
    });
    return { xfs, dxfs, palette, baseSize: fonts[0]?.size || 11 };
}

/* Плавный переход цвета — для цветовых шкал условного форматирования. */
export function mixColors(from, to, share) {
    const t = clamp01(share);
    const a = [1, 3, 5].map((at) => parseInt(from.slice(at, at + 2), 16));
    const b = [1, 3, 5].map((at) => parseInt(to.slice(at, at + 2), 16));
    return toHex(a.map((value, index) => value + (b[index] - value) * t));
}
