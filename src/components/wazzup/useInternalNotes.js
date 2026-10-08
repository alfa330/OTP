import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';

export const internalNotesKey = (chat) => chat?.channelId && chat?.chatId
    ? JSON.stringify(['op', chat.channelId, chat.chatId]) : '';

export const mergeInternalNotes = (previous = [], incoming = []) => {
    const byId = new Map(previous.map((note) => [String(note.id), note]));
    for (const note of incoming) {
        if (note?.id == null || typeof note.text !== 'string' || !note.createdAt) continue;
        byId.set(String(note.id), { ...byId.get(String(note.id)), ...note });
    }
    return [...byId.values()].sort((a, b) => {
        const delta = new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime();
        return (Number.isFinite(delta) ? delta : 0) || String(a.id).localeCompare(String(b.id), undefined, { numeric: true });
    }).slice(-200);
};

export default function useInternalNotes({ enabled, chat, apiBaseUrl, headers }) {
    const key = enabled ? internalNotesKey(chat) : '';
    const channelId = chat?.channelId;
    const chatId = chat?.chatId;
    const latestRef = useRef({ key, headers });
    latestRef.current = { key, headers };
    const requestRef = useRef(null);
    const mountedRef = useRef(false);
    const [state, setState] = useState({ key: '', items: [], loading: false, error: '' });

    const refresh = useCallback(async () => {
        if (!key || !mountedRef.current || latestRef.current.key !== key) return;
        requestRef.current?.abort();
        const controller = new AbortController();
        requestRef.current = controller;
        setState((old) => ({ key, items: old.key === key ? old.items : [], loading: true, error: '' }));
        try {
            const auth = latestRef.current.headers;
            const { data } = await axios.get(`${apiBaseUrl}/api/wazzup/pilot/notes`, {
                params: { account: 'op', channelId, chatId },
                headers: typeof auth === 'function' ? auth() : auth,
                signal: controller.signal, timeout: 20000,
            });
            if (controller.signal.aborted || !mountedRef.current || latestRef.current.key !== key) return;
            if (!Array.isArray(data?.items)) throw new Error('Invalid notes response');
            // A live note may arrive while the snapshot is being read. Keep both,
            // deduplicating by the server ID, rather than replacing the live list.
            setState((old) => ({ key, items: mergeInternalNotes(old.key === key ? old.items : [], data.items), loading: false, error: '' }));
        } catch (error) {
            if (controller.signal.aborted || !mountedRef.current || latestRef.current.key !== key) return;
            setState((old) => ({ ...old, key, loading: false,
                error: error.response?.data?.error || 'Не удалось загрузить внутренние комментарии.' }));
        } finally {
            if (requestRef.current === controller) requestRef.current = null;
        }
    }, [key, channelId, chatId, apiBaseUrl]);

    const apply = useCallback((event) => {
        if (!key || !mountedRef.current || latestRef.current.key !== key
            || event?.account !== 'op' || event?.channelId !== channelId || event?.chatId !== chatId) return;
        const note = event.note || event;
        setState((old) => ({ ...old, key, items: mergeInternalNotes(old.key === key ? old.items : [], [note]) }));
    }, [key, channelId, chatId]);

    useEffect(() => {
        mountedRef.current = true;
        if (key) refresh();
        else setState({ key: '', items: [], loading: false, error: '' });
        return () => {
            mountedRef.current = false;
            requestRef.current?.abort();
            requestRef.current = null;
        };
    }, [key, refresh]);

    return {
        items: state.key === key ? state.items : [],
        loading: Boolean(key) && (state.key !== key || state.loading),
        error: state.key === key ? state.error : '', refresh, apply,
    };
}
