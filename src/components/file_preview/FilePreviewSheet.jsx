import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
    Check, ChevronLeft, ChevronRight, Code2, Copy, Download, Loader2, Minus, Monitor, PanelLeft, Plus, RotateCw, Smartphone, X,
} from 'lucide-react';
import { APPLE_FONT, IosSegmented, iosBtnPrimary, iosBtnSecondary } from '../ui/ios';
import { renderMarkdown } from '../ui/markdown';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';
import {
    fileExtension, fileTypeLabel, formatFileSize, mediaTypeOf, officeKindFromEntries, plural,
    previewKind, sniffKind, unsupportedReason,
} from './fileKinds.js';
import { decodeTextBytes, prettyJson } from './textDecode.js';
import { columnName } from './xlsxFormat.js';
import { previewCache } from './previewCache.js';
import {
    MOCKUP_SANDBOX, acceptMockupEscape, buildHtmlPreview, createLeaveWatch, htmlViewModes, shownHtmlMode,
} from './htmlMockup.js';
import PdfPreview, { pdfDocumentText } from './PdfPreview';
import './file-preview.css';

/* Просмотр вложения без скачивания — как Quick Look в macOS, на весь экран.

   Просьба владельца 13.11.2026 («Задачи»): файлы читать, не открывая и не
   скачивая — содержимое открывается поверх страницы, а скачивать не
   обязательно. Тем же днём: окно посреди страницы «смотрится не очень» —
   просмотр занимает весь экран, но человек остаётся на той же странице:
   слой поверх портала, сверху тонкая панель (файл, листание, «Скопировать»,
   «Скачать», закрыть), слева — список файлов того же блока, справа — файл.
   Окно одно на все форматы: PDF лентой страниц, Word и PowerPoint — текстом
   с таблицами и картинками, Excel и CSV — таблицей с листами, текст и код,
   картинки, видео и звук. Что браузер прочесть не может (.doc, .xls, архивы),
   окно так и говорит и предлагает скачать.

   Файл скачивается тем же запросом, что и кнопка «Скачать» (loadFile), и
   остаётся в памяти вкладки (previewCache): повторное открытие и «Скачать»
   из окна не тянут его с сервера ещё раз.

   files — файлы группы (листаются стрелками ← → и кнопками в шапке),
   index — открытый (-1 — окно закрыто), loadFile(file, signal) → Blob,
   cacheKey(file) — ключ кэша (у разных разделов свои номера вложений). */

const TEXT_LIMIT = 1_500_000;
const TABLE_MAX_ROWS = 5000;
const ROWS_STEP = 300;
const NUMERIC = /^[-+−]?\d[\d\s ]*([.,]\d+)?%?$/;
const ZOOM_STEPS = [0.5, 0.67, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3];

// Список файлов слева: показан или спрятан — помнится в этом браузере (удобство, не данные).
const SIDEBAR_KEY = 'otp_file_viewer_sidebar';
const readSidebarPref = () => {
    try {
        return window.localStorage.getItem(SIDEBAR_KEY) !== '0';
    } catch {
        return true;
    }
};
const writeSidebarPref = (open) => {
    try {
        window.localStorage.setItem(SIDEBAR_KEY, open ? '1' : '0');
    } catch {
        // без хранилища — просто по умолчанию
    }
};

/** Сохранить уже скачанный файл на диск — без второго запроса к серверу. */
export function saveBlobAs(blob, name) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = name || 'file';
    link.rel = 'noopener';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function describeError(error) {
    if (error?.name === 'PasswordException') return 'PDF защищён паролем — скачайте файл, чтобы открыть его.';
    if (error?.name === 'PreviewError') return error.message;
    const status = error?.response?.status;
    if (status === 403) return 'Нет доступа к этому файлу.';
    if (status === 404) return 'Файл не найден — возможно, его удалили.';
    if (status >= 500) return 'Сервер не смог отдать файл. Попробуйте ещё раз чуть позже.';
    if (error?.response === undefined && (error?.code === 'ERR_NETWORK' || error?.message === 'Network Error')) {
        return 'Нет связи с сервером. Проверьте интернет и попробуйте ещё раз.';
    }
    return 'Не удалось открыть файл. Попробуйте ещё раз или скачайте его.';
}

async function writeClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        // Без доступа к буферу (старый Safari, рамка без разрешения) — через выделение.
        const area = document.createElement('textarea');
        area.value = text;
        area.setAttribute('readonly', '');
        area.style.cssText = 'position:fixed;top:-1000px;opacity:0';
        document.body.appendChild(area);
        area.select();
        const ok = document.execCommand?.('copy') === true;
        area.remove();
        return ok;
    }
}

function csvSheet(rows, truncated) {
    const colCount = rows.reduce((max, row) => Math.max(max, row.length), 0);
    return {
        name: '',
        header: true,
        rows: rows.map((row) => row.map((value) => {
            const text = String(value ?? '');
            const numeric = NUMERIC.test(text.trim());
            return text ? { text, numeric, align: numeric ? 'right' : null } : null;
        })),
        rowCount: rows.length,
        colCount,
        merges: [],
        truncatedRows: truncated,
    };
}

/** Скачанный файл → то, что рисует окно. dispose освобождает адреса картинок и документ PDF. */
async function buildPreview(file, blob) {
    const info = previewKind(file);
    let { kind } = info;
    const bytes = new Uint8Array(await blob.arrayBuffer());
    if (kind === 'unknown') kind = sniffKind(bytes);
    if (kind === 'zip') {
        const { default: JSZip } = await import('jszip');
        try {
            kind = officeKindFromEntries(Object.keys((await JSZip.loadAsync(bytes)).files));
        } catch {
            kind = 'archive';
        }
    }
    if (['legacy', 'archive', 'unknown'].includes(kind)) {
        return { kind: 'unsupported', typeKind: kind, reason: unsupportedReason(kind, info.ext) };
    }
    switch (kind) {
    case 'pdf': {
        const library = await import('../wazzup/pdfRuntime');
        const task = library.getDocument({ data: bytes, isEvalSupported: false, enableXfa: false, maxImageSize: 16_000_000, verbosity: 0 });
        const pdf = await task.promise;
        return { kind, pdf, library, dispose: () => { task.destroy()?.catch?.(() => {}); } };
    }
    case 'image': case 'video': case 'audio': {
        // Тип из базы бывает application/octet-stream — тогда SVG и видео не играют.
        const type = mediaTypeOf(info.ext) || (blob.type !== 'application/octet-stream' ? blob.type : '');
        const url = URL.createObjectURL(type && type !== blob.type ? new Blob([blob], { type }) : blob);
        return { kind, url, dispose: () => URL.revokeObjectURL(url) };
    }
    case 'csv': {
        const { text } = decodeTextBytes(bytes);
        const { default: Papa } = await import('papaparse');
        const parsed = Papa.parse(text, { skipEmptyLines: 'greedy', delimiter: info.ext === 'tsv' ? '\t' : '', preview: TABLE_MAX_ROWS + 1 });
        const rows = parsed.data.slice(0, TABLE_MAX_ROWS);
        return { kind: 'sheets', sheets: [csvSheet(rows, parsed.data.length > TABLE_MAX_ROWS)], text };
    }
    case 'xlsx': {
        const { parseXlsx, sheetToTsv } = await import('./xlsxParse.js');
        const book = await parseXlsx(bytes, { maxRows: TABLE_MAX_ROWS });
        return { kind: 'sheets', sheets: book.sheets, active: book.active || 0, sheetText: (index) => sheetToTsv(book.sheets[index]) };
    }
    case 'docx': case 'pptx': {
        const parsed = kind === 'docx'
            ? await (await import('./docxParse.js')).parseDocx(bytes)
            : await (await import('./pptxParse.js')).parsePptx(bytes);
        const media = new Map();
        parsed.media.forEach((item, path) => media.set(path, URL.createObjectURL(new Blob([item.bytes], { type: item.type }))));
        return { kind, ...parsed, media, dispose: () => media.forEach((url) => URL.revokeObjectURL(url)) };
    }
    case 'markdown': {
        const { text } = decodeTextBytes(bytes);
        return { kind: 'markdown', text, html: renderMarkdown(text) };
    }
    case 'html': {
        // Строка уже раскодирована (UTF-8 или 1251): srcdoc — это строка, и
        // <meta charset> файла её не перекодирует. Клетка — htmlMockup.js.
        return buildHtmlPreview(decodeTextBytes(bytes).text);
    }
    default: {
        const { text } = decodeTextBytes(bytes);
        const shown = kind === 'json' ? prettyJson(text) : text;
        return { kind: 'text', text: shown, mono: kind === 'json' || info.mono || info.kind === 'unknown' };
    }
    }
}

/* ── Виды ── */

const FILE_GLYPH_TONES = {
    pdf: '#e5484d', docx: '#2b6cd8', xlsx: '#1f9d55', csv: '#1f9d55', pptx: '#e8590c',
    image: '#8e4ec6', video: '#d6409f', audio: '#d6409f', archive: '#8a8f98', html: '#e4572e',
};
// Старые форматы Office — цветом своего приложения: .doc синий, как .docx.
const LEGACY_TONE_KIND = { doc: 'docx', dot: 'docx', rtf: 'docx', odt: 'docx', pages: 'docx',
    xls: 'xlsx', xlt: 'xlsx', ods: 'xlsx', numbers: 'xlsx', ppt: 'pptx', pps: 'pptx', odp: 'pptx', key: 'pptx' };

/** Значок документа с подписью расширения — как иконка файла в Finder. */
function FileGlyph({ ext, kind }) {
    const tone = FILE_GLYPH_TONES[kind] || FILE_GLYPH_TONES[LEGACY_TONE_KIND[ext]] || '#64748b';
    const label = (ext || 'файл').toUpperCase().slice(0, 4);
    return (
        <svg width="64" height="80" viewBox="0 0 64 80" aria-hidden="true" className="tfv-glyph">
            <path d="M8 2h34l20 20v52a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4V6a4 4 0 0 1 4-4z" className="tfv-glyph-page" />
            <path d="M42 2v16a4 4 0 0 0 4 4h16" className="tfv-glyph-fold" />
            <rect x="10" y="50" width="44" height="18" rx="4" fill={tone} />
            <text x="32" y="63" textAnchor="middle" fontSize="11" fontWeight="700" fill="#fff" fontFamily="-apple-system, system-ui, sans-serif">{label}</text>
        </svg>
    );
}

/** Цветная метка типа — как значки файлов в Finder и на Диске: PDF красный, Word синий. */
function TypeBadge({ ext, kind }) {
    const tone = FILE_GLYPH_TONES[kind] || FILE_GLYPH_TONES[LEGACY_TONE_KIND[ext]] || '#64748b';
    return (
        <span className="tfv-badge" style={{ background: tone }} aria-hidden="true">
            {(ext || 'файл').toUpperCase().slice(0, 4)}
        </span>
    );
}

function CenterState({ children }) {
    return <div className="tfv-state">{children}</div>;
}

function LoadingState() {
    // Индикатор — только если файл не открылся мгновенно: из кэша окно
    // показывает содержимое сразу, и мелькание крутилки было бы шумом.
    const [visible, setVisible] = useState(false);
    useEffect(() => {
        const timer = setTimeout(() => setVisible(true), 180);
        return () => clearTimeout(timer);
    }, []);
    return (
        <CenterState>
            {visible && (
                <>
                    <Loader2 size={22} className="animate-spin text-slate-400" />
                    <span>Открываем файл…</span>
                </>
            )}
        </CenterState>
    );
}

function TextView({ text, mono }) {
    const shown = text.length > TEXT_LIMIT ? text.slice(0, TEXT_LIMIT) : text;
    return (
        <div className="tfv-scroll thin-scroll">
            <article className="tfv-paper">
                {text.trim() ? <pre className={`tfv-text${mono ? ' is-mono' : ''}`}>{shown}</pre>
                    : <p className="tfv-muted">Файл пустой.</p>}
                {shown !== text && <p className="tfv-muted tfv-cut">Показано начало файла. Целиком — кнопками «Скопировать» и «Скачать» вверху.</p>}
            </article>
        </div>
    );
}

function MarkdownView({ html }) {
    return (
        <div className="tfv-scroll thin-scroll">
            {/* html прошёл DOMPurify в renderMarkdown — без него вставлять нельзя. */}
            <article className="tfv-paper tfv-doc" dangerouslySetInnerHTML={{ __html: html }} />
        </div>
    );
}

const safeHref = (href) => (/^(https?:|mailto:|tel:)/i.test(String(href || '')) ? href : null);

function DocRuns({ runs, media }) {
    return runs.map((run, index) => {
        if (run.image) {
            const url = media.get(run.image);
            return url ? <img key={index} src={url} alt="" loading="lazy" className="tfv-doc-img" />
                : <span key={index} className="tfv-doc-ph">Рисунок</span>;
        }
        if (run.placeholder) return <span key={index} className="tfv-doc-ph">{run.placeholder}</span>;
        let node = run.text;
        if (run.vert === 'superscript') node = <sup>{node}</sup>;
        if (run.vert === 'subscript') node = <sub>{node}</sub>;
        if (run.bold) node = <strong>{node}</strong>;
        if (run.italic) node = <em>{node}</em>;
        if (run.underline && !run.href) node = <u>{node}</u>;
        if (run.strike) node = <s>{node}</s>;
        const href = safeHref(run.href);
        if (href) node = <a href={href} target="_blank" rel="noopener noreferrer">{node}</a>;
        return <React.Fragment key={index}>{node}</React.Fragment>;
    });
}

function DocBlocks({ blocks, media }) {
    const out = [];
    blocks.forEach((block, index) => {
        if (block.type === 'table') {
            out.push(
                <div key={index} className="tfv-doc-table">
                    <table>
                        <tbody>
                            {block.rows.map((row, rowIndex) => (
                                <tr key={rowIndex} className={row.header ? 'is-header' : undefined}>
                                    {row.cells.map((cell, cellIndex) => (
                                        <td key={cellIndex} colSpan={cell.colSpan > 1 ? cell.colSpan : undefined}
                                            rowSpan={cell.rowSpan > 1 ? cell.rowSpan : undefined}>
                                            <DocBlocks blocks={cell.blocks} media={media} />
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            );
            return;
        }
        const empty = !block.runs.length || block.runs.every((run) => run.text !== undefined && !run.text.trim());
        if (empty && !block.list) {
            // Пустые абзацы Word — это отступы. Два подряд — один отступ.
            if (out.length && out[out.length - 1]?.props?.['data-gap']) return;
            out.push(<div key={index} className="tfv-gap" data-gap="1" aria-hidden="true" />);
            return;
        }
        const style = block.align ? { textAlign: block.align } : undefined;
        const content = <DocRuns runs={block.runs} media={media} />;
        if (block.heading !== null && block.heading !== undefined) {
            const Tag = `h${Math.max(1, Math.min(block.heading, 4))}`;
            out.push(
                <Tag key={index} className={block.heading === 0 ? 'tfv-title' : undefined} style={style}>
                    {block.list?.label ? `${block.list.label} ` : null}{content}
                </Tag>
            );
            return;
        }
        if (block.list) {
            out.push(
                <div key={index} className={`tfv-li${block.list.ordered ? ' is-ordered' : ''}`} style={{ '--lvl': block.list.level }}>
                    <span className="tfv-li-mark">{block.list.label}</span>
                    <p style={style}>{content}</p>
                </div>
            );
            return;
        }
        out.push(<p key={index} style={style}>{content}</p>);
    });
    return out;
}

function DocxView({ blocks, media }) {
    return (
        <div className="tfv-scroll thin-scroll">
            <article className="tfv-paper tfv-doc">
                {blocks.length ? <DocBlocks blocks={blocks} media={media} /> : <p className="tfv-muted">В документе нет текста.</p>}
            </article>
        </div>
    );
}

function PptxView({ slides, media }) {
    return (
        <div className="tfv-scroll thin-scroll">
            <div className="tfv-slides">
                {slides.map((slide) => (
                    <section key={slide.number} className={`tfv-slide${slide.hidden ? ' is-hidden' : ''}`} aria-label={`Слайд ${slide.number}`}>
                        <div className="tfv-slide-num">Слайд {slide.number}{slide.hidden ? ' · скрыт при показе' : ''}</div>
                        {slide.title && <h2>{slide.title}</h2>}
                        {slide.blocks.map((block, index) => {
                            if (block.type === 'image') {
                                const url = media.get(block.path);
                                return url ? <img key={index} src={url} alt="" loading="lazy" /> : null;
                            }
                            if (block.type === 'table') {
                                return (
                                    <div key={index} className="tfv-doc-table">
                                        <table><tbody>{block.rows.map((row, rowIndex) => (
                                            <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>
                                        ))}</tbody></table>
                                    </div>
                                );
                            }
                            return (
                                <div key={index} className={block.subtitle ? 'tfv-slide-sub' : 'tfv-slide-text'}>
                                    {block.paragraphs.map((item, itemIndex) => (
                                        <p key={itemIndex} className={item.bullet ? 'is-bullet' : undefined} style={{ '--lvl': item.level }}>{item.text}</p>
                                    ))}
                                </div>
                            );
                        })}
                        {!slide.title && !slide.blocks.length && <p className="tfv-muted">На слайде нет текста.</p>}
                    </section>
                ))}
            </div>
        </div>
    );
}

// Длинный текст в ячейке переносится по словам, короткий стоит в строку.
const isLongText = (text) => typeof text === 'string' && (text.length > 36 || text.includes('\n'));

function SheetTable({ sheet }) {
    const [limit, setLimit] = useState(ROWS_STEP);
    const covered = useMemo(() => {
        const cells = new Map();
        sheet.merges.forEach((merge) => {
            for (let row = merge.row; row < merge.row + merge.rowSpan; row += 1) {
                for (let col = merge.col; col < merge.col + merge.colSpan; col += 1) {
                    cells.set(`${row}:${col}`, row === merge.row && col === merge.col ? merge : 'covered');
                }
            }
        });
        return cells;
    }, [sheet]);
    if (sheet.note) return <CenterState><span>{sheet.note}</span></CenterState>;
    if (!sheet.rowCount) return <CenterState><span>Лист пустой.</span></CenterState>;
    const columns = Array.from({ length: sheet.colCount }, (_, index) => index);
    const headerRow = sheet.header ? sheet.rows[0] || [] : null;
    const firstRow = sheet.header ? 1 : 0;
    const lastRow = Math.min(sheet.rowCount, firstRow + limit);
    const rows = [];
    for (let row = firstRow; row < lastRow; row += 1) {
        const cells = sheet.rows[row] || [];
        rows.push(
            <tr key={row}>
                {!sheet.header && <th scope="row">{row + 1}</th>}
                {columns.map((col) => {
                    const merge = covered.get(`${row}:${col}`);
                    if (merge === 'covered') return null;
                    const cell = cells[col];
                    const classes = [cell?.numeric ? 'is-num' : '', cell?.bold ? 'is-bold' : '',
                        !merge && isLongText(cell?.text) ? 'is-long' : ''].filter(Boolean).join(' ');
                    return (
                        <td key={col} className={classes || undefined}
                            style={cell?.align && !cell.numeric ? { textAlign: cell.align } : undefined}
                            colSpan={merge?.colSpan > 1 ? merge.colSpan : undefined}
                            rowSpan={merge?.rowSpan > 1 ? merge.rowSpan : undefined}>
                            {cell?.text ?? ''}
                        </td>
                    );
                })}
            </tr>
        );
    }
    const hidden = sheet.rowCount - lastRow;
    return (
        <div className="tfv-grid-wrap thin-scroll">
            <table className="tfv-grid">
                <thead>
                    <tr>
                        {!sheet.header && <th className="tfv-corner" aria-hidden="true" />}
                        {columns.map((col) => (
                            <th key={col} scope="col" className={headerRow ? `is-label${isLongText(headerRow[col]?.text) ? ' is-long' : ''}` : undefined}>
                                {headerRow ? headerRow[col]?.text ?? '' : columnName(col)}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
            {(hidden > 0 || sheet.truncatedRows || sheet.truncatedCols) && (
                <div className="tfv-grid-foot">
                    {hidden > 0 && (
                        <button type="button" className={iosBtnSecondary} onClick={() => setLimit((value) => value + ROWS_STEP * 3)}>
                            Показать ещё {Math.min(hidden, ROWS_STEP * 3)} {plural(Math.min(hidden, ROWS_STEP * 3), 'строку', 'строки', 'строк')}
                        </button>
                    )}
                    {hidden <= 0 && sheet.truncatedRows && (
                        <span>
                            Показаны первые {sheet.rowCount} строк{sheet.totalRows > sheet.rowCount ? ` из ${sheet.totalRows}` : ''} — весь файл в «Скачать».
                        </span>
                    )}
                    {hidden <= 0 && !sheet.truncatedRows && sheet.truncatedCols && <span>Показаны первые {sheet.colCount} столбцов — весь файл в «Скачать».</span>}
                </div>
            )}
        </div>
    );
}

/* Лист Excel с видом из файла: ширины столбцов и высоты строк, заливки,
   шрифт, рамки, выравнивание, перенос и цвета условного форматирования
   (xlsxParse). Скрытые строки и столбцы не показываются, номера строк — как в
   Excel (1, 3, 5…). Лист — белый, как в самом Excel, и в тёмной теме. */
const ROW_HEADER_PX = 44;
const SPILL_MAX_COLS = 12;
const SPILL_BEYOND_PX = 640;

/* Ширина текста в пикселях — чтобы число или дата не обрезались по краю ячейки.
   Excel в таком случае рисует «###», обрезанное же число читается как другое
   (01.11.2026 → «01.11.202»): столбец с числами расширяется до самого широкого. */
let measureContext = null;
const measureText = (text, font) => {
    if (typeof document === 'undefined') return 0;
    if (!measureContext) measureContext = document.createElement('canvas').getContext('2d');
    if (!measureContext) return 0;
    measureContext.font = font;
    return measureContext.measureText(text).width;
};
const FIT_ROWS = 3000;

const hexAlpha = (hex, alpha) => {
    const value = parseInt(String(hex).slice(1), 16);
    return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`;
};

function StyledSheetTable({ sheet }) {
    const [limit, setLimit] = useState(ROWS_STEP);
    const { visibleRows = [], visibleCols = [] } = sheet;
    const numberWidths = useMemo(() => {
        const widths = new Map();
        const spanned = new Set();
        sheet.merges.forEach((merge) => {
            if (merge.colSpan < 2) return;
            for (let row = merge.row; row < merge.row + merge.rowSpan; row += 1) spanned.add(`${row}:${merge.col}`);
        });
        visibleRows.slice(0, FIT_ROWS).forEach((row) => {
            const cells = sheet.rows[row];
            if (!cells) return;
            visibleCols.forEach((col) => {
                const cell = cells[col];
                if (!cell?.text || (typeof cell.v !== 'number' && !cell.err) || cell.look?.wrap || spanned.has(`${row}:${col}`)) return;
                const look = cell.look || {};
                const font = `${look.bold ? '700 ' : ''}${Math.round(13 * (look.size || 1) * 10) / 10}px ${APPLE_FONT}`;
                const need = Math.ceil(measureText(cell.text, font)) + 14 + (look.indent ? look.indent * 9 : 0);
                if (need > (widths.get(col) || 0)) widths.set(col, need);
            });
        });
        return widths;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sheet]);
    const widthOf = (col) => Math.max(sheet.colWidths?.[col] || sheet.defaultColWidth || 64, numberWidths.get(col) || 0);
    const mergeInfo = useMemo(() => {
        const map = new Map();
        sheet.merges.forEach((merge) => {
            const rowsIn = visibleRows.filter((row) => row >= merge.row && row < merge.row + merge.rowSpan);
            const colsIn = visibleCols.filter((col) => col >= merge.col && col < merge.col + merge.colSpan);
            if (!rowsIn.length || !colsIn.length) return;
            rowsIn.forEach((row) => colsIn.forEach((col) => map.set(`${row}:${col}`, 'covered')));
            map.set(`${rowsIn[0]}:${colsIn[0]}`, {
                rowSpan: rowsIn.length,
                colSpan: colsIn.length,
                lastRow: rowsIn[rowsIn.length - 1],
                lastCol: colsIn[colsIn.length - 1],
                source: [merge.row, merge.col],
                width: colsIn.reduce((sum, col) => sum + widthOf(col), 0),
            });
        });
        return map;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sheet, numberWidths]);
    if (sheet.note) return <CenterState><span>{sheet.note}</span></CenterState>;
    if (!visibleRows.length || !visibleCols.length) return <CenterState><span>Лист пустой.</span></CenterState>;

    const tableWidth = ROW_HEADER_PX + visibleCols.reduce((sum, col) => sum + widthOf(col), 0);
    const shownRows = visibleRows.slice(0, limit);
    const hidden = visibleRows.length - shownRows.length;
    const emptyAt = (row, col) => {
        if (mergeInfo.has(`${row}:${col}`)) return false;
        const cell = sheet.rows[row]?.[col];
        return !cell || cell.text === '';
    };

    const renderCell = (row, col, position) => {
        const key = `${row}:${col}`;
        const merge = mergeInfo.get(key);
        if (merge === 'covered') return null;
        const [srcRow, srcCol] = merge ? merge.source : [row, col];
        const cell = sheet.rows[srcRow]?.[srcCol];
        const look = cell?.look || {};
        const edges = sheet.borders?.get(key) || {};
        const right = merge ? sheet.borders?.get(`${row}:${merge.lastCol}`)?.right : edges.right;
        const bottom = merge ? sheet.borders?.get(`${merge.lastRow}:${col}`)?.bottom : edges.bottom;
        const decoration = [look.underline && 'underline', look.strike && 'line-through'].filter(Boolean).join(' ');
        const style = {
            background: look.bg || undefined,
            color: look.color || undefined,
            fontWeight: look.bold ? 700 : undefined,
            fontStyle: look.italic ? 'italic' : undefined,
            textDecoration: decoration || undefined,
            fontSize: look.size ? `${Math.round(13 * look.size * 10) / 10}px` : undefined,
            textAlign: look.align || undefined,
            verticalAlign: look.vAlign || 'bottom',
            borderRight: right?.css,
            borderBottom: bottom?.css,
            borderLeft: edges.left?.css,
            borderTop: edges.top?.css,
            paddingLeft: look.indent ? 6 + look.indent * 9 : undefined,
            backgroundImage: look.bar
                ? `linear-gradient(90deg, ${hexAlpha(look.bar.color, 0.5)} ${Math.round(look.bar.share * 100)}%, transparent ${Math.round(look.bar.share * 100)}%)`
                : undefined,
        };
        /* Текст длиннее ячейки переливается в пустые ячейки справа, как в Excel,
           и обрезается на первой занятой. */
        let content = cell?.text ?? '';
        let spill = false;
        if (content && !merge && !look.wrap && typeof cell.v === 'string' && (!look.align || look.align === 'left')) {
            let width = widthOf(col);
            let next = position + 1;
            for (; next < Math.min(visibleCols.length, position + 1 + SPILL_MAX_COLS); next += 1) {
                if (!emptyAt(row, visibleCols[next])) break;
                width += widthOf(visibleCols[next]);
            }
            // Справа от таблицы — пустые столбцы листа: текст идёт и туда, как в Excel.
            if (next === visibleCols.length) width += SPILL_BEYOND_PX;
            if (width > widthOf(col)) {
                spill = true;
                content = <span className="tfv-spill" style={{ width: width - 12 }}>{content}</span>;
            }
        }
        return (
            <td
                key={col}
                className={[look.wrap ? 'is-wrap' : '', spill ? 'is-spill' : ''].filter(Boolean).join(' ') || undefined}
                style={style}
                colSpan={merge?.colSpan > 1 ? merge.colSpan : undefined}
                rowSpan={merge?.rowSpan > 1 ? merge.rowSpan : undefined}
            >
                {content}
            </td>
        );
    };

    return (
        <div className="tfv-grid-wrap is-sheet thin-scroll">
            <table className={`tfv-grid is-sheet${sheet.gridLines === false ? ' no-grid' : ''}`} style={{ width: tableWidth }}>
                <colgroup>
                    <col style={{ width: ROW_HEADER_PX }} />
                    {visibleCols.map((col) => <col key={col} style={{ width: widthOf(col) }} />)}
                </colgroup>
                <thead>
                    <tr>
                        <th className="tfv-corner" aria-hidden="true" />
                        {visibleCols.map((col) => <th key={col} scope="col">{columnName(col)}</th>)}
                    </tr>
                </thead>
                <tbody>
                    {shownRows.map((row) => (
                        <tr key={row} style={{ height: sheet.rowHeights?.[row] || sheet.defaultRowHeight || undefined }}>
                            <th scope="row">{row + 1}</th>
                            {visibleCols.map((col, position) => renderCell(row, col, position))}
                        </tr>
                    ))}
                </tbody>
            </table>
            {(hidden > 0 || sheet.truncatedRows || sheet.truncatedCols) && (
                <div className="tfv-grid-foot">
                    {hidden > 0 && (
                        <button type="button" className={iosBtnSecondary} onClick={() => setLimit((value) => value + ROWS_STEP * 3)}>
                            Показать ещё {Math.min(hidden, ROWS_STEP * 3)} {plural(Math.min(hidden, ROWS_STEP * 3), 'строку', 'строки', 'строк')}
                        </button>
                    )}
                    {hidden <= 0 && sheet.truncatedRows && (
                        <span>Показаны первые {sheet.rowCount} строк{sheet.totalRows > sheet.rowCount ? ` из ${sheet.totalRows}` : ''} — весь файл в «Скачать».</span>
                    )}
                    {hidden <= 0 && !sheet.truncatedRows && sheet.truncatedCols && <span>Показаны первые {sheet.colCount} столбцов — весь файл в «Скачать».</span>}
                </div>
            )}
        </div>
    );
}

function SheetsView({ sheets, active, onActive }) {
    const sheet = sheets[active] || sheets[0];
    return (
        <div className="tfv-sheets">
            {sheets.length > 1 && (
                <div className="tfv-tabs thin-scroll">
                    <IosSegmented
                        size="xs"
                        slide
                        ariaLabel="Листы книги"
                        value={active}
                        onChange={onActive}
                        options={sheets.map((item, index) => ({ value: index, label: item.name || `Лист ${index + 1}` }))}
                    />
                </div>
            )}
            {sheet.styled ? <StyledSheetTable key={active} sheet={sheet} /> : <SheetTable key={active} sheet={sheet} />}
        </div>
    );
}

/* Макет HTML. Страница живёт в клетке (htmlMockup.js); здесь — то, что
   делает окно: показывает её на всю ширину или в ширину телефона, держит
   исходник под рукой («Код» не снимает iframe — прототип не теряет открытую
   вкладку и прокрутку) и сторожит уход со страницы: ушла (скрипт сменил
   адрес) — макет загружается заново; ушла снова — без скриптов. */
function HtmlView({ preview, mode, scriptsOff, onScriptsOff, onEscape, onNotice, restoreFocus }) {
    const frame = useRef(null);
    const watch = useRef(null);
    if (!watch.current) watch.current = createLeaveWatch();
    const [frameKey, setFrameKey] = useState(0);
    const latest = useRef({ onEscape, onNotice, onScriptsOff, scriptsOff, restoreFocus });
    latest.current = { onEscape, onNotice, onScriptsOff, scriptsOff, restoreFocus };

    useEffect(() => {
        const onMessage = (event) => {
            if (acceptMockupEscape(event, frame.current, document.activeElement)) latest.current.onEscape?.();
        };
        window.addEventListener('message', onMessage);
        return () => window.removeEventListener('message', onMessage);
    }, []);

    /* Без скриптов сторожа в макете нет, и Esc из него до окна не дойдёт:
       фокус, ушедший в такой макет щелчком, возвращается окну. Прокрутка
       колесом работает и без фокуса. */
    useEffect(() => {
        if (!scriptsOff) return undefined;
        const onBlur = () => setTimeout(() => {
            if (document.activeElement === frame.current) latest.current.restoreFocus?.();
        }, 0);
        window.addEventListener('blur', onBlur);
        return () => window.removeEventListener('blur', onBlur);
    }, [scriptsOff]);

    const onLoad = (event) => {
        const verdict = watch.current.load(event.currentTarget);
        if (verdict === 'shown') return;
        const { scriptsOff: alreadyOff, onScriptsOff: turnOff, onNotice: notify } = latest.current;
        if (verdict === 'returned') notify?.('Макет открывал другую страницу — он показан снова.');
        else if (!alreadyOff) {
            turnOff?.();
            notify?.('Макет снова уходил со страницы — скрипты выключены.');
        }
        setFrameKey((value) => value + 1);
    };

    return (
        <div className="tfv-html">
            <div className={`tfv-html-stage${mode === 'phone' ? ' is-phone' : ''}`} hidden={mode === 'code'}>
                <iframe
                    key={frameKey}
                    ref={frame}
                    title="Макет страницы"
                    srcDoc={preview.srcDoc}
                    sandbox={scriptsOff ? '' : MOCKUP_SANDBOX}
                    credentialless=""
                    referrerPolicy="no-referrer"
                    allow=""
                    className="tfv-html-frame"
                    onLoad={onLoad}
                />
            </div>
            {mode === 'code' && <TextView text={preview.text} mono />}
        </div>
    );
}

function ImageView({ url, zoom, onFail }) {
    const box = useRef(null);
    const [natural, setNatural] = useState(null);
    const [fit, setFit] = useState(null);
    useEffect(() => {
        const element = box.current;
        if (!element || !natural) return undefined;
        const measure = () => {
            const ratio = Math.min(1, (element.clientWidth - 32) / natural.width, (element.clientHeight - 32) / natural.height);
            setFit(ratio > 0 ? { width: natural.width * ratio, height: natural.height * ratio } : null);
        };
        measure();
        if (typeof ResizeObserver !== 'function') return undefined;
        const observer = new ResizeObserver(measure);
        observer.observe(element);
        return () => observer.disconnect();
    }, [natural]);
    return (
        <div ref={box} className="tfv-image thin-scroll">
            <img
                src={url}
                alt=""
                draggable={false}
                onLoad={(event) => {
                    const { naturalWidth, naturalHeight } = event.currentTarget;
                    if (naturalWidth && naturalHeight) setNatural({ width: naturalWidth, height: naturalHeight });
                }}
                onError={onFail}
                style={fit ? { width: fit.width * zoom, height: fit.height * zoom } : { maxWidth: '100%', maxHeight: '100%' }}
            />
        </div>
    );
}

/* Список файлов блока — как боковая панель «Просмотра»: видно, что ещё
   приложено, и открыть любой одним щелчком. */
function FileSidebar({ files, active, onSelect, cacheKey }) {
    const activeRef = useRef(null);
    useEffect(() => {
        activeRef.current?.scrollIntoView?.({ block: 'nearest' });
    }, [active]);
    return (
        <nav className="tfv-side thin-scroll" aria-label="Файлы">
            <p className="tfv-side-title">{files.length} {plural(files.length, 'файл', 'файла', 'файлов')}</p>
            <ul>
                {files.map((item, position) => {
                    const info = previewKind(item);
                    const ext = info.ext || fileExtension(item?.file_name);
                    const current = position === active;
                    return (
                        <li key={cacheKey(item)}>
                            <button
                                type="button"
                                ref={current ? activeRef : undefined}
                                className={`tfv-side-item${current ? ' is-active' : ''}`}
                                aria-current={current ? 'true' : undefined}
                                title={item?.file_name}
                                onClick={() => onSelect(position)}
                            >
                                <TypeBadge ext={ext} kind={info.kind} />
                                <span className="tfv-side-text">
                                    <span className="tfv-side-name">{item?.file_name}</span>
                                    <span className="tfv-side-meta">
                                        {[fileTypeLabel(info.kind, ext), formatFileSize(item?.file_size)].filter(Boolean).join(' · ')}
                                    </span>
                                </span>
                            </button>
                        </li>
                    );
                })}
            </ul>
        </nav>
    );
}

function ZoomPill({ zoom, onZoom, page, pages }) {
    const at = ZOOM_STEPS.indexOf(zoom);
    return (
        <div className="tfv-pill" role="group" aria-label="Масштаб">
            <button type="button" aria-label="Уменьшить" title="Уменьшить (⌘−)" disabled={at <= 0} onClick={() => onZoom(ZOOM_STEPS[at - 1])}>
                <Minus size={15} strokeWidth={2.2} />
            </button>
            <button type="button" className="tfv-pill-value" title="Вписать (⌘0)" onClick={() => onZoom(1)}>{Math.round(zoom * 100)}%</button>
            <button type="button" aria-label="Увеличить" title="Увеличить (⌘+)" disabled={at >= ZOOM_STEPS.length - 1} onClick={() => onZoom(ZOOM_STEPS[at + 1])}>
                <Plus size={15} strokeWidth={2.2} />
            </button>
            {pages > 1 && (
                <>
                    <span className="tfv-pill-sep" aria-hidden="true" />
                    <span className="tfv-pill-page">{page} из {pages}</span>
                </>
            )}
        </div>
    );
}

/* ── Окно ── */

export default function FilePreviewSheet({
    files = [], index = -1, onIndexChange, onClose, loadFile, cacheKey = (file) => `file:${file?.id}`,
}) {
    const open = index >= 0 && index < files.length;
    const current = open ? files[index] : null;
    /* Пока просмотр уходит (затухание, на телефоне — уезжающий экран), в нём
       остаётся последний файл со своим списком: иначе гасло бы пустое окно.
       Освобождаем его после ухода. */
    const [kept, setKept] = useState(null);
    const currentKey = current ? cacheKey(current) : '';
    const latestState = useRef({ current, files });
    latestState.current = { current, files };
    useEffect(() => {
        if (currentKey) {
            setKept({ file: latestState.current.current, index, files: latestState.current.files });
            return undefined;
        }
        const timer = setTimeout(() => setKept(null), 320);
        return () => clearTimeout(timer);
    }, [currentKey, index, files.length]);
    const file = current || kept?.file || null;
    const shownIndex = open ? index : kept?.index ?? 0;
    const shownFiles = open ? files : kept?.files || [];
    const shownCount = shownFiles.length;
    const fileKey = file ? cacheKey(file) : '';
    const leaving = !open && Boolean(file);

    const isNarrow = useIsMobileShell();
    const [view, setView] = useState({ key: '', status: 'idle' });
    const [retry, setRetry] = useState(0);
    const [zoom, setZoom] = useState(1);
    const [page, setPage] = useState(1);
    const [sheetIndex, setSheetIndex] = useState(0);
    const [htmlMode, setHtmlMode] = useState('desktop');
    const [htmlScriptsOff, setHtmlScriptsOff] = useState(false);
    const [copyState, setCopyState] = useState('idle');
    const [notice, setNotice] = useState('');
    const [sidebarPref, setSidebarPref] = useState(readSidebarPref);
    const latest = useRef({});
    latest.current = { loadFile, file, onClose, onIndexChange, files, index };

    useEffect(() => {
        if (!fileKey) return undefined;
        const target = latest.current.file;
        const controller = new AbortController();
        let cancelled = false;
        let built = null;
        setView({ key: fileKey, status: 'loading' });
        setZoom(1);
        setPage(1);
        setSheetIndex(0);
        setHtmlMode('desktop');
        setHtmlScriptsOff(false);
        setCopyState('idle');
        setNotice('');
        (async () => {
            const info = previewKind(target);
            if (info.kind === 'legacy' || info.kind === 'archive') {
                built = { kind: 'unsupported', typeKind: info.kind, reason: unsupportedReason(info.kind, info.ext) };
            } else {
                let blob = previewCache.get(fileKey);
                if (!blob) {
                    blob = await latest.current.loadFile(target, controller.signal);
                    if (cancelled) return;
                    previewCache.put(fileKey, blob);
                }
                built = await buildPreview(target, blob);
            }
            if (cancelled) {
                built?.dispose?.();
                return;
            }
            setView({ key: fileKey, status: 'ready', preview: built });
            if (built.active) setSheetIndex(built.active);
        })().catch(async (error) => {
            if (cancelled) return;
            const message = await describeError(error);
            if (!cancelled) setView({ key: fileKey, status: 'error', message });
        });
        return () => {
            cancelled = true;
            controller.abort();
            built?.dispose?.();
        };
    }, [fileKey, retry]);

    const preview = view.key === fileKey && view.status === 'ready' ? view.preview : null;
    const kind = preview?.kind || '';

    const close = useCallback(() => latest.current.onClose?.(), []);
    const select = useCallback((next) => {
        const { files: list, onIndexChange: change } = latest.current;
        if (next >= 0 && next < list.length) change?.(next);
    }, []);
    const step = useCallback((delta) => select(latest.current.index + delta), [select]);

    // На телефоне просмотр — экран: системный жест «назад» закрывает его, а не раздел под ним.
    useScreenBackGesture(isNarrow && open, close);

    const zoomable = kind === 'pdf' || kind === 'image';
    const changeZoom = useCallback((direction) => {
        setZoom((value) => {
            if (direction === 0) return 1;
            const at = ZOOM_STEPS.indexOf(value);
            return ZOOM_STEPS[Math.max(0, Math.min(ZOOM_STEPS.length - 1, at + direction))] ?? 1;
        });
    }, []);

    /* Клавиши — в фазе захвата: Esc закрывает просмотр, а не карточку задачи
       под ним; ← → листают файлы (↑ ↓ — тоже, пока фокус в списке слева);
       ⌘+ ⌘− ⌘0 — масштаб PDF и картинки, а не всей страницы портала. */
    useEffect(() => {
        if (!open) return undefined;
        const onKey = (event) => {
            const typing = event.target?.closest?.('input, textarea, select, [contenteditable="true"], video, audio');
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopImmediatePropagation();
                close();
                return;
            }
            const plain = !event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey;
            const inList = Boolean(event.target?.closest?.('.tfv-side'));
            const delta = event.key === 'ArrowLeft' || (inList && event.key === 'ArrowUp') ? -1
                : event.key === 'ArrowRight' || (inList && event.key === 'ArrowDown') ? 1 : 0;
            if (delta && plain && !typing && latest.current.files.length > 1) {
                event.preventDefault();
                event.stopImmediatePropagation();
                step(delta);
                return;
            }
            if (zoomable && (event.metaKey || event.ctrlKey) && ['=', '+', '-', '0'].includes(event.key)) {
                event.preventDefault();
                event.stopImmediatePropagation();
                changeZoom(event.key === '0' ? 0 : event.key === '-' ? -1 : 1);
            }
        };
        window.addEventListener('keydown', onKey, true);
        return () => window.removeEventListener('keydown', onKey, true);
    }, [open, close, step, zoomable, changeZoom]);

    // Фокус — в просмотр, чтобы клавиши не уходили карточке под ним; после закрытия — обратно.
    const rootRef = useRef(null);
    const focusRoot = useCallback(() => rootRef.current?.focus({ preventScroll: true }), []);
    useEffect(() => {
        if (!open) return undefined;
        const opener = document.activeElement;
        focusRoot();
        return () => {
            if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus({ preventScroll: true });
        };
    }, [open, focusRoot]);

    const noticeTimer = useRef(0);
    const showNotice = useCallback((text) => {
        setNotice(text);
        clearTimeout(noticeTimer.current);
        noticeTimer.current = setTimeout(() => setNotice(''), 2800);
    }, []);
    useEffect(() => () => clearTimeout(noticeTimer.current), []);

    const copyTimer = useRef(0);
    useEffect(() => () => clearTimeout(copyTimer.current), []);
    const canCopy = Boolean(preview) && (
        kind === 'pdf' || kind === 'sheets'
        || (['text', 'markdown', 'docx', 'pptx', 'html'].includes(kind) && Boolean(preview.text?.trim()))
    );
    const copy = async () => {
        if (!preview || copyState === 'working') return;
        const key = fileKey;
        let text = '';
        if (kind === 'pdf') {
            setCopyState('working');
            const { pdfPageText } = await import('../wazzup/chatAttachments');
            text = await pdfDocumentText(preview.pdf, pdfPageText).catch(() => '');
        } else if (kind === 'sheets') {
            text = preview.text ?? preview.sheetText?.(sheetIndex) ?? '';
        } else {
            text = preview.text || '';
        }
        if (latest.current.file && cacheKey(latest.current.file) !== key) return;
        if (!text.trim()) {
            setCopyState('idle');
            showNotice(kind === 'pdf' ? 'В PDF нет текста — похоже, это скан.' : 'Копировать нечего — файл пустой.');
            return;
        }
        const ok = await writeClipboard(text);
        if (!ok) {
            setCopyState('idle');
            showNotice('Не удалось скопировать. Выделите текст и нажмите ⌘C.');
            return;
        }
        setCopyState('done');
        clearTimeout(copyTimer.current);
        copyTimer.current = setTimeout(() => setCopyState('idle'), 1600);
    };

    const [saving, setSaving] = useState(false);
    const download = async () => {
        if (!file || saving) return;
        const name = file.file_name || file.name || `file-${file.id}`;
        let blob = previewCache.get(fileKey);
        if (!blob) {
            setSaving(true);
            try {
                blob = await loadFile(file);
                previewCache.put(fileKey, blob);
            } catch (error) {
                showNotice(await describeError(error));
                return;
            } finally {
                setSaving(false);
            }
        }
        saveBlobAs(blob, name);
    };

    const info = file ? previewKind(file) : { kind: '', ext: '' };
    const ext = info.ext || fileExtension(file?.file_name);
    // Подпись типа — по тому, что файл есть (JSON, CSV), а не по тому, чем окно его рисует (текст, таблица).
    const shownKind = !preview ? info.kind
        : preview.kind === 'sheets' ? (info.kind === 'csv' ? 'csv' : 'xlsx')
            : preview.kind === 'text' && info.kind === 'json' ? 'json'
                : preview.typeKind || preview.kind;
    const extra = preview?.kind === 'pdf' ? `${preview.pdf.numPages} ${plural(preview.pdf.numPages, 'страница', 'страницы', 'страниц')}`
        : preview?.kind === 'pptx' ? `${preview.slides.length} ${plural(preview.slides.length, 'слайд', 'слайда', 'слайдов')}`
            : preview?.kind === 'sheets' && preview.sheets.length > 1 ? `${preview.sheets.length} ${plural(preview.sheets.length, 'лист', 'листа', 'листов')}`
                : '';
    const subtitle = [fileTypeLabel(shownKind, ext), formatFileSize(file?.file_size), extra]
        .filter(Boolean).join(' · ');

    const canSidebar = !isNarrow && shownCount > 1;
    const sidebarShown = canSidebar && sidebarPref;
    const toggleSidebar = () => setSidebarPref((value) => {
        writeSidebarPref(!value);
        return !value;
    });

    const copyLabel = copyState === 'done' ? 'Скопировано'
        : kind === 'sheets' ? 'Скопировать таблицу' : kind === 'html' ? 'Скопировать код' : 'Скопировать текст';

    const htmlShownMode = shownHtmlMode(htmlMode, isNarrow);
    const htmlControls = kind === 'html' ? (
        <>
            <IosSegmented
                size="xs"
                slide
                ariaLabel="Как показать файл"
                value={htmlShownMode}
                onChange={setHtmlMode}
                options={htmlViewModes(isNarrow).map((mode) => ({
                    desktop: { value: 'desktop', label: isNarrow ? 'Макет' : 'Компьютер', icon: isNarrow ? null : <Monitor size={13} strokeWidth={2} /> },
                    phone: { value: 'phone', label: 'Телефон', icon: <Smartphone size={13} strokeWidth={2} /> },
                    code: { value: 'code', label: 'Код', icon: isNarrow ? null : <Code2 size={13} strokeWidth={2} /> },
                }[mode]))}
            />
            {htmlScriptsOff && <span className="tfv-tabs-note">Скрипты макета выключены</span>}
        </>
    ) : null;

    let body;
    if (!file || view.key !== fileKey || view.status === 'loading' || view.status === 'idle') {
        body = <LoadingState />;
    } else if (view.status === 'error') {
        body = (
            <CenterState>
                <FileGlyph ext={ext} kind={info.kind} />
                <p className="tfv-state-title">{file.file_name}</p>
                <p className="tfv-state-text">{view.message}</p>
                <div className="tfv-state-actions">
                    <button type="button" className={iosBtnSecondary} onClick={() => setRetry((value) => value + 1)}>
                        <RotateCw size={14} /> Повторить
                    </button>
                </div>
            </CenterState>
        );
    } else if (kind === 'unsupported') {
        body = (
            <CenterState>
                <FileGlyph ext={ext} kind={preview.typeKind} />
                <p className="tfv-state-title">{file.file_name}</p>
                <p className="tfv-state-text">{preview.reason}</p>
                <div className="tfv-state-actions">
                    <button type="button" className={iosBtnPrimary} onClick={download} disabled={saving}>
                        <Download size={15} /> Скачать файл
                    </button>
                </div>
            </CenterState>
        );
    } else if (kind === 'pdf') {
        body = <PdfPreview key={fileKey} pdf={preview.pdf} library={preview.library} zoom={zoom} onPageChange={setPage} />;
    } else if (kind === 'image') {
        body = (
            <ImageView key={fileKey} url={preview.url} zoom={zoom}
                onFail={() => setView({ key: fileKey, status: 'error', message: 'Браузер не умеет показывать этот формат изображения — скачайте файл.' })} />
        );
    } else if (kind === 'video') {
        body = (
            <div className="tfv-media">
                {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
                <video key={fileKey} src={preview.url} controls playsInline preload="metadata" />
            </div>
        );
    } else if (kind === 'audio') {
        body = (
            <CenterState>
                <FileGlyph ext={ext} kind="audio" />
                <p className="tfv-state-title">{file.file_name}</p>
                {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
                <audio key={fileKey} src={preview.url} controls preload="metadata" className="tfv-audio" />
            </CenterState>
        );
    } else if (kind === 'sheets') {
        body = <SheetsView key={fileKey} sheets={preview.sheets} active={sheetIndex} onActive={setSheetIndex} />;
    } else if (kind === 'docx') {
        body = <DocxView key={fileKey} blocks={preview.blocks} media={preview.media} />;
    } else if (kind === 'pptx') {
        body = <PptxView key={fileKey} slides={preview.slides} media={preview.media} />;
    } else if (kind === 'html') {
        body = (
            <HtmlView key={fileKey} preview={preview} mode={htmlShownMode} scriptsOff={htmlScriptsOff}
                onScriptsOff={() => setHtmlScriptsOff(true)} onEscape={close} onNotice={showNotice} restoreFocus={focusRoot} />
        );
    } else if (kind === 'markdown') {
        body = <MarkdownView key={fileKey} html={preview.html} />;
    } else {
        body = <TextView key={fileKey} text={preview.text} mono={preview.mono} />;
    }

    if (typeof document === 'undefined') return null;
    return createPortal(
        <div className="tfv-layer">
            {file && (
                /* otp-modal-root — метка «экран поверх всего» для мобильной оболочки:
                   на телефоне просмотр въезжает и уезжает экраном, колокол прячется,
                   жест «назад» знает о нём (mobile-shell.css, mobileBackStack.js). */
                <div
                    ref={rootRef}
                    tabIndex={-1}
                    role="dialog"
                    aria-modal="true"
                    aria-label={file.file_name || 'Просмотр файла'}
                    className={`tfv-screen otp-modal-root${leaving ? ' is-leaving' : ''}${sidebarShown ? ' has-side' : ''}`}
                    style={{ fontFamily: APPLE_FONT }}
                >
                    <header className="tfv-bar">
                        <div className="tfv-bar-lead">
                            {isNarrow ? (
                                <button type="button" className="tfv-icon-btn is-back" onClick={close} aria-label="Назад">
                                    <ChevronLeft size={24} strokeWidth={2.2} />
                                </button>
                            ) : canSidebar ? (
                                <button type="button" className={`tfv-icon-btn${sidebarShown ? ' is-on' : ''}`} onClick={toggleSidebar}
                                    aria-pressed={sidebarShown} aria-label="Список файлов" title={sidebarShown ? 'Скрыть список файлов' : 'Показать список файлов'}>
                                    <PanelLeft size={18} strokeWidth={1.9} />
                                </button>
                            ) : null}
                            <TypeBadge ext={ext} kind={shownKind === 'markdown' || shownKind === 'json' || shownKind === 'text' ? 'text' : shownKind} />
                            <div className="tfv-bar-title">
                                <h2 title={file.file_name}>{file.file_name}</h2>
                                {subtitle && <p>{subtitle}</p>}
                            </div>
                        </div>
                        {htmlControls && !isNarrow && <div className="tfv-bar-center">{htmlControls}</div>}
                        <div className="tfv-bar-actions">
                            {shownCount > 1 && (
                                <div className="tfv-nav" role="group" aria-label="Другие файлы">
                                    <button type="button" className="tfv-icon-btn" disabled={shownIndex <= 0} onClick={() => step(-1)}
                                        aria-label="Предыдущий файл" title="Предыдущий файл (←)">
                                        <ChevronLeft size={18} strokeWidth={2.1} />
                                    </button>
                                    {!isNarrow && <span className="tfv-nav-count">{shownIndex + 1} из {shownCount}</span>}
                                    <button type="button" className="tfv-icon-btn" disabled={shownIndex >= shownCount - 1} onClick={() => step(1)}
                                        aria-label="Следующий файл" title="Следующий файл (→)">
                                        <ChevronRight size={18} strokeWidth={2.1} />
                                    </button>
                                </div>
                            )}
                            {canCopy && (
                                <button type="button" className="tfv-icon-btn" onClick={copy} disabled={copyState === 'working'}
                                    aria-label={copyLabel} title={copyLabel}>
                                    {copyState === 'working' ? <Loader2 size={17} className="animate-spin" />
                                        : copyState === 'done' ? <Check size={18} strokeWidth={2.4} className="tfv-done" />
                                            : <Copy size={17} strokeWidth={1.9} />}
                                </button>
                            )}
                            <button type="button" className="tfv-icon-btn" onClick={download} disabled={saving} aria-label="Скачать файл" title="Скачать">
                                {saving ? <Loader2 size={17} className="animate-spin" /> : <Download size={18} strokeWidth={1.9} />}
                            </button>
                            {!isNarrow && (
                                <>
                                    <span className="tfv-bar-sep" aria-hidden="true" />
                                    <button type="button" className="tfv-icon-btn is-close" onClick={close} aria-label="Закрыть" title="Закрыть (Esc)">
                                        <X size={18} strokeWidth={2} />
                                    </button>
                                </>
                            )}
                        </div>
                    </header>
                    {sidebarShown && <FileSidebar files={shownFiles} active={shownIndex} onSelect={select} cacheKey={cacheKey} />}
                    <main className="tfv-main">
                        {htmlControls && isNarrow && <div className="tfv-tabs">{htmlControls}</div>}
                        {body}
                        {zoomable && preview && (
                            <ZoomPill zoom={zoom} onZoom={setZoom} page={page} pages={kind === 'pdf' ? preview.pdf.numPages : 0} />
                        )}
                        {notice && <div className="tfv-toast" role="status">{notice}</div>}
                    </main>
                </div>
            )}
        </div>,
        document.body,
    );
}
