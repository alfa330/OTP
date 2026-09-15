/**
 * Обновление сессии — одно на вкладку и одинаково понятое fetch- и axios-перехватчиком.
 *
 * Токены портала живут в браузере: фронт на GitHub Pages, API на Render, домены разные,
 * и авторизация идёт заголовком (App.jsx: shouldForceBearerAuthTransport). Access-токен
 * живёт 30 минут, потом первый же запрос получает 401, и вкладка обновляет сессию.
 *
 * Раньше обновление заводили ДВА перехватчика, каждый в общий window.__otpRefreshPromise:
 * fetch клал туда промис Response, axios — промис своего ответа. Ждали его оба, а
 * проверяли по-своему: fetch-ветка смотрела на `response.ok`, которого у ответа axios
 * нет. Колокол держит поток уведомлений fetch'ем и при возврате во вкладку переподключает
 * его через 400 мс после того, как axios уже пошёл обновлять токен, — и УДАЧНОЕ
 * обновление читалось как провал: forceReloadAfterFailedAuthRefresh стирал только что
 * полученные токены и выкидывал на вход.
 *
 * В Chrome этого не видно: сервер при каждом обновлении ставит ещё и куки, Chrome
 * отправляет их на onrender.com со страницы github.io, и запрос проходит по куке мимо
 * заголовка. Яндекс Браузер сторонних кук не отправляет вовсе — у него есть только
 * токены, и он вылетал. Воспроизведено на боевом портале 15.09.2026.
 *
 * Вторая половина той же беды: на вход выкидывал любой сбой самого обновления — обрыв
 * сети, 502 прокси Render во время деплоя. О сессии такой сбой не говорит ничего.
 */

export const AUTH_REFRESH_OUTCOME = Object.freeze({
    REFRESHED: 'refreshed',
    // Сервер ответил, что сессии больше нет, — только это ведёт на экран входа.
    REJECTED: 'rejected',
    // Ответа по существу нет: сеть, 5xx, 429. Сессию не трогаем, следующий запрос попробует снова.
    UNAVAILABLE: 'unavailable',
});

// Коды AuthError из bot_schedule2.py, которые лечатся обновлением сессии.
const RECOVERABLE_AUTH_CODES = new Set([
    'TOKEN_EXPIRED',
    'INVALID_TOKEN',
    'INVALID_TOKEN_TYPE',
    'MISSING_TOKEN',
    'REFRESH_TOKEN_MISMATCH',
    'SESSION_EXPIRED',
    'SESSION_NOT_FOUND',
    'SESSION_REVOKED',
]);

export const isRecoverableAuthBody = (body) => (
    RECOVERABLE_AUTH_CODES.has(body?.code) || body?.error === 'JWT authentication failed'
);

/* result — то, что вернула процедура обновления: { status, sessionAccepted? }.
   Отвергнуть сессию может только сам /api/auth/refresh, и отвечает он на это 401.
   sessionAccepted === false — сервер токены выдал, а сохранить их не вышло: старый
   refresh-токен уже повёрнут и через 90 секунд станет чужим, так что это тоже конец. */
export const classifyAuthRefreshResult = (result) => {
    const status = Number(result?.status);
    if (status === 401) return AUTH_REFRESH_OUTCOME.REJECTED;
    if (status >= 200 && status < 300) {
        return result?.sessionAccepted === false
            ? AUTH_REFRESH_OUTCOME.REJECTED
            : AUTH_REFRESH_OUTCOME.REFRESHED;
    }
    return AUTH_REFRESH_OUTCOME.UNAVAILABLE;
};

/* Все, кто попросил обновление, пока оно в полёте, получают ОДИН и тот же итог
   { outcome, status }. Промис не отклоняется никогда: брошенное исключение — это сеть. */
export const createSharedAuthRefresh = (performRefresh) => {
    let inFlight = null;
    return () => {
        if (inFlight) return inFlight;
        const settled = new Promise((resolve) => resolve(performRefresh())).then(
            (result) => ({ outcome: classifyAuthRefreshResult(result), status: Number(result?.status) || 0 }),
            () => ({ outcome: AUTH_REFRESH_OUTCOME.UNAVAILABLE, status: 0 })
        );
        inFlight = settled;
        settled.then(() => {
            if (inFlight === settled) inFlight = null;
        });
        return settled;
    };
};

// Токен из заголовков fetch (объект или Headers) и axios (объект или AxiosHeaders).
export const readBearerToken = (headers) => {
    if (!headers || typeof headers !== 'object') return '';
    const raw = typeof headers.get === 'function'
        ? headers.get('Authorization')
        : (headers.Authorization ?? headers.authorization);
    const match = /^Bearer\s+(\S+)/i.exec(String(raw || '').trim());
    return match ? match[1] : '';
};

/* Запрос ушёл с одним токеном, а у вкладки уже другой — обновление прошло, пока запрос
   был в пути. Такой 401 лечится повтором с текущим токеном, без ещё одной ротации. */
const needsRefreshFor = (sentToken, currentToken) => !sentToken || sentToken === currentToken;

const readJsonSafe = async (response) => {
    try {
        return await response.clone().json();
    } catch (_error) {
        return null;
    }
};

// Ошибка для вызывающего, когда обновить сессию не дал сервер или сеть. Без `response`:
// восстановление сессии при старте (App.jsx, restoreSession) тогда оставляет сохранённого
// пользователя, как при любом обрыве связи, вместо того чтобы разлогинить.
export const toAuthRefreshUnavailableError = (sourceError) => {
    const error = new Error('Нет связи с сервером — повторите действие');
    error.name = 'AxiosError';
    error.code = 'ERR_NETWORK';
    error.isAxiosError = true;
    error.authRefreshUnavailable = true;
    error.config = sourceError?.config;
    return error;
};

const dropAuthorizationHeader = (headers) => {
    if (!headers || typeof headers !== 'object') return;
    if (typeof headers.delete === 'function') headers.delete('Authorization');
    delete headers.Authorization;
    delete headers.authorization;
};

/* Обёртка над window.fetch. onResponse видит каждый ответ (App.jsx забирает из
   заголовков повёрнутые токены); buildRetryHeaders собирает заголовки повтора с
   текущим токеном; onSessionRejected уводит на вход. */
export const createAuthRetryingFetch = ({
    nativeFetch,
    refreshAuthSession,
    getCurrentAccessToken,
    buildRetryHeaders,
    onSessionRejected,
    onResponse = null,
}) => async (input, init) => {
    const response = await nativeFetch(input, init);
    if (onResponse) {
        try {
            onResponse(response);
        } catch (_error) {
            // Сбой разбора заголовков не должен менять ответ вызывающему.
        }
    }

    const requestInit = init || {};
    const requestUrl = typeof input === 'string' ? input : String(input?.url || input?.href || '');
    if (
        response?.status !== 401
        || !requestUrl
        || requestUrl.includes('/api/login')
        || requestUrl.includes('/api/auth/refresh')
        || requestInit.__otpAuthRetry
    ) {
        return response;
    }
    if (!isRecoverableAuthBody(await readJsonSafe(response))) return response;

    const sentToken = readBearerToken(requestInit.headers) || readBearerToken(input?.headers);
    if (needsRefreshFor(sentToken, getCurrentAccessToken())) {
        const { outcome } = await refreshAuthSession();
        if (outcome === AUTH_REFRESH_OUTCOME.REJECTED) return onSessionRejected();
        if (outcome !== AUTH_REFRESH_OUTCOME.REFRESHED) return response;
    }

    const retryResponse = await nativeFetch(input, {
        ...requestInit,
        __otpAuthRetry: true,
        headers: buildRetryHeaders(requestInit.headers),
    });
    // Свежий токен тоже не принят — сессию отозвали.
    if (retryResponse?.status === 401 && isRecoverableAuthBody(await readJsonSafe(retryResponse))) {
        return onSessionRejected();
    }
    return retryResponse;
};

/* Обработчик ошибок для axios.interceptors.response. replay(config) переигрывает запрос
   (через axios — тогда request-перехватчик подставит текущий токен); redact чистит из
   ошибки заголовки и тело, но только на ОКОНЧАТЕЛЬНОМ отказе. */
export const createAxiosAuthErrorHandler = ({
    replay,
    refreshAuthSession,
    getCurrentAccessToken,
    onSessionRejected,
    redact = (error) => error,
}) => async (error) => {
    const request = error?.config;
    const url = String(request?.url || '');
    if (
        error?.response?.status !== 401
        || !request
        || url.includes('/api/login')
        || url.includes('/api/auth/refresh')
        || !isRecoverableAuthBody(error.response.data)
    ) {
        return Promise.reject(redact(error));
    }
    // Повтор со свежим токеном снова получил 401 — сессию отозвали.
    if (request.__isRetryRequest) return onSessionRejected();
    request.__isRetryRequest = true;

    if (needsRefreshFor(readBearerToken(request.headers), getCurrentAccessToken())) {
        const { outcome } = await refreshAuthSession();
        if (outcome === AUTH_REFRESH_OUTCOME.REJECTED) return onSessionRejected();
        if (outcome !== AUTH_REFRESH_OUTCOME.REFRESHED) {
            return Promise.reject(toAuthRefreshUnavailableError(redact(error)));
        }
    }
    dropAuthorizationHeader(request.headers);
    return replay(request);
};
