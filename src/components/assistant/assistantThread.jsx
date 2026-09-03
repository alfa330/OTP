import React from 'react';
import { AlertCircle, FileText, Quote, ThumbsDown, ThumbsUp } from 'lucide-react';
import { iosBtnGhost, IosBadge } from '../ui/ios';
import { ChatBubble } from '../ui/chat';
import Markdown from '../ui/markdown';

/* Лента ответов помощника — общая для вкладки в вике и для мини-чата шарика.
 *
 * Файл появился ровно по той же причине, по которой в проекте появился
 * src/components/ui/chat.jsx: там каркас чата успели скопировать дважды
 * (WazzupChatsView и ChatAppChatsView — почти построчные копии), и третьей
 * копией должен был стать помощник вики. Здесь третьей копией стал бы мини-чат.
 *
 * Копировать тут особенно нельзя. Почти каждое правило ниже — след разбора
 * происшествия на проде, и записано оно ОДИН раз:
 *
 *   * цитату извлекает сервер, поэтому она дословна, и флага «проверена» нет;
 *   * бейдж свежести стоит у НАЗВАНИЯ, а не под цитатой: 27.08.2026 слово
 *     «Архивные» в заголовке на экране было, но не выделялось, и оператор
 *     прочёл его как часть обычного названия;
 *   * оговорка про архив идёт ПЕРЕД припиской об ознакомлении: первая говорит,
 *     можно ли на ответ опираться, вторая — что сделать потом;
 *   * и приписка, и оговорка собираются ИЗ ИСТОЧНИКОВ, а не из notes ответа:
 *     при перезагрузке истории notes нет, и предупреждение исчезало бы ровно у
 *     старых ответов — тех, где протухшее вероятнее всего.
 *
 * Разъехавшись по двум файлам, эти правила разъедутся и по смыслу: живой ответ
 * и он же из истории начнут читаться по-разному.
 */

export const fmtTime = (iso) => {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
};

export const fmtChatDate = (iso) => {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    const today = new Date();
    const sameDay = date.toDateString() === today.toDateString();
    return sameDay
        ? date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
        : date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
};

export const errText = (error, fallback) => (
    error?.response?.data?.detail || error?.response?.data?.error || fallback);

/** Статьи ответа, требующие подтверждения ознакомления, без повторов. */
export const ackTitles = (sources) => {
    const seen = [];
    (sources || []).forEach((source) => {
        if (source.requires_ack && source.available !== false) {
            const title = source.title || '';
            if (title && !seen.includes(title)) seen.push(title);
        }
    });
    return seen;
};

/** Архивные источники ответа, без повторов. Как и ackTitles — ИЗ ИСТОЧНИКОВ. */
export const staleTitles = (sources, kind) => {
    const seen = [];
    (sources || []).forEach((source) => {
        if (source.stale && source.available !== false
                && (!kind || source.stale_kind === kind)) {
            const title = source.title || '';
            if (title && !seen.includes(title)) seen.push(title);
        }
    });
    return seen;
};

/* Формулировки те же, что у сервера (wiki/ai/answer.py: _STALE_NOTES):
   расходиться им нельзя. Архив и истёкший срок — РАЗНЫЕ утверждения, и сводить
   их в одну фразу значит говорить неправду про половину случаев. */
export const STALE_CAVEATS = [
    ['historical', 'Часть ответа взята из архивных материалов',
     'Проверьте, действует ли это сейчас, прежде чем обещать водителю.'],
    ['expired', 'Часть ответа взята из материалов с истёкшим сроком',
     'Проверьте даты, прежде чем обещать водителю.'],
];

/** Чип источника: название статьи, раздел и цитата под ним. */
export const SourceChip = ({ source, onOpen }) => {
    const unavailable = source.available === false;
    // attributed — фрагмент сопоставил сервер, модель его не назвала.
    const attributed = source.attributed === true;
    const stale = source.stale === true;
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
                {stale && !unavailable && (
                    <IosBadge tone="amber" className="shrink-0">
                        {source.stale_note || 'архив'}
                    </IosBadge>
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

/**
 * Одна реплика ленты — своя или ответ помощника со всей его обвязкой.
 *
 * compact сжимает ответ под узкую колонку мини-чата: пузырь занимает почти всю
 * ширину, а служебные подписи (модель, время ответа) прячутся. Прячутся именно
 * они, а НЕ источники и НЕ оговорки: 384 пикселя — повод убрать техническую
 * справку, но не повод перестать показывать, откуда взят ответ. Ровно на этом
 * держится доверие к помощнику, и в узкой панели оно нужно не меньше.
 */
export const AssistantMessage = ({ message, onOpenArticle, onFeedback, compact = false }) => {
    if (message.role === 'user') {
        return (
            <ChatBubble out meta={compact ? null : fmtTime(message.created_at)}>
                {message.text}
            </ChatBubble>
        );
    }

    const tone = message.kind === 'clarify'
        ? 'warn'
        : message.kind === 'no_answer' ? 'muted' : null;
    // Ширина обвязки идёт за пузырём: в узкой колонке источники, прижатые к
    // 78 %, отрывались бы от ответа, к которому относятся.
    const width = compact ? 'max-w-full' : 'max-w-[78%]';

    return (
        <div className="space-y-1.5">
            <ChatBubble
                tone={tone}
                plain={false}
                meta={compact ? null : (
                    <>
                        <span>{fmtTime(message.created_at)}</span>
                        {message.model && (
                            <span className="truncate opacity-70">{message.model}</span>
                        )}
                        {message.elapsed_ms != null && (
                            <span className="opacity-70">
                                {(message.elapsed_ms / 1000).toFixed(1)} с
                            </span>
                        )}
                    </>
                )}
            >
                {/* Ответ размечен: списки, выделения и ТАБЛИЦЫ — главный формат
                    справочных данных вики (город, цена, срок, парк). */}
                <Markdown text={message.text} />
            </ChatBubble>

            {STALE_CAVEATS.map(([kind, lead, tail]) => {
                const titles = staleTitles(message.sources, kind);
                if (!titles.length) return null;
                return (
                    <div key={kind} className="px-4">
                        <div className={`flex ${width} gap-2 rounded-xl bg-rose-50 px-3 py-2 text-[12px] text-rose-900 ring-1 ring-rose-200/70`}>
                            <AlertCircle size={14} className="mt-[1px] shrink-0" />
                            <span>
                                {lead}
                                {' — '}
                                {titles.map((title) => `«${title}»`).join(', ')}.
                                {' '}{tail}
                            </span>
                        </div>
                    </div>
                );
            })}

            {ackTitles(message.sources).map((title) => (
                <div key={title} className="px-4">
                    <div className={`${width} rounded-xl bg-amber-50 px-3 py-2 text-[12px] text-amber-900 ring-1 ring-amber-200/70`}>
                        Этот пункт входит в обязательное ознакомление
                        по статье «{title}» — подтвердите ознакомление
                        в самой статье.
                    </div>
                </div>
            ))}

            {!!(message.sources || []).length && (
                <div className="px-4">
                    <div className={`${width} space-y-1 rounded-xl bg-white p-1.5 ring-1 ring-slate-200/60`}>
                        <div className="px-1.5 pt-0.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                            Источники
                        </div>
                        {(message.sources || []).map((source, index) => (
                            <SourceChip
                                key={index}
                                source={source}
                                onOpen={(item) => onOpenArticle(item.slug, item.quote)}
                            />
                        ))}
                    </div>
                </div>
            )}

            {message.id && !String(message.id).startsWith('local-') && (
                <div className="flex gap-1 px-4">
                    <button
                        type="button"
                        onClick={() => onFeedback(message.id, 1)}
                        className={`${iosBtnGhost} ${message.feedback === 1 ? 'text-emerald-600' : ''}`}
                    >
                        <ThumbsUp size={13} /> Помогло
                    </button>
                    <button
                        type="button"
                        onClick={() => onFeedback(message.id, -1)}
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
};
