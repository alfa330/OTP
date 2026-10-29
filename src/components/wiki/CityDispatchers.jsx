import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Loader2, RefreshCw, Search } from 'lucide-react';
import { iosBtnSecondary, iosInput, IosModal, IosSegmented } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { formatDate, formatTime } from './cityRules';
import { dispatcherWord } from './registrationRules';

/* Диспетчерские кабинета Fleet — весь список, по городам (просьба владельца
 * 29.10.2026). Карточка города показывает ответ «куда регистрировать», а здесь
 * видно, из чего он сложен: парк и город каждой диспетчерской.
 *
 * Парк и город подставляет сверка по названию (wiki/dispatchers.py): «iTaxi 2»
 * — iTaxi, «Честный Темиртау» работает на Темиртау. Где разбор ошибся или
 * парка в вике нет, их правят здесь — и сверка правку больше не трогает;
 * «Как в кабинете» возвращает автоматическую.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const fold = (value) => String(value || '').toLowerCase().replace(/ё/g, 'е').trim();

const selectTrigger = '[&>button]:bg-slate-100 [&>button]:px-2.5 [&>button]:py-1.5 '
    + '[&>button]:text-[13px] [&>button]:font-normal [&>button]:text-slate-900 '
    + '[&>button:hover]:bg-slate-200/70';

const AUTO = 'auto';

const Row = ({ item, canManage, parkOptions, cityOptions, saving, onChange }) => {
    const moved = item.city_name && item.fleet_city && fold(item.city_name) !== fold(item.fleet_city);
    const notes = [
        moved && `в кабинете — ${item.fleet_city}`,
        item.park_manual && 'парк выбран вручную',
        item.city_manual && 'город выбран вручную',
    ].filter(Boolean);
    return (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3.5 py-2.5">
            <div className="min-w-0 flex-1 basis-48">
                <div className="flex items-center gap-1.5">
                    <span className="truncate text-[14px] font-medium text-slate-900">{item.name}</span>
                    {/* Спиннер — у названия, а не перед пикерами: там он сдвигал
                        их на ширину значка, и на узком телефоне правый пикер
                        уезжал за край (разбор 29.10.2026). */}
                    {saving && <Loader2 size={13} className="shrink-0 animate-spin text-slate-400" />}
                </div>
                {notes.length > 0 && (
                    <div className="truncate text-[12px] text-slate-400">{notes.join(' · ')}</div>
                )}
            </div>
            {canManage ? (
                /* На телефоне пикеры делят строку поровну: две фиксированные
                   ширины по 160 px на экране 360 px не помещались. */
                <div className="flex w-full items-center gap-2 sm:w-auto sm:shrink-0">
                    <CustomSelect
                        variant="ios"
                        className={`min-w-0 flex-1 sm:w-40 sm:flex-none ${selectTrigger}`}
                        value={item.park_id || ''}
                        onChange={(value) => onChange(item, { park_id: value === '' ? null : value })}
                        options={item.park_manual ? [{ value: AUTO, label: 'Как в кабинете' }, ...parkOptions]
                            : parkOptions}
                        searchable
                        searchPlaceholder="Поиск парка…"
                        ariaLabel={`Парк диспетчерской ${item.name}`}
                    />
                    <CustomSelect
                        variant="ios"
                        className={`min-w-0 flex-1 sm:w-40 sm:flex-none ${selectTrigger}`}
                        value={item.city_id || ''}
                        onChange={(value) => onChange(item, { city_id: value === '' ? null : value })}
                        options={item.city_manual ? [{ value: AUTO, label: 'Как в кабинете' }, ...cityOptions]
                            : cityOptions}
                        searchable
                        searchPlaceholder="Поиск города…"
                        ariaLabel={`Город диспетчерской ${item.name}`}
                    />
                </div>
            ) : (
                <div className="shrink-0 text-[13px] text-slate-500">{item.park_name || 'Без парка'}</div>
            )}
        </div>
    );
};

export default function CityDispatchers({ open, onClose, base, req, toast }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [saving, setSaving] = useState({});
    const [query, setQuery] = useState('');
    const [filter, setFilter] = useState('all');
    /* Кто был «без парка», когда включили фильтр. Привязанная строка не
       исчезает из списка тут же — иначе человек не видит, что выбор лёг, и
       следующая строка прыгает под палец. Уходит, когда фильтр сменят. */
    const [sticky, setSticky] = useState(null);
    const changed = useRef(false);

    const load = useCallback(() => {
        setLoading(true);
        axios.get(`${base}/dispatchers`, req)
            .then((r) => setData(r.data || null))
            .catch((e) => toast(errText(e, 'Не удалось загрузить диспетчерские'), 'error'))
            .finally(() => setLoading(false));
    }, [base, req, toast]);

    useEffect(() => {
        if (!open) return;
        changed.current = false;
        setQuery('');
        setFilter('all');
        setSticky(null);
        load();
    }, [open, load]);

    const chooseFilter = (next) => {
        setFilter(next);
        setSticky(next === 'unlinked'
            ? new Set((data?.items || []).filter((item) => !item.park_id).map((item) => item.id))
            : null);
    };

    const close = () => onClose(changed.current);

    const parkOptions = useMemo(() => [
        { value: '', label: 'Без парка' },
        ...(data?.parks || []).map((park) => ({ value: park.id, label: park.name })),
    ], [data]);
    const cityOptions = useMemo(() => [
        { value: '', label: 'Не выбран' },
        ...(data?.cities || []).map((city) => ({ value: city.id, label: city.name })),
    ], [data]);

    const items = data?.items || [];
    const unlinked = items.filter((item) => !item.park_id).length;

    const groups = useMemo(() => {
        const collator = new Intl.Collator('ru');
        const needle = fold(query);
        const byCity = new Map();
        (data?.items || [])
            .filter((item) => filter !== 'unlinked' || !item.park_id || sticky?.has(item.id))
            .filter((item) => !needle || [item.name, item.park_name, item.city_name, item.fleet_city]
                .some((text) => fold(text).includes(needle)))
            .forEach((item) => {
                const key = item.city_name || item.fleet_city || 'Без города';
                if (!byCity.has(key)) byCity.set(key, []);
                byCity.get(key).push(item);
            });
        return [...byCity.entries()]
            .sort(([a], [b]) => collator.compare(a, b))
            .map(([city, list]) => ({ city, list }));
    }, [data, query, filter, sticky]);

    const change = (item, fields) => {
        /* «Были правки» — с отправки, а не с ответа: окно закрыли, пока PATCH
           в пути, — карточки всё равно обязаны перечитаться (разбор 29.10.2026). */
        changed.current = true;
        setSaving((current) => ({ ...current, [item.id]: true }));
        axios.patch(`${base}/dispatchers/${item.id}`, fields, req)
            .then((r) => {
                const next = r.data?.dispatcher;
                if (!next) return;
                setData((current) => current && ({
                    ...current,
                    items: current.items.map((row) => (row.id === next.id ? next : row)),
                }));
            })
            .catch((e) => toast(errText(e, 'Не удалось сохранить'), 'error'))
            .finally(() => setSaving((current) => {
                const rest = { ...current };
                delete rest[item.id];
                return rest;
            }));
    };

    const sync = () => {
        setSyncing(true);
        axios.post(`${base}/dispatchers/sync`, {}, req)
            .then((r) => {
                setData(r.data || null);
                changed.current = true;
                const result = r.data?.sync || {};
                if (result.status !== 'ok') {
                    toast('Кабинет пока не отдал список — попробуйте позже', 'error');
                } else if (result.added || result.gone) {
                    toast(`Обновлено: новых ${result.added || 0}, ушли из кабинета ${result.gone || 0}`,
                          'success');
                } else {
                    toast('Список совпадает с кабинетом', 'success');
                }
            })
            .catch((e) => toast(errText(e, 'Не удалось обновить'), 'error'))
            .finally(() => setSyncing(false));
    };

    const source = data?.source || {};
    const checked = source.fleet_checked_at || source.synced_at;
    const subtitle = data?.has_source === false
        ? 'У этого пространства нет кабинета Fleet'
        /* Коротко, чтобы дата влезала в шапку экрана на телефоне. */
        : [`${items.length} ${dispatcherWord(items.length)}`,
            checked && `Fleet на ${formatDate(checked)}, ${formatTime(checked)}`]
            .filter(Boolean).join(' · ');

    return (
        <IosModal
            open={open}
            onClose={close}
            title="Диспетчерские"
            subtitle={data ? subtitle : null}
            maxWidth="max-w-3xl"
        >
            <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                    <div className="relative min-w-0 flex-1 basis-56">
                        <Search size={15} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input
                            className={`${iosInput} pl-9`}
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            placeholder="Диспетчерская, парк или город"
                            aria-label="Поиск диспетчерской"
                        />
                    </div>
                    {(unlinked > 0 || filter === 'unlinked') && (
                        <IosSegmented
                            value={filter}
                            onChange={chooseFilter}
                            options={[
                                { value: 'all', label: 'Все' },
                                { value: 'unlinked', label: 'Без парка', count: unlinked },
                            ]}
                            ariaLabel="Какие диспетчерские показать"
                        />
                    )}
                    {data?.can_manage && data?.has_source && (
                        <button type="button" className={iosBtnSecondary} onClick={sync} disabled={syncing}>
                            <RefreshCw size={14} className={syncing ? 'animate-spin' : ''} /> Обновить из кабинета
                        </button>
                    )}
                </div>

                {loading && !data && (
                    <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-slate-400">
                        <Loader2 size={15} className="animate-spin" /> Загружаем…
                    </div>
                )}

                {data && items.length === 0 && (
                    <div className="rounded-2xl bg-slate-50 px-4 py-6 text-center text-[13px] leading-relaxed text-slate-500 ring-1 ring-slate-200/70">
                        {data.has_source
                            ? 'Список диспетчерских ещё не пришёл из кабинета — он обновляется вместе с разделом «Рассылки».'
                            : 'Диспетчерские кабинета Fleet ведутся в пространстве «Таксопарки».'}
                    </div>
                )}

                {data && items.length > 0 && groups.length === 0 && (
                    <div className="px-1 py-4 text-[13px] text-slate-500">Ничего не найдено</div>
                )}

                {groups.map((group) => (
                    <section key={group.city} className="overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70">
                        <div className="flex items-baseline justify-between gap-2 border-b border-slate-100 bg-slate-50/80 px-3.5 py-2">
                            <span className="text-[13px] font-semibold text-slate-900">{group.city}</span>
                            <span className="text-[12px] tabular-nums text-slate-400">
                                {group.list.length} {dispatcherWord(group.list.length)}
                            </span>
                        </div>
                        <div className="divide-y divide-slate-100">
                            {group.list.map((item) => (
                                <Row
                                    key={item.id}
                                    item={item}
                                    canManage={!!data?.can_manage}
                                    parkOptions={parkOptions}
                                    cityOptions={cityOptions}
                                    saving={!!saving[item.id]}
                                    onChange={change}
                                />
                            ))}
                        </div>
                    </section>
                ))}
            </div>
        </IosModal>
    );
}
