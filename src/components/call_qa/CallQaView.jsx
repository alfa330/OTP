import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { motion } from 'framer-motion';
import {
    Sparkles, ListChecks, ClipboardList, SlidersHorizontal, Database,
    ChevronLeft, ShieldAlert, Gauge, Server, Clock, Loader2, AlertCircle, RotateCcw,
} from 'lucide-react';
import { APPLE_FONT, iosCard, iosBtnGhost, iosBtnSecondary, IosBadge } from '../ui/ios';
import CallReviewCard from './CallReviewCard';
import QaDashboard from './QaDashboard';
import EvaluationsList from './EvaluationsList';
import CriteriaClassification from './CriteriaClassification';
import AdjudicationsRag from './AdjudicationsRag';

/* Контейнер раздела «ИИ-оценка» (App.jsx: view === "ai_qa", только super_admin).
 * Все данные — реальные с /api/ai-qa/*. Мок-данных нет; при недоступности бэкенда
 * показываются состояния загрузки / ошибки / пусто. */

const TABS = [
    { key: 'overview',  label: 'Обзор',          Icon: Gauge },
    { key: 'queue',     label: 'Очередь ревью',  Icon: ListChecks },
    { key: 'evals',     label: 'Оценки',         Icon: ClipboardList },
    { key: 'criteria',  label: 'Критерии',       Icon: SlidersHorizontal },
    { key: 'rag',       label: 'База разборов',  Icon: Database },
];

const REASON = {
    critical: { tone: 'red',   label: 'Критический', Icon: ShieldAlert },
    lowconf:  { tone: 'amber', label: 'Низкая увер.', Icon: Clock },
    pending:  { tone: 'blue',  label: 'Ждёт API',    Icon: Server },
    new:      { tone: 'slate', label: 'Новый',       Icon: Sparkles },
};

function Segmented({ tab, setTab }) {
    return (
        <div className="inline-flex rounded-2xl bg-slate-100 p-1">
            {TABS.map((t) => {
                const active = tab === t.key;
                return (
                    <button key={t.key} onClick={() => setTab(t.key)}
                        className={`relative flex items-center gap-1.5 rounded-xl px-3.5 py-2 text-[13px] font-semibold transition ${
                            active ? 'text-slate-900' : 'text-slate-500 hover:text-slate-700'}`}>
                        {active && (
                            <motion.span layoutId="qa-tab" className="absolute inset-0 rounded-xl bg-white shadow-sm"
                                         transition={{ type: 'spring', stiffness: 400, damping: 32 }} />
                        )}
                        <t.Icon size={15} className="relative z-10" />
                        <span className="relative z-10">{t.label}</span>
                    </button>
                );
            })}
        </div>
    );
}

function QueueList({ items, onOpen }) {
    return (
        <div className="space-y-2.5">
            {items.map((c) => (
                <button key={c.id} onClick={() => onOpen(c)}
                    className={`${iosCard} flex w-full items-center justify-between gap-3 p-3.5 text-left transition hover:ring-blue-200 active:scale-[0.995]`}>
                    <div className="min-w-0">
                        <div className="flex items-center gap-2">
                            <span className="text-[14px] font-semibold text-slate-900">Звонок #{c.id}</span>
                            <IosBadge tone="slate">{c.direction}</IosBadge>
                        </div>
                        <p className="mt-0.5 text-[12px] text-slate-400">{c.operator} · {c.datetime}</p>
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
                        {c.human_score != null && <IosBadge tone="green">Человек: {c.human_score}</IosBadge>}
                        {(c.reasons || []).map((r) => {
                            const m = REASON[r] || REASON.new;
                            return <IosBadge key={r} tone={m.tone}><m.Icon size={11} />{m.label}</IosBadge>;
                        })}
                    </div>
                </button>
            ))}
        </div>
    );
}

const Spinner = ({ text }) => (
    <div className={`${iosCard} flex flex-col items-center justify-center gap-3 px-6 py-16 text-center`}>
        <Loader2 size={26} className="animate-spin text-blue-500" />
        <p className="text-[13px] text-slate-500">{text}</p>
    </div>
);

const ErrorCard = ({ text, onRetry }) => (
    <div className={`${iosCard} flex flex-col items-center gap-3 px-6 py-14 text-center`}>
        <AlertCircle size={26} className="text-rose-500" />
        <p className="text-[13.5px] font-medium text-slate-700">{text}</p>
        {onRetry && <button onClick={onRetry} className={iosBtnSecondary}>Повторить</button>}
    </div>
);

export default function CallQaView(props) {
    const { apiBaseUrl, withAccessTokenHeader, showToast } = props;
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});

    const [tab, setTab] = useState('queue');
    const [queue, setQueue] = useState(null);      // null = загрузка
    const [queueErr, setQueueErr] = useState(false);

    const [selected, setSelected] = useState(null);
    const [callData, setCallData] = useState(null);
    const [callLoading, setCallLoading] = useState(false);
    const [callErr, setCallErr] = useState(null);

    const loadQueue = () => {
        setQueue(null); setQueueErr(false);
        if (!apiBaseUrl) { setQueueErr(true); setQueue([]); return; }
        axios.get(`${apiBaseUrl}/api/ai-qa/review-queue`, { headers: headers() })
            .then((r) => setQueue(r.data.items || []))
            .catch(() => { setQueue([]); setQueueErr(true); });
    };

    useEffect(() => {
        if (tab === 'queue' && queue === null) loadQueue();
        // eslint-disable-next-line
    }, [tab, apiBaseUrl]);

    const openCall = (c, refresh = false) => {
        setSelected(c); if (!refresh) setCallData(null); setCallErr(null);
        if (!apiBaseUrl) { setCallErr('Бэкенд недоступен'); return; }
        setCallLoading(true);
        axios.get(`${apiBaseUrl}/api/ai-qa/call/${c.id}`, { params: refresh ? { refresh: 1 } : {}, headers: headers() })
            .then((r) => setCallData(r.data.call))
            .catch((e) => setCallErr(e?.response?.data?.error || 'Не удалось загрузить оценку звонка'))
            .finally(() => setCallLoading(false));
    };

    const closeCall = () => { setSelected(null); setCallData(null); setCallErr(null); };

    // ИИ-подсказка формулировки разбора (правило + границы) — человек редактирует и сохраняет сам.
    const refineAdjud = async (c, d) => {
        if (!apiBaseUrl || !callData) return null;
        try {
            const r = await axios.post(`${apiBaseUrl}/api/ai-qa/adjudicate/refine`, {
                direction_id: callData.direction_id, criterion_idx: c.idx, criterion_name: c.name,
                ai_verdict: c.ai, ai_comment: c.comment || '', correct_verdict: d.verdict,
                reason: d.reason || '', excerpt: c.evidence || '',
            }, { headers: headers() });
            return r.data?.proposal || null;
        } catch {
            showToast?.('Не удалось получить подсказку ИИ', 'error');
            return null;
        }
    };

    const saveAdjud = (decisions) => {
        const call = callData;
        const items = call ? (call.criteria || [])
            .filter((c) => c.source === 'transcript' && decisions[c.idx] && decisions[c.idx].verdict !== c.ai)
            .map((c) => ({ criterion_idx: c.idx, criterion_name: c.name, ai_verdict: c.ai,
                           correct_verdict: decisions[c.idx].verdict, reason: decisions[c.idx].reason || '',
                           not_covered: decisions[c.idx].not_covered || null,
                           situation: decisions[c.idx].situation || null,
                           excerpt: c.evidence || '' })) : [];
        if (apiBaseUrl && call && items.length) {
            axios.post(`${apiBaseUrl}/api/ai-qa/adjudicate`,
                       { call_id: call.id, direction_id: call.direction_id, items }, { headers: headers() })
                .then(() => showToast?.('Разбор сохранён', 'success'))
                .catch(() => showToast?.('Не удалось сохранить разбор', 'error'));
        } else {
            showToast?.(items.length ? 'Разбор сохранён' : 'Подтверждено', 'success');
        }
        closeCall();
    };

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-4">
            <div className="flex items-center gap-3">
                <div className="grid h-10 w-10 place-items-center rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-500 text-white shadow-sm">
                    <Sparkles size={20} />
                </div>
                <div>
                    <h1 className="text-[19px] font-semibold text-slate-900">ИИ-оценка звонков</h1>
                    <p className="text-[12.5px] text-slate-400">Автоматическая проверка качества · отдел продаж</p>
                </div>
            </div>

            {!selected && <Segmented tab={tab} setTab={setTab} />}

            {selected ? (
                <div className="space-y-3">
                    <div className="flex items-center justify-between gap-2">
                        <button onClick={closeCall} className={iosBtnGhost}>
                            <ChevronLeft size={16} />Назад
                        </button>
                        {callData && apiBaseUrl && (
                            <div className="flex items-center gap-2">
                                <IosBadge tone={callData._cached ? 'slate' : 'green'}>
                                    {callData._cached ? 'из кэша' : 'оценено сейчас'}
                                </IosBadge>
                                <button onClick={() => openCall(selected, true)} className={iosBtnGhost}>
                                    <RotateCcw size={14} />Переоценить
                                </button>
                            </div>
                        )}
                    </div>
                    {callLoading ? (
                        <Spinner text={`Оцениваю звонок #${selected.id} — распознавание и анализ…`} />
                    ) : callErr ? (
                        <ErrorCard text={callErr} onRetry={() => openCall(selected)} />
                    ) : (
                        <CallReviewCard call={callData || undefined} onSkip={closeCall} onSave={saveAdjud} onRefine={refineAdjud} />
                    )}
                </div>
            ) : tab === 'queue' ? (
                queue === null ? <Spinner text="Загружаю очередь…" />
                    : queueErr ? <ErrorCard text="Не удалось загрузить очередь" onRetry={loadQueue} />
                    : queue.length === 0 ? (
                        <div className={`${iosCard} px-6 py-14 text-center text-[13px] text-slate-400`}>Очередь пуста</div>
                    ) : <QueueList items={queue} onOpen={openCall} />
            ) : tab === 'overview' ? (
                <QaDashboard apiBaseUrl={apiBaseUrl} withAccessTokenHeader={withAccessTokenHeader} />
            ) : tab === 'evals' ? (
                <EvaluationsList apiBaseUrl={apiBaseUrl} withAccessTokenHeader={withAccessTokenHeader}
                                 onOpen={openCall} showToast={showToast} />
            ) : tab === 'criteria' ? (
                <CriteriaClassification showToast={showToast} apiBaseUrl={apiBaseUrl} withAccessTokenHeader={withAccessTokenHeader} />
            ) : (
                <AdjudicationsRag apiBaseUrl={apiBaseUrl} withAccessTokenHeader={withAccessTokenHeader} />
            )}
        </div>
    );
}
