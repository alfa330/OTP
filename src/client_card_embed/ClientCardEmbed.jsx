/* Страница «Карточка клиента» внутри iCORE Phone (client_card.html, задача #335).
 *
 * Телефон открывает её в своём окне и сообщает о звонках по мосту WebView2
 * (microsip-src/ClientCard.cpp): звонок поступил или начат — страница заводит его карточку с
 * уже подставленным номером, ищет аккаунты и ведёт оператора до «Сохранить».
 *
 * Мост (JSON):
 *   телефон → страница  { type: 'call:start', call_key, phone, direction: 'in' | 'out' }
 *                       { type: 'call:answered', call_key }
 *                       { type: 'call:ended', call_key }
 *                       { type: 'auth:token', … }                       — как у тренажёров и офисов
 *   страница → телефон  { type: 'auth:request' } / { type: 'ready' }     — как у тренажёров и офисов
 *                       { type: 'card:ready' }                           — можно присылать звонки
 *                                                                           (телефон повторит идущий)
 *                       { type: 'card:wrapup', call_key, seconds }       — звонок кончился, карточка
 *                                                                           не заполнена: идёт постобработка
 *                       { type: 'card:done', call_key }                  — карточка сохранена или звонок
 *                                                                           не состоялся: постобработка кончена
 *                       { type: 'open:external', url }                   — открыть аккаунт в браузере
 *
 * Вход. Токен даёт телефон (src/embed/useHostToken.js); в обычном браузере — токен портала из
 * localStorage. Страница работает и без телефона: номер вводят руками («Открыть карточку») —
 * так её проверяют и так работают, пока телефон оператора не обновился.
 *
 * Доступ. Карточка — данные водителей и клиентов; рядовому сотруднику она открывается после
 * QR-подтверждения сессии у руководителя (client_cards/routes.py). Экран QR страница показывает сама.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Check, Copy, Loader2, LogIn, PhoneCall, PhoneIncoming, PhoneOutgoing, RefreshCw, ShieldOff, WifiOff, X,
} from 'lucide-react';

import {
    IosHint, IosSegmented, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput,
} from '../components/ui/ios';
import RequestsPanel from '../components/client_cards/RequestsPanel';
import { createCardsApi } from '../components/client_cards/cardsApi';
import {
    NO_ACCOUNT_LABEL, accountTitle, autoAccountId, errorText, eventIsForCall, fmtClock, fmtWhen, parseCallMessage,
    topicLabel, topicsOfAudience, topicsPayload, wrapupLeft,
} from '../components/client_cards/cardRules';
import { StateBadge } from '../components/client_cards/ui';
import QrUnlockPanel from '../embed/QrUnlockPanel';
import copyText from '../embed/copyText';
import { isApiUrl, retryDelayMs, sessionKeyOf } from '../embed/embedAccess';
import useHostToken from '../embed/useHostToken';
import { ToastStack, useToastStack } from '../offices_embed/ToastStack';
import { postToPhone } from '../trainers_embed/phoneBridge';
import CallCard from './CallCard';
import HistoryTab from './HistoryTab';

// Строку держать дословно: её переписывает конфиг локального стенда
// (vite.stand.config.mjs), как и в OfficesEmbed.jsx, TrainersEmbed.jsx и App.jsx.
const API_BASE_URL = import.meta.env?.VITE_API_BASE_URL || 'https://api.icorehub.kz';

const NOTICES = {
    connecting: { icon: Loader2, spin: true, title: 'Подключаемся…' },
    checking: { icon: Loader2, spin: true, title: 'Открываем карточку…' },
    'no-session': { icon: LogIn, title: 'Нужен вход', text: 'Войдите в iCORE Phone заново — и карточка откроется.' },
    disabled: { icon: ShieldOff, title: 'Карточка клиента не включена',
                text: 'Её включает руководитель отдела в разделе «Карточка клиента» портала.' },
    error: { icon: WifiOff, title: 'Нет связи с порталом', text: 'Повторим сами.' },
};

function Notice({ phase, hint = '', onRetry = null }) {
    const notice = NOTICES[phase] || NOTICES.error;
    const Icon = notice.icon;
    return (
        <div className="mx-auto w-full max-w-md px-4 pt-10">
            <div className={`${iosCard} flex flex-col items-center px-5 py-7 text-center`}>
                <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-500">
                    <Icon size={22} className={notice.spin ? 'animate-spin' : ''} aria-hidden="true" />
                </div>
                <h1 className="mt-3 text-[16px] font-semibold text-slate-900">{notice.title}</h1>
                {notice.text && <p className="mt-1.5 text-[13px] leading-relaxed text-slate-500">{notice.text}</p>}
                {hint && <p className="mt-1 text-[12px] text-slate-400">{hint}</p>}
                {onRetry && (
                    <button type="button" onClick={onRetry} className={`${iosBtnSecondary} mt-4`}>
                        <RefreshCw size={14} /> Повторить
                    </button>
                )}
            </div>
        </div>
    );
}

/* Полоса звонка: кто, куда и что сейчас — разговор, постобработка с отсчётом, «не заполнена». */
function CallStrip({ card, talkStartedAt, receivedAt, nowMs, onClose, onCopy }) {
    const Direction = card.direction === 'out' ? PhoneOutgoing : PhoneIncoming;
    const left = wrapupLeft(card, receivedAt, nowMs);
    let status;
    if (card.state === 'talking') {
        status = <span className="tabular-nums text-blue-700">Разговор {talkStartedAt ? fmtClock((nowMs - talkStartedAt) / 1000) : ''}</span>;
    } else if (card.state === 'ringing') {
        status = <span className="text-amber-700">{card.direction === 'out' ? 'Набираем…' : 'Звонит…'}</span>;
    } else if (card.state === 'wrapup' && left > 0) {
        status = <span className="tabular-nums text-amber-700">Постобработка {fmtClock(left)}</span>;
    } else if (card.state === 'done') {
        status = <span className="inline-flex items-center gap-1 text-emerald-700"><Check size={13} /> Сохранена</span>;
    } else if (card.state === 'void') {
        status = <span className="text-slate-500">Звонок не состоялся</span>;
    } else {
        status = <span className="text-rose-600">Не заполнена</span>;
    }
    const live = card.state === 'ringing' || card.state === 'talking';
    return (
        <div className="flex items-center gap-2.5 px-4 py-2.5">
            <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-full ${live ? 'bg-emerald-500 text-white' : 'bg-slate-100 text-slate-500'}`}>
                <Direction size={16} aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
                <button type="button" onClick={onCopy} title="Скопировать номер"
                        className="group flex items-center gap-1.5 text-[16px] font-semibold tabular-nums text-slate-900">
                    {card.phone_text}
                    <Copy size={12} className="text-slate-300 group-hover:text-slate-500" aria-hidden="true" />
                </button>
                <div className="text-[12px] font-medium">
                    <span className="text-slate-500">{card.direction_label} · </span>{status}
                </div>
            </div>
            {!live && (
                <button type="button" onClick={onClose} aria-label="Закрыть карточку"
                        className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                    <X size={16} />
                </button>
            )}
        </div>
    );
}

/* Пока звонка нет: черновики (карточки, которые оператор не успел заполнить) и открытие
   карточки по номеру руками. */
function IdleScreen({ boot, busy, onOpenPending, onManual }) {
    const [phone, setPhone] = useState('');
    const [direction, setDirection] = useState('in');
    const submit = () => { if (phone.trim()) onManual(phone.trim(), direction); };
    return (
        <div className="space-y-5 pb-6">
            {boot.pending.length > 0 && (
                <section className="space-y-1.5">
                    <div className="flex items-center gap-1.5">
                        <div className={iosGroupLabel}>Черновики · {boot.pending.length}</div>
                        <IosHint label="Что такое черновики"
                                 text={`Карточки звонков, которые вы не успели заполнить: постобработка вышла или поступил следующий звонок. Откройте черновик${boot.records ? ', прослушайте разговор' : ''} и дозаполните причину. В списке — черновики за последние ${boot.pending_days || 7} дней.`} />
                    </div>
                    <ul className="space-y-1.5">
                        {boot.pending.map((card) => (
                            <li key={card.id}>
                                <button type="button" disabled={busy} onClick={() => onOpenPending(card.id)}
                                        className={`${iosCard} flex w-full items-center gap-3 px-3 py-2.5 text-left transition hover:bg-slate-50 active:scale-[0.995]`}>
                                    <span className="min-w-0 flex-1">
                                        <span className="block text-[14px] font-medium tabular-nums text-slate-900">{card.phone_text}</span>
                                        <span className="block truncate text-[12px] text-slate-500">
                                            {card.direction_label} · {fmtWhen(card.created_at)}
                                            {card.account ? ` · ${card.account.kind_label} ${accountTitle(card.account)}` : (card.no_account ? ` · ${NO_ACCOUNT_LABEL}` : '')}
                                            {card.topics.length ? ` · ${topicLabel(card.topics[0])}` : ''}
                                        </span>
                                    </span>
                                    <StateBadge card={card} />
                                </button>
                            </li>
                        ))}
                    </ul>
                </section>
            )}
            <section className="space-y-1.5">
                <div className={iosGroupLabel}>Карточка по номеру</div>
                <div className={`${iosCard} space-y-2.5 p-3`}>
                    <p className="text-[12.5px] leading-relaxed text-slate-500">
                        Карточка открывается сама, когда звонок поступил или начат. Если она не открылась — введите номер.
                    </p>
                    <input value={phone} onChange={(event) => setPhone(event.target.value)} inputMode="tel"
                           onKeyDown={(event) => { if (event.key === 'Enter') submit(); }}
                           placeholder="+7 701 234-56-78" className={iosInput} aria-label="Номер телефона" />
                    <div className="flex items-center justify-between gap-2">
                        <IosSegmented value={direction} onChange={setDirection} ariaLabel="Направление звонка"
                                      options={[{ value: 'in', label: 'Входящий' }, { value: 'out', label: 'Исходящий' }]} />
                        <button type="button" onClick={submit} disabled={busy || !phone.trim()} className={iosBtnPrimary}>
                            {busy ? <Loader2 size={15} className="animate-spin" /> : <PhoneCall size={15} />} Открыть
                        </button>
                    </div>
                </div>
            </section>
        </div>
    );
}

export default function ClientCardEmbed() {
    const { inPhone, token, asked, refresh } = useHostToken();
    const { toasts, push, dismiss } = useToastStack();
    const api = useMemo(() => createCardsApi(API_BASE_URL), []);
    const sessionKey = useMemo(() => sessionKeyOf(token), [token]);

    const [access, setAccess] = useState('checking');   // checking | ready | locked | disabled | no-session | error
    const [boot, setBoot] = useState(null);
    const [attempt, setAttempt] = useState(0);
    const [nonce, setNonce] = useState(0);
    const [current, setCurrent] = useState(null);        // { bundle, receivedAt, callKey, talkStartedAt }
    const [tezPending, setTezPending] = useState(false);
    const [tab, setTab] = useState('card');
    const [busy, setBusy] = useState(false);
    const [historyCount, setHistoryCount] = useState(null);
    const [focusRequest, setFocusRequest] = useState(null);
    const [nowMs, setNowMs] = useState(() => Date.now());

    const phase = !asked ? 'connecting' : !token ? 'no-session' : access;
    const tokenRef = useRef(token);
    tokenRef.current = token;
    const refreshRef = useRef(refresh);
    refreshRef.current = refresh;
    const currentRef = useRef(current);
    currentRef.current = current;
    const phaseRef = useRef(phase);
    phaseRef.current = phase;
    const queueRef = useRef([]);
    // Звонки этого сеанса: идентификатор звонка → его карточка. По нему событие, пришедшее
    // когда на экране уже другая карточка, находит свою.
    const callCardsRef = useRef(new Map());
    const toast = push;

    useEffect(() => {
        document.title = 'Карточка клиента';
        postToPhone({ type: 'ready' });
    }, []);

    /* Авторизация всех запросов страницы — один перехватчик axios, как у офисов: на 401 один
       раз спросить у телефона свежий токен и повторить. */
    useEffect(() => {
        const requestId = axios.interceptors.request.use((config) => {
            if (!isApiUrl(config?.url, API_BASE_URL)) return config;
            const value = tokenRef.current;
            if (!value) return config;
            config.__embedToken = value;
            if (config.headers && typeof config.headers.set === 'function') {
                config.headers.set('Authorization', `Bearer ${value}`);
                config.headers.set('X-Auth-Transport', 'bearer');
            } else {
                config.headers = { ...(config.headers || {}), Authorization: `Bearer ${value}`, 'X-Auth-Transport': 'bearer' };
            }
            return config;
        });
        const responseId = axios.interceptors.response.use(
            (response) => response,
            async (error) => {
                const config = error?.config;
                if (!config || !isApiUrl(config.url, API_BASE_URL)) return Promise.reject(error);
                if (error?.response?.status === 401 && !config.__embedRetried) {
                    config.__embedRetried = true;
                    const fresh = await refreshRef.current();
                    if (fresh && fresh !== config.__embedToken) {
                        tokenRef.current = fresh;
                        return axios(config);
                    }
                }
                const code = error?.response?.data?.code;
                // Замок QR вернулся посреди работы (сессию сменили) — на экран QR.
                if (code === 'SENSITIVE_ACCESS_REQUIRED' && phaseRef.current === 'ready') setAccess('locked');
                if (error?.response?.status === 401) setAccess('no-session');
                return Promise.reject(error);
            },
        );
        return () => {
            axios.interceptors.request.eject(requestId);
            axios.interceptors.response.eject(responseId);
        };
    }, []);

    const reload = useCallback(() => setNonce((value) => value + 1), []);

    /* Состояние страницы запрашивается из нескольких мест (конец звонка, сохранение, таймер).
       На экран идёт ответ только последнего запроса: ответ более раннего, пришедший позже,
       на миг вернул бы в «не заполнены» карточку, которую только что сохранили. */
    const bootSeqRef = useRef(0);
    const loadBoot = useCallback(async () => {
        bootSeqRef.current += 1;
        const seq = bootSeqRef.current;
        const { data } = await api.phoneState();
        if (!data.enabled) { setAccess('disabled'); return null; }
        if (seq === bootSeqRef.current) setBoot(data);
        return data;
    }, [api]);

    /* Что нужно странице: причины, адресаты, незаполненные карточки. Ответ заодно и проверка
       доступа: «нужен QR», «не включена», «сессии нет». */
    useEffect(() => {
        if (!sessionKey) return undefined;
        let cancelled = false;
        setAccess((value) => (value === 'ready' ? value : 'checking'));
        loadBoot()
            .then((data) => {
                if (cancelled || !data) return;
                setAttempt(0);
                setAccess('ready');
            })
            .catch((error) => {
                if (cancelled) return;
                const status = error?.response?.status;
                const code = error?.response?.data?.code;
                if (code === 'SENSITIVE_ACCESS_REQUIRED') setAccess('locked');
                else if (status === 403 || status === 404) setAccess('disabled');
                else if (status === 401) setAccess('no-session');
                else setAccess('error');
            });
        return () => { cancelled = true; };
    }, [sessionKey, nonce, loadBoot]);

    // Нет связи — повторяем сами, с растущей паузой.
    useEffect(() => {
        if (phase !== 'error') return undefined;
        const timer = setTimeout(() => { setAttempt((value) => value + 1); reload(); }, retryDelayMs(attempt));
        return () => clearTimeout(timer);
    }, [phase, attempt, reload]);

    const refreshPending = useCallback(() => {
        loadBoot().catch(() => {});
    }, [loadBoot]);

    /* Карточку сохранили или звонок не состоялся — из «не заполнены» она уходит сразу, не
       дожидаясь ответа сервера: иначе на экране ожидания на мгновение мелькала бы строка
       только что сохранённой карточки. */
    const dropPending = useCallback((id) => {
        setBoot((prev) => (prev ? { ...prev, pending: prev.pending.filter((item) => item.id !== id) } : prev));
    }, []);

    /* Счётчик ответов на запросы и список незаполненных карточек — раз в 45 секунд и при
       возврате в окно: колокола портала у страницы телефона нет. Спрятанное окно не опрашивает. */
    useEffect(() => {
        if (phase !== 'ready') return undefined;
        const tick = () => { if (document.visibilityState !== 'hidden') refreshPending(); };
        const timer = setInterval(tick, 45000);
        document.addEventListener('visibilitychange', tick);
        return () => {
            clearInterval(timer);
            document.removeEventListener('visibilitychange', tick);
        };
    }, [phase, refreshPending]);

    /* Ответ сервера по карточке — на экран. Чужой карточке (оператор уже на другой) не место. */
    const applyBundle = useCallback((data, extra = {}) => {
        setCurrent((prev) => {
            const same = Boolean(prev) && prev.bundle.card.id === data.card.id;
            /* Ответ не про карточку на экране — не применяем. В том числе когда экран уже пуст:
               запоздавший ответ TEZ APP по звонку, который успел оборваться, иначе вернул бы на
               экран закрытую карточку. Новую карточку ставит только открытие звонка (replace). */
            if (!same && !extra.replace) return prev;
            const tez = data.tez || (same ? prev.bundle.tez : undefined);
            return {
                callKey: extra.callKey !== undefined ? extra.callKey : (prev ? prev.callKey : null),
                talkStartedAt: extra.talkStartedAt !== undefined ? extra.talkStartedAt : (prev ? prev.talkStartedAt : null),
                // remount — причины пришли с сервера (продолжение обращения): карточку монтируем
                // заново, чтобы её черновик взял их, а не остался со своими.
                draftKey: ((same && prev.draftKey) || 0) + (extra.remount ? 1 : 0),
                receivedAt: Date.now(),
                bundle: { ...data, tez },
            };
        });
    }, []);

    /* Единственный привязанный аккаунт подставляется сам; несколько — выбирает оператор. */
    const autoPick = useCallback(async (data) => {
        const accountId = autoAccountId(data.card, data.accounts);
        if (!accountId) return data;
        try {
            const { data: picked } = await api.chooseAccount(data.card.id, accountId);
            applyBundle(picked);
            return picked;
        } catch {
            return data;
        }
    }, [api, applyBundle]);

    const openCall = useCallback(async ({ phone, direction, callKey, manual = false }) => {
        setBusy(true);
        // Карточка, которая сейчас на экране: новый звонок её сменит.
        const replaced = currentRef.current?.bundle?.card;
        try {
            const { data } = await api.openCall({ phone, direction, call_key: callKey || undefined });
            if (callKey) callCardsRef.current.set(callKey, data.card.id);
            setHistoryCount(null);
            setTab('card');
            applyBundle(data, { replace: true, callKey: callKey || null, talkStartedAt: null });
            // Оператор не успел её заполнить — она не пропала: говорим, где её искать.
            if (replaced && replaced.id !== data.card.id && replaced.status === 'open' && replaced.ended_at) {
                toast('Прошлая карточка не заполнена — она осталась в черновиках', 'info');
            }
            let latest = await autoPick(data);
            // Руками открытая карточка событий звонка не получит: считаем, что разговор идёт.
            if (manual && latest.card.state === 'ringing') {
                const { data: answered } = await api.callEvent(latest.card.id, 'answered');
                applyBundle(answered, { talkStartedAt: Date.now() });
                latest = answered;
            }
            // TEZ APP — отдельным заходом: карточка не ждёт чужой сервер.
            setTezPending(true);
            api.lookup(latest.card.id)
                .then(({ data: looked }) => { applyBundle(looked); return autoPick(looked); })
                .catch(() => {})
                .finally(() => setTezPending(false));
        } catch (error) {
            toast(errorText(error, 'Карточка не открылась'), 'error');
        } finally {
            setBusy(false);
        }
    }, [api, applyBundle, autoPick, toast]);

    const handleCall = useCallback(async (event) => {
        if (event.kind === 'start') {
            const now = currentRef.current;
            // Телефон повторяет идущий звонок, когда страница сказала «готова»: та же карточка.
            if (now && event.callKey && now.callKey === event.callKey) return;
            /* Второй звонок посреди разговора: карточку разговора с экрана не снимаем — её
               заполняют прямо сейчас. Карточка второго звонка заводится и ждёт в «не заполнены». */
            if (now && now.bundle.card.state === 'talking') {
                try {
                    const { data } = await api.openCall({ phone: event.phone, direction: event.direction, call_key: event.callKey || undefined });
                    if (event.callKey) callCardsRef.current.set(event.callKey, data.card.id);
                    toast(`Второй звонок: ${data.card.phone_text}. Его карточка — в «не заполнены».`, 'info');
                    refreshPending();
                } catch (error) {
                    toast(errorText(error, 'Карточка второго звонка не открылась'), 'error');
                }
                return;
            }
            await openCall({ phone: event.phone, direction: event.direction, callKey: event.callKey });
            return;
        }
        const now = currentRef.current;
        if (!now || !eventIsForCall(event, now.callKey)) {
            /* Событие не про карточку на экране (оператор уже на другой): доносим его до своей
               карточки молча — иначе она навсегда осталась бы «в звонке». */
            const otherId = event.callKey ? callCardsRef.current.get(event.callKey) : null;
            if (otherId) {
                try {
                    await api.callEvent(otherId, event.kind);
                    if (event.kind === 'ended') postToPhone({ type: 'card:done', call_key: event.callKey });
                    refreshPending();
                } catch {
                    // Карточка прошлого звонка: её состояние поправит следующий опрос.
                }
            }
            return;
        }
        try {
            const { data } = await api.callEvent(now.bundle.card.id, event.kind);
            if (event.kind === 'answered') {
                applyBundle(data, { talkStartedAt: Date.now() });
                return;
            }
            applyBundle(data);
            if (data.card.status === 'void' || data.card.status === 'done') {
                // Звонок не состоялся или карточку сохранили ещё в разговоре: постобработка
                // не нужна, карточка с экрана уходит.
                postToPhone({ type: 'card:done', call_key: now.callKey || '' });
                dropPending(data.card.id);
                setCurrent(null);
            } else {
                postToPhone({ type: 'card:wrapup', call_key: now.callKey || '', seconds: data.card.wrapup_left_sec || 0 });
            }
            refreshPending();
        } catch (error) {
            toast(errorText(error), 'error');
        }
    }, [api, applyBundle, dropPending, openCall, refreshPending, toast]);
    const handleCallRef = useRef(handleCall);
    handleCallRef.current = handleCall;

    /* События звонка от телефона. Пока страница не готова (вход, QR), запоминаем последнее
       начало звонка и отдаём, когда откроется. */
    useEffect(() => {
        const onMessage = (event) => {
            const call = parseCallMessage(event?.data);
            if (!call) return;
            if (phaseRef.current === 'ready') handleCallRef.current(call);
            else if (call.kind === 'start') queueRef.current = [call];
            else queueRef.current.push(call);
        };
        const bridge = inPhone ? window.chrome.webview : null;
        if (bridge) bridge.addEventListener('message', onMessage);
        // В браузере те же сообщения принимаются через window.postMessage — для стенда.
        const onWindowMessage = (event) => { if (event.source === window) onMessage(event); };
        window.addEventListener('message', onWindowMessage);
        return () => {
            if (bridge) bridge.removeEventListener('message', onMessage);
            window.removeEventListener('message', onWindowMessage);
        };
    }, [inPhone]);

    useEffect(() => {
        if (phase !== 'ready') return;
        postToPhone({ type: 'card:ready' });
        const queued = queueRef.current;
        queueRef.current = [];
        (async () => { for (const call of queued) await handleCallRef.current(call); })();
        // Без телефона номер можно передать адресом: client_card.html?phone=…&direction=out.
        if (!inPhone && !queued.length && !currentRef.current) {
            const params = new URLSearchParams(window.location.search);
            const phone = params.get('phone');
            if (phone) openCall({ phone, direction: params.get('direction') === 'out' ? 'out' : 'in', manual: true });
        }
        // Только на переход в «готова».
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [phase]);

    // Секундный тик — только пока есть что отсчитывать.
    const card = current?.bundle.card || null;
    const ticking = card && (card.state === 'talking' || card.state === 'wrapup');
    useEffect(() => {
        if (!ticking) return undefined;
        setNowMs(Date.now());
        const timer = setInterval(() => setNowMs(Date.now()), 1000);
        return () => clearInterval(timer);
    }, [ticking, card?.id]);

    // Постобработка вышла — перечитать карточку: она стала «не заполнена».
    const left = current ? wrapupLeft(card, current.receivedAt, nowMs) : null;
    const cardId = card?.id;
    useEffect(() => {
        if (left !== 0 || !cardId) return;
        api.cardBundle(cardId).then(({ data }) => applyBundle(data)).catch(() => {});
        refreshPending();
    }, [left, cardId, api, applyBundle, refreshPending]);

    const onCompleted = useCallback((data) => {
        toast('Карточка сохранена', 'success');
        postToPhone({ type: 'card:done', call_key: currentRef.current?.callKey || '' });
        dropPending(data.card.id);
        refreshPending();
        // Звонок уже кончился — карточка больше не нужна на экране.
        if (data.card.ended_at) setCurrent(null);
    }, [dropPending, refreshPending, toast]);

    const openPending = useCallback(async (id) => {
        setBusy(true);
        try {
            const { data } = await api.cardBundle(id);
            setHistoryCount(null);
            setTab('card');
            applyBundle(data, { replace: true, callKey: null, talkStartedAt: null });
        } catch (error) {
            toast(errorText(error, 'Карточка не открылась'), 'error');
        } finally {
            setBusy(false);
        }
    }, [api, applyBundle, toast]);

    const closeCard = useCallback(() => {
        setCurrent(null);
        refreshPending();
    }, [refreshPending]);

    const openExternal = useCallback((url) => {
        if (!url) return;
        if (!postToPhone({ type: 'open:external', url })) window.open(url, '_blank', 'noopener');
    }, []);

    /* «Продолжить это обращение»: связать карточку звонка с прошлой и взять её причины,
       если свои ещё не выбраны. */
    const continueCard = useCallback(async (picked) => {
        const now = currentRef.current;
        if (!now) return;
        const body = { parent_id: picked.id };
        // Причины прошлого обращения подходят, только если они из ветки нынешнего аккаунта
        // (или «Третьего лица»): у водителей и клиентов они свои, а с одного номера звонят
        // и те и другие.
        const kind = now.bundle.account?.kind;
        const inherited = kind ? topicsOfAudience(picked.topics, boot?.categories, kind) : picked.topics;
        if (!now.bundle.card.topics.length && inherited.length) body.topics = topicsPayload(inherited);
        try {
            const { data } = await api.saveCard(now.bundle.card.id, body);
            applyBundle(data, { remount: true });
            setTab('card');
            toast(`Связано с обращением №${picked.id}`, 'success');
        } catch (error) {
            toast(errorText(error), 'error');
        }
    }, [api, applyBundle, boot?.categories, toast]);

    const copyPhone = useCallback(() => {
        if (!card) return;
        copyText(card.phone_text).then((ok) => toast(ok ? 'Номер скопирован' : 'Не получилось скопировать', ok ? 'success' : 'error'));
    }, [card, toast]);

    if (phase === 'locked') {
        return (
            <div className="min-h-screen bg-slate-50 px-3 pb-8 pt-4">
                <div className="mx-auto w-full max-w-lg">
                    <QrUnlockPanel key={sessionKey} apiBase={API_BASE_URL} inPhone={inPhone}
                                   title="Карточка клиента откроется после подтверждения"
                                   onGranted={() => { setAccess('checking'); reload(); }} />
                </div>
                <ToastStack toasts={toasts} onDismiss={dismiss} />
            </div>
        );
    }
    if (phase !== 'ready' || !boot) {
        const hint = phase === 'error' ? `Повторим через ${Math.round(retryDelayMs(attempt) / 1000)} с.` : '';
        const retry = phase === 'error' ? () => { setAttempt(0); reload(); }
            : phase === 'no-session' ? () => { refresh().then(reload); } : null;
        return <div className="min-h-screen bg-slate-50"><Notice phase={boot ? phase : (phase === 'ready' ? 'checking' : phase)} hint={hint} onRetry={retry} /></div>;
    }

    const tabs = [
        { value: 'card', label: 'Карточка' },
        { value: 'history', label: 'Обращения', count: historyCount || undefined },
        { value: 'requests', label: 'Запросы', count: boot.unread_requests || undefined },
    ];

    return (
        <div className="flex h-screen min-h-0 flex-col bg-slate-50 text-slate-900">
            <header className="shrink-0 border-b border-slate-200/70 bg-white">
                {card && (
                    <CallStrip card={card} talkStartedAt={current.talkStartedAt} receivedAt={current.receivedAt}
                               nowMs={nowMs} onClose={closeCard} onCopy={copyPhone} />
                )}
                <div className="px-3 pb-2.5 pt-2">
                    <IosSegmented value={tab} options={tabs} onChange={setTab} stretch slide ariaLabel="Разделы карточки" />
                </div>
            </header>

            <main className={`min-h-0 flex-1 ${tab === 'requests' ? '' : 'overflow-y-auto'}`}>
                {tab === 'card' && (
                    <div className="mx-auto w-full max-w-2xl px-4 pt-4">
                        {current ? (
                            <CallCard key={`${card.id}:${current.draftKey || 0}`} api={api} bundle={current.bundle} boot={boot}
                                      toast={toast} onBundle={applyBundle} onCompleted={onCompleted}
                                      onOpenExternal={openExternal} tezPending={tezPending} />
                        ) : (
                            <IdleScreen boot={boot} busy={busy} onOpenPending={openPending}
                                        onManual={(phone, direction) => openCall({ phone, direction, manual: true })} />
                        )}
                    </div>
                )}
                {tab === 'history' && (
                    <div className="mx-auto w-full max-w-2xl px-4 py-4">
                        <HistoryTab api={api} card={card} toast={toast} onContinue={continueCard}
                                    onCount={(count, more) => setHistoryCount(more ? count + 1 : count)}
                                    onOpenRequest={(id) => { setFocusRequest(id); setTab('requests'); }} />
                    </div>
                )}
                {tab === 'requests' && (
                    <div className="mx-auto h-full w-full max-w-2xl bg-white">
                        <RequestsPanel api={api} toast={toast} limits={boot.limits} compact
                                       focusRequestId={focusRequest} onFocusHandled={() => setFocusRequest(null)}
                                       onUnread={(count) => setBoot((prev) => (prev && prev.unread_requests !== count
                                           ? { ...prev, unread_requests: count } : prev))} />
                    </div>
                )}
            </main>
            <ToastStack toasts={toasts} onDismiss={dismiss} />
        </div>
    );
}
