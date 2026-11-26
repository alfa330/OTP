import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Table as TableIcon } from 'lucide-react';
import InfoHint from '../common/InfoHint';
import { APPLE_FONT } from '../ui/ios';
import { GRID_COLS, GRID_ROWS, clampSize, pasteKeys, tableSizeLabel } from './tableTools';

/* Кнопка «Таблица» в панели редактора: сетка выбора размера, как в Word и
 * Google Документах.
 *
 * Раньше кнопка вставляла 3×3 и только. Таблицу на шесть столбцов собирали
 * тремя лишними нажатиями «добавить столбец», а на две — удаляли лишний.
 * Здесь размер выбирается одним движением: навёл на клетку — подсвечено
 * столько столбцов и строк, щёлкнул — таблица такого размера. С клавиатуры —
 * стрелки и Enter.
 *
 * Первая строка — шапка, как и раньше: справочные таблицы вики почти все с
 * шапкой, а убрать её можно в меню угловой ручки таблицы.
 */

const DEFAULT_SIZE = { cols: 3, rows: 3 };
const POPOVER_WIDTH = 264;
// Высота окна: заголовок и восемь рядов клеток. Нужна заранее — решить, где
// открываться, надо до того, как окно нарисовано.
const POPOVER_HEIGHT = 232;

const HINT = 'Первая строка — шапка. Таблицу из Excel, Google Таблиц или Word можно не '
    + `собирать заново — скопируйте её там и вставьте в текст: ${pasteKeys()}. `
    + 'Tab — следующая ячейка, в последней ячейке Tab добавляет строку.';

export default function WikiTableInsert({ editor }) {
    const [open, setOpen] = useState(false);
    const [size, setSize] = useState(DEFAULT_SIZE);
    const [coords, setCoords] = useState(null);
    const btnRef = useRef(null);
    const popRef = useRef(null);

    const place = useCallback(() => {
        const r = btnRef.current?.getBoundingClientRect();
        if (!r) return;
        // Снизу не помещается, а сверху места больше — открываемся вверх, как меню.
        const spaceBelow = window.innerHeight - r.bottom;
        const openUp = spaceBelow < POPOVER_HEIGHT + 16 && r.top > spaceBelow;
        setCoords({
            left: Math.max(8, Math.min(window.innerWidth - POPOVER_WIDTH - 8, Math.round(r.left))),
            top: openUp ? undefined : Math.round(r.bottom + 6),
            bottom: openUp ? Math.round(window.innerHeight - r.top + 6) : undefined,
        });
    }, []);

    useLayoutEffect(() => {
        if (open) place();
        else setCoords(null);
    }, [open, place]);

    // Фокус — в окно: стрелки и Enter работают сразу после открытия.
    useEffect(() => {
        if (open && coords) popRef.current?.focus({ preventScroll: true });
    }, [open, coords]);

    const close = useCallback((refocus) => {
        setOpen(false);
        if (refocus) editor?.commands.focus();
    }, [editor]);

    useEffect(() => {
        if (!open) return undefined;
        const onDoc = (e) => {
            if (btnRef.current?.contains(e.target) || popRef.current?.contains(e.target)) return;
            setOpen(false);
        };
        // Прокрутка закрывает, как у меню: окно, приклеенное к уехавшей кнопке, висело бы в пустоте.
        const onScroll = () => setOpen(false);
        document.addEventListener('mousedown', onDoc);
        window.addEventListener('scroll', onScroll, true);
        window.addEventListener('resize', onScroll);
        return () => {
            document.removeEventListener('mousedown', onDoc);
            window.removeEventListener('scroll', onScroll, true);
            window.removeEventListener('resize', onScroll);
        };
    }, [open]);

    const insert = (cols, rows) => {
        setOpen(false);
        editor?.chain().focus().insertTable({ rows, cols, withHeaderRow: true }).run();
    };

    const onKeyDown = (e) => {
        const step = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowDown: [0, 1], ArrowUp: [0, -1] }[e.key];
        if (step) {
            e.preventDefault();
            setSize((prev) => ({
                cols: clampSize(prev.cols + step[0], GRID_COLS),
                rows: clampSize(prev.rows + step[1], GRID_ROWS),
            }));
            return;
        }
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            insert(size.cols, size.rows);
            return;
        }
        if (e.key === 'Escape') {
            e.preventDefault();
            e.stopPropagation();
            close(true);
        }
    };

    const cells = [];
    for (let row = 1; row <= GRID_ROWS; row += 1) {
        for (let col = 1; col <= GRID_COLS; col += 1) {
            const on = col <= size.cols && row <= size.rows;
            cells.push(
                <button
                    key={`${row}:${col}`}
                    type="button"
                    tabIndex={-1}
                    aria-label={`Вставить таблицу: ${tableSizeLabel(col, row)}`}
                    onMouseEnter={() => setSize({ cols: col, rows: row })}
                    onClick={() => insert(col, row)}
                    className={`h-[19px] w-[19px] rounded-[5px] border transition-colors ${
                        on ? 'border-blue-300 bg-blue-100' : 'border-slate-200 bg-white hover:border-slate-300'
                    }`}
                />,
            );
        }
    }

    return (
        <>
            <button
                ref={btnRef}
                type="button"
                title="Таблица"
                aria-label="Вставить таблицу"
                aria-haspopup="dialog"
                aria-expanded={open}
                // Не уводить фокус из текста: таблица встанет туда, где курсор.
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                    setSize(DEFAULT_SIZE);
                    setOpen((v) => !v);
                }}
                className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg transition ${
                    open ? 'bg-indigo-50 text-indigo-600' : 'text-slate-500 hover:bg-slate-100'
                }`}
            >
                <TableIcon size={15} />
            </button>
            {open && coords && createPortal(
                <div
                    ref={popRef}
                    role="dialog"
                    aria-label="Вставить таблицу"
                    tabIndex={-1}
                    onKeyDown={onKeyDown}
                    style={{
                        position: 'fixed',
                        left: coords.left,
                        top: coords.top,
                        bottom: coords.bottom,
                        width: POPOVER_WIDTH,
                        // Выше окон (IosModal — 90), но ниже пузыря «i» (InfoHint — 200):
                        // подсказка из этого же окна должна ложиться поверх него.
                        zIndex: 150,
                        fontFamily: APPLE_FONT,
                    }}
                    className="rounded-2xl bg-white/95 p-3 shadow-[0_14px_40px_rgba(15,23,42,0.18)] outline-none ring-1 ring-slate-200/80 backdrop-blur-xl motion-safe:animate-popover-in"
                >
                    <div className="mb-2.5 flex items-center justify-between gap-2 pl-0.5">
                        <span className="text-[13px] font-semibold tabular-nums text-slate-800">
                            {tableSizeLabel(size.cols, size.rows)}
                        </span>
                        <InfoHint side="left" text={HINT} />
                    </div>
                    <div
                        className="grid justify-between gap-y-[4px]"
                        style={{ gridTemplateColumns: `repeat(${GRID_COLS}, 19px)` }}
                    >
                        {cells}
                    </div>
                </div>,
                document.body,
            )}
        </>
    );
}
