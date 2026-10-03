import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { dayPageRequest, itemKey, mergeDayPage } from './queueDayRules';
import { filtersKey, filtersToParams } from './filters';

/* Оценённое по дням — состояние вкладок «Звонки» и «Чаты».
 *
 * Живёт в CallQaView, а не в самой вкладке: пока открыта карточка разговора,
 * вкладка размонтирована, и без этого человек возвращался бы к списку дней, а
 * не на экран дня, где остановился. Так же устроена очередь ревью.
 *
 * Сводка дней — /evaluations/days, строки дня — /evaluations?day=… при
 * открытии дня. «Показать ещё» — с перекрытием и отсевом повторов
 * (dayPageRequest / mergeDayPage): за день могут добавиться оценки, и запрос
 * ровно со смещения «сколько загружено» пропустил бы строку. Поздние ответы
 * прежнего отдела, отбора или вида субъекта отсекает поколение.
 */
export default function useDayList({ apiBaseUrl, headers, department, filters, subject, enabled }) {
    const [days, setDays] = useState(null);          // null — не загружено
    const daysRef = useRef(days);
    daysRef.current = days;
    const [error, setError] = useState(false);
    const [dayItems, setDayItems] = useState({});
    const [openDay, setOpenDay] = useState(null);
    const generation = useRef(0);
    const daysRequest = useRef({ id: 0, controller: null });
    const dayRequest = useRef({});
    const signature = filtersKey(filters);
    const headersRef = useRef(headers);
    headersRef.current = headers;
    const filtersRef = useRef(filters);
    filtersRef.current = filters;

    const reset = useCallback(() => {
        daysRequest.current.controller?.abort();
        daysRequest.current = { id: daysRequest.current.id + 1, controller: null };
        generation.current += 1;
        setDays(null); setError(false); setDayItems({}); setOpenDay(null);
    }, []);

    const loadDays = useCallback(({ keepOpen = false } = {}) => {
        daysRequest.current.controller?.abort();
        if (!keepOpen) {
            generation.current += 1;
            setDayItems({});
        }
        if (!apiBaseUrl) { setDays([]); setError(true); return; }
        const controller = new AbortController();
        const requestId = daysRequest.current.id + 1;
        daysRequest.current = { id: requestId, controller };
        setError(false);
        if (!keepOpen) setDays(null);
        axios.get(`${apiBaseUrl}/api/ai-qa/evaluations/days`, {
            params: { subject, ...(department ? { department } : {}), ...filtersToParams(filtersRef.current) },
            headers: headersRef.current?.() || {}, signal: controller.signal,
        })
            .then((r) => {
                if (requestId !== daysRequest.current.id) return;
                setDays(r.data?.days || []);
            })
            .catch((e) => {
                if (axios.isCancel(e) || requestId !== daysRequest.current.id) return;
                // Фоновое обновление после карточки не удалось — остаёмся с
                // прежними днями: «Не удалось загрузить» вместо экрана дня, на
                // котором человек стоит, выбросило бы его из работы.
                if (keepOpen && daysRef.current !== null) return;
                setDays([]); setError(true);
            });
    }, [apiBaseUrl, department, subject]);

    /* Строки дня. fresh — день открыли заново: список читается с начала.
       Сколько уже загружено — из зеркала состояния: считать смещение внутри
       функции-обновителя setState нельзя, React вправе вызвать её дважды. */
    const itemsRef = useRef(dayItems);
    itemsRef.current = dayItems;
    /* keep — перечитать день, не убирая строки с экрана до ответа: так
       обновляется день, на который человек вернулся из карточки, — без мигания
       списка и без потери строки, куда встаёт фокус. */
    const loadDay = useCallback((day, { fresh = false, keep = false } = {}) => {
        if (!apiBaseUrl || !day) return;
        const gen = generation.current;
        const requestId = (dayRequest.current[day] || 0) + 1;
        dayRequest.current[day] = requestId;
        const loaded = fresh ? 0 : (itemsRef.current[day]?.items || []).length;
        const { offset, limit } = fresh && keep
            ? { offset: 0, limit: Math.max(50, Math.min(200, (itemsRef.current[day]?.items || []).length)) }
            : dayPageRequest(loaded);
        if (!keep) {
            setDayItems((map) => ({ ...map, [day]: { ...(fresh ? {} : map[day] || {}),
                                                     items: fresh ? [] : map[day]?.items || [],
                                                     loading: true, error: false } }));
        }
        const outdated = () => gen !== generation.current || requestId !== dayRequest.current[day];
        axios.get(`${apiBaseUrl}/api/ai-qa/evaluations`, {
            params: { day, subject, limit, offset, ...(department ? { department } : {}),
                      ...filtersToParams(filtersRef.current) },
            headers: headersRef.current?.() || {},
        })
            .then((r) => {
                if (outdated()) return;
                setDayItems((map) => ({ ...map, [day]: {
                    ...mergeDayPage(fresh ? [] : map[day]?.items, r.data?.items, offset, r.data?.total),
                    loading: false, error: false } }));
            })
            .catch(() => {
                if (outdated()) return;
                setDayItems((map) => ({ ...map, [day]: { ...(map[day] || {}), items: map[day]?.items || [],
                                                         loading: false, error: true } }));
            });
    }, [apiBaseUrl, department, subject]);

    const open = useCallback((day) => {
        if (!day) return;
        setOpenDay(day);
        loadDay(day, { fresh: true });
    }, [loadDay]);

    const close = useCallback(() => setOpenDay(null), []);

    /* Первая загрузка — когда вкладку впервые показали; смена отдела, отбора
       или вида субъекта — это новый список, а не догрузка прежнего. */
    const loadedFor = useRef(null);
    useEffect(() => {
        if (!enabled || department === null) return;
        const key = `${department}|${subject}|${signature}`;
        if (loadedFor.current === key && days !== null) return;
        loadedFor.current = key;
        setOpenDay(null);
        loadDays();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [enabled, department, subject, signature, loadDays]);

    /* Свежая оценка (подбор, «Из АТС», переоценка) — перечитать дни, не закрывая
       открытый день; строки дня перечитываются, если он открыт. */
    const refresh = useCallback(() => {
        loadDays({ keepOpen: true });
        if (openDay) loadDay(openDay, { fresh: true, keep: true });
    }, [loadDays, loadDay, openDay]);

    return {
        days, error, dayItems, openDay,
        open, close, loadDay, refresh, reset, reload: () => loadDays(),
        keyOf: itemKey,
    };
}
