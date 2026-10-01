import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Download, Droplet, Loader2, Search, SlidersHorizontal, Undo2, X } from 'lucide-react';
import { IosModal, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangeCalendar, IosDateRangePicker, rangeLabel } from '../ui/DateRangePicker';
import { driverAccountUrl, fmtDateTime, fmtPhone, rangeDays, shiftDaysBack, todayISO } from '../parcels/parcelMeta';
import { EXPORT_MAX_DAYS, blocksWord, exportFileName, kindLabel, tariffLabel } from './waterMeta';

/*
 * «Журнал» — история выдач (ТЗ, раздел 5): колонки и фильтры из ТЗ, выгрузка
 * в Excel. Раскладка та же, что у реестра «Посылок»: поиск первым, фильтры под
 * кнопкой, выгрузка — пикером периода по эталону табло СЗоВ.
 *
 * Поиск один на водителя: ФИО, телефон или ID — что продиктовали.
 */

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;
const EMPTY_FILTERS = {
    date_from: '', date_to: '', city: '', water_office_id: null, issued_by: null, park: '', kind: '',
};

const CHIP = 'flex w-full items-center gap-2 rounded-xl bg-white px-3 py-2 text-left text-[12.5px] '
    + 'font-medium text-slate-700 ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)] '
    + 'transition-all hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-blue-500/60 '
    + '[&>span]:flex-1 [&>span]:truncate [&>span]:text-left';

const DATE_PRESETS = [
    { label: 'Сегодня', range: () => ({ from: todayISO(), to: todayISO() }) },
    { label: 'Неделя', range: () => ({ from: shiftDaysBack(todayISO(), 6), to: todayISO() }) },
    { label: 'Месяц', range: () => ({ from: shiftDaysBack(todayISO(), 29), to: todayISO() }) },
];

const EXPORT_PRESETS = [
    { label: 'Неделя', range: () => ({ from: shiftDaysBack(todayISO(), 6), to: todayISO() }) },
    { label: 'Месяц', range: () => ({ from: shiftDaysBack(todayISO(), 29), to: todayISO() }) },
    { label: 'Год', range: () => ({ from: shiftDaysBack(todayISO(), EXPORT_MAX_DAYS - 1), to: todayISO() }) },
];

const ordersLabel = (item) => {
    if (item.orders_counted === null || item.orders_counted === undefined) return '—';
    if (item.orders_basis === 'new') return 'новый';
    return `${item.orders_counted} ${item.orders_basis === 'since_last' ? 'с прошлой' : 'за 7 дн.'}`;
};

/* Щелчок по ФИО ведёт во Флит и гасит всплытие: у руководителя строка сама
   открывает карточку выдачи, и один щелчок давал бы и вкладку, и окно. */
const DriverName = ({ item, muted = false }) => {
    const url = driverAccountUrl(item);
    const name = item.driver_name || '—';
    const tone = muted ? 'text-slate-400' : 'text-slate-900';
    return url ? (
        <a href={url} target="_blank" rel="noopener noreferrer" title="Открыть аккаунт водителя во Флите"
           onClick={(event) => event.stopPropagation()}
           className={`block truncate underline decoration-transparent underline-offset-2 transition hover:decoration-inherit ${tone}`}>
            {name}
        </a>
    ) : <div className={`truncate ${tone}`}>{name}</div>;
};

const CANCELED_PILL = 'inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-[11.5px] font-medium text-slate-500';

/* Карточка выдачи у руководителя — и отмена ошибочной (01.10.2026).
 *
 * Отмена — одним шагом, но не одним щелчком: кнопка оживает, только когда
 * написана причина. Причина — это и подтверждение «я понимаю, что делаю», и
 * ответ на вопрос «куда делся блок», который зададут через месяц.
 */
const IssueSheet = ({ item, onClose, apiBaseUrl, headers, onCanceled, showToast }) => {
    const [reason, setReason] = useState('');
    const [saving, setSaving] = useState(false);

    useEffect(() => { setReason(''); }, [item?.id]);

    if (!item) return null;
    const canceled = Boolean(item.canceled_at);

    const submit = async () => {
        if (!reason.trim() || saving) return;
        setSaving(true);
        try {
            const response = await axios.post(`${apiBaseUrl}/api/water/issues/${item.id}/cancel`,
                { reason: reason.trim() }, { headers: headers() });
            onCanceled?.(response.data?.issue, response.data?.office);
            showToast?.(`Выдача отменена, на остаток вернули ${blocksWord(item.blocks)}`, 'success');
            onClose();
        } catch (requestError) {
            showToast?.(requestError?.response?.data?.error || 'Не удалось отменить выдачу', 'error');
        } finally {
            setSaving(false);
        }
    };

    const rows = [
        ['Вид', `${kindLabel(item.kind)} · ${blocksWord(item.blocks)}`],
        ['Офис', `${item.city} · ${item.office_name}`],
        ['Выдал', item.issued_by_name],
        ['Телефон', item.driver_phone ? fmtPhone(item.driver_phone) : null],
        ['Парк', item.driver_park],
    ].filter(([, value]) => value);

    return (
        <IosModal
            open={Boolean(item)}
            onClose={onClose}
            title={item.driver_name || 'Выдача'}
            subtitle={fmtDateTime(item.created_at)}
            footer={canceled ? null : (
                <button
                    type="button"
                    className="inline-flex items-center justify-center gap-2 rounded-xl bg-red-600 px-4 py-2.5 text-[13.5px] font-semibold text-white transition hover:bg-red-700 active:scale-[0.98] disabled:opacity-50"
                    disabled={!reason.trim() || saving}
                    onClick={submit}
                >
                    {saving ? <Loader2 size={15} className="animate-spin" /> : <Undo2 size={15} />}
                    Отменить выдачу
                </button>
            )}
        >
            <div className="space-y-4">
                <dl className="divide-y divide-slate-100 rounded-2xl bg-white px-4 ring-1 ring-slate-200/70">
                    {rows.map(([label, value]) => (
                        <div key={label} className="flex justify-between gap-4 py-2.5 text-[13px]">
                            <dt className="text-slate-500">{label}</dt>
                            <dd className="text-right text-slate-900">{value}</dd>
                        </div>
                    ))}
                </dl>
                {canceled ? (
                    <div className="rounded-xl bg-slate-100 px-3.5 py-3 text-[13px] text-slate-700">
                        <div className="font-medium">
                            Отменена {fmtDateTime(item.canceled_at)}{item.canceled_by_name ? ` · ${item.canceled_by_name}` : ''}
                        </div>
                        {item.cancel_reason && <div className="mt-0.5 text-slate-600">{item.cancel_reason}</div>}
                    </div>
                ) : (
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Причина отмены</span>
                        <input className={iosInput} value={reason} maxLength={500}
                               onChange={(event) => setReason(event.target.value)}
                               placeholder="Например: ошиблись водителем" />
                        <span className="block px-1 text-[11.5px] text-slate-500">
                            Блоки вернутся на остаток офиса, а водителю эта выдача перестанет засчитываться.
                        </span>
                    </label>
                )}
            </div>
        </IosModal>
    );
};

const WaterJournal = ({ apiBaseUrl, headers, reloadKey, canManage = false, onStockChanged, showToast }) => {
    // Карточку выдачи открывает только руководитель: у остальных в ней нечего
    // делать, всё видно в самой строке.
    const [opened, setOpened] = useState(null);
    const [search, setSearch] = useState('');
    const [query, setQuery] = useState('');
    const [filters, setFilters] = useState(EMPTY_FILTERS);
    const [filtersOpen, setFiltersOpen] = useState(false);
    const [values, setValues] = useState({ staff: [], parks: [], offices: [] });
    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [exportOpen, setExportOpen] = useState(false);
    const [exportRange, setExportRange] = useState(() => ({
        from: shiftDaysBack(todayISO(), 29), to: todayISO(),
    }));
    const [downloading, setDownloading] = useState(false);
    const exportRef = useRef(null);
    const ticket = useRef(0);
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    useEffect(() => {
        const timer = setTimeout(() => setQuery(search.trim()), SEARCH_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [search]);

    useEffect(() => {
        axios.get(`${apiBaseUrl}/api/water/filters`, { headers: headers() })
            .then((response) => setValues({
                staff: response.data?.staff || [], parks: response.data?.parks || [],
                offices: response.data?.offices || [],
            }))
            .catch(() => {});
    }, [apiBaseUrl, headers, reloadKey]);

    useEffect(() => {
        if (!exportOpen) return undefined;
        const onDown = (event) => {
            if (exportRef.current && !exportRef.current.contains(event.target)) setExportOpen(false);
        };
        const onKey = (event) => { if (event.key === 'Escape') setExportOpen(false); };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', onDown);
            document.removeEventListener('keydown', onKey);
        };
    }, [exportOpen]);

    /* Отбор — одной функцией на список и на выгрузку, как в «Посылках». */
    const selection = useMemo(() => {
        const params = new URLSearchParams();
        if (query) params.set('q', query);
        Object.entries(filters).forEach(([key, value]) => {
            if (value !== '' && value !== null && value !== undefined) params.set(key, String(value));
        });
        return params;
    }, [query, filters]);

    const load = useCallback(async ({ append = false, from = 0 } = {}) => {
        const id = ticket.current + 1;
        ticket.current = id;
        setLoading(true);
        setError('');
        const params = new URLSearchParams(selection);
        params.set('limit', String(PAGE_SIZE));
        params.set('offset', String(append ? from : 0));
        try {
            const response = await axios.get(`${apiBaseUrl}/api/water/issues?${params}`, { headers: headers() });
            if (ticket.current !== id) return;
            const page = response.data?.items || [];
            setItems((prev) => (append ? [...prev, ...page] : page));
            setTotal(Number(response.data?.total || 0));
        } catch (requestError) {
            if (ticket.current === id) setError(requestError?.response?.data?.error || 'Не удалось загрузить журнал');
        } finally {
            if (ticket.current === id) setLoading(false);
        }
    }, [apiBaseUrl, headers, selection]);

    useEffect(() => {
        load({ append: false, from: 0 });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selection, reloadKey]);

    const download = useCallback((from, to) => {
        setExportOpen(false);
        setDownloading(true);
        const params = new URLSearchParams(selection);
        params.set('date_from', from);
        params.set('date_to', to);
        axios.get(`${apiBaseUrl}/api/water/issues/export?${params}`, { headers: headers(), responseType: 'blob' })
            .then((response) => {
                const url = URL.createObjectURL(response.data);
                const link = document.createElement('a');
                link.href = url;
                link.download = exportFileName(from, to);
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(url);
            })
            .catch(async (requestError) => {
                let message = 'Не удалось собрать выгрузку';
                try {
                    const text = await requestError?.response?.data?.text?.();
                    message = JSON.parse(text || '{}').error || message;
                } catch (_) { /* останется общая фраза */ }
                toastRef.current?.(message, 'error');
            })
            .finally(() => setDownloading(false));
    }, [apiBaseUrl, headers, selection]);

    const cities = useMemo(() => [...new Set(values.offices.map((office) => office.city))].sort(), [values.offices]);
    const officeOptions = values.offices
        .filter((office) => !filters.city || office.city === filters.city)
        .map((office) => ({ value: office.id, label: filters.city ? office.name : `${office.city} · ${office.name}` }));

    const chips = [];
    if (filters.date_from || filters.date_to) {
        chips.push({ key: 'dates', name: 'Период', label: rangeLabel(filters.date_from, filters.date_to),
            clear: () => setFilters((prev) => ({ ...prev, date_from: '', date_to: '' })) });
    }
    if (filters.city) chips.push({ key: 'city', name: 'Город', label: filters.city,
        clear: () => setFilters((prev) => ({ ...prev, city: '', water_office_id: null })) });
    if (filters.water_office_id) {
        const office = values.offices.find((item) => item.id === filters.water_office_id);
        chips.push({ key: 'office', name: 'Офис', label: office?.name || `№${filters.water_office_id}`,
            clear: () => setFilters((prev) => ({ ...prev, water_office_id: null })) });
    }
    if (filters.issued_by) {
        const person = values.staff.find((item) => item.id === filters.issued_by);
        chips.push({ key: 'staff', name: 'Сотрудник', label: person?.name || `№${filters.issued_by}`,
            clear: () => setFilters((prev) => ({ ...prev, issued_by: null })) });
    }
    if (filters.park) chips.push({ key: 'park', name: 'Парк', label: filters.park,
        clear: () => setFilters((prev) => ({ ...prev, park: '' })) });
    if (filters.kind) chips.push({ key: 'kind', name: 'Вид', label: kindLabel(filters.kind),
        clear: () => setFilters((prev) => ({ ...prev, kind: '' })) });

    const exportDays = rangeDays(exportRange.from, exportRange.to);
    const exportTooLong = exportDays > EXPORT_MAX_DAYS;

    return (
        <div className="space-y-3">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <div className="relative sm:flex-1">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input type="search" className={`${iosInput} pl-9`} value={search}
                           onChange={(event) => setSearch(event.target.value)}
                           placeholder="Водитель: ФИО, телефон или ID" />
                </div>
                <div className="flex items-center gap-2">
                    <button type="button" className={chips.length ? iosBtnPrimary : iosBtnSecondary}
                            onClick={() => setFiltersOpen((prev) => !prev)}>
                        <SlidersHorizontal size={15} /> Фильтры{chips.length ? ` · ${chips.length}` : ''}
                    </button>
                    <div ref={exportRef} className="relative">
                        <button type="button" className={`${iosBtnSecondary} ${exportOpen ? 'bg-slate-200 text-slate-900' : ''}`}
                                onClick={() => setExportOpen((value) => !value)} disabled={downloading}>
                            {downloading ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
                            {downloading ? 'Готовим файл…' : 'Выгрузить'}
                        </button>
                        {exportOpen && (
                            /* На телефоне кнопка стоит второй в ряду: привязка к её
                               правому краю уводила календарь за левый край экрана. */
                            <div className="absolute -left-24 top-full z-[60] mt-2 sm:left-auto sm:right-0">
                                <IosDateRangeCalendar
                                    from={exportRange.from}
                                    to={exportRange.to}
                                    max={todayISO()}
                                    presets={EXPORT_PRESETS}
                                    onChange={(next) => setExportRange({ from: next.from || next.to, to: next.to || next.from })}
                                    footer={(
                                        <div className="mt-2.5 border-t border-slate-100 pt-2.5">
                                            <button type="button" className={`${iosBtnPrimary} w-full`}
                                                    disabled={!exportDays || exportTooLong}
                                                    onClick={() => download(exportRange.from, exportRange.to)}>
                                                <Download size={15} /> Подтвердить
                                            </button>
                                            <p className={`mt-1.5 text-center text-[11px] ${exportTooLong ? 'text-rose-500' : 'text-slate-400'}`}>
                                                {exportTooLong ? 'Не больше года за раз' : `${rangeLabel(exportRange.from, exportRange.to)} · по дате выдачи`}
                                            </p>
                                        </div>
                                    )}
                                />
                            </div>
                        )}
                    </div>
                </div>
            </div>

            {chips.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                    {chips.map((chip) => (
                        <button key={chip.key} type="button" onClick={chip.clear}
                                className="group inline-flex max-w-full items-center gap-1.5 rounded-full bg-white px-2.5 py-1 text-[12.5px] text-slate-700 ring-1 ring-slate-200/80 transition hover:ring-slate-300">
                            <span className="text-slate-400">{chip.name}</span>
                            <span className="truncate font-medium">{chip.label}</span>
                            <X size={12} className="shrink-0 text-slate-400 group-hover:text-slate-600" />
                        </button>
                    ))}
                    <button type="button" onClick={() => setFilters(EMPTY_FILTERS)}
                            className="px-1.5 text-[12.5px] text-slate-500 underline decoration-slate-300 underline-offset-2 hover:text-slate-700">
                        сбросить всё
                    </button>
                </div>
            )}

            {filtersOpen && (
                <div className={`${iosCard} grid gap-3 p-3.5 sm:grid-cols-2 lg:grid-cols-3`}>
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Период</span>
                        <IosDateRangePicker
                            from={filters.date_from}
                            to={filters.date_to}
                            max={todayISO()}
                            presets={DATE_PRESETS}
                            onChange={({ from, to }) => setFilters((prev) => ({ ...prev, date_from: from || '', date_to: to || '' }))}
                            triggerClassName={CHIP}
                        />
                    </label>
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Город</span>
                        <CustomSelect value={filters.city} variant="ios" ariaLabel="Город"
                                      onChange={(value) => setFilters((prev) => ({ ...prev, city: value, water_office_id: null }))}
                                      options={[{ value: '', label: 'Все города' }, ...cities.map((city) => ({ value: city, label: city }))]} />
                    </label>
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Офис</span>
                        <CustomSelect value={filters.water_office_id} variant="ios" ariaLabel="Офис" placeholder="Все офисы"
                                      onChange={(value) => setFilters((prev) => ({ ...prev, water_office_id: value || null }))}
                                      options={[{ value: null, label: 'Все офисы' }, ...officeOptions]} />
                    </label>
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Сотрудник</span>
                        <CustomSelect value={filters.issued_by} variant="ios" ariaLabel="Сотрудник" searchable placeholder="Все сотрудники"
                                      onChange={(value) => setFilters((prev) => ({ ...prev, issued_by: value || null }))}
                                      options={[{ value: null, label: 'Все сотрудники' },
                                          ...values.staff.map((person) => ({ value: person.id, label: person.name }))]} />
                    </label>
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Парк</span>
                        <CustomSelect value={filters.park} variant="ios" ariaLabel="Парк" searchable
                                      onChange={(value) => setFilters((prev) => ({ ...prev, park: value || '' }))}
                                      options={[{ value: '', label: 'Все парки' },
                                          ...values.parks.map((park) => ({ value: park, label: park }))]} />
                    </label>
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Вид выдачи</span>
                        <CustomSelect value={filters.kind} variant="ios" ariaLabel="Вид выдачи"
                                      onChange={(value) => setFilters((prev) => ({ ...prev, kind: value || '' }))}
                                      options={[{ value: '', label: 'Все' },
                                          { value: 'welcome', label: kindLabel('welcome') },
                                          { value: 'activity', label: kindLabel('activity') }]} />
                    </label>
                </div>
            )}

            {error && <div className="text-[13px] text-rose-600">{error}</div>}

            <div className={`${iosCard} hidden overflow-x-auto md:block`}>
                <table className="w-full text-left text-[13px]">
                    <thead className="bg-slate-50/80 text-[11.5px] uppercase tracking-wide text-slate-500">
                        <tr>
                            <th className="px-3.5 py-2.5 font-semibold">Дата и время</th>
                            <th className="px-3.5 py-2.5 font-semibold">Офис</th>
                            <th className="px-3.5 py-2.5 font-semibold">Водитель</th>
                            <th className="px-3.5 py-2.5 font-semibold">Парк · тариф</th>
                            <th className="px-3.5 py-2.5 font-semibold">Заказы</th>
                            <th className="px-3.5 py-2.5 font-semibold">Вид</th>
                            <th className="px-3.5 py-2.5 text-right font-semibold">Блоков</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items.map((item, index) => {
                            /* Отменённая строка остаётся на месте (это журнал операций),
                               но приглушена целиком, а блоки зачёркнуты: в остатках и
                               расходе их уже нет. */
                            const off = Boolean(item.canceled_at);
                            const main = off ? 'text-slate-400' : 'text-slate-900';
                            const body = off ? 'text-slate-400' : 'text-slate-700';
                            const meta = off ? 'text-slate-400' : 'text-slate-500';
                            return (
                                <tr key={item.id}
                                    onClick={canManage ? () => setOpened(item) : undefined}
                                    className={`${index ? 'border-t border-slate-100' : ''} ${canManage ? 'cursor-pointer transition hover:bg-slate-50' : ''}`}>
                                    <td className="whitespace-nowrap px-3.5 py-2.5 align-top">
                                        <div className={`tabular-nums ${main}`}>{fmtDateTime(item.created_at)}</div>
                                        <div className={`text-[12px] ${meta}`}>{item.issued_by_name}</div>
                                    </td>
                                    <td className="px-3.5 py-2.5 align-top">
                                        <div className={main}>{item.office_name}</div>
                                        <div className={`text-[12px] ${meta}`}>{item.city}</div>
                                    </td>
                                    <td className="max-w-[220px] px-3.5 py-2.5 align-top">
                                        <DriverName item={item} muted={off} />
                                        {item.driver_phone && (
                                            <a href={`tel:${item.driver_phone}`} onClick={(event) => event.stopPropagation()}
                                               className={`tabular-nums text-[12.5px] ${meta}`}>
                                                {fmtPhone(item.driver_phone)}
                                            </a>
                                        )}
                                    </td>
                                    <td className="max-w-[220px] px-3.5 py-2.5 align-top">
                                        <div className={`truncate ${body}`}>{item.driver_park || '—'}</div>
                                        <div className={`truncate text-[12px] ${meta}`}>
                                            {(item.driver_tariffs || []).map(tariffLabel).join(', ') || '—'}
                                        </div>
                                    </td>
                                    <td className={`whitespace-nowrap px-3.5 py-2.5 align-top tabular-nums ${body}`}>{ordersLabel(item)}</td>
                                    <td className={`whitespace-nowrap px-3.5 py-2.5 align-top ${body}`}>
                                        {off ? <span className={CANCELED_PILL}>Отменена</span> : kindLabel(item.kind)}
                                    </td>
                                    <td className={`px-3.5 py-2.5 text-right align-top font-semibold tabular-nums ${off ? 'text-slate-400 line-through' : 'text-slate-900'}`}>
                                        {item.blocks}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
                {!items.length && !loading && (
                    <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
                        <Droplet size={22} className="text-slate-300" />
                        <p className="text-[13.5px] text-slate-500">
                            {query || chips.length ? 'Ничего не нашлось — попробуйте изменить запрос' : 'Выдач пока не было'}
                        </p>
                    </div>
                )}
            </div>

            <div className="space-y-2 md:hidden">
                {items.map((item) => {
                    const off = Boolean(item.canceled_at);
                    return (
                        <div key={item.id}
                             onClick={canManage ? () => setOpened(item) : undefined}
                             className={`${iosCard} p-3.5 ${canManage ? 'cursor-pointer transition active:scale-[0.99]' : ''}`}>
                            <div className="flex items-baseline justify-between gap-2">
                                <span className="min-w-0 flex-1 text-[14px] font-medium"><DriverName item={item} muted={off} /></span>
                                <span className={`shrink-0 text-[13px] font-semibold tabular-nums ${off ? 'text-slate-400 line-through' : 'text-slate-900'}`}>
                                    {item.blocks} бл.
                                </span>
                            </div>
                            {item.driver_phone && <div className="tabular-nums text-[12.5px] text-slate-500">{fmtPhone(item.driver_phone)}</div>}
                            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[12.5px] text-slate-600">
                                {off && <span className={CANCELED_PILL}>Отменена</span>}
                                <span>{kindLabel(item.kind)} · {item.office_name}</span>
                            </div>
                            <div className="mt-0.5 text-[12px] text-slate-500">
                                {fmtDateTime(item.created_at)} · {item.issued_by_name}
                            </div>
                        </div>
                    );
                })}
                {!items.length && !loading && (
                    <div className={`${iosCard} px-4 py-10 text-center text-[13.5px] text-slate-500`}>
                        {query || chips.length ? 'Ничего не нашлось' : 'Выдач пока не было'}
                    </div>
                )}
            </div>

            {loading && (
                <div className="flex items-center justify-center gap-2 text-[13px] text-slate-500">
                    <Loader2 size={15} className="animate-spin" /> Загружаем журнал…
                </div>
            )}

            {canManage && (
                <IssueSheet
                    item={opened}
                    onClose={() => setOpened(null)}
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    showToast={showToast}
                    onCanceled={(issue, office) => {
                        // Строку правим на месте, а не перечитываем список: иначе
                        // человек терял бы догруженные «Показать ещё» страницы.
                        if (issue) setItems((prev) => prev.map((row) => (row.id === issue.id ? issue : row)));
                        if (office) onStockChanged?.(office);
                    }}
                />
            )}

            {items.length < total && !loading && (
                <div className="flex flex-col items-center gap-1.5">
                    <button type="button" className={iosBtnSecondary} onClick={() => load({ append: true, from: items.length })}>
                        Показать ещё
                    </button>
                    <span className="text-[12px] tabular-nums text-slate-400">{items.length} из {total}</span>
                </div>
            )}
        </div>
    );
};

export default WaterJournal;
