import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ScrollText } from 'lucide-react';
import {
    EVENT_FORMS, EVENT_OPTIONS, HISTORY_PAGE_SIZE, PERIOD_OPTIONS, countText, errorText, periodRange,
} from './fixedAssetsMeta';
import { EmptyState, ErrorNote, FilterMenu, Pager, SEARCH_WIDTH, SearchField, useDebounced } from './fixedAssetsUi';
import FaTimeline from './FaTimeline';

/*
 * «История» — журнал раздела: кто, когда, что сделал и какие файлы приложил. Бухгалтерия, HR
 * и руководитель аналитики видят всё (ТЗ, «Доступ к истории»); остальные — записи, в которых
 * участвуют сами: сотрудник — то, что происходило с ОС при нём. Что кому отдать, решает
 * сервер; записи только читаются.
 */
export default function FaHistory({ api, version, onOpenAsset, onOpenRequest }) {
    const [search, setSearch] = useState('');
    const query = useDebounced(search.trim());
    const [kind, setKind] = useState('');
    const [period, setPeriod] = useState('');
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const selection = useMemo(() => JSON.stringify([query, kind, period]), [query, kind, period]);
    const [paging, setPaging] = useState({ key: '', page: 1 });
    const page = paging.key === selection ? paging.page : 1;

    const ticket = useRef(0);
    const load = useCallback(async () => {
        const mine = ticket.current + 1;
        ticket.current = mine;
        setLoading(true);
        try {
            const params = { limit: HISTORY_PAGE_SIZE, offset: (page - 1) * HISTORY_PAGE_SIZE, ...periodRange(period) };
            if (query) params.q = query;
            if (kind) params.kind = kind;
            const response = await api.get('/history', params);
            if (ticket.current !== mine) return;
            setData(response.data);
            setError('');
        } catch (requestError) {
            if (ticket.current !== mine) return;
            setError(errorText(requestError, 'История не загрузилась'));
        } finally {
            if (ticket.current === mine) setLoading(false);
        }
    }, [api, page, query, kind, period]);
    useEffect(() => { load(); }, [load, version]);

    const items = data?.items || [];
    const narrowed = Boolean(query || kind || period);
    return (
        <div className="max-w-4xl space-y-4">
            <div className="flex flex-wrap items-center gap-2">
                <SearchField className={SEARCH_WIDTH} value={search} onChange={setSearch}
                             placeholder="ОС, номер, сотрудник" ariaLabel="Поиск по истории" />
                <FilterMenu title="Событие" allLabel="Все события" value={kind} options={EVENT_OPTIONS} onChange={setKind} />
                <FilterMenu title="Период" allLabel="Всё время" value={period} options={PERIOD_OPTIONS} onChange={setPeriod} />
            </div>

            <ErrorNote text={error} />

            {items.length > 0 && <FaTimeline events={items} subject onOpenAsset={onOpenAsset} onOpenRequest={onOpenRequest} />}
            {data && !items.length && !loading && (
                <EmptyState icon={ScrollText} title={narrowed ? 'Ничего не нашлось' : 'Записей пока нет'}>
                    {narrowed ? 'Измените запрос или отбор.' : null}
                </EmptyState>
            )}

            <Pager page={page} pageSize={HISTORY_PAGE_SIZE} total={data?.total || 0} loading={loading && items.length > 0}
                   onPage={(next) => setPaging({ key: selection, page: next })}
                   unit={countText(data?.total || 0, EVENT_FORMS)} />
        </div>
    );
}
