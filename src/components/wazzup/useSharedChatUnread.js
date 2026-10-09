import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { pilotChatKey } from './chatPilot';

export function mergeUnread(previous, items) {
    const next = { ...previous };
    for (const item of items) {
        if (!Number.isFinite(item.unreadVersion)) continue;
        const key = pilotChatKey('op', item);
        if ((next[key]?.unreadVersion ?? -1) > item.unreadVersion) continue;
        next[key] = { ...next[key], ...item };
    }
    return next;
}

export function reconcileUnreadSnapshot(rows, current, touched, startedAt) {
    const snapshot = mergeUnread({}, rows);
    for (const [key, item] of Object.entries(current)) {
        // A later-arriving event is not necessarily newer than the snapshot:
        // HTTP can already contain revision 3 while SSE just delivered 2.
        if ((touched[key] || 0) > startedAt && (snapshot[key]?.unreadVersion ?? -1) <= item.unreadVersion) {
            snapshot[key] = { ...snapshot[key], ...item };
        }
    }
    return snapshot;
}

/* Ответ в полёте. Сервер закрывает «ждёт ответа», когда Wazzup подтвердил отправку
 * (статус sent, wazzup/unread.py), — через несколько секунд после нажатия. Всё это
 * время кнопка «Ответ не нужен», счётчик строки и «Ожидают ответа» говорили бы, что
 * клиенту не ответили. Поэтому ответ, вставший в очередь вкладки (sendQueue.js) или
 * принятый Wazzup (строка архива pending), гасит ожидание чата сразу: у отправителя
 * в момент нажатия, у остальных по живому потоку. Правило сервера не меняется: новая
 * версия строки (закрыто, клиент написал ещё) сразу заменяет подмену, отказ отправки
 * или статус error её снимает, а без подтверждения она гаснет сама через минуту. */
const REPLY_HOLD_MS = 60000;
const REPLY_STATUSES = new Set(['pending', 'sent', 'delivered', 'read']);
const OUTBOX_REPLYING = new Set(['queued', 'sending', 'checking', 'sent']);

export function presentUnread(items, replies, now) {
    let shown = items;
    for (const [key, reply] of replies) {
        const row = items[key];
        if (!(row?.unreadCount > 0) || row.unreadVersion !== reply.version || reply.expiresAt <= now
            || ![...reply.refs.values()].includes(true)) continue;
        if (shown === items) shown = { ...items };
        shown[key] = { ...row, unreadCount: 0 };
    }
    return shown;
}

/* Отправка ref ответа в чате key жива (active) или нет. Подмену заводит только начало
   ответа (start) в чате, который сейчас ждёт; поздние статусы обновляют лишь уже
   известные отправки. Подмена прежней версии строки забывается. true — изменилась. */
export function holdReply(replies, items, key, ref, { active, start = false, now }) {
    const row = items[key];
    let reply = replies.get(key);
    if (reply && row?.unreadVersion !== reply.version) {
        replies.delete(key);
        reply = undefined;
    }
    if (!reply) {
        if (!start || !active || !(row?.unreadCount > 0)) return false;
        reply = { version: row.unreadVersion, refs: new Map(), expiresAt: 0 };
        replies.set(key, reply);
    } else if (!start && !reply.refs.has(ref)) return false;
    if (!active && reply.refs.get(ref) === false) return false;
    reply.refs.set(ref, active);
    if (active) reply.expiresAt = now + REPLY_HOLD_MS;
    return true;
}

// Живой поток: строка архива с ответом сотрудника (pending) — начало ответа; статусы
// его отправки после — живая она (pending/sent/delivered/read) или нет (error).
export function replySignals(changes) {
    const signals = [];
    for (const event of changes || []) {
        if (!event?.messageId || event.kind || event.isEcho === false) continue;
        const message = event.message;
        const status = event.status ?? message?.status;
        if (!status) continue;
        const ref = `m:${event.messageId}`;
        const key = pilotChatKey('op', event);
        const reply = message?.isEcho === true && !message.isDeleted
            && Boolean(message.clientMessageId || String(message.authorId || '').trim()
                || String(message.authorName || '').trim());
        signals.push({ key, ref, active: REPLY_STATUSES.has(status) && !message?.isDeleted,
            start: event.statusOnly !== true && reply && status === 'pending' });
    }
    return signals;
}

// Очередь вкладки: ответ встал в очередь — начало; принят — добавляется id сообщения
// (по нему придёт error); отказ или неясный исход — отправка не живая.
export function outboxSignals(outbox, seen) {
    const signals = [];
    const present = new Set();
    for (const item of outbox || []) {
        if (item?.account !== 'op' || !item.clientMessageId) continue;
        present.add(item.clientMessageId);
        const before = seen.get(item.clientMessageId);
        if (before && before.state === item.state && before.messageId === item.messageId) continue;
        seen.set(item.clientMessageId, { state: item.state, messageId: item.messageId });
        const key = pilotChatKey('op', item);
        const active = OUTBOX_REPLYING.has(item.state);
        // Принятый ответ дальше ведёт id сообщения: по нему придут sent или error.
        signals.push({ key, ref: `c:${item.clientMessageId}`, active: active && !item.messageId, start: !before });
        if (item.messageId) signals.push({ key, ref: `m:${item.messageId}`, active, start: true });
    }
    for (const id of [...seen.keys()]) if (!present.has(id)) seen.delete(id);
    return signals;
}

export default function useSharedChatUnread({ enabled, selected, apiBaseUrl, headers, outbox }) {
    const [items, setItems] = useState({});
    // Снимок пришёл: items — все ждущие ответа, а не только пришедшие по потоку.
    const [ready, setReady] = useState(false);
    const [error, setError] = useState('');
    const [replyTick, setReplyTick] = useState(0);
    const state = useRef({ items: {}, epoch: 0, events: 0, touched: {}, loading: null,
        controller: null, acknowledgements: new Map(), metadata: new Map(),
        replies: new Map(), outboxSeen: new Map(), shown: null });
    const latest = useRef({});
    latest.current = { enabled, headers };
    const holdReplies = (signals) => {
        if (!latest.current.enabled || !signals.length) return;
        const now = Date.now();
        let changed = false;
        for (const { key, ref, active, start } of signals) {
            changed = holdReply(state.current.replies, state.current.items, key, ref, { active, start, now }) || changed;
        }
        if (changed) setReplyTick((tick) => tick + 1);
    };
    const apply = (changes) => {
        if (!latest.current.enabled) return;
        // До строк счётчика: ответ, пришедший в одной пачке с новым входящим, его не скроет.
        holdReplies(replySignals(changes));
        let next = state.current.items;
        const rows = changes.filter((item) => Number.isFinite(item.unreadVersion));
        // Message and unread notifications can arrive separately. Retain a
        // small recent metadata cache so a new unread chat can be shown even
        // when it is outside the currently loaded/filtered conversation list.
        for (const event of changes) {
            if (!event.chat) continue;
            const key = pilotChatKey('op', event);
            state.current.metadata.delete(key);
            state.current.metadata.set(key, event.chat);
            if (state.current.metadata.size > 1000) {
                state.current.metadata.delete(state.current.metadata.keys().next().value);
            }
            if (next[key]) {
                if (next === state.current.items) next = { ...next };
                next[key] = { ...next[key], chat: event.chat };
                state.current.touched[key] = ++state.current.events;
            }
        }
        for (const row of rows) {
            const key = pilotChatKey('op', row);
            if ((next[key]?.unreadVersion ?? -1) > row.unreadVersion) continue;
            const chat = row.chat || state.current.metadata.get(key) || next[key]?.chat;
            if (next === state.current.items) next = { ...next };
            next[key] = { ...next[key], ...row, ...(chat ? { chat } : {}) };
            state.current.touched[key] = ++state.current.events;
        }
        if (next === state.current.items) return;
        state.current.items = next;
        setItems(state.current.items);
    };
    const refresh = async () => {
        if (!latest.current.enabled) return;
        if (state.current.loading) return state.current.loading;
        const epoch = state.current.epoch;
        const start = state.current.events;
        const controller = new AbortController();
        state.current.controller = controller;
        const request = (async () => {
            const rows = [];
            let after;
            do {
                const { data } = await axios.get(`${apiBaseUrl}/api/wazzup/pilot/unread`, {
                    headers: latest.current.headers(), params: { account: 'op', after }, timeout: 15000,
                    signal: controller.signal,
                });
                if (epoch !== state.current.epoch) return;
                rows.push(...data.items);
                after = data.next;
            } while (after);
            const snapshot = reconcileUnreadSnapshot(rows, state.current.items, state.current.touched, start);
            state.current.items = snapshot;
            // Keep only revisions that the next reconciliation can encounter.
            state.current.touched = Object.fromEntries(Object.keys(snapshot).map((key) => [key, state.current.touched[key] || 0]));
            setItems(snapshot);
            setReady(true);
            setError('');
        })();
        state.current.loading = request;
        try { await request; } catch (e) {
            if (controller.signal.aborted) return;
            if (epoch === state.current.epoch) setError('Не удалось обновить чаты, ожидающие ответа');
            throw e;
        } finally { if (state.current.loading === request) state.current.loading = null; }
    };
    useEffect(() => {
        state.current.epoch += 1;
        state.current.loading = null;
        state.current.items = {};
        state.current.touched = {};
        state.current.metadata.clear();
        state.current.replies.clear();
        state.current.outboxSeen.clear();
        setItems({});
        setReady(false);
        setError('');
        return () => {
            state.current.epoch += 1;
            state.current.controller?.abort();
            for (const controller of state.current.acknowledgements.values()) controller.abort();
            state.current.acknowledgements.clear();
            state.current.loading = null;
        };
    }, [enabled, apiBaseUrl]);
    useEffect(() => { holdReplies(outboxSignals(outbox, state.current.outboxSeen)); }, [outbox, enabled, apiBaseUrl]);
    // Подмена без подтверждения гаснет сама; забытые (новая версия строки) — убираются.
    useEffect(() => {
        const now = Date.now();
        let next = Infinity;
        for (const [key, reply] of state.current.replies) {
            if (reply.expiresAt <= now || state.current.items[key]?.unreadVersion !== reply.version) {
                state.current.replies.delete(key);
            } else next = Math.min(next, reply.expiresAt);
        }
        if (!Number.isFinite(next)) return undefined;
        const timer = setTimeout(() => setReplyTick((tick) => tick + 1), next - now + 1);
        return () => clearTimeout(timer);
    }, [items, replyTick]);
    const markRead = async (chat = selected, seenMessageId) => {
        const row = state.current.items[pilotChatKey('op', chat)];
        if (!latest.current.enabled || !row?.unreadCount || (seenMessageId && row.lastInboundId !== seenMessageId)) return;
        const epoch = state.current.epoch;
        const ackKey = `${pilotChatKey('op', chat)}:${row.lastInboundId}`;
        if (state.current.acknowledgements.has(ackKey)) return;
        const controller = new AbortController();
        state.current.acknowledgements.set(ackKey, controller);
        try {
            const { data } = await axios.post(`${apiBaseUrl}/api/wazzup/pilot/read`, {
                account: 'op', channelId: chat.channelId, chatId: chat.chatId,
                seenMessageId: row.lastInboundId,
            }, { headers: latest.current.headers(), timeout: 15000, signal: controller.signal });
            if (epoch !== state.current.epoch) return;
            if (data.item) apply([data.item]);
            setError('');
        } catch {
            if (!controller.signal.aborted && epoch === state.current.epoch) setError('Не удалось сохранить отметку «Ответ не нужен»');
        } finally {
            if (state.current.acknowledgements.get(ackKey) === controller) state.current.acknowledgements.delete(ackKey);
        }
    };
    // Один и тот же объект, пока ничего не менялось: от него считаются порядок и фильтр списка.
    const memo = state.current.shown;
    const shown = memo?.items === items && memo.tick === replyTick ? memo.value
        : presentUnread(items, state.current.replies, Date.now());
    state.current.shown = { items, tick: replyTick, value: shown };
    return { items: shown, ready, apply, refresh, markRead, error,
        total: Object.values(shown).reduce((sum, row) => sum + row.unreadCount, 0) };
}
