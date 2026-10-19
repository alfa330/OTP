import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import FaIcon from '../common/FaIcon';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';
import { APPLE_FONT } from '../ui/ios';
import {
    OP_ACTIVITY_JOURNAL_PATH,
    filterJournal,
    formatClockHms,
    formatDayRu,
    formatHms,
    journalBar,
    journalFilterChips,
    journalQuery,
    journalStyle,
} from './opActivityShared';

/*
 * «Журнал статусов» смены в разделе «Активность операторов» (ТЗ #392, п. 5): боковая панель
 * справа, как у журнала на табло (OpStatusJournal.jsx), но про ЗАКОНЧЕННУЮ смену: ни «сейчас»,
 * ни досчёта секунд, ни перечитывания по статусу — смена уже не меняется. Сверху — полоса всей
 * смены по статусам и метки-итоги («Разговоры 03:20:08», «Паузы 00:51:24»…), они же фильтр
 * ленты: в смене ЯР бывает шесть сотен отрезков, и перерыв в такой ленте глазами не найти.
 * Ниже — отрезки по времени: цветной бейдж, длительность справа, точный интервал под ним.
 *
 * Прочитанный журнал помнится до перезагрузки страницы: щёлкая по строкам туда-обратно, человек
 * не ждёт одну и ту же смену дважды. Другая смена сменяет ленту проявлением, а не рывком.
 */

const journalCache = new Map();

function useShiftJournal({ apiBaseUrl, withAccessTokenHeader, shift }) {
    const [state, setState] = useState({ data: null, error: null, loading: false });
    const headersRef = useRef(withAccessTokenHeader);
    headersRef.current = withAccessTokenHeader;
    const key = shift ? journalQuery(shift) : '';

    useEffect(() => {
        if (!key || !apiBaseUrl) {
            setState({ data: null, error: null, loading: false });
            return undefined;
        }
        const cached = journalCache.get(key);
        if (cached) {
            setState({ data: cached, error: null, loading: false });
            return undefined;
        }
        const controller = typeof AbortController === 'function' ? new AbortController() : null;
        setState({ data: null, error: null, loading: true });
        const build = headersRef.current;
        fetch(`${apiBaseUrl}${OP_ACTIVITY_JOURNAL_PATH}?${key}`, {
            headers: build ? build({ Accept: 'application/json' }) : { Accept: 'application/json' },
            credentials: 'include',
            signal: controller?.signal,
        })
            .then(async (response) => {
                const body = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(body?.error || `Сервер ответил ${response.status}`);
                journalCache.set(key, body);
                setState({ data: body, error: null, loading: false });
            })
            .catch((error) => {
                if (error?.name === 'AbortError') return;
                setState({ data: null, error: error?.message || 'Журнал недоступен', loading: false });
            });
        return () => controller?.abort?.();
    }, [apiBaseUrl, key]);

    return state;
}

const ShiftBar = ({ segments }) => {
    const parts = journalBar(segments);
    if (!parts.length) return null;
    return (
        <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
            {parts.map((part, index) => (
                <div key={`${part.style}-${index}`} className={journalStyle(part.style).bar}
                     style={{ width: `${(part.share * 100).toFixed(3)}%` }} />
            ))}
        </div>
    );
};

const FilterChips = ({ chips, value, onChange }) => (
    <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Статусы журнала">
        {chips.map((chip) => {
            const active = chip.key === value;
            return (
                <button
                    key={chip.key}
                    type="button"
                    role="tab"
                    aria-selected={active}
                    onClick={() => onChange(chip.key)}
                    className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] font-medium ring-1 transition-all duration-150 active:scale-[0.97] ${
                        active
                            ? 'bg-[#eef4ff] text-[#1d4ed8] ring-[#bfd4fb]'
                            : 'bg-white text-slate-600 ring-slate-200/80 hover:bg-[#f6f6f4]'}`}
                >
                    {chip.label}
                    {chip.seconds !== null ? (
                        <span className={`tabular-nums ${active ? 'text-[#2563eb]' : 'text-slate-400'}`}>{formatHms(chip.seconds)}</span>
                    ) : null}
                </button>
            );
        })}
    </div>
);

const JournalRow = ({ segment }) => {
    const style = journalStyle(segment.style);
    const instant = !segment.seconds;
    return (
        <div className="px-3.5 py-2.5">
            <div className="flex items-center justify-between gap-3">
                <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[12.5px] font-medium ${style.chip}`}>
                    {segment.label}
                </span>
                <span className={`text-[13px] font-medium tabular-nums ${instant ? 'text-slate-400' : 'text-slate-700'}`}>
                    {formatHms(segment.seconds)}
                </span>
            </div>
            <div className="mt-1 text-[12.5px] tabular-nums text-slate-500">
                {instant ? formatClockHms(segment.start_at) : `${formatClockHms(segment.start_at)} — ${formatClockHms(segment.end_at)}`}
                {segment.note ? <span className="text-slate-400"> · {segment.note}</span> : null}
            </div>
        </div>
    );
};

export default function OpActivityJournalPanel({ shift, apiBaseUrl, withAccessTokenHeader, onClose, z = 85 }) {
    const open = Boolean(shift);
    // Строку держим и после закрытия: панель ещё полсекунды уезжает, и шапка не должна пустеть.
    const shownRef = useRef(shift);
    if (open) shownRef.current = shift;
    const shownShift = shownRef.current;
    const reduceMotion = useReducedMotion();
    const isNarrowShell = useIsMobileShell();
    useScreenBackGesture(isNarrowShell && open, onClose);
    const { data, error, loading } = useShiftJournal({ apiBaseUrl, withAccessTokenHeader, shift });
    const segments = data?.segments || [];
    const [filterKey, setFilterKey] = useState('all');
    const chips = journalFilterChips(segments);
    const shown = filterJournal(segments, chips.some((chip) => chip.key === filterKey) ? filterKey : 'all');
    const scrollRef = useRef(null);

    useEffect(() => {
        if (!open) return undefined;
        const onKey = (event) => { if (event.key === 'Escape') onClose(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [open, onClose]);

    // Другая смена — журнал с начала и без фильтра: прошлая смена читается сверху вниз, как день.
    useEffect(() => {
        setFilterKey('all');
        if (scrollRef.current) scrollRef.current.scrollTop = 0;
    }, [shift?.key]);
    const chooseFilter = (key) => {
        setFilterKey(key);
        if (scrollRef.current) scrollRef.current.scrollTop = 0;
    };

    /* Панель выезжает из-за правого края тем же ходом, каким появляются окна сайта (window-in:
       0,32 с, бросок и мягкое приземление), а уходит быстрее и с разгоном — уходящее не держат. */
    const motionProps = reduceMotion
        ? { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 }, transition: { duration: 0 } }
        : {
            initial: { opacity: 0, x: 44 },
            animate: { opacity: 1, x: 0, transition: { duration: 0.32, ease: [0.16, 1, 0.3, 1] } },
            exit: { opacity: 0, x: 36, transition: { duration: 0.2, ease: [0.4, 0, 1, 1] } },
        };

    let body;
    if (error) {
        body = <div className="px-1 text-[13px] text-rose-600">{error}</div>;
    } else if (!data) {
        body = <div className="px-1 text-[13px] text-slate-500">{loading ? 'Загружаем журнал смены…' : ''}</div>;
    } else if (!segments.length) {
        body = <div className="px-1 text-[13px] text-slate-500">За эту смену телефон не присылал статусов.</div>;
    } else {
        body = (
            <motion.div
                key={`${shift?.key}|${filterKey}`}
                className="space-y-3"
                initial={reduceMotion ? false : { opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
            >
                <div className="divide-y divide-slate-100 overflow-hidden rounded-2xl bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)] ring-1 ring-slate-200/70">
                    {shown.map((segment, index) => (
                        <JournalRow key={`${segment.start_at}-${index}`} segment={segment} />
                    ))}
                </div>
                {data.logout_missing ? (
                    <div className="px-1 text-[12px] leading-snug text-slate-500">
                        Телефон не прислал выход: смена показана до последнего события
                        в {formatClockHms(shownShift?.logout_at)}.
                    </div>
                ) : null}
            </motion.div>
        );
    }

    return createPortal(
        <AnimatePresence>
            {open ? (
                <motion.aside
                    key="op-activity-journal"
                    role="dialog"
                    aria-label="Журнал статусов"
                    className="fixed inset-y-0 right-0 flex w-full flex-col border-l border-slate-200/80 bg-slate-50/95 shadow-[-12px_0_32px_rgba(15,23,42,0.10)] backdrop-blur-xl sm:w-[400px]"
                    style={{ zIndex: z, fontFamily: APPLE_FONT }}
                    {...motionProps}
                >
                    <div className="border-b border-slate-200/70 bg-white/80 px-5 pb-3.5 pt-4">
                        <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                                <div className="text-[17px] font-semibold text-slate-900">Журнал статусов</div>
                                <div className="truncate text-[13px] text-slate-500">
                                    {shownShift?.name}{shownShift?.group_label ? ` · ${shownShift.group_label}` : ''}{shownShift?.date ? ` · ${formatDayRu(shownShift.date)}` : ''}
                                </div>
                                <div className="mt-0.5 text-[12.5px] tabular-nums text-slate-400">
                                    Смена {formatClockHms(shownShift?.login_at)} — {formatClockHms(shownShift?.logout_at)}
                                    {shownShift?.logout_next_day ? ' следующего дня' : ''}
                                </div>
                            </div>
                            <button
                                type="button"
                                onClick={onClose}
                                aria-label="Закрыть"
                                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 hover:text-slate-700 active:scale-[0.96]"
                            >
                                <FaIcon className="fas fa-xmark" aria-hidden="true" />
                            </button>
                        </div>
                        {segments.length ? (
                            <div className="mt-3 space-y-3">
                                <ShiftBar segments={segments} />
                                <FilterChips chips={chips} value={filterKey} onChange={chooseFilter} />
                            </div>
                        ) : null}
                    </div>
                    <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 pb-6 pt-3">
                        {body}
                    </div>
                </motion.aside>
            ) : null}
        </AnimatePresence>,
        document.body,
    );
}
