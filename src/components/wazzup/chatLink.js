/*
 * Адрес чата Wazzup.
 *
 * До сих пор у чата адреса не было: раздел держал открытую переписку в
 * состоянии React, и человек, который хотел «показать коллеге вот этот диалог»,
 * мог прислать только адрес раздела со словами «дальше найди по номеру».
 * Перезагрузка страницы теряла чат по той же причине.
 *
 * Идентичность чата — ПАРА (channelId, chatId): ровно она и есть первичный ключ
 * wazzup_chats (database.py). Поэтому в адресе живут оба значения, через слеш:
 *   ?view=wazzup_chats&chat=<channelId>/<chatId>
 * Второй, «человеческий» вид ссылки — только номер телефона:
 *   ?view=wazzup_chats&chat=77784237140
 * Его присылают из CRM и из переписки, где канал никому не известен; раздел
 * разрешает такой номер в пару сам, через уже существующий поиск по чатам.
 *
 * Адрес строим поверх ТЕКУЩЕГО адреса портала, а не из константы: фронт живёт
 * на GitHub Pages с базовым путём, и собранный руками '/?view=wazzup_chats'
 * увёл бы человека на корень домена. По этой же схеме сделаны ссылки на статью
 * вики и на задачу — см. components/wiki/articleLink.js.
 */
import { stripTechnicalQueryParams } from '../../utils/urlHygiene.js';

export const WAZZUP_CHATS_VIEW = 'wazzup_chats';
export const APP_VIEW_QUERY_PARAM = 'view';
export const WAZZUP_CHAT_QUERY_PARAM = 'chat';

/* channelId Wazzup — uuid канала, приходит из вебхука как есть. Проверка важна
   не по алфавиту, а по СЛУЖЕБНЫМ символам: значение уходит в параметр запроса
   /api/wazzup/chats?channel_id=… и в разделитель пары. */
export const normalizeWazzupChannelId = (value) => {
    const id = String(value ?? '').trim();
    return /^[A-Za-z0-9_-]{1,64}$/.test(id) ? id : '';
};

/* chatId — непрозрачная строка того же вебхука (database.py: chat_id TEXT): у
   whatsapp это номер, у прочих транспортов бывает логин или внутренний id.
   Поэтому алфавит шире, но '/', '%' и пробел не пускаем: слеш у нас служит
   разделителем пары, а '%' на сервере работает шаблоном ILIKE (bot_schedule2.py
   подставляет q в f"%{q}%" без экранирования).
   Значение со слешем внутри честно остаётся БЕЗ ссылки — это лучше, чем ссылка
   на чужой чат, которая получилась бы при обрезке до первого сегмента. */
export const normalizeWazzupChatId = (value) => {
    const id = String(value ?? '').trim();
    return /^[A-Za-z0-9_.@+-]{1,128}$/.test(id) ? id : '';
};

/* Номер из ссылки → то, что лежит в базе: 11 цифр без плюса (77784237140).
   Правило 8→7 обязательно: половина присланных номеров записана как
   «8 778 423 71 40», и без замены ссылка молча не находила бы ничего. */
export const normalizePhoneDigits = (value) => {
    const digits = String(value ?? '').replace(/\D+/g, '');
    const fixed = digits.length === 11 && digits.startsWith('8') ? `7${digits.slice(1)}` : digits;
    return fixed.length >= 10 && fixed.length <= 15 ? fixed : '';
};

/** Значение параметра chat → цель перехода. null — мусор, перехода нет.
 *
 * Две формы: точная («канал/чат») и номер. Режем по ПЕРВОМУ слешу, а остаток
 * нормализуем целиком: chatId — непрозрачный TEXT, и обрезка по последующим
 * слешам увела бы в никуда, причём только у части каналов.
 */
export const parseWazzupChatTarget = (value) => {
    const raw = String(value ?? '').trim();
    if (!raw) return null;
    const cut = raw.indexOf('/');
    if (cut >= 0) {
        const channelId = normalizeWazzupChannelId(raw.slice(0, cut));
        const chatId = normalizeWazzupChatId(raw.slice(cut + 1));
        if (!channelId || !chatId) return null;
        return { channelId, chatId, phone: '' };
    }
    const phone = normalizePhoneDigits(raw);
    if (!phone) return null;
    return { channelId: '', chatId: '', phone };
};

/** Цель перехода из строки запроса адреса. Нужна разделу при входе: ссылку на
 * чат открывают из переписки, и раздел обязан открыть чат сразу. */
export const readWazzupChatTargetFromSearch = (search) => {
    try {
        const params = new URLSearchParams(String(search || ''));
        return parseWazzupChatTarget(params.get(WAZZUP_CHAT_QUERY_PARAM));
    } catch (error) {
        return null;
    }
};

/** Пара в том виде, в каком она живёт в адресе. '' — пара битая. */
export const formatWazzupChatTarget = (chat) => {
    const channelId = normalizeWazzupChannelId(chat?.channelId);
    const chatId = normalizeWazzupChatId(chat?.chatId);
    return channelId && chatId ? `${channelId}/${chatId}` : '';
};

/** Строка чата, ТОЧНО совпадающая с целью.
 *
 * Сравнение на равенство, а не items[0]: поиск на сервере подстрочный (ILIKE
 * '%q%'), и '77784237140' входит подстрокой в '777842371400' — взяв первую
 * строку ответа, открыли бы чужой чат.
 */
export const findWazzupChatExact = (items, target) => {
    const channelId = normalizeWazzupChannelId(target?.channelId);
    const chatId = normalizeWazzupChatId(target?.chatId);
    if (!channelId || !chatId) return null;
    return (items || []).find((it) => it?.channelId === channelId && it?.chatId === chatId) || null;
};

/** Строки чатов, подходящие номеру, от точных к приблизительным.
 *
 * Два тира, и наружу отдаём ТОЛЬКО один из них. Точный — chatId или
 * contact_phone равны номеру; приблизительный — совпали последние 10 цифр (в
 * базе номер лежит с кодом страны, а присылают его и без кода). Если точных
 * совпадений несколько (один номер писал в два канала) — выбор за человеком,
 * поэтому отдаём весь тир, а не первую строку.
 */
export const matchWazzupChatsByPhone = (items, phone) => {
    const digits = normalizePhoneDigits(phone);
    if (!digits) return [];
    const tail = digits.slice(-10);
    const exact = [];
    const loose = [];
    (items || []).forEach((it) => {
        if (!it) return;
        const chatDigits = normalizePhoneDigits(it.chatId);
        const contactDigits = normalizePhoneDigits(it.contactPhone);
        if (it.chatId === digits || chatDigits === digits || contactDigits === digits) { exact.push(it); return; }
        if ((chatDigits && chatDigits.endsWith(tail)) || (contactDigits && contactDigits.endsWith(tail))) loose.push(it);
    });
    return exact.length ? exact : loose;
};

/** Единственный подходящий номеру чат. Список отсортирован last_message_at DESC. */
export const pickWazzupChatByPhone = (items, phone) => matchWazzupChatsByPhone(items, phone)[0] || null;

/* Метки ЧУЖИХ разделов. Их снимает при уходе из раздела сам App
   (syncAppViewWithUrl), но ticket_id он не снимает нигде, а ссылку на чат
   собирают поверх текущего адреса: человек, зашедший в портал по ссылке бота
   ?view=crm_tickets&ticket_id=812, унёс бы этот номер обращения в ссылку на
   чат и отправил его в рабочую группу. */
const FOREIGN_QUERY_PARAMS = ['task_id', 'ticket_id', 'article'];

/** Ссылка на чат, которую можно скопировать и отправить. '' — если пара битая. */
export const buildWazzupChatLink = (chat) => {
    if (typeof window === 'undefined') return '';
    const target = formatWazzupChatTarget(chat);
    if (!target) return '';
    try {
        const url = new URL(window.location.href);
        // Метки перезагрузки в ссылку не переносим — см. utils/urlHygiene.js.
        stripTechnicalQueryParams(url);
        FOREIGN_QUERY_PARAMS.forEach((name) => url.searchParams.delete(name));
        url.searchParams.set(APP_VIEW_QUERY_PARAM, WAZZUP_CHATS_VIEW);
        url.searchParams.set(WAZZUP_CHAT_QUERY_PARAM, target);
        return url.toString();
    } catch (error) {
        return '';
    }
};

/** Открытый чат в адресной строке. Пустая цель — чат закрыли, метку убираем.
 *
 * replaceState, а не pushState: раздел чатов — не роутер, и «назад» в браузере
 * должно уводить туда, откуда человек пришёл в портал, а не отматывать
 * открытые чаты по одному.
 */
export const syncWazzupChatDeepLink = (chat) => {
    if (typeof window === 'undefined') return;
    try {
        const url = new URL(window.location.href);
        stripTechnicalQueryParams(url);
        const target = formatWazzupChatTarget(chat);
        if (target) {
            url.searchParams.set(APP_VIEW_QUERY_PARAM, WAZZUP_CHATS_VIEW);
            url.searchParams.set(WAZZUP_CHAT_QUERY_PARAM, target);
        } else {
            url.searchParams.delete(WAZZUP_CHAT_QUERY_PARAM);
        }
        window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
    } catch (error) {
        // В урезанных браузерных контекстах адресная строка недоступна — не беда.
    }
};
