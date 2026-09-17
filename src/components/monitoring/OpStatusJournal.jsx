import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import FaIcon from '../common/FaIcon';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';
import { APPLE_FONT } from '../ui/ios';
import { formatClock, formatDuration } from './szovWallboardShared';
import { OP_JOURNAL_PATH, opStatusChip } from './opWallboardShared';

/*
 * «Журнал статусов» сотрудника на табло ОП (ТЗ #339): боковая панель справа, прямо поверх табло,
 * без перехода на другую страницу. Панель не модальная и подложки у неё нет: табло за ней живое,
 * и нажатие на другое ФИО просто переключает журнал — так устроены инспекторы в macOS.
 *
 * Обновление в реальном времени без своего опроса. Журнал перечитывается, когда в снимке табло у
 * человека меняется статус (`refreshKey` — ключ и время последнего события), а длительность
 * текущего статуса досчитывается здесь же раз в секунду. Отдельный таймер на запрос дал бы журнал,
 * который то обгоняет строку таблицы, то отстаёт от неё, и лишнюю нагрузку на базу впустую.
 */

// Ближе этого к низу — «человек смотрит на текущий статус»: новый отрезок прокручивает список сам.
const STICK_TO_BOTTOM_PX = 80;

function useStatusJournal({ apiBaseUrl, withAccessTokenHeader, operatorId, refreshKey }) {
    const [state, setState] = useState({ data: null, error: null, loading: false, fetchedAt: 0 });
    const headersRef = useRef(withAccessTokenHeader);
    headersRef.current = withAccessTokenHeader;

    // Другой человек — прежний журнал сразу убираем, иначе полсекунды на экране чужие статусы.
    useEffect(() => {
        setState({ data: null, error: null, loading: false, fetchedAt: 0 });
    }, [operatorId]);

    useEffect(() => {
        if (!operatorId || !apiBaseUrl) return undefined;
        const controller = typeof AbortController === 'function' ? new AbortController() : null;
        setState((prev) => ({ ...prev, loading: true }));
        const build = headersRef.current;
        fetch(`${apiBaseUrl}${OP_JOURNAL_PATH}?operator_id=${encodeURIComponent(operatorId)}`, {
            headers: build ? build({ Accept: 'application/json' }) : { Accept: 'application/json' },
            credentials: 'include',
            signal: controller?.signal,
        })
            .then(async (response) => {
                const body = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(body?.error || `Сервер ответил ${response.status}`);
                setState({ data: body, error: null, loading: false, fetchedAt: Date.now() });
            })
            .catch((error) => {
                if (error?.name === 'AbortError') return;
                // Последний прочитанный журнал не гасим: ошибка обновления — не повод прятать данные.
                setState((prev) => ({ ...prev, loading: false, error: error?.message || 'Журнал недоступен' }));
            });
        return () => controller?.abort?.();
    }, [apiBaseUrl, operatorId, refreshKey]);

    return state;
}

/** Длительность открытого отрезка: секунды с сервера плюс время, прошедшее с ответа. */
function LiveDuration({ seconds, fetchedAt }) {
    const [, tick] = useState(0);
    useEffect(() => {
        const timer = window.setInterval(() => tick((n) => n + 1), 1000);
        return () => window.clearInterval(timer);
    }, []);
    const elapsed = fetchedAt ? Math.max(0, Math.floor((Date.now() - fetchedAt) / 1000)) : 0;
    return formatDuration(Number(seconds || 0) + elapsed);
}

const JournalRow = ({ segment, live, fetchedAt }) => {
    const chip = opStatusChip(segment);
    return (
        <div className="px-3.5 py-2.5">
            <div className="flex items-center justify-between gap-3">
                <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[12.5px] font-medium ${chip.className}`}>
                    {chip.label}
                </span>
                <span className="text-[13px] font-medium tabular-nums text-slate-700">
                    {live ? <LiveDuration seconds={segment.seconds} fetchedAt={fetchedAt} /> : formatDuration(segment.seconds)}
                </span>
            </div>
            <div className="mt-1 text-[12.5px] tabular-nums text-slate-500">
                {formatClock(segment.start_at)} — {live ? 'сейчас' : formatClock(segment.end_at)}
            </div>
        </div>
    );
};

const formatDay = (day) => {
    const match = String(day || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
    return match ? `${match[3]}.${match[2]}.${match[1]}` : '';
};

export default function OpStatusJournalPanel({
    operator, refreshKey, apiBaseUrl, withAccessTokenHeader, onClose, z = 85,
}) {
    const open = Boolean(operator?.id);
    // Имя держим и после закрытия: панель ещё полсекунды уезжает, и шапка не должна пустеть.
    const shownRef = useRef(operator);
    if (open) shownRef.current = operator;
    const reduceMotion = useReducedMotion();
    const isNarrowShell = useIsMobileShell();
    useScreenBackGesture(isNarrowShell && open, onClose);
    const { data, error, loading, fetchedAt } = useStatusJournal({
        apiBaseUrl, withAccessTokenHeader, operatorId: operator?.id, refreshKey,
    });
    const segments = data?.segments || [];
    const scrollRef = useRef(null);
    const pinnedRef = useRef(true);

    useEffect(() => {
        if (!open) return undefined;
        const onKey = (event) => { if (event.key === 'Escape') onClose(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [open, onClose]);

    useEffect(() => { pinnedRef.current = true; }, [operator?.id]);

    // Журнал идёт сверху вниз по времени, текущий статус — последний. Открыли — сразу внизу;
    // пришёл новый статус, а человек не листал историю, — список доезжает до него сам.
    const lastStart = segments.length ? segments[segments.length - 1].start_at : '';
    useLayoutEffect(() => {
        const node = scrollRef.current;
        if (node && pinnedRef.current) node.scrollTop = node.scrollHeight;
    }, [segments.length, lastStart]);

    const onScroll = () => {
        const node = scrollRef.current;
        if (node) pinnedRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < STICK_TO_BOTTOM_PX;
    };

    const motionProps = reduceMotion
        ? { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 }, transition: { duration: 0 } }
        : {
            initial: { opacity: 0, x: 28 },
            animate: { opacity: 1, x: 0, transition: { duration: 0.2, ease: [0.16, 1, 0.3, 1] } },
            exit: { opacity: 0, x: 28, transition: { duration: 0.14, ease: 'easeIn' } },
        };

    let body;
    if (!data && error) {
        body = <div className="px-1 text-[13px] text-rose-600">{error}</div>;
    } else if (!data) {
        body = <div className="px-1 text-[13px] text-slate-500">{loading ? 'Загружаем журнал…' : ''}</div>;
    } else if (!segments.length) {
        body = <div className="px-1 text-[13px] text-slate-500">Сегодня телефон сотрудника не присылал статусов.</div>;
    } else {
        body = (
            <div className="divide-y divide-slate-100 overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
                {segments.map((segment, index) => (
                    <JournalRow
                        key={`${segment.start_at}-${index}`}
                        segment={segment}
                        live={index === segments.length - 1 && !segment.end_at}
                        fetchedAt={fetchedAt}
                    />
                ))}
            </div>
        );
    }

    return createPortal(
        <AnimatePresence>
            {open ? (
                <motion.aside
                    key="op-status-journal"
                    role="dialog"
                    aria-label="Журнал статусов"
                    className="fixed inset-y-0 right-0 flex w-full flex-col border-l border-slate-200/80 bg-slate-50/95 shadow-[-12px_0_32px_rgba(15,23,42,0.10)] backdrop-blur-xl sm:w-[380px]"
                    style={{ zIndex: z, fontFamily: APPLE_FONT }}
                    {...motionProps}
                >
                    <div className="flex items-start justify-between gap-3 border-b border-slate-200/70 bg-white/80 px-5 pb-3 pt-4">
                        <div className="min-w-0">
                            <div className="text-[17px] font-semibold text-slate-900">Журнал статусов</div>
                            <div className="truncate text-[13px] text-slate-500">
                                {shownRef.current?.name}{data?.day ? ` · ${formatDay(data.day)}` : ''}
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
                    {data && error ? (
                        <div className="border-b border-amber-200/70 bg-amber-50 px-5 py-1.5 text-[12px] text-amber-800">{error}</div>
                    ) : null}
                    <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
                        {body}
                    </div>
                </motion.aside>
            ) : null}
        </AnimatePresence>,
        document.body,
    );
}
