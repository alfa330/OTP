import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertCircle, BookOpen, FileText, Loader2, Plus, Quote,
    Sparkles, ThumbsDown, ThumbsUp, Trash2,
} from 'lucide-react';
import {
    iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, IosBadge, IosModal,
} from '../ui/ios';
import {
    ChatBubble, ChatComposer, ChatEmpty, useThreadAutoScroll,
} from '../ui/chat';
import Markdown from '../ui/markdown';
import useStableCallback from './useStableCallback';

/* Вкладка «Помощник» — чат по доступным пользователю статьям вики.
 *
 * Что здесь важно понимать про поведение, а не про код.
 *
 * ЦИТАТУ ИЗВЛЕКАЕТ СЕРВЕР, поэтому она дословна всегда — это не «доверяем
 * модели». Раньше цитату писала модель, а сервер сверял её с текстом; проверка на
 * проде показала, что механизм срабатывает через раз и, что хуже, выбрасывает
 * ВЕРНЫЕ ответы: на вопросе «Офис Астана» нужный кусок был найден, а модель
 * процитировала строку-метку, сверка не сошлась, и пользователь получил отказ.
 * От выдумки теперь защищает другая, устойчивая проверка на сервере: числа из
 * ответа обязаны встречаться в переданных фрагментах.
 *
 * ТРИ ИСХОДА, А НЕ ОДИН. kind различает answer, no_answer и clarify
 * (уточняющий вопрос). Уточнение решает сервер по коду, до вызова модели, и
 * оператору важно видеть, что это вопрос к нему, а не неудачный ответ. Под
 * отказом источников НЕТ намеренно: список статей под фразой «этого нет» читается
 * как противоречие и подрывает доверие к самой фразе.
 *
 * Бейдж «сопоставлено» означает, что фрагмент подобрал сервер по пересечению с
 * ответом, а не назвала модель. Клик по чипу открывает статью с подсветкой —
 * плумбинг тот же, что у поиска раздела.
 *
 * ОТВЕТ РЕНДЕРИТСЯ РАЗМЕТКОЙ, включая таблицы (src/components/ui/markdown.jsx).
 * Таблица — главный формат справочных данных вики: город, цена, срок, парк; в
 * корпусе их 63, и помощник отвечает такими же. Плоским текстом такая таблица
 * разрушается ровно там, где она нужнее всего.
 */

const fmtTime = (iso) => {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
};

const fmtChatDate = (iso) => {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    const today = new Date();
    const sameDay = date.toDateString() === today.toDateString();
    return sameDay
        ? date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
        : date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
};

const errText = (error, fallback) => (
    error?.response?.data?.detail || error?.response?.data?.error || fallback);

/** Статьи ответа, требующие подтверждения ознакомления, без повторов. */
const ackTitles = (sources) => {
    const seen = [];
    (sources || []).forEach((source) => {
        if (source.requires_ack && source.available !== false) {
            const title = source.title || '';
            if (title && !seen.includes(title)) seen.push(title);
        }
    });
    return seen;
};

/** Чип источника: название статьи, раздел и цитата под ним. */
const SourceChip = ({ source, onOpen }) => {
    const unavailable = source.available === false;
    // attributed — фрагмент сопоставил сервер, модель его не назвала.
    // Раньше здесь стояло quote_ok === false с подписью «цитата из
    // заголовка», и это врало: провал сверки подписывался как безобидная
    // особенность цитаты. Сверки цитат больше нет — их извлекает сервер.
    const attributed = source.attributed === true;
    return (
        <button
            type="button"
            disabled={unavailable || !source.slug}
            onClick={() => onOpen(source)}
            className={`group w-full rounded-xl px-2.5 py-2 text-left transition ${
                unavailable
                    ? 'cursor-default bg-slate-100 text-slate-400'
                    : 'bg-slate-50 hover:bg-slate-100'
            }`}
        >
            <div className="flex items-center gap-1.5">
                <FileText size={13} className="shrink-0 text-slate-400" />
                <span className="truncate text-[12.5px] font-medium text-slate-700">
                    {source.title || 'Без названия'}
                </span>
                {attributed && !unavailable && (
                    <IosBadge tone="slate" className="shrink-0">сопоставлено</IosBadge>
                )}
            </div>
            {source.heading_path && (
                <div className="truncate pl-[19px] text-[11px] text-slate-400">
                    {source.heading_path}
                </div>
            )}
            {source.quote && (
                <div className="mt-1 flex gap-1.5 pl-[19px] text-[11.5px] italic text-slate-500">
                    <Quote size={11} className="mt-[3px] shrink-0" />
                    <span className="line-clamp-3">{source.quote}</span>
                </div>
            )}
        </button>
    );
};

export default function WikiAssistant({ base, headers, showToast, onOpenArticle,
                                        spaceId = null }) {
    const toast = useStableCallback(showToast);
    const openArticle = useStableCallback(onOpenArticle);

    const [status, setStatus] = useState(null);
    const [chats, setChats] = useState([]);
    const [chatsLoading, setChatsLoading] = useState(true);
    const [activeId, setActiveId] = useState(null);
    const [messages, setMessages] = useState(null);
    const [draft, setDraft] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [confirmDelete, setConfirmDelete] = useState(null);

    // Гонки: ответ на старый чат не должен затирать открытый (приём из Wazzup).
    const threadRequest = useRef(0);
    const { boxRef, onScroll } = useThreadAutoScroll(messages);

    const loadStatus = useCallback(() => {
        axios.get(`${base}/ai/status`, { headers, params: { space_id: spaceId } })
            .then((r) => setStatus(r.data))
            .catch(() => setStatus(null));
    }, [base, headers]);

    const loadChats = useCallback(() => {
        setChatsLoading(true);
        return axios.get(`${base}/ai/chats`, { headers })
            .then((r) => setChats(r.data?.chats || []))
            .catch((e) => setError(errText(e, 'Не удалось загрузить список чатов')))
            .finally(() => setChatsLoading(false));
    }, [base, headers]);

    useEffect(() => { loadStatus(); loadChats(); }, [loadStatus, loadChats]);

    const openChat = useCallback((chatId) => {
        setActiveId(chatId);
        setMessages(null);
        const request = ++threadRequest.current;
        axios.get(`${base}/ai/chats/${chatId}`, { headers, params: { space_id: spaceId } })
            .then((r) => {
                if (request !== threadRequest.current) return;
                setMessages(r.data?.messages || []);
            })
            .catch((e) => {
                if (request !== threadRequest.current) return;
                setMessages([]);
                toast(errText(e, 'Не удалось открыть чат'), 'error');
            });
    }, [base, headers, toast]);

    const startNewChat = useCallback(() => {
        setActiveId(null);
        setMessages([]);
        setDraft('');
    }, []);

    const ask = useCallback(async (question) => {
        setBusy(true);
        setError('');
        const localUser = {
            id: `local-${Date.now()}`, role: 'user', kind: 'question',
            text: question, sources: [], created_at: new Date().toISOString(),
        };
        setMessages((prev) => [...(prev || []), localUser]);
        setDraft('');

        try {
            let chatId = activeId;
            if (!chatId) {
                const created = await axios.post(`${base}/ai/chats`, { space_id: spaceId }, { headers });
                chatId = created.data?.chat?.id;
                setActiveId(chatId);
            }
            const response = await axios.post(
                `${base}/ai/chats/${chatId}/ask`, { question, space_id: spaceId }, { headers });
            const data = response.data || {};
            setMessages((prev) => [...(prev || []), {
                id: data.message_id, role: 'assistant', kind: data.kind,
                text: data.text, sources: data.sources || [],
                provider: data.provider, model: data.model,
                elapsed_ms: data.elapsed ? Math.round(data.elapsed * 1000) : null,
                degraded_search: data.degraded_search,
                created_at: new Date().toISOString(),
            }]);
            loadChats();
        } catch (e) {
            setMessages((prev) => (prev || []).filter((m) => m.id !== localUser.id));
            setDraft(question);      // не теряем набранное: перепечатывать обидно
            setError(errText(e, 'Помощник не ответил'));
        } finally {
            setBusy(false);
        }
    }, [activeId, base, headers, loadChats]);

    const sendFeedback = useCallback((messageId, value) => {
        setMessages((prev) => (prev || []).map(
            (m) => (m.id === messageId ? { ...m, feedback: value } : m)));
        axios.post(`${base}/ai/messages/${messageId}/feedback`,
                   { feedback: value }, { headers })
            .catch(() => toast('Не удалось сохранить оценку', 'error'));
    }, [base, headers, toast]);

    const removeChat = useCallback((chatId) => {
        axios.delete(`${base}/ai/chats/${chatId}`, { headers })
            .then(() => {
                setChats((prev) => prev.filter((c) => c.id !== chatId));
                if (activeId === chatId) startNewChat();
                toast('Чат удалён', 'success');
            })
            .catch((e) => toast(errText(e, 'Не удалось удалить чат'), 'error'))
            .finally(() => setConfirmDelete(null));
    }, [activeId, base, headers, startNewChat, toast]);

    const perimeter = status?.perimeter;
    const indexReady = (status?.index?.chunks || 0) > 0;
    const noAccess = perimeter && perimeter.articles_for_ai === 0;

    const banner = useMemo(() => {
        if (noAccess) {
            return {
                tone: 'amber',
                text: 'Помощнику не выдан доступ ни к одной статье. Обратитесь к '
                      + 'администратору вики — доступ выдаётся отдельно от чтения.',
            };
        }
        if (status && !indexReady) {
            return {
                tone: 'amber',
                text: 'Индекс помощника пуст: администратору нужно собрать его в '
                      + 'разделе обслуживания. До этого ответы невозможны.',
            };
        }
        return null;
    }, [noAccess, status, indexReady]);

    const canAsk = !noAccess && indexReady;

    return (
        <div className="space-y-3">
            {banner && (
                <div className={`${iosCard} flex items-start gap-2 px-3.5 py-2.5 text-[12.5px] text-amber-800`}>
                    <AlertCircle size={15} className="mt-[1px] shrink-0 text-amber-500" />
                    <span>{banner.text}</span>
                </div>
            )}

            <div
                className={`${iosCard} flex overflow-hidden`}
                style={{ height: 'clamp(440px, 68vh, 720px)' }}
            >
                {/* Список чатов */}
                <div className="hidden w-64 shrink-0 flex-col border-r border-slate-100 bg-slate-50/70 sm:flex">
                    <div className="p-2.5">
                        <button type="button" onClick={startNewChat}
                                className={`${iosBtnPrimary} w-full`}>
                            <Plus size={15} /> Новый вопрос
                        </button>
                    </div>
                    <div className="flex-1 overflow-y-auto px-2 pb-2">
                        {chatsLoading && (
                            <div className="flex items-center gap-2 px-2 py-3 text-[12.5px] text-slate-400">
                                <Loader2 size={14} className="animate-spin" /> Загрузка…
                            </div>
                        )}
                        {!chatsLoading && !chats.length && (
                            <div className="px-2 py-3 text-[12px] text-slate-400">
                                Пока нет ни одного вопроса
                            </div>
                        )}
                        {chats.map((chat) => (
                            <div
                                key={chat.id}
                                className={`group mb-1 flex items-center gap-1 rounded-xl px-2 py-2 transition ${
                                    activeId === chat.id ? 'bg-white shadow-sm' : 'hover:bg-white/70'
                                }`}
                            >
                                <button
                                    type="button"
                                    onClick={() => openChat(chat.id)}
                                    className="min-w-0 flex-1 text-left"
                                >
                                    <div className="truncate text-[12.5px] font-medium text-slate-700">
                                        {chat.title || 'Без названия'}
                                    </div>
                                    <div className="text-[11px] text-slate-400">
                                        {fmtChatDate(chat.last_message_at || chat.created_at)}
                                        {chat.message_count ? ` · ${chat.message_count}` : ''}
                                    </div>
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setConfirmDelete(chat)}
                                    className="shrink-0 rounded-lg p-1.5 text-slate-300 opacity-0 transition hover:bg-slate-100 hover:text-rose-500 focus-visible:opacity-100 group-hover:opacity-100"
                                    aria-label="Удалить чат"
                                >
                                    <Trash2 size={13} />
                                </button>
                            </div>
                        ))}
                    </div>
                </div>

                {/* Лента */}
                <div className="flex min-w-0 flex-1 flex-col bg-[#f2f2f7]">
                    <div
                        ref={boxRef}
                        onScroll={onScroll}
                        className="flex-1 space-y-2 overflow-y-auto py-3"
                    >
                        {messages === null && activeId && (
                            <div className="flex flex-1 items-center justify-center gap-2 py-10 text-[13px] text-slate-400">
                                <Loader2 size={15} className="animate-spin" /> Загрузка переписки…
                            </div>
                        )}
                        {(messages === null && !activeId) || (messages && !messages.length) ? (
                            <ChatEmpty
                                icon={Sparkles}
                                title="Спросите что-нибудь по базе знаний"
                                hint="Помощник отвечает только по статьям, которые вам разрешено читать, и всегда показывает источник."
                            />
                        ) : null}

                        {(messages || []).map((message) => {
                            if (message.role === 'user') {
                                return (
                                    <ChatBubble key={message.id} out
                                                meta={fmtTime(message.created_at)}>
                                        {message.text}
                                    </ChatBubble>
                                );
                            }
                            const tone = message.kind === 'clarify'
                                ? 'warn'
                                : message.kind === 'no_answer' ? 'muted' : null;
                            return (
                                <div key={message.id} className="space-y-1.5">
                                    <ChatBubble
                                        tone={tone}
                                        meta={(
                                            <>
                                                <span>{fmtTime(message.created_at)}</span>
                                                {message.model && (
                                                    <span className="truncate opacity-70">
                                                        {message.model}
                                                    </span>
                                                )}
                                                {message.elapsed_ms != null && (
                                                    <span className="opacity-70">
                                                        {(message.elapsed_ms / 1000).toFixed(1)} с
                                                    </span>
                                                )}
                                            </>
                                        )}
                                        plain={false}
                                    >
                                        {/* Ответ размечен: списки, выделения и
                                            ТАБЛИЦЫ — главный формат справочных
                                            данных вики (город, цена, срок, парк). */}
                                        <Markdown text={message.text} />
                                    </ChatBubble>

                                    {/* Приписка про обязательное ознакомление выводится
                                        ИЗ ИСТОЧНИКОВ, а не берётся из ответа сервера:
                                        при перезагрузке истории поля notes нет, и
                                        приписка исчезала бы только из старых ответов. */}
                                    {ackTitles(message.sources).map((title) => (
                                        <div key={title} className="px-4">
                                            <div className="max-w-[78%] rounded-xl bg-amber-50 px-3 py-2 text-[12px] text-amber-900 ring-1 ring-amber-200/70">
                                                Этот пункт входит в обязательное ознакомление
                                                по статье «{title}» — подтвердите ознакомление
                                                в самой статье.
                                            </div>
                                        </div>
                                    ))}

                                    {!!(message.sources || []).length && (
                                        <div className="px-4">
                                            <div className="max-w-[78%] space-y-1 rounded-xl bg-white p-1.5 ring-1 ring-slate-200/60">
                                                <div className="px-1.5 pt-0.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                                    Источники
                                                </div>
                                                {(message.sources || []).map((source, index) => (
                                                    <SourceChip
                                                        key={index}
                                                        source={source}
                                                        onOpen={(item) => openArticle(item.slug, item.quote)}
                                                    />
                                                ))}
                                            </div>
                                        </div>
                                    )}

                                    {message.id && !String(message.id).startsWith('local-') && (
                                        <div className="flex gap-1 px-4">
                                            <button
                                                type="button"
                                                onClick={() => sendFeedback(message.id, 1)}
                                                className={`${iosBtnGhost} ${message.feedback === 1 ? 'text-emerald-600' : ''}`}
                                            >
                                                <ThumbsUp size={13} /> Помогло
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => sendFeedback(message.id, -1)}
                                                className={`${iosBtnGhost} ${message.feedback === -1 ? 'text-rose-500' : ''}`}
                                            >
                                                <ThumbsDown size={13} /> Нет
                                            </button>
                                            {message.degraded_search && (
                                                <IosBadge tone="amber" className="self-center">
                                                    поиск без векторов
                                                </IosBadge>
                                            )}
                                        </div>
                                    )}
                                </div>
                            );
                        })}

                        {busy && (
                            <div className="flex justify-start px-4">
                                <div className="flex items-center gap-2 rounded-2xl rounded-bl-md bg-white px-3 py-2 text-[13px] text-slate-400 ring-1 ring-slate-200/60">
                                    <Loader2 size={14} className="animate-spin" />
                                    Читаю доступные вам статьи…
                                </div>
                            </div>
                        )}
                    </div>

                    {error && (
                        <div className="flex items-center gap-1.5 border-t border-rose-100 bg-rose-50 px-4 py-2 text-[12px] text-rose-600">
                            <AlertCircle size={14} /> {error}
                        </div>
                    )}

                    <ChatComposer
                        value={draft}
                        onChange={setDraft}
                        onSubmit={ask}
                        busy={busy}
                        disabled={!canAsk}
                        placeholder={canAsk ? 'Спросите что-нибудь по базе знаний…'
                                            : 'Помощник пока недоступен'}
                        hint={perimeter
                            ? `Доступно статей: ${perimeter.articles_for_ai} из `
                              + `${perimeter.articles_readable} читаемых`
                            : null}
                    />
                </div>
            </div>

            <IosModal
                open={!!confirmDelete}
                onClose={() => setConfirmDelete(null)}
                title="Удалить чат?"
                subtitle={confirmDelete?.title || ''}
                footer={(
                    <>
                        <button type="button" className={iosBtnSecondary}
                                onClick={() => setConfirmDelete(null)}>
                            Отмена
                        </button>
                        <button type="button" className={iosBtnPrimary}
                                onClick={() => removeChat(confirmDelete.id)}>
                            <Trash2 size={15} /> Удалить
                        </button>
                    </>
                )}
            >
                <p className="text-[13px] text-slate-600">
                    Переписка скроется из списка. История вопросов сохраняется в журнале
                    раздела — она нужна авторам вики, чтобы видеть, о чём спрашивают, а
                    ответа в статьях нет.
                </p>
            </IosModal>

            <div className="flex items-center gap-1.5 px-1 text-[11px] text-slate-400">
                <BookOpen size={12} />
                Помощник отвечает только по статьям вашего периметра и всегда показывает
                цитату-источник. Если ответа в них нет — он так и скажет.
                {status?.embeddings?.pending_texts ? (
                    <span className="text-amber-600">
                        · не хватает векторов: {status.embeddings.pending_texts}
                    </span>
                ) : null}
            </div>
        </div>
    );
}
