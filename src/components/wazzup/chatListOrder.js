import { pilotChatKey } from './chatPilot.js';

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
