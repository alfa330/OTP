import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertCircle, AlertTriangle, ArrowDown, ArrowLeft, Check, CheckCircle2, ChevronRight, Copy,
    CornerDownRight, CornerUpLeft, Download, FileText, Inbox, ListChecks, Loader2,
    History, MessageSquare, Paperclip, Plus, RefreshCw, Search, Send, Settings2, Trash2, Users, X,
    XCircle,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosInput, iosGroupLabel,
    iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosBadge, IosModal, IosToggle,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { ReviewPanel, ReviewResolveModal } from '../common/SupervisorReview';
import TicketWizard from './TicketWizard';
import TicketsExport from './TicketsExport';
import {
    BLOCK_CHECKS, BLOCK_CONTEXT, BLOCK_WARNING, bodyDigest, describeBody,
} from './ticketBody';
import {
    attachmentKind, authorBadge, continuesRun, groupByDay, indexByTgId, messageSnippet, quoteOf,
    shortAuthorName, threadBubble,
} from './threadView';
import {
    REVIEW_FILTER, REVIEW_RESOLVED, isOverdue, lockedReplyText, markTicketSeen,
    mergeTicketsById, pluralTickets, previewAuthor, previewText, queueMonogram, queueTile,
    reviewToast, rowBadges, stateFilters, statusView as ticketStatusView, unreadLabel,
} from './ticketList';
import { fitHeight, measureShell } from './layout';
import { COMPLAINTS_FILTER, complaintStatusFor, mergeFeeds, withSortRank } from './feedMerge';
import { TARGET_ICONS } from '../complaints/ComplaintDraft';
import {
    isRecorded, openQuestion, rowBadges as complaintRowBadges,
    rowSubtitle as complaintRowSubtitle, statusView, whereabouts,
} from '../complaints/complaintRules';

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

/* Красная кнопка подтверждения — тот же iOS-примитив, перекрашенный. Своего
 * экспорта в ui/ios для неё нет, и заводить его ради двух мест значило бы
 * править общий модуль всех разделов; так же поступает «Вики» (WikiGuests.jsx).
 * Красное здесь только на кнопке подтверждения в модалке: сама «Удалить» в
 * подвале карточки серая, и краснеет лишь под курсором. */
const iosBtnDanger = `${iosBtnPrimary} !bg-rose-600 hover:!bg-rose-700`;

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
// экран по умолчанию, архив открывается отдельным сегментом. Пятый сегмент,
// «На проверку», появляется только у того, кого ждёт проверка (stateFilters).
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
    review_sent: 'Супервайзер проверил: в группу',
    review_resolved: 'Супервайзер проверил: решено',
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

/* Строка своей жалобы в ленте — тот же каркас, что у обращения. Жалоба
 * заводится здесь же, выбором направления в «Новом обращении», и здесь же автор
 * видит ответ для водителя и вопрос группы (решение владельца 29.09.2026).
 *
 * Плитка — нейтральная, с иконкой «на кого»: у очередей плитки цветные, и
 * жалоба узнаётся по тому, что она не похожа на очередь, а не по красному
 * цвету, — красное в ленте означает сбой. В отборе к удалению жалоб нет: он
 * чистит обращения. */
const ComplaintFeedRow = memo(function ComplaintFeedRow({ complaint, active, onSelect }) {
    const Icon = TARGET_ICONS[complaint.target] || AlertTriangle;
    const unread = complaint.unread;
    const closed = complaint.status === 'closed';
    // В ленте только свои жалобы — «Вопрос вам» адресован автору.
    const badges = complaintRowBadges(complaint, complaint.created_by);
    const at = complaint.last_activity_at || complaint.created_at;

    return (
        <button type="button" onClick={() => onSelect(complaint.id)}
                className={`relative flex w-full gap-3 px-3 py-2.5 text-left transition-colors ${
                    active ? 'bg-blue-50' : unread ? 'bg-blue-50/40 hover:bg-blue-50/70' : 'hover:bg-slate-50'
                }`}>
            <span className={`absolute inset-y-1 left-0 w-[3px] rounded-r-full transition-colors ${
                active ? 'bg-blue-500' : 'bg-transparent'
            }`} />
            <span className={`mt-0.5 grid h-[38px] w-[38px] shrink-0 place-items-center rounded-[12px] ring-1 ${
                closed ? 'bg-slate-50 text-slate-400 ring-slate-100' : 'bg-slate-100 text-slate-600 ring-slate-200/70'
            }`}>
                <Icon size={17} />
            </span>
            <span className="min-w-0 flex-1">
                <span className="flex items-baseline gap-2">
                    <span className={`min-w-0 flex-1 truncate text-[13.5px] leading-snug ${
                        unread ? 'font-semibold text-slate-900'
                            : closed ? 'font-medium text-slate-500' : 'font-medium text-slate-800'
                    }`}>
                        {complaint.reason_title}
                    </span>
                    <span className="shrink-0 text-[11px] tabular-nums text-slate-400" title={fmtDateTime(at)}>
                        {fmtAgo(at)}
                    </span>
                </span>
                <span className={`mt-0.5 block truncate text-[12px] leading-snug ${
                    unread ? 'text-slate-600' : 'text-slate-500'
                }`}>
                    {complaintRowSubtitle(complaint)}
                </span>
                <span className="mt-1 flex items-center gap-1.5 overflow-hidden text-[11px] text-slate-400">
                    <span className="shrink-0 tabular-nums">Жалоба №{complaint.id}</span>
                    <span className="shrink-0 text-slate-300">·</span>
                    {badges.length ? badges.map((badge) => (
                        <IosBadge key={badge.key} tone={badge.tone} className="!py-0 shrink-0 !text-[10px]">
                            {badge.label}
                        </IosBadge>
                    )) : (
                        <span className="truncate">{complaint.target_title}</span>
                    )}
                </span>
            </span>
        </button>
    );
});

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
/* Строка ленты. В обычном виде открывает обращение; в режиме отбора (он есть
 * только у администратора) — отмечает его к удалению.
 *
 * Кнопка остаётся ОДНОЙ на всю строку, а не «кнопка + чекбокс внутри»: кнопка
 * внутри кнопки — невалидная разметка, и браузер разбирает её как попало.
 * Поэтому в режиме отбора у той же кнопки меняется смысл нажатия, а кружок
 * слева — просто картинка состояния. Заодно мишень остаётся во всю строку:
 * отметить сорок обращений, целясь в кружок 18×18, — это про другое терпение. */
const TicketRow = memo(function TicketRow({
    ticket, active, onSelect, selectable = false, selected = false, onToggle,
}) {
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
            onClick={() => (selectable ? onToggle?.(ticket.id) : onSelect(ticket.id))}
            aria-pressed={selectable ? selected : undefined}
            className={`relative flex w-full gap-3 px-3 py-2.5 text-left transition-colors ${
                active || (selectable && selected)
                    ? 'bg-blue-50'
                    : unread ? 'bg-blue-50/40 hover:bg-blue-50/70' : 'hover:bg-slate-50'
            }`}
        >
            {/* Выделение выбранного — полоской у края, как в списках macOS.
                Фоном одним его мало: у непрочитанной строки фон тоже голубоват. */}
            <span className={`absolute inset-y-1 left-0 w-[3px] rounded-r-full transition-colors ${
                active ? 'bg-blue-500' : 'bg-transparent'
            }`} />

            {/* Кружок отметки — только в режиме отбора. Место под него не
                держим: в обычной ленте это сорок пустых кружков подряд. */}
            {selectable && (
                <span className={`mt-[11px] grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full border transition-colors ${
                    selected
                        ? 'border-blue-600 bg-blue-600 text-white'
                        : 'border-slate-300 bg-white text-transparent'
                }`}>
                    <Check size={11} strokeWidth={3} />
                </span>
            )}

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
const MessageMedia = ({ message, apiBaseUrl, ticketId, headers, showToast, light, url: fileUrl = null }) => {
    const kind = attachmentKind(message.attachment);
    const inline = kind === 'image' || kind === 'video' || kind === 'audio';
    const [url, setUrl] = useState(null);
    const [failed, setFailed] = useState(false);
    const [zoom, setZoom] = useState(false);
    const [downloading, setDownloading] = useState(false);

    const fetchFile = useCallback(async () => {
        // fileUrl — у жалоб в общей ленте свой адрес вложения, остальное то же.
        const response = await axios.get(
            fileUrl || `${apiBaseUrl}/api/crm/tickets/${ticketId}/attachments/${message.id}`,
            { headers: headers(), responseType: 'blob' },
        );
        return URL.createObjectURL(response.data);
    }, [apiBaseUrl, headers, message.id, ticketId, fileUrl]);

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

/* tag и attachmentUrl — для переписки жалобы в той же ленте: у её сообщений
 * есть смысл, который надо видеть сразу («Ответ для водителя» — его оператор
 * озвучивает, «Вопрос группы» — ждёт его), и свой адрес вложений. */
const MessageBubble = ({
    message, quote, grouped, apiBaseUrl, ticketId, headers, showToast, onReply, onJumpTo,
    tag = null, attachmentUrl = null,
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
                {tag && (
                    <div className="mb-1 flex items-center gap-1.5">
                        <IosBadge tone={tag.tone} className="!py-0 !text-[10px]">{tag.label}</IosBadge>
                        {tag.action}
                    </div>
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
                                  headers={headers} showToast={showToast} light={outgoing}
                                  url={attachmentUrl} />
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

/* «Обращения больше нет» — состояние, а не отказ, поэтому и рисуется серым, а
 * не красным (см. разбор ошибки в load). Одна строка на оба места: текст и
 * условие его цвета разъехались бы первой же правкой формулировки. */
const TICKET_GONE = 'Обращение удалено или больше вам не видно';

const TicketCard = ({
    ticketId, apiBaseUrl, headers, showToast, onChanged, onSeen, onBack, onDeleted, pulse,
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
    // Вопрос перед удалением. Не window.confirm: сказать нужно две вещи, и
    // вторую («в Telegram-группе сообщения останутся») в системном окошке никто
    // не читает — оно выглядит как формальность.
    const [confirmDelete, setConfirmDelete] = useState(false);
    const [deleting, setDeleting] = useState(false);
    // Решение по обращению на проверке: идёт ли запрос и открыто ли окно итога.
    const [reviewBusy, setReviewBusy] = useState(false);
    const [resolveOpen, setResolveOpen] = useState(false);
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
            /* 404 — это не отказ раздела, а «обращения больше нет»: его удалил
               администратор, а ссылка на него осталась — в колоколе, в открытой
               вкладке коллеги, в переходе из Telegram. Показывать здесь ответ
               сервера «Обращение не найдено» красным значило бы объявить
               поломкой обычный порядок вещей. */
            setError(err?.response?.status === 404
                ? TICKET_GONE
                : errorText(err, 'Не удалось открыть обращение'));
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

    /* Удалить обращение. Право приезжает с сервера (permissions.can_delete), а
       не выводится из роли: правило живёт в crm/access.py, и второй его слепок
       во фронте разошёлся бы с первым молча — кнопка есть, сервер отвечает 403.

       Ленту после удаления перезапрашивает раздел, а не карточка: строка ушла
       не только из карточки, но и из списка, и из счётчиков шапки. */
    const removeTicket = async () => {
        setDeleting(true);
        try {
            await axios.delete(`${apiBaseUrl}/api/crm/tickets/${ticketId}`,
                { headers: headers() });
            setConfirmDelete(false);
            showToast?.(`Обращение №${ticketId} удалено`, 'success');
            onDeleted?.(ticketId);
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось удалить обращение'), 'error');
        } finally {
            setDeleting(false);
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

    /* Решение по обращению на проверке (возврат задачи #297): «Решено» с итогом
       или «в группу». Сервер отвечает карточкой целиком — после решения у
       обращения меняется всё сразу: состояние, права, переписка.

       «В группу» — это решение плюс доставка, и доставка может не случиться
       (бота выгнали из группы): тогда обращение уже отправлено супервайзером, а
       повтор — обычной «Отправить ещё раз» в шапке. */
    const review = async (decision, note = '') => {
        setReviewBusy(true);
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/crm/tickets/${ticketId}/review`,
                { decision, note }, { headers: headers() });
            setData({
                item: response.data.item,
                messages: response.data.messages,
                permissions: response.data.permissions,
            });
            setResolveOpen(false);
            const toast = reviewToast(decision, response.data);
            showToast?.(toast.text, toast.tone);
            setAtBottom(true);
            // Задача проверки закрыта — число «На проверку» надо пересчитать.
            onChanged?.({ counters: true });
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось сохранить решение'), 'error');
            // Решение уже принял другой: окно «Решено» закрываем — его
            // «Сохранить» отдало бы тот же отказ ещё раз.
            if (err?.response?.status === 409) {
                setResolveOpen(false);
                load(true);
                onChanged?.({ counters: true });
            }
        } finally {
            setReviewBusy(false);
        }
    };

    if (loading) return <LoadingBlock />;
    if (error) {
        const gone = error === TICKET_GONE;
        return (
            <div className={`flex items-center justify-center gap-2 py-16 text-[13px] ${
                gone ? 'text-slate-400' : 'text-rose-500'
            }`}>
                {gone ? <Inbox size={15} /> : <AlertCircle size={15} />} {error}
            </div>
        );
    }
    if (!ticket) return null;

    // У обращения на проверке своё слово: «Отправлено» про него — неправда.
    const status = ticketStatusView(ticket, statusMeta(ticket.status));
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
                            {/* В какой чат обращение ушло НА САМОМ ДЕЛЕ. У темы
                                может быть свой чат, и по названию тематики это
                                уже не угадать; у обращений, заведённых до
                                маршрутов, поля нет — строки тоже. */}
                            {ticket.tg_chat_title && (
                                <span className="inline-flex items-center gap-2">
                                    <span className="text-slate-300">·</span>
                                    <span className="truncate">{ticket.tg_chat_title}</span>
                                </span>
                            )}
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
                            {day.items.map((message, index) => {
                                // Итог супервайзера встаёт в переписку как
                                // ответ — с меткой, чей он (threadView.js).
                                const bubble = threadBubble(message);
                                return (
                                    <MessageBubble key={message.id} message={bubble.message}
                                                   tag={bubble.tag} ticketId={ticket.id}
                                                   quote={quoteOf(message, quoteIndex)}
                                                   grouped={continuesRun(day.items[index - 1], message)}
                                                   apiBaseUrl={apiBaseUrl} headers={headers}
                                                   showToast={showToast}
                                                   onReply={permissions.can_reply ? setReplyTo : null}
                                                   onJumpTo={jumpToMessage} />
                                );
                            })}
                        </div>
                    ))}
                    {ticket.resolved_at && (
                        <div className="mt-3 flex items-center justify-center gap-1.5 text-[11.5px] font-medium text-emerald-700">
                            <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 ring-1 ring-emerald-100">
                                <CheckCircle2 size={13} />
                                {/* Кто решил при проверке — подписано на самом
                                    итоге строкой выше; здесь имя было бы вторым
                                    разом о том же человеке и той же минуте. */}
                                {ticket.review_state === REVIEW_RESOLVED
                                    ? 'Решено супервайзером'
                                    : `Решено${ticket.resolved_by_name ? ` · ${ticket.resolved_by_name}` : ''}`}
                                {' · '}{fmtDateTime(ticket.resolved_at)}
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
                {permissions.can_review ? (
                    /* Обращение на проверке (возврат задачи #297): на месте
                       поля ответа — решение супервайзера. Писать в группу
                       здесь некуда, а это главное, что он может сделать. */
                    <ReviewPanel className="" busy={reviewBusy}
                                 hint="Стоит внимания — отправьте в группу: дальше оно пойдёт как любое обращение. Нет — «Решено» с итогом: обращение закроется, в группу не уйдёт, а оператор увидит ваш итог."
                                 onResolve={() => setResolveOpen(true)}
                                 onSend={() => review('send')} />
                ) : permissions.can_reply ? (
                    <div className="flex items-end gap-2">
                        <button type="button" onClick={() => fileRef.current?.click()}
                                title="Прикрепить файл"
                                className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500 transition hover:bg-slate-200 active:scale-95">
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
                            {/* block — иначе под строчным textarea остаётся зазор
                                базовой линии и кнопки по бокам съезжают ниже поля;
                                leading-5 + py-2.5 из iosInput = 40px, ровно h-10 кнопок. */}
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
                                className={`${iosInput} block resize-none leading-5`}
                            />
                        </div>
                        <button type="button" onClick={send}
                                disabled={sending || (!reply.trim() && !attachment)}
                                className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-blue-600 text-white transition hover:bg-blue-700 active:scale-95 disabled:opacity-40">
                            {sending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
                        </button>
                    </div>
                ) : (
                    <div className="text-center text-[12px] text-slate-400">
                        {lockedReplyText(ticket)}
                    </div>
                )}

                {/* Строка стоит за правом менять статус — оно есть у каждого,
                    кто видит обращение. У обращения на проверке и у решённого
                    супервайзером этого права нет ни у кого, а «История» нужна
                    и там: в ней видно, кто и что по нему решил. */}
                {(permissions.can_change_status || permissions.can_delete
                    || Boolean(ticket.review_state)) && (
                    <div className="mt-2.5 flex flex-wrap items-center gap-2">
                        <button type="button" onClick={toggleEvents} className={iosBtnGhost}>
                            {eventsLoading ? <Loader2 size={13} className="animate-spin" /> : <History size={13} />}
                            История
                        </button>
                        {permissions.can_change_status && !closed && (
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
                        {permissions.can_change_status && closed && (
                            <button type="button" onClick={() => changeStatus('open')}
                                    className={iosBtnSecondary}>
                                <RefreshCw size={14} /> Вернуть в работу
                            </button>
                        )}
                        {/* Удаление стоит поодаль от рабочих кнопок (ml-auto) и
                            выкрашивается только под курсором: «Вопрос решён» и
                            «Удалить» — это разные по последствиям действия, и
                            стоять они рядом плечом к плечу не должны. */}
                        {permissions.can_delete && (
                            <button type="button" onClick={() => setConfirmDelete(true)}
                                    title="Удалить обращение вместе с перепиской"
                                    className="ml-auto inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-[13px] font-medium text-slate-400 transition-all hover:bg-rose-50 hover:text-rose-600 active:scale-[0.98]">
                                <Trash2 size={13} /> Удалить
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

            <IosModal
                open={confirmDelete}
                onClose={() => setConfirmDelete(false)}
                title={`Удалить обращение №${ticketId}?`}
                subtitle={ticket.subject}
                maxWidth="max-w-md"
                footer={(
                    <>
                        <button type="button" onClick={() => setConfirmDelete(false)}
                                className={iosBtnSecondary}>
                            Отмена
                        </button>
                        <button type="button" onClick={removeTicket} disabled={deleting}
                                className={iosBtnDanger}>
                            {deleting
                                ? <Loader2 size={14} className="animate-spin" />
                                : <Trash2 size={14} />}
                            Удалить
                        </button>
                    </>
                )}
            >
                <DeleteWarning />
            </IosModal>

            <ReviewResolveModal open={resolveOpen} onClose={() => setResolveOpen(false)}
                                busy={reviewBusy}
                                subtitle="Обращение закроется с вашим итогом и в группу не уйдёт"
                                onResolve={(note) => review('resolve', note)} />
        </div>
    );
};

/* Что именно случится при удалении. Отдельным блоком, потому что спрашивается
 * в двух местах — в карточке про одно обращение и в ленте про отобранные, — а
 * два разных объяснения одного действия человек прочитает как два действия.
 *
 * many меняет ровно два слова. Не мелочь: «Обращение и вся переписка по нему»
 * над списком из трёх строк читается как «удалится одно», то есть подтверждение
 * описывает не то, что произойдёт. */
/* ─── Своя жалоба — в формате обращения ───────────────────────────────────── */

/* Жалобу оператор заводит и ведёт в «Обращениях», и выглядеть она обязана как
 * обращение (владелец, 29.09.2026): переписка с группой — пузырями по дням,
 * данные жалобы — в начале переписки и уезжают при прокрутке, а открыть их
 * снова можно кнопкой «Жалоба» в шапке — той же панелью, что у обращения.
 *
 * Видно автору только то, что ему положено (сервер: OPERATOR_VISIBLE_KINDS):
 * ответ для водителя, вопрос группы и свои ответы. Внутреннего обсуждения и
 * работы с сотрудником здесь нет — это раздел «Жалобы». */
const COMPLAINT_TAGS = {
    answer: { label: 'Ответ для водителя', tone: 'blue' },
    question: { label: 'Вопрос группы', tone: 'amber' },
};

const complaintBubble = (message) => ({
    ...message,
    // Свои ответы — справа, как у обращения; всё из группы — слева.
    direction: message.kind === 'operator_reply' ? 'out' : 'in',
});

const CopyText = ({ text, showToast }) => (
    <button type="button" title="Скопировать"
            onClick={async () => {
                try {
                    await navigator.clipboard.writeText(text || '');
                    showToast?.('Скопировано', 'success');
                } catch (err) {
                    showToast?.('Не удалось скопировать', 'error');
                }
            }}
            className="inline-flex items-center gap-1 rounded-md px-1 text-[10.5px] font-medium text-slate-400 transition hover:bg-slate-100 hover:text-slate-600">
        <Copy size={11} /> Копировать
    </button>
);

const ComplaintFacts = ({ item, showToast }) => {
    const rows = [
        ['На кого', [item.target_title, item.unit_name].filter(Boolean).join(' · ')],
        ['Причина', item.reason_title],
        item.employee_name ? ['Сотрудник', item.employee_name] : null,
        item.event_at ? ['Когда', item.event_time_known ? fmtDateTime(item.event_at)
            : new Date(item.event_at).toLocaleDateString('ru-RU')] : null,
        ['Водитель', [item.driver_name, item.city].filter(Boolean).join(' · ')],
        ['Телефон', item.driver_phone, <CopyText key="copy" text={item.driver_phone} showToast={showToast} />],
        item.driver_ref ? ['ID / ВУ', item.driver_ref] : null,
        item.result_title ? ['Итог', item.result_title] : null,
    ].filter(Boolean);
    return (
        <div className="space-y-2.5">
            <div className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-slate-800">
                {item.description}
            </div>
            <dl className="space-y-1 text-[12.5px]">
                {rows.map(([label, value, action]) => (
                    <div key={label} className="flex items-baseline gap-2">
                        <dt className="w-[76px] shrink-0 text-slate-400">{label}</dt>
                        <dd className="min-w-0 flex-1 break-words text-slate-700">
                            {value || '—'} {action}
                        </dd>
                    </div>
                ))}
            </dl>
            <div className="text-[11.5px] leading-snug text-slate-400">
                Принял {item.created_by_name || '—'} · {fmtDateTime(item.created_at)}
                {whereabouts(item) ? ` · ${whereabouts(item)}` : ''}
            </div>
        </div>
    );
};

const ComplaintThreadCard = ({
    complaintId, apiBaseUrl, headers, showToast, onChanged, onSeen, onBack, pulse,
}) => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [reply, setReply] = useState('');
    const [attachment, setAttachment] = useState(null);
    const [sending, setSending] = useState(false);
    const [resending, setResending] = useState(false);
    // Панель с данными — та же настройка, что у обращения: человек выбрал
    // держать её открытой или нет, и между обращением и жалобой это не меняется.
    const [asideOpen, setAsideOpen] = useState(readAsidePreference);
    const [atBottom, setAtBottom] = useState(true);
    const fileRef = useRef(null);
    const threadRef = useRef(null);

    const load = useCallback(async (silent = false) => {
        if (!silent) setLoading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/complaints/complaints/${complaintId}`,
                { headers: headers() });
            setData(response.data);
            setError(null);
            // Открытие гасит «непрочитано» на сервере — и в ленте тоже.
            onSeen?.(Number(complaintId));
        } catch (err) {
            setError(err?.response?.status === 404 || err?.response?.status === 403
                ? 'Жалоба удалена или больше вам не видна'
                : errorText(err, 'Не удалось открыть жалобу'));
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, headers, complaintId, onSeen]);

    useEffect(() => { load(); }, [load]);
    useEffect(() => { if (pulse) load(true); }, [pulse]); // eslint-disable-line react-hooks/exhaustive-deps

    const item = data?.item;
    const permissions = data?.permissions || {};
    const messages = data?.messages;
    const days = useMemo(() => groupByDay((messages || []).map(complaintBubble)), [messages]);
    const question = useMemo(() => openQuestion(item, messages || []), [item, messages]);

    // Как у обращения: доезжаем к свежему, только если человек и так внизу.
    useEffect(() => {
        const node = threadRef.current;
        if (node && atBottom) node.scrollTop = node.scrollHeight;
    }, [messages?.length]); // eslint-disable-line react-hooks/exhaustive-deps
    useEffect(() => { setAtBottom(true); }, [complaintId]);

    const onThreadScroll = useCallback((event) => {
        const node = event.currentTarget;
        setAtBottom(node.scrollHeight - node.scrollTop - node.clientHeight < NEAR_BOTTOM);
    }, []);

    const toggleAside = useCallback(() => {
        setAsideOpen((open) => {
            writeAsidePreference(!open);
            return !open;
        });
    }, []);

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

    const send = async () => {
        const body = reply.trim();
        if (!body && !attachment) return;
        setSending(true);
        try {
            const form = new FormData();
            form.append('body', body);
            if (attachment) form.append('attachment', attachment);
            const response = await axios.post(
                `${apiBaseUrl}/api/complaints/complaints/${complaintId}/messages`, form,
                { headers: headers() });
            setData((prev) => (prev ? { ...prev, item: response.data.item || prev.item,
                                        messages: response.data.messages || prev.messages } : prev));
            setReply('');
            setAttachment(null);
            if (fileRef.current) fileRef.current.value = '';
            setAtBottom(true);
            onChanged?.();
        } catch (err) {
            showToast?.(errorText(err, 'Сообщение не ушло'), 'error');
        } finally {
            setSending(false);
        }
    };

    const resend = async () => {
        setResending(true);
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/complaints/complaints/${complaintId}/resend`, {}, { headers: headers() });
            setData((prev) => (prev ? { ...prev, item: response.data.item || prev.item } : prev));
            onChanged?.();
            showToast?.('Жалоба отправлена в группу', 'success');
        } catch (err) {
            showToast?.(errorText(err, 'Отправить не получилось'), 'error');
        } finally {
            setResending(false);
        }
    };

    if (loading) return <LoadingBlock />;
    if (error || !item) {
        return (
            <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-slate-400">
                <Inbox size={15} /> {error || 'Жалоба удалена или больше вам не видна'}
            </div>
        );
    }

    const status = statusView(item);
    const Icon = TARGET_ICONS[item.target] || AlertTriangle;
    const undelivered = item.requires_processing !== false && item.delivery_status !== 'sent';
    const facts = <ComplaintFacts item={item} showToast={showToast} />;

    return (
        <div className="flex h-full min-h-0 flex-col">
            <div className="shrink-0 border-b border-slate-200/70 bg-white/80 px-4 py-3 backdrop-blur-xl">
                <div className="flex items-start gap-2">
                    {onBack && (
                        <button type="button" onClick={onBack} aria-label="К списку"
                                className="-ml-1 mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 lg:hidden">
                            <ArrowLeft size={16} />
                        </button>
                    )}
                    <span className="mt-0.5 hidden h-[34px] w-[34px] shrink-0 place-items-center rounded-[11px] bg-slate-100 text-slate-600 ring-1 ring-slate-200/70 sm:grid">
                        <Icon size={16} />
                    </span>
                    <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                            <span className="text-[12px] font-semibold tabular-nums text-slate-400">
                                Жалоба №{item.id}
                            </span>
                            <h3 className="line-clamp-2 text-[15px] font-semibold leading-tight text-slate-900">
                                {item.reason_title}
                            </h3>
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-slate-500">
                            <span>{item.target_title}</span>
                            {/* Группа — только на широком: на телефоне строка
                                переносилась в три с висящей точкой, а группа
                                у жалоб одна и та же. */}
                            {item.tg_chat_title && (
                                <span className="hidden items-center gap-2 sm:inline-flex">
                                    <span className="text-slate-300">·</span>
                                    <span className="truncate">{item.tg_chat_title}</span>
                                </span>
                            )}
                            <span className="text-slate-300">·</span>
                            <span className="tabular-nums">{fmtDateTime(item.created_at)}</span>
                        </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                        {status.tone === 'green'
                            ? <IosBadge tone="green">{status.label}</IosBadge>
                            : <span className="text-[11.5px] text-slate-400">{status.label}</span>}
                        <button type="button" onClick={toggleAside} aria-pressed={asideOpen}
                                title={asideOpen ? 'Скрыть данные жалобы' : 'Показать данные жалобы'}
                                className={`inline-flex shrink-0 items-center gap-1.5 rounded-xl px-2.5 py-1.5 text-[12.5px] font-semibold transition-all active:scale-[0.98] ${
                                    asideOpen
                                        ? 'bg-blue-600 text-white shadow-sm hover:bg-blue-700'
                                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                                }`}>
                            <FileText size={14} />
                            <span className="hidden sm:inline">Жалоба</span>
                        </button>
                    </div>
                </div>
                {undelivered && (
                    <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-xl bg-rose-50 px-3 py-2 text-[12px] text-rose-700 ring-1 ring-rose-100">
                        <span>Жалоба не ушла в группу: {item.delivery_error || 'причина неизвестна'}</span>
                        <button type="button" onClick={resend} disabled={resending}
                                className="inline-flex items-center gap-1.5 rounded-lg bg-white px-2.5 py-1 text-[12px] font-semibold text-rose-700 transition hover:bg-rose-100">
                            {resending ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                            Отправить ещё раз
                        </button>
                    </div>
                )}
            </div>

            <div className="relative flex min-h-0 flex-1 overflow-hidden">
                <div className="relative flex min-h-0 min-w-0 flex-1">
                    <div ref={threadRef} onScroll={onThreadScroll}
                         className="crm-thread crm-scroll min-h-0 w-full overflow-y-auto px-4 pb-4">
                        {/* Данные жалобы — в начале переписки, уезжают при
                            прокрутке. При открытой панели — одной строкой: тот
                            же текст дважды рядом — это съеденное место. */}
                        {asideOpen ? (
                            <button type="button" onClick={toggleAside} title="Свернуть данные жалобы"
                                    className="mt-3 flex w-full items-center gap-2 rounded-xl bg-white/80 px-3 py-2 text-left ring-1 ring-slate-200/70 transition hover:bg-white">
                                <FileText size={12} className="shrink-0 text-slate-400" />
                                <span className="shrink-0 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                    Жалоба
                                </span>
                                <span className="min-w-0 flex-1 truncate text-[12px] text-slate-500">
                                    {[item.driver_name, item.city, item.description].filter(Boolean).join(' · ')}
                                </span>
                            </button>
                        ) : (
                            <div className="mt-3 rounded-2xl bg-white px-3.5 py-3 ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
                                <div className="mb-2 flex items-center gap-1.5">
                                    <FileText size={12} className="text-slate-400" />
                                    <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                        Жалоба
                                    </span>
                                    <span className="ml-auto text-[11px] tabular-nums text-slate-400">
                                        {fmtDateTime(item.created_at)}
                                    </span>
                                </div>
                                {facts}
                            </div>
                        )}

                        {days.map((day) => (
                            <div key={day.key}>
                                <DayChip>{day.label}</DayChip>
                                {day.items.map((message, index) => (
                                    <MessageBubble key={message.id} message={message} ticketId={item.id}
                                                   grouped={continuesRun(day.items[index - 1], message)}
                                                   apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast}
                                                   attachmentUrl={`${apiBaseUrl}/api/complaints/complaints/${item.id}/attachments/${message.id}`}
                                                   tag={COMPLAINT_TAGS[message.kind] ? {
                                                       ...COMPLAINT_TAGS[message.kind],
                                                       action: message.kind === 'answer' && message.body
                                                           ? <CopyText text={message.body} showToast={showToast} />
                                                           : null,
                                                   } : null} />
                                ))}
                            </div>
                        ))}
                        {!days.length && item.requires_processing !== false && (
                            <div className="mt-6 text-center text-[12px] text-slate-400">
                                {item.delivery_status === 'sent'
                                    ? 'Группа ещё не ответила — ответ появится здесь'
                                    : 'Жалоба ещё не в группе'}
                            </div>
                        )}
                        {item.status === 'closed' && item.closed_at && !isRecorded(item) && (
                            <div className="mt-3 flex items-center justify-center text-[11.5px] font-medium text-emerald-700">
                                <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 ring-1 ring-emerald-100">
                                    <CheckCircle2 size={13} /> Отработана · {fmtDateTime(item.closed_at)}
                                </span>
                            </div>
                        )}
                    </div>
                    {!atBottom && (
                        <button type="button" title="К свежим сообщениям"
                                onClick={() => threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight, behavior: 'smooth' })}
                                className="absolute bottom-4 left-1/2 z-10 grid h-9 w-9 -translate-x-1/2 place-items-center rounded-full bg-white text-slate-500 shadow-[0_4px_14px_rgba(15,23,42,0.18)] ring-1 ring-slate-200/70 transition hover:text-slate-800 active:scale-95">
                            <ArrowDown size={16} />
                        </button>
                    )}
                </div>

                {asideOpen && (
                    <button type="button" aria-label="Скрыть данные жалобы" onClick={toggleAside}
                            className="absolute inset-0 z-10 bg-slate-900/25 lg:hidden" />
                )}
                <aside className={`crm-aside ${asideOpen ? 'is-open' : ''}`}
                       aria-hidden={!asideOpen} aria-label="Данные жалобы">
                    <div className="crm-aside-body">
                        <div className="flex shrink-0 items-center gap-2 border-b border-slate-200/70 px-3.5 py-2.5">
                            <FileText size={13} className="text-slate-400" />
                            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                Жалоба №{item.id}
                            </span>
                            <button type="button" onClick={toggleAside} aria-label="Скрыть данные жалобы"
                                    className="ml-auto grid h-6 w-6 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-600">
                                <X size={13} />
                            </button>
                        </div>
                        <div className="crm-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain px-3.5 py-3">
                            {facts}
                        </div>
                    </div>
                </aside>
            </div>

            <div className="shrink-0 border-t border-slate-200/70 bg-white px-4 py-3">
                {permissions.can_write ? (
                    <div className="flex items-end gap-2">
                        <button type="button" onClick={() => fileRef.current?.click()} title="Прикрепить файл"
                                className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500 transition hover:bg-slate-200 active:scale-95">
                            <Paperclip size={15} />
                        </button>
                        <input ref={fileRef} type="file" className="hidden"
                               onChange={(e) => setAttachment(e.target.files?.[0] || null)} />
                        <div className="min-w-0 flex-1">
                            {attachment && (
                                <div className="mb-1.5 inline-flex items-center gap-1.5 rounded-lg bg-slate-100 px-2 py-1 text-[11.5px] text-slate-600">
                                    <Paperclip size={11} /> {attachment.name}
                                    <button type="button" aria-label="Убрать файл"
                                            onClick={() => { setAttachment(null); if (fileRef.current) fileRef.current.value = ''; }}
                                            className="text-slate-400 hover:text-slate-600">
                                        <X size={11} />
                                    </button>
                                </div>
                            )}
                            <textarea value={reply} onChange={(e) => setReply(e.target.value)}
                                      onKeyDown={(e) => {
                                          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
                                      }}
                                      rows={1}
                                      placeholder={question ? 'Ответ на вопрос группы…' : 'Написать в группу…'}
                                      className={`${iosInput} block resize-none leading-5`} />
                        </div>
                        <button type="button" onClick={send} aria-label="Отправить"
                                disabled={sending || (!reply.trim() && !attachment)}
                                className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-blue-600 text-white transition hover:bg-blue-700 active:scale-95 disabled:opacity-40">
                            {sending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
                        </button>
                    </div>
                ) : (
                    <div className="text-center text-[12px] text-slate-400">
                        {item.requires_processing === false
                            ? `Жалоба ${whereabouts(item) || 'не в группе'}`
                            : item.status === 'closed' ? 'Жалоба отработана' : 'Писать в группу по этой жалобе нельзя'}
                    </div>
                )}
            </div>
        </div>
    );
};

const DeleteWarning = ({ many = false }) => (
    <div className="space-y-2 text-[13px] leading-relaxed text-slate-600">
        <p>
            {many
                ? 'Обращения и вся переписка по ним исчезнут'
                : 'Обращение и вся переписка по нему исчезнут'} из раздела: ни в
            списке, ни в поиске по ИИН, ни в истории их больше не будет.
            Вернуть нельзя.
        </p>
        <p className="text-slate-500">Сообщения, которые бот уже отправил в
           Telegram-группу, в ней останутся — чужую переписку в рабочем чате мы
           не стираем.</p>
    </div>
);

/* ─── Куда уходят темы тематики ───────────────────────────────────────────── */

/* Тема уходит в чат своей тематики — это адрес по умолчанию. Здесь его можно
 * перебить у ОДНОЙ темы: «Отображается оплата» отправить в чат «Sapar/Kaspi —
 * отмена», оставив остальные темы Sapar на месте.
 *
 * Выбирают ЧАТ, а не соседнюю тематику. Через тематики адресом могли бы стать
 * только они же, и ради одного рабочего чата пришлось бы заводить тематику,
 * которой ни одна тема не принадлежит, — сущность ради адреса. Бот и так знает
 * все группы, где состоит, и список берётся оттуда же, откуда его берёт
 * привязка тематики.
 *
 * Список тем — из каталога сценариев, а не из crm_topics. Темы объявлены в
 * crm/scenarios.py: у них есть вопросы, проверки и правила, и адресовать надо
 * именно их. Свободный список названий в crm_topics со сценариями связан не был
 * (на проде он пуст, topic_id у всех обращений NULL), и два списка с одинаковой
 * подписью в одной карточке заставляли бы гадать, какой из них настоящий.
 *
 * Карточка отвечает на два вопроса сразу: «куда уходят МОИ темы» — список с
 * выбором, и «что ещё приходит в ЭТОТ чат» — строка внизу. Без второго
 * уведённая тема оставалась бы невидимой для той группы, которая её получает.
 */
const TopicRouting = ({ queue, chats, scenarios, onRoute, pending }) => {
    // Тема без адреса (из неё в группу ничего не уходит) в маршрутах не стоит:
    // настройка, которая ни на что не влияет, — обещание, которого нет.
    const own = useMemo(
        () => scenarios.filter((item) => item.sends_to_group && item.queue_code === queue.code),
        [scenarios, queue.code],
    );
    /* Темы ЧУЖИХ тематик, уведённые в этот же чат. Сравниваем по chat_id, а не
       по тематике: чат — единственное, что у них общего с этой карточкой. */
    const incoming = useMemo(
        () => scenarios.filter((item) => item.sends_to_group && item.routed
            && queue.chat_id && String(item.chat_id) === String(queue.chat_id)
            && item.queue_code !== queue.code),
        [scenarios, queue.chat_id, queue.code],
    );

    /* В КНОПКЕ списка стоит просто название группы — и у своей, и у чужой. Так
       строка отвечает ровно на тот вопрос, который задают («куда уйдёт»), и не
       повторяет в каждой теме заголовок карточки. Что «своя» — это своя,
       объясняют заголовки-разделители внутри списка: они видны там, где выбор и
       делается, и не занимают место всё остальное время. Отличить умолчание от
       маршрута можно по бейджу, и другого отличия нет: выбрать чат тематики —
       и есть возврат к умолчанию (сервер стирает маршрут). */
    const optionsFor = (item) => {
        const list = [
            { value: '', label: queue.chat_title || 'Группа не выбрана',
              groupLabel: 'Чат тематики' },
            ...chats
                .filter((chat) => String(chat.chat_id) !== String(queue.chat_id))
                .map((chat) => ({ value: String(chat.chat_id), label: chat.title,
                                  groupLabel: 'Другие группы бота' })),
        ];
        // Из группы могли выгнать бота. Не показав её, выпадающий список соврал
        // бы «чат тематики» там, где адрес совсем другой.
        if (item.routed && item.chat_id
            && !list.some((option) => option.value === String(item.chat_id))) {
            list.push({
                value: String(item.chat_id),
                label: `${item.chat_title || 'Группа'} — бот не в ней`,
                groupLabel: 'Другие группы бота',
            });
        }
        return list;
    };

    return (
        <div className="mt-3 border-t border-slate-100 pt-3">
            <div className="flex items-center justify-between gap-2">
                <div className={iosGroupLabel}>Темы и их группа</div>
                {!!incoming.length && (
                    <span className="text-[11.5px] text-slate-400">
                        + {incoming.length} из других тематик
                    </span>
                )}
            </div>

            {!own.length && (
                <div className="mt-1.5 text-[11.5px] leading-snug text-slate-500">
                    Своих тем у этой тематики нет.
                </div>
            )}

            {!!own.length && (
                <div className="mt-1.5 divide-y divide-slate-100">
                    {own.map((item) => (
                        <div key={item.key}
                             className="flex flex-wrap items-center gap-x-2 gap-y-1.5 py-2">
                            <div className="min-w-0 flex-1">
                                <div className="truncate text-[13px] text-slate-800">{item.title}</div>
                                {item.routed && !item.chat_known && (
                                    <div className="mt-0.5 text-[11.5px] text-amber-600">
                                        Бот не состоит в этой группе — тема не предлагается оператору
                                    </div>
                                )}
                            </div>
                            {item.routed && <IosBadge tone="blue">Другая группа</IosBadge>}
                            <CustomSelect
                                className="w-full sm:w-[250px]"
                                variant="ios"
                                value={item.routed && item.chat_id ? String(item.chat_id) : ''}
                                onChange={(value) => onRoute(item, value)}
                                options={optionsFor(item)}
                                disabled={pending === item.key}
                                searchable
                                ariaLabel={`Куда уходит тема «${item.title}»`}
                            />
                        </div>
                    ))}
                </div>
            )}

            {!!incoming.length && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11.5px] text-slate-500">
                    <CornerDownRight size={12} className="shrink-0 text-slate-400" />
                    <span>В этот же чат приходят:</span>
                    {incoming.map((item) => (
                        <span key={item.key}
                              className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-600">
                            {item.title}
                            <span className="text-slate-400"> · из «{item.home_queue_title}»</span>
                        </span>
                    ))}
                </div>
            )}
        </div>
    );
};

/* ─── Настройка очередей (админ) ──────────────────────────────────────────── */

const QueuesTab = ({ apiBaseUrl, headers, showToast, queues, scenarios, onReload }) => {
    const [chats, setChats] = useState([]);
    const [editing, setEditing] = useState(null);
    // Ключ темы, у которой сейчас меняется адрес: пока сервер отвечает, её
    // список заблокирован — второй выбор поверх первого дал бы гонку ответов.
    const [routing, setRouting] = useState(null);

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

    /* Группа темы. Пустое значение — вернуть в чат своей тематики; так же это
       понимает и сервер, поэтому «по умолчанию» не отдельная кнопка, а первая
       строка того же списка: выбор один, и делается он в одном месте. */
    const setRoute = async (item, value) => {
        const chatId = value ? Number(value) : null;
        setRouting(item.key);
        try {
            await axios.put(`${apiBaseUrl}/api/crm/routes/${item.key}`,
                { chat_id: chatId }, { headers: headers() });
            await onReload();
            const target = chats.find((chat) => String(chat.chat_id) === String(chatId));
            showToast?.(chatId
                ? `«${item.title}» уходит в «${target?.title || 'выбранную группу'}»`
                : `«${item.title}» уходит в чат своей тематики`, 'success');
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось изменить группу темы'), 'error');
        } finally {
            setRouting(null);
        }
    };

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="max-w-[640px] text-[12px] leading-snug text-slate-500">
                    Очередь — это тематика обращений и её рабочая Telegram-группа. Чтобы
                    группа появилась в списке, добавьте в неё бота — он запомнит чат сам.
                    Темы по умолчанию уходят в группу своей тематики; любую из них можно
                    отправить в другую группу бота, не трогая соседние.
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
                                {!!queue.mention_usernames?.length && (
                                    <>
                                        <span className="text-slate-300">·</span>
                                        <span>Отмечает: {queue.mention_usernames.map((name) => `@${name}`).join(' ')}</span>
                                    </>
                                )}
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

                    <TopicRouting queue={queue} chats={chats} scenarios={scenarios}
                                  onRoute={setRoute} pending={routing} />
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
                        <div>
                            {/* Ники ответственных. Список с сервера приходит
                                массивом, в поле он — строкой через пробел: так
                                его и вставляют из Telegram. */}
                            <div className={iosGroupLabel}>Кого отмечать в группе</div>
                            <input value={Array.isArray(editing.mention_usernames)
                                       ? editing.mention_usernames.map((name) => `@${name}`).join(' ')
                                       : (editing.mention_usernames || '')}
                                   onChange={(e) => setEditing((p) => ({ ...p, mention_usernames: e.target.value }))}
                                   placeholder="@ник ответственного — можно несколько через пробел"
                                   className={`mt-1.5 ${iosInput}`} />
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
    // Входы в тематики: очередь, у которой категорию выбирают ПОСЛЕ проверки
    // по ИИН (инструкция #230). Приезжают тем же запросом, что и каталог.
    const [scenarioEntries, setScenarioEntries] = useState([]);
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
    const [exportOpen, setExportOpen] = useState(false);

    /* Свои жалобы — в той же ленте (решение владельца 29.09.2026). Справочник
       раздела жалоб приезжает только тому, кто вправе их заводить; у остальных
       он null, и жалоб в разделе нет вовсе — ни направления в мастере, ни строк. */
    const [complaintsMeta, setComplaintsMeta] = useState(null);
    const [complaintItems, setComplaintItems] = useState([]);
    const [complaintsHasMore, setComplaintsHasMore] = useState(false);
    const [complaintsOffset, setComplaintsOffset] = useState(0);
    const [complaintsUnread, setComplaintsUnread] = useState(0);
    const [selectedComplaintId, setSelectedComplaintId] = useState(null);
    const complaintsEnabled = Boolean(complaintsMeta?.capabilities?.can_create);
    const selectedAny = selectedId || selectedComplaintId;

    const selectTicket = useCallback((ticketId) => {
        setSelectedComplaintId(null);
        setSelectedId(ticketId);
    }, []);
    const openComplaint = useCallback((complaintId) => {
        setSelectedId(null);
        setSelectedComplaintId(complaintId);
    }, []);
    const clearSelection = useCallback(() => {
        setSelectedId(null);
        setSelectedComplaintId(null);
    }, []);

    /* Режим отбора: лента вместо «открыть обращение» отмечает его к удалению.
       Отдельным режимом, а не действием у каждой строки, по двум причинам.
       Первая — цена в обычной работе: «три точки» у сорока строк это сорок
       мишеней на правом краю, из которых операторам не нужна ни одна (удалять
       может только администратор). Вторая — сама задача: чистят не по одному, а
       пачкой (после выката в разделе осталось полтора десятка прогонов на
       выдуманных ИИН), и открывать ради каждого карточку с перепиской — это
       пятнадцать лишних запросов и пятнадцать подтверждений. */
    const [selectMode, setSelectMode] = useState(false);
    const [picked, setPicked] = useState(() => new Set());
    const [confirmBulk, setConfirmBulk] = useState(false);
    const [bulkBusy, setBulkBusy] = useState(false);

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
    }, [tab, selectedAny]);

    /* «На проверку» — обращения, которые ждут решения этого человека как
       супервайзера (возврат задачи #297). Они чужие по определению, поэтому ни
       «Мои», ни фильтр статуса к ним не применяются, а своих жалоб в этом
       списке нет: проверка жалоб — в разделе «Жалобы». */
    const reviewing = stateFilter === REVIEW_FILTER;

    // Во время поиска выборка не сужается до «моих», поэтому и сегмент показывает
    // «Все»: подсвеченные «Мои» над списком с чужими обращениями — это не фильтр,
    // а неверная подпись к тому, что человек видит. То же — в «На проверку».
    const searching = mine && !searchApplied && !reviewing;

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
            setScenarioEntries(response.data.entries || []);
            setTaxiParks(response.data.reference?.taxi_parks || []);
        } catch (err) {
            setScenarioCatalog([]);
            setScenarioEntries([]);
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
        // Фильтр «Жалобы» — в ленте только жалобы, за обращениями не ходим.
        if (queueFilter === COMPLAINTS_FILTER) {
            setTickets([]);
            setHasMore(false);
            setOffset(0);
            setError(null);
            setLoading(false);
            return;
        }
        if (!silent) setLoading(true);
        try {
            const params = new URLSearchParams();
            const statuses = STATE_FILTERS.find((f) => f.key === stateFilter)?.statuses;
            if (statuses) params.set('status', statuses);
            if (queueFilter) params.set('queue_id', queueFilter);
            if (reviewing) {
                // Что именно «ждёт моей проверки», считает сервер — тем же
                // правилом, что число на сегменте и колокол.
                params.set('review', '1');
            } else if (mine && !searchApplied) {
                // Поиск сквозной: ищем по всем обращениям, иначе сотрудник не
                // увидит, что по этому водителю обращение уже завёл кто-то другой.
                params.set('mine', '1');
            }
            if (searchApplied) params.set('q', searchApplied);
            params.set('limit', String(PAGE_SIZE));
            params.set('offset', String(nextOffset));
            const response = await axios.get(`${apiBaseUrl}/api/crm/tickets?${params}`,
                { headers: headers() });
            // Ярус «непрочитано» фиксируем на загрузке: прочитанная строка не
            // уезжает из-под курсора до следующего перечитывания (feedMerge.js).
            const items = (response.data.items || []).map(withSortRank);
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
    }, [apiBaseUrl, headers, stateFilter, queueFilter, mine, searchApplied, reviewing]);

    /* Порядок ленты — как у обращений на сервере: в «Моих» без поиска
       непрочитанное наверху. Жалобам его передаём явно, иначе при склейке
       строки встали бы вперемешку (см. feedMerge.js). */
    const unreadFirst = searching;
    const complaintsShown = complaintsEnabled && !reviewing
        && (!queueFilter || queueFilter === COMPLAINTS_FILTER);

    /* Свои жалобы — всегда только свои, и в «Все» и в поиске: жалобы коллег
       оператору не видны (сотрудник, на которого жалуются, может сидеть рядом). */
    const loadComplaints = useCallback(async (nextOffset = 0, silent = false) => {
        if (!complaintsShown) {
            setComplaintItems([]);
            setComplaintsHasMore(false);
            setComplaintsOffset(0);
            return;
        }
        try {
            const params = new URLSearchParams();
            params.set('segment', 'mine');
            const status = complaintStatusFor(stateFilter);
            if (status) params.set('status', status);
            if (searchApplied) params.set('q', searchApplied);
            if (unreadFirst) params.set('unread_first', '1');
            params.set('limit', String(PAGE_SIZE));
            params.set('offset', String(nextOffset));
            const response = await axios.get(`${apiBaseUrl}/api/complaints/complaints?${params}`,
                { headers: headers() });
            // Ярус «непрочитано» фиксируем на загрузке: прочитанная строка не
            // уезжает из-под курсора до следующего перечитывания (feedMerge.js).
            const items = (response.data.items || []).map(withSortRank);
            setComplaintItems((prev) => (nextOffset ? mergeTicketsById(prev, items) : items));
            setComplaintsHasMore(Boolean(response.data.has_more));
            setComplaintsOffset(nextOffset);
        } catch (err) {
            if (!silent) showToast?.(errorText(err, 'Не удалось загрузить жалобы'), 'error');
        }
    }, [apiBaseUrl, headers, complaintsShown, stateFilter, searchApplied, unreadFirst, showToast]);

    /* Ответы и вопросы по своим жалобам — к бейджу раздела: колокол считает
       их вместе с обращениями (notifications/sources.py: crm). */
    const refreshComplaintCounters = useCallback(async () => {
        try {
            const response = await axios.get(`${apiBaseUrl}/api/complaints/ping`, { headers: headers() });
            setComplaintsUnread(Math.max(0, Number(response.data.counters?.unread) || 0));
        } catch (err) { /* число в меню — не повод показывать отказ */ }
    }, [apiBaseUrl, headers]);

    /* Жалобы в разделе — только у того, кто вправе их заводить (СЗоВ, админ).
       Остальным /api/complaints отвечает отказом, и раздел молча живёт без них. */
    useEffect(() => {
        let cancelled = false;
        axios.get(`${apiBaseUrl}/api/complaints/meta`, { headers: headers() })
            .then((response) => {
                if (cancelled || !response.data?.capabilities?.can_create) return;
                setComplaintsMeta(response.data);
                refreshComplaintCounters();
            })
            .catch(() => { /* жалоб у этого пользователя нет */ });
        return () => { cancelled = true; };
    }, [apiBaseUrl, headers, refreshComplaintCounters]);

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
    useEffect(() => { loadComplaints(0); }, [loadComplaints]);

    /* Реалтайм. Собственного канала раздел не открывает: «тычок» уже приходит
       колоколу по SSE, и App отдаёт его сюда счётчиком. Второй поток на
       пользователя занял бы ещё одну нить waitress — их на сервере считаные. */
    useEffect(() => {
        if (!realtimePulse) return;
        loadTickets(0, true);
        // Числа шапки — тоже по тычку: «На проверку» появляется у супервайзера
        // в ту минуту, когда оператор завёл обращение, а не при следующем входе.
        refreshCounters();
        if (complaintsEnabled) {
            loadComplaints(0, true);
            refreshComplaintCounters();
        }
    }, [realtimePulse]); // eslint-disable-line react-hooks/exhaustive-deps

    // Бейдж раздела в сайдбаре ведёт сервер — здесь только передаём наверх:
    // непрочитанное по обращениям и по своим жалобам плюс обращения, которые
    // ждут проверки этого человека, — ровно как считает колокол
    // (notifications/sources.py: crm). Разойдись суммы, число в меню прыгало бы
    // между двумя значениями: его ставят и колокол, и раздел.
    useEffect(() => {
        if (counters.unread === undefined) return;
        onUnreadChange?.((Number(counters.unread) || 0) + (Number(counters.review) || 0)
            + complaintsUnread);
    }, [counters.unread, counters.review, complaintsUnread, onUnreadChange]);

    // Сегменты состояния: «На проверку» есть только у того, кого она ждёт.
    const filters = useMemo(
        () => stateFilters(STATE_FILTERS, { reviewCount: counters.review, selected: stateFilter }),
        [counters.review, stateFilter],
    );

    /* Переход из колокола: открываем именно то обращение, о котором уведомили.
       Карточка грузится по своему id, поэтому фильтр списка её не прячет —
       иначе «Пришёл ответ» по уже решённому обращению вёл бы в пустоту.
       requestId в зависимостях, а не ticketId: повторный клик по тому же
       уведомлению должен снова открыть карточку. */
    useEffect(() => {
        // Жалоба из колокола или по старой ссылке ?view=complaints — своя цель:
        // номера у жалоб и обращений разные, и голое число открыло бы обращение.
        if (focusRequest?.complaintId) {
            openComplaint(Number(focusRequest.complaintId));
            setTab('tickets');
            return;
        }
        if (!focusRequest?.ticketId) return;
        selectTicket(Number(focusRequest.ticketId));
        setTab('tickets');
    }, [focusRequest?.requestId]); // eslint-disable-line react-hooks/exhaustive-deps

    const canManage = !!capabilities?.can_manage_queues;
    const canDelete = !!capabilities?.is_global_admin;
    const readyScenarios = useMemo(
        () => scenarioCatalog.filter((item) => item.is_ready),
        [scenarioCatalog],
    );

    /* Пересчитать числа шапки. Тот же /ping, что при входе, но без разбора
       «схема развернулась»: к моменту удаления раздел давно открыт.
       Нужен именно после удаления: counters.unread ведёт бейдж раздела в
       сайдбаре, и без пересчёта в меню осталась бы цифра, за которой уже
       ничего нет. На фильтры и на буквы в поиске этот запрос не ходит — там он
       был бы проходом по таблице ни за чем. */
    const refreshCounters = useCallback(async () => {
        try {
            const response = await axios.get(`${apiBaseUrl}/api/crm/ping`,
                { headers: headers() });
            setCounters(response.data.counters || {});
        } catch (err) { /* числа в шапке — не повод показывать отказ */ }
    }, [apiBaseUrl, headers]);

    /* В карточке что-то сделали: ответили, закрыли, решили при проверке — лента
       перечитывается. Числа шапки — только когда карточка об этом попросила:
       решение супервайзера гасит его задачу и цифру на сегменте «На проверку»,
       а ответ в группу или смена статуса чисел не трогают, и ходить за ними на
       каждую реплику было бы платой за ничего. */
    const refreshAfterChange = useCallback((changed) => {
        loadTickets(0, true);
        if (changed?.counters) refreshCounters();
    }, [loadTickets, refreshCounters]);

    const exitSelect = useCallback(() => {
        setSelectMode(false);
        setPicked(new Set());
    }, []);

    /* Отметить/снять строку. setState функцией: отметок бывает пятнадцать, и
       замыкание на прежний Set потеряло бы половину из них. */
    const togglePicked = useCallback((ticketId) => {
        setPicked((prev) => {
            const next = new Set(prev);
            if (next.has(ticketId)) next.delete(ticketId); else next.add(ticketId);
            return next;
        });
    }, []);

    /* Отобранные — в том порядке, в котором они лежат в ленте: этот же список
       показывается в вопросе перед удалением, и «что я отметил» человек должен
       читать там же, где отмечал. Отметки строк, уехавших из ленты после
       смены фильтра, здесь отсеиваются сами. */
    const pickedTickets = useMemo(
        () => tickets.filter((item) => picked.has(item.id)),
        [tickets, picked],
    );

    /* Удаление отобранных. Запросы ИДУТ ПО ОДНОМУ, а не Promise.all: у сервера
       считаное число рабочих нитей (waitress), и пятнадцать параллельных
       удалений заняли бы их все — раздел встал бы у всех остальных.
       Пятнадцать запросов подряд — это пара секунд, и на это время кнопка
       заблокирована.

       Отказ по одному обращению не отменяет остальные: у 403 и 404 разные
       причины (чужой периметр, кто-то удалил раньше), и бросать на первом же
       значило бы оставить работу недоделанной без объяснения. */
    const deletePicked = async () => {
        const ids = pickedTickets.map((item) => item.id);
        if (!ids.length) return;
        setBulkBusy(true);
        const removed = [];
        let lastError = null;
        for (const id of ids) {
            try {
                await axios.delete(`${apiBaseUrl}/api/crm/tickets/${id}`,
                    { headers: headers() });
                removed.push(id);
            } catch (err) {
                lastError = err;
            }
        }
        setBulkBusy(false);
        setConfirmBulk(false);
        exitSelect();
        if (selectedId && removed.includes(selectedId)) setSelectedId(null);
        if (removed.length) {
            showToast?.(
                `Удалено ${removed.length} ${pluralTickets(removed.length)}`
                + (ids.length > removed.length ? `, не удалось — ${ids.length - removed.length}` : ''),
                ids.length > removed.length ? 'error' : 'success',
            );
        } else {
            showToast?.(errorText(lastError, 'Не удалось удалить'), 'error');
        }
        loadTickets(0);
        refreshCounters();
    };

    /* Обращение удалили из карточки: она закрывается, а лента и числа шапки
       перечитываются — строки и её непрочитанного больше нет. */
    const handleDeleted = useCallback(() => {
        setSelectedId(null);
        loadTickets(0, true);
        refreshCounters();
    }, [loadTickets, refreshCounters]);

    /* Настройка очередей и каталог тем перезагружаются ВМЕСТЕ: адрес темы
       живёт в каталоге (/scenarios), а список очередей — в /queues, и любая
       правка на вкладке настройки меняет обе стороны. Перезагрузив одну,
       вкладка показала бы новый адрес рядом со старым списком очередей. */
    const reloadSetup = useCallback(
        () => Promise.all([loadQueues(), loadScenarios()]),
        [loadQueues, loadScenarios],
    );

    /* Карточка сообщает, что сервер погасил «непрочитано», — и лента гасит
       пузырёк у этой строки, не перезапрашиваясь.
       Функция ОБЯЗАНА быть стабильной: она уходит в зависимости load() внутри
       карточки, и новая ссылка на каждый рендер раздела означала бы
       перезагрузку карточки от любого чиха выше (в проекте это уже случалось
       с showToast). Поэтому setState функцией и пустые зависимости. */
    const handleSeen = useCallback((ticketId) => {
        setTickets((prev) => markTicketSeen(prev, ticketId));
    }, []);

    /* То же для жалобы: открытие карточки автором гасит «непрочитано» на
       сервере. Стабильна по той же причине — уходит в load() карточки;
       счётчик читается через ref, чтобы не тянуть его в зависимости. */
    const refreshComplaintCountersRef = useRef(refreshComplaintCounters);
    refreshComplaintCountersRef.current = refreshComplaintCounters;
    const handleComplaintSeen = useCallback((complaintId) => {
        setComplaintItems((prev) => prev.map((item) => (item.id === complaintId
            ? { ...item, unread: false, unread_kind: null, unread_count: 0 } : item)));
        refreshComplaintCountersRef.current();
    }, []);

    const handleComplaintChanged = useCallback(() => {
        loadComplaints(0, true);
    }, [loadComplaints]);

    /* Вся лента одной склейкой. В отборе к удалению жалоб нет: он чистит
       обращения, а жалобы удаляет только администратор в разделе «Жалобы». */
    const feed = useMemo(() => mergeFeeds({
        tickets,
        ticketsMore: hasMore,
        complaints: selectMode ? [] : complaintItems,
        complaintsMore: selectMode ? false : complaintsHasMore,
        unreadFirst,
    }), [tickets, hasMore, complaintItems, complaintsHasMore, selectMode, unreadFirst]);

    const loadMoreFeed = () => {
        if (feed.loadMore === 'complaints') loadComplaints(complaintsOffset + PAGE_SIZE);
        else loadTickets(offset + PAGE_SIZE);
    };

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
                    {/* Выгрузка в Excel за период — у СВ, главы и админа
                        (capabilities.can_export). В режиме отбора её нет: там
                        идёт чистка, и третья кнопка в шапке — лишняя. */}
                    {tab === 'tickets' && capabilities?.can_export && !selectMode && (
                        <button type="button" onClick={() => setExportOpen(true)}
                                title="Выгрузить обращения за период в Excel"
                                className={iosBtnGhost}>
                            <Download size={14} /> Выгрузить
                        </button>
                    )}
                    {/* Отбор к удалению — только у администратора и только на
                        вкладке обращений. Пока он идёт, «Новое обращение» с
                        экрана убрано: заводить обращение посреди чистки никто
                        не собирается, а две главные кнопки рядом заставляют
                        выбирать между несравнимыми действиями. */}
                    {tab === 'tickets' && canDelete && !!tickets.length && (
                        selectMode ? (
                            <button type="button" onClick={exitSelect} className={iosBtnSecondary}>
                                Готово
                            </button>
                        ) : (
                            <button type="button"
                                    onClick={() => { setSelectMode(true); clearSelection(); }}
                                    title="Отобрать обращения и удалить"
                                    className={iosBtnGhost}>
                                <ListChecks size={14} /> Выбрать
                            </button>
                        )
                    )}
                    {tab === 'tickets' && !selectMode && (
                        <button type="button" onClick={() => setComposerOpen(true)}
                                disabled={!readyScenarios.length && !complaintsEnabled}
                                title={readyScenarios.length || complaintsEnabled ? undefined
                                    : 'Ни к одной тематике не привязана Telegram-группа'}
                                className={iosBtnPrimary}>
                            <Plus size={14} /> Новое обращение
                        </button>
                    )}
                </div>
            </div>

            {tab === 'queues' && canManage && (
                <QueuesTab apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast}
                           queues={queues} scenarios={scenarioCatalog}
                           onReload={reloadSetup} />
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
                             selectedAny ? 'hidden' : 'flex'
                         }`}>
                        <div className="flex rounded-xl bg-slate-100 p-1">
                            {filters.map((item) => (
                                <button key={item.key} type="button"
                                        onClick={() => {
                                            setStateFilter(item.key);
                                            // Очередь проверки — вся, а не «в выбранной
                                            // группе»: число на сегменте считает все
                                            // обращения, и список за ним обязан их показать.
                                            if (item.key === REVIEW_FILTER) setQueueFilter('');
                                            clearSelection();
                                        }}
                                        className={`rounded-[9px] px-3 py-1.5 text-[12.5px] font-semibold transition-all ${
                                            stateFilter === item.key
                                                ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                                                : 'text-slate-500 hover:text-slate-700'
                                        } ${item.count ? 'inline-flex items-center gap-1.5' : ''}`}>
                                    {item.label}
                                    {/* Число — только у «На проверку»: это
                                        очередь задач, а не отбор. Жёлтое, как
                                        бейдж «Ждёт проверки» у самих строк. */}
                                    {!!item.count && (
                                        <span className="grid h-[17px] min-w-[17px] place-items-center rounded-full bg-amber-100 px-1 text-[10.5px] font-semibold tabular-nums leading-none text-amber-700">
                                            {unreadLabel(item.count)}
                                        </span>
                                    )}
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
                                            disabled={Boolean(searchApplied) || reviewing}
                                            title={searchApplied ? 'Поиск идёт по всем обращениям'
                                                : reviewing ? 'На проверку приходят обращения ваших операторов'
                                                    : undefined}
                                            onClick={() => { setMine(item.key); clearSelection(); }}
                                            className={`rounded-[9px] px-3 py-1.5 text-[12.5px] font-semibold transition-all ${
                                                searching === item.key
                                                    ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                                                    : 'text-slate-500 hover:text-slate-700'
                                            } ${searchApplied || reviewing ? 'cursor-not-allowed opacity-60' : ''}`}>
                                        {item.label}
                                    </button>
                                ))}
                            </div>
                        )}

                        {/* «Жалобы» — отдельной строкой в конце: у них своя
                            группа, и так их можно посмотреть без обращений.
                            В «На проверку» этой строки нет: жалоб в очереди
                            проверки не бывает, и выбор оставил бы пустой
                            список под сегментом с числом. */}
                        {queues.length + (complaintsEnabled ? 1 : 0) > 1 && (
                            <CustomSelect
                                className="w-48"
                                variant="ios"
                                value={queueFilter}
                                onChange={(value) => { setQueueFilter(value); clearSelection(); }}
                                options={[{ value: '', label: 'Все группы' }].concat(
                                    queues.map((q) => ({ value: String(q.id), label: q.title })),
                                    complaintsEnabled && !reviewing
                                        ? [{ value: COMPLAINTS_FILTER, label: 'Жалобы' }] : [],
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
                            {/* shrink-0 только с lg. На широком экране колонки
                                стоят в РЯД, и запрет на сжатие держит ленте её
                                360 px. На телефоне направление меняется на
                                колонку — и тот же запрет означал «не становись
                                ниже своего содержимого»: лента вырастала до
                                высоты всех строк (874 px на одиннадцати),
                                вылезала за карточку с overflow-hidden и
                                обрезалась. Внутренняя прокрутка при этом не
                                включалась никогда — scrollHeight равнялся
                                clientHeight, — а страница не ехала, потому что
                                лишнее не выходило за пределы карточки, а
                                исчезало в ней. Всё, что не влезло в первый
                                экран (строки, подвал «Показано N», кнопка
                                удаления отобранных), было недостижимо.
                                Теперь колонка занимает высоту карточки, а
                                прокручивается лента внутри — как на широком. */}
                            <div className={`flex w-full min-h-0 flex-col border-slate-200/70 lg:w-[360px] lg:shrink-0 lg:border-r ${
                                selectedAny ? 'hidden lg:flex' : 'flex'
                            }`}>
                                <div className="crm-scroll min-h-0 flex-1 overflow-y-auto">
                                    {loading && !feed.items.length && <LoadingBlock />}
                                    {!loading && error && (
                                        <div className="flex items-center justify-center gap-2 py-16 text-center text-[13px] text-rose-500">
                                            <AlertCircle size={15} /> {error}
                                        </div>
                                    )}
                                    {!loading && !error && !feed.items.length && (
                                        <EmptyBlock
                                            hint={reviewing
                                                ? 'Сюда приходят обращения ваших операторов, которые сначала проверяет супервайзер.'
                                                : mine
                                                    ? 'Создайте обращение — оно уйдёт в рабочую группу, а ответ вернётся сюда.'
                                                    : 'В этом фильтре пусто.'}>
                                            {reviewing ? 'Проверять нечего'
                                                : queueFilter === COMPLAINTS_FILTER ? 'Жалоб нет' : 'Обращений нет'}
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
                                        {feed.items.map((entry) => (entry.kind === 'complaint' ? (
                                            <ComplaintFeedRow key={entry.key} complaint={entry.item}
                                                              active={entry.item.id === selectedComplaintId}
                                                              onSelect={openComplaint} />
                                        ) : (
                                            <TicketRow key={entry.key} ticket={entry.item}
                                                       active={entry.item.id === selectedId}
                                                       onSelect={selectTicket}
                                                       selectable={selectMode}
                                                       selected={picked.has(entry.item.id)}
                                                       onToggle={togglePicked} />
                                        )))}
                                    </div>
                                    {!!feed.loadMore && (
                                        <button type="button" onClick={loadMoreFeed}
                                                className="w-full py-3 text-[12.5px] font-semibold text-slate-500 transition hover:bg-slate-50">
                                            Показать ещё
                                        </button>
                                    )}
                                </div>
                                {/* Подвал ленты. В режиме отбора на месте
                                    «Показано N» стоит сама работа: сколько
                                    отмечено и кнопка удаления. Двух подвалов
                                    друг под другом лента не выдержит — она и так
                                    самая узкая колонка раздела. */}
                                {!!feed.items.length && (selectMode ? (
                                    <div className="flex shrink-0 items-center gap-2 border-t border-slate-100 px-3.5 py-2">
                                        <span className="text-[11.5px] tabular-nums text-slate-500">
                                            {picked.size
                                                ? `Отмечено ${picked.size}`
                                                : 'Отметьте обращения'}
                                        </span>
                                        <button type="button" onClick={() => setConfirmBulk(true)}
                                                disabled={!picked.size || bulkBusy}
                                                className="ml-auto inline-flex items-center gap-1.5 rounded-xl bg-rose-600 px-3 py-1.5 text-[12.5px] font-semibold text-white transition-all hover:bg-rose-700 active:scale-[0.98] disabled:opacity-40">
                                            {bulkBusy
                                                ? <Loader2 size={13} className="animate-spin" />
                                                : <Trash2 size={13} />}
                                            Удалить
                                        </button>
                                    </div>
                                ) : (
                                    <div className="shrink-0 border-t border-slate-100 px-3.5 py-2 text-[11px] tabular-nums text-slate-400">
                                        Показано {feed.items.length}{feed.loadMore ? ' — есть ещё' : ''}
                                    </div>
                                ))}
                            </div>

                            {/* Карточка (про min-h-0 — см. выше) */}
                            <div className={`min-h-0 min-w-0 flex-1 ${selectedAny ? 'flex' : 'hidden lg:flex'}`}>
                                {selectedComplaintId ? (
                                    /* Своя жалоба — глазами автора: ответ для
                                       водителя, вопрос группы и ответ на него.
                                       Разбор и работа с сотрудником — в разделе
                                       «Жалобы», у тех, кто разбирает. */
                                    <div className="h-full min-h-0 w-full">
                                        <ComplaintThreadCard
                                            key={`complaint:${selectedComplaintId}`}
                                            complaintId={selectedComplaintId}
                                            apiBaseUrl={apiBaseUrl}
                                            headers={headers}
                                            showToast={showToast}
                                            onBack={clearSelection}
                                            onChanged={handleComplaintChanged}
                                            onSeen={handleComplaintSeen}
                                            pulse={realtimePulse}
                                        />
                                    </div>
                                ) : selectedId ? (
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
                                            onDeleted={handleDeleted}
                                            pulse={realtimePulse}
                                        />
                                    </div>
                                ) : (
                                    <div className="flex w-full items-center justify-center">
                                        {/* В режиме отбора нажатие по строке
                                            переписку НЕ открывает, и обещать её
                                            здесь значило бы объяснять человеку
                                            не то, что произойдёт от его
                                            следующего действия. */}
                                        {selectMode ? (
                                            <EmptyBlock icon={ListChecks}
                                                        hint="Отмеченные удалятся вместе с перепиской. «Готово» — выйти из отбора, ничего не удаляя.">
                                                Отметьте обращения слева
                                            </EmptyBlock>
                                        ) : (
                                            <EmptyBlock icon={ChevronRight}
                                                        hint="Слева — обращения в работе. Выберите любое, чтобы увидеть переписку с группой.">
                                                Выберите обращение
                                            </EmptyBlock>
                                        )}
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                </>
            )}

            {/* Вопрос перед удалением пачки. Список отобранного здесь не для
                красоты: отметок пятнадцать, они набирались минуту, и «что
                именно сейчас исчезнет» человек обязан увидеть одним взглядом —
                иначе подтверждение подтверждает не то, что он думает. */}
            <IosModal
                open={confirmBulk}
                onClose={() => { if (!bulkBusy) setConfirmBulk(false); }}
                title={`Удалить ${picked.size} ${pluralTickets(picked.size)}?`}
                maxWidth="max-w-md"
                footer={(
                    <>
                        <button type="button" onClick={() => setConfirmBulk(false)}
                                disabled={bulkBusy} className={iosBtnSecondary}>
                            Отмена
                        </button>
                        <button type="button" onClick={deletePicked} disabled={bulkBusy}
                                className={iosBtnDanger}>
                            {bulkBusy
                                ? <Loader2 size={14} className="animate-spin" />
                                : <Trash2 size={14} />}
                            Удалить
                        </button>
                    </>
                )}
            >
                <div className="space-y-3">
                    <DeleteWarning many={picked.size > 1} />
                    <div className="max-h-[220px] overflow-y-auto rounded-xl bg-white p-2 ring-1 ring-slate-200/70">
                        {pickedTickets.map((item) => (
                            <div key={item.id}
                                 className="flex items-baseline gap-2 px-1 py-1 text-[12px] text-slate-600">
                                <span className="shrink-0 tabular-nums text-slate-400">
                                    №{item.id}
                                </span>
                                <span className="min-w-0 flex-1 truncate">{item.subject}</span>
                                <span className="shrink-0 text-[11px] text-slate-400">
                                    {item.created_by_name || '—'}
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
            </IosModal>

            <TicketsExport open={exportOpen} onClose={() => setExportOpen(false)}
                           apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} />

            <TicketWizard
                open={composerOpen}
                onClose={() => setComposerOpen(false)}
                catalog={scenarioCatalog}
                entries={scenarioEntries}
                taxiParks={taxiParks}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                showToast={showToast}
                onCreated={(id) => { selectTicket(id); loadTickets(0, true); }}
                complaints={complaintsEnabled ? complaintsMeta : null}
                onComplaintCreated={(id) => {
                    openComplaint(id);
                    loadComplaints(0, true);
                    refreshComplaintCounters();
                }}
            />
        </div>
    );
}
