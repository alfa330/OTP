/* Чистые помощники встроенных страниц iCORE Phone (адреса офисов, тренажёры):
 * что значит ответ сервера о доступе, когда просить новый QR, как показать
 * резервный код, какой сессии принадлежит токен, когда повторять после сбоя.
 *
 * Без React и без DOM — их читают тесты через node (tests/offices_embed.test.mjs).
 */
import { readJwtPayload } from '../trainers_embed/phoneBridge.js';

/** Срок жизни QR по умолчанию — SENSITIVE_QR_TTL_SECONDS в bot_schedule2.py. */
export const QR_DEFAULT_TTL_SEC = 300;
/** Как часто спрашивать «подтвердили?» — тот же шаг, что у окна QR в портале (App.jsx). */
export const QR_POLL_MS = 2500;

/* Ответ сервера → что показать человеку.
 *
 * Коды — из wiki/routes.py (wiki_route: отдел, QR-гейт) и
 * wiki/routes_structure.py (request_space). state:
 *   locked     — нужна QR-сессия (SENSITIVE_ACCESS_REQUIRED);
 *   denied     — отказ, который QR не снимет: вики не выдана отделу, нет
 *                пространств, прочие 403;
 *   respace    — пространство пропало или его надо назвать — выбрать заново;
 *   no-session — 401: токена нет или сессию отозвали;
 *   error      — сеть или 5xx: стоит повторить.
 */
export const accessStateFromError = (err) => {
    const response = err?.response;
    // Ответа нет вовсе: сеть, таймаут или CORS (у 404 без заголовков CORS браузер
    // тоже показывает «нет ответа») — это повод повторить, а не отказ.
    if (!response) return { state: 'error', reason: 'network', message: '' };
    const status = Number(response.status) || 0;
    const data = response.data && typeof response.data === 'object' ? response.data : {};
    const code = String(data.code || '');
    const message = typeof data.error === 'string' ? data.error.trim() : '';

    if (status === 401) return { state: 'no-session', reason: code || 'unauthorized', message };
    if (code === 'SENSITIVE_ACCESS_REQUIRED') return { state: 'locked', reason: 'qr', message };
    if (code === 'WIKI_DEPARTMENT_DISABLED') return { state: 'denied', reason: 'department', message };
    // Пространство убрали в архив или закрыли от отдела (404), либо их стало
    // несколько, а запрос ушёл без space_id (400), — это не отказ, а повод выбрать
    // пространство заново.
    if (code === 'WIKI_SPACE_NOT_FOUND' || (code === 'WIKI_SPACE_REQUIRED' && status === 400)) {
        return { state: 'respace', reason: 'space', message };
    }
    // 403 WIKI_SPACE_REQUIRED — «вам не выдано ни одного пространства»: уже ответ.
    if (code === 'WIKI_SPACE_REQUIRED') return { state: 'denied', reason: 'no-space', message };
    // 404 с текстом — «Пользователь не найден» из wiki_route; голый 404 без тела —
    // скорее сбой выкладки, его стоит повторить.
    if (status === 403 || (status === 404 && message)) return { state: 'denied', reason: 'forbidden', message };
    return { state: 'error', reason: status >= 500 ? 'server' : 'http', message };
};

/** Запрос идёт к нашему API? Только таким подставляем токен — чужим адресам
 *  (тайлы 2ГИС и прочее) он ни к чему.
 *  Пустая база — локальный стенд (vite.stand.config.mjs вырезает адрес API из
 *  исходников): запросы там идут относительным путём /api/... на свой же origin. */
export const isApiUrl = (url, apiBase) => {
    const text = String(url || '');
    if (!text) return false;
    if (apiBase) return text === apiBase || text.startsWith(`${apiBase}/`);
    return text.startsWith('/api/');
};

/** Ключ СЕССИИ по access-токену: sid из JWT. При обновлении токена sid прежний
 *  (bot_schedule2: refresh не заводит новую сессию), а QR-подтверждение живёт
 *  именно на сессии — поэтому сменой токена считаем только смену sid. Токен не JWT —
 *  судить не по чему, ключом служит сам токен; нет токена — ''. */
export const sessionKeyOf = (token) => {
    const text = String(token || '').trim();
    if (!text) return '';
    const sid = readJwtPayload(text)?.sid;
    return sid ? `sid:${sid}` : `token:${text}`;
};

/* Когда просить новый QR и до какого момента действует нынешний.
 *
 * Считаем от момента ПОЛУЧЕНИЯ ответа, а не по часам компьютера: сервер отдаёт
 * абсолютное время истечения, а часы у оператора бывают и впереди, и позади на
 * минуты. Срок, вышедший за обычную жизнь кода (не положительный или длиннее пяти
 * минут), — признак расхождения часов, тогда берём срок по умолчанию.
 *
 * Новый код просим заранее (за 30 с, но не больше четверти срока): старый при этом
 * продолжает действовать до своего конца — сервер выданных кодов не помнит, и
 * код, уже отправленный супервайзеру, рабочий до истечения. Меньше 15 с между
 * запросами не бывает — даже если сервер отдал совсем короткий срок. */
export const qrSchedule = (expiresAt, receivedAtMs = Date.now(), {
    defaultTtlSec = QR_DEFAULT_TTL_SEC, leadSec = 30, minSec = 15,
} = {}) => {
    const expMs = Date.parse(String(expiresAt || ''));
    let ttlMs = Number.isFinite(expMs) ? expMs - receivedAtMs : NaN;
    if (!(ttlMs > 0) || ttlMs > defaultTtlSec * 1000) ttlMs = defaultTtlSec * 1000;
    const leadMs = Math.min(leadSec * 1000, ttlMs / 4);
    return {
        validUntilMs: receivedAtMs + ttlMs,
        refreshAtMs: receivedAtMs + Math.max(minSec * 1000, ttlMs - leadMs),
    };
};

/** Пауза перед автоматическим повтором после сбоя сети: 3, 6, 12, 24, потом 30 с.
 *  Общая для обеих страниц: офисы повторяют проверку доступа и загрузку списка,
 *  тренажёры — проверку записи. */
export const retryDelayMs = (attempt = 0) => Math.min(30000, 3000 * (2 ** Math.max(0, Math.min(Number(attempt) || 0, 4))));

/** Пауза перед следующим вопросом «подтвердили?». Сеть отказывает — растягиваем
 *  шаг вдвое за каждый отказ подряд, но не дальше потолка: опрос из каждого
 *  телефона раз в 2,5 с при лежащем сервере — это лишняя нагрузка ровно тогда,
 *  когда её меньше всего нужно. */
export const statusPollDelayMs = (failures = 0, baseMs = QR_POLL_MS, maxMs = 30000) => {
    const n = Math.max(0, Math.min(Number(failures) || 0, 6));
    return Math.min(maxMs, baseMs * (2 ** n));
};

/* Резервный код для показа: приставка отдельно, тело — по четыре знака.
 *
 * Код — та же строка, что в QR (OTPQ: + 52 знака base32), её же портал печатает
 * «резервным кодом», и её же принимает «Ввести код вручную» в «QR доступ».
 * Группы — только для глаз (продиктовать, сверить): копируется строка целиком,
 * без пробелов, — разбор кода на сервере пробелов внутри не прощает. */
export const splitQrCode = (payload, size = 4) => {
    const text = String(payload || '').trim();
    const match = /^([A-Za-z][A-Za-z-]*:)(.*)$/.exec(text);
    const prefix = match ? match[1] : '';
    const body = match ? match[2] : text;
    const groups = [];
    for (let i = 0; i < body.length; i += size) groups.push(body.slice(i, i + size));
    return { prefix, groups };
};

/** «4:05» — сколько осталось; 0 и меньше — «0:00». */
export const formatCountdown = (ms) => {
    const total = Math.max(0, Math.ceil((Number(ms) || 0) / 1000));
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    return `${minutes}:${String(seconds).padStart(2, '0')}`;
};
