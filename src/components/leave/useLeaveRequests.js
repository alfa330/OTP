import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LEAVE_SICK_BACK_DAYS, leaveApiError } from './leaveRequests';

/*
 * Данные заявок на отпуск, Б/С и больничный для «Графиков работы» (задачи #358, #346).
 *
 * Два хука на две стороны: оператору — свои заявки и доступные виды, СВ и
 * главе — очередь со счётчиком «ждёт вас». Оба перечитываются тихо (без
 * заготовки «Загружаем…»), если данные уже есть: иначе каждое действие на
 * мгновение опустошало бы список.
 */

const useLatestRequest = () => {
    const seq = useRef(0);
    return () => {
        seq.current += 1;
        const mine = seq.current;
        return () => mine === seq.current;
    };
};

export function useMyLeaveRequests({ enabled, api }) {
    const [payload, setPayload] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const nextRequest = useLatestRequest();

    const reload = useCallback(async ({ silent = false } = {}) => {
        if (!enabled || !api) return null;
        const isLatest = nextRequest();
        if (!silent) setLoading(true);
        try {
            const data = await api.mine();
            if (!isLatest()) return null;
            setPayload(data);
            setError('');
            return data;
        } catch (err) {
            if (isLatest() && !silent) setError(leaveApiError(err, 'Не удалось загрузить заявки'));
            return null;
        } finally {
            if (isLatest() && !silent) setLoading(false);
        }
        // nextRequest стабилен по смыслу: счётчик живёт в ref.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [enabled, api]);

    useEffect(() => {
        if (enabled) reload();
    }, [enabled, reload]);

    const requests = useMemo(() => (Array.isArray(payload?.requests) ? payload.requests : []), [payload]);
    return {
        payload,
        requests,
        kinds: Array.isArray(payload?.kinds) ? payload.kinds : [],
        canFile: Boolean(payload?.enabled),
        sickBackDays: Number(payload?.sickBackDays) || LEAVE_SICK_BACK_DAYS,
        today: payload?.today || '',
        attentionCount: Number(payload?.attentionCount) || 0,
        unseenCount: requests.filter((item) => item.unseen).length,
        loading,
        error,
        setError,
        reload,
        replace: useCallback((item) => setPayload((prev) => {
            if (!prev || !item) return prev;
            const list = Array.isArray(prev.requests) ? prev.requests : [];
            const exists = list.some((it) => it.id === item.id);
            return { ...prev, requests: exists ? list.map((it) => (it.id === item.id ? item : it)) : [item, ...list] };
        }), []),
    };
}

export function useLeaveReview({ enabled, api, statusFilter = 'pending' }) {
    const [payload, setPayload] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const nextRequest = useLatestRequest();

    const reload = useCallback(async ({ silent = false } = {}) => {
        if (!enabled || !api) return null;
        const isLatest = nextRequest();
        if (!silent) setLoading(true);
        try {
            const data = await api.review(statusFilter);
            if (!isLatest()) return null;
            setPayload(data);
            setError('');
            return data;
        } catch (err) {
            if (isLatest()) setError(leaveApiError(err, 'Не удалось загрузить заявки'));
            return null;
        } finally {
            if (isLatest() && !silent) setLoading(false);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [enabled, api, statusFilter]);

    useEffect(() => {
        if (enabled) reload();
    }, [enabled, reload]);

    return {
        payload,
        requests: Array.isArray(payload?.requests) ? payload.requests : [],
        // Вкладка нужна, только если процесс включён в отделе смотрящего (или
        // он админ — ему настраивать), либо в его периметре уже есть заявки.
        visible: Boolean(payload?.enabled) || (Array.isArray(payload?.requests) && payload.requests.length > 0),
        pendingCount: Number(payload?.pendingCount) || 0,
        // Виды, которые приходят в очередь: по ним она называет себя.
        kinds: Array.isArray(payload?.kinds) ? payload.kinds : [],
        canManageSettings: Boolean(payload?.canManageSettings),
        today: payload?.today || '',
        loading,
        error,
        setError,
        reload,
    };
}
