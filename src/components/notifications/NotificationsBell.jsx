import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import axios from 'axios';
import { Bell, BookLock, GraduationCap, Image, ClipboardList, CalendarDays, Loader2 } from 'lucide-react';
import { APPLE_FONT } from '../ui/ios';

/* Колокол уведомлений.
 *
 * Один запрос вместо пяти. До него каждый вход в портал стоил отдельного
 * обращения за «Ивентами», «4 You», обучением, опросами и (с приходом вики)
 * обязательными ознакомлениями — пять соединений из пула, который делит с
 * ними SSE аукциона смен.
 *
 * Что колокол НЕ делает: он не гасит непрочитанное самим фактом открытия.
 * «Ивенты» и «4 You» снимаются водяным знаком при заходе в раздел, обучение —
 * прочтением, ознакомления и опросы — только действием. Иначе счётчик
 * обязательного документа обнулялся бы взглядом на колокол, то есть врал бы.
 * Явное «отметить прочитанным» есть, но это отдельное намерение пользователя.
 */

const SOURCE_META = {
    wiki_ack: { label: 'Ознакомление', icon: BookLock, tint: 'text-amber-600 bg-amber-50' },
    lms: { label: 'Обучение', icon: GraduationCap, tint: 'text-indigo-600 bg-indigo-50' },
    surveys: { label: 'Опросы', icon: ClipboardList, tint: 'text-sky-600 bg-sky-50' },
    events: { label: 'Ивенты', icon: CalendarDays, tint: 'text-rose-600 bg-rose-50' },
    four_you: { label: '4 You', icon: Image, tint: 'text-violet-600 bg-violet-50' },
};

// Источники, которые вообще можно погасить кнопкой. Ознакомления и опросы
// снимаются действием — см. notifications/sources.py::mark_seen.
const CLEARABLE = ['events', 'four_you', 'lms'];

// Не чаще раза в 5 минут при возврате фокуса: уведомления приходят от чужих
// действий, а не сами по себе, поэтому фоновый polling не нужен.
const REFRESH_GAP_MS = 5 * 60 * 1000;

const fmtWhen = (iso) => {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    const days = Math.round((Date.now() - date.getTime()) / 86400000);
    if (days === 0) return 'сегодня';
    if (days === 1) return 'вчера';
    if (days > 1 && days < 7) return `${days} дн. назад`;
    return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
};

/* getHeaders — функция, а не готовый объект: токен доступа обновляется в фоне,
   и объект, собранный один раз, ушёл бы в запрос протухшим. Заодно не даёт
   новой ссылке на каждый рендер сбрасывать зависимости useCallback. */
export default function NotificationsBell({ apiBaseUrl, user, getHeaders, onNavigate, onCounts, collapsed }) {
    const [open, setOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [counts, setCounts] = useState({ total: 0 });
    const [items, setItems] = useState([]);
    const [anchor, setAnchor] = useState(null);

    const buttonRef = useRef(null);
    const panelRef = useRef(null);
    const fetchedAtRef = useRef(0);
    const aliveRef = useRef(true);

    useEffect(() => () => { aliveRef.current = false; }, []);

    const load = useCallback(async () => {
        if (!user?.id) return;
        setLoading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/notifications`, { headers: getHeaders() });
            if (!aliveRef.current) return;
            const nextCounts = response?.data?.counts || { total: 0 };
            setCounts(nextCounts);
            setItems(Array.isArray(response?.data?.items) ? response.data.items : []);
            fetchedAtRef.current = Date.now();
            // Бейджи разделов в сайдбаре берут числа отсюда же: раньше «Ивенты»
            // и «4 You» ходили за ними своими запросами, считая ровно то же.
            onCounts?.(nextCounts);
        } catch (e) {
            // Колокол — удобство: сетевой сбой не должен ничего ломать на экране.
        } finally {
            if (aliveRef.current) setLoading(false);
        }
    }, [apiBaseUrl, getHeaders, onCounts, user?.id]);

    useEffect(() => {
        if (!user?.id) {
            setCounts({ total: 0 });
            setItems([]);
            return undefined;
        }
        load();
        const onWake = () => {
            if (document.visibilityState === 'hidden') return;
            if (Date.now() - fetchedAtRef.current < REFRESH_GAP_MS) return;
            load();
        };
        window.addEventListener('focus', onWake);
        document.addEventListener('visibilitychange', onWake);
        return () => {
            window.removeEventListener('focus', onWake);
            document.removeEventListener('visibilitychange', onWake);
        };
    }, [user?.id, load]);

    /* Панель рендерится порталом в body: сайдбар — fixed-слой со своим
       контекстом наложения, и вложенная панель обрезалась бы им при свёрнутом
       состоянии. Координаты считаем от кнопки. */
    const place = useCallback(() => {
        const rect = buttonRef.current?.getBoundingClientRect();
        if (!rect) return;
        const width = 360;
        const left = Math.min(rect.right + 12, window.innerWidth - width - 12);
        setAnchor({
            top: Math.max(12, Math.min(rect.top, window.innerHeight - 480)),
            left: Math.max(12, left),
            width,
        });
    }, []);

    useEffect(() => {
        if (!open) return undefined;
        place();
        const onOutside = (event) => {
            if (panelRef.current?.contains(event.target)) return;
            if (buttonRef.current?.contains(event.target)) return;
            setOpen(false);
        };
        const onKey = (event) => { if (event.key === 'Escape') setOpen(false); };
        document.addEventListener('mousedown', onOutside);
        document.addEventListener('keydown', onKey);
        window.addEventListener('resize', place);
        return () => {
            document.removeEventListener('mousedown', onOutside);
            document.removeEventListener('keydown', onKey);
            window.removeEventListener('resize', place);
        };
    }, [open, place]);

    const total = Math.max(0, Number(counts?.total) || 0);

    const clearable = useMemo(
        () => CLEARABLE.filter((source) => (Number(counts?.[source]) || 0) > 0),
        [counts],
    );

    const markSeen = async () => {
        if (!clearable.length) return;
        try {
            await axios.post(`${apiBaseUrl}/api/notifications/seen`, { sources: clearable }, { headers: getHeaders() });
        } catch (e) {
            // Не удалось — просто перечитаем, счётчик останется прежним.
        }
        load();
    };

    const pick = (item) => {
        setOpen(false);
        onNavigate?.(item.view, item.target);
    };

    const panel = open && anchor ? createPortal(
        <div
            ref={panelRef}
            role="dialog"
            aria-label="Уведомления"
            style={{ ...anchor, fontFamily: APPLE_FONT, position: 'fixed', zIndex: 60 }}
            className="max-h-[70vh] overflow-hidden rounded-2xl border border-black/5 bg-white/95 shadow-[0_20px_60px_rgba(0,0,0,0.18)] backdrop-blur-xl"
        >
            <div className="flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
                <div className="text-[15px] font-semibold text-slate-900">Уведомления</div>
                {clearable.length > 0 && (
                    <button
                        type="button"
                        onClick={markSeen}
                        className="rounded-full px-2.5 py-1 text-[12px] font-medium text-blue-600 transition hover:bg-blue-50"
                    >
                        Отметить прочитанным
                    </button>
                )}
            </div>

            <div className="max-h-[calc(70vh-52px)] overflow-y-auto overscroll-contain">
                {loading && items.length === 0 && (
                    <div className="flex items-center justify-center gap-2 py-10 text-slate-400">
                        <Loader2 size={16} className="animate-spin" />
                        <span className="text-[13px]">Загружаем…</span>
                    </div>
                )}

                {!loading && items.length === 0 && (
                    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
                        <div className="grid h-11 w-11 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                            <Bell size={20} />
                        </div>
                        <div className="text-[14px] font-medium text-slate-900">Всё прочитано</div>
                        <p className="text-[12.5px] leading-relaxed text-slate-500">
                            Здесь появятся новые посты, документы под ознакомление и то, что ждёт вашего действия.
                        </p>
                    </div>
                )}

                {items.map((item) => {
                    const meta = SOURCE_META[item.source] || {};
                    const Icon = meta.icon || Bell;
                    return (
                        <button
                            key={`${item.source}:${item.id}`}
                            type="button"
                            onClick={() => pick(item)}
                            className="flex w-full items-start gap-3 border-b border-slate-50 px-4 py-3 text-left transition last:border-b-0 hover:bg-slate-50"
                        >
                            <span className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-xl ${meta.tint || 'bg-slate-100 text-slate-500'}`}>
                                <Icon size={15} />
                            </span>
                            <span className="min-w-0 flex-1">
                                <span className="flex items-baseline justify-between gap-2">
                                    <span className="truncate text-[13.5px] font-medium text-slate-900">{item.title}</span>
                                    <span className="shrink-0 text-[11px] text-slate-400">{fmtWhen(item.at)}</span>
                                </span>
                                <span className="mt-0.5 flex items-center gap-1.5">
                                    <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
                                        {meta.label || item.source}
                                    </span>
                                    {item.body && (
                                        <span className={`truncate text-[12px] ${item.tone === 'warning' ? 'font-medium text-amber-600' : 'text-slate-500'}`}>
                                            · {item.body}
                                        </span>
                                    )}
                                </span>
                            </span>
                        </button>
                    );
                })}
            </div>
        </div>,
        document.body,
    ) : null;

    return (
        <>
            <button
                ref={buttonRef}
                type="button"
                onClick={() => setOpen((value) => !value)}
                title={total > 0 ? `Уведомлений: ${total}` : 'Уведомления'}
                aria-label={total > 0 ? `Уведомлений: ${total}` : 'Уведомления'}
                className={`relative flex w-full items-center gap-3 rounded-lg px-4 py-3 text-left transition-all duration-200 hover:bg-blue-700 ${open ? 'bg-blue-700' : ''}`}
            >
                <span className="relative shrink-0">
                    <Bell size={16} />
                    {total > 0 && (
                        <span className="absolute -right-1.5 -top-1.5 inline-flex h-[15px] min-w-[15px] items-center justify-center rounded-full bg-rose-500 px-1 text-[9px] font-semibold leading-none text-white">
                            {total > 9 ? '9+' : total}
                        </span>
                    )}
                </span>
                {!collapsed && (
                    <span className="sidebar-text inline-flex items-center gap-2">
                        <span>Уведомления</span>
                        {total > 0 && (
                            <span className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold leading-none text-white">
                                {total > 99 ? '99+' : total}
                            </span>
                        )}
                    </span>
                )}
            </button>
            {panel}
        </>
    );
}
