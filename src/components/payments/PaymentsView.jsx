import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Download, Loader2, Plus, Receipt, Search, SlidersHorizontal, X } from 'lucide-react';
import { APPLE_FONT, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput, IosSegmented } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker, isoDate } from '../ui/DateRangePicker';
import PaymentRequestForm from './PaymentRequestForm';
import PaymentRequestCard from './PaymentRequestCard';
import PaymentsDictionaries from './PaymentsDictionaries';
import PaymentsRoles from './PaymentsRoles';
import FixedPaymentsPanel from './FixedPaymentsPanel';
import {
    ROLE_LABELS, SOURCE_OPTIONS, STATE_FILTERS, TYPE_OPTIONS, dueLabel, exportFileName, fmtDateShort, fmtMoney,
    requestState, rowTone, stateMeta, stepLabel, toneEdge, tonePill, toneRow, toneText,
} from './paymentsMeta';
import { NoticeBox, StatePill, errorText } from './paymentsUi';

/*
 * Раздел «Оплата счетов» — бизнес-процесс «Согласование — Оплата счетов» (#179).
 *
 * Четыре вкладки: реестр заявок (главная), календарь фиксированных платежей,
 * справочники (контрагенты, договоры, Приказы…) и участники ролей процесса.
 *
 * Реестр устроен как у «Посылок»: поиск — первое, что видно (одна строка на
 * все поля: расход, контрагент, БИН, номер счёта, карта, инициатор); фильтры
 * под кнопкой; полоса-легенда, она же фильтр по состоянию, со счётчиками с
 * сервера. Строка красится только там, где цвет несёт смысл: просрочка,
 * ожидание договора, закрытая, отклонённая. Обычная заявка в работе — белая.
 *
 * Диплинк: ?view=payments&request=<id> открывает карточку сразу — на него
 * ведут уведомления в Telegram.
 */

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

const TABS = [
    { value: 'requests', label: 'Заявки' },
    { value: 'fixed', label: 'Фиксированные платежи' },
    { value: 'dictionaries', label: 'Справочники' },
    { value: 'roles', label: 'Участники' },
];

const EMPTY_FILTERS = {
    responsible_id: null, initiator_id: null, counterparty_id: null, project_id: null,
    payment_source: '', payment_type: '', fixed_only: false, date_from: '', date_to: '',
};

const DATE_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-white px-3 py-2 '
    + 'text-left text-[12.5px] font-medium text-slate-700 ring-1 ring-slate-200/70 '
    + 'shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-all hover:bg-slate-50 '
    + 'active:scale-[0.99] focus:outline-none focus:ring-2 focus:ring-blue-500/60 '
    + '[&>span]:flex-1 [&>span]:text-left [&>span]:truncate';

const shiftDays = (days) => {
    const value = new Date();
    value.setDate(value.getDate() - days);
    return isoDate(value);
};
const DATE_PRESETS = [
    { label: 'Неделя', range: () => ({ from: shiftDays(6), to: isoDate(new Date()) }) },
    { label: 'Месяц', range: () => ({ from: shiftDays(29), to: isoDate(new Date()) }) },
    { label: 'Квартал', range: () => ({ from: shiftDays(90), to: isoDate(new Date()) }) },
];

const requestIdFromLocation = () => {
    try {
        const value = new URLSearchParams(window.location.search).get('request');
        const id = Number(value);
        return Number.isInteger(id) && id > 0 ? id : null;
    } catch {
        return null;
    }
};

const PaymentsView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const headers = useCallback(() => (withAccessTokenHeader ? withAccessTokenHeader() : {}), [withAccessTokenHeader]);

    const [ping, setPing] = useState(null);
    const [pingError, setPingError] = useState('');
    const [dictionaries, setDictionaries] = useState(null);
    const [users, setUsers] = useState([]);
    const [tab, setTab] = useState('requests');

    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [counters, setCounters] = useState({});
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState('');
    const [downloading, setDownloading] = useState(false);

    const [state, setState] = useState('all');
    const [search, setSearch] = useState('');
    const [query, setQuery] = useState('');
    const [filters, setFilters] = useState(EMPTY_FILTERS);
    const [filtersOpen, setFiltersOpen] = useState(false);

    const [formOpen, setFormOpen] = useState(false);
    const [editing, setEditing] = useState(null);
    const [editingItems, setEditingItems] = useState([]);
    const [openedId, setOpenedId] = useState(() => requestIdFromLocation());

    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    const loadPing = useCallback(async () => {
        try {
            const response = await axios.get(`${apiBaseUrl}/api/payments/ping`, { headers: headers() });
            setPing(response.data);
            setPingError('');
        } catch (error) {
            setPingError(errorText(error, 'Раздел недоступен'));
        }
    }, [apiBaseUrl, headers]);

    const loadDictionaries = useCallback(async () => {
        try {
            const response = await axios.get(`${apiBaseUrl}/api/payments/dictionaries`, { headers: headers() });
            setDictionaries(response.data);
        } catch { /* форма покажет пустые списки */ }
    }, [apiBaseUrl, headers]);

    useEffect(() => { loadPing(); loadDictionaries(); }, [loadPing, loadDictionaries]);

    useEffect(() => {
        let cancelled = false;
        axios.get(`${apiBaseUrl}/api/payments/users`, { headers: headers() })
            .then((response) => { if (!cancelled) setUsers(response.data?.users || []); })
            .catch(() => {});
        return () => { cancelled = true; };
    }, [apiBaseUrl, headers]);

    useEffect(() => {
        const timer = setTimeout(() => setQuery(search.trim()), SEARCH_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [search]);

    const selection = useMemo(() => {
        const params = new URLSearchParams();
        if (state && state !== 'all') params.set('state', state);
        if (query) params.set('q', query);
        Object.entries(filters).forEach(([key, value]) => {
            if (value === '' || value === null || value === undefined || value === false) return;
            params.set(key, String(value === true ? 1 : value));
        });
        return params;
    }, [state, query, filters]);

    const requestRef = useRef(0);
    const load = useCallback(async ({ append = false, from = 0 } = {}) => {
        const ticket = requestRef.current + 1;
        requestRef.current = ticket;
        setLoading(true);
        setLoadError('');
        try {
            const params = new URLSearchParams(selection);
            params.set('limit', String(PAGE_SIZE));
            params.set('offset', String(from));
            const response = await axios.get(`${apiBaseUrl}/api/payments/requests?${params}`, { headers: headers() });
            if (requestRef.current !== ticket) return;
            const data = response.data || {};
            setItems((prev) => (append ? [...prev, ...(data.items || [])] : (data.items || [])));
            setTotal(Number(data.total) || 0);
            setCounters(data.counters || {});
        } catch (error) {
            if (requestRef.current !== ticket) return;
            setLoadError(errorText(error, 'Не удалось загрузить реестр'));
        } finally {
            if (requestRef.current === ticket) setLoading(false);
        }
    }, [apiBaseUrl, headers, selection]);

    useEffect(() => { load(); }, [load]);

    /* После любого действия над заявкой список перезапрашивается: состав
       сегментов и счётчики считает сервер, у себя их не пересчитать. Но сначала
       строка правится на месте, чтобы список не мигал. */
    const applyChanged = useCallback((request) => {
        if (!request) return;
        if (request.deleted) {
            setItems((prev) => prev.filter((item) => item.id !== request.id));
            setTotal((prev) => Math.max(0, prev - 1));
        } else {
            setItems((prev) => {
                const exists = prev.some((item) => item.id === request.id);
                return exists ? prev.map((item) => (item.id === request.id ? { ...item, ...request } : item)) : [request, ...prev];
            });
        }
        load();
        loadPing();
    }, [load, loadPing]);

    const exportXlsx = useCallback(async () => {
        setDownloading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/payments/export?${selection}`, { headers: headers(), responseType: 'blob' });
            const url = URL.createObjectURL(response.data);
            const link = document.createElement('a');
            link.href = url;
            link.download = exportFileName();
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) {
            toastRef.current?.(errorText(error, 'Не удалось собрать файл'), 'error');
        } finally {
            setDownloading(false);
        }
    }, [apiBaseUrl, headers, selection]);

    const steps = ping?.steps || [];
    const capabilities = ping?.capabilities || {};
    const rolesReady = ping?.roles_ready !== false;
    const isAdmin = Boolean(capabilities.is_admin);

    const filtersActive = useMemo(() => {
        let count = 0;
        if (filters.responsible_id) count += 1;
        if (filters.initiator_id) count += 1;
        if (filters.counterparty_id) count += 1;
        if (filters.project_id) count += 1;
        if (filters.payment_source) count += 1;
        if (filters.payment_type) count += 1;
        if (filters.fixed_only) count += 1;
        if (filters.date_from || filters.date_to) count += 1;
        return count;
    }, [filters]);

    const userOptions = useMemo(() => [{ value: null, label: 'Все' }, ...users.map((user) => ({ value: user.id, label: user.name }))], [users]);
    const counterpartyOptions = useMemo(() => [{ value: null, label: 'Все контрагенты' }, ...(dictionaries?.counterparties || []).map((item) => ({ value: item.id, label: item.name }))], [dictionaries]);
    const projectOptions = useMemo(() => [{ value: null, label: 'Все проекты' }, ...(dictionaries?.projects || []).map((item) => ({ value: item.id, label: item.name }))], [dictionaries]);

    const activeFilterChips = useMemo(() => {
        const chips = [];
        const nameOf = (list, id) => list.find((item) => item.value === id)?.label || id;
        if (filters.responsible_id) chips.push({ key: 'responsible_id', name: 'Ответственный', label: nameOf(userOptions, filters.responsible_id), clear: () => setFilters((p) => ({ ...p, responsible_id: null })) });
        if (filters.initiator_id) chips.push({ key: 'initiator_id', name: 'Инициатор', label: nameOf(userOptions, filters.initiator_id), clear: () => setFilters((p) => ({ ...p, initiator_id: null })) });
        if (filters.counterparty_id) chips.push({ key: 'counterparty_id', name: 'Контрагент', label: nameOf(counterpartyOptions, filters.counterparty_id), clear: () => setFilters((p) => ({ ...p, counterparty_id: null })) });
        if (filters.project_id) chips.push({ key: 'project_id', name: 'Проект', label: nameOf(projectOptions, filters.project_id), clear: () => setFilters((p) => ({ ...p, project_id: null })) });
        if (filters.payment_source) chips.push({ key: 'payment_source', name: 'Источник', label: SOURCE_OPTIONS.find((o) => o.value === filters.payment_source)?.label, clear: () => setFilters((p) => ({ ...p, payment_source: '' })) });
        if (filters.payment_type) chips.push({ key: 'payment_type', name: 'Тип', label: TYPE_OPTIONS.find((o) => o.value === filters.payment_type)?.label, clear: () => setFilters((p) => ({ ...p, payment_type: '' })) });
        if (filters.fixed_only) chips.push({ key: 'fixed_only', name: 'Только', label: 'фиксированные', clear: () => setFilters((p) => ({ ...p, fixed_only: false })) });
        if (filters.date_from || filters.date_to) chips.push({ key: 'dates', name: 'Создана', label: `${filters.date_from ? fmtDateShort(filters.date_from) : '…'} — ${filters.date_to ? fmtDateShort(filters.date_to) : '…'}`, clear: () => setFilters((p) => ({ ...p, date_from: '', date_to: '' })) });
        return chips;
    }, [filters, userOptions, counterpartyOptions, projectOptions]);

    const openCreate = () => { setEditing(null); setEditingItems([]); setFormOpen(true); };

    return (
        <div className="mx-auto max-w-[1400px] px-3 pb-12 pt-3 sm:px-5" style={{ fontFamily: APPLE_FONT }}>
            <header className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                    <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">Оплата счетов</h1>
                    <p className="mt-0.5 text-[13px] text-slate-500">
                        Согласование закупа и оплата счетов по шагам: инициатор → руководитель → Учредитель → бухгалтерия
                    </p>
                </div>
                {tab === 'requests' && (
                    <div className="flex w-full items-center gap-2 sm:w-auto">
                        <button type="button" className={`${iosBtnSecondary} shrink-0`} disabled={downloading || !items.length} onClick={exportXlsx} title="Выгрузка реестра в Excel по текущему отбору">
                            {downloading ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
                            <span className="hidden sm:inline">Excel</span>
                        </button>
                        {capabilities.can_create && (
                            <button type="button" className={`${iosBtnPrimary} flex-1 whitespace-nowrap sm:flex-none`} onClick={openCreate} disabled={!rolesReady}>
                                <Plus size={15} /> Новая заявка
                            </button>
                        )}
                    </div>
                )}
            </header>

            <div className="mt-4 overflow-x-auto">
                <IosSegmented
                    value={tab}
                    options={TABS.map((item) => ({ ...item, count: item.value === 'requests' ? counters.mine : undefined }))}
                    onChange={setTab}
                    size="lg"
                    ariaLabel="Раздел"
                    className="min-w-[560px] sm:min-w-0"
                />
            </div>

            {pingError && <div className="mt-4"><NoticeBox text={pingError} /></div>}
            {ping && ping.schema_ready === false && (
                <div className="mt-4"><NoticeBox text="Раздел разворачивается — таблицы появятся после перезапуска сервера." /></div>
            )}
            {ping && !rolesReady && tab === 'requests' && (
                <div className="mt-4">
                    <NoticeBox text="Чтобы заводить заявки, назначьте участников процесса: кто Учредитель и кто Бухгалтерия.">
                        {' '}
                        <button type="button" className="font-medium underline decoration-amber-300 underline-offset-2 hover:decoration-amber-600" onClick={() => setTab('roles')}>Открыть «Участники»</button>
                    </NoticeBox>
                </div>
            )}
            {ping && ping.storage_ready === false && tab === 'requests' && (
                <div className="mt-3"><NoticeBox tone="slate" text="Хранилище файлов не настроено: счета и акты прикрепить не получится, пока не задан бакет." /></div>
            )}

            {tab === 'requests' && (
                <>
                    <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center">
                        <div className="relative sm:flex-1">
                            <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                            <input
                                type="search"
                                className={`${iosInput} pl-9`}
                                placeholder="Расход, контрагент, БИН, номер счёта, карта, инициатор, № заявки…"
                                value={search}
                                onChange={(event) => setSearch(event.target.value)}
                            />
                        </div>
                        <button
                            type="button"
                            className={`${filtersActive ? iosBtnPrimary : iosBtnSecondary} shrink-0 self-start sm:self-auto`}
                            onClick={() => setFiltersOpen((prev) => !prev)}
                        >
                            <SlidersHorizontal size={15} />
                            Фильтры
                            {filtersActive > 0 && <span className="tabular-nums">· {filtersActive}</span>}
                        </button>
                    </div>

                    <div className="mt-3 flex flex-wrap items-center gap-1 rounded-xl bg-slate-100 px-2 py-1.5">
                        {STATE_FILTERS.map((item) => {
                            const count = counters[item.counter];
                            const active = state === item.key;
                            if (item.key !== 'all' && item.key !== 'mine' && item.key !== 'open' && !count && !active) return null;
                            return (
                                <button
                                    key={item.key}
                                    type="button"
                                    onClick={() => setState(item.key)}
                                    className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[12.5px] transition ${active ? 'bg-slate-900 font-medium text-white' : 'text-slate-700 hover:bg-white'}`}
                                >
                                    {item.tone && <span className={`h-2 w-2 shrink-0 rounded-full ${tonePill(item.tone).dot}`} />}
                                    {item.label}
                                    {count !== undefined && <span className="font-semibold tabular-nums">{count}</span>}
                                </button>
                            );
                        })}
                    </div>

                    {filtersActive > 0 && (
                        <div className="mt-2 flex flex-wrap items-center gap-1.5">
                            {activeFilterChips.map((chip) => (
                                <button key={chip.key} type="button" onClick={chip.clear} title={`Убрать: ${chip.label}`} className="group inline-flex max-w-full items-center gap-1.5 rounded-full bg-white px-2.5 py-1 text-[12.5px] text-slate-700 ring-1 ring-slate-200/80 transition hover:ring-slate-300 active:scale-[0.98]">
                                    <span className="text-slate-400">{chip.name}</span>
                                    <span className="truncate font-medium">{chip.label}</span>
                                    <X size={12} className="shrink-0 text-slate-400 group-hover:text-slate-600" />
                                </button>
                            ))}
                            <button type="button" onClick={() => setFilters(EMPTY_FILTERS)} className="px-1.5 text-[12.5px] text-slate-500 underline decoration-slate-300 underline-offset-2 transition hover:text-slate-700">сбросить всё</button>
                        </div>
                    )}

                    {filtersOpen && (
                        <div className={`${iosCard} mt-3 p-3.5`}>
                            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                                <label className="block space-y-1.5">
                                    <span className={iosGroupLabel}>Текущий ответственный</span>
                                    <CustomSelect value={filters.responsible_id} onChange={(value) => setFilters((p) => ({ ...p, responsible_id: value || null }))} options={userOptions} placeholder="Все" variant="ios" searchable ariaLabel="Ответственный" />
                                </label>
                                <label className="block space-y-1.5">
                                    <span className={iosGroupLabel}>Инициатор</span>
                                    <CustomSelect value={filters.initiator_id} onChange={(value) => setFilters((p) => ({ ...p, initiator_id: value || null }))} options={userOptions} placeholder="Все" variant="ios" searchable ariaLabel="Инициатор" />
                                </label>
                                <label className="block space-y-1.5">
                                    <span className={iosGroupLabel}>Контрагент</span>
                                    <CustomSelect value={filters.counterparty_id} onChange={(value) => setFilters((p) => ({ ...p, counterparty_id: value || null }))} options={counterpartyOptions} placeholder="Все контрагенты" variant="ios" searchable ariaLabel="Контрагент" />
                                </label>
                                <label className="block space-y-1.5">
                                    <span className={iosGroupLabel}>Проект</span>
                                    <CustomSelect value={filters.project_id} onChange={(value) => setFilters((p) => ({ ...p, project_id: value || null }))} options={projectOptions} placeholder="Все проекты" variant="ios" ariaLabel="Проект" />
                                </label>
                                <label className="block space-y-1.5">
                                    <span className={iosGroupLabel}>Источник оплаты</span>
                                    <CustomSelect value={filters.payment_source} onChange={(value) => setFilters((p) => ({ ...p, payment_source: value || '' }))} options={[{ value: '', label: 'Все' }, ...SOURCE_OPTIONS]} placeholder="Все" variant="ios" ariaLabel="Источник оплаты" />
                                </label>
                                <label className="block space-y-1.5">
                                    <span className={iosGroupLabel}>Тип оплаты</span>
                                    <CustomSelect value={filters.payment_type} onChange={(value) => setFilters((p) => ({ ...p, payment_type: value || '' }))} options={[{ value: '', label: 'Все' }, ...TYPE_OPTIONS]} placeholder="Все" variant="ios" ariaLabel="Тип оплаты" />
                                </label>
                                <label className="block space-y-1.5">
                                    <span className={iosGroupLabel}>Создана</span>
                                    <IosDateRangePicker
                                        from={filters.date_from || ''}
                                        to={filters.date_to || ''}
                                        max={isoDate(new Date())}
                                        onChange={({ from, to }) => setFilters((p) => ({ ...p, date_from: from || '', date_to: to || '' }))}
                                        presets={DATE_PRESETS}
                                        triggerClassName={DATE_TRIGGER}
                                    />
                                </label>
                                <label className="flex items-end gap-2 pb-2 text-[13px] text-slate-700">
                                    <input type="checkbox" className="h-4 w-4 rounded border-slate-300 text-blue-600" checked={filters.fixed_only} onChange={(event) => setFilters((p) => ({ ...p, fixed_only: event.target.checked }))} />
                                    Только фиксированные платежи
                                </label>
                            </div>
                        </div>
                    )}

                    {loadError && <div className="mt-4 rounded-2xl bg-red-50 px-4 py-3 text-[13px] text-red-700 ring-1 ring-red-200">{loadError}</div>}

                    {/* Таблица на широком экране, карточки на телефоне. */}
                    <div className={`${iosCard} mt-4 hidden overflow-x-auto md:block`}>
                        <table className="w-full min-w-[980px] border-collapse text-[13.5px]">
                            <thead>
                                <tr className="border-b border-slate-200/70 bg-slate-50 text-left text-[11.5px] uppercase tracking-wider text-slate-500">
                                    <th className="px-3.5 py-2.5 font-semibold">№ · создана</th>
                                    <th className="px-3.5 py-2.5 font-semibold">Расход</th>
                                    <th className="px-3.5 py-2.5 font-semibold">Контрагент</th>
                                    <th className="px-3.5 py-2.5 text-right font-semibold">Сумма</th>
                                    <th className="px-3.5 py-2.5 font-semibold">Этап</th>
                                    <th className="px-3.5 py-2.5 font-semibold">Ответственный</th>
                                    <th className="px-3.5 py-2.5 font-semibold">Срок</th>
                                </tr>
                            </thead>
                            <tbody>
                                {items.map((request, index) => {
                                    const tone = rowTone(request);
                                    const text = toneText(tone);
                                    const st = requestState(request);
                                    return (
                                        <tr
                                            key={request.id}
                                            onClick={() => setOpenedId(request.id)}
                                            className={`h-[60px] cursor-pointer transition hover:brightness-[0.97] ${index > 0 ? 'border-t border-slate-900/[0.06]' : ''} ${toneRow(tone)}`}
                                        >
                                            <td className="px-3.5 py-2.5 align-top whitespace-nowrap">
                                                <div className={`tabular-nums ${text.main}`}>№{request.id}</div>
                                                <div className={`text-[12px] leading-4 tabular-nums ${text.meta}`}>{fmtDateShort(request.created_at)} · {request.initiator_name || '—'}</div>
                                            </td>
                                            <td className="max-w-[300px] px-3.5 py-2.5 align-top">
                                                <div className={`truncate ${text.main}`}>{request.expense_name}</div>
                                                <div className={`truncate text-[12.5px] ${text.body}`}>
                                                    {[request.category_name, request.project_name, request.department_name].filter(Boolean).join(' · ') || '—'}
                                                </div>
                                            </td>
                                            <td className="max-w-[220px] px-3.5 py-2.5 align-top">
                                                <div className={`truncate ${text.main}`}>{request.counterparty_name || '—'}</div>
                                                {request.legal_entity_name && <div className={`truncate text-[12.5px] ${text.body}`}>от {request.legal_entity_name}</div>}
                                            </td>
                                            <td className="px-3.5 py-2.5 text-right align-top whitespace-nowrap">
                                                <div className={`font-medium tabular-nums ${text.main}`}>{fmtMoney(request.amount)}</div>
                                                {request.refund_amount > 0 && <div className={`text-[12px] leading-4 tabular-nums ${text.meta}`}>возврат {fmtMoney(request.refund_amount)}</div>}
                                            </td>
                                            <td className="max-w-[240px] px-3.5 py-2.5 align-top">
                                                {request.status === 'active' ? (
                                                    <>
                                                        <div className={`truncate ${text.main}`}>{stepLabel(request, steps)}</div>
                                                        <div className={`text-[12px] leading-4 ${text.meta}`}>шаг {request.current_step} из 12{st === 'blocked' ? ' · ожидает договор' : ''}</div>
                                                    </>
                                                ) : (
                                                    <StatePill request={request} />
                                                )}
                                            </td>
                                            <td className="max-w-[180px] px-3.5 py-2.5 align-top">
                                                {request.status === 'active' ? (
                                                    <>
                                                        <div className={`truncate ${text.main}`}>{request.current_assignee_name || ROLE_LABELS[request.current_role] || '—'}</div>
                                                        <div className={`text-[12px] leading-4 ${text.meta}`}>{ROLE_LABELS[request.current_role] || ''}</div>
                                                    </>
                                                ) : (
                                                    <span className={text.meta}>—</span>
                                                )}
                                            </td>
                                            <td className="px-3.5 py-2.5 align-top whitespace-nowrap">
                                                {request.due_on ? (
                                                    <>
                                                        <div className={`tabular-nums ${text.main}`}>{fmtDateShort(request.due_on)}</div>
                                                        <div className={`text-[12px] leading-4 ${st === 'overdue' ? 'font-medium text-rose-700' : text.meta}`}>{dueLabel(request)}</div>
                                                    </>
                                                ) : (
                                                    request.paid_on ? <div className={`text-[12.5px] tabular-nums ${text.body}`}>оплачено {fmtDateShort(request.paid_on)}</div> : <span className={text.meta}>—</span>
                                                )}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                        {!items.length && !loading && (
                            <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
                                <Receipt size={22} className="text-slate-300" />
                                <p className="text-[13.5px] text-slate-500">
                                    {query || filtersActive || state !== 'all' ? 'Ничего не нашлось — измените запрос или фильтры' : 'Заявок пока нет'}
                                </p>
                            </div>
                        )}
                    </div>

                    <div className="mt-4 space-y-2 md:hidden">
                        {items.map((request) => {
                            const tone = rowTone(request);
                            return (
                                <button
                                    key={request.id}
                                    type="button"
                                    onClick={() => setOpenedId(request.id)}
                                    className={`${iosCard} relative w-full overflow-hidden p-3.5 pl-4 text-left transition active:scale-[0.99] before:absolute before:inset-y-0 before:left-0 before:w-[3px] ${toneEdge(tone)}`}
                                >
                                    <div className="flex items-baseline justify-between gap-2">
                                        <span className="truncate text-[14px] font-medium text-slate-900">{request.expense_name}</span>
                                        <span className="shrink-0 text-[14px] font-semibold tabular-nums text-slate-900">{fmtMoney(request.amount)}</span>
                                    </div>
                                    <div className="mt-0.5 truncate text-[12.5px] text-slate-500">№{request.id} · {request.counterparty_name || '—'}</div>
                                    <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-slate-600">
                                        {request.status === 'active' ? (
                                            <span className={`rounded-full px-2 py-0.5 ${tonePill(stateMeta(requestState(request)).tone || 'current').fill}`}>
                                                {request.current_step}/12 · {request.current_assignee_name || ROLE_LABELS[request.current_role] || '—'}
                                            </span>
                                        ) : <StatePill request={request} />}
                                        {request.due_on && <span className="tabular-nums">{dueLabel(request)}</span>}
                                    </div>
                                </button>
                            );
                        })}
                        {!items.length && !loading && (
                            <div className={`${iosCard} px-4 py-10 text-center text-[13.5px] text-slate-500`}>{query || filtersActive ? 'Ничего не нашлось' : 'Заявок пока нет'}</div>
                        )}
                    </div>

                    {loading && (
                        <div className="mt-4 flex items-center justify-center gap-2 text-[13px] text-slate-500"><Loader2 size={15} className="animate-spin" /> Загружаем реестр…</div>
                    )}
                    {items.length < total && (
                        <div className="mt-4 flex flex-col items-center gap-1.5">
                            <button type="button" className={iosBtnSecondary} disabled={loading} onClick={() => load({ append: true, from: items.length })}>Показать ещё</button>
                            <span className="text-[12px] tabular-nums text-slate-400">{items.length} из {total}</span>
                        </div>
                    )}
                </>
            )}

            {tab === 'fixed' && (
                <div className="mt-4">
                    <FixedPaymentsPanel
                        apiBaseUrl={apiBaseUrl}
                        headers={headers}
                        users={users}
                        dictionaries={dictionaries}
                        me={ping?.me}
                        showToast={showToast}
                        canEdit={isAdmin}
                        onRequestsChanged={() => { load(); loadPing(); }}
                        onOpenRequest={(id) => setOpenedId(id)}
                    />
                </div>
            )}

            {tab === 'dictionaries' && (
                <div className="mt-4">
                    <PaymentsDictionaries
                        apiBaseUrl={apiBaseUrl}
                        headers={headers}
                        users={users}
                        dictionaries={dictionaries}
                        roles={ping?.roles || {}}
                        onDictionariesChanged={loadDictionaries}
                        showToast={showToast}
                        canEdit={isAdmin}
                    />
                </div>
            )}

            {tab === 'roles' && (
                <div className="mt-4">
                    <PaymentsRoles
                        apiBaseUrl={apiBaseUrl}
                        headers={headers}
                        roles={ping?.roles || {}}
                        users={users}
                        onChanged={(roles) => setPing((prev) => (prev ? { ...prev, roles, roles_ready: Boolean(roles.founder?.length) && Boolean(roles.accounting?.length) } : prev))}
                        showToast={showToast}
                        canEdit={isAdmin}
                    />
                </div>
            )}

            <PaymentRequestForm
                open={formOpen}
                onClose={() => { setFormOpen(false); setEditing(null); }}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                dictionaries={dictionaries}
                users={users}
                me={ping?.me}
                request={editing}
                items={editingItems}
                stepFiles={steps.find((s) => s.no === 1)?.files || []}
                onSaved={(data) => {
                    applyChanged(data?.request);
                    if (data?.request?.id) setOpenedId(data.request.id);
                }}
                showToast={showToast}
            />

            <PaymentRequestCard
                open={Boolean(openedId)}
                onClose={() => setOpenedId(null)}
                requestId={openedId}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                dictionaries={dictionaries}
                users={users}
                me={ping?.me}
                onChanged={applyChanged}
                onEdit={(request, requestItems) => { setOpenedId(null); setEditing(request); setEditingItems(requestItems || []); setFormOpen(true); }}
                showToast={showToast}
            />
        </div>
    );
};

export default PaymentsView;
