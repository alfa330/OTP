import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Loader2, AlertCircle, MessageSquare, RefreshCw, Dice5, Quote as QuoteIcon,
    Bot, FileText, Trash2, ChevronLeft, ChevronRight, Star, Headset, X,
    SlidersHorizontal, EyeOff, Eye, CheckCircle2,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosInput, iosGroupLabel, iosBtnPrimary, iosBtnGhost,
    IosBadge, IosToggle, IosModal,
} from '../ui/ios';

/* Оценка чатов чат-менеджеров (Chat2Desk).
 * СВ задаёт фильтры -> получает случайную заявку (оцениваемую часть диалога со
 * всеми файлами) -> оценивает по критериям, выделяя цитаты прямо из текста.
 * ЧМ видит свои оценки с теми же подсвеченными фрагментами.
 * Данные заявок хранятся 45 дней, снапшоты переписки — полгода. */

const CRITERIA = [
    { key: 'greeting', label: 'Приветствие и представление' },
    { key: 'literacy', label: 'Грамотность и тон общения' },
    { key: 'understanding', label: 'Понимание вопроса клиента' },
    { key: 'solution', label: 'Решение вопроса' },
    { key: 'completeness', label: 'Полнота и точность ответов' },
];

const TRANSPORT_LABELS = {
    whatsapp: 'WhatsApp', wa_dialog: 'WABA', telegram: 'Telegram',
    instagram: 'Instagram', vk: 'ВКонтакте', viber: 'Viber', sms: 'SMS',
};
const transportLabel = (t) => TRANSPORT_LABELS[t] || t || '';

const RATING_OPTIONS = [
    { value: '', label: 'Не важно' },
    { value: 'rated', label: 'С оценкой клиента' },
    { value: 'unrated', label: 'Без оценки клиента' },
    { value: 'low', label: 'Низкая оценка (< 4)' },
];

const fmtTime = (iso) => (iso ? new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : '');
const fmtDay = (iso) => (iso ? new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }) : '');
const fmtShortDay = (iso) => (iso ? new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' }) : '');
const isoDate = (d) => d.toISOString().slice(0, 10);
const daysAgo = (n) => { const d = new Date(); d.setDate(d.getDate() - n); return isoDate(d); };

const squash = (text) => String(text || '').split(/\s+/).join(' ').trim().toLowerCase();

const scoreTone = (score) => {
    if (score == null) return 'slate';
    if (score >= 4.5) return 'emerald';
    if (score >= 3.5) return 'blue';
    if (score >= 2.5) return 'amber';
    return 'rose';
};
const SCORE_BADGE = {
    emerald: 'bg-emerald-100 text-emerald-700',
    blue: 'bg-blue-100 text-blue-700',
    amber: 'bg-amber-100 text-amber-700',
    rose: 'bg-rose-100 text-rose-700',
    slate: 'bg-slate-100 text-slate-500',
};

const ScorePill = ({ score, className = '' }) => (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[12.5px] font-bold ${SCORE_BADGE[scoreTone(score)]} ${className}`}>
        <Star size={12} className="fill-current" />
        {score != null ? Number(score).toFixed(score % 1 ? 2 : 0) : '—'}
    </span>
);

/* Подсветка цитат внутри текста сообщения: точное вхождение (без регистра). */
function highlightText(text, quoteTexts) {
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

function C2dMedia({ msg, light }) {
    const [failedUri, setFailedUri] = useState(null);
    const [zoom, setZoom] = useState(false);
    const chip = `inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[12.5px] font-medium ${
        light ? 'bg-white/20 text-white' : 'bg-slate-100 text-slate-600'}`;
    const pieces = [];
    if (msg.photo && failedUri !== msg.photo) {
        pieces.push(
            <React.Fragment key="photo">
                <img src={msg.photo} alt="" loading="lazy" onError={() => setFailedUri(msg.photo)}
                     onClick={() => setZoom(true)}
                     className="max-h-64 w-auto max-w-full cursor-zoom-in rounded-xl" />
                <IosModal open={zoom} onClose={() => setZoom(false)} title="Фото" maxWidth="max-w-3xl">
                    <img src={msg.photo} alt="" className="mx-auto max-h-[72vh] w-auto rounded-2xl" />
                </IosModal>
            </React.Fragment>
        );
    }
    if (msg.video) {
        pieces.push(<video key="video" controls preload="metadata" src={msg.video}
                           className="max-h-64 w-auto max-w-full rounded-xl" />);
    }
    if (msg.audio) {
        pieces.push(<audio key="audio" controls preload="none" src={msg.audio}
                           className="h-10 w-64 max-w-full" />);
    }
    if (msg.pdf) {
        pieces.push(
            <a key="pdf" href={msg.pdf} target="_blank" rel="noopener noreferrer" className={`${chip} hover:opacity-80`}>
                <FileText size={13} /> PDF
            </a>
        );
    }
    (msg.attachments || []).forEach((att, i) => {
        if (msg.photo && att.link === msg.photo) return; // фото уже показано
        pieces.push(
            <a key={`att-${i}`} href={att.link} target="_blank" rel="noopener noreferrer" className={`${chip} hover:opacity-80`}>
                <FileText size={13} /> {att.name || 'Файл'}
            </a>
        );
    });
    if (!pieces.length) return null;
    return <div className="flex flex-wrap items-center gap-1.5">{pieces}</div>;
}

/* Пузырь: клиент — белый слева, ЧМ — синий справа, автоответ — серый справа,
 * системные — чипы по центру. data-mid нужен захвату выделения цитаты. */
function C2dBubble({ msg, quoteTexts, operatorName }) {
    if (msg.type === 'system') {
        return (
            <div className="flex justify-center px-4 py-0.5">
                <span className="max-w-[85%] rounded-full bg-slate-500/10 px-3 py-1 text-center text-[11px] font-medium text-slate-500">
                    {msg.text || 'Системное сообщение'} · {fmtTime(msg.created)}
                </span>
            </div>
        );
    }
    const out = msg.type === 'to_client';
    const auto = msg.type === 'autoreply';
    const hasMedia = Boolean(msg.photo || msg.video || msg.audio || msg.pdf || (msg.attachments || []).length);
    const bubbleClass = out
        ? 'rounded-br-md bg-blue-500 text-white'
        : auto
            ? 'rounded-br-md bg-slate-200 text-slate-600'
            : 'rounded-bl-md bg-white text-slate-900 ring-1 ring-slate-200/60';
    return (
        <div className={`flex ${(out || auto) ? 'justify-end' : 'justify-start'} px-4`}>
            <div data-mid={msg.id}
                 className={`max-w-[75%] rounded-2xl px-3 py-2 text-[13.5px] leading-snug shadow-[0_1px_1px_rgba(15,23,42,0.05)] ${bubbleClass}`}>
                {out && operatorName && (
                    <div className="mb-0.5 flex items-center gap-1 text-[11px] font-semibold text-blue-100">
                        <Headset size={11} /> {operatorName}
                    </div>
                )}
                {auto && (
                    <div className="mb-0.5 flex items-center gap-1 text-[11px] font-semibold text-slate-500">
                        <Bot size={11} /> Автоответ
                    </div>
                )}
                {hasMedia && <div className={msg.text ? 'mb-1' : ''}><C2dMedia msg={msg} light={out} /></div>}
                {msg.text && (
                    <div className="whitespace-pre-wrap break-words">
                        {highlightText(msg.text, quoteTexts)}
                    </div>
                )}
                {!msg.text && !hasMedia && (
                    <div className={`italic ${out ? 'text-blue-100' : 'text-slate-400'}`}>[сообщение]</div>
                )}
                <div className={`mt-0.5 text-right text-[10px] ${out ? 'text-blue-100/90' : 'text-slate-400'}`}>
                    {fmtTime(msg.created)}
                </div>
            </div>
        </div>
    );
}

/* Лента снапшота. selectable=true — включает захват выделения текста в цитату
 * (кнопка «Цитировать» появляется над выделением). */
function SnapshotThread({ snapshot, quotes, selectable = false, onAddQuote, height = '100%' }) {
    const boxRef = useRef(null);
    const [hideService, setHideService] = useState(false);
    const [selection, setSelection] = useState(null); // {messageId, text, x, y}

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
        if (!hideService) return items;
        return items.filter((m) => m.type !== 'system' && m.type !== 'autoreply');
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

    const handleMouseUp = () => {
        if (!selectable) return;
        const sel = window.getSelection();
        if (!sel || sel.isCollapsed) { setSelection(null); return; }
        const text = sel.toString().trim();
        if (!text) { setSelection(null); return; }
        const toEl = (node) => (node && node.nodeType === 3 ? node.parentElement : node);
        const a = toEl(sel.anchorNode)?.closest?.('[data-mid]');
        const b = toEl(sel.focusNode)?.closest?.('[data-mid]');
        if (!a || a !== b) { setSelection(null); return; }
        const rect = sel.getRangeAt(0).getBoundingClientRect();
        const box = boxRef.current?.getBoundingClientRect();
        if (!box) return;
        setSelection({
            messageId: a.dataset.mid, text,
            x: Math.min(Math.max(rect.left + rect.width / 2 - box.left, 60), box.width - 60),
            y: rect.top - box.top + (boxRef.current?.scrollTop || 0),
        });
    };

    const addQuote = () => {
        if (!selection) return;
        onAddQuote?.(selection);
        setSelection(null);
        window.getSelection()?.removeAllRanges();
    };

    return (
        <div className="relative flex min-h-0 flex-1 flex-col bg-[#f2f2f7]" style={{ height }}>
            <div className="flex items-center justify-between gap-2 border-b border-slate-200/70 bg-white/85 px-4 py-2 backdrop-blur-xl">
                <div className="min-w-0">
                    <div className="truncate text-[13.5px] font-semibold text-slate-900">
                        {snapshot?.client_name || snapshot?.client_phone || 'Клиент'}
                    </div>
                    <div className="truncate text-[11px] text-slate-400">
                        {snapshot?.client_phone && `${snapshot.client_phone} · `}
                        {snapshot?.channel_name || ''}{snapshot?.transport ? ` · ${transportLabel(snapshot.transport)}` : ''}
                    </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                    <IosBadge tone="slate">{snapshot?.messages_count ?? 0} сообщ.</IosBadge>
                    <button onClick={() => setHideService((v) => !v)}
                            title={hideService ? 'Показать автоответы и системные' : 'Скрыть автоответы и системные'}
                            className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1.5 text-[11.5px] font-semibold text-slate-600 transition hover:bg-slate-200">
                        {hideService ? <Eye size={12} /> : <EyeOff size={12} />}
                        {hideService ? 'Автоответы' : 'Без автоответов'}
                    </button>
                </div>
            </div>
            <div ref={boxRef} onMouseUp={handleMouseUp}
                 className="relative flex-1 space-y-1.5 overflow-y-auto py-3">
                {withDays.map((m) => m._day ? (
                    <div key={m.id} className="flex justify-center py-1.5">
                        <span className="rounded-full bg-slate-500/10 px-3 py-1 text-[11px] font-medium text-slate-500">{m._day}</span>
                    </div>
                ) : (
                    <C2dBubble key={m.id} msg={m}
                               quoteTexts={quotesByMessage[String(m.id)]}
                               operatorName={snapshot?.operator_name} />
                ))}
                {!withDays.length && (
                    <div className="py-10 text-center text-sm text-slate-400">Сообщений нет</div>
                )}
                {selectable && selection && (
                    <button onClick={addQuote}
                            style={{ left: selection.x, top: Math.max(selection.y - 38, 4) }}
                            className="absolute z-10 -translate-x-1/2 rounded-full bg-slate-900 px-3 py-1.5 text-[12px] font-semibold text-white shadow-lg transition hover:bg-slate-700 active:scale-[0.97]">
                        <QuoteIcon size={12} className="mr-1 inline" /> Цитировать
                    </button>
                )}
            </div>
        </div>
    );
}

/* Строка критерия с кнопками 1–5. */
function CriterionRow({ criterion, value, onChange, disabled }) {
    return (
        <div className="flex items-center justify-between gap-2 px-1 py-1.5">
            <span className="text-[12.5px] font-medium text-slate-700">{criterion.label}</span>
            <div className="flex shrink-0 gap-1">
                {[1, 2, 3, 4, 5].map((n) => (
                    <button key={n} disabled={disabled}
                            onClick={() => onChange(value === n ? null : n)}
                            className={`h-7 w-7 rounded-lg text-[12px] font-bold transition active:scale-[0.94] ${
                                value === n
                                    ? n >= 4 ? 'bg-emerald-500 text-white' : n >= 3 ? 'bg-amber-500 text-white' : 'bg-rose-500 text-white'
                                    : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
                        {n}
                    </button>
                ))}
            </div>
        </div>
    );
}

/* Просмотр оценки (карточка критериев + комментарий + цитаты) — и для СВ, и для ЧМ. */
function EvaluationSummary({ evaluation }) {
    if (!evaluation) return null;
    return (
        <div className="space-y-3">
            <div className="flex items-center justify-between">
                <div className="text-[12px] text-slate-500">
                    {evaluation.evaluator_name || 'Супервайзер'} · {fmtShortDay(evaluation.updated_at || evaluation.created_at)}
                </div>
                <ScorePill score={evaluation.score} />
            </div>
            <div className="divide-y divide-slate-100 rounded-xl bg-slate-50 px-2 py-1">
                {(evaluation.criteria || []).map((c, i) => (
                    <div key={i} className="flex items-center justify-between px-1 py-1.5 text-[12.5px]">
                        <span className="text-slate-600">{c.label || c.key}</span>
                        <span className={`font-bold ${c.score >= 4 ? 'text-emerald-600' : c.score >= 3 ? 'text-amber-600' : 'text-rose-600'}`}>
                            {c.score}
                        </span>
                    </div>
                ))}
            </div>
            {evaluation.comment && (
                <div className="rounded-xl bg-blue-50 px-3 py-2 text-[12.5px] leading-relaxed text-slate-700">
                    {evaluation.comment}
                </div>
            )}
            {(evaluation.quotes || []).length > 0 && (
                <div className="space-y-2">
                    <div className={iosGroupLabel}>Цитаты из чата</div>
                    {evaluation.quotes.map((q, i) => (
                        <div key={i} className="rounded-xl border-l-[3px] border-amber-400 bg-amber-50/70 px-3 py-2">
                            <div className="text-[12.5px] italic text-slate-700">«{q.text}»</div>
                            {q.comment && <div className="mt-1 text-[12px] text-slate-500">{q.comment}</div>}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

export default function C2dEvalView({ apiBaseUrl, withAccessTokenHeader, showToast, canEvaluate }) {
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const [tab, setTab] = useState(canEvaluate ? 'pick' : 'list');

    /* ── Вкладка «Оценить» ── */
    const [filters, setFilters] = useState({
        date_from: daysAgo(14), date_to: isoDate(new Date()),
        operator_id: '', channel_id: '', transport: '',
        min_messages: 4, max_messages: '', rating_filter: '', exclude: 'mine',
    });
    const [options, setOptions] = useState(null);
    const [picking, setPicking] = useState(false);
    const [picked, setPicked] = useState(null);   // {request, snapshot, candidates, myEvaluation}
    const [pickEmpty, setPickEmpty] = useState(null);
    const [showFilters, setShowFilters] = useState(true);

    const [scores, setScores] = useState({});
    const [comment, setComment] = useState('');
    const [quotes, setQuotes] = useState([]);
    const [saving, setSaving] = useState(false);

    /* ── Вкладка «Оценки» ── */
    const [listFilters, setListFilters] = useState({
        date_from: daysAgo(30), date_to: isoDate(new Date()), operator_id: '',
    });
    const [list, setList] = useState(null);
    const [listPage, setListPage] = useState(1);
    const [listError, setListError] = useState(null);
    const [detail, setDetail] = useState(null);       // {evaluation, snapshot}
    const [detailLoading, setDetailLoading] = useState(false);

    const loadOptions = () => {
        if (!canEvaluate) return;
        axios.get(`${apiBaseUrl}/api/c2d_eval/options`, {
            headers: headers(),
            params: { date_from: filters.date_from, date_to: filters.date_to },
        }).then((r) => setOptions(r.data))
          .catch(() => showToast?.('Не удалось загрузить фильтры', 'error'));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
    useEffect(() => { loadOptions(); }, [apiBaseUrl, filters.date_from, filters.date_to]);

    const resetDraft = (myEvaluation) => {
        const next = {};
        (myEvaluation?.criteria || []).forEach((c) => { if (c.key) next[c.key] = c.score; });
        setScores(next);
        setComment(myEvaluation?.comment || '');
        setQuotes(myEvaluation?.quotes || []);
    };

    const pickChat = () => {
        setPicking(true); setPickEmpty(null);
        const body = {};
        Object.entries(filters).forEach(([k, v]) => { if (v !== '' && v !== null) body[k] = v; });
        axios.post(`${apiBaseUrl}/api/c2d_eval/pick`, body, { headers: headers() })
            .then((r) => {
                if (r.data.status === 'empty') {
                    setPicked(null);
                    setPickEmpty(r.data.message || 'Заявок не нашлось');
                    return;
                }
                setPicked(r.data);
                resetDraft(r.data.myEvaluation);
            })
            .catch((e) => showToast?.(e.response?.data?.error || 'Не удалось получить чат', 'error'))
            .finally(() => setPicking(false));
    };

    const addQuote = ({ messageId, text }) => {
        const msg = (picked?.snapshot?.messages || []).find((m) => String(m.id) === String(messageId));
        if (!msg || !squash(msg.text).includes(squash(text))) {
            showToast?.('Выделите фрагмент внутри одного сообщения', 'error');
            return;
        }
        setQuotes((prev) => [...prev, { messageId: msg.id, text, comment: '' }]);
    };

    const filledCount = CRITERIA.filter((c) => scores[c.key]).length;
    const draftScore = filledCount
        ? (CRITERIA.reduce((sum, c) => sum + (scores[c.key] || 0), 0) / filledCount)
        : null;

    const saveEvaluation = () => {
        if (!picked?.snapshot?.id) return;
        if (!filledCount) { showToast?.('Заполните хотя бы один критерий', 'error'); return; }
        setSaving(true);
        axios.post(`${apiBaseUrl}/api/c2d_eval/snapshots/${picked.snapshot.id}/evaluation`, {
            criteria: CRITERIA.map((c) => ({ key: c.key, label: c.label, score: scores[c.key] || null })),
            comment,
            quotes,
        }, { headers: headers() })
            .then((r) => {
                setPicked((prev) => ({ ...prev, myEvaluation: r.data.evaluation }));
                showToast?.('Оценка сохранена', 'success');
            })
            .catch((e) => showToast?.(e.response?.data?.error || 'Не удалось сохранить оценку', 'error'))
            .finally(() => setSaving(false));
    };

    const loadList = (page = listPage) => {
        setList(null); setListError(null);
        const params = { page, per_page: 12 };
        if (listFilters.date_from) params.date_from = listFilters.date_from;
        if (listFilters.date_to) params.date_to = listFilters.date_to;
        if (canEvaluate && listFilters.operator_id) params.operator_id = listFilters.operator_id;
        axios.get(`${apiBaseUrl}/api/c2d_eval/evaluations`, { headers: headers(), params })
            .then((r) => setList(r.data))
            .catch(() => setListError('Не удалось загрузить оценки'));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
    useEffect(() => { if (tab === 'list') loadList(1); setListPage(1); }, [tab, listFilters]);

    const openDetail = (item) => {
        setDetailLoading(true);
        axios.get(`${apiBaseUrl}/api/c2d_eval/evaluations/${item.id}`, { headers: headers() })
            .then((r) => setDetail({ evaluation: r.data.evaluation, snapshot: r.data.snapshot }))
            .catch((e) => showToast?.(e.response?.data?.error || 'Не удалось открыть оценку', 'error'))
            .finally(() => setDetailLoading(false));
    };

    const deleteEvaluation = (item) => {
        if (!window.confirm('Удалить оценку этого чата?')) return;
        axios.delete(`${apiBaseUrl}/api/c2d_eval/evaluations/${item.id}`, { headers: headers() })
            .then(() => { showToast?.('Оценка удалена', 'success'); loadList(); })
            .catch((e) => showToast?.(e.response?.data?.error || 'Не удалось удалить', 'error'));
    };

    const totalPages = list ? Math.max(1, Math.ceil((list.total || 0) / (list.per_page || 12))) : 1;

    const segBtn = (key, Icon, label) => (
        <button onClick={() => setTab(key)}
                className={`flex items-center gap-1.5 rounded-[9px] px-3.5 py-1.5 text-[12.5px] font-semibold transition-all ${
                    tab === key ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                                : 'text-slate-500 hover:text-slate-700'}`}>
            <Icon size={13} /> {label}
        </button>
    );

    const selectClass = `${iosInput} py-2 text-[13px]`;

    return (
        <div className="w-full" style={{ fontFamily: APPLE_FONT }}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
                <div>
                    <h2 className="text-lg font-semibold tracking-tight text-slate-900">Оценка чатов ЧМ</h2>
                    <p className="text-xs text-slate-500">
                        Chat2Desk: случайный чат по фильтрам, оценка с цитатами; заявки хранятся 45 дней, переписка оценённых — полгода
                    </p>
                </div>
                {canEvaluate && (
                    <div className="flex rounded-xl bg-slate-100 p-1">
                        {segBtn('pick', Dice5, 'Оценить')}
                        {segBtn('list', Star, 'Оценки')}
                    </div>
                )}
            </div>

            {/* ── Вкладка «Оценить» ── */}
            {canEvaluate && tab === 'pick' && (
                <div className="space-y-3">
                    <div className={`${iosCard} px-4 py-3`}>
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="flex items-center gap-2">
                                <button onClick={() => setShowFilters((v) => !v)} className={iosBtnGhost}>
                                    <SlidersHorizontal size={13} /> Настройки выборки
                                </button>
                                {options && (
                                    <span className="text-[12px] text-slate-400">
                                        {options.totalRequests ?? 0} заявок в периоде
                                    </span>
                                )}
                            </div>
                            <button onClick={pickChat} disabled={picking}
                                    className={`${iosBtnPrimary} disabled:opacity-50`}>
                                {picking ? <Loader2 size={14} className="animate-spin" /> : <Dice5 size={14} />}
                                {picked ? 'Другой случайный чат' : 'Случайный чат'}
                            </button>
                        </div>
                        {showFilters && (
                            <div className="mt-3 grid gap-2.5 border-t border-slate-100 pt-3 sm:grid-cols-2 lg:grid-cols-4">
                                <label className="block">
                                    <span className={iosGroupLabel}>Период с</span>
                                    <input type="date" value={filters.date_from}
                                           onChange={(e) => setFilters((f) => ({ ...f, date_from: e.target.value }))}
                                           className={selectClass} />
                                </label>
                                <label className="block">
                                    <span className={iosGroupLabel}>по</span>
                                    <input type="date" value={filters.date_to}
                                           onChange={(e) => setFilters((f) => ({ ...f, date_to: e.target.value }))}
                                           className={selectClass} />
                                </label>
                                <label className="block">
                                    <span className={iosGroupLabel}>Чат-менеджер</span>
                                    <select value={filters.operator_id}
                                            onChange={(e) => setFilters((f) => ({ ...f, operator_id: e.target.value }))}
                                            className={selectClass}>
                                        <option value="">Все</option>
                                        {(options?.operators || []).map((op) => (
                                            <option key={op.id} value={op.id}>{op.name} ({op.count})</option>
                                        ))}
                                    </select>
                                </label>
                                <label className="block">
                                    <span className={iosGroupLabel}>Канал</span>
                                    <select value={filters.channel_id}
                                            onChange={(e) => setFilters((f) => ({ ...f, channel_id: e.target.value }))}
                                            className={selectClass}>
                                        <option value="">Все</option>
                                        {(options?.channels || []).map((ch) => (
                                            <option key={ch.id} value={ch.id}>{ch.name || ch.id} ({ch.count})</option>
                                        ))}
                                    </select>
                                </label>
                                <label className="block">
                                    <span className={iosGroupLabel}>Мессенджер</span>
                                    <select value={filters.transport}
                                            onChange={(e) => setFilters((f) => ({ ...f, transport: e.target.value }))}
                                            className={selectClass}>
                                        <option value="">Все</option>
                                        {(options?.transports || []).map((t) => (
                                            <option key={t.id} value={t.id}>{transportLabel(t.id)} ({t.count})</option>
                                        ))}
                                    </select>
                                </label>
                                <label className="block">
                                    <span className={iosGroupLabel}>Мин. сообщений</span>
                                    <input type="number" min="1" value={filters.min_messages}
                                           onChange={(e) => setFilters((f) => ({ ...f, min_messages: e.target.value }))}
                                           className={selectClass} />
                                </label>
                                <label className="block">
                                    <span className={iosGroupLabel}>Макс. сообщений</span>
                                    <input type="number" min="1" placeholder="Без лимита" value={filters.max_messages}
                                           onChange={(e) => setFilters((f) => ({ ...f, max_messages: e.target.value }))}
                                           className={selectClass} />
                                </label>
                                <label className="block">
                                    <span className={iosGroupLabel}>Оценка клиента</span>
                                    <select value={filters.rating_filter}
                                            onChange={(e) => setFilters((f) => ({ ...f, rating_filter: e.target.value }))}
                                            className={selectClass}>
                                        {RATING_OPTIONS.map((o) => (
                                            <option key={o.value} value={o.value}>{o.label}</option>
                                        ))}
                                    </select>
                                </label>
                                <label className="flex items-center gap-2 pt-4 text-[12.5px] font-medium text-slate-600">
                                    <IosToggle checked={filters.exclude === 'mine'}
                                               onChange={(v) => setFilters((f) => ({ ...f, exclude: v ? 'mine' : 'none' }))} />
                                    Не предлагать уже оценённые мной
                                </label>
                            </div>
                        )}
                    </div>

                    {pickEmpty && (
                        <div className={`${iosCard} flex items-center justify-center gap-2 py-8 text-[13px] text-slate-500`}>
                            <AlertCircle size={15} className="text-amber-500" /> {pickEmpty}
                        </div>
                    )}

                    {!picked && !pickEmpty && (
                        <div className={`${iosCard} flex flex-col items-center justify-center gap-2 py-14 text-slate-400`}>
                            <MessageSquare size={32} strokeWidth={1.5} />
                            <span className="text-sm">Нажмите «Случайный чат» — переписка появится здесь</span>
                        </div>
                    )}

                    {picked && (
                        <div className={`${iosCard} flex overflow-hidden`} style={{ height: 'calc(100vh - 240px)', minHeight: 460 }}>
                            <div className="flex min-w-0 flex-1 flex-col">
                                <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 bg-white px-4 py-2">
                                    <IosBadge tone="blue">{picked.request?.operator_name || picked.snapshot?.operator_name || 'ЧМ не определён'}</IosBadge>
                                    <IosBadge tone="slate">{fmtShortDay(picked.request?.day)}</IosBadge>
                                    {picked.request?.rating_score != null && (
                                        <IosBadge tone={picked.request.rating_score >= 4 ? 'emerald' : 'rose'}>
                                            клиент: {picked.request.rating_score}
                                        </IosBadge>
                                    )}
                                    <span className="text-[11.5px] text-slate-400">
                                        {picked.candidates} подходящих · заявка #{picked.request?.request_id}
                                    </span>
                                </div>
                                <SnapshotThread snapshot={picked.snapshot} quotes={quotes}
                                                selectable onAddQuote={addQuote} />
                            </div>

                            {/* Панель оценки */}
                            <div className="flex w-[340px] shrink-0 flex-col border-l border-slate-100 bg-white">
                                <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
                                    <span className="text-[13.5px] font-semibold text-slate-900">Оценка</span>
                                    <div className="flex items-center gap-2">
                                        {picked.myEvaluation && (
                                            <IosBadge tone="emerald"><CheckCircle2 size={11} /> сохранена</IosBadge>
                                        )}
                                        <ScorePill score={draftScore} />
                                    </div>
                                </div>
                                <div className="flex-1 space-y-3 overflow-y-auto px-3 py-3">
                                    <div className="rounded-xl bg-slate-50 px-2 py-1">
                                        {CRITERIA.map((c) => (
                                            <CriterionRow key={c.key} criterion={c} disabled={saving}
                                                          value={scores[c.key] || null}
                                                          onChange={(v) => setScores((s) => ({ ...s, [c.key]: v }))} />
                                        ))}
                                    </div>
                                    <div>
                                        <span className={iosGroupLabel}>Комментарий</span>
                                        <textarea value={comment} onChange={(e) => setComment(e.target.value)}
                                                  rows={3} placeholder="Общий вывод по чату…"
                                                  className={`${iosInput} resize-none py-2 text-[13px]`} />
                                    </div>
                                    <div>
                                        <div className="flex items-center justify-between">
                                            <span className={iosGroupLabel}>Цитаты ({quotes.length})</span>
                                        </div>
                                        {!quotes.length && (
                                            <p className="rounded-xl bg-slate-50 px-3 py-2.5 text-[12px] leading-relaxed text-slate-400">
                                                Выделите текст сообщения в переписке и нажмите «Цитировать» —
                                                фрагмент попадёт сюда, а ЧМ увидит его подсвеченным.
                                            </p>
                                        )}
                                        <div className="space-y-2">
                                            {quotes.map((q, i) => (
                                                <div key={i} className="rounded-xl border-l-[3px] border-amber-400 bg-amber-50/70 px-3 py-2">
                                                    <div className="flex items-start justify-between gap-2">
                                                        <div className="text-[12.5px] italic text-slate-700">«{q.text}»</div>
                                                        <button onClick={() => setQuotes((prev) => prev.filter((_, j) => j !== i))}
                                                                className="shrink-0 rounded-full p-1 text-slate-400 transition hover:bg-white hover:text-rose-500">
                                                            <X size={12} />
                                                        </button>
                                                    </div>
                                                    <input value={q.comment || ''}
                                                           onChange={(e) => setQuotes((prev) => prev.map((item, j) =>
                                                               j === i ? { ...item, comment: e.target.value } : item))}
                                                           placeholder="Комментарий к цитате…"
                                                           className="mt-1.5 w-full rounded-lg border-0 bg-white/80 px-2 py-1 text-[12px] focus:outline-none focus:ring-2 focus:ring-amber-400/60" />
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                </div>
                                <div className="border-t border-slate-100 px-3 py-2.5">
                                    <button onClick={saveEvaluation} disabled={saving || !filledCount}
                                            className={`${iosBtnPrimary} w-full justify-center disabled:opacity-50`}>
                                        {saving ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
                                        {picked.myEvaluation ? 'Обновить оценку' : 'Сохранить оценку'}
                                    </button>
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* ── Вкладка «Оценки» (для ЧМ — единственная) ── */}
            {(tab === 'list' || !canEvaluate) && (
                <div className="space-y-3">
                    <div className={`${iosCard} flex flex-wrap items-end gap-2.5 px-4 py-3`}>
                        <label className="block">
                            <span className={iosGroupLabel}>Период с</span>
                            <input type="date" value={listFilters.date_from}
                                   onChange={(e) => setListFilters((f) => ({ ...f, date_from: e.target.value }))}
                                   className={selectClass} style={{ width: 150 }} />
                        </label>
                        <label className="block">
                            <span className={iosGroupLabel}>по</span>
                            <input type="date" value={listFilters.date_to}
                                   onChange={(e) => setListFilters((f) => ({ ...f, date_to: e.target.value }))}
                                   className={selectClass} style={{ width: 150 }} />
                        </label>
                        {canEvaluate && (
                            <label className="block min-w-[200px]">
                                <span className={iosGroupLabel}>Чат-менеджер</span>
                                <select value={listFilters.operator_id}
                                        onChange={(e) => setListFilters((f) => ({ ...f, operator_id: e.target.value }))}
                                        className={selectClass}>
                                    <option value="">Все</option>
                                    {(options?.operators || []).map((op) => (
                                        <option key={op.id} value={op.id}>{op.name}</option>
                                    ))}
                                </select>
                            </label>
                        )}
                        <div className="ml-auto flex items-center gap-2 pb-0.5">
                            {list && (
                                <>
                                    <IosBadge tone="slate">{list.total} оценок</IosBadge>
                                    {list.avg_score != null && <IosBadge tone={scoreTone(list.avg_score)}>средняя: {list.avg_score}</IosBadge>}
                                </>
                            )}
                            <button onClick={() => loadList()} className={iosBtnGhost}><RefreshCw size={13} /></button>
                        </div>
                    </div>

                    {list === null && !listError && (
                        <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-[13px] text-slate-400`}>
                            <Loader2 size={15} className="animate-spin" /> Загрузка…
                        </div>
                    )}
                    {listError && (
                        <div className={`${iosCard} flex items-center justify-center gap-2 py-10 text-[13px] text-rose-500`}>
                            <AlertCircle size={15} /> {listError}
                        </div>
                    )}
                    {list && !list.items?.length && (
                        <div className={`${iosCard} py-12 text-center text-sm text-slate-400`}>
                            Оценок за выбранный период нет
                        </div>
                    )}
                    {list?.items?.length > 0 && (
                        <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                            {list.items.map((item) => (
                                <button key={item.id} onClick={() => openDetail(item)}
                                        className="flex w-full flex-wrap items-center gap-x-4 gap-y-1.5 px-4 py-3 text-left transition hover:bg-slate-50">
                                    <div className="min-w-0 flex-1" style={{ minWidth: 200 }}>
                                        <div className="flex items-center gap-2">
                                            <span className="truncate text-[13.5px] font-semibold text-slate-900">
                                                {item.operator_name || 'ЧМ не определён'}
                                            </span>
                                            <ScorePill score={item.score} />
                                        </div>
                                        <div className="mt-0.5 truncate text-[11.5px] text-slate-400">
                                            {fmtShortDay(item.day)} · {item.client_name || item.client_phone || 'клиент'}
                                            {item.channel_name && ` · ${item.channel_name}`}
                                            {item.messages_count != null && ` · ${item.messages_count} сообщ.`}
                                        </div>
                                        {item.comment && (
                                            <div className="mt-0.5 line-clamp-1 text-[12px] text-slate-500">{item.comment}</div>
                                        )}
                                    </div>
                                    <div className="flex shrink-0 items-center gap-2">
                                        {(item.quotes || []).length > 0 && (
                                            <IosBadge tone="amber"><QuoteIcon size={10} /> {item.quotes.length}</IosBadge>
                                        )}
                                        <span className="text-[11.5px] text-slate-400">{item.evaluator_name || ''}</span>
                                        {canEvaluate && (
                                            <span role="button" tabIndex={-1}
                                                  onClick={(e) => { e.stopPropagation(); deleteEvaluation(item); }}
                                                  className="rounded-full p-1.5 text-slate-300 transition hover:bg-rose-50 hover:text-rose-500">
                                                <Trash2 size={13} />
                                            </span>
                                        )}
                                        <ChevronRight size={14} className="text-slate-300" />
                                    </div>
                                </button>
                            ))}
                        </div>
                    )}
                    {list && totalPages > 1 && (
                        <div className="flex items-center justify-center gap-3 text-[12.5px] font-medium text-slate-500">
                            <button disabled={listPage <= 1}
                                    onClick={() => { const p = listPage - 1; setListPage(p); loadList(p); }}
                                    className={`${iosBtnGhost} disabled:opacity-40`}><ChevronLeft size={13} /></button>
                            <span>{listPage} / {totalPages}</span>
                            <button disabled={listPage >= totalPages}
                                    onClick={() => { const p = listPage + 1; setListPage(p); loadList(p); }}
                                    className={`${iosBtnGhost} disabled:opacity-40`}><ChevronRight size={13} /></button>
                        </div>
                    )}
                </div>
            )}

            {/* Модал просмотра оценки: чат с подсветкой цитат + сводка */}
            <IosModal open={Boolean(detail) || detailLoading} onClose={() => setDetail(null)}
                      title={detail?.evaluation?.operator_name || 'Оценка чата'}
                      subtitle={detail ? `${fmtShortDay(detail.evaluation?.day)} · ${detail.evaluation?.channel_name || ''}` : ''}
                      maxWidth="max-w-5xl">
                {detailLoading && (
                    <div className="flex items-center justify-center gap-2 py-16 text-sm text-slate-400">
                        <Loader2 size={15} className="animate-spin" /> Загрузка…
                    </div>
                )}
                {detail && (
                    <div className="flex flex-col gap-3 md:flex-row" style={{ minHeight: 420 }}>
                        <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl ring-1 ring-slate-200/70"
                             style={{ height: '60vh' }}>
                            {detail.snapshot ? (
                                <SnapshotThread snapshot={detail.snapshot} quotes={detail.evaluation?.quotes} />
                            ) : (
                                <div className="flex flex-1 flex-col items-center justify-center gap-2 bg-slate-50 text-slate-400">
                                    <AlertCircle size={26} strokeWidth={1.5} />
                                    <span className="px-6 text-center text-[13px]">
                                        Переписка удалена по ретеншну (полгода) — доступна только сводка оценки
                                    </span>
                                </div>
                            )}
                        </div>
                        <div className="w-full shrink-0 overflow-y-auto md:w-[320px]" style={{ maxHeight: '60vh' }}>
                            <EvaluationSummary evaluation={detail.evaluation} />
                        </div>
                    </div>
                )}
            </IosModal>
        </div>
    );
}
