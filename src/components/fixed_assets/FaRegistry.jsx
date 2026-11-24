import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Archive, Boxes, Clock, Download, Loader2, Users, Warehouse } from 'lucide-react';
import {
    ASSET_FORMS, ASSET_SLICES, LIST_PAGE_SIZE, countText, errorText, saveBlob,
} from './fixedAssetsMeta';
import {
    AssetTile, CARD_GRID, CardsSkeleton, EmptyState, ErrorNote, FILL_BTN, FilterMenu, Pager, SEARCH_WIDTH, SearchField,
    StatTiles, useDebounced,
} from './fixedAssetsUi';

/*
 * «Реестр»: все ОС, которые видит человек. Бухгалтерия, HR, руководитель аналитики и IT — все
 * ОС компании (IT — без стоимости и истории: «наличие на складе ОС и МОЛ»), руководитель
 * отдела — ОС своего отдела. Плитки сверху — это и счётчики, и отбор; сами ОС — карточками.
 */
const SLICE_ICONS = { '': Boxes, in_use: Users, stock: Warehouse, busy: Clock, written_off: Archive };

export default function FaRegistry({ api, context, version, toast, onOpenAsset }) {
    const [search, setSearch] = useState('');
    const query = useDebounced(search.trim());
    const [slice, setSlice] = useState('');
    const [categoryId, setCategoryId] = useState('');
    const [departmentId, setDepartmentId] = useState('');
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [exporting, setExporting] = useState(false);

    /* Номер страницы помнится вместе с отбором: новый отбор начинается с первой. */
    const selection = useMemo(() => JSON.stringify([query, slice, categoryId, departmentId]), [query, slice, categoryId, departmentId]);
    const [paging, setPaging] = useState({ key: '', page: 1 });
    const page = paging.key === selection ? paging.page : 1;

    const ticket = useRef(0);
    const load = useCallback(async () => {
        const mine = ticket.current + 1;
        ticket.current = mine;
        setLoading(true);
        try {
            const params = { limit: LIST_PAGE_SIZE, offset: (page - 1) * LIST_PAGE_SIZE };
            if (query) params.q = query;
            if (slice) params.slice = slice;
            if (categoryId) params.category_id = categoryId;
            if (departmentId) params.department_id = departmentId;
            const response = await api.get('/assets', params);
            if (ticket.current !== mine) return;
            setData(response.data);
            setError('');
        } catch (requestError) {
            if (ticket.current !== mine) return;
            setError(errorText(requestError, 'Реестр не загрузился'));
        } finally {
            if (ticket.current === mine) setLoading(false);
        }
    }, [api, page, query, slice, categoryId, departmentId]);
    useEffect(() => { load(); }, [load, version]);

    const items = data?.items || [];
    const counters = data?.counters || {};
    const tiles = useMemo(() => ASSET_SLICES.map((item) => ({
        key: item.key, label: item.label, tone: item.tone, icon: SLICE_ICONS[item.key], count: counters[item.counter],
    })), [counters]);
    const categoryOptions = useMemo(() => (context.categories || []).map((item) => ({ value: item.id, label: item.name })), [context.categories]);
    const departmentOptions = useMemo(() => ((data?.facets?.departments) || []).map((item) => ({ value: item.id, label: item.name })), [data]);
    const narrowed = Boolean(query || slice || categoryId || departmentId);

    const exportXlsx = async () => {
        setExporting(true);
        try {
            saveBlob(await api.blob('/export'), 'Основные средства.xlsx');
        } catch (requestError) {
            toast(errorText(requestError, 'Выгрузка не собралась'), 'error');
        } finally {
            setExporting(false);
        }
    };

    return (
        <div className="space-y-4">
            <StatTiles ariaLabel="Срез реестра" value={slice} onChange={(key) => setSlice(key || '')} items={tiles} />

            <div className="flex flex-wrap items-center gap-2">
                <SearchField className={SEARCH_WIDTH} value={search} onChange={setSearch}
                             placeholder="Название, номер, сотрудник" ariaLabel="Поиск ОС" />
                <FilterMenu title="Категория" allLabel="Все категории" value={categoryId} options={categoryOptions} onChange={setCategoryId} />
                {departmentOptions.length > 1 && (
                    <FilterMenu title="Отдел" allLabel="Все отделы" value={departmentId} options={departmentOptions} onChange={setDepartmentId} />
                )}
                {context.capabilities?.sees_all && (
                    <button type="button" className={`${FILL_BTN} ml-auto`} onClick={exportXlsx} disabled={exporting}>
                        {exporting ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />} Excel
                    </button>
                )}
            </div>

            <ErrorNote text={error} />
            {!data && loading && <CardsSkeleton />}

            {items.length > 0 && (
                <div style={CARD_GRID}>
                    {items.map((asset) => <AssetTile key={asset.id} asset={asset} onOpen={onOpenAsset} />)}
                </div>
            )}

            {data && !items.length && !loading && (
                <EmptyState title={narrowed ? 'Ничего не нашлось' : 'Основных средств пока нет'}>
                    {narrowed ? 'Измените запрос или отбор.' : (context.capabilities?.can_intake ? 'Первую карточку заводит кнопка «Принять в учёт».' : null)}
                </EmptyState>
            )}

            <Pager page={page} pageSize={LIST_PAGE_SIZE} total={data?.total || 0} loading={loading && items.length > 0}
                   onPage={(next) => setPaging({ key: selection, page: next })}
                   unit={countText(data?.total || 0, ASSET_FORMS)} />
        </div>
    );
}
