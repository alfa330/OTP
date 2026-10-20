/*
 * «Чаты ОП» внутри чужой страницы — окно (iframe) в CRM группы «Поток».
 *
 * Постановка владельца 20.10.2026: «Поток» писал водителям из окна Wazzup в своей
 * CRM; номер +77070253322 теперь в нашем аккаунте, и окно CRM даёт «Чаты ОП».
 * У окна должны быть методы: кнопка на странице CRM открывает чат с этим человеком.
 *
 * Адрес окна: <портал>?view=wazzup_chats&embed=1 — портал рисует один раздел, без
 * меню (App.jsx). Вход — своей учёткой iCORE внутри окна, замок и правила те же.
 *
 * Команды от страницы, в которую встроено окно (window.postMessage):
 *   { type: 'icore.chats.open', phone: '+7 700 123 45 67', name?, channelId?, requestId? }
 *     — открыть чат с номером: свой чат номера, а если номеру ещё не писали —
 *       новый чат (как «+»), где первое сообщение отправит человек.
 * Ответы окна странице:
 *   { type: 'icore.chats.status', status: 'login' | 'locked' | 'ready' | 'no_access' }
 *   { type: 'icore.chats.opened', requestId, phone, channelId, chatId, isNew }
 *   { type: 'icore.chats.pending', requestId, reason: 'choose_channel' | 'several_chats' }
 *   { type: 'icore.chats.error', requestId, code, message }
 * Команда, пришедшая до входа или до QR, ждёт и выполняется, когда окно готово.
 *
 * Слушаем только window.parent — команду может дать лишь страница, в которую окно
 * встроено. Отправить сообщение за человека команда не может: окно лишь открывает
 * чат, первое сообщение отправляет он сам (то же правило, что у ИИ-помощника).
 */
import { normalizeNewChatPhone } from './newChat.js';

export const EMBED_QUERY_PARAM = 'embed';
export const EMBED_MESSAGE_PREFIX = 'icore.chats.';
const MAX_TEXT = 100;

/** Открыт ли портал окном чатов для CRM. Решает одна метка embed=1: вид в адресе
 *  портал может переписать сам (раздел недоступен учётке), а окно обязано остаться окном. */
export const isChatsEmbedSearch = (search) => {
    try {
        return new URLSearchParams(String(search || '')).get(EMBED_QUERY_PARAM) === '1';
    } catch (error) {
        return false;
    }
};

const cleanText = (value) => (typeof value === 'string' ? value.trim().slice(0, MAX_TEXT) : '');

/** Команда страницы → { command } или { error } (requestId сохраняется в обоих). */
export const parseEmbedCommand = (data) => {
    if (!data || typeof data !== 'object' || data.type !== `${EMBED_MESSAGE_PREFIX}open`) return null;
    const requestId = cleanText(typeof data.requestId === 'number' ? String(data.requestId) : data.requestId);
    const phone = normalizeNewChatPhone(typeof data.phone === 'number' ? String(data.phone) : data.phone);
    if (!phone) {
        return { requestId, error: { code: 'BAD_PHONE', message: 'Номер не распознан: нужен полный номер, например +7 700 123 45 67' } };
    }
    const channelId = cleanText(data.channelId);
    return { requestId, command: {
        phone, name: cleanText(data.name), requestId,
        channelId: /^[A-Za-z0-9-]{1,64}$/.test(channelId) ? channelId : '',
    } };
};

let target = null;          // { win, parent }
let parentOrigin = '*';     // откуда пришла последняя команда — туда и ответы с номером
let status = null;
let pending = null;
let handler = null;

const post = (message, origin = '*') => {
    if (!target) return;
    try {
        target.parent.postMessage(message, origin);
    } catch (error) {
        // Страница-хозяин ушла или запретила сообщения — окну это не мешает.
    }
};

/** Ответ странице на её команду. Номер и чат уходят только туда, откуда пришла команда. */
export const replyToEmbedParent = (type, payload = {}) => {
    post({ type: `${EMBED_MESSAGE_PREFIX}${type}`, ...payload }, parentOrigin);
};

/** Состояние окна: 'login' | 'locked' | 'ready' | 'no_access'. Шлётся только при смене. */
export const setEmbedStatus = (next) => {
    if (!target || !next || status === next) return;
    status = next;
    post({ type: `${EMBED_MESSAGE_PREFIX}status`, status: next });
};

/** Раздел принимает команды. Пришедшая раньше — выполняется сразу. Возвращает отписку. */
export const takeEmbedCommands = (onCommand) => {
    handler = onCommand;
    if (pending) {
        const command = pending;
        pending = null;
        onCommand(command);
    }
    return () => { if (handler === onCommand) handler = null; };
};

/** Команда из адреса окна (?chat=<номер>&name=…) — как от страницы, но при загрузке. */
export const queueEmbedCommand = (command) => {
    if (handler) handler(command);
    else pending = command;
};

/** Слушать команды страницы-хозяина. Один раз на окно; вне iframe — ничего. */
export const installEmbedBridge = (win = typeof window === 'undefined' ? null : window) => {
    if (target || !win || !win.parent || win.parent === win) return false;
    target = { win, parent: win.parent };
    win.addEventListener('message', (event) => {
        if (event.source !== target.parent) return;
        const parsed = parseEmbedCommand(event.data);
        if (!parsed) return;
        parentOrigin = event.origin && event.origin !== 'null' ? event.origin : '*';
        if (parsed.error) {
            replyToEmbedParent('error', { requestId: parsed.requestId, ...parsed.error });
            return;
        }
        queueEmbedCommand(parsed.command);
    });
    return true;
};

/** Только для тестов: вернуть мост в исходное состояние. */
export const resetEmbedBridgeForTests = () => {
    target = null; parentOrigin = '*'; status = null; pending = null; handler = null;
};
