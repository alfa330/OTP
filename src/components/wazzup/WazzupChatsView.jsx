import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Search, RefreshCw, Loader2, AlertCircle, MessageSquare, ExternalLink,
    ChevronUp, User2, Headset, ImageIcon, FileText, Mic, Video, MapPin, Ban,
} from 'lucide-react';
import { APPLE_FONT, iosCard, iosInput, IosBadge } from '../ui/ios';

/* Чаты Wazzup (Верификаторы): просмотр переписки «как в мессенджере».
 * Данные копятся вебхуком с 2026-07-17, ретеншн 45 дней — более ранняя
 * история доступна только в самом Wazzup (кнопка «Открыть в Wazzup»). */

const PAGE_SIZE = 30;
const THREAD_PAGE = 50;

const MEDIA_META = {
    image: { icon: ImageIcon, label: 'Фото' },
    video: { icon: Video, label: 'Видео' },
    audio: { icon: Mic, label: 'Голосовое' },
    document: { icon: FileText, label: 'Документ' },
    geo: { icon: MapPin, label: 'Геолокация' },
};

const fmtTime = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
};

const fmtDay = (iso) => {
    if (!iso) return '';
    return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' });
};

const fmtListDate = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    const today = new Date();
    return d.toDateString() === today.toDateString()
        ? fmtTime(iso)
        : d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
};

function MessageBubble({ msg }) {
    const media = MEDIA_META[msg.type];
    const MediaIcon = media?.icon;
    return (
        <div className={`flex ${msg.isEcho ? 'justify-end' : 'justify-start'} px-3`}>
            <div className={`max-w-[75%] rounded-2xl px-3 py-2 text-sm shadow-sm ${
                msg.isEcho ? 'bg-emerald-100 text-emerald-950' : 'bg-white text-slate-900 border border-slate-100'
            }`}>
                {msg.isEcho && msg.authorName && (
                    <div className="mb-0.5 flex items-center gap-1 text-[11px] font-semibold text-emerald-700">
                        <Headset size={11} /> {msg.authorName}
                    </div>
                )}
                {media && (
                    <div className="flex items-center gap-1.5 text-slate-500">
                        {MediaIcon && <MediaIcon size={14} />}
                        {msg.contentUri
                            ? <a href={msg.contentUri} target="_blank" rel="noopener noreferrer"
                                 className="underline decoration-dotted hover:text-slate-700">{media.label}</a>
                            : <span>{media.label}</span>}
                    </div>
                )}
                {msg.text && <div className="whitespace-pre-wrap break-words">{msg.text}</div>}
                {!msg.text && !media && <div className="italic text-slate-400">[{msg.type || 'сообщение'}]</div>}
                <div className="mt-0.5 flex items-center justify-end gap-1 text-[10px] text-slate-400">
                    {msg.isDeleted && <span className="flex items-center gap-0.5 text-rose-500"><Ban size={10} /> удалено</span>}
                    {msg.isEdited && !msg.isDeleted && <span>изм.</span>}
                    <span>{fmtTime(msg.dt)}</span>
                    {msg.isEcho && msg.status && <span>· {msg.status === 'read' ? 'прочитано' : msg.status}</span>}
                </div>
            </div>
        </div>
    );
}

export default function WazzupChatsView(props) {
    const { apiBaseUrl, withAccessTokenHeader, showToast } = props;
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});

    const [channels, setChannels] = useState(null);       // null = загрузка
    const [channelId, setChannelId] = useState('');       // '' = все каналы
    const [chats, setChats] = useState(null);
    const [chatsTotal, setChatsTotal] = useState(0);
    const [chatsError, setChatsError] = useState(null);
    const [search, setSearch] = useState('');
    const [appliedSearch, setAppliedSearch] = useState('');
    const [selected, setSelected] = useState(null);       // {channelId, chatId, ...}
    const [thread, setThread] = useState(null);
    const [threadHasMore, setThreadHasMore] = useState(false);
    const [threadLoadingMore, setThreadLoadingMore] = useState(false);
    const chatsRequest = useRef({ id: 0, controller: null });
    const threadRequest = useRef({ id: 0, controller: null });
    const threadBox = useRef(null);
    const searchDebounce = useRef(null);

    const channelName = useMemo(() => {
        const map = {};
        (channels || []).forEach((c) => { map[c.channelId] = c.name || c.plainId || c.channelId; });
        return map;
    }, [channels]);

    const loadChannels = () => {
        axios.get(`${apiBaseUrl}/api/wazzup/channels`, { headers: headers() })
            .then((r) => setChannels(r.data.items || []))
            .catch(() => { setChannels([]); showToast?.('Не удалось загрузить каналы', 'error'); });
    };

    const loadChats = ({ reset = true, channel = channelId, q = appliedSearch } = {}) => {
        chatsRequest.current.controller?.abort();
        const controller = new AbortController();
        const requestId = chatsRequest.current.id + 1;
        chatsRequest.current = { id: requestId, controller };
        const offset = reset ? 0 : (chats?.length || 0);
        if (reset) { setChats(null); setChatsError(null); }
        axios.get(`${apiBaseUrl}/api/wazzup/chats`, {
            headers: headers(), signal: controller.signal,
            params: { channel_id: channel || undefined, q: q || undefined, limit: PAGE_SIZE, offset },
        }).then((r) => {
            if (requestId !== chatsRequest.current.id) return;
            setChatsTotal(r.data.total || 0);
            setChats((prev) => reset ? (r.data.items || []) : [...(prev || []), ...(r.data.items || [])]);
        }).catch((e) => {
            if (axios.isCancel?.(e) || e.name === 'CanceledError') return;
            if (requestId !== chatsRequest.current.id) return;
            setChats((prev) => prev || []);
            setChatsError('Не удалось загрузить чаты');
        });
    };

    const loadThread = (chat, { before = null } = {}) => {
        threadRequest.current.controller?.abort();
        const controller = new AbortController();
        const requestId = threadRequest.current.id + 1;
        threadRequest.current = { id: requestId, controller };
        if (!before) setThread(null); else setThreadLoadingMore(true);
        axios.get(`${apiBaseUrl}/api/wazzup/chat-messages`, {
            headers: headers(), signal: controller.signal,
            params: { channel_id: chat.channelId, chat_id: chat.chatId,
                      before: before || undefined, limit: THREAD_PAGE },
        }).then((r) => {
            if (requestId !== threadRequest.current.id) return;
            setThreadHasMore(Boolean(r.data.hasMore));
            setThreadLoadingMore(false);
            if (before) {
                const box = threadBox.current;
                const prevHeight = box ? box.scrollHeight : 0;
                setThread((prev) => [...(r.data.items || []), ...(prev || [])]);
                // сохранить позицию скролла при подгрузке вверх
                requestAnimationFrame(() => {
                    if (box) box.scrollTop = box.scrollHeight - prevHeight;
                });
            } else {
                setThread(r.data.items || []);
                requestAnimationFrame(() => {
                    if (threadBox.current) threadBox.current.scrollTop = threadBox.current.scrollHeight;
                });
            }
        }).catch((e) => {
            if (axios.isCancel?.(e) || e.name === 'CanceledError') return;
            if (requestId !== threadRequest.current.id) return;
            setThreadLoadingMore(false);
            if (!before) setThread([]);
            showToast?.('Не удалось загрузить переписку', 'error');
        });
    };

    useEffect(() => { loadChannels(); loadChats(); /* eslint-disable-next-line */ }, [apiBaseUrl]);

    const pickChannel = (cid) => {
        setChannelId(cid);
        setSelected(null); setThread(null);
        loadChats({ channel: cid });
    };

    const onSearchInput = (value) => {
        setSearch(value);
        clearTimeout(searchDebounce.current);
        searchDebounce.current = setTimeout(() => {
            setAppliedSearch(value.trim());
            loadChats({ q: value.trim() });
        }, 350);
    };

    const openChat = (chat) => {
        setSelected(chat);
        setThreadHasMore(false);
        loadThread(chat);
    };

    const refreshAll = () => {
        loadChannels();
        loadChats();
        if (selected) loadThread(selected);
    };

    // Группировка ленты по дням для разделителей
    const threadWithDays = useMemo(() => {
        if (!thread) return [];
        const out = [];
        let lastDay = null;
        thread.forEach((m) => {
            const day = (m.dt || '').slice(0, 10);
            if (day && day !== lastDay) { out.push({ _day: fmtDay(m.dt), messageId: `day-${day}` }); lastDay = day; }
            out.push(m);
        });
        return out;
    }, [thread]);

    const activeChannels = (channels || []).filter((c) => (c.chatsCount || 0) > 0 || c.state === 'active');

    return (
        <div className="w-full" style={{ fontFamily: APPLE_FONT }}>
            <div className="mb-3 flex items-center justify-between gap-2 px-1">
                <div>
                    <h2 className="text-lg font-semibold text-slate-900">Чаты Верификаторов</h2>
                    <p className="text-xs text-slate-500">
                        Переписка Wazzup; история хранится 45 дней, более ранняя — в самом Wazzup
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <a href="https://app.wazzup24.com/" target="_blank" rel="noopener noreferrer"
                       className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50">
                        <ExternalLink size={13} /> Открыть в Wazzup
                    </a>
                    <button onClick={refreshAll}
                            className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50">
                        <RefreshCw size={13} /> Обновить
                    </button>
                </div>
            </div>

            <div className={`${iosCard} flex overflow-hidden`} style={{ height: 'calc(100vh - 170px)', minHeight: 420 }}>
                {/* Каналы */}
                <div className="hidden w-56 shrink-0 flex-col overflow-y-auto border-r border-slate-100 bg-slate-50/60 md:flex">
                    <button onClick={() => pickChannel('')}
                            className={`px-4 py-3 text-left text-sm hover:bg-slate-100 ${!channelId ? 'bg-slate-100 font-semibold' : ''}`}>
                        Все каналы
                    </button>
                    {channels === null && (
                        <div className="flex items-center gap-2 px-4 py-3 text-xs text-slate-400">
                            <Loader2 size={13} className="animate-spin" /> Загрузка…
                        </div>
                    )}
                    {activeChannels.map((c) => (
                        <button key={c.channelId} onClick={() => pickChannel(c.channelId)}
                                className={`px-4 py-2.5 text-left hover:bg-slate-100 ${channelId === c.channelId ? 'bg-slate-100' : ''}`}>
                            <div className={`truncate text-sm ${channelId === c.channelId ? 'font-semibold' : ''} text-slate-800`}>
                                {c.name || c.plainId || c.channelId}
                            </div>
                            <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
                                {c.plainId && <span className="truncate">{c.plainId}</span>}
                                {(c.chatsCount || 0) > 0 && <IosBadge tone="slate">{c.chatsCount}</IosBadge>}
                            </div>
                        </button>
                    ))}
                </div>

                {/* Список чатов */}
                <div className="flex w-80 shrink-0 flex-col border-r border-slate-100">
                    <div className="border-b border-slate-100 p-2">
                        <div className="relative">
                            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                            <input value={search} onChange={(e) => onSearchInput(e.target.value)}
                                   placeholder="Имя или телефон…"
                                   className={`${iosInput} w-full pl-8 text-sm`} />
                        </div>
                    </div>
                    <div className="flex-1 overflow-y-auto">
                        {chats === null && (
                            <div className="flex items-center justify-center gap-2 py-8 text-sm text-slate-400">
                                <Loader2 size={15} className="animate-spin" /> Загрузка чатов…
                            </div>
                        )}
                        {chatsError && (
                            <div className="flex items-center justify-center gap-2 py-6 text-sm text-rose-500">
                                <AlertCircle size={15} /> {chatsError}
                            </div>
                        )}
                        {chats !== null && chats.length === 0 && !chatsError && (
                            <div className="px-4 py-8 text-center text-sm text-slate-400">
                                Чатов пока нет — сбор идёт с 17.07.2026
                            </div>
                        )}
                        {(chats || []).map((chat) => {
                            const isSel = selected && selected.chatId === chat.chatId && selected.channelId === chat.channelId;
                            return (
                                <button key={`${chat.channelId}:${chat.chatId}`} onClick={() => openChat(chat)}
                                        className={`block w-full border-b border-slate-50 px-3 py-2.5 text-left hover:bg-slate-50 ${isSel ? 'bg-blue-50' : ''}`}>
                                    <div className="flex items-baseline justify-between gap-2">
                                        <span className="truncate text-sm font-medium text-slate-900">
                                            {chat.contactName || chat.contactPhone || chat.chatId}
                                        </span>
                                        <span className="shrink-0 text-[11px] text-slate-400">{fmtListDate(chat.lastMessageAt)}</span>
                                    </div>
                                    <div className="mt-0.5 flex items-center gap-1 text-xs text-slate-500">
                                        {chat.lastMessageIsEcho && <Headset size={11} className="shrink-0 text-emerald-500" />}
                                        <span className="truncate">{chat.lastMessageText || '—'}</span>
                                    </div>
                                    {!channelId && (
                                        <div className="mt-0.5 truncate text-[10px] text-slate-400">
                                            {channelName[chat.channelId] || chat.channelId}
                                        </div>
                                    )}
                                </button>
                            );
                        })}
                        {chats !== null && chats.length < chatsTotal && (
                            <button onClick={() => loadChats({ reset: false })}
                                    className="block w-full py-2.5 text-center text-xs font-medium text-blue-600 hover:bg-slate-50">
                                Показать ещё ({chats.length} из {chatsTotal})
                            </button>
                        )}
                    </div>
                </div>

                {/* Лента переписки */}
                <div className="flex min-w-0 flex-1 flex-col bg-slate-50/70">
                    {!selected && (
                        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-slate-400">
                            <MessageSquare size={32} strokeWidth={1.5} />
                            <span className="text-sm">Выберите чат слева</span>
                        </div>
                    )}
                    {selected && (
                        <>
                            <div className="flex items-center justify-between gap-2 border-b border-slate-100 bg-white px-4 py-2.5">
                                <div className="min-w-0">
                                    <div className="flex items-center gap-1.5 truncate text-sm font-semibold text-slate-900">
                                        <User2 size={14} className="shrink-0 text-slate-400" />
                                        {selected.contactName || selected.contactPhone || selected.chatId}
                                    </div>
                                    <div className="truncate text-[11px] text-slate-400">
                                        {selected.contactPhone && `${selected.contactPhone} · `}
                                        {channelName[selected.channelId] || selected.channelId}
                                    </div>
                                </div>
                                <div className="shrink-0 text-[11px] text-slate-400">
                                    {selected.inboundCount ?? 0} вх. / {selected.outboundCount ?? 0} исх.
                                </div>
                            </div>
                            <div ref={threadBox} className="flex-1 space-y-1.5 overflow-y-auto py-3">
                                {thread === null && (
                                    <div className="flex items-center justify-center gap-2 py-8 text-sm text-slate-400">
                                        <Loader2 size={15} className="animate-spin" /> Загрузка переписки…
                                    </div>
                                )}
                                {thread !== null && threadHasMore && (
                                    <div className="flex justify-center pb-1">
                                        <button disabled={threadLoadingMore}
                                                onClick={() => loadThread(selected, { before: thread[0]?.dt })}
                                                className="flex items-center gap-1 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs text-slate-500 hover:bg-slate-50 disabled:opacity-50">
                                            {threadLoadingMore ? <Loader2 size={12} className="animate-spin" /> : <ChevronUp size={12} />}
                                            Более ранние
                                        </button>
                                    </div>
                                )}
                                {threadWithDays.map((m) => m._day ? (
                                    <div key={m.messageId} className="flex justify-center py-1">
                                        <span className="rounded-full bg-slate-200/70 px-3 py-0.5 text-[11px] text-slate-500">{m._day}</span>
                                    </div>
                                ) : (
                                    <MessageBubble key={m.messageId} msg={m} />
                                ))}
                                {thread !== null && thread.length === 0 && (
                                    <div className="py-8 text-center text-sm text-slate-400">Сообщений нет</div>
                                )}
                            </div>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}
