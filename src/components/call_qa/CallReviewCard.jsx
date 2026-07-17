import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import {
    Check, X, Minus, Clock, Sparkles, Server, User2, Headphones,
    Quote, ShieldAlert, ChevronDown, Languages, Save, RotateCcw, Wand2, Loader2,
    Database, Search, Timer, Hash, AlertTriangle, ShieldCheck,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosInput, iosBtnPrimary, iosBtnGhost, IosBadge,
} from '../ui/ios';

/* Карточка ревью одного звонка — центральный экран взаимодействия с ИИ.
 * Данные приходят только с бэкенда (props.call). Мок-данных нет. */

const scoreTone = (s) => (s == null ? 'slate' : s >= 70 ? 'green' : s >= 50 ? 'amber' : 'red');
const formatTimestamp = (ms) => {
    const total = Math.max(0, Math.floor(Number(ms || 0) / 1000));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
};

// Клиентская предпроверка цитаты повторяет нормализацию сервера
// (call_qa/review/evidence.py: NFKC + приведение регистра, только буквы/цифры,
// схлопывание пробелов). Нужна, чтобы «Подтверждаю» не пропускал цитату, которой
// нет в транскрипте дословно, — иначе отказ пришёл бы только при сохранении.
const normalizeForMatch = (value) => (value || '')
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim();

const excerptFoundInTranscript = (excerpt, transcriptText) => {
    const needle = normalizeForMatch(excerpt);
    if (needle.length < 4) return false;   // сервер тоже отклоняет фрагменты короче 4 символов
    return normalizeForMatch(transcriptText).includes(needle);
};

const VERDICT = {
    Correct:    { tone: 'green', label: 'Верно',   Icon: Check },
    Deficiency: { tone: 'amber', label: 'Недочёт', Icon: AlertTriangle },
    Incorrect:  { tone: 'red',   label: 'Неверно', Icon: X },
    'N/A':      { tone: 'slate', label: 'N/A',     Icon: Minus },
    Pending:    { tone: 'amber', label: 'Ожидает', Icon: Clock },
    // «Критич. ошибка» ставит только супервайзер (панель «Супервайзер»).
    Error:      { tone: 'red',   label: 'Критич. ошибка', Icon: ShieldAlert },
};

const SOURCE = {
    transcript: { tone: 'blue',  label: 'ИИ',     Icon: Sparkles },
    system_api: { tone: 'amber', label: 'ПО-API', Icon: Server,   hint: 'нужна проверка данных в ПО' },
    manual:     { tone: 'slate', label: 'Ручная', Icon: User2,    hint: 'только ручная проверка' },
};

const HUMAN_OPTS = [
    { v: 'Correct',   label: 'Верно',   Icon: Check },
    { v: 'Incorrect', label: 'Неверно', Icon: X },
    { v: 'N/A',       label: 'N/A',     Icon: Minus },
];
// «Недочёт» доступен только критериям, у которых он предусмотрен шкалой (c.deficiency).
const DEFICIENCY_OPT = { v: 'Deficiency', label: 'Недочёт', Icon: AlertTriangle };

// Закреплённые переключатели под «Оценка по критериям»: чью оценку показываем.
const PANELS = [
    { key: 'ai', label: 'ИИ',          Icon: Sparkles },
    { key: 'sv', label: 'Супервайзер', Icon: User2 },
];

function ConfidenceBar({ value }) {
    if (value == null) return <span className="text-[11px] text-slate-400">—</span>;
    const pct = Math.round(value * 100);
    const color = value >= 0.8 ? 'bg-emerald-500' : value >= 0.6 ? 'bg-amber-500' : 'bg-rose-500';
    return (
        <div className="flex items-center gap-1.5">
            <div className="h-1.5 w-12 overflow-hidden rounded-full bg-slate-200">
                <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
            </div>
            <span className="tabular-nums text-[11px] font-medium text-slate-500">{pct}%</span>
        </div>
    );
}

function VerdictChip({ verdict }) {
    if (verdict == null) {
        return <IosBadge tone="slate"><Minus size={12} strokeWidth={2.5} />Нет оценки</IosBadge>;
    }
    const v = VERDICT[verdict] || VERDICT['N/A'];
    return <IosBadge tone={v.tone}><v.Icon size={12} strokeWidth={2.5} />{v.label}</IosBadge>;
}

const TranscriptLine = memo(function TranscriptLine({ line, onSeek }) {
    const isOp = line.speaker === 'operator';
    return (
        <div className={`flex ${isOp ? 'justify-start' : 'justify-end'}`}>
            <div className={`max-w-[88%] rounded-2xl px-3.5 py-2 text-[13.5px] leading-relaxed ${
                isOp ? 'bg-blue-50 text-slate-800 ring-1 ring-blue-100' : 'bg-slate-100 text-slate-700'
            }`}>
                <div className={`mb-0.5 flex items-center justify-between gap-3 text-[10.5px] font-semibold uppercase tracking-wide ${isOp ? 'text-blue-600' : 'text-slate-500'}`}>
                    <span>{isOp ? 'Оператор' : 'Клиент'}</span>
                    {line.start_ms != null && (
                        <button type="button" onClick={() => onSeek?.(line.start_ms)}
                            className="rounded px-1 py-0.5 font-medium tabular-nums text-slate-500 hover:bg-white/70 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60"
                            aria-label={`Перейти к ${formatTimestamp(line.start_ms)} записи`}>
                            {formatTimestamp(line.start_ms)}
                        </button>
                    )}
                </div>
                <span>
                    {(line.seg || []).map((s, i) => s.c != null && s.c < 0.5
                        ? <mark key={i} title={`распознано неуверенно · ${Math.round(s.c * 100)}%`}
                                className="rounded bg-amber-100 px-0.5 text-amber-800 decoration-amber-400 decoration-dotted underline">{s.t}</mark>
                        : <span key={i}>{s.t}</span>)}
                </span>
            </div>
        </div>
    );
});

const fieldCls = `${iosInput} px-3 py-2 text-[12.5px]`;

function EvaluationMeta({ evaluation }) {
    if (!evaluation) return null;
    const retrievalStatus = String(evaluation.retrieval_status || '').toLowerCase();
    const retrievalTone = ['ready', 'ok', 'complete', 'completed'].includes(retrievalStatus)
        ? 'green' : ['degraded', 'partial', 'stale'].includes(retrievalStatus) ? 'amber'
            : ['failed', 'error', 'unavailable'].includes(retrievalStatus) ? 'red' : 'slate';
    const retrievalLabel = {
        ready: 'Retrieval готов', ok: 'Retrieval готов', complete: 'Retrieval завершён', completed: 'Retrieval завершён',
        degraded: 'Retrieval ограничен', partial: 'Retrieval частичный', stale: 'Retrieval устарел',
        failed: 'Ошибка retrieval', error: 'Ошибка retrieval', unavailable: 'Retrieval недоступен',
        disabled: 'Retrieval отключён', skipped: 'Retrieval пропущен',
    }[retrievalStatus] || (retrievalStatus ? `Retrieval: ${retrievalStatus}` : null);
    const retrieved = evaluation.retrieved_count ?? evaluation.retrieved;
    const included = evaluation.included_count ?? evaluation.included;

    return (
        <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-slate-100 pt-2.5" aria-label="Метаданные оценки">
            {evaluation.fingerprint_short && (
                <IosBadge tone="slate" className="font-mono" title={`Fingerprint оценки: ${evaluation.fingerprint_short}`}>
                    <Hash size={10} />{evaluation.fingerprint_short}
                </IosBadge>
            )}
            {evaluation.knowledge_revision != null && (
                <IosBadge tone="blue"><Database size={10} />База r{evaluation.knowledge_revision}</IosBadge>
            )}
            {retrievalLabel && <IosBadge tone={retrievalTone}><Search size={10} />{retrievalLabel}</IosBadge>}
            {(retrieved != null || included != null) && (
                <IosBadge tone="slate"><Search size={10} />Правила: {included ?? '—'} из {retrieved ?? '—'}</IosBadge>
            )}
            {evaluation.retrieval_ms != null && (
                <IosBadge tone="slate"><Timer size={10} />{Math.round(evaluation.retrieval_ms)} мс</IosBadge>
            )}
            {evaluation.stale && <IosBadge tone="amber"><AlertTriangle size={10} />Устаревший снимок</IosBadge>}
        </div>
    );
}

function EvidenceReview({ c, decision, onEdit, disabled, transcriptText }) {
    const [notFound, setNotFound] = useState(false);
    const evidenceStatus = decision?.evidence_status || null;
    const noEvidence = evidenceStatus === 'no_evidence';
    const excerpt = decision?.excerpt ?? c.evidence ?? '';
    const hasEvidence = !noEvidence && Boolean(excerpt.trim());
    const verified = evidenceStatus === 'verified' && decision?.excerpt_verified === true && excerpt.trim().length > 0;

    const chooseEvidence = () => onEdit(c.idx, {
        excerpt: excerpt || c.evidence || '',
        excerpt_verified: false,
        evidence_status: null,
    });
    const chooseNoEvidence = () => {
        setNotFound(false);
        onEdit(c.idx, { excerpt: '', excerpt_verified: false, evidence_status: 'no_evidence' });
    };
    const updateExcerpt = (value) => {
        setNotFound(false);
        onEdit(c.idx, { excerpt: value, excerpt_verified: false, evidence_status: null });
    };
    const verifyExcerpt = () => {
        const normalized = excerpt.trim();
        if (!normalized) return;
        // Не подтверждаем цитату, которой нет в транскрипте дословно: сервер её
        // всё равно отклонит, а так проверяющий узнаёт об этом сразу, а не при сохранении.
        if (!excerptFoundInTranscript(normalized, transcriptText)) {
            setNotFound(true);
            onEdit(c.idx, { excerpt: normalized, excerpt_verified: false, evidence_status: null });
            return;
        }
        setNotFound(false);
        onEdit(c.idx, { excerpt: normalized, excerpt_verified: true, evidence_status: 'verified' });
    };

    return (
        <fieldset className={`rounded-xl p-3 ring-1 ${verified ? 'bg-emerald-50/60 ring-emerald-200' : noEvidence ? 'bg-slate-100/70 ring-slate-200' : 'bg-amber-50/60 ring-amber-200'}`}>
            <legend className="px-1 text-[10.5px] font-semibold uppercase tracking-wide text-slate-500">Подтверждение по транскрипту</legend>
            <p className="mb-2 text-[11.5px] leading-snug text-slate-500">
                Сверьте цитату с транскриптом. Текст, предложенный ИИ, не считается подтверждённым автоматически.
            </p>
            <div className="grid gap-1.5 sm:grid-cols-2" role="group" aria-label="Наличие подтверждающей цитаты">
                <button type="button" onClick={chooseEvidence} disabled={disabled} aria-pressed={hasEvidence}
                    className={`rounded-lg px-2.5 py-2 text-left text-[11.5px] font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
                        hasEvidence ? 'bg-white text-blue-700 shadow-sm ring-1 ring-blue-100' : 'bg-white/60 text-slate-500 hover:bg-white'}`}>
                    <Quote size={12} className="mr-1.5 inline" />Есть подтверждающая цитата
                </button>
                <button type="button" onClick={chooseNoEvidence} disabled={disabled} aria-pressed={noEvidence}
                    className={`rounded-lg px-2.5 py-2 text-left text-[11.5px] font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
                        noEvidence ? 'bg-white text-slate-800 shadow-sm ring-1 ring-slate-200' : 'bg-white/60 text-slate-500 hover:bg-white'}`}>
                    <Minus size={12} className="mr-1.5 inline" />В транскрипте нет подтверждающей цитаты
                </button>
            </div>
            {!noEvidence && (
                <div className="mt-2 space-y-2">
                    <label className="block">
                        <span className="mb-1 block text-[10.5px] font-semibold uppercase tracking-wide text-slate-500">Точная цитата</span>
                        <textarea rows={2} value={excerpt} disabled={disabled}
                            onChange={(event) => updateExcerpt(event.target.value)}
                            placeholder="Скопируйте фрагмент из транскрипта без пересказа"
                            className={`${fieldCls} resize-y ${verified ? '!bg-white ring-1 ring-emerald-200' : ''}`} />
                    </label>
                    <button type="button" onClick={verifyExcerpt} disabled={disabled || !excerpt.trim() || verified}
                        className={`flex w-full items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-[11.5px] font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/60 disabled:cursor-not-allowed ${
                            verified ? 'bg-emerald-100 text-emerald-700' : 'bg-white text-emerald-700 ring-1 ring-emerald-200 hover:bg-emerald-50 disabled:opacity-50'}`}>
                        {verified ? <ShieldCheck size={14} /> : <Check size={14} />}
                        {verified ? 'Цитата сверена и подтверждена' : 'Подтверждаю: цитата дословно есть в транскрипте'}
                    </button>
                    {notFound && (
                        <p className="flex items-start gap-1.5 text-[11.5px] font-medium text-rose-600">
                            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                            Не нашли эту цитату в транскрипте дословно. Скопируйте точный фрагмент из транскрипта слева или отметьте, что подтверждающей цитаты нет.
                        </p>
                    )}
                </div>
            )}
            {noEvidence && (
                <p className="mt-2 flex items-start gap-1.5 text-[11.5px] leading-snug text-slate-500">
                    <Check size={13} className="mt-0.5 shrink-0" />Сохранится явная отметка об отсутствии цитаты, а не текст, созданный моделью.
                </p>
            )}
            {!verified && !noEvidence && (
                <p className="mt-2 flex items-start gap-1.5 text-[11.5px] font-medium text-amber-700">
                    <AlertTriangle size={13} className="mt-0.5 shrink-0" />Чтобы сохранить исправление, подтвердите цитату или отметьте её отсутствие.
                </p>
            )}
        </fieldset>
    );
}

const CriterionRow = memo(function CriterionRow({ c, decision, onEdit, onRefine, disabled = false, transcriptText, panel = 'ai' }) {
    const [open, setOpen] = useState(false);
    const [refining, setRefining] = useState(false);
    const [aiNote, setAiNote] = useState(null);
    const refineRequest = useRef(0);
    const src = SOURCE[c.source] || SOURCE.transcript;
    const svPanel = panel === 'sv';
    const editable = !svPanel && c.source === 'transcript';
    const chosen = decision?.verdict ?? c.ai;
    const chosenRef = useRef(chosen);
    chosenRef.current = chosen;
    const corrected = editable && chosen !== c.ai;
    const rowDisabled = disabled || refining;
    const verdictOpts = c.deficiency
        ? [HUMAN_OPTS[0], HUMAN_OPTS[1], DEFICIENCY_OPT, HUMAN_OPTS[2]]
        : HUMAN_OPTS;

    useEffect(() => () => { refineRequest.current += 1; }, []);

    const pick = (v) => {
        if (v === chosen) return;
        const patch = { verdict: v };
        if (!decision && v !== c.ai) {
            patch.excerpt = c.evidence || '';
            patch.excerpt_verified = false;
            patch.evidence_status = null;
        }
        // Текст от ИИ формулировался под другой вердикт — сбрасываем, чтобы не сохранить противоречие.
        if (decision && v !== chosen) {
            patch.reason = ''; patch.situation = ''; patch.not_covered = ''; patch._refined_for = null;
            setAiNote(null);
        }
        onEdit(c.idx, patch);
    };

    const refine = async () => {
        if (!onRefine || refining || disabled) return;
        const requestId = ++refineRequest.current;
        const requestedVerdict = chosen;
        setRefining(true);
        try {
            const p = await onRefine(c, {
                ...decision,
                verdict: chosen,
                reason: decision?.reason || '',
                excerpt: decision?.excerpt ?? c.evidence ?? '',
                excerpt_verified: decision?.excerpt_verified === true,
                evidence_status: decision?.evidence_status || null,
            });
            if (p && requestId === refineRequest.current && chosenRef.current === requestedVerdict) {
                onEdit(c.idx, {
                    reason: p.rule || decision?.reason || '',
                    situation: p.situation || decision?.situation || '',
                    not_covered: p.not_covered || decision?.not_covered || '',
                    _refined_for: chosen,
                });
                setAiNote(p.note_to_reviewer || null);
            }
        } finally {
            if (requestId === refineRequest.current) setRefining(false);
        }
    };

    if (svPanel) {
        // Панель «Супервайзер»: что по этому критерию поставил и написал человек
        // (calls.scores / criterion_comments) — только просмотр, без правок.
        return (
            <div className={`${iosCard} p-3`}>
                <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                            {c.is_critical && <ShieldAlert size={13} className="shrink-0 text-rose-500" title="Критический критерий" />}
                            <span className="text-[13.5px] font-medium leading-snug text-slate-800">{c.name}</span>
                        </div>
                        <div className="mt-1 flex items-center gap-1.5">
                            <IosBadge tone="slate" className="!px-2 !py-0.5"><User2 size={11} />Супервайзер</IosBadge>
                        </div>
                    </div>
                    <VerdictChip verdict={c.human ?? null} />
                </div>
                {c.human != null || c.human_comment ? (
                    c.human_comment ? (
                        <p className="mt-2 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[12.5px] text-slate-600 ring-1 ring-slate-100">{c.human_comment}</p>
                    ) : (
                        <p className="mt-2 text-[12px] text-slate-400">Без комментария супервайзера.</p>
                    )
                ) : (
                    <p className="mt-2 text-[12px] text-slate-400">Супервайзер не оценивал этот критерий.</p>
                )}
            </div>
        );
    }

    return (
        <div className={`${iosCard} p-3 ${corrected ? 'ring-2 ring-blue-400/60' : ''}`}>
            <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                        {c.is_critical && <ShieldAlert size={13} className="shrink-0 text-rose-500" title="Критический критерий" />}
                        <span className="text-[13.5px] font-medium leading-snug text-slate-800">{c.name}</span>
                    </div>
                    <div className="mt-1 flex items-center gap-1.5">
                        <IosBadge tone={src.tone} className="!px-2 !py-0.5"><src.Icon size={11} />{src.label}</IosBadge>
                        {editable && <ConfidenceBar value={c.conf} />}
                    </div>
                </div>
                <VerdictChip verdict={chosen} />
            </div>

            {editable ? (
                <>
                    {(c.evidence || c.comment) && (
                        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
                                className="mt-2 flex items-center gap-1 rounded-md text-[12px] font-medium text-slate-500 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                            <ChevronDown size={13} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
                            Обоснование ИИ
                        </button>
                    )}
                    {open && (
                        <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }}
                                    className="mt-1.5 space-y-1.5 overflow-hidden">
                            {c.comment && <p className="text-[12.5px] text-slate-500">{c.comment}</p>}
                            {c.evidence && (
                                <p className="flex gap-1.5 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[12.5px] italic text-slate-600 ring-1 ring-slate-100">
                                    <Quote size={13} className="mt-0.5 shrink-0 text-slate-300" />«{c.evidence}»
                                </p>
                            )}
                        </motion.div>
                    )}

                    <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                        <div className="flex rounded-xl bg-slate-100 p-0.5" role="group" aria-label={`Решение по критерию «${c.name}»`}>
                            {verdictOpts.map((o) => {
                                const active = chosen === o.v;
                                return (
                                    <button key={o.v} type="button" onClick={() => pick(o.v)} disabled={rowDisabled} aria-pressed={active}
                                            className={`flex min-h-9 items-center gap-1 rounded-lg px-2.5 py-1.5 text-[12px] font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
                                                 active ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>
                                        <o.Icon size={12} strokeWidth={2.5} />{o.label}
                                    </button>
                                );
                            })}
                        </div>
                        {corrected && <span className="text-[11px] font-medium text-blue-500">исправлено</span>}
                    </div>

                    {corrected && (
                        <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} className="mt-2 space-y-1.5">
                            <label className="block">
                                <span className="mb-0.5 block text-[10.5px] font-semibold uppercase tracking-wide text-slate-500">Правило <span className="normal-case text-rose-500">· обязательно</span></span>
                                <textarea rows={2} value={decision?.reason || ''} disabled={rowDisabled}
                                onChange={(e) => onEdit(c.idx, { reason: e.target.value })}
                                placeholder="Почему так правильно? Сформулируйте правило для похожих случаев"
                                className={`${fieldCls} resize-y`} />
                            </label>
                            <label className="block">
                                <span className="mb-0.5 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Ситуация (когда применять правило)</span>
                                <input value={decision?.situation || ''} disabled={rowDisabled}
                                    onChange={(e) => onEdit(c.idx, { situation: e.target.value })}
                                    placeholder="Обобщённо: в какой ситуации действует правило" className={fieldCls} />
                            </label>
                            <label className="block">
                                <span className="mb-0.5 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Чего правило не оправдывает</span>
                                <input value={decision?.not_covered || ''} disabled={rowDisabled}
                                    onChange={(e) => onEdit(c.idx, { not_covered: e.target.value })}
                                    placeholder="Границы: какие нарушения этим правилом не прощаются" className={fieldCls} />
                            </label>
                            {aiNote && <p className="text-[11.5px] leading-snug text-amber-600">{aiNote}</p>}
                            {onRefine && (
                                <div className="flex items-center gap-2">
                                    <button type="button" onClick={refine} disabled={refining || disabled}
                                        className="flex min-h-9 items-center gap-1.5 rounded-lg bg-violet-50 px-2.5 py-1.5 text-[12px] font-semibold text-violet-600 ring-1 ring-violet-200/70 transition hover:bg-violet-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/60 disabled:opacity-60">
                                        {refining ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
                                        {refining ? 'Формулирую…' : 'Сформулировать с ИИ'}
                                    </button>
                                    <span className="text-[11px] text-slate-500">подсказка — финальный текст за вами</span>
                                </div>
                            )}
                            <EvidenceReview c={c} decision={decision} onEdit={onEdit} disabled={rowDisabled} transcriptText={transcriptText} />
                        </motion.div>
                    )}
                </>
            ) : (
                <p className="mt-2 flex items-center gap-1.5 text-[12px] text-amber-600">
                    <Server size={13} />{src.hint} — будет проверено автоматически, когда подключат API.
                </p>
            )}
        </div>
    );
});

export default function CallReviewCard({ call, onSave, onSkip, onRefine, onInteractionChange }) {
    const [decisions, setDecisions] = useState({});
    const [saving, setSaving] = useState(false);
    const [panel, setPanel] = useState('ai');
    const audioRef = useRef(null);

    const seekAudio = useCallback((startMs) => {
        if (!audioRef.current) return;
        audioRef.current.currentTime = Math.max(0, Number(startMs || 0) / 1000);
        audioRef.current.focus();
    }, []);

    const onEdit = useCallback((idx, patch) => {
        setDecisions((d) => ({ ...d, [idx]: { ...(d[idx] || {}), ...patch } }));
    }, []);

    // Все хуки — до раннего return: иначе появление call между рендерами меняет
    // количество хуков и React падает («Rendered more hooks…»).
    const corrections = useMemo(
        () => (call?.criteria || []).filter((c) => c.source === 'transcript' && decisions[c.idx] && decisions[c.idx].verdict !== c.ai),
        [decisions, call],
    );
    // Текст транскрипта для клиентской сверки цитаты (тот же источник, что видит проверяющий).
    const transcriptText = useMemo(
        () => (call?.transcript || [])
            .map((line) => (line.seg || []).map((seg) => seg.t || '').join(''))
            .join('\n'),
        [call],
    );
    const incompleteCorrections = useMemo(() => corrections.filter((c) => {
        const decision = decisions[c.idx] || {};
        const evidenceReady = decision.evidence_status === 'no_evidence' || (
            decision.evidence_status === 'verified' && decision.excerpt_verified === true && Boolean(decision.excerpt?.trim())
        );
        return !decision.reason?.trim() || !evidenceReady;
    }), [corrections, decisions]);
    const hasCriteria = Boolean(call?.criteria?.length);
    const hasTranscript = Boolean(call?.transcript?.length);
    const canSubmit = hasCriteria && hasTranscript;
    // Есть ли у звонка оценка супервайзера (calls.scores) — иначе панель «Супервайзер» пуста.
    const hasHumanReview = useMemo(
        () => call?.has_human_review === true || (call?.criteria || []).some((c) => c.human != null),
        [call],
    );

    useEffect(() => {
        onInteractionChange?.({ dirty: corrections.length > 0, busy: saving });
    }, [corrections.length, saving, onInteractionChange]);

    useEffect(() => () => onInteractionChange?.({ dirty: false, busy: false }), [onInteractionChange]);

    const submit = async () => {
        if (saving || !canSubmit || incompleteCorrections.length > 0) return;
        setSaving(true);
        try {
            await onSave?.(decisions);
        } finally {
            setSaving(false);
        }
    };
    if (!call) return null;

    const pendingCount = (call.criteria || []).filter((c) => c.source !== 'transcript').length;

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="grid grid-cols-1 gap-4 lg:grid-cols-[1.05fr_1fr]">
            <div className="space-y-3">
                <div className={`${iosCard} p-4`}>
                    <div className="flex items-start justify-between gap-3">
                        <div>
                            <div className="flex items-center gap-2">
                                <span className="text-[15px] font-semibold text-slate-900">Звонок #{call.id}</span>
                                <IosBadge tone="slate">{call.direction}</IosBadge>
                            </div>
                            <p className="mt-0.5 text-[12.5px] text-slate-500">{call.operator} · {call.datetime}</p>
                        </div>
                        <div className="flex flex-col items-end gap-1.5">
                            {call.ai_score != null && (
                                <IosBadge tone={scoreTone(call.ai_score)}><Sparkles size={11} />ИИ: {Math.round(call.ai_score)}</IosBadge>
                            )}
                            {call.human_score != null && (
                                <IosBadge tone={scoreTone(call.human_score)}><User2 size={11} />Человек: {Math.round(call.human_score)}</IosBadge>
                            )}
                        </div>
                    </div>
                    <div className="mt-3 flex items-center gap-2 text-[11.5px] text-slate-500">
                        <Languages size={13} />
                        {Object.entries(call.languages || {}).map(([l, p]) => (
                            <span key={l} className="rounded-md bg-slate-100 px-1.5 py-0.5 font-medium text-slate-500">
                                {l.toUpperCase()} {p}%
                            </span>
                        ))}
                        {call.asr_mean_conf != null && (
                            <span className="ml-auto">распознавание · {Math.round(call.asr_mean_conf * 100)}%</span>
                        )}
                    </div>
                    {call.audio_url && (
                        <div className="mt-3 flex items-center gap-2">
                            <Headphones size={15} className="shrink-0 text-slate-400" />
                            <audio ref={audioRef} controls preload="none" src={call.audio_url} className="h-9 w-full"
                                   aria-label={`Запись звонка ${call.id}`} />
                        </div>
                    )}
                    <EvaluationMeta evaluation={call.evaluation} />
                </div>

                <div className={`${iosCard} flex max-h-[60vh] flex-col p-0`}>
                    <div className="border-b border-slate-100 px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                        Транскрипт · диаризация
                    </div>
                    <div className="flex-1 space-y-2 overflow-y-auto px-4 py-3" tabIndex={0} aria-label="Транскрипт звонка">
                        {(call.transcript || []).length > 0
                            ? call.transcript.map((l, i) => <TranscriptLine key={i} line={l} onSeek={call.audio_url ? seekAudio : undefined} />)
                            : <div className="flex min-h-32 items-center justify-center text-center text-[13px] text-slate-500">
                                Транскрипт отсутствует. Не подтверждайте оценку, пока данные не будут загружены.
                              </div>}
                    </div>
                    <div className="border-t border-slate-100 px-4 py-2 text-[11px] text-slate-500">
                        <mark className="rounded bg-amber-100 px-1 text-amber-800">жёлтым</mark> — где ИИ не уверен в распознавании (не учитывается против оператора)
                    </div>
                </div>
            </div>

            <div className="flex flex-col">
                <div className="sticky top-0 z-10 mb-2 rounded-2xl bg-white/95 px-2.5 py-2 ring-1 ring-slate-200/70 backdrop-blur-xl">
                    <div className="flex items-center justify-between gap-2">
                        <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                            Оценка по критериям
                        </div>
                        {pendingCount > 0 && <IosBadge tone="amber"><Server size={11} />{pendingCount} ждут API</IosBadge>}
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-2">
                        <div className="flex rounded-xl bg-slate-100 p-0.5" role="group" aria-label="Чья оценка показана по критериям">
                            {PANELS.map((p) => {
                                const active = panel === p.key;
                                const svDisabled = p.key === 'sv' && !hasHumanReview;
                                return (
                                    <button key={p.key} type="button" onClick={() => setPanel(p.key)}
                                            disabled={svDisabled} aria-pressed={active}
                                            title={svDisabled ? 'Супервайзер ещё не оценил этот звонок' : undefined}
                                            className={`flex min-h-8 items-center gap-1 rounded-lg px-2.5 py-1 text-[12px] font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 disabled:cursor-not-allowed disabled:opacity-50 ${
                                                active ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>
                                        <p.Icon size={12} strokeWidth={2.5} />{p.label}
                                    </button>
                                );
                            })}
                        </div>
                        {panel === 'sv' && call.human_score != null && (
                            <IosBadge tone={scoreTone(call.human_score)}><User2 size={11} />Итог супервайзера: {Math.round(call.human_score)}</IosBadge>
                        )}
                        {!hasHumanReview && (
                            <span className="text-[11px] text-slate-400">оценки супервайзера пока нет</span>
                        )}
                    </div>
                </div>

                <div className="space-y-2.5">
                    {hasCriteria ? call.criteria.map((c) => (
                            <CriterionRow key={`${panel}:${c.idx}`} c={c} decision={decisions[c.idx]}
                                          onEdit={onEdit} onRefine={onRefine} disabled={saving}
                                          transcriptText={transcriptText} panel={panel} />
                        )) : (
                            <div className={`${iosCard} flex min-h-32 items-center justify-center px-5 text-center text-[13px] text-rose-600`} role="alert">
                                Критерии оценки не загрузились. Подтверждение этой карточки недоступно.
                            </div>
                        )}
                </div>

                {panel === 'ai' && (
                <div className="sticky bottom-0 mt-3 flex flex-col gap-2 rounded-2xl bg-white/95 px-3 py-2.5 ring-1 ring-slate-200/70 backdrop-blur-xl sm:flex-row sm:items-center sm:justify-between" aria-live="polite">
                    <span className={`text-[12.5px] ${incompleteCorrections.length ? 'font-medium text-amber-700' : 'text-slate-500'}`}>
                        {!canSubmit
                            ? `Подтверждение недоступно: ${!hasCriteria ? 'нет критериев' : 'нет транскрипта'}`
                            : incompleteCorrections.length > 0
                            ? <>Нужно завершить: <b>{incompleteCorrections.length}</b> — заполните правило и подтверждение</>
                            : corrections.length > 0
                                ? <>Исправлений: <b className="text-blue-600">{corrections.length}</b> → в черновики базы знаний</>
                                : 'Согласен с оценкой ИИ'}
                    </span>
                    <div className="flex items-center gap-2">
                        {onSkip && (
                            <button type="button" onClick={onSkip} disabled={saving} className={iosBtnGhost}>
                                <RotateCcw size={14} />Пропустить без сохранения
                            </button>
                        )}
                        <button type="button" onClick={submit} disabled={saving || !canSubmit || incompleteCorrections.length > 0} className={iosBtnPrimary}>
                            {saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
                            {saving ? 'Сохраняю…' : corrections.length > 0 ? 'Сохранить разбор' : 'Подтвердить оценку ИИ'}
                        </button>
                    </div>
                </div>
                )}
            </div>
        </div>
    );
}
