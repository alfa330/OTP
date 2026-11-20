/*
 * Окно «Чаты» раздела «Воронка найма» (владелец 19.11.2026: «сделать интерфейс обработки чата ватсапа прямо как
 * в разделе чаты ОП»): чистая логика без React — её проверяет tests/hiring_chats.test.mjs.
 *
 * Время с сервера — наивное время Алматы строкой «YYYY-MM-DDTHH:MM:SS» (как во всём разделе): часы и даты
 * берутся из строки, без перевода поясов браузером.
 */

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября',
    'ноября', 'декабря'];

const pad = (n) => String(n).padStart(2, '0');

/** «Сегодняшняя» дата в Алматы строкой YYYY-MM-DD (люди раздела — в Алматы, браузер — их часы). */
export const localDay = (date = new Date()) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

export const dayOf = (iso) => String(iso || '').slice(0, 10);
export const timeOf = (iso) => String(iso || '').slice(11, 16);

const shiftDay = (day, delta) => {
    const [y, m, d] = day.split('-').map(Number);
    return localDay(new Date(y, m - 1, d + delta));
};

/** Время в строке списка: сегодня — часы, вчера — «вчера», раньше — «19.11». */
export function listTime(iso, today = localDay()) {
    const day = dayOf(iso);
    if (!day) return '';
    if (day === today) return timeOf(iso);
    if (day === shiftDay(today, -1)) return 'вчера';
    return `${day.slice(8, 10)}.${day.slice(5, 7)}`;
}

/** Плашка дня в ленте: «Сегодня», «Вчера», «19 ноября», другой год — с годом. */
export function dayLabel(day, today = localDay()) {
    if (!day) return '';
    if (day === today) return 'Сегодня';
    if (day === shiftDay(today, -1)) return 'Вчера';
    const [y, m, d] = day.split('-').map(Number);
    return `${d} ${MONTHS[m - 1]}${String(y) === today.slice(0, 4) ? '' : ` ${y}`}`;
}

/** Время «сейчас» в формате сервера (наивное время Алматы строкой): у только что набранного своего сообщения. */
export const localStamp = (date = new Date()) => `${localDay(date)}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;

/** Номер повтора нажатия «Отправить»: тот же — второго сообщения сервер не создаст. */
export function newClientId(random = Math.random) {
    let id = 'c';
    while (id.length < 20) id += Math.floor(random() * 36).toString(36);
    return id;
}

/** Лента: подгруженные раньше страницы и свежая последняя страница (её сервер отдаёт при каждом опросе). */
export function mergeThread(older, latest) {
    const fresh = latest || [];
    const seen = new Set(fresh.map((message) => message.id));
    return [...(older || []).filter((message) => !seen.has(message.id)), ...fresh];
}

/** Всё увиденное в ленте: новые копии сообщений заменяют старые (отметки доставки, удаление), ничего не теряется —
 *  ни при «Более ранние», ни когда свежая страница опроса уезжает дальше старых сообщений. По времени, потом по id. */
export function mergeMessages(known, incoming) {
    const byId = new Map((known || []).map((message) => [message.id, message]));
    for (const message of incoming || []) byId.set(message.id, message);
    return [...byId.values()].sort((a, b) => (String(a.at) < String(b.at) ? -1 : String(a.at) > String(b.at) ? 1 : a.id - b.id));
}

/** Неотправленное из портала и набранное «прямо сейчас» — хвостом ленты, по времени создания. Локальное
 *  пропадает, как только сервер хоть раз вернул сообщение с тем же номером повтора (seen) — и не всплывает снова,
 *  когда оно уже отправлено и из очереди ушло. */
export function pendingTail(pending, local, seen = null) {
    const server = pending || [];
    const known = new Set(server.map((item) => item.client_id).filter(Boolean));
    const own = (local || []).filter((item) => !known.has(item.client_id) && !(seen && seen.has(item.client_id)));
    return [...server.map((item) => ({ ...item, key: `o:${item.outbox_id}` })),
        ...own.map((item) => ({ ...item, key: `l:${item.client_id}` }))];
}

/** Лента с плашками дней: [{day}, сообщение, …]. */
export function withDays(messages, today = localDay()) {
    const out = [];
    let last = '';
    for (const message of messages || []) {
        const day = dayOf(message.at);
        if (day && day !== last) {
            // Хвост неотправленного может быть вчерашним после сегодняшних сообщений — ключ плашки с позицией.
            out.push({ dayKey: `d:${day}:${out.length}`, label: dayLabel(day, today) });
            last = day;
        }
        out.push(message);
    }
    return out;
}

/** Последнее сообщение кандидата в ленте (не наше, не плашка дня, не неотправленное): под ним — «Ответ не нужен», как
 *  в «Чатах ОП» (владелец 20.11.2026: кнопка «должна быть снизу под последним сообщением кандидата»). */
export function lastIncomingId(rows) {
    for (let index = (rows || []).length - 1; index >= 0; index -= 1) {
        const row = rows[index];
        if (!row.dayKey && !row.pending && !row.from_me && row.id !== undefined && row.id !== null) return row.id;
    }
    return null;
}

/** Касается ли тычок живого канала (hiring/wa_live.py) открытого чата: «перечитать всё» (cards = null) или среди
 *  карточек есть его карточка (0 — чат без карточки). */
export function pulseTouches(pulse, cardId) {
    if (!pulse) return false;
    if (!pulse.cards) return true;
    return pulse.cards.includes(Number(cardId) || 0);
}

/** Тычок из кадра потока: {"c": [12, 0]} — эти карточки, {"all": 1} или непонятное — перечитать всё. */
export function pulseOf(payload, n) {
    const cards = payload && !payload.all && Array.isArray(payload.c)
        ? payload.c.map(Number).filter((value) => Number.isInteger(value) && value >= 0) : null;
    return { n, cards };
}

/** Показывать ли автора над нашим сообщением: у первого в серии одного автора (как в «Чатах ОП»). */
export function showAuthor(message, previous) {
    if (!message?.from_me || !message.author) return false;
    return !previous || !previous.from_me || previous.author !== message.author || dayOf(previous.at) !== dayOf(message.at);
}

/** Набор «/текст» в начале поля — поиск по шаблонам, иначе null. */
export function slashQuery(text) {
    const match = /^\/([^\n]*)$/.exec(String(text || ''));
    return match ? match[1] : null;
}

export function matchTemplates(items, query) {
    const text = String(query || '').trim().toLowerCase();
    if (!text) return items || [];
    return (items || []).filter((item) => `${item.title} ${item.body}`.toLowerCase().includes(text));
}

/** Вставить текст в позицию курсора: новое значение и где встанет курсор. */
export function insertAt(value, start, end, text) {
    const before = String(value || '').slice(0, start ?? String(value || '').length);
    const after = String(value || '').slice(end ?? start ?? String(value || '').length);
    return { value: before + text + after, caret: before.length + text.length };
}

export const fileSize = (bytes) => {
    if (!bytes) return '';
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
    return `${(bytes / 1024 / 1024).toFixed(1).replace('.', ',')} МБ`;
};

export const MAX_FILE = 16 * 1024 * 1024;
export const MAX_TEXT = 4096;

/** Что сказать вместо поля ответа (сервер, wa_chat.SEND_BLOCK_TEXTS). */
export const BLOCK_ACTIONS = { take: 'Взять в работу' };

/** Статус отправки для галочек (MessageDeliveryStatus «Чатов ОП»): своё неотправленное — отправляется / ошибка. */
export function pendingStatus(item) {
    if (item.status === 'failed') return 'failed';
    return 'queued';
}

/** Буквы для кружка аватара: по первой букве или цифре двух первых слов. Эмодзи и знаки не режутся пополам
 *  (у «💌» первая «буква» строки — половинка суррогатной пары) — пустая строка, кружок покажет значок. */
export function chatInitials(name) {
    return String(name || '').trim().split(/\s+/)
        .map((word) => Array.from(word).find((ch) => /[\p{L}\p{N}]/u.test(ch)))
        .filter(Boolean).slice(0, 2).join('').toUpperCase();
}

/** Галочки «Чатов ОП» не знают «прослушано» — для них это «прочитано». */
export const tickStatus = (status) => (status === 'played' ? 'read' : status || 'sent');
