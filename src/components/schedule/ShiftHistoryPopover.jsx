import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
    AlertCircle, CalendarOff, CalendarPlus, Clock, Loader2, Minus, PenLine, Plus, X,
} from 'lucide-react';
import { APPLE_FONT, iosGroupLabel } from '../ui/ios';
import {
    shiftHistoryActionLabel,
    shiftHistoryActorLabel,
    shiftHistoryTimeLabel,
    shiftHistoryTone,
    shiftHistoryWhenLabel,
} from './shiftHistoryFormat';

/*
 * История изменений по одной ячейке графика — «оператор + день».
 *
 * Живёт порталом в document.body: сетка графика скроллится по двум осям и
 * режет содержимое ячейки `overflow-hidden`, поэтому панель, вложенная в
 * ячейку, была бы обрезана. Позиционирование и правила закрытия сняты с
 * IosMenu (src/components/ui/ios.jsx): прокрутка закрывает панель, а не тащит
 * её за собой — ячейка уезжает из-под курсора, и «приклеенная» панель повисла
 * бы над чужой строкой.
 */

const PANEL_WIDTH = 380;
const VIEWPORT_GAP = 8;

const TONE_ICON = {
    green: 'bg-emerald-50 text-emerald-600',
    blue: 'bg-blue-50 text-blue-600',
    red: 'bg-rose-50 text-rose-600',
    sky: 'bg-sky-50 text-sky-600',
    slate: 'bg-slate-100 text-slate-500',
};

const ACTION_ICON = {
    added: Plus,
    removed: Minus,
    changed: PenLine,
    day_off_set: CalendarOff,
    day_off_cleared: CalendarPlus,
};

const MONTHS_RU = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];

const dayTitle = (dateStr) => {
    const parts = String(dateStr || '').split('-');
    if (parts.length !== 3) return dateStr || '';
    const day = Number(parts[2]);
    const month = Number(parts[1]) - 1;
    if (!Number.isFinite(day) || !MONTHS_RU[month]) return dateStr;
    return `${day} ${MONTHS_RU[month]} ${parts[0]}`;
};

const fullWhen = (iso) => {
    if (!iso) return '';
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return '';
    return parsed.toLocaleString('ru-RU');
};

const Block = ({ children }) => (
    <div className="flex flex-col items-center gap-2 px-6 py-10 text-center">{children}</div>
);

const HistoryRow = ({ entry }) => {
    const Icon = ACTION_ICON[entry.action] || Clock;
    const tone = TONE_ICON[shiftHistoryTone(entry)] || TONE_ICON.slate;
    const times = shiftHistoryTimeLabel(entry);
    const actor = shiftHistoryActorLabel(entry);

    return (
        <li className="flex items-start gap-3 px-3.5 py-2.5 transition hover:bg-slate-50/70">
            <span className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full ${tone}`}>
                <Icon size={15} />
            </span>

            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-1.5">
                    <span className="text-[13.5px] font-semibold text-slate-900">
                        {shiftHistoryActionLabel(entry)}
                    </span>
                    {times && (
                        <span className="text-[13.5px] tabular-nums text-slate-700">{times}</span>
                    )}
                </div>
                {/* Либо источник говорит сам за себя («Взята с аукциона»),
                    либо тут стоит ФИО. Никогда и то и другое сразу. */}
                {actor && <div className="mt-0.5 text-[12px] text-slate-500">{actor}</div>}
            </div>

            <time
                className="mt-0.5 shrink-0 text-[11.5px] tabular-nums text-slate-500"
                dateTime={entry.changedAt || undefined}
                title={fullWhen(entry.changedAt)}
            >
                {shiftHistoryWhenLabel(entry.changedAt)}
            </time>
        </li>
    );
};

function ShiftHistoryPopover({ target, onClose, fetchEntries }) {
    const panelRef = useRef(null);
    const [coords, setCoords] = useState(null);
    const [state, setState] = useState({ status: 'loading', items: [], error: '' });
    const requestSeqRef = useRef(0);

    const anchorRect = target?.anchorRect || null;
    const operatorId = target?.operatorId;
    const date = target?.date;

    const recompute = useCallback(() => {
        if (!anchorRect || typeof window === 'undefined') return;
        // Высоту берём по факту, если панель уже отрисована, иначе прикидываем:
        // на первом кадре измерять нечего.
        const height = panelRef.current?.offsetHeight || 260;
        const spaceBelow = window.innerHeight - anchorRect.bottom;
        const openUp = spaceBelow < height + 16 && anchorRect.top > spaceBelow;
        const left = Math.min(
            Math.max(VIEWPORT_GAP, Math.round(anchorRect.left)),
            Math.max(VIEWPORT_GAP, window.innerWidth - PANEL_WIDTH - VIEWPORT_GAP),
        );
        setCoords({
            left,
            top: openUp ? undefined : Math.round(anchorRect.bottom + 6),
            bottom: openUp ? Math.round(window.innerHeight - anchorRect.top + 6) : undefined,
        });
    }, [anchorRect]);

    useLayoutEffect(() => { recompute(); }, [recompute, state.status]);

    useEffect(() => {
        if (typeof window === 'undefined') return undefined;
        const onDoc = (e) => {
            if (panelRef.current?.contains(e.target)) return;
            onClose?.();
        };
        const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
        const onScroll = () => onClose?.();
        document.addEventListener('mousedown', onDoc);
        document.addEventListener('keydown', onKey);
        window.addEventListener('scroll', onScroll, true);
        window.addEventListener('resize', recompute);
        return () => {
            document.removeEventListener('mousedown', onDoc);
            document.removeEventListener('keydown', onKey);
            window.removeEventListener('scroll', onScroll, true);
            window.removeEventListener('resize', recompute);
        };
    }, [onClose, recompute]);

    useEffect(() => {
        if (operatorId == null || !date) return undefined;
        const seq = requestSeqRef.current + 1;
        requestSeqRef.current = seq;
        let cancelled = false;

        setState({ status: 'loading', items: [], error: '' });
        Promise.resolve(fetchEntries(operatorId, date))
            .then((items) => {
                // Ответ мог обогнать более свежий запрос — старые ответы отбрасываем.
                if (cancelled || requestSeqRef.current !== seq) return;
                setState({ status: 'ready', items: Array.isArray(items) ? items : [], error: '' });
            })
            .catch((error) => {
                if (cancelled || requestSeqRef.current !== seq) return;
                setState({
                    status: 'error',
                    items: [],
                    error: error?.message || 'Не удалось загрузить историю',
                });
            });

        return () => { cancelled = true; };
    }, [operatorId, date, fetchEntries]);

    const subtitle = useMemo(() => dayTitle(date), [date]);

    if (typeof document === 'undefined' || !document.body || !anchorRect) return null;

    return createPortal(
        <div
            ref={panelRef}
            role="dialog"
            aria-label="История изменений смены"
            style={{
                position: 'fixed',
                left: coords?.left ?? 0,
                top: coords?.top,
                bottom: coords?.bottom,
                width: PANEL_WIDTH,
                maxWidth: 'calc(100vw - 16px)',
                zIndex: 99999,
                fontFamily: APPLE_FONT,
                visibility: coords ? 'visible' : 'hidden',
            }}
            className="overflow-hidden rounded-2xl bg-white/95 shadow-[0_14px_40px_rgba(15,23,42,0.18)] ring-1 ring-slate-200/80 backdrop-blur-xl animate-[fadeIn_.12s_ease]"
        >
            <div className="flex items-start justify-between gap-3 border-b border-slate-200/70 px-3.5 py-2.5">
                <div className="min-w-0">
                    <div className="truncate text-[13.5px] font-semibold text-slate-900">
                        {target?.operatorName || 'История изменений'}
                    </div>
                    <div className="mt-0.5 text-[11px] tabular-nums text-slate-500">{subtitle}</div>
                </div>
                <button
                    type="button"
                    aria-label="Закрыть"
                    className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                    onClick={onClose}
                >
                    <X size={15} />
                </button>
            </div>

            <div className="max-h-[340px] overflow-y-auto overscroll-contain">
                {state.status === 'loading' && (
                    <Block>
                        <Loader2 size={18} className="animate-spin text-slate-400" />
                        <div className="text-[13px] text-slate-500">Загружаем историю…</div>
                    </Block>
                )}

                {state.status === 'error' && (
                    <Block>
                        <AlertCircle size={18} className="text-rose-500" />
                        <div className="text-[13px] text-slate-600">{state.error}</div>
                    </Block>
                )}

                {state.status === 'ready' && state.items.length === 0 && (
                    <Block>
                        <Clock size={18} className="text-slate-300" />
                        <div className="text-[13px] font-medium text-slate-600">Изменений не было</div>
                        <div className="text-[12px] text-slate-400">
                            Смены этого дня не добавляли, не меняли и не снимали.
                        </div>
                    </Block>
                )}

                {state.status === 'ready' && state.items.length > 0 && (
                    <>
                        <div className={`${iosGroupLabel} px-4 pb-1 pt-3`}>
                            Изменений: {state.items.length}
                        </div>
                        <ul className="divide-y divide-slate-100">
                            {state.items.map((entry) => (
                                <HistoryRow key={entry.id} entry={entry} />
                            ))}
                        </ul>
                    </>
                )}
            </div>
        </div>,
        document.body,
    );
}

export default ShiftHistoryPopover;
