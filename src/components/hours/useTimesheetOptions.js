import { useEffect, useRef, useState } from 'react';
import {
    pruneTimesheetFilters,
    sameTimesheetFilters,
    timesheetPeriodKey,
    timesheetSelectOptions,
} from './frontOfficeTimesheet';
import { fetchTimesheetOptions } from './frontOfficeTimesheetApi';

/*
 * Списки отбора табеля фронт-офисов (город, сотрудник, должность) за период.
 *
 * Отбор с 19.11.2026 стоит в двух местах — над табелем на экране и в окне
 * «Сформировать отчёт», — и это один и тот же отбор: что видно на экране, то и
 * уходит в файл. Списки поэтому грузит раздел один раз на период, а не каждое
 * место своим запросом. Пока ни табеля на экране, ни окна с табелем нет
 * (`enabled` = false), запроса нет; прежние списки остаются — открыл окно
 * снова, и они на месте. «Обновить» раздела (reloadKey) перечитывает.
 *
 * Сменился период — из отбора отпадает то, чего в новом периоде нет
 * (уволенные до него, города без людей), а не уходит в запрос невидимым.
 */
export default function useTimesheetOptions({
    enabled,
    period,
    apiBaseUrl,
    authHeaders,
    filters,
    onFiltersChange,
    reloadKey = 0,
}) {
    const [state, setState] = useState({ status: 'idle', error: '', payload: null, key: '' });
    const latest = useRef({ authHeaders, filters, onFiltersChange });
    latest.current = { authHeaders, filters, onFiltersChange };
    const loaded = useRef({ key: '', reloadKey: -1 });
    const key = enabled ? timesheetPeriodKey(period) : '';

    useEffect(() => {
        if (!key) return undefined;
        if (loaded.current.key === key && loaded.current.reloadKey === reloadKey) return undefined;
        const controller = new AbortController();
        const [from, to] = key.split('..');
        setState((previous) => ({ ...previous, status: 'loading', error: '' }));
        fetchTimesheetOptions({
            apiBaseUrl,
            headers: latest.current.authHeaders?.() || {},
            period: { from, to },
            signal: controller.signal,
        })
            .then((payload) => {
                loaded.current = { key, reloadKey };
                setState({ status: 'ready', error: '', payload, key });
                const current = latest.current.filters;
                const pruned = pruneTimesheetFilters(current, timesheetSelectOptions(payload));
                if (!sameTimesheetFilters(pruned, current)) latest.current.onFiltersChange?.(pruned);
            })
            .catch((error) => {
                if (error?.name === 'AbortError') return;
                setState({ status: 'error', error: error?.message || 'Не удалось загрузить списки', payload: null, key });
            });
        return () => controller.abort();
    }, [key, apiBaseUrl, reloadKey]);

    return state;
}
