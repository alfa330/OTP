import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertCircle, AlertTriangle, ArrowDown, ArrowLeft, CheckCircle2, ChevronRight,
    CornerUpLeft, FileText, Inbox, ListChecks, Loader2,
    History, MessageSquare, Paperclip, Plus, RefreshCw, Search, Send, Settings2, Trash2, Users, X,
    XCircle,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosInput, iosGroupLabel,
    iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosBadge, IosModal, IosToggle,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import TicketWizard from './TicketWizard';
import {
    BLOCK_CHECKS, BLOCK_CONTEXT, BLOCK_WARNING, bodyDigest, describeBody,
} from './ticketBody';
import {
    attachmentKind, authorBadge, continuesRun, groupByDay, indexByTgId, messageSnippet, quoteOf,
    shortAuthorName,
} from './threadView';
import {
    isOverdue, markTicketSeen, mergeTicketsById, previewAuthor, previewText,
    queueMonogram, queueTile, rowBadges, unreadLabel,
} from './ticketList';
import { fitHeight, measureShell } from './layout';

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

/* Строка ленты. Раньше это были две строки текста подряд, и сорок таких строк
 * читались как один абзац: глазу не за что зацепиться, а взгляд обязан за один
 * проход находить «где моё и что там нового».
 *
 * Теперь раскладка мессенджера: плитка очереди слева даёт ритм и цвет, тема —
 * первая строка, превью последней реплики — вторая, справа время и пузырёк
 * непрочитанного. Разбор того, ЧТО именно писать в каждом месте, лежит в
 * ticketList.js и проверен тестами.
 *
 * Бейджи остались, но только исключениями: «не доставлено», «просрочено»,
 * массовый сбой и высокий приоритет. Штатное «Отправлено» бейджем не рисуется —
 * иначе сорок строк снова превращаются в светофор.
 */
const TicketRow = memo(function TicketRow({ ticket, active, onSelect }) {
    const unread = ticket.unread;
    const count = unreadLabel(ticket.unread_count);
    const last = ticket.last_message;
    const author = previewAuthor(last);
    const preview = previewText(last);
    const badges = rowBadges(ticket, {
        status: statusMeta(ticket.status), priority: priorityMeta(ticket.priority),
    });

    return (
        <button
            type="button"
            onClick={() => onSelect(ticket.id)}
            className={`relative flex w-full gap-3 px-3 py-2.5 text-left transition-colors ${
                active
                    ? 'bg-blue-50'
                    : unread ? 'bg-blue-50/40 hover:bg-blue-50/70' : 'hover:bg-slate-50'
            }`}
        >
            {/* Выделение выбранного — полоской у края, как в списках macOS.
                Фоном одним его мало: у непрочитанной строки фон тоже голубоват. */}
            <span className={`absolute inset-y-1 left-0 w-[3px] rounded-r-full transition-colors ${
                active ? 'bg-blue-500' : 'bg-transparent'
            }`} />

            {/* Плитка очереди: куда ушло обращение. Цвет по id очереди —
                постоянный, поэтому «Посылки» узнаются до чтения подписи. */}
            <span className={`mt-0.5 grid h-[38px] w-[38px] shrink-0 place-items-center rounded-[12px] text-[13px] font-semibold ring-1 ${
                queueTile(ticket.queue_id)
            }`}>
                {queueMonogram(ticket.queue_title)}
            </span>

            <span className="min-w-0 flex-1">
                <span className="flex items-baseline gap-2">
                    <span className={`min-w-0 flex-1 truncate text-[13.5px] leading-snug ${
                        unread ? 'font-semibold text-slate-900' : 'font-medium text-slate-800'
                    }`}>
                        {ticket.subject}
                    </span>
                    <span className="shrink-0 text-[11px] tabular-nums text-slate-400"
                          title={fmtDateTime(ticket.last_message_at || ticket.created_at)}>
                        {fmtAgo(ticket.last_message_at || ticket.created_at)}
                    </span>
                </span>

                <span className="mt-0.5 flex items-end gap-2">
                    <span className="min-w-0 flex-1">
                        {/* Превью последней реплики — то, ради чего строку и
                            читают: по нему видно, ответили ли по делу, не
                            открывая обращение. */}
                        <span className={`block truncate text-[12px] leading-snug ${
                            unread ? 'text-slate-600' : 'text-slate-500'
                        }`}>
                            {preview
                                ? <>{author && <span className="font-medium text-slate-500">{author}: </span>}{preview}</>
                                : <span className="text-slate-400">{ticket.queue_title}</span>}
                        </span>
                        {/* Третья строка: номер и либо тематика, либо бейджи.
                            Вместе они не влезают и переносят строку — а ряды
                            разной высоты в ленте на сорок обращений читаются
                            хуже, чем на одну подпись меньше. Тематика при этом
                            почти всегда уже стоит в теме обращения. */}
                        <span className="mt-1 flex items-center gap-1.5 overflow-hidden text-[11px] text-slate-400">
                            <span className="shrink-0 tabular-nums">№{ticket.id}</span>
                            <span className="shrink-0 text-slate-300">·</span>
                            {badges.length ? badges.map((badge) => (
                                <IosBadge key={badge.key} tone={badge.tone}
                                          className="!py-0 shrink-0 !text-[10px]">
                                    {badge.label}
                                </IosBadge>
                            )) : (
                                <span className="truncate">{ticket.topic_title || ticket.queue_title}</span>
                            )}
                        </span>
                    </span>
                    {/* Пузырёк непрочитанного. Место под него не держим: у
                        прочитанных обращений его нет, и пустой круг был бы
                        сорок раз повторённым «ничего». */}
                    {!!count && (
                        <span className="mb-0.5 grid h-[19px] min-w-[19px] shrink-0 place-items-center rounded-full bg-blue-500 px-1.5 text-[11px] font-semibold tabular-nums leading-none text-white shadow-sm">
                            {count}
                        </span>
                    )}
                </span>
            </span>
        </button>
    );
});

/* ─── Сообщение в переписке ───────────────────────────────────────────────── */

/* Вложение внутри пузыря. Картинки, видео и звук показываются сразу — как в
 * «Чатах ЧатАпп» и «Чатах Верификаторов»; остальное остаётся файлом-кнопкой.
 *
 * Отличие от тех разделов в одном: там у файла есть прямая ссылка, а здесь файл
 * лежит в Telegram, ссылка живёт около часа и запрос требует авторизации.
 * Поэтому картинку сначала выкачиваем в память и показываем как объектную
 * ссылку — и обязательно освобождаем её при размонтировании, иначе открытая
 * переписка на сотню фото просто не отдаст память обратно.
 */
const MessageMedia = ({ message, apiBaseUrl, ticketId, headers, showToast, light }) => {
    const kind = attachmentKind(message.attachment);
    const inline = kind === 'image' || kind === 'video' || kind === 'audio';
    const [url, setUrl] = useState(null);
    const [failed, setFailed] = useState(false);
    const [zoom, setZoom] = useState(false);
    const [downloading, setDownloading] = useState(false);

    const fetchFile = useCallback(async () => {
        const response = await axios.get(
            `${apiBaseUrl}/api/crm/tickets/${ticketId}/attachments/${message.id}`,
            { headers: headers(), responseType: 'blob' },
        );
        return URL.createObjectURL(response.data);
    }, [apiBaseUrl, headers, message.id, ticketId]);

    useEffect(() => {
        if (!inline) return undefined;
        let alive = true;
        let created = null;
        fetchFile()
            .then((next) => {
                if (!alive) { URL.revokeObjectURL(next); return; }
                created = next;
                setUrl(next);
            })
            .catch(() => { if (alive) setFailed(true); });
        return () => {
            alive = false;
            if (created) URL.revokeObjectURL(created);
        };
    }, [inline, fetchFile]);

    const openFile = async () => {
        setDownloading(true);
        try {
            const next = await fetchFile();
            window.open(next, '_blank', 'noopener');
            setTimeout(() => URL.revokeObjectURL(next), 60000);
        } catch (error) {
            showToast?.(errorText(error, 'Не удалось открыть вложение'), 'error');
        } finally {
            setDownloading(false);
        }
    };

    if (inline && !failed) {
        if (!url) {
            return (
                <div className={`mt-1.5 grid h-28 w-40 place-items-center rounded-xl ${
                    light ? 'bg-white/15' : 'bg-slate-100'
                }`}>
                    <Loader2 size={16} className="animate-spin opacity-60" />
                </div>
            );
        }
        if (kind === 'image') {
            return (
                <>
                    <img src={url} alt={message.attachment.name || ''} loading="lazy"
                         onError={() => setFailed(true)}
                         onClick={() => setZoom(true)}
                         className="mt-1.5 max-h-64 w-auto max-w-full cursor-zoom-in rounded-xl" />
                    <IosModal open={zoom} onClose={() => setZoom(false)} title="Вложение"
                              maxWidth="max-w-3xl">
                        <img src={url} alt={message.attachment.name || ''}
                             className="mx-auto max-h-[72vh] w-auto rounded-2xl" />
                    </IosModal>
                </>
            );
        }
        if (kind === 'video') {
            return <video controls preload="metadata" src={url} onError={() => setFailed(true)}
                          className="mt-1.5 max-h-64 w-auto max-w-full rounded-xl" />;
        }
        return <audio controls preload="none" src={url} onError={() => setFailed(true)}
                      className="mt-1.5 h-10 w-56 max-w-full" />;
    }

    return (
        <button type="button" onClick={openFile} disabled={downloading}
                className={`mt-1.5 inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[12px] font-medium transition ${
                    light ? 'bg-white/15 hover:bg-white/25' : 'bg-slate-100 hover:bg-slate-200'
                }`}>
            {downloading ? <Loader2 size={12} className="animate-spin" /> : <Paperclip size={12} />}
            {message.attachment.name || 'Вложение'}
        </button>
    );
};

const MessageBubble = ({
    message, quote, grouped, apiBaseUrl, ticketId, headers, showToast, onReply, onJumpTo,
}) => {
    const outgoing = message.direction === 'out';
    const note = message.direction === 'note';
    // Кружок с инициалами — только у входящих: сторону своей реплики держит
    // цвет пузыря, а у заметки автора нет вовсе. Кто именно написал — подписано
    // внутри пузыря, в том числе у своих: обращение ведут несколько человек, и
    // «наша сторона» это не один и тот же сотрудник.
    const badge = !outgoing && !note ? authorBadge(message) : null;
    // ФИО целиком в подпись не влезает — берём фамилию с именем.
    const author = outgoing ? shortAuthorName(message.author_name) : message.author_name;

    return (
        <div id={`crm-msg-${message.id}`}
             className={`group flex items-end gap-1.5 ${grouped ? 'mt-0.5' : 'mt-2.5'} ${
                 outgoing ? 'justify-end' : 'justify-start'
             }`}>
            {/* Кнопка ответа показывается по наведению и стоит со стороны поля
                ввода: у исходящих слева, у входящих справа — так она не
                перекрывает текст и не занимает место постоянно. */}
            {outgoing && onReply && (
                <ReplyHandle onClick={() => onReply(message)} />
            )}
            {/* Место слева держим у КАЖДОГО входящего, включая заметку: без
                него продолжение серии и заметка уезжают влево, и вместо одной
                колонки пузырей получается три. */}
            {!outgoing && (badge && !grouped
                ? (
                    <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-full text-[11px] font-semibold ${badge.bg}`}
                          title={message.author_name || ''}>
                        {badge.initials}
                    </span>
                )
                : <span className="h-7 w-7 shrink-0" />)}
            <div className={`max-w-[76%] px-3 py-2 text-[13.5px] leading-snug ${
                note
                    ? 'rounded-2xl bg-amber-50 text-amber-900 ring-1 ring-amber-100'
                    : outgoing
                        /* Свои — синие, и «хвост» у нижнего правого угла срезан:
                           так пузырь принадлежит своей стороне, а не висит
                           посередине. Тень мягкая — на плотном полотне без неё
                           пузырь выглядит наклейкой. */
                        ? 'rounded-2xl rounded-br-md bg-blue-600 text-white shadow-[0_1px_3px_rgba(37,99,235,0.35)]'
                        /* Чужие — БЕЛЫЕ с тонким кантом. Раньше здесь был
                           bg-slate-100 на фоне bg-slate-50/60: пузырь и полотно
                           почти не отличались, и переписка читалась как текст
                           без пузырей вовсе. */
                        : 'rounded-2xl rounded-bl-md bg-white text-slate-800 ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.06)]'
            }`}>
                {quote && (
                    <button type="button"
                            disabled={!quote.id}
                            onClick={() => quote.id && onJumpTo?.(quote.id)}
                            className={`mb-1.5 flex w-full gap-2 rounded-lg border-l-[3px] px-2 py-1 text-left transition ${
                                outgoing
                                    ? 'border-white/60 bg-white/10 hover:bg-white/20'
                                    : 'border-blue-400 bg-slate-50 hover:bg-slate-100'
                            } ${quote.id ? 'cursor-pointer' : 'cursor-default'}`}>
                        <span className="min-w-0">
                            {quote.author && (
                                <span className={`block text-[11px] font-semibold ${
                                    outgoing ? 'text-white/90' : 'text-blue-700'
                                }`}>
                                    {quote.author}
                                </span>
                            )}
                            <span className={`block truncate text-[12px] ${
                                outgoing ? 'text-white/80' : 'text-slate-500'
                            }`}>
                                {quote.text}
                            </span>
                        </span>
                    </button>
                )}
                {author && !grouped && (
                    /* Подпись у обеих сторон. На синем пузыре цвет из палитры
                       не читается, поэтому там имя белёсое — различать по цвету
                       на своей стороне всё равно некого. */
                    <div className={`mb-0.5 text-[11.5px] font-semibold ${
                        note ? 'text-amber-700'
                            : outgoing ? 'text-white/85'
                                : badge ? badge.tone : 'text-slate-600'
                    }`}>
                        {author}
                    </div>
                )}
                {message.body
                    ? <div className="whitespace-pre-wrap break-words">{message.body}</div>
                    : (!message.attachment && (
                        // Вложение уходит отдельным сообщением с пустым телом —
                        // без этого в переписке висел бы пустой пузырь.
                        <div className="text-[12px] italic opacity-70">без текста</div>
                    ))}
                {message.attachment && (
                    <MessageMedia message={message} apiBaseUrl={apiBaseUrl} ticketId={ticketId}
                                  headers={headers} showToast={showToast} light={outgoing} />
                )}
                <BubbleTime message={message} outgoing={outgoing} />
            </div>
            {!outgoing && onReply && (
                <ReplyHandle onClick={() => onReply(message)} />
            )}
        </div>
    );
};

/* Время отправки — в правом нижнем углу пузыря, ровно как в «Чатах
 * Верификаторов» (WazzupChatsView.MessageBubble): `mt-0.5`, 10 px, к правому
 * краю. Раздел не должен иметь своей версии одного и того же пузыря.
 *
 * Пробовали дописывать время в строку с текстом — так делает Telegram, и пузырь
 * короткой реплики выходит ниже. Но у нас уже есть эталон в соседнем разделе, и
 * два разных чата в одном портале хуже, чем несколько лишних пикселей. */
const BubbleTime = ({ message, outgoing }) => (
    <div className={`mt-0.5 flex items-center justify-end text-[10px] tabular-nums ${
        outgoing ? 'text-blue-100/90' : 'text-slate-400'
    }`}>
        {fmtTime(message.created_at)}
    </div>
);

/* Ответить на это сообщение. Появляется только по наведению: постоянная кнопка
 * у каждой реплики — это шум на каждой строке переписки. */
const ReplyHandle = ({ onClick }) => (
    <button type="button" onClick={onClick} title="Ответить"
            className="mb-1 grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 opacity-0 transition hover:bg-white hover:text-slate-600 focus:opacity-100 group-hover:opacity-100">
        <CornerUpLeft size={14} />
    </button>
);

/* Плашка дня между сообщениями. Липкая: пролистывая длинную переписку, всегда
 * видно, какой день читаешь. */
const DayChip = ({ children }) => (
    <div className="sticky top-0 z-10 flex justify-center py-1">
        <span className="crm-day-chip rounded-full px-2.5 py-1 text-[11px] font-semibold text-slate-500 ring-1 ring-slate-200/70">
            {children}
        </span>
    </div>
);

/* ─── Текст обращения ─────────────────────────────────────────────────────── */

const CHECK_TONE = {
    green: 'text-emerald-500',
    rose: 'text-rose-500',
    red: 'text-rose-500',
    blue: 'text-blue-500',
    amber: 'text-amber-500',
    slate: 'text-slate-400',
};

/* Значок строки — по тону, а не по порядку: «подтвердилось» и «не
 * подтвердилось» стоят в одном списке рядом, и различать их обязано что-то
 * кроме цвета (цвет видят не все). */
const CHECK_ICON = {
    green: CheckCircle2,
    rose: XCircle,
    red: AlertCircle,
    slate: Search,
};

/* Само обращение: тот текст, что ушёл в группу. Раньше он выводился одним
 * серым полотном — «просто большой блок текста», как и было сказано.
 *
 * Теперь блоки рисуются по смыслу (разбор — describeBody в ticketBody.js):
 * метка сбоя полосой, «где и когда» — метками, суть — перечнем «подпись/ответ»,
 * хвост — строками с галочкой.
 *
 * Один компонент на два места: этот же блок стоит и в начале переписки, и в
 * панели справа. Двумя копиями разметки они разъехались бы на второй правке.
 */
const TicketBody = ({ body }) => {
    const blocks = describeBody(body);
    if (!blocks.length) {
        return <div className="text-[12.5px] italic text-slate-400">Текст обращения пуст</div>;
    }
    return (
        <div className="space-y-3">
            {blocks.map((block, index) => {
                if (block.kind === BLOCK_WARNING) {
                    return (
                        <div key={index} className="space-y-1">
                            {block.rows.map((row, rowIndex) => (
                                <div key={rowIndex}
                                     className="flex items-center gap-2 rounded-xl bg-amber-50 px-2.5 py-1.5 text-[12.5px] font-semibold text-amber-800 ring-1 ring-amber-100">
                                    <AlertTriangle size={13} className="shrink-0 text-amber-500" />
                                    <span className="min-w-0 break-words">{row.value}</span>
                                </div>
                            ))}
                        </div>
                    );
                }
                if (block.kind === BLOCK_CONTEXT) {
                    return (
                        <div key={index} className="flex flex-wrap gap-1.5">
                            {block.chips.map((chip, chipIndex) => (
                                <span key={chipIndex}
                                      className="rounded-lg bg-slate-100 px-2 py-0.5 text-[11.5px] font-medium text-slate-600">
                                    {chip}
                                </span>
                            ))}
                        </div>
                    );
                }
                if (block.kind === BLOCK_CHECKS) {
                    return (
                        <div key={index} className="space-y-1.5 border-t border-slate-100 pt-2.5">
                            {block.rows.map((row, rowIndex) => (
                                <div key={rowIndex} className="flex items-start gap-2 text-[12.5px]">
                                    <span className={`mt-[3px] shrink-0 ${CHECK_TONE[row.tone] || 'text-slate-400'}`}>
                                        {React.createElement(CHECK_ICON[row.tone] || ListChecks, { size: 13 })}
                                    </span>
                                    <span className="min-w-0">
                                        {row.label && (
                                            <span className="text-slate-500">{row.label}: </span>
                                        )}
                                        {row.items
                                            ? (
                                                <span className="inline-flex flex-wrap gap-1 align-middle">
                                                    {row.items.map((item, itemIndex) => (
                                                        <span key={itemIndex}
                                                              className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[11.5px] text-slate-700">
                                                            {item}
                                                        </span>
                                                    ))}
                                                </span>
                                            )
                                            : <span className="break-words font-medium text-slate-800">{row.value}</span>}
                                    </span>
                                </div>
                            ))}
                        </div>
                    );
                }
                return (
                    <div key={index} className="space-y-1">
                        {block.rows.map((row, rowIndex) => (row.label ? (
                            /* Подпись бледная, ответ тёмный — так перечень
                               «вопрос: ответ» читается по столбцу ответов, а не
                               построчно целиком. */
                            <div key={rowIndex}
                                 className="flex flex-wrap items-baseline gap-x-1.5 text-[12.5px] leading-relaxed">
                                <span className="text-slate-500">{row.label}</span>
                                <span className="min-w-0 break-words font-medium text-slate-800">
                                    {row.value}
                                </span>
                            </div>
                        ) : (
                            <div key={rowIndex}
                                 className="break-words text-[12.5px] font-medium leading-relaxed text-slate-800">
                                {row.text}
                            </div>
                        )))}
                    </div>
                );
            })}
        </div>
    );
};

/* ─── Карточка обращения ──────────────────────────────────────────────────── */

/* Показывать ли панель обращения справа — выбор человека, а не раздела, и он
 * переживает переход к другому обращению и перезагрузку страницы. Держать её
 * закрытой по умолчанию правильно (обычно нужен чат), но тому, кто работает с
 * панелью, переоткрывать её сорок раз в день — издевательство. */
const ASIDE_KEY = 'crm.ticket.aside';

const readAsidePreference = () => {
    try {
        return window.localStorage.getItem(ASIDE_KEY) === '1';
    } catch (error) {
        // Приватный режим и «запретить сайту данные» — не повод падать.
        return false;
    }
};

const writeAsidePreference = (value) => {
    try {
        window.localStorage.setItem(ASIDE_KEY, value ? '1' : '0');
    } catch (error) { /* см. выше */ }
};

// Насколько близко к низу считается «человек смотрит свежее». 120px — примерно
// один пузырь: если внизу видно последнее сообщение, лента доедет сама.
const NEAR_BOTTOM = 120;

const TicketCard = ({
    ticketId, apiBaseUrl, headers, showToast, onChanged, onSeen, onBack, pulse,
}) => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [reply, setReply] = useState('');
    // На какое сообщение отвечаем. null — на обращение целиком, как было.
    const [replyTo, setReplyTo] = useState(null);
    const [sending, setSending] = useState(false);
    const [attachment, setAttachment] = useState(null);
    // История действий не приезжает вместе с карточкой: она нужна изредка и
    // почти вся повторяет то, что видно в переписке. Один запрос по кнопке
    // вместо лишнего запроса на каждое открытие обращения.
    const [events, setEvents] = useState(null);
    const [eventsLoading, setEventsLoading] = useState(false);
    // Панель с текстом обращения справа.
    const [asideOpen, setAsideOpen] = useState(readAsidePreference);
    // Человек ушёл читать историю переписки вверх — вниз его не тащим.
    const [atBottom, setAtBottom] = useState(true);
    const fileRef = useRef(null);
    const threadRef = useRef(null);

    const load = useCallback(async (silent = false) => {
        if (!silent) setLoading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/crm/tickets/${ticketId}`,
                { headers: headers() });
            setData(response.data);
            setError(null);
            /* Открытие карточки ГАСИТ «непрочитано» на сервере — значит, и в
               ленте оно должно погаснуть. Лента при этом НЕ перезапрашивается
               намеренно: список отсортирован «непрочитанное сверху», и
               перезапрос увёз бы читаемое обращение из-под курсора вниз. */
            if (response.data?.item && !response.data.item.unread) onSeen?.(Number(ticketId));
        } catch (err) {
            setError(errorText(err, 'Не удалось открыть обращение'));
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, headers, ticketId, onSeen]);

    useEffect(() => { load(); }, [load]);

    /* Обновление по «тычку» колокола, а не по таймеру: пришёл ответ из группы —
       сервер разбудил вкладку, и карточка перечитывается. Фонового опроса в
       портале нет и заводить его здесь нельзя. */
    useEffect(() => {
        if (!pulse) return;
        load(true);
    }, [pulse]); // eslint-disable-line react-hooks/exhaustive-deps

    const messages = data?.messages;

    /* Лента доезжает к свежему сообщению — но только если человек и так стоял
       внизу. Раньше она прокручивалась всегда: стоило уйти читать переписку
       вверх, как пришедший ответ утаскивал экран в конец. Теперь вместо рывка
       появляется кнопка «вниз». */
    useEffect(() => {
        const node = threadRef.current;
        if (!node) return;
        if (atBottom) node.scrollTop = node.scrollHeight;
    }, [messages?.length]); // eslint-disable-line react-hooks/exhaustive-deps

    // При переходе к другому обращению лента всегда начинается снизу.
    useEffect(() => { setAtBottom(true); }, [ticketId]);

    const onThreadScroll = useCallback((event) => {
        const node = event.currentTarget;
        const gap = node.scrollHeight - node.scrollTop - node.clientHeight;
        setAtBottom(gap < NEAR_BOTTOM);
    }, []);

    const scrollToBottom = useCallback(() => {
        const node = threadRef.current;
        if (!node) return;
        node.scrollTo({ top: node.scrollHeight, behavior: 'smooth' });
    }, []);

    const toggleAside = useCallback(() => {
        setAsideOpen((open) => {
            writeAsidePreference(!open);
            return !open;
        });
    }, []);

    /* Раскрытие панели поджимает переписку, пузыри переверстываются, и лента,
       стоявшая внизу, оказывается «почти внизу». Догоняем — но только если она
       и была внизу: иначе панель утаскивала бы читателя истории в конец.
       Задержка равна длительности перехода в styles.css: пока панель едет,
       высота содержимого ещё меняется. */
    useEffect(() => {
        if (!atBottom) return undefined;
        const timer = setTimeout(() => {
            const node = threadRef.current;
            if (node) node.scrollTop = node.scrollHeight;
        }, 300);
        return () => clearTimeout(timer);
    }, [asideOpen]); // eslint-disable-line react-hooks/exhaustive-deps

    // Escape закрывает панель — привычка из любого оверлея; на узком экране
    // панель лежит поверх переписки, и это единственный быстрый выход.
    useEffect(() => {
        if (!asideOpen) return undefined;
        const onKey = (event) => {
            if (event.key !== 'Escape') return;
            setAsideOpen(false);
            writeAsidePreference(false);
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [asideOpen]);

    const ticket = data?.item;
    const permissions = data?.permissions || {};

    /* Указатель «на что отвечали» строится один раз на всю нить: у каждого
       сообщения искать цель перебором значило бы квадрат на длинной переписке. */
    const quoteIndex = useMemo(() => indexByTgId(messages), [messages]);

    /* Корневое сообщение уже показано блоком «Обращение» — второй раз тем же
       текстом это дубль, а не переписка. Дни считаются после отсева: иначе
       день, в котором осталось одно отсеянное сообщение, дал бы пустую плашку. */
    const days = useMemo(() => groupByDay(
        (messages || []).filter((m, index) => !(
            index === 0 && m.direction === 'out' && m.body === ticket?.body
        )),
    ), [messages, ticket?.body]);

    /* Переход к оригиналу по клику на цитату — как в Telegram. Подсветку снимаем
       сами: без неё сообщение осталось бы выделенным навсегда. */
    const jumpToMessage = useCallback((messageId) => {
        const node = document.getElementById(`crm-msg-${messageId}`);
        if (!node) return;
        node.scrollIntoView({ behavior: 'smooth', block: 'center' });
        node.classList.add('ring-2', 'ring-blue-400', 'rounded-2xl');
        setTimeout(() => node.classList.remove('ring-2', 'ring-blue-400', 'rounded-2xl'), 1400);
    }, []);

    const send = async () => {
        const body = reply.trim();
        if (!body && !attachment) return;
        setSending(true);
        try {
            const form = new FormData();
            form.append('body', body);
            if (replyTo) form.append('reply_to', String(replyTo.id));
            if (attachment) form.append('attachment', attachment);
            const response = await axios.post(
                `${apiBaseUrl}/api/crm/tickets/${ticketId}/messages`, form,
                { headers: headers() },
            );
            setData((prev) => (prev ? { ...prev, messages: response.data.messages } : prev));
            setReply('');
            setReplyTo(null);
            setAttachment(null);
            if (fileRef.current) fileRef.current.value = '';
            // Своё сообщение всегда доезжает до экрана: человек только что его
            // отправил и обязан увидеть, что оно ушло.
            setAtBottom(true);
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
    const overdue = isOverdue(ticket);

    return (
        <div className="flex h-full min-h-0 flex-col">
            {/* Шапка карточки */}
            <div className="shrink-0 border-b border-slate-200/70 bg-white/80 px-4 py-3 backdrop-blur-xl">
                <div className="flex items-start gap-2">
                    {onBack && (
                        <button type="button" onClick={onBack}
                                className="-ml-1 mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 lg:hidden">
                            <ArrowLeft size={16} />
                        </button>
                    )}
                    <span className={`mt-0.5 hidden shrink-0 place-items-center rounded-[11px] text-[12px] font-semibold ring-1 sm:grid sm:h-[34px] sm:w-[34px] ${
                        queueTile(ticket.queue_id)
                    }`}>
                        {queueMonogram(ticket.queue_title)}
                    </span>
                    <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                            <span className="text-[12px] font-semibold tabular-nums text-slate-400">
                                №{ticket.id}
                            </span>
                            <h3 className="line-clamp-2 text-[15px] font-semibold leading-tight text-slate-900">
                                {ticket.subject}
                            </h3>
                        </div>
                        {/* На телефоне из меты остаётся только необходимое:
                            группа, когда завели и до когда ждём ответ. Тематика
                            и автор переносами съедали пол-экрана до первой
                            реплики, а обе стоят в панели обращения рядом. */}
                        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-slate-500">
                            <span>{ticket.queue_title}</span>
                            {ticket.topic_title && (
                                <span className="hidden items-center gap-2 sm:inline-flex">
                                    <span className="text-slate-300">·</span>
                                    <span>{ticket.topic_title}</span>
                                </span>
                            )}
                            <span className="hidden items-center gap-2 sm:inline-flex">
                                <span className="text-slate-300">·</span>
                                <span>{ticket.created_by_name}</span>
                            </span>
                            <span className="text-slate-300">·</span>
                            <span className="tabular-nums">{fmtDateTime(ticket.created_at)}</span>
                            {ticket.due_at && (
                                <span className="inline-flex items-center gap-2">
                                    <span className="text-slate-300">·</span>
                                    <span className={`tabular-nums ${overdue ? 'font-semibold text-amber-600' : ''}`}>
                                        ответ до {fmtDateTime(ticket.due_at)}
                                    </span>
                                </span>
                            )}
                        </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                        {status.tone
                            ? <IosBadge tone={status.tone}>{status.label}</IosBadge>
                            : <span className="text-[11.5px] text-slate-400">{status.label}</span>}
                        {priority.tone && <IosBadge tone={priority.tone}>{priority.label}</IosBadge>}
                        {/* Само обращение — на расстоянии одного нажатия из
                            любого места переписки. Кнопка нажатая читается как
                            нажатая: панель может стоять открытой полдня, и
                            «откуда она взялась» не должно быть вопросом. */}
                        <button type="button" onClick={toggleAside}
                                aria-pressed={asideOpen}
                                title={asideOpen ? 'Скрыть текст обращения' : 'Показать текст обращения'}
                                className={`inline-flex shrink-0 items-center gap-1.5 rounded-xl px-2.5 py-1.5 text-[12.5px] font-semibold transition-all active:scale-[0.98] ${
                                    asideOpen
                                        ? 'bg-blue-600 text-white shadow-sm hover:bg-blue-700'
                                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                                }`}>
                            <FileText size={14} />
                            <span className="hidden sm:inline">Обращение</span>
                        </button>
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

            {/* Переписка и панель обращения. relative — под панель: на узком
                экране она выезжает поверх именно этой области, а не всей
                страницы. */}
            <div className="relative flex min-h-0 flex-1 overflow-hidden">
                {/* Обёртка нужна кнопке «вниз»: она стоит absolute, и без
                    собственного контекста позиционирования её середина
                    считалась бы от переписки ВМЕСТЕ с панелью — при открытой
                    панели кнопка уезжала бы вправо. */}
                <div className="relative flex min-h-0 min-w-0 flex-1">
                <div ref={threadRef} onScroll={onThreadScroll}
                     className="crm-thread crm-scroll min-h-0 w-full overflow-y-auto px-4 pb-4">
                    {/* Текст обращения в начале переписки — то, с чего разговор
                        начался.

                        Когда открыта панель, он сворачивается в одну строку: тот
                        же текст, показанный дважды рядом, это не «удобнее», а
                        два раза съеденное место. В строке остаётся то, чем
                        обращение опознают (парк · город · период), а сам текст в
                        полутора сантиметрах справа. */}
                    {asideOpen ? (
                        <button type="button" onClick={toggleAside}
                                title="Свернуть панель обращения"
                                className="mt-3 flex w-full items-center gap-2 rounded-xl bg-white/80 px-3 py-2 text-left ring-1 ring-slate-200/70 transition hover:bg-white">
                            <FileText size={12} className="shrink-0 text-slate-400" />
                            <span className="shrink-0 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                Обращение
                            </span>
                            <span className="min-w-0 flex-1 truncate text-[12px] text-slate-500">
                                {bodyDigest(ticket.body)}
                            </span>
                            <span className="shrink-0 text-[11px] tabular-nums text-slate-400">
                                {fmtDateTime(ticket.created_at)}
                            </span>
                        </button>
                    ) : (
                        <div className="mt-3 rounded-2xl bg-white px-3.5 py-3 ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
                            <div className="mb-2 flex items-center gap-1.5">
                                <FileText size={12} className="text-slate-400" />
                                <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                    Обращение
                                </span>
                                <span className="ml-auto text-[11px] tabular-nums text-slate-400">
                                    {fmtDateTime(ticket.created_at)}
                                </span>
                            </div>
                            <TicketBody body={ticket.body} />
                        </div>
                    )}

                    {days.map((day) => (
                        <div key={day.key}>
                            <DayChip>{day.label}</DayChip>
                            {day.items.map((message, index) => (
                                <MessageBubble key={message.id} message={message} ticketId={ticket.id}
                                               quote={quoteOf(message, quoteIndex)}
                                               grouped={continuesRun(day.items[index - 1], message)}
                                               apiBaseUrl={apiBaseUrl} headers={headers}
                                               showToast={showToast}
                                               onReply={permissions.can_reply ? setReplyTo : null}
                                               onJumpTo={jumpToMessage} />
                            ))}
                        </div>
                    ))}
                    {ticket.resolved_at && (
                        <div className="mt-3 flex items-center justify-center gap-1.5 text-[11.5px] font-medium text-emerald-700">
                            <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 ring-1 ring-emerald-100">
                                <CheckCircle2 size={13} />
                                Решено{ticket.resolved_by_name ? ` · ${ticket.resolved_by_name}` : ''} · {fmtDateTime(ticket.resolved_at)}
                            </span>
                        </div>
                    )}

                </div>
                    {/* «Вниз» появляется только когда человек ушёл читать
                        историю: внизу она была бы кнопкой «остаться на месте». */}
                    {!atBottom && (
                        <button type="button" onClick={scrollToBottom}
                                title="К свежим сообщениям"
                                className="absolute bottom-4 left-1/2 z-10 grid h-9 w-9 -translate-x-1/2 place-items-center rounded-full bg-white text-slate-500 shadow-[0_4px_14px_rgba(15,23,42,0.18)] ring-1 ring-slate-200/70 transition hover:text-slate-800 active:scale-95">
                            <ArrowDown size={16} />
                        </button>
                    )}
                </div>

                {/* Затемнение — только там, где панель лежит ПОВЕРХ переписки.
                    На широком экране она переписку поджимает, и затемнять
                    нечего: смысл панели как раз в том, чтобы чат остался
                    рабочим. */}
                {asideOpen && (
                    <button type="button" aria-label="Скрыть обращение" onClick={toggleAside}
                            className="absolute inset-0 z-10 bg-slate-900/25 lg:hidden" />
                )}

                {/* Панель обращения. Всегда в разметке, а не по условию: иначе у
                    неё не было бы анимации закрытия — нечему уезжать. Механика
                    (поджать переписку на широком, наплыть на узком) в
                    src/styles.css: это медиазапрос, а не набор классов. */}
                <aside className={`crm-aside ${asideOpen ? 'is-open' : ''}`}
                       aria-hidden={!asideOpen} aria-label="Текст обращения">
                    <div className="crm-aside-body">
                        <div className="flex shrink-0 items-center gap-2 border-b border-slate-200/70 px-3.5 py-2.5">
                            <FileText size={13} className="text-slate-400" />
                            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                Обращение №{ticket.id}
                            </span>
                            <button type="button" onClick={toggleAside} aria-label="Скрыть обращение"
                                    className="ml-auto grid h-6 w-6 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-600">
                                <X size={13} />
                            </button>
                        </div>
                        <div className="crm-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain px-3.5 py-3">
                            <div className="mb-2.5 text-[13px] font-semibold leading-snug text-slate-900">
                                {ticket.subject}
                            </div>
                            <TicketBody body={ticket.body} />
                            {(ticket.client_name || ticket.client_phone) && (
                                <div className="mt-3 border-t border-slate-100 pt-2.5">
                                    <div className={iosGroupLabel}>Клиент</div>
                                    <div className="mt-1 text-[12.5px] text-slate-700">
                                        {[ticket.client_name, ticket.client_phone].filter(Boolean).join(' · ')}
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                </aside>
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
                            {replyTo && (
                                <div className="mb-1.5 flex items-start gap-2 rounded-lg border-l-[3px] border-blue-400 bg-slate-50 px-2 py-1">
                                    <span className="min-w-0 flex-1">
                                        <span className="block text-[11px] font-semibold text-blue-700">
                                            Ответ: {replyTo.author_name
                                                || (replyTo.direction === 'out' ? 'Оператор' : 'сообщение')}
                                        </span>
                                        <span className="block truncate text-[11.5px] text-slate-500">
                                            {messageSnippet(replyTo, 70)}
                                        </span>
                                    </span>
                                    <button type="button" onClick={() => setReplyTo(null)}
                                            className="mt-0.5 shrink-0 text-slate-400 hover:text-slate-600">
                                        <X size={12} />
                                    </button>
                                </div>
                            )}
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
    // Справочники, из которых мастер даёт выбирать. Приезжают вместе с каталогом
    // тематик — отдельный запрос за списком парков не нужен.
    const [taxiParks, setTaxiParks] = useState([]);
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

    /* Карточка занимает всё место до низа экрана. Раньше высота была
       `calc(100vh-300px)`, где 300 — прикидка шапки с фильтрами: под карточкой
       оставалась полоса пустоты, а на другом разрешении поле ответа уехало бы
       под край. Считаем по факту (см. layout.js) и пересчитываем, когда
       что-нибудь поехало. */
    const shellRef = useRef(null);
    const headerRef = useRef(null);
    const filtersRef = useRef(null);
    const [shellHeight, setShellHeight] = useState(null);

    useEffect(() => {
        const node = shellRef.current;
        if (!node || typeof ResizeObserver === 'undefined') return undefined;
        const recompute = () => setShellHeight(fitHeight(measureShell(node)));
        recompute();
        /* Наблюдаем за прокрутчиком И за всем, что стоит НАД карточкой: её
           положение зависит именно от них. Первого измерения мало — на узком
           экране фильтры переносятся в три ряда уже после первого кадра, и
           карточка, посчитанная по короткой шапке, свисала за край на 67 px.
           window.resize этого не ловит: размеры окна не менялись.
           Ни один из наблюдаемых узлов не зависит от высоты карточки, так что
           обратной связи «пересчёт → новый размер → пересчёт» здесь нет. */
        const observer = new ResizeObserver(recompute);
        observer.observe(node.closest('.main-content') || document.body);
        if (headerRef.current) observer.observe(headerRef.current);
        if (filtersRef.current) observer.observe(filtersRef.current);
        window.addEventListener('resize', recompute);
        return () => {
            observer.disconnect();
            window.removeEventListener('resize', recompute);
        };
    }, [tab, selectedId]);

    // Во время поиска выборка не сужается до «моих», поэтому и сегмент показывает
    // «Все»: подсвеченные «Мои» над списком с чужими обращениями — это не фильтр,
    // а неверная подпись к тому, что человек видит.
    const searching = mine && !searchApplied;

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
            setTaxiParks(response.data.reference?.taxi_parks || []);
        } catch (err) {
            setScenarioCatalog([]);
            setTaxiParks([]);
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
            // Поиск сквозной: ищем по всем обращениям, иначе сотрудник не увидит,
            // что по этому водителю обращение уже завёл кто-то другой.
            if (mine && !searchApplied) params.set('mine', '1');
            if (searchApplied) params.set('q', searchApplied);
            params.set('limit', String(PAGE_SIZE));
            params.set('offset', String(nextOffset));
            const response = await axios.get(`${apiBaseUrl}/api/crm/tickets?${params}`,
                { headers: headers() });
            const items = response.data.items || [];
            // Склейка по id, а не конкатенация: порядок «непрочитанное сверху»
            // сдвигается от прочтения, и OFFSET на догрузке иначе то пропускает
            // строку, то приносит дубль (см. mergeTicketsById).
            setTickets((prev) => (nextOffset ? mergeTicketsById(prev, items) : items));
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

    /* Карточка сообщает, что сервер погасил «непрочитано», — и лента гасит
       пузырёк у этой строки, не перезапрашиваясь.
       Функция ОБЯЗАНА быть стабильной: она уходит в зависимости load() внутри
       карточки, и новая ссылка на каждый рендер раздела означала бы
       перезагрузку карточки от любого чиха выше (в проекте это уже случалось
       с showToast). Поэтому setState функцией и пустые зависимости. */
    const handleSeen = useCallback((ticketId) => {
        setTickets((prev) => markTicketSeen(prev, ticketId));
    }, []);

    return (
        <div className="w-full" style={{ fontFamily: APPLE_FONT }}>
            <div ref={headerRef}
                 className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
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
                        состояние видно по сегменту, остальное — нейтрально.

                        На телефоне при открытом обращении их нет вовсе: там
                        экран один, и человек в этот момент читает переписку, а
                        не отбирает очередь. Три ряда фильтров съедали половину
                        экрана, оставляя нити 200 px — это меньше трёх реплик.
                        Назад к списку (и к фильтрам) ведёт стрелка в шапке. */}
                    <div ref={filtersRef}
                         className={`mb-3 flex-wrap items-center gap-2 px-1 lg:flex ${
                             selectedId ? 'hidden' : 'flex'
                         }`}>
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
                                            disabled={Boolean(searchApplied)}
                                            title={searchApplied ? 'Поиск идёт по всем обращениям' : undefined}
                                            onClick={() => { setMine(item.key); setSelectedId(null); }}
                                            className={`rounded-[9px] px-3 py-1.5 text-[12.5px] font-semibold transition-all ${
                                                searching === item.key
                                                    ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                                                    : 'text-slate-500 hover:text-slate-700'
                                            } ${searchApplied ? 'cursor-not-allowed opacity-60' : ''}`}>
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
                                   placeholder="ИИН, номер, тема, телефон"
                                   className={`${iosInput} pl-9`} />
                        </div>
                    </div>

                    <div className={`${iosCard} overflow-hidden`}>
                        {/* Высота задана, а не только минимальная: без неё колонка
                            карточки росла под переписку, лента внутри никогда не
                            переполнялась и не скроллилась — вместо неё ехала вся
                            страница. min-h остаётся полом для низких экранов.

                            На телефоне высота тоже нужна, и по той же причине:
                            там колонка одна, и без неё переписка растягивала
                            карточку на несколько экранов, а поле ответа
                            оказывалось где-то далеко внизу страницы — в
                            мессенджере оно всегда под рукой.

                            Само число приходит из измерения (shellHeight), а не
                            из calc с прикидкой: высота шапки над карточкой не
                            постоянна — фильтры переносятся по строкам, а на
                            телефоне при открытом обращении фильтров нет вовсе. Пока первого
                            измерения нет, работает запасное значение в классе:
                            без него карточка на один кадр была бы нулевой. */}
                        <div ref={shellRef}
                             style={shellHeight ? { height: shellHeight } : undefined}
                             className="flex h-[calc(100dvh-320px)] min-h-[380px] flex-col lg:flex-row">
                            {/* Лента.
                                min-h-0 обязателен: у элемента flex по умолчанию
                                min-height:auto, то есть он не умеет стать ниже
                                своего содержимого. В колонку (телефон) это
                                значит, что панель растягивает карточку под всю
                                переписку, внутренняя прокрутка не включается
                                никогда и едет вся страница — ровно то, от чего
                                карточке и задана высота. */}
                            <div className={`flex w-full min-h-0 shrink-0 flex-col border-slate-200/70 lg:w-[360px] lg:border-r ${
                                selectedId ? 'hidden lg:flex' : 'flex'
                            }`}>
                                <div className="crm-scroll min-h-0 flex-1 overflow-y-auto">
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
                                    {/* Волосяная линия между обращениями. Она
                                        была в старой ленте, и без неё строки из
                                        трёх строк текста каждая слипаются в
                                        абзац. divide-y, а не рамка у строки:
                                        тогда линия не рисуется ни после
                                        последней строки, ни второй раз рядом с
                                        рамкой подвала «Показано N». */}
                                    <div className="divide-y divide-slate-100">
                                        {tickets.map((ticket) => (
                                            <TicketRow key={ticket.id} ticket={ticket}
                                                       active={ticket.id === selectedId}
                                                       onSelect={setSelectedId} />
                                        ))}
                                    </div>
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

                            {/* Карточка (про min-h-0 — см. выше) */}
                            <div className={`min-h-0 min-w-0 flex-1 ${selectedId ? 'flex' : 'hidden lg:flex'}`}>
                                {selectedId ? (
                                    <div className="h-full min-h-0 w-full">
                                        <TicketCard
                                            key={selectedId}
                                            ticketId={selectedId}
                                            apiBaseUrl={apiBaseUrl}
                                            headers={headers}
                                            showToast={showToast}
                                            onChanged={refreshAfterChange}
                                            onSeen={handleSeen}
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
                taxiParks={taxiParks}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                showToast={showToast}
                onCreated={(id) => { setSelectedId(id); loadTickets(0, true); }}
            />
        </div>
    );
}
