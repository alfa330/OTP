import { useMemo, useSyncExternalStore } from 'react';
import axios from 'axios';
import { classifyPilotSendFailure, newClientMessageId, pilotChatKey } from './chatPilot.js';

/* Исходящие сообщения режима обработки чатов — одна очередь на вкладку.
 *
 * Поле ввода отдаёт сообщение сюда и сразу свободно: пузырь с часами встаёт в
 * ленту в момент нажатия, а очередь отправляет сообщения чата по одному и
 * строго в порядке набора (разные чаты — параллельно). Раньше поле ждало ответа
 * сервера и Wazzup (0,4–0,7 с) только для чтения: буквы, набранные в эти доли
 * секунды, терялись, а следующее сообщение нельзя было начать.
 *
 * Повтор безопасен по построению. У каждого сообщения свой clientMessageId;
 * сервер записывает его в wazzup_pilot_outbox ДО обращения к Wazzup и на
 * повторный запрос с тем же id Wazzup уже не зовёт (wazzup/pilot.py, replay).
 * Поэтому потерянный ответ, перезагрузка вкладки и «Проверить» повторяют ТОТ
 * ЖЕ id, а новый id получает только «Повторить» после явного отказа Wazzup.
 *
 * Модуль без React-состояния намеренно: очередь переживает смену чата и
 * размонтирование раздела, а подписка идёт через useSyncExternalStore. */

const STORAGE_KEY = 'icore.wazzup.pilot.outbox.v1';
const LEGACY_DRAFT_PREFIX = 'icore.wazzup.pilot.draft.';
// Ответа нет вовсе или API перезапускается (502 на деплое): тот же id ещё раз.
export const RECHECK_DELAYS_MS = [1000, 2000, 4000, 8000];
// Сервер ждёт Wazzup не дольше 25 с; «ещё отправляется» дольше — зависшая попытка.
export const IN_PROGRESS_LIMIT_MS = 30000;
const IN_PROGRESS_POLL_MS = 1500;
// Принятое сервером сообщение уступает место строке архива; если та не пришла
// (чат закрыт, поток оборван), локальная копия всё равно уходит через 2 минуты.
const ACCEPTED_TTL_MS = 2 * 60 * 1000;
// Перезагрузка (F5) продолжает отправку сама. Вкладка, восстановленная через
// час (Ctrl+Shift+T, восстановление сессии браузера), — нет: оператор мог уже
// написать заново, и старое сообщение ушло бы клиенту без спроса.
export const RESUME_WINDOW_MS = 2 * 60 * 1000;
// Сбой, который так и не убрали, к следующему дню теряет смысл.
const PROBLEM_TTL_MS = 24 * 60 * 60 * 1000;
const SENDING = new Set(['queued', 'sending', 'checking']);
const PROBLEM = new Set(['failed', 'unknown']);

let api = null;                    // { apiBaseUrl, ownerId, headers }
let items = Object.freeze(load()); // новый массив на каждое изменение — это и есть снимок
const listeners = new Set();
const running = new Set();         // чаты, у которых запрос в полёте

export const chatKeyOf = (item) => pilotChatKey(item.account, item);

function load(now = Date.now()) {
    try {
        const saved = JSON.parse(globalThis.sessionStorage?.getItem(STORAGE_KEY) || '[]');
        if (!Array.isArray(saved)) return [];
        const age = (item) => now - (Date.parse(item.createdAt) || 0);
        return saved.filter((item) => item?.clientMessageId && item.account === 'op' && item.channelId && item.chatId
            && !(PROBLEM.has(item.state) && age(item) > PROBLEM_TTL_MS))
            .map((item) => {
                if (!SENDING.has(item.state)) return item;
                // Запрос, оборванный перезагрузкой, мог дойти до сервера, а мог и
                // нет: тот же id ещё раз, и ответит outbox. Давний — только по кнопке.
                return age(item) <= RESUME_WINDOW_MS ? { ...item, state: 'queued' }
                    : { ...item, state: 'unknown', error: 'Отправка прервалась. Проверьте переписку: сообщение могло уйти.' };
            });
    } catch {
        return [];
    }
}

function save() {
    try {
        const keep = items.filter((item) => item.state !== 'sent');
        if (keep.length) globalThis.sessionStorage?.setItem(STORAGE_KEY, JSON.stringify(keep));
        else globalThis.sessionStorage?.removeItem(STORAGE_KEY);
    } catch { /* Без хранилища (приватный режим) очередь живёт в памяти вкладки. */ }
}

function emit(next) {
    items = Object.freeze(next);
    save();
    listeners.forEach((listener) => listener());
}

const find = (id) => items.find((item) => item.clientMessageId === id);
const update = (id, patch) => emit(items.map((item) => (item.clientMessageId === id ? { ...item, ...patch } : item)));
const owned = (item) => Boolean(api && item && item.ownerId === api.ownerId && item.apiBaseUrl === api.apiBaseUrl);
const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

export const subscribeOutbox = (listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
};
export const outboxSnapshot = () => items;

/* Неясная отправка прежнего поля ввода лежала в его черновике (pending). Она
   переходит в очередь со СВОИМ id — сервер ответит, ушла ли она, — а черновик
   теряет её текст, чтобы Enter не отправил его второй раз под новым id. */
function adoptLegacyPending(authorName) {
    let storage;
    try { storage = globalThis.sessionStorage; } catch { return; }
    if (!storage || typeof storage.key !== 'function') return;
    const keys = [];
    for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        if (key?.startsWith(LEGACY_DRAFT_PREFIX)) keys.push(key);
    }
    for (const key of keys) {
        try {
            const saved = JSON.parse(storage.getItem(key) || 'null');
            const pending = saved?.pending;
            if (!pending?.clientMessageId || pending.account !== 'op' || !pending.channelId || !pending.chatId) continue;
            if (!find(pending.clientMessageId)) {
                const file = pending.attachmentId ? saved.attachment : null;
                emit([...items, {
                    clientMessageId: pending.clientMessageId, account: 'op',
                    channelId: pending.channelId, chatId: pending.chatId,
                    text: typeof pending.text === 'string' ? pending.text : '',
                    displayText: file ? file.name || 'Файл' : saved.preview || pending.text || '',
                    ...(pending.attachmentId ? { attachmentId: pending.attachmentId,
                        attachment: { name: file?.name || 'Файл', size: file?.size || 0, mime: file?.mime || '' } } : {}),
                    ...(pending.replyToMessageId ? { replyToMessageId: pending.replyToMessageId,
                        reply: saved.replyTo?.messageId === pending.replyToMessageId
                            ? { text: saved.replyTo.text || '', authorName: saved.replyTo.authorName || '' } : null } : {}),
                    authorName, ownerId: api.ownerId, apiBaseUrl: api.apiBaseUrl,
                    createdAt: new Date().toISOString(), state: 'queued', error: '',
                }]);
            }
            const rest = { ...saved, pending: null, replyTo: null };
            if (pending.attachmentId) rest.attachment = null;
            else { rest.text = ''; rest.preview = ''; }
            if (rest.text || rest.attachment) storage.setItem(key, JSON.stringify(rest));
            else storage.removeItem(key);
        } catch { /* Испорченный черновик не мешает остальным. */ }
    }
}

export function configureSendQueue({ apiBaseUrl, headers, ownerId, authorName = '' }) {
    const changed = !api || api.ownerId !== ownerId || api.apiBaseUrl !== apiBaseUrl;
    api = { apiBaseUrl, ownerId, headers: () => (typeof headers === 'function' ? headers() : headers) || {} };
    if (!changed || ownerId == null) return;
    // Во вкладку вошёл другой человек: его неотправленное под этой учёткой не уйдёт никогда.
    const mine = items.filter(owned);
    if (mine.length !== items.length) emit(mine);
    adoptLegacyPending(authorName);
    new Set(items.filter((item) => item.state === 'queued').map(chatKeyOf)).forEach(pump);
}

export function enqueueMessage(message) {
    if (!api || api.ownerId == null || !message?.clientMessageId || find(message.clientMessageId)) return null;
    const item = { ...message, account: 'op', ownerId: api.ownerId, apiBaseUrl: api.apiBaseUrl,
        createdAt: new Date().toISOString(), state: 'queued', error: '' };
    emit([...items, item]);
    pump(chatKeyOf(item));
    return item;
}

function pump(chatKey) {
    if (!api || running.has(chatKey)) return;
    const next = items.find((item) => item.state === 'queued' && owned(item) && chatKeyOf(item) === chatKey);
    if (!next) return;
    running.add(chatKey);
    deliver(next).catch(() => {}).finally(() => {
        running.delete(chatKey);
        pump(chatKey);
    });
}

async function deliver(item) {
    const id = item.clientMessageId;
    const payload = {
        account: 'op', channelId: item.channelId, chatId: item.chatId, text: item.text, clientMessageId: id,
        ...(item.attachmentId ? { attachmentId: item.attachmentId } : {}),
        ...(item.replyToMessageId ? { replyToMessageId: item.replyToMessageId } : {}),
    };
    let inProgressSince = null;
    let rechecks = 0;
    update(id, { state: 'sending', error: '' });
    for (;;) {
        // Каждая попытка — от имени владельца сообщения; сменился человек или
        // сообщение убрали, пока шла пауза, — больше ничего не отправляем.
        if (!owned(find(id))) return;
        try {
            const { data } = await axios.post(`${api.apiBaseUrl}/api/wazzup/pilot/send`, payload,
                { headers: api.headers(), timeout: 45000 });
            if (!owned(find(id))) return;
            if (data?.state === 'sent' && data.messageId) {
                update(id, { state: 'sent', messageId: String(data.messageId), acceptedAt: Date.now(), error: '' });
                return;
            }
            throw { response: { status: 409, data: { state: 'unknown' } } };
        } catch (error) {
            if (!owned(find(id))) return;
            const data = error?.response?.data || {};
            // Первая попытка ещё говорит с Wazzup (вкладку перезагрузили посреди
            // отправки или её ответ потерялся). Окно — от первого такого ответа.
            if (data.state === 'sending') {
                inProgressSince ??= Date.now();
                if (Date.now() - inProgressSince < IN_PROGRESS_LIMIT_MS) {
                    update(id, { state: 'checking' });
                    await sleep(IN_PROGRESS_POLL_MS);
                    continue;
                }
            }
            // Ответа нет вовсе или его дал шлюз (перезапуск на деплое). Ответ
            // самого приложения несёт state — это уже решение, а не потеря.
            const lost = !error?.response || ([502, 503, 504].includes(error.response.status) && !data.state);
            if (lost && rechecks < RECHECK_DELAYS_MS.length) {
                update(id, { state: 'checking' });
                await sleep(RECHECK_DELAYS_MS[rechecks]);
                rechecks += 1;
                continue;
            }
            const failure = classifyPilotSendFailure(error);
            update(id, { state: failure.state, error: failure.message });
            return;
        }
    }
}

/* «Проверить» у неясной отправки — тот же id: сервер ответит из outbox и
   второй раз в Wazzup не пойдёт. «Повторить» после отказа Wazzup — новое
   сообщение с новым id на месте прежнего пузыря. */
export function retryMessage(id) {
    const item = find(id);
    if (!owned(item)) return;
    if (item.state === 'unknown') {
        update(id, { state: 'queued', error: '' });
        pump(chatKeyOf(item));
    } else if (item.state === 'failed') {
        // В конец очереди — там же, где его нарисует лента: порядок отправки и
        // порядок на экране совпадают.
        const fresh = { ...item, clientMessageId: newClientMessageId(), state: 'queued', error: '',
            createdAt: new Date().toISOString() };
        emit([...items.filter((current) => current.clientMessageId !== id), fresh]);
        pump(chatKeyOf(fresh));
    }
}

/* «Изменить» у отклонённого текста: пузырь уходит, текст возвращается в поле
   (ChatPilotComposer, restore). Шаблон и файл так не вернуть — у них «Убрать». */
export function takeBackMessage(id) {
    const item = find(id);
    if (!owned(item) || item.state !== 'failed' || item.attachmentId || item.text !== item.displayText) return null;
    emit(items.filter((current) => current.clientMessageId !== id));
    return item.text;
}

export function discardMessage(id) {
    const item = find(id);
    if (owned(item) && (item.state === 'failed' || item.state === 'unknown')) {
        emit(items.filter((current) => current.clientMessageId !== id));
    }
}

/* Строка архива пришла (SSE или сверка) — локальная копия больше не нужна. Это
   касается и неясной отправки: строка с её id доказывает, что сообщение ушло. */
export function settleOutbox(serverMessages, now = Date.now()) {
    if (!items.some((item) => item.state === 'sent' || PROBLEM.has(item.state))) return;
    const ids = new Set();
    const clientIds = new Set();
    for (const message of serverMessages || []) {
        if (message?.messageId) ids.add(message.messageId);
        if (message?.clientMessageId) clientIds.add(message.clientMessageId);
    }
    const arrived = (item) => ids.has(item.messageId) || clientIds.has(item.clientMessageId);
    const next = items.filter((item) => {
        if (item.state === 'sent') return !arrived(item) && now - (item.acceptedAt || 0) < ACCEPTED_TTL_MS;
        return !(PROBLEM.has(item.state) && arrived(item));
    });
    if (next.length !== items.length) emit(next);
}

// Свои сбои по всем чатам — для отметки в списке чатов и одного уведомления.
export const outboxProblems = (all) => all.filter((item) => owned(item) && PROBLEM.has(item.state));

export function useOutbox() {
    return useSyncExternalStore(subscribeOutbox, outboxSnapshot, outboxSnapshot);
}

export function useChatOutbox(chatKey) {
    const all = useOutbox();
    return useMemo(() => (chatKey ? all.filter((item) => owned(item) && chatKeyOf(item) === chatKey) : []),
        [all, chatKey]);
}

// Только для тестов: чистое состояние модуля между сценариями.
export function resetSendQueueForTests(next = []) {
    api = null;
    running.clear();
    items = Object.freeze(next);
    listeners.forEach((listener) => listener());
}
