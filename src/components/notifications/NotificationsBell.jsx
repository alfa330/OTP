import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Bell, BookLock, GraduationCap, Image, ClipboardList, CalendarDays, ChevronRight, ListChecks, Loader2 } from 'lucide-react';
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
    tasks: { label: 'Задачи', icon: ListChecks, tint: 'text-blue-600 bg-blue-50' },
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
export default function NotificationsBell({ apiBaseUrl, user, getHeaders, onNavigate,
                                            onCounts, readSource }) {
    const [open, setOpen] = useState(false);
    // Закрытие в два шага, как у дропдауна «Аккаунта»: сначала обратная
    // анимация, через 200мс — размонтирование панели.
    const [closing, setClosing] = useState(false);
    const [loading, setLoading] = useState(false);
    const [counts, setCounts] = useState({ total: 0 });
    const [items, setItems] = useState([]);

    const buttonRef = useRef(null);
    const panelRef = useRef(null);
    const closeTimerRef = useRef(null);
    const fetchedAtRef = useRef(0);
    const aliveRef = useRef(true);
    // Запрос уже в пути. Отметка свежести ставится только по ответу, поэтому
    // без этого флага возврат во вкладку при медленной сети слал бы второй
    // такой же запрос поверх первого.
    const inFlightRef = useRef(false);

    useEffect(() => () => {
        aliveRef.current = false;
        if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    }, []);

    const load = useCallback(async () => {
        if (!user?.id || inFlightRef.current) return;
        inFlightRef.current = true;
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
            /* Сетевой сбой или 503 «сводку собрать не удалось». Ничего не
               трогаем: прежние числа честнее, чем нули, которых сервер не
               присылал. onCounts намеренно НЕ вызывается — иначе бейджи
               сайдбара погасли бы из-за недоступности базы. */
        } finally {
            inFlightRef.current = false;
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

    /* Раздел прочитан — гасим его здесь же, без запроса. Иначе колокол до
       конца сессии показывал бы «2 новых поста» в двух сантиметрах от уже
       погашенного бейджа: связь была односторонней. Сервер вернёт тот же ноль
       при следующем обновлении, раздел уже отправил свой seen. */
    useEffect(() => {
        const source = readSource?.source;
        if (!source) return;
        setCounts((prev) => {
            if (!prev || !(source in prev)) return prev;
            const wasCount = Math.max(0, Number(prev[source]) || 0);
            if (!wasCount) return prev;
            return {
                ...prev,
                [source]: 0,
                total: Math.max(0, (Number(prev.total) || 0) - wasCount),
            };
        });
        setItems((prev) => (prev.some((item) => item.source === source)
            ? prev.filter((item) => item.source !== source)
            : prev));
    }, [readSource?.source, readSource?.nonce]);

    const close = useCallback(() => {
        if (closeTimerRef.current) return;
        setClosing(true);
        closeTimerRef.current = setTimeout(() => {
            closeTimerRef.current = null;
            setOpen(false);
            setClosing(false);
        }, 200);
    }, []);

    const toggle = () => {
        if (closing) return;
        if (open) close(); else setOpen(true);
    };

    useEffect(() => {
        if (!open) return undefined;
        const onOutside = (event) => {
            if (panelRef.current?.contains(event.target)) return;
            if (buttonRef.current?.contains(event.target)) return;
            close();
        };
        const onKey = (event) => { if (event.key === 'Escape') close(); };
        document.addEventListener('mousedown', onOutside);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', onOutside);
            document.removeEventListener('keydown', onKey);
        };
    }, [open, close]);

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
        close();
        onNavigate?.(item.view, item.target);
    };

    /* Панель — absolute-потомок пункта, как дропдаун «Аккаунта»: она приклеена
       к сайдбару и едет вместе с ним, когда тот сворачивается или
       разворачивается по наведению. Прежний портал в body с fixed-координатами
       зависал на старом месте при любой смене ширины сайдбара. На мобильном
       вправо выпадать некуда (сайдбар обрезает всё за своей шириной) — там
       панель раскрывается вниз, см. .notifications-dropdown в styles.css. */
    const panel = (open || closing) ? (
        <div
            ref={panelRef}
            role="dialog"
            aria-label="Уведомления"
            style={{ fontFamily: APPLE_FONT }}
            className={`notifications-dropdown absolute left-full top-0 z-40 ml-2 flex w-[360px] origin-top flex-col max-h-[70vh] overflow-hidden rounded-2xl border border-black/5 bg-white/95 shadow-[0_20px_60px_rgba(0,0,0,0.18)] backdrop-blur-xl ${open && !closing ? 'animate-dropdown' : 'animate-dropdown-reverse'}`}
        >
            <div className="flex shrink-0 items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
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

            {/* min-h-0 вместо вычитания высоты шапки: на узком пункте (мобильный,
                224px) «Отметить прочитанным» переносится на вторую строку, и
                панель с «max-h минус 52px» срезала бы низ списка. */}
            <div className="min-h-0 overflow-y-auto overscroll-contain">
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
        </div>
    ) : null;

    /* Кнопка собрана по образцу остальных пунктов меню: подпись живёт в
       .sidebar-text и показывается самим CSS сайдбара — в том числе при
       наведении на свёрнутый сайдбар, чего проп collapsed дать не мог; бейдж
       в свёрнутом состоянии — общий .sidebar-surveys-collapsed-badge. */
    return (
        <div className="relative">
            <button
                ref={buttonRef}
                type="button"
                onClick={toggle}
                title={total > 0 ? `Уведомлений: ${total}` : 'Уведомления'}
                aria-label={total > 0 ? `Уведомлений: ${total}` : 'Уведомления'}
                aria-expanded={open && !closing}
                aria-haspopup="dialog"
                className={`group relative flex w-full items-center gap-3 rounded-lg px-4 py-3 text-left transition-all duration-200 hover:bg-blue-700 ${open && !closing ? 'bg-blue-700' : ''}`}
            >
                <Bell size={18} />
                {total > 0 && (
                    <span className="sidebar-surveys-collapsed-badge inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full bg-rose-500 text-white text-[10px] font-semibold leading-none">
                        {total > 9 ? '9+' : total}
                    </span>
                )}
                <span className="sidebar-text inline-flex items-center gap-2">
                    <span>Уведомления</span>
                    {total > 0 && (
                        <span className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold leading-none text-white">
                            {total > 99 ? '99+' : total}
                        </span>
                    )}
                </span>
                <ChevronRight size={14} className="sidebar-text ml-auto translate-x-2 opacity-0 transition-all duration-300 group-hover:translate-x-0 group-hover:opacity-100" />
            </button>
            {panel}
        </div>
    );
}
