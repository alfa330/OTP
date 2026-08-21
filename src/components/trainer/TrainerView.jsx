import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    AlertTriangle, BookOpen, ChevronLeft, Clock, Gauge, Loader2, Mic, MicOff,
    RefreshCw, Sparkles, Square, Wallet,
} from 'lucide-react';
import {
    APPLE_FONT, IosBadge, IosSection, IosSegmented, iosBtnGhost, iosBtnPrimary,
    iosBtnSecondary, iosCard, iosGroupLabel, scoreTone,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { VoiceLink } from './voice';
import {
    fmtCost, fmtDuration, fmtLangs, fmtMs, paceTone, roleLabel, roleSide,
    statusLabel, summarize,
} from './trainerFormat';

/* Раздел «Тренажёр» — голосовой разговор с ИИ и разбор после него.
 *
 * Два режима одной механики, переключаются сегментом в шапке:
 *   «Водитель»  — ИИ играет водителя, вы отвечаете как оператор, в конце разбор;
 *   «Наставник» — наоборот: вы спрашиваете, ИИ отвечает по базе знаний вики.
 *
 * Раздел тестовый и существует ради ЗАМЕРОВ, поэтому цифры не спрятаны в отчёт:
 * пауза до ответа видна прямо во время разговора, а в журнале лежит разбор
 * каждой реплики по звеньям. Кто тормозит — распознавание, модель или озвучка —
 * должно быть видно сразу, без выгрузок.
 *
 * Про цвет. Красим только то, что требует внимания: долгую паузу, ошибку,
 * низкий балл. Нормальные значения остаются нейтральными, иначе экран
 * превращается в светофор, по которому ничего не читается.
 */

const PACE_CLASS = {
    good: 'text-emerald-600',
    warn: 'text-amber-600',
    bad: 'text-rose-600',
    muted: 'text-slate-400',
};

// scoreTone из общих примитивов отдаёт ИМЯ ТОНА бейджа, а не класс текста:
// баллом мы красим крупную цифру, поэтому переводим тон в цвет здесь, чтобы
// пороги «зелёный/жёлтый/красный» остались общими с очередью и карточкой ревью.
const SCORE_CLASS = {
    green: 'text-emerald-600',
    amber: 'text-amber-600',
    red: 'text-rose-600',
    slate: 'text-slate-400',
};
const scoreClass = (value) => SCORE_CLASS[scoreTone(value)] || SCORE_CLASS.slate;

const MODE_OPTIONS = [
    { value: 'driver', label: 'Водитель' },
    { value: 'mentor', label: 'Наставник' },
];

const TAB_OPTIONS = [
    { value: 'talk', label: 'Разговор' },
    { value: 'log', label: 'Журнал' },
];

/** Плитка с одной цифрой. Подпись сверху, значение крупно — как в «Здоровье». */
const Stat = ({ label, value, tone = 'text-slate-900', hint = null }) => (
    <div className="flex flex-col gap-0.5">
        <span className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">{label}</span>
        <span className={`text-[19px] font-semibold tabular-nums leading-tight ${tone}`}>{value}</span>
        {hint && <span className="text-[11px] text-slate-400">{hint}</span>}
    </div>
);

/** Реплика разговора. Человек справа, ИИ слева — как в переписке. */
const Bubble = ({ turn }) => {
    const side = roleSide(turn.role);
    const human = side === 'human';
    return (
        <div className={`flex ${human ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[78%] space-y-1 ${human ? 'items-end' : 'items-start'} flex flex-col`}>
                <span className="px-1 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">
                    {roleLabel(turn.role)}
                </span>
                <div className={`rounded-2xl px-3.5 py-2.5 text-[14px] leading-relaxed ${
                    human ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-900'
                }`}>
                    {turn.text}
                </div>
                {!!(turn.sources || []).length && (
                    <div className="flex flex-wrap gap-1.5 px-1 pt-0.5">
                        {turn.sources.map((source, index) => (
                            <span key={`${source.article_id}-${index}`}
                                  className="inline-flex items-center gap-1 rounded-lg bg-slate-100 px-2 py-1 text-[11px] text-slate-600">
                                <BookOpen className="h-3 w-3 text-slate-400" />
                                {source.title || 'статья'}
                            </span>
                        ))}
                    </div>
                )}
                {turn.pace_ms != null && (
                    <span className={`px-1 text-[11px] tabular-nums ${PACE_CLASS[paceTone(turn.pace_ms)]}`}>
                        пауза {fmtMs(turn.pace_ms)}
                    </span>
                )}
            </div>
        </div>
    );
};

const TrainerView = ({ apiBaseUrl, withAccessTokenHeader, showToast, user }) => {
    const [tab, setTab] = useState('talk');
    const [mode, setMode] = useState('driver');
    const [scenarios, setScenarios] = useState([]);
    const [scenario, setScenario] = useState('');
    const [health, setHealth] = useState(null);

    const [session, setSession] = useState(null);
    const [turns, setTurns] = useState([]);
    const [live, setLive] = useState('');
    const [phase, setPhase] = useState('idle');   // idle | starting | listening | thinking | speaking
    const [review, setReview] = useState(null);
    const [cost, setCost] = useState(null);
    const [problems, setProblems] = useState([]);

    const [sessions, setSessions] = useState([]);
    const [detail, setDetail] = useState(null);
    const [loadingLog, setLoadingLog] = useState(false);

    const linkRef = useRef(null);
    const sessionRef = useRef(null);
    const busyRef = useRef(false);
    // showToast пересоздаётся на каждом рендере App — в зависимостях эффекта он
    // заставлял бы раздел перезагружаться от любого чиха. Держим в ref.
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    const headers = useCallback(() => withAccessTokenHeader({}), [withAccessTokenHeader]);

    const api = useCallback(async (path, options = {}) => {
        const response = await fetch(`${apiBaseUrl}/api/trainer${path}`, {
            ...options,
            headers: { ...headers(), 'Content-Type': 'application/json', ...(options.headers || {}) },
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || data.error || `HTTP ${response.status}`);
        return data;
    }, [apiBaseUrl, headers]);

    // ── загрузка справочников ────────────────────────────────────────────────

    useEffect(() => {
        let alive = true;
        Promise.all([api('/ping'), api('/scenarios')])
            .then(([ping, list]) => {
                if (!alive) return;
                setHealth(ping);
                setScenarios(list.scenarios || []);
                setScenario((current) => current || list.scenarios?.[0]?.key || '');
            })
            .catch((error) => { if (alive) setProblems([error.message]); });
        return () => { alive = false; };
    }, [api]);

    const loadLog = useCallback(() => {
        setLoadingLog(true);
        api('/sessions')
            .then((data) => setSessions(data.sessions || []))
            .catch((error) => toastRef.current?.(`Журнал не загрузился: ${error.message}`, 'error'))
            .finally(() => setLoadingLog(false));
    }, [api]);

    useEffect(() => { if (tab === 'log') loadLog(); }, [tab, loadLog]);

    // ── разговор ─────────────────────────────────────────────────────────────

    const pushTurn = useCallback((turn) => setTurns((prev) => [...prev, turn]), []);

    const speak = useCallback(async (text, turnId, since) => {
        setPhase('speaking');
        try {
            const result = await linkRef.current.speak(text, { turnId, since });
            if (result?.voice_to_voice_ms && turnId) {
                setTurns((prev) => prev.map((t) => (t.id === turnId
                    ? { ...t, pace_ms: result.voice_to_voice_ms } : t)));
                api(`/turns/${turnId}`, {
                    method: 'PATCH',
                    body: JSON.stringify({ voice_to_voice_ms: result.voice_to_voice_ms }),
                }).catch(() => {});
            }
        } catch (error) {
            toastRef.current?.(`Озвучка: ${error.message}`, 'error');
        } finally {
            setPhase('listening');
        }
    }, [api]);

    const handleUtterance = useCallback(async ({ text, metrics, at }) => {
        if (busyRef.current || !sessionRef.current) return;
        busyRef.current = true;
        setLive('');
        setPhase('thinking');
        pushTurn({ id: `human-${Date.now()}`, role: mode === 'driver' ? 'trainee' : 'asker', text });
        try {
            const answer = await api(`/sessions/${sessionRef.current}/turn`, {
                method: 'POST', body: JSON.stringify({ text, ...metrics }),
            });
            pushTurn({
                id: answer.turn_id, role: answer.role, text: answer.text,
                sources: answer.sources, pace_ms: null,
            });
            await speak(answer.text, answer.turn_id, at);
        } catch (error) {
            toastRef.current?.(`Собеседник не ответил: ${error.message}`, 'error');
            setPhase('listening');
        } finally {
            busyRef.current = false;
        }
    }, [api, mode, pushTurn, speak]);

    const handleUtteranceRef = useRef(handleUtterance);
    useEffect(() => { handleUtteranceRef.current = handleUtterance; }, [handleUtterance]);

    const start = useCallback(async () => {
        setPhase('starting');
        setTurns([]);
        setReview(null);
        setCost(null);
        setProblems([]);
        try {
            const created = await api('/sessions', {
                method: 'POST',
                body: JSON.stringify({
                    mode,
                    scenario: mode === 'driver' ? scenario : null,
                    client: {
                        ua: navigator.userAgent,
                        screen: `${window.screen?.width}x${window.screen?.height}`,
                    },
                }),
            });
            setSession(created);
            sessionRef.current = created.session_id;

            const link = new VoiceLink({
                apiBaseUrl,
                headers,
                onEvent: (type, payload) => {
                    if (type === 'live') setLive(payload.text);
                    else if (type === 'utterance') handleUtteranceRef.current(payload);
                    else if (type === 'barge') {
                        api(`/sessions/${sessionRef.current}/event`, {
                            method: 'POST',
                            body: JSON.stringify({ level: 'info', code: 'barge_in' }),
                        }).catch(() => {});
                    } else if (type === 'error') {
                        setProblems((prev) => [...prev, `${payload.where}: ${payload.message}`]);
                    }
                },
            });
            linkRef.current = link;
            await link.start();
            setPhase('listening');

            if (created.opening) {
                pushTurn({ id: `open-${Date.now()}`, role: 'driver', text: created.opening });
                await speak(created.opening, null, 0);
            }
        } catch (error) {
            setPhase('idle');
            setProblems((prev) => [...prev, error.message]);
            toastRef.current?.(`Не удалось начать: ${error.message}`, 'error');
        }
    }, [api, apiBaseUrl, headers, mode, pushTurn, scenario, speak]);

    const finish = useCallback(async () => {
        const id = sessionRef.current;
        linkRef.current?.stop();
        linkRef.current = null;
        setPhase('idle');
        setLive('');
        if (!id) return;
        try {
            const result = await api(`/sessions/${id}/finish`, { method: 'POST' });
            setReview(result.review);
            setCost(result.cost);
            if (result.review_error) {
                setProblems((prev) => [...prev, `разбор: ${result.review_error}`]);
            }
        } catch (error) {
            toastRef.current?.(`Не удалось закрыть разговор: ${error.message}`, 'error');
        } finally {
            sessionRef.current = null;
            setSession(null);
        }
    }, [api]);

    // Микрофон и сокет обязаны закрыться при уходе с раздела: иначе вкладка
    // продолжает слушать, а Soniox — тарифицировать.
    useEffect(() => () => { linkRef.current?.stop(); }, []);

    const summary = useMemo(() => summarize(turns), [turns]);
    const running = phase !== 'idle' && phase !== 'starting';

    // ── разметка ─────────────────────────────────────────────────────────────

    const scenarioOptions = useMemo(() => scenarios.map((item) => ({
        value: item.key,
        label: `${item.title} · ${item.difficulty}/10 · ${item.lang}`,
    })), [scenarios]);

    const keysMissing = health && Object.entries(health.keys || {})
        .filter(([, present]) => !present).map(([name]) => name);

    return (
        <div className="mx-auto w-full max-w-5xl space-y-5" style={{ fontFamily: APPLE_FONT }}>
            <header className="flex flex-wrap items-end justify-between gap-3">
                <div className="space-y-1">
                    <h1 className="text-[26px] font-semibold tracking-tight text-slate-900">Тренажёр</h1>
                    <p className="text-[13.5px] text-slate-500">
                        Голосовой разговор с ИИ и разбор после него. Раздел тестовый, доступен только супер-админу.
                    </p>
                </div>
                <IosSegmented value={tab} options={TAB_OPTIONS} onChange={setTab} ariaLabel="Раздел тренажёра" />
            </header>

            {!!keysMissing?.length && (
                <div className="flex items-start gap-2.5 rounded-2xl bg-amber-50 px-4 py-3 text-[13px] text-amber-900 ring-1 ring-amber-200">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
                    <span>Нет ключей: {keysMissing.join(', ')}. Раздел будет отказывать на этих звеньях.</span>
                </div>
            )}

            {tab === 'talk' ? (
                <div className="space-y-4">
                    <IosSection
                        title="Собеседник"
                        right={<IosSegmented value={mode} options={MODE_OPTIONS} onChange={setMode}
                                             size="sm" ariaLabel="Режим" />}
                        hint={mode === 'driver'
                            ? 'ИИ играет водителя и намеренно не помогает: детали называет, только если о них прямо спросить.'
                            : 'ИИ отвечает как опытный оператор и опирается на базу знаний вики — на те статьи, которые вам разрешено читать.'}
                    >
                        <div className="flex flex-wrap items-center gap-3">
                            {mode === 'driver' && (
                                <div className="min-w-[280px] flex-1">
                                    <CustomSelect
                                        value={scenario}
                                        onChange={setScenario}
                                        options={scenarioOptions}
                                        disabled={running}
                                        placeholder="Выберите сценарий"
                                    />
                                </div>
                            )}
                            {!running ? (
                                <button className={iosBtnPrimary} onClick={start}
                                        disabled={phase === 'starting' || (mode === 'driver' && !scenario)}>
                                    {phase === 'starting'
                                        ? <><Loader2 className="h-4 w-4 animate-spin" /> Подключаем микрофон…</>
                                        : <><Mic className="h-4 w-4" /> Начать разговор</>}
                                </button>
                            ) : (
                                <button className={iosBtnSecondary} onClick={finish}>
                                    <Square className="h-4 w-4" /> Завершить и разобрать
                                </button>
                            )}
                            {running && (
                                <span className="inline-flex items-center gap-2 text-[13px] text-slate-500">
                                    <span className={`h-2 w-2 rounded-full ${
                                        phase === 'speaking' ? 'bg-amber-500' : 'bg-rose-500'} animate-pulse`} />
                                    {phase === 'speaking' ? 'собеседник говорит'
                                        : phase === 'thinking' ? 'думает…' : 'слушаю вас'}
                                </span>
                            )}
                        </div>
                    </IosSection>

                    {(running || turns.length > 0) && (
                        <div className={`${iosCard} overflow-hidden`}>
                            <div className="flex flex-wrap gap-x-8 gap-y-3 border-b border-slate-100 px-4 py-3">
                                <Stat label="Пауза до ответа" value={fmtMs(summary.pace)}
                                      tone={PACE_CLASS[paceTone(summary.pace)]}
                                      hint={summary.paceWorst ? `худшая ${fmtMs(summary.paceWorst)}` : null} />
                                <Stat label="Модель" value={fmtMs(summary.llm)} />
                                <Stat label="Озвучка" value={fmtMs(summary.tts)} />
                                <Stat label="Реплик" value={summary.turns} />
                                {summary.barge > 0 && <Stat label="Перебиваний" value={summary.barge} />}
                            </div>
                            <div className="max-h-[46vh] space-y-3 overflow-y-auto px-4 py-4">
                                {turns.map((turn) => <Bubble key={turn.id} turn={turn} />)}
                                {live && (
                                    <div className="flex justify-end">
                                        <div className="max-w-[78%] rounded-2xl bg-blue-50 px-3.5 py-2.5 text-[14px] text-blue-900/70">
                                            {live}…
                                        </div>
                                    </div>
                                )}
                                {!turns.length && !live && (
                                    <p className="py-8 text-center text-[13px] text-slate-400">
                                        Нажмите «Начать разговор» и говорите вслух.
                                    </p>
                                )}
                            </div>
                        </div>
                    )}

                    {!!problems.length && (
                        <IosSection title="Что пошло не так">
                            <ul className="space-y-1.5">
                                {problems.map((problem, index) => (
                                    <li key={index} className="flex items-start gap-2 text-[13px] text-rose-600">
                                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                        <span>{problem}</span>
                                    </li>
                                ))}
                            </ul>
                        </IosSection>
                    )}

                    {review && <ReviewCard review={review} cost={cost} />}
                </div>
            ) : (
                <LogTab
                    sessions={sessions}
                    loading={loadingLog}
                    detail={detail}
                    onOpen={(id) => api(`/sessions/${id}`).then(setDetail)
                        .catch((error) => toastRef.current?.(error.message, 'error'))}
                    onBack={() => setDetail(null)}
                    onReload={loadLog}
                />
            )}
        </div>
    );
};

/** Разбор после разговора: балл, критерии, что пропущено. */
const ReviewCard = ({ review, cost }) => (
    <IosSection title="Разбор" right={cost ? (
        <span className="inline-flex items-center gap-1.5 text-[12px] text-slate-500">
            <Wallet className="h-3.5 w-3.5" /> {fmtCost(cost.total)}
        </span>
    ) : null}>
        <div className="flex items-baseline gap-2">
            <span className={`text-[34px] font-semibold tabular-nums ${scoreClass(review.score)}`}>
                {review.score}
            </span>
            <span className="text-[13px] text-slate-400">/ 100</span>
        </div>
        <div className="space-y-2">
            {(review.criteria || []).map((item, index) => (
                <div key={index} className="flex items-start justify-between gap-4 border-t border-slate-100 pt-2">
                    <div className="space-y-0.5">
                        <div className="text-[13.5px] font-medium text-slate-800">{item.name}</div>
                        <div className="text-[12.5px] text-slate-500">{item.comment}</div>
                    </div>
                    <span className="shrink-0 text-[13.5px] font-semibold tabular-nums text-slate-700">
                        {item.points}/{item.max}
                    </span>
                </div>
            ))}
        </div>
        {!!(review.critical || []).length && (
            <div className="space-y-1 rounded-xl bg-rose-50 px-3 py-2.5">
                <div className={iosGroupLabel}>Критические ошибки</div>
                {review.critical.map((item, index) => (
                    <div key={index} className="text-[13px] text-rose-700">{item}</div>
                ))}
            </div>
        )}
        {review.recommendation && (
            <p className="text-[13.5px] leading-relaxed text-slate-700">{review.recommendation}</p>
        )}
    </IosSection>
);

/** Журнал прогонов и разбор одного разговора по звеньям. */
const LogTab = ({ sessions, loading, detail, onOpen, onBack, onReload }) => {
    if (detail) {
        const { session, turns, events } = detail;
        return (
            <div className="space-y-4">
                <button className={iosBtnGhost} onClick={onBack}>
                    <ChevronLeft className="h-4 w-4" /> К журналу
                </button>
                <IosSection title={session.title || 'Разговор'}>
                    <div className="flex flex-wrap gap-x-8 gap-y-3">
                        <Stat label="Пауза, медиана" value={fmtMs(session.pace_p50)}
                              tone={PACE_CLASS[paceTone(session.pace_p50)]} />
                        <Stat label="Худшая" value={fmtMs(session.pace_max)} />
                        <Stat label="Длительность" value={fmtDuration(session.duration_ms)} />
                        <Stat label="Реплик" value={session.turns} />
                        <Stat label="Стоимость" value={fmtCost(session.cost_usd)} />
                        {session.score != null && (
                            <Stat label="Балл" value={`${session.score}/100`} tone={scoreClass(session.score)} />
                        )}
                    </div>
                    <div className="text-[12px] text-slate-400">
                        {session.provider} · {session.model} · озвучка {session.tts_model}
                    </div>
                </IosSection>

                <IosSection title="Реплики и замеры">
                    <div className="overflow-x-auto">
                        <table className="w-full min-w-[720px] text-[12.5px]">
                            <thead>
                                <tr className="text-left text-[10.5px] uppercase tracking-wider text-slate-400">
                                    <th className="py-2 pr-3 font-semibold">Кто</th>
                                    <th className="py-2 pr-3 font-semibold">Реплика</th>
                                    <th className="py-2 pr-3 font-semibold">Распозн.</th>
                                    <th className="py-2 pr-3 font-semibold">Модель</th>
                                    <th className="py-2 pr-3 font-semibold">Озвучка</th>
                                    <th className="py-2 font-semibold">Пауза</th>
                                </tr>
                            </thead>
                            <tbody>
                                {turns.map((turn) => (
                                    <tr key={turn.id} className="border-t border-slate-100 align-top">
                                        <td className="py-2 pr-3 text-slate-500">{roleLabel(turn.role)}</td>
                                        <td className="py-2 pr-3 text-slate-800">
                                            <div className="max-w-[280px]">{turn.text}</div>
                                            {fmtLangs(turn.stt?.langs) && (
                                                <div className="text-[11px] text-slate-400">{fmtLangs(turn.stt.langs)}</div>
                                            )}
                                        </td>
                                        <td className="py-2 pr-3 tabular-nums text-slate-500">
                                            {turn.stt?.confidence != null
                                                ? `${Math.round(turn.stt.confidence * 100)}%` : '—'}
                                            {turn.stt?.endpoint_ms != null && (
                                                <div className="text-[11px] text-slate-400">
                                                    выдержка {fmtMs(turn.stt.endpoint_ms)}
                                                </div>
                                            )}
                                        </td>
                                        <td className="py-2 pr-3 tabular-nums text-slate-500">
                                            {fmtMs(turn.llm?.total_ms)}
                                            {turn.llm?.out != null && (
                                                <div className="text-[11px] text-slate-400">
                                                    {turn.llm.in ?? '—'}/{turn.llm.out} ток.
                                                </div>
                                            )}
                                        </td>
                                        <td className="py-2 pr-3 tabular-nums text-slate-500">
                                            {fmtMs(turn.tts?.ttfb_ms)}
                                            {turn.tts?.audio_ms != null && (
                                                <div className="text-[11px] text-slate-400">
                                                    речи {fmtMs(turn.tts.audio_ms)}
                                                </div>
                                            )}
                                        </td>
                                        <td className={`py-2 tabular-nums ${PACE_CLASS[paceTone(turn.pace_ms)]}`}>
                                            {fmtMs(turn.pace_ms)}
                                            {turn.barge_in && (
                                                <div className="text-[11px] text-slate-400">перебит</div>
                                            )}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </IosSection>

                {!!events.length && (
                    <IosSection title="События">
                        <div className="space-y-1.5">
                            {events.map((event, index) => (
                                <div key={index} className="flex items-start gap-2 text-[12.5px]">
                                    <IosBadge tone={event.level === 'error' ? 'red'
                                        : event.level === 'warn' ? 'amber' : 'slate'}>
                                        {event.code}
                                    </IosBadge>
                                    <span className="text-slate-600">{event.message}</span>
                                </div>
                            ))}
                        </div>
                    </IosSection>
                )}

                {session.review && <ReviewCard review={session.review} cost={session.cost_breakdown} />}
            </div>
        );
    }

    return (
        <IosSection
            title="Прогоны"
            right={<button className={iosBtnGhost} onClick={onReload} disabled={loading}>
                <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} /> Обновить
            </button>}
        >
            {loading && !sessions.length ? (
                <p className="py-6 text-center text-[13px] text-slate-400">Загружаем…</p>
            ) : !sessions.length ? (
                <p className="py-6 text-center text-[13px] text-slate-400">
                    Прогонов пока нет. Проведите разговор на вкладке «Разговор».
                </p>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full min-w-[720px] text-[13px]">
                        <thead>
                            <tr className="text-left text-[10.5px] uppercase tracking-wider text-slate-400">
                                <th className="py-2 pr-3 font-semibold">Когда</th>
                                <th className="py-2 pr-3 font-semibold">Режим</th>
                                <th className="py-2 pr-3 font-semibold">Сценарий</th>
                                <th className="py-2 pr-3 font-semibold">Реплик</th>
                                <th className="py-2 pr-3 font-semibold">Пауза</th>
                                <th className="py-2 pr-3 font-semibold">Балл</th>
                                <th className="py-2 font-semibold">Цена</th>
                            </tr>
                        </thead>
                        <tbody>
                            {sessions.map((item) => (
                                <tr key={item.id}
                                    className="cursor-pointer border-t border-slate-100 transition hover:bg-slate-50"
                                    onClick={() => onOpen(item.id)}>
                                    <td className="py-2.5 pr-3 text-slate-500">
                                        {item.started_at ? new Date(item.started_at).toLocaleString('ru-RU', {
                                            day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
                                        }) : '—'}
                                        <div className="text-[11px] text-slate-400">{statusLabel(item.status)}</div>
                                    </td>
                                    <td className="py-2.5 pr-3">
                                        <IosBadge tone={item.mode === 'mentor' ? 'blue' : 'slate'}>
                                            {item.mode === 'mentor' ? 'наставник' : 'водитель'}
                                        </IosBadge>
                                    </td>
                                    <td className="py-2.5 pr-3 text-slate-800">
                                        {item.title}
                                        {item.errors > 0 && (
                                            <span className="ml-2 text-[11px] text-rose-600">ошибок: {item.errors}</span>
                                        )}
                                    </td>
                                    <td className="py-2.5 pr-3 tabular-nums text-slate-500">{item.turns}</td>
                                    <td className={`py-2.5 pr-3 tabular-nums ${PACE_CLASS[paceTone(item.pace_p50)]}`}>
                                        {fmtMs(item.pace_p50)}
                                    </td>
                                    <td className={`py-2.5 pr-3 tabular-nums font-semibold ${
                                        item.score != null ? scoreClass(item.score) : 'text-slate-300'}`}>
                                        {item.score != null ? item.score : '—'}
                                    </td>
                                    <td className="py-2.5 tabular-nums text-slate-500">{fmtCost(item.cost_usd)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </IosSection>
    );
};

export default TrainerView;
