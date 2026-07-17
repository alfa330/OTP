import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { motion } from 'framer-motion';
import {
    Sparkles, ListChecks, ClipboardList, SlidersHorizontal, Database, ChevronLeft,
    ShieldAlert, Gauge, Server, Clock, Loader2, AlertCircle, RotateCcw, Volume1, CheckCircle2,
} from 'lucide-react';
import { APPLE_FONT, iosCard, iosBtnGhost, iosBtnSecondary, IosBadge } from '../ui/ios';
import { isDepartmentHead, normalizeRole } from '../../utils/roles';
import CallReviewCard from './CallReviewCard';
import QaDashboard from './QaDashboard';
import EvaluationsList from './EvaluationsList';
import CriteriaClassification from './CriteriaClassification';
import AdjudicationsRag from './AdjudicationsRag';

/* Контейнер раздела «ИИ-оценка» (App.jsx: view === "ai_qa"; доступ: super_admin,
 * главы ОП/СЗоВ, СВ ОП — последним бэкенд отдаёт только их направления, а вкладки
 * «Критерии»/«База разборов» скрыты). Все данные — реальные с /api/ai-qa/*.
 * Мок-данных нет; при недоступности бэкенда — состояния загрузки / ошибки / пусто. */

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
    asr:      { tone: 'amber', label: 'Слабый звук', Icon: Volume1 },
    ok:       { tone: 'green', label: 'Без флагов',  Icon: CheckCircle2 },
    new:      { tone: 'slate', label: 'Новый',       Icon: Sparkles },
};

function Segmented({ tabs = TABS, tab, setTab }) {
    const refs = useRef([]);
    const move = (event, index) => {
        let next = null;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = tabs.length - 1;
        if (next === null) return;
        event.preventDefault();
        setTab(tabs[next].key);
        refs.current[next]?.focus();
    };
    return (
        <div className="flex max-w-full overflow-x-auto rounded-2xl bg-slate-100 p-1" role="tablist" aria-label="Разделы ИИ-оценки">
            {tabs.map((t, index) => {
                const active = tab === t.key;
                return (
                    <button key={t.key} ref={(node) => { refs.current[index] = node; }} type="button" role="tab"
                        id={`qa-tab-${t.key}`} aria-controls={`qa-panel-${t.key}`}
                        aria-selected={active} tabIndex={active ? 0 : -1} onKeyDown={(event) => move(event, index)}
                        onClick={() => setTab(t.key)}
                        className={`relative flex shrink-0 items-center gap-1.5 rounded-xl px-3.5 py-2 text-[13px] font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
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
                <button key={c.id} type="button" onClick={() => onOpen(c)}
                    className={`${iosCard} flex w-full flex-col items-stretch justify-between gap-2.5 p-3.5 text-left transition hover:ring-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 active:scale-[0.995] sm:flex-row sm:items-center`}>
                    <div className="min-w-0">
                        <div className="flex items-center gap-2">
                            <span className="text-[14px] font-semibold text-slate-900">Звонок #{c.id}</span>
                            <IosBadge tone="slate">{c.direction}</IosBadge>
                        </div>
                        <p className="mt-0.5 text-[12px] text-slate-400">{c.operator} · {c.datetime}</p>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5 sm:shrink-0 sm:justify-end">
                        {c.human_score != null && <IosBadge tone="green">Человек: {c.human_score}</IosBadge>}
                        {(c.reasons || []).map((r) => {
                            const m = REASON[r] || REASON.new;
                            return <IosBadge key={r} tone={m.tone}><m.Icon size={11} />{m.label}</IosBadge>;
                        })}
                        {c.stale && (
                            <IosBadge tone="amber" title="Конфигурация ИИ (промпт, критерии или база знаний) изменилась после этой оценки — при открытии звонок будет переоценён автоматически.">
                                <RotateCcw size={11} />Оценка устарела
                            </IosBadge>
                        )}
                    </div>
                </button>
            ))}
        </div>
    );
}

const Spinner = ({ text }) => (
    <div className={`${iosCard} flex flex-col items-center justify-center gap-3 px-6 py-16 text-center`} role="status" aria-live="polite">
        <Loader2 size={26} className="animate-spin text-blue-500" aria-hidden="true" />
        <p className="text-[13px] text-slate-500">{text}</p>
    </div>
);

const ErrorCard = ({ text, onRetry }) => (
    <div className={`${iosCard} flex flex-col items-center gap-3 px-6 py-14 text-center`} role="alert">
        <AlertCircle size={26} className="text-rose-500" aria-hidden="true" />
        <p className="text-[13.5px] font-medium text-slate-700">{text}</p>
        {onRetry && <button type="button" onClick={onRetry} className={iosBtnSecondary}>Повторить</button>}
    </div>
);

export default function CallQaView(props) {
    const { apiBaseUrl, withAccessTokenHeader, showToast, user } = props;
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    // Правка/удаление разборов — только супер-админ (бэкенд проверяет то же в _ai_qa_admin_guard).
    const canManageRag = normalizeRole(user?.role) === 'super_admin';
    // СВ ОП оценивает только свои направления: конфигурация критериев и база разборов
    // ему не показываются (бэкенд их тоже ограничивает/запрещает).
    const isScopedSupervisor = normalizeRole(user?.role) === 'sv' && !isDepartmentHead(user);
    const visibleTabs = isScopedSupervisor
        ? TABS.filter((t) => t.key !== 'criteria' && t.key !== 'rag')
        : TABS;

    const [tab, setTab] = useState('queue');
    const [sectionInteraction, setSectionInteraction] = useState({ editing: false, busy: false });
    const [reviewInteraction, setReviewInteraction] = useState({ dirty: false, busy: false });
    const [queue, setQueue] = useState(null);      // null = загрузка
    const [queueErr, setQueueErr] = useState(false);

    const [selected, setSelected] = useState(null);
    const [callData, setCallData] = useState(null);
    const [callLoading, setCallLoading] = useState(false);
    const [callErr, setCallErr] = useState(null);
    const callRequest = useRef({ id: 0, controller: null });
    const returnFocus = useRef(null);

    const changeTab = (nextTab) => {
        if (nextTab === tab) return;
        if (sectionInteraction.busy) {
            showToast?.('Дождитесь завершения сохранения', 'error');
            return;
        }
        if (sectionInteraction.editing &&
            !window.confirm('Перейти в другой раздел? Несохранённые изменения будут потеряны.')) return;
        setSectionInteraction({ editing: false, busy: false });
        setTab(nextTab);
    };

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
        if (!selected && typeof document !== 'undefined') returnFocus.current = document.activeElement;
        callRequest.current.controller?.abort();
        const controller = new AbortController();
        const requestId = callRequest.current.id + 1;
        callRequest.current = { id: requestId, controller };
        setSelected(c);
        if (!refresh) {
            setCallData(null);
            setReviewInteraction({ dirty: false, busy: false });
        }
        setCallErr(null);
        if (!apiBaseUrl) { setCallErr('Бэкенд недоступен'); setCallLoading(false); return; }
        setCallLoading(true);
        axios.get(`${apiBaseUrl}/api/ai-qa/call/${c.id}`, {
            params: refresh ? { refresh: 1 } : {}, headers: headers(), signal: controller.signal,
        })
            .then((r) => {
                if (requestId === callRequest.current.id) setCallData(r.data.call);
            })
            .catch((e) => {
                if (!axios.isCancel(e) && requestId === callRequest.current.id) {
                    setCallErr(e?.response?.data?.error || 'Не удалось загрузить оценку звонка');
                }
            })
            .finally(() => {
                if (requestId === callRequest.current.id) setCallLoading(false);
            });
    };

    const resetCall = () => {
        const focusTarget = returnFocus.current;
        callRequest.current.controller?.abort();
        callRequest.current = { id: callRequest.current.id + 1, controller: null };
        setSelected(null); setCallData(null); setCallErr(null); setCallLoading(false);
        setReviewInteraction({ dirty: false, busy: false });
        returnFocus.current = null;
        window.setTimeout(() => {
            const target = focusTarget?.isConnected ? focusTarget : document.getElementById(`qa-tab-${tab}`);
            target?.focus?.();
        }, 0);
    };

    const requestCloseCall = () => {
        if (reviewInteraction.busy) {
            showToast?.('Дождитесь завершения сохранения', 'error');
            return;
        }
        if (reviewInteraction.dirty &&
            !window.confirm('Закрыть карточку? Несохранённые исправления будут потеряны.')) return;
        resetCall();
    };

    const requestReevaluation = () => {
        if (!selected || callLoading || reviewInteraction.busy) return;
        if (reviewInteraction.dirty &&
            !window.confirm('Переоценить звонок? Несохранённые исправления будут потеряны.')) return;
        setReviewInteraction({ dirty: false, busy: false });
        openCall(selected, true);
    };

    useEffect(() => () => callRequest.current.controller?.abort(), []);

    // ИИ-подсказка формулировки разбора (правило + границы) — человек редактирует и сохраняет сам.
    const refineAdjud = async (c, d) => {
        if (!apiBaseUrl || !callData) return null;
        try {
            const r = await axios.post(`${apiBaseUrl}/api/ai-qa/adjudicate/refine`, {
                direction_id: callData.direction_id, criterion_idx: c.idx, criterion_name: c.name,
                ai_verdict: c.ai, ai_comment: c.comment || '', correct_verdict: d.verdict,
                reason: d.reason || '',
                excerpt: d.excerpt || '',
                excerpt_verified: d.excerpt_verified === true,
                evidence_status: d.evidence_status || null,
            }, { headers: headers() });
            return r.data?.proposal || null;
        } catch {
            showToast?.('Не удалось получить подсказку ИИ', 'error');
            return null;
        }
    };

    // Отправляется ВСЕГДА (даже без исправлений): «Подтвердить» — тоже результат ревью,
    // он убирает звонок из очереди и остаётся сигналом качества модели. Карточка
    // закрывается только после успешного ответа — при сбое введённый разбор не теряется.
    const saveAdjud = async (decisions) => {
        const call = callData;
        if (!call) { resetCall(); return false; }
        const items = (call.criteria || [])
            .filter((c) => c.source === 'transcript' && decisions[c.idx] && decisions[c.idx].verdict !== c.ai)
            .map((c) => ({ criterion_id: c.criterion_id, criterion_idx: c.idx,
                           criterion_name: c.name, ai_verdict: c.ai,
                           correct_verdict: decisions[c.idx].verdict, reason: decisions[c.idx].reason || '',
                           not_covered: decisions[c.idx].not_covered || null,
                           situation: decisions[c.idx].situation || null,
                           excerpt: decisions[c.idx].excerpt || '',
                           excerpt_verified: decisions[c.idx].excerpt_verified === true,
                           evidence_status: decisions[c.idx].evidence_status || null }));
        if (!apiBaseUrl) {
            showToast?.('Бэкенд недоступен — разбор не сохранён', 'error');
            return false;
        }
        try {
            await axios.post(`${apiBaseUrl}/api/ai-qa/adjudicate`,
                { call_id: call.id, direction_id: call.direction_id,
                  evaluation_run_id: call._evaluation_run_id,
                  scale_revision_id: call._scale_revision_id,
                  evaluation_fingerprint: call._evaluation_fingerprint,
                  items }, { headers: headers() });
            showToast?.(items.length ? 'Разбор сохранён как черновик' : 'Подтверждено', 'success');
            setQueue((current) => (Array.isArray(current) ? current.filter((item) => item.id !== call.id) : current));
            resetCall();
            return true;
        } catch (error) {
            showToast?.(error?.response?.data?.error || 'Не удалось сохранить — карточка оставлена открытой', 'error');
            return false;
        }
    };

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-4">
            <div className="flex items-center gap-3">
                <div className="grid h-10 w-10 place-items-center rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-500 text-white shadow-sm">
                    <Sparkles size={20} />
                </div>
                <div>
                    <h1 className="text-[19px] font-semibold text-slate-900">ИИ-оценка звонков</h1>
                    <p className="text-[12.5px] text-slate-400">
                        {isScopedSupervisor
                            ? 'Автоматическая проверка качества · ваши направления'
                            : 'Автоматическая проверка качества · отдел продаж'}
                    </p>
                </div>
            </div>

            {!selected && <Segmented tabs={visibleTabs} tab={tab} setTab={changeTab} />}

            {selected ? (
                <div className="space-y-3">
                    <div className="flex items-center justify-between gap-2">
                        <button type="button" onClick={requestCloseCall} disabled={reviewInteraction.busy}
                            className={`${iosBtnGhost} disabled:cursor-not-allowed disabled:opacity-50`}>
                            <ChevronLeft size={16} />Назад
                        </button>
                        {callData && apiBaseUrl && (
                            <div className="flex flex-wrap items-center justify-end gap-2">
                                {!callData._cached && callData._previous_evaluation_stale && (
                                    <IosBadge tone="amber" title="Прежняя оценка сделана в устаревшей конфигурации ИИ (промпт, критерии или база знаний изменились), поэтому звонок переоценён заново.">
                                        прежняя оценка устарела — переоценено
                                    </IosBadge>
                                )}
                                <IosBadge tone={callData._cached ? 'slate' : 'green'}>
                                    {callData._cached ? 'из кэша' : 'оценено сейчас'}
                                </IosBadge>
                                <button type="button" onClick={requestReevaluation} disabled={callLoading || reviewInteraction.busy}
                                    className={`${iosBtnGhost} disabled:cursor-not-allowed disabled:opacity-50`}>
                                    {callLoading ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
                                    {callLoading ? 'Переоцениваю…' : 'Переоценить'}
                                </button>
                            </div>
                        )}
                    </div>
                    {callLoading ? (
                        <Spinner text={`Оцениваю звонок #${selected.id} — распознавание и анализ…`} />
                    ) : callErr ? (
                        <ErrorCard text={callErr} onRetry={() => openCall(selected)} />
                    ) : (
                        <CallReviewCard key={callData?._evaluation_run_id || callData?.id} call={callData || undefined} onSkip={requestCloseCall}
                                        onSave={saveAdjud} onRefine={refineAdjud}
                                        onInteractionChange={setReviewInteraction} />
                    )}
                </div>
            ) : (
                <div role="tabpanel" id={`qa-panel-${tab}`} aria-labelledby={`qa-tab-${tab}`}>
                {tab === 'queue' ? (
                queue === null ? <Spinner text="Загружаю очередь…" />
                    : queueErr ? <ErrorCard text="Не удалось загрузить очередь" onRetry={loadQueue} />
                    : queue.length === 0 ? (
                        <div className={`${iosCard} flex flex-col items-center gap-3 px-6 py-14 text-center`}>
                            <CheckCircle2 size={26} className="text-emerald-500" />
                            <div>
                                <p className="text-[14px] font-semibold text-slate-700">Все звонки проверены</p>
                                <p className="mt-1 text-[12.5px] text-slate-500">В очереди сейчас нет новых карточек для ревью.</p>
                            </div>
                            <button type="button" onClick={loadQueue} className={iosBtnSecondary}>Обновить очередь</button>
                        </div>
                    ) : (
                        <div className="space-y-2.5">
                            <QueueList items={queue} onOpen={openCall} />
                            {queue.some((c) => c.stale) && (
                                <p className="px-1 text-[11.5px] text-slate-500">
                                    «Оценка устарела» — после оценки изменилась конфигурация ИИ (промпт, критерии или база знаний);
                                    при открытии такой звонок будет переоценён автоматически.
                                </p>
                            )}
                            {queue.length >= 30 && (
                                <p className="px-1 text-[11.5px] text-slate-500">Показаны первые 30 звонков по приоритету ревью.</p>
                            )}
                        </div>
                    )
            ) : tab === 'overview' ? (
                <QaDashboard apiBaseUrl={apiBaseUrl} withAccessTokenHeader={withAccessTokenHeader} />
            ) : tab === 'evals' ? (
                <EvaluationsList apiBaseUrl={apiBaseUrl} withAccessTokenHeader={withAccessTokenHeader}
                                 onOpen={openCall} showToast={showToast} />
            ) : tab === 'criteria' ? (
                <CriteriaClassification showToast={showToast} apiBaseUrl={apiBaseUrl}
                                        withAccessTokenHeader={withAccessTokenHeader} directions={props.directions}
                                        onInteractionChange={setSectionInteraction} />
            ) : (
                <AdjudicationsRag apiBaseUrl={apiBaseUrl} withAccessTokenHeader={withAccessTokenHeader}
                                   showToast={showToast} canManage={canManageRag}
                                   onInteractionChange={setSectionInteraction} />
            )}
                </div>
            )}
        </div>
    );
}
