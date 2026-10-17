import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { pollDelay, pollGivesUp } from './useDigest';

/* Состояние экрана «Период» в «Сводке»: границы, цифры и ряды для графиков,
 * сводка за месяц (если период — целый прошедший месяц), переписка с ИИ о
 * периоде.
 *
 * Живёт в CallQaView по той же причине, что useDigest: из экрана человек
 * открывает разговор по ссылке, вкладка на это время размонтирована, и
 * вернуться он должен в тот же период — с тем же выбранным графиком, разделом
 * сводки и недописанным вопросом.
 *
 * Слепки дней периода сервер досчитывает в фоне (первое открытие старого
 * месяца), сводку за месяц пишет минутами — в обоих случаях экран опрашивает
 * ЛЁГКОЕ состояние (?light=1) и перечитывает период, когда работа кончилась;
 * после нескольких сбоев подряд говорит об этом прямо.
 */

export const PERIOD_MAX_DAYS = 31;
const DAY_MS = 86400000;

const errText = (e, fallback) => e?.response?.data?.error || fallback;

export const isoOf = (date) => {
    const pad = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
};

const parseIso = (iso) => {
    const [y, m, d] = String(iso || '').split('-').map(Number);
    return new Date(y, m - 1, d);
};

export const periodKey = (range) => (range ? `${range.from}:${range.to}` : null);

/** Сколько дней в периоде (оба края включительно). */
export const spanDays = (range) => (range ? Math.round((parseIso(range.to) - parseIso(range.from)) / DAY_MS) + 1 : 0);

/** Период не длиннее PERIOD_MAX_DAYS: длинный обрезается с конца. */
export const clampRange = (range) => {
    if (!range?.from || !range?.to) return range;
    let { from, to } = range;
    if (to < from) [from, to] = [to, from];
    const span = spanDays({ from, to });
    if (span <= PERIOD_MAX_DAYS) return { from, to, clamped: false };
    const end = parseIso(from);
    end.setDate(end.getDate() + PERIOD_MAX_DAYS - 1);
    return { from, to: isoOf(end), clamped: true };
};

/** Границы календарного месяца «ГГГГ-ММ» (конец — не позже today). */
export const monthRange = (month, today = isoOf(new Date())) => {
    const [y, m] = String(month).split('-').map(Number);
    const last = new Date(y, m, 0);
    const from = `${y}-${String(m).padStart(2, '0')}-01`;
    const to = isoOf(last) > today ? today : isoOf(last);
    return { from, to };
};

/** «ГГГГ-ММ» месяца, если период — целый календарный месяц, иначе null. */
export const wholeMonthOf = (range) => {
    if (!range?.from || !range?.to) return null;
    const from = parseIso(range.from);
    const to = parseIso(range.to);
    if (from.getDate() !== 1 || from.getMonth() !== to.getMonth() || from.getFullYear() !== to.getFullYear()) return null;
    const last = new Date(from.getFullYear(), from.getMonth() + 1, 0);
    return to.getDate() === last.getDate() ? range.from.slice(0, 7) : null;
};

/** Соседний месяц: «2026-10», -1 → «2026-09». */
export const shiftMonth = (month, delta) => {
    const [y, m] = String(month).split('-').map(Number);
    const date = new Date(y, m - 1 + delta, 1);
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
};

/** Готовые периоды для календаря: прошлая неделя, 7 и 30 дней, прошлый месяц. */
export const periodPresets = (today = new Date()) => {
    const day = (offset) => { const d = new Date(today); d.setDate(d.getDate() + offset); return isoOf(d); };
    const monday = new Date(today);
    monday.setDate(today.getDate() - ((today.getDay() + 6) % 7) - 7);
    const sunday = new Date(monday);
    sunday.setDate(monday.getDate() + 6);
    const prevMonth = new Date(today.getFullYear(), today.getMonth() - 1, 1);
    return [
        { label: '7 дней', range: () => ({ from: day(-6), to: day(0) }) },
        { label: '30 дней', range: () => ({ from: day(-29), to: day(0) }) },
        { label: 'Прошлая неделя', range: () => ({ from: isoOf(monday), to: isoOf(sunday) }) },
        { label: 'Прошлый месяц', range: () => monthRange(`${prevMonth.getFullYear()}-${String(prevMonth.getMonth() + 1).padStart(2, '0')}`, isoOf(today)) },
    ];
};

export default function usePeriod({ apiBaseUrl, headers, department, enabled, showToast }) {
    const [range, setRangeState] = useState(null);     // { from, to } — экран открыт
    const [view, setView] = useState(null);             // { key, data, loading, error, stalled }
    const [chart, setChart] = useState('score');        // выбранный график
    const [section, setSection] = useState(null);       // раздел сводки месяца
    const [pane, setPane] = useState('summary');        // узкий экран: сводка или чат
    const [chat, setChat] = useState({ key: null, messages: [], loading: false, error: '' });
    const [askingKeys, setAskingKeys] = useState([]);
    const askingRef = useRef(new Set());
    const [draft, setDraft] = useState('');
    const [draftFocus, setDraftFocus] = useState(null);
    const [generating, setGenerating] = useState(false);
    const generatingRef = useRef(false);
    const headersRef = useRef(headers);
    headersRef.current = headers;
    const toastRef = useRef(showToast);
    toastRef.current = showToast;
    const generation = useRef(0);
    const pollTimer = useRef(null);
    const rangeRef = useRef(range);
    rangeRef.current = range;

    const params = useCallback((extra = {}) => ({ ...(department ? { department } : {}), ...extra }), [department]);
    const cfg = () => ({ headers: headersRef.current?.() || {} });
    const stopPoll = () => {
        if (pollTimer.current) { window.clearTimeout(pollTimer.current); pollTimer.current = null; }
    };
    const markAsking = (key, on) => {
        if (on) askingRef.current.add(key); else askingRef.current.delete(key);
        setAskingKeys([...askingRef.current]);
    };

    const loadViewRef = useRef(null);
    const pollRef = useRef(null);
    const schedulePoll = (key, failures = 0) => {
        stopPoll();
        pollTimer.current = window.setTimeout(() => pollRef.current?.(key, failures), pollDelay(failures));
    };
    const pollFailed = (key, failures) => {
        if (!pollGivesUp(failures)) { schedulePoll(key, failures + 1); return; }
        setView((current) => (current?.key === key ? { ...current, loading: false, stalled: true } : current));
    };

    /* Период целиком. quiet — без мигания загрузкой (опрос дописал данные). */
    const loadView = useCallback((next, { quiet = false, failures = 0 } = {}) => {
        const key = periodKey(next);
        if (!apiBaseUrl || !key) return;
        const gen = generation.current;
        stopPoll();
        if (!quiet) {
            setView((current) => ({ key, data: current?.key === key ? current.data : null, loading: true,
                                    error: '', stalled: false }));
        }
        axios.get(`${apiBaseUrl}/api/ai-qa/period`, { params: params({ from: next.from, to: next.to }), ...cfg() })
            .then((r) => {
                if (gen !== generation.current || periodKey(rangeRef.current) !== key) return;
                const data = r.data || {};
                setView({ key, data, loading: false, error: '', stalled: false });
                if (data.preparing || data.month?.running) schedulePoll(key);
            })
            .catch((e) => {
                if (gen !== generation.current || periodKey(rangeRef.current) !== key) return;
                if (quiet) { pollFailed(key, failures); return; }
                setView((current) => ({ key, data: current?.key === key ? current.data : null, loading: false,
                                        error: errText(e, 'Не удалось собрать период'), stalled: false }));
            });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, params]);
    loadViewRef.current = loadView;

    /* Опрос, пока сервер досчитывает слепки или пишет сводку месяца: только
       состояние; кончилось — один раз перечитать период. */
    const poll = useCallback((key, failures = 0) => {
        const current = rangeRef.current;
        if (!apiBaseUrl || !current || periodKey(current) !== key) return;
        const gen = generation.current;
        axios.get(`${apiBaseUrl}/api/ai-qa/period`, { params: params({ from: current.from, to: current.to, light: 1 }), ...cfg() })
            .then((r) => {
                if (gen !== generation.current || periodKey(rangeRef.current) !== key) return;
                const busy = (r.data?.queued || []).length > 0 || Boolean(r.data?.month?.running);
                if (busy) { schedulePoll(key); return; }
                loadViewRef.current?.(current, { quiet: true, failures });
            })
            .catch(() => {
                if (gen !== generation.current || periodKey(rangeRef.current) !== key) return;
                pollFailed(key, failures);
            });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, params]);
    pollRef.current = poll;

    const loadChat = useCallback((next) => {
        const key = periodKey(next);
        if (!apiBaseUrl || !key) return;
        const gen = generation.current;
        setChat({ key, messages: [], loading: true, error: '' });
        axios.get(`${apiBaseUrl}/api/ai-qa/period/chat`, { params: params({ from: next.from, to: next.to }), ...cfg() })
            .then((r) => {
                if (gen !== generation.current || periodKey(rangeRef.current) !== key) return;
                setChat({ key, messages: r.data?.messages || [], loading: false, error: '' });
            })
            .catch((e) => {
                if (gen !== generation.current || periodKey(rangeRef.current) !== key) return;
                setChat({ key, messages: [], loading: false, error: errText(e, 'Не удалось загрузить переписку') });
            });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, params]);

    /* Открыть период (или сменить границы на открытом экране). Длинный период
       обрезается до PERIOD_MAX_DAYS — с предупреждением. */
    const open = useCallback((next) => {
        const clamped = clampRange(next);
        if (!clamped?.from || !clamped?.to) return;
        if (clamped.clamped) toastRef.current?.(`Период — не больше ${PERIOD_MAX_DAYS} дней: обрезан до ${clamped.to}`, 'error');
        const value = { from: clamped.from, to: clamped.to };
        const changed = periodKey(value) !== periodKey(rangeRef.current);
        setRangeState(value);
        rangeRef.current = value;
        if (changed) {
            setSection(null);
            setDraft('');
            setDraftFocus(null);
        }
        loadView(value);
        loadChat(value);
    }, [loadView, loadChat]);

    const openMonth = useCallback((month) => open(monthRange(month)), [open]);

    const close = useCallback(() => {
        stopPoll();
        setRangeState(null);
        rangeRef.current = null;
        setDraftFocus(null);
    }, []);

    const reset = useCallback(() => {
        stopPoll();
        generation.current += 1;
        askingRef.current = new Set();
        generatingRef.current = false;
        setRangeState(null); rangeRef.current = null;
        setView(null); setSection(null); setChart('score'); setPane('summary');
        setChat({ key: null, messages: [], loading: false, error: '' });
        setAskingKeys([]); setDraft(''); setDraftFocus(null); setGenerating(false);
    }, []);

    useEffect(() => () => { stopPoll(); generation.current += 1; }, []);
    useEffect(() => { if (!enabled) setDraftFocus(null); }, [enabled]);

    /* «Составить» / «Обновить» сводку за месяц; force — только супер-админ. */
    const generateMonth = useCallback((force = false) => {
        const current = rangeRef.current;
        const month = wholeMonthOf(current);
        if (!apiBaseUrl || !month || generatingRef.current) return;
        const key = periodKey(current);
        const gen = generation.current;
        generatingRef.current = true;
        setGenerating(true);
        axios.post(`${apiBaseUrl}/api/ai-qa/period/digest`, { month, force, ...(department ? { department } : {}) }, cfg())
            .then((r) => {
                if (gen !== generation.current) return;
                if (r.data?.status === 'fresh') {
                    toastRef.current?.(r.data?.message || 'Сводка уже учитывает все оценки месяца', 'success');
                    return;
                }
                if (periodKey(rangeRef.current) !== key) return;
                setView((v) => (v?.key === key && v.data
                    ? { ...v, data: { ...v.data, month: { ...(v.data.month || {}), running: true } }, stalled: false } : v));
                schedulePoll(key);
            })
            .catch((e) => {
                if (gen === generation.current) toastRef.current?.(errText(e, 'Не удалось запустить сводку'), 'error');
            })
            .finally(() => {
                if (gen !== generation.current) return;
                generatingRef.current = false;
                setGenerating(false);
            });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, department]);

    /* Вопрос: свой пузырь сразу, ответ — когда придёт; упал — текст обратно в поле. */
    const ask = useCallback((question) => {
        const current = rangeRef.current;
        const key = periodKey(current);
        const text = String(question || '').trim();
        if (!apiBaseUrl || !key || !text || askingRef.current.has(key)) return;
        const gen = generation.current;
        const local = { id: `local-${Date.now()}`, role: 'user', body: text, created_at: new Date().toISOString(), pending: true };
        markAsking(key, true);
        setDraft('');
        setDraftFocus(null);
        setChat((c) => ({ ...c, key, messages: [...(c.key === key ? c.messages : []), local], error: '' }));
        axios.post(`${apiBaseUrl}/api/ai-qa/period/chat`,
            { from: current.from, to: current.to, question: text, ...(department ? { department } : {}) }, cfg())
            .then((r) => {
                if (gen !== generation.current) return;
                setChat((c) => (c.key !== key ? c : {
                    ...c,
                    messages: [...c.messages.filter((m) => m.id !== local.id), r.data?.question, r.data?.answer].filter(Boolean),
                }));
            })
            .catch((e) => {
                if (gen !== generation.current) return;
                setChat((c) => (c.key !== key ? c : { ...c, messages: c.messages.filter((m) => m.id !== local.id) }));
                if (periodKey(rangeRef.current) === key) {
                    setDraft((value) => value || text);
                    setDraftFocus((n) => (n || 0) + 1);
                }
                toastRef.current?.(errText(e, 'ИИ не ответил — попробуйте ещё раз'), 'error');
            })
            .finally(() => { if (gen === generation.current) markAsking(key, false); });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, department]);

    const clearChat = useCallback(() => {
        const current = rangeRef.current;
        const key = periodKey(current);
        if (!apiBaseUrl || !key || askingRef.current.has(key)) return;
        const gen = generation.current;
        axios.delete(`${apiBaseUrl}/api/ai-qa/period/chat`, { params: params({ from: current.from, to: current.to }), ...cfg() })
            .then(() => {
                if (gen !== generation.current) return;
                setChat((c) => (c.key === key ? { ...c, messages: [] } : c));
            })
            .catch((e) => {
                if (gen === generation.current) toastRef.current?.(errText(e, 'Не удалось очистить переписку'), 'error');
            });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, params]);

    const suggest = useCallback((text) => {
        setDraft(text);
        setDraftFocus((n) => (n || 0) + 1);
    }, []);

    return {
        range, key: periodKey(range), view, chart, setChart, section, setSection, pane, setPane,
        chat, asking: Boolean(chat.key && askingKeys.includes(chat.key)),
        draft, setDraft, draftFocus, generating,
        open, openMonth, close, reset,
        reloadView: () => range && loadView(range),
        generateMonth, ask, clearChat, suggest,
    };
}
