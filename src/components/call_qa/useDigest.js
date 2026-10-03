import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';

/* Состояние вкладки «Сводка» — список дней, открытый день, его сводка и
 * переписка с ИИ.
 *
 * Живёт в CallQaView по той же причине, что очередь и «Звонки»: из сводки
 * человек открывает разговор по ссылке, вкладка на это время размонтирована, и
 * вернуться он должен в ту же сводку — тот же день, тот же раздел, тот же
 * недописанный вопрос и тот же ответ, который ИИ дописывал, пока человек
 * смотрел разговор.
 *
 * Генерация идёт на сервере в фоне (минута на отдел): после «Составить» экран
 * опрашивает ЛЁГКОЕ состояние сводки (?light=1 — без чтения дня), пока она
 * пишется, и один раз читает её целиком, когда она готова. Сбой связи посреди
 * опроса — не конец: опрос повторяется с нарастающей паузой, а после нескольких
 * неудач экран говорит об этом прямо и даёт «Проверить», а не висит на «ИИ
 * пишет сводку… страница обновится сама».
 */

const POLL_MS = 5000;
const POLL_MAX_MS = 30000;
const POLL_TRIES = 6;
// Список дней, пока в нём есть «ИИ пишет сводку…», перечитывается сам: день
// могли закрыть посреди генерации, и опросить его стало некому.
const DAYS_POLL_MS = 10000;
const errText = (e, fallback) => e?.response?.data?.error || fallback;

/* Правила опроса и вопроса — чистыми функциями, чтобы их проверял node-тест
   (tests/ai_qa_digest_render.test.mjs), а не только отрисовка с готовыми флагами. */

/** Пауза перед попыткой опроса после failures сбоев подряд: 5, 10, 20, 30, 30 с. */
export const pollDelay = (failures = 0) => Math.min(POLL_MS * 2 ** failures, POLL_MAX_MS);

/** Опрос сдаётся (экран говорит «связь прервалась») после POLL_TRIES сбоев подряд. */
export const pollGivesUp = (failures) => failures + 1 >= POLL_TRIES;

/** «Думаю…» и пузырь ожидания — только в чате того дня, о котором спросили. */
export const askingFor = (askingDays, day) => Boolean(day && (askingDays || []).includes(day));

/** Строка списка дней после свежего чтения сводки дня: «пишется» и заголовок —
    как у прочитанной сводки (у зрителя со скоупом заголовок — его раздела). */
export const syncDayRow = (days, day, data) => (days ? days.map((d) => (d.day === day ? {
    ...d, running: Boolean(data?.running), headline: data?.headline || d.headline || '',
} : d)) : days);

export default function useDigest({ apiBaseUrl, headers, department, enabled, showToast }) {
    const [days, setDays] = useState(null);
    const [daysError, setDaysError] = useState(false);
    const [openDay, setOpenDay] = useState(null);
    // { day, data, loading, error, stalled } — stalled: опрос генерации потерял связь.
    const [view, setView] = useState(null);
    const [section, setSection] = useState(null);     // выбранный раздел открытого дня
    // На узком экране сводка и чат — две вкладки одного экрана; выбор переживает
    // переход в карточку разговора и обратно.
    const [pane, setPane] = useState('summary');
    const [chat, setChat] = useState({ day: null, messages: [], loading: false, error: '' });
    // Дни, вопрос о которых сейчас у ИИ. Вопрос привязан к своему дню: перешёл
    // человек к соседнему — там ни «Думаю…», ни пузыря ожидания, а ответ ляжет
    // в переписку своего дня.
    const [askingDays, setAskingDays] = useState([]);
    const askingRef = useRef(new Set());
    const [draft, setDraft] = useState('');
    // Просьба встать в поле ввода — одноразовая: null — просьбы нет (ChatComposer
    // на null не фокусируется). Сбрасывается при смене дня и после отправки:
    // залипший ключ забирал бы фокус при каждой загрузке переписки.
    const [draftFocus, setDraftFocus] = useState(null);
    const [generating, setGenerating] = useState(false);
    const generatingRef = useRef(false);
    const headersRef = useRef(headers);
    headersRef.current = headers;
    const toastRef = useRef(showToast);
    toastRef.current = showToast;
    const generation = useRef(0);
    const pollTimer = useRef(null);
    const openDayRef = useRef(openDay);
    openDayRef.current = openDay;

    const params = useCallback((extra = {}) => ({ ...(department ? { department } : {}), ...extra }), [department]);
    const cfg = () => ({ headers: headersRef.current?.() || {} });

    const stopPoll = () => {
        if (pollTimer.current) { window.clearTimeout(pollTimer.current); pollTimer.current = null; }
    };

    const markAsking = (day, on) => {
        if (on) askingRef.current.add(day); else askingRef.current.delete(day);
        setAskingDays([...askingRef.current]);
    };

    const daysRef = useRef(days);
    daysRef.current = days;

    /* background — перечитать уже показанный список (сводка дописалась, в
       списке «пишется»): сбой такого запроса список не стирает. */
    const loadDays = useCallback(({ background = false } = {}) => {
        if (!apiBaseUrl) { setDays([]); setDaysError(true); return; }
        const gen = generation.current;
        if (!background) setDaysError(false);
        axios.get(`${apiBaseUrl}/api/ai-qa/digests`, { params: params(), ...cfg() })
            .then((r) => { if (gen === generation.current) setDays(r.data?.days || []); })
            .catch(() => {
                if (gen !== generation.current) return;
                if (background && daysRef.current !== null) return;
                setDays([]); setDaysError(true);
            });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, params]);

    // Ссылки на функции опроса друг на друга — через ref: loadView ставит опрос,
    // опрос по окончании зовёт loadView.
    const loadViewRef = useRef(null);
    const pollRef = useRef(null);

    const schedulePoll = (day, failures = 0) => {
        stopPoll();
        pollTimer.current = window.setTimeout(() => pollRef.current?.(day, failures), pollDelay(failures));
    };

    /* Сбой опроса: ещё попытка с паузой подлиннее, а после POLL_TRIES подряд —
       честное «связь прервалась» поверх уже показанной сводки. */
    const pollFailed = (day, failures) => {
        if (!pollGivesUp(failures)) { schedulePoll(day, failures + 1); return; }
        setView((current) => (current?.day === day ? { ...current, loading: false, stalled: true } : current));
    };

    /* Сводка дня. quiet — без мигания загрузкой (после генерации); failures —
       сколько сбоев подряд уже было у опроса: тихое чтение, упавшее после
       дописанной генерации, продолжает тот же счёт, а не начинает его заново
       (иначе «лёгкий опрос ок → чтение упало» крутилось бы вечно). */
    const loadView = useCallback((day, { quiet = false, failures = 0 } = {}) => {
        if (!apiBaseUrl || !day) return;
        const gen = generation.current;
        stopPoll();
        if (!quiet) {
            setView((current) => ({ day, data: current?.day === day ? current.data : null, loading: true,
                                    error: '', stalled: false }));
        }
        axios.get(`${apiBaseUrl}/api/ai-qa/digest`, { params: params({ day }), ...cfg() })
            .then((r) => {
                if (gen !== generation.current || openDayRef.current !== day) return;
                const data = r.data || {};
                setView({ day, data, loading: false, error: '', stalled: false });
                // Строка дня в списке — по той же сводке: иначе она так и
                // крутила бы «ИИ пишет сводку…» после возврата к списку.
                setDays((list) => syncDayRow(list, day, data));
                if (data.running) schedulePoll(day);
            })
            .catch((e) => {
                if (gen !== generation.current || openDayRef.current !== day) return;
                if (quiet) { pollFailed(day, failures); return; }
                setView((current) => ({ day, data: current?.day === day ? current.data : null, loading: false,
                                        error: errText(e, 'Не удалось загрузить сводку'), stalled: false }));
            });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, params]);
    loadViewRef.current = loadView;

    /* Опрос, пока сводка пишется: только её состояние. Дописана — один раз
       читаем день целиком и обновляем строку дня в списке (там заголовок). */
    const poll = useCallback((day, failures = 0) => {
        if (!apiBaseUrl || !day) return;
        const gen = generation.current;
        axios.get(`${apiBaseUrl}/api/ai-qa/digest`, { params: params({ day, light: 1 }), ...cfg() })
            .then((r) => {
                if (gen !== generation.current || openDayRef.current !== day) return;
                if (r.data?.running) { schedulePoll(day); return; }
                loadViewRef.current?.(day, { quiet: true, failures });
                loadDays({ background: true });
            })
            .catch(() => {
                if (gen !== generation.current || openDayRef.current !== day) return;
                pollFailed(day, failures);
            });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, params, loadDays]);
    pollRef.current = poll;

    const loadChat = useCallback((day) => {
        if (!apiBaseUrl || !day) return;
        const gen = generation.current;
        setChat({ day, messages: [], loading: true, error: '' });
        axios.get(`${apiBaseUrl}/api/ai-qa/digest/chat`, { params: params({ day }), ...cfg() })
            .then((r) => {
                if (gen !== generation.current || openDayRef.current !== day) return;
                setChat({ day, messages: r.data?.messages || [], loading: false, error: '' });
            })
            .catch((e) => {
                if (gen !== generation.current || openDayRef.current !== day) return;
                setChat({ day, messages: [], loading: false, error: errText(e, 'Не удалось загрузить переписку') });
            });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, params]);

    const open = useCallback((day) => {
        if (!day) return;
        setOpenDay(day);
        openDayRef.current = day;
        setSection(null);
        setPane('summary');
        setDraft('');
        setDraftFocus(null);
        loadView(day);
        loadChat(day);
    }, [loadView, loadChat]);

    const close = useCallback(() => {
        stopPoll();
        setOpenDay(null);
        openDayRef.current = null;
        setDraftFocus(null);
    }, []);

    const reset = useCallback(() => {
        stopPoll();
        generation.current += 1;
        askingRef.current = new Set();
        generatingRef.current = false;
        setDays(null); setDaysError(false); setOpenDay(null); openDayRef.current = null;
        setView(null); setSection(null); setChat({ day: null, messages: [], loading: false, error: '' });
        setAskingDays([]); setDraft(''); setDraftFocus(null); setGenerating(false);
    }, []);

    const loadedFor = useRef(null);
    useEffect(() => {
        if (!enabled || department === null) return;
        if (loadedFor.current === department && days !== null) return;
        loadedFor.current = department;
        loadDays();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [enabled, department, loadDays]);

    // Ушли из раздела: опрос и ответы, которые ещё в пути, — уже ни к чему.
    useEffect(() => () => { stopPoll(); generation.current += 1; }, []);

    // Ушли с вкладки — просьба встать в поле ввода снимается: иначе при каждом
    // возврате на «Сводку» поле само забирало бы фокус и прокручивало страницу.
    useEffect(() => { if (!enabled) setDraftFocus(null); }, [enabled]);

    // Список дней на экране, а в нём «ИИ пишет сводку…» — перечитывать его, пока
    // не допишется: опрашивать открытый день уже некому.
    useEffect(() => {
        if (!enabled || openDay || !days?.some((d) => d.running)) return undefined;
        const timer = window.setTimeout(() => loadDays({ background: true }), DAYS_POLL_MS);
        return () => window.clearTimeout(timer);
    }, [enabled, openDay, days, loadDays]);

    /* «Составить» / «Обновить» сводку. force — переписать с тем же набором
       оценок; сервер разрешает это только супер-админу. */
    const generate = useCallback((force = false) => {
        const day = openDayRef.current;
        if (!apiBaseUrl || !day || generatingRef.current) return;
        const gen = generation.current;
        generatingRef.current = true;
        setGenerating(true);
        axios.post(`${apiBaseUrl}/api/ai-qa/digest`, { day, force, ...(department ? { department } : {}) }, cfg())
            .then((r) => {
                if (gen !== generation.current) return;
                if (r.data?.status === 'fresh') {
                    toastRef.current?.(r.data?.message || 'Сводка уже учитывает все оценки дня', 'success');
                    return;
                }
                setDays((list) => (list ? list.map((d) => (d.day === day ? { ...d, running: true } : d)) : list));
                if (openDayRef.current !== day) return;
                setView((current) => (current?.day === day && current.data
                    ? { ...current, data: { ...current.data, running: true }, stalled: false } : current));
                schedulePoll(day);
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

    /* Вопрос: свой пузырь сразу, ответ — когда придёт. Упал запрос — пузырь
       снимается, а текст возвращается в поле: перепечатывать вопрос из-за 503
       обидно (тот же приём, что у помощника вики). */
    const ask = useCallback((question) => {
        const day = openDayRef.current;
        const text = String(question || '').trim();
        if (!apiBaseUrl || !day || !text || askingRef.current.has(day)) return;
        const gen = generation.current;
        const local = { id: `local-${Date.now()}`, role: 'user', body: text, created_at: new Date().toISOString(),
                        pending: true };
        markAsking(day, true);
        setDraft('');
        setDraftFocus(null);
        setChat((current) => ({ ...current, day, messages: [...(current.day === day ? current.messages : []), local],
                                error: '' }));
        axios.post(`${apiBaseUrl}/api/ai-qa/digest/chat`,
            { day, question: text, ...(department ? { department } : {}) }, cfg())
            .then((r) => {
                if (gen !== generation.current) return;
                setChat((current) => (current.day !== day ? current : {
                    ...current,
                    messages: [...current.messages.filter((m) => m.id !== local.id),
                               r.data?.question, r.data?.answer].filter(Boolean),
                }));
            })
            .catch((e) => {
                if (gen !== generation.current) return;
                setChat((current) => (current.day !== day ? current
                    : { ...current, messages: current.messages.filter((m) => m.id !== local.id) }));
                if (openDayRef.current === day) {
                    setDraft((value) => value || text);
                    setDraftFocus((n) => (n || 0) + 1);
                }
                toastRef.current?.(errText(e, 'ИИ не ответил — попробуйте ещё раз'), 'error');
            })
            .finally(() => { if (gen === generation.current) markAsking(day, false); });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, department]);

    const clearChat = useCallback(() => {
        const day = openDayRef.current;
        if (!apiBaseUrl || !day || askingRef.current.has(day)) return;
        const gen = generation.current;
        axios.delete(`${apiBaseUrl}/api/ai-qa/digest/chat`, { params: params({ day }), ...cfg() })
            .then(() => {
                if (gen !== generation.current) return;
                setChat((current) => (current.day === day ? { ...current, messages: [] } : current));
            })
            .catch((e) => {
                if (gen === generation.current) toastRef.current?.(errText(e, 'Не удалось очистить переписку'), 'error');
            });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, params]);

    /* Подсказка в поле ввода — текст встаёт в поле, отправляет человек
       (правило владельца: ИИ ничего не отправляет за пользователя). */
    const suggest = useCallback((text) => {
        setDraft(text);
        setDraftFocus((n) => (n || 0) + 1);
    }, []);

    return {
        days, daysError, openDay, view, section, setSection, pane, setPane, chat,
        asking: askingFor(askingDays, chat.day),
        draft, setDraft, draftFocus,
        generating, open, close, reset, reloadDays: () => loadDays(),
        reloadView: () => openDay && loadView(openDay),
        generate, ask, clearChat, suggest,
    };
}
