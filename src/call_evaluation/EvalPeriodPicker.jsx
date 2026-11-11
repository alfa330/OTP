import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import FaIcon from '../components/common/FaIcon';
import {
    PERIOD_VIEWS, PERIOD_VIEW_LABELS, YEAR_PAGE, dayCells, initialPeriodView, isoDate, monthCells, periodHeader,
    periodLabel, pickDayStep, portalTabStep, presetMatches, quickPeriods, selectionBand, visibleYearCells,
    yearPageEnd,
} from '../components/ui/periodRange';

/* Календарь периода «Аналитики» журнала оценок (задача #389): год, месяц,
 * конкретный день или диапазон дат и быстрые периоды «Сегодня», «Неделя»,
 * «Месяц», «Весь период».
 *
 * Свой, а не портальный IosDateRangePicker: тот целиком на Tailwind, которого в
 * этой сборке нет (как и у EvalSelect). Правила и математика сетки — общие, из
 * src/components/ui/periodRange.js: что значит «Неделя», какой месяц «целый»,
 * как подписать выбор, какие месяцы уже недоступны — у двух календарей одно.
 * Вид — языком журнала (кнопка как у .ce-select-btn, панель как у
 * .ce-select-pop), раскладка — как у календаря периода в ИИ-оценке:
 * переключатель «Дни · Месяцы · Годы», шесть недель всегда (панель не прыгает),
 * быстрые периоды сеткой 2 × 2, совпавший подсвечен.
 *
 * Панель — в портале с fixed-координатами: журнал живёт в iframe и
 * прокручивается внутри себя, а absolute-панель резали бы шапки разделов. */

const DAYS_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const PANEL_WIDTH = 284;
const GAP = 6;
const EDGE = 8;
const PRESETS = quickPeriods();

// Экспорт — для теста: панель живёт в портале, а порталы react-dom/server не рисует.
export function PeriodCalendar({ from, to, min, max, onPick }) {
    const today = isoDate(new Date());
    const [draft, setDraft] = useState({ from, to });
    const [step, setStep] = useState(0);
    const [hover, setHover] = useState(null);
    const [mode, setMode] = useState(() => initialPeriodView(from, to, PERIOD_VIEWS));
    const [view, setView] = useState(() => {
        const [y, m] = (to || from || today).split('-').map(Number);
        return { y, m: m - 1 };
    });
    const anchorYear = Number((max || today).slice(0, 4));
    const [yearsEnd, setYearsEnd] = useState(() => yearPageEnd(Number((to || from || today).slice(0, 4)), anchorYear));

    const choose = (next) => {
        setDraft(next);
        setStep(0);
        setHover(null);
        onPick(next);
    };
    const pickDay = (cell) => {
        if (!cell.iso || cell.disabled) return;
        const next = pickDayStep(draft, step, cell.iso);
        setDraft(next.draft);
        setStep(next.step);
        setHover(next.done ? null : cell.iso);
        if (next.done) onPick(next.done);
    };
    const pickWhole = (cell) => {
        if (cell.disabled) return;
        const [y, m] = cell.range.from.split('-').map(Number);
        setView({ y, m: m - 1 });
        choose({ from: cell.range.from, to: cell.range.to });
    };

    // Годы до нижней границы не показываются; шапка — общая с календарём ИИ-оценки.
    const pageYears = mode === 'years'
        ? visibleYearCells(yearsEnd, { from: draft.from, to: draft.to, min, max, today })
        : null;
    const header = {
        ...periodHeader(mode, { view, yearsEnd, years: pageYears || [], min, max }),
        prev: mode === 'months' ? () => setView((v) => ({ ...v, y: v.y - 1 }))
            : mode === 'years' ? () => setYearsEnd((y) => y - YEAR_PAGE)
                : () => setView((v) => (v.m === 0 ? { y: v.y - 1, m: 11 } : { y: v.y, m: v.m - 1 })),
        next: mode === 'months' ? () => setView((v) => ({ ...v, y: v.y + 1 }))
            : mode === 'years' ? () => setYearsEnd((y) => y + YEAR_PAGE)
                : () => setView((v) => (v.m === 11 ? { y: v.y + 1, m: 0 } : { y: v.y, m: v.m + 1 })),
    };

    const { lo, hi } = selectionBand(draft, step, hover);
    const hint = mode === 'months' ? 'Выберите месяц'
        : mode === 'years' ? 'Выберите год'
            : step === 0 ? 'Выберите начало периода' : 'Выберите конец или тот же день ещё раз';

    return (
        <div className="ce-period-card">
            <div className="ce-period-views" role="tablist" aria-label="Что выбрать">
                {PERIOD_VIEWS.map((key) => (
                    <button key={key} type="button" role="tab" aria-selected={mode === key}
                            className={mode === key ? 'is-active' : ''}
                            onClick={() => { setMode(key); setStep(0); setHover(null); }}>
                        {PERIOD_VIEW_LABELS[key]}
                    </button>
                ))}
            </div>
            <div className="ce-period-head">
                <button type="button" className="ce-period-nav" onClick={header.prev} aria-label={header.prevLabel}
                        disabled={header.prevDisabled}>
                    <FaIcon className="fas fa-chevron-left" />
                </button>
                <span className="ce-period-title">{header.title}</span>
                <button type="button" className="ce-period-nav" onClick={header.next} aria-label={header.nextLabel}
                        disabled={header.nextDisabled}>
                    <FaIcon className="fas fa-chevron-right" />
                </button>
            </div>
            {mode === 'days' ? (
                <>
                    <div className="ce-period-weekdays">
                        {DAYS_SHORT.map((day) => <span key={day}>{day}</span>)}
                    </div>
                    <div className="ce-period-days">
                        {dayCells(view.y, view.m, { lo, hi, min, max, today, fill: true }).map((cell) => (
                            <div key={cell.key} className={[
                                'ce-period-cell',
                                cell.inBand ? 'in-band' : '',
                                cell.bandStart ? 'band-start' : '',
                                cell.bandEnd ? 'band-end' : '',
                            ].filter(Boolean).join(' ')}>
                                {cell.iso && (
                                    <button type="button" disabled={cell.disabled} onClick={() => pickDay(cell)}
                                            onMouseEnter={() => step === 1 && !cell.disabled && setHover(cell.iso)}
                                            className={[
                                                'ce-period-day',
                                                cell.isEnd ? 'is-end' : cell.inBand ? 'in-band' : cell.today ? 'is-today' : '',
                                            ].filter(Boolean).join(' ')}>
                                        {cell.day}
                                    </button>
                                )}
                            </div>
                        ))}
                    </div>
                </>
            ) : (
                <div className="ce-period-grid">
                    {(mode === 'months'
                        ? monthCells(view.y, { from: draft.from, to: draft.to, min, max, today })
                        : pageYears
                    ).map((cell) => (
                        <button key={cell.key} type="button" disabled={cell.disabled} title={cell.title}
                                aria-pressed={cell.selected} onClick={() => pickWhole(cell)}
                                className={[
                                    'ce-period-whole',
                                    cell.selected ? 'is-selected' : cell.current ? 'is-current' : '',
                                ].filter(Boolean).join(' ')}>
                            {cell.label}
                        </button>
                    ))}
                </div>
            )}
            <div className="ce-period-presets">
                {PRESETS.map((preset) => {
                    const active = presetMatches(preset, draft.from, draft.to);
                    return (
                        <button key={preset.key} type="button" aria-pressed={active}
                                className={`ce-period-preset ${active ? 'is-active' : ''}`.trim()}
                                onClick={() => choose(preset.range())}>
                            {active && <FaIcon className="fas fa-check" />}
                            {preset.label}
                        </button>
                    );
                })}
            </div>
            <p className="ce-period-hint">{hint}</p>
        </div>
    );
}

/* Журнал оценок ведётся с 2025 года (первые оценки — июль 2025): раньше дней нет,
   и календарь не предлагает заведомо пустые годы. */
export const JOURNAL_FIRST_DAY = '2025-01-01';

export default function EvalPeriodPicker({ from = '', to = '', onChange, min = JOURNAL_FIRST_DAY, max = '',
                                           disabled = false, id, ariaLabel = 'Период' }) {
    const [open, setOpen] = useState(false);
    const [coords, setCoords] = useState(null);
    const btnRef = useRef(null);
    const popRef = useRef(null);
    const limit = max || isoDate(new Date());

    /* Панель ниже кнопки, а не влезает — выше; за край окна не выходит:
       fixed-панель за краем не прокрутить ничем. Высота — настоящая, после
       первой отрисовки (вид «Дни» и «Месяцы» одной высоты, так что второй
       расчёт ничего не дёргает). */
    const recompute = useCallback(() => {
        const button = btnRef.current;
        if (!button) return;
        const view = button.ownerDocument?.defaultView || window;
        const rect = button.getBoundingClientRect();
        const height = popRef.current?.offsetHeight || 470;
        const below = view.innerHeight - rect.bottom - GAP - EDGE;
        const above = rect.top - GAP - EDGE;
        const up = below < height && above > below;
        let top = up ? rect.top - GAP - height : rect.bottom + GAP;
        top = Math.max(EDGE, Math.min(top, view.innerHeight - EDGE - height));
        let left = rect.left;
        if (left + PANEL_WIDTH > view.innerWidth - EDGE) left = rect.right - PANEL_WIDTH;
        const next = { left: Math.round(Math.max(EDGE, left)), top: Math.round(top) };
        setCoords((prev) => (prev && prev.left === next.left && prev.top === next.top ? prev : next));
    }, []);

    useEffect(() => {
        if (!open) { setCoords(null); return undefined; }
        recompute();
        const raf = requestAnimationFrame(recompute);
        const doc = btnRef.current?.ownerDocument || document;
        const view = doc.defaultView || window;
        const onDown = (event) => {
            if (btnRef.current?.contains(event.target) || popRef.current?.contains(event.target)) return;
            setOpen(false);
        };
        /* Панель — в портале в конце body, и обычный Tab с кнопки ушёл бы мимо
           неё, оставив её открытой (разбор #389). Правило — portalTabStep. */
        const onKey = (event) => {
            if (event.key === 'Escape') {
                event.stopPropagation();
                setOpen(false);
                btnRef.current?.focus();
                return;
            }
            if (event.key !== 'Tab') return;
            const items = [...(popRef.current?.querySelectorAll('button:not([disabled])') || [])];
            const step = portalTabStep({
                onTrigger: doc.activeElement === btnRef.current,
                index: items.indexOf(doc.activeElement),
                count: items.length,
                shift: event.shiftKey,
            });
            if (step === 'first') {
                event.preventDefault();
                items[0].focus();
            } else if (step === 'trigger') {
                event.preventDefault();
                btnRef.current?.focus();
            } else if (step === 'leave') {
                // Без preventDefault: фокус встаёт на кнопку, и Tab идёт от неё дальше.
                btnRef.current?.focus();
                setOpen(false);
            }
        };
        // Фокус ушёл куда-то вне кнопки и панели — панель не висит забытой.
        const onFocusOut = (event) => {
            const next = event.relatedTarget;
            if (!next || btnRef.current?.contains(next) || popRef.current?.contains(next)) return;
            setOpen(false);
        };
        // Прокрутка внутри самой панели её не двигает; снаружи — панель едет за кнопкой.
        const onScroll = (event) => {
            if (popRef.current && (popRef.current === event.target || popRef.current.contains(event.target))) return;
            recompute();
        };
        doc.addEventListener('mousedown', onDown, true);
        doc.addEventListener('keydown', onKey);
        doc.addEventListener('focusout', onFocusOut);
        view.addEventListener('scroll', onScroll, true);
        view.addEventListener('resize', recompute);
        return () => {
            cancelAnimationFrame(raf);
            doc.removeEventListener('mousedown', onDown, true);
            doc.removeEventListener('keydown', onKey);
            doc.removeEventListener('focusout', onFocusOut);
            view.removeEventListener('scroll', onScroll, true);
            view.removeEventListener('resize', recompute);
        };
    }, [open, recompute]);

    const ownerBody = btnRef.current?.ownerDocument?.body || (typeof document !== 'undefined' ? document.body : null);

    return (
        <div className="ce-period">
            <button ref={btnRef} id={id} type="button" disabled={disabled}
                    className={`ce-select-btn ce-period-btn ${open ? 'is-open' : ''}`.trim()}
                    aria-haspopup="dialog" aria-expanded={open} aria-label={`${ariaLabel}: ${periodLabel(from, to)}`}
                    onClick={() => !disabled && setOpen((value) => !value)}>
                <FaIcon className="fas fa-calendar-alt ce-period-icon" />
                <span className="ce-select-value">{periodLabel(from, to)}</span>
                <FaIcon className="fas fa-chevron-down ce-select-caret" />
            </button>
            {open && ownerBody && createPortal(
                <div ref={popRef} role="dialog" aria-label="Выбор периода" className="ce-period-pop"
                     style={coords ? { left: coords.left, top: coords.top } : { visibility: 'hidden', left: 0, top: 0 }}>
                    <PeriodCalendar from={from} to={to} min={min} max={limit}
                                    onPick={(next) => {
                                        setOpen(false);
                                        onChange?.(next);
                                        requestAnimationFrame(() => btnRef.current?.focus());
                                    }} />
                </div>,
                ownerBody,
            )}
        </div>
    );
}
