import { useSyncExternalStore } from 'react';
import axios from 'axios';
import { newClientEventId } from './workspaceStatus';

/* Состояние рабочего места верификатора в «Чатах ОП» — одно на портал.
 *
 * Почему общее хранилище, а не состояние раздела. Смена идёт, пока открыт
 * портал, а не пока открыт раздел: верификатор уходит в «Вики» или «Мои смены»,
 * и раздел при этом размонтируется. Отметки «портал жив» обязаны идти и тогда —
 * иначе сервер через десять минут тишины закрыл бы смену работающему человеку
 * (wazzup/shift.py). Отметки шлёт WazzupShiftKeeper, смонтированный на весь
 * портал, а раздел читает то же состояние отсюда: второго запроса за тем же
 * самым нет, и статус, выбранный в разделе, сторож видит сразу.
 *
 * Модуль без React-состояния намеренно: это синглтон, а подписка идёт через
 * useSyncExternalStore. */

const EMPTY = Object.freeze({
    ready: false,          // сервер ответил хотя бы раз
    error: '',             // первая загрузка не удалась
    locked: true,          // доступ не подтверждён (скан + код из Telegram)
    statuses: [],
    startKey: '',          // статус, с которого начинается смена, и ключ её конца —
    logoutKey: '',         // оба знает сервер (wazzup/shift.py), копий здесь нет
    current: null,
    receivedAt: 0,         // когда пришёл current — от него досчитывается «сколько в статусе»
    heartbeatSeconds: 60,
    pending: false,        // статус сохраняется
    statusError: '',
});

let state = EMPTY;
let api = null;            // { apiBaseUrl, headers }
/* Номер последнего нажатия статуса: ответ отметки, отправленной ДО нажатия,
   несёт прежний статус и не должен перетереть только что выбранный. */
let statusEpoch = 0;
/* Смена человека в хранилище: ответ запроса прошлого владельца сессии не
   должен лечь в состояние следующего. */
let ownerEpoch = 0;
let loadInFlight = null;
let beatInFlight = null;
const listeners = new Set();

const emit = (patch) => {
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
};

const subscribe = (listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
};

const snapshot = () => state;

const errorText = (error, fallback) => error?.response?.data?.error || fallback;

const request = (method, path, body) => axios({
    method,
    url: `${api.apiBaseUrl}/api/wazzup/workspace${path}`,
    data: body,
    headers: api.headers(),
    timeout: 20000,
});

export const configureWorkspace = ({ apiBaseUrl, headers, ownerId }) => {
    if (api && (api.ownerId !== ownerId || api.apiBaseUrl !== apiBaseUrl)) resetWorkspace();
    api = { apiBaseUrl, ownerId, headers: () => (typeof headers === 'function' ? headers() : {}) || {} };
};

export const resetWorkspace = () => {
    ownerEpoch += 1;
    statusEpoch += 1;
    api = null;
    loadInFlight = null;
    beatInFlight = null;
    state = EMPTY;
    listeners.forEach((listener) => listener());
};

const applyShift = (shift) => {
    emit({
        ready: true,
        error: '',
        locked: false,
        statuses: shift?.statuses?.length ? shift.statuses : state.statuses,
        startKey: shift?.startKey || state.startKey,
        logoutKey: shift?.logoutKey || state.logoutKey,
        current: shift?.current || null,
        receivedAt: Date.now(),
        heartbeatSeconds: Number(shift?.heartbeatSeconds) || state.heartbeatSeconds,
    });
};

/* Режим раздела и текущая смена. Зовут и сторож при входе в портал, и раздел
   (пока ждёт подтверждения доступа — раз в несколько секунд). */
export const loadWorkspace = async () => {
    if (!api || state.pending) return;
    if (loadInFlight) return;
    const owner = ownerEpoch;
    const epoch = statusEpoch;
    const flight = { promise: request('get', '') };
    loadInFlight = flight;
    try {
        const { data } = await flight.promise;
        if (owner !== ownerEpoch || epoch !== statusEpoch) return;
        if (data.locked) {
            emit({ ready: true, error: '', locked: true, current: null });
            return;
        }
        applyShift(data.shift);
    } catch (error) {
        if (owner !== ownerEpoch || epoch !== statusEpoch) return;
        if ([401, 403].includes(error?.response?.status)) {
            emit({ ready: true, error: '', locked: true, current: null });
            return;
        }
        // Уже загруженное состояние ошибкой не затираем: раздел продолжает
        // работать, а следующая отметка попробует снова.
        if (!state.ready) emit({ error: errorText(error, 'Не удалось загрузить раздел') });
    } finally {
        if (loadInFlight === flight) loadInFlight = null;
    }
};

/* Отметка «портал открыт». Сервер продлевает ею идущую смену и заодно сообщает
   текущий статус: смену могли начать или закончить в другом окне портала. */
export const beatWorkspace = async () => {
    if (!api || !state.ready || state.locked || state.pending || beatInFlight) return;
    const owner = ownerEpoch;
    const epoch = statusEpoch;
    const flight = {};
    beatInFlight = flight;
    try {
        const { data } = await request('post', '/heartbeat', {});
        if (owner !== ownerEpoch || epoch !== statusEpoch) return;
        if (data.locked) emit({ locked: true, current: null });
        else emit({ locked: false, current: data.current || null, receivedAt: Date.now() });
    } catch (error) {
        // 403 — доступ закрыли (прервали сессию, перевели из группы): раздел
        // должен вернуться к экрану подтверждения, а не молчать.
        if (owner === ownerEpoch && epoch === statusEpoch && [401, 403].includes(error?.response?.status)) {
            emit({ locked: true, current: null });
        }
        // Остальное — сеть: следующая отметка через минуту, порог сторожа — десять.
    } finally {
        if (beatInFlight === flight) beatInFlight = null;
    }
};

/* Поставить статус. Возвращает true, когда сервер его записал. */
export const setWorkspaceStatus = async (key) => {
    if (!api || !state.ready || state.locked || state.pending) return false;
    const owner = ownerEpoch;
    statusEpoch += 1;
    emit({ pending: true, statusError: '' });
    try {
        const { data } = await request('post', '/status', {
            status_key: key, client_event_id: newClientEventId(),
        });
        if (owner !== ownerEpoch) return false;
        emit({ pending: false, current: data.current || null, receivedAt: Date.now() });
        return true;
    } catch (error) {
        if (owner !== ownerEpoch) return false;
        const locked = [401, 403].includes(error?.response?.status);
        emit({
            pending: false,
            statusError: errorText(error, 'Статус не сохранился, попробуйте ещё раз'),
            ...(locked ? { locked: true, current: null } : {}),
        });
        return false;
    }
};

export const clearWorkspaceStatusError = () => {
    if (state.statusError) emit({ statusError: '' });
};

/* Код для сканера. Ошибку отдаём исключением: показывает её экран доступа. */
export const requestWorkspaceQr = async () => {
    const { data } = await request('post', '/qr', {});
    return data;
};

export const useWorkspace = (ownerId) => {
    const value = useSyncExternalStore(subscribe, snapshot, snapshot);
    return ownerId !== undefined && ownerId !== api?.ownerId ? EMPTY : value;
};
