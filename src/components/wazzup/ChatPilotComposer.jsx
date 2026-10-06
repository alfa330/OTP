import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { AlertCircle, Check, Loader2, RefreshCw, Send } from 'lucide-react';
import { classifyPilotSendFailure, pilotChatKey, pilotDraftStorageKey } from './chatPilot.js';

const readDraft = (key) => {
    try {
        const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
        if (!saved || typeof saved.text !== 'string') return { text: '', pending: null };
        const pending = saved.pending?.clientMessageId && typeof saved.pending?.text === 'string'
            ? saved.pending : null;
        return { text: pending ? pending.text : saved.text, pending };
    } catch {
        return { text: '', pending: null };
    }
};

const writeDraft = (key, text, pending) => {
    try {
        if (text || pending) sessionStorage.setItem(key, JSON.stringify({ text, pending }));
        else sessionStorage.removeItem(key);
    } catch { /* Private browsing can disable storage; the mounted draft still works. */ }
};

const settleDraft = (key, payload, text, pending) => {
    try {
        const current = JSON.parse(sessionStorage.getItem(key) || 'null');
        // An earlier mounted composer may finish after its replacement already
        // checked the send and started a new draft. Do not erase that new text.
        if (current?.pending?.clientMessageId !== payload.clientMessageId) return;
    } catch { /* Fall through when browser storage is unavailable. */ }
    writeDraft(key, text, pending);
};

const newMessageId = () => {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

function ChatPilotDraft({ apiBaseUrl, headers, chat, onSent, maxLength = 4096 }) {
    const storageKey = pilotDraftStorageKey(chat);
    const [saved] = useState(() => readDraft(storageKey));
    const [text, setText] = useState(saved.text);
    const [state, setState] = useState(saved.pending ? 'unknown' : 'idle');
    const [error, setError] = useState(saved.pending
        ? 'Предыдущая отправка ещё не подтверждена. Проверьте её результат.' : '');
    const [checkedConversation, setCheckedConversation] = useState(false);
    const pendingRef = useRef(saved.pending);
    const busyRef = useRef(false);
    const mountedRef = useRef(true);

    useEffect(() => {
        mountedRef.current = true;
        return () => { mountedRef.current = false; };
    }, []);

    const locked = state === 'sending' || state === 'unknown';
    const length = Array.from(text).length;
    const tooLong = length > maxLength;
    const canSubmit = state === 'unknown' || (Boolean(text.trim()) && !tooLong);

    const updateText = (value) => {
        if (busyRef.current || pendingRef.current) return;
        setText(value);
        setError('');
        setState('idle');
        writeDraft(storageKey, value, null);
    };

    const submit = async (event) => {
        event?.preventDefault();
        // Ref closes the same-tick window before React has disabled the button.
        if (busyRef.current || !canSubmit || !chat?.channelId || !chat?.chatId) return;
        const checkingPrevious = Boolean(pendingRef.current);
        let payload = pendingRef.current;
        if (!payload) {
            try {
                payload = {
                    account: 'op', channelId: chat.channelId, chatId: chat.chatId,
                    text: text.trim(), clientMessageId: newMessageId(),
                };
            } catch {
                setError('Не удалось подготовить отправку. Откройте iCORE по защищённому адресу HTTPS.');
                setState('failed');
                return;
            }
        }
        // A stored attempt must never be redirected into a different chat.
        if (payload.account !== 'op' || payload.channelId !== chat.channelId || payload.chatId !== chat.chatId) {
            setError('Сохранённая отправка относится к другому чату. Обновите страницу.');
            setState('unknown');
            return;
        }
        pendingRef.current = payload;
        busyRef.current = true;
        writeDraft(storageKey, payload.text, payload);
        setState('sending');
        setError('');
        setCheckedConversation(false);
        try {
            const response = await axios.post(`${apiBaseUrl}/api/wazzup/pilot/send`, payload, {
                headers: typeof headers === 'function' ? headers() : headers,
                timeout: 45000,
            });
            if (response.data?.state !== 'sent' || !response.data?.messageId) {
                throw { response: { status: 409, data: {
                    state: 'unknown', error: 'Отправка ещё не подтверждена. Проверьте её результат.',
                } } };
            }
            pendingRef.current = null;
            settleDraft(storageKey, payload, '', null);
            if (!mountedRef.current) return;
            setText('');
            setState('sent');
            setError('');
            // Refresh failures must not mislabel a successful send as unknown.
            try { Promise.resolve(onSent?.(response.data)).catch(() => {}); } catch { /* Message is already accepted. */ }
        } catch (sendError) {
            const failure = classifyPilotSendFailure(sendError);
            // An expired/revoked login while checking an old timeout tells us
            // nothing about whether that original message reached Wazzup.
            if (checkingPrevious && sendError.response?.data?.state !== 'failed') failure.state = 'unknown';
            if (failure.state === 'failed') pendingRef.current = null;
            settleDraft(storageKey, payload, payload.text, pendingRef.current);
            if (!mountedRef.current) return;
            setState(failure.state);
            setError(failure.message);
        } finally {
            busyRef.current = false;
        }
    };

    return (
        <form onSubmit={submit} className="shrink-0 border-t border-slate-200/70 bg-white p-3"
              data-testid="wazzup-pilot-composer">
            <label htmlFor="wazzup-pilot-message" className="mb-1.5 block text-[12px] font-semibold text-slate-600">
                Ответ в WhatsApp
            </label>
            <textarea id="wazzup-pilot-message" rows={3} value={text} readOnly={locked}
                      onChange={(event) => updateText(event.target.value)}
                      onKeyDown={(event) => {
                          if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)
                              && !event.nativeEvent.isComposing) submit(event);
                      }}
                      placeholder="Напишите сообщение…"
                      aria-describedby="wazzup-pilot-message-help"
                      aria-invalid={tooLong || undefined}
                      className="block max-h-40 min-h-20 w-full resize-y rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-[13px] text-slate-900 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 read-only:opacity-70" />
            {error && (
                <div role="alert" className="mt-2 flex items-start gap-1.5 text-[12px] text-amber-700">
                    <AlertCircle size={14} className="mt-0.5 shrink-0" />
                    <span>{error}{state === 'unknown' && ' Проверка использует ту же отправку и не создаёт второе сообщение.'}</span>
                </div>
            )}
            {state === 'unknown' && (
                <div className="mt-2 rounded-xl bg-amber-50 p-2.5 text-[12px] text-amber-900">
                    <label className="flex items-center gap-2">
                        <input type="checkbox" checked={checkedConversation}
                               onChange={(event) => setCheckedConversation(event.target.checked)} />
                        Я проверил результат в переписке
                    </label>
                    <button type="button" disabled={!checkedConversation}
                            onClick={() => {
                                if (!checkedConversation || busyRef.current) return;
                                pendingRef.current = null;
                                writeDraft(storageKey, '', null);
                                setText(''); setError(''); setState('idle');
                                setCheckedConversation(false);
                            }}
                            className="mt-2 font-semibold text-blue-700 hover:underline disabled:opacity-50">
                        Начать новое сообщение
                    </button>
                </div>
            )}
            <div className="mt-2 flex items-center justify-between gap-2">
                <div id="wazzup-pilot-message-help" className="text-[11px] text-slate-400" aria-live="polite">
                    {state === 'sent' ? (
                        <span className="inline-flex items-center gap-1 text-emerald-600"><Check size={12} /> Отправлено в Wazzup</span>
                    ) : <span className={tooLong ? 'text-rose-600' : ''}>{length}/{maxLength} · Ctrl+Enter — отправить</span>}
                </div>
                <button type="submit" disabled={state === 'sending' || !canSubmit}
                        className="inline-flex shrink-0 items-center gap-1.5 rounded-xl bg-blue-500 px-3 py-2 text-[12px] font-semibold text-white transition hover:bg-blue-600 disabled:cursor-not-allowed disabled:opacity-50">
                    {state === 'sending' ? <Loader2 size={14} className="animate-spin" />
                        : state === 'unknown' ? <RefreshCw size={14} /> : <Send size={14} />}
                    {state === 'sending' ? 'Ожидаем ответ…' : state === 'unknown' ? 'Проверить отправку' : 'Отправить'}
                </button>
            </div>
        </form>
    );
}

export default function ChatPilotComposer(props) {
    // The parent may replace a selected chat without remounting this component.
    // Isolate its draft and in-flight identity even in that case.
    return <ChatPilotDraft key={pilotChatKey('op', props.chat)} {...props} />;
}
