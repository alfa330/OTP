import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Loader2, Package, Plus, RefreshCw, Search, SlidersHorizontal, X } from 'lucide-react';
import {
    APPLE_FONT, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import ParcelCard from './ParcelCard';
import ParcelForm from './ParcelForm';
import {
    STATE_FILTERS, daysInOffice, fmtDate, fmtPhone, isStale, kindMeta, pluralDays, statusMeta,
} from './parcelMeta';

/*
 * Раздел «Посылки» — реестр невостребованных посылок фронт-офисов (задача #240).
 *
 * Два читателя с разными задачами:
 *   менеджер фронт-офиса   завёл карточку и ведёт её статус
 *   оператор СЗоВ          ищет посылку, пока водитель на линии
 *
 * Отсюда главное решение раскладки: ПОИСК — первое, что видно, и он один на все
 * восемь полей ТЗ (ID и телефон водителя, ФИО, номер заказа, отправитель,
 * получатель, город, офис). Оператор не выбирает, «по какому полю искать», —
 * он вводит то, что ему продиктовали.
 *
 * Фильтры (Город → Офис → Дата → Менеджер) убраны под кнопку: оператору они
 * нужны редко, а на экране постоянно занимали бы строку из четырёх пустых
 * селекторов.
 *
 * Про цвет. Красим одно — посылку, которая лежит слишком долго. Статусы
 * нейтральны: «В офисе» это рабочее состояние, а не тревога, и раскрашенный
 * реестр перестал бы отвечать на вопрос «что требует внимания».
 */

const PAGE_SIZE = 50;

// Сколько ждать после последней буквы, прежде чем идти на сервер. 300 мс —
// тот же порядок, что в остальных поисках портала: набранное целиком слово
// уходит одним запросом, а не по букве.
const SEARCH_DEBOUNCE_MS = 300;

const EMPTY_FILTERS = { city: '', office_id: null, manager_id: null, date_from: '', date_to: '' };

const activeFilterCount = (filters) => (
    (filters.city ? 1 : 0) + (filters.office_id ? 1 : 0) + (filters.manager_id ? 1 : 0)
    + (filters.date_from ? 1 : 0) + (filters.date_to ? 1 : 0)
);

const DriverCell = ({ parcel }) => (
    <>
        <div className="truncate text-slate-900">{parcel.driver_name || '—'}</div>
        {parcel.driver_phone && (
            <a
                href={`tel:${parcel.driver_phone}`}
                onClick={(event) => event.stopPropagation()}
                className="tabular-nums text-[12.5px] text-slate-500 hover:text-blue-600 hover:underline"
            >
                {fmtPhone(parcel.driver_phone)}
            </a>
        )}
    </>
);

const ParcelsView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );

    const [capabilities, setCapabilities] = useState(null);
    const [schemaReady, setSchemaReady] = useState(true);

    const [offices, setOffices] = useState([]);
    const [filterCities, setFilterCities] = useState([]);
    const [managers, setManagers] = useState([]);

    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [counters, setCounters] = useState({});
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState('');

    const [state, setState] = useState('all');
    const [search, setSearch] = useState('');
    const [query, setQuery] = useState('');
    const [filters, setFilters] = useState(EMPTY_FILTERS);
    const [filtersOpen, setFiltersOpen] = useState(false);

    const [formOpen, setFormOpen] = useState(false);
    const [editing, setEditing] = useState(null);
    const [opened, setOpened] = useState(null);
    const [openedEvents, setOpenedEvents] = useState([]);

    const canEdit = Boolean(capabilities?.can_edit);
    const canDelete = Boolean(capabilities?.can_delete);

    // Строку поиска придерживаем: каждая буква уходила бы запросом, а реестр
    // ищут по фамилии и номеру целиком.
    useEffect(() => {
        const timer = setTimeout(() => setQuery(search.trim()), SEARCH_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [search]);

    useEffect(() => {
        let cancelled = false;
        axios.get(`${apiBaseUrl}/api/parcels/ping`, { headers: headers() })
            .then((response) => {
                if (cancelled) return;
                setCapabilities(response.data?.capabilities || null);
                setSchemaReady(response.data?.schema_ready !== false);
            })
            .catch(() => { if (!cancelled) setCapabilities(null); });
        return () => { cancelled = true; };
    }, [apiBaseUrl, headers]);

    useEffect(() => {
        let cancelled = false;
        axios.get(`${apiBaseUrl}/api/parcels/offices`, { headers: headers() })
            .then((response) => { if (!cancelled) setOffices(response.data?.offices || []); })
            .catch(() => {});
        return () => { cancelled = true; };
    }, [apiBaseUrl, headers]);

    const loadFilters = useCallback(() => {
        axios.get(`${apiBaseUrl}/api/parcels/filters`, { headers: headers() })
            .then((response) => {
                setFilterCities(response.data?.cities || []);
                setManagers(response.data?.managers || []);
            })
            .catch(() => {});
    }, [apiBaseUrl, headers]);

    useEffect(() => { loadFilters(); }, [loadFilters]);

    // Каждый запрос списка получает номер: ответ на устаревший запрос (человек
    // успел дописать в поиск) не должен перезаписать свежий.
    const requestRef = useRef(0);

    // `from` приходит аргументом, а не из состояния: иначе смена фильтра при
    // догруженных страницах успевала уйти на сервер со СТАРЫМ смещением — и в
    // список подмешивалась порция от прежнего запроса.
    const load = useCallback(async ({ append = false, from = 0 } = {}) => {
        const ticket = requestRef.current + 1;
        requestRef.current = ticket;
        setLoading(true);
        setLoadError('');
        const params = new URLSearchParams();
        const status = STATE_FILTERS.find((item) => item.key === state)?.status;
        if (status) params.set('status', status);
        if (query) params.set('q', query);
        if (filters.city) params.set('city', filters.city);
        if (filters.office_id) params.set('office_id', String(filters.office_id));
        if (filters.manager_id) params.set('manager_id', String(filters.manager_id));
        if (filters.date_from) params.set('date_from', filters.date_from);
        if (filters.date_to) params.set('date_to', filters.date_to);
        params.set('limit', String(PAGE_SIZE));
        params.set('offset', String(append ? from : 0));
        try {
            const response = await axios.get(`${apiBaseUrl}/api/parcels?${params.toString()}`,
                { headers: headers() });
            if (requestRef.current !== ticket) return;
            const page = response.data?.items || [];
            setItems((prev) => (append ? [...prev, ...page] : page));
            setTotal(Number(response.data?.total || 0));
            setCounters(response.data?.counters || {});
        } catch (error) {
            if (requestRef.current !== ticket) return;
            setLoadError(error?.response?.data?.error || 'Не удалось загрузить реестр');
        } finally {
            if (requestRef.current === ticket) setLoading(false);
        }
    }, [apiBaseUrl, filters, headers, query, state]);

    // Смена условий всегда начинает список заново. Догрузка следующей порции —
    // отдельное действие кнопки, а не состояние в зависимостях эффекта.
    useEffect(() => {
        load({ append: false, from: 0 });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [state, query, filters, apiBaseUrl]);

    const openParcel = useCallback(async (parcel) => {
        setOpened(parcel);
        setOpenedEvents([]);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/parcels/${parcel.id}`,
                { headers: headers() });
            setOpened(response.data?.item || parcel);
            setOpenedEvents(response.data?.events || []);
        } catch {
            /* Карточка уже открыта на данных из списка — историю просто не покажем. */
        }
    }, [apiBaseUrl, headers]);

    const applySaved = useCallback((saved) => {
        if (!saved) { load(); return; }
        setItems((prev) => {
            const known = prev.some((item) => item.id === saved.id);
            return known
                ? prev.map((item) => (item.id === saved.id ? saved : item))
                : [saved, ...prev];
        });
        setOpened((prev) => (prev && prev.id === saved.id ? saved : prev));
        loadFilters();
    }, [load, loadFilters]);

    const officeOptions = useMemo(() => {
        const list = filters.city
            ? offices.filter((office) => office.city === filters.city)
            : offices;
        return list.map((office) => ({
            value: office.id,
            label: filters.city ? office.name : `${office.city} · ${office.name}`,
        }));
    }, [filters.city, offices]);

    const filtersActive = activeFilterCount(filters);

    if (capabilities && !capabilities.can_open) {
        return (
            <div className="mx-auto w-full max-w-xl px-4 py-12" style={{ fontFamily: APPLE_FONT }}>
                <div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>
                    Раздел «Посылки» вам не открыт.
                </div>
            </div>
        );
    }

    return (
        <div className="mx-auto w-full max-w-[1180px] px-3 py-4 sm:px-5 sm:py-6" style={{ fontFamily: APPLE_FONT }}>
            {/* Заголовок и действия — в столбик на телефоне и в строку от sm.
                Одной строкой с flex-wrap кнопка «Добавить посылку» сжимала
                заголовок в узкую колонку, и подзаголовок рвался на четыре
                строки рядом с кнопкой. */}
            <header className="flex flex-col gap-3 sm:flex-row sm:items-center">
                <div className="min-w-0 sm:flex-1">
                    <h1 className="text-[19px] font-semibold leading-tight text-slate-900">
                        Невостребованные посылки
                    </h1>
                    <p className="mt-0.5 text-[12.5px] text-slate-500">
                        {canEdit
                            ? 'Что оставили водители в офисах и кому это передали'
                            : 'Поиск по посылкам, которые водители оставили в офисах'}
                    </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                    <button
                        type="button"
                        className={iosBtnGhost}
                        onClick={() => { load({ append: false, from: 0 }); loadFilters(); }}
                        aria-label="Обновить"
                    >
                        <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
                    </button>
                    {canEdit && (
                        <button
                            type="button"
                            className={`${iosBtnPrimary} flex-1 sm:flex-none`}
                            onClick={() => { setEditing(null); setFormOpen(true); }}
                        >
                            <Plus size={15} />
                            Добавить посылку
                        </button>
                    )}
                </div>
            </header>

            {!schemaReady && (
                <div className="mt-4 rounded-2xl bg-amber-50 px-4 py-3 text-[13px] text-amber-800 ring-1 ring-amber-200">
                    Раздел разворачивается — реестр появится после перезапуска сервера.
                </div>
            )}

            {/* Поиск. Одно поле на все восемь полей ТЗ: оператор вводит то, что
                ему продиктовали, а не выбирает, по какому полю искать. */}
            <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center">
                {/* На телефоне поле занимает свою строку: в одной строке с кнопкой
                    «Фильтры» на 390 px под ввод оставалось 196 px, и подсказка
                    обрывалась посреди слова. */}
                <div className="relative sm:flex-1">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        type="search"
                        className={`${iosInput} pl-9`}
                        placeholder="Телефон, ФИО, ID водителя, номер заказа…"
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                    />
                </div>
                <button
                    type="button"
                    className={`${filtersActive ? iosBtnPrimary : iosBtnSecondary} shrink-0`}
                    onClick={() => setFiltersOpen((prev) => !prev)}
                >
                    <SlidersHorizontal size={15} />
                    Фильтры
                    {filtersActive > 0 && <span className="tabular-nums">· {filtersActive}</span>}
                </button>
            </div>

            {/* Легенда статусов — она же фильтр. Счётчики приходят с сервера и
                считаются по ТЕКУЩИМ фильтрам без учёта статуса, поэтому сегмент
                «В офисе» честно показывает, сколько уже передали. */}
            <div className="mt-3 flex flex-wrap gap-1.5">
                {STATE_FILTERS.map((item) => {
                    const count = item.key === 'all' ? counters.all : counters[item.key];
                    const active = state === item.key;
                    return (
                        <button
                            key={item.key}
                            type="button"
                            onClick={() => setState(item.key)}
                            className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] font-medium transition active:scale-[0.98] ${
                                active
                                    ? 'bg-slate-900 text-white'
                                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                            }`}
                        >
                            {item.label}
                            {count !== undefined && (
                                <span className={`tabular-nums ${active ? 'text-white/70' : 'text-slate-400'}`}>
                                    {count}
                                </span>
                            )}
                        </button>
                    );
                })}
            </div>

            {filtersOpen && (
                <div className={`${iosCard} mt-3 grid gap-3 p-3.5 sm:grid-cols-2 lg:grid-cols-4`}>
                    <label className="block space-y-1.5">
                        <span className="px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Город</span>
                        <CustomSelect
                            value={filters.city}
                            onChange={(value) => setFilters((prev) => ({ ...prev, city: value, office_id: null }))}
                            options={[{ value: '', label: 'Все города' },
                                ...filterCities.map((item) => ({ value: item.city, label: `${item.city} · ${item.parcels}` }))]}
                            placeholder="Все города"
                            variant="ios"
                            ariaLabel="Город"
                        />
                    </label>
                    <label className="block space-y-1.5">
                        <span className="px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Офис</span>
                        <CustomSelect
                            value={filters.office_id}
                            onChange={(value) => setFilters((prev) => ({ ...prev, office_id: value || null }))}
                            options={[{ value: null, label: 'Все офисы' }, ...officeOptions]}
                            placeholder="Все офисы"
                            variant="ios"
                            searchable
                            ariaLabel="Офис"
                        />
                    </label>
                    <label className="block space-y-1.5">
                        <span className="px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Менеджер</span>
                        <CustomSelect
                            value={filters.manager_id}
                            onChange={(value) => setFilters((prev) => ({ ...prev, manager_id: value || null }))}
                            options={[{ value: null, label: 'Все менеджеры' },
                                ...managers.map((item) => ({ value: item.id, label: `${item.name} · ${item.parcels}` }))]}
                            placeholder="Все менеджеры"
                            variant="ios"
                            searchable
                            ariaLabel="Менеджер"
                        />
                    </label>
                    <div className="space-y-1.5">
                        <span className="block px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Дата приёма</span>
                        <div className="flex items-center gap-1.5">
                            <input
                                type="date"
                                className={iosInput}
                                value={filters.date_from}
                                onChange={(event) => setFilters((prev) => ({ ...prev, date_from: event.target.value }))}
                                aria-label="Дата приёма: с"
                            />
                            <span className="text-slate-400">—</span>
                            <input
                                type="date"
                                className={iosInput}
                                value={filters.date_to}
                                onChange={(event) => setFilters((prev) => ({ ...prev, date_to: event.target.value }))}
                                aria-label="Дата приёма: по"
                            />
                        </div>
                    </div>
                    {filtersActive > 0 && (
                        <button
                            type="button"
                            className={`${iosBtnGhost} justify-self-start`}
                            onClick={() => setFilters(EMPTY_FILTERS)}
                        >
                            <X size={14} />
                            Сбросить фильтры
                        </button>
                    )}
                </div>
            )}

            {loadError && (
                <div className="mt-4 rounded-2xl bg-red-50 px-4 py-3 text-[13px] text-red-700 ring-1 ring-red-200">
                    {loadError}
                </div>
            )}

            {/* Таблица на широком экране, карточки на телефоне: одна и та же
                выборка, разная подача. Рамка прокручивается по горизонтали —
                иначе колонка «Описание» выдавливает статус за край. */}
            <div className={`${iosCard} mt-4 hidden overflow-x-auto md:block`}>
                <table className="w-full min-w-[900px] border-collapse text-[13.5px]">
                    <thead>
                        <tr className="border-b border-slate-200/70 bg-slate-50 text-left text-[11.5px] uppercase tracking-wider text-slate-500">
                            <th className="px-3.5 py-2.5 font-semibold">Принята</th>
                            <th className="px-3.5 py-2.5 font-semibold">Офис</th>
                            <th className="px-3.5 py-2.5 font-semibold">Водитель</th>
                            <th className="px-3.5 py-2.5 font-semibold">Что оставили</th>
                            <th className="px-3.5 py-2.5 font-semibold">Отправитель / получатель</th>
                            <th className="px-3.5 py-2.5 font-semibold">Статус</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items.map((parcel) => {
                            const lying = daysInOffice(parcel);
                            const stale = isStale(parcel);
                            return (
                                <tr
                                    key={parcel.id}
                                    onClick={() => openParcel(parcel)}
                                    className="cursor-pointer border-b border-slate-100 transition last:border-0 hover:bg-slate-50"
                                >
                                    <td className="px-3.5 py-2.5 align-top whitespace-nowrap">
                                        <div className="tabular-nums text-slate-900">{fmtDate(parcel.received_on)}</div>
                                        {lying !== null && (
                                            <div className={`text-[12px] ${stale ? 'font-medium text-amber-600' : 'text-slate-400'}`}>
                                                лежит {pluralDays(lying)}
                                            </div>
                                        )}
                                    </td>
                                    <td className="px-3.5 py-2.5 align-top">
                                        <div className="text-slate-900">{parcel.city}</div>
                                        <div className="text-[12.5px] text-slate-500">{parcel.office_name || '—'}</div>
                                    </td>
                                    <td className="max-w-[220px] px-3.5 py-2.5 align-top">
                                        <DriverCell parcel={parcel} />
                                    </td>
                                    <td className="max-w-[260px] px-3.5 py-2.5 align-top">
                                        <div className="text-slate-900">{kindMeta(parcel.kind).label}</div>
                                        <div className="truncate text-[12.5px] text-slate-500">{parcel.description}</div>
                                    </td>
                                    <td className="max-w-[200px] px-3.5 py-2.5 align-top text-[12.5px] text-slate-600">
                                        {parcel.sender && <div className="truncate">от {parcel.sender}</div>}
                                        {parcel.recipient && <div className="truncate">для {parcel.recipient}</div>}
                                        {!parcel.sender && !parcel.recipient && <span className="text-slate-400">—</span>}
                                    </td>
                                    <td className="px-3.5 py-2.5 align-top whitespace-nowrap">
                                        <div className={statusMeta(parcel.status).tone === 'muted' ? 'text-slate-500' : 'text-slate-900'}>
                                            {statusMeta(parcel.status).label}
                                        </div>
                                        {parcel.status !== 'in_office' && parcel.status_changed_by_name && (
                                            <div className="text-[12px] text-slate-400">{parcel.status_changed_by_name}</div>
                                        )}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
                {!items.length && !loading && (
                    <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
                        <Package size={22} className="text-slate-300" />
                        <p className="text-[13.5px] text-slate-500">
                            {query || filtersActive ? 'Ничего не нашлось — попробуйте изменить запрос' : 'В реестре пока пусто'}
                        </p>
                    </div>
                )}
            </div>

            <div className="mt-4 space-y-2 md:hidden">
                {items.map((parcel) => {
                    const lying = daysInOffice(parcel);
                    const stale = isStale(parcel);
                    return (
                        <button
                            key={parcel.id}
                            type="button"
                            onClick={() => openParcel(parcel)}
                            className={`${iosCard} w-full p-3.5 text-left transition active:scale-[0.99]`}
                        >
                            <div className="flex items-baseline justify-between gap-2">
                                <span className="truncate text-[14px] font-medium text-slate-900">
                                    {parcel.driver_name || '—'}
                                </span>
                                <span className={`shrink-0 text-[12px] ${statusMeta(parcel.status).tone === 'muted' ? 'text-slate-400' : 'text-slate-600'}`}>
                                    {statusMeta(parcel.status).short}
                                </span>
                            </div>
                            {parcel.driver_phone && (
                                <div className="tabular-nums text-[12.5px] text-slate-500">{fmtPhone(parcel.driver_phone)}</div>
                            )}
                            <div className="mt-1.5 text-[13px] text-slate-700">
                                {kindMeta(parcel.kind).label} · {parcel.description}
                            </div>
                            <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[12px] text-slate-500">
                                <span>{parcel.city}{parcel.office_name ? ` · ${parcel.office_name}` : ''}</span>
                                <span className="tabular-nums">{fmtDate(parcel.received_on)}</span>
                                {lying !== null && (
                                    <span className={stale ? 'font-medium text-amber-600' : ''}>лежит {pluralDays(lying)}</span>
                                )}
                            </div>
                        </button>
                    );
                })}
                {!items.length && !loading && (
                    <div className={`${iosCard} px-4 py-10 text-center text-[13.5px] text-slate-500`}>
                        {query || filtersActive ? 'Ничего не нашлось' : 'В реестре пока пусто'}
                    </div>
                )}
            </div>

            {loading && (
                <div className="mt-4 flex items-center justify-center gap-2 text-[13px] text-slate-500">
                    <Loader2 size={15} className="animate-spin" />
                    Загружаем реестр…
                </div>
            )}

            {items.length < total && (
                <div className="mt-4 flex flex-col items-center gap-1.5">
                    <button
                        type="button"
                        className={iosBtnSecondary}
                        disabled={loading}
                        onClick={() => load({ append: true, from: items.length })}
                    >
                        Показать ещё
                    </button>
                    <span className="text-[12px] tabular-nums text-slate-400">
                        {items.length} из {total}
                    </span>
                </div>
            )}

            <ParcelForm
                open={formOpen}
                onClose={() => { setFormOpen(false); setEditing(null); }}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                offices={offices}
                defaultCity={capabilities?.default_city || ''}
                parcel={editing}
                onSaved={applySaved}
                showToast={showToast}
            />

            <ParcelCard
                open={Boolean(opened)}
                onClose={() => { setOpened(null); setOpenedEvents([]); }}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                parcel={opened}
                events={openedEvents}
                canEdit={canEdit}
                canDelete={canDelete}
                onEdit={(parcel) => { setOpened(null); setEditing(parcel); setFormOpen(true); }}
                onChanged={(saved, events) => { applySaved(saved); setOpenedEvents(events || []); }}
                onDeleted={(id) => {
                    setItems((prev) => prev.filter((item) => item.id !== id));
                    setTotal((prev) => Math.max(0, prev - 1));
                }}
                showToast={showToast}
            />
        </div>
    );
};

export default ParcelsView;
