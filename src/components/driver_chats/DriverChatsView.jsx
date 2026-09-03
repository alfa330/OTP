import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    Search, Loader2, AlertCircle, Send, Check, Lock, Info,
    MessageSquare, Download, ChevronLeft, Phone, Clock, ImageIcon,
} from 'lucide-react';

import ChatThread from '../c2d_eval/ChatThread';
import {
    APPLE_FONT, iosCard, iosInput, iosBtnPrimary, iosBtnSecondary, iosBtnGhost,
    IosModal, IosSegmented, IosBadge, IosToggle,
} from '../ui/ios';
import {
    kindLabel, roleLabel, formatPhone, formatDateTime, formatTime, formatDayShort,
    exportFileName, KIND_TONE,
} from './journalMeta';

/* Раздел «Чаты водителей» (задача #271).
 *
 * Оператор СЗоВ вводит номер телефона водителя, видит его переписку за двое
 * суток, открывает нужный чат, снимает скриншот средствами системы и жмёт
 * «Передан» — в этот же чат уходит внутренний комментарий Chat2Desk, который
 * водитель не видит, а чат-менеджер видит у себя в рабочем окне.
 *
 * ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕТ:
 *
 * * Кнопки «скачать картинкой». Постановка говорит «оператор может открыть
 *   нужный чат и сделать скриншот» — снимает человек, система показывает. Две
 *   трети чатов содержат медиа с внешнего домена, и клиентский рендер молча
 *   выбросил бы из картинки именно фотографии, ради которых её и снимают.
 * * Поиска «по мере ввода». Каждый поиск может стоить обращения к вендору, чей
 *   месячный лимит общий с ночным синком метрик отдела. Поиск — явное действие
 *   по Enter или кнопке, а не побочный эффект набора текста.
 *
 * Лента переписки — общий ChatThread из «Журнала оценок»: он уже разбирает
 * внутренние заметки, автоответы и системные строки, рисует фото с лайтбоксом и
 * покрыт тёмной темой. Второй ленты в проекте быть не должно.
 */

const WINDOW_HINT = 'Показываем переписку за последние 2 дня';

const emptyResult = { chats: [], phone: '', clientId: null, clientName: '', notFound: false };

const DriverChatsView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );

    /* showToast приходит новой функцией на каждый рендер App — известная ловушка
       портала. Держим её в ref, чтобы она не попала в зависимости эффектов и не
       вызывала повторные запросы. */
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((message, kind) => {
        if (toastRef.current) toastRef.current(message, kind);
    }, []);

    const [context, setContext] = useState(null);
    const [tab, setTab] = useState('search');

    const [query, setQuery] = useState('');
    const [searching, setSearching] = useState(false);
    const [searchError, setSearchError] = useState('');
    const [result, setResult] = useState(emptyResult);
    const [activeKey, setActiveKey] = useState(null);
    const [hideService, setHideService] = useState(true);
    const [handedOff, setHandedOff] = useState({});

    const [handoffOpen, setHandoffOpen] = useState(false);
    const [handoffNote, setHandoffNote] = useState('');
    const [handoffSending, setHandoffSending] = useState(false);

    const inputRef = useRef(null);

    // ── Контекст раздела ────────────────────────────────────────────────────
    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const response = await fetch(`${apiBaseUrl}/api/driver_chats/context`, {
                    headers: headers(), credentials: 'include',
                });
                const data = await response.json().catch(() => ({}));
                if (cancelled) return;
                if (!response.ok) {
                    setSearchError(data.error || 'Раздел недоступен');
                    return;
                }
                setContext(data);
            } catch {
                if (!cancelled) setSearchError('Не удалось открыть раздел');
            }
        })();
        return () => { cancelled = true; };
    }, [apiBaseUrl, headers]);

    useEffect(() => { inputRef.current?.focus(); }, []);

    const canViewJournal = Boolean(context?.capabilities?.can_view_journal);

    // ── Поиск ───────────────────────────────────────────────────────────────
    const runSearch = useCallback(async () => {
        const phone = query.trim();
        if (!phone || searching) return;
        setSearching(true);
        setSearchError('');
        try {
            const response = await fetch(
                `${apiBaseUrl}/api/driver_chats/search?phone=${encodeURIComponent(phone)}`,
                { headers: headers(), credentials: 'include' });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                setResult(emptyResult);
                setSearchError(data.error || 'Не удалось найти чаты');
                return;
            }
            const chats = data.chats || [];
            setResult({
                chats,
                phone: data.phone || phone,
                clientId: data.client_id ?? null,
                clientName: data.client_name || '',
                notFound: Boolean(data.not_found),
                truncated: Boolean(data.truncated),
            });
            setHandedOff({});
            // Открываем самый свежий живой чат сразу: в 9 случаях из 10 нужен
            // именно он, и лишний клик здесь — это лишний клик в каждом звонке.
            const first = chats.find((chat) => !chat.is_service) || chats[0];
            setActiveKey(first ? chatKey(first) : null);
            if (typeof data.searches_left === 'number') {
                setContext((prev) => (prev ? {
                    ...prev,
                    limits: { ...(prev.limits || {}), left_today: data.searches_left },
                } : prev));
            }
        } catch {
            setResult(emptyResult);
            setSearchError('Сеть недоступна. Попробуйте ещё раз');
        } finally {
            setSearching(false);
        }
    }, [apiBaseUrl, headers, query, searching]);

    const chats = result.chats || [];
    const visibleChats = useMemo(
        () => (hideService ? chats.filter((chat) => !chat.is_service) : chats),
        [chats, hideService]);
    const serviceCount = chats.length - chats.filter((chat) => !chat.is_service).length;

    const activeChat = useMemo(
        () => chats.find((chat) => chatKey(chat) === activeKey) || null,
        [chats, activeKey]);

    /* Открытие чата пишется в журнал — это и есть ответ на вопрос «кто смотрел
       переписку». Отправляем «в фон»: ответ сервера экрану не нужен, а ждать
       его значило бы тормозить открытие ленты. Ошибку не показываем человеку —
       он не может на неё повлиять, — но и не глотаем: она уходит в консоль. */
    const logOpen = useCallback((chat) => {
        if (!chat || !result.phone) return;
        fetch(`${apiBaseUrl}/api/driver_chats/open`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...headers() },
            credentials: 'include',
            body: JSON.stringify({
                phone: result.phone,
                client_id: result.clientId,
                request_id: chat.request_id,
                dialog_id: chat.dialog_id,
                channel_name: chat.channel_name,
                messages_count: chat.messages_count,
            }),
        }).catch(() => { /* журнал не должен мешать работе оператора */ });
    }, [apiBaseUrl, headers, result.phone, result.clientId]);

    const openChat = useCallback((chat) => {
        setActiveKey(chatKey(chat));
        logOpen(chat);
    }, [logOpen]);

    /* Первый чат раскрывается сам после поиска — значит и он должен попасть в
       журнал, иначе «открыл переписку» не увидит самый частый случай. */
    const autoLogged = useRef(null);
    useEffect(() => {
        if (!activeChat) return;
        const key = `${result.phone}:${chatKey(activeChat)}`;
        if (autoLogged.current === key) return;
        autoLogged.current = key;
        logOpen(activeChat);
    }, [activeChat, result.phone, logOpen]);

    // ── «Передан» ───────────────────────────────────────────────────────────
    const sendHandoff = useCallback(async () => {
        if (!activeChat || handoffSending) return;
        setHandoffSending(true);
        try {
            const response = await fetch(`${apiBaseUrl}/api/driver_chats/handoff`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', ...headers() },
                credentials: 'include',
                body: JSON.stringify({
                    phone: result.phone,
                    client_id: result.clientId,
                    request_id: activeChat.request_id,
                    dialog_id: activeChat.dialog_id,
                    channel_name: activeChat.channel_name,
                    note: handoffNote.trim(),
                }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                toast(data.error || 'Не удалось отправить комментарий', 'error');
                return;
            }
            setHandedOff((prev) => ({ ...prev, [chatKey(activeChat)]: true }));
            setHandoffOpen(false);
            setHandoffNote('');
            toast('Комментарий отправлен чат-менеджеру', 'success');
        } catch {
            toast('Сеть недоступна. Комментарий не отправлен', 'error');
        } finally {
            setHandoffSending(false);
        }
    }, [activeChat, apiBaseUrl, handoffNote, handoffSending, headers, result, toast]);

    const snapshot = useMemo(() => (activeChat ? {
        messages: activeChat.messages || [],
        operator_name: activeChat.operator_name || null,
    } : null), [activeChat]);

    const limits = context?.limits || {};
    const leftToday = typeof limits.left_today === 'number'
        ? limits.left_today
        : (typeof limits.searches_per_day === 'number' && typeof limits.used_today === 'number'
            ? Math.max(0, limits.searches_per_day - limits.used_today)
            : null);

    return (
        <div className="space-y-4" style={{ fontFamily: APPLE_FONT }}>
            {canViewJournal && (
                <div className="flex justify-center">
                    <IosSegmented
                        value={tab}
                        onChange={setTab}
                        ariaLabel="Разделы"
                        options={[
                            { value: 'search', label: 'Поиск чатов' },
                            { value: 'journal', label: 'Журнал' },
                        ]}
                    />
                </div>
            )}

            {tab === 'journal' && canViewJournal ? (
                <JournalPanel apiBaseUrl={apiBaseUrl} headers={headers} toast={toast} />
            ) : (
                <>
                    <SearchBar
                        value={query}
                        onChange={setQuery}
                        onSubmit={runSearch}
                        searching={searching}
                        inputRef={inputRef}
                        leftToday={leftToday}
                    />

                    {searchError && (
                        <div className={`${iosCard} flex items-start gap-2.5 px-4 py-3 text-sm text-rose-600`}>
                            <AlertCircle size={16} className="mt-0.5 shrink-0" />
                            <span>{searchError}</span>
                        </div>
                    )}

                    {!searchError && !searching && !result.phone && <StartHint />}

                    {!searchError && result.phone && !chats.length && (
                        <div className={`${iosCard} px-6 py-10 text-center`}>
                            <MessageSquare size={26} className="mx-auto mb-3 text-slate-300" />
                            <div className="text-sm font-medium text-slate-700">
                                {result.notFound
                                    ? 'Такого номера нет в переписках'
                                    : 'За последние 2 дня этот водитель не писал'}
                            </div>
                            <div className="mt-1 text-[13px] text-slate-500">
                                {result.notFound
                                    ? 'Проверьте номер: возможно, водитель писал с другого.'
                                    : 'Более ранняя переписка в разделе не показывается.'}
                            </div>
                        </div>
                    )}

                    {Boolean(chats.length) && (
                        <div className="grid gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
                            <ChatList
                                chats={visibleChats}
                                activeKey={activeKey}
                                onPick={openChat}
                                handedOff={handedOff}
                                driverName={result.clientName}
                                phone={result.phone}
                                serviceCount={serviceCount}
                                hideService={hideService}
                                onToggleService={setHideService}
                                truncated={result.truncated}
                            />
                            <ChatPanel
                                chat={activeChat}
                                snapshot={snapshot}
                                phone={result.phone}
                                driverName={result.clientName}
                                handedOff={activeChat ? handedOff[chatKey(activeChat)] : false}
                                onHandoff={() => setHandoffOpen(true)}
                                onBack={() => setActiveKey(null)}
                            />
                        </div>
                    )}
                </>
            )}

            <HandoffModal
                open={handoffOpen}
                onClose={() => (handoffSending ? null : setHandoffOpen(false))}
                note={handoffNote}
                onNote={setHandoffNote}
                sending={handoffSending}
                onSend={sendHandoff}
                maxLength={context?.comment_max_length || 500}
                authorName={context?.me?.name || ''}
            />
        </div>
    );
};

/* Ключ чата: заявка, а если её нет — диалог. Тот же порядок, что на бэкенде. */
function chatKey(chat) {
    return chat?.request_id ? `r${chat.request_id}` : `d${chat?.dialog_id || 0}`;
}

// ── Поисковая строка ────────────────────────────────────────────────────────

const SearchBar = ({ value, onChange, onSubmit, searching, inputRef, leftToday }) => (
    <div className={`${iosCard} px-4 py-4 sm:px-5`}>
        <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center">
            <div className="relative flex-1">
                <Search size={17} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                    ref={inputRef}
                    value={value}
                    onChange={(event) => onChange(event.target.value)}
                    onKeyDown={(event) => { if (event.key === 'Enter') onSubmit(); }}
                    inputMode="tel"
                    placeholder="Номер телефона водителя"
                    aria-label="Номер телефона водителя"
                    className={`${iosInput} h-11 pl-10 text-[15px] tabular-nums`}
                />
            </div>
            <button
                type="button"
                onClick={onSubmit}
                disabled={searching || !value.trim()}
                className={`${iosBtnPrimary} h-11 min-w-[120px] justify-center disabled:opacity-40`}
            >
                {searching ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />}
                {searching ? 'Ищем…' : 'Найти'}
            </button>
        </div>
        <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-slate-500">
            <span>{WINDOW_HINT}</span>
            {typeof leftToday === 'number' && leftToday <= 20 && (
                <span className="tabular-nums text-amber-600">
                    Осталось поисков сегодня: {leftToday}
                </span>
            )}
        </div>
    </div>
);

const StartHint = () => (
    <div className={`${iosCard} px-6 py-12 text-center`}>
        <Phone size={26} className="mx-auto mb-3 text-slate-300" />
        <div className="text-sm font-medium text-slate-700">Введите номер телефона водителя</div>
        <div className="mx-auto mt-1.5 max-w-md text-[13px] leading-relaxed text-slate-500">
            Номер можно вводить как удобно — 87071234567, +7 707 123 45 67 или 7071234567.
            Откроется переписка за последние двое суток.
        </div>
    </div>
);

// ── Список чатов ────────────────────────────────────────────────────────────

const ChatList = ({ chats, activeKey, onPick, handedOff, driverName, phone,
                    serviceCount, hideService, onToggleService, truncated }) => (
    <div className={`${iosCard} flex max-h-[72vh] flex-col overflow-hidden`}>
        <div className="border-b border-slate-200/70 px-4 py-3">
            <div className="text-[15px] font-semibold text-slate-900">
                {driverName || 'Водитель'}
            </div>
            <div className="mt-0.5 text-[12.5px] tabular-nums text-slate-500">{formatPhone(phone)}</div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {chats.map((chat) => {
                const key = chatKey(chat);
                const active = key === activeKey;
                return (
                    <button
                        key={key}
                        type="button"
                        onClick={() => onPick(chat)}
                        className={`mb-1.5 w-full rounded-xl px-3 py-2.5 text-left transition-all active:scale-[0.98] ${
                            active ? 'bg-blue-500/10 ring-1 ring-blue-500/30' : 'hover:bg-slate-500/5'
                        }`}
                    >
                        <div className="flex items-center gap-2">
                            <span className="text-[12.5px] font-medium tabular-nums text-slate-500">
                                {formatDayShort(chat.last_at)} · {formatTime(chat.last_at)}
                            </span>
                            {handedOff[key] && (
                                <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-600">
                                    <Check size={11} /> передан
                                </span>
                            )}
                            {chat.has_media && <ImageIcon size={12} className="text-slate-400" />}
                        </div>
                        <div className="mt-1 line-clamp-2 text-[13px] leading-snug text-slate-700">
                            {chat.preview || 'Без текста'}
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[11.5px] text-slate-400">
                            <span className="tabular-nums">{chat.messages_count} сообщ.</span>
                            {chat.channel_name && <span className="truncate">{chat.channel_name}</span>}
                            {chat.is_service && <span>служебное</span>}
                        </div>
                    </button>
                );
            })}
            {!chats.length && (
                <div className="px-3 py-8 text-center text-[13px] text-slate-400">
                    Живой переписки за период нет
                </div>
            )}
        </div>

        {(serviceCount > 0 || truncated) && (
            <div className="space-y-2 border-t border-slate-200/70 px-4 py-2.5">
                {serviceCount > 0 && (
                    <label className="flex items-center justify-between gap-3 text-[12.5px] text-slate-600">
                        <span>Скрыть служебные ({serviceCount})</span>
                        <IosToggle checked={hideService} onChange={onToggleService} />
                    </label>
                )}
                {truncated && (
                    <div className="text-[11.5px] leading-snug text-amber-600">
                        Сообщений за период больше, чем помещается в один запрос —
                        показаны самые свежие.
                    </div>
                )}
            </div>
        )}
    </div>
);

// ── Панель чата ─────────────────────────────────────────────────────────────

const ChatPanel = ({ chat, snapshot, phone, driverName, handedOff, onHandoff, onBack }) => {
    if (!chat) {
        return (
            <div className={`${iosCard} flex min-h-[320px] items-center justify-center px-6 text-center`}>
                <div className="text-[13px] text-slate-400">Выберите чат слева</div>
            </div>
        );
    }
    return (
        <div className={`${iosCard} flex max-h-[72vh] flex-col overflow-hidden`}>
            <div className="flex flex-wrap items-center gap-3 border-b border-slate-200/70 px-4 py-3">
                <button
                    type="button"
                    onClick={onBack}
                    className={`${iosBtnGhost} lg:hidden`}
                    aria-label="Назад к списку"
                >
                    <ChevronLeft size={16} />
                </button>
                <div className="min-w-0 flex-1">
                    <div className="truncate text-[14.5px] font-semibold text-slate-900">
                        {driverName || formatPhone(phone)}
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[12px] text-slate-500">
                        <span className="inline-flex items-center gap-1 tabular-nums">
                            <Clock size={11} /> {formatDateTime(chat.started_at)}
                        </span>
                        {chat.channel_name && <span className="truncate">{chat.channel_name}</span>}
                        {chat.operator_name && <span className="truncate">Отвечал: {chat.operator_name}</span>}
                    </div>
                </div>
                {handedOff ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-3 py-1.5 text-[13px] font-medium text-emerald-700">
                        <Check size={14} /> Передан
                    </span>
                ) : (
                    <button type="button" onClick={onHandoff} className={iosBtnPrimary}>
                        <Send size={15} /> Передан
                    </button>
                )}
            </div>

            <div className="min-h-0 flex-1 overflow-hidden bg-[#f2f2f7]">
                <ChatThread snapshot={snapshot} className="h-full" />
            </div>

            <div className="flex items-start gap-2 border-t border-slate-200/70 px-4 py-2.5 text-[11.5px] leading-snug text-slate-500">
                <Info size={13} className="mt-0.5 shrink-0" />
                <span>
                    Снимок экрана делайте средствами системы: на Mac — ⌘⇧4, на Windows — Win+Shift+S.
                    Кнопка «Передан» отправит чат-менеджеру внутренний комментарий, водителю он не виден.
                </span>
            </div>
        </div>
    );
};

// ── Окно «Передан» ──────────────────────────────────────────────────────────

const HandoffModal = ({ open, onClose, note, onNote, sending, onSend, maxLength, authorName }) => (
    <IosModal
        open={open}
        onClose={onClose}
        title="Передать чат-менеджеру"
        subtitle="В чат уйдёт внутренний комментарий. Водитель его не увидит"
        footer={(
            <div className="flex justify-end gap-2">
                <button type="button" onClick={onClose} disabled={sending} className={iosBtnSecondary}>
                    Отмена
                </button>
                <button type="button" onClick={onSend} disabled={sending} className={iosBtnPrimary}>
                    {sending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
                    {sending ? 'Отправляем…' : 'Передать'}
                </button>
            </div>
        )}
    >
        <div className="space-y-3">
            <div className="rounded-xl bg-slate-500/5 px-3.5 py-3 text-[13px] leading-relaxed text-slate-600">
                <div className="flex items-start gap-2">
                    <Lock size={14} className="mt-0.5 shrink-0 text-slate-400" />
                    <span>
                        Комментарий подпишется вашим именем{authorName ? ` — ${authorName}` : ''}.
                        Отозвать или отредактировать его после отправки нельзя.
                    </span>
                </div>
            </div>
            <div>
                <label className="mb-1.5 block text-[12.5px] font-medium text-slate-600" htmlFor="dch-note">
                    Что передать (необязательно)
                </label>
                <textarea
                    id="dch-note"
                    value={note}
                    onChange={(event) => onNote(event.target.value.slice(0, maxLength))}
                    rows={3}
                    placeholder="Например: водитель просит уточнить статус заказа"
                    className={`${iosInput} resize-none py-2.5`}
                />
                <div className="mt-1 text-right text-[11.5px] tabular-nums text-slate-400">
                    {note.length}/{maxLength}
                </div>
            </div>
        </div>
    </IosModal>
);

// ── Журнал ──────────────────────────────────────────────────────────────────

const todayISO = () => new Date().toISOString().slice(0, 10);
const shiftDaysBack = (iso, days) => {
    const date = new Date(`${iso}T00:00:00`);
    date.setDate(date.getDate() - days);
    return date.toISOString().slice(0, 10);
};

const JournalPanel = ({ apiBaseUrl, headers, toast }) => {
    const [filters, setFilters] = useState(() => ({
        from: shiftDaysBack(todayISO(), 6),
        to: todayISO(),
        kind: 'all',
        userId: 'all',
        phone: '',
    }));
    const [data, setData] = useState({ items: [], total: 0, summary: {}, people: [] });
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [downloading, setDownloading] = useState(false);

    const params = useMemo(() => {
        const search = new URLSearchParams();
        if (filters.from) search.set('date_from', filters.from);
        if (filters.to) search.set('date_to', filters.to);
        if (filters.kind !== 'all') search.set('kinds', filters.kind);
        if (filters.userId !== 'all') search.set('user_id', String(filters.userId));
        if (filters.phone.trim()) search.set('phone', filters.phone.trim());
        return search;
    }, [filters]);

    useEffect(() => { setPage(1); }, [params]);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        const search = new URLSearchParams(params);
        search.set('page', String(page));
        (async () => {
            try {
                const response = await fetch(
                    `${apiBaseUrl}/api/driver_chats/journal?${search.toString()}`,
                    { headers: headers(), credentials: 'include' });
                const payload = await response.json().catch(() => ({}));
                if (cancelled) return;
                if (!response.ok) {
                    setError(payload.error || 'Не удалось загрузить журнал');
                    return;
                }
                setError('');
                setData(payload);
            } catch {
                if (!cancelled) setError('Сеть недоступна');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [apiBaseUrl, headers, params, page]);

    const download = useCallback(async () => {
        setDownloading(true);
        try {
            const response = await fetch(
                `${apiBaseUrl}/api/driver_chats/journal/export?${params.toString()}`,
                { headers: headers(), credentials: 'include' });
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                toast(payload.error || 'Не удалось собрать выгрузку', 'error');
                return;
            }
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = exportFileName(filters.from, filters.to);
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
        } catch {
            toast('Сеть недоступна. Выгрузка не собрана', 'error');
        } finally {
            setDownloading(false);
        }
    }, [apiBaseUrl, filters.from, filters.to, headers, params, toast]);

    const summary = data.summary || {};
    const pageCount = Math.max(1, Math.ceil((data.total || 0) / (data.page_size || 50)));

    return (
        <div className="space-y-4">
            <div className={`${iosCard} px-4 py-4`}>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                    <Field label="С">
                        <input type="date" value={filters.from} className={iosInput}
                               onChange={(e) => setFilters((f) => ({ ...f, from: e.target.value }))} />
                    </Field>
                    <Field label="По">
                        <input type="date" value={filters.to} className={iosInput}
                               onChange={(e) => setFilters((f) => ({ ...f, to: e.target.value }))} />
                    </Field>
                    <Field label="Действие">
                        <select value={filters.kind} className={iosInput}
                                onChange={(e) => setFilters((f) => ({ ...f, kind: e.target.value }))}>
                            <option value="all">Все</option>
                            <option value="handoff">Передал чат-менеджеру</option>
                            <option value="open">Открыл переписку</option>
                            <option value="search">Искал номер</option>
                        </select>
                    </Field>
                    <Field label="Сотрудник">
                        <select value={filters.userId} className={iosInput}
                                onChange={(e) => setFilters((f) => ({ ...f, userId: e.target.value }))}>
                            <option value="all">Все</option>
                            {(data.people || []).map((person) => (
                                <option key={person.user_id} value={person.user_id}>{person.name}</option>
                            ))}
                        </select>
                    </Field>
                    <Field label="Телефон водителя">
                        <input value={filters.phone} placeholder="любой" className={`${iosInput} tabular-nums`}
                               onChange={(e) => setFilters((f) => ({ ...f, phone: e.target.value }))} />
                    </Field>
                </div>

                <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                    <div className="flex flex-wrap gap-x-5 gap-y-1 text-[12.5px] text-slate-500">
                        <Stat label="Действий" value={summary.events} />
                        <Stat label="Передано" value={summary.handoffs} />
                        <Stat label="Сотрудников" value={summary.people} />
                        <Stat label="Водителей" value={summary.drivers} />
                    </div>
                    <button type="button" onClick={download} disabled={downloading || !data.total}
                            className={`${iosBtnSecondary} disabled:opacity-40`}>
                        {downloading ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
                        Выгрузить
                    </button>
                </div>
            </div>

            {error && (
                <div className={`${iosCard} flex items-start gap-2.5 px-4 py-3 text-sm text-rose-600`}>
                    <AlertCircle size={16} className="mt-0.5 shrink-0" /><span>{error}</span>
                </div>
            )}

            <div className={`${iosCard} overflow-hidden`}>
                {loading ? (
                    <div className="flex items-center justify-center gap-2 py-12 text-sm text-slate-400">
                        <Loader2 size={15} className="animate-spin" /> Загрузка журнала…
                    </div>
                ) : !data.items?.length ? (
                    <div className="py-12 text-center text-sm text-slate-400">
                        За выбранный период действий не было
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full min-w-[760px] text-left text-[13px]">
                            <thead>
                                <tr className="border-b border-slate-200/70 text-[11.5px] uppercase tracking-wide text-slate-400">
                                    <th className="px-4 py-2.5 font-medium">Когда</th>
                                    <th className="px-4 py-2.5 font-medium">Сотрудник</th>
                                    <th className="px-4 py-2.5 font-medium">Действие</th>
                                    <th className="px-4 py-2.5 font-medium">Водитель</th>
                                    <th className="px-4 py-2.5 font-medium">Чат</th>
                                    <th className="px-4 py-2.5 font-medium">Комментарий</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.items.map((item) => (
                                    <tr key={item.id} className="border-b border-slate-100 last:border-0">
                                        <td className="whitespace-nowrap px-4 py-2.5 tabular-nums text-slate-500">
                                            {formatDateTime(item.created_at)}
                                        </td>
                                        <td className="px-4 py-2.5">
                                            <div className="font-medium text-slate-800">{item.user_name || '—'}</div>
                                            <div className="text-[11.5px] text-slate-400">{roleLabel(item.user_role)}</div>
                                        </td>
                                        <td className="px-4 py-2.5">
                                            {item.kind === 'handoff' ? (
                                                <IosBadge tone={KIND_TONE[item.kind]}>{kindLabel(item.kind)}</IosBadge>
                                            ) : (
                                                <span className="text-slate-600">{kindLabel(item.kind)}</span>
                                            )}
                                        </td>
                                        <td className="whitespace-nowrap px-4 py-2.5 tabular-nums text-slate-600">
                                            {formatPhone(item.phone)}
                                        </td>
                                        <td className="px-4 py-2.5 text-[12px] text-slate-500">
                                            {item.channel_name && <div className="truncate">{item.channel_name}</div>}
                                            {item.request_id && (
                                                <div className="tabular-nums text-slate-400">№ {item.request_id}</div>
                                            )}
                                        </td>
                                        <td className="max-w-[280px] px-4 py-2.5 text-[12.5px] text-slate-600">
                                            {item.comment_text || ''}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {pageCount > 1 && (
                <div className="flex items-center justify-center gap-2">
                    <button type="button" className={`${iosBtnSecondary} disabled:opacity-40`}
                            disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                        Назад
                    </button>
                    <span className="px-2 text-[12.5px] tabular-nums text-slate-500">
                        {page} из {pageCount}
                    </span>
                    <button type="button" className={`${iosBtnSecondary} disabled:opacity-40`}
                            disabled={page >= pageCount} onClick={() => setPage((p) => p + 1)}>
                        Вперёд
                    </button>
                </div>
            )}
        </div>
    );
};

const Field = ({ label, children }) => (
    <label className="block">
        <span className="mb-1.5 block text-[12px] font-medium text-slate-500">{label}</span>
        {children}
    </label>
);

const Stat = ({ label, value }) => (
    <span>
        {label}: <span className="font-semibold tabular-nums text-slate-700">{value ?? 0}</span>
    </span>
);

export default DriverChatsView;
