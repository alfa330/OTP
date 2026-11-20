import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
    AlertCircle, ArrowLeft, Download, FileText, IdCard, Loader2, MessageSquare, Paperclip, Pencil, Plus, Reply,
    Search, Send, Slash, Smile, Trash2, UserRound, X,
} from 'lucide-react';
import { IosLightbox, IosModal, IosSegmented, iosInput } from '../ui/ios';
import { startFileDownload } from '../../utils/fileDownload';
import MessageDeliveryStatus from '../wazzup/MessageDeliveryStatus';
import ChatMessageText from '../wazzup/ChatMessageText';
import ChatMessageImage from '../wazzup/ChatMessageImage';
import ChatAudioPlayer from '../wazzup/ChatAudioPlayer';
import { loadChatEmojiPicker } from '../wazzup/chatEmojiLoading';
import '../wazzup/chatThread.css';
import './hiringChats.css';
import { errorOf, STAGE_LABELS, SUBSTAGE_LABELS } from './hiringMeta';
import {
    BLOCK_ACTIONS, MAX_FILE, MAX_TEXT, chatInitials, fileSize, insertAt, lastIncomingId, listTime, localStamp, matchTemplates,
    mergeMessages, newClientId, pendingStatus, pendingTail, showAuthor, slashQuery, tickStatus, timeOf, withDays,
} from './hiringChats';
import { ConfirmDialog, FIELD, Label, Pill, TOOLBAR_BTN, TOOLBAR_BTN_PRIMARY } from './hiringUi';

/*
 * Окно «Чаты» раздела «Воронка найма» (владелец 19.11.2026: «рекрутер разбирает таргетные отклики в приложении в
 * ватсапе, а мы собирались переводить обработку в наш сервис… сделать интерфейс обработки чата ватсапа прямо как в
 * разделе чаты ОП»). Тот же вид, что у «Чатов ОП»: слева чаты (ждущие ответа сверху, оранжевый счётчик), справа —
 * лента «как в мессенджере» и поле ответа: шаблоны по «/», эмодзи, файл, ответ с цитатой, галочки доставки.
 *
 * Отвечает закреплённый рекрутер: отклик из пула сначала «Взять в работу» (карточку ведёт тот, кто взял).
 * Сообщение уходит через сервис на шлюзе (hiring/wa_chat.py → wa_bridge/sender.go): в ленте оно сначала с одной
 * серой галочкой, потом — «доставлено» и «прочитано». Ошибку отправки видно под сообщением с «Повторить».
 *
 * Ряды — сеткой через style, а не flex с gap: оболочка телефона (mobile-shell.css) переносит такие ряды и ставит
 * «items-start + flex-1» колонкой. Окна шаблонов — вне поля ответа (у предка с backdrop-filter окно с position:fixed
 * встаёт не по экрану).
 */

const LIST_POLL_MS = 5000;
const THREAD_POLL_MS = 3500;

const MODES = [
    { value: 'all', label: 'Все' },
    { value: 'mine', label: 'Мои' },
    { value: 'pool', label: 'Отклики' },
];

const KIND_LABELS = { image: 'Фото', video: 'Видео', audio: 'Аудио', voice: 'Голосовое', document: 'Документ',
    sticker: 'Стикер', location: 'Геопозиция', contact: 'Контакт', other: 'Сообщение' };

const ROW = (columns, extra = {}) => ({ display: 'grid', gridTemplateColumns: columns, alignItems: 'center', ...extra });

const visible = () => typeof document === 'undefined' || document.visibilityState !== 'hidden';

// Буквы имени — только буквы и цифры: имя из одного эмодзи («💌») давало половинку символа «�».
const Avatar = ({ name, size = 'h-11 w-11' }) => {
    const letters = chatInitials(name);
    return (
        <div className={`${size} flex shrink-0 items-center justify-center rounded-full bg-slate-200 text-[13px] font-medium text-slate-600`}>
            {letters || <UserRound size={17} aria-hidden="true" />}
        </div>
    );
};

const stageText = (chat) => {
    const label = chat?.stage_label || STAGE_LABELS[chat?.stage];
    if (!label) return '';
    const sub = chat.substage && SUBSTAGE_LABELS[chat.substage];
    return sub ? `${label} · ${sub}` : label;
};

// ─── Список чатов ─────────────────────────────────────────────────────────

const ChatRow = ({ chat, selected, onOpen }) => (
    <div className="relative">
        <button type="button" onClick={() => onOpen(chat)} aria-current={selected ? 'true' : undefined}
                className={`block min-h-[72px] w-full py-3 pl-4 pr-3 text-left transition ${selected ? 'bg-slate-100' : 'hover:bg-slate-50'}`}>
            <div style={ROW('auto minmax(0, 1fr)', { columnGap: 12 })}>
                <Avatar name={chat.title} />
                <div className="min-w-0">
                    <div style={ROW('minmax(0, 1fr) auto', { columnGap: 8, alignItems: 'baseline' })}>
                        <span className="truncate text-[15px] font-medium leading-5 text-slate-900">{chat.title}</span>
                        <span className="text-[11px] tabular-nums text-slate-400">{listTime(chat.last_at)}</span>
                    </div>
                    <div className="mt-0.5 text-[13px] leading-5 text-slate-500" style={ROW('minmax(0, 1fr) auto', { columnGap: 4 })}>
                        <span className="truncate"><ChatMessageText text={chat.last?.text || ''} links={false} /></span>
                        <span style={ROW('auto auto', { columnGap: 4 })}>
                            {chat.last?.from_me ? <MessageDeliveryStatus status={tickStatus(chat.last.status)} size={15} /> : <span />}
                            {chat.waiting > 0 ? (
                                <span className="rounded-full bg-orange-600 px-1.5 text-[11px] font-semibold text-white" title="Ждут ответа">
                                    {chat.waiting > 99 ? '99+' : chat.waiting}
                                </span>
                            ) : <span />}
                        </span>
                    </div>
                    <div className="mt-0.5 truncate text-[11.5px] text-slate-400">
                        {[stageText(chat), chat.recruiter_name].filter(Boolean).join(' · ')}
                    </div>
                </div>
            </div>
        </button>
        <div aria-hidden="true" className="pointer-events-none absolute bottom-0 left-[72px] right-0 border-b border-slate-200" />
    </div>
);

// ─── Лента ────────────────────────────────────────────────────────────────

const Transcript = ({ transcript }) => {
    if (!transcript) return null;
    if (transcript.status === 'ready' && transcript.text) {
        return <div className="mt-1 whitespace-pre-wrap break-words text-[13.5px] leading-snug text-slate-600">{transcript.text}</div>;
    }
    if (transcript.status === 'pending' || transcript.status === 'working') {
        return <div className="mt-1 text-[11.5px] text-slate-400">Расшифровка…</div>;
    }
    return null;
};

const MediaView = ({ message, onZoom }) => {
    const media = message.media || {};
    const label = KIND_LABELS[message.kind] || 'Файл';
    if (media.url && media.inline && message.kind === 'image') {
        return <ChatMessageImage src={media.url} label={label} onOpen={() => onZoom(media.url)} />;
    }
    if (media.url && media.inline && message.kind === 'video') {
        return <video controls preload="metadata" src={media.url} className="max-h-72 w-auto max-w-full rounded-xl" />;
    }
    if (media.url && media.inline && (message.kind === 'voice' || message.kind === 'audio')) {
        return <><ChatAudioPlayer src={media.url} /><Transcript transcript={media.transcript} /></>;
    }
    if (media.url) {
        return (
            <button type="button" onClick={() => startFileDownload(media.url)}
                    className="max-w-full rounded-lg bg-slate-900/[0.05] px-2.5 py-2 text-left text-[14px] font-medium text-slate-700 transition hover:bg-slate-900/[0.08]"
                    style={ROW('auto minmax(0, 1fr) auto', { columnGap: 8 })}>
                <FileText size={20} className="text-slate-500" />
                <span className="min-w-0">
                    <span className="block truncate">{media.file_name || label}</span>
                    {media.size ? <span className="block text-[11px] font-normal tabular-nums text-slate-500">{fileSize(media.size)}</span> : null}
                </span>
                <Download size={15} className="text-slate-400" />
            </button>
        );
    }
    const note = media.state === 'loading' ? 'загружается…'
        : media.state === 'too_large' ? 'больше 25 МБ — откройте на телефоне'
            : media.state === 'failed' ? 'не сохранился — откройте на телефоне' : '';
    return (
        <span className="inline-block rounded-lg bg-slate-900/[0.05] px-2 py-1 text-[12.5px] font-medium text-slate-600">
            <FileText size={13} className="mr-1 inline align-[-2px]" />{label}{note ? <span className="font-normal text-slate-400"> · {note}</span> : null}
        </span>
    );
};

const QuoteBox = ({ quote, out }) => {
    if (!quote) return null;
    return (
        <div className={`mb-2 overflow-hidden rounded-lg border-l-[3px] px-2.5 py-1.5 text-[13px] ${
            out ? 'border-emerald-600 bg-black/5 text-emerald-950' : 'border-blue-400 bg-slate-100 text-slate-600'}`}>
            <span className="block truncate font-semibold">{quote.from_me ? (quote.author || 'Мы') : 'Кандидат'}</span>
            <span className="line-clamp-2 whitespace-pre-wrap break-words"><ChatMessageText text={quote.text} links={false} /></span>
        </div>
    );
};

const Bubble = React.memo(function Bubble({ message, quote, showName, onReply, onZoom, pending, onRetry, onDiscard }) {
    const out = Boolean(message.from_me);
    const hasMedia = message.kind && message.kind !== 'text';
    const status = pending ? pendingStatus(message) : tickStatus(message.status);
    const canFix = pending && message.status === 'failed' && (message.mine || !message.outbox_id);
    const replyButton = onReply && !message.deleted && !pending ? (
        <button type="button" onClick={() => onReply(message)} aria-label="Ответить на сообщение" title="Ответить на сообщение"
                className="wazzup-message-reply mt-1 shrink-0 rounded-full p-1.5 text-slate-500 hover:bg-slate-200/70">
            <Reply size={17} />
        </button>
    ) : null;
    const body = (
        <div className={`flex min-w-0 flex-col ${out ? 'items-end' : 'items-start'}`}>
            {out && showName && (
                <div className="mb-1 max-w-full truncate px-1 text-[11px] font-medium leading-4 text-slate-500">{message.author}</div>
            )}
            <div className={`min-w-0 max-w-full rounded-2xl px-3 py-2 text-[16px] leading-[1.45] shadow-[0_1px_1px_rgba(15,23,42,0.05)] ${
                out ? 'rounded-br-md bg-[#dcf8c6] text-slate-900' : 'rounded-bl-md bg-white text-slate-900 ring-1 ring-slate-200/60'
            } ${message.deleted ? 'opacity-70' : ''}`}>
                {message.ad_label && !out && (
                    <div className="mb-1 truncate text-[11.5px] font-medium text-blue-700" title={message.ad_label}>{message.ad_label}</div>
                )}
                <QuoteBox quote={quote} out={out} />
                {message.deleted ? (
                    <span className="italic text-slate-500">Сообщение удалено</span>
                ) : (
                    <>
                        {hasMedia && !pending && <div className={message.text ? 'mb-1' : ''}><MediaView message={message} onZoom={onZoom} /></div>}
                        {hasMedia && pending && (
                            <div className={message.text ? 'mb-1' : ''}>
                                <span className="inline-block max-w-full truncate rounded-lg bg-black/5 px-2 py-1 text-[12.5px] font-medium text-slate-700">
                                    <Paperclip size={13} className="mr-1 inline align-[-2px]" />{message.file_name || 'Файл'}
                                    {message.size ? <span className="font-normal tabular-nums text-slate-500"> · {fileSize(message.size)}</span> : null}
                                </span>
                            </div>
                        )}
                        {message.text && <div className="whitespace-pre-wrap break-words"><ChatMessageText text={message.text} /></div>}
                    </>
                )}
                <div className="mt-0.5 text-right text-[11px] tabular-nums text-slate-500">
                    {message.edited && !message.deleted ? 'изменено · ' : ''}{timeOf(message.at)}
                    {out && <span className="ml-1 inline-block align-[-3px]"><MessageDeliveryStatus status={status} /></span>}
                </div>
            </div>
            {pending && message.status === 'failed' && (
                <div role="alert" className="mt-1 max-w-full px-1 text-right text-[12px] leading-4">
                    <span className="text-rose-600">{message.error || 'Не отправлено'}</span>
                    {canFix && onRetry && <button type="button" onClick={() => onRetry(message)} className="ml-2.5 font-semibold text-blue-600 hover:underline">Повторить</button>}
                    {canFix && onDiscard && <button type="button" onClick={() => onDiscard(message)} className="ml-2.5 font-semibold text-slate-500 hover:underline">Убрать</button>}
                </div>
            )}
        </div>
    );
    return (
        // Ряд — flex по ширине ленты: «не шире 85 %» считается от неё. В сетке с колонкой «по содержимому» процент
        // считался от самой колонки, и пузырь сжимался до буквы в строке («ку/да», «а/х/а»). Без gap и без flex-1 —
        // оболочка телефона такой ряд не переносит и не ставит колонкой (mobile-shell.css).
        <div data-message-id={message.id || message.key}
             className={`wazzup-message-row flex items-start px-3 sm:px-4 ${out ? 'justify-end' : 'justify-start'}`}>
            {out && replyButton}
            <div className={`min-w-0 max-w-[85%] sm:max-w-[min(78%,720px)] ${out ? 'ml-1.5' : 'mr-1.5'}`}>{body}</div>
            {!out && replyButton}
        </div>
    );
});

// ─── Шаблоны ──────────────────────────────────────────────────────────────

const TemplateDialog = ({ open, item, onClose, onSave, busy }) => {
    const [title, setTitle] = useState('');
    const [body, setBody] = useState('');
    useEffect(() => {
        if (open) {
            setTitle(item?.title || '');
            setBody(item?.body || '');
        }
    }, [open, item]);
    return (
        <IosModal open={open} onClose={onClose} title={item?.id ? 'Шаблон ответа' : 'Новый шаблон ответа'} maxWidth="max-w-xl"
                  footer={(
                      <div className="flex justify-end gap-2">
                          <button type="button" className={TOOLBAR_BTN} onClick={onClose}>Отмена</button>
                          <button type="button" className={TOOLBAR_BTN_PRIMARY} disabled={busy || !title.trim() || !body.trim()}
                                  onClick={() => onSave({ title, body })}>Сохранить</button>
                      </div>
                  )}>
            <div className="space-y-3">
                <div>
                    <Label htmlFor="wa-template-title">Название</Label>
                    <input id="wa-template-title" className={FIELD} value={title} maxLength={80} placeholder="Приглашение на собеседование"
                           onChange={(event) => setTitle(event.target.value)} />
                </div>
                <div>
                    <Label htmlFor="wa-template-body" hint="Общий для всей команды найма. В чате текст встаёт в поле ответа — дату и время поправьте перед отправкой.">Текст</Label>
                    <textarea id="wa-template-body" className={`${FIELD} min-h-[180px] resize-y leading-snug`} value={body} maxLength={MAX_TEXT}
                              onChange={(event) => setBody(event.target.value)} />
                </div>
            </div>
        </IosModal>
    );
};

/* Список шаблонов — над полем ответа; окна правки и удаления — у вкладки (onEdit/onDelete), не внутри поля. */
const TemplatesPanel = ({ items, query, onChoose, onClose, canEdit, onEdit, onDelete }) => {
    const [search, setSearch] = useState('');
    const shown = matchTemplates(items, query !== null ? query : search);
    return (
        <div className="absolute bottom-full left-0 right-0 z-30 mb-2 overflow-hidden rounded-2xl bg-white shadow-[0_10px_40px_rgba(15,23,42,0.18)] ring-1 ring-slate-200/80">
            <div className="border-b border-slate-100 px-3 py-2" style={ROW('auto minmax(0, 1fr) auto auto', { columnGap: 8 })}>
                <span className="text-[13px] font-semibold text-slate-900">Шаблоны</span>
                {query === null ? (
                    <input autoFocus value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Поиск"
                           className="min-w-0 rounded-lg bg-slate-100 px-2.5 py-1.5 text-[13px] focus:outline-none focus:ring-2 focus:ring-blue-500/60" />
                ) : <span className="min-w-0 truncate text-[12px] text-slate-400">Набирайте после «/» — Enter вставит первый</span>}
                {canEdit ? (
                    <button type="button" onClick={() => onEdit({})} className="rounded-lg px-2 py-1 text-[12.5px] font-semibold text-blue-600 hover:bg-blue-50">
                        <Plus size={14} className="mr-0.5 inline align-[-2px]" />Добавить
                    </button>
                ) : <span />}
                <button type="button" onClick={onClose} aria-label="Закрыть шаблоны" className="rounded-full p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"><X size={15} /></button>
            </div>
            <div className="wazzup-scrollbar max-h-72 overflow-y-auto py-1">
                {items === null && <div className="px-3 py-4 text-[13px] text-slate-400"><Loader2 size={14} className="mr-2 inline animate-spin" />Загрузка…</div>}
                {items !== null && shown.length === 0 && (
                    <div className="px-3 py-4 text-[13px] text-slate-400">{items.length ? 'Ничего не нашлось' : 'Шаблонов пока нет — добавьте частые ответы: приглашение, адрес, описание вакансии'}</div>
                )}
                {shown.map((item, index) => (
                    <div key={item.id} className="group px-1.5" style={ROW(canEdit ? 'minmax(0, 1fr) auto' : 'minmax(0, 1fr)', { alignItems: 'start', columnGap: 4 })}>
                        <button type="button" onClick={() => onChoose(item)}
                                className={`min-w-0 rounded-xl px-2.5 py-2 text-left transition hover:bg-slate-100 ${query !== null && index === 0 ? 'bg-slate-100' : ''}`}>
                            <span className="block truncate text-[13.5px] font-medium text-slate-900">{item.title}</span>
                            <span className="line-clamp-2 whitespace-pre-wrap text-[12.5px] leading-snug text-slate-500">{item.body}</span>
                        </button>
                        {canEdit && (
                            <span className="mt-2 whitespace-nowrap [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100">
                                <button type="button" aria-label="Изменить шаблон" onClick={() => onEdit(item)} className="rounded-full p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"><Pencil size={14} /></button>
                                <button type="button" aria-label="Удалить шаблон" onClick={() => onDelete(item)} className="rounded-full p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600"><Trash2 size={14} /></button>
                            </span>
                        )}
                    </div>
                ))}
            </div>
        </div>
    );
};

// ─── Поле ответа ──────────────────────────────────────────────────────────

const Composer = ({ chatKey, replyTo, onCancelReply, onSend, templates, onTemplatesOpen, canEditTemplates,
    onEditTemplate, onDeleteTemplate, toast }) => {
    const [text, setText] = useState('');
    const [file, setFile] = useState(null);
    const [panel, setPanel] = useState(null);          // 'templates' | 'emoji' | null
    const [EmojiPicker, setEmojiPicker] = useState(null);
    const inputRef = useRef(null);
    const fileRef = useRef(null);
    const slash = slashQuery(text);
    const templatesOpen = panel === 'templates' || slash !== null;

    useEffect(() => { setText(''); setFile(null); setPanel(null); }, [chatKey]);
    useEffect(() => { if (templatesOpen) onTemplatesOpen(); }, [templatesOpen, onTemplatesOpen]);
    useEffect(() => {
        if (panel !== 'emoji' || EmojiPicker) return undefined;
        let alive = true;
        loadChatEmojiPicker().then((module) => { if (alive) setEmojiPicker(() => module.default); }).catch(() => {});
        return () => { alive = false; };
    }, [panel, EmojiPicker]);
    // Высота поля — по тексту, до шести строк.
    useLayoutEffect(() => {
        const node = inputRef.current;
        if (!node) return;
        node.style.height = 'auto';
        node.style.height = `${Math.min(node.scrollHeight, 168)}px`;
    }, [text]);
    useEffect(() => {
        if (typeof window === 'undefined' || !window.matchMedia?.('(hover: hover)').matches) return;
        inputRef.current?.focus();
    }, [chatKey]);

    const insert = (value) => {
        const node = inputRef.current;
        const next = insertAt(text, node?.selectionStart, node?.selectionEnd, value);
        setText(next.value);
        requestAnimationFrame(() => {
            if (!inputRef.current) return;
            inputRef.current.focus();
            inputRef.current.setSelectionRange(next.caret, next.caret);
        });
    };
    const chooseTemplate = (item) => {
        setPanel(null);
        if (slash !== null) setText(item.body);
        else insert(item.body);
        requestAnimationFrame(() => inputRef.current?.focus());
    };
    const pickFile = (event) => {
        const picked = event.target.files?.[0];
        event.target.value = '';
        if (!picked) return;
        if (picked.size > MAX_FILE) {
            toast('Файл больше 16 МБ — WhatsApp его не примет', 'error');
            return;
        }
        setFile(picked);
    };
    const send = () => {
        const value = text.trim();
        if ((!value && !file) || slash !== null) return;
        if (value.length > MAX_TEXT) {
            toast(`Сообщение длиннее ${MAX_TEXT} знаков`, 'error');
            return;
        }
        onSend({ text: value, file });
        setText('');
        setFile(null);
        setPanel(null);
    };
    const keyDown = (event) => {
        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            if (slash !== null) {
                // «/текст» + Enter — первый подходящий шаблон (как в «Чатах ОП»).
                const first = matchTemplates(templates || [], slash)[0];
                if (first) chooseTemplate(first);
                return;
            }
            send();
        } else if (event.key === 'Escape' && (panel || slash !== null)) {
            event.preventDefault();
            setPanel(null);
            if (slash !== null) setText('');
        }
    };
    const iconButton = (active) => `grid h-10 w-10 place-items-center rounded-full transition ${active ? 'bg-blue-50 text-blue-600' : 'text-slate-500 hover:bg-slate-100'}`;

    return (
        <div className="relative border-t border-slate-200/70 bg-white px-3 py-2.5 sm:px-4">
            {templatesOpen && (
                <TemplatesPanel items={templates} query={slash} canEdit={canEditTemplates} onChoose={chooseTemplate}
                                onEdit={onEditTemplate} onDelete={onDeleteTemplate}
                                onClose={() => { setPanel(null); if (slash !== null) setText(''); }} />
            )}
            {panel === 'emoji' && (
                <div className="absolute bottom-full left-3 z-30 mb-2 overflow-hidden rounded-2xl bg-white shadow-[0_10px_40px_rgba(15,23,42,0.18)] ring-1 ring-slate-200/80">
                    {EmojiPicker ? <EmojiPicker onSelect={(emoji) => insert(emoji)} />
                        : <div className="px-4 py-6 text-[13px] text-slate-400"><Loader2 size={14} className="mr-2 inline animate-spin" />Загрузка…</div>}
                </div>
            )}
            {replyTo && (
                <div className="mb-2 rounded-xl bg-slate-100 px-3 py-2" style={ROW('auto minmax(0, 1fr) auto', { columnGap: 8 })}>
                    <Reply size={16} className="text-blue-500" />
                    <div className="min-w-0 text-[13px]">
                        <div className="font-semibold text-slate-800">{replyTo.from_me ? (replyTo.author || 'Мы') : 'Кандидат'}</div>
                        <div className="truncate text-slate-500">{replyTo.text || KIND_LABELS[replyTo.kind] || 'Сообщение'}</div>
                    </div>
                    <button type="button" onClick={onCancelReply} aria-label="Не отвечать на это сообщение" className="rounded-full p-1 text-slate-400 hover:bg-slate-200 hover:text-slate-600"><X size={15} /></button>
                </div>
            )}
            {file && (
                <div className="mb-2 rounded-xl bg-slate-100 px-3 py-2 text-[13px]" style={ROW('auto minmax(0, 1fr) auto auto', { columnGap: 8 })}>
                    <Paperclip size={15} className="text-slate-500" />
                    <span className="min-w-0 truncate text-slate-800">{file.name}</span>
                    <span className="tabular-nums text-slate-500">{fileSize(file.size)}</span>
                    <button type="button" onClick={() => setFile(null)} aria-label="Убрать файл" className="rounded-full p-1 text-slate-400 hover:bg-slate-200 hover:text-slate-600"><X size={15} /></button>
                </div>
            )}
            <div style={ROW('auto auto auto minmax(0, 1fr) auto', { alignItems: 'end', columnGap: 6 })}>
                <button type="button" onClick={() => setPanel(panel === 'templates' ? null : 'templates')} title="Шаблоны (или «/» в начале поля)"
                        aria-label="Шаблоны" aria-pressed={templatesOpen} className={iconButton(templatesOpen)}>
                    <Slash size={18} />
                </button>
                <button type="button" onClick={() => setPanel(panel === 'emoji' ? null : 'emoji')} aria-label="Эмодзи" aria-pressed={panel === 'emoji'}
                        className={iconButton(panel === 'emoji')}>
                    <Smile size={19} />
                </button>
                <button type="button" onClick={() => fileRef.current?.click()} aria-label="Прикрепить файл" title="Фото, видео или документ до 16 МБ"
                        className={iconButton(false)}>
                    <Paperclip size={18} />
                </button>
                <input ref={fileRef} type="file" className="hidden" onChange={pickFile}
                       accept="image/jpeg,image/png,video/mp4,application/pdf,.doc,.docx,.xls,.xlsx,.txt,audio/*" />
                <textarea ref={inputRef} rows={1} value={text} onChange={(event) => setText(event.target.value)} onKeyDown={keyDown}
                          placeholder={file ? 'Подпись к файлу' : 'Сообщение'} maxLength={MAX_TEXT + 200}
                          className="wazzup-scrollbar min-h-[40px] min-w-0 resize-none rounded-2xl border-0 bg-slate-100 px-3.5 py-2.5 text-[15px] leading-5 text-slate-900 placeholder-slate-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/60" />
                <button type="button" onClick={send} disabled={(!text.trim() && !file) || slash !== null} aria-label="Отправить"
                        className="grid h-10 w-10 place-items-center rounded-full bg-blue-600 text-white shadow-sm transition hover:bg-blue-700 active:scale-95 disabled:bg-slate-200 disabled:text-slate-400">
                    <Send size={17} />
                </button>
            </div>
        </div>
    );
};

// ─── Открытый чат ─────────────────────────────────────────────────────────

const Thread = ({ api, toast, chatKey, onBack, onOpenCard, onChanged, caps }) => {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [messages, setMessages] = useState([]);
    const [quotes, setQuotes] = useState({});
    const [hasMore, setHasMore] = useState(false);
    const [loadingOlder, setLoadingOlder] = useState(false);
    const [local, setLocal] = useState([]);
    const [replyTo, setReplyTo] = useState(null);
    const [zoom, setZoom] = useState(null);
    const [busy, setBusy] = useState('');
    const [templates, setTemplates] = useState(null);
    const [editing, setEditing] = useState(null);
    const [deleting, setDeleting] = useState(null);
    const boxRef = useRef(null);
    const contentRef = useRef(null);
    const atBottom = useRef(true);
    const alive = useRef(true);
    const requestSeq = useRef(0);
    // Подгружены ли страницы раньше: тогда «есть раньше» знает последняя из них, а не опрос свежей страницы.
    const olderLoaded = useRef(false);
    // Номера повтора, которые сервер хоть раз вернул: такая локальная запись больше не показывается.
    const seenClients = useRef(new Set());
    useEffect(() => () => { alive.current = false; }, []);

    const load = useCallback(() => {
        const mine = ++requestSeq.current;
        return api.get(`/wa/chats/${encodeURIComponent(chatKey)}/messages`)
            .then(({ data: payload }) => {
                // Ответ устаревшего запроса (опрос ушёл раньше отправки) свежий не перетирает.
                if (!alive.current || mine !== requestSeq.current) return;
                for (const item of payload.pending || []) if (item.client_id) seenClients.current.add(item.client_id);
                setData(payload);
                setMessages((prev) => mergeMessages(prev, payload.messages));
                setQuotes((prev) => ({ ...prev, ...(payload.quotes || {}) }));
                setError('');
                if (!olderLoaded.current) setHasMore(Boolean(payload.has_more));
            })
            .catch((requestError) => {
                if (alive.current && mine === requestSeq.current) setError(errorOf(requestError, 'Переписка не загрузилась'));
            });
    }, [api, chatKey]);

    useEffect(() => {
        let timer = null;
        let stop = false;
        const tick = () => {
            const run = visible() ? load() : Promise.resolve();
            run.finally(() => { if (!stop) timer = setTimeout(tick, THREAD_POLL_MS); });
        };
        tick();
        return () => { stop = true; clearTimeout(timer); };
    }, [load]);

    const tail = useMemo(() => pendingTail(data?.pending, local, seenClients.current), [data, local]);
    const rows = useMemo(() => withDays([...messages, ...tail.map((item) => ({ ...item, from_me: true, pending: true }))]),
        [messages, tail]);
    const lastIncoming = useMemo(() => lastIncomingId(rows), [rows]);

    // Лента — у последнего сообщения, пока человек не ушёл вверх: и когда пришло новое, и когда растёт поле ответа,
    // плашка цитаты, строка ошибки или догрузилось фото (как useThreadEnd «Чатов ОП»).
    const last = rows.length ? (rows[rows.length - 1].id || rows[rows.length - 1].key) : null;
    useLayoutEffect(() => {
        const node = boxRef.current;
        if (node && atBottom.current) node.scrollTop = node.scrollHeight;
    }, [last, data?.chat?.key]);
    useEffect(() => {
        const node = boxRef.current;
        if (!node || typeof ResizeObserver === 'undefined') return undefined;
        const stick = () => { if (atBottom.current) node.scrollTop = node.scrollHeight; };
        const observer = new ResizeObserver(stick);
        observer.observe(node);
        if (contentRef.current) observer.observe(contentRef.current);
        return () => observer.disconnect();
    }, [data !== null]);

    const loadOlder = async () => {
        const first = messages[0];
        if (!first || loadingOlder) return;
        setLoadingOlder(true);
        const node = boxRef.current;
        const before = node ? node.scrollHeight - node.scrollTop : 0;
        try {
            const { data: payload } = await api.get(`/wa/chats/${encodeURIComponent(chatKey)}/messages`, { before_id: first.id });
            olderLoaded.current = true;
            setMessages((prev) => mergeMessages(prev, payload.messages));
            setQuotes((prev) => ({ ...prev, ...(payload.quotes || {}) }));
            setHasMore(Boolean(payload.has_more));
            requestAnimationFrame(() => { if (node) node.scrollTop = node.scrollHeight - before; });
        } catch (requestError) {
            toast(errorOf(requestError, 'Не загрузились более ранние сообщения'), 'error');
        } finally {
            setLoadingOlder(false);
        }
    };

    // quote и clientId — явно: «Повторить» шлёт ровно то сообщение и тем же номером повтора (сервер не создаст второе).
    const send = async ({ text, file, quote = null, clientId = newClientId() }) => {
        const item = { client_id: clientId, text, kind: file ? 'document' : 'text', file_name: file?.name, size: file?.size,
            status: 'queued', at: localStamp(), quote_id: quote?.wa_id || null, author: null };
        setLocal((prev) => [...prev.filter((entry) => entry.client_id !== clientId), item]);
        atBottom.current = true;
        try {
            if (file) {
                const form = new FormData();
                form.append('file', file);
                form.append('text', text);
                form.append('client_id', clientId);
                if (quote?.wa_id) form.append('quote_id', quote.wa_id);
                // Content-Type для FormData axios ставит сам (с границей частей), как в «Загрузках».
                await api.post(`/wa/chats/${encodeURIComponent(chatKey)}/send`, form);
            } else {
                await api.post(`/wa/chats/${encodeURIComponent(chatKey)}/send`, { text, client_id: clientId, quote_id: quote?.wa_id || null });
            }
            seenClients.current.add(clientId);
            await load();
            setLocal((prev) => prev.filter((entry) => entry.client_id !== clientId));
            onChanged?.();
        } catch (requestError) {
            setLocal((prev) => prev.map((entry) => (entry.client_id === clientId
                ? { ...entry, status: 'failed', error: errorOf(requestError, 'Не отправлено'), retry: { text, file, quote } } : entry)));
        }
    };
    const sendFromComposer = ({ text, file }) => {
        const quote = replyTo;
        setReplyTo(null);
        send({ text, file, quote });
    };
    const retry = async (item) => {
        if (item.outbox_id) {
            try {
                await api.post(`/wa/outbox/${item.outbox_id}/retry`);
                await load();
            } catch (requestError) {
                toast(errorOf(requestError, 'Не получилось отправить заново'), 'error');
            }
            return;
        }
        if (item.retry) send({ ...item.retry, clientId: item.client_id });
    };
    const discard = async (item) => {
        if (!item.outbox_id) {
            setLocal((prev) => prev.filter((entry) => entry.client_id !== item.client_id));
            return;
        }
        try {
            await api.delete(`/wa/outbox/${item.outbox_id}`);
            await load();
        } catch (requestError) {
            toast(errorOf(requestError, 'Сообщение не убралось'), 'error');
        }
    };
    const take = async () => {
        if (!data?.card?.id) return;
        setBusy('take');
        try {
            await api.post(`/cards/${data.card.id}/take`);
            toast('Отклик в работе — можно отвечать', 'success');
            await load();
            onChanged?.();
        } catch (requestError) {
            toast(errorOf(requestError, 'Не получилось взять в работу'), 'error');
            await load();
        } finally {
            setBusy('');
        }
    };
    const handled = async () => {
        setBusy('handled');
        try {
            await api.post(`/wa/chats/${encodeURIComponent(chatKey)}/handled`);
            await load();
            onChanged?.();
        } catch (requestError) {
            toast(errorOf(requestError, 'Не получилось'), 'error');
        } finally {
            setBusy('');
        }
    };
    const loadTemplates = useCallback(() => {
        if (templates !== null) return;
        api.get('/wa/templates').then(({ data: payload }) => setTemplates(payload.items || []))
            .catch((requestError) => { setTemplates([]); toast(errorOf(requestError, 'Шаблоны не загрузились'), 'error'); });
    }, [api, templates, toast]);
    const saveTemplate = async (payload) => {
        setBusy('template');
        try {
            const { data: result } = editing?.id ? await api.patch(`/wa/templates/${editing.id}`, payload) : await api.post('/wa/templates', payload);
            setTemplates(result.items || []);
            setEditing(null);
        } catch (requestError) {
            toast(errorOf(requestError, 'Шаблон не сохранился'), 'error');
        } finally {
            setBusy('');
        }
    };
    const removeTemplate = async () => {
        setBusy('template');
        try {
            const { data: result } = await api.delete(`/wa/templates/${deleting.id}`);
            setTemplates(result.items || []);
            setDeleting(null);
        } catch (requestError) {
            toast(errorOf(requestError, 'Шаблон не удалился'), 'error');
        } finally {
            setBusy('');
        }
    };

    if (error && !data) {
        return <div className="flex flex-1 items-center justify-center px-6 text-sm text-rose-500"><AlertCircle size={16} className="mr-2" /> {error}</div>;
    }
    if (!data) {
        return <div className="flex flex-1 items-center justify-center text-sm text-slate-400"><Loader2 size={15} className="mr-2 animate-spin" /> Загрузка переписки…</div>;
    }
    const card = data.card;
    const title = data.chat.title;
    const subtitle = [card?.phone_pretty, card && stageText({ stage: card.stage, substage: card.substage }), card?.recruiter_name]
        .filter(Boolean).join(' · ');
    const canEditTemplates = Boolean(caps?.take || caps?.manage);
    return (
        <>
            <div className="relative z-20 flex flex-wrap items-center justify-between gap-2 border-b border-slate-200/70 bg-white/85 px-3 py-2.5 backdrop-blur-xl sm:px-4">
                <div className="min-w-0" style={ROW(onBack ? 'auto auto minmax(0, 1fr)' : 'auto minmax(0, 1fr)', { columnGap: 10 })}>
                    {onBack && (
                        <button type="button" onClick={onBack} aria-label="К списку чатов" className="grid h-9 w-9 place-items-center rounded-full text-slate-500 hover:bg-slate-100 md:hidden">
                            <ArrowLeft size={18} />
                        </button>
                    )}
                    <Avatar name={title} size="h-9 w-9" />
                    <div className="min-w-0">
                        <div className="truncate text-[14px] font-semibold text-slate-900">{title}</div>
                        <div className="truncate text-[11.5px] text-slate-400">{subtitle}</div>
                    </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    {card?.id && (
                        <button type="button" onClick={() => onOpenCard(card.id)}
                                className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1.5 text-[12px] font-semibold text-slate-600 transition hover:bg-slate-200 active:scale-[0.97]">
                            <IdCard size={13} /> Карточка
                        </button>
                    )}
                </div>
            </div>
            <div className="relative min-h-0 flex-1">
                <div ref={boxRef} className="wazzup-scrollbar h-full overflow-y-auto py-3"
                     onScroll={(event) => {
                         const node = event.currentTarget;
                         atBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 120;
                     }}>
                    <div ref={contentRef} className="mx-auto w-full max-w-[1040px] space-y-2">
                        {hasMore && (
                            <div className="flex justify-center pb-1">
                                <button type="button" disabled={loadingOlder} onClick={loadOlder}
                                        className="rounded-full bg-white px-3.5 py-1.5 text-[12px] font-medium text-slate-500 shadow-sm ring-1 ring-slate-200/70 transition hover:bg-slate-50 disabled:opacity-50">
                                    {loadingOlder && <Loader2 size={12} className="mr-1 inline animate-spin" />}Более ранние
                                </button>
                            </div>
                        )}
                        {data.hidden && (
                            <div className="px-6 py-10 text-center text-sm text-slate-500">Переписка откроется, когда возьмёте отклик в работу</div>
                        )}
                        {!data.hidden && rows.length === 0 && (
                            <div className="py-8 text-center text-sm text-slate-400">Сообщений нет</div>
                        )}
                        {rows.map((row, index) => (row.dayKey ? (
                            <div key={row.dayKey} className="flex justify-center py-1.5">
                                <span className="rounded-full bg-slate-500/10 px-3 py-1 text-[11px] font-medium text-slate-500">{row.label}</span>
                            </div>
                        ) : (
                            <React.Fragment key={row.key || row.id}>
                                <Bubble message={row} quote={row.quote_id ? quotes[row.quote_id] : null}
                                        pending={Boolean(row.pending)} showName={showAuthor(row, rows[index - 1]?.dayKey ? null : rows[index - 1])}
                                        onReply={data.block ? undefined : setReplyTo} onZoom={setZoom} onRetry={retry} onDiscard={discard} />
                                {/* «Ответ не нужен» — под последним сообщением кандидата, как в «Чатах ОП» (владелец 20.11.2026). */}
                                {data.can_handle && row.id === lastIncoming && (
                                    <div className="flex justify-start px-3 sm:px-4">
                                        <button type="button" onClick={handled} disabled={busy === 'handled'}
                                                title="Сообщения кандидата не ждут ответа — до нового сообщения"
                                                className="inline-flex items-center gap-1.5 rounded-full border border-orange-500 bg-white py-1 pl-2.5 pr-1 text-xs font-semibold text-orange-600 transition hover:bg-orange-50 disabled:opacity-60">
                                            Ответ не нужен
                                            <span className="flex min-h-5 min-w-5 items-center justify-center rounded-full bg-orange-600 px-1 text-[11px] text-white">{data.chat.waiting}</span>
                                        </button>
                                    </div>
                                )}
                            </React.Fragment>
                        )))}
                    </div>
                </div>
            </div>
            {data.connection && !data.connection.connected && !data.block && (
                <div className="border-t border-amber-200/70 bg-amber-50 px-4 py-1.5 text-[12px] text-amber-800">
                    WhatsApp найма: {data.connection.label.toLowerCase()} — сообщение уйдёт, когда номер подключится (если в течение 30 минут)
                </div>
            )}
            {data.block ? (
                <div className="border-t border-slate-200/70 bg-white px-4 py-3 text-[13px] text-slate-600"
                     style={ROW(BLOCK_ACTIONS[data.block] ? 'minmax(0, 1fr) auto' : 'minmax(0, 1fr)', { columnGap: 8 })}>
                    <span>{data.block_text}</span>
                    {BLOCK_ACTIONS[data.block] && (
                        <button type="button" className={TOOLBAR_BTN_PRIMARY} disabled={busy === 'take'} onClick={take}>
                            {busy === 'take' && <Loader2 size={14} className="animate-spin" />} {BLOCK_ACTIONS[data.block]}
                        </button>
                    )}
                </div>
            ) : (
                <Composer chatKey={chatKey} replyTo={replyTo} onCancelReply={() => setReplyTo(null)} onSend={sendFromComposer}
                          templates={templates} onTemplatesOpen={loadTemplates} canEditTemplates={canEditTemplates}
                          onEditTemplate={setEditing} onDeleteTemplate={setDeleting} toast={toast} />
            )}
            <TemplateDialog open={editing !== null} item={editing} busy={busy === 'template'} onClose={() => setEditing(null)} onSave={saveTemplate} />
            <ConfirmDialog open={Boolean(deleting)} onClose={() => setDeleting(null)} danger busy={busy === 'template'}
                           title="Удалить шаблон?" text={deleting ? `«${deleting.title}» пропадёт у всей команды найма.` : ''}
                           confirmLabel="Удалить" onConfirm={removeTemplate} />
            <IosLightbox url={zoom} alt="Фото из WhatsApp" onClose={() => setZoom(null)} />
        </>
    );
};

// ─── Вкладка ──────────────────────────────────────────────────────────────

const ChatsTab = ({ api, toast, caps, onOpenCard, onChanged, version, onWaiting }) => {
    const [mode, setMode] = useState('all');
    const [waitingOnly, setWaitingOnly] = useState(false);
    const [search, setSearch] = useState('');
    const [query, setQuery] = useState('');
    const [list, setList] = useState(null);
    const [extra, setExtra] = useState([]);
    const [listError, setListError] = useState('');
    const [selectedKey, setSelectedKey] = useState(null);
    const [loadingMore, setLoadingMore] = useState(false);
    const [moreAfterExtra, setMoreAfterExtra] = useState(true);
    const listSeq = useRef(0);
    const params = useMemo(() => ({ mode, q: query || undefined, waiting: waitingOnly ? 1 : undefined }), [mode, query, waitingOnly]);
    const paramsKey = JSON.stringify(params);

    useEffect(() => {
        const timer = setTimeout(() => setQuery(search.trim()), 350);
        return () => clearTimeout(timer);
    }, [search]);

    const loadList = useCallback(() => {
        const mine = ++listSeq.current;
        return api.get('/wa/chats', params)
            .then(({ data }) => {
                // Ответ запроса со старым фильтром (пока он был в пути, выбрали другой) список не перетирает.
                if (mine !== listSeq.current) return;
                setList(data);
                setListError('');
                // Счётчик на вкладке раздела — тот же, что «Ждут ответа» во всех чатах: сразу, без своего опроса.
                if (params.mode === 'all' && !params.q) onWaiting?.(Number(data.totals?.waiting_chats) || 0);
            })
            .catch((error) => { if (mine === listSeq.current) setListError(errorOf(error, 'Чаты не загрузились')); });
    }, [api, params, onWaiting]);

    // Догруженные страницы сбрасывает только смена фильтра — не ответ, не «Взять в работу» (version).
    useEffect(() => { setExtra([]); setMoreAfterExtra(true); }, [paramsKey]);
    useEffect(() => {
        let timer = null;
        let stop = false;
        const tick = () => {
            const run = visible() ? loadList() : Promise.resolve();
            run.finally(() => { if (!stop) timer = setTimeout(tick, LIST_POLL_MS); });
        };
        tick();
        return () => { stop = true; clearTimeout(timer); };
    }, [loadList, version]);

    const loadMore = async () => {
        if (!list || loadingMore) return;
        setLoadingMore(true);
        const forKey = paramsKey;
        try {
            const { data } = await api.get('/wa/chats', { ...params, offset: list.items.length + extra.length });
            if (forKey !== JSON.stringify(params)) return;
            setExtra((prev) => {
                const seen = new Set(prev.map((chat) => chat.key));
                return [...prev, ...data.items.filter((chat) => !seen.has(chat.key))];
            });
            setMoreAfterExtra(Boolean(data.has_more));
        } catch (error) {
            toast(errorOf(error, 'Не загрузилось'), 'error');
        } finally {
            setLoadingMore(false);
        }
    };

    const items = useMemo(() => {
        const first = list?.items || [];
        const seen = new Set(first.map((chat) => chat.key));
        return [...first, ...extra.filter((chat) => !seen.has(chat.key))];
    }, [list, extra]);
    const totals = list?.totals || { total: 0, waiting_chats: 0 };
    const hasMore = Boolean(list && (extra.length ? moreAfterExtra : list.has_more) && items.length < totals.total);
    const changed = useCallback(() => { loadList(); onChanged?.(); }, [loadList, onChanged]);

    return (
        <div className="hiring-chats flex overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
            <div className={`${selectedKey ? 'hidden md:flex' : 'flex'} w-full shrink-0 flex-col border-r border-slate-200 md:w-80`}>
                <div className="space-y-2 border-b border-slate-100 p-2.5">
                    <div className="relative">
                        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Имя, телефон, № кандидата"
                               className={`${iosInput} py-2 pl-9 text-[13px]`} />
                    </div>
                    <div style={ROW('minmax(0, 1fr) auto', { columnGap: 8 })}>
                        <IosSegmented value={mode} onChange={setMode} options={MODES} size="xs" ariaLabel="Какие чаты" />
                        <button type="button" onClick={() => setWaitingOnly(!waitingOnly)} aria-pressed={waitingOnly}
                                title="Кандидат написал, ответа ещё нет"
                                className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${waitingOnly ? 'bg-orange-600 text-white' : 'bg-orange-50 text-orange-700 hover:bg-orange-100'}`}>
                            Ждут ответа · {totals.waiting_chats}
                        </button>
                    </div>
                </div>
                <div className="wazzup-scrollbar flex-1 overflow-y-auto">
                    {list === null && !listError && (
                        <div className="py-8 text-center text-sm text-slate-400"><Loader2 size={15} className="mr-2 inline animate-spin" />Загрузка чатов…</div>
                    )}
                    {listError && <div className="py-6 text-center text-sm text-rose-500"><AlertCircle size={15} className="mr-1 inline" />{listError}</div>}
                    {list && items.length === 0 && !listError && (
                        <div className="px-4 py-8 text-center text-sm text-slate-400">
                            {waitingOnly ? 'Все ответили — ждущих нет' : query ? 'Ничего не нашлось' : 'Чатов пока нет'}
                        </div>
                    )}
                    {items.map((chat) => (
                        <ChatRow key={chat.key} chat={chat} selected={chat.key === selectedKey} onOpen={(row) => setSelectedKey(row.key)} />
                    ))}
                    {hasMore && (
                        <button type="button" onClick={loadMore} disabled={loadingMore}
                                className="block w-full py-2.5 text-center text-[12px] font-semibold text-blue-600 hover:bg-slate-50 disabled:opacity-60">
                            Показать ещё ({items.length} из {totals.total})
                        </button>
                    )}
                </div>
                {list?.connection && !list.connection.connected && (
                    <div className="border-t border-slate-100 px-3 py-2 text-[11.5px] text-amber-700">
                        <Pill tone="amber">WhatsApp найма</Pill> {list.connection.label}
                    </div>
                )}
            </div>
            <div className={`${selectedKey ? 'flex' : 'hidden md:flex'} relative isolate min-w-0 flex-1 flex-col bg-[#f2f2f7]`}>
                {selectedKey ? (
                    <Thread key={selectedKey} api={api} toast={toast} chatKey={selectedKey} caps={caps}
                            onBack={() => setSelectedKey(null)} onOpenCard={onOpenCard} onChanged={changed} />
                ) : (
                    <div className="flex flex-1 items-center justify-center px-6">
                        <div className="flex flex-col items-center gap-2 text-slate-400">
                            <MessageSquare size={32} strokeWidth={1.5} />
                            <span className="text-sm">Выберите чат слева</span>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};

export default ChatsTab;
