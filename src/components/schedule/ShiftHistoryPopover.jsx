import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { APPLE_FONT } from '../ui/ios';
import ShiftHistoryList, { shiftHistoryDayTitle } from './ShiftHistoryList';

/*
 * История изменений по одной ячейке графика — «оператор + день».
 *
 * Живёт порталом в document.body: сетка графика скроллится по двум осям и
 * режет содержимое ячейки `overflow-hidden`, поэтому панель, вложенная в
 * ячейку, была бы обрезана. Позиционирование и правила закрытия сняты с
 * IosMenu (src/components/ui/ios.jsx): прокрутка закрывает панель, а не тащит
 * её за собой — ячейка уезжает из-под курсора, и «приклеенная» панель повисла
 * бы над чужой строкой.
 *
 * Сами строки рисует общий ShiftHistoryList — тот же, что и вкладка «История»
 * в карточке смены.
 */

const PANEL_WIDTH = 380;
const VIEWPORT_GAP = 8;

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

    const subtitle = useMemo(() => shiftHistoryDayTitle(date), [date]);

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
                <ShiftHistoryList status={state.status} items={state.items} error={state.error} />
            </div>
        </div>,
        document.body,
    );
}

export default ShiftHistoryPopover;
