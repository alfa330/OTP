import React, { useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import {
    Check, X, Minus, Clock, Sparkles, Server, User2, Headphones,
    Quote, ShieldAlert, ChevronDown, Languages, Save, RotateCcw,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosBadge,
} from '../ui/ios';

/* ──────────────────────────────────────────────────────────────────────────
 * Карточка ревью одного звонка — центральный экран взаимодействия с ИИ.
 * Слева: транскрипт с диаризацией (оператор/клиент) и подсветкой неуверенных
 * мест распознавания. Справа: критерии с вердиктом ИИ, confidence и цитатой;
 * человек подтверждает ✓ или исправляет (с причиной) → уходит в RAG.
 *
 * Данные — мок в форме будущего API-контракта (см. README в этой папке).
 * Props (когда подключат бэкенд): { call, onSave(decisions), onSkip }.
 * ────────────────────────────────────────────────────────────────────────── */

const VERDICT = {
    Correct:   { tone: 'green', label: 'Верно',   Icon: Check },
    Incorrect: { tone: 'red',   label: 'Неверно', Icon: X },
    'N/A':     { tone: 'slate', label: 'N/A',     Icon: Minus },
    Pending:   { tone: 'amber', label: 'Ожидает', Icon: Clock },
};

const SOURCE = {
    transcript: { tone: 'blue',  label: 'ИИ',     Icon: Sparkles, hint: 'оценил ИИ по разговору' },
    system_api: { tone: 'amber', label: 'ПО-API', Icon: Server,   hint: 'нужна проверка данных в ПО' },
    manual:     { tone: 'slate', label: 'Ручная', Icon: User2,    hint: 'только ручная проверка' },
};

const HUMAN_OPTS = [
    { v: 'Correct',   label: 'Верно',   Icon: Check },
    { v: 'Incorrect', label: 'Неверно', Icon: X },
    { v: 'N/A',       label: 'N/A',     Icon: Minus },
];

const MOCK_CALL = {
    id: 6434,
    direction: 'Основа',
    operator: 'Дарина С.',
    datetime: '29.06.2026, 14:12',
    human_score: 97,
    languages: { kk: 99, ru: 1 },
    asr_mean_conf: 0.96,
    transcript: [
        { speaker: 'operator', seg: [{ t: 'Алло, саламатсыз ба. Менің есімім Дарина, мен сізге Яндекс Тіркеу орталығынан хабарласып тұрмын. Сіздің есіміңіз Азамат, солай ма?' }] },
        { speaker: 'client', seg: [{ t: 'Аха.' }] },
        { speaker: 'operator', seg: [{ t: 'Танысқаныма қуаныштымын, Азамат. Яндекс-ке тіркелгіңіз келді, ' }, { t: 'солай ', c: 0.41 }, { t: 'ма?' }] },
        { speaker: 'client', seg: [{ t: 'Иә, иә.' }] },
        { speaker: 'operator', seg: [{ t: 'Кім болып тіркелгіңіз келді: жүргізуші немесе курьер ретінде?' }] },
        { speaker: 'client', seg: [{ t: 'Жүргізуші.' }] },
        { speaker: 'operator', seg: [{ t: 'НордТакси таксопаркінің комиссиясы 0% құрайды. Тіркеу WhatsApp арқылы өтеді, керекті құжаттардың тізімін жіберемін.' }] },
        { speaker: 'client', seg: [{ t: 'Жақсы, рақмет.' }] },
    ],
    criteria: [
        { idx: 0,  name: 'Приветствие', is_critical: false, source: 'transcript', ai: 'Correct', conf: 0.95, evidence: 'Менің есімім Дарина, мен сізге Яндекс Тіркеу орталығынан хабарласып тұрмын', comment: 'Поздоровался, назвал центр и имя.' },
        { idx: 1,  name: 'Персонализация', is_critical: false, source: 'transcript', ai: 'Correct', conf: 0.90, evidence: 'Танысқаныма қуаныштымын, Азамат.', comment: 'Обращается по имени.' },
        { idx: 2,  name: 'Идентификация клиента', is_critical: false, source: 'transcript', ai: 'Correct', conf: 0.85, evidence: 'Сіздің есіміңіз Азамат, солай ма?', comment: 'Уточнил имя.' },
        { idx: 4,  name: 'Выявление потребностей', is_critical: false, source: 'transcript', ai: 'N/A', conf: 0.62, evidence: '', comment: 'Явного этапа выявления нет — короткая регистрация.' },
        { idx: 6,  name: 'Отработка возражений', is_critical: false, source: 'transcript', ai: 'N/A', conf: 0.58, evidence: '', comment: 'Возражений не было.' },
        { idx: 14, name: 'КО_Грубость, хамство, конфликт', is_critical: true, source: 'transcript', ai: 'Correct', conf: 0.97, evidence: '', comment: 'Тон доброжелательный.' },
        { idx: 15, name: 'КО_Достоверность информации', is_critical: true, source: 'transcript', ai: 'Correct', conf: 0.85, evidence: 'комиссиясы 0% құрайды', comment: 'Озвученные условия корректны.' },
        { idx: 12, name: 'Корректность оформления регистрации', is_critical: false, source: 'system_api', ai: 'Pending', conf: null, evidence: '', comment: 'Нужна проверка данных в ПО.' },
        { idx: 19, name: 'КО_Внесение информации в ПО', is_critical: true, source: 'system_api', ai: 'Pending', conf: null, evidence: '', comment: 'Нужна проверка данных в ПО.' },
        { idx: 20, name: 'Сделка состоялась', is_critical: false, source: 'system_api', ai: 'Pending', conf: null, evidence: '', comment: 'Факт регистрации знает система.' },
    ],
};

const confTone = (c) => (c == null ? 'slate' : c >= 0.8 ? 'green' : c >= 0.6 ? 'amber' : 'red');

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
                    {line.seg.map((s, i) => s.c != null && s.c < 0.5
                        ? <mark key={i} title={`распознано неуверенно · ${Math.round(s.c * 100)}%`}
                                className="rounded bg-amber-100 px-0.5 text-amber-800 decoration-amber-400 decoration-dotted underline">{s.t}</mark>
                        : <span key={i}>{s.t}</span>)}
                </span>
            </div>
        </div>
    );
}

function CriterionRow({ c, decision, onDecide }) {
    const [open, setOpen] = useState(false);
    const src = SOURCE[c.source] || SOURCE.transcript;
    const editable = c.source === 'transcript';
    const chosen = decision?.verdict ?? c.ai;
    const corrected = editable && chosen !== c.ai;

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

                    {/* Решение человека */}
                    <div className="mt-2.5 flex items-center gap-1.5">
                        <div className="flex rounded-xl bg-slate-100 p-0.5">
                            {HUMAN_OPTS.map((o) => {
                                const active = chosen === o.v;
                                return (
                                    <button key={o.v} onClick={() => onDecide(c.idx, o.v)}
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
                        <motion.input initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}
                            value={decision?.reason || ''} onChange={(e) => onDecide(c.idx, chosen, e.target.value)}
                            placeholder="Почему так правильно? (запомнится для похожих случаев)"
                            className="mt-2 w-full rounded-xl bg-slate-100 px-3 py-2 text-[12.5px] text-slate-800 placeholder-slate-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/60" />
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

export default function CallReviewCard({ call = MOCK_CALL, onSave, onSkip }) {
    const [decisions, setDecisions] = useState({});

    const onDecide = (idx, verdict, reason) => {
        setDecisions((d) => ({ ...d, [idx]: { verdict, reason: reason ?? d[idx]?.reason ?? '' } }));
    };

    const corrections = useMemo(
        () => call.criteria.filter((c) => c.source === 'transcript' && decisions[c.idx] && decisions[c.idx].verdict !== c.ai),
        [decisions, call.criteria],
    );
    const pendingCount = call.criteria.filter((c) => c.source !== 'transcript').length;

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="grid grid-cols-1 gap-4 lg:grid-cols-[1.05fr_1fr]">
            {/* Левая колонка — звонок + транскрипт */}
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
                            {call.human_score != null && (
                                <IosBadge tone="green">Человек: {call.human_score}</IosBadge>
                            )}
                            <button className={iosBtnSecondary + ' !py-1.5 !px-3'}>
                                <Headphones size={14} />Аудио
                            </button>
                        </div>
                    </div>
                    <div className="mt-3 flex items-center gap-2 text-[11.5px] text-slate-400">
                        <Languages size={13} />
                        {Object.entries(call.languages).map(([l, p]) => (
                            <span key={l} className="rounded-md bg-slate-100 px-1.5 py-0.5 font-medium text-slate-500">
                                {l.toUpperCase()} {p}%
                            </span>
                        ))}
                        <span className="ml-auto">распознавание · {Math.round(call.asr_mean_conf * 100)}%</span>
                    </div>
                </div>

                <div className={`${iosCard} flex max-h-[60vh] flex-col p-0`}>
                    <div className="border-b border-slate-100 px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                        Транскрипт · диаризация
                    </div>
                    <div className="flex-1 space-y-2 overflow-y-auto px-4 py-3">
                        {call.transcript.map((l, i) => <TranscriptLine key={i} line={l} />)}
                    </div>
                    <div className="border-t border-slate-100 px-4 py-2 text-[11px] text-slate-400">
                        <mark className="rounded bg-amber-100 px-1 text-amber-800">жёлтым</mark> — где ИИ не уверен в распознавании (не учитывается против оператора)
                    </div>
                </div>
            </div>

            {/* Правая колонка — критерии + решение */}
            <div className="flex flex-col">
                <div className="mb-2 flex items-center justify-between">
                    <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                        Оценка по критериям
                    </div>
                    {pendingCount > 0 && <IosBadge tone="amber"><Server size={11} />{pendingCount} ждут API</IosBadge>}
                </div>

                <div className="space-y-2.5">
                    {call.criteria.map((c) => (
                        <CriterionRow key={c.idx} c={c} decision={decisions[c.idx]} onDecide={onDecide} />
                    ))}
                </div>

                {/* Липкий футер действий */}
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
