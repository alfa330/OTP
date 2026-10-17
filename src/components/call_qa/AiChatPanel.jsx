import React, { useEffect, useLayoutEffect, useRef } from 'react';
import { Eraser, Loader2, Sparkles } from 'lucide-react';
import { iosCard, IosMenu } from '../ui/ios';
import { ChatBubble, ChatComposer, useThreadScroll } from '../ui/chat';
import AiMarkup from './AiMarkup';

/* Панель «Спросить ИИ» — одна на чат о дне (DigestView) и чат о периоде
 * (PeriodView). Лента с автопрокруткой, подсказки, поле ввода; сами данные и
 * запросы живут в хуках (useDigest, usePeriod), здесь — только вид.
 *
 * Подсказки кладут текст в поле, отправляет человек (правило владельца: ИИ
 * ничего не отправляет за пользователя).
 */

const fmtTime = (iso) => {
    if (!iso) return '';
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
};

export default function AiChatPanel({
    title = 'Спросить ИИ', subtitle = '', intro = '', suggestions = [],
    thread, asking = false, draft = '', setDraft, draftFocus = null,
    onAsk, onClear, onSuggest, onOpenRef,
    visible = true, restore = null, placeholder = 'Спросите…', busyText = 'Думаю…', composerBusyLabel = 'Думаю…',
    className = '', style,
}) {
    const messages = thread?.messages || [];
    const { boxRef, onScroll, scrollToEnd, scrollToStart } = useThreadScroll();
    const lastSeen = useRef({ count: 0, key: null });
    const key = thread?.key ?? thread?.day ?? null;

    /* К последнему ответу — к его НАЧАЛУ: ответ длинный, и прыжок в конец
       оставил бы человека дочитывать его снизу вверх. */
    const toLatest = () => {
        const box = boxRef.current;
        const last = [...messages].reverse().find((m) => m.role === 'assistant');
        const node = last && box?.querySelector(`[data-qa-message="${last.id}"]`);
        if (node && messages[messages.length - 1]?.role === 'assistant') scrollToStart(node);
        else scrollToEnd();
    };

    /* Свой вопрос — к концу ленты, пришёл ответ — к его началу. Пока колонка
       скрыта (узкий экран, другая вкладка), у ленты нет размеров и прокрутка
       не действует: она делается, когда колонку показали. Вернулись из
       карточки — лента там же, где была (restore), а не в конце. */
    const pendingRestore = useRef(null);
    useLayoutEffect(() => {
        const box = boxRef.current;
        if (!box || !visible) return;
        const seen = lastSeen.current;
        const count = messages.length;
        if (seen.key !== key || seen.hidden) {
            lastSeen.current = { count, key };
            if (restore && (restore.key ?? restore.day) === key && Number.isFinite(restore.scrollTop)) {
                pendingRestore.current = restore.scrollTop;
            } else {
                pendingRestore.current = null;
                toLatest();
            }
            return;
        }
        if (count > seen.count) {
            pendingRestore.current = null;
            const last = messages[count - 1];
            if (last?.role === 'assistant') {
                scrollToStart(box.querySelector(`[data-qa-message="${last.id}"]`));
            } else {
                scrollToEnd();
            }
        }
        lastSeen.current = { count, key };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [messages, key, visible, boxRef, scrollToEnd, scrollToStart]);

    /* Прокрутку ленты, сохранённую до карточки, ставим, когда у ленты появилась
       высота: на широком экране её задаёт родитель уже после первой отрисовки,
       и раньше scrollTop упирался бы в ноль. */
    useLayoutEffect(() => {
        const box = boxRef.current;
        if (pendingRestore.current == null || !box || !visible) return;
        if (box.scrollHeight <= box.clientHeight) return;
        box.scrollTop = pendingRestore.current;
        pendingRestore.current = null;
    });

    useEffect(() => {
        if (!visible) lastSeen.current = { ...lastSeen.current, hidden: true };
    }, [visible]);

    // К концу ленты — когда вопрос только что задан, а не при каждом монтировании:
    // вернулись из карточки, пока ИИ думает, — лента остаётся там, где была.
    const wasAsking = useRef(asking);
    useEffect(() => {
        if (asking && !wasAsking.current && visible) scrollToEnd();
        wasAsking.current = asking;
    }, [asking, visible, scrollToEnd]);

    return (
        <section aria-label={title} style={style}
                 className={`${iosCard} flex min-h-[420px] flex-col overflow-hidden ${className}`}>
            <header className="flex items-center gap-2.5 border-b border-slate-100 px-4 py-3">
                <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-blue-500 to-indigo-500 text-white shadow-sm">
                    <Sparkles size={15} aria-hidden="true" />
                </div>
                <div className="min-w-0 flex-1">
                    <div className="text-[14px] font-semibold text-slate-900">{title}</div>
                    {subtitle && <div className="truncate text-[11.5px] text-slate-400">{subtitle}</div>}
                </div>
                {messages.length > 0 && onClear && (
                    /* Пока ИИ отвечает, начать заново нельзя (ответ лёг бы в
                       стёртую переписку) — меню недоступно, а не молчит. */
                    <IosMenu label="Действия с перепиской" disabled={asking}
                             items={[{ key: 'clear', label: 'Начать заново', icon: Eraser, onSelect: onClear }]} />
                )}
            </header>
            <div ref={boxRef} onScroll={onScroll} role="log" aria-live="polite"
                 className="thin-scroll flex-1 space-y-2.5 overflow-y-auto bg-slate-50/60 py-3">
                {thread?.loading ? (
                    <div className="flex h-full items-center justify-center gap-2 text-[13px] text-slate-400">
                        <Loader2 size={15} className="animate-spin" aria-hidden="true" />Загружаю переписку…
                    </div>
                ) : thread?.error ? (
                    <div className="px-4 py-6 text-center text-[13px] text-rose-600">{thread.error}</div>
                ) : messages.length === 0 && !asking ? (
                    <div className="flex flex-col gap-3 px-4 py-4">
                        {intro && <p className="text-[13px] leading-relaxed text-slate-500">{intro}</p>}
                        <div className="flex flex-col items-start gap-1.5">
                            {suggestions.map((text) => (
                                <button key={text} type="button" onClick={() => onSuggest?.(text)}
                                        className="rounded-2xl bg-white px-3 py-2 text-left text-[12.5px] leading-snug text-slate-700 ring-1 ring-slate-200/80 transition hover:bg-blue-50 hover:text-blue-700 hover:ring-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 active:scale-[0.99]">
                                    {text}
                                </button>
                            ))}
                        </div>
                    </div>
                ) : (
                    messages.map((message) => (
                        <div key={message.id} data-qa-message={message.id}>
                            {message.role === 'user' ? (
                                <ChatBubble out meta={message.pending ? 'отправляю…' : fmtTime(message.created_at)}>
                                    {message.body}
                                </ChatBubble>
                            ) : (
                                <ChatBubble plain={false} meta={fmtTime(message.created_at)}>
                                    <AiMarkup html={message.body} onOpenRef={onOpenRef} variant="chat" />
                                </ChatBubble>
                            )}
                        </div>
                    ))
                )}
                {asking && (
                    <ChatBubble tone="muted">
                        <span className="inline-flex items-center gap-2">
                            <Loader2 size={14} className="animate-spin" aria-hidden="true" />{busyText}
                        </span>
                    </ChatBubble>
                )}
            </div>
            <ChatComposer value={draft} onChange={setDraft} onSubmit={onAsk}
                          busy={asking} disabled={Boolean(thread?.loading)}
                          placeholder={placeholder} focusKey={draftFocus}
                          submitLabel="Спросить" busyLabel={composerBusyLabel} />
        </section>
    );
}
