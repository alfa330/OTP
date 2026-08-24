import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Calendar, ChevronUp } from 'lucide-react';
import { IosDateRangeCalendar, isoDate } from './DateRangePicker';

/* Выбор ОДНОЙ даты панелью раздела — вместо системного `<input type="date">`.
 *
 * Зачем. Раскрытый системный календарь рисует не сайт, а браузер: своя шапка
 * «август 2026 г. ▾», свои стрелки, свои кнопки «Удалить / Сегодня». Рядом с
 * карточками на rounded-2xl это деталь из другой программы, и владелец назначил
 * эталоном формы пикера выгрузку табло СЗоВ (ChatExportControls в
 * SzovWallboardView.jsx): триггер раздела + панель-карточка под ним. Здесь тот
 * же календарь, что и у диапазона, только в режиме одного дня — второй копии
 * календарной сетки в проекте быть не должно.
 *
 * Панель уходит в ПОРТАЛ с fixed-координатами, а не в absolute, как у эталона.
 * Эталон стоит в шапке табло, где его ничто не обрезает, а этот примитив нужен
 * и внутри IosModal, и внутри раскрытого поповера фильтров, и в карточке с
 * overflow-hidden: там absolute обрезало бы календарь.
 *
 * Props:
 *   value        — дата в ISO (`ГГГГ-ММ-ДД`); '' — не выбрана
 *   onChange(iso)— отдаёт ISO-строку (НЕ событие)
 *   min / max    — границы; дни за ними неактивны и некликабельны
 *   allowEmpty   — показывать ли кнопку «Очистить» (по умолчанию нет)
 *   placeholder  — что стоит в триггере, когда даты нет
 *   disabled, className, triggerClassName, ariaLabel, id
 */

const pad = (n) => String(n).padStart(2, '0');

/* В триггере дата в том же виде, в каком её показывало системное поле
   (24.08.2026): раздел открывают каждый день, и менять привычную запись даты
   заодно с видом панели — лишний повод спотыкаться. */
export const fmtDotted = (iso) => {
    if (!iso) return '';
    const [y, m, d] = String(iso).split('-');
    return `${pad(d)}.${pad(m)}.${y}`;
};

const PANEL_WIDTH = 268;   // ширина карточки календаря, задана в IosDateRangeCalendar
const PANEL_HEIGHT = 330;  // на глаз: месяц + сетка + пресеты. Нужна только чтобы решить, куда раскрывать

export default function IosDatePicker({
    value,
    onChange,
    min,
    max,
    allowEmpty = false,
    placeholder = 'Выберите дату',
    disabled = false,
    className = '',
    triggerClassName = '',
    ariaLabel,
    id,
}) {
    const [open, setOpen] = useState(false);
    const [coords, setCoords] = useState(null);
    const btnRef = useRef(null);
    const panelRef = useRef(null);

    /* Портал и размеры берём у ОКНА кнопки, а не у главного: часть разделов
       открывается в отдельном окне (Document Picture-in-Picture), и там
       document.body главного окна — чужой документ, панель просто не появится. */
    const ownerDocument = () => btnRef.current?.ownerDocument || document;

    const recompute = () => {
        const el = btnRef.current;
        if (!el) return;
        const view = el.ownerDocument?.defaultView || window;
        const r = el.getBoundingClientRect();
        const spaceBelow = view.innerHeight - r.bottom;
        const openUp = spaceBelow < PANEL_HEIGHT && r.top > spaceBelow;
        /* Панель шире кнопки, поэтому её левый край считаем сами: у правого
           края экрана раскрытие от левого края кнопки уводило бы календарь
           за экран. Прижимаем к правому краю кнопки и держим отступ от окна. */
        let left = r.left;
        if (left + PANEL_WIDTH > view.innerWidth - 8) left = r.right - PANEL_WIDTH;
        setCoords({
            left: Math.round(Math.max(8, left)),
            top: openUp ? undefined : Math.round(r.bottom + 6),
            bottom: openUp ? Math.round(view.innerHeight - r.top + 6) : undefined,
        });
    };

    useLayoutEffect(() => {
        if (open) recompute();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open]);

    useEffect(() => {
        if (!open) return undefined;
        const onDown = (e) => {
            if (btnRef.current?.contains(e.target) || panelRef.current?.contains(e.target)) return;
            setOpen(false);
        };
        /* Esc гасит ПАНЕЛЬ и дальше не идёт: этот пикер стоит и внутри IosModal,
           где необработанный Esc закрыл бы саму модалку — человек потерял бы
           заполненную форму, всего лишь передумав выбирать дату. */
        const onKey = (e) => {
            if (e.key !== 'Escape') return;
            e.stopPropagation();
            e.preventDefault();
            setOpen(false);
            requestAnimationFrame(() => btnRef.current?.focus());
        };
        // Скролл ВНУТРИ панели её не закрывает; внешний — пересчитывает позицию,
        // чтобы календарь оставался приклеен к кнопке.
        const onScroll = (e) => {
            if (panelRef.current && (panelRef.current === e.target || panelRef.current.contains(e.target))) return;
            recompute();
        };
        const doc = ownerDocument();
        const view = doc.defaultView || window;
        doc.addEventListener('mousedown', onDown);
        doc.addEventListener('keydown', onKey, true);
        view.addEventListener('scroll', onScroll, true);
        view.addEventListener('resize', recompute);
        return () => {
            doc.removeEventListener('mousedown', onDown);
            doc.removeEventListener('keydown', onKey, true);
            view.removeEventListener('scroll', onScroll, true);
            view.removeEventListener('resize', recompute);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open]);

    const today = isoDate(new Date());
    /* Календарь всегда открывается на месяце выбранной даты; если её нет —
       на сегодняшнем, но с оглядкой на границы: у поля с min={завтра} пустой
       календарь на сегодня встретил бы человека полностью серым месяцем. */
    const anchor = value || (min && min > today ? min : (max && max < today ? max : today));

    const trigger = (
        <button
            ref={btnRef}
            id={id}
            type="button"
            disabled={disabled}
            aria-label={ariaLabel}
            aria-haspopup="dialog"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className={triggerClassName || `flex items-center gap-2 rounded-xl px-3 py-2 text-[13px] font-medium tabular-nums transition
                disabled:cursor-not-allowed disabled:opacity-40 ${
                open ? 'bg-white text-slate-900 ring-2 ring-blue-500/70'
                     : 'bg-slate-100 text-slate-700 hover:bg-slate-200/80'}`}
        >
            <Calendar size={14} className="shrink-0 text-slate-400" />
            <span className={value ? '' : 'text-slate-400'}>{value ? fmtDotted(value) : placeholder}</span>
            <ChevronUp size={13} className={`shrink-0 text-slate-400 transition-transform ${open ? '' : 'rotate-180'}`} />
        </button>
    );

    const panel = open && coords ? createPortal(
        <div
            ref={panelRef}
            role="dialog"
            style={{ position: 'fixed', left: coords.left, top: coords.top, bottom: coords.bottom, zIndex: 99999 }}
        >
            <IosDateRangeCalendar
                single
                from={value || ''}
                to={value || ''}
                initialMonth={anchor}
                min={min}
                max={max}
                onChange={(next) => {
                    onChange(next.from || '');
                    setOpen(false);
                }}
                footer={allowEmpty && value ? (
                    <div className="mt-2 border-t border-slate-100 pt-2">
                        <button
                            type="button"
                            className="w-full rounded-lg py-1.5 text-[12px] font-semibold text-slate-500 transition hover:bg-slate-100"
                            onClick={() => { onChange(''); setOpen(false); }}
                        >
                            Очистить
                        </button>
                    </div>
                ) : null}
            />
        </div>,
        ownerDocument().body,
    ) : null;

    return (
        <div className={`relative ${className}`}>
            {trigger}
            {panel}
        </div>
    );
}
