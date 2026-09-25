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
 *
 * Ширина. Окно телефона узкое (обычно 400–500 px), поэтому карточки в одну колонку,
 * а проигрыватель сам переключается в узкую раскладку (TrainerPlayer: narrow).
 */
import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    ChevronDown, ChevronUp, Layers, Loader2, Monitor, PlayCircle, ShieldCheck, ShieldOff, Smartphone,
} from 'lucide-react';

import { iosCard, iosBtnPrimary, IosBadge } from '../components/ui/ios';
import { TRAINERS, trainerCard } from '../components/wiki/trainers/registry';
import {
    authHeadersFor, hasPhoneHost, isTokenFresh, parseTokenMessage, postToPhone, requestPhoneToken,
} from './phoneBridge';
import { phoneTrainers } from './trainerList';

/* Не весь реестр: тренажёры рабочего места и фотоконтроль в телефоне не показываем
   (почему — в trainerList.js). Считается один раз: реестр статический. */
const PHONE_TRAINERS = phoneTrainers(TRAINERS);

const TrainerModal = lazy(() => import('../components/wiki/trainers/TrainerPlayer'));

const API_BASE_URL = 'https://otp-2-fos4.onrender.com';
const WIKI_BASE = `${API_BASE_URL}/api/wiki`;
const ACCESS_TOKEN_STORAGE_KEY = 'otp_access_token';
// Как часто проверять срок токена. Access живёт 30 минут; проверка раз в полминуты
// успевает попросить свежий за две минуты до истечения (isTokenFresh).
const TOKEN_CHECK_MS = 30000;

const plural = (n, one, few, many) => {
    const mod100 = Math.abs(n) % 100;
    const mod10 = mod100 % 10;
    if (mod100 >= 11 && mod100 <= 14) return many;
    if (mod10 === 1) return one;
    if (mod10 >= 2 && mod10 <= 4) return few;
    return many;
};

const readPortalToken = () => {
    try {
        return String(window.localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY) || '').trim();
    } catch {
        return '';
    }
};

/* Токен для учёта попыток: от телефона (внутри WebView2) или из портала (в браузере). */
function useHostToken() {
    const inPhone = useMemo(() => hasPhoneHost(), []);
    const [auth, setAuth] = useState({ token: '', error: '', asked: false });
    const authRef = useRef(auth);
    authRef.current = auth;
    const busyRef = useRef(false);

    const refresh = useCallback(async () => {
        if (busyRef.current) return;
        busyRef.current = true;
        try {
            if (inPhone) {
                const reply = await requestPhoneToken();
                if (reply) {
                    setAuth({ token: reply.token, error: reply.token ? '' : (reply.error || 'Телефон не отдал токен'), asked: true });
                } else {
                    setAuth((prev) => ({ ...prev, asked: true, error: prev.token ? '' : 'Телефон не ответил' }));
                }
            } else {
                const token = readPortalToken();
                setAuth({ token: isTokenFresh(token, 0) ? token : '', error: '', asked: true });
            }
        } finally {
            busyRef.current = false;
        }
    }, [inPhone]);

    useEffect(() => {
        refresh();
        const timer = setInterval(() => {
            if (!isTokenFresh(authRef.current.token)) refresh();
        }, TOKEN_CHECK_MS);
        const onVisible = () => {
            if (document.visibilityState === 'visible' && !isTokenFresh(authRef.current.token)) refresh();
        };
        document.addEventListener('visibilitychange', onVisible);
        // Телефон может прислать токен и сам (перелогин оператора) — подхватываем.
        let bridge = null;
        const onMessage = (event) => {
            const parsed = parseTokenMessage(event?.data);
            if (parsed && parsed.token) setAuth({ token: parsed.token, error: '', asked: true });
        };
        if (inPhone) {
            bridge = window.chrome.webview;
            bridge.addEventListener('message', onMessage);
        }
        return () => {
            clearInterval(timer);
            document.removeEventListener('visibilitychange', onVisible);
            if (bridge) bridge.removeEventListener('message', onMessage);
        };
    }, [inPhone, refresh]);

    return { inPhone, token: auth.token, error: auth.error, asked: auth.asked };
}

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
    const { inPhone, token, error, asked } = useHostToken();
    const [open, setOpen] = useState(null);

    useEffect(() => {
        document.title = 'Тренажёры';
        postToPhone({ type: 'ready' });
    }, []);

    const headers = useMemo(() => authHeadersFor(token), [token]);
    /* Учёт попытки — только с токеном: без него запрос ушёл бы в 401 и молча потерялся,
       а с record=null проигрыватель честно работает без записи. */
    const record = useMemo(() => (
        headers
            ? { base: WIKI_BASE, headers, articleId: null, source: inPhone ? 'phone' : 'catalog' }
            : null
    ), [headers, inPhone]);

    const openTrainer = useCallback((scenario) => {
        setOpen(scenario);
        postToPhone({ type: 'trainer:open', key: scenario.key, title: scenario.title });
    }, []);
    const closeTrainer = useCallback(() => {
        setOpen(null);
        postToPhone({ type: 'trainer:close' });
    }, []);

    const recording = !!token;
    const authHint = !asked
        ? 'Подключаемся…'
        : recording
            ? 'Попытки записываются'
            : (error || (inPhone ? 'Без записи попыток' : 'Войдите в портал, чтобы попытки записывались'));

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
                    {!asked
                        ? <Loader2 size={12} className="animate-spin" />
                        : recording ? <ShieldCheck size={12} /> : <ShieldOff size={12} />}
                    <span className="hidden sm:inline">{recording ? 'с записью' : 'без записи'}</span>
                </span>
            </header>

            {asked && !recording && (
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
