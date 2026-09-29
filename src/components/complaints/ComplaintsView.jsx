import React, { memo, useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertCircle, BarChart3, ChevronRight, Inbox, ListChecks, Loader2, Search, Settings2,
} from 'lucide-react';
import {
    APPLE_FONT, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput,
    IosBadge, IosModal, IosSegmented,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { TARGET_ICONS } from './ComplaintDraft';
import ComplaintCard from './ComplaintCard';
import ComplaintsAnalytics from './ComplaintsAnalytics';
import { rowBadges, rowSubtitle } from './complaintRules';
import { fitHeight, measureShell } from '../crm/layout';

/* Раздел «Жалобы» (ТЗ задачи #297) — разбор, работа с сотрудником и аналитика.
 *
 * Принимают жалобу в «Обращениях»: оператор выбирает направление «Жалоба» в
 * «Новом обращении» и там же видит ответ для водителя и вопрос группы
 * (решение владельца 29.09.2026). Сюда ходят те, кто разбирает, и админ:
 *   супервайзер, глава  — «К разбору»: жалобы на людей своего отдела;
 *   админ, СВ, глава    — ещё «Аналитика» и выгрузка.
 * Что кому видно, решает сервер (complaints/access.py), здесь — кнопки по
 * capabilities.
 *
 * Раскладка — как у «Обращений»: слева лента, справа карточка, на телефоне
 * карточка разворачивается на весь экран.
 *
 * Цвет — только у того, что ждёт действия (см. complaintRules.rowBadges). */

const PAGE_SIZE = 40;

const STATUS_FILTERS = [
    { value: 'open', label: 'В работе' },
    { value: 'closed', label: 'Отработанные' },
    { value: '', label: 'Все' },
];

const errorText = (error, fallback) => error?.response?.data?.error || error?.message || fallback;

const fmtAgo = (iso) => {
    if (!iso) return '';
    const secs = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    if (secs < 60) return 'только что';
    if (secs < 3600) return `${Math.floor(secs / 60)} мин`;
    if (secs < 86400) return `${Math.floor(secs / 3600)} ч`;
    if (secs < 7 * 86400) return `${Math.floor(secs / 86400)} дн`;
    return new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
};

const EmptyBlock = ({ icon: Icon = Inbox, children, hint }) => (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
        <Icon size={22} className="text-slate-300" />
        <div className="text-[13px] text-slate-400">{children}</div>
        {hint && <div className="max-w-[280px] text-[11.5px] leading-snug text-slate-400">{hint}</div>}
    </div>
);

const ComplaintRow = memo(function ComplaintRow({ complaint, active, onSelect, viewerId, handler }) {
    const Icon = TARGET_ICONS[complaint.target] || Inbox;
    const badges = rowBadges(complaint, viewerId);
    const unread = complaint.unread;
    const closed = complaint.status === 'closed';
    return (
        <button type="button" onClick={() => onSelect(complaint.id)}
                className={`relative flex w-full gap-3 px-3 py-2.5 text-left transition-colors ${
                    active ? 'bg-blue-50' : unread ? 'bg-blue-50/40 hover:bg-blue-50/70' : 'hover:bg-slate-50'
                }`}>
            <span className={`absolute inset-y-1 left-0 w-[3px] rounded-r-full ${active ? 'bg-blue-500' : 'bg-transparent'}`} />
            <span className={`mt-0.5 grid h-[38px] w-[38px] shrink-0 place-items-center rounded-[12px] ring-1 ${
                closed ? 'bg-slate-50 text-slate-400 ring-slate-100' : 'bg-slate-100 text-slate-600 ring-slate-200/70'
            }`}>
                <Icon size={17} />
            </span>
            <span className="min-w-0 flex-1">
                <span className="flex items-baseline gap-2">
                    <span className={`min-w-0 flex-1 truncate text-[13.5px] leading-snug ${
                        unread ? 'font-semibold text-slate-900' : closed ? 'font-medium text-slate-500' : 'font-medium text-slate-800'
                    }`}>
                        {complaint.reason_title}
                    </span>
                    <span className="shrink-0 text-[11px] tabular-nums text-slate-400">
                        {fmtAgo(complaint.last_activity_at || complaint.created_at)}
                    </span>
                </span>
                <span className="mt-0.5 block truncate text-[12px] leading-snug text-slate-500">
                    {rowSubtitle(complaint, { handler })}
                </span>
                <span className="mt-1 flex items-center gap-1.5 overflow-hidden text-[11px] text-slate-400">
                    <span className="shrink-0 tabular-nums">№{complaint.id}</span>
                    <span className="shrink-0 text-slate-300">·</span>
                    {badges.length ? badges.map((badge) => (
                        <IosBadge key={badge.key} tone={badge.tone} className="!py-0 shrink-0 !text-[10px]">
                            {badge.label}
                        </IosBadge>
                    )) : (
                        <span className="truncate">{complaint.target_title}</span>
                    )}
                </span>
            </span>
        </button>
    );
});

/* Группа, куда уходят жалобы. Одна на раздел — «Жалобы КЦ, регионы, таксопарк».
 * Выбирают из групп, где уже состоит бот: в чужой чат он всё равно не напишет. */
const SettingsModal = ({ open, onClose, apiBaseUrl, headers, showToast, onSaved }) => {
    const [state, setState] = useState(null);
    const [chatId, setChatId] = useState('');
    const [mentions, setMentions] = useState('');
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!open) return undefined;
        let cancelled = false;
        axios.get(`${apiBaseUrl}/api/complaints/settings`, { headers: headers() })
            .then((response) => {
                if (cancelled) return;
                setState(response.data);
                setChatId(response.data.settings?.chat_id ? String(response.data.settings.chat_id) : '');
                setMentions((response.data.settings?.mention_usernames || []).map((name) => `@${name}`).join(' '));
            })
            .catch((error) => { if (!cancelled) showToast?.(errorText(error, 'Настройки недоступны'), 'error'); });
        return () => { cancelled = true; };
    }, [open, apiBaseUrl, headers, showToast]);

    const save = async () => {
        setBusy(true);
        try {
            await axios.put(`${apiBaseUrl}/api/complaints/settings`,
                { chat_id: chatId ? Number(chatId) : null, mention_usernames: mentions },
                { headers: headers() });
            showToast?.('Сохранено', 'success');
            onSaved?.();
            onClose();
        } catch (error) {
            showToast?.(errorText(error, 'Не удалось сохранить'), 'error');
        } finally {
            setBusy(false);
        }
    };

    const chats = state?.chats || [];
    return (
        <IosModal open={open} onClose={onClose} title="Группа для жалоб"
                  subtitle="Куда бот отправляет жалобы, которые требуют разбора"
                  footer={(
                      <>
                          <button type="button" onClick={onClose} className={iosBtnSecondary} disabled={busy}>Отмена</button>
                          <button type="button" onClick={save} className={iosBtnPrimary} disabled={busy || !state}>
                              Сохранить
                          </button>
                      </>
                  )}>
            {!state ? (
                <div className="flex items-center justify-center gap-2 py-8 text-[13px] text-slate-400">
                    <Loader2 size={14} className="animate-spin" /> Загрузка…
                </div>
            ) : (
                <div className="space-y-3.5">
                    <div>
                        <div className={`${iosGroupLabel} mb-1.5`}>Telegram-группа</div>
                        <CustomSelect variant="ios" searchable value={chatId} onChange={setChatId}
                                      options={[{ value: '', label: 'Не выбрана' },
                                          ...chats.map((chat) => ({ value: String(chat.chat_id), label: chat.title }))]}
                                      placeholder={chats.length ? 'Выберите группу' : 'Бота нет ни в одной группе'}
                                      ariaLabel="Telegram-группа для жалоб" />
                        <div className="mt-1.5 px-1 text-[11.5px] leading-snug text-slate-500">
                            Нет нужной группы — добавьте в неё бота, он запомнит чат сам.
                        </div>
                    </div>
                    <div>
                        <div className={`${iosGroupLabel} mb-1.5`}>Кого отмечать в группе</div>
                        <input value={mentions} onChange={(e) => setMentions(e.target.value)}
                               placeholder="Необязательно: @ник — можно несколько через пробел"
                               className={iosInput} />
                    </div>
                </div>
            )}
        </IosModal>
    );
};

export default function ComplaintsView({
    apiBaseUrl, withAccessTokenHeader, showToast, realtimePulse, onUnreadChange, focusRequest, user,
}) {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );

    const [tab, setTab] = useState('list');
    const [meta, setMeta] = useState(null);
    const [capabilities, setCapabilities] = useState(null);
    const [items, setItems] = useState([]);
    const [hasMore, setHasMore] = useState(false);
    const [offset, setOffset] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [selectedId, setSelectedId] = useState(null);
    const [settingsOpen, setSettingsOpen] = useState(false);

    const [status, setStatus] = useState('open');
    const [segment, setSegment] = useState(null);
    const [target, setTarget] = useState('');
    const [search, setSearch] = useState('');
    const [searchApplied, setSearchApplied] = useState('');
    const searchTimer = useRef(null);

    const shellRef = useRef(null);
    const headerRef = useRef(null);
    const filtersRef = useRef(null);
    const [shellHeight, setShellHeight] = useState(null);

    const viewerId = user?.id;
    const handler = Boolean(capabilities?.is_handler);
    const scoped = capabilities?.scope && capabilities.scope !== 'own';

    useEffect(() => {
        const node = shellRef.current;
        if (!node || typeof ResizeObserver === 'undefined') return undefined;
        const recompute = () => setShellHeight(fitHeight(measureShell(node)));
        recompute();
        const observer = new ResizeObserver(recompute);
        observer.observe(node.closest('.main-content') || document.body);
        if (headerRef.current) observer.observe(headerRef.current);
        if (filtersRef.current) observer.observe(filtersRef.current);
        window.addEventListener('resize', recompute);
        return () => { observer.disconnect(); window.removeEventListener('resize', recompute); };
    }, [tab, selectedId]);

    useEffect(() => {
        if (searchTimer.current) clearTimeout(searchTimer.current);
        searchTimer.current = setTimeout(() => setSearchApplied(search.trim()), 350);
        return () => { if (searchTimer.current) clearTimeout(searchTimer.current); };
    }, [search]);

    const loadMeta = useCallback(async () => {
        try {
            const response = await axios.get(`${apiBaseUrl}/api/complaints/meta`, { headers: headers() });
            setMeta(response.data);
            setCapabilities(response.data.capabilities || null);
        } catch (err) {
            setError(errorText(err, 'Раздел недоступен'));
        }
    }, [apiBaseUrl, headers]);

    useEffect(() => { loadMeta(); }, [loadMeta]);

    /* Сегмент по умолчанию — по роли: разбирающему важнее то, что ждёт его.
       Ставим один раз, когда права приехали, и дальше не трогаем выбор. */
    useEffect(() => {
        if (!capabilities || segment !== null) return;
        setSegment(capabilities.is_handler ? 'work' : 'all');
    }, [capabilities, segment]);

    const loadList = useCallback(async (nextOffset = 0, silent = false) => {
        if (segment === null) return;
        if (!silent) setLoading(true);
        try {
            const params = new URLSearchParams();
            if (status && !searchApplied) params.set('status', status);
            // Поиск идёт по всему, что зритель видит: проверить, не заведена ли
            // уже жалоба по этому водителю, узким сегментом нельзя.
            if (segment && !searchApplied) params.set('segment', segment);
            if (target) params.set('target', target);
            if (searchApplied) params.set('q', searchApplied);
            params.set('limit', String(PAGE_SIZE));
            params.set('offset', String(nextOffset));
            const response = await axios.get(`${apiBaseUrl}/api/complaints/complaints?${params}`,
                { headers: headers() });
            const next = response.data.items || [];
            setItems((prev) => {
                if (!nextOffset) return next;
                const seen = new Set(prev.map((item) => item.id));
                return prev.concat(next.filter((item) => !seen.has(item.id)));
            });
            setHasMore(Boolean(response.data.has_more));
            if (response.data.capabilities) setCapabilities(response.data.capabilities);
            setOffset(nextOffset);
            setError(null);
        } catch (err) {
            setError(errorText(err, 'Не удалось загрузить жалобы'));
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, headers, status, segment, target, searchApplied]);

    useEffect(() => { loadList(0); }, [loadList]);

    // Реалтайм — тычком колокола, своего канала раздел не открывает.
    useEffect(() => {
        if (!realtimePulse) return;
        loadList(0, true);
    }, [realtimePulse]); // eslint-disable-line react-hooks/exhaustive-deps

    const refreshCounters = useCallback(async () => {
        try {
            const response = await axios.get(`${apiBaseUrl}/api/complaints/ping`, { headers: headers() });
            // Бейдж раздела — задачи разбора. Ответы по своим жалобам автор
            // видит бейджем «Обращений» (notifications/sources.py: crm).
            onUnreadChange?.(Number(response.data.counters?.work) || 0);
        } catch (_) { /* число в меню — не повод показывать отказ */ }
    }, [apiBaseUrl, headers, onUnreadChange]);

    useEffect(() => { refreshCounters(); }, [refreshCounters, realtimePulse]);

    useEffect(() => {
        if (!focusRequest?.complaintId) return;
        setTab('list');
        setSelectedId(Number(focusRequest.complaintId));
    }, [focusRequest?.requestId]); // eslint-disable-line react-hooks/exhaustive-deps

    const handleSeen = useCallback((id) => {
        setItems((prev) => prev.map((item) => (item.id === id
            ? { ...item, unread: false, unread_kind: null, unread_count: 0 } : item)));
        refreshCounters();
    }, [refreshCounters]);

    const handleChanged = useCallback(() => {
        loadList(0, true);
        refreshCounters();
    }, [loadList, refreshCounters]);

    const handleDeleted = useCallback(() => {
        setSelectedId(null);
        loadList(0, true);
        refreshCounters();
    }, [loadList, refreshCounters]);

    const tabs = [
        { value: 'list', label: 'Жалобы', icon: <ListChecks size={13} /> },
        capabilities?.can_view_analytics ? { value: 'analytics', label: 'Аналитика', icon: <BarChart3 size={13} /> } : null,
    ].filter(Boolean);

    // «Мои» здесь нет: свои жалобы автор видит в «Обращениях».
    const segmentOptions = [
        handler ? { value: 'work', label: 'К разбору' } : null,
        { value: 'all', label: 'Все' },
    ].filter(Boolean);

    return (
        <div className="w-full" style={{ fontFamily: APPLE_FONT }}>
            <div ref={headerRef} className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
                <div>
                    <h2 className="text-lg font-semibold tracking-tight text-slate-900">Жалобы</h2>
                    <p className="text-xs text-slate-500">
                        Разбор жалоб, работа с сотрудником и аналитика. Принимают жалобы в «Обращениях»
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    {tabs.length > 1 && (
                        <IosSegmented value={tab} onChange={(value) => { setTab(value); setSelectedId(null); }}
                                      options={tabs} ariaLabel="Раздел жалоб" />
                    )}
                    {capabilities?.can_manage_settings && (
                        <button type="button" onClick={() => setSettingsOpen(true)} title="Группа для жалоб"
                                className={iosBtnGhost}>
                            <Settings2 size={15} />
                        </button>
                    )}
                </div>
            </div>

            {tab === 'analytics' && capabilities?.can_view_analytics && (
                <ComplaintsAnalytics apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} meta={meta} />
            )}

            {tab === 'list' && (
                <>
                    <div ref={filtersRef}
                         className={`mb-3 flex-wrap items-center gap-2 px-1 lg:flex ${selectedId ? 'hidden' : 'flex'}`}>
                        <IosSegmented value={searchApplied ? '' : status}
                                      onChange={(value) => { setStatus(value); setSelectedId(null); }}
                                      options={STATUS_FILTERS} ariaLabel="Статус жалоб" />
                        {scoped && segment !== null && (
                            <IosSegmented value={searchApplied ? 'all' : segment}
                                          onChange={(value) => { setSegment(value); setSelectedId(null); }}
                                          options={segmentOptions} ariaLabel="Чьи жалобы" />
                        )}
                        <CustomSelect className="w-48" variant="ios" value={target}
                                      onChange={(value) => { setTarget(value); setSelectedId(null); }}
                                      options={[{ value: '', label: 'На кого — все' },
                                          ...((meta?.targets || []).map((item) => ({ value: item.code, label: item.title })))]}
                                      ariaLabel="На кого жалоба" />
                        <div className="relative min-w-[180px] flex-1 sm:max-w-[280px]">
                            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                            <input value={search} onChange={(e) => setSearch(e.target.value)}
                                   placeholder="Номер, телефон, ФИО"
                                   className={`${iosInput} pl-9`} />
                        </div>
                    </div>

                    <div className={`${iosCard} overflow-hidden`}>
                        <div ref={shellRef} style={shellHeight ? { height: shellHeight } : undefined}
                             className="flex h-[calc(100dvh-320px)] min-h-[380px] flex-col lg:flex-row">
                            <div className={`flex w-full min-h-0 flex-col border-slate-200/70 lg:w-[360px] lg:shrink-0 lg:border-r ${
                                selectedId ? 'hidden lg:flex' : 'flex'
                            }`}>
                                <div className="crm-scroll min-h-0 flex-1 overflow-y-auto">
                                    {loading && !items.length && (
                                        <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-slate-400">
                                            <Loader2 size={15} className="animate-spin" /> Загрузка…
                                        </div>
                                    )}
                                    {!loading && error && (
                                        <div className="flex items-center justify-center gap-2 py-16 text-center text-[13px] text-rose-500">
                                            <AlertCircle size={15} /> {error}
                                        </div>
                                    )}
                                    {!loading && !error && !items.length && (
                                        <EmptyBlock hint={segment === 'work'
                                            ? 'Жалоб, которые ждут вашего разбора, нет.'
                                            : 'В этом фильтре пусто.'}>
                                            Жалоб нет
                                        </EmptyBlock>
                                    )}
                                    <div className="divide-y divide-slate-100">
                                        {items.map((complaint) => (
                                            <ComplaintRow key={complaint.id} complaint={complaint}
                                                          active={complaint.id === selectedId}
                                                          onSelect={setSelectedId} viewerId={viewerId}
                                                          handler={handler} />
                                        ))}
                                    </div>
                                    {hasMore && (
                                        <button type="button" onClick={() => loadList(offset + PAGE_SIZE)}
                                                className="w-full py-3 text-[12.5px] font-semibold text-slate-500 transition hover:bg-slate-50">
                                            Показать ещё
                                        </button>
                                    )}
                                </div>
                                {!!items.length && (
                                    <div className="shrink-0 border-t border-slate-100 px-3.5 py-2 text-[11px] tabular-nums text-slate-400">
                                        Показано {items.length}{hasMore ? ' — есть ещё' : ''}
                                    </div>
                                )}
                            </div>

                            <div className={`min-h-0 min-w-0 flex-1 ${selectedId ? 'flex' : 'hidden lg:flex'}`}>
                                {selectedId ? (
                                    <div className="h-full min-h-0 w-full">
                                        <ComplaintCard key={selectedId} complaintId={selectedId} meta={meta}
                                                       apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast}
                                                       onBack={() => setSelectedId(null)} onChanged={handleChanged}
                                                       onSeen={handleSeen} onDeleted={handleDeleted}
                                                       pulse={realtimePulse} />
                                    </div>
                                ) : (
                                    <div className="flex w-full items-center justify-center">
                                        <EmptyBlock icon={ChevronRight}
                                                    hint="Слева — жалобы. Выберите любую, чтобы увидеть ответ группы и ход разбора.">
                                            Выберите жалобу
                                        </EmptyBlock>
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                </>
            )}

            <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} apiBaseUrl={apiBaseUrl}
                           headers={headers} showToast={showToast} onSaved={loadMeta} />
        </div>
    );
}
