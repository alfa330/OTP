import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertCircle, ArrowLeft, CheckCircle2, ChevronRight, Inbox, Loader2,
    History, MessageSquare, Paperclip, Plus, RefreshCw, Search, Send, Settings2, Trash2, Users, X,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosInput, iosGroupLabel,
    iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosBadge, IosModal, IosToggle,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import TicketWizard from './TicketWizard';

/* Раздел «Обращения» — тикеты в рабочие Telegram-группы.
 *
 * Оператор заводит обращение здесь, бот относит его в нужную группу, ответы
 * сотрудников возвращаются в эту же карточку. Смысл раздела в том, чтобы в
 * Telegram руками не ходил никто.
 *
 * Раскладка — две панели, как в почте macOS: слева лента обращений, справа
 * переписка по выбранному. Вкладок «список» и «карточка» намеренно нет:
 * оператор работает с обращением, не выпуская из виду очередь остальных, а на
 * телефоне вторая панель разворачивается на весь экран.
 *
 * Про цвет. Красим ТОЛЬКО то, что требует действия: пришедший ответ, провал
 * доставки, горящий срок. «Новое» и «Решено» остаются нейтральными — иначе
 * список из тридцати обращений превращается в светофор, по которому ничего
 * не читается. */

// Статусы. tone: null = нейтральный (в списке ничем не красится).
const STATUS_META = {
    open: { label: 'Отправлено', tone: null },
    in_progress: { label: 'В работе', tone: 'amber' },
    answered: { label: 'Есть ответ', tone: 'blue' },
    resolved: { label: 'Решено', tone: 'green' },
    cancelled: { label: 'Отменено', tone: null },
};

const PRIORITY_META = {
    low: { label: 'Низкий', tone: null },
    normal: { label: 'Обычный', tone: null },
    high: { label: 'Высокий', tone: 'amber' },
    critical: { label: 'Критический', tone: 'red' },
};

// Фильтр по состоянию. «Активные» — то, что ещё не закрыто; это рабочий
// экран по умолчанию, архив открывается отдельным сегментом.
const STATE_FILTERS = [
    { key: 'active', label: 'В работе', statuses: 'open,in_progress,answered' },
    { key: 'answered', label: 'Ответили', statuses: 'answered' },
    { key: 'closed', label: 'Закрытые', statuses: 'resolved,cancelled' },
    { key: 'all', label: 'Все', statuses: '' },
];

const PAGE_SIZE = 40;

// Подписи событий истории. Технические коды человеку не показываем.
const EVENT_LABELS = {
    created: 'Обращение создано',
    sent: 'Отправлено в группу',
    send_failed: 'Не удалось отправить',
    reply_received: 'Ответ из группы',
    reply_sent: 'Сообщение в группу',
    status: 'Статус изменён',
};

const statusMeta = (code) => STATUS_META[code] || { label: code || '—', tone: null };

// Просрочен ли срок ответа. Только для незакрытых: у решённого обращения срок
// уже ничего не значит, и красить его — врать про состояние дел.
const isOverdue = (ticket) => Boolean(
    ticket.due_at
    && ['open', 'in_progress', 'answered'].includes(ticket.status)
    && new Date(ticket.due_at).getTime() < Date.now(),
);
const priorityMeta = (code) => PRIORITY_META[code] || { label: code || '—', tone: null };

const fmtDateTime = (iso) => (iso
    ? new Date(iso).toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    })
    : '—');

const fmtTime = (iso) => (iso
    ? new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
    : '');

/* «5 мин назад» — в ленте важнее давность, чем точное время. Точное всё равно
 * стоит в подсказке и в самой переписке. */
const fmtAgo = (iso) => {
    if (!iso) return '';
    const secs = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    if (secs < 60) return 'только что';
    if (secs < 3600) return `${Math.floor(secs / 60)} мин`;
    if (secs < 86400) return `${Math.floor(secs / 3600)} ч`;
    if (secs < 7 * 86400) return `${Math.floor(secs / 86400)} дн`;
    return new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
};

const fmtSla = (minutes) => {
    if (!minutes) return 'без срока';
    if (minutes % 1440 === 0) return `${minutes / 1440} дн.`;
    if (minutes % 60 === 0) return `${minutes / 60} ч.`;
    return `${minutes} мин.`;
};

const errorText = (error, fallback) => (
    error?.response?.data?.error || error?.message || fallback
);

const EmptyBlock = ({ icon: Icon = Inbox, children, hint }) => (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
        <Icon size={22} className="text-slate-300" />
        <div className="text-[13px] text-slate-400">{children}</div>
        {hint && <div className="max-w-[280px] text-[11.5px] leading-snug text-slate-400">{hint}</div>}
    </div>
);

const LoadingBlock = () => (
    <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-slate-400">
        <Loader2 size={15} className="animate-spin" /> Загрузка…
    </div>
);

/* ─── Лента обращений ─────────────────────────────────────────────────────── */

const TicketRow = memo(function TicketRow({ ticket, active, onSelect }) {
    const status = statusMeta(ticket.status);
    const priority = priorityMeta(ticket.priority);
    const failed = ticket.delivery_status === 'failed';
    const overdue = isOverdue(ticket);
    return (
        <button
            type="button"
            onClick={() => onSelect(ticket.id)}
            className={`w-full border-b border-slate-100 px-3.5 py-3 text-left transition-colors ${
                active ? 'bg-blue-50/70' : 'hover:bg-slate-50'
            }`}
        >
            <div className="flex items-start gap-2.5">
                {/* Точка непрочитанного — единственный «сигнал» в строке.
                    Место под неё держим всегда, иначе текст прыгает. */}
                <span className={`mt-[6px] h-[7px] w-[7px] shrink-0 rounded-full ${
                    ticket.unread ? 'bg-blue-500' : 'bg-transparent'
                }`} />
                <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                        <span className="shrink-0 text-[11.5px] font-semibold tabular-nums text-slate-400">
                            №{ticket.id}
                        </span>
                        <span className={`truncate text-[13.5px] leading-snug ${
                            ticket.unread ? 'font-semibold text-slate-900' : 'font-medium text-slate-800'
                        }`}>
                            {ticket.subject}
                        </span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-slate-500">
                        <span className="truncate">{ticket.queue_title}</span>
                        <span className="text-slate-300">·</span>
                        <span className="tabular-nums">{fmtAgo(ticket.last_message_at || ticket.created_at)}</span>
                        {failed && (
                            <IosBadge tone="red" className="!py-0.5 !text-[10.5px]">Не доставлено</IosBadge>
                        )}
                        {!failed && overdue && (
                            <IosBadge tone="amber" className="!py-0.5 !text-[10.5px]">Просрочено</IosBadge>
                        )}
                        {!failed && !overdue && status.tone && (
                            <IosBadge tone={status.tone} className="!py-0.5 !text-[10.5px]">{status.label}</IosBadge>
                        )}
                        {priority.tone && (
                            <IosBadge tone={priority.tone} className="!py-0.5 !text-[10.5px]">{priority.label}</IosBadge>
                        )}
                        {(ticket.flags || []).includes('mass_outage') && (
                            <IosBadge tone="red" className="!py-0.5 !text-[10.5px]">Массовый сбой</IosBadge>
                        )}
                    </div>
                </div>
            </div>
        </button>
    );
});

/* ─── Сообщение в переписке ───────────────────────────────────────────────── */

const MessageBubble = ({ message, apiBaseUrl, ticketId, headers, showToast }) => {
    const outgoing = message.direction === 'out';
    const note = message.direction === 'note';
    const [downloading, setDownloading] = useState(false);

    /* Вложение забираем через прокси раздела: файл лежит в Telegram, и прямую
       ссылку сохранить нельзя — она живёт около часа. Скачиваем с заголовками
       авторизации, поэтому обычный <a href> не подходит. */
    const openAttachment = async () => {
        setDownloading(true);
        try {
            const response = await axios.get(
                `${apiBaseUrl}/api/crm/tickets/${ticketId}/attachments/${message.id}`,
                { headers: headers(), responseType: 'blob' },
            );
            const url = URL.createObjectURL(response.data);
            window.open(url, '_blank', 'noopener');
            setTimeout(() => URL.revokeObjectURL(url), 60000);
        } catch (error) {
            showToast?.(errorText(error, 'Не удалось открыть вложение'), 'error');
        } finally {
            setDownloading(false);
        }
    };

    return (
        <div className={`flex ${outgoing ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[76%] rounded-2xl px-3.5 py-2.5 text-[13px] leading-relaxed ${
                note
                    ? 'bg-amber-50 text-amber-900 ring-1 ring-amber-100'
                    : outgoing
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-100 text-slate-800'
            }`}>
                {!outgoing && message.author_name && (
                    <div className="mb-0.5 text-[11.5px] font-semibold text-slate-500">
                        {message.author_name}
                    </div>
                )}
                {message.body && <div className="whitespace-pre-wrap break-words">{message.body}</div>}
                {message.attachment && (
                    <button
                        type="button"
                        onClick={openAttachment}
                        disabled={downloading}
                        className={`mt-1.5 inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[12px] font-medium transition ${
                            outgoing ? 'bg-white/15 hover:bg-white/25' : 'bg-white hover:bg-slate-50'
                        }`}
                    >
                        {downloading ? <Loader2 size={12} className="animate-spin" /> : <Paperclip size={12} />}
                        {message.attachment.name || 'Вложение'}
                    </button>
                )}
                <div className={`mt-1 text-right text-[10.5px] tabular-nums ${
                    outgoing ? 'text-white/70' : 'text-slate-400'
                }`}>
                    {fmtTime(message.created_at)}
                </div>
            </div>
        </div>
    );
};

/* ─── Карточка обращения ──────────────────────────────────────────────────── */

const TicketCard = ({
    ticketId, apiBaseUrl, headers, showToast, onChanged, onBack, pulse,
}) => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [reply, setReply] = useState('');
    const [sending, setSending] = useState(false);
    const [attachment, setAttachment] = useState(null);
    // История действий не приезжает вместе с карточкой: она нужна изредка и
    // почти вся повторяет то, что видно в переписке. Один запрос по кнопке
    // вместо лишнего запроса на каждое открытие обращения.
    const [events, setEvents] = useState(null);
    const [eventsLoading, setEventsLoading] = useState(false);
    const fileRef = useRef(null);
    const threadRef = useRef(null);

    const load = useCallback(async (silent = false) => {
        if (!silent) setLoading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/crm/tickets/${ticketId}`,
                { headers: headers() });
            setData(response.data);
            setError(null);
        } catch (err) {
            setError(errorText(err, 'Не удалось открыть обращение'));
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, headers, ticketId]);

    useEffect(() => { load(); }, [load]);

    /* Обновление по «тычку» колокола, а не по таймеру: пришёл ответ из группы —
       сервер разбудил вкладку, и карточка перечитывается. Фонового опроса в
       портале нет и заводить его здесь нельзя. */
    useEffect(() => {
        if (!pulse) return;
        load(true);
    }, [pulse]); // eslint-disable-line react-hooks/exhaustive-deps

    // Лента всегда прокручена к свежему сообщению — как в любом мессенджере.
    useEffect(() => {
        const node = threadRef.current;
        if (node) node.scrollTop = node.scrollHeight;
    }, [data?.messages?.length]);

    const ticket = data?.item;
    const permissions = data?.permissions || {};

    const send = async () => {
        const body = reply.trim();
        if (!body && !attachment) return;
        setSending(true);
        try {
            const form = new FormData();
            form.append('body', body);
            if (attachment) form.append('attachment', attachment);
            const response = await axios.post(
                `${apiBaseUrl}/api/crm/tickets/${ticketId}/messages`, form,
                { headers: headers() },
            );
            setData((prev) => (prev ? { ...prev, messages: response.data.messages } : prev));
            setReply('');
            setAttachment(null);
            if (fileRef.current) fileRef.current.value = '';
            onChanged?.();
        } catch (err) {
            showToast?.(errorText(err, 'Сообщение не ушло'), 'error');
        } finally {
            setSending(false);
        }
    };

    const toggleEvents = async () => {
        if (events) { setEvents(null); return; }
        setEventsLoading(true);
        try {
            const response = await axios.get(
                `${apiBaseUrl}/api/crm/tickets/${ticketId}/events`, { headers: headers() });
            setEvents(response.data.events || []);
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось открыть историю'), 'error');
        } finally {
            setEventsLoading(false);
        }
    };

    const changeStatus = async (status) => {
        try {
            await axios.post(`${apiBaseUrl}/api/crm/tickets/${ticketId}/status`, { status },
                { headers: headers() });
            await load(true);
            onChanged?.();
        } catch (err) {
            showToast?.(errorText(err, 'Статус не изменился'), 'error');
        }
    };

    const resend = async () => {
        try {
            await axios.post(`${apiBaseUrl}/api/crm/tickets/${ticketId}/resend`, {},
                { headers: headers() });
            await load(true);
            onChanged?.();
            showToast?.('Обращение отправлено в группу', 'success');
        } catch (err) {
            showToast?.(errorText(err, 'Отправить не получилось'), 'error');
        }
    };

    if (loading) return <LoadingBlock />;
    if (error) {
        return (
            <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-rose-500">
                <AlertCircle size={15} /> {error}
            </div>
        );
    }
    if (!ticket) return null;

    const status = statusMeta(ticket.status);
    const priority = priorityMeta(ticket.priority);
    const closed = ticket.status === 'resolved' || ticket.status === 'cancelled';

    return (
        <div className="flex h-full min-h-0 flex-col">
            {/* Шапка карточки */}
            <div className="shrink-0 border-b border-slate-200/70 px-4 py-3">
                <div className="flex items-start gap-2">
                    {onBack && (
                        <button type="button" onClick={onBack}
                                className="-ml-1 mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 lg:hidden">
                            <ArrowLeft size={16} />
                        </button>
                    )}
                    <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                            <span className="text-[12px] font-semibold tabular-nums text-slate-400">
                                №{ticket.id}
                            </span>
                            <h3 className="text-[15px] font-semibold leading-tight text-slate-900">
                                {ticket.subject}
                            </h3>
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-slate-500">
                            <span>{ticket.queue_title}</span>
                            {ticket.topic_title && (
                                <>
                                    <span className="text-slate-300">·</span>
                                    <span>{ticket.topic_title}</span>
                                </>
                            )}
                            <span className="text-slate-300">·</span>
                            <span>{ticket.created_by_name}</span>
                            <span className="text-slate-300">·</span>
                            <span className="tabular-nums">{fmtDateTime(ticket.created_at)}</span>
                            {ticket.due_at && (
                                <>
                                    <span className="text-slate-300">·</span>
                                    <span className={`tabular-nums ${isOverdue(ticket) ? 'font-semibold text-amber-600' : ''}`}>
                                        ответ до {fmtDateTime(ticket.due_at)}
                                    </span>
                                </>
                            )}
                        </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                        {status.tone
                            ? <IosBadge tone={status.tone}>{status.label}</IosBadge>
                            : <span className="text-[11.5px] text-slate-400">{status.label}</span>}
                        {priority.tone && <IosBadge tone={priority.tone}>{priority.label}</IosBadge>}
                    </div>
                </div>

                {(ticket.client_name || ticket.client_phone) && (
                    <div className="mt-2 flex items-center gap-1.5 text-[11.5px] text-slate-500">
                        <Users size={12} className="text-slate-400" />
                        {[ticket.client_name, ticket.client_phone].filter(Boolean).join(' · ')}
                    </div>
                )}

                {ticket.delivery_status === 'failed' && (
                    <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-xl bg-rose-50 px-3 py-2 text-[12px] text-rose-700 ring-1 ring-rose-100">
                        <span>Обращение не ушло в Telegram: {ticket.delivery_error || 'причина неизвестна'}</span>
                        <button type="button" onClick={resend}
                                className="inline-flex items-center gap-1.5 rounded-lg bg-white px-2.5 py-1 text-[12px] font-semibold text-rose-700 transition hover:bg-rose-100">
                            <RefreshCw size={12} /> Отправить ещё раз
                        </button>
                    </div>
                )}
            </div>

            {/* Переписка */}
            <div ref={threadRef} className="min-h-0 flex-1 space-y-2.5 overflow-y-auto bg-slate-50/60 px-4 py-4">
                <div className="rounded-2xl bg-white px-3.5 py-3 text-[13px] leading-relaxed text-slate-700 ring-1 ring-slate-200/70">
                    <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                        Обращение
                    </div>
                    <div className="whitespace-pre-wrap break-words">{ticket.body}</div>
                </div>
                {(data.messages || [])
                    /* Корневое сообщение уже показано блоком «Обращение» выше —
                       второй раз тем же текстом это дубль, а не переписка. */
                    .filter((m, index) => !(index === 0 && m.direction === 'out' && m.body === ticket.body))
                    .map((message) => (
                        <MessageBubble key={message.id} message={message} ticketId={ticket.id}
                                       apiBaseUrl={apiBaseUrl} headers={headers}
                                       showToast={showToast} />
                    ))}
                {ticket.resolved_at && (
                    <div className="flex items-center justify-center gap-1.5 py-1 text-[11.5px] text-emerald-600">
                        <CheckCircle2 size={13} />
                        Решено{ticket.resolved_by_name ? ` · ${ticket.resolved_by_name}` : ''} · {fmtDateTime(ticket.resolved_at)}
                    </div>
                )}
            </div>

            {/* Ответ и действия */}
            <div className="shrink-0 border-t border-slate-200/70 bg-white px-4 py-3">
                {permissions.can_reply ? (
                    <div className="flex items-end gap-2">
                        <button type="button" onClick={() => fileRef.current?.click()}
                                title="Прикрепить файл"
                                className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500 transition hover:bg-slate-200 active:scale-95">
                            <Paperclip size={15} />
                        </button>
                        <input ref={fileRef} type="file" className="hidden"
                               onChange={(e) => setAttachment(e.target.files?.[0] || null)} />
                        <div className="min-w-0 flex-1">
                            {attachment && (
                                <div className="mb-1.5 inline-flex items-center gap-1.5 rounded-lg bg-slate-100 px-2 py-1 text-[11.5px] text-slate-600">
                                    <Paperclip size={11} /> {attachment.name}
                                    <button type="button" onClick={() => { setAttachment(null); if (fileRef.current) fileRef.current.value = ''; }}
                                            className="text-slate-400 hover:text-slate-600">
                                        <X size={11} />
                                    </button>
                                </div>
                            )}
                            <textarea
                                value={reply}
                                onChange={(e) => setReply(e.target.value)}
                                onKeyDown={(e) => {
                                    // Enter отправляет, Shift+Enter переносит строку —
                                    // привычка из любого мессенджера.
                                    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
                                }}
                                rows={1}
                                placeholder="Написать в группу…"
                                className={`${iosInput} resize-none py-2`}
                            />
                        </div>
                        <button type="button" onClick={send}
                                disabled={sending || (!reply.trim() && !attachment)}
                                className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-blue-600 text-white transition hover:bg-blue-700 active:scale-95 disabled:opacity-40">
                            {sending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
                        </button>
                    </div>
                ) : (
                    <div className="text-center text-[12px] text-slate-400">
                        {closed ? 'Обращение закрыто' : 'Писать в это обращение нельзя'}
                    </div>
                )}

                {permissions.can_change_status && (
                    <div className="mt-2.5 flex flex-wrap items-center gap-2">
                        <button type="button" onClick={toggleEvents} className={iosBtnGhost}>
                            {eventsLoading ? <Loader2 size={13} className="animate-spin" /> : <History size={13} />}
                            История
                        </button>
                        {!closed && (
                            <>
                                <button type="button" onClick={() => changeStatus('resolved')}
                                        className={iosBtnSecondary}>
                                    <CheckCircle2 size={14} /> Вопрос решён
                                </button>
                                <button type="button" onClick={() => changeStatus('cancelled')}
                                        className={iosBtnGhost}>
                                    Отменить
                                </button>
                            </>
                        )}
                        {closed && (
                            <button type="button" onClick={() => changeStatus('open')}
                                    className={iosBtnSecondary}>
                                <RefreshCw size={14} /> Вернуть в работу
                            </button>
                        )}
                    </div>
                )}

                {events && (
                    <div className="mt-2.5 space-y-1 border-t border-slate-100 pt-2.5">
                        {events.length === 0 && (
                            <div className="text-[11.5px] text-slate-400">Событий нет</div>
                        )}
                        {events.map((event) => (
                            <div key={event.id} className="flex items-baseline gap-2 text-[11.5px] text-slate-500">
                                <span className="w-[86px] shrink-0 tabular-nums text-slate-400">
                                    {fmtDateTime(event.created_at)}
                                </span>
                                <span>{EVENT_LABELS[event.kind] || event.kind}</span>
                                {event.actor_name && <span className="text-slate-400">· {event.actor_name}</span>}
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
};

/* ─── Настройка очередей (админ) ──────────────────────────────────────────── */

const QueuesTab = ({ apiBaseUrl, headers, showToast, queues, onReload }) => {
    const [chats, setChats] = useState([]);
    const [editing, setEditing] = useState(null);
    const [topicDraft, setTopicDraft] = useState({});

    useEffect(() => {
        let cancelled = false;
        axios.get(`${apiBaseUrl}/api/crm/chats`, { headers: headers() })
            .then((response) => { if (!cancelled) setChats(response.data.items || []); })
            .catch(() => { if (!cancelled) setChats([]); });
        return () => { cancelled = true; };
    }, [apiBaseUrl, headers]);

    const save = async (draft) => {
        try {
            if (draft.id) {
                await axios.patch(`${apiBaseUrl}/api/crm/queues/${draft.id}`, draft,
                    { headers: headers() });
            } else {
                await axios.post(`${apiBaseUrl}/api/crm/queues`, draft, { headers: headers() });
            }
            setEditing(null);
            onReload();
            showToast?.('Очередь сохранена', 'success');
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось сохранить очередь'), 'error');
        }
    };

    const removeQueue = async (queue) => {
        if (!window.confirm(`Удалить очередь «${queue.title}»?`)) return;
        try {
            await axios.delete(`${apiBaseUrl}/api/crm/queues/${queue.id}`, { headers: headers() });
            onReload();
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось удалить'), 'error');
        }
    };

    const addTopic = async (queue) => {
        const title = (topicDraft[queue.id] || '').trim();
        if (!title) return;
        try {
            await axios.post(`${apiBaseUrl}/api/crm/queues/${queue.id}/topics`, { title },
                { headers: headers() });
            setTopicDraft((prev) => ({ ...prev, [queue.id]: '' }));
            onReload();
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось добавить тематику'), 'error');
        }
    };

    const removeTopic = async (topic) => {
        try {
            await axios.delete(`${apiBaseUrl}/api/crm/topics/${topic.id}`, { headers: headers() });
            onReload();
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось убрать тематику'), 'error');
        }
    };

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="max-w-[640px] text-[12px] leading-snug text-slate-500">
                    Очередь — это адрес обращения: одна рабочая Telegram-группа. Чтобы группа
                    появилась в списке, добавьте в неё бота — он запомнит чат сам.
                </p>
                <button type="button" className={iosBtnPrimary}
                        onClick={() => setEditing({ title: '', description: '', chat_id: '', sla_minutes: '' })}>
                    <Plus size={14} /> Очередь
                </button>
            </div>

            {!queues.length && (
                <div className={`${iosCard}`}>
                    <EmptyBlock icon={Inbox} hint="Заведите первую очередь и привяжите к ней группу — операторы сразу смогут отправлять обращения.">
                        Очередей пока нет
                    </EmptyBlock>
                </div>
            )}

            {queues.map((queue) => (
                <div key={queue.id} className={`${iosCard} p-4`}>
                    <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0">
                            <div className="flex items-center gap-2">
                                <span className="text-[14px] font-semibold text-slate-900">{queue.title}</span>
                                {!queue.is_active && <IosBadge>Выключена</IosBadge>}
                                {!queue.is_ready && <IosBadge tone="amber">Группа не привязана</IosBadge>}
                            </div>
                            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-slate-500">
                                <span>{queue.chat_title || 'Telegram-группа не выбрана'}</span>
                                <span className="text-slate-300">·</span>
                                <span>Срок ответа: {fmtSla(queue.sla_minutes)}</span>
                            </div>
                            {queue.description && (
                                <div className="mt-1 text-[11.5px] leading-snug text-slate-500">{queue.description}</div>
                            )}
                        </div>
                        <div className="flex items-center gap-2">
                            <IosToggle checked={queue.is_active}
                                       onChange={(value) => save({ id: queue.id, is_active: value })} />
                            <button type="button" onClick={() => setEditing({ ...queue })} className={iosBtnGhost}>
                                Изменить
                            </button>
                            <button type="button" onClick={() => removeQueue(queue)}
                                    className="grid h-8 w-8 place-items-center rounded-xl text-slate-400 transition hover:bg-rose-50 hover:text-rose-500">
                                <Trash2 size={14} />
                            </button>
                        </div>
                    </div>

                    <div className="mt-3 border-t border-slate-100 pt-3">
                        <div className={iosGroupLabel}>Тематики</div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                            {(queue.topics || []).map((topic) => (
                                <span key={topic.id}
                                      className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-[11.5px] text-slate-600">
                                    {topic.title}
                                    <button type="button" onClick={() => removeTopic(topic)}
                                            className="text-slate-400 transition hover:text-rose-500">
                                        <X size={11} />
                                    </button>
                                </span>
                            ))}
                            <input
                                value={topicDraft[queue.id] || ''}
                                onChange={(e) => setTopicDraft((prev) => ({ ...prev, [queue.id]: e.target.value }))}
                                onKeyDown={(e) => { if (e.key === 'Enter') addTopic(queue); }}
                                placeholder="+ тематика"
                                className="w-36 rounded-full bg-slate-100 px-3 py-1 text-[11.5px] text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/60"
                            />
                        </div>
                    </div>
                </div>
            ))}

            <IosModal
                open={!!editing}
                onClose={() => setEditing(null)}
                title={editing?.id ? 'Очередь' : 'Новая очередь'}
                subtitle="Куда уходят обращения и как быстро на них ждут ответ"
                footer={(
                    <>
                        <button type="button" onClick={() => setEditing(null)} className={iosBtnSecondary}>Отмена</button>
                        <button type="button" onClick={() => save(editing)}
                                disabled={!editing?.title?.trim()} className={iosBtnPrimary}>
                            Сохранить
                        </button>
                    </>
                )}
            >
                {editing && (
                    <div className="space-y-3.5">
                        <div>
                            <div className={iosGroupLabel}>Название</div>
                            <input value={editing.title || ''}
                                   onChange={(e) => setEditing((p) => ({ ...p, title: e.target.value }))}
                                   placeholder="Например, iTaxi"
                                   className={`mt-1.5 ${iosInput}`} />
                        </div>
                        <div>
                            <div className={iosGroupLabel}>Telegram-группа</div>
                            <CustomSelect
                                className="mt-1.5"
                                variant="ios"
                                value={editing.chat_id ? String(editing.chat_id) : ''}
                                onChange={(value) => setEditing((p) => ({ ...p, chat_id: value }))}
                                options={chats.map((chat) => ({
                                    value: String(chat.chat_id),
                                    label: chat.used_by_queue && String(chat.chat_id) !== String(editing.chat_id)
                                        ? `${chat.title} — занята «${chat.used_by_queue}»`
                                        : chat.title,
                                    disabled: !!chat.used_by_queue && String(chat.chat_id) !== String(editing.chat_id),
                                }))}
                                placeholder={chats.length ? 'Выберите группу' : 'Бота нет ни в одной группе'}
                                searchable
                                ariaLabel="Telegram-группа очереди"
                            />
                        </div>
                        <div>
                            <div className={iosGroupLabel}>Описание для оператора</div>
                            <textarea value={editing.description || ''}
                                      onChange={(e) => setEditing((p) => ({ ...p, description: e.target.value }))}
                                      rows={2}
                                      placeholder="С чем сюда обращаться"
                                      className={`mt-1.5 ${iosInput} resize-y`} />
                        </div>
                        <div>
                            <div className={iosGroupLabel}>Срок ответа, минут</div>
                            <input value={editing.sla_minutes ?? ''} inputMode="numeric"
                                   onChange={(e) => setEditing((p) => ({ ...p, sla_minutes: e.target.value.replace(/\D/g, '') }))}
                                   placeholder="Не ограничен"
                                   className={`mt-1.5 ${iosInput} tabular-nums`} />
                            <div className="mt-1.5 px-1 text-[11.5px] text-slate-500">
                                Показывается в сообщении группы и подсвечивает просроченные обращения.
                            </div>
                        </div>
                    </div>
                )}
            </IosModal>
        </div>
    );
};

/* ─── Раздел целиком ──────────────────────────────────────────────────────── */

export default function CrmTicketsView({
    apiBaseUrl, withAccessTokenHeader, showToast, realtimePulse, onUnreadChange,
    focusRequest,
}) {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );

    const [tab, setTab] = useState('tickets');
    const [capabilities, setCapabilities] = useState(null);
    const [queues, setQueues] = useState([]);
    const [scenarioCatalog, setScenarioCatalog] = useState([]);
    const [tickets, setTickets] = useState([]);
    const [counters, setCounters] = useState({});
    const [hasMore, setHasMore] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [selectedId, setSelectedId] = useState(null);
    const [composerOpen, setComposerOpen] = useState(false);

    const [stateFilter, setStateFilter] = useState('active');
    const [queueFilter, setQueueFilter] = useState('');
    const [mine, setMine] = useState(true);
    const [search, setSearch] = useState('');
    const [offset, setOffset] = useState(0);

    const searchTimer = useRef(null);
    const [searchApplied, setSearchApplied] = useState('');

    // Поиск не дёргает сервер на каждую букву: печатают быстрее, чем отвечает база.
    useEffect(() => {
        if (searchTimer.current) clearTimeout(searchTimer.current);
        searchTimer.current = setTimeout(() => setSearchApplied(search.trim()), 350);
        return () => { if (searchTimer.current) clearTimeout(searchTimer.current); };
    }, [search]);

    /* Каталог тематик: вопросы, обязательные проверки и правила приходят с
       сервера вместе с признаком «очередь готова». Тематику без привязанной
       Telegram-группы оператору предлагать нельзя — он пройдёт все вопросы и
       упрётся в «отправлять некуда». */
    const loadScenarios = useCallback(async () => {
        try {
            const response = await axios.get(`${apiBaseUrl}/api/crm/scenarios`,
                { headers: headers() });
            setScenarioCatalog(response.data.items || []);
        } catch (err) {
            setScenarioCatalog([]);
        }
    }, [apiBaseUrl, headers]);

    const loadQueues = useCallback(async () => {
        try {
            const response = await axios.get(`${apiBaseUrl}/api/crm/queues?all=1`,
                { headers: headers() });
            setQueues(response.data.items || []);
        } catch (err) {
            setQueues([]);
        }
    }, [apiBaseUrl, headers]);

    const loadTickets = useCallback(async (nextOffset = 0, silent = false) => {
        if (!silent) setLoading(true);
        try {
            const params = new URLSearchParams();
            const statuses = STATE_FILTERS.find((f) => f.key === stateFilter)?.statuses;
            if (statuses) params.set('status', statuses);
            if (queueFilter) params.set('queue_id', queueFilter);
            if (mine) params.set('mine', '1');
            if (searchApplied) params.set('q', searchApplied);
            params.set('limit', String(PAGE_SIZE));
            params.set('offset', String(nextOffset));
            const response = await axios.get(`${apiBaseUrl}/api/crm/tickets?${params}`,
                { headers: headers() });
            const items = response.data.items || [];
            setTickets((prev) => (nextOffset ? [...prev, ...items] : items));
            setHasMore(Boolean(response.data.has_more));
            // Права приезжают вместе со списком — они уже посчитаны на сервере
            // для этого запроса, и отдельный поход за ними разделу не нужен.
            if (response.data.capabilities) setCapabilities(response.data.capabilities);
            setOffset(nextOffset);
            setError(null);
        } catch (err) {
            setError(errorText(err, 'Не удалось загрузить обращения'));
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, headers, stateFilter, queueFilter, mine, searchApplied]);

    /* Один раз при входе: числа для шапки и признак, что схема развернулась.
       Агрегаты по периметру считаются только здесь — на каждый фильтр и каждую
       букву в поиске платить проходом по таблице незачем. */
    useEffect(() => {
        let cancelled = false;
        axios.get(`${apiBaseUrl}/api/crm/ping`, { headers: headers() })
            .then((response) => {
                if (cancelled) return;
                setCapabilities(response.data.capabilities || {});
                setCounters(response.data.counters || {});
                if (response.data.schema_ready === false) {
                    setError('Раздел разворачивается — обновите страницу через минуту');
                }
            })
            .catch((err) => { if (!cancelled) setError(errorText(err, 'Раздел недоступен')); });
        loadQueues();
        loadScenarios();
        return () => { cancelled = true; };
    }, [apiBaseUrl, headers, loadQueues, loadScenarios]);

    useEffect(() => { loadTickets(0); }, [loadTickets]);

    /* Реалтайм. Собственного канала раздел не открывает: «тычок» уже приходит
       колоколу по SSE, и App отдаёт его сюда счётчиком. Второй поток на
       пользователя занял бы ещё одну нить waitress — их на сервере считаные. */
    useEffect(() => {
        if (!realtimePulse) return;
        loadTickets(0, true);
    }, [realtimePulse]); // eslint-disable-line react-hooks/exhaustive-deps

    // Бейдж раздела в сайдбаре ведёт сервер — здесь только передаём наверх.
    useEffect(() => {
        if (counters.unread === undefined) return;
        onUnreadChange?.(Number(counters.unread) || 0);
    }, [counters.unread, onUnreadChange]);

    /* Переход из колокола: открываем именно то обращение, о котором уведомили.
       Карточка грузится по своему id, поэтому фильтр списка её не прячет —
       иначе «Пришёл ответ» по уже решённому обращению вёл бы в пустоту.
       requestId в зависимостях, а не ticketId: повторный клик по тому же
       уведомлению должен снова открыть карточку. */
    useEffect(() => {
        if (!focusRequest?.ticketId) return;
        setSelectedId(Number(focusRequest.ticketId));
        setTab('tickets');
    }, [focusRequest?.requestId]); // eslint-disable-line react-hooks/exhaustive-deps

    const canManage = !!capabilities?.can_manage_queues;
    const readyScenarios = useMemo(
        () => scenarioCatalog.filter((item) => item.is_ready),
        [scenarioCatalog],
    );

    const refreshAfterChange = useCallback(() => {
        loadTickets(0, true);
    }, [loadTickets]);

    return (
        <div className="w-full" style={{ fontFamily: APPLE_FONT }}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
                <div>
                    <h2 className="text-lg font-semibold tracking-tight text-slate-900">Обращения</h2>
                    <p className="text-xs text-slate-500">
                        Заявки в рабочие Telegram-группы: ответ коллег возвращается сюда, в карточку
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    {canManage && (
                        <div className="flex rounded-xl bg-slate-100 p-1">
                            {[
                                { key: 'tickets', label: 'Обращения', icon: MessageSquare },
                                { key: 'queues', label: 'Очереди', icon: Settings2 },
                            ].map((item) => (
                                <button key={item.key} type="button" onClick={() => setTab(item.key)}
                                        className={`flex items-center gap-1.5 rounded-[9px] px-3.5 py-1.5 text-[12.5px] font-semibold transition-all ${
                                            tab === item.key
                                                ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                                                : 'text-slate-500 hover:text-slate-700'
                                        }`}>
                                    <item.icon size={13} /> {item.label}
                                </button>
                            ))}
                        </div>
                    )}
                    {tab === 'tickets' && (
                        <button type="button" onClick={() => setComposerOpen(true)}
                                disabled={!readyScenarios.length}
                                title={readyScenarios.length ? undefined
                                    : 'Ни к одной тематике не привязана Telegram-группа'}
                                className={iosBtnPrimary}>
                            <Plus size={14} /> Новое обращение
                        </button>
                    )}
                </div>
            </div>

            {tab === 'queues' && canManage && (
                <QueuesTab apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast}
                           queues={queues} onReload={loadQueues} />
            )}

            {tab === 'tickets' && (
                <>
                    {/* Фильтры. Ничего не подсвечиваем «на всякий случай»: выбранное
                        состояние видно по сегменту, остальное — нейтрально. */}
                    <div className="mb-3 flex flex-wrap items-center gap-2 px-1">
                        <div className="flex rounded-xl bg-slate-100 p-1">
                            {STATE_FILTERS.map((item) => (
                                <button key={item.key} type="button"
                                        onClick={() => { setStateFilter(item.key); setSelectedId(null); }}
                                        className={`rounded-[9px] px-3 py-1.5 text-[12.5px] font-semibold transition-all ${
                                            stateFilter === item.key
                                                ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                                                : 'text-slate-500 hover:text-slate-700'
                                        }`}>
                                    {item.label}
                                </button>
                            ))}
                        </div>

                        {capabilities?.scope && capabilities.scope !== 'own' && (
                            <div className="flex rounded-xl bg-slate-100 p-1">
                                {[
                                    { key: true, label: 'Мои' },
                                    { key: false, label: 'Все' },
                                ].map((item) => (
                                    <button key={String(item.key)} type="button"
                                            onClick={() => { setMine(item.key); setSelectedId(null); }}
                                            className={`rounded-[9px] px-3 py-1.5 text-[12.5px] font-semibold transition-all ${
                                                mine === item.key
                                                    ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                                                    : 'text-slate-500 hover:text-slate-700'
                                            }`}>
                                        {item.label}
                                    </button>
                                ))}
                            </div>
                        )}

                        {queues.length > 1 && (
                            <CustomSelect
                                className="w-48"
                                variant="ios"
                                value={queueFilter}
                                onChange={(value) => { setQueueFilter(value); setSelectedId(null); }}
                                options={[{ value: '', label: 'Все группы' }].concat(
                                    queues.map((q) => ({ value: String(q.id), label: q.title })),
                                )}
                                placeholder="Все группы"
                                ariaLabel="Фильтр по очереди"
                            />
                        )}

                        <div className="relative min-w-[180px] flex-1 sm:max-w-[280px]">
                            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                            <input value={search} onChange={(e) => setSearch(e.target.value)}
                                   placeholder="Номер, тема, телефон"
                                   className={`${iosInput} pl-9`} />
                        </div>
                    </div>

                    <div className={`${iosCard} overflow-hidden`}>
                        <div className="flex min-h-[560px] flex-col lg:flex-row">
                            {/* Лента */}
                            <div className={`flex w-full shrink-0 flex-col border-slate-200/70 lg:w-[360px] lg:border-r ${
                                selectedId ? 'hidden lg:flex' : 'flex'
                            }`}>
                                <div className="min-h-0 flex-1 overflow-y-auto">
                                    {loading && !tickets.length && <LoadingBlock />}
                                    {!loading && error && (
                                        <div className="flex items-center justify-center gap-2 py-16 text-center text-[13px] text-rose-500">
                                            <AlertCircle size={15} /> {error}
                                        </div>
                                    )}
                                    {!loading && !error && !tickets.length && (
                                        <EmptyBlock
                                            hint={mine
                                                ? 'Создайте обращение — оно уйдёт в рабочую группу, а ответ вернётся сюда.'
                                                : 'В этом фильтре пусто.'}>
                                            Обращений нет
                                        </EmptyBlock>
                                    )}
                                    {tickets.map((ticket) => (
                                        <TicketRow key={ticket.id} ticket={ticket}
                                                   active={ticket.id === selectedId}
                                                   onSelect={setSelectedId} />
                                    ))}
                                    {hasMore && (
                                        <button type="button"
                                                onClick={() => loadTickets(offset + PAGE_SIZE)}
                                                className="w-full py-3 text-[12.5px] font-semibold text-slate-500 transition hover:bg-slate-50">
                                            Показать ещё
                                        </button>
                                    )}
                                </div>
                                {!!tickets.length && (
                                    <div className="shrink-0 border-t border-slate-100 px-3.5 py-2 text-[11px] tabular-nums text-slate-400">
                                        Показано {tickets.length}{hasMore ? ' — есть ещё' : ''}
                                    </div>
                                )}
                            </div>

                            {/* Карточка */}
                            <div className={`min-w-0 flex-1 ${selectedId ? 'flex' : 'hidden lg:flex'}`}>
                                {selectedId ? (
                                    <div className="w-full">
                                        <TicketCard
                                            key={selectedId}
                                            ticketId={selectedId}
                                            apiBaseUrl={apiBaseUrl}
                                            headers={headers}
                                            showToast={showToast}
                                            onChanged={refreshAfterChange}
                                            onBack={() => setSelectedId(null)}
                                            pulse={realtimePulse}
                                        />
                                    </div>
                                ) : (
                                    <div className="flex w-full items-center justify-center">
                                        <EmptyBlock icon={ChevronRight}
                                                    hint="Слева — обращения в работе. Выберите любое, чтобы увидеть переписку с группой.">
                                            Выберите обращение
                                        </EmptyBlock>
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                </>
            )}

            <TicketWizard
                open={composerOpen}
                onClose={() => setComposerOpen(false)}
                catalog={scenarioCatalog}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                showToast={showToast}
                onCreated={(id) => { setSelectedId(id); loadTickets(0, true); }}
            />
        </div>
    );
}
