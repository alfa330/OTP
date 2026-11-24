import React, { useCallback, useEffect, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { KIND_LABELS, STATUS_SHORT, STATUS_TONE, errorText, shortName } from './fixedAssetsMeta';
import {
    AssetTile, CARD_GRID, CardsSkeleton, EmptyState, ErrorNote, Group, PageTitle, StatusWord,
} from './fixedAssetsUi';

/*
 * «Мои ОС»: что числится за человеком (ТЗ: «Ответственный сотрудник (МОЛ) видит ОС на своё
 * имя») и что ему передают. Только просмотр: запросов сотрудник не заводит — их ведут
 * бухгалтерия, IT, HR и руководитель отдела. Что с ОС происходит, видно меткой на карточке.
 */
export default function FaMyAssets({ api, version, onOpenAsset, onOpenRequest }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');

    const load = useCallback(() => api.get('/assets', { scope: 'mine' })
        .then((response) => { setData(response.data); setError(''); })
        .catch((requestError) => setError(errorText(requestError, 'Список не загрузился'))), [api]);
    useEffect(() => { load(); }, [load, version]);

    if (error && !data) return <ErrorNote text={error} />;
    if (!data) return <CardsSkeleton count={4} />;

    const items = data.items || [];
    const incoming = data.incoming || [];
    return (
        <div className="space-y-7">
            {items.length > 0 && (
                <div style={CARD_GRID}>
                    {items.map((asset) => <AssetTile key={asset.id} asset={asset} onOpen={onOpenAsset} holder={false} />)}
                </div>
            )}
            {/* Пустой экран — только когда и передавать человеку нечего: иначе он оттеснял бы список вниз. */}
            {!items.length && !incoming.length && <EmptyState title="За вами нет основных средств" />}

            {incoming.length > 0 && (
                <section>
                    <PageTitle className="mb-3">Передаются вам</PageTitle>
                    <Group className="max-w-3xl">
                        {incoming.map((item) => (
                            <button key={`${item.request_id}-${item.asset_id}`} type="button" onClick={() => onOpenRequest(item.request_id)}
                                    className="flex w-full items-center gap-3 px-4 py-3 text-left transition first:rounded-t-2xl last:rounded-b-2xl hover:bg-slate-50">
                                <span className="min-w-0 flex-1">
                                    <span className="block truncate text-[14.5px] font-medium text-slate-900">{item.name}</span>
                                    <span className="block truncate text-[12.5px] text-slate-500">
                                        {item.inventory_number} · {KIND_LABELS[item.kind]} от {shortName(item.from_name) || '—'}
                                    </span>
                                </span>
                                <StatusWord tone={STATUS_TONE[item.status]} className="shrink-0">{STATUS_SHORT[item.status]}</StatusWord>
                                <ChevronRight size={16} className="shrink-0 text-slate-300" />
                            </button>
                        ))}
                    </Group>
                </section>
            )}
        </div>
    );
}
