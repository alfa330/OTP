import React, { useEffect, useRef, useState } from 'react';
import { Calendar, ChevronUp, ChevronLeft, ChevronRight } from 'lucide-react';

/* Пикер диапазона дат в стиле сайта: поповер с одним месяцем, выбор в два
 * клика (начало → конец) с подсветкой полосы по курсору и пресетами.
 *
 * Жил внутри WazzupChatsView; вынесен сюда, когда такой же понадобился разделу
 * «Чаты ChatApp» — чтобы календарь был один на оба, а не две копии. */

const MONTHS_RU = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'май', 'июн',
    'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const DAYS_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

const daysInMonth = (y, m) => new Date(y, m + 1, 0).getDate();
const firstWeekday = (y, m) => { const d = new Date(y, m, 1).getDay(); return d === 0 ? 6 : d - 1; };

export const isoDate = (d) => {
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

export const fmtShortDate = (iso, withYear) => {
    const [y, m, d] = iso.split('-').map(Number);
    return `${d} ${MONTHS_SHORT[m - 1]}${withYear ? ` ${y}` : ''}`;
};

export const rangeLabel = (from, to) => {
    if (!from && !to) return 'Весь период';
    if (from && to) {
        if (from === to) return fmtShortDate(from, true);
        const sameYear = from.slice(0, 4) === to.slice(0, 4);
        return `${fmtShortDate(from, !sameYear)} — ${fmtShortDate(to, true)}`;
    }
    return from ? `с ${fmtShortDate(from, true)}` : `по ${fmtShortDate(to, true)}`;
};

/* `presets` — [{label, range: () => ({from, to})}]; по умолчанию «Сегодня» и
 * «Весь период». Раздел, которому пустой диапазон не подходит (например синк
 * ChatApp), передаёт свои и не отдаёт вариант «Весь период». */
export function IosDateRangePicker({ from, to, max, min, onChange, presets = null }) {
    const today = isoDate(new Date());
    const [open, setOpen] = useState(false);
    const [draft, setDraft] = useState({ from, to });   // выбор внутри поповера
    const [step, setStep] = useState(0);                // 0 — ждём начало, 1 — конец
    const [hover, setHover] = useState(null);
    const [view, setView] = useState(() => {
        const [y, m] = (to || from || today).split('-').map(Number);
        return { y, m: m - 1 };
    });
    const ref = useRef(null);

    // при открытии — синхронизируем черновик и показываем месяц конца диапазона
    useEffect(() => {
        if (!open) return;
        setDraft({ from, to });
        setStep(0);
        setHover(null);
        const [y, m] = (to || from || today).split('-').map(Number);
        setView({ y, m: m - 1 });
    }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        if (!open) return undefined;
        const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
        document.addEventListener('mousedown', h);
        return () => document.removeEventListener('mousedown', h);
    }, [open]);

    const pickDay = (iso, disabled) => {
        if (disabled) return;
        if (step === 0) {
            setDraft({ from: iso, to: iso });
            setStep(1);
            setHover(iso);
        } else {
            const start = draft.from || iso;
            const next = iso < start ? { from: iso, to: start } : { from: start, to: iso };
            setStep(0);
            setHover(null);
            onChange(next);
            setOpen(false);
        }
    };

    const setPreset = (next) => { onChange(next); setOpen(false); };
    const prevMonth = () => setView((v) => (v.m === 0 ? { y: v.y - 1, m: 11 } : { y: v.y, m: v.m - 1 }));
    const nextMonth = () => setView((v) => (v.m === 11 ? { y: v.y + 1, m: 0 } : { y: v.y, m: v.m + 1 }));

    // границы подсветки: на шаге выбора конца — по позиции курсора
    let lo = draft.from, hi = draft.to;
    if (step === 1 && hover && draft.from) {
        lo = hover < draft.from ? hover : draft.from;
        hi = hover < draft.from ? draft.from : hover;
    }
    const hasRange = lo && hi && lo !== hi;

    const cells = [];
    const lead = firstWeekday(view.y, view.m);
    for (let i = 0; i < lead; i += 1) cells.push(<div key={`e${i}`} className="h-9 w-9" />);
    for (let d = 1; d <= daysInMonth(view.y, view.m); d += 1) {
        const iso = `${view.y}-${String(view.m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        const disabled = Boolean((max && iso > max) || (min && iso < min));
        const isEnd = iso === lo || iso === hi;
        const inBand = hasRange && iso > lo && iso < hi;
        let wrap = 'relative flex h-9 w-9 items-center justify-center';
        if (inBand) wrap += ' bg-blue-500/10';
        else if (hasRange && iso === lo) wrap += ' rounded-l-full bg-blue-500/10';
        else if (hasRange && iso === hi) wrap += ' rounded-r-full bg-blue-500/10';
        let btn = 'flex h-8 w-8 items-center justify-center rounded-full text-[13px] transition ';
        if (disabled) btn += 'cursor-not-allowed text-slate-300';
        else if (isEnd) btn += 'bg-blue-500 font-semibold text-white shadow-sm';
        else if (inBand) btn += 'text-blue-700 hover:bg-blue-500/15';
        else if (iso === today) btn += 'font-semibold text-blue-600 ring-1 ring-inset ring-blue-300 hover:bg-slate-100';
        else btn += 'text-slate-700 hover:bg-slate-100';
        cells.push(
            <div key={iso} className={wrap}>
                <button type="button" disabled={disabled} className={btn}
                        onClick={() => pickDay(iso, disabled)}
                        onMouseEnter={() => step === 1 && !disabled && setHover(iso)}>
                    {d}
                </button>
            </div>,
        );
    }

    const footer = presets || [
        { label: 'Сегодня', range: () => ({ from: today, to: today }) },
        { label: 'Весь период', range: () => ({ from: '', to: '' }) },
    ];

    return (
        <div ref={ref} className="relative">
            <button type="button" onClick={() => setOpen((o) => !o)}
                    className={`flex items-center gap-2 rounded-xl px-3 py-2 text-[13px] font-medium transition ${
                        open ? 'bg-white text-slate-900 ring-2 ring-blue-500/70'
                             : 'bg-slate-100 text-slate-700 hover:bg-slate-200/80'}`}>
                <Calendar size={14} className="text-slate-400" />
                <span>{rangeLabel(from, to)}</span>
                <ChevronUp size={13} className={`text-slate-400 transition-transform ${open ? '' : 'rotate-180'}`} />
            </button>
            {open && (
                <div className="absolute left-0 top-full z-50 mt-2 w-[268px] rounded-2xl bg-white p-3 shadow-xl ring-1 ring-slate-200/70">
                    <div className="mb-2 flex items-center justify-between">
                        <button type="button" onClick={prevMonth}
                                className="grid h-7 w-7 place-items-center rounded-lg text-slate-500 transition hover:bg-slate-100">
                            <ChevronLeft size={16} />
                        </button>
                        <span className="text-[13.5px] font-semibold text-slate-900">
                            {MONTHS_RU[view.m]} {view.y}
                        </span>
                        <button type="button" onClick={nextMonth}
                                className="grid h-7 w-7 place-items-center rounded-lg text-slate-500 transition hover:bg-slate-100">
                            <ChevronRight size={16} />
                        </button>
                    </div>
                    <div className="mb-1 grid grid-cols-7">
                        {DAYS_SHORT.map((d) => (
                            <div key={d} className="flex h-7 w-9 items-center justify-center text-[10.5px] font-semibold uppercase text-slate-400">
                                {d}
                            </div>
                        ))}
                    </div>
                    <div className="grid grid-cols-7 gap-y-0.5">{cells}</div>
                    <div className="mt-2.5 flex items-center gap-2 border-t border-slate-100 pt-2.5">
                        {footer.map((p) => (
                            <button key={p.label} type="button" onClick={() => setPreset(p.range())}
                                    className="flex-1 rounded-lg bg-slate-100 py-1.5 text-[12px] font-semibold text-slate-600 transition hover:bg-slate-200/80">
                                {p.label}
                            </button>
                        ))}
                    </div>
                    <p className="mt-2 text-center text-[11px] text-slate-400">
                        {step === 0 ? 'Выберите начало периода' : 'Выберите конец периода'}
                    </p>
                </div>
            )}
        </div>
    );
}

export default IosDateRangePicker;
