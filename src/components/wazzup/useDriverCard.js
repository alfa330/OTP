import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { driverCardKey, mergeCardVersions, newClientId } from './driverCards';

/* Карточка водителя открытого чата: версии (свежая — текущая карточка),
 * право заполнять, сохранение и повтор записи в amoCRM.
 *
 * Живые обновления приходят отдельным кадром потока (`card`, wazzup/pilot.py) —
 * apply() вливает их сюда; кадр без содержимого (слишком большой или строка
 * не прочиталась) — повод перечитать.
 *
 * Сохранение помнит свой clientCardId, пока сервер не ответил: повтор после
 * таймаута сервер узнаёт по нему и второй версии не заводит. */
// Без живого потока (зритель без права обработки, связь оборвалась) итог записи
// в amoCRM не придёт кадром — его спрашиваем сами, пока версия «отправляется».
const PENDING_POLL_MS = 6000;
const PENDING_POLLS = 30;

export default function useDriverCard({ enabled, chat, apiBaseUrl, headers, live = false }) {
    const key = enabled ? driverCardKey(chat) : '';
    const channelId = chat?.channelId;
    const chatId = chat?.chatId;
    const latestRef = useRef({ key, headers });
    latestRef.current = { key, headers };
    const requestRef = useRef(null);
    const mountedRef = useRef(false);
    const pendingRef = useRef(null);
    const [state, setState] = useState({ key: '', items: [], canEdit: false, loading: false, error: '' });
    const auth = () => {
        const value = latestRef.current.headers;
        return typeof value === 'function' ? value() : value;
    };

    const refresh = useCallback(async () => {
        if (!key || !mountedRef.current || latestRef.current.key !== key) return;
        requestRef.current?.abort();
        const controller = new AbortController();
        requestRef.current = controller;
        // Другой чат — и право правки, и «сервер без карточек» узнаём заново: со
        // старым canEdit «Заполнить» горела бы до ответа и открывала пустую форму
        // поверх уже заполненной карточки.
        setState((old) => (old.key === key ? { ...old, loading: true, error: '' }
            : { key, items: [], canEdit: false, unavailable: false, loading: true, error: '' }));
        try {
            const { data } = await axios.get(`${apiBaseUrl}/api/wazzup/pilot/driver-card`, {
                params: { account: 'op', channelId, chatId }, headers: auth(),
                signal: controller.signal, timeout: 20000,
            });
            if (controller.signal.aborted || !mountedRef.current || latestRef.current.key !== key) return;
            if (!Array.isArray(data?.items)) throw new Error('Invalid driver card response');
            setState((old) => ({ key, loading: false, error: '', unavailable: false, canEdit: Boolean(data.canEdit),
                items: mergeCardVersions(old.key === key ? old.items : [], data.items) }));
        } catch (error) {
            if (controller.signal.aborted || !mountedRef.current || latestRef.current.key !== key) return;
            /* Портал выкатился раньше сервера (Pages быстрее Render): ручки ещё нет,
               и старый сервер отвечает HTML-страницей 404. Тогда карточки просто не
               видно — как до выкладки, а не ошибка в каждом чате. «Чат не найден»
               нашей ручки — JSON, его показываем. */
            const missingRoute = error.response?.status === 404 && typeof error.response?.data?.error !== 'string';
            setState((old) => ({ ...old, key, loading: false, unavailable: missingRoute,
                error: missingRoute ? '' : error.response?.data?.error || 'Не удалось загрузить карточку водителя' }));
        } finally {
            if (requestRef.current === controller) requestRef.current = null;
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [key, channelId, chatId, apiBaseUrl]);

    const apply = useCallback((event) => {
        if (!key || !mountedRef.current || latestRef.current.key !== key
            || event?.account !== 'op' || event?.channelId !== channelId || event?.chatId !== chatId) return false;
        if (!event.card) { refresh(); return true; }
        setState((old) => ({ ...old, key, items: mergeCardVersions(old.key === key ? old.items : [], [event.card]) }));
        return true;
    }, [key, channelId, chatId, refresh]);

    const remember = (item) => {
        if (!mountedRef.current || latestRef.current.key !== key) return;
        setState((old) => ({ ...old, key, items: mergeCardVersions(old.key === key ? old.items : [], [item]) }));
    };

    /* values: { driverId, parkId, cityId }. Отдаёт сохранённую версию или
       бросает ошибку с .message для человека и .uncertain, если ответ не
       получен (тогда тот же clientCardId уйдёт и при повторе). */
    const save = useCallback(async (values) => {
        if (!key) throw new Error('Чат не выбран');
        const same = pendingRef.current && pendingRef.current.key === key
            && pendingRef.current.driverId === (values.driverId || null)
            && pendingRef.current.parkId === (values.parkId ?? null)
            && pendingRef.current.cityId === (values.cityId ?? null);
        const attempt = same ? pendingRef.current : {
            key, clientCardId: newClientId(), driverId: values.driverId || null,
            parkId: values.parkId ?? null, cityId: values.cityId ?? null,
        };
        pendingRef.current = attempt;
        try {
            const { data } = await axios.post(`${apiBaseUrl}/api/wazzup/pilot/driver-card`, {
                account: 'op', channelId, chatId, clientCardId: attempt.clientCardId,
                driverId: attempt.driverId, parkId: attempt.parkId, cityId: attempt.cityId,
            }, { headers: auth(), timeout: 20000 });
            if (!data?.item?.id) throw new Error('Invalid driver card response');
            if (pendingRef.current === attempt) pendingRef.current = null;
            remember(data.item);
            return data.item;
        } catch (error) {
            const status = error.response?.status;
            const uncertain = !status || status >= 500 || status === 408;
            if (!uncertain && pendingRef.current === attempt) pendingRef.current = null;
            const failure = new Error(error.response?.data?.error || (uncertain
                ? 'Ответ сервера не получен. Сохраните ещё раз — вторая версия не появится.'
                : 'Не удалось сохранить карточку'));
            failure.uncertain = uncertain;
            failure.field = error.response?.data?.field || '';
            throw failure;
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [key, channelId, chatId, apiBaseUrl]);

    const retry = useCallback(async (cardId) => {
        try {
            const { data } = await axios.post(`${apiBaseUrl}/api/wazzup/pilot/driver-card/retry`, {
                account: 'op', channelId, chatId, cardId,
            }, { headers: auth(), timeout: 20000 });
            if (data?.item?.id) remember(data.item);
            return data?.item || null;
        } catch (error) {
            if (error.response?.data?.code === 'STALE_CARD') refresh();
            throw new Error(error.response?.data?.error || 'Не удалось поставить запись в очередь');
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [key, channelId, chatId, apiBaseUrl, refresh]);

    useEffect(() => {
        mountedRef.current = true;
        if (key) refresh();
        else setState({ key: '', items: [], canEdit: false, loading: false, error: '' });
        return () => {
            mountedRef.current = false;
            requestRef.current?.abort();
            requestRef.current = null;
        };
    }, [key, refresh]);

    const items = state.key === key ? state.items : [];
    const pendingId = items[0]?.amo?.state === 'pending' ? items[0].id : null;
    const polls = useRef({ id: null, count: 0 });
    useEffect(() => {
        if (!key || live || !pendingId) return undefined;
        if (polls.current.id !== pendingId) polls.current = { id: pendingId, count: 0 };
        if (polls.current.count >= PENDING_POLLS) return undefined;
        const timer = setTimeout(() => {
            polls.current.count += 1;
            refresh();
        }, PENDING_POLL_MS);
        return () => clearTimeout(timer);
        // state.items меняется с каждым ответом опроса — он и заводит следующий.
    }, [key, live, pendingId, state.items, refresh]);

    return {
        items, current: items[0] || null,
        canEdit: state.key === key && state.canEdit,
        unavailable: state.key === key && Boolean(state.unavailable),
        loading: Boolean(key) && (state.key !== key || state.loading),
        error: state.key === key ? state.error : '',
        refresh, apply, save, retry,
    };
}

/* Данные водителя из CRM (wazzup/driver_info.py, просьба владельца 16.10.2026).
 *
 * lookupDriver — по ID, который заполняющий только что вставил в форму: кто
 * это и что подставить в «Таксопарк» и «Город». useDriverInfo — по ID текущей
 * карточки чата, для всех, кому открыта переписка: сервер берёт ID из самой
 * карточки. Ответ: { state: found | not_found | unavailable, info, suggestion,
 * fetchedAt, stale, error }. */
const authOf = (headers) => (typeof headers === 'function' ? headers() : headers);

export async function lookupDriver({ apiBaseUrl, headers, chat, driverId, signal }) {
    try {
        const { data } = await axios.post(`${apiBaseUrl}/api/wazzup/pilot/driver-card/lookup`, {
            account: 'op', channelId: chat?.channelId, chatId: chat?.chatId, driverId,
        }, { headers: authOf(headers), signal, timeout: 15000 });
        if (typeof data?.state !== 'string') throw new Error('Invalid driver lookup response');
        return data;
    } catch (error) {
        if (signal?.aborted) throw error;
        // Сервер без этой ручки (сайт выкатился раньше) — как «CRM недоступна».
        const failure = new Error(typeof error.response?.data?.error === 'string'
            ? error.response.data.error : 'CRM не ответила');
        failure.status = error.response?.status || 0;
        throw failure;
    }
}

export function useDriverInfo({ chat, driverId, apiBaseUrl, headers }) {
    const key = chat?.channelId && chat?.chatId && driverId
        ? JSON.stringify([chat.channelId, chat.chatId, driverId]) : '';
    const headersRef = useRef(headers);
    headersRef.current = headers;
    const [state, setState] = useState({ key: '', data: null, error: '', hidden: false, loading: false });
    const [attempt, setAttempt] = useState(0);

    useEffect(() => {
        if (!key) return undefined;
        const controller = new AbortController();
        setState((old) => (old.key === key ? { ...old, loading: true, error: '' }
            : { key, data: null, error: '', hidden: false, loading: true }));
        axios.get(`${apiBaseUrl}/api/wazzup/pilot/driver-card/driver`, {
            params: { account: 'op', channelId: chat.channelId, chatId: chat.chatId },
            headers: authOf(headersRef.current), signal: controller.signal, timeout: 15000,
        }).then(({ data }) => {
            if (controller.signal.aborted) return;
            if (typeof data?.state !== 'string') throw new Error('Invalid driver info response');
            setState({ key, data, error: '', hidden: false, loading: false });
        }).catch((error) => {
            if (controller.signal.aborted) return;
            // Ручки ещё нет (HTML 404 старого сервера) или в карточке уже нет ID —
            // строки данных просто нет, как до этой доработки.
            const response = error.response;
            const hidden = response?.status === 404
                && (typeof response.data?.error !== 'string' || response.data?.code === 'NO_DRIVER_ID');
            setState({ key, data: null, hidden, loading: false,
                error: hidden ? '' : response?.data?.error || 'Данные водителя из CRM не загрузились' });
        });
        return () => controller.abort();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [key, apiBaseUrl, attempt]);

    const own = state.key === key;
    return {
        data: own ? state.data : null,
        error: own ? state.error : '',
        hidden: !key || (own && state.hidden),
        loading: Boolean(key) && (!own || state.loading),
        reload: () => setAttempt((value) => value + 1),
    };
}

/* Справочники amoCRM для формы — один запрос на вкладку и адрес API, не на
 * каждый чат: списки меняются раз в несколько недель. Неудача не кешируется. */
const optionsCache = new Map();

export function loadDriverCardOptions({ apiBaseUrl, headers }) {
    const cached = optionsCache.get(apiBaseUrl);
    if (cached && (cached.data || cached.promise) && Date.now() - cached.at < 30 * 60 * 1000) {
        return cached.data ? Promise.resolve(cached.data) : cached.promise;
    }
    const entry = { at: Date.now(), data: null, promise: null };
    entry.promise = axios.get(`${apiBaseUrl}/api/wazzup/pilot/driver-card/options`, {
        params: { account: 'op' }, headers: typeof headers === 'function' ? headers() : headers, timeout: 20000,
    }).then(({ data }) => {
        if (!Array.isArray(data?.parks) || !Array.isArray(data?.cities)) throw new Error('Invalid options');
        entry.data = { parks: data.parks, cities: data.cities };
        entry.promise = null;
        return entry.data;
    }).catch((error) => {
        if (optionsCache.get(apiBaseUrl) === entry) optionsCache.delete(apiBaseUrl);
        throw new Error(error.response?.data?.error || 'Списки таксопарков и городов не загрузились');
    });
    optionsCache.set(apiBaseUrl, entry);
    return entry.promise;
}

export const resetDriverCardOptions = () => optionsCache.clear();
