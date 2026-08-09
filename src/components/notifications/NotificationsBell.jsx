import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Bell, BookLock, GraduationCap, Image, ClipboardList, CalendarDays, ChevronRight, ListChecks, Loader2 } from 'lucide-react';
import { APPLE_FONT } from '../ui/ios';
import { createCoalescedReload } from './coalescedReload.js';

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

// Последняя страховка при возврате фокуса. Обычные изменения будит SSE, а
// переходы по времени и редкие потери канала сверяет сам поток раз в минуту.
const REFRESH_GAP_MS = 5 * 60 * 1000;

/* Порция элементов на источник. Совпадает с ITEMS_PER_SOURCE в
   notifications/sources.py, потолок — с MAX_ITEMS_PER_SOURCE: расхождение
   ничего не сломает, но клиент просил бы порции, которых сервер уже не отдаёт. */
const PAGE_SIZE = 5;
const MAX_PAGE_SIZE = 50;
const SSE_STALL_MS = 90 * 1000;

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

    /* Есть ли за показанным ещё элементы. Счётчик считает ВСЁ, а сервер отдаёт
       не больше PAGE_SIZE на источник, поэтому без догрузки бейдж «6» висел бы
       над пятью карточками. */
    const [hasMore, setHasMore] = useState(false);

    const buttonRef = useRef(null);
    const panelRef = useRef(null);
    const scrollRef = useRef(null);
    const sentinelRef = useRef(null);
    const closeTimerRef = useRef(null);
    const fetchedAtRef = useRef(0);
    const aliveRef = useRef(true);
    const userIdRef = useRef(user?.id);
    userIdRef.current = user?.id;
    /* Размер порции живёт в ref, а не в состоянии: он входит в запрос, но не
       должен пересоздавать load — тот стоит в зависимостях SSE-эффекта, и
       каждая догрузка рвала бы живой канал и занимала слот заново. */
    const pageSizeRef = useRef(PAGE_SIZE);
    const hasMoreRef = useRef(false);
    const loadingMoreRef = useRef(false);

    useEffect(() => {
        // React StrictMode в dev повторно запускает effects после их cleanup.
        aliveRef.current = true;
        return () => {
            aliveRef.current = false;
            if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
        };
    }, []);

    const reloadSnapshot = useCallback(async () => {
        const requestedUserId = user?.id;
        if (!aliveRef.current || !requestedUserId) return;
        try {
            const response = await axios.get(`${apiBaseUrl}/api/notifications`, {
                headers: getHeaders(),
                params: { limit: pageSizeRef.current },
            });
            // При logout/login без размонтирования старый ответ не должен на
            // мгновение показать новому пользователю чужие уведомления.
            if (!aliveRef.current || userIdRef.current !== requestedUserId) return;
            const nextCounts = response?.data?.counts || { total: 0 };
            setCounts(nextCounts);
            setItems(Array.isArray(response?.data?.items) ? response.data.items : []);
            const more = Boolean(response?.data?.has_more);
            hasMoreRef.current = more;
            setHasMore(more);
            fetchedAtRef.current = Date.now();
            // Бейджи разделов в сайдбаре берут числа отсюда же: раньше «Ивенты»
            // и «4 You» ходили за ними своими запросами, считая ровно то же.
            onCounts?.(nextCounts);
        } catch (e) {
            /* Сетевой сбой или 503 «сводку собрать не удалось». Ничего не
               трогаем: прежние числа честнее, чем нули, которых сервер не
               присылал. onCounts намеренно НЕ вызывается — иначе бейджи
               сайдбара погасли бы из-за недоступности базы. */
        }
    }, [apiBaseUrl, getHeaders, onCounts, user?.id]);

    // Сам gate живёт весь срок компонента, а ref подставляет ему актуальные
    // URL, токен и пользователя без сброса single-flight на каждом рендере.
    const reloadSnapshotRef = useRef(reloadSnapshot);
    reloadSnapshotRef.current = reloadSnapshot;
    const coalescedLoadRef = useRef(null);
    if (!coalescedLoadRef.current) {
        coalescedLoadRef.current = createCoalescedReload(() => reloadSnapshotRef.current());
    }

    const load = useCallback(async () => {
        if (!user?.id) return;
        setLoading(true);
        try {
            await coalescedLoadRef.current();
        } finally {
            if (aliveRef.current) setLoading(false);
        }
    }, [user?.id]);

    /* Докрутили до низа — берём следующую порцию. Не догрузка «хвоста», а
       перезапрос всей сводки с бо́льшим лимитом: она собирается одним запросом,
       а склейка страниц на клиенте разъезжалась бы с тычками, которые в любой
       момент могут переписать список целиком. */
    const loadMore = useCallback(async () => {
        if (loadingMoreRef.current || !hasMoreRef.current) return;
        if (pageSizeRef.current >= MAX_PAGE_SIZE) return;
        loadingMoreRef.current = true;
        pageSizeRef.current = Math.min(MAX_PAGE_SIZE, pageSizeRef.current + PAGE_SIZE);
        try {
            await load();
        } finally {
            loadingMoreRef.current = false;
        }
    }, [load]);

    /* Наблюдатель, а не обработчик прокрутки: шести элементам прокручиваться
       негде — пять помещаются целиком, и события scroll не случилось бы вовсе,
       а шестой остался бы недостижим. Наблюдатель же срабатывает и на «низ
       списка просто виден», поэтому недостающее подтягивается сразу.
       Пересоздаётся на каждую порцию: observe() сразу сообщает текущее
       пересечение, и цепочка продолжается, пока список не заполнит панель. */
    useEffect(() => {
        if (!open || closing || !hasMore) return undefined;
        const root = scrollRef.current;
        const target = sentinelRef.current;
        if (!root || !target || typeof IntersectionObserver === 'undefined') return undefined;
        const observer = new IntersectionObserver((entries) => {
            if (entries.some((entry) => entry.isIntersecting)) loadMore();
        }, { root, rootMargin: '120px' });
        observer.observe(target);
        return () => observer.disconnect();
    }, [open, closing, hasMore, items.length, loadMore]);

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

    /* Мгновенные обновления: /api/notifications/stream отдаёт «тычок», когда
       на сервере появилось что-то для этого пользователя, и load() перечитывает
       сводку. EventSource не умеет наши заголовки авторизации — читаем fetch'ем,
       как SSE аукциона. Канал держит только видимая вкладка: скрытая рвёт
       соединение и отдаёт слот (их на сервере ровно BELL_STREAM_LIMIT — каждый
       поток занимает нить waitress). Любой отказ канала — молчаливый откат на
       прежнее обновление по фокусу, колокол без реалтайма остаётся полностью
       рабочим. */
    useEffect(() => {
        if (!user?.id) return undefined;
        let cancelled = false;
        let abortController = null;
        let retryTimer = null;
        let pokeTimer = null;
        let watchdogTimer = null;
        let attempt = 0;

        const clearWatchdog = () => {
            if (!watchdogTimer) return;
            clearTimeout(watchdogTimer);
            watchdogTimer = null;
        };

        const scheduleRetry = (delayMs) => {
            if (cancelled || retryTimer) return;
            retryTimer = setTimeout(() => {
                retryTimer = null;
                connect();
            }, delayMs);
        };

        const connect = async () => {
            if (cancelled || document.visibilityState === 'hidden') return;
            clearWatchdog();
            abortController?.abort();
            const controller = new AbortController();
            abortController = controller;
            let watchdogExpired = false;
            const armWatchdog = () => {
                clearWatchdog();
                watchdogTimer = setTimeout(() => {
                    watchdogTimer = null;
                    if (cancelled || document.visibilityState === 'hidden'
                        || abortController !== controller) return;
                    watchdogExpired = true;
                    controller.abort();
                }, SSE_STALL_MS);
            };
            // Heartbeat приходит раз в 25 секунд; 90 секунд тишины означают,
            // что proxy/соединение зависло и его нужно создать заново.
            armWatchdog();
            try {
                const response = await fetch(`${apiBaseUrl}/api/notifications/stream`, {
                    headers: { ...getHeaders(), Accept: 'text/event-stream' },
                    signal: controller.signal,
                    credentials: 'include',
                });
                if (response.status === 503) {
                    // Слоты заняты (или канал выключен) — не мешаем, попробуем позже.
                    scheduleRetry(5 * 60 * 1000);
                    return;
                }
                if (response.status === 401) {
                    /* Токен протух посреди сессии. fetch идёт мимо axios-перехватчика,
                       поэтому обновляем сессию его руками: load() ходит axios'ом и
                       перехватчик освежит токен — следующая попытка возьмёт свежие
                       заголовки из getHeaders(). */
                    load();
                    throw new Error('bell stream auth expired');
                }
                if (!response.ok || !response.body) throw new Error('bell stream failed');
                const reader = response.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buffer = '';
                let connected = false;
                while (!cancelled) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    armWatchdog();
                    buffer += decoder.decode(value, { stream: true });
                    const chunks = buffer.split('\n\n');
                    buffer = chunks.pop() || '';
                    if (!connected && chunks.some((chunk) => chunk
                        .split('\n')
                        .some((line) => line.startsWith(': connected')))) {
                        connected = true;
                        attempt = 0;
                        /* Сервер фиксирует current_seq перед этим фреймом. Snapshot
                           после него закрывает окно между первой загрузкой и
                           подпиской; reload во время snapshot попадёт в очередь. */
                        load();
                    }
                    const poked = chunks.some((chunk) => chunk
                        .split('\n')
                        .some((line) => line.startsWith('event: reload')));
                    if (poked && !pokeTimer) {
                        // Джиттер размазывает перечитки после широковещательного
                        // тычка (новый пост «Ивентов» будит все вкладки разом), а
                        // таймер-гард склеивает всплеск тычков в одну перечитку.
                        pokeTimer = setTimeout(() => {
                            pokeTimer = null;
                            if (!cancelled && document.visibilityState !== 'hidden') load();
                        }, Math.random() * 2000);
                    }
                }
            } catch (e) {
                if (cancelled || document.visibilityState === 'hidden') return;
                // visibility/unmount/new connect отменяют канал намеренно;
                // watchdog-abort, напротив, должен пройти к retry ниже.
                if (e?.name === 'AbortError' && !watchdogExpired) return;
            } finally {
                if (abortController === controller) clearWatchdog();
            }
            if (!cancelled && document.visibilityState !== 'hidden') {
                attempt += 1;
                scheduleRetry(Math.min(60000, 2000 * (2 ** Math.min(attempt, 5))) + Math.random() * 1000);
            }
        };

        const onVisibility = () => {
            if (cancelled) return;
            if (document.visibilityState === 'hidden') {
                clearWatchdog();
                abortController?.abort();
                return;
            }
            if (retryTimer) {
                clearTimeout(retryTimer);
                retryTimer = null;
            }
            connect();
        };
        document.addEventListener('visibilitychange', onVisibility);
        connect();
        return () => {
            cancelled = true;
            if (retryTimer) clearTimeout(retryTimer);
            if (pokeTimer) clearTimeout(pokeTimer);
            clearWatchdog();
            abortController?.abort();
            document.removeEventListener('visibilitychange', onVisibility);
        };
    }, [user?.id, apiBaseUrl, getHeaders, load]);

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
        // Панель закрыта — возвращаем порцию к исходной. Иначе фоновые перечитки
        // (их будит SSE) до конца сессии таскали бы полсотни элементов на
        // источник ради счётчика, который считается и без них.
        pageSizeRef.current = PAGE_SIZE;
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
            <div ref={scrollRef} className="notifications-scroll min-h-0 overflow-y-auto overscroll-contain">
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

                {/* Метка низа списка: она же цель наблюдателя и индикатор
                    догрузки. Рендерится только когда есть что подгружать. */}
                {hasMore && items.length > 0 && (
                    <div
                        ref={sentinelRef}
                        className="flex items-center justify-center gap-2 py-3 text-slate-400"
                    >
                        <Loader2 size={14} className="animate-spin" />
                        <span className="text-[12px]">Загружаем ещё…</span>
                    </div>
                )}
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
