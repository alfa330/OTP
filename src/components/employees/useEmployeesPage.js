import React from 'react';
import {
    EMPLOYEES_SEARCH_DELAY_MS,
    createPageLoader,
    employeesPageQuery,
    idsQueryOf,
    listKeyOf,
    pageOfResponse,
} from './employeesPaging';

const NO_PAGE = { listKey: null, data: null };

/*
 * Страница списка «Учета сотрудников» с сервера — одна на раздел: на экране
 * всегда один список.
 *
 * params — что выбрано на экране ({ list, roles, status, department, search,
 * sort, dir, group, page }) или null, когда списка на экране нет. load(query,
 * { signal }) — сам запрос (App.jsx: axios с заголовками портала). owner — id
 * вошедшего: страница чужой учётки (вышли и вошли другим в той же вкладке)
 * не показывается ни на кадр.
 *   • Поиск ждёт, пока человек перестанет печатать; очистка поля, смена списка
 *     и всё остальное — сразу.
 *   • Пока идёт запрос, на экране остаётся прежняя страница ТОГО ЖЕ списка, а
 *     не «Загрузка»; данные другого списка не показываются (ready = false).
 *     Ушли из списка — его страница забывается: при возвращении номер уже
 *     первый, и прошлая третья страница под ним была бы неправдой.
 *   • Когда запрос уходит, отменяется и повторяется — решает createPageLoader
 *     (employeesPaging.js): refreshStamp, прижатый номер, отмена прежнего.
 */
export default function useEmployeesPage({ load, owner, params, refreshStamp = 0, onPageMoved, onError }) {
    const listKey = listKeyOf(params, owner);
    const search = params ? String(params.search || '') : '';
    const [settled, setSettled] = React.useState({ listKey, search });
    // Пустой поиск и новый список — сразу, в этом же кадре: иначе со второй
    // страницы ушёл бы запрос первой со СТАРЫМ поиском.
    const settledSearch = settled.listKey !== listKey || !search.trim() ? search : settled.search;

    React.useEffect(() => {
        if (settled.listKey !== listKey || !search.trim()) {
            if (settled.listKey !== listKey || settled.search !== search) setSettled({ listKey, search });
            return undefined;
        }
        if (search === settled.search) return undefined;
        const timer = setTimeout(() => setSettled({ listKey, search }), EMPLOYEES_SEARCH_DELAY_MS);
        return () => clearTimeout(timer);
    }, [listKey, search, settled]);

    const query = params ? employeesPageQuery({ ...params, search: settledSearch }) : null;

    const [state, setState] = React.useState(NO_PAGE);
    const [loading, setLoading] = React.useState(false);
    // Список, чей последний запрос упал: вместо вечной «Загрузки» — честный текст.
    const [failedListKey, setFailedListKey] = React.useState(null);
    const callbacksRef = React.useRef({});
    callbacksRef.current = { load, onPageMoved, onError };
    const loaderRef = React.useRef(null);
    if (!loaderRef.current) {
        loaderRef.current = createPageLoader({
            load: (...args) => callbacksRef.current.load(...args),
            onData: (dataListKey, data) => {
                setState({ listKey: dataListKey, data });
                setFailedListKey(null);
            },
            onLoading: setLoading,
            onError: (error, errorListKey) => {
                setFailedListKey(errorListKey);
                callbacksRef.current.onError?.(error);
            },
            onPageMoved: (page) => callbacksRef.current.onPageMoved?.(page),
        });
    }

    // Человек ещё печатает: первая буква сбрасывает номер страницы, и без этой
    // паузы ушёл бы запрос первой страницы со СТАРЫМ поиском. Запрос уйдёт один,
    // когда поиск устоится.
    const typing = settled.listKey === listKey && search !== settled.search && Boolean(search.trim());

    React.useEffect(() => {
        if (typing) return;
        if (!query) setState(NO_PAGE);
        loaderRef.current.sync(query, listKey, refreshStamp);
    }, [query, refreshStamp, typing]); // eslint-disable-line react-hooks/exhaustive-deps

    React.useEffect(() => () => loaderRef.current.dispose(), []);

    // «Повторить» после сбоя — тот же запрос заново.
    const retry = React.useCallback(() => {
        if (query) loaderRef.current.retry(query, listKey);
    }, [query, listKey]);

    // «Выбрать всех» — все найденные: id одним запросом, без строк.
    const fetchIds = React.useCallback(async () => {
        if (!query) return [];
        const data = await callbacksRef.current.load(idsQueryOf(query), {});
        return (Array.isArray(data?.ids) ? data.ids : []).map(Number).filter(Number.isFinite);
    }, [query]);

    const ready = state.data != null && state.listKey === listKey;
    const page = React.useMemo(() => (ready ? pageOfResponse(state.data) : null), [ready, state.data]);
    const failed = !ready && !loading && listKey != null && failedListKey === listKey;
    return { page, ready, loading, failed, retry, fetchIds };
}
