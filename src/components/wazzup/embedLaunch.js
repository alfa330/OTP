/*
 * Вход в окно «Чатов ОП» в CRM без пароля — обмен одноразовой ссылки на сессию.
 *
 * Владелец, 20.10.2026: «операторам что нужно будет заходить в свои аккаунты внутри этого
 * iframe? Это надо бы как то решить». Сервер CRM своим ключом получает у iCORE адрес окна с
 * одноразовым кодом: …?view=wazzup_chats&embed=1&launch=<код>&uid=<учётка> (wazzup/embed_auth.py).
 *
 * Модуль грузится ПЕРВЫМ (main.jsx), до App.jsx: портал при старте поднимает сохранённую
 * сессию и обновляет её токены — прежней сессии нельзя успеть это сделать, раз её сейчас
 * сменит новая. Поэтому прежние токены до ответа сервера лежат в памяти, а не в хранилище,
 * а сам портал main.jsx запускает только после обмена.
 *
 * Сессия окна принадлежит пользователю CRM, для которого окно открыто (crmUserId с подписью
 * iCORE в адресе): на общем компьютере следующий пользователь CRM не работает под учёткой
 * прежнего — его сессия снимается, даже если в адресе нет кода.
 *
 * Сессия, полученная по ссылке (чаты без QR), той же учётки и ещё живая, остаётся — код просто
 * истечёт: иначе каждая отрисовка страницы CRM заводила бы новую сессию. Сессия входа паролем
 * меняется на сессию по ссылке. Иначе код меняется на сессию, токены и человек кладутся туда
 * же, где их держит портал (App.jsx), и окно перезагружается уже без кода. Не вышло —
 * возвращается прежняя сессия той же учётки, а без неё окно покажет вход.
 */
import { parseEmbedIdentity } from './embedBridge.js';

// Тот же адрес, что API_BASE_URL в App.jsx (тест сверяет): App.jsx здесь импортировать нельзя.
export const EMBED_API_BASE_URL = 'https://otp-2-fos4.onrender.com';
export const EMBED_SCOPE = 'crm_embed';
const ACCESS_KEY = 'otp_access_token';
const REFRESH_KEY = 'otp_refresh_token';
const TRANSPORT_KEY = 'otp_auth_transport';
const USER_KEY = 'user';
// Для какого пользователя CRM открыта сессия окна и какая сессия получена по ссылке.
export const CRM_USER_KEY = 'otp_embed_crm_user';
/* Ключ сменился 23.10.2026, когда сервер начал помечать сессии из CRM (wazzup_crm_sessions,
   режим «crm»): выданные раньше пометки не имеют, и окно один раз меняет такую сессию на
   новую — иначе админ так и сидел бы в окне на просмотре до истечения старой (30 дней). */
export const LAUNCH_SID_KEY = 'otp_embed_crm_sid';
const LEGACY_LAUNCH_SID_KEY = 'otp_embed_launch_sid';
const KEEP_MARGIN_MS = 60 * 1000;

/** Окно CRM → { code, uid, crmUserId } ('' — нет), не окно → null. */
export const readEmbedLaunch = (search) => {
    try {
        const params = new URLSearchParams(String(search || ''));
        if (params.get('embed') !== '1') return null;
        const raw = params.get('launch') || '';
        const code = /^[A-Za-z0-9_-]{20,100}$/.test(raw) ? raw : '';
        const uid = params.get('uid') || '';
        const identity = parseEmbedIdentity(Object.fromEntries(params));
        return {
            code,
            uid: code && /^\d{1,12}$/.test(uid) ? uid : '',
            crmUserId: identity ? identity.crmUserId : '',
        };
    } catch (error) {
        return null;
    }
};

/** Поля JWT без проверки подписи — только чтобы решить, оставлять ли сессию. */
export const jwtPayload = (token) => {
    try {
        const part = String(token || '').split('.')[1];
        return part ? JSON.parse(atob(part.replace(/-/g, '+').replace(/_/g, '/'))) : null;
    } catch (error) {
        return null;
    }
};

/** Живая сессия окна, полученная по ссылке для того же человека, — её и оставить. */
export const canKeepSession = (refreshToken, uid, launchSid, nowMs = Date.now()) => {
    const payload = jwtPayload(refreshToken);
    return Boolean(payload && uid && launchSid && String(payload.sub) === String(uid)
        && String(payload.sid) === String(launchSid) && payload.scp === EMBED_SCOPE
        && Number(payload.exp) * 1000 > nowMs + KEEP_MARGIN_MS);
};

/** Адрес окна без одноразового кода — для history.replaceState. */
export const stripLaunchParams = (href) => {
    const url = new URL(href);
    url.searchParams.delete('launch');
    url.searchParams.delete('uid');
    return `${url.pathname}${url.search}${url.hash}`;
};

/** Запросы, которые окну нужны: чаты, вход и выход. Остальное портал в окне не зовёт. */
export const isEmbedApiAllowed = (url, apiBaseUrl = EMBED_API_BASE_URL) => {
    const value = String(url || '');
    if (!value.startsWith(`${apiBaseUrl}/api/`)) return true;
    const path = value.slice(apiBaseUrl.length).split('?')[0];
    return path.startsWith('/api/wazzup/') || path.startsWith('/api/auth/')
        || path === '/api/login' || path === '/api/logout';
};

/** После обмена окно перезагружается — портал в этой загрузке не запускают (main.jsx). */
export const reloadPending = (result) => result === 'exchanged' || result === 'restored';

const storagesOf = (win) => ['localStorage', 'sessionStorage'].map((name) => {
    try {
        return win[name] || null;
    } catch (error) {
        return null;
    }
}).filter(Boolean);

const readStored = (win, key) => {
    for (const storage of storagesOf(win)) {
        try {
            const value = storage.getItem(key);
            if (value) return value;
        } catch (error) {
            // Хранилище закрыто — смотрим следующее.
        }
    }
    return '';
};

const writeStored = (win, key, value) => {
    for (const storage of storagesOf(win)) {
        try {
            if (value) storage.setItem(key, value);
            else storage.removeItem(key);
        } catch (error) {
            // Пишем, куда пускают.
        }
    }
};

const snapshotSession = (win) => ({
    access: readStored(win, ACCESS_KEY),
    refresh: readStored(win, REFRESH_KEY),
    transport: readStored(win, TRANSPORT_KEY),
    launchSid: readStored(win, LAUNCH_SID_KEY),
    user: (() => {
        try {
            return win.localStorage.getItem(USER_KEY) || '';
        } catch (error) {
            return '';
        }
    })(),
});

const clearSession = (win) => {
    for (const key of [ACCESS_KEY, REFRESH_KEY, TRANSPORT_KEY, LAUNCH_SID_KEY, LEGACY_LAUNCH_SID_KEY]) {
        writeStored(win, key, '');
    }
    try {
        win.localStorage.removeItem(USER_KEY);
    } catch (error) {
        // Нечего убирать.
    }
};

const writeSession = (win, { access, refresh, transport = 'bearer', user, launchSid }) => {
    writeStored(win, ACCESS_KEY, access);
    writeStored(win, REFRESH_KEY, refresh);
    writeStored(win, TRANSPORT_KEY, transport || 'bearer');
    writeStored(win, LAUNCH_SID_KEY, launchSid || '');
    try {
        if (user) win.localStorage.setItem(USER_KEY, user);
    } catch (error) {
        // Без человека портал спросит /api/auth/me — сессия всё равно поднимется.
    }
};

/** Отозвать снятую сессию окна, не дожидаясь ответа. */
const revoke = (win, apiBaseUrl, access) => {
    if (!access) return;
    try {
        win.fetch(`${apiBaseUrl}/api/logout`, { method: 'POST', credentials: 'omit', keepalive: true,
            headers: { Authorization: `Bearer ${access}` } }).catch(() => {});
    } catch (error) {
        // Сессия истечёт сама.
    }
};

const setHidden = (win, hidden) => {
    try {
        win.document.documentElement.style.visibility = hidden ? 'hidden' : '';
    } catch (error) {
        // Нет документа — прятать нечего.
    }
};

/** Вход окна. Возвращает 'kept' | 'exchanged' | 'restored' | 'failed' | 'switched' | null. */
export async function runEmbedLaunch({ win = typeof window === 'undefined' ? null : window,
    apiBaseUrl = EMBED_API_BASE_URL, nowMs = Date.now() } = {}) {
    const launch = win ? readEmbedLaunch(win.location?.search) : null;
    if (!launch) return null;
    let result = null;
    // Окно открыто для другого пользователя CRM — сессию прежнего не оставляем.
    if (launch.crmUserId && readStored(win, CRM_USER_KEY) !== launch.crmUserId) {
        const stale = snapshotSession(win);
        clearSession(win);
        revoke(win, apiBaseUrl, stale.access);
        writeStored(win, CRM_USER_KEY, launch.crmUserId);
        result = 'switched';
    }
    if (!launch.code) return result;
    // Код одноразовый: из адреса — сразу, чтобы F5 и ссылка из окна его не повторяли.
    try {
        win.history.replaceState(win.history.state, '', stripLaunchParams(win.location.href));
    } catch (error) {
        // Адресная строка недоступна — код всё равно одноразовый.
    }
    if (canKeepSession(readStored(win, REFRESH_KEY), launch.uid, readStored(win, LAUNCH_SID_KEY), nowMs)) {
        return 'kept';
    }
    const previous = snapshotSession(win);
    clearSession(win);
    setHidden(win, true);
    try {
        const response = await win.fetch(`${apiBaseUrl}/api/auth/embed-launch`, {
            method: 'POST', credentials: 'omit', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code: launch.code, auth_transport: 'bearer' }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.access_token || !data.refresh_token) throw new Error(data.code || 'LAUNCH_FAILED');
        writeSession(win, { access: data.access_token, refresh: data.refresh_token,
            user: data.user ? JSON.stringify(data.user) : '',
            launchSid: String(jwtPayload(data.refresh_token)?.sid || '') });
        // Прежнюю сессию окна сменила новая — отзываем.
        revoke(win, apiBaseUrl, previous.access);
        win.location.reload();
        return 'exchanged';
    } catch (error) {
        // Вернуть можно только сессию той учётки, которую CRM открывает сейчас.
        if (previous.refresh && String(jwtPayload(previous.refresh)?.sub || '') === launch.uid) {
            writeSession(win, previous);
            win.location.reload();
            return 'restored';
        }
        revoke(win, apiBaseUrl, previous.access);
        setHidden(win, false);
        return 'failed';
    }
}
