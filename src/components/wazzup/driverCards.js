/* Карточка водителя в чатах Верификаторов: разбор ID, версии и подписи статуса.
 *
 * Сервер — wazzup/driver_cards.py: правило разбора ID здесь повторяет его
 * normalize_driver_id, чтобы ошибка была видна у поля сразу, а не после
 * запроса. Решает всё равно сервер. */

export const DRIVER_ID_HINT = 'ID водителя — 32 символа: цифры и латинские буквы a–f. '
    + 'Можно вставить ссылку на профиль водителя во Fleet.';

// Без просмотра назад (?<!…): Safari до 16.4 его не знает, и модуль не
// разобрался бы целиком (как в library/epubText.js). Сосед слева — группой.
const HEX32 = /(^|[^0-9a-f])([0-9a-f]{32})(?![0-9a-f])/g;
const PROFILE = /(?:contractors|drivers)\/([0-9a-f]{32})(?![0-9a-f])/g;
// Ссылка без пути профиля: одинокий код в ней — park_id, а не водитель.
const LINK = /https?:|www\.|fleet\.|\/|park_id|contractors|drivers/;
const MAX_RAW = 2000;

/* { value, error }: value — ID или null для пустого поля. Ссылка из Fleet
   вида /contractors/<ID водителя>/details?park_id=<ID парка> несёт два кода —
   водитель тот, что после contractors/. */
export function normalizeDriverId(raw) {
    if (raw === null || raw === undefined) return { value: null, error: '' };
    if (typeof raw !== 'string' || raw.length > MAX_RAW) return { value: null, error: DRIVER_ID_HINT };
    const text = raw.trim().toLowerCase();
    if (!text) return { value: null, error: '' };
    const profile = new Set([...text.matchAll(PROFILE)].map((match) => match[1]));
    if (profile.size) return profile.size === 1 ? { value: [...profile][0], error: '' } : { value: null, error: DRIVER_ID_HINT };
    if (LINK.test(text)) return { value: null, error: DRIVER_ID_HINT };
    const found = new Set([...text.matchAll(HEX32)].map((match) => match[2]));
    return found.size === 1 ? { value: [...found][0], error: '' } : { value: null, error: DRIVER_ID_HINT };
}

export const driverCardKey = (chat) => chat?.channelId && chat?.chatId
    ? JSON.stringify(['op', chat.channelId, chat.chatId]) : '';

const time = (card) => {
    const value = new Date(card?.createdAt).getTime();
    return Number.isFinite(value) ? value : 0;
};

const FINAL_STATES = new Set(['synced', 'failed', 'no_deal', 'superseded']);
const queuedAt = (card) => {
    const value = new Date(card?.amo?.queuedAt).getTime();
    return Number.isFinite(value) ? value : 0;
};

/* Одна и та же версия из двух источников. Снимок GET, прочитанный до записи,
   может прийти ПОСЛЕ живого кадра с итогом — тогда «Отправляем в amoCRM»
   крутилось бы вечно: новых событий по версии уже не будет. Итог не уступает
   «отправляется» той же постановки в очередь; «Повторить» ставит версию в
   очередь заново (queuedAt новее) — и тогда «отправляется» законно. */
function fresher(existing, incoming) {
    if (!existing) return incoming;
    const before = queuedAt(existing);
    const after = queuedAt(incoming);
    const stale = after < before
        || (after === before && FINAL_STATES.has(existing.amo?.state) && incoming.amo?.state === 'pending');
    return stale ? { ...existing, ...incoming, amo: existing.amo } : { ...existing, ...incoming };
}

/* Версии карточки: свежие сверху, по одной на id; живое событие и снимок с
   сервера сливаются, а не заменяют друг друга. */
export function mergeCardVersions(previous = [], incoming = [], limit = 50) {
    const byId = new Map(previous.map((card) => [String(card.id), card]));
    for (const card of incoming) {
        if (card?.id == null || !card.createdAt || !card.amo) continue;
        byId.set(String(card.id), fresher(byId.get(String(card.id)), card));
    }
    return [...byId.values()]
        .sort((a, b) => time(b) - time(a) || String(b.id).localeCompare(String(a.id)))
        .slice(0, limit);
}

/* Что показать про запись в amoCRM. tone — смысл цвета: зелёный только у
   «записано», красный — когда нужен человек, остальное нейтральное. */
export function amoStatus(card) {
    const amo = card?.amo || {};
    switch (amo.state) {
        case 'synced':
            return { tone: 'ok', label: 'В amoCRM', title: amo.leadId ? `Записано в сделку №${amo.leadId}` : 'Записано в amoCRM' };
        case 'failed':
            return { tone: 'bad', label: 'Не записано в amoCRM', title: amo.error || '', retry: true };
        case 'no_deal':
            return { tone: 'bad', label: 'Сделка в amoCRM не найдена', title: amo.error || '', retry: true };
        case 'superseded':
            return { tone: 'muted', label: 'Заменена следующей', title: 'В amoCRM ушла более новая версия карточки' };
        default:
            return amo.error
                ? { tone: 'wait', label: 'amoCRM: повторим', title: amo.error }
                : { tone: 'wait', label: 'Отправляем в amoCRM', title: '' };
    }
}

export const sameCardValues = (card, draft) => Boolean(card)
    && (card.driverId || null) === (draft.driverId || null)
    && (card.park?.id ?? null) === (draft.parkId ?? null)
    && (card.city?.id ?? null) === (draft.cityId ?? null);

/* Что изменилось относительно предыдущей версии — для подсветки в истории.
   У первой версии сравнивать не с чем: она не выделяется вовсе. */
export function changedFields(card, previous) {
    const changed = new Set();
    if (!previous) return changed;
    if ((card.driverId || null) !== (previous.driverId || null)) changed.add('driverId');
    if ((card.park?.id ?? null) !== (previous.park?.id ?? null)) changed.add('park');
    if ((card.city?.id ?? null) !== (previous.city?.id ?? null)) changed.add('city');
    return changed;
}

export function fmtCardTime(iso) {
    const date = new Date(iso);
    if (!Number.isFinite(date.getTime())) return '';
    const now = new Date();
    const clock = date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    if (date.toDateString() === now.toDateString()) return `сегодня, ${clock}`;
    const sameYear = date.getFullYear() === now.getFullYear();
    return `${date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', ...(sameYear ? {} : { year: 'numeric' }) })}, ${clock}`;
}

export const newClientId = () => {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};
