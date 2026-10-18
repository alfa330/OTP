import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { createSearchCache, searchableText } from './chatSearch';

/* Блок «Сообщения» поиска: сообщения, в тексте которых есть запрос
 * (GET /api/wazzup/chat-search, свежие сверху, страницами по 30).
 *
 * В полёте всегда один запрос: новый ввод отменяет прежний, а ответ
 * отменённого или устаревшего (сменили аккаунт, очистили поиск) отбрасывается
 * по номеру. Первые страницы недавних запросов лежат в кэше вкладки
 * (chatSearch.js): стёртая буква возвращает найденное сразу. */
export const EMPTY_MESSAGE_SEARCH = Object.freeze({ query: '', items: [], next: null, loading: false, error: '' });

export default function useMessageSearch({ apiBaseUrl, headers, account }) {
    const [state, setState] = useState(EMPTY_MESSAGE_SEARCH);
    const request = useRef({ id: 0, controller: null });
    const cache = useRef(null);
    cache.current ||= createSearchCache();
    const latest = useRef({});
    latest.current = { apiBaseUrl, headers, account, state };

    const stop = () => {
        request.current.controller?.abort();
        request.current = { id: request.current.id + 1, controller: null };
        return request.current.id;
    };
    useEffect(() => () => { stop(); }, []);
    // Найденное принадлежит аккаунту: в другом аккаунте другой архив.
    useEffect(() => { cache.current.clear(); }, [account, apiBaseUrl]);

    const fetchPage = (query, before) => {
        const id = stop();
        const controller = new AbortController();
        request.current = { id, controller };
        const { apiBaseUrl: base, headers: head, account: target } = latest.current;
        return axios.get(`${base}/api/wazzup/chat-search`, {
            headers: head(), signal: controller.signal, timeout: 20000,
            params: { account: target, q: query, before: before || undefined },
        }).then(({ data }) => (request.current.id === id
            ? { items: data.items || [], next: data.next || null, key: `${target}\n${query}` } : null));
    };

    const failure = (error) => {
        if (axios.isCancel?.(error) || error?.name === 'CanceledError') return null;
        return error?.response?.data?.error || 'Не удалось найти сообщения. Попробуйте ещё раз.';
    };

    const search = useCallback((value) => {
        const query = searchableText(value);
        if (!query) {
            stop();
            setState(EMPTY_MESSAGE_SEARCH);
            return;
        }
        const key = `${latest.current.account}\n${query}`;
        const cached = cache.current.get(key);
        if (cached && !cached.stale) {
            stop();
            setState({ query, items: cached.items, next: cached.next, loading: false, error: '' });
            return;
        }
        // Устаревшее показываем сразу и тихо перечитываем: без мигания пустотой.
        setState(cached ? { query, items: cached.items, next: cached.next, loading: false, error: '' }
            : { query, items: [], next: null, loading: true, error: '' });
        fetchPage(query, null).then((page) => {
            if (!page) return;
            cache.current.set(page.key, { items: page.items, next: page.next });
            setState({ query, items: page.items, next: page.next, loading: false, error: '' });
        }).catch((error) => {
            const message = failure(error);
            if (message !== null) setState((current) => (current.query === query
                ? { ...current, loading: false, error: cached ? '' : message } : current));
        });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const more = useCallback(() => {
        const current = latest.current.state;
        if (!current.next || current.loading) return;
        const { query, next } = current;
        setState({ ...current, loading: true, error: '' });
        fetchPage(query, next).then((page) => {
            if (!page) return;
            setState((now) => (now.query === query && now.next === next
                ? { ...now, items: [...now.items, ...page.items], next: page.next, loading: false } : now));
        }).catch((error) => {
            const message = failure(error);
            if (message !== null) setState((now) => (now.query === query ? { ...now, loading: false, error: message } : now));
        });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const reset = useCallback(() => {
        stop();
        setState(EMPTY_MESSAGE_SEARCH);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return { ...state, search, more, reset };
}
