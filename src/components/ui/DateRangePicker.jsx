import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Calendar, Check, ChevronUp, ChevronLeft, ChevronRight } from 'lucide-react';
import {
    PERIOD_VIEW_LABELS, YEAR_PAGE, dayCells, initialPeriodView, monthCells, periodLabel, pickDayStep,
    presetMatches, selectionBand, yearCells, yearPageEnd,
} from './periodRange.js';

/* Пикер диапазона дат в стиле сайта: поповер с одним месяцем, выбор в два
 * клика (начало → конец) с подсветкой полосы по курсору и пресетами.
 *
 * Жил внутри WazzupChatsView; вынесен сюда, когда такой же понадобился разделу
 * «Чаты ChatApp» — чтобы календарь был один на оба, а не две копии.
 *
 * Внутри две части: `IosDateRangeCalendar` — сам календарь без обёртки (месяц,
 * сетка, пресеты и место под свою кнопку), `IosDateRangePicker` — привычный
 * чип-поповер над ним. Разделение появилось, когда выгрузке табло СЗоВ
 * понадобился тот же календарь, но раскрывающимся из кнопки «Выгрузить» и с
 * «Подтвердить» под ним: чипа с датой на стене быть не должно.
 *
 * Календарь ПЕРИОДА (задача #389, ИИ-оценка и «Аналитика» журнала оценок) —
 * тот же календарь с параметром `views`: сверху переключатель «Дни · Месяцы ·
 * Годы», и кроме дня или диапазона можно выбрать целый месяц или год. Правила
 * (подписи, какие месяцы недоступны, быстрые периоды) — в periodRange.js: их же
 * берёт календарь журнала, у которого своя сборка без Tailwind. */

const MONTHS_RU = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'май', 'июн',
    'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const DAYS_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

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
 * ChatApp), передаёт свои и не отдаёт вариант «Весь период».
 *
 * `footer` — место под своей кнопкой (например «Подтвердить»). Когда его нет,
 * выбор считается применённым сразу по второму клику, и обёртка закрывается.
 *
 * Черновик выбора инициализируется при МОНТИРОВАНИИ: обе обёртки рисуют
 * календарь только раскрытым, поэтому закрытие и открытие сами возвращают его
 * в исходное состояние — отдельная синхронизация по `open` не нужна.
 *
 * `roomy` — просторный вид для больших экранов (окно от 1536 px, ступень 2xl):
 * день 42 px и кегль 15 px вместо 36 и 13. Нужен разделу, который на большом
 * мониторе набран крупно («Учёт воды»): под полем в 48 px календарь прежнего
 * размера читался деталью с другого экрана. Все добавки — с префиксом 2xl,
 * поэтому до этой ширины и у остальных разделов календарь прежний. В режиме
 * `portal` обёртки-чипа ширина панели считается прежней константой: с roomy
 * этот режим пока никто не сочетает, первому такому вызывающему — перейти на
 * замер ширины карточки.
 *
 * `views` — календарь периода (#389): массив видов из periodRange.PERIOD_VIEWS.
 * Сверху переключатель видов; в «Месяцах» нажатие выбирает месяц целиком, в
 * «Годах» — год целиком. Высота сетки у всех видов одна (шесть недель): иначе
 * карточка прыгала бы при переключении вида и при листании месяцев. Быстрых
 * периодов здесь четыре — они встают сеткой 2 × 2 (в одну строку «Весь
 * период» не помещается), а совпавший с выбором подсвечен. Без `views`
 * календарь ровно прежний — его используют два десятка разделов. */
const VIEW_BODY_HEIGHT = 258;   // строка дней недели (32) + шесть недель (6 × 36 + 5 × 2)

export function IosDateRangeCalendar({ from, to, max, min, onChange, presets = null, footer = null,
                                      single = false, initialMonth = null, roomy = false, views = null }) {
    const today = isoDate(new Date());
    const [draft, setDraft] = useState({ from, to });   // выбор внутри календаря
    const [step, setStep] = useState(0);                // 0 — ждём начало, 1 — конец
    const [hover, setHover] = useState(null);
    const viewList = !single && Array.isArray(views) && views.length > 1 ? views : null;
    const [mode, setMode] = useState(() => (viewList ? initialPeriodView(from, to, viewList) : 'days'));
    /* `initialMonth` нужен пустому полю с границами: у даты, которую ещё не
       выбрали, месяц брать неоткуда, а сегодняшний у поля с min=«завтра» или
       max=«прошлый год» встретил бы человека полностью серым месяцем. */
    const [view, setView] = useState(() => {
        const [y, m] = (to || from || initialMonth || today).split('-').map(Number);
        return { y, m: m - 1 };
    });
    // Страницы годов кончаются годом верхней границы (обычно — текущим годом).
    const anchorYear = Number((max || today).slice(0, 4));
    const [yearsEnd, setYearsEnd] = useState(
        () => yearPageEnd(Number((to || from || today).slice(0, 4)), anchorYear));

    const pickDay = (iso, disabled) => {
        if (disabled) return;
        /* Одиночная дата выбирается ОДНИМ кликом: второй клик здесь нечего
           уточнять, а «шаг 1» оставил бы календарь ждать конца периода. */
        if (single) {
            setDraft({ from: iso, to: iso });
            onChange({ from: iso, to: iso });
            return;
        }
        // Начало → конец; тот же день вторым нажатием — один этот день.
        const next = pickDayStep(draft, step, iso);
        setDraft(next.draft);
        setStep(next.step);
        setHover(next.done ? null : iso);
        if (next.done) onChange(next.done);
    };

    const setPreset = (next) => {
        setDraft(next);
        setStep(0);
        setHover(null);
        onChange(next);
    };
    const prevMonth = () => setView((v) => (v.m === 0 ? { y: v.y - 1, m: 11 } : { y: v.y, m: v.m - 1 }));
    const nextMonth = () => setView((v) => (v.m === 11 ? { y: v.y + 1, m: 0 } : { y: v.y, m: v.m + 1 }));

    /* Шапка вида: месяц со стрелками у дней, год — у месяцев, страница лет — у
       годов. Вперёд за верхнюю границу месяцы и годы не листаются: там всё
       серое, и пустая страница читалась бы как «данных нет». */
    /* Годы до нижней границы (min) не показываются вовсе: раздел, который ведётся
       с 2026 года, не предлагает 2015-й — это был бы выбор заведомо пустого. */
    const pageYears = mode === 'years'
        ? yearCells(yearsEnd, { from: draft.from, to: draft.to, min, max, today })
            .filter((cell) => !(min && cell.range.to < min))
        : null;
    const header = mode === 'months'
        ? { title: String(view.y), prev: () => setView((v) => ({ ...v, y: v.y - 1 })),
            next: () => setView((v) => ({ ...v, y: v.y + 1 })),
            prevDisabled: Boolean(min) && `${view.y - 1}-12-31` < min,
            nextDisabled: Boolean(max) && `${view.y + 1}-01-01` > max,
            prevLabel: 'Предыдущий год', nextLabel: 'Следующий год' }
        : mode === 'years'
            ? { title: pageYears.length > 1 ? `${pageYears[0].label} – ${pageYears[pageYears.length - 1].label}`
                : (pageYears[0]?.label || String(yearsEnd)),
                prev: () => setYearsEnd((y) => y - YEAR_PAGE), next: () => setYearsEnd((y) => y + YEAR_PAGE),
                prevDisabled: Boolean(min) && `${yearsEnd - YEAR_PAGE}-12-31` < min,
                nextDisabled: Boolean(max) && `${yearsEnd + 1}-01-01` > max,
                prevLabel: 'Более ранние годы', nextLabel: 'Более поздние годы' }
            : { title: `${MONTHS_RU[view.m]} ${view.y}`, prev: prevMonth, next: nextMonth,
                prevDisabled: false, nextDisabled: false,
                prevLabel: 'Предыдущий месяц', nextLabel: 'Следующий месяц' };

    /* Месяц или год целиком — сразу выбор, как быстрый период; дни потом
       откроются на этом же месяце. */
    const pickWhole = (cell) => {
        if (cell.disabled) return;
        const [y, m] = cell.range.from.split('-').map(Number);
        setView({ y, m: m - 1 });
        setPreset({ from: cell.range.from, to: cell.range.to });
    };
    const wholeCells = mode === 'months'
        ? monthCells(view.y, { from: draft.from, to: draft.to, min, max, today })
        : pageYears;

    // границы подсветки: на шаге выбора конца — по позиции курсора
    const { lo, hi } = selectionBand(draft, step, hover);

    /* Ширину ячейка берёт у колонки сетки (2xl:w-full), а не своим числом в
       скобках: оболочка телефона правит всё, в чьём классе есть «w-[», и
       добавка с префиксом 2xl меняла бы календарь на телефоне.
       Календарь периода держит шесть недель всегда: месяц в пять недель
       иначе был бы ниже соседнего, и карточка прыгала бы при листании. */
    const cellSize = roomy ? ' 2xl:h-[42px] 2xl:w-full' : '';
    const cells = dayCells(view.y, view.m, { lo, hi, min, max, today, fill: Boolean(viewList) }).map((cell) => {
        if (!cell.iso) return <div key={cell.key} className={`h-9 w-9${cellSize}`} />;
        const { iso, disabled } = cell;
        let wrap = `relative flex h-9 w-9 items-center justify-center${cellSize}`;
        if (cell.inBand) wrap += ' bg-blue-500/10';
        else if (cell.bandStart) wrap += ' rounded-l-full bg-blue-500/10';
        else if (cell.bandEnd) wrap += ' rounded-r-full bg-blue-500/10';
        let btn = `flex h-8 w-8 items-center justify-center rounded-full text-[13px] transition ${
            roomy ? '2xl:size-[38px] 2xl:text-[15px] ' : ''}`;
        if (disabled) btn += 'cursor-not-allowed text-slate-300';
        else if (cell.isEnd) btn += 'bg-blue-500 font-semibold text-white shadow-sm';
        else if (cell.inBand) btn += 'text-blue-700 hover:bg-blue-500/15';
        else if (cell.today) btn += 'font-semibold text-blue-600 ring-1 ring-inset ring-blue-300 hover:bg-slate-100';
        else btn += 'text-slate-700 hover:bg-slate-100';
        return (
            <div key={iso} className={wrap}>
                <button type="button" disabled={disabled} className={btn}
                        onClick={() => pickDay(iso, disabled)}
                        onMouseEnter={() => step === 1 && !disabled && setHover(iso)}>
                    {cell.day}
                </button>
            </div>
        );
    });

    const navSize = roomy ? ' 2xl:h-8 2xl:w-8' : '';
    const footerPresets = presets || (single
        ? [{ label: 'Сегодня', range: () => ({ from: today, to: today }) }]
        : [
            { label: 'Сегодня', range: () => ({ from: today, to: today }) },
            { label: 'Весь период', range: () => ({ from: '', to: '' }) },
        ]);
    // Четыре быстрых периода в одну строку не встают — сеткой 2 × 2.
    const presetGrid = Boolean(viewList) && footerPresets.length > 3;
    const hint = mode === 'months' ? 'Выберите месяц'
        : mode === 'years' ? 'Выберите год'
            : step === 0 ? 'Выберите начало периода'
                /* «Конкретный день» (#389) — тот же день вторым нажатием; без
                   подсказки до этого не догадаться. */
                : viewList ? 'Выберите конец или тот же день ещё раз' : 'Выберите конец периода';

    return (
        <div className={`w-[268px] rounded-2xl bg-white p-3 shadow-xl ring-1 ring-slate-200/70${roomy ? ' 2xl:w-[326px] 2xl:p-4' : ''}`}>
            {viewList && (
                /* Не IosSegmented из ios.jsx: календарь — самостоятельный модуль
                   (его рендерит node-тест без сборщика), поэтому переключатель —
                   тот же вид из тех же классов, без импорта всего набора. */
                <div role="tablist" aria-label="Что выбрать" className="mb-2.5 flex rounded-[10px] bg-slate-100 p-[3px]">
                    {viewList.map((key) => (
                        <button key={key} type="button" role="tab" aria-selected={mode === key}
                                onClick={() => { setMode(key); setStep(0); setHover(null); }}
                                className={`flex-1 rounded-[8px] px-2 py-[5px] text-[12.5px] font-medium transition-all active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
                                    mode === key ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.10)]'
                                        : 'text-slate-500 hover:text-slate-800'}`}>
                            {PERIOD_VIEW_LABELS[key] || key}
                        </button>
                    ))}
                </div>
            )}
            <div className="mb-2 flex items-center justify-between">
                <button type="button" onClick={header.prev} aria-label={header.prevLabel} disabled={header.prevDisabled}
                        className={`grid h-7 w-7 place-items-center rounded-lg text-slate-500 transition hover:bg-slate-100 disabled:cursor-default disabled:text-slate-300 disabled:hover:bg-transparent${navSize}`}>
                    <ChevronLeft size={16} />
                </button>
                <span className={`text-[13.5px] font-semibold tabular-nums text-slate-900${roomy ? ' 2xl:text-[15.5px]' : ''}`}>
                    {header.title}
                </span>
                <button type="button" onClick={header.next} aria-label={header.nextLabel} disabled={header.nextDisabled}
                        className={`grid h-7 w-7 place-items-center rounded-lg text-slate-500 transition hover:bg-slate-100 disabled:cursor-default disabled:text-slate-300 disabled:hover:bg-transparent${navSize}`}>
                    <ChevronRight size={16} />
                </button>
            </div>
            {wholeCells ? (
                <div className="grid grid-cols-3 grid-rows-4 gap-1.5" style={{ height: VIEW_BODY_HEIGHT }}>
                    {wholeCells.map((cell) => (
                        <button key={cell.key} type="button" disabled={cell.disabled} title={cell.title}
                                aria-pressed={cell.selected} onClick={() => pickWhole(cell)}
                                className={`flex h-full w-full items-center justify-center rounded-xl text-[13px] tabular-nums transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
                                    cell.disabled ? 'cursor-not-allowed text-slate-300'
                                        : cell.selected ? 'bg-blue-500 font-semibold text-white shadow-sm'
                                            : cell.current ? 'font-semibold text-blue-600 ring-1 ring-inset ring-blue-300 hover:bg-slate-100'
                                                : 'text-slate-700 hover:bg-slate-100'}`}>
                            {cell.label}
                        </button>
                    ))}
                </div>
            ) : (
                <>
                    <div className="mb-1 grid grid-cols-7">
                        {DAYS_SHORT.map((d) => (
                            <div key={d} className={`flex h-7 w-9 items-center justify-center text-[10.5px] font-semibold uppercase text-slate-400${
                                roomy ? ' 2xl:h-8 2xl:w-full 2xl:text-[12px]' : ''}`}>
                                {d}
                            </div>
                        ))}
                    </div>
                    <div className="grid grid-cols-7 gap-y-0.5">{cells}</div>
                </>
            )}
            <div className={`mt-2.5 border-t border-slate-100 pt-2.5 ${presetGrid ? 'grid grid-cols-2 gap-2' : 'flex items-center gap-2'}`}>
                {footerPresets.map((p) => {
                    /* Совпавший с выбором быстрый период подсвечен мягким голубым с
                       галочкой — по нему видно, что выбрано («Неделя», а не
                       просто две даты). Только у календаря периода. */
                    const active = Boolean(viewList) && presetMatches(p, draft.from, draft.to);
                    return (
                        <button key={p.label} type="button" onClick={() => setPreset(p.range())} aria-pressed={viewList ? active : undefined}
                                className={`${presetGrid ? '' : 'flex-1 '}inline-flex items-center justify-center gap-1 rounded-lg py-1.5 text-[12px] font-semibold transition ${
                                    active ? 'bg-blue-50 text-blue-700 ring-1 ring-inset ring-blue-200'
                                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200/80'}${
                                    roomy ? ' 2xl:py-2 2xl:text-[13.5px]' : ''}`}>
                            {active && <Check size={12} strokeWidth={2.5} aria-hidden="true" />}
                            {p.label}
                        </button>
                    );
                })}
            </div>
            {/* Подсказка нужна только диапазону: там два клика и человеку
                надо знать, какой из них он сейчас делает. Над одиночной датой
                это была бы строка ради строки. */}
            {!single && (
                <p className={`mt-2 text-center text-[11px] text-slate-400${roomy ? ' 2xl:text-[12.5px]' : ''}`}>
                    {hint}
                </p>
            )}
            {footer}
        </div>
    );
}

/* Чип с диапазоном и календарь под ним. Выбор применяется сразу: второй клик по
 * дню (или пресет) отдаёт диапазон разделу и закрывает поповер.
 *
 * `triggerClassName` ЗАМЕНЯЕТ класс кнопки целиком — так же, как у одиночного
 * IosDatePicker (второй смысл того же имени в двух соседних примитивах был бы
 * ловушкой). Значит и состояния (наведение, фокус) берёт на себя вызывающий.
 * Нужен он там, где чип стоит в РЯДУ С ПОЛЯМИ формы: в фильтрах «Посылок»
 * поле-чип по своей ширине выбивалось из строки трёх селекторов. Кнопка внутри
 * — иконка, подпись и шеврон, поэтому своему классу обычно нужен
 * `[&>span]:flex-1`, иначе подпись не растягивается и шеврон уезжает к тексту. */
/* Размеры календаря для портала: ширина фиксирована классом карточки, высота —
 * прикидка на шестинедельный месяц с пресетами и подсказкой. */
const PANEL_WIDTH = 268;
const PANEL_HEIGHT = 392;
const TRIGGER_GAP = 8;
const EDGE_GAP = 8;

/* `portal` — календарь уходит в портал с fixed-координатами, как у одиночного
 * IosDatePicker и у CustomSelect. Нужен внутри модалок: их тело прокручивается
 * (`overflow-y-auto`), и absolute-поповер под чипом оно РЕЖЕТ — в окне «Найти
 * звонок» календарь не помещался целиком, видна была верхняя треть. Снаружи
 * модалок остаётся прежний absolute-поповер: там резать нечем, а портал —
 * лишние слушатели прокрутки. */
export function IosDateRangePicker({ from, to, max, min, onChange, presets = null,
                                    triggerClassName = '', portal = false, roomy = false, views = null }) {
    const [open, setOpen] = useState(false);
    const [coords, setCoords] = useState(null);
    const ref = useRef(null);
    const popRef = useRef(null);
    const triggerRef = useRef(null);

    useEffect(() => {
        if (!open) return undefined;
        // Клик внутри портала — это выбор дня, а не «клик снаружи».
        const h = (e) => {
            if (ref.current?.contains(e.target) || popRef.current?.contains(e.target)) return;
            setOpen(false);
        };
        /* Esc закрывает, как у эталона пикера (выгрузка табло СЗоВ), и
           возвращает фокус на чип — иначе он терялся бы на теле страницы. */
        const onKey = (e) => {
            if (e.key !== 'Escape') return;
            setOpen(false);
            triggerRef.current?.focus();
        };
        document.addEventListener('mousedown', h);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', h);
            document.removeEventListener('keydown', onKey);
        };
    }, [open]);

    const recompute = () => {
        const el = ref.current;
        if (!el) return;
        const view = el.ownerDocument?.defaultView || window;
        const r = el.getBoundingClientRect();
        const measured = popRef.current?.firstElementChild?.offsetHeight || PANEL_HEIGHT;
        const height = Math.min(measured, view.innerHeight - EDGE_GAP * 2);
        const spaceBelow = view.innerHeight - r.bottom - TRIGGER_GAP - EDGE_GAP;
        const spaceAbove = r.top - TRIGGER_GAP - EDGE_GAP;
        const openUp = spaceBelow < height && spaceAbove > spaceBelow;
        // Календарь шире чипа: у правого края экрана прижимаем его к правому краю чипа.
        let left = r.left;
        if (left + PANEL_WIDTH > view.innerWidth - EDGE_GAP) left = r.right - PANEL_WIDTH;
        let top = openUp ? r.top - TRIGGER_GAP - height : r.bottom + TRIGGER_GAP;
        // Fixed-панель за краем окна не прокрутить ничем — сдвигаем внутрь,
        // пусть даже наедет на чип: закрытый чип лучше обрубленного календаря.
        top = Math.max(EDGE_GAP, Math.min(top, view.innerHeight - EDGE_GAP - height));
        const next = {
            left: Math.round(Math.max(EDGE_GAP, left)), top: Math.round(top),
            maxHeight: Math.round(view.innerHeight - EDGE_GAP - top),
        };
        setCoords((prev) => (prev && prev.left === next.left && prev.top === next.top
            && prev.maxHeight === next.maxHeight ? prev : next));
    };

    useLayoutEffect(() => {
        if (!portal) return undefined;
        if (!open) { setCoords(null); return undefined; }
        recompute();
        // Второй расчёт — уже по настоящей высоте карточки (первый шёл по прикидке).
        const raf = requestAnimationFrame(recompute);
        const view = ref.current?.ownerDocument?.defaultView || window;
        // Прокрутка снаружи (тело модалки, страница) двигает чип — календарь
        // едет за ним; прокрутка внутри самого календаря позицию не трогает.
        const onScroll = (e) => {
            if (popRef.current && (popRef.current === e.target || popRef.current.contains(e.target))) return;
            recompute();
        };
        view.addEventListener('scroll', onScroll, true);
        view.addEventListener('resize', recompute);
        return () => {
            cancelAnimationFrame(raf);
            view.removeEventListener('scroll', onScroll, true);
            view.removeEventListener('resize', recompute);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, portal]);

    const calendar = (
        <IosDateRangeCalendar
            from={from} to={to} max={max} min={min} presets={presets} roomy={roomy} views={views}
            onChange={(next) => { onChange(next); setOpen(false); }}
        />
    );

    return (
        <div ref={ref} className="relative">
            <button ref={triggerRef} type="button" onClick={() => setOpen((o) => !o)}
                    aria-expanded={open} aria-haspopup="dialog"
                    className={triggerClassName
                        || `flex items-center gap-2 rounded-xl px-3 py-2 text-[13px] font-medium transition ${
                            open ? 'bg-white text-slate-900 ring-2 ring-blue-500/70'
                                 : 'bg-slate-100 text-slate-700 hover:bg-slate-200/80'}`}>
                <Calendar size={14} className="text-slate-400" />
                {/* У календаря периода целый месяц и год подписаны словами
                    («Октябрь 2026», «2026 год»), а не двумя датами. */}
                <span>{views ? periodLabel(from, to) : rangeLabel(from, to)}</span>
                <ChevronUp size={13} className={`text-slate-400 transition-transform ${open ? '' : 'rotate-180'}`} />
            </button>
            {open && !portal && (
                <div role="dialog" aria-label="Выбор периода"
                     className="absolute left-0 top-full z-50 mt-2 motion-safe:animate-popover-in">{calendar}</div>
            )}
            {open && portal && coords && createPortal(
                <div ref={popRef} role="dialog" className="motion-safe:animate-popover-in"
                     style={{ position: 'fixed', left: coords.left, top: coords.top,
                              maxHeight: coords.maxHeight, zIndex: 99999 }}>
                    {calendar}
                </div>,
                (ref.current?.ownerDocument || document).body,
            )}
        </div>
    );
}

export default IosDateRangePicker;
