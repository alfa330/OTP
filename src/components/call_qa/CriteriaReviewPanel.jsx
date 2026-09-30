import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import {
    Check, X, Minus, AlertTriangle, ShieldAlert, Sparkles, Save, Loader2, Plus, Lock,
    BookMarked, RotateCcw, ChevronDown, Quote, Undo2, ShieldCheck, Server, User2, Clock,
    MessageSquare, Pencil,
} from 'lucide-react';
import { iosCard, iosBtnPrimary, iosBtnGhost, IosBadge, IosHint, IosToggle, scoreTone } from '../ui/ios';
import {
    allowedVerdicts, verdictFromAi, aiVerdictOf, toAiVerdict, isCorrection, aiCommentFor,
    initialVerdicts, quoteInTranscript, scoreOf, validateReview, validateCorrections,
    isEmptyReview, validationLabel, filledCount, COMMENT_REQUIRED, NEGATIVE, VERDICT_LABEL, ERROR,
} from './humanReview';
import { CHAT_SUBJECTS } from './subjects';

/* Оценка по критериям в карточке ИИ-оценки — одна на ИИ и человека.
 *
 * Карточка открывается уже заполненной вердиктами ИИ. Согласен — ничего не
 * меняешь; не согласен — переключаешь критерий и пишешь, что не так. Итог
 * считается по тому, что стоит на экране, формулой журнала. Раньше это были две
 * вкладки («ИИ» — подтверждение и разбор, «Моя оценка» — оценка по шкале), и
 * одну и ту же работу приходилось делать дважды.
 *
 * Одна кнопка сохраняет всё сразу:
 *   • оценку человека (ai_human_reviews), а с переключателем «Учитывать в
 *     качестве» — и строку «Журнала оценок» (полную, как в журнале);
 *   • пока прогон ИИ не разобран — итог ревью: подтверждение, если исправлений
 *     нет, или исправления в черновики базы знаний ИИ. Объяснение исправления —
 *     комментарий человека; правило и цитату для ИИ можно уточнить отдельно.
 *
 * Правила и формула — в humanReview.js (зеркало call_qa/human_review.py).
 */

const VERDICT_UI = {
    Correct:    { Icon: Check,         active: 'bg-white text-emerald-700 shadow-sm' },
    Incorrect:  { Icon: X,             active: 'bg-white text-rose-600 shadow-sm' },
    'N/A':      { Icon: Minus,         active: 'bg-white text-slate-700 shadow-sm' },
    Deficiency: { Icon: AlertTriangle, active: 'bg-white text-amber-700 shadow-sm' },
    // Критическая ошибка обнуляет итог — единственный вердикт, закрашенный целиком:
    // пропустить его взглядом нельзя.
    Error:      { Icon: ShieldAlert,   active: 'bg-rose-600 text-white shadow-sm' },
};

// Своя строка классов, а не iosInput + утилиты сверху: утилита того же свойства
// поверх готового класса в Tailwind не выигрывает (побеждает порядок правил в CSS).
const fieldCls = 'w-full resize-y rounded-xl border-0 bg-slate-100 px-3 py-2 text-[12.5px] leading-snug '
    + 'text-slate-900 placeholder-slate-400 transition focus:bg-white focus:outline-none '
    + 'focus:ring-2 focus:ring-blue-500/70 disabled:cursor-not-allowed disabled:opacity-60';
const chipCls = 'inline-flex min-h-7 items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 '
    + 'text-[11.5px] font-medium text-slate-500 transition-all hover:bg-slate-200 hover:text-slate-700 '
    + 'active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 '
    + 'disabled:opacity-50';
const linkCls = 'inline-flex items-center gap-1 rounded-md text-[11.5px] font-medium text-slate-500 '
    + 'transition hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 '
    + 'focus-visible:ring-blue-500/60 disabled:opacity-50';

const stateFrom = (criteria, saved) => ({
    ...initialVerdicts(criteria, saved),
    comment: saved?.comment || '',
    countInQuality: Boolean(saved?.counted_in_quality),
    commentVisible: saved?.comment_visible_to_operator !== false,
    questionResolved: Boolean(saved?.question_resolved),
    firstContact: Boolean(saved?.resolved_first_contact),
    kb: {},
});

// Подпись оценки человека: что уходит в ai_human_reviews / журнал.
const humanSignature = (s) => JSON.stringify([
    s.scores, s.comments, s.comment, s.countInQuality, s.commentVisible, s.questionResolved, s.firstContact,
]);
// Уточнения для базы знаний тоже несохранённая работа — закрыть карточку молча нельзя.
const kbSignature = (kb) => JSON.stringify(Object.keys(kb || {}).sort().map((key) => {
    const x = kb[key] || {};
    return [key, x.rule || '', x.situation || '', x.not_covered || '', x.excerpt || ''];
}).filter((row) => row.slice(1).some(Boolean)));

/* Правило и цитата для ИИ — необязательное уточнение исправления. Без него в
 * черновик базы знаний уходит комментарий человека и честная отметка «без
 * цитаты»; с ним — правило для похожих случаев и дословный фрагмент разговора. */
function KnowledgeDetails({ c, value, comment, extra, onChange, disabled, transcriptText, onRefine }) {
    const [refining, setRefining] = useState(false);
    const [note, setNote] = useState(null);
    const [showSituation, setShowSituation] = useState(false);
    const [showBounds, setShowBounds] = useState(false);
    const request = useRef(0);
    const valueRef = useRef(value);
    valueRef.current = value;
    useEffect(() => () => { request.current += 1; }, []);

    const hint = 'Исправление уходит в черновики базы знаний: одобренные правила ИИ применяет к похожим '
        + 'разговорам. Без правила в черновик пойдёт ваш комментарий, без цитаты — отметка «без подтверждения».';
    if (!extra?.open) {
        return (
            <div className="mt-2 flex items-center gap-1.5">
                <button type="button" onClick={() => onChange({ open: true })} disabled={disabled} className={chipCls}>
                    <Plus size={11} strokeWidth={2.5} />Правило и цитата для ИИ
                </button>
                <IosHint label="Что уходит в базу знаний" text={hint} />
            </div>
        );
    }

    const quote = String(extra.excerpt || '');
    const found = quote.trim() ? quoteInTranscript(quote, transcriptText) : null;
    const evidence = String(c.evidence || '').trim();
    const situationOpen = showSituation || Boolean(extra.situation);
    const boundsOpen = showBounds || Boolean(extra.not_covered);
    const busy = disabled || refining;

    const refine = async () => {
        if (!onRefine || busy) return;
        const requestId = ++request.current;
        const forVerdict = value;
        setRefining(true);
        try {
            const verified = Boolean(found);
            const proposal = await onRefine(c, {
                verdict: toAiVerdict(value),
                reason: String(extra.rule || '').trim() || String(comment || '').trim(),
                excerpt: verified ? quote.trim() : '',
                excerpt_verified: verified,
                evidence_status: verified ? 'verified' : null,
            });
            if (proposal && requestId === request.current && valueRef.current === forVerdict) {
                onChange({
                    rule: proposal.rule || extra.rule || '',
                    situation: proposal.situation || extra.situation || '',
                    not_covered: proposal.not_covered || extra.not_covered || '',
                    refinedFor: forVerdict,
                });
                setNote(proposal.note_to_reviewer || null);
            }
        } finally {
            if (requestId === request.current) setRefining(false);
        }
    };

    return (
        <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}
                    className="mt-2 space-y-2 rounded-xl bg-slate-50/80 p-2.5 ring-1 ring-slate-200/70">
            <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 text-[12px] font-semibold text-slate-600">
                    Для базы знаний ИИ
                    <IosHint label="Что уходит в базу знаний" text={hint} />
                </span>
                {onRefine && (
                    <button type="button" onClick={refine} disabled={busy}
                            className="inline-flex min-h-7 shrink-0 items-center gap-1.5 rounded-lg px-2 py-1 text-[12px] font-medium text-slate-500 transition-all hover:bg-slate-200/70 hover:text-slate-700 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 disabled:opacity-50">
                        {refining ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                        {refining ? 'Формулирую…' : 'Сформулировать'}
                    </button>
                )}
            </div>
            <textarea rows={2} value={extra.rule || ''} disabled={busy}
                aria-label={`Правило для ИИ по критерию «${c.name}»`}
                onChange={(e) => onChange({ rule: e.target.value })}
                placeholder="Правило для похожих случаев. Пусто — возьмём ваш комментарий"
                className={fieldCls} />
            {situationOpen && (
                <textarea rows={2} value={extra.situation || ''} disabled={busy}
                    aria-label="Ситуация — когда правило действует"
                    onChange={(e) => onChange({ situation: e.target.value })}
                    placeholder="Ситуация: когда правило действует — обобщённо, без имён"
                    className={fieldCls} />
            )}
            {boundsOpen && (
                <textarea rows={2} value={extra.not_covered || ''} disabled={busy}
                    aria-label="Границы — чего правило не оправдывает"
                    onChange={(e) => onChange({ not_covered: e.target.value })}
                    placeholder="Границы: какие нарушения этим правилом не прощаются"
                    className={fieldCls} />
            )}
            <div>
                <textarea rows={2} value={quote} disabled={busy}
                    aria-label="Цитата из разговора"
                    onChange={(e) => onChange({ excerpt: e.target.value })}
                    placeholder="Цитата из разговора — дословно, без пересказа"
                    className={`${fieldCls} ${found === false ? 'ring-1 ring-rose-200' : ''}`} />
                {(found != null || (evidence && quote.trim() !== evidence)) && (
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 px-0.5 text-[11.5px]">
                        {found === true && (
                            <span className="inline-flex items-center gap-1 font-medium text-emerald-700">
                                <ShieldCheck size={12} />есть в транскрипте
                            </span>
                        )}
                        {found === false && (
                            <span className="inline-flex items-center gap-1 font-medium text-rose-600">
                                <AlertTriangle size={12} />дословно в транскрипте нет
                            </span>
                        )}
                        {evidence && quote.trim() !== evidence && (
                            <button type="button" onClick={() => onChange({ excerpt: evidence })} disabled={busy} className={linkCls}>
                                <Quote size={11} />Взять цитату ИИ
                            </button>
                        )}
                    </div>
                )}
            </div>
            {(!situationOpen || !boundsOpen) && (
                <div className="flex flex-wrap gap-1.5">
                    {!situationOpen && (
                        <button type="button" onClick={() => setShowSituation(true)} disabled={busy} className={chipCls}>
                            <Plus size={11} strokeWidth={2.5} />Ситуация
                        </button>
                    )}
                    {!boundsOpen && (
                        <button type="button" onClick={() => setShowBounds(true)} disabled={busy} className={chipCls}>
                            <Plus size={11} strokeWidth={2.5} />Границы
                        </button>
                    )}
                </div>
            )}
            {note && (
                <p className="flex items-start gap-1.5 rounded-lg bg-amber-50/70 px-2.5 py-1.5 text-[11.5px] leading-snug text-slate-600 ring-1 ring-amber-100">
                    <AlertTriangle size={12} className="mt-[3px] shrink-0 text-amber-500" />{note}
                </p>
            )}
        </motion.div>
    );
}

const CriterionReview = memo(function CriterionReview({
    c, index, value, comment, extra, onPick, onComment, onExtra, onRevert,
    locked, kbEnabled, kbLocked, highlight, journalVerdict, journalComment, transcriptText, onRefine,
}) {
    const [reasonOpen, setReasonOpen] = useState(false);
    const [commentOpen, setCommentOpen] = useState(false);
    const [editing, setEditing] = useState(false);
    const fieldRef = useRef(null);
    useEffect(() => { if (editing) fieldRef.current?.focus(); }, [editing]);

    const options = allowedVerdicts(c);
    const transcript = c.source === 'transcript';
    const aiVerdict = aiVerdictOf(c);
    const aiHuman = verdictFromAi(c, c.ai);
    const corrected = isCorrection(c, value);
    const text = String(comment || '');
    const aiComment = String(c.comment || '').trim();
    const commentFromAi = Boolean(aiComment) && text.trim() === aiComment;
    const negative = value != null && NEGATIVE.has(value);
    const needText = !text.trim() && ((value != null && COMMENT_REQUIRED.has(value))
        || (corrected && !String(extra?.rule || '').trim()));
    const showComment = negative || corrected || commentOpen || Boolean(text.trim());
    // Комментарий ИИ к его же ошибке — тихая заметка, а не поле: согласному с ИИ
    // править нечего, а шесть заполненных полей подряд читались бы как анкета.
    // Поле появляется по «Изменить» или когда человек исправил вердикт.
    const asNote = showComment && commentFromAi && !editing;
    // В «Обосновании» — только то, чего ещё нет на экране: комментарий ИИ,
    // который уже стоит в поле, второй раз не показываем.
    const reasonText = commentFromAi && showComment ? '' : aiComment;
    const evidence = String(c.evidence || '').trim();
    const hasReasoning = aiVerdict != null && Boolean(reasonText || evidence);
    const conf = c.conf != null ? Math.round(Number(c.conf) * 100) : null;
    const critError = value === ERROR;

    const ring = highlight ? 'ring-2 ring-amber-300/80'
        : critError ? 'ring-1 ring-rose-200'
            : corrected ? 'ring-2 ring-blue-400/50' : '';

    return (
        <div id={`qa-criterion-${c.idx ?? index}`} className={`${iosCard} scroll-mt-16 p-3 ${ring}`}>
            <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 text-[13.5px] font-medium leading-snug text-slate-800">
                    {c.name}
                    {c.description ? (
                        <span className="ml-1.5 inline-flex align-[-3px]"><IosHint label="Описание критерия" text={c.description} /></span>
                    ) : null}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                    {!showComment && !locked && (
                        <button type="button" onClick={() => setCommentOpen(true)} title="Комментарий к критерию"
                                aria-label={`Комментарий к критерию «${c.name}»`}
                                className="grid h-7 w-7 place-items-center rounded-lg text-slate-300 transition hover:bg-slate-100 hover:text-slate-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                            <MessageSquare size={14} />
                        </button>
                    )}
                    {/* Вес — как в журнале: без него не видно, чего стоит ошибка. */}
                    {c.is_critical
                        ? <IosBadge tone="red" className="!px-2 !py-0.5">критический</IosBadge>
                        : <IosBadge tone="slate" className="!px-2 !py-0.5 tabular-nums">{c.weight ?? '—'} б.</IosBadge>}
                </div>
            </div>

            <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11.5px] text-slate-400">
                {aiVerdict != null ? (
                    <>
                        <span className="inline-flex items-center gap-1" title="Оценку поставил ИИ; в процентах — его уверенность">
                            <Sparkles size={11} className="text-blue-400" />ИИ
                            {conf != null && (
                                <span className={`tabular-nums ${conf < 60 ? 'font-medium text-amber-600' : ''}`}>· {conf}%</span>
                            )}
                        </span>
                        {hasReasoning && (
                            <button type="button" onClick={() => setReasonOpen((o) => !o)} aria-expanded={reasonOpen} className={linkCls}>
                                <ChevronDown size={12} className={`transition-transform ${reasonOpen ? 'rotate-180' : ''}`} />
                                {reasonText ? 'Обоснование' : 'Цитата'}
                            </button>
                        )}
                    </>
                ) : transcript ? (
                    <span className="inline-flex items-center gap-1 text-amber-600">
                        <Clock size={11} />ИИ не вернул вердикт — оцените сами
                    </span>
                ) : (
                    <span className="inline-flex items-center gap-1">
                        {c.source === 'system_api' ? <Server size={11} /> : <User2 size={11} />}
                        {c.source === 'system_api' ? 'Проверка данных в ПО — оцените сами' : 'ИИ не проверяет — оцените сами'}
                    </span>
                )}
                {journalVerdict != null && (
                    <span className="inline-flex items-center gap-1"
                          title={journalComment ? `Оценка в журнале: ${journalComment}` : 'Оценка человека в журнале'}>
                        <BookMarked size={11} />в журнале: {VERDICT_LABEL[journalVerdict] || journalVerdict}
                    </span>
                )}
            </div>

            {reasonOpen && hasReasoning && (
                <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }}
                            className="mt-1.5 space-y-1.5 overflow-hidden">
                    {reasonText && <p className="text-[12.5px] leading-snug text-slate-500">{reasonText}</p>}
                    {evidence && (
                        <p className="flex gap-1.5 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[12.5px] italic text-slate-600 ring-1 ring-slate-100">
                            <Quote size={13} className="mt-0.5 shrink-0 text-slate-300" />«{evidence}»
                        </p>
                    )}
                </motion.div>
            )}

            <div className="mt-2 flex rounded-xl bg-slate-100 p-0.5" role="group" aria-label={`Оценка по критерию «${c.name}»`}>
                {options.map((v) => {
                    const ui = VERDICT_UI[v];
                    const active = value === v;
                    return (
                        <button key={v} type="button" disabled={locked} aria-pressed={active}
                                onClick={() => onPick(index, v)}
                                className={`flex min-h-9 min-w-0 ${v === ERROR ? 'flex-[1.4]' : 'flex-1'} items-center justify-center gap-1 rounded-lg px-1.5 py-1.5 text-[12px] font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 disabled:cursor-not-allowed ${
                                    active ? ui.active : `text-slate-500 ${locked ? 'opacity-60' : 'hover:text-slate-700'}`}`}>
                            <ui.Icon size={12} strokeWidth={2.5} className="shrink-0" />
                            <span className="truncate">{VERDICT_LABEL[v]}</span>
                        </button>
                    );
                })}
            </div>

            {corrected && (
                <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 px-0.5 text-[11.5px]">
                    <span className="font-medium text-blue-600">Исправлено</span>
                    <span className="text-slate-400">у ИИ: {VERDICT_LABEL[aiHuman] || VERDICT_LABEL[aiVerdict]}</span>
                    {!locked && (
                        <button type="button" onClick={() => onRevert(index)} className={linkCls}>
                            <Undo2 size={12} />Вернуть
                        </button>
                    )}
                </div>
            )}

            {asNote ? (
                <div className="mt-2 flex items-start gap-1.5 rounded-lg bg-slate-50 px-2.5 py-1.5 ring-1 ring-slate-100">
                    <Sparkles size={12} className="mt-[3px] shrink-0 text-blue-400" aria-label="Комментарий ИИ" />
                    <p className="min-w-0 flex-1 text-[12.5px] leading-snug text-slate-600">{text}</p>
                    {!locked && (
                        <button type="button" onClick={() => setEditing(true)} title="Изменить комментарий"
                                aria-label={`Изменить комментарий к критерию «${c.name}»`}
                                className="-mr-1 grid h-6 w-6 shrink-0 place-items-center rounded-md text-slate-400 transition hover:bg-slate-200/70 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                            <Pencil size={12} />
                        </button>
                    )}
                </div>
            ) : showComment && (
                <textarea ref={fieldRef} rows={2} value={text} disabled={locked}
                    aria-label={`Комментарий к критерию «${c.name}»`}
                    onChange={(e) => onComment(index, e.target.value)}
                    placeholder={negative ? 'Что именно не так'
                        : corrected ? 'Почему не так, как у ИИ' : 'Комментарий (необязательно)'}
                    className={`${fieldCls} mt-2 ${needText ? 'ring-1 ring-rose-200' : ''}`} />
            )}

            {corrected && transcript && kbEnabled && (
                <KnowledgeDetails c={c} value={value} comment={text} extra={extra}
                                  onChange={(patch) => onExtra(c.idx, patch)} disabled={kbLocked}
                                  transcriptText={transcriptText} onRefine={onRefine} />
            )}
        </div>
    );
});

/* Строка настроек, как в «Настройках» iOS: подпись слева, переключатель справа. */
function SettingRow({ label, hint, checked, onChange, disabled }) {
    return (
        <div className="flex items-center justify-between gap-3 py-2">
            <span className="flex items-center gap-1.5 text-[13px] text-slate-700">
                {label}
                {hint ? <IosHint label={label} text={hint} /> : null}
            </span>
            <IosToggle checked={checked} onChange={onChange} disabled={disabled} />
        </div>
    );
}

export default function CriteriaReviewPanel({
    call, transcriptText = '', canCorrectJournal = false, onSave, onSkip, onRefine, onInteractionChange,
}) {
    const criteria = useMemo(() => call?.criteria || [], [call]);
    const journal = call?.human_review || null;
    // Оценка в журнале, сделанная не мной, — справка у каждого критерия. Свою
    // карточка и так показывает: она становится стартовой оценкой.
    const journalByOther = Boolean(journal) && !journal.is_mine;
    const isChat = CHAT_SUBJECTS.includes(call?.subject_kind);
    const hasTranscript = Boolean(call?.transcript?.length);

    const [lastSaved, setLastSaved] = useState(call?.my_review || null);
    const [state, setState] = useState(() => stateFrom(criteria, call?.my_review));
    const [showComment, setShowComment] = useState(Boolean(call?.my_review?.comment));
    const [saving, setSaving] = useState(false);
    const [attempted, setAttempted] = useState(false);
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; };
    }, []);

    // Прогон ИИ ещё не разобран — сохранение заодно подтверждает или исправляет его
    // (и убирает карточку из очереди). Разобранный второй раз не принимается.
    const aiReview = call?.ai_review || null;
    const aiPending = !aiReview?.outcome && hasTranscript;
    const scaleChanged = Boolean(call?.scale_changed);
    const inJournal = Boolean(lastSaved?.counted_in_quality);
    // Ушедшая в журнал оценка правится только переоценкой, а её из карточки делает
    // админ или глава отдела — как в самом журнале.
    const humanLocked = inJournal && !canCorrectJournal;
    const humanCanSave = !scaleChanged && !humanLocked;
    // Вердикты правятся, пока есть что сохранить: своя оценка или разбор ИИ.
    const verdictsLocked = saving || humanLocked || (!humanCanSave && !aiPending);
    const toggleBlockedByOther = journalByOther && !canCorrectJournal;
    const toggleDisabled = saving || !humanCanSave || inJournal || toggleBlockedByOther;

    const correctionIdx = useMemo(
        () => criteria.map((c, i) => (isCorrection(c, state.scores[i]) ? i : -1)).filter((i) => i >= 0),
        [criteria, state.scores],
    );
    const baseline = useMemo(() => stateFrom(criteria, lastSaved), [criteria, lastSaved]);
    const humanDirty = humanSignature(state) !== humanSignature(baseline);
    const humanChanged = !lastSaved || humanDirty;
    // Несохранённое — своя оценка, а пока прогон не разобран — ещё и исправления ИИ:
    // закрыв карточку, их потеряли бы, даже если своя оценка уже сохранена.
    const dirty = humanDirty || (aiPending && (correctionIdx.length > 0 || kbSignature(state.kb) !== '[]'));

    useEffect(() => { onInteractionChange?.({ dirty, busy: saving }); }, [dirty, saving, onInteractionChange]);
    useEffect(() => () => onInteractionChange?.({ dirty: false, busy: false }), [onInteractionChange]);

    const score = scoreOf(criteria, state.scores);
    const filled = filledCount(criteria, state.scores);
    const hasCritError = criteria.some((c, i) => c.is_critical && state.scores[i] === ERROR);
    const scrollToEmpty = () => {
        const i = state.scores.findIndex((v) => v == null || v === '');
        if (i >= 0) document.getElementById(`qa-criterion-${criteria[i].idx ?? i}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    };

    const empty = isEmptyReview(state.scores, state.comments, state.comment);
    const doHuman = humanCanSave && humanChanged && !(empty && !state.countInQuality);
    const doAi = aiPending;
    const quoteFound = useCallback((text) => quoteInTranscript(text, transcriptText), [transcriptText]);
    const humanCheck = doHuman
        ? validateReview(criteria, state.scores, state.comments, { completeRequired: state.countInQuality })
        : { missing: [], commentsRequired: [], ok: true };
    const aiCheck = doAi
        ? validateCorrections(criteria, state.scores, state.comments, state.kb, quoteFound)
        : { reasonsRequired: [], quotesInvalid: [], ok: true };
    const problems = { ...humanCheck, ...aiCheck, ok: humanCheck.ok && aiCheck.ok };
    const problemIdx = useMemo(() => new Set(attempted ? [
        ...problems.missing, ...problems.commentsRequired, ...problems.reasonsRequired, ...problems.quotesInvalid,
    ] : []), [attempted, problems.missing, problems.commentsRequired, problems.reasonsRequired, problems.quotesInvalid]);
    const canSave = !saving && (doHuman || doAi);

    const update = (patch) => setState((s) => ({ ...s, ...patch }));
    const pick = useCallback((i, v) => setState((s) => {
        if (s.scores[i] === v) return s;
        const c = criteria[i];
        const scores = s.scores.slice(); scores[i] = v;
        const comments = s.comments.slice();
        // Комментарий ИИ объяснял прежний вердикт — под новым он неправда. Свой
        // текст человека не трогаем; вернулись к вердикту ИИ — вернулся и его текст.
        const before = aiCommentFor(c, s.scores[i]);
        if (before && String(comments[i] || '').trim() === before) comments[i] = '';
        const after = aiCommentFor(c, v);
        if (after && !String(comments[i] || '').trim()) comments[i] = after;
        // Правило, сформулированное ИИ под другой вердикт, противоречило бы новому.
        let { kb } = s;
        const extra = kb[c.idx];
        if (extra?.refinedFor && extra.refinedFor !== v) {
            kb = { ...kb, [c.idx]: { ...extra, rule: '', situation: '', not_covered: '', refinedFor: null } };
        }
        return { ...s, scores, comments, kb };
    }), [criteria]);
    const setComment = useCallback((i, text) => setState((s) => {
        const comments = s.comments.slice(); comments[i] = text;
        return { ...s, comments };
    }), []);
    const setExtra = useCallback((idx, patch) => setState((s) => ({
        ...s, kb: { ...s.kb, [idx]: { ...(s.kb[idx] || {}), ...patch } },
    })), []);
    const revert = useCallback((i) => setState((s) => {
        const c = criteria[i];
        const ai = verdictFromAi(c, c.ai);
        const scores = s.scores.slice(); scores[i] = ai;
        const comments = s.comments.slice(); comments[i] = aiCommentFor(c, ai);
        const kb = { ...s.kb };
        delete kb[c.idx];
        return { ...s, scores, comments, kb };
    }), [criteria]);
    const revertAll = () => setState((s) => {
        const scores = s.scores.slice();
        const comments = s.comments.slice();
        criteria.forEach((c, i) => {
            if (!isCorrection(c, s.scores[i])) return;
            scores[i] = verdictFromAi(c, c.ai);
            comments[i] = aiCommentFor(c, scores[i]);
        });
        return { ...s, scores, comments, kb: {} };
    });

    const save = async () => {
        setAttempted(true);
        if (!canSave) return;
        // Что не так, показывает строка под кнопкой — живой пересчёт, а не снимок.
        if (!problems.ok) return;
        if (doHuman && state.countInQuality && !inJournal
            && !window.confirm(`Отправить оценку ${score}/100 в «Журнал оценок»? Она войдёт в качество сотрудника.`)) return;
        const human = doHuman ? {
            scores: state.scores,
            criterion_comments: state.comments,
            comment: state.comment,
            comment_visible_to_operator: state.commentVisible,
            question_resolved: state.questionResolved,
            resolved_first_contact: state.questionResolved && state.firstContact,
            count_in_quality: state.countInQuality,
        } : null;
        const ai = doAi ? {
            items: correctionIdx.map((i) => {
                const c = criteria[i];
                const extra = state.kb[c.idx] || {};
                const quote = String(extra.excerpt || '').trim();
                const verified = Boolean(quote) && quoteFound(quote);
                return {
                    criterion_id: c.criterion_id, criterion_idx: c.idx, criterion_name: c.name,
                    ai_verdict: c.ai, correct_verdict: toAiVerdict(state.scores[i]),
                    reason: String(extra.rule || '').trim() || String(state.comments[i] || '').trim(),
                    situation: String(extra.situation || '').trim() || null,
                    not_covered: String(extra.not_covered || '').trim() || null,
                    excerpt: verified ? quote : '',
                    excerpt_verified: verified,
                    evidence_status: verified ? 'verified' : 'no_evidence',
                };
            }),
        } : null;
        setSaving(true);
        try {
            const result = await onSave?.({ human, ai });
            if (!mounted.current) return;
            const saved = result?.state?.my_review;
            if (saved) {
                setLastSaved(saved);
                setState((s) => ({ ...stateFrom(criteria, saved), kb: s.kb }));
                setAttempted(false);
            } else if (result?.ok) {
                setAttempted(false);
            }
        } finally {
            if (mounted.current) setSaving(false);
        }
    };

    if (!criteria.length) {
        return (
            <div className={`${iosCard} flex min-h-32 items-center justify-center px-5 text-center text-[13px] text-rose-600`} role="alert">
                Критерии оценки не загрузились. Оценка этой карточки недоступна.
            </div>
        );
    }

    const saveLabel = saving ? 'Сохраняю…'
        : doAi
            ? (correctionIdx.length
                ? (!doHuman ? 'Сохранить разбор'
                    : state.countInQuality ? 'Сохранить и учесть' : 'Сохранить оценку')
                : (state.countInQuality && doHuman ? 'Подтвердить и учесть' : 'Подтвердить оценку ИИ'))
            : state.countInQuality
                ? (inJournal ? 'Сохранить переоценку' : 'Сохранить и учесть')
                : 'Сохранить оценку';

    // Одна строка под кнопкой — самое важное на сейчас, без пересказа кнопки.
    let status = null;
    if (attempted && !problems.ok) {
        status = (
            <p className="flex items-center gap-1.5 text-[12px] font-medium text-amber-700">
                <AlertTriangle size={13} className="shrink-0" />{validationLabel(problems)}
            </p>
        );
    } else if (!hasTranscript && !aiReview?.outcome) {
        status = (
            <p className="text-[12px] text-slate-500">
                {isChat ? 'Переписки нет' : 'Транскрипта нет'} — подтвердить оценку ИИ нельзя, пока данные не загрузятся.
            </p>
        );
    } else if (doAi && correctionIdx.length) {
        status = (
            <p className="text-[12px] text-slate-500">
                Исправлений: <b className="text-blue-600">{correctionIdx.length}</b> — уйдут в черновики базы знаний ИИ
            </p>
        );
    } else if (lastSaved && !dirty && !humanLocked) {
        status = (
            <p className="text-[11.5px] text-slate-400">
                {inJournal ? 'Учтено в качестве сотрудника' : 'Оценка сохранена'}
                {lastSaved.updated_at ? ` · ${lastSaved.updated_at}` : ''}
            </p>
        );
    } else if (aiReview?.outcome && !lastSaved) {
        status = (
            <p className="text-[11.5px] text-slate-400">
                Оценку ИИ уже проверил {aiReview.reviewer || 'сотрудник'}{aiReview.reviewed_at ? `, ${aiReview.reviewed_at}` : ''} —
                сохранится ваша оценка
            </p>
        );
    }

    return (
        <div className="flex min-w-0 flex-col">
            <div className="mobile-sticky-top sticky top-0 z-10 mb-2 rounded-2xl bg-white/95 px-3 py-2 ring-1 ring-slate-200/70 backdrop-blur-xl">
                <div className="flex min-h-7 items-center justify-between gap-2">
                    {/* На телефоне подпись короче: рядом ещё счётчик и итог, и в три слова
                        шапка не помещалась в строку. */}
                    <div className="truncate text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                        <span className="sm:hidden">Критерии</span>
                        <span className="hidden sm:inline">Оценка по критериям</span>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5 whitespace-nowrap">
                        {correctionIdx.length > 1 && !verdictsLocked && (
                            <button type="button" onClick={revertAll} title="Вернуть все исправленные критерии к оценке ИИ"
                                    className={chipCls}>
                                <RotateCcw size={11} />Как у ИИ
                            </button>
                        )}
                        {score == null && (
                            // Итог появляется, когда проставлено всё; до тех пор — сколько
                            // осталось, и нажатие ведёт к первому пустому критерию.
                            <button type="button" onClick={scrollToEmpty}
                                    title="Итог появится, когда будут проставлены все критерии — перейти к пустому"
                                    className="whitespace-nowrap rounded-md px-1 text-[12px] text-slate-500 transition hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                                Заполнено <b className="text-slate-700">{filled}</b> из {criteria.length}
                            </button>
                        )}
                        {/* Критическая ошибка обнуляет итог при любых остальных оценках —
                            его видно сразу, не дожидаясь ручных критериев. */}
                        {(score != null || hasCritError) && (
                            <IosBadge tone={scoreTone(score ?? 0)} className="whitespace-nowrap tabular-nums"
                                      title={hasCritError ? 'Критическая ошибка обнуляет итог' : 'Итог по критериям — формулой журнала'}>
                                {hasCritError && <ShieldAlert size={11} />}Итог: {score ?? 0}
                            </IosBadge>
                        )}
                    </div>
                </div>
            </div>

            {scaleChanged && (
                <div className="mb-2.5 flex items-start gap-2 rounded-2xl bg-amber-50 px-3.5 py-2.5 text-[12.5px] text-amber-800 ring-1 ring-amber-100" role="alert">
                    <RotateCcw size={14} className="mt-0.5 shrink-0" />
                    <span>
                        Шкала сотрудника изменилась после этой оценки ИИ — свою оценку и журнал можно
                        заполнить после «Переоценить».{aiPending ? ' Подтвердить или исправить ИИ можно и сейчас.' : ''}
                    </span>
                </div>
            )}
            {humanLocked && (
                <div className="mb-2.5 flex items-start gap-2 rounded-2xl bg-slate-50 px-3.5 py-2.5 text-[12.5px] text-slate-600 ring-1 ring-slate-200/70">
                    <Lock size={14} className="mt-0.5 shrink-0 text-slate-400" />
                    <span>
                        Оценка уже учтена в журнале{lastSaved?.journal_call_id ? ` (№${lastSaved.journal_call_id})` : ''}.
                        Изменить её можно переоценкой в «Журнале оценок».
                    </span>
                </div>
            )}

            <div className="space-y-2.5">
                {criteria.map((c, i) => (
                    <CriterionReview key={c.idx ?? i} c={c} index={i}
                                     value={state.scores[i]} comment={state.comments[i]} extra={state.kb[c.idx]}
                                     onPick={pick} onComment={setComment} onExtra={setExtra} onRevert={revert}
                                     locked={verdictsLocked} kbEnabled={doAi} kbLocked={saving}
                                     highlight={problemIdx.has(i)}
                                     journalVerdict={journalByOther ? c.human : null}
                                     journalComment={journalByOther ? c.human_comment : null}
                                     transcriptText={transcriptText} onRefine={onRefine} />
                ))}
            </div>

            {/* Общий комментарий — по чипу, а не пустым полем: нужен не всегда. */}
            {humanCanSave && (
                <div className="mt-2.5 px-1">
                    {showComment || state.comment ? (
                        <textarea rows={2} value={state.comment} disabled={verdictsLocked}
                            aria-label="Комментарий к оценке"
                            onChange={(e) => update({ comment: e.target.value })}
                            placeholder="Комментарий к оценке в целом"
                            className={fieldCls} />
                    ) : (
                        <button type="button" onClick={() => setShowComment(true)} disabled={verdictsLocked} className={chipCls}>
                            <Plus size={11} strokeWidth={2.5} />Комментарий к оценке
                        </button>
                    )}
                </div>
            )}

            {/* Что ещё знает строка журнала — только когда оценка туда уходит. */}
            {state.countInQuality && humanCanSave && (
                <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}
                            className={`${iosCard} mt-2.5 divide-y divide-slate-100 px-3.5`}>
                    <SettingRow label="Показывать комментарии оператору" checked={state.commentVisible}
                                onChange={(v) => update({ commentVisible: v })} disabled={verdictsLocked}
                                hint="Как в журнале: оператор увидит комментарии к критериям и к оценке в «Моих оценках». Выключите, если это заметки для супервайзера." />
                    <SettingRow label="Вопрос решён" checked={state.questionResolved}
                                onChange={(v) => update({ questionResolved: v, firstContact: v ? state.firstContact : false })}
                                disabled={verdictsLocked}
                                hint="Отметка журнала: обращение клиента решено в этом разговоре." />
                    {state.questionResolved && (
                        <SettingRow label="С первого обращения" checked={state.firstContact}
                                    onChange={(v) => update({ firstContact: v })} disabled={verdictsLocked} />
                    )}
                </motion.div>
            )}

            <div className="sticky bottom-0 mt-3 flex flex-col gap-1.5 rounded-2xl bg-white/95 px-3 py-2.5 ring-1 ring-slate-200/70 backdrop-blur-xl" aria-live="polite">
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
                    <div className="flex items-center gap-2">
                        <IosToggle checked={state.countInQuality} disabled={toggleDisabled}
                                   onChange={(v) => { update({ countInQuality: v }); setAttempted(false); }} />
                        <span className={`flex items-center gap-1.5 text-[12.5px] font-medium ${toggleDisabled ? 'text-slate-400' : 'text-slate-700'}`}>
                            Учитывать в качестве
                            <IosHint label="Что значит учитывать в качестве" align="left"
                                text={inJournal
                                    ? `Оценка уже в «Журнале оценок»${lastSaved?.journal_call_id ? ` (№${lastSaved.journal_call_id})` : ''} и входит в качество сотрудника. Сохранение с изменениями станет переоценкой этой строки.`
                                    : scaleChanged
                                        ? 'Шкала сотрудника изменилась после этой оценки ИИ: в журнал оценку можно отправить после «Переоценить».'
                                        : toggleBlockedByOther
                                            ? `В журнале этот разговор уже оценил ${journal?.evaluator || 'другой человек'}${journal?.datetime ? ` (${journal.datetime})` : ''}. Переоценить его из карточки может админ или глава отдела; ваша оценка сохранится для сравнения с ИИ.`
                                            : 'Включено — оценка уходит в «Журнал оценок» как оценка супервайзера и входит в качество сотрудника; тогда нужно проставить все критерии. Выключено — оценка сравнивается с ИИ и на качество не влияет.'} />
                        </span>
                        {inJournal && (
                            <IosBadge tone="green" title="Оценка уже в журнале">
                                <BookMarked size={11} />№{lastSaved?.journal_call_id || '—'}
                            </IosBadge>
                        )}
                    </div>
                    <div className="ml-auto flex items-center gap-1.5">
                        {doAi && onSkip && (
                            <button type="button" onClick={onSkip} disabled={saving} className={iosBtnGhost}>
                                Пропустить
                            </button>
                        )}
                        <button type="button" onClick={save} disabled={!canSave} className={iosBtnPrimary}>
                            {saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
                            {saveLabel}
                        </button>
                    </div>
                </div>
                {status}
            </div>
        </div>
    );
}
