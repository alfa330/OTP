/* Журнал обращений раздела «Карточка клиента»: руководителю — обращения отдела,
 * оператору — свои (границу держит сервер, own_only приходит в ответе).
 *
 * Строка — одно обращение: когда, с какого номера, по какому аккаунту, причина,
 * комментарий. «Не заполнена» — единственное цветное состояние: заполненных
 * большинство, и красить их значило бы красить весь список.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ClipboardList, Search } from 'lucide-react';

import CustomSelect from '../ui/CustomSelect';
import IosDateRangePicker from '../ui/DateRangePicker';
import { IosBadge, IosSegmented, iosBtnSecondary, iosCard, iosInput } from '../ui/ios';
import CardDetail from './CardDetail';
import {
    REQUEST_TONES, almatyToday, cardAccountText, errorText, fmtWhen, periodPreset, reasonFilterOptions, topicLabel,
} from './cardRules';
import { EmptyState, Spinner, StateBadge } from './ui';

const STATE_OPTIONS = [
    { value: '', label: 'Все' },
    { value: 'done', label: 'Заполненные' },
    { value: 'pending', label: 'Не заполненные' },
];

export const periodPresets = () => {
    const today = almatyToday();
    return [
        { key: 'today', label: 'Сегодня', range: () => periodPreset('today', today) },
        { key: 'yesterday', label: 'Вчера', range: () => periodPreset('yesterday', today) },
        { key: 'week', label: 'Неделя', range: () => periodPreset('week', today) },
        { key: 'month', label: 'Этот месяц', range: () => periodPreset('month', today) },
        { key: 'prev_month', label: 'Прошлый месяц', range: () => periodPreset('prev_month', today) },
    ];
};

export default function JournalTab({ api, departmentId, categories, toast, onOpenRequest }) {
    const [range, setRange] = useState(() => periodPreset('week', almatyToday()));
    const [state, setState] = useState('');
    const [categoryId, setCategoryId] = useState('');
    const [query, setQuery] = useState('');
    const [search, setSearch] = useState('');
    const [data, setData] = useState(null);
    const [failed, setFailed] = useState('');
    const [openId, setOpenId] = useState(null);
    const [loadingMore, setLoadingMore] = useState(false);

    useEffect(() => {
        const timer = setTimeout(() => setSearch(query.trim()), 350);
        return () => clearTimeout(timer);
    }, [query]);

    const params = useMemo(() => ({
        department_id: departmentId, from: range.from, to: range.to, state: state || undefined,
        category_id: categoryId || undefined, q: search || undefined,
    }), [departmentId, range, state, categoryId, search]);

    const load = useCallback(async () => {
        try {
            const { data: body } = await api.journal(params);
            setData(body);
            setFailed('');
        } catch (error) {
            setFailed(errorText(error, 'Журнал не загрузился'));
        }
    }, [api, params]);

    useEffect(() => { setData(null); load(); }, [load]);

    const more = async () => {
        const last = data?.items?.[data.items.length - 1];
        if (!last || loadingMore) return;
        setLoadingMore(true);
        try {
            const { data: body } = await api.journal({ ...params, before: last.id });
            setData({ ...body, items: [...data.items, ...body.items] });
        } catch (error) {
            toast(errorText(error), 'error');
        } finally {
            setLoadingMore(false);
        }
    };

    // Причина любого уровня дерева: выбрали «Запрос» — в журнале и карточки всех его подтем.
    // У водителей, клиентов и третьих лиц причины свои — в подписи сказано, чья.
    const categoryOptions = useMemo(() => [
        { value: '', label: 'Все причины' },
        ...reasonFilterOptions(categories).map((item) => ({ value: String(item.id), label: item.label })),
    ], [categories]);

    return (
        <div className="space-y-3">
            <div className="flex flex-col gap-2.5 lg:flex-row lg:items-center">
                <IosDateRangePicker from={range.from} to={range.to} max={almatyToday()} presets={periodPresets()}
                                    onChange={({ from, to }) => { if (from && to) setRange({ from, to }); }} />
                <IosSegmented value={state} options={STATE_OPTIONS} onChange={setState} ariaLabel="Какие обращения показать" />
                <CustomSelect variant="ios" searchable value={categoryId} onChange={setCategoryId} options={categoryOptions}
                              className="lg:w-64" ariaLabel="Причина обращения" />
                <label className="relative block lg:flex-1">
                    <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                    <input value={query} onChange={(event) => setQuery(event.target.value)}
                           placeholder="Телефон, ID аккаунта, причина, текст" className={`${iosInput} bg-white pl-8 ring-1 ring-slate-200/70`} />
                </label>
            </div>

            {failed && (
                <EmptyState title={failed}>
                    <button type="button" onClick={load} className={`${iosBtnSecondary} mt-4`}>Повторить</button>
                </EmptyState>
            )}
            {!failed && !data && <Spinner text="Загружаем обращения…" />}
            {data && !data.items.length && (
                <EmptyState icon={ClipboardList} title={search || state || categoryId ? 'Ничего не нашлось' : 'Обращений за период нет'}
                            text={data.own_only ? 'Здесь ваши обращения: карточки звонков, которые открыл iCORE Phone.' : ''} />
            )}
            {data?.items.length > 0 && (
                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                    {data.items.map((card) => (
                        <button key={card.id} type="button" onClick={() => setOpenId(card.id)}
                                className="grid w-full grid-cols-1 gap-x-4 gap-y-1 px-4 py-3 text-left transition hover:bg-slate-50 md:grid-cols-[150px_minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,0.6fr)] md:items-center">
                            <div className="text-[12.5px] text-slate-500">
                                <div className="tabular-nums text-slate-800">{fmtWhen(card.created_at)}</div>
                                {!data.own_only && <div className="truncate">{card.operator_name}</div>}
                            </div>
                            <div className="min-w-0">
                                <div className="truncate text-[13.5px] font-medium text-slate-900">
                                    {cardAccountText(card, 'Аккаунт не указан')}
                                </div>
                                <div className="truncate text-[12px] tabular-nums text-slate-500">
                                    {card.direction_label} · {card.phone_text}{card.city ? ` · ${card.city}` : ''}
                                </div>
                            </div>
                            <div className="min-w-0">
                                <div className="truncate text-[13px] text-slate-800">
                                    {card.topics.length ? card.topics.map(topicLabel).join(' · ') : '—'}
                                </div>
                                {card.comment && <div className="truncate text-[12px] text-slate-500">{card.comment}</div>}
                            </div>
                            <div className="flex flex-wrap items-center gap-1.5 md:justify-end">
                                <StateBadge card={card} />
                                {card.requests.map((request) => (
                                    <IosBadge key={request.id} tone={REQUEST_TONES[request.status] || 'slate'}>
                                        {request.destination_title}
                                    </IosBadge>
                                ))}
                            </div>
                        </button>
                    ))}
                </div>
            )}
            {data?.has_more && (
                <button type="button" onClick={more} disabled={loadingMore} className={`${iosBtnSecondary} mx-auto flex`}>
                    {loadingMore ? 'Загружаем…' : 'Показать ещё'}
                </button>
            )}

            <CardDetail api={api} cardId={openId} onClose={() => setOpenId(null)}
                        onOpenRequest={(requestId) => { setOpenId(null); onOpenRequest(requestId); }} />
        </div>
    );
}
