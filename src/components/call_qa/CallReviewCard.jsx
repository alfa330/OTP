import React, { useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import {
    Check, X, Minus, Clock, Sparkles, Server, User2, Headphones,
    Quote, ShieldAlert, ChevronDown, Languages, Save, RotateCcw, Wand2, Loader2,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosInput, iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosBadge,
} from '../ui/ios';

/* Карточка ревью одного звонка — центральный экран взаимодействия с ИИ.
 * Данные приходят только с бэкенда (props.call). Мок-данных нет. */

const scoreTone = (s) => (s == null ? 'slate' : s >= 70 ? 'green' : s >= 50 ? 'amber' : 'red');

const VERDICT = {
    Correct:   { tone: 'green', label: 'Верно',   Icon: Check },
    Incorrect: { tone: 'red',   label: 'Неверно', Icon: X },
    'N/A':     { tone: 'slate', label: 'N/A',     Icon: Minus },
    Pending:   { tone: 'amber', label: 'Ожидает', Icon: Clock },
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
    const v = VERDICT[verdict] || VERDICT['N/A'];
    return <IosBadge tone={v.tone}><v.Icon size={12} strokeWidth={2.5} />{v.label}</IosBadge>;
}

function TranscriptLine({ line }) {
    const isOp = line.speaker === 'operator';
    return (
        <div className={`flex ${isOp ? 'justify-start' : 'justify-end'}`}>
            <div className={`max-w-[88%] rounded-2xl px-3.5 py-2 text-[13.5px] leading-relaxed ${
                isOp ? 'bg-blue-50 text-slate-800 ring-1 ring-blue-100' : 'bg-slate-100 text-slate-700'
            }`}>
                <div className={`mb-0.5 text-[10.5px] font-semibold uppercase tracking-wide ${isOp ? 'text-blue-500' : 'text-slate-400'}`}>
                    {isOp ? 'Оператор' : 'Клиент'}
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
}

const fieldCls = `${iosInput} px-3 py-2 text-[12.5px]`;

function CriterionRow({ c, decision, onEdit, onRefine }) {
    const [open, setOpen] = useState(false);
    const [refining, setRefining] = useState(false);
    const [aiNote, setAiNote] = useState(null);
    const src = SOURCE[c.source] || SOURCE.transcript;
    const editable = c.source === 'transcript';
    const chosen = decision?.verdict ?? c.ai;
    const corrected = editable && chosen !== c.ai;

    const pick = (v) => {
        if (v === chosen) return;
        const patch = { verdict: v };
        // Текст от ИИ формулировался под другой вердикт — сбрасываем, чтобы не сохранить противоречие.
        if (decision?._refined_for && decision._refined_for !== v) {
            patch.reason = ''; patch.situation = ''; patch.not_covered = ''; patch._refined_for = null;
            setAiNote(null);
        }
        onEdit(c.idx, patch);
    };

    const refine = async () => {
        if (!onRefine || refining) return;
        setRefining(true);
        try {
            const p = await onRefine(c, { verdict: chosen, reason: decision?.reason || '' });
            if (p) {
                onEdit(c.idx, {
                    reason: p.rule || decision?.reason || '',
                    situation: p.situation || decision?.situation || '',
                    not_covered: p.not_covered || decision?.not_covered || '',
                    _refined_for: chosen,
                });
                setAiNote(p.note_to_reviewer || null);
            }
        } finally {
            setRefining(false);
        }
    };

    return (
        <div className={`${iosCard} p-3 ${corrected ? 'ring-2 ring-blue-400/60' : ''}`}>
            <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                        {c.is_critical && <ShieldAlert size={13} className="shrink-0 text-rose-500" title="Критический критерий" />}
                        <span className="truncate text-[13.5px] font-medium text-slate-800">{c.name}</span>
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
                        <button onClick={() => setOpen((o) => !o)}
                                className="mt-2 flex items-center gap-1 text-[11.5px] font-medium text-slate-400 hover:text-slate-600">
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

                    <div className="mt-2.5 flex items-center gap-1.5">
                        <div className="flex rounded-xl bg-slate-100 p-0.5">
                            {HUMAN_OPTS.map((o) => {
                                const active = chosen === o.v;
                                return (
                                    <button key={o.v} onClick={() => pick(o.v)}
                                            className={`flex items-center gap-1 rounded-lg px-2.5 py-1 text-[12px] font-semibold transition ${
                                                active ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-400 hover:text-slate-600'}`}>
                                        <o.Icon size={12} strokeWidth={2.5} />{o.label}
                                    </button>
                                );
                            })}
                        </div>
                        {corrected && <span className="text-[11px] font-medium text-blue-500">исправлено</span>}
                    </div>

                    {corrected && (
                        <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} className="mt-2 space-y-1.5">
                            <textarea rows={2} value={decision?.reason || ''}
                                onChange={(e) => onEdit(c.idx, { reason: e.target.value })}
                                placeholder="Почему так правильно? (запомнится для похожих случаев)"
                                className={`${fieldCls} resize-y`} />
                            <div>
                                <div className="mb-0.5 text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">Ситуация (когда применять правило)</div>
                                <input value={decision?.situation || ''}
                                    onChange={(e) => onEdit(c.idx, { situation: e.target.value })}
                                    placeholder="Обобщённо: в какой ситуации действует правило" className={fieldCls} />
                            </div>
                            <div>
                                <div className="mb-0.5 text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">Чего правило НЕ оправдывает</div>
                                <input value={decision?.not_covered || ''}
                                    onChange={(e) => onEdit(c.idx, { not_covered: e.target.value })}
                                    placeholder="Границы: какие нарушения этим правилом не прощаются" className={fieldCls} />
                            </div>
                            {aiNote && <p className="text-[11.5px] leading-snug text-amber-600">{aiNote}</p>}
                            {onRefine && (
                                <div className="flex items-center gap-2">
                                    <button onClick={refine} disabled={refining}
                                        className="flex items-center gap-1.5 rounded-lg bg-violet-50 px-2.5 py-1.5 text-[12px] font-semibold text-violet-600 ring-1 ring-violet-200/70 transition hover:bg-violet-100 disabled:opacity-60">
                                        {refining ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
                                        {refining ? 'Формулирую…' : 'Сформулировать с ИИ'}
                                    </button>
                                    <span className="text-[11px] text-slate-400">подсказка — финальный текст за вами</span>
                                </div>
                            )}
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
}

export default function CallReviewCard({ call, onSave, onSkip, onRefine }) {
    const [decisions, setDecisions] = useState({});
    if (!call) return null;

    const onEdit = (idx, patch) => {
        setDecisions((d) => ({ ...d, [idx]: { ...(d[idx] || {}), ...patch } }));
    };

    const corrections = useMemo(
        () => (call.criteria || []).filter((c) => c.source === 'transcript' && decisions[c.idx] && decisions[c.idx].verdict !== c.ai),
        [decisions, call.criteria],
    );
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
                            <p className="mt-0.5 text-[12.5px] text-slate-400">{call.operator} · {call.datetime}</p>
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
                    <div className="mt-3 flex items-center gap-2 text-[11.5px] text-slate-400">
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
                            <audio controls preload="none" src={call.audio_url} className="h-9 w-full" />
                        </div>
                    )}
                </div>

                <div className={`${iosCard} flex max-h-[60vh] flex-col p-0`}>
                    <div className="border-b border-slate-100 px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                        Транскрипт · диаризация
                    </div>
                    <div className="flex-1 space-y-2 overflow-y-auto px-4 py-3">
                        {(call.transcript || []).map((l, i) => <TranscriptLine key={i} line={l} />)}
                    </div>
                    <div className="border-t border-slate-100 px-4 py-2 text-[11px] text-slate-400">
                        <mark className="rounded bg-amber-100 px-1 text-amber-800">жёлтым</mark> — где ИИ не уверен в распознавании (не учитывается против оператора)
                    </div>
                </div>
            </div>

            <div className="flex flex-col">
                <div className="mb-2 flex items-center justify-between">
                    <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                        Оценка по критериям
                    </div>
                    {pendingCount > 0 && <IosBadge tone="amber"><Server size={11} />{pendingCount} ждут API</IosBadge>}
                </div>

                <div className="space-y-2.5">
                    {(call.criteria || []).map((c) => (
                        <CriterionRow key={c.idx} c={c} decision={decisions[c.idx]}
                                      onEdit={onEdit} onRefine={onRefine} />
                    ))}
                </div>

                <div className="sticky bottom-0 mt-3 flex items-center justify-between gap-2 rounded-2xl bg-white/85 px-3 py-2.5 ring-1 ring-slate-200/70 backdrop-blur-xl">
                    <span className="text-[12.5px] text-slate-500">
                        {corrections.length > 0
                            ? <>Исправлений: <b className="text-blue-600">{corrections.length}</b> → в память ИИ</>
                            : 'Согласен с оценкой ИИ'}
                    </span>
                    <div className="flex items-center gap-2">
                        {onSkip && (
                            <button onClick={onSkip} className={iosBtnGhost}>
                                <RotateCcw size={14} />Пропустить
                            </button>
                        )}
                        <button onClick={() => onSave?.(decisions)} className={iosBtnPrimary}>
                            <Save size={15} />
                            {corrections.length > 0 ? 'Сохранить разбор' : 'Подтвердить'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
