/* Инструменты таблицы в редакторе статьи — расширение TipTap.
 *
 *  * ручки строк и столбцов у той таблицы, в которой стоит курсор (виджеты
 *    ProseMirror внутри ячеек — см. tableGrips в tableTools.js);
 *  * класс «активной» таблицы и вида выделения на её обёртке — по нему
 *    стиль показывает «+» и подсвечивает выбранные ручки;
 *  * высота строк: граница строки тянется мышью, как граница столбца;
 *  * заморозка ширин столбцов, когда автор берётся за границу столбца;
 *  * вставка текста с табуляциями таблицей и одной ячейки из Excel — текстом;
 *  * сторож замороженных ширин (guardFixedWidths).
 *
 * Приоритет выше, чем у таблицы (100 по умолчанию): обработчик нажатия мыши
 * обязан сработать РАНЬШЕ плагина ширины столбцов — тот на этом же нажатии
 * начинает перетаскивание и читает ширины, которые мы только что заморозили.
 */
import { Extension } from '@tiptap/react';
import { Table, TableRow } from '@tiptap/extension-table';
import { Fragment, Slice } from '@tiptap/pm/model';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { CellSelection, columnResizingPluginKey } from '@tiptap/pm/tables';
import {
    ROW_MIN_HEIGHT, activeTable, cellsEndingAtRow, freezeColumnWidths, gripSelection,
    guardFixedWidths, parseTabular, rowAtCellEdge, rowPosition, selectionShape, tableAround,
    tableAt, tableFromRows, tableGrips, unwrapSpreadsheetCell,
} from './tableTools.js';

export const wikiTableToolsKey = new PluginKey('wikiTableTools');

/* Таблица с признаком «ширины столбцов задал автор» (fixedLayout). Признак
   ставит перетаскивание границы столбца и снимает «Сбросить размеры»; в
   статью он уезжает классом wiki-table-fixed (санитайзер классы
   пропускает), и только такая таблица раскладывается по заданным ширинам
   точно — у автора и у читателя (wiki-theme.css). */
export const WikiTable = Table.extend({
    addAttributes() {
        return {
            ...this.parent?.(),
            fixedLayout: {
                default: false,
                parseHTML: (element) => element.classList?.contains('wiki-table-fixed') || false,
                renderHTML: (attributes) => (attributes.fixedLayout ? { class: 'wiki-table-fixed' } : {}),
            },
            /* Положение таблицы по ширине статьи: null — у левого края (как
               всегда было), center, right. Классом — по той же причине, что и
               fixedLayout: санитайзер классы пропускает. */
            align: {
                default: null,
                parseHTML: (element) => {
                    if (element.classList?.contains('wiki-table-center')) return 'center';
                    if (element.classList?.contains('wiki-table-right')) return 'right';
                    return null;
                },
                renderHTML: (attributes) => (attributes.align === 'center' || attributes.align === 'right'
                    ? { class: `wiki-table-${attributes.align}` } : {}),
            },
        };
    },
});

/* Строка таблицы с высотой. Высота — атрибут самой строки и уезжает в
   статью как <tr style="height: 64px">: санитайзер height в style
   пропускает, витрина рисует ту же высоту. Для таблицы это нижняя граница —
   строка с длинным текстом всё равно вырастет под него. */
export const WikiTableRow = TableRow.extend({
    addAttributes() {
        return {
            ...this.parent?.(),
            height: {
                default: null,
                parseHTML: (element) => {
                    const value = parseInt(element.style?.height || '', 10);
                    return Number.isFinite(value) && value > 0 ? value : null;
                },
                renderHTML: (attributes) => (attributes.height
                    ? { style: `height: ${attributes.height}px` } : {}),
            },
        };
    },
});

/* Выделение ячеек сделано ручкой строки, столбца или угла (а не мышью). */
export const gripSelected = (state) => {
    const sel = state.selection;
    const grip = wikiTableToolsKey.getState(state)?.grip;
    return Boolean(grip && sel instanceof CellSelection
        && sel.$anchorCell.pos === grip.anchor && sel.$headCell.pos === grip.head);
};

/* Кто открывает меню ручек в этом редакторе. Угловая ручка живёт в виде узла
   таблицы (WikiTableView.js), а вид узла создаёт плагин ширины столбцов —
   опций расширения он не видит, поэтому путь к меню лежит здесь, по view. */
const menuOpeners = new WeakMap();

/* Ручка выделяет то, что ей принадлежит, и открывает меню. Куда вернуть
   курсор, если меню закроют, ничего не выбрав, помнит состояние плагина
   (before) — оно же сдвигает эту позицию вместе с правками. */
const selectAndOpen = (view, found, grip, anchor) => {
    const tr = view.state.tr.setSelection(gripSelection(view.state.doc, found, grip));
    tr.setMeta(wikiTableToolsKey, { grip: { kind: grip.kind, from: grip.from, to: grip.to } });
    view.dispatch(tr);
    view.focus();
    menuOpeners.get(view)?.({
        kind: grip.kind, from: grip.from, to: grip.to, tablePos: found.pos, anchor,
    });
};

/* Меню всей таблицы — от угловой ручки (WikiTableView.js). */
export const openTableMenu = (view, tablePos, anchor) => {
    const found = tableAt(view.state.doc, tablePos);
    if (found) selectAndOpen(view, found, { kind: 'table', from: 0, to: 0 }, anchor);
};

/* Меню ручки закрыли, ничего не выбрав: выделение ручкой снимается, курсор
   возвращается туда, где стоял до первого нажатия на ручку (а не на ручку,
   нажатую перед этой, — тогда «вернуть» значило бы выделить чужую строку).
   Иначе следующая же буква заменила бы собой всю выделенную строку: так
   ProseMirror поступает с выделением ячеек. */
export const restoreGripCursor = (view) => {
    if (!view || view.isDestroyed || !gripSelected(view.state)) return false;
    const before = wikiTableToolsKey.getState(view.state)?.before;
    if (!before) return false;
    let selection;
    try {
        selection = before.resolve(view.state.doc);
    } catch (e) {
        return false;
    }
    view.dispatch(view.state.tr.setSelection(selection));
    return true;
};

const GRIP_LABEL = {
    row: 'Строка: вставить, переместить, удалить',
    column: 'Столбец: вставить, переместить, удалить',
};

/* Три точки — как у ручек «Заметок». Шесть точек — у угловой ручки: ей
   таблицу ещё и перетаскивают, и вид должен отличаться от ручки строки. */
const dot = (x, y) => `<circle cx="${x}" cy="${y}" r="1.25"/>`;
const GRIP_ICON = {
    row: `<svg width="4" height="14" viewBox="0 0 4 14" fill="currentColor" aria-hidden="true">${dot(2, 3)}${dot(2, 7)}${dot(2, 11)}</svg>`,
    column: `<svg width="14" height="4" viewBox="0 0 14 4" fill="currentColor" aria-hidden="true">${dot(3, 2)}${dot(7, 2)}${dot(11, 2)}</svg>`,
};

/* Ручка — кнопка внутри ячейки. Её события ProseMirror не разбирает
   (stopEvent у виджета), поэтому нажатие не ставит курсор в ячейку, а
   mousedown с preventDefault не уводит фокус из редактора. */
const gripDom = (view, getPos, grip) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `wiki-table-grip wiki-table-grip--${grip.kind}`;
    btn.setAttribute('contenteditable', 'false');
    btn.tabIndex = -1;
    btn.title = GRIP_LABEL[grip.kind];
    btn.setAttribute('aria-label', GRIP_LABEL[grip.kind]);
    btn.setAttribute('aria-haspopup', 'menu');
    btn.innerHTML = GRIP_ICON[grip.kind];
    btn.addEventListener('mousedown', (event) => {
        event.preventDefault();
        event.stopPropagation();
    });
    btn.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        const pos = getPos();
        if (pos == null) return;
        const found = tableAround(view.state.doc.resolve(pos));
        if (found) selectAndOpen(view, found, grip, btn);
    });
    return btn;
};

/* ── Высота строк ─────────────────────────────────────────────────────── */

// Сколько пикселей у края ячейки считается «границей»: столько же, сколько у
// границы столбца в prosemirror-tables (handleWidth по умолчанию).
const EDGE = 5;

/* Граница строки под указателем: { tablePos, index } или null.
   Угол ячейки отдаём границе столбца — там prosemirror-tables тянет ширину,
   и две ручки разом в одной точке спорили бы за нажатие. */
const rowEdgeAt = (view, event) => {
    const target = event.target;
    if (!(target instanceof Element)) return null;
    if (target.closest('.wiki-table-grip, .wiki-table-handle, .wiki-table-add')) return null;
    const cellDom = target.closest('td, th');
    if (!cellDom || !view.dom.contains(cellDom)) return null;
    const rect = cellDom.getBoundingClientRect();
    if (event.clientX - rect.left <= EDGE || rect.right - event.clientX <= EDGE) return null;
    let edge = null;
    if (rect.bottom - event.clientY <= EDGE) edge = 'bottom';
    else if (event.clientY - rect.top <= EDGE) edge = 'top';
    if (!edge) return null;
    let cellPos;
    try {
        cellPos = view.posAtDOM(cellDom, 0) - 1;
    } catch (e) {
        return null;
    }
    const $cell = view.state.doc.resolve(cellPos);
    const table = $cell.node(-1);
    if (table?.type.spec.tableRole !== 'table') return null;
    const tableStart = $cell.start(-1);
    const index = rowAtCellEdge(table, cellPos - tableStart, edge);
    return index < 0 ? null : { tablePos: tableStart - 1, index };
};

const sameRow = (a, b) => (!a && !b) || Boolean(a && b && a.tablePos === b.tablePos && a.index === b.index);

/* Тянем границу строки. Пока тянем, высота живёт в декорации строки
   (пишется в документ один раз, на отпускании мыши), — как ширина столбца у
   prosemirror-tables. Декорация задаёт не height, а переменную и класс:
   снимая декорацию, ProseMirror вычищает из style строки её свойства, и
   height из декорации унёс бы с собой высоту, которую строка уже хранит.
   Короче текста строку не сделать: если на отпускании строка выше заданного,
   её держит текст, и высота снимается вовсе («по содержимому»). Щелчок без
   сдвига ничего не пишет. */
const startRowDrag = (view, event, hover) => {
    const table = view.state.doc.nodeAt(hover.tablePos);
    if (table?.type.spec.tableRole !== 'table' || hover.index >= table.childCount) return false;
    const rowDom = view.nodeDOM(rowPosition(table, hover.tablePos, hover.index));
    const startHeight = rowDom instanceof Element ? rowDom.getBoundingClientRect().height : 0;
    if (!startHeight) return false;
    const startY = event.clientY;
    const win = view.dom.ownerDocument.defaultView || window;
    let moved = false;
    view.dispatch(view.state.tr.setMeta(wikiTableToolsKey, {
        rowDrag: { tablePos: hover.tablePos, index: hover.index, height: startHeight },
    }));

    function finish() {
        win.removeEventListener('mousemove', move);
        win.removeEventListener('mouseup', finish);
        const drag = wikiTableToolsKey.getState(view.state)?.rowDrag;
        if (!drag) return;
        const tr = view.state.tr.setMeta(wikiTableToolsKey, { rowDrag: null, rowHover: null });
        const current = tr.doc.nodeAt(drag.tablePos);
        if (moved && current?.type.spec.tableRole === 'table' && drag.index < current.childCount) {
            const pos = rowPosition(current, drag.tablePos, drag.index);
            const dom = view.nodeDOM(pos);
            const rendered = dom instanceof Element ? dom.getBoundingClientRect().height : drag.height;
            const value = rendered > drag.height + 1 ? null : Math.round(drag.height);
            const row = current.child(drag.index);
            if ((row.attrs.height || null) !== value) {
                tr.setNodeMarkup(pos, null, { ...row.attrs, height: value });
            }
        }
        view.dispatch(tr);
    }

    function move(ev) {
        if (!ev.buttons) {
            finish();
            return;
        }
        const drag = wikiTableToolsKey.getState(view.state)?.rowDrag;
        if (!drag) return;
        if (Math.abs(ev.clientY - startY) >= 1) moved = true;
        const height = Math.max(ROW_MIN_HEIGHT, startHeight + ev.clientY - startY);
        view.dispatch(view.state.tr.setMeta(wikiTableToolsKey, { rowDrag: { ...drag, height } }));
    }

    win.addEventListener('mousemove', move);
    win.addEventListener('mouseup', finish);
    return true;
};

/* Граница столбца: ширины замораживаются на ПЕРВОМ сдвиге мыши, а не на
   нажатии — простой щелчок по границе не должен менять статью. Слушатель
   ставится раньше, чем свой поставит плагин ширины столбцов, поэтому на
   первом сдвиге заморозка успевает до того, как плагин сдвинет границу.
   click — запись «нажали на границу, ещё не сдвинули»: пока она жива,
   filterTransaction плагина отбрасывает запись ширины, которую
   prosemirror-tables делает на отпускании мыши даже без сдвига (столбец без
   ширины получал свою же ширину числом, и статья «менялась» от щелчка). */
const freezeOnFirstMove = (view, tablePos, event, clickState) => {
    const win = view.dom.ownerDocument.defaultView || window;
    const startX = event.clientX;
    const click = { tablePos, moved: false };
    clickState.current = click;
    const done = () => {
        win.removeEventListener('mousemove', move, true);
        win.removeEventListener('mouseup', done, true);
        // Плагин ширины пишет на своём mouseup — после нашего (мы в фазе
        // перехвата): запись снимаем, когда он отработает.
        win.setTimeout(() => {
            if (clickState.current === click) clickState.current = null;
        }, 0);
    };
    function move(ev) {
        if (Math.abs(ev.clientX - startX) < 1) return;
        click.moved = true;
        win.removeEventListener('mousemove', move, true);
        freezeColumnWidths(view, tablePos);
    }
    win.addEventListener('mousemove', move, true);
    win.addEventListener('mouseup', done, true);
};

const sameAttrs = (a, b, skip) => {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const key of keys) {
        if (key === skip) continue;
        const x = a[key];
        const y = b[key];
        if (x === y) continue;
        if (JSON.stringify(x) !== JSON.stringify(y)) return false;
    }
    return true;
};

/* Две версии таблицы различаются только шириной столбцов (colwidth). */
const widthOnlyChange = (before, after) => {
    if (!before || !after || before.type !== after.type || before.childCount !== after.childCount
        || !sameAttrs(before.attrs, after.attrs)) return false;
    for (let r = 0; r < before.childCount; r += 1) {
        const rowA = before.child(r);
        const rowB = after.child(r);
        if (rowA.type !== rowB.type || rowA.childCount !== rowB.childCount || !sameAttrs(rowA.attrs, rowB.attrs)) return false;
        for (let c = 0; c < rowA.childCount; c += 1) {
            const cellA = rowA.child(c);
            const cellB = rowB.child(c);
            if (cellA.type !== cellB.type || !cellA.content.eq(cellB.content)
                || !sameAttrs(cellA.attrs, cellB.attrs, 'colwidth')) return false;
        }
    }
    return true;
};

const rowHandleDom = () => {
    const line = document.createElement('div');
    line.className = 'wiki-row-resize-handle';
    line.setAttribute('contenteditable', 'false');
    return line;
};

/* ── Декорации ────────────────────────────────────────────────────────── */

const SHAPE_CLASS = { rows: 'rows', columns: 'columns', table: 'table' };

const buildDecorations = (state) => {
    const decorations = [];
    const found = activeTable(state);
    const tools = wikiTableToolsKey.getState(state);
    if (found) {
        /* Какую ручку подсветить: выделено ручкой — по ней (у строки с
           объединённой ячейкой с краю выделение не «целая строка», но ручка
           та же), выделено мышью ровно по строкам или столбцам — по форме. */
        const shape = selectionShape(state);
        const byGrip = gripSelected(state) ? { row: 'rows', column: 'columns', table: 'table' }[tools.grip.kind] : null;
        const kind = byGrip || (shape && SHAPE_CLASS[shape.kind]);
        decorations.push(Decoration.node(found.pos, found.pos + found.node.nodeSize, {
            class: `wiki-table--active${kind ? ` wiki-table--sel-${kind}` : ''}`,
        }));
        tableGrips(found).forEach((grip) => {
            decorations.push(Decoration.widget(
                grip.pos,
                (view, getPos) => gripDom(view, getPos, grip),
                {
                    // Ключ — всё, от чего зависит кнопка: с тем же ключом ProseMirror
                    // оставляет на экране прежний элемент, и открытое от него меню
                    // не теряет опору на каждом нажатии клавиши.
                    key: `wiki-grip:${grip.kind}:${grip.from}-${grip.to}`,
                    side: -1,
                    ignoreSelection: true,
                    stopEvent: () => true,
                },
            ));
        });
    }
    /* Граница строки под мышью (или та, что тянут): линия по низу каждой
       ячейки, чей нижний край — эта граница, и живая высота строки. */
    const row = tools?.rowDrag || tools?.rowHover;
    const table = row && state.doc.nodeAt(row.tablePos);
    if (table?.type.spec.tableRole === 'table' && row.index < table.childCount) {
        cellsEndingAtRow(table, row.index).forEach((rel) => {
            const cell = table.nodeAt(rel);
            decorations.push(Decoration.widget(
                row.tablePos + 1 + rel + cell.nodeSize - 1,
                rowHandleDom,
                { key: `wiki-row-edge:${row.index}`, side: 1, ignoreSelection: true },
            ));
        });
        if (tools.rowDrag) {
            const pos = rowPosition(table, row.tablePos, row.index);
            decorations.push(Decoration.node(pos, pos + table.child(row.index).nodeSize, {
                class: 'wiki-row-dragging',
                style: `--wiki-row-drag-height: ${Math.round(tools.rowDrag.height)}px`,
            }));
        }
    }
    return decorations.length ? DecorationSet.create(state.doc, decorations) : DecorationSet.empty;
};

export const WikiTableTools = Extension.create({
    name: 'wikiTableTools',

    priority: 200,

    addOptions() {
        return {
            // Нажата ручка: { kind, from, to, tablePos, anchor, before } — меню открывает редактор.
            onGrip: null,
        };
    },

    addProseMirrorPlugins() {
        const { options } = this;
        // Нажатие на границу столбца без сдвига (см. freezeOnFirstMove).
        const columnClick = { current: null };
        return [
            new Plugin({
                key: wikiTableToolsKey,
                filterTransaction: (tr, state) => {
                    const click = columnClick.current;
                    if (!click || click.moved || !tr.docChanged) return true;
                    if (tr.doc.content.size !== state.doc.content.size) return true;
                    const before = state.doc.nodeAt(click.tablePos);
                    const after = tr.doc.nodeAt(click.tablePos);
                    return !(before && after && before !== after && widthOnlyChange(before, after));
                },
                /* grip — что выделила ручка: { anchor, head } — позиции первой и
                   последней ячейки (сдвигаются правками), kind/from/to — какая
                   ручка. Пока выделение совпадает, панель «Объединить» не
                   всплывает: у выделенной ручкой строки есть своё меню. Запись
                   живёт и после того, как выделение сменилось курсором: ⌘Z
                   возвращает выделение строки, сделанное ручкой, и всплывшая
                   тогда над соседней строкой панель ловила щелчок, адресованный
                   ячейке, — строка склеивалась. Сбрасывается другим выделением
                   ячеек (мышью).
                   before — закладка курсора до первого нажатия на ручку: туда
                   его возвращает restoreGripCursor.
                   rowHover / rowDrag — граница строки под мышью и та, что тянут. */
                state: {
                    init: () => ({ grip: null, before: null, rowHover: null, rowDrag: null }),
                    apply: (tr, prev, oldState, next) => {
                        const meta = tr.getMeta(wikiTableToolsKey) || {};
                        let { grip, before, rowHover, rowDrag } = prev;
                        if ('rowHover' in meta) rowHover = meta.rowHover;
                        if ('rowDrag' in meta) rowDrag = meta.rowDrag;
                        if (tr.docChanged) {
                            // Правка во время наведения: номер строки мог уехать.
                            if (rowHover) rowHover = null;
                            if (rowDrag && !('rowDrag' in meta)) rowDrag = null;
                            if (grip) {
                                grip = { ...grip, anchor: tr.mapping.map(grip.anchor), head: tr.mapping.map(grip.head) };
                            }
                            if (before) before = before.map(tr.mapping);
                        }
                        const sel = next.selection;
                        if (meta.grip && sel instanceof CellSelection) {
                            // Ручку нажали, когда уже было выделение ручкой: курсор
                            // «до ручек» остаётся прежним.
                            if (!gripSelected(oldState)) before = oldState.selection.getBookmark();
                            grip = { ...meta.grip, anchor: sel.$anchorCell.pos, head: sel.$headCell.pos };
                        } else if (grip && sel instanceof CellSelection
                            && (sel.$anchorCell.pos !== grip.anchor || sel.$headCell.pos !== grip.head)) {
                            grip = null;
                            before = null;
                        }
                        if (grip === prev.grip && before === prev.before
                            && rowHover === prev.rowHover && rowDrag === prev.rowDrag) return prev;
                        return { grip, before, rowHover, rowDrag };
                    },
                },
                view: (editorView) => {
                    menuOpeners.set(editorView, (payload) => options.onGrip?.(payload));
                    return { destroy: () => menuOpeners.delete(editorView) };
                },
                appendTransaction: guardFixedWidths,
                props: {
                    decorations: (state) => buildDecorations(state),
                    attributes: (state) => {
                        const tools = wikiTableToolsKey.getState(state);
                        return tools && (tools.rowHover || tools.rowDrag) ? { class: 'wiki-row-resize-cursor' } : {};
                    },
                    handleDOMEvents: {
                        mousemove: (view, event) => {
                            const tools = wikiTableToolsKey.getState(view.state);
                            // Кнопка мыши нажата — идёт выделение или перетаскивание: не мешаем.
                            if (!view.editable || !tools || tools.rowDrag || event.buttons) return false;
                            const hit = rowEdgeAt(view, event);
                            if (!sameRow(hit, tools.rowHover)) {
                                view.dispatch(view.state.tr.setMeta(wikiTableToolsKey, { rowHover: hit }));
                            }
                            return false;
                        },
                        mouseleave: (view) => {
                            const tools = wikiTableToolsKey.getState(view.state);
                            if (tools?.rowHover && !tools.rowDrag) {
                                view.dispatch(view.state.tr.setMeta(wikiTableToolsKey, { rowHover: null }));
                            }
                            return false;
                        },
                        mousedown: (view, event) => {
                            if (event.button !== 0 || !view.editable) return false;
                            const resize = columnResizingPluginKey.getState(view.state);
                            if (resize && resize.activeHandle > -1 && !resize.dragging) {
                                const found = tableAround(view.state.doc.resolve(resize.activeHandle));
                                if (found) freezeOnFirstMove(view, found.pos, event, columnClick);
                                // false — дальше нажатие разбирает плагин ширины столбцов.
                                return false;
                            }
                            const hover = wikiTableToolsKey.getState(view.state)?.rowHover;
                            if (!hover) return false;
                            if (!startRowDrag(view, event, hover)) return false;
                            event.preventDefault();
                            return true;
                        },
                    },
                    transformPastedHTML: (html) => unwrapSpreadsheetCell(html),
                    clipboardTextParser: (text, $context, plain, view) => {
                        // Shift+⌘V — «вставить как текст»: человек просит именно текст.
                        if (plain || $context.parent.type.spec.code) return null;
                        const rows = parseTabular(text);
                        if (!rows) return null;
                        /* Внутри таблицы строки ложатся в ячейки (это делает
                           prosemirror-tables), и шапка там не нужна; в тексте —
                           новая таблица с шапкой, как от кнопки. */
                        const header = !tableAround($context);
                        const table = tableFromRows(view.state.schema, rows, { header });
                        return new Slice(Fragment.from(table), 0, 0);
                    },
                },
            }),
        ];
    },
});

export default WikiTableTools;
