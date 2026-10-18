import React, { memo } from 'react';
import { Loader2 } from 'lucide-react';
import ChatMessageText from './ChatMessageText';
import MessageDeliveryStatus from './MessageDeliveryStatus';
import { highlightParts } from './chatSearch';

/* Блок «Сообщения» под найденными чатами — как в WhatsApp и Telegram: каждое
 * сообщение с запросом отдельной строкой (чат, время, кусок текста с
 * найденным), свежие сверху. Строка открывает чат сразу на этом сообщении. */

const Snippet = memo(function Snippet({ text, query }) {
    return highlightParts(text, query).map((part, index) => (part.match
        ? <mark key={index} className="bg-transparent font-semibold text-slate-900">
            <ChatMessageText text={part.text} links={false} />
        </mark>
        : <ChatMessageText key={index} text={part.text} links={false} />));
});

export default function ChatMessageHits({ search, currentId, onOpen, onMore, onRetry, formatTime }) {
    const { query, items, next, loading, error } = search;
    if (!query || (!items.length && !loading && !error)) return null;
    return (
        <section aria-label="Найденные сообщения" data-testid="wazzup-message-hits">
            <div className="px-4 pb-1.5 pt-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                Сообщения
            </div>
            {items.map((hit) => {
                const chat = hit.chat || {};
                const current = hit.messageId === currentId;
                return (
                    <div key={hit.messageId} className="relative">
                        <button type="button" onClick={() => onOpen(hit)}
                            aria-current={current ? 'true' : undefined}
                            className={`wz-contact-row block w-full py-2.5 pl-4 pr-3 text-left transition ${
                                current ? 'bg-slate-100' : 'hover:bg-slate-50'}`}>
                            <div className="flex items-baseline justify-between gap-2">
                                <span className="truncate text-[14px] font-medium leading-5 text-slate-900">
                                    {chat.contactName || chat.contactPhone || hit.chatId}
                                </span>
                                <span className="shrink-0 text-[11px] text-slate-400">{formatTime(hit.dt)}</span>
                            </div>
                            <div className="mt-0.5 flex items-start gap-1 text-[13px] leading-5 text-slate-500">
                                {hit.isEcho && <span className="mt-0.5 shrink-0">
                                    <MessageDeliveryStatus status={hit.status} size={15} />
                                </span>}
                                <span className="line-clamp-2 min-w-0 break-words">
                                    <Snippet text={hit.text} query={query} />
                                </span>
                            </div>
                        </button>
                        <div aria-hidden="true" className="pointer-events-none absolute bottom-0 left-4 right-0 border-b border-slate-200" />
                    </div>
                );
            })}
            {loading && (
                <div role="status" className="flex items-center justify-center gap-2 py-4 text-[12.5px] text-slate-400">
                    <Loader2 size={14} className="animate-spin" /> Ищем в сообщениях…
                </div>
            )}
            {error && !loading && (
                <div role="alert" className="px-4 py-3 text-center text-[12.5px] text-slate-500">
                    {error}{' '}
                    <button type="button" onClick={onRetry} className="font-semibold text-blue-600 hover:underline">
                        Повторить
                    </button>
                </div>
            )}
            {next && !loading && !error && (
                <button type="button" onClick={onMore}
                    className="block w-full py-2.5 text-center text-[12px] font-semibold text-blue-600 hover:bg-slate-50">
                    Показать ещё сообщения
                </button>
            )}
        </section>
    );
}
