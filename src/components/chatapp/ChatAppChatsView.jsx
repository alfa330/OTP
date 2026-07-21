import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Search, RefreshCw, Loader2, AlertCircle, MessageSquare, Users, Bot, Wand2,
    Link2, FileText, Download, CloudDownload, Inbox, KeyRound, Layers,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosInput, iosGroupLabel, iosBtnGhost, iosBtnPrimary,
    IosBadge, IosModal,
} from '../ui/ios';
import { IosDateRangePicker, isoDate, rangeLabel } from '../ui/DateRangePicker';

/* Чаты ChatApp (ТП/ОП ТЭЗ): переписка «как в мессенджере» + привязка
 * сотрудников ChatApp к нашим операторам.
 *
 * Отличие от «Чатов Верификаторов»: у ChatApp нет вебхуков — данные приносит
 * ночной синк через API, поэтому здесь же живёт кнопка ручного запуска и
 * счётчики последнего прогона. Переписка хранится 45 дней, эпизоды дольше. */

const PAGE_SIZE = 30;
const THREAD_PAGE = 50;

const fmtTime = (iso) => (iso
    ? new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
    : '');

const fmtDay = (iso) => (iso
    ? new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })
    : '');

const fmtListDate = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toDateString() === new Date().toDateString()
        ? fmtTime(iso)
        : d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
};

const fmtDateTime = (iso) => (iso
    ? new Date(iso).toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
    : '—');

const MEDIA_LABELS = {
    image: 'Фото', video: 'Видео', voice: 'Голосовое', audio: 'Аудио',
    file: 'Документ', files: 'Документы', sticker: 'Стикер',
    location: 'Геолокация', call_log: 'Звонок', template: 'Шаблон',
    form: 'Форма', order: 'Заказ',
};

const previewText = (chat) => {
    if (chat.lastMessageText) return chat.lastMessageText;
    return MEDIA_LABELS[chat.lastMessageType] || '—';
};

const initials = (name) => {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return '?';
    return (parts[0][0] + (parts[1]?.[0] || '')).toUpperCase();
};

const AVATAR_HUES = [
    'from-sky-400 to-blue-500', 'from-emerald-400 to-teal-500',
    'from-violet-400 to-purple-500', 'from-amber-400 to-orange-500',
    'from-rose-400 to-pink-500', 'from-cyan-400 to-sky-500',
];
const avatarClass = (name) => {
    let h = 0;
    for (const ch of String(name || '')) h = (h * 31 + ch.charCodeAt(0)) % 997;
    return AVATAR_HUES[h % AVATAR_HUES.length];
};

const Avatar = ({ name, size = 'h-9 w-9', muted = false, icon: Icon = null }) => (
    <div className={`${size} flex shrink-0 items-center justify-center rounded-full bg-gradient-to-br text-[12px] font-semibold text-white shadow-sm ${
        muted ? 'from-slate-300 to-slate-400' : avatarClass(name)}`}>
        {Icon ? <Icon size={15} /> : initials(name)}
    </div>
);

const SegButton = ({ active, onClick, icon: Icon, children }) => (
    <button onClick={onClick}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-semibold transition ${
                active ? 'bg-white text-slate-900 shadow-sm ring-1 ring-slate-200/70'
                       : 'text-slate-500 hover:text-slate-700'}`}>
        {Icon && <Icon size={13} />} {children}
    </button>
);

/* Медиа в пузыре: фото с лайтбоксом, аудио/видео плееры, остальное — чип-ссылка. */
function MediaContent({ msg, light }) {
    const [failed, setFailed] = useState(false);
    const [zoom, setZoom] = useState(false);
    const uri = msg.fileLink;
    const ct = String(msg.fileContentType || '');
    const isImage = msg.type === 'image' || ct.startsWith('image/');
    const isVideo = msg.type === 'video' || ct.startsWith('video/');
    const isAudio = msg.type === 'voice' || msg.type === 'audio' || ct.startsWith('audio/');

    if (uri && !failed && isImage) {
        return (
            <>
                <img src={uri} alt="" loading="lazy" onError={() => setFailed(true)}
                     onClick={() => setZoom(true)}
                     className="max-h-64 w-auto max-w-full cursor-zoom-in rounded-xl" />
                <IosModal open={zoom} onClose={() => setZoom(false)} title="Фото" maxWidth="max-w-3xl">
                    <img src={uri} alt="" className="mx-auto max-h-[72vh] w-auto rounded-2xl" />
                    <div className="mt-3 text-center">
                        <a href={uri} target="_blank" rel="noopener noreferrer"
                           className="text-[13px] font-medium text-blue-600 hover:underline">
                            Открыть оригинал
                        </a>
                    </div>
                </IosModal>
            </>
        );
    }
    if (uri && !failed && isVideo) {
        return <video controls preload="metadata" src={uri} onError={() => setFailed(true)}
                      className="max-h-64 w-auto max-w-full rounded-xl" />;
    }
    if (uri && !failed && isAudio) {
        return <audio controls preload="none" src={uri} onError={() => setFailed(true)}
                      className="h-10 w-64 max-w-full" />;
    }
    const label = msg.fileName || MEDIA_LABELS[msg.type] || msg.type || 'Вложение';
    const chip = `inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[12.5px] font-medium ${
        light ? 'bg-white/20 text-white' : 'bg-slate-100 text-slate-600'}`;
    return uri
        ? <a href={uri} target="_blank" rel="noopener noreferrer" className={`${chip} hover:opacity-80`}>
              <FileText size={13} /> {label}
          </a>
        : <span className={chip}><FileText size={13} /> {label}</span>;
}

/* Пузырь: исходящие оператора — синие справа, автоответы — приглушённые
 * серо-синие (их видно, но не спутать с работой человека), клиент — белые. */
function MessageBubble({ msg }) {
    const out = msg.side === 'out';
    const auto = out && msg.isAutoreply;
    const hasMedia = Boolean(msg.fileLink) || (msg.type && msg.type !== 'text');
    const tone = !out
        ? 'rounded-bl-md bg-white text-slate-900 ring-1 ring-slate-200/60'
        : auto
            ? 'rounded-br-md bg-slate-200 text-slate-600'
            : 'rounded-br-md bg-blue-500 text-white';
    return (
        <div className={`flex ${out ? 'justify-end' : 'justify-start'} px-4`}>
            <div className={`max-w-[75%] rounded-2xl px-3 py-2 text-[13.5px] leading-snug shadow-[0_1px_1px_rgba(15,23,42,0.05)] ${tone} ${
                msg.isDeleted ? 'opacity-70' : ''}`}>
                {out && (
                    <div className={`mb-0.5 flex items-center gap-1 text-[11px] font-semibold ${
                        auto ? 'text-slate-500' : 'text-blue-100'}`}>
                        {auto ? <><Bot size={10} /> Автоответ</> : (msg.authorName || 'Оператор')}
                    </div>
                )}
                {hasMedia && (
                    <div className="mb-1"><MediaContent msg={msg} light={out && !auto} /></div>
                )}
                {msg.text && <div className="whitespace-pre-wrap break-words">{msg.text}</div>}
                <div className={`mt-0.5 text-right text-[10.5px] ${
                    !out ? 'text-slate-400' : auto ? 'text-slate-500' : 'text-blue-100'}`}>
                    {fmtTime(msg.dt)}
                </div>
            </div>
        </div>
    );
}

/* Вкладка «Операторы»: привязка сотрудников ChatApp к нашим операторам.
 * Без неё эпизод остаётся без оператора и не попадает в «Случайный чат». */
function OperatorsTab({ apiBaseUrl, headers, showToast }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [savingId, setSavingId] = useState(null);
    const [filter, setFilter] = useState('');

    const load = () => {
        setData(null); setError(null);
        axios.get(`${apiBaseUrl}/api/chatapp/authors`, { headers: headers() })
            .then((r) => setData({ items: r.data.authors || [], operators: r.data.operators || [] }))
            .catch(() => setError('Не удалось загрузить сотрудников'));
    };
    useEffect(() => { load(); /* eslint-disable-next-line */ }, [apiBaseUrl]);

    const save = (author, patch) => {
        const next = { userId: author.user_id, isBot: author.is_bot, ...patch };
        if (patch.isBot) next.userId = null;
        setSavingId(author.employee_id);
        axios.post(`${apiBaseUrl}/api/chatapp/authors/map`, {
            employee_id: author.employee_id,
            user_id: next.isBot ? null : next.userId,
            is_bot: next.isBot,
        }, { headers: headers() }).then(() => {
            setSavingId(null);
            const opName = (data.operators.find((o) => o.id === next.userId) || {}).name || null;
            setData((prev) => ({
                ...prev,
                items: prev.items.map((it) => it.employee_id === author.employee_id
                    ? { ...it, user_id: next.isBot ? null : next.userId,
                        user_name: next.isBot ? null : opName, is_bot: next.isBot }
                    : it),
            }));
        }).catch(() => {
            setSavingId(null);
            showToast?.('Не удалось сохранить привязку', 'error');
        });
    };

    const needle = filter.trim().toLowerCase();
    const filtered = (data?.items || []).filter((a) => !needle
        || String(a.employee_name || '').toLowerCase().includes(needle)
        || String(a.email || '').toLowerCase().includes(needle));
    const pending = filtered.filter((a) => !a.user_id && !a.is_bot);
    const done = filtered.filter((a) => a.user_id || a.is_bot);

    const renderGroup = (title, list, emptyHint) => (
        <section className="space-y-1.5">
            <div className="flex items-center justify-between">
                <div className={iosGroupLabel}>{title}</div>
                <span className="pr-1 text-[11px] text-slate-400">{list.length}</span>
            </div>
            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                {list.length === 0 && (
                    <div className="px-4 py-5 text-center text-[13px] text-slate-400">{emptyHint}</div>
                )}
                {list.map((a) => (
                    <AuthorRow key={a.employee_id} author={a} operators={data.operators}
                               saving={savingId === a.employee_id} onSave={save} />
                ))}
            </div>
        </section>
    );

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                    {data && (
                        <>
                            <IosBadge tone="slate">{data.items.length} сотрудников</IosBadge>
                            {pending.length > 0 && <IosBadge tone="amber">без привязки: {pending.length}</IosBadge>}
                            {data.items.some((a) => a.is_bot) && (
                                <IosBadge tone="slate">
                                    <Bot size={11} /> ботов: {data.items.filter((a) => a.is_bot).length}
                                </IosBadge>
                            )}
                        </>
                    )}
                </div>
                <div className="flex items-center gap-2">
                    <div className="relative">
                        <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input value={filter} onChange={(e) => setFilter(e.target.value)}
                               placeholder="Поиск сотрудника…"
                               className={`${iosInput} w-52 py-2 pl-8 text-[13px]`} />
                    </div>
                    <button onClick={load} className={iosBtnGhost}><RefreshCw size={13} /> Обновить</button>
                </div>
            </div>

            {data === null && !error && (
                <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-[13px] text-slate-400`}>
                    <Loader2 size={15} className="animate-spin" /> Загрузка…
                </div>
            )}
            {error && (
                <div className={`${iosCard} flex items-center justify-center gap-2 py-10 text-[13px] text-rose-500`}>
                    <AlertCircle size={15} /> {error}
                </div>
            )}
            {data && (
                <>
                    {renderGroup('Требуют привязки', pending,
                        filter ? 'Никого не найдено' : 'Все сотрудники привязаны — отлично')}
                    {renderGroup('Привязанные и боты', done,
                        'Пока пусто: привяжите сотрудников к операторам или отметьте ботов')}
                    <p className="px-1 text-[11px] text-slate-500">
                        Привязка соединяет автора исходящих сообщений ChatApp с оператором в системе —
                        по ней эпизод получает оператора и попадает в «Случайный чат» журнала оценок.
                        «Бот» исключает интеграции и автоответы из атрибуции.
                    </p>
                </>
            )}
        </div>
    );
}

function AuthorRow({ author, operators, saving, onSave }) {
    const suggested = author.suggested_user;
    return (
        <div className={`flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 ${author.is_bot ? 'opacity-55' : ''}`}>
            <div className="flex min-w-0 flex-1 items-center gap-3" style={{ minWidth: 220 }}>
                <Avatar name={author.employee_name} muted={author.is_bot} icon={author.is_bot ? Bot : null} />
                <div className="min-w-0">
                    <div className="truncate text-[14px] font-semibold text-slate-900">
                        {author.employee_name || author.email || `#${author.employee_id}`}
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-slate-400">
                        {author.role_name && <span>{author.role_name} ·</span>}
                        <span>{author.messages} сообщ.</span>
                        {author.last_message_at && <span>· {fmtListDate(author.last_message_at)}</span>}
                    </div>
                </div>
            </div>
            <div className="flex items-center gap-2">
                {!author.is_bot && !author.user_id && suggested && !saving && (
                    <button onClick={() => onSave(author, { userId: suggested.id })}
                            title={`Похоже, это ${suggested.name}`}
                            className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-3 py-1.5 text-[12px] font-semibold text-amber-700 ring-1 ring-amber-200 transition hover:bg-amber-100 active:scale-[0.97]">
                        <Wand2 size={12} /> {suggested.name}
                    </button>
                )}
                <div className="relative">
                    <select value={author.user_id ?? ''} disabled={author.is_bot || saving}
                            onChange={(e) => onSave(author, { userId: e.target.value ? Number(e.target.value) : null })}
                            className={`w-56 appearance-none rounded-xl border-0 px-3.5 py-2 pr-8 text-[13px] font-medium focus:outline-none focus:ring-2 focus:ring-blue-500/70 disabled:cursor-not-allowed ${
                                author.user_id ? 'bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200'
                                               : 'bg-slate-100 text-slate-600'}`}>
                        <option value="">— не привязан —</option>
                        {operators.map((o) => (
                            <option key={o.id} value={o.id}>
                                {o.name}{o.direction_name ? ` · ${o.direction_name}` : ''}
                            </option>
                        ))}
                    </select>
                    <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
                        {saving ? <Loader2 size={13} className="animate-spin" />
                                : author.user_id ? <Link2 size={13} className="text-emerald-500" />
                                : <svg width="10" height="6" viewBox="0 0 10 6" fill="none">
                                      <path d="M1 1l4 4 4-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                                  </svg>}
                    </span>
                </div>
                <label className="flex cursor-pointer items-center gap-1.5 text-[12px] font-medium text-slate-500">
                    <input type="checkbox" checked={!!author.is_bot} disabled={saving}
                           onChange={(e) => onSave(author, { isBot: e.target.checked })}
                           className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" />
                    Бот
                </label>
            </div>
        </div>
    );
}

export default function ChatAppChatsView(props) {
    const { apiBaseUrl, withAccessTokenHeader, showToast } = props;
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const [mainTab, setMainTab] = useState('chats');

    const [overview, setOverview] = useState(null);
    const [licenseId, setLicenseId] = useState('');
    const [chats, setChats] = useState(null);
    const [chatsTotal, setChatsTotal] = useState(0);
    const [chatsError, setChatsError] = useState(null);
    const [search, setSearch] = useState('');
    const [appliedSearch, setAppliedSearch] = useState('');
    const [selected, setSelected] = useState(null);
    const [thread, setThread] = useState(null);
    const [threadHasMore, setThreadHasMore] = useState(false);
    const [threadLoadingMore, setThreadLoadingMore] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [rebuilding, setRebuilding] = useState(false);
    // Период синка по умолчанию — последние 7 дней: ночной джоб берёт трое
    // суток, так что неделя перекрывает его с запасом и не тянет лишнего.
    const [syncRange, setSyncRange] = useState(() => {
        const to = new Date();
        const from = new Date();
        from.setDate(from.getDate() - 6);
        return { from: isoDate(from), to: isoDate(to) };
    });
    const chatsRequest = useRef({ id: 0, controller: null });
    const threadRequest = useRef({ id: 0, controller: null });
    const threadBox = useRef(null);
    const searchDebounce = useRef(null);

    const loadOverview = () => {
        axios.get(`${apiBaseUrl}/api/chatapp/overview`, { headers: headers() })
            .then((r) => setOverview(r.data))
            .catch(() => setOverview({ licenses: [], chats: 0, messages: 0, episodes: 0 }));
    };

    const loadChats = ({ reset = true, license = licenseId, q = appliedSearch } = {}) => {
        chatsRequest.current.controller?.abort();
        const controller = new AbortController();
        const requestId = chatsRequest.current.id + 1;
        chatsRequest.current = { id: requestId, controller };
        const offset = reset ? 0 : (chats?.length || 0);
        if (reset) { setChats(null); setChatsError(null); }
        axios.get(`${apiBaseUrl}/api/chatapp/chats`, {
            headers: headers(), signal: controller.signal,
            params: { license_id: license || undefined, q: q || undefined, limit: PAGE_SIZE, offset },
        }).then((r) => {
            if (requestId !== chatsRequest.current.id) return;
            setChatsTotal(r.data.total || 0);
            setChats((prev) => (reset ? (r.data.items || []) : [...(prev || []), ...(r.data.items || [])]));
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
        axios.get(`${apiBaseUrl}/api/chatapp/chat-messages`, {
            headers: headers(), signal: controller.signal,
            params: { license_id: chat.licenseId, messenger_type: chat.messengerType,
                      chat_id: chat.chatId, before: before || undefined, limit: THREAD_PAGE },
        }).then((r) => {
            if (requestId !== threadRequest.current.id) return;
            setThreadHasMore(Boolean(r.data.hasMore));
            setThreadLoadingMore(false);
            if (before) {
                const box = threadBox.current;
                const prevHeight = box ? box.scrollHeight : 0;
                setThread((prev) => [...(r.data.items || []), ...(prev || [])]);
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

    useEffect(() => { loadOverview(); loadChats(); /* eslint-disable-next-line */ }, [apiBaseUrl]);

    const runSync = () => {
        if (syncing || !syncRange.from) return;
        setSyncing(true);
        axios.post(`${apiBaseUrl}/api/chatapp/sync`,
                   { date_from: syncRange.from, date_to: syncRange.to || syncRange.from },
                   { headers: headers() })
            .then((r) => {
                setSyncing(false);
                if (r.data?.skipped === 'no_credentials') {
                    showToast?.('Доступы ChatApp не заданы в окружении сервера', 'error');
                    return;
                }
                showToast?.(`Синхронизация готова: ${r.data.chats ?? 0} чатов, ${r.data.messages ?? 0} сообщений`, 'success');
                loadOverview(); loadChats();
            })
            .catch((e) => {
                setSyncing(false);
                showToast?.(e?.response?.data?.error || 'Синхронизация не удалась', 'error');
            });
    };

    // Пересборка эпизодов из уже скачанных сообщений (в API не ходит). Нужна,
    // когда длинный синк прервался до сборки — переписка есть, эпизодов нет.
    const runRebuild = () => {
        if (rebuilding) return;
        setRebuilding(true);
        axios.post(`${apiBaseUrl}/api/chatapp/sync`, { rebuild_only: true }, { headers: headers() })
            .then((r) => {
                setRebuilding(false);
                const n = r.data?.episodes?.stored ?? 0;
                showToast?.(`Эпизоды пересобраны: +${n}`, 'success');
                loadOverview();
            })
            .catch((e) => {
                setRebuilding(false);
                showToast?.(e?.response?.data?.error || 'Не удалось пересобрать эпизоды', 'error');
            });
    };

    // Пресеты пикера под синк: «Весь период» тут не годится — синк всегда
    // тянет конкретное окно, а ретеншн переписки всё равно 45 дней.
    const syncPresets = useMemo(() => {
        const back = (n) => () => {
            const to = new Date();
            const from = new Date();
            from.setDate(from.getDate() - (n - 1));
            return { from: isoDate(from), to: isoDate(to) };
        };
        return [
            { label: '7 дней', range: back(7) },
            { label: '30 дней', range: back(30) },
            { label: '45 дней', range: back(45) },
        ];
    }, []);

    const pickLicense = (id) => {
        setLicenseId(id);
        setSelected(null); setThread(null);
        loadChats({ license: id });
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
        loadOverview();
        loadChats();
        if (selected) loadThread(selected);
    };

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

    const licenses = overview?.licenses || [];
    const isEmpty = chats !== null && chats.length === 0 && !appliedSearch && !licenseId;

    return (
        <div className="w-full" style={{ fontFamily: APPLE_FONT }}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
                <div>
                    <h2 className="text-lg font-semibold tracking-tight text-slate-900">Чаты ChatApp</h2>
                    <p className="text-xs text-slate-500">
                        Переписка ТП и ОП ТЭЗ; хранится 45 дней, обновляется ночным синком
                        {overview?.lastSyncAt ? ` · последний: ${fmtDateTime(overview.lastSyncAt)}` : ''}
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <div className="flex rounded-xl bg-slate-100 p-1">
                        <SegButton active={mainTab === 'chats'} onClick={() => setMainTab('chats')} icon={MessageSquare}>
                            Чаты
                        </SegButton>
                        <SegButton active={mainTab === 'authors'} onClick={() => setMainTab('authors')} icon={Users}>
                            Операторы
                        </SegButton>
                    </div>
                    {/* Глубже 45 дней выбирать нечего: наш ретеншн переписки
                        всё равно удалит её ближайшей ночью. */}
                    <IosDateRangePicker
                        from={syncRange.from} to={syncRange.to}
                        max={isoDate(new Date())}
                        min={isoDate(new Date(Date.now() - 45 * 864e5))}
                        presets={syncPresets}
                        onChange={setSyncRange} />
                    <button onClick={runSync} disabled={syncing || !syncRange.from}
                            title={`Забрать переписку за ${rangeLabel(syncRange.from, syncRange.to)}`}
                            className={iosBtnPrimary}>
                        {syncing ? <Loader2 size={13} className="animate-spin" /> : <CloudDownload size={13} />}
                        {syncing ? 'Синхронизирую…' : 'Синхронизировать'}
                    </button>
                    <button onClick={runRebuild} disabled={rebuilding} className={iosBtnGhost}
                            title="Пересобрать эпизоды из уже скачанной переписки (без обращения к ChatApp)">
                        {rebuilding ? <Loader2 size={13} className="animate-spin" /> : <Layers size={13} />}
                        Эпизоды
                    </button>
                    {mainTab === 'chats' && (
                        <button onClick={refreshAll} className={iosBtnGhost}>
                            <RefreshCw size={13} /> Обновить
                        </button>
                    )}
                </div>
            </div>

            {overview && overview.configured === false && (
                <div className={`${iosCard} mb-3 flex items-center gap-2 px-4 py-3 text-[13px] text-amber-700 ring-amber-200`}>
                    <KeyRound size={15} />
                    Доступы ChatApp не заданы в окружении сервера — синхронизация будет пропускаться.
                    Нужны CHATAPP_EMAIL, CHATAPP_PASSWORD и CHATAPP_APP_ID.
                </div>
            )}

            {overview && mainTab === 'chats' && (
                <div className="mb-3 flex flex-wrap items-center gap-2 px-1">
                    <IosBadge tone="slate">{overview.chats} чатов</IosBadge>
                    <IosBadge tone="slate">{overview.messages} сообщений</IosBadge>
                    <IosBadge tone="slate">
                        {overview.episodesAttributed}/{overview.episodes} эпизодов с оператором
                    </IosBadge>
                    {overview.episodes === 0 && overview.messages > 0 && (
                        <IosBadge tone="amber">
                            эпизоды не собраны — нажмите «Эпизоды»
                        </IosBadge>
                    )}
                    {overview.authorsUnmapped > 0 && (
                        <IosBadge tone="amber">без привязки: {overview.authorsUnmapped}</IosBadge>
                    )}
                </div>
            )}

            {mainTab === 'authors' && (
                <OperatorsTab apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} />
            )}

            <div className={`${iosCard} flex overflow-hidden`}
                 style={{ height: 'calc(100vh - 210px)', minHeight: 420,
                          display: mainTab === 'chats' ? undefined : 'none' }}>
                {/* Лицензии */}
                <div className="hidden w-56 shrink-0 flex-col overflow-y-auto border-r border-slate-100 bg-slate-50/70 py-1.5 md:flex">
                    <button onClick={() => pickLicense('')}
                            className={`mx-1.5 rounded-lg px-3 py-2.5 text-left text-[13.5px] transition ${
                                !licenseId ? 'bg-white font-semibold text-slate-900 shadow-sm ring-1 ring-slate-200/60'
                                           : 'text-slate-600 hover:bg-slate-100'}`}>
                        Все лицензии
                    </button>
                    {overview === null && (
                        <div className="flex items-center gap-2 px-4 py-3 text-xs text-slate-400">
                            <Loader2 size={13} className="animate-spin" /> Загрузка…
                        </div>
                    )}
                    {licenses.map((l) => (
                        <button key={`${l.licenseId}-${l.messengerType}`}
                                onClick={() => pickLicense(String(l.licenseId))}
                                className={`mx-1.5 rounded-lg px-3 py-2.5 text-left transition ${
                                    String(licenseId) === String(l.licenseId)
                                        ? 'bg-white shadow-sm ring-1 ring-slate-200/60'
                                        : 'hover:bg-slate-100'}`}>
                            <div className={`truncate text-[13.5px] ${
                                String(licenseId) === String(l.licenseId)
                                    ? 'font-semibold text-slate-900' : 'text-slate-600'}`}>
                                Лицензия {l.licenseId}
                            </div>
                            <div className="text-[11px] text-slate-400">
                                {l.messengerType} · {l.chats} чат.
                            </div>
                        </button>
                    ))}
                </div>

                {/* Список чатов */}
                <div className="flex w-full shrink-0 flex-col border-r border-slate-100 sm:w-80">
                    <div className="border-b border-slate-100 p-2.5">
                        <div className="relative">
                            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                            <input value={search} onChange={(e) => onSearchInput(e.target.value)}
                                   placeholder="Имя, телефон или id чата…"
                                   className={`${iosInput} py-2 pl-8 text-[13px]`} />
                        </div>
                    </div>
                    <div className="flex-1 overflow-y-auto">
                        {chats === null && !chatsError && (
                            <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-slate-400">
                                <Loader2 size={14} className="animate-spin" /> Загрузка…
                            </div>
                        )}
                        {chatsError && (
                            <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-rose-500">
                                <AlertCircle size={14} /> {chatsError}
                            </div>
                        )}
                        {isEmpty && (
                            <div className="px-6 py-12 text-center">
                                <Inbox size={26} className="mx-auto mb-2 text-slate-300" />
                                <div className="text-[13.5px] font-medium text-slate-600">Переписки пока нет</div>
                                <div className="mt-1 text-[12px] text-slate-400">
                                    Нажмите «Синхронизировать», чтобы забрать чаты из ChatApp
                                </div>
                            </div>
                        )}
                        {(chats || []).map((c) => {
                            const active = selected && selected.chatId === c.chatId
                                && selected.licenseId === c.licenseId;
                            return (
                                <button key={`${c.licenseId}-${c.chatId}`} onClick={() => openChat(c)}
                                        className={`flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition ${
                                            active ? 'bg-blue-50/70' : 'hover:bg-slate-50'}`}>
                                    <Avatar name={c.name || c.phone} />
                                    <div className="min-w-0 flex-1">
                                        <div className="flex items-baseline justify-between gap-2">
                                            <span className="truncate text-[13.5px] font-semibold text-slate-900">
                                                {c.name || c.phone || c.chatId}
                                            </span>
                                            <span className="shrink-0 text-[11px] text-slate-400">
                                                {fmtListDate(c.lastMessageAt)}
                                            </span>
                                        </div>
                                        <div className="truncate text-[12.5px] text-slate-500">
                                            {c.lastMessageSide === 'out' && <span className="text-slate-400">Вы: </span>}
                                            {previewText(c)}
                                        </div>
                                        {c.responsibleName && (
                                            <div className="truncate text-[11px] text-slate-400">
                                                Ответственный: {c.responsibleName}
                                            </div>
                                        )}
                                    </div>
                                </button>
                            );
                        })}
                        {chats && chats.length < chatsTotal && (
                            <button onClick={() => loadChats({ reset: false })}
                                    className="w-full py-3 text-[13px] font-medium text-blue-600 hover:bg-slate-50">
                                Показать ещё ({chatsTotal - chats.length})
                            </button>
                        )}
                    </div>
                </div>

                {/* Лента */}
                <div className="hidden flex-1 flex-col bg-slate-50/60 sm:flex">
                    {!selected && (
                        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-slate-400">
                            <MessageSquare size={28} />
                            <div className="text-[13.5px]">Выберите чат слева</div>
                        </div>
                    )}
                    {selected && (
                        <>
                            <div className="flex items-center gap-3 border-b border-slate-100 bg-white/80 px-4 py-2.5 backdrop-blur">
                                <Avatar name={selected.name || selected.phone} size="h-8 w-8" />
                                <div className="min-w-0">
                                    <div className="truncate text-[14px] font-semibold text-slate-900">
                                        {selected.name || selected.phone || selected.chatId}
                                    </div>
                                    <div className="truncate text-[11.5px] text-slate-400">
                                        {selected.phone || selected.chatId} · {selected.messagesCount} сообщ.
                                        {selected.responsibleName ? ` · ${selected.responsibleName}` : ''}
                                    </div>
                                </div>
                            </div>
                            <div ref={threadBox} className="flex-1 space-y-1.5 overflow-y-auto py-3">
                                {thread === null && (
                                    <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-slate-400">
                                        <Loader2 size={14} className="animate-spin" /> Загрузка переписки…
                                    </div>
                                )}
                                {thread && threadHasMore && (
                                    <div className="px-4 pb-1 text-center">
                                        <button onClick={() => loadThread(selected, { before: thread[0]?.dt })}
                                                disabled={threadLoadingMore} className={iosBtnGhost}>
                                            {threadLoadingMore
                                                ? <><Loader2 size={13} className="animate-spin" /> Загружаю…</>
                                                : <><Download size={13} /> Показать раньше</>}
                                        </button>
                                    </div>
                                )}
                                {thread && thread.length === 0 && (
                                    <div className="py-10 text-center text-[13px] text-slate-400">
                                        Переписка не сохранилась — вероятно, её удалил ретеншн 45 дней
                                    </div>
                                )}
                                {threadWithDays.map((m) => (m._day ? (
                                    <div key={m.messageId} className="py-2 text-center">
                                        <span className="rounded-full bg-slate-200/70 px-2.5 py-1 text-[11px] font-medium text-slate-500">
                                            {m._day}
                                        </span>
                                    </div>
                                ) : (
                                    <MessageBubble key={m.messageId} msg={m} />
                                )))}
                            </div>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}
