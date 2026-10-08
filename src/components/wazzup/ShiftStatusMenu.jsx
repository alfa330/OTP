import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown, Loader2, LogOut } from 'lucide-react';
import { APPLE_FONT } from '../ui/ios';
import { elapsedNow, formatShiftElapsed, statusDotClass, statusShowsTimer } from './workspaceStatus';
import { clearWorkspaceStatusError, setWorkspaceStatus, useWorkspace } from './workspaceStore';

/* Кнопка статуса смены в шапке «Чатов ОП» — рядом с «Обновить».
 *
 * На кнопке текущий статус, под ней — остальные и «Закончить смену». Список
 * статусов приходит с сервера (wazzup/shift.py): те же слова, что у iCORE Phone,
 * поэтому учёт часов читает их без отдельных правил.
 *
 * Меню рисуется в портал с fixed-позицией, как IosMenu: шапка раздела лежит
 * рядом с карточкой с overflow-hidden, и вложенное меню она бы обрезала. */

const MENU_WIDTH = 232;

export default function ShiftStatusMenu() {
    const { statuses, logoutKey, current, receivedAt, pending, statusError } = useWorkspace();
    const [open, setOpen] = useState(false);
    const [coords, setCoords] = useState(null);
    const [nowMs, setNowMs] = useState(() => Date.now());
    const btnRef = useRef(null);
    const popRef = useRef(null);
    const timed = statusShowsTimer(current?.tone);

    const recompute = useCallback(() => {
        const rect = btnRef.current?.getBoundingClientRect();
        if (!rect) return;
        setCoords({
            top: Math.round(rect.bottom + 6),
            left: Math.max(8, Math.min(window.innerWidth - MENU_WIDTH - 8, Math.round(rect.right - MENU_WIDTH))),
        });
    }, []);

    useLayoutEffect(() => { if (open) recompute(); }, [open, recompute]);
    useLayoutEffect(() => {
        if (!open || !coords) return;
        const menu = popRef.current;
        (menu?.querySelector('[aria-checked="true"]') || menu?.querySelector('button'))?.focus();
    }, [open, coords]);

    useEffect(() => {
        if (!open) return undefined;
        const onDoc = (event) => {
            if (btnRef.current?.contains(event.target) || popRef.current?.contains(event.target)) return;
            setOpen(false);
        };
        const onKey = (event) => {
            if (event.key !== 'Escape') return;
            setOpen(false);
            requestAnimationFrame(() => btnRef.current?.focus());
        };
        const onScroll = () => setOpen(false);
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
    }, [open, recompute]);

    // Счётчик нужен только паузам — и тикает только пока он на экране.
    useEffect(() => {
        if (!timed) return undefined;
        setNowMs(Date.now());
        const timer = setInterval(() => setNowMs(Date.now()), 30000);
        return () => clearInterval(timer);
    }, [timed, receivedAt]);

    useEffect(() => {
        if (!statusError) return undefined;
        const timer = setTimeout(clearWorkspaceStatusError, 8000);
        return () => clearTimeout(timer);
    }, [statusError]);

    const pick = (key) => {
        setOpen(false);
        btnRef.current?.focus();
        if (key !== current?.key) setWorkspaceStatus(key);
    };

    const onMenuKey = (event) => {
        if (event.key === 'Tab') { setOpen(false); btnRef.current?.focus(); return; }
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const items = [...(popRef.current?.querySelectorAll('button') || [])];
        if (!items.length) return;
        const currentIndex = items.indexOf(document.activeElement);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
            : (currentIndex + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        items[next]?.focus();
    };

    const elapsed = timed ? formatShiftElapsed(elapsedNow(current, receivedAt, nowMs)) : '';
    const itemClass = 'flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-[13.5px] text-slate-800 transition hover:bg-slate-100';

    return (
        <>
            {statusError && (
                <span role="alert" className="max-w-[260px] truncate text-[12.5px] text-rose-600" title={statusError}>
                    {statusError}
                </span>
            )}
            <button ref={btnRef} type="button" disabled={pending}
                aria-haspopup="menu" aria-expanded={open} aria-controls={open ? 'wazzup-shift-menu' : undefined}
                aria-label={`Статус: ${current?.label || ''}`}
                onKeyDown={(event) => {
                    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setOpen(true); }
                }}
                onClick={() => setOpen((value) => !value)}
                className={`inline-flex items-center gap-2 rounded-xl px-3 py-2 text-[13px] font-semibold text-slate-800 transition-all active:scale-[0.98] disabled:opacity-60 ${
                    open ? 'bg-slate-200' : 'bg-slate-100 hover:bg-slate-200'}`}>
                {pending
                    ? <Loader2 size={13} className="animate-spin text-slate-400" />
                    : <span className={`h-2 w-2 shrink-0 rounded-full ${statusDotClass(current?.tone)}`} />}
                <span>{current?.label}</span>
                {elapsed && <span className="font-medium tabular-nums text-slate-500">· {elapsed}</span>}
                <ChevronDown size={14} className={`text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} />
            </button>
            {open && coords && createPortal(
                <div ref={popRef} id="wazzup-shift-menu" role="menu" aria-label="Статус смены" onKeyDown={onMenuKey}
                    style={{ position: 'fixed', top: coords.top, left: coords.left, width: MENU_WIDTH,
                        zIndex: 99999, fontFamily: APPLE_FONT }}
                    className="overflow-hidden rounded-2xl bg-white/95 p-1.5 shadow-[0_14px_40px_rgba(15,23,42,0.18)] ring-1 ring-slate-200/80 backdrop-blur-xl animate-[fadeIn_.12s_ease]">
                    {statuses.map((item) => {
                        const isCurrent = item.key === current?.key;
                        return (
                            <button key={item.key} type="button" role="menuitemradio" aria-checked={isCurrent}
                                onClick={() => pick(item.key)} className={itemClass}>
                                <span className={`h-2 w-2 shrink-0 rounded-full ${statusDotClass(item.tone)}`} />
                                <span className="min-w-0 flex-1 truncate">{item.label}</span>
                                {isCurrent && <Check size={15} className="text-blue-600" />}
                            </button>
                        );
                    })}
                    {logoutKey && <>
                        <div className="my-1.5 h-px bg-slate-200/70" />
                        <button type="button" role="menuitem" onClick={() => pick(logoutKey)} className={itemClass}>
                            <LogOut size={15} className="text-slate-400" />
                            <span className="min-w-0 flex-1 truncate">Закончить смену</span>
                        </button>
                    </>}
                </div>,
                document.body,
            )}
        </>
    );
}
