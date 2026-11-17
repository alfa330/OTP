import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import FaIcon from '../common/FaIcon';
import InfoHint from '../common/InfoHint';
import CustomSelect from '../ui/CustomSelect';
import { APPLE_FONT, IosPager, IosSegmented, iosBtnGhost, iosCard } from '../ui/ios';
import { windowResizeMotion } from '../../utils/windowResize';
import {
    dayPresets,
    daysLabel,
    defaultFilters,
    exportQuery,
    filterShifts,
    formatDayRu,
    groupCounts,
    groupOptions,
    isFiltered,
    keepOperators,
    monthGrid,
    monthLabelOf,
    monthNeighbours,
    operatorOptions,
    operatorsLabel,
    pageSlice,
    summaryLabel,
} from './opActivityShared';

/*
 * Общие части окна «Отчёт по сменам» табло ОП: у линии (#392) и у чата (SALES-1, #424) один и тот
 * же порядок работы — месяц стрелками, группа, операторы и дни отбираются на месте, таблица
 * страницами по 50, строка открывает журнал смены, Excel — те же строки. Различаются колонки, ячейки
 * строк и ручки — их задаёт описание отчёта (`config`, см. OpActivityReport.jsx и OpChatShiftReport.jsx).
 *
 * Переходы плавные и без перестроек: первый вход рисует заготовку таблицы того же вида, что
 * настоящая, повторный — сразу последний прочитанный месяц из памяти страницы (данные с ФИО в
 * хранилище браузера не кладём). Новый месяц не стирает старый: прежние строки приглушаются, пока
 * едут новые, и сменяются проявлением; так же сменяются страницы и фильтры.
 */

// Пока мост догружает звонки прошлых суток, раздел перечитывает месяц сам — иначе счётчики
// звонков так и остались бы неполными до следующего захода.
const PENDING_RELOAD_MS = 20000;
const PENDING_RELOAD_LIMIT = 15;
const SKELETON_ROWS = 15;

/*
 * Движение — кривыми сайта, а не своими: появление — как у окон и выпадашек (window-in,
 * popover-in в tailwind.config.cjs: бросок и мягкое приземление), смена высоты и сдвиг страниц —
 * как у рамки окна (windowResize.js: разгон и торможение). Одно нажатие — одно движение: таблица
 * меняет высоту тем же ходом, каким проявляются её новые строки, а не прыгает и догоняет.
 */
export const EASE_APPEAR = [0.16, 1, 0.3, 1];
const EASE_MOVE = [0.4, 0, 0.2, 1];
const EASE_LEAVE = [0.4, 0, 1, 1];
const PAGE_SHIFT_PX = 14;

/*
 * Блок, который меняет высоту плавно, а не за кадр: отфильтровали до двух строк, открыли короткую
 * последнюю страницу, догрузился месяц. Замер — ResizeObserver на содержимом: он срабатывает после
 * раскладки, но до отрисовки, и рамка начинает движение со старой высоты в том же кадре. Перехватив
 * незаконченное движение, следующее начинается с текущей высоты, а не со старой.
 */
export function AnimatedHeight({ children, className = '' }) {
    const outerRef = useRef(null);
    const innerRef = useRef(null);
    const heightRef = useRef(null);
    const animationRef = useRef(null);
    const reduceMotion = useReducedMotion();
    useLayoutEffect(() => {
        const outer = outerRef.current;
        const inner = innerRef.current;
        if (!outer || !inner || typeof ResizeObserver !== 'function') return undefined;
        heightRef.current = inner.offsetHeight;
        const observer = new ResizeObserver(() => {
            const next = inner.offsetHeight;
            const previous = heightRef.current;
            heightRef.current = next;
            if (reduceMotion || previous == null || typeof outer.animate !== 'function') return;
            const from = animationRef.current ? outer.getBoundingClientRect().height : previous;
            animationRef.current?.cancel();
            const motionFrames = windowResizeMotion(from, next);
            if (!motionFrames) return;
            outer.style.overflow = 'hidden';
            const animation = outer.animate(motionFrames.frames, { duration: motionFrames.duration, easing: motionFrames.easing });
            animationRef.current = animation;
            const done = () => {
                if (animationRef.current !== animation) return;
                animationRef.current = null;
                outer.style.overflow = '';
            };
            animation.onfinish = done;
            animation.oncancel = done;
        });
        observer.observe(inner);
        return () => {
            observer.disconnect();
            animationRef.current?.cancel();
        };
    }, [reduceMotion]);
    return (
        <div ref={outerRef} className={className}>
            <div ref={innerRef}>{children}</div>
        </div>
    );
}

// Память страницы: последний прочитанный месяц каждого отчёта у каждого, кто открывал окно.
// Повторный вход рисуется сразу готовым, а запрос обновляет его фоном.
const reportMemory = new Map();

const currentMonth = () => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
};

function useMonthReport({ path, apiBaseUrl, withAccessTokenHeader, memoryKey }) {
    const remembered = reportMemory.get(memoryKey) || null;
    const [state, setState] = useState({ report: remembered, error: null, loading: !remembered });
    const headersRef = useRef(withAccessTokenHeader);
    headersRef.current = withAccessTokenHeader;
    const requestRef = useRef(0);

    const load = useCallback(async (month, { quiet = false } = {}) => {
        if (!apiBaseUrl) return null;
        const id = requestRef.current + 1;
        requestRef.current = id;
        if (!quiet) setState((prev) => ({ ...prev, loading: true, error: null }));
        try {
            const build = headersRef.current;
            const query = month ? `?month=${encodeURIComponent(month)}` : '';
            const response = await fetch(`${apiBaseUrl}${path}${query}`, {
                headers: build ? build({ Accept: 'application/json' }) : { Accept: 'application/json' },
                credentials: 'include',
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(body?.error || `Сервер ответил ${response.status}`);
            if (requestRef.current === id) {
                reportMemory.set(memoryKey, body);
                setState({ report: body, error: null, loading: false });
            }
            return body;
        } catch (error) {
            // Тихое перечитывание (догрузка звонков) не гасит уже показанный месяц.
            if (requestRef.current === id) {
                setState((prev) => ({ ...prev, loading: false, error: quiet ? prev.error : (error?.message || 'Отчёт недоступен') }));
            }
            return null;
        }
    }, [apiBaseUrl, path, memoryKey]);

    return { ...state, load };
}

/* Ближайший прокручиваемый предок: внутри окна листается его тело, а не страница. */
const scrollParent = (node) => {
    let box = node?.parentElement || null;
    while (box && !/(auto|scroll)/.test(window.getComputedStyle(box).overflowY)) box = box.parentElement;
    return box;
};

/*
 * Состояние одного отчёта окна: месяц с сервера, фильтры, страница, журнал и выгрузка. `active` —
 * окно открыто и показывает этот отчёт: только тогда он читает месяц. Отчёт, на который не
 * переключались, сервер не спрашивают вовсе, а ушедший с экрана хранит фильтры и страницу.
 */
export function useShiftReport(config, { active, apiBaseUrl, withAccessTokenHeader, userId, showToast }) {
    const memoryKey = `${config.key}:${userId ?? ''}`;
    const { report, error, loading, load } = useMonthReport({ path: config.path, apiBaseUrl, withAccessTokenHeader, memoryKey });
    const reduceMotion = useReducedMotion();
    // Фильтры — от запомненного месяца, а без него — от текущего: шапка стоит на месте с первого кадра.
    const [filters, setFilters] = useState(() => ({ ...defaultFilters(report), month: report?.month || currentMonth() }));
    const [page, setPage] = useState(1);
    const [journalShift, setJournalShift] = useState(null);
    const [exporting, setExporting] = useState(false);
    const toastRef = useRef(showToast);
    toastRef.current = showToast;
    const tableTopRef = useRef(null);
    const reportRef = useRef(report);
    reportRef.current = report;

    // Каждое открытие отчёта перечитывает показанный месяц: с памятью — фоном, без неё — текущий.
    useEffect(() => {
        if (!active) return;
        load(reportRef.current?.month || reportMemory.get(memoryKey)?.month || '');
    }, [active, load, memoryKey]);

    // Ответ сервера назвал месяц (первый вход шлёт пустой) — фильтр месяца встаёт на него.
    useEffect(() => {
        if (report?.month) setFilters((prev) => (prev.month === report.month || loading ? prev : { ...prev, month: report.month }));
    }, [report?.month, loading]);

    // Данные ещё догружаются (у линии — звонки прошлых суток) — перечитываем месяц сам, тихо, ограниченно.
    const pending = config.pendingNotice ? config.pendingNotice(report) : null;
    const reloadsRef = useRef(0);
    useEffect(() => {
        if (!active || !pending || reloadsRef.current >= PENDING_RELOAD_LIMIT) return undefined;
        const timer = window.setTimeout(() => {
            reloadsRef.current += 1;
            load(report.month, { quiet: true });
        }, PENDING_RELOAD_MS);
        return () => window.clearTimeout(timer);
    }, [active, report, load, pending]);

    const columns = useMemo(() => config.columns(report), [config, report]);
    const sameMonth = Boolean(report) && report.month === filters.month;
    const rows = useMemo(() => (sameMonth ? filterShifts(report.shifts, filters) : []), [sameMonth, report, filters]);
    const switchingMonth = loading && Boolean(report) && !sameMonth;
    // Пока едет другой месяц, на экране остаются прежние строки, приглушённые: таблица не
    // схлопывается в заготовку на полсекунды, а сменяется проявлением, когда придут новые.
    const lastRowsRef = useRef([]);
    if (sameMonth) lastRowsRef.current = rows;
    const shownRows = sameMonth ? rows : (switchingMonth ? lastRowsRef.current : []);
    const paged = useMemo(() => pageSlice(shownRows, page), [shownRows, page]);
    const counts = useMemo(() => (sameMonth ? groupCounts(report.shifts, filters) : null), [sameMonth, report, filters]);
    const operatorChoices = useMemo(() => operatorOptions(report, filters.group), [report, filters.group]);
    const today = report?.generated_at ? report.generated_at.slice(0, 10) : '';
    const monthLabel = (report?.months || []).find((m) => m.value === filters.month)?.label || monthLabelOf(filters.month);
    const filtered = isFiltered(filters, report);
    const firstLoad = !report;

    // Любая смена фильтра — снова первая страница: «страница 7» другой выборки ничего не значит.
    const filterKey = `${filters.month}|${filters.group}|${(filters.operatorIds || []).join(',')}|${(filters.days || []).join(',')}`;
    useEffect(() => { setPage(1); }, [filterKey]);
    const shownViewRef = useRef({ filterKey, page: 1, shift: 0 });

    const closeJournal = useCallback(() => setJournalShift(null), []);
    const update = useCallback((patch) => setFilters((prev) => ({ ...prev, ...patch })), []);
    const changeGroup = useCallback((group) => setFilters((prev) => ({
        ...prev, group, operatorIds: keepOperators(report, group, prev.operatorIds),
    })), [report]);
    const changeMonth = useCallback((month) => {
        reloadsRef.current = 0;
        setJournalShift(null);
        setFilters((prev) => ({ ...prev, month, days: [] }));
        // Месяц не загрузился — фильтр возвращается к показанному, а не висит над чужими строками.
        load(month).then((body) => {
            if (!body) setFilters((prev) => ({ ...prev, month: reportRef.current?.month || prev.month }));
        });
    }, [load]);
    // День переключается от ПРЕДЫДУЩЕГО состояния: два быстрых щелчка подряд иначе оставили бы один.
    const toggleDay = useCallback((iso) => setFilters((prev) => ({
        ...prev,
        days: (prev.days || []).includes(iso) ? prev.days.filter((day) => day !== iso) : [...(prev.days || []), iso].sort(),
    })), []);
    const setDays = useCallback((days) => update({ days: [...days].sort() }), [update]);
    const reset = useCallback(() => {
        const base = defaultFilters(report);
        setJournalShift(null);
        setFilters(base);
        if (base.month && base.month !== report?.month) load(base.month);
    }, [report, load]);

    const goToPage = useCallback((next) => {
        setPage(next);
        // Новая страница начинается с шапки таблицы, а не с того места, где кончилась прошлая.
        const node = tableTopRef.current;
        const box = scrollParent(node);
        const top = box ? box.getBoundingClientRect().top : 0;
        if (node && node.getBoundingClientRect().top < top) node.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
    }, [reduceMotion]);

    const download = useCallback(async () => {
        setExporting(true);
        try {
            const build = withAccessTokenHeader;
            const response = await fetch(`${apiBaseUrl}${config.exportPath}?${exportQuery(filters)}`, {
                credentials: 'include', headers: build ? build() : {},
            });
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                throw new Error(payload?.error || `Сервер ответил ${response.status}`);
            }
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = config.exportFileName(filters.month);
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.URL.revokeObjectURL(url);
        } catch (downloadError) {
            toastRef.current?.(`Не удалось выгрузить отчёт: ${downloadError?.message || downloadError}`, 'error');
        } finally {
            setExporting(false);
        }
    }, [apiBaseUrl, config, filters, withAccessTokenHeader]);

    let rowsShift = shownViewRef.current.shift;
    if (shownViewRef.current.filterKey !== filterKey) rowsShift = 0;
    else if (shownViewRef.current.page !== paged.page) rowsShift = paged.page > shownViewRef.current.page ? 1 : -1;
    useEffect(() => {
        shownViewRef.current = { filterKey, page: paged.page, shift: rowsShift };
    });

    return {
        report, error, loading, filters, journalShift, setJournalShift, closeJournal, exporting, download,
        update, changeGroup, changeMonth, toggleDay, setDays, reset, goToPage, columns, sameMonth, rows,
        switchingMonth, shownRows, paged, counts, operatorChoices, pending, today, monthLabel, filtered,
        firstLoad, filterKey, rowsShift, tableTopRef, reduceMotion,
        empty: !firstLoad && sameMonth && !rows.length,
    };
}

export const Field = ({ label, children, className = '' }) => (
    <div className={`flex min-w-0 flex-col gap-1.5 ${className}`}>
        <div className="px-0.5 text-[12px] font-semibold text-slate-500">{label}</div>
        {children}
    </div>
);

const triggerClass = (open) => `flex w-full items-center justify-between gap-2 rounded-xl bg-white px-3 py-2 text-left text-[13px] font-medium text-slate-700 ring-1 transition-all hover:bg-slate-50 active:scale-[0.99] ${
    open ? 'ring-2 ring-blue-500/60' : 'shadow-[0_1px_2px_rgba(15,23,42,0.04)] ring-slate-200/70'}`;

const Chevron = ({ open }) => (
    <svg width="14" height="14" viewBox="0 0 20 20" fill="none"
         className={`shrink-0 text-gray-400 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}>
        <path d="M5 8l5 5 5-5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
);

/*
 * Месяц — стрелками, как листают месяцы в Календаре macOS: соседний месяц на одно нажатие, а не
 * через раскрытие списка. Список с сервера (`months`) — только месяцы, за которые данные ещё
 * хранятся; дальше стрелка гаснет.
 */
const monthLabelMotion = {
    enter: (direction) => ({ opacity: 0, x: direction * 14 }),
    center: { opacity: 1, x: 0 },
    leave: (direction) => ({ opacity: 0, x: direction * -14 }),
};

export function MonthStepper({ months, month, onChange, busy }) {
    const { newer, older } = monthNeighbours(months, month);
    // Старый месяц уходит туда, куда листают: «‹» — вправо, а новый въезжает слева.
    const shownRef = useRef(month);
    const direction = month < shownRef.current ? -1 : month > shownRef.current ? 1 : 0;
    useEffect(() => { shownRef.current = month; }, [month]);
    const reduceMotion = useReducedMotion();
    const label = (months || []).find((item) => item.value === month)?.label || monthLabelOf(month);
    const arrow = (target, side) => (
        <button
            type="button"
            onClick={() => target && onChange(target.value)}
            disabled={!target}
            aria-label={side === 'older' ? 'Предыдущий месяц' : 'Следующий месяц'}
            className="grid h-[28px] w-[28px] shrink-0 place-items-center rounded-lg text-[17px] leading-none text-slate-500 transition hover:bg-slate-100 hover:text-slate-800 active:scale-95 disabled:pointer-events-none disabled:opacity-25"
        >
            {side === 'older' ? '‹' : '›'}
        </button>
    );
    return (
        <div className="flex h-[36px] items-center gap-1 rounded-xl bg-white px-1 shadow-[0_1px_2px_rgba(15,23,42,0.04)] ring-1 ring-slate-200/70">
            {arrow(older, 'older')}
            <div className="relative min-w-[150px] flex-1 overflow-hidden text-center text-[13px] font-semibold text-slate-800">
                <AnimatePresence initial={false} mode="popLayout" custom={reduceMotion ? 0 : direction}>
                    <motion.span
                        key={month}
                        className="block truncate"
                        custom={reduceMotion ? 0 : direction}
                        variants={monthLabelMotion}
                        initial="enter"
                        animate="center"
                        exit="leave"
                        transition={{ duration: 0.26, ease: EASE_MOVE }}
                    >
                        {label}
                    </motion.span>
                </AnimatePresence>
            </div>
            <span className={`grid w-4 place-items-center transition-opacity duration-200 ${busy ? 'opacity-100' : 'opacity-0'}`} aria-hidden="true">
                <FaIcon className="fas fa-spinner fa-spin text-[11px] text-slate-400" />
            </span>
            {arrow(newer, 'newer')}
        </div>
    );
}

const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

/*
 * Дни месяца: один, несколько или весь месяц. Календарь — того же вида, что эталонный пикер сайта
 * (`IosDateRangeCalendar`), но щелчок переключает день, а не задаёт диапазон: «несколько дней» —
 * не обязательно подряд. Дни без смен светлее — видно, где искать нечего. Внизу быстрый выбор:
 * «Вчера», «7 дней», «Весь месяц».
 */
export function DaysPicker({ month, monthLabel, value, onToggle, onSet, daysWithShifts, today, disabled }) {
    const [open, setOpen] = useState(false);
    const ref = useRef(null);
    const reduceMotion = useReducedMotion();
    useEffect(() => {
        if (!open) return undefined;
        const onDown = (event) => { if (ref.current && !ref.current.contains(event.target)) setOpen(false); };
        const onKey = (event) => { if (event.key === 'Escape') setOpen(false); };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', onDown);
            document.removeEventListener('keydown', onKey);
        };
    }, [open]);

    const { lead, days } = monthGrid(month);
    const selected = new Set(value || []);
    const known = Array.isArray(daysWithShifts);
    const withShifts = new Set(daysWithShifts || []);
    const presets = dayPresets(month, today);
    const presetActive = (preset) => preset.days.length === selected.size && preset.days.every((day) => selected.has(day));
    const chip = (active) => `flex-1 rounded-lg py-1.5 text-[12px] font-semibold transition active:scale-[0.97] ${
        active ? 'bg-[#eef4ff] text-[#1d4ed8] ring-1 ring-[#bfd4fb]' : 'bg-slate-100 text-slate-600 hover:bg-slate-200/80'}`;

    return (
        <div ref={ref} className="relative">
            <button type="button" data-days-picker className={triggerClass(open)} onClick={() => setOpen((v) => !v)}
                    aria-haspopup="dialog" aria-expanded={open} disabled={disabled}>
                <span className="flex min-w-0 items-center gap-2">
                    <FaIcon className="far fa-calendar text-[12px] text-slate-400" aria-hidden="true" />
                    <span className="truncate">{daysLabel(value)}</span>
                </span>
                <Chevron open={open} />
            </button>
            <AnimatePresence>
                {open ? (
                    <motion.div
                        className="absolute left-0 z-40 mt-2 w-[296px] rounded-2xl bg-white p-3 shadow-xl ring-1 ring-slate-200/70"
                        role="dialog" aria-label="Дни месяца"
                        initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -6 }}
                        animate={{ opacity: 1, y: 0, transition: { duration: 0.22, ease: EASE_APPEAR } }}
                        exit={{ opacity: 0, y: reduceMotion ? 0 : -3, transition: { duration: 0.14, ease: EASE_LEAVE } }}
                    >
                        <div className="mb-2 flex items-center justify-between px-1">
                            <span className="text-[13.5px] font-semibold text-slate-900">{monthLabel}</span>
                            <span className={`text-[12px] tabular-nums text-slate-400 transition-opacity ${selected.size ? 'opacity-100' : 'opacity-0'}`}>
                                выбрано {selected.size}
                            </span>
                        </div>
                        <div className="mb-1 grid grid-cols-7">
                            {WEEKDAYS.map((day) => (
                                <div key={day} className="flex h-7 items-center justify-center text-[10.5px] font-semibold uppercase text-slate-400">{day}</div>
                            ))}
                        </div>
                        <div className="grid grid-cols-7 gap-y-0.5">
                            {Array.from({ length: lead }, (_, i) => <div key={`e${i}`} className="h-9" />)}
                            {days.map((iso) => {
                                const future = today && iso > today;
                                const isOn = selected.has(iso);
                                let cls = 'flex h-8 w-8 items-center justify-center rounded-full text-[13px] tabular-nums transition-all duration-150 active:scale-90 ';
                                if (future) cls += 'cursor-not-allowed text-slate-200';
                                else if (isOn) cls += 'bg-blue-500 font-semibold text-white shadow-sm';
                                else if (known && !withShifts.has(iso)) cls += 'text-slate-300 hover:bg-slate-100';
                                else if (iso === today) cls += 'font-semibold text-blue-600 ring-1 ring-inset ring-blue-300 hover:bg-slate-100';
                                else cls += 'text-slate-700 hover:bg-slate-100';
                                return (
                                    <div key={iso} className="flex h-9 items-center justify-center">
                                        <button type="button" disabled={future} className={cls} onClick={() => onToggle(iso)}
                                                aria-pressed={isOn} aria-label={formatDayRu(iso)}>
                                            {Number(iso.slice(8))}
                                        </button>
                                    </div>
                                );
                            })}
                        </div>
                        <div className="mt-2.5 flex items-center gap-1.5 border-t border-slate-100 pt-2.5">
                            {presets.map((preset) => (
                                <button key={preset.key} type="button" className={chip(presetActive(preset))}
                                        onClick={() => onSet(preset.days)}>
                                    {preset.label}
                                </button>
                            ))}
                            <button type="button" className={chip(!selected.size)} onClick={() => onSet([])}>
                                Весь месяц
                            </button>
                        </div>
                    </motion.div>
                ) : null}
            </AnimatePresence>
        </div>
    );
}

export const zeroTone = (value) => (value ? 'text-slate-700' : 'text-slate-300');

/*
 * Шапка таблицы. «i» — общий InfoHint: он рисуется порталом, и край прокручиваемой таблицы его не
 * режет (у прежнего IosHint пузырёк обрезался краем карточки). Последнее слово подписи держится
 * вместе со значком, чтобы «i» на узкой колонке не уезжал строкой ниже один.
 */
const headLabel = (column) => {
    if (!column.hint) return <span>{column.label}</span>;
    const words = String(column.label).split(' ');
    const last = words.pop();
    return (
        <span>
            {words.length ? `${words.join(' ')} ` : ''}
            <span className="whitespace-nowrap">
                {last}
                <InfoHint text={column.hint} side={column.numeric ? 'right' : 'left'} className="ml-1 align-middle" />
            </span>
        </span>
    );
};

export const TableHead = ({ columns }) => {
    const head = 'px-2.5 pb-2.5 pt-1 align-bottom text-[11px] font-medium leading-tight text-slate-400';
    return (
        <thead>
            <tr className="border-b border-slate-200/80">
                {columns.map((column) => (
                    <th key={column.key} className={`${head} ${column.numeric ? 'text-right' : ''} ${
                        column.key === 'name' ? 'min-w-[150px]' : 'max-w-[96px]'}`}>
                        {headLabel(column)}
                    </th>
                ))}
                <th className="w-6" aria-hidden="true" />
            </tr>
        </thead>
    );
};

/* Заготовка — та же таблица с теми же заголовками и строками той же высоты: при первом входе
   раздел не перестраивается, а проявляет данные на месте полос. */
const SkeletonBody = ({ columns, rows = SKELETON_ROWS }) => (
    <tbody className="divide-y divide-slate-100" aria-hidden="true">
        {Array.from({ length: rows }, (_, index) => (
            <tr key={index}>
                {columns.map((column) => (
                    <td key={column.key} className="px-2.5 py-2.5">
                        <div className={`h-[13px] animate-pulse rounded-full bg-slate-200/70 ${
                            column.key === 'name' ? 'w-36' : column.numeric ? 'ml-auto w-14' : 'w-16'}`} />
                    </td>
                ))}
                <td />
            </tr>
        ))}
    </tbody>
);

/* Новые строки проявляются на месте: следующая страница чуть въезжает справа, предыдущая — слева,
   новая выборка (фильтр, месяц) — поднимается на пару пикселей. Сдвиг мелкий: таблицу листают,
   а не перелистывают экраном. Ячейки строки рисует отчёт (`renderCells`). */
export function ShiftRows({ rows, selectedKey, onOpen, motionKey, shift = 0, reduceMotion, renderCells }) {
    const from = shift ? { opacity: 0, x: shift * PAGE_SHIFT_PX } : { opacity: 0, y: 6 };
    return (
        <motion.tbody
            key={motionKey}
            className="divide-y divide-slate-100"
            initial={reduceMotion ? false : from}
            animate={{ opacity: 1, x: 0, y: 0 }}
            transition={{ duration: 0.3, ease: EASE_APPEAR }}
        >
            {rows.map((row) => {
                const selected = row.key === selectedKey;
                return (
                    <tr key={row.key} onClick={() => onOpen(row)}
                        className={`cursor-pointer transition-colors duration-150 ${selected ? 'bg-blue-50' : 'hover:bg-slate-50'}`}>
                        {renderCells(row, {
                            selected,
                            nameCell: (
                                <td className="whitespace-nowrap px-2.5 py-2.5">
                                    <button type="button"
                                            onClick={(event) => { event.stopPropagation(); onOpen(row); }}
                                            title="Журнал смены"
                                            className={`rounded text-left font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
                                                selected ? 'text-blue-700' : 'text-slate-900'}`}>
                                        {row.name}
                                    </button>
                                </td>
                            ),
                        })}
                        <td className="py-2.5 pr-2 text-right text-[11px] text-slate-300">
                            <FaIcon className="fas fa-chevron-right" aria-hidden="true" />
                        </td>
                    </tr>
                );
            })}
        </motion.tbody>
    );
}

/* Время с пометкой «не зафиксировано»: серым с пунктиром и пояснением при наведении — без цвета. */
export const MissingMoment = ({ missing, title, children }) => (
    <span className={missing ? 'underline decoration-slate-300 decoration-dotted underline-offset-[3px]' : ''}
          title={title}>
        {children}
    </span>
);

export function ShiftReportExcelButton({ ctl }) {
    return (
        <button type="button" className={`${iosBtnGhost} disabled:opacity-40`} onClick={ctl.download}
                disabled={ctl.exporting || !ctl.rows.length} title="Те же строки и колонки — файлом Excel">
            <FaIcon className={`fas ${ctl.exporting ? 'fa-spinner fa-spin' : 'fa-file-excel'}`} aria-hidden="true" />
            <span className="hidden sm:inline">Выгрузить в Excel</span>
            <span className="sm:hidden">Excel</span>
        </button>
    );
}

/*
 * Тело окна одного отчёта: карточка фильтров и карточка смен. Группа видна, только если групп в
 * отчёте больше одной (у чата группа одна — «Верификаторы», и сегмент из одного варианта был бы шумом).
 */
export function ShiftReportBody({ ctl, config }) {
    const tableBody = (ctl.firstLoad || (ctl.switchingMonth && !ctl.shownRows.length))
        ? <SkeletonBody columns={ctl.columns} />
        : (
            <ShiftRows rows={ctl.paged.rows} selectedKey={ctl.journalShift?.key} onOpen={ctl.setJournalShift}
                       motionKey={`${ctl.filterKey}|${ctl.paged.page}`} shift={ctl.rowsShift}
                       reduceMotion={ctl.reduceMotion} renderCells={config.renderCells} />
        );
    return (
        <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
            <div className={`${iosCard} p-4 sm:p-5`}>
                <div className={`grid grid-cols-1 items-end gap-x-4 gap-y-3.5 sm:grid-cols-2 ${config.filtersGrid}`}>
                    <Field label="Месяц">
                        <MonthStepper months={ctl.report?.months} month={ctl.filters.month} onChange={ctl.changeMonth}
                                      busy={ctl.loading && !ctl.firstLoad} />
                    </Field>
                    {config.groupFilter ? (
                        <Field label="Группа">
                            <IosSegmented value={ctl.filters.group} options={groupOptions(ctl.report, ctl.counts)}
                                          onChange={ctl.changeGroup} ariaLabel="Группа" slide />
                        </Field>
                    ) : null}
                    <Field label="Оператор">
                        <CustomSelect
                            variant="ios"
                            multiple
                            searchable
                            bulkActions
                            value={ctl.filters.operatorIds}
                            onChange={(ids) => ctl.update({ operatorIds: ids })}
                            options={ctl.operatorChoices}
                            placeholder="Все операторы"
                            renderValue={(ids) => operatorsLabel(ids, ctl.operatorChoices)}
                            textClassName="text-[13px] font-medium text-slate-700"
                            ariaLabel="Оператор"
                            disabled={ctl.firstLoad}
                        />
                    </Field>
                    <Field label="Дни">
                        <DaysPicker
                            month={ctl.filters.month}
                            monthLabel={ctl.monthLabel}
                            value={ctl.filters.days}
                            onToggle={ctl.toggleDay}
                            onSet={ctl.setDays}
                            daysWithShifts={ctl.sameMonth ? ctl.report.days_with_shifts : null}
                            today={ctl.today}
                            disabled={ctl.firstLoad}
                        />
                    </Field>
                    <div className={`${ctl.filtered ? 'flex' : 'hidden xl:flex'} h-[36px] items-center sm:col-span-2 xl:col-span-1 xl:justify-end`}>
                        <button type="button" onClick={ctl.reset}
                                className={`${iosBtnGhost} transition-opacity duration-200 ${ctl.filtered ? 'opacity-100' : 'pointer-events-none opacity-0'}`}
                                tabIndex={ctl.filtered ? 0 : -1} aria-hidden={!ctl.filtered}>
                            <FaIcon className="fas fa-rotate-left" aria-hidden="true" />
                            Сбросить
                        </button>
                    </div>
                </div>
            </div>

            <div ref={ctl.tableTopRef} data-activity-table className={`${iosCard} scroll-mt-4 p-4 sm:p-5`}>
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-0.5">
                    <div className="flex items-center gap-2.5 text-[15px] font-semibold text-slate-500">
                        <FaIcon className="fas fa-list-ul" aria-hidden="true" />
                        <span>Смены · {ctl.monthLabel}</span>
                    </div>
                    <div className={`text-[12.5px] tabular-nums text-slate-500 transition-opacity duration-200 ${ctl.switchingMonth ? 'opacity-50' : ''}`}>{summaryLabel(ctl.shownRows)}</div>
                </div>
                <AnimatedHeight>
                {/* Плашка только проявляется: рост карточки под неё делает AnimatedHeight — одно движение. */}
                {ctl.pending ? (
                    <div className="mb-3 inline-flex items-center gap-2 rounded-full bg-amber-50 px-2.5 py-0.5 text-[12px] text-amber-800 ring-1 ring-amber-200 motion-safe:animate-fade-in-soft">
                        <FaIcon className="fas fa-cloud-arrow-down" aria-hidden="true" />
                        {ctl.pending}
                    </div>
                ) : null}
                {ctl.error ? <div className="mb-3 text-[12.5px] text-rose-600 motion-safe:animate-fade-in-soft">{ctl.error}</div> : null}
                {ctl.empty ? (
                    <div className="rounded-xl bg-slate-50 px-4 py-10 text-center motion-safe:animate-fade-in-soft">
                        <div className="text-[14px] font-medium text-slate-600">{config.emptyTitle}</div>
                        <div className="mt-1 text-[12.5px] text-slate-400">{config.emptyHint}</div>
                    </div>
                ) : (
                    <div className={`-mx-1 overflow-x-auto transition-opacity duration-200 ${ctl.switchingMonth && ctl.shownRows.length ? 'opacity-50' : 'opacity-100'}`}>
                        <table className="w-full text-left text-[13px]">
                            <TableHead columns={ctl.columns} />
                            {tableBody}
                        </table>
                    </div>
                )}
                {!ctl.empty && ctl.paged.pageCount > 1 ? (
                    <div className="mt-3 border-t border-slate-100 pt-3">
                        <IosPager page={ctl.paged.page} pageCount={ctl.paged.pageCount} total={ctl.paged.total}
                                  from={ctl.paged.from} to={ctl.paged.to} onPage={ctl.goToPage} unit="смены" />
                    </div>
                ) : null}
                </AnimatedHeight>
            </div>
        </div>
    );
}
