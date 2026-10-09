import { laterStatus, pilotChatKey } from './chatPilot.js';

/* Порядок списка чатов: где клиент ждёт ответа команды — вверху, дальше — по
 * времени последнего сообщения (решение владельца 09.10.2026). Ответ в соседнем
 * чате больше не уводит вниз чат, где клиент ждёт.
 *
 * Сервер отдаёт страницы уже в этом порядке и со счётчиком в строке
 * (wazzup/chat_list.py, unreadCount) — список при открытии сразу стоит как надо.
 * Живой общий счётчик (useSharedChatUnread) его уточняет: ответили — чат уходит
 * на своё место по времени, клиент написал — поднимается, даже если его строки
 * нет на загруженных страницах. Пока снимок счётчика не пришёл (ready), для
 * строк без живого значения верна подсказка сервера. */

export const byRecency = (a, b) => new Date(b.lastMessageAt || 0) - new Date(a.lastMessageAt || 0);

export const chatMatchesFilter = (chat, { channelId = '', search = '' } = {}) => {
    const query = search.trim().toLowerCase();
    return (!channelId || chat.channelId === channelId)
        && (!query || [chat.contactName, chat.contactPhone, chat.chatId]
            .some((value) => String(value || '').toLowerCase().includes(query)));
};

// Сколько сообщений чата ждут ответа: живой счётчик, до его снимка — строка сервера.
export function waitingCount(chat, unread) {
    if (!unread || !chat) return 0;
    const live = unread.items?.[pilotChatKey('op', chat)];
    if (live) return live.unreadCount || 0;
    return unread.ready ? 0 : chat.unreadCount || 0;
}

/* Галочки в строке — статус последнего сообщения чата (lastMessageId/
   lastMessageStatus, wazzup/chat_list.py). Строка приходит и страницей списка,
   и сводкой из живого потока, а статус — ещё и отдельными событиями доставки;
   пришедшая позже копия той же строки не откатывает «прочитано» к «доставлено». */
export function mergeChatRow(previous, next) {
    if (!previous || !next) return next;
    let merged = next;
    // Число каналов номера («Чаты по каналам») считает только страница списка —
    // сводка из живого потока его не несёт, и стрелка не должна от неё пропадать.
    if (next.channelsCount == null && previous.channelsCount != null) {
        merged = { ...merged, channelsCount: previous.channelsCount };
    }
    if (previous.lastMessageId === next.lastMessageId) {
        const status = laterStatus(previous.lastMessageStatus, next.lastMessageStatus);
        if (status !== next.lastMessageStatus) merged = { ...merged, lastMessageStatus: status };
    }
    return merged;
}

// События доставки (statusOnly) — к строкам, чьё последнее сообщение они касаются.
export function applyDeliveryToRows(rows, events) {
    if (!rows?.length) return rows;
    const latest = new Map();
    for (const event of events || []) {
        if (event?.statusOnly !== true || !event.status || !event.messageId) continue;
        const key = `${pilotChatKey('op', event)}:${event.messageId}`;
        latest.set(key, laterStatus(latest.get(key), event.status));
    }
    if (!latest.size) return rows;
    let changed = false;
    const next = rows.map((row) => {
        const status = latest.get(`${pilotChatKey('op', row)}:${row.lastMessageId}`);
        const merged = status ? laterStatus(row.lastMessageStatus, status) : row.lastMessageStatus;
        if (merged === row.lastMessageStatus) return row;
        changed = true;
        return { ...row, lastMessageStatus: merged };
    });
    return changed ? next : rows;
}

/* chats — загруженные строки (по времени), null — список ещё грузится.
   unread — общий счётчик аккаунта «op» ({ items, ready }); без него порядок
   сервера не трогается. unreadOnly — только ждущие ответа. */
export function orderChatList(chats, unread, { unreadOnly = false, channelId = '', search = '' } = {}) {
    if (!unread) return chats;
    const waiting = new Map();
    for (const chat of chats || []) {
        if (waitingCount(chat, unread) > 0) waiting.set(pilotChatKey('op', chat), chat);
    }
    for (const [key, item] of Object.entries(unread.items || {})) {
        if (item.unreadCount > 0 && !waiting.has(key) && item.chat && chatMatchesFilter(item.chat, { channelId, search })) {
            waiting.set(key, item.chat);
        }
    }
    const top = [...waiting.values()].sort(byRecency);
    if (unreadOnly) return top;
    if (chats === null) return null;
    return [...top, ...chats.filter((chat) => !waiting.has(pilotChatKey('op', chat)))];
}
