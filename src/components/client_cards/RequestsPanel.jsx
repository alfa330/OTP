/* Запросы в другие отделы: переписка по запросу и список запросов оператора (задача #335, п. 8).
 *
 * Thread — запрос с перепиской, один на два места: окно запроса на доске «Запросы» раздела
 * портала (variant="window") и вкладка «Запросы» страницы карточки в iCORE Phone
 * (variant="phone": список, по нажатию — переписка с кнопкой «назад», окно телефона узкое).
 * Кто что видит и кто переносит запрос по доске, решает сервер; экран только рисует.
 *
 * Переписка. Запрос ушёл в Telegram-группу — пишем в группу, ответы приходят оттуда. Запрос
 * только на доске — переписка остаётся в портале: оператор спрашивает, ведущие доску
 * отвечают ему и просят недостающее. Переносы по доске стоят в переписке строками: оператор
 * видит, кто и когда передал запрос дальше.
 *
 * Свежесть. У страницы телефона нет колокола портала, поэтому список перечитывается сам
 * раз в полминуты, открытая переписка — раз в 15 секунд, и обе — при возврате во вкладку.
 * В портале тот же опрос плюс тычок колокола (reloadKey).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    ArrowLeft, ArrowRight, CornerUpLeft, ExternalLink, FileText, Film, Image as ImageIcon, Inbox, Loader2,
    Paperclip, RotateCcw, Search, Send, X,
} from 'lucide-react';

import CustomSelect from '../ui/CustomSelect';
import { IosSegmented, iosBtnSecondary, iosInput } from '../ui/ios';
import { resolveRequestFile } from './cardsApi';
import {
    REQUEST_FILTERS, errorText, fmtSize, fmtWhen, isMine, quoteOf, requestTitle, senderColor, uploadsProblem,
} from './cardRules';
import { EmptyState, GrowingTextarea, PickedFiles, RequestBadge, Spinner } from './ui';

const LIST_POLL_MS = 30000;
const THREAD_POLL_MS = 15000;

const FILE_ICONS = { photo: ImageIcon, video: Film, document: FileText };

/* Вложение сообщения. Картинка и видео открываются тут же, документ — в новой вкладке
   (в телефоне её открывает системный браузер). Адрес из памяти освобождаем при закрытии. */
function MessageFile({ api, requestId, message, onError }) {
    const [busy, setBusy] = useState(false);
    const [viewer, setViewer] = useState(null);
    const revokeRef = useRef(null);
    const file = message.file;
    const Icon = FILE_ICONS[file.kind] || FileText;

    const close = useCallback(() => {
        if (revokeRef.current) revokeRef.current();
        revokeRef.current = null;
        setViewer(null);
    }, []);
    useEffect(() => close, [close]);

    const open = async () => {
        if (busy) return;
        setBusy(true);
        try {
            const found = await resolveRequestFile(api, requestId, message.id);
            if (file.kind === 'photo' || file.kind === 'video') {
                revokeRef.current = found.revoke;
                setViewer(found.url);
            } else {
                const link = document.createElement('a');
                link.href = found.url;
                link.target = '_blank';
                link.rel = 'noopener';
                if (found.url.startsWith('blob:')) link.download = file.name || 'file';
                document.body.appendChild(link);
                link.click();
                link.remove();
                // Адрес из памяти нужен браузеру ещё мгновение после нажатия.
                setTimeout(found.revoke, 60000);
            }
        } catch (error) {
            onError(errorText(error, 'Файл не открылся'));
        } finally {
            setBusy(false);
        }
    };

    return (
        <>
            <button type="button" onClick={open}
                    className="mt-1 flex max-w-full items-center gap-2 rounded-xl bg-white/70 px-2.5 py-1.5 text-left text-[12.5px] text-slate-700 ring-1 ring-slate-200/80 transition hover:bg-white active:scale-[0.99]">
                {busy ? <Loader2 size={14} className="shrink-0 animate-spin text-slate-400" />
                    : <Icon size={14} className="shrink-0 text-slate-400" aria-hidden="true" />}
                <span className="min-w-0 truncate">{file.name || 'Файл'}</span>
                {file.size ? <span className="shrink-0 tabular-nums text-slate-400">{fmtSize(file.size)}</span> : null}
            </button>
            {message.pending && (
                <div className="mt-0.5 text-[11px] text-amber-600">Не дошёл до группы — отправьте запрос ещё раз</div>
            )}
            {viewer && (
                <div className="fixed inset-0 z-[150] flex items-center justify-center bg-slate-900/80 p-4"
                     onClick={close} role="dialog" aria-label={file.name || 'Вложение'}>
                    <button type="button" onClick={close} aria-label="Закрыть"
                            className="absolute right-4 top-4 grid h-9 w-9 place-items-center rounded-full bg-white/15 text-white hover:bg-white/25">
                        <X size={18} />
                    </button>
                    {file.kind === 'video'
                        ? <video src={viewer} controls autoPlay className="max-h-full max-w-full rounded-xl"
                                 onClick={(event) => event.stopPropagation()} />
                        : <img src={viewer} alt={file.name || ''} className="max-h-full max-w-full rounded-xl object-contain"
                               onClick={(event) => event.stopPropagation()} />}
                </div>
            )}
        </>
    );
}

/* Перенос по доске — строкой посреди переписки: куда, кто, когда. */
const StageLine = ({ message }) => (
    <div className="flex justify-center py-0.5">
        <span className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-[11.5px] text-slate-500">
            <ArrowRight size={12} className="shrink-0 text-slate-400" aria-hidden="true" />
            <span className="min-w-0 truncate">
                <span className="font-medium text-slate-700">{message.stage_label}</span>
                {message.author_name ? ` · ${message.author_name}` : ''}
            </span>
            <span className="shrink-0 tabular-nums text-slate-400">{fmtWhen(message.created_at)}</span>
        </span>
    </div>
);

export function Thread({
    api, requestId, variant = 'phone', onBack = null, onChanged, toast, limits, reloadKey = 0, userId = null,
    stages = [],
}) {
    const [detail, setDetail] = useState(null);
    const [failed, setFailed] = useState('');
    const [text, setText] = useState('');
    const [files, setFiles] = useState([]);
    const [replyTo, setReplyTo] = useState(null);
    const [busy, setBusy] = useState('');
    const endRef = useRef(null);
    const fileRef = useRef(null);
    const countRef = useRef(0);
    const phone = variant === 'phone';

    const load = useCallback(async (quiet = false) => {
        try {
            const { data } = await api.request(requestId);
            setDetail(data);
            setFailed('');
            // Автор открыл запрос — «непрочитано» погасло и в списке.
            if (!quiet) onChanged(data.request);
        } catch (error) {
            if (!quiet) setFailed(errorText(error, 'Запрос не открылся'));
        }
    }, [api, requestId, onChanged]);

    useEffect(() => {
        setDetail(null);
        setText('');
        setFiles([]);
        setReplyTo(null);
        countRef.current = 0;
        load();
    }, [load]);

    useEffect(() => {
        const timer = setInterval(() => {
            if (document.visibilityState !== 'hidden') load(true);
        }, THREAD_POLL_MS);
        return () => clearInterval(timer);
    }, [load]);
    useEffect(() => { if (reloadKey) load(true); }, [reloadKey, load]);

    // Вниз — когда в нити прибавилось сообщений (и при открытии), а не на каждый опрос.
    const messages = detail?.messages || [];
    useEffect(() => {
        if (messages.length !== countRef.current) {
            countRef.current = messages.length;
            endRef.current?.scrollIntoView({ block: 'end' });
        }
    }, [messages.length]);

    const act = async (name, work, done) => {
        if (busy) return false;
        setBusy(name);
        try {
            const { data } = await work();
            setDetail(data);
            if (data.delivery_error) toast(`Не всё дошло до группы: ${data.delivery_error}`, 'error');
            else if (done) toast(typeof done === 'function' ? done(data) : done, 'success');
            onChanged(data.request);
            return true;
        } catch (error) {
            toast(errorText(error), 'error');
            return false;
        } finally {
            setBusy('');
        }
    };

    const send = async () => {
        const body = text.trim();
        if (!body && !files.length) return;
        const ok = await act('send', () => api.replyRequest(requestId, { body, reply_to: replyTo?.id }, files));
        if (ok) {
            setText('');
            setFiles([]);
            setReplyTo(null);
        }
    };

    const pickFiles = (event) => {
        const next = [...files, ...Array.from(event.target.files || [])];
        event.target.value = '';
        const problem = uploadsProblem(next, limits);
        if (problem) { toast(problem, 'error'); return; }
        setFiles(next);
    };

    const stageOptions = useMemo(() => (stages || []).map((item) => ({ value: item.value, label: item.label })), [stages]);

    if (failed) {
        return (
            <div className="flex h-full flex-col">
                {phone && <BackRow onBack={onBack} />}
                <EmptyState title={failed}>
                    <button type="button" onClick={() => load()} className={`${iosBtnSecondary} mt-4`}>Повторить</button>
                </EmptyState>
            </div>
        );
    }
    if (!detail) return <Spinner text="Открываем запрос…" />;

    const request = detail.request;
    const closed = Boolean(request.closed);
    // Запрос с группой, который до неё не дошёл: писать некуда, пока не отправят заново.
    const undelivered = request.delivery_status === 'failed' || request.delivery_status === 'pending';
    const boardOnly = request.delivery_status === 'none';
    const own = Number(request.created_by) === Number(userId);
    const placeholder = !boardOnly ? 'Написать в группу отдела…'
        : own ? 'Написать по запросу…' : 'Написать оператору…';

    return (
        <div className="flex h-full min-h-0 flex-col">
            <div className="shrink-0 border-b border-slate-200/70 px-4 py-3">
                <div className="flex items-start gap-2">
                    {/* В телефоне список скрыт под перепиской — назад к нему стрелкой. */}
                    {phone && (
                        <button type="button" onClick={onBack} aria-label="К списку запросов"
                                className="-ml-1.5 mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg text-slate-500 hover:bg-slate-100">
                            <ArrowLeft size={17} />
                        </button>
                    )}
                    <div className="min-w-0 flex-1">
                        {phone && (
                            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                                <span className="text-[15px] font-semibold text-slate-900">{requestTitle(request)}</span>
                                {!detail.can_move && <RequestBadge request={request} />}
                            </div>
                        )}
                        {request.topic_title && (
                            <div className={phone ? 'mt-0.5 text-[12.5px] text-slate-600' : 'text-[13.5px] font-medium text-slate-800'}>
                                {request.topic_title}
                            </div>
                        )}
                        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[12px] text-slate-500">
                            {request.account && (
                                <a href={request.account.url} target="_blank" rel="noopener noreferrer"
                                   className="inline-flex items-center gap-1 text-blue-600 hover:underline">
                                    {request.account.kind_label}{request.account.full_name ? ` · ${request.account.full_name}` : ''} · ID {request.account.tez_id}
                                    <ExternalLink size={11} aria-hidden="true" />
                                </a>
                            )}
                            {request.phone_text && <span className="tabular-nums">{request.phone_text}</span>}
                            <span>{request.created_by_name} · {fmtWhen(request.created_at)}</span>
                        </div>
                    </div>
                    {/* Стадию переносят те, кто ведёт доску; остальным она видна бейджем. */}
                    {detail.can_move ? (
                        <CustomSelect variant="ios" value={request.stage} options={stageOptions} className="w-52 shrink-0"
                                      ariaLabel="Стадия запроса" disabled={Boolean(busy)}
                                      onChange={(value) => {
                                          if (!value || value === request.stage) return;
                                          act('stage', () => api.setRequestStage(requestId, value),
                                              (data) => `Запрос №${requestId} — «${data.request.stage_label}»`);
                                      }} />
                    ) : (!phone && <RequestBadge request={request} className="shrink-0" />)}
                </div>
                {undelivered && (
                    <div className="mt-2 flex flex-wrap items-center gap-2 rounded-xl bg-rose-50 px-3 py-2 text-[12.5px] text-rose-700 ring-1 ring-rose-100">
                        <span className="min-w-0 flex-1">
                            Запрос не дошёл до группы{request.delivery_error ? `: ${request.delivery_error}` : ''}
                        </span>
                        {detail.can_act && (
                            <button type="button" disabled={Boolean(busy)}
                                    onClick={() => act('retry', () => api.retryRequest(requestId), 'Запрос отправлен')}
                                    className="inline-flex items-center gap-1.5 rounded-lg bg-white px-2.5 py-1 font-semibold text-rose-700 ring-1 ring-rose-200 hover:bg-rose-50 disabled:opacity-50">
                                <RotateCcw size={12} /> Отправить ещё раз
                            </button>
                        )}
                    </div>
                )}
            </div>

            <div className="thin-scroll min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-3">
                {messages.map((message) => {
                    if (message.direction === 'stage') return <StageLine key={message.id} message={message} />;
                    const mine = isMine(message, userId);
                    const quote = quoteOf(message, messages);
                    return (
                        <div key={message.id} className={`group flex ${mine ? 'justify-end' : 'justify-start'}`}>
                            {/* Потолок ширины — для чтения: на широком экране 85 % переписки дали бы строку во весь монитор. */}
                            <div className={`max-w-[min(85%,46rem)] rounded-2xl px-3 py-2 text-[13.5px] leading-snug ${
                                mine ? 'bg-blue-50 text-slate-900' : 'bg-slate-100 text-slate-900'}`}>
                                {!mine && (
                                    <div className={`text-[11.5px] font-semibold ${senderColor(message)}`}>
                                        {message.author_name || 'Сотрудник отдела'}
                                    </div>
                                )}
                                {quote && (
                                    <div className="mb-1 border-l-2 border-slate-300 pl-2 text-[11.5px] text-slate-500">
                                        <span className="font-medium">{quote.author}</span>
                                        <span className="block truncate">{quote.text}</span>
                                    </div>
                                )}
                                {message.body && <div className="whitespace-pre-wrap break-words">{message.body}</div>}
                                {message.file && (
                                    <MessageFile api={api} requestId={requestId} message={message}
                                                 onError={(problem) => toast(problem, 'error')} />
                                )}
                                <div className="mt-0.5 flex items-center justify-end gap-2 text-[10.5px] text-slate-400">
                                    {!mine && detail.can_act && !closed && message.tg_message_id && (
                                        <button type="button" onClick={() => setReplyTo(message)}
                                                className="inline-flex items-center gap-0.5 text-slate-400 hover:text-blue-600">
                                            <CornerUpLeft size={11} /> Ответить
                                        </button>
                                    )}
                                    <span className="tabular-nums">{fmtWhen(message.created_at)}</span>
                                </div>
                            </div>
                        </div>
                    );
                })}
                <div ref={endRef} />
            </div>

            {detail.can_act && (
                <div className="shrink-0 space-y-2 border-t border-slate-200/70 px-4 py-3">
                    {closed ? (
                        <div className="text-[12.5px] text-slate-500">
                            {request.stage_label}{request.resolved_by_name ? ` · ${request.resolved_by_name}` : ''}
                            {request.resolved_at ? ` · ${fmtWhen(request.resolved_at)}` : ''}
                        </div>
                    ) : (
                        <>
                            {replyTo && (
                                <div className="flex items-center gap-2 rounded-lg bg-slate-100 px-2.5 py-1.5 text-[12px] text-slate-600">
                                    <CornerUpLeft size={12} className="shrink-0 text-slate-400" />
                                    <span className="min-w-0 flex-1 truncate">
                                        Ответ для {replyTo.author_name || 'сотрудника'}: {replyTo.body || 'файл'}
                                    </span>
                                    <button type="button" onClick={() => setReplyTo(null)} aria-label="Не отвечать на это сообщение"
                                            className="grid h-5 w-5 place-items-center rounded-md text-slate-400 hover:bg-slate-200">
                                        <X size={12} />
                                    </button>
                                </div>
                            )}
                            <PickedFiles files={files} onRemove={(index) => setFiles(files.filter((_, i) => i !== index))} />
                            {!undelivered && (
                                <div className="flex items-end gap-2">
                                    <input ref={fileRef} type="file" multiple className="hidden" onChange={pickFiles}
                                           accept="image/*,video/*,.pdf,.doc,.docx,.xls,.xlsx,.txt,.zip" />
                                    <button type="button" onClick={() => fileRef.current?.click()} aria-label="Приложить файл"
                                            className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500 hover:bg-slate-200">
                                        <Paperclip size={16} />
                                    </button>
                                    <GrowingTextarea value={text} onChange={setText} placeholder={placeholder}
                                                     className={`${iosInput} min-h-[40px] flex-1`}
                                                     onKeyDown={(event) => {
                                                         if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) send();
                                                     }} />
                                    <button type="button" onClick={send} aria-label="Отправить"
                                            disabled={Boolean(busy) || (!text.trim() && !files.length)}
                                            className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-blue-600 text-white transition hover:bg-blue-700 active:scale-[0.97] disabled:opacity-40">
                                        {busy === 'send' ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
                                    </button>
                                </div>
                            )}
                        </>
                    )}
                </div>
            )}
        </div>
    );
}

const BackRow = ({ onBack }) => (
    <div className="shrink-0 border-b border-slate-200/70 px-3 py-2">
        <button type="button" onClick={onBack}
                className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[13px] font-medium text-slate-600 hover:bg-slate-100">
            <ArrowLeft size={15} /> К списку
        </button>
    </div>
);

/* Список запросов оператора в окне телефона: свои запросы со стадией доски, по нажатию —
   переписка. Сам запрос по доске оператор не двигает — это делают те, кто её ведёт. */
export default function RequestsPanel({
    api, toast, limits = null, userId = null, stages = [],
    focusRequestId = null, onFocusHandled = null, reloadKey = 0, onUnread = null,
}) {
    const [filter, setFilter] = useState('active');
    const [query, setQuery] = useState('');
    const [search, setSearch] = useState('');
    const [list, setList] = useState(null);
    const [hasMore, setHasMore] = useState(false);
    const [failed, setFailed] = useState('');
    const [openId, setOpenId] = useState(null);
    const [loadingMore, setLoadingMore] = useState(false);
    const unreadRef = useRef(onUnread);
    unreadRef.current = onUnread;

    // Поиск — с паузой: запрос на каждую букву ни к чему.
    useEffect(() => {
        const timer = setTimeout(() => setSearch(query.trim()), 350);
        return () => clearTimeout(timer);
    }, [query]);

    const params = useMemo(() => ({ status: filter || undefined, q: search || undefined }), [filter, search]);

    const load = useCallback(async (quiet = false) => {
        try {
            const { data } = await api.requests(params);
            setList(data.items);
            setHasMore(Boolean(data.has_more));
            setFailed('');
            unreadRef.current?.(data.unread_requests || 0);
        } catch (error) {
            if (!quiet) setFailed(errorText(error, 'Запросы не загрузились'));
        }
    }, [api, params]);

    useEffect(() => { setList(null); load(); }, [load]);
    useEffect(() => {
        const tick = () => { if (document.visibilityState !== 'hidden') load(true); };
        const timer = setInterval(tick, LIST_POLL_MS);
        document.addEventListener('visibilitychange', tick);
        return () => {
            clearInterval(timer);
            document.removeEventListener('visibilitychange', tick);
        };
    }, [load]);
    useEffect(() => { if (reloadKey) load(true); }, [reloadKey, load]);

    // Ссылка из колокола или истории: открыть названный запрос.
    useEffect(() => {
        if (!focusRequestId) return;
        setOpenId(Number(focusRequestId));
        onFocusHandled?.();
    }, [focusRequestId, onFocusHandled]);

    const more = async () => {
        const last = list?.[list.length - 1];
        if (!last || loadingMore) return;
        setLoadingMore(true);
        try {
            const { data } = await api.requests({ ...params, before_at: last.last_message_at, before_id: last.id });
            setList([...list, ...data.items]);
            setHasMore(Boolean(data.has_more));
        } catch (error) {
            toast(errorText(error), 'error');
        } finally {
            setLoadingMore(false);
        }
    };

    const onChanged = useCallback(() => { load(true); }, [load]);

    if (openId) {
        return (
            <div className="h-full min-h-0">
                <Thread key={openId} api={api} requestId={openId} variant="phone" onBack={() => setOpenId(null)}
                        onChanged={onChanged} toast={toast} limits={limits} reloadKey={reloadKey}
                        userId={userId} stages={stages} />
            </div>
        );
    }

    return (
        <div className="flex h-full min-h-0 flex-col">
            <div className="shrink-0 space-y-2 px-3 pb-2 pt-3">
                <IosSegmented value={filter} options={REQUEST_FILTERS} onChange={setFilter} ariaLabel="Какие запросы показать" />
                <label className="relative block">
                    <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                    <input value={query} onChange={(event) => setQuery(event.target.value)}
                           placeholder="Номер запроса, телефон, ID, текст" className={`${iosInput} py-2 pl-8`} />
                </label>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
                {failed && !list && (
                    <EmptyState title={failed}>
                        <button type="button" onClick={() => load()} className={`${iosBtnSecondary} mt-4`}>Повторить</button>
                    </EmptyState>
                )}
                {!failed && !list && <Spinner />}
                {list && !list.length && (
                    <EmptyState icon={Inbox} title={search ? 'Ничего не нашлось' : 'Запросов нет'}
                                text={search ? '' : 'Запрос в другой отдел создаётся из карточки звонка.'} />
                )}
                {list?.map((item) => (
                    <button key={item.id} type="button" onClick={() => setOpenId(item.id)}
                            className="mb-1 block w-full rounded-xl px-3 py-2.5 text-left transition hover:bg-slate-100/80 active:scale-[0.995]">
                        <div className="flex items-center gap-2">
                            {item.unread > 0 && <span className="h-2 w-2 shrink-0 rounded-full bg-blue-600" aria-label="Есть новое" />}
                            <span className={`min-w-0 flex-1 truncate text-[13.5px] ${item.unread > 0 ? 'font-semibold text-slate-900' : 'font-medium text-slate-800'}`}>
                                {requestTitle(item)}
                            </span>
                            <span className="shrink-0 text-[11.5px] tabular-nums text-slate-400">{fmtWhen(item.last_message_at)}</span>
                        </div>
                        <div className="mt-0.5 truncate text-[12.5px] text-slate-600">{item.topic_title || item.body}</div>
                        <div className="mt-1 flex items-center gap-2">
                            <span className="min-w-0 flex-1 truncate text-[11.5px] text-slate-400">
                                {item.account ? `${item.account.kind_label} · ${item.account.full_name || `ID ${item.account.tez_id}`}` : item.phone_text}
                            </span>
                            <RequestBadge request={item} />
                        </div>
                    </button>
                ))}
                {hasMore && (
                    <button type="button" onClick={more} disabled={loadingMore} className={`${iosBtnSecondary} mx-auto mt-2 flex`}>
                        {loadingMore ? 'Загружаем…' : 'Показать ещё'}
                    </button>
                )}
            </div>
        </div>
    );
}
