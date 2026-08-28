import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ChevronUp } from 'lucide-react';

/* Выбор ВРЕМЕНИ (ЧЧ:ММ) полем раздела — вместо системного `<input type="time">`.
 *
 * Зачем. Системное поле рисует не сайт, а браузер: свои сегменты «--:--», своя
 * стрелка, свой выпадающий список на 288 строк (шаг 5 минут за сутки) и своя
 * кнопка очистки. Рядом с карточками на rounded-2xl это деталь из другой
 * программы, а владелец назначил эталоном формы пикера выгрузку табло СЗоВ
 * (ChatExportControls в SzovWallboardView.jsx): триггер раздела + панель-карточка.
 *
 * ПОЧЕМУ ФОРМА ИМЕННО ТАКАЯ. Главный экран примитива — расписание офиса
 * (OfficeEditor): до 28 полей времени сразу, шаг 5 минут, значения кучкуются
 * вокруг 09:00–19:00. Если сделать «кнопка + раскрывающаяся панель», как у даты,
 * заполнение недели превратится в 28 раскрытий — это медленнее системного поля,
 * то есть провал задачи. Поэтому здесь пикер вывернут наизнанку:
 *   - основной способ ввода — КЛАВИАТУРА прямо в поле. Поле остаётся обычным
 *     текстовым: «0930», «9:30», «930», «9» — всё принимается и доводится до
 *     ЧЧ:ММ. Это быстрее системного поля, где приходится попадать в сегменты
 *     часов и минут по отдельности;
 *   - стрелки ↑/↓ двигают время на шаг, с Shift — на час: правка «на 15 минут
 *     позже» не требует перенабора;
 *   - панель со списком часов и минут — ВСПОМОГАТЕЛЬНАЯ и открывается только по
 *     явному нажатию на шеврон (или Alt+↓). Она узкая, в две колонки: 24 часа и
 *     минуты с шагом — вместо простыни из 288 строк системного списка.
 *
 * Панель уходит в ПОРТАЛ с fixed-координатами, а не в absolute: расписание офиса
 * живёт внутри IosModal со своей прокруткой, где absolute-панель нижних дней
 * недели обрезалась бы краем модалки. Устройство панели повторяет IosDatePicker
 * (recompute, ownerDocument, слушатели, флип вверх/вниз) — намеренно, чтобы
 * пикеры раздела вели себя одинаково и правились по одному образцу.
 *
 * Props:
 *   value         — время в 'ЧЧ:ММ'; '' — не задано
 *   onChange(hhmm)— отдаёт СТРОКУ 'ЧЧ:ММ' (или '' при очистке), НЕ событие
 *   step          — шаг минут, по умолчанию 5 (у системного поля это step={300} секунд)
 *   min / max     — границы 'ЧЧ:ММ'; за ними время не выставить ни набором, ни панелью
 *   allowEmpty    — можно ли оставить поле пустым (по умолчанию да)
 *   defaultTime   — что подставить, когда поле пустое, а человек жмёт стрелку
 *                   или открывает панель (по умолчанию '09:00' — рабочее утро)
 *   placeholder, disabled, className, inputClassName, ariaLabel, id
 */

const pad = (n) => String(n).padStart(2, '0');

/* Минуты от полуночи <-> 'ЧЧ:ММ'. Внутри примитива всё считается числом:
   строки сравнивать можно (формат отсортирован лексикографически), а вот
   прибавлять к ним шаг — нельзя. */
export const timeToMinutes = (hhmm) => {
    const m = /^(\d{1,2}):(\d{2})$/.exec(String(hhmm || '').trim());
    if (!m) return null;
    const h = Number(m[1]);
    const min = Number(m[2]);
    if (h > 23 || min > 59) return null;
    return h * 60 + min;
};

export const minutesToTime = (total) => `${pad(Math.floor(total / 60))}:${pad(total % 60)}`;

/* Разбор того, что человек НАБРАЛ. Возвращает минуты от полуночи или null,
 * если из набранного времени не выходит — тогда вызывающий откатывает поле к
 * прежнему значению, а не обнуляет его: мусор не должен стирать данные.
 *
 * Правила подобраны так, чтобы привычные способы набора совпали с ожиданием:
 *   «9» → 09:00, «19» → 19:00, «930» → 09:30, «0930»/«9:30»/«9-30» → 09:30.
 * Двузначное без разделителя — это ЧАС, а не минуты: в расписании офиса круглый
 * час набирают чаще, чем «сегодня в 09 минут».
 */
export const parseTimeInput = (raw) => {
    const text = String(raw ?? '').trim();
    if (!text) return null;
    const parts = text.split(/[^\d]+/).filter(Boolean);
    if (!parts.length) return null;
    let h;
    let m;
    if (parts.length >= 2) {
        // Разделитель набран явно — верим ему: «9-3» это 09:03, а не 09:30.
        h = Number(parts[0]);
        m = Number(parts[1].length === 1 ? parts[1] : parts[1].slice(0, 2));
    } else {
        const digits = parts[0].slice(0, 4);
        if (digits.length <= 2) { h = Number(digits); m = 0; }
        else { h = Number(digits.slice(0, digits.length - 2)); m = Number(digits.slice(-2)); }
    }
    if (!Number.isFinite(h) || !Number.isFinite(m)) return null;
    if (h > 23 || m > 59) return null;
    return h * 60 + m;
};

/* Маска ввода: двоеточие ставим сами, как только набор стал ОДНОЗНАЧНЫМ, — то
   есть на четвёртой цифре подряд. «1300» превращается в «13:00» под пальцами, и
   человеку не приходится держать в голове, разделит ли поле цифры само. Раньше
   это откладывалось до blur, и в поле стояло «1300» — вид, которого в остальном
   интерфейсе нет нигде.

   Почему ИМЕННО на четвёртой, а не на второй. Три цифры без разделителя — это
   Ч:ММ (см. parseTimeInput: одиночная группа читается как «9:30»). Маска,
   вставляющая двоеточие после двух цифр, превратила бы «930» в «93:0» — набор,
   который потом откатится как негодный, хотя человек набрал совершенно
   нормальное время. Явно набранный разделитель не трогаем вовсе: «9:3» это
   09:03, и дописывать туда нечего.

   Каретку возвращаем по ЧИСЛУ ЦИФР слева от неё, а не по позиции: вставка
   двоеточия сдвигает индексы, и «прыжок курсора» — ровно то, из-за чего
   форматирование на лету обычно и не делают. */
export const maskTimeInput = (raw, caret) => {
    const cleaned = String(raw ?? '').replace(/[^\d:.\-\s]/g, '').slice(0, 5);
    const hasSeparator = /[:.\-]/.test(cleaned);
    const digits = cleaned.replace(/\D/g, '');
    if (hasSeparator || digits.length < 4) return { text: cleaned, caret };
    const text = `${digits.slice(0, 2)}:${digits.slice(2, 4)}`;
    if (caret === null || caret === undefined) return { text, caret: text.length };
    const digitsBefore = cleaned.slice(0, caret).replace(/\D/g, '').length;
    return { text, caret: digitsBefore >= 2 ? digitsBefore + 1 : digitsBefore };
};

/* Приводим к шагу и границам. Округляем к БЛИЖАЙШЕМУ шагу, а не вниз: человек,
   набравший 09:33 при шаге 5, имел в виду 09:35 ровно с той же вероятностью,
   что и 09:30, но «вниз» всегда выглядит как потеря набранного. */
const snap = (total, step, minTotal, maxTotal) => {
    let value = step > 0 ? Math.round(total / step) * step : total;
    value = Math.max(0, Math.min(23 * 60 + 59, value));
    // 23:59 округлилось бы в следующие сутки; после обрезки возвращаемся на
    // последний шаг дня, иначе поле показало бы время не по сетке.
    if (step > 0 && value % step) value -= value % step;
    if (minTotal !== null && value < minTotal) value = minTotal;
    if (maxTotal !== null && value > maxTotal) value = maxTotal;
    return value;
};

const PANEL_WIDTH = 160;   // две колонки по 64px + отступы карточки
const PANEL_HEIGHT = 268;  // на глаз: заголовки колонок + список; нужна только чтобы решить, куда раскрывать

const colBtn = (active, disabled) => `w-full rounded-lg py-1 text-[13px] tabular-nums transition ${
    disabled ? 'cursor-not-allowed text-slate-300'
        : active ? 'bg-blue-600 font-semibold text-white'
            : 'text-slate-700 hover:bg-slate-100'}`;

export function IosTimePicker({
    value,
    onChange,
    step = 5,
    min = null,
    max = null,
    allowEmpty = true,
    defaultTime = '09:00',
    placeholder = '--:--',
    disabled = false,
    className = '',
    inputClassName = '',
    ariaLabel,
    id,
}) {
    const [open, setOpen] = useState(false);
    const [coords, setCoords] = useState(null);
    const [draft, setDraft] = useState(value || '');
    const boxRef = useRef(null);
    const inputRef = useRef(null);
    const panelRef = useRef(null);
    const hourColRef = useRef(null);
    const minuteColRef = useRef(null);
    const focusedRef = useRef(false);
    /* Значение, которое было ДО живой отдачи НЕОКОНЧЕННОГО набора; null — отдавать
       нечего. Набор из трёх цифр («240») уже разбирается как 02:40 и уходит наверх,
       хотя человек лишь на пути к «2400» — привычной записи полуночи. Когда дожатое
       оказывается негодным (час 24), откатываться надо к тому, что было ДО этой
       догадки: иначе поле «откатится» к собственной догадке, и в расписании офиса
       молча осядет 02:40 — время, которого никто не набирал. */
    const guessedFromRef = useRef(null);

    const minTotal = timeToMinutes(min);
    const maxTotal = timeToMinutes(max);
    const current = timeToMinutes(value);

    /* Пока поле в фокусе, оно принадлежит человеку: значение из props
       перерисовывает текст только снаружи фокуса. Иначе живая отдача onChange
       (см. ниже) затирала бы набираемое «0932» на «09:30» под пальцами. */
    useEffect(() => {
        if (!focusedRef.current) setDraft(value || '');
    }, [value]);

    /* Портал и размеры берём у ОКНА поля, а не у главного: часть разделов
       открывается в отдельном окне (Document Picture-in-Picture), и там
       document.body главного окна — чужой документ, панель просто не появится. */
    const ownerDocument = () => boxRef.current?.ownerDocument || document;

    const recompute = () => {
        const el = boxRef.current;
        if (!el) return;
        const view = el.ownerDocument?.defaultView || window;
        const r = el.getBoundingClientRect();
        const spaceBelow = view.innerHeight - r.bottom;
        const openUp = spaceBelow < PANEL_HEIGHT && r.top > spaceBelow;
        /* Панель шире поля, поэтому её левый край считаем сами: у правого края
           экрана раскрытие от левого края поля увело бы колонки за экран.
           Прижимаем к правому краю поля и держим отступ от окна. */
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

    /* Открытую панель прокручиваем к выбранным часу и минуте. Считаем сами, а
       не через scrollIntoView: тот тащит за собой и внешнюю прокрутку модалки,
       и поле уезжает из-под панели. */
    useLayoutEffect(() => {
        if (!open || !coords) return;
        [hourColRef, minuteColRef].forEach((ref) => {
            const col = ref.current;
            const active = col?.querySelector('[data-active="1"]');
            if (col && active) col.scrollTop = active.offsetTop - (col.clientHeight - active.offsetHeight) / 2;
        });
    }, [open, coords]);

    useEffect(() => {
        if (!open) return undefined;
        const onDown = (e) => {
            if (boxRef.current?.contains(e.target) || panelRef.current?.contains(e.target)) return;
            setOpen(false);
        };
        /* Esc гасит ПАНЕЛЬ и дальше не идёт: расписание офиса редактируют внутри
           IosModal, где необработанный Esc закрыл бы саму форму — человек потерял
           бы заполненную неделю, всего лишь передумав выбирать час. */
        const onKey = (e) => {
            if (e.key !== 'Escape') return;
            e.stopPropagation();
            e.preventDefault();
            setOpen(false);
            requestAnimationFrame(() => inputRef.current?.focus());
        };
        // Прокрутка ВНУТРИ панели её не закрывает (колонки часов и минут
        // прокручиваются); внешняя — пересчитывает позицию, чтобы панель
        // оставалась приклеена к полю.
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

    const emit = (total) => {
        const next = total === null ? '' : minutesToTime(snap(total, step, minTotal, maxTotal));
        if (next !== (value || '')) onChange(next);
        return next;
    };

    /* Набранное отдаём наверх, как только оно ПОЛНОЕ (набраны минуты), не дожидаясь
       ухода из поля: в расписании офиса кнопка «Сохранить» стоит рядом, и значение
       не должно зависеть от того, успел ли сработать blur до клика. Текст при этом
       не переписываем — доводим до ЧЧ:ММ на blur и Enter, чтобы не прыгал курсор.
       Но «полное» бывает двух сортов: «0930» и «9:30» дочитать нельзя, а три цифры
       без разделителя — ДОГАДКА на полпути: «240» это и 02:40, и начало «2400».
       Догадку отдаём (иначе «930» не сохранится по клику мимо поля), но запоминаем,
       к чему возвращаться, если следующая цифра сделает набор негодным. */
    const typed = (raw, caret) => {
        const { text, caret: nextCaret } = maskTimeInput(raw, caret);
        setDraft(text);
        if (nextCaret !== caret && inputRef.current) {
            // После setDraft React перерисует поле — каретку ставим уже поверх
            // нового значения, иначе она уедет в конец строки.
            const el = inputRef.current;
            requestAnimationFrame(() => {
                if (el.ownerDocument.activeElement === el) el.setSelectionRange(nextCaret, nextCaret);
            });
        }
        if (!text.trim()) { if (allowEmpty) { guessedFromRef.current = null; emit(null); } return; }
        const exact = /^\d{1,2}\s*[:.\-]\s*\d{2}$/.test(text) || /^\d{4}$/.test(text);
        const guess = !exact && /^\d{3}$/.test(text);
        if (!exact && !guess) return;
        const total = parseTimeInput(text);
        if (total === null) return;
        if (!guess) guessedFromRef.current = null;
        else if (guessedFromRef.current === null) guessedFromRef.current = value || '';
        emit(total);
    };

    /* Последнее ПОДТВЕРЖДЁННОЕ значение: props, а если наверх успела уйти догадка по
       неоконченному набору — то, что было до неё. */
    const confirmed = () => (guessedFromRef.current === null ? (value || '') : guessedFromRef.current);

    /* Откат негодного набора. Возвращаем не только текст поля, но и значение НАВЕРХ:
       догадка уже дошла до формы, и без этого расписание сохранилось бы с ней. */
    const rollback = () => {
        const back = confirmed();
        guessedFromRef.current = null;
        if (back !== (value || '')) onChange(back);
        setDraft(back);
    };

    // Приведение к ЧЧ:ММ. Непонятный набор откатываем к прежнему значению.
    const commit = () => {
        const text = draft.trim();
        if (!text) {
            if (allowEmpty) { guessedFromRef.current = null; emit(null); setDraft(''); } else rollback();
            return;
        }
        const total = parseTimeInput(text);
        if (total === null) { rollback(); return; }
        guessedFromRef.current = null;
        setDraft(emit(total));
    };

    const nudge = (delta) => {
        /* Считаем от того, что СЕЙЧАС в поле, а не от props: набор из одной-двух цифр
           («10») наверх ещё не ушёл, props держат прежние 09:00 — и стрелка выбрасывала
           бы набранное, ставя время, к которому человек не прикасался. Если в поле
           набрано негодное, отталкиваемся от подтверждённого значения. */
        const typedTotal = parseTimeInput(draft);
        const from = typedTotal !== null ? typedTotal : timeToMinutes(confirmed());
        const base = from === null ? (timeToMinutes(defaultTime) ?? 0) : from + delta;
        guessedFromRef.current = null;
        setDraft(emit(base));
    };

    const onKeyDown = (e) => {
        if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            if (e.altKey) { if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); } return; }
            e.preventDefault();
            const stepBy = e.shiftKey ? 60 : step;   // Shift — целый час: «открываемся на час позже»
            nudge(e.key === 'ArrowUp' ? stepBy : -stepBy);
            return;
        }
        if (e.key === 'Enter') { e.preventDefault(); commit(); setOpen(false); return; }
        if (e.key === 'Escape') {
            /* Панель закрыта, но набор не закончен — Esc отменяет НАБОР и дальше не
               идёт. Если отменять нечего, Esc отдаём наверх: там его ждёт модалка. */
            if (draft !== confirmed() || guessedFromRef.current !== null) {
                e.stopPropagation();
                e.preventDefault();
                rollback();
            }
        }
    };

    const anchor = current === null ? (timeToMinutes(defaultTime) ?? 9 * 60) : current;
    const outOfRange = (total) => (minTotal !== null && total < minTotal) || (maxTotal !== null && total > maxTotal);
    const minutes = [];
    for (let m = 0; m < 60; m += (step > 0 ? step : 1)) minutes.push(m);

    const panel = open && coords ? createPortal(
        <div
            ref={panelRef}
            role="dialog"
            aria-label={ariaLabel || 'Выбор времени'}
            style={{ position: 'fixed', left: coords.left, top: coords.top, bottom: coords.bottom, zIndex: 99999 }}
            className="w-[160px] rounded-2xl bg-white p-3 shadow-xl ring-1 ring-slate-200/70"
        >
            <div className="flex gap-2">
                {[
                    {
                        key: 'h',
                        ref: hourColRef,
                        label: 'часы',
                        items: Array.from({ length: 24 }, (_, h) => h),
                        // Час доступен, пока в нём есть хоть одна допустимая минута.
                        isOff: (h) => minutes.every((m) => outOfRange(h * 60 + m)),
                        isOn: (h) => current !== null && Math.floor(current / 60) === h,
                        text: (h) => pad(h),
                        pick: (h) => { guessedFromRef.current = null; setDraft(emit(h * 60 + (anchor % 60))); },
                    },
                    {
                        key: 'm',
                        ref: minuteColRef,
                        label: 'мин',
                        items: minutes,
                        isOff: (m) => outOfRange(Math.floor(anchor / 60) * 60 + m),
                        isOn: (m) => current !== null && current % 60 === m,
                        text: (m) => pad(m),
                        pick: (m) => { guessedFromRef.current = null; setDraft(emit(Math.floor(anchor / 60) * 60 + m)); setOpen(false); },
                    },
                ].map((col) => (
                    <div key={col.key} className="min-w-0 flex-1">
                        <div className="pb-1 text-center text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                            {col.label}
                        </div>
                        <div ref={col.ref} className="thin-scroll max-h-[196px] space-y-0.5 overflow-y-auto overscroll-contain pr-0.5">
                            {col.items.map((item) => {
                                const off = col.isOff(item);
                                const on = col.isOn(item);
                                return (
                                    <button
                                        key={item}
                                        type="button"
                                        data-active={on ? '1' : undefined}
                                        disabled={off}
                                        className={colBtn(on, off)}
                                        /* Фокус остаётся в поле: гасить его на каждый клик по часу
                                           значит гонять commit и терять место для дальнейшего набора.
                                           preventDefault висит на КНОПКАХ, а не на всей панели —
                                           иначе не потащить полосу прокрутки колонки. */
                                        onMouseDown={(e) => e.preventDefault()}
                                        onClick={() => col.pick(item)}
                                    >
                                        {col.text(item)}
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                ))}
            </div>
            {allowEmpty && value ? (
                <div className="mt-2 border-t border-slate-100 pt-2">
                    <button
                        type="button"
                        className="w-full rounded-lg py-1.5 text-[12px] font-semibold text-slate-500 transition hover:bg-slate-100"
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => { guessedFromRef.current = null; emit(null); setDraft(''); setOpen(false); }}
                    >
                        Очистить
                    </button>
                </div>
            ) : null}
        </div>,
        ownerDocument().body,
    ) : null;

    return (
        <div ref={boxRef} className={`relative inline-flex ${className}`}>
            <input
                ref={inputRef}
                id={id}
                type="text"
                inputMode="numeric"
                autoComplete="off"
                aria-label={ariaLabel}
                aria-haspopup="dialog"
                aria-expanded={open}
                disabled={disabled}
                placeholder={placeholder}
                value={draft}
                onChange={(e) => typed(e.target.value, e.target.selectionStart)}
                onKeyDown={onKeyDown}
                onFocus={(e) => { focusedRef.current = true; guessedFromRef.current = null; e.target.select(); }}
                onBlur={() => { focusedRef.current = false; commit(); }}
                /* Отступы слева и справа одинаковые, хотя шеврон стоит только
                   справа: иначе «09:30» съезжало бы влево от центра поля. */
                className={inputClassName || `w-[104px] h-9 rounded-xl border-0 bg-slate-100 px-6 text-center text-[14px]
                    tabular-nums text-slate-900 placeholder-slate-400 transition
                    focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70
                    disabled:cursor-not-allowed disabled:opacity-40`}
            />
            <button
                type="button"
                tabIndex={-1}          /* Tab идёт по полям времени, а не по шевронам: неделю заполняют набором */
                disabled={disabled}
                aria-hidden="true"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => { inputRef.current?.focus(); setOpen((v) => !v); }}
                className="absolute inset-y-0 right-1.5 flex w-5 items-center justify-center text-slate-400
                    transition hover:text-slate-600 disabled:opacity-40"
            >
                <ChevronUp size={13} className={`shrink-0 transition-transform ${open ? '' : 'rotate-180'}`} />
            </button>
            {panel}
        </div>
    );
}

export default IosTimePicker;
