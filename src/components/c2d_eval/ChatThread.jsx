import React, { useEffect, useImperativeHandle, useMemo, useRef, useState, forwardRef } from 'react';
import { Loader2, AlertCircle, Bot, FileText, Headset, Quote, ExternalLink, X, Lock } from 'lucide-react';

/* Лента переписки снапшота (Chat2Desk/Wazzup) — общая для всех мест, где чат
 * показывается: док «Мои оценки» (ChatSnapshotModal) и полноэкранная проверка
 * низких оценок в «Учёте часов». Компонент презентационный: снапшот грузит
 * родитель, здесь только рендер, подсветка цитат и прокрутка к сообщению. */

export const fmtTime = (iso) => (iso ? new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : '');
export const fmtDay = (iso) => (iso ? new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }) : '');

export function highlightText(text, quoteTexts) {
    if (!text || !quoteTexts?.length) return text;
    let segments = [{ text, mark: false }];
    quoteTexts.forEach((quote) => {
        const q = String(quote || '');
        if (!q) return;
        const next = [];
        segments.forEach((seg) => {
            if (seg.mark) { next.push(seg); return; }
            const idx = seg.text.toLowerCase().indexOf(q.toLowerCase());
            if (idx === -1) { next.push(seg); return; }
            if (idx > 0) next.push({ text: seg.text.slice(0, idx), mark: false });
            next.push({ text: seg.text.slice(idx, idx + q.length), mark: true });
            if (idx + q.length < seg.text.length) next.push({ text: seg.text.slice(idx + q.length), mark: false });
        });
        segments = next;
    });
    return segments.map((seg, i) => seg.mark
        ? <mark key={i} className="rounded bg-amber-200/90 px-0.5 text-slate-900">{seg.text}</mark>
        : <React.Fragment key={i}>{seg.text}</React.Fragment>);
}

/* Лайтбокс фото — как в «Чатах Верификаторов»: просмотр поверх ленты,
 * «Открыть оригинал» отдельной кнопкой (без прыжка по ссылке при клике). */
function ImageLightbox({ url, onClose }) {
    useEffect(() => {
        const onKeyDown = (e) => {
            if (e.key !== 'Escape') return;
            // capture + stopImmediatePropagation: Esc закрывает только фото,
            // не задевая Esc-обработчик контейнера переписки.
            e.stopImmediatePropagation();
            e.preventDefault();
            onClose();
        };
        window.addEventListener('keydown', onKeyDown, true);
        return () => window.removeEventListener('keydown', onKeyDown, true);
    }, [onClose]);
    return (
        <div className="fixed inset-0 z-[200] flex cursor-zoom-out flex-col items-center justify-center bg-slate-900/80 p-6"
             onClick={onClose}>
            <img src={url} alt="" onClick={(e) => e.stopPropagation()}
                 className="max-h-[80vh] w-auto max-w-[92vw] cursor-default rounded-2xl bg-white shadow-2xl" />
            <div className="mt-3 flex items-center gap-3" onClick={(e) => e.stopPropagation()}>
                <a href={url} target="_blank" rel="noopener noreferrer"
                   className="inline-flex items-center gap-1.5 rounded-full bg-white/15 px-4 py-2 text-[13px] font-semibold text-white transition hover:bg-white/25">
                    <ExternalLink size={13} /> Открыть оригинал
                </a>
                <button onClick={onClose}
                        className="inline-flex items-center gap-1.5 rounded-full bg-white px-4 py-2 text-[13px] font-semibold text-slate-800 transition hover:bg-slate-100">
                    <X size={13} /> Закрыть
                </button>
            </div>
        </div>
    );
}

function Media({ msg, light }) {
    const [zoom, setZoom] = useState(null); // url фото в лайтбоксе
    const chip = `inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[12.5px] font-medium ${
        light ? 'bg-white/20 text-white' : 'bg-slate-100 text-slate-600'}`;
    const pieces = [];
    if (msg.photo) {
        pieces.push(
            <img key="photo" src={msg.photo} alt="" loading="lazy"
                 onClick={() => setZoom(msg.photo)}
                 className="max-h-56 w-auto max-w-full cursor-zoom-in rounded-xl" />
        );
    }
    if (msg.video) pieces.push(<video key="video" controls preload="metadata" src={msg.video} className="max-h-56 w-auto max-w-full rounded-xl" />);
    if (msg.audio) pieces.push(<audio key="audio" controls preload="none" src={msg.audio} className="h-9 w-60 max-w-full" />);
    if (msg.pdf) {
        pieces.push(
            <a key="pdf" href={msg.pdf} target="_blank" rel="noopener noreferrer" className={`${chip} hover:opacity-80`}>
                <FileText size={13} /> PDF
            </a>
        );
    }
    (msg.attachments || []).forEach((att, i) => {
        if (msg.photo && att.link === msg.photo) return;
        pieces.push(
            <a key={`att-${i}`} href={att.link} target="_blank" rel="noopener noreferrer" className={`${chip} hover:opacity-80`}>
                <FileText size={13} /> {att.name || 'Файл'}
            </a>
        );
    });
    if (!pieces.length) return null;
    return (
        <>
            <div className="flex flex-wrap items-center gap-1.5">{pieces}</div>
            {zoom && <ImageLightbox url={zoom} onClose={() => setZoom(null)} />}
        </>
    );
}

const ChatThread = forwardRef(function ChatThread({
    snapshot,
    loading = false,
    error = '',
    quotes = [],
    hideService = false,
    focusMessageId = null,
    emptyText = 'В этом чате нет сообщений',
    loadingText = 'Загрузка переписки…',
    placeholder = null,
    className = '',
}, ref) {
    const [flashId, setFlashId] = useState(null);
    const threadRef = useRef(null);
    const flashTimer = useRef(null);

    const scrollToMessage = (messageId, behavior = 'smooth') => {
        const host = threadRef.current;
        if (!host || messageId == null) return;
        const el = host.querySelector(`[data-mid="${messageId}"]`);
        if (!el) return;
        el.scrollIntoView({ behavior, block: 'center' });
        setFlashId(String(messageId));
        clearTimeout(flashTimer.current);
        flashTimer.current = setTimeout(() => setFlashId(null), 1600);
    };

    useImperativeHandle(ref, () => ({ scrollToMessage }), []);

    useEffect(() => () => clearTimeout(flashTimer.current), []);

    // После загрузки переписки — сразу показать нужный фрагмент:
    // явно запрошенное сообщение (клик по цитате в оценке) или первую цитату.
    useEffect(() => {
        if (!snapshot) return;
        const target = focusMessageId ?? (quotes?.[0]?.messageId ?? null);
        if (target == null) {
            if (threadRef.current) threadRef.current.scrollTop = 0;
            return;
        }
        const timer = setTimeout(() => scrollToMessage(target, 'auto'), 120);
        return () => clearTimeout(timer);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [snapshot, focusMessageId]);

    const quotesByMessage = useMemo(() => {
        const map = {};
        (quotes || []).forEach((q) => {
            const key = String(q.messageId);
            (map[key] = map[key] || []).push(q.text);
        });
        return map;
    }, [quotes]);

    const messages = useMemo(() => {
        const items = snapshot?.messages || [];
        return hideService ? items.filter((m) => m.type !== 'system' && m.type !== 'autoreply') : items;
    }, [snapshot, hideService]);

    const withDays = useMemo(() => {
        const out = [];
        let lastDay = null;
        messages.forEach((m) => {
            const day = (m.created || '').slice(0, 10);
            if (day && day !== lastDay) { out.push({ _day: fmtDay(m.created), id: `day-${day}` }); lastDay = day; }
            out.push(m);
        });
        return out;
    }, [messages]);

    return (
        <div ref={threadRef} className={`min-h-0 flex-1 overflow-y-auto overscroll-contain bg-[#f2f2f7] py-3 ${className}`}>
            {error && (
                <div className="flex items-center justify-center gap-2 px-6 py-10 text-center text-[13px] text-rose-500">
                    <AlertCircle size={15} className="shrink-0" /> {error}
                </div>
            )}
            {!error && loading && (
                <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-400">
                    <Loader2 size={15} className="animate-spin" /> {loadingText}
                </div>
            )}
            {!error && !loading && !snapshot && placeholder}
            {!error && !loading && snapshot && !withDays.length && (
                <div className="flex items-center justify-center px-6 py-10 text-center text-sm text-slate-400">{emptyText}</div>
            )}
            {!error && snapshot && withDays.map((m) => {
                if (m._day) {
                    return (
                        <div key={m.id} className="flex justify-center py-1.5">
                            <span className="rounded-full bg-slate-500/10 px-3 py-1 text-[11px] font-medium text-slate-500">{m._day}</span>
                        </div>
                    );
                }
                if (m.type === 'system') {
                    return (
                        <div key={m.id} className="flex justify-center px-4 py-0.5">
                            <span className="max-w-[85%] rounded-full bg-slate-500/10 px-3 py-1 text-center text-[11px] font-medium text-slate-500">
                                {m.text || 'Системное сообщение'} · {fmtTime(m.created)}
                            </span>
                        </div>
                    );
                }
                const out = m.type === 'to_client';
                const auto = m.type === 'autoreply';
                // Внутренний комментарий оператора (Chat2Desk type='comment'):
                // клиент его не видит, поэтому это НЕ его реплика — рисуем на
                // стороне оператора отдельным стилем, иначе заметка вроде
                // «нет ответа/обед» читается как сообщение клиента.
                const note = m.type === 'comment';
                const hasMedia = Boolean(m.photo || m.video || m.audio || m.pdf || (m.attachments || []).length);
                const isFlashing = flashId != null && String(m.id) === flashId;
                const bubbleClass = out
                    ? 'rounded-br-md bg-blue-500 text-white'
                    : note
                        ? 'rounded-br-md border border-dashed border-amber-300 bg-amber-50 text-amber-900'
                        : auto
                            ? 'rounded-br-md bg-slate-200 text-slate-600'
                            : 'rounded-bl-md bg-white text-slate-900 ring-1 ring-slate-200/60';
                return (
                    <div key={m.id} className={`flex ${(out || auto || note) ? 'justify-end' : 'justify-start'} px-4 py-0.5`}>
                        <div data-mid={m.id}
                             className={`max-w-[78%] rounded-2xl px-3 py-2 text-[13.5px] leading-snug shadow-[0_1px_1px_rgba(15,23,42,0.05)] transition-shadow duration-300 ${bubbleClass} ${
                                 isFlashing ? 'ring-4 ring-amber-400/80' : ''}`}>
                            {/* Автор исходящего. У Wazzup он свой у каждого сообщения
                                (эпизод могли вести несколько операторов), поэтому там
                                подписываем строго m.author: если автор неизвестен —
                                лучше без подписи, чем чужим именем. Имя из снапшота —
                                фолбэк для Chat2Desk и снапшотов старой схемы. */}
                            {out && ('author' in m ? m.author : snapshot.operator_name) && (
                                <div className="mb-0.5 flex items-center gap-1 text-[11px] font-semibold text-blue-100">
                                    <Headset size={11} /> {'author' in m ? m.author : snapshot.operator_name}
                                </div>
                            )}
                            {auto && (
                                <div className="mb-0.5 flex items-center gap-1 text-[11px] font-semibold text-slate-500">
                                    <Bot size={11} /> Автоответ
                                </div>
                            )}
                            {note && (
                                <div className="mb-0.5 flex items-center gap-1 text-[11px] font-semibold text-amber-700">
                                    <Lock size={11} /> Внутренний комментарий{m.author ? ` · ${m.author}` : ''}
                                </div>
                            )}
                            {hasMedia && <div className={m.text ? 'mb-1' : ''}><Media msg={m} light={out} /></div>}
                            {m.text && (
                                <div className="whitespace-pre-wrap break-words">
                                    {highlightText(m.text, quotesByMessage[String(m.id)])}
                                </div>
                            )}
                            {!m.text && !hasMedia && <div className={`italic ${out ? 'text-blue-100' : 'text-slate-400'}`}>[сообщение]</div>}
                            <div className={`mt-0.5 text-right text-[10px] ${out ? 'text-blue-100/90' : note ? 'text-amber-700/70' : 'text-slate-400'}`}>{fmtTime(m.created)}</div>
                        </div>
                    </div>
                );
            })}
        </div>
    );
});

/* Список цитат СВ под лентой: клик — прокрутка к сообщению цитаты. */
export function ChatQuotesBar({ quotes = [], onSelect, className = '' }) {
    if (!(quotes || []).length) return null;
    return (
        <div className={`max-h-48 space-y-2 overflow-y-auto overscroll-contain border-t border-slate-100 bg-white px-4 py-3 ${className}`}>
            <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                    <Quote size={11} /> Цитаты супервайзера ({quotes.length})
                </span>
                <span className="text-[11px] text-slate-400">нажмите — покажем в переписке</span>
            </div>
            {quotes.map((q, i) => (
                <button key={i} onClick={() => onSelect?.(q.messageId)}
                        title="Показать это место в переписке"
                        className="block w-full rounded-xl border-l-[3px] border-amber-400 bg-amber-50/70 px-3 py-2 text-left transition hover:bg-amber-100 active:scale-[0.995]">
                    <div className="text-[12.5px] italic text-slate-700">«{q.text}»</div>
                    {q.comment && <div className="mt-1 text-[12px] text-slate-500">{q.comment}</div>}
                </button>
            ))}
        </div>
    );
}

export default ChatThread;
