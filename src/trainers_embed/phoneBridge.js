/* Мост между страницей тренажёров и iCORE Phone.
 *
 * Страница живёт внутри WebView2 телефона (microsip-src/DialTrainers.cpp) и сама в
 * портал не входит: токен ей отдаёт телефон. Так у оператора одна сессия — та, что
 * в телефоне; refresh-токен страница не видит вовсе, а access получает свежим по
 * запросу (телефон сам обновляет пару, если срок на исходе).
 *
 * Протокол (JSON-сообщения; описание с той же стороны — в DialTrainers.h):
 *   страница → телефон  { type: 'auth:request' }
 *                       { type: 'ready' }
 *                       { type: 'trainer:open', key, title } / { type: 'trainer:close' }
 *                       { type: 'open:external', url }
 *   телефон → страница  { type: 'auth:token', token, user_id, phone_version, error? }
 *                       { type: 'trainer:host', framed, width, height } — на 'trainer:open'
 *                         телефон перенёс страницу в отдельное окно на весь экран (framed)
 *                         или оставил во вкладке; только теперь можно монтировать
 *                         проигрыватель: он меряет ширину один раз при монтировании
 *                       { type: 'trainer:close-request' } — закрыть тренажёр (крестик
 *                         окна телефона или входящий звонок)
 *
 * Модуль без React и без DOM-зависимостей сверх window.chrome.webview — его читают
 * тесты через node.
 */

const AUTH_TIMEOUT_MS = 8000;

/** Мы внутри WebView2 телефона? Только там есть window.chrome.webview. */
export const hasPhoneHost = (win = globalThis.window) => {
    const bridge = win?.chrome?.webview;
    return !!(bridge && typeof bridge.postMessage === 'function'
        && typeof bridge.addEventListener === 'function');
};

/** Отправить сообщение телефону. Вне телефона — молча ничего: страница обязана
 *  работать и в обычном браузере. */
export const postToPhone = (message, win = globalThis.window) => {
    if (!hasPhoneHost(win)) return false;
    try {
        win.chrome.webview.postMessage(message);
        return true;
    } catch {
        return false;
    }
};

/** Срок access-токена (поле exp JWT) в секундах от эпохи; 0 — не разобрать.
 *  Подпись не проверяем — нам нужен только срок, чтобы попросить свежий заранее. */
export const readJwtExpiry = (token) => {
    const parts = String(token || '').split('.');
    if (parts.length < 2) return 0;
    try {
        const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
        const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
        const json = typeof atob === 'function'
            ? atob(padded)
            : Buffer.from(padded, 'base64').toString('binary');
        const payload = JSON.parse(json);
        const exp = Number(payload?.exp);
        return Number.isFinite(exp) && exp > 0 ? exp : 0;
    } catch {
        return 0;
    }
};

/** Токен ещё годится, если до истечения больше запаса (по умолчанию 2 минуты). */
export const isTokenFresh = (token, graceSec = 120, nowMs = Date.now()) => {
    if (!token) return false;
    const exp = readJwtExpiry(token);
    if (!exp) return true; // не JWT — судить не по чему, пусть решает сервер
    return exp - nowMs / 1000 > graceSec;
};

/** Любое сообщение телефона как объект с полем type; null — не наше сообщение. */
export const parseHostMessage = (data) => {
    let payload = data;
    if (typeof payload === 'string') {
        try { payload = JSON.parse(payload); } catch { return null; }
    }
    if (!payload || typeof payload !== 'object' || typeof payload.type !== 'string' || !payload.type) return null;
    return payload;
};

/** Разобрать сообщение от телефона. Возвращает { token, userId, phoneVersion, error }
 *  или null, если это не ответ на запрос токена. */
export const parseTokenMessage = (data) => {
    const payload = parseHostMessage(data);
    if (!payload || payload.type !== 'auth:token') return null;
    return {
        token: String(payload.token || '').trim(),
        userId: Number(payload.user_id) || 0,
        phoneVersion: String(payload.phone_version || ''),
        error: String(payload.error || ''),
    };
};

/** Дождаться, пока окно страницы сменит ширину (телефон перенёс её в большое окно).
 *  Проигрыватель меряет ширину ОДИН РАЗ при монтировании: смонтируй его до того, как
 *  окно выросло, — и он останется в узкой раскладке на весь урок. Ширина уже другая —
 *  сразу; иначе ждём событие resize, но не дольше timeoutMs (телефон мог не суметь
 *  открыть окно — тогда тренажёр всё равно нужно показать). */
export const waitForViewport = (fromWidth, timeoutMs = 500, win = globalThis.window) => (
    new Promise((resolve) => {
        if (!win || typeof win.addEventListener !== 'function') { resolve(false); return; }
        if (win.innerWidth !== fromWidth) { resolve(true); return; }
        let done = false;
        const finish = (changed) => {
            if (done) return;
            done = true;
            clearTimeout(timer);
            try { win.removeEventListener('resize', onResize); } catch { /* уже снят */ }
            resolve(changed);
        };
        const onResize = () => finish(true);
        const timer = setTimeout(() => finish(false), timeoutMs);
        win.addEventListener('resize', onResize);
    })
);

/** Попросить у телефона токен. Ответ приходит событием message; ждём не дольше
 *  AUTH_TIMEOUT_MS — если телефон молчит, страница работает без записи попыток. */
export const requestPhoneToken = (win = globalThis.window, timeoutMs = AUTH_TIMEOUT_MS) => (
    new Promise((resolve) => {
        if (!hasPhoneHost(win)) { resolve(null); return; }
        const bridge = win.chrome.webview;
        let done = false;
        const finish = (value) => {
            if (done) return;
            done = true;
            clearTimeout(timer);
            try { bridge.removeEventListener('message', onMessage); } catch { /* уже снят */ }
            resolve(value);
        };
        const onMessage = (event) => {
            const parsed = parseTokenMessage(event?.data);
            if (parsed) finish(parsed);
        };
        const timer = setTimeout(() => finish(null), timeoutMs);
        try {
            bridge.addEventListener('message', onMessage);
            bridge.postMessage({ type: 'auth:request' });
        } catch {
            finish(null);
        }
    })
);

/** Заголовки для API вики по токену; null — без токена запросы не шлём. */
export const authHeadersFor = (token) => (
    token
        ? { Authorization: `Bearer ${token}`, 'X-Auth-Transport': 'bearer' }
        : null
);
