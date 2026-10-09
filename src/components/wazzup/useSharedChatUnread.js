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

export default function useSharedChatUnread({ enabled, selected, apiBaseUrl, headers }) {
    const [items, setItems] = useState({});
    // Снимок пришёл: items — все ждущие ответа, а не только пришедшие по потоку.
    const [ready, setReady] = useState(false);
    const [error, setError] = useState('');
    const state = useRef({ items: {}, epoch: 0, events: 0, touched: {}, loading: null,
        controller: null, acknowledgements: new Map(), metadata: new Map() });
    const latest = useRef({});
    latest.current = { enabled, headers };
    const apply = (changes) => {
        if (!latest.current.enabled) return;
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
    return { items, ready, apply, refresh, markRead, error, total: Object.values(items).reduce((sum, row) => sum + row.unreadCount, 0) };
}
