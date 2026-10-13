/* Страница «Адреса офисов» внутри iCORE Phone (offices.html).
 *
 * Это раздел «Офисы» вики целиком — тот же компонент WikiOffices, что в портале:
 * статус офисов на дату, поиск, фильтры, карточка офиса с картой, телефонами и
 * часами. Своего здесь только то, чего у встроенной страницы нет от портала:
 * вход, QR-подтверждение, выбор пространства, тосты и судьба ссылок.
 *
 * Вход. Токен даёт телефон по мосту WebView2 (src/embed/useHostToken.js); в обычном
 * браузере — токен портала из localStorage того же origin. Подставляет его ОДИН
 * перехватчик axios: WikiOffices получает неизменный объект заголовков и не
 * перезагружает список при каждом обновлении токена (раз в ~28 минут).
 *
 * Доступ. Вики у операторов закрыта QR-гейтом (wiki/routes.py), и решение владельца —
 * оставить его и здесь: страница сама показывает QR и резервный код, супервайзер
 * своего отдела подтверждает (src/embed/QrUnlockPanel.jsx). Подтверждение живёт на
 * сессии телефона до выхода из него — и заодно включает запись попыток тренажёров.
 *
 * Только чтение. Править справочник из телефона нельзя даже тому, у кого есть
 * право в портале (WikiOffices readOnly): вкладка телефона — рабочее место на
 * линии, а не место ведения справочника.
 *
 * Свежесть. Вкладка живёт всю смену: телефон её только прячет и показывает, а не
 * перезагружает. Поэтому список обновляется сам (useListRefresh): раз в пять минут,
 * при возврате во вкладку и с наступлением новых суток; сбой загрузки — карточка
 * «Не удалось загрузить офисы» с повтором, а не «Справочник ещё не заполнен».
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Loader2, LogIn, MapPinOff, RefreshCw, WifiOff } from 'lucide-react';

import { iosBtnSecondary, iosCard, iosInput } from '../components/ui/ios';
import WikiOffices from '../components/wiki/WikiOffices';
import { officeTodayISO } from '../components/wiki/officeSchedule';
import { postToPhone } from '../trainers_embed/phoneBridge';
import useHostToken from '../embed/useHostToken';
import QrUnlockPanel from '../embed/QrUnlockPanel';
import copyText from '../embed/copyText';
import { QR_POLL_MS, accessStateFromError, isApiUrl, sessionKeyOf } from '../embed/embedAccess';
import {
    linkAction, noticeFor, officeSpaces, phoneCopiedText, phoneToCopy, pickOfficesSpace,
    refreshDue, retryDelayMs, toastText,
} from './officesPage';
import { ToastStack, useToastStack } from './ToastStack';

// Строку держать дословно: её переписывает конфиг локального стенда
// (vite.stand.config.mjs), как и в TrainersEmbed.jsx и App.jsx.
const API_BASE_URL = 'https://otp-2-fos4.onrender.com';
const WIKI_BASE = `${API_BASE_URL}/api/wiki`;
// Выбранное пространство — своё, а портальное ('wiki:space') только читаем: страница
// в браузере не должна переключать человеку вики в портале.
const SPACE_STORAGE_KEY = 'icore-phone.offices.space';
const PORTAL_SPACE_STORAGE_KEY = 'wiki:space';
/* Заголовки для WikiOffices — один и тот же объект всю жизнь страницы. Авторизацию
   подставляет перехватчик axios; новый объект на каждый токен WikiOffices принял бы
   за смену запроса и перезагрузил бы список. */
const NO_HEADERS = {};
// После «подтвердили» сервер всё ещё отвечает «нужен QR» — признак расхождения
// (ручка статуса и гейт вики спорят). Такой повтор не должен крутиться вхолостую.
const GRANT_MISMATCH_WINDOW_MS = 15000;
// Как часто переспрашивать телефон, когда сессия на сервере закончилась.
const SESSION_RETRY_MS = 30000;
// Как часто смотреть, не пора ли обновить список (сами сроки — LIST_REFRESH_MS и
// соседи в officesPage.js). Раз в минуту: после полуночи «вчера» не висит на экране
// дольше минуты.
const LIST_CHECK_MS = 60000;

const readStoredSpace = () => {
    try {
        return Number(window.localStorage.getItem(SPACE_STORAGE_KEY))
            || Number(window.localStorage.getItem(PORTAL_SPACE_STORAGE_KEY))
            || null;
    } catch {
        return null;
    }
};

const storeSpace = (id) => {
    try {
        window.localStorage.setItem(SPACE_STORAGE_KEY, String(id));
    } catch {
        // Приватный режим — живём без запомненного выбора.
    }
};

const NOTICE_ICONS = { session: LogIn, denied: MapPinOff, error: WifiOff };

function Notice({ notice, hint = '', onAction = null }) {
    if (!notice) return null;
    if (notice.kind === 'wait') {
        return (
            <div className="mx-auto w-full max-w-md pt-6">
                <div className={`${iosCard} flex items-center justify-center gap-3 px-5 py-6 text-[13.5px] text-slate-500`}>
                    <Loader2 size={16} className="animate-spin text-slate-400" aria-hidden="true" />
                    <span>{notice.title}</span>
                </div>
            </div>
        );
    }
    const Icon = NOTICE_ICONS[notice.kind] || MapPinOff;
    return (
        <div className="mx-auto w-full max-w-md pt-6">
            <div className={`${iosCard} flex flex-col items-center px-5 py-6 text-center`}>
                <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-500">
                    <Icon size={22} aria-hidden="true" />
                </div>
                <h1 className="mt-3 text-[16px] font-semibold leading-snug text-slate-900">{notice.title}</h1>
                {notice.text && (
                    <p className="mt-1.5 text-[13px] leading-relaxed text-slate-500">{notice.text}</p>
                )}
                {hint && <p className="mt-1 text-[12px] text-slate-400">{hint}</p>}
                {notice.action && onAction && (
                    <button type="button" onClick={onAction} className={`${iosBtnSecondary} mt-4`}>
                        <RefreshCw size={14} aria-hidden="true" /> {notice.action}
                    </button>
                )}
            </div>
        </div>
    );
}

/* Пространств со справочником бывает несколько (Таксопарки и Тез — разные вики).
   Простой выпадающий список, а не переключатель вики: тот рассчитан на шапку
   портала (меню 304 px) и во вкладку шириной 243 px не помещается. */
function SpaceSelect({ spaces, value, onChange }) {
    return (
        <label className="mb-3 flex items-center gap-2">
            <span className="shrink-0 px-1 text-[12.5px] font-medium text-slate-500">Вики</span>
            <select
                value={value ?? ''}
                onChange={(event) => onChange(Number(event.target.value))}
                className={`${iosInput} py-2`}
                aria-label="Пространство вики"
            >
                {spaces.map((space) => (
                    <option key={space.id} value={space.id}>{space.name || `Пространство ${space.id}`}</option>
                ))}
            </select>
        </label>
    );
}

/* Фоновое обновление списка офисов (когда пора — refreshDue в officesPage.js).
 *
 * Вкладка телефона не перезагружается всю смену, а WikiOffices сам грузит список
 * только при открытии и при смене поиска, фильтров или даты. Здесь решается, когда
 * список устарел; разделу отдаётся:
 *   reloadKey    — его смена и есть просьба «обнови тихо» (список остаётся на
 *                  экране, пока идёт запрос);
 *   onLoadResult — чем кончилась очередная загрузка: от неё идёт и отсчёт
 *                  свежести, и повторы после сбоя;
 *   errorHint    — подпись под карточкой ошибки: повторим сами и когда.
 * Сбой загрузки повторяем с растущей паузой, как и проверку доступа. Спрятанная
 * вкладка запросов не шлёт: ждёт, пока её откроют, и тогда обновляет сразу. */
function useListRefresh(active) {
    const [reloadKey, setReloadKey] = useState(0);
    const [failures, setFailures] = useState(0);
    // В ref: отметки читают таймер и слушатель видимости, перерисовка им не нужна.
    const stamp = useRef({ okAt: Date.now(), askedAt: 0, day: officeTodayISO() });

    const ask = useCallback(() => {
        stamp.current.askedAt = Date.now();
        setReloadKey((n) => n + 1);
    }, []);

    const onLoadResult = useCallback(({ ok }) => {
        stamp.current.askedAt = 0;
        if (ok) {
            stamp.current.okAt = Date.now();
            stamp.current.day = officeTodayISO();
            setFailures(0);
        } else {
            setFailures((n) => n + 1);
        }
    }, []);

    // Раздел только что открылся (вход, QR, возврат после отказа) — отсчёт с нуля:
    // первую загрузку он делает сам.
    useEffect(() => {
        if (!active) return;
        stamp.current = { okAt: Date.now(), askedAt: 0, day: officeTodayISO() };
        setFailures(0);
    }, [active]);

    // Список загружен: раз в минуту и при возврате во вкладку смотрим, не устарел ли.
    useEffect(() => {
        if (!active || failures) return undefined;
        const check = (returning) => {
            if (document.visibilityState === 'hidden') return;
            if (refreshDue({
                ...stamp.current, now: Date.now(), today: officeTodayISO(), returning,
            })) ask();
        };
        const timer = setInterval(() => check(false), LIST_CHECK_MS);
        const onVisible = () => check(true);
        document.addEventListener('visibilitychange', onVisible);
        return () => {
            clearInterval(timer);
            document.removeEventListener('visibilitychange', onVisible);
        };
    }, [active, failures, ask]);

    // Загрузка не удалась: повторяем сами, а вернувшись во вкладку — сразу.
    useEffect(() => {
        if (!active || !failures) return undefined;
        const timer = setTimeout(() => {
            if (document.visibilityState !== 'hidden') ask();
        }, retryDelayMs(failures - 1));
        const onVisible = () => {
            if (document.visibilityState === 'visible') ask();
        };
        document.addEventListener('visibilitychange', onVisible);
        return () => {
            clearTimeout(timer);
            document.removeEventListener('visibilitychange', onVisible);
        };
    }, [active, failures, ask]);

    return {
        reloadKey,
        onLoadResult,
        errorHint: failures
            ? `Повторим сами через ${Math.round(retryDelayMs(failures - 1) / 1000)} с.`
            : '',
    };
}

export default function OfficesEmbed() {
    const { inPhone, token, error: tokenError, asked, refresh } = useHostToken();
    const { toasts, push, dismiss } = useToastStack();
    /* Состояние доступа, когда токен уже есть: checking → ready | locked | denied |
       no-session | error. Пока токена нет, фазу задаёт сам хук токена (ниже). */
    const [access, setAccess] = useState({ phase: 'checking' });
    const [checkNonce, setCheckNonce] = useState(0);
    const [attempt, setAttempt] = useState(0);         // автоповторы после сбоя сети
    const [grantMisses, setGrantMisses] = useState(0); // «подтвердили», а гейт против

    const sessionKey = useMemo(() => sessionKeyOf(token), [token]);
    const phase = !asked ? 'connecting' : !token ? 'no-session' : access.phase;
    const listRefresh = useListRefresh(phase === 'ready');

    /* Перехватчики axios вешаются один раз и читают всё нужное из ref. phaseRef
       ставится ещё и синхронно в goTo: ошибка запроса доходит до WikiOffices уже
       ПОСЛЕ перехватчика, и её тост должен знать, что страница ушла на экран QR. */
    const tokenRef = useRef(token);
    tokenRef.current = token;
    const refreshRef = useRef(refresh);
    refreshRef.current = refresh;
    const phaseRef = useRef(phase);
    phaseRef.current = phase;
    const grantedAtRef = useRef(0);

    useEffect(() => {
        document.title = 'Адреса офисов';
        postToPhone({ type: 'ready' });
    }, []);

    const recheck = useCallback(() => setCheckNonce((n) => n + 1), []);

    const goTo = useCallback((next) => {
        phaseRef.current = next.phase;
        setAccess(next);
    }, []);

    const applyVerdict = useCallback((verdict) => {
        switch (verdict.state) {
            case 'locked': goTo({ phase: 'locked' }); break;
            case 'denied': goTo({ phase: 'denied', reason: verdict.reason, message: verdict.message }); break;
            case 'no-session': goTo({ phase: 'no-session', reason: 'expired' }); break;
            case 'respace': recheck(); break;
            default: goTo({ phase: 'error', reason: verdict.reason, message: verdict.message });
        }
    }, [goTo, recheck]);
    const applyRef = useRef(applyVerdict);
    applyRef.current = applyVerdict;

    /* Авторизация для всех запросов страницы к API — один перехватчик на экземпляр
       axios по умолчанию (WikiOffices ходит именно им). На 401 — один раз спросить
       у телефона свежий токен и повторить; не помогло — сессии нет. Отказы гейта
       посреди работы (подтверждение сняли, сменилась сессия, пропало пространство)
       переводят страницу на нужный экран, а не оставляют пустой список с тостом. */
    useEffect(() => {
        const requestId = axios.interceptors.request.use((config) => {
            if (!isApiUrl(config?.url, API_BASE_URL)) return config;
            const current = tokenRef.current;
            if (!current) return config;
            config.__embedToken = current;
            if (config.headers && typeof config.headers.set === 'function') {
                config.headers.set('Authorization', `Bearer ${current}`);
                config.headers.set('X-Auth-Transport', 'bearer');
            } else {
                config.headers = { ...(config.headers || {}), Authorization: `Bearer ${current}`, 'X-Auth-Transport': 'bearer' };
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
                    // Тот же токен повторять бессмысленно — ответ будет тем же 401.
                    if (fresh && fresh !== config.__embedToken) {
                        tokenRef.current = fresh;
                        return axios(config);
                    }
                }
                const verdict = accessStateFromError(error);
                if (verdict.state === 'no-session') applyRef.current(verdict);
                else if (phaseRef.current === 'ready' && verdict.state !== 'error') applyRef.current(verdict);
                return Promise.reject(error);
            },
        );
        return () => {
            axios.interceptors.request.eject(requestId);
            axios.interceptors.response.eject(responseId);
        };
    }, []);

    /* Проверка доступа — /api/wiki/ping, и сразу им, без предварительного
       /api/sensitive-access/status. Ping проходит тот же гейт, что и справочник, и
       отвечает точно: «нужен QR» (403 SENSITIVE_ACCESS_REQUIRED) или список
       пространств. Ручка статуса отвечала бы «нужен QR» и тем, кого вики пускает
       без него: стажёрам (их QR спрашивают только «Чаты водителей») и главам
       отделов, — и таким людям страница зря показывала бы замок. Статус нужен ниже,
       в экране QR: там он дешёвый опрос «подтвердили ли уже». */
    useEffect(() => {
        if (!sessionKey) return undefined;
        let cancelled = false;
        goTo({ phase: 'checking' });
        axios.get(`${WIKI_BASE}/ping`)
            .then(({ data }) => {
                if (cancelled) return;
                const spaces = officeSpaces(data?.spaces);
                const space = pickOfficesSpace(spaces, readStoredSpace());
                if (!space) {
                    goTo({ phase: 'denied', reason: 'no-offices' });
                    return;
                }
                setAttempt(0);
                setGrantMisses(0);
                goTo({ phase: 'ready', spaces, spaceId: space.id });
            })
            .catch((err) => {
                if (cancelled) return;
                const verdict = accessStateFromError(err);
                if (verdict.state === 'locked' && Date.now() - grantedAtRef.current < GRANT_MISMATCH_WINDOW_MS) {
                    setGrantMisses((n) => n + 1);
                }
                // У ping нет пространства, и «выбрать заново» от него не приходит;
                // если всё же пришло — это сбой, а не повод перепроверять по кругу.
                applyRef.current(verdict.state === 'respace' ? { ...verdict, state: 'error' } : verdict);
            });
        return () => { cancelled = true; };
    }, [sessionKey, checkNonce, goTo]);

    /* Сессия на сервере закончилась, а токен у страницы прежний: переспрашиваем
       телефон раз в полминуты (сам он новый токен не пришлёт), и любой новый токен —
       даже той же сессии (часы компьютера отстали, и токен истёк раньше, чем
       казалось) — повод проверить доступ заново. Новую сессию проверяет эффект
       выше: он зависит от sessionKey, и второй запрос здесь был бы лишним. */
    const lastTokenRef = useRef({ token, sessionKey });
    useEffect(() => {
        const prev = lastTokenRef.current;
        lastTokenRef.current = { token, sessionKey };
        if (token && token !== prev.token && sessionKey === prev.sessionKey
            && access.phase === 'no-session') recheck();
    }, [token, sessionKey, access.phase, recheck]);
    useEffect(() => {
        if (phase !== 'no-session' || !token) return undefined;
        const timer = setInterval(() => { refresh(); }, SESSION_RETRY_MS);
        return () => clearInterval(timer);
    }, [phase, token, refresh]);

    /* Сбой сети — повторяем сами, с растущей паузой; спрятанная вкладка ждёт, пока
       её откроют, и тогда проверяет сразу. */
    useEffect(() => {
        if (phase !== 'error') return undefined;
        const again = () => {
            setAttempt((n) => n + 1);
            recheck();
        };
        const timer = setTimeout(() => {
            if (document.visibilityState !== 'hidden') again();
        }, retryDelayMs(attempt));
        const onVisible = () => {
            if (document.visibilityState === 'visible') again();
        };
        document.addEventListener('visibilitychange', onVisible);
        return () => {
            clearTimeout(timer);
            document.removeEventListener('visibilitychange', onVisible);
        };
    }, [phase, attempt, recheck]);

    const onGranted = useCallback(() => {
        grantedAtRef.current = Date.now();
        recheck();
    }, [recheck]);

    const chooseSpace = useCallback((id) => {
        storeSpace(id);
        setAccess((prev) => (prev.phase === 'ready' ? { ...prev, spaceId: id } : prev));
    }, []);

    /* Тосты WikiOffices. Ошибку запроса, после которой страница уже ушла на экран
       QR или отказа, второй раз тостом не повторяем: экран сам говорит, что
       случилось. То же с ошибкой загрузки списка (meta.inline): раздел показывает
       её на месте — карточкой «Не удалось загрузить офисы» или плашкой над
       списком, — а тост внизу вкладки высотой 285 px лёг бы прямо на них.
       Английский текст axios («Network Error») — по-русски: toastText. */
    const toast = useCallback((message, type, meta) => {
        if (type === 'error' && (phaseRef.current !== 'ready' || meta?.inline)) return;
        push(type === 'error' ? toastText(message) : message, type);
    }, [push]);

    /* Ссылки — до React, в фазе захвата: tel: копируем, внешние в телефоне отдаём
       телефону (подробности — linkAction). Не вышло отдать — ссылка срабатывает как
       обычно: «новое окно» телефон и так открывает в системном браузере.
       Копируем номер, который человек ВИДИТ в ссылке, а не её href: в href портал
       склеивает несколько номеров и добавочный в один (phoneToCopy). */
    useEffect(() => {
        const onClick = (event) => {
            if (event.defaultPrevented || event.button !== 0) return;
            const anchor = event.target?.closest?.('a[href]');
            if (!anchor) return;
            const action = linkAction(anchor.href, anchor.getAttribute('target'), inPhone, window.location.origin);
            if (action === 'copy-tel') {
                event.preventDefault();
                const phone = phoneToCopy(anchor.getAttribute('href'), anchor.textContent);
                if (!phone) return;
                copyText(phone.value).then((ok) => push(phoneCopiedText(phone, ok), ok ? 'success' : 'error'));
            } else if (action === 'external' && postToPhone({ type: 'open:external', url: anchor.href })) {
                event.preventDefault();
            }
        };
        document.addEventListener('click', onClick, true);
        return () => document.removeEventListener('click', onClick, true);
    }, [inPhone, push]);

    let body;
    if (phase === 'ready') {
        body = (
            <>
                {access.spaces.length > 1 && (
                    <SpaceSelect spaces={access.spaces} value={access.spaceId} onChange={chooseSpace} />
                )}
                {/* key: фильтры города и парка принадлежат пространству — в соседней
                    вики они ничего бы не нашли. */}
                <WikiOffices
                    key={access.spaceId}
                    base={WIKI_BASE}
                    headers={NO_HEADERS}
                    showToast={toast}
                    spaceId={access.spaceId}
                    readOnly
                    reloadKey={listRefresh.reloadKey}
                    onLoadResult={listRefresh.onLoadResult}
                    errorHint={listRefresh.errorHint}
                />
            </>
        );
    } else if (phase === 'locked') {
        body = (
            <div className="mx-auto w-full max-w-lg">
                <QrUnlockPanel
                    key={sessionKey}
                    apiBase={API_BASE_URL}
                    onGranted={onGranted}
                    title="Офисы откроются после подтверждения"
                    inPhone={inPhone}
                    pollMs={QR_POLL_MS * (2 ** Math.min(grantMisses, 4))}
                />
            </div>
        );
    } else {
        const details = access.phase === phase ? access : {};
        const notice = noticeFor(phase, {
            inPhone, reason: details.reason, message: details.message, tokenError,
        });
        const onAction = phase === 'error'
            ? () => { setAttempt(0); recheck(); }
            : phase === 'no-session'
                ? () => { refresh().then(() => recheck()); }
                : recheck;
        body = (
            <Notice
                notice={notice}
                hint={phase === 'error' ? `Повторим сами через ${Math.round(retryDelayMs(attempt) / 1000)} с.` : ''}
                onAction={phase === 'connecting' || phase === 'checking' ? null : onAction}
            />
        );
    }

    /* Фон — сплошной, как у вики в портале (WikiView: wiki-scope … bg-slate-50): без
       него сквозь полупрозрачные плашки раздела просвечивал логотип с body из
       styles.css.
       offices-embed — метка для стилей страницы (offices-embed.css): узкой вкладке
       нужна сетка карточки офиса, а мобильный слой вики, где она живёт, включается
       только классом оболочки портала. */
    return (
        <div className="wiki-scope offices-embed min-h-screen bg-slate-50">
            <div className="mx-auto w-full max-w-5xl px-3 pb-8 pt-3 sm:px-4">
                {body}
            </div>
            <ToastStack toasts={toasts} onDismiss={dismiss} />
        </div>
    );
}
