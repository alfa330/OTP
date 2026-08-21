import React, { useMemo, useRef, useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { ChevronLeft, ChevronRight, CalendarRange } from 'lucide-react';
import { formatMonth } from './constants';

/* Выбор месяца в стиле раздела.
 *
 * Зачем свой контрол. Во всём портале месяц выбирается сырым
 * `<input type="month">` — системным полем, которое в каждом браузере своё, не
 * умеет ни клавиш «предыдущий/следующий», ни подписи «Август 2026», и рядом с
 * карточками на rounded-2xl выглядит деталью из другой программы. Общего
 * примитива для месяца в ui/ нет (DateRangePicker умеет только диапазон дней),
 * поэтому он появляется здесь.
 *
 * Поповер уходит в портал с фиксированными координатами — иначе его обрезало бы
 * `overflow-hidden` карточки, в которой стоит строка фильтров.
 */

const MONTHS_SHORT = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн',
    'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек'];

const shiftMonth = (month, delta) => {
    const [year, m] = String(month).split('-').map(Number);
    const date = new Date(year, (m - 1) + delta, 1);
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
};

export default function MonthPicker({ value, onChange, minYear = 2024 }) {
    const [open, setOpen] = useState(false);
    const [coords, setCoords] = useState(null);
    const buttonRef = useRef(null);
    const currentMonth = useMemo(() => new Date().toISOString().slice(0, 7), []);
    const [year, monthNumber] = useMemo(() => {
        const parts = String(value || currentMonth).split('-').map(Number);
        return [parts[0], parts[1]];
    }, [value, currentMonth]);
    const [shownYear, setShownYear] = useState(year);

    useEffect(() => { setShownYear(year); }, [year, open]);

    // Любая прокрутка закрывает поповер: координаты фиксированные, и при
    // прокрутке он «отклеился» бы от кнопки. Тот же приём, что в IosMenu.
    useEffect(() => {
        if (!open) return undefined;
        const close = () => setOpen(false);
        const onKey = (event) => { if (event.key === 'Escape') setOpen(false); };
        window.addEventListener('scroll', close, true);
        window.addEventListener('resize', close);
        window.addEventListener('keydown', onKey);
        return () => {
            window.removeEventListener('scroll', close, true);
            window.removeEventListener('resize', close);
            window.removeEventListener('keydown', onKey);
        };
    }, [open]);

    const toggle = () => {
        if (open) { setOpen(false); return; }
        const rect = buttonRef.current?.getBoundingClientRect();
        if (!rect) return;
        const width = 268;
        const left = Math.min(Math.max(8, rect.left), window.innerWidth - width - 8);
        const spaceBelow = window.innerHeight - rect.bottom;
        const height = 250;
        setCoords({
            left,
            top: spaceBelow < height ? Math.max(8, rect.top - height - 6) : rect.bottom + 6,
            width,
        });
        setOpen(true);
    };

    const maxMonth = currentMonth;
    const canGoForward = value < maxMonth;

    const pick = (index) => {
        const next = `${shownYear}-${String(index + 1).padStart(2, '0')}`;
        if (next > maxMonth) return;
        onChange(next);
        setOpen(false);
    };

    return (
        <div className="flex shrink-0 items-center gap-1">
            <button
                type="button"
                onClick={() => onChange(shiftMonth(value, -1))}
                title="Предыдущий месяц"
                aria-label="Предыдущий месяц"
                className="grid h-[34px] w-[30px] place-items-center rounded-l-xl bg-white text-slate-400 ring-1 ring-slate-200/70 transition hover:bg-slate-50 hover:text-slate-700 active:scale-[0.97]"
            >
                <ChevronLeft size={15} />
            </button>

            <button
                ref={buttonRef}
                type="button"
                onClick={toggle}
                aria-haspopup="dialog"
                aria-expanded={open}
                className={`flex h-[34px] min-w-[148px] items-center justify-center gap-2 bg-white px-3 text-[12.5px] font-semibold text-slate-700 ring-1 transition active:scale-[0.99] ${
                    open ? 'ring-2 ring-blue-500/60' : 'ring-slate-200/70 hover:bg-slate-50'
                }`}
                style={{ marginLeft: -4, marginRight: -4 }}
            >
                <CalendarRange size={14} className="text-slate-400" />
                <span className="tabular-nums">{formatMonth(value)}</span>
            </button>

            <button
                type="button"
                onClick={() => canGoForward && onChange(shiftMonth(value, 1))}
                disabled={!canGoForward}
                title={canGoForward ? 'Следующий месяц' : 'Это текущий месяц'}
                aria-label="Следующий месяц"
                className="grid h-[34px] w-[30px] place-items-center rounded-r-xl bg-white text-slate-400 ring-1 ring-slate-200/70 transition hover:bg-slate-50 hover:text-slate-700 active:scale-[0.97] disabled:opacity-40 disabled:hover:bg-white"
            >
                <ChevronRight size={15} />
            </button>

            {open && coords && createPortal(
                <>
                    <div className="fixed inset-0 z-[190]" onMouseDown={() => setOpen(false)} aria-hidden="true" />
                    <div
                        role="dialog"
                        aria-label="Выбор месяца"
                        className="fixed z-[200] overflow-hidden rounded-2xl bg-white p-3 shadow-[0_14px_40px_rgba(15,23,42,0.16)] ring-1 ring-slate-200/80 animate-[fadeIn_.12s_ease]"
                        style={{ left: coords.left, top: coords.top, width: coords.width }}
                    >
                        <div className="mb-2 flex items-center justify-between">
                            <button
                                type="button"
                                onClick={() => setShownYear((prev) => Math.max(minYear, prev - 1))}
                                disabled={shownYear <= minYear}
                                className="grid h-7 w-7 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30"
                                aria-label="Предыдущий год"
                            >
                                <ChevronLeft size={14} />
                            </button>
                            <div className="text-[13px] font-semibold tabular-nums text-slate-900">{shownYear}</div>
                            <button
                                type="button"
                                onClick={() => setShownYear((prev) => prev + 1)}
                                disabled={shownYear >= Number(maxMonth.slice(0, 4))}
                                className="grid h-7 w-7 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30"
                                aria-label="Следующий год"
                            >
                                <ChevronRight size={14} />
                            </button>
                        </div>

                        <div className="grid grid-cols-3 gap-1">
                            {MONTHS_SHORT.map((label, index) => {
                                const key = `${shownYear}-${String(index + 1).padStart(2, '0')}`;
                                const selected = key === value;
                                // Будущие месяцы недоступны: тренинг задним
                                // числом — норма, наперёд — нет.
                                const disabled = key > maxMonth;
                                return (
                                    <button
                                        key={label}
                                        type="button"
                                        onClick={() => pick(index)}
                                        disabled={disabled}
                                        className={`rounded-xl py-2 text-[12.5px] font-medium transition ${
                                            selected
                                                ? 'bg-blue-600 text-white shadow-sm'
                                                : disabled
                                                    ? 'text-slate-300'
                                                    : 'text-slate-600 hover:bg-slate-100 active:scale-[0.97]'
                                        }`}
                                    >
                                        {label}
                                    </button>
                                );
                            })}
                        </div>

                        {value !== currentMonth && (
                            <button
                                type="button"
                                onClick={() => { onChange(currentMonth); setOpen(false); }}
                                className="mt-2 w-full rounded-xl bg-slate-100 py-2 text-[12px] font-semibold text-slate-600 transition hover:bg-slate-200 active:scale-[0.99]"
                            >
                                Текущий месяц
                            </button>
                        )}
                    </div>
                </>,
                document.body,
            )}
        </div>
    );
}
