import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ChevronDown, Download, Inbox, Loader2 } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import { IosBadge, iosBtnGhost, iosCard, prefersReducedMotion } from '../ui/ios';
import ShiftRequestComment from '../schedule/ShiftRequestComment';
import { foldMotion } from '../../utils/foldMotion';
import {
    LEAVE_EVENT_LABELS,
    LEAVE_JOURNAL_STATUSES,
    LEAVE_KIND_LABELS,
    LEAVE_STATUS_META,
    daysText,
    formatLeaveDateTime,
    formatLeavePeriod,
    isoDay,
    leaveApiError,
    leaveJournalFileName,
    leaveJournalKinds,
    leaveMonthLabel,
    saveLeaveFile,
    shiftLeaveMonth,
} from './leaveRequests';

/*
 * Журнал заявок на отпуск, Б/С и больничный — второй сегмент очереди
 * «Запросы» (просьба владельца 03.11.2026: «какой-никакой журнал и выгрузку,
 * без лишнего текста»). В журнале месяца — заявки любого статуса, чей период
 * задевает месяц; строка раскрывается историей заявки, «Выгрузить» отдаёт тот
 * же отбор в Excel. Пояснений на экране нет: месяц, отборы, строки.
 */

const EASE_MOVE = [0.4, 0, 0.2, 1];

const monthMotion = {
    enter: (direction) => ({ opacity: 0, x: direction * 14 }),
    center: { opacity: 1, x: 0 },
    leave: (direction) => ({ opacity: 0, x: direction * -14 }),
};

/* Месяц — стрелками, как «Месяц» отчёта активности операторов: старый уходит
   туда, куда листают, новый въезжает с другой стороны. */
function MonthStepper({ month, onChange }) {
    const shown = useRef(month);
    const direction = month < shown.current ? -1 : month > shown.current ? 1 : 0;
    useEffect(() => { shown.current = month; }, [month]);
    const reduceMotion = useReducedMotion();
    const arrow = (delta, label, glyph) => (
        <button
            type="button"
            onClick={() => onChange(shiftLeaveMonth(month, delta))}
            aria-label={label}
            className="grid h-[28px] w-[28px] shrink-0 place-items-center rounded-lg text-[17px] leading-none text-slate-500 transition hover:bg-slate-100 hover:text-slate-800 active:scale-95"
        >
            {glyph}
        </button>
    );
    return (
        <div className="flex h-[36px] w-full shrink-0 items-center rounded-xl bg-white px-1 shadow-[0_1px_2px_rgba(15,23,42,0.04)] ring-1 ring-slate-200/70 sm:w-auto">
            {arrow(-1, 'Предыдущий месяц', '‹')}
            <div className="relative mx-1 min-w-[124px] flex-1 overflow-hidden text-center text-[13px] font-semibold text-slate-800">
                <AnimatePresence initial={false} mode="popLayout" custom={reduceMotion ? 0 : direction}>
                    <motion.span
                        key={month}
                        className="block truncate"
                        custom={reduceMotion ? 0 : direction}
                        variants={monthMotion}
                        initial="enter"
                        animate="center"
                        exit="leave"
                        transition={{ duration: 0.26, ease: EASE_MOVE }}
                    >
                        {leaveMonthLabel(month)}
                    </motion.span>
                </AnimatePresence>
            </div>
            {arrow(1, 'Следующий месяц', '›')}
        </div>
    );
}

function History({ item, today }) {
    return (
        <div className="space-y-2 px-4 pb-3">
            <ShiftRequestComment text={item.reason} />
            <ol className="space-y-1.5">
                {(item.events || []).map((event, index) => (
                    // Не flex с gap и не сетка: оболочка телефона переносит такие строки
                    // и схлопывает сетку в столбик — время уехало бы над событием.
                    <li key={`${event.action}-${index}`} className="text-[12.5px] leading-snug">
                        <span className="inline-block w-[84px] align-top tabular-nums text-slate-400">
                            {formatLeaveDateTime(event.at, today)}
                        </span>
                        <span className="inline-block max-w-[calc(100%-84px)] align-top text-slate-700">
                            {LEAVE_EVENT_LABELS[event.action] || event.action}
                            {event.actor?.name && <span className="text-slate-500"> · {event.actor.name}</span>}
                            {event.action === 'proposed' && event.startDate && (
                                <span className="tabular-nums text-slate-500">
                                    {' · '}{formatLeavePeriod(event.startDate, event.endDate, today)}
                                </span>
                            )}
                            {/* Слова подачи и правки — это причина, она уже стоит выше. */}
                            {event.comment && !['created', 'edited'].includes(event.action) && (
                                <span className="block text-slate-500">«{event.comment}»</span>
                            )}
                        </span>
                    </li>
                ))}
            </ol>
            {item.excludeFromTenure && <div className="text-[12px] text-slate-500">Не учитывается в стаже</div>}
        </div>
    );
}

/* Строка раскрывается по высоте, как разделы окна заявки «Оплаты счетов»
   (расчёт — utils/foldMotion.js): строки ниже едут вместе с ней, история
   проявляется по ходу. Пока строка сворачивается, история остаётся в разметке. */
function JournalRow({ item, today, open, onToggle }) {
    const meta = LEAVE_STATUS_META[item.status] || LEAVE_STATUS_META.supervisor;
    const rowRef = useRef(null);
    const bodyRef = useRef(null);
    const running = useRef(null);
    const seenOpen = useRef(open);
    const [kept, setKept] = useState(open);
    if (open && !kept) setKept(true);

    useLayoutEffect(() => {
        if (seenOpen.current === open) return;
        seenOpen.current = open;
        const body = bodyRef.current;
        if (!body) {
            if (!open) setKept(false);
            return;
        }
        const previous = running.current;
        const from = previous || !open ? body.getBoundingClientRect().height : 0;
        const opacity = previous ? Number(getComputedStyle(body).opacity) : undefined;
        if (previous) {
            running.current = null;
            previous.cancel();
        }
        const fold = prefersReducedMotion() || typeof body.animate !== 'function'
            ? null
            : foldMotion(from, open ? body.offsetHeight : 0, opacity);
        if (!fold) {
            body.style.overflow = '';
            if (open) rowRef.current?.scrollIntoView({ block: 'nearest' });
            else setKept(false);
            return;
        }
        body.style.overflow = 'hidden';
        const animation = body.animate(fold.frames, {
            duration: fold.duration, easing: fold.easing, fill: open ? 'none' : 'forwards',
        });
        running.current = animation;
        animation.onfinish = () => {
            if (running.current !== animation) return;
            running.current = null;
            body.style.overflow = '';
            // Раскрытая у нижнего края окна — доводим до неё прокрутку.
            if (open) rowRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            else setKept(false);
        };
    }, [open]);

    useEffect(() => () => { if (running.current) running.current.cancel(); }, []);

    return (
        <div ref={rowRef} className="scroll-mb-3">
            <button
                type="button"
                onClick={onToggle}
                aria-expanded={open}
                className="flex w-full items-center gap-3 px-4 py-2.5 text-left outline-none transition-colors hover:bg-slate-50 active:bg-slate-100 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400"
            >
                <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13.5px] font-semibold text-slate-900">
                        {item.operator?.name || 'Сотрудник'}
                    </span>
                    {/* На узком экране строка переносится по точкам, а не режет даты. */}
                    <span className="block text-[12px] tabular-nums text-slate-500">
                        {LEAVE_KIND_LABELS[item.kind] || 'Заявка'}
                        {' · '}<span className="whitespace-nowrap">{formatLeavePeriod(item.startDate, item.endDate, today)}</span>
                        {' · '}<span className="whitespace-nowrap">{daysText(item.days)}</span>
                    </span>
                </span>
                <IosBadge tone={meta.tone} className="shrink-0">{meta.short}</IosBadge>
                <ChevronDown
                    size={15}
                    aria-hidden="true"
                    className={`shrink-0 text-slate-400 transition-transform duration-300 ease-[cubic-bezier(0.4,0,0.2,1)] motion-reduce:transition-none ${open ? 'rotate-180' : ''}`}
                />
            </button>
            {kept && (
                <div ref={bodyRef} aria-hidden={open ? undefined : true}>
                    <History item={item} today={today} />
                </div>
            )}
        </div>
    );
}

export default function LeaveJournal({ api, kinds, today, reloadKey = 0 }) {
    const [month, setMonth] = useState(() => String(today || isoDay(Date.now())).slice(0, 7));
    const [kind, setKind] = useState('');
    const [status, setStatus] = useState('');
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    // Сбой загрузки прячет список: под новым месяцем не должны стоять строки прошлого.
    const [error, setError] = useState('');
    const [fileError, setFileError] = useState('');
    const [openId, setOpenId] = useState(null);
    const [exporting, setExporting] = useState(false);

    useEffect(() => {
        let alive = true;
        setLoading(true);
        api.journal({ month, kind, status })
            .then((payload) => {
                if (!alive) return;
                setData(payload);
                setError('');
            })
            .catch((err) => { if (alive) setError(leaveApiError(err, 'Не удалось загрузить журнал')); })
            .finally(() => { if (alive) setLoading(false); });
        return () => { alive = false; };
    }, [api, month, kind, status, reloadKey]);

    const items = Array.isArray(data?.items) ? data.items : [];

    const download = async () => {
        setExporting(true);
        setFileError('');
        try {
            saveLeaveFile(await api.journalFile({ month, kind, status }), leaveJournalFileName(month));
        } catch (err) {
            setFileError(leaveApiError(err, 'Не удалось выгрузить журнал'));
        } finally {
            setExporting(false);
        }
    };

    const changeMonth = (next) => {
        setMonth(next);
        setOpenId(null);
    };

    return (
        <div className="space-y-3">
            {/* Телефон: месяц во всю ширину, отборы и выгрузка — строкой ниже. */}
            <div className="flex flex-wrap items-center gap-2">
                <MonthStepper month={month} onChange={changeMonth} />
                <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto sm:flex-1">
                    <CustomSelect
                        variant="ios"
                        value={kind}
                        onChange={setKind}
                        options={leaveJournalKinds(kinds)}
                        ariaLabel="Вид заявки"
                        className="min-w-0 flex-1"
                    />
                    <CustomSelect
                        variant="ios"
                        value={status}
                        onChange={setStatus}
                        options={LEAVE_JOURNAL_STATUSES}
                        ariaLabel="Статус заявки"
                        className="min-w-0 flex-1"
                    />
                    <button
                        type="button"
                        className={`${iosBtnGhost} shrink-0`}
                        disabled={exporting || items.length === 0}
                        onClick={download}
                        aria-label="Выгрузить в Excel"
                        title="Выгрузить в Excel"
                    >
                        {exporting
                            ? <Loader2 size={13} className="animate-spin text-slate-400" aria-hidden="true" />
                            : <Download size={13} className="text-slate-400" aria-hidden="true" />}
                        <span className="hidden sm:inline">Выгрузить</span>
                    </button>
                </div>
            </div>

            {(error || fileError) && (
                <div className="rounded-xl bg-rose-50 px-3.5 py-2.5 text-[12.5px] text-rose-700 ring-1 ring-rose-200/70">
                    {error || fileError}
                </div>
            )}

            {error && !loading ? null : data === null ? (
                // Заготовка в рост строк: окно не прыгает, когда журнал придёт.
                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`} aria-busy="true">
                    {[0, 1, 2, 3].map((i) => (
                        <div key={i} className="px-4 py-2.5">
                            <div className="h-[13px] w-40 rounded bg-slate-200/70" />
                            <div className="mt-[7px] h-[11px] w-56 rounded bg-slate-200/70" />
                        </div>
                    ))}
                </div>
            ) : items.length === 0 ? (
                <div className="px-4 py-10 text-center">
                    <div className="mx-auto mb-2 grid h-11 w-11 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                        <Inbox size={18} aria-hidden="true" />
                    </div>
                    <div className="text-[14px] font-semibold text-slate-700">Заявок нет</div>
                </div>
            ) : (
                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden transition-opacity duration-200 ${loading ? 'opacity-60' : ''}`}>
                    {items.map((item) => (
                        <JournalRow
                            key={`leave-journal-${item.id}`}
                            item={item}
                            today={today}
                            open={openId === item.id}
                            onToggle={() => setOpenId((current) => (current === item.id ? null : item.id))}
                        />
                    ))}
                </div>
            )}
            {data?.truncated && (
                <div className="px-1 text-[12px] text-slate-500">Показаны первые {items.length} — сузьте отбор</div>
            )}
        </div>
    );
}
