/* Токен для встроенных страниц iCORE Phone — тренажёров и адресов офисов.
 *
 * Внутри WebView2 телефона токен приходит по мосту (trainers_embed/phoneBridge.js):
 * страница просит его при старте и заново, когда срок на исходе; refresh-токена у
 * неё нет вовсе. В обычном браузере берём access-токен портала из localStorage того
 * же origin (github.io) — так страницу можно открыть и без телефона.
 *
 * Хук жил в TrainersEmbed.jsx и вынесен, когда появилась вторая страница: две копии
 * разъехались бы на первой же правке моста.
 *
 * refresh() возвращает промис со свежим токеном ('' — токена нет). Тренажёрам ответ
 * не нужен, офисам — да: на ответ 401 они один раз спрашивают телефон и повторяют
 * запрос. Вызов, пришедший, пока идёт прежний, получает тот же промис: второй запрос
 * токена посреди первого телефону ни к чему.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    hasPhoneHost, isTokenFresh, parseTokenMessage, requestPhoneToken,
} from '../trainers_embed/phoneBridge';

export const ACCESS_TOKEN_STORAGE_KEY = 'otp_access_token';
// Как часто проверять срок токена. Access живёт 30 минут; проверка раз в полминуты
// успевает попросить свежий за две минуты до истечения (isTokenFresh).
const TOKEN_CHECK_MS = 30000;

/** Access-токен портала, открытого в этом же браузере (тот же origin). */
export const readPortalToken = () => {
    try {
        return String(window.localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY) || '').trim();
    } catch {
        return '';
    }
};

/* Токен страницы: от телефона (внутри WebView2) или из портала (в браузере).
   Возвращает { inPhone, token, error, asked, refresh }. */
export default function useHostToken() {
    const inPhone = useMemo(() => hasPhoneHost(), []);
    const [auth, setAuth] = useState({ token: '', error: '', asked: false });
    const authRef = useRef(auth);
    authRef.current = auth;
    const pendingRef = useRef(null);

    const refresh = useCallback(() => {
        if (pendingRef.current) return pendingRef.current;
        const run = (async () => {
            if (inPhone) {
                const reply = await requestPhoneToken();
                if (reply) {
                    setAuth({ token: reply.token, error: reply.token ? '' : (reply.error || 'Телефон не отдал токен'), asked: true });
                    return reply.token;
                }
                setAuth((prev) => ({ ...prev, asked: true, error: prev.token ? '' : 'Телефон не ответил' }));
                return authRef.current.token;
            }
            const token = readPortalToken();
            const fresh = isTokenFresh(token, 0) ? token : '';
            setAuth({ token: fresh, error: '', asked: true });
            return fresh;
        })();
        pendingRef.current = run;
        // Снимаем ПОСЛЕ завершения, а не в finally внутри: вне телефона тело
        // отрабатывает синхронно, и finally успел бы раньше присваивания выше —
        // в ref навсегда остался бы исполненный промис, и токен больше не читался бы.
        const clear = () => { if (pendingRef.current === run) pendingRef.current = null; };
        run.then(clear, clear);
        return run;
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

    return { inPhone, token: auth.token, error: auth.error, asked: auth.asked, refresh };
}
