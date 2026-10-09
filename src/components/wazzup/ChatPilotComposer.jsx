import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { AlertCircle, Check, Clock3, FileText, Languages, Loader2, Paperclip, RefreshCw, Reply, Send, Sparkles, Undo2, X } from 'lucide-react';
import { classifyPilotSendFailure, pilotChatKey, pilotDraftStorageKey } from './chatPilot.js';
import ChatComposerTools from './ChatComposerTools';
import useWabaWindowExpired from './useWabaWindowExpired';
import ChatMessageText from './ChatMessageText';
import { UPLOAD_ACCEPT, uploadedAttachment, uploadFileError, uploadSizeLabel } from './chatUploads.js';

const readDraft = (key) => {
    try {
        const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
        if (!saved || typeof saved.text !== 'string') return { text: '', pending: null };
        const pending = saved.pending?.clientMessageId && typeof saved.pending?.text === 'string'
            ? saved.pending : null;
        return { text: pending && !pending.attachmentId ? pending.text : saved.text, pending,
            attachment: uploadedAttachment(saved.attachment),
            preview: typeof saved.preview === 'string' ? saved.preview : '', replyTo: saved.replyTo || null };
    } catch {
        return { text: '', pending: null };
    }
};

const writeDraft = (key, text, pending, preview = '', replyTo = null, attachment = null) => {
    try {
        if (text || pending || attachment) sessionStorage.setItem(key, JSON.stringify({ text, pending, preview, replyTo,
            attachment: uploadedAttachment(attachment) }));
        else sessionStorage.removeItem(key);
    } catch { /* Private browsing can disable storage; the mounted draft still works. */ }
};

const settleDraft = (key, payload, text, pending, preview = '', replyTo = null, attachment = null) => {
    try {
        const current = JSON.parse(sessionStorage.getItem(key) || 'null');
        // An earlier mounted composer may finish after its replacement already
        // checked the send and started a new draft. Do not erase that new text.
        if (current?.pending?.clientMessageId !== payload.clientMessageId) return;
    } catch { /* Fall through when browser storage is unavailable. */ }
    writeDraft(key, text, pending, preview, replyTo, attachment);
};

const newMessageId = () => {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

function ChatPilotDraft({ apiBaseUrl, headers, chat, channelTransport, lastInboundAt, onSent, replyTo, onCancelReply, maxLength = 4096 }) {
    const storageKey = pilotDraftStorageKey(chat);
    const [saved] = useState(() => readDraft(storageKey));
    const [text, setText] = useState(saved.text);
    const [preview, setPreview] = useState(saved.preview || '');
    const [attachment, setAttachment] = useState(saved.attachment || null);
    const [uploading, setUploading] = useState(null);
    const attachmentRef = useRef(saved.attachment || null);
    const uploadRef = useRef(null);
    const fileInputRef = useRef(null);
    const textareaRef = useRef(null);
    const composerKeyDownRef = useRef(null);
    const selectionRef = useRef({ start: saved.text.length, end: saved.text.length });
    const [state, setState] = useState(saved.pending ? 'unknown' : 'idle');
    const [error, setError] = useState(saved.pending
        ? 'Предыдущая отправка ещё не подтверждена. Проверьте её результат.' : '');
    const [checkedConversation, setCheckedConversation] = useState(false);
    const [assistAction, setAssistAction] = useState(null);
    const [undoText, setUndoText] = useState(null);
    const assistRef = useRef(null);
    const editVersionRef = useRef(0);
    const pendingRef = useRef(saved.pending);
    const pendingReplyRef = useRef(saved.replyTo);
    const busyRef = useRef(false);
    const mountedRef = useRef(true);

    useEffect(() => {
        mountedRef.current = true;
        return () => { mountedRef.current = false; assistRef.current?.abort(); uploadRef.current?.abort(); };
    }, []);

    useEffect(() => {
        const field = textareaRef.current;
        if (!field) return;
        field.style.height = 'auto';
        field.style.height = `${Math.min(field.scrollHeight, 160)}px`;
        field.style.overflowY = field.scrollHeight > 160 ? 'auto' : 'hidden';
    }, [text, preview]);

    useEffect(() => {
        if (replyTo?.messageId && !pendingRef.current) textareaRef.current?.focus();
    }, [replyTo?.messageId]);

    const locked = state === 'sending' || state === 'unknown';
    const hasFile = Boolean(attachment || uploading);
    const length = Array.from(text).length;
    const tooLong = length > maxLength;
    const canSubmit = !uploading && (state === 'unknown' || Boolean(attachment) || (Boolean(text.trim()) && !tooLong));
    const effectiveReply = pendingRef.current ? pendingReplyRef.current || (pendingRef.current.replyToMessageId
        ? { messageId: pendingRef.current.replyToMessageId, text: 'Сообщение из этой переписки' } : null) : replyTo;
    const assistDisabled = locked || hasFile || Boolean(preview) || !text.trim() || tooLong || Boolean(assistAction);
    const assistHint = preview ? 'Одобренный шаблон нельзя изменять. Уберите шаблон, чтобы написать обычное сообщение.' : '';
    const showWabaHint = useWabaWindowExpired(channelTransport === 'wapi', lastInboundAt);

    const cancelAssist = () => {
        editVersionRef.current += 1;
        assistRef.current?.abort();
        assistRef.current = null;
        setAssistAction(null);
        setUndoText(null);
    };

    const updateText = (value) => {
        if (busyRef.current || pendingRef.current || attachmentRef.current || uploadRef.current) return;
        cancelAssist();
        setText(value);
        setPreview('');
        setError('');
        setState('idle');
        writeDraft(storageKey, value, null, '', replyTo);
    };

    const removeAttachment = () => {
        if (busyRef.current || pendingRef.current) return;
        uploadRef.current?.abort();
        uploadRef.current = null;
        attachmentRef.current = null;
        setUploading(null); setAttachment(null); setError(''); setState('idle');
        writeDraft(storageKey, text, null, preview, replyTo);
    };

    const chooseFile = async (event) => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (!file || busyRef.current || pendingRef.current || preview || attachmentRef.current || uploadRef.current) return;
        const validationError = uploadFileError(file);
        if (validationError) { setError(validationError); return; }
        const controller = new AbortController();
        uploadRef.current = controller;
        cancelAssist();
        setUploading({ name: file.name, size: file.size });
        setError(''); setState('idle');
        try {
            const body = new FormData();
            body.append('account', 'op');
            body.append('channelId', chat.channelId);
            body.append('chatId', chat.chatId);
            body.append('clientUploadId', newMessageId());
            body.append('file', file);
            const requestHeaders = { ...(typeof headers === 'function' ? headers() : headers) };
            // The browser must add the multipart boundary itself.
            for (const key of Object.keys(requestHeaders)) {
                if (key.toLowerCase() === 'content-type') delete requestHeaders[key];
            }
            const { data } = await axios.post(`${apiBaseUrl}/api/wazzup/pilot/uploads`, body, {
                headers: requestHeaders, signal: controller.signal, timeout: 90000,
            });
            if (!mountedRef.current || controller.signal.aborted || uploadRef.current !== controller) return;
            const ready = uploadedAttachment(data?.attachment);
            if (!ready) throw new Error('invalid_attachment');
            attachmentRef.current = ready;
            setAttachment(ready);
            writeDraft(storageKey, text, null, '', replyTo, ready);
        } catch (uploadError) {
            if (!mountedRef.current || controller.signal.aborted || uploadRef.current !== controller) return;
            setError(uploadError.response?.data?.error || 'Не удалось загрузить файл. Выберите его ещё раз.');
        } finally {
            if (uploadRef.current === controller) {
                uploadRef.current = null;
                if (mountedRef.current) setUploading(null);
            }
        }
    };

    const assist = async (action) => {
        if (assistDisabled || assistRef.current || busyRef.current || pendingRef.current || attachmentRef.current || uploadRef.current) return;
        const controller = new AbortController();
        const version = editVersionRef.current;
        const original = text;
        assistRef.current = controller;
        setAssistAction(action);
        setError('');
        try {
            const { data } = await axios.post(`${apiBaseUrl}/api/wazzup/pilot/assist`, {
                account: 'op', action, text: original,
            }, { headers: typeof headers === 'function' ? headers() : headers, signal: controller.signal, timeout: 25000 });
            // Editing, changing chat or sending must never be overwritten by an old AI result.
            if (!mountedRef.current || controller.signal.aborted || editVersionRef.current !== version
                || busyRef.current || pendingRef.current) return;
            if (typeof data?.text !== 'string' || !data.text.trim() || Array.from(data.text).length > maxLength) {
                setError('Не удалось получить готовый текст. Исходное сообщение сохранено.');
                return;
            }
            setText(data.text);
            setUndoText(original);
            setState('idle');
            writeDraft(storageKey, data.text, null, '', replyTo);
            selectionRef.current = { start: data.text.length, end: data.text.length };
            textareaRef.current?.focus();
        } catch (assistError) {
            if (!mountedRef.current || controller.signal.aborted || editVersionRef.current !== version) return;
            setError(assistError.response?.data?.error || 'Не удалось обработать текст. Попробуйте ещё раз.');
        } finally {
            if (assistRef.current === controller) {
                assistRef.current = null;
                if (mountedRef.current) setAssistAction(null);
            }
        }
    };

    const submit = async (event) => {
        event?.preventDefault();
        // Ref closes the same-tick window before React has disabled the button.
        if (busyRef.current || assistRef.current || uploadRef.current || !canSubmit || !chat?.channelId || !chat?.chatId) return;
        const checkingPrevious = Boolean(pendingRef.current);
        const selectedAttachment = attachmentRef.current;
        let payload = pendingRef.current;
        if (!payload) {
            if (selectedAttachment?.expiresAt && Date.parse(selectedAttachment.expiresAt) <= Date.now()) {
                setError('Срок хранения файла истёк. Уберите вложение и выберите файл ещё раз.');
                setState('failed');
                return;
            }
            try {
                payload = {
                    account: 'op', channelId: chat.channelId, chatId: chat.chatId,
                    text: selectedAttachment ? '' : text.trim(), clientMessageId: newMessageId(),
                    ...(selectedAttachment ? { attachmentId: selectedAttachment.id } : {}),
                    ...(replyTo?.messageId ? { replyToMessageId: replyTo.messageId } : {}),
                };
                pendingReplyRef.current = replyTo ? { messageId: replyTo.messageId, text: replyTo.text,
                    authorName: replyTo.authorName, isEcho: replyTo.isEcho } : null;
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
        const draftText = payload.attachmentId ? text : payload.text;
        writeDraft(storageKey, draftText, payload, preview, pendingReplyRef.current, selectedAttachment);
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
            const remainingText = payload.attachmentId ? draftText : '';
            settleDraft(storageKey, payload, remainingText, null);
            if (!mountedRef.current) return;
            setText(remainingText);
            attachmentRef.current = null;
            setAttachment(null);
            setPreview('');
            setUndoText(null);
            setState('sent');
            setError('');
            if (payload.replyToMessageId) {
                try { onCancelReply?.(payload.replyToMessageId); } catch { /* Message is already accepted. */ }
            }
            // Refresh failures must not mislabel a successful send as unknown.
            try { Promise.resolve(onSent?.(response.data)).catch(() => {}); } catch { /* Message is already accepted. */ }
        } catch (sendError) {
            const failure = classifyPilotSendFailure(sendError);
            // An expired/revoked login while checking an old timeout tells us
            // nothing about whether that original message reached Wazzup.
            if (checkingPrevious && sendError.response?.data?.state !== 'failed') failure.state = 'unknown';
            if (failure.state === 'failed') pendingRef.current = null;
            settleDraft(storageKey, payload, draftText, pendingRef.current, preview, pendingReplyRef.current, selectedAttachment);
            if (!mountedRef.current) return;
            setState(failure.state);
            setError(failure.message);
        } finally {
            busyRef.current = false;
        }
    };

    return (
        <form onSubmit={submit} className="shrink-0 border-t border-slate-200/70 bg-white p-2.5 sm:px-4"
              data-testid="wazzup-pilot-composer">
            <div className="relative mx-auto w-full max-w-[1040px]">
            {showWabaHint && <div id="wazzup-waba-window-help"
                className="mb-2 flex items-start gap-2 rounded-xl bg-slate-50 px-3 py-2 text-[12px] leading-[1.5] text-slate-500">
                <Clock3 size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
                <div>
                    <p>Обычные сообщения можно отправлять в течение 24 часов после последнего сообщения клиента.</p>
                    <p>Чтобы написать первым или после 24 часов, выберите одобренный шаблон WABA из Wazzup через <span className="font-semibold text-slate-600">/</span>.</p>
                </div>
            </div>}
            {effectiveReply && <div className="mb-2 flex items-center gap-2 rounded-lg border-l-2 border-blue-400 bg-blue-50 px-3 py-2 text-sm"
                data-testid="wazzup-reply-preview">
                <Reply size={17} className="shrink-0 text-blue-500" />
                <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-semibold text-blue-600">{effectiveReply.authorName || (effectiveReply.isEcho ? 'Вы' : 'Собеседник')}</div>
                    <div className="truncate text-slate-600"><ChatMessageText text={effectiveReply.text || 'Вложение'} /></div>
                </div>
                <button type="button" disabled={locked} aria-label="Отменить ответ" title="Отменить ответ"
                    onClick={() => onCancelReply?.()} className="rounded p-1 text-slate-500 hover:bg-blue-100 disabled:opacity-40"><X size={17} /></button>
            </div>}
            {preview && <div className="mb-1 flex items-center justify-between text-xs text-slate-500">
                <span>Одобренный шаблон Wazzup</span>
                <button type="button" disabled={locked} onClick={() => updateText('')} className="text-blue-600">Убрать шаблон</button>
            </div>}
            {hasFile && <div className="mb-2 flex items-center gap-2.5 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2"
                data-testid="wazzup-file-preview" aria-live="polite">
                {uploading ? <Loader2 size={20} className="shrink-0 animate-spin text-slate-400" />
                    : <FileText size={20} className="shrink-0 text-slate-500" />}
                <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-slate-700">{(attachment || uploading).name}</div>
                    <div className="text-xs text-slate-500">{uploadSizeLabel((attachment || uploading).size)} · {uploading ? 'Загружается…' : 'Готов к отправке'}</div>
                    <div className="mt-0.5 text-xs text-slate-500">{text ? 'Файл отправится отдельно. Текст сохранён как черновик.' : 'Файл отправится отдельно, без подписи.'}</div>
                </div>
                <button type="button" disabled={locked} onClick={removeAttachment} aria-label="Убрать файл" title="Убрать файл"
                    className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-200 disabled:opacity-40"><X size={17} /></button>
            </div>}
            <div className="flex flex-wrap items-end gap-1.5 sm:flex-nowrap">
                <input ref={fileInputRef} type="file" accept={UPLOAD_ACCEPT} onChange={chooseFile} tabIndex={-1}
                    disabled={locked || Boolean(preview) || hasFile} className="hidden" aria-label="Выбрать файл" />
                <button type="button" disabled={locked || Boolean(preview) || hasFile} aria-label="Прикрепить файл" title={preview ? 'Уберите шаблон, чтобы прикрепить файл' : 'Прикрепить файл'}
                    onClick={() => {
                        if (!busyRef.current && !pendingRef.current && !preview && !attachmentRef.current && !uploadRef.current) fileInputRef.current?.click();
                    }} className="flex h-10 w-9 shrink-0 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-blue-600 disabled:opacity-35">
                    <Paperclip size={20} />
                </button>
                <ChatComposerTools apiBaseUrl={apiBaseUrl} headers={headers} channelId={chat.channelId}
                    composerRef={textareaRef} composerKeyDownRef={composerKeyDownRef}
                    locked={locked || hasFile} emojiDisabled={Boolean(preview) || hasFile} slash={!preview && !hasFile && /^\/[^\n]*$/.test(text) ? text.slice(1) : null}
                    onChoose={({ text: next, preview: nextPreview }) => {
                        if (busyRef.current || pendingRef.current || attachmentRef.current || uploadRef.current) return;
                        cancelAssist();
                        setText(next); setPreview(nextPreview); setError(''); setState('idle');
                        writeDraft(storageKey, next, null, nextPreview, replyTo);
                        selectionRef.current = { start: next.length, end: next.length };
                        textareaRef.current?.focus();
                    }}
                    onEmoji={(emoji) => {
                        if (preview || locked || attachmentRef.current || uploadRef.current) return;
                        const { start, end } = selectionRef.current;
                        const next = text.slice(0, start) + emoji + text.slice(end);
                        updateText(next);
                        const position = start + emoji.length;
                        selectionRef.current = { start: position, end: position };
                        requestAnimationFrame(() => { textareaRef.current?.focus(); textareaRef.current?.setSelectionRange(position, position); });
                    }} />
            <textarea ref={textareaRef} id="wazzup-pilot-message" rows={1} value={preview || text} readOnly={locked || Boolean(preview) || hasFile}
                      onChange={(event) => updateText(event.target.value)}
                      onSelect={(event) => { selectionRef.current = { start: event.target.selectionStart, end: event.target.selectionEnd }; }}
                      onKeyDown={(event) => {
                          if (composerKeyDownRef.current?.(event)) return;
                          if (event.key === 'Enter' && !event.shiftKey
                              && !event.nativeEvent?.isComposing && event.keyCode !== 229
                              && event.nativeEvent?.keyCode !== 229) submit(event);
                      }}
                      placeholder={hasFile ? 'Сначала отправьте или уберите файл…' : 'Сообщение…'}
                      aria-label="Сообщение"
                      aria-describedby={showWabaHint ? 'wazzup-pilot-message-help wazzup-waba-window-help' : 'wazzup-pilot-message-help'}
                      aria-invalid={tooLong || undefined}
                      className="wazzup-thin-scrollbar order-first block max-h-40 min-h-10 w-full min-w-0 resize-none rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-[15px] leading-6 text-slate-900 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 read-only:opacity-70 sm:order-none sm:flex-1" />
                <div className="ml-auto flex shrink-0 items-center gap-0.5">
                    {[['ru', 'Перевести на русский', 'RU'], ['kk', 'Қазақшаға аудару', 'ҚАЗ']].map(([action, label, language]) =>
                        <button key={action} type="button" disabled={assistDisabled} aria-label={label} title={assistHint || label}
                            onClick={() => assist(action)} className="relative flex h-10 w-9 items-start justify-center rounded-lg pt-1.5 text-slate-500 hover:bg-slate-100 hover:text-blue-600 disabled:opacity-35">
                            {assistAction === action ? <Loader2 size={17} className="animate-spin" /> : <Languages size={17} />}
                            <span aria-hidden="true" className="absolute bottom-0.5 text-[8px] font-semibold leading-3">{language}</span>
                        </button>)}
                    <button type="button" disabled={assistDisabled} aria-label="Перефразировать" title={assistHint || 'Перефразировать: вежливо и грамотно'}
                        onClick={() => assist('rewrite')} className="flex h-10 w-9 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-blue-600 disabled:opacity-35">
                        {assistAction === 'rewrite' ? <Loader2 size={18} className="animate-spin" /> : <Sparkles size={18} />}
                    </button>
                    <button type="submit" disabled={state === 'sending' || Boolean(assistAction) || !canSubmit}
                            aria-label={state === 'sending' ? 'Ожидаем ответ…' : state === 'unknown' ? 'Проверить отправку' : 'Отправить'}
                            title={state === 'unknown' ? 'Проверить отправку' : 'Отправить · Enter'}
                            className="ml-1 inline-flex h-10 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-blue-500 px-3 text-[12px] font-semibold text-white transition hover:bg-blue-600 disabled:cursor-not-allowed disabled:opacity-50">
                        {state === 'sending' ? <Loader2 size={18} className="animate-spin" />
                            : state === 'unknown' ? <RefreshCw size={18} /> : <Send size={18} />}
                        {state === 'unknown' && <span>Проверить отправку</span>}
                    </button>
                </div>
            </div>
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
                                const remainingText = pendingRef.current?.attachmentId ? text : '';
                                pendingRef.current = null;
                                attachmentRef.current = null;
                                setAttachment(null);
                                writeDraft(storageKey, remainingText, null);
                                setText(remainingText); setPreview(''); setError(''); setState('idle');
                                setUndoText(null);
                                onCancelReply?.();
                                setCheckedConversation(false);
                            }}
                            className="mt-2 font-semibold text-blue-700 hover:underline disabled:opacity-50">
                        Начать новое сообщение
                    </button>
                </div>
            )}
            <div className="mt-1 flex min-h-4 items-center justify-between gap-2">
                <div id="wazzup-pilot-message-help" className="text-[11px] text-slate-400" aria-live="polite">
                    {state === 'sent' ? (
                        <span className="inline-flex items-center gap-1 text-emerald-600"><Check size={12} /> Отправлено в Wazzup</span>
                    ) : <span className={tooLong ? 'text-rose-600' : ''}>{length > 0 && `${length}/${maxLength} · `}Enter — отправить · Shift+Enter — новая строка</span>}
                </div>
                {undoText !== null && !locked && !hasFile && <button type="button" aria-label="Вернуть исходный текст" title="Вернуть исходный текст"
                    onClick={() => updateText(undoText)} className="shrink-0 rounded p-1 text-slate-500 hover:bg-slate-100"><Undo2 size={14} /></button>}
            </div>
            </div>
        </form>
    );
}

export default function ChatPilotComposer(props) {
    // The parent may replace a selected chat without remounting this component.
    // Isolate its draft and in-flight identity even in that case.
    return <ChatPilotDraft key={pilotChatKey('op', props.chat)} {...props} />;
}
