import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { AlertCircle, Clock3, FileText, Languages, Loader2, Paperclip, Reply, Send, Sparkles, Undo2, X } from 'lucide-react';
import { newClientMessageId, pilotChatKey, pilotDraftStorageKey } from './chatPilot.js';
import ChatComposerTools from './ChatComposerTools';
import useWabaWindowExpired from './useWabaWindowExpired';
import ChatMessageText from './ChatMessageText';
import { UPLOAD_ACCEPT, uploadedAttachment, uploadFileError, uploadSizeLabel } from './chatUploads.js';

const readDraft = (key) => {
    try {
        const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
        if (!saved || typeof saved.text !== 'string') return { text: '', preview: '', attachment: null };
        // Неясную отправку прежнего поля ввода (pending) забирает очередь со
        // СВОИМ id (sendQueue.js); её текст не должен стать новым черновиком —
        // Enter отправил бы его второй раз под новым id.
        const legacy = saved.pending?.clientMessageId ? saved.pending : null;
        return {
            text: legacy && !legacy.attachmentId ? '' : saved.text,
            preview: !legacy && typeof saved.preview === 'string' ? saved.preview : '',
            attachment: legacy?.attachmentId ? null : uploadedAttachment(saved.attachment),
        };
    } catch {
        return { text: '', preview: '', attachment: null };
    }
};

const writeDraft = (key, text, preview = '', replyTo = null, attachment = null) => {
    try {
        if (text || attachment) sessionStorage.setItem(key, JSON.stringify({ text, pending: null, preview, replyTo,
            attachment: uploadedAttachment(attachment) }));
        else sessionStorage.removeItem(key);
    } catch { /* Private browsing can disable storage; the mounted draft still works. */ }
};

/* Поле ответа. Отправка отдаёт сообщение очереди (onSend → sendQueue.js) и
 * сразу освобождает поле: пузырь с часами уже в ленте, а следующее сообщение
 * можно набирать не дожидаясь ни сервера, ни Wazzup. */
// Второй Enter сразу после отправки файла — случайный (двойное нажатие,
// автоповтор клавиши): он не должен отправить черновик, который поле обещало
// сохранить. Осознанный Enter через секунду отправит его как обычно.
const FILE_SUBMIT_GUARD_MS = 1000;

function ChatPilotDraft({ apiBaseUrl, headers, chat, channelTransport, lastInboundAt, onSend, onSent, replyTo,
    onCancelReply, restore = null, onRestored, authorName = '', maxLength = 4096 }) {
    const storageKey = pilotDraftStorageKey(chat);
    const [saved] = useState(() => readDraft(storageKey));
    const [text, setText] = useState(saved.text);
    const [preview, setPreview] = useState(saved.preview);
    const [attachment, setAttachment] = useState(saved.attachment);
    const [uploading, setUploading] = useState(null);
    // Refs carry the current draft: two Enter presses in one tick both see the
    // same rendered closure, and only the first may take the text.
    const textRef = useRef(saved.text);
    const previewRef = useRef(saved.preview);
    const attachmentRef = useRef(saved.attachment);
    const uploadRef = useRef(null);
    const fileInputRef = useRef(null);
    const textareaRef = useRef(null);
    const composerKeyDownRef = useRef(null);
    const selectionRef = useRef({ start: saved.text.length, end: saved.text.length });
    const [error, setError] = useState('');
    const [assistAction, setAssistAction] = useState(null);
    const [undoText, setUndoText] = useState(null);
    const assistRef = useRef(null);
    const editVersionRef = useRef(0);
    const mountedRef = useRef(true);
    const fileSentAtRef = useRef(0);
    const restoredRef = useRef(null);

    useEffect(() => {
        mountedRef.current = true;
        return () => { mountedRef.current = false; assistRef.current?.abort(); uploadRef.current?.abort(); };
    }, []);

    /* «Изменить» у отклонённого Wazzup сообщения возвращает его текст сюда.
       Набранное с тех пор не теряется: возвращённый текст встаёт перед ним. */
    useEffect(() => {
        if (!restore?.id || restoredRef.current === restore.id || typeof restore.text !== 'string') return;
        restoredRef.current = restore.id;
        cancelAssist();
        const current = previewRef.current ? '' : textRef.current;
        const next = current ? `${restore.text}\n${current}` : restore.text;
        setDraft(next);
        setError('');
        writeDraft(storageKey, next, '', replyTo, attachmentRef.current);
        selectionRef.current = { start: restore.text.length, end: restore.text.length };
        textareaRef.current?.focus();
        // The text now lives in the draft; a remounted field must not insert it twice.
        onRestored?.(restore.id);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [restore?.id]);

    useEffect(() => {
        const field = textareaRef.current;
        if (!field) return;
        field.style.height = 'auto';
        field.style.height = `${Math.min(field.scrollHeight, 160)}px`;
        field.style.overflowY = field.scrollHeight > 160 ? 'auto' : 'hidden';
    }, [text, preview]);

    useEffect(() => {
        if (replyTo?.messageId) textareaRef.current?.focus();
    }, [replyTo?.messageId]);

    const hasFile = Boolean(attachment || uploading);
    const length = Array.from(text).length;
    const tooLong = length > maxLength;
    const canSubmit = !uploading && (Boolean(attachment) || (Boolean(text.trim()) && !tooLong));
    const assistDisabled = hasFile || Boolean(preview) || !text.trim() || tooLong || Boolean(assistAction);
    const assistHint = preview ? 'Одобренный шаблон нельзя изменять. Уберите шаблон, чтобы написать обычное сообщение.' : '';
    const showWabaHint = useWabaWindowExpired(channelTransport === 'wapi', lastInboundAt);

    const setDraft = (value, nextPreview = '') => {
        textRef.current = value;
        previewRef.current = nextPreview;
        setText(value);
        setPreview(nextPreview);
    };

    const cancelAssist = () => {
        editVersionRef.current += 1;
        assistRef.current?.abort();
        assistRef.current = null;
        setAssistAction(null);
        setUndoText(null);
    };

    const updateText = (value) => {
        if (attachmentRef.current || uploadRef.current) return;
        cancelAssist();
        setDraft(value);
        setError('');
        writeDraft(storageKey, value, '', replyTo);
    };

    const removeAttachment = () => {
        uploadRef.current?.abort();
        uploadRef.current = null;
        attachmentRef.current = null;
        setUploading(null); setAttachment(null); setError('');
        writeDraft(storageKey, textRef.current, previewRef.current, replyTo);
    };

    const chooseFile = async (event) => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (!file || previewRef.current || attachmentRef.current || uploadRef.current) return;
        const validationError = uploadFileError(file);
        if (validationError) { setError(validationError); return; }
        const controller = new AbortController();
        uploadRef.current = controller;
        cancelAssist();
        setUploading({ name: file.name, size: file.size });
        setError('');
        try {
            const body = new FormData();
            body.append('account', 'op');
            body.append('channelId', chat.channelId);
            body.append('chatId', chat.chatId);
            body.append('clientUploadId', newClientMessageId());
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
            writeDraft(storageKey, textRef.current, '', replyTo, ready);
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
        if (assistDisabled || assistRef.current || attachmentRef.current || uploadRef.current) return;
        const controller = new AbortController();
        const version = editVersionRef.current;
        const original = textRef.current;
        assistRef.current = controller;
        setAssistAction(action);
        setError('');
        try {
            const { data } = await axios.post(`${apiBaseUrl}/api/wazzup/pilot/assist`, {
                account: 'op', action, text: original,
            }, { headers: typeof headers === 'function' ? headers() : headers, signal: controller.signal, timeout: 25000 });
            // Editing, changing chat or sending must never be overwritten by an old AI result.
            if (!mountedRef.current || controller.signal.aborted || editVersionRef.current !== version) return;
            if (typeof data?.text !== 'string' || !data.text.trim() || Array.from(data.text).length > maxLength) {
                setError('Не удалось получить готовый текст. Исходное сообщение сохранено.');
                return;
            }
            setDraft(data.text);
            setUndoText(original);
            writeDraft(storageKey, data.text, '', replyTo);
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

    const submit = (event) => {
        event?.preventDefault();
        if (assistRef.current || uploadRef.current || !chat?.channelId || !chat?.chatId) return;
        if (Date.now() - fileSentAtRef.current < FILE_SUBMIT_GUARD_MS) return;
        const file = attachmentRef.current;
        const value = textRef.current;
        const shown = previewRef.current;
        if (file) {
            if (file.expiresAt && Date.parse(file.expiresAt) <= Date.now()) {
                setError('Срок хранения файла истёк. Уберите вложение и выберите файл ещё раз.');
                return;
            }
        } else if (!value.trim() || Array.from(value).length > maxLength) return;
        const message = {
            clientMessageId: newClientMessageId(), account: 'op', channelId: chat.channelId, chatId: chat.chatId,
            // A file goes without a caption (Wazzup does not accept both); the
            // typed text stays a draft. A template is sent as its Wazzup code.
            text: file ? '' : value.trim(),
            displayText: file ? file.name : shown || value.trim(),
            ...(file ? { attachmentId: file.id, attachment: { name: file.name, size: file.size, mime: file.mime } } : {}),
            ...(replyTo?.messageId ? { replyToMessageId: replyTo.messageId, reply: {
                text: replyTo.text || '', authorName: replyTo.authorName || (replyTo.isEcho ? 'Вы' : 'Собеседник') } } : {}),
            authorName,
        };
        // The draft is cleared only once the queue owns the message: a queue
        // that refused it (no signed-in owner yet) must not swallow the text.
        if (!onSend?.(message)) {
            setError('Сообщение не отправлено: обновите страницу и попробуйте ещё раз. Текст сохранён.');
            return;
        }
        if (file) {
            attachmentRef.current = null;
            fileSentAtRef.current = Date.now();
            setAttachment(null);
            writeDraft(storageKey, value, shown, null);
        } else {
            setDraft('');
            selectionRef.current = { start: 0, end: 0 };
            writeDraft(storageKey, '');
        }
        setUndoText(null);
        setError('');
        if (replyTo?.messageId) {
            try { onCancelReply?.(replyTo.messageId); } catch { /* The message is already queued. */ }
        }
        try { onSent?.(message); } catch { /* The message is already queued. */ }
        textareaRef.current?.focus();
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
            {replyTo && <div className="mb-2 flex items-center gap-2 rounded-lg border-l-2 border-blue-400 bg-blue-50 px-3 py-2 text-sm"
                data-testid="wazzup-reply-preview">
                <Reply size={17} className="shrink-0 text-blue-500" />
                <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-semibold text-blue-600">{replyTo.authorName || (replyTo.isEcho ? 'Вы' : 'Собеседник')}</div>
                    <div className="truncate text-slate-600"><ChatMessageText text={replyTo.text || 'Вложение'} /></div>
                </div>
                <button type="button" aria-label="Отменить ответ" title="Отменить ответ"
                    onClick={() => onCancelReply?.()} className="rounded p-1 text-slate-500 hover:bg-blue-100 disabled:opacity-40"><X size={17} /></button>
            </div>}
            {preview && <div className="mb-1 flex items-center justify-between text-xs text-slate-500">
                <span>Одобренный шаблон Wazzup</span>
                <button type="button" onClick={() => updateText('')} className="text-blue-600">Убрать шаблон</button>
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
                <button type="button" onClick={removeAttachment} aria-label="Убрать файл" title="Убрать файл"
                    className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-200 disabled:opacity-40"><X size={17} /></button>
            </div>}
            <div className="flex flex-wrap items-end gap-1.5 sm:flex-nowrap">
                <input ref={fileInputRef} type="file" accept={UPLOAD_ACCEPT} onChange={chooseFile} tabIndex={-1}
                    disabled={Boolean(preview) || hasFile} className="hidden" aria-label="Выбрать файл" />
                <button type="button" disabled={Boolean(preview) || hasFile} aria-label="Прикрепить файл" title={preview ? 'Уберите шаблон, чтобы прикрепить файл' : 'Прикрепить файл'}
                    onClick={() => {
                        if (!previewRef.current && !attachmentRef.current && !uploadRef.current) fileInputRef.current?.click();
                    }} className="flex h-10 w-9 shrink-0 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-blue-600 disabled:opacity-35">
                    <Paperclip size={20} />
                </button>
                <ChatComposerTools apiBaseUrl={apiBaseUrl} headers={headers} channelId={chat.channelId}
                    composerRef={textareaRef} composerKeyDownRef={composerKeyDownRef}
                    locked={hasFile} emojiDisabled={Boolean(preview) || hasFile} slash={!preview && !hasFile && /^\/[^\n]*$/.test(text) ? text.slice(1) : null}
                    onChoose={({ text: next, preview: nextPreview }) => {
                        if (attachmentRef.current || uploadRef.current) return;
                        cancelAssist();
                        setDraft(next, nextPreview); setError('');
                        writeDraft(storageKey, next, nextPreview, replyTo);
                        selectionRef.current = { start: next.length, end: next.length };
                        textareaRef.current?.focus();
                    }}
                    onEmoji={(emoji) => {
                        if (previewRef.current || attachmentRef.current || uploadRef.current) return;
                        const current = textRef.current;
                        const { start, end } = selectionRef.current;
                        const next = current.slice(0, start) + emoji + current.slice(end);
                        updateText(next);
                        const position = start + emoji.length;
                        selectionRef.current = { start: position, end: position };
                        requestAnimationFrame(() => { textareaRef.current?.focus(); textareaRef.current?.setSelectionRange(position, position); });
                    }} />
            <textarea ref={textareaRef} id="wazzup-pilot-message" rows={1} value={preview || text} readOnly={Boolean(preview) || hasFile}
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
                    <button type="submit" disabled={Boolean(assistAction) || !canSubmit}
                            aria-label="Отправить" title="Отправить · Enter"
                            className="ml-1 inline-flex h-10 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-blue-500 px-3 text-[12px] font-semibold text-white transition hover:bg-blue-600 disabled:cursor-not-allowed disabled:opacity-50">
                        <Send size={18} />
                    </button>
                </div>
            </div>
            {error && (
                <div role="alert" className="mt-2 flex items-start gap-1.5 text-[12px] text-amber-700">
                    <AlertCircle size={14} className="mt-0.5 shrink-0" />
                    <span>{error}</span>
                </div>
            )}
            <div className="mt-1 flex min-h-4 items-center justify-between gap-2">
                <div id="wazzup-pilot-message-help" className="text-[11px] text-slate-400" aria-live="polite">
                    <span className={tooLong ? 'text-rose-600' : ''}>{length > 0 && `${length}/${maxLength} · `}Enter — отправить · Shift+Enter — новая строка</span>
                </div>
                {undoText !== null && !hasFile && <button type="button" aria-label="Вернуть исходный текст" title="Вернуть исходный текст"
                    onClick={() => updateText(undoText)} className="shrink-0 rounded p-1 text-slate-500 hover:bg-slate-100"><Undo2 size={14} /></button>}
            </div>
            </div>
        </form>
    );
}

export default function ChatPilotComposer(props) {
    // The parent may replace a selected chat without remounting this component.
    // Isolate its draft even in that case.
    return <ChatPilotDraft key={pilotChatKey('op', props.chat)} {...props} />;
}
