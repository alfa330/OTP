import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Check, Loader2, StickyNote, X } from 'lucide-react';
import { internalNotesKey } from './useInternalNotes';

const storageKeyFor = (chat) => `icore.wazzup.internal-note.${internalNotesKey(chat)}`;
const readDraft = (key) => {
    try {
        const draft = JSON.parse(sessionStorage.getItem(key) || 'null');
        const pending = typeof draft?.pending?.clientNoteId === 'string' && typeof draft.pending.text === 'string'
            ? draft.pending : null;
        return { text: pending?.text ?? (typeof draft?.text === 'string' ? draft.text : ''), pending };
    } catch { return { text: '', pending: null }; }
};
const storeDraft = (key, text, pending = null) => {
    try {
        if (text || pending) sessionStorage.setItem(key, JSON.stringify({ text, pending }));
        else sessionStorage.removeItem(key);
    } catch { /* A private browser can still keep the current mounted draft. */ }
};
const clearAcceptedDraft = (key, clientNoteId) => {
    try {
        if (JSON.parse(sessionStorage.getItem(key) || 'null')?.pending?.clientNoteId !== clientNoteId) return;
        sessionStorage.removeItem(key);
    } catch { /* Storage is optional. */ }
};
const newNoteId = () => {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

export function InternalNoteDraft({ apiBaseUrl, headers, chat, onSaved, onCancel }) {
    const storageKey = storageKeyFor(chat);
    const [saved] = useState(() => readDraft(storageKey));
    const [text, setText] = useState(saved.text);
    const [busy, setBusy] = useState(false);
    const [uncertain, setUncertain] = useState(Boolean(saved.pending));
    const [error, setError] = useState(saved.pending ? 'Сохранение ещё не подтверждено. Повторите его — второй комментарий не появится.' : '');
    const pendingRef = useRef(saved.pending);
    const requestRef = useRef(null);
    const mountedRef = useRef(false);
    const textareaRef = useRef(null);
    const length = Array.from(text).length;
    const tooLong = length > 4000;

    useEffect(() => {
        mountedRef.current = true;
        textareaRef.current?.focus();
        return () => { mountedRef.current = false; requestRef.current?.abort(); };
    }, []);

    useEffect(() => {
        const field = textareaRef.current;
        if (!field) return;
        field.style.height = 'auto';
        field.style.height = `${Math.min(field.scrollHeight, 144)}px`;
        field.style.overflowY = field.scrollHeight > 144 ? 'auto' : 'hidden';
    }, [text]);

    const updateText = (value) => {
        if (requestRef.current || pendingRef.current) return;
        setText(value);
        setError('');
        storeDraft(storageKey, value);
    };

    const submit = async (event) => {
        event?.preventDefault();
        if (requestRef.current || !chat?.channelId || !chat?.chatId || !text.trim() || tooLong) return;
        let payload = pendingRef.current;
        const checkingPrevious = Boolean(payload);
        if (!payload) {
            try {
                payload = { account: 'op', channelId: chat.channelId, chatId: chat.chatId,
                    text: text.trim(), clientNoteId: newNoteId() };
            } catch {
                setError('Не удалось подготовить комментарий. Откройте iCORE по защищённому адресу HTTPS.');
                return;
            }
        }
        if (payload.account !== 'op' || payload.channelId !== chat.channelId || payload.chatId !== chat.chatId) {
            setError('Сохранённый комментарий относится к другому чату. Обновите страницу.');
            return;
        }
        pendingRef.current = payload;
        storeDraft(storageKey, payload.text, payload);
        const controller = new AbortController();
        requestRef.current = controller;
        setBusy(true);
        setError('');
        try {
            const { data } = await axios.post(`${apiBaseUrl}/api/wazzup/pilot/notes`, payload, {
                headers: typeof headers === 'function' ? headers() : headers,
                signal: controller.signal, timeout: 20000,
            });
            if (data?.item?.id == null || typeof data.item.text !== 'string' || !data.item.createdAt) {
                throw new Error('Invalid note response');
            }
            clearAcceptedDraft(storageKey, payload.clientNoteId);
            pendingRef.current = null;
            if (!mountedRef.current || controller.signal.aborted) return;
            setText('');
            setUncertain(false);
            // An accepted note stays accepted even if a UI callback fails.
            try { Promise.resolve(onSaved?.(data.item)).catch(() => {}); } catch { /* Already saved. */ }
        } catch (failure) {
            if (!mountedRef.current || controller.signal.aborted) return;
            const status = failure.response?.status;
            // Losing access while retrying says nothing about whether the first
            // request committed. Keep its UUID until access is restored.
            const unknown = !status || status >= 500 || [408, 425].includes(status)
                || (checkingPrevious && [401, 403, 404].includes(status));
            if (!unknown) pendingRef.current = null;
            storeDraft(storageKey, payload.text, pendingRef.current);
            setUncertain(unknown);
            setError(failure.response?.data?.error || (unknown
                ? 'Ответ сервера не получен. Повторите сохранение — второй комментарий не появится.'
                : 'Не удалось сохранить комментарий. Проверьте текст и попробуйте ещё раз.'));
        } finally {
            if (requestRef.current === controller) requestRef.current = null;
            if (mountedRef.current && !controller.signal.aborted) setBusy(false);
        }
    };

    return (
        <form onSubmit={submit} className="shrink-0 border-t border-slate-200/70 bg-white p-2.5 sm:px-4">
            <div className="mx-auto w-full max-w-[1040px]">
            <div className="mb-1.5 flex items-center gap-1.5 px-1 text-[11px] text-slate-500">
                <StickyNote size={13} aria-hidden="true" />
                <span>Комментарий · только команде</span>
                {length > 3500 && <span className={`ml-auto tabular-nums ${tooLong ? 'text-red-600' : ''}`}>{length}/4000</span>}
            </div>
            <div className="flex items-end gap-1 rounded-2xl border border-slate-200 bg-white p-1.5 shadow-sm focus-within:border-slate-300">
                <textarea ref={textareaRef} rows={1} value={text} readOnly={busy || uncertain}
                    onChange={(event) => updateText(event.target.value)}
                    onKeyDown={(event) => {
                        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent?.isComposing && !event.isComposing && event.keyCode !== 229) {
                            event.preventDefault();
                            submit();
                        }
                    }}
                    aria-label="Текст внутреннего комментария" placeholder="Комментарий для коллег…"
                    title="Enter — сохранить, Shift + Enter — новая строка"
                    className="min-w-0 flex-1 resize-none border-0 bg-transparent px-2 py-1.5 text-[15px] leading-6 text-slate-900 outline-none placeholder:text-slate-400" />
                <button type="button" onClick={onCancel}
                    className="flex h-9 w-8 shrink-0 items-center justify-center rounded-xl text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                    aria-label="Закрыть внутренний комментарий" title="Закрыть, сохранив черновик"><X size={16} /></button>
                <button type="submit" disabled={busy || !text.trim() || tooLong}
                    aria-label={busy ? 'Сохранение комментария' : uncertain ? 'Повторить сохранение комментария' : 'Сохранить комментарий'}
                    title={uncertain ? 'Повторить сохранение комментария' : 'Сохранить комментарий'}
                    className="inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-slate-700 px-2.5 text-xs font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40">
                    {busy ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}
                    <span className="hidden sm:inline">{busy ? 'Сохранение…' : uncertain ? 'Повторить' : 'Сохранить'}</span>
                </button>
            </div>
            {error && <p role="alert" className="mt-1.5 px-1 text-xs text-red-700">{error}</p>}
            </div>
        </form>
    );
}

export default function ChatInternalNoteComposer(props) {
    return <InternalNoteDraft key={internalNotesKey(props.chat)} {...props} />;
}
