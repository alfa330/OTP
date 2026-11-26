import React, { useCallback, useMemo } from 'react';
import { useEditorState } from '@tiptap/react';
import { BubbleMenu } from '@tiptap/react/menus';
import { Selection, TextSelection } from '@tiptap/pm/state';
import { CellSelection, TableMap, moveTableColumn, moveTableRow } from '@tiptap/pm/tables';
import {
    AlignHorizontalJustifyCenter, AlignHorizontalJustifyEnd, AlignHorizontalJustifyStart, ArrowDown,
    ArrowDownToLine, ArrowLeft, ArrowLeftToLine, ArrowRight, ArrowRightToLine, ArrowUp, ArrowUpDown,
    ArrowUpToLine, Copy, CopyPlus, Eraser, MoveHorizontal, PanelLeft, PanelTop, Scaling, Scissors,
    TableCellsMerge, TableCellsSplit, Trash2,
} from 'lucide-react';
import { IosMenuPopover } from '../ui/ios';
import {
    canDuplicateRow, duplicateRow, duplicateTable, hasHeaderColumn, hasHeaderRow, moveTable,
    neighborTarget, pasteKeys, resetTableSizes, selectionShape, setTableAlign, tableAlign, tableAt,
    tableClipboard, tableHasSizes, writeClipboard,
} from './tableTools';
import { gripSelected, restoreGripCursor } from './WikiTableTools';

/* Меню таблицы в редакторе статьи.
 *
 * Было: одна панель из десяти значков под таблицей. Три беды. Значки без
 * подписей читались наугад (крестик «удалить столбец» — как «плюс»). Панель
 * висела под НИЖНИМ краем таблицы, и у длинной таблицы её не было видно, пока
 * правишь верхние строки. И чтобы удалить строку, надо было сначала поставить
 * в неё курсор, а потом искать кнопку внизу.
 *
 * Стало, как в «Заметках» macOS: у таблицы с курсором слева от каждой строки
 * и над каждым столбцом — ручка. Нажал — строка выделена, рядом с ручкой меню
 * с подписанными пунктами. Угловая ручка — вся таблица: копировать, вырезать,
 * дублировать, шапка, удалить. Ручки и кнопки «+» рисует WikiTableTools
 * (внутри таблицы), здесь — только меню.
 *
 * Панель «Объединить / Разбить» осталась, но появляется лишь там, где без неё
 * никак: когда мышью выделено несколько ячеек или курсор стоит в уже
 * объединённой. Выделение ручкой её не вызывает — у ручки своё меню.
 */

const runPm = (editor, command) => {
    const done = command(editor.state, (tr) => editor.view.dispatch(tr));
    editor.view.focus();
    return done;
};

/* Курсор — в ячейку (строка, столбец) таблицы. После пункта меню выделение
   строки снимается: оно уже сделало своё дело, а оставшись, вызвало бы
   панель «Объединить» над только что вставленной строкой. */
const cursorToCell = (editor, tablePos, row, col) => {
    const found = tableAt(editor.state.doc, tablePos);
    if (!found) return;
    const map = TableMap.get(found.node);
    const r = Math.max(0, Math.min(map.height - 1, row));
    const c = Math.max(0, Math.min(map.width - 1, col));
    const cellPos = found.start + map.map[r * map.width + c];
    const tr = editor.state.tr.setSelection(TextSelection.near(editor.state.doc.resolve(cellPos + 1)));
    editor.view.dispatch(tr);
    editor.view.focus();
};

/* Удалить таблицу по позиции, а не «где курсор»: меню открыто от ручки, и
   курсор к этому моменту может стоять где угодно. */
const deleteTableAt = (tablePos) => (state, dispatch) => {
    const node = state.doc.nodeAt(tablePos);
    if (!node || node.type.spec.tableRole !== 'table') return false;
    if (dispatch) {
        const tr = state.tr.delete(tablePos, tablePos + node.nodeSize);
        const at = Math.min(tablePos, tr.doc.content.size);
        tr.setSelection(Selection.near(tr.doc.resolve(at)));
        dispatch(tr.scrollIntoView());
    }
    return true;
};

const undoKeys = () => pasteKeys().replace('V', 'Z');

const cellsBarShow = ({ editor: ed, state }) => {
    if (!ed.isEditable) return false;
    if (gripSelected(state)) return false;
    const shape = selectionShape(state);
    if (!shape) return false;
    const merged = (cell) => Boolean(cell && (cell.attrs.colspan > 1 || cell.attrs.rowspan > 1));
    if (state.selection instanceof CellSelection) {
        if (shape.count > 1) return true;
        // Одна выделенная ячейка (так оставляет выделение «Объединить») — панель
        // нужна, только если ячейка объединённая: в ней есть что разбить.
        let single = null;
        state.selection.forEachCell((cell) => { single = cell; });
        return merged(single);
    }
    return merged(shape.cell);
};

const BarButton = ({ onClick, children, tone = 'plain' }) => (
    <button
        type="button"
        onMouseDown={(e) => e.preventDefault()}
        onClick={onClick}
        className={`flex h-7 items-center gap-1.5 rounded-lg px-2 text-[12.5px] font-medium transition ${
            tone === 'danger' ? 'text-rose-600 hover:bg-rose-50' : 'text-slate-700 hover:bg-slate-100'
        }`}
    >
        {children}
    </button>
);

export default function WikiTableMenu({ editor, menu, onClose, showToast }) {
    /* Доступность «Объединить» и «Разбить» — через useEditorState: useEditor
       в TipTap 3 не перерисовывает компонент на транзакциях, и прямой
       editor.can() застыл бы на значении момента открытия редактора.
       Проверка ed.isDestroyed обязательна: селектор успевает сработать на
       разобранном редакторе, и ed.can() там падает, роняя весь редактор. */
    const cells = useEditorState({
        editor,
        selector: ({ editor: ed }) => {
            if (!ed || ed.isDestroyed) return { merge: false, split: false, multi: false };
            const sel = ed.state.selection;
            return {
                merge: ed.can().mergeCells(),
                split: ed.can().splitCell(),
                multi: sel instanceof CellSelection && sel.ranges.length > 1,
            };
        },
    });

    /* Меню закрыли, ничего не выбрав, — выделение ручкой снимаем и курсор
       возвращаем туда, где он стоял до нажатия на ручку (restoreGripCursor).
       Иначе следующая же набранная буква заменила бы собой ВСЮ выделенную
       строку: так ProseMirror поступает с выделением ячеек, а человек, нажавший
       Esc, считает, что меню закрыл и печатает дальше. Не возвращаем только на
       клавиши, которые адресованы самому выделению: Delete и Backspace
       (очистить), стрелки и Tab (перейти), сочетания с ⌘ или Ctrl
       (копировать, вырезать, жирный). И на щелчок мимо: он сам ставит курсор
       туда, куда щёлкнули. */
    const close = useCallback((reason, event) => {
        onClose?.();
        if (reason === 'pick' || reason === 'outside' || !editor || editor.isDestroyed) return;
        if (reason === 'key' && event && (event.metaKey || event.ctrlKey || event.altKey
            || ['Delete', 'Backspace', 'Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key))) {
            return;
        }
        restoreGripCursor(editor.view);
    }, [onClose, editor]);

    const items = useMemo(() => {
        if (!menu || !editor || editor.isDestroyed) return [];
        const found = tableAt(editor.state.doc, menu.tablePos);
        if (!found) return [];
        const table = found.node;
        const map = TableMap.get(table);
        const headerRow = hasHeaderRow(table);
        const headerColumn = hasHeaderColumn(table);
        const { from, to, tablePos } = menu;
        const tableStart = tablePos + 1;
        const state = editor.state;

        if (menu.kind === 'row') {
            /* Шапка остаётся наверху: строку-шапку не двигаем, и под неё ничего
               не поднимаем — иначе шапка уехала бы в середину таблицы. */
            const firstMovable = headerRow ? 1 : 0;
            const canUp = from > firstMovable
                && moveTableRow({ from, to: from - 1, pos: tableStart })(state);
            const canDown = from >= firstMovable && to < map.height - 1
                && moveTableRow({ from, to: to + 1, pos: tableStart })(state);
            const single = from === to;
            return [
                {
                    key: 'above', label: 'Вставить строку выше', icon: ArrowUpToLine,
                    onSelect: () => {
                        editor.chain().focus().addRowBefore().run();
                        cursorToCell(editor, tablePos, from, 0);
                    },
                },
                {
                    key: 'below', label: 'Вставить строку ниже', icon: ArrowDownToLine,
                    onSelect: () => {
                        editor.chain().focus().addRowAfter().run();
                        cursorToCell(editor, tablePos, to + 1, 0);
                    },
                },
                {
                    key: 'duplicate', label: 'Дублировать строку', icon: CopyPlus,
                    disabled: !single || !canDuplicateRow(table, from),
                    onSelect: () => runPm(editor, duplicateRow(tablePos, from)),
                },
                {
                    key: 'up', label: 'Переместить вверх', icon: ArrowUp, separatorBefore: true,
                    disabled: !canUp,
                    onSelect: () => {
                        runPm(editor, moveTableRow({ from, to: from - 1, pos: tableStart }));
                        cursorToCell(editor, tablePos, from - 1, 0);
                    },
                },
                {
                    key: 'down', label: 'Переместить вниз', icon: ArrowDown, disabled: !canDown,
                    onSelect: () => {
                        runPm(editor, moveTableRow({ from, to: to + 1, pos: tableStart }));
                        cursorToCell(editor, tablePos, from + 1, 0);
                    },
                },
                {
                    key: 'clear', label: 'Очистить строку', icon: Eraser, separatorBefore: true,
                    onSelect: () => editor.chain().focus().deleteSelection().run(),
                },
                {
                    key: 'delete', label: 'Удалить строку', icon: Trash2, danger: true,
                    // Последнюю строку не удалить: таблица без строк — это удаление таблицы.
                    disabled: from === 0 && to === map.height - 1,
                    onSelect: () => {
                        editor.chain().focus().deleteRow().run();
                        cursorToCell(editor, tablePos, from, 0);
                    },
                },
            ];
        }

        if (menu.kind === 'column') {
            const firstMovable = headerColumn ? 1 : 0;
            const canLeft = from > firstMovable
                && moveTableColumn({ from, to: from - 1, pos: tableStart })(state);
            const canRight = from >= firstMovable && to < map.width - 1
                && moveTableColumn({ from, to: to + 1, pos: tableStart })(state);
            return [
                {
                    key: 'left', label: 'Вставить столбец слева', icon: ArrowLeftToLine,
                    onSelect: () => {
                        editor.chain().focus().addColumnBefore().run();
                        cursorToCell(editor, tablePos, 0, from);
                    },
                },
                {
                    key: 'right', label: 'Вставить столбец справа', icon: ArrowRightToLine,
                    onSelect: () => {
                        editor.chain().focus().addColumnAfter().run();
                        cursorToCell(editor, tablePos, 0, to + 1);
                    },
                },
                {
                    key: 'move-left', label: 'Переместить влево', icon: ArrowLeft, separatorBefore: true,
                    disabled: !canLeft,
                    onSelect: () => {
                        runPm(editor, moveTableColumn({ from, to: from - 1, pos: tableStart }));
                        cursorToCell(editor, tablePos, 0, from - 1);
                    },
                },
                {
                    key: 'move-right', label: 'Переместить вправо', icon: ArrowRight, disabled: !canRight,
                    onSelect: () => {
                        runPm(editor, moveTableColumn({ from, to: to + 1, pos: tableStart }));
                        cursorToCell(editor, tablePos, 0, from + 1);
                    },
                },
                {
                    key: 'clear', label: 'Очистить столбец', icon: Eraser, separatorBefore: true,
                    onSelect: () => editor.chain().focus().deleteSelection().run(),
                },
                {
                    key: 'delete', label: 'Удалить столбец', icon: Trash2, danger: true,
                    disabled: from === 0 && to === map.width - 1,
                    onSelect: () => {
                        editor.chain().focus().deleteColumn().run();
                        cursorToCell(editor, tablePos, 0, from);
                    },
                },
            ];
        }

        /* Вся таблица. «Копировать» кладёт в буфер таблицу целиком — вставка
           ⌘V в любом месте этой или другой статьи даёт её же, с объединениями
           и ширинами; Excel и Word тоже понимают её таблицей. */
        const copy = (cut) => {
            const data = tableClipboard(editor.view, tablePos);
            if (!data) return;
            writeClipboard(data).then((ok) => {
                if (!ok) {
                    showToast?.('Не получилось скопировать — нажмите ⌘C или Ctrl+C: таблица уже выделена', 'error');
                    return;
                }
                if (cut) runPm(editor, deleteTableAt(tablePos));
                showToast?.(cut
                    ? `Таблица вырезана — поставьте курсор, куда её перенести, и нажмите ${pasteKeys()}`
                    : `Таблица скопирована — поставьте курсор, куда её вставить, и нажмите ${pasteKeys()}`,
                'success');
            });
        };
        // Ширины столбцов или высоты строк заданы перетаскиванием границ.
        const sized = tableHasSizes(table);
        /* Переставить таблицу на шаг — тот же перенос, что ручкой, но без
           прицеливания мышью: выше соседнего блока или ниже него. */
        const upTarget = neighborTarget(state.doc, tablePos, -1);
        const downTarget = neighborTarget(state.doc, tablePos, 1);
        /* Положение по ширине имеет смысл, только если таблица уже колонки
           текста: таблице во всю ширину двигаться вбок некуда, и кнопки
           гаснут, а не делают вид, что сработали. */
        const align = tableAlign(table);
        const wrapper = editor.view.nodeDOM(tablePos);
        const tableEl = wrapper?.querySelector?.('table');
        let room = true;
        if (wrapper && tableEl) {
            const style = window.getComputedStyle(wrapper);
            const inner = wrapper.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0);
            room = inner - tableEl.getBoundingClientRect().width > 4;
        }
        const placeAt = (side) => ({
            key: `align-${side}`,
            label: { left: 'У левого края', center: 'По центру', right: 'У правого края' }[side],
            icon: { left: AlignHorizontalJustifyStart, center: AlignHorizontalJustifyCenter, right: AlignHorizontalJustifyEnd }[side],
            checked: align === side,
            disabled: !room && align === 'left',
            onSelect: () => runPm(editor, setTableAlign(tablePos, side)),
        });
        return [
            { key: 'copy', label: 'Копировать таблицу', icon: Copy, onSelect: () => copy(false) },
            { key: 'cut', label: 'Вырезать таблицу', icon: Scissors, onSelect: () => copy(true) },
            {
                key: 'duplicate', label: 'Дублировать таблицу', icon: CopyPlus,
                onSelect: () => runPm(editor, duplicateTable(tablePos)),
            },
            {
                key: 'move', label: 'Переместить', icon: ArrowUpDown, separatorBefore: true,
                segments: [
                    {
                        key: 'move-up', label: 'Переместить выше', icon: ArrowUp,
                        disabled: upTarget == null,
                        onSelect: () => runPm(editor, moveTable(tablePos, upTarget)),
                    },
                    {
                        key: 'move-down', label: 'Переместить ниже', icon: ArrowDown,
                        disabled: downTarget == null,
                        onSelect: () => runPm(editor, moveTable(tablePos, downTarget)),
                    },
                ],
            },
            {
                key: 'align', label: 'Положение', icon: MoveHorizontal,
                segments: ['left', 'center', 'right'].map(placeAt),
            },
            {
                key: 'header-row', label: 'Шапка сверху', icon: PanelTop, separatorBefore: true,
                checked: headerRow,
                onSelect: () => editor.chain().focus().toggleHeaderRow().run(),
            },
            {
                key: 'header-col', label: 'Шапка слева', icon: PanelLeft,
                checked: headerColumn,
                onSelect: () => editor.chain().focus().toggleHeaderColumn().run(),
            },
            sized && {
                key: 'auto-size', label: 'Сбросить размеры', icon: Scaling,
                hint: 'по тексту',
                onSelect: () => runPm(editor, resetTableSizes(tablePos)),
            },
            {
                key: 'delete', label: 'Удалить таблицу', icon: Trash2, danger: true, separatorBefore: true,
                onSelect: () => {
                    runPm(editor, deleteTableAt(tablePos));
                    showToast?.(`Таблица удалена. Вернуть — ${undoKeys()}`, 'info');
                },
            },
        ];
    }, [menu, editor, showToast]);

    if (!editor) return null;

    return (
        <>
            <IosMenuPopover
                open={Boolean(menu) && items.length > 0}
                anchor={menu?.anchor}
                items={items}
                onClose={close}
                side={menu?.kind === 'row' ? 'right' : 'bottom'}
                keepFocus
                label={menu?.kind === 'row' ? 'Строка таблицы'
                    : menu?.kind === 'column' ? 'Столбец таблицы' : 'Таблица'}
            />
            <BubbleMenu
                editor={editor}
                pluginKey="wikiTableCells"
                shouldShow={cellsBarShow}
                options={{ placement: 'top', offset: 8 }}
                className="flex items-center gap-0.5 rounded-xl border border-slate-200 bg-white/95 p-1 shadow-lg shadow-slate-900/10 backdrop-blur-xl"
            >
                {cells?.merge && (
                    <BarButton onClick={() => editor.chain().focus().mergeCells().run()}>
                        <TableCellsMerge size={14} />
                        Объединить
                    </BarButton>
                )}
                {cells?.split && (
                    <BarButton onClick={() => editor.chain().focus().splitCell().run()}>
                        <TableCellsSplit size={14} />
                        Разбить
                    </BarButton>
                )}
                {cells?.multi && (
                    <BarButton onClick={() => editor.chain().focus().deleteSelection().run()}>
                        <Eraser size={14} />
                        Очистить
                    </BarButton>
                )}
            </BubbleMenu>
        </>
    );
}
