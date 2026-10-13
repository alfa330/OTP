/* Страница тренажёров внутри iCORE Phone.
 *
 * Что здесь есть и чего нет — намеренно. Есть список тренажёров из реестра
 * (components/wiki/trainers/registry.js — тот же, что у вики) и сам проигрыватель.
 * Нет статистики, «где вставлен» и прочего, что нужно редактору вики: оператору на
 * смене нужно открыть тренажёр и пройти, остальное — в портале.
 *
 * Вход. Внутри телефона токен приходит по мосту WebView2 (phoneBridge.js): страница
 * просит его при старте и заново, когда срок на исходе; refresh-токена у неё нет.
 * В обычном браузере страница берёт access-токен портала из localStorage (тот же
 * origin) — так её можно посмотреть и без телефона. Без токена тренажёры всё равно
 * работают, только попытки не записываются — учёт никогда не мешает уроку.
 * Хук токена общий со страницей офисов — src/embed/useHostToken.js.
 *
 * Запись. Токен ещё не значит «попытки пишутся»: у операторов вики, а с ней и
 * /trainers/runs, закрыта QR-подтверждением сессии, и без него сервер молча
 * отказывает. Значок спрашивает сервер (useRecordingAccess) и честно говорит «без
 * записи», а включить запись можно тут же, тем же QR, что открывает «Офисы». Если
 * спросить не вышло (сеть, портал), значок говорит «не проверено» и спрашивает
 * снова сам — молчаливого «с записью» по умолчанию нет.
 *
 * Ширина. Окно телефона узкое (обычно 400–500 px), поэтому карточки в одну колонку,
 * а проигрыватель сам переключается в узкую раскладку (TrainerPlayer: narrow).
 */
import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import lazyWithRetry from '../utils/lazyWithRetry';
import {
    ChevronDown, ChevronUp, Layers, Loader2, Monitor, PlayCircle, QrCode, RefreshCw, ShieldCheck, ShieldOff,
    ShieldQuestion, Smartphone,
} from 'lucide-react';

import { iosCard, iosBtnPrimary, IosBadge } from '../components/ui/ios';
import { TRAINERS, trainerCard } from '../components/wiki/trainers/registry';
import useHostToken from '../embed/useHostToken';
import QrUnlockPanel from '../embed/QrUnlockPanel';
import { accessStateFromError, retryDelayMs, sessionKeyOf } from '../embed/embedAccess';
import {
    authHeadersFor, parseHostMessage, postToPhone, waitForViewport,
} from './phoneBridge';
import { phoneTrainers } from './trainerList';

/* Не весь реестр: тренажёры рабочего места и фотоконтроль в телефоне не показываем
   (почему — в trainerList.js). Считается один раз: реестр статический. */
const PHONE_TRAINERS = phoneTrainers(TRAINERS);

const TrainerModal = lazyWithRetry(() => import('../components/wiki/trainers/TrainerPlayer'));

const API_BASE_URL = 'https://otp-2-fos4.onrender.com';
const WIKI_BASE = `${API_BASE_URL}/api/wiki`;
// Сколько ждать от телефона "trainer:host" после "trainer:open": не ответил — значит,
// окно он не открыл, и тренажёр монтируем во вкладке как есть.
const HOST_REPLY_MS = 1500;

const plural = (n, one, few, many) => {
    const mod100 = Math.abs(n) % 100;
    const mod10 = mod100 % 10;
    if (mod100 >= 11 && mod100 <= 14) return many;
    if (mod10 === 1) return one;
    if (mod10 >= 2 && mod10 <= 4) return few;
    return many;
};

/* Пишутся ли попытки НА САМОМ ДЕЛЕ.
 *
 * Прежде значок горел «с записью», как только был токен. Но у операторов вики, а
 * с ней и POST /trainers/runs, закрыта QR-гейтом (wiki/routes.py): без
 * подтверждённой сессии сервер отвечает 403 SENSITIVE_ACCESS_REQUIRED, а
 * useTrainerRun такие отказы глотает молча (учёт не мешает уроку). Значок обещал
 * запись, строк в статистике не было.
 *
 * Спрашиваем ту же дверь, через которую идёт запись: GET /api/wiki/me проходит
 * wiki_route (отдел, QR-гейт) и больше ничего не проверяет — как и сама запись.
 * /api/sensitive-access/status тут не годится: «нужен QR» он ответил бы и стажёру,
 * которому вики и запись открыты без подтверждения.
 *
 * status: checking | on | qr | blocked | expired | unknown | off.
 *
 * unknown — проверка не дошла: сеть, таймаут, 5xx. Прежде такой сбой считался «on»
 * («пугать "без записи" из-за минутного обрыва незачем»), но проверка после него
 * не повторялась вовсе: одна неудача на старте — выкладка API, мигнувший Wi-Fi — и
 * «с записью» горело всю смену, до перевхода в телефон. Ровно у тех, кому нужен QR
 * и чьи попытки сервер отклоняет: значок обещал запись, плашка с «Включить запись»
 * была скрыта, в статистике — ни строки. Теперь это честное «не проверено»:
 * значок нейтральный, а проверка повторяется сама с растущей паузой и при
 * возврате во вкладку.
 */
const VERDICT_STATUS = { locked: 'qr', denied: 'blocked', 'no-session': 'expired' };

/** Отказ проверки записи → { status, message }. Всё, что не отказ по существу
 *  (сеть, 5xx, невнятный ответ), — «не проверено», а не «записывается». */
export const recordingStatusOf = (err) => {
    const verdict = accessStateFromError(err);
    return { status: VERDICT_STATUS[verdict.state] || 'unknown', message: verdict.message };
};

// Сколько ждать ответа на проверку записи. Без срока повисшее соединение держало
// бы «проверяем…» без конца, и повторов не было бы вовсе; полминуты хватает и на
// холодный старт API.
const RECORD_CHECK_TIMEOUT_MS = 30000;
// Как часто переспрашивать у телефона токен, когда сервер сказал «сессии нет».
const SESSION_RETRY_MS = 30000;

function useRecordingAccess(token, refreshToken) {
    const sessionKey = useMemo(() => sessionKeyOf(token), [token]);
    const tokenRef = useRef(token);
    tokenRef.current = token;
    // misses — сколько проверок подряд не дошло: от него пауза перед следующей.
    const [probe, setProbe] = useState({ key: '', status: 'checking', message: '', misses: 0 });
    const [nonce, setNonce] = useState(0);
    const recheck = useCallback(() => setNonce((n) => n + 1), []);

    // Проверка — на каждую новую СЕССИЮ (sid), а не на каждый токен: подтверждение
    // живёт на сессии, а токен обновляется раз в полчаса.
    useEffect(() => {
        if (!sessionKey) return undefined;
        let cancelled = false;
        axios.get(`${WIKI_BASE}/me`, { headers: authHeadersFor(tokenRef.current), timeout: RECORD_CHECK_TIMEOUT_MS })
            .then(() => {
                if (!cancelled) setProbe({ key: sessionKey, status: 'on', message: '', misses: 0 });
            })
            .catch((err) => {
                if (cancelled) return;
                const { status, message } = recordingStatusOf(err);
                setProbe((prev) => ({
                    key: sessionKey,
                    status,
                    message,
                    misses: status === 'unknown' ? (prev.key === sessionKey ? prev.misses : 0) + 1 : 0,
                }));
            });
        return () => { cancelled = true; };
    }, [sessionKey, nonce]);

    const fresh = probe.key === sessionKey;
    const status = !sessionKey ? 'off' : fresh ? probe.status : 'checking';
    const misses = fresh ? probe.misses : 0;

    // Не проверено — спрашиваем снова сами: через 3, 6, 12, 24, потом каждые 30 с.
    // Спрятанная вкладка запросов не шлёт — её перепроверит возврат (эффект ниже).
    useEffect(() => {
        if (status !== 'unknown') return undefined;
        const timer = setTimeout(() => {
            if (document.visibilityState !== 'hidden') recheck();
        }, retryDelayMs(misses - 1));
        return () => clearTimeout(timer);
    }, [status, misses, recheck]);

    // Вернулись во вкладку без записи — может, QR уже подтвердили во вкладке «Офисы»,
    // а «не проверено» самое время проверить. Для «on» повод не нужен.
    useEffect(() => {
        if (status === 'on' || status === 'checking' || status === 'off') return undefined;
        const onVisible = () => {
            if (document.visibilityState === 'visible') recheck();
        };
        document.addEventListener('visibilitychange', onVisible);
        return () => document.removeEventListener('visibilitychange', onVisible);
    }, [status, recheck]);

    /* Сервер сказал «сессии нет» (401). Токен той же сессии может прийти новым —
       часы компьютера отстали, и прежний истёк раньше, чем казалось, — а эффект
       проверки ждёт смены sid и сам не пойдёт: «без записи» висело бы до перевхода.
       Поэтому раз в полминуты переспрашиваем токен у телефона, и любой новый
       токен — повод проверить заново. Новую сессию (другой sid) проверит эффект
       выше: статус у неё уже «checking», и второй запрос отсюда не уйдёт. */
    useEffect(() => {
        if (status !== 'expired' || !refreshToken) return undefined;
        const timer = setInterval(() => { refreshToken(); }, SESSION_RETRY_MS);
        return () => clearInterval(timer);
    }, [status, refreshToken]);
    const seenToken = useRef(token);
    useEffect(() => {
        const previous = seenToken.current;
        seenToken.current = token;
        if (token && previous && token !== previous && status === 'expired') recheck();
    }, [token, status, recheck]);

    return { status, message: fresh ? probe.message : '', recheck };
}

/** Подпись к значку записи (и текст плашки под шапкой). */
export const recordingHint = ({ asked, status, message = '', error = '', inPhone = false }) => {
    if (!asked) return 'Подключаемся…';
    switch (status) {
        case 'checking': return 'Проверяем запись…';
        case 'on': return 'Попытки записываются';
        case 'unknown': return 'Запись не проверена — нет связи с порталом';
        case 'qr': return 'Без записи — нужно подтверждение по QR';
        case 'blocked': return message || 'Без записи — вики недоступна';
        case 'expired':
            return inPhone
                ? 'Сессия закончилась — войдите в iCORE Phone заново'
                : 'Сессия портала закончилась — войдите заново';
        default:
            return error || (inPhone ? 'Без записи попыток' : 'Войдите в портал, чтобы попытки записывались');
    }
};

const StageIcon = ({ stage }) => (
    stage === 'desktop'
        ? <Monitor size={18} className="text-indigo-500" aria-hidden="true" />
        : <Smartphone size={18} className="text-blue-500" aria-hidden="true" />
);

function TrainerCardView({ scenario, onOpen }) {
    const card = useMemo(() => trainerCard(scenario), [scenario]);
    const [showSteps, setShowSteps] = useState(false);
    const steps = card.checklist || [];
    const sandbox = scenario.mode === 'sandbox';

    return (
        <article className={`${iosCard} p-4 space-y-3`}>
            <header className="flex items-start gap-3">
                <div className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-100">
                    <StageIcon stage={card.stage} />
                </div>
                <div className="min-w-0 flex-1">
                    <h2 className="text-[15px] font-semibold leading-snug text-slate-900">{card.title}</h2>
                    <p className="mt-0.5 truncate text-[12px] text-slate-500">{card.subtitle}</p>
                </div>
            </header>

            <div className="flex flex-wrap gap-1.5">
                <IosBadge tone="slate">
                    {card.stage === 'desktop' ? <Monitor size={11} /> : <Smartphone size={11} />} {card.app}
                </IosBadge>
                {sandbox ? (
                    <IosBadge tone="blue"><Layers size={11} /> свободная среда</IosBadge>
                ) : card.stages > 0 ? (
                    <IosBadge tone="blue">
                        <Layers size={11} /> {card.stages} {plural(card.stages, 'шаг', 'шага', 'шагов')}
                    </IosBadge>
                ) : null}
            </div>

            <p className="text-[13px] leading-relaxed text-slate-600">{card.description}</p>

            {steps.length > 0 && (
                <div>
                    <button
                        type="button"
                        onClick={() => setShowSteps((v) => !v)}
                        className="inline-flex items-center gap-1 text-[12.5px] font-medium text-slate-500 hover:text-slate-700"
                        aria-expanded={showSteps}
                    >
                        {showSteps ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                        {showSteps ? 'Скрыть шаги' : `Что внутри · ${steps.length}`}
                    </button>
                    {showSteps && (
                        <ol className="mt-2 space-y-1 pl-5 text-[12.5px] leading-relaxed text-slate-500 [list-style:decimal]">
                            {steps.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}
                        </ol>
                    )}
                </div>
            )}

            <button type="button" className={`${iosBtnPrimary} w-full`} onClick={() => onOpen(scenario)}>
                <PlayCircle size={16} /> Пройти тренажёр
            </button>
        </article>
    );
}

export default function TrainersEmbed() {
    const { inPhone, token, error, asked, refresh } = useHostToken();
    const { status: recordStatus, message: recordMessage, recheck: recheckRecording } = useRecordingAccess(token, refresh);
    // Окно QR «Включить запись» — только по нажатию: тренажёры работают и без записи.
    const [showQr, setShowQr] = useState(false);
    /* Открыли «Включить запись» — подводим панель к верху вкладки. Она стоит под
       шапкой страницы, и во вкладке высотой 285 px кнопка «Скопировать код»
       оказывалась под нижним краем — а удалённому оператору нужна именно она. */
    const qrBoxRef = useRef(null);
    useEffect(() => {
        if (showQr) qrBoxRef.current?.scrollIntoView?.({ block: 'start' });
    }, [showQr]);
    const [open, setOpen] = useState(null);
    /* Тренажёр, который ждёт, пока телефон вынесет страницу в большое окно.
       В ref, а не в state: ответ телефона приходит в слушатель, который вешается один раз. */
    const pendingRef = useRef(null);      // { scenario, fromWidth, timer }

    useEffect(() => {
        document.title = 'Тренажёры';
        postToPhone({ type: 'ready' });
    }, []);

    /* Смонтировать отложенный тренажёр — после того, как окно сменило ширину (или
       не сменит: телефон не открыл окно либо не ответил вовремя). */
    const mountPending = useCallback(async () => {
        const pending = pendingRef.current;
        if (!pending) return;
        pendingRef.current = null;
        clearTimeout(pending.timer);
        await waitForViewport(pending.fromWidth);
        setOpen(pending.scenario);
    }, []);

    /* Команды телефона: окно открыто (монтируем тренажёр) и «закрой тренажёр»
       (крестик окна или входящий звонок — закрываем без анимации, сразу). */
    useEffect(() => {
        if (!inPhone) return undefined;
        const bridge = window.chrome.webview;
        const onMessage = (event) => {
            const msg = parseHostMessage(event?.data);
            if (!msg) return;
            if (msg.type === 'trainer:host' && pendingRef.current) {
                mountPending();
            } else if (msg.type === 'trainer:close-request') {
                if (pendingRef.current) {
                    clearTimeout(pendingRef.current.timer);
                    pendingRef.current = null;
                }
                setOpen(null);
                postToPhone({ type: 'trainer:close' });
            }
        };
        bridge.addEventListener('message', onMessage);
        return () => bridge.removeEventListener('message', onMessage);
    }, [inPhone, mountPending]);

    const headers = useMemo(() => authHeadersFor(token), [token]);
    /* Учёт попытки — только с токеном: без него запрос ушёл бы в 401 и молча потерялся,
       а с record=null проигрыватель честно работает без записи. */
    const record = useMemo(() => (
        headers
            ? { base: WIKI_BASE, headers, articleId: null, source: inPhone ? 'phone' : 'catalog' }
            : null
    ), [headers, inPhone]);

    const openTrainer = useCallback((scenario) => {
        if (!inPhone) {
            setOpen(scenario);
            return;
        }
        /* В телефоне тренажёр не монтируем сразу: телефон на "trainer:open" переносит
           страницу в окно на весь экран и отвечает "trainer:host". Проигрыватель меряет
           ширину один раз при монтировании — смонтированный в узкой вкладке, он остался
           бы сжатым на весь урок (владелец, 25.09.2026: «в окне ноутбука тренажёр очень
           сжатый»). */
        if (pendingRef.current) clearTimeout(pendingRef.current.timer);
        pendingRef.current = {
            scenario,
            fromWidth: window.innerWidth,
            timer: setTimeout(() => mountPending(), HOST_REPLY_MS),
        };
        postToPhone({ type: 'trainer:open', key: scenario.key, title: scenario.title });
    }, [inPhone, mountPending]);
    const closeTrainer = useCallback(() => {
        setOpen(null);
        postToPhone({ type: 'trainer:close' });
    }, []);

    const onQrGranted = useCallback(() => {
        setShowQr(false);
        recheckRecording();
    }, [recheckRecording]);

    const checking = !asked || recordStatus === 'checking';
    const recording = recordStatus === 'on';
    // «Не проверено» — не «без записи»: проверка не дошла, и что ответит сервер,
    // пока неизвестно. Значок и плашка нейтральные, перепроверка идёт сама.
    const unverified = !checking && recordStatus === 'unknown';
    const authHint = recordingHint({ asked, status: recordStatus, message: recordMessage, error, inPhone });

    return (
        <div className="mx-auto min-h-screen w-full max-w-2xl px-3 pb-8 pt-3 sm:px-4">
            <header className="mb-3 flex items-start justify-between gap-3 px-1">
                <div className="min-w-0">
                    <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">Тренажёры</h1>
                    <p className="mt-0.5 text-[12.5px] text-slate-500">
                        Учебные симуляции приложений — пройдите заранее, чтобы уверенно подсказывать водителю.
                    </p>
                </div>
                <span
                    title={authHint}
                    className={`mt-1 inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-1 text-[11px] font-medium
                                ${recording ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}
                >
                    {checking
                        ? <Loader2 size={12} className="animate-spin" />
                        : recording ? <ShieldCheck size={12} />
                            : unverified ? <ShieldQuestion size={12} /> : <ShieldOff size={12} />}
                    {/* Пока ответа нет, подпись не утверждает ни «с записью», ни «без». */}
                    <span className="hidden sm:inline">
                        {checking ? 'проверяем…' : recording ? 'с записью' : unverified ? 'не проверено' : 'без записи'}
                    </span>
                </span>
            </header>

            {/* Нужен QR — запись включается тут же, тем же кодом, что открывает «Офисы»:
                подтверждение одно на сессию телефона. */}
            {!checking && recordStatus === 'qr' && (showQr ? (
                <div ref={qrBoxRef} className="mb-3 scroll-mt-3 space-y-2">
                    <QrUnlockPanel
                        apiBase={API_BASE_URL}
                        headers={headers}
                        onGranted={onQrGranted}
                        title="Запись включится после подтверждения"
                        inPhone={inPhone}
                    />
                    <button
                        type="button"
                        onClick={() => setShowQr(false)}
                        className="w-full rounded-xl px-3 py-2 text-[12.5px] font-medium text-slate-500 hover:bg-slate-100"
                    >
                        Не сейчас
                    </button>
                </div>
            ) : (
                <div className="mb-3 rounded-xl bg-amber-50 px-3 py-2 text-[12px] leading-relaxed text-amber-800">
                    <p>
                        Попытки не записываются — нужно подтверждение по QR. Тренажёры при этом
                        работают полностью.
                    </p>
                    <button
                        type="button"
                        onClick={() => setShowQr(true)}
                        className="mt-1.5 inline-flex items-center gap-1.5 font-semibold text-amber-900 hover:underline"
                    >
                        <QrCode size={13} aria-hidden="true" /> Включить запись
                    </button>
                </div>
            ))}

            {/* Проверка записи не дошла (сеть, портал). Плашка серая, а не жёлтая «без
                записи»: что ответит сервер, неизвестно, и пугать зря незачем. Но и
                молчать нельзя — если сессии нужен QR, попытки сейчас не пишутся. */}
            {unverified && (
                <div className="mb-3 flex items-start gap-2 rounded-xl bg-slate-100 px-3 py-2 text-[12px] leading-relaxed text-slate-600">
                    <p className="min-w-0 flex-1">
                        Не удалось проверить, записываются ли попытки: нет связи с порталом.
                        Проверим снова сами — тренажёры работают и так.
                    </p>
                    <button
                        type="button"
                        onClick={recheckRecording}
                        className="inline-flex shrink-0 items-center gap-1 font-semibold text-slate-700 hover:underline"
                    >
                        <RefreshCw size={13} aria-hidden="true" /> Проверить
                    </button>
                </div>
            )}

            {!checking && !recording && !unverified && recordStatus !== 'qr' && (
                <p className="mb-3 rounded-xl bg-amber-50 px-3 py-2 text-[12px] leading-relaxed text-amber-800">
                    {authHint}. Тренажёры при этом работают полностью — не записывается только прохождение.
                </p>
            )}

            <div className="space-y-3">
                {PHONE_TRAINERS.map((scenario) => (
                    <TrainerCardView key={scenario.key} scenario={scenario} onOpen={openTrainer} />
                ))}
            </div>

            {open && (
                <Suspense fallback={(
                    <div className="fixed inset-0 z-[95] flex items-center justify-center gap-2 bg-slate-900/40 text-white backdrop-blur-md">
                        <Loader2 size={18} className="animate-spin" />
                        <span className="text-[13px]">Готовим тренажёр…</span>
                    </div>
                )}>
                    <TrainerModal scenario={open} onClose={closeTrainer} record={record} />
                </Suspense>
            )}
        </div>
    );
}
